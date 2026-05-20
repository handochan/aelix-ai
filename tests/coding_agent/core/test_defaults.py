"""Sprint 6g₁ (ADR-0067 P-201/P-202): coding-agent core defaults tests."""

from __future__ import annotations

from aelix_coding_agent.core.defaults import (
    DEFAULT_THINKING_LEVEL,
    is_valid_thinking_level,
)


def test_default_thinking_level_is_medium() -> None:
    """Pi parity: ``defaults.ts`` DEFAULT_THINKING_LEVEL.

    Sprint 6g₂ W6 P-205 BLOCKING fix: Pi at SHA 734e08e exports
    ``DEFAULT_THINKING_LEVEL: ThinkingLevel = "medium"``. The earlier
    Sprint 6g₁ port shipped ``"off"`` per the W1 spec §E draft, which
    contradicted the actual upstream value verified at the pinned SHA.
    """

    assert DEFAULT_THINKING_LEVEL == "medium"


def test_default_thinking_level_matches_pi_exactly() -> None:
    """Sprint 6g₂ W6 P-205 closure pin: byte-equivalent to Pi.

    The Pi reference at ``earendil-works/pi@734e08e`` exports
    ``DEFAULT_THINKING_LEVEL: ThinkingLevel = "medium"`` in
    ``packages/coding-agent/src/core/defaults.ts:3``. This regression
    guards against future Pi-parity drift on this single-token export.
    """

    assert DEFAULT_THINKING_LEVEL == "medium"
    # Both the literal string and the EXTENDED_THINKING_LEVELS membership
    # check (the `is_valid_thinking_level` consumer) must agree.
    assert is_valid_thinking_level(DEFAULT_THINKING_LEVEL)


def test_is_valid_thinking_level_accepts_all_six_levels() -> None:
    """Pi parity: ``cli/args.ts::isValidThinkingLevel`` returns True for
    every member of EXTENDED_THINKING_LEVELS (off/minimal/low/medium/high/xhigh).
    """

    for level in ("off", "minimal", "low", "medium", "high", "xhigh"):
        assert is_valid_thinking_level(level), level


def test_is_valid_thinking_level_rejects_invalid_strings() -> None:
    """Pi parity: any string not in EXTENDED_THINKING_LEVELS returns False."""

    for bogus in ("super", "extreme", ""):
        assert not is_valid_thinking_level(bogus), bogus
