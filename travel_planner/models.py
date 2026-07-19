from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Annotated, ClassVar, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_now() -> datetime:
    """Return an aware timestamp in the application's canonical timezone."""

    return datetime.now(BEIJING_TZ)


def _to_beijing(value: datetime) -> datetime:
    # Version-0 rows used naive local timestamps. Treating those values as
    # Beijing time is the only lossless compatibility rule for the migration.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


CityText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
ListItemText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
IdentifierText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
ChildAge = Annotated[int, Field(ge=0, le=17)]


class VerificationStatus(str, Enum):
    DRAFT = "draft"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    VERIFIED = "verified"


class EvidenceKind(str, Enum):
    POI_LOCATION = "poi_location"
    POI_OPERATING_STATUS = "poi_operating_status"
    CURRENT_WEATHER = "current_weather"
    WEATHER_FORECAST = "weather_forecast"
    ROUTE = "route"
    HOTEL_LOCATION = "hotel_location"


DEFAULT_EVIDENCE_TTLS: dict[EvidenceKind, timedelta] = {
    EvidenceKind.POI_LOCATION: timedelta(days=30),
    EvidenceKind.POI_OPERATING_STATUS: timedelta(hours=24),
    EvidenceKind.CURRENT_WEATHER: timedelta(hours=3),
    EvidenceKind.ROUTE: timedelta(hours=6),
    EvidenceKind.HOTEL_LOCATION: timedelta(days=7),
}


class UserProfile(ContractModel):
    schema_version: Literal[1] = 1
    profile_id: str = Field(default="default", min_length=1, max_length=64)
    created_at: datetime = Field(default_factory=beijing_now)
    updated_at: datetime = Field(default_factory=beijing_now)
    home_city: str = Field(default="", max_length=64)
    travel_pace: Literal["休闲", "适中", "紧凑"] = "适中"
    hotel_budget_min: int = Field(default=300, ge=0, le=100_000)
    hotel_budget_max: int = Field(default=800, ge=0, le=100_000)
    hotel_preferences: list[ListItemText] = Field(
        default_factory=lambda: ["近公共交通", "安静"], max_length=30
    )
    food_restrictions: list[ListItemText] = Field(default_factory=list, max_length=30)
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(21, 0)
    max_daily_walk_km: float = Field(default=10.0, gt=0, le=50)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return _to_beijing(value)

    @field_validator("home_city")
    @classmethod
    def validate_optional_city(cls, value: str) -> str:
        value = value.strip()
        if value and any(ord(character) < 32 for character in value):
            raise ValueError("常住城市不能包含控制字符")
        return value

    @model_validator(mode="after")
    def validate_profile(self) -> "UserProfile":
        if self.hotel_budget_max < self.hotel_budget_min:
            raise ValueError("住宿预算上限不能低于下限")
        if self.daily_end_time <= self.daily_start_time:
            raise ValueError("每日结束时间必须晚于开始时间")
        if self.updated_at < self.created_at:
            raise ValueError("更新时间不能早于创建时间")
        return self


