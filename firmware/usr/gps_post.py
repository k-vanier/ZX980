import math
import utime
import _thread
import checkNet
from machine import Pin, UART
from usr.post_message import post_json, load_config

# Server address and send intervals: /usr/config.json
# (server_host, server_port, server_path, gps_interval_s, full_interval_s)

# ZX908 (EC800N / EC800M): GPS AT6558 power on GPIO10 (pin54), NMEA on UART2 (pin17 RX / pin18 TX)
NMEA_PORT = UART.UART2
ENABLE_PIN = Pin.GPIO10
LED_RED = Pin.GPIO15     # pin80: SENT_BLINKS quick flashes after each message sent
SENT_BLINKS = 3
SOS_BLINKS = 6
LED_YELLOW = Pin.GPIO17  # pin76: fast blink while the GPS has a fix
LED_BLUE = Pin.GPIO16    # pin81: button feedback only
FIX_BLINK_MS = 125       # yellow on/off time with a fix (4 Hz)
FIX_TIMEOUT_MS = 5000    # no valid RMC for this long = fix lost

# Position quality filter: positions not meeting all three are not sent
MIN_FIX_TYPE = 3         # 3 = 3D fix only (2 to also accept 2D)
MAX_HDOP = 2.5           # horizontal dilution of precision (GGA), lower is better
MIN_SATS_USED = 6        # satellites used in the fix (GGA)

# AT6558 constellations ($PCAS04 bitmask): 1 GPS, 2 BeiDou, 4 GLONASS -> 7 = all three
GNSS_MODE = 7

LOW_BATTERY_MV = 3500      # power off below this voltage (on battery only)
LOW_BATTERY_CHECKS = 3     # consecutive low readings required
BATTERY_CHECK_MS = 30000   # time between two battery checks

# Li-ion voltage -> percentage (same table as usr/battery.py)
VOLTAGE_TABLE = [
	(4.143, 100), (4.079, 95), (4.023, 90), (3.972, 85), (3.923, 80), (3.877, 75), (3.837, 70),
	(3.804, 65), (3.774, 60), (3.748, 55), (3.722, 50), (3.695, 45), (3.670, 40), (3.647, 35),
	(3.626, 30), (3.607, 25), (3.587, 20), (3.563, 15), (3.529, 10), (3.477, 5), (3.430, 0)]

# Updated by run(), read by the LED thread
gps_fix = False
last_fix_ms = 0
last_utc = None    # (year, month, day, hour, minute, second) from the last valid RMC
last_lat = 0.0
last_lon = 0.0
last_speed_kmh = 0.0
last_altitude = None  # metres above sea level, from GGA
last_hdop = None      # horizontal dilution of precision, from GGA
fix_type = 1       # GSA: 1 = no fix, 2 = 2D, 3 = 3D

# net.getNetMode()[3] (access technology) -> label
NET_TECH = {0: '2G', 1: '2G', 2: '3G', 3: '2G', 4: '3G', 5: '3G', 6: '3G', 7: '4G', 9: '4G'}


# --- LEDs: all three are driven by led_thread() only ----------------------------
# Button feedback (send now / SOS in progress), all three LEDs at once:
#   red fast blink, yellow fast blink, blue: 2 quick flashes, 1 quick flash, 0.5 s off
FEEDBACK_RED_MS = 50       # red on/off time during feedback (10 Hz)
FEEDBACK_YELLOW_MS = 60    # yellow on/off time during feedback (about 8 Hz)
BLUE_FLASHES = ((0, 60), (120, 180), (300, 360))  # (on, off) in ms within one cycle
BLUE_CYCLE_MS = 860        # last flash ends at 360 ms, then 500 ms off
FEEDBACK_MIN_MS = 1000     # feedback shown at least this long
FEEDBACK_MAX_MS = 30000    # safety: feedback stops by itself after this

feedback_start = 0
feedback_until = 0         # feedback shown while ticks_ms() is before this (or an SOS is pending)
red_flash_start = 0
red_flash_count = 0        # flashes still to show for "message accepted"


def start_feedback():
	global feedback_start, feedback_until
	feedback_start = utime.ticks_ms()
	feedback_until = utime.ticks_add(feedback_start, FEEDBACK_MAX_MS)


