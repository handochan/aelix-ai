# 0247. A failed turn is said once, and the copy the model reads stays

Status: Accepted (2026-09-22)
Date: 2026-09-22
Supersedes/relates: ADR-0122 (`renderCurrentSessionState` parity — this is the
replay path it defines, narrowed by one case), ADR-0104 (the TUI shell's
terminal-outcome line `stop_reason ∈ {"error","aborted"}` + `error_message` —
unchanged, and now the ONLY rendering of a synthesised failure on both TUI
roads, live and replay; `export_to_html` is a third road, does not speak this
vocabulary at all, and is measured but not fixed here — see §Consequences),
ADR-0183 (the display tier `build_display_messages` feeds replay — unchanged,
and deliberately not where this lands), ADR-0242 (the session record rules —
**nothing on disk changes here**, which is the point), ADR-0211 / ADR-0208
(the message decoder and session durability, untouched).
Issue: #194, the persisted half of the pair split out of #189. The live half
shipped with #189 and is unchanged.
Probe: `.omc/specs/194-measure.py` (the same file runs on the branch base and
on the branch). Live driver: `.omc/specs/194-live.py`.

`/resume` showed a failed turn twice, in two different renderings of one
sentence.

## What was broken, measured on `fad2e28`

When an exception escapes the agent loop, `harness/core.py` synthesises the
assistant message that terminates the turn:

```python
failure = AssistantMessage(
    content=[TextContent(text=f"[error] {exc}")],
    stop_reason="error",
    error_message=str(exc),
)
```

The same string is in two fields, and both persist. `EventRenderer.replay`
walks `content` — through `markdown_lines`, so a provider message's backticks
come back as code spans — and then commits the stop-reason line built from
`error_message`. The live path never renders that `content` at all: a
synthesised message is never streamed, so no `text_delta` ever reaches the
glass.

