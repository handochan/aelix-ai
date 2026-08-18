"""The ``aelix_status`` tool — registration, redaction, liveness, degradation (#101).

THE BENCH IS REAL. Every test below drives a genuine :class:`_ExtensionRuntime` /
:class:`ExtensionAPI` / :class:`AgentHarness` triple in the order
``cli/entry.py`` builds them (extensions load, THEN the harness is constructed
over their shared runtime), and reaches the tool through the registration record
the factory actually wrote — ``record.tools[STATUS_TOOL_NAME]`` and
``record.handlers["tool_call"]`` — never by calling ``StatusExtension._execute``
by name. A double that called the method directly would keep passing after the
``register_tool`` / ``on`` wiring was deleted.

REDACTION GETS THE HARDER STANDARD, because it is the whole point of this tool:
the credential in :func:`test_planted_credentials_never_reach_the_output` is
planted into a REAL ``aelix-plugin.toml`` that a REAL
``discover_and_load_extensions`` parses, and into the process environment, and
the assertion is on the tool's real output string. Asserting that the word
"token" is absent would prove nothing — a snapshot that emitted the whole
manifest under a different key would pass it.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_agent_core.types import AgentTool
from aelix_ai.providers._openai_responses_shared import convert_responses_tools
from aelix_ai.streaming import Model
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_status import STATUS_TOOL_NAME, STATUS_TOOL_PARAMETERS, StatusExtension

SECRET_IN_MANIFEST = "ghp_PLANTED_IN_MANIFEST_abc123"
SECRET_IN_ENV = "sk-PLANTED_IN_ENVIRONMENT_xyz789"

_MODULE_SRC = "def setup(aelix):\n    return None\n"


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[])


class _Bench:
    """A live extension + harness pair plus the handles the tests drive."""

    def __init__(
        self,
        *,
        tools: list[AgentTool] | None = None,
        cwd: str | None = None,
        harness_project_trusted: bool = True,
        **status_kwargs: Any,
    ) -> None:
        self.runtime = _ExtensionRuntime()
        self.record = Extension(name="StatusExtension")
        self.api = ExtensionAPI(self.record, self.runtime)
        self.ext = StatusExtension(**status_kwargs)
        # Production order: the factory runs during extension LOAD, before the
        # harness exists (``entry.py`` passes ``loaded.extensions`` /
        # ``loaded.runtime`` into ``AgentHarnessOptions``).
        self.ext(self.api)
        self.harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="m", api="anthropic"),
                extensions=[self.record],
                runtime=self.runtime,
                tools=list(tools or []),
                cwd=cwd or os.getcwd(),
                project_trusted=harness_project_trusted,
            )
        )

    @property
    def tool(self) -> AgentTool:
        return self.record.tools[STATUS_TOOL_NAME]

    async def deliver_context(self, tool_name: str = STATUS_TOOL_NAME) -> None:
        """Run the REGISTERED ``tool_call`` handler with a real context.

        This is how the extension ever sees an ``ExtensionContext`` in
        production — the hook fires immediately before the ``execute()`` of the
        call it describes.
        """

        (handler,) = self.record.handlers["tool_call"]
        event = ToolCallHookEvent(tool_call_id="c1", tool_name=tool_name, args={})
        assert await handler(event, self.harness._make_context()) is None

    async def call(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.deliver_context()
        result = await self.tool.execute(args or {}, ToolExecutionContext())
        assert result.is_error is False
        (block,) = result.content
        return json.loads(block.text)


# === registration =============================================================


def test_the_factory_registers_the_tool_and_a_non_blocking_hook() -> None:
    bench = _Bench()
    assert set(bench.record.tools) == {STATUS_TOOL_NAME}
    assert len(bench.record.handlers["tool_call"]) == 1
    assert bench.tool.execute is not None


def test_the_no_argument_schema_survives_the_responses_wire_shape() -> None:
    """A bare ``{}`` would reach OpenAI Responses unchanged.

    Measured on this tree, one tool through the shipped converters:

        parameters={}        -> convert_responses_tools -> "parameters": {}
        parameters={}        -> google convert_tools    -> "parametersJsonSchema": {}
        parameters={}        -> openai_completions      -> {"type":"object","properties":{}}

    Only the Chat-Completions path normalises, and only by accident of a falsy
    ``or`` (``openai_completions.py:505``). The Responses adapter tests
    ``params is None`` (``_openai_responses_shared.py:171``), and ``{}`` is not
    ``None``. So the schema is declared rather than left empty, exactly as
    ``tools/ls.py`` declares it for its all-optional parameters.
    """

    bench = _Bench()
    assert bench.tool.parameters == {
        "type": "object",
        "properties": {},
        "required": [],
    }
    (wire,) = convert_responses_tools([bench.tool])
    assert wire["name"] == STATUS_TOOL_NAME
    assert wire["parameters"] == STATUS_TOOL_PARAMETERS
    assert wire["parameters"] != {}


# === redaction ================================================================


def _leaky_manifest(module: str) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "leakypack"
        name = "Leaky Pack"
        version = "2.3.4"
        description = "Ships an MCP server whose env holds a credential"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/leakypack"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [plugin.entry]
        python = "{module}:setup"

        [capabilities]
        shell_exec = true

        [activation]
        on_startup_finished = true

        [[contributes.mcp_servers]]
        name = "secretsrv"
        transport = "stdio"
        command = "node"
        [contributes.mcp_servers.env]
        GITHUB_TOKEN = "{SECRET_IN_MANIFEST}"
    """).strip()


