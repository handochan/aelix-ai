"""Guarded product sabotage for the verify pass (throwaway tree / measure branch only).

Inert unless V446_LABEL_FILE / V446_NOTERM is set in the process env:
  V446_LABEL_FILE: a counter file; while it holds N>0 the site, after an ORDINARY
                   exit, labels the call timed out (kills, returns None) - a
                   timed_out=True the timed wait never raised.
  V446_NOTERM=1:   the site hands _exit_drain_cap timeout=None (the deadline term gone).
"""
import sys
from pathlib import Path
p = Path(sys.argv[1]) / "packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py"
s = p.read_text(encoding="utf-8")
old1 = "            try:\n                return await asyncio.to_thread(_wait_and_stamp)\n            except subprocess.TimeoutExpired:\n"
new1 = ("            try:\n                _v446_code = await asyncio.to_thread(_wait_and_stamp)\n"
        "                if _v446_label_once():\n"
        "                    _timed_out = True\n"
        "                    _end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)\n"
        "                    _mark_the_kill()\n"
        "                    return None\n"
        "                return _v446_code\n"
        "            except subprocess.TimeoutExpired:\n")
assert s.count(old1) == 1
s = s.replace(old1, new1)
old2 = "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=timeout)"
assert s.count(old2) == 1, s.count(old2)
s = s.replace(old2, "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=None if __import__('os').environ.get('V446_NOTERM') else timeout)")
helper = '''

def _v446_label_once() -> bool:
    import os as _os

    path = _os.environ.get("V446_LABEL_FILE")
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            n = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return False
    if n <= 0:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(n - 1))
    return True
'''
s = s.rstrip("\n") + "\n" + helper
p.write_text(s, encoding="utf-8")
print("mutated", p)
