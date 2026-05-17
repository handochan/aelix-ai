"""Pi-parity ``branch_summarization`` module (Sprint 4b / Phase 2.2.2).

Pi source: ``packages/agent/src/harness/compaction/branch-summarization.ts``
at SHA ``734e08e``. Sprint 4b ships the data-shape parity needed by
:meth:`AgentHarness.navigate_tree` plus a minimum
:func:`collect_entries_for_branch_summary` / :func:`generate_branch_summary`
surface.

Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks. Pi calls
``generateBranchSummary()`` inline using ``this.model`` +
``await this.getApiKeyAndHeaders(model)``. Sprint 4b mirrors this and
defers the real LLM call to Phase 4 (ADR-0038) — the function raises
``AgentHarnessError("invalid_state")`` when no override is supplied and
``get_api_key_and_headers`` is ``None``. A test-only ``_summarizer_override``
seam (Aelix-additive) lets fixtures substitute a deterministic summarizer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_agent_core.session.entries import BranchSummaryEntry, SessionTreeEntry

if TYPE_CHECKING:
    from aelix_agent_core.session.session import Session


# === Result dataclasses (Pi parity, ``branch-summarization.ts:23-66``) ===


@dataclass(frozen=True)
class BranchSummaryPreparation:
    """Pi ``preparation`` object (``agent-harness.ts:771-780``).

    Pi's preparation is an inline object literal at the navigateTree call
    site; Aelix promotes it to a named dataclass so :meth:`Session.move_to`
    callers + hook handlers have a typed surface.
    """

    target_id: str
    old_leaf_id: str | None
    common_ancestor_id: str | None
    entries_to_summarize: list[SessionTreeEntry] = field(default_factory=list)
    user_wants_summary: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool | None = None
    label: str | None = None


# Pi exposes ``SummaryEntry`` as ``BranchSummaryEntry`` directly — the
# narrowing happens at the navigateTree return value (`entry?.type ===
# "branch_summary"`). Aelix mirrors that by re-exporting the entry type as a
# domain-specific alias used by the hook payload + result types.
SummaryEntry = BranchSummaryEntry


# === Test-only seam (Aelix-additive — ADR-0023 §"Aelix-additive divergences") ===

BranchSummarizerOverride = Callable[
    [Model, list[SessionTreeEntry], str | None],
    "Awaitable[str] | str",
]


# === Public API (Pi parity, ``branch-summarization.ts:69-98 / 199-262``) ==


async def collect_entries_for_branch_summary(
    session: Session,
    old_leaf_id: str | None,
    target_id: str,
) -> tuple[list[SessionTreeEntry], str | None]:
    """Pi ``collectEntriesForBranchSummary`` (``branch-summarization.ts:69-98``).

    Returns ``(entries_in_chronological_order, common_ancestor_id)``. The
    Pi return value is a dict; Aelix returns a tuple to keep the call site
    in :meth:`AgentHarness.navigate_tree` ergonomic.
    """

    from aelix_agent_core.session.storage import SessionError

    if not old_leaf_id:
        return [], None
    old_branch = await session.get_branch(old_leaf_id)
    old_path = {e.id for e in old_branch}
    target_path = await session.get_branch(target_id)
    common_ancestor_id: str | None = None
    for entry in reversed(target_path):
        if entry.id in old_path:
            common_ancestor_id = entry.id
            break
    entries: list[SessionTreeEntry] = []
    current: str | None = old_leaf_id
    while current and current != common_ancestor_id:
        entry = await session.get_entry(current)
        if entry is None:
            raise SessionError("invalid_session", f"Entry {current} not found")
        entries.append(entry)
        current = entry.parent_id
    entries.reverse()
    return entries, common_ancestor_id


async def generate_branch_summary(
    model: Model,
    get_api_key_and_headers: Callable[..., Any] | None,
    entries: list[SessionTreeEntry],
    custom_instructions: str | None = None,
    *,
    _summarizer_override: BranchSummarizerOverride | None = None,
) -> str:
    """Pi ``generateBranchSummary`` (``branch-summarization.ts:199-262``).

    Sprint 4b ships a Pi-shape signature that raises
    :class:`AgentHarnessError("invalid_state")` when no override or auth is
    available. Phase 4 provider adapter (ADR-0038) wires the real
    ``completeSimple`` call.
    """

    from aelix_agent_core.harness.core import AgentHarnessError

    if _summarizer_override is not None:
        raw = _summarizer_override(model, entries, custom_instructions)
        if hasattr(raw, "__await__"):
            summary = await raw  # type: ignore[misc]
        else:
            summary = raw  # type: ignore[assignment]
        if not isinstance(summary, str):
            raise AgentHarnessError(
                "invalid_state",
                "_summarizer_override must return str",
            )
        return summary

    if get_api_key_and_headers is None:
        raise AgentHarnessError(
            "invalid_state",
            "generate_branch_summary requires options.get_api_key_and_headers "
            "(Phase 4 owner)",
        )

    # spec-deviation: Phase 4 provider adapter (ADR-0038) ports the real
    # ``completeSimple`` invocation. Sprint 4b raises a clear error so test
    # paths use ``_summarizer_override``.
    raise AgentHarnessError(
        "invalid_state",
        "generate_branch_summary() LLM provider not yet implemented "
        "(Phase 4 / ADR-0038)",
    )


__all__ = [
    "BranchSummarizerOverride",
    "BranchSummaryPreparation",
    "SummaryEntry",
    "collect_entries_for_branch_summary",
    "generate_branch_summary",
]
