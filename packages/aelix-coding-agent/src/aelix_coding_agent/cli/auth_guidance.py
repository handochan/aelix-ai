"""Auth-guidance messages — Pi parity (honestly adapted).

Pi source: ``coding-agent/src/core/auth-guidance.ts`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` (ITEM #2). Pi's four helpers
produce the "no model / no key" guidance the CLI prints when a turn cannot
run because no usable/authenticated model is available.

Honesty adaptation (P0 #5 principle — no false claims). Pi's
``getProviderLoginHelp`` references on-disk docs::

    Use /login to log into a provider via OAuth or API key. See:
      <docs>/providers.md
      <docs>/models.md

Aelix has NO ``getDocsPath()`` / ``providers.md`` / ``models.md`` yet, so
the two doc-path lines are DROPPED (printing dead paths would be a false
claim). Pi's ``/login`` command is ALSO dropped — Aelix has no registered
``/login`` command (the BuiltinCommand set has no such entry), so claiming it
would be the same class of false claim. The shared help block is replaced with
what Aelix actually offers: the environment-variable path
(``<PROVIDER>_API_KEY``). The real ``/model`` TUI command is still surfaced by
``format_no_model_selected_message`` (Pi's verbatim ``Then use /model to
select a model.`` tail). Message shapes/wording otherwise track Pi where
truthful.
"""

from __future__ import annotations

# Pi parity: ``getProviderLoginHelp`` minus the non-existent doc paths, plus
# the env-var route Aelix genuinely supports. Kept as a module constant so the
# four formatters interpolate one consistent block (Pi's ``help`` variable).
_PROVIDER_LOGIN_HELP = (
    "Set the provider's API key in the environment "
    "(e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY)."
)


def get_provider_login_help() -> str:
    """Pi parity: ``getProviderLoginHelp`` (honestly adapted).

    Returns the shared "how to authenticate" help block referenced by the
    other three formatters. The Pi ``See: <docs>/providers.md\\n  <docs>/
    models.md`` tail is dropped (those files do not exist in Aelix), and Pi's
    ``/login`` command is dropped too (Aelix has no such command), in favor of
    the ``<PROVIDER>_API_KEY`` env route.
    """

    return _PROVIDER_LOGIN_HELP


def format_no_models_available_message() -> str:
    """Pi parity: ``formatNoModelsAvailableMessage``.

    Pi: ``No models available. {help}``.
    """

    return f"No models available. {get_provider_login_help()}"


def format_no_model_selected_message() -> str:
    """Pi parity: ``formatNoModelSelectedMessage``.

    Pi: ``No model selected.\\n\\n{help}\\n\\nThen use /model to select a
    model.`` — preserved verbatim around the (adapted) help block.
    """

    return (
        "No model selected.\n\n"
        f"{get_provider_login_help()}\n\n"
        "Then use /model to select a model."
    )


def format_no_api_key_found_message(provider: str) -> str:
    """Pi parity: ``formatNoApiKeyFoundMessage(provider)``.

    Pi: ``No API key found for {providerDisplay}.\\n\\n{help}`` where
    ``providerDisplay`` is ``"the selected model"`` when ``provider`` is
    ``"unknown"`` / empty, else the provider display name.

    The display-name lookup is the registry's job; this formatter takes the
    already-resolved ``provider`` string. An empty / ``"unknown"`` provider
    collapses to Pi's ``"the selected model"`` phrasing.
    """

    provider_display = (
        "the selected model"
        if not provider or provider == "unknown"
        else provider
    )
    return (
        f"No API key found for {provider_display}.\n\n"
        f"{get_provider_login_help()}"
    )


__all__ = [
    "format_no_api_key_found_message",
    "format_no_model_selected_message",
    "format_no_models_available_message",
    "get_provider_login_help",
]
