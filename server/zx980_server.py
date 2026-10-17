#!/usr/bin/env python3
"""ZX980 tracker server: receives the device's POST /message and shows the data on a web page.

Standard library only (no uvicorn/fastapi needed), replaces api.py.

- Receiver (internet side):   POST http://<host>:8080/message  {"texte": "..."}
- Dashboard (local network):  http://<host>:8090/

Two kinds of lines are sent by ZX980/usr/gps_post.py:
  short (every 10 s): date,lon,lat,alt,speed,
  full  (every 60 s): date,battery,fix,lon,lat,alt,speed,ip,operator,network,phone,satellites,
  (older firmware: same full line without satellites, 11 fields)
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger('zx980')

DMS_RE = re.compile(r'''(\d+)°(\d+)'([\d.]+)"([NSEW])''')
MOVING_KMH = 2.0  # below this speed a track segment is GPS jitter, not distance

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
	battery_mv INTEGER,
	fix TEXT,
	ip TEXT,
	operator TEXT,
	network TEXT,
	phone TEXT,
	satellites TEXT
);
CREATE INDEX IF NOT EXISTS messages_received ON messages(received);
'''

FIELDS = ('kind', 'device_time', 'lat', 'lon', 'alt', 'speed', 'battery', 'battery_mv', 'fix',
	'ip', 'operator', 'network', 'phone', 'satellites')


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
	elif len(parts) in (11, 12):
		out['kind'] = 'full'
		if len(parts) == 12:
			out['satellites'] = parts[11]  # '7/12(GP8+BD4)': used / in view (per constellation)
		# '100%' or '100%(4165mV)'
		m = re.match(r'(\d+)%(?:\((\d+)mV\))?', parts[1])
		out['battery'] = int(m.group(1)) if m else None
		out['battery_mv'] = int(m.group(2)) if m and m.group(2) else None
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
		# databases created before these columns existed: add them and fill them from the raw lines
		cols = [r['name'] for r in self.db.execute('PRAGMA table_info(messages)')]
		missing = [(c, t) for c, t in (('satellites', 'TEXT'), ('battery_mv', 'INTEGER')) if c not in cols]
		for col, sqltype in missing:
			self.db.execute('ALTER TABLE messages ADD COLUMN %s %s' % (col, sqltype))
		if missing:
			names = [c for c, _ in missing]
			rows = self.db.execute("SELECT id, raw FROM messages WHERE kind='full'").fetchall()
			for r in rows:
				parsed = parse_line(r['raw'])
				self.db.execute('UPDATE messages SET %s WHERE id=?' % ','.join(c + '=?' for c in names),
					[parsed[c] for c in names] + [r['id']])
			self.db.commit()
			log.info('Base mise à jour : colonnes %s ajoutées (%d lignes complètes relues)', ', '.join(names), len(rows))

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

	def purge(self, start=None, end=None):
		"""Delete the messages received between start and end (ISO strings), or all of them.
		VACUUM rewrites the file so the deleted positions do not stay in free pages."""
		with self.lock:
			if start is None:
				n = self.db.execute('DELETE FROM messages').rowcount
			else:
				n = self.db.execute('DELETE FROM messages WHERE received BETWEEN ? AND ?', (start, end)).rowcount
			self.db.commit()
			self.db.execute('VACUUM')
		return n

	def state(self, start, end, limit):
		"""Everything computed on messages received between start and end (ISO strings),
		except last_seen (latest message overall, for the online badge)"""
		rng = (start, end)
		last_seen = self.query('SELECT MAX(received) AS r FROM messages')[0]['r']
		last_full = self.query("SELECT * FROM messages WHERE kind='full' AND received BETWEEN ? AND ? "
			'ORDER BY id DESC LIMIT 1', rng)
		last_pos = self.query('SELECT * FROM messages WHERE lat IS NOT NULL AND received BETWEEN ? AND ? '
			'ORDER BY id DESC LIMIT 1', rng)
		track = self.query('SELECT lat, lon, speed FROM messages '
			'WHERE lat IS NOT NULL AND NOT (lat = 0 AND lon = 0) AND received BETWEEN ? AND ? ORDER BY id', rng)
		recent = self.query('SELECT * FROM messages WHERE received BETWEEN ? AND ? '
			'ORDER BY id DESC LIMIT ?', rng + (limit,))
		count = self.query('SELECT COUNT(*) AS n FROM messages WHERE received BETWEEN ? AND ?', rng)[0]['n']
		# stationary GPS jitter (a few metres every 10 s) would add up: count only moving segments
		dist = sum(haversine_km(a['lat'], a['lon'], b['lat'], b['lon']) for a, b in zip(track, track[1:])
			if max(a['speed'] or 0, b['speed'] or 0) >= MOVING_KMH)
		speeds = [t['speed'] for t in track if t['speed'] is not None]
		return {
			'now': datetime.now().isoformat(timespec='seconds'),
			'tz': datetime.now().astimezone().isoformat()[-6:],  # server UTC offset, e.g. +00:00
			'start': start,
			'end': end,
			'last_seen': last_seen,
			'last_full': last_full[0] if last_full else None,
			'last_pos': last_pos[0] if last_pos else None,
			'track': [[t['lat'], t['lon']] for t in track],
			'stats': {'points': len(track), 'km': round(dist, 2), 'max_speed': max(speeds, default=None)},
			'recent': recent,
			'count': count,
		}


def haversine_km(lat1, lon1, lat2, lon2):
	p1, p2 = math.radians(lat1), math.radians(lat2)
	a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
	return 2 * 6371.0 * math.asin(math.sqrt(a))


def parse_range(q):
	"""?from=2026-10-16T08:00&to=2026-10-16T18:00 or ?hours=24 -> (start, end) ISO strings"""
	now = datetime.now()
	def iso(key, pad):
		v = q.get(key, [''])[0].strip()
		if not v:
			return None
		datetime.fromisoformat(v)  # validation, raises ValueError
		return v + pad if len(v) == 16 else v  # datetime-local has no seconds
	start, end = iso('from', ':00'), iso('to', ':59')
	if start is None:
		hours = float(q.get('hours', ['24'])[0])
		start = (now - timedelta(hours=hours)).isoformat(timespec='seconds')
	return start, end or now.isoformat(timespec='seconds')


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


PBKDF2_ROUNDS = 200000


def hash_password(password):
	salt = secrets.token_bytes(16)
	digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ROUNDS)
	return 'pbkdf2_sha256$%d$%s$%s' % (PBKDF2_ROUNDS, salt.hex(), digest.hex())


def check_password(password, stored):
	try:
		algo, rounds, salt, digest = stored.split('$')
		if algo != 'pbkdf2_sha256':
			return False
		test = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
	except ValueError:
		return False
	return hmac.compare_digest(test.hex(), digest)


class Users:
	"""user:hash file, one per line; re-read when it changes so adding a user needs no restart.
	A missing or empty file locks the dashboard (fail closed)."""

	def __init__(self, path):
		self.path = path
		self.mtime = None
		self.users = {}
		self.ok_cache = set()  # sha256(user, password) already checked, PBKDF2 is slow on every 5 s refresh
		self.lock = threading.Lock()

	def load(self):
		try:
			mtime = os.stat(self.path).st_mtime
		except OSError:
			if self.mtime is not False:
				log.error("Fichier d'accès %s introuvable : tableau de bord verrouillé "
					'(créer un accès avec --add-user)', self.path)
			self.mtime, self.users = False, {}
			self.ok_cache.clear()
			return
		if mtime == self.mtime:
			return
		users = {}
		with open(self.path, encoding='utf-8') as fh:
			for line in fh:
				line = line.strip()
				if line and not line.startswith('#') and ':' in line:
					name, stored = line.split(':', 1)
					users[name] = stored
		self.mtime, self.users = mtime, users
		self.ok_cache.clear()
		log.info("Fichier d'accès %s : %d utilisateur(s)", self.path, len(users))

	def check(self, header):
		if not header or not header.startswith('Basic '):
			return None
		try:
			name, password = base64.b64decode(header[6:]).decode('utf-8').split(':', 1)
		except (ValueError, UnicodeDecodeError):
			return None
		with self.lock:
			self.load()
			key = hashlib.sha256((name + ':' + password).encode()).digest()
			if key in self.ok_cache:
				return name
			stored = self.users.get(name)
			if stored and check_password(password, stored):
				self.ok_cache.add(key)
				return name
		return None


def add_user(path, name):
	if not name or ':' in name:
		raise SystemExit("Nom d'utilisateur invalide")
	password = getpass.getpass('Mot de passe pour %s : ' % name)
	if len(password) < 8:
		raise SystemExit('Mot de passe trop court (8 caractères minimum)')
	if password != getpass.getpass('Confirmer : '):
		raise SystemExit('Les mots de passe ne correspondent pas')
	lines = []
	if os.path.exists(path):
		with open(path, encoding='utf-8') as fh:
			lines = [l for l in fh.read().splitlines() if not l.startswith(name + ':')]
	lines.append('%s:%s' % (name, hash_password(password)))
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
	with os.fdopen(fd, 'w', encoding='utf-8') as fh:
		fh.write('\n'.join(lines) + '\n')
	print('Accès %s enregistré dans %s' % (name, path))


class WebHandler(BaseHTTPRequestHandler):
	"""Local network side: dashboard + JSON API"""
	store = None
	users = None  # Users, or None = no access control

	def log_message(self, fmt, *args):
		log.debug('web %s - %s', self.client_address[0], fmt % args)

	def authorized(self):
		if self.users is None or self.users.check(self.headers.get('Authorization')):
			return True
		if self.headers.get('Authorization'):  # browsers always try once without credentials first
			log.warning('Mauvais identifiant ou mot de passe depuis %s', self.client_address[0])
		body = 'Accès protégé'.encode()
		self.send_response(401)
		self.send_header('WWW-Authenticate', 'Basic realm="ZX980 Tracker", charset="UTF-8"')
		self.send_header('Content-Type', 'text/plain; charset=utf-8')
		self.send_header('Content-Length', str(len(body)))
		self.end_headers()
		self.wfile.write(body)
		return False

	def send(self, code, ctype, body):
		self.send_response(code)
		self.send_header('Content-Type', ctype)
		self.send_header('Content-Length', str(len(body)))
		self.send_header('Cache-Control', 'no-store')
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		if not self.authorized():
			return
		url = urlparse(self.path)
		if url.path == '/':
			return self.send(200, 'text/html; charset=utf-8', PAGE.encode())
		if url.path == '/api/state':
			try:
				start, end = parse_range(parse_qs(url.query))
			except ValueError as e:
				return self.send(400, 'text/plain; charset=utf-8', str(e).encode())
			body = json.dumps(self.store.state(start, end, 500), ensure_ascii=False).encode()
			return self.send(200, 'application/json; charset=utf-8', body)
		if url.path == '/api/export.csv':
			q = parse_qs(url.query)
			try:
				start, end = parse_range(q) if 'from' in q else ('', '9999')
			except ValueError as e:
				return self.send(400, 'text/plain; charset=utf-8', str(e).encode())
			rows = self.store.query('SELECT received, source, raw FROM messages '
				'WHERE received BETWEEN ? AND ? ORDER BY id', (start, end))
			body = '\n'.join('%s;%s;%s' % (r['received'], r['source'], r['raw']) for r in rows).encode()
			return self.send(200, 'text/csv; charset=utf-8', body)
		self.send(404, 'text/plain', b'not found')

	def do_POST(self):
		"""POST /api/purge?from=...&to=... (or ?hours=N) deletes that period, ?all=1 deletes everything"""
		if not self.authorized():
			return
		url = urlparse(self.path)
		if url.path != '/api/purge':
			return self.send(404, 'text/plain', b'not found')
		# the browser re-sends Basic credentials on its own, even for a form posted by another site:
		# a custom header can only come from this page's fetch() (a cross-site one needs a CORS preflight)
		if self.headers.get('X-ZX980') != 'purge':
			log.warning('Purge refusée depuis %s : en-tête X-ZX980 absent', self.client_address[0])
			return self.send(403, 'text/plain; charset=utf-8', 'requête refusée'.encode())
		q = parse_qs(url.query)
		try:
			if q.get('all') == ['1']:
				start = end = None
				n = self.store.purge()
			else:
				if 'from' not in q and 'hours' not in q:
					raise ValueError('période manquante (from/to ou hours)')
				start, end = parse_range(q)
				n = self.store.purge(start, end)
		except ValueError as e:
			return self.send(400, 'text/plain; charset=utf-8', str(e).encode())
		log.warning('Purge par %s : %d message(s) supprimé(s) (%s)', self.client_address[0], n,
			'tout' if start is None else '%s → %s' % (start, end))
		self.send(200, 'application/json; charset=utf-8', json.dumps({'deleted': n}).encode())


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
#range { display:inline-flex; gap:8px; flex-wrap:wrap; align-items:center; }
select, input, .btn { font:inherit; cursor:pointer; padding:4px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); text-decoration:none; }
.btn.danger { color:var(--bad); border-color:var(--bad); }
.btn.danger:hover { background:var(--bad); color:#fff; }
.btn:disabled { opacity:.5; cursor:default; }
a { color:var(--accent); }
@media (prefers-color-scheme: dark) { a { color:#60a5fa; } }
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
    <option value="24" selected>24 h</option><option value="168">7 j</option>
    <option value="custom">Période choisie</option></select></label>
  <span id="range">
    <label>du <input type="datetime-local" id="from"></label>
    <label>au <input type="datetime-local" id="to"></label>
    <button id="show" class="btn">Afficher</button>
  </span>
  <a class="btn" id="csv" href="/api/export.csv">Export CSV</a>
  <button id="purge" class="btn danger" title="Supprime les messages de la période affichée">Purger la période</button>
  <button id="purgeAll" class="btn danger" title="Supprime tous les messages de la base">Tout purger</button>
</header>
<main>
  <div class="side">
    <div class="card"><h2>Trajet</h2><dl id="trip"></dl></div>
    <div class="card"><h2>Position</h2><dl id="pos"></dl></div>
    <div class="card"><h2>Appareil</h2><dl id="dev"></dl></div>
  </div>
  <div id="map"></div>
  <div class="card wide"><h2>Messages de la période (<span id="count">0</span>)</h2>
    <div class="scroll"><table><thead><tr>
      <th>Reçu</th><th>Type</th><th>Heure GPS</th><th>Lat</th><th>Lon</th><th>Alt</th><th>Vitesse</th>
      <th>Batt.</th><th>Fix</th><th title="utilisés / visibles · visibles par constellation">Satellites</th><th>Réseau</th><th>Opérateur</th><th>IP</th></tr></thead>
    <tbody id="rows"></tbody></table></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f = (v, d, u='') => v == null ? '—' : Number(v).toFixed(d) + u;
const t = s => s ? s.replace('T', ' ') : '—';
const volts = (mv, sep = ' ') => mv == null ? '' : sep + (mv / 1000).toFixed(2) + ' V';

// '13/18(BD7+GP11)' = 13 used in the fix / 18 in view, of which 7 BeiDou and 11 GPS
const CONSTELLATIONS = {GP: 'GPS', BD: 'BeiDou', GL: 'GLONASS', GA: 'Galileo', GQ: 'QZSS'};
function sats(s) {
  const m = /^(\d+)\/(\d+)(?:\((.*)\))?$/.exec(s || '');
  if (!m) return null;
  const per = (m[3] || '').split('+').map(p => /^([A-Z]{2})(\d+)$/.exec(p)).filter(Boolean)
    .map(([, c, n]) => [CONSTELLATIONS[c] || c, +n]);
  return {used: +m[1], view: +m[2], per};
}
const satCell = s => {
  const x = sats(s);
  return x ? `${x.used}/${x.view}` + x.per.map(([name, n]) => ` · ${name} ${n}`).join('') : esc(s);
};

const map = L.map('map').setView([46.6, 2.4], 6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap'}).addTo(map);
const line = L.polyline([], {color: '#2563eb', weight: 3}).addTo(map);
const marker = L.circleMarker([0, 0], {radius: 8, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1});
const startMarker = L.circleMarker([0, 0], {radius: 7, color: '#fff', weight: 2, fillColor: '#16a34a', fillOpacity: 1});
let centered = false, fitPending = false;

function dl(el, items) {
  el.innerHTML = items.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('');
}

// Server times are naive in the server's timezone (often UTC on FreeBSD): convert to/from the browser's
let srvTz = '+00:00';
const local = (d, n = 16) => new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, n);
const tl = s => s ? local(new Date(s + srvTz), 19).replace('T', ' ') : '—';
const toServer = v => {
  if (!v) return '';
  const off = (srvTz[0] === '-' ? -1 : 1) * (parseInt(srvTz.slice(1, 3)) * 60 + parseInt(srvTz.slice(4, 6)));
  return new Date(new Date(v).getTime() + off * 60000).toISOString().slice(0, 19);
};
const custom = () => $('hours').value === 'custom';

function query() {
  return custom()
    ? 'from=' + encodeURIComponent(toServer($('from').value)) + '&to=' + encodeURIComponent(toServer($('to').value && $('to').value + ':59'))
    : 'hours=' + $('hours').value;
}

let last = null;  // last /api/state answer, for the purge confirmation

async function refresh() {
  let s;
  const q = query();
  $('csv').href = '/api/export.csv' + (custom() ? '?' + q : '');
  try {
    const r = await fetch('/api/state?' + q);
    if (!r.ok) throw new Error(await r.text());
    s = await r.json();
    srvTz = s.tz;
  } catch (e) { $('age').textContent = 'erreur : ' + e.message; $('age').style.background = 'var(--bad)'; return; }
  const p = s.last_pos, d = s.last_full;
  const none = '<dt style="grid-column:1/-1">Aucune donnée sur la période</dt>';
  if (s.last_seen) {
    const age = (new Date(s.now) - new Date(s.last_seen)) / 1000;
    $('age').textContent = age < 60 ? `en ligne · ${Math.round(age)} s` : age < 3600 ? `vu il y a ${Math.round(age/60)} min` : `hors ligne · ${tl(s.last_seen)}`;
    $('age').style.background = age < 30 ? 'var(--ok)' : age < 300 ? 'var(--warn)' : 'var(--bad)';
  } else {
    $('age').textContent = 'aucune donnée';
    $('age').style.background = 'var(--muted)';
  }
  if (!p) { $('pos').innerHTML = none; marker.remove(); }
  else {
    dl($('pos'), [
      ['Heure GPS', esc(t(p.device_time))], ['Latitude', f(p.lat, 6)], ['Longitude', f(p.lon, 6)],
      ['Altitude', f(p.alt, 1, ' m')], ['Vitesse', f(p.speed, 1, ' km/h')],
      ['Carte', `<a href="https://www.google.com/maps?q=${p.lat},${p.lon}" target="_blank">Google Maps</a>`]]);
    marker.setLatLng([p.lat, p.lon]).addTo(map);
    if (!centered && !custom()) { map.setView([p.lat, p.lon], 17); centered = true; }
  }
  if (!d) $('dev').innerHTML = none;
  else {
    const b = d.battery ?? 0, col = b > 50 ? 'var(--ok)' : b > 20 ? 'var(--warn)' : 'var(--bad)';
    const sat = sats(d.satellites);
    dl($('dev'), [
      ['Batterie', `${b} %${volts(d.battery_mv, ' · ')}<div class="bar"><div style="width:${b}%;background:${col}"></div></div>`],
      ['Fix GPS', esc(d.fix)],
      ['Satellites', sat ? `${sat.used} utilisés / ${sat.view} visibles` : '—'],
      ...(sat ? sat.per.map(([name, n]) => ['· ' + name, `${n} visibles`]) : []),
      ['Réseau', esc(d.network)], ['Opérateur', esc(d.operator)],
      ['IP', esc(d.ip)], ['Téléphone', esc(d.phone)], ['Dernier état', esc(tl(d.received))]]);
  }
  line.setLatLngs(s.track);
  if (s.track.length) startMarker.setLatLng(s.track[0]).addTo(map); else startMarker.remove();
  if (fitPending) {
    fitPending = false;
    if (s.track.length) map.fitBounds(line.getBounds(), {padding: [30, 30], maxZoom: 17});
  }
  const st = s.stats;
  if (s.start > s.end) $('trip').innerHTML = '<dt style="grid-column:1/-1;color:var(--bad)">La date de début est après la date de fin</dt>';
  else dl($('trip'), [
    ['Du', esc(tl(s.start))], ['Au', esc(tl(s.end))], ['Points', st.points],
    ['Distance', f(st.km, 2, ' km')], ['Vitesse max', f(st.max_speed, 1, ' km/h')]]);
  $('count').textContent = s.count;
  last = s;
  $('purge').disabled = !s.count;
  $('rows').innerHTML = s.recent.map(r => r.kind === 'other'
    ? `<tr><td>${esc(tl(r.received))}</td><td><span class="tag">brut</span></td><td colspan="11">${esc(r.raw)}</td></tr>`
    : `<tr><td>${esc(tl(r.received))}</td><td><span class="tag ${r.kind}">${r.kind}</span></td><td>${esc(t(r.device_time))}</td>
      <td>${f(r.lat, 6)}</td><td>${f(r.lon, 6)}</td><td>${f(r.alt, 1, ' m')}</td><td>${f(r.speed, 1, ' km/h')}</td>
      <td>${r.battery == null ? '' : r.battery + ' %' + volts(r.battery_mv, ' · ')}</td><td>${esc(r.fix)}</td>
      <td>${r.satellites ? satCell(r.satellites) : ''}</td><td>${esc(r.network)}</td><td>${esc(r.operator)}</td><td>${esc(r.ip)}</td></tr>`).join('');
}
// default range: today from midnight to now
const now = new Date();
$('from').value = local(new Date(now.getFullYear(), now.getMonth(), now.getDate()));
$('to').value = local(now);
$('hours').onchange = () => { fitPending = true; refresh(); };
$('show').onclick = () => { $('hours').value = 'custom'; fitPending = true; refresh(); };
$('from').onchange = $('to').onchange = $('show').onclick;

async function purge(q) {
  const r = await fetch('/api/purge?' + q, {method: 'POST', headers: {'X-ZX980': 'purge'}});
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()).deleted;
}
$('purge').onclick = async () => {
  const q = query();  // same period as the one displayed
  if (!last || !last.count) return;
  if (last.start > last.end) return alert('La date de début est après la date de fin.');
  if (!confirm(`Supprimer définitivement les ${last.count} messages reçus\ndu ${tl(last.start)}\nau ${tl(last.end)} ?\n\nCette action est irréversible.`)) return;
  try { alert(`${await purge(q)} message(s) supprimé(s).`); }
  catch (e) { alert('Échec de la purge : ' + e.message); }
  refresh();
};
$('purgeAll').onclick = async () => {
  if (!confirm('Supprimer TOUS les messages de la base, toutes dates confondues ?\n\nCette action est irréversible.')) return;
  if (prompt('Pour confirmer, tapez SUPPRIMER :') !== 'SUPPRIMER') return alert('Purge annulée.');
  try { alert(`${await purge('all=1')} message(s) supprimé(s). La base est vide.`); }
  catch (e) { alert('Échec de la purge : ' + e.message); }
  refresh();
};
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
	ap.add_argument('--auth-file', help="fichier d'accès au tableau de bord (absent = pas de mot de passe)")
	ap.add_argument('--add-user', metavar='NOM', help="crée ou change l'accès NOM dans --auth-file, puis quitte")
	ap.add_argument('-v', '--verbose', action='store_true')
	args = ap.parse_args()

	if args.add_user:
		if not args.auth_file:
			ap.error('--add-user demande --auth-file')
		return add_user(args.auth_file, args.add_user)

	logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
		format='%(asctime)s %(levelname)s %(message)s')
	store = Store(args.db)
	ReceiverHandler.store = store
	WebHandler.store = store
	if args.auth_file:
		WebHandler.users = Users(args.auth_file)
		WebHandler.users.load()
	else:
		log.warning("Tableau de bord sans mot de passe (pas de --auth-file)")
	serve('Récepteur', args.api_host, args.api_port, ReceiverHandler)
	serve('Tableau de bord', args.web_host, args.web_port, WebHandler)
	try:
		threading.Event().wait()
	except KeyboardInterrupt:
		pass


if __name__ == '__main__':
	main()
