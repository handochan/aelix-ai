"""#433 — ``max`` is a thinking level of its own, after ``xhigh``, as in pi.

pi added it in ``fbdd46389`` (2026-07-09): ``EXTENDED_THINKING_LEVELS`` gained a
seventh value (``packages/ai/src/models.ts:1242`` at ``pi@fdce48eb4``), and
``getSupportedThinkingLevels`` gates ``max`` exactly like ``xhigh``: offered only
where a model's ``thinkingLevelMap`` names it (``:1250``).

MEASURED ON ``7faf4b9a`` (``.omc/probes/433-live/impl/``; a local recorder behind
the real CLI, fake key): ``--thinking max`` and ``defaultThinkingLevel: "max"``
sent ``thinking: {"type": "disabled"}`` on ``anthropic/claude-opus-4-6``,
``claude-opus-4-7`` and ``claude-sonnet-4-5``, and ``thinking: {"type":
"disabled"}`` with no effort on ``openrouter/deepseek/deepseek-v4-pro``: the
clamp treated the unknown spelling as "the model's first level", which is
``off``. Asking for the most thinking bought none.

The owner's decision of 2026-10-20 (issue comment) also reclassifies the 24 shipped
rows whose map spelled their top tier ``"xhigh": "max"`` - Claude Opus 4.6 (12),
DeepSeek V4 (8), GLM 5.2 (4). 21 of them say ``"max": "max"`` with no ``xhigh``
key, as pi did: a level saved as ``xhigh`` there (a ``defaultThinkingLevel``, a
session) clamps UP to ``max`` (the scan goes up first) and sends exactly what
``7faf4b9a`` sent. The other 3 are OpenRouter's DeepSeek V4 rows, which follow pi
(the owner's correction of 2026-10-21; ``generate-models.ts:1165-1182``, pi #4801):
``xhigh: "xhigh"`` and ``max: null``, so a saved ``xhigh`` there now sends
``xhigh`` where ``7faf4b9a`` sent ``max``. The rest of pi's ``max`` rules are
applied to the rows aelix ships too (``generate-models.ts:1122-1182``, the
map-less DeepSeek V4 rows included); the full row list is in ADR-0266's appendix.
"""

from __future__ import annotations

import json
import typing
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.models import (
    EXTENDED_THINKING_LEVELS,
    ThinkingLevel,
    clamp_thinking_level,
    get_model,
    get_supported_thinking_levels,
)
from aelix_ai.models_generated import MODELS
from aelix_ai.providers._anthropic_transforms import (
    _DEFAULT_THINKING_BUDGETS,
    adjust_max_tokens_for_thinking,
    clamp_reasoning,
    resolve_anthropic_thinking,
)
from aelix_ai.providers._google_shared import get_google_budget, get_thinking_level
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.providers.openai_completions import (
    OpenAICompletionsOptions,
    stream_simple_openai_completions,
)
from aelix_ai.providers.openai_responses import (
    OpenAIResponsesOptions,
    build_params,
)
from aelix_ai.settings import SettingsManager
from aelix_ai.settings.types import ThinkingLevel as SettingsThinkingLevel
from aelix_ai.streaming import Context, Model, SimpleStreamOptions
from aelix_coding_agent import models_json
from aelix_coding_agent.cli.args import VALID_THINKING_LEVELS, parse_args
from aelix_coding_agent.core.defaults import is_valid_thinking_level

_CATALOG = Path(__file__).resolve().parent.parent / (
    "packages/aelix-ai/src/aelix_ai/models_generated.json"
)


def _row(provider: str, model_id: str) -> Model:
    model = get_model(provider, model_id)
    assert model is not None, f"{provider}/{model_id} left the catalog"
    return model


# === 1. the scale ============================================================


