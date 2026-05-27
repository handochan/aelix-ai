# 0115. TUI — Render Thinking Before the Answer (not after)

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure `tui/` consumer)

## Context

With OpenRouter reasoning models now emitting tool calls (ADR-0114), the user
observed reasoning printing **after** the text answer in the transcript.

Root cause: the adapter emits per-block *end* events at **end-of-stream**, in
`output_content` index order (`openai_completions.py:1142-1162`) — so for a
`thinking → text` turn the order on the wire is:
`ThinkingStart → ThinkingDelta×N → TextStart → TextDelta×M (streamed live) →
ThinkingEnd → TextEnd → Done`. But `EventRenderer` only **rendered** thinking at
`thinking_end` (`render.py`), by which point the answer had already streamed
live. Result: dim-italic reasoning dumped *below* the answer.

## Decision (pure `tui/` consumer)

`EventRenderer` now flushes buffered thinking **before** the block that followed
it, instead of at `thinking_end`:

- `text_delta` (first delta, before opening the text stream) → `_flush_thinking`.
- `_render_tool_start` → `_flush_thinking` (reasoning renders above a tool card too).
- `_flush_thinking` is now **idempotent** via a `_thinking_flushed` flag (reset in
  `_reset_message_state`): the late `thinking_end` — which carries the same content
  — is a no-op, so reasoning is never double-printed.
- `thinking_end` still flushes for **thinking-only** turns (no following text/tool),
  preserving the existing `test_thinking_rendered_on_end` behavior.

## Consequences

- Reasoning renders above the answer / tool card, exactly once (live-order correct).
- Thinking is still *buffered* (flushed as one dim-italic block), not streamed live
  or collapsible — that remains a P2 gap (see the TUI gap analysis).
- Known edge case: thinking that *interleaves* with text after the first flush is
  dropped (the flag suppresses re-render). Acceptable — the qwen/OpenRouter pattern
  is thinking-then-answer; interleaved channels are rare.

## Verification

- ruff clean; pyright 8-baseline (0 new); full pytest 2914 pass / 1 skip
  (+2: `test_thinking_renders_before_text_not_after`,
  `test_thinking_renders_before_tool_card`); protected paths byte-unchanged.
