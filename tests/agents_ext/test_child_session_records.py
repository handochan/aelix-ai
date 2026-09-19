"""#199 — a delegated child's session is recorded, and the parent keeps the receipt.

WHAT IS PINNED HERE, AND AT WHAT LEVEL. Everything below drives the REAL
runtime (``_SubagentRuntimeImpl._run``) against a REAL parent session on disk
(``JsonlSessionStorage`` under ``tmp_path``) and a channel stand-in — no process.
The quantities under test are all decided before or after ``channel.run``: where
the child's file goes, what it holds before the child exists, which records the
parent writes on which exit path, and what survives a cancel. The real-child
half (a child writing its transcript into the file the parent allocated, an
early-exit child) is ``test_child_session_real_child.py``.

PATHS ARE KEPT SHORT ON PURPOSE. The windows leg runs this file, a ``tmp_path``
there is ~90 characters, and a bucket name encodes a whole cwd; the parent files
built here are called ``p.jsonl`` so a child's staged ``.tmp`` stays well under
``MAX_PATH`` whatever the runner's long-path setting.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    JsonlSessionListOptions,
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
    load_jsonl_session_metadata,
)
from aelix_agent_core.session.jsonl_repo import _encode_cwd
from aelix_agent_core.session.storage import SessionError
from aelix_agents import child_session
from aelix_agents import runtime as runtime_module
from aelix_agents.batch import run_batch
from aelix_agents.child_session import (
    CHILD_ORIGIN_TYPE,
    CHILD_SESSION_TYPE,
    PARENT_OUTSIDE_A_BUCKET,
    RECORD_BUILD_FAILED,
    USAGE_TYPE,
    SpawnReceipt,
    cost_is_known,
    place_children,
    usage_is_complete,
)
from aelix_agents.consent import SpawnGrant
from aelix_agents.envelope import build_result
from aelix_agents.print_channel import (
    RunningChild,
    SpawnPlan,
    apply_cost_fallback,
    build_child_argv,
)
from aelix_agents.runtime import (
    MAX_DELEGATIONS_PER_PROMPT,
    MAX_LIVE_CHILDREN,
    SpawnRecordMeta,
    SubagentHost,
    _SubagentRuntimeImpl,
)
from aelix_agents.stream import _StreamState, reduce_line
from aelix_agents.tool import AgentCall
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.cli.entry import _build_session, _resolve_session_metadata
from aelix_coding_agent.extensions.api import ExtensionContext, _ExtensionRuntime
from aelix_coding_agent.subagent_contract import (
    DEPTH_ENV_VAR,
    ResolvedProfile,
    SubagentResult,
    SubagentUsage,
)

from tests.agents_ext.test_tool_and_security import _bench, _call, _write_profile

POSIX_MODES = sys.platform != "win32"

# === The exact record shapes (ADR-0243; the kernel folds aelix.usage) =========

_IDENTITY_KEYS = [
    "v",
    "key",
    "phase",
    "tool_call_id",
    "index",
    "mode",
    "profile",
    "task_preview",
    "child",
    "requested_model",
    "permission_mode",
    "aelix_version",
]
_START_KEYS = [*_IDENTITY_KEYS, "error"]
_SETTLE_KEYS = [
    *_IDENTITY_KEYS,
    "status",
    "model",
    "provider",
    "usage",
    "cost_known",
    "context_tokens",
    "turns",
    "elapsed_ms",
    "exit_code",
    "stop_reason",
    "truncated",
    "summary_bytes",
    "details_bytes",
    "error",
]
_FINAL_KEYS = ["v", "key", "state", "usage", "cost_known"]
_USAGE_KEYS = ["input", "output", "cache_read", "cache_write", "cost"]
_ORIGIN_KEYS = [
    "v",
    "key",
    "parent",
    "tool_call_id",
    "index",
    "mode",
    "profile",
    "permission_mode",
    "aelix_version",
]


# === Scaffolding ==============================================================


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
    }
    base.update(kwargs)
    return AgentProfile(**base)


def _resolved(profile: AgentProfile | None = None) -> ResolvedProfile:
    p = profile if profile is not None else _profile()
    return ResolvedProfile(name=p.name, profile=p, source_path=p.file_path, scope=p.scope)


def _grant(mode: PermissionMode = PermissionMode.PLAN) -> SpawnGrant:
    return SpawnGrant(
        profile="scout",
        source_path="/home/u/.aelix/agent/agents/scout.md",
        scope="user",
        mode=mode,
        widened=False,
        consented=True,
    )


async def _parent(
    tmp_path: Path, *, cwd: Path | None = None, name: str = "p.jsonl"
) -> tuple[Session, JsonlSessionRepo, Path]:
    """A parent session INSIDE the bucket ``JsonlSessionRepo`` uses for ``cwd``.

    Published with a short name rather than through ``repo.create`` (whose
    ``<timestamp>_<uuid>.jsonl`` is 62 characters) — see the module docstring.
    ``list``/``find_most_recent`` accept any ``*.jsonl`` in the bucket.
    """

    project = cwd if cwd is not None else tmp_path / "p"
    project.mkdir(parents=True, exist_ok=True)
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "s"))
    bucket = tmp_path / "s" / _encode_cwd(str(project))
    path = bucket / name
    storage = await JsonlSessionStorage.create(
        fs, str(path), cwd=str(project), session_id=str(uuid.uuid4())
    )
    return Session(storage), repo, path


class _Channel:
    """A channel stand-in: records plans, optionally spends, parks or raises."""

    def __init__(
        self,
        result: SubagentResult | None = None,
        *,
        park: bool = False,
        raises: BaseException | None = None,
        spend: dict[str, Any] | None = None,
    ) -> None:
        self.plans: list[SpawnPlan] = []
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()
        self._result = result
        self._park = park
        self._raises = raises
        self._spend = spend or {}

    async def run(
        self,
        plan: SpawnPlan,
        *,
        child: RunningChild | None = None,
        on_stream: Any = None,
    ) -> SubagentResult:
        self.plans.append(plan)
        if child is not None:
            for key, value in self._spend.items():
                setattr(child.stream, key, value)
        self.arrived.set()
        if self._raises is not None:
            raise self._raises
        if self._park:
            await self.release.wait()
        if child is not None and child.stopped:
            child.state = "stopped"
            return SubagentResult(
                id=plan.id, profile=plan.resolved.name, ok=False, status="aborted", summary="stopped"
            )
        if child is not None:
            child.state = "done"
        if self._result is not None:
            return self._result
        return SubagentResult(
            id=plan.id,
            profile=plan.resolved.name,
            ok=True,
            status="ok",
            summary="the child says hello",
            usage=SubagentUsage(input=100, output=20, cache_read=5, cache_write=1, cost=0.25, tokens=126, turns=2),
            elapsed_ms=1234,
            exit_code=0,
            stop_reason="end_turn",
            details="the child says hello",
            permission_mode=plan.permission_mode.value,
            model="m-ran",
            provider="p-ran",
        )


def _runtime(
    tmp_path: Path,
    channel: Any,
    *,
    session: Any = None,
    getter: Any = None,
    registry: Any = None,
) -> _SubagentRuntimeImpl:
    project = tmp_path / "p"
    project.mkdir(parents=True, exist_ok=True)
    host = SubagentHost(
        cwd=lambda: str(project),
        session=getter if getter is not None else (lambda: session),
        model_registry=lambda: registry,
    )
    return _SubagentRuntimeImpl(host=host, channel=channel)


async def _records(session: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (entry.custom_type, entry.data)
        for entry in await session.get_entries()
        if entry.type == "custom" and entry.custom_type in (CHILD_SESSION_TYPE, USAGE_TYPE)
    ]


def _shape(records: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """``start`` / ``pending`` / ``settle`` / ``final`` — the order on the branch."""

    return [data.get("phase") or data.get("state") for _type, data in records]


async def _spawn(runtime: _SubagentRuntimeImpl, **kwargs: Any) -> SubagentResult:
    return await runtime.spawn_granted(_grant(), _resolved(), "look at the thing", **kwargs)


class _Registry:
    """``ModelRegistry.find`` — all the cost fallback asks of a registry."""

    def __init__(self, model: Model | None) -> None:
        self._model = model

    def find(self, provider: str, model_id: str) -> Model | None:
        return self._model


_PRICED = Model(
    id="m-ran",
    provider="p-ran",
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.25),
)


# === Placement (A.1) ==========================================================


def test_children_live_beside_the_parent_under_its_stem(tmp_path: Path) -> None:
    parent = tmp_path / "s" / "--home-u-proj--" / "p.jsonl"
    placement = place_children(str(parent))
    assert placement is not None
    assert placement.directory == str(parent.with_name("p"))
    assert placement.rel_dir == "p"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("p.json", "p.json.children"), ("p", "p.children"), (".jsonl", ".jsonl.children")],
)
def test_only_an_exact_jsonl_suffix_is_stripped(
    tmp_path: Path, name: str, expected: str
) -> None:
    """Anything else gets ``.children`` — never a directory named like the file."""

    placement = place_children(str(tmp_path / "s" / "--b--" / name))
    assert placement is not None
    assert placement.rel_dir == expected


def test_a_parent_outside_a_bucket_gets_no_child_directory(tmp_path: Path) -> None:
    """A parent directly in a sessions root would make ``<stem>/`` a new bucket."""

    assert place_children(str(tmp_path / "sessions" / "p.jsonl")) is None


async def test_the_child_is_invisible_to_every_picker_even_when_newest(
    tmp_path: Path,
) -> None:
    """``list``, ``find_most_recent`` and id-prefix resolution never see a child.

    The child file is made the NEWEST file under the root: ``--continue`` sorts
    by mtime, so a child that was merely older would prove nothing.
    """

    session, repo, parent_path = await _parent(tmp_path)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)
    await _spawn(runtime)

    child_path = Path(channel.plans[0].session_path or "")
    assert child_path.is_file()
    future = parent_path.stat().st_mtime + 3600
    os.utime(child_path, (future, future))

    cwd = str(tmp_path / "p")
    parent_meta = await session.get_metadata()
    newest = await repo.find_most_recent(cwd)
    assert newest is not None and Path(newest.path) == parent_path
    assert [m.id for m in await repo.list(JsonlSessionListOptions(cwd=cwd))] == [parent_meta.id]
    assert [m.id for m in await repo.list(JsonlSessionListOptions())] == [parent_meta.id]

    start = (await _records(session))[0][1]
    child_id = start["child"]["session_id"]
    fs = LocalFileSystem()
    assert await _resolve_session_metadata(repo, fs, child_id[:8], cwd) is None
    found = await _resolve_session_metadata(repo, fs, parent_meta.id[:8], cwd)
    assert found is not None and found.id == parent_meta.id


# === Allocation (A.2) =========================================================


async def test_the_parent_publishes_the_childs_header_and_origin_before_it_runs(
    tmp_path: Path,
) -> None:
    """One atomic publish: header + ``aelix.child_origin``, ``parent_id`` None.

    ``parent_id`` must be ``None`` or the child's first ``get_branch()`` raises
    ``invalid_session`` before its first turn — so the file is re-opened here
    and walked from its leaf, exactly as a child's startup does.
    """

    session, _repo, parent_path = await _parent(tmp_path)
    seen: dict[str, Any] = {}

    class _Peek(_Channel):
        async def run(self, plan: SpawnPlan, **kwargs: Any) -> SubagentResult:
            # The file exists BEFORE the child does — the property an early-exit
            # child depends on.
            seen["exists"] = Path(plan.session_path or "").is_file()
            return await super().run(plan, **kwargs)

    channel = _Peek()
    runtime = _runtime(tmp_path, channel, session=session)
    result = await _spawn(runtime, record=SpawnRecordMeta(tool_call_id="tc-1", mode="single", index=0))

    plan = channel.plans[0]
    assert seen["exists"] is True
    child_path = Path(plan.session_path or "")
    assert child_path.parent == parent_path.with_name("p")
    assert child_path.name == f"{result.id}.jsonl"

    lines = [json.loads(line) for line in child_path.read_text(encoding="utf-8").splitlines()]
    header, origin = lines
    assert header["type"] == "session" and header["version"] == 3
    assert header["cwd"] == plan.cwd
    assert "parentSession" not in header, "a delegated child is not a FORK of its parent"
    assert origin["type"] == "custom"
    assert origin["customType"] == CHILD_ORIGIN_TYPE
    assert origin["parentId"] is None
    parent_meta = await session.get_metadata()
    assert list(origin["data"]) == _ORIGIN_KEYS
    assert origin["data"]["key"] == result.id
    assert origin["data"]["parent"] == {"session_id": parent_meta.id, "path": str(parent_path)}
    assert origin["data"]["tool_call_id"] == "tc-1"
    assert origin["data"]["permission_mode"] == "plan"

    reopened = await JsonlSessionStorage.open(LocalFileSystem(), str(child_path))
    branch = await Session(reopened).get_branch()
    assert [entry.id for entry in branch] == [origin["id"]]
    assert (await reopened.get_metadata()).id == header["id"]

    if POSIX_MODES:
        assert stat.S_IMODE(child_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(child_path.parent.stat().st_mode) == 0o700


async def test_a_relative_parent_path_gives_an_absolute_child_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent opened with a RELATIVE ``--session``, a child in a subdirectory.

    The child resolves ``--session`` against ITS cwd. A relative path would name
    nothing there (the design critique measured ``Failed to read session
    header``, exit 1), so the path is made absolute in the parent — and the
    child's view of it is checked from the subdirectory itself.
    """

    _session, _repo, parent_path = await _parent(tmp_path)
    monkeypatch.chdir(tmp_path)
    relative = os.path.relpath(parent_path, tmp_path)
    fs = LocalFileSystem()
    meta = await load_jsonl_session_metadata(fs, relative)
    reopened = Session(await JsonlSessionStorage.open(fs, meta.path))
    assert reopened.session_file is not None
    assert not os.path.isabs(reopened.session_file), "the premise: the parent holds a relative path"

    sub = tmp_path / "p" / "sub"
    sub.mkdir(parents=True)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=reopened)
    await runtime.spawn_granted(_grant(), _resolved(), "go", cwd="sub")

    plan = channel.plans[0]
    assert plan.cwd == str(sub.resolve())
    assert plan.session_path is not None and os.path.isabs(plan.session_path)
    start = (await _records(reopened))[0][1]
    assert start["child"]["path"] == plan.session_path
    assert start["child"]["rel"] == f"p/{Path(plan.session_path).name}"
    monkeypatch.chdir(sub)
    child_meta = await load_jsonl_session_metadata(LocalFileSystem(), plan.session_path)
    assert child_meta.id == start["child"]["session_id"]