def test_every_list_of_the_levels_is_pis_seven() -> None:
    """The three fixed lists and the two ``Literal`` types name the same seven.

    They are separate on purpose (each is the source of truth for its own
    validation: ``--thinking``, a ``models.json`` ``thinkingLevelMap``, the
    ``settings.json`` type), so nothing but this keeps them together.
    """

    seven = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert list(EXTENDED_THINKING_LEVELS) == seven
    assert list(VALID_THINKING_LEVELS) == seven
    assert list(models_json._THINKING_LEVELS) == seven
    assert list(typing.get_args(SettingsThinkingLevel)) == seven
    assert ["off", *typing.get_args(ThinkingLevel)] == seven
    for options in (OpenAICompletionsOptions, OpenAIResponsesOptions):
        hint = typing.get_type_hints(options)["reasoning_effort"]
        literal = next(a for a in typing.get_args(hint) if typing.get_origin(a) is typing.Literal)
        assert list(typing.get_args(literal)) == seven[1:], options.__name__


def test_max_is_accepted_wherever_a_level_is_typed() -> None:
    args = parse_args(["--thinking", "max"])
    assert args.thinking == "max"
    assert not args.diagnostics
    assert is_valid_thinking_level("max")  # ``--model id:max``, ``--models`` scopes
    loaded = SettingsManager.in_memory({"defaultThinkingLevel": "max"})
    assert loaded.get_default_thinking_level() == "max"
    # A ``models.json`` map value under ``max`` is type-checked like any level's.
    errors: list[tuple[str, str]] = []
    models_json._check_thinking_level_map({"max": 3}, "m.thinkingLevelMap", errors)
    assert errors == [("m.thinkingLevelMap.max", "Expected string | null")]


# === 2. the gate and the clamp ===============================================


def _reasoning(thinking_level_map: dict[str, Any] | None) -> Model:
    return Model(
        api="anthropic-messages",
        id="probe",
        provider="test",
        reasoning=True,
        thinking_level_map=thinking_level_map,
    )


def test_max_is_offered_only_where_the_map_names_it() -> None:
    """pi ``models.ts:1250``: ``xhigh`` and ``max`` need an explicit entry."""

    assert "max" not in get_supported_thinking_levels(_reasoning(None))
    assert "max" not in get_supported_thinking_levels(_reasoning({"xhigh": "xhigh"}))
    assert "max" not in get_supported_thinking_levels(_reasoning({"max": None}))
    assert get_supported_thinking_levels(_reasoning({"max": "max"}))[-1] == "max"
    # The gate is the explicit-entry gate, not a check of the value.
    assert "max" in get_supported_thinking_levels(_reasoning({"max": "anything"}))


@pytest.mark.parametrize(
    ("thinking_level_map", "level", "expected"),
    [
        # A reclassified row: a saved ``xhigh`` scans UP first and finds ``max``.
        ({"max": "max"}, "xhigh", "max"),
        ({"max": "max"}, "max", "max"),
        # A row with both: each is itself.
        ({"xhigh": "xhigh", "max": "max"}, "xhigh", "xhigh"),
        ({"xhigh": "xhigh", "max": "max"}, "max", "max"),
        # A row that tops out at ``xhigh``: ``max`` scans down to it.
        ({"xhigh": "xhigh"}, "max", "xhigh"),
        # No map: ``max`` comes down to ``high`` - on 7faf4b9a it was ``off``.
        (None, "max", "high"),
        (None, "xhigh", "high"),
    ],
)
def test_the_clamp_finds_the_nearest_level_up_first(
    thinking_level_map: dict[str, Any] | None, level: str, expected: str
) -> None:
    assert clamp_thinking_level(_reasoning(thinking_level_map), level) == expected


# === 3. the catalog ==========================================================

