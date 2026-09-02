# Sprint 6h₅b — Phase 4.15 BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

---

## §0 — W0 Findings (P-356 → P-363)

### P-356 — `ReplacedSessionContext` placement (BINDING)
Land as `typing.Protocol` in `runtime/_types.py`. Rationale: avoids cross-package circular import (`aelix_agent_core` → `aelix_coding_agent`) + bypass `__getattribute__` stale-guard. Pi structural typing preserved.

### P-357 — `create_replaced_session_context` factory (BINDING)
On `AgentHarness`. Returns `types.SimpleNamespace` (Pi `Object.defineProperties` clone idiom). Wraps `_make_context()` baseline + overlays `send_message`/`send_user_message`.

### P-358 — `with_session` parameter (BINDING)
Add to `switch_session`/`new_session`/`fork`/`_finish_session_replacement`. Type: `Callable[[ReplacedSessionContext], Awaitable[None]] | None = None`. Order in `_finish_session_replacement`: teardown → apply → rebind → session_start emit → `with_session(create_replaced_session_context())`.

### P-359 — `setup` callback in `new_session` (BINDING)
`setup: Callable[[ReadonlySessionManager], Awaitable[None]] | None`. Position: AFTER `_apply`, BEFORE rebind. Then rebuild: `harness._state.messages = list((await new_session.build_context()).messages)`.

**Implementation:** Refactor `_finish_session_replacement` to accept `setup` parameter and run it between `_apply` and `rebind_session`.

### P-360 — `import_from_jsonl` real body (BINDING)
Port Pi `agent-session-runtime.ts:329-364`. Steps:
1. `resolve_path` → `SessionImportFileNotFoundError` if missing
2. mkdir session dir
3. `destination_path = join(session_dir, basename(resolved))`
4. `_emit_before_switch(reason="resume", target=destination)` cancel short-circuit
5. snapshot previous_session_file
6. `copy_file` if paths differ
7. `load_jsonl_session_metadata` + apply `cwd` override via `dataclasses.replace(metadata, cwd=cwd)`
8. `repo.open(metadata)` + `assert_session_cwd_exists`
9. `_finish_session_replacement(reason="resume")` — NO with_session (Pi signature confirms)

Add `SessionImportFileNotFoundError` to `runtime/_types.py`.

### P-361 — `JsonlSessionRepo.fork_from` (BINDING)
Add `async def fork_from(self, source: JsonlSessionMetadata, target_cwd: str) -> Session`. Pi `session-manager.ts:1353-1394`: ALL entries copy (no leaf truncation), rewrite cwd, `parent_session_path=source.path`. No internal consumer in 6h₅b — CLI `/branch-from` lands Phase 5.

### P-362 — `ExtensionRunner.invalidate` + bridge (BINDING — SYNTHESIS per §J)
- Drop `frozen=True` on `ExtensionRunner` dataclass
- Add `_invalidate_runtime: Callable[[str], None] | None = None` bridge field
- Add `invalidate(message=None)` — passes Pi default if None; calls bridge; bridge propagates to `_ExtensionRuntime.invalidate`
- Add `assert_active()` — reads through `_ExtensionRuntime._stale_message` via callable
- Wire bridge in `AgentHarness.__init__`: `_invalidate_runtime=self._runtime.invalidate`
- Add `PI_STALENESS_MESSAGE` constant in `runtime/_types.py` (Pi verbatim long string from `runner.ts:467`)
- Align `_ExtensionRuntime.invalidate` default to `PI_STALENESS_MESSAGE` (currently divergent)

**Synthesis decision** (per §J): single source of truth via runtime — `_ExtensionRuntime._stale_message` is the only mutable flag; `ExtensionRunner` is the public Pi-named entry point that delegates.

### P-363 — call `runner.invalidate` from teardown/dispose (BINDING)
Insert `runner.invalidate(PI_STALENESS_MESSAGE)` in `_teardown_current` AND `dispose` between EMIT and `before_session_invalidate` callback. Order: emit_shutdown → runner.invalidate → before_session_invalidate → harness.dispose.

---

## §A — Scope LOC table

