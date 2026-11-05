# Gemini Agent Instructions (`GEMINI.md`)

See [AGENTS.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/AGENTS.md) and [NOTES.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/NOTES.md) for the complete architecture and root-cause analysis.

## Quick Context for Gemini
- **Target OS**: Kubuntu 26.04, KDE Plasma 6.6.6 Wayland (`kwin_wayland`), PipeWire `1.6.2`.
- **Active Service**: `teams-pwa-companion.service` (`~/.config/systemd/user/teams-pwa-companion.service`) runs [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py).
- **Important**: The systemd user unit points directly to `/home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py`. After any code edits to [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py), restart the service via `systemctl --user restart teams-pwa-companion.service` and verify its status.
- **Dependencies**: Uses system `/usr/bin/python3` (`PyQt6`, `dbus`). Keep the script self-contained and dependency-free outside standard Kubuntu packages.
