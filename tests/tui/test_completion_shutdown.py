"""#428: actual input-completer wiring exits cleanly under repeated cancellation."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui.commands import BuiltinCommand
from aelix_coding_agent.tui.shell import _build_input_completer
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document

_PROBE = Path(__file__).with_name("_completion_shutdown_probe.py")


def _run_probe(tmp_path: Path, text: str, cancels: int, phase: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["AELIX_CODING_AGENT_DIR"] = str(tmp_path / "agent")
    env["AELIX_SETTINGS_PATH"] = str(tmp_path / "settings.json")
    try:
        result = subprocess.run(
            [sys.executable, str(_PROBE), text, str(cancels), phase],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"completion probe timed out; stdout={exc.stdout!r}; stderr={exc.stderr!r}")
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("text", ["/quit", "@sample"])
@pytest.mark.parametrize("cancels", [1, 2, 3])
def test_input_completion_executor_exits_after_cancel(
    tmp_path: Path, text: str, cancels: int
) -> None:
    """An unstarted producer must not strand a queue-consumer worker.

    Before #428, both texts submit a producer and consumer; one cancel is clean
    while two or three leave the child in executor shutdown. The event gate
    selects that schedule without sleeps in CPython's private worker code.
    """

    result = _run_probe(tmp_path, text, cancels, "queued")
    lines = result.stdout.splitlines()
    assert lines[-1] == "executor_shutdown_complete", result.stdout
    assert json.loads(lines[0])["jobs"] == (["producer"] if text.startswith("@") else [])


def test_running_completion_executor_finishes_after_double_cancel(tmp_path: Path) -> None:
    """A job already running completes after cancellation and is joined normally."""

    result = _run_probe(tmp_path, "@sample", 2, "running")
    assert result.stdout.splitlines() == [
        '{"jobs": ["producer"]}',
        "executor_shutdown_complete",
    ]


async def _collect(completer: Any, document: Document, event: CompleteEvent) -> list[Completion]:
    return [item async for item in completer.get_completions_async(document, event)]


@pytest.mark.parametrize("text", ["@", "@src/", "@sample", 'review @"file sp', "/qui"])
async def test_async_completion_keeps_sync_menu(
    monkeypatch: Any, tmp_path: Path, text: str
) -> None:
    """Drill-in, fuzzy, quoted and slash menus preserve values and presentation."""

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sample.py").write_text("sample", encoding="utf-8")
    (tmp_path / "file space.txt").write_text("sample", encoding="utf-8")
    monkeypatch.setattr(completion_mod, "_fd_binary", lambda: None)
    completer = _build_input_completer(lambda: {}, [BuiltinCommand("quit", "Exit")], str(tmp_path))
    doc = Document(text)
    event = CompleteEvent(completion_requested=True)
    expected = list(completer.get_completions(doc, event))
    actual = await _collect(completer, doc, event)

    def signature(items: list[Completion]) -> list[tuple[Any, ...]]:
        return [(c.text, c.start_position, c.display, c.display_meta) for c in items]

    assert expected, "fixture must exercise a real menu"
    assert signature(actual) == signature(expected)


@pytest.mark.parametrize("text", ["/qui", "plain prose", "user@example.com", "@sample thanks"])
async def test_non_mention_completion_submits_no_worker(
    monkeypatch: Any, tmp_path: Path, text: str
) -> None:
    def no_executor(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("non-mention completion submitted an executor job")

    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", no_executor)
    completer = _build_input_completer(lambda: {}, [BuiltinCommand("quit", "Exit")], str(tmp_path))
    items = await _collect(completer, Document(text), CompleteEvent())
    assert [item.text for item in items] == (["/quit"] if text == "/qui" else [])


async def test_file_completion_runs_off_loop_and_preserves_event(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """A stalled worker leaves the loop free and receives the original inputs."""

    started = threading.Event()
    release = threading.Event()
    loop_thread = threading.get_ident()
    doc = Document("@sample")
    event = CompleteEvent(completion_requested=True)
    offered = [Completion("@sample.py", start_position=-7, display_meta="file")]

    def blocked_completions(_self: Any, document: Document, complete_event: CompleteEvent) -> Any:
        assert threading.get_ident() != loop_thread, "file enumeration blocked the event loop"
        assert document is doc and complete_event is event
        started.set()
        assert release.wait(5), "test failed to release its completion worker"
        yield from offered

    monkeypatch.setattr(completion_mod.FileMentionCompleter, "get_completions", blocked_completions)
    completer = _build_input_completer(lambda: {}, [], str(tmp_path))
    task = asyncio.create_task(_collect(completer, doc, event))
    try:
        async with asyncio.timeout(3):
            while not started.is_set():
                if task.done():
                    await task  # report a worker assertion instead of a wait timeout
                await asyncio.sleep(0)
        # The event loop advances while the worker is explicitly stalled.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    assert await task == offered
