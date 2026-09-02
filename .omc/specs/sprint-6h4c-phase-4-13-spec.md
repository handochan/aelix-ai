# Sprint 6h₄c · Phase 4.13 — `switch_session` / `fork` / `clone` wiring + Phase 4 RPC closure (BINDING SPEC)

**Status:** Binding (Architect READ-ONLY)
**Author:** Architect (Opus)
**Date:** 2026-05-21
**Pi pin (ADR-0034):** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
**Top-level principle (binding):** **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

Sprint 6h₄c is the **CLOSURE** sprint for the Phase 4 RPC roster. We wire the 3 last DEFERRED session-tree commands (`switch_session` / `fork` / `clone`) on top of the 6h₄b `AgentSessionRuntime` foundation (ADR-0077/0078). Counts move **SUPPORTED 26→29 / DEFERRED 3→0 / total 29** — full Pi parity for `RpcCommand`. The 4 stubbed replace APIs on `AgentSessionRuntime` (`switch_session` / `new_session` / `fork` / `import_from_jsonl` — currently `NotImplementedError`) are filled with real bodies routed through `JsonlSessionRepo` (Aelix is persisted-only); the test-only `_apply_for_test` seam from 6h₄b is removed and its asserting tests migrate to the real public API. Sprint 6d's `_handle_new_session` stub (which currently rejects `parent_session` via `RpcErrorResponse` — `rpc_mode.py:309-347`) is replaced by a runtime-host route so `parent_session` lineage finally lands.

---

## §0 — W0 INVESTIGATION FINDINGS

P-numbering continues from P-322 (last 6h₄b W5 finding) → **P-323 ~ P-331**.

### P-323 — Pi line-drift discovery (mirror of 6h₄a P-293)

ADR-0076 carry-forward roster (Sprint 6h₄a) estimated Pi case sites for the 3 deferred commands at `rpc-mode.ts:528-557`. W0 verification at SHA `734e08e` puts the actual sites at `:563-589` — **~35 lines off**. ADR-0078's carry-forward roster (lines 67-78) inherited the drift: `switch_session` was cited as `:566`, `fork` as `:574`, `clone` as `:586` — wrong by 3 lines on each. W0 verified at `/tmp/pi-6h4c/rpc-mode.ts` (offset 540-590) confirms the actual case sites are:

- `switch_session` — `rpc-mode.ts:563-569` (Pi handler body 7 lines)
- `fork` — `rpc-mode.ts:571-577` (Pi handler body 7 lines)
- `clone` — `rpc-mode.ts:579-589` (Pi handler body 11 lines)

The 5 read-only session commands that ADR-0076's roster also estimated (`get_fork_messages` / `get_last_assistant_text` were 591-594 / 596-599 — already corrected in ADR-0075) corroborate the drift pattern: Pi `rpc-mode.ts` grew between the ADR-0072 / ADR-0074 estimates and the SHA `734e08e` snapshot. **Forward-fix: ADR-0076 amend in 6h₄c W6 records the line-citation supersession and ADR-0078 amend rewrites the carry-forward roster lines from `566`/`574`/`586` → `563-569`/`571-577`/`579-589`** so future archaeology grep returns clean.

### P-324 — Runtime constructor extension (`repo` + `fs`)

Sprint 6h₄b shipped `AgentSessionRuntime.__init__(harness, create_harness, *, diagnostics, model_fallback_message)` at `runtime/agent_session_runtime.py:58-89`. The replace bodies need:

1. `JsonlSessionRepo.open(metadata)` (`jsonl_repo.py:164`) for `switch_session` (load by file path).
2. `JsonlSessionRepo.create(JsonlSessionCreateOptions(cwd, parent_session_path, id=None))` (`jsonl_repo.py:136`) for `new_session`.
3. `JsonlSessionRepo.fork(source, ForkOptions(cwd, entry_id, position, parent_session_path))` (`jsonl_repo.py:238`) for `fork` / `clone`.
4. `load_jsonl_session_metadata(fs, path)` (`jsonl_storage.py:229`) to build the `JsonlSessionMetadata` from a path that arrives over the wire.

The runtime CANNOT reach into `harness._options` to find a repo because Pi's design holds the repo in `_services` (Pi `AgentSessionServices`) which Aelix folded into the runtime via P-302. **Decision (BINDING):** extend the runtime constructor with explicit `repo: JsonlSessionRepo` and `fs: FileSystem` keyword parameters. Both are stored as `self._repo` / `self._fs` and consumed by the 4 replace bodies. The constructor signature becomes:

```python
def __init__(
    self,
    harness: AgentHarness,
    create_harness: HarnessFactory,
    *,
    repo: JsonlSessionRepo,                  # NEW (P-324)
    fs: FileSystem,                          # NEW (P-324)
    diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
    model_fallback_message: str | None = None,
) -> None:
```

`repo` and `fs` are **keyword-only and required** (no default). The 6h₄b unit tests, `_make_passthrough_runtime`, and the existing closure pin all break loud until updated — this is intentional (compile-time enforcement of the new contract).

**Why required, not optional:** an `Optional` `repo=None` shape would silently re-raise the existing `NotImplementedError` from the replace bodies, masking the wiring gap. Per ADR-0078 carry-forward roster line 60-66, 6h₄c "wires the matching RPC handlers + implements the 4 stubbed methods" — the contract is that 6h₄c CLOSES the foundation, so the bodies become reachable and a missing repo means broken construction, not silent stubbing.

**Callsite migration (P-309 ripple):** `_make_passthrough_runtime(harness, harness_factory)` at `rpc_mode.py:1399-1429` becomes `_make_passthrough_runtime(harness, harness_factory, repo, fs)`. `run_rpc_mode` signature gains `repo: JsonlSessionRepo | None = None` and `fs: FileSystem | None = None` — when both are `None` AND `runtime_host is None`, the passthrough constructor uses a default `JsonlSessionRepo()` + `LocalFileSystem()` (Pi default cwd-rooted location). When one is provided, the other must be too; otherwise raise `RuntimeError("repo and fs must be provided together")`. The 7 shim tests in `tests/rpc/test_rpc_mode_runtime_shim.py:107-253` migrate by passing `repo=JsonlSessionRepo(fs=LocalFileSystem())` to `_make_passthrough_runtime`. The 6h₄b unit suite (`tests/runtime/test_agent_session_runtime.py:67-145`) migrates by adding `repo=JsonlSessionRepo(fs=LocalFileSystem())` to every `AgentSessionRuntime(...)` construction.

### P-325 — Runtime method bodies (4 stubs → real implementations)

The Pi method bodies are at `/tmp/pi-6h4c/agent-session-runtime.ts`:

| Pi method | Pi lines | Aelix Pi-citation | Notes |
|---|---|---|---|
| `switchSession` | `175-198` | already in `agent_session_runtime.py:233` docstring | uses `SessionManager.open(path)` → Aelix uses `repo.open(load_jsonl_session_metadata(fs, path))` |
| `newSession` | `200-232` | already in `:245` docstring | uses `SessionManager.create(cwd, sessionDir)` → Aelix uses `repo.create(JsonlSessionCreateOptions(cwd=current_cwd, parent_session_path=options.parent_session))` |
| `fork` | `234-320` | already in `:257` docstring | 3 branches (top + persisted + in-memory); Aelix collapses to persisted-only via `repo.fork(source_metadata, ForkOptions(cwd, entry_id, position))` |
| `importFromJsonl` | `329-364` | already in `:269` docstring | **STAYS STUBBED** in 6h₄c — no RPC command in `RpcCommand` union (`rpc_types.py:309-340`) maps to it. Carry-forward per ADR-0080. |

**`switch_session` body (Pi `:175-198`):**

```python
async def switch_session(
    self,
    path: str,
    *,
    options: dict | None = None,
) -> RuntimeReplaceResult:
    """Pi parity: ``switchSession`` (``agent-session-runtime.ts:175-198``).

    Sprint 6h₄c (ADR-0079): real body. Pi 4-step waveform:
      1. ``emit_before_switch()`` → bail if cancelled (P-308 stub
         returns False in 6h₄c — real cancel hooks deferred to ADR-0080).
      2. Resolve target session via ``repo.open(load_jsonl_session_metadata(...))``.
      3. ``_finish_session_replacement(new_session)`` (LIFO: dispose OLD
         harness → ``_apply`` constructs NEW harness via factory →
         registered ``rebind_session`` callback fires).
      4. Return ``RuntimeReplaceResult(cancelled=False)``.
    """
    if await self._emit_before_switch():
        return RuntimeReplaceResult(cancelled=True)
    try:
        metadata = await load_jsonl_session_metadata(self._fs, path)
        new_session = await self._repo.open(metadata)
    except SessionError:
        raise   # propagated to RPC handler — surfaces as RpcErrorResponse
    await self._finish_session_replacement(new_session)
    return RuntimeReplaceResult(cancelled=False)
```

**Pi-divergence acknowledged:** Pi's `assertSessionCwdExists` (`:186`) is omitted — that helper is part of `session-cwd.ts` (Pi-internal) and gates Pi's runtime on cwd mismatch. Aelix surfaces the equivalent error implicitly through `SessionError("storage", ...)` when `repo.open` fails to find the file (`jsonl_repo.py:170-178`). Per ADR-0078 carry-forward roster + this spec's explicit deferral list (§0 P-331 / ADR-0080), `assertSessionCwdExists` Pi parity is **deferred to Sprint 6h₅+**.

**`new_session` body (Pi `:200-232`) — REPLACES Sprint 6d stub at `rpc_mode.py:309-347`:**