| Surface | File | prod | test |
|---|---|---|---|
| `ReplacedSessionContext` Protocol + `SessionImportFileNotFoundError` + `PI_STALENESS_MESSAGE` | `runtime/_types.py` | +40 | — |
| `AgentHarness.create_replaced_session_context` factory | `harness/core.py` | +30 | — |
| `with_session` plumbing (4 sites) | `runtime/agent_session_runtime.py` | +35 | — |
| `setup` plumbing + `_finish_session_replacement` refactor | `runtime/agent_session_runtime.py` | +35 | — |
| `import_from_jsonl` real body | `runtime/agent_session_runtime.py` | +60 | — |
| `JsonlSessionRepo.fork_from` | `session/jsonl_repo.py` | +50 | — |
| `ExtensionRunner.invalidate` + bridge + frozen drop | `harness/_extension_runner.py` | +30 | — |
| `_ExtensionRuntime.invalidate` default-msg align | `extensions/api.py` | +5 | — |
| `runner.invalidate` calls in teardown + dispose | `runtime/agent_session_runtime.py` | +10 | — |
| `FileSystem.copy_file` (verify exists; add if missing) | `session/fs.py` | +15 | — |
| **Prod total** | | **~310** | — |
| Test files (6 NEW) | tests/ | — | ~230 |

**Combined: ~310 prod + ~200 test (±30 tolerance).**

### NOT in scope (carry-forward)
- CLI `/branch-from` consumer for `fork_from` — Phase 5
- Pi factory `assert_session_cwd_exists` call at `:391` — Sprint 6h₅c
- First `session_start` at bootstrap (`reason="startup"`) — Sprint 6h₅c
- TUI `/import` command wiring for `import_from_jsonl` — Phase 5
- New RPC commands — ZERO

---

## §B — `ReplacedSessionContext` Protocol

`runtime/_types.py`:
```python
from typing import Protocol, runtime_checkable, Any, Mapping
from collections.abc import Awaitable

PI_STALENESS_MESSAGE = (
    "This extension ctx is stale after session replacement or reload. "
    "Do not use a captured pi or command ctx after ctx.newSession(), "
    "ctx.fork(), ctx.switchSession(), or ctx.reload(). For newSession, "
    "fork, and switchSession, move post-replacement work into withSession "
    "and use the ctx passed to withSession. For reload, do not use the "
    "old ctx after await ctx.reload()."
)


@runtime_checkable
class ReplacedSessionContext(Protocol):
    """Pi parity: extensions/types.ts:366-381."""
    cwd: str
    model: Any
    session_manager: Any
    signal: Any
    has_ui: bool

    def is_idle(self) -> bool: ...
    def abort(self) -> None: ...
    def get_active_tools(self) -> list[str]: ...
    def get_system_prompt(self) -> str: ...
    def has_pending_messages(self) -> bool: ...
    def shutdown(self) -> None: ...
    def get_context_usage(self) -> Any | None: ...
    def compact(self, **kwargs: Any) -> None: ...

    async def send_message(
        self,
        message: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None: ...

    async def send_user_message(
        self,
        content: str | list[Any],
        options: Mapping[str, Any] | None = None,
    ) -> None: ...


class SessionImportFileNotFoundError(Exception):
    """Pi parity: agent-session-runtime.ts import path missing error."""
    def __init__(self, path: str) -> None:
        super().__init__(f"Session import file not found: {path}")
        self.path = path
```

---

## §C — Runtime AMEND

### C.1 `_finish_session_replacement` signature
```python
async def _finish_session_replacement(
    self,
    new_session: Session,
    *,
    reason: Literal["new", "resume", "fork"] = "resume",
    previous_session_file: str | None = None,
    target_session_file: str | None = None,
    setup: Callable[[Any], Awaitable[None]] | None = None,
    with_session: Callable[[ReplacedSessionContext], Awaitable[None]] | None = None,
) -> None:
    await self._teardown_current(reason, target_session_file)
    await self._apply(new_session)
    
    # P-359: setup BEFORE rebind
    if setup is not None:
        await setup(self._harness._make_context().session_manager)
        self._harness._state.messages = list(
            (await new_session.build_context()).messages
        )
    
    if self._rebind_session is not None:
        await self._rebind_session(self._harness)
    
    # session_start emit (Sprint 6h₅a)
    
    # P-358: with_session AFTER rebind + emit
    if with_session is not None:
        await with_session(self._harness.create_replaced_session_context())
```

