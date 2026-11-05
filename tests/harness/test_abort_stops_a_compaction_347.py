"""#347 — an abort stops a running compaction now, and nothing of it is written.

Every row of the design's table, for each of the three compactions, on a real
:class:`AgentHarness` and a JSONL session read back from disk. What is measured is
the summary being CANCELLED, not merely outlived: a summarizer that holds records
the cancel it gets, and nobody releases it. The moments are reached by a trigger of
their own, never by a sleep (``tests/harness/test_abort_tears_down_the_provider_socket.py``
for the socket: a server that reports the moment the peer goes).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.harness.hooks import SessionBeforeCompactResult
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.types import AutoRetryStartEvent
from aelix_ai import clear_providers, register_provider, unregister_provider
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.streaming import Model

from tests.harness.test_abort_tears_down_the_provider_socket import _HoldingProvider
from tests.harness.test_withdraw_unanswered_input_654 import (
    _overflow_run,
    _Provider,
    _Run,
    _said,
    _start,
)

KINDS = ["manual", "threshold", "overflow"]
WAIT = 5.0


@dataclass
class _Summary:
    """A summarizer override: holds until cancelled, and says what became of it."""

    hold: bool = True
    swallow: bool = False
    on_entry: Any = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: int = 0
    calls: int = 0

    async def __call__(self, _model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        self.calls += 1
        self.entered.set()
        if self.on_entry is not None:
            self.on_entry()
        if self.hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                if not self.swallow:
                    raise
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )


async def _compactions(run: _Run) -> int:
    """Off the file, reopened: what a resume would find."""

    assert run.path is not None
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(run.path)))
    return len([e for e in await reopened.get_entries() if e.type == "compaction"])


async def _rig(tmp_path: Path, kind: str, summary: Any) -> _Run:
    provider = _Provider({"manual": "answer", "threshold": "big_answer", "overflow": "overflow"}[kind])
    return await _overflow_run(tmp_path, provider, summary)


def _begin(run: _Run, kind: str) -> asyncio.Task[Any]:
    if kind == "manual":
        return asyncio.create_task(run.harness.compact())
    return asyncio.create_task(run.harness.prompt("THE PROMPT"))


async def _end(run: _Run, kind: str, task: asyncio.Task[Any]) -> None:
    """Without releasing anything: the row's own abort is what lets the call return."""

    if kind == "manual":
        with pytest.raises(AgentHarnessError, match="Compaction cancelled") as caught:
            await asyncio.wait_for(task, WAIT)
        assert caught.value.code == "compaction"
    else:
        await asyncio.wait_for(task, WAIT)


async def _assert_nothing_written(run: _Run, kind: str) -> None:
    assert await _compactions(run) == 0
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.reason, e.aborted, e.error_message) for e in ends] == [(kind, True, None)]
    assert run.harness.phase == "idle"
    assert run.harness._summary_task is None
    if kind == "threshold":
        assert (await run.disk())[-2:] == ["THE PROMPT", "answer"]


# === The table ==============================================================


