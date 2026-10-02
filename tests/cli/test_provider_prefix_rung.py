"""#344 / ADR-0249 — a ``--model`` OpenRouter cannot serve is resolved before OpenRouter-from-env.

PARTLY SUPERSEDED by #362 / ADR-0250 (2026-10-02): rung 0 and the
OpenRouter-from-env rung are gone, replaced by pi's ``resolveCliModel`` order with
two guards (``tests/cli/test_route_follows_pi_362.py``). What this file still
pins is what ADR-0250 kept from #344: a user-defined prefix (models.json custom,
a re-pointed built-in, an extension ``register_provider``, case-insensitively,
user-defined first) is resolved inside that provider and never falls to
OpenRouter; a catalogued provider that is not an OpenRouter namespace stays
itself; a re-pointed built-in's ``baseUrl`` is adopted; a NAMED provider gets the
case rule. The rows whose meaning ADR-0250 changed are rewritten in place and
say so (the dual-key ``openai/…`` goes to OpenAI, ``openrouter/<id>`` is
stripped, ``--provider X --model X/<id>`` strips the repeated prefix, a stored
credential is the user's own route).

Every row builds a REAL :class:`ModelRegistry` over a ``tmp_path`` models.json and
auth.json, with fake keys; nothing here opens a socket. The design lane's
``_FakeRegistry`` has no ``get_user_defined_providers``, and whether a provider is
user-defined is the whole question, so it would test the fallback, not the product.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, resolve_model
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

_RP = "http://127.0.0.1:9/v1"
_OLLAMA = "http://127.0.0.1:11434/v1"
_CORP = "http://127.0.0.1:9/corp-gw/v1"
_EXT = "http://127.0.0.1:9/ext/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"

_MODELS_JSON = {
    "providers": {
        # The issue's repro: a models.json CUSTOM provider.
        "retryprobe": {
            "api": "openai-completions",
            "baseUrl": _RP,
            "apiKey": "retryprobe-fake-literal",
            "models": [{"id": "held-model"}],
        },
        # The owner's own shape (~/.aelix/agent/models.json, read-only).
        "ollama": {
            "api": "openai-completions",
            "baseUrl": _OLLAMA,
            "apiKey": "ollama",
            "models": [{"id": "qwen3.6:35b-a3b"}, {"id": "qwen3.6-hermes:latest"}],
        },
        # A BUILT-IN provider re-pointed at a corporate gateway (decision 3).
        "openai": {"baseUrl": _CORP, "apiKey": "corp-gateway-fake-literal"},
        # modelOverrides ALONE: tunes anthropic, does not make it user-defined.
        "anthropic": {"modelOverrides": {"claude-haiku-4-5": {"name": "Haiku (mine)"}}},
        # compat ALONE and headers ALONE, on two more OVERLAPPING namespaces
        # (a non-overlapping one would reach its provider through 0c anyway, so
        # it could not show whether 0a claimed it). Neither is user-defined.
        "deepseek": {"compat": {"supportsDeveloperRole": False}},
        "xiaomi": {"headers": {"x-probe": "headers-only"}},
    }
}


@pytest.fixture
def scrubbed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No credential, no OpenRouter setting, no agent dir from the real shell."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith("OPENROUTER_"):
            monkeypatch.delenv(name)
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    return agent


async def _registry(agent: Path, auth: dict[str, object] | None = None) -> ModelRegistry:
    (agent / "models.json").write_text(json.dumps(_MODELS_JSON), encoding="utf-8")
    (agent / "auth.json").write_text(json.dumps(auth or {}), encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    # An EXTENSION provider, as ``bind_model_registry`` replays it (X1 puts that
    # before the launch resolve; the entry-level test proves the order).
    registry.register_provider(
        "extprov",
        ProviderConfigInput(
            name="ext probe",
            api_key="ext-fake-literal",
            models={
                "m1": Model(id="m1", provider="extprov", api="openai-completions", base_url=_EXT)
            },
        ),
    )
    assert registry.get_error() is None
    return registry


def _route(model: Model) -> tuple[str, str, str]:
    return (model.provider, model.id, model.base_url)


# === rung 0a — a user-defined prefix ==========================================


async def test_models_json_prefix_beats_openrouter_from_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's repro (C1): ``retryprobe/held-model`` reached openrouter.ai."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("retryprobe/held-model", None, registry)
    assert _route(model) == ("retryprobe", "held-model", _RP)
    assert model.api == "openai-completions"


async def test_owner_ollama_prefix_beats_openrouter_from_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured on the owner's config: 12 × CONNECT openrouter.ai, never 11434."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("ollama/qwen3.6:35b-a3b", None, registry)
    assert _route(model) == ("ollama", "qwen3.6:35b-a3b", _OLLAMA)


async def test_extension_prefix_beats_openrouter_from_env(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C4 at the resolve level (the entry test proves the launch reaches it)."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("extprov/m1", None, registry)
    assert _route(model) == ("extprov", "m1", _EXT)


async def test_unlisted_id_under_a_user_defined_provider_never_falls_to_openrouter(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0a claims the prefix even for an id the provider does not list.

    Backfilled from that provider's OWN registry models when they agree on one
    api (a model pulled into ollama after models.json was written reaches the
    user's ollama); a provider with nothing to backfill from is a refusal
    (``api="unknown"`` → ``is_runnable`` says so), never OpenRouter.
    """

    registry = await _registry(scrubbed)
    registry.register_provider("emptyext", ProviderConfigInput(api_key="x"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")

    pulled = resolve_model("ollama/llama3.3:70b", None, registry)
    assert _route(pulled) == ("ollama", "llama3.3:70b", _OLLAMA)
    assert pulled.api == "openai-completions"

    refused = resolve_model("emptyext/anything", None, registry)
    assert (refused.provider, refused.api) == ("emptyext", "unknown")


@pytest.mark.parametrize(
    ("model_flag", "provider_flag"),
    [("retryprobe/unlisted-model", None), ("unlisted-model", "retryprobe")],
    ids=["slash", "explicit-provider"],
)
async def test_unlisted_id_is_backfilled_from_the_provider_without_openrouter_too(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_flag: str,
    provider_flag: str | None,
) -> None:
    """The backfill is the explicit-provider tail for every caller, not only rung 0.

    No OpenRouter key: ``_resolve_in_provider`` is what the slash shorthand and
    ``--provider`` reach, so an id a models.json provider does not list takes that
    provider's own ``api``/``base_url``. On ``fbead6e0`` both rows came back
    ``api="unknown"`` and were refused (fix round 2, N2 — measured by
    ``/tmp/344-work/fix2/n2_probe.py``).
    """

    registry = await _registry(scrubbed)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    model = resolve_model(model_flag, provider_flag, registry)
    assert _route(model) == ("retryprobe", "unlisted-model", _RP)
    assert model.api == "openai-completions"


async def test_built_in_base_url_override_takes_the_override_host(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 3 (S + M2): a re-pointed built-in counts as user-defined.

    ``openai/gpt-4o-mini`` keeps the catalog api/metadata and adopts the
    models.json ``baseUrl`` — not OpenRouter (the old route), and not
    api.openai.com with the gateway key (what a prefix-only fix would have done,
    critique M2). The bearer here is the models.json ``apiKey`` only because this
    env holds no OpenAI credential: which key the gateway receives is the auth
    cascade's call, pinned by the next test.
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("openai/gpt-4o-mini", None, registry)
    assert _route(model) == ("openai", "gpt-4o-mini", _CORP)
    assert model.api == "openai-responses"  # the catalog's, unchanged
    auth = await registry.get_api_key_and_headers(model)
    assert auth.ok and auth.api_key == "corp-gateway-fake-literal"


@pytest.mark.parametrize(
    ("vendor_key", "bearer"),
    [
        (None, "corp-gateway-fake-literal"),
        ("exported", "sk-exported-fake-literal"),
        ("dotenv", "sk-planted-by-repo-fake"),
    ],
    ids=["no-openai-key", "exported-openai-key", "cwd-dotenv-openai-key"],
)
async def test_re_pointed_built_in_bearer_follows_the_auth_cascade(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    vendor_key: str | None,
    bearer: str,
) -> None:
    """The gateway gets the host from models.json but the KEY from the cascade.

    Review of ``0fcc3333`` (must_fix): ``ModelRegistry.get_api_key_and_headers``
    asks the AuthStorage cascade (runtime ``--api-key``, auth.json, the env var)
    BEFORE the models.json ``apiKey`` — pi's order (``authStorage`` before the
    provider's ``apiKey``). So an OpenAI key anywhere in that cascade, including
    one ``load_dotenv`` admitted from a cloned repo's ``.env``, is what the
    re-pointed provider sends to the gateway. This pins that behaviour — kept,
    not changed, in #344 (ADR-0249 §2.4); making the models.json ``apiKey`` win
    for a re-pointed provider is an owner decision, and this test is where it
    would show. The ROUTE is the same in all three rows: no credential moves it.
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    # setenv-then-delenv records "absent" so teardown removes what load_dotenv sets.
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.delenv("OPENAI_API_KEY")
    if vendor_key == "exported":
        monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake-literal")
    elif vendor_key == "dotenv":
        dotenv = tmp_path / "repo" / ".env"
        dotenv.parent.mkdir()
        dotenv.write_text("OPENAI_API_KEY=sk-planted-by-repo-fake\n", encoding="utf-8")
        load_dotenv(str(dotenv))
        assert os.environ.get("OPENAI_API_KEY") == "sk-planted-by-repo-fake"

    model = resolve_model("openai/gpt-4o-mini", None, registry)
    assert _route(model) == ("openai", "gpt-4o-mini", _CORP)
    auth = await registry.get_api_key_and_headers(model)
    assert auth.ok and auth.api_key == bearer


async def test_built_in_base_url_override_is_honoured_without_openrouter_too(
    scrubbed: Path,
) -> None:
    """C7/C9: ``--provider openai`` and ``openai/…`` reached api.openai.com."""

    registry = await _registry(scrubbed)
    explicit = resolve_model("gpt-4o-mini", "openai", registry)
    shorthand = resolve_model("openai/gpt-4o-mini", None, registry)
    assert _route(explicit) == _route(shorthand) == ("openai", "gpt-4o-mini", _CORP)


async def test_model_overrides_alone_do_not_make_a_provider_user_defined(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("anthropic/claude-haiku-4-5", None, registry)
    assert (model.provider, model.id) == ("openrouter", "anthropic/claude-haiku-4-5")


@pytest.mark.parametrize(
    "ref",
    ["deepseek/deepseek-chat", "xiaomi/mimo-v2-flash"],
    ids=["compat-only", "headers-only"],
)
async def test_compat_or_headers_alone_do_not_make_a_provider_user_defined(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    """The owner's rule for the other two tuning keys (review of ``0fcc3333``).

    Only ``modelOverrides`` was pinned; making every models.json entry
    user-defined left the suite green, and an OpenRouter-only user's
    ``deepseek/…`` would have moved to the vendor.
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model(ref, None, registry)
    assert _route(model) == ("openrouter", ref, _OPENROUTER)


async def test_a_re_pointed_openrouter_keeps_openrouter_ids(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``providers.openrouter.baseUrl`` moves the host, never the id's meaning.

    REWRITTEN for #362 / ADR-0250. ``openrouter/<id>`` is now an explicit route
    whose prefix pi strips: ``openrouter/auto`` is OpenRouter's ``auto`` (pi's
    generator alias, ``packages/ai/scripts/generate-models.ts:3341-3360`` @
    88ff80b98 — the catalogue holds both ``auto`` and ``openrouter/auto``, and
    pi's order finds ``auto`` first). ``openai/gpt-4o-mini`` reaches OpenRouter by
    pi's swap. Both adopt the ``baseUrl`` (ADR-0249 §2.4).
    """

    proxy = "http://127.0.0.1:9/or-proxy/api/v1"
    (scrubbed / "models.json").write_text(
        json.dumps({"providers": {"openrouter": {"baseUrl": proxy}}}), encoding="utf-8"
    )
    (scrubbed / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(scrubbed / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(scrubbed / "models.json"))
    assert registry.get_error() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    pairs = (("openrouter/auto", "auto"), ("openai/gpt-4o-mini", "openai/gpt-4o-mini"))
    for ref, model_id in pairs:
        assert _route(resolve_model(ref, None, registry)) == ("openrouter", model_id, proxy)


# === an extension that registers a BUILT-IN name ==============================


async def _openai_taken_over_by_an_extension(agent: Path, *apis: str) -> ModelRegistry:
    """No models.json override; an extension registers ``openai`` WITH models."""

    (agent / "models.json").write_text('{"providers": {}}', encoding="utf-8")
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    registry.register_provider(
        "openai",
        ProviderConfigInput(
            name="openai via ext",
            api_key="ext-fake-literal",
            models={
                f"corp-model-{n}": Model(
                    id=f"corp-model-{n}", provider="openai", api=api, base_url=_EXT
                )
                for n, api in enumerate(apis)
            },
        ),
    )
    return registry


async def test_an_extension_taking_over_a_built_in_keeps_its_prefix_inside_the_registration(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of ``0fcc3333``: the catalog id went to the vendor with the extension's key.

    ``register_provider("openai", models=…)`` is MERGED next to the catalog's
    ``openai`` models, and the registration's ``api_key`` becomes the provider's.
    0a then took ``openai/gpt-4o-mini`` (not in the registration) to the static
    catalog — ``api.openai.com`` with the extension's key — where ``fbead6e0``
    sent it to OpenRouter with the user's. Now an id the registration does not
    list is backfilled from the registration's own models (one api), and a bare
    catalog id is not "served" by the extension.

    #362 / ADR-0250: the bare ``gpt-4o-mini`` is pi's ambiguity (several
    providers serve it, none authenticated) — and the extension's ``openai`` is
    not among them, because its registration's models replace the catalogue's
    in the set a string can name.
    """

    registry = await _openai_taken_over_by_an_extension(scrubbed, "openai-completions")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")

    listed = resolve_model("openai/corp-model-0", None, registry)
    assert _route(listed) == ("openai", "corp-model-0", _EXT)
    unlisted = resolve_model("openai/gpt-4o-mini", None, registry)
    assert _route(unlisted) == ("openai", "gpt-4o-mini", _EXT)
    assert unlisted.api == "openai-completions"
    assert _route(resolve_model("corp-model-0", None, registry)) == ("openai", "corp-model-0", _EXT)
    # A catalog id the registration does not bring is not the extension's.
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    bare = resolve_route("gpt-4o-mini", None, registry)
    assert bare.error is not None and "is ambiguous across providers" in bare.error
    assert "openai/gpt-4o-mini" not in bare.error


async def test_an_extension_taking_over_a_built_in_with_split_apis_refuses_an_unlisted_id(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = await _openai_taken_over_by_an_extension(
        scrubbed, "openai-completions", "openai-responses"
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    refused = resolve_model("openai/gpt-4o-mini", None, registry)
    assert (refused.provider, refused.api, refused.base_url) == ("openai", "unknown", "")


# === rung 0b — an id exactly one user-defined provider serves =================


async def test_bare_id_of_one_user_defined_provider_beats_openrouter(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C13 and the owner's case: a bare ``qwen3.6:35b-a3b`` from their ollama."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    assert _route(resolve_model("held-model", None, registry)) == (
        "retryprobe",
        "held-model",
        _RP,
    )
    assert _route(resolve_model("qwen3.6:35b-a3b", None, registry)) == (
        "ollama",
        "qwen3.6:35b-a3b",
        _OLLAMA,
    )


# === rung 0c — a catalogued prefix OpenRouter has no namespace for ============


async def test_codex_prefix_goes_to_codex_not_openrouter(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``openai-codex/gpt-5.1`` reached OpenRouter and failed 400 (R4).

    No Codex credential exists in this registry, and none is needed: OpenRouter
    lists no ``openai-codex/`` id, so neither pi's swap nor guard 2 (ADR-0250)
    can move it.
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("openai-codex/gpt-5.1", None, registry)
    assert (model.provider, model.id, model.api) == (
        "openai-codex",
        "gpt-5.1",
        "openai-codex-responses",
    )


async def test_xai_prefix_goes_to_xai_hit_or_backfill(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``xai`` is not an OpenRouter namespace (OpenRouter spells it ``x-ai``)."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    for registry in (await _registry(scrubbed), None):
        hit = resolve_model("xai/grok-4.3", None, registry)
        assert (hit.provider, hit.id) == ("xai", "grok-4.3")
        backfilled = resolve_model("xai/grok-4", None, registry)  # not catalogued
        assert (backfilled.provider, backfilled.id, backfilled.api) == (
            "xai",
            "grok-4",
            "openai-completions",
        )
        assert "x.ai" in backfilled.base_url


# === what stays on OpenRouter ================================================


@pytest.mark.parametrize(
    "vendor_key",
    [None, "exported", "dotenv"],
    ids=["no-openai-key", "exported-openai-key", "cwd-dotenv-openai-key"],
)
async def test_overlapping_namespace_goes_where_pi_sends_it_and_a_dotenv_key_cannot_move_it(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    vendor_key: str | None,
) -> None:
    """``openai/gpt-4o-mini`` with an OpenRouter key exported (REWRITTEN for #362 / ADR-0250).

    No OpenAI key: pi's swap (``model-resolver.ts:525-540`` @ 88ff80b98) —
    ``openai`` is unauthenticated and OpenRouter's raw id is — so OpenRouter.
    An EXPORTED ``OPENAI_API_KEY``: the dual-key user goes to OpenAI direct, as
    pi does (the owner's decision; ADR-0249 kept it on OpenRouter). A key
    ``load_dotenv`` admitted from a cloned repo's ``.env`` — the #344 critique's
    M1: guard 1 keeps it out of the swap, so the route stays OpenRouter; with
    pi's order and no guard the repo's key chose the vendor.
    """

    # A registry WITHOUT the openai override (which would make openai user-defined).
    (scrubbed / "models.json").write_text('{"providers": {}}', encoding="utf-8")
    storage = AuthStorage(scrubbed / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(scrubbed / "models.json"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    # setenv-then-delenv records "absent" so teardown removes what load_dotenv sets.
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    monkeypatch.delenv("OPENAI_API_KEY")
    if vendor_key == "exported":
        monkeypatch.setenv("OPENAI_API_KEY", "sk-exported-fake-literal")
    elif vendor_key == "dotenv":
        dotenv = tmp_path / "repo" / ".env"
        dotenv.parent.mkdir()
        dotenv.write_text("OPENAI_API_KEY=sk-planted-by-repo-fake\n", encoding="utf-8")
        load_dotenv(str(dotenv))
        assert os.environ.get("OPENAI_API_KEY") == "sk-planted-by-repo-fake"
    assert registry.has_configured_auth(Model(provider="openai")) is (vendor_key is not None)

    model = resolve_model("openai/gpt-4o-mini", None, registry)
    if vendor_key == "exported":
        assert _route(model) == ("openai", "gpt-4o-mini", "https://api.openai.com/v1")
    else:
        assert _route(model) == ("openrouter", "openai/gpt-4o-mini", _OPENROUTER)


async def test_a_stale_stored_credential_is_the_users_own_route(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #344 critique's S2, decided the other way by ADR-0250 (§4; pi's rule).

    An expired Anthropic OAuth record in ``auth.json`` is configured auth to
    pi's ``hasConfiguredAuth``, and it is the user's own file — so
    ``anthropic/claude-haiku-4-5`` stays on Anthropic (where the refresh, or its
    failure, is loud) instead of guard 2 taking it to OpenRouter.
    """

    registry = await _registry(
        scrubbed,
        auth={
            "anthropic": {
                "type": "oauth",
                "access": "a-fake",
                "refresh": "r-fake",
                "expires": 1,
            }
        },
    )
    assert registry.has_configured_auth(Model(provider="anthropic"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("anthropic/claude-haiku-4-5", None, registry)
    assert model.provider == "anthropic"


async def test_unknown_id_under_an_overlapping_namespace_stays_openrouter(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Since #362 / ADR-0250 this is guard 2: an exported OpenRouter key, no key for the vendor."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    for ref in ("anthropic/claude-new-9", "meta-llama/llama-3.3-70b-instruct:free"):
        model = resolve_model(ref, None, registry)
        assert _route(model) == ("openrouter", ref, _OPENROUTER)


async def test_openrouter_default_model_stays_an_openrouter_id(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variable is named for OpenRouter: an explicit ``--provider openrouter`` route.

    Shell-only since #362 / ADR-0250 (a project ``.env`` can no longer set it).
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "retryprobe/held-model")
    model = resolve_model(None, None, registry)
    assert _route(model) == ("openrouter", "retryprobe/held-model", _OPENROUTER)


async def test_explicit_provider_openrouter_is_unchanged(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--provider openrouter`` is an explicit route (ADR-0250 step E): no swap, no strip here."""

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("retryprobe/held-model", "openrouter", registry)
    assert _route(model) == ("openrouter", "retryprobe/held-model", _OPENROUTER)


async def test_explicit_provider_keeps_its_meaning(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("held-model", "retryprobe", registry)
    assert _route(model) == ("retryprobe", "held-model", _RP)


@pytest.mark.parametrize(
    ("model_flag", "provider_flag"),
    [
        # 0a would read the prefix as the user-defined ``retryprobe``.
        ("retryprobe/held-model", "openai"),
        # 0b would read the bare id as the one ``retryprobe`` lists.
        ("held-model", "anthropic"),
    ],
    ids=["slash-under-openai", "bare-under-anthropic"],
)
async def test_explicit_provider_is_never_overridden_by_rung_0(
    scrubbed: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_flag: str,
    provider_flag: str,
) -> None:
    """An explicit ``--provider X`` keeps its meaning with the OpenRouter key set.

    The row above cannot tell: rung 0 would land ``held-model`` on
    ``retryprobe`` anyway. These two strings are ones rung 0 WOULD move — a
    user-defined prefix, an id only a user-defined provider lists — so a rung 0
    that ran under ``--provider`` (anything but ``openrouter``) sends them to
    ``retryprobe`` and these rows go red (fix round 2, B1).
    """

    registry = await _registry(scrubbed)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model(model_flag, provider_flag, registry)
    assert (model.provider, model.id) == (provider_flag, model_flag)
    assert model.base_url != _RP


# === the prefix is matched case-insensitively (C20) ==========================


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
async def test_prefix_is_case_insensitive(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, with_openrouter_key: bool
) -> None:
    """``RetryProbe/held-model`` was refused as an unknown protocol (C20)."""

    registry = await _registry(scrubbed)
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    assert _route(resolve_model("RetryProbe/held-model", None, registry)) == (
        "retryprobe",
        "held-model",
        _RP,
    )
    codex = resolve_model("OpenAI-Codex/gpt-5.1", None, registry)
    assert (codex.provider, codex.api) == ("openai-codex", "openai-codex-responses")


# === the registry is what makes a provider user-defined ======================


async def test_without_a_registry_a_models_json_prefix_is_unknowable(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins why every launch caller passes the registry (and S5 below)."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("retryprobe/held-model", None, None)
    assert model.provider == "openrouter"


async def test_user_defined_set_is_configuration_only(scrubbed: Path) -> None:
    registry = await _registry(scrubbed)
    registry.register_provider("anthropic", ProviderConfigInput(api_key="x"))
    assert registry.get_user_defined_providers() == frozenset(
        {"retryprobe", "ollama", "openai", "extprov"}
    )


# === Codex cross-review C1: a user-defined provider never loses to a catalogue
# spelling that differs from it only in case =====================================

_CUSTOM = "http://127.0.0.1:9/custom-openai/v1"
_SHOUTED = "http://127.0.0.1:9/shouted-openai/v1"


async def _case_registry(agent: Path, providers: dict[str, object]) -> ModelRegistry:
    (agent / "models.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")
    (agent / "auth.json").write_text("{}", encoding="utf-8")
    storage = AuthStorage(agent / "auth.json")
    await storage.load()
    registry = ModelRegistry.create(storage, str(agent / "models.json"))
    assert registry.get_error() is None
    return registry


def _custom(base_url: str, key: str) -> dict[str, object]:
    return {
        "api": "openai-completions",
        "baseUrl": base_url,
        "apiKey": key,
        "models": [{"id": "m1"}],
    }


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
@pytest.mark.parametrize(
    "reference",
    ["OpenAI/m1", "openai/m1", "OPENAI/m1", "m1"],
    ids=["exact-case", "catalogue-spelling", "other-case", "bare-0b"],
)
async def test_a_user_defined_provider_wins_over_a_catalogue_spelling(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, reference: str, with_openrouter_key: bool
) -> None:
    """Codex C1 on ``8c9d6397``: ``openai/m1`` went to OpenRouter with ``--api-key``.

    A models.json provider named ``OpenAI`` (custom, capitalised). The matcher
    collected the catalogue's ``openai`` and the user's ``OpenAI`` together and
    preferred the exact spelling, so rung 0a missed the user's provider and
    ``openai`` — an OpenRouter namespace — sent the string to OpenRouter, the
    ``--api-key`` meant for the user's endpoint as its bearer (Codex's mock
    transport: ``('…/or/v1/chat/completions', 'Bearer fake-custom-cli', True)``).
    Without the OpenRouter key the same miss sent it to the catalogue's
    ``openai`` — ``api.openai.com``. The bare id is rung 0b's (with the key) and
    the registry lookup's (without it).
    """

    registry = await _case_registry(scrubbed, {"OpenAI": _custom(_CUSTOM, "custom-fake-literal")})
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model(reference, None, registry)
    assert _route(model) == ("OpenAI", "m1", _CUSTOM)
    assert model.api == "openai-completions"


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
async def test_two_user_defined_providers_differing_only_in_case_are_refused(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, with_openrouter_key: bool
) -> None:
    """``OpenAI`` and ``OPENAI`` both custom: ``openai/m1`` could mean either.

    Refused — held on ``api='unknown'``, which every turn entry refuses — and
    never resolved in the catalogue's same-spelled ``openai`` (the vendor's host)
    or handed to OpenRouter. The refusal names both. The exact spellings still
    resolve, each to its own host.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import (
        ambiguous_provider_message,
        ambiguous_provider_prefix,
    )
    from aelix_coding_agent.core.runnable_models import is_runnable

    registry = await _case_registry(
        scrubbed,
        {
            "OpenAI": _custom(_CUSTOM, "custom-fake-literal"),
            "OPENAI": _custom(_SHOUTED, "shouted-fake-literal"),
        },
    )
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    held = resolve_model("openai/m1", None, registry)
    assert (held.provider, held.id, held.api, held.base_url) == ("openai", "m1", "unknown", "")
    assert not is_runnable(held, {"openai-completions"})
    assert ambiguous_provider_prefix("openai/m1", registry) == ("OPENAI", "OpenAI")
    message = ambiguous_provider_message("openai/m1", registry)
    assert message is not None and "'OPENAI'" in message and "'OpenAI'" in message
    assert _route(resolve_model("OpenAI/m1", None, registry)) == ("OpenAI", "m1", _CUSTOM)
    assert _route(resolve_model("OPENAI/m1", None, registry)) == ("OPENAI", "m1", _SHOUTED)
    assert ambiguous_provider_prefix("OpenAI/m1", registry) == ()


# === Codex cross-review C5: every catalogued provider, not a sample ===========


def _catalogue_partition() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(catalogued, overlapping, not overlapping), read from the catalogue HERE.

    Derived independently of ``runtime_bootstrap.openrouter_namespaces`` so a
    rung 0c that silently skips a provider cannot also skip it in the
    expectation.
    """

    from aelix_ai.models import get_models, get_providers

    catalogued = frozenset(get_providers())
    namespaces = {
        model.id.split("/", 1)[0].lower() for model in get_models("openrouter") if "/" in model.id
    }
    overlapping = frozenset(name for name in catalogued if name.lower() in namespaces)
    return catalogued, overlapping, catalogued - overlapping


def test_the_catalogue_partition_has_the_sizes_the_adr_states() -> None:
    """35 catalogued, 9 overlap, 26 do not (ADR-0249). A regeneration that moves
    these moves prefixes between rung 0c and OpenRouter — make it visible."""

    catalogued, overlapping, rest = _catalogue_partition()
    assert (len(catalogued), len(overlapping), len(rest)) == (35, 9, 26)
    assert overlapping == {
        "anthropic",
        "deepseek",
        "google",
        "minimax",
        "moonshotai",
        "nvidia",
        "openai",
        "openrouter",
        "xiaomi",
    }


@pytest.mark.parametrize("provider", sorted(_catalogue_partition()[0]))
async def test_every_catalogued_prefix_lands_where_the_partition_says(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    """Codex C5: dropping ``groq/`` from rung 0c passed every earlier test.

    Each catalogued provider, with an id from its own catalogue and
    ``OPENROUTER_API_KEY`` exported, no vendor key: the 26 that are not an
    OpenRouter namespace resolve to themselves (ADR-0250: nothing can take them
    elsewhere). The 9 that are go to OpenRouter as the whole string (#362 /
    ADR-0250: pi's swap when OpenRouter's snapshot lists it, guard 2 when it does
    not — the user holds no key of their own for that vendor), except
    ``openrouter/<id>``, an explicit route whose prefix pi strips.
    """

    from aelix_ai.models import get_models

    _, overlapping, _ = _catalogue_partition()
    model_id = sorted(model.id for model in get_models(provider))[0]
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model(f"{provider}/{model_id}", None, None)
    if provider == "openrouter":
        assert (model.provider, model.id) == ("openrouter", model_id)
    elif provider in overlapping:
        assert (model.provider, model.id) == ("openrouter", f"{provider}/{model_id}")
    else:
        assert (model.provider, model.id) == (provider, model_id)
        assert model.api != "unknown"


# === Codex second pass on ebfe411a (F1): a NAMED provider gets the case rule ===

_OVERRIDE = "http://127.0.0.1:9/override/v1"


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
@pytest.mark.parametrize(
    "named", ["openai", "OPENAI", "OpenAI"], ids=["catalogue-spelling", "other-case", "exact"]
)
async def test_a_named_provider_is_matched_as_the_prefix_is(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, named: str, with_openrouter_key: bool
) -> None:
    """F1: ``--model m1 --provider openai`` reached ``api.openai.com`` with the user's key.

    A models.json provider named ``OpenAI``. ``--model openai/m1`` already
    reached it (the prefix rule, Codex C1), but the explicit ``--provider``
    skipped the rule: ``openai`` picked the catalogue's vendor (Codex's mock
    transport: ``('https://api.openai.com/v1/responses', 'Bearer
    fake-custom-cli', True)``) and ``OPENAI`` was ``api='unknown'``. Settings
    ``defaultProvider`` and a profile's ``provider`` are named the same way.
    The exact spelling is the guard, green before and after.
    """

    registry = await _case_registry(scrubbed, {"OpenAI": _custom(_CUSTOM, "custom-fake-literal")})
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("m1", named, registry)
    assert _route(model) == ("OpenAI", "m1", _CUSTOM)
    assert model.api == "openai-completions"
    # settings.json ``defaultProvider`` — a bare id falls to it.
    fallback = resolve_model("m1", None, registry, named)
    assert _route(fallback) == ("OpenAI", "m1", _CUSTOM)


@pytest.mark.parametrize("with_openrouter_key", [True, False], ids=["or-key", "no-or-key"])
async def test_a_named_provider_two_user_defined_share_up_to_case_is_held(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, with_openrouter_key: bool
) -> None:
    """``OpenAI`` and ``OPENAI`` both custom, ``--provider openai``: held, naming both.

    Never the catalogue's ``openai`` (the vendor's host with the user's key) and
    never a guess; an exact spelling still selects its own provider.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import (
        ambiguous_provider_name,
        ambiguous_route_message,
    )
    from aelix_coding_agent.core.runnable_models import is_runnable

    registry = await _case_registry(
        scrubbed,
        {
            "OpenAI": _custom(_CUSTOM, "custom-fake-literal"),
            "OPENAI": _custom(_SHOUTED, "shouted-fake-literal"),
        },
    )
    if with_openrouter_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    held = resolve_model("m1", "openai", registry)
    assert (held.provider, held.id, held.api, held.base_url) == ("openai", "m1", "unknown", "")
    assert not is_runnable(held, {"openai-completions", "openai-responses"})
    assert ambiguous_provider_name("openai", registry) == ("OPENAI", "OpenAI")
    message = ambiguous_route_message("m1", "openai", registry)
    assert message is not None and message.startswith("--provider openai matches")
    assert "'OPENAI' and 'OpenAI'" in message
    # settings.json ``defaultProvider`` breaks a bare-id tie (ADR-0250 step 2),
    # so its case clash is held too — with the key as without it (on
    # ``9ca53a4f`` the key made the bare id OpenRouter's before it was consulted).
    fallback = resolve_model("m1", None, registry, "openai")
    assert (fallback.provider, fallback.api) == ("openai", "unknown")
    assert (ambiguous_route_message("m1", None, registry, "openai") or "").startswith(
        "settings defaultProvider openai matches"
    )
    assert _route(resolve_model("m1", "OpenAI", registry)) == ("OpenAI", "m1", _CUSTOM)
    assert _route(resolve_model("m1", "OPENAI", registry)) == ("OPENAI", "m1", _SHOUTED)
    assert ambiguous_route_message("m1", "OpenAI", registry) is None


@pytest.mark.parametrize("named", ["openai", "OPENAI", "OpenAI"])
async def test_a_re_pointed_built_in_is_matched_for_any_named_case(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch, named: str
) -> None:
    """F1: ``providers.openai.baseUrl`` held only for a lower-case ``--provider``.

    ``OPENAI`` / ``OpenAI`` came back ``api='unknown'`` (Codex's
    ``override_provider_flag`` rows); they are the re-pointed ``openai`` now,
    at the override host with the catalogue's ``api``.
    """

    registry = await _case_registry(scrubbed, {"openai": {"baseUrl": _OVERRIDE}})
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    model = resolve_model("gpt-4o-mini", named, registry)
    assert _route(model) == ("openai", "gpt-4o-mini", _OVERRIDE)
    assert model.api == "openai-responses"


async def test_a_named_provider_still_switches_the_openrouter_rungs_off(
    scrubbed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case rule is about spelling, not routing: a named provider keeps its meaning.

    With the OpenRouter key set, ``--provider OPENAI --model openai/m1`` is the
    user's ``OpenAI`` — no swap, no guard 2, no OpenRouter — and
    ``--provider openrouter`` is still OpenRouter. REWRITTEN for #362 /
    ADR-0250: the id is ``m1``, because pi tolerates ``--model <provider>/<id>``
    under an explicit provider by stripping the repeated prefix
    (``model-resolver.ts:506-512`` @ 88ff80b98; ``OpenAI`` lists ``m1``, not
    ``openai/m1``).
    """

    registry = await _case_registry(scrubbed, {"OpenAI": _custom(_CUSTOM, "custom-fake-literal")})
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    whole = resolve_model("openai/m1", "OPENAI", registry)
    assert (whole.provider, whole.id) == ("OpenAI", "m1")
    routed = resolve_model("openai/gpt-4o-mini", "openrouter", registry)
    assert (routed.provider, routed.id) == ("openrouter", "openai/gpt-4o-mini")
