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

## Amendment (2026-09-08, #249)

The "Live context-window meter" decision above records the trigger as "refreshed
async on the `turn_end` AgentEvent". That is no longer the whole set, and the
reason it had to change is that **`turn_end` was never once per turn**: the loop
emits it inside `while has_more_tool_calls or pending_messages:` (`loop.py:240`),
so a thirty-tool turn already fired thirty of them and already ran thirty
`get_session_stats` reads. The meter was not frozen mid-turn because the refresh
was rare — it was frozen because **`get_session_stats` cannot answer a mid-turn
question at all.** `_get_context_usage_safe` estimates over `self._state.messages`
(`core.py:2761`), and the only turn-path mutation of that list is the
`extend(new_messages)` at `core.py:4598`, which runs *after* the loop returns. The
#249 design's probe inside a live turn recorded `len(harness.messages) == 0` at
every `message_end` / `turn_end` / `agent_end` and `123956` context tokens the
moment the turn ended (`.omc/specs/249-design-2026-09-08.md` §0); the code path
says the same thing without a stopwatch. Every one of those thirty reads
therefore returned the pre-turn figure, and on turn 1 of a fresh session that
figure is `◔ 0%`.

The trigger set is now: `message_end` (the live mid-turn figure) + `compaction_end`
+ `turn_end` **when no live figure is held** + `settled` + `model_select` + a
session rebind. Three consequences worth recording:

- **The per-round-trip walk is work REMOVED, not added — but only the meter's
  half of it.** While a live figure is held the stats read is strictly worse than
  what is already painted — it estimates over a list that provably has not
  changed — so the meter skips it. The `/stats` history recorder on the same
  `turn_end` still performs one `get_session_stats` per round-trip
  unconditionally (ADR-0168), so this halves the per-round-trip reads rather
  than eliminating them. The in-memory half of one read
  (`estimate_context_tokens` + `aggregate_session_stats`) measured 0.035 ms at
  200 messages and 0.349 ms at 2000, nearly all of it in `aggregate_session_stats`
  — the estimate alone is 0.001 ms — and a persisted session adds a
  `Session.get_branch()` disk read on top that was not measured. The live paint
  measured 1.13 µs, so no debounce is warranted.
- **The abort path's `turn_end` refresh was stale too**, which the shell's own
  comment denied ("the ABORT and ERROR turn paths … append their message to
  `state.messages` BEFORE emitting turn_end and so are already current here").
  Only a **bodiless** aborted stub is appended (`core.py:4545`); the turn's real
  assistant messages sit in `new_messages` and are dropped by the `return []`
  before the extend, so that estimate anchors on the *previous* turn and is
  **lower** than the live figure. Skipping it there is the better number, not
  merely a harmless one. The false comment is corrected in `tui/shell.py`.
- **The post-compaction `tokens=None` sentinel is bypassed sooner.**
  `_get_context_usage_safe` returns `ContextUsage(tokens=None, percent=None)`
  when a compaction has no post-compaction assistant usage behind it
  (`core.py:2736-2762`, pi `getContextUsage`) — the deliberate blank window after
  `/compact`. The live paint does not consult the session branch, so that window
  now ends at the first post-compaction assistant response instead of at the next
  `turn_end`. A stated divergence from pi under ADR-0235; pi has no equivalent
  trigger.

- **Known gap: a rolled-back `/agents use` leaves the denominator on the
  abandoned model.** `AgentProfileService.apply` calls `harness.set_model` (which
  emits `model_select`, so the meter moves) and, if a later step raises, restores
  the model by writing `harness.state.model` directly — deliberately, to avoid
  re-emitting hooks that already fired. No event tells the meter to move back, so
  the footer reads against the refused model's window until the next
  `settled`/`turn_end`/model change repaints it (one turn at most). Accepted
  rather than re-emitting on the rollback path; recorded at the rollback site.

`model_select` is registered on the harness bus rather than in `/model`'s handler
because `harness.set_model` (`core.py:2313`) is the single funnel that `/model`,
the model picker, the pick offered after `/login` and an extension's
`ctx.set_model` all reach. It carries `error_mode="continue"`: the bus default
is `"throw"`, and `set_model` converts a handler error into `AgentHarnessError`
*after* `_state.model` is already replaced (`core.py:2338`), which `/model` prints
as `✖ model switch failed` — a footer bug must not report a successful switch as a
failed one. The handler body is *also* wrapped in `contextlib.suppress(Exception)`,
which is redundant rather than complementary (both catch the same class,
`hooks.py:1349-1354`); it is kept as a local guard so the guarantee survives a
registration that loses the kwarg, and the tests pin each mechanism separately.

One correction to the "Known limitation" in Consequences above ("the meter's
percent can read low/0 until that protected `aelix-agent-core` path threads usage
through"): it was already stale before #249, not superseded by it. The adapter
usage capture recorded in this same ADR put the usage on the `AssistantMessage`,
and `estimate_context_tokens` has been reading it off `_state.messages` at rest
ever since (`core.py:2761` → `compaction.py:1130`/`:1102`); nothing in
`aelix-agent-core` changed on this branch. What #249 adds is a second reader of
the same usage — off the `message_end` event — for the mid-turn figure.
