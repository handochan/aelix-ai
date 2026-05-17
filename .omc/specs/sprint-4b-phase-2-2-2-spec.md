# Sprint 4b · Phase 2.2.2 — compact + navigate_tree + Phase machine + 4 session emits (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — P-14 ~ P-20 INVESTIGATION (Pi compact/navigate/Phase machine — VERIFIED at SHA 734e08e)

### P-14 — No summarizer callback in Pi `AgentHarnessOptions`

Pi `types.ts` has NO `compactSummarizer` / `branchSummarizer` / `Summarizer` field. Pi `compact()` at `agent-harness.ts:689-745` calls `compact()` from `compaction/compaction.ts:545` inline using `this.model` + `await this.getApiKeyAndHeaders(model)`. Pi `navigateTree()` at `:747-867` calls `generateBranchSummary()` from `compaction/branch-summarization.ts:129` the same way.

**Decision:** Aelix Sprint 4b ships NO summarizer callbacks. Expose two Pi-parity helper module entry points (`compaction.py:compact()` + `branch_summarization.py:generate_branch_summary()`) called inline through `options.get_api_key_and_headers` (F-6 placeholder). Phase 4 (ADR-0038) wires real provider. Sprint 4b raises `AgentHarnessError("invalid_state")` when no `get_api_key_and_headers` is provided; dev/test paths use a `_summarizer_override` test-only seam (Aelix-additive, documented).

### P-15 — Phase Literal is 5 values (Pi), 4 (Aelix)

Pi `types.ts:262`: `AgentHarnessPhase = "idle" | "turn" | "compaction" | "branch_summary" | "retry"`. ADR-0023 Draft already chose Aelix-additive omission of `"retry"` (declared-but-unused in Pi at SHA). **Sprint 4b ships 4 values** (`"idle" | "turn" | "compaction" | "branch_summary"`); ADR-0023 unchanged on this point.

### P-16 — `compact()` and `navigate_tree()` signatures

- Pi `compact(customInstructions?)` returns `{summary, firstKeptEntryId, tokensBefore, details?}` (`agent-harness.ts:689-693`).
- Pi `navigateTree(targetId, options?)` where options = `{summarize?, customInstructions?, replaceInstructions?, label?}` returns `NavigateTreeResult = {cancelled, editorText?, summaryEntry?}` (`agent-harness.ts:747-750`, `types.ts:269-273`).
- ADR-0023 Draft signatures partially match; missing options dataclass + return tuples. **Sprint 4b ships Pi-exact signatures.**

### P-17 — `SessionBeforeCompactHookEvent` payload (empty stub)

Pi `agent-harness.ts:706-711` payload: `{preparation, branchEntries, customInstructions, signal}`. Sprint 3a registered `SessionBeforeCompactHookEvent` with empty stub at `hooks.py:464-467`. **Sprint 4b extends payload** to Pi shape.

### P-18 — `SessionBeforeTreeHookEvent` missing `signal`

Pi `agent-harness.ts:765` payload: `{preparation, signal}`. Aelix `SessionBeforeTreeHookEvent(preparation)` lacks `signal` (`hooks.py:566-574`). **Sprint 4b adds `signal` field.**

### P-19 — `SessionTreeHookEvent.new_leaf_id` type widening

Pi `types.ts:303-309`: `newLeafId: string | null`. Aelix has `new_leaf_id: str = ""` (`hooks.py:577-589`). **Sprint 4b narrows to `str | None`** matching Pi.

### P-20 — `SessionBeforeCompactResult` payload

Pi `types.ts:339-342`: `{cancel?, compaction?}` where `compaction: CompactResult` allows hook to substitute the LLM call entirely. Aelix has 2-field minimal stub `{cancel, reason}` (`hooks.py:455-460`). **Sprint 4b extends** to Pi shape; the `cancel` flag stays Aelix-additive convenience (Pi uses `cancel?: boolean` same way).

---

## §A — Phase machine expansion

Today `AgentHarnessPhase = Literal["idle", "turn"]` at `harness/core.py:102`. Sprint 4b expands to:

```python
AgentHarnessPhase = Literal["idle", "turn", "compaction", "branch_summary"]
```