#: The owner's 24 (issue comment, 2026-10-20), measured on 7faf4b9a's catalog:
#: every row whose map said ``"xhigh": "max"``.
_RECLASSIFIED = (
    ("amazon-bedrock", "anthropic.claude-opus-4-6-v1"),
    ("amazon-bedrock", "au.anthropic.claude-opus-4-6-v1"),
    ("amazon-bedrock", "eu.anthropic.claude-opus-4-6-v1"),
    ("amazon-bedrock", "global.anthropic.claude-opus-4-6-v1"),
    ("amazon-bedrock", "us.anthropic.claude-opus-4-6-v1"),
    ("anthropic", "claude-opus-4-6"),
    ("cloudflare-ai-gateway", "claude-opus-4-6"),
    ("github-copilot", "claude-opus-4.6"),
    ("opencode", "claude-opus-4-6"),
    ("openrouter", "anthropic/claude-opus-4.6"),
    ("openrouter", "anthropic/claude-opus-4.6-fast"),
    ("vercel-ai-gateway", "anthropic/claude-opus-4.6"),
    ("deepseek", "deepseek-v4-flash"),
    ("deepseek", "deepseek-v4-pro"),
    ("opencode", "deepseek-v4-flash-free"),
    ("opencode-go", "deepseek-v4-flash"),
    ("opencode-go", "deepseek-v4-pro"),
    ("openrouter", "deepseek/deepseek-v4-flash"),
    ("openrouter", "deepseek/deepseek-v4-flash:free"),
    ("openrouter", "deepseek/deepseek-v4-pro"),
    ("fireworks", "accounts/fireworks/models/glm-5p2"),
    ("opencode-go", "glm-5.2"),
    ("zai", "glm-5.2"),
    ("zai-coding-cn", "glm-5.2"),
)


def test_no_row_spells_its_top_tier_xhigh_max_any_more() -> None:
    """A regeneration that brought one back would put ``xhigh`` on the wire as
    ``max`` under the wrong name: the picker would offer ``xhigh (max)`` and
    no ``max``."""

    spelled = [
        f"{p}/{i}"
        for p, rows in MODELS.items()
        for i, m in rows.items()
        if (m.thinking_level_map or {}).get("xhigh") == "max"
    ]
    assert spelled == []


#: pi's OpenRouter DeepSeek V4 map (``generate-models.ts:1173`` at ``fdce48eb4``):
#: OpenRouter takes its own ``xhigh`` and rejects DeepSeek's ``max`` (pi #4801).
_OPENROUTER_DEEPSEEK_V4_MAP = {
    "minimal": None,
    "low": None,
    "medium": None,
    "high": "high",
    "xhigh": "xhigh",
    "max": None,
}

#: The 3 of the 24 that follow pi instead (the owner's correction of 2026-10-21).
_OPENROUTER_DEEPSEEK_V4 = tuple(
    row for row in _RECLASSIFIED if row[0] == "openrouter" and "deepseek-v4" in row[1]
)

#: The 21 that say ``max`` and no ``xhigh``.
_RENAMED = tuple(row for row in _RECLASSIFIED if row not in _OPENROUTER_DEEPSEEK_V4)


def test_the_24_split_21_renamed_and_3_on_openrouter() -> None:
    assert len(_RECLASSIFIED) == 24
    assert len(_RENAMED) == 21
    assert _OPENROUTER_DEEPSEEK_V4 == (
        ("openrouter", "deepseek/deepseek-v4-flash"),
        ("openrouter", "deepseek/deepseek-v4-flash:free"),
        ("openrouter", "deepseek/deepseek-v4-pro"),
    )


@pytest.mark.parametrize(("provider", "model_id"), _RENAMED)
def test_21_of_the_24_rows_name_max_and_not_xhigh(provider: str, model_id: str) -> None:
    model = _row(provider, model_id)
    thinking_map = model.thinking_level_map or {}
    assert thinking_map.get("max") == "max"
    assert "xhigh" not in thinking_map
    supported = get_supported_thinking_levels(model)
    assert supported[-1] == "max" and "xhigh" not in supported
    # What 7faf4b9a sent for ``xhigh`` is what ``xhigh`` and ``max`` send now.
    assert clamp_thinking_level(model, "xhigh") == "max"