@pytest.mark.parametrize("kind", KINDS)
async def test_a_summary_in_flight_is_cut_at_once_and_nothing_is_written(
    tmp_path: Path, kind: str
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)
    task = _begin(run, kind)
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await _end(run, kind, task)

    assert summary.cancelled == 1
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_an_abort_in_the_hook_counts_when_it_returns_and_sends_no_request(
    tmp_path: Path, kind: str
) -> None:
    """The hook is not cut: it runs on the compaction's own task, and one that parks
    holds the compaction, as it did before #347 (ADR-0023's limit 1)."""

    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled: list[bool] = []

    async def hook(_event: Any, _ctx: Any) -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    run.harness.hooks.on("session_before_compact", hook)
    task = _begin(run, kind)
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done(), "the hook holds it"
    assert run.harness.phase == "compaction"
    release.set()
    await _end(run, kind, task)

    assert cancelled == []
    assert summary.calls == 0
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("how", ["abort", "ctx.abort"])
async def test_an_abort_between_the_hook_and_the_request_sends_none(
    tmp_path: Path, kind: str, how: str
) -> None:
    """``abort``: lands in the loop turn after the hook ended, so nothing is left to cancel."""

    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)

    async def hook(_event: Any, _ctx: Any) -> None:
        if how == "abort":
            asyncio.ensure_future(run.harness.abort())
        else:
            run.harness._mark_abort()

    run.harness.hooks.on("session_before_compact", hook)
    task = _begin(run, kind)
    await _end(run, kind, task)

    assert summary.calls == 0
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_hook_that_provides_the_compaction_after_an_abort_writes_nothing(
    tmp_path: Path, kind: str
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)

    async def hook(event: Any, _ctx: Any) -> SessionBeforeCompactResult:
        await run.harness.abort()
        return SessionBeforeCompactResult(
            compaction=CompactResult(
                summary="FROM A HOOK",
                first_kept_entry_id=event.preparation.first_kept_entry_id,
                tokens_before=99,
            )
        )

    run.harness.hooks.on("session_before_compact", hook)
    await _end(run, kind, _begin(run, kind))

    assert summary.calls == 0
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_a_hook_that_raises_after_an_abort_reads_as_cancelled(
    tmp_path: Path, kind: str
) -> None:
    """A raise after an abort is that abort's, whatever raised, and the end says one thing."""

    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)

    async def hook(_event: Any, _ctx: Any) -> None:
        await run.harness.abort()
        raise RuntimeError("a hook's failure")

    run.harness.hooks.on("session_before_compact", hook)
    await _end(run, kind, _begin(run, kind))

    assert summary.calls == 0
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("swallow", [False, True], ids=["returned", "swallowed"])
async def test_a_summary_already_back_writes_no_entry(
    tmp_path: Path, kind: str, swallow: bool
) -> None:
    """``returned``: it returns on its own, and the abort lands before the caller wakes.
    ``swallowed``: a summarizer that eats the cancel, and returns (a limit of #347)."""

    summary = _Summary(hold=swallow, swallow=swallow)
    run = await _rig(tmp_path, kind, summary)
    if not swallow:
        summary.on_entry = lambda: asyncio.ensure_future(run.harness.abort())
    task = _begin(run, kind)
    if swallow:
        await asyncio.wait_for(summary.entered.wait(), WAIT)
        await run.harness.abort()
    await _end(run, kind, task)

    assert summary.cancelled == int(swallow)
    await _assert_nothing_written(run, kind)


@pytest.mark.parametrize("kind", KINDS)
async def test_an_entry_written_stands(tmp_path: Path, kind: str) -> None:
    """The barrier of ADR-0286: the hook after the write is not cancelled."""

    summary = _Summary(hold=False)
    run = await _rig(tmp_path, kind, summary)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hook(_event: Any, _ctx: Any) -> None:
        entered.set()
        await release.wait()

    run.harness.hooks.on("session_compact", hook)
    task = _begin(run, kind)
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort(withdraw_unanswered=True)
    assert run.harness.phase == "compaction"
    release.set()
    await asyncio.wait_for(task, WAIT)

    assert await _compactions(run) == 1
    assert run.withdrawn == []
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.aborted, e.error_message, e.result is not None) for e in ends] == [(False, None, True)]


