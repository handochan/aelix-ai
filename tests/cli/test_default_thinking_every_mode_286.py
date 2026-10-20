"""#286 — ``defaultThinkingLevel`` applies in every launch mode; what a launch records
is unchanged but where a ``session_start`` handler sets the level or the model.

MEASURED ON ``529b997e`` (``.omc/probes/286-live/impl/base-1.out``, a local
OpenRouter-format recorder, fake key): with settings ``defaultThinkingLevel=high``,
``-p``, ``--mode json`` and ``--mode rpc`` sent ``"reasoning": {"effort": "none"}``
(RPC ``get_state`` said ``off``), while the TUI sent ``high``. The settings rung
lived inside ``run_tui`` only (``tui/shell.py``); ``_seed_startup_state`` restored
only a level the SESSION recorded (#198). A delegated child is a ``--mode json -p``
launch, so a profile naming its own model and no ``thinking:`` ran at ``off`` too
(``base-2.out``, ``D[ownmodel]``).

pi resolves the level in ``createAgentSession`` for every mode (``core/sdk.ts:253-278``
at ``pi@3daa20280``): explicit, then the session's, then the settings default,
clamped to the model. These drive the real ``_async_main`` (mode entries stubbed; no
turn reaches a provider) with the #376/#413 sandbox and read the level the harness
starts at and the session file:

* every mode starts at the default; ``--thinking`` and the session's recorded level
  (an explicit ``off`` included, with or without a conversation) still outrank it;
* every rung is CLAMPED to the model — the TUI's seed SKIPPED a level its model did
  not list, leaving ``xhigh`` on a ``high``-max model at ``off``;
* what a launch RECORDS is what it recorded on ``529b997e`` (owner decision
  2026-10-15, review round 3): ``-p``, ``--mode json`` and ``--mode rpc`` add no
  ``thinking_level_change`` for the default; the TUI records the default only when
  ``run_tui``'s old seed did (a default the model supports as written, not ``off``,
  no flag or restored level); ``--thinking`` keeps #198's carry. Rounds 1-2 recorded
  a start level at creation, which froze levels nobody chose. The exceptions are a
  ``session_start`` handler's ``set_thinking_level`` and ``set_model``, which now
  meet the start level instead of ``off`` (ADR-0239 decision 4; measured live in
  ``.omc/probes/286-live/r4/``, not pinned here);
* a pending launch (#367) applies the level unjudged while ``session_start`` runs on
  the placeholder and clamps it after the late-route decision; a held one never
  clamps against the hold's placeholder;
* a delegated child runs at its global default; ``/new`` carries the seeded level.

Hermetic: fake keys, an isolated agent dir and home, no network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_agent_core.session.entries import CustomEntry
from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.read_only import ReadOnlySessionStorage
from aelix_agent_core.session.session import Session
from aelix_agent_core.session.storage import SessionError
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.models import get_model
from aelix_ai.streaming import Model
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.env_sandbox import sandbox_home

_LAUNCH = ["--provider", "anthropic", "--model", "claude-haiku-4-5"]
_MODES = {
    "rpc": ["--mode", "rpc"],
    "print": ["-p", "hi"],
    "json": ["--mode", "json", "hi"],
    "tui": [],
}
_HEADLESS = ("rpc", "print", "json")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_session_start_model_413.py``'s sandbox: no key, an empty agent dir."""

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


@pytest.fixture(params=list(_MODES))
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    return request.param


def _settings(env: Path, **values: Any) -> None:
    (env / "agent" / "settings.json").write_text(json.dumps(values), encoding="utf-8")


def _sessions(env: Path) -> str:
    return str(env / "sessions")


def _files(env: Path) -> list[Path]:
    return sorted(Path(_sessions(env)).rglob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns)


def _only_file(env: Path) -> Path:
    files = _files(env)
    assert len(files) == 1, files
    return files[0]


