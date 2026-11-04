"""#684 — a swap sent right after an Esc (RPC ``abort``) no longer keeps the input the Esc withdrew.

RPC runs each command in a task of its own, so a ``new_session`` sent with an
``abort`` can start before the cancelled turn has unwound. The swap's teardown
disposed the harness, and ``dispose()`` aborts a running turn with the default
``withdraw_unanswered=False`` — the last abort wins (ADR-0286 §2), so the Esc's
request was overwritten: no ``input_withdrawn`` went out and the old session's
path still ended with the cancelled prompt. ``reload`` waits for idle first and
was not affected.

Now a swap first lets a turn an Esc is ending finish giving its input back, before
it reads the session it leaves (``/clone`` copies it) or takes it apart. A swap
with no Esc pending tears down as before: ``dispose`` keeps the input (§2).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model

MODEL = Model(id="fixture", provider="fixture")


class _Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        self.runtime: AgentSessionRuntime
        self.session: Session
        self.other: Session

    async def _stream(self, model: Any, context: Any, options: Any) -> Any:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        self.started.set()
        await self.gate.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="late")], stop_reason="end_turn")
        )

    def build(self, session: Session) -> AgentHarness:
        return AgentHarness(
            AgentHarnessOptions(
                model=MODEL, stream_fn=self._stream, session=session, cwd=str(self.tmp)
            )
        )

    async def factory(self, session: Session, **_kw: Any) -> AgentHarness:
        return self.build(session)

    @classmethod
    async def create(cls, tmp: Path) -> _Rig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        rig.other = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await rig.other.append_message(UserMessage(content=[TextContent(text="elsewhere")]))
        rig.session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        await rig.session.append_message(UserMessage(content=[TextContent(text="hello")]))
        rig.runtime = AgentSessionRuntime(rig.build(rig.session), rig.factory, repo=repo, fs=fs)
        return rig


async def _texts(session: Session) -> list[str]:
    out = []
    for entry in await session.get_branch():
        if entry.type == "message":
            message: Any = entry.message  # type: ignore[attr-defined]
            out.append("".join(getattr(c, "text", "") for c in message.content) or message.role)
    return out


async def _swap(rig: _Rig, how: str) -> None:
    runtime = rig.runtime
    if how == "new_session":
        await runtime.new_session()
    elif how == "switch_session":
        assert rig.other.session_file is not None
        await runtime.switch_session(rig.other.session_file)
    elif how in ("fork", "clone"):
        first = (await rig.session.get_branch())[0]
        await runtime.fork(first.id, position="before" if how == "fork" else "at")
    elif how == "import":
        assert rig.other.session_file is not None
        copy = rig.tmp / "import-me.jsonl"
        copy.write_text(Path(rig.other.session_file).read_text())
        await runtime.import_from_jsonl(str(copy))
    else:
        await runtime.reload()


@pytest.mark.parametrize(
    "how", ["new_session", "switch_session", "fork", "clone", "import", "reload"]
)
async def test_a_swap_right_after_an_esc_keeps_the_esc(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    events: list[Any] = []
    harness.subscribe(lambda e: events.append(e) if e.type == "input_withdrawn" else None)
    turn = asyncio.create_task(harness.prompt("CANCEL-ME"))
    await asyncio.wait_for(rig.started.wait(), 5)
    await harness.steer("STEERED")

    # As RPC dispatches them: the abort and the swap, each its own task, sent together.
    abort = asyncio.create_task(harness.abort(withdraw_unanswered=True))
    swap = asyncio.create_task(_swap(rig, how))
    await asyncio.wait_for(asyncio.gather(abort, swap), 10)
    await asyncio.wait_for(turn, 10)

    given_back = [
        ["".join(getattr(c, "text", "") for c in m.content) for m in e.messages] for e in events
    ]
    assert given_back == [["CANCEL-ME", "STEERED"]]
    assert events[0].prompt_withdrawn is True
    # The session it left ends where it was before the cancelled prompt.
    assert await _texts(rig.session) == ["hello"]
    if how == "clone":
        # And the clone made after the Esc does not carry it.
        clone = rig.runtime.session
        assert clone is not None and await _texts(clone) == ["hello"]


async def test_a_swap_with_no_esc_keeps_the_running_prompt(tmp_path: Path) -> None:
    """ADR-0286 §2: a teardown that nobody asked to withdraw keeps the input."""

    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    events: list[Any] = []
    harness.subscribe(lambda e: events.append(e) if e.type == "input_withdrawn" else None)
    turn = asyncio.create_task(harness.prompt("KEEP-ME"))
    await asyncio.wait_for(rig.started.wait(), 5)

    await asyncio.wait_for(rig.runtime.new_session(), 10)
    await asyncio.wait_for(turn, 10)

    assert events == []
    assert (await _texts(rig.session))[:2] == ["hello", "KEEP-ME"]


# === Codex review r1: an Esc while ``before_agent_start`` still runs (#336's window) ===


def _park(harness: AgentHarness) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold the next prompt in its ``before_agent_start`` handler: accepted, no loop yet."""

    entered, release = asyncio.Event(), asyncio.Event()

    async def _held(_event: Any, _ctx: Any) -> None:
        entered.set()
        await release.wait()

    harness.hooks.on("before_agent_start", _held)
    return entered, release


