#!/usr/bin/env python3
"""
KDE Plasma System Tray, Taskbar Badge & Meeting Alert Companion for Microsoft Teams Chrome PWA.

Fixes three gaps in Google Chrome's Linux PWA implementation on Kubuntu / KDE Plasma Wayland:
1. Renders a live KDE System Tray icon (StatusNotifierItem) with a dynamic unread badge count
   and click-to-focus / launch support via KWin's Wayland DBus API.
2. Publishes com.canonical.Unity.LauncherEntry DBus signals so KDE's Icons-Only Task Manager
   displays the native unread count badge on the Teams PWA taskbar icon.
3. Watches both KWin window titles and Chrome desktop notifications from teams.cloud.microsoft
   to trigger persistent critical-urgency popups + PipeWire audio alerts when a meeting starts
   or new messages arrive.
"""

import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from PyQt6.QtCore import QRect, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

PWA_APP_ID = "ompifgpmddkgmclendfeacglnodjjndh"
PWA_DESKTOP_ID = f"chrome-{PWA_APP_ID}-Default.desktop"
PWA_DESKTOP_URI = f"application://{PWA_DESKTOP_ID}"
ICON_PATH = (
    Path.home()
    / f".local/share/icons/hicolor/128x128/apps/chrome-{PWA_APP_ID}-Default.png"
)
TEAMS_IDB_DIR = (
    Path.home()
    / ".config/google-chrome/Default/IndexedDB/https_teams.cloud.microsoft_0.indexeddb.leveldb"
)

UNREAD_RE = re.compile(r"\((\d+)\)")
MEETING_START_RE = re.compile(
    rb'searchKey".*?(19:meeting_[^"@]+@thread\.v2_\d+).*?messageType"\s*Event/Call"\s*threadType"\s*meeting".*?<meetingDetails>',
    re.DOTALL,
)

CALL_KEYWORDS = (
    "incoming call",
    "is calling",
    "calling you",
    "audio call",
    "video call",
    "call from",
    "answer",
    "accept call",
)
MEETING_KEYWORDS = (
    "meeting started",
    "join meeting",
    "started a meeting",
    "wants to join",
    "meeting now",
)

# 1. Incoming Call: Classic telephone ringtone
CALL_SOUND_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga",
    "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
]
# 2. Meeting Start: Pleasant melodic chime (distinct from phone ring and message blip)
MEETING_SOUND_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/service-login.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
]
# 3. Incoming Message: Short, subtle blip (~0.15s)
MESSAGE_SOUND_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
    "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
]


class UnityLauncherBridge(dbus.service.Object):
    """Emits com.canonical.Unity.LauncherEntry signals so KDE Task Manager shows unread badges."""

    def __init__(self, bus: dbus.SessionBus):
        super().__init__(bus, "/com/canonical/unity/launcherentry/teams_pwa")

    @dbus.service.signal(
        dbus_interface="com.canonical.Unity.LauncherEntry",
        signature="sa{sv}",
    )
    def Update(self, app_uri: str, properties: dict):
        pass

    def set_badge(self, count: int, urgent: bool = False):
        props = {
            "count": dbus.Int64(count),
            "count-visible": dbus.Boolean(count > 0),
            "urgent": dbus.Boolean(urgent),
        }
        self.Update(PWA_DESKTOP_URI, props)


