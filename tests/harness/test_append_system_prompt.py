"""Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — append-system-prompt tests.

Covers the §D assembly contract: when
:attr:`AgentHarnessOptions.append_system_prompt` is non-empty, the
harness joins its elements with ``"\\n\\n"`` and appends after the base
system prompt onto :attr:`AgentHarness._state.system_prompt`.

Aelix-additive divergence (BINDING per ADR-0090): assembly happens
ONCE at init time (Pi rebuilds on every reload; 6h₇a has no reload
trigger in scope).
"""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions


def test_append_appends_after_base_with_double_newline() -> None:
    opts = AgentHarnessOptions(
        system_prompt="base", append_system_prompt=["x", "y"]
    )
    harness = AgentHarness(opts)
    assert harness._state.system_prompt == "base\n\nx\n\ny"


def test_append_empty_leaves_prompt_unchanged() -> None:
    opts = AgentHarnessOptions(system_prompt="base")
    harness = AgentHarness(opts)
    assert harness._state.system_prompt == "base"


def test_append_with_empty_base_drops_leading_separator() -> None:
    opts = AgentHarnessOptions(
        system_prompt="", append_system_prompt=["x", "y"]
    )
    harness = AgentHarness(opts)
    # No leading "\n\n" — appended chunks alone joined with "\n\n".
    assert harness._state.system_prompt == "x\n\ny"


def test_append_single_chunk_joined_after_base() -> None:
    opts = AgentHarnessOptions(
        system_prompt="base", append_system_prompt=["only"]
    )
    harness = AgentHarness(opts)
    assert harness._state.system_prompt == "base\n\nonly"


def test_append_default_factory_is_empty_list() -> None:
    """Two instances must not share the default list (mutable default
    safety via :func:`dataclasses.field`)."""

    opts_a = AgentHarnessOptions()
    opts_b = AgentHarnessOptions()
    assert opts_a.append_system_prompt == []
    assert opts_b.append_system_prompt == []
    opts_a.append_system_prompt.append("contamination-check")
    assert opts_b.append_system_prompt == []
