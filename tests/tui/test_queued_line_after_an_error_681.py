"""#681 — the TUI's #345 path: a line steered into a turn that ends in an error.

The loop stops on an error without draining the steering queue (#345), so the
line stays queued on an idle harness — ``1 queued`` in the footer — and the
next prompt takes it. ``/reload`` and every session swap build a new harness,
and the line stayed on the torn-down one: lost, with nothing said. Now
``/reload`` carries it (the next request holds it) and ``/new`` puts it back in
the input box, as an Esc gives a line back.

``run_tui`` over a real :class:`AgentSessionRuntime` on a JSONL session; the
model is scripted and answers the first prompt with a non-retryable ``400``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aelix_coding_agent.core.runnable_models as runnable_models
import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import ReloadSeed, create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.tui.shell import run_tui

from tests.tui.test_reload_extensions_25 import _Registry
from tests.tui.test_run_tui_smoke import _harness_chrome, _quit_within, _wait

MODEL = Model(id="fixture", provider="fixture")
_BAD_REQUEST = "400 Bad Request: invalid_request_error"


class _Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.seen: list[list[str]] = []
        self.release = asyncio.Event()
        self.reached = asyncio.Event()
        self.runtime: Any = None

    async def _stream(
        self, model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.seen.append(
            [
                "".join(getattr(c, "text", "") for c in m.content)
                for m in context.messages
                if getattr(m, "role", None) == "user"
            ]
        )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if len(self.seen) == 1:
            self.reached.set()
            await self.release.wait()
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(stop_reason="error", error_message=_BAD_REQUEST),
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    def build(self, session: Session) -> AgentHarness:
        harness = AgentHarness(
            AgentHarnessOptions(
                model=MODEL, stream_fn=self._stream, session=session, cwd=str(self.tmp)
            )
        )
        harness.state.auto_retry_enabled = False
        return harness

    async def factory(
        self, session: Session, *, reload_seed: ReloadSeed | None = None
    ) -> AgentHarness:
        return self.build(session)

    @classmethod
    async def create(cls, tmp: Path) -> _Rig:
        rig = cls(tmp)
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp / "sessions"))
        session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp)))
        rig.runtime = await create_agent_session_runtime(
            rig.build(session), rig.factory, repo=repo, fs=fs
        )
        return rig


@pytest.mark.parametrize("command", ["/reload", "/new"])
async def test_a_line_left_queued_by_an_error_survives_reload_and_comes_back_on_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    # The scripted model has no adapter; the #189 gate is not what is measured.
    monkeypatch.setattr(runnable_models, "turn_refusal", lambda _m: None)
    rig = await _Rig.create(tmp_path)
    async with _harness_chrome() as (_base, chrome, pipe):
        task = asyncio.ensure_future(
            run_tui(
                rig.runtime,
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
            )
        )
        await _wait(lambda: chrome.app.is_running)

        pipe.send_text("first\n")
        await asyncio.wait_for(rig.reached.wait(), 10)
        pipe.send_text("QUEUED-LINE\n")  # Enter during the turn steers
        await _wait(lambda: rig.runtime.harness.pending_message_count == 1)
        rig.release.set()
        await _wait(lambda: rig.runtime.harness.is_idle and not chrome.running)
        # #345: the error ended the turn with the line still queued.
        assert rig.runtime.harness.pending_message_count == 1
        before = rig.runtime.harness

        pipe.send_text(command + "\n")
        await _wait(lambda: rig.runtime.harness is not before and rig.runtime.harness.is_idle)

        if command == "/reload":
            assert rig.runtime.harness.pending_message_count == 1
            pipe.send_text("next\n")
            await _wait(lambda: len(rig.seen) == 2 and rig.runtime.harness.is_idle)
            assert rig.seen[1][-2:] == ["next", "QUEUED-LINE"]
        else:
            await _wait(lambda: chrome.get_editor_text() == "QUEUED-LINE")
            assert rig.runtime.harness.pending_message_count == 0
            assert len(rig.seen) == 1

        chrome.set_editor_text("")
        pipe.send_text("/quit\n")
        await _quit_within(task)
