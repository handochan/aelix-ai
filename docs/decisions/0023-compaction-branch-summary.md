# 0023. Compaction + Branch Summary

Status: **Accepted (Sprint 4b / Phase 2.2.2 shipped)**
Supersedes (partial): ADR-0016 deferred (Phase machine expansion)
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Pi `AgentHarness`는 `compact()`, `navigateTree()` 메서드를 보유하고
`session_before_compact` / `session_compact` / `session_before_tree` /
`session_tree` hook을 emit합니다. Phase machine은 `idle | turn | compaction |
branch_summary` 상태를 가집니다(`retry`는 Pi에서 declared but unused).

ADR-0016은 "compaction/branch_summary 도입 시점 미정"으로 Phase machine 확장을
deferred했습니다. 1차 원칙(Pi parity)에 따라 Phase 2.2에 명시합니다.

현재 Aelix Phase machine은 `idle | turn`만 구현합니다. `session_before_compact`
event class는 Phase 1.2에 정의되어 있으나 emit site가 없습니다.

## Decision

Phase 2.2.2 (Sprint 4b)에서 다음을 구현합니다.

### `AgentHarness.compact(custom_instructions?)`

Pi signature parity (`agent-harness.ts:689-693`):

```python
async def compact(self, custom_instructions: str | None = None) -> CompactResult:
    ...
```

Phase flow:

1. Guard busy (raise `AgentHarnessError("busy")` if not idle).
2. `self._phase = "compaction"`; clear `_idle_event`.
3. Build `CompactionPreparation` from current branch entries.
4. Emit `SessionBeforeCompactHookEvent(preparation, branch_entries,
   custom_instructions, signal)` — payload extended per P-17.
5. Hook may cancel via `SessionBeforeCompactResult(cancel=True)` OR
   substitute a `CompactResult` via `compaction=...` (P-20).
6. Otherwise call `compaction.compact()` with `self._state.model` +
   `options.get_api_key_and_headers` (P-14 — no Pi-divergent summarizer
   callback on `AgentHarnessOptions`).
7. Persist via `Session.append_compaction(summary, first_kept_entry_id,
   tokens_before, details, from_hook=...)`.
8. Emit `SessionCompactHookEvent(compaction_entry, from_hook)`.
9. `finally`: restore `phase = "idle"`.

### `AgentHarness.navigate_tree(target_id, options?)`

Pi signature parity (`agent-harness.ts:747-750`, `types.ts:269-273`):

```python
@dataclass(frozen=True)
class NavigateTreeOptions:
    summarize: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass(frozen=True)
class NavigateTreeResult:
    cancelled: bool
    editor_text: str | None = None
    summary_entry: SummaryEntry | None = None


async def navigate_tree(
    self, target_id: str | None, options: NavigateTreeOptions | None = None,
) -> NavigateTreeResult:
    ...
```

Phase flow (Pi parity `agent-harness.ts:747-867`):

1. Guard busy → raise.
2. `phase = "branch_summary"`.
3. `target_id is None` → return `NavigateTreeResult(cancelled=False)`.
4. `old_leaf_id == target_id` → short-circuit return (Pi
   `agent-harness.ts:756`).
5. Resolve target; raise `invalid_argument` if missing.
6. `collect_entries_for_branch_summary(...)` builds entries +
   `common_ancestor_id`.
7. Emit `SessionBeforeTreeHookEvent(preparation, signal)` — P-18 payload
   extension.
8. If hook `cancel=True` → return `cancelled=True`.
9. If hook provided `summary` dict → use it (`from_hook=True`).
10. Else if `options.summarize` AND `len(entries) > 0` → call
    `generate_branch_summary` (P-14: uses `get_api_key_and_headers`).
11. Editor-branch handling for `user_message` / `custom_message` targets:
    extract text, set `new_leaf_id = target.parent_id`.
12. `Session.move_to(new_leaf_id, summary=...)`.
13. Emit `SessionTreeHookEvent(new_leaf_id, old_leaf_id, summary_entry,
    from_hook)` — `new_leaf_id` is `str | None` per P-19.
14. `finally`: `phase = "idle"`.

### Phase machine 확장

