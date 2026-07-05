"""Settings package — Sprint 6h₇b · Phase 5a-iii-β.

Pi parity: ``packages/coding-agent/src/core/settings-manager.ts`` (SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``).

Public surface:

- :class:`SettingsManager` — port of Pi's class with 3 static factories
  (:meth:`SettingsManager.create` / :meth:`from_storage` /
  :meth:`in_memory`), :meth:`reload`, :meth:`flush`, modification
  tracking, async write queue, and ~80 getters / setters.
- :class:`Settings` + 10 nested dataclass types + :data:`PackageSource`
  union + 5 ``Literal`` union types.
- :class:`SettingsStorage` Protocol + two backends
  (:class:`FileSettingsStorage`, :class:`InMemorySettingsStorage`).
- :class:`SettingsError` carrier for scope-tagged load/save errors
  drained via :meth:`SettingsManager.drain_errors`.
- :func:`deep_merge_settings` standalone helper (Pi `:116-144`).
- :func:`default_settings_path` + :func:`default_project_settings_path`
  XDG-compliant path helpers mirroring
  :func:`aelix_ai.oauth.auth_storage.default_auth_path`.
"""

from aelix_ai.settings.settings_manager import (
    SettingsManager,
    deep_merge_settings,
)
from aelix_ai.settings.storage import (
    FileSettingsStorage,
    InMemorySettingsStorage,
    SettingsStorage,
    default_project_settings_path,
    default_settings_path,
)
from aelix_ai.settings.types import (
    DEFAULT_THINKING_LEVEL,
    BranchSummarySettings,
    CompactionSettings,
    DoubleEscapeAction,
    ExtensionSourceObject,
    FollowUpMode,
    ImageSettings,
    MarkdownSettings,
    PackageSource,
    PackageSourceObject,
    ProviderRetrySettings,
    RetrySettings,
    Settings,
    SettingsError,
    SettingsScope,
    SteeringMode,
    TerminalSettings,
    ThinkingBudgetsSettings,
    ThinkingLevel,
    TransportSetting,
    TreeFilterMode,
    WarningSettings,
)

__all__ = [
    "DEFAULT_THINKING_LEVEL",
    "BranchSummarySettings",
    "CompactionSettings",
    "DoubleEscapeAction",
    "ExtensionSourceObject",
    "FileSettingsStorage",
    "FollowUpMode",
    "ImageSettings",
    "InMemorySettingsStorage",
    "MarkdownSettings",
    "PackageSource",
    "PackageSourceObject",
    "ProviderRetrySettings",
    "RetrySettings",
    "Settings",
    "SettingsError",
    "SettingsManager",
    "SettingsScope",
    "SettingsStorage",
    "SteeringMode",
    "TerminalSettings",
    "ThinkingBudgetsSettings",
    "ThinkingLevel",
    "TransportSetting",
    "TreeFilterMode",
    "WarningSettings",
    "deep_merge_settings",
    "default_project_settings_path",
    "default_settings_path",
]
