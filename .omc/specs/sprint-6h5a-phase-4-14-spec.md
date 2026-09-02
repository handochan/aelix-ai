# Sprint 6h₅a — Phase 4.14 BINDING SPEC

**Sprint:** 6h₅a (Phase 4.14 — Extension event Pi parity)
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
**Architect:** W1 (READ-ONLY — spec only; parent persists to `.omc/specs/sprint-6h5a-phase-4-14-spec.md`)
**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."

---

## §0 — W0 INVESTIGATION FINDINGS

P-numbering continues from Sprint 6h₄c W5 (last `P-331`) → **P-332 ~ P-343**.

### P-332 — 4 new event types + 2 new result types absent from Aelix surface

W0 verified `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:115-178` lists 21 own-events + 10 loop projections (31 total). The 4 Pi events at `extensions/types.ts:510-557` (`SessionStartEvent` / `SessionBeforeSwitchEvent` / `SessionBeforeForkEvent` / `SessionShutdownEvent`) and the 2 result types (`SessionBeforeSwitchResult` / `SessionBeforeForkResult` — both `{cancel?: boolean}`) are MISSING. The precedent for adding session-family events to `hooks.py` (rather than splitting into `extensions/events.py`) is `SessionBeforeCompactHookEvent` (`hooks.py:505-519`) + `SessionBeforeTreeHookEvent` (`hooks.py:617-628`) with matching `SessionBeforeCompactResult` + `SessionBeforeTreeResult` (`hooks.py:486-502`, `:329-342`). **Spec mandates: extend `hooks.py` to mirror precedent** — do NOT create a new module.

### P-333 — `ExtensionRunner.emit()` + `has_handlers()` missing

W0 verified `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py:60-127` exposes ONLY `get_registered_commands()`. There is no `emit()`, no `has_handlers()`, no Pi-style `runner.emit(event)` surface. Pi `runner.ts:177-189` (`emitSessionShutdownEvent`) calls both `extensionRunner.hasHandlers("session_shutdown")` + `extensionRunner.emit(event)`. **Critical W0 gap:** the actual event dispatch infrastructure already exists on `HookBus` at `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:2131-2171` (`has_handlers` + `emit`). The minimal Pi-parity adaptation is to **delegate** `ExtensionRunner.emit/has_handlers` to `harness.hooks.emit/has_handlers` via an injected callable bridge — preserving Pi's surface name while reusing Aelix's tested reducer/observer infrastructure. The `ExtensionRunner` dataclass at `_extension_runner.py:59-69` must gain 2 optional fields (callables) wired at harness construction (`harness/core.py:632-634`).

### P-334 — `emit_session_shutdown_event()` helper module placement

Pi `runner.ts:177-189` lives outside the `AgentSessionRuntime` class (top-level export). The Aelix equivalent SHOULD be co-located with the runtime to keep the carry-forward simple and avoid a new module surface for one ~10-LOC helper. **Spec mandates:** inline as a module-private `_emit_session_shutdown_event` function in `runtime/agent_session_runtime.py` (next to `_extract_user_message_text` at `:59-76`). No new module file.

### P-335 — `HookEventName` Literal widening + 4 `ExtensionAPI.on` overloads + 4 `HookBus.on` overloads

W0 verified pattern at `harness/hooks.py:143-178` (HookEventName Literal) + `harness/hooks.py:1879-1888` (HookBus `session_before_compact` overload) + `extensions/api.py:928-936` (ExtensionAPI `session_before_compact` overload) + handler alias at `hooks.py:1634-1637` (SessionBeforeCompactHandler). 4 new event names extend HookEventName closed union; 4 new HandlerEntry aliases; 4 new HookBus overloads; 4 new ExtensionAPI overloads. **TOTAL: 31 → 35** HookEventName entries; **28 → 32** ExtensionAPI overloads; **28 → 32** HookBus overloads.

### P-336 — `Session.session_file` sync property missing

W0 verified `Session` class at `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/session.py:84-302`. The class has `get_storage()` sync (`:98-101`) but `get_metadata()` is async (`:95-96`). Pi `AgentSession.sessionFile` (referenced by Pi `agent-session-runtime.ts:184`/`:210`/`:261` for `previousSessionFile` snapshot) is a SYNC getter. The Aelix equivalent must read the cached `_metadata.path` via the same `getattr(storage, "_metadata", None)` pattern already used in `runtime/agent_session_runtime.py:171-173` (cwd getter). `JsonlSessionMetadata.path` at `session/storage.py:64` is the source. **Spec mandates:** add `Session.session_file` sync `@property` returning `self._storage._metadata.path` (or `None` when metadata absent / not a JsonlSessionMetadata).

### P-337 — `session/session_cwd.py` module missing

W0 verified Pi `session-cwd.ts:1-59` exports `getMissingSessionCwdIssue` + `MissingSessionCwdError` + `assertSessionCwdExists` + `formatMissingSessionCwdError`. NO equivalent in Aelix. Aelix `FileSystem` at `session/fs.py:33-52` has `async def exists(self, path: str) -> bool` (Pi uses sync `existsSync` — Aelix divergence accepted because Aelix `FileSystem` is a Protocol with all-async surface). **Spec mandates:** port to `session/session_cwd.py`; helper functions become `async def` to satisfy `await fs.exists(...)`. Call sites: only the **switch_session post-metadata-load** site is in scope for 6h₅a; Pi `importFromJsonl :352` is N/A (Aelix stub) and Pi factory site `:391` is N/A (Aelix harness factory pattern — P-302).

### P-338 — Real `_emit_before_switch(reason, target_session_file)` body

W0 verified Aelix stub at `runtime/agent_session_runtime.py:209-214` (no-args, returns False). Pi `:115-130` takes `(reason: "new" | "resume", targetSessionFile?: string)`, gates on `runner.hasHandlers("session_before_switch")`, emits payload `{type, reason, targetSessionFile}`, returns `{cancelled: result?.cancel === true}`. **Spec mandates:** Aelix signature mirrors Pi exactly — return type becomes `RuntimeReplaceResult`-shaped or a private `_CancelOutcome` dataclass; for simplicity preserve current `bool` return (True = cancelled) and have the 3 public replace APIs translate to `RuntimeReplaceResult(cancelled=True)`. Existing callers at `switch_session :294`, `new_session :328` already check this contract — they pass NO args today but the call sites must be amended to pass `reason="resume"` / `reason="new"` + `target_session_file` (switch only).

### P-339 — Real `_emit_before_fork(entry_id, position)` body

W0 verified Aelix stub at `runtime/agent_session_runtime.py:216-220` (no-args, returns False). Pi `:132-147` takes `(entryId: string, options: {position: "before" | "at"})`, gates on `runner.hasHandlers("session_before_fork")`, emits `{type, entryId, ...options}`. Existing caller at `fork :379` passes no args; **spec mandates:** amend to pass `entry_id=entry_id, position=position` (kwargs).

### P-340 — `_teardown_current(reason, target_session_file)` ordering correction + shutdown emit

