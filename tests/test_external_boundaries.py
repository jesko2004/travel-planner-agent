from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from travel_planner.config import Settings
from travel_planner.mcp.amap_client import AmapMCPClient
from travel_planner.mcp.amap_client import AmapServiceError
from travel_planner.models import (
    EvidenceKind,
    PoiCandidate,
    SourceEvidence,
    TripRequest,
    VerificationStatus,
)
from travel_planner.services.model_client import DeepSeekPlanner
import travel_planner.services.model_client as model_client_module


def _request() -> TripRequest:
    return TripRequest(
        origin_city="上海",
        destination="北京",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 2),
        adults=2,
        children=1,
        child_ages=[8],
        total_budget=8000,
        hotel_budget_min=400,
        hotel_budget_max=700,
    )


def test_amap_receives_research_query_and_cannot_self_verify(monkeypatch):
    prompts: list[str] = []

    class FakeAgent:
        async def arun(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(
                content={
                    "destination_confirmed": True,
                    "administrative_area": "北京市",
                    "pois": [
                        {
                            "poi_id": "POI-1",
                            "name": "示例景点",
                            "address": "北京市示例地址",
                            "longitude": 116.39,
                            "latitude": 39.9,
                        }
                    ],
                }
            )

    client = AmapMCPClient(
        Settings(deepseek_api_key="test", amap_api_key="test")
    )
    monkeypatch.setattr(client, "_agent", lambda name, schema: FakeAgent())

    result = asyncio.run(client.research(_request()))

    assert "上海" not in prompts[0]
    assert "total_budget" not in prompts[0]
    evidence = result.pois[0].evidence
    assert evidence.status == VerificationStatus.UNVERIFIED
    assert evidence.tool_call_id is None
    assert evidence.kind == EvidenceKind.POI_LOCATION


def test_route_rejects_invalid_tool_parameters_before_agent(monkeypatch):
    client = AmapMCPClient(
        Settings(deepseek_api_key="test", amap_api_key="test")
    )
    calls = []
    monkeypatch.setattr(client, "_agent", lambda *args: calls.append(args))

    with pytest.raises(AmapServiceError, match="坐标"):
        asyncio.run(client.route("None,None", "116.39,39.90", "公共交通"))
    with pytest.raises(AmapServiceError, match="交通方式"):
        asyncio.run(client.route("116.39,39.90", "116.40,39.91", "飞行"))

    assert calls == []


def test_route_result_is_unverified_and_drops_untrusted_url(monkeypatch):
    class FakeAgent:
        async def arun(self, prompt):
            return SimpleNamespace(
                content={
                    "distance_meters": 1200,
                    "duration_minutes": 18,
                    "summary": "测试路线",
                    "navigation_url": "javascript:alert(1)",
                }
            )

    client = AmapMCPClient(
        Settings(deepseek_api_key="test", amap_api_key="test")
    )
    monkeypatch.setattr(client, "_agent", lambda *args: FakeAgent())

    result = asyncio.run(
        client.route("116.390000,39.900000", "116.400000,39.910000", "步行")
    )

    assert result.navigation_url == ""
    assert result.evidence is not None
    assert result.evidence.status == VerificationStatus.UNVERIFIED
    assert result.evidence.tool_call_id is None


def test_model_receives_planning_payload_without_child_ages(monkeypatch):
    prompts: list[str] = []

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        async def arun(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(
                content={
                    "title": "北京一日草案",
                    "overview": "测试",
                    "days": [],
                    "alternative_poi_ids": [],
                }
            )

    monkeypatch.setattr(model_client_module, "Agent", FakeAgent)
    planner = DeepSeekPlanner(
        Settings(deepseek_api_key="test", amap_api_key="test")
    )
    poi = PoiCandidate(
        poi_id="POI-1",
        name="示例景点",
        address="北京市示例地址",
        longitude=116.39,
        latitude=39.9,
        evidence=SourceEvidence(
            kind=EvidenceKind.POI_LOCATION,
            source="高德地图",
            tool_name="untrusted-test-adapter",
            raw_identifier="POI-1",
        ),
    )

    result = asyncio.run(planner.create_draft(_request(), [poi]))

    assert result.title == "北京一日草案"
    assert len(prompts) == 1
    assert "child_ages" not in prompts[0]
    assert '"origin_city":"上海"' in prompts[0]
