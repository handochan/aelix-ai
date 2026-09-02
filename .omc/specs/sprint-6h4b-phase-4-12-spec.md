# Sprint 6h₄b · Phase 4.12 — `AgentSessionRuntime` Pi port + `rebindSession` seam (FOUNDATION) (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-21
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint is a **FOUNDATION-ONLY** port — no new RPC commands are wired. We port Pi `AgentSessionRuntime` (`core/agent-session-runtime.ts:67-374`) + the `rebindSession` closure seam (`rpc-mode.ts:310-349`) to Aelix so that Sprint 6h₄c can wire `switch_session` / `fork` / `clone` on top without re-touching the runtime layer. SUPPORTED stays at **26**; DEFERRED stays at **3** (`switch_session` / `fork` / `clone`); total **29**. Counts do not move in 6h₄b.

---

## §0 — W0 INVESTIGATION FINDINGS

P-numbering continues from P-301 (Sprint 6h₄a W5 last finding) → P-302, P-303, …

### P-302 — Harness rebuild vs Session swap (the foundational architectural decision)

**Pi reality (`agent-session-runtime.ts:67-374`).** Pi keeps the Session pointer mutable on the runtime: `private _session: AgentSession` (constructor `:73`) is reassigned by `finishSessionReplacement` (`:166-173`). Pi's `AgentSession` is a stateless wrapper over `SessionManager` and exposes `dispose()` / `subscribe()` / `bindExtensions()` at the SESSION level, so swapping `_session` does not invalidate the surrounding TS process.

**Aelix reality (verified W0).** `aelix_agent_core.session.session.Session` has **none** of `dispose` / `subscribe` / `bindExtensions` — those live on `AgentHarness` (`harness/core.py:859-870` subscribe, `:1961-1976` dispose). `AgentHarness._session` is set ONCE in `__init__` at `core.py:486`, and `_state.session_id` is captured eagerly at `core.py:524` from the storage metadata. Tools / hooks / extension runtime are bound through `_runtime.bind_core(...)` (`core.py:551+`) at construction time. Replacing only `harness._session` would leave `_state.session_id`, the per-turn snapshot machinery (`_TurnState`), the merged tool table, the cached session-name (`_cached_session_name`), and the extension action bindings all pointing at the OLD session — a class of latent bugs that violates the 1차 목표.

**Decision (BINDING).** Aelix `AgentSessionRuntime` adopts the **harness-rebuild pattern**: the runtime holds a `HarnessFactory` callable that produces a fresh `AgentHarness` bound to a new `Session`. On replace, the OLD harness is fully `dispose()`d (LIFO cleanup, hook bus drain, runtime invalidation) and a NEW harness is constructed from the factory. This preserves the `__init__` invariants (`_state.session_id`, action bindings, merged tools, cached name) that Aelix relies on but Pi's TS class does not. Wire-level Pi parity is maintained because the closure-pinned outputs of `subscribe()`/event pipe + RPC handler returns are observationally identical from the wire.

**Mapping to Pi.** Pi `_session` → Aelix `_harness` (with `_harness.session` and `_harness.subscribe` standing in for Pi `_session.subscribe`). Pi `_session.dispose()` (during `teardownCurrent`) → Aelix `await self._harness.dispose()`. Pi `bindExtensions(...)` → Aelix's `_runtime.bind_core(...)` already runs in `__init__`; no separate `bind_extensions` needed in 6h₄b foundation (real extension-cancel hooks defer to 6h₄c+ per P-308).

Antithesis + tradeoff are recorded in §I.

### P-303 — `rebindSession` closure: parity in shape, async in callable type

Pi closure (`rpc-mode.ts:310-349`) is `const rebindSession = async (): Promise<void> => { … }` and:
1. reassigns the outer `session` capture from `runtimeHost.session`,
2. calls `await session.bindExtensions({…})` to rewire UI-context + commandContextActions + shutdown + onError + onUiResponse,
3. tears down the previous subscription (`unsubscribe?.()`),
4. re-subscribes (`session.subscribe((event) => output(event))`).

Aelix mirrors verbatim with these Pi-parity adjustments:
- `async def rebind() -> None:` (Python `async def` ≡ Pi `async () => Promise<void>`),
- `harness` (capture cell, mutable from outside via the runtime) is reassigned from `runtime_host.harness`,
- the extension-binding equivalent (`bind_core`) already ran during the NEW harness's `__init__` (P-302), so the body in 6h₄b is the SUBSET: reassign capture + tear down old subscription + re-subscribe to the new harness's `subscribe()`,
- the extension-action waveform (`waitForIdle`, `newSession`, `fork`, `navigateTree`, `switchSession`, `reload`) is **NOT WIRED IN 6h₄b** — those land in 6h₄c when the 3 deferred RPC handlers move.

The unsubscribe/subscribe pair MUST be balanced per replace (closure pin asserts).

### P-304 — `runtimeHost.session` getter — Aelix maps to `runtime_host.harness`

Pi getter at `agent-session-runtime.ts:83-85` returns `this._session`. Aelix exposes `AgentSessionRuntime.harness` (current `AgentHarness`) AND `AgentSessionRuntime.session` (read-through to `self._harness._session`, preserving Pi method name for source-level grep parity). Both are documented to return the LIVE references; callers MUST re-read after `setRebindSession`-triggered replacements (Pi semantics — Pi closure captures the local `session` variable via the same re-read pattern at `rpc-mode.ts:310-311`).

### P-305 — Pi `setRebindSession` is fire-and-forget; Aelix mirrors

Pi `setRebindSession(cb: (session: AgentSession) => Promise<void>)` (`agent-session-runtime.ts:99-101`) is called from `finishSessionReplacement` (`:166-173`) as `await this.rebindSession?.(this._session);`. The callback is async but its result is awaited before `finishSessionReplacement` returns. Aelix mirrors: `set_rebind_session(cb: Callable[[AgentHarness], Awaitable[None]])`. The replace path `await self._rebind_session(self._harness)` is awaited inline. **Idempotency:** if `set_rebind_session` is not registered, the runtime no-ops (Pi optional-chaining parity `?.()`).

### P-306 — `_state.session_id` invariant after harness rebuild

`harness/core.py:524` writes `self._state.session_id = metadata.id` in `__init__` only. The NEW harness produced by the factory MUST be bound to the NEW session BEFORE construction so the eager metadata read at `:521-524` resolves to the NEW session's ID. The factory contract therefore receives `Session` as input and returns a constructed `AgentHarness` (NOT an `AgentHarnessOptions`). This makes the invariant **encapsulated by the factory** and unit-testable.

### P-307 — `dispose` ordering + `beforeSessionInvalidate` semantics

Pi `dispose()` (`agent-session-runtime.ts:366-373`):
```ts
async dispose() {
  this.beforeSessionInvalidate?.();
  await this._session.subscribe(...).emit({ type: "session_shutdown" });  // pseudo
  await this._session.dispose();
}
```