W0 verified Aelix `:222-239`: order is `before_session_invalidate?.() → harness.dispose()` (NO `session_shutdown` emit). **Pi `:149-157` order is REVERSED**: `emit("session_shutdown") → beforeSessionInvalidate?.() → session.dispose()`. The Aelix mistake: invalidate runs BEFORE the shutdown emit, but extensions subscribed to `session_shutdown` may legitimately need to read harness state (e.g. last messages, current session_file) which is still valid before invalidate. **Spec mandates:** correct ordering to Pi: emit FIRST, invalidate SECOND, dispose THIRD. **CRITICAL race avoidance:** capture `extension_runner_emit` + `extension_runner_has_handlers` references at the TOP of the method (or pass `harness` reference to helper) — they MUST be resolved BEFORE `harness.dispose()` is awaited because `dispose()` tears down the HookBus.

### P-341 — `dispose()` shutdown emit gap

W0 verified Aelix `:440-455` mirrors `_teardown_current` order (invalidate → dispose) with NO shutdown emit. Pi `:366-373` order: `beforeSessionInvalidate?.() → emit({type: "session_shutdown", reason: "quit"}) → session.dispose()`. **Aelix-divergence accepted:** Pi orders invalidate-first-then-emit on dispose, but emit-first-then-invalidate on teardown. The reason: `dispose()` is the terminal shutdown (no `targetSessionFile`, no follow-up replace), while `_teardown_current` is mid-replace (extensions might cache state for the NEW session). **Spec mandates:** mirror Pi exactly — `dispose()` uses INVALIDATE → EMIT → DISPOSE; `_teardown_current` uses EMIT → INVALIDATE → DISPOSE. This intentional asymmetry is recorded in §I steelman.

### P-342 — `previous_session_file` snapshot capture site

W0 verified Pi `:184` (switchSession), `:210` (newSession), `:261` (fork) — all capture `const previousSessionFile = this.session.sessionFile` BEFORE `await this.teardownCurrent(...)`. Aelix currently has NO snapshot. **Spec mandates:** capture `previous_session_file = self.session.session_file if self.session is not None else None` BEFORE `_teardown_current` in all 3 replace methods. This value is passed to `_finish_session_replacement(new_session, previous_session_file=..., reason=...)`.

### P-343 — `session_start` emit site + signature extension to `_finish_session_replacement`

W0 verified Pi `:170-172` (`finishSessionReplacement` emits `session_start` AFTER `apply` AFTER `rebindSession`). Aelix `_finish_session_replacement` at `:252-265` does NOT emit. **Spec mandates:** extend signature to `_finish_session_replacement(new_session, *, reason: Literal["resume","new","fork"], previous_session_file: str | None)`. After step 3 (`rebind_session` callback), emit `SessionStartHookEvent(type="session_start", reason=reason, previous_session_file=previous_session_file)` via the NEW harness's `harness.hooks.emit(...)` (NOT the OLD — the OLD is disposed by step 1). The first `session_start` (factory bootstrap, `reason="startup"|"reload"`) is OUT OF SCOPE for 6h₅a (carry-forward to 6h₅b).

---

## §A — Scope (binding) — LOC table

| File | Action | Estimated LOC |
|---|---|---|
| `harness/hooks.py` — 4 new event payloads + 2 new result types + 2 reducers + `HookEventName` widen + 4 HookBus overloads + 4 handler aliases + HOOK_RESULT_TYPES + _REDUCERS + __all__ | EXTEND | ~140 |
| `harness/_extension_runner.py` — `ExtensionRunner` gains optional `_emit`/`_has_handlers` callables + `async def emit(self, event)` + `def has_handlers(self, name) -> bool` | EXTEND | ~40 |
| `harness/core.py` — Wire `_extension_runner` construction at `:632-634` to inject `self._hooks.emit` + `self._hooks.has_handlers` as bridges | AMEND | ~6 |
| `runtime/agent_session_runtime.py` — `_emit_session_shutdown_event` helper + real `_emit_before_switch` + `_emit_before_fork` + `_teardown_current` rewrite + `dispose` rewrite + `_finish_session_replacement` extension + 3 replace methods snapshot/wire | AMEND | ~120 |
| `runtime/_types.py` — (no change — RuntimeReplaceResult already exists) | — | 0 |
| `session/session.py` — `Session.session_file` sync property | EXTEND | ~12 |
| `session/session_cwd.py` — NEW module: `SessionCwdIssue` dataclass + `get_missing_session_cwd_issue` + `MissingSessionCwdError` + `assert_session_cwd_exists` + `format_missing_session_cwd_error` | NEW | ~80 |
| `extensions/api.py` — 4 new `ExtensionAPI.on` overloads + handler aliases | AMEND | ~50 |
| **PROD TOTAL** | | **~448** |
| `tests/runtime/test_agent_session_runtime_extension_events.py` (NEW) — emit ordering + cancel + has_handlers gate + previous_session_file snapshot | NEW | ~180 |
| `tests/runtime/test_agent_session_runtime_session_cwd.py` (NEW) — assert_session_cwd_exists wiring in switch_session | NEW | ~60 |
| `tests/session/test_session_cwd_helper.py` (NEW) — unit tests for session_cwd module | NEW | ~50 |
| `tests/session/test_session_file_property.py` (NEW) — Session.session_file getter | NEW | ~30 |
| `tests/extensions/test_extension_runner_emit_delegate.py` (NEW) — bridge delegation tests | NEW | ~50 |
| `tests/pi_parity/test_phase_4_14_extension_events.py` (NEW) — closure pin: 4 events × overload narrowing × cancel semantics × Pi line citations | NEW | ~140 |
| `tests/extensions/test_overloads_extension_api.py` (AMEND) — bump 28 → 32 overloads | AMEND | ~20 |
| `scripts/pyright_spike.py` (AMEND) — bump expected overload count | AMEND | ~10 |
| **TEST TOTAL** | | **~540** |

### NOT in scope (deferred to Sprint 6h₅b / 6h₅c per ADR-0081 carry-forward)
1. **`session_start` reason="startup" | "reload"** first-emit at harness bootstrap (`HarnessFactory` signature change) — defer to 6h₅b.
2. **`session_start` reason="fork"** emit path with full Pi `:285-289` lineage propagation — included in fork path per P-343 but **W1 confirms** Pi `fork` finishes with `reason="fork"` (not just "resume"); spec includes this in fork wire.
3. **`importFromJsonl` body + `session_start` reason="resume"` from import** — runtime method stays stubbed (Sprint 6h₄c P-325 deferral).
4. **`assertSessionCwdExists` at factory site (Pi `:391`)** — Aelix factory pattern differs (P-302); defer to 6h₅c when full session_start lifecycle wires.
5. **`assertSessionCwdExists` at importFromJsonl (Pi `:352`)** — N/A while importFromJsonl is stubbed.
6. **`ResourcesDiscoverHookEvent.reason` widening to include "new"|"resume"|"fork"`** — currently `Literal["startup", "reload"]` (`hooks.py:830`); Pi `coding-agent/.../types.ts` widens this. Defer to 6h₅b (touches CLI loop).
7. **Pi `SessionShutdownEvent.reason: "quit"|"reload"|"new"|"resume"|"fork"`** — Aelix mirrors all 5 literals in 6h₅a but only emits "quit"/"new"/"resume"/"fork" (no "reload" emit site in scope).

