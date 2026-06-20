"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — entry.py append-system-prompt wire.

Covers the §D wiring contract: ``parsed.append_system_prompt`` (the
:class:`list[str]` accumulator from ``args.py:101``) propagates into
:attr:`AgentHarnessOptions.append_system_prompt` via
:func:`_build_harness_options`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options


async def test_append_system_prompt_propagates_into_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Isolate from the real cwd so AGENTS.md auto-discovery (the REAL default
    # ``--no-context-files`` path) walks an empty tree and contributes nothing.
    monkeypatch.chdir(tmp_path)
    parsed = Args(append_system_prompt=["x", "y"])
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(parsed, session)
    assert options.append_system_prompt == ["x", "y"]


async def test_empty_append_system_prompt_yields_empty_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    parsed = Args()
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(parsed, session)
    assert options.append_system_prompt == []


async def test_append_system_prompt_is_copied_not_aliased(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness options own a copy; later mutation of
    ``parsed.append_system_prompt`` MUST NOT leak into the options
    list (defensive copy in :func:`_build_harness_options`)."""

    monkeypatch.chdir(tmp_path)
    parsed = Args(append_system_prompt=["x"])
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(parsed, session)
    parsed.append_system_prompt.append("y")
    assert options.append_system_prompt == ["x"]


async def test_cwd_agents_md_is_prepended_to_append_system_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lock in the REAL default discovery path: an ``AGENTS.md`` in the cwd is
    discovered and prepended ahead of the explicit ``--append-system-prompt``
    chunks (Pi ``--no-context-files`` gate, off by default)."""

    (tmp_path / "AGENTS.md").write_text("PROJECT_RULES", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    parsed = Args(append_system_prompt=["x"])
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(parsed, session)
    assert len(options.append_system_prompt) == 2
    assert "PROJECT_RULES" in options.append_system_prompt[0]
    assert options.append_system_prompt[1] == "x"
