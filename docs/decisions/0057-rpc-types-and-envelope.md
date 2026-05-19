# 0057. RPC Types and Envelope (RpcCommand union + Response envelope + SessionState)

Status: Accepted (Sprint 6d / Phase 4.4 / W6 shipped)

## Context

Pi `packages/coding-agent/src/modes/rpc/rpc-types.ts` (262 LOC) is the
authoritative wire schema for the RPC mode protocol. Aelix's port lives
at `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py`
(P-105 placement decision).

The W0 audit established four invariants Sprint 6d must honour:

1. **P-107 — 29 RpcCommand variants** at Pi SHA 734e08e. The Sprint 6d
   spec preamble cited "28" as the count; the fixture's
   `rpc_command_types` list is the authoritative wire surface and lists
   29 discriminators. W4 M2 / P-121 amends every count reference (28 →
   29 / 19 → 20) across module docstrings, fixture, and tests.
2. **P-108 — Uniform error envelope.** Every command can fail; the
   failure shape is `{id?, type: "response", command, success: false,
   error: string}`. The success shape is per-command but always carries
   `{id?, type: "response", command, success: true, data?}`. The `id`
   echo is critical for client-side correlation via the
   `pending_requests` map.
3. **P-128 — Per-variant field roster.** Each Pi RpcCommand variant has
   a fixed field set (e.g. `set_model` ⊇ `{provider, model_id}`,
   `bash` ⊇ `{command}`). The closure pin enforces the table
   mechanically so renames/drops trip per-command.
4. **12-field RpcSessionState.** Pi `rpc-types.ts:90-103` exposes
   exactly 12 fields; the Aelix port renames them to snake_case in
   Python and remaps to camelCase on the wire via `to_json()` / accepts
   camelCase on `from_json()`.

## Decision

`aelix_coding_agent.rpc.rpc_types` ships as a port of Pi's TS module:

- **29 `@dataclass(frozen=True)` variants** of `RpcCommand` covering
  every Pi discriminator (`prompt`, `steer`, `follow_up`, `abort`,
  `new_session`, `get_state`, `set_model`, `cycle_model`,
  `get_available_models`, `set_thinking_level`, `cycle_thinking_level`,
  `set_steering_mode`, `set_follow_up_mode`, `compact`,
  `set_auto_compaction`, `set_auto_retry`, `abort_retry`, `bash`,
  `abort_bash`, `get_session_stats`, `export_html`, `switch_session`,
  `fork`, `clone`, `get_fork_messages`, `get_last_assistant_text`,
  `set_session_name`, `get_messages`, `get_commands`).
- **`RpcSuccessResponse` + `RpcErrorResponse`** dataclasses with
  `to_json()` helpers that omit `id` when `None` and (for success) omit
  `data` when None — Pi `data === undefined ? undefined : data`.
- **`RpcSessionState`** with 12 fields (`session_id`, `thinking_level`,
  `is_streaming`, `is_compacting`, `steering_mode`, `follow_up_mode`,
  `message_count`, `pending_message_count`, `auto_compaction_enabled`,
  `model`, `session_file`, `session_name`).
- **`RpcExtensionUIRequest`** 9-method union (TYPES only — bridge
  deferred to Sprint 6f per ADR-0058) + **`RpcExtensionUIResponse`**
  3-shape union (value / confirmed / cancelled).
- **`parse_rpc_command(payload)`** discriminator dispatch with
  camelCase ↔ snake_case remap; raises `ValueError` on unknown type and
  `TypeError` on missing required fields.
- **`command_to_json(cmd)`** serialize path that emits `type` first,
  drops `None`s, and applies the multi-word camelCase remap. W4 m6
  binds `wire_key: str` before the dict subscript to satisfy pyright
  `reportArgumentType`.

**Closure pin allowlists** (importable from the module):

```python
RPC_COMMAND_TYPES: frozenset[str]               # all 29 discriminators
RPC_EXTENSION_UI_REQUEST_METHODS: frozenset[str] # all 9 UI method names
```

## Consequences

- A future PR that adds a Pi RpcCommand variant MUST land both the
  dataclass + the `_RPC_COMMAND_REGISTRY` entry + the
  `PI_COMMAND_FIELDS` row in the closure pin OR add it to
  `DEFERRED_COMMANDS` with an owning ADR. The closure pin's
  exhaustiveness assertion trips otherwise.
- Per-variant field-set assertions (P-128) catch renames / dropped
  fields without aggregate masking.
- `data` omission semantics on `to_json()` mirror Pi's
  `undefined`-vs-`null` distinction. Clients that look up `data` via
  `payload.get("data")` see `None` (Python idiom for missing); the
  server never emits an explicit `"data": null` member.

## Related

- ADR-0020 — RPC Mode for Multi-Language Clients (Accepted Sprint 6d).
- ADR-0034 — Pi reference version pin.
- ADR-0056 — RPC JSONL protocol.
- ADR-0058 — Phase 4.4 strict superset closure.

## Phase

Sprint 6d / Phase 4.4 (shipped — closure pin Green).
