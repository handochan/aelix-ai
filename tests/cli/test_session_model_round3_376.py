"""#376 review round 3 — through the real ``_async_main``: the order of the prompt check, the fallback line, ``--fork``.

* **The check comes after the ``input`` hook** (pi: ``agent-session.ts:1993``,
  then ``:2032-2050`` at ``pi@1cedd3272``). Round 2 asked in RPC's
  ``_handle_prompt`` and the TUI's input loop, before the hook: an extension
  that handled the input itself on a keyless setup was refused (it worked on
  ``8f7d98aa``), a model an ``input`` handler switched to was never judged (the
  prompt was accepted, written, and failed ``No API key for provider:
  openai``), and an extension's ``send_message(..., trigger_turn=True)`` from
  ``session_start`` skipped the question (written, failed ``No API key for
  provider: anthropic``). Codex r2 cat1 and cat3.
* **The fallback line names the model the run is on when it is said.** It was
  computed before ``session_start`` and said after it, so a handler that moved
  the model there left it naming the model the build had fallen back to (Codex
  r2 cat2: ``ACTIVE_ROUTE anthropic/claude-haiku-4-5`` beside ``Using
  anthropic/claude-sonnet-4-5``).
* **Startup ``--fork`` restores too** (Codex r2 cat4: a mutant skipping it
  passed every row).

Review round 4 (owner decision 2026-10-08): the check asks no credential, so
the rows that judged a keyless model now judge one with no adapter
(``newlab/model-x``, ``api='unknown'``); a keyless prompt runs as on ``main``
(``tests/cli/test_session_model_round4_376.py``).

Hermetic: fake keys, an isolated agent dir and home, no network — every prompt
here is refused or handled before a request.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)

from tests.cli import test_session_model_record_376 as _record
from tests.cli.test_session_model_restore_376 import (
    _HAIKU,
    _SONNET,
    _ident,
    _session,
    _sessions,
    _settings,
    _stub_modes,
)

env = _record.env  # the sandbox fixture: no key, an isolated agent dir and home


def _messages(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        entries = [json.loads(line) for line in handle if line.strip()]
    return [e for e in entries if e.get("type") == "message"]


def _extension(env: Path, name: str, source: str) -> str:
    path = env / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return str(path)


async def _rpc(env: Path, monkeypatch: pytest.MonkeyPatch, drive: Any, *extra: str) -> int:
    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    return await entry_mod._async_main(
        ["--continue", "--session-dir", _sessions(env), "--mode", "rpc", *extra]
    )


async def _settle(harness: Any) -> list[Any]:
    return await asyncio.wait_for(
        asyncio.gather(*list(harness._pending_tasks), return_exceptions=True), 10
    )


_INPUT_HANDLED = """
from pathlib import Path
from aelix_agent_core.harness.hooks import InputHandled

def setup(aelix):
    def handle(event, ctx):
        if event.text == "ping":
            Path({marker!r}).write_text("pong", encoding="utf-8")
            return InputHandled()
    aelix.on("input", handle)
"""

_SWITCH_TO_NEWLAB = """
from aelix_ai.streaming import Model

def setup(aelix):
    async def handle(event, ctx):
        if event.text == "use-newlab":
            await aelix.set_model(Model(id="model-x", provider="newlab"))
    aelix.on("input", handle)
"""

_TRIGGER_IN_SESSION_START = """
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Model

def setup(aelix):
    async def started(event, ctx):
        await aelix.set_model(Model(id="model-x", provider="newlab"))
        aelix.send_message(
            UserMessage(content=[TextContent(text="unrunnable-trigger")]), trigger_turn=True
        )
    aelix.on("session_start", started)
"""

_HAIKU_IN_SESSION_START = """
from aelix_ai.models import get_model

def setup(aelix):
    async def started(event, ctx):
        await aelix.set_model(get_model("anthropic", "claude-haiku-4-5"))
    aelix.on("session_start", started)
"""


# === 1. the check comes after the input hook ==================================


async def test_an_input_an_extension_handles_works_without_a_credential(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 cat3: ``8f7d98aa`` RPC_SUCCESS True / INPUT_HANDLER_CALLED True;
    round 2 RPC_SUCCESS False / INPUT_HANDLER_CALLED False."""

    marker = env / "pong.txt"
    ext = _extension(env, "handled", _INPUT_HANDLED.format(marker=str(marker)))
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    path = await _session(env, _HAIKU)  # no key anywhere
    before = _messages(path)
    seen: list[Any] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen.append(await _handle_prompt(live, RpcCommandPrompt(message="ping", id="p1")))
        await _settle(live)
        seen.append(live.phase)

    assert await _rpc(env, monkeypatch, drive, "-e", ext) == 0

    response, phase = seen
    assert isinstance(response, RpcSuccessResponse), response
    assert marker.read_text(encoding="utf-8") == "pong"
    assert phase == "idle"
    assert _messages(path) == before


