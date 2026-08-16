"""``aelix_agents.aggregate`` — N envelopes → one ``ToolResult`` (ADR-0199 §3.4).

PURE tests: every envelope is hand-built, the wall clock is an argument, and the
outcome classification arrives from the caller — so the layout, the ``is_error``
rule, the usage roll-up and the three outcome classes are all pinned without a
process or a loop.

The three things these tests exist to stop, each a way a model concludes that
delegated work is finished when it is not:

1. a 7-of-8 batch rendered as a clean success;
2. a member that NEVER RAN rendered the same as one that ran and failed;
3. ``tokens`` summed instead of maxed, so the aggregate reports a context level
   several times the real one.
"""

from __future__ import annotations

import pytest
from aelix_agents.aggregate import (
    MemberOutcome,
    render_batch_result,
    roll_up_usage,
)
from aelix_agents.tool import render_subagent_result
from aelix_coding_agent.subagent_contract import SubagentResult, SubagentUsage


def _result(
    index: int = 1,
    *,
    ok: bool = True,
    status: str = "ok",
    summary: str = "done",
    **kwargs: object,
) -> SubagentResult:
    return SubagentResult(
        id=f"s{index}",
        profile="scout",
        ok=ok,
        status=status,  # type: ignore[arg-type]
        summary=summary,
        **kwargs,  # type: ignore[arg-type]
    )


def _ran(index: int = 1, **kwargs: object) -> MemberOutcome:
    return MemberOutcome.ran(_result(index, **kwargs))


def _refused(index: int = 1, summary: str = "too many live children") -> MemberOutcome:
    return MemberOutcome.never_started(
        _result(index, ok=False, status="error", summary=summary)
    )


def _text(members: list[MemberOutcome], **kwargs: object) -> str:
    defaults: dict[str, object] = {"not_run": 0, "wall_ms": 1000}
    defaults.update(kwargs)
    tool_result = render_batch_result(  # type: ignore[arg-type]
        "scout", "parallel", members, **defaults
    )
    return tool_result.content[0].text  # type: ignore[union-attr]


# --- MemberOutcome: the classification is the CALLER's, never re-derived -----


def test_the_three_outcome_classes() -> None:
    assert _ran(1).outcome == "ok"
    assert MemberOutcome.ran(_result(2, ok=False, status="error")).outcome == "failed"
    assert _refused(3).outcome == "did_not_start"


def test_started_has_no_default_so_the_caller_must_state_it() -> None:
    """§3.4: the executor decides at the point it CREATES the envelope.

    The rejected alternative is recognising a refusal by matching
    ``_TOO_MANY_LIVE`` / ``_BUDGET_EXHAUSTED`` against ``summary`` — which would
    couple this renderer to ``runtime.py``'s message strings and would
    misclassify a child that happened to echo one of them.
    """

    with pytest.raises(TypeError):
        MemberOutcome(_result(1))  # type: ignore[call-arg]


def test_a_member_that_never_started_cannot_be_ok() -> None:
    """The incoherent combination is refused at construction rather than
    rendered as "did not start" while counting toward the ok tally."""

    with pytest.raises(ValueError, match="cannot carry ok=True"):
        MemberOutcome.never_started(_result(1, ok=True))


def test_a_refused_member_that_echoes_a_runtime_string_is_still_a_real_failure() -> None:
    """The point of carrying ``started`` explicitly: a CHILD whose own summary
    quotes the live-cap message ran, failed, and must be counted as failed."""

    member = MemberOutcome.ran(
        _result(1, ok=False, status="error", summary="too many live children")
    )

    assert member.outcome == "failed"


# --- usage roll-up ----------------------------------------------------------


