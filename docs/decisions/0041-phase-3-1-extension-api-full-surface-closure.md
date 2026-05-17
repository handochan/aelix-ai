# 0041. Phase 3.1 Extension API Full Surface Closure

Status: Accepted (Sprint 5a / Phase 3.1.1 shipped — 2026-05-17)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## 1st-principle invariant

> **Aelix Phase 3.1 is a strict Pi-parity superset of the
> `aelix-coding-agent` Extension surface.** Every Pi-verified
> ExtensionAPI method, ExtensionContext field, ExtensionRuntimeActions
> entry, and Extension dataclass collection in the Phase 3.1 scope has a
> corresponding binding (real or throwing-stub-deferred) in Aelix.
> Aelix-additive members (`add_cleanup`, `error_mode` overloads,
> `register_flag` / `get_flag` from Phase 1.2) remain documented as
> additive and never shadow Pi semantics.
>
> Top-level principle (binding, all sprints): **"pi agent를 완전 동일하게
> 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

## Context

Sprint 5a (Phase 3.1) ships three closely-coupled deliverables:

1. **§A — Extension auto-discovery**: 3-tier Pi-parity directory scan
   (`cwd/.aelix/extensions/` → `~/.aelix/extensions/` → explicit) plus an
   Aelix-additive `entry_points` pass loaded LAST. The Draft ADR-0028
   inverted this priority; Sprint 5a corrects to **directory-scan
   primary, entry_points additive** (P-21 reversal).
2. **§B — Full ExtensionAPI surface**: 8 methods → 23 non-event methods
   + 31 `on()` overloads. New methods land as a mix of real bindings
   (`set_session_name` / `get_session_name` / `set_label` / `set_model` /
   `set_thinking_level` / `get_thinking_level` / `get_all_tools` /
   `exec` plus `register_command` / `register_shortcut` /
   `register_message_renderer` / `register_provider` /
   `unregister_provider`) and throwing-stub-deferred bindings
   (`send_message` / `send_user_message` / `append_entry` /
   `get_commands`). New `events` property exposes the shared per-runtime
   :class:`EventBus`.
3. **§C — Full ExtensionContext**: 5 → 14 fields. New non-UI bindings:
   `has_ui` (constant `False`), `session_manager`, `model_registry`,
   `signal`, `has_pending_messages`, `shutdown`, `get_context_usage`,
   `compact`. The `ui` field is deferred to ADR-0033 (Phase 5) but
   exposed so factory code does not `AttributeError`.
4. **§D — 3 new hook events**: `input` / `user_bash` /
   `resources_discover` (P-24/P-25/P-26 — verified in Pi `coding-agent`
   at SHA `734e08e`). Registered without emit (mirrors Sprint 3a
   `session_*` pattern); emit sites are owned by ADR-0042 (Sprint 5b CLI
   loop).

## Sprint 5a findings roster (P-21 through P-28)

Inherited / new in Sprint 5a:

| ID | Origin | Subject | Resolution |
| ---- | ---------------------- | ------------------------------------------------------------------------------------------------ | ---------- |
| P-21 | Sprint 5a W1 architect | ADR-0028 Draft INVERTED — treated `entry_points` as primary; Pi truth is directory-scan primary. | Reversed in this ADR + ADR-0028 Accepted: **directory-scan PRIMARY (Pi parity), entry_points ADDITIVE**. |
| P-22 | Sprint 5a W1 architect | Pi ExtensionAPI = **52 entries** (29 `on()` event names + 23 non-event methods including `events`). The spec's "48 methods" was a counting heuristic; the SHA-pinned fixture `pi_extension_api_methods_734e08e.json` is authoritative. Aelix had 8. | 23 non-event methods + 31 `on()` overloads (Sprint 3a 28 + Sprint 5a 3) landed in Aelix `ExtensionAPI` (54 total = 23 non-event + 31 `on()` overloads, 2 additive overloads for symmetry). Deferred bindings (`send_message`, `send_user_message`, `append_entry`, `get_commands`) raise `ExtensionError("unbound")` until ADR-0042 lands. |
| P-23 | Sprint 5a W1 architect | Pi ExtensionContext = 14 fields. Aelix had 5. | All 14 fields exposed on Aelix `ExtensionContext`. 13 production bindings + `ui` deferred to ADR-0033 (Phase 5 TUI) with explicit `invalid_state` raise. |
| P-24 | Sprint 5a W1 architect | Sprint 3a P-1 mis-classified `InputEvent` as wishlist; Pi DOES ship at `coding-agent/extensions/types.ts:619-625`. | Sprint 5a registers `InputHookEvent` + `InputResult` (Continue/Transform/Handled) + reducer + overload; emit deferred to ADR-0042. |
| P-25 | Sprint 5a W1 architect | Sprint 3a P-1 mis-classified `UserBashEvent`; Pi ships at `types.ts:602-609`. | `UserBashHookEvent` + `UserBashResult` (stub `BashOperations` / `BashResult` Protocols) + reducer + overload landed; emit deferred. |
| P-26 | Sprint 5a W1 architect | Sprint 3a P-1 mis-classified `ResourcesDiscoverEvent`; Pi ships at `types.ts:512-517`. | `ResourcesDiscoverHookEvent` + `ResourcesDiscoverResult` + collect+dedup reducer + overload landed; emit deferred. |
| P-27 | Sprint 5a W1 architect | Pi Extension = 7 collections + 2 metadata. Aelix had 4 collections. | Extended Aelix `Extension` with `commands`, `shortcuts`, `message_renderers`, `source_info`, `resolved_path`. `cleanups` retained as Aelix-additive. |
| P-28 | Sprint 5a W1 architect | Pi ExtensionRuntime = 15 actions. Aelix had 3. | Extended `ExtensionRuntimeActions` to 15 fields; 12 new actions default to throwing stubs, harness rebinds the real ones for `set_session_name`, `get_session_name`, `set_label`, `set_model`, `get_thinking_level`, `set_thinking_level`, `get_all_tools` at construction. |

## Decision

### Closure

With Sprint 5a shipped:

- Aelix `HookEventName` Literal expands from 28 → 31 (input, user_bash,
  resources_discover added).
- `ExtensionAPI` exposes every Pi non-event member + 31 `on()` overload
  surface; 4 members are throwing stubs until ADR-0042 lands their
  binding.
- `ExtensionContext` exposes every Pi field; `ui` is deferred to
  ADR-0033 but present as an attribute that raises `invalid_state`.
- `ExtensionRuntimeActions` is the 15-field surface Pi specifies;
  harness binds 11 of them (Sprint 3a 3 + Sprint 5a 8); 4 stay as
  throwing stubs.
- `Extension` dataclass carries the 7 Pi collections + 2 metadata fields
  + the Aelix-additive `cleanups` / `handler_error_modes`.
- `discover_and_load_extensions` ships with the corrected
  directory-primary / entry_points-additive ordering (P-21).

### Sprint 5b runtime ergonomics fixes (W4 MAJOR findings)

The following findings from W4 review are tracked here as Sprint 5b TODO items (non-blocking for Sprint 5a acceptance):