---

## §B — `harness/hooks.py` + `harness/_extension_runner.py` AMEND

### B.1 New event payloads + result types (in `hooks.py`)

Add directly after `SessionBeforeTreeHookEvent` (`:618-628`), preserving Pi insertion order (`types.ts:510-557`):

```python
# === Sprint 6h₅a (Phase 4.14, ADR-0081) — extension session lifecycle events ===
# Pi parity (SHA 734e08e):
#   - SessionStartEvent          → extensions/types.ts:510-515
#   - SessionBeforeSwitchEvent   → extensions/types.ts:528-533
#   - SessionBeforeForkEvent     → extensions/types.ts:541-546
#   - SessionShutdownEvent       → extensions/types.ts:549-557
#   - SessionBeforeSwitchResult  → extensions/types.ts (cancel?: boolean)
#   - SessionBeforeForkResult    → extensions/types.ts (cancel?: boolean)

@dataclass(frozen=True)
class SessionBeforeSwitchResult:
    """Pi parity: ``SessionBeforeSwitchResult`` (``types.ts``). ``cancel`` short-circuits."""
    cancel: bool = False

@dataclass(frozen=True)
class SessionBeforeForkResult:
    """Pi parity: ``SessionBeforeForkResult`` (``types.ts``). ``cancel`` short-circuits."""
    cancel: bool = False

@dataclass(frozen=True)
class SessionStartHookEvent(HookEvent):
    """Pi ``SessionStartEvent`` (``extensions/types.ts:510-515``)."""
    reason: Literal["startup", "reload", "new", "resume", "fork"] = "startup"
    previous_session_file: str | None = None
    type: Literal["session_start"] = "session_start"

@dataclass(frozen=True)
class SessionBeforeSwitchHookEvent(HookEvent):
    """Pi ``SessionBeforeSwitchEvent`` (``extensions/types.ts:528-533``)."""
    reason: Literal["new", "resume"] = "resume"
    target_session_file: str | None = None
    type: Literal["session_before_switch"] = "session_before_switch"

@dataclass(frozen=True)
class SessionBeforeForkHookEvent(HookEvent):
    """Pi ``SessionBeforeForkEvent`` (``extensions/types.ts:541-546``)."""
    entry_id: str = ""
    position: Literal["before", "at"] = "before"
    type: Literal["session_before_fork"] = "session_before_fork"

@dataclass(frozen=True)
class SessionShutdownHookEvent(HookEvent):
    """Pi ``SessionShutdownEvent`` (``extensions/types.ts:549-557``)."""
    reason: Literal["quit", "reload", "new", "resume", "fork"] = "quit"
    target_session_file: str | None = None
    type: Literal["session_shutdown"] = "session_shutdown"
```

### B.2 `HookEventName` Literal widening (`hooks.py:115-178`)

Append 4 new entries to `AgentHarnessEventName` AND mirror into `HookEventName` (close-after the `resources_discover` entry):

```python
# Sprint 6h₅a additions (Phase 4.14) — extension session lifecycle (Pi 734e08e)
"session_start",
"session_before_switch",
"session_before_fork",
"session_shutdown",
```

Closed-union count: 31 → **35**.

### B.3 Reducer for `session_before_switch` / `session_before_fork`

Reuse the existing `_reducer_session_before` (`hooks.py:1245-1263`) by widening its return-type union to include the 2 new result types AND by widening its `isinstance(...)` check to include them. Pi cancel-aggregation semantics (`runner.ts:680-712`): first cancel wins (short-circuit on `raw.cancel`), sequential await, exceptions caught at `_safe_invoke`'s `error_mode="throw"` (re-raise) or `"continue"` (swallow). **First-cancel-wins is satisfied** by the existing `if raw.cancel: return raw` short-circuit at `:1260-1261`.

```python
async def _reducer_session_before(
    handlers: list[HandlerEntry],
    event: HookEvent,
    ctx: ExtensionContext,
) -> SessionBeforeCompactResult | SessionBeforeTreeResult | SessionBeforeSwitchResult | SessionBeforeForkResult | None:
    last = None
    for handler, mode in handlers:
        raw = await _safe_invoke(handler, event, ctx, mode)
        if isinstance(raw, (SessionBeforeCompactResult, SessionBeforeTreeResult, SessionBeforeSwitchResult, SessionBeforeForkResult)):
            if raw.cancel:
                return raw
            last = raw
    return last
```

### B.4 `HOOK_RESULT_TYPES` + `_REDUCERS` registrations

```python
# In HOOK_RESULT_TYPES dict (extends:`hooks.py:1033-1068`):
"session_start": None,                                # observational
"session_before_switch": SessionBeforeSwitchResult,
"session_before_fork": SessionBeforeForkResult,
"session_shutdown": None,                             # observational

# In _REDUCERS dict (extends `hooks.py:1537-1572`):
"session_start": _reducer_observational,
"session_before_switch": _reducer_session_before,
"session_before_fork": _reducer_session_before,
"session_shutdown": _reducer_observational,
```

### B.5 Handler aliases (`hooks.py:1575-1703` block)

```python
SessionStartHandler = Callable[
    [SessionStartHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
SessionBeforeSwitchHandler = Callable[
    [SessionBeforeSwitchHookEvent, "ExtensionContext"],
    SessionBeforeSwitchResult | None | Awaitable[SessionBeforeSwitchResult | None],
]
SessionBeforeForkHandler = Callable[
    [SessionBeforeForkHookEvent, "ExtensionContext"],
    SessionBeforeForkResult | None | Awaitable[SessionBeforeForkResult | None],
]
SessionShutdownHandler = Callable[
    [SessionShutdownHookEvent, "ExtensionContext"],
    None | Awaitable[None],
]
```

### B.6 4 new `HookBus.on` overloads (`hooks.py:1739-2050` block)

Append before the runtime impl at `:2052`. Each mirrors the `session_before_compact` overload at `:1879-1888` exactly.

### B.7 `__all__` additions

Extend the export list at `hooks.py:2195-2306` with all 4 events, 2 results, 4 handlers (10 new symbols).

### B.8 `ExtensionRunner.emit()` + `has_handlers()` (`_extension_runner.py`)

Replace the `@dataclass(frozen=True)` with one carrying 2 optional callable fields. `frozen=True` preserved — the callables are injected at construction by `harness/core.py:632-634`:

```python
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from aelix_agent_core.harness.hooks import HookEvent, HookEventName

@dataclass(frozen=True)
class ExtensionRunner:
    """Pi parity: ``ExtensionRunner`` aggregation surface.

    Sprint 6h₅a (ADR-0081, P-333) — extends the Sprint 6h₁ commands-only
    aggregation surface with Pi-parity ``emit()`` / ``has_handlers()``.
    Both methods delegate to the harness :class:`HookBus` via injected
    callable fields wired at construction time (``harness/core.py:632-634``).
    The dataclass remains ``frozen=True`` — callables are read-only
    after construction. Pi ``runner.ts:680-712`` cancel-aggregation
    semantics are inherited from ``HookBus._reducer_session_before``
    (first-cancel-wins short-circuit + sequential await + per-handler
    error_mode isolation).
    """

    extensions: list[Extension] = field(default_factory=list)
    _emit: Callable[[HookEvent], Awaitable[Any]] | None = None
    _has_handlers: Callable[[HookEventName], bool] | None = None

    def get_registered_commands(self) -> list[ResolvedCommand]:
        ...  # unchanged

    async def emit(self, event: HookEvent) -> Any:
        """Pi parity: ``ExtensionRunner.emit`` (``runner.ts:680-712``).

        Delegates to ``HookBus.emit`` when a bridge callable was injected
        at construction; returns ``None`` (no-op) when unwired.
        """
        if self._emit is None:
            return None
        return await self._emit(event)

    def has_handlers(self, event_name: HookEventName) -> bool:
        """Pi parity: ``ExtensionRunner.hasHandlers``.

        Delegates to ``HookBus.has_handlers``; returns ``False`` when unwired.
        """
        if self._has_handlers is None:
            return False
        return self._has_handlers(event_name)
```

### B.9 `harness/core.py:632-634` wiring AMEND

```python
self._extension_runner: ExtensionRunner = ExtensionRunner(
    extensions=self._extensions,
    _emit=self._hooks.emit,
    _has_handlers=self._hooks.has_handlers,
)
```

---

## §C — `runtime/agent_session_runtime.py` AMEND

### C.1 Module-private `_emit_session_shutdown_event` helper

Add directly after `_extract_user_message_text` (`:76`):

```python
async def _emit_session_shutdown_event(
    extension_runner: Any,  # ExtensionRunner — Any to avoid circular import
    reason: Literal["quit", "reload", "new", "resume", "fork"],
    target_session_file: str | None = None,
) -> bool:
    """Pi parity: ``emitSessionShutdownEvent`` (``runner.ts:177-189``).

    Sprint 6h₅a (ADR-0081, P-334). Module-private helper mirroring Pi's
    top-level export. Gates on ``has_handlers("session_shutdown")`` to
    avoid constructing the event payload when no extension cares.

    Returns ``True`` when the event was emitted, ``False`` when skipped.
    """
    from aelix_agent_core.harness.hooks import SessionShutdownHookEvent
    if not extension_runner.has_handlers("session_shutdown"):
        return False
    await extension_runner.emit(
        SessionShutdownHookEvent(
            type="session_shutdown",
            reason=reason,
            target_session_file=target_session_file,
        )
    )
    return True
```

### C.2 Real `_emit_before_switch` body (replaces `:209-214`)

```python
async def _emit_before_switch(
    self,
    reason: Literal["new", "resume"],
    target_session_file: str | None = None,
) -> bool:
    """Pi parity: ``emitBeforeSwitch`` (``agent-session-runtime.ts:115-130``).

    Sprint 6h₅a (ADR-0081, P-338) — real body. Returns ``True`` when ANY
    handler returned ``{cancel: true}`` (Pi first-cancel-wins via the
    shared ``_reducer_session_before``).
    """
    from aelix_agent_core.harness.hooks import (
        SessionBeforeSwitchHookEvent,
        SessionBeforeSwitchResult,
    )
    runner = self._harness.extension_runner
    if not runner.has_handlers("session_before_switch"):
        return False
    result = await runner.emit(
        SessionBeforeSwitchHookEvent(
            type="session_before_switch",
            reason=reason,
            target_session_file=target_session_file,
        )
    )
    return isinstance(result, SessionBeforeSwitchResult) and result.cancel is True
```

### C.3 Real `_emit_before_fork` body (replaces `:216-220`)

```python
async def _emit_before_fork(
    self,
    entry_id: str,
    position: Literal["before", "at"],
) -> bool:
    """Pi parity: ``emitBeforeFork`` (``agent-session-runtime.ts:132-147``).

    Sprint 6h₅a (ADR-0081, P-339) — real body.
    """
    from aelix_agent_core.harness.hooks import (
        SessionBeforeForkHookEvent,
        SessionBeforeForkResult,
    )
    runner = self._harness.extension_runner
    if not runner.has_handlers("session_before_fork"):
        return False
    result = await runner.emit(
        SessionBeforeForkHookEvent(
            type="session_before_fork",
            entry_id=entry_id,
            position=position,
        )
    )
    return isinstance(result, SessionBeforeForkResult) and result.cancel is True
```

### C.4 `_teardown_current` rewrite (replaces `:222-239`) — **P-340 ORDERING CORRECTION**

```python
async def _teardown_current(
    self,
    reason: Literal["quit", "reload", "new", "resume", "fork"],
    target_session_file: str | None = None,
) -> None:
    """Pi parity: ``teardownCurrent`` (``agent-session-runtime.ts:149-157``).

    Sprint 6h₅a (ADR-0081, P-340) — ordering correction to match Pi:
      1. emit ``session_shutdown`` (extensions still see live harness state)
      2. ``before_session_invalidate?.()`` (signals invalidation)
      3. ``await harness.dispose()`` (tears down HookBus + everything)

    **Race avoidance:** the ``extension_runner`` reference is captured
    at the TOP of the method BEFORE ``harness.dispose()`` is awaited
    (dispose tears down the HookBus → bridge becomes a no-op after).
    """
    # CRITICAL — capture runner BEFORE invalidate/dispose tears it down.
    runner = self._harness.extension_runner
    try:
        await _emit_session_shutdown_event(runner, reason, target_session_file)
    except Exception:
        _log.exception("AgentSessionRuntime.session_shutdown emit raised")

    if self._before_session_invalidate is not None:
        try:
            self._before_session_invalidate()
        except Exception:
            _log.exception(
                "AgentSessionRuntime.before_session_invalidate raised"
            )
    try:
        await self._harness.dispose()
    except Exception:
        _log.exception("AgentSessionRuntime.harness.dispose raised")
```

### C.5 `dispose()` rewrite (replaces `:440-455`) — **P-341**

```python
async def dispose(self) -> None:
    """Pi parity: ``dispose`` (``agent-session-runtime.ts:366-373``).

    Sprint 6h₅a (ADR-0081, P-341) — adds the missing ``session_shutdown``
    emit with ``reason="quit"``. Pi order is INVALIDATE → EMIT → DISPOSE
    (terminal shutdown — extensions see the invalidate signal before
    the final emit). Note the intentional asymmetry vs ``_teardown_current``
    (EMIT → INVALIDATE → DISPOSE) — see §I steelman.
    """
    runner = self._harness.extension_runner
    if self._before_session_invalidate is not None:
        try:
            self._before_session_invalidate()
        except Exception:
            _log.exception(
                "AgentSessionRuntime.before_session_invalidate raised"
            )
    try:
        await _emit_session_shutdown_event(runner, "quit", None)
    except Exception:
        _log.exception("AgentSessionRuntime.session_shutdown emit raised")
    await self._harness.dispose()
```

### C.6 `_finish_session_replacement` signature extension (replaces `:252-265`) — **P-343**

