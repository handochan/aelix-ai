"""Sprint 6h₁₀b (ADR-0105) — run_tui (chrome-driven) headless smoke tests.

Drives the reworked ``run_tui`` with a headless ``AelixChrome`` (pipe input +
DummyOutput under create_app_session) and a fake runtime/harness that records
the lifecycle calls (bootstrap, bind_ui, subscribe, prompt, dispose).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from aelix_coding_agent.extensions import HEADLESS_UI_CONTEXT
from aelix_coding_agent.tui import shell as tui_shell
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


class _FakeHooks:
    async def emit(self, event: object) -> None:
        return None


class _FakeExtRuntime:
    def __init__(self) -> None:
        self.bound: list[object] = []

    def bind_ui(self, ui: object) -> None:
        self.bound.append(ui)


class FakeHarness:
    def __init__(self) -> None:
        self.bootstrapped = 0
        self.prompts: list[tuple[str, str]] = []
        self.reloads = 0
        self.aborts = 0
        self.subscribers: list[object] = []
        self.unsubscribed = 0
        self.runtime = _FakeExtRuntime()
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


async def _wait(predicate, *, timeout: float = 3.0) -> None:
    """Poll until ``predicate()`` is true (deterministic; no fixed sleeps)."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _harness_chrome(
    *, harness: FakeHarness | None = None
) -> AsyncGenerator[tuple[FakeRuntime, AelixChrome, PipeInput]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime = FakeRuntime(harness or FakeHarness())
        chrome = AelixChrome()
        yield runtime, chrome, pipe


def _launch(runtime: FakeRuntime, chrome: AelixChrome) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
    )


async def test_run_tui_drives_prompt_and_quits() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("hello world\n")
        await _wait(lambda: runtime.harness.prompts == [("hello world", "interactive")])
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert runtime.harness.bootstrapped == 1
    assert runtime.harness.subscribers, "renderer must subscribe"
    assert runtime.rebind_cb is not None
    assert runtime.disposed == 1


async def test_run_tui_binds_then_unbinds_ui() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)

    bound = runtime.harness.runtime.bound
    assert isinstance(bound[0], AelixTUIContext)  # real UI bound first
    assert bound[-1] is HEADLESS_UI_CONTEXT  # reverted to headless on teardown


async def test_run_tui_reload_command() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/reload\n")
        await _wait(lambda: runtime.harness.reloads == 1)
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert runtime.harness.reloads == 1
    assert runtime.harness.prompts == []


async def test_run_tui_eof_exits() -> None:
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("\x04")  # Ctrl+D on an empty buffer → EOF
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0
    assert runtime.disposed == 1
    assert runtime.harness.prompts == []


async def test_run_tui_survives_turn_exception() -> None:
    class ExplodingHarness(FakeHarness):
        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            self.prompts.append((text, source))
            raise RuntimeError("turn blew up")

    async with _harness_chrome(harness=ExplodingHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("boom\n")
        await _wait(lambda: runtime.harness.prompts == [("boom", "interactive")])
        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0  # a failed turn did not kill the REPL
    assert runtime.disposed == 1


async def test_run_tui_ctrl_c_during_turn_aborts_and_survives() -> None:
    class BlockingHarness(FakeHarness):
        def __init__(self) -> None:
            super().__init__()
            self._unblock = asyncio.Event()

        async def prompt(self, text: str, *, source: str = "interactive", images=None):
            self.prompts.append((text, source))
            await self._unblock.wait()  # block until aborted
            return []

        async def abort(self) -> None:
            self.aborts += 1
            self._unblock.set()  # the in-flight turn returns (harness contract)

    async with _harness_chrome(harness=BlockingHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("long turn\n")
        await _wait(lambda: runtime.harness.prompts == [("long turn", "interactive")])
        pipe.send_text("\x03")  # Ctrl+C mid-turn → on_interrupt → abort
        await _wait(lambda: runtime.harness.aborts == 1)
        pipe.send_text("/quit\n")  # REPL must still accept input
        code = await asyncio.wait_for(task, timeout=5)
    assert code == 0
    assert runtime.harness.aborts == 1


async def test_run_tui_bash_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool, str]] = []

    async def _fake_bash(harness, command, *, exclude_from_context, cwd):
        calls.append((command, exclude_from_context, cwd))
        return "bash output\n"

    # Patch on the module object (not a dotted string) — robust against other
    # tests in the full suite that perturb the aelix_coding_agent.tui attr.
    monkeypatch.setattr(tui_shell, "handle_user_bash", _fake_bash)
    async with _harness_chrome() as (runtime, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(runtime, cwd="/work", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("!ls\n")
        await _wait(lambda: calls == [("ls", False, "/work")])
        pipe.send_text("/quit\n")
        await asyncio.wait_for(task, timeout=5)
    assert calls == [("ls", False, "/work")]
    assert runtime.harness.prompts == []
