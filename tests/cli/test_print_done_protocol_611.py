"""A ``--mode json`` run writes the versioned ``aelix_print_done`` line only when asked (#611 S1c, ADR-0295).

The parent of a background delegation sets ``AELIX_JOBS_PROTOCOL=1`` in the
child's environment (``JobContext.protocol_env``) and then needs the line
before it counts a run that lingered as finished (``envelope.build_result``).
Nobody else sets the variable, so every other run's stream is what it was —
and with the gate off nothing sets it at all. The child honours the variable
only while its OWN gate is on as well: with ``ACTIVE`` off a run's stream is
what it was even when the variable was inherited (Codex #1).

Fails on ``6caaae09``: ``run_print_mode`` writes no such line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.streaming import Model
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.contract import JOBS_PROTOCOL_ENV, PRINT_DONE_EVENT
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.jobs.conftest import RecordingModel, make_session


async def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> tuple[int, list[str]]:
    written: list[str] = []
    monkeypatch.setattr(print_mode, "_write_raw_stdout", written.append)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=RecordingModel("answered"),
            session=await make_session(tmp_path),
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(
        harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem()
    )
    code = await run_print_mode(runtime, mode=mode, messages=[], initial_message="go")  # type: ignore[arg-type]
    return code, written


def _events(written: list[str]) -> list[Any]:
    out = []
    for chunk in written:
        for line in chunk.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append(line)
    return out


async def test_json_mode_writes_print_done_last_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setenv(JOBS_PROTOCOL_ENV, "1")
    code, written = await _run(tmp_path, monkeypatch, "json")
    events = _events(written)
    assert code == 0
    assert events[-1] == {"type": PRINT_DONE_EVENT}
    assert [e for e in events if isinstance(e, dict) and e.get("type") == PRINT_DONE_EVENT] == [
        {"type": PRINT_DONE_EVENT}
    ]
    agent_end = max(
        i for i, e in enumerate(events) if isinstance(e, dict) and e.get("type") == "agent_end"
    )
    assert agent_end < len(events) - 1, "after the run's own terminator"


@pytest.mark.parametrize("value", [None, "0", "2"])
async def test_json_mode_without_the_request_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(JOBS_PROTOCOL_ENV, raising=False)
    else:
        monkeypatch.setenv(JOBS_PROTOCOL_ENV, value)
    _code, written = await _run(tmp_path, monkeypatch, "json")
    assert all(
        not (isinstance(e, dict) and e.get("type") == PRINT_DONE_EVENT) for e in _events(written)
    )


async def test_text_mode_never_writes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JOBS_PROTOCOL_ENV, "1")
    _code, written = await _run(tmp_path, monkeypatch, "text")
    assert PRINT_DONE_EVENT not in "".join(written)


async def test_with_the_gate_off_an_inherited_request_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex #1: ``ACTIVE`` off and ``AELIX_JOBS_PROTOCOL=1`` inherited — no extra line.

    Fails on ``b387b4b5``: the stream ended with ``aelix_print_done``.
    """

    assert not activation.is_active()
    monkeypatch.delenv(JOBS_PROTOCOL_ENV, raising=False)
    _code, plain = await _run(tmp_path / "plain", monkeypatch, "json")
    monkeypatch.setenv(JOBS_PROTOCOL_ENV, "1")
    _code, asked = await _run(tmp_path / "asked", monkeypatch, "json")
    kinds = [e.get("type") if isinstance(e, dict) else e for e in _events(asked)]
    assert PRINT_DONE_EVENT not in kinds
    assert kinds == [e.get("type") if isinstance(e, dict) else e for e in _events(plain)]
