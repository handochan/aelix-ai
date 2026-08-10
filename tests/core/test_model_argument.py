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

And the one exception carved out in #136, because the registry is a BUILD-TIME
snapshot and a gateway model newer than the build was otherwise unreachable by
name (measured: 179 of OpenRouter's 400 live ids are missing from it):

* ``<provider>/<uncatalogued-id>`` where ``<provider>`` is in the offered pool →
  a switch WITH ``caution`` set, carrying that provider's own unanimous
  ``api``/``base_url`` and no invented cost/context;
* everything that would let it become #134 again — a bare id, an unauthed or
  allow-list-excluded prefix, a provider whose siblings disagree on ``api`` or
  ``base_url`` — is still refused, each pinned by its own test below.

The end-to-end handler behaviour (no switch / no persist / no success line on a
refusal) is asserted in ``tests/tui/test_commands.py``.
"""

from __future__ import annotations

from typing import Any

from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.core.model_argument import resolve_model_argument


def _model(
    provider: str,
    id: str,
    base_url: str = "https://example.invalid",
    api: str = "anthropic-messages",
) -> Model:
    return Model(
        id=id, name=id, api=api, provider=provider, base_url=base_url
    )


ANTHROPIC_HAIKU = _model("anthropic", "claude-haiku-4-5", "https://api.anthropic.com")
COPILOT_HAIKU = _model("github-copilot", "claude-haiku-4-5")
OPENAI_GPT = _model("openai", "gpt-4o")
# A REAL OpenRouter id — OpenRouter's Anthropic models are DOT-versioned. The
# dash-versioned form is a different namespace (a provider/id reference), and
# conflating the two is what kept #134 alive through the slash carve-out.
OPENROUTER_SONNET = _model(
    "openrouter",
    "anthropic/claude-sonnet-4.5",
    "https://openrouter.ai/api/v1",
    # The REAL api OpenRouter speaks (measured: all 266 catalog rows are
    # openai-completions). It differs from every other fixture here, so a
    # backfill that adopted the wrong provider's protocol is visible.
    api="openai-completions",
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


async def test_uncatalogued_id_under_an_authed_provider_backfills() -> None:
    # #136 — REWRITTEN from test_unknown_qualified_id_is_refused. The registry is
    # a BUILD-TIME snapshot (measured: 179 of OpenRouter's 400 live ids are
    # missing from it), so refusing every uncatalogued id made a model newer than
    # the build unreachable by name. ``openrouter/`` NAMES a provider this session
    # is credentialled for, which is what licenses the id — the same rule as pi's
    # buildFallbackModel, which fires only when --provider is explicit.
    result = await resolve_model_argument(
        "openrouter/gpt-4o", registry=_Registry([OPENROUTER_SONNET, OPENAI_GPT])
    )
    assert result.error is None
    assert result.model is not None
    assert result.model.provider == "openrouter"
    assert result.model.id == "gpt-4o"
    # It inherits the NAMED provider's protocol and host, never openai's — the
    # two fixtures deliberately disagree on ``api``.
    assert result.model.api == "openai-completions"
    assert OPENAI_GPT.api == "anthropic-messages"
    assert result.model.base_url == "https://openrouter.ai/api/v1"
    # The caution field cannot exist before the fix, so it is what makes this
    # test impossible to pass by accident.
    assert result.caution is not None
    assert "gpt-4o" in result.caution


async def test_backfilled_model_carries_no_invented_cost_or_context() -> None:
    # #136 SHAPE. dataclasses.replace(sibling, id=...) would inherit the
    # sibling's context_window and per-million pricing — measured on the real
    # catalog: 256000 tokens and $2/$8 for a model NOTHING is known about, which
    # /cost would then report as fact. The bare shape's zeros are also exactly
    # what the same session gets after a restart, when the persisted pair goes
    # back through runtime_bootstrap.resolve_model, so the two paths agree.
    rich = Model(
        id="anthropic/claude-sonnet-4.5",
        name="Claude Sonnet 4.5",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=256_000,
        max_tokens=4096,
        cost=ModelCost(input=2.0, output=8.0),
    )
    result = await resolve_model_argument(
        "openrouter/brand-new-id", registry=_Registry([rich])
    )
    assert result.model is not None
    assert result.model.context_window == 0
    assert result.model.max_tokens == 0
    assert result.model.cost.input == 0.0
    assert result.model.cost.output == 0.0


async def test_qualified_id_under_an_UNAUTHED_provider_is_still_refused() -> None:
    # #134's hard fence, and the reason the backfill keys off the POOL. The
    # session has only OpenRouter; ``anthropic/claude-haiku-4-5`` is dash-versioned
    # so it is NOT an OpenRouter id. Before #134 this came back as a fully-formed
    # openrouter/anthropic/claude-haiku-4-5 — the user's own vendor and
    # credentials swapped without being asked. The #136 backfill must NOT be a
    # back door to that: anthropic is absent from the pool, so nothing licenses it.
    result = await resolve_model_argument(
        "anthropic/claude-haiku-4-5",
        registry=_Registry(
            [ANTHROPIC_HAIKU, OPENROUTER_SONNET], available=[OPENROUTER_SONNET]
        ),
    )
    assert result.model is None
    assert result.error is not None
    assert "anthropic" in result.error
    assert "/login" in result.error
    # Belt and braces: the refusal must not have quietly produced an
    # openrouter-scoped model under any name.
    assert "openrouter/anthropic/claude-haiku-4-5" not in result.error
    assert result.caution is None


async def test_bare_uncatalogued_id_gets_no_backfill() -> None:
    # #134's repro verbatim. No slash means no provider was named, so nothing
    # licenses picking one — the single configured gateway must NOT volunteer.
    # This is the rule that keeps the hatch from becoming "whatever I am logged
    # in to wins", which is the bug itself.
    result = await resolve_model_argument(
        "kimi-k3-brand-new", registry=_Registry([OPENROUTER_SONNET])
    )
    assert result.model is None
    assert result.error is not None
    assert result.caution is None


async def test_vendor_slashed_uncatalogued_id_is_refused_accepted_regression() -> None:
    # THE ACCEPTED COST of #136, pinned so nobody "fixes" it by widening the rule.
    # ``moonshotai`` is itself a catalogued first-party provider, so reading a
    # known prefix as a gateway VENDOR segment would read
    # ``anthropic/claude-haiku-4-5`` the same way — that IS #134. Separating the
    # two needs the gateway's LIVE model list (OpenRouter really does serve
    # moonshotai/… and really does not serve dash-versioned anthropic/…), i.e.
    # network I/O inside /model. The user pays one extra ``openrouter/`` prefix.
    moonshot = _model("moonshotai", "kimi-k2.6", "https://api.moonshot.ai/v1")
    result = await resolve_model_argument(
        "moonshotai/kimi-k3-brand-new",
        registry=_Registry([moonshot, OPENROUTER_SONNET], available=[OPENROUTER_SONNET]),
    )
    assert result.model is None
    assert result.error is not None
    assert result.caution is None


async def test_backfill_declines_a_provider_with_no_unanimous_api() -> None:
    # The #98 guard, inherited from cli.runtime_bootstrap._sibling_backfill. Six
    # catalog providers span several apis; github-copilot's catalog spans
    # anthropic-messages + openai-completions + openai-responses, and picking the
    # first sibling once routed a Copilot id to the ANTHROPIC adapter — whose
    # ``base_url or None`` collapses to the SDK default host, so a Copilot OAuth
    # bearer left the process for api.anthropic.com. Refuse rather than guess.
    copilot_claude = _model(
        "github-copilot", "claude-haiku-4.5", "https://api.githubcopilot.com"
    )
    copilot_gpt = _model(
        "github-copilot",
        "gpt-5.4",
        "https://api.githubcopilot.com",
        api="openai-responses",
    )
    result = await resolve_model_argument(
        "github-copilot/gpt-5.7-brand-new",
        registry=_Registry([copilot_claude, copilot_gpt]),
    )
    assert result.model is None
    assert result.error is not None
    assert result.caution is None


async def test_backfill_declines_a_provider_with_no_unanimous_base_url() -> None:
    # Stricter than _sibling_backfill, deliberately: it emits ``base_url=""`` when
    # siblings disagree, and an empty base_url is the #98 egress shape itself
    # (every adapter resolves ``base_url or None`` → the SDK's FIRST-PARTY host).
    # Decline instead. amazon-bedrock is the real single-api/many-host case.
    east = _model("amazon-bedrock", "claude-a", "https://bedrock.us-east-1.invalid")
    west = _model("amazon-bedrock", "claude-b", "https://bedrock.us-west-2.invalid")
    result = await resolve_model_argument(
        "amazon-bedrock/claude-brand-new", registry=_Registry([east, west])
    )
    assert result.model is None
    assert result.error is not None


async def test_backfill_declines_a_provider_whose_siblings_declare_no_host() -> None:
    # An extension's register_provider may omit base_url entirely (the dataclass
    # default is ""). Unanimous, but unanimously hostless — adopting it would put
    # that provider's credentials on the SDK default host (#98). Refuse.
    hostless = _model("mycorp", "corp-x", "")
    result = await resolve_model_argument(
        "mycorp/corp-brand-new", registry=_Registry([hostless])
    )
    assert result.model is None
    assert result.error is not None


async def test_backfill_sources_siblings_from_the_REGISTRY_not_the_static_catalog() -> (
    None
):
    # cli.runtime_bootstrap._sibling_backfill reads aelix_ai.models.get_models(),
    # which knows nothing about a models.json custom provider or an extension
    # register_provider — reusing it verbatim would make the hatch silently never
    # fire for exactly the users who hand-configured a provider, with no error to
    # point at. ``mycorp`` is in no static catalog, so this passes only because
    # siblings come from the registry's own get_all().
    corp = _model("mycorp", "corp-a", "https://llm.mycorp.invalid/v1", api="openai-completions")
    result = await resolve_model_argument(
        "mycorp/corp-brand-new", registry=_Registry([corp])
    )
    assert result.model is not None
    assert result.model.provider == "mycorp"
    assert result.model.id == "corp-brand-new"
    assert result.model.api == "openai-completions"
    assert result.model.base_url == "https://llm.mycorp.invalid/v1"
    assert result.caution is not None


async def test_backfill_respects_the_scoped_models_allow_list() -> None:
    # The predicate reads the NARROWED pool, not raw has_configured_auth: an
    # allow-list that excludes openrouter means "do not offer me this provider",
    # and the hatch must honour that rather than route around it.
    class _SM:
        def get_enabled_models(self) -> list[str]:
            return ["github-copilot/claude-haiku-4-5"]

    result = await resolve_model_argument(
        "openrouter/brand-new-id",
        registry=_Registry([COPILOT_HAIKU, OPENROUTER_SONNET]),
        settings_manager=_SM(),
    )
    assert result.model is None
    assert result.error is not None


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
