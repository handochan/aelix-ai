"""Sprint 6h₁₀a (ADR-0104) — run_tui headless smoke tests.

Drives the real ``run_tui`` loop with a prompt-toolkit pipe input + DummyOutput
and a fake harness/runtime that records the lifecycle calls. No real terminal,
no real agent, no sleeps.
"""

from __future__ import annotations

import io
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.input import build_prompt_session
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console


class _FakeHooks:
    async def emit(self, event: object) -> None:  # handle_user_bash calls this
        return None


class FakeHarness:
    def __init__(self) -> None:
        self.bootstrapped = 0
        self.prompts: list[tuple[str, str]] = []
        self.reloads = 0
        self.aborts = 0
        self.subscribers: list[object] = []
        self.unsubscribed = 0
        self.hooks = _FakeHooks()
        self.session = None

    async def bootstrap(self) -> None:
        self.bootstrapped += 1

    def subscribe(self, listener: object):
        self.subscribers.append(listener)

        def _unsub() -> None:
            self.unsubscribed += 1

        return _unsub

    async def prompt(self, text: str, *, source: str = "interactive", images=None):
        self.prompts.append((text, source))
        return []

    async def reload_resources(self) -> None:
        self.reloads += 1

    async def abort(self) -> None:
        self.aborts += 1


class FakeRuntime:
    def __init__(self, harness: FakeHarness) -> None:
        self._harness = harness
        self.rebind_cb = None
        self.disposed = 0

    @property
    def harness(self) -> FakeHarness:
        return self._harness

    def set_rebind_session(self, cb) -> None:
        self.rebind_cb = cb

    async def dispose(self) -> None:
        self.disposed += 1


@contextmanager
def _session(lines: str) -> Generator[PromptSession[str]]:
    """Yield a prompt session fed by a pipe carrying ``lines``.

    Wrapped in ``create_app_session`` so prompt-toolkit's global app/input
    state is isolated per test — without it, the input-fd registration leaks
    across pytest-asyncio's per-function event loops and a later
    ``prompt_async`` blocks forever.
    """

    with create_pipe_input() as pipe:
        pipe.send_text(lines)
        with create_app_session(input=pipe, output=DummyOutput()):
            yield build_prompt_session(pt_input=pipe, pt_output=DummyOutput())


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=True, width=80)


async def test_run_tui_drives_prompt_and_quits() -> None:
    harness = FakeHarness()
    runtime = FakeRuntime(harness)
    with _session("hello world\n/quit\n") as session:
        code = await run_tui(runtime, cwd="/tmp", console=_console(), session=session, install_signal_handlers=False)  # type: ignore[arg-type]

    assert code == 0
    assert harness.bootstrapped == 1
    assert harness.prompts == [("hello world", "interactive")]
    assert harness.subscribers, "renderer must subscribe to the harness"
    assert runtime.rebind_cb is not None, "set_rebind_session must be wired"
    assert runtime.disposed == 1
    assert harness.unsubscribed == 1


async def test_run_tui_reload_command() -> None:
    harness = FakeHarness()
    runtime = FakeRuntime(harness)
    with _session("/reload\n/quit\n") as session:
        await run_tui(runtime, cwd="/tmp", console=_console(), session=session, install_signal_handlers=False)  # type: ignore[arg-type]
    assert harness.reloads == 1
    assert harness.prompts == []


async def test_run_tui_eof_exits_cleanly() -> None:
    harness = FakeHarness()
    runtime = FakeRuntime(harness)
    # A *closed*, empty pipe makes the first prompt_async raise EOFError
    # (Ctrl+D equivalent); an unclosed empty pipe would block forever.
    with create_pipe_input() as pipe:
        pipe.close()
        with create_app_session(input=pipe, output=DummyOutput()):
            session = build_prompt_session()
            code = await run_tui(
                runtime,
                cwd="/tmp",
                console=_console(),
                session=session,
                install_signal_handlers=False,
            )
    assert code == 0
    assert runtime.disposed == 1
    assert harness.prompts == []


async def test_run_tui_empty_line_is_skipped() -> None:
    harness = FakeHarness()
    runtime = FakeRuntime(harness)
    with _session("\n   \nreal prompt\n/quit\n") as session:
        await run_tui(runtime, cwd="/tmp", console=_console(), session=session, install_signal_handlers=False)  # type: ignore[arg-type]
    assert harness.prompts == [("real prompt", "interactive")]


async def test_run_tui_survives_turn_exception() -> None:
    """A failed turn must render the error and return to the prompt — NOT
    crash the REPL (parity with run_print_mode's turn-loop guard)."""

    class ExplodingHarness(FakeHarness):
        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            raise RuntimeError("turn blew up")

    harness = ExplodingHarness()
    runtime = FakeRuntime(harness)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    with _session("boom\n/quit\n") as session:
        code = await run_tui(runtime, cwd="/tmp", console=console, session=session, install_signal_handlers=False)  # type: ignore[arg-type]
    assert code == 0  # loop survived and reached /quit
    assert "turn blew up" in buf.getvalue()  # error surfaced
    assert runtime.disposed == 1
    assert harness.unsubscribed == 1


async def test_run_tui_keyboard_interrupt_mid_turn_aborts_and_continues() -> None:
    """Ctrl+C during a turn → best-effort abort, then the loop continues."""

    class InterruptingHarness(FakeHarness):
        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            raise KeyboardInterrupt

    harness = InterruptingHarness()
    runtime = FakeRuntime(harness)
    with _session("hello\n/quit\n") as session:
        code = await run_tui(runtime, cwd="/tmp", console=_console(), session=session, install_signal_handlers=False)  # type: ignore[arg-type]
    assert code == 0
    assert harness.aborts == 1


async def test_run_tui_bash_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """`!cmd` / `!!cmd` call handle_user_bash with the right context flag and
    render its output; they never reach harness.prompt."""

    calls: list[tuple[str, bool, str]] = []

    async def _fake_bash(harness, command, *, exclude_from_context, cwd):
        calls.append((command, exclude_from_context, cwd))
        return "bash output here\n"

    # Patch on the module object (not a dotted string) so monkeypatch does not
    # re-resolve "aelix_coding_agent.tui" via getattr — robust against other
    # tests that perturb the parent package's attributes.
    monkeypatch.setattr(tui_shell, "handle_user_bash", _fake_bash)
    harness = FakeHarness()
    runtime = FakeRuntime(harness)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    with _session("!ls\n!!secret\n/quit\n") as session:
        await run_tui(runtime, cwd="/work", console=console, session=session, install_signal_handlers=False)  # type: ignore[arg-type]
    assert calls == [("ls", False, "/work"), ("secret", True, "/work")]
    assert "bash output here" in buf.getvalue()
    assert harness.prompts == []  # bash lines bypass harness.prompt


async def test_run_tui_rebind_swaps_subscription() -> None:
    """The rebind closure drops the prior subscription and subscribes the new
    harness (session-swap survival, §7 acceptance criterion)."""

    harness1 = FakeHarness()
    runtime = FakeRuntime(harness1)
    with _session("/quit\n") as session:
        await run_tui(runtime, cwd="/tmp", console=_console(), session=session, install_signal_handlers=False)  # type: ignore[arg-type]

    assert harness1.subscribers  # initial subscribe happened
    assert runtime.rebind_cb is not None
    # Swap to a fresh harness via the captured closure: it must subscribe the
    # new harness (and drop the prior token).
    harness2 = FakeHarness()
    await runtime.rebind_cb(harness2)
    assert harness2.subscribers
