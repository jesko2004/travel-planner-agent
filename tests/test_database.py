from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from travel_planner.models import (
    Activity,
    DayPlan,
    EvidenceKind,
    HotelCandidate,
    Itinerary,
    PoiCandidate,
    RouteLeg,
    SourceEvidence,
    TripRequest,
    UserProfile,
    VerificationStatus,
    WeatherInfo,
)
from travel_planner.services.input_validation import SensitiveDataError, build_request_digest
from travel_planner.storage.database import (
    CURRENT_SCHEMA_VERSION,
    DatabaseIntegrityError,
    DatabaseMigrationError,
    SQLiteRepository,
    UnsupportedDatabaseVersionError,
)


BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def database_itinerary() -> Itinerary:
    request = TripRequest(
        origin_city="上海",
        destination="北京",
        start_date=date(2026, 10, 2),
        end_date=date(2026, 10, 4),
        adults=2,
        total_budget=8000,
        hotel_budget_min=400,
        hotel_budget_max=700,
    )
    first_poi = PoiCandidate(
        poi_id="B000A83M61",
        name="示例景点",
        address="北京市示例地址",
        longitude=116.397,
        latitude=39.908,
        evidence=SourceEvidence(
            kind=EvidenceKind.POI_LOCATION,
            source="高德地图",
            tool_name="maps_search_detail",
            tool_call_id="poi-call-1",
            raw_identifier="B000A83M61",
            status=VerificationStatus.VERIFIED,
        ),
    )
    second_poi = first_poi.model_copy(
        update={
            "poi_id": "B000SECOND",
            "name": "第二景点",
            "evidence": first_poi.evidence.model_copy(
                update={
                    "tool_call_id": "poi-call-2",
                    "raw_identifier": "B000SECOND",
                }
            ),
        }
    )
    first_activity = Activity(
        activity_id="activity-13812345678",
        day=request.start_date,
        start_time=time(9, 0),
        end_time=time(11, 0),
        poi=first_poi,
        estimated_cost=50,
    )
    second_activity = Activity(
        activity_id="activity-second",
        day=request.start_date,
        start_time=time(12, 0),
        end_time=time(14, 0),
        poi=second_poi,
        estimated_cost=100,
    )
    route = RouteLeg(
        origin_activity_id=first_activity.activity_id,
        destination_activity_id=second_activity.activity_id,
        transport_mode="公共交通",
        distance_meters=3000,
        duration_minutes=30,
        evidence=SourceEvidence(
            kind=EvidenceKind.ROUTE,
            source="高德地图",
            tool_name="route_planning",
            tool_call_id="route-call-1",
            raw_identifier="route-1",
            status=VerificationStatus.VERIFIED,
        ),
    )
    hotel = HotelCandidate(
        poi=PoiCandidate(
            poi_id="B000HOTEL",
            name="示例酒店",
            address="北京市酒店地址",
            longitude=116.4,
            latitude=39.9,
            category="酒店",
            evidence=SourceEvidence(
                kind=EvidenceKind.HOTEL_LOCATION,
                source="高德地图",
                tool_name="maps_search_detail",
                tool_call_id="hotel-call-1",
                raw_identifier="B000HOTEL",
                status=VerificationStatus.VERIFIED,
            ),
        )
    )
    weather = WeatherInfo(
        summary="晴",
        evidence=SourceEvidence(
            kind=EvidenceKind.CURRENT_WEATHER,
            source="高德地图",
            tool_name="maps_weather",
            tool_call_id="weather-call-1",
            raw_identifier="weather-beijing",
            status=VerificationStatus.VERIFIED,
        ),
    )
    return Itinerary(
        request=request,
        title="北京三日行",
        overview="测试行程",
        status=VerificationStatus.VERIFIED,
        hotels=[hotel],
        weather=weather,
        days=[
            DayPlan(
                day=request.start_date,
                title="第一天",
                activities=[first_activity, second_activity],
                route_legs=[route],
            )
        ],
    )


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _table_columns(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }


