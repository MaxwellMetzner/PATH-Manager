from __future__ import annotations

import json
from pathlib import Path

from .models import AppState


class StateStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.state_path = self.data_dir / "state.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> AppState:
        if not self.state_path.exists():
            return AppState()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return AppState()

        disabled_entries = raw.get("disabled_entries", {"User": [], "System": []})
        snapshots = raw.get("recent_snapshot_files", [])
        return AppState(disabled_entries=disabled_entries, recent_snapshot_files=snapshots)

    def save(self, state: AppState) -> None:
        payload = {
            "disabled_entries": state.disabled_entries,
            "recent_snapshot_files": state.recent_snapshot_files,
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
