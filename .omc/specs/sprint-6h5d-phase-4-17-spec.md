# Sprint 6h₅d — Phase 4.17 — Non-UI Carry-Forward Closure BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance).

A 단계 closure tail. Wire 3 non-UI carry-forward items from ADR-0086 + DEFER `reload()` per binding principle. NO new RPC commands. UI items (ANSI/tool-renderer/sidebar/theme math) excluded per user consultation gate.

---

## §0 — W0 findings (P-380 ~ P-384)

### P-380 — Pi `reload()` body (Pi `agent-session.ts:2383-2404`) — 6 primitives
```ts
async reload(): Promise<void> {
    const previousFlagValues = this._extensionRunner.getFlagValues();
    await emitSessionShutdownEvent(this._extensionRunner, { type: "session_shutdown", reason: "reload" });
    await this.settingsManager.reload();
    resetApiProviders();
    await this._resourceLoader.reload();
    this._buildRuntime({...});
    const hasBindings = this._extensionUIContext || ...;
    if (hasBindings) {
        await this._extensionRunner.emit({ type: "session_start", reason: "reload" });
        await this.extendResourcesFromExtensions("reload");
    }
}
```

**5 of 6 primitives missing in Aelix**: `settingsManager.reload`, `resetApiProviders`, `_resourceLoader.reload`, `flagValues` round-trip, `_buildRuntime`. Only `extendResourcesFromExtensions("reload")` is wired (`harness.reload_resources()` at `core.py:2085-2092`). Pi `:2401` emit cannot ship in isolation — would emit `session_start(reason="reload")` over a session that did NOT actually reload (semantic divergence violates principle).

### P-381 — Aelix `reload` surface inventory
- `AgentHarness.reload_resources()` `core.py:2085-2092` — partial (re-emits `resources_discover` only)
- `ExtensionCommandContext.reload()` `command_context.py:86-89` — delegates to harness
- `ReplacedSessionContext.reload` stub `core.py:2374-2382` — raises NotImplementedError (Sprint 6h₅+ TUI)
- CLI `/reload` `cli/repl.py:96-97` — calls reload_resources
- `SessionStartHookEvent.reason` literal already permits `"reload"` (`hooks.py:727`)
- `SessionShutdownHookEvent.reason` literal already permits `"reload"` (`hooks.py:772`)

### P-382 — P-375 monkeypatch fragility root cause
`tests/test_factory_assert_session_cwd.py:95-141` patches `aelix_agent_core.session.session_cwd.assert_session_cwd_exists` but `runtime/agent_session_runtime.py:925` imports inside factory body. Currently works only because import is function-local (re-resolves module attr per call). Future hoist breaks the test.

**Fix Option A (recommended)**: hoist import + patch single new binding site at `aelix_agent_core.runtime.agent_session_runtime.assert_session_cwd_exists`. Use `monkeypatch.setattr` fixture.

### P-383 — W4 MINOR-1 root cause (`template.py:45-240`, NOT format.py)
**ADR-0085/0086 typo**: cite `format.py` but actual f-string is `template.py:45-240` (196-line f-string with `_PYGMENTS_CSS` interpolation inside, requiring brace-doubling). Refactor to string concat:
```python
_THEME_CSS = _BASE_THEME_CSS + "\n" + _PYGMENTS_CSS + "\n" + _IMAGE_CSS
```

### P-384 — `harness._session` private reaches (6 sites)
| File:line | Caller |
|---|---|
| `runtime/agent_session_runtime.py:241` | `RuntimeHost.session` property |
| `runtime/agent_session_runtime.py:248` | `RuntimeHost.cwd` |
| `runtime/agent_session_runtime.py:929` | factory check |
| `runtime/agent_session_runtime.py:931` | factory assert call |
| `rpc/rpc_mode.py:545,551` | RPC `setSessionName` |
| `cli/repl.py:64,66` | REPL `user_bash` |

Add `AgentHarness.session: Session | None` public property after `session_name` (`core.py:728`).

---

## §A — Scope LOC table (~58-83 prod + ~30 test)

| Item | Files | Prod | Test |
|---|---|---|---|
| §C P-375 hoist | runtime + 1 test | ~3 | ~10 |
| §D template.py concat | template.py + 1 test | ~40 | ~10 |
| §E session property + 6 migrations | core.py + runtime + rpc + cli + 1 test | ~15 | ~10 |
| **Total** | | **~58** | **~30** |

NO §B reload() port — DEFERRED.

---

## §B — `reload()` decision: DEFER to Phase 5 (BINDING)

Pi `reload()` requires 5 missing primitives (P-380). Porting only `:2401` emit branch in isolation **violates Korean parity principle** — extensions would observe `session_start(reason="reload")` over a session that did NOT actually reload. Document re-deferral in ADR-0087 with full ledger.

---

## §C — P-375 hoist import + monkeypatch fix

Hoist to `agent_session_runtime.py` top-level:
```python
from aelix_agent_core.harness.hooks import SessionStartHookEvent
from aelix_agent_core.session.session_cwd import assert_session_cwd_exists
```

Verify no import cycle (grep `session_cwd.py` for runtime imports — currently zero).

Test rewrite (`tests/test_factory_assert_session_cwd.py:95-141`):
```python
from aelix_agent_core.runtime import agent_session_runtime as _mod
monkeypatch.setattr(_mod, "assert_session_cwd_exists", raising_assert)
monkeypatch.setattr(_mod.AgentSessionRuntime, "__init__", spy_init)
```

