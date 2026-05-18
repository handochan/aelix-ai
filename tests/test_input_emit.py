"""Sprint 5b §B.1 — ``input`` event emit at AgentHarness.prompt() head."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    InputHandled,
    InputTransform,
)
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


def _stub_stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _capturing_stream(captured: list[str]) -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        for msg in context.messages:
            content = getattr(msg, "content", None)
            if content:
                for c in content:
                    text = getattr(c, "text", None)
                    if text is not None:
                        captured.append(text)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


async def test_input_no_handlers_passthrough():
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            stream_fn=_stub_stream(),
        )
    )
    out = await h.prompt("hello")
    assert out  # turn ran normally


async def test_input_handled_short_circuits():
    """Pi parity: handled returns from prompt() immediately."""

    calls = {"before_start": 0}

    def input_handler(event, ctx):
        return InputHandled()

    def before_start(event, ctx):
        calls["before_start"] += 1

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("input", input_handler)
    api.on("before_agent_start", before_start)
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            stream_fn=_stub_stream(),
        )
    )
    result = await h.prompt("ignored")
    assert result == []
    assert calls["before_start"] == 0
    assert h.phase == "idle"


async def test_input_transform_modifies_text():
    seen: list[str] = []

    def transform(event, ctx):
        return InputTransform(text=event.text.upper())

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("input", transform)
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            stream_fn=_capturing_stream(seen),
        )
    )
    await h.prompt("hello")
    assert any("HELLO" in t for t in seen)


async def test_input_source_default_interactive():
    captured = {}

    def handler(event, ctx):
        captured["source"] = event.source

    ext = Extension(name="t")
    api = ExtensionAPI(ext, _ExtensionRuntime())
    api.on("input", handler)
    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            extensions=[ext],
            stream_fn=_stub_stream(),
        )
    )
    await h.prompt("hi")
    assert captured["source"] == "interactive"
