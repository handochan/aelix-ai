"""#299 — ``!cmd`` output reaches the model; ``!!cmd`` still does not.

Three tiers have to agree and they are read from three different places, so
each one is asserted separately:

* **live**   — ``harness.messages``, what the NEXT ``prompt()`` sends;
* **replay** — ``build_session_context``, what a ``--continue`` sends;
* **display**— ``build_display_messages``, what the TUI redraws.

Before this issue the record was a ``CustomEntry``, which ADR-0242 rule 1.6
has every reader skip, and the TUI never touched ``_state.messages`` — so all
three were empty and ``!`` and ``!!`` were the same command from the model's
side. Half of these tests would pass against a fix that only repaired the live
tier, and half against one that only repaired the record; the ``!!`` half
fails against a fix that just includes everything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import UserBashResult
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.session.context import (
    build_display_messages,
    build_session_context,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli.repl import (
    _MAX_BYTES,
    _MAX_COMMAND_CHARS,
    _MAX_LINES,
    bash_execution_to_text,
    handle_user_bash,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)
from aelix_coding_agent.tools.bash import ExecExitResult

MARKER = "MARKER-299"


def _text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(getattr(block, "text", "") or "" for block in content)
    return str(content)


def _joined(messages: list) -> str:
    return "\n".join(_text(m) for m in messages)


async def _harness_with_session(
    storage: object,
) -> tuple[AgentHarness, Session]:
    session = Session(storage)  # type: ignore[arg-type]
    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="m", api="anthropic"), session=session)
    )
    return harness, session


async def _memory_harness() -> tuple[AgentHarness, Session]:
    return await _harness_with_session(MemorySessionStorage())


async def test_bang_output_reaches_every_tier(tmp_path: Path) -> None:
    harness, session = await _memory_harness()

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )

    branch = await session.get_branch()
    assert MARKER in _joined(harness.messages), "live tier: the next prompt()"
    assert MARKER in _joined(build_session_context(branch).messages), "replay tier"
    assert MARKER in _joined(build_display_messages(branch)), "display tier"


async def test_double_bang_reaches_no_tier(tmp_path: Path) -> None:
    """The half that a careless fix breaks: ``!!`` is still transient."""

    harness, session = await _memory_harness()

    output = await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=True, cwd=str(tmp_path)
    )

    assert MARKER in output, "the user still sees it on screen"
    branch = await session.get_branch()
    assert branch == [], "nothing is recorded at all"
    assert harness.messages == [], "live tier stays empty"
    assert build_session_context(branch).messages == []
    assert build_display_messages(branch) == []


async def test_the_record_is_a_custom_message_entry(tmp_path: Path) -> None:
    """ADR-0242 rule 1.1 — a record that must reach the model is not ``custom``."""

    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(tmp_path / "s.jsonl"), cwd="/repo", session_id="t"
    )
    harness, session = await _harness_with_session(storage)

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )

    (entry,) = await session.get_entries()
    assert entry.type == "custom_message"
    assert entry.custom_type == "bash_execution"  # type: ignore[union-attr]
    assert entry.display is True  # type: ignore[union-attr]
    # Rule 1.4: a first-party ``CustomMessageEntry.content`` is a plain string.
    assert isinstance(entry.content, str)  # type: ignore[union-attr]
    # The output is NOT here: ``content`` above already carries it, and
    # nothing reads this key back. Storing it twice doubled the session file.
    assert entry.details == {  # type: ignore[union-attr]
        "command": f"echo {MARKER}",
        "exit_code": 0,
    }


async def test_live_and_replay_are_the_same_message(tmp_path: Path) -> None:
    """Including the timestamp — two clocks would render one turn two ways."""

    harness, session = await _memory_harness()

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )

    (live,) = harness.messages
    (replayed,) = build_session_context(await session.get_branch()).messages
    assert _text(live) == _text(replayed)
    assert live.timestamp == replayed.timestamp


async def test_the_live_tier_works_without_a_session(tmp_path: Path) -> None:
    """``--no-session``/embedder path: no record to write, still one message."""

    harness = AgentHarness(AgentHarnessOptions(model=Model(id="m", api="anthropic")))

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )

    assert MARKER in _joined(harness.messages)


async def test_a_released_bash_execution_custom_entry_still_loads(
    tmp_path: Path,
) -> None:
    """Sessions written before #299 keep loading — they just stay invisible.

    ADR-0242 rule 1.6 is unchanged for ``custom``: a reader skips it. The
    entry is not dropped and nothing below it is orphaned, which is the only
    promise the old shape ever made.
    """

    session = Session(MemorySessionStorage())
    await session.append_custom_entry(
        custom_type="bash_execution",
        data={"command": f"echo {MARKER}", "output": f"{MARKER}\n"},
    )
    await session.append_custom_message_entry(
        custom_type="bash_execution", content="after", display=True, details=None
    )

    branch = await session.get_branch()
    assert [e.type for e in branch] == ["custom", "custom_message"]
    assert MARKER not in _joined(build_session_context(branch).messages)
    assert "after" in _joined(build_session_context(branch).messages)


def test_rendering_matches_pi_for_both_branches() -> None:
    """pi ``bashExecutionToText`` (``harness/messages.ts:63-79``)."""

    assert bash_execution_to_text("ls", "a\nb") == "Ran `ls`\n```\na\nb\n```"
    assert bash_execution_to_text("true", "") == "Ran `true`\n(no output)"


# --------------------------------------------------------------------------
# What every test above this line shares is ``echo MARKER`` — one short line,
# zero exit, a session that writes. Each test below drives one thing that is
# not that, because the record only has to be TRUE for the easy case to look
# fixed.
#
# They also stop spawning an interpreter. ``python3 -c "print(...)"`` was how
# the first cut of #299 made a huge output, and it costs the test two things it
# cannot afford here: a ``python3`` on PATH, which is a name the windows runner
# happens to have rather than one anything in this repo provisions, and a line
# ending chosen by the platform — ``print()`` ends in ``\n`` on POSIX and
# ``\r\n`` on Windows, and those are two DIFFERENT rows of the table below.
# Letting the platform pick the row is what left the ``\r\n`` behaviour
# unasserted until the windows CI legs went red on it.
# --------------------------------------------------------------------------


class _EmittingOps:
    """A ``BashOperations`` that writes EXACT bytes and exits with a code.

    The Protocol's own swap seam (``tools/bash.py:220`` — "swap surface for
    SSH/remote"), reached through the ``user_bash`` hook's ``operations``
    return. Only the spawn is replaced: ``decode_child_output``, the cap, the
    record and both tiers all still run for real.
    """

    def __init__(self, output: str, exit_code: int = 0) -> None:
        self._data = output.encode()
        self._exit_code = exit_code

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: Any,
        signal: Any = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecExitResult:
        on_data(self._data)
        return ExecExitResult(exit_code=self._exit_code)


def _emitting(output: str, exit_code: int = 0) -> Extension:
    def handler(_event: Any, _ctx: Any) -> UserBashResult:
        return UserBashResult(operations=_EmittingOps(output, exit_code))  # type: ignore[arg-type]

    ext = Extension(name="emit")
    ExtensionAPI(ext, _ExtensionRuntime()).on("user_bash", handler)
    return ext


async def _harness_printing(
    output: str, *, exit_code: int = 0, storage: object | None = None
) -> tuple[AgentHarness, Session]:
    """A harness whose ``!`` commands print ``output`` and nothing else."""

    session = Session(storage or MemorySessionStorage())  # type: ignore[arg-type]
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=session,
            extensions=[_emitting(output, exit_code)],
        )
    )
    return harness, session


def _recorded_body(text: str) -> str:
    """What is between the fences — ``""`` when the record says (no output)."""

    _, fence, rest = text.partition("```\n")
    return rest[: rest.rindex("\n```")] if fence else ""


#: One output line long enough that its own bytes blow the cap, ending the
#: way every ``print()`` does.
_HUGE_OUTPUT = MARKER + "x" * (_MAX_BYTES * 4) + "\n"


async def test_a_huge_output_is_capped_in_the_record_but_not_on_screen(
    tmp_path: Path,
) -> None:
    """The ``!`` path was the only bash surface here with no cap.

    Every sibling has one — the model-facing tool at 2000 lines / 50KB
    (``tools/bash.py:63``), ad-hoc RPC bash at 256 / 32KB
    (``rpc/rpc_mode.py:605``), pi's own ``!`` path via ``truncateTail``
    (``core/bash-executor.ts:113``). Uncapped, the record is re-sent on every
    later turn and every ``--continue``: measured before the cap,
    ``print('x'*1000000)`` wrote a 1,000,047-char ``UserMessage`` and a
    2,000,455-byte session file.
    """

    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(tmp_path / "s.jsonl"), cwd="/repo", session_id="t"
    )
    harness, session = await _harness_printing(_HUGE_OUTPUT, storage=storage)

    on_screen = await handle_user_bash(
        harness, "print-a-lot", exclude_from_context=False, cwd=str(tmp_path)
    )

    # The user asked to run a command; the user gets all of it.
    assert len(on_screen) > _MAX_BYTES * 4
    # The model does not.
    (live,) = harness.messages
    recorded = _text(live)
    assert len(recorded) < _MAX_BYTES * 2, len(recorded)
    assert len(recorded.encode()) < len(on_screen.encode())
    assert (
        len((tmp_path / "s.jsonl").read_bytes()) < _MAX_BYTES * 2
    ), "the session file carries the capped copy too"
    # Visible, not silent: a record that stops mid-stream without saying so
    # reads as a command that printed that much and stopped.
    assert "[Showing the last" in recorded
    assert "50.0KB limit" in recorded
    (entry,) = await session.get_entries()
    assert entry.details["truncation"]["original_bytes"] > _MAX_BYTES * 4  # type: ignore[index,union-attr]


async def test_the_marker_survives_the_cap_because_the_TAIL_is_kept(
    tmp_path: Path,
) -> None:
    """``truncate_tail``, not head: bash output's answer is at the end."""

    harness, _ = await _harness_printing("y" * (_MAX_BYTES * 4) + "\n" + MARKER + "\n")

    await handle_user_bash(
        harness, "print-a-lot-then-the-marker", exclude_from_context=False,
        cwd=str(tmp_path),
    )

    recorded = _text(harness.messages[0])
    assert MARKER in recorded, "the last line is the one that must survive"
    assert "[Showing lines" in recorded


