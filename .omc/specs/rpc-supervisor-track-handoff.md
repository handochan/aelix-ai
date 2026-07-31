# RPC-as-standard / supervising-orchestrator track — kickoff handoff

**Written:** 2026-07-31, at the end of the session that shipped ADR-0201 and ADR-0202.
**Base:** `main` = `origin/main` = `c33e867` at the time of writing (it moves often — re-check).
**Why this file exists:** `/tmp` and the session scratchpad are wiped between sessions (proven —
P3's recon was lost that way). `.omc/specs/` survives and is tracked.

**Read first:** `docs/decisions/0201-rpc-delegation-channel-and-turn-contract.md`,
`docs/decisions/0202-chain-tool-trail.md`, `.omc/specs/rpc-sprint-log.md`.

---

## 1. The owner's decision

**Direction: make the rpc channel the standard delegation transport**, motivated by *mid-run
interaction* — an orchestrator that monitors running child agents and **intervenes** when one goes
the wrong way.

The owner accepted the recommended sequencing:

1. **B — a dev-only channel selector** (env var), so `RpcChannel` can be smoke-tested against a
   REAL model for the first time. Plus the three small must-fix items in §4.
2. Then observation (the projection), durability, `set_permission_mode`.
3. **C — the product selector, rpc as default — LAST.**

**Not chosen:** A (do nothing). **Deferred:** C.

---

## 2. THE MEASUREMENTS — do not re-derive any of this

Two workflows, ~1.2 M subagent tokens: `wf_acfd5c09-b3c` (chain/steer recon) and
`wf_ac12598d-98f` (supervisor/recovery/scale recon). Everything below is MEASURED unless marked.

### 2.1 The single most important sequencing fact

**Making rpc the standard transport buys INTERVENTION and NOTHING ELSE. It buys zero observability.**

Both channels feed the identical `_StreamState` through the identical `reduce_event` and hand it to
an identically-shaped `on_stream`. The parent's blindness is a **projection in one function** —
`runtime._publish` collapses the 18-field `_StreamState` into the 7-field `SubagentProgress`, which
carries **no assistant text**.

> A supervisor built on `SubagentProgress` will be **exactly as blind on rpc as on print**.
> Widening the projection is a SEPARATE work item, and it is the one that actually enables
> "sees it going the wrong way". **Sequencing them as one sprint ships a back channel with
> nothing to trigger it.**

The rich tap already exists and is already delivered: `SubagentChannel.run`'s
`on_stream: Callable[[_StreamState], None]` gets `summary` (the child's assistant TEXT),
`tool_trail`, `stop_reason`, `error_message`, `provider`, `model`, `turns`. `_publish` throws it
away one frame later.

### 2.2 What rpc genuinely buys

* **Intervention.** `PrintChannel` uses `stdin=DEVNULL` — there is no back channel at all, only
  signals, and the only thing a signal does is kill.
* **`steer` is exactly the intervention verb.** Fully implemented server-side, enqueues regardless
  of phase (pi parity), and drains at **every turn boundary inside a running run**. NOTE: this is
  the OPPOSITE use from the one ADR-0201 closed as WONT-FIX — that was about using steer/follow_up
  to *dispatch chain steps*, which fails because on an idle child they start nothing.
* **Two independent death detectors** vs print's one: PDEATHSIG(SIGTERM) normally wins, and when
  SIGTERM is ignored or the child's loop is wedged, **stdin EOF on the command channel still ends
  it**. Measured: the real child exits cleanly, code 0, in 0.36 s on stdin EOF alone.
* Incidental: `PrintChannel` reports a child that died mid-turn with **exit 0 as a SUCCESS**
  (`build_result` never reads `state.saw_agent_end`). rpc is immune only by accident —
  `RpcClient` raises `RpcServerExited` on any exit. `saw_agent_end` is the correct fix for both.

### 2.3 Recovery — MUCH closer than the earlier estimate

Three corrections to what this repo previously believed:

1. **A scoped session dir EXISTS today.** `AELIX_CODING_AGENT_SESSION_DIR` / `--session-dir`, read
   by `cli/config.py::get_session_dir` → `entry.py`'s `sessions_root = parsed.session_dir or
   get_session_dir()`. **ADR-0201 measured the WRONG variable** (`AELIX_CODING_AGENT_DIR`, which is
   not in the session-path chain at all). Making a delegated child durable is an **argv change**,
   not a build.
2. **Resumption works today, proven at the provider wire.** A fresh child continues a dead child's
   work via `--session <path>`, `--continue`, or rpc `switch_session` — verified against a local
   fake provider: the replacement child's request body carried the dead child's full message
   history. Mechanism: `harness/core.py::_run` → `session.build_context()`.
3. **Mid-tool death is safe to resume on every shipped provider.** A child SIGKILLed during a tool
   call leaves a dangling `tool_call` in the session file; `providers/_transform_messages.py`
   synthesises `ToolResultMessage("No result provided", is_error=True)`. All four adapter families
   route through it (the module docstring saying "only OpenAI Completions calls it" is STALE).

Messages are durable **per message**, written synchronously at `message_end`
(`session.append_message` → `O_APPEND` + `os.write`). **No `fsync` anywhere under `session/`** — so
durable against process death, NOT against host death. Fine for supervision; not a WAL.

**THE REAL RECOVERY GAP:** a resumed child is **invisible to the orchestrator**. `get_messages`
returns `[]`, `messageCount` is 0, `get_session_stats` is all zeros — while the model demonstrably
sees the full history. Cause: `_state.messages` is only ever `.extend()`-ed, never rebuilt from the
session; `_finish_session_replacement` rebuilds it only when `setup is not None`, and
`switch_session` passes no `setup`. Contrast: `get_fork_messages` DOES read the tree and works.
**Fix is small and local** — rebuild `_state.messages` from `build_context()` (and refresh
`_cached_session_name`), or make `get_messages`/`get_session_stats` read the session.

### 2.4 Scale — resources are NOT the constraint

MEASURED at n=20 live rpc children: **+80 fds** (4/child — the 4th is a pidfd), **+60 asyncio
tasks** (3/child: stdout pump, stderr drain, exit watcher), **+796 kB RSS**, **0.92 % of one core**
(all of it `EXIT_POLL_SECONDS = 0.05` wakeups). `RLIMIT_NOFILE` is 524288. Transport throughput:
48 024 events through 8 concurrent children for 0.83 s of parent CPU = **17.3 µs/event**, zero
dropped lines; the transport half saturates one core at ~58 000 events/s.

**Ranked real risks — CPU and fds do not make the list:**

| # | Risk | Size |
|---|---|---|
| 1 | ~~`RpcClient.prompt_and_wait` buffers EVERY event of the turn in a local `collected` list, and `RpcChannel._run_turn` **discards the return value**.~~ **CLOSED in Step 1** (`d28fe3a`) — keyword-only `collect: bool = True`, opted out at the one production call site. Re-measured on this branch: 25.6 MB → 0.36 MB peak. | ~~1 line~~ done |
| 2 | `batch.MAX_CONCURRENCY`'s permit is released when `run()` RETURNS. If a child outlives its call the permit is held forever, and `_SEM_BY_LOOP` is module-global — **after 4, the next `agent()` call in the session parks forever.** A deadlock, not a throttle. | design |
| 3 | No time-based throttle on statusline writes (`progress._set_row` dedups by rendered TEXT only, and the text includes `tokens`). N children share one height-1 row. | small |
| 4 | Correlation returns the instant one client serves two turns. | wire change |

### 2.5 The correlation boundary — the architectural line

`RpcChannel.run` constructs a **new `RpcClient` per delegation**, so N children are N **disjoint**
listener sets. Child k's uncorrelated `agent_end` cannot reach child j's waiter. **Parallelism is
safe at any N; do not spend design budget on client-side correlation for the fan-out case.**

It comes back the instant **one client serves two turns** — i.e. "resume this child with a new
task". `prompt_and_wait` resolves on the FIRST `agent_end` after subscribing, and it is NOT fixable
client-side (the abandoned turn emits `agent_start` *and* `agent_end` after the second waiter
subscribes). Fixing it needs a **server-side correlation id on `agent_end`** — a wire-protocol
change, aelix-original (pi has the same defect).

> **BINDING UNTIL THEN: "supervised" must mean "ONE TURN, STEERED" — never "one child, many
> tasks".** `steer`/`follow_up` are safe precisely BECAUSE they are pure enqueue and create no
> second turn.

### 2.6 Other constraints that will bite

* **No `set_permission_mode` / `set_system_prompt` / `set_tools` / `set_cwd` on the 29-command rpc
  surface.** A live child cannot be re-postured, so ADR-0199's `permission_floor` goes **silently
  inert** — the human shift+tabs to tighten and nothing applies it. If a supervised child is
  long-lived, this must be closed and it must **fail CLOSED** (kill the child) when unsupported.
* **`set_model` does not work on a spawned child at all** — `entry.py`'s rpc branch passes no model
  registry, so `set_model`/`cycle_model`/`get_available_models` answer "no registry configured".
* **The registry pops the row in `_run`'s `finally`.** A child outliving `run()` is invisible to
  `list`/`status`/`stop`/`stop_all` and to teardown. Six changes are needed; the blocking one is
  that **`RunningChild` has no route to the command channel** — `row.proc = client.process` is
  SIGNALS ONLY, and the `RpcClient` is a local inside `RpcChannel.run`. So `stop(id)` works but
  `steer(id, …)` has no path from a registry row — **the exact intervention the owner wants.**
* **`list()` / `status()` / `stop()` have ZERO production callers.** The three contract event
  channels (the documented "P4 dashboard" seam) have zero subscribers repo-wide. `/agents list` is
  the PROFILE list, not live children. **The monitoring UI the decision is premised on does not
  exist at any layer** — budget for building the consumer, not just un-popping the row.
* A supervised child **dies on `/new`, `/resume`, `/fork` and quit** (`extension._on_session_shutdown`
  → `stop_all`). That is a product decision to make, not a bug to fix.
* Finest text granularity is a **completed assistant message**, never mid-message —
  `message_update` is deliberately ignored by the reducer (O(n²): measured 329x/2773x ratios). A
  supervisor can act at turn boundaries only. Same on both channels.

---

## 3. Landmines (carried forward — re-reading these is cheaper than re-learning them)

* **`agent_end` has no correlation id.** Do NOT add a client-side "fix" that only appears to work.
* **pi's `send` (`rpc-client.ts:474`) does NOT throw on `success:false`** — the throw is in the
  separate `getData` (`:506-508`). The discard in `prompt`/`steer`/`abort` is REAL pi parity.
  `RpcChannel` already opts out via `raise_on_command_error=True`, so this defect is closed there.
* **`CancelledError` is a `BaseException`** — widening a filter to `except Exception` delivers none
  of the fix.
* **Forwarding ≠ delivery.** Assert at the `stream_fn` / provider boundary, not at the call site.
  This is how `prompt(images=)` stayed broken through a whole sprint with 22 green tests.
* **A test that passes under mutation pins nothing.** Every row below should be mutation-proven.
* **Never run anything alongside the full suite** (2 cores; causes exit 144 and induces flakes).
* **Worktree PYTHONPATH is mandatory** — the venv's editable installs point at the MAIN tree:
  ```
  cd /workspaces/aelix-rpc && \
  PYTHONPATH=/workspaces/aelix-rpc/packages/aelix-coding-agent/src:/workspaces/aelix-rpc/packages/aelix-agent-core/src:/workspaces/aelix-rpc/packages/aelix-ai/src \
  /workspaces/aelix-ai/.venv/bin/python -m pytest -q
  ```
* **`main` moves often** (several other worktrees are active). Always `git fetch` and re-check
  before branching or merging; expect to merge `origin/main` in mid-flight.
* **ADR numbers collide.** Check `docs/decisions/` immediately before claiming one — this happened
  once already (0200 → 0201 rename at merge time).

---

## 4. THE WORK — in order

### Step 1 — the three must-fix items — ✅ DONE (`d28fe3a`, `5f6f6fd`, `64ceb5d`)

These are correctness fixes that are right regardless of whether rpc becomes standard. They landed
**before** the smoke test, so the smoke test measures a fixed channel.

| # | Fix | Where | Why it was required |
|---|---|---|---|
| ① | Move `unsubscribe()` to AFTER `_shutdown` | `aelix_agents/rpc_channel.py::RpcChannel._drive`'s `finally` | **The intervention path IS the kill path.** Every `stop`/timeout threw away bytes the child had already written that the parent had not yet reduced. Measured on a timed-out delegation: `summary='(no output)'`, 0 tokens, 0 turns → the child's real partial answer with its full usage block. |
| ② | Stop buffering discarded events | `rpc/rpc_client.py::prompt_and_wait` (keyword-only `collect: bool = True`) + `rpc_channel.py::_run_turn` | Measured 25.6 MB peak parent memory for one turn against 0.36 MB, for a list the only production caller discards. (The earlier 109 MiB figure was a different workload; 25.6 MB is what this branch measured end-to-end.) |
| ③ | Subscribe the reducer BEFORE `client.start()` | `rpc_channel.py::RpcChannel.run` | "The child said nothing" and "we were not listening yet" were indistinguishable. Measured: a child that speaks and exits 9 inside the grace now reports its own `rate limited` and 33 tokens instead of a boot traceback and zeroes. |

#### TWO CLAIMS IN THE ROW ABOVE WERE FALSE AND ARE STRUCK. Do not reinstate them.

1. **There is no "closing report".** `run_rpc_mode` calls `capture.unsubscribe()` *before*
   `runtime_host.dispose()` aborts the turn, and the abort close-out emits no `message_end` at all
   — a real child writes **zero bytes** after its stdin EOF. What ① recovers is what the child had
   ALREADY written and the parent had not yet read. That is real, and it is all of it.
2. **① does NOT make `build_result`'s `stop_reason == "aborted"` branch reachable on rpc.**
   `build_result` gives the caller's proposed outcome precedence (`envelope.py:265-266`), so a
   timeout stays a timeout. Aborted rpc delegations were never mislabelled by this.

#### What Step 1 cost, so the next reader does not repeat it

The first commit (`d28fe3a`) shipped **two regressions**, both found by adversarial review and both
independently reproduced before being accepted. Both had one cause: **one subscription fans out to
two consumers with different lifetimes**, so moving the single `unsubscribe()` moved both.

* The **progress tap** must stop at a terminal `row.state`. `_eager_abort` makes the row terminal
  *before* the `finally` drains, so a live tap published a terminal snapshot per drained line and
  `SubagentProgressBridge` emitted a fresh `subagent_start`/`subagent_end` pair for each: **2325
  pairs for one cancelled delegation** on the channels a dashboard subscribes to. `PrintChannel`
  holds this invariant by cancelling its pumps next to its own `_eager_abort`; this channel cannot,
  because the accumulator still has to read.
* The **accumulator** must stop at `state.saw_agent_end`. `build_result`'s
  `stop_reason in ("error","aborted")` disjunct is *not* gated on the caller's outcome, so one late
  `message_end` flipped a clean exit-0 delegation to `ok=False` and replaced the answer. Gating on
  the caller's outcome instead was measured and **rejected** — it still folded 1145 post-terminator
  lines, because `agent_end` resolves the waiter's future *inside* the fan-out while the pump keeps
  delivering.
* The one thing the accumulator gate subtracts, recorded in the code: a drain that delivered
  `agent_end` *before* a partial `message_end` would now lose that partial. Reachable only from a
  synthetic child — the harness emits `MessageEndEvent` then `AgentEndEvent`
  (`harness/core.py:4389-4391`).

Also fixed in `64ceb5d`, and **not** caused by this work: `test_start_raises_a_typed_error_when_the_child_dies_in_the_grace`
was the suite's one red. `_watch_for_exit` POLLS `returncode` every `EXIT_POLL_SECONDS` (50 ms), so
death detection is quantised to {50, 100, …} ms against a 100 ms grace — **a window one poll tick
wide**, which a child dying at 51 ms already loses. Two CPU hogs on 2 cores is enough to fail it
8/8. Only the test was moved out of the way; **the production race is untouched**, and the real fix
is to race the exit event instead of polling it.

Suite after Step 1: **7425 passed, 1 skipped, 0 failed**; both band gates green.

### Step 2 — B, the dev-only selector

An env var read in `cli/entry.py` where `AgentsExtension(...)` is constructed (the ONE site allowed
to name `aelix_agents` — `tests/cli/test_p2_import_direction.py` pins this).

**Why an env var and not a setting:** a repo cannot set an env var, so the global-scope-only
security decision (§5) does **not** have to be made yet. `[features] agents` is read
global-scope-only precisely so a cloned repo cannot switch delegation on from its own
`.aelix/settings.json`; a channel selector read from merged settings would be the same shape.

**Then: smoke-test `RpcChannel` against a REAL model for the first time.** All 25 of its tests use
scripted stubs. This is the biggest open unknown and the prerequisite for everything after.

### Step 3 — observation (the projection), in PARALLEL

Channel-independent, and per §2.1 it is the item that actually enables the vision. Widen what
`runtime._publish` projects (or add a second, richer tap) so a supervisor can see assistant text.
`panel.py`'s widget is the only existing container that could host a supervisor view; its input
type must widen first.

Also available and currently discarded: the child's **tool RESULTS** cross the PrintChannel wire in
full and only `is_error` is read — "what did the child get back from that bash call" is the single
most useful signal for detecting a child going wrong, and it is already delivered.

### Step 4 — durability + the resumption gap

Drop `--no-session` from `build_rpc_child_argv`, add `--session-dir <run-scoped path>`, record the
path on the row. Then fix §2.3's real gap so a resumed child is visible to the orchestrator.

### Step 5 — `set_permission_mode` on the wire, fail-closed

Product-core change (D1-shaped, allowed by ADR-0201's reading of the band rule) plus a harness-side
re-clamp. **Must kill the child if the command is unsupported**, or the floor is a lie.

### Step 6 — C, the product selector, rpc as default

Needs: a global-scope-only getter with the same property `get_features_agents` has, a CLI flag, and
a decision about whether `PrintChannel` stays. **Recommendation: keep it.** The simple, stateless,
robust one-shot path has standalone value, and `SubagentChannel` exists precisely to let both live.

---

## 5. Open owner decisions

1. **Does a supervised child survive `/new`, `/resume`, `/fork`?** Today it dies (teardown calls
   `stop_all`). Product decision.
2. **Does the delegation call stop being the child's lifetime?** Required before a child can outlive
   `run()` — and until it is resolved, §2.4 risk #2 makes long-lived children a deadlock.
3. **The global-scope-only setting** (only at step 6; step 2 sidesteps it).
4. **Raise `MAX_LIVE_CHILDREN`?** Not on fd/task/CPU grounds — those are negligible at 20. If it is
   raised, refuse on provider-connection and child-memory grounds instead.

---

## 6. Unrelated leftovers, still open

* `build_result`'s `failed` predicate ignores `state.error_message` → a child with an error message
  but no error `stop_reason` and exit 0 returns `status="ok"` with the ERROR TEXT as its summary and
  `error=None`, which `_run_chain` then forwards as `{previous}`. Narrow, real, small.
* ADR-0199's "pi has none of this" list is wrong on three of six claims (pi HAS delegation with
  parallel+chain and a `{previous}` placeholder, in `examples/`; a batch cap; a concurrency bound; a
  consent dialog). The load-bearing claim — none of it in the KERNEL — is true, so the band rule is
  unaffected, but ADR-0201 cites the framing and wants narrowing.
* `rpc_types` defaults `steering_mode`/`follow_up_mode` to `"all"`; the harness defaults to
  `"one-at-a-time"`. Latent client-side mismatch.
* ~~`saw_agent_end` is read by nothing~~ — **no longer true.** Step 1 made it the rpc accumulator's
  gate (`5f6f6fd`). It is still unread by `build_result`, so PrintChannel's "died mid-turn, exit 0,
  reported success" (§2.2) is still open and `saw_agent_end` is still the correct fix for it.
* The busy-preflight window: a recon claimed `agent_end` precedes `_phase = "idle"`, leaving a
  window. **I could not reproduce it over the wire** and did not action it. Recorded so nobody
  inherits an unverified claim.
