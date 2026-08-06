# RPC sprint — working log

**Started:** 2026-07-29. Base `da61337` (= `main`, = `origin/main`).
**Worktree:** `/workspaces/aelix-rpc`, branch `feat/rpc-channel-and-protocol`.

> Written early and appended to as the sprint runs, because `/tmp` and the session scratchpad
> are wiped between sessions (proven — P3's recon was lost that way). `.omc/specs/` survives.

---

## 1. Owner decisions so far

### D1 — RPC belongs in product-core, fully implemented (settled 2026-07-29)

The kickoff handoff §3 framed the sprint's central fork as *"how much product-core may this
sprint touch?"* and offered a four-way ladder from "zero delta" to "terminator + containment
seam + R3". **The owner rejected the framing**, not just an option:

> "rpc는 원래 패리티 충실하게 코어에 구현되는게 맞는거죠? 그리고 모든 기능들도 충실이
> 구현되어야 하구요."

The correction: ADR-0197's 3-band rule forbids **delegation/spawn *policy*** leaking into
product-core — depth threading, `--no-agents`, consent, per-prompt caps. It does **not** make
product-core a sanctuary. P1/P2/P3 shipped a zero product-core delta because all three sprints'
work *was* policy, not because core is untouchable.

So the RPC turn contract, the missing terminators, `_handle_prompt` dropping `cmd.images`, and
the absent reader `limit=` are **core's own unpaid debt** and get fixed in core, without asking.
What stays in `aelix_agents` is unchanged: depth, `--no-agents`, consent, caps, argv/env policy.

The practical effect is that the handoff's option "C" (or wider) is the floor, and the ladder
itself was a mis-framing. Recorded to auto-memory as `feedback_band_rule_is_policy_not_core`.

### D2 — OPEN: pi-bespoke wire vs. a standard RPC

The owner raised a fork the handoff never considered:

> "완전하게 pi parity를 지켜서 pi의 독자적인 rpc 형태로 구현되어야할지 아니면 표준 rpc로
> 구현되어야할지 판단해보야할 필요도있을것같습니다"

This is **upstream of the sprint's deliverables**, because it decides what the long-lived
`RpcChannel` and the parity test are even built against. Under research — see §3.

---

## 2. Measured baseline (do not re-derive)

### 2.1 The RPC wire is a public, multi-consumer surface — not an internal detail

Established by grep against `da61337`:

| Consumer | Evidence |
|---|---|
| Advertised in the README, EN **and** KO | `README.md:50`, `README.ko.md:52` |
| Documented as a user-facing mode | `docs/guides/getting-started.md:32,74` ("JSONL command/response protocol on stdio") |
| Referenced in extension-authoring docs | `docs/guides/extension-authoring.md:176` |
| **Exposed over the network by `packages/aelix-server`** | ADR-0103: `WS /rpc` is "a thin WebSocket transport adapter over the existing `run_rpc_mode`", wire "byte-identical to the TUI's stdio transport — no translation layer" |
| Planned contract for the separate `aelix-web` repo (Phase 6) | ADR-0103 / ADR-0097 |
| **+ this sprint adds a 4th consumer**: the subagent delegation channel | — |

This is why D2 is not a side question: a cross-repo, cross-language, network-exposed protocol
is exactly where a bespoke `{success, data|error}` shape with no error codes, no version and
mixed casing becomes expensive.

### 2.2 No JSON-RPC 2.0 anywhere in the tree

`grep -rln "jsonrpc" --include=*.py packages/ src/` → **zero hits**, despite
`packages/aelix-coding-agent/src/aelix_coding_agent/mcp/` existing (`client.py`, `adapter.py`,
`manager.py`). Whether that MCP client is hand-rolled JSON-RPC 2.0 or an SDK is being measured
in the research below — it decides whether standard-protocol machinery already exists in-house.

### 2.3 `rpc/rpc_client.py` IS on the band gate's spawn allowlist

`tests/agents/test_p2_band_boundaries.py:47` — `_SPAWN_ALLOWLIST = ("rpc/rpc_client.py", "tools/")`.
So the machine gate stays green whatever this sprint does there. Confirms the handoff's read that
this was a question of intent, now settled by D1.

### 2.4 The Sprint-6f terminator bridge is still unwired, verbatim

`rpc/rpc_mode.py:~300-330` still carries the NOTE promising a synthetic terminal event that
"Sprint 6f wires ... per ADR-0058 carry-forward". It was never wired. Re-confirmed against the
current tree, matching the recon.

### 2.5 PRE-EXISTING FLAKY TEST — `tests/rpc/test_rpc_client_shutdown.py`

`test_stop_escalates_to_sigkill_when_sigterm_ignored` (`:56-65`) fails **~40% of runs**
(measured: 2 failures / 5 consecutive runs, plus 1 in the first full `tests/rpc/` run).

```
AssertionError: assert 'stub starting' in ''
tests/rpc/test_rpc_client_shutdown.py:65
```

**Not a regression — it predates this sprint** (base is untouched `main`). The handoff's
"7102 passed / 1 skipped" run simply got lucky.

**Root cause, by inspection:** the test calls `start()` then immediately `stop()` and asserts the
stub's stderr breadcrumb was captured, **with no drain loop**. The stub writes `stub starting\n`
at boot; whether the stderr drain task has read it before SIGKILL lands ~300 ms later is a pure
scheduler race. Its sibling `test_get_stderr_returns_captured_output` (`:68-83`) asserts the same
breadcrumb but polls up to 20 iterations first — and is reliable.

**Fix:** the assertion at `:65` is incidental and already covered correctly by the sibling test;
this test's subject is the SIGKILL escalation itself. In scope for this sprint (rpc shutdown /
stderr drain is exactly the code being changed). **Do not ship without fixing it** — a 40% flake
in the file this sprint edits will otherwise be blamed on the sprint.

