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
  (Closed in the TUI on 2026-10-22, #559 review round 2 below: `/agents use`'s
  repaint after the rollback re-derives the meter from the restored model.)

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

## Amendment (2026-10-22, #534)

The `model_select` hook the #249 amendment registered is the only repaint that
`/agents use` and an extension's `ctx.set_model` reach, and it repainted the
footer only through `set_context_label`, which returns on an unchanged label. A
switch inside one context window, on the same provider or not, therefore left
the footer's `✱` id on the old model until the next turn ended, while the next
prompt already went to the new one. Measured on `e8ba1009` in a pty (fake keys,
a local recorder, `.omc/probes/b4-b-live/534/`): `/agents use` from `gwa/m1`
onto `gwa/m2` and onto `gwb/n1` (all at 128K), `/agents use --none` after
`--model openrouter/auto --agent orprof` (the issue's example: footer
`openrouter/auto`, request `model=auto`), and an extension's `ctx.set_model`.
A use onto a model with another window repainted at once, because its label
changed. The `🧠` segment had the same defect with no hook at all: `/thinking
high` printed `thinking → high` above `🧠 off`, and a profile's `thinking:` and
an extension's `ctx.set_thinking_level` left it as well. `/model`, the model
and thinking pickers, the pick after `/login` and the `/settings` thinking row
repaint themselves and were not affected; a session swap repaints through its
own path (measured: `/resume` onto a session last run on another model at the
same label).

Three changes:

- `_model_select_hook` calls `_refresh_footer()` after the meter, whatever the
  meter did, under its own `suppress` — on both branches (the idle stats read
  and the synchronous paint while a live `message_end` figure is held).
- A `thinking_level_select` hook (`_thinking_level_select_hook`,
  `source="tui-footer"`, `error_mode="continue"` for the same reason as the
  meter's: `set_thinking_level` raises for a handler error after the level is
  assigned) repaints the footer. It has the `model_select` hook's lifecycle:
  registered in `_rebind`, moved to the new bus on every swap and reload,
  released at teardown.
- `/agents use` repaints the footer after the use, on success and on failure.
  The hooks already cover a use that succeeds; this is for the rollback.
  `AgentProfileService.use` restores the model and the level by assignment,
  with no event, and with either hook alone the footer kept the abandoned
  profile's `✱` id beside the red `✖ agents use failed` line (sabotage: red
  with each hook present and this repaint removed).

pi: the footer reads `session.state.model` on every render
(`modes/interactive/components/footer.ts` `render`, at `fdce48eb4`), so it has
no cached id to go stale, and it invalidates on `thinking_level_changed`
(`interactive-mode.ts` `handleEvent`). The two hooks are the equivalent for a
footer that is a composed string; pi has no `/agents use`.

The #249 known gap above is unchanged by #534 (the #559 amendment's review round
2 below closes it): after a rollback the footer now shows the
restored id and level, but the context label is cached and only its next
refresh re-derives it. At idle the label was already right after a refused use
across windows (`gwa/m1` → `gwa/big`, measured), because the idle branch
schedules a stats read that lands after the rollback; the gap is the
synchronous branch.

Cost: one composed string and one `invalidate` per model or level change. Two
`invalidate`s in one synchronous run cost one redraw (prompt_toolkit's
`Application.invalidate` returns while one is pending). The unchanged-label
short-circuit in `set_context_label`, which the flicker history asked for,
stays; its docstring now says it is not a repaint any other segment can rely
on.

## Amendment (2026-10-22, #559)

#559 asked that `/thinking <level>` repaint the footer's `🧠` level as the
picker does. Filed against `27fa21c5`, where it did not repaint at all
(measured in a pty: `/thinking max` on `claude-opus-4-6` printed
`thinking → max` above `🧠 off`, still `🧠 off` after another key press). The
#534 amendment above fixed that path through the `thinking_level_select` hook:
on this lane's `b2af463c` the same keys show `🧠 max` at once, and
`/thinking off` shows `🧠 off`.

Through the hook is not the picker's way, and the difference is measurable.
The picker's host repaints after every pick (`_open_thinking_picker`), and pi's
`selectThinkingLevel` invalidates the footer after `setThinkingLevel` for both
the typed command and the selector (`interactive-mode.ts` at `fdce48eb4`). The
hook is one handler on the bus, registered by `run_tui` after the handlers the
harness was built with, which is where an extension's handlers sit. A handler
there that raises (the default `error_mode="throw"`) stops the bus, and
`set_thinking_level` has already replaced the level by then: it assigns, then
emits. The command printed `✖ thinking switch failed`, the footer kept the old
level, and the next request went at the new one. Measured on `b2af463c` with an
extension whose handler refuses `low` (`.omc/probes/b4-b-live/559/`): typed
`/thinking low` left `🧠 off` over a request sent with
`reasoning_effort=low`, and the picker, choosing the same level, showed
`🧠 low`.

