# 0086. A 단계 closure — Phase 4 RPC + extension events + runtime callbacks + visual fidelity COMPLETE

Status: Accepted (Sprint 6h₅c / Phase 4.16 / W6 shipped — **A 단계 CLOSED**)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

A 단계 (the "Phase 4 strict Pi-parity superset" stage) opened with Sprint
6a (ADR-0045 / ADR-0046) and the goal of bringing the Aelix agent core to
byte-identical Pi parity for the streaming + provider + OAuth + RPC +
extension event + runtime callback + visual fidelity surfaces at SHA
`734e08e`. ADR-0080 (Sprint 6h₄c) records **Phase 4 RPC ROSTER CLOSED**
at 29 / 0 / 29 with the 3 final session-tree handler wires
(`switch_session` / `fork` / `clone`). ADR-0082, ADR-0084, and ADR-0085
record the extension-event + runtime-callback + visual-fidelity polish
sprints that close the remaining carry-forward items on top of that
closed RPC roster.

This ADR records the **A 단계 closure milestone**: every binding item
enumerated as carry-forward across the A-stage sprint chain has a paired
delivering sprint + owner ADR. The Phase 4 RPC discriminator union is
fully wired; the 4 extension session lifecycle events emit Pi-parity
end-to-end; the 3 runtime callback surfaces (`with_session` / `setup` /
`forkFrom`) ship; the HTML emitter renders with full visual fidelity +
inline image data URIs; and `_get_context_usage_safe` returns Pi-shape
results both at the harness level and through the extension-context
sync bridge.

## Decision

### A 단계 closure ledger

Every A 단계 carry-forward item from ADR-0080 / ADR-0082 / ADR-0084 is
paired below with the delivering Sprint + ADR.

| Carry-forward item | Sprint | Owner ADR |
|---|---|---|
| Phase 4 RPC 29/29 (full `RpcCommand` discriminator union wired) | 6h₃ ~ 6h₄c | ADR-0073 ~ ADR-0080 |
| P-307 `session_shutdown` extension event emit from `AgentHarness.dispose` | 6h₅a | ADR-0081 |
| P-308 Real `session_before_switch` / `session_before_fork` cancel hooks | 6h₅a | ADR-0081 |
| `with_session` 2-stage callback on 3 replace APIs | 6h₅b | ADR-0083 |
| `setup` callback in `new_session` | 6h₅b | ADR-0083 |
| `forkFrom` cross-cwd import (`JsonlSessionRepo.fork_from`) | 6h₅b | ADR-0083 |
| `import_from_jsonl` real runtime body | 6h₅b | ADR-0083 |
| P-351 `ExtensionRunner.invalidate` semantics + runtime bridge | 6h₅b | ADR-0083 |
| `_get_context_usage_safe` real implementation (P-282 from ADR-0074) | 6h₅c | ADR-0085 |
| `_ExtensionContext.get_context_usage` real sync bridge (P-374) | 6h₅c | ADR-0085 |
| Factory bootstrap `assertSessionCwdExists` (Pi `:391`) | 6h₅c | ADR-0085 |
| Bootstrap `session_start(reason="startup")` emit | 6h₅c | ADR-0085 |
| HTML visual fidelity (CSS framework + syntax highlighting + markdown) | 6h₅c | ADR-0085 |
| `ImageContent` HTML rendering (inline base64 data URI) | 6h₅c | ADR-0085 |

### A 단계 invariants preserved at closure

- **Phase 4 RPC roster CLOSED at 29 / 0 / 29.** The `RpcCommand`
  discriminator union at SHA `734e08e` is fully wired.
  `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` (full set equality);
  `DEFERRED_COMMANDS == {}` (literal empty). Pinned by
  `tests/pi_parity/test_phase_4_13_strict_superset.py`.
- **35-name `HookEventName` cascade preserved.** Sprint 6h₅a widened
  the cascade from 31 → 35 (adding `session_start` /
  `session_before_switch` / `session_before_fork` /
  `session_shutdown`); subsequent sprints have not changed the count.
  Pinned by `tests/pi_parity/test_phase_4_14_extension_events.py`.
- **35-overload `HookBus.on` / `ExtensionAPI.on` count preserved.**
  Sprint 6h₅a widening; subsequent sprints unchanged.
- **`ReplacedSessionContext` Protocol at 19 members.** Sprint 6h₅b
  P-364 W5 MAJOR widening (13 → 19) adds the 6
  `ExtensionCommandContext` methods per Pi `extensions/types.ts:371`.
- **PI_STALENESS_MESSAGE Pi-verbatim single source of truth.** Sprint
  6h₅b `runner.ts:467` verbatim string is shared across
  :class:`ExtensionRunner` + :class:`_ExtensionRuntime` + runtime
  bridge.
- **EMIT → INVALIDATE → DISPOSE ordering in `_teardown_current` AND
  `dispose`.** Sprint 6h₅a P-355 W5 BLOCKING correction (uniform Pi
  ordering; the W2 §J "intentional asymmetry" claim was refuted as a
  Pi misread).

### B 단계 (next phase) candidate scope

A 단계 closure unlocks **B 단계** — the next strict-Pi-parity sprint
chain. ADR-0086 does NOT formally open B 단계 (that's a Sprint 6h₅d /
6i + ADR-0087+ decision); the candidate scope is enumerated below for
future planning:

- **Phase 5 — CLI / runner mode (candidate scope).** Pi
  `coding-agent` CLI surfaces (`/branch-from`, `/import`, TUI tree
  navigation, `reload()` primitive) that consume the runtime
  surfaces wired in A 단계 but have no internal consumer yet. The
  Aelix-additive equivalents (`aelix` CLI + future TUI) live here.
