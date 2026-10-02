"""Switch the running harness to a ``/model <argument>`` — one path for every caller.

``/model <argument>`` (``tui/commands.py``) resolved, guarded, switched and
persisted inline. #344 (ADR-0249 §2.3) needs the SAME switch at launch: a
provider an extension registers in ``session_start`` arrives after the launch
model was chosen, so that model is an OpenRouter id (ADR-0250 guard 2) or a
placeholder no turn runs — so the launch moves the harness onto the provider exactly as the user typing
``/model <that model>`` would. Sharing the function is what keeps the two from
drifting: the same resolution (:func:`~aelix_coding_agent.core.model_argument.
resolve_model_argument` over the live registry, the launch resolver only when it
has no opinion), the same runnability refusal and the same ``set_model`` (so the
same ``model_select`` event). One difference, by design: ``/model`` persists the
choice as the default model, the launch does not (``persist=False``) — a
``--model`` launch never writes settings, and a persisted session_start provider
made the next plain launch, which has not run ``session_start`` yet when it
resolves, start on ``Model(m1, sessext, api='unknown')`` until ``/model`` (fix
round 3, R4a).

The caller renders: :class:`ModelSwitch` carries the refusal line ready to print
and the #136 caution, and the ``/model`` handler keeps every line it printed
before this was extracted.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ModelSwitch:
    """Outcome of :func:`switch_model_argument`.

    ``model`` set — the harness now runs it (and, when it names a provider, it is
    persisted as the default). ``refusal`` set — nothing switched and nothing was
    persisted; it is the red line the caller prints, verbatim. ``caution`` (#136)
    rides along with a switch whose id the catalog never saw.
    """

    model: Any | None = None
    refusal: str | None = None
    caution: str | None = None


async def switch_model_argument(
    argument: str,
    *,
    harness: Any,
    model_registry: Any,
    settings_manager: Any,
    warn: Callable[[str], None],
    persist: bool = True,
) -> ModelSwitch:
    """Resolve ``argument`` as ``/model`` does, switch ``harness`` to it and persist it.

    Never raises: a resolution or switch failure comes back as a ``refusal``
    (``✖ model switch failed: …``), exactly as the ``/model`` handler reported it.
    Persistence is pi's ``setModel → setDefaultModelAndProvider``
    (``agent-session.ts:1416-1425``), best-effort, and only for a model that names
    a provider; ``persist=False`` (the launch's late switch) skips it, and
    ``settings_manager`` is then only the ``/scoped-models`` allow-list source.
    """

    caution: str | None = None
    try:
        from aelix_coding_agent.cli.runtime_bootstrap import (
            enrich_copilot_base_url,
            resolve_route,
        )
        from aelix_coding_agent.core.model_argument import resolve_model_argument
        from aelix_coding_agent.core.runnable_models import (
            is_runnable,
            unsupported_message,
        )

        resolution = await resolve_model_argument(
            argument,
            registry=model_registry,
            current_model=getattr(harness, "current_model", None),
            settings_manager=settings_manager,
            warn=warn,
        )
        if resolution.error is not None:
            # Refuse HERE, before any switch/persist/success line: a mismatched
            # provider that is only reported at send time costs the user the turn
            # AND misattributes the failure to the provider (#134).
            return ModelSwitch(refusal=f"✖ {resolution.error}")
        if resolution.model is not None:
            # A registry hit is ALREADY the modify_models-injected copy, so it
            # carries the proxy-ep base_url enrich_copilot_base_url exists to add.
            model = resolution.model
            caution = resolution.caution
        else:
            # UNDECIDED — no usable registry (headless / RPC / test doubles), or
            # one whose introspection failed (``resolve_model_argument`` degrades
            # to this on an exception, so a real registry CAN arrive here). The
            # launch-path resolution, handed the registry it has (#344, S5: with
            # ``None`` it could not see a models.json or extension provider), and
            # its refusal (ADR-0250: pi's ambiguity / not-found) when there IS a
            # registry. With none, the resolver sees no credential and no
            # user-defined provider, so its "ambiguous … none authenticated" would
            # be a claim about nothing; the placeholder goes to the runnability
            # gate below and the caller's "no provider resolved" caution instead.
            # Adopt the registry's proxy-ep base_url for github-copilot (enterprise/
            # business host); the resolver alone returns the static individual host.
            route = resolve_route(argument, None, model_registry)
            if route.error is not None and model_registry is not None:
                return ModelSwitch(refusal=f"✖ {route.error}")
            model = enrich_copilot_base_url(route.model, model_registry)
        # WP-8 follow-up — guard an explicit id whose api has no adapter (e.g.
        # ``/model gpt-5.x`` → openai-responses): surface the actionable reason,
        # not the cryptic ``No provider registered for api=...`` the loop raises.
        if not is_runnable(model):
            return ModelSwitch(refusal=unsupported_message(model))
        await harness.set_model(model)
    except Exception as exc:  # noqa: BLE001 — surface, never kill the caller
        return ModelSwitch(refusal=f"✖ model switch failed: {exc}")
    provider = getattr(model, "provider", "")
    if persist and provider and settings_manager is not None:
        # Persist as the default (pi parity: setModel → setDefaultModelAndProvider,
        # agent-session.ts:1416-1425) so the switch SURVIVES restart / /new — the
        # same behaviour as /settings → Default model. Only when a provider is
        # resolved (a bare, providerless model is a soft-fail we never pin).
        with contextlib.suppress(Exception):
            settings_manager.set_default_model_and_provider(
                provider, getattr(model, "id", argument)
            )
            await settings_manager.flush()
    return ModelSwitch(model=model, caution=caution)


async def switch_to_late_registered_route(
    model_flag: str,
    *,
    harness: Any,
    model_registry: Any,
    settings_manager: Any,
    interactive: bool = True,
) -> tuple[str, str]:
    """Move a launch that ``late_registered_route`` flagged off OpenRouter.

    #344 / ADR-0249 §2.3, kept by ADR-0250. A provider an extension registers in
    ``session_start`` lands after the launch resolve, so the harness holds
    ``--model <provider>/<id>`` as an OpenRouter id (guard 2, an OpenRouter key
    of the user's own) or on the not-found placeholder. Every mode —
    interactive, RPC, print and json (fix round 3, R4b; print/json used to
    refuse) — now switches the harness through :func:`switch_model_argument`,
    what ``/model <that model>`` runs: the same resolution, the same refusal,
    the same ``model_select`` event. It does NOT persist the default model
    (R4a): a ``--model`` launch never writes settings.

    A prefix that now matches two late-registered providers differing only in
    case is held before any switch (F3; :func:`~aelix_coding_agent.cli.
    runtime_bootstrap.ambiguous_provider_message` is the notice).

    If that switch is refused (the provider has no credential, an allow-list
    excludes it …) or lands on OpenRouter, the harness is put on a placeholder
    ``Model(id, provider)`` whose ``api`` stays ``"unknown"``: every turn entry
    refuses it before any request (the TUI's #189 gate names ``/model``; RPC's
    turn fails at the adapter lookup), so no prompt reaches OpenRouter for that
    string either way. ``interactive=False`` (print/json, which have no
    ``/model``) words the notice as the refusal the caller exits with.

    Returns ``(notice, outcome)``: the text to print — one line, then the
    switch's allow-list warnings and its #136 caution, each on its own line, as
    ``/model`` shows them — and ``"switched"``, ``"held"`` (on the placeholder)
    or ``"failed"`` — even the placeholder could not be set (a ``model_select``
    handler raised), so the caller refuses the launch.
    """

    from aelix_ai.streaming import Model

    from aelix_coding_agent.cli.runtime_bootstrap import (
        ambiguous_provider_message,
        resolve_model,
    )

    route = resolve_model(model_flag, None, model_registry)
    ambiguous = ambiguous_provider_message(model_flag, model_registry)
    if ambiguous is not None:
        # Codex second pass on ``ebfe411a`` (F3): ``session_start`` registered
        # two providers the prefix matches only up to case. Held without asking
        # ``/model``, whose credential-filtered pool could hold just one of them
        # and switch to it — the launch refuses a guess in every mode, as the
        # print gate already did (RPC sent the prompt to OpenRouter).
        try:
            await harness.set_model(Model(id=route.id, provider=route.provider))
        except Exception:  # noqa: BLE001 — the caller refuses the launch instead
            return ambiguous, "failed"
        if not interactive:
            return f"{ambiguous} No prompt was sent.", "held"
        return f"{ambiguous} No prompt will be sent for it; run /model to select a model.", "held"
    warnings: list[str] = []
    switched = await switch_model_argument(
        model_flag,
        harness=harness,
        model_registry=model_registry,
        settings_manager=settings_manager,
        warn=warnings.append,
        persist=False,
    )
    landed = switched.model
    landed_provider = getattr(landed, "provider", "") if landed is not None else ""
    if landed_provider and landed_provider != "openrouter":
        lines = [
            f"switched to {landed_provider}/{getattr(landed, 'id', '')} as /model "
            f"{model_flag} would (not saved as the default model): provider "
            f"'{landed_provider}' was registered in a session_start handler, after "
            "the launch model was chosen.",
            *warnings,
        ]
        if switched.caution is not None:
            # #136 — what ``/model`` prints after "⚠ switched to …".
            lines.append(f"⚠ {switched.caution}")
        return "\n".join(lines), "switched"
    reason = (switched.refusal or "it resolved to OpenRouter").removeprefix("✖ ")
    head = (
        f"--model {model_flag} names provider '{route.provider}', registered in a "
        f"session_start handler after the launch model was chosen, and switching to "
        f"it failed ({reason})."
    )
    try:
        await harness.set_model(Model(id=route.id, provider=route.provider))
    except Exception:  # noqa: BLE001 — the caller refuses the launch instead
        return head, "failed"
    if not interactive:
        return f"{head} No prompt was sent.", "held"
    return (
        f"{head} No prompt will be sent to OpenRouter for it; run /model to select a model.",
        "held",
    )


__all__ = ["ModelSwitch", "switch_model_argument", "switch_to_late_registered_route"]
