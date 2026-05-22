"""Migration transforms — Sprint 6h₇b · §F.6 · Commit 4 · Aelix-additive.

Covers all 4 P-427 transforms (Pi `settings-manager.ts:334-393`):

1. ``queueMode`` -> ``steeringMode`` (when target absent).
2. ``websockets: bool`` -> ``transport: "websocket"|"sse"``.
3. ``skills`` object -> ``skills`` array + extract
   ``enableSkillCommands`` to top-level.
4. ``retry.maxDelayMs`` -> ``retry.provider.maxRetryDelayMs``.

Aelix-additive tests — Pi's own suite does NOT cover these transforms.
"""

from __future__ import annotations

from aelix_ai.settings import SettingsManager

# === 1. queueMode -> steeringMode ===


def test_migrate_queue_mode_to_steering_mode_when_target_absent() -> None:
    """``queueMode`` is migrated and the legacy key is removed."""

    migrated = SettingsManager.migrate_settings({"queueMode": "all"})
    assert migrated.get("steeringMode") == "all"
    assert "queueMode" not in migrated


def test_migrate_queue_mode_skipped_when_target_present() -> None:
    """When ``steeringMode`` already exists, ``queueMode`` is left alone."""

    migrated = SettingsManager.migrate_settings(
        {"queueMode": "all", "steeringMode": "one-at-a-time"}
    )
    assert migrated["steeringMode"] == "one-at-a-time"
    # Pi removes ``queueMode`` only when used; if target was present
    # the legacy key stays (per Pi `:336-339`).
    assert migrated["queueMode"] == "all"


# === 2. websockets boolean -> transport enum ===


def test_migrate_websockets_true_to_websocket() -> None:
    migrated = SettingsManager.migrate_settings({"websockets": True})
    assert migrated.get("transport") == "websocket"
    assert "websockets" not in migrated


def test_migrate_websockets_false_to_sse() -> None:
    migrated = SettingsManager.migrate_settings({"websockets": False})
    assert migrated.get("transport") == "sse"
    assert "websockets" not in migrated


def test_migrate_websockets_skipped_when_target_present() -> None:
    migrated = SettingsManager.migrate_settings(
        {"websockets": True, "transport": "sse"}
    )
    assert migrated["transport"] == "sse"
    # Pi's guard is ``!("transport" in settings)`` — keeps legacy when
    # target is present.
    assert migrated["websockets"] is True


def test_migrate_websockets_non_boolean_skipped() -> None:
    """Non-boolean ``websockets`` is left untouched (Pi `typeof === "boolean"`)."""

    migrated = SettingsManager.migrate_settings({"websockets": "yes"})
    assert "transport" not in migrated
    assert migrated["websockets"] == "yes"


# === 3. skills object -> array + extract enableSkillCommands ===


def test_migrate_skills_object_to_array() -> None:
    migrated = SettingsManager.migrate_settings(
        {
            "skills": {
                "enableSkillCommands": True,
                "customDirectories": ["./skill1.md", "./skill2.md"],
            }
        }
    )
    assert migrated["enableSkillCommands"] is True
    assert migrated["skills"] == ["./skill1.md", "./skill2.md"]


def test_migrate_skills_empty_custom_dirs_drops_skills_key() -> None:
    """When ``customDirectories`` is empty, the legacy ``skills`` key is dropped."""

    migrated = SettingsManager.migrate_settings(
        {"skills": {"enableSkillCommands": True, "customDirectories": []}}
    )
    assert migrated["enableSkillCommands"] is True
    assert "skills" not in migrated


def test_migrate_skills_object_preserves_existing_enable_setting() -> None:
    """If top-level ``enableSkillCommands`` is already set, do not overwrite."""

    migrated = SettingsManager.migrate_settings(
        {
            "enableSkillCommands": False,
            "skills": {
                "enableSkillCommands": True,
                "customDirectories": ["./s.md"],
            },
        }
    )
    assert migrated["enableSkillCommands"] is False
    assert migrated["skills"] == ["./s.md"]


def test_migrate_skills_already_array_is_left_alone() -> None:
    migrated = SettingsManager.migrate_settings(
        {"skills": ["./s.md"]}
    )
    assert migrated["skills"] == ["./s.md"]


# === 4. retry.maxDelayMs -> retry.provider.maxRetryDelayMs ===


def test_migrate_retry_max_delay_to_provider_max_retry_delay() -> None:
    migrated = SettingsManager.migrate_settings(
        {"retry": {"maxDelayMs": 30000, "maxRetries": 3}}
    )
    assert migrated["retry"].get("provider", {}).get("maxRetryDelayMs") == 30000
    assert "maxDelayMs" not in migrated["retry"]
    assert migrated["retry"]["maxRetries"] == 3


def test_migrate_retry_preserves_existing_provider_max_retry_delay() -> None:
    """If ``retry.provider.maxRetryDelayMs`` is set, leave it untouched."""

    migrated = SettingsManager.migrate_settings(
        {
            "retry": {
                "maxDelayMs": 30000,
                "provider": {"maxRetryDelayMs": 90000},
            }
        }
    )
    # Existing value preserved; legacy key still dropped.
    assert migrated["retry"]["provider"]["maxRetryDelayMs"] == 90000
    assert "maxDelayMs" not in migrated["retry"]


def test_migrate_retry_merges_with_existing_provider_settings() -> None:
    """Migration merges into existing provider settings (preserves siblings)."""

    migrated = SettingsManager.migrate_settings(
        {
            "retry": {
                "maxDelayMs": 30000,
                "provider": {"timeoutMs": 5000},
            }
        }
    )
    assert migrated["retry"]["provider"]["timeoutMs"] == 5000
    assert migrated["retry"]["provider"]["maxRetryDelayMs"] == 30000


def test_migrate_retry_non_numeric_max_delay_skipped() -> None:
    """Non-numeric ``maxDelayMs`` is left untouched (Pi `typeof === "number"`)."""

    migrated = SettingsManager.migrate_settings(
        {"retry": {"maxDelayMs": "30s"}}
    )
    # Pi's guard requires number — non-number stays as-is, no provider
    # is created, the legacy field remains.
    assert migrated["retry"]["maxDelayMs"] == "30s"
    assert migrated["retry"].get("provider") is None


def test_migrate_idempotent_on_second_call() -> None:
    """Migration is idempotent — running it twice yields the same dict."""

    raw = {
        "queueMode": "all",
        "websockets": True,
        "skills": {
            "enableSkillCommands": True,
            "customDirectories": ["./s.md"],
        },
        "retry": {"maxDelayMs": 30000},
    }
    once = SettingsManager.migrate_settings(dict(raw))
    twice = SettingsManager.migrate_settings(dict(once))
    assert once == twice


def test_migrate_on_load_via_in_memory_factory() -> None:
    """SettingsManager.in_memory runs migrations on load."""

    m = SettingsManager.in_memory({"queueMode": "all"})
    assert m.get_steering_mode() == "all"
