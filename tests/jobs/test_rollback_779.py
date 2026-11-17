"""#779 (contract r2 §5.5, r4 §5): a session written with jobs on opens with them off, then on again.

A product rollback is one line (``jobs/activation.py`` ``ACTIVE = False``) and a
user's off switch is ``backgroundJobs.enabled: false``; either way a session that
jobs wrote to is opened by a build without them, and may be opened by one with
them again. The session here holds, written with the shipped gate on by a job
service:

* a moved job (S3a, ADR-0298): the ``agent`` kind's ``start`` fact whose
  ``client`` is the adopted linkage, version 1 (``adopted_client_start``), its
  closing ``child_session`` record carrying the ``job_id``, and its outcome;
* that job's notice, already placed (its ``nt-…`` entry is in the file);
* a second job's notice, waiting: its outcome is on disk, the process ended
  before the notice was placed.

Then the REAL CLI opens it (``_async_main`` with ``--session``, its own
``run_print_mode`` and drain, a recorded wire), as ``-p`` and as json:

* **Off** (the rollback, or the switch): the build has no job service and no
  kernel inbox, exactly the user's request is sent, the placed notice replays as
  context once and the waiting one is not delivered — no notice turn.
* **On again**: the waiting notice goes once, the placed one is not placed or
  sent again (counted by the notice id each entry carries,
  ``details[NOTICE_ID_KEY]``, not only by the entry id), and once more on, nothing
  is waiting.

The gate is on through ``jobs_active`` and off through a monkeypatch, never read
from the shipped value, so the test means the same after a rollback (fix-r1,
verify r1 F6); it pins behaviour ``bf507d59`` had with the gate on, a guard there.
Fix round 1 (Codex M1, M2, verify F4): the off and on stages went through a
harness this file built; now they go through ``cli/entry.py``, so a CLI that
builds the service or the inbox with the gate off, starts a turn of its own, or
places a notice twice, fails here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.inbox import NOTICE_ID_KEY, notice_entry_id
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agents.child_session import CHILD_SESSION_TYPE
from aelix_agents.jobs_client import (
    KIND,
    DelegationJobs,
    DelegationKindAdapter,
    adopted_client_start,
)
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.notice import final_notice_id
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE, append_fact, append_record, make_fact

from tests.agents_ext.test_child_session_records import _parent
from tests.cli.test_rpc_model_registry_371 import (  # noqa: F401 - env is a fixture
    _write_models,
    env,
)
from tests.jobs import _sleeper
from tests.jobs.conftest import RecordingModel, fast_service, wait_for

MOVED = "job-moved"
MOVED_ANSWER = "the moved agent's answer"
WAITING_TEXT = "sleeper said"


def _entries_of(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _count(path: Path, entry_id: str) -> int:
    return sum(1 for entry in _entries_of(path) if entry.get("id") == entry_id)


def _placed(path: Path) -> dict[str, int]:
    """Every entry that carries a notice id, counted by that id (Codex M1: not only by entry id)."""

    counts: dict[str, int] = {}
    for entry in _entries_of(path):
        details = entry.get("details")
        if isinstance(details, dict) and isinstance(details.get(NOTICE_ID_KEY), str):
            counts[details[NOTICE_ID_KEY]] = counts.get(details[NOTICE_ID_KEY], 0) + 1
    return counts


async def _open(path: Path, model: RecordingModel) -> tuple[AgentHarness, Any]:
    """A job service and its harness on the session, as the gate-on build that writes it composes them."""

    session = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    service = fast_service()
    _sleeper.register(service)
    service.register_kind(
        DelegationKindAdapter(DelegationJobs(), session=lambda: session),
        lambda _params: None,  # type: ignore[arg-type,return-value]
    )
    harness = AgentHarness(AgentHarnessOptions(stream_fn=model, session=session, notice_inbox=True))
    await service.bind(harness, reason="resume")
    return harness, service


async def _write_with_jobs_on(tmp_path: Path) -> tuple[Path, str, str]:
    """The session the gate-on build leaves: its path, the placed notice's id and the waiting job's id."""

    session, _repo, path = await _parent(tmp_path)
    owner = (await session.get_metadata()).id
    row = SimpleNamespace(id="sub-k", session_id="child-1", session_rel="kids/child-1.jsonl")
    linkage = adopted_client_start(row, profile="scout", task="t", tool_call_id="tc", posture="plan")  # type: ignore[arg-type]
    assert linkage["v"] == 1 and linkage["adopted"] is True
    await append_fact(
        session,
        make_fact(
            "start", MOVED, kind=KIND, kind_version=1, owner=owner, mode="background", notify=True,
            title="scout: t", created=0.0, settings_key="agents", client=linkage,
        ),
    )
    await append_record(
        session,
        CHILD_SESSION_TYPE,
        {"phase": "settle", "status": "ok", "key": "sub-k", "child": {"session_id": "child-1"},
         "text": MOVED_ANSWER, "job_id": MOVED},
    )
    await append_fact(
        session,
        make_fact(
            "outcome", MOVED, status="done", brief=MOVED_ANSWER, elapsed_ms=5, client_records="ok",
            notice={"id": final_notice_id(MOVED), "body": f"[moved job] {MOVED_ANSWER}", "brief": MOVED_ANSWER},
        ),
    )

    # The build that wrote it: the moved job's notice is placed by a notice turn,
    # and a second job ends while the process does, its notice still waiting.
    model = RecordingModel("noted")
    harness, service = await _open(path, model)
    assert harness.pending_notices == 1, "the moved job's notice, posted at the bind"
    (posted,) = harness.undelivered_notices()
    moved_notice = posted.notice_id
    assert (await harness.prompt_notices()).started
    assert _count(path, notice_entry_id(moved_notice)) == 1, "placed"
    waiting = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await wait_for(lambda: harness.pending_notices == 1)
    await service.shutdown()  # the process ends before the notice is placed
    return path, moved_notice, waiting


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Each request body the CLI sends, answered with a short SSE reply (``test_rpc_model_registry_371``'s shape)."""

    import httpx

    sent: list[dict[str, Any]] = []

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content or b"{}"))
        chunks = [
            {"id": "p", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
            {"id": "p", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        ]
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data.encode("utf-8"))

    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = httpx.MockTransport(_record)
        kwargs["mounts"] = {}
        kwargs["trust_env"] = False
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return sent


async def _cli(monkeypatch: pytest.MonkeyPatch, path: Path, mode: str, prompt: str) -> list[dict[str, Any]]:
    """Open ``path`` with the REAL ``_async_main`` (``--session``, ``--agents``); what each build had.

    Each entry is the harness the run built: whether its kernel inbox was on, and
    whether a job service was installed when it was built.
    """

    built: list[dict[str, Any]] = []
    real_create = entry_mod.create_agent_session_runtime

    async def _spy(harness: Any, factory: Any, **kwargs: Any) -> Any:
        built.append({"inbox": harness.notices_enabled, "service": activation._service is not None})  # noqa: SLF001
        return await real_create(harness, factory, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(entry_mod, "create_agent_session_runtime", _spy)
        flags = ["--mode", "json"] if mode == "json" else []
        code = await entry_mod._async_main(
            ["--agents", "--model", "rec/m1", "--session", str(path), *flags, "-p", prompt]
        )
    assert code == 0
    return built


def _context(body: dict[str, Any]) -> str:
    return "\n".join(_text_of(m) for m in body.get("messages", []))


@pytest.mark.parametrize(
    ("off", "mode"),
    [
        pytest.param("rollback", "print", id="rollback-p"),
        pytest.param("rollback", "json", id="rollback-json"),
        pytest.param("switched-off", "print", id="switched-off-p"),
    ],
)
@pytest.mark.usefixtures("jobs_active")
async def test_jobs_on_then_off_then_on(
    tmp_path: Path,
    env: Path,  # noqa: F811
    wire: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    off: str,
    mode: str,
) -> None:
    written, moved_notice, waiting = await _write_with_jobs_on(tmp_path / "written")
    waiting_notice = final_notice_id(waiting)
    path = tmp_path / "opened.jsonl"
    shutil.copy(written, path)
    _write_models(env, "rec")
    settings = env / "agent" / "settings.json"
    assert _placed(path) == {moved_notice: 1}

    # Off: the rollback's build, or the user's switch. No service, no inbox, the
    # user's request and nothing else; the placed notice replays, the waiting one waits.
    with pytest.MonkeyPatch.context() as gate:
        if off == "rollback":
            gate.setattr(activation, "ACTIVE", False)
        else:
            settings.write_text(json.dumps({"backgroundJobs": {"enabled": False}}), encoding="utf-8")
        built = await _cli(monkeypatch, path, mode, "hello")
    assert built == [{"inbox": False, "service": False}]
    (request,) = wire
    context = _context(request)
    assert _text_of(request["messages"][-1]) == "hello", "the user's request, no notice turn"
    assert context.count(MOVED_ANSWER) == 1, "the placed notice replays as context, once"
    assert WAITING_TEXT not in context, "the waiting one is not delivered"
    assert _placed(path) == {moved_notice: 1}

    # On again: the waiting notice goes once; the placed one is neither placed nor sent again.
    settings.write_text("{}", encoding="utf-8")  # the next launch reads it: on
    wire.clear()
    built = await _cli(monkeypatch, path, mode, "next")
    assert built == [{"inbox": True, "service": True}]
    (request,) = wire
    assert _text_of(request["messages"][-1]) == "next", "the restored notice goes with the user's message"
    last = _context(request)
    assert last.count(WAITING_TEXT) == 1, "the waiting notice, once"
    assert last.count(MOVED_ANSWER) == 1, "the placed one, once: replayed, not sent again"
    assert _placed(path) == {moved_notice: 1, waiting_notice: 1}
    assert _count(path, notice_entry_id(waiting_notice)) == 1
    assert _count(path, notice_entry_id(moved_notice)) == 1

    # Once more on: nothing is waiting, nothing is placed again.
    wire.clear()
    built = await _cli(monkeypatch, path, mode, "again")
    assert built == [{"inbox": True, "service": True}]
    (request,) = wire
    assert _text_of(request["messages"][-1]) == "again"
    again = _context(request)
    assert again.count(WAITING_TEXT) == 1 and again.count(MOVED_ANSWER) == 1
    assert _placed(path) == {moved_notice: 1, waiting_notice: 1}

    # The moved job is still read as moved by a service, after all of it.
    harness, service = await _open(path, RecordingModel())
    try:
        assert harness.pending_notices == 0
        moved = service.snapshot(MOVED)
        assert moved is not None and moved.state == "done"
        assert moved.client_start.get("adopted") is True and moved.client_start.get("v") == 1, "read as moved"
    finally:
        await service.shutdown()
    assert any(e.get("customType") == JOB_FACT_TYPE for e in _entries_of(path))
