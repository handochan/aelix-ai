# Sprint 6h₇a — Phase 5a-iii-α — List-Models + Append-System-Prompt BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance).
**Splits from:** original Sprint 6h₇ scope per Option A decision (partial Phase 5a-iii only).
**Carry-forward to:** Sprint 6h₇b (SettingsManager full port standalone, ~1400-1600 LOC).

Partial Phase 5a-iii: ports Pi `tui/src/fuzzy.ts` + `cli/list-models.ts` and wires `--list-models` (replacing current stderr deferred diagnostic) + minimal text-only `--append-system-prompt` (no @file). NO SettingsManager / NO ResourceLoader / NO image branch / NO migrations / NO session-picker / NO new RPC / NO Pi pin advance / NO ANSI / NO TUI.

---

## §0 — W0 findings (P-414 ~ P-422)

### P-414 — Pi `tui/src/fuzzy.ts` algorithm (137 LOC, stdlib only)
- `fuzzyMatch(query, text)`: lowercase both → all query chars must appear in order in text → score with consecutive-bonus (-5), gap-penalty (+2), word-boundary (-10), position (+0.1), exact-match (-100).
- `fuzzyFilter(items, query, getText)`: whitespace-split query → AND-filter (each token must match) → sort by total score ascending.
- Word boundaries: ` `, `-`, `_`, `.`, `/`, `:`.
- Alphanumeric swap fallback for queries like `codex52` matching `gpt-5.2-codex` (+5 penalty).

### P-415 — Pi `cli/list-models.ts` body (111 LOC)
Single async function `listModels(modelRegistry, searchPattern?)`. Body order:
1. Load-error warning (Pi uses `chalk.yellow` → stderr).
2. Get models from registry (Pi `getAvailable()`).
3. If empty: print `formatNoModelsAvailableMessage()` and return.
4. If pattern: `fuzzyFilter` on `${provider} ${id}` keys.
5. If filtered empty: print `"No models matching ..."` and return.
6. Sort by provider then id (Pi `localeCompare`).
7. Compute column widths → print header + rows (6 columns: provider / model / context / max-out / thinking / images).
8. `formatTokenCount(n)`: 200000 → "200K", 1500000 → "1.5M", trailing `.0` stripped.

Pi dispatch site: `main.ts:624-627` — `if (parsed.listModels !== undefined)` → `await listModels(...)` → `process.exit(0)`.

### P-416 — Current Aelix `--list-models` site
`cli/entry.py:162-168` emits stderr "deferred" diagnostic. W2 REPLACES this with `await list_models(...)` call (build ModelRegistry instance same way as model_resolver wires it).

### P-417 — Aelix `ModelRegistry` surface (RESOLVED pre-W2)
Concrete class at `packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py:93` (NOT the Protocol at `extensions/api.py:170`).
- Constructor: `ModelRegistry(auth_storage: AuthStorage, models_json_path: str | None = None)` — `models_json_path != None` raises NotImplementedError.
- Factories: `ModelRegistry.create(auth_storage, ...)` (line 124), `ModelRegistry.in_memory(auth_storage)` (line 134).
- **For `--list-models` use `get_available()` (line 149)** — Pi parity for `getAvailable()`. NOT `get_all()`. Matches Pi `list-models.ts:42` exactly.
- Helpers: `get_all()` (line 144), `find(provider, model_id)` (line 160), `has_configured_auth(model)` (line 169).
- W2 constructs `ModelRegistry` instance in entry.py before the `--list-models` short-circuit. Dependency: `AuthStorage` — locate via `grep -rn "class AuthStorage" packages/` (likely `AuthStorage.from_default()` or similar factory used by `model_resolver.py` callers).

