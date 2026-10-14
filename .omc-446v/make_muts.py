"""Write mutated copies of the committed test file (the fix's wrong implementations)."""
import sys
from pathlib import Path
tree = Path(sys.argv[1])
src = (tree / "tests/tools/test_bash_tool_containment.py").read_text(encoding="utf-8")
MUTS = {
    # the call site trusts any timeout (no witness verdict)
    "any": [("defect = _why_the_root_timed_out(witness, started=started, timeout=timeout)", "defect = None")],
    # the kill check dropped
    "nokillcheck": [("left = _the_timeout_leg_left(witness, pids)", "left = []")],
    # the draft rule: alive at the give-up or a defect (the look ignored)
    "alive": [("    if witness.looked_saw == STATE_ALIVE:\n        return None\n", "    if False:\n        return None\n")],
    # any look state accepted
    "anylook": [("    if witness.looked_saw == STATE_ALIVE:\n        return None\n", "    if witness.looked_saw is not None:\n        return None\n")],
    # no early check
    "noearly": [("    if gave_up < timeout - _EARLY_TIMEOUT_SLACK:\n", "    if False:\n")],
    # a slack wide enough to hide a short deadline
    "slack": [("_EARLY_TIMEOUT_SLACK = 0.05\n", "_EARLY_TIMEOUT_SLACK = 1.2\n")],
    # no record read as a premise
    "nolabel": [("    if witness.timed_out_at is None:\n        return (\n", "    if witness.timed_out_at is None:\n        return None\n    if False:\n        return (\n")],
    # dead root without a look accepted (fail-open instead of closed)
    "failopen": [("    if witness.looked_at is None or witness.looked_saw is None:\n        return (\n", "    if witness.looked_at is None or witness.looked_saw is None:\n        return None\n    if False:\n        return (\n")],
    # the old floor
    "oldfloor": [("        assert started + timeout <= returned, (\n", "        assert timeout <= elapsed, (\n")],
    # the witness classification gated off (the lane's commit reverted to e24ea2e8 shape at the branch)
    "nobranch": [("            end.kind == \"no-exit-drain\"\n            and timeout is not None\n", "            False\n            and timeout is not None\n")],
}
for name, reps in MUTS.items():
    out = src
    for old, new in reps:
        n = out.count(old)
        assert n == 1, (name, n, old)
        out = out.replace(old, new)
    (tree / f"tests/tools/test_v446mut_{name}.py").write_text(out, encoding="utf-8")
    print("wrote", name)
