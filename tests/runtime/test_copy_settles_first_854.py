"""#854 (ADR-0307 D3, K3): a copy of a session taken during a delegation settles the turn first.

``AgentSessionRuntime.fork`` copies the branch (``repo.fork``) and then tears the outgoing
harness down, and since #800 the teardown seals and settles its turn, so the cancelled
delegation's report is the ``agent`` call's result in the outgoing file (D1). The copy was
taken before that settle: on ``0436193c`` a clone (``position="at"`` at the leaf, RPC
classic ``clone``) held the user message and the assistant message with the ``agent``
call and no result, and the report was only in the outgoing file (the issue's probe).

Now the fork asks ``session_before_fork`` first (a veto touches nothing, ADR-0307 D3),
then seals and settles the turn, then copies: a copy at the leaf the fork was asked for,
which the settle extended, is taken at the leaf the settle left, so it holds the call's
result. The lead's call (2026-11-22): veto first, then settle, then copy.

Every path that copies the current session mid-turn reaches ``AgentSessionRuntime.fork``:
RPC classic ``clone`` and ``fork``, an extension command's ``ctx.fork`` (the TUI runs an
extension's command mid-turn, ADR-0292), and RPC ``sessions/branch`` and the TUI's
``/fork`` and ``/clone``, which refuse while a turn runs. Each test fails on ``0436193c``
unless it says it is a guard.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    TurnSeal,
)
from aelix_agent_core.harness.hooks import SessionBeforeForkResult
from aelix_agent_core.runtime import agent_session_runtime as asr
from aelix_agent_core.runtime.agent_session_runtime import create_agent_session_runtime
from aelix_agent_core.session import LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_coding_agent.extensions.command_context import ExtensionCommandContext

from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_stopped_child_reports_604 import _message_text
from tests.runtime.test_swap_settles_first_800 import (
    _delegating_host,
    _finish,
    _reopened,
    _reports,
    _StoppableChannel,
)


async def _target(host: Any, position: str) -> str:
    """Clone's target is the leaf (RPC ``_handle_clone`` reads it); fork's the user message."""

    session = host.session
    if position == "at":
        leaf = await session.get_leaf_id()
        assert leaf is not None
        return leaf
    first = next(
        entry
        for entry in await session.get_branch()
        if entry.type == "message" and entry.message.role == "user"
    )
    return first.id


def _roles(messages: list[Any]) -> list[str]:
    return [m.role for m in messages]


async def test_a_clone_during_a_delegation_holds_the_calls_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's probe, ``at``: the copy is a new file holding the user message, the
    ``agent`` call and its result, the report; the outgoing file holds it too, and the
    old harness asked its model once. On ``0436193c``: copy roles ``user, assistant`` and
    no report in the copy."""

    channel = _StoppableChannel()
    host, _harness, seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        assert host.harness.phase == "turn"
        result = await asyncio.wait_for(host.fork(await _target(host, "at"), position="at"), 20)
        await asyncio.wait_for(prompt, 20)
        assert result.cancelled is False
        copy_path = Path(host.session.session_file)
        assert copy_path != path
        copy = await _reopened(copy_path)
        assert _roles(copy) == ["user", "assistant", "toolResult"]
        assert _reports(copy) == ["toolResult"]
        assert _reports(await _reopened(path)) == ["toolResult"]
        assert len(seen) == 1, "the old harness asked its model again"
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_an_extension_commands_ctx_fork_at_the_leaf_holds_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ctx.fork(leaf, {"position": "at"})`` from an extension's command, which the TUI
    runs mid-turn, is the same fork. On ``0436193c``: no report in the copy."""

    channel = _StoppableChannel()
    host, harness, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        ctx = ExtensionCommandContext(
            harness.runtime,
            harness=harness,
            session_runtime=host,
            cwd=str(tmp_path),
            model=None,
            is_idle=lambda: False,
            abort=lambda: None,
            get_active_tools=lambda: [],
            get_system_prompt=lambda: "",
        )
        leaf = await _target(host, "at")
        await asyncio.wait_for(ctx.fork(leaf, {"position": "at"}), 20)
        await asyncio.wait_for(prompt, 20)
        copy_path = Path(host.session.session_file)
        assert copy_path != path
        assert _reports(await _reopened(copy_path)) == ["toolResult"]
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_fork_before_the_delegating_prompt_copies_nothing_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard (passes on ``0436193c``), the issue's probe ``before``: the copy ends before
    the user message, so it holds neither the call nor its result, and the outgoing file
    holds the report; the user message's text comes back as the fork's."""

    channel = _StoppableChannel()
    host, _harness, seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        result = await asyncio.wait_for(
            host.fork(await _target(host, "before"), position="before"), 20
        )
        await asyncio.wait_for(prompt, 20)
        assert result.selected_text == "delegate"
        copy_path = Path(host.session.session_file)
        assert copy_path != path
        assert _roles(await _reopened(copy_path)) == []
        assert _reports(await _reopened(path)) == ["toolResult"]
        assert len(seen) == 1
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_clone_at_an_earlier_entry_is_not_moved_to_the_new_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard (passes on ``0436193c``): only the leaf the fork was asked for follows the
    settle; a copy ``at`` an earlier entry (the user message) ends there."""

    channel = _StoppableChannel()
    host, _harness, _seen, _events, _path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        await asyncio.wait_for(host.fork(await _target(host, "before"), position="at"), 20)
        await asyncio.wait_for(prompt, 20)
        assert _roles(await _reopened(Path(host.session.session_file))) == ["user"]
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


@pytest.mark.parametrize("position", ["at", "before"])
async def test_a_vetoed_fork_during_a_delegation_leaves_the_turn_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    """Guard (passes on ``0436193c``; kills a settle moved ahead of the veto): a fork that
    ``session_before_fork`` cancels seals nothing, aborts nothing and copies nothing; the
    delegation goes on and the harness stays installed."""

    channel = _StoppableChannel()
    host, harness, _seen, _events, path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    vetoed: list[Any] = []

    async def veto(event: Any, _ctx: Any) -> SessionBeforeForkResult:
        vetoed.append(event.position)
        return SessionBeforeForkResult(cancel=True)

    harness.hooks.on("session_before_fork", veto)
    sessions = sorted(p.name for p in path.parent.glob("*.jsonl"))
    try:
        result = await asyncio.wait_for(
            host.fork(await _target(host, position), position=position), 10
        )
        assert result.cancelled is True and vetoed == [position]
        await asyncio.sleep(0.1)
        assert not prompt.done() and harness.phase == "turn"
        assert harness.turns_held is None
        assert host.harness is harness
        assert sorted(p.name for p in path.parent.glob("*.jsonl")) == sessions
        assert not channel.stopped.is_set()
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_an_invalid_fork_during_a_delegation_leaves_the_turn_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard (passes on ``0436193c``): a ``before`` fork of an entry that is not a user
    message is refused before the settle, so the refusal aborts nothing."""

    channel = _StoppableChannel()
    host, harness, _seen, _events, _path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )
    try:
        with pytest.raises(ValueError, match="Invalid entry ID for forking"):
            await asyncio.wait_for(host.fork(await _target(host, "at"), position="before"), 10)
        await asyncio.sleep(0.1)
        assert not prompt.done() and harness.phase == "turn"
        assert harness.turns_held is None and host.harness is harness
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def test_a_copy_that_fails_after_the_settle_puts_the_gate_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy that raises once the turn has settled leaves the harness installed and
    taking prompts: the seal the fork put up is lifted (``_abandon``). On ``0436193c`` the
    copy came before any seal, so this case did not exist; under a fix that forgets the
    seal on this path the harness refuses "the session is being replaced"."""

    channel = _StoppableChannel()
    host, harness, _seen, _events, _path, prompt = await _delegating_host(
        tmp_path, channel, monkeypatch
    )

    async def broken_fork(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk full")

    try:
        target = await _target(host, "at")
        monkeypatch.setattr(host._repo, "fork", broken_fork)
        with pytest.raises(OSError, match="disk full"):
            await asyncio.wait_for(host.fork(target, position="at"), 20)
        await asyncio.wait_for(prompt, 20)
        assert host.harness is harness and not harness.disposed
        assert harness.turns_held is None
        assert all(s.done() for s in asr._SETTLES)
    finally:
        channel.stopped.set()
        await _finish(prompt)
        await host.dispose()


async def _turning_host(
    tmp_path: Path,
) -> tuple[Any, AgentHarness, dict[str, asyncio.Event], asyncio.Future[Any]]:
    """A runtime host whose harness is in a turn: its model parks until ``finish``."""

    session, repo, _path = await _parent(tmp_path)
    ev = {"called": asyncio.Event(), "finish": asyncio.Event(), "shutdown": asyncio.Event()}

    async def model(*_args: Any) -> Any:
        ev["called"].set()
        await ev["finish"].wait()
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="completed")], stop_reason="end_turn"
            )
        )

    model_id = Model(id="mock", provider="mock")
    harness = AgentHarness(AgentHarnessOptions(model=model_id, session=session, stream_fn=model))

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=model_id, session=new_session))

    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    turn = asyncio.ensure_future(harness.prompt("first"))
    await asyncio.wait_for(ev["called"].wait(), 10)
    return host, harness, ev, turn


async def _takes_prompts(host: Any, harness: AgentHarness, ev: dict[str, asyncio.Event]) -> None:
    assert host.harness is harness and not harness.disposed
    assert harness.turns_held is None
    ev["finish"].set()
    await asyncio.wait_for(harness.prompt("second"), 10)
    assert _message_text(harness.state.messages[-1]) == "completed"


async def test_a_fork_cancelled_in_a_session_shutdown_handler_lifts_its_own_seal(
    tmp_path: Path,
) -> None:
    """The teardown takes the seal the fork put up before its copy, so a fork cancelled in
    an awaited ``session_shutdown`` handler undoes it (``_abandon``) and the harness left
    installed takes the next prompt. Kills a fork that keeps its seal from its teardown
    (the harness then refuses "the session is being replaced"); on ``0436193c`` the fork
    sealed only in its teardown, so this passes there too."""

    host, harness, ev, turn = await _turning_host(tmp_path)

    async def park(event: Any, _ctx: Any) -> None:
        if event.reason == "fork":
            ev["shutdown"].set()
            await asyncio.Event().wait()

    harness.hooks.on("session_shutdown", park)
    op: asyncio.Future[Any] | None = None
    try:
        op = asyncio.ensure_future(host.fork(await host.session.get_leaf_id(), position="at"))
        await asyncio.wait_for(ev["shutdown"].wait(), 10)
        op.cancel()
        await asyncio.gather(op, return_exceptions=True)
        await asyncio.wait_for(turn, 10)
        await _takes_prompts(host, harness, ev)
    finally:
        ev["finish"].set()
        await _finish(turn, *([op] if op is not None else []))
        await host.dispose()


async def test_a_fork_that_fails_before_its_teardown_lifts_its_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fork whose replacement raises after it handed its seal on and before its teardown
    took it (the give-back at the start of ``_finish_session_replacement``) puts the gate
    back: the harness left installed takes the next prompt. Kills a fork that leaves that
    seal up. On ``0436193c`` there is no hand-off, nothing raises, and the test fails at
    ``pytest.raises``."""

    host, harness, ev, turn = await _turning_host(tmp_path)
    given_back = host._give_back_queued

    async def raising_while_handed() -> None:
        if getattr(host, "_copy_seal", None) is not None:
            raise RuntimeError("give-back failed")
        await given_back()

    monkeypatch.setattr(host, "_give_back_queued", raising_while_handed)
    try:
        with pytest.raises(RuntimeError, match="give-back failed"):
            await asyncio.wait_for(host.fork(await host.session.get_leaf_id(), position="at"), 10)
        await asyncio.wait_for(turn, 10)
        await _takes_prompts(host, harness, ev)
    finally:
        ev["finish"].set()
        await _finish(turn)
        await host.dispose()


