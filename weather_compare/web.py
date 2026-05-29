"""A tiny standard-library web server for the weather consensus tool.

Exposes the same logic the CLI uses (:func:`geocode`, :func:`fetch_all`,
:func:`build_consensus`, :func:`to_dict`) over HTTP:

* ``GET  /``        -> a self-contained HTML page (search box + results).
* ``GET  /api``     -> JSON consensus for ``?location=&days=&no_metno=``.
* ``GET  /health``  -> ``ok`` (for monitoring / the systemd unit).

It is dependency-free on purpose and designed to sit behind an Apache reverse
proxy, e.g. ``ProxyPass /weather/ http://127.0.0.1:3020/``. Every front-end URL
is relative, so the app works no matter what path it is mounted under.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .compare import build_consensus
from .geocode import GEOCODE_URL, LocationNotFound, geocode
from .http import HTTPError, get_json
from .render import to_dict
from .sources import fetch_all

MAX_DAYS = 16
MAX_SUGGESTIONS = 8


def suggest_locations(query: str) -> list[dict]:
    """Return up to :data:`MAX_SUGGESTIONS` place suggestions for ``query``.

    Thin wrapper over the Open-Meteo geocoding API used to power the search
    box's autocomplete. Returns ``[{"label", "value", "country"}]``; an empty
    list on any error or short query so the UI degrades gracefully.
    """
    query = query.strip()
    if len(query) < 2:
        return []
    try:
        data = get_json(
            GEOCODE_URL,
            {"name": query, "count": MAX_SUGGESTIONS, "language": "en", "format": "json"},
        )
    except HTTPError:
        return []

    out: list[dict] = []
    for r in data.get("results") or []:
        name = r.get("name")
        if not name:
            continue
        parts = [name]
        admin1 = r.get("admin1")
        country = r.get("country")
        if admin1 and admin1 != name:
            parts.append(admin1)
        if country:
            parts.append(country)
        label = ", ".join(parts)
        out.append({"label": label, "value": label, "country": country})
    return out


def build_payload(location_query: str, days: int, include_metno: bool) -> dict:
    """Run the full pipeline and return a JSON-serialisable consensus payload.

    Raises :class:`LocationNotFound` if the place can't be resolved, or
    :class:`RuntimeError` if no weather sources were reachable.
    """
    days = max(1, min(days, MAX_DAYS))
    location = geocode(location_query)

    warnings: list[str] = []
    sources = fetch_all(
        location,
        days=days,
        include_metno=include_metno,
        on_error=lambda name, exc: warnings.append(f"{name}: {exc}"),
    )
    if not sources:
        raise RuntimeError(
            "no weather sources were reachable: " + "; ".join(warnings)
        )

    consensus = build_consensus(sources)[:days]
    payload = to_dict(location, sources, consensus)
    payload["warnings"] = warnings
    payload["generated_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "weather-compare/1.0"
    protocol_version = "HTTP/1.1"

    # --- helpers ---------------------------------------------------------- #
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Always revalidate: the page and data are dynamic, and caching the HTML
        # would otherwise serve a stale UI after a deploy.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8")

    # --- routing ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/":
            self._send(200, INDEX_HTML.encode("utf-8"),
                       "text/html; charset=utf-8")
        elif path == "/health":
            self._send(200, b"ok", "text/plain; charset=utf-8")
        elif path == "/api":
            self._handle_api()
        elif path == "/suggest":
            self._handle_suggest()
        else:
            self._send_json(404, {"error": "not found"})

    do_HEAD = do_GET

    def _handle_api(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        location = (qs.get("location") or [""])[0].strip()
        if not location:
            self._send_json(400, {"error": "missing 'location' parameter"})
            return

        try:
            days = int((qs.get("days") or ["7"])[0])
        except ValueError:
            self._send_json(400, {"error": "'days' must be an integer"})
            return

        include_metno = (qs.get("no_metno") or ["0"])[0] not in ("1", "true", "yes")

        try:
            payload = build_payload(location, days, include_metno)
        except LocationNotFound as exc:
            self._send_json(404, {"error": str(exc)})
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})
        except Exception as exc:  # defensive: never 500 with a stack trace
            self._send_json(500, {"error": f"internal error: {exc}"})
        else:
            self._send_json(200, payload)

    def _handle_suggest(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        query = (qs.get("q") or [""])[0]
        try:
            results = suggest_locations(query)
        except Exception:  # defensive: autocomplete must never break the page
            results = []
        self._send_json(200, {"results": results})

    def log_message(self, fmt: str, *args) -> None:
        # Concise single-line logging to stderr (captured by systemd/journald).
        super().log_message(fmt, *args)


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"weather-compare web server listening on http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="weather-compare-web",
        description="Serve the weather consensus tool as a web UI + JSON API.",
    )
    p.add_argument("--host", default=os.environ.get("WEATHER_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("WEATHER_PORT", "3020")))
    args = p.parse_args(argv)
    serve(args.host, args.port)
    return 0


# --------------------------------------------------------------------------- #
# Front-end: a single self-contained page. Kept inline so the server has no
# static-file dependencies. All fetch/asset URLs are relative ("api?...") so it
# works under any mount path (e.g. https://host/weather/).
# --------------------------------------------------------------------------- #
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weather Consensus</title>
<style>
  :root{
    --bg:#0f172a; --panel:#1e293b; --panel2:#334155; --ink:#e2e8f0;
    --muted:#94a3b8; --accent:#38bdf8; --good:#34d399; --mid:#fbbf24; --low:#f87171;
    --border:#334155;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:linear-gradient(160deg,#0f172a,#1e293b);color:var(--ink);min-height:100vh}
  .wrap{max-width:1000px;margin:0 auto;padding:32px 20px 64px}
  h1{font-size:1.8rem;margin:0 0 4px;display:flex;align-items:center;gap:10px}
  .sub{color:var(--muted);margin:0 0 24px;font-size:.95rem}
  form{display:flex;flex-wrap:wrap;gap:12px;align-items:end;background:var(--panel);
    padding:18px;border-radius:14px;border:1px solid var(--border)}
  .field{display:flex;flex-direction:column;gap:6px}
  .field label{font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
  input[type=text],select{background:#0b1220;border:1px solid var(--border);color:var(--ink);
    padding:10px 12px;border-radius:9px;font-size:1rem;outline:none}
  input[type=text]{min-width:240px}
  input[type=text]:focus,select:focus{border-color:var(--accent)}
  .grow{flex:1 1 240px}
  .row-mini{display:flex;gap:12px;align-items:end}
  .ac{position:relative}
  .ac input{width:100%}
  #sugg{list-style:none;margin:6px 0 0;padding:4px;position:absolute;z-index:20;left:0;right:0;
    background:#0b1220;border:1px solid var(--border);border-radius:9px;max-height:280px;overflow:auto;
    box-shadow:0 12px 30px rgba(0,0,0,.45)}
  #sugg li{padding:9px 11px;border-radius:6px;cursor:pointer;font-size:.95rem}
  #sugg li:hover,#sugg li.active{background:var(--panel2)}
  #sugg li .c{color:var(--muted);font-size:.78rem;margin-left:6px}
  button{background:var(--accent);color:#04212e;border:0;padding:11px 20px;border-radius:9px;
    font-size:1rem;font-weight:600;cursor:pointer}
  button:disabled{opacity:.6;cursor:default}
  .chk{display:flex;align-items:center;gap:8px;font-size:.85rem;color:var(--muted)}
  #status{margin:20px 0;color:var(--muted)}
  #status.error{color:var(--low)}
  .meta{margin:22px 0 10px}
  .meta h2{margin:0;font-size:1.25rem}
  .meta .coords{color:var(--muted);font-size:.85rem;margin-top:2px}
  .srcs{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .srcs span{background:var(--panel2);border-radius:999px;padding:3px 11px;font-size:.78rem;color:var(--ink)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-top:14px}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:14px}
  .card .day{font-weight:700;font-size:1.05rem}
  .card .date{color:var(--muted);font-size:.78rem;margin-bottom:8px}
  .card .cond{font-size:.92rem;margin-bottom:10px;min-height:2.6em}
  .temps{display:flex;align-items:baseline;gap:8px}
  .temps .hi{font-size:1.7rem;font-weight:700}
  .temps .lo{color:var(--muted)}
  .row{display:flex;justify-content:space-between;font-size:.82rem;color:var(--muted);margin-top:6px}
  .row b{color:var(--ink);font-weight:600}
  .conf{margin-top:10px;font-size:.78rem;display:flex;align-items:center;gap:6px}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
  .conf.High .dot{background:var(--good)} .conf.Medium .dot{background:var(--mid)} .conf.Low .dot{background:var(--low)}
  .warn{margin-top:18px;font-size:.8rem;color:var(--mid)}
  footer{margin-top:40px;color:var(--muted);font-size:.78rem;text-align:center}
  a{color:var(--accent)}
  /* share controls */
  .actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
  .actions button{background:var(--panel2);color:var(--ink);font-weight:600;font-size:.9rem;
    padding:10px 16px;border-radius:9px;display:inline-flex;align-items:center;gap:7px}
  .actions button.primary{background:var(--accent);color:#04212e}
  #toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);
    background:#0b1220;border:1px solid var(--border);color:var(--ink);padding:11px 18px;
    border-radius:10px;font-size:.9rem;opacity:0;pointer-events:none;transition:.25s;z-index:50;
    box-shadow:0 10px 30px rgba(0,0,0,.5)}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  /* mobile first: comfortable tap targets, then enhance on wider screens */
  @media (max-width:560px){
    .wrap{padding:18px 13px 48px}
    h1{font-size:1.4rem}
    .sub{font-size:.88rem}
    form{padding:13px;gap:10px}
    .field{flex:1 1 100%}
    .row-mini{display:flex;gap:10px;width:100%}
    .row-mini .field{flex:1 1 0}
    input[type=text],select,button{width:100%;font-size:1rem;padding:12px}
    #go{width:100%}
    .grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
    .actions button{flex:1 1 auto;justify-content:center}
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>🌤️ Weather Consensus</h1>
  <p class="sub">A skill-weighted ensemble of up to 6 independent forecast models (ECMWF, GFS, ICON, GEM, Météo-France, MET Norway). Tighter agreement between models means higher confidence.</p>

  <form id="f">
    <div class="field grow">
      <label for="loc">Location</label>
      <div class="ac">
        <input type="text" id="loc" placeholder="e.g. Glasgow, Tokyo, Paris France"
               autocomplete="off" autocapitalize="off" spellcheck="false" autofocus
               role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="sugg">
        <ul id="sugg" role="listbox" hidden></ul>
      </div>
    </div>
    <div class="row-mini">
      <div class="field">
        <label for="days">Days</label>
        <select id="days">
          <option>3</option><option>5</option><option selected>7</option>
          <option>10</option><option>14</option><option>16</option>
        </select>
      </div>
      <div class="field">
        <label for="units">Units</label>
        <select id="units"><option value="metric">°C / mm</option><option value="imperial">°F / in</option></select>
      </div>
    </div>
    <button type="submit" id="go">Forecast</button>
  </form>

  <div id="status"></div>
  <div id="result"></div>
  <div id="toast"></div>

  <footer>
    JSON API: <code>api?location=Glasgow&amp;days=7</code> ·
    Data from open-meteo.com &amp; met.no
  </footer>
</div>

<script>
const f = document.getElementById('f');
const statusEl = document.getElementById('status');
const result = document.getElementById('result');
const go = document.getElementById('go');
const loc = document.getElementById('loc');
const sugg = document.getElementById('sugg');

// --- location autocomplete -------------------------------------------------
let acItems = [];      // current suggestion labels
let acActive = -1;     // highlighted index for keyboard nav
let acTimer = null;    // debounce timer
let acSeq = 0;         // request sequence guard against out-of-order responses

function closeAc(){ sugg.hidden = true; sugg.innerHTML = ''; acItems = []; acActive = -1; loc.setAttribute('aria-expanded','false'); }

function renderAc(items){
  acItems = items; acActive = -1;
  if (!items.length){ closeAc(); return; }
  sugg.innerHTML = items.map((it,i) =>
    `<li role="option" data-i="${i}">${it.label.replace(/</g,'&lt;')}</li>`).join('');
  sugg.hidden = false;
  loc.setAttribute('aria-expanded','true');
}

function pick(i){ if (acItems[i]){ loc.value = acItems[i].value; closeAc(); loc.focus(); } }

async function fetchSuggestions(q){
  const seq = ++acSeq;
  try {
    const r = await fetch('suggest?q=' + encodeURIComponent(q));
    const data = await r.json();
    if (seq !== acSeq) return;          // a newer keystroke superseded this one
    renderAc(data.results || []);
  } catch { closeAc(); }
}

loc.addEventListener('input', () => {
  const q = loc.value.trim();
  clearTimeout(acTimer);
  if (q.length < 2){ closeAc(); return; }
  acTimer = setTimeout(() => fetchSuggestions(q), 180);
});

loc.addEventListener('keydown', e => {
  if (sugg.hidden || !acItems.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp'){
    e.preventDefault();
    acActive = (acActive + (e.key === 'ArrowDown' ? 1 : -1) + acItems.length) % acItems.length;
    [...sugg.children].forEach((li,i) => li.classList.toggle('active', i === acActive));
  } else if (e.key === 'Enter' && acActive >= 0){
    e.preventDefault(); pick(acActive);
  } else if (e.key === 'Escape'){ closeAc(); }
});

sugg.addEventListener('mousedown', e => {        // mousedown so it fires before input blur
  const li = e.target.closest('li'); if (li){ e.preventDefault(); pick(+li.dataset.i); }
});
document.addEventListener('click', e => { if (!e.target.closest('.ac')) closeAc(); });

const cToF = c => c == null ? null : c * 9/5 + 32;
const mmToIn = mm => mm == null ? null : mm / 25.4;
const wd = d => { const x = new Date(d + 'T00:00:00'); return isNaN(x) ? '?' : x.toLocaleDateString(undefined,{weekday:'short'}); };

let last = null;   // {data, imperial, query, days} of the current view, for sharing

f.addEventListener('submit', async e => {
  e.preventDefault();
  closeAc();
  const query = loc.value.trim();
  if (!query) return;
  const days = document.getElementById('days').value;
  const imperial = document.getElementById('units').value === 'imperial';
  go.disabled = true;
  statusEl.className = '';
  statusEl.textContent = 'Fetching ' + query + ' over ' + days + ' days across models…';
  result.innerHTML = '';
  try {
    const r = await fetch('api?location=' + encodeURIComponent(query) + '&days=' + days);
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    last = {data, imperial, query, days};
    render(data, imperial);
    // Make the current view shareable / bookmarkable without a reload.
    history.replaceState(null, '', shareUrl());
    statusEl.textContent = '';
  } catch (err) {
    statusEl.className = 'error';
    statusEl.textContent = 'Error: ' + err.message;
  } finally {
    go.disabled = false;
  }
});

function render(data, imperial){
  const ut = imperial ? '°F' : '°C';
  const up = imperial ? 'in' : 'mm';
  const t = c => c == null ? '–' : Math.round(imperial ? cToF(c) : c) + '°';
  const p = mm => mm == null ? '–' : (imperial ? mmToIn(mm).toFixed(2) : mm.toFixed(1)) + ' ' + up;

  const cards = data.forecast.map(d => `
    <div class="card">
      <div class="day">${wd(d.date)}</div>
      <div class="date">${d.date}</div>
      <div class="cond">${d.condition}</div>
      <div class="temps"><span class="hi">${t(d.temp_max_c)}</span><span class="lo">${t(d.temp_min_c)}</span></div>
      <div class="row"><span>Rain chance</span><b>${d.precip_chance_pct == null ? '–' : d.precip_chance_pct + '%'}</b></div>
      <div class="row"><span>Precip</span><b>${p(d.precip_mm)}</b></div>
      <div class="row"><span>Model spread</span><b>${d.temp_max_spread_c == null ? '–' : '±' + (imperial ? (d.temp_max_spread_c*9/5).toFixed(1) : d.temp_max_spread_c.toFixed(1)) + ut}</b></div>
      <div class="conf ${d.confidence}"><span class="dot"></span>${d.confidence} confidence · ${d.n_sources} models</div>
    </div>`).join('');

  const warn = (data.warnings && data.warnings.length)
    ? `<div class="warn">⚠ Some sources were skipped: ${data.warnings.map(w=>w.replace(/</g,'&lt;')).join('; ')}</div>` : '';

  result.innerHTML = `
    <div class="meta">
      <h2>${data.location.name}</h2>
      <div class="coords">${data.location.latitude.toFixed(3)}, ${data.location.longitude.toFixed(3)} · tz ${data.location.timezone}</div>
      <div class="srcs">${data.sources.map(s=>`<span>${s}</span>`).join('')}</div>
      <div class="actions">
        <button type="button" id="share" class="primary">📤 Share</button>
        <button type="button" id="copy">🔗 Copy link</button>
      </div>
    </div>
    <div class="grid">${cards}</div>
    ${warn}`;
  document.getElementById('share').addEventListener('click', doShare);
  document.getElementById('copy').addEventListener('click', copyLink);
}

// --- sharing ---------------------------------------------------------------
function shareUrl(){
  if (!last) return location.href;
  const u = new URL(location.href);
  u.search = '';
  u.searchParams.set('location', last.query);
  u.searchParams.set('days', last.days);
  u.searchParams.set('units', last.imperial ? 'imperial' : 'metric');
  return u.toString();
}

function shareText(){
  if (!last) return '';
  const {data, imperial} = last;
  const t = c => c == null ? '–' : Math.round(imperial ? cToF(c) : c) + '°';
  const lines = data.forecast.slice(0, 4).map(d =>
    `${wd(d.date)} ${t(d.temp_max_c)}/${t(d.temp_min_c)} ${d.condition} (${d.confidence})`);
  return `Weather consensus for ${data.location.name}\n` + lines.join('\n')
    + `\n${data.sources.length}-model ensemble`;
}

async function doShare(){
  const url = shareUrl(), text = shareText();
  if (navigator.share){
    try { await navigator.share({title: 'Weather Consensus', text, url}); return; }
    catch (e){ if (e.name === 'AbortError') return; }  // user dismissed the sheet
  }
  copyLink();  // desktop / unsupported: fall back to clipboard
}

async function copyLink(){
  const payload = shareText() + '\n' + shareUrl();
  try { await navigator.clipboard.writeText(payload); toast('Copied to clipboard'); }
  catch { toast('Could not copy — link is in the address bar'); }
}

let toastTimer = null;
function toast(msg){
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// --- deep links: ?location=&days=&units= auto-runs the forecast on load ----
(function initFromUrl(){
  const p = new URLSearchParams(location.search);
  const ql = p.get('location');
  if (!ql) return;
  loc.value = ql;
  const d = p.get('days'); if (d && [...document.getElementById('days').options].some(o => o.value === d)) document.getElementById('days').value = d;
  const u = p.get('units'); if (u === 'imperial' || u === 'metric') document.getElementById('units').value = u;
  if (f.requestSubmit) f.requestSubmit(); else f.dispatchEvent(new Event('submit'));
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