async def test_a_trailing_newline_does_not_eat_the_whole_output(
    tmp_path: Path,
) -> None:
    """The cap's own failure mode, and the reviewer's exact repro.

    ``truncate_tail`` splits on ``\\n`` raw, so a trailing newline leaves an
    empty final element that IS a line: it costs 0 bytes, so it is the one
    line that fits, and the long line above it is dropped whole. Every
    ``print()`` ends in a newline. pi pops that element first
    (``splitLinesForCounting``, ``core/tools/truncate.ts:47-56``) and
    :func:`_cap_for_the_record` strips the whole terminator — without it the
    1MB record reads ``(no output)``, which is a different false record for
    the same command.

    This is the row the windows CI legs failed on (run 35522838940), at
    ``51199 == 51200``: there the same ``print()`` ends in ``\\r\\n``, the
    ``removesuffix("\\n")`` the first cut used left the ``\\r`` behind, and it
    ate a byte of the cap. The table below drives both endings; this test
    keeps the name the failure was reported under.
    """

    harness, _ = await _harness_printing(_HUGE_OUTPUT)

    await handle_user_bash(
        harness, "print-one-huge-line", exclude_from_context=False, cwd=str(tmp_path)
    )

    recorded = _text(harness.messages[0])
    assert "(no output)" not in recorded
    # The whole long line would otherwise be dropped for the empty one below
    # it; what must survive is the cap's worth of it.
    body = _recorded_body(recorded)
    assert body.count("x") == _MAX_BYTES
    assert MARKER not in body, "this one's marker is at the HEAD, and cut"