class TeamsPwaCompanion:
    def __init__(self, app: QApplication):
        self.app = app
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.launcher_bridge = UnityLauncherBridge(self.bus)

        self.last_unread_count = -1
        self.last_window_title = ""
        self.last_match_id = None
        self.last_meeting_alert_time = 0.0
        self.last_mic_active_time = 0.0
        self.last_message_time = 0.0
        self.last_message_blip_time = 0.0
        self.handled_sink_ids: set[str] = set()
        self.candidate_sink_ticks: dict[str, int] = {}
        self.alerted_meetings: dict[str, float] = {}
        self.seen_idb_meetings: set[str] = set()
        self.idb_log_path: Path | None = None
        self.idb_log_offset: int = 0
        self._init_indexeddb_watcher()

        self.base_pixmap = self._load_base_pixmap()
        self.tray = QSystemTrayIcon()
        self.tray.setToolTip("Microsoft Teams (PWA) — Waiting for window...")
        self.tray.activated.connect(self._on_tray_activated)

        self.menu = QMenu()
        self.status_action = QAction("Teams PWA: Checking...", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        focus_action = QAction("Focus / Launch Teams PWA", self.menu)
        focus_action.triggered.connect(self.focus_or_launch_teams)
        self.menu.addAction(focus_action)

        self.auto_focus_action = QAction(
            "Auto-Focus Teams on Incoming Call / Meeting", self.menu
        )
        self.auto_focus_action.setCheckable(True)
        self.auto_focus_action.setChecked(True)
        self.menu.addAction(self.auto_focus_action)

        self.speaker_ring_action = QAction(
            "Play Alerts on Laptop Speakers + Headphones", self.menu
        )
        self.speaker_ring_action.setCheckable(True)
        self.speaker_ring_action.setChecked(True)
        self.menu.addAction(self.speaker_ring_action)

        self.menu.addSeparator()
        test_call_action = QAction("Test Incoming Call Alert (Ring)", self.menu)
        test_call_action.triggered.connect(self.send_test_call_alert)
        self.menu.addAction(test_call_action)

        test_meeting_action = QAction("Test Meeting Start Alert (Chime)", self.menu)
        test_meeting_action.triggered.connect(self.send_test_meeting_alert)
        self.menu.addAction(test_meeting_action)

        test_msg_action = QAction("Test New Message Blip", self.menu)
        test_msg_action.triggered.connect(self.send_test_message_blip)
        self.menu.addAction(test_msg_action)

        self.menu.addSeparator()
        quit_action = QAction("Quit Companion", self.menu)
        quit_action.triggered.connect(self.quit)
        self.menu.addAction(quit_action)

        self.tray.setContextMenu(self.menu)
        self._update_tray_icon(0)
        self.tray.show()

        self._setup_notification_monitor()

        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.poll_kwin_window)
        self.poll_timer.start(1000)
        self.poll_kwin_window()

    def _load_base_pixmap(self) -> QPixmap:
        if ICON_PATH.exists():
            pixmap = QPixmap(str(ICON_PATH))
            if not pixmap.isNull():
                return pixmap.scaled(
                    128,
                    128,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        pixmap = QPixmap(128, 128)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#5B5FC7"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(8, 8, 112, 112, 24, 24)
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Sans Serif", 64, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRect(0, 0, 128, 128), Qt.AlignmentFlag.AlignCenter, "T")
        painter.end()
        return pixmap

    def _update_tray_icon(self, unread_count: int):
        canvas = QPixmap(self.base_pixmap)
        if unread_count > 0:
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            badge_text = str(unread_count) if unread_count < 100 else "99+"
            badge_w = 58 if len(badge_text) == 1 else 74
            badge_h = 58
            rect = QRect(128 - badge_w - 2, 128 - badge_h - 2, badge_w, badge_h)

            painter.setBrush(QColor("#D92B2B"))
            painter.setPen(QColor("#FFFFFF"))
            painter.drawRoundedRect(rect, 28, 28)

            font = QFont("Sans Serif", 28 if len(badge_text) < 3 else 22, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, badge_text)
            painter.end()

        self.tray.setIcon(QIcon(canvas))

    def _setup_notification_monitor(self):
        """Listen for Chrome notifications from teams.cloud.microsoft via DBus eavesdropping."""
        try:
            self.bus.add_match_string(
                "type='method_call',interface='org.freedesktop.Notifications',member='Notify',eavesdrop='true'"
            )
            self.bus.add_message_filter(self._on_dbus_message)
        except Exception as exc:
            print(f"[teams-pwa-companion] Notification eavesdrop setup warning: {exc}", file=sys.stderr)

    def _init_indexeddb_watcher(self):
        """Initialize the Teams IndexedDB .log tailer at the current end-of-file so old events are ignored."""
        try:
            if not TEAMS_IDB_DIR.exists():
                return
            logs = sorted(TEAMS_IDB_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
            if not logs:
                return
            for log_file in logs:
                try:
                    data = log_file.read_bytes()
                    for m in MEETING_START_RE.finditer(data):
                        self.seen_idb_meetings.add(
                            m.group(1).decode("latin-1", errors="ignore")
                        )
                except Exception:
                    pass
            self.idb_log_path = logs[-1]
            self.idb_log_offset = self.idb_log_path.stat().st_size
        except Exception:
            pass

    def _poll_teams_indexeddb(self):
        """
        Tail Chrome's live Teams IndexedDB write-ahead log (*.log) to:
        1. Detect real-time Meeting Start events (messageType="Event/Call", threadType="meeting", <meetingDetails>)
           and play the distinct Meeting Start chime.
        2. Detect real-time Chat / Card message arrivals (including Snappy-compressed RichText/Html, preview,
           imdisplayname) so Chrome's in-app message blip stream is never misclassified by PipeWire as a call.
        """
        try:
            if not TEAMS_IDB_DIR.exists():
                return
            logs = sorted(TEAMS_IDB_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
            if not logs:
                return
            latest_log = logs[-1]
            if self.idb_log_path != latest_log:
                self.idb_log_path = latest_log
                self.idb_log_offset = 0

            size = latest_log.stat().st_size
            if size < self.idb_log_offset:
                self.idb_log_offset = 0
            if size == self.idb_log_offset:
                return

            read_start = max(0, self.idb_log_offset - 512)
            with latest_log.open("rb") as f:
                f.seek(read_start)
                chunk = f.read(size - read_start)
            new_bytes = chunk[self.idb_log_offset - read_start :]
            self.idb_log_offset = size

            now = time.time()

            # 1. Check for a newly started meeting (Event/Call + meeting + <meetingDetails>, without <ended/>)
            for m in MEETING_START_RE.finditer(chunk):
                raw_match = m.group(0)
                if b"<ended/>" in raw_match:
                    continue
                meeting_key = m.group(1).decode("latin-1", errors="ignore")
                if meeting_key not in self.seen_idb_meetings:
                    self.seen_idb_meetings.add(meeting_key)
                    self.last_message_time = now
                    self._trigger_persistent_meeting_alert(
                        "Microsoft Teams Meeting Started",
                        "A Teams meeting has started — switching focus to Microsoft Teams PWA.",
                        kind="meeting",
                        force=True,
                    )

            # 2. Check if a chat message / mention / card just arrived (handles Snappy block framing too)
            if any(
                marker in new_bytes
                for marker in (
                    b"RichT",
                    b"ext/Html",
                    b"Media_Card",
                    b"48:notifications",
                    b'preview"',
                    b'imdisplayname"',
                    b'imDisplayName"',
                )
            ):
                self.last_message_time = now
        except Exception:
            pass

    def _on_dbus_message(self, bus, message):
        if (
            message.get_interface() == "org.freedesktop.Notifications"
            and message.get_member() == "Notify"
        ):
            # Never react to notifications sent by our own D-Bus connection
            if message.get_sender() == self.bus.get_unique_name():
                return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

            args = message.get_args_list()
            if len(args) >= 7:
                app_name = str(args[0] or "")
                summary = str(args[3] or "")
                body = str(args[4] or "")
                raw_actions = list(args[5] or [])
                actions = " ".join(str(a) for a in raw_actions)
                hints = args[6] if isinstance(args[6], dict) else {}

                # Ignore notifications generated by this companion daemon
                if hints.get("x-teams-pwa-companion") or app_name == "Teams Companion":
                    return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

                # On KDE Plasma, Chrome places "teams.cloud.microsoft" in hints["x-kde-origin-name"]
                origin_name = str(hints.get("x-kde-origin-name") or "")
                desktop_entry = str(hints.get("desktop-entry") or "")
                combined = f"{summary}\n{body}\n{actions}\n{origin_name}\n{desktop_entry}".lower()

                is_teams = (
                    "teams.cloud.microsoft" in combined
                    or "teams.microsoft.com" in combined
                    or PWA_APP_ID in combined
                    or "microsoft teams" in app_name.lower()
                )
                if is_teams:
                    # NOTE: Do NOT check len(raw_actions) >= 4 here! Chrome passes 4 action strings
                    # (['default', 'Activate', 'settings', 'Notification settings']) on EVERY chat notification.
                    if any(k in combined for k in CALL_KEYWORDS):
                        self._trigger_persistent_meeting_alert(
                            summary, body, kind="call"
                        )
                    elif any(k in combined for k in MEETING_KEYWORDS):
                        self.last_message_time = time.time()
                        self._trigger_persistent_meeting_alert(
                            summary, body, kind="meeting"
                        )
                    else:
                        self.last_message_time = time.time()
                        self._play_sound(kind="message")
        return dbus.lowlevel.HANDLER_RESULT_NOT_YET_HANDLED

    def _is_chrome_mpris_playing(self) -> bool:
        """Check if non-Teams Chrome media (e.g. YouTube) is actively playing."""
        try:
            for name in self.bus.list_names():
                if (
                    name.startswith("org.mpris.MediaPlayer2.chromium")
                    or name == "org.kde.plasma.browser_integration"
                ):
                    try:
                        obj = self.bus.get_object(name, "/org/mpris/MediaPlayer2")
                        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                        status = str(
                            props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus")
                        )
                        if status.lower() == "playing":
                            meta = str(
                                props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
                            ).lower()
                            if "teams" not in meta:
                                return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def _is_sink_input_audible(self, sink_id: str) -> bool:
        """
        Sample ~25ms of PCM float32 audio from a specific PipeWire/PulseAudio Sink Input.
        Returns True only if the stream is actively outputting non-silent audio (peak >= 0.005).
        After playing a 0.2s chat message blip, Chrome keeps its Sink Input in `Corked: no`
        while outputting 0.0 digital silence — this check filters out silent keep-alive streams.
        """
        parec = shutil.which("parec")
        if not parec:
            return True
        proc = None
        try:
            proc = subprocess.Popen(
                [
                    parec,
                    f"--monitor-stream={sink_id}",
                    "--format=float32le",
                    "--rate=8000",
                    "--channels=1",
                    "--latency-msec=20",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            raw = proc.stdout.read(800) if proc.stdout else b""
            if len(raw) < 4:
                return False
            samples = struct.unpack(f"<{len(raw) // 4}f", raw[: (len(raw) // 4) * 4])
            peak = max((abs(s) for s in samples), default=0.0)
            return peak >= 0.005
        except Exception:
            return False
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.2)
                except Exception:
                    pass

    def _check_pipewire_incoming_call(self):
        """
        Detect when Teams PWA plays an in-app incoming call ringtone (when Teams suppresses
        OS desktop notifications because the Teams window is open on another monitor).
        Tracks individual Sink Input IDs so each incoming call stream alerts at most ONCE,
        and ignores:
          - Short in-app message notification blips (which drop to 0.0 amplitude after ~0.3s)
          - Streams that were active during a call (which Chrome keeps open after hanging up)
        """
        if not self.last_match_id:
            self.handled_sink_ids.clear()
            self.candidate_sink_ticks.clear()
            return

        pactl = shutil.which("pactl")
        if not pactl:
            return

        try:
            sink_out = subprocess.check_output(
                [pactl, "list", "sink-inputs"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            source_out = subprocess.check_output(
                [pactl, "list", "source-outputs"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
        except Exception:
            return

        # Collect all currently uncorked Google Chrome Sink Input IDs
        uncorked_chrome_sinks: set[str] = set()
        for block in sink_out.split("Sink Input #")[1:]:
            lines = block.splitlines()
            if not lines:
                continue
            sink_id = lines[0].strip()
            if (
                'application.name = "Google Chrome"' in block
                or 'node.name = "Google Chrome"' in block
            ) and "Corked: no" in block:
                uncorked_chrome_sinks.add(sink_id)

        # Prune closed/corked sink IDs from tracking sets
        self.handled_sink_ids.intersection_update(uncorked_chrome_sinks)
        for sid in list(self.candidate_sink_ticks.keys()):
            if sid not in uncorked_chrome_sinks:
                del self.candidate_sink_ticks[sid]

        # Check if Chrome is actively capturing the microphone (user is in an active call/meeting)
        chrome_mic_active = False
        for block in source_out.split("Source Output #")[1:]:
            if (
                "Google Chrome" in block
                or 'application.process.binary = "chrome"' in block
            ) and "Corked: no" in block:
                chrome_mic_active = True
                break

        now = time.time()
        if chrome_mic_active:
            self.last_mic_active_time = now

        # If currently in a call, within 15s after hanging up a call, within 12s of a message/meeting blip,
        # or playing non-Teams media: mark active Chrome audio streams as handled so they never ring!
        if (
            chrome_mic_active
            or (now - self.last_mic_active_time < 15.0)
            or (now - self.last_message_time < 12.0)
            or self._is_chrome_mpris_playing()
        ):
            self.handled_sink_ids.update(uncorked_chrome_sinks)
            self.candidate_sink_ticks.clear()
            return

        # Check for a sustained, audible Chrome audio stream (incoming call ringtone >= 3 consecutive ticks)
        for sink_id in uncorked_chrome_sinks:
            if sink_id in self.handled_sink_ids:
                continue
            if not self._is_sink_input_audible(sink_id):
                # Stream is silent (e.g. Chrome's idle keep-alive after a 0.2s message blip)
                if self.candidate_sink_ticks.get(sink_id, 0) > 0:
                    self.handled_sink_ids.add(sink_id)
                    self.candidate_sink_ticks.pop(sink_id, None)
                continue

            ticks = self.candidate_sink_ticks.get(sink_id, 0) + 1
            self.candidate_sink_ticks[sink_id] = ticks
            if ticks >= 3:
                # Mark all current Chrome streams as handled so we only alert ONCE for this call
                self.handled_sink_ids.update(uncorked_chrome_sinks)
                self.candidate_sink_ticks.clear()
                self._trigger_persistent_meeting_alert(
                    "Incoming Microsoft Teams Call",
                    "Teams is ringing — switching focus to Microsoft Teams PWA.",
                    kind="call",
                    force=True,
                )
                break

    def _find_teams_window(self) -> tuple[str | None, str | None]:
        """
        Discover the Microsoft Teams PWA window title and backend handle across Linux desktops:
        1. KDE Plasma (Wayland & X11): org.kde.KWin /WindowsRunner D-Bus
        2. X11 / XWayland (XFCE, Cinnamon, MATE, GNOME Xorg, i3): wmctrl / xdotool
        3. Sway / Hyprland Wayland compositors: swaymsg / hyprctl
        4. GNOME Wayland / Generic AT-SPI2 accessibility bus: gi.repository.Atspi
        """
        # 1. KDE Plasma KWin D-Bus
        try:
            runner = self.bus.get_object("org.kde.KWin", "/WindowsRunner")
            iface = dbus.Interface(runner, "org.kde.krunner1")
            matches = iface.Match("Microsoft Teams")
            pwa_title = None
            pwa_match_id = None
            for match in matches:
                text = str(match[1])
                match_id = str(match[0])
                if "Microsoft Teams" in text:
                    pwa_title = text
                    pwa_match_id = f"kwin:{match_id}"
                    if "(PWA)" in text:
                        break
            if pwa_title:
                return pwa_title, pwa_match_id
        except Exception:
            pass

        # 2. X11 / XWayland via wmctrl
        wmctrl = shutil.which("wmctrl")
        if wmctrl:
            try:
                out = subprocess.check_output(
                    [wmctrl, "-l"], text=True, stderr=subprocess.DEVNULL, timeout=1.0
                )
                for line in out.splitlines():
                    if "Microsoft Teams" in line:
                        parts = line.split(None, 3)
                        if len(parts) >= 4:
                            return parts[3], f"wmctrl:{parts[0]}"
            except Exception:
                pass

        # 3. X11 / XWayland via xdotool
        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                out = subprocess.check_output(
                    [xdotool, "search", "--name", "Microsoft Teams"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                for win_id in out.splitlines():
                    win_id = win_id.strip()
                    if win_id:
                        title = subprocess.check_output(
                            [xdotool, "getwindowname", win_id],
                            text=True,
                            stderr=subprocess.DEVNULL,
                            timeout=1.0,
                        ).strip()
                        if "Microsoft Teams" in title:
                            return title, f"xdotool:{win_id}"
            except Exception:
                pass

        # 4. Hyprland Wayland compositor
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            try:
                import json

                out = subprocess.check_output(
                    ["hyprctl", "clients", "-j"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                for client in json.loads(out):
                    title = str(client.get("title") or "")
                    if "Microsoft Teams" in title:
                        addr = str(client.get("address") or "")
                        return title, f"hyprland:{addr}"
            except Exception:
                pass

        # 5. Sway Wayland compositor
        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            try:
                import json

                out = subprocess.check_output(
                    ["swaymsg", "-t", "get_tree"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                m = re.search(r'"name":\s*"([^"]*Microsoft Teams[^"]*)"', out)
                if m:
                    return m.group(1), "sway:teams"
            except Exception:
                pass

        # 6. GNOME Wayland / Generic AT-SPI2 Accessibility fallback
        if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
            try:
                import gi

                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi

                desktop = Atspi.get_desktop(0)
                for i in range(desktop.get_child_count()):
                    app = desktop.get_child_at_index(i)
                    if app and "chrome" in (app.get_name() or "").lower():
                        for j in range(app.get_child_count()):
                            win = app.get_child_at_index(j)
                            name = (win.get_name() or "") if win else ""
                            if "Microsoft Teams" in name:
                                clean = re.split(r" - High memory usage", name)[0]
                                return clean, "atspi:teams"
            except Exception:
                pass

        return None, None

    def poll_kwin_window(self):
        """Poll the active desktop window manager for the live Chrome Teams PWA title."""
        pwa_title, pwa_match_id = self._find_teams_window()

        if not pwa_title:
            if self.last_unread_count != 0:
                self.last_unread_count = 0
                self._update_tray_icon(0)
                self.launcher_bridge.set_badge(0)
            self.last_match_id = None
            self.handled_sink_ids.clear()
            self.candidate_sink_ticks.clear()
            self.status_action.setText("Teams PWA: Window not open")
            self.tray.setToolTip("Microsoft Teams (PWA) — Not running (click to launch)")
            return

        self.last_match_id = pwa_match_id

        # 1. Poll IndexedDB first so message arrivals or meeting starts update self.last_message_time
        #    before PipeWire checks active Chrome audio streams.
        self._poll_teams_indexeddb()

        # 2. Check window title unread badge count (N)
        m = UNREAD_RE.search(pwa_title)
        unread_count = int(m.group(1)) if m else 0

        if unread_count != self.last_unread_count or pwa_title != self.last_window_title:
            increased = self.last_unread_count >= 0 and unread_count > self.last_unread_count
            self.last_unread_count = unread_count
            self.last_window_title = pwa_title

            self._update_tray_icon(unread_count)
            self.launcher_bridge.set_badge(unread_count, urgent=increased)

            clean_title = pwa_title.replace("Microsoft Teams (PWA) - ", "")
            self.status_action.setText(f"Unread: {unread_count} | {clean_title[:48]}")
            self.tray.setToolTip(
                f"Microsoft Teams (PWA)\nUnread: {unread_count}\n{clean_title}"
            )

            if increased:
                self.last_message_time = time.time()
                self._play_sound(kind="message")
                self._send_desktop_notification(
                    summary=f"Teams: {unread_count} unread message(s)",
                    body=clean_title,
                    critical=False,
                )

            lower_title = clean_title.lower()
            if any(k in lower_title for k in CALL_KEYWORDS):
                self._trigger_persistent_meeting_alert(
                    "Incoming Microsoft Teams Call", clean_title, kind="call"
                )
            elif any(k in lower_title for k in MEETING_KEYWORDS):
                self.last_message_time = time.time()
                self._trigger_persistent_meeting_alert(
                    "Microsoft Teams Meeting Started", clean_title, kind="meeting"
                )

        # 3. Check PipeWire for sustained incoming-call ringtone
        self._check_pipewire_incoming_call()

    def _trigger_persistent_meeting_alert(
        self,
        summary: str,
        body: str,
        kind: str = "call",
        force: bool = False,
    ):
        now = time.time()
        clean_summary = summary.lstrip("📞📅💬 ").strip()
        key = f"{kind}:{clean_summary}:{body}"
        if not force:
            if now - self.last_meeting_alert_time < 15.0:
                return
            if now - self.alerted_meetings.get(key, 0) < 45.0:
                return
        self.last_meeting_alert_time = now
        self.alerted_meetings[key] = now

        prefix = "📅" if kind == "meeting" else "📞"
        default_title = (
            "Microsoft Teams Meeting Started"
            if kind == "meeting"
            else "Incoming Microsoft Teams Call"
        )
        self.launcher_bridge.set_badge(max(1, self.last_unread_count), urgent=True)
        self._play_sound(kind=kind)
        self._send_desktop_notification(
            summary=f"{prefix} {clean_summary or default_title}",
            body=body or "A Microsoft Teams call or meeting requires your attention.",
            critical=True,
        )
        if self.auto_focus_action.isChecked():
            self.focus_or_launch_teams()

    def _send_desktop_notification(self, summary: str, body: str, critical: bool = False):
        try:
            notif_obj = self.bus.get_object(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            )
            notif_iface = dbus.Interface(notif_obj, "org.freedesktop.Notifications")
            hints = {
                "urgency": dbus.Byte(2 if critical else 1),
                "desktop-entry": dbus.String(PWA_DESKTOP_ID.removesuffix(".desktop")),
                "x-teams-pwa-companion": dbus.Boolean(True),
            }
            timeout = 0 if critical else 8000
            notif_iface.Notify(
                "Teams Companion",
                dbus.UInt32(0),
                str(ICON_PATH) if ICON_PATH.exists() else "preferences-system-network",
                summary,
                body,
                dbus.Array([], signature="s"),
                dbus.Dictionary(hints, signature="sv"),
                dbus.Int32(timeout),
            )
        except Exception as exc:
            print(f"[teams-pwa-companion] Failed to send notification: {exc}", file=sys.stderr)

    def _get_audio_sinks(self) -> list[str]:
        """Return all PipeWire/PulseAudio output sink names (e.g. laptop speakers + USB headset)."""
        pactl = shutil.which("pactl")
        if not pactl:
            return []
        try:
            out = subprocess.check_output(
                [pactl, "list", "sinks", "short"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            sinks = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].startswith("alsa_output."):
                    sinks.append(parts[1])
            return sinks
        except Exception:
            return []

    def _play_sound(self, kind: str = "message"):
        """
        Play one of three distinct sound profiles across all active audio sinks:
        - kind="call":    phone-incoming-call.oga (telephone ringtone)
        - kind="meeting": service-login.oga       (melodic meeting start chime)
        - kind="message": dialog-information.oga  (short ~0.15s message blip)
        """
        now = time.time()
        if kind == "message":
            if now - self.last_message_blip_time < 2.0:
                return
            self.last_message_blip_time = now
        pw_play = shutil.which("pw-play")
        paplay = shutil.which("paplay")
        if not pw_play and not paplay:
            return

        if kind == "call":
            candidates = CALL_SOUND_CANDIDATES
        elif kind == "meeting":
            candidates = MEETING_SOUND_CANDIDATES
        else:
            candidates = MESSAGE_SOUND_CANDIDATES

        chosen_sound = next((s for s in candidates if os.path.exists(s)), None)
        if not chosen_sound:
            return

        sinks = self._get_audio_sinks() if self.speaker_ring_action.isChecked() else []
        if sinks:
            for sink in sinks:
                cmd = (
                    [pw_play, f"--target={sink}", chosen_sound]
                    if pw_play
                    else [paplay, f"--device={sink}", chosen_sound]
                )
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            player = pw_play or paplay
            subprocess.Popen(
                [player, chosen_sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _find_chrome_binary(self) -> str:
        for candidate in (
            "/opt/google/chrome/google-chrome",
            "google-chrome-stable",
            "google-chrome",
            "chromium-browser",
            "chromium",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return "/opt/google/chrome/google-chrome"

    def focus_or_launch_teams(self):
        # 1. Try KDE KWin WindowsRunner D-Bus
        try:
            runner = self.bus.get_object("org.kde.KWin", "/WindowsRunner")
            iface = dbus.Interface(runner, "org.kde.krunner1")
            matches = iface.Match("Microsoft Teams")
            target_id = (
                self.last_match_id.removeprefix("kwin:")
                if self.last_match_id and self.last_match_id.startswith("kwin:")
                else None
            )
            for match in matches:
                text = str(match[1])
                if "Microsoft Teams" in text:
                    target_id = str(match[0])
                    if "(PWA)" in text:
                        break
            if target_id:
                iface.Run(target_id, "")
                return
        except Exception:
            pass

        # 2. Try X11 wmctrl / xdotool activation
        if self.last_match_id and self.last_match_id.startswith("wmctrl:"):
            win_id = self.last_match_id.removeprefix("wmctrl:")
            wmctrl = shutil.which("wmctrl")
            if wmctrl:
                subprocess.Popen(
                    [wmctrl, "-i", "-a", win_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
        elif self.last_match_id and self.last_match_id.startswith("xdotool:"):
            win_id = self.last_match_id.removeprefix("xdotool:")
            xdotool = shutil.which("xdotool")
            if xdotool:
                subprocess.Popen(
                    [xdotool, "windowactivate", win_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return

        # 3. Try Hyprland / Sway native focus commands
        if self.last_match_id and self.last_match_id.startswith("hyprland:"):
            addr = self.last_match_id.removeprefix("hyprland:")
            subprocess.Popen(
                ["hyprctl", "dispatch", "focuswindow", f"address:{addr}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        elif self.last_match_id == "sway:teams":
            subprocess.Popen(
                ["swaymsg", '[title="Microsoft Teams"] focus'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        # 4. Universal fallback (GNOME Wayland + launching when closed):
        # Re-invoking Chrome with --app-id focuses the existing PWA window via xdg_activation_v1
        subprocess.Popen(
            [
                self._find_chrome_binary(),
                "--profile-directory=Default",
                f"--app-id={PWA_APP_ID}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.focus_or_launch_teams()

    def send_test_call_alert(self):
        self._trigger_persistent_meeting_alert(
            "Incoming Microsoft Teams Call",
            "Test incoming call ringtone + window focus.",
            kind="call",
            force=True,
        )

    def send_test_meeting_alert(self):
        self._trigger_persistent_meeting_alert(
            "Meeting started: Weekly Sync",
            "Test meeting start chime + window focus.",
            kind="meeting",
            force=True,
        )

    def send_test_message_blip(self):
        self.last_message_time = time.time()
        self._play_sound(kind="message")
        self._send_desktop_notification(
            summary="💬 Teams: New chat message",
            body="Test short message blip (no ringing, no window focus jump).",
            critical=False,
        )

    def quit(self):
        self.launcher_bridge.set_badge(0, urgent=False)
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Teams PWA Companion")
    _companion = TeamsPwaCompanion(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
