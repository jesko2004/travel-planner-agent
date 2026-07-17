from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import shutil

from travel_planner.config import Settings
from travel_planner.mcp.amap_client import AmapMCPClient, AmapServiceError
from travel_planner.models import (
    Activity,
    BudgetSummary,
    DayPlan,
    HotelCandidate,
    Itinerary,
    PoiCandidate,
    ResearchBundle,
    RouteLeg,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
    WeatherInfo,
)
from travel_planner.services.hotel_links import build_hotel_platform_links
from travel_planner.services.model_client import DeepSeekPlanner
from travel_planner.services.validator import ItineraryValidator
from travel_planner.storage.database import SQLiteRepository


ProgressCallback = Callable[[str, str], None]


@dataclass
class HealthStatus:
    deepseek: bool
    amap_key: bool
    node: bool
    npm: bool
    database: bool
    messages: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    itinerary: Itinerary
    saved: bool
    amap_available: bool
    stage_messages: list[str]


def check_health(settings: Settings, repository: SQLiteRepository) -> HealthStatus:
    messages: list[str] = []
    database_ready = True
    try:
        repository.initialize()
    except Exception as exc:
        database_ready = False
        messages.append(f"数据库不可用：{type(exc).__name__}")
    node = shutil.which("node") is not None
    npm = shutil.which("npm") is not None
    if not node or not npm:
        messages.append("未发现 Node/npm，官方高德 MCP 无法启动")
    if not settings.deepseek_ready:
        messages.append("未配置 DEEPSEEK_API_KEY")
    if not settings.amap_ready:
        messages.append("未配置 AMAP_MAPS_API_KEY")
    return HealthStatus(
        deepseek=settings.deepseek_ready,
        amap_key=settings.amap_ready,
        node=node,
        npm=npm,
        database=database_ready,
        messages=messages,
    )


