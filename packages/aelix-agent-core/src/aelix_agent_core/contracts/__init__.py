"""Aelix extension contracts (Sprint 6h₉a / Phase 5b-foundation).

This package contains the Pydantic v2 models that define Aelix's cross-surface
contract layer:

- :mod:`api_level` — ABI version constant + compatibility helper (ADR-0096)
- :mod:`descriptor` — Tier 2 descriptor envelope + 8-kind payload union (ADR-0095)
- :mod:`manifest` — ``aelix-plugin.toml`` v1 schema (ADR-0096)
- :mod:`primitives` — 8 UI primitives composing descriptor payloads (ADR-0095)
- :mod:`slots` — multiplicity + payload-tier metadata for the 8 slots (ADR-0095)

JSON Schemas generated from these models live in ``docs/contracts/`` via
``scripts/generate_contracts_schemas.py``.
"""

from __future__ import annotations

from .api_level import AELIX_API_LEVEL, IncompatibleApiLevelError, assert_compatible
from .descriptor import (
    ActionDescriptor,
    AgentMetricPayload,
    BreadcrumbPayload,
    CommandRoutePayload,
    DescriptorEnvelope,
    DescriptorKind,
    DescriptorPayload,
    FooterSegmentPayload,
    ManagementModalPayload,
    StatusItemPayload,
    ToastPayload,
    ToolRendererDescPayload,
)
from .manifest import (
    LICENSE_WHITELIST,
    Activation,
    Capabilities,
    CommandContrib,
    Contributes,
    DescriptorContrib,
    HookContrib,
    McpServerContrib,
    PluginApi,
    PluginEntry,
    PluginIdentity,
    PluginManifest,
    ThemeContrib,
    ToolContrib,
    TuiWidgetContrib,
    parse_manifest_toml,
)
from .primitives import (
    BadgePrimitive,
    ColumnSpec,
    FieldSpec,
    FormPrimitive,
    GatePrimitive,
    GridItem,
    GridPrimitive,
    MetricPrimitive,
    TablePrimitive,
    TextPrimitive,
)
from .slots import (
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
    SlotKind,
    SlotMultiplicity,
    SlotPayloadTier,
)

__all__ = [
    "AELIX_API_LEVEL",
    "LICENSE_WHITELIST",
    "SLOT_MULTIPLICITY",
    "SLOT_PAYLOAD_TIER",
    "ActionDescriptor",
    "Activation",
    "AgentMetricPayload",
    "BadgePrimitive",
    "BreadcrumbPayload",
    "Capabilities",
    "ColumnSpec",
    "CommandContrib",
    "CommandRoutePayload",
    "Contributes",
    "DescriptorContrib",
    "DescriptorEnvelope",
    "DescriptorKind",
    "DescriptorPayload",
    "FieldSpec",
    "FooterSegmentPayload",
    "FormPrimitive",
    "GatePrimitive",
    "GridItem",
    "GridPrimitive",
    "HookContrib",
    "IncompatibleApiLevelError",
    "ManagementModalPayload",
    "McpServerContrib",
    "MetricPrimitive",
    "PluginApi",
    "PluginEntry",
    "PluginIdentity",
    "PluginManifest",
    "SlotKind",
    "SlotMultiplicity",
    "SlotPayloadTier",
    "StatusItemPayload",
    "TablePrimitive",
    "TextPrimitive",
    "ThemeContrib",
    "ToastPayload",
    "ToolContrib",
    "ToolRendererDescPayload",
    "TuiWidgetContrib",
    "assert_compatible",
    "parse_manifest_toml",
]