def test_roll_up_sums_the_counters_and_maxes_tokens() -> None:
    """``SubagentUsage.tokens`` is a context LEVEL, "last message wins"
    (``subagent_contract.py:95-96``), not a running total. Summing three
    children's context levels reports a number three times the real one — the
    mistake ``stream.py:214-217`` already warns about.
    """

    results = [
        _result(1, usage=SubagentUsage(input=100, output=10, tokens=5000, turns=2, cost=0.01)),
        _result(2, usage=SubagentUsage(input=200, output=20, tokens=9000, turns=3, cost=0.02)),
        _result(3, usage=SubagentUsage(input=300, output=30, tokens=1000, turns=1, cost=0.03)),
    ]

    total = roll_up_usage(results)

    assert total.input == 600
    assert total.output == 60
    assert total.turns == 6
    assert total.cost == pytest.approx(0.06)
    assert total.tokens == 9000  # max, NOT 15000


def test_roll_up_sums_the_cache_counters() -> None:
    results = [
        _result(1, usage=SubagentUsage(cache_read=7, cache_write=3)),
        _result(2, usage=SubagentUsage(cache_read=5, cache_write=1)),
    ]

    total = roll_up_usage(results)

    assert (total.cache_read, total.cache_write) == (12, 4)


def test_roll_up_of_nothing_is_all_zeroes() -> None:
    assert roll_up_usage([]) == SubagentUsage()


# --- is_error truth table ---------------------------------------------------


def test_is_error_is_false_only_when_every_member_is_ok() -> None:
    result = render_batch_result(
        "scout", "parallel", [_ran(1), _ran(2), _ran(3)], not_run=0, wall_ms=100
    )

    assert result.is_error is False


def test_one_failed_member_makes_the_whole_batch_an_error() -> None:
    """REJECTED: ``is_error = not any(m.ok)``. A 7-of-8 batch reported clean is
    exactly how a model concludes that delegated work is finished."""

    members = [
        _ran(1),
        MemberOutcome.ran(_result(2, ok=False, status="error")),
        _ran(3),
    ]

    assert render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1).is_error


def test_all_failed_is_an_error() -> None:
    members = [MemberOutcome.ran(_result(i, ok=False, status="error")) for i in (1, 2)]

    assert render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1).is_error


def test_a_did_not_start_member_makes_the_batch_an_error() -> None:
    assert render_batch_result(
        "scout", "parallel", [_ran(1), _refused(2)], not_run=0, wall_ms=1
    ).is_error


def test_a_batch_whose_body_says_NOT_RUN_is_never_reported_clean() -> None:
    """``not_run`` counts toward ``is_error``, not only the members' ``ok``.

    Without the ``not_run > 0`` clause this result reads
    ``… 4 tasks · 1 ok · 0 failed · 3 not run`` and
    ``Tasks 2-4 of 4 were NOT RUN`` while reporting ``is_error=False`` — the
    result contradicts its own text, and the model is told a partially executed
    batch finished. Unreachable from today's executor (``_run_parallel``
    hard-codes ``not_run=0``; a chain only leaves steps unrun after a member
    that was not ok), which is exactly why it needs a test rather than a
    reachability argument: the first caller that trims a batch on a deadline
    gets the coupling for free.
    """

    result = render_batch_result(
        "scout", "parallel", [_ran(1)], not_run=3, wall_ms=1000
    )

    assert "3 not run" in result.content[0].text  # type: ignore[union-attr]
    assert result.is_error is True


def test_a_one_member_batch_reduces_to_todays_single_mode_verdict() -> None:
    """``is_error = any(not m.ok)`` collapses to ``not result.ok`` at N == 1, so
    the strict reading changes nothing for the single-mode path it replaces."""

    assert not render_batch_result(
        "scout", "chain", [_ran(1)], not_run=0, wall_ms=1
    ).is_error
    assert render_batch_result(
        "scout", "chain", [MemberOutcome.ran(_result(1, ok=False, status="error"))],
        not_run=0,
        wall_ms=1,
    ).is_error


# --- the header -------------------------------------------------------------


def test_the_header_names_the_profile_the_mode_and_the_three_tallies() -> None:
    members = [_ran(1), _ran(2), _ran(3), MemberOutcome.ran(_result(4, ok=False, status="error"))]

    header = _text(members).splitlines()[0]

    assert header == "agent scout · parallel · 4 tasks · 3 ok · 1 failed"