### 2.5b THE SECOND BASELINE FLAKE — and the machine is the reason

`tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout`
is the other intermittent failure (the one the adversarial reviewer saw as "baseline 2 failed").

**It is LOAD-SENSITIVE, not broken.** Measured: **6/6 pass in isolation** (~1.4-2.4 s each). It only
fails inside a full-suite run. The test itself explains why — it deliberately drives a deadline race,
using a 300 ms budget against a stubbed 1.0 s exit-status poll so the deadline lands inside the
EOF-vs-`returncode` gap.

**The box:** `nproc` = **2**, and ~1 GB of 7 GB RAM free. Both baseline flakes (§2.5 and this one) are
timing races on a 2-core machine.

**Methodology landmine I hit myself:** I once ran a 6× pytest loop *concurrently* with the full suite.
The suite died at 98% with **exit 144** (128+16), and the concurrency is also what makes these two
tests flake. **Never run anything alongside the full suite here.** A "failure" observed under
self-inflicted contention is not evidence.

### 2.6 Worktree test invocation (the PYTHONPATH landmine)

The venv's editable installs point at the MAIN tree, so a bare `pytest` in the worktree silently
tests the wrong source. Always:

```
cd /workspaces/aelix-rpc && \
PYTHONPATH=/workspaces/aelix-rpc/packages/aelix-coding-agent/src:/workspaces/aelix-rpc/packages/aelix-agent-core/src:/workspaces/aelix-rpc/packages/aelix-ai/src \
/workspaces/aelix-ai/.venv/bin/python -m pytest -q
```

Verified working: `tests/agents/test_p2_band_boundaries.py tests/rpc/` → 195 passed, 1 skipped,
1 flaky failure (§2.5).

---

## 2.7 SHIPPED TO THE BRANCH — `d4a920e`

`fix(rpc): restore the turn terminator and make --mode rpc reachable`
7 files, +638/−21. **Full suite 7107 passed / 1 skipped / 0 failed; ruff clean.**
(7103 baseline + the 4 new prompt-contract tests — arithmetic checks out, nothing lost.)

| Lane | What landed | Proof |
|---|---|---|
| — | `cli/entry.py`: drop `repo=`/`fs=` so `--mode rpc` boots | revert → RED with the `RuntimeError`; restore → 6 s green |
| **K** | `harness/core.py`: abort emits `turn_end`+`agent_end` and returns; outer filter widened to `Exception` | reviewer's own probe: `agent_end` False→True, `prompt()` still returns `[]`; 141 abort/cancel tests green |
| **P** | `rpc_mode.py`: busy preflight, `streamingBehavior` routing, `cmd.images` forwarding, false NOTE replaced | mutation: preflight off → 2 RED, images off → 1 RED |
| **T** | `test_rpc_real_child.py` (real-child bed), `test_rpc_prompt_contract.py` | both mutation-proven |
| — | `test_p2_band_boundaries.py`: blanket kernel freeze → freeze-by-exception | passes with the authorised file; **still RED** for an unauthorised one (`hooks.py` probe) |
| — | `test_w6_regressions.py`: stale stub missing `images` | — |

### Design corrections worth not re-deriving

- **Abort must NOT reuse the four-event failure block.** pi's *primary* abort is
  `agent-loop.ts:196-199` — emits `turn_end`+`agent_end`, returns normally, never throws.
  `emitRunFailure` is the fallback for a genuinely *thrown* error.
- **Never widen the outer filter to `BaseException`.** `CancelledError` is a `BaseException`;
  abort is handled in the INNER handler and returns first, so the outer never needs it. This is
  what made the reviewer's `except (Exception, CancelledError)` unnecessary here.