#: Every shape of trailing line terminator, and the cap's own boundary.
#:
#: Five of these nine rows were wrong on the first cut of #299's cap, which
#: stripped one ``"\n"`` and asked ``truncate_tail`` the rest. Two of them lost
#: the WHOLE output behind the word ``(no output)`` — reachable from any
#: ordinary command that prints a trailing blank line, e.g. ``!python3 -c
#: "print('x'*60000); print()"``, where the user watches 60,000 characters go
#: past and the model is told the command printed nothing. One is the windows
#: CI failure. One is the cap failing to cap.
#:
#: They are one bug: a trailing line terminator is not a line, and the run of
#: them is not one character. The rows are stated as literal strings and driven
#: through :class:`_EmittingOps` because the difference between two of them is
#: which line ending a platform's child happens to write.
_TABLE: list[tuple[str, str, str, bool]] = [
    # (label, the command's output, the body the record carries, a notice?)
    ("empty", "", "", False),
    ("at the line cap", "\n" * 2000, "\n" * 2000, False),
    ("over the line cap", "\n" * 2001, "\n" * 1999, True),
    ("huge + LF", "x" * 60000 + "\n", "x" * _MAX_BYTES, True),
    ("huge + LF LF", "x" * 60000 + "\n\n", "x" * (_MAX_BYTES - 1) + "\n", True),
    ("huge + CRLF", "x" * 60000 + "\r\n", "x" * _MAX_BYTES, True),
    (
        "huge + CRLF CRLF",
        "x" * 60000 + "\r\n\r\n",
        "x" * (_MAX_BYTES - 1) + "\n",
        True,
    ),
    ("exactly at the cap", "x" * _MAX_BYTES, "x" * _MAX_BYTES, False),
    ("at the cap + LF", "x" * _MAX_BYTES + "\n", "x" * _MAX_BYTES, False),
]


