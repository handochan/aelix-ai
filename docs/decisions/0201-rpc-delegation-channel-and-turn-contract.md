# 0201. The RPC delegation channel, and the turn contract it needs

Status: Accepted (2026-07-31) — owner-ratified protocol fork (D2), owner-ratified
band reading (D1), owner-ratified kernel scope (D3/D4). Design record that lands
**after** the implementation, deliberately: see *Why this record was written last*.
Date: 2026-07-31
Builds on: ADR-0197 (the subagent-runtime seam and the 3-band rule this ADR
re-reads rather than amends), ADR-0198 (**this ADR delivers the `rpc` half its
*Partial fulfilment* section named as a follow-up, and the cross-channel parity
test with it**), ADR-0199 (the fan-out governance whose `permission_floor` is why
one-child-per-task is a security property and not a preference).
Relates: ADR-0056 (RPC JSONL framing — the LF-only discipline the per-direction
budget below had to preserve), ADR-0057 (RPC types & envelope), ADR-0058
(**whose `P-119 / W4 m2` carry-forward this sprint retired as a phantom** — see
`9e3f096`), ADR-0002 (small kernel — one authorised file, one hunk).
Implementation: `d4a920e`, `9e3f096`, `9d960d3`, `0eb5204`.
Working log (measurements, landmines, what was refuted): `.omc/specs/rpc-sprint-log.md`.
Pi pin: `earendil-works/pi@734e08e`.

> **Numbering note.** This record was written as ADR-0200 and renumbered to 0201 at merge
> time: `main` had independently landed ADR-0200 (catalog fetch classification and the
> installer backend, W1-A) while this sprint ran on its own branch. The commit messages on
> `9d960d3` / `0eb5204` / `e54dc8b` predate the rename and still say 0200; this file, the
> index and the band gate are the authority.

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적
목표입니다."** This record therefore separates, for every behaviour it describes,
*ported from pi*, *restored to pi*, and **aelix-original**. That separation is the
main thing it exists to write down: the sprint began by discovering that a
citation nobody had checked had sent a work item to the wrong layer for six
sprints, and the cure for that is not more prose but labelled provenance.

**Anchor convention.** Symbol names are preferred over `file:line`. The line
citations in this repository have already rotted — `batch.py` cites
`runtime.py:478-490` for a block now at `705-721`, and six files cite
`runtime.py:481` for `_new_id`, now at `719`. Anchors here are evidence for a
decision, not a maintenance contract.

---

## Why this record was written last

ADR-0058 claimed `rpc-mode.ts:379-401` "emits a synthetic terminal event".
Independently re-measured at the pin: `grep -nE "agent_end|synthetic"` over that
file returns **zero hits**, at both the pinned and the current upstream revision.
The claim was false in every clause, it propagated into an ADR, and it sent the
missing turn terminator to the wrong layer for six sprints.

So this record was written **after** the code, and every claim in it is either a
measurement or a citation to a test that fails when the claim stops being true.
Writing it first would have reproduced the exact failure mode the sprint had just
finished retiring.

---

## Scope

**In.** The rpc half of the channel-envelope contract; the turn contract a
delegating parent needs; `RpcChannel` as the second implementation of the channel
seam; the seam itself; the kernel's abort terminator; and the band-gate decisions
all of that required.

**Out.** Any user-facing way to *select* the rpc channel — see *Deferred*.

---

## The owner decisions

### D1 — RPC belongs in product-core, fully implemented

The sprint's kickoff framed its central question as *"how much product-core may
this sprint touch?"* and offered a ladder from "zero delta" upward. **The owner
rejected the framing, not an option:**

> "rpc는 원래 패리티 충실하게 코어에 구현되는게 맞는거죠? 그리고 모든 기능들도
> 충실이 구현되어야 하구요."

The correction is exact and worth preserving, because the ladder was a
misreading of ADR-0197. The 3-band rule forbids **delegation/spawn *policy***
leaking into product-core — depth threading, `--no-agents`, consent, per-prompt
caps. It does **not** make product-core a sanctuary. P1/P2/P3 shipped a zero
product-core delta because all three sprints' work *was* policy, not because core
was untouchable.

