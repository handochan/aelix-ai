# 0058. Phase 4.4 Strict Superset Closure

Status: Accepted (Sprint 6d / Phase 4.4 / W6 shipped)

## Context

ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055 established the Aelix
strict-Pi-parity-superset invariant for Phases 2.1 / 2.2 / 3 / 4.1 /
4.2 / 4.3. Each closure ADR pins a regression-guard test under
`tests/pi_parity/` that asserts every Pi-verified surface in scope has a
corresponding binding in Aelix, OR sits in a deferred allowlist with an
owning ADR.

Sprint 6d lands the RPC mode JSONL protocol (ADR-0056) + RpcCommand /
Response / SessionState types (ADR-0057) + rpc_mode dispatcher +
RpcClient subprocess wrapper + CLI `--mode rpc` flag. The W4 code
review + W5 Pi parity audit produced **1 BLOCKING + 10 MAJOR +
many MINOR** drift findings; Sprint 6d W6 applied the must-fix triage
in 5 atomic commits.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.4 strict-superset closure pin is
`tests/pi_parity/test_phase_4_4_strict_superset.py`. It asserts the
Sprint 6d roster (P-105 → P-129 + W4 M1..M5 + W4 m1..m10) PLUS the
cumulative invariant from ADR-0039 / 0040 / 0044 / 0046 / 0050 / 0055.

### Roster (Sprint 6d)

#### W0 binding-spec findings (P-105..P-114)

| Finding | Subject | Resolution |
|---|---|---|
| **P-105** | Pi RPC lives under `coding-agent/modes/rpc/`, not `agent/` | Aelix ports under `aelix_coding_agent.rpc/` (flat) per spec §0 |
| **P-106** | JSONL framing is LF-only (Pi avoids U+2028/U+2029) | `_jsonl.py` uses `\n`-split + CR strip; round-trip closure-pinned |
| **P-107** | Pi `RpcCommand` has 29 variants; Aelix can satisfy only 9 directly | 9 supported + 20 deferred = 29 (closure pin asserts) |
| **P-108** | Pi `RpcResponse` envelope shape is uniform; error path is a separate union member | `RpcSuccessResponse` + `RpcErrorResponse` dataclasses with `id` echo |
| **P-109** | RpcMode is fire-and-forget event subscriber, NOT request-response | `harness.subscribe(_on_agent_event)` pipes events without transformation |
| **P-110** | Pi RpcClient spawns Node child; Aelix needs Python equivalent | `asyncio.create_subprocess_exec` with `python -m aelix --mode rpc` |
| **P-111** | CLI must accept `--mode rpc` flag | `src/aelix/__main__.py` adds `--mode {interactive,rpc}` |
| **P-112** | Pi event sender uses `takeOverStdout()` to hijack stdout | `contextlib.redirect_stdout(sys.stderr)` at entry |
| **P-113** | Pi RpcClient `id` generation is numeric monotonic counter | `itertools.count(1)` per instance |
| **P-114** | Pi RpcClient default 60s `waitForIdle` watches for `agent_end` | `wait_for_idle()` subscribes to event stream + listens for `agent_end` |

#### W4 + W5 W6 must-fix BLOCKING

| Finding | Subject | Resolution |
|---|---|---|
| **P-115** | `_handle_bash` emitted wrong wire shape (`{output, exitCode, truncation: <dict>}`) — Pi `BashResult` is `{output, exitCode, cancelled, truncated, fullOutputPath?}` | Replaced data dict with Pi 4/5-key shape; `cancelled` hardcoded False until Sprint 6f bash cancel token |

#### W4 + W5 W6 must-fix MAJOR

| Finding | Subject | Resolution |
|---|---|---|
| **W4 M1** | `session_file` always `None` because storage attr is `_file_path`, not `_path` | New `harness.session_file` property probes `_file_path` first, falls back to `_path` |
| **W4 M2 / P-121** | Count drift docstrings 28/19 → reality 29/20 | Mass docstring update across `rpc_mode.py`, `rpc_types.py`, `rpc_client.py`, tests, fixture |
| **W4 M3** | `RpcClient.stop()` cancels Futures without meaningful exception | `future.set_exception(RpcClientError("rpc", "RPC server stopped"))` |
| **W4 M4** | `_drain_stderr` unbounded — memory exhaustion risk | Cap at 10 MB (`STDERR_MAX_BYTES`) with FIFO truncation |
| **W4 M5** | Closure pin doesn't catch `session_file` shape drift | New regression test builds `JsonlSessionStorage`-backed `Session`, asserts the real path |
| **P-116** | `is_streaming` underreports during tool execution (uses `phase == "turn"` proxy) | `is_streaming = harness.phase != "idle"` (covers turn + tool exec + compaction) |
| **P-117** | `_handle_new_session` silently drops `parent_session` | Returns Pi-shape error envelope citing Sprint 6f deferral |
| **P-118** | `_handle_get_state` reached into `_steering_queue._messages` / `_session` / `_cached_session_name` / `_options` | New harness public properties: `pending_message_count`, `session_file`, `session_name`, `steering_mode`, `follow_up_mode`. AST-walk closure pin asserts no `harness._foo` reads |
| **P-119 / W4 m2** | `_handle_prompt` swallows synchronous failures with `contextlib.suppress(Exception)` | Try/except logs to stderr; synthetic terminal event emission deferred to Sprint 6f when harness exposes event bus |
| **P-120** | Parse-error envelope used user's claimed `command` instead of `"parse"` | Always `command="parse"` on JSON/type/value error (Pi parity) |

