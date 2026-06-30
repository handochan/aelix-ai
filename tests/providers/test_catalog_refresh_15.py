"""Model-catalog refresh (#15) — presence + metadata closure.

Faithful port of Pi's current ``packages/ai/src/providers/*.models.ts``,
``env-api-keys.ts``, ``types.ts``, and ``model-resolver.ts``:

- GLM-5.2 across Z.AI / OpenRouter / Fireworks / OpenCode-Go (xhigh
  effort + correct endpoints).
- OpenRouter ``Fusion`` alias.
- Kimi K2.7 Code on the ``kimi-coding`` provider.
- New OpenAI-compatible providers + env wiring: NVIDIA NIM, Ant Ling,
  Z.AI Coding Plan China (``zai-coding-cn``).

Values are pulled verbatim from Pi — no invented context windows /
pricing / effort levels.

Pi audit anchor: every value asserted here was independently re-fetched and
diffed (contextWindow / maxTokens / cost / baseUrl / thinkingLevelMap key-sets)
against pi ``packages/ai/src/providers/*.models.ts`` at commit
``f2e9d75388fe17325ebe31372e5287b4acdb67a3``. Confirmed faithful, including the
genuine per-provider thinkingLevelMap asymmetry (pi's fireworks ``glm-5p2``
omits the ``high`` key while pi's zai ``glm-5.2`` omits ``off``) and the NVIDIA
NIM free-tier entries that pi genuinely prices at 0/0 — both pinned below so a
future transcription error fails the test rather than silently matching a
copy-pasted catalog value.
"""

from __future__ import annotations

import typing

import pytest
from aelix_ai.models_generated import MODELS
from aelix_ai.providers._env_api_keys import ENV_API_KEYS, get_env_api_key
from aelix_ai.streaming import KnownProvider
from aelix_coding_agent.core.model_resolver import DEFAULT_MODEL_PER_PROVIDER

# ── GLM-5.2 across four providers ──────────────────────────────────


def test_zai_glm_5_2_present_with_xhigh_effort() -> None:
    m = MODELS["zai"]["glm-5.2"]
    assert m.name == "GLM-5.2"
    assert m.api == "openai-completions"
    assert m.base_url == "https://api.z.ai/api/coding/paas/v4"
    assert m.context_window == 1_000_000
    assert m.max_tokens == 131072
    assert m.reasoning is True
    # xhigh effort maps to the Z.AI "max" thinking budget.
    assert m.thinking_level_map is not None
    assert m.thinking_level_map["xhigh"] == "max"
    assert m.compat is not None and m.compat["supportsReasoningEffort"] is True


def test_openrouter_glm_5_2_present_with_xhigh_effort() -> None:
    m = MODELS["openrouter"]["z-ai/glm-5.2"]
    assert m.name == "Z.ai: GLM 5.2"
    assert m.base_url == "https://openrouter.ai/api/v1"
    assert m.context_window == 1048576
    assert m.max_tokens == 32768
    assert m.cost.input == 0.95
    assert m.cost.output == 3.0
    assert m.cost.cache_read == 0.18
    assert m.thinking_level_map == {"xhigh": "xhigh"}


def test_fireworks_glm_5_2_present() -> None:
    m = MODELS["fireworks"]["accounts/fireworks/models/glm-5p2"]
    assert m.name == "GLM 5.2"
    assert m.base_url == "https://api.fireworks.ai/inference/v1"
    assert m.context_window == 1048576
    assert m.cost.input == 1.4
    assert m.cost.output == 4.4
    assert m.cost.cache_read == 0.26
    assert m.thinking_level_map is not None
    assert m.thinking_level_map["xhigh"] == "max"


def test_opencode_go_glm_5_2_present() -> None:
    m = MODELS["opencode-go"]["glm-5.2"]
    assert m.name == "GLM-5.2"
    assert m.base_url == "https://opencode.ai/zen/go/v1"
    assert m.context_window == 1_000_000
    assert m.cost.input == 1.4
    assert m.cost.output == 4.4
    assert m.thinking_level_map is not None
    assert m.thinking_level_map["xhigh"] == "max"
    assert m.compat is not None and m.compat["maxTokensField"] == "max_tokens"


# ── OpenRouter Fusion alias ────────────────────────────────────────


def test_openrouter_fusion_alias_present() -> None:
    m = MODELS["openrouter"]["openrouter/fusion"]
    assert m.name == "OpenRouter: Fusion"
    assert m.api == "openai-completions"
    assert m.base_url == "https://openrouter.ai/api/v1"
    assert m.context_window == 1_000_000
    assert m.max_tokens == 30000
    assert m.reasoning is True
    assert m.compat is not None and m.compat["thinkingFormat"] == "openrouter"


# ── Kimi K2.7 ──────────────────────────────────────────────────────


def test_kimi_k2_7_code_present() -> None:
    m = MODELS["kimi-coding"]["k2p7"]
    assert m.name == "Kimi K2.7 Code"
    assert m.api == "anthropic-messages"
    assert m.provider == "kimi-coding"
    assert m.base_url == "https://api.kimi.com/coding"
    assert m.headers == {"User-Agent": "KimiCLI/1.5"}
    assert m.context_window == 262144
    assert m.max_tokens == 32768
    assert "image" in m.input


# ── New providers: presence ────────────────────────────────────────


@pytest.mark.parametrize(
    ("provider", "expected_count"),
    [("ant-ling", 3), ("nvidia", 19), ("zai-coding-cn", 6)],
)
def test_new_provider_present(provider: str, expected_count: int) -> None:
    assert provider in MODELS
    assert len(MODELS[provider]) == expected_count
    for mid, model in MODELS[provider].items():
        assert model.provider == provider
        assert model.id == mid