Pi `teardownCurrent` (`:149-157`) is the per-replace counterpart: it calls `beforeSessionInvalidate?.()` THEN disposes the current `_session`. Aelix mirrors with `set_before_session_invalidate(cb: Callable[[], None])` (sync; Pi parity — Pi signature is `() => void` not `() => Promise<void>`). `RuntimeReplaceResult` is returned from each replace API; `dispose()` returns `None`.

The Pi `session_shutdown` event is currently emitted by Pi's `AgentSession.dispose()` itself (one layer deeper than this sprint touches). Aelix `AgentHarness.dispose()` does NOT emit `session_shutdown` today (`harness/core.py:1961-1976`). **Decision (binding for 6h₄b):** the runtime's `dispose()` does NOT inject a `session_shutdown` emit in this sprint — the gap is recorded in ADR-0078 carry-forward and lands when Aelix adds the event to harness dispose (Phase 4.x). Closure pin asserts current behavior so the gap is visible.

### P-308 — `session_before_switch` / `session_before_fork` extension cancel hooks DEFER

Pi extension cancel hooks (Pi `agent-session-runtime.ts:115-130` `emitBeforeSwitch` + `:132-147` `emitBeforeFork`) call into the extension API and let a handler return `cancelled: true` to abort the operation. Aelix has no `session_before_switch` / `session_before_fork` hook events today (search of `harness/hooks.py` confirms — neither name is in the `HookEvent` union).

**Decision (binding for 6h₄b):** stub `emit_before_switch()` / `emit_before_fork()` as **no-ops returning `False`** (i.e. never cancel). Real cancel-hook surface defers to ADR-0078 (likely Sprint 6h₅+) and gates on the underlying Aelix hook events being defined. Closure pin asserts both stubs return `False` regardless of inputs.

### P-309 — `run_rpc_mode` signature compat shim — backward-compat for 24 already-wired RPC handlers

W0 verified: `rpc_mode.py:1413-1421` signature is currently `async def run_rpc_mode(harness, *, model_registry=None, stdin=None, stdout_write=None, install_signal_handlers=True)`. Any breaking change to drop `harness` would invalidate every callsite of the 26 already-wired RPC handlers + every test in `tests/rpc/` that calls `run_rpc_mode(harness, …)`.

**Decision (binding):** ADDITIVE signature change.
```python
async def run_rpc_mode(
    harness: AgentHarness,
    *,
    model_registry: ModelRegistry | None = None,
    runtime_host: AgentSessionRuntime | None = None,   # NEW (P-309)
    stdin: ...,
    stdout_write: ...,
    install_signal_handlers: bool = True,
) -> None:
```

When `runtime_host is None`, `run_rpc_mode` constructs a no-replace `AgentSessionRuntime` internally via the helper `_make_passthrough_runtime(harness)` (factory returns the same harness; `setRebindSession` registered to a no-op). The 26 existing handlers receive the SAME `harness` argument they receive today (the existing call site `await handler(harness, cmd)` at `rpc_mode.py:1404` is unchanged). 6h₄c can pass an explicit `runtime_host` to enable replace paths for the 3 deferred handlers without retouching the signature.

### P-310 — Async/sync risk inventory for the Pi port

| Pi member | Pi async? | Aelix async? | Rationale |
|---|---|---|---|
| `setRebindSession` (`:99-101`) | sync (stores callback) | sync | Mirror |
| `setBeforeSessionInvalidate` (`:111-113`) | sync | sync | Mirror |
| `switchSession` (`:175-198`) | async | async | Pi parity; UNUSED in 6h₄b |
| `newSession` (`:200-232`) | async | async | Pi parity; UNUSED in 6h₄b |
| `fork` (`:234-320`) | async | async | Pi parity; UNUSED in 6h₄b |
| `importFromJsonl` (`:329-364`) | async | async | Pi parity; UNUSED in 6h₄b |
| `dispose` (`:366-373`) | async | async | Pi parity |
| `apply` (`:159-164`) (private) | async | async (rebuild via factory) | Awaits factory + rebind |
| `teardownCurrent` (`:149-157`) (private) | async | async | Awaits `harness.dispose()` |
| `finishSessionReplacement` (`:166-173`) (private) | async | async | Awaits `rebind_session` cb |
| `rebindSession` closure (`rpc-mode.ts:310-349`) | async | async | Pi parity |

**No sync→async boundary leaks for 6h₄b foundation.** All four replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) are stubbed in 6h₄b — they raise `NotImplementedError("Sprint 6h₄c — ADR-0078")` from the runtime methods themselves. The `apply` / `teardownCurrent` / `finishSessionReplacement` private helpers ARE fully implemented in 6h₄b because they form the **rebind seam under test**: 6h₄b's unit tests exercise them directly via a synthetic call path (test-only `_apply_for_test(new_harness)`), satisfying the closure-pin requirement that the seam works before 6h₄c wires real callers.

---

