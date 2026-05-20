# Sprint 6h₂ · Phase 4.9 — 9 RPC commands wired (queue + auto + abort + cycle) (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-20
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint wires **9 RPC commands** in `rpc_mode.py`: `steer` / `follow_up` (queue paths with `images`), `cycle_thinking_level`, `set_steering_mode` / `set_follow_up_mode` (queue mode setters), `set_auto_compaction` / `set_auto_retry` / `abort_retry` (auto-mode flags), `abort_bash` (best-effort cancellation). After Sprint 6h₂: `SUPPORTED_COMMANDS` 13→22, `DEFERRED_COMMANDS` 16→7. Remaining 7 commands (5 session tree + 2 session inspection) defer to Sprint 6h₃.

---

## §0 — W0 INVESTIGATION FINDINGS

### P-245 — All 9 RpcCommand types already exist in `rpc_types.py` (Sprint 6d Pi-parity port)

W0 verified: `rpc_types.py` already defines `RpcCommandSteer`, `RpcCommandFollowUp`, `RpcCommandCycleThinkingLevel`, `RpcCommandSetSteeringMode`, `RpcCommandSetFollowUpMode`, `RpcCommandSetAutoCompaction`, `RpcCommandSetAutoRetry`, `RpcCommandAbortRetry`, `RpcCommandAbortBash` with the correct fields. Sprint 6h₂ does **NOT** touch types — only adds handlers + harness setters.

### P-246 — Aelix harness already has `steer(text)` + `follow_up(text)` but missing `images` parameter

Pi `session.steer(message, images)` accepts `ImageContent[]` (`rpc-mode.ts:528-531`). Aelix `harness.steer(text)` at `core.py:897` enqueues a `UserMessage(content=[TextContent(...)])` — no image support.

**Decision:** Sprint 6h₂ amends `harness.steer` and `harness.follow_up` to accept `images: list[ImageContent] | None = None`. When supplied, the enqueued `UserMessage.content` includes both `TextContent(text)` and one or more `ImageContent(...)` blocks. ADDITIVE — existing callers (`steer(text)`) continue to work.

### P-247 — `harness.set_thinking_level(level)` exists; `cycle_thinking_level` is a thin wrapper

Pi `session.cycleThinkingLevel()` (`rpc-mode.ts:571-577`):
- Returns the new `ThinkingLevel` or `null` when nothing to cycle (model has only `"off"`).

**Decision:** Aelix port:
```python
def cycle_thinking_level(self) -> str | None:
    """Pi parity: session.cycleThinkingLevel.

    Rotates through ``get_supported_thinking_levels(current_model)``.
    Returns the new level (persisted via :meth:`set_thinking_level`) or
    ``None`` when the model supports only one level (typically ``"off"``).
    """
    model = self.current_model
    if model is None:
        return None
    levels = get_supported_thinking_levels(model)
    if len(levels) <= 1:
        return None
    current = self._state.thinking_level or "off"
    idx = levels.index(current) if current in levels else 0
    next_level = levels[(idx + 1) % len(levels)]
    await self.set_thinking_level(next_level)  # persists + emits
    return next_level
```

Note: `set_thinking_level` is async in Aelix; `cycle_thinking_level` therefore becomes `async def`. The RPC handler awaits it.

### P-248 — Steering/follow_up mode setters missing — read-only properties exist

Aelix already has `_state.steering_mode` / `_state.follow_up_mode` fields and read-only properties at `core.py:689,697`. Missing: setters.

**Decision:** Add `harness.set_steering_mode(mode)` and `harness.set_follow_up_mode(mode)`. Each:
1. Validates `mode in ("all", "one-at-a-time")` → ValueError otherwise (Pi parity — Pi types narrow at compile time; Aelix does runtime check).
2. Updates `_state.<field>` AND the corresponding `_MessageQueue.mode` (the queue dispatcher needs the new mode to drain correctly).
3. NO event emission — Pi `setSteeringMode` is a state setter with no event (P-4 setter-no-emit rule).

### P-249 — `auto_compaction_enabled` and `auto_retry_enabled` state fields missing