So the rpc turn contract, the missing terminators, `_handle_prompt` dropping
`cmd.images`, the absent reader `limit=` and the absent child-death detection are
**core's own unpaid debt**, and they were fixed in core. What stays in
`aelix_agents` is unchanged: depth, `--no-agents`, consent, caps, argv/env policy.

The measurable consequence of D1 is the shape of every seam in *§The seams*
below: product-core got the **mechanism** and the extension kept the **decision**.

### D2 — Keep the pi-shaped JSONL wire. No JSON-RPC, no ACP

Researched as a first-class fork, because it decides what the channel and the
parity test are built *against*. Four ground-truth agents, three independent
option designs, three adversarial judges; notes at
`.omc/specs/rpc-protocol-{recon,option,judgment}-*.md`, brief at
`.omc/specs/rpc-protocol-decision-brief.md`.

The decisive finding: **the blocking defects are semantic, not structural.** They
cost the same ~15–80 lines in any envelope, and a standard envelope fixes none of
them. The standard-protocol option's own author measured its interop dividend at
**zero** — JSON-RPC carrying snake_case kernel dataclasses under an `aelix/*`
namespace is a standard nobody speaks. External obligation is also zero: no tags,
no releases, no `aelix-web`, and `aelix-server` is `rm -f`'d from the publish set
by `release.yml`.

Borrowed *inside* the pi envelope so a future adapter is a rename rather than a
redesign: ACP's `StopReason` vocabulary, and JSON-RPC's `-32000…-32099` error band.

### D3 — Fix the kernel parity regressions. D4 — no `dialect:` seam

D3 authorises exactly one kernel file, `harness/core.py`, for exactly one thing:
an aborted turn emitted **no terminal event at all**, so every `agent_end`-waiting
parent hung for its full 60 s. D4 refuses an abstraction with one implementation.

---

## The turn contract

Six ways a delegated turn ends. The first five are the ones the plan had; **the
sixth is the one it did not, and it is the one that costs the most.**

| # | How the turn ends | What the parent observes |
|---|---|---|
| 1 | Normal completion | `agent_end`, after the real `message_end` |
| 2 | The model errored | `message_end` with `stop_reason: "error"`, then `agent_end`, **exit code 0** |
| 3 | The turn was aborted | `turn_end` + `agent_end` carrying the REAL message, `stop_reason: "aborted"` |
| 4 | The command was refused before the turn started | a correlated `success: false` response, and **no** terminator |
| 5 | The caller's budget expired | nothing; the parent's own deadline fires |
| 6 | **The child process died** | **nothing at all, until the fix below** |

**Row 3 is the kernel change (D3), and its shape is not the obvious one.** pi's
*primary* abort path is `agent-loop.ts:196-199`: emit `turn_end` + `agent_end` on
the real streamed message, then **return normally**. `emitRunFailure` is the
fallback for a genuinely *thrown* error. Routing an abort into the existing
four-event closure block would emit a synthetic `message_start` + `message_end`
**on top of the real `message_start` that already went out** — measured live with
65 `message_update`s already streamed. pi never produces that shape, and the
synthetic message persists into the session file with the literal body
`"[error] "`, because `str(CancelledError()) == ""`.

Two more things about row 3 that cost time to learn and are cheap to write down:

* `asyncio.CancelledError` is a **`BaseException`**. "Widen the filter to bare
  `except Exception`" delivers *none* of the fix — built verbatim, `agent_end` was
  still absent with an event list byte-identical to baseline.
* The outer filter must **not** be widened to `BaseException`. Abort is handled in
  the inner handler and returns first; the four consumers that catch this
  (`tui/shell.py`, `modes/print_mode.py`, `rpc/rpc_mode.py`, `harness/core.py`'s
  own `dispose`) all filter on `Exception`, and a re-raise makes Esc kill the REPL.

**Row 4 is why the channel opts into strictness.** MEASURED at the pin: pi's
`send` (`rpc-client.ts:474`) returns the raw response, and the throw lives in the
separate `getData` (`:506-508`) — so `prompt` / `steer` / `abort` call `send`
alone and **discard** a server-side refusal. aelix ports that asymmetry
faithfully, and it is a trap for an unattended caller: the server's busy preflight
refuses a second `prompt` with exactly this shape, and a caller that discards it
waits for a terminator that is never coming. Hence
`RpcClientOptions.raise_on_command_error`, **default off to stay faithful**, and
`RpcChannel` turns it on.

