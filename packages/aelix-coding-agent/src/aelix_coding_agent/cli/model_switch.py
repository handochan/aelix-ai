"""Switch the running harness to a ``/model <argument>`` — one path for every caller.

``/model <argument>`` (``tui/commands.py``) resolved, guarded, switched and
persisted inline; #344 extracted it here so the launch could switch to a
provider an extension registered in ``session_start`` through the very same
path. #367 retired that launch switch (pi refuses such a launch model; ADR-0249
§2.3 as amended, ADR-0250 §2.11), and with it the non-persisting variant: the
``/model`` handler is the one caller left. An explicit ``/model`` to a provider
``session_start`` registered is the user's own later choice and switches as any
other — the provider is in the registry by then.

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
) -> ModelSwitch:
    """Resolve ``argument`` as ``/model`` does, switch ``harness`` to it and persist it.

    Never raises: a resolution or switch failure comes back as a ``refusal``
    (``✖ model switch failed: …``), exactly as the ``/model`` handler reported it.
    Persistence is pi's ``setModel → setDefaultModelAndProvider``
    (``agent-session.ts:1416-1425``), best-effort, and only for a model that names
    a provider.
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
    if provider and settings_manager is not None:
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


__all__ = ["ModelSwitch", "switch_model_argument"]