Pi `session.setAutoCompactionEnabled(enabled)` + `session.setAutoRetryEnabled(enabled)` (rpc-mode.ts:603-617) toggle session-level flags consumed by the harness's auto-compaction trigger + retry loop.

Aelix has compaction (Sprint 4b) but NO auto-compaction trigger AND no retry loop. The state fields don't exist either.

**Decision:** Sprint 6h₂ adds:
- `_state.auto_compaction_enabled: bool = True` (Pi default — fixture `pi_rpc_mode_734e08e.json` confirms)
- `_state.auto_retry_enabled: bool = True`
- `harness.set_auto_compaction_enabled(enabled: bool) -> None`
- `harness.set_auto_retry_enabled(enabled: bool) -> None`

These are **state-only setters** — the auto-compaction trigger and retry loop themselves are Sprint 6h₃+ work. Sprint 6h₂'s `_handle_get_state` ALREADY reports `auto_compaction_enabled` (per Sprint 6d's RpcSessionState shape); Sprint 6h₂ wires the source field so it reflects real state instead of hardcoded `True`.

### P-250 — `abort_retry` and `abort_bash` are best-effort given the underlying machinery isn't ported

Pi `session.abortRetry()` (rpc-mode.ts:619-622) sets a retry-cancellation flag the agent-harness retry loop polls. Pi `session.abortBash()` (rpc-mode.ts:632-635) cancels the in-flight bash invocation.

Aelix has neither a retry loop nor a bash cancellation token. Honest decision:
- `harness.abort_retry()` sets `_state.retry_aborted: bool = True` flag — no-op visible side effect for now, but future-proof for the Sprint 6h₃+ retry loop port. Returns success.
- `harness.abort_bash()` sets `_state.bash_aborted: bool = True` flag — Sprint 5b bash tool can poll this in a follow-up sprint (or via Sprint 6h₃ cancellation token threading). Returns success.

This is documented as carry-forward in ADR-0072.

### P-251 — `steer`/`follow_up` `images` argument requires `ImageContent` import

`ImageContent` lives in `aelix_ai.messages`. Sprint 6h₂ amends the harness imports.

### P-252 — `_handle_get_state` `auto_compaction_enabled` source fix

Sprint 6d `_handle_get_state` returns `auto_compaction_enabled=True` hardcoded (Sprint 6f W6 P-118 fix used public accessor). After Sprint 6h₂, the state field exists, so the handler reads `harness.auto_compaction_enabled` (NEW public property).

### P-253 — Queue mode change must propagate to existing queue instances

`_MessageQueue` has a `mode` field set at construction (`core.py:578-579`). Sprint 6h₂ setters MUST mutate the queue's `mode` field too — otherwise the queue keeps draining with the old mode. Add `_MessageQueue.set_mode(mode)` helper (if missing).

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `harness/core.py` AMEND — `steer`/`follow_up` accept `images`, add 7 new methods + 4 state fields + 1 public property | ~180 | ~140 |
| `harness/core.py::_MessageQueue.set_mode` helper if missing | ~10 | ~20 |
| `aelix_ai/models.py` (USE existing `get_supported_thinking_levels`) | 0 | 0 |
| `rpc/rpc_mode.py` AMEND — 9 new handlers + drop 9 from DEFERRED + add 9 to SUPPORTED | ~140 | ~180 |
| Pi parity closure pin (`test_phase_4_9_strict_superset.py`) | — | ~100 |
| Sprint 6d closure pin update (DEFERRED 16→7, SUPPORTED 13→22) | — | ~10 |
| Sprint 6f closure pin update (same counts) | — | ~10 |
| **Totals** | **~330** | **~460** |

**Total ~790 LOC** — small focused sprint.

### NOT in scope (deferred per §J)

- **5 session tree commands** (switch_session / fork / clone / get_fork_messages / get_last_assistant_text) — Sprint 6h₃
- **2 session inspection commands** (get_session_stats / export_html) — Sprint 6h₃
- **Pi `agent-harness.ts` retry loop port** — `abort_retry` is a state-flag setter only
- **Pi `bash-executor` cancellation token threading** — `abort_bash` is a state-flag setter only (Sprint 5b bash tool to honor the flag in a follow-up)
- **`image-models.ts`** — Sprint 6h₃
- **Typed `Model.compat` discriminated union** — Sprint 6h₃

