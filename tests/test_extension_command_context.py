"""Sprint 5b §D / P0 #7 item 4 — ExtensionCommandContext.

4 methods always bound (wait_for_idle / fork / navigate_tree / reload);
new_session / switch_session / fork delegate to a bound AgentSessionRuntime
(P0 #7 item 4) and raise a clear error when no runtime is bound.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime._types import RuntimeReplaceResult
from aelix_ai.streaming import Model
from aelix_coding_agent.extensions.api import (
    ExtensionError,
)
from aelix_coding_agent.extensions.command_context import (
    ExtensionCommandContext,
)


def _ctx(harness, repo=None, session_runtime=None):
    return ExtensionCommandContext(
        harness.runtime,
        harness=harness,
        repo=repo,
        session_runtime=session_runtime,
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


class _FakeRuntime:
    """Spy mirroring the AgentSessionRuntime command surface (Pi handlers)."""

    def __init__(self):
        self.new_session_calls: list[dict] = []
        self.switch_session_calls: list[tuple[str, dict]] = []
        self.fork_calls: list[tuple[str, dict]] = []

    async def new_session(
        self, *, parent_session=None, setup=None, with_session=None
    ) -> RuntimeReplaceResult:
        self.new_session_calls.append(
            {
                "parent_session": parent_session,
                "setup": setup,
                "with_session": with_session,
            }
        )
        return RuntimeReplaceResult(cancelled=False)

    async def switch_session(
        self, path, *, options=None, with_session=None
    ) -> RuntimeReplaceResult:
        self.switch_session_calls.append(
            (path, {"with_session": with_session})
        )
        return RuntimeReplaceResult(cancelled=False)

    async def fork(
        self, entry_id, *, position="before", with_session=None
    ) -> RuntimeReplaceResult:
        self.fork_calls.append(
            (entry_id, {"position": position, "with_session": with_session})
        )
        return RuntimeReplaceResult(cancelled=False, selected_text="hi")


async def test_wait_for_idle_delegates_to_harness():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    await ctx.wait_for_idle()  # idle by default; returns immediately.


async def test_navigate_tree_no_session_raises_invalid_state():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    # navigate_tree requires session; we expect harness to raise AgentHarnessError.
    from aelix_agent_core.harness.core import AgentHarnessError

    with pytest.raises(AgentHarnessError) as exc_info:
        await ctx.navigate_tree("target")
    assert exc_info.value.code == "invalid_state"


async def test_reload_delegates_to_reload_resources():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    await ctx.reload()  # no handlers → noop.


# === fork ===================================================================


async def test_fork_raises_when_unbound():
    """No runtime AND no repo → clear error (not the old 'deferred' message)."""
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.fork("entry-1")
    assert exc_info.value.code == "invalid_state"
    assert "deferred" not in str(exc_info.value).lower()


async def test_fork_delegates_to_runtime():
    """Bound runtime → fork delegates with realigned (entry_id, position) shape."""
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)

    async def _ws(_replaced):
        return None

    result = await ctx.fork(
        "entry-7", {"position": "at", "with_session": _ws}
    )
    assert isinstance(result, RuntimeReplaceResult)
    assert result.selected_text == "hi"
    assert runtime.fork_calls == [
        ("entry-7", {"position": "at", "with_session": _ws})
    ]


async def test_fork_falls_back_to_repo_when_no_runtime():
    """No runtime but a repo bound → legacy repo.fork path (unattached)."""
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))

    class _FakeRepo:
        def __init__(self):
            self.fork_calls = []

        async def fork(self, source, options):
            self.fork_calls.append((source, options))
            return "forked-session"

    repo = _FakeRepo()
    ctx = _ctx(h, repo=repo)
    result = await ctx.fork("meta", "opts")
    assert result == "forked-session"
    assert repo.fork_calls == [("meta", "opts")]


# === new_session ============================================================


async def test_new_session_raises_when_unbound():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.new_session()
    assert exc_info.value.code == "invalid_state"
    assert "deferred" not in str(exc_info.value).lower()


async def test_new_session_delegates_to_runtime():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)

    async def _setup(_sm):
        return None

    async def _ws(_replaced):
        return None

    result = await ctx.new_session(
        {"parent_session": "p.jsonl", "setup": _setup, "with_session": _ws}
    )
    assert isinstance(result, RuntimeReplaceResult)
    assert result.cancelled is False
    assert runtime.new_session_calls == [
        {"parent_session": "p.jsonl", "setup": _setup, "with_session": _ws}
    ]


async def test_new_session_delegates_with_no_options():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)
    await ctx.new_session()
    assert runtime.new_session_calls == [
        {"parent_session": None, "setup": None, "with_session": None}
    ]


# === switch_session =========================================================


async def test_switch_session_raises_when_unbound():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    ctx = _ctx(h)
    with pytest.raises(ExtensionError) as exc_info:
        await ctx.switch_session("target")
    assert exc_info.value.code == "invalid_state"
    assert "deferred" not in str(exc_info.value).lower()


async def test_switch_session_delegates_to_runtime():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)

    async def _ws(_replaced):
        return None

    result = await ctx.switch_session("/path.jsonl", {"with_session": _ws})
    assert isinstance(result, RuntimeReplaceResult)
    assert runtime.switch_session_calls == [
        ("/path.jsonl", {"with_session": _ws})
    ]


async def test_switch_session_delegates_with_no_options():
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)
    await ctx.switch_session("/path.jsonl")
    assert runtime.switch_session_calls == [
        ("/path.jsonl", {"with_session": None})
    ]


# === ReplacedSessionContext threading =======================================


async def test_with_session_callback_threads_through_runtime():
    """The with_session callback is forwarded verbatim to the runtime, which
    is responsible for producing the ReplacedSessionContext handle via
    create_replaced_session_context (verified at the runtime layer)."""
    h = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))
    runtime = _FakeRuntime()
    ctx = _ctx(h, session_runtime=runtime)
    sentinel_called = {"n": 0}

    async def _ws(_replaced):
        sentinel_called["n"] += 1

    await ctx.new_session({"with_session": _ws})
    # The exact same callable object reaches the runtime (delegation, not copy).
    forwarded = runtime.new_session_calls[0]["with_session"]
    assert forwarded is _ws


# === surface ================================================================


def test_ecc_full_surface_6_methods():
    """Pi parity ``dir(ExtensionCommandContext)`` closure (P-35)."""

    members = set(dir(ExtensionCommandContext))
    for name in (
        "wait_for_idle",
        "fork",
        "navigate_tree",
        "reload",
        "new_session",
        "switch_session",
    ):
        assert name in members, f"ExtensionCommandContext missing {name}"
