"""N envelopes → ONE ``ToolResult`` for the parent's model (ADR-0199 §3.4).

PURE. No ``asyncio``, no process, no clock: the wall time and the per-member
outcome classification both arrive as arguments, so the whole batch layout, the
``is_error`` rule and the usage roll-up are pinned without spawning anything.

ONE ``ToolResult`` PER ``agent`` CALL, ALWAYS. ``mode="single"`` never reaches
this module — it stays on ``render_subagent_result`` (``tool.py:832-864``)
byte-for-byte, which is what keeps the 40 tests in ``test_tool_and_security.py``
and the 69 in ``test_print_channel_spawn.py`` meaningful.

THE OTHER RENDERER IS NOT TOUCHED. There are two: this one plus
``tui/commands.py:1261-1262`` → ``_render_subagent_result``, the human-facing
``/agents run`` door. That door stays single-task under decision S2, so it needs
no batch rendering and gets none.

"FAILED" AND "NEVER STARTED" ARE DIFFERENT FACTS AND ARE RENDERED DIFFERENTLY.
A member refused by ``_admit_live`` (``runtime.py:454-459``), by the per-prompt
budget, or by the batch's own wall-clock budget produced NO CHILD AT ALL. A model
that cannot tell "this ran and failed" from "this never ran" will report the work
as done. The classification is supplied by the executor at the point it creates
the envelope — see :class:`MemberOutcome` — and is NEVER re-derived here by
string-matching a summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.subagent_contract import SubagentUsage

from aelix_agents.envelope import NO_OUTPUT

# The per-member usage line is IMPORTED, not re-spelled. ``tool.py:697-782``
# already owns that format and ``render_subagent_result`` prints it for the
# single-mode path; a second spelling here would drift the moment either is
# edited, and a batch whose member lines disagree with a single call's line is
# exactly the kind of inconsistency a model narrates as a real difference.
from aelix_agents.tool import _usage_line

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aelix_coding_agent.subagent_contract import SubagentMode, SubagentResult

MemberClass = Literal["ok", "failed", "did_not_start"]
"""The three outcome classes a batch member can land in (§3.4)."""

_DID_NOT_START_TAG = "did not start"
"""How :data:`MemberClass` ``"did_not_start"`` reads in the rendered heading. A
phrase rather than a ``SubagentOutcome`` value on purpose: ``SubagentOutcome``
(``subagent_contract.py:67``) has no member for "no process was ever created",
and inventing one would be a product-core edit (S2)."""


@dataclass(frozen=True)
class MemberOutcome:
    """One batch member's envelope, plus WHETHER A CHILD EVER EXISTED.

    ``started`` is keyword-only and has NO DEFAULT, deliberately: the executor is
    the only code that knows the answer — it knows it at the moment it builds the
    envelope, because it built the refusal itself — and a default would let a
    future caller get the distinction wrong silently. The alternative,
    recognising a refusal by matching ``_TOO_MANY_LIVE`` or ``_BUDGET_EXHAUSTED``
    against ``summary`` here, would couple this renderer to
    ``runtime.py``'s message strings and would misclassify a CHILD that happened
    to echo one of them.

    Prefer the two named constructors at the call site: ``MemberOutcome.ran(r)``
    and ``MemberOutcome.never_started(r)`` say which fact is being asserted.
    """

    result: SubagentResult
    started: bool = field(kw_only=True)

    def __post_init__(self) -> None:
        # A member that never started cannot have succeeded. Every envelope in
        # that class is a refusal built by the executor, and every refusal
        # carries ``ok=False`` (``envelope.py`` never mints an ``ok=True``
        # envelope without a process). Catching the incoherent combination here
        # is what stops it being rendered as "did not start" while counting
        # toward the ok tally.
        if not self.started and self.result.ok:
            raise ValueError(
                "a member that never started cannot carry ok=True "
                f"(id={self.result.id!r})"
            )

    @classmethod
    def ran(cls, result: SubagentResult) -> MemberOutcome:
        """A child process existed. It may still have failed."""

        return cls(result, started=True)

    @classmethod
    def never_started(cls, result: SubagentResult) -> MemberOutcome:
        """No child process was ever created — a live-cap, budget or
        batch-deadline refusal."""

        return cls(result, started=False)

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def outcome(self) -> MemberClass:
        if not self.started:
            return "did_not_start"
        return "ok" if self.result.ok else "failed"


def roll_up_usage(results: Iterable[SubagentResult]) -> SubagentUsage:
    """Sum a batch's spend — EXCEPT ``tokens``, which takes the maximum.

    ``SubagentUsage.tokens`` is documented as a context LEVEL, "last message
    wins" (``subagent_contract.py:95-96``), not a running total. Summing four
    children's context levels reports a number several times the real one — the
    same mistake ``stream.py:214-217`` already warns about — and it is the number
    the statusline and any future cost display read. ``max`` is the honest
    aggregate: the largest context any single child reached.

    Everything else — ``input``, ``output``, ``cache_read``, ``cache_write``,
    ``cost``, ``turns`` — is a genuine counter and is summed.
    """

    total = SubagentUsage()
    for result in results:
        usage = result.usage
        total = SubagentUsage(
            input=total.input + usage.input,
            output=total.output + usage.output,
            cache_read=total.cache_read + usage.cache_read,
            cache_write=total.cache_write + usage.cache_write,
            cost=total.cost + usage.cost,
            tokens=max(total.tokens, usage.tokens),
            turns=total.turns + usage.turns,
        )
    return total


def _format_count(value: int) -> str:
    """Compact token counts for the ``[total]`` line only.

    Mirrors ``progress._format_tokens`` (``progress.py:176-179``) rather than
    importing it: that helper appends its own ``" tok"`` unit, which this line
    does not want (it reads ``… in / … out``). The threshold and the one decimal
    place are kept identical so a batch total and a statusline row never disagree
    about how 3 600 is spelled.
    """

    if value < 1000:
        return str(value)
    return f"{value / 1000:.1f}k"


def _member_block(index: int, total: int, member: MemberOutcome) -> str:
    """One member's paragraph: heading + summary, notes, then the usage line.

    Single newlines inside a member, blank lines BETWEEN members. That differs
    from ``render_subagent_result``, which joins with blank lines
    (``tool.py:859``) — there it has the whole tool result to itself, whereas
    here a blank line is the only thing separating one child's answer from the
    next one's, and reusing it would make the two levels indistinguishable.
    """

    tag = _DID_NOT_START_TAG if not member.started else member.result.status
    body = member.result.summary or NO_OUTPUT
    lines = [f"[{index}/{total} {tag}] {body}"]

    # The note set mirrors ``render_subagent_result`` (``tool.py:846-858``) so a
    # batch member never says less about itself than the same child would say on
    # the single-mode path.
    if member.result.error and member.result.error not in body:
        lines.append(f"Error: {member.result.error}")
    if member.result.dropped_tools:
        lines.append(
            "Tools not granted to this agent: "
            + ", ".join(member.result.dropped_tools)
            + " (it holds no tool you do not hold)."
        )
    if member.result.dropped_lines:
        lines.append(f"{member.result.dropped_lines} oversize output line(s) were dropped.")
    # THE USAGE LINE MUST NOT CONTRADICT THE HEADING ONE LINE ABOVE IT. Every
    # never-started envelope carries ``status="error"`` — ``_refusal_envelope``
    # (``batch.py:681-696``) and ``runtime._error_result`` both mint one that way
    # — so printing ``result.status`` verbatim renders
    # ``[2/2 did not start] …`` immediately above ``[agent scout · error · 0.0s]``.
    # That is the exact conflation this module exists to prevent (see the
    # "FAILED AND NEVER STARTED" paragraph in the module docstring): the reader
    # is told twice, in two words, and the second one is wrong. The override is
    # passed rather than the status re-spelled here so ``tool._usage_line``
    # stays the single owner of the format (``tool.py:697-782``).
    lines.append(
        _usage_line(
            member.result, status=None if member.started else _DID_NOT_START_TAG
        )
    )
    return "\n".join(lines)


def _not_run_line(
    mode: SubagentMode, first: int, total: int, stopped_by: MemberOutcome | None
) -> str:
    """Name the steps that never ran (§3.3), and say TRUTHFULLY why.

    A COUNT alone is not enough: the model addressed the tasks by position, so it
    has to be told WHICH positions produced nothing. The steps are contiguous and
    trailing by construction — a chain runs in submitted order and stops at the
    first failure — so naming the range is exact rather than approximate.

    ``stopped_by`` is the LAST member, i.e. the one whose outcome ended the
    chain, and the reason is read off it rather than assumed. A chain has two
    ways to stop and only one of them is a failure: a step whose RENDERED text
    exceeded ``MAX_TASK_BYTES`` after ``{previous}`` substitution never starts a
    child at all (``batch.py:355-365``), so "the chain stopped at the first
    failure" is a statement about an event that did not happen — printed
    directly beneath a heading that says ``did not start``, and beneath a header
    counting ``0 failed``. A model reading three mutually inconsistent sentences
    about the same batch has no way to decide which to believe.
    """

    noun = "Step" if mode == "chain" else "Task"
    span = f"{first}" if first == total else f"{first}-{total}"
    plural = "" if first == total else "s"
    if mode != "chain":
        # Parallel submits everything it is given, so a parallel batch with
        # not-run members was trimmed by its caller, not stopped by a member.
        reason = "they were never submitted"
    elif stopped_by is None:
        reason = "the chain never started"
    elif not stopped_by.started:
        reason = f"the chain stopped when step {first - 1} could not be started"
    elif not stopped_by.ok:
        reason = "the chain stopped at the first failure"
    else:
        # No failure and no refusal: the executor does not produce this today
        # (``_run_chain`` only breaks on those two), so all this branch may
        # honestly say is WHERE it stopped, not why.
        reason = f"the chain stopped after step {first - 1}"
    was = "was" if first == total else "were"
    return f"{noun}{plural} {span} of {total} {was} NOT RUN — {reason}."


def _header(
    profile: str,
    mode: SubagentMode,
    total: int,
    counts: dict[MemberClass, int],
    not_run: int,
) -> str:
    parts = [
        f"agent {profile}",
        mode,
        f"{total} tasks",
        f"{counts['ok']} ok",
        f"{counts['failed']} failed",
    ]
    # The two remaining classes are shown ONLY when non-zero: they are the
    # unusual outcomes, and a header that always carried "0 did not start · 0 not
    # run" would train the reader to skip the segment that matters.
    if counts["did_not_start"]:
        parts.append(f"{counts['did_not_start']} {_DID_NOT_START_TAG}")
    if not_run:
        parts.append(f"{not_run} not run")
    return " · ".join(parts)


def _total_line(
    total: int,
    counts: dict[MemberClass, int],
    not_run: int,
    usage: SubagentUsage,
    wall_ms: int,
) -> str:
    parts = [
        f"{total} tasks",
        f"{counts['ok']} ok",
        f"{counts['failed']} failed",
    ]
    if counts["did_not_start"]:
        parts.append(f"{counts['did_not_start']} {_DID_NOT_START_TAG}")
    if not_run:
        parts.append(f"{not_run} not run")
    if usage.input or usage.output:
        parts.append(f"{_format_count(usage.input)} in / {_format_count(usage.output)} out")
    if usage.cost:
        parts.append(f"${usage.cost:.4f}")
    # WALL CLOCK, measured by the executor — not a sum and not a max of the
    # members' ``elapsed_ms``. It is the only number that is true for both modes:
    # a sum lies about parallel (four children in 12 s are not 48 s of user time)
    # and a max lies about chain. Per-member ``elapsed_ms`` stays on each block.
    parts.append(f"{wall_ms / 1000:.1f}s wall")
    return "[total] " + " · ".join(parts)


def _join_details(profile: str, total: int, members: Sequence[MemberOutcome]) -> str | None:
    """The members' ``details``, separated and labelled by position.

    UNCAPPED, exactly as on the single-mode path: ``details`` rides
    ``ToolResult.details`` and is not sent to the model, so the truncation marker
    inside each ``summary`` keeps its promise that the full output was preserved.
    Empty ones are omitted rather than rendered as an empty section, and an
    all-empty batch yields ``None`` — which is what ``render_subagent_result``
    passes when a single child had nothing (``tool.py:862``).
    """

    chunks = [
        f"--- [{index}/{total}] {profile} ---\n{member.result.details}"
        for index, member in enumerate(members, start=1)
        if member.result.details
    ]
    return "\n\n".join(chunks) or None


def render_batch_result(
    profile: str,
    mode: SubagentMode,
    members: Sequence[MemberOutcome],
    *,
    not_run: int,
    wall_ms: int,
) -> ToolResult:
    """Fold a whole batch into the one ``ToolResult`` the parent's model reads.

    ``members`` is in SUBMITTED ORDER, never completion order, and this function
    preserves it. A transcript ordered by whichever child happened to finish
    first is not reproducible between two runs of the same batch, and the model
    addressed the tasks by position — ``[3/4]`` has to be the third task it
    wrote.

    ``not_run`` counts steps for which NO envelope exists at all: a chain stops
    at the first failure (§3.3), so the trailing steps were never submitted and
    never produced one. That is a different fact from a member with
    ``started=False``, which DOES have an envelope explaining its refusal, and
    the two are counted and rendered separately.

    ``is_error`` is true when ANY member failed OR ANY task did not run. The
    member half rejects ``not any(member.ok)``, i.e. only a total wipe-out is an
    error: a 7-of-8 batch reported clean is exactly how a model concludes that
    delegated work is finished. The ``not_run`` half closes the same hole one
    step further out — ``not_run > 0`` means the body of this very result says
    ``… were NOT RUN``, and a partially-executed batch that reports
    ``is_error=False`` contradicts its own text. It is unreachable from today's
    executor (``_run_parallel`` hard-codes ``not_run=0`` and a chain only leaves
    steps unrun after a member that was not ok), so the clause changes no shipped
    behaviour; it holds for the first caller that produces "some tasks were
    dropped, all the ones we ran succeeded" — a deadline trim, a future
    ``continue_on_error``. ``is_error`` is not fatal — the kernel renders an
    error tool result as readable text either way — so the strict reading costs
    nothing and buys attention. For a one-member batch with nothing unrun this
    reduces to ``not result.ok``, i.e. today's single-mode behaviour, by
    construction.
    """

    total = len(members) + not_run
    counts: dict[MemberClass, int] = {"ok": 0, "failed": 0, "did_not_start": 0}
    for member in members:
        counts[member.outcome] += 1

    usage = roll_up_usage(member.result for member in members)
    blocks = [
        _member_block(index, total, member)
        for index, member in enumerate(members, start=1)
    ]

    sections = [_header(profile, mode, total, counts, not_run), *blocks]
    if not_run:
        # The LAST member is the one whose outcome ended the run — see
        # :func:`_not_run_line` for why the reason is read off it instead of
        # being assumed to be a failure.
        sections.append(
            _not_run_line(mode, len(members) + 1, total, members[-1] if members else None)
        )
    sections.append(_total_line(total, counts, not_run, usage, wall_ms))

    return ToolResult(
        content=[TextContent(text="\n\n".join(sections))],
        details=_join_details(profile, total, members),
        is_error=not_run > 0 or any(not member.ok for member in members),
    )


__all__ = [
    "MemberClass",
    "MemberOutcome",
    "render_batch_result",
    "roll_up_usage",
]
