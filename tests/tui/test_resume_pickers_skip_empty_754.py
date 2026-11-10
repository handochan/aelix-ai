"""#754 — ``/resume`` and ``aelix -r`` list only sessions ``aelix -c`` would continue.

Since #709 ``aelix -c`` skips a session whose branch carries no conversation
(``JsonlSessionRepo._has_conversation``). The two pickers still listed every
file ``repo.list`` returned, newest-used first, so on ``aaca9e09`` a launch quit
without a prompt, an unused ``/new``, a launch whose only prompt was withdrawn
with Esc and a ``/name``-only launch sat above the last conversation: ``/resume``
opened with the top one selected (Enter gave ``Resumed session (0 messages)``),
and ``aelix -r`` gave them ``[1]``…, pushing conversations into ``… and N older``.

Both pickers now filter at their data source with the same judgment as ``-c``
(the lead's call on #754, open to the owner), before the ``-r`` menu is cut, so
its footer counts only sessions it could have listed. A branch holding only a
summary is a conversation to ``-c`` and stays listed. ``--resume <id>`` still
opens an empty session (#709 left it so; out of scope here).

The session files are written by the real ``Session`` API, then every
``timestamp`` is set by hand so the newest-used order (read from the tail) is
fixed rather than racing the millisecond clock.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session import jsonl_repo as jsonl_repo_module
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionRepo,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli import session_labels as labels_mod
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    _quit_within,
    _ResumeRuntime,
    _ResumeSession,
    _spy_commits,
    _wait,
)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _restamp(path: str, minute: int) -> None:
    """Every ``timestamp`` in ``path`` (header included) set to one minute of one day."""

    stamp = f"2026-11-10T01:{minute:02d}:00.000Z"
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        record = json.loads(line)
        record["timestamp"] = stamp
        out.append(json.dumps(record))
    Path(path).write_bytes(("\n".join(out) + "\n").encode("utf-8"))  # LF on every OS, as written


async def _write(repo: JsonlSessionRepo, cwd: str, kind: str, text: str, minute: int) -> str:
    """One session file of ``kind`` as a real launch leaves it, aged ``minute``."""

    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    first = await session.append_model_change("scripted", "s-1")
    if kind == "conversation":
        await session.append_message(_user(text))
        await session.append_message(AssistantMessage(content=[TextContent(text="REPLY")]))
    elif kind == "esc":  # the only prompt withdrawn with Esc: session, model, user, leaf
        await session.append_message(_user(text))
        await session.withdraw_tail(first, lambda e: getattr(e, "type", "") == "message")
    elif kind == "named":  # /name and /quit: session, model, session_info
        await session.append_session_name(text)
    elif kind == "summary":  # /tree back to the start with a summary: model -> branch_summary
        await session.append_message(_user(text))
        await session.append_message(AssistantMessage(content=[TextContent(text="a")]))
        await session.move_to(first, summary={"summary": "We chose blue"})
        assert [e.type for e in await session.get_branch()] == ["model_change", "branch_summary"]
    else:  # "empty": a launch quit without a prompt, or an unused /new: session, model
        assert kind == "empty", kind
    path = (await session.get_metadata()).path
    _restamp(path, minute)
    return path


async def _folder(
    tmp_path: Path, plan: list[tuple[str, str]]
) -> tuple[JsonlSessionRepo, str, list[str]]:
    """Sessions in ``plan`` order, each one minute newer than the one before."""

    repo = JsonlSessionRepo(sessions_root=str(tmp_path / "sroot"))
    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    paths = [
        await _write(repo, cwd, kind, text, minute) for minute, (kind, text) in enumerate(plan)
    ]
    return repo, cwd, paths


#: The issue's second folder plus its first: one conversation, then every kind of
#: session ``-c`` skips, each newer than it, and a summary-only branch, which ``-c``
#: continues.
_EMPTY_KINDS = [
    ("empty", ""),  # a launch quit without a prompt
    ("empty", ""),  # an unused /new
    ("esc", "withdrawn-prompt"),
    ("named", "keepme"),
]


# === aelix -r ================================================================


async def _startup_menu(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    repo: JsonlSessionRepo,
    cwd: str,
    reply: str = "",
) -> tuple[str, Any]:
    from aelix_agent_core.session.fs import LocalFileSystem

    monkeypatch.setattr(entry_mod, "_read_resume_line", lambda: reply)
    session = await entry_mod._resume_session_startup(
        Args(resume=True), repo, LocalFileSystem(), cwd
    )
    return capsys.readouterr().err, session


def _rows(menu: str) -> list[str]:
    return [line for line in menu.splitlines() if line.lstrip().startswith("[")]


async def test_startup_menu_lists_no_session_continue_would_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, cwd, paths = await _folder(
        tmp_path, [("conversation", "hello"), *_EMPTY_KINDS, ("summary", "summarised-q")]
    )
    menu, _ = await _startup_menu(monkeypatch, capsys, repo, cwd)
    rows = _rows(menu)
    assert len(rows) == 2, menu
    assert "summarised-q" in rows[0] and "[ 1]" in rows[0], menu  # a summary is a conversation
    assert "hello" in rows[1], menu
    for absent in ("(no messages)", "withdrawn-prompt", "keepme"):
        assert absent not in menu, (absent, menu)
    assert "older" not in menu, menu


async def test_startup_menu_number_one_is_the_last_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On aaca9e09 ``[1]`` was the newest empty file and opened with no transcript."""

    repo, cwd, paths = await _folder(tmp_path, [("conversation", "hello"), *_EMPTY_KINDS])
    menu, session = await _startup_menu(monkeypatch, capsys, repo, cwd, reply="1")
    assert (await session.get_metadata()).path == paths[0], menu


