"""Sprint 5a (Phase 3.1) — ExtensionContext Pi-parity drift fixture (P-23).

Asserts every Pi ``ExtensionContext`` field (14 names at SHA ``734e08e``)
has a matching attribute on Aelix's :class:`ExtensionContext` after
snake_case conversion. The ``ui`` field is deferred to ADR-0033 (Phase 5
TUI) but MUST still be exposed as an attribute (raising
:class:`ExtensionError("invalid_state")` per the deferred contract) so
Pi-shaped factory code does not :exc:`AttributeError`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from aelix_coding_agent.extensions.api import ExtensionContext

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "pi_extension_context_fields_734e08e.json"
)


def _camel_to_snake(name: str) -> str:
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


def test_fixture_pi_sha_pin() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_pi_extension_context_fields_present_on_aelix_class() -> None:
    fixture = _load_fixture()
    members = set(dir(ExtensionContext))
    missing: list[str] = []
    for pi_name in fixture["field_names"]:
        # Special-cases for Pi vocab → Aelix vocab.
        snake = {
            "hasUI": "has_ui",
        }.get(pi_name, _camel_to_snake(pi_name))
        if snake not in members:
            missing.append(f"{pi_name} → {snake}")
    assert not missing, (
        f"Pi ExtensionContext fields missing on Aelix: {missing}"
    )
    assert len(fixture["field_names"]) == 14


def test_aelix_extension_context_has_at_least_14_pi_fields() -> None:
    fixture = _load_fixture()
    members = set(dir(ExtensionContext))
    pi_snake = {
        {"hasUI": "has_ui"}.get(n, _camel_to_snake(n))
        for n in fixture["field_names"]
    }
    covered = pi_snake & members
    assert covered == pi_snake, (
        f"Pi fields missing from Aelix ExtensionContext: {pi_snake - members}"
    )
