# ADR-0159 — In-flow modal slot (fix centered-Float clipping) + footer permission-first / steering-hidden

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** 6h₂₈ (TUI v2 overhaul roadmap)
- **Supersedes/relates:** ADR-0105 (`AelixTUIContext` dialogs + `show_modal` over Floats —
  this changes the captured-modal placement), ADR-0156 (the completions menu Float — UNCHANGED,
  stays a Float), ADR-0157 (permission posture badge + approval dialog — the approval dialog now
  mounts in-flow; the footer badge moves to the leading segment), ADR-0153 (footer segments).

## Context

Every captured interactive modal — the `/model` picker, `/settings`, `/resume`, `/thinking`,
`/mcp`, the WP-0 tool-approval dialog, and extension `custom()` overlays — routed through
`overlay.show_modal`, which wrapped the dialog content in a centered prompt-toolkit `Float`
(`make_float`, default `anchor="center"`) added to the chrome's `FloatContainer`. The chrome
`Application` runs `full_screen=False` (inline mode).

**The bug (user-reported):** these modals opened CENTERED and were CLIPPED at the terminal
bottom — the user saw only the top ~half; the Yes/No options were cut off.

**Root cause (empirically confirmed against prompt-toolkit 3.0.52):** a `Float` is an OVERLAY
that never contributes to the container's preferred height — `FloatContainer.preferred_height`
delegates to its `content` (the body HSplit) only ("we don't care about the height of the
floats"). In the non-fullscreen render path the app's rendered region equals the body's
preferred height, capped at terminal rows; inline, the body owns only a few rows at the terminal
bottom (measured: idle body preferred = 5 on an 80×24/40 terminal). `FloatContainer._draw_float`
then constrains a centered Float to that 5-row region, so a 30-line modal computes a negative
`ypos` and is clipped to the few rows the inline app owns — the tail overflows below the terminal
edge. The autocomplete `CompletionsMenu` Float "worked" only because it is ≤8 rows and
cursor-anchored, so it fit inside the rows the app already owned.

## Decision

**Mount the captured modal IN-FLOW, above the input**, instead of as a Float.

1. **`chrome.py` — a reserved focusable modal slot.** A `DynamicContainer(self._render_modal_slot)`
   is inserted into the body `HSplit` immediately ABOVE `input_window` (between `widgets_above`
   and the input editor — the same in-flow zone the stream tail + autocomplete occupy: "below the
   chat, above the prompt"). `_render_modal_slot()` returns `self._modal` when a modal is mounted,
   else a zero-row `Window(height=0)` placeholder (measured: contributes 0 rows, so no idle gap).
   `DynamicContainer` (not `ConditionalContainer`) is required because the latter only toggles a
   FIXED child's visibility — it cannot SWAP which container is shown. New methods: `mount_modal`,
   `unmount_modal`, `is_modal_open`. `add_float`/`remove_float`/`_floats`/`_completions_float` are
   UNTOUCHED — the completions menu + descriptor toasts stay Floats (they never clip).

   Because the slot is a real HSplit child, the body's preferred height GROWS to include the modal
   (measured: a 30-line modal lifts body preferred from 5 to ≥24), the non-fullscreen renderer
   allocates `min(preferred, terminal_rows)` and the terminal scrolls prior scrollback UP — the
   whole modal renders, never clipped. This is the exact mechanism the multi-line input editor
   already uses to grow the inline app upward.

2. **`overlay.py` — `show_modal` mounts in-flow + height-caps.** Same public signature (zero
   call-site changes across all 7 dialog builders + the approval dialog + descriptor
   management-modal). It now calls `chrome.mount_modal(slot_content)` / `chrome.unmount_modal()`
   instead of `add_float` / `remove_float`. The content is wrapped in a `ConditionalContainer`
   (preserves `AelixOverlayHandle.hide()`/`set_hidden()` over the shared `hidden` dict) inside a
   new `_CappedContainer` — a thin delegating `Container` that lets the child compute its natural
   `preferred_height` and only CLAMPS the result to `terminal_rows − reserve` (reserve = 5 rows for
   input/status/footer). A plain `HSplit(height=Dimension(max=…))` could NOT be used: an explicit
   `height` Dimension forces the HSplit's *preferred* to the Dimension's `preferred` (0/1),
   overriding the content-derived height, so a capped HSplit reported 0 rows and never grew the
   body. The cap is a callable read per render, so it tracks terminal resize. A modal taller than
   the cap relies on the child's own cursor-driven internal scroll (`select` + the approval dialog
   already self-viewport); the cap guarantees no terminal overflow regardless.

   `make_float` is RETAINED + exported (descriptor toasts + the completions menu use it; its
   edge-mapping tests are unaffected). `OverlayOptions.anchor`/`offset_x`/`offset_y` become no-ops
   for in-flow modals (they always render above the input); `max_height` (int) tightens the cap.

3. **Footer (`context.py._refresh_footer` + `permission_mode.py`).**
   - The **permission posture badge is the LEADING (leftmost) segment, shown at ALL times** when a
     posture is wired — a glyph badge (✎/⏸/⚠/🤖) for non-DEFAULT modes and a neutral `● default`
     label (`DEFAULT_BADGE`) on DEFAULT (the live provider returns `None` there). When no provider
     is wired (headless / no posture) the segment is omitted entirely (degrade, never crash).
     `MODE_META[DEFAULT].badge_text` stays `""` (a security-relevant contract relied on by
     `PermissionPosture.badge()` returning `None` and the toast/command `badge_text or "default"`
     fallbacks) — the new `DEFAULT_BADGE` constant is a SEPARATE footer-only label.
   - The **steering `⏵⏵ {mode}` segment is HIDDEN by default**: rendered only when the steering mode
     differs from `"one-at-a-time"` (i.e. the user switched it to `"all"`).
   - The other segments (queued / cwd / model / context / branch) are unchanged, after the badge.

## Consequences

- All captured modals (`/model`, `/settings`, `/resume`, `/thinking`, `/mcp`, approval, custom,
  descriptor management-modal) render fully visible above the input and can no longer clip — the
  chrome grows upward and is height-bounded to the terminal with internal scroll as a last resort.
- A single modal slot is assumed (modals are driven serially from the input loop; the only async
  path, `descriptors._spawn`, mounts one at a time). Concurrent mounts would overwrite the slot;
  documented as a single-modal invariant.
- The footer always advertises the active permission posture at the front; the noisy default
  steering label is gone.

## Alternatives rejected

- **Switch the chrome to `full_screen=True`** — would break the inline scrollback UX (ADR-0105) and
  the whole Pi/Claude-Code "print above a running app" model.
- **Keep the Float but height-cap it** — a Float still ignores body height, so it cannot grow the
  app's rendered region upward; capping only shrinks the (already clipped) overlay.
- **`HSplit(height=Dimension(max=…))` wrapper** — forces preferred to 0/1, never grows the body
  (see Decision §2).
