"""Tests for cli/runtime_bootstrap — .env load + model resolution (OpenRouter)."""

from __future__ import annotations

import os

import pytest
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_API
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, resolve_model


def test_resolve_model_openrouter_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    m = resolve_model(None, None)
    assert m.provider == "openrouter"
    assert m.api == OPENAI_COMPLETIONS_API
    assert m.id == "anthropic/claude-3.5-sonnet"
    assert "openrouter.ai" in m.base_url


def test_resolve_model_model_flag_overrides_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "default/model")
    m = resolve_model("openai/gpt-4o", None)
    assert m.provider == "openrouter" and m.id == "openai/gpt-4o"


def test_resolve_model_explicit_non_openrouter_provider_is_bare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "x")
    m = resolve_model("gpt-4o", "openai")  # explicit non-openrouter provider
    assert m.provider == "openai" and m.id == "gpt-4o"
    assert m.api != OPENAI_COMPLETIONS_API  # did NOT take the OpenRouter path


def test_resolve_model_no_config_is_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model(None, None)
    assert m.id == "" and m.provider == ""


def test_resolve_model_key_without_model_is_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model(None, None)  # key but no model id → can't resolve
    assert m.provider == ""


# --- Explicit --provider/--model path: catalog enrichment + slash shorthand ---
# (regression: the bare return left api="unknown", so the documented
# ``aelix --provider anthropic --model claude-sonnet-4-6 -p hi`` raised the
# internal "No provider registered for api='unknown'. Sprint 6a ..." error.)


def test_resolve_model_explicit_provider_model_enriched_from_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_ai.models import get_model

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("claude-sonnet-4-6", "anthropic")
    assert m == get_model("anthropic", "claude-sonnet-4-6")
    assert m.api == "anthropic-messages"  # NOT the bare "unknown"


def test_resolve_model_slash_shorthand_splits_and_enriches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_ai.models import get_model

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("openai/gpt-4o-mini", None)  # <provider>/<model>, no --provider
    assert m.provider == "openai" and m.id == "gpt-4o-mini"
    assert m.api == get_model("openai", "gpt-4o-mini").api  # type: ignore[union-attr]


def test_resolve_model_openrouter_key_keeps_slash_form_as_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With an OpenRouter key, ``openai/gpt-4o-mini`` is a valid OpenRouter model
    # id and must NOT be split into the ``openai`` provider.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("openai/gpt-4o-mini", None)
    assert m.provider == "openrouter"


def test_resolve_model_unknown_id_known_provider_backfills_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_ai.models import get_models

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("my-unreleased-model", "anthropic")  # id absent from catalog
    assert m.provider == "anthropic" and m.id == "my-unreleased-model"
    assert m.api == get_models("anthropic")[0].api and m.api != "unknown"
    # The sibling's base_url is carried too (#98): every anthropic sibling agrees
    # on one host, so pin it explicitly instead of leaving "" for the adapter to
    # collapse into whatever its SDK default happens to be.
    assert m.base_url == get_models("anthropic")[0].base_url and m.base_url


def test_resolve_model_unknown_provider_is_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    # Truly-unknown provider (no catalog entry, no registry) → bare model whose
    # api stays "unknown". Such a model CANNOT drive a turn: it raises the
    # internal "No provider registered for api='unknown'" at the first turn, so
    # the caller must gate on ``is_runnable`` first (#98).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("some-model", "no-such-provider")
    assert m.provider == "no-such-provider" and m.api == "unknown"
    # The provider is NON-EMPTY, so a ``not model.provider`` emptiness check is
    # blind to this model — the reason the gate must be ``is_runnable`` instead.
    assert m.provider != ""


# === #98: registry-aware resolution + constrained sibling backfill ===========
# Root cause: ``resolve_model`` consulted ONLY the build-time catalog, so a bare
# id, an uncatalogued provider, or an extension/models.json provider fell to
# ``api="unknown"`` → "No provider registered for api='unknown'" at the FIRST
# user message, behind a banner that looked healthy (it prints only id/base_url).


class _FakeRegistry:
    """Minimal ``ModelRegistry`` stand-in — ``resolve_model`` uses only these two."""

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def find(self, provider: str, model_id: str) -> Model | None:
        return next(
            (m for m in self._models if m.provider == provider and m.id == model_id),
            None,
        )

    def get_all(self) -> list[Model]:
        return list(self._models)