```python
async def new_session(
    self,
    *,
    parent_session: str | None = None,
) -> RuntimeReplaceResult:
    """Pi parity: ``newSession`` (``agent-session-runtime.ts:200-232``).

    Sprint 6h₄c (ADR-0079): real body. Replaces the Sprint 6d stub at
    ``rpc_mode.py:309-347`` which rejected ``parent_session`` with an
    ``RpcErrorResponse``. Pi waveform:
      1. ``emit_before_switch()`` → bail if cancelled.
      2. ``repo.create(JsonlSessionCreateOptions(cwd=current_cwd,
         parent_session_path=parent_session))`` builds a fresh session
         under the current cwd, lineage-linked to ``parent_session`` if
         supplied (Pi parity ``:213-215``).
      3. ``_finish_session_replacement(new_session)``.
      4. Return ``RuntimeReplaceResult(cancelled=False)``.

    Aelix omits Pi's optional ``setup`` 2-stage callback (Pi
    ``:226-229``) — carry-forward per ADR-0080 P-314.
    """
    if await self._emit_before_switch():
        return RuntimeReplaceResult(cancelled=True)
    cwd = self.cwd
    if cwd is None:
        raise RuntimeError("new_session requires the current harness session to have a cwd")
    new_session = await self._repo.create(
        JsonlSessionCreateOptions(cwd=cwd, parent_session_path=parent_session)
    )
    await self._finish_session_replacement(new_session)
    return RuntimeReplaceResult(cancelled=False)
```

**Aelix-additive simplification:** Pi takes options dict (`{parentSession?, setup?, withSession?}`). Aelix exposes ONLY `parent_session` as a keyword for 6h₄c. `setup` (Pi `:202`) + `withSession` (Pi `:203`) defer per ADR-0080 (P-314 — `with_session` 2-stage callback). The new keyword shape is Pi-byte-equivalent for the only field the wire actually carries (`RpcCommandNewSession.parent_session` at `rpc_types.py:96`).

**`fork` body (Pi `:234-320`):**

```python
async def fork(
    self,
    entry_id: str,
    *,
    position: ForkPosition = "before",
) -> RuntimeReplaceResult:
    """Pi parity: ``fork`` (``agent-session-runtime.ts:234-320``).

    Sprint 6h₄c (ADR-0079): real body. Pi has 3 branches (top + persisted +
    in-memory). Aelix is persisted-only — the in-memory branch
    (``:303-319``) is dropped (P-325 SYNTHESIS). The remaining
    waveform:
      1. ``emit_before_fork()`` → bail if cancelled.
      2. Resolve ``selected_entry`` via ``session.get_entry(entry_id)``;
         raise ``ValueError("Invalid entry ID for forking")`` if missing
         (Pi parity ``:247``).
      3. Resolve ``target_leaf_id`` + optional ``selected_text``:
         - position=="at" → ``target_leaf_id = selected_entry.id``,
           ``selected_text = None``.
         - position=="before" → require ``selected_entry`` is a user
           message; ``target_leaf_id = selected_entry.parent_id``,
           ``selected_text = _extract_user_message_text(...)``.
      4. Resolve current session metadata for ``ForkOptions.cwd`` +
         ``parent_session_path``.
      5. ``new_session = await repo.fork(source_metadata,
         ForkOptions(cwd, entry_id=target_leaf_id, position="at",
         parent_session_path=current_session_path))``.
         ``position="at"`` is correct here because P-325 pre-computed
         the effective leaf via the Pi user-message walk above —
         passing it to ``ForkOptions`` as ``"at"`` mirrors Pi's
         ``createBranchedSession(targetLeafId)`` call at ``:285/:289/:307``.
      6. ``_finish_session_replacement(new_session)``.
      7. Return ``RuntimeReplaceResult(cancelled=False,
         selected_text=selected_text)``.
    """
    if await self._emit_before_fork():
        return RuntimeReplaceResult(cancelled=True)

    if self.session is None:
        raise RuntimeError("fork requires an active session")

    selected_entry = await self.session.get_entry(entry_id)
    if selected_entry is None:
        raise ValueError("Invalid entry ID for forking")

    selected_text: str | None = None
    if position == "at":
        target_leaf_id: str | None = selected_entry.id
    else:
        if selected_entry.type != "message" or selected_entry.message.role != "user":
            raise ValueError("Invalid entry ID for forking")
        target_leaf_id = selected_entry.parent_id
        selected_text = _extract_user_message_text(selected_entry.message.content)

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
    await self._finish_session_replacement(new_session)
    return RuntimeReplaceResult(cancelled=False, selected_text=selected_text)
```

**`_extract_user_message_text` helper (Pi `:49-58`):** module-private function in `agent_session_runtime.py` — mirrors Pi inline `extractUserMessageText` (a string-or-array-of-text-parts joiner). 6-line function — no separate module:

```python
def _extract_user_message_text(content: Any) -> str:
    """Pi parity: ``extractUserMessageText`` (``agent-session-runtime.ts:49-58``)."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        # Pi narrows on ``part.type === "text" && typeof part.text === "string"``.
        if getattr(part, "type", None) == "text":
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
```

### P-326 — 3 new RPC handlers + new arity class

Pi `rpc-mode.ts:563-589` shows the 3 handlers route to `runtimeHost.<method>` (NOT `session.<method>`). The existing Aelix `_SUPPORTED_HANDLERS_HARNESS_ONLY` (`rpc_mode.py:1294-1327`) and `_SUPPORTED_HANDLERS_HARNESS_REGISTRY` (`:1329-1336`) signatures both take `harness` as the first positional. The 3 new handlers take `runtime_host: AgentSessionRuntime` instead — a new arity class is required to keep type signatures honest.

**Decision (BINDING):** add **third** arity class `_SUPPORTED_HANDLERS_RUNTIME_HOST: dict[str, Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]]]` with the 3 new handlers. The `build_dispatch_table(model_registry)` factory at `rpc_mode.py:1346-1379` is extended to take a NEW required `runtime_host: AgentSessionRuntime` argument. The dispatch loop in `run_rpc_mode` (`rpc_mode.py:1700-1713`) reads `capture.harness` for the HARNESS_ONLY / HARNESS_REGISTRY handlers AND the same `runtime_host` for the 3 new handlers via a per-cmd binding closure (`_bind_runtime_host`).

```python
# NEW arity class — runtime_host + cmd
_SUPPORTED_HANDLERS_RUNTIME_HOST: dict[
    str, Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]]
] = {
    "switch_session": _handle_switch_session,
    "fork": _handle_fork,
    "clone": _handle_clone,
}

def _bind_runtime_host(
    handler: Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]],
    runtime_host: AgentSessionRuntime,
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Adapt a 2-arg ``(runtime_host, cmd)`` handler to the dispatch
    table's 2-arg ``(harness, cmd)`` shape by closing over ``runtime_host``.
    The ``harness`` positional is ignored (Pi parity — these handlers
    operate on the runtime, not the harness directly).
    """
    async def _adapted(_harness: Any, cmd: Any) -> RpcResponse:
        return await handler(runtime_host, cmd)
    return _adapted
```

`build_dispatch_table(model_registry, runtime_host)` adds:

```python
for cmd_type, runtime_handler in _SUPPORTED_HANDLERS_RUNTIME_HOST.items():
    table[cmd_type] = _bind_runtime_host(runtime_handler, runtime_host)
```

**Aelix-additive simplification vs Pi:** Pi handlers call `await rebindSession()` explicitly after success (`rpc-mode.ts:566`/`:574`/`:586`). Aelix 6h₄b runtime's `_finish_session_replacement` (`agent_session_runtime.py:196-209`) **ALREADY auto-invokes the registered `rebind_session` callback** as step 3 of the 3-step waveform. **Aelix handlers MUST NOT call rebind manually** — the runtime is the single source of truth. This is recorded as deliberate convergence in P-329.

### P-327 — Wire shape `selectedText` → `text` rename for `fork`

Pi `rpc-mode.ts:576` returns `success(id, "fork", { text: result.selectedText, cancelled: result.cancelled })`. The Pi return key on the wire is `text`, NOT `selectedText` — Pi remaps the internal `selectedText` field to the wire key `text`. Aelix mirrors:

```python
async def _handle_fork(
    runtime_host: AgentSessionRuntime,
    cmd: Any,  # ``RpcCommandFork``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:571-577`` (fork handler)."""
    try:
        result = await runtime_host.fork(cmd.entry_id)
    except ValueError as exc:
        return RpcErrorResponse(id=cmd.id, command="fork", error=str(exc))
    data: dict[str, Any] = {"cancelled": result.cancelled}
    if result.selected_text is not None:
        data["text"] = result.selected_text
    return RpcSuccessResponse(id=cmd.id, command="fork", data=data)
```

**Key-omission parity per Sprint 6h₄a P-298:** when `result.selected_text is None`, the `text` key is omitted entirely (mirrors Pi `JSON.stringify({text: undefined})` → `{cancelled: false}`). The pattern is identical to `_handle_get_last_assistant_text` at `rpc_mode.py:1240-1264`. Closure pin asserts: `await _handle_fork(...).to_json()["data"]` contains `"text"` IFF `selected_text is not None`.

### P-328 — `clone` leaf_id capture ordering (Pi `rpc-mode.ts:579-589`)

Pi line 580 captures `leafId` BEFORE the `runtimeHost.fork(leafId, {position: "at"})` call on `:584`. Order matters because once `fork` invokes `_finish_session_replacement`, the old harness is disposed — `session.sessionManager` becomes invalid. Pi pre-captures via `const leafId = session.sessionManager.getLeafId();` and errors out via `if (!leafId) return error(...)` BEFORE entering the fork waveform. Aelix mirrors verbatim:

