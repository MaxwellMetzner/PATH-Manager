# PATH Manager (Windows)

Desktop app for editing Windows PATH without wrestling with a semicolon-delimited registry value.

PATH Manager is a Windows-focused Tkinter app for inspecting, cleaning, and editing both User and System PATH values. It keeps changes visible before you save, flags common PATH problems, helps resolve command precedence, and creates backup snapshots before registry writes.

## What It Does

- Shows PATH as a reorderable list instead of a single semicolon-delimited string
- Separates **My PATH** (the account running the app) from **All users (System)** PATH
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
- Read-only combined PATH tab with order numbers and scope labels; includes unsaved edits and excludes disabled entries
- Folder search, visible reorder controls, and full issue explanations with double-click navigation to the affected folder
- User/System scope switching with explicit impact descriptions
- Browse System PATH without elevation; explicitly unlock system editing when needed
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

## Using the interface

- **My PATH** is for personal tools and affects only the Windows account running PATH Manager.
- **All users (System)** is shared across accounts. It opens read-only in a non-administrator session.
  Choose **Unlock system editing...** to restart with Administrator permission.
  If elevation uses another account's credentials, **My PATH** in that elevated app belongs to that account.
- **Folders** shows the selected scope. Search narrows the display, not the saved list; order numbers
  still refer to the full list. Use **Tools** for cleanup and snapshot restore.
- **Combined PATH (read-only)** shows System folders followed by My PATH. Moving a personal folder
  to the top of My PATH does not place it ahead of System folders. This is a PATH-only preview;
  shells may have aliases, extra search locations, or a different inherited environment.
- **Health** explains warnings; double-click an issue to locate its folder. **Folder details** shows
  expanded paths and permissions. **Find command** compares both scopes, the combined preview,
  and this app's inherited environment.
- **Save User PATH** or **Save System PATH** reviews and writes only the selected scope.
  Editing, cleanup, and reordering do not write to the registry until you save.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The UI tests require Windows with Tkinter. They use a mocked environment store and do not write PATH registry values.

## Notes

- The app writes to registry keys used by Windows PATH:
  - `HKCU\Environment\Path`
  - `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\Path`
- Backup snapshots are stored under the user's app data directory in `PathManager\backups`
- After save, the app broadcasts `WM_SETTINGCHANGE`, but already-open terminals may need restart.
