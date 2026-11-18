"""#123 (ADR-0306 §3): ``agent(action="send")`` and ``agent(action="wrap_up")``.

The parse (``_parse_send`` beside ``_parse_resume``; ``parse_agent_call`` gains no
branch), the schema where background jobs are on, the description's byte budget,
and the two actions end to end on the gated rig: a note queued into a spawned
agent and consumed, a wrap-up that ends the agent ``aborted``, a note to an agent
that finished refused with the resume hint.

Fails on ``a1606f58``: ``send`` and ``wrap_up`` are not actions; the parser refuses
them as unknown and the schema does not list them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.print_channel import PrintChannel
from aelix_agents.tool import (
    _BACKGROUND_HEAD,
    _DESCRIPTION_HEAD,
    BACKGROUND_HEAD_MAX_BYTES,
    DESCRIPTION_HEAD_MAX_BYTES,
    AgentCallError,
    agent_tool_parameters,
    parse_agent_call,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_intervention_doors_123 import _INBOX_STUB
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs.conftest import wait_for

# === the parse ======================================================================


def test_send_parses_its_note_and_its_mode_and_wrap_up_takes_only_an_id() -> None:
    call = parse_agent_call({"action": "send", "id": "j1", "task": "look at b too"}, background=True)
    assert (call.action, call.id, call.task, call.delivery) == ("send", "j1", "look at b too", "steer")
    later = parse_agent_call({"action": "send", "id": "j1", "task": "then x", "mode": "follow_up"}, background=True)
    assert later.delivery == "follow_up"
    wrap = parse_agent_call({"action": "wrap_up", "id": "j1"}, background=True)
    assert (wrap.action, wrap.id, wrap.delivery, wrap.tasks) == ("wrap_up", "j1", None, ("",))


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({"action": "send", "task": "x"}, "needs 'id'"),
        ({"action": "send", "id": "j1"}, "needs 'task'"),
        ({"action": "send", "id": "j1", "task": "x", "mode": "parallel"}, "mode='steer'"),
        ({"action": "send", "id": "j1", "task": "x", "tasks": ["y"]}, "takes no 'tasks'"),
        ({"action": "send", "id": "j1", "task": "x", "cwd": "/tmp"}, "takes no 'cwd'"),
        ({"action": "send", "id": "j1", "task": "x", "timeout": 3}, "takes no 'timeout'"),
        ({"action": "send", "id": "j1", "task": "x", "offset": 3}, "takes no 'offset'"),
        ({"action": "send", "id": "a/b", "task": "x"}, "not a file path"),
        ({"action": "wrap_up", "id": "j1", "task": "finish"}, "takes no 'task'"),
        ({"action": "wrap_up", "id": "j1", "mode": "steer"}, "takes no 'mode'"),
        ({"action": "wrap_up"}, "needs 'id'"),
    ],
)
def test_send_and_wrap_up_refuse_what_they_cannot_take(args: dict[str, Any], words: str) -> None:
    with pytest.raises(AgentCallError, match=words):
        parse_agent_call(args, background=True)


def test_without_background_jobs_send_is_an_unknown_action_as_before() -> None:
    with pytest.raises(AgentCallError, match="not an action"):
        parse_agent_call({"action": "send", "id": "j1", "task": "x"})
    with pytest.raises(AgentCallError, match="not an action"):
        parse_agent_call({"action": "wrap_up", "id": "j1"})


# === the schema and the budget ======================================================


def test_the_background_schema_lists_the_actions_and_widens_mode_only_there() -> None:
    on = agent_tool_parameters(background=True)
    assert on["properties"]["action"]["enum"][-2:] == ["send", "wrap_up"]
    assert on["properties"]["mode"]["enum"] == ["single", "parallel", "chain", "steer", "follow_up"]
    assert "send" in on["properties"]["id"]["description"]
    off = agent_tool_parameters(background=False)
    assert "send" not in off["properties"]["action"]["enum"]
    assert off["properties"]["mode"]["enum"] == ["single", "parallel", "chain"]


def test_the_description_names_send_and_wrap_up_and_stays_inside_its_budgets() -> None:
    assert "you cannot steer it mid-run" not in _DESCRIPTION_HEAD
    assert 'action="send"' in _BACKGROUND_HEAD and 'action="wrap_up"' in _BACKGROUND_HEAD
    assert "never in the middle of one" in _BACKGROUND_HEAD
    # Measured: 1566 and 966 bytes (contract r2 §3.2 asked for the number).
    assert len(_DESCRIPTION_HEAD.encode("utf-8")) <= DESCRIPTION_HEAD_MAX_BYTES
    assert len(_BACKGROUND_HEAD.encode("utf-8")) <= BACKGROUND_HEAD_MAX_BYTES


# === end to end on the gated rig ====================================================

pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")


def _channel(**env: str) -> PrintChannel:
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME", "TMPDIR", "SYSTEMROOT")}
    return PrintChannel(
        grace=1.0,
        argv_builder=lambda *_a, **_k: [sys.executable, "-c", _INBOX_STUB],
        env_builder=lambda *_a, **_k: {**base, **env},
    )


@pytestmark_posix
@pytest.mark.usefixtures("jobs_on")
async def test_a_note_is_queued_into_a_spawned_agent_and_a_wrap_up_ends_it_stopped(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, channel=_channel(AELIX_JOBS_PROTOCOL="1"), posture=PermissionMode.YOLO)
    await rig.prompt_started()
    spawned = await rig.call({"action": "spawn", "profile": "scout", "task": "count files"})
    assert not spawned.is_error, _text(spawned)
    agent_id = _text(spawned).split("(id ", 1)[1].split(";", 1)[0]
    (job_id,) = [s.job_id for s in rig.jobs()]
    await wait_for(lambda: (rig.service.snapshot(job_id).progress or {}).get("last_text") == "halfway")  # type: ignore[union-attr]
    try:
        sent = await rig.call({"action": "send", "id": agent_id, "task": "NOTE"})
        assert not sent.is_error, _text(sent)
        body = json.loads(_text(sent))
        assert body["delivered_to"] == agent_id and body["outcome"] == "queued" and body["phase"] == "streaming"
        assert body["note"].startswith("queued; the agent reads it after the tool calls it is already running")
        later = await rig.call({"action": "send", "id": job_id, "task": "LATER", "mode": "follow_up"})
        assert "after its current answer" in json.loads(_text(later))["note"], "the job id works too"
        wrapped = await rig.call({"action": "wrap_up", "id": agent_id})
        assert not wrapped.is_error, _text(wrapped)
        assert json.loads(_text(wrapped))["note"].startswith("queued; the agent gets one more reply with no tools")
        await wait_for(lambda: rig.service.snapshot(job_id).state in ("done", "failed", "stopped"), timeout=30)  # type: ignore[union-attr]
        snap = rig.service.snapshot(job_id)
        assert snap is not None and snap.state == "stopped" and snap.payload.get("envelope_status") == "aborted"
        refused = await rig.call({"action": "send", "id": agent_id, "task": "too late"})
        assert refused.is_error and "finished or was stopped" in _text(refused) and 'action="resume"' in _text(refused)
        unknown = await rig.call({"action": "send", "id": "nope", "task": "x"})
        assert unknown.is_error and "No background agent" in _text(unknown)
    finally:
        await rig.service.shutdown()