@pytest.mark.parametrize(
    ("label", "output", "expected_body", "expected_notice"),
    _TABLE,
    ids=[row[0] for row in _TABLE],
)
async def test_the_recorded_body_for_every_trailing_terminator(
    tmp_path: Path,
    label: str,
    output: str,
    expected_body: str,
    expected_notice: bool,
) -> None:
    """The whole table, body and notice, one row at a time.

    The two CRLF rows are the ones that matter for the windows legs: asserting
    them here covers that behaviour on every platform instead of hoping the
    runner produces it. ``huge + LF`` and ``huge + CRLF`` record the SAME body,
    which is the point — what a command printed should not depend on which
    line ending the platform gave it.

    The last two rows are the cap's boundary. ``"x" * 50KB`` is recorded whole;
    ``"x" * 50KB + "\\n"`` is one byte over, and the first cut let it through at
    51,201 bytes with no notice at all. A trailing terminator is a line ending
    and not a line, so it is dropped rather than announced — but it is COUNTED,
    which is why that row cannot slip past the cap uncounted.
    """

    harness, _ = await _harness_printing(output)

    await handle_user_bash(harness, "c", exclude_from_context=False, cwd=str(tmp_path))

    recorded = _text(harness.messages[0])
    body = _recorded_body(recorded)
    assert body == expected_body, label
    assert ("[Showing" in recorded) is expected_notice, label
    # The invariant the whole exercise is for, asserted on every row.
    assert len(body.encode()) <= _MAX_BYTES, label


async def test_a_blank_last_line_does_not_swallow_an_ordinary_command(
    tmp_path: Path,
) -> None:
    """The same row again, spelled the way a user would meet it.

    ``print('x'*60000); print()`` — nothing exotic, and on the first cut of the
    cap it recorded ``(no output)`` for output the user had just watched go
    past. That is worse than the defect #299 was filed about, because it is
    silent and it looks like an answer.
    """

    harness, _ = await _harness_printing("x" * 60000 + "\n\n")

    await handle_user_bash(
        harness, "print-then-blank", exclude_from_context=False, cwd=str(tmp_path)
    )

    recorded = _text(harness.messages[0])
    assert "(no output)" not in recorded
    assert _recorded_body(recorded).count("x") == _MAX_BYTES - 1