```python
async def _finish_session_replacement(
    self,
    new_session: Session,
    *,
    reason: Literal["new", "resume", "fork"],
    previous_session_file: str | None,
    target_session_file: str | None = None,
) -> None:
    """Pi parity: ``finishSessionReplacement`` (``:166-173``).

    Sprint 6h₅a (ADR-0081, P-343) — extends to carry the Pi
    ``session_start`` payload (``reason`` + ``previousSessionFile``) and
    the ``targetSessionFile`` consumed by ``session_shutdown``.

    Order:
      1. ``_teardown_current(target_session_file)`` — emits shutdown FIRST.
      2. ``_apply(new_session)`` — constructs NEW harness via factory.
      3. ``rebind_session?.(new_harness)`` — fires registered callback.
      4. emit ``session_start`` on NEW harness's ``extension_runner``.
    """
    from aelix_agent_core.harness.hooks import SessionStartHookEvent

    # Map session_start reason → session_shutdown reason (Pi parity:
    # both names mirror each other in the replace lifecycle).
    shutdown_reason: Literal["new", "resume", "fork"] = reason
    await self._teardown_current(shutdown_reason, target_session_file)
    await self._apply(new_session)
    if self._rebind_session is not None:
        await self._rebind_session(self._harness)

    # Emit session_start on the NEW harness's runner (OLD is disposed).
    new_runner = self._harness.extension_runner
    if new_runner.has_handlers("session_start"):
        try:
            await new_runner.emit(
                SessionStartHookEvent(
                    type="session_start",
                    reason=reason,
                    previous_session_file=previous_session_file,
                )
            )
        except Exception:
            _log.exception("AgentSessionRuntime.session_start emit raised")
```

### C.7 `switch_session` AMEND (replaces `:269-299`) — **P-338 + P-342 + P-337 wire**

```python
async def switch_session(
    self,
    path: str,
    *,
    options: dict | None = None,
) -> RuntimeReplaceResult:
    """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

    Sprint 6h₅a (ADR-0081, P-338/P-342/P-337) — wires cancel hook +
    previous_session_file snapshot + assertSessionCwdExists post-load.
    """
    from aelix_agent_core.session.session_cwd import assert_session_cwd_exists

    if await self._emit_before_switch(reason="resume", target_session_file=path):
        return RuntimeReplaceResult(cancelled=True)

    previous_session_file = self.session.session_file if self.session is not None else None

    metadata = await load_jsonl_session_metadata(self._fs, path)
    new_session = await self._repo.open(metadata)

    # P-337 — Pi `session-cwd.ts:1-59`. Aelix runs AFTER repo.open so
    # `new_session.session_file` is populated. Pass `fallback_cwd=self.cwd`
    # so the MissingSessionCwdError carries actionable context.
    await assert_session_cwd_exists(new_session, fallback_cwd=self.cwd, fs=self._fs)

    await self._finish_session_replacement(
        new_session,
        reason="resume",
        previous_session_file=previous_session_file,
        target_session_file=path,
    )
    return RuntimeReplaceResult(cancelled=False)
```

### C.8 `new_session` AMEND (replaces `:301-341`) — **P-338 + P-342**

```python
async def new_session(
    self,
    *,
    parent_session: str | None = None,
) -> RuntimeReplaceResult:
    """Pi parity: ``newSession`` (``agent-session-runtime.ts:200-232``).

    Sprint 6h₅a (ADR-0081, P-338/P-342). Pi `:210` snapshot site.
    """
    if await self._emit_before_switch(reason="new", target_session_file=None):
        return RuntimeReplaceResult(cancelled=True)

    cwd = self.cwd
    if cwd is None:
        raise RuntimeError(
            "new_session requires the current harness session to have a cwd"
        )

    previous_session_file = self.session.session_file if self.session is not None else None

    new_session = await self._repo.create(
        JsonlSessionCreateOptions(
            cwd=cwd, parent_session_path=parent_session
        )
    )
    await self._finish_session_replacement(
        new_session,
        reason="new",
        previous_session_file=previous_session_file,
        target_session_file=None,
    )
    return RuntimeReplaceResult(cancelled=False)
```

### C.9 `fork` AMEND (replaces `:343-417`) — **P-339 + P-342**

```python
async def fork(
    self,
    entry_id: str,
    *,
    position: ForkPosition = "before",
) -> RuntimeReplaceResult:
    """Pi parity: ``fork`` (``agent-session-runtime.ts:234-320``).

    Sprint 6h₅a (ADR-0081, P-339/P-342). Pi `:261` snapshot site.
    """
    if await self._emit_before_fork(entry_id=entry_id, position=position):
        return RuntimeReplaceResult(cancelled=True)

    if self.session is None:
        raise RuntimeError("fork requires an active session")

    # Existing P-325 entry resolution + leaf walk (unchanged) ...
    selected_entry = await self.session.get_entry(entry_id)
    if selected_entry is None:
        raise ValueError("Invalid entry ID for forking")
    selected_text: str | None = None
    if position == "at":
        target_leaf_id: str | None = selected_entry.id
    else:
        if (
            selected_entry.type != "message"
            or selected_entry.message.role != "user"  # type: ignore[union-attr]
        ):
            raise ValueError("Invalid entry ID for forking")
        target_leaf_id = selected_entry.parent_id
        selected_text = _extract_user_message_text(
            selected_entry.message.content  # type: ignore[union-attr]
        )

    previous_session_file = self.session.session_file
    metadata = await self.session.get_metadata()
    new_session = await self._repo.fork(
        source=metadata,
        options=ForkOptions(
            cwd=metadata.cwd,
            entry_id=target_leaf_id,
            position="at",
            parent_session_path=metadata.path,
        ),
    )
    await self._finish_session_replacement(
        new_session,
        reason="fork",
        previous_session_file=previous_session_file,
        target_session_file=None,  # Pi fork has no targetSessionFile
    )
    return RuntimeReplaceResult(
        cancelled=False, selected_text=selected_text
    )
```

### C.10 Module docstring AMEND

Replace the Sprint 6h₄c paragraph with a Sprint 6h₅a addendum noting: extension event Pi parity per ADR-0081 (P-307/P-308/P-337 closure); 4 new events wired (`session_start` / `session_before_switch` / `session_before_fork` / `session_shutdown`); ordering correction (`_teardown_current` emit FIRST); `previous_session_file` snapshot at all 3 replace sites; `assert_session_cwd_exists` wired in `switch_session`.

---

## §D — `session/session.py` + `session/session_cwd.py`

### D.1 `Session.session_file` sync property (`session/session.py:84-302`)

Add directly after `get_storage` (`:98-101`):

```python
@property
def session_file(self) -> str | None:
    """Pi parity: ``AgentSession.sessionFile`` (sync getter).

    Sprint 6h₅a (ADR-0081, P-336). Reads cached ``_metadata.path`` from
    the underlying storage. Returns ``None`` when:
      - metadata has not been hydrated yet, OR
      - the metadata subclass does not carry a ``path`` attribute
        (non-JSONL storage backends).

    Mirrors the cached-metadata access pattern used by
    :attr:`AgentSessionRuntime.cwd` (``runtime/agent_session_runtime.py:171-173``).
    """
    storage = self._storage
    metadata = getattr(storage, "_metadata", None)
    if metadata is None:
        return None
    return getattr(metadata, "path", None) or None
```

