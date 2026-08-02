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
    """Declared capabilities — three ENFORCED, six documentation-only.

    ADR-0205. The nine fields look identical in the TOML and in this class,
    but they do not mean the same thing, so each carries its enforcement
    status in ``description``: that string is the ONLY per-flag prose that
    reaches ``docs/contracts/manifest.schema.json``, i.e. the only place a
    non-Python consumer (an editor's TOML schema, the web UI, a third-party
    packer) can learn that ``shell_exec = false`` is a refusal and
    ``fs_write = false`` is a promise.

    Enforcement is a LOAD-TIME check on declared data, never a sandbox: once
    ``factory(api)`` runs, a plugin can reach its own (mutable, non-frozen)
    ``Capabilities`` through ``api`` and self-grant.
    """

    model_config = ConfigDict(extra="forbid")
    shell_exec: bool = Field(
        default=False,
        description=(
            "ENFORCED. Runs subprocesses. Required by [[contributes.hooks]] "
            "(load refused without it) and by a stdio "
            "[[contributes.mcp_servers]] (server not started without it)."
        ),
    )
    fs_write: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. Writes "
            "outside the workspace. The host does not restrict file writes; "
            "a Tier-1 plugin is in-process Python and open() is unrestricted."
        ),
    )
    fs_read_user: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. Reads "
            "user files outside the workspace."
        ),
    )
    net: bool = Field(
        default=False,
        description=(
            "ENFORCED for MCP. Makes network calls. Required by an http/sse "
            "[[contributes.mcp_servers]] (server not started without it). "
            "Plain outbound calls from plugin code are not restricted."
        ),
    )
    mcp_invoke: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. Calls "
            "MCP servers the host has already connected. Enforcement "
            "deferred (ADR-0101)."
        ),
    )
    ui_tui_trusted: bool = Field(
        default=False,
        description=(
            "ENFORCED. Renders in the TUI. Required by "
            "[[contributes.tui_widgets]]; the load is refused before the "
            "plugin module is imported."
        ),
    )
    ui_descriptor: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. Emits "
            "UI descriptors. Requires entry.python."
        ),
    )
    ui_web_trusted: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. Renders "
            "in the web UI (Phase 6)."
        ),
    )
    mcp_serve: bool = Field(
        default=False,
        description=(
            "DECLARED ONLY — documentation of intent, NOT a sandbox. The "
            "plugin EXPOSES ITS OWN MCP server; requires entry.python. This "
            "is NOT the flag for [[contributes.mcp_servers]], which tells "
            "the host to connect OUT to someone else's server and is gated "
            "on shell_exec (stdio) / net (http, sse). See ADR-0205."
        ),
    )


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
    # Argv for the stdio ``command`` (e.g. ``["-y", "@modelcontextprotocol/
    # server-filesystem", "/tmp"]`` for npx-style servers). Additive optional
    # field — backward compatible with existing manifests.
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables ADDED for a stdio server, on top of the "
            "small default set the MCP SDK gives every child (HOME, PATH, "
            "SHELL, TERM, USER). It does NOT widen what the child inherits: "
            "the host's own environment — including the user's provider API "
            "keys — is never passed through, so a server that needs a value "
            "from it will not receive one. Declaring this key used to hand "
            "over the parent environment whole, which made the most "
            "innocuous-looking line in a manifest the one that leaked "
            "credentials; it no longer does."
        ),
    )


class HookContrib(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str  # ADR-0017 hook event names (validated against the subprocess allowlist — ADR-0102)
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
