"""ADR-0196 — ``--no-builtin-tools`` is honored (it was parsed-but-ignored).

The flag existed in ``args.py`` and reached ``parsed.no_builtin_tools``, but
nothing consumed it: ``_resolve_active_tools`` (``cli/entry.py:453-475``)
deliberately refuses to express it, because ``active_tool_names`` is seeded
BEFORE extensions register their tools — a filter written there would also
disable every extension and ``<server>__<tool>`` MCP tool. The faithful
expression is a POST-registration ``set_active_tools`` inside
``_harness_factory``, immediately after ``AgentHarness(opts)``.

That placement has four properties this file pins, three of which are the
failure modes an "obvious" implementation walks into:

1. built-ins drop, extension + MCP tools survive (the point of the flag);
2. ``--no-tools`` WINS — ``_action_set_active_tools`` is non-destructive
   (``core.py:3525-3534``), so ``state.tools`` still holds the full registry
   after ``--no-tools``, and an unguarded filter would RE-ENABLE every
   extension/MCP tool the user just killed;
3. ``--tools`` still intersects — the allowlist applies, it just cannot readmit
   a built-in;
4. the filter survives ``AgentSessionRuntime.reload()``, whose step 6
   (``agent_session_runtime.py:772-798``) restores
   ``(active_before ∩ current) ∪ (ext ∩ current)``.

Everything is driven through the REAL ``_async_main`` because the filter lives
inside a closure (``_harness_factory``) that only ``_async_main`` produces — a
unit test of ``_resolve_active_tools`` provably cannot see it. The run is
stopped at the first harness build (see :func:`_run_to_harness`), so no turn is
attempted and nothing touches the network.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.types import AgentTool
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.tools import ALL_TOOL_NAMES

_EXT_TOOL = "ext_probe"
_MCP_TOOL = "probe__ping"

_BUILT = 42
"""Sentinel exit code meaning "the run reached the first harness build".

Not a real ``_async_main`` return value: the spy raises out of
``create_agent_session_runtime`` (``cli/entry.py:1833``), which is AFTER the
factory has built and filtered the harness and BEFORE ``run_print_mode`` would
attempt a turn. Any other code means the run bailed earlier, which is itself the
assertion for the negative tests.
"""


class _StopAfterBuild(Exception):
    """Raised by the runtime spy once the harness exists — see :data:`_BUILT`."""


class _FakePipedStdin:
    """Non-tty stdin → ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A hermetic cwd + agent dir + settings file, and a fake MCP server.

    ``OPENROUTER_*`` are cleared because ``resolve_model``'s first branch is
    "OpenRouter-from-env", which would silently replace the model these tests
    name (and, on a developer machine with a real key, make the suite
    order-dependent).
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)

    # A single env-configured MCP server so ``mcp_contribs`` is non-empty and the
    # (faked) manager is constructed. ``$AELIX_MCP_CONFIG`` is never trust-gated,
    # which keeps this independent of the Project Trust verdict.
    mcp_config = tmp_path / "mcp.json"
    mcp_config.write_text('{"mcpServers": {"probe": {"command": "echo"}}}')
    monkeypatch.setenv("AELIX_MCP_CONFIG", str(mcp_config))

    class _FakeMcpManager:
        """Yields one connected tool without spawning a subprocess."""

        def __init__(self, contribs: Any) -> None:
            self.contribs = contribs

        async def connect_all(self) -> list[str]:
            return []

        async def collect_agent_tools(self) -> list[AgentTool]:
            return [AgentTool(name=_MCP_TOOL, description="fake mcp tool")]

        async def disconnect_all(self) -> None:
            return None

    monkeypatch.setattr(entry_mod, "McpClientManager", _FakeMcpManager)

    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _extension_file(tmp_path: Path) -> Path:
    """An explicit ``-e`` extension (tier 3) registering ONE tool.

    Tier 3 is ungated by both discovery guards, so this loads regardless of the
    Project Trust verdict — which keeps these tests about the tool filter and
    nothing else.
    """

    ext = tmp_path / "probe_ext.py"
    ext.write_text(
        textwrap.dedent(
            f"""
            from aelix_agent_core.types import AgentTool

            def setup(aelix):
                aelix.register_tool(
                    AgentTool(name={_EXT_TOOL!r}, description="probe tool")
                )
            """
        ).strip()
        + "\n"
    )
    return ext


async def _run_to_harness(
    argv: list[str],
    cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
) -> int:
    """Run ``_async_main`` in ``cwd``, stopping at the first harness build.

    The spy builds the REAL :class:`AgentSessionRuntime` first (so a test can
    drive ``reload()``), records both objects, then raises. Returns
    :data:`_BUILT` when the build was reached, or the real exit code when the
    run bailed before it.
    """

    monkeypatch.chdir(cwd)
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        runtime = await real_create(harness, factory, **kwargs)
        captured["harness"] = harness
        captured["runtime"] = runtime
        raise _StopAfterBuild

    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy)
    try:
        return await entry_mod._async_main(argv)
    except _StopAfterBuild:
        return _BUILT


def _base_argv(ext: Path) -> list[str]:
    return [
        "--no-session",
        "--print",
        "-e",
        str(ext),
        "--provider",
        "anthropic",
        "--model",
        "probe-model",
    ]


# === 1. Built-ins drop, extension + MCP tools stay ===========================


