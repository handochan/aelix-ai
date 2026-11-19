"""#775 (#611 S3a) — a call Ctrl+B never moved runs as ``await`` runs it (ADR-0298 §5).

THE CLAIM, measured cases only. On the TUI host with background jobs on, a
single ``run`` is awaited through :func:`aelix_agents.detach.drive`, and its
row's live wait also waits on a detach future nothing resolves. Each scenario
here runs twice through the real ``AgentsExtension``, the real job service and a
real ``PrintChannel`` child (``test_agent_background_611``'s rig): ``direct`` —
no ``detach_host``, today's bare ``await`` — and ``driven``. Both must give the
same result or cancellation, the same D14 report count and text, the same rows
and cleanup tasks left, the closing records already in the parent's file when
the call returned, and the call's slot back by then.

The scenarios are Codex's four real-channel ones (r3 review N1 A and B, an Esc
pressed twice in one batch, a plain end) and a second Esc 50 ms after the first.

``direct`` rows are guards (green on ``c91cdc7b``). ``driven`` rows fail there:
they also assert that the call was driven — it made an offer while its child
was live — and ``JobService`` has no ``detach`` on that commit.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aelix_agents.child_session as cs
import aelix_agents.runtime as rt
import pytest
from aelix_agents.jobs_client import KIND
from aelix_agents.stream import reduce_event

from tests.agents_ext._detach_rig import (  # noqa: F401 — reap_children is the fixture
    reap_children,
    within,
)
from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.agents_ext.test_stopped_child_reports_604 import _message, _QueueSpy
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs.conftest import wait_for

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason="as test_agent_background_611: the gated rig's real child is proven on win32 by #611 S3c",
)
pytestmark = pytest.mark.usefixtures("jobs_on", "reap_children")

SCENARIOS = ("normal", "finish_then_cancel", "cancel_then_producer", "esc_esc", "esc_then_esc_50ms")
_SLOW = {"cancel_then_producer", "esc_esc", "esc_then_esc_50ms"}


def _script(slow: bool) -> str:
    tail = "time.sleep(20)\n" if slow else "time.sleep(0.04)\ndone()\n"
    return _stub("start()\nsay('halfway')\n" + tail)


async def _observe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, shape: str) -> dict[str, Any]:
    root = tmp_path / shape
    root.mkdir()
    rig = await _rig(root, script=_script(scenario in _SLOW))
    offers: list[Any] = []
    if shape == "driven":
        rig.ext.detach_host = lambda: True
        rig.service.detach.guard = lambda: None
        real_offer = rig.service.detach.offer

        def offer(made: Any) -> Any:
            offers.append(made)
            return real_offer(made)

        rig.service.detach.offer = offer  # type: ignore[method-assign]
    queued: list[Any] = []
    rig.ext._api = _QueueSpy(rig.ext._api, queued)
    runtime = rig.ext.runtime
    assert runtime is not None
    holder: dict[str, Any] = {}
    with monkeypatch.context() as mp:
        mp.setattr(cs, "uuid", SimpleNamespace(uuid4=lambda: uuid.UUID("11111111-1111-4111-8111-111111111111")))
        mp.setattr(rt, "_new_id", lambda: "sub-fixed")
        if scenario == "finish_then_cancel":
            # Codex N1 A: the interrupt is queued in the step the run finishes in.
            facts = rt._SubagentRuntimeImpl._with_run_facts

            def finishing(self: Any, *args: Any, **kwargs: Any) -> Any:
                asyncio.get_running_loop().call_soon(holder["turn"].cancel)
                return facts(self, *args, **kwargs)

            mp.setattr(rt._SubagentRuntimeImpl, "_with_run_facts", finishing)
        turn = asyncio.ensure_future(rig.call({"profile": "scout", "task": "look"}))
        holder["turn"] = turn
        if scenario in _SLOW:
            await wait_for(lambda: any(r.stream.last_text == "halfway" for r in runtime._children.values()))
            holder["row"] = next(iter(runtime._children.values()))
            turn.cancel()
            if scenario == "esc_esc":
                turn.cancel()
            elif scenario == "cancel_then_producer":
                # Codex N1 B: a producer runs after the cancel was delivered.
                asyncio.get_running_loop().call_soon(
                    reduce_event, holder["row"].stream, _message("late output arrived")
                )
            else:
                await asyncio.sleep(0.05)
                turn.cancel()
        try:
            result_text: str | None = _text(await within(turn))
            outcome = "result"
        except asyncio.CancelledError:
            outcome, result_text = "cancelled", None
        entries = await rig.session.get_entries()
        phases = [
            e.data.get("phase") or e.data.get("state")
            for e in entries
            if e.type == "custom" and e.data.get("key") == "sub-fixed"
        ]
        records_at_return = "settle" in phases and "final" in phases
        slot_back_at_return = rig.service.admission.count(KIND) == 0
        await runtime.stop_all()
        await runtime.drain_cleanups()
    owed = getattr(rig.ext, "_owed", {})
    observed = {
        "outcome": outcome,
        "result_text": _timeless(result_text),
        # The D14 report is the cancelled call's result since #800, and owed
        # under its id until a prompt finds it placed (ADR-0307 D1-D2); nothing
        # goes on the next-turn queue any more.
        # ``getattr``: on the base (no owed table) this fails on the assertion.
        "queued": len(queued),
        "d14_count": len(owed),
        "d14_text": _timeless(next(iter(owed.values())).text) if owed else None,
        "rows_left": len(runtime._children),
        "cleanups_left": len(runtime._cleanups),
        "records_at_return": records_at_return,
        "slot_back_at_return": slot_back_at_return,
        "jobs": len(rig.jobs()),
        "offered": bool(offers),
    }
    await rig.service.shutdown()
    return observed


def _timeless(text: str | None) -> str | None:
    """The envelope's footer names the run's seconds (``· 0.1s]``), which load decides."""

    return None if text is None else re.sub(r"· \d+\.\ds\]", "· Ns]", text)


_EXPECTED = {
    "normal": ("result", 0),
    "finish_then_cancel": ("result", 0),
    "cancel_then_producer": ("cancelled", 1),
    "esc_esc": ("cancelled", 1),
    "esc_then_esc_50ms": ("cancelled", 1),
}


@REAL_CHILD
@pytest.mark.parametrize("shape", ["direct", "driven"])
@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_each_scenario_ends_as_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, shape: str
) -> None:
    observed = await _observe(tmp_path, monkeypatch, scenario, shape)
    outcome, reports = _EXPECTED[scenario]
    assert observed["outcome"] == outcome, observed
    assert observed["queued"] == 0, observed
    assert observed["d14_count"] == reports, observed
    if reports:
        assert "halfway" in observed["d14_text"] and "late output arrived" not in observed["d14_text"]
    assert observed["records_at_return"], "the closing records are in the parent's file at the return"
    assert observed["slot_back_at_return"], "the call's slot is back by the step it returned in"
    assert observed["rows_left"] == 0 and observed["cleanups_left"] == 0 and observed["jobs"] == 0
    assert observed["offered"] is (shape == "driven"), "driven means the run parked with an offer"


@REAL_CHILD
@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_driven_equals_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    direct = await _observe(tmp_path, monkeypatch, scenario, "direct")
    driven = await _observe(tmp_path, monkeypatch, scenario, "driven")
    print(f"DRIVER_{scenario}=" + json.dumps({"direct": direct, "driven": driven}))
    assert driven.pop("offered") and not direct.pop("offered")
    assert driven == direct


def test_a_gate_off_or_non_tui_call_builds_no_delegate() -> None:
    """Off the TUI, or with the kind unregistered, ``_detach_call`` is ``None`` — the bare ``await``.

    A guard that holds once the S3a seam exists: on ``c91cdc7b`` it fails at
    ``_detach_call``, which does not exist there (verify N6). The ``direct``
    rows above are what observes the base's behaviour.
    """

    from aelix_agents.extension import AgentsExtension

    ext = AgentsExtension()
    pending = SimpleNamespace(call=SimpleNamespace(mode="single"))
    assert ext._detach_call(pending, "k") is None  # type: ignore[arg-type]
    ext.detach_host = lambda: True
    assert ext._detach_call(pending, "k") is None, "no job kind registered: jobs off, RPC or ACP"
    ext._jobs_facade = SimpleNamespace(offer_detach=None, detach_refused=None)
    assert ext._detach_call(pending, "k") is not None
    for mode in ("parallel", "chain"):
        assert ext._detach_call(SimpleNamespace(call=SimpleNamespace(mode=mode)), "k") is None  # type: ignore[arg-type]
    ext.detach_host = lambda: False
    assert ext._detach_call(pending, "k") is None


def test_only_the_interactive_mode_is_a_detach_host() -> None:
    """Verify F10 M123: ``cli/entry.py`` wires ``detach_host`` to ``app_mode == "interactive"`` —
    not "everything but RPC", which would offer Ctrl+B's delegate to ``-p`` and json."""

    import ast
    import inspect

    from aelix_coding_agent.cli import entry

    tree = ast.parse(inspect.getsource(entry))
    wired = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AgentsExtension"
        for kw in node.keywords
        if kw.arg == "detach_host"
    ]
    assert len(wired) == 1 and isinstance(wired[0], ast.Lambda)
    assert ast.unparse(wired[0].body) == "app_mode == 'interactive'"

