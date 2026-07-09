"""Sprint 6h₁₁ — coding-agent system prompt + AGENTS.md context + tool wiring.

Covers the fix for the "bare chat model" gap: the interactive/print/rpc harness
now ships the 7 coding tools + a real coding-agent system prompt, and
auto-discovers AGENTS.md project context.
"""

from __future__ import annotations

from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli.agent_context import (
    build_system_prompt,
    discover_context_files,
)
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options

_TOOL_NAMES = {"read", "bash", "edit", "write", "grep", "find", "ls"}


# --- system prompt -----------------------------------------------------------


def test_build_system_prompt_has_identity_and_tools() -> None:
    prompt = build_system_prompt(".")
    assert "Aelix" in prompt  # identity (was empty → generic chatbot)
    assert "coding agent" in prompt.lower()
    for tool in ("read", "write", "edit", "bash", "grep", "find", "ls"):
        assert tool in prompt  # the toolset is described


def test_build_system_prompt_includes_environment(tmp_path) -> None:
    prompt = build_system_prompt(str(tmp_path))
    assert str(tmp_path) in prompt  # absolute cwd surfaced
    assert "Working directory" in prompt


def test_build_system_prompt_has_convergence_guidance() -> None:
    """Weak models loop on vague requests without explicit stop-when-done /
    no-repeat guidance (pi's default prompt lacks it; authored here)."""
    prompt = build_system_prompt(".")
    assert "STOP calling tools" in prompt
    assert "same tool with the same arguments twice" in prompt
    assert "ambiguous" in prompt


# --- AGENTS.md discovery -----------------------------------------------------


def test_discover_context_files_finds_agents_md(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use tabs. Run `make test`.\n", encoding="utf-8")
    context = discover_context_files(str(tmp_path))
    assert "Use tabs" in context
    assert "AGENTS.md" in context  # labeled with the source path


def test_discover_context_files_walks_up_tree(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    sub = tmp_path / "pkg" / "deep"
    sub.mkdir(parents=True)
    context = discover_context_files(str(sub))
    assert "root rules" in context  # parent AGENTS.md discovered from a child cwd


def test_discover_context_files_none_returns_empty(tmp_path) -> None:
    sub = tmp_path / "empty"
    sub.mkdir()
    # No AGENTS.md in `sub`; parents (tmp_path) also have none.
    assert discover_context_files(str(sub)) == ""


def test_discover_context_files_skips_binary(tmp_path) -> None:
    # A non-UTF-8 AGENTS.md must be skipped (UnicodeDecodeError is a ValueError,
    # not an OSError) — it must NOT crash CLI startup.
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe\x00\x80not utf-8")
    assert discover_context_files(str(tmp_path)) == ""


def test_discover_context_files_truncates_oversized(tmp_path) -> None:
    from aelix_coding_agent.cli.agent_context import _MAX_CONTEXT_BYTES

    (tmp_path / "AGENTS.md").write_text("A" * (_MAX_CONTEXT_BYTES * 2), encoding="utf-8")
    context = discover_context_files(str(tmp_path))
    assert context  # truncated, NOT silently dropped
    assert len(context.encode("utf-8")) <= _MAX_CONTEXT_BYTES


# --- _build_harness_options wiring -------------------------------------------


async def test_build_harness_options_wires_seven_tools() -> None:
    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert {t.name for t in options.tools} == _TOOL_NAMES


async def test_build_harness_options_sets_coding_agent_system_prompt() -> None:
    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert options.system_prompt  # non-empty (was "" → no identity)
    assert "Aelix" in options.system_prompt


async def test_build_harness_options_explicit_system_prompt_overrides() -> None:
    parsed = Args(system_prompt="CUSTOM PROMPT")
    options = await _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt == "CUSTOM PROMPT"  # --system-prompt wins


# --- cli flag wiring: --tools / --no-tools (active_tool_names) ---------------


def test_resolve_active_tools() -> None:
    from aelix_coding_agent.cli.entry import _resolve_active_tools

    assert _resolve_active_tools(Args(no_tools=True)) == []
    assert _resolve_active_tools(Args(tools=["read", "grep"])) == ["read", "grep"]
    assert _resolve_active_tools(Args()) is None


async def test_build_harness_options_wires_active_tool_names() -> None:
    """--tools / --no-tools flow into AgentHarnessOptions.active_tool_names."""

    opts_all = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts_all.active_tool_names is None  # default: all tools active

    opts_allow = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage())
    )
    assert opts_allow.active_tool_names == ["read"]

    opts_none = await _build_harness_options(
        Args(no_tools=True), Session(MemorySessionStorage())
    )
    assert opts_none.active_tool_names == []