async def test_a_parent_outside_a_bucket_runs_its_child_without_a_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s"
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(root / "p.jsonl"), cwd=str(tmp_path), session_id=str(uuid.uuid4())
    )
    session = Session(storage)
    seen: list[list[str]] = []

    class _Argv(_Channel):
        async def run(self, plan: SpawnPlan, **kwargs: Any) -> SubagentResult:
            seen.append(_child_argv(plan))
            return await super().run(plan, **kwargs)

    channel = _Argv()
    runtime = _runtime(tmp_path, channel, session=session)
    await _spawn(runtime)

    assert channel.plans[0].session_path is None
    assert "--no-session" in seen[0] and "--session" not in seen[0]
    start = (await _records(session))[0][1]
    assert start["child"] is None
    assert start["error"] == PARENT_OUTSIDE_A_BUCKET
    assert not (root / "p").exists(), "no new directory in a sessions root"


async def test_an_allocation_failure_is_a_no_session_child_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A FILE where the children's directory must go — the create fails.

    Portable (no permission bits, so it holds on the windows leg too), and it is
    the same shape as a too-long path: an ``OSError`` out of the publish.
    """

    session, _repo, parent_path = await _parent(tmp_path)
    parent_path.with_name("p").write_text("in the way", encoding="utf-8")
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)
    result = await _spawn(runtime)

    assert result.ok is True, "recording never changes a delegation"
    assert channel.plans[0].session_path is None
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    start = records[0][1]
    assert start["child"] is None
    assert start["error"].startswith("could not create the child session file")


async def test_an_existing_child_file_is_refused_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0243 §2: the publish is an ``os.replace``, so a name that is taken
    must be refused BEFORE it — or a colliding spawn id would silently take over
    another child's transcript."""

    session, _repo, parent_path = await _parent(tmp_path)
    monkeypatch.setattr(runtime_module, "_new_id", lambda: "sub-00000000abcd")
    taken = parent_path.with_name("p") / "sub-00000000abcd.jsonl"
    taken.parent.mkdir()
    taken.write_bytes(b"another child's transcript\n")
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)
    result = await _spawn(runtime)

    assert result.ok is True, "recording never changes a delegation"
    assert channel.plans[0].session_path is None, "the child runs --no-session"
    assert "--no-session" in _child_argv(channel.plans[0])
    start = (await _records(session))[0][1]
    assert start["child"] is None
    assert start["error"] == "a child session file with this name already exists"
    assert taken.read_bytes() == b"another child's transcript\n"
    assert [p.name for p in taken.parent.iterdir()] == [taken.name]