def _entries(path: Path | str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert lines and lines[0]["type"] == "session"
    return lines[1:]


def _shape(path: Path | str) -> list[str]:
    """Entry types; a message as its role, a level change as ``thinking:<level>``."""

    out: list[str] = []
    for e in _entries(path):
        if e["type"] == "message":
            out.append(e["message"]["role"])
        elif e["type"] == "thinking_level_change":
            out.append(f"thinking:{e['thinkingLevel']}")
        else:
            out.append(e["type"])
    return out


def _stub_modes(monkeypatch: pytest.MonkeyPatch, drive: Any = None) -> dict[str, Any]:
    """Every mode entry stubbed: record the level and model it was handed, then
    run ``drive(runtime)`` if given."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent import modes

    seen: dict[str, Any] = {}

    async def _record(runtime: Any) -> None:
        seen["thinking"] = runtime.harness.state.thinking_level
        seen["model"] = runtime.harness.current_model
        seen["runtime"] = runtime
        if drive is not None:
            await drive(runtime)

    async def _print(runtime: Any, **kwargs: Any) -> int:
        await _record(runtime)
        return 0

    async def _rpc(
        harness: Any, *, model_registry: Any, runtime_host: Any, harness_factory: Any
    ) -> None:
        await _record(runtime_host)

    monkeypatch.setattr(modes, "run_print_mode", _print)
    monkeypatch.setattr(modes, "run_rpc_mode", _rpc)
    monkeypatch.setattr(tui_pkg, "run_tui", _print)
    return seen


async def _one_turn(runtime: Any) -> None:
    """A conversation, so ``--continue`` has history to come back to."""

    session = runtime.harness.session
    await session.append_message(UserMessage(content=[TextContent(text="q")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="a")],
            stop_reason="end_turn",
            provider="anthropic",
            model="claude-haiku-4-5",
            api="anthropic-messages",
        )
    )


async def _launch(env: Path, *argv: str) -> int:
    return await entry_mod._async_main(["--session-dir", _sessions(env), *argv])


# === 1. every mode starts at the settings default ===========================


async def test_every_mode_starts_at_the_settings_default(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """THE DEFECT: on 529b997e ``-p``, ``--mode json`` and ``--mode rpc`` started at
    ``off`` with ``defaultThinkingLevel=high``. (The TUI row was right on 529b997e
    only because ``run_tui`` itself seeded; with ``run_tui`` stubbed it is ``off``
    there too — the seed now runs before the mode dispatch, so it is ``high``.)"""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], *_LAUNCH) == 0, capsys.readouterr().err

    assert seen["thinking"] == "high"


async def test_no_default_starts_at_off_and_records_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The bottom rung is ``off`` (pi's is ``medium``, ``core/defaults.ts:3``) and is
    not recorded, as on ``529b997e``: rounds 1-2 recorded a start level at creation,
    and a default set later then did not reach ``--continue``."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], *_LAUNCH) == 0

    assert seen["thinking"] == "off"
    assert _shape(_only_file(env)) == ["model_change"]


@pytest.mark.parametrize("value", ["banana", 5, ""])
async def test_a_default_that_is_not_a_level_seeds_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str, value: object
) -> None:
    """The settings file is not validated on load; the TUI's seed suppressed whatever
    a bad value did. Nothing is seeded or recorded and the launch runs."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel=value)
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], *_LAUNCH) == 0

    assert seen["thinking"] == "off"
    assert _shape(_only_file(env)) == ["model_change"]


# === 2. clamped to the model, not skipped — and then not recorded ===========


@pytest.mark.parametrize(
    ("default", "launch", "key", "expected"),
    [
        # claude-haiku-4-5 tops out at ``high``: the TUI's seed skipped ``xhigh``.
        ("xhigh", _LAUNCH, "ANTHROPIC_API_KEY", "high"),
        # A model that cannot reason supports only ``off``.
        (
            "high",
            ["--provider", "anthropic", "--model", "claude-3-5-haiku-latest"],
            "ANTHROPIC_API_KEY",
            "off",
        ),
        # gpt-5-mini cannot switch reasoning off (``thinkingLevelMap.off = null``):
        # ``off`` clamps up to its lowest level, as the adapter would send it.
        ("off", ["--provider", "openai", "--model", "gpt-5-mini"], "OPENAI_API_KEY", "minimal"),
    ],
    ids=["xhigh-on-high-max", "high-on-non-reasoning", "off-on-reasoning-mandatory"],
)
async def test_the_default_is_clamped_to_the_model_and_not_recorded(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    default: str,
    launch: list[str],
    key: str,
    expected: str,
) -> None:
    """Owner decision (3): CLAMP, as pi's ``clampThinkingLevel`` (``sdk.ts:277``)
    and aelix's own resume restore do. The TUI's seed asked
    ``get_supported_thinking_levels`` and skipped: ``xhigh`` there stayed ``off``.

    Not recorded in any mode: the headless modes record no default, and the TUI's
    seed skipped (so recorded nothing for) a default its model did not list — the
    TUI's record keeps that test (``_record_tui_default_level``)."""

    monkeypatch.setenv(key, "fake-literal")
    _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], *launch) == 0

    assert seen["thinking"] == expected
    assert _shape(_only_file(env)) == ["model_change"]


@pytest.mark.parametrize("default", [None, "off"], ids=["unset", "explicit-off"])
async def test_the_bottom_rung_is_clamped_like_the_others(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str, default: str | None
) -> None:
    """Review round 2 (verify NB, cross F6): pi clamps whatever its ladder resolved
    (``sdk.ts:273-278``), the bottom rung included. gpt-5-mini cannot switch reasoning
    off (``thinkingLevelMap.off = null``): on ``7dd70238`` an unset default stayed at
    ``off`` (the request then carried no reasoning field, the endpoint's default)
    while ``defaultThinkingLevel=off`` ran at ``minimal``. Both run at ``minimal``
    now, and neither is recorded."""

    monkeypatch.setenv("OPENAI_API_KEY", "fake-literal")
    if default is not None:
        _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], "--provider", "openai", "--model", "gpt-5-mini") == 0

    assert seen["thinking"] == "minimal"
    assert _shape(_only_file(env)) == ["model_change"]


# === 3. what a new session records: exactly what 529b997e recorded ===========


