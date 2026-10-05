"""#363 / ADR-0251 — the key a request carries follows pi's order.

``--api-key`` -> ``auth.json`` (a stored credential owns the provider) ->
``models.json`` ``apiKey`` -> environment -> an extension registration's
``api_key`` (kept after the environment until #365). pi:
``packages/ai/src/auth/resolve.ts:56-92`` and
``coding-agent/src/core/provider-composer.ts:457-479`` @ b223082bb (since
9993c9690, 2026-07-14). Before #363 aelix asked the AuthStorage cascade first, so
an exported vendor key or a cwd ``.env`` one beat the ``models.json`` ``apiKey``.

Every row builds a REAL :class:`ModelRegistry` over a ``tmp_path`` models.json and
auth.json with fake keys; nothing opens a socket.
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
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, resolve_route
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

_OWN = "own-literal-fake"


@pytest.fixture
def scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No credential or OpenRouter setting from the real shell; a private agent dir."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith("OPENROUTER_"):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    # setenv-then-delenv records "absent" so teardown removes what load_dotenv sets.
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.delenv("OPENAI_API_KEY")
    return agent


async def _registry(
    agent: Path, providers: dict[str, Any], auth: dict[str, Any] | None = None
) -> tuple[AuthStorage, ModelRegistry]:
    (agent / "models.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")
    (agent / "auth.json").write_text(json.dumps(auth or {}), encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    return storage, registry


def _dotenv(tmp_path: Path, line: str) -> None:
    path = tmp_path / "repo" / ".env"
    path.parent.mkdir(exist_ok=True)
    path.write_text(line + "\n", encoding="utf-8")
    load_dotenv(str(path))


def _row(registry: ModelRegistry) -> Model:
    model = registry.find("openai", "gpt-4o-mini")
    assert model is not None
    return model


async def _bearer(registry: ModelRegistry) -> str | None:
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert auth.ok, auth.error
    return auth.api_key


_OWN_ENTRY = {"openai": {"apiKey": _OWN, "headers": {"x-probe": "1"}}}


async def test_models_json_api_key_beats_an_exported_vendor_key(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 3 before step 4. RED on 5dee21d1: the exported key went out."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    assert await _bearer(registry) == _OWN
    assert await registry.get_api_key_for_provider("openai") == _OWN


async def test_runtime_api_key_comes_first(scrubbed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    storage, registry = await _registry(scrubbed, _OWN_ENTRY)
    storage.set_runtime_api_key("openai", "runtime-fake")
    assert await _bearer(registry) == "runtime-fake"
    status = await registry.get_provider_auth_status("openai")
    assert status.source == "runtime"


async def test_auth_json_beats_the_models_json_api_key(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(
        scrubbed, _OWN_ENTRY, {"openai": {"type": "api_key", "key": "stored-fake"}}
    )
    assert await _bearer(registry) == "stored-fake"
    assert await registry.get_api_key_for_provider("openai") == "stored-fake"
    status = await registry.get_provider_auth_status("openai")
    assert status.source == "stored"


async def test_a_stored_oauth_whose_refresh_fails_owns_the_provider(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, probe_oauth: Any
) -> None:
    """pi ``resolve.ts:70-87``, ``:142``: the request FAILS - no key from below.

    RED on 5dee21d1: the cascade returned None and the models.json key went out.
    RED on 4cbff14e (review round 1, R1): ``ok=True`` with no key, which the CLI's
    callback reads as "no opinion", so the adapter sent the exported key
    (``tests/cli/test_refresh_failure_sends_nothing_363.py`` pins the wire).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    storage, registry = await _registry(
        scrubbed,
        _OWN_ENTRY,
        {"openai": {"type": "oauth", "refresh": "r", "access": "a", "expires": 0}},
    )

    async def _refresh_fails(_provider: str) -> str | None:
        raise RuntimeError("refresh failed (test)")

    monkeypatch.setattr(storage, "get_oauth_api_key", _refresh_fails)
    monkeypatch.setattr(storage, "load", _noop_load)
    # An OAuth provider for the record (review round 2: one that is not registered
    # is its own failure, ``test_a_stored_entry_that_gives_no_key_owns_the_provider``).
    probe_oauth("openai")
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert (auth.ok, auth.api_key) == (False, None)
    assert auth.error is not None
    assert auth.error.startswith("OAuth refresh failed for openai: refresh failed (test).")
    assert "/login" in auth.error
    # pi's getApiKeyForProvider catches the error: no key, never one from below.
    assert await registry.get_api_key_for_provider("openai") is None
    assert (await registry.get_provider_auth_status("openai")).source == "stored"