---

## §B — `harness/core.py` AMEND

### B.1 `steer` / `follow_up` accept `images`

```python
async def steer(
    self,
    text: str,
    images: list[ImageContent] | None = None,
) -> None:
    """Pi parity: ``session.steer(message, images)``.

    Sprint 6h₂ (P-246): accept optional ``images`` parameter. When
    supplied, the enqueued ``UserMessage.content`` contains both the
    ``TextContent(text)`` and the supplied ``ImageContent`` blocks.
    Existing callers passing only ``text`` continue to work.
    """
    content: list[Any] = [TextContent(text=text)]
    if images:
        content.extend(images)
    self._steering_queue.enqueue(UserMessage(content=content))
    await self._emit_queue_update()
```

Same pattern for `follow_up`. Update imports to include `ImageContent`.

### B.2 `cycle_thinking_level`

```python
async def cycle_thinking_level(self) -> str | None:
    """Pi parity: ``session.cycleThinkingLevel`` (``rpc-mode.ts:571-577``).

    Rotates through the supported thinking levels for the current model.
    Returns the new level after rotation, or ``None`` if the model has
    no levels to cycle (only ``"off"`` supported).
    """
    from aelix_ai.models import get_supported_thinking_levels

    model = self.current_model
    if model is None:
        return None
    levels = get_supported_thinking_levels(model)
    if len(levels) <= 1:
        return None
    current = self._state.thinking_level or "off"
    idx = levels.index(current) if current in levels else 0
    next_level = levels[(idx + 1) % len(levels)]
    await self.set_thinking_level(next_level)
    return next_level
```

### B.3 Mode setters

```python
def set_steering_mode(self, mode: str) -> None:
    """Pi parity: ``session.setSteeringMode``. P-4 setter-no-emit."""
    if mode not in ("all", "one-at-a-time"):
        raise ValueError(
            f"steering_mode must be 'all' or 'one-at-a-time', got {mode!r}"
        )
    self._state.steering_mode = mode  # type: ignore[assignment]
    self._steering_queue.set_mode(mode)


def set_follow_up_mode(self, mode: str) -> None:
    """Pi parity: ``session.setFollowUpMode``. P-4 setter-no-emit."""
    if mode not in ("all", "one-at-a-time"):
        raise ValueError(
            f"follow_up_mode must be 'all' or 'one-at-a-time', got {mode!r}"
        )
    self._state.follow_up_mode = mode  # type: ignore[assignment]
    self._follow_up_queue.set_mode(mode)
```

`_MessageQueue.set_mode(mode)`:
```python
def set_mode(self, mode: str) -> None:
    """Pi parity: queue mode mutator. No emit (setter-no-emit P-4)."""
    self.mode = mode  # type: ignore[assignment]
```

### B.4 Auto-mode setters + state fields

Add to `AgentState`:
```python
auto_compaction_enabled: bool = True
auto_retry_enabled: bool = True
retry_aborted: bool = False  # toggled by abort_retry
bash_aborted: bool = False  # toggled by abort_bash
```

Harness methods:
```python
def set_auto_compaction_enabled(self, enabled: bool) -> None:
    """Pi parity: ``session.setAutoCompactionEnabled``."""
    self._state.auto_compaction_enabled = bool(enabled)


def set_auto_retry_enabled(self, enabled: bool) -> None:
    """Pi parity: ``session.setAutoRetryEnabled``."""
    self._state.auto_retry_enabled = bool(enabled)


def abort_retry(self) -> None:
    """Pi parity: ``session.abortRetry``.

    Sprint 6h₂ (P-250): the Aelix retry loop is not yet ported; this
    setter persists the cancel intent for the future Sprint 6h₃+ loop.
    """
    self._state.retry_aborted = True


def abort_bash(self) -> None:
    """Pi parity: ``session.abortBash``.

    Sprint 6h₂ (P-250): the Sprint 5b bash tool does not yet honor a
    cancellation token; this setter persists the cancel intent so a
    future bash hardening sprint can poll the flag.
    """
    self._state.bash_aborted = True
```

### B.5 Public `auto_compaction_enabled` property (P-252)