async def test_a_fork_past_the_settle_bound_does_not_settle_again_in_its_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn that ignores the settle's cancels (its model swallows them) is still running
    when the fork's 0.3 s bound passes, and the fork copies. Its teardown takes the fork's
    seal and live settle instead of sealing and settling again: ``session_shutdown`` sees
    one settle alive, not two, and the gate is that seal. Kills a teardown that does not
    take the handed seal; on ``0436193c`` the teardown's settle was the fork's only one,
    so this passes there too."""

    from aelix_agent_core.harness import core as harness_core

    monkeypatch.setattr(harness_core, "_REABORT_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(asr, "SWAP_SETTLE_SECONDS", 0.3)
    session, repo, _path = await _parent(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def stubborn(*_args: Any) -> Any:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="late")], stop_reason="end_turn")
        )

    model_id = Model(id="mock", provider="mock")
    harness = AgentHarness(AgentHarnessOptions(model=model_id, session=session, stream_fn=stubborn))
    seen: dict[str, Any] = {}

    async def at_shutdown(event: Any, _ctx: Any) -> None:
        if event.reason == "fork":
            seen["settles"] = len(asr._SETTLES)
            seen["gate"] = harness.turns_held
            seen["phase"] = harness.phase
            release.set()

    harness.hooks.on("session_shutdown", at_shutdown)

    async def fresh(new_session: Any, **_kwargs: Any) -> AgentHarness:
        return AgentHarness(AgentHarnessOptions(model=model_id, session=new_session))

    # Codex review C8: each step 0 wait on a settle (its bound) is recorded, so a teardown
    # that waits on the handed settle a second time is seen (0.3 s twice) and killed.
    bounds: list[float | None] = []
    real_wait = asyncio.wait

    class _Asyncio:
        def __getattr__(self, name: str) -> Any:
            return getattr(asyncio, name)

        async def wait(self, fs: Any, *, timeout: float | None = None, **kw: Any) -> Any:
            here = inspect.currentframe()
            caller = here.f_back if here is not None else None
            if caller is not None and caller.f_code.co_name == "_seal_and_settle":
                bounds.append(timeout)
            return await real_wait(fs, timeout=timeout, **kw)

    monkeypatch.setattr(asr, "asyncio", _Asyncio())
    host = await create_agent_session_runtime(harness, fresh, repo=repo, fs=LocalFileSystem())
    first = asyncio.ensure_future(harness.prompt("first"))
    try:
        await asyncio.wait_for(started.wait(), 10)
        leaf = await host.session.get_leaf_id()
        await asyncio.wait_for(host.fork(leaf, position="at"), 20)
        assert seen["phase"] == "turn", "the turn settled within the bound; nothing was measured"
        assert seen["settles"] == 1
        assert isinstance(seen["gate"], TurnSeal)
        assert host.harness is not harness
        assert bounds == [0.3], "the settle's bound was not waited exactly once"
    finally:
        release.set()
        await _finish(first)
        await asyncio.wait_for(asyncio.gather(*asr._SETTLES, return_exceptions=True), 10)
        await host.dispose()


def _withdrawn_lines(harness: AgentHarness) -> list[tuple[list[str], bool]]:
    """What ``input_withdrawn`` gives back on ``harness``, recorded as it is emitted."""

    seen: list[tuple[list[str], bool]] = []

    def on(event: Any) -> None:
        if event.type == "input_withdrawn":
            seen.append(([_message_text(m) for m in event.messages], event.prompt_withdrawn))

    harness.subscribe(on)
    return seen


@pytest.mark.parametrize("position", ["at", "before"])
async def test_a_fork_during_a_turn_gives_the_queued_lines_back(
    tmp_path: Path, position: str
) -> None:
    """Verify F1 and Codex C1 of fix round 1: the steer and follow-up lines queued on the
    outgoing turn come back on ``input_withdrawn`` (``prompt_withdrawn`` false), as a swap
    gives them back (#681), before the fork's settle aborts that turn and clears the
    queues. In round 1: no ``input_withdrawn`` and the lines were in neither file.
    ``0436193c`` gave them back (inside ``_finish_session_replacement``)."""

    host, harness, ev, turn = await _turning_host(tmp_path)
    withdrawn = _withdrawn_lines(harness)
    try:
        await harness.steer("STEER-LINE")
        await harness.follow_up("FOLLOW-LINE")
        target = await _target(host, position)
        await asyncio.wait_for(host.fork(target, position=position), 20)
        await asyncio.wait_for(asyncio.gather(turn, return_exceptions=True), 10)
        assert withdrawn == [(["STEER-LINE", "FOLLOW-LINE"], False)]
        assert host.harness is not harness
    finally:
        ev["finish"].set()
        await _finish(turn)
        await host.dispose()


async def test_a_fork_whose_copy_fails_during_a_turn_has_given_the_queued_lines_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C1's failed copy: the lines are given back before the settle, so a copy that
    then raises has given them back too, and the harness left installed takes prompts. In
    round 1 the settle had cleared them and nothing gave them back."""

    host, harness, ev, turn = await _turning_host(tmp_path)
    withdrawn = _withdrawn_lines(harness)

    async def broken_fork(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("copy failed")

    try:
        await harness.steer("STEER-LINE")
        await harness.follow_up("FOLLOW-LINE")
        target = await _target(host, "at")
        monkeypatch.setattr(host._repo, "fork", broken_fork)
        with pytest.raises(OSError, match="copy failed"):
            await asyncio.wait_for(host.fork(target, position="at"), 20)
        await asyncio.wait_for(asyncio.gather(turn, return_exceptions=True), 10)
        assert withdrawn == [(["STEER-LINE", "FOLLOW-LINE"], False)]
        assert harness.pending_message_count == 0
        await _takes_prompts(host, harness, ev)
    finally:
        ev["finish"].set()
        await _finish(turn)
        await host.dispose()


async def test_a_quit_while_a_fork_waits_before_its_teardown_leaves_the_forks_seal_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex C3. The fork has handed its seal on and waits before its teardown (here in the
    give-back at the start of ``_finish_session_replacement``); a ``dispose()`` (a quit,
    which runs outside the transition lock) comes and is cancelled in an awaited
    ``session_shutdown`` handler. The fork's seal stays up, so the outgoing harness refuses
    a prompt while the fork runs, and the fork then completes. In round 1 the quit
    took the fork's seal and its interruption lifted it: the gate was ``None`` and the
    prompt ran on the harness the fork was replacing."""

    host, harness, ev, turn = await _turning_host(tmp_path)
    handed, release_fork, quit_in = asyncio.Event(), asyncio.Event(), asyncio.Event()
    given_back = host._give_back_queued

    async def parks_while_handed() -> None:
        if getattr(host, "_copy_seal", None) is not None:
            handed.set()
            await release_fork.wait()
        await given_back()

    async def parks_the_quit(event: Any, _ctx: Any) -> None:
        if event.reason == "quit" and not quit_in.is_set():  # the first quit only
            quit_in.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(host, "_give_back_queued", parks_while_handed)
    harness.hooks.on("session_shutdown", parks_the_quit)
    fork: asyncio.Future[Any] | None = None
    quit_: asyncio.Future[Any] | None = None
    try:
        fork = asyncio.ensure_future(host.fork(await host.session.get_leaf_id(), position="at"))
        await asyncio.wait_for(handed.wait(), 10)
        seal = harness.turns_held
        assert isinstance(seal, TurnSeal)
        quit_ = asyncio.ensure_future(host.dispose())
        await asyncio.wait_for(quit_in.wait(), 10)
        quit_.cancel()
        await asyncio.gather(quit_, return_exceptions=True)
        assert harness.turns_held is seal, "the quit's interruption lifted the fork's seal"
        with pytest.raises(AgentHarnessError) as refused:
            await asyncio.wait_for(harness.prompt("refused"), 5)
        assert refused.value.code == "invalid_state"
        release_fork.set()
        result = await asyncio.wait_for(fork, 20)
        assert result.cancelled is False and host.harness is not harness
    finally:
        release_fork.set()
        ev["finish"].set()
        await _finish(turn, *[t for t in (fork, quit_) if t is not None])
        await host.dispose()
