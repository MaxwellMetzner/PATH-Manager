# PATH Manager (Windows)

Desktop app for editing Windows PATH without wrestling with a semicolon-delimited registry value.

PATH Manager is a Windows-focused Tkinter app for inspecting, cleaning, and editing both User and System PATH values. It keeps changes visible before you save, flags common PATH problems, helps resolve command precedence, and creates backup snapshots before registry writes.

## What It Does

- Shows PATH as a reorderable list instead of a single semicolon-delimited string
- Lets you work against User or System scope from the same UI
- Helps clean PATH safely with normalization, duplicate removal, missing-path cleanup, and recommended fixes
- Surfaces diagnostics for unresolved variables, risky writable folders, relative entries, and command shadowing
- Saves backup snapshots automatically so you can undo or restore previous PATH states

## Features

- Create / Read / Edit / Delete PATH entries
- Open target folder in Explorer or terminal
- Reorder one or multiple entries to control precedence
- Enable/disable entries without deleting
- Metrics for enabled, disabled, missing, issue count, and PATH length
- Selected-entry inspector with expanded path, accessibility, writability, and per-entry issues
- One-click normalize, remove duplicates, remove missing entries, and apply recommended fixes
- Optional Windows core ordering helper
- Startup health scan that surfaces available fixes without forcing a modal prompt
- Validation for missing/inaccessible entries
- Detection for unresolved `%VAR%` references and relative/current-directory entries
- Risk warnings for writable folders early in PATH
- Duplicate and command-shadow detection
- Command resolver showing current scope, other scope, effective PATH, and current session winner
- Save confirmation with a diff summary before registry writes
- Effective PATH preview
- User/System scope switching
- System scope automatically requests Administrator elevation when selected
- Post-save session awareness with restart guidance for running shells/IDEs
- Backup snapshots before every save
- Undo/restore selected scope from snapshots
- Context menu and keyboard shortcuts for common edits

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

> Selecting **System PATH** prompts for Administrator elevation.

## Notes

- The app writes to registry keys used by Windows PATH:
  - `HKCU\Environment\Path`
  - `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\Path`
- Backup snapshots are stored under the user's app data directory in `PathManager\backups`
- After save, the app broadcasts `WM_SETTINGCHANGE`, but already-open terminals may need restart.
