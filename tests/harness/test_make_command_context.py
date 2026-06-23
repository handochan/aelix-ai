"""Issue #9 — AgentHarness.make_command_context factory.

Builds a real :class:`ExtensionCommandContext` from the SAME closure assembly
as the hook context (``_make_context_kwargs``), so a slash-command handler gets
the full base context PLUS the 6 lifecycle methods. The bound UI flows through
the shared ``_ExtensionRuntime`` (the surface's ``bind_ui`` target).
"""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.extensions.command_context import ExtensionCommandContext


def _harness() -> AgentHarness:
    return AgentHarness(AgentHarnessOptions(session=Session(MemorySessionStorage())))


def test_make_command_context_returns_command_context() -> None:
    ctx = _harness().make_command_context()
    assert isinstance(ctx, ExtensionCommandContext)


def test_make_command_context_exposes_lifecycle_methods() -> None:
    ctx = _harness().make_command_context()
    for method in (
        "wait_for_idle",
        "new_session",
        "fork",
        "switch_session",
        "navigate_tree",
        "reload",
    ):
        assert callable(getattr(ctx, method)), method


def test_make_command_context_carries_base_context_fields() -> None:
    """The shared ``_make_context_kwargs`` assembly flows through (cwd, ui, …)."""
    h = _harness()
    ctx = h.make_command_context()
    # cwd came from harness options; ui is the runtime's bound (headless) default.
    assert isinstance(ctx.cwd, str)
    assert ctx.ui is h.runtime.ui


def test_make_command_context_threads_repo_and_session_runtime() -> None:
    sentinel_repo = object()
    sentinel_runtime = object()
    ctx = _harness().make_command_context(
        repo=sentinel_repo, session_runtime=sentinel_runtime
    )
    # Private slots (set via object.__setattr__ in ExtensionCommandContext).
    assert object.__getattribute__(ctx, "_repo") is sentinel_repo
    assert object.__getattribute__(ctx, "_runtime_session") is sentinel_runtime