def test_resolve_model_bare_id_without_provider_is_not_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(A) a settings.json with ``defaultModel`` but no ``defaultProvider``.

    The catalog cannot be queried without a provider, so the model stays bare and
    the caller's ``is_runnable`` gate is what must report it.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("gpt-5.4", None)
    assert m.provider == "" and m.api == "unknown"


def test_resolve_model_bare_id_unambiguous_in_registry_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare id served by exactly ONE registry provider resolves to it."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    registry = _FakeRegistry(
        [Model(id="tn-1", provider="telnaut", api="openai-completions")]
    )
    m = resolve_model("tn-1", None, registry)
    assert m.provider == "telnaut" and m.api == "openai-completions"


def test_resolve_model_bare_id_ambiguous_across_providers_refuses_to_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id served by SEVERAL providers must never be guessed.

    Picking an owner would dispatch the turn — and the credentials with it — to
    whichever vendor happened to sort first. The bundled catalog alone serves
    ``gpt-5.4`` from six providers. Staying bare hands the decision to ``/model``.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    registry = _FakeRegistry(
        [
            Model(id="shared", provider="alpha", api="openai-completions"),
            Model(id="shared", provider="beta", api="anthropic-messages"),
        ]
    )
    m = resolve_model("shared", None, registry)
    assert m.provider == "" and m.api == "unknown"


def test_resolve_model_registry_only_provider_resolves_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(D) a NON-EMPTY provider the static catalog never heard of.

    A models.json custom provider or an extension ``register_provider`` is known
    to the LIVE registry only. Before #98 the registry was never consulted, so
    this resolved ``api="unknown"`` and raised at the first turn — and because
    the provider is non-empty, the entry guard's ``not model.provider`` check
    could not catch it either.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    registry = _FakeRegistry(
        [
            Model(
                id="tn-1",
                provider="telnaut",
                api="openai-completions",
                base_url="https://telnaut.example/v1",
            )
        ]
    )
    m = resolve_model("tn-1", "telnaut", registry)
    assert m.provider == "telnaut" and m.api == "openai-completions"
    assert m.base_url == "https://telnaut.example/v1"


def test_resolve_model_registry_miss_stays_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("nope", "telnaut", _FakeRegistry([]))
    assert m.api == "unknown"


def test_resolve_model_broken_registry_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry introspection must never break launch."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)

    class _Broken:
        def find(self, provider: str, model_id: str) -> Model | None:
            raise RuntimeError("boom")

        def get_all(self) -> list[Model]:
            raise RuntimeError("boom")

    assert resolve_model("x", "telnaut", _Broken()).api == "unknown"
    assert resolve_model("x", None, _Broken()).api == "unknown"


def test_resolve_model_catalog_hit_wins_over_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact catalog hit is authoritative — the registry never shadows it."""

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    poisoned = _FakeRegistry(
        [Model(id="claude-sonnet-4-6", provider="anthropic", api="wrong-api")]
    )
    m = resolve_model("claude-sonnet-4-6", "anthropic", poisoned)
    assert m.api == "anthropic-messages"


def test_resolve_model_multi_api_provider_never_guesses_sibling_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """github-copilot spans 3 apis → an uncatalogued id under it gets NO guess.

    ``get_models("github-copilot")[0]`` is claude-haiku-4.5, so the old
    ``siblings[0].api`` backfill stamped ``api="anthropic-messages"`` with an
    empty base_url onto a COPILOT model. ``providers/anthropic.py`` does
    ``base_url=model.base_url or None``, collapsing "" to the AsyncAnthropic
    default host — so the GitHub Copilot OAuth bearer was sent to
    ``api.anthropic.com``. Note ``claude-sonnet-4-6`` is a realistic near-miss:
    the real copilot id is ``claude-sonnet-4.6`` (dot, not dash), so a plain user
    typo was enough to trigger the leak.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("claude-sonnet-4-6", "github-copilot")
    assert m.provider == "github-copilot"
    assert m.api != "anthropic-messages"  # THE leak: a copilot bearer → anthropic
    assert m.api == "unknown"  # refused to guess → the is_runnable gate reports it


