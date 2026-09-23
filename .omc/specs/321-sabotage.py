#!/usr/bin/env python3
"""#321: what silently defeats the new tests?

Each arm is a one-idea edit to the fix — small enough that a reviewer could
wave it through — applied to a scratch copy of ``aelix-agent-core``'s ``src``
(put first on ``PYTHONPATH``, so the working tree is never touched), and the
two neighbouring test files are run against it: this issue's and #311's.

Run from a worktree root::

    uv run --no-sync python .omc/specs/321-sabotage.py

The loaded ``core.py`` is printed per arm, so an arm that silently measured the
working tree instead of its copy would show up as the wrong path.

Measured 2026-09-24 in the cross-review round on
``fix/321-prompt-cancel-resets-phase`` (baseline: 31 passed — this issue's 23
tests and #311's 8; every arm loaded its temp copy)::

    A reset on any "turn" (no owner check)          5 failed  both trap arms, both re-run-ownership arms, ARM 22's
    B except Exception again                       11 failed  every cancel that must reset (13c's, 17c's, 21c's too)
    C the retry re-run keeps the standing claim     7 failed  the re-run-ownership arms, 17r/17c, 13 [retry-*], ARM 24's
    D the overflow re-run keeps the standing claim  2 failed  13 [overflow-cancel], [overflow-raise]
    E swallow the cancel (drop the re-raise)       28 failed
    F compaction_start before the try again         2 failed  both compact() tests
    G phase back BEFORE the drained queue           1 failed  the order test
    H the outer clause restores too                 8 failed  the order test + 7 of #311's
    I the first version's release + None arm        0 failed  (dead: see below)
    J the first run keeps the standing claim        3 failed  both of ARM 21's, ARM 22's
    K InputHandled resets with no owner check       1 failed  ARM 24's
    L _run's flip takes no claim at all            10 failed  C's seven, D's two, ARM 22's

Each arm A-H and J-L is caught by the test written for it. A is the fix the
issue text suggested. C, D and J are the three ``_run`` calls — the retry
re-run's, overflow recovery's and the first run's — each made to hand ``_run``
the claim that already stands, which is what a call that took no claim of its
own did in the earlier versions; J is the second version exactly. L takes the
claim out of ``_run``'s flip altogether, which leaves the entry's claim only —
the first version without its release and ``None`` arm. C also reddens ARM 24's
test: with #1's re-run taking no claim, #2's ``InputHandled`` finds its own
claim still standing and gives back #1's live re-run. K is the ``InputHandled``
return's owner check.

I is how the first version handed a re-run its turn back: release the claim
when the call ends, and reset on ``None`` too. Its 0 is now the expected count.
With the claim taken in ``_run``'s own flip nothing reaches that arm: the whole
cancel probe prints the same outcome in every arm with and without it, bar ARM
13's diagnostic ``claim=`` line. The second version's record said the same of
its own 0 — "dead once every flip takes the claim" — and was wrong: its first
run's flip took no claim, and put back into the second version arm I gives all
five ARM 21 arms their phase back (the probe run with and without it differs in
exactly those five). That 0 meant only that none of the tests then built ARM
21's shape; the two ARM 21 tests do now, and J is the arm that fails them.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

SRC = pathlib.Path("packages/aelix-agent-core/src")
CORE_REL = "aelix_agent_core/harness/core.py"
TESTS = [
    "tests/test_harness_cancel_gives_the_phase_back.py",
    "tests/test_harness_next_turn_fault_injection.py",
]

OUTER = "        except BaseException:\n            # Give the phase back on every exit"
# Each anchor starts with a newline where its indentation must be exact: the
# ``InputHandled`` return and the ``except`` clause test the same condition at
# different depths, and a re-run call's line contains the other's.
CONDITION = (
    '\n            if self._phase == "turn" and self._turn_owner is owner:\n'
    '                self._phase = "idle"\n'
)
HANDLED = """
                    if self._phase == "turn" and self._turn_owner is owner:
                        self._phase = "idle"
                        self._idle_event.set()
                    return []
"""
RUN_FLIP = """
        self._phase = "turn"
        self._turn_owner = owner