```python
async def _handle_clone(
    runtime_host: AgentSessionRuntime,
    cmd: Any,  # ``RpcCommandClone``
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:579-589`` (clone handler).

    Pi 3-step waveform:
      1. Capture ``leaf_id`` BEFORE entering the fork waveform (P-328 —
         old session is disposed during fork; leaf must be captured against
         the LIVE session). The capture order matches Pi line 580 verbatim.
      2. Error envelope ``"Cannot clone session: no current entry selected"``
         when leaf_id is None (Pi parity ``:582``).
      3. Delegate to ``runtime_host.fork(leaf_id, position="at")`` — Pi
         line 584. The ``position="at"`` differentiates clone from fork:
         clone forks AT the current leaf (no user-message walk), fork
         walks BACK to the parent.
      4. Wire shape: ``{cancelled}`` — Pi DROPS ``selectedText`` for
         clone (Pi line 588 returns only ``{cancelled: result.cancelled}``).
    """
    session = runtime_host.session
    if session is None:
        return RpcErrorResponse(
            id=cmd.id, command="clone",
            error="Cannot clone session: no current entry selected",
        )
    leaf_id = await session.get_leaf_id()
    if leaf_id is None:
        return RpcErrorResponse(
            id=cmd.id, command="clone",
            error="Cannot clone session: no current entry selected",
        )
    try:
        result = await runtime_host.fork(leaf_id, position="at")
    except ValueError as exc:
        return RpcErrorResponse(id=cmd.id, command="clone", error=str(exc))
    return RpcSuccessResponse(
        id=cmd.id, command="clone", data={"cancelled": result.cancelled},
    )
```

**Aelix-divergence acknowledged + tested:** Pi `Session.get_leaf_id()` is sync (`session-manager.ts`); Aelix `Session.get_leaf_id()` is `async def` (`session.py:103-104`). The `await` is necessary; pre-capture ordering is preserved because the `await` resolves BEFORE the `runtime_host.fork(...)` call enters its replace waveform. Closure pin includes a test that asserts (via a spy on `session.get_leaf_id`) that the leaf-id resolution finishes before the OLD harness's `dispose()` is invoked.

### P-329 — Aelix handlers MUST NOT call rebind manually (deliberate convergence)

Pi `rpc-mode.ts:565-567` / `:573-575` / `:585-587` each contains:

```ts
if (!result.cancelled) {
    await rebindSession();
}
```

This is Pi belt-and-braces — Pi's `AgentSessionRuntime.finishSessionReplacement` (`agent-session-runtime.ts:166-173`) ALSO awaits `this.rebindSession?.(this.session)`. So Pi rebinds TWICE per successful replace (once in the runtime, once from the handler). In Aelix, the 6h₄b `_finish_session_replacement` at `agent_session_runtime.py:196-209` already auto-invokes the registered rebind callback. **Adding a second handler-side rebind would call the closure twice per replace** — breaking the closure-pin invariant from `tests/runtime/test_agent_session_runtime.py:294-310` (`test_apply_for_test_invokes_rebind_session_callback_exactly_once`).

**Decision (BINDING):** Aelix handlers DO NOT call rebind manually. The runtime is the single source. This is documented as deliberate convergence:

- In `_handle_switch_session` / `_handle_fork` / `_handle_clone` docstrings.
- In `agent_session_runtime.py` module docstring (P-329 paragraph).
- In `ADR-0079 §Consequences` as a Pi divergence rationale.

The Sprint 6h₄c closure pin (`tests/pi_parity/test_phase_4_13_strict_superset.py`) ADDS an assertion: after `await _handle_switch_session(runtime, cmd)`, the spy on `runtime._rebind_session` records exactly 1 await call (NOT 2).

**Antithesis & synthesis recorded in §H.**

### P-330 — Replace `_handle_new_session` Sprint 6d stub via `runtime_host.new_session`

Sprint 6d shipped `_handle_new_session` at `rpc_mode.py:309-347` with this body (key excerpt):

```python
if cmd.parent_session is not None:
    return RpcErrorResponse(
        id=cmd.id, command="new_session",
        error="parent_session lineage tracking deferred to Sprint 6f (ADR-0058)",
    )
if not harness.is_idle:
    await harness.abort()
    with contextlib.suppress(Exception):
        await harness.wait_for_idle()
return RpcSuccessResponse(
    id=cmd.id, command="new_session", data={"cancelled": False},
)
```

The `parent_session is not None` rejection branch + the bare-abort fallback both predate the runtime layer. With `runtime_host.new_session(parent_session=...)` real, the handler is reduced to a thin wrapper. **Migration:**

```python
async def _handle_new_session(
    runtime_host: AgentSessionRuntime,
    cmd: RpcCommandNewSession,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:277-282`` (new_session handler).

    Sprint 6h₄c (ADR-0079): replaces the Sprint 6d stub (which rejected
    ``parent_session`` with an RpcErrorResponse — old body at
    ``rpc_mode.py:309-347``) by routing through
    ``runtime_host.new_session(parent_session=cmd.parent_session)``.
    The Sprint 6d ``ADR-0058 — parent_session deferred`` carry-forward
    closes here.
    """
    result = await runtime_host.new_session(parent_session=cmd.parent_session)
    return RpcSuccessResponse(
        id=cmd.id, command="new_session", data={"cancelled": result.cancelled},
    )
```

