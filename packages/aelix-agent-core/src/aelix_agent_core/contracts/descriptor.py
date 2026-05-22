"""Tier 2 cross-surface descriptor envelope + 8-kind payload union (ADR-0095).

The descriptor envelope is the canonical wire format that makes Aelix's TUI
and Web extension contributions interchangeable. See ADR-0095 §"Descriptor
envelope" and §"8-slot taxonomy v1".
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DescriptorKind = Literal[
    "footer-segment",
    "status-item",
    "tool-renderer-desc",
    "command-route",
    "breadcrumb",
    "toast",
    "management-modal",
    "agent-metric",
]


class ActionDescriptor(BaseModel):
    """Wire-safe action reference (no function refs cross the wire).

    Frontend dispatches ``plugin_action`` events with this descriptor;
    host routes to the plugin's registered action handler.
    """

    model_config = ConfigDict(extra="forbid")
    plugin_id: str = Field(..., pattern=r"^[a-z][a-z0-9-]{0,63}$")
    action: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirm: str | None = None


class FooterSegmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["footer-segment"] = "footer-segment"
    text: str
    icon: str | None = None
    tooltip: str | None = None


class StatusItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["status-item"] = "status-item"
    text: str
    level: Literal["info", "warning", "error"] = "info"


class ToolRendererDescPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tool-renderer-desc"] = "tool-renderer-desc"
    tool_name: str
    view: Literal["table", "grid", "form", "text"]
    title: str | None = None
    columns: list[dict[str, Any]] | None = None
    rows_path: str | None = None  # JSONPath into tool result for rows
    text_path: str | None = None  # JSONPath into tool result for text


class CommandRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["command-route"] = "command-route"
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    description: str
    keybind: str | None = None


class BreadcrumbPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["breadcrumb"] = "breadcrumb"
    label: str
    href: str | None = None
    icon: str | None = None


class ToastPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["toast"] = "toast"
    text: str
    level: Literal["info", "warning", "error", "success"] = "info"
    auto_dismiss_ms: int | None = Field(default=4000, ge=0, le=60_000)


class ManagementModalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["management-modal"] = "management-modal"
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    title: str
    view: Literal["table", "grid", "form"]
    fields: list[dict[str, Any]] | None = None
    columns: list[dict[str, Any]] | None = None
    actions: list[ActionDescriptor] = Field(default_factory=list)


class AgentMetricPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["agent-metric"] = "agent-metric"
    label: str
    value: str | float | int
    delta: str | None = None
    level: Literal["info", "success", "warning", "error"] = "info"


DescriptorPayload = Annotated[
    FooterSegmentPayload | StatusItemPayload | ToolRendererDescPayload | CommandRoutePayload | BreadcrumbPayload | ToastPayload | ManagementModalPayload | AgentMetricPayload,
    Field(discriminator="kind"),
]


class DescriptorEnvelope(BaseModel):
    """Tier 2 cross-surface descriptor envelope (ADR-0095)."""

    model_config = ConfigDict(extra="forbid")
    kind: DescriptorKind
    namespace: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    id: str = Field(..., min_length=1, max_length=128)
    payload: DescriptorPayload
    removed: bool = False

    @model_validator(mode="after")
    def _validate_payload_kind_matches(self) -> DescriptorEnvelope:
        if self.payload.kind != self.kind:
            raise ValueError(
                f"payload.kind={self.payload.kind!r} does not match "
                f"envelope.kind={self.kind!r}"
            )
        return self


__all__ = [
    "ActionDescriptor",
    "AgentMetricPayload",
    "BreadcrumbPayload",
    "CommandRoutePayload",
    "DescriptorEnvelope",
    "DescriptorKind",
    "DescriptorPayload",
    "FooterSegmentPayload",
    "ManagementModalPayload",
    "StatusItemPayload",
    "ToastPayload",
    "ToolRendererDescPayload",
]