- **Image-models parallel registry.** Pi `image-models.ts` +
  `image-models.generated.ts`. Tracked since Sprint 6g₁ (ADR-0068).
- **Typed `Model.compat` discriminated union.** Tracked since Sprint
  6h₁ (ADR-0070).
- **`enableGitHubCopilotModel` POST automation.** Tracked since
  Sprint 6e (ADR-0063).
- **`Model.knowledgeCutoff` / `releaseDate` Pi-untyped runtime
  fields.** Tracked since Sprint 6g₁ (ADR-0068).

### Sprint 6h₅d carry-forward (from ADR-0085)

Visual-polish + grep-tooling items deliberately scoped out of Sprint
6h₅c per ADR-0085:

- ANSI → HTML pipeline (Pi `ansi-to-html.ts`).
- Tool-renderer per-tool templates (bash / read / write / edit / ls).
- Client-side JS port (sidebar / tree navigation).
- Pi color-derivation math (luminance-based theme).
- `reload()` bootstrap emit branch (`reason="reload"` per Pi
  `:2401`).
- Pixel-perfect HTML closure pin tests.
- P-375 monkeypatch fragility in
  `tests/test_factory_assert_session_cwd.py`.
- MINOR-1 f-string assembly polish in `_export_html/format.py`.
- MINOR-3 `harness._session` private-attribute reads.
- Live `session_id` read via session manager (P-291).
- Pi-source-grep verification tooling (P-286).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₅b (start of 6h₅c) | 29 | 0 | 29 |
| Sprint 6h₅c (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED at A 단계 closure.** No new commands; no
dispatch impact. The discriminator union at SHA `734e08e` is fully
wired.

## Consequences

- **A 단계 CLOSED.** The 14-row ledger above pairs every binding
  carry-forward item in ADR-0080 + ADR-0082 + ADR-0084 with a
  delivering sprint + ADR. The forward-compat clause from each
  closure ADR continues to hold — any PR that touches the RPC
  dispatch table MUST move items between SUPPORTED / DEFERRED in
  lockstep, enforced by the cascade closure pin tests.

- **B 단계 candidate scope enumerated.** Phase 5 / CLI / runner-mode
  + 4 lower-priority tracked items. ADR-0087+ formally opens B 단계
  when the next sprint chain begins.

- **Sprint 6h₅d carry-forward scoped.** Pure visual-polish + grep-
  tooling + minor cleanups; no RPC dispatch impact. Tracked in
  ADR-0085 §"Carry-forward to Sprint 6h₅d".

- **Closure pin cascade preserved.** No new pin file lands in this
  ADR (no new HookEventName / no new RPC command); the existing pin
  chain (4.10 → 4.11 → 4.12 → 4.13 → 4.14 → 4.15) plus the unit-test
  invariants for runtime callbacks + visual fidelity continue to
  guard the A 단계 surface.

- **Pi pin advances permitted starting B 단계.** ADR-0034 update
  policy allows moving the pin forward when a new sprint imports
  new Pi features. A 단계 sprints have consistently held to
  `734e08e`; B 단계 (Phase 5) is the natural pin-advance window.

## References

- ADR-0073 — Sprint 6h₃ session stats + HTML export wire port.
- ADR-0074 — Sprint 6h₃ Phase 4.10 strict-superset closure (P-280 /
  P-282 / P-283 carry-forward items closed in Sprint 6h₅c).
- ADR-0075 — Sprint 6h₄a session navigation read-only RPC commands.
- ADR-0076 — Sprint 6h₄a Phase 4.11 strict-superset closure.
- ADR-0077 — `AgentSessionRuntime` Pi port + `rebindSession` seam
  (Sprint 6h₄b foundation).
- ADR-0078 — Sprint 6h₄b Phase 4.12 strict-superset closure +
  Sprint 6h₄c wiring carry-forward.
- ADR-0079 — Session-tree handlers wired (Sprint 6h₄c) — **PHASE 4
  RPC CLOSURE**.
- ADR-0080 — Sprint 6h₄c Phase 4.13 strict-superset closure + Phase
  4 RPC roster CLOSED.
- ADR-0081 — Sprint 6h₅a extension event Pi parity (4 events +
  session_cwd + W5 P-355 dispose ordering).
- ADR-0082 — Sprint 6h₅a Phase 4.14 strict-superset closure +
  6h₅b / 6h₅c carry-forward (6h₅b items closed in ADR-0084;
  6h₅c items closed in ADR-0085 / this ADR).
- ADR-0083 — Sprint 6h₅b runtime callback Pi parity.
- ADR-0084 — Sprint 6h₅b Phase 4.15 strict-superset closure +
  6h₅c carry-forward (all 5 items CLOSED per ADR-0085 / this ADR
  — amended this sprint).
- ADR-0085 — Sprint 6h₅c Phase 4.16 visual fidelity + context_usage
  + bootstrap session_start + factory cwd + ImageContent (sibling
  ADR — records the per-item closure decisions).
- ADR-0034 — Pi pin (amended Sprint 6h₅c row this sprint).
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅c / Phase 4.16 / W6 (shipped — **A 단계 CLOSED**;
Phase 4 RPC roster STAYS CLOSED at 29 / 0 / 29; B 단계 candidate
scope enumerated; Sprint 6h₅d carry-forward scoped to visual polish
+ Pi grep tooling + minor cleanups).