@pytest.mark.parametrize("kind", KINDS)
async def test_a_failure_after_the_write_reports_as_itself_under_an_abort(
    tmp_path: Path, kind: str
) -> None:
    """``compaction_end`` must not say aborted over an entry on file, nor hide the failure."""

    summary = _Summary(hold=False)
    run = await _rig(tmp_path, kind, summary)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hook(_event: Any, _ctx: Any) -> None:
        entered.set()
        await release.wait()
        raise RuntimeError("a later failure")

    run.harness.hooks.on("session_compact", hook)
    task = _begin(run, kind)
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort()
    release.set()
    words = "session_compact hook handler raised: a later failure"
    if kind == "manual":
        with pytest.raises(AgentHarnessError, match=words):
            await asyncio.wait_for(task, WAIT)
    else:
        await asyncio.wait_for(task, WAIT)

    assert await _compactions(run) == 1
    prefix = {
        "manual": "",
        "threshold": "Auto-compaction failed: ",
        "overflow": "Context overflow recovery failed: ",
    }[kind]
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.aborted, e.result, e.error_message) for e in ends] == [(False, None, prefix + words)]


@pytest.mark.parametrize("kind", KINDS)
async def test_an_abort_while_the_write_waits_on_its_lock_does_not_stop_it(
    tmp_path: Path, kind: str
) -> None:
    """The last check is at the entry into ``append_compaction`` (ADR-0023's limits): the
    boundary is there, not at the bytes."""

    summary = _Summary(hold=False)
    run = await _rig(tmp_path, kind, summary)
    assert run.session is not None
    storage: Any = run.session._storage
    original = run.session.append_compaction
    entered = asyncio.Event()

    async def append(**kwargs: Any) -> Any:
        await storage._lock.acquire()
        entered.set()
        return await original(**kwargs)

    run.session.append_compaction = append  # type: ignore[method-assign]
    task = _begin(run, kind)
    await asyncio.wait_for(entered.wait(), WAIT)
    assert await _compactions(run) == 0
    await run.harness.abort()
    storage._lock.release()
    await asyncio.wait_for(task, WAIT)

    assert await _compactions(run) == 1
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.aborted, e.result is not None) for e in ends] == [(False, True)]


async def test_a_write_that_fails_after_an_abort_ends_as_aborted_and_raises_its_own_words(
    tmp_path: Path,
) -> None:
    """Nothing is on file, so the end's ``aborted`` is true. ``written`` is set after the
    call, not at its entry (ADR-0023's limit 9)."""

    summary = _Summary(hold=False)
    run = await _rig(tmp_path, "manual", summary)
    assert run.session is not None
    entered = asyncio.Event()
    release = asyncio.Event()

    async def append(**_kwargs: Any) -> Any:
        entered.set()
        await release.wait()
        raise OSError("disk full")

    run.session.append_compaction = append  # type: ignore[method-assign]
    task = _begin(run, "manual")
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort()
    release.set()
    with pytest.raises(OSError, match="disk full"):
        await asyncio.wait_for(task, WAIT)

    await _assert_nothing_written(run, "manual")


@pytest.mark.parametrize("kind", KINDS)
async def test_an_abort_while_compaction_start_is_emitted_runs_no_hook_and_no_request(
    tmp_path: Path, kind: str
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)
    called: list[bool] = []

    async def hook(_event: Any, _ctx: Any) -> None:
        called.append(True)

    async def start(event: Any) -> None:
        if getattr(event, "type", "") == "compaction_start":
            await run.harness.abort()

    run.harness.hooks.on("session_before_compact", hook)
    run.harness.subscribe(start)
    await _end(run, kind, _begin(run, kind))

    assert called == []
    assert summary.calls == 0
    await _assert_nothing_written(run, kind)


# === The hook runs on the compaction's own task =============================


async def test_the_hook_shares_its_callers_task_and_context_with_the_summary(tmp_path: Path) -> None:
    """A credential a hook sets reaches the summarizer, a token the caller made can be
    reset in the hook, and the task is the caller's, as before #347."""

    credential: contextvars.ContextVar[str] = contextvars.ContextVar("credential", default="none")
    token = credential.set("the caller's")
    seen: list[str] = []
    same_task: list[bool] = []
    outer = asyncio.current_task()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        seen.append(credential.get())
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _rig(tmp_path, "manual", summarise)

    async def hook(_event: Any, _ctx: Any) -> None:
        same_task.append(asyncio.current_task() is outer)
        credential.reset(token)
        credential.set("the hook's")

    run.harness.hooks.on("session_before_compact", hook)
    await run.harness.compact()  # not through ``wait_for``: on 3.11 that is a task of its own

    assert same_task == [True]
    assert seen == ["the hook's"]
    assert credential.get() == "the hook's"
    assert await _compactions(run) == 1