async def test_startup_menu_footer_one_conversation_among_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue table row 1: filtering inside ``by_recent_activity`` printed ``… and 3 older``."""

    repo, cwd, _ = await _folder(
        tmp_path, [("conversation", "hello"), ("empty", ""), ("empty", ""), ("empty", "")]
    )
    assert len(await repo.list(JsonlSessionListOptions(cwd=cwd))) == 4
    menu, _ = await _startup_menu(monkeypatch, capsys, repo, cwd)
    assert len(_rows(menu)) == 1, menu
    assert "older" not in menu, menu


async def test_startup_menu_footer_counts_only_listed_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue table row 2: 21 conversations and 3 newer empty files.

    aaca9e09 printed three ``(no messages)`` rows first and ``… and 4 older``,
    with ``talk-00``…``talk-03`` cut; one older conversation is the truth.
    """

    plan = [("conversation", f"talk-{i:02d}") for i in range(21)] + [("empty", "")] * 3
    repo, cwd, _ = await _folder(tmp_path, plan)
    assert len(await repo.list(JsonlSessionListOptions(cwd=cwd))) == 24
    menu, _ = await _startup_menu(monkeypatch, capsys, repo, cwd)
    rows = _rows(menu)
    assert len(rows) == entry_mod._RESUME_MENU_LIMIT == 20, menu
    assert "talk-20" in rows[0] and "talk-01" in rows[-1], menu
    assert "(no messages)" not in menu, menu
    assert "… and 1 older — open one with --resume <id>" in menu, menu


async def test_startup_menu_with_only_empty_sessions_starts_a_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, cwd, paths = await _folder(tmp_path, list(_EMPTY_KINDS))
    menu, session = await _startup_menu(monkeypatch, capsys, repo, cwd)
    assert "No previous sessions in this folder; starting a new one." in menu, menu
    assert not _rows(menu), menu
    assert (await session.get_metadata()).path not in paths


async def test_resume_by_id_still_opens_an_empty_session(tmp_path: Path) -> None:
    """Out of scope, pinned so the filter does not reach it: an explicit id opens.

    Passes on aaca9e09 as well; ``_resolve_session_metadata`` keeps reading
    ``repo.list`` unfiltered.
    """

    from aelix_agent_core.session.fs import LocalFileSystem

    repo, cwd, paths = await _folder(tmp_path, [("conversation", "hello"), ("named", "keepme")])
    named_id = json.loads(Path(paths[1]).read_text(encoding="utf-8").splitlines()[0])["id"]
    session = await entry_mod._resume_session_startup(
        Args(resume=True, resume_id=named_id), repo, LocalFileSystem(), cwd
    )
    assert (await session.get_metadata()).path == paths[1]


# === /resume =================================================================


