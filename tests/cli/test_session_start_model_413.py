"""#413 — a new session records the model it starts on, as pi does.

pi appends ``model_change`` for the model of every session with no messages
yet, inside ``createAgentSession`` (``core/sdk.ts:449-459`` at
``pi@3daa20280``): before ``bindExtensions`` emits ``session_start``, and on
``newSession`` before its ``setup`` runs (``agent-session-runtime.ts:226-262``).
aelix wrote none: on ``7d163232`` a session whose every answer failed without
provenance (a hook that raised before the request) held no model at all, so
``--continue`` without ``--model`` came back on the settings default instead
(``.omc/probes/413-live/impl/``, phase X).

These drive the real ``_async_main`` (the mode entry replaced by a driver; no
turn reaches a provider) with the sandbox of ``test_session_model_restore_376.py``
and read the session file the run wrote:

* every mode's new session starts with ``model_change`` naming the launch model,
  with or without a credential; ``/new`` and a ``/fork`` onto the empty branch
  do too, a fork that keeps messages does not;
* the record is written when the session is CREATED, so messages written while
  ``session_start`` runs (a handler's turn, awaited or not) or by
  ``new_session``'s ``setup`` land after it (review round 2, item 1);
* a launch whose route is decided only after ``session_start`` (#367's pending
  launch) and a rebuild that resolved to such a route record after that
  decision, still for a session created empty, and never the hold's
  placeholder (items 1, 2);
* nothing more on a reopen: a session with a conversation keeps its own record,
  an empty one gains a record only when its LATEST record names another model;
  ``switch_session``, ``import`` and ``/reload`` onto such a session record
  (item 4);
* a storage failure on the record is a warning, not a crash (item 3);
* a delegated child's ``aelix.child_origin`` stays first (#199);
* review round 3: a deferred record does not outrank an answer written after
  the session was created that names its model (where pi's record, written
  first, would not either), a pending route meeting a session with a
  conversation records nothing, and every reopen of a run that left two files
  names the file (``--session``), since ``--continue`` picks by mtime and two
  files written within one NTFS tick (~15.6 ms) tie;
* review round 4: the provenance check reads every message written since, not the
  last; every pending rebuild records (``switch_session``, ``import``, ``/reload``,
  not only ``new_session``); a pending launch records in every mode; and ``/clone``
  and RPC ``clone`` refuse a session with no conversation, on disk and under
  ``--no-session``, with the words 7d163232 used, now that the start record gives
  it a leaf.

Hermetic: fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agent_core.session.entries import CustomEntry
from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.read_only import ReadOnlySessionStorage
from aelix_agent_core.session.session import Session
from aelix_agent_core.session.storage import SessionError
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.cli.test_session_model_restore_376 import (
    _HAIKU,
    _MODES,
    _SONNET,
    _ident,
    _sessions,
    _settings,
    _stub_modes,
)
from tests.env_sandbox import sandbox_home

_LAUNCH = ["--provider", "anthropic", "--model", "claude-haiku-4-5"]
#: #367 guard 2: an id no registered provider claims, sent to OpenRouter as
#: written on the user's own key — a PENDING launch route.
_GUARD2_ID = "somevendor/brand-new-model-x"
_GUARD2 = ("openrouter", _GUARD2_ID)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_session_model_restore_376.py``'s sandbox: no key, an empty agent dir."""

    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_", "CLOUDFLARE_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "models.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    register_providers()
    return tmp_path


def _files(env: Path) -> list[Path]:
    root = Path(_sessions(env))
    return sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns)


def _entries(path: Path | str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert lines and lines[0]["type"] == "session"
    return lines[1:]


def _records(path: Path | str) -> list[tuple[str, str]]:
    return [
        (e["provider"], e["modelId"]) for e in _entries(path) if e["type"] == "model_change"
    ]


def _shape(path: Path | str) -> list[str]:
    """Entry types, a message as its role: ``['model_change', 'user', 'assistant']``."""

    return [e["message"]["role"] if e["type"] == "message" else e["type"] for e in _entries(path)]


def _only_file(env: Path) -> Path:
    files = _files(env)
    assert len(files) == 1, files
    return files[0]


@pytest.fixture(params=list(_MODES))
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    return request.param


def _drive(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    """Every mode entry replaced by ``fn(runtime)``; print/json/TUI and RPC alike."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent import modes

    async def _print(runtime: Any, **kwargs: Any) -> int:
        await fn(runtime)
        return 0

    async def _rpc(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        await fn(runtime_host)

    monkeypatch.setattr(modes, "run_print_mode", _print)
    monkeypatch.setattr(modes, "run_rpc_mode", _rpc)
    monkeypatch.setattr(tui_pkg, "run_tui", _print)


async def _wait_idle(runtime: Any) -> None:
    """Let a turn a ``session_start`` handler triggered start, then end."""

    for _ in range(5):
        await asyncio.sleep(0)
    await runtime.harness.wait_for_idle()


async def _fail_one_turn(runtime: Any) -> None:
    """The #413 shape: one answer that names no model (a hook raised before the request)."""

    session = runtime.harness.session
    await session.append_message(UserMessage(content=[TextContent(text="q")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="[error] context hook handler raised")],
            stop_reason="error",
            error_message="context hook handler raised",
        )
    )


async def _reopened_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, *extra: str, session: str | None = None
) -> tuple[str, str]:
    """A reopen with no model flags over a settings default of sonnet.

    ``--continue`` when the run left ONE session file. A run that left two (a
    ``/new``) passes ``session``: ``--continue`` takes the file with the newest
    mtime, and two files written a few ms apart share one on NTFS (~15.6 ms
    tick), where the alphabetical listing then hands it the OLDER file (review
    round 3, item 1)."""

    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    seen = _stub_modes(monkeypatch)
    pick = ["--session", session] if session is not None else ["--continue", "--session-dir", _sessions(env)]
    code = await entry_mod._async_main([*pick, "--mode", "rpc", *extra])
    assert code == 0
    return _ident(seen["model"])


# === 1. a new session records its launch model, in every mode ==================


