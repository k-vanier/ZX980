import utime
import _thread
import checkNet
from machine import Pin, UART
from usr.post_message import post_json

HOST = 'unix.server.bsd'
PORT = 8080
PATH = '/message'
SEND_INTERVAL_MS = 60000

# ZX908 (EC800N / EC800M): GPS AT6558 power on GPIO10 (pin54), NMEA on UART2 (pin17 RX / pin18 TX)
NMEA_PORT = UART.UART2
ENABLE_PIN = Pin.GPIO10
LED_RED = Pin.GPIO15     # pin80: double blink after each message sent
LED_YELLOW = Pin.GPIO17  # pin76: fast blink while the GPS has a fix
FIX_BLINK_MS = 125       # yellow on/off time with a fix (4 Hz)
FIX_TIMEOUT_MS = 5000    # no valid RMC for this long = fix lost

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
fix_type = 1       # GSA: 1 = no fix, 2 = 2D, 3 = 3D

# net.getNetMode()[3] (access technology) -> label
NET_TECH = {0: '2G', 1: '2G', 2: '3G', 3: '2G', 4: '3G', 5: '3G', 6: '3G', 7: '4G', 9: '4G'}


def led_thread():
	"""Yellow LED blinks fast while the GPS has a fix, off otherwise"""
	led = Pin(LED_YELLOW, Pin.OUT, Pin.PULL_DISABLE, 0)
	while True:
		if has_fix():
			led.write(1)
			utime.sleep_ms(FIX_BLINK_MS)
			led.write(0)
			utime.sleep_ms(FIX_BLINK_MS)
		else:
			led.write(0)
			utime.sleep_ms(200)


def blink_sent(led):
	"""Red LED: two quick flashes = message accepted by the server"""
	for _ in range(2):
		led.write(1)
		utime.sleep_ms(80)
		led.write(0)
		utime.sleep_ms(120)


def has_fix():
	return gps_fix and utime.ticks_diff(utime.ticks_ms(), last_fix_ms) < FIX_TIMEOUT_MS


def nmea_to_degrees(value, hemisphere):
	"""NMEA ddmm.mmmm / dddmm.mmmm -> signed decimal degrees"""
	v = float(value.decode())
	deg = int(v / 100)
	deg += (v - deg * 100) / 60
	return -deg if hemisphere in (b'S', b'W') else deg


def to_dms(deg, pos, neg):
	"""Decimal degrees -> 49°08'48.0"N (rounded to 0.1 s without 60.0 s carry issues)"""
	tenths = int(abs(deg) * 36000 + 0.5)
	d = tenths // 36000
	m = (tenths % 36000) // 600
	s = tenths % 600
	return '%d°%d\'%d.%d"%s' % (d, m, s // 10, s % 10, neg if deg < 0 else pos)


def parse_rmc(line):
	"""$xxRMC,hhmmss.sss,A|V,lat,N|S,lon,E|W,speed(knots),course,ddmmyy,..."""
	global gps_fix, last_fix_ms, last_utc, last_lat, last_lon, last_speed_kmh
	f = line.split(b',')
	gps_fix = len(f) > 9 and f[2] == b'A'
	if not gps_fix:
		return
	try:
		t, d = f[1].decode(), f[9].decode()
		last_utc = (2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]),
			int(t[0:2]), int(t[2:4]), int(t[4:6]))
		last_lat = nmea_to_degrees(f[3], f[4])
		last_lon = nmea_to_degrees(f[5], f[6])
		last_speed_kmh = float(f[7].decode()) * 1.852 if f[7] else 0.0
		last_fix_ms = utime.ticks_ms()
	except (ValueError, IndexError):
		gps_fix = False


def parse_gga(line):
	"""$xxGGA,time,lat,N,lon,E,quality,sats,hdop,altitude,M,..."""
	global last_altitude
	f = line.split(b',')
	try:
		if f[6] != b'0' and f[9]:
			last_altitude = float(f[9].decode())
	except (ValueError, IndexError):
		pass


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
	"""Read UART, parse RMC and GSA sentences, return leftover partial line"""
	global fix_type, pending_fix_type
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
				parse_rmc(line)
				# one RMC per second: commit the best GSA fix type seen since the previous one
				fix_type, pending_fix_type = pending_fix_type, 1
			elif kind == b'GSA':
				parse_gsa(line)
			elif kind == b'GGA':
				parse_gga(line)
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


def battery_percent():
	from misc import Power
	v = Power.getVbatt() / 1000.0
	if v >= VOLTAGE_TABLE[0][0]:
		return 100
	for (v_high, p_high), (v_low, p_low) in zip(VOLTAGE_TABLE, VOLTAGE_TABLE[1:]):
		if v_low <= v <= v_high:
			return int(p_low + (v - v_low) * (p_high - p_low) / (v_high - v_low))
	return 0


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


def build_line():
	"""datefrance,niveaubat,fix2Dou3D,posX,posY,altitude,vitesse,IP,operator,signal,telephone,"""
	import dataCall
	import net
	import sim
	return '%s,%s%%,%s,%s,%s,%s,%.1fkm/h,%s,%s,%s,%s,' % (
		france_time(last_utc),
		safe(battery_percent),
		{2: '2D', 3: '3D'}.get(fix_type, '?'),
		to_dms(last_lon, 'E', 'W'),   # X = longitude
		to_dms(last_lat, 'N', 'S'),   # Y = latitude
		'%.1fm' % last_altitude if last_altitude is not None else '?',
		last_speed_kmh,
		safe(lambda: dataCall.getInfo(1, 0)[2][2]),
		safe(lambda: net.operatorName()[0]),
		safe(cell_signal),
		safe(sim.getPhoneNumber))


def wait_network():
	"""Block until the cellular data link is up (at boot this can take a while)"""
	while True:
		stage, state = checkNet.waitNetworkReady(30)
		if stage == 3 and state == 1:
			print('Network ready')
			return
		print('Network not ready: stage=%d state=%d, retrying' % (stage, state))


led_started = False


def run():
	global led_started, gps_fix
	if not led_started:  # run() may be restarted by main.py after a crash
		_thread.start_new_thread(led_thread, ())
		led_started = True
	wait_network()
	uart = UART(NMEA_PORT, 9600, 8, 0, 1, 0)
	en_pin = Pin(ENABLE_PIN, Pin.OUT, Pin.PULL_DISABLE, 1)
	red_led = Pin(LED_RED, Pin.OUT, Pin.PULL_DISABLE, 0)
	print('GPS on, sending position every %d ms to %s:%d%s' % (SEND_INTERVAL_MS, HOST, PORT, PATH))
	buf = b''
	last_send = utime.ticks_ms()
	try:
		while True:
			buf = read_nmea(uart, buf)
			if utime.ticks_diff(utime.ticks_ms(), last_send) >= SEND_INTERVAL_MS:
				last_send = utime.ticks_ms()
				if has_fix():
					try:
						line = build_line()
						status, _ = post_json(HOST, PORT, PATH, {'texte': line})
						print('HTTP %d: %s' % (status, line))
						if 200 <= status < 300:
							blink_sent(red_led)
					except Exception as e:
						print('POST error:', e)
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
