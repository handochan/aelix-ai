# r2verify: a python start-up the runner made slow; logs when it woke (a kill that missed it).
import os
import time

_s = os.environ.pop("V446_SITE_SLEEP", None)
if _s:
    time.sleep(float(_s))
    _log = os.environ.get("V446_LOG")
    if _log:
        with open(_log, "a", encoding="utf-8") as _h:
            _h.write(f"python root pid={os.getpid()} woke after {_s}s at t={time.monotonic():.3f}: SURVIVED the timeout leg\n")
