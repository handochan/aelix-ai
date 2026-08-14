"""Issue #161 shape 3 — the approval dialog can redirect an extension write.

WHY THIS EXISTS. Shapes 1 and 2 were built and measured against a real model:

- **Shape 1** (the system prompt asks): complied in 1 of 4 interactive runs, and
  the variable was how the USER phrased the request — "Please add it" got an ask
  out of the strongest wording, "Do the work." did not.
- **Shape 2** (`/extension new <name>`): shipped, and works when a human runs it.
  Pointing the model at it, so it hands the choice over instead of writing, was
  measured at **0 of 2**.

What every one of those runs DID do is stop at the permission dialog with the
chosen path on screen. So the user already sees the path; what they cannot do is
answer anything but yes or no. This adds the third answer.

THE PREMISE THE ISSUE GOT HALF WRONG. #161 says the permission layer "is
allow/deny only, so it cannot offer 'write here instead'". True of
``ToolCallResult`` — it has ``block`` and ``reason``. False of the mechanism:
``ToolCallHookEvent.args`` is *the same dict* the loop passes to
``tool.execute``, so rewriting the path there redirects the write with no kernel
change at all. ``test_the_redirect_reaches_the_real_write_tool`` is the proof,
driven through the loop's own bridge rather than by calling the tool directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.tui.approval_dialog import (
    ApprovalDecision,
    ApprovalRequest,
    build_options_view,
    rows_for,
)


class _Ctx:
    """The two attributes the permission hook reads off its context."""

    def __init__(self, cwd: Path, ui: Any = None) -> None:
        self.cwd = str(cwd)
        self.has_ui = ui is not None
        self.ui = ui


class _Ui:
    def __init__(self, answer: int | None) -> None:
        self.answer = answer
        self.offered: list[list[str]] = []

    async def select(self, _title: str, options: list[str]) -> str | None:
        self.offered.append(list(options))
        return None if self.answer is None else options[self.answer]


def _event(path: Path) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="write",
        args={"path": str(path), "content": "print('hi')\n"},
        assistant_message=None,  # type: ignore[arg-type]
        context=None,  # type: ignore[arg-type]
    )


@pytest.fixture()
def tiers(tmp_path, monkeypatch):
    """The two real extension tiers, isolated."""

    agent_dir = tmp_path / "agent"
    project = tmp_path / "proj"
    (agent_dir / "extensions").mkdir(parents=True)
    (project / ".aelix" / "extensions").mkdir(parents=True)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    return project, agent_dir / "extensions", project / ".aelix" / "extensions"


# --- which writes get the extra answer ---------------------------------------


def test_the_two_tiers_are_recognised_and_named_by_consequence(tiers) -> None:
    """The labels say what the choice MEANS, not just where the file goes.

    The project tier is trust-gated and fails SILENTLY when the project is
    untrusted; the global tier is not gated at all. Neither path name says so,
    and that asymmetry is the entire reason this question is worth asking.
    """

    from aelix_coding_agent.builtin.permission import _extension_redirect

    project, global_dir, project_dir = tiers

    chosen, other, other_path = _extension_redirect(
        {"path": str(global_dir / "x.py")}, str(project)
    )
    assert "user global" in chosen
    assert "every project" in chosen
    assert "Only this project" in other
    assert other_path == str(project_dir / "x.py")

    # Symmetric: pick the project tier and the alternative is the global one.
    chosen, other, other_path = _extension_redirect(
        {"path": str(project_dir / "x.py")}, str(project)
    )
    assert "Only this project" in chosen
    assert "user global" in other
    assert other_path == str(global_dir / "x.py")


@pytest.mark.parametrize(
    "relative",
    ["src/main.py", ".aelix/skills/s/SKILL.md", ".aelix/extensions/sub/deep.py"],
    ids=["ordinary-source", "another-aelix-dir", "nested-below-the-tier"],
)
def test_an_ordinary_write_gets_no_extra_answer(tiers, relative) -> None:
    """There are exactly TWO targets, and this is not a free-form "move it".

    The nested case is the one worth having: a file two levels under the
    extensions directory is not in the tier — the loader scans the directory
    itself — so offering to "move" it would invent a destination.
    """

    from aelix_coding_agent.builtin.permission import _extension_redirect

    project, _global_dir, _project_dir = tiers
    assert _extension_redirect({"path": str(project / relative)}, str(project)) is None


def test_a_write_with_no_path_is_not_redirected(tiers) -> None:
    from aelix_coding_agent.builtin.permission import _extension_redirect

    project, _g, _p = tiers
    assert _extension_redirect({"content": "x"}, str(project)) is None


# --- the dialog rows ----------------------------------------------------------


def test_an_ordinary_approval_keeps_the_three_static_rows() -> None:
    """Every approval that is not an extension write is byte-for-byte unchanged."""

    rows = rows_for(ApprovalRequest(tool_name="bash", kind="bash"))
    assert [label for _d, _m, label in rows] == [
        "Yes",
        "Yes, for this session",
        "No",
    ]
    view = build_options_view(0, rows)
    assert view[-1] == (
        "  ↑/↓ to move · 1-3 / y·s·n · Enter to confirm · Esc to deny"
    )


def test_the_redirect_row_sits_above_no_and_the_hint_follows_it() -> None:
    """It is a second way to say YES, so it must not read as a way to decline.

    The hint line is derived from the rows: the first revision of this change
    left it saying "1-3 / y·s·n" under a four-row dialog, which is the
    stale-prose defect this whole batch exists to remove.
    """

    request = ApprovalRequest(
        tool_name="write",
        kind="write",
        yes_label="Yes — user global, every project (/g/x.py)",
        redirect_label="Only this project (/p/x.py)",
    )
    rows = rows_for(request)
    decisions = [d for d, _m, _l in rows]
    assert decisions == [
        ApprovalDecision.YES,
        ApprovalDecision.YES_SESSION,
        ApprovalDecision.REDIRECT,
        ApprovalDecision.NO,
    ]
    view = build_options_view(0, rows)
    assert "1. [y] Yes — user global" in view[0]
    assert "3. [p] Only this project" in view[2]
    assert "4. [n] No" in view[3]
    assert view[-1] == (
        "  ↑/↓ to move · 1-4 / y·s·p·n · Enter to confirm · Esc to deny"
    )


# --- the generic ctx.ui fallback ----------------------------------------------


async def test_the_generic_prompt_offers_and_performs_the_redirect(tiers) -> None:
    project, global_dir, project_dir = tiers
    ext = PermissionExtension()
    ui = _Ui(answer=2)  # index 2 = the inserted redirect option
    event = _event(global_dir / "x.py")

    result = await ext._prompt(event, _Ctx(project, ui))  # type: ignore[arg-type]

    assert result is None, "a redirect ALLOWS the write; it is not a denial"
    assert event.args["path"] == str(project_dir / "x.py")
    assert "Only this project" in ui.offered[0][2]


async def test_the_generic_prompt_is_unchanged_for_an_ordinary_write(tiers) -> None:
    project, _g, _p = tiers
    ext = PermissionExtension()
    ui = _Ui(answer=0)
    event = _event(project / "src.py")

    await ext._prompt(event, _Ctx(project, ui))  # type: ignore[arg-type]

    assert ui.offered[0] == ["Yes", "Yes, for this session", "No", "No, provide reason"]


async def test_declining_after_a_redirect_is_offered_still_denies(tiers) -> None:
    project, global_dir, project_dir = tiers
    ext = PermissionExtension()
    ui = _Ui(answer=3)  # "No", now at index 3
    event = _event(global_dir / "x.py")

    result = await ext._prompt(event, _Ctx(project, ui))  # type: ignore[arg-type]

    assert result is not None and result.block
    assert event.args["path"] == str(global_dir / "x.py"), "a denial must not move it"


# --- the purpose-built dialog path -------------------------------------------


async def test_the_dialog_decision_performs_the_redirect(tiers) -> None:
    project, global_dir, project_dir = tiers
    seen: list[ApprovalRequest] = []

    async def _runner(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return ApprovalDecision.REDIRECT

    ext = PermissionExtension(approval_runner=_runner)
    event = _event(global_dir / "x.py")

    result = await ext._prompt(event, _Ctx(project, ui=object()))  # type: ignore[arg-type]

    assert result is None
    assert event.args["path"] == str(project_dir / "x.py")
    assert seen[0].redirect_label and "Only this project" in seen[0].redirect_label
    assert seen[0].yes_label and "user global" in seen[0].yes_label


async def test_a_redirect_decision_the_dialog_never_offered_is_denied(tiers) -> None:
    """A host that hands back REDIRECT for an ordinary write gets a denial.

    Allowing it would mean inventing a destination; allowing it *unchanged*
    would mean the user's answer silently became "yes to the original path",
    which is the worse of the two.
    """

    async def _runner(_request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.REDIRECT

    project, _g, _p = tiers
    ext = PermissionExtension(approval_runner=_runner)
    event = _event(project / "src.py")

    result = await ext._prompt(event, _Ctx(project, ui=object()))  # type: ignore[arg-type]

    assert result is not None and result.block


async def test_a_redirect_leaves_no_standing_session_rule(tiers) -> None:
    """"Put THIS one somewhere else" is not "always allow this tier"."""

    async def _runner(_request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.REDIRECT

    project, global_dir, _p = tiers
    ext = PermissionExtension(approval_runner=_runner)
    await ext._prompt(_event(global_dir / "x.py"), _Ctx(project, ui=object()))  # type: ignore[arg-type]

    assert ext._session_allows == set()


# --- the claim the whole design rests on --------------------------------------


async def test_the_redirect_reaches_the_real_write_tool() -> None:
    """#161's premise, tested: mutating args in the hook redirects the write.

    Driven through ``AgentHarness._before_tool_call_bridge`` — the loop's own
    callback→hook bridge — rather than by calling the tool directly, because
    the claim is about what the LOOP passes on, not about dict aliasing. If the
    bridge ever takes a defensive copy this test goes red and shape 3 is dead.
    """

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.types import AgentContext, BeforeToolCallContext
    from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
    from aelix_ai.streaming import Model
    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.tools import create_all_tools

    with tempfile.TemporaryDirectory() as td:
        cwd = Path(td)
        (cwd / "a").mkdir()
        (cwd / "b").mkdir()
        chosen, other = cwd / "a" / "ext.py", cwd / "b" / "ext.py"

        tools = list(create_all_tools(str(cwd)).values())
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="probe", provider="probe"), tools=tools, cwd=str(cwd)
            )
        )
        harness.hooks.on(
            "tool_call",
            lambda e, _c: e.args.__setitem__("path", str(other)),
        )

        args = {"path": str(chosen), "content": "print('hi')\n"}
        blocked = await harness._before_tool_call_bridge(
            BeforeToolCallContext(
                tool_call=ToolCallContent(
                    tool_call_id="t1", tool_name="write", input=dict(args)
                ),
                args=args,
                assistant_message=AssistantMessage(content=[TextContent(text="")]),
                context=AgentContext(),
            )
        )
        assert blocked is None
        write = next(t for t in tools if t.name == "write")
        await write.execute(args, ToolExecutionContext())

        assert other.exists(), "the redirect did not reach the tool"
        assert not chosen.exists(), "the original path was written anyway"
