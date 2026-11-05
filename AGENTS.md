# Agent Guidelines (`AGENTS.md`)

## Project Purpose
This repository provides `teams-pwa-companion` ([teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py)), a KDE Plasma 6 Wayland background daemon that adds a live System Tray icon with unread badge counts, native KDE Taskbar launcher badges (`com.canonical.Unity.LauncherEntry`), click-to-focus support via KWin D-Bus, and persistent critical meeting-start notifications for the **Microsoft Teams Google Chrome PWA** on Kubuntu 26.04.

## Key Files
- [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py): Single-file Python 3 daemon using system packages (`PyQt6`, `dbus-python`, `PyGObject`).
- [install.sh](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/install.sh): Generates and enables `~/.config/systemd/user/teams-pwa-companion.service`.
- [README.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/README.md): User documentation, installation steps, and recommended Chrome/Teams settings.
- [NOTES.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/NOTES.md): Deep technical root-cause notes on `teams-for-linux` Snap screen-sharing failures and Chrome PWA D-Bus/notification limitations.

## Architecture & Constraints
1. **No External Pip Dependencies Required**:
   - Always use `/usr/bin/python3` with system-installed packages (`python3-pyqt6`, `python3-dbus`, `python3-gi`). Do not require a virtual environment unless explicitly requested, as `~/.config/systemd/user/teams-pwa-companion.service` executes `/usr/bin/python3` directly.
2. **Wayland Window Detection & Activation**:
   - On KDE Plasma 6 Wayland, X11 tools like `wmctrl` or `xdotool` cannot see native Wayland windows or reliably inspect all KWin surfaces.
   - Use KWin's D-Bus KRunner interface (`org.kde.KWin`, path `/WindowsRunner`, interface `org.kde.krunner1`) with `Match("Microsoft Teams")` to read window titles and `Run(match_id, "")` to activate the window.
3. **Chrome Teams PWA Identifiers**:
   - Chrome App ID: `ompifgpmddkgmclendfeacglnodjjndh`
   - Desktop File ID: `chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop`
   - Unity Launcher URI: `application://chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop`

## Verification & Testing
After modifying [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py) or [install.sh](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/install.sh):
1. Restart the user service:
   ```bash
   systemctl --user restart teams-pwa-companion.service
   systemctl --user status teams-pwa-companion.service --no-pager
   ```
2. Verify registration with KDE's `StatusNotifierWatcher`:
   ```bash
   qdbus6 org.kde.StatusNotifierWatcher /StatusNotifierWatcher org.kde.StatusNotifierWatcher.RegisteredStatusNotifierItems
   ```
3. Check logs for D-Bus or Qt warnings:
   ```bash
   journalctl --user -u teams-pwa-companion.service -n 50 --no-pager
   ```
