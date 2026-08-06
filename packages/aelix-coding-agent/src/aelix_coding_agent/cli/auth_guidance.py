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
claim). The shared help block is replaced with what Aelix actually offers on
the path these messages print from: the environment-variable route
(``<PROVIDER>_API_KEY``).

WHY THE HELP BLOCK STILL DOES NOT MENTION ``/login`` — and it is NOT the
reason this docstring used to give. The old rationale was "Aelix has no
registered ``/login`` command"; that stopped being true when ``/login``
shipped (``tui/commands.py``: ``BuiltinCommand("login", ...)``, alongside
``BuiltinCommand("model", ...)``). The real reason is REACHABILITY. Every
production caller of these formatters is ``cli/entry.py`` inside
``if app_mode in ("print", "json")`` — a HEADLESS run with no TUI to type a
slash command into. A slash command is the correct advice only when the reader
can get to a prompt; from ``aelix --print`` it is the same dead end ``aelix
auth`` was (#111 B-1). ``cli/list_models.py`` does name ``/login``, correctly:
it tells the reader to *start* ``aelix`` first.

For the same reason ``format_no_model_selected_message`` no longer ends with
Pi's verbatim ``Then use /model to select a model.`` — ``/model`` is TUI-only,
so a headless reader could not act on it. The tail now leads with the
``--model`` flag, which works from exactly where the message is printed, and
mentions ``/model`` only as the interactive alternative.
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
    models.md`` tail is dropped (those files do not exist in Aelix), in favor
    of the ``<PROVIDER>_API_KEY`` env route.

    ``/login`` is deliberately absent: it exists (``tui/commands.py``) but is
    TUI-only, and every caller of this block prints from a headless
    ``--print`` / ``--mode json`` run where no prompt is available to type it
    at. See the module docstring.
    """

    return _PROVIDER_LOGIN_HELP


def format_no_models_available_message() -> str:
    """Pi parity: ``formatNoModelsAvailableMessage``.

    Pi: ``No models available. {help}``.
    """

    return f"No models available. {get_provider_login_help()}"


def format_no_model_selected_message() -> str:
    """Pi parity: ``formatNoModelSelectedMessage`` (tail honestly adapted).

    Pi: ``No model selected.\\n\\n{help}\\n\\nThen use /model to select a
    model.`` The shape is preserved; the tail is not verbatim.

    #111 B-1 — ``cli/entry.py`` prints this ONLY under
    ``if app_mode in ("print", "json")``, i.e. a headless run. ``/model`` is a
    TUI ``BuiltinCommand`` with no headless equivalent, so Pi's verbatim tail
    told a headless reader to type something they cannot type — the same dead
    end as the ``aelix auth`` string this batch removed from
    ``cli/list_models.py``. ``--model`` is a real flag on the very invocation
    that just failed, so it leads.
    """

    return (
        "No model selected.\n\n"
        f"{get_provider_login_help()}\n\n"
        "Then pass --model <id> on the command line "
        "(or run 'aelix' and use /model inside the TUI)."
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