async def test_the_child_opens_its_file_through_its_own_cli_on_every_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path the parent hands over is the path the child's CLI opens.

    The one real-child test is POSIX only (``test_child_session_real_child.py``),
    so this closes the same loop in-process, on every leg: the parent allocates
    under a project path with a space and a non-ASCII letter, the child's argv
    is built by the real print builder and parsed by the real ``parse_args``,
    ``_build_session`` resolves it the way a child's startup does (a
    backslash path on windows), and what the child then appends lands after
    the origin record, in the file the parent's start record names.
    """

    project = tmp_path / "p q é"
    session, repo, _parent_path = await _parent(tmp_path, cwd=project)
    channel = _Channel()
    host = SubagentHost(cwd=lambda: str(project), session=lambda: session)
    runtime = _SubagentRuntimeImpl(host=host, channel=channel)
    await _spawn(runtime)

    plan = channel.plans[0]
    assert plan.session_path is not None
    parsed = parse_args(_child_argv(plan))
    assert parsed.session == plan.session_path and parsed.unknown_flags == {}
    monkeypatch.setenv(DEPTH_ENV_VAR, "1")  # inside a delegation: no warning
    child = await _build_session(parsed, repo, LocalFileSystem(), plan.cwd)
    await child.append_custom_entry("probe.child", {"n": 1})

    start = (await _records(session))[0][1]
    assert start["child"]["path"] == plan.session_path
    lines = [
        json.loads(line)
        for line in Path(plan.session_path).read_text(encoding="utf-8").splitlines()
    ]
    assert lines[0]["id"] == start["child"]["session_id"]
    assert lines[1]["customType"] == CHILD_ORIGIN_TYPE
    assert lines[2]["customType"] == "probe.child"
    assert lines[2]["parentId"] == lines[1]["id"]


async def test_a_no_session_parent_writes_no_file_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-session`` parent, ``AELIX_CODING_AGENT_SESSION_DIR`` set: no file.

    The env var IS inherited by the child, so the child must be told
    ``--no-session`` explicitly — asserted on the argv the real builder makes —
    and the parent must not allocate. The records still reach the parent's
    in-memory session, which is what its stats read for the process lifetime.
    """

    env_dir = tmp_path / "env-sessions"
    monkeypatch.setenv("AELIX_CODING_AGENT_SESSION_DIR", str(env_dir))
    session = Session(MemorySessionStorage())
    seen: list[list[str]] = []

    class _Argv(_Channel):
        async def run(self, plan: SpawnPlan, **kwargs: Any) -> SubagentResult:
            seen.append(_child_argv(plan))
            return await super().run(plan, **kwargs)

    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    runtime = _runtime(tmp_path, _Argv(), session=session)
    await _spawn(runtime)
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    assert "--no-session" in seen[0] and "--session" not in seen[0]
    assert set(after) - set(before) <= {Path("p")}, f"files appeared: {set(after) - set(before)}"
    assert not env_dir.exists()
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    assert records[0][1]["child"] is None and records[0][1]["error"] is None


def _child_argv(plan: SpawnPlan) -> list[str]:
    """The argv the REAL print builder makes for ``plan``, as a child parses it."""

    return build_child_argv(
        plan.resolved.profile,
        prompt_path="/tmp/prompt.md",
        task=plan.task,
        permission_mode=plan.permission_mode,
        child_cwd=plan.cwd,
        parent_cwd=plan.parent_cwd,
        session_path=plan.session_path,
    )[3:]


# === The records, on every exit path (A.3) ====================================


