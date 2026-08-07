"""``/model <id>`` registry resolution tests (#134).

Proves :func:`aelix_coding_agent.core.model_argument.resolve_model_argument`:

* a bare id served by ONE available provider → that scoped ``(provider, id)``;
* an id served by SEVERAL → refused with the candidates listed, unless the
  session's current provider is one of them (then it wins);
* an unknown id → refused, naming the picker;
* an id the registry knows but cannot offer → refused, naming the provider;
* a ``<provider>/<id>`` reference → the NAMED provider, with no current-provider
  tie-break, while a gateway's vendor-slashed id (OpenRouter's
  ``anthropic/claude-sonnet-4.5``) still resolves to the gateway;
* the ``/scoped-models`` allow-list narrows the offered pool;
* every degradation (no registry, empty registry, a registry that RAISES, an
  unreadable auth filter) → UNDECIDED, so ``/model`` keeps its previous
  behaviour instead of refusing.

The end-to-end handler behaviour (no switch / no persist / no success line on a
refusal) is asserted in ``tests/tui/test_commands.py``.
"""

from __future__ import annotations

from typing import Any

from aelix_ai.streaming import Model
from aelix_coding_agent.core.model_argument import resolve_model_argument


def _model(provider: str, id: str, base_url: str = "https://example.invalid") -> Model:
    return Model(
        id=id, name=id, api="anthropic-messages", provider=provider, base_url=base_url
    )


ANTHROPIC_HAIKU = _model("anthropic", "claude-haiku-4-5", "https://api.anthropic.com")
COPILOT_HAIKU = _model("github-copilot", "claude-haiku-4-5")
OPENAI_GPT = _model("openai", "gpt-4o")
# A REAL OpenRouter id — OpenRouter's Anthropic models are DOT-versioned. The
# dash-versioned form is a different namespace (a provider/id reference), and
# conflating the two is what kept #134 alive through the slash carve-out.
OPENROUTER_SONNET = _model(
    "openrouter", "anthropic/claude-sonnet-4.5", "https://openrouter.ai/api/v1"
)


class _Registry:
    def __init__(self, models: list[Model], available: list[Model] | None = None):
        self._models = models
        self._available = models if available is None else available

    def get_all(self) -> list[Model]:
        return list(self._models)

    def get_available(self) -> list[Model]:
        return list(self._available)


class _Raises:
    def get_all(self) -> list[Model]:
        raise RuntimeError("registry unavailable")

    def get_available(self) -> list[Model]:
        raise RuntimeError("registry unavailable")


class _AvailableRaises:
    """``get_all`` works, ``get_available`` does not (F3).

    The auth filter is the whole point of the pool: without it, resolution would
    span providers this session has no credentials for.
    """

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def get_all(self) -> list[Model]:
        return list(self._models)

    def get_available(self) -> list[Model]:
        raise RuntimeError("auth storage unreadable")


class _CurrentModel:
    def __init__(self, provider: str) -> None:
        self.provider = provider


async def test_unique_bare_id_resolves_to_the_scoped_pair() -> None:
    result = await resolve_model_argument(
        "claude-haiku-4-5", registry=_Registry([ANTHROPIC_HAIKU, OPENAI_GPT])
    )
    assert result.error is None
    assert result.model is ANTHROPIC_HAIKU


async def test_bare_id_match_is_case_insensitive() -> None:
    # pi's find_exact_model_reference_match folds case; the diagnosis path must
    # agree with it or a case-mismatched id would report a false "unknown".
    result = await resolve_model_argument(
        "CLAUDE-Haiku-4-5", registry=_Registry([ANTHROPIC_HAIKU])
    )
    assert result.model is ANTHROPIC_HAIKU


async def test_ambiguous_bare_id_is_refused_with_candidates() -> None:
    result = await resolve_model_argument(
        "claude-haiku-4-5", registry=_Registry([ANTHROPIC_HAIKU, COPILOT_HAIKU])
    )
    assert result.model is None
    assert result.error is not None
    assert "anthropic/claude-haiku-4-5" in result.error
    assert "github-copilot/claude-haiku-4-5" in result.error


async def test_current_provider_breaks_the_tie() -> None:
    result = await resolve_model_argument(
        "claude-haiku-4-5",
        registry=_Registry([ANTHROPIC_HAIKU, COPILOT_HAIKU]),
        current_model=_CurrentModel("github-copilot"),
    )
    assert result.model is COPILOT_HAIKU


async def test_unknown_id_is_refused_and_points_at_the_picker() -> None:
    result = await resolve_model_argument(
        "no-such-model", registry=_Registry([ANTHROPIC_HAIKU])
    )
    assert result.model is None
    assert result.error is not None
    assert "no-such-model" in result.error
    assert "/model" in result.error


