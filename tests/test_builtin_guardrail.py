"""Tests for the built-in GuardrailExtension.

Default rules are tested both via the handler directly and via a full
AgentHarness integration. The handler is public enough to call as
``guardrail._on_tool_call(event, ctx=None)`` in unit tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.builtin.guardrail import GuardrailExtension, GuardrailRule
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

# ============================================================
# Helpers
# ============================================================


def _make_mock_stream(turn_finals: list[AssistantMessage]) -> Any:
    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        if i >= len(turn_finals):
            raise AssertionError(
                f"mock stream_fn exhausted at idx={i}"
            )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=turn_finals[i])

    return fn


def _bash_event(command: str) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="bash",
        args={"command": command},
    )


def _write_event(path: str, tool_name: str = "write") -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name=tool_name,
        args={"path": path},
    )


def _build_ext_with_guardrail(guardrail: GuardrailExtension) -> Extension:
    runtime = _ExtensionRuntime()
    ext = Extension(name="guardrail_ext")
    api = ExtensionAPI(extension=ext, runtime=runtime)
    guardrail(api)
    return ext


def _should_block(guardrail: GuardrailExtension, event: ToolCallHookEvent) -> bool:
    result = guardrail._on_tool_call(event, None)  # type: ignore[arg-type]
    return isinstance(result, ToolCallResult) and result.block


# ============================================================
# Default rule: rm -rf variants (D.1.13 M-7)
# ============================================================


async def test_default_blocks_rm_rf_in_bash_command() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _bash_event("rm -rf /tmp/foo"))


async def test_default_blocks_rm_fr_variant() -> None:
    """rm -fr must be blocked (D.1.13 M-7 — fr flag order)."""
    g = GuardrailExtension()
    assert _should_block(g, _bash_event("rm -fr /tmp/foo"))


async def test_default_blocks_sudo_rm_r() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _bash_event("sudo rm -r /home/user"))


async def test_default_blocks_absolute_path_rm() -> None:
    """'/bin/rm -rf /foo' must be blocked (D.1.13 M-7 — path prefix)."""
    g = GuardrailExtension()
    assert _should_block(g, _bash_event("/bin/rm -rf /foo"))


async def test_default_blocks_fork_bomb() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _bash_event(":(){ :|:& };:"))


# ============================================================
# Default rule: write to protected paths
# ============================================================


async def test_default_blocks_write_to_dotenv() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _write_event(".env"))


async def test_default_blocks_write_to_dotenv_local() -> None:
    """'.env.local', '.env.production' must be blocked."""
    g = GuardrailExtension()
    assert _should_block(g, _write_event(".env.local"))
    assert _should_block(g, _write_event(".env.production"))


async def test_default_blocks_write_to_node_modules() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _write_event("project/node_modules/lodash/index.js"))


async def test_default_blocks_write_to_git_dir() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _write_event(".git/config"))


async def test_default_blocks_write_to_pycache() -> None:
    g = GuardrailExtension()
    assert _should_block(g, _write_event("src/__pycache__/module.pyc"))


# ============================================================
# Disabling a default rule
# ============================================================


async def test_disabled_default_rule_no_longer_blocks() -> None:
    """Disabling the 'bash.rm_rf' rule means 'rm -rf' passes through."""
    g = GuardrailExtension(disabled_default_rules=frozenset({"bash.rm_rf"}))
    result = g._on_tool_call(_bash_event("rm -rf /tmp/safe"), None)  # type: ignore[arg-type]
    # Must NOT block (None or non-blocking result)
    assert result is None or (isinstance(result, ToolCallResult) and not result.block)


# ============================================================
# Additional (user-supplied) patterns
# ============================================================


async def test_additional_pattern_blocks() -> None:
    """A user-supplied GuardrailRule must block matching calls."""

    def _no_curl(event: ToolCallHookEvent) -> str | None:
        cmd = event.args.get("command", "")
        if "curl" in cmd:
            return "[custom] curl is not allowed"
        return None

    custom_rule = GuardrailRule(
        name="custom.no_curl",
        applies_to_tools=frozenset({"bash"}),
        predicate=_no_curl,
    )

    g = GuardrailExtension(additional_patterns=(custom_rule,))
    assert _should_block(g, _bash_event("curl http://example.com"))


# ============================================================
# Predicate returning None does not block
# ============================================================


async def test_rule_predicate_returning_none_does_not_block() -> None:
    """A rule whose predicate returns None must not produce a block."""
    g = GuardrailExtension()
    result = g._on_tool_call(_bash_event("ls -la"), None)  # type: ignore[arg-type]
    assert result is None or (isinstance(result, ToolCallResult) and not result.block)


# ============================================================
# applies_to_tools scoping
# ============================================================


async def test_rule_only_applies_to_listed_tools() -> None:
    """A rule with applies_to_tools={"bash"} must NOT fire on a 'write' call."""

    def _always_block(event: ToolCallHookEvent) -> str | None:
        return "[test] always block"

    bash_only_rule = GuardrailRule(
        name="test.bash_only",
        applies_to_tools=frozenset({"bash"}),
        predicate=_always_block,
    )

    g = GuardrailExtension(
        disabled_default_rules=frozenset(
            {"bash.rm_rf", "bash.sudo_rm_r", "bash.fork_bomb",
             "write.dotenv", "write.git_dir", "write.node_modules", "write.pycache"}
        ),
        additional_patterns=(bash_only_rule,),
    )

    # Must block on bash
    assert _should_block(g, _bash_event("anything"))

    # Must NOT block on write (applies_to_tools restricts to bash only)
    write_event = _write_event("/some/safe/path.txt")
    result = g._on_tool_call(write_event, None)  # type: ignore[arg-type]
    assert result is None or (isinstance(result, ToolCallResult) and not result.block)


# ============================================================
# Integration — full harness + GuardrailExtension
# ============================================================


# ============================================================
# H-4: False-positive policy — conservative blocking is intentional
# ============================================================


async def test_guardrail_blocks_quoted_rm_rf_acceptable_false_positive() -> None:
    """Intentional false positive per docstring policy: rm -rf substring in quoted arg blocks.

    The guardrail pattern is conservative — it matches 'rm -rf' anywhere in the
    command string, including inside commit messages or quoted args. This is
    intentional; apps needing fine-grained control should disable the default rule.
    """
    g = GuardrailExtension()
    # intentional false positive per docstring policy
    assert _should_block(g, _bash_event('git commit -m "remove rm -rf trick"'))


# ============================================================
# Integration — full harness + GuardrailExtension
# ============================================================


async def test_integration_with_harness_blocks_actual_bash_call() -> None:
    """Full chain: GuardrailExtension blocks rm -rf / from reaching bash.execute."""

    executed = {"count": 0}

    async def bash_execute(args: dict, ctx: ToolExecutionContext) -> ToolResult:
        executed["count"] += 1
        return ToolResult(content=[TextContent(text="executed")])

    bash_tool = AgentTool(name="bash", execute=bash_execute)
    guardrail = GuardrailExtension()
    ext = _build_ext_with_guardrail(guardrail)

    stream = _make_mock_stream(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id="t1",
                        tool_name="bash",
                        input={"command": "rm -rf /"},
                    )
                ],
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            ),
        ]
    )

    h = AgentHarness(
        AgentHarnessOptions(
            extensions=[ext],
            tools=[bash_tool],
            stream_fn=stream,
        )
    )

    new_messages = await h.prompt("delete everything")

    # bash.execute must NOT have run
    assert executed["count"] == 0

    # A blocked tool result must appear in the new messages
    tool_results = [m for m in new_messages if isinstance(m, ToolResultMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
