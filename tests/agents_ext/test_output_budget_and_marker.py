"""#199 — the truncation marker tells the truth, and a batch shares one budget.

Two decisions of the #199 design, both about what reaches the parent's context:

* A.5 — "Full output preserved in tool details" was false the moment a call
  returned (``details`` is never persisted, #168). The marker now names the
  delegated session file when — and only when — the rest of the text is in it,
  and otherwise claims nothing. The exact wording is pinned here.
* A.9, owner decision (c) of 2026-09-19 — single mode keeps the profile's
  ``output_cap`` (51 200 bytes); a parallel or chain call shares ONE 64 KiB
  budget split evenly across its members, applied where the batch is RENDERED,
  so the chain's ``{previous}`` hand-off still gets each step's own summary.
  A member's share is everything it says — its summary AND its ``Error:``
  note — so the size asserted below is the whole tool result's, in UTF-8 bytes.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from aelix_agents.aggregate import (
    BATCH_OUTPUT_BUDGET_BYTES,
    MemberOutcome,
    member_output_budget,
    render_batch_result,
)
from aelix_agents.batch import run_batch
from aelix_agents.consent import SpawnGrant
from aelix_agents.envelope import _marker, build_result, cap_summary, recap_summary
from aelix_agents.print_channel import RunningChild, SpawnPlan
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_agents.stream import _StreamState
from aelix_agents.tool import AgentCall, render_subagent_result
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile, SubagentResult

_RECORDED = " The full output is recorded in the delegated session."


def _text(result: ToolResult) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def _member(summary: str, *, truncated: bool = False, recorded: bool = True, **kw: Any) -> MemberOutcome:
    return MemberOutcome.ran(
        SubagentResult(
            id="sub-x",
            profile="scout",
            ok=True,
            status="ok",
            summary=summary,
            truncated=truncated,
            output_recorded=recorded,
            **kw,
        )
    )


# === A.5 — the marker =========================================================


def test_the_marker_names_the_delegated_session_only_when_it_is_true() -> None:
    claimed, truncated, omitted = cap_summary("x" * 100, 10, recorded=True)
    assert (truncated, omitted) == (True, 90)
    assert claimed == "x" * 10 + (
        "\n\n[Output truncated: 90 bytes omitted. "
        "The full output is recorded in the delegated session.]"
    )

    plain, _t, _o = cap_summary("x" * 100, 10)
    assert plain == "x" * 10 + "\n\n[Output truncated: 90 bytes omitted.]"

    for text in (claimed, plain):
        assert "tool details" not in text, "details are never persisted (#168)"
        assert "/" not in text and "\\" not in text, "no path in the model's context"


def _state(**kw: Any) -> _StreamState:
    state = _StreamState(saw_agent_start=True, saw_agent_end=True, stop_reason="end_turn")
    for key, value in kw.items():
        setattr(state, key, value)
    return state


def test_the_envelope_claims_the_file_only_for_the_childs_own_stream() -> None:
    answer = build_result(
        id="sub-a", profile="scout", state=_state(summary="a" * 200), exit_code=0,
        output_cap=50, session_recorded=True,
    )
    assert answer.output_recorded is True and answer.truncated is True
    assert answer.summary.endswith(_RECORDED + "]")

    unrecorded = build_result(
        id="sub-b", profile="scout", state=_state(summary="a" * 200), exit_code=0,
        output_cap=50, session_recorded=False,
    )
    assert unrecorded.output_recorded is False
    assert unrecorded.summary.endswith("bytes omitted.]")

    # A child that never got as far as its first turn: the summary is its
    # STDERR, which is not in the child file (only header + origin are).
    early_exit = build_result(
        id="sub-c", profile="scout", state=_StreamState(), outcome="ok", exit_code=1,
        stderr_tail="No API key found for provider nope.\n" * 10, output_cap=50,
        session_recorded=True,
    )
    assert early_exit.ok is False
    assert early_exit.output_recorded is False
    assert _RECORDED not in early_exit.summary

    # The child's OWN error message is in its file (its harness wrote it).
    own_error = build_result(
        id="sub-d", profile="scout",
        state=_state(stop_reason="error", error_message="e" * 200), exit_code=0,
        output_cap=50, session_recorded=True,
    )
    assert own_error.output_recorded is True
    assert own_error.summary.endswith(_RECORDED + "]")


def test_a_recap_counts_everything_the_member_lost() -> None:
    """Re-capping an already-capped summary adds the two omitted counts."""

    channel_capped, _t, first = cap_summary("y" * 52_200, 51_200, recorded=True)
    assert first == 1_000
    text, truncated, omitted = recap_summary(channel_capped, 8_192, truncated=True, recorded=True)
    assert truncated is True
    assert omitted == 1_000 + (51_200 - 8_192)
    assert text == "y" * 8_192 + (
        f"\n\n[Output truncated: {omitted} bytes omitted.{_RECORDED}]"
    )


def test_a_summary_under_its_share_is_untouched_marker_and_all() -> None:
    capped, _t, _o = cap_summary("z" * 6_000, 5_000)
    assert recap_summary(capped, 8_192, truncated=True) == (capped, True, 1_000)
    assert recap_summary("short", 8_192, truncated=False) == ("short", False, 0)


def test_a_child_that_echoes_a_marker_is_not_parsed_as_one() -> None:
    """Only a summary the ENVELOPE says it truncated is read back."""

    echo = "x" * 10_000 + "\n\n[Output truncated: 999999 bytes omitted.]"
    _text_, _t, omitted = recap_summary(echo, 8_192, truncated=False)
    assert omitted == len(echo.encode()) - 8_192


# === A.9 (c) — one budget per batch call ======================================


def test_the_budget_is_sixty_four_kib_split_evenly() -> None:
    assert BATCH_OUTPUT_BUDGET_BYTES == 64 * 1024
    assert member_output_budget(8) == 8_192
    assert member_output_budget(1) == BATCH_OUTPUT_BUDGET_BYTES
    assert member_output_budget(0) == BATCH_OUTPUT_BUDGET_BYTES
    assert member_output_budget(10**9) == 1, "never 0, which would mean no cap"


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


_NOTE_FRAME = len("\nError: ")
_MARKER_MAX = _bytes(_marker(10**12, recorded=True))


def _frame(members: list[MemberOutcome]) -> int:
    """What the call renders AROUND its members' words, measured independently.

    The same call with every summary one byte long and no error, minus those
    bytes, plus what each member may add on top of its share: the ``Error: ``
    prefix and two truncation markers (the summary's and the note's), both
    appended after the cut by design (``envelope.cap_summary``).
    """

    bare = [
        MemberOutcome.ran(
            dataclasses.replace(m.result, summary="x", error=None, truncated=False)
        )
        for m in members
    ]
    text = _text(render_batch_result("scout", "parallel", bare, not_run=0, wall_ms=1))
    return _bytes(text) - len(bare) + len(bare) * (_NOTE_FRAME + 2 * _MARKER_MAX)


def test_an_eight_member_batch_hands_the_parent_at_most_its_budget() -> None:
    """The critique's worst case was 8 x 51 200 bytes ~ 410 KB in one result.

    Measured on the WHOLE tool result, in UTF-8 bytes: the members' words take
    at most the 64 KiB, and nothing else in the result is theirs.
    """

    members = [_member("x" * 20_000) for _ in range(8)]
    text = _text(render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1))
    assert text.count("x") == 8 * 8_192
    assert text.count(_RECORDED) == 8
    assert f"{20_000 - 8_192} bytes omitted" in text
    assert _bytes(text) <= BATCH_OUTPUT_BUDGET_BYTES + _frame(members)


def test_the_error_note_is_asked_of_the_uncapped_summary() -> None:
    """An error the budget cut out of the body must not come back in full."""

    long_error = "E" * 20_000
    member = MemberOutcome.ran(
        SubagentResult(
            id="sub-e", profile="scout", ok=False, status="error",
            summary=long_error, error=long_error,
        )
    )
    text = _text(render_batch_result("scout", "parallel", [member] * 8, not_run=0, wall_ms=1))
    assert "Error: " not in text
    assert text.count("E") <= 8 * 8_192 + 8 * len("Error")


def _failed_child(error_bytes: int) -> SubagentResult:
    """A child that failed with its own long error message, through the real
    envelope: its summary IS that message, cut at ``output_cap``."""

    return build_result(
        id="sub-f", profile="scout",
        state=_state(stop_reason="error", error_message="E" * error_bytes),
        exit_code=0, session_recorded=True,
    )


def test_an_error_the_summary_is_a_cut_of_is_not_repeated_in_either_mode() -> None:
    """Measured before the fix: 111 336 bytes for one delegation and 546 832
    for eight, because an error longer than the cap is never "in" the summary
    it began, and came back in full as a note."""

    failed = _failed_child(60_000)
    assert failed.truncated is True and failed.error == "E" * 60_000

    single = _text(render_subagent_result(failed))
    assert "Error: " not in single
    assert single.count("E") == 51_200

    members = [MemberOutcome.ran(failed) for _ in range(8)]
    batch = _text(render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1))
    assert "Error: " not in batch
    assert batch.count("E") == 8 * 8_192
    assert f"{60_000 - 8_192} bytes omitted" in batch, "the whole shortfall is told"
    assert _bytes(batch) <= BATCH_OUTPUT_BUDGET_BYTES + _frame(members)


def test_a_distinct_error_comes_out_of_the_members_own_share() -> None:
    """An error the summary does not say — here 30 000 bytes of multibyte text
    beside a 20 000-byte summary — is kept, but inside the member's share: at
    most half of it, and the summary gets the rest. Before, the note was added
    uncapped, and eight of these made one call return 226 400 bytes."""

    members = [
        MemberOutcome.ran(
            SubagentResult(
                id="sub-d", profile="scout", ok=False, status="error",
                summary="q" * 20_000, error="오" * 10_000,
            )
        )
        for _ in range(8)
    ]
    text = _text(render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1))

    assert text.count("Error: ") == 8, "the error is still said"
    share = member_output_budget(8)
    note_bytes = text.count("오") * 3
    assert note_bytes <= 8 * (share // 2)
    assert text.count("q") + note_bytes <= BATCH_OUTPUT_BUDGET_BYTES
    assert _bytes(text) <= BATCH_OUTPUT_BUDGET_BYTES + _frame(members)


def test_a_short_distinct_error_is_still_a_note() -> None:
    result = SubagentResult(
        id="sub-n", profile="scout", ok=False, status="error",
        summary="partial answer", error="spawn failed: no such file",
    )
    assert "Error: spawn failed: no such file" in _text(render_subagent_result(result))
    batch = _text(
        render_batch_result("scout", "parallel", [MemberOutcome.ran(result)], not_run=0, wall_ms=1)
    )
    assert "Error: spawn failed: no such file" in batch

    # Only a TRUNCATED summary is read as a cut of the error: an uncut one that
    # merely begins it still gets the note with the rest.
    begins = dataclasses.replace(result, summary="spawn failed")
    assert "Error: spawn failed: no such file" in _text(render_subagent_result(begins))


def test_a_lone_surrogate_is_capped_rather_than_raised() -> None:
    """A child's JSON can carry one, and so can an ``OSError`` naming an
    undecodable path. The cap counts it as the three bytes it takes."""

    text, truncated, omitted = cap_summary("a\ud83d" * 10, 8)
    assert truncated is True and text.startswith("a\ud83da\ud83d")
    assert omitted == 40 - 8
    envelope = build_result(
        id="sub-s", profile="scout", state=_state(summary="x\ud83d" * 1_000),
        exit_code=0, output_cap=100,
    )
    assert envelope.ok is True and envelope.truncated is True


def test_a_single_delegation_keeps_the_profile_cap() -> None:
    body = "s" * 40_000
    result = SubagentResult(id="sub-s", profile="scout", ok=True, status="ok", summary=body)
    assert body in _text(render_subagent_result(result))


class _Scripted:
    def __init__(self, summaries: list[str]) -> None:
        self._summaries = list(summaries)
        self.plans: list[SpawnPlan] = []

    async def run(self, plan: SpawnPlan, *, child: RunningChild | None = None, on_stream: Any = None) -> SubagentResult:
        self.plans.append(plan)
        if child is not None:
            child.state = "done"
        return SubagentResult(
            id=plan.id, profile="scout", ok=True, status="ok", summary=self._summaries.pop(0)
        )


async def test_the_chain_hand_off_keeps_each_steps_own_summary(tmp_path: Path) -> None:
    """The budget is a RENDER-time cap: step 2 receives step 1's 30 000 bytes."""

    profile = AgentProfile(
        name="scout", description="d", body="b", file_path=str(tmp_path / "s.md"), scope="user"
    )
    resolved = ResolvedProfile(name="scout", profile=profile, source_path=profile.file_path, scope="user")
    channel = _Scripted(["q" * 30_000, "second", "third"])
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: str(tmp_path)), channel=channel)
    outcome = await run_batch(
        runtime=runtime,
        grant=SpawnGrant(
            profile="scout", source_path=profile.file_path, scope="user",
            mode=PermissionMode.PLAN, widened=False, consented=True,
        ),
        resolved=resolved,
        call=AgentCall(profile="scout", tasks=("one", "two {previous}", "three"), mode="chain"),
        cwd=str(tmp_path),
        has_ui=lambda: False,
        posture=lambda: PermissionMode.DEFAULT,
    )

    assert "q" * 30_000 in channel.plans[1].task, "the hand-off is not re-capped"
    text = _text(
        render_batch_result(
            "scout", "chain", outcome.members, not_run=outcome.not_run, wall_ms=outcome.wall_ms
        )
    )
    share = member_output_budget(3)
    assert text.count("q") == share
    assert f"{30_000 - share} bytes omitted.]" in text, "no child file here, so no claim"
