"""pytest plugin (-p v446_plug): r2verify's runtime mutants for #446; $V446_MUT, comma-separated.

Nothing tracked is edited; each mutant is a plausible wrong product or test shape.

Timeout leg (the site's ``_end_the_tree``):
  end:nokill      reap only, no kill at all
  end:rootonly    ``proc.kill()`` on the root only, then the reap
  end:reapfirst   reap first (REAP_GRACE), then the real kill
  end:latekill    sleep 1.0 s, then the real ``_end_the_tree`` (control: a late but whole kill)
Site's timed wait, as the SITE calls it (wrapped outside the witness):
  site:short      hands proc.wait half the caller's timeout
  site:long       hands proc.wait the caller's timeout + 5 s
  site:latestart  starts its timed wait 1.0 s late (deadline from a later origin)
  site:left       hands what is left of the timeout after 0.3 s (an earlier origin)
The wait itself (inside the witness, Popen.wait):
  wait:blind      the first timed wait sleeps its timeout and raises without reaping
  wait:early      the first timed wait gives up 0.5 s before its deadline
  wait:late       the first timed wait gives up 1.0 s past its deadline
Witness look:
  look:signerr    due = waited_at + 0.25 (sign error)
  look:atonce     due = waited_at
Rule (module attributes):
  rule:noahead, rule:slack045, rule:zombienolook, rule:acceptall, rule:lateslack1
Every mutant that fires appends a line to $V446_LOG.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time

_MUT = {m for m in os.environ.get("V446_MUT", "").split(",") if m}
_FIRED: dict[str, bool] = {}


def _log(line: str) -> None:
    path = os.environ.get("V446_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _first(key: str) -> bool:
    if _FIRED.get(key):
        return False
    _FIRED[key] = True
    return True


def pytest_configure(config):  # noqa: ARG001
    from aelix_coding_agent.tools import bash as bash_module

    real_end = bash_module._end_the_tree

    def end(tree, proc, *, reap):
        t0 = time.monotonic()
        if "end:nokill" in _MUT:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=reap)
        elif "end:rootonly" in _MUT:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=reap)
        elif "end:reapfirst" in _MUT:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=reap)
            real_end(tree, proc, reap=reap)
        elif "end:latekill" in _MUT:
            time.sleep(1.0)
            real_end(tree, proc, reap=reap)
        else:
            real_end(tree, proc, reap=reap)
        _log(f"end_the_tree mut={sorted(m for m in _MUT if m.startswith('end:'))} root={proc.pid} rc={proc.returncode} took={time.monotonic() - t0:.3f}")

    if any(m.startswith("end:") for m in _MUT):
        bash_module._end_the_tree = end

    real_retained = bash_module._retained_handle

    def retained(proc):
        inner = proc.wait

        fired_here = {"v": False}

        def outer(timeout=None):
            if "site:latestart_all" in _MUT and timeout is not None and not fired_here["v"]:
                fired_here["v"] = True
                delay = float(os.environ.get("V446_DELAY", "1.0"))
                _log(f"site starts its timed wait {delay} s late (pid {proc.pid}, timeout {timeout})")
                time.sleep(delay)
                return inner(timeout)
            if timeout is not None and _first("site"):
                if "site:short" in _MUT:
                    _log(f"site hands {timeout / 2} instead of {timeout}")
                    return inner(timeout / 2)
                if "site:long" in _MUT:
                    _log(f"site hands {timeout + 5} instead of {timeout}")
                    return inner(timeout + 5)
                if "site:latestart" in _MUT:
                    _log("site starts its timed wait 1.0 s late")
                    time.sleep(1.0)
                    return inner(timeout)
                if "site:left" in _MUT:
                    _log(f"site hands {timeout - 0.3} (what is left after 0.3 s)")
                    return inner(timeout - 0.3)
            return inner(timeout)

        proc.wait = outer
        return real_retained(proc)

    if any(m.startswith("site:") for m in _MUT):
        bash_module._retained_handle = retained

    real_wait = subprocess.Popen.wait

    def wait(self, timeout=None):
        if timeout is not None and timeout <= 3.0 and _first("wait"):
            if "wait:blind" in _MUT:
                _log(f"blind wait on {self.pid}: sleeps {timeout}s, does not reap")
                time.sleep(timeout)
                raise subprocess.TimeoutExpired(self.args, timeout)
            if "wait:early" in _MUT:
                _log(f"early wait on {self.pid}: gives up 0.5 s before {timeout}s")
                real_wait(self, max(0.0, timeout - 0.5))
                return self.returncode
            if "wait:late" in _MUT:
                try:
                    return real_wait(self, timeout)
                except subprocess.TimeoutExpired:
                    _log(f"late wait on {self.pid}: gives up 1.0 s past {timeout}s")
                    time.sleep(1.0)
                    raise
        return real_wait(self, timeout)

    if any(m.startswith("wait:") for m in _MUT):
        subprocess.Popen.wait = wait


def pytest_collection_modifyitems(session, config, items):  # noqa: ARG001
    seen = set()
    for item in items:
        m = getattr(item, "module", None)
        if m is None or not hasattr(m, "_TimeoutWitness") or id(m) in seen:
            continue
        seen.add(id(m))
        if "look:signerr" in _MUT or "look:atonce" in _MUT:
            mode = "signerr" if "look:signerr" in _MUT else "atonce"

            def _look(self, _mode=mode, _m=m):
                if not self._armed.wait(_m.OUTLIVE):
                    return
                waited_at, waited_for, root = self.waited_at, self.waited_for, self.root
                if waited_at is None or waited_for is None or root is None:
                    return
                due = waited_at + _m._LOOK_BEFORE_THE_DEADLINE if _mode == "signerr" else waited_at
                if self._back.wait(max(0.0, due - time.monotonic())):
                    return
                began = time.monotonic()
                state = _m.probe_state(root)
                self.look_began, self.looked_at, self.looked_saw = began, time.monotonic(), state
                _log(f"look ({_mode}) began {began - waited_at:+.3f}s from the wait's start, saw {state}")

            m._TimeoutWitness._look = _look
        real = m._why_the_root_timed_out
        if "rule:slack045" in _MUT:
            m._EARLY_TIMEOUT_SLACK = 0.45
        if "rule:lateslack1" in _MUT:
            m._LATE_TIMEOUT_SLACK = 1.0
        if "rule:acceptall" in _MUT:
            m._why_the_root_timed_out = lambda witness, *, started, timeout: None
        if "rule:zombienolook" in _MUT:

            def rule(witness, *, started, timeout, _m=m, _real=real):
                if (
                    witness.timed_out_at is not None
                    and witness.waited_for == timeout
                    and witness.root_then == _m.STATE_ZOMBIE
                    and witness.look_began is None
                ):
                    return None
                return _real(witness, started=started, timeout=timeout)

            m._why_the_root_timed_out = rule
        if "rule:noahead" in _MUT:

            def rule2(witness, *, started, timeout, _m=m, _real=real):
                if (
                    witness.timed_out_at is not None
                    and witness.root_then != _m.STATE_ALIVE
                    and witness.looked_saw not in (None, _m.STATE_ALIVE)
                ):
                    saved = _m._EARLY_TIMEOUT_SLACK
                    # every dead look is 'too late to place'
                    return "the root was dead at a look - a look too late to place its exit"
                return _real(witness, started=started, timeout=timeout)

            m._why_the_root_timed_out = rule2