**Handler arity change:** `_handle_new_session` MOVES from `_SUPPORTED_HANDLERS_HARNESS_ONLY` (where it takes `harness`) to `_SUPPORTED_HANDLERS_RUNTIME_HOST` (where it takes `runtime_host`). The 4-entry `_SUPPORTED_HANDLERS_RUNTIME_HOST` total is `new_session` + `switch_session` + `fork` + `clone`. The `_SUPPORTED_HANDLERS_HARNESS_ONLY` dict at `rpc_mode.py:1294-1327` REMOVES the `"new_session": _handle_new_session` line. The `parent_session is not None` rejection AND the bare abort fallback are both deleted (runtime's `_teardown_current` at `agent_session_runtime.py:166-183` already calls `await self._harness.dispose()` which handles the in-flight abort via `dispose()` at `harness/core.py:1961-1976`).

**Test migration:** any test in `tests/rpc/` asserting `_handle_new_session` rejects `parent_session` with an `RpcErrorResponse` MUST be migrated — the new contract is success with lineage-tracked session created via `repo.create(parent_session_path=...)`. Suite touch ~5-10 tests; explicit list emitted in W4 review.

### P-331 — Remove `_apply_for_test` test seam

Sprint 6h₄b shipped `_apply_for_test` at `agent_session_runtime.py:213-223` with the explicit comment "Sprint 6h₄c may remove this helper once `switch_session` lands" (line 220-221). **Decision (BINDING):** remove `_apply_for_test` in 6h₄c. The 6h₄b unit tests that drive the rebind seam through `_apply_for_test` (10 tests at `tests/runtime/test_agent_session_runtime.py:226-392`) migrate to drive `switch_session` via the real public API. The migration substitutes the test seam with:

```python
# OLD (6h₄b):
await runtime._apply_for_test(new_sess)

# NEW (6h₄c):
# write new_sess to disk via repo, then call switch_session
path = ...  # JSONL file path of new_sess
await runtime.switch_session(path)
```

For tests that need to inject a synthetic `new_session` (e.g. `test_apply_for_test_invokes_factory_with_new_session`), the migration uses an in-memory `MemoryRepo` test double that returns the pre-built `Session` directly from `repo.open(metadata)`. Closure pin: `assert not hasattr(AgentSessionRuntime, "_apply_for_test")` — the public-API regression caught immediately if any 6h₄c PR re-adds it.

---

## §A — Scope (binding) — LOC table

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_agent_core/runtime/agent_session_runtime.py` AMEND — constructor +`repo`/`fs` + 4 method bodies (switch/new/fork — `import_from_jsonl` stays stubbed) + `_extract_user_message_text` helper + REMOVE `_apply_for_test` | ~140 | — |
| `aelix_agent_core/runtime/_types.py` UNCHANGED (RuntimeReplaceResult already supports `selected_text`) | 0 | — |
| `aelix_coding_agent/rpc/rpc_mode.py` AMEND — 3 new handlers + REPLACE `_handle_new_session` + `_SUPPORTED_HANDLERS_RUNTIME_HOST` + `_bind_runtime_host` + `build_dispatch_table(runtime_host)` + `_make_passthrough_runtime(harness, factory, repo, fs)` + `run_rpc_mode(..., repo, fs)` + DEFERRED_COMMANDS 3→0 + docstring closure note | ~180 | — |
| `tests/runtime/test_agent_session_runtime.py` MIGRATE — drop `_apply_for_test` usage; drive replace via `switch_session` over `MemoryRepo` test double | — | ~80 (net delta after migrate) |
| `tests/runtime/test_agent_session_runtime_replace_apis.py` (NEW) — real `switch_session` / `new_session` / `fork` over tmp-path `JsonlSessionRepo` | — | ~120 |
| `tests/rpc/test_rpc_mode_runtime_shim.py` AMEND — add `repo` + `fs` to the 7 existing tests | — | ~30 (delta) |
| `tests/rpc/test_rpc_mode_switch_fork_clone.py` (NEW) — 3 handler integration over `JsonlSessionRepo` tmp_path + rebind callback invocation count + `clone` leaf-id pre-capture ordering | — | ~140 |
| `tests/rpc/test_rpc_mode_new_session_parent.py` (NEW) — `_handle_new_session` parent_session now success (regression for Sprint 6d stub removal P-330) | — | ~40 |
| `tests/pi_parity/test_phase_4_13_strict_superset.py` (NEW closure pin) | — | ~180 |
| `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json` (NEW fixture) | — | — |
| **Totals** | **~320 prod** | **~590 test** (~80 of that is delta-migrate, ~30 is amend) |

LOC fits the 350-450 prod / 250-350 test envelope. Net new test coverage ~390 LOC after subtracting migrate-deltas; total ~910 LOC. **Test-heavy by design** — Sprint 6h₄c is the Phase 4 RPC closure; every wire shape + capture-ordering invariant for the 3 new handlers + the `new_session` parent_session regression + the `_apply_for_test` removal MUST be locked.

### NOT in scope (deferred to Sprint 6h₅+ per ADR-0080)

- **`import_from_jsonl` real body** — no `import_from_jsonl` RPC command in the Pi `RpcCommand` union (verified `rpc_types.py:309-340`). Stays as `NotImplementedError` stub; ADR-0080 documents that the method is implementable but unreachable from the wire until Pi adds a command (Pi reality: the call site is the TUI `/import` command which doesn't go through RPC).
- `commandContextActions` wiring inside `rebind_session` closure (Pi `:315-345`: `waitForIdle`, `newSession`, `fork`, `navigateTree`, `switchSession`, `reload`) — defer per ADR-0078 P-308 fill-in target (Sprint 6h₅+).
- P-307 `session_shutdown` extension event emit from `AgentHarness.dispose()` — defer per ADR-0078 (cumulative).
- P-308 real `session_before_switch` / `session_before_fork` extension cancel hooks — defer per ADR-0078 (cumulative).
- **P-313 `HarnessFactory` 4-field refresh — CONFIRMED NOT NEEDED**, drop from carry-forward. Aelix harness rebuild encapsulates services + diagnostics + model_fallback_message INSIDE the new harness construction (factory closure carries the application's template options). The Pi 4-field `apply()` shape was an artifact of Pi's session-swap pattern; harness-rebuild makes it redundant. ADR-0080 records the explicit drop with rationale.
- P-314 `with_session` 2-stage callback — defer (no RPC wire surface today).
- P-315 optional-cb signatures — defer (no caller depends on optional-clear today).
- `assertSessionCwdExists` Pi parity — defer (surface as `SessionError("storage")` when `repo.open` fails per `jsonl_repo.py:170-178`).
- `previousSessionFile` / `sessionStartEvent` tracking — defer (no event surface yet).
- Pi `forkFrom` cross-cwd import — defer (no RPC command wires).

---

## §B — `runtime/agent_session_runtime.py` AMEND

### B.1 Imports + new constructor

```python
# NEW imports at top of agent_session_runtime.py
from aelix_agent_core.session.fs import FileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata
from aelix_agent_core.session.repo_utils import ForkOptions, ForkPosition
from aelix_agent_core.session.storage import SessionError
```

Constructor extension per P-324 (NEW required keyword-only `repo` + `fs`):

```python
def __init__(
    self,
    harness: AgentHarness,
    create_harness: HarnessFactory,
    *,
    repo: JsonlSessionRepo,                   # NEW (P-324)
    fs: FileSystem,                           # NEW (P-324)
    diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
    model_fallback_message: str | None = None,
) -> None:
    # ... unchanged body, then:
    self._repo = repo
    self._fs = fs
```

### B.2 Module-private helper (Pi `:49-58`)

```python
def _extract_user_message_text(content: Any) -> str:
    """Pi parity: ``extractUserMessageText`` (``agent-session-runtime.ts:49-58``)."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if getattr(part, "type", None) == "text":
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)
```

### B.3 `switch_session` body (replaces stub at `:227-240`)

Per P-325 — uses `load_jsonl_session_metadata(self._fs, path)` + `self._repo.open(metadata)` + `_finish_session_replacement`. SessionError propagates to the RPC handler which wraps it via the existing `_handle_command` exception envelope (`rpc_mode.py:1465-1472`). Pi docstring citation `:175-198` preserved.

### B.4 `new_session` body (replaces stub at `:242-249`)

Per P-325 + P-330 — accepts `parent_session: str | None` keyword (NOT the Pi options dict). Uses `self._repo.create(JsonlSessionCreateOptions(cwd=self.cwd, parent_session_path=parent_session))`. Raises `RuntimeError` when `self.cwd is None`. Pi docstring citation `:200-232` preserved.

### B.5 `fork` body (replaces stub at `:251-261`)

Per P-325 — drops Pi in-memory branch (`:303-319`). Aelix is persisted-only — `repo.fork(source, ForkOptions(cwd, entry_id=target_leaf_id, position="at", parent_session_path=metadata.path))`. The 3-Pi-branch logic collapses because Aelix routes everything through `JsonlSessionRepo.fork` (`jsonl_repo.py:238`) which internally branches on `target_leaf_id is None` (`get_entries_to_fork` at `repo_utils.py:58`) producing a full copy when None — covering Pi's "no targetLeafId" case (`:268`). Pi docstring citation `:234-320` preserved.

### B.6 `import_from_jsonl` — STAYS STUBBED

Body unchanged from 6h₄b — still raises `NotImplementedError`. Docstring amended to cite ADR-0080 carry-forward: "no RPC command in the Pi `RpcCommand` union (`rpc_types.py:309-340`) maps to this method as of SHA `734e08e`; defer real body until a wire surface lands per ADR-0080."

### B.7 Remove `_apply_for_test`

Delete the entire `_apply_for_test` method body at `agent_session_runtime.py:213-223`. Also delete the test-seam comment block.

### B.8 Module docstring amendment

Append per-section note: 

> Sprint 6h₄c (ADR-0079) — wiring sprint. The 4 public replace APIs from 6h₄b are filled with real bodies routed through `JsonlSessionRepo.open` / `JsonlSessionRepo.create` / `JsonlSessionRepo.fork` (Aelix is persisted-only — the Pi in-memory branch at `:303-319` is dropped). `import_from_jsonl` STAYS STUBBED — no RPC wire surface today. Constructor extends with required keyword-only `repo: JsonlSessionRepo` + `fs: FileSystem`. The Sprint 6h₄b `_apply_for_test` test seam is REMOVED — 6h₄b tests migrate to drive `switch_session` via the real public API. P-329 deliberate convergence: Aelix handlers MUST NOT call rebind manually — the runtime's `_finish_session_replacement` auto-invokes the registered callback as single source of truth (Pi belt-and-braces handler-side rebind at `rpc-mode.ts:565-567`/`:573-575`/`:585-587` is NOT mirrored).

---

## §C — `rpc_mode.py` AMEND

### C.1 Imports

```python
# NEW imports at top of rpc_mode.py
from aelix_agent_core.session.fs import FileSystem, LocalFileSystem
from aelix_agent_core.session.jsonl_repo import JsonlSessionRepo
```

### C.2 DEFERRED_COMMANDS 3 → 0

```python
DEFERRED_COMMANDS: dict[str, str] = {}
```

Comment block above DEFERRED_COMMANDS amended:

> Sprint 6h₄c (ADR-0079 / ADR-0080 / P-323~P-331) wires the 3 last DEFERRED session-tree commands (`switch_session` / `fork` / `clone`) on top of the 6h₄b `AgentSessionRuntime` foundation. The counts move to **29 supported / 0 deferred / 29 total** — full Pi parity for the RPC roster (Phase 4 closure).

### C.3 SUPPORTED_COMMANDS additions

Append `"switch_session"`, `"fork"`, `"clone"` to the frozenset. Total = 29. (`new_session` stays — it was already in the SUPPORTED set since Sprint 6d; only its handler arity changes per P-330.)

### C.4 NEW handlers (3 + 1 replaced)

Per P-326 / P-327 / P-328 / P-330. Each handler docstring cites the Pi case site:
- `_handle_switch_session` cites `rpc-mode.ts:563-569`.
- `_handle_fork` cites `rpc-mode.ts:571-577` + P-327 wire-shape note (`selectedText`→`text` rename + key-omission per P-298 pattern).
- `_handle_clone` cites `rpc-mode.ts:579-589` + P-328 leaf-id pre-capture note.
- `_handle_new_session` REPLACED — cites `rpc-mode.ts:277-282` + P-330 stub-removal note.

### C.5 New arity class + factory

```python
_SUPPORTED_HANDLERS_RUNTIME_HOST: dict[
    str, Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]]
] = {
    "new_session": _handle_new_session,         # MOVED from HARNESS_ONLY (P-330)
    "switch_session": _handle_switch_session,
    "fork": _handle_fork,
    "clone": _handle_clone,
}

def _bind_runtime_host(
    handler: Callable[[AgentSessionRuntime, Any], Awaitable[RpcResponse]],
    runtime_host: AgentSessionRuntime,
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Adapt a 2-arg ``(runtime_host, cmd)`` handler to the dispatch
    table's 2-arg ``(harness, cmd)`` shape (P-326). The ``harness``
    positional is ignored.
    """
    async def _adapted(_harness: Any, cmd: Any) -> RpcResponse:
        return await handler(runtime_host, cmd)
    return _adapted
```

### C.6 `build_dispatch_table` signature + body

```python
def build_dispatch_table(
    model_registry: ModelRegistry | None = None,
    *,
    runtime_host: AgentSessionRuntime,    # NEW required keyword-only (P-326)
) -> dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]]:
    """... amended docstring ...

    Sprint 6h₄c (ADR-0079): 29 supported + 0 deferred = 29 total —
    PHASE 4 CLOSURE. Wires `switch_session` / `fork` / `clone` via
    the new `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class. The
    `runtime_host` argument is required and is closed over by
    `_bind_runtime_host` for each of the 4 runtime-host handlers
    (`new_session` MOVED from HARNESS_ONLY per P-330).
    """
    table: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]] = dict(
        _SUPPORTED_HANDLERS_HARNESS_ONLY
    )
    # `new_session` is no longer in HARNESS_ONLY — remove if present from
    # legacy callers (defensive; the source dict already drops it).
    table.pop("new_session", None)
    for cmd_type, three_arg in _SUPPORTED_HANDLERS_HARNESS_REGISTRY.items():
        table[cmd_type] = _bind_registry(three_arg, model_registry)
    for cmd_type, runtime_handler in _SUPPORTED_HANDLERS_RUNTIME_HOST.items():
        table[cmd_type] = _bind_runtime_host(runtime_handler, runtime_host)
    for cmd_type, adr in DEFERRED_COMMANDS.items():
        table[cmd_type] = _make_deferred_handler(cmd_type, adr)
    return table
```

### C.7 `_make_passthrough_runtime` extension

```python
def _make_passthrough_runtime(
    harness: AgentHarness,
    harness_factory: HarnessFactory | None,
    *,
    repo: JsonlSessionRepo,   # NEW required (P-324)
    fs: FileSystem,           # NEW required (P-324)
) -> AgentSessionRuntime:
    """... amended docstring per P-324 ..."""
    if harness_factory is None:
        async def _noop_factory(_new_session: Any) -> AgentHarness:
            raise RuntimeError(
                "Passthrough runtime cannot replace harness — caller "
                "must pass an explicit harness_factory to run_rpc_mode"
            )
        harness_factory = _noop_factory
    return AgentSessionRuntime(harness, harness_factory, repo=repo, fs=fs)