Measured through a real `AgentHarness` over a real `JsonlSessionStorage` file,
driven by a provider stream that ends with no terminal event (`loop.py`'s "The
model stream ended without a result"), which is the failure that still escapes
the loop after #189 — #189's own case is now refused by `tui/shell.py`'s
runnability gate and never gets there:

```
LIVE turn emissions:  1
   | ✖ The model stream ended without a result: it stopped before …

REPLAY emissions:     2
   | [error] The model stream ended without a result: it stopped before …
   | ✖ The model stream ended without a result: it stopped before …
```

So the live turn is right and the reload is wrong. The user who hit a failure
reads it once when it happens, and twice forever after.

## Decision

### 1. `error_message` is the field a reader renders. `content` is the field the model reads.

They are not redundant copies of one value with one job; they have two jobs,
and only one of them is display.

- `error_message` is what `stop_reason == "error"` promises a reader. Both the
  live renderer (`_render_message_error`) and replay already build the `✖` line
  from it, and ADR-0104 fixed that vocabulary.
- `content` is what the **model** reads on the following turn, and that was
  measured rather than assumed. Through a real harness: the `[error] …` text is
  in the `Context.messages` that the next `prompt()` sends the provider, and
  it is still there in `build_session_context` after a reload. A failure with
  an empty body would leave the next turn unable to see what went wrong.

So "delete one of them" is not available, and the rule is about *rendering*:
**when a persisted failure's body is the writer's echo of `error_message`, the
replay renders it once, from `error_message`.** The qualifier is load-bearing,
not hedging. A body that is NOT that echo is a different sentence and keeps its
render (§3), and a message with no `error_message` at all has no echo to match,
so its body is the only record of the failure and stays (§3.1). The first draft
of this ADR stated the rule unconditionally and the code did not deliver it.

### 2. The fix is in `replay`, not in the writer.

Two measurements decided this, not taste:

- Every session file already on disk carries both fields. A writer-only change
  cannot reach a single one of them, so every failure a user has already
  recorded would keep double-printing. `.omc/specs/194-measure.py` ARM 3
  replays that exact shape with no writer in the picture: 2 before, 1 after.
- The `content` copy is load-bearing (§1), so the writer has nothing to give
  up.

`replay` is also where the suppression is provably safe. It knows it is about
to commit the `✖` line unconditionally for `stop_reason == "error"`, so
skipping the echo turns 2 into 1 and can never turn 1 into 0.

### 3. The match is exact, and that is the whole safety argument.

`replay` skips a text block only when its text equals `f"[error] {error_message}"`
on a message whose `stop_reason` is `"error"`. **Outer** whitespace is stripped
from both sides and nothing else is touched — a persisted body that was
re-wrapped or re-indented *inside* does not match and keeps its render. (The
test file's `_flat` collapses whitespace, but that is an instrument, not the
rule; an earlier draft of this ADR said "whitespace-normalised" and described
the instrument rather than the code.)

An **adapter-reported** failure is a different shape and must keep its body:
`providers/anthropic.py` snapshots `content=list(output_content)` — whatever
the model actually streamed before the connection broke — onto the error
message before yielding `AssistantErrorEvent`. That text has no `[error] `
prefix and is the only record of what the turn produced. `harness/core.py` is
the only `[error] ` synthesiser in `packages/`, so nothing else can match this
shape by accident.

"Skip every text block when `stop_reason == "error"`" is the tempting simpler
rule, and it silently eats those partial answers.
`test_a_partial_answer_before_a_provider_error_still_replays` exists to stop
it, and does (measured, §Tests).

### 3.1. `error_message == ""` is a shape the writer produces, and it decides the guard.

Found in review, on the first draft of this fix, and the reason the guard reads
`if err is not None` and not `if err`.

`harness/core.py` writes `error_message=str(exc)`, and `str(exc)` is `""` for
any exception raised with no message — a bare `TimeoutError()`, an
`asyncio` timeout, a third-party sentinel `raise Stop()`. The body it is paired
with is then `"[error] "`. A truth test reads that as *no error message at all*,
so the suppression switched itself OFF for precisely the failures that carry the
least information: the `[error]` body came back, and the `✖` line degraded to
`✖ request error` (`render.py` falls back with `or`), so the two lines were not
even the same sentence any more — the user read `[error]` and `✖ request error`.
Measured through a real `AgentHarness` and a real JSONL round trip
(`.omc/specs/194-measure.py` ARM 4): LIVE 1, REPLAY 2 with `if err:`; LIVE 1,
REPLAY 1 with `is not None`.

`error_message=None` is the *other* side of that boundary and is deliberately
NOT suppressed. There is no echo to match, the `✖` line can only say
`✖ request error`, and the body is then the only record of what happened —
suppressing it would be the "1 into 0" direction. This repo's writer never
produces that shape; a third-party or hand-edited session can.
`test_a_failure_with_no_error_message_at_all_keeps_its_body` pins it, and goes
red under the "skip every text block" sabotage (§Tests).

### 4. Nothing on disk changes.

No session format change, no entry-type change, no decoder change — ADR-0242's
record rules and ADR-0211's decoder are untouched. Old sessions render better;
new sessions are byte-identical to what the previous build wrote. There is
nothing to migrate and nothing for an older Aelix to choke on.

The issue body filed this as a data-format decision, and it turned out not to
be one. That is the decision this ADR records.

## Consequences

- A resumed transcript now agrees with the live transcript it is supposed to
  reproduce, for the synthesised-failure case.
- The `[error] ` prefix is an internal marker and is no longer shown to a user
  **on the two roads this ADR covers** — the live turn and the TUI replay
  (`/resume`, `--continue`, `--session`, `--fork`). It remains in the persisted
  `content`, where the model reads it, and it is still user-visible on the HTML
  export road (next bullet). An earlier draft of this ADR said "on any road";
  that was false, and its own "What this does not change" section contradicted
  it three paragraphs later.
- **`export_to_html` still shows it, and now shows nothing else.** (`/export`
  in the TUI, `commands.py:1331`; the RPC export at `rpc_mode.py:1316`.)
  Measured (`.omc/specs/194-measure.py` ARM 5): the exporter renders the persisted
  `content` text block verbatim — prefix and all — and renders neither
  `error_message` nor the `✖` line, because it has no notion of either field
  (`grep -rn 'error_message\|stop_reason'` over
  `packages/aelix-coding-agent/src/aelix_coding_agent/_export_html/` is empty).
  So an exported failed turn reads as if the model itself typed `[error] …`,
  and this fix made the export the ONLY place a user still meets the marker.
  Deliberately not fixed here — it is a different renderer with a different
  vocabulary, and #194 is one issue — but it is now a measured number rather
  than an open question.
- One case is knowingly left as-is: a session written by a **third party** that
  happens to hold a text block equal to `f"[error] {error_message}"` on an
  error message would have that block hidden. That is the same message this
  repo writes, rendered the way this repo renders it.

## What this does not change

- **The live path.** #189's shell dedup is untouched and its tests still drive
  `run_tui`.
- **The writer.** `harness/core.py` still writes both fields, for the reason in
  §1.
- **`build_display_messages`.** Suppressing there would have fixed the TUI and
  broken any display consumer that renders `content` but not the stop-reason
  line — the "print nothing" direction. Replay is the layer that knows both.
- **Other renderers of a persisted transcript.** Neither `export_to_html` nor
  the print/json modes is touched. The export does NOT double — it shows the
  `content` copy and only that (measured, §Consequences); its problem is the
  opposite one and is its own issue. `modes/print_mode.py:329-332` does not
  double either: on `error`/`aborted` it prints `error_message` to stderr and
  skips the content blocks entirely. Read, not measured by a probe.

## Tests

`tests/tui/test_failed_turn_replay.py` — eleven tests, alongside the live
counterpart `tests/tui/test_first_run_no_provider.py`. **Five** fail on
`fad2e28` (the branch base), measured on this tree with `git show
main:packages/aelix-coding-agent/src/aelix_coding_agent/tui/render.py` written
over the working copy:

```
$ uv run pytest tests/tui/test_failed_turn_replay.py -q -p no:cacheprovider
FAILED ...::test_a_persisted_failure_replays_once
FAILED ...::test_the_surviving_rendering_is_the_live_one
FAILED ...::test_a_failure_whose_exception_carried_no_message_replays_once
FAILED ...::test_a_real_failed_turn_reloaded_from_disk_replays_once
FAILED ...::test_a_real_message_less_exception_reloaded_from_disk_replays_once
5 failed, 6 passed in 0.17s
```

The six that pass on the base pass **by design** and it is worth saying which
way each one points:
`test_a_writer_that_stopped_echoing_still_shows_the_failure` guards the
never-zero direction; `test_a_partial_answer_before_a_provider_error_still_replays`,
`test_a_text_block_that_merely_starts_with_the_prefix_replays`,
`test_a_successful_turn_that_talks_about_errors_is_untouched` and
`test_a_failure_with_no_error_message_at_all_keeps_its_body` guard against an
over-broad fix, which the base does not have; and
`test_the_model_still_sees_the_failure_on_the_next_turn` is the §1 measurement,
which the fix deliberately does not move.

**An instrument trap, and the first draft of the test file fell into it.** The
Markdown emission is wrapped to the replay width, so a long error sentence is
broken across lines inside that one commit, and a plain `sentence in commit`
finds nothing. Written that way, the end-to-end test **passed against the
unfixed renderer** with two emissions on screen. Every search in the file and
in the probe now collapses whitespace first.

**What silently defeats these assertions.** Six sabotages — five inside
`EventRenderer.replay` (four of them the code this ADR adds, plus ADR-0104's
`✖` commit, which the safety argument leans on and which this change did not
write), and a sixth in #189's shell dedup, a different file — each applied to a
working tree that was restored (`diff -q`) afterwards and run with the same
command: `uv run pytest tests/tui/test_failed_turn_replay.py
tests/tui/test_first_run_no_provider.py -q -p no:cacheprovider`, 18 tests:

| sabotage | result |
| --- | --- |
| the `continue` removed (the defect restored) | 5 failed, 13 passed |
| the match widened to `startswith` | 1 failed, 17 passed |
| skip EVERY text block when `stop_reason == "error"` | 3 failed, 15 passed |
| the `✖` commit deleted from `replay` | 9 failed, 9 passed |
| the guard back to `if err:` (the reviewed defect) | 2 failed, 16 passed |
| #189's shell dedup disabled (`if True or …`) | 1 failed, 17 passed |

The last row is why the two files are kept apart: only
`test_first_run_no_provider.py::test_an_escaping_error_reaches_the_glass_exactly_once`
goes red for it, and none of the `startswith` / "skip everything" / `if err:`
rows touches that file at all. Neither file subsumes the other.

**What still would not be caught.** The suppression reads `error_message` and
nothing else, so a future change that makes the WRITER stop pairing the two
fields (a different prefix, a reformatted body) would silently stop matching
and the double-print would come back with no test going red — the tests build
the pair themselves. The e2e pair
(`test_a_real_failed_turn_reloaded_from_disk_replays_once`,
`test_a_real_message_less_exception_reloaded_from_disk_replays_once`) is the
only guard on that, and it guards it only for the two exception shapes it
drives.

## Live check

**Done, on a real terminal** (CLAUDE.md 9). `.omc/specs/194-live.py` seeds TWO
real session files, each with a real failed turn produced by the harness rather
than hand-written, and both trees then open them with a real `aelix`:

```
uv run aelix --session /tmp/aelix-194/failed-turn.jsonl
uv run aelix --session /tmp/aelix-194/failed-turn-no-message.jsonl
```

The agent that wrote this has no TTY, so the TUI was driven through
`os.forkpty` — the child gets its own pty, 120x40, `TERM=xterm-256color` — and
the bytes it painted were captured and counted. The control is not an overlay
or a stubbed renderer but a second worktree checked out at `fad2e28`.

`failed-turn.jsonl` — TWO lines on the base, ONE here:

```
base fad2e28   | [error] The model stream ended without a result: it stopped …
               | ✖ The model stream ended without a result: it stopped …
               '[error]' lines: 1     '✖' lines: 1

this branch    | ✖ The model stream ended without a result: it stopped …
               '[error]' lines: 0     '✖' lines: 1
```

`failed-turn-no-message.jsonl` — the boundary review found (a bare
`TimeoutError()`, so `str(exc)` is `""`), where the base's two lines were not
even the same sentence:

```
base fad2e28   | [error]            ← body emptied, marker left standing
               | ✖ request error
               '[error]' lines: 1

this branch    | ✖ request error
               '[error]' lines: 0
```

The surviving line is unchanged in colour: on BOTH trees the raw SGR preceding
`✖` is `\x1b[1;31m`, bold red, which is what `render.py` asks for. Only the
line count moved.

`--session <path>` takes the startup-paint road into `EventRenderer.replay`
(issue #165); `/resume` and picking the session takes the ADR-0122 road. Both
end in the same method, and only `--session` was looked at — checking either is
enough for that reason, and this records which one it was.
