# Sprint 4a · Phase 2.2.1 — Session Manager + JsonlSessionRepo (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus, READ-ONLY)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다. 변형 혹은 추가는 그 다음 중요도입니다."**

---

## §0 — Sub-sprint scoping + P-11/P-12/P-13 surprises

### 0.1 Sub-sprint split (CONFIRMED)

| Sub-sprint | Scope | ADRs | LOC est | Risk |
|---|---|---|---|---|
| **4a (this spec)** | `Session` class + `SessionStorage` Protocol + `MemorySessionStorage` + `JsonlSessionStorage` + `JsonlSessionRepo` + `buildSessionContext` + `flush_pending_session_writes` rewrite + `handle_agent_event(message_end)` wiring + 8-variant `PendingSessionWrite` union (delete `PendingActiveToolsChangeWrite`, add 5 defensive flush arms) | ADR-0022 Draft → Accepted | ~700 prod + ~500 test | High — file I/O + 11 SessionTreeEntry types |
| **4b** | `compact()` + `navigate_tree()` + `Phase` Literal expansion + `compaction/branch-summarization` ports + 4 session_* emit sites + LLM-summarizer adapter | ADR-0023 Draft → Accepted | ~500 prod + ~400 test | Medium — state machine + LLM dependency |

### 0.2 P-11/P-12/P-13 surprises (CRITICAL findings)

**P-11 (LOAD-BEARING):** Sprint 3b W4 MAJOR-1 introduced `PendingActiveToolsChangeWrite` claiming "Pi pushes an active-set change onto pendingSessionWrites." **This is factually wrong at SHA 734e08e.** Pi `setActiveTools` (`agent-harness.ts:875-882`) only validates and assigns — no pending push. Pi `flushPendingSessionWrites` (`agent-harness.ts:459-481`) has NO `active_tools_change` case. **Resolution:** delete the variant + delete the push site in `set_active_tools` for strict Pi parity. The active-set is a per-process runtime concept in Pi — restoration is via `options.activeToolNames` on harness construction, not session replay.

**P-12:** Pi `PendingSessionWrite` is a TS conditional type (`types.ts:492-496`) — NOT 9 enumerated variants as Draft ADR-0022 implied. Pi `flushPendingSessionWrites` handles **8 concrete switch arms** but **only 3 push sites exist** in Pi (`message`, `model_change`, `thinking_level_change`). Sprint 4a ships all 8 dispatcher arms for defensive completeness; only 3 push sites exist (matching Pi exactly). The 5 unreachable arms are tested via synthetic injection.

**P-13:** Pi `Session` is a **concrete class** (`session/session.ts:78-252`) with **17 public methods + 1 private**, NOT a Protocol with 7 methods as Draft ADR-0022 described. The Protocol is `SessionStorage` (`types.ts:438-452`) with 10 methods. `appendCompaction` takes **5 params** (`summary`, `first_kept_entry_id`, `tokens_before`, `details?`, `from_hook?`) — not 1. ADR-0022 major revision required.

---

## §A — Session class + SessionStorage Protocol (Pi parity)

### A.1 `SessionStorage` Protocol (`packages/aelix-agent-core/src/aelix_agent_core/session/storage.py`)

Pi `types.ts:438-452`. 10 methods. Generic over `TMetadata`.

```python
class SessionStorage(Protocol[TMetadata]):
    async def get_metadata(self) -> TMetadata: ...
    async def get_leaf_id(self) -> str | None: ...
    async def set_leaf_id(self, leaf_id: str | None) -> None: ...
    async def create_entry_id(self) -> str: ...
    async def append_entry(self, entry: SessionTreeEntry) -> None: ...
    async def get_entry(self, id: str) -> SessionTreeEntry | None: ...
    async def find_entries(self, type: str) -> list[SessionTreeEntry]: ...
    async def get_label(self, id: str) -> str | None: ...
    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]: ...
    async def get_entries(self) -> list[SessionTreeEntry]: ...
```

