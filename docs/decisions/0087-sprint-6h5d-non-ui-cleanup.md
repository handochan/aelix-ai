# 0087. Sprint 6h₅d Phase 4.17 — Non-UI Carry-Forward Closure

Status: Accepted (Sprint 6h₅d / Phase 4.17 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₅c (ADR-0085) closed the 5 binding visual-fidelity + bootstrap
+ context-usage items and ADR-0086 recorded the A 단계 closure ledger.
The Sprint 6h₅c carry-forward roster in ADR-0085 §"Carry-forward to
Sprint 6h₅d" enumerated 10 items split between UI surfaces (ANSI → HTML,
tool-renderer templates, sidebar JS, color-derivation math,
pixel-perfect HTML pins) and non-UI cleanups (P-375 monkeypatch
fragility, MINOR-1 f-string assembly polish, MINOR-3 `harness._session`
private reaches, Pi `reload()` bootstrap emit, live `session_id` read,
Pi-source-grep tooling).

Sprint 6h₅d scope is **non-UI carry-forward only**. UI items are
deliberately deferred to a Phase 5 / B 단계 sprint that ports the
interactive-mode surface as a whole (per user consultation gate — the
UI items consume primitives that B 단계 will also revisit). The Pi
`reload()` bootstrap emit was further studied and re-deferred per W0
finding P-380 (see §"Deferred items" below).

## Decision

### §C — P-375 monkeypatch fragility CLOSES (commit `b975197`)

`tests/test_factory_assert_session_cwd.py` previously patched
`session.session_cwd.assert_session_cwd_exists` via a manual
`try/finally` block. It worked only because
`runtime/agent_session_runtime.py:create_agent_session_runtime`
re-imported the helper inside the function body, so the patched module
attribute was re-resolved per call. Any future hoist would silently
break the test.

W2 lifted the `SessionStartHookEvent` + `assert_session_cwd_exists`
imports to module top-level (single binding site on
`runtime.agent_session_runtime`) and the test migrated to
`monkeypatch.setattr(_mod, "assert_session_cwd_exists", ...)` against
that single binding site. The `AgentSessionRuntime.__init__` spy also
moves to `monkeypatch.setattr` for symmetry. No import cycle: grep
confirms `session/session_cwd.py` has zero `runtime.*` imports.

### §D — MINOR-1 `template.py` f-string → string concat CLOSES (commit `a34aee7`)

ADR-0085 / ADR-0086 cited the f-string in `_export_html/format.py`;
W0 P-383 corrected the file to `_export_html/template.py` — the 196-line
`_THEME_CSS = f"""..."""` constant at `:45-240` with a single
`{_PYGMENTS_CSS}` interpolation site. The brace-doubling forced on every
CSS literal was the polish target.

W2 splits the f-string into two plain string constants concatenated with
the runtime-computed Pygments stylesheet:

    _THEME_CSS = _BASE_THEME_CSS + "\n" + _PYGMENTS_CSS + "\n" + _IMAGE_CSS

All CSS literals revert to single braces; the rendered output is
identical (modulo brace de-doubling). NEW
`tests/test_export_html_template_concat.py` (2 tests) locks the
Pygments + base-theme + image-class invariants;
`tests/test_export_html_visual_fidelity.py` (7 tests, unchanged)
continues to pin the renderer-side observable invariants.

### §E — MINOR-3 `AgentHarness.session` public property + 6 migrations CLOSES (commit `1ffe025`)

Pi parity: `runtimeHost.session` (`agent-session-runtime.ts:83-85`).
W0 P-384 catalogued 6 external private-attribute reaches on
`harness._session`:

| File:line | Caller |
|---|---|
| `runtime/agent_session_runtime.py:241` | `RuntimeHost.session` getter |
| `runtime/agent_session_runtime.py:248` | `RuntimeHost.cwd` getter |
| `runtime/agent_session_runtime.py:929` | factory check |
| `runtime/agent_session_runtime.py:931` | factory assert call |
| `rpc/rpc_mode.py:545,551` | `set_session_name` RPC handler |
| `cli/repl.py:64,66` | REPL `user_bash` path |

W2 adds :attr:`AgentHarness.session` (a plain `Session | None`
read-through to `self._session`) and migrates all 6 sites. The
`rpc_mode` + `repl` callers narrow once via a local binding so the
subsequent operation needs no second probe. Internal `harness/core.py`
reads continue using `self._session` directly (a class accessing its
own private attribute is canonical Python).

NEW `tests/harness/test_session_property.py` (2 tests) locks the
bound-and-unbound invariants. Grep gate:

    grep -rn "harness\._session" \
        packages/aelix-coding-agent/ \
        packages/aelix-agent-core/src/aelix_agent_core/runtime/

returns zero matches (exit 1) at sprint close.

## Deferred items

