# 0292. A `/` line Entered during a turn is a command, not a steer

Status: Accepted (2026-11-06, #701).
Date: 2026-11-06
Issue: #701. Owner request (2026-11-06): "턴 진행 중에 / 슬래시 명령어 열어서 조작하고
설정가능하게 하는건. 예를 들어 thinking 변경 등등". Evidence (captures, wire tap):
`.omc/specs/beta3-polish-2026-11-06/tui/701/` and the lane report beside it.
Amends: ADR-0119 (Enter during a turn steers every line but a `!` one, and now a `/` one),
ADR-0159 (the in-flow modal slot stacks) — each by a dated note.
Fix round 1 (2026-11-06; verify V1–V3, V6, V9, Codex 701-*): `/quit` runs mid-turn, an
extension's command runs at once, a template or `/skill:<name>` steers its expansion, a
panel's own close names its modal, and the "applies from" line says only what is true. Fix
round 2 (2026-11-07; verify F2–F4): an extension's panel (its management modal) opens mid-turn,
and a setter run mid-turn says when its change applies on its own line (D5). The Decision and
Limits below are as amended.
pi: `earendil-works/pi@4ffe32c6f`,
`packages/coding-agent/src/modes/interactive/interactive-mode.ts`.

## Context

While a turn runs, the input stays live and Enter steers (ADR-0119): the chrome's accept
handler sends the line to `harness.steer()`, bypassing the input loop, which is parked in
`harness.prompt()` for the whole turn. The command dispatch (`match_command`, the extension
dispatcher, the management modals, `/skill:` and template expansion) lives only in that loop.
So `/thinking high` typed mid-turn — the `/` menu opens and completes it — went to the model
as a steering message, with nothing on screen to say so, and `Alt+Enter` sent it as a
follow-up. The "Can't … while a turn is running." branches in `/resume`, `/new`, `/fork`,
`/clone` and `/import` could never be reached from the keyboard. Measured before the fix
(scripted provider, 120x40 pty): `/thinking high`, `/new` and `/thinking` each showed as
`Steering: …`, and the next request carried
`"write a long answer\n\n/thinking high\n\n/new\n\n/thinking…"` as the user's text.

pi's `onSubmit` (`:3315-3458`) dispatches every built-in before it looks at `isStreaming`;
a handler that cannot run then refuses (`handleReloadCommand`, `:6576`: "Wait for the
current response to finish before reloading."). Only what is left goes to
`prompt(text, { streamingBehavior: "steer" })` (`:3493-3500`), which runs an extension
command at once and expands a template into the steer. pi's Alt+Enter (`handleFollowUp`)
does not dispatch built-ins: there `/thinking high` + Alt+Enter is a follow-up message.

Two facts decide what "run it now" can mean. The model and the thinking level are read
once per run: `AgentLoopConfig(model=…, reasoning=…)` is built when `prompt()` starts
(`harness/core.py`), and a steer joins that run. And the harness already queues the session
writes a mid-turn change makes (`PendingModelChangeWrite`, `PendingThinkingLevelChangeWrite`).

## Decision

### D1. A typed `/` line is a command during a turn, as it is at idle

Enter and Alt+Enter on a line the input drew as a `/` command, while a turn the input loop
started runs, never reach the model as the typed text. The chrome asks the loop first
(`AelixChrome.on_mid_turn_command`, synchronous; Alt+Enter's line carries `after_turn`):
`("ran", "")`, `("refused", why)`, or `None` — not a command, and the line steers or follows
up as before. The loop resolves the line in the idle path's order: a built-in, an extension's
command, a management modal, a `/skill:<name>` or `/<template>`, else unknown. A collapsed paste that
begins with `/` is text (#667, `literal`), a `!` line is ADR-0287's, and a line Entered while a
*command* holds the loop (`/compact`, `/agents run`) is queued as the next input, as #604 D15
made it.

### D2. An explicit table, not handler refusals

The chrome has to know at Enter whether to clear the input or keep the draft, and a
handler runs later, in a task. Five handlers carried a busy check and the rest none, so
"let the handler refuse" would have run `/clear`, `/compact` or `/login` under the stream. So
`tui/commands.py` holds the table for the built-ins: `MID_TURN_RUNS` (run now) and
`_MID_TURN_REFUSED` (why not, as the row under the input says it), read by
`mid_turn_refusal(command, args)`. A built-in in neither is refused, and a test fails until a
new built-in is placed. An extension's command, a template and a skill are pi's: they run or
expand as pi's `prompt()` does.

| Runs now | Why it is safe mid-turn |
| --- | --- |
| `/thinking`, `/model` (and their pickers) | read by the next run; the session write is queued by the harness |
| `/settings`, `/scoped-models`, `/statusline`, `/trust` | settings files, the footer, the next launch |
| `/permissions`, `/mode` | the posture and the steering mode; shift+tab already works mid-turn |
| `/tool`, `/skill` | the panel defers its apply to the turn's end (ADR-0279 D6) |
| `/help`, `/hotkeys`, `/cost`, `/stats`, `/session`, `/context`, `/hooks`, `/mcp`, `/tree`, `/expand`, `/copy`, `/export` | they show or copy, and write nothing the turn reads |
| `/name` with no text, `/agents`, `/agents list`, `/agents show <name>` | read-only forms |
| `/quit`, `/exit` | it stops the answer (as a signal does: `abort_until_idle`) and the loop returns once `prompt()` does, with no done line; pi runs `/quit` ahead of its streaming branch (`interactive-mode.ts:3455`, `:3493`) |
| an extension's command | it runs now, in a task of its own, through the idle path's dispatcher, and nothing reaches the model; pi `agent-session.ts:2144-2153` |
| an extension's management modal (its panel) | it opens through `show_modal`, as the `/skill` and `/tool` panels do: a dialog the turn opens goes over it and hands it back, and its own close names its modal (D7); an action picked in it reaches the extension at once, as its command would (verify F4: it was refused, with no reason the stacking slot left) |
| `/skill:<name>`, `/<template>` | expanded as at idle, and the expansion is steered (Alt+Enter: queued as a follow-up); the pending row and the echo show the typed line, as the idle echo does; pi `agent-session.ts:2196-2198` |

| Refused | The row under the input says |
| --- | --- |
| `/reload` | it rebuilds the session the answer is running in |
| `/new`, `/resume`, `/import` | it replaces the session the answer is running in |
| `/fork` | it starts from the session the answer is still writing |
| `/clone` | it copies the session the answer is still writing |
| `/compact` | it rewrites the context the answer is using |
| `/clear` | it clears the screen the answer is being written to |
| `/login`, `/logout` | it changes credentials the answer may be using |
| `/extension` | it installs and scaffolds extensions |
| `/name <text>` | renaming writes the session file the answer is still writing |
| `/agents use`, `/agents run`, any other `/agents` form | it switches the profile or starts a delegation |
| a skill that is off | its "is off — /skill to turn it on" line |
| anything else | `Unknown command: /x — type /help`, as at idle |

Each refusal reads `/<word> can't run until the answer ends — <why>`.

### D3. A refusal keeps the draft and says why under the input

The line stays in the input; the status row shows the reason, in yellow, while the input
still holds that line and the turn runs. An edit, a send or the turn's end takes it away,
and Enter then runs the command as at idle. Nothing is committed to the transcript: the
answer is still being written there.

### D4. A command that runs, runs beside the turn

It runs in a task of its own; the input loop stays parked in `prompt()`. Esc and Ctrl+C keep
interrupting the turn: the two commands that take the interrupt for themselves
(`run_cancellable`: `/compact`, `/agents run`) are refused. `run_tui` holds the tasks, and one
still open when the TUI closes (a picker, a setter still awaiting, an extension's command) is
cancelled first in its teardown (Codex 701-handler-survives-quit: it outlived `/quit`).

### D5. What changed applies from the next message, and the transcript says so

A setter of the model or the thinking level run while the turn still runs says when the change
applies on its own line, dim: `thinking → high · applies from your next message (or a retry of
this one)`, where idle it leaves the green `thinking → high`. The same goes for `/model <id>` (`model → gpt-4o
(openai) · applies from …`), the `/thinking` and `/model` pickers, and the `/settings` thinking
row (`thinking level → high (persisted as default) · applies from …`); a warning (`⚠ switched to
…`, a model with no provider) keeps its yellow. The input loop sets `tui/mid_turn.MID_TURN` in the
task the command runs in, and `setter_line` reads it where the line is committed: a picker held
open past the turn's end says the idle line. Both values are read when a run of the model starts
(`AgentLoopConfig`): a steer, a tool result or a follow-up joins the run under way and keeps what
it started with, and an auto-retry of the same message starts a new run and reads the new value
(Codex 701-thinking-retry). The line says nothing of the answer under way: it can land under that
answer once it is over (D6), and the first wording ("The answer being written keeps thinking
medium") was then stale, or false after a retry (verify V9). Fix round 1 added the claim as a
second line under the setter's, "Applies from your next message, or a retry of this one:
thinking high.", which said the same thing twice (fix round 2: one line). The footer shows the
new value at once. Applying it to the next request inside the same run would mean re-reading
both in the loop; not taken (Alternatives).

### D6. What it prints goes under the answer

Committed as it ran, a command's line landed above the whole answer the live window was
still drawing — between the user's line and the answer. While a mid-turn command is open
(its task, and the picker or panel it awaits), `_commit` sends `after_stream`: the output
pump holds those lines while the live window has text and commits them under it when the
window is committed and cleared, or when the turn ends (`release`). The renderer's own
commits go straight through (`_stream_commit`). A panel that closes while the window is up
prints its summary through the queue instead of straight above the chrome.

### D7. The modal slot stacks

A picker opened mid-turn can be under a dialog the turn opens (a tool approval). The slot
used to be one: `mount_modal` replaced the picker, and when the dialog closed the picker's
future was never answered. Now `mount_modal(content, focus=…)` keeps the modal it covers,
`unmount_modal(content)` hands the slot back to it and focuses it, a covered modal that
leaves only leaves the stack, and a second unmount of the same modal does nothing.
`show_modal` refocuses the input only when no modal is left. Measured: `/thinking` picker
open, the turn asks to run `echo approved-run`, `1` answers it, the picker comes back, `high`
is picked and the next request carries `reasoning_effort: high`.

No close may take the modal on top as its own. The `/skill` and `/tool` panels and the
tabbed host (`/extension`) unmounted the slot with no argument when their save or close
finished; with a tool approval mounted over a panel whose save was still running, that took
the approval down and left its future pending, so the turn waited on a dialog nobody could see
(verify V3: 12 of 13 runs). Each now unmounts the window it built (`unmount_modal` matches the
mounted container or the window it focuses) and refocuses the input only when no modal is
left. Measured with the verify probe (real chrome, panel and `show_modal`, a save held 0.3 s):
30 of 30 runs keep the approval up and focused.
An extension's management modal closes through `show_modal`, which names its modal too, so
it opens mid-turn (verify F4). Measured with a probe (real chrome, `open_modal` and
`show_modal`) that mounts the approval after the modal's Esc or action key has resolved it and
before its unmount: 30 of 30 runs each keep the approval up and focused, and 30 of 30 hand the
modal back, focused, once the approval is answered; with an argumentless unmount the closed
modal stays in the stack under the approval.

## Consequences

- `/thinking`, `/model`, `/settings` and the other D2 commands work while the model answers;
  the change shows in the footer at once and reaches the next request (the next message, or a
  retry of this one, D5), never the answer already streaming.
- No `/`-command string reaches the model from the input during a turn. Live
  (a real openrouter model, wire tap on `before_provider_payload`): requests 1
  and 2 (the steer) ran with `reasoning: {"effort": "none"}`, request 3 (the next message)
  with `{"effort": "high"}`, and no request's messages held `/thinking` or `/new`.
- Alt+Enter on a command behaves as Enter, which pi's does not.

## Limits

- An extension's command, and an action picked in its panel, run mid-turn whatever they do:
  nothing declares them safe, as in pi. One that calls `ctx.reload()`, `ctx.new_session()` or
  `ctx.wait_for_idle()` waits for the answer to end (the runtime's own idle wait) or raises as
  at idle.
- A template's or skill's expansion steered mid-turn is what Alt+Up gives back to the
  input (the harness queues only the expansion), as pi's restore does.
- A refused line is not queued: Enter it again once the answer is over.
- RPC and json `steer` with a `/` text, and a turn an extension starts (#674, the chrome is
  not running), keep their behaviour; this is the TUI input's.
- What a mid-turn command prints is held while the answer's live window shows text and
  lands under it when the window is cleared: at the end of the message being streamed (or of
  a thinking block), or at the turn's end (`release`). The stable lines the stream commits
  as it goes do not release it, so on a long answer the line waits for the answer's end
  (verify V9).

## Alternatives

- **pi's shape: dispatch everything, let handlers refuse.** Rejected for D2's reasons: the
  draft decision has to be synchronous, and most handlers have no busy check.
- **Queue a refused command to run after the turn.** Considered for Alt+Enter, which queues
  a `!` line that way (ADR-0287 D6). Not taken: a queued `/new` or `/reload` would fire
  unattended after an answer the user has not read.
- **Commit the refusal to the transcript.** It would land in the middle of the answer.
- **Re-read the model and level per request inside the run.** A loop change for a choice
  pi does not make either; the next message is when a change applies.
