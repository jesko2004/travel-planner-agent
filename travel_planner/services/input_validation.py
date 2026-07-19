from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, time
from enum import Enum
from hashlib import sha256
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from travel_planner.models import (
    CityText,
    ContractModel,
    ListItemText,
    TripRequest,
)


class SensitiveDataCategory(str, Enum):
    API_KEY = "api_key"
    PRIVATE_KEY = "private_key"
    COOKIE = "cookie"
    PASSWORD = "password"
    NATIONAL_ID = "national_id"
    BANK_CARD = "bank_card"
    PHONE = "phone"
    HOME_ADDRESS = "home_address"


class SensitiveDataFinding(ContractModel):
    field: str = Field(min_length=1, max_length=200)
    category: SensitiveDataCategory


class SensitiveDataError(ValueError):
    """Safe rejection that deliberately retains no matched source text."""

    def __init__(self, findings: Iterable[SensitiveDataFinding]) -> None:
        unique = {
            (finding.field, finding.category): finding
            for finding in findings
        }
        self.findings = tuple(
            unique[key]
            for key in sorted(unique, key=lambda item: (item[0], item[1].value))
        )
        fields = ", ".join(
            f"{finding.field}({finding.category.value})" for finding in self.findings
        )
        super().__init__(f"检测到禁止的敏感信息：{fields}")


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)
_API_KEY_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{16,}\b|"
    r"(?:api[_\s-]?key|access[_\s-]?token|secret[_\s-]?key|密钥)"
    r"\s*[:：=]\s*[A-Za-z0-9_./+\-=]{8,})",
    re.IGNORECASE,
)
_COOKIE_RE = re.compile(r"(?:cookie|session[_\s-]?id)\s*[:：=]\s*\S{4,}", re.IGNORECASE)
_PASSWORD_RE = re.compile(
    r"(?:password|passwd|pwd|密码)\s*[:：=]\s*\S{4,}", re.IGNORECASE
)
_NATIONAL_ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")
_LABELED_BANK_CARD_RE = re.compile(
    r"(?:银行卡|卡号|bank\s*card)\s*[:：=]\s*(?:\d[ -]?){12,18}\d",
    re.IGNORECASE,
)
_HOME_ADDRESS_RE = re.compile(
    r"(?:家庭住址|家庭地址|家庭详细地址|home\s+address)\s*[:：=]\s*\S.{3,}",
    re.IGNORECASE,
)

_SENSITIVE_FIELD_NAMES: dict[str, SensitiveDataCategory] = {
    "api_key": SensitiveDataCategory.API_KEY,
    "apikey": SensitiveDataCategory.API_KEY,
    "access_token": SensitiveDataCategory.API_KEY,
    "secret_key": SensitiveDataCategory.API_KEY,
    "private_key": SensitiveDataCategory.PRIVATE_KEY,
    "cookie": SensitiveDataCategory.COOKIE,
    "password": SensitiveDataCategory.PASSWORD,
    "passwd": SensitiveDataCategory.PASSWORD,
    "pwd": SensitiveDataCategory.PASSWORD,
}


def _luhn_valid(digits: str) -> bool:
    if not 16 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        number = int(character)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _categories_in_text(value: str) -> set[SensitiveDataCategory]:
    categories: set[SensitiveDataCategory] = set()
    if _PRIVATE_KEY_RE.search(value):
        categories.add(SensitiveDataCategory.PRIVATE_KEY)
    if _API_KEY_RE.search(value):
        categories.add(SensitiveDataCategory.API_KEY)
    if _COOKIE_RE.search(value):
        categories.add(SensitiveDataCategory.COOKIE)
    if _PASSWORD_RE.search(value):
        categories.add(SensitiveDataCategory.PASSWORD)
    if _NATIONAL_ID_RE.search(value):
        categories.add(SensitiveDataCategory.NATIONAL_ID)
    if _PHONE_RE.search(value):
        categories.add(SensitiveDataCategory.PHONE)
    if _HOME_ADDRESS_RE.search(value):
        categories.add(SensitiveDataCategory.HOME_ADDRESS)

    if _LABELED_BANK_CARD_RE.search(value):
        categories.add(SensitiveDataCategory.BANK_CARD)
    else:
        for match in _BANK_CARD_RE.finditer(value):
            digits = re.sub(r"\D", "", match.group(0))
            if _NATIONAL_ID_RE.fullmatch(digits):
                continue
            if _luhn_valid(digits):
                categories.add(SensitiveDataCategory.BANK_CARD)
                break
    return categories


def _walk_values(value: object, path: str = "request") -> Iterable[tuple[str, object]]:
    if isinstance(value, ContractModel):
        value = value.model_dump(mode="python")
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, child
            yield from _walk_values(child, child_path)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, child
            yield from _walk_values(child, child_path)


def find_sensitive_data(
    value: object,
    *,
    root_field: str = "request",
    forbidden_values: Iterable[str] = (),
) -> list[SensitiveDataFinding]:
    """Return only safe field/category metadata, never matched source fragments."""

    findings: list[SensitiveDataFinding] = []
    exact_secrets = tuple(
        item for item in forbidden_values if isinstance(item, str) and len(item) >= 8
    )
    root_items: Iterable[tuple[str, object]]
    if isinstance(value, (ContractModel, Mapping, list, tuple, set, frozenset)):
        root_items = _walk_values(value, root_field)
    else:
        root_items = [(root_field, value)]

    for field_path, item in root_items:
        field_name = field_path.rsplit(".", 1)[-1].lower()
        field_category = _SENSITIVE_FIELD_NAMES.get(field_name)
        if field_category is not None and item not in (None, "", [], {}):
            findings.append(SensitiveDataFinding(field=field_path, category=field_category))
        if isinstance(item, str):
            if any(secret in item for secret in exact_secrets):
                findings.append(
                    SensitiveDataFinding(
                        field=field_path,
                        category=SensitiveDataCategory.API_KEY,
                    )
                )
            findings.extend(
                SensitiveDataFinding(field=field_path, category=category)
                for category in _categories_in_text(item)
            )
    return findings


