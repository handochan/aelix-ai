# ADR-0156 — TUI slash-command autocomplete menu (selected-row marker + match counter)

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₂₆
- **Supersedes/relates:** ADR-0105 (`AelixChrome` live bottom region + the completions `Float`),
  ADR-0110 / ADR-0121 (`DescriptorCommandCompleter` + `FileMentionCompleter`, the completers this menu
  renders), ADR-0132 / ADR-0154 (`select()` rich picker — a *separate* modal surface). Roadmap:
  `.omc/specs/tui-v2-overhaul-roadmap.md` (autocomplete-menu WP, claude/qwen-additive).

## Context

The slash-command / `@file` dropdown rendered through prompt-toolkit's **stock**
`CompletionsMenu` (`chrome.py`, the leading `self._completions_float` `Float`). That menu already showed
each command plus its description column (`display_meta`, yielded by `DescriptorCommandCompleter`), but
it lacked two affordances present in the best-of-breed mockup:

1. a **selected-row pointer** — the stock menu only changes the *style class* of the highlighted row, which
   is invisible on terminals/themes where the current-row style is subtle, and
2. a **`(current/total)` match counter** — so the user can see their position in a long completion list.

The completers themselves already carry everything needed (`display`, `display_meta`); the gap was purely
in the dropdown *presentation* (the `Float` content). This is an additive divergence from pi, not a parity
port — pi renders a plain menu.

## Decision

A surgical subclass of prompt-toolkit's completion menu **control**, mounted in place of the stock
`CompletionsMenu`. Everything lives in `tui/chrome.py` (the single mount point); the completers
(`completion.py`), the command registry, `CommandContext`, and `shell.py` are **untouched** — no new
slash command, no new `CommandContext` field, no `_open_X` flow.

1. **`_MarkedCompletionsMenuControl(CompletionsMenuControl)`** overrides ONLY `create_content` and
   `preferred_height`, reusing the base control's `_get_menu_width` / `_get_menu_meta_width` /
   `_show_meta` / `_get_menu_item_meta_fragments` helpers so the description column keeps rendering
   exactly as before.
   - **Marker:** the current row swaps the single leading space that
     `prompt_toolkit.layout.menus._get_menu_item_fragments(..., space_after=True)` always emits as its
     first fragment (`("", " ")`) for the marker glyph `→`. Non-current rows are left untouched (they keep
     the plain leading space), so the marker is the *only* visual delta on those rows.
   - **Counter:** a synthetic EXTRA last row (`line_count = len(completions) + 1`) renders
     `(current/total)`. `current` comes from `CompletionState.complete_index` (None-safe via
     `(index or 0) + 1`, 1-based), `total` from `len(completions)`. It is styled with the menu's
     already-defined `class:completion-menu.meta.completion` class — no new style key.
   - `preferred_height` returns `len(completions) + 1` (the counter row consumes one menu row); with no
     `complete_state` it returns `0` and `create_content` returns an empty `UIContent` (inert/headless).
2. **`_MarkedCompletionsMenu(ConditionalContainer)`** mirrors the stock `CompletionsMenu` container shape
   exactly — same `Window` sizing (`Dimension(min=8)`, `height max=max_height`), `ScrollOffsets`,
   `ScrollbarMargin(display_arrows=False)`, `z_index=10**8`, and the `has_completions & ~is_done`
   visibility filter — but mounts `_MarkedCompletionsMenuControl`. `max_height` stays 8, so at 8+
   completions the window scrolls (`scroll_offset=1` keeps the selected row visible) while the counter
   pins to the content bottom.
3. **Mount** (`chrome.py`, `AelixChrome.__init__`): `self._completions_float`'s content is
   `_MarkedCompletionsMenu(max_height=8, scroll_offset=1)` instead of the stock
   `CompletionsMenu(max_height=8, scroll_offset=1)`. Installed once at construction, so it applies to every
   completer the host later sets via `set_command_completer(...)` — no extra wiring.

## Consequences

- **Pure TUI-consumer; no protected-core (`aelix-agent-core` / `aelix-ai/src`) changes.** `completion.py`
  needs no change (it already yields `display` + `display_meta`).
- **Headless-safe:** a pure `UIControl` with no I/O — verified rendering under `DummyOutput` +
  `create_app_session`. With no completions the `ConditionalContainer` filter renders nothing, exactly like
  the stock menu, so it stays inert (FakeHarness / non-slash input).
- **No regression** to the existing completion/chrome tests, which exercise the *completers* and chrome
  *state renderers* — both disjoint from the menu control. Baseline 56 tests stay green; +5 new tests cover
  marker+counter render, None-index safety, the inert empty state, `preferred_height`'s counter row, and a
  chrome smoke check that the `Float` mounts the marked menu.
- **Subtlety captured:** the counter row consumes one menu row, so `preferred_height` MUST be overridden to
  `len(completions) + 1`; the `Float`'s `max_height` stays 8, so 8+ completions scroll while the counter
  stays pinned to the content bottom.
- Coupled to prompt-toolkit internals (`CompletionsMenuControl`, `_get_menu_item_fragments`,
  `_get_menu_item_meta_fragments`). This is the documented/stable shape of the 3.0.x menu control; the
  marker swap is guarded (`frags[0][1] == " "`) so a future change to the leading-fragment shape degrades
  to "no marker" rather than a crash. Pinned via the regression tests.
