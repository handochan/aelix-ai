"""#446 sabotage mutator (v2): python sab446.py <tree> <mutation>[+<mutation>...] | --list | --restore.

Applies to a throwaway tree whose ``*.pristine`` copies are the FIX (test) and
e24ea2e8 (product). Product mutations (P_*) are defect classes near 8a's new
branch; ``*_once`` ones fire on the FIRST timed call only (a file named by
$SAB446_ONCE, consumed), which is the shape a retry could hide. Test
mutations: T_* put an attempt into a premise shape (not a defect); W_* are
wrong forms of the #446 change; BASE swaps the whole test back to e24ea2e8's.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BASH = "packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py"
TEST = "tests/tools/test_bash_tool_containment.py"
BASE_TEST = os.environ.get("SAB446_BASE_TEST", "/tmp/446-work/impl/edit/test_e24ea2e8.py")

ONCE = (
    "(lambda _o: bool(_o) and __import__('os').path.exists(_o) and "
    "(__import__('os').remove(_o) or True))(__import__('os').environ.get('SAB446_ONCE'))"
)

M = {
    # --- product defects ---
    # The site hands proc.wait half the caller's deadline (every call).
    "P_early": (BASH, "code = proc.wait(timeout)", "code = proc.wait(None if timeout is None else timeout * 0.5)"),
    # First call: a co-waiter holds _waitpid_lock past the deadline, so the timed
    # wait cannot see the root's exit (POSIX) - TimeoutExpired on a zombie root.
    "P_coawait_once": (
        BASH,
        "code = proc.wait(timeout)",
        "if timeout is not None and " + ONCE + ":\n"
        "                    proc._waitpid_lock.acquire()\n"
        "                    __import__('threading').Timer(timeout + 0.3, proc._waitpid_lock.release).start()\n"
        "                code = proc.wait(timeout)",
    ),
    # First call: the label without the wait - sleeps the deadline and raises
    # TimeoutExpired itself, never asking proc.wait.
    "P_label_once": (
        BASH,
        "code = proc.wait(timeout)",
        "if timeout is not None and " + ONCE + ":\n"
        "                    time.sleep(timeout)\n"
        "                    raise subprocess.TimeoutExpired(proc.args, timeout)\n"
        "                code = proc.wait(timeout)",
    ),
    # The timeout leg's kill reaches nothing (no hard_kill, no belt; reap only).
    "P_nokill": (
        BASH,
        "_end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)",
        "with contextlib.suppress(subprocess.TimeoutExpired):\n"
        "                    proc.wait(timeout=REAP_GRACE_SECONDS)",
    ),
    # The timeout leg's kill reaches the ROOT only, not its group/job.
    "P_rootonly": (
        BASH,
        "_end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)",
        "proc.kill()\n"
        "                with contextlib.suppress(subprocess.TimeoutExpired):\n"
        "                    proc.wait(timeout=REAP_GRACE_SECONDS)",
    ),
    # The exit path's drain cap never fires.
    "P_nocap": (
        BASH,
        "await _drain_after_the_exit(exited_at, cap=cap, hard_cap=exited_at + DRAIN_CAP_SECONDS)",
        "await _drain_after_the_exit(exited_at, cap=float('inf'), hard_cap=float('inf'))",
    ),
    # 8a's docstring mutation: the ordinary exit stamped as a kill.
    "P_exitstamp": (
        BASH,
        "                _root_exited_at = time.monotonic()\n",
        "                _root_exited_at = time.monotonic()\n                state.exited_at = _root_exited_at\n",
    ),
    # timed_out on every call.
    "P_label_always": (BASH, "timed_out=_timed_out and not _signal_aborted,", "timed_out=True,"),
    # The deadline term dropped from the exit drain's cap.
    "P_noterm": (
        BASH,
        "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=timeout)",
        "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=None)",
    ),
    # --- premise shapes (test side, not defects) ---
    # Attempt 1's python root holds 30 s (past the kill check's 5 s poll), so it is alive and announced at the deadline.
    "T_hold1": (
        TEST,
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=0.0)\n",
        "        command = _holder_command(\n"
        "            marker.parent, marker, tick=0.05, hold=30.0 if attempt == 1 and timeout else 0.0\n"
        "        )\n",
    ),
    # The worker resumes 0.2 s late after the wait's timeout (a loaded runner),
    # so a root exiting just past the deadline is dead when the witness looks.
    "T_lateprobe": (
        TEST,
        "                    if first:\n                        witness.timed_out_at = time.monotonic()\n",
        "                    if first:\n                        time.sleep(0.2)\n"
        "                        witness.timed_out_at = time.monotonic()\n",
    ),
    # --- wrong forms of the #446 change ---
    "BASE": (TEST, "", ""),
    # No deadline branch at all (e24ea2e8's loop, the rest of the change kept).
    "W_revert": (
        TEST,
        '        elif (\n            end.kind == "no-exit-drain"\n            and timeout is not None\n',
        '        elif (\n            False\n            and timeout is not None\n',
    ),
    # Any timed-out attempt is a premise (no witness, no kill check).
    "W_anytimeout": (
        TEST,
        "            defect = _why_the_root_timed_out(witness, started=started, timeout=timeout)\n"
        "            if defect is None:\n"
        "                left = _the_timeout_leg_left(witness, pids)\n",
        "            defect = None\n"
        "            if defect is None:\n"
        "                left = []\n",
    ),
    "W_noearly": (TEST, "    if gave_up < timeout - _EARLY_TIMEOUT_SLACK:\n", "    if False:\n"),
    "W_slack": (TEST, "_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.6\n"),
    # A root dead when the wait gave up is a premise whatever the look said.
    "W_nolook": (
        TEST,
        "    if witness.root_then == STATE_ALIVE:\n        return None\n",
        "    if witness.root_then is not None:\n        return None\n",
    ),
    # The look's verdict ignored (any state at the look clears the site).
    "W_lookany": (
        TEST,
        "    if witness.looked_saw == STATE_ALIVE:\n        return None\n",
        "    if witness.looked_saw is not None:\n        return None\n",
    ),
    # The prior draft's rule: only ALIVE at the give-up clears it, no look (too strict).
    "W_alivegate": (
        TEST,
        "    waited_at = witness.waited_at if witness.waited_at is not None else started\n",
        "    return f'the root was already {witness.root_then} when the site wait gave up'\n"
        "    waited_at = witness.waited_at if witness.waited_at is not None else started\n",
    ),
    "W_nolabel": (
        TEST,
        "    if witness.timed_out_at is None:\n        return (\n",
        "    if witness.timed_out_at is None:\n        return None\n        return (\n",
    ),
    "W_nokillcheck": (TEST, "                left = _the_timeout_leg_left(witness, pids)\n", "                left = []\n"),
    # The floor back to the subtraction.
    "W_floor": (TEST, "        assert started + timeout <= returned, (\n", "        assert timeout <= elapsed, (\n"),
}


def main() -> None:
    tree = Path(sys.argv[1])
    arg = sys.argv[2]
    if arg == "--list":
        print(" ".join(M))
        return
    for rel in (BASH, TEST):
        shutil.copy(tree / (rel + ".pristine"), tree / rel)
    if arg in ("--restore", "none"):
        return
    for name in arg.split("+"):
        rel, old, new = M[name]
        path = tree / rel
        if name == "BASE":
            shutil.copy(BASE_TEST, path)
            print("applied BASE")
            continue
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"{name}: expected exactly one match, found {text.count(old)}")
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"applied {name}")


if __name__ == "__main__":
    main()