```

### C.8 `run_rpc_mode` signature extension

```python
async def run_rpc_mode(
    harness: AgentHarness,
    *,
    model_registry: ModelRegistry | None = None,
    runtime_host: AgentSessionRuntime | None = None,
    harness_factory: HarnessFactory | None = None,
    repo: JsonlSessionRepo | None = None,         # NEW (P-324)
    fs: FileSystem | None = None,                 # NEW (P-324)
    stdin: asyncio.StreamReader | None = None,
    stdout_write: Callable[[bytes], None] | None = None,
    install_signal_handlers: bool = True,
) -> None:
```

Body amendment: when `runtime_host is None`, the passthrough construction at `rpc_mode.py:1580-1581` becomes:

```python
if runtime_host is None:
    effective_fs = fs if fs is not None else LocalFileSystem()
    effective_repo = repo if repo is not None else JsonlSessionRepo(fs=effective_fs)
    runtime_host = _make_passthrough_runtime(
        harness, harness_factory, repo=effective_repo, fs=effective_fs,
    )
elif repo is not None or fs is not None:
    # When runtime_host is explicit, repo/fs are owned by it — caller
    # must not double-pass.
    raise RuntimeError(
        "repo and fs must not be supplied when runtime_host is explicit "
        "— the runtime owns them"
    )