def end_feedback():
	"""Stop the feedback now, but not before FEEDBACK_MIN_MS"""
	global feedback_until
	feedback_until = utime.ticks_add(feedback_start, FEEDBACK_MIN_MS)
	if utime.ticks_diff(utime.ticks_ms(), feedback_until) > 0:
		feedback_until = utime.ticks_ms()


def feedback_active(now):
	return sos_requested or utime.ticks_diff(feedback_until, now) > 0


def flash_red(count):
	"""count quick red flashes = message accepted (shown after any running feedback)"""
	global red_flash_start, red_flash_count
	now = utime.ticks_ms()
	red_flash_start = feedback_until if utime.ticks_diff(feedback_until, now) > 0 else now
	red_flash_count = count


def led_thread():
	"""Yellow: blinks while the GPS has a fix. Red: flashes per message accepted.
	Button feedback overrides all three. 20 ms resolution."""
	global red_flash_count
	red = Pin(LED_RED, Pin.OUT, Pin.PULL_DISABLE, 0)
	yellow = Pin(LED_YELLOW, Pin.OUT, Pin.PULL_DISABLE, 0)
	blue = Pin(LED_BLUE, Pin.OUT, Pin.PULL_DISABLE, 0)
	t0 = utime.ticks_ms()
	while True:
		now = utime.ticks_ms()
		if feedback_active(now):
			ph = utime.ticks_diff(now, feedback_start)
			r = ph % (2 * FEEDBACK_RED_MS) < FEEDBACK_RED_MS
			y = ph % (2 * FEEDBACK_YELLOW_MS) < FEEDBACK_YELLOW_MS
			c = ph % BLUE_CYCLE_MS
			b = any(on <= c < off for on, off in BLUE_FLASHES)
		else:
			b = False
			y = has_fix() and utime.ticks_diff(now, t0) % (2 * FIX_BLINK_MS) < FIX_BLINK_MS
			r = False
			if red_flash_count:
				rp = utime.ticks_diff(now, red_flash_start)
				if rp >= red_flash_count * 200:
					red_flash_count = 0
				elif rp >= 0:
					r = rp % 200 < 80
		red.write(1 if r else 0)
		yellow.write(1 if y else 0)
		blue.write(1 if b else 0)
		utime.sleep_ms(20)


def has_fix():
	return gps_fix and utime.ticks_diff(utime.ticks_ms(), last_fix_ms) < FIX_TIMEOUT_MS


def nmea_to_degrees(value, hemisphere):
	"""NMEA ddmm.mmmm / dddmm.mmmm -> signed decimal degrees"""
	v = float(value.decode())
	deg = int(v / 100)
	deg += (v - deg * 100) / 60
	return -deg if hemisphere in (b'S', b'W') else deg