class TripRequest(ContractModel):
    origin_city: CityText
    destination: CityText
    start_date: date
    end_date: date
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=10)
    child_ages: list[ChildAge] = Field(default_factory=list, max_length=10)
    rooms: int = Field(default=1, ge=1, le=10)
    total_budget: int = Field(ge=100, le=1_000_000)
    hotel_budget_min: int = Field(default=300, ge=0, le=100_000)
    hotel_budget_max: int = Field(default=800, ge=0, le=100_000)
    pace: Literal["休闲", "适中", "紧凑"] = "适中"
    local_transport: Literal["公共交通优先", "步行优先", "自驾优先"] = "公共交通优先"
    must_visit: list[ListItemText] = Field(default_factory=list, max_length=30)
    avoid: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_restrictions: list[ListItemText] = Field(default_factory=list, max_length=30)
    hotel_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)
    intercity_transport: str = Field(default="", max_length=1000)
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(21, 0)
    max_daily_walk_km: float = Field(default=10.0, gt=0, le=50)

    @field_validator("origin_city", "destination")
    @classmethod
    def reject_city_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("城市不能包含控制字符")
        return value

    @field_validator("intercity_transport")
    @classmethod
    def reject_unsafe_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("城际交通文本不能包含控制字符")
        return value

    @model_validator(mode="after")
    def validate_trip(self) -> "TripRequest":
        if self.end_date < self.start_date:
            raise ValueError("返程日期不能早于出发日期")
        if (self.end_date - self.start_date).days > 30:
            raise ValueError("第一版最多规划 31 天")
        if self.hotel_budget_max < self.hotel_budget_min:
            raise ValueError("住宿预算上限不能低于下限")
        if self.daily_end_time <= self.daily_start_time:
            raise ValueError("每日结束时间必须晚于开始时间")
        if self.children == 0 and self.child_ages:
            raise ValueError("没有儿童时儿童年龄列表必须为空")
        if self.children and len(self.child_ages) not in (0, self.children):
            raise ValueError("儿童年龄数量应与儿童人数一致，或留空")
        if self.rooms > self.adults + self.children:
            raise ValueError("房间数不能超过出行人数")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class SourceEvidence(ContractModel):
    kind: EvidenceKind
    source: str = Field(min_length=1, max_length=80)
    tool_name: str = Field(min_length=1, max_length=128)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=128)
    checked_at: datetime = Field(default_factory=beijing_now)
    expires_at: datetime | None = None
    is_realtime: bool = False
    raw_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"
    status: VerificationStatus = VerificationStatus.UNVERIFIED

    ttl_by_kind: ClassVar[dict[EvidenceKind, timedelta]] = DEFAULT_EVIDENCE_TTLS

    @field_validator("checked_at", "expires_at")
    @classmethod
    def normalize_evidence_time(cls, value: datetime | None) -> datetime | None:
        return _to_beijing(value) if value is not None else None

    @model_validator(mode="after")
    def apply_expiry_policy(self) -> "SourceEvidence":
        if self.kind == EvidenceKind.WEATHER_FORECAST:
            if self.expires_at is None:
                raise ValueError("天气预报证据必须提供供应商返回的过期时间")
        else:
            default_expiry = self.checked_at + self.ttl_by_kind[self.kind]
            if self.expires_at is None or self.expires_at > default_expiry:
                self.expires_at = default_expiry

        if self.expires_at is None or self.expires_at <= self.checked_at:
            raise ValueError("证据过期时间必须晚于查询时间")

        if self.status in {VerificationStatus.VERIFIED, VerificationStatus.EXPIRED}:
            if not self.tool_call_id or not self.raw_identifier:
                # Merely parsing a model-provided `verified` value must never
                # create trust. A controlled adapter must attach both records.
                self.status = VerificationStatus.UNVERIFIED
        return self

    @property
    def has_auditable_tool_record(self) -> bool:
        return bool(self.tool_call_id and self.raw_identifier)

    def status_at(self, now: datetime) -> VerificationStatus:
        """Derive effective trust at a caller-supplied deterministic time."""

        now = _to_beijing(now)
        if self.status == VerificationStatus.DRAFT:
            return VerificationStatus.DRAFT
        if not self.has_auditable_tool_record:
            return VerificationStatus.UNVERIFIED
        if self.status == VerificationStatus.UNVERIFIED:
            return VerificationStatus.UNVERIFIED
        if self.status == VerificationStatus.EXPIRED:
            return VerificationStatus.EXPIRED
        if now < self.checked_at:
            return VerificationStatus.UNVERIFIED
        if self.expires_at is not None and now >= self.expires_at:
            return VerificationStatus.EXPIRED
        return VerificationStatus.VERIFIED


class PoiCandidate(ContractModel):
    poi_id: IdentifierText
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=300)
    longitude: float | None = Field(default=None, ge=73.0, le=135.0)
    latitude: float | None = Field(default=None, ge=3.0, le=54.0)
    category: str = Field(default="景点", min_length=1, max_length=80)
    business_area: str = Field(default="", max_length=120)
    suggested_duration_minutes: int = Field(default=120, ge=30, le=720)
    estimated_cost: float = Field(default=0, ge=0, le=1_000_000)
    reservation_note: str = Field(default="", max_length=500)
    evidence: SourceEvidence


class PlatformLink(ContractModel):
    platform: Literal["携程", "同程", "飞猪", "美团"]
    url: str = Field(min_length=1, max_length=2048)
    search_text: str = Field(min_length=1, max_length=300)


class HotelCandidate(ContractModel):
    poi: PoiCandidate
    location_note: str = Field(default="", max_length=300)
    estimated_grade: str = Field(default="待确认", max_length=80)
    platform_links: list[PlatformLink] = Field(default_factory=list, max_length=4)
    confirmation_notice: str = "价格、库存、房型、发票和取消政策需在平台确认"


class Activity(ContractModel):
    activity_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    day: date
    start_time: time
    end_time: time
    poi: PoiCandidate
    estimated_cost: float = Field(default=0, ge=0, le=1_000_000)
    notes: str = Field(default="", max_length=1000)
    locked: bool = False


class RouteLeg(ContractModel):
    origin_activity_id: IdentifierText
    destination_activity_id: IdentifierText
    transport_mode: str = Field(min_length=1, max_length=40)
    distance_meters: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    summary: str = Field(default="", max_length=500)
    navigation_url: str = Field(default="", max_length=2048)
    evidence: SourceEvidence | None = None


