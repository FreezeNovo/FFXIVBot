"""FF14 Bot 的最小运行配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    """应用运行配置；QQ 授权状态存储在本机 SQLite。"""

    database_path: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> "AppConfig":
        return cls(database_path=workspace_root / "data" / "ff14_bot.sqlite3")
