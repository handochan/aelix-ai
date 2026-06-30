"""Issue #24 — ``AgentSessionRuntime.reload()`` full hot-reload round-trip via
the P-302 factory rebuild (ADR-pending).

Proves the #53 moat: write an extension file AFTER startup -> ``reload()`` ->
its command goes live WITHOUT a process restart; flag values round-trip; a
removed extension's command disappears (HookBus/runner rebuilt from disk); the
old runtime is invalidated (captured ctx goes stale); the rebind callback fires
with ``reason="reload"``.

The factory mirrors the production coding-agent ``_harness_factory``: it re-runs
``discover_and_load_extensions`` over a temp ``.aelix/extensions`` dir on every
build, so a freshly-written file is re-discovered by the reload rebuild.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import ExtensionError
from aelix_coding_agent.extensions.loader import discover_and_load_extensions


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


async def _build_harness(session: Session, cwd: Path, agent_dir: Path) -> AgentHarness:
    # Mirror entry.py _harness_factory: re-discover on-disk extensions every build.
    loaded = await discover_and_load_extensions([], cwd=cwd, agent_dir=agent_dir)
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
            extensions=loaded.extensions,
            runtime=loaded.runtime,
        )
    )


async def _make_runtime(tmp_path: Path) -> tuple[AgentSessionRuntime, Path]:
    cwd = tmp_path / "proj"
    (cwd / ".aelix" / "extensions").mkdir(parents=True)
    agent_dir = tmp_path / "agent"  # empty -> no global ~/.aelix extensions leak in
    agent_dir.mkdir()
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(cwd)))

    async def _factory(new_sess: Session) -> AgentHarness:
        return await _build_harness(new_sess, cwd, agent_dir)

    harness = await _factory(session)
    runtime = AgentSessionRuntime(harness, _factory, repo=repo, fs=fs)
    return runtime, cwd


def _ext_dir(cwd: Path) -> Path:
    return cwd / ".aelix" / "extensions"


def _command_names(runtime: AgentSessionRuntime) -> set[str]:
    return {
        c.invocation_name
        for c in runtime.harness.extension_runner.get_registered_commands()
    }


async def test_reload_picks_up_newly_written_extension(tmp_path: Path) -> None:
    # THE #24 moat regression: write an extension file AFTER startup -> /reload ->
    # its command is live WITHOUT a process restart.
    runtime, cwd = await _make_runtime(tmp_path)
    assert "foo" not in _command_names(runtime)  # absent at startup

    (_ext_dir(cwd) / "foo.py").write_text(
        "def setup(aelix):\n"
        "    aelix.register_command("
        "'foo', handler=lambda *a, **k: 'foo!', description='x')\n"
    )
    await runtime.reload()

    assert "foo" in _command_names(runtime)  # re-discovered + live, no restart


async def test_reload_round_trips_flag_values(tmp_path: Path) -> None:
    # A user-toggled extension flag must SURVIVE the reload rebuild (pi
    # _buildRuntime flagValues loop), not reset to the re-registered default.
    runtime, cwd = await _make_runtime(tmp_path)
    (_ext_dir(cwd) / "flagger.py").write_text(
        "def setup(aelix):\n"
        "    aelix.register_flag('myflag', type='bool', default=False)\n"
    )
    await runtime.reload()  # load the ext (seeds default False)
    runtime.harness.extension_runner.set_flag_value("myflag", True)  # user toggles
    await runtime.reload()  # reload again

    assert runtime.harness.extension_runner.get_flag_values().get("myflag") is True


async def test_reload_drops_removed_extension_command(tmp_path: Path) -> None:
    # Removing an extension file then reloading rebuilds the runner/HookBus from
    # the re-discovered set, so the removed command disappears.
    runtime, cwd = await _make_runtime(tmp_path)
    foo = _ext_dir(cwd) / "foo.py"
    foo.write_text(
        "def setup(aelix):\n"
        "    aelix.register_command('foo', handler=lambda *a, **k: 'foo!')\n"
    )
    await runtime.reload()
    assert "foo" in _command_names(runtime)

    foo.unlink()
    await runtime.reload()
    assert "foo" not in _command_names(runtime)


async def test_reload_invalidates_old_runtime(tmp_path: Path) -> None:
    # The teardown invalidates the OLD _ExtensionRuntime, so a ctx captured before
    # reload fails loudly (staleness contract); the harness object is swapped.
    runtime, _cwd = await _make_runtime(tmp_path)
    old_runtime = runtime.harness.runtime  # the _ExtensionRuntime bridge
    await runtime.reload()

    with pytest.raises(ExtensionError) as excinfo:
        old_runtime.assert_active()
    # Contract: the OLD ctx raises a staleness error (code == "stale"). The message
    # is the dispose override ("AgentHarness has been disposed") rather than the
    # reload-specific PI_STALENESS_MESSAGE — a documented LOW divergence: the P-302
    # rebuild genuinely disposes the old harness, so dispose()'s invalidate() runs
    # last (last-write-wins). The contract (loud failure) is what matters.
    assert excinfo.value.code == "stale"
    assert runtime.harness.runtime is not old_runtime


async def test_reload_fires_rebind_with_reload_reason(tmp_path: Path) -> None:
    # The rebind callback is invoked with reason="reload" (so a TUI can preserve
    # its transcript) — the widened set_rebind_session contract.
    runtime, _cwd = await _make_runtime(tmp_path)
    seen: list[str] = []

    async def _rebind(h: AgentHarness, reason: str = "resume") -> None:
        seen.append(reason)

    runtime.set_rebind_session(_rebind)
    await runtime.reload()
    assert seen == ["reload"]


async def test_reload_reuses_same_session_and_returns_uncancelled(
    tmp_path: Path,
) -> None:
    # reload() reuses the SAME Session object (no repo.create/fork) and never
    # cancels (no before-switch veto).
    runtime, _cwd = await _make_runtime(tmp_path)
    session_before = runtime.harness.session
    result = await runtime.reload()
    assert result.cancelled is False
    assert runtime.harness.session is session_before


async def test_reload_picks_up_newly_written_extension_tool(tmp_path: Path) -> None:
    # The #53 moat for TOOLS (not just commands): a newly-written extension's
    # register_tool() tool is live in the rebuilt harness's tool registry after
    # reload, no restart.
    runtime, cwd = await _make_runtime(tmp_path)
    assert "reload_tool" not in {t.name for t in runtime.harness._state.tools}

    (_ext_dir(cwd) / "tooler.py").write_text(
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool("
        "AgentTool(name='reload_tool', description='added by reload'))\n"
    )
    await runtime.reload()

    assert "reload_tool" in {t.name for t in runtime.harness._state.tools}


async def test_reload_does_not_clear_streaming_providers(tmp_path: Path) -> None:
    # Adversarial-review HIGH regression guard: reload() must NOT flush the global
    # _PROVIDERS streaming-dispatch table (the factory never re-registers it), or
    # every model access bricks until a process restart. A registered provider
    # survives reload. (The other tests inject a mock stream_fn that bypasses
    # _PROVIDERS, so this is the only test exercising the real dispatch registry.)
    from aelix_ai.api_registry import (
        get_registered_providers,
        register_provider,
        unregister_provider,
    )

    runtime, _cwd = await _make_runtime(tmp_path)

    async def _fake_stream(
        model: Any, context: Any, options: Any
    ) -> AsyncIterator[Any]:
        # Empty async generator — never invoked; registration is what matters.
        return
        yield  # pragma: no cover — unreachable, makes this an async generator

    register_provider("reload-probe-api", _fake_stream, source_id="test")
    try:
        assert "reload-probe-api" in get_registered_providers()
        await runtime.reload()
        assert "reload-probe-api" in get_registered_providers()  # NOT cleared
    finally:
        unregister_provider("reload-probe-api")