async def test_a_new_session_starts_with_its_model_recorded_in_every_mode(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """THE DEFECT: on 7d163232 the new file held the header and nothing else."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), *_MODES[mode], *_LAUNCH]
    )

    assert code == 0, capsys.readouterr().err
    assert _ident(seen["model"]) == _HAIKU
    entries = _entries(_only_file(env))
    assert [e["type"] for e in entries] == ["model_change"]
    assert (entries[0]["provider"], entries[0]["modelId"]) == _HAIKU
    assert entries[0]["parentId"] is None


async def test_the_issue_a_record_and_a_failure_without_provenance_reopen_on_the_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The case #413 was filed for. The session's one answer is a failure that names
    no model (``harness/core.py``'s turn-failure message: a hook raised before the
    request); reopened with no flags over a settings default, it comes back on the
    model it started on. On 7d163232 it came back on the default (sonnet)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _drive(monkeypatch, _fail_one_turn)
    assert (
        await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH])
        == 0
    )

    assert await _reopened_model(env, monkeypatch) == _HAIKU, capsys.readouterr().err
    assert _records(_only_file(env)) == [_HAIKU]


async def test_a_launch_with_no_credential_records_its_resolved_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Review round 2, item 5: pi records at ``createAgentSession`` whatever the
    credential state. A launch on a RESOLVED model with no key yet (print/json refuse
    the prompt, rpc/TUI start) writes exactly one file holding that one record, so a
    turn whose messages are written after ``/login`` (and that fails before the
    request) still reopens on it — the next row. A prompt refused for want of a key
    writes nothing (ADR-0263), so it leaves the session empty, and an empty
    session's reopen follows the launch inputs (#376). Round 1 wrote nothing here
    (and the row passed with no file)."""

    _stub_modes(monkeypatch)

    await entry_mod._async_main(["--session-dir", _sessions(env), *_MODES[mode], *_LAUNCH])

    path = _only_file(env)
    assert _shape(path) == ["model_change"]
    assert _records(path) == [_HAIKU]


async def test_a_no_key_launch_then_login_then_a_failed_turn_reopens_on_its_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cross-review p05: the key arrives after the launch (``/login`` stores it), and the
    session's one turn fails without provenance. On b9e09f01 nothing recorded haiku and
    the reopen landed on the settings default."""

    _drive(monkeypatch, _fail_one_turn)
    await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")  # what /login leaves behind

    assert await _reopened_model(env, monkeypatch) == _HAIKU, capsys.readouterr().err
    assert _shape(_only_file(env)) == ["model_change", "user", "assistant"]


# === 2. written at creation: session_start and setup messages land after it ===

_START_TURN = """
import asyncio


def setup(aelix):
    async def _on_start(event, ctx):
        aelix.send_message("startup task: summarise the repo", trigger_turn=True)
        if {wait!r}:
            await asyncio.sleep(0.05)

    def _boom(event, ctx=None):
        raise RuntimeError("probe: the context hook refuses every turn")

    aelix.on("session_start", _on_start)
    aelix.on("context", _boom)
"""


@pytest.mark.parametrize("wait", [False, True], ids=["no-await", "await"])
@pytest.mark.parametrize("launch_mode", ["rpc", "print", "json"])
async def test_a_session_start_turn_lands_after_the_record(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launch_mode: str,
    wait: bool,
) -> None:
    """Cross-review p02/p04, verify B1: a ``session_start`` handler triggers a turn that
    fails before the request. pi writes the record before ``session_start``, so the file
    is [model_change, user, assistant] whatever the handler awaits. On b9e09f01 the
    record came after ``session_start`` and an await let the turn's messages in first,
    which suppressed it for good: the reopen landed on the settings default."""

    (env / "start_turn.py").write_text(_START_TURN.format(wait=wait), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _drive(monkeypatch, _wait_idle)

    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            *_MODES[launch_mode],
            "-e",
            str(env / "start_turn.py"),
            *_LAUNCH,
        ]
    )

    assert code == 0, capsys.readouterr().err
    path = _only_file(env)
    assert _shape(path) == ["model_change", "user", "assistant"]
    assert _records(path) == [_HAIKU]
    assert await _reopened_model(env, monkeypatch) == _HAIKU


_TRIGGER_ON_START = """
def setup(aelix):
    async def _on_start(event, ctx):
        aelix.send_message("startup task: summarise the repo", trigger_turn=True)

    aelix.on("session_start", _on_start)
"""


@pytest.mark.parametrize("launch_mode", list(_MODES))
@pytest.mark.parametrize("handler", [False, True], ids=["plain", "start-turn"])
async def test_a_pending_launch_records_its_route_once_decided(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launch_mode: str,
    handler: bool,
) -> None:
    """Cross-review p03 (start-turn) and S1 (plain). A guard-2 launch is PENDING while
    ``session_start`` runs (#367): its model is a placeholder until the late-route
    decision, so its record is written after that decision, for a session created
    empty. A handler's ``trigger_turn`` there is refused by the turn gate and its
    messages written first (``_finish_refused_turns``); the record follows them and
    still names the route, so the reopen lands on it. On b9e09f01 the start-turn file
    held no record at all; under S1 (record taken before the decision) neither did
    the plain one. Every mode (review round 4, cross F5: with rpc and print only, a
    record written for those two modes alone passed every row)."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    if launch_mode == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    argv = ["--session-dir", _sessions(env), *_MODES[launch_mode], "--model", _GUARD2_ID]
    if handler:
        (env / "trigger.py").write_text(_TRIGGER_ON_START, encoding="utf-8")
        argv += ["-e", str(env / "trigger.py")]
    # Without the handler's refused turn the session would be empty, and an empty
    # session's reopen follows the launch inputs (#376): give it the #413 failure.
    _drive(monkeypatch, _wait_idle if handler else _fail_one_turn)

    code = await entry_mod._async_main(argv)

    assert code == 0, capsys.readouterr().err
    path = _only_file(env)
    assert _records(path) == [_GUARD2]
    if handler:
        # The refused turn (user + its failure) was written during session_start,
        # before the decision; the record follows it.
        assert _shape(path) == ["user", "assistant", "model_change"]
    else:
        assert _shape(path) == ["model_change", "user", "assistant"]
    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    assert await _reopened_model(env, monkeypatch) == _GUARD2


async def test_new_session_records_before_its_session_start_turn(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cross-review p04b: the same handler on RPC ``new_session`` (``/new``). The rebuild
    records when the factory builds it, before ``session_start``."""

    (env / "start_turn.py").write_text(_START_TURN.format(wait=True), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    made: dict[str, str] = {}

    async def _new(runtime: Any) -> None:
        await _wait_idle(runtime)
        await runtime.new_session()
        await _wait_idle(runtime)
        made["new"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _new)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", "-e", str(env / "start_turn.py"), *_LAUNCH]
    )

    assert code == 0, capsys.readouterr().err
    shape = _shape(made["new"])
    assert shape[0] == "model_change" and len(shape) == 3, shape
    assert _records(made["new"]) == [_HAIKU]
    assert await _reopened_model(env, monkeypatch, session=made["new"]) == _HAIKU


async def test_new_session_setup_messages_land_after_the_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify B1: ``ctx.new_session({setup})`` that appends a message. pi's ``newSession``
    runs ``createRuntime`` (which records) before ``setup``; on b9e09f01 setup's message
    suppressed the record and the reopen landed on the settings default."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    made: dict[str, str] = {}

    async def _seed(session_manager: Any) -> None:
        await session_manager.get_session().append_message(
            UserMessage(content=[TextContent(text="seeded by setup")])
        )

    async def _new(runtime: Any) -> None:
        await runtime.new_session(setup=_seed)
        made["new"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _new)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH]
    )

    assert code == 0, capsys.readouterr().err
    assert _shape(made["new"]) == ["model_change", "user"]
    assert _records(made["new"]) == [_HAIKU]
    # Review round 3, item 1: the launch file [model_change haiku] is EMPTY, and an
    # empty session reopens on the launch inputs (sonnet); ``--continue`` picked it
    # on an mtime tie. Name the /new file.
    assert await _reopened_model(env, monkeypatch, session=made["new"]) == _HAIKU


# === 3. a rebuild held on the placeholder records nothing (settle order) ======

_LATE_ON_NEW = """
from aelix_ai.streaming import Model
from aelix_coding_agent.model_registry import ProviderConfigInput


def setup(aelix):
    async def _on_start(event, ctx):
        if getattr(event, "reason", None) != "new":
            return
        aelix.register_provider(
            "sessext",
            ProviderConfigInput(
                name="late-on-new probe",
                api_key="ext-fake-literal",
                models={
                    "m1": Model(
                        id="m1",
                        provider="sessext",
                        api="openai-completions",
                        base_url="http://127.0.0.1:9/v1",
                    )
                },
            ),
        )

    aelix.on("session_start", _on_start)
"""


async def test_a_rebuild_held_on_the_placeholder_records_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify B2 / sabotage S10. The launch ``--model sessext/m1`` is a guard-2 route
    (OpenRouter as written) and records it. ``new_session``'s factory resolves the same
    guard-2 route, but that rebuild's own ``session_start`` registers ``sessext``: the
    inputs now land on a late provider and the rebuild is held on ``Model(m1,
    sessext)`` (#367). Its record waits for that decision, so the new file names
    nothing; recorded before the settle (S10) it named the OpenRouter route the held
    session never runs on."""

    (env / "late_on_new.py").write_text(_LATE_ON_NEW, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    made: dict[str, Any] = {}

    async def _new(runtime: Any) -> None:
        made["first"] = str(runtime.harness.session.session_file)
        await runtime.new_session()
        made["new"] = str(runtime.harness.session.session_file)
        made["model"] = runtime.harness.current_model

    _drive(monkeypatch, _new)
    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            "--mode",
            "rpc",
            "-e",
            str(env / "late_on_new.py"),
            "--model",
            "sessext/m1",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert _records(made["first"]) == [("openrouter", "sessext/m1")]
    assert made["model"].api == "unknown"
    assert _ident(made["model"]) == ("sessext", "m1")
    assert _entries(made["new"]) == []


# === 4. a reopen adds nothing, or the one record the latest does not name =====


async def test_a_reopen_with_a_conversation_records_nothing_whatever_it_runs_on(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """pi's ``hasExistingSession`` arm writes no ``model_change``: the session's
    answers already name their model, and ``--model`` for one run is not a pick
    the session should remember before a prompt runs on it."""

    from aelix_coding_agent import modes

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")

    async def _answer(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        session = runtime_host.harness.session
        await session.append_message(UserMessage(content=[TextContent(text="q")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="a")],
                stop_reason="end_turn",
                provider=_HAIKU[0],
                model=_HAIKU[1],
                api="anthropic-messages",
            )
        )

    monkeypatch.setattr(modes, "run_rpc_mode", _answer)
    assert (
        await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH])
        == 0
    )
    before = _entries(_only_file(env))
    _stub_modes(monkeypatch)

    for extra in ([], ["--provider", "anthropic", "--model", "claude-sonnet-4-5"]):
        code = await entry_mod._async_main(
            ["--continue", "--session-dir", _sessions(env), *_MODES[mode], *extra]
        )
        assert code == 0, capsys.readouterr().err

    assert _entries(_only_file(env)) == before


async def test_an_empty_session_records_only_what_its_latest_record_does_not_name(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty session reopened is still a session with no messages (pi's new-session
    arm). On the same model nothing is appended; on another, that one is recorded (no
    prompt ran on the first, and the launch inputs decide an empty session's model).
    Cross-review S3: back on haiku after sonnet the LATEST record is sonnet, so haiku is
    recorded again, and a failed haiku turn reopens on haiku — the skip asking whether
    ANY record names the model left the file on sonnet."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _stub_modes(monkeypatch)
    argv = ["--session-dir", _sessions(env), "--mode", "rpc"]
    sonnet = ["--provider", "anthropic", "--model", "claude-sonnet-4-5"]

    assert await entry_mod._async_main([*argv, *_LAUNCH]) == 0
    assert await entry_mod._async_main(["--continue", *argv, *_LAUNCH]) == 0
    assert _records(_only_file(env)) == [_HAIKU]

    code = await entry_mod._async_main(["--continue", *argv, *sonnet])
    assert code == 0, capsys.readouterr().err
    assert _records(_only_file(env)) == [_HAIKU, _SONNET]

    _drive(monkeypatch, _fail_one_turn)
    assert await entry_mod._async_main(["--continue", *argv, *_LAUNCH]) == 0
    assert _records(_only_file(env)) == [_HAIKU, _SONNET, _HAIKU]
    assert await _reopened_model(env, monkeypatch) == _HAIKU


# === 5. /new, /fork and every other rebuild ==================================


async def test_new_session_and_a_fork_onto_the_empty_branch_record_a_fork_with_messages_does_not(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pi's ``newSession`` and ``fork`` build through ``createAgentSession``
    (``agent-session-runtime.ts`` at ``pi@3daa20280``), so the empty session each
    makes records its model and a fork that keeps messages records none."""

    from aelix_coding_agent import modes

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    made: dict[str, str] = {}

    async def _drive_rpc(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        session = runtime_host.harness.session
        user_id = await session.append_message(UserMessage(content=[TextContent(text="q")]))
        answer_id = await session.append_message(
            AssistantMessage(
                content=[TextContent(text="a")],
                stop_reason="end_turn",
                provider=_HAIKU[0],
                model=_HAIKU[1],
                api="anthropic-messages",
            )
        )
        made["first"] = str(runtime_host.harness.session.session_file)
        await runtime_host.fork(answer_id, position="at")
        made["fork_kept"] = str(runtime_host.harness.session.session_file)
        await runtime_host.switch_session(made["first"])
        await runtime_host.fork(user_id, position="before")
        made["fork_empty"] = str(runtime_host.harness.session.session_file)
        await runtime_host.new_session()
        made["new"] = str(runtime_host.harness.session.session_file)

    monkeypatch.setattr(modes, "run_rpc_mode", _drive_rpc)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH]
    )

    assert code == 0, capsys.readouterr().err
    assert len(set(made.values())) == 4, made
    assert _records(made["first"]) == [_HAIKU]
    # The kept branch carries the source's record (copied), and gains none.
    assert _records(made["fork_kept"]) == [_HAIKU]
    assert [e["type"] for e in _entries(made["fork_empty"])] == ["model_change"]
    assert _records(made["fork_empty"]) == [_HAIKU]
    assert [e["type"] for e in _entries(made["new"])] == ["model_change"]
    assert _records(made["new"]) == [_HAIKU]


async def _empty_session_on(env: Path, model: tuple[str, str], root: str | None = None) -> str:
    """A session in the cwd with no messages whose one record names ``model``."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=root or _sessions(env))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    await session.append_model_change(*model)
    path = session.session_file
    assert path is not None
    return str(path)


@pytest.mark.parametrize("rebuild", ["switch_session", "import", "reload"])
async def test_a_rebuild_onto_an_empty_session_on_another_model_records(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rebuild: str
) -> None:
    """Cross-review S2: RPC ``switch_session`` (and the TUI's ``/resume``, the same
    runtime call), ``/import`` and ``/reload`` onto a session with no messages whose
    latest record names sonnet, while this run's inputs resolve haiku: the rebuild
    runs on haiku, so it records haiku. A seam that recorded only for ``new``/``fork``
    left these files naming sonnet."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    made: dict[str, str] = {}
    outside = str(env / "elsewhere")

    async def _rebuild(runtime: Any) -> None:
        if rebuild == "switch_session":
            target = await _empty_session_on(env, _SONNET)
            await runtime.switch_session(target)
        elif rebuild == "import":
            source = await _empty_session_on(env, _SONNET, root=outside)
            await runtime.import_from_jsonl(source)
        else:
            await runtime.harness.session.append_model_change(*_SONNET)
            await runtime.reload()
        made["path"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _rebuild)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH]
    )

    assert code == 0, capsys.readouterr().err
    expected = [_HAIKU, _SONNET, _HAIKU] if rebuild == "reload" else [_SONNET, _HAIKU]
    assert _records(made["path"]) == expected


# === 6. a storage failure is a warning, not a crash =========================


def _inject_storage_error(monkeypatch: pytest.MonkeyPatch, code: str = "storage") -> list[int]:
    calls: list[int] = []

    async def _fail(self: Session, provider: str, model_id: str) -> str:
        calls.append(1)
        raise SessionError(code, f"Failed to append session entry: injected {code} failure")  # type: ignore[arg-type]

    monkeypatch.setattr(Session, "append_model_change", _fail)
    return calls


async def test_a_storage_error_on_the_launch_record_warns_and_the_launch_runs(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """Cross-review p09, verify B3: an append that fails (EACCES, ENOSPC, a read-only
    mount) went unseen on 7d163232 (a prompt ran, wrote nothing and said nothing;
    only ``--thinking`` failed, at launch); b9e09f01's record re-raised it at launch
    and RPC, ``-p`` and the TUI exited 1 with a traceback. Injected, so every
    platform runs it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    calls = _inject_storage_error(monkeypatch)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), *_MODES[mode], *_LAUNCH]
    )

    err = capsys.readouterr().err
    assert code == 0, err
    assert calls == [1]
    assert _ident(seen["model"]) == _HAIKU
    assert "could not record the model this session starts on" in err
    assert "Traceback" not in err
    assert _entries(_only_file(env)) == []


async def test_switch_session_with_a_storage_error_on_the_record_still_switches(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify B3: b9e09f01 raised out of the replacement after the swap, so RPC
    ``switch_session`` answered success=false while the target was already live."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    target = await _empty_session_on(env, _SONNET)
    made: dict[str, Any] = {}

    async def _switch(runtime: Any) -> None:
        _inject_storage_error(monkeypatch)
        made["result"] = await runtime.switch_session(target)
        made["path"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _switch)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH]
    )

    err = capsys.readouterr().err
    assert code == 0, err
    assert made["path"] == target
    assert not getattr(made["result"], "cancelled", False)
    assert "could not record the model this session starts on" in err
    assert _records(target) == [_SONNET]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file modes (0444 does not stop the owner on every win32 filesystem); "
    "the injected-error rows above cover the same handling on every platform",
)
@pytest.mark.skipif(
    sys.platform != "win32" and os.geteuid() == 0, reason="root writes through 0444"
)
@pytest.mark.parametrize("launch_mode", ["rpc", "print"])
async def test_an_unwritable_empty_session_file_still_opens(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], launch_mode: str
) -> None:
    """The real case behind p09: a header-plus-sonnet file mode 0444, opened on haiku."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _empty_session_on(env, _SONNET)
    before = Path(path).read_bytes()
    os.chmod(path, 0o444)
    seen = _stub_modes(monkeypatch)
    try:
        code = await entry_mod._async_main(
            ["--session", path, *_MODES[launch_mode], *_LAUNCH]
        )
    finally:
        os.chmod(path, 0o644)

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _HAIKU
    assert "could not record the model this session starts on" in err
    assert Path(path).read_bytes() == before


# === 7. what a session_start handler moves, and a child's origin =============


_SET_MODEL_ON_START = """
from aelix_ai.models import get_model


