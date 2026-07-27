"""The DELEGATED-CHILD headless floor on :class:`PermissionExtension` (ADR-0197 §(e)).

A subagent runs as ``-m aelix_coding_agent --mode json -p --no-session``, so its
``ctx.has_ui`` is :data:`False` and it reaches branch (d) of the
``_on_tool_call`` ladder — which ships as **ALLOW** to preserve non-interactive
behaviour for every ``-p`` / json / rpc user. Without an override that means a
delegated child auto-approves every mutating tool with no channel back to the
human. ``headless_default="block"`` (flipped by ``cli/entry.py`` ONLY when
``subagent_depth() > 0``) turns that terminal allow into a model-readable block.

The point of this file is as much to pin what the floor is **not**:
:func:`test_auto_accept_write_bypasses_headless_floor` documents review finding
B4 — branch (f) (the AUTO_ACCEPT in-cwd write short-circuit) ``return None``s
ABOVE branch (d), so the floor cannot bound a child running under AUTO_ACCEPT.
The real guarantee is the spawner-side posture CLAMP
(``aelix_agents.posture.child_permission_mode``). Nobody may mistake the belt
for the braces again.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import (
    _HEADLESS_BLOCK_REASON,
    PermissionExtension,
)
from aelix_coding_agent.builtin.permission_mode import (
    PermissionMode,
    PermissionPosture,
)

# ============================================================
# Fakes — mirrors ``tests/builtin/test_permission.py``'s shape
# ============================================================


class _FakeUI:
    """A UI that FAILS the test if it is ever consulted.

    Every case in this file is headless, so a ``select`` call would mean the
    ladder fell through branch (d) into the prompt at ``:385-392`` — which in a
    real child would hang on a terminal that does not exist.
    """

    async def select(self, title: str, options: list[str], opts: object = None) -> str:
        raise AssertionError(f"headless run must never prompt (title={title!r})")

    async def input(
        self, title: str, placeholder: str | None = None, opts: object = None
    ) -> str:
        raise AssertionError(f"headless run must never prompt (title={title!r})")


class _FakeCtx:
    """Fake ExtensionContext exposing ``has_ui`` + ``ui`` + ``cwd``."""

    def __init__(self, *, has_ui: bool, cwd: str = "/proj") -> None:
        self.has_ui = has_ui
        self.ui = _FakeUI()
        self.cwd = cwd


def _child(mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionExtension:
    """A PermissionExtension configured the way ``cli/entry.py`` configures a child."""

    return PermissionExtension(
        posture=PermissionPosture(mode=mode), headless_default="block"
    )


def _bash_event(command: str = "echo hi") -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1", tool_name="bash", args={"command": command}
    )


def _write_event(path: str = "src/app.py") -> ToolCallHookEvent:
    return ToolCallHookEvent(tool_call_id="t1", tool_name="write", args={"path": path})


def _read_event() -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1", tool_name="read", args={"path": "src/app.py"}
    )


# ============================================================
# The default is UNCHANGED — the regression guard for every -p user
# ============================================================


def test_headless_default_is_allow() -> None:
    """A bare ``PermissionExtension()`` still defaults to the shipped behaviour."""

    assert PermissionExtension().headless_default == "allow"


@pytest.mark.parametrize(
    "event",
    [_bash_event(), _write_event()],
    ids=["bash", "write"],
)
async def test_headless_default_allow_is_unchanged(event: ToolCallHookEvent) -> None:
    """``headless_default="allow"`` → the pre-ADR-0197 terminal ALLOW at branch (d).

    This is the regression guard for every existing ``-p`` / ``--mode json`` /
    ``--mode rpc`` user: the new field must be inert unless a child sets it.
    """

    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT))
    result = await perm._on_tool_call(event, _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    assert result is None


# ============================================================
# The child floor
# ============================================================


@pytest.mark.parametrize(
    "event",
    [_bash_event(), _write_event(), _write_event("/proj/src/app.py")],
    ids=["bash", "relative-write", "absolute-in-cwd-write"],
)
async def test_headless_default_block_blocks_write_and_bash(
    event: ToolCallHookEvent,
) -> None:
    """A DEFAULT-posture child blocks every mutating tool, with the model-facing reason."""

    result = await _child()._on_tool_call(event, _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == _HEADLESS_BLOCK_REASON


async def test_headless_block_still_allows_read_only() -> None:
    """The floor gates MUTATING tools only — a child must still be able to investigate.

    Branch (a) (``permission.py:324-325``) returns before branch (d) is reached,
    so ``read`` is unaffected. A floor that also blocked reads would make a
    read-only delegated agent useless, which is P2's entire default posture.
    """

    result = await _child()._on_tool_call(_read_event(), _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    assert result is None


async def test_headless_block_applies_under_yolo() -> None:
    """Even YOLO cannot get past the floor once ``headless_default="block"``.

    YOLO returns at branch (e) ``:339-340`` — ABOVE branch (d) — so this asserts
    the ONE thing the floor genuinely cannot do, and pins it deliberately: a
    YOLO child is allowed. The clamp is what stops a YOLO child from existing
    unless the PARENT is already YOLO (``child_permission_mode``).
    """

    perm = _child(PermissionMode.YOLO)
    result = await perm._on_tool_call(_bash_event(), _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    # Documented, not desired: branch (e) short-circuits above the floor.
    assert result is None


# ============================================================
# What sits ABOVE branch (d) — the reason the floor is not the guarantee
# ============================================================


@pytest.mark.parametrize("headless_default", ["allow", "block"])
async def test_plan_mode_blocks_above_headless_branch(headless_default: str) -> None:
    """PLAN blocks at branch (b) (``permission.py:318-321``), above branch (d).

    This is why ``child_permission_mode`` tightens a clamped DEFAULT to PLAN
    rather than relying on ``headless_default``: the PLAN denial holds no matter
    how the floor is configured, and carries a reason the model can act on.
    """

    perm = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.PLAN),
        headless_default=headless_default,  # type: ignore[arg-type]
    )
    result = await perm._on_tool_call(_bash_event(), _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    # The PLAN reason, NOT the headless one — proving which branch fired.
    assert result.reason != _HEADLESS_BLOCK_REASON
    assert "Plan mode" in result.reason


async def test_auto_accept_write_bypasses_headless_floor() -> None:
    """B4, documented as executable evidence: branch (f) returns ABOVE branch (d).

    ``headless_default="block"`` + AUTO_ACCEPT + an in-cwd write → **ALLOW**,
    because ``permission.py``'s AUTO_ACCEPT write short-circuit ``return None``s
    before ``not ctx.has_ui`` is ever evaluated. The floor is a belt; the
    spawner-side posture CLAMP is the guarantee. If this test ever starts
    failing, the ladder was reordered — check that the clamp still holds before
    "fixing" it.
    """

    perm = _child(PermissionMode.AUTO_ACCEPT)
    result = await perm._on_tool_call(_write_event("src/app.py"), _FakeCtx(has_ui=False))  # type: ignore[arg-type]
    assert result is None


async def test_auto_accept_out_of_cwd_write_still_hits_the_floor() -> None:
    """The complement: branch (f) declines, so an AUTO_ACCEPT child falls to the floor.

    ``_is_auto_allowable_write`` rejects outside-cwd targets, so control DOES
    reach branch (d) and the child blocks instead of silently writing
    ``/etc/passwd`` — the case that would otherwise be a headless allow.
    """

    perm = _child(PermissionMode.AUTO_ACCEPT)
    result = await perm._on_tool_call(  # type: ignore[arg-type]
        _write_event("../../etc/passwd"), _FakeCtx(has_ui=False)
    )
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == _HEADLESS_BLOCK_REASON


async def test_auto_accept_child_cannot_write_dot_aelix() -> None:
    """OC-5 holding INSIDE a widened child — the escalation the floor alone misses.

    An ``auto-accept-edits`` child is exactly the posture a human may grant at
    the spawn-time consent dialog (ADR-0197 §(i)). Because ``.aelix`` is now a
    sensitive path component, branch (f) declines and the child hits the floor,
    so it cannot author the project identity / extension that a LATER parent run
    would execute under an ancestor ``trust.json``. This is the prerequisite
    that makes bounded widening safe to offer at all.
    """

    perm = _child(PermissionMode.AUTO_ACCEPT)
    result = await perm._on_tool_call(  # type: ignore[arg-type]
        _write_event(".aelix/agents/evil.md"), _FakeCtx(has_ui=False)
    )
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == _HEADLESS_BLOCK_REASON


# ============================================================
# The guardrail floor is still FIRST and still independent
# ============================================================


async def test_guardrail_still_first() -> None:
    """First-block-wins: the guardrail's hard deny beats the permission verdict.

    ``cli/entry.py`` prepends ``[GuardrailExtension(), permission, ...]`` and the
    kernel's ``_reducer_tool_call`` is sequential with a first-``block=True``
    short-circuit, so a catastrophic pattern is refused by the guardrail before
    the permission ladder is consulted at all — including in a YOLO child, the
    one posture that walks straight past the floor
    (:func:`test_headless_block_applies_under_yolo`). DO NOT REORDER.
    """

    guardrail = GuardrailExtension()
    perm = _child(PermissionMode.YOLO)
    ctx = _FakeCtx(has_ui=False)
    event = _bash_event("rm -rf /")

    first = guardrail._on_tool_call(event, ctx)  # type: ignore[arg-type]
    assert isinstance(first, ToolCallResult)
    assert first.block is True
    # The permission ext would have ALLOWED this call (YOLO, branch (e)); the
    # guardrail reason is what the model actually sees.
    assert await perm._on_tool_call(event, ctx) is None  # type: ignore[arg-type]
    assert first.reason != _HEADLESS_BLOCK_REASON