- **`message_end` is the session write path** (`core.py:~4175`). Not emitting it on abort is what
  keeps a bodiless `"[error] "` turn out of the session file.
- **The double-response problem dissolved.** Because Lane K terminates any turn that *started*,
  `_handle_prompt` only owes "rejected before acceptance" — so no second response for an acked id.
- **A test that passes under mutation pins nothing.** My first `streamingBehavior` test asserted
  only the response type, which the pre-repair code also returned. Assert the queue contents.

## 2.8 NEW FINDING — the pi_parity fixtures pin FALSE pi facts, and the guard is a tautology

Found while doing the Lane D(a) citation correction. **Not fixed — it is a parity-pin change and
wants its own pass.**

`tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json` records `pi_file_loc` for four pi files, and
`test_phase_4_4_strict_superset.py::test_fixture_loc_counts_present` asserts them under a section
header reading `# === §G — Pi fixture immutability ===`. I fetched all four at the pinned SHA:

| file | fixture | RAW | non-blank | code-only |
|---|---|---|---|---|
| `jsonl.ts` | 58 | **58** ✓ | 49 | 36 |
| `rpc-types.ts` | 262 | **264** ✗ | 228 | 170 |
| `rpc-mode.ts` | 492 | **754** ✗ | 651 | 563 |
| `rpc-client.ts` | 343 | **515** ✗ | 443 | 290 |

**Three of four are wrong.** I checked the obvious alternative reading — that `loc` means
*lines of code* rather than raw lines — and it is refuted: `jsonl.ts` matches RAW exactly, and no
column (raw / non-blank / code-only) matches the fixture across all four. The likely cause is that
the numbers were recorded against an earlier pi and never re-measured when ADR-0034 moved the pin.

**Why no test caught it:** `test_fixture_loc_counts_present` loads the fixture and asserts it equals
hardcoded constants. Neither side ever reads pi. It is a tautology wearing the word *immutability*.

**Scope:** `grep -ln pi_file_loc tests/pi_parity/fixtures/*.json` → **7 fixtures**. All want the same
audit; only the rpc one is measured above.

This is the *same class* of defect as the ADR-0058 citation this sprint just retired — an
unverified claim about pi, written down once and thereafter trusted — except this one is dressed as
a test-enforced parity pin, which makes it more persuasive and therefore worse.

## 2.9 LANE P/E RECON — measured 2026-07-31, do not re-derive

Workflow `wf_d9d22b34-89e`, six ground-truth agents against `9e3f096`. The decisive facts:

### 2.9.1 `RpcClient` has ZERO production consumers

`grep -rn "RpcClient(\|RpcClientOptions("` → the class's own default-arg fallback plus **three test
files**. `aelix-server/rpc_ws.py` calls `run_rpc_mode` **in-process** (`rpc_ws.py:139-153`, a hand-fed
`StreamReader`) and never spawns. `src/aelix/__main__.py` imports `run_rpc_mode` only.

And the shipped default argv is `-m aelix` — the **mock-echo demo** (`__main__.py:143-147`). All three
test clients override `_build_argv`. So the class as shipped could not have had a user.

**Consequence: the blast radius of any `RpcClient` change is `tests/rpc/` (3 files) + the constant
pins.** This is the widest latitude in the sprint; use it.

### 2.9.2 The containment kwargs are MEASURED safe

A probe injecting `start_new_session=True` + `limit=8 MiB` into `rpc_client.py`'s spawn: **16/16 pass,
three runs.** No test anywhere greps `killpg|getpgid|setsid|start_new_session|SIGINT`.

`limit=` is **INERT today** — `attach_jsonl_line_reader` uses `read(4096)`, never `readline()`, and
`read(n)` never consults the limit (measured: 200 001 bytes drained clean at the 65536 default, while
`readline()` raised). It is a guard against a future reader, exactly as `print_channel.py:26-40` documents.

### 2.9.3 pi has NEITHER fix — both are aelix-original divergences

Fetched `rpc-client.ts` at the pin (`734e08e`, 515 lines, HTTP 200):

* **No child-exit observation.** The whole file has two `.on(` calls: stderr `data` at `:93`, and an
  `exit` handler at `:128` that lives *inside* `stop()`. pi's only death check is the same 100 ms
  one-shot grace aelix already ports (`:103-108`).
* **pi has the SAME F7 cross-talk defect** (`:429-432`, `:458-468`) — `collectEvents` resolves on the
  first `agent_end` from a global fan-out, and uncorrelated payloads fall through to every listener.

