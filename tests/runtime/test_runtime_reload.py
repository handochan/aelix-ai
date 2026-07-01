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
from aelix_agent_core.runtime import AgentSessionRuntime, ReloadSeed
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_agent_core.types import AgentTool
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


async def _build_harness(
    session: Session,
    cwd: Path,
    agent_dir: Path,
    *,
    reload_seed: ReloadSeed | None = None,
    app_tools: list[AgentTool] | None = None,
    active_tool_names: list[str] | None = None,
) -> AgentHarness:
    # Mirror entry.py _harness_factory / _build_harness_options: re-discover on-disk
    # extensions every build; (on reload) pre-seed the restored flag values into the
    # fresh runtime BEFORE setup() re-runs (#24-FU ReloadSeed); and — critically —
    # on reload build UNFILTERED (active_tool_names=None) and defer the active set to
    # reload() step-6, because re-applying a launch ``--tools`` filter that named a
    # since-removed extension tool would raise inside construction and brick the
    # session (adversarial-review MEDIUM).
    flag_values = reload_seed.flag_values if reload_seed is not None else None
    loaded = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=agent_dir, flag_values=flag_values
    )
    active = None if reload_seed is not None else active_tool_names
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
            extensions=loaded.extensions,
            runtime=loaded.runtime,
            # Non-extension (app/builtin-like) tools — stable across rebuilds so a
            # ``--tools`` filter over them is what the #24-FU active-tool round-trip
            # must preserve (extension tools are separately unioned in on reload).
            tools=list(app_tools) if app_tools else [],
            active_tool_names=active,
        )
    )


async def _make_runtime(
    tmp_path: Path,
    *,
    app_tools: list[AgentTool] | None = None,
    active_tool_names: list[str] | None = None,
    pre_files: dict[str, str] | None = None,
) -> tuple[AgentSessionRuntime, Path]:
    cwd = tmp_path / "proj"
    ext_dir = cwd / ".aelix" / "extensions"
    ext_dir.mkdir(parents=True)
    # Extensions that must exist BEFORE the first build (e.g. so a launch --tools
    # filter naming an extension tool validates at construction time).
    for fname, src in (pre_files or {}).items():
        (ext_dir / fname).write_text(src)
    agent_dir = tmp_path / "agent"  # empty -> no global ~/.aelix extensions leak in
    agent_dir.mkdir()
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(cwd)))

    async def _factory(
        new_sess: Session, *, reload_seed: ReloadSeed | None = None
    ) -> AgentHarness:
        return await _build_harness(
            new_sess,
            cwd,
            agent_dir,
            reload_seed=reload_seed,
            app_tools=app_tools,
            active_tool_names=active_tool_names,
        )

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


# === #24-FU: flag pre-seed BEFORE setup() ==================================


async def test_reload_pre_seeds_flag_before_setup(tmp_path: Path) -> None:
    # #24-FU FU1: the reload ReloadSeed pre-seeds flag_values BEFORE each setup()
    # re-runs, so a setup() that BRANCHES on a flag reads the user's RESTORED value
    # (not the register_flag default). The belt-and-braces post-apply restore fixes
    # the end-state flag VALUE but NOT a setup-time branch — so this asserts the
    # branch outcome: a command registered only when the flag reads True at setup.
    runtime, cwd = await _make_runtime(tmp_path)
    (_ext_dir(cwd) / "brancher.py").write_text(
        "def setup(aelix):\n"
        "    aelix.register_flag('gate', type='bool', default=False)\n"
        "    if aelix.get_flag('gate'):\n"
        "        aelix.register_command('gated', handler=lambda *a, **k: 'y')\n"
    )
    await runtime.reload()  # first load: gate defaults False -> no 'gated'
    assert "gated" not in _command_names(runtime)

    runtime.harness.extension_runner.set_flag_value("gate", True)  # user toggles ON
    await runtime.reload()  # rebuild: setup() must read the RESTORED True
    assert "gated" in _command_names(runtime)


# === #24-FU: active-tool round-trip + includeAllExtensionTools union =========