class DayPlan(ContractModel):
    day: date
    title: str = Field(min_length=1, max_length=120)
    activities: list[Activity] = Field(default_factory=list, max_length=30)
    route_legs: list[RouteLeg] = Field(default_factory=list, max_length=30)


class WeatherInfo(ContractModel):
    summary: str = Field(min_length=1, max_length=1000)
    packing_advice: list[ListItemText] = Field(default_factory=list, max_length=30)
    evidence: SourceEvidence | None = None


class BudgetSummary(ContractModel):
    activities: float = Field(default=0, ge=0)
    accommodation_estimate: float = Field(default=0, ge=0)
    local_transport_estimate: float = Field(default=0, ge=0)
    food_estimate: float = Field(default=0, ge=0)
    other: float = Field(default=0, ge=0)
    total: float = Field(default=0, ge=0)


class ValidationIssue(ContractModel):
    severity: Literal["info", "warning", "error"]
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    day: date | None = None
    activity_id: str | None = Field(default=None, max_length=64)
    suggestion: str = Field(default="", max_length=500)


class Itinerary(ContractModel):
    schema_version: Literal[1] = 1
    itinerary_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    run_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    request_digest: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    created_at: datetime = Field(default_factory=beijing_now)
    updated_at: datetime = Field(default_factory=beijing_now)
    request: TripRequest
    title: str = Field(min_length=1, max_length=200)
    overview: str = Field(default="", max_length=2000)
    status: VerificationStatus = VerificationStatus.DRAFT
    days: list[DayPlan] = Field(default_factory=list, max_length=31)
    hotels: list[HotelCandidate] = Field(default_factory=list, max_length=30)
    weather: WeatherInfo | None = None
    budget: BudgetSummary = Field(default_factory=BudgetSummary)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    validation_issues: list[ValidationIssue] = Field(default_factory=list, max_length=500)
    alternative_poi_ids: list[IdentifierText] = Field(default_factory=list, max_length=30)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        return _to_beijing(value)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "Itinerary":
        if self.updated_at < self.created_at:
            raise ValueError("更新时间不能早于创建时间")
        return self


class DraftActivity(ContractModel):
    day: date
    start_time: time
    end_time: time
    poi_id: IdentifierText
    notes: str = Field(default="", max_length=1000)


class DraftDay(ContractModel):
    day: date
    title: str = Field(min_length=1, max_length=120)
    activities: list[DraftActivity] = Field(default_factory=list, max_length=30)


class ItineraryDraft(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    overview: str = Field(default="", max_length=2000)
    days: list[DraftDay] = Field(default_factory=list, max_length=31)
    alternative_poi_ids: list[IdentifierText] = Field(default_factory=list, max_length=30)


class ResearchBundle(ContractModel):
    destination_confirmed: bool
    administrative_area: str = Field(default="", max_length=120)
    weather_summary: str = Field(default="", max_length=1000)
    packing_advice: list[ListItemText] = Field(default_factory=list, max_length=30)
    pois: list[PoiCandidate] = Field(default_factory=list, max_length=100)
    hotel_pois: list[PoiCandidate] = Field(default_factory=list, max_length=30)


class RouteResult(ContractModel):
    """Controlled route result; model-supplied `verified` is forbidden."""

    distance_meters: int | None = Field(default=None, ge=1)
    duration_minutes: int | None = Field(default=None, ge=1)
    summary: str = Field(default="", max_length=500)
    navigation_url: str = Field(default="", max_length=2048)
    evidence: SourceEvidence | None = None


class LockedActivitySnapshot(ContractModel):
    activity_id: IdentifierText
    day: date
    start_time: time
    end_time: time
    poi_id: IdentifierText
    locked: bool = True

    @classmethod
    def from_activity(cls, activity: Activity) -> "LockedActivitySnapshot":
        return cls(
            activity_id=activity.activity_id,
            day=activity.day,
            start_time=activity.start_time,
            end_time=activity.end_time,
            poi_id=activity.poi.poi_id,
            locked=activity.locked,
        )


class ValidationContext(ContractModel):
    required_stage_names: ClassVar[frozenset[str]] = frozenset(
        {
            "normalize_request",
            "research_destination",
            "create_draft",
            "enrich_routes",
        }
    )
    now: datetime = Field(default_factory=beijing_now)
    destination_confirmed: bool = False
    approved_poi_ids: set[IdentifierText] = Field(default_factory=set, max_length=200)
    locked_activities: list[LockedActivitySnapshot] = Field(default_factory=list, max_length=100)
    required_stages: dict[str, bool] = Field(default_factory=dict, max_length=20)

    @field_validator("now")
    @classmethod
    def normalize_now(cls, value: datetime) -> datetime:
        return _to_beijing(value)

    @property
    def required_stages_complete(self) -> bool:
        return all(
            self.required_stages.get(stage, False)
            for stage in self.required_stage_names
        )
