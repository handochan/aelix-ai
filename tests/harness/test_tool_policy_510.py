"""#510 (ADR-0283) — the harness's one tool policy boundary, writer by writer.

Every write of ``state.tools`` / ``state.active_tool_names`` goes through
``AgentHarness._commit_tools``. These pin each writer against each rule:

* excluded (``--exclude-tools``) — never in the registry;
* locked off (``--no-tools``, ``--no-builtin-tools``, not named by ``--tools``)
  — registered, never active;
* saved off (``defaultTools``) — registered, inactive; only a new policy turns
  it back on.

Writers: the constructor seed (Codex F02), ``set_active_tools`` (the extension
action and the public setter), the ``register_tool`` refresh (``agent``'s
re-registration and a lazy pack), ``set_tools`` (an MCP attach),
``set_tool_policy`` (the panel's delta apply) and ``restore_active_tools``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    _rechain_system_prompt,
)
from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent, BeforeAgentStartResult
from aelix_agent_core.harness.tool_policy import ToolPolicy, tool_name_matcher, tool_pattern_matches
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import Extension

BUILTIN = frozenset({"read", "bash", "edit"})


def _tool(name: str) -> AgentTool:
    return AgentTool(name=name, description=f"{name} tool")


def _harness(
    policy: ToolPolicy | None = None,
    *,
    tools: tuple[str, ...] = ("read", "bash", "edit"),
    ext_tools: tuple[str, ...] = ("weather",),
    active: list[str] | None = None,
    rebuilder: Any = None,
    stream: Any = None,
) -> AgentHarness:
    ext = Extension(name="probe")
    for name in ext_tools:
        ext.tools[name] = _tool(name)
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="mock"),
            tools=[_tool(n) for n in tools],
            extensions=[ext],
            tool_policy=policy,
            active_tool_names=active,
            system_prompt_rebuilder=rebuilder,
            stream_fn=stream,
        )
    )


def _registry(h: AgentHarness) -> list[str]:
    return [t.name for t in h.state.tools]


def _active(h: AgentHarness) -> list[str]:
    return list(h._action_get_active_tools())


# --- the matcher (pi createToolNameMatcher) -----------------------------------


@pytest.mark.parametrize(
    ("pattern", "name", "hit"),
    [
        ("bash", "bash", True),
        ("bash", "bash2", False),
        ("github__*", "github__create_issue", True),
        ("github__*", "gitlab__x", False),
        ("*", "anything", True),
        ("a.b", "a.b", True),
        ("a.b", "aXb", False),  # "." is literal, not "any character"
        ("a+", "aa", False),  # "+" is literal
        ("a?", "ab", False),  # "?" is literal (pi has no "?")
        ("a?", "a?", True),
        ("[ab]", "a", False),
        ("*_status", "aelix_status", True),
        ("de*__e*o", "demo__echo", True),
    ],
)
def test_the_pattern_matcher_is_pis_star_only_matcher(pattern: str, name: str, hit: bool) -> None:
    assert tool_pattern_matches(pattern, name) is hit
    assert tool_name_matcher([pattern])(name) is hit


def test_the_policy_precedence_is_excluded_no_tools_no_builtin_tools_then_saved() -> None:
    p = ToolPolicy(
        exclude=("bash",),
        allow=frozenset({"bash", "read", "weather"}),
        builtin=BUILTIN,
        saved_off=frozenset({"read", "edit"}),
    )
    assert p.excluded("bash") == "--exclude-tools bash"
    read_lock = p.lock("read")
    assert read_lock is not None and read_lock.value is True  # --tools beats saved
    assert p.allows("read") and not p.allows("edit")  # not named by --tools
    assert p.refusal("bash") == "bash is removed for this run by --exclude-tools bash"
    nb = ToolPolicy(no_builtin_tools=True, builtin=BUILTIN, allow=frozenset({"read", "weather"}))
    assert not nb.allows("read") and nb.allows("weather")
    nt = ToolPolicy(no_tools=True, allow=frozenset({"read"}))
    assert not nt.allows("read")
    saved = ToolPolicy(saved_off=frozenset({"edit"}))
    assert not saved.allows("edit") and saved.lock("edit") is None
    assert (
        saved.refusal("edit") == "edit is turned off in settings (defaultTools); /tool turns it on"
    )


def test_a_profile_lock_reads_as_the_agent_profile() -> None:
    p = ToolPolicy(allow=frozenset({"read"}), allow_tag="profile")
    on, off = p.lock("read"), p.lock("bash")
    assert on is not None and off is not None
    assert on.reason == "named by the agent profile"
    assert off.reason == "not named by the agent profile"
    assert off.tag == "profile"


# --- F02: the constructor seed ------------------------------------------------


def test_the_constructor_never_registers_an_excluded_tool() -> None:
    h = _harness(ToolPolicy(exclude=("bash", "wea*")))
    assert _registry(h) == ["read", "edit"] or set(_registry(h)) == {"read", "edit"}
    assert {t.name for t, _r in h.excluded_tools()} == {"bash", "weather"}
    assert dict((t.name, r) for t, r in h.excluded_tools())["weather"] == "--exclude-tools wea*"


def test_the_constructor_seeds_only_what_the_policy_allows() -> None:
    h = _harness(ToolPolicy(saved_off=frozenset({"edit"})))
    assert "edit" in _registry(h)
    assert "edit" not in _active(h)
    unrestricted = _harness()
    assert unrestricted.state.active_tool_names is None  # no policy: as before #510


def test_an_explicit_seed_naming_an_excluded_tool_is_dropped_not_raised() -> None:
    h = _harness(ToolPolicy(exclude=("bash",)), active=["bash", "read"])
    assert _active(h) == ["read"]
    with pytest.raises(AgentHarnessError):
        _harness(ToolPolicy(exclude=("bash",)), active=["nope"])


# --- set_active_tools: narrows, never widens ---------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        ToolPolicy(exclude=("bash",)),
        ToolPolicy(no_tools=True),
        ToolPolicy(allow=frozenset({"read"})),
        ToolPolicy(no_builtin_tools=True, builtin=BUILTIN),
        ToolPolicy(saved_off=frozenset({"bash"})),
    ],
    ids=["excluded", "no-tools", "tools", "no-builtin", "saved-off"],
)
async def test_an_extension_cannot_turn_on_what_the_policy_keeps_off(policy: ToolPolicy) -> None:
    h = _harness(policy)
    h.runtime.actions.set_active_tools(["bash", "read", "weather"])
    assert "bash" not in _active(h)
    await h.set_active_tools(["bash"])
    assert "bash" not in _active(h)
    # Narrowing is still allowed, and an unknown name still raises (F-9).
    h.runtime.actions.set_active_tools([])
    assert _active(h) == []
    with pytest.raises(AgentHarnessError):
        await h.set_active_tools(["nope"])


# --- the register_tool refresh ------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        ToolPolicy(exclude=("late",)),
        ToolPolicy(no_tools=True),
        ToolPolicy(allow=frozenset({"read"})),
        ToolPolicy(saved_off=frozenset({"late"})),
    ],
    ids=["excluded", "no-tools", "tools", "saved-off"],
)
def test_a_late_registration_cannot_widen_the_set(policy: ToolPolicy) -> None:
    h = _harness(policy)
    ext = h._extensions[0]
    ext.tools["late"] = _tool("late")
    h._refresh_extension_tools()
    assert "late" not in _active(h)
    if policy.exclude:
        assert "late" not in _registry(h)
        assert "late" in {t.name for t, _r in h.excluded_tools()}


def test_the_agent_tools_re_registration_stays_excluded() -> None:
    h = _harness(ToolPolicy(exclude=("agent",)), ext_tools=("agent",))
    assert "agent" not in _registry(h)
    for _ in range(3):  # the roster changes: before_agent_start re-registers it
        h._extensions[0].tools["agent"] = _tool("agent")
        h._refresh_extension_tools()
        assert "agent" not in _registry(h)


def test_an_unrestricted_refresh_still_auto_activates_a_new_tool() -> None:
    h = _harness()
    h._extensions[0].tools["late"] = _tool("late")
    h._refresh_extension_tools()
    assert "late" in _active(h)


# --- set_tools (an MCP attach) -----------------------------------------------


async def test_set_tools_filters_an_excluded_tool_and_keeps_the_record() -> None:
    h = _harness(ToolPolicy(exclude=("bash", "srv__*")))
    await h.set_tools([*h.state.tools, _tool("srv__a"), _tool("srv__b"), _tool("other")])
    assert "srv__a" not in _registry(h) and "other" in _registry(h)
    assert {"bash", "srv__a", "srv__b"} <= {t.name for t, _r in h.excluded_tools()}
    await h.set_tools(list(h.state.tools), active_tool_names=["srv__a", "read"])
    assert _active(h) == ["read"]


# --- set_tool_policy (the panel's apply) and restore --------------------------


def test_set_tool_policy_is_a_delta_that_keeps_an_extensions_narrowing() -> None:
    policy = ToolPolicy(saved_off=frozenset({"edit"}))
    h = _harness(policy)
    h.runtime.actions.set_active_tools(["read", "weather"])  # an extension narrowed bash off
    h.set_tool_policy(ToolPolicy(saved_off=frozenset()), turn_on=["edit"])
    assert set(_active(h)) == {"read", "weather", "edit"}  # bash stays narrowed off
    h.set_tool_policy(ToolPolicy(saved_off=frozenset({"weather"})))
    assert set(_active(h)) == {"read", "edit"}


def test_a_strict_policy_change_whose_prompt_fails_changes_nothing() -> None:
    def _rebuild(tools: list[AgentTool]) -> str:
        if len(tools) > 3:
            raise RuntimeError("boom")
        return ",".join(t.name for t in tools)

    h = _harness(ToolPolicy(saved_off=frozenset({"edit"})), rebuilder=_rebuild)
    before = (list(h.state.tools), h.state.active_tool_names, h.state.system_prompt, h.tool_policy)
    with pytest.raises(RuntimeError):
        h.set_tool_policy(ToolPolicy(), turn_on=["edit"], strict=True)
    after = (list(h.state.tools), h.state.active_tool_names, h.state.system_prompt, h.tool_policy)
    assert after == before


def test_restore_active_tools_drops_unknown_names_and_clamps() -> None:
    h = _harness(ToolPolicy(saved_off=frozenset({"bash"})))
    h.restore_active_tools(["bash", "gone", "read"])
    assert _active(h) == ["read"]
    h.restore_active_tools(None, policy=ToolPolicy())
    assert h.state.active_tool_names is None


def test_every_commit_rebuilds_the_prompt_from_the_committed_set() -> None:
    seen: list[list[str]] = []

    def _rebuild(tools: list[AgentTool]) -> str:
        seen.append([t.name for t in tools])
        return "|".join(t.name for t in tools)

    h = _harness(ToolPolicy(exclude=("bash",)), rebuilder=_rebuild)
    h.runtime.actions.set_active_tools(["bash", "read"])
    assert h.state.system_prompt == "read"
    h._extensions[0].tools["late"] = _tool("late")
    h._refresh_extension_tools()
    assert "late" in h.state.system_prompt and "bash" not in h.state.system_prompt


# --- F05: a tool change inside before_agent_start ----------------------------


def test_rechain_puts_appended_text_on_the_rebuilt_prompt() -> None:
    assert _rechain_system_prompt("A + x", "A", "B") == "B + x"
    assert _rechain_system_prompt("x + A", "A", "B") == "x + B"
    assert _rechain_system_prompt("Z", "A", "B") == "Z"  # replaced outright: a Limit
    assert _rechain_system_prompt("A + x", "A", "A") == "A + x"
    # Codex F02: one handler prepends, a later one appends — the original sits inside.
    assert _rechain_system_prompt("p + A + x", "A", "B") == "p + B + x"
    # Repeated, it is not uniquely found inside: only a start or an end match.
    assert _rechain_system_prompt("A A + x", "A", "B") == "B A + x"
    assert _rechain_system_prompt("x A y A z", "A", "B") == "x A y A z"


async def test_a_tool_change_inside_before_agent_start_reaches_the_request_prompt() -> None:
    """Codex F05: the handler narrows to ``read`` and appends text to the event's prompt.

    Before the fix the request carried the PRE-hook prompt (``read,bash,edit``)
    plus the text, while its schema had only ``read``.
    """

    captured: dict[str, Any] = {}

    async def _stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured["prompt"] = context.system_prompt
        captured["tools"] = [t.name for t in context.tools or []]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    def _rebuild(tools: list[AgentTool]) -> str:
        return "Available: " + ",".join(t.name for t in tools)

    h = _harness(tools=("read", "bash"), ext_tools=(), rebuilder=_rebuild, stream=_stream)
    h.rebuild_system_prompt()

    async def _narrow(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        h.runtime.actions.set_active_tools(["read"])
        return BeforeAgentStartResult(system_prompt=event.system_prompt + "\n\nprofile text")

    h.hooks.on("before_agent_start", _narrow)
    await h.prompt("hi")
    assert captured["tools"] == ["read"]
    assert captured["prompt"] == "Available: read\n\nprofile text"


async def test_a_prefix_and_a_suffix_from_two_handlers_wrap_the_rebuilt_prompt() -> None:
    """Codex F02: one handler prepends, a later one narrows to ``read`` and appends.

    Before the fix the original sat inside the returned prompt with text on
    both sides, matched neither its start nor its end, and the request carried
    ``Available: read,bash`` while its schema had only ``read``.
    """

    captured: dict[str, Any] = {}

    async def _stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        captured["prompt"] = context.system_prompt
        captured["tools"] = [t.name for t in context.tools or []]
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    def _rebuild(tools: list[AgentTool]) -> str:
        return "Available: " + ",".join(t.name for t in tools)

    h = _harness(tools=("read", "bash"), ext_tools=(), rebuilder=_rebuild, stream=_stream)
    h.rebuild_system_prompt()

    async def _prefix(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(system_prompt="house rules\n\n" + event.system_prompt)

    async def _narrow(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        h.runtime.actions.set_active_tools(["read"])
        return BeforeAgentStartResult(system_prompt=event.system_prompt + "\n\nprofile text")

    h.hooks.on("before_agent_start", _prefix)
    h.hooks.on("before_agent_start", _narrow)
    await h.prompt("hi")
    assert captured["tools"] == ["read"]
    assert captured["prompt"] == "house rules\n\nAvailable: read\n\nprofile text"
