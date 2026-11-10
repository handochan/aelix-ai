"""#712 — a Ctrl+C during an extension's ``setup()`` after the main task's cancel count was left up.

On Python 3.11 and 3.12 a ``TaskGroup`` whose child fails after its body finished
leaves the main task's cancel count one up with no cancel behind it (a CPython bug,
fixed in 3.13; #698, ADR-0284). ``asyncio.run`` turns the cancel its SIGINT handler
made into ``KeyboardInterrupt`` only when ``Task.uncancel()`` brings the count back to
0, so with that count left over it raised the ``CancelledError`` the loader had marked
instead, and ``main_sync`` read #685's mark only under a ``KeyboardInterrupt``.

Measured before (main ``80ff2cc2``, macOS, scripted local model, extension A's
``TaskGroup`` then extension B's ``time.sleep(3)``, SIGINT 0.6 s into B): ``-p`` and the
pty TUI ended in a ``CancelledError`` traceback and exit 1 on 3.11.15 and 3.12.13, and
in the one line and exit 130 on 3.14.5 (``.omc/probes/712/impl/``). A count left up by a
setup() that swallows a cancel of its own task does the same on every Python, so the
rows below that use it fail on base on every version; the ``TaskGroup`` row fails there
on 3.11 and 3.12 only.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import aelix_coding_agent.cli.entry as entry_mod
import pytest

from tests.cli.test_ctrl_c_during_setup_685 import _launch
from tests.cli.test_extension_exit_664 import _Scripted, scripted  # noqa: F401

# --- in process: asyncio.run's own SIGINT handler -----------------------------------------


def _main_sync_running(
    monkeypatch: pytest.MonkeyPatch, body: Callable[[], Coroutine[Any, Any, int]]
) -> BaseException:
    """Run ``main_sync`` with ``_async_main`` replaced by *body*, under the real ``asyncio.run``."""

    monkeypatch.setattr(entry_mod, "_inject_truststore", lambda: None)
    monkeypatch.setattr(entry_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(entry_mod, "register_providers", lambda: None)
    monkeypatch.setattr(entry_mod, "_async_main", lambda argv: body())
    monkeypatch.setattr(entry_mod.sys, "argv", ["aelix", "-p", "hi"])
    # Everything is caught: an escaping KeyboardInterrupt would end the pytest run.
    with pytest.raises(BaseException) as caught:
        entry_mod.main_sync()
    return caught.value


def _setup_interrupted(*, left_over: bool, by: str) -> Callable[[], Coroutine[Any, Any, int]]:
    """A main task that is cancelled while it stands for an extension's ``setup()``.

    *left_over* first leaves the count one up with nothing pending, as a 3.11/3.12
    ``TaskGroup`` does — here by swallowing a cancel of its own, which does it on every
    Python. *by* is ``"sigint"`` (the process gets a real SIGINT, which ``asyncio.run``'s
    handler turns into the cancel) or ``"code"`` (``Task.cancel()``, no Ctrl+C). The
    ``CancelledError`` is marked as the loader marks one passing through a ``setup()``.
    """

    async def _body() -> int:
        task = asyncio.current_task()
        assert task is not None
        if left_over:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(0)
            assert task.cancelling() == 1
        if by == "sigint":
            signal.raise_signal(signal.SIGINT)
        else:
            task.cancel()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            exc._aelix_extension_interrupted = ("/x/slow.py", "setup()")  # type: ignore[attr-defined]
            raise
        return 0

    return _body


_needs_the_default_sigint_handler = pytest.mark.skipif(
    signal.getsignal(signal.SIGINT) is not signal.default_int_handler,
    reason="asyncio.run installs its SIGINT handler only over the default one",
)


@_needs_the_default_sigint_handler
@pytest.mark.parametrize("left_over", [True, False], ids=["count-left-over", "count-clean"])
def test_main_sync_says_one_line_for_a_ctrl_c_in_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], left_over: bool
) -> None:
    raised = _main_sync_running(monkeypatch, _setup_interrupted(left_over=left_over, by="sigint"))
    assert isinstance(raised, SystemExit), f"main_sync re-raised {raised!r}: a traceback"
    assert raised.code == 130
    err = capsys.readouterr().err
    assert err == "aelix: interrupted while the extension /x/slow.py was running its setup()\n"


@pytest.mark.parametrize("left_over", [True, False], ids=["count-left-over", "count-clean"])
def test_main_sync_keeps_a_marked_cancel_no_ctrl_c_made(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], left_over: bool
) -> None:
    """A cancel of the main task that no SIGINT made carries the same mark, and ``asyncio.run``
    raises it as the same ``CancelledError``: it stays a traceback, as before."""

    raised = _main_sync_running(monkeypatch, _setup_interrupted(left_over=left_over, by="code"))
    assert type(raised) is asyncio.CancelledError
    assert capsys.readouterr().err == ""


def test_main_sync_keeps_a_marked_cancelled_error_it_was_handed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ``asyncio.run`` stand-in that raises a marked ``CancelledError`` ran no task at all."""

    cancelled = asyncio.CancelledError()
    cancelled._aelix_extension_interrupted = ("/x/slow.py", "setup()")  # type: ignore[attr-defined]

    def _run(coro: Any) -> int:
        coro.close()
        raise cancelled

    monkeypatch.setattr(entry_mod, "_inject_truststore", lambda: None)
    monkeypatch.setattr(entry_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(entry_mod, "register_providers", lambda: None)
    monkeypatch.setattr(entry_mod.asyncio, "run", _run)
    monkeypatch.setattr(entry_mod.sys, "argv", ["aelix", "-p", "hi"])
    with pytest.raises(BaseException) as caught:
        entry_mod.main_sync()
    assert caught.value is cancelled
    assert capsys.readouterr().err == ""


# --- real processes: ``python -m aelix_coding_agent -e A -e B -p hi`` ----------------------

# 3.11 and 3.12 only: the TaskGroup leaves the count up there (#698); 3.13 fixed it.
_A_TASKGROUP = (
    "import asyncio\n\n"
    "async def _start(ok):\n"
    "    await asyncio.sleep(0.01)\n"
    "    if not ok:\n"
    "        raise OSError('server b: connection refused')\n\n"
    "async def setup(aelix):\n"
    "    try:\n"
    "        async with asyncio.TaskGroup() as tg:\n"
    "            tg.create_task(_start(True))\n"
    "            tg.create_task(_start(False))\n"
    "    except* OSError:\n"
    "        pass\n"
)
# Every Python: a cancel of its own task, swallowed, leaves the same count.
_A_SWALLOWED = (
    "import asyncio\nimport contextlib\n\n"
    "async def setup(aelix):\n"
    "    asyncio.current_task().cancel()\n"
    "    with contextlib.suppress(asyncio.CancelledError):\n"
    "        await asyncio.sleep(0)\n"
)
# A sync setup() holds the loop: the SIGINT handler's cancel waits for it (#685).
_B_SLOW = (
    "import time\nfrom pathlib import Path\n\n"
    "def setup(aelix):\n"
    "    Path(READY).write_text('started')\n"
    "    time.sleep(2)\n"
)


def _ends(proc: Any) -> tuple[str, str]:
    try:
        return proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT to a child process")
@pytest.mark.parametrize("first", [_A_TASKGROUP, _A_SWALLOWED], ids=["taskgroup", "swallowed"])
def test_print_mode_ends_a_ctrl_c_after_a_left_over_count_with_one_line(
    tmp_path: Path,
    scripted: _Scripted,  # noqa: F811
    first: str,
) -> None:
    ready = tmp_path / "ready"
    slow = tmp_path / "b_slow.py"
    slow.write_text(_B_SLOW.replace("READY", repr(str(ready))), encoding="utf-8")
    proc = _launch(tmp_path, scripted, first, tmp_path / "unused", after=slow)
    try:
        deadline = time.monotonic() + 90
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the second extension's setup() never started"
        proc.send_signal(signal.SIGINT)
    finally:
        out, err = _ends(proc)
    assert proc.returncode == 130, err
    [line] = err.strip().splitlines()
    assert line.startswith("aelix: interrupted while the extension ")
    assert line.endswith("b_slow.py was running its setup()")
    assert out == ""
    assert scripted.requests == 0


def test_print_mode_keeps_a_cancelled_error_an_extension_raised_with_no_ctrl_c(
    tmp_path: Path,
    scripted: _Scripted,  # noqa: F811
) -> None:
    """The loader marks every ``CancelledError`` passing through a ``setup()``. One the
    extension raised itself, with the count left up and no Ctrl+C, is still a traceback."""

    raises = tmp_path / "c_raises.py"
    raises.write_text(
        "import asyncio\n\n"
        "async def setup(aelix):\n"
        "    fut = asyncio.get_running_loop().create_future()\n"
        "    fut.cancel()\n"
        "    await fut\n",
        encoding="utf-8",
    )
    proc = _launch(tmp_path, scripted, _A_SWALLOWED, tmp_path / "unused", after=raises)
    out, err = _ends(proc)
    assert proc.returncode == 1, err
    assert "Traceback" in err
    assert err.strip().splitlines()[-1] == "asyncio.exceptions.CancelledError"
    assert "interrupted" not in err
    assert out == ""
    assert scripted.requests == 0
