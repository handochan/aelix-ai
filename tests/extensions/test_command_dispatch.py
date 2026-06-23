"""Issue #9 — CommandDispatchService unit tests.

Pure: a fake harness exposing ``extension_runner.get_command`` +
``make_command_context`` drives the service directly. Pins the pi-faithful
semantics: name/args split, str-return shim, throw-still-handled, miss
falls-through, built-ins handled upstream (so not tested here).
"""

from __future__ import annotations

import asyncio
from typing import Any

from aelix_coding_agent.extensions.command_dispatch import (
    CommandDispatchService,
    CommandSurfaceBindings,
    DispatchOutcome,
    _split_command,
)


class _RC:
    """Stand-in for RegisteredCommand. ``handler`` is an INSTANCE attribute
    (like the real dataclass field) so it is NOT bound as a method on access."""

    def __init__(self, handler: Any, description: str) -> None:
        self.handler = handler
        self.description = description


class _Cmd:
    def __init__(self, name: str, handler: Any, description: str = "") -> None:
        self.invocation_name = name
        self.command = _RC(handler, description)


class _Runner:
    def __init__(self, cmds: list[_Cmd]) -> None:
        self._cmds = cmds

    def get_registered_commands(self) -> list[_Cmd]:
        return list(self._cmds)

    def get_command(self, name: str) -> _Cmd | None:
        for c in self._cmds:
            if c.invocation_name == name:
                return c
        return None


class _Harness:
    def __init__(self, cmds: list[_Cmd]) -> None:
        self.extension_runner = _Runner(cmds)
        self.ctx_builds: list[dict] = []

    def make_command_context(self, *, repo: Any = None, session_runtime: Any = None):
        self.ctx_builds.append({"repo": repo, "session_runtime": session_runtime})
        return object()  # handlers in these tests ignore ctx


def _service(cmds: list[_Cmd], **kw: Any) -> tuple[CommandDispatchService, _Harness]:
    harness = _Harness(cmds)
    return CommandDispatchService(lambda: harness, **kw), harness


def _bindings() -> tuple[CommandSurfaceBindings, list[str], list[str]]:
    texts: list[str] = []
    errors: list[str] = []
    return (
        CommandSurfaceBindings(emit_text=texts.append, emit_error=errors.append),
        texts,
        errors,
    )


def _run(svc: CommandDispatchService, text: str, b: CommandSurfaceBindings):
    return asyncio.run(svc.try_execute(text, b))


def test_split_command_pi_semantics() -> None:
    assert _split_command("/hello world") == ("hello", "world")
    assert _split_command("/hello") == ("hello", "")
    # The remainder after the FIRST space is raw (a second space is kept).
    assert _split_command("/hello  two") == ("hello", " two")


def test_handler_runs_with_args_and_is_handled() -> None:
    seen: list[Any] = []

    def h(args, ctx):
        seen.append((args, ctx))

    svc, harness = _service([_Cmd("hello", h)])
    b, texts, errors = _bindings()
    result = _run(svc, "/hello world", b)
    assert result.outcome is DispatchOutcome.HANDLED
    assert seen[0][0] == "world"
    assert seen[0][1] is not None  # ctx was built + passed
    assert harness.ctx_builds == [{"repo": None, "session_runtime": None}]
    assert texts == [] and errors == []


def test_str_return_is_rendered_to_surface() -> None:
    svc, _ = _service([_Cmd("hi", lambda args, ctx: "hello from ext")])
    b, texts, errors = _bindings()
    result = _run(svc, "/hi", b)
    assert result.outcome is DispatchOutcome.HANDLED
    assert texts == ["hello from ext"]
    assert errors == []


def test_none_and_blank_return_render_nothing() -> None:
    svc, _ = _service(
        [_Cmd("a", lambda args, ctx: None), _Cmd("b", lambda args, ctx: "   ")]
    )
    b, texts, _ = _bindings()
    _run(svc, "/a", b)
    _run(svc, "/b", b)
    assert texts == []


