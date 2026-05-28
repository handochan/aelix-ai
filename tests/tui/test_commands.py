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
    await help_cmd.handler(ctx, "")  # Sprint 6h₁₂d — handler takes (ctx, args)

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
    # Sprint 6h₁₂d added model/clear/compact/cost/tools/mode; the P0 consumer
    # batch added thinking + export; 6h₁₄a (ADR-0121) /expand; 6h₁₄b (ADR-0122)
    # /resume; 6h₁₅ (ADR-0123) /hotkeys + /new.
    names = [c.name for c in BUILTIN_COMMANDS]
    assert names == [
        "help",
        "hotkeys",
        "model",
        "clear",
        "compact",
        "cost",
        "session",
        "name",
        "thinking",
        "tools",
        "mode",
        "settings",
        "expand",
        "export",
        "copy",
        "resume",
        "new",
        "quit",
        "exit",
        "reload",
    ]
    by_name: dict[str, Any] = {c.name: c for c in BUILTIN_COMMANDS}
    for required in (
        "help", "thinking", "expand", "export", "resume", "hotkeys", "new",
        "session", "name", "copy", "settings",
    ):
        assert by_name[required].handler is not None
    assert by_name["quit"].handler is None
    assert by_name["exit"].handler is None
    assert by_name["reload"].handler is None


# === Sprint 6h₁₂d — model / context command handlers ========================


class _FakeModel:
    def __init__(self, id: str) -> None:
        self.id = id


class _SwitchHarness:
    """Records set_model calls + exposes a current_model for /model show."""

    def __init__(self, current: str | None = "anthropic/claude-opus-4-7") -> None:
        self.current_model = _FakeModel(current) if current is not None else None
        self.set_calls: list[object] = []

    async def set_model(self, model: object) -> None:
        self.set_calls.append(model)
        self.current_model = model  # type: ignore[assignment]


class _FakeTokens:
    input = 10
    output = 5
    total = 15


class _StatsHarness:
    total_messages = 4
    cost = 0.1234
    tokens = _FakeTokens()

    def __init__(self) -> None:
        self.called = 0

    async def get_session_stats(self) -> object:
        self.called += 1
        return self


class _CompactResult:
    summary = "did the thing"
    tokens_before = 999


class _CompactHarness:
    def __init__(self) -> None:
        self.compact_args: list[str | None] = []

    async def compact(self, instructions: str | None = None) -> object:
        self.compact_args.append(instructions)
        return _CompactResult()


class _ToolView:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


class _ToolsHarness:
    def _action_get_all_tools(self) -> list[object]:
        return [_ToolView("read_file", "Read a file"), _ToolView("bash", "Run bash")]


class _ModeHarness:
    def __init__(self) -> None:
        self.steering_mode = "one-at-a-time"
        self.mode_calls: list[str] = []

    def set_steering_mode(self, mode: str) -> None:
        if mode not in ("all", "one-at-a-time"):
            raise ValueError(f"bad mode {mode!r}")
        self.mode_calls.append(mode)
        self.steering_mode = mode


def _ctx(
    harness: object,
    committed: list[object],
    *,
    chrome: object | None = None,
    set_mode: Any | None = None,
    refresh_footer: Any | None = None,
    expand_lookup: Any | None = None,
) -> CommandContext:
    return CommandContext(
        chrome=chrome if chrome is not None else _FakeChrome(),  # type: ignore[arg-type]
        harness=harness,  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/work",
        commands=list(BUILTIN_COMMANDS),
        set_mode=set_mode,
        refresh_footer=refresh_footer,
        expand_lookup=expand_lookup,
    )


def _run(cmd_name: str, ctx: CommandContext, args: str) -> None:
    import asyncio
    from collections.abc import Coroutine
    from typing import Any, cast

    command = match_command(f"/{cmd_name}", ctx.commands)
    assert command is not None and command.handler is not None
    # The handler is annotated Awaitable[None]; an async def returns a Coroutine
    # at runtime, which is what asyncio.run requires — cast to satisfy the type.
    asyncio.run(cast(Coroutine[Any, Any, None], command.handler(ctx, args)))


