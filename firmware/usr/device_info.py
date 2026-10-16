import gc
import uos
import utime
from machine import Pin, UART

# Prints everything the ZX908 (EC800M) can report. Every call is wrapped:
# a function missing from the firmware prints "n/a" instead of stopping the script.

NMEA_PORT = UART.UART2
ENABLE_PIN = Pin.GPIO10
GPS_LISTEN_S = 5


def show(label, fn, *args):
	try:
		print('  %-22s %s' % (label, fn(*args)))
	except Exception as e:
		print('  %-22s n/a (%s)' % (label, e))


def module_info():
	import modem
	print('== Module ==')
	show('Model', modem.getDevModel)
	show('IMEI', modem.getDevImei)
	show('Serial number', modem.getDevSN)
	show('Firmware', modem.getDevFwVersion)
	show('QuecPython', lambda: uos.uname())
	show('RAM free (bytes)', gc.mem_free)
	show('/usr free (bytes)', lambda: uos.statvfs('/usr')[0] * uos.statvfs('/usr')[3])
	show('Uptime (ms)', utime.ticks_ms)
	show('Local time', utime.localtime)


def power_info():
	from misc import Power, USB
	print('== Power ==')
	show('Battery (mV)', Power.getVbatt)
	show('USB (1 = plugged)', lambda: USB().getStatus())
	show('Power-on reason', Power.powerOnReason)
	show('Last power-down', Power.powerDownReason)


def sim_info():
	import sim
	print('== SIM ==')
	show('Status (1 = ready)', sim.getStatus)
	show('IMSI', sim.getImsi)
	show('ICCID', sim.getIccid)
	show('Phone number', sim.getPhoneNumber)


def network_info():
	import net
	import dataCall
	print('== Cellular network ==')
	show('Operator', net.operatorName)
	show('CSQ (0-31, 99=?)', net.csqQueryPoll)
	show('Signal', net.getSignal)        # ([GSM/WCDMA...], [LTE rssi, rsrp, rsrq, cqi(, sinr)])
	show('Registration', net.getState)
	show('Network mode', net.getNetMode)
	show('Config', net.getConfig)
	show('Cell ID', net.getServingCi)
	show('LAC / TAC', net.getServingLac)
	# returned as BCD read in hex: 0x208 -> printed 520, so show it in hex
	show('MCC', lambda: '%x' % net.getServingMcc())
	show('MNC', lambda: '%02x' % net.getServingMnc())
	show('Cells (serving+neigh.)', net.getCellInfo)
	show('Network time (NITZ)', net.nitzTime)
	show('APN profile 1', dataCall.getPDPContext, 1)
	show('IP / DNS', dataCall.getInfo, 1, 0)


def wifi_info():
	import wifiScan
	print('== WiFi scan ==')
	try:
		wifiScan.control(1)
		ret = wifiScan.start()
		wifiScan.control(0)
		if ret == -1 or not ret:
			print('  scan failed:', ret)
			return
		count, aps = ret
		print('  %d access point(s)' % count)
		for mac, rssi in sorted(aps, key=lambda ap: ap[1], reverse=True):
			print('    %s  %d dBm' % (mac, rssi))
	except Exception as e:
		print('  n/a (%s)' % e)


def gps_info():
	print('== GPS (AT6558, listening %d s) ==' % GPS_LISTEN_S)
	uart = UART(NMEA_PORT, 9600, 8, 0, 1, 0)
	en = Pin(ENABLE_PIN, Pin.OUT, Pin.PULL_DISABLE, 1)
	buf = b''
	last = {}
	sats = {}
	end = utime.ticks_add(utime.ticks_ms(), GPS_LISTEN_S * 1000)
	while utime.ticks_diff(end, utime.ticks_ms()) > 0:
		n = uart.any()
		if n:
			buf += uart.read(n)
			while b'\n' in buf:
				line, buf = buf.split(b'\n', 1)
				line = line.strip().split(b'*')[0]
				if not line.startswith(b'$'):
					continue
				f = line.decode().split(',')
				kind = f[0][3:]
				if kind == 'GSV':
					# $xxGSV,total,num,in_view,(prn,elev,azim,snr)*
					for i in range(4, len(f) - 3, 4):
						sats[f[0][1:3] + f[i]] = (f[i + 1], f[i + 2], f[i + 3] or '-')
				elif kind == 'TXT':
					last.setdefault('TXT', []).append(f[-1])
				else:
					last[kind] = f
		else:
			utime.sleep_ms(50)
	# GPS left powered on: /usr/main.py (gps_post) may be using it at the same time

	rmc, gga, gsa = last.get('RMC'), last.get('GGA'), last.get('GSA')
	if rmc and len(rmc) > 9:
		print('  Status (RMC)           %s' % ('A = fix' if rmc[2] == 'A' else 'V = no fix'))
		print('  UTC time / date        %s / %s' % (rmc[1], rmc[9]))
		print('  Lat / Lon (NMEA)       %s %s / %s %s' % (rmc[3], rmc[4], rmc[5], rmc[6]))
		print('  Speed (knots)          %s' % rmc[7])
		print('  Course (deg)           %s' % rmc[8])
	if gga and len(gga) > 11:
		print('  Fix quality (GGA)      %s (0 none, 1 GPS, 2 DGPS, 6 estimated)' % gga[6])
		print('  Satellites used        %s' % gga[7])
		print('  HDOP                   %s' % gga[8])
		print('  Altitude (m)           %s' % gga[9])
		print('  Geoid separation (m)   %s' % gga[11])
	if gsa and len(gsa) > 17:
		print('  Fix type (GSA)         %s (1 none, 2 2D, 3 3D)' % gsa[2])
		print('  PDOP / HDOP / VDOP     %s / %s / %s' % (gsa[15], gsa[16], gsa[17]))
	print('  Satellites in view     %d' % len(sats))
	for key in sorted(sats):
		elev, azim, snr = sats[key]
		print('    %s PRN %-4s elev %-3s azim %-3s SNR %s dB-Hz' % (key[:2], key[2:], elev, azim, snr))
	for txt in last.get('TXT', []):
		print('  Chip info (TXT)        %s' % txt)


if __name__ == '__main__':
	for section in (module_info, power_info, sim_info, network_info, wifi_info, gps_info):
		try:
			section()
		except Exception as e:
			print('  section error:', e)
