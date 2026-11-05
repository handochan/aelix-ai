"""#685 — a real Ctrl+C while an extension's ``setup()`` runs at launch: one line, exit 130.

Measured before (``.omc/specs/663-followups-evidence/out/pL1_ctrl_c_setup-*.txt``):
SIGINT three seconds into ``aelix -p hi -e slow.py`` ended with a 45-line
traceback for an ``await asyncio.sleep`` in ``setup()`` and a 33-line one for a
``time.sleep``, both ending in ``asyncio/runners.py``'s ``raise
KeyboardInterrupt()``; #664's one line came only when the extension raised
``KeyboardInterrupt`` itself.

The real-process rows run ``python -m aelix_coding_agent`` (no ``uv`` in between,
so the signal reaches aelix) against the #664 scripted server, with a scratch
HOME and agent dir, and send SIGINT once the extension's ``setup()`` has said it
started. POSIX only: Windows has no SIGINT to send a child that way.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aelix_coding_agent.cli.entry as entry_mod
import pytest

from tests.cli.test_extension_exit_664 import _Scripted, scripted  # noqa: F401


def _main_sync_with(monkeypatch: pytest.MonkeyPatch, raised: BaseException) -> None:
    def _run(coro: Any) -> int:
        coro.close()
        raise raised

    monkeypatch.setattr(entry_mod, "_inject_truststore", lambda: None)
    monkeypatch.setattr(entry_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(entry_mod, "register_providers", lambda: None)
    monkeypatch.setattr(entry_mod.asyncio, "run", _run)
    monkeypatch.setattr(entry_mod.sys, "argv", ["aelix", "-p", "hi"])
    entry_mod.main_sync()


def _runner_interrupt(cancelled: asyncio.CancelledError) -> KeyboardInterrupt:
    """``asyncio.Runner.run``'s ``KeyboardInterrupt``, raised over the task's cancel."""

    try:
        try:
            raise cancelled
        except asyncio.CancelledError:
            raise KeyboardInterrupt  # noqa: B904
    except KeyboardInterrupt as exc:
        return exc


def test_main_sync_says_one_line_for_a_ctrl_c_the_loader_marked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cancelled = asyncio.CancelledError()
    cancelled._aelix_extension_interrupted = ("/x/slow.py", "setup()")  # type: ignore[attr-defined]
    # Caught here as well: an escaping KeyboardInterrupt would end the pytest run.
    with pytest.raises((SystemExit, KeyboardInterrupt)) as exc:
        _main_sync_with(monkeypatch, _runner_interrupt(cancelled))
    assert isinstance(exc.value, SystemExit), "main_sync re-raised it: a traceback"
    assert exc.value.code == 130
    err = capsys.readouterr().err
    assert err == "aelix: interrupted while the extension /x/slow.py was running its setup()\n"


def test_main_sync_says_so_for_a_ctrl_c_between_two_extensions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Fix round 1 (Codex M): it landed in the loader's own code; it names none.
    cancelled = asyncio.CancelledError()
    cancelled._aelix_extension_interrupted = (None, None)  # type: ignore[attr-defined]
    with pytest.raises((SystemExit, KeyboardInterrupt)) as exc:
        _main_sync_with(monkeypatch, _runner_interrupt(cancelled))
    assert isinstance(exc.value, SystemExit), "main_sync re-raised it: a traceback"
    assert exc.value.code == 130
    assert capsys.readouterr().err == "aelix: interrupted while loading extensions\n"


def test_main_sync_keeps_a_ctrl_c_outside_any_extension(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(KeyboardInterrupt):
        _main_sync_with(monkeypatch, _runner_interrupt(asyncio.CancelledError()))
    assert capsys.readouterr().err == ""


def _launch(
    tmp_path: Path,
    server: _Scripted,
    ext_body: str,
    ready: Path,
    *,
    after: Path | None = None,
) -> subprocess.Popen[str]:
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "scripted": {
                        "baseUrl": f"http://127.0.0.1:{server.port}/v1",
                        "apiKey": "sk-scripted",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "s-1",
                                "name": "Scripted",
                                "contextWindow": 32000,
                                "maxTokens": 500,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    ext = tmp_path / "the_ext.py"
    ext.write_text(ext_body.replace("READY", repr(str(ready))), encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if not (
            k.endswith(("_API_KEY", "_TOKEN"))
            or k.startswith(("AELIX_", "OPENROUTER", "ANTHROPIC"))
        )
        and k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        AELIX_CODING_AGENT_DIR=str(agent),
        PI_OFFLINE="1",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "aelix_coding_agent",
            "-e",
            str(ext),
            *(("-e", str(after)) if after is not None else ()),
            "--no-session",
            "--provider",
            "scripted",
            "--model",
            "s-1",
            "-p",
            "hi",
        ],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


_ASYNC = (
    "import asyncio\nfrom pathlib import Path\n\n"
    "async def setup(aelix):\n"
    "    Path(READY).write_text('started')\n"
    "    await asyncio.sleep(60)\n"
)
# A sync setup() holds the loop: the SIGINT handler's cancel waits for it, so the
# sleep is short — it is how long the run takes after the signal.
_SYNC = (
    "import time\nfrom pathlib import Path\n\n"
    "def setup(aelix):\n"
    "    Path(READY).write_text('started')\n"
    "    time.sleep(2)\n"
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT to a child process")
@pytest.mark.parametrize("body", [_ASYNC, _SYNC], ids=["async-setup", "sync-setup"])
def test_print_mode_ends_a_ctrl_c_during_setup_with_one_line(
    tmp_path: Path,
    scripted: _Scripted,  # noqa: F811
    body: str,
) -> None:
    ready = tmp_path / "ready"
    proc = _launch(tmp_path, scripted, body, ready)
    try:
        deadline = time.monotonic() + 90
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the extension's setup() never started"
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 130, err
    # The loader names the ``-e`` path as discovery resolved it (#664's row).
    [line] = err.strip().splitlines()
    assert line.startswith("aelix: interrupted while the extension ")
    assert line.endswith("the_ext.py was running its setup()")
    assert "Traceback" not in err
    assert out == ""
    assert scripted.requests == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT to a child process")
@pytest.mark.parametrize(
    "body",
    [
        _SYNC + "    raise RuntimeError('setup failed after the ctrl-c')\n",
        (
            "import asyncio\nfrom pathlib import Path\n\n"
            "async def setup(aelix):\n"
            "    Path(READY).write_text('started')\n"
            "    try:\n"
            "        await asyncio.sleep(60)\n"
            "    finally:\n"
            "        raise RuntimeError('cleanup failed')\n"
        ),
    ],
    ids=["sync-setup-raises", "async-finally-raises"],
)
def test_a_ctrl_c_in_a_setup_that_then_fails_still_ends_the_run_with_one_line(
    tmp_path: Path,
    scripted: _Scripted,  # noqa: F811
    body: str,
) -> None:
    """Fix round 1 (Codex H): the failure was recorded and the run went on — the
    next extension loaded, then a traceback (sync) or a model request and exit 0
    (async)."""

    ready = tmp_path / "ready"
    ran = tmp_path / "after.ran"
    after = tmp_path / "after.py"
    after.write_text(
        f"from pathlib import Path\n\ndef setup(aelix):\n    Path({str(ran)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    proc = _launch(tmp_path, scripted, body, ready, after=after)
    try:
        deadline = time.monotonic() + 90
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "the extension's setup() never started"
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 130, err
    [line] = err.strip().splitlines()
    assert line.startswith("aelix: interrupted while the extension ")
    assert line.endswith("the_ext.py was running its setup()")
    assert out == ""
    assert not ran.exists(), "the extension after the interrupted one loaded"
    assert scripted.requests == 0


def test_print_mode_runs_with_a_taskgroup_extension_and_no_ctrl_c(
    tmp_path: Path,
    scripted: _Scripted,  # noqa: F811
) -> None:
    """Fix round 2 (verify B1): on 3.11 and 3.12 a TaskGroup whose child fails
    after its body finished leaves the task's cancel count up (fixed in 3.13);
    round 1 read that as a Ctrl+C and launch ended in a CancelledError traceback,
    exit 1, before the model was asked."""

    ran = tmp_path / "after.ran"
    after = tmp_path / "after.py"
    after.write_text(
        f"from pathlib import Path\n\ndef setup(aelix):\n    Path({str(ran)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    body = (
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
    proc = _launch(tmp_path, scripted, body, tmp_path / "unused", after=after)
    try:
        out, err = proc.communicate(timeout=90)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, err
    assert "Traceback" not in err
    assert out.strip() == "OK"
    assert ran.exists()
    assert scripted.requests == 1
