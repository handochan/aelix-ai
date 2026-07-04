"""Issue #21 (W1) — VS Code-style lazy activation for on_command plugins.

A manifest plugin whose ONLY activation trigger is ``on_command`` is deferred
at discovery (no module import, no factory run — ADR-0096 §Activation policy,
"lazy load is mandatory") and activated by the command-dispatch layer when a
declared trigger fires. End-to-end against the REAL loader + AgentHarness so
the two late-registration refreshes are genuinely exercised:

- ``refresh_tools`` — a tool registered by the late factory reaches
  ``harness.state.tools``;
- ``refresh_hooks`` (new, issue #21) — a hook registered by the late factory
  reaches the live HookBus (which is otherwise a construction-time snapshot).
"""

from __future__ import annotations

import textwrap
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
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
from aelix_coding_agent.extensions.command_dispatch import (
    CommandDispatchService,
    CommandSurfaceBindings,
    DispatchOutcome,
)
from aelix_coding_agent.extensions.loader import discover_and_load_extensions


def _manifest(module_name: str, *, activation: str) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "lazy-plug"
        name = "Lazy Plugin"
        version = "0.1.0"
        description = "Defers until its command fires"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/lazy-plug"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module_name}:setup"

        [activation]
        {activation}

        [contributes]
        commands = [{{ id = "lazy-cmd", description = "Lazy greeting" }}]
    """).strip()


def _module_src(import_marker: Path, setup_marker: Path) -> str:
    return textwrap.dedent(f"""
        from pathlib import Path

        Path({str(import_marker)!r}).write_text("IMPORTED")

        def setup(aelix):
            Path({str(setup_marker)!r}).write_text("SETUP")
            aelix.register_command(
                "lazy-cmd",
                handler=lambda args, ctx: "lazy says hi",
                description="Lazy greeting",
            )
            from aelix_agent_core.types import AgentTool
            aelix.register_tool(
                AgentTool(name="lazy_tool", description="late tool")
            )
            aelix.on("session_shutdown", lambda event, ctx: None)
    """)


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


async def _build(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    module_name: str,
    activation: str = 'on_command = ["lazy-cmd"]',
    module_src: str | None = None,
    contributes_extra: str = "",
) -> tuple[AgentHarness, Any]:
    """Real loader + real harness over a tmp plugin dir (module on sys.path
    via monkeypatch.syspath_prepend — auto-cleaned per test)."""
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "lazy-plug"
    pkg.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    manifest = _manifest(module_name, activation=activation)
    if contributes_extra:
        manifest += "\n" + contributes_extra
    pkg.joinpath("aelix-plugin.toml").write_text(manifest, encoding="utf-8")
    if module_src is not None:
        (tmp_path / f"{module_name}.py").write_text(module_src, encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
    loaded = await discover_and_load_extensions(
        [], cwd=cwd, agent_dir=agent_dir
    )
    assert loaded.errors == []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            extensions=loaded.extensions,
            runtime=loaded.runtime,
        )
    )
    return harness, loaded.runtime


def _bindings() -> tuple[CommandSurfaceBindings, list[str], list[str]]:
    texts: list[str] = []
    errors: list[str] = []
    return (
        CommandSurfaceBindings(emit_text=texts.append, emit_error=errors.append),
        texts,
        errors,
    )


async def test_lazy_plugin_defers_then_activates_on_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_defer_case",
        module_src=_module_src(import_marker, setup_marker),
    )

    # Deferred: shell present, NO code ran, command not yet registered.
    assert not import_marker.exists() and not setup_marker.exists()
    assert "lazy-plug" in runtime.pending_activations
    runner_names = {
        rc.invocation_name
        for rc in harness.extension_runner.get_registered_commands()
    }
    assert "lazy-cmd" not in runner_names

    svc = CommandDispatchService(lambda: harness)
    # The stub surfaces for autocomplete with the declared description.
    assert ("lazy-cmd", "Lazy greeting") in svc.list_commands()

    bindings, texts, errors = _bindings()
    result = await svc.try_execute("/lazy-cmd", bindings)

    assert result.outcome is DispatchOutcome.HANDLED, errors
    assert import_marker.exists() and setup_marker.exists()  # activated NOW
    assert texts == ["lazy says hi"]
    assert runtime.pending_activations == {}  # one-shot

    # refresh_tools: the late-registered tool reached the live registry.
    assert "lazy_tool" in {t.name for t in harness.state.tools}
    # refresh_hooks (issue #21): the late-registered hook reached the live
    # HookBus — without the re-sync this handler would be silently dead.
    assert harness.extension_runner.has_handlers("session_shutdown")

    # Subsequent invocations dispatch directly (no pending machinery).
    bindings2, texts2, _ = _bindings()
    result2 = await svc.try_execute("/lazy-cmd", bindings2)
    assert result2.outcome is DispatchOutcome.HANDLED
    assert texts2 == ["lazy says hi"]


async def test_eager_trigger_keeps_load_time_factory_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any eager trigger alongside on_command (here on_session_start) keeps
    today's behavior: the factory runs at load, nothing is deferred."""
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    _, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_eager_case",
        activation='on_command = ["lazy-cmd"]\non_session_start = true',
        module_src=_module_src(import_marker, setup_marker),
    )
    assert import_marker.exists() and setup_marker.exists()
    assert runtime.pending_activations == {}