async def test_a_hook_that_awaits_abort_finishes_it_and_the_queued_lines_reach_their_receiver(
    tmp_path: Path,
) -> None:
    """In a task of its own this hook cancelled itself at the abort's first suspension: the
    later subscriber got no lines, the queues were already empty, and the abort's own hook
    never finished."""

    summary = _Summary()
    run = await _rig(tmp_path, "manual", summary)
    harness = run.harness
    await harness.steer("STEER")
    await harness.follow_up("FOLLOW")
    received: list[list[str]] = []
    finished: list[bool] = []

    async def slow_subscriber(event: Any) -> None:
        if getattr(event, "type", "") == "input_withdrawn":
            await asyncio.sleep(0)

    def receiver(event: Any) -> None:
        if getattr(event, "type", "") == "input_withdrawn":
            received.append([m.content[0].text for m in event.messages])

    async def abort_hook(_event: Any, _ctx: Any) -> None:
        await asyncio.sleep(0)
        finished.append(True)

    async def hook(_event: Any, _ctx: Any) -> None:
        await harness.abort(withdraw_unanswered=True)

    harness.subscribe(slow_subscriber)
    harness.subscribe(receiver)
    harness.hooks.on("abort", abort_hook)
    harness.hooks.on("session_before_compact", hook)
    with pytest.raises(AgentHarnessError, match="Compaction cancelled"):
        await asyncio.wait_for(harness.compact(), WAIT)

    assert received == [["STEER", "FOLLOW"]]
    assert finished == [True]
    assert summary.calls == 0
    assert await _compactions(run) == 0


# === Under a prompt's claim =================================================


async def test_a_nested_compaction_keeps_its_prompts_abort() -> None:
    """Only a public ``compact()`` starts afresh: under a claim the flag is the prompt's."""

    summary = _Summary(hold=False)
    harness = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), _summarizer_override=summary)
    )
    assert harness._session is not None
    for _ in range(6):
        await harness._session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    claim = harness._claim = object()
    harness._phase = "turn"
    harness._abort_requested = True
    with pytest.raises(AgentHarnessError, match="Compaction cancelled"):
        await harness.compact(reason="threshold", _claim=claim)
    assert summary.calls == 0
    assert harness._abort_requested


# === What follows ===========================================================


async def test_esc_in_the_overflow_summary_withdraws_the_input_once(tmp_path: Path) -> None:
    summary = _Summary()
    provider = _Provider("overflow")
    run = await _overflow_run(tmp_path, provider, summary)
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(task, WAIT)

    assert summary.cancelled == 1
    assert _said(run) == [(["CANCELLED"], True)]
    assert "CANCELLED" not in run.state()
    assert "CANCELLED" not in await run.disk()
    assert "<failed>" not in await run.disk()
    assert len(provider.seen) == 1, "no re-run after it"
    assert "CANCELLED" not in await run.next_request()


async def test_a_plain_abort_in_the_overflow_summary_keeps_the_input_and_the_failed_answer(
    tmp_path: Path,
) -> None:
    summary = _Summary()
    provider = _Provider("overflow")
    run = await _overflow_run(tmp_path, provider, summary)
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await asyncio.wait_for(task, WAIT)

    assert run.withdrawn == []
    assert run.state()[-2:] == ["KEPT", "<failed>"]
    assert (await run.disk())[-2:] == ["KEPT", "<failed>"]
    assert len(provider.seen) == 1


