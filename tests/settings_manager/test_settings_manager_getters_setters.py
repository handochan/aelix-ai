"""Table-driven coverage for SettingsManager ~80 getters/setters.

Sprint 6h₇b · §F.5 · Commit 3.

Each top-level / nested field is verified with:

- Getter returns the documented default when the field is unset.
- Setter mutates + persists.
- Nested setter updates per-key without clobbering sibling keys.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import (
    FileSettingsStorage,
    PackageSourceObject,
    Settings,
    SettingsManager,
    WarningSettings,
)


def _make_manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    storage = FileSettingsStorage(
        settings_dirs["project_dir"], settings_dirs["agent_dir"]
    )
    return SettingsManager.from_storage(storage)


@pytest.fixture
def manager(settings_dirs: dict[str, Path]) -> SettingsManager:
    return _make_manager(settings_dirs)


# === Defaults: every getter returns its Pi default when unset ===


def test_get_last_changelog_version_default(manager: SettingsManager) -> None:
    assert manager.get_last_changelog_version() is None


def test_get_session_dir_default(manager: SettingsManager) -> None:
    assert manager.get_session_dir() is None


def test_get_default_provider_default(manager: SettingsManager) -> None:
    assert manager.get_default_provider() is None


def test_get_default_model_default(manager: SettingsManager) -> None:
    assert manager.get_default_model() is None


def test_get_steering_mode_default(manager: SettingsManager) -> None:
    assert manager.get_steering_mode() == "one-at-a-time"


def test_get_follow_up_mode_default(manager: SettingsManager) -> None:
    assert manager.get_follow_up_mode() == "one-at-a-time"


def test_get_theme_default(manager: SettingsManager) -> None:
    assert manager.get_theme() is None


def test_get_default_thinking_level_default(manager: SettingsManager) -> None:
    assert manager.get_default_thinking_level() is None


def test_get_transport_default(manager: SettingsManager) -> None:
    assert manager.get_transport() == "auto"


def test_get_compaction_enabled_default(manager: SettingsManager) -> None:
    assert manager.get_compaction_enabled() is True


def test_get_compaction_reserve_tokens_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_compaction_reserve_tokens() == 16384


def test_get_compaction_keep_recent_tokens_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_compaction_keep_recent_tokens() == 20000


def test_get_compaction_settings_default(manager: SettingsManager) -> None:
    assert manager.get_compaction_settings() == {
        "enabled": True,
        "reserveTokens": 16384,
        "keepRecentTokens": 20000,
    }


def test_get_branch_summary_settings_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_branch_summary_settings() == {
        "reserveTokens": 16384,
        "skipPrompt": False,
    }


def test_get_branch_summary_skip_prompt_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_branch_summary_skip_prompt() is False


def test_get_retry_enabled_default(manager: SettingsManager) -> None:
    assert manager.get_retry_enabled() is True


def test_get_retry_settings_default(manager: SettingsManager) -> None:
    assert manager.get_retry_settings() == {
        "enabled": True,
        "maxRetries": 3,
        "baseDelayMs": 2000,
    }


def test_get_provider_retry_settings_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_provider_retry_settings() == {
        "timeoutMs": None,
        "maxRetries": None,
        "maxRetryDelayMs": 60000,
    }


def test_get_hide_thinking_block_default(manager: SettingsManager) -> None:
    assert manager.get_hide_thinking_block() is False


def test_get_hide_compaction_summary_default(manager: SettingsManager) -> None:
    # Aelix-original DISPLAY gate — default visible (prior behavior).
    assert manager.get_hide_compaction_summary() is False


def test_get_shell_path_default(manager: SettingsManager) -> None:
    assert manager.get_shell_path() is None


def test_get_quiet_startup_default(manager: SettingsManager) -> None:
    assert manager.get_quiet_startup() is False


def test_get_shell_command_prefix_default(manager: SettingsManager) -> None:
    assert manager.get_shell_command_prefix() is None


def test_get_npm_command_default(manager: SettingsManager) -> None:
    assert manager.get_npm_command() is None


def test_get_collapse_changelog_default(manager: SettingsManager) -> None:
    assert manager.get_collapse_changelog() is False


def test_get_enable_install_telemetry_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_enable_install_telemetry() is True


def test_get_packages_default(manager: SettingsManager) -> None:
    assert manager.get_packages() == []


def test_get_extension_paths_default(manager: SettingsManager) -> None:
    assert manager.get_extension_paths() == []


def test_get_skill_paths_default(manager: SettingsManager) -> None:
    assert manager.get_skill_paths() == []


def test_get_prompt_template_paths_default(manager: SettingsManager) -> None:
    assert manager.get_prompt_template_paths() == []


def test_get_theme_paths_default(manager: SettingsManager) -> None:
    assert manager.get_theme_paths() == []


def test_get_enable_skill_commands_default(manager: SettingsManager) -> None:
    assert manager.get_enable_skill_commands() is True


def test_get_thinking_budgets_default(manager: SettingsManager) -> None:
    assert manager.get_thinking_budgets() is None


def test_get_show_images_default(manager: SettingsManager) -> None:
    assert manager.get_show_images() is True


def test_get_image_width_cells_default(manager: SettingsManager) -> None:
    assert manager.get_image_width_cells() == 60


def test_get_clear_on_shrink_default(manager: SettingsManager) -> None:
    assert manager.get_clear_on_shrink() is False


def test_get_show_terminal_progress_default(manager: SettingsManager) -> None:
    assert manager.get_show_terminal_progress() is False


def test_get_image_auto_resize_default(manager: SettingsManager) -> None:
    assert manager.get_image_auto_resize() is True


def test_get_block_images_default(manager: SettingsManager) -> None:
    assert manager.get_block_images() is False


def test_get_enabled_models_default(manager: SettingsManager) -> None:
    assert manager.get_enabled_models() is None


def test_get_double_escape_action_default(manager: SettingsManager) -> None:
    assert manager.get_double_escape_action() == "tree"


def test_get_tree_filter_mode_default(manager: SettingsManager) -> None:
    assert manager.get_tree_filter_mode() == "default"


def test_get_show_hardware_cursor_default(manager: SettingsManager) -> None:
    assert manager.get_show_hardware_cursor() is False


def test_get_editor_padding_x_default(manager: SettingsManager) -> None:
    assert manager.get_editor_padding_x() == 0


def test_get_autocomplete_max_visible_default(
    manager: SettingsManager,
) -> None:
    assert manager.get_autocomplete_max_visible() == 5


def test_get_code_block_indent_default(manager: SettingsManager) -> None:
    assert manager.get_code_block_indent() == "  "


def test_get_warnings_default(manager: SettingsManager) -> None:
    w = manager.get_warnings()
    assert w == WarningSettings()


# === Env var fallbacks (PI_CLEAR_ON_SHRINK + PI_HARDWARE_CURSOR) ===


@pytest.fixture
def env_clear_on_shrink_one(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("PI_CLEAR_ON_SHRINK", "1")
    yield


@pytest.fixture
def env_hardware_cursor_one(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("PI_HARDWARE_CURSOR", "1")
    yield


def test_clear_on_shrink_env_var_one(
    manager: SettingsManager, env_clear_on_shrink_one: None
) -> None:
    """Pi parity: env var ``PI_CLEAR_ON_SHRINK == "1"`` -> True when settings unset."""

    assert manager.get_clear_on_shrink() is True


def test_clear_on_shrink_settings_override_env(
    settings_dirs: dict[str, Path],
    write_settings: Any,
    env_clear_on_shrink_one: None,
) -> None:
    """Pi parity: explicit settings value wins over env var."""

    # Even with env=1, explicit settings false wins.
    os.environ["PI_CLEAR_ON_SHRINK"] = "1"
    write_settings(
        settings_dirs["global_path"],
        {"terminal": {"clearOnShrink": False}},
    )
    manager = _make_manager(settings_dirs)
    assert manager.get_clear_on_shrink() is False


def test_hardware_cursor_env_var_one(
    manager: SettingsManager, env_hardware_cursor_one: None
) -> None:
    """Pi parity: env var ``PI_HARDWARE_CURSOR == "1"`` -> True when settings unset."""

    assert manager.get_show_hardware_cursor() is True


# === Setters: mutate + persist + nested per-key isolation ===


async def test_set_last_changelog_version_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_last_changelog_version("1.0.0")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["lastChangelogVersion"]
        == "1.0.0"
    )


async def test_set_default_model_and_provider_atomic(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_default_model_and_provider("anthropic", "claude-sonnet")
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["defaultProvider"] == "anthropic"
    assert saved["defaultModel"] == "claude-sonnet"


async def test_set_steering_mode_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_steering_mode("all")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["steeringMode"] == "all"
    )


async def test_set_follow_up_mode_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_follow_up_mode("all")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["followUpMode"] == "all"
    )


async def test_set_hide_compaction_summary_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_hide_compaction_summary(True)
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["hideCompactionSummary"]
        is True
    )


async def test_set_theme_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_theme("midnight")
    await manager.flush()
    assert read_settings(settings_dirs["global_path"])["theme"] == "midnight"


async def test_set_default_thinking_level_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_default_thinking_level("xhigh")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["defaultThinkingLevel"]
        == "xhigh"
    )


async def test_set_transport_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_transport("websocket")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["transport"]
        == "websocket"
    )


# --- defaultProjectTrust (issue #5) — global-scope-only ---


def test_get_default_project_trust_default(manager: SettingsManager) -> None:
    assert manager.get_default_project_trust() == "ask"


async def test_set_default_project_trust_persists_to_global(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_default_project_trust("always")
    assert manager.get_default_project_trust() == "always"
    await manager.flush()
    # Persisted to GLOBAL scope (agent dir) only — never the project scope.
    assert (
        read_settings(settings_dirs["global_path"])["defaultProjectTrust"]
        == "always"
    )
    assert read_settings(settings_dirs["project_path"]) == {}


def test_get_default_project_trust_is_global_scope_only(
    settings_dirs: dict[str, Path],
    write_settings: Any,
) -> None:
    # SECURITY (issue #5): a PROJECT-scope settings.json must NOT influence the
    # getter — otherwise an untrusted project could set defaultProjectTrust="always"
    # in its own .aelix/settings.json and SELF-ELEVATE to trusted, defeating the
    # gate. The getter reads GLOBAL scope only, unlike every other (merged) getter.
    write_settings(
        settings_dirs["project_path"], {"defaultProjectTrust": "always"}
    )
    manager = _make_manager(settings_dirs)  # global empty, project = "always"
    # Getter ignores the project value → returns the "ask" default.
    assert manager.get_default_project_trust() == "ask"
    # ...but the project setting WAS loaded (proving the getter deliberately
    # bypasses the merged/project read, not that the value was simply absent).
    assert manager.get_project_settings().default_project_trust == "always"


async def test_set_compaction_enabled_isolates_nested(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    """Nested setter for ``compaction.enabled`` does not clobber siblings."""

    write_settings(
        settings_dirs["global_path"],
        {
            "compaction": {
                "enabled": True,
                "reserveTokens": 8192,
                "keepRecentTokens": 12000,
            }
        },
    )
    manager = _make_manager(settings_dirs)
    manager.set_compaction_enabled(False)
    await manager.flush()

    saved = read_settings(settings_dirs["global_path"])
    assert saved["compaction"]["enabled"] is False
    assert saved["compaction"]["reserveTokens"] == 8192
    assert saved["compaction"]["keepRecentTokens"] == 12000


async def test_set_retry_enabled_isolates_nested(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    write_settings(
        settings_dirs["global_path"],
        {"retry": {"enabled": True, "maxRetries": 5, "baseDelayMs": 4000}},
    )
    manager = _make_manager(settings_dirs)
    manager.set_retry_enabled(False)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["retry"]["enabled"] is False
    assert saved["retry"]["maxRetries"] == 5
    assert saved["retry"]["baseDelayMs"] == 4000


async def test_set_show_images_isolates_nested(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    write_settings: Any,
    read_settings: Any,
) -> None:
    write_settings(
        settings_dirs["global_path"],
        {
            "terminal": {
                "showImages": True,
                "imageWidthCells": 80,
                "showTerminalProgress": True,
            }
        },
    )
    manager = _make_manager(settings_dirs)
    manager.set_show_images(False)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["terminal"]["showImages"] is False
    assert saved["terminal"]["imageWidthCells"] == 80
    assert saved["terminal"]["showTerminalProgress"] is True


async def test_set_image_width_cells_clamps_to_min_1(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_image_width_cells(0)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["terminal"]["imageWidthCells"] == 1


async def test_set_image_width_cells_floor(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_image_width_cells(7.9)  # type: ignore[arg-type]
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["terminal"]["imageWidthCells"] == 7


async def test_set_clear_on_shrink_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_clear_on_shrink(True)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["terminal"]["clearOnShrink"] is True


async def test_set_show_terminal_progress_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_show_terminal_progress(True)
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["terminal"][
            "showTerminalProgress"
        ]
        is True
    )


async def test_set_image_auto_resize_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_image_auto_resize(False)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["images"]["autoResize"] is False


async def test_set_block_images_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_block_images(True)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["images"]["blockImages"] is True


async def test_set_enabled_models_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_enabled_models(["claude-opus-*"])
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["enabledModels"] == ["claude-opus-*"]


async def test_get_extension_sources_default(manager: SettingsManager) -> None:
    # #32-A (ADR-0186): unset → empty list (never None; a defensive copy).
    assert manager.get_extension_sources() == []


async def test_set_extension_sources_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    from aelix_ai.settings import ExtensionSourceObject

    manager.set_extension_sources(
        [
            ExtensionSourceObject(spec="https://idx/simple", kind="index"),
            ExtensionSourceObject(spec="git+https://h/r.git", kind="git", name="r"),
        ]
    )
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    # camelCase JSON key; `name` omitted when None (Pi-style optional).
    assert saved["extensionSources"] == [
        {"spec": "https://idx/simple", "kind": "index"},
        {"spec": "git+https://h/r.git", "kind": "git", "name": "r"},
    ]
    # Round-trips back through a fresh manager over the same on-disk file.
    reloaded = _make_manager(settings_dirs)
    got = reloaded.get_extension_sources()
    assert [(s.spec, s.kind, s.name) for s in got] == [
        ("https://idx/simple", "index", None),
        ("git+https://h/r.git", "git", "r"),
    ]


def test_extension_sources_decode_drops_malformed() -> None:
    # #32-A: a hostile/legacy extensionSources list with junk entries (non-dict,
    # or a dict with no spec) must degrade to only the well-formed entries — a
    # blank-spec source would render a blank Sources row + a no-op resolution.
    mgr = SettingsManager.in_memory(
        {
            "extensionSources": [
                "oops",  # not a dict
                {"kind": "index"},  # spec-less
                {"spec": "", "kind": "path"},  # empty spec
                {"spec": "https://x/simple", "kind": "index"},  # the only valid one
            ]
        }
    )
    got = mgr.get_extension_sources()
    assert [(s.spec, s.kind) for s in got] == [("https://x/simple", "index")]


async def test_set_double_escape_action_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_double_escape_action("fork")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["doubleEscapeAction"]
        == "fork"
    )


async def test_set_tree_filter_mode_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_tree_filter_mode("user-only")
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["treeFilterMode"]
        == "user-only"
    )


async def test_set_show_hardware_cursor_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_show_hardware_cursor(True)
    await manager.flush()
    assert (
        read_settings(settings_dirs["global_path"])["showHardwareCursor"]
        is True
    )


async def test_set_editor_padding_x_clamps_high(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Pi parity: ``editorPaddingX`` clamped to ``[0, 3]``."""

    manager.set_editor_padding_x(99)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["editorPaddingX"] == 3