Aelix `SessionError(code, ...)` mirrors Pi 6-code taxonomy: `"not_found" | "invalid_session" | "invalid_entry" | "invalid_fork_target" | "storage" | "unknown"`.

### A.2 `SessionTreeEntry` dataclasses (11 types)

All `@dataclass(frozen=True)`. Common base: `type, id, parent_id, timestamp`. JSON on-disk uses camelCase for cross-runtime Pi compatibility.

| Pi name | Aelix dataclass | Discriminator | Extra fields |
|---|---|---|---|
| `MessageEntry` | `MessageEntry` | `"message"` | `message: AgentMessage` |
| `ThinkingLevelChangeEntry` | `ThinkingLevelChangeEntry` | `"thinking_level_change"` | `thinking_level: str` |
| `ModelChangeEntry` | `ModelChangeEntry` | `"model_change"` | `provider: str, model_id: str` |
| `CompactionEntry` | `CompactionEntry` | `"compaction"` | `summary, first_kept_entry_id, tokens_before, details?, from_hook?` |
| `BranchSummaryEntry` | `BranchSummaryEntry` | `"branch_summary"` | `from_id, summary, details?, from_hook?` |
| `CustomEntry` | `CustomEntry` | `"custom"` | `custom_type, data?` |
| `CustomMessageEntry` | `CustomMessageEntry` | `"custom_message"` | `custom_type, content, display, details?` |
| `LabelEntry` | `LabelEntry` | `"label"` | `target_id, label?` |
| `SessionInfoEntry` | `SessionInfoEntry` | `"session_info"` | `name?` |
| `LeafEntry` | `LeafEntry` | `"leaf"` | `target_id?` |

### A.3 `Session` concrete class (17 public + 1 private method)

Pi `session/session.ts:78-252`. Wraps `SessionStorage`. Owns timestamps, ID generation, leaf parenting.

Critical signatures:
```python
class Session:
    def __init__(self, storage: SessionStorage[TMetadata]) -> None: ...
    async def get_metadata(self) -> TMetadata: ...
    def get_storage(self) -> SessionStorage[TMetadata]: ...  # sync per Pi
    async def get_leaf_id(self) -> str | None: ...
    async def get_entry(self, id: str) -> SessionTreeEntry | None: ...
    async def get_entries(self) -> list[SessionTreeEntry]: ...
    async def get_branch(self, from_id: str | None = None) -> list[SessionTreeEntry]: ...
    async def build_context(self) -> SessionContext: ...
    async def get_label(self, id: str) -> str | None: ...
    async def get_session_name(self) -> str | None: ...
    async def _append_typed_entry(self, entry: SessionTreeEntry) -> str: ...  # private
    async def append_message(self, message: AgentMessage) -> str: ...
    async def append_thinking_level_change(self, thinking_level: str) -> str: ...
    async def append_model_change(self, provider: str, model_id: str) -> str: ...
    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any | None = None,
        from_hook: bool | None = None,
    ) -> str: ...  # 5 params per P-13
    async def append_custom_entry(self, custom_type: str, data: Any | None = None) -> str: ...
    async def append_custom_message_entry(
        self, custom_type: str, content, display: bool, details: Any | None = None,
    ) -> str: ...
    async def append_label(self, target_id: str, label: str | None) -> str: ...
    async def append_session_name(self, name: str) -> str: ...
    async def move_to(
        self, entry_id: str | None, summary: dict[str, Any] | None = None,
    ) -> str | None: ...
```

**Error contract:** `append_label(unknown_target)` raises `SessionError("not_found")`. `move_to(unknown_id)` raises `SessionError("not_found")` when `entry_id is not None`.

### A.4 `buildSessionContext` (`session/context.py`)

