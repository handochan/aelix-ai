# Aelix Contracts

This directory contains the JSON Schema artifacts for Aelix's cross-surface
contract layer (ADR-0094 §"4-tier extension model", ADR-0095 §"UI Descriptor
Protocol", ADR-0096 §"Manifest v1", ADR-0097 §"Multi-Frontend Architecture").

## Source of truth

The Pydantic v2 models in
`packages/aelix-agent-core/src/aelix_agent_core/contracts/` are the source
of truth. The JSON Schemas in this directory are **generated artifacts** —
do NOT hand-edit.

## Files

| File | Source model | Consumer |
|---|---|---|
| `manifest.schema.json` | `PluginManifest` | aelix-plugin.toml validators (host + IDE + marketplace) |
| `descriptor-envelope.schema.json` | `DescriptorEnvelope` | TUI/Web descriptor host renderers, cross-repo validation |
| `primitives.schema.json` | composite ($defs of 8 primitives + ActionDescriptor) | Tier 2 descriptor host renderers, Phase 6 Web slots |
| `slot-taxonomy.schema.json` | `SLOT_MULTIPLICITY` + `SLOT_PAYLOAD_TIER` | Slot registry validators (host + Phase 6 aelix-web) |

## Regeneration

```sh
python scripts/generate_contracts_schemas.py
```

The script is idempotent; re-running produces no diff if the Pydantic
models are unchanged.

## CI drift detection

CI runs:

```sh
python scripts/generate_contracts_schemas.py --check
```

This exits non-zero if any generated schema differs from the committed
artifact. Local fix:

```sh
python scripts/generate_contracts_schemas.py     # write current schemas
git add docs/contracts/*.schema.json
git commit -m "chore(contracts): regenerate JSON Schemas"
```

## Versioning policy

See ADR-0095 §"Versioning policy" and ADR-0096 §"API_LEVEL policy".

- Adding optional fields = minor (non-breaking).
- Renaming/removing required fields = major (breaking).
- Adding new descriptor kinds (`DescriptorKind` Literal members) = minor.
- Renaming/removing descriptor kinds = major.
- Schema changes are accompanied by an `AELIX_API_LEVEL` bump for breaking
  changes.

## Cross-repo consumption

Phase 6 `aelix-web` (separate repo per ADR-0097) consumes these schemas:

1. **Build-time**: fetch from a pinned Aelix release tarball / npm artifact.
2. **Runtime**: `GET /schemas/{name}` endpoint on aelix-server (Phase 6
   multi-tenant gateway).

JSON Schema is the lingua franca; aelix-web does not need a Python
dependency.

## Current API level

Sprint 6h₉a baseline: **API level 1** (`AELIX_API_LEVEL = 1`).
