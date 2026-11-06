from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from path_manager.app import PathManagerApp
from path_manager.models import PathEntry, Scope


class PathManagerUITests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {
            Scope.USER: [
                PathEntry(str(Path.home()), Scope.USER),
                PathEntry(r"C:\missing-personal-tool", Scope.USER),
                PathEntry(r"C:\disabled-personal-tool", Scope.USER, enabled=False),
            ],
            Scope.SYSTEM: [PathEntry(r"C:\Windows", Scope.SYSTEM)],
        }
        self.store = Mock()
        self.store.read_entries.side_effect = lambda scope: copy.deepcopy(self.saved[scope])

        def write_entries(scope: Scope, entries: list[PathEntry], reason: str) -> None:
            self.saved[scope] = copy.deepcopy(entries)

        self.store.write_entries.side_effect = write_entries
        self.admin = self.enterContext(patch.object(PathManagerApp, "_is_admin", return_value=False))
        self.enterContext(patch("path_manager.app.EnvStore", return_value=self.store))
        self.enterContext(patch("path_manager.app.sys.argv", ["main.py"]))
        self.app = PathManagerApp()
        self.app.root.withdraw()
        self.addCleanup(self.destroy_app)
        self.app.root.update_idletasks()

    def destroy_app(self) -> None:
        for after_id in self.app.root.tk.splitlist(self.app.root.tk.call("after", "info")):
            self.app.root.after_cancel(after_id)
        self.app.root.destroy()

    def switch(self, scope: Scope) -> None:
        self.app.scope_var.set(scope.value)
        self.app._on_scope_changed()

    def test_system_browsing_does_not_elevate_and_all_mutations_are_locked(self) -> None:
        with patch.object(self.app, "_relaunch_as_admin") as elevate:
            self.switch(Scope.SYSTEM)
        elevate.assert_not_called()
        self.assertEqual(self.app.current_scope, Scope.SYSTEM)
        self.app.tree.selection_set("entry:0")
        self.app._on_selection_changed()
        before = self.app._entry_signature(self.app.entries)
        for action in (
            self.app.add_entry, self.app.edit_selected, self.app.delete_selected,
            self.app.toggle_selected, self.app.normalize_entries, self.app.remove_duplicates,
            self.app.remove_missing, self.app.apply_safe_fix_pack, self.app.move_windows_core_first,
            lambda: self.app.move_selected(1),
            lambda: self.app._add_path_entry(r"C:\new-tool", True),
        ):
            action()
            self.assertEqual(self.app._entry_signature(self.app.entries), before)
        for widget in (self.app.add_entry_button, self.app.edit_button, self.app.toggle_button,
                       self.app.delete_button, self.app.up_button, self.app.down_button, self.app.save_button):
            self.assertIn("disabled", widget.state())
        for menu, indices in ((self.app.edit_menu, (0, 1, 2, 4, 5, 6)),
                              (self.app.tools_menu, (0, 1, 2, 4, 5)),
                              (self.app.context_menu, (0, 1, 2, 4, 5)),
                              (self.app.file_menu, (0, 2, 3))):
            for index in indices:
                self.assertEqual(menu.entrycget(index, "state"), "disabled")
        self.store.write_entries.assert_not_called()
        self.assertEqual(self.app.unlock_button.winfo_manager(), "pack")

    def test_combined_order_labels_and_unsaved_edits(self) -> None:
        rows = [self.app.combined_tree.item(iid, "values") for iid in self.app.combined_tree.get_children()]
        self.assertEqual([row[1] for row in rows], ["All users", "My PATH", "My PATH"])
        self.assertEqual(rows[0][2], r"C:\Windows")
        self.app._add_path_entry(r"C:\new-tool", True)
        rows = [self.app.combined_tree.item(iid, "values") for iid in self.app.combined_tree.get_children()]
        self.assertEqual(rows[-1][2], r"C:\new-tool")
        self.assertIn("unsaved", self.app.combined_note_var.get())
        self.store.write_entries.assert_not_called()

    def test_filtered_selection_edits_original_model_index(self) -> None:
        self.app.filter_var.set("MISSING-PERSONAL")
        self.assertEqual(self.app.tree.get_children(), ("entry:1",))
        self.app.tree.selection_set("entry:1")
        self.app.toggle_selected()
        self.assertTrue(self.app.entries[0].enabled)
        self.assertFalse(self.app.entries[1].enabled)
        self.assertEqual(self.app.tree.item("entry:1", "values")[0], "2")

    def test_dirty_switch_saves_original_scope_not_destination(self) -> None:
        self.app._add_path_entry(r"C:\new-tool", True)
        with patch("path_manager.app.messagebox.askyesnocancel", return_value=True), \
                patch.object(self.app, "_confirm_save_changes", return_value=True), \
                patch.object(self.app, "show_session_restart_guidance"):
            self.switch(Scope.SYSTEM)
        self.store.write_entries.assert_called_once()
        self.assertEqual(self.store.write_entries.call_args.args[0], Scope.USER)
        self.assertEqual(self.saved[Scope.USER][-1].path, r"C:\new-tool")
        self.assertEqual([entry.path for entry in self.saved[Scope.SYSTEM]], [r"C:\Windows"])
        self.assertEqual(self.app.current_scope, Scope.SYSTEM)

    def test_cancel_dirty_switch_preserves_edits(self) -> None:
        self.app._add_path_entry(r"C:\new-tool", True)
        before = self.app._entry_signature(self.app.entries)
        with patch("path_manager.app.messagebox.askyesnocancel", return_value=None):
            self.switch(Scope.SYSTEM)
        self.assertEqual(self.app.current_scope, Scope.USER)
        self.assertEqual(self.app._entry_signature(self.app.entries), before)

    def test_admin_can_edit_system_without_unlock_button(self) -> None:
        self.admin.return_value = True
        self.switch(Scope.SYSTEM)
        self.app._add_path_entry(r"C:\shared-tool", True)
        self.assertEqual(self.app.entries[-1].scope, Scope.SYSTEM)
        self.assertNotIn("disabled", self.app.save_button.state())
        self.assertEqual(self.app.unlock_button.winfo_manager(), "")

    def test_cancelled_elevation_preserves_read_only_view(self) -> None:
        self.switch(Scope.SYSTEM)
        with patch("path_manager.app.messagebox.askyesno", return_value=True), \
                patch("path_manager.app.messagebox.showwarning") as warning, \
                patch.object(self.app, "_relaunch_as_admin", return_value=False) as elevate:
            self.app.unlock_system_editing()
        elevate.assert_called_once_with(target_scope=Scope.SYSTEM)
        warning.assert_called_once()
        self.assertTrue(self.app.root.winfo_exists())
        self.assertFalse(self.app._can_write_current_scope())

    def test_failed_scope_read_keeps_original_scope_and_model(self) -> None:
        before = self.app._entry_signature(self.app.entries)
        self.store.read_entries.side_effect = PermissionError("Read denied")
        with patch("path_manager.app.messagebox.showerror") as error:
            self.switch(Scope.SYSTEM)
        error.assert_called_once()
        self.assertEqual(self.app.current_scope, Scope.USER)
        self.assertEqual(self.app._entry_signature(self.app.entries), before)

    def test_combined_read_failure_is_explicit_not_stale_success(self) -> None:
        self.store.read_entries.side_effect = PermissionError("Read denied")
        self.app._refresh_combined_view()
        self.assertEqual(self.app.combined_tree.get_children(), ())
        self.assertIn("Could not read", self.app.combined_note_var.get())
        self.app.run_command_test()
        self.assertIn("Could not read", self.app.tester_output.get("1.0", "end"))

    def test_issue_navigation_clears_filter_and_shows_folder_details(self) -> None:
        index = next(i for i, issue in enumerate(self.app.current_issues)
                     if issue.path == r"C:\missing-personal-tool" and issue.code == "missing")
        self.app.filter_var.set("no-match")
        self.app.issue_tree.selection_set(f"issue:{index}")
        self.app._select_issue_entry()
        self.assertEqual(self.app.filter_var.get(), "")
        self.assertEqual(self.app.selected_model_indices(), [1])
        self.assertEqual(self.app.notebook.index(self.app.notebook.select()), 1)

    def test_new_entry_is_visible_even_when_search_would_hide_it(self) -> None:
        self.app.filter_var.set("no-match")
        self.app.workspace.select(1)
        self.app._add_path_entry(r"C:\new-tool", True)
        self.assertEqual(self.app.filter_var.get(), "")
        self.assertEqual(self.app.selected_model_indices(), [3])
        self.assertEqual(self.app.workspace.index(self.app.workspace.select()), 0)

    def test_command_preview_gives_system_match_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = Path(directory) / "system"
            user = Path(directory) / "user"
            system.mkdir()
            user.mkdir()
            (system / "example.exe").touch()
            (user / "example.exe").touch()
            self.saved[Scope.SYSTEM] = [PathEntry(str(system), Scope.SYSTEM)]
            self.saved[Scope.USER] = [PathEntry(str(user), Scope.USER)]
            self.app.load_scope(force=True)
            self.app.command_var.set("example")
            with patch.dict("os.environ", {"PATHEXT": ".EXE"}), \
                    patch("path_manager.app.quick_find_current", return_value=str(user / "example.exe")):
                self.app.run_command_test()
            output = self.app.tester_output.get("1.0", "end")
            self.assertIn(f"Combined PATH preview winner: {system / 'example.exe'}", output)
            self.assertIn(f"This app's inherited environment winner: {user / 'example.exe'}", output)
            self.assertIn("resolves this command differently", output)

    def test_primary_controls_fit_at_minimum_window_size(self) -> None:
        self.app.root.geometry("1060x680")
        self.app.root.deiconify()
        self.app.root.update()
        try:
            for scope in (Scope.USER, Scope.SYSTEM):
                self.switch(scope)
                for tab in range(3):
                    self.app.notebook.select(tab)
                    self.app.root.update()
                    for widget in (self.app.add_entry_button, self.app.edit_button, self.app.toggle_button,
                                   self.app.delete_button, self.app.up_button, self.app.down_button,
                                   self.app.more_button, self.app.open_button, self.app.terminal_button,
                                   self.app.copy_button, self.app.save_button, self.app.unlock_button):
                        if widget.winfo_ismapped():
                            self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(),
                                                    f"Clipped {widget.cget('text')} in {scope.value}, tab {tab}")
        finally:
            self.app.root.withdraw()


if __name__ == "__main__":
    unittest.main()
