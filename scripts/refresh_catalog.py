"""Add models that exist upstream and not here, WITHOUT touching a row we already have.

#172. ``models_generated.json`` is a corrected fork of pi's catalog, not a
mirror of anything: 41 hand edits over six commits, pinned in
``tests/test_catalog_corrections_are_pinned.py``. MEASURED, and it is why this
script is additive-only: a faithful regeneration from pi reverts 19 of those 32
corrections and drops 376 of 847 models. So this never writes a key that
already exists. Reverting a correction is then not a thing it can do — not a
rule it follows, a shape it has.

WHAT UPSTREAM CANNOT TELL US. models.dev serves model metadata: cost, limits,
modalities, ``tool_call``. It carries NO transport — there is no ``api``, no
``baseUrl``, no ``headers``, no ``compat`` anywhere in ``api.json``. Those four
fields come from pi's per-provider model files, and they are what makes a row
actually run. A row with the wrong ``api`` is worse than an absent one: it
shows up in ``/model``, the user picks it, and it fails at request time with a
transport error nobody can read.

So transport is INHERITED from a model we already ship, never invented, and
where it cannot be inherited unambiguously the model is SKIPPED and reported.
:func:`audit_transport_rule` measures that inheritance rule against the rows we
already have — leave-one-out, so the row being predicted is never its own
evidence — and ``tests/test_catalog_overlay.py`` fails if the accuracy drops.

Usage::

    python scripts/refresh_catalog.py --audit                 # score the rule
    python scripts/refresh_catalog.py --source api.json       # dry run
    python scripts/refresh_catalog.py --source api.json --apply

``--source`` takes a models.dev ``api.json`` on disk; ``--fetch`` downloads one
first. Nothing here runs in CI and nothing here runs at import: the catalog is
committed data, and the test that guards this file needs no network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

UPSTREAM_URL = "https://models.dev/api.json"

UPSTREAM_IDS: dict[str, tuple[str, ...]] = {
    "together": ("togetherai",),
    "vercel-ai-gateway": ("vercel",),
    "zai-coding-cn": ("zhipuai", "zhipuai-coding-plan"),
    "zai": ("zai", "zai-coding-plan"),
}
"""Providers whose upstream id is not the one we call them, and why.

Every entry is a MEASURED id-set overlap against the rows we already ship, and
the ones NOT here matter more than the ones that are:

- ``together`` → ``togetherai``: 17 of 17 local ids, 100%. A rename.
- ``vercel-ai-gateway`` → ``vercel``: 134 of 154, 87%; next best 45%.
- ``zai-coding-cn`` → the two ``zhipuai`` providers. Our base URL is literally
  ``open.bigmodel.cn/api/coding/paas/v4``, which is zhipuai's coding plan.
- ``zai`` → itself PLUS ``zai-coding-plan``. Our base URL is the coding-plan
  endpoint, yet three of our six rows (``glm-4.5-air``, ``glm-5.1``,
  ``glm-5v-turbo``) exist upstream only under plain ``zai`` — so pi already
  treats the two as one catalog, and the union is what it has been doing.

NOT aliased, and this is the whole reason the table is written by hand rather
than solved by overlap: ``openai-codex`` overlaps ``opencode`` 93%, and
``azure-openai-responses`` overlaps ``openai`` 79%. Those numbers are high
because resellers carry the same model NAMES, not because they are the same
service. Overlap measures the catalog, not the provider, so a threshold would
have quietly pointed our ChatGPT-subscription transport at a reseller's list.
``ant-ling``, ``fireworks`` and ``kimi-coding`` have no counterpart worth the
name (0%, 30%, 33%) and stay frozen.
"""

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "packages/aelix-ai/src/aelix_ai/models_generated.json"
)

Row = dict[str, Any]
Catalog = dict[str, dict[str, Row]]
Transport = tuple[str, str, str | None, str | None]
"""``(api, baseUrl, headers, compat)`` with the last two JSON-encoded.