async def _drive_resume(
    monkeypatch: pytest.MonkeyPatch, repo: JsonlSessionRepo, cwd: str, active: str
) -> tuple[dict[str, Any], _ResumeRuntime, list[str]]:
    """Run ``/resume`` in the real ``run_tui`` against the real repository.

    ``select`` is spied: it records the rows and the row the modal would open
    on, and answers as Enter on that row would.
    """

    seen: dict[str, Any] = {}

    async def _spy(self, title, options, opts=None, detail=None, initial_index=0, own=False):  # type: ignore[no-untyped-def]
        seen["options"] = list(options)
        seen["initial_index"] = initial_index
        return options[initial_index]

    monkeypatch.setattr(AelixTUIContext, "select", _spy)
    # ``cancelled=True``: the switch is recorded and nothing is replayed; which
    # file the picker chose is what these tests read.
    real_repo: Any = repo  # the fake runtime's annotation names the fake repo
    runtime = _ResumeRuntime(FakeHarness(), real_repo, _ResumeSession(active, []), cancelled=True)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=cwd, chrome=chrome, install_signal_handlers=False)  # type: ignore[arg-type]
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/resume\n")
        await _wait(
            lambda: (
                bool(runtime.switch_calls)
                or any("to resume" in c or "Resume cancelled" in c for c in commits)
            ),
            what="/resume to switch or to say there is nothing to resume",
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)
    return seen, runtime, commits


async def test_resume_list_holds_no_session_continue_would_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's folders: the top row was ``(no messages)`` or ``keepme`` and opened empty."""

    repo, cwd, paths = await _folder(
        tmp_path,
        [("conversation", "hello"), *_EMPTY_KINDS, ("summary", "summarised-q"), ("empty", "")],
    )
    active = paths[-1]  # this launch's own file, newest of all
    seen, runtime, _ = await _drive_resume(monkeypatch, repo, cwd, active)
    options = seen["options"]
    assert len(options) == 2, options
    assert "summarised-q" in options[0] and "hello" in options[1], options
    for row in options:
        for absent in ("(no messages)", "withdrawn-prompt", "keepme"):
            assert absent not in row, (absent, options)
    assert seen["initial_index"] == 0
    assert runtime.switch_calls == [paths[-2]]  # the summary-only conversation, not an empty file


async def test_resume_opens_on_the_last_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, cwd, paths = await _folder(
        tmp_path, [("conversation", "hello"), *_EMPTY_KINDS, ("empty", "")]
    )
    seen, runtime, _ = await _drive_resume(monkeypatch, repo, cwd, paths[-1])
    assert len(seen["options"]) == 1 and "hello" in seen["options"][0], seen
    assert runtime.switch_calls == [paths[0]]


async def test_resume_with_only_empty_sessions_says_there_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, cwd, paths = await _folder(tmp_path, [*_EMPTY_KINDS, ("empty", "")])
    seen, runtime, commits = await _drive_resume(monkeypatch, repo, cwd, paths[-1])
    assert "options" not in seen  # the picker never opened
    assert runtime.switch_calls == []
    assert any("No other sessions to resume in this folder." in c for c in commits), commits


# === one read per file, and a name read before a rename ======================


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """How often each session file is decoded, by either module's reference."""

    counts: dict[str, int] = {}
    real = jsonl_repo_module._parse_jsonl_content

    def _counting(content: str, path: str, *a: Any, **k: Any) -> Any:
        counts[str(path)] = counts.get(str(path), 0) + 1
        return real(content, path, *a, **k)

    monkeypatch.setattr(jsonl_repo_module, "_parse_jsonl_content", _counting)
    monkeypatch.setattr(labels_mod, "_parse_jsonl_content", _counting)
    return counts