### C.2 Public API plumbing
- `switch_session(path, *, with_session=None)` — forward
- `new_session(*, parent_session=None, setup=None, with_session=None)` — forward both
- `fork(entry_id, *, position="before", with_session=None)` — forward
- Update docstrings: remove "omits setup + with_session per ADR-0080 carry-forward" lines

### C.3 `import_from_jsonl` real body (P-360)
Replace stub at `:653-670`:
```python
async def import_from_jsonl(
    self,
    path: str,
    *,
    cwd: str | None = None,
) -> RuntimeReplaceResult:
    """Pi parity: agent-session-runtime.ts:329-364."""
    resolved_path = await self._fs.resolve_path(path)
    if not await self._fs.path_exists(resolved_path):
        raise SessionImportFileNotFoundError(resolved_path)

    current_cwd = cwd or self.cwd
    if current_cwd is None:
        raise RuntimeError("import_from_jsonl requires a cwd")
    session_dir = await self._repo._get_session_dir(current_cwd)
    await self._fs.mkdir_p(session_dir)
    destination_path = await self._fs.join_path(
        [session_dir, await self._fs.basename(resolved_path)]
    )

    if await self._emit_before_switch(
        reason="resume", target_session_file=destination_path
    ):
        return RuntimeReplaceResult(cancelled=True)

    previous_session_file = (
        self.session.session_file if self.session is not None else None
    )

    if resolved_path != destination_path:
        await self._fs.copy_file(resolved_path, destination_path)

    metadata = await load_jsonl_session_metadata(self._fs, destination_path)
    if cwd is not None:
        metadata = replace(metadata, cwd=cwd)
    new_session = await self._repo.open(metadata)
    await assert_session_cwd_exists(
        new_session, fallback_cwd=current_cwd, fs=self._fs
    )

    await self._finish_session_replacement(
        new_session,
        reason="resume",
        previous_session_file=previous_session_file,
        target_session_file=destination_path,
    )
    return RuntimeReplaceResult(cancelled=False)
```

### C.4 `_teardown_current` + `dispose` (P-363)
Insert `runner.invalidate(PI_STALENESS_MESSAGE)` between EMIT and `before_session_invalidate`.

---

## §D — `JsonlSessionRepo.fork_from`

`session/jsonl_repo.py`:
```python
async def fork_from(
    self,
    source: JsonlSessionMetadata,
    target_cwd: str,
) -> Session:
    """Pi parity: SessionManager.forkFrom (session-manager.ts:1353-1394).
    
    Cross-cwd import: loads ALL source entries, creates NEW file under
    target_cwd, rewrites cwd header, parent_session_path=source.path.
    Unlike .fork — no leaf truncation.
    """
    session_id, created_at = await _next_session_identity()
    session_dir = await self._get_session_dir(target_cwd)
    await self._fs.mkdir_p(session_dir)
    target_path = await self._build_session_path(
        target_cwd, session_id, created_at
    )
    source_storage = await _load_jsonl_storage(self._fs, source.path)
    all_entries = await source_storage.get_entries()
    new_metadata = JsonlSessionMetadata(
        id=session_id,
        path=target_path,
        cwd=target_cwd,
        created_at=created_at,
        parent_session_path=source.path,
    )
    await _write_jsonl_session(self._fs, new_metadata, all_entries)
    return await self.open(new_metadata)
```

---

## §E — `ExtensionRunner.invalidate` + bridge

`harness/_extension_runner.py`:
```python
@dataclass  # was frozen=True — Sprint 6h₅b drops for invalidate state
class ExtensionRunner:
    extensions: list[Extension]
    _emit: Callable[[Any], Awaitable[Any]] | None = None
    _has_handlers: Callable[[str], bool] | None = None
    _invalidate_runtime: Callable[[str], None] | None = None  # NEW

    def invalidate(self, message: str | None = None) -> None:
        """Pi parity: ExtensionRunner.invalidate (runner.ts:466-473).
        
        Idempotent — first call sets the message and propagates to
        the underlying _ExtensionRuntime.
        """
        if self._invalidate_runtime is not None:
            self._invalidate_runtime(message or PI_STALENESS_MESSAGE)

    def assert_active(self) -> None:
        """Pi parity: ExtensionRunner.assertActive."""
        if self._invalidate_runtime is None:
            return
        # Runtime owns single source of truth — synthesis per spec §J
```

