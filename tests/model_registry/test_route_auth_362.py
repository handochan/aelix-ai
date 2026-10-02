"""#362 / ADR-0250 guard 1 — ``has_route_auth``: configured auth minus what a ``.env`` supplied.

``has_configured_auth`` (pi's ``hasConfiguredAuth``) answers "can this route
run"; ``has_route_auth`` answers "may this credential CHOOSE a route", and the
only difference is the cwd ``.env``'s record (``AELIX_DOTENV_ADMITTED``). One row
per auth layer, each with its ``.env`` twin where a ``.env`` can reach it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    path = tmp_path / "agent"
    path.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(path))
    return path


async def _registry(
    agent: Path, models_json: dict[str, Any] | None = None, auth: dict[str, Any] | None = None
) -> ModelRegistry:
    (agent / "models.json").write_text(json.dumps(models_json or {}), encoding="utf-8")
    (agent / "auth.json").write_text(json.dumps(auth or {}), encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    return ModelRegistry.create(storage, str(agent / "models.json"))


def _both(registry: ModelRegistry, provider: str) -> tuple[bool, bool]:
    return (
        registry.has_configured_auth(Model(id="", provider=provider)),
        registry.has_route_auth(provider),
    )


async def test_an_exported_key_routes(agent: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert _both(await _registry(agent), "openrouter") == (True, True)


async def test_a_dotenv_key_runs_but_does_not_route(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    assert _both(await _registry(agent), "openrouter") == (True, False)


async def test_an_exported_sibling_name_still_routes(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``anthropic`` has two names; the one the user exported counts."""

    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "ANTHROPIC_API_KEY")
    assert _both(await _registry(agent), "anthropic") == (True, True)


@pytest.mark.parametrize(
    ("entry", "routes"),
    [
        ({"type": "api_key", "key": "sk-literal"}, True),
        ({"type": "api_key", "key": "!printf x"}, True),
        # An expired OAuth record still counts — pi's ``hasConfiguredAuth`` does,
        # and it is the user's own file (ADR-0250 §4; the #344 critique's S2).
        ({"type": "oauth", "access": "a", "refresh": "r", "expires": 0}, True),
        ({"type": "api_key", "key": "STORED_NAMED_KEY"}, False),
    ],
    ids=["literal", "command", "expired-oauth", "names-a-dotenv-variable"],
)
async def test_auth_json_routes_unless_it_names_a_dotenv_variable(
    agent: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any], routes: bool
) -> None:
    monkeypatch.setenv("STORED_NAMED_KEY", "planted")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "STORED_NAMED_KEY")
    registry = await _registry(agent, auth={"openrouter": entry})
    assert _both(registry, "openrouter") == (True, routes)


