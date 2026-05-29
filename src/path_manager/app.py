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

from .diagnostics import Issue, analyze_entries, detect_shadowing, quick_find_current, resolve_command
from .env_store import EnvStore
from .models import PathEntry, Scope


COLORS = {
    "bg": "#f5f7fb",
    "surface": "#ffffff",
    "surface_alt": "#eef2f7",
    "border": "#d8dee8",
    "text": "#1d2733",
    "muted": "#5e6b7a",
    "accent": "#2563eb",
    "accent_dark": "#1d4ed8",
    "success": "#027a48",
    "warning": "#b54708",
    "danger": "#b42318",
    "danger_bg": "#fff4f2",
    "warning_bg": "#fff8e6",
}

COMMON_COMMANDS = ["python", "node", "git", "java", "dotnet"]


class HoverTip:
    def __init__(self, widget: tk.Misc, text: str = "", text_provider: Callable[[], str] | None = None) -> None:
        self.widget = widget
        self.text = text
        self.text_provider = text_provider
        self.tip_window: tk.Toplevel | None = None
        self.after_id: str | None = None

        self.widget.bind("<Enter>", self._schedule, add="+")
        self.widget.bind("<Leave>", self._hide, add="+")

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
            background=COLORS["surface"],
            foreground=COLORS["text"],
            padx=9,
            pady=7,
            wraplength=360,
        )
        label.pack()

    def _hide(self, _: tk.Event | None = None) -> None:
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
        self.root.geometry("1260x780")
        self.root.minsize(1060, 680)
        self.root.configure(background=COLORS["bg"])

        self.data_dir = Path(os.getenv("APPDATA", str(Path.home()))) / "PathManager"
        self.store = EnvStore(self.data_dir)
        initial_scope = self._initial_scope_from_args()

        self.scope_var = tk.StringVar(value=initial_scope.value)
        self.scope_note_var = tk.StringVar(value="")
        self.command_var = tk.StringVar(value="python")
        self.status_var = tk.StringVar(value="Ready")
        self.unsaved_var = tk.StringVar(value="")
        self.health_summary_var = tk.StringVar(value="")
        self.metric_vars = {
            "enabled": tk.StringVar(value="0"),
            "disabled": tk.StringVar(value="0"),
            "missing": tk.StringVar(value="0"),
            "issues": tk.StringVar(value="0"),
            "length": tk.StringVar(value="0"),
        }

        self.entries: list[PathEntry] = []
        self.current_issues: list[Issue] = []
        self.entry_issues: dict[int, list[Issue]] = {}
        self.loaded_signature: tuple[tuple[str, bool], ...] = tuple()
        self.last_scope = initial_scope
        self.did_startup_scan = False

        self._configure_styles()
        self._build_menus()
        self._build_ui()
        self._bind_shortcuts()

        self.load_scope(force=True)
        self.root.after(300, self.auto_health_scan)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    @property
    def current_scope(self) -> Scope:
        return Scope(self.scope_var.get())

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Segoe UI", 10), background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("App.TFrame", background=COLORS["bg"])
        style.configure("Surface.TFrame", background=COLORS["surface"])
        style.configure("Toolbar.TFrame", background=COLORS["surface"])
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
        style.configure("SurfaceMuted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
        style.configure("Status.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
        style.configure("TButton", padding=(10, 6))
        style.configure("Primary.TButton", padding=(12, 7), foreground="#ffffff", background=COLORS["accent"])
        style.map("Primary.TButton", background=[("active", COLORS["accent_dark"]), ("disabled", COLORS["border"])])
        style.configure("Tool.TButton", padding=(8, 5))
        style.configure("TMenubutton", padding=(10, 6))
        style.configure("Treeview", rowheight=28, background=COLORS["surface"], fieldbackground=COLORS["surface"])
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=(6, 6))
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 7))
        style.configure("TLabelframe", background=COLORS["surface"])
        style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 9, "bold"))

    def _build_menus(self) -> None:
        menubar = tk.Menu(self.root)

        self.file_menu = tk.Menu(menubar, tearoff=0)
        self.file_menu.add_command(label="Save Changes", accelerator="Ctrl+S", command=self.save_scope)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Restore Snapshot", command=self.restore_snapshot)
        self.file_menu.add_command(label="Undo Latest Snapshot", command=self.undo)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=self.file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Add Entry", accelerator="Ctrl+N", command=self.add_entry)
        edit_menu.add_command(label="Edit Selected", accelerator="Enter", command=self.edit_selected)
        edit_menu.add_command(label="Remove Selected", accelerator="Del", command=self.delete_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="Enable or Disable Selected", accelerator="Space", command=self.toggle_selected)
        edit_menu.add_command(label="Move Up", accelerator="Alt+Up", command=lambda: self.move_selected(-1))
        edit_menu.add_command(label="Move Down", accelerator="Alt+Down", command=lambda: self.move_selected(1))
        menubar.add_cascade(label="Edit", menu=edit_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Normalize Formatting", command=self.normalize_entries)
        tools_menu.add_command(label="Remove Duplicates", command=self.remove_duplicates)
        tools_menu.add_command(label="Remove Missing Enabled Entries", command=self.remove_missing)
        tools_menu.add_separator()
        tools_menu.add_command(label="Apply Recommended Fixes", command=self.apply_safe_fix_pack)
        tools_menu.add_command(label="Move Windows Core First", command=self.move_windows_core_first)
        tools_menu.add_separator()
        tools_menu.add_command(label="Preview Effective PATH", command=self.preview_effective_path)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self.root.configure(menu=menubar)

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="App.TFrame", padding=(16, 14, 16, 8))
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)

        title_area = ttk.Frame(header, style="App.TFrame")
        title_area.grid(row=0, column=0, sticky="w")
        ttk.Label(title_area, text="PATH Manager", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_area, text="Windows PATH editor", style="Subtitle.TLabel").pack(anchor="w", pady=(1, 0))

        header_actions = ttk.Frame(header, style="App.TFrame")
        header_actions.grid(row=0, column=1, sticky="e")

        ttk.Label(header_actions, text="Scope", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        for scope in (Scope.USER, Scope.SYSTEM):
            ttk.Radiobutton(
                header_actions,
                text=scope.value,
                value=scope.value,
                variable=self.scope_var,
                command=self._on_scope_changed,
            ).pack(side="left", padx=(0, 8))

        self.scope_badge = tk.Label(
            header_actions,
            textvariable=self.scope_note_var,
            background=COLORS["surface_alt"],
            foreground=COLORS["text"],
            padx=10,
            pady=4,
        )
        self.scope_badge.pack(side="left", padx=(2, 12))

        self.save_button = ttk.Button(header_actions, text="Save", style="Primary.TButton", command=self.save_scope)
        self.save_button.pack(side="left")

        metrics = ttk.Frame(self.root, style="App.TFrame", padding=(16, 0, 16, 12))
        metrics.pack(fill="x")
        for key, label in (
            ("enabled", "Enabled"),
            ("disabled", "Disabled"),
            ("missing", "Missing"),
            ("issues", "Issues"),
            ("length", "PATH chars"),
        ):
            self._build_metric_card(metrics, key, label)

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        left = ttk.Frame(content, style="App.TFrame")
        right = ttk.Frame(content, style="App.TFrame")
        content.add(left, weight=5)
        content.add(right, weight=2)

        self._build_path_table(left)
        self._build_side_panel(right)

        footer = ttk.Frame(self.root, style="App.TFrame", padding=(16, 0, 16, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.unsaved_var, style="Status.TLabel").pack(side="right")

    def _build_metric_card(self, parent: tk.Misc, key: str, label: str) -> None:
        card = tk.Frame(
            parent,
            background=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            bd=0,
        )
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(
            card,
            textvariable=self.metric_vars[key],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI", 18, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(9, 0))
        tk.Label(
            card,
            text=label,
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))

    def _build_path_table(self, parent: tk.Misc) -> None:
        toolbar_shell = tk.Frame(
            parent,
            background=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            bd=0,
        )
        toolbar_shell.pack(fill="x", pady=(0, 8))

        action_row = ttk.Frame(toolbar_shell, style="Toolbar.TFrame", padding=(10, 10, 10, 10))
        action_row.pack(fill="x")

        self.add_entry_button = ttk.Button(action_row, text="New Entry", style="Tool.TButton", command=self.add_entry)
        self.add_entry_button.pack(side="left", padx=(0, 6))
        self.edit_button = ttk.Button(action_row, text="Edit", style="Tool.TButton", command=self.edit_selected)
        self.edit_button.pack(side="left", padx=(0, 6))
        self.toggle_button = ttk.Button(action_row, text="Disable", style="Tool.TButton", command=self.toggle_selected)
        self.toggle_button.pack(side="left", padx=(0, 6))
        self.delete_button = ttk.Button(action_row, text="Remove", style="Tool.TButton", command=self.delete_selected)
        self.delete_button.pack(side="left", padx=(0, 10))

        self.more_button = ttk.Menubutton(action_row, text="More")
        self.more_button.pack(side="right")
        more_menu = tk.Menu(self.more_button, tearoff=0)
        more_menu.add_command(label="Preview Effective PATH", command=self.preview_effective_path)
        more_menu.add_separator()
        more_menu.add_command(label="Restore Snapshot", command=self.restore_snapshot)
        more_menu.add_command(label="Undo Latest Snapshot", command=self.undo)
        self.more_button.configure(menu=more_menu)

        table_shell = tk.Frame(
            parent,
            background=COLORS["surface"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            bd=0,
        )
        table_shell.pack(fill="both", expand=True)
        table_shell.rowconfigure(0, weight=1)
        table_shell.columnconfigure(0, weight=1)

        columns = ("order", "state", "path", "health")
        self.tree = ttk.Treeview(table_shell, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("order", text="#")
        self.tree.heading("state", text="State")
        self.tree.heading("path", text="Path")
        self.tree.heading("health", text="Health")
        self.tree.column("order", width=54, minwidth=46, anchor="center", stretch=False)
        self.tree.column("state", width=78, minwidth=70, anchor="center", stretch=False)
        self.tree.column("path", width=660, minwidth=320, anchor="w")
        self.tree.column("health", width=130, minwidth=110, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(table_shell, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.tag_configure("ready", foreground=COLORS["text"])
        self.tree.tag_configure("disabled", foreground=COLORS["muted"])
        self.tree.tag_configure("missing", foreground=COLORS["danger"], background=COLORS["danger_bg"])
        self.tree.tag_configure("warning", foreground=COLORS["warning"], background=COLORS["warning_bg"])
        self.tree.tag_configure("info", foreground=COLORS["accent"])

        self.tree.bind("<<TreeviewSelect>>", lambda _: self._on_selection_changed())
        self.tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.tree.bind("<Button-3>", self._open_context_menu)
        self.tree.bind("<Delete>", lambda _: self.delete_selected())
        self.tree.bind("<Return>", lambda _: self.edit_selected())
        self.tree.bind("<space>", lambda _: self.toggle_selected())

        self._build_context_menu()

    def _build_side_panel(self, parent: tk.Misc) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)

        overview = ttk.Frame(self.notebook, style="Surface.TFrame", padding=10)
        selected = ttk.Frame(self.notebook, style="Surface.TFrame", padding=10)
        command = ttk.Frame(self.notebook, style="Surface.TFrame", padding=10)

        self.notebook.add(overview, text="Overview")
        self.notebook.add(selected, text="Selected")
        self.notebook.add(command, text="Command")

        ttk.Label(overview, textvariable=self.health_summary_var, style="Surface.TLabel", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", pady=(0, 8)
        )

        issue_shell = ttk.Frame(overview, style="Surface.TFrame")
        issue_shell.pack(fill="both", expand=True)
        issue_shell.rowconfigure(0, weight=1)
        issue_shell.columnconfigure(0, weight=1)

        issue_columns = ("severity", "issue", "path")
        self.issue_tree = ttk.Treeview(issue_shell, columns=issue_columns, show="headings", selectmode="browse", height=8)
        self.issue_tree.heading("severity", text="Severity")
        self.issue_tree.heading("issue", text="Issue")
        self.issue_tree.heading("path", text="Path")
        self.issue_tree.column("severity", width=76, stretch=False)
        self.issue_tree.column("issue", width=210, minwidth=160)
        self.issue_tree.column("path", width=260, minwidth=140)
        self.issue_tree.grid(row=0, column=0, sticky="nsew")
        issue_scroll = ttk.Scrollbar(issue_shell, orient="vertical", command=self.issue_tree.yview)
        issue_scroll.grid(row=0, column=1, sticky="ns")
        self.issue_tree.configure(yscrollcommand=issue_scroll.set)
        self.issue_tree.tag_configure("warning", foreground=COLORS["warning"])
        self.issue_tree.tag_configure("info", foreground=COLORS["accent"])

        quick_fixes = ttk.LabelFrame(overview, text="Fixes", padding=8)
        quick_fixes.pack(fill="x", pady=(10, 0))
        ttk.Button(quick_fixes, text="Recommended", style="Tool.TButton", command=self.apply_safe_fix_pack).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(quick_fixes, text="Duplicates", style="Tool.TButton", command=self.remove_duplicates).pack(side="left", padx=(0, 6))
        ttk.Button(quick_fixes, text="Missing", style="Tool.TButton", command=self.remove_missing).pack(side="left", padx=(0, 6))
        ttk.Button(quick_fixes, text="Normalize", style="Tool.TButton", command=self.normalize_entries).pack(side="left")

        selected_actions = ttk.Frame(selected, style="Surface.TFrame")
        selected_actions.pack(fill="x", pady=(0, 8))
        self.open_button = ttk.Button(selected_actions, text="Open", style="Tool.TButton", command=self.open_selected_path)
        self.open_button.pack(side="left", padx=(0, 6))
        self.terminal_button = ttk.Button(selected_actions, text="Terminal", style="Tool.TButton", command=self.open_terminal_here)
        self.terminal_button.pack(side="left", padx=(0, 6))
        self.copy_button = ttk.Button(selected_actions, text="Copy", style="Tool.TButton", command=self.copy_selected_paths)
        self.copy_button.pack(side="left", padx=(0, 6))

        self.detail_text = tk.Text(
            selected,
            wrap="word",
            height=12,
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            padx=10,
            pady=8,
        )
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.configure(state="disabled")

        command_row = ttk.Frame(command, style="Surface.TFrame")
        command_row.pack(fill="x")
        self.command_entry = ttk.Entry(command_row, textvariable=self.command_var)
        self.command_entry.pack(side="left", fill="x", expand=True)
        self.command_entry.bind("<Return>", lambda _: self.run_command_test())
        ttk.Button(command_row, text="Resolve", style="Tool.TButton", command=self.run_command_test).pack(side="left", padx=(8, 0))

        command_quick = ttk.Frame(command, style="Surface.TFrame")
        command_quick.pack(fill="x", pady=(8, 8))
        for name in COMMON_COMMANDS:
            ttk.Button(command_quick, text=name, style="Tool.TButton", command=lambda value=name: self._quick_command(value)).pack(
                side="left", padx=(0, 6)
            )

        self.tester_output = tk.Text(
            command,
            wrap="word",
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            padx=10,
            pady=8,
        )
        self.tester_output.pack(fill="both", expand=True)
        self.tester_output.configure(state="disabled")

    def _build_context_menu(self) -> None:
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Edit", command=self.edit_selected)
        self.context_menu.add_command(label="Enable or Disable", command=self.toggle_selected)
        self.context_menu.add_command(label="Remove", command=self.delete_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Move Up", command=lambda: self.move_selected(-1))
        self.context_menu.add_command(label="Move Down", command=lambda: self.move_selected(1))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Open Folder", command=self.open_selected_path)
        self.context_menu.add_command(label="Open Terminal", command=self.open_terminal_here)
        self.context_menu.add_command(label="Copy Path", command=self.copy_selected_paths)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-s>", lambda _: self._shortcut(self.save_scope))
        self.root.bind("<Control-n>", lambda _: self._shortcut(self.add_entry))
        self.root.bind("<Alt-Up>", lambda _: self._shortcut(lambda: self.move_selected(-1)))
        self.root.bind("<Alt-Down>", lambda _: self._shortcut(lambda: self.move_selected(1)))

    def _shortcut(self, callback: Callable[[], object]) -> str:
        callback()
        return "break"

    def _on_scope_changed(self) -> None:
        next_scope = self.current_scope
        if next_scope == self.last_scope:
            return

        if self.has_unsaved_changes() and not self._confirm_leave_dirty("switch scope"):
            self.scope_var.set(self.last_scope.value)
            return

        if next_scope == Scope.SYSTEM and not self._is_admin():
            if self._relaunch_as_admin(target_scope=next_scope):
                self.root.destroy()
                return
            self.scope_var.set(self.last_scope.value)
            self._update_save_button_state()
            messagebox.showwarning(
                "Administrator required",
                "Stayed in User PATH. Approve the administrator prompt to edit System PATH.",
            )
            return

        self.last_scope = next_scope
        self.load_scope(force=True)

    def load_scope(self, force: bool = False) -> bool:
        if not force and self.has_unsaved_changes() and not self._confirm_leave_dirty("reload"):
            return False

        try:
            self.entries = self.store.read_entries(self.current_scope)
        except PermissionError:
            messagebox.showerror("Permission required", "This PATH scope could not be read.")
            return False

        self.loaded_signature = self._entry_signature(self.entries)
        self._sync_view(f"Loaded {len(self.entries)} {self.current_scope.value} PATH entries")
        return True

    def reload_scope(self) -> None:
        self.load_scope(force=False)

    def _sync_view(self, status: str | None = None, select_indices: list[int] | None = None) -> None:
        self._refresh_entry_statuses()
        self.run_diagnostics()
        self.refresh_tree(select_indices=select_indices)
        self._update_metrics()
        self._update_details()
        self._update_action_state()
        self._update_save_button_state()
        if status is not None:
            self.status_var.set(status)

    def refresh_tree(self, select_indices: list[int] | None = None) -> None:
        if not hasattr(self, "tree"):
            return

        previous = set(select_indices if select_indices is not None else self.selected_model_indices())
        for item in self.tree.get_children():
            self.tree.delete(item)

        for idx, entry in enumerate(self.entries):
            health = self._entry_health(idx)

            iid = self._iid_for_index(idx)
            tags = (self._entry_tag(idx),)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(idx + 1, "On" if entry.enabled else "Off", entry.path, health),
                tags=tags,
            )
            if idx in previous:
                self.tree.selection_add(iid)

    def selected_model_indices(self) -> list[int]:
        if not hasattr(self, "tree"):
            return []
        indices: list[int] = []
        for iid in self.tree.selection():
            idx = self._index_from_iid(iid)
            if idx is not None and 0 <= idx < len(self.entries):
                indices.append(idx)
        return sorted(set(indices))

    def selected_model_index(self) -> int | None:
        indices = self.selected_model_indices()
        return indices[0] if indices else None

    def _iid_for_index(self, idx: int) -> str:
        return f"entry:{idx}"

    def _index_from_iid(self, iid: str) -> int | None:
        if not iid.startswith("entry:"):
            return None
        try:
            return int(iid.split(":", 1)[1])
        except ValueError:
            return None

    def _on_selection_changed(self) -> None:
        self._update_details()
        self._update_action_state()

    def _open_context_menu(self, event: tk.Event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    def _update_action_state(self) -> None:
        selected = self.selected_model_indices()
        has_selection = bool(selected)
        single_selection = len(selected) == 1

        self._set_widget_enabled(self.edit_button, single_selection)
        self._set_widget_enabled(self.delete_button, has_selection)
        self._set_widget_enabled(self.toggle_button, has_selection)
        self._set_widget_enabled(self.open_button, single_selection)
        self._set_widget_enabled(self.terminal_button, single_selection)
        self._set_widget_enabled(self.copy_button, has_selection)

        if has_selection and any(not self.entries[idx].enabled for idx in selected):
            self.toggle_button.configure(text="Enable")
        else:
            self.toggle_button.configure(text="Disable")

    def _set_widget_enabled(self, widget: ttk.Widget, enabled: bool) -> None:
        if enabled:
            widget.state(["!disabled"])
        else:
            widget.state(["disabled"])

    def _update_save_button_state(self) -> None:
        dirty = self.has_unsaved_changes()
        can_write = self._can_write_current_scope()
        save_enabled = dirty and can_write
        self.save_button.configure(text="Save Changes" if dirty else "No Changes")

        if save_enabled:
            self.save_button.state(["!disabled"])
        else:
            self.save_button.state(["disabled"])

        self.file_menu.entryconfigure(0, state="normal" if save_enabled else "disabled")
        self.unsaved_var.set("Unsaved changes" if dirty else "No unsaved changes")

        if self.current_scope == Scope.SYSTEM and not self._is_admin():
            self.root.title("PATH Manager - System Space")
            self.scope_note_var.set("System space - administrator required")
            self.scope_badge.configure(background=COLORS["warning_bg"], foreground=COLORS["warning"])
        elif self.current_scope == Scope.SYSTEM:
            self.root.title("PATH Manager - System Space")
            self.scope_note_var.set("System space - administrator session")
            self.scope_badge.configure(background=COLORS["surface_alt"], foreground=COLORS["success"])
        else:
            self.root.title("PATH Manager - User Space")
            self.scope_note_var.set("User space - current account")
            self.scope_badge.configure(background=COLORS["surface_alt"], foreground=COLORS["text"])

    def _update_metrics(self) -> None:
        enabled_entries = [entry for entry in self.entries if entry.enabled]
        disabled_count = len(self.entries) - len(enabled_entries)
        missing_count = sum(1 for entry in enabled_entries if not entry.exists)
        path_length = len(";".join(entry.path.strip().strip('"') for entry in enabled_entries if entry.path.strip()))
        warning_count = sum(1 for issue in self.current_issues if issue.severity == "warning")

        self.metric_vars["enabled"].set(str(len(enabled_entries)))
        self.metric_vars["disabled"].set(str(disabled_count))
        self.metric_vars["missing"].set(str(missing_count))
        self.metric_vars["issues"].set(str(warning_count))
        self.metric_vars["length"].set(str(path_length))

    def _update_details(self) -> None:
        selected = self.selected_model_indices()
        if not selected:
            self._set_text(self.detail_text, "No entry selected.")
            return

        if len(selected) > 1:
            paths = "\n".join(f"- {self.entries[idx].path}" for idx in selected[:12])
            if len(selected) > 12:
                paths += "\n- ..."
            self._set_text(self.detail_text, f"{len(selected)} entries selected\n\n{paths}")
            return

        idx = selected[0]
        entry = self.entries[idx]
        expanded = os.path.expandvars(entry.path)
        normalized = self._normalize_path(entry.path)
        writable = os.access(expanded, os.W_OK) if entry.exists else False
        issues = self.entry_issues.get(idx, [])

        lines = [
            f"Position: {idx + 1} of {len(self.entries)}",
            f"Scope: {entry.scope.value}",
            f"State: {'Enabled' if entry.enabled else 'Disabled'}",
            f"Health: {self._entry_health(idx)}",
            "",
            "Path:",
            entry.path,
            "",
            "Expanded:",
            expanded,
            "",
            f"Exists: {'Yes' if entry.exists else 'No'}",
            f"Readable: {'Yes' if entry.accessible else 'No'}",
            f"Writable: {'Yes' if writable else 'No'}",
            f"Normalized key: {normalized}",
        ]

        if issues:
            lines.extend(["", "Issues:"])
            lines.extend(f"- {issue.severity.upper()} {issue.code}: {issue.message}" for issue in issues)
        else:
            lines.extend(["", "Issues: none"])

        self._set_text(self.detail_text, "\n".join(lines))

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, value)
        widget.configure(state="disabled")

    def _refresh_entry_statuses(self) -> None:
        for entry in self.entries:
            clean = entry.path.strip().strip('"')
            expanded = os.path.expandvars(clean)
            entry.exists = os.path.isdir(expanded)
            entry.accessible = os.access(expanded, os.R_OK) if entry.exists else False

    def _entry_health(self, idx: int) -> str:
        entry = self.entries[idx]
        if not entry.enabled:
            return "Disabled"
        if not entry.path.strip():
            return "Empty"
        if not entry.exists:
            return "Missing"
        issues = self.entry_issues.get(idx, [])
        if any(issue.severity == "warning" for issue in issues):
            return "Review"
        if issues:
            return "Info"
        return "Ready"

    def _entry_tag(self, idx: int) -> str:
        health = self._entry_health(idx)
        if health == "Disabled":
            return "disabled"
        if health in {"Missing", "Empty"}:
            return "missing"
        if health == "Review":
            return "warning"
        if health == "Info":
            return "info"
        return "ready"

    def run_diagnostics(self) -> None:
        self.current_issues = analyze_entries(self.entries, self.current_scope)
        self.current_issues.extend(detect_shadowing(self.entries, COMMON_COMMANDS))
        self.entry_issues = self._map_issues_to_entries(self.current_issues)

        for item in self.issue_tree.get_children():
            self.issue_tree.delete(item)

        if not self.current_issues:
            self.health_summary_var.set("No obvious issues found")
            return

        warnings = sum(1 for issue in self.current_issues if issue.severity == "warning")
        infos = sum(1 for issue in self.current_issues if issue.severity != "warning")
        self.health_summary_var.set(f"{warnings} warnings, {infos} notes")

        for issue in self.current_issues:
            self.issue_tree.insert(
                "",
                "end",
                values=(issue.severity.upper(), f"{issue.code}: {issue.message}", issue.path or ""),
                tags=(issue.severity,),
            )

    def _map_issues_to_entries(self, issues: list[Issue]) -> dict[int, list[Issue]]:
        mapped: dict[int, list[Issue]] = {idx: [] for idx in range(len(self.entries))}
        normalized_by_index = {idx: self._normalize_path(entry.path) for idx, entry in enumerate(self.entries)}

        for issue in issues:
            if not issue.path:
                continue
            issue_norm = self._normalize_path(issue.path)
            for idx, entry_norm in normalized_by_index.items():
                if issue_norm == entry_norm:
                    mapped[idx].append(issue)
        return mapped

    def add_entry(self) -> None:
        result = self._entry_dialog("New PATH entry", "")
        if result is None:
            return
        path_value, enabled = result
        self._add_path_entry(path_value, enabled=enabled)

    def _add_path_entry(self, value: str, enabled: bool) -> None:
        clean = value.strip().strip('"')
        if not clean:
            return

        duplicate = self._find_first_duplicate(clean)
        if duplicate is not None:
            should_add = messagebox.askyesno(
                "Duplicate entry",
                "This folder already appears in the editor.\n\nAdd it again anyway?",
            )
            if not should_add:
                return

        self.entries.append(self._make_entry(clean, enabled))
        self._sync_view(f"Added PATH entry", select_indices=[len(self.entries) - 1])

    def edit_selected(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return
        entry = self.entries[idx]
        result = self._entry_dialog("Edit PATH entry", entry.path, enabled=entry.enabled)
        if result is None:
            return

        path_value, enabled = result
        entry.path = path_value.strip().strip('"')
        entry.enabled = enabled
        self._sync_view("Updated PATH entry", select_indices=[idx])

    def delete_selected(self) -> None:
        selected = self.selected_model_indices()
        if not selected:
            return

        count = len(selected)
        noun = "entry" if count == 1 else "entries"
        if not messagebox.askyesno("Remove from editor", f"Remove {count} PATH {noun} from the editor?"):
            return

        for idx in sorted(selected, reverse=True):
            del self.entries[idx]

        next_index = min(selected[0], len(self.entries) - 1)
        select_indices = [next_index] if next_index >= 0 else []
        self._sync_view(f"Removed {count} {noun}", select_indices=select_indices)

    def toggle_selected(self) -> None:
        selected = self.selected_model_indices()
        if not selected:
            return

        enable = any(not self.entries[idx].enabled for idx in selected)
        for idx in selected:
            self.entries[idx].enabled = enable

        state = "Enabled" if enable else "Disabled"
        self._sync_view(f"{state} {len(selected)} PATH entries", select_indices=selected)

    def move_selected(self, delta: int) -> None:
        selected = self.selected_model_indices()
        if not selected:
            return

        if delta < 0:
            if selected[0] == 0:
                return
            for idx in selected:
                self.entries[idx - 1], self.entries[idx] = self.entries[idx], self.entries[idx - 1]
            new_indices = [idx - 1 for idx in selected]
        else:
            if selected[-1] >= len(self.entries) - 1:
                return
            for idx in reversed(selected):
                self.entries[idx + 1], self.entries[idx] = self.entries[idx], self.entries[idx + 1]
            new_indices = [idx + 1 for idx in selected]

        self._sync_view("Reordered PATH entries", select_indices=new_indices)

    def normalize_entries(self) -> None:
        changed = 0
        for entry in self.entries:
            clean = entry.path.strip().strip('"')
            if clean != entry.path:
                changed += 1
                entry.path = clean

        self._sync_view(f"Normalized {changed} entries")

    def remove_duplicates(self) -> None:
        unique, removed_paths = self._dedupe_entries(self.entries)
        self.entries = unique
        self._sync_view(f"Removed {len(removed_paths)} duplicate entries")

    def remove_missing(self) -> None:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if not entry.enabled or entry.exists]
        removed = before - len(self.entries)
        self._sync_view(f"Removed {removed} missing enabled entries")

    def _build_safe_fix_plan(self) -> SafeFixPlan:
        normalized_count = 0
        removed_duplicate_paths: list[str] = []
        removed_missing_paths: list[str] = []
        candidates: list[PathEntry] = []

        for entry in self.entries:
            clean = entry.path.strip().strip('"')
            if clean != entry.path:
                normalized_count += 1

            if not clean:
                removed_missing_paths.append("(empty entry)")
                continue

            candidates.append(self._make_entry(clean, entry.enabled))

        deduped_entries, removed_duplicate_paths = self._dedupe_entries(candidates)

        normalized_entries: list[PathEntry] = []
        for updated in deduped_entries:
            if updated.enabled and not updated.exists:
                removed_missing_paths.append(updated.path)
                continue

            normalized_entries.append(updated)

        return SafeFixPlan(
            entries=normalized_entries,
            normalized_count=normalized_count,
            removed_duplicates=removed_duplicate_paths,
            removed_missing=removed_missing_paths,
            reordered_count=0,
            before_count=len(self.entries),
            after_count=len(normalized_entries),
        )

    def _dedupe_entries(self, entries: list[PathEntry]) -> tuple[list[PathEntry], list[str]]:
        positions: dict[str, int] = {}
        unique: list[PathEntry] = []
        removed: list[str] = []

        for entry in entries:
            normalized = self._normalize_path(entry.path)
            if normalized not in positions:
                positions[normalized] = len(unique)
                unique.append(entry)
                continue

            existing_index = positions[normalized]
            existing = unique[existing_index]
            if not existing.enabled and entry.enabled:
                removed.append(existing.path)
                unique[existing_index] = entry
            else:
                removed.append(entry.path)

        return unique, removed

    def apply_safe_fix_pack(self, plan: SafeFixPlan | None = None, skip_confirm: bool = False) -> None:
        if plan is None:
            plan = self._build_safe_fix_plan()

        actionable = plan.normalized_count + len(plan.removed_duplicates) + len(plan.removed_missing) + plan.reordered_count
        if actionable == 0:
            messagebox.showinfo("Recommended fixes", "No recommended fixes are available for this PATH.")
            return

        preview = self._format_safe_fix_preview(plan)
        if not skip_confirm and not self._confirm_text_dialog("Recommended Fixes", preview, confirm_label="Apply"):
            return

        self.entries = plan.entries
        self._sync_view("Applied recommended fixes")

    def _format_safe_fix_preview(self, plan: SafeFixPlan) -> str:
        lines = [
            f"Scope: {self.current_scope.value}",
            f"Entries: {plan.before_count} -> {plan.after_count}",
            "",
            f"Normalize formatting: {plan.normalized_count}",
            f"Remove duplicates: {len(plan.removed_duplicates)}",
            f"Remove missing enabled entries: {len(plan.removed_missing)}",
        ]

        if plan.removed_duplicates:
            lines.extend(["", "Duplicate entries to remove:"])
            lines.extend(f"- {item}" for item in plan.removed_duplicates[:16])
            if len(plan.removed_duplicates) > 16:
                lines.append("- ...")

        if plan.removed_missing:
            lines.extend(["", "Missing or empty enabled entries to remove:"])
            lines.extend(f"- {item}" for item in plan.removed_missing[:16])
            if len(plan.removed_missing) > 16:
                lines.append("- ...")

        lines.extend(["", "Apply these changes to the editor? You can still preview and save afterward."])
        return "\n".join(lines)

    def move_windows_core_first(self) -> None:
        updated = self._prioritize_windows_core(self.entries)
        if self._entry_signature(updated) == self._entry_signature(self.entries):
            messagebox.showinfo("Windows core order", "Windows core folders already appear before other enabled entries.")
            return

        moved = self._count_moved_enabled_entries(self.entries, updated)
        body = (
            f"Move Windows core folders before other enabled entries in {self.current_scope.value} PATH?\n\n"
            f"Entries that would move: {moved}\n\n"
            "This keeps the relative order of non-core entries unchanged."
        )
        if not self._confirm_text_dialog("Windows Core First", body, confirm_label="Move"):
            return

        self.entries = updated
        self._sync_view("Moved Windows core folders first")

    def _prioritize_windows_core(self, entries: list[PathEntry]) -> list[PathEntry]:
        core_patterns = (
            "\\windows\\system32",
            "\\windows\\system32\\wbem",
            "\\windows\\system32\\windowspowershell\\v1.0",
            "\\windows",
        )

        core: list[PathEntry] = []
        other_enabled: list[PathEntry] = []
        disabled: list[PathEntry] = []

        for entry in entries:
            lowered = os.path.expandvars(entry.path).lower()
            if not entry.enabled:
                disabled.append(entry)
            elif any(pattern in lowered for pattern in core_patterns):
                core.append(entry)
            else:
                other_enabled.append(entry)

        return core + other_enabled + disabled

    def _count_moved_enabled_entries(self, before: list[PathEntry], after: list[PathEntry]) -> int:
        before_positions = {
            self._normalize_path(entry.path): idx
            for idx, entry in enumerate(before)
            if entry.enabled and self._normalize_path(entry.path)
        }
        moved = 0
        for idx, entry in enumerate(after):
            key = self._normalize_path(entry.path)
            if entry.enabled and key in before_positions and before_positions[key] != idx:
                moved += 1
        return moved

    def save_scope(self) -> bool:
        if not self.has_unsaved_changes():
            self.status_var.set("No changes to save")
            return True

        if not self._can_write_current_scope():
            messagebox.showwarning(
                "Administrator required",
                "System PATH changes cannot be saved until the app is running as Administrator.",
            )
            self._update_save_button_state()
            return False

        if not self._confirm_save_changes():
            return False

        try:
            self.store.write_entries(self.current_scope, self.entries, reason=f"Save {self.current_scope.value} PATH")
        except PermissionError:
            messagebox.showerror("Permission required", "Administrator approval is required to update System PATH.")
            self._update_save_button_state()
            return False
        except OSError as exc:
            messagebox.showerror("Failed to save", str(exc))
            return False

        self.status_var.set("Saved successfully. New terminals will pick up changes.")
        self.load_scope(force=True)
        self.show_session_restart_guidance()
        return True

    def _confirm_save_changes(self) -> bool:
        body = self._format_save_confirmation()
        return self._confirm_text_dialog("Confirm PATH Save", body, confirm_label="Save Changes")

    def _format_save_confirmation(self) -> str:
        before_entries = self.store.read_entries(self.current_scope)
        before = [entry.path.strip().strip('"') for entry in before_entries if entry.enabled and entry.path.strip()]
        after = [entry.path.strip().strip('"') for entry in self.entries if entry.enabled and entry.path.strip()]

        before_norm = [self._normalize_path(path) for path in before]
        after_norm = [self._normalize_path(path) for path in after]
        before_set = set(before_norm)
        after_set = set(after_norm)

        added = [after[idx] for idx, norm in enumerate(after_norm) if norm not in before_set]
        removed = [before[idx] for idx, norm in enumerate(before_norm) if norm not in after_set]

        index_before = {norm: idx for idx, norm in enumerate(before_norm)}
        moved = sum(1 for idx, norm in enumerate(after_norm) if norm in index_before and index_before[norm] != idx)
        row_changes = self._count_signature_changes(self._entry_signature(before_entries), self._entry_signature(self.entries))
        missing = [entry.path for entry in self.entries if entry.enabled and not entry.exists]

        lines = [
            f"Scope: {self.current_scope.value} PATH",
            f"App space: {'System administrator session' if self.current_scope == Scope.SYSTEM else 'User account session'}",
            "",
            f"Editor rows: {len(before_entries)} -> {len(self.entries)}",
            f"Enabled rows: {sum(1 for entry in before_entries if entry.enabled)} -> {sum(1 for entry in self.entries if entry.enabled)}",
            f"Changed rows: {row_changes}",
            "",
            "Active PATH diff:",
            f"Added: {len(added)}",
            f"Removed: {len(removed)}",
            f"Reordered: {moved}",
            "",
        ]

        if not added and not removed and not moved:
            lines.append("No active PATH value changes detected.")

        if added:
            lines.append("Added:")
            lines.extend(f"+ {item}" for item in added[:24])
            if len(added) > 24:
                lines.append("+ ...")
            lines.append("")

        if removed:
            lines.append("Removed:")
            lines.extend(f"- {item}" for item in removed[:24])
            if len(removed) > 24:
                lines.append("- ...")
            lines.append("")

        if missing:
            lines.append("Warning:")
            lines.append(f"{len(missing)} enabled PATH entries point to folders that do not exist.")
            lines.extend(f"- {item}" for item in missing[:8])
            if len(missing) > 8:
                lines.append("- ...")
            lines.append("")

        lines.append("Save these changes to the Windows registry?")
        return "\n".join(lines)

    @staticmethod
    def _count_signature_changes(before: tuple[tuple[str, bool], ...], after: tuple[tuple[str, bool], ...]) -> int:
        total = max(len(before), len(after))
        changes = 0
        for idx in range(total):
            before_item = before[idx] if idx < len(before) else None
            after_item = after[idx] if idx < len(after) else None
            if before_item != after_item:
                changes += 1
        return changes

    def undo(self) -> None:
        snapshots = self.store.latest_snapshots()
        if not snapshots:
            messagebox.showinfo("Undo", "No snapshot available.")
            return

        latest = snapshots[0]
        if not self._can_write_current_scope():
            messagebox.showwarning("Administrator required", "Administrator approval is required to restore System PATH.")
            return

        if not messagebox.askyesno("Undo", f"Restore {self.current_scope.value} PATH from latest snapshot?\n{latest.name}"):
            return

        try:
            self.store.restore_snapshot(latest, scope=self.current_scope)
        except PermissionError:
            messagebox.showerror("Permission required", "Administrator approval is required to restore System PATH.")
            return

        self.status_var.set(f"Restored {self.current_scope.value} PATH from {latest.name}")
        self.load_scope(force=True)

    def restore_snapshot(self) -> None:
        snapshots = self.store.latest_snapshots()
        if not snapshots:
            messagebox.showinfo("Restore", "No snapshots found.")
            return

        if not self._can_write_current_scope():
            messagebox.showwarning("Administrator required", "Administrator approval is required to restore System PATH.")
            return

        choice = self._pick_snapshot(snapshots)
        if choice is None:
            return

        if not messagebox.askyesno("Restore snapshot", f"Restore {self.current_scope.value} PATH from {choice.name}?"):
            return

        try:
            self.store.restore_snapshot(choice, scope=self.current_scope)
        except PermissionError:
            messagebox.showerror("Permission required", "Administrator approval is required to restore System PATH.")
            return

        self.status_var.set(f"Restored {self.current_scope.value} PATH from {choice.name}")
        self.load_scope(force=True)

    def preview_effective_path(self) -> None:
        user_entries = self.entries if self.current_scope == Scope.USER else self.store.read_entries(Scope.USER)
        system_entries = self.entries if self.current_scope == Scope.SYSTEM else self.store.read_entries(Scope.SYSTEM)

        combined = [entry for entry in system_entries + user_entries if entry.enabled and entry.path.strip()]
        lines = [
            "Effective PATH order for new processes",
            "System scope is listed before User scope.",
            "",
        ]
        for idx, entry in enumerate(combined, start=1):
            lines.append(f"{idx:>3}. [{entry.scope.value}] {entry.path}")

        self._show_text_dialog("Effective PATH", "\n".join(lines))

    def run_command_test(self) -> None:
        command = self.command_var.get().strip()
        if not command:
            return

        current_resolution = resolve_command(command, self.entries)
        other_scope = Scope.SYSTEM if self.current_scope == Scope.USER else Scope.USER
        other_entries = self.store.read_entries(other_scope)
        other_resolution = resolve_command(command, other_entries)

        if self.current_scope == Scope.USER:
            user_entries = self.entries
            system_entries = other_entries
        else:
            system_entries = self.entries
            user_entries = other_entries
        effective_resolution = resolve_command(command, system_entries + user_entries)
        session_winner = quick_find_current(command)

        lines = [
            f"Command: {command}",
            "",
            f"{self.current_scope.value} winner: {current_resolution.winner or 'No match'}",
            f"{other_scope.value} winner: {other_resolution.winner or 'No match'}",
            f"Effective winner: {effective_resolution.winner or 'No match'}",
            f"Current session winner: {session_winner or 'No match'}",
            "",
        ]

        if session_winner and effective_resolution.winner:
            if self._normalize_path(session_winner) != self._normalize_path(effective_resolution.winner):
                lines.extend(
                    [
                        "Session note:",
                        "The current process resolves this command differently than the editor preview.",
                        "Save changes and restart terminals or IDEs for new PATH values.",
                        "",
                    ]
                )

        lines.append(f"Matches in {self.current_scope.value} scope:")
        if current_resolution.all_matches:
            lines.extend(f"- {match}" for match in current_resolution.all_matches)
        else:
            lines.append("- none")

        lines.extend(["", "Effective matches:"])
        if effective_resolution.all_matches:
            lines.extend(f"- {match}" for match in effective_resolution.all_matches)
        else:
            lines.append("- none")

        self._set_text(self.tester_output, "\n".join(lines))

    def _quick_command(self, command: str) -> None:
        self.command_var.set(command)
        self.run_command_test()
        self.notebook.select(2)

    def open_selected_path(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return

        target = os.path.expandvars(self.entries[idx].path)
        if not os.path.isdir(target):
            messagebox.showwarning("Open folder", "Directory does not exist.")
            return
        subprocess.Popen(["explorer", target])

    def open_terminal_here(self) -> None:
        idx = self.selected_model_index()
        if idx is None:
            return

        target = os.path.expandvars(self.entries[idx].path)
        if not os.path.isdir(target):
            messagebox.showwarning("Open terminal", "Directory does not exist.")
            return
        subprocess.Popen(["cmd", "/k", "cd", "/d", target])

    def copy_selected_paths(self) -> None:
        selected = self.selected_model_indices()
        if not selected:
            return

        value = "\n".join(self.entries[idx].path for idx in selected)
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status_var.set(f"Copied {len(selected)} PATH entries")

    def auto_health_scan(self) -> None:
        if self.did_startup_scan:
            return
        self.did_startup_scan = True

        plan = self._build_safe_fix_plan()
        actionable = plan.normalized_count + len(plan.removed_duplicates) + len(plan.removed_missing) + plan.reordered_count
        if actionable <= 0:
            return

        self.status_var.set(f"Health scan found {actionable} recommended fixes")
        self.notebook.select(0)

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
            "New terminals opened after this save should pick up the new PATH.",
        )

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

    def _entry_dialog(self, title: str, initial: str, enabled: bool = True) -> tuple[str, bool] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(background=COLORS["bg"])

        shell = ttk.Frame(dialog, style="App.TFrame", padding=14)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="Path", style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(shell, style="App.TFrame")
        row.pack(fill="x", pady=(5, 10))
        value_var = tk.StringVar(value=initial)
        entry = ttk.Entry(row, textvariable=value_var, width=84)
        entry.pack(side="left", fill="x", expand=True)

        def browse() -> None:
            directory = filedialog.askdirectory(title="Choose folder", parent=dialog)
            if directory:
                value_var.set(directory)

        ttk.Button(row, text="Browse", style="Tool.TButton", command=browse).pack(side="left", padx=(8, 0))

        enabled_var = tk.BooleanVar(value=enabled)
        ttk.Checkbutton(shell, text="Enabled", variable=enabled_var).pack(anchor="w")

        result: dict[str, tuple[str, bool] | None] = {"value": None}

        buttons = ttk.Frame(shell, style="App.TFrame")
        buttons.pack(fill="x", pady=(14, 0))

        def accept() -> None:
            clean = value_var.get().strip().strip('"')
            if not clean:
                messagebox.showwarning("Path required", "Enter a folder path.", parent=dialog)
                return
            result["value"] = (clean, bool(enabled_var.get()))
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", style="Primary.TButton", command=accept).pack(side="right", padx=(0, 8))

        dialog.bind("<Return>", lambda _: accept())
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        entry.focus_set()
        entry.select_range(0, tk.END)
        self.root.wait_window(dialog)
        return result["value"]

    def _confirm_text_dialog(self, title: str, body: str, confirm_label: str = "Apply") -> bool:
        return self._text_dialog(title, body, confirm_label=confirm_label)

    def _show_text_dialog(self, title: str, body: str) -> None:
        self._text_dialog(title, body, confirm_label=None)

    def _text_dialog(self, title: str, body: str, confirm_label: str | None) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("760x520")

        shell = ttk.Frame(dialog, style="App.TFrame", padding=12)
        shell.pack(fill="both", expand=True)

        text = tk.Text(
            shell,
            wrap="word",
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            padx=10,
            pady=8,
        )
        text.pack(fill="both", expand=True)
        text.insert("1.0", body)
        text.configure(state="disabled")

        result = {"ok": False}
        footer = ttk.Frame(shell, style="App.TFrame")
        footer.pack(fill="x", pady=(10, 0))

        def accept() -> None:
            result["ok"] = True
            dialog.destroy()

        close_label = "Cancel" if confirm_label else "Close"
        ttk.Button(footer, text=close_label, command=dialog.destroy).pack(side="right")
        if confirm_label:
            ttk.Button(footer, text=confirm_label, style="Primary.TButton", command=accept).pack(side="right", padx=(0, 8))

        self.root.wait_window(dialog)
        return bool(result["ok"])

    def _pick_snapshot(self, snapshots: list[Path]) -> Path | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Select snapshot")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry("620x440")

        shell = ttk.Frame(dialog, style="App.TFrame", padding=12)
        shell.pack(fill="both", expand=True)

        listbox = tk.Listbox(
            shell,
            background=COLORS["surface"],
            foreground=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
        )
        listbox.pack(fill="both", expand=True)
        for path in snapshots:
            listbox.insert(tk.END, path.name)

        result: dict[str, Path | None] = {"path": None}

        def accept() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            result["path"] = snapshots[int(selection[0])]
            dialog.destroy()

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(footer, text="Restore", style="Primary.TButton", command=accept).pack(side="right", padx=(0, 8))
        listbox.bind("<Double-1>", lambda _: accept())

        self.root.wait_window(dialog)
        return result["path"]

    def _confirm_leave_dirty(self, action: str) -> bool:
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save changes before you {action}?",
        )
        if answer is None:
            return False
        if answer:
            return self.save_scope()
        return True

    def _on_close(self) -> None:
        if self.has_unsaved_changes() and not self._confirm_leave_dirty("exit"):
            return
        self.root.destroy()

    @staticmethod
    def _initial_scope_from_args() -> Scope:
        for arg in sys.argv[1:]:
            lowered = arg.lower()
            if lowered in {"--system", "--scope=system", "/system"}:
                return Scope.SYSTEM
            if lowered in {"--user", "--scope=user", "/user"}:
                return Scope.USER
        return Scope.USER

    def _relaunch_as_admin(self, target_scope: Scope | None = None) -> bool:
        script_path = Path(sys.argv[0]).resolve()
        args = [f'"{script_path}"']
        if target_scope is not None:
            args.append(f"--scope={target_scope.value}")
        params = " ".join(args)
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

    @staticmethod
    def _is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _can_write_current_scope(self) -> bool:
        return self.current_scope == Scope.USER or self._is_admin()

    def _make_entry(self, path_value: str, enabled: bool) -> PathEntry:
        clean = path_value.strip().strip('"')
        expanded = os.path.expandvars(clean)
        exists = os.path.isdir(expanded)
        accessible = os.access(expanded, os.R_OK) if exists else False
        return PathEntry(
            path=clean,
            scope=self.current_scope,
            enabled=enabled,
            exists=exists,
            accessible=accessible,
        )

    def _find_first_duplicate(self, path_value: str) -> int | None:
        normalized = self._normalize_path(path_value)
        for idx, entry in enumerate(self.entries):
            if self._normalize_path(entry.path) == normalized:
                return idx
        return None

    def has_unsaved_changes(self) -> bool:
        return self._entry_signature(self.entries) != self.loaded_signature

    @staticmethod
    def _entry_signature(entries: list[PathEntry]) -> tuple[tuple[str, bool], ...]:
        return tuple((entry.path, entry.enabled) for entry in entries)

    @staticmethod
    def _normalize_path(path_value: str) -> str:
        clean = path_value.strip().strip('"')
        expanded = os.path.expandvars(clean)
        return os.path.normcase(os.path.normpath(expanded)) if expanded else ""

    def run(self) -> None:
        self.root.mainloop()
