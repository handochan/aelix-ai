"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — entry.py append-system-prompt wire.

Covers the §D wiring contract: ``parsed.append_system_prompt`` (the
:class:`list[str]` accumulator from ``args.py:135``) propagates into
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


async def test_cwd_agents_md_lands_after_the_users_own_append_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lock in the REAL default discovery path AND its position (#121).

    An ``AGENTS.md`` in the cwd is discovered by default (Pi
    ``--no-context-files`` gate, off by default) and appended AFTER the explicit
    ``--append-system-prompt`` chunks.

    That order is pi's (``system-prompt.ts``: base → ``appendSystemPrompt`` →
    project context → skills, in the ``customPrompt`` branch at ``:49-67`` and
    the default branch at ``:140-157`` alike). This test asserted the opposite
    until #121 / ADR-0217, which is the divergence that decision corrected: an
    ``AGENTS.md`` arrives with a ``git clone`` and is injected
    trust-independently by decision, so ranking it ahead of the chunk the user
    typed on their own command line put an untrusted repo's text above the
    user's own.
    """

    (tmp_path / "AGENTS.md").write_text("PROJECT_RULES", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    parsed = Args(append_system_prompt=["x"])
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(parsed, session)
    assert len(options.append_system_prompt) == 2
    assert options.append_system_prompt[0] == "x"
    assert "PROJECT_RULES" in options.append_system_prompt[1]
    # Pin the fence too, not merely the position: a revert of either half of
    # #121 is then caught by whichever of these tests runs first.
    assert options.append_system_prompt[1].startswith("<project_context>")
    assert options.append_system_prompt[1].endswith("</project_context>\n")