**Row 6 is aelix-original.** pi has no child-exit observation after `start()`:
its whole 515-line client has two `.on(` calls — stderr `data`, and an `exit`
handler *inside* `stop()`. Its only liveness check is the same one-shot 100 ms
grace aelix already ported. MEASURED without the fix: a child exiting at 400 ms
left `prompt_and_wait(timeout_ms=5000)` blocked for **5.02 s** and then raised a
bare `TimeoutError()` with empty args, while `proc.returncode == 7` had been
readable the entire time. At the idle default that is 60 s; at a delegation budget
it is ten minutes. Every reason a real child dies — a bad `--tools` name (measured:
an uncaught traceback in rpc mode, so the child dies *before the transport
exists*), no API key, an import error, an OOM kill — lands outside the 100 ms
window.

The two legs of row 6 are also **normalised to one exception**. Before: a command
issued to an already-dead child raised `RuntimeError` in 0.00 s off the broken
stdin pipe, while one issued moments earlier blocked the full budget and raised
`TimeoutError`. One fact about the world, two unrecognisably different failures,
and a caller would only ever discover whichever it hit first.

---

## The rpc half of the envelope contract

`--mode rpc` and `--mode json` serialise the **same** kernel dataclasses through
`dataclasses.asdict`, so the event shapes are identical and there is no field
mapping. What differs is everything around them.

| Field | `--mode json` (ADR-0198) | `--mode rpc` |
|---|---|---|
| `exit_code` | the child's status; it exits when done | the child exits when **told**, so the status describes teardown, not the turn |
| terminator | pipe EOF *and* `agent_end` | `agent_end` alone; the pipes outlive the turn |
| `dropped_lines` | `LineAssembler`'s per-line budget | opt-in per-direction budget, republished by the client |
| `stderr_tail` | a 64 KiB ring | the client's capture, capped to the same 64 KiB by the channel |
| `elapsed_ms` | from `run()` entry | the same, and it additionally covers a handshake |

**`exit_code` must not be trusted on either channel, and on rpc it means less.**
`build_result` already tightens a proposed `ok` when the stream disagrees — a
child whose model errored exits 0 with empty stderr while the stream carries
`stop_reason: "error"` (row 2). On rpc the exit code additionally cannot describe
the turn at all, because the turn ends while the process lives.

**`dropped_lines`: the budget is per-direction, and that is a correctness
requirement rather than a tidiness one.** `rpc/_jsonl.py` serves **both**
directions — the client reads a child's stdout with it, the server reads its own
stdin with it. MEASURED with an unconditional budget: a 5000-char command at a
2048 budget **vanished with no signal at all**; the server emits no error response
for a line it never saw, so the client's request future never resolves and the
caller eats the full 30 s send timeout. That is the exact class of silent-failure
defect this sprint exists to eliminate.

So the budget is a **constructor argument defaulting to `None`**, and the server's
intake does not opt in. **Binding for anyone adding one later: a budget on the
intake direction must emit an explicit over-budget error response, never a silent
drop.**

One more thing the budget had to learn: a check on the un-terminated tail alone
enforces **nothing** for any line shorter than the read chunk. Measured — a
500-char line at a 64-byte budget sailed straight through, because its terminator
arrived in the same chunk and the check was never reached.

---

## `RpcChannel`: one child per task

The kickoff called for a "long-lived" channel, and the obvious reading — one child
answering many delegations — **cannot be built**. Three measured blockers:

1. **The 29-command rpc surface cannot re-target a live child.** There is no
   `set_permission_mode`, `set_system_prompt`, `set_tools` or `set_cwd`. Every
   `SpawnPlan` field that varies per delegation — the resolved profile, the
   consent-resolved posture, the contained cwd — is startup-only.

   **This is a security consequence.** ADR-0199 §3.9's `permission_floor` exists
   so a batch's later waves honour a posture the human tightened *after* wave 1
   started. On a reused child there is no command to tighten with, so the floor
   would be silently inert while `SubagentResult.permission_mode` reported a
   posture the child was not running under.

2. **The runtime deregisters a child that outlives its delegation.** MEASURED
   against the real `_SubagentRuntimeImpl`: a child surviving `run()` was
   invisible to `list` / `status` / `stop` / `stop_all` and to teardown —
   verbatim the orphan ADR-0197 forbids and that `stop_all`'s own docstring says
   P3 already measured and fixed once.

