"""Sprint 6e · Phase 4.5 — Copilot ``modify_models`` Protocol integration.

Wires :data:`GITHUB_COPILOT_OAUTH_PROVIDER.modify_models` and asserts
``Model.base_url`` injection per Pi P-132.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aelix_ai.oauth import GITHUB_COPILOT_OAUTH_PROVIDER, OAuthCredentials


@dataclass
class _FakeModel:
    """Minimal Model stand-in (Sprint 6e doesn't depend on the real
    :class:`aelix_ai.streaming.Model` shape for ``modify_models``)."""

    id: str
    provider: str
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def test_modify_models_injects_proxy_ep_base_url() -> None:
    """End-to-end: Copilot provider Protocol callback updates base_url."""

    creds = OAuthCredentials(
        refresh="rt",
        access="tid=x;exp=1;proxy-ep=proxy.individual.githubcopilot.com",
        expires=1,
    )
    models = [
        _FakeModel(id="claude-3-5", provider="github-copilot"),
        _FakeModel(id="gpt-4", provider="openai"),
    ]
    result = GITHUB_COPILOT_OAUTH_PROVIDER.modify_models(models, creds)

    # Copilot-routed model gets base_url injected.
    assert result[0].base_url == "https://api.individual.githubcopilot.com"
    # Non-Copilot model passes through untouched.
    assert result[1].base_url is None
    # Returned list MUST be NEW (Pi parity ``{...m, baseUrl}``).
    assert result[0] is not models[0]


def test_modify_models_enterprise_domain_fallback() -> None:
    """Token without proxy-ep + enterprise URL → enterprise base URL.

    Sprint 6e W6 (P-147): persisted extras key is ``enterpriseUrl``
    (Pi camelCase, raw user input).
    """

    creds = OAuthCredentials(
        refresh="rt",
        access="no-proxy-ep-here",
        expires=1,
        extra={"enterpriseUrl": "ghe.example.com"},
    )
    models = [_FakeModel(id="m1", provider="github-copilot")]
    result = GITHUB_COPILOT_OAUTH_PROVIDER.modify_models(models, creds)
    assert result[0].base_url == "https://copilot-api.ghe.example.com"
