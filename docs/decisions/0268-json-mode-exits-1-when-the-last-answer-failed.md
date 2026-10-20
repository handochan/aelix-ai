# 0268. `--mode json` exits 1 when the run's last answer failed, as `-p` does

Status: Accepted (2026-10-20, the owner's decision on #410; §1's second half added in
review round 2, 2026-10-21)
Date: 2026-10-20
Issue: #410 (this change); #379 (the OAuth refresh retry whose exhaustion the issue
came from); #391 (`-p` that ends with no output and exit 0, B4, not this decision)
Amends: ADR-0198 §D5, its first clause, "JSON mode never exits non-zero on an
assistant error" (dated note there); ADR-0197 §(k), the sentence that repeats it
(dated note there); ADR-0251, the bullet "`--mode json` exits 0 when every retry
fails" and §12's `rc 0` measurement (dated notes there); ADR-0201, row 2 of the turn
contract ("exit code 0") and the channel table's `exit_code` paragraph (dated notes
there). The rest of each stands, including D5's consumer rule.
Relates: ADR-0089 (print mode), ADR-0235 (pi is a reference, not a target; a
divergence is recorded, not required), ADR-0238 and #220 (the signal exit codes,
unchanged), ADR-0180 (#57, exit 141 for a vanished stdout consumer, unchanged)

## Context

`run_print_mode` turned a failed last answer into exit 1 inside `if mode == "text"`,
as pi's `modes/print-mode.ts` still does (`origin/main` `fdce48eb4`, 2026-10-20). So a
`--mode json` run whose retries ran out exited **0**. Measured on `7faf4b9a` with the
real `aelix` against a local server that answers every request `502` (fake key,
`"retry": {"maxRetries": 2, "baseDelayMs": 5}`, isolated agent, settings and
session dirs; kit in `.omc/probes/410-live/impl/`):

```
502 text rc=1 stdout_lines=0  stderr=<html><body>502 Bad Gateway (probe)</body></html>
502 json rc=0 stdout_lines=26 stderr=
ok  text rc=0 stdout_lines=1  stderr=
ok  json rc=0 stdout_lines=14 stderr=
```

The json stream did say it: the last `message_end` had `stop_reason: "error"` and
`auto_retry_end` had `success: false`. But a script that runs `aelix --mode json` and
checks `$?` saw a success. The same held for a `/login` refresh that kept failing
after #379.

pi documents its behaviour (`docs/cli-integration.md`): "A failed or aborted
assistant response appears in the event stream but does not by itself produce a
nonzero exit status. Inspect the events when success or failure matters."

pi's text rule reads the session's last message whoever wrote it. Review round 2
found what that means for a resumed session (`.omc/probes/410-live/r2/`, the
`f1_*.out` files): a session whose last answer failed in an EARLIER run, continued
with `--continue` and no prompt, or with a prompt an extension's `input` hook handles
(no turn runs, no request is made), exited 1 in text mode printing the old error, and
on the first commit of this change in `--mode json` too, with nothing on stdout or
stderr to say why.

## Decision

1. **One rule, both modes.** `-p` and `--mode json` exit 1 when the session's last
   message is an assistant message whose `stop_reason` is `"error"` or `"aborted"`
   **and this run appended it** (`print_mode._final_failure`, read once by step 8 for
   both modes).
   - The first half is pi's text rule exactly: the LAST message
     (`state.messages[-1]`), not the last assistant message found by searching back,
     so with several prompts the last one decides, a prompt whose retries ran out
     before a later one succeeded does not, and a session whose last message is not
     an answer exits 0. This covers a provider error that outlasted the retries, a
     refresh that kept failing until the retries ran out, any other final `"error"`,
     and an `"aborted"` answer that no signal caused.
   - The second half is aelix's, in both modes: a failed answer an earlier run left
     is history, not this run's verdict. The run notes the session's messages just
     before its first prompt, and a last message that is one of those exits 0; text
     mode prints nothing for it (a failed answer is never printed as an answer).
     They are matched by identity, not counted: a compaction during the run
     rebuilds `state.messages` in place from the session branch, so the list can end
     shorter than it started while its last message is this run's failed answer,
     and the rebuild keeps the message objects (a session entry holds the object
     that was appended).
   - `"length"` (the answer hit the output token limit) is not a failure in either
     mode: the partial answer is printed and the run exits 0 (see *Not covered*).
2. **Nothing else about the exit code moves.** A signal still overrides the 1 with
   `128 + sig` (#220); an exception still exits 1 with its message on stderr; a stdout
   consumer that went away still exits 141 (#57); a run that succeeds exits 0.
3. **JSON mode writes nothing extra, on stdout or stderr.** Stdout is the event stream
   and it is unchanged: the probe's json stdout before and after this change is equal
   line for line once `id`, `created_at` and the `timestamp` fields are masked (the
   only paths that differed). Text mode prints the error to stderr because its stdout
   carries only the answer. In JSON mode the stream already carries the failed answer:
   its `turn_end` has the `stop_reason` and `error_message` (an aborted answer has no
   `message_end`, ADR-0201 row 3). The one consumer in this tree reads stderr as
   evidence: the delegation parent puts a JSON child's stderr tail second in its
   summary chain (`error_message`, then stderr, then the streamed summary) and appends
   it to `details`. Measured with `envelope.build_result` (`stderr_rung_probe.py` in
   the kit): an aborted child with a partial answer and no `error_message` reports
   `summary='half of the answer'` when stderr is empty and `summary='Request aborted'`
   when stderr carries text mode's line, and an errored child's `details` gains
   `--- stderr ---` with its error a second time. A delegation stopped by its parent
   is exactly that aborted child, so copying text mode would replace the partial
   answer that `_select_summary` keeps for an aborted or timed-out run with the words
   `Request aborted`. The same holds when stderr is a terminal. Pi's framing (stdout
   is JSONL, diagnostics go to stderr) does not require a line either way.
4. **This diverges from pi on purpose** (ADR-0235), twice: pi returns 0 for this case
   in JSON mode and documents it, and pi's text mode exits 1 on a failed answer an
   earlier run left (read, not run: `print-mode.ts:139-147` judges
   `state.messages[state.messages.length - 1]`, and `main.ts:987` runs print mode
   with no message too). The reason for the first is the owner's: a script must see
   an exhausted retry in `$?`. The reason for the second is that an exit code reports
   what the run did, and a run that asked the model nothing did not fail.

## Consequences

- `aelix --mode json` and `aelix -p` agree on the exit code of every run whose stdout
  stays open. When the stdout consumer is gone they differ, as before this change: a
  failing text run writes nothing to stdout and exits 1, while a json run hits the
  closed pipe on its first event and exits 141 (#57). Measured after the change, with
  the same probe: `502 json rc=1`, the other three rows as before.
- A script that relied on JSON mode exiting 0 for a failed answer now sees 1.
  The CHANGELOG lists it under Changed.
- `aelix -p --continue` with no prompt, or with a prompt an extension handles, on a
  session whose last answer failed earlier, exits 0 and prints nothing, where it
  exited 1 and printed the old error (measured, `f1_c7.out` against `f1_tree.out`).
  A run that does make a turn on that session is judged on that turn's answer, as
  before.
- Delegation (`aelix_agents`): a JSON child whose last answer failed now reports
  `exit_code=1` on its `SubagentResult` and in its parent's receipt, and its
  `/agents` row ends as `error` instead of `done` (`PrintChannel` sets the row from
  the exit code). The verdict itself does not change: `build_result` already failed
  the run on `stop_reason`, and still does, since a child from an older release still
  exits 0. The rpc channel is unchanged: an rpc child's exit code is the end of its
  server, not of a turn.
- `tests/cli/test_json_mode_exit_code_410.py` pins the rule through the real CLI
  (`_async_main`: provider 502 and refresh 502 exhausted, a recovered run, an
  exhausted prompt followed by a successful one, and a continued session whose last
  answer failed earlier, with no prompt and with an extension-handled prompt), through
  `run_print_mode` (error, aborted, ok, length, several prompts, a last message that
  is not an answer, a compaction that shortens the session during the run, json
  stderr on a terminal; in both modes, with nothing on stderr and only events on
  stdout in JSON mode), and through a real `--mode json` child read by
  `PrintChannel`. Each mutant named in the commit message turns at least one row red.

## Not covered

- **`stop_reason: "length"`.** pi `441f5767c` (2026-10-12) makes text `-p` print the
  partial answer, warn on stderr and exit 1 when the answer hit the output token
  limit. aelix exits 0 for it in both modes, and a test row pins that; a follow-up
  decides whether to follow.
- **A run that switches session.** The messages noted before the first prompt belong
  to the session the run started on. If an extension command switches to another
  session during the run, that session's messages are loaded as new objects, so its
  last message is judged as before this change. Not measured.
- **#391**: `-p` that ends with no output and exit 0 lives in `harness/core.py` (B4).
- **`aelix --help`** names no exit codes and is not changed (`cli/args.py`). The
  getting-started guide's "Exit codes" section is where they are written down.