The #534 amendment says `/model`, the pickers, the pick after `/login` and the
`/settings` thinking row "repaint themselves and were not affected". That holds
on the success path only. Each of them returns or re-raises before its repaint
when the switch reports a failure, and `set_model` also assigns before it
emits. Measured with a `model_select` handler refusing `m2`: `/model gwa/m2`
and the `/model` picker each left `✱ m1` over a request sent as `m2`. A second
`/settings` thinking cycle refused at `low` left `🧠 minimal` over `low`. The
pick after `/login` has the same shape (code read, and row 6 of the new tests).

Five changes, each a repaint on the path that skipped it:

- `/thinking <level>` repaints in a `finally`, on success and on failure,
  through `_refresh_footer_quietly`. On success it no longer depends on the hook
  (row 2 takes the hook off the bus).
- `/model <id>` repaints on its refusal branch when the model in force is no
  longer the one it found. A refusal that switched nothing still repaints
  nothing, which `tests/tui/test_commands.py` already pins (an unconditional
  repaint turned two of its rows red).
- The `/model` picker repaints before it reports a failed switch.
- The `/settings` thinking row repaints before it re-raises, and `/settings`
  still reports the failure.
- The pick after `/login` repaints before it reports the failure.

What this leaves open. An extension's own `ctx.set_model` or
`ctx.set_thinking_level`, refused by an earlier handler, reaches no command
path, so the footer stays on the old value there. A fix needs the TUI's repaint
to run whatever an earlier handler did. That means hook ordering or the
harness. Neither is a TUI change, so it is filed as a follow-up. The red line
that calls such a switch "failed" while the change stays in force is the
harness's contract (`set_model` / `set_thinking_level` raise
`AgentHarnessError` after assigning), and it is unchanged. pi's
`setThinkingLevel` emits the extension event fire-and-forget, so a handler's
raise never turns its switch into a failure.

Cost: one composed string per failed switch, and a second one on a typed
`/thinking` that succeeds (the hook's repaint, then the command's). Not
measured: whether those two land in one redraw.

### Review round 2 (2026-10-22): the meter's window follows the model in force

The five repaints above recompose the footer from its cached segments, and the
context meter's label (`◔ 0% · 0/128K`) is one of them. Only the TUI's
`model_select` hook re-derived that label from the new model, and the refusing
handler had stopped the bus before it. So after a refused switch that stayed in
force, the footer paired the new model's `✱` id with the previous model's
window. Measured by the cross-review in a pty (`.omc/probes/b4-b-live/cross/`,
`s559.meter.out`): `gwa/m1` at 128K, `gwa/m2` at 32K, a handler refusing `m2`;
`/model gwa/m2` showed `✱ m2 · 🧠 off · ◔ 0% · 0/128K`. The new rows 7-9 of
`tests/tui/test_footer_repaints_after_a_refused_switch_559.py` (`/model <id>`,
the `/model` picker, the pick after `/login`, on a pair of windows) were red on
`73fbe718`: each waited out its bound with the old window (or no meter) on
screen.

Decision: one helper, `_repaint_footer_for_model_in_force` in `run_tui`, does
what the `model_select` hook did, and the hook now calls it: re-derive the meter
from the model in force (mid-turn, from the live token count and the model's
window; otherwise a stats read scheduled off the frame, which reads the model in
force when it runs), then recompose the footer, under two guards. Every path the
#559 sweep fixed repaints through it: `/model <id>` and `/agents use` through
`CommandContext.refresh_footer`, which `run_tui` now wires to the helper, the
`/model` picker through its `refresh_footer`, and the `/settings` thinking row
and the pick after `/login` directly. `/agents use`'s repaint after a rollback
therefore re-derives the meter from the restored model, which closes the #249
gap above for the TUI, mid-turn included. The cost: a path that already
repainted through the hook (a `/model` that succeeds) schedules a second stats
read, and the generation counter drops the first.

Still open: an extension's own refused `ctx.set_model` has no command path, so
its meter and `✱` id both stay on the old model (the follow-up above).
