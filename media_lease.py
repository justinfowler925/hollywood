"""Cross-application media admission; shared by configured image service and Studio Media."""
from functools import wraps
import fcntl

def media_gpu_lease(function):
    @wraps(function)
    def admitted(*args,**kwargs):
        with open('/tmp/hollywood-gpu.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            return function(*args,**kwargs)
    return admitted
