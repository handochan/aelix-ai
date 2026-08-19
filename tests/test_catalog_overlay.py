"""The rules ``scripts/refresh_catalog.py`` follows, and the catalog it produced.

#172. The script adds models that exist upstream and not here. What makes that
safe is not that it is careful — it is that it can only append, and that the one
thing it has to guess is scored before it guesses. This file gates both.

WHY THE GUESS EXISTS. models.dev carries no transport: there is no ``api``, no
``baseUrl``, no ``headers``, no ``compat`` anywhere in its ``api.json``. Those
four come from pi's per-provider files and they are what makes a row reach a
server. So every added row inherits its transport from a model we already ship,
and :func:`refresh_catalog.audit_transport_rule` measures that inheritance
against the rows we have — hide one, predict it, score it.

Nothing here needs the network. The catalog is committed data and these are its
properties; the audit is exercised on a synthetic pair so its logic is gated
without a 4MB fixture.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.models_generated import MODELS

_ROOT = Path(__file__).resolve().parent.parent


def _load_script() -> Any:
    """Load ``scripts/refresh_catalog.py`` — a script, not a package.

    ``scripts/`` has no ``__init__.py`` and is not on ``sys.path``; the same
    explicit spec that ``test_docs_bundle_sync.py`` uses.
    """

    path = _ROOT / "scripts/refresh_catalog.py"
    spec = importlib.util.spec_from_file_location("refresh_catalog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load_script()

CATALOG: dict[str, dict[str, dict[str, Any]]] = json.loads(
    (_ROOT / "packages/aelix-ai/src/aelix_ai/models_generated.json").read_text(encoding="utf-8")
)


def _rows() -> list[tuple[str, str, dict[str, Any]]]:
    return [(p, m, r) for p, models in CATALOG.items() for m, r in models.items()]


# ── the shape the script can only append to ────────────────────────────


def test_merge_refuses_to_overwrite_a_row_we_already_have() -> None:
    """The safety property, asserted rather than described.

    ``models_generated.json`` is a corrected fork: 41 hand edits pinned in
    ``test_catalog_corrections_are_pinned.py``, and a faithful regeneration
    from pi reverts 19 of them. This script cannot revert one because it cannot
    write a key that exists — that is a shape, not a rule it remembers.
    """

    local = {"acme": {"a": {"api": "x", "baseUrl": "u", "contextWindow": 1}}}
    with pytest.raises(AssertionError):
        rc.merge(local, {"acme": {"a": {"api": "DIFFERENT"}}})


def test_merge_appends_and_leaves_the_existing_rotation_alone() -> None:
    """Order is load-bearing: ``cycle_model`` walks insertion order.

    A new row inserted in the middle would silently move every model after it
    in the rotation a user cycles through with the model key.
    """

    local = {"acme": {"a": {"v": 1}, "b": {"v": 2}}}
    merged = rc.merge(local, {"acme": {"c": {"v": 3}}})
    assert list(merged["acme"]) == ["a", "b", "c"]
    assert list(local["acme"]) == ["a", "b"], "merge mutated the catalog it was reading"


def test_the_shipped_catalog_is_what_the_script_would_leave_behind() -> None:
    """Every provider still ends with rows, and nothing lost its identity.

    Cheap, and it catches the class of accident that a 20000-line diff hides:
    ``git diff`` reported 11209 deleted lines for a change that deleted
    nothing, because the file is mostly ``},`` and git re-anchored. Line counts
    cannot see this; the parsed rows can.
    """

    for provider, model_id, row in _rows():
        assert row["provider"] == provider, f"{provider}/{model_id} claims another provider"
        assert row["id"] == model_id, f"{provider}/{model_id} disagrees with its own key"


# ── numbers a context meter divides by ─────────────────────────────────

# Upstream's own bad data, carried since before this script existed. Both are
# harmless (``gpt-3.5-turbo-0613`` is off by exactly one token) and neither is
# ours to correct here; they are listed so a THIRD one fails the test.
_OUTPUT_ABOVE_CONTEXT = {
    ("openrouter", "nex-agi/deepseek-v3.1-nex-n1"),
    ("openrouter", "openai/gpt-3.5-turbo-0613"),
}


def test_no_new_row_promises_more_output_than_it_has_context() -> None:
    """An output cap above the context window is not a number anything can act on.

    MEASURED, and the reason the script clamps: the Copilot normalisation
    lowers a context window without touching the output cap, which gave
    ``github-copilot/kimi-k3`` a 131072 cap inside a 128000 window.
    """

    offenders = {
        (provider, model_id)
        for provider, model_id, row in _rows()
        if row["maxTokens"] > row["contextWindow"]
    }
    assert offenders == _OUTPUT_ABOVE_CONTEXT, (
        f"new incoherent limits: {sorted(offenders - _OUTPUT_ABOVE_CONTEXT)}"
    )


def test_every_row_has_limits_a_meter_can_use() -> None:
    for provider, model_id, row in _rows():
        assert row["contextWindow"] > 0, f"{provider}/{model_id} has no context window"
        assert row["maxTokens"] > 0, f"{provider}/{model_id} has no output cap"


def test_the_catalog_speaks_two_modalities_and_not_a_third() -> None:
    """``input`` is narrowed on the way in, so nothing downstream meets a new value.

    Upstream serves ``pdf``, ``video`` and ``audio``; 1427 rows here are
    ``["text", "image"]`` or ``["text"]``, and the readers of this field were
    written against exactly those two.
    """

    seen = {tuple(row["input"]) for _, _, row in _rows()}
    assert seen == {("text", "image"), ("text",)}, seen


# ── the Copilot conventions, extended to rows nobody hand-wrote ────────


def test_a_copilot_seat_is_still_never_priced_per_token() -> None:
    """Now over 36 rows rather than 27, which is the point of re-asserting it.

    Upstream prices every Copilot model it lists. The script zeroes them
    because a seat is a subscription, and this is the gate that the zeroing
    covered the models it added too.
    """

    priced = [
        model_id for model_id, row in CATALOG["github-copilot"].items() if any(row["cost"].values())
    ]
    assert not priced, f"Copilot rows acquired per-token pricing: {sorted(priced)}"
    assert len(CATALOG["github-copilot"]) >= 36


@pytest.mark.parametrize(
    ("api", "context"), [("anthropic-messages", 200000), ("openai-completions", 128000)]
)
def test_copilot_context_windows_follow_the_seat_not_the_model(api: str, context: int) -> None:
    """The cap is the seat's, so every row on one wire reports the same number.

    759fae9 normalised the Claude rows to the documented 200000 against an
    inconsistent 144K/160K/1M mix. The ``openai-completions`` rows have carried
    128000 uniformly since the provider was added. Upstream disagrees with both
    — it serves 1000000 for ``claude-sonnet-5`` and 1000000 for
    ``gemini-3.6-flash`` — so this is what the added rows had to be corrected to.
    """

    windows = {
        model_id: row["contextWindow"]
        for model_id, row in CATALOG["github-copilot"].items()
        if row["api"] == api
    }
    assert windows, f"no Copilot rows on {api} — bad scan"
    assert set(windows.values()) == {context}, (
        f"Copilot {api} windows disagree again: "
        f"{sorted({(m, w) for m, w in windows.items() if w != context})}"
    )


def test_the_models_the_owner_asked_for_are_selectable() -> None:
    """A user cannot pick a model that is not in the catalog.

    Named by the owner on 2026-08-19, each on the providers upstream actually
    serves it from. ``glm-5.3`` reaches ``zai`` only through the alias in
    :data:`refresh_catalog.UPSTREAM_IDS` — models.dev files it under
    ``zai-coding-plan``, which is the endpoint our ``zai`` base URL already
    points at.
    """

    for provider, model_id in (
        ("xai", "grok-4.6"),
        ("github-copilot", "grok-4.6"),
        ("openrouter", "x-ai/grok-4.6"),
        ("zai", "glm-5.3"),
        ("zai-coding-cn", "glm-5.3"),
        ("opencode-go", "glm-5.3"),
        ("openrouter", "z-ai/glm-5.3"),
        ("opencode-go", "qwen3.8-max"),
        ("openrouter", "qwen/qwen3.8-max"),
        ("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-v4-flash-0731"),
        ("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-v4-pro-0813"),
        ("openrouter", "deepseek/deepseek-v4-pro-0813"),
        ("moonshotai", "kimi-k3"),
        ("github-copilot", "kimi-k3"),
    ):
        assert model_id in MODELS[provider], f"{provider}/{model_id} is not selectable"
        row = MODELS[provider][model_id]
        assert row.context_window > 0 and row.max_tokens > 0
        assert row.base_url, f"{provider}/{model_id} has nowhere to send a request"


# ── the guess, and how it is scored ────────────────────────────────────


def test_a_namespace_is_a_route_not_a_version() -> None:
    """``eu.`` is a region; ``glm-5`` of ``glm-5.2`` is not.

    :func:`refresh_catalog._namespace` separates them on whether the leading
    dotted segment contains a digit, and it decides which siblings a row may
    inherit from — MEASURED, without it every ``eu.`` bedrock row was handed
    the ``us-east-1`` endpoint. This checks the rule against all 1427 ids
    rather than the handful it was designed on.
    """

    routed = [
        model_id for _, model_id, _ in _rows() if "/" not in model_id and rc._namespace(model_id)
    ]
    assert routed, "no dotted route survived the scan — the rule stopped matching"
    for _, model_id, _row in _rows():
        namespace = rc._namespace(model_id)
        if namespace and "/" not in model_id:
            assert not any(c.isdigit() for c in namespace), (
                f"{model_id}: {namespace!r} looks like a version, not a route"
            )


def test_a_family_that_disagrees_about_the_wire_gets_no_answer() -> None:
    """Declining is a supported outcome and most of the value.

    ``minimax`` on ``opencode-go`` is genuinely split between
    ``anthropic-messages`` and ``openai-completions``. Guessing there would
    produce a row that shows up in ``/model``, gets picked, and fails at
    request time — worse than the model being absent.
    """

    local = {
        "old": {"api": "anthropic-messages", "baseUrl": "https://a", "contextWindow": 1},
        "new": {"api": "openai-completions", "baseUrl": "https://b", "contextWindow": 1},
    }
    transport, why = rc.resolve_transport("minimax-m9", {"family": "minimax"}, local, {})
    assert transport is None
    assert "wire" in why


def test_quirks_come_from_the_newest_sibling_and_do_not_block_the_wire() -> None:
    """A ``compat`` difference is not a reason to refuse a model.

    MEASURED: of the leave-one-out misses, ten were ``compat`` alone. Treating
    those as ambiguity is what kept ``glm-5.3`` and ``qwen3.8`` out of the
    catalog on the first pass, though every sibling agreed about where to send
    the request.
    """

    local = {
        "glm-5.1": {
            "api": "openai-completions",
            "baseUrl": "https://z",
            "contextWindow": 1,
        },
        "glm-5.2": {
            "api": "openai-completions",
            "baseUrl": "https://z",
            "contextWindow": 1,
            "compat": {"supportsReasoningEffort": True},
        },
    }
    upstream = {
        "glm-5.1": {"family": "glm", "release_date": "2026-01-01"},
        "glm-5.2": {"family": "glm", "release_date": "2026-06-01"},
    }
    transport, _ = rc.resolve_transport(
        "glm-5.3", {"family": "glm", "release_date": "2026-08-01"}, local, upstream
    )
    assert transport is not None
    assert rc._wire(transport) == ("openai-completions", "https://z")
    assert json.loads(transport[3]) == {"supportsReasoningEffort": True}


def test_the_audit_reports_a_wrong_prediction_as_a_miss() -> None:
    """The scorer's own positive control.

    A measurement that can only return "no misses" cannot tell a clean rule
    from a broken detector, so this feeds it a catalog it must get wrong: two
    rows of one family on different wires, which no ladder can reconcile.
    """

    local = {
        "acme": {
            "m-1": {"api": "a", "baseUrl": "https://one", "contextWindow": 1, "id": "m-1"},
            "m-2": {"api": "b", "baseUrl": "https://two", "contextWindow": 1, "id": "m-2"},
        }
    }
    _, answered, misses = rc.audit_transport_rule(local, {}, forward=False)
    assert answered == 2, "the rule should have answered both, from each other"
    assert len(misses) == 2, f"the scorer saw no problem with {local}"
    assert all("api" in wrong for _, _, wrong, _ in misses)


def test_a_model_that_cannot_call_a_tool_is_not_offered() -> None:
    """This is a coding agent; a model that cannot call a tool cannot edit a file.

    185 of the 578 rows upstream has and we lack are in that state — image
    models, embedders, TTS. Offering one sells the user a session that fails on
    its first action.
    """

    assert rc.is_eligible({"tool_call": False, "limit": {"context": 1, "output": 1}})
    assert rc.is_eligible({"tool_call": True, "limit": {"context": 0, "output": 1}})
    assert rc.is_eligible({"tool_call": True, "limit": {"context": 1, "output": 0}})
    assert rc.is_eligible({"tool_call": True, "limit": {"context": 1, "output": 1}}) is None
