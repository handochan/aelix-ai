"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — entry.py append-system-prompt wire.

Covers the §D wiring contract: ``parsed.append_system_prompt`` (the
:class:`list[str]` accumulator from ``args.py:101``) propagates into
:attr:`AgentHarnessOptions.append_system_prompt` via
:func:`_build_harness_options`.
"""

from __future__ import annotations

from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options


def test_append_system_prompt_propagates_into_options() -> None:
    parsed = Args(append_system_prompt=["x", "y"])
    session = Session(MemorySessionStorage())
    options = _build_harness_options(parsed, session)
    assert options.append_system_prompt == ["x", "y"]


def test_empty_append_system_prompt_yields_empty_list() -> None:
    parsed = Args()
    session = Session(MemorySessionStorage())
    options = _build_harness_options(parsed, session)
    assert options.append_system_prompt == []


def test_append_system_prompt_is_copied_not_aliased() -> None:
    """The harness options own a copy; later mutation of
    ``parsed.append_system_prompt`` MUST NOT leak into the options
    list (defensive copy in :func:`_build_harness_options`)."""

    parsed = Args(append_system_prompt=["x"])
    session = Session(MemorySessionStorage())
    options = _build_harness_options(parsed, session)
    parsed.append_system_prompt.append("y")
    assert options.append_system_prompt == ["x"]
