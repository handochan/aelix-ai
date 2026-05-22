"""Slot taxonomy v1 (ADR-0095) — multiplicity + payload tier tables.

The actual slot identifiers are ``DescriptorKind`` Literal values defined in
``descriptor.py``. This module exposes the metadata about each slot for
host renderers and manifest validators.
"""

from __future__ import annotations

from typing import Final, Literal

from .descriptor import DescriptorKind

SlotMultiplicity = Literal["one", "one-active", "many"]
SlotPayloadTier = Literal["descriptor-only", "react-or-descriptor", "react-only"]

SLOT_MULTIPLICITY: Final[dict[DescriptorKind, SlotMultiplicity]] = {
    "footer-segment": "many",
    "status-item": "many",
    "tool-renderer-desc": "one",  # one per (tool_name)
    "command-route": "one",  # one per (command)
    "breadcrumb": "many",
    "toast": "many",
    "management-modal": "one",  # one per (command)
    "agent-metric": "many",
}

SLOT_PAYLOAD_TIER: Final[dict[DescriptorKind, SlotPayloadTier]] = {
    # Phase 5b: all 8 v1 slots are descriptor-only (Aelix-additive: subset of
    # Pi-dashboard 22-slot, React-only slots deferred to Phase 6 expansion)
    "footer-segment": "descriptor-only",
    "status-item": "descriptor-only",
    "tool-renderer-desc": "descriptor-only",
    "command-route": "descriptor-only",
    "breadcrumb": "descriptor-only",
    "toast": "descriptor-only",
    "management-modal": "descriptor-only",
    "agent-metric": "descriptor-only",
}

SlotKind = DescriptorKind  # alias for readability

__all__ = [
    "SLOT_MULTIPLICITY",
    "SLOT_PAYLOAD_TIER",
    "SlotKind",
    "SlotMultiplicity",
    "SlotPayloadTier",
]