async def test_reload_preserves_filter_and_unions_extension_tools(
    tmp_path: Path,
) -> None:
    # #24-FU FU2: an explicit active-tool filter over a NON-extension (app) tool
    # SURVIVES the reload rebuild (round-trip), AND pi ``includeAllExtensionTools``
    # unions ALL current extension tools on top — so a just-written extension tool
    # is usable even under a filter that never named it (the #53 moat).
    app_tool = AgentTool(name="app_tool", description="builtin-like app tool")
    runtime, cwd = await _make_runtime(tmp_path, app_tools=[app_tool])
    (_ext_dir(cwd) / "toolerA.py").write_text(
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool(AgentTool(name='ext_a', description='a'))\n"
    )
    await runtime.reload()
    # User filters active tools to ONLY the app tool (ext_a not active).
    await runtime.harness.set_active_tools(["app_tool"])
    assert runtime.harness.state.active_tool_names == ["app_tool"]

    # A new extension tool is written, then reload.
    (_ext_dir(cwd) / "toolerB.py").write_text(
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool(AgentTool(name='ext_b', description='b'))\n"
    )
    await runtime.reload()

    active = set(runtime.harness.state.active_tool_names or [])
    assert "app_tool" in active  # filter over the app tool survived the reload
    assert {"ext_a", "ext_b"} <= active  # ALL extension tools unioned back in


async def test_reload_drops_removed_extension_tool_from_active_filter(
    tmp_path: Path,
) -> None:
    # #24-FU FU2 — RUNTIME-snapshot path. When the active filter was set at RUNTIME
    # (set_active_tools) to an extension tool that is then removed, reload() step-6's
    # intersect must drop the now-stale name — aelix ``set_active_tools`` RAISES on
    # unknown names, so a naive restore would crash the reload. (The companion test
    # ``test_reload_survives_removed_tool_named_by_launch_filter`` covers the other
    # source of the same stale name: a launch ``--tools`` filter re-applied by the
    # factory during _apply.)
    runtime, cwd = await _make_runtime(tmp_path)
    tool_file = _ext_dir(cwd) / "tooler.py"
    tool_file.write_text(
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool(AgentTool(name='ext_gone', description='x'))\n"
    )
    await runtime.reload()
    await runtime.harness.set_active_tools(["ext_gone"])
    assert runtime.harness.state.active_tool_names == ["ext_gone"]

    tool_file.unlink()  # remove the extension providing 'ext_gone'
    await runtime.reload()  # must NOT raise on the now-stale 'ext_gone' name

    assert "ext_gone" not in (runtime.harness.state.active_tool_names or [])
    assert "ext_gone" not in {t.name for t in runtime.harness._state.tools}


async def test_reload_survives_removed_tool_named_by_launch_filter(
    tmp_path: Path,
) -> None:
    # #24-FU regression (adversarial-review MEDIUM). If the LAUNCH ``--tools`` filter
    # named an extension tool whose extension is later removed, the reload rebuild
    # must NOT crash. The factory re-applying the raw filter through the harness's
    # RAISING active-tool validator would raise INSIDE _apply — AFTER teardown has
    # disposed the old harness — bricking the session. The fix: the factory drops
    # the launch filter on reload (builds unfiltered) and reload() step-6 restores
    # the live filter intersected with the rebuilt registry (dropping the removed
    # name). Before the fix this reload raised AgentHarnessError; it must now pass.
    ext_src = (
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool(AgentTool(name='ext_gone', description='x'))\n"
    )
    runtime, cwd = await _make_runtime(
        tmp_path,
        active_tool_names=["ext_gone"],  # simulate `--tools ext_gone`
        pre_files={"tooler.py": ext_src},  # ext present at first build -> validates
    )
    assert runtime.harness.state.active_tool_names == ["ext_gone"]

    (_ext_dir(cwd) / "tooler.py").unlink()  # remove the extension providing ext_gone
    await runtime.reload()  # must NOT raise (was: AgentHarnessError, session bricked)

    assert "ext_gone" not in (runtime.harness.state.active_tool_names or [])
    assert "ext_gone" not in {t.name for t in runtime.harness._state.tools}


async def test_reload_leaves_unfiltered_active_as_all_tools(
    tmp_path: Path,
) -> None:
    # #24-FU FU2: when NO explicit filter existed (active_tool_names is None => all
    # tools active) and the rebuild is likewise unfiltered, reload does NOT
    # needlessly materialize the active list — it stays None, and a newly-written
    # extension tool is active (None => all).
    runtime, cwd = await _make_runtime(tmp_path)
    assert runtime.harness.state.active_tool_names is None
    (_ext_dir(cwd) / "tooler.py").write_text(
        "def setup(aelix):\n"
        "    from aelix_agent_core.types import AgentTool\n"
        "    aelix.register_tool(AgentTool(name='ext_t', description='x'))\n"
    )
    await runtime.reload()

    assert runtime.harness.state.active_tool_names is None  # not materialized
    assert "ext_t" in {t.name for t in runtime.harness._state.tools}
