#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPANION_SCRIPT="${SCRIPT_DIR}/teams_pwa_companion.py"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/teams-pwa-companion.service"

chmod +x "${COMPANION_SCRIPT}"
mkdir -p "${SERVICE_DIR}"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=System Tray, Unread Badge & Meeting Alert Companion for Microsoft Teams PWA
After=graphical-session.target plasma-workspace.target plasma-plasmashell.service org.gnome.Shell@wayland.service org.gnome.Shell@x11.service
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${COMPANION_SCRIPT}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
EOF

# Remove duplicate Chrome PWA autostart entry if present
if [[ -f "${HOME}/.config/autostart/chrome-ompifgpmddkgmclendfeacglnodjjndh-Default_1_.desktop" ]]; then
    rm -f "${HOME}/.config/autostart/chrome-ompifgpmddkgmclendfeacglnodjjndh-Default_1_.desktop"
    echo "Removed duplicate autostart entry: chrome-ompifgpmddkgmclendfeacglnodjjndh-Default_1_.desktop"
fi

systemctl --user daemon-reload
systemctl --user enable --now teams-pwa-companion.service
echo "teams-pwa-companion.service is now enabled and running!"
systemctl --user status teams-pwa-companion.service --no-pager
