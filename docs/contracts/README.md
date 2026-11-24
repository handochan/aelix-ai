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
| `primitives.schema.json` | `TextPrimitive`, `BadgePrimitive`, `MetricPrimitive`, `TablePrimitive`, `GridPrimitive`, `FormPrimitive`, `GatePrimitive` and `ActionDescriptor` in shared `$defs`, with their supporting definitions | Tier 2 descriptor host renderers, Phase 6 Web slots |
| `slot-taxonomy.schema.json` | `SLOT_MULTIPLICITY` + `SLOT_PAYLOAD_TIER` | Slot registry validators (host + Phase 6 aelix-web) |
| `control-v1.md` | none: hand-written, not generated (`scripts/generate_contracts_schemas.py` writes only the four schemas above) | aelix-acp and Aelix Studio: who reads which part of the `aelix_control` runtime-control API, version 1 (ADR-0302). `tests/rpc/control_v1/test_consumer_list.py` fails while it and `tests/rpc/control_v1/fixtures` or `floor_v1.json` disagree |

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

The workspace's local single-user server serves the committed artifacts at
`GET /schemas/{name}` (`aelix_server/schemas.py`). It defaults to localhost
(`aelix_server/config.py`) and the WebSocket RPC service allows one active
connection (`aelix_server/rpc_ws.py`).

External consumers can fetch the schema files from a pinned source revision
and validate them with JSON Schema. Multi-tenancy is part of the Phase 6 design
in [ADR-0097](../decisions/0097-multi-frontend-architecture.md).

## Current API level

Sprint 6h₉a baseline: **API level 1** (`AELIX_API_LEVEL = 1`).