async def test_after_a_threshold_compaction_is_cut_the_next_turns_check_compacts_again(
    tmp_path: Path,
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, "threshold", summary)
    task = _begin(run, "threshold")
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await asyncio.wait_for(task, WAIT)
    assert await _compactions(run) == 0

    summary.hold = False
    run.provider.script.append("big_answer")
    await asyncio.wait_for(run.harness.prompt("NEXT"), WAIT)
    assert await _compactions(run) == 1
    assert run.provider.seen[-1][-1] == "NEXT"


# === A stale flag ===========================================================


@pytest.mark.parametrize("withdraw", [False, True], ids=["plain", "esc"])
async def test_an_abort_made_while_idle_does_not_cancel_the_next_compaction(
    tmp_path: Path, withdraw: bool
) -> None:
    summary = _Summary(hold=False)
    run = await _rig(tmp_path, "manual", summary)
    await run.harness.abort(withdraw_unanswered=withdraw)
    seen: list[bool] = []
    run.harness.subscribe(
        lambda e: seen.append(run.harness.withdraw_pending)
        if getattr(e, "type", "") == "compaction_start"
        else None
    )
    await asyncio.wait_for(run.harness.compact(), WAIT)

    assert seen == [False], "the idle Esc is not this compaction's"
    assert await _compactions(run) == 1


async def test_a_compaction_cut_by_an_abort_leaves_the_next_prompt_and_compaction_alone(
    tmp_path: Path,
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, "manual", summary)
    task = _begin(run, "manual")
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await _end(run, "manual", task)

    await asyncio.wait_for(run.harness.prompt("NEXT"), WAIT)
    assert run.state()[-2:] == ["NEXT", "answer"]

    summary.entered.clear()
    task = _begin(run, "manual")
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await _end(run, "manual", task)
    summary.hold = False
    await asyncio.wait_for(run.harness.compact(), WAIT)
    assert await _compactions(run) == 1


# === The other ends of an abort =============================================


@pytest.mark.parametrize("kind", KINDS)
async def test_dispose_during_a_summary_returns_promptly(tmp_path: Path, kind: str) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)
    task = _begin(run, kind)
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await asyncio.wait_for(run.harness.dispose(), WAIT)
    with contextlib.suppress(AgentHarnessError):
        await asyncio.wait_for(task, WAIT)

    assert summary.cancelled == 1
    assert await _compactions(run) == 0
    assert run.withdrawn == []
    if kind == "overflow":
        assert "THE PROMPT" in await run.disk()


async def test_abort_retry_does_not_cut_a_request_in_flight(tmp_path: Path) -> None:
    """``abort_retry`` is "stop retrying", as ever (RPC's, and the countdown's Esc)."""

    summary = _Summary()
    run = await _rig(tmp_path, "manual", summary)
    task = _begin(run, "manual")
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    run.harness.abort_retry()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done() and summary.cancelled == 0
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_ctx_abort_does_not_cut_a_request_but_writes_nothing(tmp_path: Path) -> None:
    """#545 owns a signal for ``ctx.abort()``. Until then it has no task to cancel, and
    it counts when the summary returns."""

    release = asyncio.Event()
    entered = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _rig(tmp_path, "manual", summarise)
    task = _begin(run, "manual")
    await asyncio.wait_for(entered.wait(), WAIT)
    run.harness._mark_abort()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done()
    release.set()
    await _end(run, "manual", task)
    await _assert_nothing_written(run, "manual")


# === A cancel of the caller is the caller's =================================


@pytest.mark.parametrize("kind", KINDS)
async def test_an_outer_cancel_propagates_as_a_cancel(tmp_path: Path, kind: str) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, kind, summary)
    task = _begin(run, kind)
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, WAIT)

    assert summary.cancelled == 1
    assert run.harness.phase == "idle"
    assert run.harness._summary_task is None
    assert await _compactions(run) == 0