async def test_a_model_an_input_hook_switches_to_that_cannot_run_is_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 cat1 (switch), round 4's form: the hook moves the prompt to a
    model with no adapter; round 2 accepted and wrote it. The model the hook
    chose is the one judged, and nothing is written."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    ext = _extension(env, "switch", _SWITCH_TO_NEWLAB)
    path = await _session(env, _HAIKU)
    before = _messages(path)
    seen: list[Any] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen.append(_ident(live.current_model))
        seen.append(await _handle_prompt(live, RpcCommandPrompt(message="use-newlab", id="p1")))
        await _settle(live)
        seen.append(_ident(live.current_model))

    assert await _rpc(env, monkeypatch, drive, "-e", ext) == 0

    start, response, after = seen
    assert start == _HAIKU
    assert isinstance(response, RpcErrorResponse), response
    assert "model-x" in response.error, response.error
    assert after == ("newlab", "model-x")
    assert _messages(path) == before


@pytest.mark.parametrize("mode", [["-p"], ["--mode", "json"]], ids=["print", "json"])
async def test_print_and_json_judge_the_model_an_input_hook_switched_to(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: list[str],
) -> None:
    """The real print/json mode, no stub: the launch gate passed (haiku, keyed),
    the hook moved the prompt to a model with no adapter, and the harness
    refused it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    ext = _extension(env, "switch", _SWITCH_TO_NEWLAB)
    path = await _session(env, _HAIKU)
    before = _messages(path)

    code = await entry_mod._async_main(
        ["--continue", "--session-dir", _sessions(env), *mode, "-e", ext, "use-newlab"]
    )

    err = capsys.readouterr().err
    assert code == 1, err
    assert "model-x" in err, err
    assert _messages(path) == before


async def test_a_turn_session_start_triggers_on_a_model_that_cannot_run_writes_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 cat1 (trigger), round 4's form: a ``session_start`` handler
    moves to a model with no adapter and triggers a turn; round 2 wrote the
    trigger and its failure."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    ext = _extension(env, "trigger", _TRIGGER_IN_SESSION_START)
    path = await _session(env, _HAIKU)
    before = _messages(path)
    seen: list[Any] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen.append([str(o) for o in await _settle(live)])
        seen.append(len(live.state.messages))

    assert await _rpc(env, monkeypatch, drive, "-e", ext) == 0

    outcomes, in_state = seen
    assert len(outcomes) == 1, outcomes
    assert "model-x" in outcomes[0], outcomes
    assert in_state == len(before)
    assert _messages(path) == before


# === 2. the fallback line names the model the run is on =======================


async def test_the_fallback_line_names_the_model_session_start_moved_to(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r2 cat2: the session records a model no registry knows; the build
    falls back to the settings default (sonnet), then a ``session_start``
    handler selects haiku. Round 2 said ``Using anthropic/claude-sonnet-4-5``
    while haiku was active — at launch, and after a swap (``switch_session``
    here; the TUI's ``/resume`` reads the same runtime member).

    (A ``/reload`` of the launch session would restore haiku: the handler's
    idle ``set_model`` is that session's record now, and no line is owed.)"""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    ext = _extension(env, "notice", _HAIKU_IN_SESSION_START)
    other = await _session(env, ("removed-provider", "removed-model"))
    first = await _session(env, ("removed-provider", "removed-model"))
    seen: list[Any] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        seen.append((_ident(runtime_host.harness.current_model), runtime_host.model_fallback_message))
        await runtime_host.switch_session(other)
        seen.append((_ident(runtime_host.harness.current_model), runtime_host.model_fallback_message))

    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    argv = ["--session", first, "--session-dir", _sessions(env), "--mode", "rpc", "-e", ext]
    assert await entry_mod._async_main(argv) == 0

    line = "Could not restore model removed-provider/removed-model. Using anthropic/claude-haiku-4-5"
    assert seen == [(_HAIKU, line), (_HAIKU, line)], seen
    err = capsys.readouterr().err
    assert f"Warning: {line}" in err.splitlines(), err
    assert "Using anthropic/claude-sonnet-4-5" not in err


async def test_without_a_session_start_move_the_line_names_the_fallback(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the row above: nothing moves the model, the line names
    the settings default the build fell back to."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    await _session(env, ("removed-provider", "removed-model"))
    seen = _stub_modes(monkeypatch)

    assert (
        await entry_mod._async_main(
            ["--continue", "--session-dir", _sessions(env), "--mode", "rpc"]
        )
        == 0
    )

    assert _ident(seen["model"]) == _SONNET
    assert seen["fallback"] == (
        "Could not restore model removed-provider/removed-model. Using anthropic/claude-sonnet-4-5"
    )


# === 3. startup --fork restores =================================================


async def test_a_fork_without_flags_comes_up_on_the_sessions_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r2 cat4: ``if not _user_named_a_model() and parsed.fork is None``
    passed every changed test file; under it a forked haiku session came up on
    the settings default."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    path = await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        ["--fork", path, "--session-dir", _sessions(env), "--mode", "rpc"]
    )

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _HAIKU, err
    assert seen["fallback"] is None
    assert seen["runtime"].harness.session.session_file != path  # a fork, not the original
