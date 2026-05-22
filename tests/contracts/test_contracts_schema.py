"""Sprint 6h₉a — contract Pydantic model validation tests.

Smoke + roundtrip + edge-case tests for the contracts package. Not exhaustive
property-based testing (deferred to Phase 5b later sprints); enough to lock
the schema shape and catch obvious regressions.
"""

from __future__ import annotations

import pathlib

import pytest
from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    LICENSE_WHITELIST,
    SLOT_MULTIPLICITY,
    SLOT_PAYLOAD_TIER,
    ActionDescriptor,
    BadgePrimitive,
    DescriptorEnvelope,
    FooterSegmentPayload,
    PluginManifest,
    TablePrimitive,
    TextPrimitive,
    assert_compatible,
)

# === api_level ===


def test_api_level_is_1() -> None:
    assert AELIX_API_LEVEL == 1


def test_assert_compatible_accepts_equal() -> None:
    assert_compatible(plugin_min_level=1, plugin_level=1)


def test_assert_compatible_accepts_lower_min_level() -> None:
    # plugin built for a future API but min_level still 1 → host accepts (warn only)
    assert_compatible(plugin_min_level=1, plugin_level=2)


def test_assert_compatible_rejects_min_above_host() -> None:
    from aelix_agent_core.contracts.api_level import IncompatibleApiLevelError

    with pytest.raises(IncompatibleApiLevelError):
        assert_compatible(plugin_min_level=2, plugin_level=2)


# === DescriptorEnvelope ===


def test_descriptor_envelope_footer_segment_roundtrip() -> None:
    env = DescriptorEnvelope(
        kind="footer-segment",
        namespace="my-plugin",
        id="git-status",
        payload=FooterSegmentPayload(text="main", icon="git", tooltip="branch"),
    )
    dumped = env.model_dump(mode="json")
    restored = DescriptorEnvelope.model_validate(dumped)
    assert restored == env


def test_descriptor_envelope_kind_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        DescriptorEnvelope.model_validate(
            {
                "kind": "footer-segment",
                "namespace": "my-plugin",
                "id": "x",
                # payload kind says status-item, envelope kind says footer-segment
                "payload": {"kind": "status-item", "text": "hello", "level": "info"},
            }
        )


def test_descriptor_envelope_namespace_pattern() -> None:
    with pytest.raises(ValueError):
        DescriptorEnvelope(
            kind="footer-segment",
            namespace="UPPERCASE-BAD",
            id="x",
            payload=FooterSegmentPayload(text="hello"),
        )


def test_descriptor_envelope_extra_field_forbidden() -> None:
    with pytest.raises(ValueError):
        DescriptorEnvelope.model_validate(
            {
                "kind": "footer-segment",
                "namespace": "ok",
                "id": "x",
                "payload": {"kind": "footer-segment", "text": "hello"},
                "extra_unexpected_field": "value",  # extra=forbid
            }
        )


def test_descriptor_envelope_removed_default_false() -> None:
    env = DescriptorEnvelope(
        kind="footer-segment",
        namespace="ns",
        id="x",
        payload=FooterSegmentPayload(text="hello"),
    )
    assert env.removed is False


def test_descriptor_envelope_removed_true_roundtrip() -> None:
    env = DescriptorEnvelope(
        kind="footer-segment",
        namespace="ns",
        id="x",
        payload=FooterSegmentPayload(text="hello"),
        removed=True,
    )
    dumped = env.model_dump(mode="json")
    assert dumped["removed"] is True


# === ActionDescriptor ===


def test_action_descriptor_minimal() -> None:
    a = ActionDescriptor(plugin_id="my-plugin", action="run-thing")
    assert a.payload == {}
    assert a.confirm is None


def test_action_descriptor_plugin_id_pattern() -> None:
    with pytest.raises(ValueError):
        ActionDescriptor(plugin_id="UPPERCASE", action="x")


def test_action_descriptor_with_confirm() -> None:
    a = ActionDescriptor(
        plugin_id="my-plugin",
        action="delete-all",
        payload={"target": "world"},
        confirm="Are you sure?",
    )
    assert a.confirm == "Are you sure?"
    assert a.payload == {"target": "world"}


# === PluginManifest ===


VALID_MANIFEST_TOML = """
[plugin]
id = "my-plugin"
name = "My Plugin"
version = "0.1.0"
description = "Test plugin"
authors = ["Test <test@example.com>"]
repository = "https://github.com/example/my-plugin"
license = "MIT"

[plugin.api]
level = 1
min_level = 1

[plugin.entry]
python = "my_plugin:extension"

[capabilities]
ui_descriptor = true

[activation]
on_startup_finished = true

[contributes]
commands = [{ id = "greet", description = "Say hello" }]
"""


def test_plugin_manifest_valid_toml_roundtrip() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    m = parse_manifest_toml(VALID_MANIFEST_TOML)
    assert m.plugin.id == "my-plugin"
    assert m.api.level == 1
    assert m.api.min_level == 1
    assert m.capabilities.ui_descriptor is True
    assert m.activation.on_startup_finished is True
    assert len(m.contributes.commands) == 1
    assert m.contributes.commands[0].id == "greet"


