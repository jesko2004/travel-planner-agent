from __future__ import annotations

import asyncio
from datetime import date, time

import pytest

from travel_planner.config import Settings
from travel_planner.models import (
    DraftActivity,
    DraftDay,
    EvidenceKind,
    ItineraryDraft,
    PoiCandidate,
    ResearchBundle,
    RouteResult,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
)
from travel_planner.services.input_validation import SensitiveDataError
import travel_planner.workflow as workflow_module


def _request(**updates) -> TripRequest:
    values = {
        "origin_city": "上海",
        "destination": "北京",
        "start_date": date(2026, 10, 2),
        "end_date": date(2026, 10, 2),
        "adults": 2,
        "total_budget": 8000,
        "hotel_budget_min": 400,
        "hotel_budget_max": 700,
    }
    values.update(updates)
    return TripRequest(**values)


class _RepositorySpy:
    def __init__(self) -> None:
        self.saved = []

    def save_itinerary(self, itinerary) -> None:
        self.saved.append(itinerary)


def test_sensitive_request_stops_before_model_mcp_and_sqlite(monkeypatch):
    calls = {"model": 0, "mcp": 0}

    class PlannerSpy:
        async def create_draft(self, *args, **kwargs):
            calls["model"] += 1
            raise AssertionError("敏感输入不应到达模型")

    class AmapSpy:
        def __init__(self, settings):
            calls["mcp"] += 1

        async def __aenter__(self):
            raise AssertionError("敏感输入不应到达 MCP")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    planner = PlannerSpy()
    repository = _RepositorySpy()
    secret = "never-forward-this"
    monkeypatch.setattr(workflow_module, "DeepSeekPlanner", lambda settings: planner)
    monkeypatch.setattr(workflow_module, "AmapMCPClient", AmapSpy)
    workflow = workflow_module.TravelPlannerWorkflow(
        Settings(deepseek_api_key="test", amap_api_key=secret), repository
    )

    with pytest.raises(SensitiveDataError) as error:
        asyncio.run(workflow.generate(_request(intercity_transport=secret)))

    assert secret not in str(error.value)
    assert calls == {"model": 0, "mcp": 0}
    assert repository.saved == []


def test_complete_untrusted_tool_data_remains_unverified(monkeypatch):
    def poi(poi_id: str, name: str) -> PoiCandidate:
        return PoiCandidate(
            poi_id=poi_id,
            name=name,
            address=f"北京市{name}地址",
            longitude=116.39,
            latitude=39.9,
            evidence=SourceEvidence(
                kind=EvidenceKind.POI_LOCATION,
                source="高德地图",
                tool_name="untrusted-test-adapter",
                raw_identifier=poi_id,
                status=VerificationStatus.UNVERIFIED,
            ),
        )

    first = poi("POI-1", "第一站")
    second = poi("POI-2", "第二站")

    class PlannerStub:
        async def create_draft(self, request, pois, **kwargs):
            return ItineraryDraft(
                title="北京一日草案",
                overview="离线可信边界测试",
                days=[
                    DraftDay(
                        day=request.start_date,
                        title="第一天",
                        activities=[
                            DraftActivity(
                                day=request.start_date,
                                start_time=time(9, 0),
                                end_time=time(10, 0),
                                poi_id=first.poi_id,
                            ),
                            DraftActivity(
                                day=request.start_date,
                                start_time=time(11, 0),
                                end_time=time(12, 0),
                                poi_id=second.poi_id,
                            ),
                        ],
                    )
                ],
            )

    class AmapStub:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def research(self, request):
            return ResearchBundle(
                destination_confirmed=True,
                administrative_area="北京市",
                pois=[first, second],
            )

        async def route(self, origin, destination, transport):
            return RouteResult(
                distance_meters=3000,
                duration_minutes=20,
                summary="结构完整但没有可信调用记录",
                evidence=SourceEvidence(
                    kind=EvidenceKind.ROUTE,
                    source="高德地图",
                    tool_name="untrusted-test-adapter",
                    raw_identifier="route-1",
                    status=VerificationStatus.UNVERIFIED,
                ),
            )

    repository = _RepositorySpy()
    monkeypatch.setattr(
        workflow_module, "DeepSeekPlanner", lambda settings: PlannerStub()
    )
    monkeypatch.setattr(workflow_module, "AmapMCPClient", AmapStub)
    workflow = workflow_module.TravelPlannerWorkflow(
        Settings(deepseek_api_key="test", amap_api_key="test"), repository
    )

    result = asyncio.run(workflow.generate(_request()))

    assert result.saved is True
    assert result.itinerary.status == VerificationStatus.UNVERIFIED
    assert len(result.itinerary.request_digest) == 64
    assert repository.saved == [result.itinerary]
    assert any(
        issue.code in {"UNVERIFIED_POI", "UNVERIFIED_ROUTE"}
        for issue in result.itinerary.validation_issues
    )
    assert any("工具调用记录" in warning for warning in result.itinerary.warnings)
