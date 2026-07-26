"""System-prompt composition mirror (ADR-0196).

AELIX-ORIGINAL module wrapping ONE piece of mirrored kernel logic — the join
:meth:`AgentHarness.__init__` performs at ``aelix_agent_core/harness/core.py:571-578``.

Why mirror at all: ``/agents use`` swaps the active identity WITHOUT rebuilding
the harness (no reload — a reload resets the provider and would brick a live
stream). Applying the new prompt therefore means writing
``harness.state.system_prompt`` directly, which requires computing the exact
string the constructor WOULD have produced from the same base + appends. There
is no ``set_system_prompt`` on the kernel (``grep -rn "set_system_prompt"
packages/`` → zero hits) and the kernel must not be edited, so a mirror is the
only option.

Why it is safe: ``tests/agents/test_prompt_composition.py`` pins this function
against a REAL :class:`AgentHarness` over the base/appends permutation matrix.
If ``core.py`` ever changes its join, that test fails — the drift is caught by
construction rather than by a reader noticing.
"""

from __future__ import annotations

from collections.abc import Sequence


def compose_system_prompt(base: str, appends: Sequence[str]) -> str:
    """Mirror of ``AgentHarness.__init__``'s prompt join (``core.py:571-578``).

    THE ONLY mirrored kernel code in this package. Pinned by
    ``tests/agents/test_prompt_composition.py`` against a real ``AgentHarness``.

    Note the empty-base branch: the kernel does NOT emit a leading ``"\\n\\n"``
    when the base prompt is empty, so ``compose_system_prompt("", ["a"])`` is
    ``"a"`` and not ``"\\n\\na"``.
    """

    if not appends:
        return base
    appended = "\n\n".join(appends)
    return f"{base}\n\n{appended}" if base else appended


__all__ = ["compose_system_prompt"]