@pytest.fixture
def probe_oauth() -> Any:
    """Register an OAuth provider by id for one test; unregistered at teardown."""

    from types import SimpleNamespace

    from aelix_ai.oauth import register_oauth_provider, unregister_oauth_provider

    registered: list[str] = []

    def _register(provider: str, get_api_key: Any = None) -> None:
        register_oauth_provider(
            SimpleNamespace(  # type: ignore[arg-type]
                id=provider,
                name="Probe OAuth",
                get_api_key=get_api_key or (lambda c: c.access),
            )
        )
        registered.append(provider)

    yield _register
    for provider in registered:
        unregister_oauth_provider(provider)


async def _noop_load() -> None:
    # The cascade's recovery re-reads auth.json after a failed refresh; the file
    # here is the same expired record, so skipping the read changes nothing but
    # keeps the test off the real refresh path.
    return None


async def test_runtime_api_key_and_a_stored_entry_report_runtime(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1, R2: the status follows the request - ``--api-key`` first.

    pi ``model-runtime.ts:638-648`` @ b223082bb. RED on 4cbff14e (and 5dee21d1):
    the request carried the runtime key and the status said ``stored``.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    storage, registry = await _registry(
        scrubbed, _OWN_ENTRY, {"openai": {"type": "api_key", "key": "stored-fake"}}
    )
    storage.set_runtime_api_key("openai", "runtime-fake")
    assert await _bearer(registry) == "runtime-fake"
    assert await registry.get_api_key_for_provider("openai") == "runtime-fake"
    status = await registry.get_provider_auth_status("openai")
    assert (status.source, status.label) == ("runtime", "--api-key")


async def test_a_command_api_key_beats_the_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(
        scrubbed, {"openai": {"apiKey": "!echo cmd-fake", "headers": {"x-probe": "1"}}}
    )
    assert await _bearer(registry) == "cmd-fake"
    assert await registry.get_api_key_for_provider("openai") == "cmd-fake"
    status = await registry.get_provider_auth_status("openai")
    assert status.source == "models_json_command"


async def test_a_bare_name_api_key_reads_its_own_variable(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """aelix's config syntax: a bare name is an env lookup (pi: ``$NAME``)."""

    monkeypatch.setenv("CORP_GW_KEY", "corp-env-fake")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(
        scrubbed, {"openai": {"apiKey": "CORP_GW_KEY", "headers": {"x-probe": "1"}}}
    )
    assert await _bearer(registry) == "corp-env-fake"
    status = await registry.get_provider_auth_status("openai")
    assert (status.source, status.label) == ("environment", "CORP_GW_KEY")


async def test_auth_status_reports_the_models_json_key_ahead_of_the_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pi ``model-runtime.ts:638-648``. RED on 5dee21d1: ``environment/OPENAI_API_KEY``."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    status = await registry.get_provider_auth_status("openai")
    assert (status.configured, status.source) == (True, "models_json_key")


async def test_auth_status_without_a_models_json_key_is_the_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, {"openai": {"headers": {"x-probe": "1"}}})
    status = await registry.get_provider_auth_status("openai")
    assert (status.source, status.label) == ("environment", "OPENAI_API_KEY")


async def test_an_auth_tiebreak_route_carries_the_key_that_chose_it(
    scrubbed: Path, tmp_path: Path
) -> None:
    """Guard 1 (#362) chose ``openai`` because of the models.json key; the bearer follows.

    RED on 5dee21d1: the route was ``auth_tiebreak`` on api.openai.com and the
    request carried the cwd ``.env`` key.
    """

    _dotenv(tmp_path, "OPENAI_API_KEY=sk-dotenv-fake")
    assert os.environ.get("OPENAI_API_KEY") == "sk-dotenv-fake"
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    route = resolve_route("gpt-4o-mini", None, registry)
    assert route.kind == "auth_tiebreak"
    assert (route.model.provider, route.model.base_url) == ("openai", "https://api.openai.com/v1")
    auth = await registry.get_api_key_and_headers(route.model)
    assert auth.ok and auth.api_key == _OWN


@pytest.mark.parametrize(
    ("api_key", "own"),
    [("OPENAI_API_KEY", False), (_OWN, True), ("!echo cmd-fake", True)],
    ids=["names-the-dotenv-var", "literal", "command"],
)
async def test_guard_one_is_unchanged(
    scrubbed: Path, tmp_path: Path, api_key: str, own: bool
) -> None:
    """``has_route_auth`` is a union; the order change moves no route decision."""

    _dotenv(tmp_path, "OPENAI_API_KEY=sk-dotenv-fake")
    _, registry = await _registry(
        scrubbed, {"openai": {"apiKey": api_key, "headers": {"x-probe": "1"}}}
    )
    assert registry.has_route_auth("openai") is own


async def test_an_api_key_naming_the_dotenv_variable_still_sends_its_value(
    scrubbed: Path, tmp_path: Path
) -> None:
    """The stated consequence of aelix's bare-name syntax (ADR-0251 §divergences)."""

    _dotenv(tmp_path, "OPENAI_API_KEY=sk-dotenv-fake")
    _, registry = await _registry(
        scrubbed, {"openai": {"apiKey": "OPENAI_API_KEY", "headers": {"x-probe": "1"}}}
    )
    assert await _bearer(registry) == "sk-dotenv-fake"


async def test_an_extension_api_key_stays_after_the_env_until_365(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scope limit: only the models.json ``apiKey`` moved ahead of the environment.

    pi puts a registration's key in step 3 too; here the registration still
    leaves the catalogue's rows in the registry (#365), so step 3 would put the
    extension's key on ``api.openai.com`` (measured: ``bearer=ext``).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, {})
    registry.register_provider(
        "openai",
        ProviderConfigInput(
            api_key="ext-fake",
            models={
                "m1": Model(
                    id="m1",
                    provider="openai",
                    api="openai-completions",
                    base_url="http://127.0.0.1:9/ext/v1",
                )
            },
        ),
    )
    row = _row(registry)
    assert row.base_url == "https://api.openai.com/v1"
    assert await _bearer(registry) == "sk-exported-fake"
    status = await registry.get_provider_auth_status("openai")
    assert status.source == "environment"


async def test_an_extension_api_key_still_answers_when_the_env_has_none(
    scrubbed: Path,
) -> None:
    _, registry = await _registry(scrubbed, {})
    registry.register_provider("openai", ProviderConfigInput(api_key="ext-fake"))
    assert await _bearer(registry) == "ext-fake"
    assert await registry.get_api_key_for_provider("openai") == "ext-fake"
    status = await registry.get_provider_auth_status("openai")
    assert status.source == "models_json_key"


async def test_an_extension_registering_models_but_no_key_keeps_the_models_json_key(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scope limit is by SOURCE, not by provider name (review round 1, R3/V3).

    A registration for ``openai`` that brings models and no key leaves the
    models.json ``apiKey`` at step 3. A scope limit written as "skip step 3 for a
    registered name" passed the whole suite on 4cbff14e and sent the exported key.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    registry.register_provider(
        "openai",
        ProviderConfigInput(
            models={
                "m1": Model(
                    id="m1",
                    provider="openai",
                    api="openai-completions",
                    base_url="http://127.0.0.1:9/ext/v1",
                )
            },
        ),
    )
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert auth.ok and auth.api_key == _OWN
    # A registration that carries nothing leaves the models.json config whole.
    assert auth.headers == {"x-probe": "1"}
    assert await registry.get_api_key_for_provider("openai") == _OWN
    assert (await registry.get_provider_auth_status("openai")).source == "models_json_key"


async def test_a_headers_only_registration_keeps_the_models_json_key(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1, R5: pi ``configuredApiKey = extension?.apiKey ?? config?.apiKey``.

    RED on 4cbff14e (and 5dee21d1): the registration replaced the models.json
    request config, the key with it, and the exported key went out. The
    registration's headers are the ones sent (unchanged: it replaces them).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    registry.register_provider("openai", ProviderConfigInput(headers={"x-ext": "e"}))
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert auth.ok and auth.api_key == _OWN
    assert auth.headers == {"x-ext": "e"}
    assert await registry.get_api_key_for_provider("openai") == _OWN
    assert (await registry.get_provider_auth_status("openai")).source == "models_json_key"


async def test_a_registration_with_its_own_key_still_waits_for_the_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension's key replaces the models.json one (pi) and stays after the env (#365)."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, _OWN_ENTRY)
    registry.register_provider("openai", ProviderConfigInput(api_key="ext-fake"))
    assert await _bearer(registry) == "sk-exported-fake"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert await _bearer(registry) == "ext-fake"


async def test_the_fallback_resolver_never_authenticates_a_request(
    scrubbed: Path,
) -> None:
    """Step 4 asks the environment with ``include_fallback=False`` (review round 1, V12).

    Flipping it to True passed the whole suite on 4cbff14e.
    """

    storage, registry = await _registry(scrubbed, {})
    storage.set_fallback_resolver(lambda provider: "fallback-fake")
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert auth.ok and auth.api_key is None
    assert await registry.get_api_key_for_provider("openai") is None
    # It still counts as configured auth (pi ``hasAuth``); it is not a request's key.
    assert registry.has_configured_auth(_row(registry))


@pytest.mark.parametrize(
    "entry",
    [{"apiKey": "!echo k-fake"}, {"apiKey": "!echo k-fake", "authHeader": True}],
    ids=["api-key-only", "api-key-and-auth-header"],
)
async def test_an_api_key_only_entry_is_accepted(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]
) -> None:
    """pi since 9993c9690 (``provider-composer.ts:310-323``).

    RED on 5dee21d1: ``Failed to load models.json: Provider anthropic: must
    specify "baseUrl", ...`` and the exported key went out.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-exported-fake")
    _, registry = await _registry(scrubbed, {"anthropic": entry})
    model = registry.find("anthropic", "claude-haiku-4-5")
    assert model is not None
    auth = await registry.get_api_key_and_headers(model)
    assert auth.ok and auth.api_key == "k-fake"
    assert "anthropic" not in registry.get_user_defined_providers()


async def test_an_auth_header_only_entry_is_accepted(scrubbed: Path) -> None:
    _, registry = await _registry(scrubbed, {"anthropic": {"authHeader": False}})
    assert registry.find("anthropic", "claude-haiku-4-5") is not None


def test_an_empty_entry_is_still_refused() -> None:
    from aelix_coding_agent.models_json import validate_config_semantics

    with pytest.raises(ValueError, match="must specify"):
        validate_config_semantics({"providers": {"anthropic": {}}})


# ── Review round 2 ──────────────────────────────────────────────────────────


async def test_an_auth_header_only_registration_keeps_the_models_json_key(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2 (verify M12): ``authHeader`` alone keeps the key, as headers do.

    A registration carrying only ``auth_header=True`` keeps the ``models.json``
    ``apiKey`` at step 3, and that key is the ``Authorization`` header too. A
    keep-the-key rule that asked for ``headers`` only passed the whole suite on
    a79861ce and sent the exported key to the gateway.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    _, registry = await _registry(
        scrubbed, {"openai": {"baseUrl": "http://127.0.0.1:9/gw/v1", "apiKey": _OWN}}
    )
    registry.register_provider("openai", ProviderConfigInput(auth_header=True))
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert auth.ok and auth.api_key == _OWN
    assert (auth.headers or {}).get("Authorization") == f"Bearer {_OWN}"
    assert (await registry.get_provider_auth_status("openai")).source == "models_json_key"


# An auth.json entry that gives no key: (id, entry, OAuth provider to register or None,
# a phrase its error names). pi gives no key for the OAuth and unknown-type entries
# (resolve.ts:70-87); for an empty api_key its built-in envApiKeyAuth reads the env
# (helpers.ts:18-28) - aelix is stricter there (ADR-0251 §4).
_NO_KEY_ENTRIES = [
    ("empty-literal", {"type": "api_key", "key": ""}, None, "is an api_key with no key"),
    ("helper-true", {"type": "api_key", "key": "!true"}, None, "resolves to an empty key"),
    (
        "oauth-unregistered",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 9999999999999},
        None,
        "no OAuth provider 'openai' is registered",
    ),
    (
        "oauth-unregistered-expired",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 0},
        None,
        "no OAuth provider 'openai' is registered",
    ),
    (
        "oauth-gives-no-key",
        {"type": "oauth", "refresh": "r", "access": "a", "expires": 9999999999999},
        "empty",
        "is an OAuth login that gave no key",
    ),
    ("unknown-type", {"type": "weird"}, None, "has type 'weird'"),
]


@pytest.mark.parametrize(
    ("entry", "oauth", "phrase"),
    [pytest.param(e, o, p, id=i) for i, e, o, p in _NO_KEY_ENTRIES],
)
async def test_a_stored_entry_that_gives_no_key_owns_the_provider(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_oauth: Any,
    entry: dict[str, Any],
    oauth: str | None,
    phrase: str,
) -> None:
    """Review round 2 (Codex pass 2 C1, verify M9): a stored entry owns its provider
    WHATEVER it yields. The request fails naming the entry, ``/login`` and auth.json;
    no key from below, the status says ``stored``.

    RED on a79861ce: the empty, ``!true``, unregistered-unexpired, no-key and
    unknown-type entries answered ``ok=True`` with the exported key or ``""`` (which
    the CLI's callback reads as "no opinion", so the adapter sent the exported key).
    """

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    if oauth == "empty":
        probe_oauth("openai", lambda _c: "")
    _, registry = await _registry(
        scrubbed,
        {"openai": {"baseUrl": "http://127.0.0.1:9/gw/v1", "apiKey": _OWN}},
        {"openai": entry},
    )
    auth = await registry.get_api_key_and_headers(_row(registry))
    assert (auth.ok, auth.api_key) == (False, None)
    assert auth.error is not None
    assert auth.error.startswith("The auth.json entry for openai ")
    assert phrase in auth.error
    assert "/login" in auth.error
    assert str(scrubbed / "auth.json") in auth.error
    assert await registry.get_api_key_for_provider("openai") is None
    assert (await registry.get_provider_auth_status("openai")).source == "stored"


async def test_other_cascade_callers_keep_their_answer(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the request path asks the cascade for ``stored_owns``; the default is unchanged."""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake")
    storage, _ = await _registry(scrubbed, {}, {"openai": {"type": "api_key", "key": ""}})
    assert await storage.get_api_key_cascade("openai") == "sk-exported-fake"


def test_a_refresh_error_names_the_cause_once() -> None:
    """Review round 2 (verify M4): the cause's own prefix is not repeated."""

    from aelix_ai.oauth import OAuthRefreshError, StoredCredentialError

    error = OAuthRefreshError(
        "openai", RuntimeError("Failed to refresh OAuth token for openai: boom")
    )
    assert (
        str(error)
        == "OAuth refresh failed for openai: boom. Run /login to sign in to openai again."
    )
    assert isinstance(error, StoredCredentialError)