async def test_activation_failure_reports_error_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken entry module surfaces as a dispatch ERROR (never a silent
    fall-through to the model) and the pending record is consumed one-shot —
    the next invocation is a plain NOT_A_COMMAND, not a retry loop."""
    harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="module_that_does_not_exist_lazy_case",
        module_src=None,  # entry module missing → activation will fail
    )
    assert "lazy-plug" in runtime.pending_activations

    svc = CommandDispatchService(lambda: harness)
    bindings, _, errors = _bindings()
    result = await svc.try_execute("/lazy-cmd", bindings)
    assert result.outcome is DispatchOutcome.ERROR
    assert any("activating plugin" in e for e in errors)
    assert runtime.pending_activations == {}

    bindings2, _, _ = _bindings()
    result2 = await svc.try_execute("/lazy-cmd", bindings2)
    assert result2.outcome is DispatchOutcome.NOT_A_COMMAND


async def test_activated_but_unregistered_trigger_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plugin activates fine but never registers its declared trigger —
    a plugin defect the user must see (ERROR), not a model fall-through."""
    module_src = textwrap.dedent("""
        def setup(aelix):
            pass  # declares on_command=["lazy-cmd"] but registers nothing
    """)
    harness, _ = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_unregistered_case",
        module_src=module_src,
    )
    svc = CommandDispatchService(lambda: harness)
    bindings, _, errors = _bindings()
    result = await svc.try_execute("/lazy-cmd", bindings)
    assert result.outcome is DispatchOutcome.ERROR
    assert any("did not register" in e for e in errors)


async def test_failed_activation_rolls_back_partial_registrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factory that registers surfaces THEN raises must leave the plugin
    fully inert (review HIGH, live-reproduced pre-fix): without the rollback
    its command executed on the NEXT invocation and its hooks armed on any
    later refresh_hooks."""
    module_src = textwrap.dedent("""
        def setup(aelix):
            aelix.register_command(
                "lazy-cmd", handler=lambda args, ctx: "leaked!"
            )
            from aelix_agent_core.types import AgentTool
            aelix.register_tool(AgentTool(name="leaked_tool", description="x"))
            aelix.on("session_shutdown", lambda event, ctx: None)
            raise RuntimeError("factory exploded after registering")
    """)
    harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_rollback_case",
        module_src=module_src,
    )
    svc = CommandDispatchService(lambda: harness)

    bindings, _, errors = _bindings()
    result = await svc.try_execute("/lazy-cmd", bindings)
    assert result.outcome is DispatchOutcome.ERROR
    assert any("activating plugin" in e for e in errors)

    # Rolled back: NOTHING the partial factory registered survives.
    runner_names = {
        rc.invocation_name
        for rc in harness.extension_runner.get_registered_commands()
    }
    assert "lazy-cmd" not in runner_names
    assert "leaked_tool" not in {t.name for t in harness.state.tools}
    assert not harness.extension_runner.has_handlers("session_shutdown")

    # A LATER refresh (e.g. another plugin's activation) must not arm the
    # failed plugin's leftovers either — the shell is empty.
    runtime.actions.refresh_tools()
    runtime.actions.refresh_hooks()
    assert "leaked_tool" not in {t.name for t in harness.state.tools}
    assert not harness.extension_runner.has_handlers("session_shutdown")

    # One-shot: the retry is a plain NOT_A_COMMAND (no half-activated exec).
    bindings2, _, _ = _bindings()
    result2 = await svc.try_execute("/lazy-cmd", bindings2)
    assert result2.outcome is DispatchOutcome.NOT_A_COMMAND


async def test_declared_tools_force_eager_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """contributes.tools forces EAGER load even with on_command-only
    activation (review MEDIUM): a deferred plugin's declared tools would
    silently vanish from the model until a human typed a trigger command."""
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_tools_eager_case",
        module_src=_module_src(import_marker, setup_marker),
        contributes_extra='tools = [{ name = "lazy_tool", description = "late tool" }]',
    )
    assert import_marker.exists() and setup_marker.exists()  # ran at load
    assert runtime.pending_activations == {}
    assert "lazy_tool" in {t.name for t in harness.state.tools}  # model-visible


async def test_tui_widgets_contrib_forces_eager_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """contributes.tui_widgets forces EAGER load (issue #21, ADR-0182): the
    manifest-widget adapter only reads LOADED extensions, so a deferred
    plugin's declared widgets would silently never paint — the same
    silent-vanish class as contributes.tools above."""
    import_marker = tmp_path / "imported.marker"
    setup_marker = tmp_path / "setup.marker"
    harness, runtime = await _build(
        tmp_path,
        monkeypatch,
        module_name="lazy_mod_widgets_eager_case",
        module_src=_module_src(import_marker, setup_marker),
        contributes_extra=(
            'tui_widgets = [{ slot = "above_editor", '
            'factory = "lazy_mod_widgets_eager_case:setup" }]\n'
            "\n"
            "[capabilities]\n"
            "ui_tui_trusted = true"
        ),
    )
    _ = harness
    assert import_marker.exists() and setup_marker.exists()  # ran at load
    assert runtime.pending_activations == {}
