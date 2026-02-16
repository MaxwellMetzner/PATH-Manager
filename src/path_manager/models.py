from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Scope(str, Enum):
    USER = "User"
    SYSTEM = "System"


@dataclass
class PathEntry:
    path: str
    scope: Scope
    enabled: bool = True
    source_index: Optional[int] = None
    exists: bool = False
    accessible: bool = False
    warning: Optional[str] = None


@dataclass
class Snapshot:
    created_at: str
    reason: str
    user_path: str
    system_path: str

    @staticmethod
    def now(reason: str, user_path: str, system_path: str) -> "Snapshot":
        return Snapshot(
            created_at=datetime.now().isoformat(timespec="seconds"),
            reason=reason,
            user_path=user_path,
            system_path=system_path,
        )


@dataclass
class AppState:
    disabled_entries: dict[str, list[str]] = field(default_factory=lambda: {"User": [], "System": []})
    recent_snapshot_files: list[str] = field(default_factory=list)