@pytest.mark.parametrize("how", ["new_session", "switch_session", "fork", "clone", "import"])
async def test_an_esc_while_before_agent_start_runs_is_kept_by_the_swap(
    tmp_path: Path, how: str
) -> None:
    """The prompt had no turn to cancel yet; ``_run`` cleared the Esc and sent it.

    Measured before (Codex review r1): no ``input_withdrawn``, the model was
    asked, the session left behind ended with the prompt and its answer, and a
    clone copied both.
    """

    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    entered, release = _park(harness)
    events: list[Any] = []
    harness.subscribe(lambda e: events.append(e) if e.type == "input_withdrawn" else None)
    turn = asyncio.create_task(harness.prompt("CANCEL-ME"))
    await asyncio.wait_for(entered.wait(), 5)

    await harness.abort(withdraw_unanswered=True)
    swap = asyncio.create_task(_swap(rig, how))
    release.set()
    await asyncio.wait_for(asyncio.gather(swap, turn), 5)

    assert [(_shape(e.messages), e.prompt_withdrawn) for e in events] == [(["CANCEL-ME"], True)]
    assert not rig.started.is_set(), "the model was asked"
    assert await _texts(rig.session) == ["hello"]
    if how == "clone":
        clone = rig.runtime.session
        assert clone is not None and await _texts(clone) == ["hello"]


async def test_a_swap_while_before_agent_start_runs_does_not_wait_for_an_answer(
    tmp_path: Path,
) -> None:
    """No Esc: the teardown's own abort ends the turn as it starts and keeps its input (§2).

    It was cleared the same way, so the turn went to the model and the swap
    waited for the whole answer — with a model that holds, for ever (verify r1,
    ``p684_stale_rpc-slowext``: no answer in 12 s).
    """

    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    entered, release = _park(harness)
    events: list[Any] = []
    harness.subscribe(lambda e: events.append(e) if e.type == "input_withdrawn" else None)
    turn = asyncio.create_task(harness.prompt("KEEP-ME"))
    await asyncio.wait_for(entered.wait(), 5)

    swap = asyncio.create_task(rig.runtime.new_session())
    while not harness.disposed:  # the teardown's abort is made
        await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(swap, turn), 5)

    assert not rig.started.is_set(), "the model was asked"
    assert events == []
    assert await _texts(rig.session) == ["hello", "KEEP-ME"]


def _shape(messages: Any) -> list[str]:
    return ["".join(getattr(c, "text", "") for c in m.content) for m in messages]
