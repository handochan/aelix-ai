"""r2verify text mutants of the committed test file (throwaway tree only).

usage: mutate.py <name>|restore   -- rewrites tree/tests/tools/test_bash_tool_containment.py from the pristine copy
Each mutant is (old, new) replacements; each old must occur exactly once.
"""
import sys
from pathlib import Path

TARGET = Path("tests/tools/test_bash_tool_containment.py")
PRISTINE = Path(".omc-446v/pristine.py")

M = {
    # --- CI wrong versions ---
    "W_anytimeout": [
        ("defect = _why_the_root_timed_out(witness, started=started, timeout=timeout)", "defect = None"),
        ("left, checked = _the_timeout_leg_left(witness, pids, marker, strays)", "left, checked = [], []"),
    ],
    "W_nokillcheck": [("left, checked = _the_timeout_leg_left(witness, pids, marker, strays)", "left, checked = [], []")],
    "W_revert": [('            end.kind == "no-exit-drain"\n            and timeout is not None', '            False\n            and timeout is not None')],
    "W_floor": [("    return started + timeout <= returned\n", "    return timeout <= returned - started\n")],
    "W_norc": [("            code == 0\n            and witness.root_at_reap == STATE_ALIVE", "            False\n            and witness.root_at_reap == STATE_ALIVE")],
    # --- kill-check drops ---
    "K_noannounce": [("            if after > _EARLY_TIMEOUT_SLACK:\n                left.append(", "            if False:\n                left.append(")],
    "K_noalive": [("        for pid in alive:\n            left.append(", "        for pid in []:\n            left.append(")],
    "K_nogroup": [("        elif still:\n            strays.extend(still)", "        elif False:\n            strays.extend(still)")],
    "K_nogroup_noannounce": [
        ("        elif still:\n            strays.extend(still)", "        elif False:\n            strays.extend(still)"),
        ("            if after > _EARLY_TIMEOUT_SLACK:\n                left.append(", "            if False:\n                left.append("),
    ],
    "K_poll5": [("        limit = kill_at + _A_KILLED_PROCESS_ENDS_WITHIN\n", "        limit = time.monotonic() + 5.0\n")],
    "K_announce_vs_giveup": [("            witness.reap_began_wall if witness.reap_began_wall is not None else witness.settled_wall", "            (witness.reap_began_wall - (witness.reap_began - witness.timed_out_at)) if witness.reap_began_wall is not None and witness.reap_began is not None and witness.timed_out_at is not None else witness.settled_wall")],
    # --- rule drops ---
    "R_nohanded": [("    if witness.waited_for != timeout:\n", "    if False:\n")],
    "R_noearly": [("    if gave_up < timeout - _EARLY_TIMEOUT_SLACK:\n", "    if False:\n")],
    "R_nolate": [("    if late > _LATE_TIMEOUT_SLACK:\n", "    if False:\n")],
    "R_alivealways": [("    if witness.root_then == STATE_ALIVE:\n        return None\n", "    if witness.root_then is not None:\n        return None\n")],
    "R_nolookwindow": [("        if early <= _LOOK_BEFORE_THE_DEADLINE + _EARLY_TIMEOUT_SLACK:\n", "        if True:\n")],
    "R_noahead": [("    if ahead > _EARLY_TIMEOUT_SLACK:\n        return (\n            f\"the root was already", "    if False:\n        return (\n            f\"the root was already")],
    "R_anylook": [("    if witness.looked_saw == STATE_ALIVE:\n", "    if witness.looked_saw is not None:\n")],
    "R_slack0": [("_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.0\n")],
    "R_slack045": [("_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.45\n")],
    "R_slack1": [("_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 0.1\n")],
    "R_lateslack1": [("_LATE_TIMEOUT_SLACK = 0.5\n", "_LATE_TIMEOUT_SLACK = 1.0\n")],
    "R_lateslack04": [("_LATE_TIMEOUT_SLACK = 0.5\n", "_LATE_TIMEOUT_SLACK = 0.4\n")],
    "R_look04": [("_LOOK_BEFORE_THE_DEADLINE = 0.25\n", "_LOOK_BEFORE_THE_DEADLINE = 0.4\n")],
    "R_zombienolook": [("    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n        return (", "    if witness.root_then == STATE_ZOMBIE and witness.look_began is None:\n        return None\n    if witness.look_began is None or witness.looked_at is None or witness.looked_saw is None:\n        return (")],
    "R_lookedat_for_window": [("        early = deadline - witness.look_began\n", "        early = deadline - witness.looked_at\n")],
    # --- look schedule ---
    "M_signerr": [("        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE\n", "        due = waited_at + _LOOK_BEFORE_THE_DEADLINE\n")],
    "M_atonce": [("        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE\n", "        due = waited_at\n")],
    "M_half": [("        due = waited_at + waited_for - _LOOK_BEFORE_THE_DEADLINE\n", "        due = waited_at + waited_for - 0.5\n")],
    "M_noprobe_saw_alive": [("        state = probe_state(root)\n        self.look_began", "        state = STATE_ALIVE\n        self.look_began")],
    # --- witness record ---
    "X_waitedat_late": [("                    witness.waited_at, witness.waited_for = time.monotonic(), timeout\n", "                    witness.waited_at, witness.waited_for = time.monotonic() + 0.5, timeout\n")],
}


def main() -> None:
    name = sys.argv[1]
    src = PRISTINE.read_bytes().decode("utf-8").replace("\r\n", "\n")
    if name != "restore":
        for old, new in M[name]:
            n = src.count(old)
            if n != 1:
                raise SystemExit(f"{name}: anchor occurs {n} times: {old[:80]!r}")
            src = src.replace(old, new)
    TARGET.write_bytes(src.encode("utf-8"))
    print(f"mutant {name} applied")


if __name__ == "__main__":
    main()
