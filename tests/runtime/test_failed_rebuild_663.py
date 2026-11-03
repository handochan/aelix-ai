"""#663 (ADR-0289, owner decision b) — a rebuild that fails after the teardown.

``AgentSessionRuntime.reload`` tears the old harness down first (pi's order) and
its ``HookBus.dispose`` clears every handler, Guardrail's and Permission's
``tool_call`` among them. When re-reading the settings or building the new
harness then raised, the torn-down harness stayed installed and tool calls ran
ungated. ``/new``, ``/resume``, ``/fork`` and ``/import`` share the shape
(``_finish_session_replacement``).

Now the runtime builds the session again on the settings it was running with
(``kept``), or — when that build fails too — stops: the torn-down harness holds
its turns and blocks every tool call until a build succeeds (``stopped``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.runtime import (
    STOPPED_MESSAGE,
    AgentSessionRuntime,
    RebuildFailedError,
    ReloadSeed,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.settings import SettingsManager
from aelix_ai.settings.storage import InMemorySettingsStorage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult

MODEL = Model(id="fixture", provider="fixture")


class _Rig:
    """A persisted session behind a real runtime whose factory fails on demand."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.builds: list[tuple[str | None, ReloadSeed | None, str | None]] = []
        self.fail: Callable[[Session, ReloadSeed | None], BaseException | None] = (
            lambda _s, _seed: None
        )
        self.requests = 0
        self.ran: list[str] = []
        storage = InMemorySettingsStorage()
        storage.with_lock("global", lambda _c: '{"theme": "before"}')
        self.storage = storage
        self.settings = SettingsManager.from_storage(storage)
        self.runtime: AgentSessionRuntime
        self.session: Session
        self.other: Session

    async def _stream(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.requests += 1
        last = context.messages[-1] if context.messages else None
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if getattr(last, "role", None) == "user":
            message = AssistantMessage(
                content=[ToolCallContent(tool_call_id=f"t{self.requests}", tool_name="run", input={})],
                stop_reason="tool_use",
            )
        else:
            message = AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        yield AssistantEndEvent(message=message)

    def _tool(self) -> AgentTool:
        async def execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
            self.ran.append("run")
            return ToolResult(content=[TextContent(text="ran")])

        return AgentTool(
            name="run",
            description="run",
            parameters={"type": "object", "properties": {}},
            execute=execute,
        )

    def harness(self, session: Session) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL,
                stream_fn=self._stream,
                session=session,
                cwd=str(self.tmp),
                tools=[self._tool()],
                settings_manager=self.settings,
            )
        )

    async def factory(self, session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        self.builds.append((session.session_file, reload_seed, self.settings.get_theme()))
        failure = self.fail(session, reload_seed)
        if failure is not None:
            raise failure
        return self.harness(session)

    @classmethod
    async def create(cls, tmp: Path) -> _Rig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        other = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await other.append_message(UserMessage(content=[TextContent(text="elsewhere")]))
        rig.other = other
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await session.append_message(UserMessage(content=[TextContent(text="hello")]))
        rig.session = session
        rig.runtime = AgentSessionRuntime(rig.harness(session), rig.factory, repo=repo, fs=fs)
        return rig


def _always(exc: BaseException) -> Callable[[Session, ReloadSeed | None], BaseException | None]:
    return lambda _s, _seed: exc


def _once(exc: BaseException) -> Callable[[Session, ReloadSeed | None], BaseException | None]:
    """Fail the first build; the build that puts the session back works."""

    return lambda _s, seed: None if seed is not None and seed.restore else exc


# --- reload ---------------------------------------------------------------------------


async def test_a_settings_failure_rebuilds_the_session_on_its_previous_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness

    async def _raise() -> None:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(rig.settings, "reload", _raise)
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    err = caught.value
    assert (err.state, err.phase, err.operation) == ("kept", "settings", "reload")
    assert isinstance(err.cause, RecursionError)
    assert str(err) == "RecursionError: maximum recursion depth exceeded — kept the previous settings"
    assert rig.runtime.reload_phase == "settings"
    assert old.disposed
    restored = rig.runtime.harness
    assert restored is not old and not restored.disposed
    assert rig.runtime.stopped is None
    [(_file, seed, _theme)] = rig.builds
    assert seed is not None and seed.restore is True


async def test_a_build_failure_puts_back_the_settings_the_session_ran_with(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    rig.storage.with_lock("global", lambda _c: '{"theme": "after"}')
    rig.fail = _once(PermissionError(13, "Permission denied", "zlocked/inner"))

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    assert (caught.value.state, caught.value.phase) == ("kept", "build")
    # The failed build read the new file; the one after it ran on the old settings,
    # and they are what the session keeps (the file is not touched).
    assert [(seed.restore, theme) for _f, seed, theme in rig.builds if seed] == [
        (False, "after"),
        (True, "before"),
    ]
    assert rig.settings.get_theme() == "before"
    assert rig.runtime.stopped is None


async def test_the_restored_harness_runs_tools_through_its_own_hooks(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    rig.fail = _once(RuntimeError("build boom"))
    with pytest.raises(RebuildFailedError):
        await rig.runtime.reload()
    harness = rig.runtime.harness
    # Its transcript is the session's, as the swap would have rebuilt it.
    assert [m.role for m in harness.messages] == ["user"]
    await harness.prompt("again")
    assert rig.ran == ["run"]


async def test_when_the_restore_fails_too_the_runtime_stops(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    rig.fail = _always(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    err = caught.value
    assert err.state == "stopped"
    assert isinstance(err.restore_error, RuntimeError)
    # The restore raised what the build did: said once.
    assert str(err) == (
        "RuntimeError: build boom; rebuilding with the previous settings failed too — "
        + STOPPED_MESSAGE
    )
    assert rig.runtime.stopped == STOPPED_MESSAGE
    assert rig.runtime.harness is old and old.disposed
    assert old.turns_held == STOPPED_MESSAGE
    requests = rig.requests
    with pytest.raises(AgentHarnessError) as refused:
        await old.prompt("run it")
    assert refused.value.code == "invalid_state"
    assert STOPPED_MESSAGE in str(refused.value)
    assert rig.requests == requests and rig.ran == []

    # Recovery: the cause is fixed and the next reload succeeds.
    rig.fail = lambda _s, _seed: None
    await rig.runtime.reload()
    assert rig.runtime.stopped is None
    assert rig.runtime.harness is not old
    await rig.runtime.harness.prompt("run it")
    assert rig.ran == ["run"]


async def test_a_stopped_runtime_comes_back_through_new_session(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    rig.fail = _always(RuntimeError("build boom"))
    with pytest.raises(RebuildFailedError):
        await rig.runtime.reload()
    assert rig.runtime.stopped is not None
    rig.fail = lambda _s, _seed: None
    await rig.runtime.new_session()
    assert rig.runtime.stopped is None
    assert rig.runtime.harness.turns_held is None


async def test_a_cancelled_rebuild_leaves_the_runtime_stopped(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    rig.fail = _always(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await rig.runtime.reload()
    assert rig.runtime.stopped == STOPPED_MESSAGE
    assert old.turns_held == STOPPED_MESSAGE
    # Nothing was attempted after the cancel: one build, no restore.
    assert len(rig.builds) == 1


async def test_a_disposed_harness_blocks_every_tool_call(tmp_path: Path) -> None:
    """Below the turn gate: a turn that reaches a disposed harness runs no tool."""

    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    await harness.dispose()
    await harness.prompt("run it")
    assert rig.ran == []
    results = [m for m in harness.messages if getattr(m, "role", None) == "toolResult"]
    assert results and all(getattr(r, "is_error", False) for r in results)
    assert "AgentHarness has been disposed" in "".join(
        getattr(c, "text", "") for r in results for c in r.content
    )


# --- the session swaps ----------------------------------------------------------------


async def _swap(rig: _Rig, how: str) -> Any:
    runtime = rig.runtime
    if how == "new":
        return await runtime.new_session()
    if how == "resume":
        assert rig.other.session_file is not None
        return await runtime.switch_session(rig.other.session_file)
    if how == "fork":
        entry = (await rig.session.get_entries())[-1]
        return await runtime.fork(entry.id, position="at")
    assert rig.other.session_file is not None
    copy = rig.tmp / "import-me.jsonl"
    copy.write_text(Path(rig.other.session_file).read_text())
    return await runtime.import_from_jsonl(str(copy))


_OPERATION = {"new": "new", "resume": "resume", "fork": "fork", "import": "resume"}


@pytest.mark.parametrize("how", ["new", "resume", "fork", "import"])
async def test_a_swap_whose_build_fails_stays_on_this_session(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    old.state.thinking_level = "high"
    here = rig.session.session_file
    rig.fail = lambda session, seed: (
        RuntimeError("build boom") if session.session_file != here else None
    )

    with pytest.raises(RebuildFailedError) as caught:
        await _swap(rig, how)

    err = caught.value
    assert (err.state, err.operation, err.phase) == ("kept", _OPERATION[how], "build")
    assert str(err).endswith("stayed on this session and kept the previous settings")
    assert rig.runtime.session is not None and rig.runtime.session.session_file == here
    restored = rig.runtime.harness
    assert restored is not old and not restored.disposed
    assert restored.state.thinking_level == "high"
    assert [m.role for m in restored.messages] == ["user"]
    await restored.prompt("run it")
    assert rig.ran == ["run"]


@pytest.mark.parametrize("how", ["new", "resume", "fork", "import"])
async def test_a_swap_whose_restore_fails_too_stops(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    rig.fail = _always(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError) as caught:
        await _swap(rig, how)

    assert caught.value.state == "stopped"
    assert rig.runtime.stopped == STOPPED_MESSAGE
    assert rig.runtime.harness is old and old.turns_held == STOPPED_MESSAGE


async def test_a_factory_without_the_seed_keyword_is_restored_without_it(tmp_path: Path) -> None:
    """``HarnessFactory``: a factory that never declared ``reload_seed`` is never handed it."""

    rig = await _Rig.create(tmp_path)
    here = rig.session.session_file
    calls: list[str | None] = []

    async def positional(session: Session) -> AgentHarness:
        calls.append(session.session_file)
        if session.session_file != here:
            raise RuntimeError("build boom")
        return rig.harness(session)

    rig.runtime._create_harness = positional
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.new_session()
    assert caught.value.state == "kept"
    assert calls[-1] == here
    assert rig.runtime.stopped is None