```

The `build_dispatch_table` call at `rpc_mode.py:1570` is updated to thread `runtime_host`:

```python
dispatch = build_dispatch_table(model_registry, runtime_host=runtime_host)
```

### C.9 Module docstring amendment

Append per-section block:

```python
"""
Sprint 6h₄c (ADR-0079 / ADR-0080 / P-323~P-331) — PHASE 4 CLOSURE.
Wires the 3 last DEFERRED session-tree commands (`switch_session` /
`fork` / `clone`) on top of the Sprint 6h₄b `AgentSessionRuntime`
foundation. Counts move to **29 supported / 0 deferred / 29 total**
— full Pi parity for the `RpcCommand` discriminator union.

New `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class (P-326) carries
4 handlers operating on `AgentSessionRuntime` instead of
`AgentHarness`: `new_session` (MOVED from HARNESS_ONLY per P-330 —
replaces the Sprint 6d stub that rejected `parent_session`) +
`switch_session` + `fork` + `clone`.

`run_rpc_mode` signature extends with optional `repo` / `fs` keyword
parameters (P-324) — supplied by the caller for explicit
`runtime_host` setup, or defaulted to `LocalFileSystem()` +
`JsonlSessionRepo()` when constructing the passthrough.

Aelix handlers DO NOT call rebind manually (P-329 deliberate
convergence): Pi belt-and-braces handler-side `await
rebindSession()` at `rpc-mode.ts:566`/`:574`/`:586` is NOT
mirrored — the runtime's `_finish_session_replacement` auto-invokes
the registered callback as single source of truth (verified by
closure pin assertion that `_rebind_session` is awaited exactly 1×
per replace).

Pi line citations at SHA 734e08e per Sprint 6h₄c W0 (P-323):
- `rpc-mode.ts:563-569` (switch_session handler — 7 lines)
- `rpc-mode.ts:571-577` (fork handler — 7 lines)
- `rpc-mode.ts:579-589` (clone handler — 11 lines)
- `rpc-mode.ts:277-282` (new_session handler — Sprint 6d stub
  replaced via runtime_host route per P-330)
"""
```

---

## §D — Tests (binding plan)

### D.1 `tests/runtime/test_agent_session_runtime.py` (MIGRATE — net delta ~80 LOC)

Drop every `_apply_for_test` usage. Migration substitutions:

- Replace `await runtime._apply_for_test(new_sess)` with a `MemoryRepo` test double pre-loaded with `new_sess`, then `await runtime.switch_session("<test-path>")`.
- 11 tests in §B/§C/§D blocks remain. The §D block ("Replace seam exercised via `_apply_for_test`") becomes "Replace seam exercised via `switch_session` over MemoryRepo".

Keep these tests (real public API now):
- `test_runtime_constructor_stores_harness_identity` (no `_apply_for_test` — passes as-is after constructor migration).
- `test_runtime_session_is_read_through_to_harness_session` (no change).
- `test_runtime_cwd_returns_none_when_session_is_none` (no change).
- `test_runtime_diagnostics_returns_copy_not_internal_list` (no change).
- `test_emit_before_switch_stub_returns_false` (no change — 6h₄c keeps stub per ADR-0080).
- `test_emit_before_fork_stub_returns_false` (no change).
- `test_dispose_calls_harness_dispose_exactly_once` (no change).
- `test_dispose_invokes_before_session_invalidate_before_harness_dispose` (no change).
- `test_runtime_replace_result_*` (no change — still frozen).
- `test_agent_session_runtime_diagnostic_*` (no change).

REMOVE these tests (`_apply_for_test` was the SUT):
- `test_switch_session_raises_not_implemented`
- `test_new_session_raises_not_implemented`
- `test_fork_raises_not_implemented`
- `test_apply_for_test_invokes_factory_with_new_session`
- `test_apply_for_test_disposes_old_harness_once`
- `test_apply_for_test_invokes_before_session_invalidate_before_dispose`
- `test_apply_for_test_invokes_rebind_session_callback_exactly_once`
- `test_apply_for_test_runs_without_rebind_session_registered`
- `test_apply_for_test_runs_without_before_invalidate_registered`
- `test_state_session_id_on_new_harness_reflects_new_session_metadata` (REPLACE with the same assertion in `test_agent_session_runtime_replace_apis.py` driven through `switch_session`).
- `test_two_consecutive_replaces_dispose_each_old_harness` (REPLACE with the same assertion in the new module driven through 2× `switch_session`).

KEEP `test_import_from_jsonl_raises_not_implemented` — `import_from_jsonl` stays stubbed per ADR-0080.

### D.2 `tests/runtime/test_agent_session_runtime_replace_apis.py` (NEW, ~120 LOC)

Real `switch_session` / `new_session` / `fork` over `tmp_path` `JsonlSessionRepo` + `LocalFileSystem`.

| # | Test | Assertion |
|---|---|---|
| 1 | `switch_session` opens existing JSONL — runtime.harness re-binds to NEW session | identity changes + new metadata.id matches |
| 2 | `switch_session` on missing file raises `SessionError("not_found")` | error envelope path |
| 3 | `switch_session` invokes rebind_session callback EXACTLY once (P-329) | spy count == 1 |
| 4 | `switch_session` disposes OLD harness EXACTLY once | spy count == 1 |
| 5 | `new_session(parent_session=None)` creates fresh session (cwd preserved) | new file under same cwd dir |
| 6 | `new_session(parent_session="/old/path.jsonl")` writes `parentSession` header (Pi parity P-330) | jsonl header reads back |
| 7 | `new_session` invokes rebind_session callback EXACTLY once | spy count == 1 |
| 8 | `new_session` raises `RuntimeError` when current cwd is None | defensive |
| 9 | `fork(entry_id, position="before")` returns `selected_text` (extracted user msg) | exact text match |
| 10 | `fork(entry_id, position="at")` returns `selected_text=None` | None propagates |
| 11 | `fork` on invalid entry id raises `ValueError("Invalid entry ID for forking")` | error envelope path |
| 12 | `fork` on a non-user-message entry with position="before" raises ValueError | Pi parity `:254-255` |
| 13 | `fork` over persisted session writes new JSONL file with `parentSession=<source.path>` | persisted-only branch P-325 |
| 14 | After replace, `_state.session_id` on NEW harness reflects NEW session metadata (P-306 invariant preserved) | regression for the P-306 test removed from §D.1 |
| 15 | Two consecutive `switch_session` calls dispose each old harness exactly once (regression for `test_two_consecutive_replaces` removed from §D.1) | sequence |

### D.3 `tests/rpc/test_rpc_mode_runtime_shim.py` AMEND (~30 LOC delta)

All 7 existing tests receive `repo=JsonlSessionRepo(fs=LocalFileSystem())` (or shared fixture). Test #7 (deferred returns ADR-0078) becomes test #7 (deferred set is EMPTY — `DEFERRED_COMMANDS == {}`).

### D.4 `tests/rpc/test_rpc_mode_switch_fork_clone.py` (NEW, ~140 LOC)

| # | Test | Assertion |
|---|---|---|
| **§A switch_session** | | |
| 1 | `_handle_switch_session(runtime, cmd)` returns success on real JSONL | wire shape `{cancelled: false}` |
| 2 | Closure pin: handler invokes runtime.switch_session exactly once | spy |
| 3 | Closure pin: handler does NOT call rebind manually (P-329 — `_rebind_session` cb awaited 1×, not 2×) | invariant |
| 4 | `switch_session` on missing path returns RpcErrorResponse with SessionError msg | error envelope |
| **§B fork** | | |
| 5 | `_handle_fork(runtime, cmd)` over user-message entry returns `{text: "...", cancelled: false}` | wire shape P-327 |
| 6 | `_handle_fork` over `selected_text=None` path returns `{cancelled: false}` (text key omitted — P-327 key-omission per P-298) | key-omission verified |
| 7 | `_handle_fork` on invalid entry_id returns RpcErrorResponse | error envelope |
| 8 | P-329: `_handle_fork` does NOT manually rebind (spy count == 1) | invariant |
| **§C clone** | | |
| 9 | `_handle_clone(runtime, cmd)` over session with current leaf returns `{cancelled: false}` (NO `text` key — Pi drops `selectedText` for clone per P-328) | wire shape |
| 10 | `_handle_clone` when leaf_id is None returns RpcErrorResponse("Cannot clone session: no current entry selected") | wire match Pi `:582` |
| 11 | P-328 ordering: leaf_id captured BEFORE OLD harness dispose (spy on `session.get_leaf_id` + on `harness.dispose` — get_leaf_id resolves first) | ordering invariant |
| 12 | P-329: `_handle_clone` does NOT manually rebind (spy count == 1) | invariant |
| **§D arity** | | |
| 13 | All 3 handlers route through `_bind_runtime_host` (qualname check) | arity class P-326 |
| 14 | `build_dispatch_table(runtime_host=...)` requires the kwarg | P-326 enforcement |

### D.5 `tests/rpc/test_rpc_mode_new_session_parent.py` (NEW, ~40 LOC)

| # | Test | Assertion |
|---|---|---|
| 1 | `_handle_new_session(runtime, cmd)` with `parent_session=None` returns success `{cancelled: false}` (regression for Sprint 6d stub at rpc_mode.py:309-347 removed) | wire shape |
| 2 | `_handle_new_session(runtime, cmd)` with `parent_session="/old/path.jsonl"` returns success + new JSONL has `parentSession` header (P-330 lineage actually persists) | regression for ADR-0058 carry-forward closed |
| 3 | `_handle_new_session` is in `_SUPPORTED_HANDLERS_RUNTIME_HOST` (NOT in HARNESS_ONLY) — P-330 arity change | qualname check |
| 4 | Sprint 6d rejection branch is GONE — confirm no `parent_session lineage tracking deferred` string in the handler source | grep guard |

### D.6 `tests/pi_parity/test_phase_4_13_strict_superset.py` (NEW closure pin, ~180 LOC)

| # | Test | Assertion |
|---|---|---|
| **§A counts (CLOSURE)** | | |
| 1 | `len(SUPPORTED_COMMANDS) == 29` | Phase 4 closure |
| 2 | `len(DEFERRED_COMMANDS) == 0` | DEFERRED roster empty |
| 3 | `SUPPORTED == RPC_COMMAND_TYPES` (NOT just `SUPPORTED ∪ DEFERRED`) | full Pi parity |
| **§B 3 new handlers wired (NOT stubbed)** | | |
| 4 | `build_dispatch_table(runtime_host=...)[ "switch_session" ]` qualname is `_bind_runtime_host._adapted` (NOT `_make_deferred_handler`) | wired |
| 5 | Same for `"fork"` | wired |
| 6 | Same for `"clone"` | wired |
| 7 | Same for `"new_session"` (now routes through runtime_host per P-330) | arity P-330 |
| **§C runtime constructor extension (P-324)** | | |
| 8 | `inspect.signature(AgentSessionRuntime.__init__).parameters` contains `"repo"` + `"fs"` (both keyword-only, no default) | signature lock |
| 9 | `AgentSessionRuntime(harness, factory, repo=None, fs=fs)` raises TypeError (required, not Optional) | required-kwarg enforcement |
| 10 | `_apply_for_test` removed: `not hasattr(AgentSessionRuntime, "_apply_for_test")` | P-331 invariant |
| **§D wire shape pins** | | |
| 11 | `_handle_fork` data when `selected_text="hi"`: `{text: "hi", cancelled: false}` | P-327 wire |
| 12 | `_handle_fork` data when `selected_text=None`: `{cancelled: false}` (no `text` key) | P-327 key-omission per P-298 |
| 13 | `_handle_clone` data shape: `{cancelled: false}` only (NO `text` key per Pi) | P-328 wire |
| 14 | `_handle_switch_session` data shape: `{cancelled: false}` (the runtime's `RuntimeReplaceResult` is dropped to dict per ADR-0079 §C.4) | Pi parity (568) |
| **§E P-329 deliberate convergence (handlers DO NOT manually rebind)** | | |
| 15 | After `_handle_switch_session(runtime, cmd)`, `_rebind_session` invoked exactly 1× (NOT 2×) | invariant |
| 16 | After `_handle_fork(runtime, cmd)`, same | invariant |
| 17 | After `_handle_clone(runtime, cmd)`, same | invariant |
| **§F P-328 clone leaf-id pre-capture ordering** | | |
| 18 | `get_leaf_id` resolves BEFORE OLD harness `dispose` is invoked (spy + ordering) | invariant |
| **§G run_rpc_mode signature (P-324)** | | |
| 19 | `inspect.signature(run_rpc_mode).parameters["repo"].default is None` | additive |
| 20 | `inspect.signature(run_rpc_mode).parameters["fs"].default is None` | additive |
| 21 | `inspect.signature(build_dispatch_table).parameters["runtime_host"].kind == KEYWORD_ONLY` and no default | required |
| **§H Pi line-citation pins** | | |
| 22 | `_handle_switch_session.__doc__` cites `rpc-mode.ts:563-569` | line ref |
| 23 | `_handle_fork.__doc__` cites `rpc-mode.ts:571-577` | line ref |
| 24 | `_handle_clone.__doc__` cites `rpc-mode.ts:579-589` | line ref |
| 25 | `_handle_new_session.__doc__` cites `rpc-mode.ts:277-282` AND P-330 | line ref + cite |
| 26 | `AgentSessionRuntime.switch_session.__doc__` cites `agent-session-runtime.ts:175-198` | preserved from 6h₄b |
| 27 | `AgentSessionRuntime.new_session.__doc__` cites `:200-232` | preserved |
| 28 | `AgentSessionRuntime.fork.__doc__` cites `:234-320` | preserved |
| **§I cumulative cascade allowlists** | | |
| 29 | Cascade pins in 4.4/4.9/4.10/4.11/4.12 — DEFERRED_COMMANDS now empty so the owner-string allowlist tests no longer trigger; verify they still PASS over the empty dict | guard |
| **§J fixture** | | |
| 30 | Fixture pi_sha == `734e08edf82ff315bc3d96472a6ebfa69a1d8016` | Pi pin |
| 31 | Fixture `phase == "4.13"` | self-id |
| 32 | Fixture `closure_supported == 29` + `closure_deferred == 0` | counts |
| 33 | Fixture `pi_handler_lines == {"switch_session": "563-569", "fork": "571-577", "clone": "579-589", "new_session": "277-282"}` | line refs |
| 34 | Fixture `aelix_drops_in_memory_fork == true` (P-325) | architectural decision |
| 35 | Fixture `handler_side_rebind == "deliberate-convergence-runtime-owns"` (P-329) | architectural decision |

### D.7 `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json` (NEW)

```json
{
  "pi_sha": "734e08edf82ff315bc3d96472a6ebfa69a1d8016",
  "sprint": "6h_4_c",
  "phase": "4.13",
  "closure": true,
  "closure_supported": 29,
  "closure_deferred": 0,
  "closure_total": 29,
  "pi_handler_lines": {
    "switch_session": "563-569",
    "fork": "571-577",
    "clone": "579-589",
    "new_session": "277-282"
  },
  "pi_runtime_method_lines": {
    "switch_session": "175-198",
    "new_session": "200-232",
    "fork": "234-320",
    "import_from_jsonl": "329-364"
  },
  "wire_shapes": {
    "switch_session": {"cancelled": "bool"},
    "fork_with_text": {"text": "str", "cancelled": "bool"},
    "fork_text_omitted": {"cancelled": "bool"},
    "clone": {"cancelled": "bool"},
    "new_session": {"cancelled": "bool"}
  },
  "command_count_arithmetic": {
    "before_sprint_6h_4_c": "26 supported / 3 deferred / 29 total",
    "after_sprint_6h_4_c": "29 supported / 0 deferred / 29 total",
    "supported_added": ["switch_session", "fork", "clone"],
    "supported_arity_changed": ["new_session"],
    "deferred_removed": ["switch_session", "fork", "clone"]
  },
  "aelix_drops_in_memory_fork": true,
  "aelix_persisted_only_via_jsonl_repo": true,
  "handler_side_rebind": "deliberate-convergence-runtime-owns",
  "p_323_line_drift_note": "ADR-0076 estimated :566/:574/:586 (3-line stub citations). W0 verified at SHA 734e08e: actual case sites are :563-569/:571-577/:579-589 — supersedes ADR-0078 carry-forward roster lines 67-78.",
  "p_325_synthesis_note": "Pi fork has 3 branches (top + persisted + in-memory). Aelix is persisted-only — the in-memory branch (Pi :303-319) is dropped. Both Pi persisted sub-branches (root fork vs mid-tree at :268-282/:284-300) reduce in Aelix to a single call: repo.fork(source_metadata, ForkOptions(cwd, entry_id=target_leaf_id, position='at', parent_session_path=metadata.path)).",
  "p_329_synthesis_note": "Pi rpc-mode.ts handlers call `await rebindSession()` after success (:565-567/:573-575/:585-587). Pi runtime.finishSessionReplacement ALSO awaits rebind — Pi rebinds 2x per replace. Aelix runtime.finishSessionReplacement (agent_session_runtime.py:196-209) auto-invokes the registered callback. Aelix handlers MUST NOT call rebind manually — closure pin asserts _rebind_session is awaited 1x per replace.",
  "p_330_synthesis_note": "Sprint 6d _handle_new_session at rpc_mode.py:309-347 rejected parent_session via RpcErrorResponse('parent_session lineage tracking deferred to Sprint 6f (ADR-0058)'). Sprint 6h₄c replaces with runtime_host.new_session(parent_session=...) — ADR-0058 carry-forward closes.",
  "p_331_apply_for_test_removed": true,
  "deferred_to_sprint_6h_5": [
    "import_from_jsonl real body (no RPC wire surface today)",
    "commandContextActions in rebind_session closure",
    "P-307 session_shutdown extension event emit",
    "P-308 real session_before_switch / session_before_fork hook events",
    "P-314 with_session 2-stage callback",
    "P-315 optional-cb signatures",
    "assertSessionCwdExists Pi parity",
    "previousSessionFile / sessionStartEvent tracking",
    "Pi forkFrom cross-cwd import"
  ],
  "explicitly_dropped_from_carry_forward": {
    "P-313": "HarnessFactory 4-field refresh — NOT NEEDED (harness rebuild encapsulates services/diagnostics/model_fallback_message inside the new harness construction via factory closure carrying the application's template options)"
  }
}
```

---

## §E — ADRs

### E.1 NEW: ADR-0079 — `0079-phase-4-13-rpc-closure.md`

**Status:** Accepted (Sprint 6h₄c / Phase 4.13 — RPC CLOSURE)

**Context:** Sprint 6h₄b shipped the `AgentSessionRuntime` foundation (ADR-0077) + closure pin (ADR-0078) with 4 stubbed replace APIs raising `NotImplementedError("Sprint 6h₄c — ADR-0078")`. The 3 last DEFERRED session-tree RPC commands (`switch_session` / `fork` / `clone`) remained in `DEFERRED_COMMANDS` with owner ADR-0078. Sprint 6d shipped a `_handle_new_session` stub that rejected `parent_session` with an `RpcErrorResponse`. Sprint 6h₄c CLOSES the Phase 4 RPC roster.

**Decision:**
1. Extend `AgentSessionRuntime.__init__` with required keyword-only `repo: JsonlSessionRepo` + `fs: FileSystem` (P-324).
2. Implement real bodies for `switch_session` / `new_session` / `fork` per P-325 (routed through `JsonlSessionRepo`; Aelix drops Pi's in-memory fork branch as persisted-only). `import_from_jsonl` STAYS STUBBED — no wire surface today.
3. Add `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class + `_bind_runtime_host` factory (P-326); MOVE `_handle_new_session` into it per P-330.
4. Implement 3 new RPC handlers `_handle_switch_session` / `_handle_fork` / `_handle_clone` per P-326 / P-327 / P-328.
5. `_handle_fork` wire shape: `{cancelled, text?}` — `text` key omitted when `selected_text is None` per P-298 pattern (P-327).
6. `_handle_clone` captures `leaf_id` BEFORE entering `runtime_host.fork(...)` per P-328 — pre-capture ordering closure-pinned.
7. Aelix handlers DO NOT call rebind manually (P-329 deliberate convergence) — runtime is single source.
8. REPLACE Sprint 6d `_handle_new_session` stub per P-330 — `ADR-0058 parent_session lineage` carry-forward closes.
9. REMOVE `_apply_for_test` test seam per P-331 — 6h₄b tests migrate to real public API.
10. DEFERRED_COMMANDS 3 → 0; SUPPORTED 26 → 29. Full Pi parity for `RpcCommand`.