#### W4 + W5 W6 must-fix MINOR (applied)

| Finding | Subject | Resolution |
|---|---|---|
| **P-127** | Closure-pin JSONL framing weak (no U+2028 round-trip) | Round-trip test with literal U+2028 + U+2029 in payload |
| **P-128** | Per-variant RpcCommand field-set assertion missing | `PI_COMMAND_FIELDS` dict maps each of 29 commands to field set; closure pin asserts per-command |
| **W4 m1** | `auto_compaction_enabled=True` hardcoded silently | Cross-reference comment pointing at `DEFERRED_COMMANDS["set_auto_compaction"]` |
| **W4 m3** | `_handle_prompt` uses `hasattr` feature flag on private `_pending_tasks` | `hasattr` guard dropped + module-top coupling note |
| **W4 m6** | Pyright `reportArgumentType` in `command_to_json` | Bind `wire_key: str` before dict subscript |
| **W4 m8** | Stale response silently swallowed | Log to stderr when tracked id is no longer in `pending_requests` |
| **W4 m9** | `stop()` final `await proc.wait()` has no timeout | Wrapped in `asyncio.wait_for(proc.wait(), timeout=5.0)` with `contextlib.suppress(TimeoutError)` |

### Closure invariant

```python
# Pi RpcCommand variant cardinality:
len(RPC_COMMAND_TYPES) == 29

# Sprint 6d partition:
SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS.keys()) == RPC_COMMAND_TYPES
SUPPORTED_COMMANDS.isdisjoint(set(DEFERRED_COMMANDS.keys()))
len(SUPPORTED_COMMANDS) == 9
len(DEFERRED_COMMANDS) == 20

# Supported (9): prompt, abort, new_session, get_state, get_messages,
#                compact, bash, set_thinking_level, set_session_name
# Deferred (20): steer, follow_up, set_model, cycle_model,
#                get_available_models, cycle_thinking_level,
#                set_steering_mode, set_follow_up_mode,
#                set_auto_compaction, set_auto_retry, abort_retry,
#                abort_bash, get_session_stats, export_html,
#                switch_session, fork, clone, get_fork_messages,
#                get_last_assistant_text, get_commands

# Per-variant field-set table (P-128) is exhaustive:
set(PI_COMMAND_FIELDS.keys()) == RPC_COMMAND_TYPES

# Wire constants pinned (P-114):
RpcClient.DEFAULT_SEND_TIMEOUT_MS == 30_000
RpcClient.DEFAULT_WAIT_FOR_IDLE_MS == 60_000
RpcClient.STARTUP_GRACE_MS == 100
RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS == 1_000
RpcClient.STDERR_MAX_BYTES == 10 * 1024 * 1024
```

### What ships

- `aelix_coding_agent.rpc/` package: `_jsonl.py` + `rpc_types.py` +
  `rpc_mode.py` + `rpc_client.py` + facade `__init__.py` (~1,100 prod
  LOC).
- `AgentHarness` public-API additions: `pending_message_count`,
  `session_file`, `session_name`, `steering_mode`, `follow_up_mode`.
- CLI: `src/aelix/__main__.py` gains `--mode {interactive,rpc}`.
- Closure pin
  `tests/pi_parity/test_phase_4_4_strict_superset.py` with P-127
  U+2028 round-trip + P-128 per-variant field-set + W4 M5 session_file
  regression.
- W6 regression suite `tests/rpc/test_w6_regressions.py` (14 tests
  pinning P-115 / W4 M1 / P-116 / P-117 / P-118 / P-119 / P-120
  explicitly).
- Pi-parity fixture `tests/pi_parity/fixtures/pi_rpc_mode_734e08e.json`
  (29 command count, 12 session-state field shape, 9 UI method list).

### Forward-compat clause

Phase 4.4 RpcCommand coverage is now at **9 of 29**. Any future Pi
sprint that adds:

