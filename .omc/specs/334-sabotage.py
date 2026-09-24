"""#334 sabotage — break the fix one way at a time and see which tests redden.

Each id builds a copy of ``aelix_agent_core`` (from the tree this runs in) with
one edit to ``harness/core.py`` under ``<out>/<id>/src`` and runs the #334
files, #321's, #311's and the retry/compaction neighbours with that copy first
on ``PYTHONPATH``. The measured table is in ADR-0023's #334 amendment.

    cd <tree> && python .omc/specs/334-sabotage.py [--out DIR] [ID ...]

  S1    ``_run``'s ``finally`` sets idle again (the defect)
  S2    the nested ``compact()`` ``finally`` hands "idle" and sets the event
  Sb    the nested ``finally`` hands "turn" but sets the event (critic S-b)
  S3    the tail calls ``compact()`` without its claim
  S4    no release flush (the release's drain call dropped)
  S5    the release does not set the idle event
  S6    the release drain outside the nested ``finally``
  S7    ``compact()`` nests on any token, not ``is self._claim``
  S8    the busy guard inside the ``try`` (a refused call releases)
  S9    a one-pass drain (round 4: the release's loop is the drain's ``while``)
  S10a  drop ``prompt()``'s retry-loop abort check
  S10e  drop ``prompt()``'s abort check after the overflow compaction
  S10b  drop the backoff's ``retry_aborted`` check
  S10c  drop the threshold check's post-read abort check
  S10d  drop the overflow recovery's post-read abort check
  S11   the release does not reset ``_turn_state``
  S12   the release does not drop ``_claim``
  S3s   the silent-overflow compaction (``will_retry=False``) without its claim
        (round 3's verification sabotage M4b)
  S13   an idle ``set_thinking_level`` appends at once past a non-empty queue
        (Codex C4 (a))
  S14   no drain at ``prompt()``'s entry
  S15   no drain in ``dispose()`` (Codex C4 (b))
  S16   the drain takes no lock
  S17   no drain at a public ``compact()``'s entry
  S18   no drain at ``navigate_tree()``'s entry
  S19   the release flushes without the drain lock (round 3's unlocked loop;
        round 4's verification R1)
  S20   the drain's fast path checks the queue alone, not the lock (round 3's
        verification sabotage M3)
  S21   the release drains only when its own queue is non-empty (round 4's
        verification sabotage M10)
  S22   the drain suspends once before its fast path (``await
        asyncio.sleep(0)``; the second Codex review's finding 5)

(Round 2 removed ``prompt()``'s abort checks before the overflow recovery and
before the threshold check: dropping either alone reddened nothing — the
checks inside those two methods, after their branch reads, do the work.)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess

SRC = pathlib.Path("packages/aelix-agent-core/src/aelix_agent_core")
FILES = [
    "tests/test_harness_prompt_holds_the_turn_through_its_tail.py",
    "tests/rpc/test_rpc_prompt_during_a_retry.py",
    "tests/test_harness_cancel_gives_the_phase_back.py",
    "tests/test_harness_next_turn_fault_injection.py",
    "tests/harness/test_overflow_recovery.py",
    "tests/test_compact.py",
    "tests/test_auto_retry.py",
    "tests/test_input_emit.py",
]
SAB = {
 'S1': [("""                    exc_info=True,
                )
            self._turn_state = None

    async def _drain_steering""", """                    exc_info=True,
                )
            self._turn_state = None
            self._phase = "idle"  # SABOTAGE S1
            self._idle_event.set()

    async def _drain_steering""")],
 'S2': [("""                self._phase = "turn"
            else:
                self._phase = "idle"
                self._idle_event.set()""", """                self._phase = "idle"  # SABOTAGE S2
                self._idle_event.set()
            else:
                self._phase = "idle"
                self._idle_event.set()""")],
 'Sb': [("""                self._phase = "turn"
            else:
                self._phase = "idle"
                self._idle_event.set()""", """                self._phase = "turn"
                self._idle_event.set()  # SABOTAGE S-b
            else:
                self._phase = "idle"
                self._idle_event.set()""")],
 'S3': [('await self.compact(reason="threshold", _claim=claim)', 'await self.compact(reason="threshold")  # SABOTAGE S3'),
        ('await self.compact(reason="overflow", will_retry=False, _claim=claim)', 'await self.compact(reason="overflow", will_retry=False)'),
        ('await self.compact(reason="overflow", will_retry=True, _claim=claim)', 'await self.compact(reason="overflow", will_retry=True)')],
 'S4': [("""            try:
                await self._drain_pending_session_writes()
            except Exception:  # noqa: BLE001""", """            try:
                pass  # SABOTAGE S4
            except Exception:  # noqa: BLE001""")],
 'S5': [("""                self._claim = None
                self._turn_state = None
                self._phase = "idle"
                self._idle_event.set()""", """                self._claim = None
                self._turn_state = None
                self._phase = "idle"  # SABOTAGE S5 (no set)""")],
 'S6': [("""            try:
                await self._drain_pending_session_writes()
            except Exception:  # noqa: BLE001
                _log.warning(
                    "flush_pending_session_writes raised at the prompt's release "
                    "— pending session writes were lost",
                    exc_info=True,
                )
            finally:
                self._claim = None""", """            await self._drain_pending_session_writes()  # SABOTAGE S6
            if True:
                self._claim = None""")],
 'S7': [("nested = _claim is not None and _claim is self._claim", "nested = _claim is not None  # SABOTAGE S7")],
 'S8': [("""        if self._phase != "idle":
            raise AgentHarnessError(
                "busy",
                f"AgentHarness is busy (phase={self._phase!r}); use "
                "steer()/follow_up() while in a turn.",
            )
""", ""),
        ("""        self._phase = "turn"
        self._idle_event.clear()
        claim = self._claim = object()
""", ""),
        ("""        self._overflow_recovery_attempted = False
        try:
            # #334 — writes an earlier call left queued""", """        try:
            if self._phase != "idle":  # SABOTAGE S8: the guard inside the try
                raise AgentHarnessError("busy", f"AgentHarness is busy (phase={self._phase!r})")
            self._phase = "turn"
            self._idle_event.clear()
            claim = self._claim = object()
            self._overflow_recovery_attempted = False
            # #334 — writes an earlier call left queued""")],
 'S9': [("""        async with self._drain_lock:
            while self._pending_session_writes:
                await self.flush_pending_session_writes()""", """        async with self._drain_lock:
            await self.flush_pending_session_writes()  # SABOTAGE S9 one pass""")],
 'S10a': [("""                    if self._abort_requested:
                        break
                    did_retry""", """                    # SABOTAGE S10a
                    did_retry""")],
 'S10e': [("""                if not await self._try_overflow_recovery(system_prompt, claim=claim):
                    break
                if self._abort_requested:
                    break
                result""", """                if not await self._try_overflow_recovery(system_prompt, claim=claim):
                    break
                # SABOTAGE S10e
                result""")],
 'S10b': [("""        if self._state.retry_aborted:
            self._retry_abort_event.set()""", """        pass  # SABOTAGE S10b""")],
 'S10c': [("""            if claim is not None and self._abort_requested:
                return
            try:
                await self.compact(reason="threshold", _claim=claim)""", """            try:
                await self.compact(reason="threshold", _claim=claim)  # SABOTAGE S10c""")],
 'S11': [("""                self._claim = None
                self._turn_state = None
                self._phase = \"idle\"""", """                self._claim = None  # SABOTAGE S11
                self._phase = \"idle\"""")],
 'S12': [("""                self._claim = None
                self._turn_state = None""", """                self._turn_state = None  # SABOTAGE S12""")],
 'S3s': [('await self.compact(reason="overflow", will_retry=False, _claim=claim)', 'await self.compact(reason="overflow", will_retry=False)  # SABOTAGE S3s')],
 'S13': [("if self._pending_session_writes or self._drain_lock.locked():", "if False:  # SABOTAGE S13")],
 'S14': [("""            # nothing is queued; inside the ``try``, so a cancel here releases.
            await self._drain_pending_session_writes()""", """            pass  # SABOTAGE S14""")],
 'S15': [("""        try:
            await self._drain_pending_session_writes()
        except Exception:  # noqa: BLE001""", """        try:
            pass  # SABOTAGE S15
        except Exception:  # noqa: BLE001""")],
 'S16': [("""        async with self._drain_lock:
            while self._pending_session_writes:""", """        if True:  # SABOTAGE S16
            while self._pending_session_writes:""")],
 'S17': [("""            if not nested:
                # #334 — writes a cancelled prompt left queued land before the""", """            if False:  # SABOTAGE S17
                # #334 — writes a cancelled prompt left queued land before the""")],
 'S18': [("""            # (see :meth:`_drain_pending_session_writes`).
            await self._drain_pending_session_writes()
            old_leaf_id""", """            pass  # SABOTAGE S18
            old_leaf_id""")],
 'S19': [("""            try:
                await self._drain_pending_session_writes()
            except Exception:  # noqa: BLE001""", """            try:
                while self._pending_session_writes:  # SABOTAGE S19
                    await self.flush_pending_session_writes()
            except Exception:  # noqa: BLE001""")],
 'S20': [("""        if not self._pending_session_writes and not self._drain_lock.locked():
            return""", """        if not self._pending_session_writes:  # SABOTAGE S20
            return""")],
 'S21': [("""            try:
                await self._drain_pending_session_writes()
            except Exception:  # noqa: BLE001
                _log.warning(
                    "flush_pending_session_writes raised at the prompt's release""", """            try:
                if self._pending_session_writes:  # SABOTAGE S21 (M10)
                    await self._drain_pending_session_writes()
            except Exception:  # noqa: BLE001
                _log.warning(
                    "flush_pending_session_writes raised at the prompt's release""")],
 'S22': [("""        if not self._pending_session_writes and not self._drain_lock.locked():
            return""", """        await asyncio.sleep(0)  # SABOTAGE S22
        if not self._pending_session_writes and not self._drain_lock.locked():
            return""")],
 'S10d': [("""        # #334 — an ``abort()`` during the read above: no compaction, no re-run
        # (see the same check in :meth:`_check_auto_compaction`).
        if claim is not None and self._abort_requested:
            return False
""", "")],
}

def build(sid: str, out: pathlib.Path) -> pathlib.Path:
    dst = out / sid / "src" / "aelix_agent_core"
    if dst.parent.exists():
        shutil.rmtree(dst.parent)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    core = dst / "harness" / "core.py"
    s = core.read_text(encoding="utf-8")
    for a, b in SAB[sid]:
        assert s.count(a) == 1, (sid, a[:80])
        s = s.replace(a, b)
    core.write_text(s, encoding="utf-8")
    return dst.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/334-sabotage")
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    for sid in args.ids or list(SAB):
        src = build(sid, out)
        env = dict(os.environ, PYTHONPATH=str(src))
        run = subprocess.run(
            ["uv", "run", "--no-sync", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf", *FILES],
            env=env, capture_output=True, text=True, encoding="utf-8",
        )
        lines = run.stdout.splitlines()
        failed = [ln.split(" - ")[0].removeprefix("FAILED ") for ln in lines if ln.startswith("FAILED ")]
        print(f"{sid}: {lines[-1] if lines else run.stderr[-300:]}")
        for name in failed:
            print("   ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
