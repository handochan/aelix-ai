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
    assert "run 'aelix' and use /model inside the TUI" in msg


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