Pi `session/session.ts:21-76`. Pure function: `pathEntries → SessionContext(messages, thinking_level, model)`. Honors compaction `first_kept_entry_id` boundary. Helper `createCompactionSummaryMessage` / `createBranchSummaryMessage` / `createCustomMessage` ported from Pi `harness/messages.ts`.

```python
@dataclass(frozen=True)
class SessionContext:
    messages: list[AgentMessage]
    thinking_level: str
    model: dict | None
```

---

## §B — JsonlSessionRepo + JsonlSessionStorage

### B.1 File layout

- Default sessions root: `~/.aelix/sessions/` (configurable via `AgentHarnessOptions.session_dir`)
- Cwd encoding: Pi `encodeCwd` ported verbatim — strip leading `/`/`\\`, replace `/\\:` with `-`, wrap in `--...--`
- File path: `{cwd_dir}/{timestamp_with_colons_to_dashes}_{session_id}.jsonl`
- Session ID: `uuid.uuid4()` (Pi uses `uuidv7` — deferred parity gap, TODO Phase 4)
- Entry ID: 100 tries of `uuid4().hex[:8]`; fall back to full `uuid4().hex`

### B.2 JSONL format (Pi version 3)

Line 1 — header:
```json
{"type":"session","version":3,"id":"<sessionId>","timestamp":"<ISO8601>","cwd":"<abs>","parentSession":"<path?>"}
```
Lines 2..N — entries (one `SessionTreeEntry` per line, camelCase keys).

`version: 3` preserved verbatim for Pi↔Aelix cross-runtime read/write.

### B.3 Write atomicity

- Single `await loop.run_in_executor(None, _append_line, ...)` per entry
- POSIX `O_APPEND` atomicity for ≤ PIPE_BUF (4096B) writes
- Windows divergence acceptable (Pi has same caveat)
- **Aelix-additive safety net:** per-instance `asyncio.Lock` around appends (Pi has no lock)

### B.4 Read

`JsonlSessionStorage.open`: reads entire file via `asyncio.to_thread`, parses header + entries, computes `leaf_id = leaf_id_after_entry(last_entry)`. Port Pi `leafIdAfterEntry`: for `leaf` entries return `target_id`; else return `entry.id`.

### B.5 `MemorySessionStorage`

In-memory `SessionStorage` for tests and as fallback. Same Protocol.

### B.6 `JsonlSessionRepo` API

Pi `SessionRepo<JsonlSessionMetadata>` 5 methods. Sprint 4a ships **create / open / list / delete**. `fork` deferred to 4b (depends on `getEntriesToFork` from compaction utilities).

`FileSystem` Protocol with 11 methods (Pi `jsonl-repo.ts:19-32`). `LocalFileSystem` default impl wraps `pathlib`/`os`. **Aelix-additive note:** Pi uses `Result<T, FileError>` ADT; Aelix raises `OSError` directly + wraps at JSONL boundary into `SessionError("storage", ...)`. Simpler, idiomatic Python.

---

## §C — Integration with Sprint 3b pending_session_writes

### C.1 `PendingSessionWrite` union (8 variants — DELETE active_tools, ADD 5 defensive)

| Aelix variant | Push site exists? | Pi cite |
|---|---|---|
| `PendingMessageWrite` (existing) | YES — `append_message` during turn | `agent-harness.ts:674` |
| `PendingModelChangeWrite` (existing) | YES — `setModel` during turn | `:851` |
| `PendingThinkingLevelChangeWrite` (existing) | YES — `setThinkingLevel` during turn | `:866` |
| **DELETE** `PendingActiveToolsChangeWrite` | — | NO Pi push (P-11) |
| `PendingCustomWrite` NEW | NO push site | Defensive flush arm only |
| `PendingCustomMessageWrite` NEW | NO push site | Defensive flush arm only |
| `PendingLabelWrite` NEW | NO push site | Defensive flush arm only |
| `PendingSessionInfoWrite` NEW | NO push site | Defensive flush arm only |
| `PendingLeafWrite` NEW | NO push site | Defensive flush arm only |