## §A — Scope (binding) — LOC table

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_agent_core/runtime/__init__.py` (NEW package) | ~10 | — |
| `aelix_agent_core/runtime/agent_session_runtime.py` (NEW — class + dataclasses) | ~280 | — |
| `aelix_agent_core/runtime/_types.py` (NEW — `HarnessFactory`, `RuntimeReplaceResult`, `AgentSessionRuntimeDiagnostic`) | ~60 | — |
| `aelix_coding_agent/rpc/rpc_mode.py` AMEND — signature shim + `rebind_session` closure + `_make_passthrough_runtime` helper | ~90 | — |
| `tests/runtime/test_agent_session_runtime_unit.py` (NEW) | — | ~120 |
| `tests/runtime/test_rebind_session_closure.py` (NEW) | — | ~90 |
| `tests/rpc/test_rpc_mode_runtime_shim.py` (NEW) | — | ~70 |
| `tests/pi_parity/test_phase_4_12_strict_superset.py` (NEW closure pin) | — | ~140 |
| `tests/pi_parity/fixtures/pi_runtime_734e08e.json` (NEW fixture) | — | — |
| **Totals** | **~440** | **~420** |

LOC fits the 450-550 prod / 250-350 test envelope with a small test overshoot driven by the closure-pin scope (4 test files vs typical 2-3). Total ~860 LOC.

### NOT in scope (deferred to Sprint 6h₄c / later)
- 3 RPC handlers (`switch_session` / `fork` / `clone`) — STILL DEFERRED via `_make_deferred_handler` (closure-pin asserted)
- Real implementations of `AgentSessionRuntime.switch_session` / `new_session` / `fork` / `import_from_jsonl` (stubs raise `NotImplementedError` in 6h₄b)
- `commandContextActions` wiring inside the `rebind_session` closure (`waitForIdle` / `newSession` / `fork` / `navigateTree` / `switchSession` / `reload`)
- `session_shutdown` event emit from `AgentHarness.dispose()` (P-307 carry-forward)
- `session_before_switch` / `session_before_fork` hook events (P-308 carry-forward)
- `SessionManager.getLeafId()` (already partially present via `Session.get_leaf_id()` — Pi parity gap audit deferred)

---

## §B — `aelix_agent_core/runtime/agent_session_runtime.py` (NEW module)

### B.1 Module structure

```python
"""Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.

Sprint 6h₄b (ADR-0077, P-302~P-310) — FOUNDATION-ONLY port. The class is
fully constructible and the rebind seam (``setRebindSession`` +
``finishSessionReplacement`` + the private ``apply`` / ``teardownCurrent``
helpers) is wired and unit-tested. The four public replace APIs
(``switch_session`` / ``new_session`` / ``fork`` / ``import_from_jsonl``)
are scaffolded but raise :class:`NotImplementedError` referencing
ADR-0078 (Sprint 6h₄c wires them when the 3 DEFERRED RPC handlers move).

Architectural decision (P-302): Aelix adopts **harness-rebuild** instead
of session-swap. Pi can swap ``_session`` directly because
``AgentSession`` is a stateless wrapper; Aelix ``AgentHarness`` captures
``_state.session_id`` at ``__init__`` (``harness/core.py:524``) and binds
runtime actions / merges tools / caches session_name during construction.
The harness factory pattern preserves all of these invariants.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aelix_agent_core.runtime._types import (
    AgentSessionRuntimeDiagnostic,
    HarnessFactory,
    RuntimeReplaceResult,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session

_log = logging.getLogger(__name__)


class AgentSessionRuntime:
    """Pi parity: ``AgentSessionRuntime`` (``agent-session-runtime.ts:67-374``).

    The runtime owns the LIVE :class:`AgentHarness` and exposes a rebind
    seam so callers (the ``rpc_mode`` event pipe) can refresh their
    captured ``harness`` reference after a session-replacement operation.

    Sprint 6h₄b ships the FOUNDATION:
      - constructor + getters,
      - ``set_rebind_session`` / ``set_before_session_invalidate``,
      - ``_apply`` / ``_teardown_current`` / ``_finish_session_replacement``
        (private; tested through the ``_apply_for_test`` test seam),
      - ``dispose()`` (no-op-extra; defers to harness dispose),
      - stub `emit_before_switch` / `emit_before_fork` (return False).

    The four public replace APIs (``switch_session`` / ``new_session`` /
    ``fork`` / ``import_from_jsonl``) raise :class:`NotImplementedError`
    referencing ADR-0078 — Sprint 6h₄c implements them.
    """

    def __init__(
        self,
        harness: "AgentHarness",
        create_harness: HarnessFactory,
        *,
        diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
        model_fallback_message: str | None = None,
    ) -> None:
        """Pi parity: constructor signature mirrors
        ``agent-session-runtime.ts:67-74`` modulo the harness-rebuild
        adaptation (P-302). Pi positional args (in order):
        ``_session`` / ``_services`` / ``createRuntime`` /
        ``_diagnostics`` / ``_modelFallbackMessage``.

        Aelix maps:
          - ``_session``  → ``harness`` (P-302 — harness wraps Session)
          - ``_services`` → folded INTO harness (extension runtime / tools)
          - ``createRuntime`` → ``create_harness`` (factory: Session -> Harness)
          - ``_diagnostics`` → ``diagnostics``
          - ``_modelFallbackMessage`` → ``model_fallback_message``
        """

        self._harness = harness
        self._create_harness = create_harness
        self._diagnostics = list(diagnostics) if diagnostics else []
        self._model_fallback_message = model_fallback_message
        self._rebind_session: (
            Callable[["AgentHarness"], Awaitable[None]] | None
        ) = None
        self._before_session_invalidate: Callable[[], None] | None = None

    # === Public getters (Pi `:79-97`) ===========================================

    @property
    def harness(self) -> "AgentHarness":
        """Aelix-additive (P-304). The LIVE :class:`AgentHarness`. Callers
        MUST re-read after a ``setRebindSession``-triggered replacement.
        """
        return self._harness

    @property
    def session(self) -> "Session | None":
        """Pi parity for ``runtimeHost.session`` (``:83-85``). Read-through
        to ``self._harness._session`` (P-304).
        """
        return self._harness._session

    @property
    def cwd(self) -> str | None:
        """Pi parity (``:87-89``). Reads through harness session metadata."""
        # Aelix `Session.get_metadata()` is async; expose the cached cwd
        # captured in the harness state if present, else None.
        session = self._harness._session
        if session is None:
            return None
        storage = session.get_storage()
        metadata = getattr(storage, "_metadata", None)
        return getattr(metadata, "cwd", None) if metadata is not None else None

    @property
    def diagnostics(self) -> list[AgentSessionRuntimeDiagnostic]:
        """Pi parity (``:91-93``)."""
        return list(self._diagnostics)

    @property
    def model_fallback_message(self) -> str | None:
        """Pi parity (``:95-97``)."""
        return self._model_fallback_message

    # === The seam (Pi `:99-113`) ================================================

    def set_rebind_session(
        self, cb: Callable[["AgentHarness"], Awaitable[None]]
    ) -> None:
        """Pi parity: ``setRebindSession`` (``agent-session-runtime.ts:99-101``).

        Stores the callback invoked after every successful harness
        replacement (P-305). Pi signature: ``(session: AgentSession) =>
        Promise<void>``; Aelix passes the NEW harness instead (P-302).
        """
        self._rebind_session = cb

    def set_before_session_invalidate(
        self, cb: Callable[[], None]
    ) -> None:
        """Pi parity: ``setBeforeSessionInvalidate`` (``:111-113``).

        Pi signature is sync (``() => void``). Aelix mirrors.
        """
        self._before_session_invalidate = cb

    # === Private replace seam (Pi `:149-173`) ===================================

    async def _emit_before_switch(self) -> bool:
        """Pi parity: ``emitBeforeSwitch`` (``:115-130``). P-308 stub:
        Aelix has no ``session_before_switch`` hook event yet; returns
        ``False`` (never cancel). Real surface lands per ADR-0078.
        """
        return False

    async def _emit_before_fork(self) -> bool:
        """Pi parity: ``emitBeforeFork`` (``:132-147``). P-308 stub: see
        :meth:`_emit_before_switch`. Returns ``False``.
        """
        return False

    async def _teardown_current(self) -> None:
        """Pi parity: ``teardownCurrent`` (``:149-157``).

        Calls ``beforeSessionInvalidate?.()`` THEN disposes the current
        harness (Pi disposes ``_session``; Aelix disposes the harness
        wrapper — P-302). LIFO ordering preserved per Pi.
        """
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

    async def _apply(self, new_session: "Session") -> None:
        """Pi parity: ``apply`` (``:159-164``).

        Pi reassigns ``this._session = newSession``; Aelix uses the
        factory to construct a NEW harness bound to ``new_session``
        (P-302/P-306). The factory is awaited so async setup (e.g.
        ``await harness.bootstrap()``) is permitted.
        """
        new_harness = await self._create_harness(new_session)
        self._harness = new_harness

    async def _finish_session_replacement(
        self, new_session: "Session"
    ) -> None:
        """Pi parity: ``finishSessionReplacement`` (``:166-173``).

        Order:
          1. ``_teardown_current`` (dispose OLD harness),
          2. ``_apply`` (construct NEW harness from factory),
          3. ``rebind_session?.(new_harness)`` (P-305 fire-and-await).
        """
        await self._teardown_current()
        await self._apply(new_session)
        if self._rebind_session is not None:
            await self._rebind_session(self._harness)

    # === Test seam (Aelix-additive; closure-pin entry point) ===================

    async def _apply_for_test(self, new_session: "Session") -> None:
        """Aelix-additive test seam. Drives the full replace path
        WITHOUT requiring any of the 4 still-stubbed public APIs.

        Used by the closure pin and ``test_rebind_session_closure``. NOT
        part of the Pi surface; explicit ``_for_test`` suffix +
        underscore prefix discourage accidental call from production
        code paths. Sprint 6h₄c may remove this helper once
        ``switch_session`` lands.
        """
        await self._finish_session_replacement(new_session)

    # === Public replace APIs (Pi `:175-364`) — STUBBED for 6h₄b ===============

    async def switch_session(
        self,
        path: str,
        *,
        options: dict | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

        Sprint 6h₄b stub — raises :class:`NotImplementedError`. Sprint
        6h₄c (ADR-0078) implements + wires the matching RPC handler.
        """
        raise NotImplementedError(
            "AgentSessionRuntime.switch_session — Sprint 6h₄c (ADR-0078)"
        )

    async def new_session(
        self, *, options: dict | None = None
    ) -> RuntimeReplaceResult:
        """Pi parity: ``newSession`` (``:200-232``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.new_session — Sprint 6h₄c (ADR-0078)"
        )

    async def fork(
        self,
        entry_id: str,
        *,
        options: dict | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``fork`` (``:234-320``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.fork — Sprint 6h₄c (ADR-0078)"
        )

    async def import_from_jsonl(
        self,
        path: str,
        *,
        cwd: str | None = None,
    ) -> RuntimeReplaceResult:
        """Pi parity: ``importFromJsonl`` (``:329-364``). Stub — see
        :meth:`switch_session`."""
        raise NotImplementedError(
            "AgentSessionRuntime.import_from_jsonl — Sprint 6h₄c (ADR-0078)"
        )

    # === Dispose (Pi `:366-373`) ===============================================

    async def dispose(self) -> None:
        """Pi parity: ``dispose`` (``agent-session-runtime.ts:366-373``).

        Pi: ``beforeSessionInvalidate?.() → emit("session_shutdown") →
        await _session.dispose()``. Aelix 6h₄b: ``beforeSessionInvalidate?.()
        → await harness.dispose()``. The ``session_shutdown`` emit gap is
        recorded in ADR-0078 carry-forward (P-307).
        """
        if self._before_session_invalidate is not None:
            try:
                self._before_session_invalidate()
            except Exception:
                _log.exception(
                    "AgentSessionRuntime.before_session_invalidate raised"
                )
        await self._harness.dispose()


__all__ = ["AgentSessionRuntime"]
```

### B.2 `aelix_agent_core/runtime/_types.py` (NEW)

```python
"""Sprint 6h₄b types — :class:`HarnessFactory`, :class:`RuntimeReplaceResult`,
:class:`AgentSessionRuntimeDiagnostic`. ADR-0077.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session


