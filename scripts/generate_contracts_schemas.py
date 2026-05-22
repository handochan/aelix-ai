#!/usr/bin/env python3
"""Generate JSON Schemas from Aelix contract Pydantic models.

ADR-0096 §"Validation responsibility" — Pydantic v2 ``model_json_schema()`` is
the source of truth. This script emits the schemas as committed artifacts in
``docs/contracts/`` so cross-repo consumers (aelix-web Phase 6) can fetch
them without a Python dependency.

Idempotency: re-running produces no diff if models are unchanged. CI uses
``--check`` to fail on drift.

Usage:
    python scripts/generate_contracts_schemas.py            # write/update files
    python scripts/generate_contracts_schemas.py --check    # exit 1 if drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
    ActionDescriptor,
    BadgePrimitive,
    DescriptorEnvelope,
    FormPrimitive,
    GatePrimitive,
    GridPrimitive,
    MetricPrimitive,
    PluginManifest,
    TablePrimitive,
    TextPrimitive,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "contracts"


def build_primitives_schema() -> dict[str, Any]:
    """Compose all 8 primitives into a single $defs schema."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Aelix UI Primitives",
        "description": (
            "ADR-0095 §UI primitives — schema for the 8 UI primitives that "
            "descriptor payloads compose."
        ),
        "$defs": {
            "TextPrimitive": TextPrimitive.model_json_schema(),
            "BadgePrimitive": BadgePrimitive.model_json_schema(),
            "MetricPrimitive": MetricPrimitive.model_json_schema(),
            "TablePrimitive": TablePrimitive.model_json_schema(),
            "GridPrimitive": GridPrimitive.model_json_schema(),
            "FormPrimitive": FormPrimitive.model_json_schema(),
            "GatePrimitive": GatePrimitive.model_json_schema(),
            "ActionDescriptor": ActionDescriptor.model_json_schema(),
        },
    }


def build_slot_taxonomy_schema() -> dict[str, Any]:
    """Static doc-style schema for the 8-slot taxonomy v1."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Aelix Slot Taxonomy v1",
        "description": (
            "ADR-0095 §8-slot taxonomy v1 — static metadata for descriptor "
            "kinds: multiplicity + payload tier. The actual descriptor schema "
            "is in descriptor-envelope.schema.json."
        ),
        "type": "object",
        "properties": {
            "api_level": {"type": "integer", "const": AELIX_API_LEVEL},
            "slots": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    slot: {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "multiplicity": {
                                "type": "string",
                                "enum": ["one", "one-active", "many"],
                            },
                            "payload_tier": {
                                "type": "string",
                                "enum": [
                                    "descriptor-only",
                                    "react-or-descriptor",
                                    "react-only",
                                ],
                            },
                        },
                        "required": ["multiplicity", "payload_tier"],
                    }
                    for slot in SLOT_MULTIPLICITY
                },
            },
        },
    }


def serialize(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def main() -> int:
    doc = __doc__ or ""
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Exit 1 on drift")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Inject slot taxonomy metadata into the slot-taxonomy schema content.
    slot_schema = build_slot_taxonomy_schema()
    # Populate concrete slot values so the emitted artifact is informative.
    slot_data_object = {
        "api_level": AELIX_API_LEVEL,
        "slots": {
            slot: {
                "multiplicity": SLOT_MULTIPLICITY[slot],
                "payload_tier": SLOT_PAYLOAD_TIER[slot],
            }
            for slot in SLOT_MULTIPLICITY
        },
    }
    slot_schema["examples"] = [slot_data_object]

    files: dict[str, str] = {
        "manifest.schema.json": serialize(PluginManifest.model_json_schema()),
        "descriptor-envelope.schema.json": serialize(DescriptorEnvelope.model_json_schema()),
        "primitives.schema.json": serialize(build_primitives_schema()),
        "slot-taxonomy.schema.json": serialize(slot_schema),
    }

    repo_root = OUTPUT_DIR.parent.parent
    drift = False
    for name, content in files.items():
        path = OUTPUT_DIR / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                print(f"[drift] {path.relative_to(repo_root)}", file=sys.stderr)
                drift = True
        else:
            path.write_text(content, encoding="utf-8")
            print(f"[wrote] {path.relative_to(repo_root)}")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
