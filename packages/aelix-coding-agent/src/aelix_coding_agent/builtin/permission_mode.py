"""Permission posture engine (WP-0, ADR-0157) — pure, no prompt-toolkit.

The shift+tab-cycled posture that drives :class:`~aelix_coding_agent.builtin.
permission.PermissionExtension`. Modelled on Claude Code's permission modes
(default / auto-accept-edits / plan) plus a ``yolo`` skip-the-prompt mode and a
tree-sitter-classifier-driven ``auto`` mode (ADR-0158).

Naming is deliberately disambiguated from the harness ``steering_mode``
(``all`` / ``one-at-a-time``) so the two never collide: the permission concept
uses :class:`PermissionMode` / :class:`PermissionPosture` / ``posture`` in code,
and a distinct footer glyph set (``✎`` / ``⏸`` / ``⚠`` / ``🤖``) that never
reuses steering's ``⏵⏵``.

This module is pure (zero prompt-toolkit / Rich / asyncio dependency) so the
enum / cycle / metadata is unit-testable in isolation, mirroring the
``model_picker`` / ``thinking_picker`` purity convention.

SECURITY: the default posture is :data:`PermissionMode.DEFAULT` (always prompt
for mutating tools). Nothing here widens permissions on its own — it only
records the *intent*; the gate in ``permission.py`` enforces it (and the regex
``GuardrailExtension`` remains the non-bypassable first-block-wins floor).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class PermissionMode(StrEnum):
    """The active permission posture.

    A ``str`` enum so a value round-trips through logs / footers / tests as its
    plain name without ``.value`` ceremony.

    - :data:`DEFAULT` — prompt for every mutating tool (the safe default).
      "Mutating" is every tool that is not one of aelix's own read-only tools
      — MCP, extension and pack tools included (ADR-0253).
    - :data:`AUTO_ACCEPT` — auto-allow aelix's own *edits/writes*; STILL prompt
      for bash and for every tool aelix did not build (bash can do arbitrary
      damage, so "auto-accept" means edits only).
    - :data:`PLAN` — block ALL mutating tools (aelix's own read-only tools still
      allowed so the agent can investigate); the user exits with shift+tab.
    - :data:`YOLO` — skip the permission PROMPT for all mutating tools. The
      regex ``GuardrailExtension`` STILL hard-denies catastrophic patterns
      (``rm -rf`` etc.) because it runs first via the prepend order — YOLO
      bypasses the prompt, NOT the floor.
    - :data:`AUTO` — route bash through the tree-sitter classifier (ADR-0158):
      ALLOW → no prompt, ASK → prompt, DENY → block; aelix's own writes
      auto-allowed like AUTO_ACCEPT; a tool aelix did not build is neither
      classified nor auto-allowed, so it prompts. Falls back to DEFAULT
      semantics if the classifier is unavailable.
    """

    DEFAULT = "default"
    AUTO_ACCEPT = "auto-accept-edits"
    PLAN = "plan"
    YOLO = "yolo"
    AUTO = "auto"


# Sprint 6h₂₈ (ADR-0159) — the leading footer label shown for DEFAULT mode. The
# user wants the permission posture visible at the FRONT of the footer at ALL
# times (including DEFAULT), so the footer substitutes this neutral label when
# the live badge is empty. Kept SEPARATE from ``MODE_META[DEFAULT].badge_text``
# (which stays ``""`` — its empty value is a security-relevant contract relied on
# by ``PermissionPosture.badge()`` returning ``None`` and by the toast / command
# fallbacks ``badge_text or "default"``).
DEFAULT_BADGE = "● default"


# The shift+tab cycle order. ``AUTO`` is included (the tree-sitter classifier
# ships in the same sprint, ADR-0158); if the grammar is unavailable at runtime
# the AUTO branch fails safe to DEFAULT prompting (never silent-allow).
CYCLE_ORDER: tuple[PermissionMode, ...] = (
    PermissionMode.DEFAULT,
    PermissionMode.AUTO_ACCEPT,
    PermissionMode.PLAN,
    PermissionMode.YOLO,
    PermissionMode.AUTO,
)

# The AUTHORITY order, PLAN (tightest) → YOLO (loosest) — NOT the cycle above,
# which is a UX rotation and ranks PLAN above AUTO_ACCEPT. Declared here, beside
# the enum, as the one source: ``aelix_agents.posture`` (the delegation clamp,
# ADR-0199 §3.9) reads it, and a delegated child's own inbox refuses a widen by
# it (#123, ADR-0306 §6.3) without reaching into the extension.
AUTHORITY_RANK: dict[PermissionMode, int] = {
    PermissionMode.PLAN: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.AUTO_ACCEPT: 2,
    PermissionMode.AUTO: 3,
    PermissionMode.YOLO: 4,
}


def authority_rank(mode: PermissionMode) -> int:
    """Authority rank of ``mode`` — 0 (PLAN) to 4 (YOLO) — by :data:`AUTHORITY_RANK`."""

    return AUTHORITY_RANK[mode]


@dataclass(frozen=True)
class ModeMeta:
    """Display + gate metadata for a :class:`PermissionMode`."""

    badge_text: str
    badge_style: str
    description: str
    block_reason: str


# Per-mode metadata. DEFAULT has an EMPTY ``badge_text`` so the footer omits the
# segment entirely (parity with the steering segment's "no badge when default").
# Glyphs are distinct from steering's ⏵⏵ (✎/⏸/⚠/🤖).
PLAN_BLOCKED = (
    "Plan mode is active: file edits and shell commands are blocked so "
    "you can investigate and propose a plan first."
)
"""The first sentence of plan mode's refusal of a built-in edit or shell command."""

