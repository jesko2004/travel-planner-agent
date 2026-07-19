from datetime import date

import pytest
from pydantic import ValidationError

from travel_planner.services.input_validation import (
    PlanningPayload,
    ResearchQuery,
    SensitiveDataCategory,
    SensitiveDataError,
    build_request_digest,
    normalize_trip_request,
)


def _payload(**updates):
    value = {
        "origin_city": " 上海 ",
        "destination": " 北京 ",
        "start_date": date(2026, 10, 2),
        "end_date": date(2026, 10, 4),
        "adults": 2,
        "total_budget": 8_000,
    }
    value.update(updates)
    return value


def test_normalize_trims_and_deduplicates_without_mutating_source():
    source = _payload(must_visit=[" 故宫 ", "故宫", "天坛"])
    normalized = normalize_trip_request(source)
    assert normalized.origin_city == "上海"
    assert normalized.destination == "北京"
    assert normalized.must_visit == ["故宫", "天坛"]
    assert source["must_visit"] == [" 故宫 ", "故宫", "天坛"]


@pytest.mark.parametrize(
    "category,sensitive_value",
    [
        (SensitiveDataCategory.API_KEY, "sk-" + "A" * 24),
        (
            SensitiveDataCategory.PRIVATE_KEY,
            "-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key",
        ),
        (SensitiveDataCategory.COOKIE, "cookie" + "=session-value"),
        (SensitiveDataCategory.PASSWORD, "password" + "=example-value"),
        (SensitiveDataCategory.NATIONAL_ID, "110105" + "19491231002" + "X"),
        (SensitiveDataCategory.BANK_CARD, "4111" + "1111" + "1111" + "1111"),
        (SensitiveDataCategory.PHONE, "138" + "1234" + "5678"),
        (SensitiveDataCategory.HOME_ADDRESS, "家庭" + "住址：北京市某街道 1 号"),
    ],
)
def test_sensitive_categories_are_rejected_without_echo(category, sensitive_value):
    with pytest.raises(SensitiveDataError) as captured:
        normalize_trip_request(_payload(intercity_transport=sensitive_value))
    assert category in {finding.category for finding in captured.value.findings}
    assert sensitive_value not in str(captured.value)
    assert all(not hasattr(finding, "matched_text") for finding in captured.value.findings)


def test_sensitive_extra_field_is_rejected_before_schema_error():
    value = _payload(api_key="sk-" + "B" * 24)
    with pytest.raises(SensitiveDataError) as captured:
        normalize_trip_request(value)
    assert captured.value.findings[0].field.endswith("api_key")


def test_loaded_bare_key_is_rejected_by_exact_value_without_echo():
    configured_key = "a1" * 16
    with pytest.raises(SensitiveDataError) as captured:
        normalize_trip_request(
            _payload(intercity_transport=configured_key),
            forbidden_values=[configured_key],
        )
    assert configured_key not in str(captured.value)
    assert captured.value.findings[0].category == SensitiveDataCategory.API_KEY


def test_ordinary_extra_field_is_forbidden():
    with pytest.raises(ValidationError) as captured:
        normalize_trip_request(_payload(ordinary_extra="not allowed"))
    assert captured.value.errors()[0]["type"] == "extra_forbidden"


def test_external_payloads_are_minimized_and_forbid_extra_fields(trip_request):
    request = trip_request.model_copy(update={"children": 1, "child_ages": [8]})
    planning = PlanningPayload.from_request(request)
    research = ResearchQuery.from_request(request)

    assert "child_ages" not in planning.model_dump()
    assert "origin_city" not in research.model_dump()
    assert "total_budget" not in research.model_dump()

    with pytest.raises(ValidationError) as captured:
        PlanningPayload.model_validate({**planning.model_dump(), "verified": True})
    assert captured.value.errors()[0]["type"] == "extra_forbidden"


def test_payload_literal_constraints_apply_to_direct_construction(trip_request):
    payload = PlanningPayload.from_request(trip_request).model_dump()
    payload["pace"] = "极速"
    with pytest.raises(ValidationError):
        PlanningPayload.model_validate(payload)


def test_request_digest_is_stable_after_normalization():
    first = _payload(must_visit=[" 故宫 ", "故宫"])
    second = _payload(must_visit=["故宫"])
    assert build_request_digest(first) == build_request_digest(second)
    assert len(build_request_digest(first)) == 64