def test_model_no_arg_shows_current() -> None:
    committed: list[object] = []
    _run("model", _ctx(_SwitchHarness(), committed), "")
    assert any("anthropic/claude-opus-4-7" in _render(c) for c in committed)


def test_model_with_id_calls_set_model() -> None:
    harness = _SwitchHarness()
    committed: list[object] = []
    _run("model", _ctx(harness, committed), "gpt-4o")
    assert len(harness.set_calls) == 1
    assert getattr(harness.set_calls[0], "id", None) == "gpt-4o"
    assert any("gpt-4o" in _render(c) for c in committed)


def test_model_switch_refreshes_footer() -> None:
    # The footer ✱ segment is a cached string — /model must trigger a refresh
    # so the new model shows live (regression: it didn't, qa PARTIAL).
    harness = _SwitchHarness()
    committed: list[object] = []
    refreshed: list[int] = []
    _run("model", _ctx(harness, committed, refresh_footer=lambda: refreshed.append(1)), "gpt-4o")
    assert refreshed == [1]


def test_model_empty_provider_warns(monkeypatch: Any) -> None:
    # No OpenRouter env → resolve_model yields a bare empty-provider model; the
    # switch "succeeds" but turns would fail, so caution (not green success).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    harness = _SwitchHarness()
    committed: list[object] = []
    _run("model", _ctx(harness, committed), "gpt-4o")
    assert any("no provider resolved" in _render(c) for c in committed)


def test_model_show_degrades_with_no_model() -> None:
    committed: list[object] = []
    _run("model", _ctx(_SwitchHarness(current=None), committed), "")
    assert any("No model set" in _render(c) for c in committed)


def test_compact_calls_compact_and_reports() -> None:
    harness = _CompactHarness()
    committed: list[object] = []
    _run("compact", _ctx(harness, committed), "focus on tests")
    assert harness.compact_args == ["focus on tests"]
    rendered = "".join(_render(c) for c in committed)
    assert "999" in rendered
    assert "did the thing" in rendered


def test_compact_no_arg_passes_none() -> None:
    harness = _CompactHarness()
    committed: list[object] = []
    _run("compact", _ctx(harness, committed), "")
    assert harness.compact_args == [None]


def test_cost_renders_stats() -> None:
    harness = _StatsHarness()
    committed: list[object] = []
    _run("cost", _ctx(harness, committed), "")
    assert harness.called == 1
    rendered = "".join(_render(c) for c in committed)
    assert "15" in rendered  # total tokens
    assert "0.1234" in rendered  # cost
    assert "4" in rendered  # total messages


def test_clear_calls_chrome_clear() -> None:
    class _ClearChrome:
        def __init__(self) -> None:
            self.cleared = 0

        def clear(self) -> None:
            self.cleared += 1

    chrome = _ClearChrome()
    committed: list[object] = []
    _run("clear", _ctx(_SwitchHarness(), committed, chrome=chrome), "")
    assert chrome.cleared == 1


def test_clear_degrades_when_unavailable() -> None:
    committed: list[object] = []
    _run("clear", _ctx(_SwitchHarness(), committed, chrome=_FakeChrome()), "")
    assert any("unavailable" in _render(c) for c in committed)


def test_tools_lists_tools() -> None:
    harness = _ToolsHarness()
    committed: list[object] = []
    _run("tools", _ctx(harness, committed), "")
    rendered = "".join(_render(c) for c in committed)
    assert "read_file" in rendered
    assert "bash" in rendered


def test_tools_degrades_when_empty() -> None:
    class _EmptyTools:
        def _action_get_all_tools(self) -> list[object]:
            return []

    committed: list[object] = []
    _run("tools", _ctx(_EmptyTools(), committed), "")
    assert any("No tools" in _render(c) for c in committed)


def test_mode_no_arg_shows_mode() -> None:
    committed: list[object] = []
    _run("mode", _ctx(_ModeHarness(), committed), "")
    assert any("one-at-a-time" in _render(c) for c in committed)


def test_mode_with_name_sets_and_reflects_footer() -> None:
    harness = _ModeHarness()
    committed: list[object] = []
    reflected: list[str] = []
    _run("mode", _ctx(harness, committed, set_mode=reflected.append), "all")
    assert harness.mode_calls == ["all"]
    assert reflected == ["all"]  # footer reflection wired