HarnessFactory = Callable[["Session"], Awaitable["AgentHarness"]]
"""Aelix-additive: factory called by :class:`AgentSessionRuntime` to build
a NEW :class:`AgentHarness` bound to ``new_session`` (P-302/P-306).
Async so callers can ``await harness.bootstrap()`` inside the factory.
"""


@dataclass(frozen=True)
class RuntimeReplaceResult:
    """Pi parity: shape of the value returned by ``switchSession`` /
    ``newSession`` / ``fork`` / ``importFromJsonl`` (Pi
    ``agent-session-runtime.ts:175-320`` return signatures).

    Wire-shape preserves Pi camelCase keys when serialized:
    ``{"cancelled": bool, "selectedText"?: str}``.
    """

    cancelled: bool
    selected_text: str | None = None


@dataclass(frozen=True)
class AgentSessionRuntimeDiagnostic:
    """Pi parity: ``AgentSessionRuntimeDiagnostic`` (Pi
    ``agent-session-runtime.ts`` diagnostics array element type).

    Minimal frozen wrapper carrying a code + human-readable message.
    Extended in Sprint 6h₄c+ as real diagnostics emerge from the four
    replace APIs.
    """

    code: str
    message: str


__all__ = [
    "AgentSessionRuntimeDiagnostic",
    "HarnessFactory",
    "RuntimeReplaceResult",
]
```

### B.3 `aelix_agent_core/runtime/__init__.py` (NEW)

```python
"""Sprint 6h₄b — :class:`AgentSessionRuntime` Pi port (ADR-0077)."""

from __future__ import annotations

from aelix_agent_core.runtime._types import (
    AgentSessionRuntimeDiagnostic,
    HarnessFactory,
    RuntimeReplaceResult,
)
from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime

__all__ = [
    "AgentSessionRuntime",
    "AgentSessionRuntimeDiagnostic",
    "HarnessFactory",
    "RuntimeReplaceResult",
]
```

---

## §C — Harness factory + minimal `harness/core.py` edits

### C.1 Harness factory contract (P-302/P-306)

The factory contract is defined ENTIRELY in `runtime/_types.py` as the `HarnessFactory` callable type. **No changes to `harness/core.py` are required for 6h₄b** — the existing `AgentHarness(AgentHarnessOptions(session=new_session, ...))` constructor is sufficient. Callers (rpc_mode + tests) wrap their own factory closures around `AgentHarnessOptions` to carry whatever extra config (stream_fn, model, tools, extensions, etc.) the parent process supplied.

Example factory closure (lives in rpc_mode, NOT in harness/core):
```python
def make_harness_factory(
    template_options: AgentHarnessOptions,
) -> HarnessFactory:
    async def _factory(new_session: Session) -> AgentHarness:
        from dataclasses import replace
        opts = replace(template_options, session=new_session)
        harness = AgentHarness(opts)
        await harness.bootstrap()  # P-306: populate cached session-name
        return harness
    return _factory