```python
@property
def auto_compaction_enabled(self) -> bool:
    """Pi parity: ``session.autoCompactionEnabled``."""
    return self._state.auto_compaction_enabled
```

Update `_handle_get_state` to read `harness.auto_compaction_enabled` (replacing the hardcoded `True`).

---

## §C — `rpc/rpc_mode.py` AMEND

### C.1 Drop 9 entries from `DEFERRED_COMMANDS` + add 9 to `SUPPORTED_COMMANDS`

```python
DEFERRED_COMMANDS: dict[str, str] = {
    # Sprint 6h₂ (ADR-0071, P-245~P-252) wired 9 commands to the
    # harness — entries below shrink to the 7 session-tree + session-
    # inspection commands deferred to Sprint 6h₃.
    "get_session_stats": "ADR-0072 — Sprint 6h₃ session inspection",
    "export_html": "ADR-0072 — Sprint 6h₃ session inspection",
    "switch_session": "ADR-0072 — Sprint 6h₃ session tree navigation",
    "fork": "ADR-0072 — Sprint 6h₃ session tree navigation",
    "clone": "ADR-0072 — Sprint 6h₃ session tree navigation",
    "get_fork_messages": "ADR-0072 — Sprint 6h₃ session tree navigation",
    "get_last_assistant_text": "ADR-0072 — Sprint 6h₃ session tree navigation",
}

SUPPORTED_COMMANDS: frozenset[str] = frozenset({
    "prompt", "abort", "new_session",
    "get_state", "get_messages", "compact", "bash",
    "set_thinking_level", "set_session_name",
    "set_model", "cycle_model", "get_available_models",
    "get_commands",
    # Sprint 6h₂ additions:
    "steer", "follow_up",
    "cycle_thinking_level",
    "set_steering_mode", "set_follow_up_mode",
    "set_auto_compaction", "set_auto_retry", "abort_retry",
    "abort_bash",
})
```

### C.2 9 new handlers

Each handler mirrors Pi `rpc-mode.ts:528-635` line-by-line. Skeleton:

```python
async def _handle_steer(harness: AgentHarness, cmd: RpcCommandSteer) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:528-531``."""
    images = _decode_images(cmd.images)  # Sprint 6h₂ helper — decode RPC ImageContent dicts
    await harness.steer(cmd.message, images=images)
    return RpcSuccessResponse(id=cmd.id, command="steer")


async def _handle_follow_up(harness: AgentHarness, cmd: RpcCommandFollowUp) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:533-536``."""
    images = _decode_images(cmd.images)
    await harness.follow_up(cmd.message, images=images)
    return RpcSuccessResponse(id=cmd.id, command="follow_up")


async def _handle_cycle_thinking_level(harness, cmd) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:571-577``."""
    level = await harness.cycle_thinking_level()
    if level is None:
        return RpcSuccessResponse(id=cmd.id, command="cycle_thinking_level", data=None)
    return RpcSuccessResponse(
        id=cmd.id, command="cycle_thinking_level", data={"level": level},
    )


async def _handle_set_steering_mode(harness, cmd) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:585-588``."""
    try:
        harness.set_steering_mode(cmd.mode)
    except ValueError as exc:
        return RpcErrorResponse(id=cmd.id, command="set_steering_mode", error=str(exc))
    return RpcSuccessResponse(id=cmd.id, command="set_steering_mode")


# Symmetric for set_follow_up_mode, set_auto_compaction, set_auto_retry,
# abort_retry, abort_bash — each one is 3-5 lines.
```

`_decode_images(payload)` is a 5-line helper that converts the RPC `images` wire shape (`list[dict]`) to `list[ImageContent]`.

### C.3 Update `SUPPORTED_HANDLERS` dispatcher

Add the 9 handlers to the dispatcher table.

### C.4 Update `_handle_get_state` to read `harness.auto_compaction_enabled`

```python
# Sprint 6h₂ (P-252): real source instead of hardcoded True.
auto_compaction_enabled = harness.auto_compaction_enabled
```

---

## §D — Tests (binding plan, ~460 LOC)

