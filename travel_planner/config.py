from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    amap_api_key: str
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    database_path: Path = Path("data/travel_planner.db")
    mcp_timeout_seconds: int = 60

    @property
    def deepseek_ready(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def amap_ready(self) -> bool:
        return bool(self.amap_api_key)


def _secret(secrets: Mapping[str, Any] | None, name: str) -> str:
    if secrets:
        try:
            value = secrets.get(name, "")
            if value:
                return str(value)
        except Exception:
            pass
    return os.getenv(name, "")


def load_settings(secrets: Mapping[str, Any] | None = None) -> Settings:
    """从环境变量或 Streamlit Secrets 加载配置，不记录或回显密钥。"""
    database_value = _secret(secrets, "TRAVEL_PLANNER_DB") or "data/travel_planner.db"
    return Settings(
        deepseek_api_key=_secret(secrets, "DEEPSEEK_API_KEY"),
        amap_api_key=_secret(secrets, "AMAP_MAPS_API_KEY"),
        deepseek_model=_secret(secrets, "DEEPSEEK_MODEL") or "deepseek-chat",
        deepseek_base_url=_secret(secrets, "DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        database_path=Path(database_value),
        mcp_timeout_seconds=int(_secret(secrets, "MCP_TIMEOUT_SECONDS") or "60"),
    )

