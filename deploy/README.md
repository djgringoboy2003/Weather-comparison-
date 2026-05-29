# Deploying the weather-compare web app

The web UI + JSON API ([`weather_compare/web.py`](../weather_compare/web.py)) runs
as a small systemd service on `127.0.0.1:3020` and is reverse-proxied by Apache
at `/weather/`. It has **no third-party dependencies** — just Python 3.9+.

## Files here

| File | Purpose |
|------|---------|
| `weather-compare.service` | systemd unit (runs as `www-data`, auto-restart, hardened) |
| `apache-weather.conf`     | Apache reverse-proxy block for `/weather/` |
| `deploy.sh`               | Package → upload → install unit → restart → health-check |

## First-time setup

1. **Apache modules** (once):
   ```bash
   a2enmod proxy proxy_http && systemctl reload apache2
   ```
2. **Reverse proxy** (once): paste the contents of `apache-weather.conf` into the
   site's HTTPS `<VirtualHost *:443>` (e.g. `rabmclean.online-le-ssl.conf`), then
   `apache2ctl configtest && systemctl reload apache2`.
3. **Deploy the code + service:**
   ```bash
   ./deploy/deploy.sh
   ```
   On Windows, point it at the native OpenSSH (so the ssh-agent key is used):
   ```bash
   SSH_BIN=/c/Windows/System32/OpenSSH/ssh.exe \
   SCP_BIN=/c/Windows/System32/OpenSSH/scp.exe ./deploy/deploy.sh
   ```

## Updates

Just re-run `./deploy/deploy.sh`. It re-syncs the package, reinstalls the unit,
restarts the service, and verifies `/health`.

## Config (env vars, set in the unit)

| Var | Default | Meaning |
|-----|---------|---------|
| `WEATHER_HOST` | `127.0.0.1` | bind address |
| `WEATHER_PORT` | `3020` | bind port |
| `WEATHER_USER_AGENT` | repo URL | identifying UA for upstream APIs (MET Norway needs a real contact) |

## Endpoints

- `GET /` — HTML UI
- `GET /api?location=Glasgow&days=7` (or `?lat=&lon=`) — JSON consensus
- `GET /suggest?q=gla` — location autocomplete
- `GET /health` — liveness probe

Forecasts are cached in-memory for 30 min and suggestions for 6 h; both endpoints
are rate-limited per client IP.