@pytest.mark.parametrize(
    ("default", "flag", "expected", "tui_shape", "headless_shape"),
    [
        # The TUI's seed applied a supported default through ``set_thinking_level``,
        # which appended it (#198); the headless modes never applied it.
        ("high", None, "high", ["model_change", "thinking:high"], ["model_change"]),
        # ``off`` over the unset ``off`` was no change: nothing appended.
        ("off", None, "off", ["model_change"], ["model_change"]),
        # #198's flag carry, every mode; the default's record stands down for it.
        ("high", "low", "low", ["model_change", "thinking:low"], ["model_change", "thinking:low"]),
        ("high", "high", "high", ["model_change", "thinking:high"], ["model_change", "thinking:high"]),
        # ``off`` is the unset sentinel: the flag carry does not write it.
        ("high", "off", "off", ["model_change"], ["model_change"]),
    ],
    ids=["supported-default", "default-off", "flag", "flag-equal-to-default", "flag-off"],
)
async def test_a_new_session_records_what_529b997e_recorded(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    default: str,
    flag: str | None,
    expected: str,
    tui_shape: list[str],
    headless_shape: list[str],
) -> None:
    """Owner decision 2026-10-15 (review round 3): the seed runs in every mode, but no
    new recording path. Rounds 1-2 recorded the start level after the start model
    in every mode (pi ``sdk.ts:458``): a headless ``--thinking off`` or default wrote
    ``thinking:off``, and a headless default ``thinking:high``, which then outranked
    every later default. A flag equal to the default is recorded once: the flag
    carry writes it, and the TUI's default record stands down (``restored``)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)
    extra = ["--thinking", flag] if flag is not None else []

    assert await _launch(env, *_MODES[mode], *extra, *_LAUNCH) == 0

    assert seen["thinking"] == expected
    path = _only_file(env)
    assert _shape(path) == (tui_shape if mode == "tui" else headless_shape)
    entries = _entries(path)
    if len(entries) > 1:
        assert entries[1]["parentId"] == entries[0]["id"]


@pytest.mark.parametrize(
    ("created_in", "expected", "shape"),
    [
        # Recorded by the TUI (as on 529b997e): the session keeps it.
        ("tui", "high", ["model_change", "thinking:high", "user", "assistant"]),
        # Not recorded headless: the default set since applies.
        ("print", "low", ["model_change", "user", "assistant"]),
    ],
    ids=["tui-recorded-the-default", "headless-recorded-nothing"],
)
async def test_reopening_after_the_default_changed(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    created_in: str,
    expected: str,
    shape: list[str],
) -> None:
    """Fix item 1 of review round 3 (``.omc/probes/286-live/r3/rows.*.out``, R1): a
    ``-p`` run at ``defaultThinkingLevel=high``, the default then set to ``low``,
    ``--continue``: ``3d1bc5fa`` had recorded ``high`` at creation and reopened at
    it; nothing is recorded now, so the reopen runs at the default set since. A TUI
    launch records the default it applied, as ``run_tui``'s seed did on
    ``529b997e``, and its reopen comes back at that."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    if created_in == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    _settings(env, defaultThinkingLevel="high")
    _stub_modes(monkeypatch, drive=_one_turn)
    assert await _launch(env, *_MODES[created_in], *_LAUNCH) == 0

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    _settings(env, defaultThinkingLevel="low")
    seen = _stub_modes(monkeypatch)
    assert await _launch(env, "--continue", "--mode", "rpc", *_LAUNCH) == 0

    assert seen["thinking"] == expected
    assert _shape(_only_file(env)) == shape


async def test_a_clamped_default_comes_back_at_the_default_after_a_model_switch(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2 (verify blocking 1, cross F3). ``defaultThinkingLevel=high``, a
    session started on a model that cannot reason (so it runs at ``off``), a model
    switch to a reasoning model, a turn, quit, ``--continue``: the reopen runs at the
    default, as ``529b997e`` did. Nothing recorded the clamped ``off``. (pi re-applies
    the default on the model switch itself, ``_getThinkingLevelForModelSwitch``;
    aelix's ``set_model`` does not — a follow-up.)"""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="high")
    during: dict[str, Any] = {}

    async def _switch_then_turn(runtime: Any) -> None:
        await runtime.harness.set_model(get_model("anthropic", "claude-haiku-4-5"))
        during["level"] = runtime.harness.state.thinking_level
        await _one_turn(runtime)

    seen = _stub_modes(monkeypatch, drive=_switch_then_turn)
    assert await _launch(
        env, "--mode", "rpc", "--provider", "anthropic", "--model", "claude-3-5-haiku-latest"
    ) == 0
    assert seen["thinking"] == "off"
    assert during["level"] == "off"  # set_model does not re-apply the default (pi does)

    seen = _stub_modes(monkeypatch)
    assert await _launch(env, "--continue", "--mode", "rpc") == 0

    assert (seen["model"].provider, seen["model"].id) == ("anthropic", "claude-haiku-4-5")
    assert seen["thinking"] == "high"
    shape = _shape(_only_file(env))
    assert not any(t.startswith("thinking:") for t in shape), shape


# === 4. the ladder: flag > session > default > off ===========================


async def _session(env: Path, *, levels: tuple[str, ...] = (), conversation: bool = True) -> str:
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=_sessions(env))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    if conversation:
        await session.append_message(UserMessage(content=[TextContent(text="q")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="a")],
                stop_reason="end_turn",
                provider="anthropic",
                model="claude-haiku-4-5",
                api="anthropic-messages",
            )
        )
    for level in levels:
        await session.append_thinking_level_change(level)
    return str(session.session_file)


