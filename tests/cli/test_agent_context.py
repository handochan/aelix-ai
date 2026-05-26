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


def test_build_harness_options_wires_seven_tools() -> None:
    options = _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert {t.name for t in options.tools} == _TOOL_NAMES


def test_build_harness_options_sets_coding_agent_system_prompt() -> None:
    options = _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert options.system_prompt  # non-empty (was "" → no identity)
    assert "Aelix" in options.system_prompt


def test_build_harness_options_explicit_system_prompt_overrides() -> None:
    parsed = Args(system_prompt="CUSTOM PROMPT")
    options = _build_harness_options(parsed, Session(MemorySessionStorage()))
    assert options.system_prompt == "CUSTOM PROMPT"  # --system-prompt wins
