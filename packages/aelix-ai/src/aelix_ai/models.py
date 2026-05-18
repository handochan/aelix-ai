"""Model-level helpers — Sprint 6b W6 (ADR-0050 §Carry-forward).

Pi parity: ``packages/ai/src/models.ts``. Today this module hosts the
:func:`clamp_thinking_level` helper (P-62 fix); future Pi model surface
extensions (``thinkingLevelMap`` field on :class:`aelix_ai.streaming.Model`,
the full ``adjustMaxTokensForThinking`` helper, etc.) land here as they
get ported per ADR-0050 §J.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from aelix_ai.streaming import Model


ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]


# Pi parity: ``clampThinkingLevel`` (``models.ts``). Pi reads
# ``model.thinkingLevelMap`` to pick the closest supported level (e.g.
# clamp ``xhigh`` → ``high`` when ``xhigh`` is unsupported). Sprint 6b
# ships the simple ``xhigh → high`` fallback because :class:`Model` does
# not yet carry ``thinking_level_map`` — full ``thinking_level_map``
# support is deferred to Sprint 6d per ADR-0050 §Carry-forward (P-65).
def clamp_thinking_level(
    model: Model, level: ThinkingLevel | str | None
) -> ThinkingLevel | None:
    """Clamp ``level`` against the model's supported thinking-level map.

    Args:
        model: target :class:`Model`. Today the parameter is accepted
            for forward-compat: when :attr:`Model.thinking_level_map`
            lands (Sprint 6d) this helper will read the per-level
            supported flags and clamp up to the next allowed slot.
        level: requested level. ``None`` returns ``None``; ``"xhigh"``
            clamps to ``"high"`` (Sprint 6b simple fallback).

    Returns:
        The clamped level, or ``None`` when no level was requested.
    """

    if level is None:
        return None
    if level == "xhigh":
        return "high"
    if level in ("minimal", "low", "medium", "high"):
        return level  # type: ignore[return-value]
    # Unknown spelling — surface as None rather than blindly forwarding
    # an invalid enum value to the wire.
    return None


__all__ = ["ThinkingLevel", "clamp_thinking_level"]