async def test_set_editor_padding_x_clamps_low(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_editor_padding_x(-5)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["editorPaddingX"] == 0


async def test_set_autocomplete_max_visible_clamps_high(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    """Pi parity: ``autocompleteMaxVisible`` clamped to ``[3, 20]``."""

    manager.set_autocomplete_max_visible(999)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["autocompleteMaxVisible"] == 20


async def test_set_autocomplete_max_visible_clamps_low(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_autocomplete_max_visible(0)
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["autocompleteMaxVisible"] == 3


async def test_set_warnings_persists(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_warnings(WarningSettings(anthropic_extra_usage=False))
    await manager.flush()
    saved = read_settings(settings_dirs["global_path"])
    assert saved["warnings"]["anthropicExtraUsage"] is False


# === Defensive-copy getters ===


def test_get_npm_command_returns_copy() -> None:
    manager = SettingsManager.in_memory({"npmCommand": ["mise", "exec"]})
    cmd = manager.get_npm_command()
    assert cmd == ["mise", "exec"]
    cmd.append("MUTATED")  # type: ignore[union-attr]
    # Original unchanged.
    assert manager.get_npm_command() == ["mise", "exec"]


def test_get_packages_returns_copy() -> None:
    manager = SettingsManager.in_memory({"packages": ["npm:foo"]})
    pkgs = manager.get_packages()
    pkgs.append("MUTATED")
    assert manager.get_packages() == ["npm:foo"]


def test_get_warnings_returns_copy() -> None:
    manager = SettingsManager.in_memory(
        {"warnings": {"anthropicExtraUsage": True}}
    )
    w = manager.get_warnings()
    w.anthropic_extra_usage = False
    # Original unchanged.
    assert manager.get_warnings().anthropic_extra_usage is True


# === Project setters write to the project-scope file ===


async def test_project_packages_persist(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_project_packages(
        [PackageSourceObject(source="npm:proj-pkg")]
    )
    await manager.flush()
    saved = read_settings(settings_dirs["project_path"])
    assert saved["packages"][0]["source"] == "npm:proj-pkg"


async def test_project_skill_paths_persist(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_project_skill_paths(["./skill.md"])
    await manager.flush()
    saved = read_settings(settings_dirs["project_path"])
    assert saved["skills"] == ["./skill.md"]


async def test_project_prompt_template_paths_persist(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_project_prompt_template_paths(["./prompt.md"])
    await manager.flush()
    saved = read_settings(settings_dirs["project_path"])
    assert saved["prompts"] == ["./prompt.md"]


async def test_project_theme_paths_persist(
    manager: SettingsManager,
    settings_dirs: dict[str, Path],
    read_settings: Any,
) -> None:
    manager.set_project_theme_paths(["./theme.json"])
    await manager.flush()
    saved = read_settings(settings_dirs["project_path"])
    assert saved["themes"] == ["./theme.json"]


# === Load tests for non-default values ===


def test_get_default_provider_loads(settings_dirs: dict[str, Path]) -> None:
    s = Settings(default_provider="anthropic")
    m = SettingsManager.in_memory(s)
    assert m.get_default_provider() == "anthropic"


def test_get_npm_command_loads(settings_dirs: dict[str, Path]) -> None:
    m = SettingsManager.in_memory(
        {"npmCommand": ["mise", "exec", "node@20", "--", "npm"]}
    )
    assert m.get_npm_command() == ["mise", "exec", "node@20", "--", "npm"]


def test_get_thinking_budgets_loads() -> None:
    m = SettingsManager.in_memory(
        {
            "thinkingBudgets": {
                "minimal": 1024,
                "low": 2048,
                "medium": 4096,
                "high": 8192,
            }
        }
    )
    tb = m.get_thinking_budgets()
    assert tb is not None
    assert tb.minimal == 1024
    assert tb.high == 8192


def test_get_provider_retry_settings_loads() -> None:
    m = SettingsManager.in_memory(
        {
            "retry": {
                "provider": {
                    "timeoutMs": 5000,
                    "maxRetries": 2,
                    "maxRetryDelayMs": 30000,
                }
            }
        }
    )
    assert m.get_provider_retry_settings() == {
        "timeoutMs": 5000,
        "maxRetries": 2,
        "maxRetryDelayMs": 30000,
    }


def test_invalid_tree_filter_mode_falls_back_to_default() -> None:
    """Pi parity: invalid ``treeFilterMode`` falls back to ``"default"``."""

    m = SettingsManager.in_memory({"treeFilterMode": "garbage-value"})
    assert m.get_tree_filter_mode() == "default"


def test_branch_summary_skip_prompt_loads_true() -> None:
    m = SettingsManager.in_memory(
        {"branchSummary": {"skipPrompt": True, "reserveTokens": 8192}}
    )
    assert m.get_branch_summary_skip_prompt() is True
    assert m.get_branch_summary_settings() == {
        "reserveTokens": 8192,
        "skipPrompt": True,
    }
