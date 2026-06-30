"""Issue #5 (Lane C) — extension runtime introspection + Project Trust surface.

Part (1): ``exec`` / ``get_all_tools`` / ``get_commands`` return correct data
through a REAL :class:`ExtensionAPI` bound to a live :class:`AgentHarness`
(get_all_tools/get_commands delegate to the harness action table; exec is the
pi-faithful in-process ``execCommand`` port — pi does not bind ``exec`` through
``bindCore``).

Part (2): ``ctx.is_project_trusted()`` reflects the harness trust state; the
``project_trust`` decide/defer event fires via
:func:`emit_project_trust_event`; ``resolve_project_trusted`` honours the event
result and the ``default_project_trust`` setting (pi parity, SHA ``927e980``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    ProjectTrustContext,
    ProjectTrustEventDecision,
    ProjectTrustEventResult,
    ProjectTrustHookEvent,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.streaming import Model
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.cli.project_trust import (
    ProjectTrustStore,
    emit_project_trust_event,
    resolve_project_trusted,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[])


def _bound_api_and_harness(
    *,
    tools: list[AgentTool] | None = None,
    project_trusted: bool = True,
) -> tuple[ExtensionAPI, Extension, AgentHarness]:
    """A real ExtensionAPI sharing the harness's runtime (so actions are bound)."""

    rt = _ExtensionRuntime()
    ext = Extension(name="ext5")
    api = ExtensionAPI(ext, rt)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            runtime=rt,
            tools=tools or [],
            project_trusted=project_trusted,
        )
    )
    return api, ext, harness


# === Part (1): runtime introspection / action through a real ExtensionAPI ====


def test_get_commands_returns_registered_commands() -> None:
    api, _ext, _h = _bound_api_and_harness()
    api.register_command("greet", handler=lambda: None, description="say hi")
    cmds = api.get_commands()
    assert any(c.name == "greet" and c.source == "ext5" for c in cmds)
    assert any(c.description == "say hi" for c in cmds)


def test_get_all_tools_returns_harness_tool_set() -> None:
    tool = AgentTool(name="mytool", execute=_noop_execute)
    api, _ext, _h = _bound_api_and_harness(tools=[tool])
    infos = api.get_all_tools()
    names = {t.name for t in infos}
    assert "mytool" in names
    # ToolInfo views, not raw AgentTool instances.
    assert all(hasattr(t, "name") for t in infos)


async def test_exec_runs_command_and_returns_stdout() -> None:
    # exec is the in-process subprocess port (pi ``execCommand``); it works even
    # though the harness binds ``exec`` as a throwing stub (the API falls back).
    api, _ext, _h = _bound_api_and_harness()
    result = await api.exec("echo", ["aelix-exec-ok"])
    assert result.code == 0
    assert "aelix-exec-ok" in result.stdout
    assert result.killed is False


async def test_exec_missing_command_returns_127() -> None:
    api, _ext, _h = _bound_api_and_harness()
    result = await api.exec("this-binary-does-not-exist-aelix", [])
    assert result.code == 127


# === Part (2a): ctx.is_project_trusted() reflects harness trust state ========


def test_ctx_is_project_trusted_default_true() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=True)
    ctx = h._make_context()
    assert ctx.is_project_trusted() is True


def test_ctx_is_project_trusted_false_when_untrusted() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=False)
    ctx = h._make_context()
    assert ctx.is_project_trusted() is False


def test_set_project_trusted_is_reflected_live() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=True)
    assert h._make_context().is_project_trusted() is True
    h.set_project_trusted(False)
    # A fresh context built after the flip sees the new state.
    assert h._make_context().is_project_trusted() is False
    assert h.project_trusted is False


def test_command_context_also_reports_trust() -> None:
    _api, _ext, h = _bound_api_and_harness(project_trusted=False)
    cmd_ctx = h.make_command_context()
    assert cmd_ctx.is_project_trusted() is False


# === Part (2b): emit_project_trust_event walk semantics =====================


def _trust_ctx() -> ProjectTrustContext:
    return ProjectTrustContext(cwd="/proj", mode="interactive", has_ui=True)


async def test_emit_first_yes_no_decision_wins() -> None:
    ext = Extension(name="voter")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    def decide(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="no", remember=True)

    api.on("project_trust", decide)
    result, errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert errors == []
    assert result is not None
    assert result.trusted == "no"
    assert result.remember is True