So the ADR must record child-death detection as **aelix-original hardening, not parity**. pi simply
never ran two overlapping delegations through one client; the defect was latent upstream and only
becomes reachable under P3's `MAX_DELEGATIONS_PER_PROMPT = 12`.

Bonus parity gap found: pi attaches `Stderr: ${this.stderr}` to its idle/collect timeouts (`:405`,
`:426`); aelix's three `wait_for` calls are bare and raise `TimeoutError()` with **empty args**.
aelix already does it correctly in `_send` (`:577-580`). Cheap win, bundle it.

### 2.9.4 F7 IS NOT SOLVABLE CLIENT-SIDE — do not claim to have fixed it

Reproduced verbatim. Every event delegation B collected carries **no id at all**, and B's own
correlated response is consumed into the pending-future map, so it never reaches the listeners.
A client-side epoch cannot help either: the stub's abandoned turn emits `agent_start` **and**
`agent_end` after B subscribes, so B cannot tell them apart by pairing.

**The two real fixes are already the design:** (a) the busy preflight shipped in `d4a920e` makes the
second `prompt` fail instead of cross-talking, and (b) one-child-per-task means two delegations never
share a client. Pin both; do not write a client-side "fix" that only appears to work.

### 2.9.5 `reduce_line` takes a `str`; `RpcClient` hands listeners a `dict` — and the failure is SILENT

`reduce_line(state, line: str)` does `line.strip()` first → `AttributeError` on a dict. And
`rpc_client.py:535` wraps every listener in `contextlib.suppress(Exception)`. So the naive wiring
drops **every** event silently, leaving an empty state that `build_result` reports as
`ok` / `'(no output)'`.

Fix: refactor `stream.py` into `reduce_event(state, event: dict)` with `reduce_line` as a thin str
wrapper. Pure-module change, no behaviour delta. The wire shapes already match — both channels emit
`dataclasses.asdict(event)` with the same snake_case `type` (`rpc_mode.py:290-302` vs
`print_mode.py:59`), so there is **no field mapping to do**.

### 2.9.6 The channel seam is one method — but `stop_all` bypasses it

The runtime reads exactly **one** attribute on the channel: `self.channel.run` (`runtime.py:750`).
A bare duck-typed object already satisfies it (`tests/agents_ext/test_events_and_statusline.py:53`
injects one and passes). So a `SubagentChannel` Protocol is a zero-behaviour-change refactor;
the annotations to widen are `runtime.py:298` and `extension.py:205`.

**But `stop`/`stop_all` do not go through the channel at all** — they call `abort_child` and
`remove_prompt_dir` directly (`runtime.py:591`, `:646`, `:652`), and `abort_child` hands `child.proc`
to `reap()`/`kill_tree()`. There is also **no channel teardown hook** anywhere in the extension.

**Consequence for Lane E:** put the REAL `asyncio` `Process` on `row.proc` and the existing
kill machinery works unchanged. That is the cheapest correct wiring, and it is only available
because one-child-per-task means there is a real local process per row.

The channel MUST also write `row.state` to a terminal value before returning, and MUST NOT raise
(`runtime.py:749-752` is `try/finally` with **no `except`** — the single-task door would raise into
the harness tool dispatcher).

### 2.9.7 argv/env for an rpc child

* `profile_to_argv(oneshot=False)` differs in **four** ways, not one: `--mode rpc` (not `json`), no
  `-p`, **no `--no-session`**, and the `task` is **silently dropped**. So Lane E must append
  `--no-session` itself and deliver the task over the wire as a `prompt` command.
* `--mode rpc` accepts every profile flag, and `entry.py` reads the system-prompt file and applies
  `--tools` **above** the mode dispatch (`entry.py:1294`, `:879`). Verified live: a real
  `-m aelix_coding_agent --mode rpc --no-session --tools read,bash --append-system-prompt-file …
  --permission-mode default --no-agents --no-approve` child answered `get_state` with `success:true`.
* Do **not** copy the oneshot prefix wholesale — `-p` opportunistically eats the next non-flag token
  into `parsed.messages` (`args.py:341-347`).
* **`--no-session` is what makes the child ephemeral.** Without it the child writes a real
  `~/.aelix/sessions/…jsonl` even with `AELIX_CODING_AGENT_DIR` pointing elsewhere (measured).
* The rpc branch passes **no `model_registry`** (`entry.py:2157-2165`), so the child's
  `set_model`/`cycle_model` return "no registry configured". **The model must come from the profile's
  `--model`/`--provider`.** This also finishes off any lingering reuse argument: `set_model` was the
  one mutable knob, and on a spawned child it does not work at all.
* An unknown `--tools` name is an **uncaught traceback** in rpc mode — the child dies before the
  transport exists. "Exited before the first JSONL byte" must be a first-class failure with the
  child's stderr attached.