### D.2 NEW `session/session_cwd.py` module

```python
"""Pi parity port: ``packages/agent/src/harness/session/session-cwd.ts:1-59``.

Sprint 6h₅a (ADR-0081, P-337). Diagnostic helpers for the
``MissingSessionCwdError`` raised when a session-replacement target
references a working directory that no longer exists on disk.

Aelix divergence: Pi uses sync ``existsSync(...)`` from ``node:fs``;
Aelix :class:`FileSystem` is all-async (``session/fs.py:33-52``), so the
helper functions are themselves ``async`` and accept an injected ``fs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_agent_core.session.fs import FileSystem
    from aelix_agent_core.session.session import Session


@dataclass(frozen=True)
class SessionCwdIssue:
    """Pi parity: ``SessionCwdIssue`` (``session-cwd.ts:1-15``)."""
    session_file: str
    session_cwd: str
    fallback_cwd: str | None


class MissingSessionCwdError(Exception):
    """Pi parity: ``MissingSessionCwdError`` (``session-cwd.ts:30-44``)."""

    def __init__(self, issue: SessionCwdIssue) -> None:
        super().__init__(format_missing_session_cwd_error(issue))
        self.name = "MissingSessionCwdError"
        self.issue = issue


def format_missing_session_cwd_error(issue: SessionCwdIssue) -> str:
    """Pi parity: ``formatMissingSessionCwdError`` (``session-cwd.ts:46-56``)."""
    parts = [
        f"Session working directory does not exist: {issue.session_cwd}",
        f"Session file: {issue.session_file}",
    ]
    if issue.fallback_cwd is not None:
        parts.append(f"Fallback cwd: {issue.fallback_cwd}")
    return "\n".join(parts)


async def get_missing_session_cwd_issue(
    session: Session,
    fallback_cwd: str | None,
    *,
    fs: FileSystem,
) -> SessionCwdIssue | None:
    """Pi parity: ``getMissingSessionCwdIssue`` (``session-cwd.ts:17-28``).

    Returns ``None`` when:
      - session has no session_file (in-memory or pre-hydration), OR
      - session has no cwd metadata, OR
      - the cwd exists on disk.

    Otherwise returns a :class:`SessionCwdIssue` carrying the diagnostic
    triple ``{session_file, session_cwd, fallback_cwd}``.
    """
    session_file = session.session_file
    if session_file is None:
        return None
    metadata = await session.get_metadata()
    session_cwd: str | None = getattr(metadata, "cwd", None)
    if not session_cwd:
        return None
    if await fs.exists(session_cwd):
        return None
    return SessionCwdIssue(
        session_file=session_file,
        session_cwd=session_cwd,
        fallback_cwd=fallback_cwd,
    )


async def assert_session_cwd_exists(
    session: Session,
    fallback_cwd: str | None,
    *,
    fs: FileSystem,
) -> None:
    """Pi parity: ``assertSessionCwdExists`` (``session-cwd.ts:58-63``).

    Raises :class:`MissingSessionCwdError` when the session's cwd is set
    and does not resolve. No-op when metadata or fs check passes.
    """
    issue = await get_missing_session_cwd_issue(session, fallback_cwd, fs=fs)
    if issue is not None:
        raise MissingSessionCwdError(issue)


__all__ = [
    "MissingSessionCwdError",
    "SessionCwdIssue",
    "assert_session_cwd_exists",
    "format_missing_session_cwd_error",
    "get_missing_session_cwd_issue",
]
```

---

## §E — `extensions/api.py` AMEND

4 new `ExtensionAPI.on` overloads + 4 new handler aliases imported from `harness.hooks`:

```python
# Imports at the top:
from aelix_agent_core.harness.hooks import (
    SessionBeforeForkHandler,
    SessionBeforeSwitchHandler,
    SessionShutdownHandler,
    SessionStartHandler,
    # ... existing imports
)

# 4 new @overload blocks appended after `:1082` (resources_discover):
@overload
def on(
    self,
    event: Literal["session_start"],
    handler: SessionStartHandler,
    *,
    cleanup: HookCleanup | None = None,
    error_mode: HookErrorMode = "throw",
) -> Callable[[], None]: ...
@overload
def on(
    self,
    event: Literal["session_before_switch"],
    handler: SessionBeforeSwitchHandler,
    *,
    cleanup: HookCleanup | None = None,
    error_mode: HookErrorMode = "throw",
) -> Callable[[], None]: ...
@overload
def on(
    self,
    event: Literal["session_before_fork"],
    handler: SessionBeforeForkHandler,
    *,
    cleanup: HookCleanup | None = None,
    error_mode: HookErrorMode = "throw",
) -> Callable[[], None]: ...
@overload
def on(
    self,
    event: Literal["session_shutdown"],
    handler: SessionShutdownHandler,
    *,
    cleanup: HookCleanup | None = None,
    error_mode: HookErrorMode = "throw",
) -> Callable[[], None]: ...
```

Overload count: 28 → **32**. Bump the docstring note in the `on` runtime impl from "28 overloads" to "32 overloads" (`extensions/api.py:1104`).

---

## §F — Tests (binding plan)

### F.1 `tests/runtime/test_agent_session_runtime_extension_events.py` (NEW, ~180 LOC)

7 binding cases:

1. **`test_session_shutdown_emit_in_teardown_current_ordering`** — fake harness w/ HookBus subscribed to `session_shutdown`, `before_session_invalidate` recorder. Drive `switch_session(path)`; assert the recorder log ORDER is `["shutdown_emitted", "invalidate_called", "harness_disposed"]`. (P-340 binding.)
2. **`test_session_shutdown_emit_in_dispose_uses_quit_reason`** — call `runtime.dispose()`; assert the captured `SessionShutdownHookEvent.reason == "quit"` AND order is `["invalidate_called", "shutdown_emitted", "harness_disposed"]`. (P-341 binding — intentional asymmetry.)
3. **`test_session_before_switch_cancel_short_circuits_switch`** — register a handler returning `SessionBeforeSwitchResult(cancel=True)` on the OLD harness; call `switch_session(path)`; assert return is `RuntimeReplaceResult(cancelled=True)` AND `repo.open` was NEVER called AND `_teardown_current` was NEVER called.
4. **`test_session_before_fork_cancel_short_circuits_fork`** — symmetric to (3) for `fork`.
5. **`test_session_start_emits_on_new_harness_with_reason_and_previous_session_file`** — register `session_start` handler on the NEW harness (via a HarnessFactory that registers); drive `switch_session(path)`; assert the captured event has `reason="resume"`, `previous_session_file == <old_session.session_file>`.
6. **`test_previous_session_file_captured_before_teardown`** — instrument with a `before_session_invalidate` that mutates `_session = None` mid-flight; assert the captured `previous_session_file` STILL equals the pre-teardown value (snapshot timing).
7. **`test_session_shutdown_emit_no_handlers_is_noop`** — no handlers registered; `_teardown_current` STILL succeeds and dispose order is preserved.

