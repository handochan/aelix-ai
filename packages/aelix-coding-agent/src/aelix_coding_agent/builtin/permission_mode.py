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

from dataclasses import dataclass
from enum import StrEnum


class PermissionMode(StrEnum):
    """The active permission posture.

    A ``str`` enum so a value round-trips through logs / footers / tests as its
    plain name without ``.value`` ceremony.

    - :data:`DEFAULT` — prompt for every mutating tool (the safe default).
    - :data:`AUTO_ACCEPT` — auto-allow *edits/writes*; STILL prompt for bash
      (bash can do arbitrary damage, so "auto-accept" means edits only).
    - :data:`PLAN` — block ALL mutating tools (read-only still allowed so the
      agent can investigate); the user exits with shift+tab.
    - :data:`YOLO` — skip the permission PROMPT for all mutating tools. The
      regex ``GuardrailExtension`` STILL hard-denies catastrophic patterns
      (``rm -rf`` etc.) because it runs first via the prepend order — YOLO
      bypasses the prompt, NOT the floor.
    - :data:`AUTO` — route bash through the tree-sitter classifier (ADR-0158):
      ALLOW → no prompt, ASK → prompt, DENY → block; writes auto-allowed like
      AUTO_ACCEPT. Falls back to DEFAULT semantics if the classifier is
      unavailable.
    """

    DEFAULT = "default"
    AUTO_ACCEPT = "auto-accept-edits"
    PLAN = "plan"
    YOLO = "yolo"
    AUTO = "auto"


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
MODE_META: dict[PermissionMode, ModeMeta] = {
    PermissionMode.DEFAULT: ModeMeta(
        badge_text="",
        badge_style="",
        description="Default — prompt before each file edit or shell command.",
        block_reason="",
    ),
    PermissionMode.AUTO_ACCEPT: ModeMeta(
        badge_text="✎ auto-edit",
        badge_style="yellow",
        description=(
            "Auto-accept edits — file edits/writes run without a prompt; "
            "shell commands still prompt."
        ),
        block_reason="",
    ),
    PermissionMode.PLAN: ModeMeta(
        badge_text="⏸ plan",
        badge_style="cyan",
        description=(
            "Plan mode — read-only investigation; all edits and shell "
            "commands are blocked. shift+tab to exit plan mode."
        ),
        block_reason=(
            "Plan mode is active: file edits and shell commands are blocked so "
            "you can investigate and propose a plan first. shift+tab to exit "
            "plan mode."
        ),
    ),
    PermissionMode.YOLO: ModeMeta(
        badge_text="⚠ yolo",
        badge_style="bold red",
        description=(
            "Yolo — edits and shell commands run WITHOUT a prompt. Guardrail "
            "still blocks catastrophic patterns (rm -rf, fork-bomb, .env/.git "
            "writes)."
        ),
        block_reason="",
    ),
    PermissionMode.AUTO: ModeMeta(
        badge_text="🤖 auto",
        badge_style="green",
        description=(
            "Auto — shell commands are classified (safe→run, risky→prompt, "
            "dangerous→block); edits auto-run. Falls back to prompting if the "
            "classifier is unavailable."
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

    def get(self) -> PermissionMode:
        return self.mode

    def set(self, mode: PermissionMode) -> None:
        self.mode = mode

    def cycle(self) -> PermissionMode:
        """Advance to the next mode in :data:`CYCLE_ORDER` (wrapping) and return it.

        An off-cycle current value (should never happen) restarts at the first
        cycle entry — fail-safe toward DEFAULT rather than raising.
        """

        try:
            idx = CYCLE_ORDER.index(self.mode)
        except ValueError:
            self.mode = CYCLE_ORDER[0]
            return self.mode
        self.mode = CYCLE_ORDER[(idx + 1) % len(CYCLE_ORDER)]
        return self.mode

    def badge(self) -> str | None:
        """The footer badge text for the current mode, or ``None`` to omit it."""

        text = MODE_META[self.mode].badge_text
        return text or None


__all__ = [
    "CYCLE_ORDER",
    "MODE_META",
    "ModeMeta",
    "PermissionMode",
    "PermissionPosture",
]