* `AELIX_STDIN_TIMEOUT=1` is inert on the rpc path (`entry.py` reads piped stdin only for print/json),
  so `build_child_env` is reusable — but its stated justification no longer holds and must be re-documented.
* `get_state` is a safe zero-side-effect handshake/liveness probe and echoes its `id`.

### 2.9.8 The `stop()` stall is narrower than the omissions doc claimed

Measured across four cases: `stop()` pays the full 1.00 s **only** when the child is still alive at
entry **and** a descendant holds stdout/stderr. Already-dead child → 0.00 s even with a live holder.
Cause is in the stdlib (`base_subprocess.py:232-239`): `_try_finish` wakes the exit waiters only once
**every pipe is disconnected**. So a pinning test must construct that exact case; the other three pass
already.

**Second, independent defect in the same measurement: the grandchild SURVIVES `stop()`** in every
case — there is no `kill_tree` on the rpc path at all.

**pi's `stop()` has the INVERSE bug** (measured in node v24): it pays 1.00 s and SIGKILLs a corpse when
the child already exited, because it registers `.on("exit")` *after* `kill("SIGTERM")`. aelix already
beats pi there. Do **not** cite pi as the reference implementation for this fix.

### 2.9.9 The flake's root cause is a child-boot race, not drain ordering

`test_stop_escalates_to_sigkill_when_sigterm_ignored` fails because the stub has not yet reached
`signal.signal(SIGTERM, SIG_IGN)` within the 100 ms grace — so it dies on the first SIGTERM and the
SIGKILL escalation the test claims to exercise never runs. Measured 10 iterations: the FAIL runs
return from `stop()` in ~110 ms, the PASS runs in ~415 ms.

**So switching `stop()` to poll `returncode` will NOT fix it** — the failing path never reaches that
code. The fix is to wait for a readiness breadcrumb instead of a fixed 100 ms.

### 2.9.10 Gate arithmetic for Lane P/E

* `_SPAWN_ALLOWLIST` exempts `rpc/rpc_client.py` **wholesale** and nothing inspects spawn kwargs, so
  `limit=` / `start_new_session=` / `preexec_fn=` are band-legal there. A spawn in a **new**
  product-core file would be red by construction.
* `_KERNEL_CHANGE_ALLOWLIST` has exactly one entry and the tree already uses it — **zero headroom**.
* `_PRODUCT_CORE_CAP_ALLOWLIST` is an exact set, both directions, and `_importable_assignments`
  **descends into class bodies**. Renaming or deleting any of the five `RpcClient` constants also reds
  `tests/pi_parity/test_phase_4_4_strict_superset.py:316-319`.
* Cap-like by the regex: `STREAM_LIMIT_BYTES`, `MAX_LINE_BYTES`, `CHILD_DEATH_TIMEOUT_MS`,
  `EXIT_POLL_INTERVAL_MS`. **Escapes: `EXIT_POLL_SECONDS`** (`_SECONDS$` is not in the pattern) —
  and that is the same honest name `print_channel.py:154` already uses.
* **Prefer options with `None` defaults over new core constants.** The per-line budget and the reader
  limit can both be *parameters*, letting band 3 supply the numbers (`stream.MAX_LINE_BYTES`,
  `print_channel.STREAM_LIMIT_BYTES`) and keeping the policy where the band rule wants it.
* No gate forbids a `Callable` seam in product-core.
* Line citations across this repo are already rotted by ~225 lines (`batch.py:415` cites
  `runtime.py:478-490`, actual `705-721`; six files cite `runtime.py:481` for `_new_id`, actual `719`).
  **Prefer symbol-name citations in anything new.**

---

## 2.10 SHIPPED TO THE BRANCH — `9d960d3` (Lane P) and `0eb5204` (Lane E)

### `9d960d3` — `feat(rpc): child-death detection, containment and the Lane E seams`

Full suite **7121 passed / 1 skipped / 0 failed** (7107 baseline + 14 new). ruff clean.
Kernel untouched. **7 mutations, 7 killed.**

| What landed | Was |
|---|---|
| child-death detection (`_watch_for_exit`) + both legs normalised to `RpcServerExited` | 5.02 s block then a bare `TimeoutError()`; fast leg was `RuntimeError` at 0.00 s |
| `start_new_session=True` + a `preexec_fn` seam | child joined the parent's process group |
| `stop()` polls `returncode`; transport closed | full 1.00 s grace on an already-dead child; `__del__` traceback |
| `argv` / `env_base` / `stderr_max_bytes` / `send_timeout_ms` / `reader_limit` options | F5/F6 — Lane E was unbuildable |
| opt-in per-direction line budget + `dropped_lines` | no budget; a global one would swallow inbound COMMANDS |
| the §2.5 flake | **root cause was a child-boot race, not drain ordering** — see below |

