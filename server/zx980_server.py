#!/usr/bin/env python3
"""ZX980 tracker server: receives the device's POST /message and shows the data on a web page.

Standard library only (no uvicorn/fastapi needed), replaces api.py.

- Receiver (internet side):   POST http://<host>:8080/message  {"texte": "..."}
- Dashboard (local network):  http://<host>:8090/

Two kinds of lines are sent by helpers/gps_post.py:
  short (every 10 s): date,lon,lat,alt,speed,
  full  (every 60 s): date,battery,fix,lon,lat,alt,speed,ip,operator,network,phone,
"""
import argparse
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger('zx980')

DMS_RE = re.compile(r'''(\d+)°(\d+)'([\d.]+)"([NSEW])''')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS messages (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	received TEXT NOT NULL,
	source TEXT,
	raw TEXT NOT NULL,
	kind TEXT,
	device_time TEXT,
	lat REAL,
	lon REAL,
	alt REAL,
	speed REAL,
	battery INTEGER,
	fix TEXT,
	ip TEXT,
	operator TEXT,
	network TEXT,
	phone TEXT
);
CREATE INDEX IF NOT EXISTS messages_received ON messages(received);
'''

FIELDS = ('kind', 'device_time', 'lat', 'lon', 'alt', 'speed', 'battery', 'fix',
	'ip', 'operator', 'network', 'phone')


def dms_to_deg(text):
	"""0°13'36.0"E -> 0.226667"""
	m = DMS_RE.fullmatch(text.strip())
	if not m:
		return None
	d, mn, s, hemi = m.groups()
	deg = int(d) + int(mn) / 60 + float(s) / 3600
	return -deg if hemi in 'SW' else deg


def number(text, suffix):
	try:
		return float(text.strip().removesuffix(suffix))
	except ValueError:
		return None


def parse_line(texte):
	"""CSV line from gps_post.py -> dict of FIELDS (missing ones = None)"""
	parts = [p.strip() for p in texte.split(',')]
	if parts and parts[-1] == '':
		parts.pop()
	out = dict.fromkeys(FIELDS)
	try:
		out['device_time'] = datetime.strptime(parts[0], '%d/%m/%Y %H:%M:%S').isoformat()
	except (ValueError, IndexError):
		out['kind'] = 'other'
		return out
	if len(parts) == 5:
		out['kind'] = 'gps'
		gps = parts[1:5]
	elif len(parts) == 11:
		out['kind'] = 'full'
		out['battery'] = int(number(parts[1], '%') or 0)
		out['fix'] = parts[2]
		gps = parts[3:7]
		out['ip'], out['operator'], out['network'], out['phone'] = parts[7:11]
	else:
		out['kind'] = 'other'
		return out
	out['lon'] = dms_to_deg(gps[0])
	out['lat'] = dms_to_deg(gps[1])
	out['alt'] = number(gps[2], 'm')
	out['speed'] = number(gps[3], 'km/h')
	return out


class Store:
	def __init__(self, path):
		self.lock = threading.Lock()
		self.db = sqlite3.connect(path, check_same_thread=False)
		self.db.row_factory = sqlite3.Row
		self.db.executescript(SCHEMA)

	def add(self, source, texte):
		row = parse_line(texte)
		row.update(received=datetime.now().isoformat(timespec='seconds'), source=source, raw=texte)
		cols = ','.join(row)
		with self.lock:
			self.db.execute('INSERT INTO messages (%s) VALUES (%s)' % (cols, ','.join('?' * len(row))),
				tuple(row.values()))
			self.db.commit()
		return row

	def query(self, sql, args=()):
		with self.lock:
			return [dict(r) for r in self.db.execute(sql, args)]

	def state(self, hours, limit):
		since = datetime.fromtimestamp(datetime.now().timestamp() - hours * 3600).isoformat(timespec='seconds')
		last_full = self.query("SELECT * FROM messages WHERE kind='full' ORDER BY id DESC LIMIT 1")
		last_pos = self.query('SELECT * FROM messages WHERE lat IS NOT NULL ORDER BY id DESC LIMIT 1')
		track = self.query('SELECT device_time, lat, lon, speed FROM messages '
			'WHERE lat IS NOT NULL AND received >= ? ORDER BY id', (since,))
		recent = self.query('SELECT * FROM messages ORDER BY id DESC LIMIT ?', (limit,))
		count = self.query('SELECT COUNT(*) AS n FROM messages')[0]['n']
		return {
			'now': datetime.now().isoformat(timespec='seconds'),
			'last_full': last_full[0] if last_full else None,
			'last_pos': last_pos[0] if last_pos else None,
			'track': [[t['lat'], t['lon']] for t in track],
			'recent': recent,
			'count': count,
		}


class ReceiverHandler(BaseHTTPRequestHandler):
	"""Internet side: only POST /message"""
	store = None

	def log_message(self, fmt, *args):
		log.debug('%s - %s', self.client_address[0], fmt % args)

	def reply(self, code, obj):
		body = json.dumps(obj).encode()
		self.send_response(code)
		self.send_header('Content-Type', 'application/json')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_POST(self):
		if urlparse(self.path).path != '/message':
			return self.reply(404, {'error': 'not found'})
		try:
			length = int(self.headers.get('Content-Length', 0))
			data = json.loads(self.rfile.read(length).decode('utf-8'))
			texte = str(data['texte'])
		except (ValueError, KeyError, TypeError) as e:
			log.warning('Bad request from %s: %s', self.client_address[0], e)
			return self.reply(400, {'error': 'invalid JSON or missing "texte"'})
		log.info('Message reçu de %s : %s', self.client_address[0], texte)
		self.store.add(self.client_address[0], texte)
		self.reply(200, {'status': 'ok'})

	def do_GET(self):
		self.reply(404, {'error': 'not found'})


class WebHandler(BaseHTTPRequestHandler):
	"""Local network side: dashboard + JSON API"""
	store = None

	def log_message(self, fmt, *args):
		log.debug('web %s - %s', self.client_address[0], fmt % args)

	def send(self, code, ctype, body):
		self.send_response(code)
		self.send_header('Content-Type', ctype)
		self.send_header('Content-Length', str(len(body)))
		self.send_header('Cache-Control', 'no-store')
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		url = urlparse(self.path)
		if url.path == '/':
			return self.send(200, 'text/html; charset=utf-8', PAGE.encode())
		if url.path == '/api/state':
			q = parse_qs(url.query)
			hours = float(q.get('hours', ['24'])[0])
			body = json.dumps(self.store.state(hours, 200), ensure_ascii=False).encode()
			return self.send(200, 'application/json; charset=utf-8', body)
		if url.path == '/api/export.csv':
			rows = self.store.query('SELECT received, source, raw FROM messages ORDER BY id')
			body = '\n'.join('%s;%s;%s' % (r['received'], r['source'], r['raw']) for r in rows).encode()
			return self.send(200, 'text/csv; charset=utf-8', body)
		self.send(404, 'text/plain', b'not found')


PAGE = r'''<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZX980 Tracker</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root { --bg:#f4f5f7; --card:#fff; --fg:#1d2330; --muted:#6b7280; --line:#e3e6eb; --ok:#16a34a; --warn:#d97706; --bad:#dc2626; --accent:#2563eb; }
@media (prefers-color-scheme: dark) { :root { --bg:#12151b; --card:#1b1f27; --fg:#e6e8ec; --muted:#9aa3b2; --line:#2b313c; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, sans-serif; }
header { display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid var(--line); background:var(--card); flex-wrap:wrap; }
header h1 { font-size:18px; margin:0; }
#age { padding:2px 10px; border-radius:99px; font-weight:600; color:#fff; background:var(--muted); }
header .sp { flex:1; }
select, a.btn { font:inherit; padding:4px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); text-decoration:none; }
main { display:grid; grid-template-columns: 340px 1fr; gap:16px; padding:16px; }
@media (max-width: 860px) { main { grid-template-columns: 1fr; } }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }
.card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:0 0 10px; }
dl { display:grid; grid-template-columns: auto 1fr; gap:6px 12px; margin:0; }
dt { color:var(--muted); } dd { margin:0; font-variant-numeric:tabular-nums; text-align:right; }
#map { height:520px; border-radius:10px; border:1px solid var(--line); }
.side { display:flex; flex-direction:column; gap:16px; }
.bar { height:8px; background:var(--line); border-radius:4px; overflow:hidden; margin-top:4px; }
.bar > div { height:100%; background:var(--ok); }
table { width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { padding:6px 8px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }
th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
.wide { grid-column:1 / -1; }
.scroll { max-height:420px; overflow:auto; }
.tag { font-size:11px; padding:1px 6px; border-radius:4px; background:var(--line); }
.tag.full { background:var(--accent); color:#fff; }
</style>
</head>
<body>
<header>
  <h1>ZX980 Tracker</h1>
  <span id="age">—</span>
  <span class="sp"></span>
  <label>Trace <select id="hours">
    <option value="1">1 h</option><option value="6">6 h</option>
    <option value="24" selected>24 h</option><option value="168">7 j</option></select></label>
  <a class="btn" href="/api/export.csv">Export CSV</a>
</header>
<main>
  <div class="side">
    <div class="card"><h2>Position</h2><dl id="pos"></dl></div>
    <div class="card"><h2>Appareil</h2><dl id="dev"></dl></div>
  </div>
  <div id="map"></div>
  <div class="card wide"><h2>Derniers messages (<span id="count">0</span> au total)</h2>
    <div class="scroll"><table><thead><tr>
      <th>Reçu</th><th>Type</th><th>Heure GPS</th><th>Lat</th><th>Lon</th><th>Alt</th><th>Vitesse</th>
      <th>Batt.</th><th>Fix</th><th>Réseau</th><th>Opérateur</th><th>IP</th></tr></thead>
    <tbody id="rows"></tbody></table></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f = (v, d, u='') => v == null ? '—' : Number(v).toFixed(d) + u;
const t = s => s ? s.replace('T', ' ') : '—';

const map = L.map('map').setView([46.6, 2.4], 6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(map);
const line = L.polyline([], {color: '#2563eb', weight: 3}).addTo(map);
const marker = L.circleMarker([0, 0], {radius: 8, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1});
let centered = false;

function dl(el, items) {
  el.innerHTML = items.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('');
}

async function refresh() {
  let s;
  try { s = await (await fetch('/api/state?hours=' + $('hours').value)).json(); }
  catch (e) { $('age').textContent = 'serveur injoignable'; $('age').style.background = 'var(--bad)'; return; }
  const p = s.last_pos, d = s.last_full;
  if (p) {
    const age = (new Date(s.now) - new Date(p.received)) / 1000;
    $('age').textContent = age < 60 ? `en ligne · ${Math.round(age)} s` : age < 3600 ? `vu il y a ${Math.round(age/60)} min` : `hors ligne · ${t(p.received)}`;
    $('age').style.background = age < 30 ? 'var(--ok)' : age < 300 ? 'var(--warn)' : 'var(--bad)';
    dl($('pos'), [
      ['Heure GPS', esc(t(p.device_time))], ['Latitude', f(p.lat, 6)], ['Longitude', f(p.lon, 6)],
      ['Altitude', f(p.alt, 1, ' m')], ['Vitesse', f(p.speed, 1, ' km/h')],
      ['Carte', `<a href="https://www.google.com/maps?q=${p.lat},${p.lon}" target="_blank">Google Maps</a>`]]);
    marker.setLatLng([p.lat, p.lon]).addTo(map);
    if (!centered) { map.setView([p.lat, p.lon], 17); centered = true; }
  }
  if (d) {
    const b = d.battery ?? 0, col = b > 50 ? 'var(--ok)' : b > 20 ? 'var(--warn)' : 'var(--bad)';
    dl($('dev'), [
      ['Batterie', `${b} %<div class="bar"><div style="width:${b}%;background:${col}"></div></div>`],
      ['Fix GPS', esc(d.fix)], ['Réseau', esc(d.network)], ['Opérateur', esc(d.operator)],
      ['IP', esc(d.ip)], ['Téléphone', esc(d.phone)], ['Dernier état', esc(t(d.received))]]);
  }
  line.setLatLngs(s.track);
  $('count').textContent = s.count;
  $('rows').innerHTML = s.recent.map(r => r.kind === 'other'
    ? `<tr><td>${esc(t(r.received))}</td><td><span class="tag">brut</span></td><td colspan="10">${esc(r.raw)}</td></tr>`
    : `<tr><td>${esc(t(r.received))}</td><td><span class="tag ${r.kind}">${r.kind}</span></td><td>${esc(t(r.device_time))}</td>
      <td>${f(r.lat, 6)}</td><td>${f(r.lon, 6)}</td><td>${f(r.alt, 1, ' m')}</td><td>${f(r.speed, 1, ' km/h')}</td>
      <td>${r.battery == null ? '' : r.battery + ' %'}</td><td>${esc(r.fix)}</td><td>${esc(r.network)}</td><td>${esc(r.operator)}</td><td>${esc(r.ip)}</td></tr>`).join('');
}
$('hours').onchange = refresh;
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
'''


def serve(name, host, port, handler):
	httpd = ThreadingHTTPServer((host, port), handler)
	log.info('%s sur http://%s:%d', name, host, port)
	threading.Thread(target=httpd.serve_forever, name=name, daemon=True).start()
	return httpd


def main():
	ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	ap.add_argument('--db', default='/var/db/zx980/zx980.sqlite')
	ap.add_argument('--api-host', default='0.0.0.0')
	ap.add_argument('--api-port', type=int, default=8080, help='POST /message (ouvert sur internet)')
	ap.add_argument('--web-host', default='0.0.0.0')
	ap.add_argument('--web-port', type=int, default=8090, help='tableau de bord (réseau local)')
	ap.add_argument('-v', '--verbose', action='store_true')
	args = ap.parse_args()

	logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
		format='%(asctime)s %(levelname)s %(message)s')
	store = Store(args.db)
	ReceiverHandler.store = store
	WebHandler.store = store
	serve('Récepteur', args.api_host, args.api_port, ReceiverHandler)
	serve('Tableau de bord', args.web_host, args.web_port, WebHandler)
	try:
		threading.Event().wait()
	except KeyboardInterrupt:
		pass


if __name__ == '__main__':
	main()
