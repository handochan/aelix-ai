"""#199 A.11 and A.3a on the product-core side of the band.

* A child session file is a RECORD, not a resumable identity: everything that
  bounded the child (its ``--permission-mode`` clamp, its narrowed tools,
  ``--no-agents``, the depth guard) was argv and environment, none of it in the
  file. ``_build_session`` therefore warns — one stderr line naming
  ``aelix --export`` — when a human opens one, and stays silent inside a
  delegation, where the child opens its own file exactly this way.
* ``cli/entry.py`` hands the delegation extension the runtime host's CURRENT
  session through a late-bound getter; the extension reads it once per spawn.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import (
    JsonlSessionRepo,
    JsonlSessionStorage,
    LocalFileSystem,
)
from aelix_agent_core.session.entries import CustomEntry
from aelix_coding_agent.cli.args import parse_args
from aelix_coding_agent.cli.entry import _build_session, _live_session_of
from aelix_coding_agent.subagent_contract import DEPTH_ENV_VAR

_ENTRY = (
    Path(__file__).resolve().parents[2]
    / "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py"
)


async def _file(tmp_path: Path, name: str, *, child: bool) -> Path:
    path = tmp_path / "s" / "--b--" / name
    entries = []
    if child:
        entries.append(
            CustomEntry(
                id="00000001",
                parent_id=None,
                timestamp="2026-09-19T00:00:00.000Z",
                custom_type="aelix.child_origin",
                data={"v": 1, "key": "sub-0123456789ab"},
            )
        )
    await JsonlSessionStorage.create(
        LocalFileSystem(), str(path), cwd=str(tmp_path), session_id=str(uuid.uuid4()), entries=entries
    )
    return path


async def _open(tmp_path: Path, *argv: str) -> Any:
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "s"))
    return await _build_session(parse_args(list(argv)), repo, fs, str(tmp_path))


async def test_opening_a_child_file_warns_once_and_names_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DEPTH_ENV_VAR, raising=False)
    child = await _file(tmp_path, "sub-0123456789ab.jsonl", child=True)

    session = await _open(tmp_path, "--session", str(child))

    assert session.session_file == str(child), "a warning, not a refusal"
    err = capsys.readouterr().err
    assert err.count("\n") == 1, "exactly one line"
    assert err.startswith("Warning: ")
    assert f"aelix --export {child}" in err
    assert "permission clamp" in err


async def test_forking_a_child_file_warns_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fork copies the origin record, so the fork is the same unclamped run."""

    monkeypatch.delenv(DEPTH_ENV_VAR, raising=False)
    child = await _file(tmp_path, "sub-0123456789ab.jsonl", child=True)
    await _open(tmp_path, "--fork", str(child))
    assert "aelix --export" in capsys.readouterr().err


async def test_the_child_opening_its_own_file_is_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a delegation (depth > 0) the child IS the one opening the file.

    A warning there would land on the child's stderr, which is the tail a
    failed delegation is diagnosed from.
    """

    monkeypatch.setenv(DEPTH_ENV_VAR, "1")
    child = await _file(tmp_path, "sub-0123456789ab.jsonl", child=True)
    await _open(tmp_path, "--session", str(child))
    assert capsys.readouterr().err == ""


async def test_an_ordinary_session_is_not_warned_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DEPTH_ENV_VAR, raising=False)
    plain = await _file(tmp_path, "p.jsonl", child=False)
    await _open(tmp_path, "--session", str(plain))
    assert capsys.readouterr().err == ""


def test_entry_hands_the_extension_the_live_session_getter() -> None:
    """The tripwire for the wiring line (AST, not a substring — ``session=``
    appears in entry.py in a dozen unrelated calls)."""

    tree = ast.parse(_ENTRY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentsExtension"
    ]
    assert calls, "entry.py no longer constructs an AgentsExtension at all"
    keywords = [kw for call in calls for kw in call.keywords if kw.arg == "session"]
    assert keywords, "AgentsExtension is built without the session getter"
    assert isinstance(keywords[0].value, ast.Lambda), "it must be late-bound"


def test_the_getter_follows_the_runtime_host() -> None:
    class _Host:
        def __init__(self) -> None:
            self.session: Any = "first"

    host: dict[str, Any] = {}
    assert _live_session_of(host) is None, "before the runtime exists"
    runtime = _Host()
    host["runtime"] = runtime
    assert _live_session_of(host) == "first"
    runtime.session = "after /new"
    assert _live_session_of(host) == "after /new"
