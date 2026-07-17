from __future__ import annotations

import sqlite3
from pathlib import Path

from travel_planner.models import Itinerary, UserProfile


class SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS itineraries (
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

    def save_profile(self, profile: UserProfile) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO profiles(profile_key, data_json, updated_at)
                VALUES('default', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(profile_key) DO UPDATE SET
                    data_json=excluded.data_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (profile.model_dump_json(),),
            )

    def load_profile(self) -> UserProfile:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM profiles WHERE profile_key='default'"
            ).fetchone()
        return UserProfile.model_validate_json(row["data_json"]) if row else UserProfile()

    def save_itinerary(self, itinerary: Itinerary) -> None:
        payload = itinerary.model_dump_json()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO itineraries(
                    itinerary_id, title, destination, start_date, created_at, status, data_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(itinerary_id) DO UPDATE SET
                    title=excluded.title,
                    status=excluded.status,
                    data_json=excluded.data_json
                """,
                (
                    itinerary.itinerary_id,
                    itinerary.title,
                    itinerary.request.destination,
                    itinerary.request.start_date.isoformat(),
                    itinerary.created_at.isoformat(),
                    itinerary.status.value,
                    payload,
                ),
            )
        reloaded = self.get_itinerary(itinerary.itinerary_id)
        if reloaded is None or reloaded.itinerary_id != itinerary.itinerary_id:
            raise RuntimeError("行程保存后完整性检查失败")

    def get_itinerary(self, itinerary_id: str) -> Itinerary | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data_json FROM itineraries WHERE itinerary_id=?", (itinerary_id,)
            ).fetchone()
        return Itinerary.model_validate_json(row["data_json"]) if row else None

    def list_itineraries(self, limit: int = 50) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT itinerary_id, title, destination, start_date, created_at, status
                FROM itineraries ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_itinerary(self, itinerary_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM itineraries WHERE itinerary_id=?", (itinerary_id,))