async def test_the_truncation_details_count_the_whole_output(
    tmp_path: Path,
) -> None:
    """``original_bytes`` is the output's, not the sub-slice's.

    The first cut measured the string it had already stripped a newline from,
    so a 60,001-byte output was recorded as 60,000 — a number nothing else in
    the record could contradict.
    """

    harness, session = await _harness_printing("x" * 60000 + "\r\n")

    await handle_user_bash(harness, "c", exclude_from_context=False, cwd=str(tmp_path))

    (entry,) = await session.get_entries()
    truncation = entry.details["truncation"]  # type: ignore[index]
    assert truncation["original_bytes"] == len(("x" * 60000 + "\r\n").encode())  # type: ignore[index]
    assert truncation["kept_bytes"] == _MAX_BYTES  # type: ignore[index]


async def test_a_failing_command_is_distinguishable_from_a_passing_one(
    tmp_path: Path,
) -> None:
    """``BashOperations.exec`` returns an ``ExecExitResult``; use it.

    Both commands print nothing, so without the exit code they reach the model
    as the same two lines — a record that cannot tell failure from success is
    the defect #299 is about, one field over. pi appends the same sentence
    (``harness/messages.ts:70-73``).
    """

    passing, _ = await _memory_harness()
    failing, _ = await _memory_harness()

    # ``exit 0`` and not ``true``: ``exit`` is a builtin in every shell
    # ``_resolve_shell`` can pick, including the PowerShell a windows box gets
    # when ``$SHELL`` names nothing that exists (``tools/bash.py:150``).
    # ``true`` is a bash builtin, and on the windows runner it resolves only
    # because that image happens to put Git's ``usr/bin`` on PATH.
    await handle_user_bash(
        passing, "exit 0", exclude_from_context=False, cwd=str(tmp_path)
    )
    await handle_user_bash(
        failing, "exit 3", exclude_from_context=False, cwd=str(tmp_path)
    )

    ok = _text(passing.messages[0])
    bad = _text(failing.messages[0])
    assert ok != bad
    assert ok == "Ran `exit 0`\n(no output)", "zero stays silent, as in pi"
    assert bad == "Ran `exit 3`\n(no output)\n\nCommand exited with code 3"


def test_a_killed_command_reports_no_code_rather_than_a_wrong_one() -> None:
    """``exit_code=None`` means killed; pi's ``?? undefined`` prints nothing."""

    assert bash_execution_to_text("sleep 9", "", exit_code=None) == (
        "Ran `sleep 9`\n(no output)"
    )


async def test_an_extension_supplied_result_is_recorded_too(
    tmp_path: Path,
) -> None:
    """The ``result``-bearing short-circuit (``repl.py`` intercept branch).

    An extension that replaces execution never touches ``BashOperations``, so
    it is the one branch where the exit code cannot come from an
    ``ExecExitResult``. It is read off the extension's own result, the way
    ``output`` already was, and the record is written from that.
    """

    class _StubResult:
        output = f"{MARKER}-from-extension"
        exit_code = 7

    def handler(_event: Any, _ctx: Any) -> UserBashResult:
        return UserBashResult(result=_StubResult())  # type: ignore[arg-type]

    ext = Extension(name="t")
    ExtensionAPI(ext, _ExtensionRuntime()).on("user_bash", handler)
    session = Session(MemorySessionStorage())
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"), session=session, extensions=[ext]
        )
    )

    await handle_user_bash(
        harness, "would-not-run", exclude_from_context=False, cwd=str(tmp_path)
    )

    recorded = _text(harness.messages[0])
    assert f"{MARKER}-from-extension" in recorded
    assert "Command exited with code 7" in recorded
    branch = await session.get_branch()
    assert MARKER in _joined(build_session_context(branch).messages)