```

### C.2 Why no harness-level edit in 6h₄b

The runtime treats `AgentHarness` as a black box (constructor + `subscribe` + `dispose`). All three of those exist today. The harness needs no new methods to support the foundation. Sprint 6h₄c may add a helper if profiling shows the `dispose() → new __init__` cost is unacceptable, but 6h₄b explicitly accepts the cost (the operations are rare — once per session-tree command) in exchange for invariant preservation.

---

## §D — `aelix_coding_agent/rpc/rpc_mode.py` AMEND

### D.1 Signature shim (P-309 — additive, non-breaking)

```python
async def run_rpc_mode(
    harness: AgentHarness,
    *,
    model_registry: ModelRegistry | None = None,
    runtime_host: AgentSessionRuntime | None = None,   # NEW (P-309)
    harness_factory: HarnessFactory | None = None,     # NEW companion
    stdin: asyncio.StreamReader | None = None,
    stdout_write: Callable[[bytes], None] | None = None,
    install_signal_handlers: bool = True,
) -> None:
```

- When `runtime_host is None`: construct via `_make_passthrough_runtime(harness, harness_factory)`. If `harness_factory is None`, use a no-op factory that returns the SAME `harness` (closes the type while preserving "no replace possible" semantics — `_apply_for_test` is the only entry that would exercise it, and production code paths in 6h₄b never call it).
- When `runtime_host is not None`: ignore `harness` for construction, but the existing dispatch loop `await handler(harness, cmd)` continues to receive `runtime_host.harness` (the LIVE harness after the most recent rebind — P-303/P-304).

### D.2 `_make_passthrough_runtime` helper (NEW)

```python
def _make_passthrough_runtime(
    harness: AgentHarness,
    harness_factory: HarnessFactory | None,
) -> AgentSessionRuntime:
    """Construct a no-replace :class:`AgentSessionRuntime` wrapping the
    passed harness. Used by :func:`run_rpc_mode` when caller passes no
    explicit ``runtime_host`` so the 26 already-wired handlers keep
    working without API breakage (P-309).

    When ``harness_factory is None`` a closure returning the same
    harness is installed — calling any of the 4 still-stubbed replace
    APIs from 6h₄b still raises :class:`NotImplementedError`, so the
    no-op factory is unreachable from production paths.
    """

    if harness_factory is None:
        async def _noop_factory(_new_session: "Session") -> AgentHarness:
            return harness
        harness_factory = _noop_factory
    return AgentSessionRuntime(harness, harness_factory)
```

### D.3 `rebind_session` closure (NEW — mirror Pi `rpc-mode.ts:310-349`)

Inserted INSIDE `run_rpc_mode` after the existing `unsubscribe = harness.subscribe(_on_agent_event)` line (currently `rpc_mode.py:1507`). The closure captures the mutable `harness` and `unsubscribe` cells via a tiny `_Capture` object (Python lacks Pi's outer `let` mutation across function boundaries):

```python
class _Capture:
    """6h₄b — mutable cell for the ``rebind_session`` closure (P-303).

    Pi closes over outer ``let session`` + ``let unsubscribe`` variables
    (``rpc-mode.ts:310-349``); Python closures can read but not rebind
    enclosing names, so we attach them to a lightweight container.
    """
    harness: AgentHarness
    unsubscribe: Callable[[], None]

capture = _Capture()
capture.harness = runtime_host.harness
capture.unsubscribe = capture.harness.subscribe(_on_agent_event)

async def rebind_session(new_harness: AgentHarness) -> None:
    """Pi parity: ``rebindSession`` closure (``rpc-mode.ts:310-349``).

    Sprint 6h₄b — FOUNDATION subset (P-303). Reassigns the captured
    harness, tears down the previous subscription, and re-subscribes
    to the new harness's event stream. The ``bindExtensions`` /
    ``commandContextActions`` waveform (Pi ``:315-345``) is NOT wired
    in 6h₄b — Aelix's ``_runtime.bind_core`` already ran during the
    NEW harness's ``__init__`` (P-302). Sprint 6h₄c wires the
    explicit action surface when the 3 DEFERRED RPC handlers move.
    """
    capture.harness = new_harness
    capture.unsubscribe()
    capture.unsubscribe = capture.harness.subscribe(_on_agent_event)

runtime_host.set_rebind_session(rebind_session)
```

### D.4 Dispatch loop change (1 line — P-309)

```python
# Existing:
response = await _handle_command(harness, payload, dispatch)