async def _load_real_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[Any], str, Path]:
    """Load one extension per discovery tier through the real loader.

    Returns ``(extensions, cwd, agent_dir)``. The paths sit under a fake home so
    the "$HOME must not appear" assertion has something specific to look for.
    """

    home = tmp_path / "home" / "someuser"
    cwd = home / "proj"
    (cwd / ".aelix" / "extensions").mkdir(parents=True)
    (cwd / ".aelix" / "extensions" / "projext.py").write_text(
        _MODULE_SRC, encoding="utf-8"
    )
    agent_dir = home / ".aelix" / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "extensions" / "globext.py").write_text(_MODULE_SRC, encoding="utf-8")

    module = "leakypack_status_mod"
    (tmp_path / f"{module}.py").write_text(_MODULE_SRC, encoding="utf-8")
    pkg = agent_dir / "extensions" / "leakypack"
    pkg.mkdir()
    (pkg / "aelix-plugin.toml").write_text(_leaky_manifest(module), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    explicit = home / "elsewhere" / "explicitext.py"
    explicit.parent.mkdir(parents=True)
    explicit.write_text(_MODULE_SRC, encoding="utf-8")

    loaded = await discover_and_load_extensions(
        [str(explicit)], cwd=cwd, agent_dir=agent_dir
    )
    assert not loaded.errors, [(e.path, e.error) for e in loaded.errors]
    return loaded.extensions, str(cwd), agent_dir


async def test_planted_credentials_never_reach_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extensions, cwd, agent_dir = await _load_real_tiers(tmp_path, monkeypatch)
    monkeypatch.setenv("AELIX_TEST_PLANTED_CREDENTIAL", SECRET_IN_ENV)

    # The manifest secret is genuinely reachable from what the tool is handed —
    # otherwise this whole test asserts against an empty room.
    leaky = next(e for e in extensions if e.name == "leakypack")
    assert leaky.manifest is not None
    assert leaky.manifest.contributes.mcp_servers[0].env["GITHUB_TOKEN"] == (
        SECRET_IN_MANIFEST
    )
    assert SECRET_IN_ENV in os.environ.values()

    bench = _Bench(
        extensions=extensions, cwd=cwd, agent_dir=agent_dir, mode="print"
    )
    payload = await bench.call()
    rendered = json.dumps(payload)

    assert SECRET_IN_MANIFEST not in rendered
    assert SECRET_IN_ENV not in rendered
    assert "AELIX_TEST_PLANTED_CREDENTIAL" not in rendered
    # No environment values at all, not merely not-ours.
    assert "mcp_servers" not in rendered
    assert "secretsrv" not in rendered

    # The pack IS reported — a snapshot that redacted by dropping the extension
    # would pass every assertion above and be useless.
    names = {e["name"] for e in payload["loaded_extensions"]}
    assert "leakypack" in names
    entry = next(e for e in payload["loaded_extensions"] if e["name"] == "leakypack")
    assert entry["version"] == "2.3.4"
    assert entry["has_manifest"] is True


async def test_no_extension_path_or_home_directory_is_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Extension.name`` is an absolute path for the directory tiers.

    Measured through the real loader:
    ``name='<tmp>/proj/.aelix/extensions/projext.py'``. On a user's machine that
    string is their home directory and OS username, which is why
    ``extension_paths`` was dropped from the snapshot and why the label is a
    basename.
    """

    extensions, cwd, agent_dir = await _load_real_tiers(tmp_path, monkeypatch)
    home = str(tmp_path / "home" / "someuser")

    raw_names = {e.name for e in extensions}
    assert any(n.startswith(home) for n in raw_names), raw_names

    bench = _Bench(extensions=extensions, cwd=cwd, agent_dir=agent_dir)
    payload = await bench.call()

    # ``cwd`` is the one path that IS emitted, and only because
    # ``build_system_prompt`` already emits it (``cli/agent_context.py:1067``) —
    # so it is excluded here rather than the assertion being weakened, and its
    # value is pinned separately.
    assert payload["cwd"] == cwd
    rendered = json.dumps({k: v for k, v in payload.items() if k != "cwd"})

    assert "someuser" not in rendered
    assert str(agent_dir) not in rendered
    # Only the PATH-shaped names — a manifest pack's ``Extension.name`` is its
    # ``[plugin] id`` (``leakypack``), which is exactly what the snapshot is
    # supposed to report.
    for name in raw_names:
        if os.sep in name:
            assert name not in rendered


async def test_scope_labels_follow_the_discovery_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extensions, cwd, agent_dir = await _load_real_tiers(tmp_path, monkeypatch)
    bench = _Bench(extensions=extensions, cwd=cwd, agent_dir=agent_dir)
    payload = await bench.call()

    scopes = {e["name"]: e["scope"] for e in payload["loaded_extensions"]}
    assert scopes["projext.py"] == "project"
    assert scopes["globext.py"] == "global"
    assert scopes["explicitext.py"] == "explicit"
    # A manifest pack is located by ``resolved_path`` — the only extension kind
    # whose ``name`` is not path-shaped but which still has a real home.
    assert scopes["leakypack"] == "global"


def test_an_inline_prepended_extension_is_labelled_unclassified() -> None:
    """The honest label, and what is left of the reason it exists.

    ``loader._resolve_factory`` gives an inline factory (every PREPENDED
    built-in) and a manifest-less entry-point pack the SAME derived name —
    ``__qualname__ or type(...).__name__`` at ``loader.py:1868-1871`` and
    ``:1870-1873``. The endpoint half is no longer stuck here: since the #101
    M1 review the loader records that tier in ``Extension.source_info`` and it
    reports ``entry_point`` (``tests/status/test_status_scope_entry_point.py``).

    An inline prepend has no such record and no path, so it stays
    ``unclassified``. Claiming "builtin" would mean hardcoding a list of
    built-in class names — the list ``tui/extension_manager.py`` keeps — and a
    label that is true beats one that is specific and wrong.
    """

    bench = _Bench(extensions=[Extension(name="GuardrailExtension")])
    (only,) = bench.ext.snapshot().loaded_extensions
    assert only.name == "GuardrailExtension"
    assert only.scope == "unclassified"


# === liveness =================================================================


async def test_it_reports_live_state_rather_than_a_startup_snapshot() -> None:
    """Same extension instance, three mutations, three different answers."""

    tool = AgentTool(name="mytool", execute=_noop_execute)
    holder: list[Any] = []
    bench = _Bench(tools=[tool], extensions=holder, mode="interactive")

    first = await bench.call()
    assert first["mode"] == "interactive"
    assert "mytool" in first["all_tools"]
    assert first["loaded_extensions"] == []

    # (1) the extension list is held BY REFERENCE, the way ``entry.py``'s
    # ``discovered_extensions`` holder is refilled on every harness rebuild.
    holder.append(Extension(name="LateArrival"))
    # (2) the active tool set is a runtime action, not a startup value.
    bench.api.set_active_tools(["mytool"])

    second = await bench.call()
    assert [e["name"] for e in second["loaded_extensions"]] == ["LateArrival"]
    assert second["active_tools"] == ["mytool"]
    assert second["active_tools"] != first["active_tools"]


def test_the_run_mode_is_whatever_the_caller_resolved() -> None:
    for mode in ("interactive", "print", "json", "rpc"):
        assert _Bench(mode=mode).ext.snapshot().mode == mode
    # Unwired is an honest gap, never a guess from argv or a TTY probe.
    assert _Bench().ext.snapshot().mode == "unknown"


# === fail-closed trust ========================================================


def test_project_trust_is_not_read_from_the_context_default() -> None:
    """The regression this issue exists to prevent.

    ``ctx.is_project_trusted()`` answers ``True`` here — the harness below was
    built with the field's own default, exactly as ``rpc_ws.py:90-94`` builds
    one. A snapshot that consulted the context would report "trusted" for a
    process where nothing ever resolved trust.
    """

    bench = _Bench(harness_project_trusted=True)
    assert bench.harness._make_context().is_project_trusted() is True
    assert bench.ext.snapshot().project_trusted is False


def test_project_trust_reports_the_wired_decision_both_ways() -> None:
    assert _Bench(project_trusted=lambda: True).ext.snapshot().project_trusted is True
    assert _Bench(project_trusted=lambda: False).ext.snapshot().project_trusted is False


def test_a_raising_trust_getter_is_not_evidence_of_trust() -> None:
    def _boom() -> bool:
        raise RuntimeError("trust store unreadable")

    assert _Bench(project_trusted=_boom).ext.snapshot().project_trusted is False


def test_the_wired_decision_is_read_live() -> None:
    """A ``/trust``-style flip mid-session must be visible.

    The callable, not a bool, for the same reason
    ``AgentsExtension.no_context_files`` is one.
    """

    trusted = [False]
    ext = StatusExtension(project_trusted=lambda: trusted[0])
    assert ext.snapshot().project_trusted is False
    trusted[0] = True
    assert ext.snapshot().project_trusted is True


# === degradation ==============================================================


def test_a_snapshot_taken_before_any_hook_still_answers() -> None:
    """No ``ExtensionContext`` has been delivered — nothing may raise."""

    ext = StatusExtension(cwd="/some/where", mode="rpc")
    snap = ext.snapshot()
    assert snap.cwd == "/some/where"
    assert snap.active_tools == ()
    assert snap.all_tools == ()
    assert snap.loaded_extensions == ()
    assert snap.project_trusted is False


async def test_it_degrades_under_an_rpc_ws_shaped_harness(tmp_path: Path) -> None:
    """``rpc_ws.py:90-94`` builds options with no ``tools=`` and no trust.

    The whole snapshot must survive several missing sources at once, because
    that call site is real and is not going to grow arguments for this tool.
    """

    bench = _Bench(cwd=str(tmp_path))
    payload = await bench.call()
    assert payload["all_tools"] == [STATUS_TOOL_NAME]
    assert payload["project_trusted"] is False
    assert payload["cwd"] == str(tmp_path)
    assert payload["manifest_api_level"] == 1
    assert payload["version"]


def test_a_stale_runtime_does_not_turn_the_tool_into_an_error() -> None:
    """After ``invalidate`` every action raises; the snapshot degrades instead.

    ``/reload`` and ``AgentHarness.dispose`` both invalidate the old runtime
    while handlers may still hold it, and ``assert_active`` then raises
    ``ExtensionError("stale", ...)`` from inside ``get_all_tools`` /
    ``get_active_tools`` — a code the API's own ``"unbound"`` fallback does not
    catch.
    """

    bench = _Bench()
    bench.runtime.invalidate("replaced by a newer harness")
    snap = bench.ext.snapshot()
    assert snap.all_tools == ()
    assert snap.active_tools == ()


def test_a_malformed_extension_record_is_labelled_not_raised() -> None:
    class _Odd:
        pass

    bench = _Bench(extensions=[_Odd()])
    (only,) = bench.ext.snapshot().loaded_extensions
    assert only.name == "?"
    assert only.scope == "unclassified"
    assert only.has_manifest is False


# === read-only ================================================================


async def test_calling_it_twice_changes_nothing() -> None:
    tool = AgentTool(name="mytool", execute=_noop_execute)
    holder: list[Any] = [Extension(name="Held")]
    bench = _Bench(tools=[tool], extensions=holder, mode="print")

    before_active = list(bench.api.get_active_tools())
    before_all = [t.name for t in bench.api.get_all_tools()]

    first = await bench.call()
    second = await bench.call()

    assert first == second
    assert list(bench.api.get_active_tools()) == before_active
    assert [t.name for t in bench.api.get_all_tools()] == before_all
    assert [e.name for e in holder] == ["Held"]


async def test_invented_arguments_are_ignored_rather_than_honoured() -> None:
    """``validate_tool_arguments`` preserves unknown keys (``tools.py:317``).

    So a model that invents ``{"verbose": true}`` reaches ``execute`` with it.
    The tool has no input at all, which is what makes "read-only" checkable.
    """

    bench = _Bench(mode="print")
    plain = await bench.call()
    noisy = await bench.call({"verbose": True, "cwd": "/etc"})
    assert plain == noisy


async def test_the_hook_never_blocks_or_rewrites_another_tools_call() -> None:
    bench = _Bench()
    (handler,) = bench.record.handlers["tool_call"]
    event = ToolCallHookEvent(
        tool_call_id="c9", tool_name="bash", args={"command": "ls"}
    )
    assert await handler(event, bench.harness._make_context()) is None
    assert event.args == {"command": "ls"}