```python
AgentHarnessPhase = Literal["idle", "turn", "compaction", "branch_summary"]
# Pi의 "retry"는 declared but unused → Aelix는 처음부터 포함하지 않음.
```

## Aelix-additive divergences

Sprint 4b ships these intentional divergences from Pi at SHA `734e08e`:

1. **`"retry"` Phase Literal value omitted (P-15).** Pi `types.ts:262`
   declares 5 values including `"retry"`; the value is declared-but-unused
   at the pinned SHA. Aelix omits it; future re-introduction is a single-
   line widening of `AgentHarnessPhase`.

2. **No summarizer callbacks on `AgentHarnessOptions` (P-14).** Pi has no
   `compactSummarizer` / `branchSummarizer` field; Aelix mirrors that
   exactly. Production code calls into the provider via
   `options.get_api_key_and_headers` (Phase 4 ADR-0038 wires the real
   adapter). Sprint 4b raises `AgentHarnessError("invalid_state")` when the
   summarizer needs auth but `get_api_key_and_headers is None`.

3. **Test-only `_summarizer_override` / `_branch_summarizer_override`
   seam.** `AgentHarnessOptions` carries two underscore-prefixed callables
   used exclusively by the Sprint 4b unit tests (`test_compact.py` /
   `test_navigate_tree.py`) to inject deterministic summarizers without
   standing up a provider. Production callers MUST leave them `None`.
   Documented as Aelix-additive per the top-level Pi-parity principle.

4. **In-memory `state.messages` mirror retained when `session=None`
   (Sprint 3b backward compat).** When a `Session` is attached, the
   per-turn `_TurnState.messages` is derived from
   `session.build_context().messages` (Pi parity). When `session is None`,
   the in-memory `state.messages` remains the primary source so existing
   Sprint 3b tests + the backward-compat fallback path keep working. See
   ADR-0022 §"Aelix-additive divergences" item 3.

5. **`SessionBeforeCompactResult.reason` field retained** (Pi has only
   `{cancel?, compaction?}`). Aelix-additive convenience for surfacing the
   cancellation message on the raised `AgentHarnessError("compaction")`.
   (W4 finding #14 / Fix 1 — cancel path now raises code `"compaction"` to
   match Pi `agent-harness.ts:707-708`, not `"invalid_state"`.)

## Consequences

- ADR-0016 deferred 종료 — 이 ADR로 supersede합니다.
- ADR-0040 Phase 2.2 closure pin ensures every session_* event has an
  emit site in `harness/core.py`.
- `pendingSessionWrites` queue: harness busy 중 session write 큐잉, idle 전이 시 flush.
  ADR-0022 Session Manager와 함께 구현합니다.
- 모노레포(ADR-0015)에서 `packages/aelix-agent-core/session/compaction.py`
  + `branch_summarization.py` 위치.
- ADR-0017 v2 catalogue의 `session_before_compact` / `session_compact` /
  `session_before_tree` / `session_tree` emit site가 Sprint 4b에서 land했습니다.
- Sprint 4b 신규 테스트 (`+34 tests`, 313 → 347):
  - `tests/test_compact.py` (9 tests — happy path, cancel, P-20 hook
    substitution, no-session, no-auth, busy guard, error propagation,
    concurrent compact, payload shape)
  - `tests/test_navigate_tree.py` (8 tests — noop, editor text,
    non-user/no-summary, summarize override, cancel, hook substitute,
    invalid target, busy guard)
  - `tests/test_phase_machine.py` (5 tests — busy guards for prompt /
    compact / navigate_tree from each non-idle phase; idle restoration)
  - `tests/test_session_emit_payloads.py` (4 tests — P-17/P-18/P-19/P-20
    payload shape verification)
  - `tests/test_jsonl_repo_fork.py` (4 tests — full copy / position=before
    / position=at / invalid_fork_target)
  - `tests/test_state_messages_derived.py` (2 tests — derived from
    build_context when Session attached, fall back to state.messages when
    None)
  - `tests/pi_parity/test_phase_2_2_strict_superset.py` (2 tests — zero
    Phase 2.2 entries in DEFERRED_ALLOWLIST, all 4 emit sites present)