class TravelPlannerWorkflow:
    def __init__(self, settings: Settings, repository: SQLiteRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.planner = DeepSeekPlanner(settings)
        self.validator = ItineraryValidator()

    @staticmethod
    def _notify(callback: ProgressCallback | None, stage: str, message: str) -> None:
        if callback:
            callback(stage, message)

    @staticmethod
    def _fallback_pois(request: TripRequest) -> list[PoiCandidate]:
        names = request.must_visit or [f"{request.destination}待确认地点"]
        return [
            PoiCandidate(
                poi_id=f"unverified-{index}",
                name=name,
                address="待高德确认",
                longitude=0,
                latitude=0,
                category="未验证候选",
                evidence=SourceEvidence(
                    source="用户输入",
                    tool_name="none",
                    is_realtime=False,
                    status=VerificationStatus.UNVERIFIED,
                ),
            )
            for index, name in enumerate(names, start=1)
        ]

    @staticmethod
    def _transport_label(request: TripRequest) -> str:
        return {
            "公共交通优先": "公共交通",
            "步行优先": "步行",
            "自驾优先": "驾车",
        }[request.local_transport]

    def _build_itinerary(
        self,
        request: TripRequest,
        draft,
        research: ResearchBundle,
        amap_available: bool,
        locked_activities: list[Activity] | None = None,
    ) -> Itinerary:
        poi_lookup = {poi.poi_id: poi for poi in research.pois}
        locked_lookup = {
            (item.day, item.start_time, item.end_time, item.poi.poi_id): item
            for item in (locked_activities or [])
        }
        days: list[DayPlan] = []
        skipped: list[str] = []
        for draft_day in draft.days:
            activities: list[Activity] = []
            for item in draft_day.activities:
                poi = poi_lookup.get(item.poi_id)
                if poi is None:
                    skipped.append(item.poi_id)
                    continue
                lock_key = (item.day, item.start_time, item.end_time, item.poi_id)
                previous_locked = locked_lookup.get(lock_key)
                identity = (
                    {"activity_id": previous_locked.activity_id}
                    if previous_locked is not None
                    else {}
                )
                activities.append(
                    Activity(
                        **identity,
                        day=item.day,
                        start_time=item.start_time,
                        end_time=item.end_time,
                        poi=poi,
                        estimated_cost=poi.estimated_cost,
                        notes=item.notes,
                        locked=previous_locked is not None,
                    )
                )
            days.append(
                DayPlan(
                    day=draft_day.day,
                    title=draft_day.title,
                    activities=sorted(activities, key=lambda value: value.start_time),
                )
            )

        hotels = [
            HotelCandidate(
                poi=poi,
                location_note=f"位于 {poi.business_area or poi.address}",
                platform_links=build_hotel_platform_links(
                    request.destination,
                    poi.name,
                    request.start_date.isoformat(),
                    request.end_date.isoformat(),
                ),
            )
            for poi in research.hotel_pois[:5]
        ]
        persons = request.adults + request.children
        nights = max(1, (request.end_date - request.start_date).days)
        hotel_midpoint = (request.hotel_budget_min + request.hotel_budget_max) / 2
        budget = BudgetSummary(
            accommodation_estimate=round(hotel_midpoint * nights * request.rooms, 2),
            local_transport_estimate=round(50 * persons * request.days, 2),
            food_estimate=round(150 * persons * request.days, 2),
        )
        evidence = (
            SourceEvidence(
                source="高德地图",
                tool_name="weather",
                is_realtime=True,
                status=VerificationStatus.VERIFIED,
            )
            if amap_available and research.weather_summary
            else None
        )
        warnings = []
        if not amap_available:
            warnings.append("高德服务不可用：当前仅为未验证草案，地址、天气和路线不可作为出行依据。")
        if skipped:
            warnings.append(f"模型引用了 {len(skipped)} 个候选列表外的 POI，已由程序移除。")
        if not hotels:
            warnings.append("未获得酒店候选，请在官方平台手工搜索并确认。")

        return Itinerary(
            request=request,
            title=draft.title,
            overview=draft.overview,
            days=days,
            hotels=hotels,
            weather=WeatherInfo(
                summary=research.weather_summary or "天气待高德确认",
                packing_advice=research.packing_advice,
                evidence=evidence,
            ),
            budget=budget,
            warnings=warnings,
            alternative_poi_ids=[*draft.alternative_poi_ids, *skipped],
        )

    @staticmethod
    def _shift_for_route(day_plan: DayPlan, route_index: int, travel_minutes: int, day_end) -> None:
        current = day_plan.activities[route_index]
        following = day_plan.activities[route_index + 1]
        earliest = datetime.combine(following.day, current.end_time) + timedelta(
            minutes=travel_minutes + 15
        )
        original_start = datetime.combine(following.day, following.start_time)
        if original_start >= earliest:
            return
        duration = datetime.combine(following.day, following.end_time) - original_start
        shifted_end = earliest + duration
        if shifted_end.time() <= day_end:
            following.start_time = earliest.time()
            following.end_time = shifted_end.time()

    async def generate(
        self,
        request: TripRequest,
        progress: ProgressCallback | None = None,
        locked_activities: list[Activity] | None = None,
    ) -> WorkflowResult:
        stage_messages: list[str] = []
        self._notify(progress, "需求标准化", "输入已通过类型、日期和预算校验")

        amap_available = False
        research = ResearchBundle(destination_confirmed=False)
        self._notify(progress, "高德研究", "正在连接官方高德 MCP")
        try:
            async with AmapMCPClient(self.settings) as client:
                research = await client.research(request)
                if not research.destination_confirmed:
                    raise ValueError("高德无法确认该中国大陆目的地")
                known_ids = {poi.poi_id for poi in research.pois}
                for locked in locked_activities or []:
                    if locked.poi.poi_id not in known_ids:
                        research.pois.append(locked.poi)
                        known_ids.add(locked.poi.poi_id)
                amap_available = True
                stage_messages.append("高德目的地、天气、POI 和酒店候选查询完成")

                self._notify(progress, "行程草案", "正在使用 DeepSeek 生成结构化草案")
                draft = await self.planner.create_draft(
                    request, research.pois, locked_activities=locked_activities
                )
                itinerary = self._build_itinerary(
                    request, draft, research, True, locked_activities
                )

                self._notify(progress, "路线补全", "正在查询每天相邻活动路线")
                for day_plan in itinerary.days:
                    for index in range(max(0, len(day_plan.activities) - 1)):
                        origin = day_plan.activities[index]
                        destination = day_plan.activities[index + 1]
                        route = await client.route(
                            f"{origin.poi.longitude},{origin.poi.latitude}",
                            f"{destination.poi.longitude},{destination.poi.latitude}",
                            self._transport_label(request),
                        )
                        evidence = (
                            SourceEvidence(
                                source="高德地图",
                                tool_name="route_planning",
                                is_realtime=True,
                                status=VerificationStatus.VERIFIED,
                            )
                            if route.verified
                            else None
                        )
                        day_plan.route_legs.append(
                            RouteLeg(
                                origin_activity_id=origin.activity_id,
                                destination_activity_id=destination.activity_id,
                                transport_mode=self._transport_label(request),
                                distance_meters=route.distance_meters,
                                duration_minutes=route.duration_minutes,
                                summary=route.summary,
                                navigation_url=route.navigation_url,
                                evidence=evidence,
                            )
                        )
                        if route.duration_minutes is not None:
                            self._shift_for_route(
                                day_plan, index, route.duration_minutes, request.daily_end_time
                            )
        except ValueError:
            raise
        except AmapServiceError as exc:
            stage_messages.append(f"高德降级：{type(exc).__name__}")
            research = ResearchBundle(
                destination_confirmed=False,
                pois=[
                    *(item.poi for item in (locked_activities or [])),
                    *self._fallback_pois(request),
                ],
            )
            self._notify(progress, "未验证草案", "高德不可用，生成不含精确路线的醒目草案")
            draft = await self.planner.create_draft(
                request,
                research.pois,
                allow_unverified=True,
                locked_activities=locked_activities,
            )
            itinerary = self._build_itinerary(
                request, draft, research, False, locked_activities
            )

        self._notify(progress, "确定性校验", "正在检查日期、时间、路线、步行量和预算")
        self.validator.apply_status(itinerary, amap_available)

        saved = False
        try:
            self.repository.save_itinerary(itinerary)
            saved = True
            stage_messages.append("SQLite 保存与回读校验成功")
        except Exception as exc:
            itinerary.warnings.append(f"本地保存失败：{type(exc).__name__}；当前页面仍可查看。")
            stage_messages.append("SQLite 保存失败")
        self._notify(progress, "完成", "行程已生成" if saved else "行程已生成，但未保存")
        return WorkflowResult(
            itinerary=itinerary,
            saved=saved,
            amap_available=amap_available,
            stage_messages=stage_messages,
        )
