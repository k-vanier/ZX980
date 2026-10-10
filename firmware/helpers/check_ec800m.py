import utime
import modem
import uos
from machine import Pin, UART

# Run this on the device before deploying /usr to check that the module and its
# QuecPython firmware provide everything the tracker uses.

print('Model   :', modem.getDevModel())
print('Firmware:', modem.getDevFwVersion())
print('uname   :', uos.uname())

# Modules imported by /usr/*.py
for name in ('gnss', 'wifiScan', 'sms', 'dataCall', 'checkNet', 'net', 'ntptime', 'usocket', 'misc'):
	try:
		__import__(name)
		print('[OK]  module', name)
	except ImportError as e:
		print('[ERR] module', name, e)

# LEDs: GPIO15 (pin80) red, GPIO16 (pin81) blue, GPIO17 (pin76) yellow
for label, gpio in (('red', Pin.GPIO15), ('blue', Pin.GPIO16), ('yellow', Pin.GPIO17)):
	p = Pin(gpio, Pin.OUT, Pin.PULL_DISABLE, 1)
	print('LED', label, 'ON')
	utime.sleep(1)
	p.write(0)

# GPS: power on GPIO10 (pin54), NMEA on UART2 (pin17 RX / pin18 TX) at 9600
en = Pin(Pin.GPIO10, Pin.OUT, Pin.PULL_DISABLE, 1)
uart = UART(UART.UART2, 9600, 8, 0, 1, 0)
utime.sleep(3)
n = uart.any()
print('GPS bytes received in 3s:', n)
if n:
	print(uart.read(min(n, 200)))
en.write(0)