async def test_each_listed_file_is_decoded_once_by_each_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The label already decoded the whole file; the filter must not add a second decode.

    A guard on the fix's shape. On aaca9e09 each label decoded its file once
    and nothing else did; this fails there only on the ``-r`` row count, since
    the empty file is listed too (4 rows, not 3). The issue measured 1238 ms
    for the labels of 100 near-1 MiB files and 1238 ms for the check alone.
    """

    repo, cwd, paths = await _folder(
        tmp_path, [("conversation", f"talk-{i}") for i in range(3)] + [("empty", "")]
    )
    counts = _count_parses(monkeypatch)
    menu, _ = await _startup_menu(monkeypatch, capsys, repo, cwd)
    assert len(_rows(menu)) == 3, menu
    assert {p: counts.get(p, 0) for p in paths[:3]} == {p: 1 for p in paths[:3]}, counts

    counts.clear()
    seen, _, _ = await _drive_resume(monkeypatch, repo, cwd, paths[-1])
    assert len(seen["options"]) == 3, seen
    assert {p: counts.get(p, 0) for p in paths[:3]} == {p: 1 for p in paths[:3]}, counts


async def test_a_name_saved_after_the_filter_read_still_labels_the_row(tmp_path: Path) -> None:
    """The label reuses the filter's decode only for the bytes that decode read."""

    repo, cwd, paths = await _folder(tmp_path, [("conversation", "hello")])
    metas = await repo.list(JsonlSessionListOptions(cwd=cwd))
    with_conversation = getattr(labels_mod, "with_conversation")  # noqa: B009 — absent on aaca9e09
    assert with_conversation(metas) == metas
    assert "hello" in labels_mod.session_choice_label(metas[0], 1e9)
    session = await repo.open(metas[0])
    await session.append_session_name("renamed-later")
    assert "renamed-later" in labels_mod.session_choice_label(metas[0], 1e9)


async def _named(tmp_path: Path) -> tuple[Any, Path, os.stat_result]:
    """One conversation saved as ``name-AAAA``, already through the filter."""

    repo, cwd, paths = await _folder(tmp_path, [("conversation", "hello")])
    session = await repo.open((await repo.list(JsonlSessionListOptions(cwd=cwd)))[0])
    await session.append_session_name("name-AAAA")
    metas = await repo.list(JsonlSessionListOptions(cwd=cwd))
    with_conversation = getattr(labels_mod, "with_conversation")  # noqa: B009 — absent on aaca9e09
    assert with_conversation(metas) == metas
    assert "name-AAAA" in labels_mod.session_choice_label(metas[0], 1e9)
    path = Path(paths[0])
    return metas[0], path, os.stat(path)


@pytest.mark.parametrize("change", ["new-inode", "new-mtime", "new-size"])
async def test_each_part_of_the_file_identity_drops_the_remembered_name(
    tmp_path: Path, change: str
) -> None:
    """A rename that moves one of inode, mtime and size, and only it, is seen.

    The label reuses the filter's decode while ``(inode, size, mtime_ns)`` is
    unchanged. Each case keeps two of the three equal (asserted) and renames
    ``name-AAAA``: a writer's ``os.replace`` with its mtime restored, a same-size
    in-place rewrite with a later mtime, and a longer in-place rewrite with the
    mtime restored. The mtimes are set by hand so no case depends on the
    filesystem's clock granularity.
    """

    meta, path, before = await _named(tmp_path)
    data = path.read_bytes()
    assert data.count(b"name-AAAA") == 1
    if change == "new-inode":
        staged = path.with_name(path.name + ".tmp")
        staged.write_bytes(data.replace(b"name-AAAA", b"name-BBBB"))
        os.replace(staged, path)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        renamed = b"name-BBBB" if change == "new-mtime" else b"name-BBBBBB"
        with open(path, "r+b") as handle:
            handle.write(data.replace(b"name-AAAA", renamed))
            handle.truncate()
        later = before.st_mtime_ns + (1_000_000_000 if change == "new-mtime" else 0)
        os.utime(path, ns=(before.st_atime_ns, later))
    after = os.stat(path)
    moved = {
        "new-inode": after.st_ino != before.st_ino,
        "new-mtime": after.st_mtime_ns != before.st_mtime_ns,
        "new-size": after.st_size != before.st_size,
    }
    assert moved == {key: key == change for key in moved}, (before, after)
    label = labels_mod.session_choice_label(meta, 1e9)
    assert "name-BBBB" in label and "name-AAAA" not in label, label