"""
FIRST_RUN = "\n            result = await self._run(prompts, system_prompt=system_prompt, owner=owner)\n"
RETRY_RERUN = (
    '"retry continue requires a pending user message in state"\n'
    "                    result = await self._run([], system_prompt=system_prompt, owner=owner)\n"
)
OVERFLOW_RERUN = (
    "                    break\n"
    "                result = await self._run([], system_prompt=system_prompt, owner=owner)\n"
)
CLAUSE_END = """                self._idle_event.set()
            raise

    async def steer("""
INNER = """            except BaseException:
                self._next_turn_queue = drained_next + self._next_turn_queue
                raise
"""
START_EMIT = """        try:
            # Issue #4 (FU3) — compaction_start to subscribers"""


def _keeps_the_standing_claim(anchor: str) -> str:
    # The call still goes through ``_run``'s flip, but hands it whatever claim
    # stands — so the flip leaves the claim as it was: that run takes none.
    return anchor.replace("owner=owner)", "owner=self._turn_owner)")


ARMS: dict[str, tuple[str, str]] = {
    "A reset on any 'turn' (the obvious widening, no owner check)": (
        CONDITION,
        '\n            if self._phase == "turn":\n                self._phase = "idle"\n',
    ),
    "B except Exception again (owner check kept)": (
        OUTER,
        "        except Exception:\n            # Give the phase back on every exit",
    ),
    "C the retry re-run keeps the standing claim (takes none of its own)": (
        RETRY_RERUN,
        _keeps_the_standing_claim(RETRY_RERUN),
    ),
    "D the overflow re-run keeps the standing claim": (
        OVERFLOW_RERUN,
        _keeps_the_standing_claim(OVERFLOW_RERUN),
    ),
    "E the cancel is swallowed (drop the re-raise)": (
        CLAUSE_END,
        """                self._idle_event.set()

    async def steer(""",
    ),
    "F compaction_start emitted before the try again": (
        START_EMIT,
        """        await self._emit_to_subscribers(CompactionStartEvent(reason=reason))
        try:
            # Issue #4 (FU3) — compaction_start to subscribers""",
    ),
    "G the phase goes back BEFORE the drained queue does": (
        INNER,
        """            except BaseException:
                self._phase = "idle"
                self._idle_event.set()
                self._next_turn_queue = drained_next + self._next_turn_queue
                raise
""",
    ),
    "H the outer clause restores the queue too (double restore)": (
        OUTER,
        """        except BaseException:
            if "drained_next" in locals():
                self._next_turn_queue = drained_next + self._next_turn_queue
            # Give the phase back on every exit""",
    ),
    "I the first version's release finally and `or claim is None` put back": (
        CONDITION + "                self._idle_event.set()\n            raise\n\n    async def steer(",
        """
            claim = self._turn_owner
            if self._phase == "turn" and (claim is owner or claim is None):
                self._phase = "idle"
                self._idle_event.set()
            raise
        finally:
            if self._turn_owner is owner:
                self._turn_owner = None

    async def steer(""",
    ),
    "J the first run keeps the standing claim (the second version)": (
        FIRST_RUN,
        _keeps_the_standing_claim(FIRST_RUN),
    ),
    "K the InputHandled return resets with no owner check again": (
        HANDLED,
        """
                    self._phase = "idle"
                    self._idle_event.set()
                    return []
""",
    ),
    "L _run's flip takes no claim at all (the entry's claim only)": (
        RUN_FLIP,
        """
        self._phase = "turn"
""",
    ),
}


def _env(src_root: pathlib.Path | None) -> dict[str, str]:
    env = dict(os.environ)
    if src_root is not None:
        env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_suite(src_root: pathlib.Path | None, label: str) -> None:
    env = _env(src_root)
    loaded = subprocess.run(
        [sys.executable, "-c", "import aelix_agent_core.harness.core as c; print(c.__file__)"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    print(label)
    print("        core.py:", loaded)
    for line in out.stdout.splitlines():
        if line.startswith("FAILED"):
            print("       ", line.split("::", 1)[-1].split(" - ")[0])
        elif line[:1].isdigit() and ("passed" in line or "failed" in line):
            print("       ", line.strip())


def main() -> int:
    if not SRC.is_dir():
        print(f"run me from a worktree root — {SRC} not found", file=sys.stderr)
        return 2
    original = (SRC / CORE_REL).read_text()
    _run_suite(None, "baseline (working tree)")
    for label, (old, new) in ARMS.items():
        if original.count(old) != 1:
            print(f"{label}\n        SKIPPED: anchor found {original.count(old)} times")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "src"
            shutil.copytree(SRC, root)
            (root / CORE_REL).write_text(original.replace(old, new, 1))
            _run_suite(root, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