def _make_evidence_legacy(evidence: dict[str, object]) -> None:
    for field in (
        "kind",
        "tool_call_id",
        "expires_at",
        "raw_identifier",
        "coordinate_system",
    ):
        evidence.pop(field, None)
    evidence["status"] = "verified"


def _create_legacy_database(
    path: Path,
    *,
    profile_payload: str,
    itinerary_payload: str,
    itinerary: Itinerary,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE profiles (
                profile_key TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE itineraries (
                itinerary_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                destination TEXT NOT NULL,
                start_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO profiles(profile_key, data_json, updated_at) VALUES(?, ?, ?)",
            ("default", profile_payload, "2026-01-01 00:00:00"),
        )
        connection.execute(
            """
            INSERT INTO itineraries(
                itinerary_id, title, destination, start_date, created_at, status, data_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                itinerary.itinerary_id,
                itinerary.title,
                itinerary.request.destination,
                itinerary.request.start_date.isoformat(),
                itinerary.created_at.isoformat(),
                itinerary.status.value,
                itinerary_payload,
            ),
        )


def test_database_v1_round_trip_and_metadata(tmp_path, database_itinerary):
    database_path = tmp_path / "travel.db"
    repository = SQLiteRepository(database_path)
    repository.initialize()

    assert _user_version(database_path) == CURRENT_SCHEMA_VERSION
    assert repository.last_backup_path is None
    assert not (tmp_path / "backups").exists()

    old_time = datetime(2025, 1, 1, 9, 0, tzinfo=BEIJING_TIMEZONE)
    profile = UserProfile(
        home_city="上海",
        created_at=old_time,
        updated_at=old_time,
    )
    repository.save_profile(profile)
    loaded_profile = repository.load_profile()
    assert loaded_profile.home_city == "上海"
    assert loaded_profile.profile_id == profile.profile_id
    assert loaded_profile.schema_version == CURRENT_SCHEMA_VERSION
    assert loaded_profile.created_at == old_time
    assert loaded_profile.updated_at > old_time

    original_created_at = database_itinerary.created_at
    repository.save_itinerary(database_itinerary)
    loaded = repository.get_itinerary(database_itinerary.itinerary_id)
    assert loaded is not None
    assert loaded.title == database_itinerary.title
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    assert loaded.created_at == original_created_at
    assert loaded.updated_at >= loaded.created_at
    assert len(loaded.run_id) == 32
    assert loaded.request_digest == build_request_digest(loaded.request)
    assert len(repository.list_itineraries()) == 1

    repository.delete_itinerary(database_itinerary.itinerary_id)
    assert repository.get_itinerary(database_itinerary.itinerary_id) is None


def test_updates_preserve_created_at_and_refresh_updated_at(tmp_path, database_itinerary):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    repository.save_itinerary(database_itinerary)
    first = repository.get_itinerary(database_itinerary.itinerary_id)
    assert first is not None

    repository.save_itinerary(first.model_copy(update={"title": "更新后的标题"}))
    second = repository.get_itinerary(database_itinerary.itinerary_id)
    assert second is not None
    assert second.title == "更新后的标题"
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at


def test_persisted_verified_status_expires_on_read_without_rewriting_database(
    tmp_path, database_itinerary
):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    repository.save_itinerary(database_itinerary)
    route_evidence = database_itinerary.days[0].route_legs[0].evidence
    assert route_evidence is not None and route_evidence.expires_at is not None

    loaded = repository.get_itinerary(
        database_itinerary.itinerary_id, now=route_evidence.expires_at
    )
    history = repository.list_itineraries(now=route_evidence.expires_at)

    assert loaded is not None
    assert loaded.status == VerificationStatus.EXPIRED
    assert history[0]["status"] == VerificationStatus.EXPIRED.value
    with sqlite3.connect(repository.path) as connection:
        stored_status = connection.execute(
            "SELECT status FROM itineraries WHERE itinerary_id=?",
            (database_itinerary.itinerary_id,),
        ).fetchone()[0]
    assert stored_status == VerificationStatus.VERIFIED.value


@pytest.mark.parametrize(
    "stored_status",
    [VerificationStatus.DRAFT, VerificationStatus.UNVERIFIED],
)
def test_persisted_lower_trust_status_is_never_promoted_on_read(
    tmp_path, database_itinerary, stored_status
):
    repository = SQLiteRepository(tmp_path / f"{stored_status.value}.db")
    repository.initialize()
    itinerary = database_itinerary.model_copy(
        deep=True, update={"status": stored_status}
    )
    repository.save_itinerary(itinerary)

    loaded = repository.get_itinerary(itinerary.itinerary_id)
    history = repository.list_itineraries()

    assert loaded is not None
    assert loaded.status == stored_status
    assert history[0]["status"] == stored_status.value


def test_database_payload_does_not_contain_api_key(tmp_path, database_itinerary):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    repository.save_itinerary(database_itinerary)
    raw = (tmp_path / "travel.db").read_bytes()
    assert b"DEEPSEEK_API_KEY" not in raw
    assert b"AMAP_MAPS_API_KEY" not in raw


def test_sensitive_profile_is_rejected_before_sqlite_connect(tmp_path):
    database_path = tmp_path / "travel.db"
    repository = SQLiteRepository(database_path)
    secret = "never-save-this"
    profile = UserProfile(food_restrictions=[f"密码: {secret}"])

    with pytest.raises(SensitiveDataError) as error:
        repository.save_profile(profile)

    assert secret not in str(error.value)
    assert not database_path.exists()


def test_sensitive_itinerary_is_rejected_before_sqlite_connect(
    tmp_path, database_itinerary
):
    database_path = tmp_path / "travel.db"
    repository = SQLiteRepository(database_path)
    secret = "never-save-this"
    itinerary = database_itinerary.model_copy(
        update={"overview": f"password={secret}"}
    )

    with pytest.raises(SensitiveDataError) as error:
        repository.save_itinerary(itinerary)

    assert secret not in str(error.value)
    assert not database_path.exists()


def test_legacy_v0_database_is_backed_up_and_migrated(tmp_path, database_itinerary):
    database_path = tmp_path / "travel.db"
    profile = UserProfile(home_city="上海")
    legacy_profile = profile.model_dump(
        mode="json",
        exclude={"schema_version", "profile_id", "created_at", "updated_at"},
    )
    legacy_itinerary = database_itinerary.model_dump(
        mode="json",
        exclude={"schema_version", "run_id", "request_digest", "updated_at"},
    )
    for day_plan in legacy_itinerary["days"]:
        for activity in day_plan["activities"]:
            _make_evidence_legacy(activity["poi"]["evidence"])
        for route in day_plan["route_legs"]:
            _make_evidence_legacy(route["evidence"])
    for hotel in legacy_itinerary["hotels"]:
        _make_evidence_legacy(hotel["poi"]["evidence"])
    weather_evidence = legacy_itinerary["weather"]["evidence"]
    _make_evidence_legacy(weather_evidence)
    legacy_itinerary["days"][0]["activities"][0]["poi"]["longitude"] = 0.0
    legacy_itinerary["days"][0]["activities"][0]["poi"]["latitude"] = 0.0
    _create_legacy_database(
        database_path,
        profile_payload=json.dumps(legacy_profile, ensure_ascii=False),
        itinerary_payload=json.dumps(legacy_itinerary, ensure_ascii=False),
        itinerary=database_itinerary,
    )

    repository = SQLiteRepository(database_path)
    repository.initialize()

    assert _user_version(database_path) == CURRENT_SCHEMA_VERSION
    assert repository.last_backup_path is not None
    assert repository.last_backup_path.exists()
    assert repository.last_backup_path.parent == tmp_path / "backups"
    assert _user_version(repository.last_backup_path) == 0
    assert "profile_key" in _table_columns(repository.last_backup_path, "profiles")
    assert "profile_id" in _table_columns(database_path, "profiles")
    assert "updated_at" in _table_columns(database_path, "itineraries")

    migrated_profile = repository.load_profile()
    migrated_itinerary = repository.get_itinerary(database_itinerary.itinerary_id)
    assert migrated_profile.home_city == "上海"
    assert migrated_profile.schema_version == CURRENT_SCHEMA_VERSION
    assert migrated_profile.created_at.tzinfo is not None
    assert migrated_profile.updated_at.tzinfo is not None
    assert migrated_itinerary is not None
    assert migrated_itinerary.schema_version == CURRENT_SCHEMA_VERSION
    assert migrated_itinerary.created_at == database_itinerary.created_at
    assert migrated_itinerary.updated_at == migrated_itinerary.created_at
    assert len(migrated_itinerary.request_digest) == 64
    assert migrated_itinerary.status == VerificationStatus.UNVERIFIED
    migrated_activity = migrated_itinerary.days[0].activities[0]
    assert migrated_activity.poi.longitude is None
    assert migrated_activity.poi.latitude is None
    assert migrated_activity.poi.evidence.kind == EvidenceKind.POI_LOCATION
    assert migrated_activity.poi.evidence.status == VerificationStatus.UNVERIFIED
    assert migrated_itinerary.days[0].route_legs[0].evidence is not None
    assert (
        migrated_itinerary.days[0].route_legs[0].evidence.kind
        == EvidenceKind.ROUTE
    )
    assert migrated_itinerary.hotels[0].poi.evidence.kind == EvidenceKind.HOTEL_LOCATION
    assert migrated_itinerary.weather is not None
    assert migrated_itinerary.weather.evidence is not None
    assert migrated_itinerary.weather.evidence.kind == EvidenceKind.CURRENT_WEATHER


def test_failed_migration_rolls_back_and_preserves_backup(tmp_path, database_itinerary):
    database_path = tmp_path / "travel.db"
    _create_legacy_database(
        database_path,
        profile_payload="{not-valid-json",
        itinerary_payload=database_itinerary.model_dump_json(),
        itinerary=database_itinerary,
    )

    repository = SQLiteRepository(database_path)
    with pytest.raises(DatabaseMigrationError, match="原数据库和迁移前备份均已保留"):
        repository.initialize()

    assert _user_version(database_path) == 0
    assert "profile_key" in _table_columns(database_path, "profiles")
    assert "profile_id" not in _table_columns(database_path, "profiles")
    with sqlite3.connect(database_path) as connection:
        stored = connection.execute(
            "SELECT data_json FROM profiles WHERE profile_key='default'"
        ).fetchone()
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert stored is not None and stored[0] == "{not-valid-json"
    assert "profiles_v0" not in names
    assert repository.last_backup_path is not None
    assert repository.last_backup_path.exists()
    assert _user_version(repository.last_backup_path) == 0


def test_migration_readback_failure_rolls_back(
    tmp_path, database_itinerary, monkeypatch
):
    database_path = tmp_path / "travel.db"
    profile = UserProfile(home_city="上海")
    _create_legacy_database(
        database_path,
        profile_payload=json.dumps(
            profile.model_dump(
                mode="json",
                exclude={"schema_version", "profile_id", "created_at", "updated_at"},
            ),
            ensure_ascii=False,
        ),
        itinerary_payload=database_itinerary.model_dump_json(),
        itinerary=database_itinerary,
    )
    repository = SQLiteRepository(database_path)

    def fail_readback(*args, **kwargs):
        raise DatabaseIntegrityError("模拟迁移回读失败")

    monkeypatch.setattr(repository, "_validate_migrated_rows", fail_readback)
    with pytest.raises(DatabaseMigrationError):
        repository.initialize()

    assert _user_version(database_path) == 0
    assert "profile_key" in _table_columns(database_path, "profiles")
    assert "profile_id" not in _table_columns(database_path, "profiles")
    assert repository.last_backup_path is not None
    assert repository.last_backup_path.exists()


def test_sensitive_legacy_payload_is_not_duplicated_into_backup(
    tmp_path, database_itinerary
):
    database_path = tmp_path / "travel.db"
    secret = "never-copy-this"
    _create_legacy_database(
        database_path,
        profile_payload=json.dumps(
            {"food_restrictions": [f"密码: {secret}"]}, ensure_ascii=True
        ),
        itinerary_payload=database_itinerary.model_dump_json(),
        itinerary=database_itinerary,
    )

    repository = SQLiteRepository(database_path)
    with pytest.raises(SensitiveDataError) as error:
        repository.initialize()

    assert secret not in str(error.value)
    assert _user_version(database_path) == 0
    assert repository.last_backup_path is None
    assert not (tmp_path / "backups").exists()


def test_sensitive_legacy_phone_is_rejected_before_backup(
    tmp_path, database_itinerary
):
    database_path = tmp_path / "travel.db"
    _create_legacy_database(
        database_path,
        profile_payload=json.dumps(
            {"food_restrictions": ["13812345678"]}, ensure_ascii=True
        ),
        itinerary_payload=database_itinerary.model_dump_json(),
        itinerary=database_itinerary,
    )

    repository = SQLiteRepository(database_path)
    with pytest.raises(SensitiveDataError) as error:
        repository.initialize()

    assert "13812345678" not in str(error.value)
    assert repository.last_backup_path is None
    assert not (tmp_path / "backups").exists()


def test_configured_bare_key_is_rejected_before_connect_and_legacy_backup(
    tmp_path, database_itinerary
):
    configured_key = "b2" * 16
    new_database = tmp_path / "new.db"
    repository = SQLiteRepository(
        new_database, forbidden_values=[configured_key]
    )
    with pytest.raises(SensitiveDataError) as save_error:
        repository.save_profile(UserProfile(food_restrictions=[configured_key]))
    assert configured_key not in str(save_error.value)
    assert not new_database.exists()

    legacy_database = tmp_path / "legacy.db"
    _create_legacy_database(
        legacy_database,
        profile_payload=json.dumps(
            {"food_restrictions": [configured_key]}, ensure_ascii=True
        ),
        itinerary_payload=database_itinerary.model_dump_json(),
        itinerary=database_itinerary,
    )
    legacy_repository = SQLiteRepository(
        legacy_database, forbidden_values=[configured_key]
    )
    with pytest.raises(SensitiveDataError) as migration_error:
        legacy_repository.initialize()
    assert configured_key not in str(migration_error.value)
    assert legacy_repository.last_backup_path is None
    assert not (tmp_path / "backups").exists()


def test_post_save_readback_failure_rolls_back(
    tmp_path, database_itinerary, monkeypatch
):
    repository = SQLiteRepository(tmp_path / "travel.db")
    repository.initialize()
    original_insert = repository._insert_itinerary

    def insert_corrupted_copy(connection, itinerary):
        original_insert(connection, itinerary)
        corrupted = itinerary.model_copy(update={"title": "被篡改的回读标题"})
        connection.execute(
            "UPDATE itineraries SET data_json=? WHERE itinerary_id=?",
            (corrupted.model_dump_json(), itinerary.itinerary_id),
        )

    monkeypatch.setattr(repository, "_insert_itinerary", insert_corrupted_copy)
    with pytest.raises(DatabaseIntegrityError, match="行程保存后完整性检查失败"):
        repository.save_itinerary(database_itinerary)

    assert repository.get_itinerary(database_itinerary.itinerary_id) is None


def test_unsupported_database_version_is_not_overwritten(tmp_path):
    database_path = tmp_path / "travel.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE future_data(value TEXT NOT NULL)")
        connection.execute("INSERT INTO future_data(value) VALUES('keep-me')")
        connection.execute("PRAGMA user_version=2")

    repository = SQLiteRepository(database_path)
    with pytest.raises(UnsupportedDatabaseVersionError, match="不支持 SQLite schema 版本 2"):
        repository.initialize()

    assert _user_version(database_path) == 2
    assert repository.last_backup_path is None
    with sqlite3.connect(database_path) as connection:
        value = connection.execute("SELECT value FROM future_data").fetchone()[0]
    assert value == "keep-me"
