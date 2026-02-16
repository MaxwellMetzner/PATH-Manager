# PATH Manager (Windows)

Minimal desktop app to manage PATH entries with a cleaner UX than the default Environment Variables dialog.

## Features

- Create / Read / Edit / Delete PATH entries
- Open target folder in Explorer or terminal
- Reorder entries to control precedence
- Enable/Disable entries without deleting
- One-click normalize / remove duplicates / remove missing entries
- Startup health scan with proposed-change preview before Safe Fix Pack
- Validation for missing/inaccessible entries
- Detection for unresolved `%VAR%` references and relative/current-directory entries
- Risk warnings for writable folders early in PATH
- Duplicate and command-shadow detection
- Command resolver showing current scope, other scope, and current session winner
- Save diff preview (added/removed/reordered)
- User/System scope switching
- Admin preflight: System Save is disabled when not elevated
- Post-save session awareness with restart guidance for running shells/IDEs
- Backup snapshots before every save
- Undo/restore from snapshots
- Profile import/export (JSON)
- Search/filter entries
- Grouped/compact actions with icon-style quick actions to reduce toolbar clutter

## Run

```powershell
python -m pip install -e .
python main.py
```

> Editing **System PATH** requires running the app elevated (Administrator).

## Notes

- The app writes to registry keys used by Windows PATH:
  - `HKCU\Environment\Path`
  - `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\Path`
- After save, the app broadcasts `WM_SETTINGCHANGE`, but already-open terminals may need restart.
