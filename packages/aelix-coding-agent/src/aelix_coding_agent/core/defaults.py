"""Pi parity: ``coding-agent/src/core/defaults.ts``.

Sprint 6g₁ (ADR-0067, P-201/P-202) ships:

- :data:`DEFAULT_THINKING_LEVEL` constant (Pi ``defaults.ts:1``)
- :func:`is_valid_thinking_level` predicate (Pi
  ``coding-agent/src/cli/args.ts::isValidThinkingLevel``)
"""

from __future__ import annotations

from aelix_ai.models import EXTENDED_THINKING_LEVELS

DEFAULT_THINKING_LEVEL: str = "medium"
"""Pi parity: ``defaults.ts`` ``DEFAULT_THINKING_LEVEL``.

Sprint 6g₂ W6 P-205 BLOCKING fix: byte-equivalent to Pi
``packages/coding-agent/src/core/defaults.ts:3`` at SHA 734e08e:
``export const DEFAULT_THINKING_LEVEL: ThinkingLevel = "medium";``.
The earlier Sprint 6g₁ port shipped ``"off"`` per the W1 spec §E
draft — verified at the pinned SHA, the actual Pi value is
``"medium"``. All 7 ``model_resolver.py`` consumers read the symbol
(no string literals), so flipping the constant here propagates.
"""


def is_valid_thinking_level(value: str) -> bool:
    """Pi parity: ``cli/args.ts::isValidThinkingLevel``.

    Returns ``True`` iff ``value`` is one of
    :data:`aelix_ai.models.EXTENDED_THINKING_LEVELS`.
    """

    return value in EXTENDED_THINKING_LEVELS


__all__ = ["DEFAULT_THINKING_LEVEL", "is_valid_thinking_level"]