3. **`set_model` — the one mutable knob, and the last argument for reuse — does
   not work on a spawned child at all.** `cli/entry.py`'s rpc branch passes no
   model registry, so the child answers `set_model` / `cycle_model` /
   `get_available_models` with "no registry configured". The model must come from
   the profile's `--model` / `--provider`.

**What the channel therefore buys over `PrintChannel`, stated honestly:** a live,
bidirectional command channel to a *running* turn — an `abort` the child
acknowledges rather than a signal, `steer` and `follow_up` at turn boundaries, and
a correlated request/response surface for a future chain mode. On a strict
one-shot path it buys none of that and costs the whole containment list. That is a
real cost and this record does not hide it.

### Two argv/env decisions that are not obvious

* **The task goes over the wire, not on the argv.** `profile_to_argv(oneshot=False)`
  *silently drops* its `task` argument. An author "fixing" the builder to pass it
  would change nothing while believing otherwise — and would put the user's prompt
  on a command line visible to every process on the box.
* **`--no-session` must be appended by the channel.** The oneshot prefix supplies
  it; the rpc prefix does not. MEASURED: without it the child writes a real
  `~/.aelix/sessions/…jsonl` even with `AELIX_CODING_AGENT_DIR` pointed elsewhere,
  and under ADR-0199's `MAX_DELEGATIONS_PER_PROMPT = 12` that is twelve session
  files per prompt in the user's `/resume` picker that they never started.
* **`AELIX_STDIN_TIMEOUT` is deleted, not set.** It is inert on this path today
  (`entry.py` reads piped stdin only for print/json), but here stdin is the
  **transport**, so the two ways of being wrong are not symmetric: if that flag
  ever became mode-agnostic, a one-second stdin timeout would tear the command
  channel down a second after the child booted.

---

## The seams (D1 in practice)

| Seam (product-core) | The decision it carries (extension) |
|---|---|
| `RpcClientOptions.argv` | the complete command line; the shipped default points at the umbrella package's **mock-echo demo**, not the CLI |
| `RpcClientOptions.env_base` | a complete environment, because `env` can only add or overwrite — a DELETION was inexpressible, which made the mandatory `AELIX_MCP_CONFIG` pop impossible and every child would fan out its own copy of every configured MCP server |
| `RpcClientOptions.preexec_fn` | the parent-death signal, whose implementation lives in band 3 and which product-core may not import |
| `RpcClientOptions.raise_on_command_error` | row 4 above |
| `RpcClientOptions.send_timeout_ms` | a cold real child is ~25 s to its read loop, ~18.6 s of it import time; pi's 30 s default made that a coin flip |
| `RpcClient.process` | the reaper, the descendant walk and the registry row all take a `Process`, and none can be built from the public surface otherwise |

`start_new_session=True` is **not** a seam — it is unconditional, because without
it the child joins the parent's process group and one Ctrl+C SIGINTs every rpc
child at once, with nothing to convert that into a response.

### `SubagentChannel`

Measured, not designed: an AST walk over `runtime.py` and `extension.py` finds
exactly **one** attribute read on the channel object — `run`. The suite already
injected bare duck-typed stand-ins, so declaring the Protocol was a
zero-behaviour-change replacement for two concrete annotations.

**The contract is wider than the signature, and this is the first place it is
written down:**

* `run` must **not raise** except on cancellation — the runtime's call site is
  `try/finally` with no `except`, so an escape reaches the harness's tool
  dispatcher on the single-task door.
* `run` must leave `child.state` **terminal** before returning.
* `run` may mutate `child.stream` in place; that is what the progress taps publish.
* `child.proc` must hold a **real process**, because `stop` / `stop_all` /
  teardown bypass the channel entirely and hand it to the reaper.

---

## The cross-channel parity test (ADR-0198's other follow-up)

One scripted agent body, run through a `--mode json -p` one-shot reading its task
from argv and a `--mode rpc` server reading it off the wire, must produce the same
`SubagentResult`. **No model and no network**: the events are scripted, so what is
under test is the channel.

Three forms: the happy path; a failing agent (`stop_reason: "error"` with exit 0,
which is the case that proves neither channel trusts the exit status alone); and
tool narrowing, because `dropped_tools` is a security-relevant field.