def to_dms(deg, pos, neg):
	"""Decimal degrees -> 49°08'48.00"N, rounded to 0.01 s (about 30 cm) without 60.00 s carry issues"""
	hundredths = int(abs(deg) * 360000 + 0.5)
	d = hundredths // 360000
	m = (hundredths % 360000) // 6000
	s = hundredths % 6000
	return '%d°%d\'%d.%02d"%s' % (d, m, s // 100, s % 100, neg if deg < 0 else pos)


def nmea_command(body):
	"""'PCAS04,7' -> b'$PCAS04,7*1E\\r\\n' (checksum = XOR of the characters between $ and *)"""
	cs = 0
	for c in body:
		cs ^= ord(c)
	return ('$%s*%02X\r\n' % (body, cs)).encode()


def configure_gps(uart):
	"""Settings are lost each time GPIO10 cuts the GPS power: send them after every power-on"""
	utime.sleep_ms(1000)  # let the chip boot
	uart.write(nmea_command('PCAS04,%d' % GNSS_MODE))
	print('GPS constellations set to mode %d' % GNSS_MODE)


def good_fix():
	"""(True, '') or (False, reason) according to MIN_FIX_TYPE, MAX_HDOP and MIN_SATS_USED"""
	if fix_type < MIN_FIX_TYPE:
		return False, 'fix %dD' % fix_type if fix_type > 1 else 'no 2D/3D fix'
	if last_hdop is None or last_hdop > MAX_HDOP:
		return False, 'HDOP %s > %s' % (last_hdop, MAX_HDOP)
	if sats_used < MIN_SATS_USED:
		return False, '%d satellites < %d' % (sats_used, MIN_SATS_USED)
	return True, ''


# --- Coherence check -------------------------------------------------------------
# Each RMC position (1 per second) is a candidate, compared with the last ACCEPTED
# one. Only accepted positions are sent (last_lat, last_lon, last_speed_kmh,
# last_altitude, last_utc are the accepted values).
MAX_SPEED_KMH = 250        # implied speed above this = jump, rejected
MAX_ACCEL_MS2 = 8          # change of reported speed above this (m/s per s) = rejected
JUMP_BASE_M = 15           # distance tolerance on top of the reported speed ...
JUMP_HDOP_M = 5            # ... plus this many metres per HDOP unit
GAP_S = 30                 # after a gap this long (tunnel, lost fix): only MAX_SPEED_KMH is checked
RESET_AFTER_REJECTS = 5    # this many rejections in a row: the reference was wrong, restart from the candidate
ALT_BASE_M = 25            # altitude tolerance on top of ...
MAX_CLIMB_MS = 10          # ... this vertical speed (m/s); beyond: previous altitude is kept
STATIONARY_KMH = 2         # below this reported speed the device is considered stopped:
STATIONARY_AVG_N = 10      # its position is the average of the last N points, speed 0

ref_time_s = None          # GPS time (seconds) of the last accepted position
ref_lat = ref_lon = 0.0    # last accepted raw position (before averaging)
ref_speed_ms = 0.0
rejects = 0
stationary = []            # recent (lat, lon) while stopped
gga_altitude = None        # raw altitude of the last GGA


def distance_m(lat1, lon1, lat2, lon2):
	"""Equirectangular approximation: exact enough for a few km"""
	x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
	y = math.radians(lat2 - lat1)
	return 6371000 * math.sqrt(x * x + y * y)


def check_candidate(t_s, lat, lon, speed_ms):
	"""'' if the candidate is coherent with the last accepted position, else the reason"""
	if ref_time_s is None:
		return ''
	dt = t_s - ref_time_s
	if dt == 0:
		return 'same second'
	if dt < 0:
		return 'GPS time going back %d s' % int(-dt)
	d = distance_m(ref_lat, ref_lon, lat, lon)
	if d / dt > MAX_SPEED_KMH / 3.6:
		return 'jump of %d m in %d s (%d km/h)' % (int(d), int(dt), int(d / dt * 3.6))
	if dt > GAP_S:
		return ''
	tolerance = JUMP_BASE_M + JUMP_HDOP_M * (last_hdop or 5)
	expected = max(speed_ms, ref_speed_ms) * dt
	if d > expected + tolerance:
		return '%d m moved, %d m expected at %.1f km/h' % (int(d), int(expected), max(speed_ms, ref_speed_ms) * 3.6)
	accel = abs(speed_ms - ref_speed_ms) / dt
	if accel > MAX_ACCEL_MS2:
		return 'speed %.0f -> %.0f km/h in %d s' % (ref_speed_ms * 3.6, speed_ms * 3.6, int(dt))
	return ''


def accept(t_s, utc, lat, lon, speed_ms):
	"""Store the accepted candidate and update the published (possibly averaged) values"""
	global ref_time_s, ref_lat, ref_lon, ref_speed_ms, rejects, stationary
	global last_utc, last_lat, last_lon, last_speed_kmh, last_altitude, last_fix_ms
	dt = t_s - ref_time_s if ref_time_s is not None else 0
	ref_time_s, ref_lat, ref_lon, ref_speed_ms, rejects = t_s, lat, lon, speed_ms, 0
	last_utc = utc
	last_fix_ms = utime.ticks_ms()
	# stopped: average the last positions (GPS jitter of a few metres), speed 0
	if speed_ms * 3.6 < STATIONARY_KMH:
		stationary = (stationary + [(lat, lon)])[-STATIONARY_AVG_N:]
		last_lat = sum(p[0] for p in stationary) / len(stationary)
		last_lon = sum(p[1] for p in stationary) / len(stationary)
		last_speed_kmh = 0.0
	else:
		stationary = []
		last_lat, last_lon, last_speed_kmh = lat, lon, speed_ms * 3.6
	# altitude: keep the previous one if the new value climbs faster than possible
	if gga_altitude is not None:
		if last_altitude is None or dt <= 0 or \
				abs(gga_altitude - last_altitude) <= ALT_BASE_M + MAX_CLIMB_MS * dt:
			last_altitude = gga_altitude


def parse_rmc(line):
	"""$xxRMC,hhmmss.sss,A|V,lat,N|S,lon,E|W,speed(knots),course,ddmmyy,...
	A valid position is accepted only if check_candidate() finds it coherent."""
	global gps_fix, rejects, ref_time_s
	f = line.split(b',')
	gps_fix = len(f) > 9 and f[2] == b'A'
	if not gps_fix:
		return
	try:
		t, d = f[1].decode(), f[9].decode()
		utc = (2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]),
			int(t[0:2]), int(t[2:4]), int(t[4:6]))
		lat = nmea_to_degrees(f[3], f[4])
		lon = nmea_to_degrees(f[5], f[6])
		speed_ms = float(f[7].decode()) * 0.5144 if f[7] else 0.0  # knots -> m/s
	except (ValueError, IndexError):
		gps_fix = False
		return
	t_s = utime.mktime(utc + (0, 0))
	reason = check_candidate(t_s, lat, lon, speed_ms)
	if not reason:
		accept(t_s, utc, lat, lon, speed_ms)
		return
	if reason == 'same second':
		return  # same second sent twice: nothing to do (a time going back counts as a rejection)
	rejects += 1
	print('GPS position rejected (%d/%d): %s' % (rejects, RESET_AFTER_REJECTS, reason))
	if rejects >= RESET_AFTER_REJECTS:
		# several coherent-looking positions all disagree with the reference:
		# the reference was the outlier, start again from the current position
		print('GPS reference reset')
		ref_time_s = None
		accept(t_s, utc, lat, lon, speed_ms)


