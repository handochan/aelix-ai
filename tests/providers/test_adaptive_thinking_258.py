"""#258 — which Claude rows think adaptively, and what "off" means to them.

On 0985fcf the answer was a literal substring whitelist of ``opus-4-6`` /
``opus-4-7`` / ``sonnet-4-6``, so every other Claude id built
``thinking: {"type": "enabled", "budget_tokens": N}``. Measured against
``api.anthropic.com`` on 2026-09-09, that request is a
``400 invalid_request_error`` on ``claude-opus-5``, ``claude-opus-4-8``,
``claude-sonnet-5``, ``claude-fable-5`` and ``claude-fable-5-1`` at every
level, and the two ``fable`` rows reject ``thinking.type.disabled`` as well
(``req_011CerzNQTwZdGcBeTt9o3Ea``) — i.e. they could not be used at all, with
thinking on or off.

Every test here fails on the pre-#258 resolution.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from aelix_ai.models import EXTENDED_THINKING_LEVELS, get_model
from aelix_ai.models_generated import MODELS
from aelix_ai.providers._anthropic_transforms import (
    lowest_thinking_level,
    resolve_anthropic_thinking,
    supports_adaptive_thinking,
    supports_thinking_off,
)
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import Context, Model, SimpleStreamOptions

#: The six first-party rows #258 is about. ``claude-opus-4-7`` is the control:
#: the one id the old whitelist carried, and the one that answered normally
#: through the adaptive branch in the same measurement run.
_AFFECTED = (
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
)

#: Pi ``supportsAdaptiveThinking`` (anthropic.ts:692-702) verbatim — the whole
#: rule before #258, kept here so the "this only adds rows" walk below cannot
#: drift with the implementation.
_PI_MARKERS = (
    "opus-4-6",
    "opus-4.6",
    "opus-4-7",
    "opus-4.7",
    "sonnet-4-6",
    "sonnet-4.6",
)


def _anthropic_messages_rows() -> list[Model]:
    return [
        model
        for provider_models in MODELS.values()
        for model in provider_models.values()
        if model.api == "anthropic-messages" and model.reasoning
    ]


def _row(provider: str, model_id: str) -> Model:
    model = get_model(provider, model_id)
    assert model is not None, f"{provider}/{model_id} left the catalog"
    return model


# === the decision =========================================================


def test_every_affected_first_party_row_takes_the_adaptive_branch() -> None:
    """The five 400s plus their control, at both measured levels.

    ``high`` and ``xhigh`` stand for every level: what the API rejected was
    the ``thinking.type`` value, which the budget path sends identically at
    all five tiers.
    """

    for model_id in _AFFECTED:
        model = _row("anthropic", model_id)
        assert supports_adaptive_thinking(model) is True, model_id
        for level in ("high", "xhigh"):
            extra, max_tokens, needs_beta = resolve_anthropic_thinking(
                model, level, 32000
            )
            assert extra["thinking"]["type"] == "adaptive", (model_id, level)
            assert "budget_tokens" not in extra["thinking"], (model_id, level)
            assert extra["output_config"] == {"effort": level}, (model_id, level)
            # Adaptive rows have interleaved thinking built in, so the beta
            # header is not needed and the caller's cap is passed through.
            assert (max_tokens, needs_beta) == (32000, False), (model_id, level)


def test_the_catalog_flag_decides_in_both_directions() -> None:
    """``compat.forceAdaptiveThinking`` is read, and it is believed.

    12 shipped rows carry it (counted 2026-09-09) and none carries it as
    ``false``; the two synthetic rows below pin both answers, so a catalog
    refresh can move a row either way without touching this package.
    """

    flagged = [
        model
        for model in _anthropic_messages_rows()
        if (model.compat or {}).get("forceAdaptiveThinking")
    ]
    assert len(flagged) == 12
    assert {model.provider for model in flagged} == {
        "anthropic",
        "cloudflare-ai-gateway",
        "opencode",
    }
    for model in flagged:
        assert supports_adaptive_thinking(model) is True, model.id

    # A flag on an id no family regex would ever match still wins…
    off_family = Model(
        api="anthropic-messages",
        id="some-proxy/mystery-thinker",
        provider="test",
        reasoning=True,
        compat={"forceAdaptiveThinking": True},
    )
    assert supports_adaptive_thinking(off_family) is True
    # …and so does an explicit ``false`` on an id the regex does match.
    opted_out = replace(
        _row("anthropic", "claude-opus-5"), compat={"forceAdaptiveThinking": False}
    )
    assert supports_adaptive_thinking(opted_out) is False
    assert resolve_anthropic_thinking(opted_out, "high", 32000)[0]["thinking"][
        "type"
    ] == "enabled"


def test_the_mirrors_the_catalog_forgot_are_adaptive_too() -> None:
    """The flag alone would leave the same models broken behind a proxy.

    ``github-copilot`` and ``vercel-ai-gateway`` serve these models over the
    same Anthropic Messages API and carry **no** ``compat`` at all — asserted
    below, so this test starts failing the day upstream flags them, which is
    the day the family fallback stops being load-bearing for them.
    ``amazon-bedrock`` mirrors are not listed: their rows are
    ``bedrock-converse-stream`` and never reach this resolver.
    """

    mirrors = (
        ("github-copilot", "claude-opus-5"),
        ("github-copilot", "claude-opus-4.8"),
        ("github-copilot", "claude-sonnet-5"),
        ("github-copilot", "claude-fable-5"),
        ("github-copilot", "claude-fable-5.1"),
        ("github-copilot", "claude-opus-4.7"),
        ("vercel-ai-gateway", "anthropic/claude-opus-5"),
        ("vercel-ai-gateway", "anthropic/claude-opus-5-fast"),
        ("vercel-ai-gateway", "anthropic/claude-fable-5.1"),
        ("cloudflare-ai-gateway", "claude-opus-4-7"),
        ("opencode", "claude-sonnet-5"),
    )
    for provider, model_id in mirrors:
        model = _row(provider, model_id)
        assert supports_adaptive_thinking(model) is True, (provider, model_id)

    for provider, model_id in mirrors:
        if provider in ("github-copilot", "vercel-ai-gateway"):
            flag = (_row(provider, model_id).compat or {}).get(
                "forceAdaptiveThinking"
            )
            assert flag is None, (provider, model_id)

    assert not [
        model
        for provider_models in MODELS.values()
        for model in provider_models.values()
        if model.api == "bedrock-converse-stream"
        and (model.compat or {}).get("forceAdaptiveThinking")
    ]


def test_the_fix_only_adds_rows() -> None:
    """No row that worked on 0985fcf loses the adaptive path.

    The old whitelist is a subset of the new answer, and the new answer is
    strictly bigger — 37 adaptive rows against 15, the 22 added being the
    4.8 / 5-family rows across ``anthropic`` and its three mirrors (counted
    2026-09-09).
    """

    rows = _anthropic_messages_rows()
    old = [m for m in rows if any(k in (m.id or "") for k in _PI_MARKERS)]
    new = [m for m in rows if supports_adaptive_thinking(m)]
    assert old, "the pi whitelist matched nothing — the walk lost its subject"
    for model in old:
        assert supports_adaptive_thinking(model) is True, model.id
    assert len(old) == 15
    assert len(new) == 37
    for model in new:
        assert "claude" in (model.id or ""), model.id


def test_a_budget_row_still_sends_budget_tokens() -> None:
    """The other side of the branch is untouched.

    ``claude-haiku-4-5`` is a first-party row that still takes the budget
    path, and is the row Anthropic's 1024 ``budget_tokens`` floor was measured
    on (``req_011CerQpmN4cr9DZAtFwqYzn``).
    """

    model = _row("anthropic", "claude-haiku-4-5")
    assert supports_adaptive_thinking(model) is False
    extra, max_tokens, needs_beta = resolve_anthropic_thinking(model, "high", 32000)
    assert extra["thinking"]["type"] == "enabled"
    assert extra["thinking"]["budget_tokens"] == 16384
    assert (max_tokens, needs_beta) == (48384, True)
    assert "output_config" not in extra


# === what "off" means =====================================================


def test_off_is_a_request_every_affected_row_accepts() -> None:
    """"Off" must produce a request that works — that is the whole fix.

    Two shapes, because the rows differ: ``thinking.type.disabled`` where the
    row supports it (measured 2026-09-09: ``claude-opus-5``,
    ``claude-opus-4-8`` and ``claude-sonnet-5`` all answered with it), and
    adaptive at the row's lowest effort where it does not — ``claude-fable-5``
    and ``claude-fable-5-1`` 400 on ``disabled``
    (``req_011CerzNQTwZdGcBeTt9o3Ea``), so the least thinking they offer is
    the honest answer. Neither shape is the ``enabled`` one both reject.
    """

    for model_id in _AFFECTED:
        model = _row("anthropic", model_id)
        for level in ("off", None):
            extra, max_tokens, needs_beta = resolve_anthropic_thinking(
                model, level, 32000
            )
            thinking = extra["thinking"]
            assert thinking["type"] != "enabled", (model_id, level)
            assert (max_tokens, needs_beta) == (32000, False), (model_id, level)
            if supports_thinking_off(model):
                assert extra == {"thinking": {"type": "disabled"}}, model_id
            else:
                assert extra == {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "low"},
                }, model_id
                # ``display`` is deliberately absent: the API default
                # ("omitted") keeps reasoning the user asked not to have out
                # of the transcript, which is as close to off as the row goes.
                assert "display" not in thinking, model_id

    # The split, named. The catalog decides, and it is now right about which
    # rows this is: upstream shipped ``"off": null`` on three anthropic rows,
    # and only two of them mean it. Measured against ``api.anthropic.com`` on
    # 2026-09-09 — ``claude-fable-5`` and ``claude-fable-5-1`` answer
    # *"thinking.type.disabled" is not supported for this model*, while
    # ``claude-sonnet-5`` answered normally through ``disabled``. The
    # sonnet-5 row was corrected (pinned in
    # ``tests/test_catalog_corrections_are_pinned.py``) rather than left to
    # send billed reasoning to a user who asked for none.
    assert supports_thinking_off(_row("anthropic", "claude-opus-5")) is True
    assert supports_thinking_off(_row("anthropic", "claude-opus-4-8")) is True
    assert supports_thinking_off(_row("anthropic", "claude-opus-4-7")) is True
    assert supports_thinking_off(_row("anthropic", "claude-sonnet-5")) is True
    assert supports_thinking_off(_row("anthropic", "claude-fable-5")) is False
    assert supports_thinking_off(_row("anthropic", "claude-fable-5-1")) is False


def test_a_fable_mirror_without_a_thinking_level_map_is_still_never_off() -> None:
    """The ``"off": null`` declaration is missing on every fable mirror.

    ``github-copilot/claude-fable-5``, ``opencode/claude-fable-5`` and the
    ``vercel-ai-gateway`` pair carry no ``off`` key (asserted), so the map
    alone would send them the ``disabled`` request the model 400s on.
    """

    for provider, model_id in (
        ("github-copilot", "claude-fable-5"),
        ("github-copilot", "claude-fable-5.1"),
        ("opencode", "claude-fable-5"),
        ("opencode", "claude-fable-5-1"),
        ("vercel-ai-gateway", "anthropic/claude-fable-5"),
        ("vercel-ai-gateway", "anthropic/claude-fable-5.1"),
    ):
        model = _row(provider, model_id)
        assert "off" not in (model.thinking_level_map or {}), (provider, model_id)
        assert supports_thinking_off(model) is False, (provider, model_id)
        assert resolve_anthropic_thinking(model, None, 32000)[0] == {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low"},
        }, (provider, model_id)


def test_lowest_thinking_level_reads_the_row() -> None:
    base = Model(
        api="anthropic-messages",
        id="claude-fable-5",
        provider="anthropic",
        reasoning=True,
        compat={"forceAdaptiveThinking": True},
    )
    assert lowest_thinking_level(base) == "minimal"
    # A row that declares its floor higher moves the "off" request with it.
    raised = replace(base, thinking_level_map={"minimal": None, "low": None})
    assert lowest_thinking_level(raised) == "medium"
    assert resolve_anthropic_thinking(raised, "off", 32000)[0] == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }


def test_no_adaptive_row_can_build_the_rejected_shape() -> None:
    """Catalog walk: every level, every adaptive row, no ``budget_tokens``.

    Includes ``None`` and ``"off"``, which is where beta.2's brand-new
    ``claude-fable-5-1`` was unusable: thinking on was a 400 and so was
    thinking off.
    """

    adaptive = [m for m in _anthropic_messages_rows() if supports_adaptive_thinking(m)]
    assert adaptive, "no adaptive row — the walk lost its subject"
    for model in adaptive:
        for level in [None, *EXTENDED_THINKING_LEVELS]:
            extra, _max, needs_beta = resolve_anthropic_thinking(model, level, 32000)
            thinking = extra["thinking"]
            assert thinking["type"] in ("adaptive", "disabled"), (model.id, level)
            assert "budget_tokens" not in thinking, (model.id, level)
            assert needs_beta is False, (model.id, level)
            if thinking["type"] == "disabled":
                assert supports_thinking_off(model), (model.id, level)
                assert level in (None, "off"), (model.id, level)


# === end to end through stream_anthropic ==================================


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"x-test": "1"})


class _MockStream:
    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _MockFinalMessage:
        return _MockFinalMessage()

    response = _MockResponse()


class _MockMessages:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def stream(self, **params: Any) -> _MockStream:
        self._captured.update(params)
        return _MockStream()


class _MockClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.messages = _MockMessages(captured)


async def _capture(model: Model, reasoning: str | None) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    opts = SimpleStreamOptions(
        api_key="sk-test", client=_MockClient(captured), reasoning=reasoning
    )
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    return captured


async def test_the_request_fable_5_1_actually_sends() -> None:
    """The row this release added, through the adapter that clamps first.

    ``stream_anthropic`` runs the level through ``clamp_thinking_level``
    before resolving, which turns ``"off"`` into ``"minimal"`` on this row
    (its map declares ``"off": null``). That clamp is what used to smuggle
    thinking back in as ``budget_tokens: 1024``; both spellings now build a
    request the model accepts.
    """

    model = _row("anthropic", "claude-fable-5-1")
    xhigh = await _capture(model, "xhigh")
    assert xhigh["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert xhigh["output_config"] == {"effort": "xhigh"}

    off = await _capture(model, "off")
    assert off["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert off["output_config"] == {"effort": "low"}
    assert "budget_tokens" not in off["thinking"]

    none = await _capture(model, None)
    assert none["thinking"] == {"type": "adaptive"}
    assert none["output_config"] == {"effort": "low"}