async def test_the_ok_path_writes_the_four_records_in_their_exact_shapes(
    tmp_path: Path,
) -> None:
    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel()
    runtime = _runtime(tmp_path, channel, session=session)
    result = await _spawn(
        runtime, record=SpawnRecordMeta(tool_call_id="tc-7", mode="single", index=0)
    )

    records = await _records(session)
    assert [t for t, _d in records] == [CHILD_SESSION_TYPE, USAGE_TYPE, CHILD_SESSION_TYPE, USAGE_TYPE]
    (_, start), (_, pending), (_, settle), (_, final) = records

    assert list(start) == _START_KEYS
    assert start["key"] == result.id and start["phase"] == "start"
    assert (start["tool_call_id"], start["mode"], start["index"]) == ("tc-7", "single", 0)
    assert start["profile"] == "scout"
    assert start["task_preview"] == "look at the thing"
    assert start["permission_mode"] == "plan"
    assert list(start["child"]) == ["session_id", "path", "rel"]
    assert start["error"] is None

    assert pending == {"v": 1, "key": result.id, "state": "pending"}

    assert list(settle) == _SETTLE_KEYS
    assert settle["phase"] == "settle" and settle["status"] == "ok"
    assert (settle["model"], settle["provider"]) == ("m-ran", "p-ran"), "what RAN, not what was asked"
    assert settle["usage"] == {"input": 100, "output": 20, "cache_read": 5, "cache_write": 1, "cost": 0.25}
    assert settle["cost_known"] is True
    assert settle["context_tokens"] == 126 and settle["turns"] == 2
    assert settle["elapsed_ms"] == 1234 and settle["exit_code"] == 0
    assert settle["stop_reason"] == "end_turn" and settle["truncated"] is False
    assert settle["summary_bytes"] == len(b"the child says hello")
    assert settle["details_bytes"] == len(b"the child says hello")
    assert {k: settle[k] for k in _IDENTITY_KEYS if k != "phase"} == {
        k: start[k] for k in _IDENTITY_KEYS if k != "phase"
    }, "a settle is a COMPLETE record, not the second half of the start"

    assert list(final) == _FINAL_KEYS
    assert final["state"] == "final"
    assert list(final["usage"]) == _USAGE_KEYS
    assert final["usage"] == settle["usage"] and final["cost_known"] is True


async def test_the_records_survive_a_reload_of_the_parent_file(tmp_path: Path) -> None:
    session, _repo, parent_path = await _parent(tmp_path)
    await _spawn(_runtime(tmp_path, _Channel(), session=session))
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(parent_path)))
    assert _shape(await _records(reopened)) == ["start", "pending", "settle", "final"]


@pytest.mark.parametrize(
    ("status", "exit_code", "error"),
    [
        ("error", 1, "model said no"),
        ("timeout", None, None),
        ("aborted", -15, None),
        # An exec failure: no process ever existed, the channel says so.
        ("error", None, "[Errno 2] No such file or directory: 'python'"),
    ],
    ids=["error", "timeout", "aborted", "exec-failure"],
)
async def test_every_envelope_status_settles_with_that_status(
    tmp_path: Path, status: str, exit_code: int | None, error: str | None
) -> None:
    session, _repo, _path = await _parent(tmp_path)
    envelope = SubagentResult(
        id="sub-x", profile="scout", ok=False, status=status, summary="partial",  # type: ignore[arg-type]
        exit_code=exit_code, error=error,
    )
    await _spawn(_runtime(tmp_path, _Channel(envelope), session=session))
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    settle = records[2][1]
    assert settle["status"] == status and settle["exit_code"] == exit_code
    assert settle["error"] == error


async def test_an_exception_out_of_the_channel_settles_error_and_still_raises(
    tmp_path: Path,
) -> None:
    """``write_prompt_file`` runs outside the channel's own ``try`` — it raises.

    The exception is NOT swallowed (recording never changes a delegation), and
    the ``finally`` still writes a settle and a final, and still pops the row.
    """

    session, _repo, _path = await _parent(tmp_path)
    runtime = _runtime(tmp_path, _Channel(raises=OSError(28, "No space left on device")), session=session)
    with pytest.raises(OSError, match="No space left"):
        await _spawn(runtime)
    assert runtime.list() == []
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    settle = records[2][1]
    assert settle["status"] == "error"
    assert settle["error"].startswith("OSError:") and "No space left" in settle["error"]
    assert settle["summary_bytes"] == 0


async def test_while_a_child_runs_the_parent_holds_start_and_pending_only(
    tmp_path: Path,
) -> None:
    """The prefix a KILLED parent leaves: outcome unknown, spend unconfirmed."""

    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(park=True)
    runtime = _runtime(tmp_path, channel, session=session)
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()
    assert _shape(await _records(session)) == ["start", "pending"]
    channel.release.set()
    await task
    assert _shape(await _records(session)) == ["start", "pending", "settle", "final"]


async def test_a_real_cancel_settles_cancelled_with_its_priced_partial_spend(
    tmp_path: Path,
) -> None:
    """Ctrl+C is ``turn_task.cancel()`` — no envelope is ever built on it.

    So the fallback never priced the spend; the ``finally`` prices it first, or
    the record would say ``cost: 0`` for tokens that were billed.
    """

    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(
        park=True,
        spend={"input": 1_000_000, "output": 500_000, "provider": "p-ran", "model": "m-ran", "turns": 1, "tokens": 9, "summary": "half an answer"},
    )
    runtime = _runtime(tmp_path, channel, session=session, registry=_Registry(_PRICED))
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.list() == []
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    settle, final = records[2][1], records[3][1]
    assert settle["status"] == "cancelled" and settle["error"] is None
    assert settle["usage"]["input"] == 1_000_000 and settle["usage"]["output"] == 500_000
    assert settle["usage"]["cost"] == pytest.approx(2.0)  # 1M x $1 + 0.5M x $2
    assert settle["cost_known"] is True
    assert (settle["model"], settle["provider"], settle["turns"]) == ("m-ran", "p-ran", 1)
    assert settle["details_bytes"] == len(b"half an answer")
    assert final["usage"] == settle["usage"] and final["cost_known"] is True


