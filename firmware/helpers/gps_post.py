import utime
import _thread
import checkNet
from machine import Pin, UART
from usr.post_message import post_json

HOST = 'unix.server.bsd'
PORT = 8080
PATH = '/message'
AUTHOR = 'zx908'
SEND_INTERVAL_MS = 60000

# ZX908 (EC800N / EC800M): GPS AT6558 power on GPIO10 (pin54), NMEA on UART2 (pin17 RX / pin18 TX)
NMEA_PORT = UART.UART2
ENABLE_PIN = Pin.GPIO10
LED_YELLOW = Pin.GPIO17  # pin76
FIX_TIMEOUT_MS = 5000    # no valid RMC for this long = fix lost

# Updated by run(), read by the LED thread
gps_fix = False
last_fix_ms = 0
last_position = None  # latest valid fix: 'YYYY-MM-DD hh:mm:ss UTC 49°08'48.0"N 0°13'36.0"E'


def led_thread():
	"""Yellow LED blinks at 1 Hz while the GPS has a fix, off otherwise"""
	led = Pin(LED_YELLOW, Pin.OUT, Pin.PULL_DISABLE, 0)
	while True:
		if has_fix():
			led.write(1)
			utime.sleep_ms(500)
			led.write(0)
			utime.sleep_ms(500)
		else:
			led.write(0)
			utime.sleep_ms(200)


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
	"""$xxRMC,hhmmss.sss,A|V,lat,N|S,lon,E|W,speed,course,ddmmyy,..."""
	global gps_fix, last_fix_ms, last_position
	f = line.split(b',')
	gps_fix = len(f) > 9 and f[2] == b'A'
	if not gps_fix:
		return
	try:
		t, d = f[1], f[9]
		lat = nmea_to_degrees(f[3], f[4])
		lon = nmea_to_degrees(f[5], f[6])
		last_position = '20%s-%s-%s %s:%s:%s UTC %s %s' % (
			d[4:6].decode(), d[2:4].decode(), d[0:2].decode(),
			t[0:2].decode(), t[2:4].decode(), t[4:6].decode(),
			to_dms(lat, 'N', 'S'), to_dms(lon, 'E', 'W'))
		last_fix_ms = utime.ticks_ms()
	except (ValueError, IndexError):
		gps_fix = False


def read_nmea(uart, buf):
	"""Read UART, parse RMC sentences, return leftover partial line"""
	size = uart.any()
	if size:
		buf += uart.read(size)
		while b'\n' in buf:
			line, buf = buf.split(b'\n', 1)
			line = line.strip()
			# whatever the talker ($GN.. multi-GNSS, $GP.. GPS only)
			if line.startswith(b'$') and line[3:6] == b'RMC':
				parse_rmc(line)
	return buf


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
						status, _ = post_json(HOST, PORT, PATH, {'auteur': AUTHOR, 'texte': last_position})
						print('HTTP %d: %s' % (status, last_position))
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
