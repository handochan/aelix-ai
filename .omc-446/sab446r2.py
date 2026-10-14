"""#446 review-round-2 sabotage mutator: python sab446r2.py <tree> <mutation>[+<mutation>...] | --list | --restore.

Applies to a throwaway tree whose ``*.pristine`` copies are the AMENDED test and
e24ea2e8's product. P_* product defects (``*_once`` fire on the FIRST timed call
only, via the file named by $SAB446_ONCE); T_* premise shapes (test side, not
defects); W_* wrong forms of the #446 change; M_* wrong look schedules; BASE
swaps the whole test back to e24ea2e8's ($SAB446_BASE_TEST); R1 swaps it to
fb5eae28's ($SAB446_R1_TEST).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BASH = "packages/aelix-coding-agent/src/aelix_coding_agent/tools/bash.py"
TEST = "tests/tools/test_bash_tool_containment.py"

ONCE = (
    "(lambda _o: bool(_o) and __import__('os').path.exists(_o) and "
    "(__import__('os').remove(_o) or True))(__import__('os').environ.get('SAB446_ONCE'))"
)

M = {
    # --- product defects (e24ea2e8's bash.py) ---
    "P_early": (BASH, "code = proc.wait(timeout)", "code = proc.wait(None if timeout is None else timeout * 0.5)"),
    "P_late": (BASH, "code = proc.wait(timeout)", "code = proc.wait(None if timeout is None else timeout + 5.0)"),
    "P_coawait_once": (
        BASH,
        "code = proc.wait(timeout)",
        "if timeout is not None and " + ONCE + ":\n"
        "                    proc._waitpid_lock.acquire()\n"
        "                    __import__('threading').Timer(timeout + 0.3, proc._waitpid_lock.release).start()\n"
        "                code = proc.wait(timeout)",
    ),
    "P_label_once": (
        BASH,
        "code = proc.wait(timeout)",
        "if timeout is not None and " + ONCE + ":\n"
        "                    time.sleep(timeout)\n"
        "                    raise subprocess.TimeoutExpired(proc.args, timeout)\n"
        "                code = proc.wait(timeout)",
    ),
    "P_nokill": (
        BASH,
        "_end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)",
        "with contextlib.suppress(subprocess.TimeoutExpired):\n"
        "                    proc.wait(timeout=REAP_GRACE_SECONDS)",
    ),
    "P_rootonly": (
        BASH,
        "_end_the_tree(tree, proc, reap=REAP_GRACE_SECONDS)",
        "proc.kill()\n"
        "                with contextlib.suppress(subprocess.TimeoutExpired):\n"
        "                    proc.wait(timeout=REAP_GRACE_SECONDS)",
    ),
    "P_nocap": (
        BASH,
        "await _drain_after_the_exit(exited_at, cap=cap, hard_cap=exited_at + DRAIN_CAP_SECONDS)",
        "await _drain_after_the_exit(exited_at, cap=float('inf'), hard_cap=float('inf'))",
    ),
    "P_exitstamp": (
        BASH,
        "                _root_exited_at = time.monotonic()\n",
        "                _root_exited_at = time.monotonic()\n                state.exited_at = _root_exited_at\n",
    ),
    "P_label_always": (BASH, "timed_out=_timed_out and not _signal_aborted,", "timed_out=True,"),
    "P_noterm": (
        BASH,
        "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=timeout)",
        "cap = _exit_drain_cap(exited_at, started_at=started_at, timeout=None)",
    ),
    # --- premise shapes (test side) ---
    "T_hold1": (
        TEST,
        "        hold = first_root_holds if attempt == 1 else 0.0\n",
        "        hold = 30.0 if attempt == 1 and timeout else first_root_holds if attempt == 1 else 0.0\n",
    ),
    # Attempt 1 runs a first python that sleeps 8 s BEFORE the real root, in the same shell: the
    # shell stays the root (two statements), and the sleeper is a member of its group (POSIX) /
    # job (win32) that never announces - the python root still starting when the kill goes out.
    "T_slowstart1": (
        TEST,
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=hold)\n",
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=hold)\n"
        "        if attempt == 1 and timeout:\n"
        "            command = _command('-c', 'import time; time.sleep(8)') + '; ' + command\n",
    ),
    # The same two shapes on e24ea2e8's / fb5eae28's test (BASE / R1), whose loop has no ``hold`` variable.
    "T_hold1_old": (
        TEST,
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=0.0)\n",
        "        command = _holder_command(\n"
        "            marker.parent, marker, tick=0.05, hold=30.0 if attempt == 1 and timeout else 0.0\n"
        "        )\n",
    ),
    "T_slowstart1_old": (
        TEST,
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=0.0)\n",
        "        command = _holder_command(marker.parent, marker, tick=0.05, hold=0.0)\n"
        "        if attempt == 1 and timeout:\n"
        "            command = _command('-c', 'import time; time.sleep(8)') + '; ' + command\n",
    ),
    "T_lateprobe": (
        TEST,
        "                    if first:\n                        witness.timed_out_at = time.monotonic()\n",
        "                    if first:\n                        time.sleep(0.2)\n"
        "                        witness.timed_out_at = time.monotonic()\n",
    ),
    # --- wrong forms of the change ---
    "BASE": (TEST, "", ""),
    "R1": (TEST, "", ""),
    "W_revert": (
        TEST,
        '        elif (\n            end.kind == "no-exit-drain"\n            and timeout is not None\n',
        '        elif (\n            False\n            and timeout is not None\n',
    ),
    "W_anytimeout": (
        TEST,
        "            defect = _why_the_root_timed_out(witness, started=started, timeout=timeout)\n"
        "            checked: list[str] = []\n"
        "            if defect is None:\n"
        "                left, checked = _the_timeout_leg_left(witness, pids, marker, strays)\n",
        "            defect = None\n"
        "            checked: list[str] = []\n"
        "            if defect is None:\n"
        "                left = []\n",
    ),
    "W_nokillcheck": (
        TEST,
        "                left, checked = _the_timeout_leg_left(witness, pids, marker, strays)\n",
        "                left = []\n",
    ),
    "W_floor": (TEST, "    return started + timeout <= returned\n", "    return timeout <= returned - started\n"),
    "W_noearly": (TEST, "    if gave_up < timeout - _EARLY_TIMEOUT_SLACK:\n", "    if False:\n"),
    "W_slack045": (TEST, "_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.45\n"),
    "W_slack0": (TEST, "_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.0\n"),
    "W_nolate": (TEST, "    if late > _LATE_TIMEOUT_SLACK:\n", "    if False:\n"),
    "W_late1": (TEST, "_LATE_TIMEOUT_SLACK = 0.5\n", "_LATE_TIMEOUT_SLACK = 1.0\n"),
    "W_nohanded": (TEST, "    if witness.waited_for != timeout:\n", "    if False:\n"),
    "W_alivegate": (
        TEST,
        "    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n",
        "    return f'the root was already {witness.root_then} when the site wait gave up'\n"
        "    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n",
    ),
    "W_nolook": (
        TEST,
        "    if witness.root_then == STATE_ALIVE:\n        return None\n",
        "    if witness.root_then is not None:\n        return None\n",
    ),
    "W_zombienolook": (
        TEST,
        "    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n",
        "    if witness.root_then == STATE_ZOMBIE and witness.look_began is None:\n        return None\n"
        "    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n",
    ),
    "W_lookany": (
        TEST,
        "    if witness.looked_saw == STATE_ALIVE:\n        early = deadline - witness.look_began\n",
        "    if witness.looked_saw is not None:\n        return None\n"
        "    if witness.looked_saw == STATE_ALIVE:\n        early = deadline - witness.look_began\n",
    ),
    "W_nowindow": (
        TEST,
        "        if early <= _LOOK_BEFORE_THE_DEADLINE + _EARLY_TIMEOUT_SLACK:\n",
        "        if True:\n",
    ),
    "W_noahead": (TEST, "    if ahead > _EARLY_TIMEOUT_SLACK:\n", "    if False:\n"),
    "W_nolabel": (
        TEST,
        "    if witness.timed_out_at is None:\n        return (\n",
        "    if witness.timed_out_at is None:\n        return None\n        return (\n",
    ),
    "W_norc": (TEST, "            code == 0\n            and witness.root_at_reap == STATE_ALIVE\n",
               "            False\n            and witness.root_at_reap == STATE_ALIVE\n"),
    "W_noannounce": (TEST, "            if after > _EARLY_TIMEOUT_SLACK:\n", "            if False:\n"),
    "W_nogroup": (TEST, "            still = held()\n", "            still = []\n"),
    "W_noowed": (
        TEST,
        "            alive = [pid for pid in owed if probe_state(pid) == STATE_ALIVE]\n",
        "            alive = []\n",
    ),
    # --- wrong look schedules (cross-review M1, M2) ---
    "M_signerr": (
        TEST,
        "        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE\n",
        "        due = waited_at + _LOOK_BEFORE_THE_DEADLINE\n",
    ),
    "M_atonce": (
        TEST,
        "        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE\n",
        "        due = waited_at\n",
    ),
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
        if name in ("BASE", "R1"):
            src = os.environ["SAB446_BASE_TEST" if name == "BASE" else "SAB446_R1_TEST"]
            shutil.copy(src, path)
            print(f"applied {name}")
            continue
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"{name}: expected exactly one match, found {text.count(old)}")
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"applied {name}")


if __name__ == "__main__":
    main()
