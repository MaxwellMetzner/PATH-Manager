from __future__ import annotations

import ctypes
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable
import winreg

from .models import AppState, PathEntry, Scope, Snapshot
from .profiles import StateStore

HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


class EnvStore:
    def __init__(self, app_data_dir: Path) -> None:
        self.app_data_dir = app_data_dir
        self.backups_dir = self.app_data_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.state_store = StateStore(self.app_data_dir)
        self.state = self.state_store.load()

    def read_raw(self, scope: Scope) -> str:
        return self._read_registry_path(scope)

    def read_entries(self, scope: Scope) -> list[PathEntry]:
        raw = self._read_registry_path(scope)
        entries: list[PathEntry] = []
        disabled = {self._normalize_path(value) for value in self.state.disabled_entries.get(scope.value, [])}

        for index, path in enumerate(self._split_path(raw)):
            normalized = self._normalize_path(path)
            enabled = normalized not in disabled
            expanded = os.path.expandvars(path)
            exists = os.path.isdir(expanded)
            accessible = os.access(expanded, os.R_OK) if exists else False
            entries.append(
                PathEntry(
                    path=path,
                    scope=scope,
                    enabled=enabled,
                    source_index=index,
                    exists=exists,
                    accessible=accessible,
                )
            )

        for disabled_path in self.state.disabled_entries.get(scope.value, []):
            normalized = self._normalize_path(disabled_path)
            if normalized in {self._normalize_path(entry.path) for entry in entries}:
                continue
            expanded = os.path.expandvars(disabled_path)
            exists = os.path.isdir(expanded)
            accessible = os.access(expanded, os.R_OK) if exists else False
            entries.append(
                PathEntry(
                    path=disabled_path,
                    scope=scope,
                    enabled=False,
                    source_index=None,
                    exists=exists,
                    accessible=accessible,
                )
            )

        return entries

    def write_entries(self, scope: Scope, entries: Iterable[PathEntry], reason: str = "Manual update") -> None:
        active_paths: list[str] = []
        disabled_paths: list[str] = []

        for entry in entries:
            clean = entry.path.strip().strip('"')
            if not clean:
                continue
            if entry.enabled:
                active_paths.append(clean)
            else:
                disabled_paths.append(clean)

        self.create_snapshot(reason)

        self.state.disabled_entries[scope.value] = disabled_paths
        self.state_store.save(self.state)

        new_path = ";".join(active_paths)
        self._write_registry_path(scope, new_path)
        self.broadcast_environment_change()

    def create_snapshot(self, reason: str) -> Path:
        snapshot = Snapshot.now(
            reason=reason,
            user_path=self._read_registry_path(Scope.USER),
            system_path=self._read_registry_path(Scope.SYSTEM),
        )
        filename = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path = self.backups_dir / filename
        path.write_text(json.dumps(snapshot.__dict__, indent=2), encoding="utf-8")

        self.state.recent_snapshot_files.insert(0, str(path))
        self.state.recent_snapshot_files = self.state.recent_snapshot_files[:50]
        self.state_store.save(self.state)
        return path

    def restore_snapshot(self, snapshot_path: Path, scope: Scope | None = None) -> None:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.create_snapshot("Before restore")

        if scope is None:
            self._write_registry_path(Scope.SYSTEM, payload.get("system_path", ""))
            self._write_registry_path(Scope.USER, payload.get("user_path", ""))
        elif scope == Scope.USER:
            self._write_registry_path(Scope.USER, payload.get("user_path", ""))
        else:
            self._write_registry_path(Scope.SYSTEM, payload.get("system_path", ""))

        self.broadcast_environment_change()

    def latest_snapshots(self) -> list[Path]:
        candidates: list[Path] = []
        for item in self.state.recent_snapshot_files:
            p = Path(item)
            if p.exists():
                candidates.append(p)
        return candidates

    @staticmethod
    def broadcast_environment_change() -> None:
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            3000,
            0,
        )

    @staticmethod
    def _split_path(path_value: str) -> list[str]:
        return [part.strip().strip('"') for part in path_value.split(";") if part.strip().strip('"')]

    @staticmethod
    def _normalize_path(path_value: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.expandvars(path_value.strip().strip('"'))))

    @staticmethod
    def _read_registry_path(scope: Scope) -> str:
        if scope == Scope.USER:
            root = winreg.HKEY_CURRENT_USER
            subkey = r"Environment"
            access = winreg.KEY_READ
        else:
            root = winreg.HKEY_LOCAL_MACHINE
            subkey = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
            access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY

        try:
            with winreg.OpenKey(root, subkey, 0, access) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                return value
        except FileNotFoundError:
            return ""

    @staticmethod
    def _write_registry_path(scope: Scope, value: str) -> None:
        if scope == Scope.USER:
            root = winreg.HKEY_CURRENT_USER
            subkey = r"Environment"
            access = winreg.KEY_SET_VALUE
        else:
            root = winreg.HKEY_LOCAL_MACHINE
            subkey = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
            access = winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY

        with winreg.OpenKey(root, subkey, 0, access) as key:
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)