- `_action_get_session_name` synchronous-read returns None silently inside running loop (Sprint 5b: add sync cache mirroring Pi's `cachedSessionName`)
- Fire-and-forget tasks in `_action_set_*` need `_pending_tasks: set[asyncio.Task]` GC pinning + `add_done_callback`
- `asyncio.run` fallback in extension action helpers misbehaves in active loop — replace with `asyncio.new_event_loop().run_until_complete(...)` or require active loop

### Deferred-binding allowlist (Sprint 5a → Sprint 5b / Phase 4)

| Surface | Status | Owner |
| --- | --- | --- |
| `input` / `user_bash` / `resources_discover` emit sites | DEFERRED | ADR-0042 (Sprint 5b CLI loop) |
| `ExtensionAPI.send_message` real binding | DEFERRED stub | ADR-0042 |
| `ExtensionAPI.send_user_message` real binding | DEFERRED stub | ADR-0042 |
| `ExtensionAPI.append_entry` real binding | DEFERRED stub | ADR-0042 |
| `ExtensionAPI.get_commands` real registry | DEFERRED stub | ADR-0042 |
| `ExtensionContext.shutdown` graceful exit | DEFERRED default | ADR-0042 |
| `ExtensionContext.ui` real binding | DEFERRED raise | ADR-0033 (Phase 5 TUI) |
| `ModelRegistry` full impl | DEFERRED stub | ADR-0038 (Phase 4 provider) |
| Built-in coding tools (bash/read/edit/write/grep/find/ls) | OUT OF SCOPE | ADR-0042 (NEW) |
| Pi tool-typed `ToolCallEvent` variants | OUT OF SCOPE | ADR-0043 (NEW) |
| `ExtensionCommandContext` (6 methods) | OUT OF SCOPE | Phase 5 |
| Full `BashOperations` / `BashResult` types | OUT OF SCOPE | ADR-0042 |
| `SlashCommandInfo` registry | OUT OF SCOPE | ADR-0042 |
| `MessageRenderer` actual rendering | OUT OF SCOPE | Phase 5 |
| `KeyId` shortcut dispatch | OUT OF SCOPE | Phase 5 |

### Durable regression guard

`tests/pi_parity/test_phase_3_1_strict_superset.py` is the mechanical
truth-check for this ADR's invariant. It loads
`tests/pi_parity/fixtures/pi_extension_api_methods_734e08e.json` and
`pi_extension_context_fields_734e08e.json` (SHA-pinned per ADR-0034) and
fails when:

1. A Pi non-event `ExtensionAPI` member has no Aelix counterpart.
2. A Pi `ExtensionContext` field has no Aelix counterpart.
3. The 3 new Sprint 5a events are missing from `HookEventName` /
   `HOOK_RESULT_TYPES`.
4. The 3 new events are missing from
   `tests/pi_parity/test_phase_2_1_strict_superset.py`
   `DEFERRED_ALLOWLIST` OR have the wrong owner.

### Forward-compat clause

Future Pi `ExtensionAPI` / `ExtensionContext` members landing upstream
from SHA `734e08e` MUST follow the same contract:

1. Land the binding in the **same sprint** that updates the fixture, OR
2. Add the member to a deferred allowlist (this ADR's table above OR the
   E.5 Phase 2.1 / Phase 3.1 closure pin) with its owning ADR.

Adding the type without doing either is a strict-superset contract
violation — the closure pin will fail.

Conversely, when a deferred entry's real binding lands, the same PR MUST
drop the entry from the deferred allowlist.

### Time-bound deferral clause (EXPLICIT)

**If Sprint 5b (ADR-0042 CLI loop) does NOT ship within 4 weeks of the
Sprint 5a accepted commit (commit date 2026-05-17 → deadline
2026-06-14), this ADR auto-demotes to `Draft` status and the
`DEFERRED_ALLOWLIST` entries for `input` / `user_bash` /
`resources_discover` are subject to re-evaluation.** The intent is to
keep the deferred-binding queue short and prevent indefinite stub
proliferation. The Sprint 5a executor explicitly accepts this clause as
a binding constraint on Sprint 5a's "Accepted" status.

## Consequences

- Phase 3.1 ADRs all Accepted: 0017 (Sprint 5a amendment), 0028
  (Accepted from Draft), 0041 (this ADR).
- Sprint 5a is unblocked. The `aelix-coding-agent` extension surface is
  now Pi-shape-complete from an extension author's perspective —
  porting a Pi extension factory to Aelix is a re-import + snake_case
  rename exercise rather than a re-architect.
- Sprint 5b can land the 7 built-in coding tools + 3 emit sites + the
  4 throwing-stub→real bindings without further surface changes.
- The closure pin (`test_phase_3_1_strict_superset.py`) becomes the
  single mechanical truth for Phase 3.1 Pi-parity claims; future Phase
  3.1 amendments must keep the fixtures, the deferred allowlist, and
  the binding map mutually consistent.

## Relationships

- Cross-references: ADR-0017 (catalogue — Sprint 5a §"Phase 3.1 event
  additions" subsection), ADR-0019 (error policy v3 — extends to 3 new
  events), ADR-0028 (auto-discovery — Accepted from Draft), ADR-0033
  (`ui` deferred), ADR-0036 (loop vs harness event distinction — 5a 3
  new events are own-events), ADR-0038 (Phase 4 provider — owns
  `ModelRegistry` real binding), ADR-0040 (Phase 2.2 closure parent
  pattern), ADR-0042 (NEW — Sprint 5b CLI loop, owns 3 emit sites + 4
  throwing-stub real bindings), ADR-0043 (NEW — Sprint 5b tool-typed
  `ToolCallEvent` variants).
- Forward dependencies: ADR-0042 / ADR-0043 (Sprint 5b), ADR-0033
  (Phase 5 TUI).
