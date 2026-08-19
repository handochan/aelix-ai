"""The catalog values a maintainer corrected BY HAND, pinned so a refresh cannot revert them.

``models_generated.json`` is not a snapshot of upstream. It is a **corrected
fork** of one: 41 field edits over six hand-written commits, each with a sourced
rationale in its commit body — and nowhere else. The JSON has no comments, there
is no ADR, and until this file existed only ONE of those corrections had a test.

MEASURED, and it is the whole reason this file exists: reverting all fourteen
``openai-codex`` context windows to the stale ``272000`` that commit ``f422ae9``
explicitly corrected leaves the full suite GREEN — 9026 passed. The commit
message says "stale 272K → real per-model values"; nothing checked.

So a refresh from upstream — by hand or by a future generator — would silently
put the wrong numbers back, and the only symptom would be a context meter that
is wrong by 2-4x on the models people actually code with.

WHAT BELONGS HERE. A value this repo chose that DIFFERS from what upstream says,
where getting it wrong is user-visible. Not a mirror of the catalog: adding a
model does not belong here, and neither does a value nobody deliberated.
"""

from __future__ import annotations

import pytest
from aelix_ai.models_generated import MODELS
from aelix_ai.streaming import Model


def _row(provider: str, model_id: str) -> Model:
    assert provider in MODELS, f"provider {provider!r} vanished from the catalog"
    assert model_id in MODELS[provider], (
        f"{provider}/{model_id} vanished from the catalog — if that was "
        "deliberate, delete its pin here in the same commit"
    )
    return MODELS[provider][model_id]


# === f422ae9 — "correct openai-codex context windows" ======================
#
# The catalog carried a flat contextWindow 272000 for every codex model. The
# commit corrected them against the canonical source and verified end-to-end
# that the harness context meter then reported 400K / 1.05M.
_CODEX_CONTEXT = {
    "gpt-5.1": 400000,
    "gpt-5.1-codex-max": 400000,
    "gpt-5.1-codex-mini": 400000,
    "gpt-5.2": 400000,
    "gpt-5.2-codex": 400000,
    "gpt-5.3-codex": 400000,
    "gpt-5.4": 1050000,
    "gpt-5.4-mini": 400000,
    "gpt-5.5": 1050000,
}


@pytest.mark.parametrize(("model_id", "expected"), sorted(_CODEX_CONTEXT.items()))
def test_openai_codex_context_windows_stay_corrected(
    model_id: str, expected: int
) -> None:
    assert _row("openai-codex", model_id).context_window == expected, (
        f"openai-codex/{model_id} is back to a stale context window. This value "
        "was corrected by hand in f422ae9 against the canonical source; a "
        "refresh must not overwrite it."
    )


def test_the_codex_spark_output_cap_is_not_mirroring_its_context() -> None:
    """759fae9: ``maxTokens`` had been copied from ``contextWindow``."""

    assert _row("openai-codex", "gpt-5.3-codex-spark").max_tokens == 32000


# === 23b98e4 — the canonical providers, not the mirrors ====================
#
# The commit's own reasoning: take the number from the canonical provider, "NOT
# the azure/copilot mirrors, which lag and would regress e.g. azure gpt-5
# 400K->272K".
@pytest.mark.parametrize(
    ("provider", "model_id", "expected"),
    [
        ("openai", "gpt-5-chat-latest", 400000),
        ("openai", "gpt-5.4", 1050000),
        ("openai", "gpt-5.5", 1050000),
        ("azure-openai-responses", "gpt-5.4", 1050000),
        ("azure-openai-responses", "gpt-5.5", 1050000),
        ("anthropic", "claude-sonnet-4-5", 1000000),
    ],
)
def test_canonical_context_windows_stay_corrected(
    provider: str, model_id: str, expected: int
) -> None:
    assert _row(provider, model_id).context_window == expected


# === 759fae9 — the Copilot Claude normalisation ============================


def test_every_copilot_claude_model_reports_the_documented_cap() -> None:
    """One number, uniformly, because Copilot's cap is the seat's not the model's.

    The commit replaced an inconsistent 144K/160K/1M mix with the documented
    200K. Asserted over ALL of them rather than a sample: the defect it fixed
    was precisely that they disagreed with each other, so a per-model pin would
    let the next one drift in unnoticed.
    """

    claude = {
        model_id: row.context_window
        for model_id, row in MODELS["github-copilot"].items()
        if "claude" in model_id
    }
    assert len(claude) >= 9, f"only {len(claude)} Copilot Claude models — bad scan"
    assert set(claude.values()) == {200000}, (
        f"Copilot Claude context windows disagree again: {sorted(set(claude.values()))}. "
        "Upstream serves 1000000 for several of these; 200000 is the documented "
        "Copilot cap and was normalised deliberately in 759fae9."
    )


def test_a_copilot_seat_is_never_priced_per_token() -> None:
    """The correction NO commit message mentions, and the easiest to lose.

    A GitHub Copilot seat is a subscription; usage is not billed per token. The
    catalog has carried zero costs for every Copilot model since the provider
    was added — 26 rows, invariant across every commit that touched the file.
    Upstream prices them (models.dev gives claude-opus-4.8 5.0/25.0), so a
    refresh would invent charges the user does not pay and `/cost` would report
    money that was never spent.
    """

    priced = {
        model_id: row.cost
        for model_id, row in MODELS["github-copilot"].items()
        if any(
            getattr(row.cost, field, 0)
            for field in ("input", "output", "cache_read", "cache_write")
        )
    }
    assert not priced, f"Copilot models acquired per-token pricing: {sorted(priced)}"


# === the flagships added by hand, and why their numbers are what they are ==


def test_the_current_flagships_are_selectable() -> None:
    """A user cannot pick a model that is not in the catalog.

    These were added by hand from the canonical source because the catalog has
    no refresh pipeline yet (#172). The point of pinning them is not the
    numbers — it is that a future regeneration which DROPS them fails here
    instead of silently shrinking what a user can select.
    """

    for provider, model_id in (
        ("anthropic", "claude-opus-5"),
        ("github-copilot", "claude-opus-5"),
        ("google", "gemini-3.6-flash"),
        ("google", "gemini-3.7-flash"),
    ):
        row = _row(provider, model_id)
        assert row.provider == provider
        assert row.context_window > 0
        assert row.max_tokens > 0


def test_the_copilot_flagship_follows_the_copilot_conventions_not_upstream() -> None:
    """The row most likely to be "corrected" back to upstream by someone helpful.

    models.dev serves ``claude-opus-5`` on github-copilot with contextWindow
    1000000 and real per-token pricing. Both are wrong for this product: the
    Copilot cap is 200000 (759fae9) and a seat is not metered.
    """

    row = _row("github-copilot", "claude-opus-5")
    assert row.context_window == 200000
    assert (row.cost.input, row.cost.output) == (0, 0)
    assert (row.cost.cache_read, row.cost.cache_write) == (0, 0)
    # It inherits the sibling's transport, which is what makes it actually run.
    sibling = _row("github-copilot", "claude-opus-4.8")
    assert row.api == sibling.api
    assert row.base_url == sibling.base_url
    assert row.headers == sibling.headers


def test_the_anthropic_flagship_keeps_its_full_context() -> None:
    """The direct-API row, where the Copilot cap does NOT apply.

    Same model id on two providers with two different correct answers is
    exactly the kind of thing a bulk refresh flattens.
    """

    assert _row("anthropic", "claude-opus-5").context_window == 1000000
    assert _row("anthropic", "claude-opus-5").cost.input > 0
