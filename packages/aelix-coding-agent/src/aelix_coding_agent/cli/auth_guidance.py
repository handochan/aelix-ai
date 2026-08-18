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

REACHABILITY HAS A SECOND READER: a delegated subagent, which is headless AND
does not own its own command line. ``format_no_model_selected_message`` closes
by telling that reader where a child's model actually comes from, because the
two flag/slash remedies above are ones only its parent can apply.
"""

from __future__ import annotations

# Pi parity: ``getProviderLoginHelp`` minus the non-existent doc paths, plus
# the env-var route Aelix genuinely supports. Kept as a module constant so the
# four formatters interpolate one consistent block (Pi's ``help`` variable).
_PROVIDER_LOGIN_HELP = (
    "Set the provider's API key in the environment "
    "(e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY)."
)

# #111 — the OTHER route, and the one that works for a user who has no
# credential to export yet. Word-for-word the sentence ``cli/list_models.py``
# already prints, so the two headless surfaces do not describe the same product
# differently. "run ``aelix``" is what makes a TUI-only command actionable from
# a headless message; without that clause naming ``/login`` really would be a
# dead end, which is the objection this constant answers rather than dodges.
# Kept as a WHOLE SENTENCE appended after the block rather than spliced into
# it: ``get_provider_login_help()`` is a public formatter that three callers and
# their tests treat as one indivisible unit, and shaving its final period to
# make a comma fit would break "the shared block appears verbatim" — the exact
# property that keeps the three messages consistent.
_INTERACTIVE_LOGIN_ROUTE = (
    "Or run 'aelix' and use the /login command inside the TUI."
)


def get_provider_login_help() -> str:
    """Pi parity: ``getProviderLoginHelp`` (honestly adapted).

    Returns the shared "how to authenticate" help block referenced by the
    other three formatters. The Pi ``See: <docs>/providers.md\\n  <docs>/
    models.md`` tail is dropped (those files do not exist in Aelix), in favor
    of the ``<PROVIDER>_API_KEY`` env route.

    ``/login`` is deliberately absent FROM THIS BLOCK — but not from the
    messages that interpolate it. It is TUI-only (``tui/commands.py``) and
    every caller prints from a headless ``--print`` / ``--mode json`` run, so
    dropping the bare command name into a shared "how to authenticate" block
    would tell a headless reader to type something they cannot type.

    CORRECTED (#111). The earlier version of this docstring stopped there and
    concluded ``/login`` had no place in these messages at all. It did not
    follow: ``cli/list_models.py`` had already solved the same problem on the
    same surface by saying "run ``aelix`` and use ``/login``", which is
    actionable from any shell. The consequence of the stricter reading was
    measured — ``aelix -p`` with zero credentials named only ``/model``, which
    cannot help someone with nothing to select, and ``--provider X`` with no
    key named no interactive route at all. Both now carry
    :data:`_INTERACTIVE_LOGIN_ROUTE`.
    """

    return _PROVIDER_LOGIN_HELP


def format_no_models_available_message() -> str:
    """Pi parity: ``formatNoModelsAvailableMessage``.

    Pi: ``No models available. {help}``.
    """

    return (
        f"No models available. {get_provider_login_help()} "
        f"{_INTERACTIVE_LOGIN_ROUTE}"
    )


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

    A DELEGATED SUBAGENT IS THE OTHER HEADLESS READER, and neither of the first
    two remedies is one it can act on: its command line is built by its parent,
    and it has no TUI to type into. The third sentence is for it, and both
    halves of it are true — ``/model`` persists via
    ``set_default_model_and_provider``, so it rescues a later child as a stored
    default, and a profile declaring neither ``model:`` nor ``provider:``
    inherits whatever the parent is using (``agents/resolver.py``).
    """

    return (
        "No model selected.\n\n"
        f"{get_provider_login_help()}\n\n"
        # #111 — ``/login`` LEADS the interactive half. This message is reached
        # when nothing resolved a provider at all, and the previous tail named
        # only ``/model``: a picker over the models your credentials unlock,
        # which is empty for exactly the reader who got this message. ``/model``
        # stays because it is right for someone who has credentials and simply
        # did not pass ``--model``.
        "Then pass --model <id> on the command line, or run 'aelix' and use "
        "/login to sign in — or /model, if you already have credentials and "
        "just need to pick one (both persist the choice).\n\n"
        "A delegated subagent takes its model from its profile's "
        "model:/provider:, and inherits the parent's when the profile declares "
        "neither — so give the PARENT a model, or name one in the profile."
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
        # #111 — this variant used to end at the env-var block, so it offered
        # no interactive route whatsoever: not ``/login``, not ``/model``, not
        # even "run aelix". A user who names a provider they have not signed
        # in to is precisely the one who needs ``/login``.
        f"{get_provider_login_help()} {_INTERACTIVE_LOGIN_ROUTE}"
    )


__all__ = [
    "format_no_api_key_found_message",
    "format_no_model_selected_message",
    "format_no_models_available_message",
    "get_provider_login_help",
]
