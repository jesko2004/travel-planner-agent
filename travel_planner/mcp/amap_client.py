from __future__ import annotations

from datetime import datetime

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.mcp import MultiMCPTools

from travel_planner.config import Settings
from travel_planner.models import (
    ResearchBundle,
    RouteResult,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
)
from travel_planner.services.model_client import parse_agent_output


class AmapServiceError(RuntimeError):
    pass


class AmapMCPClient:
    """官方高德 MCP 的最小权限只读客户端。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tools: MultiMCPTools | None = None
        self.model = OpenAIChat(
            id=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

    async def __aenter__(self) -> "AmapMCPClient":
        if not self.settings.amap_ready:
            raise AmapServiceError("未配置 AMAP_MAPS_API_KEY")
        self.tools = MultiMCPTools(
            ["npx -y @amap/amap-maps-mcp-server"],
            env={"AMAP_MAPS_API_KEY": self.settings.amap_api_key},
            timeout_seconds=self.settings.mcp_timeout_seconds,
        )
        try:
            await self.tools.connect()
        except Exception as exc:
            self.tools = None
            raise AmapServiceError("官方高德 MCP 连接失败") from exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        tools, self.tools = self.tools, None
        if tools is not None:
            try:
                await tools.close()
            except Exception:
                # 清理错误不能掩盖规划结果或原始异常。
                pass

    def _agent(self, name: str, output_schema: type) -> Agent:
        if self.tools is None:
            raise RuntimeError("高德 MCP 尚未连接")
        return Agent(
            name=name,
            model=self.model,
            tools=[self.tools],
            output_schema=output_schema,
            markdown=False,
            instructions=[
                "只使用高德 MCP 的只读查询工具",
                "每条地址、坐标、路线和天气必须来自工具返回",
                "不得声称已下单、已预约或价格实时有效",
                "工具没有返回时保留为空，不得猜测",
            ],
        )

    async def research(self, request: TripRequest) -> ResearchBundle:
        agent = self._agent("高德地点研究器", ResearchBundle)
        try:
            response = await agent.arun(
                f"""
核实中国大陆目的地并完成只读研究：{request.model_dump_json()}
查询目的地行政区与旅行日期相关天气；根据必去、避开项和偏好查询适合的景点、餐饮 POI；
再查询 3 至 5 个酒店 POI。每个 POI 必须使用详情工具核实 POI ID、地址和经纬度。
所有 evidence 统一填写 source=高德地图、实际工具名、当前查询时间、is_realtime 与 status=verified。
酒店 POI 不填写或推测价格、库存、房型、发票和取消政策。
"""
            )
            bundle = parse_agent_output(response.content, ResearchBundle)
        except Exception as exc:
            raise AmapServiceError("高德目的地或 POI 研究失败") from exc
        now = datetime.now()
        for poi in [*bundle.pois, *bundle.hotel_pois]:
            poi.evidence = SourceEvidence(
                source="高德地图",
                tool_name=poi.evidence.tool_name or "amap_mcp",
                checked_at=now,
                is_realtime=poi.evidence.is_realtime,
                status=VerificationStatus.VERIFIED,
            )
        return bundle

    async def route(self, origin: str, destination: str, transport: str) -> RouteResult:
        agent = self._agent("高德路线补全器", RouteResult)
        try:
            response = await agent.arun(
                f"查询从高德坐标 {origin} 到 {destination} 的{transport}路线。"
                "返回距离、时间、简短摘要和可用导航链接；工具失败时 verified=false，数值留空。"
            )
            return parse_agent_output(response.content, RouteResult)
        except Exception as exc:
            return RouteResult(summary=f"路线查询失败：{type(exc).__name__}", verified=False)
