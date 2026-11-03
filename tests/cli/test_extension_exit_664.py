"""#664 — ``aelix -p`` and ``main_sync`` with an extension whose ``setup()`` exits or is interrupted.

Measured before (``.omc/specs/25-followups-evidence/i2b/``): ``-e exit_ext.py -p hi``
printed nothing at all and exited 1 with no model request; ``-e kbi_ext.py``
printed a ``KeyboardInterrupt`` traceback and exited 130.

The real-process rows run ``python -m aelix_coding_agent`` against a scripted
OpenAI-compatible server on 127.0.0.1, with a scratch HOME and agent dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import aelix_coding_agent.cli.entry as entry_mod
import pytest
from aelix_coding_agent.extensions.loader import ExtensionInterrupted


def test_main_sync_says_one_line_for_an_extension_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    ext = str(tmp_path / "kbi_ext.py")

    def _run(coro: Any) -> int:
        coro.close()
        raise ExtensionInterrupted(ext, "setup()")

    monkeypatch.setattr(entry_mod, "_inject_truststore", lambda: None)
    monkeypatch.setattr(entry_mod, "load_dotenv", lambda: None)
    monkeypatch.setattr(entry_mod, "register_providers", lambda: None)
    monkeypatch.setattr(entry_mod.asyncio, "run", _run)
    monkeypatch.setattr(entry_mod.sys, "argv", ["aelix", "-p", "hi"])
    with pytest.raises(SystemExit) as exc:
        entry_mod.main_sync()
    assert exc.value.code == 130
    err = capsys.readouterr().err
    assert err == f"aelix: interrupted while the extension {ext} was running its setup()\n"


# --- real processes ---------------------------------------------------------------------


class _Scripted:
    """Answers every chat completion with ``OK`` as OpenAI SSE; counts the requests."""

    def __init__(self) -> None:
        self.requests = 0
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: Any) -> None:
                return None

            def do_POST(self) -> None:  # noqa: N802 — http.server API
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, finish in (({"role": "assistant", "content": "OK"}, None), ({}, "stop")):
                    chunk = {
                        "id": "c664",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "s-1",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def scripted() -> Iterator[_Scripted]:
    server = _Scripted()
    try:
        yield server
    finally:
        server.close()


def _run(tmp_path: Path, server: _Scripted, ext_body: str) -> subprocess.CompletedProcess[str]:
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
                        "models": [{"id": "s-1", "name": "Scripted", "contextWindow": 32000, "maxTokens": 500}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    ext = tmp_path / "the_ext.py"
    ext.write_text(ext_body, encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if not (k.endswith(("_API_KEY", "_TOKEN")) or k.startswith(("AELIX_", "OPENROUTER", "ANTHROPIC")))
        and k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        AELIX_CODING_AGENT_DIR=str(agent),
        PI_OFFLINE="1",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "-e", str(ext), "--no-session",
         "--provider", "scripted", "--model", "s-1", "-p", "hi"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        ("sys.exit(1)", "error='SystemExit: sys.exit(1)'"),
        # Verify V9: this read error='bye: setup gave up', as a RuntimeError's would.
        ("raise SystemExit('bye: setup gave up')", "error='SystemExit: bye: setup gave up'"),
    ],
)
def test_print_mode_runs_past_an_extension_that_exits(
    tmp_path: Path, scripted: _Scripted, raised: str, said: str
) -> None:
    done = _run(tmp_path, scripted, f"import sys\n\ndef setup(aelix):\n    {raised}\n")
    assert done.returncode == 0, done.stderr
    assert "OK" in done.stdout
    assert "Warning: extension load:" in done.stderr and said in done.stderr
    assert scripted.requests == 1


def test_print_mode_ends_on_an_interrupted_setup_with_one_line(
    tmp_path: Path, scripted: _Scripted
) -> None:
    done = _run(tmp_path, scripted, "def setup(aelix):\n    raise KeyboardInterrupt\n")
    assert done.returncode == 130, done.stderr
    # The loader names the ``-e`` path as discovery resolved it, which is not
    # spelled like ``tmp_path`` everywhere (a short or linked temp directory).
    [line] = done.stderr.strip().splitlines()
    assert line.startswith("aelix: interrupted while the extension ")
    assert line.endswith("the_ext.py was running its setup()")
    assert "Traceback" not in done.stderr
    assert scripted.requests == 0
