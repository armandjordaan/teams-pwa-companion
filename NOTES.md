# Technical Research & Root Cause Notes (`NOTES.md`)

## System Environment
- **OS**: Kubuntu 26.04 (Resolute)
- **Desktop Environment**: KDE Plasma 6.6.6 (`kwin_wayland`, 3 connected displays)
- **Portals**: `xdg-desktop-portal 1.20.3`, `xdg-desktop-portal-kde 6.6.6`
- **PipeWire**: `1.6.2` (`/run/user/1000/pipewire-0` and `/run/user/1000/pipewire-0-manager`)
- **Browser**: `google-chrome-stable 156.0.8078.47-1`
- **Teams Chrome PWA ID**: `ompifgpmddkgmclendfeacglnodjjndh` (`https://teams.cloud.microsoft/`)

---

## Investigation 1: Why `teams-for-linux` (Snap `v2.29.0`, rev `2718`) Failed to Share Screen

### Root Cause 1: Electron `desktopCapturer.getSources({ types: ['window', 'screen'] })` Deadlock on KDE Portal
In `/snap/teams-for-linux/2718/resources/app.asar`, `teams-for-linux` injects a preload script that calls:
```js
desktopCapturer.getSources({ types: ['window', 'screen'] })
```
Under Chromium/Electron's Wayland PipeWire capturer (`WebRTCPipeWireCapturer`), passing both `window` and `screen` causes Chromium to initiate **two sequential/competing `org.freedesktop.portal.ScreenCast` sessions** before the user even picks a source in the WebRTC UI. On KDE Plasma (`xdg-desktop-portal-kde`), this causes duplicate portal dialogs or an aborted PipeWire stream before `getUserMedia` completes.

