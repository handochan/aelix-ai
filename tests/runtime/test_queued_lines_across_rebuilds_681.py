"""#681 — steering and follow-up lines queued while idle survive a ``/reload`` and come back on a swap.

``steer`` and ``follow_up`` queue on the harness also while it is idle (pi
parity), and the TUI's #345 path leaves lines queued when the turn they waited
on ends in an error. ``/reload``, ``/new``, ``/resume``, ``/fork``, ``/clone`` and
``/import`` install a new harness; the queues stayed on the torn-down one, and a
torn-down idle harness is not aborted, so nothing gave the lines back either:
RPC answered ``success: true`` and ``pendingMessageCount`` went from 1 to 0.

Now a reload carries them to the rebuilt harness (pi's ``reload`` keeps its
``AgentSession`` and its queue), and so does the build that keeps the session
after a failed one; a swap gives them back as an Esc does — ``input_withdrawn``
with ``prompt_withdrawn`` false — before the old harness is taken apart. A swap
an extension cancels leaves them queued, and a stopped runtime keeps them on
the harness it holds (ADR-0289 D4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import SessionBeforeSwitchResult
from aelix_agent_core.runtime import RebuildFailedError

from tests.runtime.test_failed_rebuild_663 import _always, _once, _Rig


def _texts(messages: Any) -> list[str]:
    out = []
    for m in messages:
        content = getattr(m, "content", None) or []
        out.append("".join(getattr(c, "text", "") for c in content))
    return out


async def _queue(rig: _Rig) -> None:
    harness = rig.runtime.harness
    await harness.steer("STEER-IDLE")
    await harness.follow_up("FOLLOW-IDLE")
    assert harness.pending_message_count == 2


def _withdrawn(rig: _Rig) -> list[Any]:
    events: list[Any] = []
    rig.runtime.harness.subscribe(
        lambda e: events.append(e) if getattr(e, "type", None) == "input_withdrawn" else None
    )
    return events


async def test_a_reload_carries_the_queued_lines(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    await _queue(rig)
    events = _withdrawn(rig)

    await rig.runtime.reload()

    new = rig.runtime.harness
    assert new is not old
    assert new.pending_message_count == 2
    assert _texts(new._steering_queue._messages) == ["STEER-IDLE"]
    assert _texts(new._follow_up_queue._messages) == ["FOLLOW-IDLE"]
    assert old.pending_message_count == 0
    assert events == []

    # And they reach the model with the next prompt, as they did without the reload.
    await new.prompt("next")
    assert "FOLLOW-IDLE" in _texts(m for m in new.messages if getattr(m, "role", None) == "user")
    assert new.pending_message_count == 0


async def test_a_reload_carries_the_next_turn_messages(tmp_path: Path) -> None:
    """pi keeps ``_pendingNextTurnMessages`` across a reload too (one ``AgentSession``)."""

    from aelix_ai.messages import TextContent, UserMessage

    rig = await _Rig.create(tmp_path)
    marker = UserMessage(content=[TextContent(text="NEXT-TURN")])
    rig.runtime.harness._next_turn_queue.append(marker)

    await rig.runtime.reload()

    assert rig.runtime.harness._next_turn_queue == [marker]


async def test_a_kept_reload_carries_them_to_the_restored_harness(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    await _queue(rig)
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    assert caught.value.state == "kept"
    assert rig.runtime.harness.pending_message_count == 2


async def test_a_stopped_reload_keeps_them_on_the_harness_it_holds(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    await _queue(rig)
    rig.fail = _always(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()

    assert caught.value.state == "stopped"
    assert rig.runtime.harness is old and old.pending_message_count == 2


async def _swap(rig: _Rig, how: str) -> None:
    runtime = rig.runtime
    if how == "new":
        await runtime.new_session()
    elif how == "resume":
        assert rig.other.session_file is not None
        await runtime.switch_session(rig.other.session_file)
    elif how in ("fork", "clone"):
        entry = (await rig.session.get_entries())[-1]
        await runtime.fork(entry.id, position="before" if how == "fork" else "at")
    else:
        assert rig.other.session_file is not None
        copy = rig.tmp / "import-me.jsonl"
        copy.write_text(Path(rig.other.session_file).read_text())
        await runtime.import_from_jsonl(str(copy))


@pytest.mark.parametrize("how", ["new", "resume", "fork", "clone", "import"])
async def test_a_swap_gives_the_queued_lines_back(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    await _queue(rig)
    events = _withdrawn(rig)

    await _swap(rig, how)

    assert rig.runtime.harness is not old
    assert rig.runtime.harness.pending_message_count == 0
    assert [(_texts(e.messages), e.prompt_withdrawn) for e in events] == [
        (["STEER-IDLE", "FOLLOW-IDLE"], False)
    ]
    # Nothing of them reached either session.
    for session in (rig.session, rig.runtime.session):
        assert session is not None
        written = [e for e in await session.get_entries() if e.type == "message"]
        assert not {"STEER-IDLE", "FOLLOW-IDLE"} & set(_texts(e.message for e in written))  # type: ignore[attr-defined]


async def test_a_swap_that_fails_still_gave_them_back(tmp_path: Path) -> None:
    """The swap began: they were given back before the teardown, and the session it kept has none."""

    rig = await _Rig.create(tmp_path)
    await _queue(rig)
    events = _withdrawn(rig)
    rig.fail = _once(RuntimeError("build boom"))

    with pytest.raises(RebuildFailedError):
        await rig.runtime.new_session()

    assert len(events) == 1
    assert rig.runtime.harness.pending_message_count == 0


async def test_a_swap_an_extension_cancels_leaves_them_queued(tmp_path: Path) -> None:
    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness

    async def _cancel(_event: Any, _ctx: Any) -> SessionBeforeSwitchResult:
        return SessionBeforeSwitchResult(cancel=True)

    harness.hooks.on("session_before_switch", _cancel)
    await _queue(rig)
    events = _withdrawn(rig)

    result = await rig.runtime.new_session()

    assert result.cancelled is True
    assert rig.runtime.harness is harness and harness.pending_message_count == 2
    assert events == []


# === Codex review r1: the next-turn queue, and what the teardown queues ===


@pytest.mark.parametrize("how", ["new", "resume", "fork", "clone", "import"])
async def test_a_swap_gives_back_the_users_next_turn_lines_first(tmp_path: Path, how: str) -> None:
    """User messages on the next-turn queue come back ahead of the steer and follow-up lines.

    They went in front of the next prompt, so they come back first — a text an
    extension queued there too, as its steer and follow-up lines do. An
    extension's custom message is no line anyone could type: it goes with the
    old harness, as pi's new ``AgentSession`` has no ``_pendingNextTurnMessages``.
    """

    from aelix_agent_core.session.context import CustomMessage

    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    await old.next_turn("NEXT-TURN")
    custom = CustomMessage(
        custom_type="ext", content="EXT-DATA", display=False, details=None, timestamp=None
    )
    old._action_send_message(custom, deliver_as="next_turn")
    old._action_send_message("EXT-TEXT", deliver_as="next_turn")
    await _queue(rig)
    events = _withdrawn(rig)

    await _swap(rig, how)

    assert [(_texts(e.messages), e.prompt_withdrawn) for e in events] == [
        (["NEXT-TURN", "EXT-TEXT", "STEER-IDLE", "FOLLOW-IDLE"], False)
    ]
    assert old._next_turn_queue == [custom]
    assert rig.runtime.harness._next_turn_queue == []


async def test_a_prompt_the_teardown_cancelled_before_it_committed_comes_back(
    tmp_path: Path,
) -> None:
    """The swap's abort lands in ``agent_start``: the prompt is requeued (#320), then given back.

    It was requeued on the next-turn queue of the harness being taken apart,
    after the swap had given that harness's queues back: lost, with no event.
    """

    import asyncio

    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    started = asyncio.Event()

    async def _held(_event: Any, _ctx: Any) -> None:
        started.set()
        await asyncio.Event().wait()

    old.hooks.on("agent_start", _held)
    events = _withdrawn(rig)
    turn = asyncio.create_task(old.prompt("TYPED"))
    await asyncio.wait_for(started.wait(), 5)

    await asyncio.wait_for(rig.runtime.new_session(), 5)
    await asyncio.wait_for(turn, 5)

    assert [(_texts(e.messages), e.prompt_withdrawn) for e in events] == [(["TYPED"], False)]
    assert rig.requests == 0
    assert old._next_turn_queue == []


async def test_a_line_a_session_shutdown_handler_queues_comes_back_too(tmp_path: Path) -> None:
    """Codex review r1: queued after the first give-back, on a harness about to be taken apart."""

    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness

    async def _shutdown(_event: Any, _ctx: Any) -> None:
        await old.follow_up("SHUTDOWN-LINE")

    old.hooks.on("session_shutdown", _shutdown)
    events = _withdrawn(rig)

    await rig.runtime.new_session()

    assert [(_texts(e.messages), e.prompt_withdrawn) for e in events] == [
        (["SHUTDOWN-LINE"], False)
    ]
    assert old.pending_message_count == 0
    assert rig.runtime.harness.pending_message_count == 0
