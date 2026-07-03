"""Issue #57 — CLI pipe robustness (aelix-original hardening).

Two defect classes, both reachable in production:

1. stdin hang — ``_read_piped_stdin`` read-to-EOF blocked forever on a
   non-TTY pipe whose writer never closes, reachable with ZERO flags (any
   piped stdin promotes app_mode to "print"). pi DECLINED the same report
   (pi#5571, workaround ``</dev/null``), so the select-based first-byte
   deadline here is aelix-original.
2. stdout EPIPE — no BrokenPipeError guard existed anywhere: ``-p`` text
   died on the interpreter's shutdown flush ("Exception ignored", exit
   120), JSON mode kept writing every event to a dead pipe and exited 0.
   Now: quiet exit 141 (128+SIGPIPE pipeline convention) via the
   ``main_sync`` top-level guard.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli.entry import _read_piped_stdin
from aelix_coding_agent.modes.print_mode import run_print_mode

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="select-based stdin guard + EPIPE semantics are POSIX-gated",
)


# === stdin first-byte deadline (hang guard) ===================================


@posix_only
async def test_read_piped_stdin_times_out_on_silent_pipe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pipe whose writer never sends anything must NOT hang: after the
    deadline, warn on stderr and proceed as if no stdin input was given."""
    read_fd, write_fd = os.pipe()  # writer stays OPEN → no data, no EOF
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv("AELIX_STDIN_TIMEOUT", "0.1")
    try:
        started = time.monotonic()
        # wait_for (review MEDIUM): if the deadline gate regresses, this
        # test must FAIL in 15s — not hang the whole run (no pytest-timeout
        # plugin is configured). The stranded reader thread is acceptable
        # inside a failing test.
        result = await asyncio.wait_for(_read_piped_stdin(), timeout=15)
        elapsed = time.monotonic() - started
    finally:
        os.close(write_fd)
        stdin.close()
    assert result is None
    assert elapsed < 5  # returned at the 0.1s deadline, not hung
    assert "AELIX_STDIN_TIMEOUT" in capsys.readouterr().err


@posix_only
async def test_read_piped_stdin_reads_ready_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal piped usage (`echo hi | aelix`) is unaffected by the guard."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"  hello from pipe \n")
    os.close(write_fd)  # EOF delivered
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        result = await _read_piped_stdin()
    finally:
        stdin.close()
    assert result == "hello from pipe"


