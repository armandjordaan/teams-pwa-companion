# Microsoft Teams Chrome PWA Companion for KDE Plasma (`teams-pwa-companion`)

A lightweight Python/PyQt6 companion daemon for **Kubuntu 26.04 (KDE Plasma 6 Wayland)** that restores native desktop app integration—System Tray unread badges, KDE Taskbar icon badges, click-to-focus across multi-monitor setups, and persistent critical meeting alerts—for the **Microsoft Teams Google Chrome PWA**.

---

## Why This Exists

Running Microsoft Teams as a Google Chrome Progressive Web App (PWA) provides reliable PipeWire/XDG Desktop Portal screen sharing on KDE Plasma Wayland (unlike the `teams-for-linux` Snap package, see [NOTES.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/NOTES.md)). However, Chrome PWAs on Linux suffer from three desktop integration gaps:

1. **No System Tray Icon (`StatusNotifierItem`)**: Web apps cannot create a persistent KDE System Tray icon or display an unread badge in the tray.
2. **No KDE Taskbar Unread Badge**: Google Chrome on Linux does not implement the `com.canonical.Unity.LauncherEntry` D-Bus protocol for `navigator.setAppBadge()`, so KDE's Icons-Only Task Manager never shows unread message counts on the Teams PWA icon.
3. **Missed Meeting-Start & Message Notifications**:
   - On multi-monitor setups, Teams suppresses notifications or shows silent in-app banners if the PWA window is open on an unfocused monitor.
   - KDE's notification daemon (`kded6`) rate-limits burst notifications from Chrome (`Created too many similar notifications in quick succession`) and times out normal notifications after 10 seconds.

---

## Features

- **Live KDE System Tray Icon with Unread Badge**:
  - Polls KWin's Wayland D-Bus interface (`org.kde.KWin /WindowsRunner`) for the Teams PWA window title (e.g., `Microsoft Teams (PWA) - (8) Chat | ...`) and dynamically renders a red circular badge with the unread count directly on the tray icon.
  - **Left-click** the tray icon to immediately focus/activate the Teams PWA window across all monitors (or launch the PWA if it was closed).
  - **Right-click** context menu to inspect unread count, focus/launch Teams, or trigger a test meeting alert.
- **Native KDE Taskbar Unread Badge**:
  - Emits `com.canonical.Unity.LauncherEntry` D-Bus signals for `application://chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop` so the KDE Plasma panel icon displays the native unread count badge.
- **Persistent Critical Meeting Alerts, Auto-Focus & Dual Speaker/Headphone Ringer**:
  - Monitors both KWin window title changes and D-Bus `org.freedesktop.Notifications` calls from Chrome (`teams.cloud.microsoft`).
  - Elevates **"Meeting started"** and **incoming call** alerts to `urgency=2` (Critical) with `timeout=0` so they stay pinned on screen across all monitors until dismissed.
  - **Plays alerts through both your laptop speakers and USB headphones simultaneously** (`pw-play --target=<sink>` across all active PipeWire output sinks), acting as a "Secondary Ringer" when your headset isn't on your head.
  - **Automatically focuses and raises the Microsoft Teams PWA window** via KWin's Wayland D-Bus API (`org.kde.KWin /WindowsRunner`) when an incoming call or meeting start is detected (both auto-focus and dual-speaker ringing can be toggled on/off from the tray icon's right-click menu).

---

## Files in This Repository

| File | Description |
| :--- | :--- |
| [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py) | Main Python 3 / PyQt6 / D-Bus companion daemon. |
| [install.sh](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/install.sh) | Installer script that configures and enables the `systemd --user` service (`teams-pwa-companion.service`) and removes duplicate autostart entries. |
| [NOTES.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/NOTES.md) | Deep-dive root cause analysis of `teams-for-linux` Snap screen-sharing failures and Chrome PWA notification architecture on Kubuntu 26.04. |
| [AGENTS.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/AGENTS.md) | Instructions and technical context for AI coding agents. |
| [GEMINI.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/GEMINI.md) | Gemini / Antigravity agent guidelines for this repository. |
| [CLAUDE.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/CLAUDE.md) | Claude Code agent guidelines for this repository. |
| [LICENSE](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/LICENSE) | MIT License. |

---

## Installation & Service Management

### Install / Update the Auto-Start Service

```bash
./install.sh
```

This creates `~/.config/systemd/user/teams-pwa-companion.service`, orders it after `plasma-plasmashell.service` so the KDE system tray is ready on boot, and starts it immediately.

### Useful Commands

```bash
# Check service status
systemctl --user status teams-pwa-companion.service

# View live logs
journalctl --user -u teams-pwa-companion.service -f

# Restart the companion daemon
systemctl --user restart teams-pwa-companion.service

# Stop and disable auto-start
systemctl --user disable --now teams-pwa-companion.service
```

---

## Recommended Chrome & Teams PWA Settings

1. **Prevent Chrome Memory Saver from Suspending Teams**:
   - Open `chrome://settings/performance` in Google Chrome.
   - Under **Memory -> Always keep these sites active**, add:
     - `teams.cloud.microsoft`
     - `teams.microsoft.com`
2. **Enable Teams Meeting-Start Banners**:
   - In the Teams PWA, open **`...` (Settings and more) -> Settings -> Notifications and activity**.
   - Enable **Play sounds for incoming calls and notifications**.
   - Enable **Show notifications during calls and meetings** (optional, recommended).
   - Under **Meetings**, set **Meeting started notification** to **Banner and feed**.

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/LICENSE) file for details.