### Unit
- `tests/harness/test_harness_steer_follow_up_images.py` (~80 LOC) — `steer`/`follow_up` enqueue with text-only + with images + None images; queue update emitted.
- `tests/harness/test_harness_cycle_thinking_level.py` (~80 LOC) — model with `off` only → None; model with full 6 levels → rotates correctly; index wraps; updates `state.thinking_level`.
- `tests/harness/test_harness_mode_setters.py` (~60 LOC) — `set_steering_mode("all")` updates `_state.steering_mode` + `_steering_queue.mode`; invalid mode raises ValueError; same for `set_follow_up_mode`.
- `tests/harness/test_harness_auto_modes.py` (~60 LOC) — `set_auto_compaction_enabled(False)` updates state + public property; `set_auto_retry_enabled` similarly; `abort_retry` sets `_state.retry_aborted=True`; `abort_bash` sets `_state.bash_aborted=True`.

### RPC handler integration
- `tests/rpc/test_rpc_mode_steer_follow_up.py` (~50 LOC) — full RPC round-trip with stub harness.
- `tests/rpc/test_rpc_mode_cycle_thinking_level.py` (~40 LOC) — happy path + null data when nothing to cycle.
- `tests/rpc/test_rpc_mode_mode_setters.py` (~40 LOC) — both setters + invalid mode error envelope.
- `tests/rpc/test_rpc_mode_auto_and_abort.py` (~50 LOC) — 4 commands (set_auto_compaction / set_auto_retry / abort_retry / abort_bash).

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_9_strict_superset.py` (~100 LOC):
  - Assert `DEFERRED_COMMANDS` 7 entries; `SUPPORTED_COMMANDS` 22; total 29.
  - Assert all 9 new commands in `SUPPORTED_COMMANDS` and dispatcher table.
  - Assert `harness.cycle_thinking_level` returns expected per fixture algorithm.
  - Assert `set_steering_mode("invalid")` raises ValueError (Pi: TS narrow type; Aelix runtime check).
  - Assert RPC `cycle_thinking_level` response shape: `{level: ...} | null`.
  - Assert `_handle_get_state` reflects `auto_compaction_enabled` real state (P-252).

### Sprint 6d + 6f closure pin updates
- `tests/pi_parity/test_phase_4_4_strict_superset.py` — update count assertions: DEFERRED 16→7, SUPPORTED 13→22.
- `tests/pi_parity/test_phase_4_6_strict_superset.py` — same updates.

---

## §E — ADRs

### Amend
- **ADR-0034** — add row: "Sprint 6h₂ wired 9 RPC commands (steer/follow_up with images, cycle_thinking_level, queue mode setters, auto-mode flags, abort_retry/abort_bash). DEFERRED 16→7, SUPPORTED 13→22."

### NEW
- **ADR-0071** — `0071-9-rpc-commands-and-harness-setters.md` — Pi parity port of 9 handlers + harness state additions (`auto_compaction_enabled`, `auto_retry_enabled`, `retry_aborted`, `bash_aborted`) + mode setters + cycle algorithm.
- **ADR-0072** — `0072-phase-4-9-strict-superset-closure.md` — closure pin. Roster: P-245 ~ P-253. Sprint 6h₃ carry-forward (5 session tree + 2 session inspection commands, retry loop port, bash cancellation token threading).

### README
Add 2 new ADR rows + Sprint 6h₂ sub-table.

---

## §F — Sprint workflow (ADR-0032)

- W0 — research ✓ DONE
- W1 — this spec (binding)
- W2 — executor opus
- W3 — verification
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: harness — steer/follow_up images + cycle_thinking_level + mode setters (P-246/P-247/P-248)`
2. `feat: harness — auto_compaction/auto_retry state + abort_retry/abort_bash flags (P-249/P-250) + _handle_get_state real source (P-252)`
3. `feat: rpc — 9 new handlers + dispatch wiring (P-245~P-253)`
4. `test: Sprint 6h₂ — closure pin + Sprint 6d/6f count updates + 7 new test files`
5. `docs: ADRs 0034 amend + NEW 0071/0072 + README + spec — Phase 4.9 closure`

---

## §G — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1491 baseline + ~50 new ≈ 1541+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Sprint 6d/6f closure pins | DEFERRED 16→7, SUPPORTED 13→22 |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