#: How plan mode is left, said to the model in the refusal, per host (#826). Only
#: the TUI has the key; a delegated child's print/json run, ``-p``/json and an
#: RPC/ACP host get the way that applies there (``cli/entry.py`` picks one with
#: :func:`plan_exit_for`).
PLAN_EXIT_TUI = "shift+tab to exit plan mode."
PLAN_EXIT_CHILD = (
    "You are a delegated agent and your parent set plan mode, which a delegated "
    "agent's run only ever tightens: give the plan in your answer."
)
PLAN_EXIT_PRINT = "This run was started in plan mode and stays in it: give the plan in your answer."
PLAN_EXIT_HOST = (
    "Plan mode can be left only by the application running aelix; until it is, "
    "give the plan in your answer."
)


def plan_exit_for(app_mode: str, *, inbox: bool) -> str:
    """The way out of plan mode a run's model is told (#826): ``app_mode`` is
    ``cli/entry.py``'s (``interactive``, ``print``, ``json``, ``rpc``; ACP drives
    ``rpc``), ``inbox`` whether this launch serves a delegation parent's command
    inbox (``--command-inbox``, ADR-0306).

    Chosen by the host that runs, not by ``AELIX_SUBAGENT_DEPTH``: an RPC or TUI
    process can inherit that marker (a ``bash`` command a delegated child ran
    starts one) and still has its own way out, as ``permissions/set`` or
    shift+tab change its mode there (Codex review of #826, M7). The child's
    sentence is for the print/json run whose inbox only ever tightens
    (``modes/print_inbox.py``); one with no inbox stays in the mode it started in.
    """

    if app_mode == "interactive":
        return PLAN_EXIT_TUI
    if app_mode == "rpc":
        return PLAN_EXIT_HOST
    return PLAN_EXIT_CHILD if inbox else PLAN_EXIT_PRINT


MODE_META: dict[PermissionMode, ModeMeta] = {
    PermissionMode.DEFAULT: ModeMeta(
        badge_text="",
        badge_style="",
        description=(
            "Default — prompt before each file edit, shell command, or MCP or extension tool."
        ),
        block_reason="",
    ),
    PermissionMode.AUTO_ACCEPT: ModeMeta(
        badge_text="✎ auto-edit",
        badge_style="yellow",
        description=(
            "Auto-accept edits — file edits/writes run without a prompt; "
            "shell commands and MCP or extension tools still prompt."
        ),
        block_reason="",
    ),
    PermissionMode.PLAN: ModeMeta(
        badge_text="⏸ plan",
        badge_style="cyan",
        description=(
            "Plan mode — read-only investigation; edits, shell commands and "
            "MCP or extension tools are blocked. shift+tab to exit plan mode."
        ),
        block_reason=f"{PLAN_BLOCKED} {PLAN_EXIT_TUI}",
    ),
    PermissionMode.YOLO: ModeMeta(
        badge_text="⚠ yolo",
        badge_style="bold red",
        description=(
            "Yolo — edits, shell commands, MCP and extension tools and delegated "
            "subagents run WITHOUT a prompt. Guardrail still blocks catastrophic "
            "patterns (rm -rf, fork-bomb, .env/.git writes)."
        ),
        block_reason="",
    ),
    PermissionMode.AUTO: ModeMeta(
        badge_text="🤖 auto",
        badge_style="green",
        description=(
            "Auto — shell commands are classified (safe→run, risky→prompt, "
            "dangerous→block); edits auto-run; MCP and extension tools still "
            "prompt. Falls back to prompting if the classifier is unavailable."
        ),
        block_reason="",
    ),
}


@dataclass
class PermissionPosture:
    """Mutable holder for the active :class:`PermissionMode`.

    ONE instance is built in ``cli/entry.py`` and threaded (by held reference)
    into both the :class:`PermissionExtension` and ``run_tui`` so a shift+tab
    cycle and the gate read/write the SAME object across ``/resume`` / ``/new``
    / ``/fork`` harness rebuilds (a security requirement — see ADR-0157).
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    listeners: list[Callable[[PermissionMode, PermissionMode], None]] = field(default_factory=list)
    """Called ``(previous, current)`` after EVERY mutation (#123, ADR-0306 §6.2;
    Codex H6): :meth:`set` and :meth:`cycle` alike, so shift+tab and an RPC
    ``permissions/set`` that is accepted both reach a listener. The
    agents extension subscribes once and tightens its live delegations. A
    listener that raises is dropped for that call, never the mutation."""

    def get(self) -> PermissionMode:
        return self.mode

    def set(self, mode: PermissionMode) -> None:
        previous, self.mode = self.mode, mode
        for listener in list(self.listeners):
            with contextlib.suppress(Exception):
                listener(previous, mode)

    def cycle(self) -> PermissionMode:
        """Advance to the next mode in :data:`CYCLE_ORDER` (wrapping) and return it.

        An off-cycle current value (should never happen) restarts at the first
        cycle entry — fail-safe toward DEFAULT rather than raising. Through
        :meth:`set` (#123): until then this assigned ``mode`` directly and no
        listener heard a shift+tab (Codex-measured: zero setter calls).
        """

        try:
            idx = CYCLE_ORDER.index(self.mode)
        except ValueError:
            self.set(CYCLE_ORDER[0])
            return self.mode
        self.set(CYCLE_ORDER[(idx + 1) % len(CYCLE_ORDER)])
        return self.mode

    def badge(self) -> str | None:
        """The footer badge text for the current mode, or ``None`` to omit it."""

        text = MODE_META[self.mode].badge_text
        return text or None


__all__ = [
    "AUTHORITY_RANK",
    "CYCLE_ORDER",
    "DEFAULT_BADGE",
    "MODE_META",
    "ModeMeta",
    "PermissionMode",
    "PermissionPosture",
    "authority_rank",
]