**Consequences:**
- **Pi parity (structural):** every Pi case site (`rpc-mode.ts:563-569`/`571-577`/`579-589`/`277-282`) has an Aelix handler at the matching method.
- **Pi parity (wire):** all 29 RPC commands now wire to real handlers. `DEFERRED_COMMANDS` is empty; the `_make_deferred_handler` factory remains as scaffolding for future Pi additions.
- **Phase 4 RPC closure:** Aelix matches Pi's `RpcCommand` discriminator union at full parity.
- **Cost:** harness rebuild per replace remains (P-302). Closure pins from 6h₄b carry forward — `_rebind_session` awaited exactly 1× per replace (NOT 2× per Pi belt-and-braces) is the breaking departure with reviewed rationale.
- **The `_handle_new_session` carry-forward from Sprint 6d (ADR-0058 P-117) closes**: `parent_session` lineage now persists via `repo.create(parent_session_path=...)`.

**Pi line citations (verified at SHA 734e08e):**
- `rpc-mode.ts:563-569` (`switch_session` case site)
- `rpc-mode.ts:571-577` (`fork` case site)
- `rpc-mode.ts:579-589` (`clone` case site)
- `rpc-mode.ts:277-282` (`new_session` case site — Pi side stays unchanged)
- `agent-session-runtime.ts:49-58` (`extractUserMessageText` helper — Aelix module-private mirror)
- `agent-session-runtime.ts:175-198` (`switchSession` body)
- `agent-session-runtime.ts:200-232` (`newSession` body)
- `agent-session-runtime.ts:234-320` (`fork` body — Aelix drops in-memory branch `:303-319`)

### E.2 NEW: ADR-0080 — `0080-phase-4-13-strict-superset-closure.md` (Phase 4 RPC closure pin)

**Status:** Accepted (Sprint 6h₄c / Phase 4.13 closure pin — PHASE 4 CLOSURE)

**Closure pin scope:** P-323 ~ P-331. Counts: **29 supported / 0 deferred / 29 total** — PHASE 4 RPC CLOSURE.

**Carry-forward roster (cumulative — defer to Sprint 6h₅+):**

From ADR-0078 (Sprint 6h₄b — kept open):
1. **P-307 — `session_shutdown` event emit** from `AgentHarness.dispose()`.
2. **P-308 — real `session_before_switch` / `session_before_fork` hook events.** (Cancel-hook surface lands when Aelix hook event union grows.)
3. **P-314 — `with_session` 2-stage callback** wire (no caller surface today).
4. **P-315 — `set_rebind_session` / `set_before_session_invalidate` optional-cb signatures.**
5. **`assertSessionCwdExists` Pi parity** — currently surfaces as `SessionError("storage")` via `repo.open` (`jsonl_repo.py:170-178`).
6. **`previousSessionFile` / `sessionStartEvent` tracking** — no event surface yet.
7. **Pi `forkFrom` cross-cwd import** — no RPC command wires.