### `0eb5204` — `feat(agents): RpcChannel — the rpc delegation channel, one child per task`

Full suite **7146 passed / 1 skipped / 0 failed** (7121 + 25 new). ruff clean. Kernel untouched.
**9 mutations, 9 killed.** Same 9 pre-existing warnings as the Lane P run, so Lane E added none.

New: `aelix_agents/rpc_channel.py`, `SubagentChannel` Protocol (in `print_channel.py`, beside the
seam's data types, so naming the seam does not drag an implementation into the runtime's import
graph), `stream.reduce_event`, `tests/agents_ext/test_rpc_channel.py`.

### Design corrections worth not re-deriving

- **The §2.5 flake was NEVER about `stop()`.** The stub had not reached
  `signal.signal(SIGTERM, SIG_IGN)` inside the 100 ms grace, so it died on the FIRST SIGTERM and the
  escalation the test names never ran. Its assertion was a stderr substring — which passes in exactly
  the runs where the escalation did *not* happen. Now: wait for a readiness breadcrumb, assert
  **elapsed >= the grace**. 12/12 green; mutating the stub to stop ignoring SIGTERM reds it.
- **`prompt` discarding a `success:false` IS pi.** I suspected a fabricated parity claim and was
  wrong: fetched at the pin, pi's `send` (`:474`) returns the raw response and the throw lives in the
  separate `getData` (`:506-508`); `prompt`/`steer`/`abort` call `send` alone. The existing test's
  "asymmetric invariant" docstring is correct. So the fix belonged to the CALLER — an opt-in
  `raise_on_command_error`, default off. **Verify before "correcting" a parity claim.**
- **`stop()` must not drop the framing reader.** The envelope is built after the child is gone, so
  nulling `_stdout_reader` made `dropped_lines` always 0. `get_stderr()` already outlives `stop`.
- **A budget that only checks the un-terminated tail enforces nothing** for any line shorter than the
  read chunk. Found by measurement: a 500-char line at a 64-byte budget sailed through.
- **`runtime.list()` returns MAPPINGS, not objects.** `getattr(row, "id", None)` silently yields
  `None` and a polling loop never fires.
- **Mutation harnesses must not classify on `tail -2`.** An asyncio teardown traceback pushed the
  pytest summary out of view and reported a killed mutation as SURVIVED.

## 2.11 SCOPE NOT TAKEN — the channel selector (owner decision)

`RpcChannel` is reachable programmatically (`AgentsExtension(channel=…)`) and by tests. There is
**no user-facing way to select it**, and that was deliberate rather than an omission.

