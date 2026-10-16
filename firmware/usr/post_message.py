import ujson
import usocket
import checkNet

CONFIG_FILE = '/usr/config.json'
DEFAULT_CONFIG = {
	'server_host': 'unix.server.bsd',
	'server_port': 8080,
	'server_path': '/message',
}
MESSAGE = {'texte': 'Bonjour FreeBSD'}


def load_config():
	"""Read /usr/config.json; missing file or keys fall back to DEFAULT_CONFIG"""
	config = dict(DEFAULT_CONFIG)
	try:
		with open(CONFIG_FILE) as f:
			config.update(ujson.load(f))
	except OSError:
		print('%s not found, using defaults' % CONFIG_FILE)
	except ValueError as e:
		print('%s is not valid JSON (%s), using defaults' % (CONFIG_FILE, e))
	config['server_port'] = int(config['server_port'])
	return config


def post_json(host, port, path, data):
	"""HTTP POST of a JSON body, returns (status_code, body)"""
	# Content-Length counts bytes: non-ASCII chars like ° are 2 bytes in UTF-8
	body = ujson.dumps(data).encode()
	req = ('POST %s HTTP/1.1\r\n'
		'Host: %s:%d\r\n'
		'Content-Type: application/json; charset=utf-8\r\n'
		'Content-Length: %d\r\n'
		'Connection: close\r\n'
		'\r\n') % (path, host, port, len(body))

	addr = usocket.getaddrinfo(host, port)[0][-1]
	print('Connecting to', host, addr)
	s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
	try:
		# connect in blocking mode: with settimeout() before connect, a refused or
		# filtered port only shows up later as ENOTCONN on send()
		try:
			s.connect(addr)
		except Exception as e:
			raise Exception('TCP connect to %s:%d failed (%s): port closed or filtered from the internet?' % (addr[0], addr[1], e))
		print('TCP connected')
		s.settimeout(15)
		data = req.encode() + body
		sent = 0
		while sent < len(data):
			sent += s.send(data[sent:])
		print('Request sent (%d bytes)' % sent)
		resp = b''
		while True:
			try:
				chunk = s.recv(512)
			except OSError as e:
				# QuecPython raises ENOTCONN (107) instead of returning b'' once the
				# server has closed the connection after its response
				if e.args[0] == 107:
					break
				raise
			if not chunk:
				break
			resp += chunk
	finally:
		s.close()

	# MicroPython 1.13 bytes have no partition()
	sep = resp.find(b'\r\n\r\n')
	head = resp[:sep] if sep >= 0 else resp
	payload = resp[sep + 4:] if sep >= 0 else b''
	parts = head.split(b' ')
	status = int(parts[1]) if len(parts) > 1 else -1
	return status, payload.decode()


if __name__ == '__main__':
	stage, state = checkNet.waitNetworkReady(30)
	if stage == 3 and state == 1:
		try:
			cfg = load_config()
			status, body = post_json(cfg['server_host'], cfg['server_port'], cfg['server_path'], MESSAGE)
			print('HTTP', status)
			print(body)
		except Exception as e:
			print('POST error:', e)
	else:
		print('Network not ready: stage=%d state=%d' % (stage, state))
