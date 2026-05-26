"""Sprint 6h₁₀e §F — fixture extension + real-harness descriptor QA.

A test-only Tier-1 fixture extension subscribes to ``ui:list-modules`` and
appends a command-route + status-item + toast + management-modal. A REAL
:class:`AgentHarness` (``options.extensions=[fixture]`` + the shared runtime)
drives ``run_tui`` headlessly (pipe input + DummyOutput under
``create_app_session``) to assert the live probe → render path: the command-route
reaches the completer's live route store, the status item renders to the chrome
status, the toast adds a float, and the management-modal becomes openable through
the registry.

Headless: no real TTY, no real sleeps, stubbed ``stream_fn`` (no network).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai import (
    AssistantEndEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextContent,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.descriptors import DescriptorRenderer, ListModulesProbe
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

# --- the fixture extension (Tier-1 setup(api)) -------------------------------


def _fixture_setup(api: ExtensionAPI) -> None:
    """Append a command-route + status-item + toast + management-modal on probe."""

    def _on_list_modules(probe: ListModulesProbe) -> None:
        probe.modules.extend(
            [
                {
                    "kind": "command-route",
                    "namespace": "fix",
                    "id": "route",
                    "payload": {
                        "kind": "command-route",
                        "command": "deploy",
                        "description": "Deploy the app",
                    },
                },
                {
                    "kind": "status-item",
                    "namespace": "fix",
                    "id": "stat",
                    "payload": {"kind": "status-item", "text": "fixture-ready"},
                },
                {
                    "kind": "toast",
                    "namespace": "fix",
                    "id": "toast",
                    "payload": {"kind": "toast", "text": "hello", "auto_dismiss_ms": 0},
                },
                {
                    "kind": "management-modal",
                    "namespace": "fix",
                    "id": "modal",
                    "payload": {
                        "kind": "management-modal",
                        "command": "settings",
                        "title": "Settings",
                        "view": "form",
                    },
                },
            ]
        )

    api.events.on("ui:list-modules", _on_list_modules)


def _stub_stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


# --- runtime host wrapping a REAL AgentHarness -------------------------------


class _RealHarnessHost:
    """Minimal ``AgentSessionRuntime`` surface over a real :class:`AgentHarness`.

    Mirrors the smoke test's ``FakeRuntime`` (``harness`` / ``set_rebind_session``
    / ``dispose``) but the wrapped harness is a real one whose ``runtime`` exposes
    the shared :class:`EventBus` the fixture subscribed to.
    """

    def __init__(self, harness: AgentHarness) -> None:
        self._harness = harness
        self.rebind_cb: Any = None
        self.disposed = 0

    @property
    def harness(self) -> AgentHarness:
        return self._harness

    def set_rebind_session(self, cb: Any) -> None:
        self.rebind_cb = cb

    async def dispose(self) -> None:
        self.disposed += 1


def _build_host() -> _RealHarnessHost:
    runtime = _ExtensionRuntime()
    ext = Extension(name="fixture")
    api = ExtensionAPI(ext, runtime)
    _fixture_setup(api)  # registers the ui:list-modules subscriber on the bus
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            runtime=runtime,
            stream_fn=_stub_stream(),
        )
    )
    return _RealHarnessHost(harness)


async def _wait(predicate: Any, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


# === the real-harness QA test ================================================


async def test_fixture_ext_probe_renders_and_routes() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        host = _build_host()
        chrome = AelixChrome()
        task = asyncio.ensure_future(
            run_tui(host, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)

        # The probe fired at startup → the fixture's descriptors are applied.
        # status-item → chrome status; toast → a float; command-route → completer.
        await _wait(lambda: "fix:stat" in chrome._status)
        assert chrome._status["fix:stat"] == "fixture-ready"
        assert any(f is not chrome._completions_float for f in chrome._floats)

        # command-route reaches the live completer (typing "/de" offers "/deploy").
        from prompt_toolkit.completion import CompleteEvent
        from prompt_toolkit.document import Document

        completer = chrome.buffer.completer
        assert completer is not None
        completions = list(completer.get_completions(Document("/de"), CompleteEvent()))
        assert any(c.text == "/deploy" for c in completions)

        pipe.send_text("/quit\n")
        code = await asyncio.wait_for(task, timeout=5)

    assert code == 0
    assert host.disposed == 1


async def test_fixture_ext_management_modal_command_opens_not_prompts() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        host = _build_host()
        chrome = AelixChrome()
        opened: list[Any] = []
        orig_open = DescriptorRenderer.open_modal

        def _spy_open(self: DescriptorRenderer, env: Any) -> None:
            opened.append(env)
            orig_open(self, env)

        DescriptorRenderer.open_modal = _spy_open  # type: ignore[method-assign]
        try:
            task = asyncio.ensure_future(
                run_tui(host, cwd=".", chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
            )
            await _wait(lambda: chrome.app.is_running)
            await _wait(lambda: "fix:stat" in chrome._status)  # probe completed

            # "/settings" matches the stored management-modal → open_modal, NOT prompt.
            pipe.send_text("/settings\n")
            await _wait(lambda: len(opened) == 1)
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=5)
        finally:
            DescriptorRenderer.open_modal = orig_open  # type: ignore[method-assign]

    assert len(opened) == 1
    assert getattr(opened[0].payload, "command", None) == "settings"