@pytest.mark.parametrize(
    ("recorded", "default"),
    [("medium", "low"), ("off", "high"), ("high", "high")],
    ids=["recorded-medium", "recorded-off", "recorded-equal-to-default"],
)
async def test_the_sessions_level_outranks_the_default(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str, recorded: str, default: str
) -> None:
    """#198's rung. ``off`` is the case a value-sniffing guard cannot see: a restored
    explicit ``off`` looks like "never set", and the seed must not refill it (nor
    write over it in the file). A restored level equal to the default is not
    written again: the TUI's default record stands down for a restored level, as
    ``run_tui``'s seed did."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, levels=("high", recorded))
    _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--session", path, *_MODES[mode], *_LAUNCH) == 0

    assert seen["thinking"] == recorded
    assert _shape(path) == ["user", "assistant", "thinking:high", f"thinking:{recorded}"]


async def test_a_reopened_sessions_level_is_clamped_to_the_model_on_a_normal_launch(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Review round 2 (verify M07, cross MX): ``_async_main`` passes ``clamp=not
    launch_pending`` to ``_seed_startup_state``. A session left at ``xhigh``,
    reopened on claude-haiku-4-5 (which tops out at ``high``), starts at ``high``;
    with the call site passing ``clamp=False`` it reported ``xhigh`` (get_state, the
    footer, a model-inheriting child's ``--thinking``). Nothing is written: the
    session keeps its own level."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, levels=("xhigh",))
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--session", path, *_MODES[mode], *_LAUNCH) == 0

    assert seen["thinking"] == "high"
    assert _shape(path) == ["user", "assistant", "thinking:xhigh"]


async def test_a_conversation_with_no_level_starts_at_the_default(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A session written before #198 (or by a headless run) holds no level: it opens
    at the default, as pi's ``hasExistingSession && !hasThinkingEntry`` branch does.
    The TUI records it (``run_tui``'s seed did, through ``set_thinking_level``); the
    headless modes record nothing, as on ``529b997e``."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env)
    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--session", path, *_MODES[mode], *_LAUNCH) == 0

    assert seen["thinking"] == "high"
    expected = ["user", "assistant", "thinking:high"] if mode == "tui" else ["user", "assistant"]
    assert _shape(path) == expected


@pytest.mark.parametrize(
    ("default", "flag"),
    [(None, None), ("off", None), ("high", "off")],
    ids=["no-default", "default-off", "flag-off"],
)
async def test_a_conversation_with_no_level_records_no_off(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str, default: str | None, flag: str | None
) -> None:
    """``off`` is not written into a session, whichever rung it came from — chosen
    (``--thinking off``, ``defaultThinkingLevel=off``) or not (the bottom rung)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env)
    if default is not None:
        _settings(env, defaultThinkingLevel=default)
    extra = ["--thinking", flag] if flag is not None else []
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--session", path, *_MODES[mode], *extra, *_LAUNCH) == 0

    assert seen["thinking"] == "off"
    assert _shape(path) == ["user", "assistant"]


# --- a session left without a prompt: #198's restore, unchanged ---------------


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(None, "low"), ("high", "high")],
    ids=["no-flag", "flag-for-this-run"],
)
async def test_a_level_recorded_with_no_conversation_comes_back(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str | None,
    expected: str,
) -> None:
    """Fix item 2 of review round 3 (``rows.*.out`` R2/R2b, ``tui.*.out`` T7): a level
    chosen in a session left without a prompt — ``/thinking low`` in a TUI opened
    and quit, an RPC ``set_thinking_level low`` with no prompt — comes back on the
    next launch, as on ``529b997e`` (a level set before the first prompt is still a
    decision, ADR-0239). ``3d1bc5fa`` skipped the session rung without a
    conversation (pi's ``hasExistingSession``) and recorded the bottom rung's
    ``off`` after it, so the level was lost for good (round-2 verify B1). A
    ``--thinking`` outranks it for that run only and is not written over it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, levels=("low",), conversation=False)
    extra = ["--thinking", flag] if flag is not None else []

    seen = _stub_modes(monkeypatch, drive=_one_turn)
    assert await _launch(env, "--session", path, "-p", "hi", *extra, *_LAUNCH) == 0
    assert seen["thinking"] == expected

    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch, drive=_one_turn)
    assert await _launch(env, "--session", path, "-p", "again", *_LAUNCH) == 0
    assert seen["thinking"] == "low"
    assert _shape(path) == [
        "thinking:low", "model_change", "user", "assistant", "user", "assistant"
    ]


@pytest.mark.parametrize(
    ("first_default", "tui_level", "tui_shape"),
    [
        (None, "high", ["model_change"]),
        # The TUI recorded the default it applied (as on 529b997e); headless did not.
        ("low", "low", ["model_change", "thinking:low"]),
    ],
    ids=["opened-with-no-default", "opened-at-default-low"],
)
async def test_a_session_left_without_a_prompt_on_its_next_launch(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    first_default: str | None,
    tui_level: str,
    tui_shape: list[str],
) -> None:
    """Opened and left without a prompt — a TUI opened and quit, an RPC host that
    never prompted — then ``defaultThinkingLevel`` set to ``high``, then
    ``--continue -p``. With no level recorded the new default applies; a level the
    first launch recorded (only the TUI records its default, ``low`` here) comes
    back. Both as on ``529b997e``; the second launch writes nothing."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    if first_default is not None:
        _settings(env, defaultThinkingLevel=first_default)
    _stub_modes(monkeypatch)
    assert await _launch(env, *_MODES[mode], *_LAUNCH) == 0

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch)
    assert await _launch(env, "--continue", "-p", "hi", *_LAUNCH) == 0

    if mode == "tui":
        assert seen["thinking"] == tui_level
        assert _shape(_only_file(env)) == tui_shape
    else:
        assert seen["thinking"] == "high"
        assert _shape(_only_file(env)) == ["model_change"]


