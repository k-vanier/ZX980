# /usr/main.py is executed by QuecPython at boot.
# Needs /usr/gps_post.py and /usr/post_message.py.
import _thread
import utime
from usr import gps_post


def worker():
	while True:
		try:
			gps_post.run()
		except Exception as e:
			print('gps_post crashed:', e)
		# never stop sending: restart after any crash
		utime.sleep(10)


# Run in a thread so the REPL stays available (QPYcom can still update files)
_thread.start_new_thread(worker, ())