@pytest.mark.parametrize("order", ["abort_first", "cancel_first"])
async def test_an_outer_cancel_landing_with_an_abort_still_propagates(
    tmp_path: Path, order: str
) -> None:
    summary = _Summary()
    run = await _rig(tmp_path, "manual", summary)
    task = _begin(run, "manual")
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    if order == "abort_first":
        await run.harness.abort()
        task.cancel()
    else:
        task.cancel()
        await run.harness.abort()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, WAIT)
    assert await _compactions(run) == 0
    assert run.harness.phase == "idle"


async def test_an_abort_after_an_earlier_swallowed_cancel_is_still_an_abort(tmp_path: Path) -> None:
    """The count is read against what it was on entry: a host that ate a cancel of its own
    task long ago, with no ``uncancel()``, would otherwise see this abort as an outer cancel."""

    summary = _Summary()
    run = await _rig(tmp_path, "manual", summary)
    outcome: list[str] = []
    armed = asyncio.Event()

    async def host() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            armed.set()
            await asyncio.Event().wait()
        current = asyncio.current_task()
        assert current is not None and current.cancelling() == 1
        try:
            await run.harness.compact()
        except AgentHarnessError as exc:
            outcome.append(str(exc))

    task = asyncio.create_task(host())
    await asyncio.wait_for(armed.wait(), WAIT)
    task.cancel()
    await asyncio.wait_for(summary.entered.wait(), WAIT)
    await run.harness.abort()
    await asyncio.wait_for(task, WAIT)
    assert outcome == ["Compaction cancelled"]


# === A real connection ======================================================