async def test_known_but_unavailable_id_names_its_provider() -> None:
    # Served by anthropic, but anthropic is not in get_available() (no
    # credentials) — "unknown model" would send the user to fix the wrong thing.
    result = await resolve_model_argument(
        "claude-haiku-4-5",
        registry=_Registry([ANTHROPIC_HAIKU, OPENAI_GPT], available=[OPENAI_GPT]),
    )
    assert result.model is None
    assert result.error is not None
    assert "anthropic" in result.error
    assert "/login" in result.error


async def test_qualified_reference_resolves_to_the_named_provider() -> None:
    # The slash form goes through the pool too. Exempting it preserved #134: a
    # dash-versioned anthropic/claude-haiku-4-5 is NOT an OpenRouter id, so the
    # launch resolver returned openrouter/anthropic/claude-haiku-4-5 and moved
    # the session's vendor AND credentials without being asked.
    result = await resolve_model_argument(
        "anthropic/claude-haiku-4-5",
        registry=_Registry([ANTHROPIC_HAIKU, OPENROUTER_SONNET]),
    )
    assert result.model is ANTHROPIC_HAIKU


async def test_vendor_slashed_gateway_id_resolves_to_the_gateway() -> None:
    # The other namespace: no available provider claims this as a provider/id
    # split, so pi's resolver falls through to the whole-string id match and the
    # OpenRouter copy wins — on OpenRouter's own host.
    result = await resolve_model_argument(
        "anthropic/claude-sonnet-4.5",
        registry=_Registry([ANTHROPIC_HAIKU, OPENROUTER_SONNET]),
    )
    assert result.model is OPENROUTER_SONNET


async def test_canonical_key_reaches_a_gateway_copy() -> None:
    # provider/id where the id ITSELF contains a slash — how a user asks for the
    # gateway's copy when a real provider would otherwise claim the split.
    result = await resolve_model_argument(
        "openrouter/anthropic/claude-sonnet-4.5",
        registry=_Registry([ANTHROPIC_HAIKU, OPENROUTER_SONNET]),
    )
    assert result.model is OPENROUTER_SONNET


async def test_qualified_reference_gets_no_current_provider_tiebreak() -> None:
    # A bare id tie-breaks to the current provider; a QUALIFIED one must not —
    # it NAMES a provider, so substituting a different one is the exact failure
    # this module exists to prevent. Both gateways serve this id verbatim.
    vercel = _model("vercel-ai-gateway", "anthropic/claude-sonnet-4.5")
    result = await resolve_model_argument(
        "anthropic/claude-sonnet-4.5",
        registry=_Registry([OPENROUTER_SONNET, vercel]),
        current_model=_CurrentModel("openrouter"),
    )
    assert result.model is None
    assert result.error is not None
    assert "openrouter/anthropic/claude-sonnet-4.5" in result.error
    assert "vercel-ai-gateway/anthropic/claude-sonnet-4.5" in result.error


async def test_unknown_qualified_id_is_refused() -> None:
    # openrouter/gpt-4o: OpenRouter serves openai/gpt-4o, not this. It used to
    # report success on openrouter/openrouter/gpt-4o and 400 at the next send.
    result = await resolve_model_argument(
        "openrouter/gpt-4o", registry=_Registry([OPENROUTER_SONNET, OPENAI_GPT])
    )
    assert result.model is None
    assert result.error is not None
    assert "openrouter/gpt-4o" in result.error


async def test_undecided_when_the_auth_filter_is_unreadable() -> None:
    # F3: get_all() works but get_available() raises. Falling back to get_all()
    # would resolve across providers with NO configured credentials, trading a
    # clear refusal for a confusing auth failure at send time.
    secret = _model("secret-corp", "corp-only-model")
    result = await resolve_model_argument(
        "corp-only-model", registry=_AvailableRaises([secret])
    )
    assert result.model is None
    assert result.error is None


async def test_scoped_models_allow_list_narrows_the_pool() -> None:
    class _SM:
        def get_enabled_models(self) -> list[str]:
            return ["github-copilot/claude-haiku-4-5"]

    # Both providers are authed, so the id is ambiguous — but the allow-list
    # leaves exactly one candidate, which resolves without a prompt.
    result = await resolve_model_argument(
        "claude-haiku-4-5",
        registry=_Registry([ANTHROPIC_HAIKU, COPILOT_HAIKU]),
        settings_manager=_SM(),
    )
    assert result.model is COPILOT_HAIKU


async def test_degrades_to_undecided_without_a_usable_registry() -> None:
    # No registry (headless / RPC), an empty one (a stub, or a load failure), and
    # one that raises must all defer rather than refuse: resolution is never
    # allowed to be the thing that breaks /model.
    for registry in (None, _Registry([]), _Raises()):
        result = await resolve_model_argument("claude-haiku-4-5", registry=registry)
        assert result.model is None, registry
        assert result.error is None, registry


async def test_empty_argument_is_undecided() -> None:
    result: Any = await resolve_model_argument(
        "   ", registry=_Registry([ANTHROPIC_HAIKU])
    )
    assert result.model is None
    assert result.error is None
