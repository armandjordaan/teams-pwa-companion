# Claude Code Instructions (`CLAUDE.md`)

See [AGENTS.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/AGENTS.md) and [NOTES.md](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/NOTES.md) for full technical details and D-Bus architecture.

## Quick Reference
- **Daemon Script**: [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py)
- **Installer**: [install.sh](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/install.sh)
- **Systemd Service**: `~/.config/systemd/user/teams-pwa-companion.service`

## Development Workflow
- Run `./install.sh` or `systemctl --user restart teams-pwa-companion.service` after editing [teams_pwa_companion.py](file:///home/ajordaan/Documents/teams_for_linux_not_sharing_screen/teams_pwa_companion.py).
- Do not introduce external PyPI packages; the service executes `/usr/bin/python3` with system `PyQt6` and `dbus-python`.
- Use `org.kde.KWin /WindowsRunner` (`org.kde.krunner1`) for Wayland-compatible window title inspection and window activation.
