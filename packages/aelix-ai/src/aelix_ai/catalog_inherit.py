"""ADR-0232's ladder: the transport a new model id inherits from rows already shipped.

#172, ADR-0264 §4.3 and §4.8. models.dev serves model metadata (cost, limits,
modalities, ``tool_call``) and carries NO transport: there is no ``api``, no
``baseUrl``, no ``headers`` and no ``compat`` anywhere in its ``api.json``. A
row with the wrong ``api`` is worse than an absent one, because it shows up in
``/model``, the user picks it, and it fails at the provider. So transport is
INHERITED from a model the catalog already ships, never invented, and where it
cannot be inherited unambiguously there is no answer.

This module is that rule and nothing else. It moved here from
``scripts/refresh_catalog.py`` so the generator that writes the catalog and the
client that reads a published revision run ONE implementation (ADR-0264 §4.3:
"moved out of ``scripts/refresh_catalog.py`` into aelix-ai so the generator and
the client run one implementation"). The script re-exports these functions
under their old names, and ``tests/test_catalog_overlay.py`` checks that they
are the same objects, so the two cannot drift apart.

WHAT THE LADDER READS. Of a row's upstream metadata it reads exactly three
fields: ``family`` (the new id's, and each shipped row's it compares) and the
release date (``release_date``, else ``last_updated``) of each shipped row.
The generator hands it models.dev's rows; the client hands it what a published
revision carries for each row, ``family`` and ``releaseDate``, through
:func:`ladder_inputs`. Nothing else reaches it, so nothing else a revision
carries can steer where a request goes.

``local_models`` is always ONE provider's shipped rows, in catalog order:
order breaks ties (:func:`settle`), and catalog order puts hand-added rows last.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "Row",
    "Transport",
    "encode",
    "family_keys",
    "ladder_inputs",
    "namespace",
    "orphans",
    "released",
    "resolve_transport",
    "settle",
    "transport_of",
    "wire",
]

Row = dict[str, Any]
Transport = tuple[str, str, str | None, str | None]
"""``(api, baseUrl, headers, compat)`` with the last two JSON-encoded.

Encoded because a transport has to be comparable and set-membership is how
this module asks "does the family agree with itself"; ``headers`` and
``compat`` are dicts, and two dicts that mean the same thing must not count as
two answers. ``sort_keys`` is what makes that true.
"""


def encode(value: Any) -> str | None:
    """``value`` as comparable JSON, or ``None`` when it is absent."""

    return None if value is None else json.dumps(value, sort_keys=True)


def transport_of(row: Row) -> Transport:
    """The four fields that decide whether a request arrives."""

    return (
        row["api"],
        row["baseUrl"],
        encode(row.get("headers")),
        encode(row.get("compat")),
    )


def ladder_inputs(family: str | None, release_date: str | None) -> Row:
    """A row's upstream inputs in the shape :func:`resolve_transport` reads.

    The client's half of the shared rule. A published revision carries, per
    row, ``family`` and ``releaseDate`` (the generator's ``release_date``, else
    ``last_updated``, ADR-0264 §4.2 Format); this puts them where the ladder
    looks for models.dev's own fields, so the client asks the generator's
    question with the generator's inputs.
    """

    inputs: Row = {}
    if family:
        inputs["family"] = family
    if release_date:
        inputs["release_date"] = release_date
    return inputs


def family_keys(model_id: str, upstream_row: Row | None) -> list[str]:
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


def namespace(model_id: str) -> str:
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


def released(model_id: str, upstream_models: dict[str, Row]) -> str:
    """The release date the ladder orders by: ``release_date``, else ``last_updated``."""

    row = upstream_models.get(model_id) or {}
    for field in ("release_date", "last_updated"):
        value = row.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def wire(transport: Transport) -> tuple[str, str]:
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


def orphans(local_models: dict[str, Row], upstream_models: dict[str, Row]) -> list[str]:
    """Local rows whose family nothing else in the provider shares.

    A model we have never seen a relative of is the evidence class for a model
    that arrives with no relative — ``kimi-k3`` and ``mai-code-1.1-flash`` on
    ``github-copilot``, where the provider itself speaks three different wires
    and family tells us nothing. What those rows have in common is not a family
    but a position: everything that is not Claude and not GPT-5 reaches Copilot
    over ``openai-completions``, and the orphans we already ship say so.
    """

    found = []
    for model_id in local_models:
        keys = set(family_keys(model_id, upstream_models.get(model_id)))
        route = namespace(model_id)
        relatives = [
            other
            for other in local_models
            if other != model_id
            and namespace(other) == route
            and keys & set(family_keys(other, upstream_models.get(other)))
        ]
        if not relatives:
            found.append(model_id)
    return found


def settle(
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
    wires = {wire(transport_of(local_models[c])) for c in candidates}
    if len(wires) != 1:
        return None
    only = next(iter(wires))
    same = [c for c in candidates if wire(transport_of(local_models[c])) == only]
    order = list(local_models)
    newest = max(
        same,
        key=lambda c: (released(c, upstream_models), order.index(c)),
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
    3. **The orphans agree.** See :func:`orphans` — a model with no relative
       inherits from the rows that also have none.
    """

    if not local_models:
        return None, "provider has no local rows to inherit from"

    route = namespace(model_id)
    for key in family_keys(model_id, upstream_row):
        siblings = [
            sibling_id
            for sibling_id in local_models
            if key in family_keys(sibling_id, upstream_models.get(sibling_id))
            and namespace(sibling_id) == route
        ]
        if not siblings:
            continue
        settled = settle(siblings, local_models, upstream_models)
        if settled is None:
            # A family that disagrees about the wire is not evidence, and a
            # broader key will not fix it: the broader key contains these same
            # rows. ``minimax`` on ``opencode-go`` is genuinely split between
            # ``anthropic-messages`` and ``openai-completions``.
            return None, f"family {key!r} spans several wires"
        return settled, f"family {key!r}"

    settled = settle(list(local_models), local_models, upstream_models)
    if settled is not None:
        return settled, "sole wire on the provider"

    settled = settle(orphans(local_models, upstream_models), local_models, upstream_models)
    if settled is not None:
        return settled, "the provider's other family-less models"
    return None, "no sibling family, and the provider spans several wires"
