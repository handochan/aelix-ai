# RPC sprint — implementation plan **v2**

**Base:** `da61337`. **Worktree:** `/workspaces/aelix-rpc`, branch `feat/rpc-channel-and-protocol`.
**Status:** v2 — rewritten after adversarial review. v1's Lane K design was **demonstrably broken**;
see §9 for the full list of what the review killed and why.

Review notes: `.omc/specs/rpc-review-{kernel-risk,premises,omissions,test-adequacy}.md`.
Every finding there is backed by a command + real output or a source mutation + a red test.

---

## 1. Owner decisions (settled)

| # | Decision |
|---|---|
| **D1** | RPC belongs in product-core, faithfully and completely implemented. The band rule isolates delegation **policy**; it does not make core a sanctuary. |
| **D2** | **Keep the pi-shaped JSONL wire.** No JSON-RPC/ACP migration. Borrow ACP's `StopReason` vocabulary and JSON-RPC's `-32000…-32099` error band *inside* the pi envelope, so a future ACP adapter is a rename, not a redesign. |
| **D3** | **Fix the kernel parity regressions** in `harness/core.py`. |
| **D4** | **No `dialect:` seam.** No abstraction with one implementation. |

### D2's evidence in one line
The blocking defects are **semantic, not structural** — they cost the same ~15-80 lines in any
envelope, and a standard envelope fixes neither. Option B's own author measured its interop dividend
at **zero**: JSON-RPC carrying snake_case kernel dataclasses under an `aelix/*` namespace is a
standard nobody speaks. External obligation is also zero (0 tags, 0 releases, no `aelix-web`,
`aelix-server` `rm -f`'d from the publish set by `release.yml:82-87`).

---

## 2. The headline finding — CONFIRMED by independent pi fetch

`rpc_mode.py:316-320` claims pi "emits a synthetic terminal event" at `rpc-mode.ts:379-401`.
**False in every clause.** Independently re-measured at both refs:

```
$ grep -nE "agent_end|synthetic" pin/rpc-mode.ts    -> 0 hits (754 lines)
$ grep -nE "agent_end|synthetic" v0802/rpc-mode.ts  -> 0 hits (774 lines)
```

What pi actually does on prompt failure is `output(error(id,"prompt", e.message))` — a **correlated
error response**, and only when preflight did *not* succeed. That false citation propagated into
ADR-0058 `:66`/`:173-175` and **sent this work item to the wrong layer for six sprints**. Retire it.

---

## 3. pi's abort path — the fact that dictates Lane K's design

v1 assumed pi routes abort through `emitRunFailure`'s four synthetic events. **It does not.**
MEASURED at `pin/agent-loop.ts`:

```
196   if (message.stopReason === "error" || message.stopReason === "aborted") {
197     await emit({ type: "turn_end", message, toolResults: [] });
198     await emit({ type: "agent_end", messages: newMessages });
199     return;                     // <- RETURNS NORMALLY. No exception thrown.
```

So pi's **primary** abort shape is built on the REAL streamed message:

```
message_start(real) -> message_update* -> message_end(real, stopReason "aborted") -> turn_end -> agent_end
```

`emitRunFailure` is only the fallback for a genuinely *thrown* error.

aelix aborts by `task.cancel()` → `CancelledError` (`core.py:1416-1419` → `:4287`), so its loop is
torn down mid-await and cannot emit its own `turn_end`/`agent_end`. **Routing `CancelledError` into
the existing four-event closure block would emit a synthetic `message_start` + `message_end` on top
of the real assistant `message_start` that already went out** — measured live with 65 `message_update`s
already streamed. pi never produces that shape.

---

## 4. Lane K — the corrected kernel design

v1's three bullets were each wrong. The corrected design:

### 4.1 Catch the right exception type
`asyncio.CancelledError` is a **`BaseException`**, not an `Exception` (proven:
`issubclass(CancelledError, Exception) == False` on 3.12.1). v1 said "widen to bare `except Exception`",
which **delivers none of the fix** — built verbatim, `agent_end` was still absent, with an event list
byte-identical to baseline, while `prompt()` began raising where it used to return. The clause must be:

```python
except (Exception, asyncio.CancelledError) as exc:
```

pi's `catch (error)` has no BaseException/Exception split; transliterating it to `except Exception`
is a mistranslation.

### 4.2 Do NOT re-raise — return, as pi does
pi's `emitRunFailure` ends `return [failureMessage]` (`agent-harness.ts:525`) and `:568-580` returns
that from `catch (error)`. Preserving v1's `raise` was measured to:

| consumer | effect |
|---|---|
| `tui/shell.py:2501-2506` | **Esc kills the REPL** — `renderer.finalize()` never runs, the ✖ banner never prints, falsifying `shell.py:2494`'s "A failed turn must not kill the REPL" |
| `modes/print_mode.py:215-228` | the **only** consumer of `stop_reason=="aborted"` in the product — i.e. exactly what this lane exists to feed — becomes unreachable |
| `rpc/rpc_mode.py:309-315` | silences the breadcrumb whose own comment says *"do NOT silently suppress"* |
| `harness/core.py:2921-2923` | `dispose()` raises — `contextlib.suppress(Exception)` does not suppress `CancelledError` |

All four filter on `Exception`. None catches `BaseException`.

### 4.3 Split the closure block — abort is not a failure
Abort must NOT reuse the four-event synthetic block. Emit **only `turn_end` + `agent_end`**, carrying
the REAL assistant message already in flight, with `stop_reason="aborted"`; then `return`.

This also dissolves the **session-pollution defect the review found**: v1's design persisted a
synthetic assistant message with the literal body `"[error] "` — empty, because
`str(CancelledError()) == ""` — into the session JSONL. A resume rebuilds it and hands the provider
a leading assistant message with no preceding user turn. pi persists on its *thrown* path too, but
with an empty body, so v1's "converges with pi" was only half true.

The genuine-exception path keeps the existing four-event block, with the widened filter from §4.1.

### 4.4 pi orders the abort ack AFTER the terminator (new, unlisted)
pi's `abort()` awaits idle (`agent-harness.ts:949` → `:965 await this.runPromise`) and `rpc-mode.ts:413-416`
acks only after that. aelix's `abort()` (`core.py:1388-1441`) has **no idle wait**, so the ack races
the terminator. Fix, so an automated parent can rely on ordering.

### 4.5 The band gate — resolve explicitly, do not "verify green"
There are **two** kernel gates and v1 cited neither correctly (`:39-41` is a *constants block*, not a
test; the text scan is `test_kernel_has_no_subagent_surface` at `:188`).

`test_kernel_untouched_vs_merge_base` (`:214-241`) is a **blanket kernel freeze** — it diffs against
`git merge-base origin/main HEAD` **and** checks `git status --porcelain` (`:236-238`). **No wording of
Lane K passes it.** MEASURED red:

```
AssertionError: the kernel must not change in P2:
  [' M packages/aelix-agent-core/src/aelix_agent_core/harness/core.py']
```

The gate encodes "P2 must not touch the kernel". P2 has shipped; D3 authorises this change. So the
gate must be **re-baselined as a reviewed decision in the same commit**, with the new ADR as its
justification — not silently deleted, and not discovered red at merge time.

---

## 5. Lane P — product-core rpc

### 5.1 The preflight, correctly guarded
`harness.prompt` raises `AgentHarnessError("busy", …)` **inside the coroutine**, so v1's "preflight
before `create_task`" was called *mechanically impossible* by the review — a synchronous pre-check
cannot observe it. Mirror pi's actual mechanism instead: pi threads a `preflightResult(didSucceed)`
callback (`rpc-mode.ts:388-391`) and emits `success` **from inside it**, then at `:396`:

```
396     if (!preflightSucceeded) {
397       output(error(id, "prompt", e.message));
```

So: **the ack moves inside the task**, emitted once the turn is known to have started; a failure
before that point emits the correlated error response instead.

**v1's §4 table row 5 was wrong** and contradicted v1's own prose: an *unguarded* error response on
task failure emits a **second response for an id that already received `success:true`**. Measured —
the ack really does go out first. Guard it on `preflightSucceeded`, exactly as pi does.

### 5.2 The rest
- `rpc_mode.py:309` — `_handle_prompt` drops `cmd.images` **and `streamingBehavior`**; pi forwards both
  (`rpc-mode.ts:382-386`).
- `rpc_client.py` — containment (`start_new_session`, pdeathsig, explicit `limit=`) **plus** the seams
  Lane E needs (§6.2). No child-death detection exists today: a child that dies mid-turn costs the
  caller its full timeout. Add it.
- **`_jsonl.py` per-line budget: BLOCKED as specified.** See §7.

---

## 6. Lane E — `RpcChannel` (the actual deliverable; v1 gave it one paragraph for ~28 obligations)

### 6.1 One child per task — reuse is structurally impossible
MEASURED: the 29-command rpc surface **cannot change a live child's profile, permission mode, or
cwd**, and the runtime deregisters the child when the delegation returns, reproducing the
orphaned-child failure ADR-0197 forbids. Settle it in the plan, with the reason.

### 6.2 The seam question, settled
`RpcClientOptions` has **no argv, env-delete, or exit-callback seam**, so Lane E cannot be built
without a product-core change. Under D1 that change belongs in Lane P and must be listed there —
v1 omitted it.

### 6.3 Port list
Enumerate every `PrintChannel.run` obligation and mark it ports / changes / meaningless on a
long-lived channel: reaping, grace, `kill_tree`, descendant pids, stderr ring, cost fallback,
truncation, `dropped_tools`, permission-mode plumbing. Never `-m aelix` (mock `stream_fn`,
`src/aelix/__main__.py:142-147`). Depth / `--no-agents` / MCP-pop stay here, never in
`rpc_client._build_env`.

### 6.4 Turn-contract row v1 missed
**"the child process died"** — no detection exists. Add the row and the handling.

---

## 7. `dropped_lines` — v1's §4.2 is a REGRESSION, blocked

`_jsonl.py` serves **both directions**. An unconditional per-line budget there **silently swallows
inbound RPC commands**. MEASURED with a 5000-char message at budget 2048 (and reproduced at the real
4 MiB with a 4 MiB+1000 payload): the command **vanishes with no signal at all** — the exact class of
silent-failure defect this sprint exists to eliminate. No existing test notices.

Also: the symmetry claim cannot be delivered by the listed work. `LineAssembler` already implements
budget/counter/resync in the extension; a second one in product-core creates two budgets that can drift.

**Required:** make any budget **per-direction**, leaving the server's intake unbounded — or emit an
explicit over-budget error rather than dropping. Decide before writing code.

**Naming:** `_CAP_NAME_RE` (`:131-133`) matches `_BYTES$`, so every honest name trips the cap gate and
only a name that hides what it is would pass. Amend the allowlist deliberately; do not pick an
evasive name.

---

## 8. Tests

**Already done and verified in the worktree:**
- `cli/entry.py` — the `--mode rpc` startup fix (2 kwargs). Live: `get_state` answers.
- `tests/rpc/test_rpc_real_child.py` — the real-child bed. **Mutation-proven**: revert the fix →
  RED with `RuntimeError` at `rpc_mode.py:1911`; restore → green in 6 s.

**Confirmed feasible by the review:** a single dual-mode scripted stub satisfies **both** channels
(`run_print_mode(mode="json")` / `run_rpc_mode`) with a scripted `stream_fn` — **no real model
needed**, so the parity test is a unit test, not an integration test.

**Still to build:** the two-form parity test (event-sequence *and* `SubagentResult`), the rpc
conformance test, multi-turn `RpcClient` coverage, the `stop()` ≈6 s worst-case pin, and the §3.3
flake fix.

**Mutation gate:** every §4/§5 row must be proved by mutating source and showing a named test go red.
Note the review's warning: the kernel change is **invisible to 155 existing abort/cancel tests**, so
new tests must cover the TUI and `--mode json` abort rendering, not just the rpc path.

**Baseline is 2 failed, not 0** — `test_stop_escalates_to_sigkill_when_sigterm_ignored` (§3.3 flake)
plus one more to identify before implementation starts.

---

## 9. What the adversarial review killed

| v1 claim | Verdict |
|---|---|
| "widen to bare `except Exception`" | **CRITICAL — delivers none of the fix.** `CancelledError` is a `BaseException`. |
| "re-raise preserved" | **CRITICAL — pi divergence**; breaks TUI/print/rpc/dispose, all four filter on `Exception`. |
| "the gate is a text scan … verify green" | **CRITICAL — false**, and the real gate is a blanket freeze that no wording passes. |
| "preflight before `create_task`" | **Mechanically impossible**; pi uses a `preflightResult` callback. |
| §4 table row 5 (unguarded error response) | Emits a **second response for an already-acked id**; contradicted v1's own prose. |
| §4.2 `_jsonl` budget | **Silently swallows inbound commands.** Blocked. |
| "converges with pi" (D3) | **Half true.** True at "an `agent_end` exists"; a new divergence at the message level. |
| Lane E as one paragraph | ~28 obligations, 4 recon questions silently dropped, 2 of them structural blockers. |

The v1 diagnosis was sound — the busy-lie, the abort silence, the kernel attribution, the two band-gate
traps and the flake all reproduce. **The v1 prescription was not.** That gap is exactly what requiring
reviewers to *execute and mutate* rather than read is for.
