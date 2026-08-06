# RPC sprint plan — adversarial review, OMISSION lens

**Reviewed:** `.omc/specs/rpc-sprint-plan.md` (DRAFT).
**Tree:** worktree `/workspaces/aelix-rpc` @ `feat/rpc-channel-and-protocol` (base `da61337` + the
uncommitted `entry.py --mode rpc` fix). Every line number below was opened against **that** tree.
**Python:** `/workspaces/aelix-ai/.venv/bin/python`, always with the worktree `PYTHONPATH`.
**Probes:** written to `/workspaces/aelix-rpc/_probe/` during the review and **removed afterwards**
(the worktree is back to `M cli/entry.py` + the pre-existing untracked `tests/rpc/test_rpc_real_child.py`,
and the kernel is clean). The two decisive ones are reproduced verbatim in the appendix.

Everything marked **MEASURED** is a command + its real output, or a source mutation + the test result.
Everything marked **INFERRED** is reasoning from measured facts and is labelled as such.

---

## VERDICT

The plan is **accurate about what it covers and dangerously thin about the thing the sprint owes.**
Its §1–§4 (D1–D4, the kernel finding, the turn contract) are measured-correct — I re-verified the
`core.py:4287/4298/4302` line numbers, the band-gate trap, and the §3.3 flake, and all three hold.

But **Lane E — the `RpcChannel` — is one paragraph for a deliverable that has ~28 distinct
obligations**, and the plan silently drops **4 of the 8 open questions its own recon says "the plan
author must settle"** (`rpc-sprint-recon-transport.md:118`). Two of those four are not preferences;
they are structural blockers that make the deliverable impossible as written.

**Must change before execution:**

1. **Settle one-child-per-task vs one-child-reused, in the plan, with the reason.** MEASURED: reuse is
   structurally impossible (F2/F3) — the 29-command rpc surface cannot change a live child's profile,
   permission mode, or cwd, and the runtime deregisters the child when the delegation returns, which
   reproduces the orphaned-child failure ADR-0197 forbids.
2. **Add the "the child process died" row to the §4 turn contract.** MEASURED: `RpcClient` has zero
   child-death detection; a child that dies mid-turn costs the caller its FULL timeout (F4).
3. **Decide the `RpcClient` seam question (recon Q2) explicitly.** MEASURED: `RpcClientOptions` has no
   argv, env-delete, or exit-callback seam, so Lane E as written cannot be built without a
   product-core change that Lane P does not list (F5).
4. **Replace Lane K's "pick a non-matching name" advice with an allowlist amendment.** MEASURED: every
   honest name for the `_jsonl` budget trips the gate; only a name that hides what it is passes (F9).
5. **State the Lane K blast radius.** MEASURED: the kernel change is invisible to 155 abort/cancel
   tests; the plan's §6.3 mutation gate covers only the new tests, not the regression risk to TUI and
   `--mode json` abort rendering (F10).

---

## F1 — THE DELIVERABLE. `PrintChannel.run`'s obligations, item by item

Read `aelix_agents/print_channel.py` in full (1114 lines). Below is the list the plan owes and does
not have. **PORTS** = reusable unchanged. **CHANGES** = must be re-decided. **MISSING** = no
equivalent exists anywhere on the rpc path today.

### Pre-spawn