def test_ant_ling_ring_metadata() -> None:
    m = MODELS["ant-ling"]["Ring-2.6-1T"]
    assert m.name == "Ring 2.6 1T"
    assert m.api == "openai-completions"
    assert m.base_url == "https://api.ant-ling.com/v1"
    assert m.reasoning is True
    assert m.context_window == 262144
    assert m.cost.input == 0.06
    assert m.cost.output == 0.25
    assert m.thinking_level_map is not None
    assert m.thinking_level_map["xhigh"] == "xhigh"


def test_nvidia_nemotron_super_metadata() -> None:
    m = MODELS["nvidia"]["nvidia/nemotron-3-super-120b-a12b"]
    assert m.name == "Nemotron 3 Super"
    assert m.api == "openai-completions"
    assert m.base_url == "https://integrate.api.nvidia.com/v1"
    assert m.headers == {"NVCF-POLL-SECONDS": "3600"}
    assert m.context_window == 262144
    assert m.cost.input == 0.2
    assert m.cost.output == 0.8


def test_zai_coding_cn_glm_5_2_metadata() -> None:
    m = MODELS["zai-coding-cn"]["glm-5.2"]
    assert m.name == "GLM-5.2"
    assert m.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert m.context_window == 1_000_000
    assert m.thinking_level_map is not None
    assert m.thinking_level_map["xhigh"] == "max"


# ── KnownProvider + env-key wiring + defaults ──────────────────────


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("ant-ling", "ANT_LING_API_KEY"),
        ("nvidia", "NVIDIA_API_KEY"),
        ("zai-coding-cn", "ZAI_CODING_CN_API_KEY"),
    ],
)
def test_new_provider_env_wiring(
    provider: str, env_var: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Listed in KnownProvider Literal.
    assert provider in typing.get_args(KnownProvider)
    # Env-key map carries the Pi env var name.
    assert ENV_API_KEYS[provider] == [env_var]
    # Resolution returns the configured value.
    monkeypatch.setenv(env_var, f"key-for-{provider}")
    assert get_env_api_key(provider) == f"key-for-{provider}"


@pytest.mark.parametrize(
    ("provider", "default_id"),
    [
        ("ant-ling", "Ring-2.6-1T"),
        ("nvidia", "nvidia/nemotron-3-super-120b-a12b"),
        ("zai-coding-cn", "glm-5.1"),
    ],
)
def test_new_provider_default_model_resolvable(
    provider: str, default_id: str
) -> None:
    assert DEFAULT_MODEL_PER_PROVIDER[provider] == default_id
    # The Pi default id is an actual catalog entry for that provider.
    assert default_id in MODELS[provider]


# ── Pi-audit pins: thinkingLevelMap key-set asymmetry + NVIDIA free tier ──
#
# These lock the exact pi values that a self-referential presence check could
# miss. Audited against pi @ f2e9d75388fe17325ebe31372e5287b4acdb67a3.


def test_glm_5_2_thinking_level_map_key_set_asymmetry() -> None:
    """pi's fireworks ``glm-5p2`` omits ``high``; pi's zai ``glm-5.2`` omits
    ``off`` — this is real pi data, not a transcription slip. Both maps keep
    ``minimal: null`` (an explicitly-unmapped level), preserved verbatim.
    """

    fireworks = MODELS["fireworks"]["accounts/fireworks/models/glm-5p2"]
    zai = MODELS["zai"]["glm-5.2"]
    assert fireworks.thinking_level_map == {
        "off": "none",
        "minimal": None,
        "low": "high",
        "medium": "high",
        "xhigh": "max",
    }
    assert zai.thinking_level_map == {
        "minimal": None,
        "low": "high",
        "medium": "high",
        "high": "high",
        "xhigh": "max",
    }
    # The flagged asymmetry, asserted directly.
    assert "high" not in fireworks.thinking_level_map
    assert "off" not in zai.thinking_level_map


@pytest.mark.parametrize(
    "model_id",
    [
        "meta/llama-3.1-70b-instruct",
        "meta/llama-3.1-8b-instruct",
        "meta/llama-3.2-11b-vision-instruct",
        "meta/llama-3.2-90b-vision-instruct",
        "meta/llama-3.3-70b-instruct",
        "mistralai/mistral-large-3-675b-instruct-2512",
        "mistralai/mistral-small-4-119b-2603",
        "moonshotai/kimi-k2.6",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/nvidia-nemotron-nano-9b-v2",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.5-122b-a10b",
        "stepfun-ai/step-3.5-flash",
        "stepfun-ai/step-3.7-flash",
    ],
)
def test_nvidia_free_tier_models_priced_zero(model_id: str) -> None:
    """pi genuinely prices these NVIDIA NIM entries at 0/0/0/0 — verbatim, not
    a missing-cost placeholder. The two paid NVIDIA models are pinned below."""

    cost = MODELS["nvidia"][model_id].cost
    assert (cost.input, cost.output, cost.cache_read, cost.cache_write) == (
        0,
        0,
        0,
        0,
    )


def test_nvidia_paid_tier_models_keep_pi_pricing() -> None:
    super_ = MODELS["nvidia"]["nvidia/nemotron-3-super-120b-a12b"].cost
    ultra = MODELS["nvidia"]["nvidia/nemotron-3-ultra-550b-a55b"].cost
    assert (super_.input, super_.output) == (0.2, 0.8)
    assert (ultra.input, ultra.output, ultra.cache_read) == (0.5, 2.5, 0.15)
