"""Tests for the built-in PermissionExtension.

PermissionExtension is an ExtensionFactory callable — ``perm(aelix)`` registers
the ``tool_call`` + ``session_shutdown`` handlers. These unit tests exercise the
``_on_tool_call`` / ``_on_shutdown`` handlers directly with a fake
ExtensionContext whose ``ui`` stub returns scripted ``select`` / ``input``
values.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)
from aelix_coding_agent.builtin.permission import (
    PermissionExtension,
    _rule_key,
    _session_wildcard,
)

# ============================================================
# Fakes
# ============================================================


class _FakeUI:
    """Minimal stub UI: scripted ``select`` / ``input`` + call counters."""

    def __init__(
        self,
        *,
        select_return: str | None = None,
        input_return: str | None = None,
    ) -> None:
        self._select_return = select_return
        self._input_return = input_return
        self.select_calls = 0
        self.input_calls = 0
        self.last_select_title: str | None = None

    async def select(
        self,
        title: str,
        options: list[str],
        opts: Any = None,
    ) -> str | None:
        self.select_calls += 1
        self.last_select_title = title
        return self._select_return

    async def input(
        self,
        title: str,
        placeholder: str | None = None,
        opts: Any = None,
    ) -> str | None:
        self.input_calls += 1
        return self._input_return


class _FakeCtx:
    """Fake ExtensionContext exposing only ``has_ui`` + ``ui``."""

    def __init__(self, *, has_ui: bool, ui: _FakeUI | None = None) -> None:
        self.has_ui = has_ui
        self.ui = ui


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


def _read_event() -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="read",
        args={"path": "/etc/hosts"},
    )


# ============================================================
# Non-mutating tools — silent allow
# ============================================================


async def test_non_mutating_tool_returns_none() -> None:
    perm = PermissionExtension()
    ctx = _FakeCtx(has_ui=True, ui=_FakeUI(select_return="No"))
    result = await perm._on_tool_call(_read_event(), ctx)  # type: ignore[arg-type]
    assert result is None
    # A read-only tool must NOT prompt.
    assert ctx.ui is not None and ctx.ui.select_calls == 0


# ============================================================
# Headless (no UI) — default allow
# ============================================================


async def test_mutating_headless_returns_none() -> None:
    perm = PermissionExtension()
    ctx = _FakeCtx(has_ui=False)
    result = await perm._on_tool_call(_bash_event("rm foo"), ctx)  # type: ignore[arg-type]
    assert result is None


# ============================================================
# select -> "Yes" : allow once, NOT stored
# ============================================================


async def test_select_yes_allows_once_not_stored() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="Yes")
    ctx = _FakeCtx(has_ui=True, ui=ui)

    result1 = await perm._on_tool_call(_bash_event("ls -la"), ctx)  # type: ignore[arg-type]
    assert result1 is None
    assert ui.select_calls == 1

    # A second matching call must prompt again (NOT stored).
    result2 = await perm._on_tool_call(_bash_event("ls -la"), ctx)  # type: ignore[arg-type]
    assert result2 is None
    assert ui.select_calls == 2


# ============================================================
# select -> "Yes, for this session" : stored + 2nd call no prompt
# ============================================================


async def test_select_yes_for_session_stores_rule() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="Yes, for this session")
    ctx = _FakeCtx(has_ui=True, ui=ui)

    result1 = await perm._on_tool_call(_bash_event("git status"), ctx)  # type: ignore[arg-type]
    assert result1 is None
    assert ui.select_calls == 1

    # A 2nd matching call (same first-2-tokens) must NOT prompt again.
    result2 = await perm._on_tool_call(_bash_event("git status --short"), ctx)  # type: ignore[arg-type]
    assert result2 is None
    assert ui.select_calls == 1  # no new prompt


async def test_select_yes_for_session_path_wildcard() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="Yes, for this session")
    ctx = _FakeCtx(has_ui=True, ui=ui)

    result1 = await perm._on_tool_call(_write_event("src/a.py"), ctx)  # type: ignore[arg-type]
    assert result1 is None
    assert ui.select_calls == 1

    # Another file in the same dir matches ``src/*`` — no new prompt.
    result2 = await perm._on_tool_call(_write_event("src/b.py"), ctx)  # type: ignore[arg-type]
    assert result2 is None
    assert ui.select_calls == 1


# ============================================================
# select -> "No" : block
# ============================================================


async def test_select_no_blocks() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="No")
    ctx = _FakeCtx(has_ui=True, ui=ui)
    result = await perm._on_tool_call(_bash_event("rm foo"), ctx)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason is not None


# ============================================================
# select -> "No, provide reason" : block with reason text
# ============================================================


async def test_select_no_with_reason_blocks_with_reason() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="No, provide reason", input_return="because")
    ctx = _FakeCtx(has_ui=True, ui=ui)
    result = await perm._on_tool_call(_write_event("out.txt"), ctx)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason is not None
    assert "because" in result.reason
    assert ui.input_calls == 1


# ============================================================
# select -> None (Esc / cancelled) : block
# ============================================================


async def test_select_cancelled_blocks() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return=None)
    ctx = _FakeCtx(has_ui=True, ui=ui)
    result = await perm._on_tool_call(_bash_event("rm foo"), ctx)  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True


# ============================================================
# Helpers — _rule_key + _session_wildcard
# ============================================================


def test_rule_key_bash_uses_command() -> None:
    assert _rule_key("bash", {"command": "git status"}) == "git status"


def test_rule_key_write_uses_path() -> None:
    assert _rule_key("write", {"path": "src/a.py"}) == "src/a.py"
    # ``file_path`` is also accepted (edit-family arg name).
    assert _rule_key("edit", {"file_path": "src/b.py"}) == "src/b.py"


def test_session_wildcard_bash_first_two_tokens() -> None:
    wc = _session_wildcard("bash", {"command": "git status --short"})
    assert wc == "git status *"


def test_session_wildcard_bash_single_token() -> None:
    assert _session_wildcard("bash", {"command": "ls"}) == "ls *"


def test_session_wildcard_path_parent_dir() -> None:
    assert _session_wildcard("write", {"path": "src/.env"}) == "src/*"


def test_session_wildcard_path_no_parent() -> None:
    assert _session_wildcard("write", {"path": "out.txt"}) == "*"


# ============================================================
# session_shutdown clears session rules
# ============================================================


async def test_session_shutdown_clears_session_allows() -> None:
    perm = PermissionExtension()
    ui = _FakeUI(select_return="Yes, for this session")
    ctx = _FakeCtx(has_ui=True, ui=ui)

    await perm._on_tool_call(_bash_event("git status"), ctx)  # type: ignore[arg-type]
    assert perm._session_allows  # populated

    perm._on_shutdown(SessionShutdownHookEvent(), ctx)  # type: ignore[arg-type]
    assert not perm._session_allows  # cleared

    # After shutdown, a matching call prompts again.
    result = await perm._on_tool_call(_bash_event("git status --short"), ctx)  # type: ignore[arg-type]
    assert result is None
    assert ui.select_calls == 2
