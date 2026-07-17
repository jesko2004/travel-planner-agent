from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class VerificationStatus(str, Enum):
    DRAFT = "draft"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class UserProfile(BaseModel):
    home_city: str = ""
    travel_pace: Literal["休闲", "适中", "紧凑"] = "适中"
    hotel_budget_min: int = 300
    hotel_budget_max: int = 800
    hotel_preferences: list[str] = Field(default_factory=lambda: ["近公共交通", "安静"])
    food_restrictions: list[str] = Field(default_factory=list)
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(21, 0)
    max_daily_walk_km: float = 10.0


class TripRequest(BaseModel):
    origin_city: str
    destination: str
    start_date: date
    end_date: date
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=10)
    child_ages: list[int] = Field(default_factory=list)
    rooms: int = Field(default=1, ge=1, le=10)
    total_budget: int = Field(ge=100)
    hotel_budget_min: int = Field(default=300, ge=0)
    hotel_budget_max: int = Field(default=800, ge=0)
    pace: Literal["休闲", "适中", "紧凑"] = "适中"
    local_transport: Literal["公共交通优先", "步行优先", "自驾优先"] = "公共交通优先"
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)
    food_restrictions: list[str] = Field(default_factory=list)
    hotel_preferences: list[str] = Field(default_factory=list)
    intercity_transport: str = ""
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(21, 0)
    max_daily_walk_km: float = Field(default=10.0, gt=0, le=50)

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
        if self.children and len(self.child_ages) not in (0, self.children):
            raise ValueError("儿童年龄数量应与儿童人数一致，或留空")
        return self

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class SourceEvidence(BaseModel):
    source: str
    tool_name: str
    checked_at: datetime = Field(default_factory=datetime.now)
    is_realtime: bool = False
    status: VerificationStatus = VerificationStatus.UNVERIFIED


class PoiCandidate(BaseModel):
    poi_id: str
    name: str
    address: str
    longitude: float
    latitude: float
    category: str = "景点"
    business_area: str = ""
    suggested_duration_minutes: int = Field(default=120, ge=30, le=720)
    estimated_cost: float = Field(default=0, ge=0)
    reservation_note: str = ""
    evidence: SourceEvidence


class PlatformLink(BaseModel):
    platform: Literal["携程", "同程", "飞猪", "美团"]
    url: str
    search_text: str


class HotelCandidate(BaseModel):
    poi: PoiCandidate
    location_note: str = ""
    estimated_grade: str = "待确认"
    platform_links: list[PlatformLink] = Field(default_factory=list)
    confirmation_notice: str = "价格、库存、房型、发票和取消政策需在平台确认"


class Activity(BaseModel):
    activity_id: str = Field(default_factory=lambda: uuid4().hex)
    day: date
    start_time: time
    end_time: time
    poi: PoiCandidate
    estimated_cost: float = Field(default=0, ge=0)
    notes: str = ""
    locked: bool = False


class RouteLeg(BaseModel):
    origin_activity_id: str
    destination_activity_id: str
    transport_mode: str
    distance_meters: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    summary: str = ""
    navigation_url: str = ""
    evidence: SourceEvidence | None = None


class DayPlan(BaseModel):
    day: date
    title: str
    activities: list[Activity] = Field(default_factory=list)
    route_legs: list[RouteLeg] = Field(default_factory=list)


class WeatherInfo(BaseModel):
    summary: str
    packing_advice: list[str] = Field(default_factory=list)
    evidence: SourceEvidence | None = None


class BudgetSummary(BaseModel):
    activities: float = 0
    accommodation_estimate: float = 0
    local_transport_estimate: float = 0
    food_estimate: float = 0
    other: float = 0
    total: float = 0


class ValidationIssue(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    day: date | None = None
    activity_id: str | None = None
    suggestion: str = ""


class Itinerary(BaseModel):
    itinerary_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=datetime.now)
    request: TripRequest
    title: str
    overview: str
    status: VerificationStatus = VerificationStatus.DRAFT
    days: list[DayPlan] = Field(default_factory=list)
    hotels: list[HotelCandidate] = Field(default_factory=list)
    weather: WeatherInfo | None = None
    budget: BudgetSummary = Field(default_factory=BudgetSummary)
    warnings: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    alternative_poi_ids: list[str] = Field(default_factory=list)


class DraftActivity(BaseModel):
    day: date
    start_time: time
    end_time: time
    poi_id: str
    notes: str = ""


class DraftDay(BaseModel):
    day: date
    title: str
    activities: list[DraftActivity]


class ItineraryDraft(BaseModel):
    title: str
    overview: str
    days: list[DraftDay]
    alternative_poi_ids: list[str] = Field(default_factory=list)


class ResearchBundle(BaseModel):
    destination_confirmed: bool
    administrative_area: str = ""
    weather_summary: str = ""
    packing_advice: list[str] = Field(default_factory=list)
    pois: list[PoiCandidate] = Field(default_factory=list)
    hotel_pois: list[PoiCandidate] = Field(default_factory=list)


class RouteResult(BaseModel):
    distance_meters: int | None = None
    duration_minutes: int | None = None
    summary: str = ""
    navigation_url: str = ""
    verified: bool = False