# Becomes:
response = await _handle_command(capture.harness, payload, dispatch)
```

The single capture-read change preserves Pi parity: the handler always sees the LIVE harness (after any replace). When `runtime_host` is None or no replace happens (the entire 6h₄b production path), `capture.harness` IS the same object as the original `harness` argument — no behavior change for the 26 wired handlers.

### D.5 DEFERRED_COMMANDS — UNCHANGED in 6h₄b

```python
DEFERRED_COMMANDS: dict[str, str] = {
    "switch_session": "ADR-0078 — Sprint 6h₄c wires runtime-host bridge",
    "fork": "ADR-0078 — Sprint 6h₄c wires runtime-host bridge",
    "clone": "ADR-0078 — Sprint 6h₄c wires runtime-host bridge",
}
```

**Only the ADR citation string changes** (0076 → 0078) — the keys stay identical. The 3 commands STILL route to `_make_deferred_handler` (closure pin asserts). Counts stay at 26/3/29.

### D.6 SUPPORTED_COMMANDS — UNCHANGED in 6h₄b

No additions. Stays at 26.

### D.7 Module docstring update

Append:
```python
"""
Sprint 6h₄b (ADR-0077 / ADR-0078 / P-302~P-310) — FOUNDATION-ONLY port
of Pi ``AgentSessionRuntime`` (``core/agent-session-runtime.ts:67-374``)
and the ``rebindSession`` closure (``rpc-mode.ts:310-349``). No new RPC
commands wired; counts stay at 26 supported / 3 deferred / 29 total.
The 3 deferred commands rebrand to ADR-0078 owner (Sprint 6h₄c). The
``run_rpc_mode`` signature accepts a NEW optional
``runtime_host: AgentSessionRuntime | None = None`` parameter; existing
callers continue to work unchanged (P-309 compat shim).
"""
```

---

## §E — Tests (binding plan)

### E.1 `tests/runtime/test_agent_session_runtime_unit.py` (~120 LOC)

| # | Test | Assertion |
|---|---|---|
| 1 | construct with harness + factory | getters return non-None |
| 2 | `runtime.harness is original_harness` after construction | identity preserved |
| 3 | `runtime.session is original_harness._session` | P-304 read-through |
| 4 | `runtime.cwd` returns metadata cwd when present | helper logic |
| 5 | `runtime.cwd` returns None when session is None | defensive |
| 6 | `runtime.diagnostics` returns COPY (not internal list) | mutation isolation |
| 7 | `runtime.model_fallback_message` returns init value | mirror |
| 8 | `set_rebind_session` stores the cb (introspect `_rebind_session`) | seam |
| 9 | `set_before_session_invalidate` stores the cb | seam |
| 10 | `await runtime._emit_before_switch()` returns False | P-308 stub |
| 11 | `await runtime._emit_before_fork()` returns False | P-308 stub |
| 12 | `await runtime.switch_session("x")` → `NotImplementedError("Sprint 6h₄c")` | stub |
| 13 | `await runtime.new_session()` → `NotImplementedError("Sprint 6h₄c")` | stub |
| 14 | `await runtime.fork("e1")` → `NotImplementedError("Sprint 6h₄c")` | stub |
| 15 | `await runtime.import_from_jsonl("p")` → `NotImplementedError("Sprint 6h₄c")` | stub |
| 16 | `await runtime.dispose()` disposes the harness | observe via `_phase`/disposal flag |
| 17 | `await runtime.dispose()` calls `before_session_invalidate` first | order |
| 18 | `RuntimeReplaceResult(cancelled=False)` constructs frozen with default `selected_text=None` | dataclass |
| 19 | `RuntimeReplaceResult(cancelled=True, selected_text="hi")` — frozen mutation raises | dataclass |
| 20 | `AgentSessionRuntimeDiagnostic` is frozen with `code` + `message` fields | dataclass |

### E.2 `tests/runtime/test_rebind_session_closure.py` (~90 LOC)

| # | Test | Assertion |
|---|---|---|
| 1 | `_apply_for_test(new_session)` produces NEW harness via factory | identity changes |
| 2 | OLD harness `dispose()` called exactly ONCE during replace | mock spy |
| 3 | `before_session_invalidate` called exactly ONCE before dispose | order |
| 4 | `rebind_session` callback called exactly ONCE per replace (P-308 closure pin) | spy |
| 5 | `rebind_session` callback receives the NEW harness | identity |
| 6 | `runtime.harness` points at NEW harness after replace | mutability |
| 7 | `runtime.session` points at NEW harness's session after replace | P-304 read-through |
| 8 | `_state.session_id` on NEW harness reflects NEW session metadata (P-306) | invariant preserved |
| 9 | Subscribe/unsubscribe count balanced (1 unsubscribe + 1 subscribe per replace) | spy on subscribe |
| 10 | Two consecutive `_apply_for_test` calls → 2 disposes + 2 rebinds + 2 sub/unsub pairs | repeat |
| 11 | `set_rebind_session` not registered → replace succeeds, no cb invocation, no crash | optional-chain parity |

### E.3 `tests/rpc/test_rpc_mode_runtime_shim.py` (~70 LOC)

| # | Test | Assertion |
|---|---|---|
| 1 | `run_rpc_mode(harness)` (no `runtime_host`) — runs without `AttributeError` | back-compat |
| 2 | `_make_passthrough_runtime(harness, None)` returns runtime whose `.harness is harness` | shim |
| 3 | `_make_passthrough_runtime`'s no-op factory returns SAME harness | identity |
| 4 | Dispatch loop reads `capture.harness` (introspect via patched dispatch) | P-309 |
| 5 | When `runtime_host` passed in: dispatch still gets `runtime_host.harness` | seam |
| 6 | All 24 existing supported handlers still callable with no `runtime_host` arg | regression |
| 7 | All 3 deferred handlers STILL return `RpcErrorResponse` with `ADR-0078` in error string | rebrand |

### E.4 `tests/pi_parity/test_phase_4_12_strict_superset.py` (NEW, ~140 LOC)

| # | Test | Assertion |
|---|---|---|
| **§A counts** | | |
| 1 | `len(SUPPORTED_COMMANDS) == 26` (UNCHANGED from 6h₄a) | foundation sprint |
| 2 | `len(DEFERRED_COMMANDS) == 3` (UNCHANGED) | foundation sprint |
| 3 | `SUPPORTED ∪ DEFERRED == 29 with disjoint` | invariant |
| **§B deferred owner rebrand** | | |
| 4 | Every DEFERRED owner cites `ADR-0078` (was `ADR-0076` in 6h₄a) | P-309 doc decision |
| 5 | `set(DEFERRED_COMMANDS) == {"switch_session", "fork", "clone"}` | invariant |
| **§C deferred STILL route to `_make_deferred_handler`** | | |
| 6 | For each of 3 deferred: `table[cmd]` qualname contains `_make_deferred_handler` | 6h₄c NOT YET wired |
| 7 | For each of 3 deferred: invoking returns `RpcErrorResponse` with `ADR-0078` | rebrand verify |
| **§D runtime class shape** | | |
| 8 | `AgentSessionRuntime` is importable from `aelix_agent_core.runtime` | package |
| 9 | `AgentSessionRuntime` has methods: `set_rebind_session`, `set_before_session_invalidate`, `switch_session`, `new_session`, `fork`, `import_from_jsonl`, `dispose` | surface |
| 10 | `AgentSessionRuntime` has properties: `harness`, `session`, `cwd`, `diagnostics`, `model_fallback_message` | getters |
| 11 | `RuntimeReplaceResult.__dataclass_fields__.keys() == {"cancelled", "selected_text"}` | shape |
| 12 | `RuntimeReplaceResult` is frozen | invariant |
| 13 | `AgentSessionRuntimeDiagnostic` is frozen with `{"code", "message"}` | invariant |
| **§E closure-pin assertions (P-308)** | | |
| 14 | `_apply_for_test` invokes `set_rebind_session` cb exactly once per successful replace | seam |
| 15 | `_apply_for_test` keeps subscribe/unsubscribe count balanced (delta == 0) | seam |
| **§F line-citation pins** | | |
| 16 | Runtime class docstring cites `agent-session-runtime.ts:67-374` | Pi ref |
| 17 | `set_rebind_session` docstring cites `:99-101` | Pi ref |
| 18 | `set_before_session_invalidate` docstring cites `:111-113` | Pi ref |
| 19 | `_teardown_current` docstring cites `:149-157` | Pi ref |
| 20 | `_apply` docstring cites `:159-164` | Pi ref |
| 21 | `_finish_session_replacement` docstring cites `:166-173` | Pi ref |
| 22 | `dispose` docstring cites `:366-373` | Pi ref |
| 23 | `rebind_session` closure (in `rpc_mode.py`) module-level note cites `rpc-mode.ts:310-349` | Pi ref |
| **§G compat shim** | | |
| 24 | `run_rpc_mode` signature contains `runtime_host` parameter | P-309 |
| 25 | `_make_passthrough_runtime` is importable from `rpc_mode` | helper |
| **§H fixture pin** | | |
| 26 | Fixture pi_sha == `734e08edf82ff315bc3d96472a6ebfa69a1d8016` | Pi pin |
| 27 | Fixture `runtime_class_lines == "67-374"` | line ref |
| 28 | Fixture `rebind_session_lines == "310-349"` | line ref |
| 29 | Fixture `architecture_decision == "harness-rebuild"` | P-302 |

### E.5 Fixture `tests/pi_parity/fixtures/pi_runtime_734e08e.json` (NEW)

```json
{
  "pi_sha": "734e08edf82ff315bc3d96472a6ebfa69a1d8016",
  "sprint": "6h_4_b",
  "phase": "4.12",
  "runtime_class_lines": "67-374",
  "rebind_session_lines": "310-349",
  "pi_members": {
    "constructor": "67-74",
    "get_services": "79-81",
    "get_session": "83-85",
    "get_cwd": "87-89",
    "get_diagnostics": "91-93",
    "get_model_fallback_message": "95-97",
    "set_rebind_session": "99-101",
    "set_before_session_invalidate": "111-113",
    "emit_before_switch": "115-130",
    "emit_before_fork": "132-147",
    "teardown_current": "149-157",
    "apply": "159-164",
    "finish_session_replacement": "166-173",
    "switch_session": "175-198",
    "new_session": "200-232",
    "fork": "234-320",
    "import_from_jsonl": "329-364",
    "dispose": "366-373"
  },
  "command_count_arithmetic": {
    "before_sprint_6h_4_b": "26 supported / 3 deferred / 29 total",
    "after_sprint_6h_4_b": "26 supported / 3 deferred / 29 total",
    "supported_added": [],
    "supported_removed": [],
    "still_deferred": ["switch_session", "fork", "clone"],
    "owner_rebrand": {
      "from": "ADR-0076 — Sprint 6h₄b session tree runtime",
      "to": "ADR-0078 — Sprint 6h₄c wires runtime-host bridge"
    }
  },
  "architecture_decision": "harness-rebuild",
  "architecture_rationale": "Pi AgentSession is a stateless wrapper; Aelix AgentHarness captures _state.session_id at __init__ (harness/core.py:524) and binds runtime actions during construction. Session swap would leave captured fields stale; harness rebuild preserves invariants (P-302/P-306).",
  "_p_302_note": "Aelix adopts harness-rebuild over Pi's session-swap because AgentHarness.__init__ captures _state.session_id at line 524 from session.get_storage()._metadata. Replacing only _session would leave session_id stale. The HarnessFactory pattern preserves the invariant by reconstructing the harness for each new Session.",
  "_p_307_note": "Pi dispose() emits session_shutdown before disposing _session. Aelix AgentHarness.dispose() does NOT emit this event today (core.py:1961-1976). Gap recorded in ADR-0078 carry-forward; closure pin asserts current behavior so the gap is visible.",
  "_p_308_note": "Pi emitBeforeSwitch / emitBeforeFork dispatch to extensions for cancellation. Aelix has no session_before_switch / session_before_fork hook events today. Sprint 6h₄b stubs return False (never cancel); real surface lands per ADR-0078."
}
```

---

## §F — ADRs

### F.1 NEW: ADR-0077 — `0077-agent-session-runtime-port.md`

**Status:** Accepted (Sprint 6h₄b / Phase 4.12 — FOUNDATION-ONLY port)

**Context:** Pi `AgentSessionRuntime` (`agent-session-runtime.ts:67-374`) abstracts the session-replacement lifecycle so RPC commands like `switch_session` / `fork` / `clone` (Pi `rpc-mode.ts` case sites 566 / 574 / 586) can swap the active session WITHOUT tearing down the RPC process. The `rebindSession` closure (`rpc-mode.ts:310-349`) is the seam through which the event-pipe `output` callback reacquires the new session's subscription. Aelix lacks this layer entirely.

**Decision:**
1. Port `AgentSessionRuntime` as `aelix_agent_core.runtime.agent_session_runtime.AgentSessionRuntime`.
2. Adopt **harness-rebuild** instead of session-swap (P-302). The runtime holds a `HarnessFactory: Callable[[Session], Awaitable[AgentHarness]]` and replaces by disposing the old harness + constructing a fresh one via the factory.
3. Port the `rebind_session` closure into `rpc_mode.py` (subset: re-subscribe only; the Pi `bindExtensions` action surface defers to 6h₄c per ADR-0078).
4. The 4 public replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) are STUBBED with `NotImplementedError("Sprint 6h₄c — ADR-0078")` in 6h₄b. The PRIVATE seam (`_apply` / `_teardown_current` / `_finish_session_replacement`) is fully implemented and tested through the `_apply_for_test` helper.
5. `run_rpc_mode` accepts a NEW optional `runtime_host: AgentSessionRuntime | None = None`. When None, a no-op passthrough runtime is constructed (P-309 compat shim).

**Consequences:**
- **Pi parity (architectural):** every Pi member of `AgentSessionRuntime` has an Aelix counterpart at the corresponding line range.
- **Pi parity (wire):** zero observable change in 6h₄b — counts stay 26/3/29, no new commands.
- **Foundation enables 6h₄c:** the 3 deferred RPC handlers can be wired in 6h₄c by removing the `NotImplementedError` stubs + updating DEFERRED_COMMANDS, with NO changes required to the runtime seam.
- **Cost:** harness rebuild on each replace pays a `__init__` cost (tool merge + action bind). Acceptable because replace operations are rare (user explicit `/fork`, `/clone`, `/switch_session`).

**Pi line citations (verified at SHA 734e08e):** `agent-session-runtime.ts:67-374` (class), `:99-101` (setRebindSession), `:111-113` (setBeforeSessionInvalidate), `:149-157` (teardownCurrent), `:159-164` (apply), `:166-173` (finishSessionReplacement), `:175-198` (switchSession), `:200-232` (newSession), `:234-320` (fork), `:329-364` (importFromJsonl), `:366-373` (dispose). `rpc-mode.ts:306-308` (registration), `:310-349` (rebindSession closure), `:367` (initial bind), `:422` / `:566` / `:574` / `:586` (call sites).

### F.2 NEW: ADR-0078 — `0078-phase-4-12-strict-superset-closure.md`

**Status:** Accepted (Sprint 6h₄b / Phase 4.12 closure pin)

**Closure pin scope:** P-302 ~ P-310. Counts: **26 supported / 3 deferred / 29 total** (UNCHANGED from 6h₄a — foundation sprint).

**Carry-forward roster (Sprint 6h₄c targets):**
1. Wire `switch_session` RPC handler → `runtime_host.switch_session(path)` (Pi `rpc-mode.ts:566`).
2. Wire `fork` RPC handler → `runtime_host.fork(entry_id, options)` (Pi `:574`).
3. Wire `clone` RPC handler → `runtime_host.import_from_jsonl(path)` or per-Pi clone path (verify call site at `:586`).
4. Implement the 4 stubbed replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) on `AgentSessionRuntime`.
5. Extend the `rebind_session` closure body with Pi's `commandContextActions` action surface (`waitForIdle` / `newSession` / `fork` / `navigateTree` / `switchSession` / `reload` — Pi `rpc-mode.ts:315-340`).
6. Audit `_state.session_id` rebuild path against the live `switch_session` flow (P-306 stress-test).

**Carry-forward roster (later sprints):**
1. **P-307 — `session_shutdown` event emit** from `AgentHarness.dispose()`. Pi emits at `agent-session.ts` during the session dispose; Aelix doesn't emit today. Closure pin asserts CURRENT behavior; lift the pin when the emit is added.
2. **P-308 — `session_before_switch` / `session_before_fork` hook events**. Pi `emitBeforeSwitch` / `emitBeforeFork` (`agent-session-runtime.ts:115-130` / `:132-147`) dispatch to extensions for cancellation. Aelix `harness/hooks.py` lacks these event types entirely. 6h₄b stubs return `False`; real surface lands when Aelix hook event union grows (Sprint 6h₅+ likely).
3. **`AgentSessionRuntimeDiagnostic` real population.** Currently a frozen `(code, message)` placeholder.
4. **`model_fallback_message`** integration (currently unused in 6h₄b — present for Pi parity).

### F.3 AMEND: ADR-0034 (Pi reference version pin)

Add Sprint 6h₄b row to the sprint ledger: "Sprint 6h₄b — Phase 4.12 — `AgentSessionRuntime` port + `rebindSession` seam (FOUNDATION) — P-302~P-310 — pin 734e08e".

### F.4 AMEND: ADR-0076 (Phase 4.11 closure pin)

Append note: "Sprint 6h₄b lands the `AgentSessionRuntime` foundation; 3 commands still deferred. ADR-0078 owner rebrand from ADR-0076 → ADR-0078 in `DEFERRED_COMMANDS` strings. ADR-0076 closure pin assertions remain valid — counts unchanged at 26/3/29."

### F.5 README

Add 0077 + 0078 rows; add Sprint 6h₄b sub-table marking it as a FOUNDATION sprint with `counts unchanged` annotation.

---

## §G — Atomic commit plan (W6, EXACTLY 5)

1. `feat: runtime/_types + runtime/agent_session_runtime — AgentSessionRuntime FOUNDATION port (P-302/P-303/P-304/P-305/P-306/P-307/P-308/P-309/P-310)`
2. `feat: rpc/rpc_mode — run_rpc_mode runtime_host shim + rebind_session closure + _make_passthrough_runtime + DEFERRED owner rebrand 0076→0078 (P-303/P-309)`
3. `test: runtime — agent_session_runtime unit + rebind_session closure pin (P-308/P-302/P-306)`
4. `test: rpc — rpc_mode_runtime_shim (P-309 backward-compat regression)`
5. `test+docs: Sprint 6h₄b closure pin (P-302~P-310) + ADRs 0077/0078 + ADR-0034/0076 amends + README`

---

## §H — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1678 baseline + ~50 new ≥ 1728; 0 fail |
| ruff | clean |
| pyright | 8 baseline (no regression) |
| Closure pins 4.4/4.6/4.8/4.9/4.10/4.11/4.12 | counts 26 / 3 / 29 (UNCHANGED — foundation) |
| Atomic commits | exactly 5 |
| Pi line citations in docstrings | `67-374` / `99-101` / `111-113` / `149-157` / `159-164` / `166-173` / `175-198` / `200-232` / `234-320` / `329-364` / `366-373` (runtime), `310-349` (rebind closure) |
| `RuntimeReplaceResult` lock | `{"cancelled", "selected_text"}` frozen |
| `AgentSessionRuntimeDiagnostic` lock | `{"code", "message"}` frozen |
| `run_rpc_mode` signature | accepts optional `runtime_host` param (P-309) |
| DEFERRED owner rebrand | every owner cites `ADR-0078` (was `ADR-0076`) |
| 3 deferred still route to `_make_deferred_handler` | closure pin asserts |
| Subscribe/unsubscribe balance | delta == 0 per replace |
| `_rebind_session` callback invocation count | exactly 1 per successful replace |

---

## §I — Consensus Addendum (P-302 SYNTHESIS — Harness rebuild vs Session swap)

**Antithesis (steelman against harness-rebuild):**
The Top-level binding principle is "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다." Pi reassigns `this._session` in-place (`agent-session-runtime.ts:166-173`); it does NOT rebuild the surrounding host. A strict-Pi-parity reading would say: **port Pi's structure literally — swap `harness._session` and accept the consequences**, then patch `_state.session_id` reactively. Harness-rebuild is an Aelix-additive divergence that adds construction cost (tool merge + action bind) on every replace, makes the rebind seam observably different from Pi, and forces a `HarnessFactory` callable that has no Pi analog.

**Tradeoff tension (real, irreducible):**

| Axis | Harness-rebuild (recommended) | Session-swap (literal Pi) |
|---|---|---|
| Pi structural parity | LOWER (factory has no Pi analog) | HIGHER (mirrors `_session = newSession`) |
| Pi behavioral parity (wire) | EQUAL (events emitted same way) | EQUAL |
| Invariant safety (`_state.session_id`, tool merge, action bind, cached session-name) | SAFE (rebuilt every replace) | FRAGILE (must patch reactively; risk of latent staleness) |
| Replace cost | HIGHER (one full `__init__`) | LOWER (one pointer write) |
| Test-time invariants | tractable (each replace is observably idempotent) | brittle (must enumerate every `_state.*` field that depends on session) |
| 6h₄c surface-area impact | LOW (factory is the only new contract) | MEDIUM (every `_state.*` field becomes a 6h₄c migration task) |

**Synthesis (binding):**
The 1차 목표 ("Pi 완전 동일하게") admits two readings: **structural** (mirror the source line-for-line) vs **behavioral** (wire bytes + observable semantics identical). Aelix consistently chooses **behavioral parity** when the source-level mirror is impossible without latent bugs — see Sprint 4a's `Session` becoming concrete (Pi parity = "Pi `Session` is concrete", which Aelix mirrors structurally) AND Sprint 5b's `_runtime.bind_core(...)` model (Aelix-additive surface that has no Pi analog because Pi's TS extension API uses field-level capture, which Python can't replicate cheaply).

The harness-rebuild decision is the SAME SHAPE of decision: pick the Aelix-additive surface (factory) that preserves the wire-level Pi parity AND the captured-invariant safety. The Pi line citations in every method docstring keep the structural-parity audit trail intact for reviewers.

**Why this is not a rubber-stamp:** the session-swap alternative is real, has lower replace cost, AND would be a smaller code change. We reject it because it would force 6h₄c (or worse, a post-6h₄c bug-fix sprint) to enumerate the captured-invariant patch list — a class of work that would be invisible until production exhibits a stale `session_id` in a `before_provider_request` event. The cost asymmetry (one extra `__init__` per session replace, vs. an unbounded latent-bug surface) makes harness-rebuild the right call.

**Principle violations (deliberate mode):** None. The harness-rebuild surface is Aelix-additive and Pi-citation-traceable; no Pi member is omitted from the runtime class; every Pi line range has an Aelix counterpart at the matching method.

---

## §J — Workflow (ADR-0032)

- W0 ✓ DONE — Pi line ranges verified at SHA 734e08e (this spec §0).
- W1 binding spec (this doc).
- W2 executor opus — emits commits 1-2 (runtime + rpc shim).
- W3 verification — runs full pytest + ruff + pyright; confirms 26/3/29 unchanged.
- W4 code-reviewer opus (parallel W5) — audits factory contract + harness-rebuild rationale + rebind-closure capture pattern.
- W5 architect Pi parity audit (parallel W4) — verifies docstring line citations against Pi at SHA 734e08e for every runtime method.
- W6 apply must-fixes + 5 commits + ADRs 0077/0078 + ADR-0034/0076 amends + README.

---

**End of binding spec. Architect READ-ONLY until W6.**

**Korean principle echo:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다." — 본 스프린트는 FOUNDATION-ONLY로, RPC wire 카운트는 26/3/29 그대로 유지하면서 6h₄c가 Pi `rpc-mode.ts:566 / :574 / :586` 사이트를 추가 변경 없이 실어낼 수 있는 `AgentSessionRuntime` + `rebindSession` seam을 Aelix에 정밀하게 이식합니다.
