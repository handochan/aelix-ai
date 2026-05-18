"""Sprint 6b (Phase 4.2, §B) — env-api-keys table tests.

Pi parity: ``env-api-keys.ts`` (SHA 734e08e). Verifies the full Pi
provider→envvar table is mirrored verbatim and that ``find_env_keys`` /
``get_env_api_key`` honor :data:`os.environ`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_ai.providers._env_api_keys import (
    ENV_API_KEYS,
    find_env_keys,
    get_env_api_key,
)

_FIXTURE = (
    Path(__file__).parent.parent
    / "pi_parity"
    / "fixtures"
    / "pi_openai_completions_734e08e.json"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip every candidate env var so individual tests can opt in via
    # monkeypatch.setenv.
    for vars_list in ENV_API_KEYS.values():
        for name in vars_list:
            monkeypatch.delenv(name, raising=False)


def test_pi_provider_envvar_map_subset_of_aelix() -> None:
    """Aelix's table must contain every Pi entry in the W0 fixture."""

    fixture = json.loads(_FIXTURE.read_text())
    pi_map = {
        k: v for k, v in fixture["env_api_key_mapping"].items()
        if not k.startswith("_")
    }
    for provider, envs in pi_map.items():
        assert provider in ENV_API_KEYS, f"missing provider: {provider}"
        assert ENV_API_KEYS[provider] == envs, (
            f"env vars for {provider} drifted from Pi"
        )


def test_unknown_provider_returns_none() -> None:
    assert find_env_keys("not-a-real-provider") is None
    assert get_env_api_key("not-a-real-provider") is None


def test_find_env_keys_empty_when_no_vars_set() -> None:
    # Every provider has at least one env var defined in the table; with
    # the fixture-cleared environment none should be configured.
    assert find_env_keys("openai") is None
    assert find_env_keys("openrouter") is None
    assert find_env_keys("anthropic") is None


def test_get_env_api_key_returns_first_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_env_api_key("openai") == "sk-test"


def test_anthropic_oauth_takes_precedence_over_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat-xx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-yy")
    assert get_env_api_key("anthropic") == "sk-ant-oat-xx"


def test_anthropic_falls_back_to_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-yy")
    assert get_env_api_key("anthropic") == "sk-ant-api-yy"


def test_find_env_keys_returns_configured_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-zz")
    assert find_env_keys("anthropic") == ["ANTHROPIC_API_KEY"]


def test_openrouter_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert get_env_api_key("openrouter") == "or-test"


def test_moonshot_aliases_share_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "moon-test")
    assert get_env_api_key("moonshotai") == "moon-test"
    assert get_env_api_key("moonshotai-cn") == "moon-test"


def test_opencode_aliases_share_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-test")
    assert get_env_api_key("opencode") == "oc-test"
    assert get_env_api_key("opencode-go") == "oc-test"


def test_all_providers_resolvable_via_their_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Each provider's first env var, when set, must surface as the key.
    for provider, envs in ENV_API_KEYS.items():
        monkeypatch.setenv(envs[0], f"key-for-{provider}")
        assert get_env_api_key(provider) == f"key-for-{provider}"
        monkeypatch.delenv(envs[0], raising=False)


def test_table_has_minimum_thirty_entries() -> None:
    """Sprint 6b table parity: 30 known providers (Pi fixture +
    ``minimax`` row which is in Pi env-api-keys but absent from the
    fixture's openai-completions provider-detect view)."""

    assert len(ENV_API_KEYS) >= 30