@pytest.mark.parametrize(("provider", "model_id"), _OPENROUTER_DEEPSEEK_V4)
def test_openrouter_deepseek_v4_names_xhigh_and_not_max(provider: str, model_id: str) -> None:
    """A wire change, by the owner's decision: 7faf4b9a's map said
    ``"xhigh": "max"``, so a saved ``xhigh`` sent ``reasoning_effort: "max"``; it
    sends ``"xhigh"`` now, and ``max`` is not offered (it clamps down to
    ``xhigh``)."""

    model = _row(provider, model_id)
    assert model.thinking_level_map == _OPENROUTER_DEEPSEEK_V4_MAP
    assert get_supported_thinking_levels(model) == ["off", "high", "xhigh"]
    assert clamp_thinking_level(model, "xhigh") == "xhigh"
    assert clamp_thinking_level(model, "max") == "xhigh"


#: pi's map-less DeepSeek V4 rule (``generate-models.ts:1165-1182`` at
#: ``fdce48eb4``): an ``openai-completions`` row whose id names ``deepseek-v4``
#: and that has no map gets OpenRouter's map on OpenRouter, Azure's on Azure (no
#: shipped row), the Flash map (``low: "low"``) on DeepSeek's own and opencode's
#: Flash rows, else the DeepSeek V4 map. The 15 shipped rows it reaches, all
#: map-less on 7faf4b9a (the owner's decision of 2026-10-21).
_DEEPSEEK_V4 = {"minimal": None, "low": None, "medium": None, "high": "high", "max": "max"}
_DEEPSEEK_V4_FLASH = {**_DEEPSEEK_V4, "low": "low"}
_MAPLESS_DEEPSEEK_V4 = (
    ("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-v4-flash-0731", _DEEPSEEK_V4),
    ("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-v4-pro-0813", _DEEPSEEK_V4),
    ("nvidia", "deepseek-ai/deepseek-v4-flash", _DEEPSEEK_V4),
    ("nvidia", "deepseek-ai/deepseek-v4-flash-0731", _DEEPSEEK_V4),
    ("nvidia", "deepseek-ai/deepseek-v4-pro", _DEEPSEEK_V4),
    ("nvidia", "deepseek-ai/deepseek-v4-pro-0813", _DEEPSEEK_V4),
    ("opencode", "deepseek-v4-pro", _DEEPSEEK_V4),
    ("deepseek", "deepseek-v4-flash-vision-exp", _DEEPSEEK_V4_FLASH),
    ("opencode", "deepseek-v4-flash", _DEEPSEEK_V4_FLASH),
    ("opencode", "deepseek-v4-flash-vision-exp", _DEEPSEEK_V4_FLASH),
    ("opencode-go", "deepseek-v4-flash-vision-exp", _DEEPSEEK_V4_FLASH),
    ("openrouter", "deepseek/deepseek-v4-flash-0731", _OPENROUTER_DEEPSEEK_V4_MAP),
    ("openrouter", "deepseek/deepseek-v4-flash-vision-exp", _OPENROUTER_DEEPSEEK_V4_MAP),
    ("openrouter", "deepseek/deepseek-v4-pro-0813", _OPENROUTER_DEEPSEEK_V4_MAP),
    ("openrouter", "~deepseek/deepseek-v4-flash-latest", _OPENROUTER_DEEPSEEK_V4_MAP),
)


@pytest.mark.parametrize(("provider", "model_id", "expected"), _MAPLESS_DEEPSEEK_V4)
def test_the_map_less_deepseek_v4_rows_get_pis_map(
    provider: str, model_id: str, expected: dict[str, Any]
) -> None:
    model = _row(provider, model_id)
    assert model.api == "openai-completions"
    assert model.thinking_level_map == expected
    # A saved ``xhigh`` moves UP to the row's top tier (it clamped down to
    # ``high`` on 7faf4b9a), and ``medium`` moves up to ``high``: DeepSeek V4
    # takes only ``high`` and ``max`` (the Flash map adds ``low``).
    assert clamp_thinking_level(model, "xhigh") == ("xhigh" if provider == "openrouter" else "max")
    assert clamp_thinking_level(model, "medium") == "high"


