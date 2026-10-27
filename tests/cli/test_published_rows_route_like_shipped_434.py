"""#434 (ADR-0264 §4.4, §7 Q4) — a published row routes as a release shipping it would.

r5's ``proto_r5_q4.py`` (state A) and r7's ``proto_r7_q4a.py``, on a revision
installed the way the client installs one (parsed over the shipped catalog,
overlaid, the registry recomposed with no generation bump), not on rows
written into the catalog by hand:

* a new OpenRouter id takes a typed ``<vendor>/<id>`` string from the vendor's
  custom id once OpenRouter publishes it (with Anthropic and OpenRouter keys);
* a second gateway publishing the same id turns ``/model`` into "name one",
  and ``--model`` takes the first keyed provider in catalog order;
* the vendor's own row under its own spelling does not win the string back;
* with only an Anthropic key nothing moves;
* a delegated child's pin passes the string as typed and resolves it the same way.

Fake keys, no network.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai import models as catalog
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.agents import resolver as child
from aelix_coding_agent.cli import runtime_bootstrap as rb
from aelix_coding_agent.core import model_argument as ma
from aelix_coding_agent.model_registry import ModelRegistry

from tests.hosted_catalog_fixtures_434 import new_row, revision

_TYPED = "anthropic/claude-sonnet-9.9"
_ROW = new_row("Claude Sonnet 9.9", family="claude-sonnet")


@pytest.fixture
async def registry(monkeypatch: pytest.MonkeyPatch) -> ModelRegistry:
    for name in list(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)
    storage = AuthStorage(Path(tempfile.mkdtemp(prefix="434-q4-")) / "auth.json")
    await storage.load()
    return ModelRegistry.in_memory(storage)


def _publish(registry: ModelRegistry, add: dict) -> None:
    raw = revision(add=add)
    parsed = hc.parse_revision(json.loads(raw), catalog.shipped_catalog())
    generation = registry._generation
    catalog.install_overlay(parsed.rows, revision=hc.revision_name(raw))
    registry._recompose()
    assert registry._generation == generation


def _where(route: rb.ResolvedRoute) -> str:
    return f"{route.model.provider}/{route.model.id}"


async def test_a_published_id_takes_the_typed_string_as_a_release_would(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    before = rb.resolve_route(_TYPED, None, registry)
    assert (_where(before), before.kind) == ("anthropic/claude-sonnet-9.9", "custom")

    _publish(registry, {"openrouter": {_TYPED: _ROW}})

    after = rb.resolve_route(_TYPED, None, registry)
    assert (_where(after), after.model.api) == ("openrouter/anthropic/claude-sonnet-9.9", "openai-completions")
    picked = await ma.resolve_model_argument(_TYPED, registry=registry)
    assert picked.model is not None and picked.model.provider == "openrouter"
    assert child._pin_route(["--model", _TYPED], registry) == ["--model", _TYPED]


async def test_two_gateways_make_model_ambiguous_and_launch_takes_catalog_order(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY"):
        monkeypatch.setenv(name, "fake")
    _publish(registry, {"openrouter": {_TYPED: _ROW}, "vercel-ai-gateway": {_TYPED: _ROW}})

    assert _where(rb.resolve_route(_TYPED, None, registry)) == "openrouter/anthropic/claude-sonnet-9.9"
    picked = await ma.resolve_model_argument(_TYPED, registry=registry)
    assert picked.error is not None and "several providers" in picked.error


async def test_the_vendors_own_spelling_does_not_win_the_string_back(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _publish(
        registry,
        {"openrouter": {_TYPED: _ROW}, "anthropic": {"claude-sonnet-9-9": _ROW}},
    )

    assert _where(rb.resolve_route(_TYPED, None, registry)) == "openrouter/anthropic/claude-sonnet-9.9"
    own = rb.resolve_route("anthropic/claude-sonnet-9-9", None, registry)
    assert (_where(own), own.model.api) == ("anthropic/claude-sonnet-9-9", "anthropic-messages")


async def test_with_only_the_vendors_key_nothing_moves(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _publish(registry, {"openrouter": {_TYPED: _ROW}})

    route = rb.resolve_route(_TYPED, None, registry)
    assert (_where(route), route.model.api) == ("anthropic/claude-sonnet-9.9", "anthropic-messages")