async def test_a_name_saved_while_the_filter_reads_still_labels_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity is taken BEFORE the read, so a write landing after it is seen.

    Taken after the read, it would describe the new bytes while the remembered
    name came from the old ones.
    """

    meta, path, _ = await _named(tmp_path)
    real = JsonlSessionRepo._read_conversation

    def _read_then_rename(file: Path) -> Any:
        answer = real(file)
        file.write_bytes(file.read_bytes().replace(b"name-AAAA", b"name-BBBBBB"))
        return answer

    monkeypatch.setattr(JsonlSessionRepo, "_read_conversation", staticmethod(_read_then_rename))
    with_conversation = getattr(labels_mod, "with_conversation")  # noqa: B009 — absent on aaca9e09
    assert with_conversation([meta]) == [meta]
    monkeypatch.undo()
    label = labels_mod.session_choice_label(meta, 1e9)
    assert "name-BBBBBB" in label and "name-AAAA" not in label, label


async def test_the_latest_of_two_names_labels_both_pickers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The name the filter's decode yields is the latest one, in both pickers.

    On aaca9e09 the ``-r`` half passes (the label's own decode took the latest
    name) and the ``/resume`` half fails on its row count: it also lists the
    empty session the ``-r`` half's Enter started (2 rows, not 1).
    """

    repo, cwd, paths = await _folder(tmp_path, [("conversation", "hello"), ("empty", "")])
    session = await repo.open((await repo.list(JsonlSessionListOptions(cwd=cwd)))[-1])
    assert (await session.get_metadata()).path == paths[0]
    await session.append_session_name("first-name")
    await session.append_session_name("second-name")
    menu, _ = await _startup_menu(monkeypatch, capsys, repo, cwd)
    assert "second-name" in menu and "first-name" not in menu, menu
    seen, _, _ = await _drive_resume(monkeypatch, repo, cwd, paths[-1])
    assert len(seen["options"]) == 1 and "second-name" in seen["options"][0], seen


# === the judgment is -c's =====================================================


def test_the_filter_answers_as_continue_does(tmp_path: Path) -> None:
    """Every edge ``_has_conversation`` pins, asked through the pickers' filter."""

    from types import SimpleNamespace

    header = json.dumps(
        {
            "type": "session",
            "version": 3,
            "id": "h",
            "timestamp": "2026-11-10T00:00:00.000Z",
            "cwd": str(tmp_path),
        }
    )
    user = json.dumps(
        {
            "type": "message",
            "id": "u",
            "parentId": None,
            "timestamp": "2026-11-10T00:00:01.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "x"}]},
        }
    )
    filler = json.dumps(
        {
            "type": "custom",
            "id": "c",
            "parentId": None,
            "timestamp": "2026-11-10T00:00:01.000Z",
            "customType": "x",
            "data": "y" * 4000,
        }
    )
    cap = jsonl_repo_module._CONVERSATION_SCAN_MAX_BYTES
    shapes = {
        "user": (header + "\n" + user + "\n").encode(),
        "header-only": (header + "\n").encode(),
        "not-utf8": (header + "\n").encode() + b"\xff\xfe\n" + user.encode() + b"\n",
        "torn-only-turn": (header + "\n").encode() + b'{"type": "message", "id": "u", "mess',
        "over-the-bound": (header + "\n" + (filler + "\n") * (cap // 4000 + 2)).encode(),
    }
    metas = []
    for name, data in shapes.items():
        path = tmp_path / f"{name}.jsonl"
        path.write_bytes(data)
        metas.append(SimpleNamespace(path=str(path), id=name))
    metas.append(SimpleNamespace(path=str(tmp_path / "missing.jsonl"), id="missing"))
    with_conversation = getattr(labels_mod, "with_conversation")  # noqa: B009 — absent on aaca9e09
    kept = {m.id for m in with_conversation(metas)}
    expected = {m.id for m in metas if JsonlSessionRepo._has_conversation(Path(m.path))}
    assert kept == expected == {"user", "over-the-bound"}


def test_a_check_that_raises_keeps_its_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One file the check cannot answer for keeps its row, as before #754.

    ``session_labels`` promises that one bad file never takes the list away
    (``session_choice_label``'s blanket); the filter runs over every file first.
    """

    from types import SimpleNamespace

    good = tmp_path / "good.jsonl"
    bad = tmp_path / "bad.jsonl"
    good.write_text("x", encoding="utf-8")
    bad.write_text("x", encoding="utf-8")
    real = JsonlSessionRepo._read_conversation

    def _check(path: Path) -> Any:
        if path.name == "bad.jsonl":
            raise RuntimeError("a shape nobody thought of")
        return real(path)

    monkeypatch.setattr(JsonlSessionRepo, "_read_conversation", staticmethod(_check))
    metas = [SimpleNamespace(path=str(bad), id="bad"), SimpleNamespace(path=str(good), id="good")]
    with_conversation = getattr(labels_mod, "with_conversation")  # noqa: B009 — absent on aaca9e09
    assert [m.id for m in with_conversation(metas)] == ["bad"]