def test_did_not_start_is_counted_separately_in_the_header() -> None:
    """§3.4: "the header counts all three"."""

    members = [_ran(i) for i in (1, 2, 3, 4)] + [_refused(i) for i in (5, 6, 7, 8)]

    header = _text(members).splitlines()[0]

    assert header == "agent scout · parallel · 8 tasks · 4 ok · 0 failed · 4 did not start"


def test_the_header_total_includes_the_steps_that_never_ran() -> None:
    """A chain of 5 that stopped at step 3 still reports 5 tasks — the model
    submitted five and must see all five accounted for."""

    members = [_ran(1), _ran(2), MemberOutcome.ran(_result(3, ok=False, status="error"))]

    text = render_batch_result(
        "scout", "chain", members, not_run=2, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert text.splitlines()[0] == "agent scout · chain · 5 tasks · 2 ok · 1 failed · 2 not run"


def test_the_unusual_classes_are_omitted_from_the_header_when_zero() -> None:
    """A header that always carried "0 did not start · 0 not run" would train the
    reader to skip the segment that matters."""

    header = _text([_ran(1)]).splitlines()[0]

    assert "did not start" not in header
    assert "not run" not in header


# --- member blocks ----------------------------------------------------------


def test_a_member_block_carries_the_position_the_status_and_the_usage_line() -> None:
    member = MemberOutcome.ran(
        _result(
            1,
            summary="found three callers",
            permission_mode="plan",
            elapsed_ms=12400,
            usage=SubagentUsage(input=900, output=120, cost=0.0031, turns=2),
        )
    )

    text = _text([member, _ran(2)])

    assert "[1/2 ok] found three callers" in text
    assert "[agent scout · ok · plan · 2 turns · 900 in / 120 out · $0.0031 · 12.4s]" in text


def test_a_failed_member_carries_its_error_on_its_own_line() -> None:
    member = MemberOutcome.ran(
        _result(1, ok=False, status="error", summary="partial work", error="exit 1")
    )

    block = _text([member]).split("\n\n")[1]

    assert block.splitlines()[0] == "[1/1 error] partial work"
    assert block.splitlines()[1] == "Error: exit 1"


def test_an_error_already_inside_the_summary_is_not_repeated() -> None:
    """Mirrors ``render_subagent_result`` (``tool.py:847-848``): the fallback
    chain often puts ``error_message`` INTO ``summary``, and printing it twice
    reads as two distinct failures."""

    member = MemberOutcome.ran(
        _result(1, ok=False, status="error", summary="model not found", error="model not found")
    )

    assert _text([member]).count("model not found") == 1


def test_an_empty_summary_falls_back_to_the_no_output_sentinel() -> None:
    """``envelope.NO_OUTPUT`` (``envelope.py:32``): an empty summary renders as a
    blank line and reads like a bug, whereas "(no output)" is a fact."""

    assert "[1/1 ok] (no output)" in _text([_ran(1, summary="")])


def test_dropped_tools_and_dropped_lines_reach_the_member_block() -> None:
    """A batch member must never say less about itself than the same child would
    say on the single-mode path (``tool.py:849-858``)."""

    member = MemberOutcome.ran(
        _result(1, dropped_tools=("bash", "edit"), dropped_lines=3)
    )

    text = _text([member])

    assert "Tools not granted to this agent: bash, edit" in text
    assert "3 oversize output line(s) were dropped." in text


def test_a_did_not_start_member_renders_under_its_own_heading_in_parallel() -> None:
    """§3.4: "failed" and "never started" are rendered differently in BOTH
    modes. A model that cannot tell them apart reports the work as done."""

    text = _text([_ran(1), _refused(2, "too many live children")])

    assert "[2/2 did not start] too many live children" in text
    assert "[2/2 error]" not in text


def test_a_did_not_start_member_renders_under_its_own_heading_in_chain() -> None:
    members = [_ran(1), _refused(2, "delegation budget exhausted")]

    text = render_batch_result(
        "scout", "chain", members, not_run=0, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "[2/2 did not start] delegation budget exhausted" in text


def test_a_did_not_start_members_usage_line_does_not_say_error() -> None:
    """The heading and the line beneath it must not disagree.

    Every refusal envelope carries ``status="error"`` — ``_refusal_envelope``
    (``batch.py:681-696``) and ``runtime._error_result`` both mint one that way —
    so a verbatim ``result.status`` prints ``[agent scout · error · 0.0s]``
    directly under ``[2/2 did not start] …``. That tells the reader in two
    consecutive lines that this member did and did not run, and "error" is the
    word a model acts on. Pinning the WHOLE line, because an assertion on the
    absence of "error" alone would also pass if the footer vanished.
    """

    block = _text([_ran(1), _refused(2)]).split("\n\n")[2]

    assert block.splitlines()[0] == "[2/2 did not start] too many live children"
    assert block.splitlines()[-1] == "[agent scout · did not start · 0.0s]"


def test_a_member_that_ran_still_reports_its_own_status_verbatim() -> None:
    """The other side of the override: it fires ONLY for ``started=False``.

    ``_usage_line``'s ``status`` argument defaults to ``None``, and a member that
    ran must go on reporting whatever the envelope said — including the case
    where a real child failed and "error" is the true word.
    """

    failed = MemberOutcome.ran(_result(1, ok=False, status="error", summary="boom"))

    assert "[agent scout · error · 0.0s]" in _text([failed])
    assert "did not start" not in _text([failed])


def test_both_renderers_emit_the_same_note_set_for_the_same_envelope() -> None:
    """A batch member must never say less about itself than the single path.

    ``_member_block`` mirrors ``render_subagent_result``'s notes by hand
    (``tool.py:846-858``), so a fourth note added to one and not the other is
    silent — the batch model would simply never hear about, say, ``truncated``.
    Compares the note lines themselves rather than counting them, so a note that
    is present in both but WORDED differently is caught too.
    """

    result = _result(
        1,
        ok=False,
        status="error",
        summary="partial work",
        error="exit 1",
        dropped_tools=("bash", "edit"),
        dropped_lines=3,
    )
    single = render_subagent_result(result).content[0].text  # type: ignore[union-attr]
    batch = _text([MemberOutcome.ran(result)])

    # Drop each renderer's own framing: the summary/heading it starts with and
    # the usage line it ends with. What is left is exactly the note set.
    single_notes = single.split("\n\n")[1:-1]
    batch_notes = batch.split("\n\n")[1].splitlines()[1:-1]

    assert single_notes == batch_notes
    assert single_notes == [
        "Error: exit 1",
        "Tools not granted to this agent: bash, edit (it holds no tool you do not hold).",
        "3 oversize output line(s) were dropped.",
    ]


def test_members_render_in_submitted_order() -> None:
    """§3.4: SUBMITTED order, never completion order. A transcript ordered by
    whichever child finished first is not reproducible between two runs of the
    same batch, and the model addressed the tasks by position.

    The elapsed times below are deliberately descending: sorting by any
    completion-derived key would reverse them.
    """

    members = [
        MemberOutcome.ran(_result(i, summary=f"answer {i}", elapsed_ms=(9 - i) * 1000))
        for i in (1, 2, 3, 4)
    ]

    text = _text(members)
    positions = [text.index(f"[{i}/4 ok] answer {i}") for i in (1, 2, 3, 4)]

    assert positions == sorted(positions)


# --- the steps that never ran -----------------------------------------------


def test_the_steps_that_never_ran_are_NAMED_not_merely_counted() -> None:
    """§3.3: "the aggregate carries every completed envelope, the failed one, and
    an explicit line naming the steps that were NOT run". A count alone does not
    tell the model WHICH positions produced nothing, and it addressed the tasks
    by position.
    """

    members = [_ran(1), _ran(2), MemberOutcome.ran(_result(3, ok=False, status="error"))]

    text = render_batch_result(
        "scout", "chain", members, not_run=2, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "Steps 4-5 of 5 were NOT RUN — the chain stopped at the first failure." in text


def test_a_single_step_that_never_ran_reads_in_the_singular() -> None:
    members = [_ran(1), MemberOutcome.ran(_result(2, ok=False, status="error"))]

    text = render_batch_result(
        "scout", "chain", members, not_run=1, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "Step 3 of 3 was NOT RUN — the chain stopped at the first failure." in text


def test_no_not_run_line_when_every_step_ran() -> None:
    text = render_batch_result(
        "scout", "chain", [_ran(1), _ran(2)], not_run=0, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "NOT RUN" not in text


def test_the_not_run_line_does_not_blame_a_failure_that_never_happened() -> None:
    """A chain has TWO ways to stop and only one of them is a failure.

    The reachable one: step 2's task renders past ``MAX_TASK_BYTES`` once
    ``{previous}`` is substituted, so ``_run_chain`` appends a ``never_started``
    member and breaks (``batch.py:379-386``) — no child, no failure. With the
    reason hard-coded, the same result then says ``0 failed`` in its header,
    ``did not start`` in step 2's heading, and "the chain stopped at the first
    failure" in its last line. Three sentences about one batch, two of which
    contradict the third.
    """

    members = [_ran(1), _refused(2, "rendered step is 102760 bytes")]

    text = render_batch_result(
        "scout", "chain", members, not_run=1, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "Step 3 of 3 was NOT RUN — the chain stopped when step 2 could not be started." in text
    assert "first failure" not in text


def test_a_chain_that_really_did_fail_still_says_so() -> None:
    """The negative half of the rule above — the common case is unchanged."""

    members = [_ran(1), MemberOutcome.ran(_result(2, ok=False, status="error"))]

    text = render_batch_result(
        "scout", "chain", members, not_run=1, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "Step 3 of 3 was NOT RUN — the chain stopped at the first failure." in text


@pytest.mark.parametrize(
    ("members", "expected"),
    [
        ([], "Steps 1-2 of 2 were NOT RUN — the chain never started."),
        ([_ran(1)], "Step 2 of 2 was NOT RUN — the chain stopped after step 1."),
    ],
)
def test_the_two_shapes_the_executor_does_not_produce_still_read_truthfully(
    members: list[MemberOutcome], expected: str
) -> None:
    """The defensive branches, pinned so neither can rot into a lie.

    Neither is reachable from ``_run_chain`` today — it appends a member per
    step and breaks only on a member that was not ok — but ``render_batch_result``
    is a public function whose ``not_run`` is a first-class parameter, and both
    shapes are one future feature away (a batch refused before its first step; a
    deadline trim after a step that succeeded). The empty case additionally pins
    that reading the reason off ``members[-1]`` does not ``IndexError``.
    """

    text = render_batch_result(
        "scout", "chain", members, not_run=2 - len(members), wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert expected in text
    assert "failure" not in text


def test_a_parallel_batch_never_blames_a_chain_stop() -> None:
    """Parallel submits everything it is given, so unrun tasks were TRIMMED.

    Reading the reason off the last member must not leak the chain vocabulary
    into the mode that has no ordering at all.
    """

    text = _text([_ran(1), _refused(2)], not_run=2)

    assert "Tasks 3-4 of 4 were NOT RUN — they were never submitted." in text


def test_never_started_and_never_run_are_different_lines() -> None:
    """The two facts a model must not conflate: a ``did_not_start`` member HAS an
    envelope explaining its refusal; a not-run step has none at all."""

    members = [_ran(1), _refused(2), MemberOutcome.ran(_result(3, ok=False, status="error"))]

    text = render_batch_result(
        "scout", "chain", members, not_run=1, wall_ms=1
    ).content[0].text  # type: ignore[union-attr]

    assert "[2/4 did not start]" in text
    assert "Step 4 of 4 was NOT RUN" in text
    assert text.splitlines()[0].endswith("4 tasks · 1 ok · 1 failed · 1 did not start · 1 not run")


# --- the total line ---------------------------------------------------------


def test_the_total_line_reports_the_executors_wall_clock() -> None:
    """§3.4: the executor's OWN measured wall clock, not a sum and not a max of
    the members. A sum lies about parallel (four children in 12 s are not 48 s of
    user time) and a max lies about chain."""

    members = [
        MemberOutcome.ran(
            _result(i, elapsed_ms=30000, usage=SubagentUsage(input=900, output=120, cost=0.0026))
        )
        for i in (1, 2, 3, 4)
    ]

    line = _text(members, wall_ms=41200).splitlines()[-1]

    assert line == "[total] 4 tasks · 4 ok · 0 failed · 3.6k in / 480 out · $0.0104 · 41.2s wall"


def test_the_total_line_omits_usage_that_is_entirely_absent() -> None:
    line = _text([_ran(1)], wall_ms=2500).splitlines()[-1]

    assert line == "[total] 1 tasks · 1 ok · 0 failed · 2.5s wall"


def test_token_counts_below_a_thousand_are_not_abbreviated() -> None:
    """The threshold mirrors ``progress._format_tokens`` (``progress.py:147-150``)
    so a batch total and a statusline row never disagree about how a number is
    spelled."""

    member = MemberOutcome.ran(_result(1, usage=SubagentUsage(input=999, output=1000)))

    assert "999 in / 1.0k out" in _text([member])


# --- details ----------------------------------------------------------------


def test_details_are_joined_and_labelled_by_position() -> None:
    """UNCAPPED, exactly as on the single-mode path: ``details`` rides
    ``ToolResult.details`` and is not sent to the model, so the truncation marker
    inside each ``summary`` keeps its promise."""

    members = [
        MemberOutcome.ran(_result(1, details="full text one")),
        MemberOutcome.ran(_result(2, details="full text two")),
    ]

    result = render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1)

    assert result.details == (
        "--- [1/2] scout ---\nfull text one\n\n--- [2/2] scout ---\nfull text two"
    )


def test_empty_details_are_omitted_and_keep_the_positions_of_the_others() -> None:
    members = [_ran(1), MemberOutcome.ran(_result(2, details="only mine"))]

    result = render_batch_result("scout", "parallel", members, not_run=0, wall_ms=1)

    assert result.details == "--- [2/2] scout ---\nonly mine"


def test_details_is_none_when_every_member_had_none() -> None:
    """What ``render_subagent_result`` passes when a single child had nothing
    (``tool.py:862``)."""

    result = render_batch_result(
        "scout", "parallel", [_ran(1), _ran(2)], not_run=0, wall_ms=1
    )

    assert result.details is None


# --- the whole layout -------------------------------------------------------


def test_the_rendered_batch_matches_the_layout_in_the_plan() -> None:
    """§3.4's worked example, end to end, so a drift in any one helper is caught
    as a shape change rather than only as a failed substring search."""

    members = [
        MemberOutcome.ran(
            _result(
                1,
                summary="first answer",
                permission_mode="plan",
                elapsed_ms=12400,
                usage=SubagentUsage(input=900, output=120, cost=0.0031, turns=2),
            )
        ),
        MemberOutcome.ran(
            _result(
                2,
                ok=False,
                status="error",
                summary="second answer",
                error="child exited 1",
                permission_mode="plan",
                elapsed_ms=4000,
            )
        ),
    ]

    text = _text(members, wall_ms=16400)

    assert text == (
        "agent scout · parallel · 2 tasks · 1 ok · 1 failed\n"
        "\n"
        "[1/2 ok] first answer\n"
        "[agent scout · ok · plan · 2 turns · 900 in / 120 out · $0.0031 · 12.4s]\n"
        "\n"
        "[2/2 error] second answer\n"
        "Error: child exited 1\n"
        "[agent scout · error · plan · 4.0s]\n"
        "\n"
        "[total] 2 tasks · 1 ok · 1 failed · 900 in / 120 out · $0.0031 · 16.4s wall"
    )