def parse_gga(line):
	"""$xxGGA,time,lat,N,lon,E,quality,sats,hdop,altitude,M,..."""
	global gga_altitude, sats_used, last_hdop
	f = line.split(b',')
	try:
		sats_used = int(f[7].decode()) if f[7] else 0
		last_hdop = float(f[8].decode()) if f[8] else None
		if f[6] != b'0' and f[9]:
			gga_altitude = float(f[9].decode())
	except (ValueError, IndexError):
		pass


# GSV talker -> constellation label (CASIC chips use BD or GB for BeiDou)
CONSTELLATIONS = {'GP': 'GP', 'BD': 'BD', 'GB': 'BD', 'GL': 'GL', 'GA': 'GA', 'GQ': 'GQ'}
sats_used = 0
sats_in_view = {}          # {'GP': 8, 'BD': 4} committed once per second
pending_sats_in_view = {}


def parse_gsv(line):
	"""$xxGSV,total_msgs,msg_num,sats_in_view,... one series per constellation"""
	f = line.split(b',')
	try:
		talker = CONSTELLATIONS.get(f[0][1:3].decode(), f[0][1:3].decode())
		pending_sats_in_view[talker] = int(f[3].decode())
	except (ValueError, IndexError):
		pass


def satellites_field():
	"""'7/12(GP8+BD4)': used in the fix / in view (per constellation)"""
	if not sats_in_view:
		return '%d/?' % sats_used
	total = sum(sats_in_view.values())
	detail = '+'.join('%s%d' % (k, sats_in_view[k]) for k in sorted(sats_in_view))
	return '%d/%d(%s)' % (sats_used, total, detail)


pending_fix_type = 1


def parse_gsa(line):
	"""$xxGSA,mode,fix(1/2/3),... one sentence per constellation: keep the best"""
	global pending_fix_type
	f = line.split(b',')
	try:
		pending_fix_type = max(pending_fix_type, int(f[2].decode()))
	except (ValueError, IndexError):
		pass


