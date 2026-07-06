"""Tests for cli/runtime_bootstrap — .env load + model resolution (OpenRouter)."""

from __future__ import annotations

import os

import pytest
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_API
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


def test_resolve_model_unknown_provider_is_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    # Truly-unknown provider → bare model. The entry-point guard turns this into
    # a graceful "No API key" message rather than the internal adapter error.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    m = resolve_model("some-model", "no-such-provider")
    assert m.provider == "no-such-provider" and m.api == "unknown"


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
