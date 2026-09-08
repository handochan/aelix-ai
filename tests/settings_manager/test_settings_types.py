"""Settings dataclass tree — Sprint 6h₇b · §F.2 · Commit 1.

Covers :class:`Settings` + 10 nested dataclasses + :data:`PackageSource`
union + 5 ``Literal`` unions + ``DEFAULT_THINKING_LEVEL`` constant.
"""

from __future__ import annotations

from aelix_ai.settings import (
    DEFAULT_THINKING_LEVEL,
    BranchSummarySettings,
    CompactionSettings,
    ImageSettings,
    MarkdownSettings,
    PackageSourceObject,
    ProviderRetrySettings,
    RetrySettings,
    Settings,
    SettingsManager,
    TerminalSettings,
    ThinkingBudgetsSettings,
    WarningSettings,
)
from aelix_ai.settings.settings_manager import (
    _json_dict_to_settings,
    _settings_to_json_dict,
)


def test_settings_default_all_none() -> None:
    """Every top-level field defaults to None."""

    s = Settings()
    # Spot-check across each shape (scalar / list / nested).
    assert s.last_changelog_version is None
    assert s.default_provider is None
    assert s.default_model is None
    assert s.default_thinking_level is None
    assert s.transport is None
    assert s.steering_mode is None
    assert s.follow_up_mode is None
    assert s.theme is None
    assert s.compaction is None
    assert s.branch_summary is None
    assert s.retry is None
    assert s.hide_thinking_block is None
    assert s.shell_path is None
    assert s.quiet_startup is None
    assert s.shell_command_prefix is None
    assert s.npm_command is None
    assert s.collapse_changelog is None
    # NOTE: no ``enable_install_telemetry`` — removed in #111 B-2 (no sink).
    assert s.packages is None
    assert s.extensions is None
    assert s.skills is None
    assert s.prompts is None
    assert s.themes is None
    assert s.enable_skill_commands is None
    assert s.terminal is None
    assert s.images is None
    assert s.enabled_models is None
    assert s.double_escape_action is None
    assert s.tree_filter_mode is None
    assert s.thinking_budgets is None
    assert s.editor_padding_x is None
    assert s.autocomplete_max_visible is None
    assert s.show_hardware_cursor is None
    assert s.markdown is None
    assert s.warnings is None
    assert s.session_dir is None


def test_default_thinking_level_constant() -> None:
    """Pi parity: ``defaults.ts::DEFAULT_THINKING_LEVEL = "medium"``."""

    assert DEFAULT_THINKING_LEVEL == "medium"


def test_nested_dataclass_defaults() -> None:
    """Each nested dataclass defaults all fields to None."""

    assert CompactionSettings() == CompactionSettings(
        enabled=None, reserve_tokens=None, keep_recent_tokens=None
    )
    assert BranchSummarySettings() == BranchSummarySettings(
        reserve_tokens=None, skip_prompt=None
    )
    assert ProviderRetrySettings() == ProviderRetrySettings(
        timeout_ms=None, max_retries=None, max_retry_delay_ms=None
    )
    assert RetrySettings() == RetrySettings(
        enabled=None,
        max_retries=None,
        base_delay_ms=None,
        provider=None,
    )
    assert TerminalSettings() == TerminalSettings(
        show_images=None,
        image_width_cells=None,
        clear_on_shrink=None,
        show_terminal_progress=None,
    )
    assert ImageSettings() == ImageSettings(
        auto_resize=None, block_images=None
    )
    assert ThinkingBudgetsSettings() == ThinkingBudgetsSettings(
        minimal=None, low=None, medium=None, high=None, xhigh=None
    )
    assert MarkdownSettings() == MarkdownSettings(code_block_indent=None)
    assert WarningSettings() == WarningSettings(anthropic_extra_usage=None)


def test_thinking_budgets_xhigh_survives_the_json_boundary() -> None:
    """#250: the fifth tier needs a row in ``NESTED_PY_TO_JSON`` too.

    ``_json_dict_to_nested`` drops any JSON key absent from that table
    *silently*, so a field added to the dataclass alone would read back None.
    """

    m = SettingsManager.in_memory({"thinkingBudgets": {"xhigh": 40000}})
    budgets = m.get_thinking_budgets()
    assert budgets is not None
    assert budgets.xhigh == 40000