def read_nmea(uart, buf):
	"""Read UART, parse RMC, GSA, GGA and GSV sentences, return leftover partial line"""
	global fix_type, pending_fix_type, sats_in_view, pending_sats_in_view
	size = uart.any()
	if size:
		buf += uart.read(size)
		while b'\n' in buf:
			line, buf = buf.split(b'\n', 1)
			line = line.strip()
			# whatever the talker ($GN.. multi-GNSS, $GP.. GPS only)
			if not line.startswith(b'$'):
				continue
			kind = line[3:6]
			if kind == b'RMC':
				try:
					parse_rmc(line)
				except Exception as e:  # never let one bad sentence stop the tracker
					print('RMC error:', e, line)
				# one RMC per second: commit the best GSA fix type seen since the previous one
				fix_type, pending_fix_type = pending_fix_type, 1
				# GSV may be sent less often than RMC: keep the last complete set
				if pending_sats_in_view:
					sats_in_view, pending_sats_in_view = pending_sats_in_view, {}
			elif kind == b'GSA':
				parse_gsa(line)
			elif kind == b'GGA':
				parse_gga(line)
			elif kind == b'GSV':
				parse_gsv(line)
	return buf


def day_of_week(y, m, d):
	"""0 = Sunday (Sakamoto)"""
	t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
	if m < 3:
		y -= 1
	return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def france_time(utc):
	"""UTC tuple -> 'dd/mm/yyyy hh:mm:ss' Paris time (CEST from last Sunday of March
	01:00 UTC to last Sunday of October 01:00 UTC, CET otherwise)"""
	y, mo, d, h, mi, s = utc
	dst_start = (3, 31 - day_of_week(y, 3, 31), 1)
	dst_end = (10, 31 - day_of_week(y, 10, 31), 1)
	offset = 2 if dst_start <= (mo, d, h) < dst_end else 1
	t = utime.localtime(utime.mktime((y, mo, d, h, mi, s, 0, 0)) + offset * 3600)
	return '%02d/%02d/%04d %02d:%02d:%02d' % (t[2], t[1], t[0], t[3], t[4], t[5])


def battery_mv(samples=5):
	"""Average of a few readings: a 4G transmit burst can pull VBAT down by 50-150 mV"""
	from misc import Power
	total = 0
	for _ in range(samples):
		total += Power.getVbatt()
		utime.sleep_ms(20)
	return total // samples


def battery_percent(mv):
	v = mv / 1000.0
	if v >= VOLTAGE_TABLE[0][0]:
		return 100
	for (v_high, p_high), (v_low, p_low) in zip(VOLTAGE_TABLE, VOLTAGE_TABLE[1:]):
		if v_low <= v <= v_high:
			return int(p_low + (v - v_low) * (p_high - p_low) / (v_high - v_low))
	return 0


def battery_field():
	"""'100%(4165mV)'"""
	mv = battery_mv()
	return '%d%%(%dmV)' % (battery_percent(mv), mv)


def usb_plugged():
	from misc import USB
	return USB().getStatus() == 1


def battery_thread():
	"""Power the module off when the battery stays below LOW_BATTERY_MV.
	Ignored while USB is plugged (charging). LOW_BATTERY_CHECKS readings in a row
	are required so a single dip during a 4G transmission cannot shut it down."""
	from misc import Power
	low_count = 0
	while True:
		utime.sleep_ms(BATTERY_CHECK_MS)
		try:
			if usb_plugged():
				low_count = 0
				continue
			mv = battery_mv()
			low_count = low_count + 1 if mv < LOW_BATTERY_MV else 0
			if low_count:
				print('Low battery: %d mV (%d/%d)' % (mv, low_count, LOW_BATTERY_CHECKS))
			if low_count >= LOW_BATTERY_CHECKS:
				print('Battery below %d mV: powering off' % LOW_BATTERY_MV)
				Pin(ENABLE_PIN, Pin.OUT, Pin.PULL_DISABLE, 0)  # GPS off
				utime.sleep_ms(500)
				Power.powerDown()
		except Exception as e:
			print('Battery check error:', e)


def safe(fn, default='?'):
	try:
		return fn()
	except Exception:
		return default