### P-418 — Pi `--append-system-prompt` flow
- Parser captures `string[]` (Aelix ALREADY done — `args.py:101`).
- Pi routes via `ResourceLoader.getAppendSystemPrompt()` (after `resolvePromptInput` resolves @file paths or returns literal).
- Final assembly in agent-session.ts `_rebuildSystemPrompt` (~lines 1580-1582) joins with `"\n\n"` and appends to base system_prompt.

### P-419 — Aelix minimal text-only divergence (vs full Pi parity)
- Drop @file resolution (defer to ResourceLoader port — separate future sprint).
- Drop auto-discovery of `cwd/.pi/APPEND_SYSTEM.md` and `agentDir/APPEND_SYSTEM.md` (requires ResourceLoader).
- Keep: literal text accumulation + `"\n\n"` join + appended-after-base ordering.
- Document as Aelix-additive divergence in ADR-0090.

### P-420 — `AgentHarnessOptions` field missing
Aelix `AgentHarnessOptions` has NO `append_system_prompt` field today (Agent #3 confirmed). W2 adds it as `list[str]` with `field(default_factory=list)` at `harness/core.py:200`.

### P-421 — System_prompt assembly site (RESOLVED pre-W2)
Assembly site located: `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:969-973`:
```python
system_prompt = (
    injected.system_prompt
    if injected and injected.system_prompt is not None
    else self._state.system_prompt
)
```
This is in the `process` method's `before_agent_start` hook fallback. Base `system_prompt` lives on `self._state.system_prompt`, set in `AgentHarness.__init__` from `options.system_prompt`.

**BINDING placement decision:** append in `AgentHarness.__init__` when building `AgentState` — compute `options.system_prompt + "\n\n" + "\n\n".join(options.append_system_prompt)` (when non-empty) and store on `_state.system_prompt` ONCE. Pi semantics differ (Pi rebuilds on reload via `_rebuildSystemPrompt`) but Aelix 6h₇a has no reload trigger for append-system-prompt in scope, so init-time placement is semantically equivalent for the supported lifecycle. Document as Aelix-additive divergence in ADR-0090.

### P-422 — Pyright baseline
8 errors all from `scripts/pyright_spike.py` (test fixture, NOT production). DO NOT touch.

---

## §A — Scope LOC table (~255-335 prod + ~160-210 test)

| Item | Files | Prod | Test |
|---|---|---|---|
| §B fuzzy port | util/fuzzy.py | ~120-150 | ~80-100 |
| §C list-models | cli/list_models.py + entry.py wire | ~120-160 | ~50-70 |
| §D append-system-prompt wire | core.py + entry.py | ~15-25 | ~30-40 |
| §F ADR-0090 + amends | 3 docs | (docs) | (docs) |
| **Total prod** | | **~255-335** | **~160-210** |

---

## §B — Pi `tui/src/fuzzy.ts` → `util/fuzzy.py` port

**Pi source:** `tui/src/fuzzy.ts` (137 LOC TS, hand-rolled stdlib only).
**Target:** `packages/aelix-coding-agent/src/aelix_coding_agent/util/fuzzy.py` (~120-150 LOC Python).

**Constraints:**
- stdlib ONLY (no `fuzzywuzzy`, no `difflib`).
- Mirror Pi algorithm exactly — scoring constants are load-bearing.

**Public API:**
```python
@dataclass(frozen=True)
class FuzzyMatch:
    matched: bool
    score: float
    indices: list[int]  # matched char indices into text

def fuzzy_match(query: str, text: str) -> FuzzyMatch: ...

def fuzzy_filter(
    items: list[T],
    query: str,
    get_text: Callable[[T], str],
) -> list[T]: ...
```

**Scoring constants (exact mirror — DO NOT alter):**
| Event | Score delta |
|---|---|
| Exact match | -100 |
| Word boundary char (` `, `-`, `_`, `.`, `/`, `:`) preceding match | -10 |
| Consecutive match | -5 |
| Gap between matches | +2 |
| Position (i-th matched char) | +0.1 × i |
| Alphanumeric swap fallback (e.g., `codex52` → `5.2-codex`) | +5 penalty |

**Token AND filtering:** `fuzzy_filter` whitespace-splits query → each token must `fuzzy_match` on text → final score is sum.

**Sort:** ascending total score (lower = better match).

---

## §C — Pi `cli/list-models.ts` → `cli/list_models.py` port

**Pi source:** `packages/coding-agent/src/cli/list-models.ts` (111 LOC TS).
**Target:** `packages/aelix-coding-agent/src/aelix_coding_agent/cli/list_models.py` (~120-160 LOC Python).

**Signature:**
```python
async def list_models(
    model_registry: ModelRegistry,
    search_pattern: str | None = None,
) -> None: ...
```

**Body order (mirror P-415):**
1. Load-error warning (Aelix-additive divergence: plain text to stderr, NO chalk/ANSI — document in ADR-0090).
2. Models from `model_registry.get_available()` (line 149) — Pi parity for `getAvailable()`. NOT `get_all()`.
3. If empty: print inline fallback message (NO auth-guidance import — Pi-internal) and return.
4. If `search_pattern`: filter via `fuzzy_filter(models, search_pattern, get_text=lambda m: f"{m.provider} {m.id}")`.
5. If filtered empty: print `f"No models matching {search_pattern!r}"` and return.
6. Sort by `(provider.lower(), id.lower())` — match Pi `localeCompare` ascending.
7. Compute column widths → print 6-column header + rows (provider / model / context / max-out / thinking / images).
8. `format_token_count(n)`: 200000 → "200K", 1500000 → "1.5M", strip trailing `.0`.

**Wire `--list-models` in entry.py:**
- REPLACE current stderr "deferred" diagnostic (`cli/entry.py:162-168`) with `await list_models(model_registry, parsed.list_models)`.
- Construct `ModelRegistry` via `ModelRegistry.create(auth_storage)` (factory at `model_registry.py:124`). Dependency: `AuthStorage` — W2 locates the canonical construction (`grep -rn "class AuthStorage" packages/` → expect `AuthStorage.from_default()` or equivalent factory matching `model_resolver.py` callers).

---

## §D — `--append-system-prompt` minimal text-only wire

**Field addition** (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:200`):
```python
append_system_prompt: list[str] = field(default_factory=list)
```

**Entry wire** (`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` `_build_harness_options`):
```python
options.append_system_prompt = parsed.append_system_prompt
```

**Assembly site (BINDING)** — `AgentHarness.__init__` when building `AgentState`. Compute the joined prompt ONCE and store on `_state.system_prompt`:
```python
base = options.system_prompt or ""
if options.append_system_prompt:
    base = base + "\n\n" + "\n\n".join(options.append_system_prompt) if base else "\n\n".join(options.append_system_prompt)
self._state = AgentState(system_prompt=base, ...)
```
(W2 picks exact code shape — the contract is: base + `"\n\n"` + `"\n\n".join(append_system_prompt)` lands on `_state.system_prompt` before any `before_agent_start` hook runs. The downstream `process` method fallback at `core.py:969-973` already reads `self._state.system_prompt`, so no further wiring needed.)

**Divergence (BINDING — document in ADR-0090):** Pi rebuilds system_prompt on every reload via `_rebuildSystemPrompt`. Aelix 6h₇a has no reload trigger for `append_system_prompt` in scope, so init-time placement is semantically equivalent for the supported lifecycle.

**Divergences (BINDING — document in ADR-0090):**
- NO `@file` resolution (literal text only; defer to ResourceLoader port).
- NO auto-discovery of `cwd/.pi/APPEND_SYSTEM.md` or `agentDir/APPEND_SYSTEM.md`.
- Keep: `"\n\n"` join + appended-after-base ordering.

---

## §E — Tests

**Tests directory structure (confirmed pre-W2):**
- `tests/cli/` EXISTS — add `test_list_models.py` + `test_append_system_prompt.py` here.
- `tests/harness/` EXISTS — add `test_append_system_prompt.py` here.
- `tests/util/` does NOT exist — W2 creates `tests/util/__init__.py` first, then `tests/util/test_fuzzy.py`.
- Naming pattern: `test_<module>.py` (singular, snake_case).

**NEW `tests/util/__init__.py`** — empty marker.

**NEW `tests/util/test_fuzzy.py`** (~80-100 LOC) — port Pi `tui/test/fuzzy.test.ts` test cases (15 cases per agent #2 report):
- Empty query matches all.
- All query chars must appear in order.
- Case insensitive.
- Word boundary bonus (`gpt-4` matches `gpt`).
- Consecutive bonus beats scattered (`gpt` > `g.p.t`).
- Exact match scores best (-100).
- Alphanumeric swap fallback (`codex52` → `gpt-5.2-codex` with +5 penalty).
- Token AND filtering (`fuzzy_filter` whitespace-split).
- Sort ascending (lower score first).
- Empty result when no match.

**NEW `tests/cli/test_list_models.py`** (~50-70 LOC):
- Table output column structure.
- Sort by `(provider, id)` ascending.
- Fuzzy filter integration (search_pattern reduces rows).
- Empty-result message: `"No models matching ..."`.
- No-models-available inline fallback.
- `format_token_count(200000)` == `"200K"`, `(1500000)` == `"1.5M"`, trailing `.0` stripped.

**EXTEND `tests/cli/test_entry_router.py`** (or NEW `tests/cli/test_list_models_integration.py`):
- Entry.py dispatch site test (REPLACES deferred-error test): `--list-models` triggers `list_models(...)` (not stderr deferred diagnostic).

**NEW `tests/harness/test_append_system_prompt.py`** (~30-40 LOC):
- `AgentHarnessOptions(system_prompt="base", append_system_prompt=["x", "y"])` → final assembled prompt contains `"base\n\nx\n\ny"`.
- Empty `append_system_prompt` → unchanged prompt.

**NEW `tests/cli/test_append_system_prompt.py`** (~20-30 LOC):
- Entry.py wiring: `parsed.append_system_prompt` propagates to `AgentHarnessOptions.append_system_prompt`.

ALL existing tests stay green. Expected: 2077 → ~2092 pass + 1 skipped.

---

## §F — ADR-0090 + ADR-0034/README amends

### NEW ADR-0090 `docs/decisions/0090-sprint-6h7a-phase-5a-iii-alpha.md`
- Title: Sprint 6h₇a Phase 5a-iii-α — List-Models + Append-System-Prompt (Partial Phase 5a-iii)
- Status: Accepted, Date: 2026-05-22
- Decisions per §B/§C/§D
- Pi citations: `tui/src/fuzzy.ts`, `packages/coding-agent/src/cli/list-models.ts`, `main.ts:624-627` (dispatch), agent-session.ts `_rebuildSystemPrompt` (~lines 1580-1582)
- Aelix-additive divergences:
  - List-models warning: plain stderr (no `chalk.yellow` / no ANSI).
  - No auth-guidance import (Pi-internal).
  - `--append-system-prompt`: literal text only — NO `@file` resolution, NO auto-discovery.
- §"Deferred items": ResourceLoader port (carries @file + auto-discovery), SettingsManager full port (Sprint 6h₇b).
- Reference companions: ADR-0089 (Sprint 6h₆ closure — `--list-models` deferred + P-401), ADR-0087 (P-380 reload primitives), ADR-0086 (carry-forward catalog), ADR-0034 (Pi pin).

### AMEND ADR-0034 `docs/decisions/0034-pi-reference-version-pin.md`
Append Sprint 6h₇a row to version-pin table (no Pi pin advance — same SHA, new sprint reference).

### AMEND `docs/decisions/README.md`
Append ADR-0090 row.

---

## §G — Atomic commit plan (EXACTLY 4)

**Commit 1** — `feat(util): port Pi tui/fuzzy.ts → fuzzy.py (Sprint 6h₇a §B)`
- `packages/aelix-coding-agent/src/aelix_coding_agent/util/__init__.py` (NEW if not present)
- `packages/aelix-coding-agent/src/aelix_coding_agent/util/fuzzy.py`
- `tests/util/__init__.py` (NEW if not present)
- `tests/util/test_fuzzy.py`

**Commit 2** — `feat(cli): port Pi cli/list-models.ts → list_models.py + wire entry.py (Sprint 6h₇a §C)`
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/list_models.py`
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` (replace deferred diagnostic with `await list_models()`)
- `tests/cli/test_list_models.py`
- `tests/cli/test_entry_router.py` (extend or replace deferred-error test)

**Commit 3** — `feat(harness): --append-system-prompt text-only wire (Sprint 6h₇a §D)`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (AgentHarnessOptions field + assembly site)
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` (_build_harness_options)
- `tests/harness/test_append_system_prompt.py` (NEW)
- `tests/cli/test_append_system_prompt.py` (NEW)

**Commit 4** — `docs: ADR-0090 (Sprint 6h₇a closure) + ADR-0034/README amends`
- `docs/decisions/0090-sprint-6h7a-phase-5a-iii-alpha.md` (NEW)
- `docs/decisions/0034-pi-reference-version-pin.md` (AMEND — append row)
- `docs/decisions/README.md` (AMEND — append row)

Each commit uses HEREDOC + trailer (no shortcut):
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §H — Verification gates

After each commit:
- `uv run ruff check 2>&1 | tail -2` — clean
- `uv run pyright 2>&1 | tail -3` — 8 baseline preserved (no new)
- `uv run pytest 2>&1 | tail -3` — current 2077 + N new pass, 1 skipped

After C2 smoke:
- `uv run aelix --list-models 2>&1 | head -10` → either table or "No models available" (depending on auth state — both acceptable; verify NOT the deferred stderr).

After C3 smoke:
- Python script: construct `AgentHarnessOptions(system_prompt="base", append_system_prompt=["x", "y"])` → assert final assembled `system_prompt` contains both `x` and `y` joined with `\n\n`.

Final: ~2092 pass + 1 skipped. RPC roster unchanged.

---

## §I — Workflow

W2 executor (sonnet for C1/C3, opus for C2) → W3 verification → W4 code-reviewer → W5 critic → W6 commits (NO push — user pushes).

---

## §J — Out-of-scope (BINDING — 9 non-goals)

1. **NO SettingsManager port** — full standalone port reserved for Sprint 6h₇b. Do NOT add even a stub class.
2. **NO ResourceLoader port** — `--append-system-prompt @file` resolution deferred.
3. **NO image branch** in file_processor — Sprint 6h₈ (Pillow). User confirmed Pillow (a) for next-but-one sprint.
4. **NO `migrations.ts` port** — Sprint 6h₈ or later.
5. **NO session-picker** (`--continue` / `--resume` / `--fork` interactive) — Phase 5b.
6. **NO new RPC commands**.
7. **NO Pi pin advance**.
8. **NO ANSI color** — Pi uses `chalk.yellow` for warning; Aelix prints plain to stderr (Aelix-additive divergence, document).
9. **NO TUI work** — Phase 5b.

---

## §K — Sprint 6h₇b preview (carry-forward)

SettingsManager full port (~1400-1600 LOC standalone). Out-of-scope for 6h₇a. Will own:
- proper-lockfile equivalent (Python `filelock`).
- migrations (4 backward-compat transforms).
- dual-scope (`~/.aelix/agent/settings.json` + `.aelix/settings.json`).
- modification tracking + async write queue.
- `reload()` with deep merge.
- ~80 getters/setters.

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