@pytest.mark.parametrize(
    ("api_key", "env", "routes"),
    [
        ("literal-in-models-json", {}, True),
        ("!printf x", {}, True),
        ("GW_KEY", {"GW_KEY": "exported"}, True),
        ("GW_KEY", {"GW_KEY": "planted", "AELIX_DOTENV_ADMITTED": "GW_KEY"}, False),
        # An unset name is used VERBATIM (``resolve_config_value``), as a literal.
        ("UNSET_GW_KEY", {}, True),
    ],
    ids=["literal", "command", "exported-name", "dotenv-name", "unset-name"],
)
async def test_a_models_json_api_key_routes_unless_a_dotenv_supplied_it(
    agent: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
    env: dict[str, str],
    routes: bool,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    registry = await _registry(
        agent,
        {
            "providers": {
                "gw": {
                    "api": "openai-completions",
                    "baseUrl": "http://127.0.0.1:9/v1",
                    "apiKey": api_key,
                    "models": [{"id": "m"}],
                }
            }
        },
    )
    assert _both(registry, "gw") == (True, routes)


async def test_an_extension_registration_routes(agent: Path) -> None:
    registry = await _registry(agent)
    registry.register_provider("ext", ProviderConfigInput(api_key="ext-literal"))
    assert _both(registry, "ext") == (True, True)


async def test_a_runtime_override_routes_unless_the_child_question_is_asked(agent: Path) -> None:
    """``--api-key`` counts; ``runtime_overrides=False`` is what a delegated child sees."""

    registry = await _registry(agent)
    registry._auth_storage.set_runtime_api_key("openrouter", "k")
    assert registry.has_route_auth("openrouter") is True
    assert registry.has_route_auth("openrouter", runtime_overrides=False) is False


async def test_nothing_configured_routes_nothing(agent: Path) -> None:
    assert _both(await _registry(agent), "openrouter") == (False, False)


def test_without_a_registry_only_the_environment_counts(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli.runtime_bootstrap import route_authenticated

    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert route_authenticated(None, "openrouter") is True
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    assert route_authenticated(None, "openrouter") is False


class _Duck:
    """An embedder's registry: no ``has_route_auth``, only pi's ``has_configured_auth``."""

    def get_all(self) -> list[Model]:
        return []

    def get_available(self) -> list[Model]:
        return []

    def has_configured_auth(self, model: Model) -> bool:
        return model.provider == "openrouter"


def test_a_duck_typed_registry_fails_closed_on_a_dotenv_key(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critique S3: its ``has_configured_auth`` cannot see past a ``.env`` key, so it does not count."""

    from aelix_coding_agent.cli.runtime_bootstrap import route_authenticated

    assert route_authenticated(_Duck(), "openrouter") is True
    monkeypatch.setenv("OPENROUTER_API_KEY", "planted")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    assert route_authenticated(_Duck(), "openrouter") is False


@pytest.mark.parametrize(
    ("record", "answer", "routes"),
    [
        # Codex's third cross-review, C1: the shipped get_env_api_key as the
        # resolver hands the .env's value back; it does not route.
        ("OPENAI_API_KEY", "repo-openai-fake", False),
        # The same resolver answer with no record: the user's own, as before.
        ("", "repo-openai-fake", True),
        # A different value (the resolver's own source) routes, record or not.
        ("OPENAI_API_KEY", "own-fallback-fake", True),
        # Codex's fourth cross-review, F1: a resolver that transforms the .env
        # value still hands it back - prefixed, suffixed, stripped, or not a str.
        ("OPENAI_API_KEY", "namespace:repo-openai-fake", False),
        ("OPENAI_API_KEY", "repo-openai-fake:namespace", False),
        ("OPENAI_API_KEY", "  repo-openai-fake  ", False),
        ("OPENAI_API_KEY", b"repo-openai-fake", False),
        ("OPENAI_API_KEY", 123, False),
        # Containment needs both sides >= 8 characters: a short answer that
        # happens to occur inside the planted key is not derived from it.
        ("OPENAI_API_KEY", "fake", True),
        # Codex's fifth cross-review: a case change, and a str subclass whose
        # own strip() hides the equal text (the builtin strip is used).
        ("OPENAI_API_KEY", "REPO-OPENAI-FAKE", False),
        ("OPENAI_API_KEY", "masked-subclass", False),
    ],
    ids=[
        "equal to a .env value",
        "no record",
        "a different value",
        "prefixed",
        "suffixed",
        "padded",
        "bytes",
        "int",
        "short and unrelated",
        "case changed",
        "str subclass",
    ],
)
async def test_a_fallback_answer_routes_unless_it_is_a_dotenv_value(
    agent: Path, monkeypatch: pytest.MonkeyPatch, record: str, answer: object, routes: bool
) -> None:
    """Layer 6 of ``has_route_auth``: compared by value, since a resolver is opaque.

    ``has_configured_auth`` (the "can it run" question) still says True: the
    request's bearer is unchanged, only the route-deciding predicate differs.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "repo-openai-fake")
    if record:
        monkeypatch.setenv("AELIX_DOTENV_ADMITTED", record)
    if answer == "masked-subclass":

        class _Masked(str):
            def strip(self, chars: str | None = None) -> str:
                return "masked-fake-answer"

        answer = _Masked("repo-openai-fake")
    registry = await _registry(agent)
    registry._auth_storage.set_fallback_resolver(lambda p: answer if p == "fallback-only" else None)
    assert _both(registry, "fallback-only") == (True, routes)
    assert registry.has_fallback_resolver() is True


@pytest.mark.parametrize(
    ("planted_name", "other_name"),
    [("OPENAI_API_KEY", "OTHER_TOKEN"), ("OTHER_TOKEN", "OPENAI_API_KEY")],
    ids=["planted under OPENAI_API_KEY", "planted under OTHER_TOKEN"],
)
async def test_every_recorded_value_is_checked_not_just_one(
    agent: Path, monkeypatch: pytest.MonkeyPatch, planted_name: str, other_name: str
) -> None:
    """Codex's fourth cross-review, F4: an answer equal to ONE of two recorded values.

    A mutant that accepted an answer differing from ANY recorded value (an
    any/all slip) passed every test; with two ``.env`` names it lets the planted
    value route because it differs from the other one. The record is read as a
    set, so its iteration order follows the hash seed: the value is planted
    under each name in turn, so a "check only the first" slip is red whatever
    the seed (the independent check of the fifth pass's fixes).
    """

    monkeypatch.setenv(planted_name, "repo-openai-fake")
    monkeypatch.setenv(other_name, "unrelated-fake")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OTHER_TOKEN,OPENAI_API_KEY")
    registry = await _registry(agent)
    registry._auth_storage.set_fallback_resolver(
        lambda p: "repo-openai-fake" if p == "fallback-only" else None
    )
    assert _both(registry, "fallback-only") == (True, False)


async def test_a_mixed_case_dotenv_value_is_case_folded_too(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sides are case-folded: a mixed-case planted value, a lower-cased answer.

    The "case changed" row plants a lower-case value, so dropping the fold on the
    RECORDED side alone kept every test green (the independent check of the
    fifth pass's fixes; Codex's fifth pass used exactly this value).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "Repo-OpenAI-Fake")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENAI_API_KEY")
    registry = await _registry(agent)
    registry._auth_storage.set_fallback_resolver(
        lambda p: "repo-openai-fake" if p == "fallback-only" else None
    )
    assert _both(registry, "fallback-only") == (True, False)


async def test_case_folding_is_casefold_not_lower(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``"Straße".casefold() == "strasse"``, while ``.lower()`` keeps the ``ß``.

    ``.lower()`` in place of ``.casefold()`` kept every other test green (the
    independent re-check of ``6b0607e3``).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "Straße-key-fake")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENAI_API_KEY")
    registry = await _registry(agent)
    registry._auth_storage.set_fallback_resolver(
        lambda p: "strasse-key-fake" if p == "fallback-only" else None
    )
    assert _both(registry, "fallback-only") == (True, False)


async def test_a_short_dotenv_value_inside_an_own_answer_does_not_refuse_it(
    agent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containment needs BOTH sides >= 8 characters (Codex's fifth cross-review).

    A mutant that required only the answer's length refused an own vault key
    ``vault-dev-own-fake`` because a ``.env`` happened to set ``OTHER_TOKEN=dev``.
    """

    monkeypatch.setenv("OTHER_TOKEN", "dev")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OTHER_TOKEN")
    registry = await _registry(agent)
    registry._auth_storage.set_fallback_resolver(
        lambda p: "vault-dev-own-fake" if p == "fallback-only" else None
    )
    assert _both(registry, "fallback-only") == (True, True)


async def test_no_fallback_resolver_is_installed_by_default(agent: Path) -> None:
    """``holds_route_auth`` counts an installed resolver (C2); the stock registry has none."""

    from aelix_coding_agent.cli.runtime_bootstrap import holds_route_auth

    registry = await _registry(agent)
    assert registry.has_fallback_resolver() is False
    assert holds_route_auth(registry) is False
    registry._auth_storage.set_fallback_resolver(lambda p: None)
    assert holds_route_auth(registry) is True
