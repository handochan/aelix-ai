"""Telnaut — a worked example of a corporate custom provider extension (#77).

Demonstrates the full "bring your own provider + login screen" pattern an
in-house team would use to add a private provider ``telnaut`` to Aelix:

1. :meth:`ExtensionAPI.register_provider` wires the provider for TURN execution —
   which wire protocol it speaks (``api``), its ``base_url``, and its catalog
   ``models`` (which now surface in ``/model`` once a credential is stored, #77
   Gap B). Here it reuses the built-in ``openai-completions`` adapter.

2. :meth:`ExtensionAPI.register_login_provider` adds ``telnaut`` to the ``/login``
   method list. When the user picks it, the ``authenticate`` handler runs a
   CUSTOM credential flow — here, "enter your employee number" (사번) — using the
   same masked dialogs the built-in flows use, and returns the credential the
   wizard stores under ``telnaut`` (the extension never touches the auth store).

The two share the id ``telnaut`` so the credential collected at ``/login``
authenticates the models registered for turns. Swap the ``authenticate`` body for
your real corporate handshake (LDAP, an internal token exchange, etc.).
"""

from __future__ import annotations

from aelix_ai.streaming import Model

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_coding_agent.login_registry import LoginContext, LoginProvider
from aelix_coding_agent.model_registry import ProviderConfigInput

_PROVIDER_ID = "telnaut"
_BASE_URL = "https://llm.telnaut.internal/v1"


async def _authenticate(ctx: LoginContext) -> str | None:
    """Custom ``/login`` flow for Telnaut: employee number → internal token.

    Returns the credential string to store under ``telnaut`` (used as the API
    key for every turn), or ``None`` if the user cancels at any prompt.
    """

    employee_no = await ctx.prompt("사번을 입력하세요 (employee number)")
    if not employee_no or not employee_no.strip():
        return None
    passcode = await ctx.prompt("사내 비밀번호 (passcode)", password=True)
    if not passcode:
        return None

    # Replace this with your real corporate auth (e.g. POST the employee number +
    # passcode to an internal SSO endpoint and return the issued bearer token).
    # For the demo we simply combine them into an opaque credential string.
    token = f"{employee_no.strip()}.{passcode}"
    ctx.notify(f"Telnaut: signed in as employee {employee_no.strip()}", kind="info")
    return token


def setup(aelix: ExtensionAPI) -> None:
    """Register the Telnaut provider (for turns) + its ``/login`` method (#77)."""

    aelix.register_provider(
        _PROVIDER_ID,
        ProviderConfigInput(
            name="Telnaut (사내)",
            # The credential the /login flow stores under `telnaut` is picked up
            # from the auth store at turn time — no api_key hardcoded here.
            models={
                "telnaut-large": Model(
                    id="telnaut-large",
                    name="Telnaut Large",
                    provider=_PROVIDER_ID,
                    api="openai-completions",
                    base_url=_BASE_URL,
                ),
            },
        ),
    )

    aelix.register_login_provider(
        LoginProvider(
            id=_PROVIDER_ID,
            name="Telnaut (사내)",
            authenticate=_authenticate,
        )
    )


__all__ = ["setup"]
