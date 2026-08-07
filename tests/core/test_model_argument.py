"""``/model <id>`` registry resolution tests (#134).

Proves :func:`aelix_coding_agent.core.model_argument.resolve_model_argument`:

* a bare id served by ONE available provider → that scoped ``(provider, id)``;
* an id served by SEVERAL → refused with the candidates listed, unless the
  session's current provider is one of them (then it wins);
* an unknown id → refused, naming the picker;
* an id the registry knows but cannot offer → refused, naming the provider;
* a ``<provider>/<id>`` argument → UNDECIDED (the launch-path resolver owns it);
* the ``/scoped-models`` allow-list narrows the offered pool;
* every degradation (no registry, empty registry, a registry that RAISES) →
  UNDECIDED, so ``/model`` keeps its previous behaviour instead of refusing.

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


async def test_provider_slash_id_is_left_to_the_launch_resolver() -> None:
    # UNDECIDED (both fields None): under an OPENROUTER_API_KEY the slash form is
    # how OpenRouter's own canonical ids are written, so re-reading it here would
    # move the turn to a different vendor.
    result = await resolve_model_argument(
        "anthropic/claude-haiku-4-5", registry=_Registry([ANTHROPIC_HAIKU])
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