def setup(aelix):
    async def _on_start(event, ctx):
        await aelix.set_model(get_model("anthropic", "claude-sonnet-4-5"))

    aelix.on("session_start", _on_start)
"""


async def test_a_session_start_set_model_records_after_the_start_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pi's order: the start record (haiku) at creation, then the handler's
    ``setModel`` (sonnet), which records itself. The latest names the model the
    session runs on. (A PENDING launch records once: the next section's
    ``test_a_pending_launch_whose_session_start_set_model_records_once``.)"""

    (env / "onstart.py").write_text(_SET_MODEL_ON_START, encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            "--mode",
            "rpc",
            "-e",
            str(env / "onstart.py"),
            *_LAUNCH,
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert _ident(seen["model"]) == _SONNET
    assert _records(_only_file(env)) == [_HAIKU, _SONNET]


@pytest.mark.parametrize("held_mode", ["rpc", "print"])
async def test_the_late_provider_hold_records_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], held_mode: str
) -> None:
    """#367: a launch model naming a provider registered in ``session_start`` is held
    on a placeholder (``api 'unknown'``) in RPC and refused in print (exit 1); the
    session never runs on a resolved model, so nothing is written."""

    from tests.cli.test_launch_route_344 import _SESSION_START_EXTENSION as ext_src

    (env / "sessext.py").write_text(ext_src, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            *_MODES[held_mode],
            "-e",
            str(env / "sessext.py"),
            "--model",
            "sessext/m1",
        ]
    )

    err = capsys.readouterr().err
    if held_mode == "rpc":
        assert code == 0, err
        assert seen["model"].api == "unknown"
    else:
        assert code == 1, err
        assert "No prompt was sent." in err
    assert _entries(_only_file(env)) == []