#: DeepSeek V4 rows a refresh added with a map of their own: OpenRouter's
#: ``supported_efforts`` (the two OpenRouter rows) and models.dev's effort list
#: (Cloudflare's). Decision 5's rule is for a row "that has no map" (ADR-0266),
#: so these keep the evidence they came with (#172's fold-back, review round 1).
_DERIVED_MAP_DEEPSEEK_V4 = {
    ("openrouter", "deepseek/deepseek-v4.1-flash"),
    ("openrouter", "deepseek/deepseek-v4.1-flash-1017"),
    ("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-v4.1-flash-1017"),
}
#: What they derive to: OpenRouter's ``supported_efforts`` ``[max, high, low,
#: none]`` (and models.dev's ``[none, low, high, max]`` for Cloudflare's), as
#: pi.dev's OpenRouter rows publish. OpenRouter's rule map would instead hide
#: ``low`` and ``max`` and offer an ``xhigh`` neither list names; the other
#: rule map would hide ``low`` and ``off``'s ``none``.
_DERIVED_DEEPSEEK_V4_1_MAP = {
    "off": "none",
    "minimal": None,
    "low": "low",
    "medium": None,
    "high": "high",
    "xhigh": None,
    "max": "max",
}


def test_every_deepseek_v4_completions_row_follows_pis_rule() -> None:
    """Over the whole shipped catalog, not only the rows listed above: every
    ``openai-completions`` DeepSeek V4 row on OpenRouter has OpenRouter's map,
    and every other one names ``high`` and ``max`` and no ``xhigh``. The three
    Flash rows that carried a map before #433 keep ``low: null`` (pi's Flash
    ``low`` is not a ``max`` rule; ADR-0266, Not applied). The rows a refresh
    added with a derived map are outside the rule and keep that map."""

    covered = derived = 0
    for provider, rows in MODELS.items():
        for model_id, model in rows.items():
            if model.api != "openai-completions" or "deepseek-v4" not in model_id:
                continue
            tm = model.thinking_level_map or {}
            if (provider, model_id) in _DERIVED_MAP_DEEPSEEK_V4:
                derived += 1
                assert tm == _DERIVED_DEEPSEEK_V4_1_MAP, (provider, model_id, tm)
                continue
            covered += 1
            if provider == "openrouter":
                assert tm == _OPENROUTER_DEEPSEEK_V4_MAP, (provider, model_id, tm)
            else:
                assert tm.get("high") == "high" and tm.get("max") == "max", (provider, model_id)
                assert "xhigh" not in tm, (provider, model_id, tm)
            if provider in ("opencode", "opencode-go") and "deepseek-v4.1-flash" in model_id:
                # The Flash map: models.dev lists ``low`` for these, and pi.dev's
                # rows have it; pi's substring rule misses the ``.1``.
                assert tm.get("low") == "low", (provider, model_id, tm)
    # 15 map-less on 7faf4b9a + the 8 DeepSeek V4 rows of the 24, + the 4
    # map-less V4.1 Flash rows the #172 fold-back of 2026-10-27 added.
    assert covered == 27
    assert derived == len(_DERIVED_MAP_DEEPSEEK_V4)


