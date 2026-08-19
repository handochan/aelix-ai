"""ImplConsumers (ADR-0161) — pure row specs for the expanded ``/settings`` menu.

The interactive driver lives in :func:`aelix_coding_agent.tui.shell._open_settings`
(it loops ``context.select`` over these rows, applies the chosen change, and
flushes). These helpers are deliberately side-effect-free at *build* time and the
``apply`` dispatch is the ONLY place that mutates — both are unit-testable without
standing up the prompt-toolkit modal.

Two row kinds:

* **bool** — a checkbox-style toggle (``on``/``off``); :func:`apply_setting`
  flips it via the row's setter.
* **enum** — cycles through a fixed ordered tuple of literals (wraps);
  :func:`apply_setting` advances to the next value.
* **int** — a numeric input; the *caller* (shell) collects the new value via an
  input dialog and passes it; the setter clamps (the SettingsManager setters
  clamp ``autocomplete_max_visible`` to ``[3,20]`` + ``editor_padding_x`` to
  ``[0,3]``), then :func:`apply_setting` re-reads to surface the clamped value.
* **action** — delegated to a host flow (theme sub-select, model picker,
  thinking-level cycle); :func:`apply_setting` returns a sentinel so the shell
  runs the delegated coroutine.

The LIVE-effect rows (theme / default-model / steering / follow-up /
thinking-level / thinking-blocks / tool-card-max-lines) DUAL-WRITE: persist via
the SettingsManager AND apply to the live session (harness / renderer / context).
The shell owns the live half (it holds the harness/renderer/context); these
helpers own the persist half + the canonical cycle orderings + the human-readable
labels.

The remaining rows are PERSIST-ONLY, and TEN of them are outright INERT: the
value round-trips to ``settings.json`` and no production code ever reads it back
(re-measured 2026-08-18 for #84 — see the block comment above those rows).
Their help text says so rather than promising "applies next launch", which was
never true for them. ``features_agents``, ``tool_card_max_lines``,
``render_max_width`` and ``enable_skill_commands`` sit in the same block but ARE
wired; do not sweep them into a blanket rewrite. It was eleven until #115 wired
``enable_skill_commands`` and left its copy claiming otherwise for twelve days.

SKIPPED: ``markdown.code_block_indent`` — :class:`SettingsManager` exposes
``get_code_block_indent`` but NO setter, so a row would be dead/unsettable UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_ai.settings import SettingsManager


# Sentinel apply-results so the shell knows what (if anything) to do after a row
# is applied. ``APPLY_OK`` = persisted (commit a green line). ``APPLY_DELEGATE``
# = an ``action`` row whose live flow the shell must run (theme / model /
# thinking). The string payload of ``APPLY_OK`` is the human commit message.
@dataclass(frozen=True)
class ApplyResult:
    """The outcome of :func:`apply_setting` for one row."""

    kind: str  # "ok" | "delegate" | "error"
    message: str = ""
    #: For ``ok`` rows: a tuple ``(live_target, new_value)`` the shell uses to
    #: mirror the change onto the live session (``None`` for persist-only rows).
    live: tuple[str, Any] | None = None


@dataclass(frozen=True)
class SettingsRow:
    """One row in the ``/settings`` menu.

    :param key: stable identity (used by :func:`apply_setting` to dispatch).
    :param label: the left-column display label.
    :param kind: ``"bool"`` | ``"enum"`` | ``"int"`` | ``"action"``.
    :param read: ``(sm) -> str`` — the current value rendered for the row.
    :param help: one-line description shown in the select detail panel.
    :param live: ``True`` when the change also applies to the live session this
        run (the shell mirrors it); ``False`` = persist-only. Note that
        ``live=False`` does NOT imply "applies next launch" — for the ten
        inert rows (#111 B-11, #84) nothing reads the value at any point, and
        it also does not imply INERT: ``features_agents`` and
        ``enable_skill_commands`` are ``live=False`` and genuinely wired.
    :param choices: the ordered enum literals (``enum`` rows only).
    :param int_range: ``(lo, hi)`` advisory range shown in the prompt (``int``
        rows only; the SettingsManager setter is the authoritative clamp).
    """

    key: str
    label: str
    kind: str
    read: Callable[[SettingsManager], str]
    help: str = ""
    live: bool = False
    choices: tuple[str, ...] = field(default_factory=tuple)
    int_range: tuple[int, int] | None = None
    apply_note: str | None = None
    """Appended to the confirmation line when the change is not yet in effect.

    ROW-SCOPED rather than a special case on ``key``, so the next persist-only
    row that needs a restart inherits the mechanism instead of re-discovering it.

    It exists because ``live=False`` is invisible at the one moment it matters.
    The confirmation for a toggle reads ``agent delegation → on`` whether or not
    anything now behaves differently, and for ``features_agents`` nothing does:
    the flag is read once per process (``cli/entry.py::_agents_delegation_enabled``,
    called from ``_async_main``), and ``/reload`` re-runs the same harness factory
    over a closure variable that is already frozen — so the toggle looks like it
    worked, ``/agents run`` still refuses, and the user has been told twice that
    they enabled something they did not."""


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def build_settings_rows(sm: SettingsManager) -> list[SettingsRow]:
    """Build the ordered ``/settings`` rows for the live SettingsManager.

    The read closures call the existing typed getters (consuming the API — no new
    fields on the pinned Settings dataclass). Order matches appendix O of the
    roadmap: the live-effect rows first, then the persist-only block.
    """

    return [
        # --- LIVE-effect rows (dual-write: persist + apply this run) ----------
        SettingsRow(
            key="theme",
            label="Theme",
            kind="action",
            read=lambda s: s.get_theme() or "default",
            help="Color theme for the footer + chrome (applies live).",
            live=True,
        ),
        SettingsRow(
            key="default_model",
            label="Default model",
            kind="action",
            read=lambda s: s.get_default_model() or "(unset)",
            help="Default model + provider; opens the model picker (applies to this session).",
            live=True,
        ),
        SettingsRow(
            key="steering_mode",
            label="Steering mode",
            kind="enum",
            read=lambda s: s.get_steering_mode(),
            help="How mid-turn messages queue: one-at-a-time or all (applies live).",
            live=True,
            choices=("one-at-a-time", "all"),
        ),
        SettingsRow(
            key="follow_up_mode",
            label="Follow-up mode",
            kind="enum",
            read=lambda s: s.get_follow_up_mode(),
            help="How follow-up messages queue: one-at-a-time or all (applies live).",
            live=True,
            choices=("one-at-a-time", "all"),
        ),
        SettingsRow(
            key="thinking_level",
            label="Thinking level",
            kind="action",
            read=lambda s: s.get_default_thinking_level() or "off",
            help="Default reasoning effort; cycles the model's levels (applies to this session).",
            live=True,
        ),
        SettingsRow(
            key="hide_thinking_block",
            label="Thinking blocks",
            kind="bool",
            read=lambda s: "hidden" if s.get_hide_thinking_block() else "visible",
            help="Hide or show the model's thinking blocks in the transcript (applies live).",
            live=True,
        ),
        SettingsRow(
            key="hide_compaction_summary",
            label="Compaction summary",
            kind="bool",
            read=lambda s: "hidden" if s.get_hide_compaction_summary() else "visible",
            help="Hide or show the /compact summary in the transcript (applies live).",
            live=True,
        ),
        # --- PERSIST-ONLY rows (no live coding-agent consumer) ---------------
        #
        # #111 B-11 — HONESTY OF THE HELP TEXT. Most rows below are not merely
        # "not live": they have NO production consumer at all, so the value is
        # written to settings.json and then read by nobody, this launch or any
        # other. Their help text used to promise "Persisted; applies next
        # launch", which is a lie by omission. It now says the value is inert.
        #
        # Re-measured 2026-08-18 (#84) by grepping every getter AND its backing
        # field across ``packages/*/src`` and ``src/``, excluding the setting's
        # own definition (settings_manager.py, types.py) and this file. TEN of
        # the eleven #111 rows still return ZERO consumers.
        #
        # The five exceptions in this block, which are genuinely wired and whose
        # help text is therefore left alone:
        #   * ``features_agents``       -> cli/entry.py::_build_harness_options
        #   * ``tool_card_max_lines``   -> tui/shell.py -> render.py (live)
        #   * ``render_max_width``      -> tui/shell.py -> tui/width.py (live)
        #   * ``enable_skill_commands`` -> tui/shell.py -> cli/resource_commands.py
        #   * ``check_for_updates``      -> tui/shell.py::_start_update_check
        #
        # WHEN YOU WIRE ONE OF THESE UP, revert its help text in the same
        # commit. A row that works but claims to be inert is the same defect
        # pointing the other way — and that is not hypothetical: #115 wired
        # ``enable_skill_commands`` on 2026-08-12 and left this copy saying "no
        # /skill:<name> surface exists yet", with a test asserting the sentence
        # so the claim stayed GREEN while being false. Fixed under #84.
        SettingsRow(
            key="features_agents",
            label="Agent delegation",
            kind="bool",
            read=lambda s: _on_off(s.get_features_agents()),
            help="Allow this agent to delegate work to a subagent. Persisted; applies next launch.",
            apply_note="takes effect after you restart aelix",
            # DELIBERATELY NOT live (ADR-0197 §5.6): the flag is consumed once,
            # by ``cli/entry.py::_build_harness_options``, when the harness is
            # built — the ``agent`` tool and the delegation extension are either
            # loaded for this process or they are not. A ``live=True`` here would
            # promise a mid-session effect that nothing delivers, i.e. exactly the
            # inert-row failure #84 shipped 11 of.
            live=False,
        ),
        SettingsRow(
            key="check_for_updates",
            label="Check for updates",
            kind="bool",
            read=lambda s: _on_off(s.get_check_for_updates()),
            help=(
                "Look for a newer Aelix release at launch and print the upgrade "
                "command. Sends nothing about you — a plain GET of a public "
                "file. Persisted; applies next launch."
            ),
            apply_note="takes effect after you restart aelix",
            # Read once, by ``tui/shell.py::_start_update_check``, before the
            # banner paints. ``live=True`` would promise a mid-session effect
            # that nothing delivers — the #84 defect, pointing the usual way.
            live=False,
        ),
        SettingsRow(
            key="autocomplete_max_visible",
            label="Autocomplete max items",
            kind="int",
            read=lambda s: str(s.get_autocomplete_max_visible()),
            help=(
                "Max rows in the autocomplete menu (3-20). "
                "Saved but not yet wired — no effect in this build."
            ),
            int_range=(3, 20),
        ),
        SettingsRow(
            key="tool_card_max_lines",
            label="Tool card max lines",
            kind="int",
            read=lambda s: str(s.get_tool_card_max_lines()),
            help="Max lines shown in a tool-output card (3-40). Persisted; applies live (next render).",
            live=True,
            int_range=(3, 40),
        ),
        SettingsRow(
            key="render_max_width",
            label="Render max width",
            kind="int",
            read=lambda s: (
                str(s.get_render_max_width()) if s.get_render_max_width() is not None else "default"
            ),
            help=(
                "Ceiling on render width in columns (60-240; unset = 120). A "
                "CEILING, not a fixed width: the renderer uses min(terminal, "
                "this), so it narrows a wide terminal and does nothing on a "
                "narrow one. Setting 120 restores the unset behaviour. "
                "Persisted; applies live (next message)."
            ),
            live=True,
            int_range=(60, 240),
        ),
        SettingsRow(
            key="show_hardware_cursor",
            label="Show hardware cursor",
            kind="bool",
            read=lambda s: _on_off(s.get_show_hardware_cursor()),
            help=(
                "Use the terminal's hardware cursor. "
                "Saved but not yet wired — no effect in this build."
            ),
        ),
        SettingsRow(
            key="editor_padding_x",
            label="Editor padding",
            kind="int",
            read=lambda s: str(s.get_editor_padding_x()),
            help=(
                "Horizontal input padding (0-3). "
                "Saved but not yet wired — no effect in this build."
            ),
            int_range=(0, 3),
        ),
        SettingsRow(
            key="quiet_startup",
            label="Quiet startup",
            kind="bool",
            read=lambda s: _on_off(s.get_quiet_startup()),
            help=(
                "Suppress the startup banner. "
                "Saved but not yet wired — the banner always shows."
            ),
        ),
        SettingsRow(
            key="enable_skill_commands",
            label="Skill commands",
            kind="bool",
            read=lambda s: _on_off(s.get_enable_skill_commands()),
            # WIRED since #115 (871a6be, 2026-08-12): ``tui/shell.py`` passes
            # ``get_enable_skill_commands()`` into ``expand_resource_command``,
            # which returns ``None`` for a ``/skill:`` prefix when it is off —
            # so the command falls through to the unknown-command hint instead
            # of expanding. Shaped exactly like ``features_agents``: read once
            # per ``_input_loop``, hence ``live=False`` plus an ``apply_note``
            # rather than a live claim.
            #
            # CAREFUL, the note that outlived the inertness: it is THIS SETTING
            # the row speaks for, not skills. Skills load at startup
            # (``cli/entry.py`` ``load_skills`` -> ``harness.set_skills``) and
            # feed ``/skills``, the banner and rpc_mode's command list whatever
            # this flag says. Turning it off disables the ``/skill:<name>``
            # SURFACE only — wording that says otherwise would tell users
            # ``--skill`` and ``.aelix/skills`` do nothing, which is false.
            help=(
                "Enable /skill:<name> dynamic commands. Off disables that "
                "command surface only — skills still load and /skills still "
                "lists them. Persisted; applies next launch."
            ),
            apply_note="takes effect after you restart aelix",
        ),
        SettingsRow(
            key="double_escape_action",
            label="Double-escape action",
            kind="enum",
            read=lambda s: s.get_double_escape_action(),
            help=(
                "What a quick double-Esc does. "
                "Saved but not yet wired — no effect in this build."
            ),
            choices=("fork", "tree", "none"),
        ),
        SettingsRow(
            key="tree_filter_mode",
            label="Tree filter mode",
            kind="enum",
            read=lambda s: s.get_tree_filter_mode(),
            help=(
                "Default /tree filter. "
                "Saved but not yet wired — no effect in this build."
            ),
            choices=("default", "no-tools", "user-only", "labeled-only", "all"),
        ),
        SettingsRow(
            key="image_auto_resize",
            label="Auto-resize images",
            kind="bool",
            read=lambda s: _on_off(s.get_image_auto_resize()),
            help=(
                "Down-scale large pasted images. Saved but not yet wired — "
                "images are always auto-resized."
            ),
        ),
        SettingsRow(
            key="block_images",
            label="Block images",
            kind="bool",
            read=lambda s: _on_off(s.get_block_images()),
            help=(
                "Refuse image attachments entirely. "
                "Saved but not yet wired — images are never blocked."
            ),
        ),
        SettingsRow(
            key="show_terminal_progress",
            label="Terminal progress",
            kind="bool",
            read=lambda s: _on_off(s.get_show_terminal_progress()),
            help=(
                "Emit terminal progress (OSC) sequences. "
                "Saved but not yet wired — no effect in this build."
            ),
        ),
        SettingsRow(
            key="clear_on_shrink",
            label="Clear on shrink",
            kind="bool",
            read=lambda s: _on_off(s.get_clear_on_shrink()),
            help=(
                "Clear scrollback when the terminal shrinks. "
                "Saved but not yet wired — no effect in this build."
            ),
        ),
    ]


def _next_enum(current: str, choices: tuple[str, ...]) -> str:
    """The next value in ``choices`` after ``current`` (wraps; defensive on miss)."""

    if not choices:
        return current
    try:
        idx = choices.index(current)
    except ValueError:
        return choices[0]
    return choices[(idx + 1) % len(choices)]


def apply_setting(
    row: SettingsRow,
    sm: SettingsManager,
    *,
    int_value: int | None = None,
) -> ApplyResult:
    """Apply ``row``'s change to the SettingsManager (the PERSIST half).

    ``bool`` rows flip; ``enum`` rows cycle to the next value; ``int`` rows take
    ``int_value`` (the shell collected it via an input dialog) and let the setter
    clamp, then re-read; ``action`` rows return ``APPLY_DELEGATE`` so the shell
    runs the delegated live flow (theme / model / thinking). For ``ok`` rows the
    ``live`` field carries ``(key, new_value)`` so the shell can mirror onto the
    live session for the dual-write rows.

    Never raises: a setter blowing up returns an ``error`` ApplyResult so the
    shell commits a red line instead of crashing the REPL.
    """

    try:
        if row.kind == "action":
            # Delegated to a host flow (theme sub-select / model picker /
            # thinking-level cycle). The shell owns the live + persist halves.
            return ApplyResult(kind="delegate", message=row.key)

        if row.kind == "bool":
            current = _row_bool(row, sm)
            new = not current
            _set_bool(row.key, sm, new)
            shown = _bool_label(row, new)
            note = f" ({row.apply_note})" if row.apply_note else ""
            return ApplyResult(
                kind="ok",
                message=f"{row.label.lower()} → {shown}{note}",
                live=(row.key, new) if row.live else None,
            )

        if row.kind == "enum":
            current = row.read(sm)
            new = _next_enum(current, row.choices)
            _set_enum(row.key, sm, new)
            return ApplyResult(
                kind="ok",
                message=f"{row.label.lower()} → {new}",
                live=(row.key, new) if row.live else None,
            )

        if row.kind == "int":
            if int_value is None:
                return ApplyResult(
                    kind="error", message=f"{row.label}: no value provided"
                )
            _set_int(row.key, sm, int_value)
            # Re-read to surface the CLAMPED value (the setter clamps the range).
            clamped = row.read(sm)
            return ApplyResult(
                kind="ok",
                message=f"{row.label.lower()} → {clamped}",
                live=(row.key, clamped) if row.live else None,
            )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        return ApplyResult(kind="error", message=f"{row.label}: {exc}")

    return ApplyResult(kind="error", message=f"{row.label}: unknown row kind {row.kind!r}")


# --- per-key bool read/write (the boolean setters/getters keyed by row.key) ----

_BOOL_GETTERS: dict[str, str] = {
    "hide_thinking_block": "get_hide_thinking_block",
    "hide_compaction_summary": "get_hide_compaction_summary",
    "show_hardware_cursor": "get_show_hardware_cursor",
    "quiet_startup": "get_quiet_startup",
    "enable_skill_commands": "get_enable_skill_commands",
    "image_auto_resize": "get_image_auto_resize",
    "block_images": "get_block_images",
    "show_terminal_progress": "get_show_terminal_progress",
    "clear_on_shrink": "get_clear_on_shrink",
    # ADR-0197 (P2) — agent delegation. A bool row whose key is missing from
    # THIS table raises ``KeyError`` inside ``_row_bool`` on the first toggle,
    # which ``apply_setting`` swallows into a red line: a silently dead row.
    "features_agents": "get_features_agents",
}
_BOOL_SETTERS: dict[str, str] = {
    "hide_thinking_block": "set_hide_thinking_block",
    "hide_compaction_summary": "set_hide_compaction_summary",
    "show_hardware_cursor": "set_show_hardware_cursor",
    "quiet_startup": "set_quiet_startup",
    "enable_skill_commands": "set_enable_skill_commands",
    "image_auto_resize": "set_image_auto_resize",
    "block_images": "set_block_images",
    "show_terminal_progress": "set_show_terminal_progress",
    "clear_on_shrink": "set_clear_on_shrink",
    # ADR-0197 (P2) — see the getter note above; the setter half fails the same
    # way, one keystroke later.
    "features_agents": "set_features_agents",
}
_ENUM_SETTERS: dict[str, str] = {
    "steering_mode": "set_steering_mode",
    "follow_up_mode": "set_follow_up_mode",
    "double_escape_action": "set_double_escape_action",
    "tree_filter_mode": "set_tree_filter_mode",
}
_INT_SETTERS: dict[str, str] = {
    "autocomplete_max_visible": "set_autocomplete_max_visible",
    "editor_padding_x": "set_editor_padding_x",
    "tool_card_max_lines": "set_tool_card_max_lines",
    "render_max_width": "set_render_max_width",
}


def _row_bool(row: SettingsRow, sm: SettingsManager) -> bool:
    return bool(getattr(sm, _BOOL_GETTERS[row.key])())


def _set_bool(key: str, sm: SettingsManager, value: bool) -> None:
    getattr(sm, _BOOL_SETTERS[key])(value)


def _set_enum(key: str, sm: SettingsManager, value: str) -> None:
    getattr(sm, _ENUM_SETTERS[key])(value)


def _set_int(key: str, sm: SettingsManager, value: int) -> None:
    getattr(sm, _INT_SETTERS[key])(value)


def _bool_label(row: SettingsRow, value: bool) -> str:
    # ``hide_*`` rows read as hidden/visible; the rest as on/off.
    if row.key in ("hide_thinking_block", "hide_compaction_summary"):
        return "hidden" if value else "visible"
    return _on_off(value)


__all__ = [
    "ApplyResult",
    "SettingsRow",
    "apply_setting",
    "build_settings_rows",
]