### F.2 `tests/runtime/test_agent_session_runtime_session_cwd.py` (NEW, ~60 LOC)

3 cases:

1. **`test_switch_session_raises_missing_session_cwd_when_target_cwd_missing`** — fake `FileSystem.exists` returns False; assert `MissingSessionCwdError` raised, `_finish_session_replacement` NEVER called.
2. **`test_switch_session_succeeds_when_target_cwd_exists`** — `FileSystem.exists` returns True; assert success path.
3. **`test_switch_session_assert_runs_after_repo_open_not_before`** — assert ordering: `repo.open()` runs first, THEN `assert_session_cwd_exists`. (so the assertion checks the NEW session, not OLD.)

### F.3 `tests/session/test_session_cwd_helper.py` (NEW, ~50 LOC)

4 cases for the standalone helper:

1. `get_missing_session_cwd_issue` returns `None` when `session_file` is None.
2. `get_missing_session_cwd_issue` returns `None` when `fs.exists(cwd)` is True.
3. `get_missing_session_cwd_issue` returns issue when `fs.exists(cwd)` is False.
4. `MissingSessionCwdError` message contains session_file, session_cwd, fallback_cwd.

### F.4 `tests/session/test_session_file_property.py` (NEW, ~30 LOC)

2 cases:

1. `session.session_file` returns the storage `_metadata.path`.
2. `session.session_file` returns None when storage has no `_metadata`.

### F.5 `tests/extensions/test_extension_runner_emit_delegate.py` (NEW, ~50 LOC)

3 cases:

1. `ExtensionRunner.emit(event)` calls the injected `_emit` callable with the event.
2. `ExtensionRunner.has_handlers(name)` calls the injected `_has_handlers` callable.
3. `ExtensionRunner.emit/has_handlers` is no-op (returns None / False) when callables are not wired (defensive).

### F.6 `tests/pi_parity/test_phase_4_14_extension_events.py` (NEW closure pin, ~140 LOC)

CLOSURE PIN — verifies these properties together as a single anti-regression suite:

1. `HookEventName` Literal members include EXACTLY `{session_start, session_before_switch, session_before_fork, session_shutdown}` (string assertion).
2. `HOOK_RESULT_TYPES["session_before_switch"] is SessionBeforeSwitchResult`.
3. `_REDUCERS["session_before_switch"] is _reducer_session_before` (shared reducer).
4. `ExtensionAPI.on` overload count == 32 (introspect via `typing.get_overloads`).
5. `HookBus.on` overload count == 32.
6. Pi line citations present in docstrings: `runner.ts:177-189`, `runner.ts:680-712`, `agent-session-runtime.ts:115-130`, `agent-session-runtime.ts:132-147`, `agent-session-runtime.ts:149-157`, `agent-session-runtime.ts:166-173`, `agent-session-runtime.ts:366-373`, `types.ts:510-557`, `session-cwd.ts:1-59` (grep-based assertion).
7. Pi cancel-aggregation semantics: register 3 handlers; the 2nd returns `cancel=True`; assert the 3rd NEVER ran (first-cancel-wins short-circuit).
8. Exception isolation: 1st handler raises with `error_mode="continue"`, 2nd returns `cancel=True`; assert cancel still wins (exception isolated, chain continued).

### F.7 `tests/extensions/test_overloads_extension_api.py` AMEND (~20 LOC delta)

Bump expected overload count: 28 → 32. Add 4 narrowing assertions per event (handler param type narrowing).

### F.8 `scripts/pyright_spike.py` AMEND (~10 LOC)

Bump expected overload assertion count, add 4 new fixture handlers.

---

## §G — ADRs

### G.1 NEW ADR-0081 — `0081-phase-4-14-extension-event-parity.md`

- **Title:** Phase 4.14 — Extension event Pi parity (P-307/P-308/P-337 closure).
- **Status:** Accepted (Sprint 6h₅a).
- **Context:** Sprint 6h₄c closed the 29/29 RPC roster but left 2 extension-event gaps from ADR-0078 carry-forward (P-307 `session_shutdown` emit, P-308 real `session_before_switch`/`session_before_fork` cancel hooks) AND 1 ADR-0080 deferral (P-337 `assertSessionCwdExists`).
- **Decision:** Wire the 4 Pi extension events end-to-end (types + reducers + overloads + emit sites + cancel semantics) on the existing `AgentSessionRuntime` without changing `HarnessFactory` signatures. Delegate `ExtensionRunner.emit`/`has_handlers` to `HookBus` via injected callables (preserves Pi surface naming without duplicating reducer logic). Correct `_teardown_current` ordering to Pi (emit → invalidate → dispose). Snapshot `previous_session_file` BEFORE teardown at all 3 replace sites.
- **Consequences:** 31 → 35 event names; 28 → 32 overloads; HookEventName closed union widened (anti-regression closure pin in F.6). `session_start` first-emit at bootstrap deferred to 6h₅b (carry-forward).
- **Carry-forward to 6h₅b:** `session_start` reason="startup"|"reload" at factory bootstrap; `ResourcesDiscoverHookEvent.reason` widening to include "new"|"resume"|"fork".

### G.2 NEW ADR-0082 — `0082-phase-4-14-extension-event-closure-pin.md`

- **Title:** Phase 4.14 extension event closure pin.
- **Status:** Accepted (Sprint 6h₅a).
- **Decision:** The Sprint 6h₅a invariants under `tests/pi_parity/test_phase_4_14_extension_events.py` are an anti-regression closure pin. Future sprints MUST NOT collapse the shared `_reducer_session_before` back to handling only 2 types, MUST NOT drop the `previous_session_file` snapshot site, MUST NOT reverse the `_teardown_current` ordering. The closure pin asserts Pi line citations present in docstrings to detect drift.

### G.3 AMEND ADR-0034 (Pi reference version pin)

Add Sprint 6h₅a row: Pi SHA `734e08e` confirmed for `extensions/types.ts:510-557` + `runner.ts:177-189`/`:680-712` + `agent-session-runtime.ts:115-130`/`:132-147`/`:149-157`/`:166-173`/`:366-373` + `session-cwd.ts:1-59`.

### G.4 AMEND ADR-0078 — closure paragraph: "P-307 + P-308 satisfied by ADR-0081 (Sprint 6h₅a). All Sprint 6h₄a/6h₄b extension-event carry-forwards now closed except `session_start` first-emit (deferred to 6h₅b)."

### G.5 AMEND ADR-0080 — closure paragraph: "P-337 (`assertSessionCwdExists` at switch_session site) satisfied by ADR-0081 (Sprint 6h₅a). Pi factory site (`:391`) + importFromJsonl site (`:352`) deferred to Sprint 6h₅c."

### G.6 README addendum

Update the Phase 4 progress section: "Phase 4.14 — extension event Pi parity COMPLETE. 4 new events wired (session_start/session_before_switch/session_before_fork/session_shutdown). 32 ExtensionAPI overloads. `assertSessionCwdExists` ported at switch_session site."

---

