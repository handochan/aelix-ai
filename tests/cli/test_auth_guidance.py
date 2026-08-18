"""ITEM #2 — ``cli/auth_guidance.py`` formatter tests.

Pi source: ``coding-agent/src/core/auth-guidance.ts`` (SHA 734e08e). These
pin the four formatters' shapes AND the honesty adaptation: no dead doc paths,
the real ``<PROVIDER>_API_KEY`` env route, and — #111 B-1 — no instruction that
the reader cannot act on from where the message is printed. Every production
caller is ``cli/entry.py`` under ``if app_mode in ("print", "json")``, so a
TUI-only slash command is not a usable instruction here; ``/login`` stays out of
the help block and ``--model`` leads the no-model-selected tail.
"""

from __future__ import annotations

from aelix_coding_agent.cli.auth_guidance import (
    format_no_api_key_found_message,
    format_no_model_selected_message,
    format_no_models_available_message,
    get_provider_login_help,
)


def test_login_help_is_honest() -> None:
    """The shared help block references the REAL surfaces and drops Pi's
    non-existent doc paths (P0 #5 honesty principle)."""

    help_text = get_provider_login_help()
    # ``/login`` IS registered (tui/commands.py) — but it is TUI-only, and this
    # block is only ever printed on the headless --print / --mode json path,
    # where there is no prompt to type it at. Naming it would be the same dead
    # end as the removed ``aelix auth`` string (#111 B-1).
    assert "/login" not in help_text
    assert "_API_KEY" in help_text  # the env-var route Aelix actually supports
    # Pi's ``<docs>/providers.md`` / ``<docs>/models.md`` MUST NOT appear —
    # those files do not exist in Aelix (printing them would be a false claim).
    assert "providers.md" not in help_text
    assert "models.md" not in help_text
    assert "See:" not in help_text


def test_no_models_available_shape() -> None:
    """Pi: ``No models available. {help}``."""

    msg = format_no_models_available_message()
    assert msg.startswith("No models available. ")
    assert get_provider_login_help() in msg


def test_no_model_selected_shape() -> None:
    """Pi's ``No model selected.\\n\\n{help}\\n\\n<tail>`` shape, with a tail the
    reader can actually act on.

    #111 B-1 — this message is printed ONLY from the headless ``print``/``json``
    dispatch in ``cli/entry.py``. Pi's verbatim ``Then use /model to select a
    model.`` names a TUI-only command, so it was an instruction a headless
    reader could not follow. ``--model`` works on the invocation that just
    failed and therefore leads; ``/model`` survives as the explicitly
    interactive alternative.
    """

    msg = format_no_model_selected_message()
    assert msg.startswith("No model selected.\n\n")
    assert get_provider_login_help() in msg
    assert "--model" in msg
    # The bare TUI-only imperative must not come back.
    assert not msg.endswith("Then use /model to select a model.")
    assert "run 'aelix'" in msg
    # #111 — and ``/login`` must LEAD the interactive half. This assertion used
    # to require only ``/model``, which is a picker over the models your
    # credentials unlock: empty for the reader who got "No model selected"
    # because they have no credentials at all. Both are named now, in that
    # order.
    assert "/login" in msg
    assert "/model" in msg
    assert msg.index("/login") < msg.index("/model inside the TUI") if "/model inside the TUI" in msg else True
    assert msg.index("/login") < msg.index("or /model")


def test_no_model_selected_tells_a_subagent_where_its_model_comes_from() -> None:
    """The other headless reader, and the only one for whom BOTH leading
    remedies are unreachable.

    A delegated child does not own its command line (the parent builds it) and
    has no TUI, so ``--model`` and ``/model`` are things only its parent can do.
    The closing sentence has to name the two places a child's model can actually
    come from — its profile, or the parent it inherits from.
    """

    msg = format_no_model_selected_message()
    assert "delegated subagent" in msg
    assert "model:/provider:" in msg
    assert "inherits the parent's" in msg


def test_no_api_key_found_named_provider() -> None:
    """A concrete provider display name is interpolated verbatim."""

    msg = format_no_api_key_found_message("Anthropic")
    assert msg.startswith("No API key found for Anthropic.\n\n")
    assert get_provider_login_help() in msg


def test_no_api_key_found_unknown_collapses_to_selected_model() -> None:
    """Pi: an empty / ``"unknown"`` provider collapses to the phrase
    ``"the selected model"``."""

    for provider in ("", "unknown"):
        msg = format_no_api_key_found_message(provider)
        assert "No API key found for the selected model." in msg


# === #111 — every headless dead end now names a way out =====================


def test_no_api_key_found_names_an_interactive_route() -> None:
    """The variant the roadmap missed, and the worse of the two.

    ``aelix -p --provider anthropic --model X`` with no key printed the env-var
    block and stopped: no ``/login``, no ``/model``, not even "run aelix". A
    reader who names a provider they have not signed in to is exactly who needs
    ``/login``, and this was the one message that never mentioned it.
    """

    msg = format_no_api_key_found_message("Anthropic")
    assert msg.startswith("No API key found for Anthropic.")
    assert "run 'aelix'" in msg
    assert "/login" in msg


def test_no_models_available_matches_the_sentence_list_models_already_prints() -> None:
    """One product, one sentence.

    ``cli/list_models.py`` solved this first (#111 B-1) and its wording is the
    precedent: naming a TUI-only command is fine from a headless message as
    long as "run ``aelix``" comes with it. Asserting the two agree keeps a
    future edit to one of them from silently forking the advice.
    """

    msg = format_no_models_available_message()
    assert "run 'aelix' and use the /login command inside the TUI" in msg
    # ...and the shared env-var block must still appear WHOLE, which is what
    # stops a future edit from paraphrasing it per-callsite.
    assert get_provider_login_help() in msg


def test_every_headless_formatter_offers_a_route_a_reader_can_take() -> None:
    """The general property, so a NEW formatter cannot reintroduce the dead end.

    Each of these is printed to a user with a shell and no credentials. The
    minimum useful content is: how to supply a key without a TUI (env var) AND
    how to get one interactively (run aelix, /login).
    """

    for msg in (
        format_no_models_available_message(),
        format_no_model_selected_message(),
        format_no_api_key_found_message("Anthropic"),
    ):
        assert "API key in the environment" in msg, msg
        assert "run 'aelix'" in msg, msg
        assert "/login" in msg, msg