async def test_a_child_session_keeps_its_origin_first(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parent publishes the child's file with ``aelix.child_origin`` as its one
    entry, and the child opens it with ``--session``; the record lands after it."""

    from aelix_agents.child_session import CHILD_ORIGIN_TYPE

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    child_dir = Path(_sessions(env)) / "parent"
    child_dir.mkdir(parents=True)
    path = child_dir / "sub-0123456789ab.jsonl"
    await JsonlSessionStorage.create(
        LocalFileSystem(),
        str(path),
        cwd=str(Path.cwd()),
        session_id="11111111-2222-3333-4444-555555555555",
        entries=[
            CustomEntry(
                id="origin01",
                parent_id=None,
                timestamp="2026-10-14T00:00:00.000Z",
                custom_type=CHILD_ORIGIN_TYPE,
                data={"v": 1, "key": "sub-0123456789ab"},
            )
        ],
    )
    _stub_modes(monkeypatch)

    code = await entry_mod._async_main(["--session", str(path), "--mode", "rpc", *_LAUNCH])

    assert code == 0, capsys.readouterr().err
    entries = _entries(path)
    assert [e["type"] for e in entries] == ["custom", "model_change"]
    assert entries[0]["customType"] == CHILD_ORIGIN_TYPE
    assert entries[1]["parentId"] == "origin01"
    assert _records(path) == [_HAIKU]
    # #199's reader still sees a child file.
    reopened = await JsonlSessionRepo(fs=LocalFileSystem()).open(
        await _metadata_of(path)
    )
    first = (await reopened.get_entries())[0]
    assert first.type == "custom" and first.custom_type == CHILD_ORIGIN_TYPE


async def _metadata_of(path: Path) -> Any:
    from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata

    return await load_jsonl_session_metadata(LocalFileSystem(), str(path))


# === 8. the helper's own guards ==============================================


def _harness(session: Session, model: Any, *, held: str | None = None) -> Any:
    return SimpleNamespace(session=session, current_model=model, turns_held=held)


def _haiku() -> Any:
    from aelix_ai.models import get_model

    model = get_model(*_HAIKU)
    assert model is not None
    return model


async def _written(session: Session) -> list[tuple[str, str]]:
    return [
        (e.provider, e.model_id)  # type: ignore[union-attr]
        for e in await session.get_entries()
        if e.type == "model_change"
    ]


async def test_the_helper_writes_once_for_a_resolved_model_on_an_empty_session(
    env: Path,
) -> None:
    """No credential is asked (item 5): the model is resolved, so it is recorded."""

    session = Session(MemorySessionStorage())
    harness = _harness(session, _haiku())

    assert await entry_mod._record_session_start_model(harness) is True
    assert await entry_mod._record_session_start_model(harness) is False
    assert await _written(session) == [_HAIKU]


async def test_the_helper_trusts_the_creation_flag_over_messages_written_since(
    env: Path,
) -> None:
    """The deferred record (a pending launch, a held rebuild) is decided by whether the
    session was created empty, not by the messages ``session_start`` wrote since."""

    session = Session(MemorySessionStorage())
    await session.append_message(UserMessage(content=[TextContent(text="refused turn")]))

    assert (
        await entry_mod._record_session_start_model(
            _harness(session, _haiku()), created_empty=False
        )
        is False
    )
    assert (
        await entry_mod._record_session_start_model(
            _harness(session, _haiku()), created_empty=True
        )
        is True
    )
    assert await _written(session) == [_HAIKU]


async def test_the_helper_reads_the_branch_not_every_entry(env: Path) -> None:
    """Verify S13: with the leaf moved back to the record, the branch holds no message
    and its latest record is sonnet, so a haiku start is recorded; the whole entry list
    (a message on the abandoned branch) would have said 'a conversation'."""

    session = Session(MemorySessionStorage())
    record = await session.append_model_change(*_SONNET)
    await session.append_message(UserMessage(content=[TextContent(text="abandoned")]))
    await session.move_to(record)

    assert await entry_mod._record_session_start_model(_harness(session, _haiku())) is True
    branch = [e.type for e in await session.get_branch()]
    assert branch == ["model_change", "model_change"]


@pytest.mark.parametrize(
    "case",
    [
        "placeholder",
        "placeholder-no-adapters",
        "no-provider",
        "no-provider-no-adapters",
        "held",
        "conversation",
        "read-only",
    ],
)
async def test_the_helper_writes_nothing_when(
    env: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """``-no-adapters``: an embedder that registered no adapter; only the placeholder
    rule keeps the hold's ``Model(id, provider)`` and a nameless model out, as
    ``AgentHarness`` does."""

    from aelix_ai.streaming import Model
    from aelix_coding_agent.core import runnable_models

    if case.endswith("-no-adapters"):
        monkeypatch.setattr(runnable_models, "supported_apis", lambda: set())

    storage = MemorySessionStorage()
    session = Session(storage)
    model: Any = _haiku()
    held: str | None = None
    target = session
    if case.startswith("placeholder"):
        model = Model(id=_HAIKU[1], provider=_HAIKU[0])
    elif case.startswith("no-provider"):
        model = Model(id="", provider="", api="anthropic-messages")
    elif case == "held":
        held = "a session_start is deciding the launch route"
    elif case == "conversation":
        await session.append_message(UserMessage(content=[TextContent(text="q")]))
    elif case == "read-only":
        target = Session(ReadOnlySessionStorage(storage))

    wrote = await entry_mod._record_session_start_model(_harness(target, model, held=held))

    assert wrote is False
    assert await _written(session) == []


async def test_a_read_only_refusal_from_storage_is_silent(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A storage that refuses with ``read_only`` without being the wrapper class
    (another viewer implementation) records nothing, says nothing and does not raise."""

    session = Session(MemorySessionStorage())

    async def _refuse(provider: str, model_id: str) -> str:
        raise SessionError("read_only", "owned by another process")

    session.append_model_change = _refuse  # type: ignore[method-assign]

    assert await entry_mod._record_session_start_model(_harness(session, _haiku())) is False
    assert capsys.readouterr().err == ""


async def test_a_storage_failure_is_a_warning(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = Session(MemorySessionStorage())

    async def _fail(provider: str, model_id: str) -> str:
        raise SessionError("storage", "Failed to append session entry: [Errno 28] No space")

    session.append_model_change = _fail  # type: ignore[method-assign]

    assert await entry_mod._record_session_start_model(_harness(session, _haiku())) is False
    err = capsys.readouterr().err
    assert err.startswith("Warning: could not record the model this session starts on")
    assert "anthropic/claude-haiku-4-5" in err and "No space" in err


@pytest.mark.parametrize("code", ["invalid_session", "invalid_entry", "not_found", "unknown"])
async def test_a_session_error_that_is_not_a_storage_failure_propagates(
    env: Path, code: str
) -> None:
    """Item 3 / sabotage S12. Only a failed write (``storage``) or a refused one
    (``read_only``) is the file's condition; the other codes say the session state
    itself is broken (a leaf that is not in the tree, an entry that cannot be
    serialized), which every other append surfaces too and this one must not hide."""

    session = Session(MemorySessionStorage())

    async def _broken(provider: str, model_id: str) -> str:
        raise SessionError(code, "broken session state")  # type: ignore[arg-type]

    session.append_model_change = _broken  # type: ignore[method-assign]

    with pytest.raises(SessionError) as raised:
        await entry_mod._record_session_start_model(_harness(session, _haiku()))
    assert raised.value.code == code


# === 9. review round 3: the deferred record meets what was written since ========


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake keys for both routes and every proxy pointed at a closed port."""

    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")


_OPUS = ("anthropic", "claude-opus-4-5")


@pytest.mark.parametrize("launch", ["guard2", "resolved"])
async def test_a_deferred_record_does_not_outrank_an_answer_setup_seeded(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], launch: str
) -> None:
    """Review round 3, cross F1. ``new_session``'s ``setup`` seeds a conversation whose
    answer names opus (a handoff-style extension). pi records at creation, before
    setup, so its file is [model_change, user, assistant opus] and it reopens on
    opus; so does aelix after a resolved launch. After a guard-2 launch the /new
    rebuild is pending and its record is deferred past setup: on 32910ebb it was
    written after the seeded answer, [user, assistant opus, model_change guard-2],
    and the reopen landed on the OpenRouter route. A message written since that
    names its model now keeps the deferred record out."""

    _block_network(monkeypatch)
    made: dict[str, str] = {}

    async def _seed(session_manager: Any) -> None:
        session = session_manager.get_session()
        await session.append_message(UserMessage(content=[TextContent(text="handoff: go on")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
                provider=_OPUS[0],
                model=_OPUS[1],
                api="anthropic-messages",
            )
        )

    async def _new(runtime: Any) -> None:
        await runtime.new_session(setup=_seed)
        made["new"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _new)
    flags = ["--model", _GUARD2_ID] if launch == "guard2" else _LAUNCH
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *flags])

    assert code == 0, capsys.readouterr().err
    if launch == "guard2":
        assert _shape(made["new"]) == ["user", "assistant"]
    else:
        assert _shape(made["new"]) == ["model_change", "user", "assistant"]
    assert await _reopened_model(env, monkeypatch, session=made["new"]) == _OPUS


_TURN_ON_NEW = """
import asyncio


def setup(aelix):
    ended = asyncio.Event()

    async def _on_start(event, ctx):
        if getattr(event, "reason", None) != "new":
            return
        ended.clear()
        aelix.send_message("startup task on /new", trigger_turn=True)
        # The handler returns only once the turn it started has ended, so the
        # deferred record (written after session_start) follows that turn
        # whatever the scheduler does (review round 4, verify N2: a fixed
        # sleep held the row only if the turn ended inside it).
        await asyncio.wait_for(ended.wait(), timeout=30)

    def _end(event, ctx=None):
        ended.set()

    def _boom(event, ctx=None):
        raise RuntimeError("probe: the context hook refuses every turn")

    aelix.on("session_start", _on_start)
    aelix.on("agent_end", _end)
    aelix.on("context", _boom)
"""


async def test_a_pending_new_session_whose_start_turn_failed_still_records(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review round 3, verify's rebuild-flag mutant (b). A guard-2 launch, then
    ``new_session``: the rebuild is pending, so its record waits for the decision
    after ``session_start``, and that ``session_start`` triggers a turn that fails
    before the request (no provenance). The record is judged by the flag read when
    the session was created (empty), not by the branch at record time (which then
    holds that turn): it is written after the turn and the reopen lands on it.
    Read at record time, nothing was written and the reopen landed on sonnet."""

    _block_network(monkeypatch)
    (env / "turn_on_new.py").write_text(_TURN_ON_NEW, encoding="utf-8")
    made: dict[str, str] = {}

    async def _new(runtime: Any) -> None:
        await runtime.new_session()
        await _wait_idle(runtime)
        made["new"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _new)
    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            "--mode",
            "rpc",
            "-e",
            str(env / "turn_on_new.py"),
            "--model",
            _GUARD2_ID,
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert _shape(made["new"]) == ["user", "assistant", "model_change"]
    assert _records(made["new"]) == [_GUARD2]
    assert await _reopened_model(env, monkeypatch, session=made["new"]) == _GUARD2


async def _failed_conversation(env: Path) -> str:
    """A session whose one turn failed before the request: a conversation, no model."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=_sessions(env))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    await session.append_message(UserMessage(content=[TextContent(text="q")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="[error] context hook handler raised")],
            stop_reason="error",
            error_message="context hook handler raised",
        )
    )
    path = session.session_file
    assert path is not None
    return str(path)


@pytest.mark.parametrize("reach", ["switch_session", "continue"])
async def test_a_pending_route_onto_a_conversation_records_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], reach: str
) -> None:
    """Review round 3, cross F4 mutants D and E (and verify's mutant (a)). A guard-2
    route is pending, so its record is deferred and judged by the flag read when the
    session was created. Reached by RPC ``switch_session`` from a guard-2 launch (a
    deferred rebuild) or by ``--continue --model <guard-2 id>`` (a pending launch),
    a session that already had a conversation gains no record (pi's
    ``hasExistingSession``). Its turn failed before the request, so no message names
    a model and only that flag keeps the record out."""

    _block_network(monkeypatch)
    target = await _failed_conversation(env)
    before = Path(target).read_bytes()
    made: dict[str, str] = {}
    seen: dict[str, Any] = {}

    if reach == "switch_session":

        async def _switch(runtime: Any) -> None:
            await runtime.switch_session(target)
            made["path"] = str(runtime.harness.session.session_file)

        _drive(monkeypatch, _switch)
        argv = ["--session-dir", _sessions(env), "--mode", "rpc", "--model", _GUARD2_ID]
    else:
        seen = _stub_modes(monkeypatch)
        argv = ["--continue", "--session-dir", _sessions(env), "--mode", "rpc", "--model", _GUARD2_ID]

    code = await entry_mod._async_main(argv)

    assert code == 0, capsys.readouterr().err
    if reach == "switch_session":
        assert made["path"] == target
    else:
        assert str(seen["runtime"].harness.session.session_file) == target
    assert Path(target).read_bytes() == before


async def test_a_pending_launch_whose_session_start_set_model_records_once(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review round 3, cross F3: on a PENDING (guard-2) launch the handler's
    ``set_model`` runs under the turn gate, so ``AgentHarness`` does not record it;
    the deferred start record then names the handler's model, and the file holds
    that one record (on a resolved launch it is [launch model, handler's model])."""

    (env / "onstart.py").write_text(_SET_MODEL_ON_START, encoding="utf-8")
    _block_network(monkeypatch)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        [
            "--session-dir",
            _sessions(env),
            "--mode",
            "rpc",
            "-e",
            str(env / "onstart.py"),
            "--model",
            _GUARD2_ID,
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert _ident(seen["model"]) == _SONNET
    assert _records(_only_file(env)) == [_SONNET]


async def test_the_helper_does_not_outrank_an_answer_written_since(env: Path) -> None:
    """Review round 3, F1, at the helper: a deferred record (``created_empty=True``)
    is not written when a message on the branch names its model, and is written when
    the only answer there names none (the #413 failure)."""

    named = Session(MemorySessionStorage())
    await named.append_message(UserMessage(content=[TextContent(text="seeded")]))
    await named.append_message(
        AssistantMessage(
            content=[TextContent(text="ok")],
            stop_reason="end_turn",
            provider=_OPUS[0],
            model=_OPUS[1],
            api="anthropic-messages",
        )
    )
    unnamed = Session(MemorySessionStorage())
    await unnamed.append_message(UserMessage(content=[TextContent(text="refused")]))
    await unnamed.append_message(
        AssistantMessage(
            content=[TextContent(text="[error] refused")],
            stop_reason="error",
            error_message="refused",
        )
    )

    assert (
        await entry_mod._record_session_start_model(
            _harness(named, _haiku()), created_empty=True
        )
        is False
    )
    assert await _written(named) == []
    assert (
        await entry_mod._record_session_start_model(
            _harness(unnamed, _haiku()), created_empty=True
        )
        is True
    )
    assert await _written(unnamed) == [_HAIKU]


async def test_the_helper_dedups_on_the_branch_not_the_file(env: Path) -> None:
    """Review round 3, cross F4 mutant A: the leaf moved back onto a sonnet record, so
    the branch is empty and its LATEST record names sonnet; the abandoned branch,
    written last, ends in an answer naming haiku. A haiku start is recorded: the
    dedup asks the branch the session continues from, not the whole file."""

    session = Session(MemorySessionStorage())
    record = await session.append_model_change(*_SONNET)
    await session.append_message(UserMessage(content=[TextContent(text="abandoned")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="a")],
            stop_reason="end_turn",
            provider=_HAIKU[0],
            model=_HAIKU[1],
            api="anthropic-messages",
        )
    )
    await session.move_to(record)

    assert await entry_mod._record_session_start_model(_harness(session, _haiku())) is True
    assert [e.type for e in await session.get_branch()] == ["model_change", "model_change"]
    assert await _written(session) == [_SONNET, _HAIKU]


# === 10. review round 4 ========================================================


@pytest.mark.parametrize("launch", ["guard2", "resolved"])
async def test_a_deferred_record_does_not_outrank_an_answer_before_a_failed_turn(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], launch: str
) -> None:
    """Review round 4, cross F3. ``setup`` seeds a handoff whose answer names opus,
    then one more turn that failed before the request (no provenance), so the LAST
    message names no model. The check reads every message written since, not the
    last one: after a guard-2 launch nothing is recorded and the reopen lands on
    opus, as after a resolved launch (where the record precedes the seed). Under the
    mutant that reads only the last message the deferred record followed the failed
    turn and the reopen went to the OpenRouter route."""

    _block_network(monkeypatch)
    made: dict[str, str] = {}

    async def _seed(session_manager: Any) -> None:
        session = session_manager.get_session()
        await session.append_message(UserMessage(content=[TextContent(text="handoff: go on")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
                provider=_OPUS[0],
                model=_OPUS[1],
                api="anthropic-messages",
            )
        )
        await session.append_message(UserMessage(content=[TextContent(text="next")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="[error] context hook handler raised")],
                stop_reason="error",
                error_message="context hook handler raised",
            )
        )

    async def _new(runtime: Any) -> None:
        await runtime.new_session(setup=_seed)
        made["new"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _new)
    flags = ["--model", _GUARD2_ID] if launch == "guard2" else _LAUNCH
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *flags])

    assert code == 0, capsys.readouterr().err
    if launch == "guard2":
        assert _shape(made["new"]) == ["user", "assistant", "user", "assistant"]
    else:
        assert _shape(made["new"]) == ["model_change", "user", "assistant", "user", "assistant"]
    assert await _reopened_model(env, monkeypatch, session=made["new"]) == _OPUS


