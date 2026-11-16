"""#775 (#611 S3a) — what may be suspended across the point a run moves at (ADR-0298 §2, Codex M1).

A coroutine moved to another task carries every frame suspended across the
move. :func:`aelix_agents.detach.audit` admits a function only when no ``with``
encloses its crossing await, the await calls the next function directly, and it
names no timeout, ``TaskGroup``, ``current_task``, ``ContextVar`` or
``copy_context``; every commit walks the suspended chain and refuses a frame
that was not admitted.

* The product chain — ``spawn_granted``/``resume_granted`` → ``_run`` →
  ``PrintChannel.run`` → ``_stream_and_reap`` → ``migrate_point`` — is admitted,
  all five, none refused. A future edit that wraps one of those awaits turns
  Ctrl+B off silently; this test makes it loud.
* The modules a moved run executes name none of those constructs at all, so a
  helper that returned before the point cannot have bound the run to a task.
* Codex's five negative probes (a ContextVar token, ``asyncio.timeout``, a
  ``TaskGroup``, ``current_task``, ``wait_for``) each stay in their task.

Guards: green once S3a's seam exists (contract r4 §7), so all of this fails on
``c91cdc7b`` at the import of ``aelix_agents.detach``. The probes run on
3.11–3.14 (3.11's ``wait_for`` runs the chain in a task of its own, refused by
``migrate_point``'s task check before any commit).
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import importlib
import inspect
import sys
from asyncio import current_task as _who
from contextvars import ContextVar as _MakeCell
from contextvars import copy_context as _cc
from typing import Any

import pytest
from aelix_agents.detach import (
    Allowlist,
    Detached,
    Plan,
    drive,
    migrate_point,
    product_allowlist,
    run_ordered_commit,
    task_factory_installed,
)

MOVED_MODULES = (
    "aelix_agents.runtime",
    "aelix_agents.print_channel",
    "aelix_agents.reaper",
    "aelix_agents.stream",
    "aelix_agents.envelope",
    "aelix_agents.child_session",
    "aelix_coding_agent.tools.launch_ledger",
)
_TASK_BOUND = {"current_task", "ContextVar", "TaskGroup", "copy_context", "timeout_at"}


def test_the_product_chain_is_admitted_five_of_five() -> None:
    allow = product_allowlist()
    assert len(allow.codes) == 5, allow.refused
    assert allow.refused == {}


@pytest.mark.parametrize("module", MOVED_MODULES)
def test_no_task_bound_construct_in_a_module_a_moved_run_executes(module: str) -> None:
    tree = ast.parse(inspect.getsource(importlib.import_module(module)))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = node.attr
            if name == "timeout" and isinstance(node.value, ast.Name) and node.value.id == "asyncio":
                found.append(("asyncio.timeout", node.lineno))
        else:
            name = node.id if isinstance(node, ast.Name) else None
        if name in _TASK_BOUND:
            found.append((name, node.lineno))
    assert found == []


def test_a_wrapped_or_guarded_crossing_is_refused_by_the_audit() -> None:
    from aelix_agents.detach import audit

    async def wrapped() -> None:
        await asyncio.shield(_next())

    async def guarded() -> None:
        with open(__file__):
            await _next()

    async def plain() -> None:
        await _next()

    assert audit(wrapped, {"_next"}) == (False, "shield wraps the crossing call")
    assert audit(guarded, {"_next"}) == (False, "a with-block encloses the crossing await of _next")
    assert audit(plain, {"_next"}) == (True, "ok")
    assert audit(plain, {"elsewhere"}) == (False, "no crossing await")


async def _next() -> None:
    return None


# --- Codex's five negative probes ----------------------------------------------------

_VAR: contextvars.ContextVar[str] = contextvars.ContextVar("detach_probe", default="default")


async def _context_inner(release: asyncio.Event) -> None:
    _VAR.set("inner")
    await migrate_point()
    await release.wait()


async def _timeout_inner(release: asyncio.Event) -> None:
    async with asyncio.timeout(0.025):
        await migrate_point()
        await asyncio.sleep(0.06)


async def _taskgroup_inner(release: asyncio.Event) -> None:
    async with asyncio.TaskGroup():
        await migrate_point()
        await release.wait()


async def _identity_inner(release: asyncio.Event) -> None:
    asyncio.current_task()
    await migrate_point()
    await release.wait()


async def _plain_inner(release: asyncio.Event, log: dict[str, Any]) -> str:
    log["migrated"] = await migrate_point()
    await release.wait()
    return "done"


async def _wait_for_inner(release: asyncio.Event, log: dict[str, Any]) -> str:
    return await asyncio.wait_for(_plain_inner(release, log), 1)


# Codex round 1 #5: the same constructs under other names (verify F7).


async def _aliased_context_inner(release: asyncio.Event) -> None:
    cell = _MakeCell("aliased")
    token = cell.set("value")
    await migrate_point()
    await release.wait()
    cell.reset(token)


async def _aliased_task_inner(release: asyncio.Event) -> None:
    before = _who()
    await migrate_point()
    await release.wait()
    assert before is _who()


def _begin_hidden() -> contextvars.Token[str]:
    return _VAR.set("hidden")


def _end_hidden(token: contextvars.Token[str]) -> None:
    _VAR.reset(token)


async def _hidden_inner(release: asyncio.Event) -> None:
    token = _begin_hidden()  # a helper that returned before the point; its token is held across it
    await migrate_point()
    await release.wait()
    _end_hidden(token)


async def _probe(kind: str) -> dict[str, Any]:
    release, log, refusals = asyncio.Event(), {}, []
    inner_fn, args = {
        "context": (_context_inner, (release,)),
        "timeout": (_timeout_inner, (release,)),
        "taskgroup": (_taskgroup_inner, (release,)),
        "identity": (_identity_inner, (release,)),
        "wait_for": (_wait_for_inner, (release, log)),
        "aliased_context": (_aliased_context_inner, (release,)),
        "aliased_task": (_aliased_task_inner, (release,)),
        "hidden_helper": (_hidden_inner, (release,)),
    }[kind]
    allow = Allowlist()
    admitted = allow.admit(inner_fn, {"migrate_point", "_plain_inner"})
    allow.admit(_plain_inner, {"migrate_point"})

    async def resume(box: Any) -> Any:
        return await drive(box.inner, None, first=True)

    def commit(inner: Any) -> Detached | None:
        box = run_ordered_commit(
            inner,
            checks=[
                task_factory_installed,
                lambda: None if allow.chain_allowed(inner) else "a frame across the move is not in the audited set",
            ],
            build=lambda box: Plan(
                start=lambda: resume(box),
                rescue=lambda: None,
                publish=[],
                take_slot=lambda: None,
                result=Detached(job_id="j", agent_id="a", text="moved"),
            ),
            on_refused=refusals.append,
        )
        return box

    task = asyncio.ensure_future(drive(inner_fn(*args), commit))
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    try:
        result = await asyncio.wait_for(task, 2)
        outcome = "moved" if isinstance(result, Detached) else "stayed in its task"
    except Exception as exc:  # noqa: BLE001
        outcome = type(exc).__name__
    return {"admitted": admitted, "refusals": refusals, "outcome": outcome, "log": log}


@pytest.mark.parametrize(
    "kind",
    ["context", "timeout", "taskgroup", "identity", "wait_for", "aliased_context", "aliased_task", "hidden_helper"],
)
async def test_codex_negative_probe_does_not_move(kind: str) -> None:
    observed = await _probe(kind)
    print(f"M1_{kind}_py{sys.version_info[0]}.{sys.version_info[1]}={observed}")
    assert observed["admitted"] is False
    assert observed["outcome"] == ("TimeoutError" if kind == "timeout" else "stayed in its task")
    if kind == "wait_for" and sys.version_info < (3, 12):
        # 3.11's wait_for runs the chain in a task of its own: no commit is reached.
        assert observed["refusals"] == [] and observed["log"]["migrated"] is False
    else:
        assert observed["refusals"] == ["a frame across the move is not in the audited set"]
    if kind == "wait_for":
        assert observed["log"] == {"migrated": False}, "a refused run is told it stays"


async def test_the_delegate_closes_the_run_it_drives_and_forgets_its_task() -> None:
    import aelix_agents.detach as dt

    ran: list[str] = []
    parked = asyncio.get_running_loop().create_future()

    async def inner() -> None:
        try:
            await parked
        finally:
            ran.append("inner finally")

    task = asyncio.ensure_future(drive(inner()))
    await asyncio.sleep(0)
    assert dt._DRIVING.get(task) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ran == ["inner finally"] and task not in dt._DRIVING, "no entry outlives the drive"
    closed: list[str] = []

    async def held() -> None:
        try:
            await asyncio.get_running_loop().create_future()
        finally:
            closed.append("closed")

    run = held()  # held here too, so only the delegate's own close can end it
    outer = drive(run)
    outer.send(None)  # parked at the future
    outer.close()
    assert closed == ["closed"] and run.cr_frame is None, "closing the delegate closes the run it drives"


def test_a_chain_of_migrate_point_alone_is_not_a_run() -> None:
    allow = Allowlist()
    point = migrate_point()
    try:
        assert allow.chain_allowed(point) is False
    finally:
        point.close()
    assert allow.chain_allowed(object()) is False


def test_the_call_tap_goes_quiet_once_released() -> None:
    from aelix_agents.detach import CallDetach

    seen: list[int] = []
    call = CallDetach(adopt=lambda c, i: None, offer=lambda c: lambda: None)
    tap = call.guard(seen.append)
    tap(1)
    call.released = True
    tap(2)
    assert seen == [1]


async def test_migrate_point_outside_a_driving_task_returns_false_at_once() -> None:
    assert await migrate_point() is False

    async def inner() -> Any:
        return await migrate_point()

    assert await asyncio.ensure_future(inner()) is False


def test_the_audit_sees_aliases_closures_and_helpers() -> None:
    """Codex r1 5, verify F7, F10 (M009). On ``fb5e7654`` the alias, the local import and the
    returned helper were admitted; the closure cell was refused only by the closure check."""

    from aelix_agents.detach import audit

    cell_var: contextvars.ContextVar[int] = contextvars.ContextVar("closed_over")

    async def closes_over() -> None:
        cell_var.set(1)
        await _next()

    async def imports_locally() -> None:
        from asyncio import current_task as mine

        mine()
        await _next()

    assert audit(_aliased_task_inner, {"migrate_point"}) == (False, "_who is current_task")
    assert audit(_aliased_context_inner, {"migrate_point"}) == (False, "_MakeCell is ContextVar")
    assert audit(_hidden_inner, {"migrate_point"}) == (
        False,
        "its helper _begin_hidden reaches the ContextVar _VAR",
    )
    assert audit(closes_over, {"_next"}) == (False, "reaches the ContextVar a closure cell")
    assert audit(imports_locally, {"_next"}) == (False, "imports current_task")

    async def handed_a_timeout(timeout: Any) -> None:
        limit = timeout(1)  # whatever it is, a name the crossing function itself may not use
        await _next()
        del limit

    async def in_a_lambda() -> None:
        later = lambda: _who()  # noqa: E731 — the alias read only inside nested code
        await _next()
        del later

    async def through_a_timeout_helper() -> None:
        limit = _a_timeout_helper()
        await _next()
        del limit

    assert audit(handed_a_timeout, {"_next"}) == (False, "names timeout")
    assert audit(in_a_lambda, {"_next"}) == (False, "_who is current_task")
    assert audit(through_a_timeout_helper, {"_next"}) == (
        False,
        "its helper _a_timeout_helper names asyncio.timeout",
    )


def _a_timeout_helper() -> Any:
    timeout = 1.0  # a helper may name a parameter or a local ``timeout``; asyncio's is what counts
    return asyncio.timeout(timeout)


# Verify round 2 F3 (D13, D17, D08b): how deep the helper walk goes, and copy_context aliased.


def _leaf() -> contextvars.Token[str]:
    return _VAR.set("deep")


def _via1() -> contextvars.Token[str]:
    return _leaf()


def _via2() -> contextvars.Token[str]:
    return _via1()


def _via3() -> contextvars.Token[str]:
    return _via2()


async def _leaf_at_depth_2() -> None:
    token = _via1()
    await _next()
    del token


async def _leaf_at_depth_3() -> None:
    token = _via2()
    await _next()
    del token


async def _leaf_at_depth_4() -> None:
    token = _via3()
    await _next()
    del token


async def _copies_the_context() -> None:
    ctx = _cc()
    await _next()
    del ctx


def test_the_helper_walk_goes_three_calls_deep_and_no_further() -> None:
    from aelix_agents.detach import audit

    reached = (False, "its helper _leaf reaches the ContextVar _VAR")
    assert audit(_leaf_at_depth_2, {"_next"}) == reached
    assert audit(_leaf_at_depth_3, {"_next"}) == reached
    assert audit(_leaf_at_depth_4, {"_next"}) == (True, "ok"), "a fourth call is not followed (ADR-0298 limits)"
    assert audit(_copies_the_context, {"_next"}) == (False, "_cc is copy_context")