Excluded from the comparison, deliberately: `id` (minted per delegation),
`elapsed_ms`, `exit_code` (see the table above), and `details` (carries stderr,
which the two children write differently).

---

## Band-gate decisions

* **`_KERNEL_CHANGE_ALLOWLIST`** gains nothing here; `harness/core.py` was already
  its single entry, added by `d4a920e` with the reasoning quoted in place. The
  gate is a freeze-by-exception, not a blanket allowlist, so every other kernel
  file still fires.
* **`_SPAWN_ALLOWLIST`** already exempted `rpc/rpc_client.py` wholesale, which is
  why the containment kwargs needed no gate change. A spawn in a **new**
  product-core file would still be red by construction, and that remains correct.
* **`_PRODUCT_CORE_CAP_ALLOWLIST` was not amended, and that was a design choice.**
  Every idiomatic name for a framing budget trips `_CAP_NAME_RE`, and the
  tempting workarounds are both wrong: a name that *hides* what the constant is
  defeats a gate that exists to catch exactly that drift, and a leading underscore
  exempts it silently. So the budget and the reader limit are **parameters with
  `None` defaults**, letting band 3 supply the numbers
  (`stream.MAX_LINE_BYTES`, `print_channel.STREAM_LIMIT_BYTES`). The policy number
  stays where the band rule wants it and no new core constant exists to argue about.

---

## What is NOT fixed, and why

**`agent_end` carries no correlation id — no session event does.** Two overlapping
turns on one client cannot be told apart, and a stale terminator resolves the
wrong waiter. MEASURED: a delegation returned another's answer, another's token
usage and another's terminator, with `ok=True`, in a fifth of its own runtime.

**pi has the identical defect** (`rpc-client.ts:429-432`, `:458-468`); it was
simply unreachable there, because pi never ran two delegations through one client.
ADR-0199's `MAX_DELEGATIONS_PER_PROMPT = 12` is what makes it reachable here.

**It is not solvable on the client side.** The abandoned turn emits `agent_start`
*and* `agent_end` after the second waiter subscribes, so no epoch or pairing can
separate them. The two fixes that do work are both elsewhere and both in place:
the server's busy preflight refuses the second `prompt` outright, and one child
per task means two delegations never share a client. **Binding: do not add a
client-side "fix" that only appears to work.**

---

## Consequences

- ADR-0198's *Partial fulfilment* is closed: the rpc half of the envelope contract
  and the cross-channel parity test both exist.
- ADR-0058's `P-119 / W4 m2` carry-forward is retired as a phantom (`9e3f096`).
- `print_channel.py` no longer claims to be the only file in aelix that spawns a
  subagent. It was true and now is not; both spawners are in this extension and
  product-core still contains no line of either.
- `RpcClient` is, for the first time, usable against a real child: its shipped
  default argv pointed at a mock-echo demo, and every test overrode it.

## Deferred, with the reason

- **No user-facing way to select the rpc channel.** It is reachable
  programmatically (`AgentsExtension(channel=…)`) and by tests. A selector needs a
  setting whose read is **GLOBAL-scope-only**, the same property
  `get_features_agents` has and states as a security property — "a merged read
  would let any cloned repo switch delegation on from its own
  `.aelix/settings.json`". A merged read here would let a cloned repo choose the
  channel, which is the same self-elevation defeat onto a channel the user did not
  pick. **Owner decision; half-wiring it is worse than not wiring it.**
- ~~**`tests/pi_parity` fixtures pin FALSE pi facts.**~~ **CLOSED** — audited and
  removed. All 22 pinned files were fetched and counted: **24 of the 27 claims
  were false**, off by as much as +5885 lines, and two fixtures contradicted each
  other about the same file at the same SHA. Only three were right, two of which
  were the only two anybody had ever measured. `pi_file_loc` is gone from all
  seven fixtures along with both "guards", which compared the fixture to
  hardcoded constants and therefore never read pi at all;
  `test_no_fixture_carries_unverifiable_loc_metadata` now asserts its absence.
  Line counts were never a parity property — the SHA, the command surface and
  the wire shapes are, and those have real assertions.
- **`steer` / `follow_up` for chain mode.** `prompt` rejects on busy; `steer` and
  `follow_up` enqueue. That is the turn-boundary design for `{previous}`
  substitution and it wants its own decision.
