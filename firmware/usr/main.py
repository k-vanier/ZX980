# /usr/main.py is executed by QuecPython at boot.
# Needs /usr/gps_post.py, /usr/post_message.py and /usr/config.json.
import _thread
import utime
from usr import gps_post
from usr.post_message import load_config

if not hasattr(gps_post, 'run'):
	raise ImportError('/usr/gps_post.py is an old version without run(): copy the latest one')

config = load_config()
print('Server: %s:%d%s' % (config['server_host'], config['server_port'], config['server_path']))


def worker():
	while True:
		try:
			gps_post.run(config)
		except Exception as e:
			print('gps_post crashed:', e)
		# never stop sending: restart after any crash
		utime.sleep(10)


# Run in a thread so the REPL stays available (QPYcom can still update files)
_thread.start_new_thread(worker, ())
