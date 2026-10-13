"""#408 - the real CLI's turn auto-retry follows ``settings.json``'s ``retry``.

The real ``_async_main`` in ``--mode json`` builds its harness with the one
startup ``SettingsManager`` (``cli/entry.py`` ``_build_harness_options``), so
the settings file the CLI reads is the one the retry obeys - the TUI, ``-p``,
json and rpc share that construction. Model requests go to an
``httpx.MockTransport`` recorder that answers ``502`` (the #379 fixtures);
fake keys, an isolated agent dir, no socket.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_launch_route_344 import _FakePipedStdin
from tests.cli.test_refresh_retry_379 import _MODEL, _Endpoints, agent, endpoints

__all__ = ["agent", "endpoints"]  # the fixtures, re-used


async def _json_run(
    agent: Path,
    endpoints: _Endpoints,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settings: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    (agent / "settings.json").write_text(json.dumps(settings), "utf-8")
    endpoints.model_plan = ["502"] * 10
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    rc = await entry_mod._async_main(
        ["--no-session", "--provider", "anthropic", "--model", _MODEL["anthropic"], "--mode", "json", "-p", "hi"]
    )
    out = capsys.readouterr().out
    return rc, [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def _starts(lines: list[dict[str, Any]]) -> list[tuple[Any, Any, Any]]:
    """aelix's wire is snake_case (``max_attempts``, ``delay_ms``); pi's is camelCase."""

    return [
        (ln["attempt"], ln["max_attempts"], ln["delay_ms"])
        for ln in lines
        if ln.get("type") == "auto_retry_start"
    ]


async def test_retry_enabled_false_sends_one_request(
    agent: Path, endpoints: _Endpoints, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, lines = await _json_run(agent, endpoints, monkeypatch, capsys, {"retry": {"enabled": False}})
    assert len(endpoints.model_auth) == 1
    assert _starts(lines) == []
    assert [ln for ln in lines if ln.get("type") == "auto_retry_end"] == []


async def test_max_retries_and_base_delay_from_the_file(
    agent: Path, endpoints: _Endpoints, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, lines = await _json_run(
        agent, endpoints, monkeypatch, capsys, {"retry": {"maxRetries": 1, "baseDelayMs": 3}}
    )
    assert len(endpoints.model_auth) == 2
    assert _starts(lines) == [(1, 1, 3)]
    # Review round 2: the guide names these keys; pin the spelling it names.
    [start] = [ln for ln in lines if ln.get("type") == "auto_retry_start"]
    assert sorted(start) == ["attempt", "delay_ms", "error_message", "max_attempts", "type"]
    [end] = [ln for ln in lines if ln.get("type") == "auto_retry_end"]
    assert sorted(end) == ["attempt", "final_error", "success", "type"]
    assert (end["success"], end["attempt"]) == (False, 1)


async def test_an_empty_settings_file_keeps_the_fallbacks(
    agent: Path, endpoints: _Endpoints, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``{}``: three retries at the (fixture-patched, 1 ms) base - never real 2/4/8 s."""

    _, lines = await _json_run(agent, endpoints, monkeypatch, capsys, {})
    assert len(endpoints.model_auth) == 4
    assert _starts(lines) == [(1, 3, 1), (2, 3, 2), (3, 3, 4)]