def test_mode_invalid_degrades_not_crashes() -> None:
    harness = _ModeHarness()
    committed: list[object] = []
    _run("mode", _ctx(harness, committed), "bogus")
    assert harness.mode_calls == []  # set_steering_mode raised, contained
    assert any("failed" in _render(c) for c in committed)


def test_handlers_degrade_on_bare_fake_harness() -> None:
    # The Sprint A FakeHarness exposes only current_model — every other handler
    # must degrade with a committed message, never raise.
    for name in ("model", "compact", "cost", "tools", "mode"):
        committed: list[object] = []
        ctx = _ctx(_FakeHarness(), committed)
        if name == "model":
            _run(name, ctx, "gpt-4o")  # switch path needs set_model → degrade
        else:
            _run(name, ctx, "")
        assert committed, f"/{name} committed nothing on a bare harness"


def test_help_lists_new_commands() -> None:
    out = _render(build_help_renderable(BUILTIN_COMMANDS))
    for name in ("model", "clear", "compact", "cost", "tools", "mode"):
        assert f"/{name}" in out


# === Sprint 6h₁₄a (ADR-0121) — /expand handler =============================


def test_expand_shows_full_stored_body() -> None:
    committed: list[object] = []
    store = {3: "FULL BODY LINE\n" * 5}
    _run("expand", _ctx(_FakeHarness(), committed, expand_lookup=store.get), "3")
    assert any("FULL BODY LINE" in _render(c) for c in committed)


def test_expand_unknown_id_degrades() -> None:
    committed: list[object] = []
    _run("expand", _ctx(_FakeHarness(), committed, expand_lookup=lambda _n: None), "9")
    assert any("No expandable result #9" in _render(c) for c in committed)


def test_expand_non_numeric_arg_shows_usage() -> None:
    committed: list[object] = []
    _run("expand", _ctx(_FakeHarness(), committed, expand_lookup=lambda _n: "x"), "abc")
    assert any("Usage: /expand" in _render(c) for c in committed)


def test_expand_no_arg_shows_usage() -> None:
    committed: list[object] = []
    _run("expand", _ctx(_FakeHarness(), committed, expand_lookup=lambda _n: "x"), "")
    assert any("Usage: /expand" in _render(c) for c in committed)


def test_expand_unavailable_degrades_when_no_lookup() -> None:
    committed: list[object] = []
    _run("expand", _ctx(_FakeHarness(), committed, expand_lookup=None), "1")
    assert any("Expand is unavailable" in _render(c) for c in committed)


# === Sprint 6h₁₄b (ADR-0122) — /resume handler ============================


def test_resume_unavailable_degrades_when_no_callback() -> None:
    committed: list[object] = []
    ctx = _ctx(_FakeHarness(), committed)  # resume_session defaults to None
    _run("resume", ctx, "")
    assert any("Resume is unavailable" in _render(c) for c in committed)


def test_resume_invokes_wired_callback() -> None:
    committed: list[object] = []
    calls: list[int] = []

    async def _resume() -> None:
        calls.append(1)

    ctx = _ctx(_FakeHarness(), committed)
    ctx.resume_session = _resume
    _run("resume", ctx, "")
    assert calls == [1]


def test_resume_callback_failure_surfaces_not_crashes() -> None:
    committed: list[object] = []

    async def _boom() -> None:
        raise RuntimeError("disk gone")

    ctx = _ctx(_FakeHarness(), committed)
    ctx.resume_session = _boom
    _run("resume", ctx, "")
    assert any("resume failed" in _render(c) for c in committed)


# === Sprint 6h₁₅ (ADR-0123) — /new + /hotkeys handlers ====================


def test_new_invokes_wired_callback() -> None:
    calls: list[int] = []

    async def _new() -> None:
        calls.append(1)

    ctx = _ctx(_FakeHarness(), [])
    ctx.new_session = _new
    _run("new", ctx, "")
    assert calls == [1]


def test_new_unavailable_degrades() -> None:
    committed: list[object] = []
    _run("new", _ctx(_FakeHarness(), committed), "")  # new_session defaults None
    assert any("New session is unavailable" in _render(c) for c in committed)


