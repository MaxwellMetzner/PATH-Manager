from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import Iterable

from .models import PathEntry, Scope


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    scope: Scope | None = None
    path: str | None = None


@dataclass
class CommandResolution:
    command: str
    winner: str | None
    all_matches: list[str]


def analyze_entries(entries: Iterable[PathEntry], scope: Scope) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[str, int] = {}
    normalized_enabled: list[str] = []

    enabled_entries = [entry for entry in entries if entry.enabled]
    unresolved_var_pattern = re.compile(r"%[^%]+%")

    for idx, entry in enumerate(enabled_entries):
        cleaned = entry.path.strip().strip('"')
        expanded = os.path.expandvars(cleaned)
        normalized = os.path.normcase(os.path.normpath(expanded)) if expanded else ""

        if not cleaned:
            issues.append(Issue("warning", "empty", "Empty path entry detected.", scope, entry.path))
            continue

        if normalized in seen:
            issues.append(
                Issue(
                    "warning",
                    "duplicate",
                    f"Duplicate entry appears at positions {seen[normalized] + 1} and {idx + 1}.",
                    scope,
                    entry.path,
                )
            )
        else:
            seen[normalized] = idx

        normalized_enabled.append(normalized)

        if cleaned in {".", ".\\", "%CD%"}:
            issues.append(
                Issue(
                    "warning",
                    "risky-cwd",
                    "Current-directory style entries are risky because command resolution depends on launch location.",
                    scope,
                    entry.path,
                )
            )

        unresolved_vars = unresolved_var_pattern.findall(expanded)
        if unresolved_vars:
            issues.append(
                Issue(
                    "warning",
                    "env-unresolved",
                    f"Unresolved environment variable reference(s): {', '.join(unresolved_vars)}.",
                    scope,
                    entry.path,
                )
            )

        if not os.path.isabs(expanded) and not unresolved_vars:
            issues.append(
                Issue(
                    "warning",
                    "relative",
                    "Relative path entry detected; use an absolute path to avoid inconsistent behavior.",
                    scope,
                    entry.path,
                )
            )

        if unresolved_vars:
            continue

        if not os.path.isdir(expanded):
            issues.append(Issue("warning", "missing", "Directory does not exist.", scope, entry.path))
        elif not os.access(expanded, os.R_OK):
            issues.append(Issue("warning", "inaccessible", "Directory exists but cannot be read.", scope, entry.path))

        if cleaned != entry.path:
            issues.append(Issue("info", "format", "Entry had extra quotes/spaces; app will normalize it.", scope, entry.path))

        if scope == Scope.SYSTEM and os.access(expanded, os.W_OK):
            issues.append(
                Issue(
                    "warning",
                    "security",
                    "System PATH contains a user-writable folder; this can be risky.",
                    scope,
                    entry.path,
                )
            )

        if idx < 3 and os.path.isdir(expanded) and os.access(expanded, os.W_OK):
            issues.append(
                Issue(
                    "warning",
                    "security-early",
                    "Writable directory appears very early in PATH; this increases executable hijack risk.",
                    scope,
                    entry.path,
                )
            )

        lowered = cleaned.lower()
        if "program files (x86)" in lowered:
            issues.append(
                Issue(
                    "info",
                    "arch",
                    "x86 directory detected; verify 32-bit/64-bit tool precedence is intentional.",
                    scope,
                    entry.path,
                )
            )

    path_length = len(";".join([e.path for e in enabled_entries]))
    if path_length > 8191:
        issues.append(Issue("warning", "length", f"PATH length is {path_length}, which can break older tools/shells."))
    elif path_length > 2048:
        issues.append(Issue("info", "length", f"PATH length is {path_length}; monitor for tool compatibility issues."))

    return issues


def detect_shadowing(entries: Iterable[PathEntry], command_names: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    enabled = [
        os.path.expandvars(entry.path)
        for entry in entries
        if entry.enabled and os.path.isdir(os.path.expandvars(entry.path))
    ]

    for command in command_names:
        matches = _find_command_matches(command, enabled)
        if len(matches) > 1:
            issues.append(
                Issue(
                    "info",
                    "shadow",
                    f"'{command}' resolves to '{matches[0]}' but {len(matches)-1} additional matches exist later in PATH.",
                )
            )
    return issues


def resolve_command(command: str, entries: Iterable[PathEntry]) -> CommandResolution:
    enabled = [
        os.path.expandvars(entry.path)
        for entry in entries
        if entry.enabled and os.path.isdir(os.path.expandvars(entry.path))
    ]
    matches = _find_command_matches(command, enabled)
    winner = matches[0] if matches else None
    return CommandResolution(command=command, winner=winner, all_matches=matches)


def _find_command_matches(command: str, directories: list[str]) -> list[str]:
    matches: list[str] = []
    candidates = [command]
    if not os.path.splitext(command)[1]:
        ext_list = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        candidates = [f"{command}{ext.lower()}" for ext in ext_list] + [f"{command}{ext.upper()}" for ext in ext_list]

    seen: set[str] = set()
    for folder in directories:
        expanded_folder = os.path.expandvars(folder)
        for candidate in candidates:
            full = os.path.join(expanded_folder, candidate)
            if os.path.isfile(full):
                normalized = os.path.normcase(os.path.normpath(full))
                if normalized not in seen:
                    matches.append(full)
                    seen.add(normalized)
    return matches


def quick_find_current(command: str) -> str | None:
    return shutil.which(command)
