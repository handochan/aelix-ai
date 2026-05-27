# 0116. TUI P0 Consumer Batch — Footer Meter, Real Mode, Diffs, /export, /thinking, Usage Capture

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

## Context

A TUI gap analysis (vs pi / Claude Code) found the biggest theme was "rails but
no train": the harness already exposed capabilities the TUI never surfaced. The
user reported the `⏵⏵ default` footer was meaningless and the UI felt
unfinished. This sprint wires the highest-value, lowest-effort P0 consumer items.
All edits are in `packages/aelix-coding-agent` + `packages/aelix-ai` (NOT the
protected `aelix-agent-core` core).

## The decisions

- **Real steering-mode footer** (`context.py` + `shell.py`): the footer's `⏵⏵`
  segment was a hardcoded `"default"` placeholder. Added a `mode_provider`
  callback wired to `harness.steering_mode` (`"one-at-a-time"`/`"all"`), so the
  segment reflects the live harness mode; `_mode` remains the headless fallback.
- **Live context-window meter** (`context.py` + `shell.py`): a cached
  `_context_label` segment (`◔ N% · used/window`), refreshed async on the
  `turn_end` AgentEvent via `get_session_stats().context_usage`. The refresh task
  is held in a `set` so it isn't GC'd before running; failures degrade to no
  segment.
- **Colorized diffs** (`render.py`): edit/write tools emit a unified diff;
  `_render_tool_end` now detects it (`_looks_like_diff`, gated on a `@@` hunk
  header) and renders `+`green / `-`red / `@@`cyan / `---|+++`bold instead of flat
  dim text.
- **`/export`** (`commands.py`): wired to `harness.export_to_html()`.
- **`/thinking [level]`** (`commands.py`): show / set via
  `harness.set_thinking_level`.
- **History persistence** (`shell.py`): pass a `history_path`
  (`<agent_dir>/tui_input_history`) to the default `AelixChrome` so ↑/↓ + Ctrl+R
  survive sessions (the chrome already supported it).
- **Catalog model enrichment** (`cli/runtime_bootstrap.py::resolve_model`): the
  OpenRouter-from-env path returned a **bare** `Model` (`context_window=0`,
  `max_tokens=0`, empty cost, no `thinking_level_map`), which silently disabled
  the context meter (`getContextUsage` returns None when the window is 0), zeroed
  `/cost`, and dropped thinking levels. It now returns the full Pi-catalog entry
  when the id is known (honoring a custom `OPENROUTER_BASE_URL`), falling back to
  the bare model for unknown ids. (Safe w.r.t. ADR-0114: the catalog qwen3.6 has
  `max_tokens == context_window`, which the ADR-0114 guard omits — no 400.)
- **Streaming usage capture** (`openai_completions.py`): the adapter ignored
  `chunk.usage` (a Sprint-6b deferral) and skipped the final usage-only chunk via
  the empty-`choices` guard. It now reads `chunk.usage` BEFORE that guard and
  populates `AssistantMessage.usage` (`_usage_to_dict` emits both `input`/`output`
  and `input_tokens`/`output_tokens` + `total_tokens` + `cache_read` so the
  session-stats aggregator AND `calculate_context_tokens` agree). `/cost` now
  reflects real token totals.

## Consequences

- Live-verified (PTY, qwen/qwen3.6-35b-a3b): footer shows `⏵⏵ one-at-a-time`;
  reasoning renders above the answer (ADR-0115); colorized diff confirmed
  (red/green/cyan ANSI); `/export` writes HTML; `/help` lists `/thinking` +
  `/export`; the context-meter segment appears with the correct window; `/cost`
  shows real `input`/`output`/`total` tokens.
- **Known limitation**: the meter's *token %* reads `getContextUsage` which
  estimates over the harness's in-memory `_state.messages`. That list does not yet
  carry per-message `usage` (the persisted session does — which is why `/cost` is
  correct), so the meter's percent can read low/0 until that protected
  `aelix-agent-core` path threads usage through. The meter structurally works
  (segment + correct window + per-turn refresh).
- **Deferred (separate, protected/large)**: `/compact` summarizer is an
  unimplemented core stub (`session/compaction.py` — "compact() LLM provider not
  yet implemented"); steer/queue-while-running; tool approval prompts; `/resume`.

## Verification

- ruff clean; pyright 8-baseline (0 new); full pytest 2930 pass / 1 skip
  (+16 in `tests/tui/test_p0_consumer_batch.py`, +registry update); protected
  paths byte-unchanged.
- Live PTY (tmux) on the real `python -m aelix_coding_agent` with qwen3.6.
