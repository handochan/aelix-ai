"""``clone`` copies the branch a landed-and-raised final line ends (Codex review of #721, finding 5).

The handler reads the tip with ``Session.get_leaf_id``. After an append whose
line reached the file and then raised, that accessor answered from before the
line, so the clone was cut one entry short. Measured on ``ed76ea76`` with
Codex's ``rpc-clone-stale-tip`` probe — the real handler, the real JSONL fork::

    {"success": true, "selected_seed": true,
     "source": ["seed", "landed answer"], "cloned": ["seed"]}

``Session``'s leaf readers settle the pending outcome first now
(``session/session.py``), so no clone site — this one, the bridge's branch in
``rpc/control.py``, ``/clone`` in ``tui/shell.py`` — had to change.
"""

from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    Session,
    SessionError,
)
from aelix_agent_core.session.jsonl_repo import ForkOptions, JsonlSessionRepo
from aelix_ai.messages import TextContent, UserMessage
from aelix_coding_agent.rpc.rpc_mode import _handle_clone
from aelix_coding_agent.rpc.rpc_types import RpcCommandClone, RpcSuccessResponse


class _LandsThenRaises(LocalFileSystem):
    armed = False

    async def append_file(self, path: str, content: str) -> None:
        await super().append_file(path, content)
        if self.armed:
            self.armed = False
            raise OSError(errno.EIO, "after the newline (injected)")


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


async def _branch(session: Session) -> list[str]:
    return [e.message.content[0].text for e in await session.get_branch()]  # type: ignore[union-attr]


async def test_clone_after_a_final_line_that_landed_and_raised_copies_that_line(
    tmp_path: Path,
) -> None:
    fs = _LandsThenRaises()
    path = tmp_path / "s.jsonl"
    session = Session(
        await JsonlSessionStorage.create(fs, str(path), cwd=str(tmp_path), session_id="c")
    )
    seed = await session.append_message(_user("seed"))
    fs.armed = True
    with pytest.raises(SessionError):
        await session.append_message(_user("landed answer"))
    metadata = await session.get_metadata()
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path / "forks"))

    class _Host:
        harness = SimpleNamespace(withdraw_pending=False)
        model_fallback_message = None
        selected: str | None = None
        clone: Any = None

        async def fork(self, leaf: str, position: str) -> Any:
            self.selected = leaf
            self.clone = await repo.fork(
                metadata, ForkOptions(cwd=str(tmp_path), entry_id=leaf, position=position)
            )
            return SimpleNamespace(cancelled=False)

    host = _Host()
    host.session = session  # type: ignore[attr-defined]

    response = await _handle_clone(host, RpcCommandClone(id="clone-721"))

    assert isinstance(response, RpcSuccessResponse)
    assert host.selected != seed
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(path)))
    assert await _branch(reopened) == ["seed", "landed answer"]
    assert await _branch(host.clone) == ["seed", "landed answer"]
