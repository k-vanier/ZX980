import utime
import wifiScan
from machine import Pin, UART

# ZX908 (EC800N / EC800M): GPS AT6558 power on GPIO10 (pin54), NMEA on UART2 (pin17 RX / pin18 TX)
NMEA_PORT = UART.UART2
ENABLE_PIN = Pin.GPIO10
GPS_DURATION = 0  # seconds of NMEA output, 0 = forever (Ctrl+C to stop)


def scan_wifi():
	print('=== WiFi scan ===')
	if wifiScan.control(1) != 0:
		print('WiFi scan enable failed')
		return
	try:
		ret = wifiScan.start()  # blocking: (count, [(mac, rssi), ...]) or -1
		if ret == -1 or not ret:
			print('WiFi scan failed:', ret)
			return
		count, aps = ret
		print('%d access point(s) found' % count)
		for mac, rssi in sorted(aps, key=lambda ap: ap[1], reverse=True):
			print('  MAC=%s  RSSI=%d dBm' % (mac, rssi))
	finally:
		wifiScan.control(0)


def show_gps():
	print('=== GPS NMEA (UART2 9600) ===')
	uart = UART(NMEA_PORT, 9600, 8, 0, 1, 0)
	en_pin = Pin(ENABLE_PIN, Pin.OUT, Pin.PULL_DISABLE, 1)
	buf = b''
	start = utime.time()
	try:
		while GPS_DURATION == 0 or utime.time() - start < GPS_DURATION:
			size = uart.any()
			if size:
				buf += uart.read(size)
				while b'\n' in buf:
					line, buf = buf.split(b'\n', 1)
					line = line.strip()
					if line.startswith(b'$'):
						print(line.decode())
			else:
				utime.sleep_ms(100)
	except KeyboardInterrupt:
		pass
	finally:
		en_pin.write(0)
		print('GPS off')


if __name__ == '__main__':
	scan_wifi()
	show_gps()
