"""#199 / ADR-0243 — tool-reported usage (``aelix.usage`` records) in session stats.

A tool that runs work outside the session's own model calls — today the
``agent`` tool's delegated children — records what that work spent as
``aelix.usage`` custom entries (design ``.omc/specs/199-design-2026-09-19.md``
§A.3). The kernel folds them (§A.4). Every record in this file is built BY HAND
in exactly the §A.3 shape, so these tests pin the reader's contract rather than
whatever a writer happens to produce:

- pending — ``{"v": 1, "key": k, "state": "pending"}``
- final   — ``{"v": 1, "key": k, "state": "final", "usage": {"input", "output",
  "cache_read", "cache_write", "cost"}, "cost_known": bool}``

Four groups: the fold itself; how it merges into :class:`SessionStats` (and
stays off the RPC wire); ``aelix_agents.aggregate.roll_up_usage`` agreeing with
it on the flows; and the branch semantics through a real harness — one branch
read, compaction, ``/tree`` moves and ``/fork``.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness._session_stats import (
    USAGE_RECORD_TYPE,
    SessionStats,
    ToolUsage,
    aggregate_session_stats,
    fold_usage_records,
)
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

# -- records, built by hand ---------------------------------------------------


def _pending(key: str) -> dict[str, Any]:
    return {"v": 1, "key": key, "state": "pending"}


def _final(
    key: str,
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: float = 0.0,
    cost_known: bool = True,
) -> dict[str, Any]:
    return {
        "v": 1,
        "key": key,
        "state": "final",
        "usage": {
            "input": input,
            "output": output,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "cost": cost,
        },
        "cost_known": cost_known,
    }


# === 1. the fold =============================================================


def test_the_record_type_is_the_design_name() -> None:
    assert USAGE_RECORD_TYPE == "aelix.usage"


def test_no_records_is_a_known_zero() -> None:
    folded = fold_usage_records([])
    assert folded == ToolUsage()
    assert folded.cost_known is True
    assert folded.runs == 0 and folded.pending == 0


def test_the_exact_design_records_fold_as_one_settled_run() -> None:
    """The two §A.3 records for one key, written out literally."""

    folded = fold_usage_records(
        [
            {"v": 1, "key": "sub-0123456789ab", "state": "pending"},
            {
                "v": 1,
                "key": "sub-0123456789ab",
                "state": "final",
                "usage": {
                    "input": 1200,
                    "output": 340,
                    "cache_read": 50,
                    "cache_write": 7,
                    "cost": 0.0123,
                },
                "cost_known": True,
            },
        ]
    )
    assert folded.tokens.input == 1200
    assert folded.tokens.output == 340
    assert folded.tokens.cache_read == 50
    assert folded.tokens.cache_write == 7
    assert folded.tokens.total == 1200 + 340 + 50 + 7
    assert folded.cost == 0.0123
    assert folded.cost_known is True
    assert (folded.runs, folded.pending) == (1, 0)


def test_a_key_left_pending_adds_nothing_and_makes_the_cost_unknown() -> None:
    """Spend that was never confirmed: a run still going, or a process killed
    between the pending and the final line. Not zero, not a number — unknown."""

    folded = fold_usage_records([_pending("a")])
    assert folded.tokens.total == 0
    assert folded.cost == 0.0
    assert folded.cost_known is False
    assert (folded.runs, folded.pending) == (1, 1)


def test_one_pending_among_settled_runs_keeps_their_figures() -> None:
    folded = fold_usage_records(
        [
            _pending("a"),
            _final("a", input=100, output=10, cost=0.5),
            _pending("b"),
        ]
    )
    assert folded.tokens.input == 100
    assert folded.cost == 0.5
    assert folded.cost_known is False  # b never settled
    assert (folded.runs, folded.pending) == (2, 1)


def test_a_duplicated_final_counts_once() -> None:
    """The store is at-least-once (ADR-0242 §3): a raising append can still
    leave a complete line, and a retry writes it again. Last line per key wins."""

    line = _final("a", input=100, output=10, cost=0.25)
    folded = fold_usage_records([_pending("a"), line, dict(line)])
    assert folded.tokens.input == 100
    assert folded.tokens.output == 10
    assert folded.cost == 0.25
    assert folded.cost_known is True
    assert folded.runs == 1


def test_the_last_line_of_a_key_wins_even_when_it_is_pending() -> None:
    """The rule is last-wins, stated without exceptions. A writer never puts a
    pending after its final; if one did, the reader must not guess."""

    folded = fold_usage_records([_final("a", input=5, cost=1.0), _pending("a")])
    assert folded.tokens.total == 0
    assert folded.cost == 0.0
    assert folded.cost_known is False
    assert folded.pending == 1


def test_lines_without_a_key_count_once_each() -> None:
    line = _final("x", input=10, output=1, cost=0.1)
    for missing in ({k: v for k, v in line.items() if k != "key"}, {**line, "key": None}):
        folded = fold_usage_records([missing, dict(missing)])
        assert folded.tokens.input == 20
        assert folded.runs == 2
    # An empty or non-string key is no key either.
    folded = fold_usage_records([{**line, "key": ""}, {**line, "key": 7}])
    assert folded.runs == 2
    assert folded.tokens.input == 20


def test_a_final_that_says_its_cost_is_unknown_still_adds_what_was_priced() -> None:
    folded = fold_usage_records(
        [_final("a", input=4000, output=369, cost=0.002, cost_known=False)]
    )
    assert folded.tokens.input == 4000
    assert folded.cost == 0.002  # the floor survives, so "at least" can show it
    assert folded.cost_known is False


def test_a_final_without_cost_known_fails_closed() -> None:
    line = _final("a", input=10, cost=0.1)
    del line["cost_known"]
    folded = fold_usage_records([line])
    assert folded.cost == 0.1
    assert folded.cost_known is False


@pytest.mark.parametrize(
    "bad",
    [math.nan, math.inf, -math.inf, -1, -0.5, True, False, "12", None, [1], 12.5],
    ids=[
        "nan", "inf", "-inf", "neg-int", "neg-float", "true", "false", "str", "none", "list",
        "half",
    ],
)
def test_a_bad_token_count_is_ignored_and_the_cost_becomes_unknown(bad: Any) -> None:
    """NaN and ±Infinity are written bare and read back (ADR-0242 rule 1.7)."""

    line = _final("a", input=100, output=10, cache_read=3, cache_write=2, cost=0.5)
    line["usage"]["output"] = bad
    folded = fold_usage_records([line])
    assert folded.tokens.output == 0  # the bad number adds nothing …
    assert folded.tokens.input == 100  # … and takes nothing else with it
    assert folded.tokens.total == 100 + 3 + 2
    assert folded.cost == 0.5
    assert folded.cost_known is False


@pytest.mark.parametrize(
    "bad",
    [math.nan, math.inf, -math.inf, -0.01, True, "0.5", None, 10**400],
    ids=["nan", "inf", "-inf", "neg", "true", "str", "none", "huge-int"],
)
def test_a_bad_cost_is_ignored_and_the_cost_becomes_unknown(bad: Any) -> None:
    line = _final("a", input=100, cost=0.0)
    line["usage"]["cost"] = bad
    folded = fold_usage_records([line])
    assert folded.cost == 0.0
    assert folded.tokens.input == 100
    assert folded.cost_known is False


def test_a_missing_usage_field_fails_closed() -> None:
    line = _final("a", input=100, cost=0.5)
    del line["usage"]["cache_write"]
    folded = fold_usage_records([line])
    assert folded.tokens.input == 100
    assert folded.cost == 0.5
    assert folded.cost_known is False


def test_a_whole_float_token_count_is_read_as_the_integer() -> None:
    line = _final("a", cost=0.0)
    line["usage"]["input"] = 12.0
    folded = fold_usage_records([line])
    assert folded.tokens.input == 12
    assert isinstance(folded.tokens.input, int)
    assert folded.cost_known is True


def test_a_final_whose_usage_is_not_an_object_adds_nothing() -> None:
    line = _final("a", input=100, cost=0.5)
    line["usage"] = [100, 10]
    folded = fold_usage_records([line])
    assert folded.tokens.total == 0
    assert folded.cost == 0.0
    assert folded.cost_known is False
    assert (folded.runs, folded.pending) == (1, 0)


@pytest.mark.parametrize(
    "unreadable",
    [
        None,
        "final",
        3,
        ["state", "final"],
        {"v": 1, "key": "b"},
        {"v": 1, "key": "b", "state": "done"},
    ],
    ids=["null", "string", "number", "list", "no-state", "unknown-state"],
)
def test_a_line_the_fold_cannot_read_is_skipped_and_makes_the_cost_unknown(
    unreadable: Any,
) -> None:
    """It may be spend a newer writer recorded in a shape this reader predates."""

    folded = fold_usage_records([_final("a", input=10, cost=0.1), unreadable])
    assert folded.tokens.input == 10
    assert folded.cost == 0.1
    assert folded.runs == 1  # the unreadable line is no run of its own
    assert folded.cost_known is False


def test_an_unreadable_line_does_not_displace_the_final_before_it() -> None:
    folded = fold_usage_records(
        [_final("a", input=10, cost=0.1), {"v": 1, "key": "a", "state": "refunded"}]
    )
    assert folded.tokens.input == 10
    assert folded.cost == 0.1
    assert folded.cost_known is False


def test_a_context_level_is_never_summed() -> None:
    """``tokens`` on a delegated child is a context LEVEL (the size of its last
    request), not spend. Only the four flows are read, wherever a level hides."""

    line = _final("a", input=100, output=10, cost=0.1)
    line["usage"]["tokens"] = 90_000
    line["usage"]["context_tokens"] = 90_000
    line["usage"]["total"] = 90_000
    line["context_tokens"] = 90_000
    line["tokens"] = 90_000
    folded = fold_usage_records([line])
    assert folded.tokens.total == 110
    assert folded.cost_known is True


def test_the_version_is_not_consulted() -> None:
    """A later ``v`` keeps these fields' meaning or is a different customType
    (ADR-0242 rule 1.3); unknown keys are ignored (rule 1.6)."""

    line = {**_final("a", input=10, cost=0.1), "v": 2, "futureKey": {"x": 1}}
    assert fold_usage_records([line]) == fold_usage_records([_final("a", input=10, cost=0.1)])


# === 2. the merge into SessionStats ===========================================


def _priced_turn() -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="a")],
        usage={"input": 1000, "output": 100, "cost": {"total": 0.01}},  # type: ignore[arg-type]
        provider="anthropic",
        model="claude-haiku-4-5",
    )


def test_tool_usage_is_added_to_the_session_totals_and_broken_out() -> None:
    stats = aggregate_session_stats(
        "s",
        [_priced_turn()],
        usage_records=[
            _pending("a"),
            _final("a", input=200, output=20, cache_read=5, cache_write=1, cost=0.002),
        ],
    )
    assert stats.tokens.input == 1200
    assert stats.tokens.output == 120
    assert stats.tokens.cache_read == 5
    assert stats.tokens.cache_write == 1
    assert stats.tokens.total == 1200 + 120 + 5 + 1
    assert stats.cost == pytest.approx(0.012)
    assert stats.cost_known is True
    # The breakdown is the tools' share only — already inside the totals above.
    assert stats.tool_usage.tokens.input == 200
    assert stats.tool_usage.cost == 0.002
    assert (stats.tool_usage.runs, stats.tool_usage.pending) == (1, 0)
    # Records are not messages: no count moves.
    assert stats.total_messages == 1
    assert stats.assistant_messages == 1


def test_a_pending_run_turns_the_session_cost_into_a_floor() -> None:
    stats = aggregate_session_stats("s", [_priced_turn()], usage_records=[_pending("a")])
    assert stats.cost == pytest.approx(0.01)  # the messages' figure is still real
    assert stats.cost_known is False  # but the bill is not all of it
    assert stats.tool_usage.pending == 1


def test_a_compacted_message_list_does_not_make_the_tool_share_unknown() -> None:
    """``cost_complete`` speaks for ``messages`` only; the records are read over
    the whole branch, compacted entries included."""

    stats = aggregate_session_stats(
        "s",
        [_priced_turn()],
        cost_complete=False,
        usage_records=[_final("a", input=1, cost=0.001)],
    )
    assert stats.cost_known is False
    assert stats.tool_usage.cost_known is True


def test_without_records_the_stats_are_what_they_were() -> None:
    before = aggregate_session_stats("s", [_priced_turn()])
    after = aggregate_session_stats("s", [_priced_turn()], usage_records=[])
    assert before == after
    assert before.tool_usage == ToolUsage()


def test_tool_usage_stays_off_the_rpc_wire_but_its_spend_is_in_the_totals() -> None:
    """``_session_stats_to_dict`` enumerates pi's keys; ``tool_usage`` must not
    appear, while ``tokens``/``cost`` carry the merged totals (design §A.4)."""

    from aelix_coding_agent.rpc.rpc_mode import _session_stats_to_dict

    stats = aggregate_session_stats(
        "s",
        [_priced_turn()],
        usage_records=[_final("a", input=200, output=20, cost=0.002)],
    )
    wire = _session_stats_to_dict(stats)
    assert set(wire) == {
        "sessionId",
        "userMessages",
        "assistantMessages",
        "toolCalls",
        "toolResults",
        "totalMessages",
        "tokens",
        "cost",
    }
    assert set(wire["tokens"]) == {"input", "output", "cacheRead", "cacheWrite", "total"}
    assert wire["tokens"]["input"] == 1200
    assert wire["cost"] == pytest.approx(0.012)
    assert "toolUsage" not in json.dumps(wire)
    assert "tool_usage" not in json.dumps(wire)


# === 3. roll_up_usage agrees with the kernel fold on the flows ================


def test_roll_up_usage_equals_the_kernel_fold_on_the_flows() -> None:
    """Design §A.4: ``aggregate.roll_up_usage`` keeps only its display role (the
    batch ``[total]`` line, where ``tokens`` is a level and takes the max). On
    the FLOWS — input, output, cache read, cache write, cost — it and the kernel
    fold must say the same thing for the same runs, or the batch line and
    ``/cost`` disagree about one delegation."""

    from aelix_agents.aggregate import roll_up_usage
    from aelix_coding_agent.subagent_contract import SubagentResult, SubagentUsage

    usages = [
        SubagentUsage(
            input=1200, output=340, cache_read=50, cache_write=7, cost=0.0123, tokens=1597, turns=2
        ),
        SubagentUsage(),  # a run that spent nothing
        SubagentUsage(
            input=98765,
            output=4321,
            cache_read=12000,
            cache_write=800,
            cost=0.3317,
            tokens=90000,
            turns=7,
        ),
        SubagentUsage(input=3, output=1, cost=0.1 + 0.2, tokens=4, turns=1),
    ]
    results = [
        SubagentResult(
            id=f"sub-{i:012x}", profile="scout", ok=True, status="ok", summary="", usage=u
        )
        for i, u in enumerate(usages)
    ]
    rolled = roll_up_usage(results)

    # What the parent session holds for these runs, in a parallel batch's shape:
    # the pendings as the members start, then the finals as they settle.
    records = [_pending(r.id) for r in results] + [
        _final(
            r.id,
            input=r.usage.input,
            output=r.usage.output,
            cache_read=r.usage.cache_read,
            cache_write=r.usage.cache_write,
            cost=r.usage.cost,
        )
        for r in results
    ]
    folded = fold_usage_records(records)

    assert folded.tokens.input == rolled.input
    assert folded.tokens.output == rolled.output
    assert folded.tokens.cache_read == rolled.cache_read
    assert folded.tokens.cache_write == rolled.cache_write
    assert folded.cost == pytest.approx(rolled.cost, rel=1e-12, abs=1e-12)
    # The level is the one field they must NOT agree on: the kernel never
    # sums one, and its ``total`` is a flow.
    flows = rolled.input + rolled.output + rolled.cache_read + rolled.cache_write
    assert folded.tokens.total == flows
    assert rolled.tokens == max(u.tokens for u in usages)
    assert folded.runs == len(results)


# === 4. through the harness: one branch read, compaction, /tree, /fork ========


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


def _harness(session: Session | None, messages: list[Any] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=None,  # no model → the context meter never reads the branch
            stream_fn=_stream(),
            session=session,
            initial_messages=list(messages or []),
        )
    )


async def _delegating_turn(
    session: Session, key: str, *, text: str, input: int, cost: float, settle: bool = True
) -> dict[str, str]:
    """One turn in the order a delegation writes it: the tool call, the pending
    record, the final record (if the run settled), then the tool result."""

    user = await session.append_message(UserMessage(content=[TextContent(text=text)]))
    call = await session.append_message(
        AssistantMessage(
            content=[ToolCallContent(tool_call_id=f"c-{key}", tool_name="agent", input={})],
        )
    )
    await session.append_custom_entry(USAGE_RECORD_TYPE, _pending(key))
    if settle:
        await session.append_custom_entry(
            USAGE_RECORD_TYPE, _final(key, input=input, output=1, cost=cost)
        )
    result = await session.append_message(
        ToolResultMessage(tool_call_id=f"c-{key}", content=[TextContent(text="done")])
    )
    return {"user": user, "call": call, "result": result}


async def test_get_session_stats_folds_the_branch_records() -> None:
    session = Session(MemorySessionStorage())
    await _delegating_turn(session, "k1", text="u1", input=100, cost=0.25)
    await _delegating_turn(session, "k2", text="u2", input=50, cost=0.5)
    harness = _harness(session)
    try:
        stats = await harness.get_session_stats()
        assert isinstance(stats, SessionStats)
        assert stats.tool_usage.runs == 2
        assert stats.tool_usage.tokens.input == 150
        assert stats.tokens.input == 150  # no message carried usage here
        assert stats.cost == pytest.approx(0.75)
        assert stats.cost_known is True
    finally:
        await harness.dispose()


async def test_a_custom_message_of_the_same_type_is_not_a_record() -> None:
    """Only ``custom`` entries are records; a ``custom_message`` enters context
    and is something else (ADR-0242 rule 1.1)."""

    session = Session(MemorySessionStorage())
    await session.append_custom_message_entry(
        USAGE_RECORD_TYPE, "not a record", display=False, details=_final("m", input=999, cost=9.0)
    )
    await session.append_custom_entry("someone.else", _final("o", input=999, cost=9.0))
    harness = _harness(session)
    try:
        stats = await harness.get_session_stats()
        assert stats.tool_usage == ToolUsage()
        assert stats.cost == 0.0
    finally:
        await harness.dispose()


async def test_the_branch_is_read_once_for_the_records_and_the_compaction_check() -> None:
    session = Session(MemorySessionStorage())
    await _delegating_turn(session, "k1", text="u1", input=10, cost=0.1)
    calls: list[Any] = []
    original = session.get_branch

    async def counting(from_id: str | None = None) -> list[Any]:
        calls.append(from_id)
        return await original(from_id)

    session.get_branch = counting  # type: ignore[method-assign]
    harness = _harness(session)
    try:
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 1
        assert len(calls) == 1
    finally:
        await harness.dispose()


async def test_an_unreadable_branch_yields_a_floor_and_no_records() -> None:
    session = Session(MemorySessionStorage())
    await _delegating_turn(session, "k1", text="u1", input=10, cost=0.1)

    async def broken(from_id: str | None = None) -> list[Any]:
        raise RuntimeError("storage gone")

    session.get_branch = broken  # type: ignore[method-assign]
    harness = _harness(session)
    try:
        stats = await harness.get_session_stats()
        assert stats.cost_known is False  # fails closed, as before #199
        assert stats.tool_usage.runs == 0
    finally:
        await harness.dispose()


async def test_no_session_means_no_records_and_a_known_cost() -> None:
    harness = _harness(None, [_priced_turn()])
    try:
        stats = await harness.get_session_stats()
        assert stats.tool_usage == ToolUsage()
        assert stats.cost_known is True
        assert stats.cost == pytest.approx(0.01)
    finally:
        await harness.dispose()


async def test_records_before_the_latest_compaction_still_count() -> None:
    """``_state.messages`` is the post-compaction list, but the records are read
    over the whole root→leaf path — compaction summarizes messages away, it does
    not refund what a tool spent."""

    session = Session(MemorySessionStorage())
    await _delegating_turn(session, "k1", text="u1", input=100, cost=0.25)
    kept = await session.append_message(UserMessage(content=[TextContent(text="u2")]))
    reply = _priced_turn()
    await session.append_message(reply)
    await session.append_compaction(
        summary="rolled up", first_kept_entry_id=kept, tokens_before=5000
    )
    harness = _harness(session, [UserMessage(content=[TextContent(text="u2")]), reply])
    try:
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 1
        assert stats.tool_usage.tokens.input == 100
        assert stats.tool_usage.cost_known is True
        assert stats.tokens.input == 100 + 1000
        assert stats.cost == pytest.approx(0.25 + 0.01)
        # The messages part is a floor after a compaction, exactly as before.
        assert stats.cost_known is False
    finally:
        await harness.dispose()


async def test_a_tree_move_stops_counting_records_off_the_path() -> None:
    """Records follow the branch the way messages do. pi sums every entry of
    the file, every branch — the ADR-0235 divergence design §A.4 records."""

    session = Session(MemorySessionStorage())
    await _delegating_turn(session, "k1", text="u1", input=100, cost=0.25)
    second = await _delegating_turn(session, "k2", text="u2", input=50, cost=0.5)
    harness = _harness(session)
    try:
        assert (await harness.get_session_stats()).tool_usage.runs == 2

        # Back to before u2 — the leaf becomes turn one's tool result.
        await harness.navigate_tree(second["user"])
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 1
        assert stats.tool_usage.tokens.input == 100
        assert stats.cost == pytest.approx(0.25)

        # A new branch from there counts its own record, never k2's.
        await _delegating_turn(session, "k3", text="u3", input=7, cost=0.0, settle=False)
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 2
        assert stats.tool_usage.pending == 1
        assert stats.tool_usage.tokens.input == 100
        assert stats.cost_known is False

        # And moving back to the first branch counts k2 again.
        await harness.navigate_tree(second["result"])
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 2
        assert stats.tool_usage.pending == 0
        assert stats.tool_usage.tokens.input == 150
    finally:
        await harness.dispose()


async def test_a_fork_carries_the_records_on_its_branch(tmp_path: Path) -> None:
    """``/fork`` (a fork before a user message) copies the path to that point,
    records included — and ``CustomEntry.data`` survives the decode/re-encode a
    fork does (ADR-0242 rule 1.3). Short cwds keep Windows paths short."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path / "s"))
    source = await repo.create(JsonlSessionCreateOptions(cwd="/p"))
    await _delegating_turn(source, "k1", text="u1", input=100, cost=0.25)
    second = await _delegating_turn(source, "k2", text="u2", input=50, cost=0.5)
    source_meta = (await repo.list())[0]

    before_u2 = await repo.fork(
        source_meta, ForkOptions(cwd="/f", entry_id=second["user"], position="before")
    )
    whole = await repo.fork(source_meta, ForkOptions(cwd="/f"))
    for forked, runs, tokens in ((before_u2, 1, 100), (whole, 2, 150)):
        harness = _harness(forked)
        try:
            stats = await harness.get_session_stats()
            assert stats.tool_usage.runs == runs
            assert stats.tool_usage.tokens.input == tokens
            assert stats.tool_usage.cost_known is True
        finally:
            await harness.dispose()


async def test_records_read_back_from_disk_fold_the_same(tmp_path: Path) -> None:
    """Through a real JSONL file: what the fold reads is what a reload returns.
    A NaN cost is written bare and reads back as NaN (ADR-0242 rule 1.7), which
    the fold ignores and reports as an unknown cost."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path / "s"))
    session = await repo.create(JsonlSessionCreateOptions(cwd="/p"))
    await _delegating_turn(session, "k1", text="u1", input=100, cost=0.25)
    nan_line = _final("k2", input=5, cost=0.0)
    nan_line["usage"]["cost"] = math.nan
    await session.append_custom_entry(USAGE_RECORD_TYPE, nan_line)
    reopened = await repo.open((await repo.list())[0])
    harness = _harness(reopened)
    try:
        stats = await harness.get_session_stats()
        assert stats.tool_usage.runs == 2
        assert stats.tool_usage.tokens.input == 105
        assert stats.tool_usage.cost == pytest.approx(0.25)
        assert stats.tool_usage.cost_known is False
    finally:
        await harness.dispose()
