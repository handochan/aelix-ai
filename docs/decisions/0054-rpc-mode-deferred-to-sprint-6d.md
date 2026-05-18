# 0054. RPC Mode — Deferred to Sprint 6d

Status: Accepted (Sprint 6c / Phase 4.3 / W6 — formal carry-forward record)

## Context

ADR-0020 (Draft) introduced `aelix mode rpc` — stdin/stdout JSONL
protocol mirroring Pi `--mode rpc`. ADR-0009 forward-referenced this
as the multi-language client story.

W0 measurement at Pi SHA `734e08e`:

| Surface | Pi LOC | Python est |
|---|---|---|
| `coding-agent/src/modes/rpc/rpc-mode.ts` | ~400 | ~300 |
| `coding-agent/src/modes/rpc/rpc-client.ts` | ~350 | ~250 |
| `coding-agent/src/modes/rpc/rpc-types.ts` | ~200 | ~150 |
| `coding-agent/src/modes/rpc/jsonl.ts` | ~80 | ~70 |
| Tests | ~280 | ~200 |
| **Total** | **~1,310** | **~970 prod + ~600 test** |

Sprint 6c original scope (per Sprint 6a §0 sub-sprint split) was
~700 prod + ~500 test. W0 verified Sprint 6c's actual OAuth+secrets
work alone is ~980 prod + ~700 test — the original estimate was 3.5×
too low. Bundling RPC mode in the same sprint would land ~2,000 prod
LOC and fail W3 verification time-box.

## Decision

RPC mode is **formally deferred to Sprint 6d**.

`aelix_ai.oauth._registry._PHASE_4_DEFERRED_FEATURES` carries:

```python
_PHASE_4_DEFERRED_FEATURES: Final[dict[str, str]] = {
    "rpc-mode": "ADR-0054 — Sprint 6d",
    "auth-storage-layered-resolution": (
        "ADR-0053 — Sprint 6e (runtime-override + env + fallback "
        "resolver per Pi auth-storage.ts:455-516)"
    ),
}
```

The Phase 4.3 closure pin
(`tests/pi_parity/test_phase_4_3_strict_superset.py::test_rpc_mode_in_phase_4_deferred_features`)
asserts the `"rpc-mode"` key + its owning ADR reference.

### Forward-compat clause (binding)

Sprint 6d MUST:

1. Land the four Pi-port modules (`rpc-mode.py`, `rpc-client.py`,
   `rpc-types.py`, `jsonl.py`) under `packages/aelix-coding-agent/`.
2. Drop `"rpc-mode"` from `_PHASE_4_DEFERRED_FEATURES` in the SAME PR
   (closure pin enforces).
3. Write the owning ADR (placeholder: ADR-0056 — "RPC Mode + JSONL
   Protocol").
4. Update ADR-0020 from Draft to Accepted.

## Consequences

- ADR-0020 stays Draft until Sprint 6d lands.
- ADR-0009 multi-language client story is unblocked once RPC mode
  ships in 6d.
- Sprint 6c W6 closure (ADR-0055) explicitly inherits this carry-forward
  in §Carry-forward.

## Related

- ADR-0009 — Python-first SDK (RPC mode is the multi-language story).
- ADR-0020 — RPC Mode (Draft → Sprint 6d).
- ADR-0034 — Pi reference version pin.
- ADR-0055 — Phase 4.3 strict superset closure.

## Phase

Sprint 6c / Phase 4.3 (formal carry-forward — code lands Sprint 6d).