### C.2 `flush_pending_session_writes` rewrite

Today: only `PendingMessageWrite` flushed; others logged + dropped.

After Sprint 4a: match-based dispatch with 8 arms + assert_never. When session attached, route to corresponding `session.append_*`. When `session is None`, fallback to Sprint 3b behavior (in-memory message append; debug log for others).

Key Pi parity note: Pi `state.messages` is REBUILT every turn via `session.buildContext()` (`agent-harness.ts:314`). Aelix Sprint 4a retains `state.messages` as primary source-of-truth (transitional divergence). Sprint 4b will flip to derived view. Documented in ADR-0022 §"Sprint 4a → 4b transition plan".

### C.3 `handle_agent_event(message_end)` wiring (BIGGEST behavior shift)

Pi `handleAgentEvent` (`agent-harness.ts:483-510`) appends EVERY `message_end` message via `await this.session.appendMessage(event.message)` BEFORE re-emitting. This is Pi's PRIMARY write path.

Aelix Sprint 4a: in `_run`'s `emit` callback (`harness/core.py:999-1037`), when `event.type == "message_end"`, call `session.append_message(event.message)` BEFORE `_hooks.emit(...)`. When `session is None`, skip Session call.

---

## §D — Session lifecycle

### D.1 Construction
`AgentHarnessOptions.session: Session | None = None` (type narrow from current `Any | None`). `AgentHarness.__init__`: `self._session = options.session`. Caller constructs externally via `JsonlSessionRepo.create(...)` or `Session(MemorySessionStorage(...))`.

### D.2 No-session fallback (Aelix-additive)
Pi assumes `session` always present. Aelix permits `None` for Phase 1/2 backward compat. When `session is None`: skip `session.build_context()` (use `state.messages`); skip `session.append_message` in message_end wiring; flush falls back to drop-with-log.

### D.3 `_TurnState` extension deferred
ADR-0025 mentions `messages` / `session_id` extension. **Sprint 4a defers** — compaction-driven reconstruction (what makes `_TurnState.messages` meaningful) lands in 4b.

### D.4 `state.session_id`
`AgentState` gains `session_id: str | None = None`. Populated at `__init__` from `await session.get_metadata().id` if session present. Used by `before_provider_request.session_id` (Phase 4).

---

## §E — Tests (~+28, current 273 → target 301)

E.1 `test_session_storage_memory.py` (8) — Protocol contract round-trips
E.2 `test_session_class.py` (10) — append_* + move_to + build_context happy/error
E.3 `test_jsonl_session_storage.py` (5) — header v3 + round-trip + malformed input
E.4 `test_jsonl_session_repo.py` (3) — create/open/list/delete
E.5 `test_session_pending_writes_integration.py` (5) — 8-arm flush + no-session fallback + **P-11 regression: NO active_tools_change variant**
E.6 `test_session_message_end_wiring.py` (3) — Pi append-then-emit ordering
E.7 `test_session_no_session_fallback.py` (2) — degrades gracefully
E.8 `tests/pi_parity/test_session_parity.py` + vendored Pi `.jsonl` fixture (2) — cross-runtime parse
E.9 `tests/pi_parity/test_phase_2_1_strict_superset.py` — add P-11 lockdown test (no `PendingActiveToolsChangeWrite` reference)

Sprint 3b `test_harness_pending_writes.py` updated for 8-arm union.

---

## §F — ADR amendments

### F.1 ADR-0022 major revision (Draft → Accepted)
1. Header: "Session class + SessionStorage Protocol" (not just "interface")
2. Signatures corrected per §A.3 (5-param `append_compaction`, etc)
3. NEW §"Aelix-additive divergences": session optional, asyncio.Lock, state.messages mirror, OSError vs Pi Result
4. NEW §"Removed claims": P-11 reversal — `PendingActiveToolsChangeWrite` removed; Sprint 3b W4 MAJOR-1 verdict superseded
5. NEW §"Sprint 4a → 4b transition plan": session_* emit sites + state.messages derivation + ADR-0025 _TurnState extension
6. Status: `Accepted (Sprint 4a / Phase 2.2.1 shipped)`

