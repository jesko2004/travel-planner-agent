from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterable
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from travel_planner.models import (
    Itinerary,
    UserProfile,
    ValidationContext,
    VerificationStatus,
    beijing_now,
)
from travel_planner.services.input_validation import build_request_digest, reject_sensitive_data
from travel_planner.services.validator import ItineraryValidator


CURRENT_SCHEMA_VERSION = 1
BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


class DatabaseMigrationError(RuntimeError):
    """Raised when a database cannot be migrated without risking existing data."""


class DatabaseIntegrityError(RuntimeError):
    """Raised when persisted data cannot be read back without alteration."""


class UnsupportedDatabaseVersionError(RuntimeError):
    """Raised when the database was created by an unsupported schema version."""


class SQLiteRepository:
    def __init__(
        self, path: Path, *, forbidden_values: Iterable[str] = ()
    ) -> None:
        self.path = path
        self.last_backup_path: Path | None = None
        self.set_forbidden_values(forbidden_values)

    def set_forbidden_values(self, values: Iterable[str]) -> None:
        self._forbidden_values = tuple(
            value for value in values if isinstance(value, str) and len(value) >= 8
        )

    def _open_connection(self, *, enable_wal: bool) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        if enable_wal:
            connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def connect(self) -> sqlite3.Connection:
        return self._open_connection(enable_wal=True)

    def initialize(self) -> None:
        """Create schema v1 or transactionally migrate an existing v0 database."""

        database_existed = self.path.exists()
        connection = self._open_connection(enable_wal=False)
        try:
            version = self._user_version(connection)
            if version == CURRENT_SCHEMA_VERSION:
                self._validate_v1_schema(connection)
                self._reject_sensitive_legacy_payloads(
                    connection, self._user_tables(connection)
                )
            elif version == 0:
                tables = self._user_tables(connection)
                if database_existed and tables:
                    self._reject_sensitive_legacy_payloads(connection, tables)
                    self.last_backup_path = self._create_backup(connection)
                    self._migrate_v0_to_v1(connection)
                else:
                    self._create_new_v1_database(connection)
            else:
                raise UnsupportedDatabaseVersionError(
                    f"不支持 SQLite schema 版本 {version}；当前仅支持版本 {CURRENT_SCHEMA_VERSION}"
                )

            # Journal mode is changed only after schema creation/migration succeeds,
            # so a failed migration does not mutate the original database first.
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()

    def _create_new_v1_database(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._create_v1_schema(connection)
            connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
            self._validate_v1_schema(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_v0_to_v1(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            tables = self._user_tables(connection)
            migrated_profiles: dict[str, UserProfile] = {}
            migrated_itineraries: dict[str, Itinerary] = {}

            if "profiles" in tables:
                connection.execute("ALTER TABLE profiles RENAME TO profiles_v0")
            if "itineraries" in tables:
                connection.execute("ALTER TABLE itineraries RENAME TO itineraries_v0")

            self._create_v1_schema(connection)

            if "profiles" in tables:
                rows = connection.execute(
                    "SELECT profile_key, data_json, updated_at FROM profiles_v0"
                ).fetchall()
                for row in rows:
                    profile = self._upgrade_legacy_profile(row)
                    self._insert_profile(connection, profile)
                    migrated_profiles[profile.profile_id] = profile
                connection.execute("DROP TABLE profiles_v0")

            if "itineraries" in tables:
                rows = connection.execute(
                    "SELECT itinerary_id, created_at, data_json FROM itineraries_v0"
                ).fetchall()
                for row in rows:
                    itinerary = self._upgrade_legacy_itinerary(row)
                    self._insert_itinerary(connection, itinerary)
                    migrated_itineraries[itinerary.itinerary_id] = itinerary
                connection.execute("DROP TABLE itineraries_v0")

            connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
            self._validate_v1_schema(connection)
            self._validate_migrated_rows(
                connection,
                profiles=migrated_profiles,
                itineraries=migrated_itineraries,
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise DatabaseMigrationError(
                "SQLite v0 到 v1 迁移失败；原数据库和迁移前备份均已保留"
            ) from exc

    @staticmethod
    def _validate_migrated_rows(
        connection: sqlite3.Connection,
        *,
        profiles: dict[str, UserProfile],
        itineraries: dict[str, Itinerary],
    ) -> None:
        for profile_id, expected in profiles.items():
            row = connection.execute(
                "SELECT data_json FROM profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            actual = UserProfile.model_validate_json(row["data_json"]) if row else None
            if actual != expected:
                raise DatabaseIntegrityError("偏好迁移后回读完整性检查失败")
        for itinerary_id, expected in itineraries.items():
            row = connection.execute(
                "SELECT data_json FROM itineraries WHERE itinerary_id=?",
                (itinerary_id,),
            ).fetchone()
            actual = Itinerary.model_validate_json(row["data_json"]) if row else None
            if actual != expected:
                raise DatabaseIntegrityError("行程迁移后回读完整性检查失败")

    def _create_backup(self, source: sqlite3.Connection) -> Path:
        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(BEIJING_TIMEZONE).strftime("%Y%m%dT%H%M%S%f")
        backup_path = backup_directory / (
            f"{self.path.stem}.v0-{timestamp}-{uuid4().hex[:8]}.db"
        )
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise DatabaseMigrationError("迁移前 SQLite 备份完整性检查失败")
        finally:
            destination.close()
        return backup_path

    @staticmethod
    def _create_v1_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                profile_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS itineraries (
                itinerary_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                run_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                title TEXT NOT NULL,
                destination TEXT NOT NULL,
                start_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                data_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_itineraries_created_at "
            "ON itineraries(created_at DESC)"
        )

    @classmethod
    def _validate_v1_schema(cls, connection: sqlite3.Connection) -> None:
        if cls._user_version(connection) != CURRENT_SCHEMA_VERSION:
            raise DatabaseIntegrityError("SQLite schema 版本未正确设置为 1")

        required_columns = {
            "profiles": {
                "profile_id",
                "schema_version",
                "created_at",
                "updated_at",
                "data_json",
            },
            "itineraries": {
                "itinerary_id",
                "schema_version",
                "run_id",
                "request_digest",
                "title",
                "destination",
                "start_date",
                "created_at",
                "updated_at",
                "status",
                "data_json",
            },
        }
        for table, expected in required_columns.items():
            actual = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = expected - actual
            if missing:
                raise DatabaseIntegrityError(
                    f"SQLite v1 表 {table} 缺少字段：{', '.join(sorted(missing))}"
                )

    @staticmethod
    def _user_version(connection: sqlite3.Connection) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _user_tables(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _reject_sensitive_legacy_payloads(
        self, connection: sqlite3.Connection, tables: set[str]
    ) -> None:
        # Scan before backup so migration cannot duplicate forbidden content.
        # Malformed legacy schemas are still backed up and then rejected by
        # the transactional migrator; only known JSON payload columns are read.
        for table in ("profiles", "itineraries"):
            if table not in tables:
                continue
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "data_json" not in columns:
                continue
            rows = connection.execute(f"SELECT data_json FROM {table}").fetchall()
            for index, row in enumerate(rows):
                raw_payload = row["data_json"]
                try:
                    parsed_payload = json.loads(raw_payload)
                except (json.JSONDecodeError, TypeError):
                    # The transactional migration will report malformed JSON;
                    # raw scanning still prevents obvious plaintext leaks.
                    reject_sensitive_data(
                        raw_payload,
                        root_field=f"legacy_{table}[{index}]",
                        forbidden_values=self._forbidden_values,
                    )
                    continue
                reject_sensitive_data(
                    raw_payload,
                    root_field=f"legacy_{table}[{index}]",
                    forbidden_values=self._forbidden_values,
                    include_unlabeled_numeric=False,
                )
                reject_sensitive_data(
                    parsed_payload,
                    root_field=f"legacy_{table}[{index}]",
                    forbidden_values=self._forbidden_values,
                )

    def _upgrade_legacy_profile(self, row: sqlite3.Row) -> UserProfile:
        payload = self._load_json_object(row["data_json"], "旧版用户偏好")
        legacy_updated_at = self._legacy_sqlite_timestamp(row["updated_at"])
        payload["schema_version"] = CURRENT_SCHEMA_VERSION
        payload.setdefault("profile_id", row["profile_key"] or "default")
        payload.setdefault("created_at", legacy_updated_at.isoformat())
        payload.setdefault("updated_at", legacy_updated_at.isoformat())
        return UserProfile.model_validate(payload)

    def _upgrade_legacy_itinerary(self, row: sqlite3.Row) -> Itinerary:
        payload = self._load_json_object(row["data_json"], "旧版行程")
        self._upgrade_legacy_itinerary_content(payload)
        created_at = self._coerce_beijing_datetime(
            payload.get("created_at", row["created_at"])
        )
        payload["schema_version"] = CURRENT_SCHEMA_VERSION
        payload.setdefault("run_id", uuid4().hex)
        payload.setdefault("request_digest", self._request_digest(payload.get("request", {})))
        payload["created_at"] = created_at.isoformat()
        payload.setdefault("updated_at", created_at.isoformat())
        return Itinerary.model_validate(payload)

    @classmethod
    def _upgrade_legacy_itinerary_content(cls, payload: dict[str, Any]) -> None:
        """Translate the real v0 nested shape into conservative v1 data.

        Version 0 evidence had no kind, expiry, tool-call identifier, raw
        identifier, or coordinate-system marker. It therefore cannot retain a
        trusted status during migration. Old fallback POIs also used ``0, 0``
        to mean "unknown"; v1 represents those coordinates as ``None``.
        """

        for day_plan in payload.get("days", []):
            if not isinstance(day_plan, dict):
                continue
            for activity in day_plan.get("activities", []):
                if isinstance(activity, dict):
                    cls._upgrade_legacy_poi(activity.get("poi"), "poi_location")
            for route in day_plan.get("route_legs", []):
                if isinstance(route, dict):
                    cls._upgrade_legacy_evidence(route.get("evidence"), "route")

        for hotel in payload.get("hotels", []):
            if isinstance(hotel, dict):
                cls._upgrade_legacy_poi(hotel.get("poi"), "hotel_location")

        weather = payload.get("weather")
        if isinstance(weather, dict):
            cls._upgrade_legacy_evidence(weather.get("evidence"), "current_weather")

        if payload.get("status") != "draft":
            payload["status"] = "unverified"

    @classmethod
    def _upgrade_legacy_poi(cls, value: Any, evidence_kind: str) -> None:
        if not isinstance(value, dict):
            return
        longitude = value.get("longitude")
        latitude = value.get("latitude")
        if not cls._coordinates_in_mainland_range(longitude, latitude):
            value["longitude"] = None
            value["latitude"] = None
        cls._upgrade_legacy_evidence(value.get("evidence"), evidence_kind)

    @staticmethod
    def _coordinates_in_mainland_range(longitude: Any, latitude: Any) -> bool:
        if isinstance(longitude, bool) or isinstance(latitude, bool):
            return False
        if not isinstance(longitude, (int, float)) or not isinstance(
            latitude, (int, float)
        ):
            return False
        return 73.0 <= float(longitude) <= 135.0 and 3.0 <= float(latitude) <= 54.0

    @staticmethod
    def _upgrade_legacy_evidence(value: Any, evidence_kind: str) -> None:
        if not isinstance(value, dict):
            return
        value["kind"] = evidence_kind
        value["status"] = "unverified"
        value.pop("tool_call_id", None)
        value.pop("raw_identifier", None)
        # The v1 model deterministically applies the configured TTL from the
        # legacy checked_at timestamp. A legacy payload cannot assert a later
        # supplier expiry.
        value.pop("expires_at", None)
        value["coordinate_system"] = "GCJ-02"

    @staticmethod
    def _load_json_object(raw: str, description: str) -> dict[str, Any]:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DatabaseMigrationError(f"{description}不是 JSON 对象")
        return payload

    @staticmethod
    def _legacy_sqlite_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            # SQLite CURRENT_TIMESTAMP is UTC.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BEIJING_TIMEZONE)

    @staticmethod
    def _coerce_beijing_datetime(value: datetime | str) -> datetime:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
        return parsed.astimezone(BEIJING_TIMEZONE)

    @staticmethod
    def _request_digest(request: Any) -> str:
        return build_request_digest(request)

    @staticmethod
    def _assert_current_model_schema(model: UserProfile | Itinerary) -> None:
        if getattr(model, "schema_version", CURRENT_SCHEMA_VERSION) != CURRENT_SCHEMA_VERSION:
            raise UnsupportedDatabaseVersionError(
                f"对象 schema 版本必须为 {CURRENT_SCHEMA_VERSION}"
            )

    def _prepare_profile(
        self, profile: UserProfile, existing: UserProfile | None
    ) -> UserProfile:
        self._assert_current_model_schema(profile)
        now = datetime.now(BEIJING_TIMEZONE)
        created_at = existing.created_at if existing is not None else profile.created_at
        return UserProfile.model_validate(
            {
                **profile.model_dump(),
                "schema_version": CURRENT_SCHEMA_VERSION,
                "profile_id": existing.profile_id if existing is not None else profile.profile_id,
                "created_at": self._coerce_beijing_datetime(created_at),
                "updated_at": now,
            }
        )

    def _prepare_itinerary(
        self, itinerary: Itinerary, existing: Itinerary | None
    ) -> Itinerary:
        self._assert_current_model_schema(itinerary)
        now = datetime.now(BEIJING_TIMEZONE)
        created_at = existing.created_at if existing is not None else itinerary.created_at
        digest = self._request_digest(itinerary.request)
        return Itinerary.model_validate(
            {
                **itinerary.model_dump(),
                "schema_version": CURRENT_SCHEMA_VERSION,
                "request_digest": digest,
                "created_at": self._coerce_beijing_datetime(created_at),
                "updated_at": now,
            }
        )

    def _insert_profile(self, connection: sqlite3.Connection, profile: UserProfile) -> None:
        connection.execute(
            """
            INSERT INTO profiles(profile_id, schema_version, created_at, updated_at, data_json)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                data_json=excluded.data_json
            """,
            (
                profile.profile_id,
                profile.schema_version,
                profile.created_at.isoformat(),
                profile.updated_at.isoformat(),
                profile.model_dump_json(),
            ),
        )

    def _insert_itinerary(self, connection: sqlite3.Connection, itinerary: Itinerary) -> None:
        connection.execute(
            """
            INSERT INTO itineraries(
                itinerary_id, schema_version, run_id, request_digest, title,
                destination, start_date, created_at, updated_at, status, data_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(itinerary_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                run_id=excluded.run_id,
                request_digest=excluded.request_digest,
                title=excluded.title,
                destination=excluded.destination,
                start_date=excluded.start_date,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                status=excluded.status,
                data_json=excluded.data_json
            """,
            (
                itinerary.itinerary_id,
                itinerary.schema_version,
                itinerary.run_id,
                itinerary.request_digest,
                itinerary.title,
                itinerary.request.destination,
                itinerary.request.start_date.isoformat(),
                itinerary.created_at.isoformat(),
                itinerary.updated_at.isoformat(),
                itinerary.status.value,
                itinerary.model_dump_json(),
            ),
        )

    def save_profile(self, profile: UserProfile) -> None:
        reject_sensitive_data(
            profile,
            root_field="profile",
            forbidden_values=self._forbidden_values,
        )
        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT data_json FROM profiles WHERE profile_id=?", (profile.profile_id,)
                ).fetchone()
                existing = UserProfile.model_validate_json(row["data_json"]) if row else None
                saved = self._prepare_profile(profile, existing)
                self._insert_profile(connection, saved)
                persisted = connection.execute(
                    "SELECT data_json FROM profiles WHERE profile_id=?", (saved.profile_id,)
                ).fetchone()
                reloaded = (
                    UserProfile.model_validate_json(persisted["data_json"])
                    if persisted
                    else None
                )
                if reloaded != saved:
                    raise DatabaseIntegrityError("偏好保存后完整性检查失败")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def load_profile(self) -> UserProfile:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT data_json FROM profiles ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        profile = (
            UserProfile.model_validate_json(row["data_json"])
            if row
            else UserProfile()
        )
        reject_sensitive_data(
            profile,
            root_field="profile",
            forbidden_values=self._forbidden_values,
        )
        return profile

    @staticmethod
    def _refresh_persisted_trust(
        itinerary: Itinerary, *, now: datetime | None = None
    ) -> Itinerary:
        """Revalidate persisted data at read time without ever promoting trust."""

        original_status = itinerary.status
        completed = original_status != VerificationStatus.DRAFT
        approved_poi_ids = {
            activity.poi.poi_id
            for day_plan in itinerary.days
            for activity in day_plan.activities
        }
        approved_poi_ids.update(hotel.poi.poi_id for hotel in itinerary.hotels)
        context = ValidationContext(
            now=now or beijing_now(),
            destination_confirmed=original_status
            in {
                VerificationStatus.VERIFIED,
                VerificationStatus.EXPIRED,
            },
            approved_poi_ids=approved_poi_ids,
            required_stages={
                stage: completed for stage in ValidationContext.required_stage_names
            },
        )
        ItineraryValidator().apply_status(itinerary, context)

        # A read can reveal expiry or corruption, but cannot manufacture new
        # evidence for a result that was stored at a lower trust level.
        if original_status == VerificationStatus.DRAFT:
            itinerary.status = VerificationStatus.DRAFT
        elif (
            original_status == VerificationStatus.UNVERIFIED
            and itinerary.status
            in {VerificationStatus.VERIFIED, VerificationStatus.EXPIRED}
        ):
            itinerary.status = VerificationStatus.UNVERIFIED
        elif (
            original_status == VerificationStatus.EXPIRED
            and itinerary.status == VerificationStatus.VERIFIED
        ):
            itinerary.status = VerificationStatus.EXPIRED
        return itinerary

    def save_itinerary(self, itinerary: Itinerary) -> None:
        reject_sensitive_data(
            itinerary,
            root_field="itinerary",
            forbidden_values=self._forbidden_values,
        )
        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT data_json FROM itineraries WHERE itinerary_id=?",
                    (itinerary.itinerary_id,),
                ).fetchone()
                existing = Itinerary.model_validate_json(row["data_json"]) if row else None
                saved = self._prepare_itinerary(itinerary, existing)
                self._insert_itinerary(connection, saved)
                persisted = connection.execute(
                    "SELECT data_json FROM itineraries WHERE itinerary_id=?",
                    (saved.itinerary_id,),
                ).fetchone()
                reloaded = (
                    Itinerary.model_validate_json(persisted["data_json"])
                    if persisted
                    else None
                )
                if reloaded != saved:
                    raise DatabaseIntegrityError("行程保存后完整性检查失败")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def get_itinerary(
        self, itinerary_id: str, *, now: datetime | None = None
    ) -> Itinerary | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT data_json FROM itineraries WHERE itinerary_id=?", (itinerary_id,)
            ).fetchone()
        if row is None:
            return None
        itinerary = Itinerary.model_validate_json(row["data_json"])
        reject_sensitive_data(
            itinerary,
            root_field="itinerary",
            forbidden_values=self._forbidden_values,
        )
        return self._refresh_persisted_trust(itinerary, now=now)

    def list_itineraries(
        self, limit: int = 50, *, now: datetime | None = None
    ) -> list[dict[str, str]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT itinerary_id, title, destination, start_date,
                       created_at, updated_at, data_json
                FROM itineraries ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result: list[dict[str, str]] = []
        effective_now = now or beijing_now()
        for row in rows:
            itinerary = Itinerary.model_validate_json(row["data_json"])
            reject_sensitive_data(
                itinerary,
                root_field="itinerary",
                forbidden_values=self._forbidden_values,
            )
            itinerary = self._refresh_persisted_trust(itinerary, now=effective_now)
            metadata = {
                "itinerary_id": row["itinerary_id"],
                "title": row["title"],
                "destination": row["destination"],
                "start_date": row["start_date"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": itinerary.status.value,
            }
            reject_sensitive_data(
                metadata,
                root_field="itinerary_metadata",
                forbidden_values=self._forbidden_values,
            )
            result.append(metadata)
        return result

    def delete_itinerary(self, itinerary_id: str) -> None:
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM itineraries WHERE itinerary_id=?", (itinerary_id,)
                )