def reject_sensitive_data(
    value: object,
    *,
    root_field: str = "request",
    forbidden_values: Iterable[str] = (),
) -> None:
    findings = find_sensitive_data(
        value,
        root_field=root_field,
        forbidden_values=forbidden_values,
    )
    if findings:
        raise SensitiveDataError(findings)


_LIST_FIELDS = (
    "must_visit",
    "avoid",
    "food_preferences",
    "food_restrictions",
    "hotel_preferences",
)


def _normalized_list(value: object) -> object:
    if not isinstance(value, (list, tuple)):
        return value
    result: list[object] = []
    seen: set[object] = set()
    for item in value:
        normalized: object = item.strip() if isinstance(item, str) else item
        try:
            is_duplicate = normalized in seen
        except TypeError:
            is_duplicate = False
        if is_duplicate:
            continue
        result.append(normalized)
        try:
            seen.add(normalized)
        except TypeError:
            pass
    return result


def normalize_trip_request(
    value: TripRequest | Mapping[str, object],
    *,
    forbidden_values: Iterable[str] = (),
) -> TripRequest:
    """Scan, normalize and validate one request before any external boundary."""

    raw: dict[str, Any]
    if isinstance(value, TripRequest):
        raw = value.model_dump(mode="python")
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        raise TypeError("旅行需求必须是 TripRequest 或字段映射")

    forbidden_values = tuple(forbidden_values)
    reject_sensitive_data(raw, forbidden_values=forbidden_values)
    normalized = dict(raw)
    for field in ("origin_city", "destination", "intercity_transport"):
        if isinstance(normalized.get(field), str):
            normalized[field] = normalized[field].strip()
    for field in _LIST_FIELDS:
        if field in normalized:
            normalized[field] = _normalized_list(normalized[field])

    request = TripRequest.model_validate(normalized)
    reject_sensitive_data(request, forbidden_values=forbidden_values)
    return request


class PlanningPayload(ContractModel):
    origin_city: CityText
    destination: CityText
    start_date: date
    end_date: date
    adults: int = Field(ge=1, le=20)
    children: int = Field(ge=0, le=10)
    rooms: int = Field(ge=1, le=10)
    total_budget: int = Field(ge=100, le=1_000_000)
    hotel_budget_min: int = Field(ge=0, le=100_000)
    hotel_budget_max: int = Field(ge=0, le=100_000)
    pace: Literal["休闲", "适中", "紧凑"]
    local_transport: Literal["公共交通优先", "步行优先", "自驾优先"]
    must_visit: list[ListItemText] = Field(default_factory=list, max_length=30)
    avoid: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_restrictions: list[ListItemText] = Field(default_factory=list, max_length=30)
    hotel_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)
    intercity_transport: str = Field(default="", max_length=1000)
    daily_start_time: time
    daily_end_time: time
    max_daily_walk_km: float = Field(gt=0, le=50)

    @model_validator(mode="after")
    def reject_sensitive_fields(self) -> "PlanningPayload":
        reject_sensitive_data(self)
        return self

    @classmethod
    def from_request(
        cls,
        request: TripRequest | Mapping[str, object],
        *,
        forbidden_values: Iterable[str] = (),
    ) -> "PlanningPayload":
        normalized = normalize_trip_request(
            request, forbidden_values=forbidden_values
        )
        return cls.model_validate(
            normalized.model_dump(
                include={
                    "origin_city",
                    "destination",
                    "start_date",
                    "end_date",
                    "adults",
                    "children",
                    "rooms",
                    "total_budget",
                    "hotel_budget_min",
                    "hotel_budget_max",
                    "pace",
                    "local_transport",
                    "must_visit",
                    "avoid",
                    "food_preferences",
                    "food_restrictions",
                    "hotel_preferences",
                    "intercity_transport",
                    "daily_start_time",
                    "daily_end_time",
                    "max_daily_walk_km",
                }
            )
        )


class ResearchQuery(ContractModel):
    destination: CityText
    start_date: date
    end_date: date
    must_visit: list[ListItemText] = Field(default_factory=list, max_length=30)
    avoid: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)
    food_restrictions: list[ListItemText] = Field(default_factory=list, max_length=30)
    hotel_preferences: list[ListItemText] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def reject_sensitive_fields(self) -> "ResearchQuery":
        reject_sensitive_data(self)
        return self

    @classmethod
    def from_request(
        cls,
        request: TripRequest | Mapping[str, object],
        *,
        forbidden_values: Iterable[str] = (),
    ) -> "ResearchQuery":
        normalized = normalize_trip_request(
            request, forbidden_values=forbidden_values
        )
        return cls.model_validate(
            normalized.model_dump(
                include={
                    "destination",
                    "start_date",
                    "end_date",
                    "must_visit",
                    "avoid",
                    "food_preferences",
                    "food_restrictions",
                    "hotel_preferences",
                }
            )
        )


def build_request_digest(request: TripRequest | Mapping[str, object]) -> str:
    """Build a stable opaque digest from the already-minimized planning DTO."""

    payload = PlanningPayload.from_request(request)
    canonical = payload.model_dump_json(exclude_none=True)
    return sha256(canonical.encode("utf-8")).hexdigest()