(Excludes Pi's `"retry"` per P-15 / ADR-0023 Aelix-additive omission.)

**Phase guards** in each method:
- `prompt()` / `steer()` / `follow_up()`: allowed only when `phase == "idle"`. Raise `AgentHarnessError("busy")` otherwise.
- `compact()` / `navigate_tree()`: allowed only when `phase == "idle"`. Raise `AgentHarnessError("busy")` otherwise.
- `abort()`: always allowed (cooperative).
- `dispose()`: always allowed (transitions through abort first).

**Transitions:**
- `compact()`: `idle` → `compaction` → `idle` (via `finally`)
- `navigate_tree()`: `idle` → `branch_summary` → `idle` (via `finally`)

`is_idle` semantics unchanged (`phase == "idle"`).

---

## §B — `compact()` method on AgentHarness

```python
async def compact(self, custom_instructions: str | None = None) -> CompactResult:
    """Pi parity ``compact()`` (agent-harness.ts:689-745).

    Returns CompactResult({summary, first_kept_entry_id, tokens_before, details?}).
    """
```

Phase flow:
1. Guard: raise `AgentHarnessError("busy")` if not idle.
2. `self._phase = "compaction"`; `self._idle_event.clear()`
3. Build `CompactionPreparation` (Pi `compaction.ts:prepareCompaction`)
4. Emit `SessionBeforeCompactHookEvent(preparation, branch_entries, custom_instructions, signal)` — reducer can return `SessionBeforeCompactResult(cancel=True)` or substitute `compaction=CompactResult(...)` (P-20)
5. If reducer returned compaction: use it (skip LLM call)
6. Else: call `compact()` from `aelix_agent_core.session.compaction` module — needs `self._state.model` + `await options.get_api_key_and_headers(model)` (raise `AgentHarnessError("invalid_state")` if `get_api_key_and_headers is None`)
7. `await self._session.append_compaction(summary, first_kept_entry_id, tokens_before, details, from_hook=False)` (if reducer substituted, from_hook=True)
8. Emit `SessionCompactHookEvent(compaction_entry, from_hook)` observational
9. `finally`: `self._phase = "idle"`; `self._idle_event.set()`
10. Return `CompactResult`

---

## §C — `navigate_tree()` method on AgentHarness

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
    """Pi parity ``navigateTree()`` (agent-harness.ts:747-867)."""
```

Phase flow:
1. Guard: raise busy if not idle
2. `self._phase = "branch_summary"`; clear idle
3. If `target_id` is None → noop case
4. If `target_id` targets a `user_message` → editor branch: extract text (Pi `:760-780`)
5. Else if `options.summarize is True` → build `BranchSummaryPreparation`; emit `SessionBeforeTreeHookEvent(preparation, signal)` (P-18); reducer can cancel
6. If cancelled → return `NavigateTreeResult(cancelled=True)`
7. Call `generate_branch_summary()` from `aelix_agent_core.session.branch_summarization` module
8. `await self._session.move_to(target_id, summary={...})`
9. Emit `SessionTreeHookEvent(new_leaf_id, old_leaf_id, summary_entry, from_hook)` (P-19: `new_leaf_id: str | None`)
10. `finally`: phase → idle
11. Return `NavigateTreeResult(cancelled, editor_text, summary_entry)`

---

## §D — 4 emit-site payload extensions

Per P-17/P-18/P-19/P-20 — extend Sprint 3a stub events in `harness/hooks.py`:

**`SessionBeforeCompactHookEvent`** (extend `:464-467`):
```python
@dataclass(frozen=True)
class SessionBeforeCompactHookEvent(HookEvent):
    preparation: CompactionPreparation
    branch_entries: list[SessionTreeEntry]
    custom_instructions: str | None = None
    signal: Any | None = None
    type: Literal["session_before_compact"] = "session_before_compact"
```

**`SessionBeforeCompactResult`** (extend `:455-460`):
```python
@dataclass(frozen=True)
class SessionBeforeCompactResult:
    cancel: bool = False
    compaction: CompactResult | None = None
```

**`SessionBeforeTreeHookEvent`** (extend `:566-574`):
```python
@dataclass(frozen=True)
class SessionBeforeTreeHookEvent(HookEvent):
    preparation: BranchSummaryPreparation
    signal: Any | None = None
    type: Literal["session_before_tree"] = "session_before_tree"
```

**`SessionTreeHookEvent`** (extend `:577-589`):
```python
@dataclass(frozen=True)
class SessionTreeHookEvent(HookEvent):
    new_leaf_id: str | None = None  # was `str = ""`
    old_leaf_id: str | None = None
    summary_entry: SummaryEntry | None = None
    from_hook: bool = False
    type: Literal["session_tree"] = "session_tree"
```

**`SessionCompactHookEvent`** unchanged (already correct shape).

---

## §E — `JsonlSessionRepo.fork`

Port Pi `jsonl-repo.ts:103-127`. Signature:
```python
async def fork(
    self,
    source: JsonlSessionMetadata,
    options: ForkOptions,  # {entry_id?, position: "before"|"at"}
) -> Session:
```

Calls `get_entries_to_fork(source_entries, entry_id, position)` helper from `aelix_agent_core.session.repo_utils` (Pi `session/repo-utils.ts:27-45`).

Tests: `position="before"` user-msg, `position="at"` any-entry, invalid entry_id raises `SessionError("invalid_fork_target")`.

---

## §F — `state.messages` source-flip

Today: `AgentState.messages` is in-memory primary. Pi has NO top-level `state.messages` — Pi rebuilds per turn from `session.buildContext().messages` (`agent-harness.ts:419, 427`).

**Sprint 4b strategy:**
1. Extend `_TurnState` (per ADR-0025) with `messages: list[AgentMessage]` + `session_id: str | None`.
2. In `_run()` at `core.py:1088-1124`, when `self._session is not None`: replace `messages=list(self._state.messages)` (L1122) with `messages=list((await self._session.build_context()).messages)`.
3. When None: retain in-memory primary (Sprint 3b backward compat).
4. Keep `AgentState.messages` as **shadow mirror, not authoritative when Session attached**. Document in ADR-0025 amendment.

---

## §G — `compaction.py` + `branch_summarization.py` modules

New files under `aelix_agent_core/session/`:

**`compaction.py`** — Port Pi `compaction/compaction.ts`:
- `prepare_compaction(entries, custom_instructions?) -> CompactionPreparation`
- `compact(model, get_api_key_and_headers, preparation, custom_instructions?) -> CompactResult`
- `CompactionPreparation` dataclass
- `CompactResult` dataclass
- Token counting helpers (placeholder if Pi uses tiktoken-like)

**`branch_summarization.py`** — Port Pi `compaction/branch-summarization.ts`:
- `collect_entries_for_branch_summary(session, target_id) -> list[SessionTreeEntry]`
- `generate_branch_summary(model, get_api_key_and_headers, entries, custom_instructions?) -> str`
- `BranchSummaryPreparation` dataclass
- `SummaryEntry` dataclass

For Sprint 4b: ship the function signatures + minimum logic; LLM call uses `get_api_key_and_headers` per Pi. If summarizer can't run (no headers), raise `AgentHarnessError("invalid_state", "compact requires options.get_api_key_and_headers")`. Test-only seam: `_summarizer_override` callable injected via test fixture.

---

## §H — Tests (~+33 tests; 313 → ~346)

### H.1 `test_compact.py` (8 tests)
- happy path with mock summarizer
- cancel via `SessionBeforeCompactResult(cancel=True)`
- hook substitutes `compaction` (P-20)
- no model → error
- no get_api_key_and_headers → AgentHarnessError("invalid_state")
- Phase machine: prompt during compaction → busy
- error propagation (LLM raise)
- concurrent compact() → second raises busy

### H.2 `test_navigate_tree.py` (8 tests)
- target=None noop
- target=user_message → editor_text returned
- target=non-user without summarize → noop
- summarize=True with mock summarizer → SummaryEntry returned
- cancel via SessionBeforeTreeResult
- summary from hook (substitute)
- non-existent target → SessionError("not_found")
- Phase machine: prompt during branch_summary → busy

### H.3 `test_phase_machine.py` (5 tests)
- Each method (prompt/steer/follow_up/compact/navigate_tree) raises busy from each non-idle phase

### H.4 `test_session_emit_payloads.py` (4 tests)
- One per emit site verifying full Pi payload shape:
  - SessionBeforeCompactHookEvent: preparation, branch_entries, custom_instructions, signal
  - SessionCompactHookEvent: compaction_entry, from_hook
  - SessionBeforeTreeHookEvent: preparation, signal
  - SessionTreeHookEvent: new_leaf_id, old_leaf_id, summary_entry, from_hook

### H.5 `test_jsonl_repo_fork.py` (4 tests)
- no entry_id → full copy
- position="before" user-msg
- position="at" any-entry
- invalid target → SessionError("invalid_fork_target")

### H.6 `test_state_messages_derived.py` (2 tests)
- Session attached → state.messages derived from build_context
- session=None → state.messages in-memory primary

### H.7 `tests/pi_parity/test_phase_2_2_strict_superset.py` (2 tests)
- DEFERRED_ALLOWLIST has zero Phase 2.2 entries (4 session_* now emit)
- All 4 emit sites present in code

---

## §I — ADR amendments + new ADR-0040

### I.1 ADR-0023 (Draft → Accepted)
- Status flip
- §"Decision" updates per P-14 (no summarizer callbacks; use get_api_key_and_headers), P-16 (signatures), P-15 (4-phase Aelix-additive)
- §"Aelix-additive divergences" section listing: "retry" phase omitted, summarizer_override test seam, in-memory mirror backward-compat for state.messages when None

### I.2 ADR-0017 amendment
Add §"Session emit sites landed Sprint 4b (Phase 2.2.2)" — payload extensions per P-17/P-18/P-19/P-20 + 4 emit sites active.

### I.3 ADR-0022 amendment
§"Sprint 4a → 4b transition plan" → "Sprint 4b completed" marker. state.messages source-flip landed.

### I.4 ADR-0025 amendment
_TurnState extension landed: `messages` + `session_id` fields populated.

### I.5 ADR-0039 amendment
DEFERRED_ALLOWLIST update: remove 4 session_* entries; closure pin still passes.

### I.6 **NEW ADR-0040 "Phase 2.2 Strict Superset Closure"**
Mirror ADR-0039:
- 1st-principle invariant
- Closure date + Pi SHA
- Roster: P-11 ~ P-20 (Sprint 4a inherited + Sprint 4b new findings)
- E.5-style closure pin pointer (test_phase_2_2_strict_superset.py)
- Deferred allowlist: 3 Phase 4 names only (before_provider_*, after_provider_response)
- Forward-compat clause mirroring ADR-0039

### I.7 README index updates
- ADR-0023 row Status → Accepted
- ADR-0040 row added
- Sprint 4b ADRs sub-table

---

## §J — Acceptance checklist

1. 313 → ~346 tests pass
2. ruff clean
3. pyright spike: 8 errors (no regression)
4. demo unchanged
5. 4 session_* events emit at correct sites with full Pi payloads
6. Phase machine 4-value Literal works (busy guards correct)
7. compact() + navigate_tree() public methods on AgentHarness
8. JsonlSessionRepo.fork added (4a deferred item closed)
9. state.messages source-flip: session attached → derived; None → in-memory
10. Phase 2.2 closure pin: zero deferred Phase 2.2 entries
11. ADR-0023 Accepted; ADR-0040 created
12. Phase 2.2 ADRs all Accepted: 0017, 0022, 0023, 0025, 0040 (+0039 references)

---

## §K — Out of scope

- Phase 3 (ADR-0028 extension auto-discovery)
- Phase 4 (real providers, OAuth, ADR-0020 RPC, ADR-0038 adapters)
- Task #37 pyright 142 cleanup
- LLM-summarizer real impl (callback interface ships; default Phase 4)
- `"retry"` phase support (Pi declared but unused; Aelix-additive omission per ADR-0023)

---

## §L — Implementation order

1. §D extend 4 hook events + result types (P-17/P-18/P-19/P-20)
2. §G new compaction.py + branch_summarization.py modules
3. §E JsonlSessionRepo.fork + repo_utils.py helpers
4. §A Phase Literal expansion + guards
5. §B compact() method
6. §C navigate_tree() method
7. §F state.messages source-flip + _TurnState extension
8. §H tests
9. §I ADR amendments + ADR-0040

End of binding spec.