*(Note: Upstream `teams-for-linux` PR #2266 / v2.30.0 removed this broken custom `desktopCapturer` flow in favor of Chromium's native `setDisplayMediaRequestHandler`.)*

### Root Cause 2: Missing `pipewire` Snap Interface & `core24` AppArmor Denial
Inspecting `snap connections teams-for-linux` and `/var/lib/snapd/apparmor/profiles/snap.teams-for-linux.teams-for-linux` revealed:
1. The `teams-for-linux` snap (`base: core24`) **does not declare the `pipewire` snap plug** in its `/snap/teams-for-linux/2718/meta/snap.yaml`.
2. Even when `org.freedesktop.portal.ScreenCast` hands back a PipeWire file descriptor (`OpenPipeWireRemote`), the `core24` sandbox AppArmor profile blocks direct socket access to `/run/user/1000/pipewire-0` and lacks the `core24` PipeWire compatibility libraries required to negotiate DMA-BUF / MemFD video frames with host PipeWire `1.6.2`.

### Root Cause 3: Default XWayland Launch inside Snap
Without `--ozone-platform=wayland`, the snap runs under XWayland (`DISPLAY=:0`), causing Chromium's X11 capturer to inspect the XWayland root window (which is black/empty on Wayland) whenever the portal fallback triggers.

---

## Investigation 2: Why the Chrome Teams PWA Missed Tray Badges & Meeting Notifications

Switching to the **Google Chrome Teams PWA** (`chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop`) solved screen sharing immediately because native `.deb` Google Chrome runs unsandboxed against host PipeWire `1.6.2` and `xdg-desktop-portal-kde`. However, it introduced notification and tray gaps:

### Root Cause 1: Missing `com.canonical.Unity.LauncherEntry` in Linux Chrome
- Teams Web dynamically updates both its document title (`(8) Chat | ...`) and calls the Badging API (`navigator.setAppBadge(8)`).
- Binary inspection of `/opt/google/chrome/chrome` confirmed `SetAppBadge` exists in Blink, **but `com.canonical.Unity.LauncherEntry` is completely absent from the Linux Chrome binary**.
- As a result, KDE Plasma's Task Manager (`org.kde.plasma.icontasks`) never receives badge counts from Chrome PWAs.
- Furthermore, the W3C Web App Manifest / Badging specification provides no API for web apps to create an `org.kde.StatusNotifierItem` icon in the KDE System Tray.

### Root Cause 2: No Background Web Push Registration (`gcm.push_messaging_application_id_map`)
- Inspecting `~/.config/google-chrome/Default/Preferences` showed that while `https://teams.cloud.microsoft:443,*` has `notifications.setting = 1` (Allowed), **`teams.cloud.microsoft` is not registered in `gcm.push_messaging_application_id_map`** (unlike `meet.google.com` and `web.whatsapp.com`).
- Consequently, Teams relies entirely on its active renderer WebSocket/long-polling connection. If the PWA window is closed, or if Chrome's Memory Saver freezes the background renderer, all notifications stop.

### Root Cause 3: Multi-Monitor Focus Suppression & KDE `kded6` Rate-Limiting
- On a 3-monitor KDE Wayland workspace, leaving the Teams PWA window open on a secondary monitor causes Teams to suppress desktop notifications when the window is considered visible/active or when status changes to *In a call / Presenting*.
- `journalctl --user` logs from `kded6` showed repeated:
  - `kf.notifications: Failed to notify "Created too many similar notifications in quick succession"`
  - `kf.notifications: Playing audio notification failed: File or data not found`
- Normal-urgency notifications from Chrome also auto-expire after 10 seconds (`PopupTimeout=10000` in `~/.config/plasmanotifyrc`), making meeting-start banners easy to miss across 3 monitors.

---

## Solution Architecture (`teams_pwa_companion.py`)

[teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py) bridges these gaps without modifying Chrome or requiring root privileges:

1. **KWin Wayland Window Introspection (`org.kde.KWin /WindowsRunner`)**:
   - Calls `org.kde.krunner1.Match("Microsoft Teams")` over the session D-Bus to read the real-time window title (`Microsoft Teams (PWA) - (8) Chat | ...`) and extract the unread count `(N)`.
   - Uses `org.kde.krunner1.Run(match_id, "")` to natively focus and raise the Wayland window when the user clicks the KDE system tray icon.
2. **KDE System Tray (`PyQt6.QtWidgets.QSystemTrayIcon`)**:
   - Registers a native `StatusNotifierItem` with `org.kde.StatusNotifierWatcher`.
   - Composites a high-contrast red unread count badge over the Teams PWA icon (`~/.local/share/icons/hicolor/128x128/apps/chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.png`).
3. **KDE Taskbar Badge Bridge (`com.canonical.Unity.LauncherEntry`)**:
   - Broadcasts `com.canonical.Unity.LauncherEntry.Update("application://chrome-ompifgpmddkgmclendfeacglnodjjndh-Default.desktop", {"count": N, "count-visible": True})` so KDE's taskbar icon displays the unread count natively.
4. **Three Distinct Sound Profiles (Call vs. Meeting Start vs. Chat Message)**:
   - **Why messages previously triggered the call ringtone**: When an in-app chat message sound plays (~0.3s), Chromium's `AudioService` keeps that PipeWire `Sink Input` stream in `Corked: no` (streaming silence) for ~10 seconds afterward.
   - [`teams_pwa_companion.py`](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py) tails Chrome's live Teams IndexedDB write-ahead log (`~/.config/google-chrome/Default/IndexedDB/https_teams.cloud.microsoft_0.indexeddb.leveldb/*.log`) alongside D-Bus notifications, window titles, and PipeWire streams to distinguish all three events:
     - **Incoming Call (`kind="call"`)**: Plays `/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga` (telephone ring), raises/focuses the Teams PWA window, and shows a persistent critical notification. Triggered only by call notifications or a sustained (`>= 4s`) uncorked Chrome audio stream when no message/meeting blip occurred in the last 12s and the microphone wasn't recently active.
     - **Meeting Started (`kind="meeting"`)**: Plays `/usr/share/sounds/freedesktop/stereo/service-login.oga` (melodic chime), raises/focuses the Teams PWA window, and shows a persistent critical notification when Teams writes a new `messageType="Event/Call"` + `threadType="meeting"` + `<meetingDetails>` record to IndexedDB (or posts a meeting-start notification).
     - **New Chat Message (`kind="message"`)**: Plays `/usr/share/sounds/freedesktop/stereo/dialog-information.oga` (a short ~0.15s blip) without stealing window focus, and marks current Chrome `Sink Input` streams as handled so Chrome's 10-second audio keep-alive never triggers the call ringtone.
