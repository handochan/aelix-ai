"""Settings dataclass tree — Sprint 6h₇b · Phase 5a-iii-β · §B.

Pi parity: ``packages/coding-agent/src/core/settings-manager.ts:8-113``
(SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``).

Mirrors the Pi ``Settings`` interface + 10 nested type interfaces +
``PackageSource`` union (total 11 nested types) as Python dataclasses.

Conventions (Sprint 6h₇b §B):

- All dataclasses are ``@dataclass`` (mutable per Pi parity — Pi uses
  plain TypeScript object literals which are mutable).
- Every field defaults to ``None`` (the Pi ``?:`` optional marker maps
  to ``Optional[T] = None``).
- 5 union string-literal types use :data:`typing.Literal` per Pi
  enumerated unions.
- :data:`PackageSource` is :data:`typing.Union` of :class:`str` and
  :class:`PackageSourceObject` (Pi line 66-74).
- ``DEFAULT_THINKING_LEVEL`` is ``"medium"`` (Pi ``defaults.ts``).
- The ``snake_case`` field names map to Pi's ``camelCase`` via the
  :func:`settings_to_dict` / :func:`settings_from_dict` boundary
  helpers in :mod:`aelix_ai.settings.settings_manager`. Internal Python
  code uses ``snake_case``; on-disk JSON stays Pi-shaped ``camelCase``
  for parity.

Aelix-retained env vars (NOT renamed in 6h₇b — Phase 5b TUI surface
concern):

- ``PI_CLEAR_ON_SHRINK`` — fallback for ``terminal.clearOnShrink``.
- ``PI_HARDWARE_CURSOR`` — fallback for ``showHardwareCursor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: Pi parity ``defaults.ts``: ``DEFAULT_THINKING_LEVEL: ThinkingLevel = "medium"``.
DEFAULT_THINKING_LEVEL: Final[str] = "medium"

# === 5 union string-literal types (Pi `settings-manager.ts:80-105`) ===
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

DefaultProjectTrust = Literal["ask", "always", "never"]
"""Pi parity: ``settings-manager.ts:61`` ``DefaultProjectTrust``. The persisted
``defaultProjectTrust`` GLOBAL setting (issue #5) — how to resolve trust for a
project with no stored decision and no extension vote: ``"ask"`` (default,
interactive prompt), ``"always"`` (auto-trust), ``"never"`` (auto-deny)."""
SteeringMode = Literal["all", "one-at-a-time"]
FollowUpMode = Literal["all", "one-at-a-time"]
DoubleEscapeAction = Literal["fork", "tree", "none"]
TreeFilterMode = Literal[
    "default", "no-tools", "user-only", "labeled-only", "all"
]
TransportSetting = Literal["auto", "sse", "websocket"]


# === 11 nested types (Pi `settings-manager.ts:8-74`) ===


@dataclass
class CompactionSettings:
    """Pi parity: ``settings-manager.ts:8-12`` ``CompactionSettings``."""

    enabled: bool | None = None
    reserve_tokens: int | None = None
    keep_recent_tokens: int | None = None


@dataclass
class BranchSummarySettings:
    """Pi parity: ``settings-manager.ts:14-17`` ``BranchSummarySettings``."""

    reserve_tokens: int | None = None
    skip_prompt: bool | None = None


@dataclass
class ProviderRetrySettings:
    """Pi parity: ``settings-manager.ts:19-23`` ``ProviderRetrySettings``."""

    timeout_ms: int | None = None
    max_retries: int | None = None
    max_retry_delay_ms: int | None = None


@dataclass
class RetrySettings:
    """Pi parity: ``settings-manager.ts:25-30`` ``RetrySettings``."""

    enabled: bool | None = None
    max_retries: int | None = None
    base_delay_ms: int | None = None
    provider: ProviderRetrySettings | None = None


@dataclass
class TerminalSettings:
    """Pi parity: ``settings-manager.ts:32-37`` ``TerminalSettings``."""

    show_images: bool | None = None
    image_width_cells: int | None = None
    clear_on_shrink: bool | None = None
    show_terminal_progress: bool | None = None


@dataclass
class ImageSettings:
    """Pi parity: ``settings-manager.ts:39-42`` ``ImageSettings``."""

    auto_resize: bool | None = None
    block_images: bool | None = None


@dataclass
class ThinkingBudgetsSettings:
    """Pi parity: ``settings-manager.ts:44-49`` ``ThinkingBudgetsSettings``."""

    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None


@dataclass
class MarkdownSettings:
    """Pi parity: ``settings-manager.ts:51-53`` ``MarkdownSettings``."""

    code_block_indent: str | None = None


@dataclass
class WarningSettings:
    """Pi parity: ``settings-manager.ts:55-57`` ``WarningSettings``."""

    anthropic_extra_usage: bool | None = None


@dataclass
class PackageSourceObject:
    """Pi parity: ``settings-manager.ts:66-74`` ``PackageSource`` object form.

    Object form of the :data:`PackageSource` union — used when callers
    want to filter which sub-resources (extensions / skills / prompts /
    themes) load from the package.
    """

    source: str = ""
    extensions: list[str] | None = None
    skills: list[str] | None = None
    prompts: list[str] | None = None
    themes: list[str] | None = None


@dataclass
class ExtensionSourceObject:
    """Aelix-original (#32-A, ADR-0186): a registered extension install source.

    ``spec`` is the exact string a later ``pip install`` consumes — a git URL
    (``git+https://…``), a pip index URL (``https://pypi.example/simple``), or a
    local path. ``kind`` is one of ``"index"`` / ``"git"`` / ``"path"``. ``name``
    is the best-effort distribution / entry-point name captured after a
    successful install (``None`` until known), so ``extension update <name>`` can
    locate the source that produced ``<name>`` and reinstall it with
    ``--upgrade``.

    Deliberately DISTINCT from :class:`PackageSourceObject`: pi's ``packages`` is
    an npm-package model that bundles sub-resources
    (extensions/skills/prompts/themes) from one installed package; an aelix
    extension source describes only WHERE to install FROM. The two never merge —
    conflating them would put a ``kind`` on the npm shape and a sub-resource
    filter on the pip-source shape, both nonsensical.
    """

    spec: str = ""
    kind: str = ""
    name: str | None = None


#: Pi parity: ``settings-manager.ts:66-74`` ``PackageSource`` union.
PackageSource = str | PackageSourceObject


# === Top-level `Settings` dataclass (Pi `:76-113`) ===
# 33 optional fields — every field defaults to ``None`` so unset →
# ``None`` and getters apply per-method defaults (Pi pattern).


@dataclass
class Settings:
    """Pi parity: ``settings-manager.ts:76-113`` ``Settings`` interface.

    33 optional top-level fields. Defaults are applied in the per-getter
    methods on :class:`SettingsManager` (NOT here) — this dataclass is
    the structural shape only.
    """

    last_changelog_version: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    default_thinking_level: ThinkingLevel | None = None
    transport: TransportSetting | None = None
    steering_mode: SteeringMode | None = None
    follow_up_mode: FollowUpMode | None = None
    theme: str | None = None
    compaction: CompactionSettings | None = None
    branch_summary: BranchSummarySettings | None = None
    retry: RetrySettings | None = None
    hide_thinking_block: bool | None = None
    shell_path: str | None = None
    quiet_startup: bool | None = None
    shell_command_prefix: str | None = None
    npm_command: list[str] | None = None
    collapse_changelog: bool | None = None
    enable_install_telemetry: bool | None = None
    packages: list[PackageSource] | None = None
    # Aelix-original (#32-A, ADR-0186): registered extension install sources
    # (pip index / git repo / local path). Distinct from ``packages`` (pi's
    # npm-package model). GLOBAL-scope only in practice (user-level install
    # sources), mirroring ``enabled_models``.
    extension_sources: list[ExtensionSourceObject] | None = None
    extensions: list[str] | None = None
    skills: list[str] | None = None
    prompts: list[str] | None = None
    themes: list[str] | None = None
    enable_skill_commands: bool | None = None
    terminal: TerminalSettings | None = None
    images: ImageSettings | None = None
    enabled_models: list[str] | None = None
    double_escape_action: DoubleEscapeAction | None = None
    tree_filter_mode: TreeFilterMode | None = None
    thinking_budgets: ThinkingBudgetsSettings | None = None
    editor_padding_x: int | None = None
    autocomplete_max_visible: int | None = None
    # Issue #66 (TUI polish) — aelix-original: configurable cap on the NORMAL
    # tool-card output body (the separate 40-line diff/error cap is unaffected).
    # Clamped to ``[3, 40]`` in the setter; default 12 applied in the getter.
    tool_card_max_lines: int | None = None
    show_hardware_cursor: bool | None = None
    markdown: MarkdownSettings | None = None
    warnings: WarningSettings | None = None
    session_dir: str | None = None
    # Issue #5 — pi ``settings-manager.ts:96`` ``defaultProjectTrust`` (GLOBAL
    # setting only; read GLOBAL-scope, never merged — a project must not be able
    # to self-elevate via its own settings.json). Default applied in the getter.
    default_project_trust: DefaultProjectTrust | None = None


SettingsScope = Literal["global", "project"]
"""Pi parity: ``settings-manager.ts:146`` ``SettingsScope``."""


@dataclass(frozen=True)
class SettingsError:
    """Pi parity: ``settings-manager.ts:152-155`` ``SettingsError``.

    Carries the scope (``"global"`` / ``"project"``) plus the underlying
    exception so :meth:`SettingsManager.drain_errors` consumers can
    triage load/save failures by source.
    """

    scope: SettingsScope
    error: BaseException


# === camelCase <-> snake_case maps for JSON boundary ===
# Pi JSON files use camelCase verbatim; Aelix internals use snake_case
# per project convention. Boundary translation happens at JSON read /
# write points only — never inside the dataclass tree.

#: Mapping of dataclass attribute -> Pi JSON key (camelCase).
SETTINGS_PY_TO_JSON: Final[dict[str, str]] = {
    "last_changelog_version": "lastChangelogVersion",
    "default_provider": "defaultProvider",
    "default_model": "defaultModel",
    "default_thinking_level": "defaultThinkingLevel",
    "transport": "transport",
    "steering_mode": "steeringMode",
    "follow_up_mode": "followUpMode",
    "theme": "theme",
    "compaction": "compaction",
    "branch_summary": "branchSummary",
    "retry": "retry",
    "hide_thinking_block": "hideThinkingBlock",
    "shell_path": "shellPath",
    "quiet_startup": "quietStartup",
    "shell_command_prefix": "shellCommandPrefix",
    "npm_command": "npmCommand",
    "collapse_changelog": "collapseChangelog",
    "enable_install_telemetry": "enableInstallTelemetry",
    "packages": "packages",
    "extension_sources": "extensionSources",
    "extensions": "extensions",
    "skills": "skills",
    "prompts": "prompts",
    "themes": "themes",
    "enable_skill_commands": "enableSkillCommands",
    "terminal": "terminal",
    "images": "images",
    "enabled_models": "enabledModels",
    "double_escape_action": "doubleEscapeAction",
    "tree_filter_mode": "treeFilterMode",
    "thinking_budgets": "thinkingBudgets",
    "editor_padding_x": "editorPaddingX",
    "autocomplete_max_visible": "autocompleteMaxVisible",
    "tool_card_max_lines": "toolCardMaxLines",
    "show_hardware_cursor": "showHardwareCursor",
    "markdown": "markdown",
    "warnings": "warnings",
    "session_dir": "sessionDir",
    "default_project_trust": "defaultProjectTrust",
}


SETTINGS_JSON_TO_PY: Final[dict[str, str]] = {
    v: k for k, v in SETTINGS_PY_TO_JSON.items()
}


#: Mapping for nested dataclass attribute names -> Pi JSON keys.
#: Keyed by dataclass type name to keep the boundary table compact.
NESTED_PY_TO_JSON: Final[dict[str, dict[str, str]]] = {
    "CompactionSettings": {
        "enabled": "enabled",
        "reserve_tokens": "reserveTokens",
        "keep_recent_tokens": "keepRecentTokens",
    },
    "BranchSummarySettings": {
        "reserve_tokens": "reserveTokens",
        "skip_prompt": "skipPrompt",
    },
    "ProviderRetrySettings": {
        "timeout_ms": "timeoutMs",
        "max_retries": "maxRetries",
        "max_retry_delay_ms": "maxRetryDelayMs",
    },
    "RetrySettings": {
        "enabled": "enabled",
        "max_retries": "maxRetries",
        "base_delay_ms": "baseDelayMs",
        "provider": "provider",
    },
    "TerminalSettings": {
        "show_images": "showImages",
        "image_width_cells": "imageWidthCells",
        "clear_on_shrink": "clearOnShrink",
        "show_terminal_progress": "showTerminalProgress",
    },
    "ImageSettings": {
        "auto_resize": "autoResize",
        "block_images": "blockImages",
    },
    "ThinkingBudgetsSettings": {
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
    },
    "MarkdownSettings": {
        "code_block_indent": "codeBlockIndent",
    },
    "WarningSettings": {
        "anthropic_extra_usage": "anthropicExtraUsage",
    },
    "PackageSourceObject": {
        "source": "source",
        "extensions": "extensions",
        "skills": "skills",
        "prompts": "prompts",
        "themes": "themes",
    },
    "ExtensionSourceObject": {
        "spec": "spec",
        "kind": "kind",
        "name": "name",
    },
}


NESTED_JSON_TO_PY: Final[dict[str, dict[str, str]]] = {
    cls: {v: k for k, v in m.items()}
    for cls, m in NESTED_PY_TO_JSON.items()
}


# === Top-level field -> nested dataclass class map ===
# Used by the JSON boundary converter to know which fields are objects
# that need recursive hydration into nested dataclasses.
SETTINGS_NESTED_CLASSES: Final[dict[str, type]] = {
    "compaction": CompactionSettings,
    "branch_summary": BranchSummarySettings,
    "retry": RetrySettings,
    "terminal": TerminalSettings,
    "images": ImageSettings,
    "thinking_budgets": ThinkingBudgetsSettings,
    "markdown": MarkdownSettings,
    "warnings": WarningSettings,
}


__all__ = [
    "DEFAULT_THINKING_LEVEL",
    "NESTED_JSON_TO_PY",
    "NESTED_PY_TO_JSON",
    "SETTINGS_JSON_TO_PY",
    "SETTINGS_NESTED_CLASSES",
    "SETTINGS_PY_TO_JSON",
    "BranchSummarySettings",
    "CompactionSettings",
    "DefaultProjectTrust",
    "DoubleEscapeAction",
    "ExtensionSourceObject",
    "FollowUpMode",
    "ImageSettings",
    "MarkdownSettings",
    "PackageSource",
    "PackageSourceObject",
    "ProviderRetrySettings",
    "RetrySettings",
    "Settings",
    "SettingsError",
    "SettingsScope",
    "SteeringMode",
    "TerminalSettings",
    "ThinkingBudgetsSettings",
    "ThinkingLevel",
    "TransportSetting",
    "TreeFilterMode",
    "WarningSettings",
]
