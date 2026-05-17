"""Pi-parity ``compaction`` module (Sprint 4b / Phase 2.2.2 — ADR-0023).

Pi source: ``packages/agent/src/harness/compaction/compaction.ts`` at SHA
``734e08e``. Sprint 4b ships the data-shape parity (``CompactionPreparation``
+ ``CompactResult``) and a minimal :func:`prepare_compaction` /
:func:`compact` surface that the harness calls from :meth:`AgentHarness.compact`.

Per P-14 (W1 finding), Aelix does NOT add summarizer callbacks to
``AgentHarnessOptions``. Pi calls ``compact()`` inline using ``this.model`` +
``await this.getApiKeyAndHeaders(model)``; Aelix mirrors this. Sprint 4b
ships a thin LLM-driver placeholder that raises ``AgentHarnessError(
"invalid_state")`` when no auth is available and supports a test-only
``_summarizer_override`` callable injected via fixture (Aelix-additive,
documented).

The full Pi compaction pipeline (``estimateContextTokens``, ``findCutPoint``,
``generateSummary`` with file-ops accounting) is deferred to Phase 4 when the
provider adapter (ADR-0038) lands. Sprint 4b ships a Pi-shape preparation
result with the minimum fields needed for the emit + persist path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.streaming import Model

from aelix_agent_core.session.entries import SessionTreeEntry
from aelix_agent_core.types import AgentMessage

if TYPE_CHECKING:
    pass


# === Result dataclasses (Pi parity, ``compaction.ts:89-99 / 521-538``) ===


@dataclass(frozen=True)
class CompactionPreparation:
    """Pi ``CompactionPreparation`` (``compaction.ts:521-538``).

    Sprint 4b ships the four fields the harness needs to thread the emit +
    persist path (``first_kept_entry_id``, ``messages_to_summarize``,
    ``tokens_before``, ``settings``). The remaining Pi fields
    (``turn_prefix_messages``, ``is_split_turn``, ``previous_summary``,
    ``file_ops``) are added with sane defaults so future Phase 4 work can
    populate them without breaking the dataclass shape.
    """

    first_kept_entry_id: str
    messages_to_summarize: list[AgentMessage] = field(default_factory=list)
    turn_prefix_messages: list[AgentMessage] = field(default_factory=list)
    is_split_turn: bool = False
    tokens_before: int = 0
    previous_summary: str | None = None
    # ``file_ops`` and ``settings`` are Pi dicts/dataclasses — ship as Any
    # placeholders to keep the cross-runtime shape stable until Phase 4 wires
    # the real CompactionSettings + FileOperations ports.
    file_ops: Any | None = None
    settings: Any | None = None


@dataclass(frozen=True)
class CompactResult:
    """Pi ``CompactionResult`` (``compaction.ts:89-99``).

    The Pi field is named ``firstKeptEntryId``; Aelix uses snake_case
    ``first_kept_entry_id`` per ADR-0035 idiomatic-Python policy.
    """

    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: Any | None = None


# === Test-only seam (Aelix-additive, documented per ADR-0023) =============

# Sprint 4b ships a test-only override hook so tests can substitute a
# deterministic summarizer instead of standing up a real provider. The
# harness wires this through :class:`AgentHarnessOptions` only when a test
# explicitly opts in by setting ``_summarizer_override``. Documented as
# Aelix-additive divergence in ADR-0023 §"Aelix-additive divergences".
SummarizerOverride = Callable[
    [Model, "CompactionPreparation", str | None],
    "Awaitable[CompactResult] | CompactResult",
]


# === Public API (Pi parity, ``compaction.ts:541-606`` + ``:626-705``) =====


def prepare_compaction(
    path_entries: list[SessionTreeEntry],
    custom_instructions: str | None = None,
) -> CompactionPreparation | None:
    """Pi ``prepareCompaction`` (``compaction.ts:541-606``).

    Sprint 4b ships a minimal port — the heavy logic (``findCutPoint``,
    ``estimateContextTokens``, ``extractFileOperations``) lands when Phase 4
    provider adapter (ADR-0038) wires real token accounting. For now we
    return ``None`` when there is nothing to compact (mirroring Pi's empty /
    already-compacted short-circuits) and otherwise a preparation that names
    the **first** entry as the cut point. This keeps the emit + persist
    pipeline exercisable in unit tests without depending on a real LLM.

    The ``custom_instructions`` parameter is accepted to match Pi's
    Sprint 4b call site (``agent-harness.ts:706-708``) but not yet threaded
    into the preparation — the harness passes it independently into the
    summarizer call.
    """

    if not path_entries:
        return None
    if path_entries[-1].type == "compaction":
        # Already compacted to the tail.
        return None

    first_kept = path_entries[0]
    if not first_kept.id:
        return None
    # spec-deviation: full ``findCutPoint`` / ``estimateContextTokens`` port
    # deferred to Phase 4 (ADR-0038). Sprint 4b uses the head entry as the
    # cut point so the persist path can be exercised end-to-end.
    _ = custom_instructions  # accepted for Pi-parity signature
    return CompactionPreparation(
        first_kept_entry_id=first_kept.id,
        messages_to_summarize=[],
        tokens_before=0,
    )


async def compact(
    model: Model,
    get_api_key_and_headers: Callable[..., Any] | None,
    preparation: CompactionPreparation,
    custom_instructions: str | None = None,
    *,
    _summarizer_override: SummarizerOverride | None = None,
) -> CompactResult:
    """Pi ``compact`` (``compaction.ts:626-705``).

    Calls into the provider via ``get_api_key_and_headers`` per P-14 (no
    summarizer callback on :class:`AgentHarnessOptions`). Test fixtures can
    short-circuit the LLM call via ``_summarizer_override`` (Aelix-additive
    test-only seam documented in ADR-0023).

    Raises :class:`AgentHarnessError("invalid_state", ...)` when no override
    is supplied and ``get_api_key_and_headers`` is ``None``.
    """

    from aelix_agent_core.harness.core import AgentHarnessError

    if _summarizer_override is not None:
        raw = _summarizer_override(model, preparation, custom_instructions)
        if hasattr(raw, "__await__"):
            result = await raw  # type: ignore[misc]
        else:
            result = raw  # type: ignore[assignment]
        if not isinstance(result, CompactResult):
            raise AgentHarnessError(
                "invalid_state",
                "_summarizer_override must return CompactResult",
            )
        return result

    if get_api_key_and_headers is None:
        raise AgentHarnessError(
            "invalid_state",
            "compact requires options.get_api_key_and_headers (Phase 4 owner)",
        )

    # spec-deviation: Phase 4 provider adapter (ADR-0038) ports the real
    # ``generateSummary`` call. Sprint 4b raises a clear error so test
    # paths use ``_summarizer_override`` and production paths surface the
    # missing-provider state explicitly.
    raise AgentHarnessError(
        "invalid_state",
        "compact() LLM provider not yet implemented (Phase 4 / ADR-0038)",
    )


__all__ = [
    "CompactResult",
    "CompactionPreparation",
    "SummarizerOverride",
    "compact",
    "prepare_compaction",
]
