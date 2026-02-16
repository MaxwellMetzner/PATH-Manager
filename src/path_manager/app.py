from __future__ import annotations

import csv
import ctypes
import os
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .diagnostics import analyze_entries, detect_shadowing, quick_find_current, resolve_command
from .env_store import EnvStore
from .models import PathEntry, Scope


class HoverTip:
    def __init__(self, widget: tk.Misc, text: str = "", text_provider: Callable[[], str] | None = None) -> None:
        self.widget = widget
        self.text = text
        self.text_provider = text_provider
        self.tip_window: tk.Toplevel | None = None
        self.after_id: str | None = None

        self.widget.bind("<Enter>", self._schedule)
        self.widget.bind("<Leave>", self._hide)

    def _schedule(self, _: tk.Event) -> None:
        self._cancel()
        self.after_id = self.widget.after(350, self._show)

    def _cancel(self) -> None:
        if self.after_id is not None:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self) -> None:
        if self.tip_window is not None:
            return

        tip_text = self.text_provider() if self.text_provider else self.text
        if not tip_text:
            return

        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + 22
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            tw,
            text=tip_text,
            justify="left",
            relief="solid",
            borderwidth=1,
            background="#ffffff",
            foreground="#303030",
            padx=8,
            pady=6,
        )
        label.pack()

    def _hide(self, _: tk.Event) -> None:
        self._cancel()
        if self.tip_window is not None:
            self.tip_window.destroy()
            self.tip_window = None


@dataclass
class SafeFixPlan:
    entries: list[PathEntry]
    normalized_count: int
    removed_duplicates: list[str]
    removed_missing: list[str]
    reordered_count: int
    before_count: int
    after_count: int


class PathManagerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("PATH Manager")
        self.root.geometry("1120x720")
        self.root.minsize(980, 620)

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TButton", padding=8)
        style.configure("Treeview", rowheight=26)
        style.configure("Header.TButton", padding=(8, 5))

        self.data_dir = Path(os.getenv("APPDATA", str(Path.home()))) / "PathManager"
        self.store = EnvStore(self.data_dir)

        self.scope_var = tk.StringVar(value=Scope.USER.value)
        self.filter_var = tk.StringVar(value="")
        self.command_var = tk.StringVar(value="python")
        self.status_var = tk.StringVar(value="Ready")

        self.entries: list[PathEntry] = []
        self.filtered_indices: list[int] = []
        self.help_icon: ttk.Label | None = None
        self.save_button: ttk.Button | None = None
        self.did_startup_scan = False

        self._build_ui()
        self.load_scope()
        self.auto_health_scan()

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(12, 10))
        header.pack(fill="x")

        ttk.Label(header, text="PATH Manager", font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(header, text="Scope:").pack(side="left", padx=(18, 6))

        scope_combo = ttk.Combobox(
            header,
            state="readonly",
            width=10,
            textvariable=self.scope_var,
            values=[Scope.USER.value, Scope.SYSTEM.value],
        )
        scope_combo.pack(side="left")
        scope_combo.bind("<<ComboboxSelected>>", self._on_scope_changed)

        ttk.Button(header, text="Reload", style="Header.TButton", command=self.load_scope).pack(side="left", padx=(8, 0))
        self.save_button = ttk.Button(header, text="Save", style="Header.TButton", command=self.save_scope)
        self.save_button.pack(side="left", padx=(8, 0))
        ttk.Button(header, text="Undo", style="Header.TButton", command=self.undo).pack(side="left", padx=(8, 0))

        add_btn = ttk.Button(header, text="New", style="Header.TButton", command=self.add_entry)
        add_btn.pack(side="left", padx=(8, 0))
        edit_btn = ttk.Button(header, text="Edit", style="Header.TButton", command=self.edit_selected)
        edit_btn.pack(side="left", padx=(4, 0))
        delete_btn = ttk.Button(header, text="Delete", style="Header.TButton", command=self.delete_selected)
        delete_btn.pack(side="left", padx=(4, 0))
        toggle_btn = ttk.Button(header, text="Enable/Disable", style="Header.TButton", command=self.toggle_selected)
        toggle_btn.pack(side="left", padx=(4, 0))
        normalize_btn = ttk.Button(header, text="Normalize", style="Header.TButton", command=self.normalize_entries)
        cleanup_btn = ttk.Menubutton(header, text="Cleanup ▾", style="Header.TButton")
        cleanup_btn.pack(side="left", padx=(4, 0))
        cleanup_menu = tk.Menu(cleanup_btn, tearoff=0)
        cleanup_menu.add_command(label="Normalize", command=self.normalize_entries)
        cleanup_menu.add_command(label="Remove Duplicates", command=self.remove_duplicates)
        cleanup_menu.add_command(label="Remove Missing", command=self.remove_missing)
        cleanup_menu.add_separator()
        cleanup_menu.add_command(label="Safe Fix Pack", command=self.apply_safe_fix_pack)
        cleanup_btn.configure(menu=cleanup_menu)
        folder_btn = ttk.Button(header, text="Open Folder", style="Header.TButton", command=self.open_selected_path)
        folder_btn.pack(side="left", padx=(4, 0))
        terminal_btn = ttk.Button(header, text="Open Terminal", style="Header.TButton", command=self.open_terminal_here)
        terminal_btn.pack(side="left", padx=(4, 0))
        diff_btn = ttk.Button(header, text="Preview Save Diff", style="Header.TButton", command=self.preview_save_diff)
        diff_btn.pack(side="left", padx=(4, 0))
        snapshot_btn = ttk.Button(header, text="Restore Snapshot", style="Header.TButton", command=self.restore_snapshot)
        snapshot_btn.pack(side="left", padx=(4, 0))

        HoverTip(add_btn, text="Add PATH entry")
        HoverTip(edit_btn, text="Edit selected entry")
        HoverTip(delete_btn, text="Delete selected entry")
        HoverTip(toggle_btn, text="Enable/disable selected entry")
        HoverTip(cleanup_btn, text="Cleanup actions: Normalize, Remove Duplicates, Remove Missing, Safe Fix Pack")
        HoverTip(folder_btn, text="Open selected folder")
        HoverTip(terminal_btn, text="Open terminal in selected folder")
        HoverTip(diff_btn, text="Preview save diff")
        HoverTip(snapshot_btn, text="Restore snapshot")

        ttk.Button(header, text="Export Profile", command=self.export_profile).pack(side="right")
        ttk.Button(header, text="Import Profile", command=self.import_profile).pack(side="right", padx=(0, 8))

        self.help_icon = ttk.Label(header, text="?", font=("Segoe UI", 11, "bold"), cursor="question_arrow")
        self.help_icon.pack(side="right", padx=(0, 10))
        HoverTip(self.help_icon, text_provider=self._get_help_tooltip_text)

        toolbar = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="Filter:").pack(side="left")
        entry_filter = ttk.Entry(toolbar, textvariable=self.filter_var, width=40)
        entry_filter.pack(side="left", padx=(6, 8))
        entry_filter.bind("<KeyRelease>", lambda _: self.refresh_tree())

        ttk.Button(toolbar, text="↑", width=3, command=lambda: self.move_selected(-1)).pack(side="right")
        ttk.Button(toolbar, text="↓", width=3, command=lambda: self.move_selected(1)).pack(side="right", padx=(0, 8))

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        left = ttk.Frame(content)
        content.add(left, weight=3)

        columns = ("enabled", "path", "exists", "status")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("enabled", text="On")
        self.tree.heading("path", text="Path")
        self.tree.heading("exists", text="Exists")
        self.tree.heading("status", text="Status")
        self.tree.column("enabled", width=40, anchor="center")
        self.tree.column("path", width=600)
        self.tree.column("exists", width=60, anchor="center")
        self.tree.column("status", width=120)
        self.tree.pack(fill="both", expand=True, side="left")
        self.tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._update_help_icon())

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        right = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(right, weight=2)

        tester = ttk.LabelFrame(right, text="Command Tester", padding=8)
        tester.pack(fill="x")
        tester_entry = ttk.Entry(tester, textvariable=self.command_var)
        tester_entry.pack(side="left", fill="x", expand=True)
        tester_entry.bind("<Return>", lambda _: self.run_command_test())
        ttk.Button(tester, text="Resolve", command=self.run_command_test).pack(side="left", padx=(8, 0))

        self.tester_output = tk.Text(right, height=9, wrap="word")
        self.tester_output.pack(fill="x", pady=(8, 12))

        diag = ttk.LabelFrame(right, text="Diagnostics", padding=8)
        diag.pack(fill="both", expand=True)
        self.diag_output = tk.Text(diag, wrap="word")
        self.diag_output.pack(fill="both", expand=True)

        footer = ttk.Frame(self.root, padding=(12, 4, 12, 8))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        ttk.Label(
            footer,
            text="Tip: restart terminals after saving PATH changes.",
            foreground="#6a6a6a",
        ).pack(side="right")

    @property
    def current_scope(self) -> Scope:
        return Scope(self.scope_var.get())

    def _on_scope_changed(self, _: tk.Event) -> None:
        if self.current_scope == Scope.SYSTEM and not self._is_admin():
            wants_relaunch = messagebox.askyesno(
                "Administrator required",
                "System PATH editing requires Administrator rights.\n\n"
                "Relaunch app as Administrator now?",
            )
            if wants_relaunch:
                if self._relaunch_as_admin():
                    self.root.destroy()
                    return
                messagebox.showwarning(
                    "Relaunch failed",
                    "Could not relaunch as Administrator. You can manually run the app elevated.",
                )
        self.load_scope()

    def _relaunch_as_admin(self) -> bool:
        script_path = Path(sys.argv[0]).resolve()
        params = f'"{script_path}"'
        try:
            result = ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                sys.executable,
                params,
                str(script_path.parent),
                1,
            )
            return result > 32
        except Exception:
            return False

    def load_scope(self) -> None:
        self.entries = self.store.read_entries(self.current_scope)
        self.refresh_tree()
        self.run_diagnostics()
        self._update_help_icon()
        self._update_save_button_state()
        self.status_var.set(f"Loaded {len(self.entries)} entries for {self.current_scope.value} PATH")

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        query = self.filter_var.get().strip().lower()
        self.filtered_indices = []

        for idx, entry in enumerate(self.entries):
            if query and query not in entry.path.lower():
                continue
            self.filtered_indices.append(idx)
            self.tree.insert(
                "",
                "end",
                values=(
                    "✓" if entry.enabled else "✕",
                    entry.path,
                    "Yes" if entry.exists else "No",
                    "Accessible" if entry.accessible else "Check",
                ),
            )

    def selected_model_index(self) -> int | None:
        selected = self.tree.selection()
        if not selected:
            return None
        view_index = self.tree.index(selected[0])
        if view_index >= len(self.filtered_indices):
            return None
        return self.filtered_indices[view_index]

    def _update_help_icon(self) -> None:
        if self.help_icon is None:
            return
        if self.current_scope == Scope.SYSTEM:
            self.help_icon.configure(foreground="#8a2f2f")
        else:
            self.help_icon.configure(foreground="#1f4f96")

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _update_save_button_state(self) -> None:
        if self.save_button is None:
            return
        if self.current_scope == Scope.SYSTEM and not self._is_admin():
            self.save_button.state(["disabled"])
        else:
            self.save_button.state(["!disabled"])

    def _get_help_tooltip_text(self) -> str:
        if self.current_scope == Scope.SYSTEM:
            if self._is_admin():
                scope_tip = "System scope selected (admin privileges detected)."
            else:
                scope_tip = "System scope selected (run app as Administrator to save)."
        else:
            scope_tip = "User scope selected (no elevation usually needed)."

        idx = self.selected_model_index()
        selection_tip = "No entry selected."
        if idx is not None and idx < len(self.entries):
            entry = self.entries[idx]
            if not entry.exists:
                selection_tip = "Selected entry points to a missing folder."
            elif not entry.enabled:
                selection_tip = "Selected entry is disabled and will not be used."
            else:
                selection_tip = "Selected entry is enabled and participates in command lookup order."

        return (
            "PATH Manager tips:\n"
            f"- {scope_tip}\n"
            "- Save writes changes to Windows registry\n"
            "- Existing terminals may need restart\n"
            "- Use Preview Save Diff before saving\n"
            f"- {selection_tip}"
        )

    def add_entry(self) -> None:
        value = self._prompt("Add PATH entry", "Path:")
        if not value:
            return
        clean = value.strip().strip('"')
        expanded = os.path.expandvars(clean)
        self.entries.append(
            PathEntry(
                path=clean,
                scope=self.current_scope,
                enabled=True,
                exists=os.path.isdir(expanded),
                accessible=os.access(expanded, os.R_OK),
            )
        )
        self.refresh_tree()
        self.run_diagnostics()

    def edit_selected(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        current = self.entries[idx]
        updated = self._prompt("Edit PATH entry", "Path:", initial=current.path)
        if updated is None:
            return
        clean = updated.strip().strip('"')
        expanded = os.path.expandvars(clean)
        current.path = clean
        current.exists = os.path.isdir(expanded)
        current.accessible = os.access(expanded, os.R_OK)
        self.refresh_tree()
        self.run_diagnostics()

    def delete_selected(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        del self.entries[idx]
        self.refresh_tree()
        self.run_diagnostics()

    def toggle_selected(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        self.entries[idx].enabled = not self.entries[idx].enabled
        self.refresh_tree()
        self.run_diagnostics()

    def move_selected(self, delta: int) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        target = idx + delta
        if target < 0 or target >= len(self.entries):
            return
        self.entries[idx], self.entries[target] = self.entries[target], self.entries[idx]
        self.refresh_tree()
        self.run_diagnostics()

    def normalize_entries(self) -> None:
        for entry in self.entries:
            cleaned = entry.path.strip().strip('"')
            expanded = os.path.expandvars(cleaned)
            entry.path = cleaned
            entry.exists = os.path.isdir(expanded)
            entry.accessible = os.access(expanded, os.R_OK) if entry.exists else False
        self.refresh_tree()
        self.run_diagnostics()
        self.status_var.set("Normalized path formatting")

    def remove_duplicates(self) -> None:
        seen: set[str] = set()
        unique: list[PathEntry] = []
        removed = 0
        for entry in self.entries:
            normalized = os.path.normcase(os.path.normpath(os.path.expandvars(entry.path.strip().strip('"'))))
            if normalized in seen:
                removed += 1
                continue
            seen.add(normalized)
            unique.append(entry)
        self.entries = unique
        self.refresh_tree()
        self.run_diagnostics()
        self.status_var.set(f"Removed {removed} duplicate entries")

    def remove_missing(self) -> None:
        before = len(self.entries)
        self.entries = [
            entry
            for entry in self.entries
            if not entry.enabled or os.path.isdir(os.path.expandvars(entry.path.strip().strip('"')))
        ]
        removed = before - len(self.entries)
        self.refresh_tree()
        self.run_diagnostics()
        self.status_var.set(f"Removed {removed} missing enabled entries")

    def _build_safe_fix_plan(self) -> SafeFixPlan:
        normalized_count = 0
        removed_duplicate_paths: list[str] = []
        removed_missing_paths: list[str] = []

        normalized_entries: list[PathEntry] = []
        seen: set[str] = set()

        for entry in self.entries:
            cleaned = entry.path.strip().strip('"')
            expanded = os.path.expandvars(cleaned)

            if cleaned != entry.path:
                normalized_count += 1

            updated = PathEntry(
                path=cleaned,
                scope=entry.scope,
                enabled=entry.enabled,
                source_index=entry.source_index,
                exists=os.path.isdir(expanded),
                accessible=os.access(expanded, os.R_OK) if os.path.isdir(expanded) else False,
                warning=entry.warning,
            )

            normalized = os.path.normcase(os.path.normpath(expanded))
            if normalized in seen:
                removed_duplicate_paths.append(cleaned)
                continue
            seen.add(normalized)

            if updated.enabled and not updated.exists:
                removed_missing_paths.append(cleaned)
                continue
            normalized_entries.append(updated)

        result_entries = self._prioritize_common_toolchains(normalized_entries)

        before_enabled: list[str] = []
        after_enabled: list[str] = []
        for entry in self.entries:
            if entry.enabled and entry.path.strip():
                before_enabled.append(os.path.normcase(os.path.normpath(os.path.expandvars(entry.path.strip().strip('"')))))
        for entry in result_entries:
            if entry.enabled and entry.path.strip():
                after_enabled.append(os.path.normcase(os.path.normpath(os.path.expandvars(entry.path.strip().strip('"')))))

        index_before: dict[str, int] = {}
        for idx, normalized in enumerate(before_enabled):
            if normalized not in index_before:
                index_before[normalized] = idx

        reordered_count = 0
        for idx, normalized in enumerate(after_enabled):
            if normalized in index_before and index_before[normalized] != idx:
                reordered_count += 1

        return SafeFixPlan(
            entries=result_entries,
            normalized_count=normalized_count,
            removed_duplicates=removed_duplicate_paths,
            removed_missing=removed_missing_paths,
            reordered_count=reordered_count,
            before_count=len(self.entries),
            after_count=len(result_entries),
        )

    def _format_safe_fix_preview(self, plan: SafeFixPlan, startup: bool = False) -> str:
        removed_duplicates = plan.removed_duplicates
        removed_missing = plan.removed_missing
        normalized_count = plan.normalized_count
        reordered_count = plan.reordered_count
        before_count = plan.before_count
        after_count = plan.after_count

        header = "Startup Safe Fix Preview" if startup else "Safe Fix Pack Preview"
        lines = [
            f"{header} ({self.current_scope.value} scope)",
            "",
            f"Entries: {before_count} -> {after_count}",
            f"Normalize formatting: {normalized_count}",
            f"Remove duplicates: {len(removed_duplicates)}",
            f"Remove missing enabled entries: {len(removed_missing)}",
            f"Reorder candidates: {reordered_count}",
            "",
        ]

        if removed_duplicates:
            lines.append("Duplicate entries to remove:")
            lines.extend(f"- {item}" for item in removed_duplicates[:12])
            if len(removed_duplicates) > 12:
                lines.append("- ...")
            lines.append("")

        if removed_missing:
            lines.append("Missing enabled entries to remove:")
            lines.extend(f"- {item}" for item in removed_missing[:12])
            if len(removed_missing) > 12:
                lines.append("- ...")
            lines.append("")

        lines.append("Apply these changes?")
        return "\n".join(lines)

    def _confirm_text_dialog(self, title: str, body: str, confirm_label: str = "Apply") -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("760x520")

        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        text.insert("1.0", body)
        text.configure(state="disabled")

        result = {"ok": False}

        footer = ttk.Frame(dialog)
        footer.pack(fill="x", padx=12, pady=(0, 12))

        def accept() -> None:
            result["ok"] = True
            dialog.destroy()

        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(footer, text=confirm_label, command=accept).pack(side="right", padx=(0, 8))

        self.root.wait_window(dialog)
        return bool(result["ok"])

    def apply_safe_fix_pack(self, plan: SafeFixPlan | None = None, skip_confirm: bool = False) -> None:
        if plan is None:
            plan = self._build_safe_fix_plan()

        preview = self._format_safe_fix_preview(plan, startup=False)
        if not skip_confirm and not self._confirm_text_dialog("Safe Fix Pack", preview, confirm_label="Apply"):
            return

        self.entries = plan.entries
        self.refresh_tree()
        self.run_diagnostics()

        messagebox.showinfo(
            "Safe Fix Pack",
            f"Applied safe fixes.\nRemoved duplicates: {len(plan.removed_duplicates)}\n"
            f"Removed missing enabled entries: {len(plan.removed_missing)}\n"
            "Reordered common toolchains to safer defaults.",
        )
        self.status_var.set("Applied Safe Fix Pack")

    def _prioritize_common_toolchains(self, entries: list[PathEntry]) -> list[PathEntry]:
        preferred_patterns = [
            "\\windows\\system32",
            "\\windows",
            "\\git\\cmd",
            "\\python",
            "\\nodejs",
            "\\dotnet",
        ]

        def rank(path_value: str) -> int:
            lowered = os.path.expandvars(path_value).lower()
            for index, pattern in enumerate(preferred_patterns):
                if pattern in lowered:
                    return index
            return len(preferred_patterns)

        enabled: list[tuple[int, PathEntry]] = []
        disabled: list[PathEntry] = []
        for idx, entry in enumerate(entries):
            if entry.enabled:
                enabled.append((idx, entry))
            else:
                disabled.append(entry)

        enabled_sorted = [
            pair[1]
            for pair in sorted(
                enabled,
                key=lambda pair: (rank(pair[1].path), pair[0]),
            )
        ]
        return enabled_sorted + disabled

    def save_scope(self) -> None:
        if self.current_scope == Scope.SYSTEM and not self._is_admin():
            messagebox.showwarning(
                "Administrator required",
                "System PATH changes are disabled until you run this app as Administrator.",
            )
            self._update_save_button_state()
            return

        try:
            self.store.write_entries(self.current_scope, self.entries, reason=f"Save {self.current_scope.value} PATH")
        except PermissionError:
            messagebox.showerror(
                "Permission required",
                "System PATH update requires running this app as Administrator.",
            )
            return
        except OSError as exc:
            messagebox.showerror("Failed to save", str(exc))
            return

        self.status_var.set("Saved successfully. New terminals will pick up changes.")
        self.load_scope()
        self.show_session_restart_guidance()

    def undo(self) -> None:
        snapshots = self.store.latest_snapshots()
        if not snapshots:
            messagebox.showinfo("Undo", "No snapshot available.")
            return
        latest = snapshots[0]
        if not messagebox.askyesno("Undo", f"Restore latest snapshot?\n{latest.name}"):
            return

        try:
            self.store.restore_snapshot(latest)
        except PermissionError:
            messagebox.showerror("Permission required", "Run as Administrator to restore system PATH.")
            return

        self.status_var.set(f"Restored snapshot {latest.name}")
        self.load_scope()

    def export_profile(self) -> None:
        target = filedialog.asksaveasfilename(
            title="Export profile",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not target:
            return
        payload = self.store.export_profile_payload()
        self.store.state_store.export_profile(payload, Path(target))
        self.status_var.set("Exported profile")

    def import_profile(self) -> None:
        source = filedialog.askopenfilename(title="Import profile", filetypes=[("JSON", "*.json")])
        if not source:
            return
        payload = self.store.state_store.import_profile(Path(source))
        user_entries, system_entries = self.store.import_profile_payload(payload)

        if self.current_scope == Scope.USER:
            self.entries = user_entries
        else:
            self.entries = system_entries
        self.refresh_tree()
        self.run_diagnostics()
        self.status_var.set("Imported profile into editor; click Save to apply")

    def restore_snapshot(self) -> None:
        snapshots = self.store.latest_snapshots()
        if not snapshots:
            messagebox.showinfo("Restore", "No snapshots found.")
            return

        choice = self._pick_snapshot([p.name for p in snapshots])
        if choice is None:
            return

        path = snapshots[choice]
        if not messagebox.askyesno("Restore snapshot", f"Restore {path.name}?"):
            return

        try:
            self.store.restore_snapshot(path)
        except PermissionError:
            messagebox.showerror("Permission required", "Run as Administrator to restore system PATH.")
            return

        self.status_var.set(f"Restored {path.name}")
        self.load_scope()

    def run_command_test(self) -> None:
        command = self.command_var.get().strip()
        if not command:
            return

        current_scope_resolution = resolve_command(command, self.entries)
        other_scope = Scope.SYSTEM if self.current_scope == Scope.USER else Scope.USER
        other_entries = self.store.read_entries(other_scope)
        other_scope_resolution = resolve_command(command, other_entries)
        session_winner = quick_find_current(command)

        self.tester_output.delete("1.0", tk.END)
        self.tester_output.insert(
            tk.END,
            f"{self.current_scope.value} scope winner: {current_scope_resolution.winner or 'No match'}\n",
        )
        self.tester_output.insert(
            tk.END,
            f"{other_scope.value} scope winner: {other_scope_resolution.winner or 'No match'}\n",
        )
        self.tester_output.insert(tk.END, f"Current session winner: {session_winner or 'No match'}\n\n")

        if session_winner and current_scope_resolution.winner:
            if os.path.normcase(session_winner) != os.path.normcase(current_scope_resolution.winner):
                self.tester_output.insert(
                    tk.END,
                    "Note: Session winner differs from this editor view. Save changes and restart terminals/apps to apply.\n\n",
                )

        self.tester_output.insert(tk.END, f"Matches in {self.current_scope.value} scope:\n")
        if current_scope_resolution.all_matches:
            for match in current_scope_resolution.all_matches:
                self.tester_output.insert(tk.END, f"- {match}\n")
        else:
            self.tester_output.insert(tk.END, "- none\n")

    def run_diagnostics(self) -> None:
        issues = analyze_entries(self.entries, self.current_scope)
        issues.extend(detect_shadowing(self.entries, ["python", "node", "git", "java", "dotnet"]))

        self.diag_output.delete("1.0", tk.END)
        if not issues:
            self.diag_output.insert(tk.END, "No obvious issues found.\n")
            return

        for issue in issues:
            scope = f"[{issue.scope.value}] " if issue.scope else ""
            path = f" | {issue.path}" if issue.path else ""
            self.diag_output.insert(
                tk.END,
                f"{issue.severity.upper():<7} {scope}{issue.code}: {issue.message}{path}\n",
            )

    def auto_health_scan(self) -> None:
        if self.did_startup_scan:
            return
        self.did_startup_scan = True

        plan = self._build_safe_fix_plan()
        actionable_changes = (
            plan.normalized_count
            + len(plan.removed_duplicates)
            + len(plan.removed_missing)
            + plan.reordered_count
        )
        if actionable_changes <= 0:
            return

        preview = self._format_safe_fix_preview(plan, startup=True)
        if self._confirm_text_dialog("Startup Health Scan", preview, confirm_label="Apply Fixes"):
            self.apply_safe_fix_pack(plan=plan, skip_confirm=True)

    def open_selected_path(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        target = self.entries[idx].path
        if not os.path.isdir(target):
            messagebox.showwarning("Open folder", "Directory does not exist.")
            return
        subprocess.Popen(["explorer", target])

    def open_terminal_here(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        target = self.entries[idx].path
        if not os.path.isdir(target):
            messagebox.showwarning("Open terminal", "Directory does not exist.")
            return
        subprocess.Popen(["cmd", "/k", "cd", "/d", target])

    def preview_save_diff(self) -> None:
        before_entries = self.store.read_entries(self.current_scope)
        before = [entry.path for entry in before_entries if entry.enabled]
        after = [entry.path.strip().strip('"') for entry in self.entries if entry.enabled and entry.path.strip()]

        before_norm = [os.path.normcase(os.path.normpath(path)) for path in before]
        after_norm = [os.path.normcase(os.path.normpath(path)) for path in after]

        added = [after[idx] for idx, norm in enumerate(after_norm) if norm not in set(before_norm)]
        removed = [before[idx] for idx, norm in enumerate(before_norm) if norm not in set(after_norm)]

        moved = 0
        index_in_before = {norm: i for i, norm in enumerate(before_norm)}
        for i, norm in enumerate(after_norm):
            if norm in index_in_before and index_in_before[norm] != i:
                moved += 1

        summary = (
            f"Scope: {self.current_scope.value}\n"
            f"Active entries now: {len(after)}\n"
            f"Added: {len(added)}\n"
            f"Removed: {len(removed)}\n"
            f"Reordered: {moved}\n\n"
        )

        details: list[str] = [summary]
        if added:
            details.append("Added:\n" + "\n".join(f"+ {item}" for item in added[:20]) + "\n")
        if removed:
            details.append("Removed:\n" + "\n".join(f"- {item}" for item in removed[:20]) + "\n")
        if len(added) > 20 or len(removed) > 20:
            details.append("Only first 20 added/removed entries are shown.\n")

        messagebox.showinfo("Save Diff Preview", "\n".join(details))

    def _collect_possible_stale_processes(self) -> list[str]:
        targets = {
            "Code.exe": "VS Code",
            "WindowsTerminal.exe": "Windows Terminal",
            "cmd.exe": "Command Prompt",
            "powershell.exe": "PowerShell",
            "pwsh.exe": "PowerShell 7",
            "devenv.exe": "Visual Studio",
        }

        try:
            raw = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"], text=True, encoding="utf-8", errors="replace")
        except Exception:
            return []

        running_labels: list[str] = []
        seen: set[str] = set()
        for row in csv.reader(raw.splitlines()):
            if not row:
                continue
            image = row[0]
            if image in targets and image not in seen:
                running_labels.append(targets[image])
                seen.add(image)
        return running_labels

    def show_session_restart_guidance(self) -> None:
        running = self._collect_possible_stale_processes()
        if running:
            restart_list = "\n".join(f"- {name}" for name in running)
        else:
            restart_list = "- Open terminals and IDEs"

        messagebox.showinfo(
            "PATH Updated",
            "PATH was updated successfully.\n\n"
            "Already-open applications may still use the old PATH.\n"
            "Consider restarting:\n"
            f"{restart_list}\n\n"
            "Tip: New terminals opened after this save should pick up the new PATH.",
        )

    def _prompt(self, title: str, label: str, initial: str = "") -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(dialog, text=label).pack(anchor="w", padx=12, pady=(12, 4))
        value_var = tk.StringVar(value=initial)
        entry = ttk.Entry(dialog, textvariable=value_var, width=80)
        entry.pack(fill="x", padx=12, pady=(0, 12))
        entry.focus_set()

        result: dict[str, str | None] = {"value": None}

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=(0, 12))

        def accept() -> None:
            result["value"] = value_var.get()
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", command=accept).pack(side="right", padx=(0, 8))

        self.root.wait_window(dialog)
        return result["value"]

    def _pick_snapshot(self, names: list[str]) -> int | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Select snapshot")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("560x420")

        listbox = tk.Listbox(dialog)
        listbox.pack(fill="both", expand=True, padx=12, pady=12)
        for name in names:
            listbox.insert(tk.END, name)

        result: dict[str, int | None] = {"index": None}

        def accept() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            result["index"] = int(sel[0])
            dialog.destroy()

        footer = ttk.Frame(dialog)
        footer.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(footer, text="Restore", command=accept).pack(side="right", padx=(0, 8))

        self.root.wait_window(dialog)
        return result["index"]

    def run(self) -> None:
        self.root.mainloop()
