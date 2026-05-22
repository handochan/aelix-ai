"""8 UI primitives for descriptor payloads (ADR-0095 §UI primitives).

These Pydantic models are the building blocks that descriptor payloads
reference when composing tabular/form/grid/badge UI on either TUI or Web.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .descriptor import ActionDescriptor


class TextPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    style: Literal["default", "muted", "accent", "success", "warning", "error"] = "default"


class BadgePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str
    level: Literal["info", "success", "warning", "error"] = "info"


class MetricPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str | float | int
    delta: str | None = None
    level: Literal["info", "success", "warning", "error"] = "info"


class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    kind: Literal["text", "number", "boolean", "datetime", "badge", "code"] = "text"
    sortable: bool = False


class TablePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[ColumnSpec]
    rows: list[dict[str, Any]]
    actions: list[ActionDescriptor] = Field(default_factory=list)


class GridItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    subtitle: str | None = None
    badge: BadgePrimitive | None = None


class GridPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[GridItem]
    item_actions: list[ActionDescriptor] = Field(default_factory=list)


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    kind: Literal["text", "number", "boolean", "select", "textarea", "code", "datetime"] = "text"
    required: bool = False
    values: list[str] | None = None  # for kind="select"


class FormPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[FieldSpec]
    submit_action: ActionDescriptor
    cancel_action: ActionDescriptor | None = None


class GatePrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flag: str
    when: dict[str, Any] = Field(default_factory=dict)
    on_blocked_action: ActionDescriptor | None = None


__all__ = [
    "BadgePrimitive",
    "ColumnSpec",
    "FieldSpec",
    "FormPrimitive",
    "GatePrimitive",
    "GridItem",
    "GridPrimitive",
    "MetricPrimitive",
    "TablePrimitive",
    "TextPrimitive",
]
