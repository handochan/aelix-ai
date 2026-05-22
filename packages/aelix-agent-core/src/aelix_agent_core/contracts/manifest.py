"""``aelix-plugin.toml`` v1 manifest schema (ADR-0096).

Pydantic v2 models matching the manifest sections specified in ADR-0096
§"Section schema". Includes a ``parse_manifest_toml`` helper that handles
TOML's ``[plugin.api]`` / ``[plugin.entry]`` table flattening.
"""

from __future__ import annotations

import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LICENSE_WHITELIST: frozenset[str] = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-3-Clause",
        "BSD-2-Clause",
        "MPL-2.0",
        "ISC",
        "Unlicense",
        "Apache-2.0 WITH LLVM-exception",
    }
)
"""SPDX identifiers permitted by the v1 license whitelist (ADR-0096 §SPDX).

GPL family is intentionally excluded from v1; compatibility audit deferred
to Phase 6. Custom licenses are accepted with a warning when authored as
``"Custom (LICENSE-FILENAME.md)"``; strict enforcement is gated by
``--strict-licenses`` (Phase 6 default true).
"""


class PluginIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]{0,63}$")
    name: str = Field(..., min_length=1, max_length=128)
    version: str = Field(
        ...,
        pattern=r"^\d+\.\d+\.\d+(-[0-9A-Za-z-.]+)?(\+[0-9A-Za-z-.]+)?$",
    )
    description: str = Field(..., min_length=1, max_length=512)
    authors: list[str] = Field(..., min_length=1)
    repository: str = Field(..., pattern=r"^https?://.+")
    license: str
    homepage: str | None = Field(default=None, pattern=r"^https?://.+")

    @model_validator(mode="after")
    def validate_license(self) -> PluginIdentity:
        # Phase 5b: warn-only on unknown license (Phase 6 strict gate).
        # Pydantic does not surface warnings here; the host loader checks
        # ``license in LICENSE_WHITELIST`` and emits the warning. The
        # validator only rejects empty strings.
        if not self.license.strip():
            raise ValueError("license must be non-empty")
        return self


class PluginApi(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: int = Field(..., ge=1)
    min_level: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_ordering(self) -> PluginApi:
        if self.min_level > self.level:
            raise ValueError("min_level must be <= level")
        return self


class PluginEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    python: str | None = Field(default=None, pattern=r"^[\w.]+:\w+$")


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell_exec: bool = False
    fs_write: bool = False
    fs_read_user: bool = False
    net: bool = False
    mcp_invoke: bool = False
    ui_tui_trusted: bool = False
    ui_descriptor: bool = False
    ui_web_trusted: bool = False
    mcp_serve: bool = False


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on_startup_finished: bool = False
    on_command: list[str] = Field(default_factory=list)
    on_tool_call: list[str] = Field(default_factory=list)
    on_session_start: bool = False

    @model_validator(mode="after")
    def at_least_one(self) -> Activation:
        has_any = (
            self.on_startup_finished
            or bool(self.on_command)
            or bool(self.on_tool_call)
            or self.on_session_start
        )
        if not has_any:
            raise ValueError("at least one activation trigger required (no `*`)")
        return self

    @model_validator(mode="after")
    def reject_wildcard_in_trigger_lists(self) -> Activation:
        # Spec §3.3.7: `*` wildcard activation is banned. Enforce at the
        # per-element level so `on_command = ["valid", "*"]` is rejected,
        # not just the all-empty case.
        if "*" in self.on_command or "*" in self.on_tool_call:
            raise ValueError(
                "`*` wildcard not allowed in activation trigger lists; "
                "declare specific commands/tools instead"
            )
        return self


class CommandContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(..., min_length=1)


class TuiWidgetContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str = Field(..., min_length=1)
    factory: str = Field(..., pattern=r"^[\w.]+:\w+$")


class DescriptorContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    id: str


class ToolContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


class ThemeContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., min_length=1)


class McpServerContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class HookContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str  # ADR-0017 hook event names (validated downstream Sprint 6h₉e)
    command: str = Field(..., min_length=1)
    timeout_ms: int = Field(default=60_000, ge=100, le=600_000)


class Contributes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commands: list[CommandContrib] = Field(default_factory=list)
    tui_widgets: list[TuiWidgetContrib] = Field(default_factory=list)
    descriptors: list[DescriptorContrib] = Field(default_factory=list)
    tools: list[ToolContrib] = Field(default_factory=list)
    themes: list[ThemeContrib] = Field(default_factory=list)
    mcp_servers: list[McpServerContrib] = Field(default_factory=list)
    hooks: list[HookContrib] = Field(default_factory=list)


class PluginManifest(BaseModel):
    """Top-level ``aelix-plugin.toml`` schema (ADR-0096)."""

    model_config = ConfigDict(extra="forbid")
    plugin: PluginIdentity
    api: PluginApi
    entry: PluginEntry = Field(default_factory=PluginEntry)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    activation: Activation
    contributes: Contributes = Field(default_factory=Contributes)

    @model_validator(mode="after")
    def validate_entry_python_required_for_python_capabilities(self) -> PluginManifest:
        # Spec §3.3.3 / ADR-0096: if a plugin declares any capability that
        # requires Python code (TUI trusted widget, descriptor emit, MCP
        # server), `entry.python` MUST be set so the host has a load target.
        # `mcp_invoke` alone does NOT require entry.python (the plugin only
        # invokes MCP servers; doesn't expose its own Python surface).
        requires_python = (
            self.capabilities.ui_tui_trusted
            or self.capabilities.ui_descriptor
            or self.capabilities.mcp_serve
        )
        if requires_python and self.entry.python is None:
            raise ValueError(
                "`entry.python` is required when capabilities.ui_tui_trusted, "
                ".ui_descriptor, or .mcp_serve is True"
            )
        return self


def parse_manifest_toml(toml_text: str) -> PluginManifest:
    """Parse ``aelix-plugin.toml`` text into a :class:`PluginManifest`.

    Handles the TOML ``[plugin.api]`` / ``[plugin.entry]`` table flattening
    so the Pydantic model can use top-level ``api`` / ``entry`` fields.
    """
    raw = tomllib.loads(toml_text)
    plugin_section = raw.get("plugin", {})
    flattened = {
        "plugin": {k: v for k, v in plugin_section.items() if k not in {"api", "entry"}},
        "api": plugin_section.get("api", {}),
        "entry": plugin_section.get("entry", {}),
        "capabilities": raw.get("capabilities", {}),
        "activation": raw.get("activation", {}),
        "contributes": raw.get("contributes", {}),
    }
    return PluginManifest.model_validate(flattened)


__all__ = [
    "LICENSE_WHITELIST",
    "Activation",
    "Capabilities",
    "CommandContrib",
    "Contributes",
    "DescriptorContrib",
    "HookContrib",
    "McpServerContrib",
    "PluginApi",
    "PluginEntry",
    "PluginIdentity",
    "PluginManifest",
    "ThemeContrib",
    "ToolContrib",
    "TuiWidgetContrib",
    "parse_manifest_toml",
]