async def test_no_session_starts_at_the_default(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--no-session``: an in-memory session, the same ladder; no file appears, and
    the default is not recorded headless."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="medium")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--no-session", "-p", "hi", *_LAUNCH) == 0

    assert seen["thinking"] == "medium"
    assert _files(env) == []
    branch = await seen["runtime"].harness.session.get_branch()
    assert [e.type for e in branch] == ["model_change"]


# === 5. a delegated child and /new ==========================================


async def _child_file(env: Path) -> Path:
    from aelix_agents.child_session import CHILD_ORIGIN_TYPE

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
    return path


@pytest.mark.parametrize(
    ("flag", "expected", "shape"),
    [
        (None, "high", ["custom", "model_change"]),
        ("off", "off", ["custom", "model_change"]),
        ("low", "low", ["custom", "model_change", "thinking:low"]),
    ],
    ids=["own-model-no-thinking", "parent-at-off", "profile-thinking"],
)
async def test_a_delegated_child_starts_at_its_ladder(
    env: Path, monkeypatch: pytest.MonkeyPatch, flag: str | None, expected: str, shape: list[str]
) -> None:
    """A child is ``--mode json -p --session <file the parent published>``
    (``resolver.profile_to_argv``, ADR-0243). Owner decision (a): a profile naming its
    own model and no ``thinking:`` gets no ``--thinking`` (#354, argv unchanged), so
    it now runs at the settings default — on 529b997e ``off``. A child of a parent at
    ``off`` gets ``--thinking off``, which outranks its own default; a profile's
    ``thinking:`` is its ``--thinking``. Its file records what it recorded on
    ``529b997e``: the origin, the model, and #198's carry of a non-``off`` flag."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="high")
    path = await _child_file(env)
    seen = _stub_modes(monkeypatch)
    extra = ["--thinking", flag] if flag is not None else []

    code = await entry_mod._async_main(
        ["--mode", "json", "-p", "--session", str(path), *extra, *_LAUNCH, "Reply OK"]
    )

    assert code == 0
    assert seen["thinking"] == expected
    assert _shape(path) == shape


async def test_a_child_depth_does_not_turn_the_default_off(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2 (cross M3): owner decision (a) gives a delegated child the
    default; option (b), no default for a child, was rejected. A child runs with
    ``AELIX_SUBAGENT_DEPTH=1`` in its environment; a launch there still starts at the
    settings default."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", "1")
    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--mode", "json", "-p", "hi", *_LAUNCH) == 0

    assert seen["thinking"] == "high"


async def test_a_trusted_projects_default_applies(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Review round 2 (cross M2): the default is read through the launch's settings
    manager, project scope included — not from the global file only. A trusted
    project's ``.aelix/settings.json`` sets ``high`` and the global file sets
    nothing. The TUI records it, as its seed did; the headless modes do not."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    project = Path.cwd() / ".aelix"
    project.mkdir()
    (project / "settings.json").write_text(
        json.dumps({"defaultThinkingLevel": "high"}), encoding="utf-8"
    )
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], "--approve", *_LAUNCH) == 0

    assert seen["thinking"] == "high"
    expected = ["model_change", "thinking:high"] if mode == "tui" else ["model_change"]
    assert _shape(_only_file(env)) == expected


async def test_new_session_carries_the_seeded_level(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RPC ``new_session`` (``/new``) carries the live level into the new session and
    records it unless it is ``off`` — ADR-0239 decision 6's carry, unchanged. On
    529b997e the live level of an RPC launch was ``off``, so the new session started
    at ``off`` and recorded nothing; it is the seeded level now (a clamped one too —
    a follow-up)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="high")
    made: dict[str, Any] = {}

    async def _new(runtime: Any) -> None:
        await runtime.new_session()
        made["level"] = runtime.harness.state.thinking_level
        made["path"] = str(runtime.harness.session.session_file)

    _stub_modes(monkeypatch, drive=_new)
    assert await _launch(env, "--mode", "rpc", *_LAUNCH) == 0

    assert made["level"] == "high"
    assert _shape(made["path"]) == ["model_change", "thinking:high"]


# === 6. a pending launch is judged after the late-route decision =============

_PROBE_START = """
import json

from aelix_ai.models import get_model