def test_the_xhigh_key_is_accepted_but_never_written() -> None:
    """#250 Codex cross-review: the CHANGELOG said files "gain" the key.

    They do not, and this is the measurement that says so — a fresh install
    omits the whole ``thinkingBudgets`` block, and a file carrying only
    ``high`` reads back and re-serializes with only ``high``. The shape
    *accepts* a fifth key when you write one; nothing writes it for you.
    """

    fresh = _json_dict_to_settings({})
    assert fresh.thinking_budgets is None
    assert "thinkingBudgets" not in _settings_to_json_dict(fresh)

    existing = _json_dict_to_settings({"thinkingBudgets": {"high": 8192}})
    assert existing.thinking_budgets == ThinkingBudgetsSettings(high=8192)
    assert existing.thinking_budgets.xhigh is None
    assert _settings_to_json_dict(existing) == {"thinkingBudgets": {"high": 8192}}


def test_package_source_str_form() -> None:
    """PackageSource accepts a bare string."""

    s = Settings(packages=["npm:my-pkg"])
    assert s.packages == ["npm:my-pkg"]


def test_package_source_object_form() -> None:
    """PackageSource accepts the filtering-object form."""

    obj = PackageSourceObject(
        source="npm:my-pkg",
        extensions=["ext.ts"],
        skills=["skill.md"],
        prompts=None,
        themes=None,
    )
    s = Settings(packages=[obj])
    assert s.packages is not None
    assert s.packages[0] == obj


def test_settings_dataclasses_are_mutable() -> None:
    """Pi parity: ``Settings`` and nested dataclasses are mutable."""

    s = Settings()
    s.theme = "dark"  # should not raise
    assert s.theme == "dark"
    comp = CompactionSettings()
    comp.enabled = True
    assert comp.enabled is True


def test_settings_with_full_nested_tree() -> None:
    """Construct a fully-populated Settings tree to exercise every nested type."""

    s = Settings(
        last_changelog_version="1.0.0",
        default_provider="anthropic",
        default_model="claude-sonnet",
        default_thinking_level="high",
        transport="websocket",
        steering_mode="all",
        follow_up_mode="one-at-a-time",
        theme="dark",
        compaction=CompactionSettings(
            enabled=True, reserve_tokens=16384, keep_recent_tokens=20000
        ),
        branch_summary=BranchSummarySettings(
            reserve_tokens=16384, skip_prompt=True
        ),
        retry=RetrySettings(
            enabled=True,
            max_retries=3,
            base_delay_ms=2000,
            provider=ProviderRetrySettings(
                timeout_ms=30000,
                max_retries=2,
                max_retry_delay_ms=60000,
            ),
        ),
        terminal=TerminalSettings(
            show_images=True,
            image_width_cells=60,
            clear_on_shrink=False,
            show_terminal_progress=False,
        ),
        images=ImageSettings(auto_resize=True, block_images=False),
        thinking_budgets=ThinkingBudgetsSettings(
            minimal=1024, low=2048, medium=4096, high=8192
        ),
        markdown=MarkdownSettings(code_block_indent="  "),
        warnings=WarningSettings(anthropic_extra_usage=True),
        double_escape_action="tree",
        tree_filter_mode="default",
        editor_padding_x=2,
        autocomplete_max_visible=5,
    )
    assert s.compaction is not None and s.compaction.enabled is True
    assert s.retry is not None and s.retry.provider is not None
    assert s.retry.provider.max_retry_delay_ms == 60000
    assert s.terminal is not None and s.terminal.image_width_cells == 60
    assert s.thinking_budgets is not None and s.thinking_budgets.high == 8192


def test_settings_alias_map_carries_respect_gitignore() -> None:
    """⑪ (#238) — the JSON boundary must know the key or it is dropped SILENTLY.

    ``_json_dict_to_settings`` skips any JSON key absent from
    ``SETTINGS_JSON_TO_PY`` with no error, so omitting the alias row produces a
    flag that reads its default forever with a perfectly valid-looking
    ``settings.json`` on disk. Assert both the table entry and a full
    py → json → py round trip.
    """

    import json

    from aelix_ai.settings.settings_manager import (
        _json_dict_to_settings,
        _settings_to_json_dict,
    )
    from aelix_ai.settings.types import SETTINGS_JSON_TO_PY, SETTINGS_PY_TO_JSON

    assert SETTINGS_PY_TO_JSON["respect_gitignore"] == "respectGitignore"
    assert SETTINGS_JSON_TO_PY["respectGitignore"] == "respect_gitignore"

    raw = _settings_to_json_dict(Settings(respect_gitignore=False))
    assert raw == {"respectGitignore": False}
    back = _json_dict_to_settings(json.loads(json.dumps(raw)))
    assert back.respect_gitignore is False