async def test_an_unpriceable_cancel_is_recorded_as_unknown_spend(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(park=True, spend={"input": 10, "provider": "p", "model": "m"})
    runtime = _runtime(tmp_path, channel, session=session, registry=_Registry(None))
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    final = (await _records(session))[3][1]
    assert final["usage"]["cost"] == 0.0 and final["cost_known"] is False


class _SlowSession:
    """A parent session whose appends take several loop turns each.

    ``LocalFileSystem`` never yields inside an append, so a real file cannot
    show a second cancel landing mid-write; this can, deterministically.
    """

    def __init__(self, inner: Session, passes: int = 20) -> None:
        self.inner = inner
        self.passes = passes

    @property
    def session_file(self) -> str | None:
        return self.inner.session_file

    async def get_metadata(self) -> Any:
        return await self.inner.get_metadata()

    async def append_custom_entry(self, custom_type: str, data: Any = None) -> str:
        for _ in range(self.passes):
            await asyncio.sleep(0)
        return await self.inner.append_custom_entry(custom_type, data)


async def test_a_second_cancel_cannot_lose_the_settle(tmp_path: Path) -> None:
    """The critique's probe P.3, case D: the WAIT is cancelled, the write is not.

    First cancel → the ``finally`` starts a shielded settle. Second cancel →
    ``_run`` gives up waiting at once (the row is popped, the task ends) while
    the settle is still in flight — and it lands afterwards.
    """

    inner, _repo, _path = await _parent(tmp_path)
    slow = _SlowSession(inner)
    channel = _Channel(park=True)
    runtime = _runtime(tmp_path, channel, session=slow)
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()
    for _ in range(100):  # let start + pending finish before the first cancel
        await asyncio.sleep(0)
        if len(await _records(inner)) == 2:
            break

    task.cancel()
    await asyncio.sleep(0)  # the task enters its finally and starts the settle
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.list() == [], "the row goes whatever happens to the settle"
    assert _shape(await _records(inner)) == ["start", "pending"], "still in flight"
    for _ in range(200):
        await asyncio.sleep(0)
    records = await _records(inner)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    assert records[2][1]["status"] == "cancelled"


async def test_stop_all_joins_the_settles_a_second_cancel_left_in_flight(
    tmp_path: Path,
) -> None:
    """The settle a second cancel abandons must not outlive teardown.

    ``stop_all`` is what every teardown runs (``session_shutdown`` at ``/new``,
    ``/resume``, ``/fork`` and quit). Called right after the second cancel —
    two children, not one spare loop turn — it returns only once both settles
    have landed, so a loop torn down next cannot cut one off part-written.
    """

    inner, _repo, _path = await _parent(tmp_path)
    slow = _SlowSession(inner)
    channel = _Channel(park=True)
    runtime = _runtime(tmp_path, channel, session=slow)
    tasks = [asyncio.ensure_future(_spawn(runtime)) for _ in range(2)]
    for _ in range(400):  # both openings land before the first cancel
        await asyncio.sleep(0)
        if len(await _records(inner)) == 4:
            break
    assert sorted(_shape(await _records(inner))) == ["pending", "pending", "start", "start"]

    for task in tasks:
        task.cancel()
    await asyncio.sleep(0)  # each enters its finally and starts its settle
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(runtime._settling) == 2, "both settles are still being written"

    await runtime.stop_all()

    assert runtime._settling == set()
    shapes = _shape(await _records(inner))
    assert shapes.count("settle") == 2 and shapes.count("final") == 2


class _ParkingSession(_SlowSession):
    """Parks inside its ``park_at``-th append until a cancel gets it out."""

    def __init__(self, inner: Session, *, park_at: int) -> None:
        super().__init__(inner, passes=0)
        self.park_at = park_at
        self.calls = 0
        self.parked = asyncio.Event()

    async def append_custom_entry(self, custom_type: str, data: Any = None) -> str:
        self.calls += 1
        if self.calls == self.park_at:
            self.parked.set()
            await asyncio.Event().wait()
        return await self.inner.append_custom_entry(custom_type, data)


@pytest.mark.parametrize("park_at", [1, 2], ids=["in-the-start", "in-the-pending"])
async def test_a_cancel_inside_an_opening_append_still_leaves_all_four(
    tmp_path: Path, park_at: int
) -> None:
    """The opening is counted one record at a time, AFTER each append returns.

    ``LocalFileSystem`` never yields inside an append, but a session store may.
    A cancel while the start — or the pending — is being written leaves that
    record to the ``finally``, which writes it ahead of the settle. It used to
    be marked written before the append began, and the ``finally`` then wrote
    only ``settle, final``.
    """

    inner, _repo, _path = await _parent(tmp_path)
    parking = _ParkingSession(inner, park_at=park_at)
    runtime = _runtime(tmp_path, _Channel(), session=parking)
    task = asyncio.ensure_future(_spawn(runtime))
    await parking.parked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    records = await _records(inner)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    assert records[2][1]["status"] == "cancelled"


async def test_a_lone_surrogate_cannot_turn_a_cancel_into_an_error(
    tmp_path: Path,
) -> None:
    """The settle counts bytes, and a count must never raise.

    A child's JSON can carry a lone surrogate (``"\\ud83d"``), which comes back
    into the stream state as that code point. Counted with a bare
    ``encode("utf-8")`` it raised ``UnicodeEncodeError`` out of ``_run``'s
    ``finally``: the Ctrl+C came out as that error, and the key stayed pending
    for good.
    """

    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(park=True, spend={"summary": "half \ud83d"})
    runtime = _runtime(tmp_path, channel, session=session)
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    assert records[2][1]["details_bytes"] == len(b"half ") + 3


async def test_a_lone_surrogate_in_the_details_does_not_cost_the_result(
    tmp_path: Path,
) -> None:
    """The same count on the envelope path. A failed child's partial answer
    rides in ``details``, and with a lone surrogate in it ``_run`` raised
    instead of returning the envelope the channel had built."""

    state = _StreamState(summary="half \ud83d", saw_agent_start=True)
    envelope = build_result(
        id="sub-u", profile="scout", state=state, exit_code=1, stderr_tail="boom"
    )
    session, _repo, _path = await _parent(tmp_path)
    runtime = _runtime(tmp_path, _Channel(envelope), session=session)

    assert await _spawn(runtime) is envelope
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    assert records[2][1]["details_bytes"] == len(
        (envelope.details or "").encode("utf-8", "surrogatepass")
    )


@pytest.mark.parametrize("path", ["envelope", "cancel"])
async def test_a_record_that_cannot_be_built_still_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """The belt behind the total builders: whatever a builder raises, the
    delegation keeps its result (or its cancel), and the key still settles —
    bare, with the spend, and ``cost_known`` false."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the builder broke")

    builder = "outcome_records" if path == "envelope" else "unsettled_records"
    monkeypatch.setattr(SpawnReceipt, builder, _boom)
    session, _repo, _path = await _parent(tmp_path)
    channel = _Channel(park=path == "cancel", spend={"input": 7, "output": 3})
    runtime = _runtime(tmp_path, channel, session=session)
    if path == "envelope":
        assert (await _spawn(runtime)).ok is True
        status, spent = "ok", 100  # the envelope's usage
    else:
        task = asyncio.ensure_future(_spawn(runtime))
        await channel.arrived.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        status, spent = "cancelled", 7  # the live stream's

    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    settle, final = records[2][1], records[3][1]
    assert list(settle) == _SETTLE_KEYS
    assert (settle["status"], settle["error"]) == (status, RECORD_BUILD_FAILED)
    assert settle["cost_known"] is False and final["cost_known"] is False
    assert settle["usage"]["input"] == spent and final["usage"] == settle["usage"]


async def test_a_refused_spawn_records_nothing(tmp_path: Path) -> None:
    """Admitted spawns only: drain, live cap and budget refusals spent nothing."""

    session, _repo, _path = await _parent(tmp_path)
    runtime = _runtime(tmp_path, _Channel(), session=session)

    runtime._closed = True
    assert (await _spawn(runtime)).ok is False
    runtime._closed = False

    for i in range(MAX_LIVE_CHILDREN):
        runtime._children[f"sub-held{i}"] = RunningChild(id=f"sub-held{i}", profile="x")
    assert (await _spawn(runtime)).ok is False
    runtime._children.clear()

    runtime._delegations_this_prompt = MAX_DELEGATIONS_PER_PROMPT
    assert (await _spawn(runtime)).ok is False

    assert await _records(session) == []


async def test_a_recording_failure_never_changes_the_result(tmp_path: Path) -> None:
    class _Broken:
        session_file = None

        async def get_metadata(self) -> Any:
            raise SessionError("storage", "gone")

        async def append_custom_entry(self, *_a: Any, **_k: Any) -> str:
            raise SessionError("storage", "disk full")

    envelope = SubagentResult(id="sub-r", profile="scout", ok=True, status="ok", summary="fine")
    runtime = _runtime(tmp_path, _Channel(envelope), session=_Broken())
    assert await _spawn(runtime) is envelope


async def test_a_raising_session_getter_is_no_session(tmp_path: Path) -> None:
    def _boom() -> Any:
        raise RuntimeError("host broken")

    channel = _Channel()
    runtime = _runtime(tmp_path, channel, getter=_boom)
    assert (await _spawn(runtime)).ok is True
    assert channel.plans[0].session_path is None


# === The session is captured ONCE (A.3a) ======================================


async def test_a_settle_after_new_lands_in_the_session_that_ran_the_child(
    tmp_path: Path,
) -> None:
    """``/new`` mid-delegation: ``stop_all`` at shutdown, then the harness swap.

    The getter answers the NEW session by the time the settle is written; the
    settle must still go to the session that ran the child, and the new one
    must get nothing — it would otherwise be billed for spend it never had.
    """

    old, _repo, _path = await _parent(tmp_path, name="old.jsonl")
    new, _repo2, _path2 = await _parent(tmp_path, name="new.jsonl")
    current = {"session": old}
    calls: list[int] = []

    def _getter() -> Any:
        calls.append(1)
        return current["session"]

    channel = _Channel(park=True)
    runtime = _runtime(tmp_path, channel, getter=_getter)
    task = asyncio.ensure_future(_spawn(runtime))
    await channel.arrived.wait()

    current["session"] = new  # the runtime host has swapped harnesses
    await runtime.stop_all()  # session_shutdown
    channel.release.set()
    result = await task

    assert result.status == "aborted"
    assert calls == [1], "read once per spawn, never again"
    assert _shape(await _records(old)) == ["start", "pending", "settle", "final"]
    assert (await _records(old))[2][1]["status"] == "aborted"
    assert await _records(new) == []


async def _agents_run(bench: Any) -> SubagentResult:
    """``/agents run scout run it`` — the product-core door, ``runtime.spawn``."""

    runtime = bench.ext.runtime
    assert runtime is not None
    return await runtime.spawn(_resolved(), "run it")


def _stale_context(cwd: Path, session: Any) -> ExtensionContext:
    class _Manager:
        def get_session(self) -> Any:
            return session

    runtime = _ExtensionRuntime()
    ctx = ExtensionContext(
        runtime,
        cwd=str(cwd),
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: ["read"],
        get_system_prompt=lambda: "",
        session_manager=_Manager(),
    )
    runtime.invalidate("stale")
    return ctx


async def test_agents_run_records_before_any_hook_has_fired(tmp_path: Path) -> None:
    """``/agents run`` as the very first command: ``_ctx`` is ``None``."""

    bench = _bench(tmp_path)
    session, _repo, _path = await _parent(tmp_path, cwd=bench.cwd)
    bench.ext.session = lambda: session
    assert bench.ext._ctx is None
    result = await _agents_run(bench)
    assert result.ok is True
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    start = records[0][1]
    assert (start["tool_call_id"], start["index"], start["mode"]) == (None, None, None)
    assert start["child"] is not None


async def test_agents_run_records_with_a_stale_hook_context(tmp_path: Path) -> None:
    """``/agents run`` right after ``/new``: ``_ctx`` belongs to the dead session.

    Two things used to go wrong here. The consent read raised
    ``ExtensionError("stale")`` before anything ran, and a context-based
    session read would have pointed at the OLD session. The getter wins.
    """

    bench = _bench(tmp_path)
    live, _repo, _path = await _parent(tmp_path, cwd=bench.cwd, name="live.jsonl")
    dead, _repo2, _path2 = await _parent(tmp_path, cwd=bench.cwd, name="dead.jsonl")
    bench.ext.session = lambda: live
    bench.ext._ctx = _stale_context(bench.cwd, dead)

    result = await _agents_run(bench)

    assert result.ok is True
    assert _shape(await _records(live)) == ["start", "pending", "settle", "final"]
    assert await _records(dead) == []


async def test_an_unwired_extension_falls_back_to_a_live_context_only(
    tmp_path: Path,
) -> None:
    bench = _bench(tmp_path)
    old, _repo, _path = await _parent(tmp_path, cwd=bench.cwd)
    bench.ext._ctx = _stale_context(bench.cwd, old)
    assert bench.ext._host_session() is None, "a stale context is no context"
    assert (await _agents_run(bench)).ok is True
    assert await _records(old) == []


async def test_the_model_door_records_the_call_its_topology_and_each_index(
    tmp_path: Path,
) -> None:
    _write_profile(tmp_path / "agent" / "agents" / "scout.md", "scout")
    bench = _bench(tmp_path)
    session, _repo, _path = await _parent(tmp_path, cwd=bench.cwd)
    bench.ext.session = lambda: session

    single = await _call(bench, {"profile": "scout", "task": "one"}, tool_call_id="tc-s")
    assert not single.is_error
    batch = await _call(
        bench,
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c"]},
        tool_call_id="tc-p",
    )
    assert not batch.is_error

    starts = [d for t, d in await _records(session) if t == CHILD_SESSION_TYPE and d["phase"] == "start"]
    assert [(s["tool_call_id"], s["mode"], s["index"]) for s in starts] == [
        ("tc-s", "single", 0),
        ("tc-p", "parallel", 0),
        ("tc-p", "parallel", 1),
        ("tc-p", "parallel", 2),
    ]
    assert len({s["key"] for s in starts}) == 4, "one key per CHILD, not per call"


async def test_a_chain_member_records_its_step_index(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    runtime = _runtime(tmp_path, _Channel(), session=session)
    await run_batch(
        runtime=runtime,
        grant=_grant(),
        resolved=_resolved(),
        call=AgentCall(profile="scout", tasks=("x", "y"), mode="chain"),
        cwd=str(tmp_path / "p"),
        has_ui=lambda: False,
        posture=lambda: PermissionMode.DEFAULT,
        record=SpawnRecordMeta(tool_call_id="tc-c", mode="chain"),
    )
    starts = [d for t, d in await _records(session) if d.get("phase") == "start"]
    assert [(s["mode"], s["index"]) for s in starts] == [("chain", 0), ("chain", 1)]


def test_the_cli_warning_spells_the_same_origin_type() -> None:
    from aelix_coding_agent.cli import entry

    assert entry._CHILD_ORIGIN_TYPE == CHILD_ORIGIN_TYPE


# === cost_known (A.3b) ========================================================


def test_the_fallback_says_whether_it_priced() -> None:
    def _state(**kw: Any) -> _StreamState:
        state = _StreamState(provider="p-ran", model="m-ran", input=1_000_000)
        for key, value in kw.items():
            setattr(state, key, value)
        return state

    assert apply_cost_fallback(_state(), None) is False
    assert apply_cost_fallback(_state(), _Registry(None)) is False
    priced = _state()
    assert apply_cost_fallback(priced, _Registry(_PRICED)) is True
    assert priced.cost == pytest.approx(1.0) and priced.cost_priced is True
    free = _state()
    assert apply_cost_fallback(free, _Registry(Model(id="m", provider="p"))) is True
    assert free.cost == 0.0 and free.cost_priced is True, "$0.00 is an ANSWER"
    already = _state(cost=0.5)
    assert apply_cost_fallback(already, _Registry(_PRICED)) is False
    assert already.cost == 0.5


@pytest.mark.parametrize(
    ("usage", "reported"),
    [
        ({"input": 5, "cost": {"total": 0}}, True),
        ({"input": 5, "cost": 0.25}, True),
        ({"input": 5}, False),
        ({"input": 5, "cost": {"total": float("nan")}}, False),
        ({"input": 5, "cost": "1.0"}, False),
    ],
    ids=["zero-total", "flat", "absent", "nan", "string"],
)
def test_the_reducer_notes_a_cost_answer_zero_included(
    usage: dict[str, Any], reported: bool
) -> None:
    state = _StreamState()
    line = json.dumps({"type": "message_end", "message": {"role": "assistant", "content": [], "usage": usage}})
    reduce_line(state, line)
    assert state.cost_reported is reported


def test_cost_is_known_exactly_when_the_design_says() -> None:
    zero = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    spent = {**zero, "input": 10}
    assert cost_is_known(_StreamState(), usage=zero, cost=0.0) is True, "nothing spent"
    assert cost_is_known(_StreamState(), usage=spent, cost=0.0) is False, "spent, unpriced"
    assert cost_is_known(_StreamState(cost_reported=True), usage=spent, cost=0.0) is True
    assert cost_is_known(_StreamState(cost_priced=True), usage=spent, cost=0.0) is True
    assert cost_is_known(_StreamState(cost_priced=True), usage=spent, cost=float("inf")) is False
    assert cost_is_known(_StreamState(cost_priced=True), usage=spent, cost=-1.0) is False


# A run that began (``agent_start``) and was cut before its own ``agent_end``:
# the parent stopped reading, and a request in flight then may have been billed
# with no ``message_end`` ever reaching it. Nothing about that run is a whole
# bill — not zeros, and not a priced partial (Codex review of #199: a Ctrl+C
# before any usage arrived was recorded as a confirmed "$0").
_CUT_SHORT = {"saw_agent_start": True, "run_open": True}
_RAN_TO_THE_END = {"saw_agent_start": True, "saw_agent_end": True, "run_open": False}


def test_usage_is_complete_only_when_the_run_ended_or_never_began() -> None:
    assert usage_is_complete(_StreamState()) is True, "never began: nothing was requested"
    assert usage_is_complete(_StreamState(**_RAN_TO_THE_END)) is True
    assert usage_is_complete(_StreamState(**_CUT_SHORT)) is False


def _fold(*kinds: str) -> _StreamState:
    state = _StreamState()
    for kind in kinds:
        reduce_line(state, json.dumps({"type": kind}))
    return state


def test_a_stream_cut_during_an_auto_retry_is_still_unfinished() -> None:
    """An auto-retry is a second run: ``agent_start`` → ``agent_end`` →
    ``agent_start``. The latched ``saw_agent_end`` stays true through it, so
    completeness reads the LAST event instead (Codex review of the lead delta)."""

    retried = _fold("agent_start", "agent_end", "agent_start")
    assert retried.saw_agent_end is True, "the latched flag keeps its meaning"
    assert retried.run_open is True
    assert usage_is_complete(retried) is False
    finished = _fold("agent_start", "agent_end", "agent_start", "agent_end")
    assert usage_is_complete(finished) is True


@pytest.mark.parametrize("status", sorted(child_session.CUT_BY_THE_PARENT))
def test_a_run_the_parent_cut_is_never_complete_even_with_an_agent_end(status: str) -> None:
    """The child's own abort path emits ``agent_end`` without a ``message_end``
    for the request it abandoned, so a terminator after a parent-side cut proves
    nothing about the bill (Codex review of the lead delta)."""

    assert usage_is_complete(_StreamState(**_RAN_TO_THE_END), status=status) is False
    assert usage_is_complete(_StreamState(), status=status) is True, (
        "a child cut before it began a run requested nothing"
    )
    zero = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    assert cost_is_known(
        _StreamState(**_RAN_TO_THE_END, cost_priced=True), usage=zero, cost=0.0, status=status
    ) is False


@pytest.mark.parametrize("status", ["ok", "error"])
def test_a_run_that_closed_on_its_own_is_complete(status: str) -> None:
    assert usage_is_complete(_StreamState(**_RAN_TO_THE_END), status=status) is True
    assert usage_is_complete(_StreamState(**_CUT_SHORT), status=status) is False, (
        "a child that died mid-turn left its run open"
    )


def test_a_run_cut_short_is_never_a_known_cost() -> None:
    zero = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    spent = {**zero, "input": 10}
    assert cost_is_known(_StreamState(**_CUT_SHORT), usage=zero, cost=0.0) is False, (
        "zeros from a cut run are not a confirmed nothing"
    )
    assert cost_is_known(
        _StreamState(**_CUT_SHORT, cost_priced=True), usage=spent, cost=0.05
    ) is False, "a priced partial is still a floor"
    assert cost_is_known(_StreamState(**_RAN_TO_THE_END), usage=zero, cost=0.0) is True
    assert cost_is_known(
        _StreamState(**_RAN_TO_THE_END, cost_priced=True), usage=spent, cost=0.05
    ) is True


@pytest.mark.parametrize("status", ["cancelled", "error"])
def test_a_cancel_before_any_usage_records_a_floor_not_zero(status: str) -> None:
    receipt = SpawnReceipt(
        key="sub-000000000000",
        tool_call_id="t",
        index=None,
        mode="single",
        profile="scout",
        task_preview="t",
        requested_model=None,
        permission_mode="plan",
    )
    records = dict(
        (record["phase"] if "phase" in record else record["state"], record)
        for _type, record in receipt.unsettled_records(
            _StreamState(**_CUT_SHORT), status=status, error=None, elapsed_ms=5, exit_code=None
        )
    )
    assert records["settle"]["usage"]["cost"] == 0
    assert records["settle"]["cost_known"] is False
    assert records["final"]["cost_known"] is False


@pytest.mark.parametrize("status", ["timeout", "aborted"])
def test_an_envelope_from_a_run_cut_short_records_a_floor(status: str) -> None:
    receipt = SpawnReceipt(
        key="sub-000000000000",
        tool_call_id="t",
        index=None,
        mode="single",
        profile="scout",
        task_preview="t",
        requested_model=None,
        permission_mode="plan",
    )
    result = SubagentResult(
        id="sub-000000000000", profile="scout", ok=False, status=status, summary="partial"
    )
    for fields in (_CUT_SHORT, _RAN_TO_THE_END):  # a terminator after the cut proves nothing
        state = _StreamState(**fields, cost_priced=True)
        records = receipt.outcome_records(result, state=state)
        settle, final = records[0][1], records[1][1]
        assert settle["cost_known"] is False and final["cost_known"] is False, fields


# === The receipt, directly ====================================================


def test_the_unsettled_records_open_with_start_and_pending_when_never_opened() -> None:
    receipt = SpawnReceipt(
        key="sub-000000000000",
        tool_call_id=None,
        index=None,
        mode=None,
        profile="scout",
        task_preview="t",
        requested_model=None,
        permission_mode="plan",
    )
    def _unsettled() -> list[tuple[str, dict[str, Any]]]:
        return receipt.unsettled_records(
            _StreamState(), status="cancelled", error=None, elapsed_ms=5, exit_code=None
        )

    assert _shape(_unsettled()) == ["start", "pending", "settle", "final"]
    receipt.opening_attempted = 1
    assert _shape(_unsettled()) == ["pending", "settle", "final"]
    receipt.opening_attempted = 2
    assert _shape(_unsettled()) == ["settle", "final"]
    assert _shape(receipt.bare_records(_StreamState(), status="error")) == [
        "settle",
        "final",
    ]


def test_a_long_error_and_task_are_cut_short(tmp_path: Path) -> None:
    receipt = SpawnReceipt(
        key="sub-000000000000",
        tool_call_id="t",
        index=0,
        mode="single",
        profile="scout",
        task_preview="x" * child_session.TASK_PREVIEW_CHARS,
        requested_model=None,
        permission_mode="plan",
    )
    result = SubagentResult(id="sub-000000000000", profile="scout", ok=False, status="error", summary="s", error="e" * 5000)
    settle = receipt.outcome_records(result, state=_StreamState())[0][1]
    assert len(settle["error"]) == child_session.ERROR_PREVIEW_CHARS


async def test_the_task_preview_is_the_first_two_hundred_characters(tmp_path: Path) -> None:
    session, _repo, _path = await _parent(tmp_path)
    runtime = _runtime(tmp_path, _Channel(), session=session)
    await runtime.spawn_granted(_grant(), _resolved(), "abc" * 300)
    start = (await _records(session))[0][1]
    assert start["task_preview"] == ("abc" * 300)[:200]


def test_the_child_argv_parses_with_the_session_flag() -> None:
    parsed = parse_args(
        _child_argv(
            SpawnPlan(
                id="sub-x",
                resolved=_resolved(),
                task="t",
                cwd="/tmp",
                parent_cwd="/tmp",
                permission_mode=PermissionMode.PLAN,
                session_path="/abs/s/--b--/p/sub-x.jsonl",
            )
        )
    )
    assert parsed.session == "/abs/s/--b--/p/sub-x.jsonl"
    assert parsed.no_session is False
    assert parsed.unknown_flags == {}


# === Cross-lane: what the runtime records is what session stats count ==========


async def test_what_the_runtime_records_is_what_the_session_stats_count(
    tmp_path: Path,
) -> None:
    """The writer (this band) and the reader (the kernel fold, ADR-0243) agree.

    Three delegations go through the REAL runtime into a parent file on disk, and
    the kernel reads that file back through ``AgentHarness.get_session_stats`` —
    the path the footer, ``/cost``, ``/stats``, History and RPC all take:

    - one settled from its envelope (the channel reported its cost);
    - one CANCELLED mid-run, its partial spend priced by the registry fallback in
      ``_run``'s ``finally``;
    - one still RUNNING when the stats are read: start + pending only, so its
      spend is unconfirmed and the session cost is a floor.

    Each settle carries the same ``usage`` as its final. Only ``aelix.usage`` is
    folded, so each delegation counts once. Then the third run settles and the
    floor becomes a bill.
    """

    from aelix_agent_core.harness._session_stats import USAGE_RECORD_TYPE
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    assert USAGE_TYPE == USAGE_RECORD_TYPE, "the writer's type is the one the kernel reads"
    session, _repo, parent_path = await _parent(tmp_path)
    await _spawn(_runtime(tmp_path, _Channel(), session=session))  # 100/20/5/1, $0.25

    cancelled = _Channel(
        park=True, spend={"input": 1000, "output": 500, "provider": "p-ran", "model": "m-ran"}
    )
    task = asyncio.ensure_future(
        _spawn(_runtime(tmp_path, cancelled, session=session, registry=_Registry(_PRICED)))
    )
    await cancelled.arrived.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    running = _Channel(park=True)
    in_flight = asyncio.ensure_future(_spawn(_runtime(tmp_path, running, session=session)))
    await running.arrived.wait()

    async def _stats() -> Any:
        # Read back from DISK: what the kernel folds is what a reload returns.
        reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(parent_path)))
        harness = AgentHarness(AgentHarnessOptions(model=None, session=reopened))  # type: ignore[arg-type]
        try:
            return await harness.get_session_stats()
        finally:
            await harness.dispose()

    during = await _stats()
    tool = during.tool_usage
    assert (tool.runs, tool.pending) == (3, 1)
    assert (tool.tokens.input, tool.tokens.output) == (100 + 1000, 20 + 500)
    assert (tool.tokens.cache_read, tool.tokens.cache_write) == (5, 1)
    assert tool.tokens.total == 1100 + 520 + 5 + 1
    assert tool.cost == pytest.approx(0.25 + 0.002)  # 1000 x $1/M + 500 x $2/M
    assert tool.cost_known is False, "a pending run is spend nobody has confirmed"
    # No parent message carried usage, so the session totals ARE the tool share.
    assert during.tokens == tool.tokens
    assert during.cost == pytest.approx(tool.cost)
    assert during.cost_known is False

    running.release.set()
    await in_flight
    after = await _stats()
    assert (after.tool_usage.runs, after.tool_usage.pending) == (3, 0)
    assert (after.tokens.input, after.tokens.output) == (200 + 1000, 40 + 500)
    assert after.tokens.total == 1200 + 540 + 10 + 2
    assert after.cost == pytest.approx(0.5 + 0.002)
    assert after.cost_known is True and after.tool_usage.cost_known is True
