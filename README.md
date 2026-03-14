# PATH Manager (Windows)

Minimal desktop app to manage PATH entries with a cleaner UX than the default Environment Variables dialog.

PATH Manager is a Windows-focused Tkinter app for inspecting, cleaning, and editing both User and System PATH values without manually working through the registry or the stock system dialog. It keeps changes visible before you save, flags common PATH problems early, and creates a backup snapshot before every write.

## What It Does

- Shows PATH as a reorderable list instead of a single semicolon-delimited string
- Lets you work against User or System scope from the same UI
- Helps clean PATH safely with normalization, duplicate removal, missing-path cleanup, and a guided Safe Fix Pack
- Surfaces diagnostics for unresolved variables, risky writable folders, relative entries, and command shadowing
- Saves backup snapshots automatically so you can undo or restore previous PATH states

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

## How It Works

- Reads and writes the Windows PATH registry values for the selected scope
- Stores disabled entries and snapshot history in the app data directory
- Broadcasts `WM_SETTINGCHANGE` after save so new processes can pick up the updated environment
- Leaves already-open terminals and IDEs alone, so some running tools may still need restart

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
- Backup snapshots are stored under the user's app data directory in `PathManager\backups`
- After save, the app broadcasts `WM_SETTINGCHANGE`, but already-open terminals may need restart.
