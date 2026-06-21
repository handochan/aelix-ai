"""WP-2 (ADR-0160) — the configurable footer segment registry.

Before this module the footer was an inline list in :meth:`AelixTUIContext.
_refresh_footer`; ADR-0159 hard-coded two rules into that list (the
permission-mode badge is the LEADING segment + the ⏵⏵ steering segment is hidden
at the "one-at-a-time" default). This module extracts each segment into a named
:class:`FooterSegment` whose ``produce`` closure reads the LIVE context state, so
the footer can be made user-configurable (the ``/statusline`` command) WITHOUT
moving the ADR-0159 invariants into the user-toggleable enabled-set.

The ADR-0159 rules live INSIDE the producers — not the enabled-set — on purpose:
an adversarial / empty statusline store can only hide a segment the user
explicitly unchecked; it can never make the security-visible permission badge
surface on a model with no provider, and the steering segment stays hidden at the
default regardless of whether the segment id is enabled. The default-enabled set
is byte-identical to the pre-ADR-0160 hard-coded order so an out-of-box footer is
unchanged.

Extension statuses (``footer.get_extension_statuses()``) are deliberately NOT
registry segments — they are appended after the registry loop by the caller and
are never user-toggleable (an extension owns its own status slot).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_coding_agent.tui.context import AelixTUIContext


@dataclass(frozen=True)
class FooterSegment:
    """A single named footer segment.

    :param id: the stable identifier persisted in the statusline store + shown in
        the ``/statusline`` checkbox picker (e.g. ``"permission-mode"``).
    :param label: the human label shown in the picker (e.g. ``"Permission mode"``).
    :param description: a one-line picker hint.
    :param produce: a no-arg closure (bound over the live context) returning the
        rendered segment string, or ``None`` to OMIT it. The ADR-0159 in-producer
        rules (badge leading + omit-when-no-provider; steering hidden at default)
        live here, independent of the enabled-set.
    :param default_enabled: whether the segment is rendered out-of-box (no
        statusline.json on disk). The default-ON set reproduces the pre-ADR-0160
        footer byte-for-byte.
    """

    id: str
    label: str
    description: str
    produce: Callable[[], str | None] = field(compare=False)
    default_enabled: bool = True


def build_footer_registry(ctx: AelixTUIContext) -> list[FooterSegment]:
    """Build the ordered footer-segment registry bound over ``ctx``.

    The order is canonical and matches the pre-ADR-0160 hard-coded footer for the
    default-ON segments: permission-mode (LEADING) → steering → pending-queued →
    current-dir → model → context-remaining → git-branch. Optional default-OFF
    segments (tokens / cost) read CACHED scalars (``set_usage_stats``) so the
    footer producer never awaits.
    """

    # Local import avoids a cycle (context imports this module).
    from aelix_coding_agent.tui.context import _DEFAULT_STEERING_MODE

    def _permission_mode() -> str | None:
        # ADR-0159: the LEADING segment, shown at ALL times when a posture is
        # wired (glyph badge for non-DEFAULT, neutral "● default" on DEFAULT);
        # OMITTED entirely when no provider/posture is wired (headless / tests).
        if ctx._permission_badge_provider is None:
            return None
        from aelix_coding_agent.builtin.permission_mode import DEFAULT_BADGE

        return ctx._permission_badge_provider() or DEFAULT_BADGE

    def _steering() -> str | None:
        # ADR-0159: HIDDEN at the "one-at-a-time" default (and the legacy
        # "default" string, which is NOT a real steering mode); surfaces only when
        # the user switched steering to "all".
        mode = (
            ctx._mode_provider() if ctx._mode_provider is not None else None
        ) or ctx._mode
        if mode and mode not in (_DEFAULT_STEERING_MODE, "default"):
            return f"⏵⏵ {mode}"
        return None

    def _pending() -> str | None:
        count = ctx._pending_provider() if ctx._pending_provider is not None else 0
        return f"⋯ {count} queued" if count > 0 else None

    def _current_dir() -> str | None:
        return f"📂 {ctx._abbrev_cwd(ctx._cwd)}" if ctx._cwd else None

    def _model() -> str | None:
        model = ctx._model_provider() if ctx._model_provider is not None else None
        return f"✱ {model}" if model else None

    def _context_remaining() -> str | None:
        return ctx._context_label

    def _git_branch() -> str | None:
        branch = ctx._footer.get_git_branch()
        return f"⎇ {branch}" if branch else None

    def _input_tokens() -> str | None:
        tokens = ctx._usage_input_tokens
        return f"↑ {tokens:,}" if tokens else None

    def _output_tokens() -> str | None:
        tokens = ctx._usage_output_tokens
        return f"↓ {tokens:,}" if tokens else None

    def _cost() -> str | None:
        cost = ctx._usage_cost
        return f"$ {cost:.4f}" if cost else None

    return [
        FooterSegment(
            "permission-mode",
            "Permission mode",
            "The active permission posture (✎/⏸/⚠/🤖 or ● default)",
            _permission_mode,
        ),
        FooterSegment(
            "steering",
            "Steering mode",
            "The ⏵⏵ steering segment (shown only when set to 'all')",
            _steering,
        ),
        FooterSegment(
            "pending-queued",
            "Queued messages",
            "How many steer/follow-up messages are queued this turn",
            _pending,
        ),
        FooterSegment(
            "current-dir",
            "Current directory",
            "The home-abbreviated working directory (📂)",
            _current_dir,
        ),
        FooterSegment(
            "model",
            "Model",
            "The active model id (✱)",
            _model,
        ),
        FooterSegment(
            "context-remaining",
            "Context usage",
            "The context-window usage meter (◔ 42% · 84K/200K)",
            _context_remaining,
        ),
        FooterSegment(
            "git-branch",
            "Git branch",
            "The current git branch (⎇)",
            _git_branch,
        ),
        FooterSegment(
            "input-tokens",
            "Input tokens",
            "Session input token total (↑)",
            _input_tokens,
            default_enabled=False,
        ),
        FooterSegment(
            "output-tokens",
            "Output tokens",
            "Session output token total (↓)",
            _output_tokens,
            default_enabled=False,
        ),
        FooterSegment(
            "cost",
            "Cost",
            "Session cost in USD ($)",
            _cost,
            default_enabled=False,
        ),
    ]


def default_enabled_ids(segments: list[FooterSegment]) -> list[str]:
    """The ids of the default-ON segments, in registry order."""

    return [s.id for s in segments if s.default_enabled]


# The canonical (id, label, description, default_enabled) registry SPEC,
# independent of any bound context. Used to derive the default-enabled ids + the
# /statusline option rows WITHOUT needing a live AelixTUIContext (the producers
# are the only ctx-dependent part). Kept in lockstep with build_footer_registry
# by the segment-registry tests.
_SEGMENT_SPEC: list[tuple[str, str, str, bool]] = [
    ("permission-mode", "Permission mode",
     "The active permission posture (✎/⏸/⚠/🤖 or ● default)", True),
    ("steering", "Steering mode",
     "The ⏵⏵ steering segment (shown only when set to 'all')", True),
    ("pending-queued", "Queued messages",
     "How many steer/follow-up messages are queued this turn", True),
    ("current-dir", "Current directory",
     "The home-abbreviated working directory (📂)", True),
    ("model", "Model", "The active model id (✱)", True),
    ("context-remaining", "Context usage",
     "The context-window usage meter (◔ 42% · 84K/200K)", True),
    ("git-branch", "Git branch", "The current git branch (⎇)", True),
    ("input-tokens", "Input tokens", "Session input token total (↑)", False),
    ("output-tokens", "Output tokens", "Session output token total (↓)", False),
    ("cost", "Cost", "Session cost in USD ($)", False),
]


def default_enabled_ids_from_spec() -> list[str]:
    """The default-ON segment ids in registry order, without a bound context.

    ``run_tui`` needs the default-enabled ids to seed the statusline store BEFORE
    the context (and thus the bound producer registry) exists; this reads the
    static spec so the seam stays clean. A test asserts it equals
    :func:`default_enabled_ids` over :func:`build_footer_registry`.
    """

    return [sid for sid, _label, _desc, on in _SEGMENT_SPEC if on]


__all__ = [
    "FooterSegment",
    "build_footer_registry",
    "default_enabled_ids",
    "default_enabled_ids_from_spec",
]
