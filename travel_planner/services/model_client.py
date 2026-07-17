from __future__ import annotations

import json
import re
from typing import TypeVar

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel, ValidationError

from travel_planner.config import Settings
from travel_planner.models import Activity, ItineraryDraft, PoiCandidate, TripRequest


T = TypeVar("T", bound=BaseModel)


class ModelOutputError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def parse_agent_output(content: object, schema: type[T]) -> T:
    if isinstance(content, schema):
        return content
    if isinstance(content, BaseModel):
        return schema.model_validate(content.model_dump())
    if isinstance(content, dict):
        return schema.model_validate(content)
    if isinstance(content, str):
        return schema.model_validate_json(_extract_json(content))
    raise ModelOutputError(f"无法解析模型输出类型：{type(content).__name__}")


class DeepSeekPlanner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = OpenAIChat(
            id=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )

    async def create_draft(
        self,
        request: TripRequest,
        pois: list[PoiCandidate],
        allow_unverified: bool = False,
        locked_activities: list[Activity] | None = None,
    ) -> ItineraryDraft:
        if not self.settings.deepseek_ready:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        if not pois and not allow_unverified:
            raise RuntimeError("没有已验证 POI，不能生成已验证行程草案")

        poi_payload = [
            {
                "poi_id": poi.poi_id,
                "name": poi.name,
                "address": poi.address,
                "category": poi.category,
                "duration_minutes": poi.suggested_duration_minutes,
                "estimated_cost": poi.estimated_cost,
            }
            for poi in pois
        ]
        locked_payload = [
            {
                "day": item.day.isoformat(),
                "start_time": item.start_time.isoformat(),
                "end_time": item.end_time.isoformat(),
                "poi_id": item.poi.poi_id,
                "notes": item.notes,
            }
            for item in (locked_activities or [])
        ]
        prompt = f"""
你是国内私人旅行助手的行程规划步骤，只能输出结构化数据，不能调用外部写操作。
旅行需求：{request.model_dump_json()}
候选 POI：{json.dumps(poi_payload, ensure_ascii=False)}
用户锁定且必须原样保留的活动：{json.dumps(locked_payload, ensure_ascii=False)}

规则：
1. 日期必须在请求范围内，活动时间必须在每日开始/结束范围内。
2. 只使用候选列表中的 poi_id；候选为空时仅可生成空日程并说明尚未验证。
3. 同一天活动不能重叠，活动间至少预留 30 分钟；路线时间稍后由程序补全。
4. 不生成酒店价格、库存、票价、地址、坐标或“已验证”等声明。
5. 每天控制活动数量，优先满足必去项和旅行节奏。
6. 锁定活动的日期、开始时间、结束时间和 poi_id 必须原样保留，并围绕它们安排其他活动。
"""
        agent = Agent(
            name="行程规划器",
            model=self.model,
            instructions=["仅输出符合 schema 的内容", "不得虚构候选列表之外的 POI"],
            output_schema=ItineraryDraft,
            markdown=False,
        )

        first_error: Exception | None = None
        response = await agent.arun(prompt)
        try:
            return parse_agent_output(response.content, ItineraryDraft)
        except (ValidationError, ModelOutputError, ValueError) as exc:
            first_error = exc

        repair_prompt = f"""
上一次输出未通过结构校验：{type(first_error).__name__}。
请重新输出完整对象，严格遵守 schema 和日期/POI 约束。不要解释，不要使用 Markdown。
原始需求：{request.model_dump_json()}
可用 POI：{json.dumps(poi_payload, ensure_ascii=False)}
锁定活动：{json.dumps(locked_payload, ensure_ascii=False)}
"""
        repaired = await agent.arun(repair_prompt)
        try:
            return parse_agent_output(repaired.content, ItineraryDraft)
        except Exception as exc:
            raise ModelOutputError("DeepSeek 结构化输出修复一次后仍失败") from exc