async def test_build_harness_options_drops_tools_filter_on_reload() -> None:
    """#24-FU (adversarial-review MEDIUM): on reload the factory must build
    UNFILTERED (active_tool_names=None) instead of re-applying the launch --tools
    filter through the harness's RAISING validator — otherwise a --tools-named
    extension tool whose extension was since removed would raise inside _apply and
    brick the session. reload() step-6 restores the live filter instead."""

    opts_reload = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage()), on_reload=True
    )
    assert opts_reload.active_tool_names is None  # filter deferred to reload step-6

    # Non-reload rebuilds (/new, /fork, /resume, first build) still apply --tools.
    opts_build = await _build_harness_options(
        Args(tools=["read"]), Session(MemorySessionStorage()), on_reload=False
    )
    assert opts_build.active_tool_names == ["read"]


async def test_build_harness_options_appends_mcp_tools() -> None:
    """MCP tools passed to _build_harness_options join the harness toolset."""

    base = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    builtin_count = len(base.tools)

    sentinel = next(iter(base.tools))  # reuse a real AgentTool as the "mcp" tool
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), mcp_tools=[sentinel]
    )
    assert len(opts.tools) == builtin_count + 1


async def test_build_harness_options_trusted_loads_on_disk_extension(
    tmp_path, monkeypatch, capsys
) -> None:
    """Sprint P0 #10: a TRUSTED project loads its on-disk extension and the
    old post-hoc security warning is GONE (replaced by the trust gate)."""

    # Isolate discovery: cwd = tmp project with one project-local extension;
    # global agent dir → empty tmp so no real ~/.aelix extensions leak in.
    (tmp_path / ".aelix" / "extensions").mkdir(parents=True)
    (tmp_path / ".aelix" / "extensions" / "probe.py").write_text(
        'def setup(aelix):\n    aelix.register_flag("probe_flag", type="bool", default=True)\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), project_trusted=True
    )
    err = capsys.readouterr().err
    # The old cosmetic warning was removed in favor of the real gate.
    assert "full system permissions" not in err
    # built-ins (2) + the discovered probe (1)
    assert len(opts.extensions) == 3


async def test_build_harness_options_untrusted_suppresses_on_disk_extension(
    tmp_path, monkeypatch, capsys
) -> None:
    """Sprint P0 #10: an UNTRUSTED project drops its project-local on-disk
    extension (``no_project_local``) — only the 2 built-ins load."""

    (tmp_path / ".aelix" / "extensions").mkdir(parents=True)
    (tmp_path / ".aelix" / "extensions" / "probe.py").write_text(
        'def setup(aelix):\n    aelix.register_flag("probe_flag", type="bool", default=True)\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), project_trusted=False
    )
    # The project-local probe was NOT loaded; only Guardrail + Permission.
    assert len(opts.extensions) == 2


async def test_build_harness_options_no_warning_without_on_disk(
    tmp_path, monkeypatch, capsys
) -> None:
    """No on-disk extensions → no security warning (only the 2 built-ins load)."""

    monkeypatch.chdir(tmp_path)  # empty project, no .aelix/extensions
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "empty_agent"))

    opts = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    err = capsys.readouterr().err
    assert "full system permissions" not in err
    assert len(opts.extensions) == 2


# --- issue #44: settings_manager harness seam wiring -------------------------


async def test_build_harness_options_threads_settings_manager() -> None:
    """Issue #44: a passed SettingsManager reaches AgentHarnessOptions.settings_manager
    — the dormant enabler that makes harness.reload() stop raising invalid_state in
    production. The aelix-agent-core seam (field/property/reload) already exists; this
    asserts the coding-agent glue forwards the instance."""
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory()
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=sm
    )
    assert opts.settings_manager is sm


async def test_build_harness_options_settings_manager_defaults_none() -> None:
    """Issue #44: omitting settings_manager preserves the pre-#44 default (None),
    so no caller is forced to thread it and existing behavior is unchanged."""
    opts = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts.settings_manager is None


# --- steering / follow-up mode seed from persisted settings ------------------


async def test_build_harness_options_seeds_steering_and_follow_up() -> None:
    """A persisted /settings steering / follow-up change must SURVIVE restart:
    the harness options are seeded from the SettingsManager (they had get/set
    pairs but no startup consumer, so the harness always booted the default and
    the persisted value silently reverted on every relaunch / /new / /fork)."""
    from aelix_ai.settings import SettingsManager

    sm = SettingsManager.in_memory({"steeringMode": "all", "followUpMode": "all"})
    opts = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=sm
    )
    assert opts.steering_mode == "all"
    assert opts.follow_up_mode == "all"


async def test_build_harness_options_steering_defaults_one_at_a_time() -> None:
    """No SettingsManager (or unset) → the pi-parity default "one-at-a-time",
    matching the AgentHarnessOptions dataclass default (no behaviour change)."""
    from aelix_ai.settings import SettingsManager

    opts_none = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert opts_none.steering_mode == "one-at-a-time"
    assert opts_none.follow_up_mode == "one-at-a-time"

    opts_unset = await _build_harness_options(
        Args(), Session(MemorySessionStorage()), settings_manager=SettingsManager.in_memory({})
    )
    assert opts_unset.steering_mode == "one-at-a-time"
    assert opts_unset.follow_up_mode == "one-at-a-time"