# === #98: ``default_provider`` is the LOWEST rung of the provider ladder ======
# settings.json ``defaultProvider`` must never arrive as ``provider_flag``:
# ``provider_flag`` means "the user EXPLICITLY named this provider", and BOTH the
# OpenRouter-env branch and the ``<provider>/<model>`` split are gated on its
# absence. Impersonating the flag silently reroutes the turn to the persisted
# vendor, and the ``is_runnable`` gate cannot catch it because that vendor's own
# api backfills cleanly.


def test_default_provider_never_hijacks_the_slash_shorthand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("openai/gpt-4o-mini", None, None, "anthropic")
    assert m.provider == "openai" and m.id == "gpt-4o-mini"
    assert m.api == "openai-responses"
    assert "api.openai.com" in m.base_url  # NOT api.anthropic.com


def test_default_provider_never_disables_the_openrouter_env_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    # A BARE id: a "/"-only guard would not save this one.
    m = resolve_model("some-or-model", None, None, "anthropic")
    assert m.provider == "openrouter"
    assert "openrouter.ai" in m.base_url


def test_explicit_provider_flag_still_outranks_default_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("claude-sonnet-4-6", "anthropic", None, "openai")
    assert m.provider == "anthropic" and m.api == "anthropic-messages"


def test_default_provider_resolves_a_bare_id_nothing_else_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#98 (C): ``--model <id>`` with no ``--provider`` — the reason the rung exists.

    No slash, no OpenRouter key: without the persisted default the provider stays
    empty, the catalog cannot be queried, and the model resolves api="unknown".
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    assert resolve_model("gpt-5.4", None).api == "unknown"  # no rung → unresolvable
    m = resolve_model("gpt-5.4", None, None, "openai")
    assert m.provider == "openai" and m.api == "openai-responses"


def test_registry_model_without_a_base_url_keeps_its_declared_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#98: a hostless registry hit is returned VERBATIM, not dropped.

    An extension ``register_provider`` model may omit base_url (the dataclass
    default is ""), and ``_load_models`` merges it without injecting a host. It
    must not be degraded to "no match" here: ``_sibling_backfill`` would then
    stamp the catalog's unanimous api over the api the registration DECLARED,
    misrouting the turn on a second axis. ``is_runnable`` refuses it downstream
    (see tests/tui/test_runnable_models.py) — that is the gate, not this.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    # Registered UNDER a catalog provider name whose siblings are unanimously
    # anthropic-messages, but declaring a different api — the sibling backfill
    # would happily overwrite it.
    registry = _FakeRegistry(
        [Model(id="corp-x", provider="anthropic", api="openai-completions")]
    )
    m = resolve_model("corp-x", "anthropic", registry)
    assert m.api == "openai-completions"  # NOT the sibling guess
    assert m.base_url == ""  # unchanged → is_runnable refuses it


@pytest.mark.parametrize(
    ("model_id", "expected_api"),
    [
        ("claude-haiku-4.5", "anthropic-messages"),
        ("gemini-2.5-pro", "openai-completions"),
        ("gpt-5-mini", "openai-responses"),
    ],
)
def test_resolve_model_copilot_catalogued_ids_stay_exact(
    model_id: str,
    expected_api: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing to guess must not regress the REAL copilot ids across all 3 apis.

    Each carries a non-empty copilot base_url, so no adapter can fall back to a
    foreign SDK-default host.
    """

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model(model_id, "github-copilot")
    assert m.api == expected_api
    assert "githubcopilot.com" in m.base_url


def test_load_dotenv_sets_new_keys(tmp_path) -> None:
    envfile = tmp_path / ".env"
    envfile.write_text('AELIX_TEST_K=hello\n# comment\nAELIX_TEST_Q="quoted"\n\nbadline\n')
    try:
        load_dotenv(str(envfile))
        assert os.environ["AELIX_TEST_K"] == "hello"
        assert os.environ["AELIX_TEST_Q"] == "quoted"  # quotes stripped
    finally:
        os.environ.pop("AELIX_TEST_K", None)
        os.environ.pop("AELIX_TEST_Q", None)


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_TEST_EXISTING", "real")
    envfile = tmp_path / ".env"
    envfile.write_text("AELIX_TEST_EXISTING=fromfile\n")
    load_dotenv(str(envfile))
    assert os.environ["AELIX_TEST_EXISTING"] == "real"  # setdefault — real env wins


def test_load_dotenv_missing_file_is_noop(tmp_path) -> None:
    load_dotenv(str(tmp_path / "does_not_exist.env"))  # must not raise