def test_async_handler_is_awaited() -> None:
    async def h(args, ctx):
        await asyncio.sleep(0)
        return "async result"

    svc, _ = _service([_Cmd("go", h)])
    b, texts, _ = _bindings()
    result = _run(svc, "/go", b)
    assert result.outcome is DispatchOutcome.HANDLED
    assert texts == ["async result"]


def test_handler_throw_is_error_and_still_handled() -> None:
    def boom(args, ctx):
        raise RuntimeError("kaboom")

    svc, _ = _service([_Cmd("boom", boom)])
    b, texts, errors = _bindings()
    result = _run(svc, "/boom", b)
    # ERROR (NOT fall-through to the model) — pi: a thrown command is handled.
    assert result.outcome is DispatchOutcome.ERROR
    assert any("kaboom" in e for e in errors)
    assert texts == []


def test_unknown_command_falls_through() -> None:
    svc, _ = _service([_Cmd("hello", lambda args, ctx: None)])
    b, _, errors = _bindings()
    result = _run(svc, "/nope", b)
    assert result.outcome is DispatchOutcome.NOT_A_COMMAND
    assert errors == []  # a miss is silent here; the caller renders "unknown"


def test_missing_make_command_context_is_clean_error() -> None:
    class _NoCtxHarness:
        extension_runner = _Runner([_Cmd("x", lambda args, ctx: None)])

    svc = CommandDispatchService(lambda: _NoCtxHarness())
    b, _, errors = _bindings()
    result = _run(svc, "/x", b)
    assert result.outcome is DispatchOutcome.ERROR
    assert any("unavailable" in e for e in errors)


def test_repo_and_session_runtime_threaded_into_context() -> None:
    sentinel_repo = object()
    sentinel_rt = object()
    svc, harness = _service(
        [_Cmd("x", lambda args, ctx: None)],
        repo=sentinel_repo,
        session_runtime=sentinel_rt,
    )
    b, _, _ = _bindings()
    _run(svc, "/x", b)
    assert harness.ctx_builds == [
        {"repo": sentinel_repo, "session_runtime": sentinel_rt}
    ]


def test_list_commands_for_autocomplete() -> None:
    svc, _ = _service(
        [_Cmd("alpha", lambda a, c: None, "first"), _Cmd("beta", lambda a, c: None, "second")]
    )
    assert svc.list_commands() == [("alpha", "first"), ("beta", "second")]


def test_no_runner_yields_not_a_command_and_empty_list() -> None:
    svc = CommandDispatchService(lambda: object())
    b, _, _ = _bindings()
    assert _run(svc, "/x", b).outcome is DispatchOutcome.NOT_A_COMMAND
    assert svc.list_commands() == []


def test_resolution_failure_is_error_not_raise() -> None:
    """A faulty registry (get_command/get_registered_commands raises) must NOT
    escape try_execute — it degrades to ERROR (review LOW-2)."""

    class _BoomRunner:
        def get_registered_commands(self):
            raise RuntimeError("registry boom")

        def get_command(self, name):
            raise RuntimeError("registry boom")

    class _H:
        extension_runner = _BoomRunner()

    svc = CommandDispatchService(lambda: _H())
    b, _, errors = _bindings()
    result = _run(svc, "/x", b)
    assert result.outcome is DispatchOutcome.ERROR
    assert any("lookup failed" in e for e in errors)
    # list_commands stays silent + empty on the same fault.
    assert svc.list_commands() == []


def test_huge_str_return_is_truncated() -> None:
    """The str-return shim caps render length (review NIT-3)."""
    svc, _ = _service([_Cmd("big", lambda a, c: "x" * 250_000)])
    b, texts, _ = _bindings()
    _run(svc, "/big", b)
    assert len(texts[0]) < 250_000
    assert texts[0].endswith("… (truncated)")
