"""GitHub Copilot enterprise base_url fix — turn model adopts the proxy-ep host.

Defect A (enterprise Connection error): the turn Model materialized by
``resolve_model`` → ``get_model`` carries the STATIC default host
``https://api.individual.githubcopilot.com``. The token-derived proxy-ep host
(which DIFFERS for Business/Enterprise seats) is injected only by
``modify_models`` inside the ModelRegistry — reachable only via the interactive
``/model`` picker. Every non-picker path dispatched to the wrong host →
Connection error. ``enrich_copilot_base_url`` adopts the registry copy's
(modify_models-injected) base_url for github-copilot turn models.

These tests use a SYNTHETIC enterprise proxy-ep token, so the enterprise path is
exercised with only an individual/fake credential — no enterprise account needed.
"""

from __future__ import annotations

from aelix_ai.oauth.github_copilot import get_github_copilot_base_url
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import enrich_copilot_base_url

_STATIC = "https://api.individual.githubcopilot.com"
_ENTERPRISE = "https://api.enterprise.example.com"


class _FakeRegistry:
    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def find(self, provider: str, model_id: str) -> Model | None:
        for m in self._models:
            if m.provider == provider and m.id == model_id:
                return m
        return None


def _copilot(model_id: str, base_url: str) -> Model:
    return Model(
        id=model_id, api="openai-responses", provider="github-copilot", base_url=base_url
    )


def test_get_base_url_is_host_agnostic_for_enterprise_proxy_ep() -> None:
    """A synthetic enterprise proxy-ep token resolves to its own api.<host>,
    proving the base_url derivation is host-agnostic (enterprise covered by
    construction, no enterprise account required)."""

    tok = "tid=abc;exp=9999999999;proxy-ep=proxy.enterprise.example.com;x=y"
    assert get_github_copilot_base_url(tok) == "https://api.enterprise.example.com"
    tok_ind = "tid=abc;proxy-ep=proxy.individual.githubcopilot.com"
    assert get_github_copilot_base_url(tok_ind) == _STATIC


def test_enrich_adopts_registry_proxy_ep_base_url() -> None:
    """github-copilot turn model (static host) adopts the registry's enterprise host."""

    turn = _copilot("gpt-5.4", _STATIC)
    reg = _FakeRegistry([_copilot("gpt-5.4", _ENTERPRISE)])
    out = enrich_copilot_base_url(turn, reg)
    assert out.base_url == _ENTERPRISE
    assert out.api == "openai-responses"  # only base_url changes


def test_enrich_noop_when_registry_none() -> None:
    turn = _copilot("gpt-5.4", _STATIC)
    assert enrich_copilot_base_url(turn, None) is turn


def test_enrich_noop_on_registry_miss() -> None:
    turn = _copilot("gpt-5.4", _STATIC)
    reg = _FakeRegistry([])  # find() returns None
    assert enrich_copilot_base_url(turn, reg).base_url == _STATIC


def test_enrich_does_not_touch_non_copilot_provider() -> None:
    """OpenRouter env base_url override must NOT be clobbered by the registry."""

    turn = Model(
        id="anthropic/claude-3",
        api="openai-completions",
        provider="openrouter",
        base_url="https://my-custom-openrouter.example/api/v1",
    )
    # Even if the registry had an openrouter row with a different base_url, a
    # non-copilot provider is left untouched.
    reg = _FakeRegistry(
        [
            Model(
                id="anthropic/claude-3",
                api="openai-completions",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
            )
        ]
    )
    assert enrich_copilot_base_url(turn, reg).base_url == (
        "https://my-custom-openrouter.example/api/v1"
    )


def test_enrich_noop_when_hosts_already_match() -> None:
    """Individual account: registry host == static host → returns input unchanged."""

    turn = _copilot("gpt-5.4", _STATIC)
    reg = _FakeRegistry([_copilot("gpt-5.4", _STATIC)])
    assert enrich_copilot_base_url(turn, reg) is turn