From ADR-0078 — **explicitly dropped from carry-forward this sprint:**
- **P-313 — `HarnessFactory` 4-field refresh.** Architectural reassessment: harness rebuild encapsulates `services` / `diagnostics` / `model_fallback_message` inside the new harness construction (factory closure carries the application's template options). The Pi 4-field `apply()` shape was an artifact of Pi's session-swap pattern; harness-rebuild makes it redundant. DROPPED.

NEW from Sprint 6h₄c:
1. **`import_from_jsonl` real body** — no `import_from_jsonl` RPC command in the Pi `RpcCommand` union (`rpc_types.py:309-340`). Implementable but unreachable from wire. STAYS STUBBED.
2. **`commandContextActions`** waveform inside `rebind_session` closure (Pi `rpc-mode.ts:315-345`: `waitForIdle`, `newSession`, `fork`, `navigateTree`, `switchSession`, `reload`) — defer.

Cumulative from earlier sprints (still open):
- From ADR-0076: Pi HTML visual fidelity / ImageContent rendering — Sprint 6h₅+.
- From ADR-0073/0074: `_get_context_usage_safe` real impl (P-282), live `session_id` read (P-291), Pi-source-grep verification tooling (P-286).

### E.3 AMEND: ADR-0034 (Pi reference version pin)

Add Sprint 6h₄c row to the sprint ledger:

> Sprint 6h₄c — Phase 4.13 — `switch_session` / `fork` / `clone` wire + Phase 4 RPC CLOSURE — P-323~P-331 — counts 26/3/29 → 29/0/29 — pin 734e08e

### E.4 AMEND: ADR-0074 / ADR-0076 / ADR-0078 (line-citation supersession per P-323)

- **ADR-0074:** carry-forward roster line citations for `switch_session`/`fork`/`clone` updated from `528-557` (original estimate) → `563-569`/`571-577`/`579-589` (W0-verified).
- **ADR-0076:** carry-forward roster line citations same correction.
- **ADR-0078:** carry-forward roster lines 67-78 superseded — citations `:566`/`:574`/`:586` (3-line stubs) → `:563-569`/`:571-577`/`:579-589` (verified ranges).

Each amendment adds a 2-line "P-323 line drift correction" note pointing to ADR-0079.

### E.5 AMEND: ADR-0078 (closure: foundation→wiring complete)

Append section:

> **2026-05-21 — Sprint 6h₄c closure update.** Sprint 6h₄c lands the wiring on top of this foundation per ADR-0079. The 3 commands (`switch_session` / `fork` / `clone`) MOVED from DEFERRED → SUPPORTED. `_apply_for_test` test seam REMOVED. `_handle_new_session` Sprint 6d stub REPLACED. Foundation→wiring complete; this ADR's closure pin remains valid for the 6h₄b foundation invariants but the 6h₄c closure pin (ADR-0080) supersedes for the active wire roster.

### E.6 README

Add 0079 + 0080 rows; add Sprint 6h₄c sub-table marking it as the RPC CLOSURE sprint with `counts 26/3/29 → 29/0/29` annotation.

---

## §F — Atomic commit plan (W6, EXACTLY 5)

1. `feat: runtime/agent_session_runtime — constructor +repo/+fs + switch_session/new_session/fork real bodies + _extract_user_message_text helper + remove _apply_for_test (P-324/P-325/P-330/P-331)`
2. `feat: rpc/rpc_mode — _SUPPORTED_HANDLERS_RUNTIME_HOST arity + switch_session/fork/clone handlers + replace _handle_new_session + build_dispatch_table(runtime_host=...) + _make_passthrough_runtime(+repo+fs) + run_rpc_mode(+repo+fs) + DEFERRED 3→0 (P-326/P-327/P-328/P-329/P-330)`
3. `test: runtime — replace_apis unit (switch_session/new_session/fork over JsonlSessionRepo) + migrate 6h₄b _apply_for_test usages to switch_session via MemoryRepo`
4. `test: rpc — switch_fork_clone integration + new_session_parent regression + amend runtime_shim for repo/fs (P-324/P-330)`
5. `test+docs: Sprint 6h₄c closure pin (P-323~P-331) + ADRs 0079/0080 + ADR-0034 amend + ADR-0074/0076/0078 line-citation amends + ADR-0078 closure-update amend + README`

---

## §G — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1746 baseline + ~150 new − ~10 removed (`_apply_for_test`) ≥ 1886; 0 fail |
| ruff | clean |
| pyright | 8 baseline (no regression) |
| Closure pin 4.13 | counts 29 supported / 0 deferred / 29 total |
| Cascade pins 4.4/4.6/4.8/4.9/4.10/4.11/4.12 | pass over empty DEFERRED (no owner-string assertions trigger) |
| Atomic commits | EXACTLY 5 |
| Pi line citations in handler docstrings | `563-569` / `571-577` / `579-589` / `277-282` (case sites); `49-58` (`_extract_user_message_text`); `175-198` / `200-232` / `234-320` (runtime methods — preserved from 6h₄b) |
| `_apply_for_test` removed | `not hasattr(AgentSessionRuntime, "_apply_for_test")` |
| `AgentSessionRuntime.__init__` signature | required keyword-only `repo: JsonlSessionRepo` + `fs: FileSystem` |
| `build_dispatch_table` signature | required keyword-only `runtime_host: AgentSessionRuntime` |
| `_handle_fork` wire | `{cancelled}` when `selected_text is None`; `{cancelled, text}` otherwise (P-327) |
| `_handle_clone` wire | `{cancelled}` only (NO `text` key — Pi parity P-328) |
| `_handle_clone` ordering | `leaf_id` resolved BEFORE OLD harness dispose (P-328 spy invariant) |
| `_handle_*` rebind invocation count | EXACTLY 1× per replace (P-329 — runtime owns rebind) |
| `_handle_new_session` parent_session | success path (NOT RpcErrorResponse) — Sprint 6d ADR-0058 carry-forward CLOSED |
| DEFERRED_COMMANDS | empty dict `{}` |
| `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` | full Pi parity (NOT just disjoint union) |

---

## §H — Consensus Addendum (P-329 SYNTHESIS — handler-side vs runtime-side rebind)

**Antithesis (steelman against handler-side OMISSION):**

The top-level binding principle is "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다." Pi handlers explicitly call `await rebindSession()` after each successful replace (`rpc-mode.ts:565-567`/`:573-575`/`:585-587`). The strict-Pi-parity reading is: **port Pi's structure literally — mirror the handler-side rebind call**, even though it duplicates the runtime's call. The Pi belt-and-braces pattern is defensive: if a future runtime modification accidentally drops the `await this.rebindSession?.(...)` line, the handler-side call still keeps the event pipe alive. Omitting handler-side rebind makes Aelix structurally divergent from Pi at the case-site level — every reviewer comparing Aelix `rpc_mode.py` to Pi `rpc-mode.ts` will notice the 2-line block missing, and forensic archaeology against future Pi updates becomes harder.

**Tradeoff tension (real, irreducible):**

| Axis | Runtime-owns rebind (recommended) | Pi belt-and-braces (handler ALSO rebinds) |
|---|---|---|
| Pi structural parity | LOWER (handler skips Pi's `if (!result.cancelled) await rebindSession()` block) | HIGHER (line-for-line mirror) |
| Pi wire parity | EQUAL (event-pipe output identical — 1× rebind suffices to re-subscribe) | EQUAL |
| Idempotency invariant (`_rebind_session` cb count == 1 per replace) | HOLDS (closure pin enforces) | BREAKS (cb fires 2×; 6h₄b closure-pin test at `test_agent_session_runtime.py:294-310` would have to be loosened) |
| Defensive depth | LOWER (one path through rebind) | HIGHER (handler fallback if runtime bug drops rebind) |
| Reviewer surprise | HIGHER (need to read P-329 to understand the omission) | LOWER (Pi mirror is obvious) |
| Test surface | LOWER (1× rebind = 1× assertion) | HIGHER (2× rebind requires asserting both paths fire AND don't fire when one path is bypassed) |

**Synthesis (binding):**

The 1차 목표 admits two readings: **structural** (mirror source line-for-line) vs **behavioral** (wire bytes + observable semantics identical). Sprint 6h₄b's P-302 decision already chose **behavioral** over structural (harness-rebuild vs session-swap). The same shape of decision applies here: Pi belt-and-braces at the handler is a SYMPTOM of Pi's particular invariants — Pi's `setRebindSession` callback shape returns `Promise<void>` with no acknowledgment that the inner rebind already fired, and Pi has no closure-pin assertion that the cb fires exactly once. Aelix DID pin the once-only invariant (`tests/runtime/test_agent_session_runtime.py:294-310`) BECAUSE harness-rebuild makes cb count observably meaningful (each cb invocation = one full `__init__` cost + one event-pipe rebalance).

Mirroring Pi at the handler would break the 6h₄b closure pin and force a regression — the cb would fire 2× per replace, the event pipe would attempt 2 subscribe/unsubscribe cycles per replace (the second one re-subscribing to the SAME harness the first one just subscribed to), and the listener-count invariant would have to be loosened to `>= 1`. The "defensive depth" argument cuts both ways: if a future runtime modification drops the rebind, the closure pin catches it AT THE 6h₄b test layer, not via handler fallback.

The handler-side OMISSION decision is consistent with Aelix's pattern: **enforce invariants via closure pins, not via belt-and-braces redundancy.** The runtime is the single source of truth for replace lifecycle; the handler is a thin façade.

**Why this is not a rubber-stamp:** the Pi belt-and-braces alternative is real, has higher reviewer-readability, AND would be 6 lines simpler in this spec (no P-329 paragraph needed). We reject it because mirroring it would require either (a) loosening the 6h₄b closure pin to `cb count >= 1` (weakening an invariant we paid for), or (b) building a complex spy + suppress mechanism in 6h₄c that lets the handler-side `await rebindSession()` no-op when the runtime already fired it (adding state the runtime doesn't otherwise need). Both options cost more than the documented divergence.

**Principle violations (deliberate mode):** None of the binding principles are violated.
- P-329 is an **Aelix-additive omission** explicitly documented in 3 places (handler docstrings, module docstring, ADR-0079 §Consequences).
- The Pi line citations (`:565-567`/`:573-575`/`:585-587`) ARE preserved in the docstrings with the "Aelix omits per P-329" annotation, so source-grep parity remains intact.
- No Pi member is structurally absent — only the call site within each handler is collapsed.

**Severity note:** if a future reviewer disagrees, the remedy is symmetrical and low-cost: relax the 6h₄b closure pin to `cb count >= 1` AND re-add the 2-line handler-side rebind block per case. The decision is reversible without breaking the wire shape — only the closure pin asymmetry between 6h₄b and 6h₄c is at stake.

---

## §I — Workflow (ADR-0032)

- W0 ✓ DONE — Pi line ranges verified at SHA 734e08e (this spec §0 — `/tmp/pi-6h4c/` snapshot read; case sites `563-589` / runtime bodies `175-320`).
- W1 binding spec (this doc).
- W2 executor opus — emits commits 1-2 (runtime amend + rpc_mode amend).
- W3 verification — runs full pytest + ruff + pyright; confirms `len(SUPPORTED_COMMANDS) == 29` and `len(DEFERRED_COMMANDS) == 0`.
- W4 code-reviewer opus (parallel W5) — audits new arity class, P-329 deliberate convergence, P-330 stub removal, in-memory branch drop rationale, key-omission for fork, leaf-id pre-capture ordering.
- W5 architect Pi parity audit (parallel W4) — verifies Pi line citations against `/tmp/pi-6h4c/rpc-mode.ts:563-589` + `/tmp/pi-6h4c/agent-session-runtime.ts:49-58/175-320` + grep that ADR-0058 carry-forward closing matches the P-330 narrative.
- W6 apply must-fixes + EXACTLY 5 commits + ADRs 0079/0080 + ADR-0034 amend + ADR-0074/0076/0078 line-citation amends + ADR-0078 closure-update amend + README.

---

**End of binding spec. Architect READ-ONLY until W6.**

**Korean principle echo:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다." — 본 스프린트는 Phase 4 RPC closure로, Pi `rpc-mode.ts:563-569 / :571-577 / :579-589` 3 case site를 Aelix `_handle_switch_session` / `_handle_fork` / `_handle_clone` 핸들러로 정확히 미러링하고, Pi `agent-session-runtime.ts:175-198 / :200-232 / :234-320` 3 method body를 `JsonlSessionRepo` 위에 구현하여 SUPPORTED 26→29 / DEFERRED 3→0를 달성합니다. Pi belt-and-braces handler-side rebind (P-329)는 의도적 단일 소스 수렴으로 omit하고, 6h₄b의 closure-pin invariant (`_rebind_session` 1× per replace)를 보호합니다.

---

## Reference file paths

- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py` — runtime to amend (constructor + 4 method bodies + remove `_apply_for_test`)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/_types.py` — `RuntimeReplaceResult` (unchanged; already carries `selected_text`)
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py` — handlers + arity class + dispatch + run_rpc_mode signature
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_types.py:243-265` — `RpcCommandSwitchSession` / `RpcCommandFork` / `RpcCommandClone` defs
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_repo.py:136,164,238` — `create` / `open` / `fork` surface
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_storage.py:229` — `load_jsonl_session_metadata`
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/repo_utils.py:22-37` — `ForkOptions` + `ForkPosition`
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/session.py:103-110` — `Session.get_leaf_id` / `get_entry` (async)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/session/storage.py:60-66` — `JsonlSessionMetadata` (`cwd`, `path`, `parent_session_path`)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:475-524` — harness `__init__` + `_state.session_id` capture (P-306 anchor)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:1961-1976` — `harness.dispose()`
- `/workspaces/aelix-ai/tests/runtime/test_agent_session_runtime.py` — 6h₄b unit suite to migrate
- `/workspaces/aelix-ai/tests/rpc/test_rpc_mode_runtime_shim.py` — 6h₄b shim suite to amend
- `/workspaces/aelix-ai/tests/pi_parity/test_phase_4_12_strict_superset.py` — 6h₄b closure pin pattern (template for 4.13)
- `/workspaces/aelix-ai/docs/decisions/0077-agent-session-runtime-port.md` — 6h₄b architecture ADR (parent)
- `/workspaces/aelix-ai/docs/decisions/0078-phase-4-12-strict-superset-closure.md` — 6h₄b closure ADR (carry-forward roster amended by ADR-0080)
- `/workspaces/aelix-ai/docs/decisions/0034-pi-reference-version-pin.md:416-461` — Pi pin ledger (Sprint 6h₄c row to append)
- `/tmp/pi-6h4c/rpc-mode.ts:563-589` — Pi handler case sites (W0 verified)
- `/tmp/pi-6h4c/agent-session-runtime.ts:49-58,175-320` — Pi runtime bodies (W0 verified)