1. A new Pi RpcCommand variant MUST either:
   - Land the corresponding Aelix handler in the same PR (add to
     `SUPPORTED_COMMANDS` + register in `_SUPPORTED_HANDLERS`).
   - Add an entry to `DEFERRED_COMMANDS` with an owning ADR.
2. A new RpcExtensionUI method MUST either:
   - Land the corresponding Aelix bridge in the same PR.
   - Add an entry to a future `_RPC_EXTENSION_UI_DEFERRED_METHODS` with
     an owning ADR.

The forward-compat clauses from ADR-0039 / 0046 / 0050 / 0055 continue
to apply: any deferred entry that subsequently gains the missing
binding MUST be dropped from the allowlist in the same PR (enforced by
the closure pin's exhaustiveness assertion).

## Consequences

### Carry-forward — Sprint 6e (ModelRegistry + extension/skill aggregation)

- **P-129** — `id` type validation hardening (also defensive in
  `parse_rpc_response`).
- Deferred commands owned by Sprint 6e per
  `rpc_mode.DEFERRED_COMMANDS`:
  - `set_model`, `cycle_model`, `get_available_models` — needs
    central ModelRegistry.
  - `get_commands` — needs extension/skill/template aggregation.

### Carry-forward — Sprint 6f (harness command paths + session tree + bash cancel + UI bridge)

- **P-122** — `id` field order on `RpcSuccessResponse`/`RpcErrorResponse`
  (cosmetic).
- **P-123** — Redundant `_handle_compact` inner try/except.
- **P-124** — `extension_ui_response` no-op semantics (bridge ships
  with Sprint 6f UI work).
- **P-125** — `wait_for_idle` standalone listener race (Pi has the same
  shape).
- **P-126** — `rebindSession` seam for Sprint 6f switch/fork/clone.
- **W4 m2** — `_handle_prompt` synthetic event emission so
  `wait_for_idle` clients unblock on error (Sprint 6f when harness
  exposes a public event-emit method).
- **W4 m4** — bash guardrail audit (Sprint 6f hardening).
- **W4 m7** — Closure-pin fixture path (installed-package compatibility
  — Sprint 6f as part of test-infra hardening).
- **W4 m10** — Compact failure shape diagnostic.
- **W4 N1..N9** — Code quality cleanups.
- Deferred commands owned by Sprint 6f per
  `rpc_mode.DEFERRED_COMMANDS`:
  - `steer`, `follow_up` — separate harness command paths.
  - `cycle_thinking_level` — UI-side cycling logic.
  - `set_steering_mode`, `set_follow_up_mode` — queue mode flags.
  - `set_auto_compaction`, `set_auto_retry`, `abort_retry` — auto-retry
    harness loop additions.
  - `abort_bash` — bash tool cancellation token (Sprint 6f hardening of
    Sprint 5b tool).
  - `get_session_stats`, `export_html` — session inspection surface.
  - `switch_session`, `fork`, `clone`, `get_fork_messages`,
    `get_last_assistant_text` — full session tree navigation API.

### Immediate consequences

- Sprint 6d ships the durable RPC boundary every multi-language client
  routes through; Sprints 6e/6f slot in without framework change.
- The bash wire-shape fix (P-115) means a Pi-shaped client that
  dispatches on the `BashResult` 4/5-key shape works against Aelix
  today; the prior `{truncation: <dict>}` shape would have made every
  bash invocation parse-fail.
- The harness public-API additions (P-118) close the last `_`-prefixed
  read in `rpc_mode.py`; future RPC dispatchers won't accidentally
  couple to harness internals.
- The closure-pin strengthening (P-127 + P-128) means a future PR that
  adds a Pi RpcCommand variant without updating the table mechanically
  trips per-command.

## Related

- ADR-0009 — Python-first SDK (partially superseded by ADR-0020).
- ADR-0020 — RPC Mode for Multi-Language Clients (Draft → Accepted
  Sprint 6d).
- ADR-0034 — Pi reference version pin (amended Sprint 6d).
- ADR-0035 — Error code taxonomy.
- ADR-0046 — Phase 4.1 strict superset closure (forward-compat clause
  inherited).
- ADR-0050 — Phase 4.2 strict superset closure.
- ADR-0054 — RPC mode deferred to Sprint 6d (this ADR closes the
  carry-forward).
- ADR-0055 — Phase 4.3 strict superset closure.
- ADR-0056 — RPC JSONL protocol.
- ADR-0057 — RPC types and envelope.

## Phase

Sprint 6d / Phase 4.4 (shipped — closure pin Green; 9 of 29 RpcCommand
variants live; 20 deferred with owning ADR-0058).