Per F8 a selector needs five things, and one is a security decision: the getter must be
**GLOBAL-scope-only**, the same property `get_features_agents` has (`entry.py:491-496` states it as a
security property — "a merged read would let any cloned repo switch delegation on from its own
`.aelix/settings.json`"). A merged read would let a cloned repo choose the channel, which is the same
self-elevation defeat, onto a channel the user did not pick. Half-wiring it is worse than not wiring
it. **Ask the owner.**

## 2.12 LANE T + D — the pins, the flake, and ADR-0200

**Lane T pins** land in `tests/agents/test_rpc_sprint_pins.py`. Each was MEASURED unguarded — mutate
the source and the whole suite stays green:

* **`_reject_unsupported` was completely unpinned.** Relaxing it to admit
  `("single","parallel","chain")` left `tests/agents/` green. And the sprint's own recon note still
  *advises* relaxing it ("This is where P3 items 1 and 2 land") — advice P3 deliberately did not take
  (`_UNSUPPORTED_MODE`: *"P3 SHIPS PARALLEL AND CHAIN, AND THIS STILL RAISES — deliberately"*). So the
  most likely reader of that note would break "ONE CALL IS ONE CHILD" with zero test signal, guided by
  a document. Now pinned at **both** doors, plus a wiring assertion (a refactor that inlined the check
  into one door and forgot the other would keep the behaviour tests green through the door it kept).
* **`stop()`'s worst case** — SIGTERM grace + the bounded 5 s reap — with a lower bound too, so it
  cannot pass without actually escalating.
* **rpc conformance against the REAL child**, through the REAL `RpcClient`. Every other rpc client
  test overrides the argv and has to, because the shipped default points at the mock-echo demo. This
  is the only test that would notice the real server and the real client disagreeing about the wire.

### The §2.5b flake — fixed, and the fix is mutation-proven not to have retired the subject

Root cause: the **300 ms budget had to cover a real interpreter's BOOT**, because the channel starts
its clock at `run()`. 6/6 in isolation, intermittent inside a full run on a 2-core box — a scheduler
measurement wearing the test's name.

Widened to `timeout_ms=1500` / `poll=1.9`, five times the boot headroom. Both inequalities still hold:
the pumps must finish before the deadline (`boot < 1.5 s`), and the remaining budget must be shorter
than the exit gap (`1.5 − boot < 1.9`, true for any boot) while the gap stays under the 2.0 s floor.

**The risk here was retiring the MEDIUM #3 pin while appearing to fix a flake**, so it was measured:
remove `POST_EOF_EXIT_GRACE_SECONDS` from the wait and the widened test still goes **red**.

### ADR-0200

`docs/decisions/0200-rpc-delegation-channel-and-turn-contract.md`. Closes ADR-0198's *Partial
fulfilment* (the rpc half + the cross-channel parity test). The band gate now cites it by number, and
`_PRODUCT_CORE_CAP_ALLOWLIST` carries a comment recording that it deliberately did **not** grow and
why (both workarounds — an evasive name, a leading underscore — defeat the gate; parameters with
`None` defaults were the way out).

---

## 3. REMAINING WORK

1. ~~**Lane P rest**~~ — DONE, `9d960d3`.
2. ~~**Lane E**~~ — DONE, `0eb5204`.
3. ~~**Lane T**~~ / ~~**Lane D**~~ — DONE (this section).

### Still open

* **The channel selector** — §2.11. Owner decision.
* ~~**The `pi_file_loc` fixture audit**~~ — **CLOSED 2026-07-31, option B (delete).** All 22 pinned
  files fetched and counted: **24 of 27 claims false**, worst off by +5885; two fixtures contradicted
  each other about the same file at the same SHA (`model-resolver.ts` 530 vs 637 — 637 is right).
  Only 3 correct, and 2 of those were the only ones anybody had ever measured; the rest were round
  numbers (380/540/820/530/50/290/400/410/460/150/470) — estimates recorded once and thereafter
  trusted. Field removed from all 7 fixtures + both tautological guards; a new
  `test_no_fixture_carries_unverifiable_loc_metadata` asserts the ABSENCE and is mutation-proven.
  **Correcting the numbers (option A) was refused deliberately**: it leaves the tautology, so they
  rot again at the next pin move with nothing to catch it.
* **`dropped_lines` on the intake direction** — deliberately unbounded. Any future budget there must
  emit an explicit over-budget error, never a silent drop (ADR-0201, binding).
* ~~**`steer` / `follow_up` for chain mode**~~ — **CLOSED 2026-07-31, WONT-FIX-AS-STATED.**
  Measured (workflow `wf_acfd5c09-b3c`, six ground-truth agents). Three independent reasons:

  1. **Neither verb starts a turn.** Both are fully implemented on the real server and both are
     *pure enqueue*: sent to an IDLE child they return `success:true`, bump `pendingMessageCount`,
     and then sit inert forever. A chain of the form "await `agent_end` for step k, then
     `follow_up(step k+1)`" **hangs** until the parent's budget expires. They extend a RUNNING run;
     they do not dispatch one.
  2. **They are structurally incompatible with `{previous}`.** `{previous}` needs the parent to READ
     step k's summary before composing step k+1 — but these verbs must be sent while step k is still
     running, i.e. *before that summary exists*. A `follow_up` delivered mid-turn is absorbed into
     the same turn and produces **no second terminator**, so it cannot yield a per-step envelope either.
  3. **pi's chain is already exactly one-child-per-task** — a fresh one-shot process per step, output
     passed by string substitution, zero rpc / steer / follow_up. ADR-0201's decision is therefore
     **vindicated by parity**, not merely excused by aelix's 29-command gap.

  Also measured, against the "shared child" the item implies: **1.41x–2.45x more input tokens** for a
  3-step chain with **no operating point where it is cheaper** (the spawn cost it would amortise is
  re-sent every turn anyway), and it makes ADR-0199's injection class strictly worse — the fence's own
  label ("produced by the previous agent … DATA, not instruction") becomes literally false when the
  previous agent *is* this agent.

  **The one genuinely useful finding underneath it:** the parent already receives the child's entire
  conversation on the wire and discards 100% of it. A richer `{previous}` (transcript digest,
  tool-result index, file manifest) is buildable **today on `PrintChannel`** — no shared child, no
  rpc, no ADR-0201 conflict. If chain fidelity is the real want, that is the cheap door.

* ~~**`prompt(images=…)` never reached the model**~~ — **FIXED**, kernel, parity restoration. The rpc
  layer forwards `cmd.images` (this sprint), but `harness.prompt` built its `UserMessage` from the
  text alone. pi routes all four verbs through one `createUserMessage(text, images)`
  (`agent-harness.ts:43-45`). **The lesson outlives the fix:** every guard on this path asserted the
  FORWARDING and stayed green while the feature was broken — 22 of them, including this sprint's own
  `test_rpc_prompt_contract.py`. Forwarding is not delivery; assert at the `stream_fn` boundary.

### Found by the same recon, NOT actioned — for the owner

* **ADR-0199's "pi has none of this" list is partly false.** pi *does* have delegation with `parallel`
  and `chain` modes plus a `{previous}` placeholder (in `examples/`, not core), a batch cap, a
  concurrency bound and a consent dialog. Right on the wall-clock ceiling and the per-prompt budget;
  wrong on the other three. The load-bearing claim ("no delegation *in the kernel*") is true and
  unaffected — but ADR-0201 cites that framing, so it wants narrowing. Same defect class as the
  ADR-0058 citation and the `pi_file_loc` fixtures: an unverified claim about pi, written once.
* **`build_result`'s `failed` predicate ignores `state.error_message`.** A child that reports an error
  message with no error `stop_reason` and exit 0 comes back `status="ok"` with the ERROR TEXT as its
  summary and `error=None` — which `_run_chain` then forwards as `{previous}`. Narrow, but it is the
  one gap the chain's fail-fast guard does not cover.
* **`rpc_types` defaults `steering_mode` / `follow_up_mode` to `"all"`; the harness defaults to
  `"one-at-a-time"`.** A latent client-side mismatch.
* **The busy-preflight window — CLAIMED BY THE RECON, NOT REPRODUCED BY ME.** The recon said
  `agent_end` is emitted before `_phase = "idle"`, leaving a window in which a second `prompt` is
  mishandled. Measured against the mock-echo server: `is_streaming` was already `False` immediately
  after `agent_end`, and a second `prompt` was accepted normally. The in-process ordering is real (the
  `finally` runs after the emit) but one wire round trip is enough for it to close. **Not asserted,
  not actioned** — recorded so the next reader does not inherit an unverified claim, which is the
  entire subject of this log.

### Superseded — the original plan's list, for the record

1. **Lane P rest** — `rpc_client.py`: containment (`start_new_session`, pdeathsig, explicit `limit=`
   per ADR-0198 D6), child-death detection (none exists: a child dying mid-turn costs the caller its
   full timeout), and the argv/env/exit-callback seams Lane E needs (`RpcClientOptions` has none).
2. **Lane E — `RpcChannel`. THE DELIVERABLE, and the largest piece.** ~28 obligations; see
   `rpc-review-omissions.md`. Settled: **one child per task** (reuse is structurally impossible —
   the 29-command surface cannot change a live child's profile/permission-mode/cwd, and the runtime
   deregisters on return → orphaned child, which ADR-0197 forbids).
3. **Lane T** — the two-form parity test (a dual-mode scripted stub satisfies BOTH channels, no real
   model needed — confirmed in `rpc-review-test-adequacy.md`), rpc conformance test, `stop()` ≈6 s
   pin, and the two flakes in §2.5 / §2.5b.
4. **Lane D** — the new ADR (rpc half of the envelope contract + D1–D4 + turn contract +
   `exit_code`/`dropped_lines`), formally retire ADR-0058's phantom carry-forward, correct
   `rpc_mode.py`'s stale pi citations ("492 LOC", cases at "483-547"; the pin is 754 lines with
   `prompt` at 379). **The band-gate allowlist comment should cite this ADR by number once it exists.**
5. **`dropped_lines` — still BLOCKED**, see plan §7. A budget in `_jsonl.py` silently swallows
   inbound COMMANDS (that file serves both directions). Must be per-direction.

**Not merged to main.** The sprint's actual deliverable (Lane E) is not built, and the ADR does not
exist yet. `d4a920e` is a coherent, independently-valuable checkpoint on the branch.

## 4. Research (complete)

Workflow `wf_e376eb1f-224` — the D2 protocol fork. Four ground-truth agents (exact wire grammar /
pi reference & whether the defects are *inherited* / consumer census / standards landscape incl.
JSON-RPC 2.0, ACP, MCP), then three independent option designs (bespoke-fixed, standard-native,
dual-stack), then three adversarial judges (compatibility, defect-closure, cost & sequencing),
then a synthesis.

Notes land at `.omc/specs/rpc-protocol-recon-*.md`, `.omc/specs/rpc-protocol-option-*.md`, and
the brief at `.omc/specs/rpc-protocol-decision-brief.md`.

The pivotal question posed to the pi agent: **are aelix's defects inherited from pi?** If they
are, then "fix them" is a divergence from pi under *every* option, and option A's claim to
"preserve parity" is false — which would collapse the fork's main argument for staying bespoke.