def cell_signal():
	"""'4G -78dBm': access technology + RSSI of the serving cell"""
	import net
	tech = NET_TECH.get(net.getNetMode()[3], '?')
	gsm_wcdma, lte = net.getSignal()  # ([rssi, ber, rscp, ecio], [rssi, rsrp, rsrq, cqi(, sinr)])
	rssi = lte[0] if tech == '4G' else gsm_wcdma[0]
	return '%s %ddBm' % (tech, rssi)


def gps_fields():
	"""datefrance,posX,posY,altitude,vitesse (shared by both lines)"""
	return (
		france_time(last_utc),
		to_dms(last_lon, 'E', 'W'),   # X = longitude
		to_dms(last_lat, 'N', 'S'),   # Y = latitude
		'%.1fm' % last_altitude if last_altitude is not None else '?',
		'%.1fkm/h' % last_speed_kmh)


def build_gps_line():
	"""datefrance,posX,posY,altitude,vitesse,"""
	return '%s,%s,%s,%s,%s,' % gps_fields()


def build_line():
	"""datefrance,niveaubat,fix2Dou3D,posX,posY,altitude,vitesse,IP,operator,signal,telephone,satellites,"""
	import dataCall
	import net
	import sim
	date, x, y, alt, speed = gps_fields()
	return '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,' % (
		date,
		safe(battery_field),
		{2: '2D', 3: '3D'}.get(fix_type, '?'),
		x, y, alt, speed,
		safe(lambda: dataCall.getInfo(1, 0)[2][2]),
		safe(lambda: net.operatorName()[0]),
		safe(cell_signal),
		safe(sim.getPhoneNumber),
		safe(satellites_field))


def wait_network():
	"""Block until the cellular data link is up (at boot this can take a while)"""
	while True:
		stage, state = checkNet.waitNetworkReady(30)
		if stage == 3 and state == 1:
			print('Network ready')
			return
		print('Network not ready: stage=%d state=%d, retrying' % (stage, state))


# --- Button (module PWRKEY, read with misc.PowerKey) ---------------------------
# 1 click  : send the full line now (MANUAL)
# 2 clicks : SOS, sent even without GPS fix, retried until the server accepts it
# Both start the LED feedback (see led_thread). A press of about 10 s powers the
# board off in hardware: nothing is mapped on long presses.
DOUBLE_PRESS_MS = 500     # max gap between the two clicks of a double click
SOS_RETRY_MS = 10000

button = None             # PowerKey object, kept referenced so its callback stays alive
btn_down = False
btn_clicks = 0
btn_last_release = 0
send_now_requested = False
sos_requested = False


def on_button(status):
	"""PowerKey callback (1 = pressed, 0 = released): only counts clicks, run() does the work.
	A release without a press (sent once at registration) is ignored."""
	global btn_down, btn_clicks, btn_last_release
	if status == 1:
		btn_down = True
	elif btn_down:
		btn_down = False
		btn_clicks += 1
		btn_last_release = utime.ticks_ms()


def setup_button():
	global button
	try:
		from misc import PowerKey
		button = PowerKey()
		button.powerKeyEventRegister(on_button)
		print('Button ready: 1 click = send now, 2 clicks = SOS')
	except Exception as e:
		print('Button not available:', e)


def resolve_clicks():
	"""Called by run(): once DOUBLE_PRESS_MS has passed after the last release,
	1 click = send now, 2 clicks or more = SOS"""
	global btn_clicks, send_now_requested, sos_requested
	if not btn_clicks or utime.ticks_diff(utime.ticks_ms(), btn_last_release) < DOUBLE_PRESS_MS:
		return
	if btn_clicks >= 2:
		sos_requested = True
		print('SOS requested')
	else:
		send_now_requested = True
	btn_clicks = 0
	start_feedback()


def rtc_france_time():
	"""Module clock (set by the network, already Paris time) for an SOS without GPS fix"""
	t = utime.localtime()
	return '%02d/%02d/%04d %02d:%02d:%02d' % (t[2], t[1], t[0], t[3], t[4], t[5])


