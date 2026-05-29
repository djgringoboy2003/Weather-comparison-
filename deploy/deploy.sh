#!/usr/bin/env bash
#
# Deploy weather-compare to the VPS: sync the package, (re)install the systemd
# unit, restart the service, and health-check it.
#
# Usage:
#   ./deploy/deploy.sh
#
# Environment overrides:
#   WEATHER_SERVER   ssh target            (default root@213.171.210.251)
#   WEATHER_DEST     install dir on server (default /opt/weather-compare)
#   SSH_BIN / SCP_BIN
#                    ssh/scp binaries to use. On Windows set these to the
#                    native OpenSSH so the passphrase key in ssh-agent is used:
#                      SSH_BIN=/c/Windows/System32/OpenSSH/ssh.exe \
#                      SCP_BIN=/c/Windows/System32/OpenSSH/scp.exe ./deploy/deploy.sh
#
# The Apache reverse-proxy (deploy/apache-weather.conf) is a one-time manual
# step; see deploy/README.md.
set -euo pipefail

SERVER="${WEATHER_SERVER:-root@213.171.210.251}"
DEST="${WEATHER_DEST:-/opt/weather-compare}"
SSH="${SSH_BIN:-ssh}"
SCP="${SCP_BIN:-scp}"

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> Packaging app from $root"
tar --exclude='__pycache__' -czf "$tmp/app.tgz" \
    -C "$root" weather_compare pyproject.toml README.md

echo "==> Uploading to $SERVER:$DEST"
"$SSH" "$SERVER" "mkdir -p '$DEST'"
"$SCP" "$tmp/app.tgz" "$SERVER:$DEST/app.tgz"
"$SCP" "$root/deploy/weather-compare.service" \
       "$SERVER:/etc/systemd/system/weather-compare.service"

echo "==> Installing and restarting service"
"$SSH" "$SERVER" "set -e; cd '$DEST'; tar -xzf app.tgz; rm app.tgz; \
  chown -R root:root '$DEST'; \
  systemctl daemon-reload; systemctl enable --now weather-compare; \
  systemctl restart weather-compare; sleep 1; \
  systemctl is-active weather-compare; \
  curl -fsS http://127.0.0.1:3020/health && echo ' <- health OK'"

echo "==> Done."