def test_pis_max_rules_hold_on_every_row_they_cover() -> None:
    """pi ``generate-models.ts:1122-1158`` at ``fdce48eb4``, over the shipped rows.

    The two exceptions are the ones #433 did not apply (ADR-0266): the
    ``google-vertex`` Claude ids, which ride the Gemini transport, and a
    ``gpt-5.6`` row whose map does not offer ``xhigh`` yet (pi gives those
    ``xhigh`` by another rule; ``max`` alone would turn a saved ``xhigh`` into
    ``max``).
    """

    with open(_CATALOG, encoding="utf-8") as handle:
        raw = json.load(handle)
    checked = skipped = 0
    for provider, rows in raw.items():
        for model_id, row in rows.items():
            tm = row.get("thinkingLevelMap") or {}
            expect: dict[str, Any] = {}
            if row["api"] != "google-vertex":
                if any(s in model_id for s in ("opus-4-6", "opus-4.6", "sonnet-4-6", "sonnet-4.6")):
                    expect.update(max="max")
                if any(
                    s in model_id
                    for s in (
                        "opus-4-7",
                        "opus-4.7",
                        "opus-4-8",
                        "opus-4.8",
                        "opus-5",
                        "opus.5",
                        "sonnet-5",
                        "sonnet.5",
                        "haiku-5",
                        "haiku.5",
                    )
                ):
                    expect.update(xhigh="xhigh", max="max")
                if "fable-5" in model_id:
                    expect.update(off=None, xhigh="xhigh", max="max")
            if (
                ("gpt-5.6" in model_id or "gpt-6" in model_id)
                and row["api"]
                in (
                    "openai-responses",
                    "azure-openai-responses",
                    "openai-codex-responses",
                    "openai-completions",
                )
                and tm.get("xhigh") is not None
            ):
                expect.update(max="max")
            for key, value in expect.items():
                assert key in tm and tm[key] == value, (provider, model_id, key, tm)
            checked += bool(expect)
            # The exceptions hold too: neither kind of row names ``max``.
            if row["api"] == "google-vertex" or (
                ("gpt-5.6" in model_id or "gpt-6" in model_id) and tm.get("xhigh") is None
            ):
                assert "max" not in tm, (provider, model_id, tm)
                skipped += 1
    # 105 = the 132 rows #433 changed, less the 27 DeepSeek V4 / GLM 5.2 rows no
    # Claude or OpenAI rule covers; 67 = every google-vertex row and every
    # gpt-5.6/gpt-6 row with no ``xhigh``. At least, not exactly: a refresh (#172)
    # may add rows either way.
    assert checked >= 105
    assert skipped >= 67


# === 4. what each adapter SENDS ==============================================


@dataclass
class _Final:
    stop_reason: str = "end_turn"
    content: list[Any] = field(default_factory=list)
    usage: Any = None


