#!/usr/bin/env python3
"""#311: ``prompt()`` detaches the next_turn queue, then awaits two hooks.

``prompt()`` takes the queue away from the harness before it has anywhere
else to put it. Line numbers below are ``main``'s at ``fad2e28``, the tree
this reproduces on; the fix adds 31 lines at :1291, so on the branch the
same statements sit 31 lines lower::

    core.py:1289-1290   drained_next = self._next_turn_queue
                        self._next_turn_queue = []
    core.py:1295-1296   if drained_next:
                            await self._emit_queue_update()      # can raise
    core.py:1299        injected = await self._emit_before_agent_start(text)  # can raise
    core.py:1303        prompts.extend(drained_next)
    core.py:1310        result = await self._run(prompts, ...)   # first place they are held again

Both awaits turn a handler exception into a raised ``AgentHarnessError``
(``_emit_queue_update`` at ``core.py:2294-2309``, ``_emit_before_agent_start``
at ``core.py:4198-4212``) and a handler's ``error_mode`` defaults to
``"throw"`` (``harness/hooks.py:2493-2500``). Between :1290 and :1310 the drained
messages live only in the local ``drained_next``, so either raise unwinds the
frame and the user's queued text exists nowhere.

Run from a worktree root::

    uv run --no-sync python .omc/specs/311-next-turn-drain.py

ARM 5 re-executes this file with a patched copy of ``aelix_agent_core`` on
``PYTHONPATH`` to measure the alternative remedy — moving the emit below
:1303 — and needs no setup of its own.

Measured 2026-09-22.

BEFORE the fix (worktree at fad2e28)::

    ARM 1  queue_update observer throws
      queue after      : []
      VERDICT lost: True
    ARM 2  before_agent_start handler throws
      queue after      : []
      VERDICT lost: True
    ARM 3  ordering when a message arrives during the failing await
      queue after      : ['ARRIVED-DURING-AWAIT']
      VERDICT drained message survived: False
    ARM 4  the next prompt() re-delivers what the failed one drained
      reached stream_fn: []
      VERDICT redelivered: False
AFTER the fix (arms 1-4 run against the working tree; ARM 5 always runs
against ``main``, which is what makes it a rejection rather than a claim)::

    ARM 1  VERDICT lost: False
           queue after: ['QUEUED-BY-NEXT-TURN']
    ARM 2  VERDICT lost: False
           queue after: ['QUEUED-BY-NEXT-TURN']
    ARM 3  VERDICT drained message survived: True
           VERDICT drained message is FIRST: True
           queue after: ['QUEUED-BY-NEXT-TURN', 'ARRIVED-DURING-AWAIT']
    ARM 4  VERDICT redelivered: True
           reached stream_fn: ['QUEUED-BY-NEXT-TURN', 'second prompt']
    ARM 5  main as-is        : VERDICT lost: True
           main + emit moved : VERDICT lost: True
           -- the two runs differ by exactly the move, and the move changes
              nothing, so the emit's POSITION was never the defect.

ARMS 6-8 are the LIMITS of the fix, added after review found the ADR and the
code comment claiming a far edge the code does not have. They pass on the
branch in the sense that they reproduce; none of them is fixed by it::

    ARM 6  the residual window: _run raises before agent_loop has the list
           queue after      : []
           reached stream_fn: []
           in state.messages: []
           VERDICT drained message lost: True
           VERDICT live user message lost too: True
           -- the guard ends at the CALL to _run, and one await
              (``self._session.build_context()``) sits between there and
              ``agent_loop``. Same defect, different trigger, filed separately.
    ARM 7  queue after: ['QUEUED-BY-NEXT-TURN'] / phase after: 'turn'
           next prompt(): AgentHarnessError(AgentHarness is busy …)
           -- a CANCELLED drain restores the queue and leaves the harness
              wedged, so the restored message is never delivered. The base
              wedges the same way (queue [] / phase 'turn'): not this fix's
              doing, not its fix either.
    ARM 7b _current_turn_task: None / prompt() after abort(): still awaiting
           VERDICT abort() cannot reach this window: True
           -- so the CancelledError the guard catches comes from an embedder
              cancelling prompt(), not from abort().
    ARM 8  snapshots seen: [['QUEUED-BY-NEXT-TURN'], []]
           queue in fact : ['QUEUED-BY-NEXT-TURN']
           -- the restore does not re-emit, so an observer's last snapshot
              disagrees with the queue until the next enqueue or drain.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

MARKER = "QUEUED-BY-NEXT-TURN"
LATE = "ARRIVED-DURING-AWAIT"


def _texts(messages: Any) -> list[str]:
    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


def _harness() -> tuple[AgentHarness, list[list[Any]]]:
    seen: list[list[Any]] = []

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(list(context.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")], stop_reason="end_turn"
            )
        )

    return AgentHarness(AgentHarnessOptions(stream_fn=fn)), seen


async def _prompt_expecting_raise(h: AgentHarness, text: str) -> BaseException | None:
    try:
        await h.prompt(text)
    except BaseException as exc:  # noqa: BLE001
        return exc
    return None


async def arm1() -> bool:
    """A throwing ``queue_update`` observer between the detach and the use."""

    print("ARM 1  queue_update observer throws")
    h, seen = _harness()
    # Queue while idle. ``next_turn()`` emits ``queue_update`` itself, so the
    # faulty handler is registered afterwards — an extension that loads late,
    # or any observer that starts failing between two turns.
    await h.next_turn(MARKER)

    def boom(event: Any, _ctx: Any) -> None:
        raise RuntimeError("observer blew up")

    h.hooks.on("queue_update", boom)  # type: ignore[arg-type]
    raised = await _prompt_expecting_raise(h, "live user text")

    print("  prompt() raised  :", repr(raised))
    print("  queue after      :", _texts(h._next_turn_queue))
    print("  reached stream_fn:", [t for p in seen for t in _texts(p)])
    print("  in state.messages:", _texts(h.state.messages))
    lost = (
        raised is not None
        and MARKER not in _texts(h._next_turn_queue)
        and MARKER not in _texts(h.state.messages)
        and not any(MARKER in _texts(p) for p in seen)
    )
    print("  VERDICT lost:", lost)
    return lost


async def arm2() -> bool:
    """The window is wider than the issue said: ``before_agent_start`` too.

    ``_emit_before_agent_start`` (``core.py:1299``) sits inside the same
    detach window and wraps handler exceptions the same way, so a remedy that
    only guards the ``queue_update`` emit leaves half the window open.
    """

    print("ARM 2  before_agent_start handler throws")
    h, seen = _harness()
    await h.next_turn(MARKER)

    def boom(event: Any, _ctx: Any) -> None:
        raise RuntimeError("before_agent_start blew up")

    h.hooks.on("before_agent_start", boom)  # type: ignore[arg-type]
    raised = await _prompt_expecting_raise(h, "live user text")

    print("  prompt() raised  :", repr(raised))
    print("  queue after      :", _texts(h._next_turn_queue))
    lost = raised is not None and MARKER not in _texts(h._next_turn_queue)
    print("  VERDICT lost:", lost)
    return lost


async def arm3() -> list[str]:
    """Ordering: what if a message arrives DURING the await that then fails?

    This is the question the restore has to answer. The handler enqueues a
    fresh next_turn message and only then raises, so at the moment of the
    raise ``self._next_turn_queue`` is non-empty and holds a message that is
    strictly NEWER than everything in ``drained_next``. Appending the restored
    messages would hand the model the user's two sentences out of order.
    """

    print("ARM 3  ordering when a message arrives during the failing await")
    h, seen = _harness()
    await h.next_turn(MARKER)

    async def enqueue_then_boom(event: Any, _ctx: Any) -> None:
        await h.next_turn(LATE)
        raise RuntimeError("before_agent_start blew up after enqueueing")

    h.hooks.on("before_agent_start", enqueue_then_boom)  # type: ignore[arg-type]
    raised = await _prompt_expecting_raise(h, "live user text")

    order = _texts(h._next_turn_queue)
    print("  prompt() raised  :", repr(raised))
    print("  queue after      :", order)
    print("  VERDICT drained message survived:", MARKER in order)
    if MARKER in order and LATE in order:
        print("  VERDICT drained message is FIRST:", order.index(MARKER) < order.index(LATE))
    return order


async def arm4() -> bool:
    """The point of restoring: the NEXT prompt() must deliver the message."""

    print("ARM 4  the next prompt() re-delivers what the failed one drained")
    h, seen = _harness()
    await h.next_turn(MARKER)

    calls = {"n": 0}

    def boom_once(event: Any, _ctx: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("observer blew up")

    h.hooks.on("queue_update", boom_once)  # type: ignore[arg-type]
    await _prompt_expecting_raise(h, "first prompt")
    await h.prompt("second prompt")

    delivered = [t for p in seen for t in _texts(p)]
    print("  reached stream_fn:", delivered)
    ok = MARKER in delivered
    print("  VERDICT redelivered:", ok)
    return ok


CORE_REL = "packages/aelix-agent-core/src/aelix_agent_core/harness/core.py"


def _run_arm1_against(core_source: str, label: str) -> None:
    """Run ARM 1 in a child whose ``aelix_agent_core.harness.core`` is patched.

    Everything else — ``hooks.py`` included — comes from the working tree, so
    the drain is the only thing that differs between the two calls below.
    """

    import aelix_agent_core

    pkg = pathlib.Path(aelix_agent_core.__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        dst_root = pathlib.Path(tmp) / "src"
        dst = dst_root / pkg.name
        shutil.copytree(pkg, dst)
        (dst / "harness" / "core.py").write_text(core_source)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(dst_root) + os.pathsep + env.get("PYTHONPATH", "")
        out = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "--arm1"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        for line in out.stdout.splitlines():
            if "VERDICT" in line:
                print(f"  {label}: {line.strip()}")
        if out.returncode != 0:
            print(f"  {label} stderr:", out.stderr.strip()[-400:])


def arm5() -> None:
    """The alternative remedy, measured instead of argued.

    Moving ``await self._emit_queue_update()`` below
    ``prompts.extend(drained_next)`` (:1303) looks like it closes the window.
    It does not: ``prompts`` is a local too, and the drained messages are not
    held anywhere the harness can find them until ``_run`` hands them to
    ``agent_loop``.

    Measured against the BASE file (``git show <ref>:core.py``, default
    ``main``), because that is the tree the alternative was a candidate for.
    The control run is the same base file untouched, so the two lines below
    differ by exactly the move.
    """

    ref = os.environ.get("AELIX_311_BASE", "main")
    print(f"ARM 5  alternative: emit moved below prompts.extend(drained_next)  [{ref}]")
    show = subprocess.run(
        ["git", "show", f"{ref}:{CORE_REL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        print("  SKIPPED: cannot read the base file —", show.stderr.strip()[-200:])
        return
    base = show.stdout
    emit = "            if drained_next:\n                await self._emit_queue_update()\n"
    anchor = "            prompts.extend(drained_next)\n"
    if base.count(emit) != 1 or base.count(anchor) != 1:
        print(f"  SKIPPED: {ref} does not have the drain shape this arm patches")
        return
    moved = base.replace(emit, "", 1).replace(anchor, anchor + emit, 1)
    _run_arm1_against(base, f"{ref} as-is        ")
    _run_arm1_against(moved, f"{ref} + emit moved")


class _BoomSession:
    """A session whose ``build_context()`` raises, as a real one can.

    ``Session.build_context`` (``session/session.py:149-154``) goes through
    ``get_branch`` → ``storage.get_leaf_id()`` / ``get_path_to_root()``. The
    shipped ``JsonlSessionStorage`` raises ``SessionError`` from both when its
    entry index does not resolve (``jsonl_storage.py:660-666``, ``:703-721``),
    and ``SessionStorage`` is a public Protocol, so an embedder's
    implementation can raise anything at all.
    """

    async def build_context(self) -> Any:
        raise RuntimeError("session storage unavailable")


async def arm6() -> bool:
    """The far edge is the CALL to ``_run`` — not "until ``_run`` has them".

    ``_run`` receives ``prompts`` and then awaits
    ``self._session.build_context()`` (``core.py:4466``) BEFORE
    ``agent_loop(prompts, ...)`` (``core.py:4683``) ever sees the list; that
    is the only await on the straight-line path between the two. Until it
    returns, ``prompts`` is still nothing but a local and the guard has
    already been left behind, so a session that raises there loses the drained
    message in the same three places ARM 1 measures — on no queue, in no turn,
    in no exception. Same defect, different trigger; #311 does NOT close it,
    and the prose says so rather than claiming an edge the code does not have.

    The live user message goes with it, which is why widening the guard is not
    the remedy: this window loses a turn's whole input, not just the queue.
    """

    print("ARM 6  the residual window: _run raises before agent_loop has the list")
    h, seen = _harness()
    await h.next_turn(MARKER)
    h._session = _BoomSession()  # type: ignore[assignment]

    raised = await _prompt_expecting_raise(h, "live user text")

    print("  prompt() raised  :", repr(raised))
    print("  queue after      :", _texts(h._next_turn_queue))
    print("  reached stream_fn:", [t for p in seen for t in _texts(p)])
    print("  in state.messages:", _texts(h.state.messages))
    lost = (
        raised is not None
        and MARKER not in _texts(h._next_turn_queue)
        and MARKER not in _texts(h.state.messages)
        and not any(MARKER in _texts(p) for p in seen)
    )
    print("  VERDICT drained message lost:", lost)
    print(
        "  VERDICT live user message lost too:",
        "live user text" not in _texts(h.state.messages),
    )
    print("  phase after      :", repr(h._phase))
    return lost


async def arm7() -> bool:
    """A cancelled drain hands the messages back — and nothing can send them.

    The restore catches ``BaseException`` so a ``CancelledError`` arriving on
    one of the two awaits puts the queue back. It does, and the test asserts
    exactly that much. One assertion further on the harness is still wedged:
    ``prompt()``'s outer reset is ``except Exception`` (``core.py:1457``),
    which a ``CancelledError`` walks straight past, so ``_phase`` stays
    ``"turn"`` and every later ``prompt()`` is refused by the busy guard
    (``core.py:1225-1228``) before a hook can run. The base sticks the phase
    the same way — this is not this change's doing, and not its fix either.
    """

    print("ARM 7  after a cancelled drain, can the restored message ever ship?")
    h, _seen = _harness()
    await h.next_turn(MARKER)
    entered = asyncio.Event()

    async def block(event: Any, _ctx: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    h.hooks.on("queue_update", block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.prompt("live user text"))
    await entered.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    print("  queue after      :", _texts(h._next_turn_queue))
    print("  phase after      :", repr(h._phase))
    nxt = await _prompt_expecting_raise(h, "next prompt")
    print("  next prompt()    :", repr(nxt))
    wedged = h._phase != "idle"
    print("  VERDICT restored but undeliverable:", wedged)
    return wedged


async def arm7b() -> bool:
    """``abort()`` is NOT the caller that delivers that ``CancelledError``.

    The clause is still right to catch ``BaseException``, but the reason first
    written down was not: ``abort()`` cancels ``_current_turn_task``
    (``core.py:1547-1549``) and nothing else, and that attribute is assigned
    inside ``_run`` — which this window is in front of. So an abort arriving
    on one of these two awaits does not unwind ``prompt()`` at all. The
    ``CancelledError`` the guard exists for comes from whoever cancels the
    ``prompt()`` task itself: an embedder, or loop shutdown. Nothing in the
    shipped product does (the RPC prompt task is only awaited, never
    cancelled: ``rpc_mode.py:404-411``, ``core.py:3245-3248``).
    """

    print("ARM 7b abort() during the drain window: does prompt() even unwind?")
    h, _seen = _harness()
    await h.next_turn(MARKER)
    entered = asyncio.Event()

    async def block(event: Any, _ctx: Any) -> None:
        entered.set()
        await asyncio.Event().wait()

    # ``before_agent_start`` rather than ``queue_update``, because ``abort()``
    # emits ``queue_update`` itself and would block on its own hook.
    h.hooks.on("before_agent_start", block)  # type: ignore[arg-type]
    task = asyncio.ensure_future(h.prompt("live user text"))
    await entered.wait()

    print("  _current_turn_task:", h._current_turn_task)
    await asyncio.wait_for(h.abort(), timeout=5)
    await asyncio.sleep(0)
    still_running = not task.done()
    print("  prompt() after abort():", "still awaiting the hook" if still_running else "done")
    print("  queue after abort()   :", _texts(h._next_turn_queue))
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    print("  VERDICT abort() cannot reach this window:", still_running)
    return still_running


async def arm8() -> list[list[str]]:
    """What the observer's last ``queue_update`` says after the restore.

    The emit fires at the drain, so the snapshot observers hold is the EMPTY
    queue. The restore puts the messages back without emitting again, so an
    extension painting a queue indicator off this event shows nothing queued
    while one message is queued. It corrects itself on the next enqueue or the
    next turn's drain.
    """

    print("ARM 8  what the queue_update observer last saw")
    h, _seen = _harness()
    snapshots: list[list[str]] = []

    def record(event: Any, _ctx: Any) -> None:
        snapshots.append(_texts(event.next_turn))

    def boom(event: Any, _ctx: Any) -> None:
        raise RuntimeError("observer blew up")

    h.hooks.on("queue_update", record)  # type: ignore[arg-type]
    await h.next_turn(MARKER)
    h.hooks.on("queue_update", boom)  # type: ignore[arg-type]
    await _prompt_expecting_raise(h, "live user text")

    print("  snapshots seen   :", snapshots)
    print("  queue in fact    :", _texts(h._next_turn_queue))
    return snapshots


async def main() -> None:
    if "--arm1" in sys.argv:
        await arm1()
        return
    await arm1()
    print()
    await arm2()
    print()
    await arm3()
    print()
    await arm4()
    print()
    arm5()
    print()
    await arm6()
    print()
    await arm7()
    print()
    await arm7b()
    print()
    await arm8()


if __name__ == "__main__":
    asyncio.run(main())