@posix_only
async def test_read_piped_stdin_empty_closed_pipe_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`aelix </dev/null`-style: EOF with no data → None, instantly."""
    read_fd, write_fd = os.pipe()
    os.close(write_fd)  # immediate EOF
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        result = await _read_piped_stdin()
    finally:
        stdin.close()
    assert result is None


@posix_only
async def test_read_piped_stdin_timeout_zero_disables_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AELIX_STDIN_TIMEOUT=0 skips the readiness gate (wait-forever opt-in);
    with data already present the read still succeeds."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"payload")
    os.close(write_fd)
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv("AELIX_STDIN_TIMEOUT", "0")
    try:
        result = await _read_piped_stdin()
    finally:
        stdin.close()
    assert result == "payload"


@posix_only
async def test_read_piped_stdin_inf_timeout_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AELIX_STDIN_TIMEOUT=inf (the natural wait-forever spelling) must not
    crash: ``select`` rejects inf/huge timeouts with OverflowError, and the
    guard fails OPEN to the blocking read — which here has data+EOF ready."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"payload")
    os.close(write_fd)
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv("AELIX_STDIN_TIMEOUT", "inf")
    try:
        result = await asyncio.wait_for(_read_piped_stdin(), timeout=15)
    finally:
        stdin.close()
    assert result == "payload"


@posix_only
async def test_stdin_timeout_warning_survives_dead_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead STDERR consumer must not abort a healthy run: the deadline
    warning print is suppressed instead of raising BrokenPipeError (which
    main_sync would misclassify as stdout death → exit 141 + devnull the
    LIVE stdout)."""

    class _BrokenStderr:
        def write(self, _text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    read_fd, write_fd = os.pipe()  # silent pipe → deadline fires
    stdin = os.fdopen(read_fd)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stderr", _BrokenStderr())
    monkeypatch.setenv("AELIX_STDIN_TIMEOUT", "0.1")
    try:
        result = await asyncio.wait_for(_read_piped_stdin(), timeout=15)
    finally:
        os.close(write_fd)
        stdin.close()
    assert result is None  # proceeded quietly; no BrokenPipeError escaped


async def test_read_piped_stdin_fake_stdin_without_fileno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stdin double with no real fd (pytest capture, embedders) skips the
    select gate and reads directly — the shape every existing entry-router
    test injects."""

    class _FakePipedStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> str:
            return ""

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    assert await _read_piped_stdin() is None


# === stdout EPIPE propagation (print/JSON modes) ==============================


def _ok_stream(reply: str = "hello-from-mock") -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=reply)],
                stop_reason="end_turn",
            )
        )

    return fn


def _new_runtime(stream_fn: Any) -> AgentSessionRuntime:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=stream_fn,
        )
    )

    async def _noop(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(
        harness,
        _noop,
        repo=JsonlSessionRepo(fs=LocalFileSystem()),
        fs=LocalFileSystem(),
    )


class _BrokenStdout:
    """stdout double whose consumer has vanished — every write raises."""

    def __init__(self) -> None:
        self.write_attempts = 0

    def write(self, _text: str) -> int:
        self.write_attempts += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self) -> None:  # pragma: no cover — write raises first
        raise BrokenPipeError(32, "Broken pipe")


async def test_text_mode_epipe_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final text printout hitting a dead pipe must PROPAGATE
    BrokenPipeError to main_sync's guard (quiet 141) — not mask it as
    exit 1 with a dirty buffer (the old behavior, which then crashed the
    interpreter's shutdown flush → exit 120)."""
    runtime = _new_runtime(_ok_stream())
    monkeypatch.setattr(sys, "stdout", _BrokenStdout())
    with pytest.raises(BrokenPipeError):
        await run_print_mode(
            runtime, mode="text", messages=[], initial_message="ping"
        )


async def test_json_mode_dead_consumer_raises_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON mode: a consumer that vanishes mid-run is recorded by the event
    emitter (subscribers must not raise — harness dispatch swallows listener
    errors, pi parity) and surfaced AFTER the prompt loop, instead of the
    old behavior (suppress every write, run to completion, exit 0)."""
    runtime = _new_runtime(_ok_stream())
    monkeypatch.setattr(sys, "stdout", _BrokenStdout())
    with pytest.raises(BrokenPipeError):
        await run_print_mode(
            runtime, mode="json", messages=[], initial_message="ping"
        )


async def test_json_mode_stops_writing_to_dead_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the first EPIPE the emitter must stop attempting writes (the
    old suppress-and-continue retried the dead pipe for every event)."""
    runtime = _new_runtime(_ok_stream())
    broken = _BrokenStdout()
    monkeypatch.setattr(sys, "stdout", broken)
    with pytest.raises(BrokenPipeError):
        await run_print_mode(
            runtime, mode="json", messages=[], initial_message="ping"
        )
    # The mock turn emits multiple events (start/end/...); only the FIRST
    # may touch the dead pipe.
    assert broken.write_attempts == 1


# === end-to-end: exit code 141, no traceback ==================================


@posix_only
def test_main_sync_exits_141_on_broken_stdout() -> None:
    """`aelix --version | (consumer exits immediately)` must exit 141
    quietly — no traceback, no 'Exception ignored' interpreter noise
    (which previously forced exit 120)."""
    repo_root = Path(__file__).resolve().parents[2]
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # consumer already gone → every write is EPIPE
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "aelix_coding_agent", "--version"],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=str(repo_root),
            timeout=120,
        )
    finally:
        os.close(write_fd)
    assert proc.returncode == 141, proc.stderr.decode(errors="replace")
    stderr = proc.stderr.decode(errors="replace")
    assert "Traceback" not in stderr
    assert "Exception ignored" not in stderr