class _Stream:
    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._empty()

    async def _empty(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _Final:
        return _Final()


class _Messages:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    def stream(self, **params: Any) -> _Stream:
        self._captured.update(params)
        return _Stream()


class _AnthropicClient:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.messages = _Messages(captured)


async def _anthropic_sends(model: Model, reasoning: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    opts = SimpleStreamOptions(
        api_key="sk-test", client=_AnthropicClient(captured), reasoning=reasoning
    )
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    return {k: captured[k] for k in ("thinking", "output_config") if k in captured}


@pytest.mark.parametrize(
    ("model_id", "level", "effort"),
    [
        # A reclassified row: both spellings send what 7faf4b9a sent for xhigh.
        ("claude-opus-4-6", "xhigh", "max"),
        ("claude-opus-4-6", "max", "max"),
        # A 4.7+ row has both tiers natively.
        ("claude-opus-4-7", "xhigh", "xhigh"),
        ("claude-opus-4-7", "max", "max"),
        ("claude-opus-5", "max", "max"),
        ("claude-sonnet-4-6", "max", "max"),
    ],
)
async def test_anthropic_sends_the_effort_the_row_names(
    model_id: str, level: str, effort: str
) -> None:
    sent = await _anthropic_sends(_row("anthropic", model_id), level)
    assert sent == {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": effort},
    }


async def test_a_row_with_no_map_clamps_max_down_and_never_turns_thinking_off() -> None:
    """``claude-sonnet-4-5`` offers neither top level: ``max`` sends ``high``'s
    budget. On 7faf4b9a it sent ``thinking: {"type": "disabled"}``."""

    model = _row("anthropic", "claude-sonnet-4-5")
    assert "max" not in get_supported_thinking_levels(model)
    assert await _anthropic_sends(model, "max") == await _anthropic_sends(model, "high")
    assert (await _anthropic_sends(model, "max"))["thinking"]["budget_tokens"] == 16384


def test_max_on_a_budget_row_takes_the_top_budget_not_mediums() -> None:
    """No shipped budget row offers ``max``; a ``models.json`` map can. The
    budget table has no ``max`` row, so without the fold ``max`` was an
    unknown spelling and sent ``medium``'s 8192 - less than ``low`` asks."""

    assert clamp_reasoning("max") == "xhigh"
    assert clamp_reasoning("max", {**_DEFAULT_THINKING_BUDGETS, "max": 5000}) == "max"
    _, xhigh_budget = adjust_max_tokens_for_thinking(64000, 64000, "xhigh")
    _, max_budget = adjust_max_tokens_for_thinking(64000, 64000, "max")
    assert max_budget == xhigh_budget == 32768
    budget_row = replace(_row("anthropic", "claude-sonnet-4-5"), thinking_level_map={"max": "max"})
    extra, _, _ = resolve_anthropic_thinking(budget_row, "max", 64000)
    assert extra["thinking"]["budget_tokens"] == 32768


class _CompletionsRaw:
    status_code = 200
    headers: dict[str, Any] = {}


class _CompletionsWrap:
    http_response = _CompletionsRaw()

    def parse(self) -> Any:
        class _Empty:
            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                raise StopAsyncIteration

        return _Empty()


class _OpenAIClient:
    def __init__(self) -> None:
        captured: dict[str, Any] = {}
        self.captured = captured

        class _WithRaw:
            async def create(self, **kwargs: Any) -> _CompletionsWrap:
                captured.update(kwargs)
                return _CompletionsWrap()

        self.chat = type(
            "_Chat", (), {"completions": type("_C", (), {"with_raw_response": _WithRaw()})()}
        )()


async def _completions_sends(model: Model, reasoning: str) -> dict[str, Any]:
    client = _OpenAIClient()
    opts = SimpleStreamOptions(reasoning=reasoning, client=client, api_key="k")
    async for _ in stream_simple_openai_completions(model, Context(), opts):
        pass
    # The SDK takes the non-OpenAI fields (``thinking``, OpenRouter's
    # ``reasoning``) through ``extra_body``; on the wire they are top-level.
    sent = {**client.captured, **client.captured.get("extra_body", {})}
    return {k: sent[k] for k in ("thinking", "reasoning", "reasoning_effort") if k in sent}


@pytest.mark.parametrize("level", ["xhigh", "max"])
async def test_openrouter_deepseek_v4_sends_xhigh_for_both_spellings(level: str) -> None:
    """pi's OpenRouter map (pi #4801: OpenRouter answered DeepSeek's ``max`` with
    a 400). 7faf4b9a sent ``reasoning_effort: "max"`` for ``xhigh`` (its map said
    ``"xhigh": "max"``) and ``thinking: disabled`` for ``max``; round 1 of #433
    sent ``max`` for both. Both send ``xhigh`` now."""

    sent = await _completions_sends(_row("openrouter", "deepseek/deepseek-v4-pro"), level)
    assert sent == {"thinking": {"type": "enabled"}, "reasoning_effort": "xhigh"}
    # A map-less OpenRouter row in OpenRouter's own format sends the same tier.
    latest = _row("openrouter", "~deepseek/deepseek-v4-flash-latest")
    assert await _completions_sends(latest, level) == {"reasoning": {"effort": "xhigh"}}


async def test_a_map_less_deepseek_v4_row_sends_max_for_xhigh_and_high_for_medium() -> None:
    """``opencode/deepseek-v4-pro`` had no map on 7faf4b9a: ``xhigh`` clamped down
    and sent ``high``, ``medium`` sent ``medium``. pi's map: ``xhigh`` and ``max``
    send ``max``, ``medium`` comes up to ``high``."""

    model = _row("opencode", "deepseek-v4-pro")
    enabled = {"thinking": {"type": "enabled"}}
    assert await _completions_sends(model, "xhigh") == {**enabled, "reasoning_effort": "max"}
    assert await _completions_sends(model, "max") == {**enabled, "reasoning_effort": "max"}
    assert await _completions_sends(model, "medium") == {**enabled, "reasoning_effort": "high"}


async def test_openrouter_claude_rows_send_the_openrouter_effort() -> None:
    opus_46 = _row("openrouter", "anthropic/claude-opus-4.6")
    assert await _completions_sends(opus_46, "xhigh") == {"reasoning": {"effort": "max"}}
    assert await _completions_sends(opus_46, "max") == {"reasoning": {"effort": "max"}}
    opus_47 = _row("openrouter", "anthropic/claude-opus-4.7")
    assert await _completions_sends(opus_47, "xhigh") == {"reasoning": {"effort": "xhigh"}}
    assert await _completions_sends(opus_47, "max") == {"reasoning": {"effort": "max"}}


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [("openai", "gpt-5.6"), ("openai", "gpt-6-astra"), ("github-copilot", "gpt-5.6-sol")],
)
def test_the_gpt_5_6_family_sends_max_natively(provider: str, model_id: str) -> None:
    """pi ``supportsOpenAiMax`` (``generate-models.ts:600-607``): the Responses
    adapters send the level's own name, as they do for ``xhigh``."""

    model = _row(provider, model_id)
    for level in ("xhigh", "max"):
        effort: Any = clamp_thinking_level(model, level)  # what stream_simple hands on
        params = build_params(model, Context(), OpenAIResponsesOptions(reasoning_effort=effort))
        assert params["reasoning"]["effort"] == level


def test_google_reads_max_like_xhigh() -> None:
    """No Google row names ``max``; a ``models.json`` map can, and the clamp then
    passes it through. Both resolvers answer it as they answer ``xhigh``: the
    top of the ``thinkingLevel`` scale, and the API's dynamic budget on Gemini
    2.x - never a ``KeyError`` out of the sync factory."""

    assert (
        get_thinking_level("max", "gemini-3-flash-preview")
        == get_thinking_level("xhigh", "gemini-3-flash-preview")
        == "HIGH"
    )
    assert get_google_budget("gemini-2.5-pro", "max") == -1
    assert get_google_budget("gemini-2.5-pro", "max") == get_google_budget(
        "gemini-2.5-pro", "xhigh"
    )


# === 5. a refresh keeps the reclassified maps =================================


def _refresh_script() -> Any:
    """``scripts/refresh_catalog.py``, loaded as ``test_catalog_overlay.py`` does."""

    import importlib.util

    path = _CATALOG.parents[4] / "scripts/refresh_catalog.py"
    spec = importlib.util.spec_from_file_location("refresh_catalog_433", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_refresh_from_upstream_cannot_undo_the_reclassification() -> None:
    """``refresh_catalog.py`` is additive-only (it never writes a key that exists)
    and writes a ``thinkingLevelMap`` only on a row it adds, so an upstream that
    still lists every reclassified id - with a map of its own - plans nothing for
    them and leaves their maps as #433 left them. Since #172 (ADR-0264 §7 Q2) a
    map it DERIVES for a new id may name ``max``, which the build now keeps."""

    rc = _refresh_script()
    with open(_CATALOG, encoding="utf-8") as handle:
        local = json.load(handle)
    upstream: dict[str, Any] = {}
    for provider, model_id in _RECLASSIFIED:
        for upstream_id in rc.UPSTREAM_IDS.get(provider, (provider,)):
            upstream.setdefault(upstream_id, {"models": {}})["models"][model_id] = {
                "name": model_id,
                "tool_call": True,
                "reasoning": True,
                "limit": {"context": 200000, "output": 32000},
                "thinkingLevelMap": {"xhigh": "max"},
            }
    additions, _skips = rc.plan(local, upstream)
    for provider, model_id in _RECLASSIFIED:
        assert model_id not in additions.get(provider, {}), (provider, model_id)
    merged = rc.merge(local, additions)
    for provider, model_id in _RECLASSIFIED:
        assert (
            merged[provider][model_id]["thinkingLevelMap"]
            == local[provider][model_id]["thinkingLevelMap"]
        )
        assert merged[provider][model_id]["thinkingLevelMap"].get("xhigh") != "max"
