"""Issue #194 — a failed turn is written into the session twice, so ``/resume``
shows it twice.

The persisted half of the pair whose live half lives in
``tests/tui/test_first_run_no_provider.py``. #189 fixed the LIVE double-print
at the shell (renderer prints the synthesised failure, shell re-prints the
re-raised exception) and that fix holds; this file is about what is left on
disk afterwards.

``harness/core.py`` builds the failure message with the same string in two
fields::

    failure = AssistantMessage(
        content=[TextContent(text=f"[error] {exc}")],
        stop_reason="error",
        error_message=str(exc),
    )

Both persist. ``EventRenderer.replay`` walks ``content`` (through Markdown, so
a provider message's backticks come back as code spans) and then commits the
stop-reason line from ``error_message`` — one sentence, two renderings.

MEASURED on the branch base ``fad2e28``, real harness + real JSONL session
(``.omc/specs/194-measure.py``)::

    LIVE turn emissions:  1
    REPLAY emissions:     2

WHY THE FIX IS IN ``replay`` AND NOT IN THE WRITER, and why that split is what
these tests are shaped around:

* every session already on disk carries both fields. A writer-only change
  cannot reach them, which is what ``test_a_persisted_failure_replays_once``
  pins — it never calls the harness at all, it builds the on-disk shape by
  hand;
* the ``content`` copy is what the MODEL reads on the next turn. Measured
  through a real harness: the ``[error] …`` text is in the
  ``Context.messages`` of the following ``prompt()`` and in
  ``build_session_context`` after a reload. Dropping it at the writer to save
  a line of screen would take the failure out of the model's context.

THE BOUNDARY THE FIRST DRAFT OF THE FIX GOT WRONG, and the reason the guard
reads ``if err is not None`` rather than ``if err``: ``str(exc)`` is ``""`` for
any exception raised with no message, so the writer itself emits
``error_message=""`` beside the body ``"[error] "``. A truth test read that as
"no error message at all" and put the echo back on screen — the defect, intact,
for exactly the failures carrying the least information. Both sides of that
boundary are pinned here
(``…_whose_exception_carried_no_message_replays_once`` and
``…_with_no_error_message_at_all_keeps_its_body``), the first of them also end
to end through a real harness, because "is this shape reachable?" was the
question.

AN INSTRUMENT TRAP, AND THE FIRST DRAFT OF THIS FILE FELL INTO IT. The Markdown
emission is wrapped to the replay width, so a long error sentence is broken
across lines inside that one commit. A plain ``sentence in commit`` therefore
finds nothing on the BROKEN build — the duplicate is on screen, the ruler
cannot see it. Measured: the end-to-end test below, written that way, PASSED
against the unfixed renderer. Every search here goes through :func:`_flat`,
which collapses whitespace first, and the emissions are asserted by identity
(``== [f"✖ {…}"]``) rather than by tally.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.session.context import build_display_messages
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.tui.render import EventRenderer

MARKER = "MARKER194"


def _plain(renderable: Any) -> str:
    rows = getattr(renderable, "renderables", None)
    if rows is not None:
        return "\n".join(_plain(r) for r in rows)
    inner = getattr(renderable, "renderable", None)
    if inner is not None:
        return _plain(inner)
    text = getattr(renderable, "plain", None)
    return text if isinstance(text, str) else str(renderable)


def _norm(text: str) -> str:
    """Drop the Markdown renderer's right-hand padding, keep everything else.

    ``markdown_lines`` pads every line out to the replay width, so a committed
    block compares unequal to the source string by a run of trailing spaces
    that no reader can see. Stripping per LINE (not the whole string) keeps a
    wrap visible as a ``\\n``.
    """

    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def _renderer() -> tuple[EventRenderer, list[str]]:
    commits: list[str] = []
    return (
        EventRenderer(
            commit=lambda r: commits.append(_norm(_plain(r))),
            set_tail=lambda _s: None,
            width=80,
        ),
        commits,
    )


def _flat(text: str) -> str:
    """Collapse every run of whitespace, so a wrapped line still matches.

    This is the instrument fix described in the module docstring: without it a
    search for a long error sentence misses the Markdown copy entirely, because
    ``markdown_lines`` broke the sentence across lines. Measured — with a plain
    ``in``, ``test_a_real_failed_turn_reloaded_from_disk_replays_once`` PASSED
    against the unfixed renderer while two emissions were on screen.
    """

    return " ".join(text.split())


def _mentions(commits: list[str], needle: str = MARKER) -> list[str]:
    flat_needle = _flat(needle)
    return [_flat(c) for c in commits if flat_needle in _flat(c)]


def _failure_message(error: str) -> AssistantMessage:
    """The exact shape ``harness/core.py`` writes, and every session holds."""

    return AssistantMessage(
        content=[TextContent(text=f"[error] {error}")],
        stop_reason="error",
        error_message=error,
    )


# === The on-disk shape — what a writer-only fix cannot reach =================


def test_a_persisted_failure_replays_once() -> None:
    """The backward-compatibility lane: no writer is involved anywhere here.

    Every session file released before this fix holds a message with BOTH
    fields populated. Change the writer however you like and this message is
    still what ``/resume`` loads, so this is the assertion that says the fix
    landed on the reading side.
    """

    error = f"the wheels came off {MARKER}"
    renderer, commits = _renderer()

    renderer.replay([_failure_message(error)])

    assert _mentions(commits) == [f"✖ {error}"], commits


def test_the_surviving_rendering_is_the_live_one() -> None:
    """Of the two spellings, the one that stays is the one live turns show.

    ``_render_message_error`` commits ``✖ {error_message}`` in bold red during a
    live turn. If replay had kept the Markdown copy instead, a resumed
    transcript would disagree with the transcript it is supposed to reproduce —
    and the ``[error] `` prefix, an internal marker, would be the user-visible
    one.
    """

    error = f"boom {MARKER}"
    renderer, commits = _renderer()

    renderer.replay([_failure_message(error)])

    assert commits == [f"✖ {error}"]
    assert not any(c.startswith("[error]") for c in commits), commits


def test_a_writer_that_stopped_echoing_still_shows_the_failure() -> None:
    """The other direction, and the one that must never regress to zero.

    If the writer is ever changed to leave ``content`` empty, replay must still
    report the turn. The suppression is allowed to turn 2 into 1; it is never
    allowed to turn 1 into 0.
    """

    error = f"nothing in content {MARKER}"
    renderer, commits = _renderer()

    renderer.replay(
        [AssistantMessage(content=[], stop_reason="error", error_message=error)]
    )

    assert _mentions(commits) == [f"✖ {error}"], commits


# === Narrowness — the sabotage lane =========================================


def test_a_partial_answer_before_a_provider_error_still_replays() -> None:
    """The failure mode of an over-broad fix, and the reason the match is EXACT.

    An adapter-reported failure is NOT synthesised: ``providers/anthropic.py``
    snapshots whatever the model actually streamed onto the error message
    (``content=list(output_content)``) before yielding ``AssistantErrorEvent``.
    That text is the only record of what the turn produced, and it has no
    ``[error] `` prefix.

    "Skip every text block when ``stop_reason == 'error'``" passes every test
    above and silently eats this one — which is what this test exists to stop.
    """

    renderer, commits = _renderer()

    renderer.replay(
        [
            AssistantMessage(
                content=[TextContent(text=f"I was saying something {MARKER}")],
                stop_reason="error",
                error_message=f"connection reset {MARKER}",
            )
        ]
    )

    assert commits == [
        f"I was saying something {MARKER}",
        f"✖ connection reset {MARKER}",
    ], commits


def test_a_text_block_that_merely_starts_with_the_prefix_replays() -> None:
    """``startswith`` and ``in`` are both too loose; only equality is right."""

    error = f"short {MARKER}"
    renderer, commits = _renderer()

    renderer.replay(
        [
            AssistantMessage(
                content=[TextContent(text=f"[error] {error} — and then some more")],
                stop_reason="error",
                error_message=error,
            )
        ]
    )

    assert _mentions(commits) == [
        f"[error] {error} — and then some more",
        f"✖ {error}",
    ], commits


def test_a_successful_turn_that_talks_about_errors_is_untouched() -> None:
    """Not ``stop_reason == "error"``, so nothing is suppressed whatever it says.

    Both spellings of "not an error" are here. ``stop_reason=None`` is the
    COMMON one — most persisted assistant messages carry no stop reason at all
    — and it is the shape a guard that forgot to test ``stop`` would eat first.
    """

    for stop in ("done", None):
        renderer, commits = _renderer()

        renderer.replay(
            [
                AssistantMessage(
                    content=[TextContent(text=f"[error] {MARKER}")],
                    stop_reason=stop,
                )
            ]
        )

        assert commits == [f"[error] {MARKER}"], (stop, commits)


# === The boundary the first draft of this fix got wrong (review) ============


def test_a_failure_whose_exception_carried_no_message_replays_once() -> None:
    """``error_message=""`` is a shape the WRITER produces, not a hypothetical.

    ``harness/core.py`` writes ``error_message=str(exc)``, and ``str(exc)`` is
    ``""`` for any exception raised with no message — a bare ``TimeoutError()``,
    a third-party sentinel ``raise Stop()``. The body it is paired with is then
    ``"[error] "``.

    The first draft of this fix tested ``if err:``, which reads that as "no
    error message at all", so the suppression switched itself OFF for exactly
    the failures that carry the least information: the ``[error]`` body came
    back AND the stop-reason line degraded to ``✖ request error``, so the two
    lines were no longer even the same sentence. Measured on that draft
    (``.omc/specs/194-measure.py`` ARM 4): REPLAY 2, LIVE 1.
    """

    renderer, commits = _renderer()

    renderer.replay(
        [
            AssistantMessage(
                content=[TextContent(text="[error] ")],
                stop_reason="error",
                error_message="",
            )
        ]
    )

    # Exactly what the LIVE path shows for the same message:
    # ``_render_message_error`` falls back to ``f"request {stop_reason}"``.
    assert commits == ["✖ request error"], commits


def test_a_failure_with_no_error_message_at_all_keeps_its_body() -> None:
    """The other side of that boundary — the guard is not "always suppress".

    ``error_message=None`` means there is no echo to match: the ``✖`` line has
    nothing to say but ``✖ request error``, so the body is the only record of
    what happened and has to survive. This repo's writer never produces the
    shape (``str(exc)`` is always a ``str``), but a third-party or hand-edited
    session can, and suppressing there is the "1 into 0" direction.
    """

    renderer, commits = _renderer()

    renderer.replay(
        [
            AssistantMessage(
                content=[TextContent(text=f"[error] {MARKER}")],
                stop_reason="error",
                error_message=None,
            )
        ]
    )

    assert commits == [f"[error] {MARKER}", "✖ request error"], commits


# === End to end — a real harness, a real session file, a real reload ========


async def _truncated_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """A provider that starts a message and then just stops.

    No ``done`` / ``error`` / ``end`` event, which is the contract violation
    ``loop.py`` raises ``RuntimeError("The model stream ended without a
    result: …")`` for. It is the failure that still ESCAPES the agent loop
    after #189: #189's own case is now refused by the runnability gate in
    ``tui/shell.py`` and never reaches the loop at all.
    """

    yield AssistantStartEvent()


async def test_a_real_failed_turn_reloaded_from_disk_replays_once() -> None:
    """The measured defect, end to end: harness → JSONL → reload → replay.

    Everything between the exception and the glass is production code — the
    harness's synthesis, ``session.append_message``, the JSONL round trip, and
    the display tier ``tui/shell.py`` feeds ``replay``.
    """

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "s.jsonl")
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="t194"
        )
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="m", api="anthropic"),
                session=Session(storage),
                stream_fn=_truncated_stream,
            )
        )

        live_renderer, live_commits = _renderer()
        harness.subscribe(live_renderer.on_agent_event)

        raised: Exception | None = None
        try:
            await harness.prompt(f"hello {MARKER}")
        except Exception as exc:  # noqa: BLE001 — the loop is expected to raise
            raised = exc
        assert raised is not None, "the truncated stream must escape the loop"
        detail = str(raised)

        reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
        messages = list(build_display_messages(await Session(reopened).get_branch()))
        replay_renderer, replay_commits = _renderer()
        replay_renderer.replay(messages)

    # The live turn reported the failure exactly once, and the reload agrees
    # with it — replay was brought up to the live path, not the other way.
    assert _mentions(live_commits, detail) == [f"✖ {detail}"], live_commits
    assert _mentions(replay_commits, detail) == [f"✖ {detail}"], replay_commits


async def _bare_timeout_stream(
    model: Model, context: Context, options: SimpleStreamOptions
) -> AsyncIterator[AssistantMessageEvent]:
    """A provider that raises an exception carrying NO message.

    ``str(TimeoutError())`` is ``""``, so the harness synthesises
    ``error_message=""`` beside ``content=[TextContent(text="[error] ")]``.
    Nothing is hand-written: this is the writer's own output for an ordinary
    exception, and ``asyncio`` timeouts and third-party sentinels arrive the
    same way.
    """

    yield AssistantStartEvent()
    raise TimeoutError


async def test_a_real_message_less_exception_reloaded_from_disk_replays_once() -> None:
    """The review blocker, end to end — the REACHABILITY of ``error_message=""``.

    The by-hand unit above pins the rendering; this pins that the harness
    really writes that shape, through a real JSONL round trip. Asserted by
    IDENTITY and not by tally, because there is no error sentence to search
    for — that absence is the whole case.
    """

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "s.jsonl")
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="t194c"
        )
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="m", api="anthropic"),
                session=Session(storage),
                stream_fn=_bare_timeout_stream,
            )
        )

        live_renderer, live_commits = _renderer()
        harness.subscribe(live_renderer.on_agent_event)

        raised: BaseException | None = None
        try:
            await harness.prompt(f"hello {MARKER}")
        except Exception as exc:  # noqa: BLE001 — the loop is expected to raise
            raised = exc
        assert raised is not None, "the bare TimeoutError must escape the loop"
        assert str(raised) == "", f"the point of this test is an empty str(exc): {raised!r}"

        reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
        messages = list(build_display_messages(await Session(reopened).get_branch()))
        replay_renderer, replay_commits = _renderer()
        replay_renderer.replay(messages)

    # The writer really does produce the empty field beside the bare body.
    failures = [m for m in messages if getattr(m, "stop_reason", None) == "error"]
    assert len(failures) == 1, messages
    assert failures[0].error_message == "", failures[0].error_message
    assert [b.text for b in failures[0].content] == ["[error] "], failures[0].content

    def _assistant(commits: list[str]) -> list[str]:
        return [_flat(c) for c in commits if _flat(c) and not _flat(c).startswith("»")]

    assert _assistant(live_commits) == ["✖ request error"], live_commits
    assert _assistant(replay_commits) == ["✖ request error"], replay_commits


async def test_the_model_still_sees_the_failure_on_the_next_turn() -> None:
    """Why the ``content`` copy is kept rather than deleted at the writer.

    ``harness/core.py`` puts the failure in ``content`` so the message is not
    empty AND so the model reads what went wrong. The second purpose is real,
    and this is the measurement of it: the persisted ``[error] …`` text is in
    the context a resumed turn would send.
    """

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "s.jsonl")
        storage = await JsonlSessionStorage.create(
            LocalFileSystem(), path, cwd=tmp, session_id="t194b"
        )
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="m", api="anthropic"),
                session=Session(storage),
                stream_fn=_truncated_stream,
            )
        )
        with contextlib.suppress(Exception):  # the loop is expected to raise
            await harness.prompt(f"hello {MARKER}")

        reopened = await JsonlSessionStorage.open(LocalFileSystem(), path)
        branch = await Session(reopened).get_branch()

    from aelix_agent_core.session.context import build_session_context

    def _text(message: object) -> str:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return ""
        return "".join(getattr(b, "text", "") or "" for b in content)

    resumed = build_session_context(branch).messages
    errors = [
        m
        for m in resumed
        if isinstance(m, AssistantMessage) and m.stop_reason == "error"
    ]
    assert len(errors) == 1, resumed
    assert _text(errors[0]).startswith("[error] "), _text(errors[0])
    assert errors[0].error_message and errors[0].error_message in _text(errors[0])
    # And the user turn that provoked it is still in front of it.
    assert isinstance(resumed[0], UserMessage)
