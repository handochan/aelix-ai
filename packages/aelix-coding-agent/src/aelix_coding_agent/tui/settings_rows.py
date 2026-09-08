"""ImplConsumers (ADR-0161) — pure row specs for the expanded ``/settings`` menu.

The interactive driver lives in :func:`aelix_coding_agent.tui.shell._open_settings`
(it loops ``context.select`` over these rows, applies the chosen change, and
flushes). These helpers are deliberately side-effect-free at *build* time and the
``apply`` dispatch is the ONLY place that mutates — both are unit-testable without
standing up the prompt-toolkit modal.

Two row kinds:

* **bool** — a checkbox-style toggle (``on``/``off``); :func:`apply_setting`
  flips it via the row's setter, then re-reads (like the ``int`` arm below): the
  setters write the GLOBAL cell and most getters read the MERGED view, so a
  project ``.aelix/settings.json`` pin makes the write invisible — ``error``
  for a persist-only row, ``ok`` plus a session-only message for a PUSH row,
  whose mirror does not go through the getter.
* **enum** — cycles through a fixed ordered tuple of literals (wraps);
  :func:`apply_setting` advances to the next value.
* **int** — a numeric input; the *caller* (shell) collects the new value via an
  input dialog and passes it; the setter clamps (the SettingsManager setters
  clamp ``autocomplete_max_visible`` to ``[3,20]`` + ``editor_padding_x`` to
  ``[0,3]``), then :func:`apply_setting` re-reads to surface the clamped value.
* **action** — delegated to a host flow (theme sub-select, model picker,
  thinking-level cycle); :func:`apply_setting` returns a sentinel so the shell
  runs the delegated coroutine.

The LIVE-effect rows persist AND take effect this run, by one of TWO mechanisms.
Which one a row uses decides whether ``shell._apply_live_setting`` needs a branch
for it, so the split is machine-checked (tests/tui/test_settings_rows.py). The
prose list this replaced named seven rows while nine were ``live=True``, for as
long as nothing derived it from source.

PUSH — dual-write: ``apply_setting`` persists via the SettingsManager and hands
back ``ApplyResult.live``; the shell writes the second copy onto the live session
(``_apply_live_setting`` for the bool/enum/int rows, the delegated host flow for
the ``action`` rows). The shell owns that half because it holds the
harness/renderer/context.
  PUSH keys: theme, default_model, steering_mode, follow_up_mode, thinking_level,
  hide_thinking_block, hide_compaction_summary, tool_card_max_lines, render_max_width

PULL — nothing is mirrored: the consumer holds a callable onto the getter and
re-reads it on every use, so the persist half IS the live half.
``_apply_live_setting`` carries an explicit pass-only branch so the absence of a
mirror reads as a decision, not an omission.
  PULL keys: respect_gitignore, enable_skill_commands

These helpers own the persist half + the canonical cycle orderings + the
human-readable labels under both mechanisms.

The remaining rows are PERSIST-ONLY, and TEN of them are outright INERT: the
value round-trips to ``settings.json`` and no production code ever reads it back
(re-measured 2026-08-18 for #84 — see the block comment above those rows).
Their help text says so rather than promising "applies next launch", which was
never true for them. Some rows in that block ARE wired, some of those LIVE; the
block comment above them is the ONE list, so do not sweep the block into a
blanket rewrite. (This paragraph used to carry a second copy of that list and
went stale, omitting ``check_for_updates`` — nothing derived it, because the
guard in tests/tui/test_settings_rows.py splits the source on the block
comment's own wording. #244 deleted the copy.) It was eleven inert until #115
wired ``enable_skill_commands`` and left its copy claiming otherwise for twelve
days.

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
    :param live: ``True`` when the change also takes effect this run, by ONE OF TWO
        mechanisms — the shell mirrors the new value onto the live session (the PUSH
        rows, see the module docstring), or the consumer holds a callable onto the
        getter and re-reads it on every use so nothing is mirrored (the PULL rows:
        ``respect_gitignore`` #238, ``enable_skill_commands`` #244). ``False`` =
        persist-only. Note that ``live=False`` does NOT imply "applies next
        launch" — for the ten inert rows (#111 B-11, #84) nothing reads the value
        at any point, and it also does not imply INERT: ``features_agents`` is
        ``live=False`` and genuinely wired.
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
        SettingsRow(
            key="respect_gitignore",
            label="Gitignore in @ menu",
            kind="bool",
            read=lambda s: _on_off(s.get_respect_gitignore()),
            # 75 cells, deliberately. ``_open_settings`` hands ``select`` ONE
            # unwrapped string as ``detail``; ``select``'s Window is
            # ``wrap_lines=False`` and ``_picker_frame`` clamps only its RULES to
            # ``_PICK_MAX_WIDTH`` (78), so a longer help renders to exactly the
            # pane width, cut mid-word — measured, a 272-cell help gave 200 cells
            # at ``tmux -x 200`` against 78-cell rules. What fits is ranked by
            # measured harm: the fd condition MUST be present, or the live claim
            # is false for offline / Termux / never-ran-``find`` users (a
            # first-class configuration — ``ensure_tool("fd")`` is reached only by
            # the ``find`` tool and returns ``None`` offline). The phrasing is
            # "files the ignore files hide", not "files git ignores", because
            # ``--no-ignore`` also lifts ``.ignore``/``.fdignore`` and parent-
            # directory rules, which git does not own. "Default on" is dropped
            # because the value column already shows the current state; the
            # exclude list and the enumeration cap live in the README, ADR-0193
            # and the CHANGELOG.
            help="Off → the @ menu also matches files the ignore files hide (needs fd; live).",
            # LIVE by the PULL mechanism (see the module docstring): the completer
            # holds a callable onto ``get_respect_gitignore`` and re-reads it on
            # every enumeration, with the flag in its tree-cache key, so the flip
            # is answered by the next keystroke. ``_apply_live_setting``'s branch
            # is therefore a documented no-op — the shell could not mirror even if
            # it wanted to, because ``FileMentionCompleter`` is constructed inline
            # inside ``merge_completers([...])`` and never bound to a name.
            live=True,
        ),
        # --- PERSIST-ONLY rows (the rule; the exceptions are named below) ----
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
        # The exceptions in this block, which are genuinely wired and whose
        # help text is therefore left alone. Persist-only is the RULE, not a
        # property of the block: some of the rows below are live, which is why
        # the marker above says so and why a test derives the live ones from the
        # source rather than trusting a count word here (#244).
        #   * ``features_agents``       -> cli/entry.py::_build_harness_options
        #   * ``tool_card_max_lines``   -> tui/shell.py -> render.py (live)
        #   * ``render_max_width``      -> tui/shell.py -> tui/width.py (live)
        #   * ``enable_skill_commands`` -> tui/shell.py -> cli/resource_commands.py (live)
        #   * ``check_for_updates``     -> tui/shell.py::_start_update_check
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
            # of expanding.
            #
            # LIVE by the PULL mechanism (#244), NOT shaped like
            # ``features_agents``. The comment here used to say "read once per
            # ``_input_loop``", and the copy promised a restart; both are false.
            # Measured by AST: the one production call is INSIDE ``_input_loop``'s
            # ``while True:`` body, so the gate is an argument re-evaluated on
            # every turn, off the same SettingsManager the nested
            # ``_open_settings`` writes — a flip is in effect at the next line
            # typed. Nothing to mirror, so ``_apply_live_setting``'s branch is a
            # documented no-op, exactly like ``respect_gitignore``.
            #
            # CAREFUL, the note that outlived the inertness: it is THIS SETTING
            # the row speaks for, not skills. Skills load at startup
            # (``cli/entry.py`` ``load_skills`` -> ``harness.set_skills``) and
            # feed ``/skills``, the banner and rpc_mode's command list whatever
            # this flag says. Turning it off disables the ``/skill:<name>``
            # SURFACE only — wording that says otherwise would tell users
            # ``--skill`` and ``.aelix/skills`` do nothing, which is false. The
            # RPC command list is deliberately NOT gated (``rpc/rpc_mode.py``
            # emits ``skill:<name>`` unconditionally): what an RPC client is
            # offered is protocol-visible, and this row speaks for the TUI.
            #
            # 73 cells against ``_PICK_MAX_WIDTH`` (78) — the detail panel does
            # not wrap, and the live clause is the part a longer help loses.
            help="Live — off hides /skill:<name>; skills still load and /skills lists them.",
            live=True,
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

    A ``bool`` row re-reads its getter after the set (#244). When the re-read
    disagrees with the value asked for — a project ``.aelix/settings.json`` pins
    the key over the global cell the setter writes — a PUSH row (``_PUSH_BOOL_KEYS``)
    still returns ``ok`` with its mirror, because its live half bypasses the
    getter, and says the change is session-only; every other row returns ``error``
    so the shell draws a red line instead of a green one over an unchanged row.

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
            # Re-read before rendering, for the reason the ``int`` arm below
            # already re-reads: the value that comes back is not always the value
            # asked for. EVERY bool setter writes the GLOBAL cell, while most bool
            # getters read the MERGED view (``get_features_agents`` and
            # ``get_respect_gitignore`` deliberately read the global cell too, so
            # those two can never mismatch — see their docstrings), so for the
            # other ten a project ``.aelix/settings.json`` carrying the key wins
            # and the write is a no-op on screen. Measured on
            # ``check_for_updates`` (global ``true`` / project ``false``):
            # ``set_check_for_updates(True)`` leaves the getter ``False``, so
            # rendering ``new`` would draw a green "→ on" over a row that redraws
            # "off" — the #84 class of defect, in the confirmation line. Say what
            # survived instead. (#244; the merge scope itself is deliberate — see
            # ``get_respect_gitignore``'s docstring.)
            if _row_bool(row, sm) != new:
                if row.key in _PUSH_BOOL_KEYS:
                    # A PUSH row's live half does NOT go through the getter: the
                    # shell mirrors ``live`` straight onto the renderer flag
                    # (``shell.py``'s ``_apply_live_setting``), so the session DOES
                    # flip even though the next launch reads the project file
                    # again. Reporting ``error`` here would make the shell skip
                    # ``_apply_live_setting`` entirely (it ``continue``s on
                    # ``error``) and cost the user the only in-session control
                    # ``hide_compaction_summary`` has — a runtime regression the
                    # beta2 review measured against ``main``. So: ``ok`` plus the
                    # mirror, with a message built from what actually happened.
                    return ApplyResult(
                        kind="ok",
                        message=(
                            f"{row.label.lower()} toggled for this session only — a "
                            f"project .aelix/settings.json pins this key, so the next "
                            f"launch is back to {row.read(sm)}"
                        ),
                        live=(row.key, new),
                    )
                return ApplyResult(
                    kind="error",
                    message=(
                        f"{row.label}: still {row.read(sm)} — a project "
                        f".aelix/settings.json sets this key and wins over the "
                        f"global file this row writes (the global value was "
                        f"updated and applies where no project file overrides it)"
                    ),
                )
            note = f" ({row.apply_note})" if row.apply_note else ""
            return ApplyResult(
                kind="ok",
                message=f"{row.label.lower()} → {row.read(sm)}{note}",
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
    # #238 — the ``@``-menu ignore switch. Same table contract as above; this row
    # is LIVE, so a KeyError here would be a red line where the user expects the
    # menu to widen on the next keystroke.
    "respect_gitignore": "get_respect_gitignore",
    # #244 — the update-check switch, and the failure the note above describes,
    # shipped. The row went out ``kind="bool"`` with its key in NEITHER table, so
    # the first Enter raised ``KeyError`` inside ``_row_bool``, ``apply_setting``
    # swallowed it into ``✖ Check for updates: 'check_for_updates'``, and nothing
    # was written — while both READMEs said ``/settings`` turned the check off.
    "check_for_updates": "get_check_for_updates",
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
    "respect_gitignore": "set_respect_gitignore",
    # #244 — see the getter note above; the row was undispatchable on both halves.
    "check_for_updates": "set_check_for_updates",
}
# The bool rows whose live half is PUSH (see the module docstring): the shell
# mirrors ``ApplyResult.live`` onto a renderer flag, so the session changes
# WITHOUT the getter agreeing. That is what makes the project-pin case above an
# ``ok`` for these two and an ``error`` for every other bool row — a PULL row's
# consumer re-reads the same merged getter, so a pin genuinely wins in-session
# too. Not a fourth copy of the split: ``test_the_push_bool_keys_are_derived``
# rebuilds it from ``_apply_live_setting``'s branches and requires equality.
_PUSH_BOOL_KEYS: frozenset[str] = frozenset({"hide_thinking_block", "hide_compaction_summary"})
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


__all__ = [
    "ApplyResult",
    "SettingsRow",
    "apply_setting",
    "build_settings_rows",
]
