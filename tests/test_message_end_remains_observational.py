"""P-3 pin tests — ``message_end`` is observational in Sprint 3b (Pi parity).

Sprint 3b §0 verdict (Option B): Pi has no ``MessageEndResult`` and no
replacement reducer. ADR-0018 → Deprecated. These tests pin the decision
so any future attempt to add a replacement reducer flags loudly.
"""

from __future__ import annotations

from aelix_agent_core.harness.hooks import (
    _REDUCERS,
    HOOK_RESULT_TYPES,
    _reducer_observational,
)


def test_message_end_result_type_is_none() -> None:
    """``HOOK_RESULT_TYPES['message_end']`` must remain ``None``.

    Adding a ``MessageEndResult`` here would silently enable replacement
    semantics across the codebase — that requires its own ADR and a Pi
    upstream change first.
    """

    assert HOOK_RESULT_TYPES["message_end"] is None


def test_message_end_reducer_is_observational() -> None:
    """``_REDUCERS['message_end']`` must remain ``_reducer_observational``."""

    assert _REDUCERS["message_end"] is _reducer_observational