Encoded because a transport has to be comparable and set-membership is how
this file asks "does the family agree with itself"; ``headers`` and ``compat``
are dicts, and two dicts that mean the same thing must not count as two
answers. ``sort_keys`` is what makes that true.
"""


def upstream_models_for(provider: str, upstream: dict[str, Any]) -> dict[str, Row]:
    """Every upstream row that describes ``provider``, under whatever id it uses.

    Merged left to right, first writer winning, so an entry listed first in
    :data:`UPSTREAM_IDS` is the authority when both describe the same model.
    """

    merged: dict[str, Row] = {}
    for upstream_id in UPSTREAM_IDS.get(provider, (provider,)):
        for model_id, row in ((upstream.get(upstream_id) or {}).get("models") or {}).items():
            merged.setdefault(model_id, row)
    return merged


# ── what we refuse to add ──────────────────────────────────────────────


def is_eligible(upstream_row: Row) -> str | None:
    """Reason to skip ``upstream_row``, or ``None`` if it may be added.

    ``tool_call`` is the one that matters. This is a coding agent: a model that
    cannot call a tool cannot edit a file, and offering it in ``/model`` sells
    the user a session that will fail on its first action. 185 of the 578
    upstream rows we lack are in that state — image models, embedders, TTS.

    The limits are a data-quality floor, not a policy: :class:`Model` requires
    ``contextWindow`` and ``maxTokens``, and a 0 there would render a context
    meter that divides by zero.
    """

    if not upstream_row.get("tool_call"):
        return "no tool_call"
    limit = upstream_row.get("limit") or {}
    if not limit.get("context"):
        return "no limit.context"
    if not limit.get("output"):
        return "no limit.output"
    return None


# ── transport, which upstream does not have ────────────────────────────


def _encode(value: Any) -> str | None:
    return None if value is None else json.dumps(value, sort_keys=True)


def transport_of(row: Row) -> Transport:
    return (
        row["api"],
        row["baseUrl"],
        _encode(row.get("headers")),
        _encode(row.get("compat")),
    )


def _family_keys(model_id: str, upstream_row: Row | None) -> list[str]:
    """Sibling-matching keys for ``model_id``, most specific first.

    models.dev's own ``family`` ("claude-opus", "gpt-codex", "qwen") is the
    first key because it is maintained by someone who tracks these releases.
    Its root ("claude", "gpt") is the second, for a genuinely new family whose
    transport its cousins already establish — ``claude-fable`` had no sibling
    when it appeared, but every ``claude-*`` on every provider we ship speaks
    ``anthropic-messages``.

    The root is deliberately NOT tried when the cousins disagree; see
    :func:`resolve_transport`. On ``github-copilot`` the ``gpt`` root spans
    ``openai-completions`` (gpt-4.1, gpt-4o) and ``openai-responses``
    (everything 5.x), which is exactly the case where a guess would be wrong
    half the time.
    """

    keys: list[str] = []
    family = (upstream_row or {}).get("family")
    if isinstance(family, str) and family:
        keys.append(family.lower())
        root = family.lower().split("-")[0]
        if root not in keys:
            keys.append(root)
    stem = model_id.rsplit("/", 1)[-1].lower()
    fallback = stem.split("-")[0]
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys


def _namespace(model_id: str) -> str:
    """The routing prefix an id carries, or ``""``.

    ``amazon-bedrock`` puts the AWS region in the model id and the region in
    the base URL: ``eu.anthropic.claude-opus-4-7`` is served from
    ``eu-central-1``, its ``us.`` twin from ``us-east-1``. MEASURED: without
    this, the family rule handed every ``eu.`` row the ``us-east-1`` endpoint —
    the one leave-one-out miss in 827 that was a real defect rather than an
    artifact of holding a row out.

    A namespace is the leading ``/``-segment, or the leading ``.``-segment when
    it has no digit in it. The digit test is what separates a route
    (``eu``, ``anthropic``, ``global``) from a version (``glm-5`` of
    ``glm-5.2``, ``gpt-4`` of ``gpt-4.1``), and it is checked against the whole
    catalog by ``test_a_namespace_is_a_route_not_a_version``.
    """

    if "/" in model_id:
        return model_id.split("/", 1)[0]
    if "." in model_id:
        head = model_id.split(".", 1)[0]
        if not any(character.isdigit() for character in head):
            return head
    return ""


def _released(model_id: str, upstream_models: dict[str, Row]) -> str:
    row = upstream_models.get(model_id) or {}
    for field in ("release_date", "last_updated"):
        value = row.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _wire(transport: Transport) -> tuple[str, str]:
    """The half of a transport that decides whether a request arrives at all.

    ``api`` and ``baseUrl`` are the wire; ``headers`` and ``compat`` are quirks.
    The split is the difference between a model that cannot work and one that
    works with a wrong detail, and MEASURED it is most of the disagreement: of
    14 leave-one-out misses, 10 were ``compat`` alone. Requiring a family to
    agree on all four fields therefore refused models over quirks — it is what
    kept ``glm-5.3`` and ``qwen3.8`` out on the first pass, both of which have
    siblings that agree perfectly about where to send the request.
    """

    return transport[0], transport[1]


def _orphans(local_models: dict[str, Row], upstream_models: dict[str, Row]) -> list[str]:
    """Local rows whose family nothing else in the provider shares.

    A model we have never seen a relative of is the evidence class for a model
    that arrives with no relative — ``kimi-k3`` and ``mai-code-1.1-flash`` on
    ``github-copilot``, where the provider itself speaks three different wires
    and family tells us nothing. What those rows have in common is not a family
    but a position: everything that is not Claude and not GPT-5 reaches Copilot
    over ``openai-completions``, and the orphans we already ship say so.
    """

    orphans = []
    for model_id in local_models:
        keys = set(_family_keys(model_id, upstream_models.get(model_id)))
        namespace = _namespace(model_id)
        relatives = [
            other
            for other in local_models
            if other != model_id
            and _namespace(other) == namespace
            and keys & set(_family_keys(other, upstream_models.get(other)))
        ]
        if not relatives:
            orphans.append(model_id)
    return orphans


def _settle(
    candidates: list[str], local_models: dict[str, Row], upstream_models: dict[str, Row]
) -> Transport | None:
    """One transport from ``candidates``, or ``None`` if they disagree on the wire.

    The wire has to be unanimous. The quirks come from the newest candidate
    that speaks that wire — newest by upstream release date, ties broken by
    catalog order, which puts hand-added rows last. Newest because ``compat``
    tracks a provider's recent models: a new ``qwen`` on ``opencode-go`` needs
    ``thinkingFormat: qwen``, which only its recent siblings carry, and a new
    ``glm`` needs the ``supportsReasoningEffort`` its predecessor gained.
    """

    if not candidates:
        return None
    wires = {_wire(transport_of(local_models[c])) for c in candidates}
    if len(wires) != 1:
        return None
    wire = next(iter(wires))
    same = [c for c in candidates if _wire(transport_of(local_models[c])) == wire]
    newest = max(
        same,
        key=lambda c: (_released(c, upstream_models), list(local_models).index(c)),
    )
    return transport_of(local_models[newest])


def resolve_transport(
    model_id: str,
    upstream_row: Row | None,
    local_models: dict[str, Row],
    upstream_models: dict[str, Row],
) -> tuple[Transport | None, str]:
    """The transport a new ``model_id`` should get, and how we got it.

    Returns ``(None, reason)`` when no answer is defensible, which happens on
    purpose: upstream carries no transport at all, so every answer here is
    inherited from something we already ship, and where nothing we ship
    resembles the model there is nothing honest to inherit.

    The ladder, first match wins:

    1. **Same family, same namespace.** The family is models.dev's own, its
       root second, an id-stem guess last.
    2. **The provider speaks one wire.** 22 of the 27 providers with an
       upstream counterpart do, and then family does not enter into it.
    3. **The orphans agree.** See :func:`_orphans` — a model with no relative
       inherits from the rows that also have none.
    """

    if not local_models:
        return None, "provider has no local rows to inherit from"

    namespace = _namespace(model_id)
    for key in _family_keys(model_id, upstream_row):
        siblings = [
            sibling_id
            for sibling_id in local_models
            if key in _family_keys(sibling_id, upstream_models.get(sibling_id))
            and _namespace(sibling_id) == namespace
        ]
        if not siblings:
            continue
        settled = _settle(siblings, local_models, upstream_models)
        if settled is None:
            # A family that disagrees about the wire is not evidence, and a
            # broader key will not fix it: the broader key contains these same
            # rows. ``minimax`` on ``opencode-go`` is genuinely split between
            # ``anthropic-messages`` and ``openai-completions``.
            return None, f"family {key!r} spans several wires"
        return settled, f"family {key!r}"

    settled = _settle(list(local_models), local_models, upstream_models)
    if settled is not None:
        return settled, "sole wire on the provider"

    settled = _settle(_orphans(local_models, upstream_models), local_models, upstream_models)
    if settled is not None:
        return settled, "the provider's other family-less models"
    return None, "no sibling family, and the provider spans several wires"


# ── the local conventions upstream disagrees with ──────────────────────


def _copilot_context(transport: Transport, local_models: dict[str, Row]) -> int | None:
    """What a Copilot row's context window is here, which is not what it is upstream.

    A GitHub Copilot seat caps the request, so the number is the seat's, not the
    model's. 759fae9 normalised every Copilot Claude row to the documented
    200000 against an inconsistent 144K/160K/1M mix; the ``openai-completions``
    rows have carried 128000 uniformly across every commit that touched them.
    Upstream serves the model's own numbers — 1000000 for claude-sonnet-5,
    1000000 for gemini-3.1-pro — and taking those would put the mix back and
    make the context meter wrong by 5x on the models people actually use.

    So a new Copilot row inherits the context of the rows that share its
    transport. Returns ``None`` if they disagree, and the caller skips.
    """

    peers = {
        row["contextWindow"]
        for row in local_models.values()
        if transport_of(row)[0] == transport[0]
    }
    if len(peers) == 1:
        return next(iter(peers))
    return max(peers) if peers else None


def build_row(
    provider: str,
    model_id: str,
    upstream_row: Row,
    transport: Transport,
    local_models: dict[str, Row],
) -> Row:
    """The catalog row for a model we do not have, in this file's own shape.

    Everything the JSON needs comes from one of two places and it is worth
    being able to see which: transport from a sibling we already ship, metadata
    from upstream. ``input`` is narrowed to the two values this catalog uses —
    ``["text", "image"]`` and ``["text"]``, every row, no third — so upstream's
    ``pdf``/``video``/``audio`` collapse rather than introducing a modality
    nothing downstream reads.
    """

    api, base_url, headers, compat = transport
    cost = upstream_row.get("cost") or {}
    limit = upstream_row.get("limit") or {}
    modalities = (upstream_row.get("modalities") or {}).get("input") or ["text"]

    row: Row = {
        "id": model_id,
        "name": upstream_row.get("name") or model_id,
        "api": api,
        "provider": provider,
        "baseUrl": base_url,
        "reasoning": bool(upstream_row.get("reasoning")),
        "input": ["text", "image"] if "image" in modalities else ["text"],
        "cost": {
            "input": cost.get("input", 0),
            "output": cost.get("output", 0),
            "cacheRead": cost.get("cache_read", 0),
            "cacheWrite": cost.get("cache_write", 0),
        },
        "contextWindow": int(limit["context"]),
        "maxTokens": int(limit["output"]),
    }
    # An output cap above the context window is not a number anything can act
    # on, and the harness divides by these. Upstream ships two such rows on its
    # own (``Inkling-Small`` at 1048576/524288); the Copilot normalisation below
    # can create a third by lowering the context under an untouched output.
    row["maxTokens"] = min(row["maxTokens"], row["contextWindow"])
    if headers is not None:
        row["headers"] = json.loads(headers)
    if compat is not None:
        row["compat"] = json.loads(compat)

    if provider == "github-copilot":
        # A seat is a subscription; usage is not billed per token. The catalog
        # has carried zero cost for every Copilot row since the provider was
        # added, and `/cost` would otherwise report money nobody spent.
        row["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        context = _copilot_context(transport, local_models)
        if context is None:
            raise ValueError(f"no Copilot context convention for {model_id}")
        row["contextWindow"] = context
        row["maxTokens"] = min(row["maxTokens"], context)
    return row


# ── planning ───────────────────────────────────────────────────────────


def plan(local: Catalog, upstream: dict[str, Any]) -> tuple[Catalog, list[tuple[str, str, str]]]:
    """``(additions, skips)`` for every provider we already ship.

    Providers we ship that upstream does not have — ``openai-codex``,
    ``azure-openai-responses``, ``fireworks``, ``together``,
    ``vercel-ai-gateway``, ``ant-ling``, ``kimi-coding``, ``zai-coding-cn`` —
    are untouched and not reported: upstream has nothing to say about them, and
    a provider we do NOT ship is not a gap this script is allowed to decide to
    fill. Adding one means adding auth, an env var, and a resolver default.
    """

    additions: Catalog = {}
    skips: list[tuple[str, str, str]] = []
    for provider, local_models in local.items():
        upstream_models = upstream_models_for(provider, upstream)
        for model_id, upstream_row in upstream_models.items():
            if model_id in local_models:
                continue
            reason = is_eligible(upstream_row)
            if reason is not None:
                skips.append((provider, model_id, reason))
                continue
            transport, why = resolve_transport(
                model_id, upstream_row, local_models, upstream_models
            )
            if transport is None:
                skips.append((provider, model_id, why))
                continue
            try:
                row = build_row(provider, model_id, upstream_row, transport, local_models)
            except ValueError as exc:
                skips.append((provider, model_id, str(exc)))
                continue
            additions.setdefault(provider, {})[model_id] = row
    return additions, skips


def merge(local: Catalog, additions: Catalog) -> Catalog:
    """``local`` with ``additions`` appended per provider — never overwriting.

    New rows land at the end of their provider, which is where every hand
    addition has landed. Order is load-bearing: ``models_generated.py`` relies
    on insertion order for ``cycle_model`` rotation, so appending keeps every
    existing model's position in that rotation.
    """

    merged: Catalog = {provider: dict(rows) for provider, rows in local.items()}
    for provider, rows in additions.items():
        for model_id, row in rows.items():
            assert model_id not in merged[provider], f"would overwrite {model_id}"
            merged[provider][model_id] = row
    return merged


# ── the rule, scored against the rows we already have ──────────────────


def _evidence_for(
    model_id: str,
    index: int,
    order: list[str],
    local_models: dict[str, Row],
    upstream_models: dict[str, Row],
    *,
    forward: bool,
) -> dict[str, Row]:
    """The rows the audit lets itself use when predicting ``model_id``.

    Always minus ``model_id``. Under ``forward``, also minus everything
    released after it — by upstream date where both rows have one, otherwise by
    catalog position, which puts hand-added rows last.
    """

    mine = _released(model_id, upstream_models)
    evidence: dict[str, Row] = {}
    for position, (candidate, row) in enumerate(local_models.items()):
        if candidate == model_id:
            continue
        if forward:
            stamp = _released(candidate, upstream_models)
            newer = stamp > mine if (stamp and mine) else position > index
            if newer:
                continue
        evidence[candidate] = row
    return evidence


def audit_transport_rule(
    local: Catalog, upstream: dict[str, Any], *, forward: bool = True
) -> tuple[int, int, list[tuple[str, str, str, str]]]:
    """Hide a row we ship and ask the rule to predict it. ``(correct, answered, misses)``.

    This is the whole reason to trust the additions. Without it, "inherit from a
    sibling" is a plausible sentence; with it, it is a number that a change to
    the ladder moves, and ``tests/test_catalog_overlay.py`` fails when it moves
    the wrong way.

    ``forward`` hides the row AND everything released after it, which is the
    question actually being asked: a model being added is new, so the evidence
    available to it is its predecessors. Plain leave-one-out instead lets a row
    be predicted from its own successors, and MEASURED that inverts the
    ``compat`` result — it asks the rule to guess that ``claude-3-haiku`` does
    NOT need the ``forceAdaptiveThinking`` that every recent Claude carries, and
    scores 89.1% by punishing it for saying the newest thing. The wire is
    unaffected either way: 5 misses in 981 under both.

    A row the rule declines is not an answer — declining is a supported
    outcome, and the caller skips the model — so ``answered`` is the
    denominator and ``misses`` names only what it got confidently wrong.
    """

    correct = answered = 0
    misses: list[tuple[str, str, str, str]] = []
    fields = ("api", "baseUrl", "headers", "compat")
    for provider, local_models in local.items():
        upstream_models = upstream_models_for(provider, upstream)
        order = list(local_models)
        for index, (model_id, row) in enumerate(local_models.items()):
            held_out = _evidence_for(
                model_id, index, order, local_models, upstream_models, forward=forward
            )
            predicted, why = resolve_transport(
                model_id, upstream_models.get(model_id), held_out, upstream_models
            )
            if predicted is None:
                continue
            answered += 1
            actual = transport_of(row)
            if predicted == actual:
                correct += 1
                continue
            # Name the field that disagreed. Reporting only ``api`` here would
            # have called ten ``compat`` differences "wrong transport" and made
            # a rule that is right about the wire look broken.
            wrong = [
                field
                for field, guess, truth in zip(fields, predicted, actual, strict=True)
                if guess != truth
            ]
            misses.append((provider, model_id, "+".join(wrong), why))
    return correct, answered, misses


# ── cli ────────────────────────────────────────────────────────────────


def _load_upstream(args: argparse.Namespace) -> dict[str, Any]:
    if args.fetch:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    if not args.source:
        raise SystemExit("--source PATH or --fetch is required")
    return json.loads(Path(args.source).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="path to a models.dev api.json")
    parser.add_argument("--fetch", action="store_true", help=f"download {UPSTREAM_URL}")
    parser.add_argument("--apply", action="store_true", help="write the catalog")
    parser.add_argument("--audit", action="store_true", help="score the transport rule")
    parser.add_argument("--show-skips", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="audit without the release-date cutoff"
    )
    args = parser.parse_args(argv)

    local: Catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    upstream = _load_upstream(args)

    if args.audit:
        correct, answered, misses = audit_transport_rule(local, upstream, forward=not args.strict)
        pct = 100.0 * correct / answered if answered else 0.0
        mode = "leave-one-out" if args.strict else "forward"
        print(f"transport rule: {correct}/{answered} = {pct:.1f}% ({mode})")
        fatal = [m for m in misses if "api" in m[2] or "baseUrl" in m[2]]
        print(f"  of {len(misses)} misses, {len(fatal)} would not reach the provider")
        for provider, model_id, wrong, why in misses:
            mark = "FATAL" if ("api" in wrong or "baseUrl" in wrong) else "quirk"
            print(f"  {mark} {provider}/{model_id}: {wrong} disagreed — {why}")
        return 0

    additions, skips = plan(local, upstream)
    total = sum(len(rows) for rows in additions.values())
    for provider, rows in sorted(additions.items(), key=lambda kv: -len(kv[1])):
        print(f"  + {provider:26} {len(rows):>4}  {', '.join(sorted(rows))[:96]}")
    print(f"{total} model(s) to add, {len(skips)} skipped")
    if args.show_skips:
        for provider, model_id, reason in sorted(skips):
            print(f"  - {provider}/{model_id}: {reason}")

    if args.apply:
        merged = merge(local, additions)
        CATALOG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