## §H — Atomic commit plan (EXACTLY 5)

| # | Commit | Files | Description |
|---|---|---|---|
| 1 | `feat: harness/hooks — 4 new extension lifecycle events + 2 result types (P-332/P-335)` | `harness/hooks.py` | 4 event dataclasses + 2 result dataclasses + 4 handler aliases + 4 HookBus overloads + HookEventName widening + HOOK_RESULT_TYPES + _REDUCERS + __all__ |
| 2 | `feat: extensions/api — 4 new ExtensionAPI.on overloads (P-335)` | `extensions/api.py`, `tests/extensions/test_overloads_extension_api.py`, `scripts/pyright_spike.py` | 4 overload blocks; doc note 28 → 32; overload-count test bumps |
| 3 | `feat: harness/_extension_runner — delegate emit/has_handlers to HookBus (P-333) + Session.session_file property (P-336) + session_cwd module (P-337)` | `harness/_extension_runner.py`, `harness/core.py`, `session/session.py`, `session/session_cwd.py`, `tests/extensions/test_extension_runner_emit_delegate.py`, `tests/session/test_session_file_property.py`, `tests/session/test_session_cwd_helper.py` | ExtensionRunner extension + Session sync getter + session_cwd port + 3 unit-test suites |
| 4 | `feat: runtime/agent_session_runtime — extension event wiring (P-307/P-308/P-340/P-341/P-342/P-343)` | `runtime/agent_session_runtime.py`, `tests/runtime/test_agent_session_runtime_extension_events.py`, `tests/runtime/test_agent_session_runtime_session_cwd.py` | _emit_session_shutdown_event helper + real _emit_before_switch/fork + ordering correction + dispose shutdown emit + previous_session_file snapshot + _finish_session_replacement extension + assert_session_cwd_exists wire + 2 test suites |
| 5 | `test+docs: phase 4.14 closure pin + ADR-0081/0082 + ADR-0034/0078/0080 amends + README` | `tests/pi_parity/test_phase_4_14_extension_events.py`, `docs/decisions/0081-...md`, `docs/decisions/0082-...md`, `docs/decisions/0034-...md`, `docs/decisions/0078-...md`, `docs/decisions/0080-...md`, `README.md` | Closure pin + 2 NEW ADRs + 3 AMEND ADRs + README update |

---

## §I — Verification gates

| Gate | Command | Expected |
|---|---|---|
| Pytest | `uv run pytest -q` | Baseline 1821+ passing → 1821 + ~28 NEW = ~1849+ passing |
| Pyright | `uv run pyright src tests` | Baseline 8 errors maintained (no new) |
| Ruff | `uv run ruff check .` | Clean |
| Overload count | `uv run python scripts/pyright_spike.py` | Assertions pass for 32 narrowed overloads |
| Closure pin | `uv run pytest tests/pi_parity/test_phase_4_14_extension_events.py -v` | 8/8 pass |
| Line citation grep | `grep -rn 'runner.ts:177-189\|agent-session-runtime.ts:115-130\|session-cwd.ts:1-59' src/` | Present in 3+ files |

---

## §J — Consensus Addendum (P-340 steelman — `_teardown_current` vs `dispose` ordering asymmetry)

**Antithesis (steelman against the asymmetry):**
A reviewer could object that `_teardown_current` and `dispose` having DIFFERENT orderings (emit-first vs invalidate-first) is gratuitous complexity — surely a single consistent order ("emit-first everywhere") is simpler and cheaper to test? The asymmetry forces every reader of these two methods to maintain mental state about WHICH lifecycle they're in.

**Why we keep the asymmetry anyway (Pi binding):**
The top-level principle is "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다" — perfect Pi parity is the FIRST objective. Pi DOES this exact asymmetry at `agent-session-runtime.ts:149-157` (teardown) vs `:366-373` (dispose). The reason Pi shipped it this way:

- **`_teardown_current` is mid-replace**: extensions subscribed to `session_shutdown` may legitimately want to cache live harness state (last messages, active model, current session_file) BEFORE the rebind invalidates them — emit-first guarantees that read window.
- **`dispose` is terminal**: there is no successor harness; the invalidate signal is the canonical "we're going away" cue, and extensions get one last `session_shutdown` event AFTER they've acknowledged invalidation (different semantic — "you've been told, here's the formal goodbye").

**Tradeoff tension:**
The asymmetry costs ~3 reviewer-minutes per pull-request that touches lifecycle code, vs gaining Pi-binding exactness which is the SPRINT'S TOP-LEVEL PRINCIPLE. The asymmetry is also load-bearing for any future extension author who reads Pi documentation expecting Pi-shaped behavior. **Tradeoff resolved in favor of Pi parity** with explicit docstring callout (§C.4 + §C.5) + closure pin test (F.6 case 1 & 2) so future drift is caught.

**Synthesis (no synthesis possible — fork forced by Pi binding):**
A consistent-ordering synthesis would diverge from Pi; this spec rejects synthesis here.

**Principle violations (deliberate mode):**
None. The Pi-parity principle is upheld; the convergence option (consistent ordering) was steel-manned and rejected with documented rationale.

---

## §K — Workflow (ADR-0032)

- W0 — investigation (done; this spec).
- W1 — binding spec write (this artifact).
- W2 — implementer drafts commits 1-2 (events surface + overloads); reviewer verifies overload count + Literal widening + handler-alias coverage.
- W3 — implementer drafts commit 3 (runner delegation + Session.session_file + session_cwd module); reviewer verifies bridge no-op safety + Pi-line-citation completeness.
- W4 — implementer drafts commit 4 (runtime extension event wiring); reviewer verifies ordering tests pass + previous_session_file snapshot timing + cancel short-circuit.
- W5 — implementer drafts commit 5 (closure pin + ADRs); reviewer verifies all 3 verification gates green + closure pin grep assertions hit.
- W6 — squash review (NONE — atomic plan is already 5 commits).

---

## References

- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:209-220` — current P-308 stubs (no-args, return False)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:222-239` — current `_teardown_current` (WRONG ordering — invalidate before emit, no shutdown emit)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:440-455` — current `dispose` (missing shutdown emit)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py:252-265` — `_finish_session_replacement` (signature extension target)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py:59-127` — ExtensionRunner (commands-only — needs emit/has_handlers extension)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:115-178` — HookEventName Literal (31 → 35)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:486-519` — SessionBeforeCompactResult + Hook precedent
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:1245-1263` — `_reducer_session_before` (widening target)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py:2131-2171` — `HookBus.has_handlers` + `emit` (delegation target)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:628-634` — ExtensionRunner construction site (wire `_emit`/`_has_handlers`)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/session.py:84-101` — Session class (add `session_file` property after `get_storage`)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/storage.py:60-65` — JsonlSessionMetadata (`.path` source)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/fs.py:33-52` — FileSystem.exists Protocol (async)
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py:802-1083` — ExtensionAPI.on overload block (28 → 32)
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1947-1967` — rebind_session closure (unchanged; reference only)
- `/workspaces/aelix-ai/.omc/specs/sprint-6h4c-phase-4-13-spec.md` — structural template
