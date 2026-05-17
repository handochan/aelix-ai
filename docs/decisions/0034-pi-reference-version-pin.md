# 0034. Pi Reference Version Pin

Status: Accepted (Sprint 2.5 shipped)

## Context

ADR-0003 names pi agent as the primary reference but doesn't pin a version.
As Pi evolves on `main`, Aelix line citations drift and parity-audit
reproducibility breaks. Every Phase 1.x ADR that quotes Pi line numbers (e.g.
ADR-0017's "Pi `AgentHarnessEvent` at `types.ts:467-469`", ADR-0021's
"`packages/agent/src/harness/agent-harness.ts:369,381,391`") is silently
anchored to whatever SHA the contributor happened to read at authoring time.
Without an explicit pin, a critic-pass three weeks later can find the cited
line moved 20 lines down, breaking the audit chain.

## Decision

Pin Pi to a specific commit SHA per sprint.

**Current pin: `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`**
(`main` HEAD as of 2026-05-17, commit message "chore: approve contributor
mattiacerutti").

Update policy:

1. Each new sprint that imports new Pi features MAY move the pin forward.
2. The sprint spec MUST cite the new SHA in its preamble.
3. Every ADR that quotes Pi MUST cite the SHA (either inline or by reference
   to this ADR's "current pin").
4. When the pin moves, the previous pin's SHA is appended to the "Pin history"
   table below for traceability.

### Pin history

| Sprint | Pin SHA | Date | Reason |
|--------|---------|------|--------|
| 2.5 (Phase 1.4) | `734e08edf82ff315bc3d96472a6ebfa69a1d8016` | 2026-05-17 | initial pin; spec citations anchored |

## Consequences

- Parity audits become reproducible — the W5 audit lane can `git checkout`
  the pinned SHA to validate every Pi citation.
- Forward-port effort becomes visible per-sprint as the delta between the
  previous and new pin.
- Existing ADRs (0017, 0018, 0019, 0021, 0022, 0023, 0025) are silently
  anchored to this SHA going forward; if a quote breaks against a newer SHA,
  that's a Phase 2.x action item, not a Phase 1.4 bug.
- Phase 2.1 specs MAY introduce a `PI_PIN` constant in `pyproject.toml` or
  `docs/` to make the pin machine-readable for future tooling — out of scope
  for Phase 1.4.

## Related

- ADR-0003 — pi agent as primary reference (this ADR refines the binding).
- ADR-0029 — Pi-parity acceptance test harness (will consume this pin once
  vendored fixtures are introduced).
- ADR-0032 — Sprint workflow review + Pi parity audit (W5 audit consumer).

## Phase

Sprint 2.5 / Phase 1.4 (shipped).
