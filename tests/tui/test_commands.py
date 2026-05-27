"""Sprint 6h₁₂a (ADR-0110) — built-in command core unit tests.

Pure: no Application/TTY. The registry + ``match_command`` + ``build_help_renderable``
are pure; ``/help`` dispatch is exercised through a fake ``CommandContext`` whose
``commit`` records the committed renderable. The startup banner content is asserted
by rendering ``_build_banner`` to plain text.
"""

from __future__ import annotations

import io
from typing import Any

from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    BuiltinCommand,
    CommandContext,
    build_help_renderable,
    match_command,
)
from rich.console import Console


def _render(renderable: object) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=80, no_color=True).print(renderable)
    return buffer.getvalue()


# === match_command (PURE) ===================================================


def test_match_command_resolves_help() -> None:
    cmd = match_command("/help", BUILTIN_COMMANDS)
    assert cmd is not None
    assert cmd.name == "help"
    assert cmd.handler is not None


def test_match_command_resolves_with_trailing_args() -> None:
    # "/help me" → the leading word "help".
    cmd = match_command("/help me", BUILTIN_COMMANDS)
    assert cmd is not None
    assert cmd.name == "help"


def test_match_command_unknown_returns_none() -> None:
    assert match_command("/nope", BUILTIN_COMMANDS) is None


def test_match_command_non_slash_returns_none() -> None:
    assert match_command("help", BUILTIN_COMMANDS) is None
    assert match_command("do /help later", BUILTIN_COMMANDS) is None


def test_match_command_bare_slash_returns_none() -> None:
    assert match_command("/", BUILTIN_COMMANDS) is None
    assert match_command("/ ", BUILTIN_COMMANDS) is None


def test_match_command_is_case_sensitive() -> None:
    assert match_command("/HELP", BUILTIN_COMMANDS) is None


def test_match_command_metadata_only_entries_resolve() -> None:
    # quit/exit/reload are handler=None metadata entries (parse_input_line owns
    # their behavior); match_command still resolves them for completeness.
    for name in ("quit", "exit", "reload"):
        cmd = match_command(f"/{name}", BUILTIN_COMMANDS)
        assert cmd is not None
        assert cmd.name == name
        assert cmd.handler is None


# === build_help_renderable ==================================================


def test_help_renderable_lists_every_command() -> None:
    out = _render(build_help_renderable(BUILTIN_COMMANDS))
    for command in BUILTIN_COMMANDS:
        assert f"/{command.name}" in out
        assert command.description in out


# === /help dispatch (fake ctx) ==============================================


class _FakeChrome:
    pass


class _FakeHarness:
    current_model = None


async def test_help_handler_commits_table() -> None:
    committed: list[object] = []
    commands = list(BUILTIN_COMMANDS)
    ctx = CommandContext(
        chrome=_FakeChrome(),  # type: ignore[arg-type]
        harness=_FakeHarness(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/work",
        commands=commands,
    )
    help_cmd = match_command("/help", commands)
    assert help_cmd is not None and help_cmd.handler is not None
    await help_cmd.handler(ctx)

    assert len(committed) == 1
    rendered = _render(committed[0])
    assert "/help" in rendered
    assert "/quit" in rendered  # the table lists the whole registry


# === startup banner =========================================================


class _Model:
    id = "anthropic/claude-opus-4-7"


class _BannerHarness:
    current_model = _Model()


def test_banner_contains_model_id_cwd_and_help_hint() -> None:
    from aelix_coding_agent.tui.shell import _build_banner

    out = _render(_build_banner(_BannerHarness(), "/home/me/project"))  # type: ignore[arg-type]
    assert "anthropic/claude-opus-4-7" in out
    assert "/home/me/project" in out
    assert "/help" in out
    assert "Aelix" in out


def test_banner_degrades_when_no_model() -> None:
    class _NoModel:
        current_model = None

    from aelix_coding_agent.tui.shell import _build_banner

    out = _render(_build_banner(_NoModel(), "/work"))  # type: ignore[arg-type]
    assert "unknown" in out
    assert "/work" in out


# === registry shape =========================================================


def test_builtin_command_is_frozen() -> None:
    cmd = BuiltinCommand("x", "desc")
    try:
        cmd.name = "y"  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:  # pragma: no cover - frozen dataclass must raise
        raise AssertionError("BuiltinCommand must be frozen")


def test_sprint_a_registry_set() -> None:
    names = [c.name for c in BUILTIN_COMMANDS]
    assert names == ["help", "quit", "exit", "reload"]
    by_name: dict[str, Any] = {c.name: c for c in BUILTIN_COMMANDS}
    assert by_name["help"].handler is not None
    assert by_name["quit"].handler is None
    assert by_name["exit"].handler is None
    assert by_name["reload"].handler is None