async def test_builtins_dropped_extension_and_mcp_kept(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [*_base_argv(ext), "--no-builtin-tools"],
        _isolated_env,
        monkeypatch,
        captured,
    )
    assert code == _BUILT

    harness = captured["harness"]
    active = harness.state.active_tool_names
    assert active is not None, "--no-builtin-tools must produce an explicit filter"
    assert ALL_TOOL_NAMES.isdisjoint(active)
    assert set(active) == {_EXT_TOOL, _MCP_TOOL}

    # The filter is NON-destructive by design (core.py:3525-3534): the built-ins
    # are still REGISTERED, they are merely inactive. Asserting this is what
    # distinguishes "filtered" from "the registry was rebuilt without them",
    # which is the shape reload() step 6 depends on.
    registered = {tool.name for tool in harness.state.tools}
    assert registered.issuperset(ALL_TOOL_NAMES)


async def test_without_the_flag_everything_stays_active(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control arm: no flag → no filter at all (``active_tool_names is None``).

    Without this, a filter that dropped built-ins unconditionally would pass
    every other test in this file.
    """

    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _base_argv(ext), _isolated_env, monkeypatch, captured
    )
    assert code == _BUILT
    assert captured["harness"].state.active_tool_names is None


# === 2. --no-tools wins ======================================================


async def test_no_tools_wins_over_no_builtin_tools(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-tools --no-builtin-tools`` ⇒ NOTHING active.

    The regression this pins is precise: ``_action_set_active_tools`` records a
    filter without dropping tools, so after ``--no-tools`` the registry is still
    full. An unguarded post-registration filter would compute
    "every non-built-in in state.tools" and hand the user back every extension
    and MCP tool they had just switched off — a kill switch that re-opens itself.
    """

    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [*_base_argv(ext), "--no-tools", "--no-builtin-tools"],
        _isolated_env,
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    assert captured["harness"].state.active_tool_names == []


# === 3. --tools intersects ===================================================


async def test_tools_intersection(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-builtin-tools --tools bash,<mcp>`` ⇒ only the MCP tool.

    Both halves apply: the allowlist still narrows, and it cannot readmit
    ``bash`` just because the user named it.
    """

    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [*_base_argv(ext), "--no-builtin-tools", "--tools", f"bash,{_MCP_TOOL}"],
        _isolated_env,
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    assert captured["harness"].state.active_tool_names == [_MCP_TOOL]


async def test_filter_never_raises_on_removed_tool(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``--tools`` name whose extension is GONE drops silently on reload.

    ``set_active_tools`` validates against ``state.tools`` and RAISES on an
    unknown name (``core.py:3527-3532``). On the reload path that raise would be
    unrecoverable — ``reload()`` has already disposed the old harness by then —
    which is exactly why the reloaded build is unfiltered (``on_reload=True``,
    ``cli/entry.py:787-799``). The ``--no-builtin-tools`` filter runs on that
    same rebuild, and it computes its names FROM the live ``state.tools`` before
    intersecting with ``parsed.tools``, so a since-removed name can only be
    dropped, never re-asserted.

    The scenario is the real one the guard was written for: an allowlist naming
    an extension tool, and the extension file deleted mid-session (the #53 moat
    in reverse). Both names are valid at FIRST build, so this is not a restatement
    of ``test_tools_intersection`` — it is the second build that matters.
    """

    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [
            *_base_argv(ext),
            "--no-builtin-tools",
            "--tools",
            f"{_EXT_TOOL},{_MCP_TOOL}",
        ],
        _isolated_env,
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    assert set(captured["harness"].state.active_tool_names) == {
        _EXT_TOOL,
        _MCP_TOOL,
    }

    runtime = captured["runtime"]
    try:
        # The extension vanishes; ``--tools`` still names its tool.
        ext.unlink()
        await runtime.reload()

        after = runtime.harness.state.active_tool_names
        assert after is not None
        assert _EXT_TOOL not in after  # dropped, not raised on
        assert _MCP_TOOL in after
        assert ALL_TOOL_NAMES.isdisjoint(after)
    finally:
        await runtime.dispose()


# === 4. Reload round-trip ====================================================


async def test_no_builtin_tools_survives_reload(
    _isolated_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the REAL ``AgentSessionRuntime.reload()``; no built-in reappears.

    Reload is the one path that could resurrect them: it rebuilds the harness
    ``on_reload=True`` (deliberately UNFILTERED, so the raising validator cannot
    brick a session whose ``--tools`` named a since-removed extension tool), then
    step 6 restores ``(active_before ∩ current) ∪ (ext_names ∩ current)``. The
    union arm is the risk — but built-ins are *app* tools passed through
    ``options.tools``, never extension tools, so ``ext_names`` cannot contain
    one. This asserts that end to end rather than by reading the code.
    """

    ext = _extension_file(tmp_path)
    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        [*_base_argv(ext), "--no-builtin-tools"],
        _isolated_env,
        monkeypatch,
        captured,
    )
    assert code == _BUILT

    runtime = captured["runtime"]
    try:
        before = list(captured["harness"].state.active_tool_names or [])
        assert ALL_TOOL_NAMES.isdisjoint(before)

        await runtime.reload()

        rebuilt = runtime.harness
        after = rebuilt.state.active_tool_names
        assert after is not None, "reload must not drop back to unfiltered"
        assert ALL_TOOL_NAMES.isdisjoint(after)
        # The extension tool survives (pi's ``includeAllExtensionTools: true``);
        # the MCP tool survives because its closure is shared across rebuilds.
        assert _EXT_TOOL in after
        assert set(before) <= set(after)
    finally:
        await runtime.dispose()
