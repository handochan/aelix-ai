#!/usr/bin/env python3
"""#311: what silently defeats the assertions in the new test file?

A test that stays green while its subject is broken guards nothing. This
script breaks the guard four different ways — each a change small enough that
a reviewer could wave it through — and reports which tests notice.

Every arm copies ``aelix-agent-core``'s ``src`` to a temp dir, edits only
``harness/core.py`` there, and runs the suite against it via ``PYTHONPATH``,
so the working tree is never touched.

Run from a worktree root::

    uv run --no-sync python .omc/specs/311-sabotage.py

Measured 2026-09-22 on ``fix/311-next-turn-queue`` (baseline: 8 passed)::

    A append instead of prepend
        1 failed, 7 passed
        test_the_restored_messages_go_back_in_front_of_what_arrived_mid_await
    B Exception instead of BaseException
        1 failed, 7 passed
        test_a_cancelled_drain_hands_the_messages_back
    C restore unconditionally (finally)
        2 failed, 6 passed
        test_a_successful_prompt_still_empties_the_queue
        test_a_turn_that_fails_after_the_drain_does_not_requeue
    D widen the guard over _run
        1 failed, 7 passed
        test_a_turn_that_fails_after_the_drain_does_not_requeue

Each arm is caught, and caught by the test written for it — including the two
that pass on the base (C and D redden exactly those two, which is what makes
them load-bearing rather than decorative).
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
TESTS = "tests/test_harness_next_turn_fault_injection.py"

GUARD = """            except BaseException:
                self._next_turn_queue = drained_next + self._next_turn_queue
                raise
            result = await self._run(prompts, system_prompt=system_prompt)
"""

ARMS: dict[str, tuple[str, str]] = {
    "A append instead of prepend": (
        "self._next_turn_queue = drained_next + self._next_turn_queue",
        "self._next_turn_queue = self._next_turn_queue + drained_next",
    ),
    "B Exception instead of BaseException": (
        "            except BaseException:\n",
        "            except Exception:\n",
    ),
    "C restore unconditionally (finally)": (
        GUARD,
        """            finally:
                self._next_turn_queue = drained_next + self._next_turn_queue
            result = await self._run(prompts, system_prompt=system_prompt)
""",
    ),
    "D widen the guard over _run": (
        GUARD,
        """                result = await self._run(prompts, system_prompt=system_prompt)
            except BaseException:
                self._next_turn_queue = drained_next + self._next_turn_queue
                raise
""",
    ),
}


def _run_suite(src_root: pathlib.Path | None, label: str) -> None:
    env = dict(os.environ)
    if src_root is not None:
        env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    print(label)
    for line in out.stdout.splitlines():
        if line.startswith("FAILED"):
            print("       ", line.split("::", 1)[-1])
        elif line[:1].isdigit() and ("passed" in line or "failed" in line):
            # pytest's summary line only — a traceback quoting a docstring
            # that contains the word "failed" is not a count.
            print("       ", line.strip())


def main() -> int:
    if not SRC.is_dir():
        print(f"run me from a worktree root — {SRC} not found", file=sys.stderr)
        return 2
    original = (SRC / CORE_REL).read_text()
    if GUARD not in original:
        print("the guard this script sabotages is not in core.py", file=sys.stderr)
        return 2

    _run_suite(None, "baseline (working tree)")
    for label, (old, new) in ARMS.items():
        if original.count(old) != 1:
            print(f"{label}\n        SKIPPED: anchor is not unique")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "src"
            shutil.copytree(SRC, root)
            (root / CORE_REL).write_text(original.replace(old, new, 1))
            _run_suite(root, label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
