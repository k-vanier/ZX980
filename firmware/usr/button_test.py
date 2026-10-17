# Finds how the ZX908 button reaches the EC800M.
# 1. misc.PowerKey: events of the module's PWRKEY input (PWR_BTN pad)
# 2. every free GPIO read as an input, with pull-up then pull-down
#
# Rename /usr/main.py and reboot first, so gps_post is not running.
# Press the button SHORTLY (under 2 s): a press of 10 s or more powers the board off.
import utime
from machine import Pin

PHASE_S = 15  # seconds per GPIO phase

# Pins NOT reconfigured: GPS power (10), LEDs (15, 16, 17), I2C0 (23, 24),
# GPS UART2 (25, 26), debug UART0 (34, 35)
SKIP = (10, 15, 16, 17, 23, 24, 25, 26, 34, 35)


def test_powerkey():
	print('=== 1. PowerKey (PWRKEY of the module) ===')
	try:
		from misc import PowerKey
	except ImportError as e:
		print('  PowerKey not available:', e)
		return None

	def on_key(status):
		print('  >>> PowerKey event: %s (status=%s)' % ('PRESSED' if status == 1 else 'RELEASED', status))

	try:
		pk = PowerKey()
		ret = pk.powerKeyEventRegister(on_key)
		print('  callback registered (ret=%s): press the button now, events appear below' % ret)
		return pk  # keep a reference so the callback stays alive
	except Exception as e:
		print('  PowerKey error:', e)
		return None


def free_gpios():
	pins = []
	for n in range(1, 48):
		if n in SKIP:
			continue
		gpio = getattr(Pin, 'GPIO%d' % n, None)
		if gpio is not None:
			pins.append((n, gpio))
	return pins


def watch_gpios(pull, label):
	print('=== GPIO inputs with %s: press the button several times (%d s) ===' % (label, PHASE_S))
	inputs = []
	for n, gpio in free_gpios():
		try:
			inputs.append((n, Pin(gpio, Pin.IN, pull)))
		except Exception as e:
			print('  GPIO%d cannot be used as input: %s' % (n, e))
	state = {n: p.read() for n, p in inputs}
	print('  watching %d pins, idle levels: %s' % (len(inputs),
		' '.join('%d=%d' % (n, state[n]) for n, _ in inputs)))
	changes = {}
	end = utime.ticks_add(utime.ticks_ms(), PHASE_S * 1000)
	while utime.ticks_diff(end, utime.ticks_ms()) > 0:
		for n, p in inputs:
			v = p.read()
			if v != state[n]:
				state[n] = v
				changes[n] = changes.get(n, 0) + 1
				print('  >>> GPIO%d -> %d' % (n, v))
		utime.sleep_ms(10)
	return changes


if __name__ == '__main__':
	pk = test_powerkey()
	print()
	results = {}
	for pull, label in ((Pin.PULL_PU, 'pull-up'), (Pin.PULL_PD, 'pull-down')):
		results[label] = watch_gpios(pull, label)
		print()
	print('=== Summary ===')
	found = False
	for label, changes in results.items():
		for n, count in changes.items():
			found = True
			print('  GPIO%d changed %d times with %s' % (n, count, label))
	if not found:
		print('  No GPIO changed: if PowerKey events were printed above, the button is the')
		print('  module PWRKEY (use misc.PowerKey); otherwise it only drives the power circuit.')
