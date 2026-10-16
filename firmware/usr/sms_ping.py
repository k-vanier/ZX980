import utime
import checkNet
import dataCall

PHONE = '+33600000000'  # numéro destinataire, format international
TEXT = 'Test ZX908 EC800M'
PING_HOST = 'google.com'
APN = 'free'  # Free Mobile: no user / password
PDP_PROFILE = 1


def setup_apn():
	"""Set APN on PDP profile 1 (saved in flash, so only written when it differs)"""
	ctx = dataCall.getPDPContext(PDP_PROFILE)  # (ipType, apn, user, password, authType) or -1
	print('Current PDP context:', ctx)
	if ctx != -1 and ctx[1] == APN:
		return
	ret = dataCall.setPDPContext(PDP_PROFILE, 0, APN, '', '', 0)
	print('Set APN "%s" ->' % APN, 'OK' if ret == 0 else 'FAILED')
	ret = dataCall.activate(PDP_PROFILE)
	print('Activate PDP ->', 'OK' if ret == 0 else 'FAILED')


def wait_network():
	print('=== Network ===')
	setup_apn()
	stage, state = checkNet.waitNetworkReady(30)
	if stage == 3 and state == 1:
		print('Network ready, IP info:', dataCall.getInfo(PDP_PROFILE, 0))
		return True
	print('Network not ready: stage=%d state=%d' % (stage, state))
	return False


def send_sms_module():
	import sms  # absent in QPY_OCPU_V0005_EC800M_CNLE_FW
	ret = sms.sendTextMsg(PHONE, TEXT, 'GSM')
	print('sms.sendTextMsg ->', ret, '(OK)' if ret >= 0 else '(FAILED)')


def send_sms_at():
	# Fallback when the firmware has no sms module. Quectel says SMS AT commands
	# are often stripped from QuecPython firmwares, so this may return ERROR.
	import atcmd
	resp = bytearray(128)
	ret = atcmd.sendSync('AT+CMGF=1\r\n', resp, '', 5)
	print('AT+CMGF=1 ->', ret, bytes(resp).rstrip(b'\x00'))
	resp = bytearray(128)
	ret = atcmd.sendSync('AT+CMGS="%s"\r\n' % PHONE, resp, '>', 5)
	print('AT+CMGS ->', ret, bytes(resp).rstrip(b'\x00'))
	if ret != 0:
		return
	resp = bytearray(128)
	ret = atcmd.sendSync(TEXT + '\x1a', resp, '+CMGS', 60)
	print('SMS body ->', ret, bytes(resp).rstrip(b'\x00'))


def send_sms():
	print('=== SMS to %s ===' % PHONE)
	try:
		send_sms_module()
		return
	except ImportError:
		print('No sms module, trying AT commands')
	except Exception as e:
		print('sms module error:', e)
		return
	try:
		send_sms_at()
	except ImportError:
		print('No atcmd module either: SMS not possible with this firmware')
	except Exception as e:
		print('AT SMS error:', e)


def ping():
	print('=== Ping %s ===' % PING_HOST)
	try:
		import uping
		res = uping.ping(PING_HOST, COUNT=4, quiet=True)
		print(res)
		return
	except ImportError:
		print('No uping module, using DNS + TCP connect instead')
	except Exception as e:
		print('uping error:', e)
		return
	import usocket
	try:
		addr = usocket.getaddrinfo(PING_HOST, 80)[0][-1]
		print('DNS', PING_HOST, '->', addr)
		for i in range(4):
			s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
			s.settimeout(5)
			t0 = utime.ticks_ms()
			try:
				s.connect(addr)
				print('TCP connect #%d: %d ms' % (i + 1, utime.ticks_diff(utime.ticks_ms(), t0)))
			except Exception as e:
				print('TCP connect #%d failed: %s' % (i + 1, e))
			finally:
				s.close()
			utime.sleep(1)
	except Exception as e:
		print('Ping error:', e)


if __name__ == '__main__':
	net_ok = wait_network()
	send_sms()
	if net_ok:
		ping()