def test_new_failure_surfaces_not_crashes() -> None:
    committed: list[object] = []

    async def _boom() -> None:
        raise RuntimeError("storage full")

    ctx = _ctx(_FakeHarness(), committed)
    ctx.new_session = _boom
    _run("new", ctx, "")
    assert any("new session failed" in _render(c) for c in committed)


def test_hotkeys_lists_shortcuts() -> None:
    committed: list[object] = []
    _run("hotkeys", _ctx(_FakeHarness(), committed), "")
    out = "".join(_render(c) for c in committed)
    assert "Enter" in out and "Ctrl+T" in out and "Alt+↑" in out and "Ctrl+V" in out


# === Sprint 6h₁₆ (ADR-0124) — /copy + /session + /name ====================


class _CopyChrome:
    def __init__(self) -> None:
        self.copied: str | None = None

    def copy_to_clipboard(self, text: str) -> bool:
        self.copied = text
        return True


class _MsgHarness:
    current_model = None

    def __init__(self, messages: list[object]) -> None:
        self.messages = messages


def test_copy_copies_last_assistant_message() -> None:
    from aelix_ai.messages import AssistantMessage, TextContent, UserMessage

    msgs = [
        UserMessage(content=[TextContent(text="question")]),
        AssistantMessage(content=[TextContent(text="the answer")]),
    ]
    chrome = _CopyChrome()
    committed: list[object] = []
    _run("copy", _ctx(_MsgHarness(msgs), committed, chrome=chrome), "")
    assert chrome.copied == "the answer"
    assert any("Copied" in _render(c) for c in committed)


def test_copy_nothing_to_copy_degrades() -> None:
    committed: list[object] = []
    _run("copy", _ctx(_MsgHarness([]), committed, chrome=_CopyChrome()), "")
    assert any("Nothing to copy" in _render(c) for c in committed)


class _FakeMeta2:
    id = "sess123"
    cwd = "/work"


class _FakeSession2:
    session_file = "/work/.sessions/sess123.jsonl"

    def __init__(self, name: str | None = None) -> None:
        self._name = name
        self.set_name: str | None = None

    async def get_metadata(self) -> object:
        return _FakeMeta2()

    async def get_session_name(self) -> str | None:
        return self._name

    async def append_session_name(self, name: str) -> str:
        self.set_name = name
        self._name = name
        return name


class _SessionHarness:
    current_model = None

    def __init__(self, name: str | None = None) -> None:
        self.session = _FakeSession2(name)

    async def get_session_stats(self) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(total_messages=3, cost=0.05, tokens=SimpleNamespace(total=42))


def test_session_shows_info_and_stats() -> None:
    committed: list[object] = []
    _run("session", _ctx(_SessionHarness(name="my session"), committed), "")
    out = "".join(_render(c) for c in committed)
    assert "sess123" in out and "my session" in out and "messages" in out


def test_name_shows_current() -> None:
    committed: list[object] = []
    _run("name", _ctx(_SessionHarness(name="current name"), committed), "")
    assert any("current name" in _render(c) for c in committed)


def test_name_sets_via_append() -> None:
    harness = _SessionHarness()
    committed: list[object] = []
    _run("name", _ctx(harness, committed), "new title")
    assert harness.session.set_name == "new title"
    assert any("session name → new title" in _render(c) for c in committed)


# === Sprint 6h₁₇ (ADR-0125) — /settings handler ===========================


def test_settings_unavailable_degrades() -> None:
    committed: list[object] = []
    _run("settings", _ctx(_FakeHarness(), committed), "")  # settings_action None
    assert any("Settings are unavailable" in _render(c) for c in committed)


def test_settings_invokes_wired_callback() -> None:
    calls: list[int] = []

    async def _action() -> None:
        calls.append(1)

    ctx = _ctx(_FakeHarness(), [])
    ctx.settings_action = _action
    _run("settings", ctx, "")
    assert calls == [1]


def test_settings_failure_surfaces_not_crashes() -> None:
    committed: list[object] = []

    async def _boom() -> None:
        raise RuntimeError("settings store broke")

    ctx = _ctx(_FakeHarness(), committed)
    ctx.settings_action = _boom
    _run("settings", ctx, "")
    assert any("settings failed" in _render(c) for c in committed)