async def _socket_rig() -> tuple[_HoldingProvider, Any, AgentHarness]:
    """A real adapter, a real socket: the request it holds is the summary's."""

    provider = _HoldingProvider()
    server = await asyncio.start_server(provider.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    model = Model(
        api="held-completions",
        id="held-model",
        provider="openai",
        base_url=f"http://127.0.0.1:{port}/v1",
        context_window=200_000,
        max_tokens=4096,
    )

    async def auth(_model: Any) -> dict[str, Any]:
        return {"apiKey": "sk-not-a-real-key", "headers": {}}

    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    harness = AgentHarness(AgentHarnessOptions(model=model, session=session, get_api_key_and_headers=auth))
    harness.state.auto_retry_enabled = False
    return provider, server, harness


async def _close(provider: _HoldingProvider, server: Any) -> None:
    for writer in provider.writers:
        with contextlib.suppress(Exception):
            writer.close()
    server.close()
    await server.wait_closed()


@pytest.fixture
def _adapter() -> Any:
    """The real adapter under its API name: a summary reaches it through ``stream_simple``."""

    register_provider("held-completions", stream_openai_completions)
    yield
    unregister_provider("held-completions")


async def test_an_abort_drops_the_summary_requests_connection(_adapter: Any) -> None:
    provider, server, harness = await _socket_rig()
    task = asyncio.create_task(harness.compact())
    try:
        await asyncio.wait_for(provider.streaming.wait(), 20)
        assert not provider.peer_gone.is_set()
        await harness.abort()
        await asyncio.wait_for(provider.peer_gone.wait(), WAIT)
        with pytest.raises(AgentHarnessError, match="Compaction cancelled"):
            await asyncio.wait_for(task, WAIT)
        assert provider.requests == 1
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        await _close(provider, server)


async def test_a_held_summary_request_stays_open_without_an_abort(_adapter: Any) -> None:
    """The positive control: an instrument that cannot say "still open" proves nothing."""

    provider, server, harness = await _socket_rig()
    task = asyncio.create_task(harness.compact())
    try:
        await asyncio.wait_for(provider.streaming.wait(), 20)
        await asyncio.sleep(0.2)
        assert not provider.peer_gone.is_set()
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        await _close(provider, server)


# === A branch summary =======================================================


async def _branched(tmp_path: Path, summarise: Any) -> tuple[_Run, str | None]:
    run = await _start(tmp_path, _Provider(), True, _branch_summarizer_override=summarise)
    assert run.session is not None
    root = await run.session.append_message(UserMessage(content=[TextContent(text="root")]))
    await run.session.append_message(UserMessage(content=[TextContent(text="branch")]))
    return run, root


async def test_an_abort_stops_a_branch_summary_and_the_leaf_stays(tmp_path: Path) -> None:
    entered = asyncio.Event()
    cancelled: list[bool] = []

    async def summarise(*_args: Any, **_kwargs: Any) -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "unreachable"

    run, root = await _branched(tmp_path, summarise)
    assert run.session is not None
    leaf = await run.session.get_leaf_id()
    task = asyncio.create_task(
        run.harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    )
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort()
    result = await asyncio.wait_for(task, WAIT)

    assert result.cancelled is True
    assert cancelled == [True]
    assert await run.session.get_leaf_id() == leaf
    assert run.harness.phase == "idle" and run.harness._summary_task is None


@pytest.mark.parametrize("swallow", [False, True], ids=["returned", "swallowed"])
async def test_a_branch_summary_already_back_moves_nothing(tmp_path: Path, swallow: bool) -> None:
    """``returned``: it returns on its own, and the abort lands before the caller wakes.
    ``swallowed``: a summarizer that eats the cancel, and returns. The check before the
    write, as :meth:`compact` makes."""

    entered = asyncio.Event()

    async def summarise(*_args: Any, **_kwargs: Any) -> str:
        entered.set()
        if swallow:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.Event().wait()
        else:
            asyncio.ensure_future(run.harness.abort())
        return "SUMMARY"

    run, root = await _branched(tmp_path, summarise)
    assert run.session is not None
    leaf = await run.session.get_leaf_id()
    task = asyncio.create_task(
        run.harness.navigate_tree(root, NavigateTreeOptions(summarize=True))
    )
    await asyncio.wait_for(entered.wait(), WAIT)
    if swallow:
        await run.harness.abort()
    result = await asyncio.wait_for(task, WAIT)

    assert result.cancelled is True
    assert result.summary_entry is None
    assert await run.session.get_leaf_id() == leaf
    assert [e for e in await run.session.get_entries() if e.type == "branch_summary"] == []
    assert run.harness.phase == "idle"


async def test_an_abort_in_the_tree_hook_sends_no_branch_summary(tmp_path: Path) -> None:
    calls: list[bool] = []

    async def summarise(*_args: Any, **_kwargs: Any) -> str:
        calls.append(True)
        return "unreachable"

    run, root = await _branched(tmp_path, summarise)
    assert run.session is not None
    leaf = await run.session.get_leaf_id()

    async def hook(_event: Any, _ctx: Any) -> None:
        run.harness._mark_abort()

    run.harness.hooks.on("session_before_tree", hook)
    result = await run.harness.navigate_tree(root, NavigateTreeOptions(summarize=True))

    assert result.cancelled is True
    assert calls == []
    assert await run.session.get_leaf_id() == leaf


async def test_an_abort_left_by_a_finished_prompt_does_not_stop_a_later_navigation(
    tmp_path: Path,
) -> None:
    async def summarise(*_args: Any, **_kwargs: Any) -> str:
        return "SUMMARY"

    run, root = await _branched(tmp_path, summarise)
    run.harness._abort_requested = run.harness._withdraw_requested = True
    seen: list[bool] = []

    async def hook(_event: Any, _ctx: Any) -> None:
        seen.append(run.harness.withdraw_pending)

    run.harness.hooks.on("session_before_tree", hook)
    result = await run.harness.navigate_tree(root, NavigateTreeOptions(summarize=True))

    assert result.cancelled is False
    assert result.summary_entry is not None
    assert seen == [False]


async def test_a_failure_with_no_abort_is_still_a_failure(tmp_path: Path) -> None:
    """The raise after an abort reads as cancelled; one with no abort keeps its words."""

    async def summarise(_model: Any, _prep: CompactionPreparation, _ci: Any) -> CompactResult:
        raise AgentHarnessError("compaction", "the model said no")

    run = await _rig(tmp_path, "manual", summarise)
    with pytest.raises(AgentHarnessError, match="the model said no"):
        await asyncio.wait_for(run.harness.compact(), WAIT)
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.aborted, e.error_message) for e in ends] == [(False, "the model said no")]