def _capturing_stream(seen: list[list[Any]]) -> Any:
    async def fn(
        _m: Model, ctx: Context, _o: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(list(ctx.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


async def test_a_failed_session_write_costs_this_turn_as_well(
    tmp_path: Path,
) -> None:
    """What the PROVIDER sees, not what ``_state.messages`` holds.

    This is the assertion the fix pass was missing. ``AgentHarness._run``
    derives the turn's messages from ``session.build_context()`` whenever a
    session is attached (``harness/core.py:4543-4546``, pinned by
    ``tests/test_state_messages_derived.py``), so the live append is not a
    second chance at the same turn: when the entry cannot be written, the
    output reaches nothing. The suppression is deliberate — an unwritable
    session must not take the REPL down over a ``!`` line — but the comment
    that called it "the next resume, not this turn" was false, and only a test
    that reads ``Context.messages`` can tell the two apart.
    """

    seen: list[list[Any]] = []
    session = Session(MemorySessionStorage())

    async def refuse(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("no space left on device (injected)")

    session.append_custom_message_entry = refuse  # type: ignore[method-assign]
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            session=session,
            stream_fn=_capturing_stream(seen),
        )
    )

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )
    assert MARKER in _joined(harness.messages), "the REPL survived the failure"

    await harness.prompt("what did that print?")

    (context_messages,) = seen
    assert MARKER not in _joined(context_messages)


async def test_without_a_session_the_live_append_is_what_reaches_the_provider(
    tmp_path: Path,
) -> None:
    """The other half of the same measurement — why the live append stays.

    On ``--no-session`` there is no entry to write and ``_state.messages`` IS
    the turn's list (``harness/core.py:4547-4549``), so the append is the only
    thing carrying the output. Delete it and this test fails while the
    session-backed ones still pass.
    """

    seen: list[list[Any]] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", api="anthropic"),
            stream_fn=_capturing_stream(seen),
        )
    )

    await handle_user_bash(
        harness, f"echo {MARKER}", exclude_from_context=False, cwd=str(tmp_path)
    )
    await harness.prompt("what did that print?")

    (context_messages,) = seen
    assert MARKER in _joined(context_messages)


async def test_double_bang_stays_excluded_at_every_size_and_exit(
    tmp_path: Path,
) -> None:
    """The guard the cap and the exit code must not have loosened.

    ``!!`` returns before any of it, so a big output and a non-zero exit are
    the two inputs most likely to reach a new early-return by accident.
    """

    for output, exit_code in ((_HUGE_OUTPUT, 0), ("", 3), (MARKER + "\n", 0)):
        harness, session = await _harness_printing(output, exit_code=exit_code)
        await handle_user_bash(
            harness, "c", exclude_from_context=True, cwd=str(tmp_path)
        )
        assert harness.messages == [], output[:20]
        assert await session.get_branch() == [], output[:20]


async def test_the_command_is_capped_too(tmp_path: Path) -> None:
    """The other unbounded thing a ``!`` line can put in the context.

    A ``!`` line whose OUTPUT is empty still writes its command into the
    record, and the record is re-sent on every later turn — so ``!#`` followed
    by 60,000 characters produced a 60,019-byte model message that the output
    cap never looked at. ADR-0242 rule 1.5's amended sentence covers it: what a
    ``CustomMessageEntry`` records is context, so it is bounded at the writer.

    A DIVERGENCE from pi, which renders ``Ran `${msg.command}` `` verbatim
    (``harness/messages.ts:63-64``); ADR-0235 makes that a judgement rather
    than a bug. The cap is far below the output's because a ``!`` line is one
    command typed into a one-line prompt.
    """

    harness, session = await _harness_printing("")

    await handle_user_bash(
        harness, "#" + "x" * 60000, exclude_from_context=False, cwd=str(tmp_path)
    )

    recorded = _text(harness.messages[0])
    assert len(recorded) < _MAX_COMMAND_CHARS * 2, len(recorded)
    assert recorded.count("x") == _MAX_COMMAND_CHARS - 1, "the leading '#' is one"
    assert "... [truncated]" in recorded, "the cut is visible, not silent"
    # The stored copy is bounded too — the entry is what a resume re-sends.
    (entry,) = await session.get_entries()
    assert len(str(entry.details["command"])) < _MAX_COMMAND_CHARS * 2  # type: ignore[index]


def test_the_caps_are_pis_own_numbers_for_this_path() -> None:
    """Not a round number someone liked: ``truncate.ts:11-12``.

    ``_MAX_COMMAND_CHARS`` is not one of pi's — pi does not cap the command —
    so it is asserted separately, as the deliberate divergence it is.
    """

    assert (_MAX_LINES, _MAX_BYTES) == (2000, 50 * 1024)
    assert _MAX_COMMAND_CHARS == 1024