async def test_emit_undecided_falls_through() -> None:
    ext = Extension(name="abstain")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    def abstain(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="undecided")

    api.on("project_trust", abstain)
    result, errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is None
    assert errors == []


async def test_emit_async_handler_supported() -> None:
    ext = Extension(name="async-voter")
    api = ExtensionAPI(ext, _ExtensionRuntime())

    async def decide(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        return ProjectTrustEventResult(trusted="yes")

    api.on("project_trust", decide)
    result, _errors = await emit_project_trust_event(
        [ext], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is not None and result.trusted == "yes"


async def test_emit_handler_error_collected_not_raised() -> None:
    bad = Extension(name="boom")
    bad_api = ExtensionAPI(bad, _ExtensionRuntime())

    def explode(event: ProjectTrustHookEvent, ctx: ProjectTrustContext) -> ProjectTrustEventResult:
        raise RuntimeError("kaboom")

    bad_api.on("project_trust", explode)

    good = Extension(name="good")
    good_api = ExtensionAPI(good, _ExtensionRuntime())
    good_api.on(
        "project_trust",
        lambda e, c: ProjectTrustEventResult(trusted="yes"),
    )

    # The bad extension's error is collected; the next extension still decides.
    result, errors = await emit_project_trust_event(
        [bad, good], ProjectTrustHookEvent(cwd="/proj"), _trust_ctx()
    )
    assert result is not None and result.trusted == "yes"
    assert len(errors) == 1
    assert "boom" in errors[0] and "kaboom" in errors[0]


# === Part (2c): resolve_project_trusted honours the event + default =========


def _resources_dir(tmp_path: Path) -> Path:
    aelix = tmp_path / ".aelix"
    aelix.mkdir(parents=True)
    (aelix / "mcp.json").write_text('{"mcpServers": {}}')
    return tmp_path


def _voting_extension(
    decision: ProjectTrustEventDecision, *, remember: bool | None = None
) -> Extension:
    ext = Extension(name=f"vote-{decision}")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on(
        "project_trust",
        lambda e, c: ProjectTrustEventResult(trusted=decision, remember=remember),
    )
    return ext


async def test_resolve_event_yes_trusts_before_store(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(cwd, False)  # store says NO, but the event decides first.
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=store,
        extensions=[_voting_extension("yes")],
    )
    assert out is True


async def test_resolve_event_remember_persists(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    store = ProjectTrustStore(tmp_path / "agent")
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=store,
        extensions=[_voting_extension("no", remember=True)],
    )
    assert out is False
    # remember=True wrote the decision to disk.
    assert store.get(cwd) is False


async def test_resolve_event_undecided_falls_through_to_deny(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,  # headless → deny-by-default after the event abstains
        store=ProjectTrustStore(tmp_path / "agent"),
        extensions=[_voting_extension("undecided")],
    )
    assert out is False


async def test_resolve_event_errors_reported(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    bad = Extension(name="boom")
    ExtensionAPI(bad, _ExtensionRuntime()).on(
        "project_trust",
        lambda e, c: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    seen: list[str] = []
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=ProjectTrustStore(tmp_path / "agent"),
        extensions=[bad],
        on_extension_error=seen.append,
    )
    assert out is False  # handler raised → no decision → headless deny
    assert len(seen) == 1 and "nope" in seen[0]


async def test_resolve_default_always_trusts_without_prompt(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=False,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="always",
    )
    assert out is True


async def test_resolve_default_never_denies(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)

    async def _prompt(_c: Path) -> Any:
        raise AssertionError("must not prompt when default is 'never'")

    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="never",
    )
    assert out is False


async def test_resolve_default_ask_falls_through_to_prompt(tmp_path: Path) -> None:
    cwd = _resources_dir(tmp_path)
    from aelix_coding_agent.cli.project_trust import ProjectTrustPromptResult

    async def _prompt(_c: Path) -> ProjectTrustPromptResult:
        return ProjectTrustPromptResult(trusted=True, remember=False)

    out = await resolve_project_trusted(
        cwd,
        override=None,
        has_ui=True,
        prompt=_prompt,
        store=ProjectTrustStore(tmp_path / "agent"),
        default_project_trust="ask",
    )
    assert out is True