def setup(aelix):
    async def _on_start(event, ctx):
        model = ctx.model
        with open({out!r}, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{
                "api": getattr(model, "api", None),
                "level": aelix.get_thinking_level(),
            }}) + "\\n")
        if {move!r}:
            await aelix.set_model(get_model("anthropic", "claude-haiku-4-5"))

    aelix.on("session_start", _on_start)
"""

_GUARD2_ID = "somevendor/brand-new-model-x"


@pytest.mark.parametrize(
    ("move", "default", "settled", "expected"),
    [
        # Guard 2's route for an id OpenRouter does not list cannot reason.
        (False, "high", ("openrouter", _GUARD2_ID), "off"),
        # A handler's own model stands after the decision (#367): judged against it.
        (True, "xhigh", ("anthropic", "claude-haiku-4-5"), "high"),
        # ...and a default that model supports is the default as written: recorded.
        (True, "high", ("anthropic", "claude-haiku-4-5"), "high"),
    ],
    ids=["route-stands", "handler-moved-the-model", "handler-moved-default-fits"],
)
async def test_a_pending_launch_judges_the_level_after_the_decision_not_on_the_placeholder(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    move: bool,
    default: str,
    settled: tuple[str, str],
    expected: str,
) -> None:
    """Owner decision (5). An id no registered provider claims, with the user's own
    OpenRouter key, is guard 2 (#367): sent to OpenRouter as written, PENDING while
    ``session_start`` runs on ``Model(id, provider)`` (``api 'unknown'``, which
    supports only ``off``). The default is applied then as written — the probe
    handler sees it on the placeholder — and judged after the late-route decision
    against the model the run is on: the route, or the model a handler moved to.
    Only the TUI records it, and only as written (a default the settled model
    supports): its record runs right before ``run_tui``, after the decision, where
    ``run_tui``'s seed recorded it on ``529b997e``."""

    out = env / "start.jsonl"
    (env / "probe.py").write_text(_PROBE_START.format(out=str(out), move=move), encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)

    code = await _launch(env, *_MODES[mode], "-e", str(env / "probe.py"), "--model", _GUARD2_ID)

    assert code == 0, capsys.readouterr().err
    assert (seen["model"].provider, seen["model"].id) == settled
    during = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert during == [{"api": "unknown", "level": default}]
    assert seen["thinking"] == expected
    shape = _shape(_only_file(env))
    thinking = [t for t in shape if t.startswith("thinking:")]
    if mode == "tui" and expected == default:
        assert shape[-1] == f"thinking:{expected}" and thinking == [shape[-1]], shape
    else:
        assert thinking == [], shape


@pytest.mark.parametrize(
    ("move", "recorded", "settled", "expected"),
    [
        (False, "high", ("openrouter", _GUARD2_ID), "off"),
        (True, "xhigh", ("anthropic", "claude-haiku-4-5"), "high"),
    ],
    ids=["route-stands", "handler-moved-the-model"],
)
async def test_a_pending_launch_settles_a_reopened_sessions_level_after_the_decision(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    move: bool,
    recorded: str,
    settled: tuple[str, str],
    expected: str,
) -> None:
    """Review round 2 (verify M11, cross ME/MS): the session rung on a pending launch.
    A session with a conversation that recorded ``high``, reopened on a guard-2 route:
    the restore applies it as written while ``session_start`` runs on the placeholder
    (the probe sees ``high``), and ``_settle_start_level`` clamps it after the decision
    against the model the run is on — ``off`` on the guard-2 route, which cannot
    reason. With the settle handed the default's level instead of the restored one,
    or no settle, it stayed ``high`` on that route. Nothing is written: the session
    keeps its own level."""

    out = env / "start.jsonl"
    (env / "probe.py").write_text(_PROBE_START.format(out=str(out), move=move), encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    path = await _session(env, levels=(recorded,))
    seen = _stub_modes(monkeypatch)

    code = await _launch(
        env, "--session", path, *_MODES[mode], "-e", str(env / "probe.py"), "--model", _GUARD2_ID
    )

    assert code == 0, capsys.readouterr().err
    assert (seen["model"].provider, seen["model"].id) == settled
    during = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert during == [{"api": "unknown", "level": recorded}]
    assert seen["thinking"] == expected
    assert [t for t in _shape(path) if t.startswith("thinking:")] == [f"thinking:{recorded}"]


async def test_a_pending_launch_leaves_the_flag_unclamped(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """``--thinking`` is not clamped at launch, pending or not: ``_settle_start_level``
    is handed ``None`` for it. Guard 2's route for an id OpenRouter does not list
    cannot reason, so a settle handed the live level (review round 2, cross F3)
    turned ``--thinking high`` into ``off`` there; the adapters clamp at request
    build. The flag is recorded by #198's carry before ``session_start``, ahead of
    the deferred model record, as on ``529b997e``."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    _settings(env, defaultThinkingLevel="low")
    seen = _stub_modes(monkeypatch)

    code = await _launch(env, *_MODES[mode], "--thinking", "high", "--model", _GUARD2_ID)

    assert code == 0, capsys.readouterr().err
    assert (seen["model"].provider, seen["model"].id) == ("openrouter", _GUARD2_ID)
    assert seen["thinking"] == "high"
    assert _shape(_only_file(env)) == ["thinking:high", "model_change"]


@pytest.mark.parametrize("held_mode", ["rpc", "tui"])
@pytest.mark.parametrize("source", ["default", "session"])
async def test_a_held_launch_keeps_its_level_unclamped_and_records_nothing(
    env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    held_mode: str,
) -> None:
    """#367: a launch model naming a provider registered in ``session_start`` is held
    on ``Model(id, provider)`` (``api 'unknown'``) in RPC and the TUI, and is pending
    before that on an unresolved route. Clamped against such a placeholder any level
    is ``off`` — the #286 defect again, after ``/model`` leaves the hold
    (``set_model`` keeps the level). Applied as written; the adapters clamp at
    request build. ``session``: #198's restore clamped a resumed session's recorded
    ``high`` against the unresolved launch placeholder on 529b997e and ran at
    ``off``. A held launch records nothing (#413), and the TUI's default record
    skips the placeholder, which supports only ``off``, as ``run_tui``'s seed did."""

    from tests.cli.test_launch_route_344 import _SESSION_START_EXTENSION as ext_src

    if held_mode == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    (env / "sessext.py").write_text(ext_src, encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake-literal")
    pick: list[str] = []
    if source == "default":
        _settings(env, defaultThinkingLevel="high")
    else:
        pick = ["--session", await _session(env, levels=("high",))]
    seen = _stub_modes(monkeypatch)

    code = await _launch(
        env, *pick, *_MODES[held_mode], "-e", str(env / "sessext.py"), "--model", "sessext/m1"
    )

    assert code == 0, capsys.readouterr().err
    assert seen["model"].api == "unknown"
    assert seen["thinking"] == "high"
    if source == "default":
        assert _entries(_only_file(env)) == []
    else:
        assert _shape(_only_file(env)) == ["user", "assistant", "thinking:high"]


# === 7. the helpers' own guards ==============================================


def _harness(session: Session | None, model: Model | None, level: str | None = None) -> AgentHarness:
    async def _stream(*_a: Any, **_k: Any) -> Any:  # pragma: no cover — never streamed
        raise AssertionError("no turn runs here")

    options: dict[str, Any] = {"model": model, "stream_fn": _stream, "session": session}
    if level is not None:
        options["thinking_level"] = level
    return AgentHarness(AgentHarnessOptions(**options))


_HAIKU_MODEL = get_model("anthropic", "claude-haiku-4-5")


def test_seed_stands_down_for_a_restored_or_set_level() -> None:
    harness = _harness(None, _HAIKU_MODEL)
    assert entry_mod._seed_default_thinking_level(harness, "high", restored=True) is None
    assert harness.state.thinking_level == "off"

    # A level already on the harness that ``restored`` did not report (a caller
    # building the harness with one). No launch path reaches this today: an
    # extension cannot set a level in ``setup()`` (``aelix.set_thinking_level``
    # raises before the harness is bound, and the extension fails to load).
    harness = _harness(None, _HAIKU_MODEL, level="low")
    assert entry_mod._seed_default_thinking_level(harness, "high", restored=False) is None
    assert harness.state.thinking_level == "low"

    # No default: the bottom rung, ``off``, which haiku can do.
    harness = _harness(None, _HAIKU_MODEL)
    assert entry_mod._seed_default_thinking_level(harness, None, restored=False) == "off"
    assert harness.state.thinking_level == "off"


def test_the_bottom_rung_is_clamped_unless_told_not_to_judge() -> None:
    must_reason = get_model("openai", "gpt-5-mini")  # thinkingLevelMap.off = null
    harness = _harness(None, must_reason)
    assert entry_mod._seed_default_thinking_level(harness, None, restored=False) == "minimal"
    assert harness.state.thinking_level == "minimal"

    harness = _harness(None, must_reason)
    assert entry_mod._seed_default_thinking_level(
        harness, None, restored=False, clamp=False
    ) == "off"
    entry_mod._settle_start_level(harness, "off")
    assert harness.state.thinking_level == "minimal"


def test_seed_applies_as_written_when_told_not_to_judge_or_on_a_placeholder() -> None:
    harness = _harness(None, _HAIKU_MODEL)
    assert entry_mod._seed_default_thinking_level(harness, "xhigh", restored=False, clamp=False) == "xhigh"

    placeholder = Model(id="m1", provider="sessext")  # api 'unknown', reasoning False
    harness = _harness(None, placeholder)
    assert entry_mod._seed_default_thinking_level(harness, "high", restored=False) == "high"
    entry_mod._settle_start_level(harness, "high")
    assert harness.state.thinking_level == "high"


def test_settle_leaves_a_level_a_handler_set() -> None:
    harness = _harness(None, _HAIKU_MODEL, level="minimal")
    entry_mod._settle_start_level(harness, "xhigh")
    assert harness.state.thinking_level == "minimal"
    harness.state.thinking_level = "xhigh"
    entry_mod._settle_start_level(harness, "xhigh")
    assert harness.state.thinking_level == "high"


async def _levels(session: Session) -> list[str]:
    return [
        e.thinking_level  # type: ignore[union-attr]
        for e in await session.get_branch()
        if e.type == "thinking_level_change"
    ]


async def test_the_tui_default_record_writes_a_supported_default_once_applied() -> None:
    session = Session(MemorySessionStorage())
    harness = _harness(session, _HAIKU_MODEL, level="high")
    assert await entry_mod._record_tui_default_level(harness, "high", restored=False) is True
    assert await _levels(session) == ["high"]


@pytest.mark.parametrize(
    ("default", "live", "restored", "model"),
    [
        # A flag or a restored level applied: the seed stood down (#198's signal).
        ("high", "high", True, _HAIKU_MODEL),
        # No default (the bottom rung), and ``off`` (no change from the unset ``off``).
        (None, "off", False, _HAIKU_MODEL),
        ("off", "off", False, _HAIKU_MODEL),
        # The live level is not the default: clamped (``xhigh`` on a ``high``-max
        # model), or a ``session_start`` handler chose another one.
        ("xhigh", "high", False, _HAIKU_MODEL),
        ("high", "low", False, _HAIKU_MODEL),
        # The model the run is on does not support it as written: a handler moved
        # the model after the clamp, a held placeholder, no model at all.
        ("xhigh", "xhigh", False, _HAIKU_MODEL),
        ("high", "high", False, Model(id="m1", provider="sessext")),
        ("high", "high", False, None),
    ],
    ids=[
        "restored",
        "no-default",
        "default-off",
        "clamped",
        "handler-level",
        "unsupported-as-written",
        "placeholder",
        "no-model",
    ],
)
async def test_the_tui_default_record_keeps_the_old_seeds_conditions(
    default: str | None, live: str, restored: bool, model: Model | None
) -> None:
    """``run_tui``'s seed on ``529b997e`` recorded (through ``set_thinking_level``)
    only a default the model supported as written, over the unset ``off``, when no
    flag or restored level had applied. Each row is a launch it recorded nothing
    for."""

    session = Session(MemorySessionStorage())
    harness = _harness(session, model, level=live)
    assert (
        await entry_mod._record_tui_default_level(harness, default, restored=restored)
        is False
    )
    assert await _levels(session) == []


async def test_the_tui_default_record_is_silent_on_a_read_only_or_failing_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The seed's ``contextlib.suppress(Exception)``: a viewer of another terminal's
    session (``read_only``) and a storage failure write nothing and print nothing."""

    owned = Session(MemorySessionStorage())
    viewer = Session(ReadOnlySessionStorage(owned.get_storage()))
    harness = _harness(viewer, _HAIKU_MODEL, level="high")
    assert await entry_mod._record_tui_default_level(harness, "high", restored=False) is False
    assert await _levels(owned) == []

    async def _fail(self: Session, level: str) -> str:
        raise SessionError("storage", "Failed to append session entry: injected")

    monkeypatch.setattr(Session, "append_thinking_level_change", _fail)
    harness = _harness(Session(MemorySessionStorage()), _HAIKU_MODEL, level="high")
    assert await entry_mod._record_tui_default_level(harness, "high", restored=False) is False
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


def test_the_settings_default_is_read_guarded() -> None:
    """``settings.json`` is not validated on load: a value that is not a level seeds
    nothing (on a placeholder it would otherwise be applied as written), and a
    settings object that raises does not stop the launch."""

    from aelix_ai.settings import SettingsManager

    assert entry_mod._settings_thinking_default(None) is None
    for value in ("banana", 5, "", None):
        sm = SettingsManager.in_memory({"defaultThinkingLevel": value})
        assert entry_mod._settings_thinking_default(sm) is None, value
    for level in ("off", "minimal", "low", "medium", "high", "xhigh"):
        sm = SettingsManager.in_memory({"defaultThinkingLevel": level})
        assert entry_mod._settings_thinking_default(sm) == level

    class _Raising:
        def get_default_thinking_level(self) -> str:
            raise RuntimeError("malformed settings")

    assert entry_mod._settings_thinking_default(_Raising()) is None  # type: ignore[arg-type]


_SELECT_AND_START = """
import json


def setup(aelix):
    async def _on_select(event, ctx=None):
        with open({out!r}, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{"select": event.level}}) + "\\n")

    async def _on_start(event, ctx):
        with open({out!r}, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({{"start": aelix.get_thinking_level()}}) + "\\n")

    aelix.on("thinking_level_select", _on_select)
    aelix.on("session_start", _on_start)
"""


async def test_session_start_sees_the_start_level_and_no_select_fires(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """The level is resolved BEFORE ``session_start``, as pi's ``createAgentSession``
    resolves it before ``bindExtensions``: a handler there sees the level the run
    starts at (the TUI's seed ran after ``session_start``, so a handler saw ``off``).
    And it is assigned, not selected: no ``thinking_level_select`` reports a choice
    nobody made (the restore's rule, ADR-0239; the TUI's seed fired one)."""

    out = env / "events.jsonl"
    (env / "probe.py").write_text(_SELECT_AND_START.format(out=str(out)), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultThinkingLevel="high")
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], "-e", str(env / "probe.py"), *_LAUNCH) == 0, (
        capsys.readouterr().err
    )

    events = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert events == [{"start": "high"}]
    assert seen["thinking"] == "high"