Drop manual try/finally block.

---

## §D — `template.py` f-string → string concat

`_export_html/template.py:45-240`:

1. Locate `{_PYGMENTS_CSS}` interpolation site in current f-string.
2. Split into `_BASE_THEME_CSS` (everything before) + `_IMAGE_CSS` (everything after).
3. Convert `{{` → `{` and `}}` → `}` in both halves.
4. Drop `f` prefix on both new constants.
5. Add `_THEME_CSS = _BASE_THEME_CSS + "\n" + _PYGMENTS_CSS + "\n" + _IMAGE_CSS`.

New test `tests/test_export_html_template_concat.py`:
```python
def test_theme_css_contains_pygments_classes() -> None:
    from aelix_coding_agent._export_html.template import _THEME_CSS
    assert ".pyg" in _THEME_CSS

def test_theme_css_contains_base_theme() -> None:
    from aelix_coding_agent._export_html.template import _THEME_CSS
    assert "--bg: #1e1e1e" in _THEME_CSS
    assert ".message-image" in _THEME_CSS
    assert ".tool-image" in _THEME_CSS
```

---

## §E — `AgentHarness.session` public property + 6 migrations

`harness/core.py` after `session_name` (`:728`):
```python
@property
def session(self) -> Session | None:
    """Pi parity for runtimeHost.session (agent-session-runtime.ts:83-85).
    
    Sprint 6h₅d P-384: replaces 6 harness._session private reaches across
    runtime/RPC/CLI. Re-reads self._session per call so rebind sessions
    propagate.
    """
    return self._session
```

Update 6 callsites to `harness.session`:
- `runtime/agent_session_runtime.py:241, 248, 929, 931`
- `rpc/rpc_mode.py:545, 551` (use local binding for type narrowing)
- `cli/repl.py:64, 66` (use local binding for type narrowing)

DO NOT migrate internal `_session` reads inside `harness/core.py` (class accesses own private — canonical pattern).

New test `tests/harness/test_session_property.py`:
```python
async def test_harness_session_property_returns_attached_session(...) -> None:
    ...
    assert harness.session is session

async def test_harness_session_property_none_when_unattached() -> None:
    harness = _new_harness(session=None)
    assert harness.session is None
```

---

## §F — Tests
- NEW `tests/harness/test_session_property.py`
- NEW `tests/test_export_html_template_concat.py`
- MODIFIED `tests/test_factory_assert_session_cwd.py` (monkeypatch.setattr rewrite)
- ALL existing tests stay green (29/0 roster unchanged, 6h₅a~c closure pins unchanged)

Expected: 1928 → ~1932 pass + 1 skipped.

---

## §G — ADRs

### NEW ADR-0087 `docs/decisions/0087-sprint-6h5d-non-ui-cleanup.md`
- Title: Sprint 6h₅d Phase 4.17 — Non-UI Carry-Forward Closure
- Status: Accepted, Date: 2026-05-22
- Decisions per §C/§D/§E + DEFER §B
- Pi citations: `agent-session.ts:2383-2404`, `:391`, `agent-session-runtime.ts:83-85`
- §"Deferred items": full `reload()` port with 5-primitive ledger
- §"Closed carry-forward": P-375, MINOR-1 (with template.py file correction), MINOR-3 strikethrough

### AMEND ADR-0086 carry-forward catalog
- Strikethrough closed items (P-375 / MINOR-1 / MINOR-3) with ADR-0087 citation
- Note `reload()` re-deferred to Phase 5 with rationale link

### NO ADR-0085 retroactive edit — historical record preserved

---

## §H — Atomic commit plan (EXACTLY 4)

**Commit 1** — `fix(test): P-375 hoist import + monkeypatch.setattr (Sprint 6h₅d §C)`
- `runtime/agent_session_runtime.py`
- `tests/test_factory_assert_session_cwd.py`

**Commit 2** — `refactor: MINOR-1 template.py f-string → string concat (Sprint 6h₅d §D)`
- `_export_html/template.py`
- `tests/test_export_html_template_concat.py`

**Commit 3** — `feat(harness): MINOR-3 AgentHarness.session property + 6 callsite migrations (Sprint 6h₅d §E)`
- `harness/core.py`
- `runtime/agent_session_runtime.py`
- `rpc/rpc_mode.py`
- `cli/repl.py`
- `tests/harness/test_session_property.py`

**Commit 4** — `docs: ADR-0087 (Sprint 6h₅d closure) + ADR-0086 amend (strikethrough + reload re-defer)`
- `docs/decisions/0087-sprint-6h5d-non-ui-cleanup.md` (NEW)
- `docs/decisions/0086-a-stage-closure.md` (AMEND)
- `docs/decisions/README.md` (append row)

Each commit uses HEREDOC + trailer:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §I — Verification gates

After each commit:
- ruff: clean
- pyright: 8 baseline (no new)
- pytest: target subset green
- After C3 grep: `grep -rn "harness\._session" packages/aelix-coding-agent/ packages/aelix-agent-core/src/aelix_agent_core/runtime/` → 0

Final: 1932 pass + 1 skipped. RPC roster 29/0 unchanged.

---

## §J — Workflow

W2 executor → W3 verification → W4/W5 review → W6 commits.

**Out-of-scope** (binding):
- NO `reload()` method (deferred per §B)
- NO UI items (per project memory)
- NO new RPC commands
- NO Pi pin advance

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