`harness/core.py:632-641` — wire bridge:
```python
self._extension_runner = ExtensionRunner(
    extensions=self._extensions,
    _emit=self._hooks.emit,
    _has_handlers=self._hooks.has_handlers,
    _invalidate_runtime=self._runtime.invalidate,  # NEW
)
```

`extensions/api.py:435`:
```python
def invalidate(self, message: str | None = None) -> None:
    self._stale_message = message or PI_STALENESS_MESSAGE
```

---

## §F — Tests (binding plan)

| Test file | Coverage |
|---|---|
| `tests/runtime/test_replaced_session_context.py` | Protocol structural check + factory returns SimpleNamespace conforming to Protocol + send_message/send_user_message route correctly |
| `tests/runtime/test_with_session_callback.py` | All 3 replace APIs accept with_session; callback fires AFTER rebind on NEW harness's runner; raises propagate |
| `tests/runtime/test_setup_callback_new_session.py` | setup(session_manager) invoked AFTER apply BEFORE rebind; messages rebuilt from build_context |
| `tests/runtime/test_import_from_jsonl_real.py` | missing path → SessionImportFileNotFoundError; same-dir → no copy; different-dir → copy; cwd override rewrites metadata; cancel short-circuits |
| `tests/session/test_jsonl_fork_from.py` | ALL entries copied; target cwd matches; parent_session_path set; full-history not partial |
| `tests/harness/test_extension_runner_invalidate.py` | invalidate sets stale via runtime; idempotent; default = PI_STALENESS_MESSAGE; bridge propagates; teardown/dispose call invalidate |

~230 test LOC.

---

## §G — ADRs

**NEW ADR-0083** — Runtime callback Pi parity (Phase 4.15)
- Pi citations: `agent-session-runtime.ts:166-173/226-229/329-364`, `agent-session.ts:3087-3095`, `extensions/types.ts:366-381`, `session-manager.ts:1353-1394`, `runner.ts:466-473`
- Divergences:
  1. Protocol (not subclass) for `ReplacedSessionContext`
  2. `SimpleNamespace` factory
  3. `cwd_override` via `dataclasses.replace`
  4. Runtime-owned single source of truth for staleness (synthesis)

**NEW ADR-0084** — Sprint 6h₅b closure (with_session/setup/forkFrom/import_from_jsonl/P-351 done)

**AMEND ADR-0034** — Runtime callback parity closed by ADR-0083/0084

**AMEND ADR-0082** — Reference ADR-0084 closure

---

## §H — Atomic commit plan (EXACTLY 5)

1. `feat: runtime/_types — ReplacedSessionContext Protocol + SessionImportFileNotFoundError + PI_STALENESS_MESSAGE (P-356)`
2. `feat: harness — create_replaced_session_context factory + ExtensionRunner.invalidate bridge + _ExtensionRuntime default-msg align (P-357/P-362)`
3. `feat: runtime — with_session + setup plumbing + teardown/dispose invalidate (P-358/P-359/P-363)`
4. `feat: runtime+session — import_from_jsonl real body + fork_from (P-360/P-361)`
5. `docs+test: ADRs 0083/0084 + amends + closure docs`

---

## §I — Verification gates

After EACH commit:
- pyright: 8 baseline preserved
- ruff clean
- pytest 1865+ pass (target 1890+ at final commit)

Final:
- 5 commits on top of `e6fdcc6`
- Pi line citations present in code + ADRs (grep)
- `PI_STALENESS_MESSAGE` single source of truth (referenced from both packages)
- Korean principle echoed in ADR-0083

---

## §J — Consensus Addendum

**Antithesis (steelman):** Skip `_stale_message` on `ExtensionRunner`; let `_ExtensionRuntime` own it solely. Pi happens to model on the runner but Aelix already has the runtime as lifecycle owner. Two flags is a footgun.

**Synthesis (binding):** Pi symbolic parity (callers say `runner.invalidate(msg)`) + single source of truth (runtime owns the flag). `ExtensionRunner.invalidate` is a delegating method that calls `_ExtensionRuntime.invalidate` via the bridge. NO `_stale_message` field on runner. `assert_active` delegates the same way.

---

## §K — Workflow
W2 executor opus → W3 verification → W4/W5 parallel review/audit → W6 commits + ADRs.

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
