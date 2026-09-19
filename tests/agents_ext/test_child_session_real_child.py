"""#199 against a REAL child: the file the parent allocates is the file it writes.

``test_child_session_records.py`` pins the parent's half with a channel
stand-in. This file closes the loop through a real ``-m aelix_coding_agent``
child, driven by the same stub provider ``test_child_realized_posture.py`` uses
(an ``-e`` extension that registers a wire adapter — no network, no key):

* the child opens ``--session <the file the parent published>`` and appends its
  own transcript AFTER the parent's ``aelix.child_origin`` record, and the
  parent's settle carries what the child actually ran and spent;
* a child that exits before its first turn (a provider that does not exist)
  still leaves the header and the origin — findable, and settled ``error``.

POSIX only, because the real-child fixture it reuses is. That leaves a gap on
the windows leg, stated rather than denied: this file is the only end-to-end
proof that a real child opens the file its parent published and appends to it.
There the path is covered in process instead — argv built and parsed, then
resolved and opened by the child's own ``_build_session`` under a project path
with a space and a non-ASCII letter (``test_child_session_records.py``,
``test_the_child_opens_its_file_through_its_own_cli_on_every_platform``) — but
no real child process on windows has written one.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import PrintChannel, build_child_env
from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
from aelix_ai.streaming import Model, ModelCost
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.agents_ext.test_child_realized_posture import _Fixture
from tests.agents_ext.test_child_session_records import _parent, _records, _shape

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="real-child spawn semantics are POSIX"
)


class _Registry:
    def __init__(self, model: Model | None) -> None:
        self._model = model

    def find(self, provider: str, model_id: str) -> Model | None:
        return self._model


_STUB_PRICE = Model(
    id="stubmodel",
    provider="stubprov",
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.25),
)


def _runtime(fixture: _Fixture, session: Any, registry: Any) -> _SubagentRuntimeImpl:
    env = fixture.env()
    channel = PrintChannel(
        grace=1.0,
        env_builder=lambda p: build_child_env(p, base=env),
        model_registry=lambda: registry,
    )
    host = SubagentHost(
        cwd=lambda: str(fixture.cwd),
        session=lambda: session,
        model_registry=lambda: registry,
        active_tools=lambda: ["read", "write", "ls"],
    )
    return _SubagentRuntimeImpl(host=host, channel=channel)


def _resolved(profile: AgentProfile) -> ResolvedProfile:
    return ResolvedProfile(
        name=profile.name, profile=profile, source_path=profile.file_path, scope=profile.scope
    )


def _grant(profile: AgentProfile) -> SpawnGrant:
    return SpawnGrant(
        profile=profile.name,
        source_path=profile.file_path,
        scope=profile.scope,
        mode=PermissionMode.PLAN,
        widened=False,
        consented=True,
    )


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def test_a_real_child_writes_its_transcript_into_the_file_its_parent_made(
    tmp_path: Path,
) -> None:
    fixture = _Fixture(tmp_path)
    session, _repo, parent_path = await _parent(tmp_path, cwd=fixture.cwd)
    runtime = _runtime(fixture, session, _Registry(_STUB_PRICE))
    profile = fixture.profile(tools=("read",))

    result = await asyncio.wait_for(
        runtime.spawn_granted(
            _grant(profile),
            _resolved(profile),
            "write the file",
            timeout_ms=90_000,
            record=SpawnRecordMeta(tool_call_id="tc-real", mode="single", index=0),
        ),
        120,
    )

    assert result.ok is True, result.summary
    assert result.summary == "probe-done"
    assert result.output_recorded is True

    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    start, settle, final = records[0][1], records[2][1], records[3][1]
    child_path = Path(start["child"]["path"])
    assert child_path.parent == parent_path.with_name("p")

    written = _lines(child_path)
    assert written[0]["type"] == "session"
    assert written[0]["id"] == start["child"]["session_id"]
    assert written[1]["customType"] == "aelix.child_origin"
    assert written[1]["data"]["key"] == result.id
    messages = [e["message"] for e in written if e.get("type") == "message"]
    assert messages[0]["role"] == "user"
    first_text = messages[0]["content"][0]["text"]
    assert first_text == "Task: write the file"
    assert messages[-1]["role"] == "assistant"
    # The child's transcript chains onto the origin — its root is the record.
    assert written[2]["parentId"] is not None

    assert settle["status"] == "ok" and settle["exit_code"] == 0
    assert (settle["model"], settle["provider"]) == ("stubmodel", "stubprov")
    assert settle["usage"]["input"] == 16 and settle["usage"]["output"] == 28
    assert settle["usage"]["cost"] == pytest.approx((16 * 1.0 + 28 * 2.0) / 1_000_000)
    assert settle["cost_known"] is True, "the parent's registry priced the child's tokens"
    assert final["usage"] == settle["usage"] and final["cost_known"] is True


async def test_an_early_exit_child_leaves_its_header_and_origin(tmp_path: Path) -> None:
    """A child that dies before its first turn wrote NOTHING on stdout.

    Before #199 the parent could not even learn where such a child's session
    was. Now the parent made the file, so it exists with the header and the
    origin record, and the settle says ``error``.
    """

    fixture = _Fixture(tmp_path)
    session, _repo, _parent_path = await _parent(tmp_path, cwd=fixture.cwd)
    runtime = _runtime(fixture, session, None)
    profile = AgentProfile(
        name="ghost",
        description="Runs a model that does not exist.",
        body="You are nobody.",
        file_path=str(tmp_path / "ghost.md"),
        scope="user",
        model="no-such-model",
        provider="no-such-provider",
        tools=("read",),
    )

    result = await asyncio.wait_for(
        runtime.spawn_granted(_grant(profile), _resolved(profile), "go", timeout_ms=90_000),
        120,
    )

    assert result.ok is False
    assert result.output_recorded is False, "stderr is not in the child file"
    records = await _records(session)
    assert _shape(records) == ["start", "pending", "settle", "final"]
    settle = records[2][1]
    assert settle["status"] == "error" and settle["exit_code"] != 0
    assert settle["cost_known"] is True, "nothing was spent"
    written = _lines(Path(records[0][1]["child"]["path"]))
    assert [e["type"] for e in written] == ["session", "custom"]
    assert written[1]["customType"] == "aelix.child_origin"
