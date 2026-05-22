# 0090. Sprint 6h₇a Phase 5a-iii-α — List-Models + Append-System-Prompt (Partial Phase 5a-iii)

Status: Accepted (Sprint 6h₇a / Phase 5a-iii-α / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₆ (ADR-0089) closed the non-interactive CLI shell (Phase
5a-i + 5a-ii) but left two surfaces wired only as stderr deferred
diagnostics:

- `--list-models` (P-401 / ADR-0089 §"Aelix-additive divergences")
  exited with a stderr message pointing at the missing
  `SettingsManager` port.
- `--append-system-prompt` (P-401) was parsed into
  `Args.append_system_prompt: list[str]` but not threaded onto the
  harness.

Sprint 6h₇a is the **partial** Phase 5a-iii cleanup that closes those
two carry-forwards without paying for the full Pi 5a-iii surface
(SettingsManager, ResourceLoader, `@file` resolution, image branch,
migrations, session-picker). The full SettingsManager port is reserved
for Sprint 6h₇b (~1400-1600 LOC standalone — see ADR-0090 §F).

## Decision

### §B — Port Pi `tui/src/fuzzy.ts` → `util/fuzzy.py`

`packages/aelix-coding-agent/src/aelix_coding_agent/util/fuzzy.py`
ports the Pi fuzzy-matching utility verbatim (stdlib-only — no
`fuzzywuzzy`, no `difflib`). Scoring constants are LOAD-BEARING:

| Event | Score delta |
|---|---|
| Exact match | -100 |
| Word boundary char (` `, `-`, `_`, `.`, `/`, `:`) preceding match | -10 |
| Consecutive match | -5 × consecutive_count |
| Gap between matches | +2 × gap |
| Position (i-th matched char) | +0.1 × i |
| Alphanumeric swap fallback (e.g., `codex52` → `52codex`) | +5 penalty |

Public surface: `fuzzy_match(query, text) -> FuzzyMatch` +
`fuzzy_filter(items, query, get_text) -> list[T]`. The dataclass
`FuzzyMatch` exposes `matched: bool` + `score: float` + an
**Aelix-additive** `indices: list[int]` field (Pi does not expose
matched positions; the Aelix shape is forward-compatible for
Phase 5b TUI highlight rendering).

Pi citation: `packages/tui/src/fuzzy.ts:1-137` at SHA
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

### §C — Port Pi `cli/list-models.ts` → `cli/list_models.py`

`packages/aelix-coding-agent/src/aelix_coding_agent/cli/list_models.py`
ports the Pi `listModels` async function and wires it into the
existing `--list-models` short-circuit in `cli/entry.py:168-181`
(REPLACES the Sprint 6h₆ deferred stderr diagnostic).

Body order (mirrors Pi `list-models.ts:30-111`):

1. Surface `ModelRegistry.get_error()` as a stderr warning.
2. Fetch `model_registry.get_available()` (Pi parity for
   `getAvailable()` — NOT `get_all()`).
3. If empty: print inline no-models-available fallback and return.
4. If pattern supplied: `fuzzy_filter(models, pattern, get_text=lambda m: f"{m.provider} {m.id}")`.
5. If filtered empty: print `No models matching "<pattern>"` and return.
6. Sort by `(provider.lower(), id.lower())` ascending (Pi `localeCompare`).
7. Compute column widths → print 6-column header + rows (provider /
   model / context / max-out / thinking / images).

`format_token_count(n)`: 200000 → `"200K"`, 1500000 → `"1.5M"`,
2000000 → `"2M"` (trailing `.0` stripped — matches Pi
`millions % 1 === 0`).

Entry.py wires `AuthStorage(get_agent_dir() / "auth.json")` →
`ModelRegistry.create(auth_storage)` → `await list_models(registry,
parsed.list_models)`. The auth path matches the Sprint 6h₆
`cli.config.get_agent_dir()` Aelix-additive default (`.aelix/agent/`
rather than Pi's `.pi/agent/`).

### §D — Wire `--append-system-prompt` (text-only)

Adds `append_system_prompt: list[str] = field(default_factory=list)`
to `AgentHarnessOptions` (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:200`).
In `AgentHarness.__init__`, when the list is non-empty, joins
elements with `"\n\n"` and appends after the base system prompt
ONCE — the result lands on `_state.system_prompt` before any
`before_agent_start` hook runs:

```python
base_system_prompt = options.system_prompt
if options.append_system_prompt:
    appended = "\n\n".join(options.append_system_prompt)
    base_system_prompt = (
        f"{base_system_prompt}\n\n{appended}"
        if base_system_prompt
        else appended
    )
```

`cli/entry.py:_build_harness_options` propagates
`parsed.append_system_prompt` (a defensive copy via `list(...)`) into
the options dataclass.

## Aelix-additive divergences from Pi (BINDING)

1. **List-models load-error warning** — Pi uses
   `chalk.yellow(...)` to stderr; Aelix prints plain stderr text
   (no ANSI / no `chalk`). The Pi `chalk` dependency lands with
   Phase 5b TUI; Aelix 6h₇a stays ANSI-free.
2. **List-models no-models-available message** — Pi delegates to
   `formatNoModelsAvailableMessage()` in `core/auth-guidance.ts`.
   That helper has not been ported; Aelix prints the inline string
   `"No models available. Run 'aelix auth' to configure a provider."`
   so 6h₇a stays self-contained.
3. **`--append-system-prompt` literal-text only** — Pi resolves
   `@file` paths via `ResourceLoader.getAppendSystemPrompt()` and
   auto-discovers `cwd/.pi/APPEND_SYSTEM.md` +
   `agentDir/APPEND_SYSTEM.md`. Aelix 6h₇a accepts literal text only;
   `@file` resolution + auto-discovery defer to the ResourceLoader
   port (separate future sprint).
4. **`--append-system-prompt` init-time assembly** — Pi rebuilds the
   system prompt on every reload via
   `agent-session.ts:_rebuildSystemPrompt` (~lines 1580-1582). Aelix
   6h₇a has no reload trigger for `append_system_prompt` in scope, so
   init-time placement on `_state.system_prompt` is semantically
   equivalent for the supported lifecycle.
5. **`FuzzyMatch.indices`** — Pi exposes only `matches` + `score`.
   Aelix adds an `indices: list[int]` field carrying the matched-char
   positions into `text` for downstream highlight rendering. Empty
   when `matched=False`; forward-compatible no-op for the current
   list-models consumer. **Caveat (W5 §MINOR-1):** for the
   alphanumeric-swap fallback branch (e.g., `"codex52"` matching
   `"gpt-5.2-codex"`), `indices` reflect the SWAPPED-query order, not
   the original query order. Phase 5b TUI highlight rendering must
   account for this when it consumes the field — re-derive the
   original-order indices from the swap mapping at render time, or
   accept that highlights cluster by `digits+letters` grouping for
   swap matches.
6. **`list_models.search_pattern: str | bool | None` tri-state** —
   spec §C declares `str | None`. Implementation widens to
   `str | bool | None` so it can accept `Args.list_models: str |
   bool | None` directly (`True` = pattern absent, no filter applied).
   `True` is coerced to "no filter" at the boundary; semantically
   equivalent to `None`. Aelix-additive ergonomic widening to avoid a
   coerce-at-callsite step in `entry.py`.

## Tests

16 new + 1 rewritten tests across §B / §C / §D:

- `tests/util/test_fuzzy.py` — 16 tests (13 Pi-parity cases ported
  from `tui/test/fuzzy.test.ts` — 8 `fuzzyMatch` + 5 `fuzzyFilter` —
  plus 1 Aelix-additive `whitespace-only query returns all unchanged`
  test + 2 Aelix-additive `indices` assertions). The Aelix-additive
  whitespace test guards the Python `str.split()` empty-token edge
  case (Pi `string.split(/\s+/)` filters empties differently); the
  `indices` tests cover the new `FuzzyMatch.indices` field.
- `tests/cli/test_list_models.py` — 14 tests
  (`format_token_count` × 6 + `list_models` × 8 covering table
  shape / sort / fuzzy filter / empty result / no-models-available
  fallback / load-error warning / `True`/`None` pattern / images
  column).
- `tests/cli/test_entry_router.py::test_list_models_invokes_list_models_and_exits_0`
  — REWRITE of the Sprint 6h₆ deferred-error test; now asserts the
  wired path exits 0 and emits either the inline fallback or the
  table header (NOT the deferred `SettingsManager` stderr
  diagnostic).
- `tests/harness/test_append_system_prompt.py` — 5 tests (joined
  assembly / empty base / single chunk / empty append / default
  factory list-safety).
- `tests/cli/test_append_system_prompt.py` — 3 tests
  (propagation / empty default / defensive copy).

Verification: 2078 baseline (collected) + ~15 new = ~2093 collected;
pyright 8 baseline errors preserved (all from `scripts/pyright_spike.py`
intentional fixture); ruff clean.

## Deferred items (binding carry-forward)

| Item | Owner | Deferred to |
|---|---|---|
| `SettingsManager` full standalone port (~1400-1600 LOC) | ADR-0090 | Sprint 6h₇b |
| `ResourceLoader` port (carries `@file` + auto-discovery) | ADR-0090 | Sprint 6h₇b or later |
| `--append-system-prompt @file` resolution | ADR-0090 | with ResourceLoader |
| Image branch in `file_processor` (Pillow) | ADR-0089 | Sprint 6h₈ |
| `migrations.ts` port | ADR-0089 | Sprint 6h₈ or later |
| Session-picker (`--continue` / `--resume` / `--fork` interactive) | ADR-0089 | Phase 5b |
| ANSI / `chalk.yellow` warnings | ADR-0090 | Phase 5b TUI |
| Interactive TUI | ADR-0088 | Phase 5b |

## Reference companions

- **ADR-0089** — Sprint 6h₆ Phase 5a-i + 5a-ii closure (`--list-models`
  deferred entry, P-401 append-system-prompt parsed but unwired).
- **ADR-0087** — Sprint 6h₅d non-UI cleanup (P-380 `reload()` 5-primitive
  ledger; informs why init-time `--append-system-prompt` placement is
  acceptable in 6h₇a — no reload trigger in scope).
- **ADR-0086** — A 단계 closure (carry-forward catalog).
- **ADR-0088** — Phase 5b TUI library decision (analysis basis; Pi
  `chalk.yellow` lands with the TUI library selection).
- **ADR-0034** — Pi reference version pin (Sprint 6h₇a row added —
  no SHA advance).

## Phase

Sprint 6h₇a / Phase 5a-iii-α / B 단계 (shipped).