| # | Obligation (`print_channel.py`) | Status on rpc |
|---|---|---|
| 1 | `narrow_tools` (`:266-299`, called `:756`) — profile ∩ parent grant ∩ `ALL_TOOL_NAMES` − `{"agent"}` | **PORTS** (pure). Must still reach `build_result(dropped_tools=…)`. |
| 2 | `output_cap` / `timeout_ms` resolution (`:760-767`) | **CHANGES.** `RpcClient` has only class constants; a per-run budget rides `prompt_and_wait(timeout_ms=)`, and MEASURED that raises `TimeoutError` **without killing the turn** — see F4. |
| 3 | 0600 prompt file written BEFORE spawn and recorded on the row (`:799`) | **CHANGES.** The `finally` at `:859-863` unlinks it when `run` returns; on a reused child `run` returns while the child lives. |
| 4 | `row.stopped` pre-spawn race check (`:801-807`) | **PORTS.** |
| 5 | `argv_builder` seam (`:721-732`, `:808-815`) | **CHANGES + MISSING seam.** See F5. |
| 6 | `build_child_env` four amendments (`:400-446`) | **CHANGES + MISSING seam.** See F5/F6. |
| 7 | `stdin=DEVNULL` (`:825`) | **MEANINGLESS** — stdin is the transport. |
| 8 | explicit `limit=STREAM_LIMIT_BYTES` 8 MiB (`:829`) | **MISSING** — `rpc_client.py:120-127` passes no `limit=`. |
| 9 | `start_new_session=True` (`:835`) | **MISSING** — MEASURED below. |
| 10 | `preexec_fn=pdeathsig` (`:836`) | **MISSING.** |
| 11 | spawn failure → envelope, never raise (`:838-840`) | **CHANGES** — `RpcClient.start()` **raises** `RuntimeError`; and its 100 ms grace uses `proc.wait()`, which (per this repo's own `_wait_for_exit` finding) does not resolve while any descendant holds the pipes. |
| 12 | `row.proc` / `row.state` publication + post-spawn stop race (`:842-848`) | **CHANGES** — `RpcClient` never exposes its `Process`; `RunningChild.proc` would have to hold the private `client._proc`. |

**MEASURED — 9, 10 and the descendant reap** (`/workspaces/aelix-rpc/_probe/probe_containment.py`):

```
MEASURED parent pgid=503142 child pid=503720 child pgid=503142
MEASURED same process group as parent? True
MEASURED grandchild pid=503721
MEASURED stop() took 1.00s
MEASURED grandchild STILL ALIVE after stop()? True
```

Same-process-group is exactly the hazard `print_channel.py:830-834` documents: one Ctrl+C SIGINTs
every rpc child at once, and neither parent (`tui/shell.py`) nor an rpc child installs a SIGINT
handler, so nothing converts that into an envelope.

### Streaming

| # | Obligation | Status on rpc |
|---|---|---|
| 13 | Both pipes pumped concurrently for the child's whole life (`:904-909`) | **PORTS** — `rpc_client.py:134-139` already runs two tasks. |
| 14 | `LineAssembler` per-line budget + `dropped_lines` (`stream.py:74-177`) | **MISSING** — MEASURED below. |
| 15 | `reduce_line` wrapped per line so one bad line cannot kill the pump (`:513-522`) | **PARTIAL** — `rpc_client.py:535` already suppresses listener exceptions. The plan's new `reduce_event(state, dict)` must keep it. |
| 16 | `on_stream` tap, exceptions swallowed (`:520-522`) | **PORTS** via `on_event`. |
| 17 | `StderrRing` 64 KiB + `total_bytes` (`:231-263`) | **CHANGES** — rpc keeps 10 MiB and exposes no total. Consequence measured below. |

**MEASURED — 14 and 17** (`/workspaces/aelix-rpc/_probe/probe_misc.py`):

```
MEASURED JsonlLineReader internal buffer after 64 MiB with no newline: 64.0 MiB held
MEASURED LineAssembler (aelix_agents) after the same 64 MiB: buffer=0 bytes, dropped_lines=1
MEASURED attach_jsonl_line_reader signature: (stream, on_line) -> None
MEASURED JsonlLineReader public attrs: ['end', 'feed']       # no counter, no accessor
MEASURED stderr_tail=3 MiB -> summary=51283 bytes (capped), details=3.00 MiB (UNCAPPED)
```

Two omissions here, neither in the plan:

* **The `dropped_lines` counter has no route home.** §4.2 says the counter goes into `_jsonl.py` and
  that "`dropped_lines` then becomes symmetric". It does not follow: `JsonlLineReader` exposes no
  counter, `attach_jsonl_line_reader` returns `None`, and `RpcClient` has no attribute for it. Three
  layers of plumbing the plan does not list.
* **`_jsonl` is used on BOTH sides of the wire.** `rpc_client.py:135` reads the child's stdout with
  it; `rpc_mode.py:2075` reads the SERVER's stdin with it. A budget added to `_jsonl` therefore also
  silently drops oversize **commands**, for which the server emits no error response — so the client's
  `_send` future never resolves and the caller eats the full 30 s `DEFAULT_SEND_TIMEOUT_MS`. The plan
  discusses only the stdout direction.
* **`details` is uncapped** (`envelope.py:149-169`, `_build_details`). `PrintChannel` bounds the input
  at 64 KiB; the rpc path would hand it up to 10 MiB. This is recon **open question 6**, which the
  plan never mentions.

### Completion, abort, teardown

| # | Obligation | Status on rpc |
|---|---|---|
| 18 | `_wait_for_exit` polling `returncode`, **never** `proc.wait()` (`:561-581`) | **MISSING ENTIRELY** — F4. |
| 19 | Completion RACED against child exit + `POST_EOF_EXIT_GRACE_SECONDS` floor (`:937-958`) | **MEANINGLESS in shape** (the rpc child never exits on its own) but the **dead-child leg is mandatory** and absent. |
| 20 | `_drain_after_exit` + `pipe_holder_pids` by pipe inode (`:1020-1051`) | **MISSING** — and MEASURED, `stop()` pays the full 1.00 s SIGTERM grace whenever anything holds the pipes (0.00 s without a holder), because it waits on `proc.wait()`. |
| 21 | `_reap` detached + shielded, `descendant_pids(proc.pid)` (`:1053-1082`) | **MISSING** — grandchild survives (MEASURED above). |
| 22 | `_eager_abort` / `kill_tree` on second cancel (`:1084-1090`) | **MISSING.** |
| 23 | `CancelledError` leg: eager abort, `pumps.cancel()`, re-raise (`:968-975`) | **MISSING** — `RpcClient` has no cancellation contract. |
| 24 | Last-line-of-defence `except Exception` → envelope, never raise (`:976-991`) | **MUST BE WRITTEN.** |
| 25 | `stopped`→`aborted`, `timed_out`→`timeout`, else `ok` proposal (`:1006-1018`) | **PORTS** — but with `exit_code=None` (§4.1) `build_result`'s exit-code clause no longer tightens anything. |
| 26 | `apply_cost_fallback(state, registry())` (`:449-480`) | **PORTS** — needs a `model_registry` callable on `RpcChannel.__init__`. The plan's Lane E does not list it. |
| 27 | `permission_mode` into the envelope (`:791`) | **PORTS as a value** — but see F3: on a reused child it is a **lie** after delegation 1. |
| 28 | `RunningChild.reaper_task` registry-owned so `stop_all` can JOIN it (`:186-189`, `runtime.py:648-651`) | **MISSING** — `RpcClient.stop()` is a coroutine, not a task on the row. |

---

## F2 — Q2: a long-lived channel does not map onto `SubagentResult`, and the plan never says which shape it picks

**MEASURED — usage accumulates across delegations on one live child**
(`/workspaces/aelix-rpc/_probe/probe_crosstalk.py`), two one-turn delegations on one client:

```
MEASURED one _StreamState across TWO delegations on the same live child ->
  turns=2 input=200 output=100 tokens=150 summary='answer-for-req_4'
```

`_StreamState` has no reset (`stream.py:180-232`): `input`/`output`/`cache_*`/`cost`/`turns` are
`+=`, `tokens` is last-write-wins, `summary` is overwritten. A fresh `_StreamState` per delegation
fixes the accumulation — but only if the events can be partitioned per delegation, and they cannot
(F7).

`elapsed_ms`: `PrintChannel` measures from `run()` entry (`:751`, `:789`). On a reused child
delegation 1 pays the whole spawn + `STARTUP_GRACE_MS`, delegation 2 pays none — so §6.2's "rpc
additionally pays `STARTUP_GRACE_MS`" is true of the FIRST delegation only. The plan states it as if
it were a constant.

`id`: `SubagentResult.id` is `sub-<uuid>` minted per delegation by `runtime._run` (`:719`). Fine
either way — but `RunningChild.id` is also the **registry key**, and that is where reuse breaks (F3).

**Which shape does the P3 runtime actually need? MEASURED: one-child-per-task. Reuse is impossible.**
See F3. The plan must say this and say why, because "long-lived channel" in the title implies the
opposite.

---

## F3 — Reuse is structurally impossible, and the plan does not notice

Two independent, measured blockers.

**(a) The rpc command surface cannot re-target a live child.**

```
MEASURED SUPPORTED_COMMANDS ( 29 ):
['abort','abort_bash','abort_retry','bash','clone','compact','cycle_model','cycle_thinking_level',
 'export_html','follow_up','fork','get_available_models','get_commands','get_fork_messages',
 'get_last_assistant_text','get_messages','get_session_stats','get_state','new_session','prompt',
 'set_auto_compaction','set_auto_retry','set_follow_up_mode','set_model','set_session_name',
 'set_steering_mode','set_thinking_level','steer','switch_session']
   'set_permission_mode' available? False
   'set_system_prompt'   available? False
   'set_tools'           available? False
   'set_cwd'             available? False
```

Every `SpawnPlan` field that varies per delegation is **startup-only**: `resolved` (system prompt via
`--append-system-prompt-file`, tools via `--tools`), `permission_mode` (`--permission-mode`), `cwd`
(the process cwd). Only `set_model` / `set_thinking_level` are mutable — which is exactly the three
verbs the handoff names ("`prompt_and_wait` / `stop` / `set_model`", `rpc-sprint-kickoff-handoff.md:37`)
and the plan's Lane E **does not mention `set_model` at all**.

**This is a security consequence, not just a fidelity one.** ADR-0199 §3.9's `permission_floor` —
`runtime.py:744`, `_tighten(grant.mode, permission_floor)` — exists so a batch's later waves honour a
posture the human tightened with shift+tab after wave 1 started. On a reused child there is no
command to tighten with, so the floor would be silently inert, and `SubagentResult.permission_mode`
would report a posture the child is not actually running under. Handoff §5 invariant 3 names this
exact property as one that must not regress. The plan's Lane E mentions "the permission-mode
plumbing" in passing and never confronts it.

**(b) The runtime deregisters a child that outlives its delegation — the orphan ADR-0197 forbids.**

MEASURED (`/workspaces/aelix-rpc/_probe/probe_registry.py`), a stand-in channel whose child outlives
`run()`, driven through the REAL `_SubagentRuntimeImpl`:

```
MEASURED long-lived child started pid=650308
MEASURED delegation 1 returned status=ok; child process alive=True
MEASURED runtime registry after run() returned: rt.list() = []
MEASURED after stop_all(): long-lived child STILL ALIVE = True (pid 650308)
```

`runtime._run`'s `finally` pops the row (`:752`) and `stop_all` iterates `self._children` (`:637`).
A process that outlives its delegation is therefore invisible to `list` / `status` / `stop` /
`stop_all` / teardown — verbatim the failure `stop_all`'s own docstring (`:606-618`) says P3 already
measured once and fixed. Reuse re-opens it. Nothing in the plan touches the registry.

**INFERRED (from (a)+(b)):** `RpcChannel` must be one-child-per-task. That is a legitimate design —
but then the sprint should say what the rpc channel actually buys over `PrintChannel` (mid-turn
`abort`, `steer`, `follow_up`, live `set_model`), because on the strict one-shot path it buys nothing
and costs items 8–12 and 18–28 of F1.

---

## F4 — The §4 turn contract has no row for "the child process died"

§4's table has five rows, all of them in-harness. MEASURED, the sixth is the one that costs the most
(`/workspaces/aelix-rpc/_probe/probe_death.py`; stub exits at 400 ms, past the 100 ms grace):

```
MEASURED start() SUCCEEDED even though the child dies at 400ms (grace is 100ms)
MEASURED wait_for_idle on a DEAD child: waited the FULL 5.01s timeout, child returncode=7
MEASURED _send on a DEAD child raised RuntimeError after 0.00s: RPC server closed stdin: Connection lost
```

`RpcClient` has **no** child-exit observation after `start()`: no `returncode` poll, no EOF→fail-fast,
no `on_exit`. So a child that dies for any reason a real child dies for — no API key, an import
error, OOM-kill, a provider crash — leaves `prompt_and_wait` blocked for its whole budget. At
`DEFAULT_WAIT_FOR_IDLE_MS` that is 60 s; if `RpcChannel` passes the delegation budget
(`DEFAULT_TIMEOUT_MS = 600_000`) it is **ten minutes on an instantly-dead child**.
`PrintChannel._wait_for_exit` (`:561-581`) exists precisely so that cannot happen, and the plan ports
none of it.

Note the asymmetry MEASURED above: `_send` fails FAST (broken stdin) while the event wait fails SLOW.
A terminator contract that says "`RpcChannel` waits on either" therefore behaves completely
differently depending on which leg the failure lands in — and the plan does not say which leg is
authoritative.

Also unstated: `envelope._select_summary`'s stderr rung (`envelope.py:112-146`) is documented as
existing "for" exactly this case — a child that "exits 1 having written **zero** stdout bytes". On
rpc that child produces no envelope at all until the timeout expires.

---

## F5 — Lane E cannot be built without a product-core change Lane P does not list

MEASURED (`/workspaces/aelix-rpc/_probe/probe_seams.py`):

```
=== RpcClientOptions fields (the ONLY injection surface) ===
    cli_path : str | None
    cwd : str | None
    env : dict[str, str]
    provider : str | None
    model : str | None
    args : list[str]
MEASURED: no argv_builder, no env_builder, no timeout, no grace, no on_exit.

MEASURED PrintChannel.__init__: (self, *, grace=5.0, model_registry=None,
                                 argv_builder=None, env_builder=None)

MEASURED RpcClient._build_argv() with no cli_path -> [python, '-m', 'aelix', '--mode', 'rpc']
MEASURED with cli_path='X'                        -> [python, 'X', '--mode', 'rpc']
```

The plan's Lane E promises "injectable `argv_builder`/`env_builder` seams mirroring
`print_channel.py:697-708`" — but those seams live on `PrintChannel`, and the corresponding surface is
`RpcClient`, which has none. So Lane E must either (i) subclass `RpcClient` in the extension and
override two private methods, (ii) add seams to `RpcClientOptions` (product-core, Lane P), or
(iii) reimplement the client in `aelix_agents`. **The plan never picks one.** This is recon open
question 2 (`rpc-sprint-recon-transport.md`, "the biggest scoping fork in the lane") and the plan
answers only its half about *whether* product-core may be edited, not *what shape* the edit takes.

`cli_path` is not an escape and the plan already says so — MEASURED, it splices as a bare script path.

---

## F6 — `RpcClientOptions.env` cannot DELETE a key, so the `AELIX_MCP_CONFIG` pop is not expressible

MEASURED:

```
MEASURED RpcClient._build_env() -> AELIX_MCP_CONFIG present? True (value='/some/mcp.json')
   passing None: None -> still a key: True
MEASURED build_child_env() (print channel) -> AELIX_MCP_CONFIG present? False;
   DEPTH='1'; STDIN_TIMEOUT='1'
```

`_build_env` is `dict(os.environ)` then `env.update(options.env)` (`rpc_client.py:474-477`). There is
no removal. Passing `None` leaves the key present with a `None` value, which `create_subprocess_exec`
will reject. Lane E's "env builder carrying … `AELIX_MCP_CONFIG` pop" is therefore **not
implementable through the existing options object** — while the plan's own landmine forbids putting
env policy into `rpc_client._build_env`. The plan asserts both halves and never resolves them.

Consequence if it is skipped: every rpc-delegated child inherits `AELIX_MCP_CONFIG` and fans out its
own copy of every configured MCP server — the N × M process explosion `build_child_env:432-434`
exists to prevent, now multiplied by P3's `MAX_DELEGATIONS_PER_PROMPT = 12`.

---

## F7 — Q4: the reducer hazard is real and it is worse than "out of order"

**Correction to the brief's premise (MEASURED).** `RpcClient` does **not** dispatch a task per line —
`attach_jsonl_line_reader` (`_jsonl.py:92-110`) calls `_handle_stdout_line` synchronously, in order.
The per-line `create_task` is on the **server** (`rpc_mode.py:2051-2073`), so it is *responses* that
can be emitted out of request order.

The hazard for `RpcChannel`'s reducer is a different and sharper one: **`agent_end` carries no
correlation id.**

```
MEASURED AgentEndEvent fields:  messages: list[AgentMessage];  type: Literal["agent_end"]
```
(`packages/aelix-agent-core/src/aelix_agent_core/types.py:228-230` — no id, no request id, no turn id;
the same is true of every event class at `:166-266`.)

MEASURED, driving the real `RpcClient` (`/workspaces/aelix-rpc/_probe/probe_crosstalk3.py`):

```
MEASURED delegation A abandoned by its caller at 100ms; A's agent_end lands at ~400ms
MEASURED delegation B 'finished' after 0.30s -- B's own work takes 1.50s
MEASURED B's envelope state: summary='answer-for-req_1' turns=1 input=100 saw_agent_end=True
         (req_1 == delegation A)
```

**Delegation B returned delegation A's answer, A's token usage and A's terminator, with `ok=True`,
in a fifth of the time B's own work takes.** `_handle_stdout_line` fans every payload to every
listener (`rpc_client.py:532-536`) with no per-turn scoping, and `prompt_and_wait`'s listener
(`:439-442`) resolves on the first `agent_end` it sees after subscribing.

The plan's §4 says "`RpcChannel` waits on either [the correlated error response or `agent_end`].
Neither requires a new event type." **The error-response leg is correlated by `id`; the `agent_end`
leg is not correlated by anything**, and the plan never says so. Any timeout, any abort, any retry
that leaves a turn running re-arms this.

Second half, also unaddressed: a response that arrives after its request was timed-out and popped
falls through to the **event** listeners (`rpc_client.py:519-536`, deliberate, with a stderr
breadcrumb). The plan's §6.2 asks the *test* to "normalise the interleaved `{"type":"response"…}`
envelopes"; it never decides whether the **channel** should split responses and events onto two taps.
That is recon open question 7, dropped.

---

## F8 — Q5: there is no `agents.*` settings namespace, and a channel choice cannot reach the extension

MEASURED — the entire agents settings surface is one boolean:

* `entry.py:480-506` `_agents_delegation_enabled(parsed, settings_manager)` → `--no-agents` >
  `--agents` > `settings_manager.get_features_agents()`, i.e. `[features] agents`, default `False`.
* The docstring at `:491-496` states a **security property**: the read is GLOBAL-scope-only, "a merged
  read would let any cloned repo switch delegation on from its own `.aelix/settings.json`".
* `entry.py:1577-1592` constructs `AgentsExtension(posture=…, agent_dir=…, cwd=…, project_trusted=…)`
  — **four kwargs, no `channel`**.
* `grep -rn "agents" packages/aelix-coding-agent/src/aelix_coding_agent/settings/` → the directory
  does not exist; the manager lives in `aelix_ai`. There is no `agents.*` namespace at all.

So making a second channel selectable needs **five** things the plan's one line
("`runtime.py:220`, `extension.py` — a channel Protocol") does not name:

1. A Protocol/union replacing **two** concrete annotations — MEASURED, `runtime.py:298`
   `channel: PrintChannel = field(default_factory=PrintChannel)` and `extension.py:205` the same,
   threaded at `extension.py:232`. **The plan's citation `runtime.py:220` is stale** — line 220 is
   inside the `_TOO_MANY_LIVE` message string. (The recon carries the same stale number, so it
   propagated.)
2. A `channel` kwarg on `AgentsExtension`.
3. A selection source (a setting and/or a CLI flag) — none exists.
4. A getter with the **same global-scope-only property** `get_features_agents` has. A merged read
   would let a cloned repo choose the channel, which is the same self-elevation defeat, on a channel
   that (per F1) has none of P2's containment.
5. `tests/cli/test_p2_import_direction.py` must stay green — `entry.py` is already the one allowed
   site, so this is satisfiable, but it means the wiring cannot live anywhere else in product-core.

---

## F9 — Lane K's `_CAP_NAME_RE` mitigation is wrong, and the plan's advice is actively harmful

The plan says: *"the per-line budget constant planned for `_jsonl.py` must not be named
`MAX_LINE_BYTES` … Pick a non-matching name or add it to the allowlist deliberately."*

MEASURED — appending each candidate to `rpc/_jsonl.py` and running
`tests/agents/test_p2_band_boundaries.py::test_product_core_declares_only_the_allowlisted_caps`:

```
MAX_LINE_BYTES      -> 1 failed
RPC_LINE_BUDGET     -> 1 failed
LINE_CAP            -> 1 failed
LINE_LIMIT          -> 1 failed
JSONL_LINE_CEILING  -> 1 failed
BIGGEST_LINE        -> 1 passed
```

`_CAP_NAME_RE` (`test_p2_band_boundaries.py:131-133`) matches
`(^|_)(MAX|MIN)_|_(CAP|LIMIT|BUDGET|CEILING|TIMEOUT|MS|BYTES|TASKS|CHILDREN|CONCURRENCY)$`. **Every
honest name fails; the only name that passes is one that hides what the constant is.** "Pick a
non-matching name" therefore instructs the implementer to obfuscate a cap to slip past a gate that
exists to catch exactly that drift (its own comment at `:127-130`: "STILL A HEURISTIC, and a bare
`FANOUT = 8` defeats it").

The correct route is the second one the plan lists as an afterthought: amend
`_PRODUCT_CORE_CAP_ALLOWLIST` (`:91-107`), which already carries five `rpc/rpc_client.py` entries for
exactly this reason ("pre-P2, rpc plumbing"). The plan should say so, and should record in the ADR
that a **framing** cap is not a **delegation** cap.

---

## F10 — Lane K changes the kernel for every mode, and 155 abort tests cannot see it

MEASURED. I applied the plan's Lane K change as a probe (`core.py`: route the aborted
`CancelledError` into the closure block, widen `except AgentHarnessError` → `except Exception`,
`stop_reason = "aborted"` when the abort fired, return `[]` instead of re-raising on abort), then ran
every abort/cancel/agent_end/closure test in the tree:

```
$ pytest -q -p no:randomly tests/ -k "abort or agent_end or closure or cancel"
155 passed, 6949 deselected in 106.00s
```

Then the **whole tree**, still with the kernel patched:

```
$ pytest -q -p no:randomly tests/ --ignore=tests/agents/test_p2_band_boundaries.py
2 failed, 7094 passed, 1 skipped, 9 warnings in 709.56s (0:11:49)
FAILED tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout
FAILED tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored
```

Both failures are load-sensitive real-subprocess timing tests — the second is the plan's own §3.3
flake, and the first has a 300 ms budget against a real child
(`test_print_channel_spawn.py:654-673`). MEASURED, with the kernel **restored**, both pass 3/3 in
isolation, so neither is attributable to the change:

```
$ git checkout -- .../harness/core.py && pytest -q <those two>   ->  2 passed (x3)
```

**7096 tests, zero signal on a kernel abort-semantics change.** The plan's §6.3 mutation gate says "revert each of the four §4 rows
independently and confirm a named test fails" — that proves the NEW tests work; it says nothing about
the regression surface. And the surface is large: `harness/core.py` is the kernel used by
**interactive, print, json and rpc alike**. After Lane K, every Ctrl+C / ESC abort in the TUI appends
a synthetic `AssistantMessage(content=[TextContent(text="[error] …")])` to `self._state.messages`
(`core.py:4300-4304`) and emits four extra events. The plan never mentions the TUI, `--mode json`,
session persistence of that synthetic message, or `/agents run`'s own abort path.

Two more Lane K omissions:

* The plan says the gate "must be amended in the same commit" but not **how**. MEASURED, one comment
  byte turns `test_kernel_untouched_vs_merge_base` red for the branch's whole life:
  `printf '\n# probe\n' >> harness/core.py` → `FAILED …::test_kernel_untouched_vs_merge_base`.
  A blanket allowlist for `harness/core.py` makes the gate **vacuous for the rest of the sprint**;
  the plan should require a pinned expected-diff instead. (The content half,
  `test_kernel_has_no_subagent_surface`, MEASURED green under the probe — the plan is right about
  that.)
* `except Exception` does **not** catch `asyncio.CancelledError` (it is `BaseException` in ≥3.8), so
  the two bullets are genuinely independent changes. The plan lists them separately, which is right,
  but does not say the abort leg must NOT re-raise (pi returns) while the error leg must. My probe
  had to add a sentinel exception to express it; the plan makes it sound like a filter widening.

---

## F11 — Q6: what §8 defers that is load-bearing

Most of §8 is correctly out of scope. Two items are not what §8 says they are.

**(a) `set_model` and the whole "long-lived" premise.** The handoff defines deliverable 3 as
"a long-lived `RpcChannel` wrapping `RpcClient`, with `prompt_and_wait` / `stop` / **`set_model`**"
(`rpc-sprint-kickoff-handoff.md:37`). §8 does not defer `set_model`; the plan simply never mentions
it. MEASURED (F3), `set_model` is one of only two mutable knobs on a live child, so it is the ONLY
thing that makes reuse partially meaningful — and per F3 it is not enough.

**(b) `--no-session`.** MEASURED:

```
MEASURED oneshot=True  -> ['--mode','json','-p','--no-session', …, 'Task: T']
MEASURED oneshot=False -> ['--mode','rpc', …]          # no --no-session
```

`profile_to_argv(oneshot=False)` — which the recon explicitly recommends reusing "unchanged" — omits
`--no-session`, so **every rpc-delegated child persists a session file**. `--no-session` is real
(`cli/args.py:84,421`) and `entry.py:266,332,351` honour it. Under P3's
`MAX_DELEGATIONS_PER_PROMPT = 12` that is up to twelve session files per user prompt appearing in the
user's `/resume` picker for sessions they never started. Neither §5, §7 nor §8 mentions it.

**Correctly deferred, confirmed:** the approval back-channel is not load-bearing for §5. MEASURED,
`entry.py:1161` makes `has_ui` true only in interactive mode, so an rpc child denies permission
requests by default exactly as a print child does. §8's reasoning holds.

---

## F12 — the recon's own open questions the plan silently drops

`rpc-sprint-recon-transport.md:118` lists 8 questions "the plan author must settle". Scored against
the plan:

| # | Question | Plan |
|---|---|---|
| 1 | Which terminator is authoritative? | **ANSWERED** (§4) — but incomplete, see F4. |
| 2 | Does the owner accept product-core edits, and in what shape? | **HALF** — D1 answers *whether*, not *what shape*. See F5. |
| 3 | Is an rpc child even the right shape for a subagent? What does "the same task" mean across a channel that terminates and one that does not? | **DROPPED.** §4.1 addresses `exit_code` only. This is F2/F3. |
| 4 | What replaces `AELIX_STDIN_TIMEOUT=1`? | **DEFERRED TO IMPLEMENTATION** — Lane E says "re-reasoned" and never states the answer. |
| 5 | `dropped_lines`: budget or documented asymmetry? | **ANSWERED** (§4.2) — but the plumbing is unlisted, F1/14. |
| 6 | `STDERR_MAX_BYTES` 10 MiB vs `STDERR_RING_BYTES` 64 KiB | **DROPPED.** Consequence measured in F1/17. |
| 7 | Filter response envelopes in the test, or split taps at the channel boundary? | **HALF** — §6.2 decides the test, not the channel. F7. |
| 8 | Does chain mode need `steer`/`follow_up` rather than `prompt`? | **DROPPED.** `prompt` rejects on busy and (today) drops images; `steer`/`follow_up` enqueue and decode images. This is the turn-boundary design for `{previous}` substitution. |

**4 of 8 dropped, 2 half-answered.**

One recon item is now **STALE and dangerous** and the plan does not retire it: the recon's gap list
says "Relax `_reject_unsupported`, which currently raises for any mode != 'single' … This is where P3
items 1 and 2 land." P3 shipped and **deliberately kept it raising** —
`runtime.py:80-92` (`_UNSUPPORTED_MODE`: *"P3 SHIPS PARALLEL AND CHAIN, AND THIS STILL RAISES —
deliberately"*), function now at `runtime.py:821-827` (not `:540-546`). MEASURED, relaxing it is
completely unguarded:

```
# mutate: if mode not in ("single","parallel","chain"): raise ...
$ pytest -q -p no:randomly tests/agents/
1 failed, 91 passed        # the 1 failure is my unrelated kernel probe, still applied
```

An implementer working from the recon would break P3's "ONE CALL IS ONE CHILD" invariant with zero
test signal. The plan should explicitly retire that recon item, and Lane T should add the missing pin.

**Answering the brief's Q3 directly.** `_reject_unsupported` has nothing to do with channels: it takes
`(mode, background)` and is called from both doors (`runtime.py:459`, `:537`). What the runtime does
"when asked for a channel" today is: nothing — the channel is a dataclass field fixed at construction
(`runtime.py:298`, `extension.py:205`, `extension.py:232`), with no selection path from `entry.py`,
no setting, and no flag. See F8 for the five things that must change.

---

## Smaller omissions, each measured

* **`RunningChild.proc`** is typed `Any` but every consumer treats it as an `asyncio` `Process`
  (`abort_child:675`, `_reap:1074` reads `proc.pid`, `progress.py`). `RpcClient` keeps its process
  private (`self._proc`). The plan does not say what goes on the row.
* **`AgentsExtension.channel` is a session-wide singleton** (`extension.py:205,232`) and `PrintChannel`
  is documented "Stateless between runs … so a single instance is shared by the whole session"
  (`:706-708`). An `RpcChannel` holding a live client is **not** stateless, and up to
  `MAX_LIVE_CHILDREN = 4` delegations share that one instance concurrently. The plan does not say
  whether `RpcChannel` is per-session or per-delegation.
* **`asyncio` teardown noise.** MEASURED on every containment probe:
  `Exception ignored in BaseSubprocessTransport.__del__ … RuntimeError: Event loop is closed`.
  `RpcClient.stop()` sets `self._proc = None` (`:202`) without closing the transport. In a TUI this
  prints a traceback the user cannot act on. Not in the plan.
* **§3.3 flake CONFIRMED.** MEASURED, 6 consecutive runs of
  `tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored`:
  `passed, passed, FAILED, passed, passed, passed`. The plan is right to fix it here.
* **`/agents show`'s dry run stops being the truth.** `build_child_argv`'s docstring (`:379-381`) says
  `child_trust_argv` is "the same call the `/agents show` dry-run renders, so the dry run stays the
  truth". MEASURED, the only `profile_to_argv` caller in the shipped tree is `print_channel.py:390`
  (`oneshot=True`). With an rpc channel selected, the panel would render a `--mode json -p` command
  while the real spawn is `--mode rpc`. INFERRED consequence; the render path itself was not exercised.

---

## What I could NOT falsify (the plan is right about these)

* `core.py:4287-4295` / `:4298` / `:4302` — MEASURED, the line numbers are exact on this tree.
* The two-gate BAND-GATE CORRECTION — MEASURED, a comment byte reds
  `test_kernel_untouched_vs_merge_base`; `test_kernel_has_no_subagent_surface` stays green.
* §3.3 flake — MEASURED, reproduced 1/6.
* `-m aelix` default argv and the `cli_path`-is-not-an-escape claim — MEASURED.
* `_read_piped_stdin` gated to print/json — MEASURED exact:
  `grep -n 'app_mode in ("print", "json")' entry.py` → `1346:` (and `2188:`). The plan's `:1346` is
  correct.
* D2's "the interop consumer does not exist" — not re-measured; out of this lens.

---

## Appendix — the two decisive probes, verbatim

Run from `/workspaces/aelix-rpc` with
`PYTHONPATH=/workspaces/aelix-rpc/packages/aelix-coding-agent/src:/workspaces/aelix-rpc/packages/aelix-agent-core/src:/workspaces/aelix-rpc/packages/aelix-ai/src`
and `/workspaces/aelix-ai/.venv/bin/python`.

### A. F7 — a stale `agent_end` terminates the wrong delegation

`_probe/stub_slow.py` (a minimal rpc server; the prompt message's first token is the turn's duration):

```python
import sys, json, threading, time
def emit(o):
    sys.stdout.write(json.dumps(o)+"\n"); sys.stdout.flush()
def run_turn(rid, delay):
    time.sleep(delay)
    emit({"type":"agent_start"}); emit({"type":"turn_start"})
    emit({"type":"message_end","message":{"role":"assistant","stop_reason":"end_turn",
          "content":[{"type":"text","text":f"answer-for-{rid}"}],
          "usage":{"input":100,"output":50,"total_tokens":150}}})
    emit({"type":"agent_end"})
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    cmd=json.loads(line)
    if cmd.get("type")=="prompt":
        emit({"type":"response","command":"prompt","success":True,"id":cmd.get("id")})
        d=float(cmd.get("message","0.05").split()[0])
        threading.Thread(target=run_turn, args=(cmd.get("id"), d), daemon=True).start()
    else:
        emit({"type":"response","command":cmd.get("type"),"success":True,"id":cmd.get("id")})
```

`_probe/probe_crosstalk3.py`:

```python
import asyncio, time, json
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions
from aelix_agents.stream import _StreamState, reduce_line
STUB="/workspaces/aelix-rpc/_probe/stub_slow.py"
async def main():
    c=RpcClient(RpcClientOptions(cli_path=STUB)); await c.start()
    try:
        await c.prompt_and_wait("0.40 delegation-A", timeout_ms=100)
    except TimeoutError:
        print("MEASURED delegation A abandoned at 100ms; A's agent_end lands at ~400ms")
    t0=time.monotonic()
    ev = await c.prompt_and_wait("1.50 delegation-B", timeout_ms=5000)   # B needs 1.5s
    print(f"MEASURED delegation B 'finished' after {time.monotonic()-t0:.2f}s")
    st=_StreamState()
    for e in ev: reduce_line(st, json.dumps(e))
    print(f"MEASURED B's envelope state: summary={st.summary!r} turns={st.turns} "
          f"input={st.input} saw_agent_end={st.saw_agent_end}  (req_1 == delegation A)")
    await asyncio.sleep(1.4); await c.stop()
asyncio.run(main())
```

Output:

```
MEASURED delegation A abandoned at 100ms; A's agent_end lands at ~400ms
MEASURED delegation B 'finished' after 0.30s
MEASURED B's envelope state: summary='answer-for-req_1' turns=1 input=100 saw_agent_end=True
```

### B. F3(b) — a child that outlives its delegation is unreachable by `stop_all`

`_probe/probe_registry.py` — a stand-in channel driven through the REAL `_SubagentRuntimeImpl`:

```python
import asyncio, sys, subprocess
from aelix_agents.runtime import _SubagentRuntimeImpl, SubagentHost
from aelix_coding_agent.subagent_contract import SubagentResult, ResolvedProfile
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_agents.consent import SpawnGrant

class LongLivedChannel:
    def __init__(self): self.proc = None
    async def run(self, plan, *, child=None, on_stream=None):
        if self.proc is None:
            self.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
            print(f"MEASURED long-lived child started pid={self.proc.pid}")
        child.state = "done"
        return SubagentResult(id=plan.id, profile="p", ok=True, status="ok", summary="done")

async def main():
    prof = AgentProfile(name="p", description="d", body="b", file_path="/tmp/p.md", scope="user")
    resolved = ResolvedProfile(name="p", profile=prof, source_path="/tmp/p.md", scope="user")
    ch = LongLivedChannel()
    rt = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: "/tmp"), channel=ch)
    grant = SpawnGrant(consented=True, mode=PermissionMode.PLAN, profile="p",
                       source_path="/tmp/p.md", scope="user", widened=False)
    r = await rt.spawn_granted(grant, resolved, "task 1")
    print(f"MEASURED delegation 1 returned status={r.status}; child alive={ch.proc.poll() is None}")
    print(f"MEASURED runtime registry after run() returned: rt.list() = {rt.list()}")
    await rt.stop_all(); await asyncio.sleep(0.3)
    print(f"MEASURED after stop_all(): child STILL ALIVE = {ch.proc.poll() is None} (pid {ch.proc.pid})")
    ch.proc.kill()
asyncio.run(main())
```

Output:

```
MEASURED long-lived child started pid=650308
MEASURED delegation 1 returned status=ok; child alive=True
MEASURED runtime registry after run() returned: rt.list() = []
MEASURED after stop_all(): child STILL ALIVE = True (pid 650308)
```