### Pi `reload()` bootstrap emit re-DEFERRED to Phase 5

W0 P-380 audited Pi `reload()` (`agent-session.ts:2383-2404`) — 6
primitives, 5 of which are missing in Aelix:

  1. `settingsManager.reload()` — Aelix has no global settings manager.
  2. `resetApiProviders()` — Aelix's provider registration is per-harness.
  3. `_resourceLoader.reload()` — Aelix's resource loader has no reload entry point.
  4. `flagValues` round-trip (`getFlagValues()` + restoring after `_buildRuntime`) — Aelix has no per-runtime flag values.
  5. `_buildRuntime({...})` — Aelix uses harness-rebuild (ADR-0077 P-302) instead of session-swap; there is no equivalent in-place rebuild path.

Only `extendResourcesFromExtensions("reload")` (Pi `:2404`) is wired in
Aelix as :meth:`AgentHarness.reload_resources`. Porting only the Pi
`:2401` `session_start(reason="reload")` emit in isolation would emit
the event over a session that did NOT actually reload — extensions
would observe a lifecycle event with no underlying lifecycle change.
**That divergence violates the binding principle.**

Per the binding principle, the emit is re-deferred to Phase 5 alongside
the 5 missing primitives. ADR-0086 §"Sprint 6h₅d carry-forward" is
amended to mark the `reload()` row "deferred to Phase 5 (re-deferred)"
with this ADR cited as the rationale.

### UI items deferred to Phase 5 / B 단계

Per user consultation gate the following UI items stay deferred:

- ANSI → HTML pipeline (Pi `ansi-to-html.ts`).
- Tool-renderer per-tool templates (bash / read / write / edit / ls).
- Client-side JS port (sidebar / tree navigation).
- Pi color-derivation math (luminance-based theme).
- Pixel-perfect HTML closure pin tests.

### Lower-priority items deferred to B 단계

- Live `session_id` read via session manager (P-291 from ADR-0074).
- Pi-source-grep verification tooling (P-286 from ADR-0074).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₅c (start of 6h₅d) | 29 | 0 | 29 |
| Sprint 6h₅d (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED.** No new commands; non-UI cleanup doesn't
change the dispatch table.

## Consequences

- **3 carry-forward items CLOSED** — P-375 / MINOR-1 / MINOR-3 from
  the ADR-0086 §"Sprint 6h₅d carry-forward" roster. ADR-0086 is
  amended in lockstep (strikethrough rows + cite this ADR).
- **`reload()` re-deferred to Phase 5** — full 5-primitive ledger
  recorded above; ADR-0086 amended in lockstep.
- **A 단계 closure invariants preserved** — Phase 4 RPC roster STAYS
  CLOSED at 29 / 0 / 29; 35-name `HookEventName` cascade unchanged;
  35-overload count unchanged; `ReplacedSessionContext` Protocol stays
  at 19 members; `PI_STALENESS_MESSAGE` single source of truth; uniform
  EMIT → INVALIDATE → DISPOSE ordering preserved.
- **No new closure pin file lands** — no new `HookEventName` literal,
  no new RPC command. The closure pin lane sits on the 3 new + 1
  rewritten unit-test files this sprint:
  - `tests/test_factory_assert_session_cwd.py` (REWRITE — monkeypatch.setattr)
  - `tests/test_export_html_template_concat.py` (NEW — 2 tests)
  - `tests/harness/test_session_property.py` (NEW — 2 tests)
- **Pi pin held at `734e08e`.** Per ADR-0034 update policy, B 단계
  is the natural pin-advance window; the Sprint 6h₅d non-UI closure
  doesn't import any new Pi feature so the pin stays.

## References

- Pi `agent-session-runtime.ts:83-85` — `runtimeHost.session` accessor
  (E binding target).
- Pi `agent-session.ts:2383-2404` — `reload()` body (5-primitive ledger
  in §"Deferred items").
- Pi `agent-session.ts:2401` — `session_start(reason="reload")` emit
  (re-deferred).
- Pi `agent-session-runtime.ts:391` — factory bootstrap
  `assertSessionCwdExists` site (C binding context).
- ADR-0085 — Sprint 6h₅c visual fidelity + context_usage + bootstrap
  session_start + factory cwd + ImageContent (parent ADR with original
  Sprint 6h₅c carry-forward roster — preserved unchanged).
- ADR-0086 — A 단계 closure ledger (amended this sprint —
  strikethrough closed rows + cite this ADR).
- ADR-0034 — Pi pin policy (no advance this sprint).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit gate.

## Phase

Sprint 6h₅d / Phase 4.17 / W6 (shipped — non-UI carry-forward
closure; UI items + `reload()` deferred to Phase 5 / B 단계;
Phase 4 RPC roster STAYS CLOSED at 29 / 0 / 29; A 단계 closure
invariants preserved).