def test_plugin_manifest_license_whitelist_contents() -> None:
    # Phase 5b: warn-only at host loader, but whitelist must exist with v1 entries.
    assert "MIT" in LICENSE_WHITELIST
    assert "Apache-2.0" in LICENSE_WHITELIST
    assert "BSD-3-Clause" in LICENSE_WHITELIST
    # GPL family intentionally NOT in v1 whitelist
    assert "GPL-3.0" not in LICENSE_WHITELIST


def test_plugin_manifest_unknown_license_accepted_phase_5b() -> None:
    # Phase 5b is warn-only — Pydantic accepts unknown licenses (host loader
    # surfaces the warning).
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    text = VALID_MANIFEST_TOML.replace('license = "MIT"', 'license = "Proprietary"')
    m = parse_manifest_toml(text)
    assert m.plugin.license == "Proprietary"


def test_plugin_manifest_invalid_version_rejected() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    text = VALID_MANIFEST_TOML.replace('version = "0.1.0"', 'version = "v1.2"')
    with pytest.raises(ValueError):
        parse_manifest_toml(text)


def test_plugin_manifest_no_activation_trigger_rejected() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    text = VALID_MANIFEST_TOML.replace(
        "[activation]\non_startup_finished = true",
        "[activation]\non_startup_finished = false",
    )
    with pytest.raises(ValueError):
        parse_manifest_toml(text)


def test_plugin_manifest_min_level_above_level_rejected() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    text = VALID_MANIFEST_TOML.replace(
        "[plugin.api]\nlevel = 1\nmin_level = 1",
        "[plugin.api]\nlevel = 1\nmin_level = 2",
    )
    with pytest.raises(ValueError):
        parse_manifest_toml(text)


def test_plugin_manifest_invalid_id_pattern_rejected() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    text = VALID_MANIFEST_TOML.replace('id = "my-plugin"', 'id = "InvalidUppercase"')
    with pytest.raises(ValueError):
        parse_manifest_toml(text)


def test_plugin_manifest_capabilities_default_all_false() -> None:
    from aelix_agent_core.contracts.manifest import parse_manifest_toml

    # Strip [capabilities] entirely → defaults all false
    text = VALID_MANIFEST_TOML.replace("[capabilities]\nui_descriptor = true\n", "")
    m = parse_manifest_toml(text)
    assert m.capabilities.ui_descriptor is False
    assert m.capabilities.shell_exec is False


# === slot taxonomy ===


def test_slot_taxonomy_has_8_slots() -> None:
    assert len(SLOT_MULTIPLICITY) == 8
    assert len(SLOT_PAYLOAD_TIER) == 8


def test_slot_taxonomy_keys_match() -> None:
    assert set(SLOT_MULTIPLICITY) == set(SLOT_PAYLOAD_TIER)


def test_slot_taxonomy_phase_5b_all_descriptor_only() -> None:
    # Sprint 6h₉a invariant — see ADR-0095 §"Pi-dashboard divergences"
    assert all(tier == "descriptor-only" for tier in SLOT_PAYLOAD_TIER.values())


def test_slot_taxonomy_multiplicity_values_valid() -> None:
    valid = {"one", "one-active", "many"}
    assert all(m in valid for m in SLOT_MULTIPLICITY.values())


# === primitives ===


def test_table_primitive_roundtrip() -> None:
    t = TablePrimitive(
        columns=[{"id": "col1", "label": "Column 1", "kind": "text"}],  # type: ignore[list-item]
        rows=[{"col1": "hello"}],
    )
    dumped = t.model_dump(mode="json")
    restored = TablePrimitive.model_validate(dumped)
    assert restored == t


def test_text_primitive_default_style() -> None:
    t = TextPrimitive(text="hello")
    assert t.style == "default"


def test_badge_primitive_default_level() -> None:
    b = BadgePrimitive(label="status", value="ok")
    assert b.level == "info"


def test_text_primitive_invalid_style_rejected() -> None:
    with pytest.raises(ValueError):
        TextPrimitive(text="hello", style="neon-rainbow")  # type: ignore[arg-type]


# === JSON Schema generation ===


def test_descriptor_envelope_json_schema_valid() -> None:
    schema = DescriptorEnvelope.model_json_schema()
    assert "$defs" in schema or "properties" in schema


def test_plugin_manifest_json_schema_valid() -> None:
    schema = PluginManifest.model_json_schema()
    assert "$defs" in schema or "properties" in schema


def test_schema_generation_script_exists() -> None:
    repo_root = pathlib.Path(__file__).parent.parent.parent
    assert (repo_root / "scripts" / "generate_contracts_schemas.py").exists()


def test_generated_schemas_present() -> None:
    repo_root = pathlib.Path(__file__).parent.parent.parent
    contracts_dir = repo_root / "docs" / "contracts"
    for name in (
        "manifest.schema.json",
        "descriptor-envelope.schema.json",
        "primitives.schema.json",
        "slot-taxonomy.schema.json",
    ):
        assert (contracts_dir / name).exists(), f"missing {name}"