def build_sos_line():
	"""'SOS,' + full line; without GPS fix the position fields are '?' and the date
	comes from the module clock"""
	if has_fix():
		return 'SOS,' + build_line()
	import dataCall
	import net
	import sim
	return 'SOS,%s,%s,%s,?,?,?,?,%s,%s,%s,%s,%s,' % (
		rtc_france_time(),
		safe(battery_field),
		{2: '2D', 3: '3D'}.get(fix_type, '?'),
		safe(lambda: dataCall.getInfo(1, 0)[2][2]),
		safe(lambda: net.operatorName()[0]),
		safe(cell_signal),
		safe(sim.getPhoneNumber),
		safe(satellites_field))


def send_line(host, port, path, line):
	"""POST one line; returns True on HTTP 2xx"""
	try:
		status, _ = post_json(host, port, path, {'texte': line})
		print('HTTP %d: %s' % (status, line))
		return 200 <= status < 300
	except Exception as e:
		print('POST error:', e)
	return False


led_started = False


def run(config=None):
	"""config: dict from post_message.load_config() (read from /usr/config.json if None)"""
	global led_started, gps_fix, send_now_requested, sos_requested
	if config is None:
		config = load_config()
	host, port, path = config['server_host'], config['server_port'], config['server_path']
	gps_interval_ms = config['gps_interval_s'] * 1000    # short line: GPS position only
	full_interval_ms = config['full_interval_s'] * 1000  # full line: GPS + battery, network, phone
	if not led_started:  # run() may be restarted by main.py after a crash
		_thread.start_new_thread(led_thread, ())
		# started before wait_network(): must work even without 4G coverage
		_thread.start_new_thread(battery_thread, ())
		setup_button()
		led_started = True
	wait_network()
	uart = UART(NMEA_PORT, 9600, 8, 0, 1, 0)
	en_pin = Pin(ENABLE_PIN, Pin.OUT, Pin.PULL_DISABLE, 1)
	configure_gps(uart)
	print('GPS on, sending GPS line every %d ms and full line every %d ms to %s:%d%s' % (
		gps_interval_ms, full_interval_ms, host, port, path))
	buf = b''
	last_gps = last_full = utime.ticks_ms()
	last_sos_try = utime.ticks_add(last_gps, -SOS_RETRY_MS)  # first SOS attempt is immediate
	try:
		while True:
			buf = read_nmea(uart, buf)
			resolve_clicks()
			now = utime.ticks_ms()

			# SOS (2 clicks): sent with or without GPS fix, retried until the server accepts it
			# (sos_requested is global, so a pending SOS survives a restart of run();
			# the LED feedback lasts as long as it is pending)
			if sos_requested and utime.ticks_diff(now, last_sos_try) >= SOS_RETRY_MS:
				last_sos_try = now
				print('Sending SOS')
				if send_line(host, port, path, build_sos_line()):
					sos_requested = False
					end_feedback()
					flash_red(SOS_BLINKS)

			# 1 click: full line right away (quality filter skipped: explicit request),
			# prefixed with MANUAL so the server can tell it from the periodic ones
			if send_now_requested:
				send_now_requested = False
				if not has_fix():
					print('Send now: no GPS fix, nothing sent')
					end_feedback()
				else:
					ok = send_line(host, port, path, 'MANUAL,' + build_line())
					end_feedback()
					if ok:
						flash_red(SENT_BLINKS)
					last_gps = last_full = now

			full_due = utime.ticks_diff(now, last_full) >= full_interval_ms
			gps_due = utime.ticks_diff(now, last_gps) >= gps_interval_ms
			if full_due or gps_due:
				# the full line already contains the GPS fields: never send both at once
				last_gps = now
				if full_due:
					last_full = now
				good, reason = good_fix()
				if has_fix() and not good:
					print('Imprecise position (%s), nothing sent' % reason)
				elif has_fix():
					if send_line(host, port, path, build_line() if full_due else build_gps_line()):
						flash_red(SENT_BLINKS)
				else:
					print('No GPS fix, nothing sent')
			utime.sleep_ms(100)
	except KeyboardInterrupt:
		pass
	finally:
		en_pin.write(0)
		gps_fix = False
		print('GPS off')


if __name__ == '__main__':
	run()