@pytest.mark.parametrize("kind", KINDS)
async def test_an_adapter_that_turns_the_cancel_into_a_raise_still_reads_as_cancelled(
    tmp_path: Path, kind: str
) -> None:
    """ADR-0286 limit 9's adapter, on a summary: the abort is what ended it."""

    entered = asyncio.Event()

    async def summarise(_model: Any, _prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("the adapter turned the cancel into this") from None
        raise AssertionError("cancelled")

    run = await _rig(tmp_path, kind, summarise)
    task = _begin(run, kind)
    await asyncio.wait_for(entered.wait(), WAIT)
    await run.harness.abort()
    await _end(run, kind, task)
    await _assert_nothing_written(run, kind)


# === The split turn's two summaries, and a sequence that was retrying ========


@pytest.mark.parametrize("kind", KINDS)
async def test_an_abort_in_a_summarys_retry_wait_ends_it_at_once(kind: str) -> None:
    """A minute's wait, on each path: the row of the table that was there before #347."""

    from tests.test_summary_retry_409 import (
        _MINUTE,
        _SMALL_WINDOW,
        _compactable,
        _retry_events,
        _TurnAndSummary,
    )
    from tests.test_summary_retry_409 import _compactions as _live_compactions
    from tests.test_summary_retry_409 import _rig as _retrying

    clear_providers()
    try:
        provider = _TurnAndSummary({"manual": [], "threshold": ["big"], "overflow": ["overflow"]}[kind], ["502"])
        session = await _compactable()
        harness, events = _retrying(session, settings=_MINUTE, model=_SMALL_WINDOW)

        def on_retry(event: Any) -> None:
            if isinstance(event, AutoRetryStartEvent):
                asyncio.get_running_loop().create_task(harness.abort())

        harness.subscribe(on_retry)
        if kind == "manual":
            with pytest.raises(AgentHarnessError, match="Compaction cancelled"):
                await asyncio.wait_for(harness.compact(), WAIT)
        else:
            await asyncio.wait_for(harness.prompt("go"), WAIT)

        assert provider.calls["summary"] == 1
        assert _retry_events(events)[-1] == ("end", False, 1, "Retry cancelled")
        ends = [e for e in events if getattr(e, "type", "") == "compaction_end"]
        assert [(e.reason, e.aborted, e.error_message) for e in ends] == [(kind, True, None)]
        assert await _live_compactions(session) == 0
        assert harness.phase == "idle" and harness._retry_abort_event is None
    finally:
        clear_providers()


async def test_an_abort_of_a_retried_request_closes_its_sequence_as_cancelled() -> None:
    """A re-sent summary request cut by the abort reads ``Retry cancelled``, as an aborted
    wait does: the TUI draws this text as ``✖ Retry failed: …``, and ``CancelledError`` is
    not a thing to show anyone."""

    from tests.test_summary_retry_409 import _compactable, _retry_events
    from tests.test_summary_retry_409 import _Provider as _Registered
    from tests.test_summary_retry_409 import _rig as _retrying

    clear_providers()
    try:
        held = asyncio.Event()

        async def hold() -> None:
            held.set()
            await asyncio.Event().wait()

        _Registered("502", hold)
        harness, events = _retrying(await _compactable())
        task = asyncio.create_task(harness.compact())
        await asyncio.wait_for(held.wait(), WAIT)
        await harness.abort()
        with pytest.raises(AgentHarnessError, match="Compaction cancelled"):
            await asyncio.wait_for(task, WAIT)
        assert _retry_events(events)[-1] == ("end", False, 1, "Retry cancelled")
        assert harness._retry_abort_event is None
    finally:
        clear_providers()