@pytest.mark.parametrize("rebuild", ["switch_session", "import", "reload"])
async def test_a_pending_rebuild_onto_an_empty_session_records_its_route(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rebuild: str
) -> None:
    """Review round 4, cross F4. After a guard-2 launch every rebuild is pending, not
    only ``new_session``: a ``switch_session``, an ``import`` or a ``/reload`` onto a
    session with no messages whose latest record names sonnet runs on the guard-2
    route and records it last, after its late-route decision. Under the mutant that
    writes the deferred record only for reason ``new`` the file's latest record
    still named sonnet while the session ran on the guard-2 route."""

    _block_network(monkeypatch)
    made: dict[str, Any] = {}
    outside = str(env / "elsewhere")

    async def _rebuild(runtime: Any) -> None:
        if rebuild == "switch_session":
            await runtime.switch_session(await _empty_session_on(env, _SONNET))
        elif rebuild == "import":
            await runtime.import_from_jsonl(await _empty_session_on(env, _SONNET, root=outside))
        else:
            await runtime.harness.session.append_model_change(*_SONNET)
            await runtime.reload()
        made["path"] = str(runtime.harness.session.session_file)
        made["model"] = _ident(runtime.harness.current_model)

    _drive(monkeypatch, _rebuild)
    code = await entry_mod._async_main(
        ["--session-dir", _sessions(env), "--mode", "rpc", "--model", _GUARD2_ID]
    )

    assert code == 0, capsys.readouterr().err
    assert made["model"] == _GUARD2
    assert _records(made["path"])[-1] == _GUARD2


