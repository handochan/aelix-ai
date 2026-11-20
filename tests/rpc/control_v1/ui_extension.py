"""The control-v1 fixture extension: one hook, one tool and the commands the cases run.

The ``full`` and ``child`` rigs load it in process (``rigs.py``), and the real
``--mode rpc`` child of ``test_real_child.py`` loads it with ``-e <this file>``,
so a dialog case is replayed through the same extension on both.

Every dialog command answers with what its dialog returned, so a case's
``expect`` can check that an ``extension_ui_response`` reached the command.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.extensions.api import ExtensionAPI

WAIT_NOTICE = "fixture-wait started"


async def _echo(args: dict[str, Any], ctx: Any) -> ToolResult:
    result = ToolResult(content=[TextContent(text=str(args.get("text", "")))])
    if ctx.on_partial is not None:
        ctx.on_partial(result)
    return result


ECHO = AgentTool(
    name="fixture_echo",
    description="Echoes its text (control-v1 fixture).",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    execute=_echo,
)


async def _ok(args: str, ctx: Any) -> str:
    return f"fixture-ok: {args}"


async def _fail(args: str, ctx: Any) -> str:
    raise ValueError("fixture failure")


async def _wait(args: str, ctx: Any) -> str:
    ctx.ui.notify(WAIT_NOTICE)
    await asyncio.Event().wait()
    return "unreachable"


async def _select(args: str, ctx: Any) -> str:
    return f"select: {await ctx.ui.select('Pick one', ['alpha', 'beta'])!r}"


async def _confirm(args: str, ctx: Any) -> str:
    return f"confirm: {await ctx.ui.confirm('Sure?', 'Proceed with the fixture')!r}"


async def _input(args: str, ctx: Any) -> str:
    return f"input: {await ctx.ui.input('Name', 'type a name')!r}"


async def _editor(args: str, ctx: Any) -> str:
    return f"editor: {await ctx.ui.editor('Edit', 'prefilled text')!r}"


async def _notify(args: str, ctx: Any) -> str:
    ctx.ui.notify("fixture notice", "warning")
    return "notified"


async def _status(args: str, ctx: Any) -> str:
    ctx.ui.set_status("fixture", "fixture status")
    return "status set"


async def _widget(args: str, ctx: Any) -> str:
    ctx.ui.set_widget("fixture", ["line one", "line two"])
    return "widget set"


async def _title(args: str, ctx: Any) -> str:
    ctx.ui.set_title("fixture title")
    return "title set"


COMMANDS = {
    "fixture-ok": (_ok, "Answers with its arguments."),
    "fixture-fail": (_fail, "Raises."),
    "fixture-wait": (_wait, "Notifies, then waits until it is cancelled."),
    "ui-select": (_select, "Opens a select dialog."),
    "ui-confirm": (_confirm, "Opens a confirm dialog."),
    "ui-input": (_input, "Opens an input dialog."),
    "ui-editor": (_editor, "Opens an editor dialog."),
    "ui-notify": (_notify, "Sends a notice."),
    "ui-status": (_status, "Sets a status line."),
    "ui-widget": (_widget, "Sets a widget."),
    "ui-title": (_title, "Sets the title."),
}


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(ECHO)
    import os

    if os.environ.get("AELIX_655_FIXTURE_PROVIDER") == "1":
        _register_transcript_provider(aelix)
    aelix.on("session_start", lambda event, ctx: None)
    for name, (handler, description) in COMMANDS.items():
        aelix.register_command(name, handler=handler, description=description)


async def transcript_stream(model: Any, context: Any, options: Any) -> Any:
    from aelix_ai.messages import AssistantMessage, ToolCallContent
    from aelix_ai.streaming import AssistantDoneEvent, AssistantStartEvent, TextDeltaEvent

    user = next(message for message in reversed(context.messages) if message.role == "user")
    text = user.content if isinstance(user.content, str) else "".join(
        block.text for block in user.content if isinstance(block, TextContent)
    )
    partial = AssistantMessage(content=[], provider=model.provider, model=model.id)
    yield AssistantStartEvent(partial=partial)
    yield TextDeltaEvent(delta="fixture", partial=partial)
    if text == "fixture wait":
        await asyncio.Event().wait()
    if text == "fixture tools" and context.messages[-1].role != "toolResult":
        yield AssistantDoneEvent(
            message=AssistantMessage(
                content=[ToolCallContent(tool_call_id="fixture-call", tool_name=ECHO.name, input={})],
                stop_reason="toolUse",
            ),
            reason="toolUse",
        )
    else:
        yield AssistantDoneEvent(
            message=AssistantMessage(content=[TextContent(text="fixture")], stop_reason="stop"),
            reason="stop",
        )


def _register_transcript_provider(aelix: ExtensionAPI) -> None:
    from aelix_ai.api_registry import register_provider
    from aelix_ai.streaming import Model
    from aelix_coding_agent.model_registry import ProviderConfigInput

    register_provider("fixture-transcript", transcript_stream)
    aelix.register_provider(
        "fixture-transcript",
        ProviderConfigInput(
            api_key="fixture-transcript-local",
            models={
                "local": Model(
                    id="local", provider="fixture-transcript", api="fixture-transcript",
                    base_url="http://127.0.0.1:9",
                )
            },
        ),
    )