### F.2 ADR-0017 amendment
§"Session message_end wiring landed Sprint 4a — `MessageEndHookEvent` emitted AFTER `session.append_message` (Pi `agent-harness.ts:484-486` ordering)"

### F.3 ADR-0039 closure pin update
- `DEFERRED_ALLOWLIST` unchanged (session_* emit sites in Sprint 4b)
- Add P-11 lockdown regression test
- Narrative bump: Phase 2.2.1 Sprint 4a shipped — 4 session_* emit sites remain deferred to 4b

### F.4 ADR-0025 amendment
Pending extensions section: mark `messages`/`session_id` extension as "Sprint 4b owner"

### F.5 README index
Bump ADR-0022 Draft → Accepted. No new ADR number.

---

## §G — Acceptance checklist

1. Session class (17+1 methods) + SessionStorage Protocol (10 methods)
2. JsonlSessionStorage + MemorySessionStorage + JsonlSessionRepo (create/open/list/delete)
3. Pi v3 fixture round-trip passes
4. PendingSessionWrite = 8-arm union (no `active_tools_change`)
5. `flush_pending_session_writes` dispatches all 8 with no-session fallback
6. `handle_agent_event(message_end)` calls session.append_message BEFORE re-emit
7. ~301 tests pass, ruff clean, pyright spike stable
8. ADR-0022 Accepted with major revisions
9. E.8 Pi-parity cross-runtime test passes
10. E.9 P-11 lockdown regression test passes
11. Sprint 3b `test_harness_pending_writes.py` updated
12. Phase 2.1 strict-superset closure pin still passes

---

## §H — Out of scope (Sprint 4b)

- compact() / navigate_tree() / Phase Literal expansion
- 4 session_* emit sites
- compaction/branch-summarization Python port
- getEntriesToFork + JsonlSessionRepo.fork
- `_TurnState.messages` from session.build_context()
- `state.messages` flip from primary → derived
- `before_provider_request.session_id` wiring (Phase 4)
- `uuidv7` port (deferred)
- LLM-summarizer adapter

---

## §I — Implementation order

1. §A.2 entry dataclasses + JSON helpers + SessionError taxonomy
2. §A.1 SessionStorage Protocol + MemorySessionStorage
3. §A.3 Session class
4. §B.2-B.5 JsonlSessionStorage + FileSystem + LocalFileSystem
5. §B.1, B.6 JsonlSessionRepo
6. §A.4 buildSessionContext + message helpers
7. §C.1 PendingSessionWrite 8-arm + DELETE PendingActiveToolsChangeWrite + DELETE push site
8. §C.2 flush_pending_session_writes 8-arm dispatcher
9. §C.3 handle_agent_event(message_end) wiring
10. §D.4 AgentState.session_id
11. §D.1-D.2 AgentHarness session typing + None fallbacks
12. §E.8 Pi-parity vendored fixture
13. §F ADR amendments

---

## §J — Risk register

| Risk | Mitigation |
|---|---|
| P-11 deletion breaks Sprint 3b regression tests | E.5 includes explicit no-variant test |
| JSONL drifts from Pi v3 | E.8 vendored fixture CI lockdown |
| `state.messages` dual source-of-truth out of sync | Sprint 4a transitional divergence documented; 4b fixes |
| Blocking I/O slows hook chain | `asyncio.to_thread` wrapper |
| Pi `uuidv7` vs Aelix `uuid4` cross-runtime | Entry IDs are local; session IDs use uuid4 — documented non-blocking |

End of binding spec.