async def _clone_rpc(runtime: Any) -> Any:
    from aelix_coding_agent.rpc.rpc_mode import _handle_clone
    from aelix_coding_agent.rpc.rpc_types import RpcCommandClone

    return await _handle_clone(runtime, RpcCommandClone(id="c"))


async def _clone_tui(runtime: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The real TUI shell on the real runtime: type ``/clone``, then ``/quit``.

    ``tests/tui``'s smoke helpers import ``_polling`` from their own directory,
    which is on ``sys.path`` only while pytest runs that directory."""

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tui"))
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.shell import run_tui
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.tui.test_run_tui_smoke import (
        _input_loop_is_idle,
        _quit_within,
        _spy_commits,
        _wait,
    )

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(runtime, cwd=".", chrome=chrome, install_signal_handlers=False)
        )
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: _input_loop_is_idle(chrome), what="the input loop to park")
        typed = len(commits)
        pipe.send_text("/clone\n")
        await _wait(
            lambda: any("clone" in c.lower() for c in commits[typed:]), what="the /clone answer"
        )
        await _wait(lambda: _input_loop_is_idle(chrome), what="the input loop to park again")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    return commits


@pytest.mark.parametrize("surface", ["rpc", "tui"])
@pytest.mark.parametrize("persist", ["persisted", "no-session"])
@pytest.mark.parametrize("before", ["fresh", "named"])
async def test_clone_of_a_session_with_no_conversation_is_refused_as_before(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: str,
    persist: str,
    before: str,
) -> None:
    """Review round 4, cross F1 and F2. The start record gives a new session a leaf,
    so on e7038c92 ``/clone`` and RPC ``clone`` of a fresh session cloned it on disk
    and, under ``--no-session``, reached an older in-memory fork bug ("'SessionMetadata'
    object has no attribute 'cwd'"). On 7d163232 both refused a fresh session (it had
    no leaf) with the words asserted here; pi refuses a persisted one too ("This
    session has not been saved yet": its file waits for a user or assistant message).
    The guard asks for a conversation, not a leaf, so a session whose only entry
    besides the start record is a name is refused as well; on 7d163232 that one was
    cloned on disk and hit the in-memory bug under ``--no-session``
    (``.omc/probes/413-live/r4/``). No second file is written."""

    _block_network(monkeypatch)
    seen: dict[str, Any] = {}

    async def _clone(runtime: Any) -> None:
        if before == "named":
            await runtime.harness.session.append_session_name("named before any prompt")
        seen["leaf"] = await runtime.harness.session.get_leaf_id()
        seen["session"] = runtime.harness.session
        if surface == "rpc":
            seen["answer"] = await _clone_rpc(runtime)
        else:
            seen["answer"] = await _clone_tui(runtime, monkeypatch)
        seen["after"] = runtime.harness.session

    _drive(monkeypatch, _clone)
    argv = ["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH]
    if persist == "no-session":
        argv.append("--no-session")
    code = await entry_mod._async_main(argv)

    assert code == 0, capsys.readouterr().err
    # The start record (and the name) are there: a leaf, but no conversation.
    assert seen["leaf"] is not None
    assert seen["after"] is seen["session"]
    if surface == "rpc":
        assert seen["answer"].success is False
        assert seen["answer"].error == "Cannot clone session: no current entry selected"
    else:
        assert "Nothing to clone (session is empty)." in seen["answer"]
        assert not any("Cloned session" in c or "clone failed" in c for c in seen["answer"])
    if persist == "persisted":
        assert len(_files(env)) == 1
    else:
        assert _files(env) == []


@pytest.mark.parametrize("surface", ["rpc", "tui"])
@pytest.mark.parametrize("leaf", ["tip", "rewound"])
async def test_clone_of_a_session_with_a_conversation_still_clones(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: str,
    leaf: str,
) -> None:
    """The control for the refusal above: one turn (that failed before the request)
    is a conversation, and ``/clone`` / RPC ``clone`` copy it into a second file.
    ``rewound``: the leaf moved back onto the start record, so the branch holds no
    message but the file does. The conversation is asked of the file, as pi's
    ``_hasConversation`` reads ``fileEntries``: pi clones a branch with a leaf once
    its file exists, 7d163232's guard asked only for a leaf, and this clones it."""

    _block_network(monkeypatch)
    seen: dict[str, Any] = {}

    async def _clone(runtime: Any) -> None:
        await _fail_one_turn(runtime)
        if leaf == "rewound":
            session = runtime.harness.session
            start = next(e for e in await session.get_entries() if e.type == "model_change")
            await session.move_to(start.id)
        seen["before"] = str(runtime.harness.session.session_file)
        if surface == "rpc":
            seen["answer"] = await _clone_rpc(runtime)
        else:
            seen["answer"] = await _clone_tui(runtime, monkeypatch)
        seen["after"] = str(runtime.harness.session.session_file)

    _drive(monkeypatch, _clone)
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc", *_LAUNCH])

    assert code == 0, capsys.readouterr().err
    if surface == "rpc":
        assert seen["answer"].success is True
    else:
        assert any(c.startswith("⎇ Cloned session") for c in seen["answer"])
    assert seen["after"] != seen["before"]
    assert len(_files(env)) == 2
    if leaf == "tip":
        assert _shape(seen["after"]) == ["model_change", "user", "assistant"]
    else:
        assert _shape(seen["after"]) == ["model_change"]
