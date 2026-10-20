"""#433 — a saved ``xhigh`` on a reclassified row starts at ``max`` and sends ``max``.

The owner's decision of 2026-10-20 reclassifies the 24 rows whose map spelled their
top tier ``"xhigh": "max"``; 21 of them say ``"max": "max"`` (no ``xhigh`` key), as
pi did in ``fbdd46389`` (OpenRouter's 3 DeepSeek V4 rows follow pi's later
``xhigh: "xhigh"``, ``tests/test_max_thinking_level_433.py``). On the 21, nothing a
user saved may change what is SENT: a
``defaultThinkingLevel: "xhigh"`` and a session recorded at ``xhigh`` reach the
clamp, which scans up first (pi ``models.ts:1265`` at ``fdce48eb4``), lands on
``max``, and the adapter sends ``output_config.effort = "max"`` - exactly what
``7faf4b9a`` sent for ``xhigh`` on ``anthropic/claude-opus-4-6``
(``.omc/probes/433-live/impl/wire.before.txt``, cases A1, A3, S1-S2). Only the name
the screen shows moves, from ``xhigh`` to ``max``.

These drive the real ``_async_main`` (mode entries stubbed, as in
``test_default_thinking_every_mode_286.py``) and read the level the harness starts
at, then build the request ``stream_anthropic`` sends at that level.

MEASURED ON ``7faf4b9a`` (these tests, copied onto it): ``--thinking max`` was
dropped with a diagnostic and ``defaultThinkingLevel: "max"`` started the harness
at ``off`` - the clamp read the unknown spelling as "the model's first level" - so
both sent ``thinking: {"type": "disabled"}`` (the recorder's cases A2/A4); on
``claude-haiku-4-5`` the same default started at ``off`` instead of ``high``. A
saved ``xhigh`` started at ``xhigh`` and sent ``max``: what moves is the name.

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
from aelix_ai.models import get_model
from aelix_ai.streaming import Model
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_agents_use_keeps_live_model_412 import _tui
from tests.cli.test_default_thinking_every_mode_286 import (
    _MODES,
    _one_turn,
    _only_file,
    _session,
    _sessions,
    _shape,
    _stub_modes,
)
from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.env_sandbox import sandbox_home
from tests.test_max_thinking_level_433 import _anthropic_sends, _completions_sends

_OPUS_46 = ["--provider", "anthropic", "--model", "claude-opus-4-6"]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_default_thinking_every_mode_286.py``'s sandbox: no key, an empty agent dir."""

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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    register_providers()
    return tmp_path


@pytest.fixture(params=list(_MODES))
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    return request.param


def _settings(env: Path, **values: Any) -> None:
    (env / "agent" / "settings.json").write_text(json.dumps(values), encoding="utf-8")


async def _launch(env: Path, *argv: str) -> int:
    return await entry_mod._async_main(["--session-dir", _sessions(env), *argv])


def _opus_46() -> Model:
    model = get_model("anthropic", "claude-opus-4-6")
    assert model is not None
    assert model.thinking_level_map == {"max": "max"}, "the row this file is about moved"
    return model


_SENT_ON_7FAF4B9A = {
    "thinking": {"type": "adaptive", "display": "summarized"},
    "output_config": {"effort": "max"},
}


@pytest.mark.parametrize("default", ["xhigh", "max"])
async def test_a_saved_default_starts_at_max_and_sends_what_it_sent(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str, default: str
) -> None:
    _settings(env, defaultThinkingLevel=default)
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, *_MODES[mode], *_OPUS_46) == 0

    assert seen["thinking"] == "max"
    assert await _anthropic_sends(seen["model"], seen["thinking"]) == _SENT_ON_7FAF4B9A


async def test_a_session_recorded_at_xhigh_reopens_at_max_and_keeps_its_record(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """The session file is not rewritten: it keeps ``xhigh``, the level the user
    chose, and every launch clamps it again - so a row that one day offers
    ``xhigh`` again gets it back."""

    path = await _session(env, levels=("xhigh",))
    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "--session", path, *_MODES[mode], *_OPUS_46) == 0

    assert seen["thinking"] == "max"
    assert _shape(path) == ["user", "assistant", "thinking:xhigh"]
    assert await _anthropic_sends(_opus_46(), seen["thinking"]) == _SENT_ON_7FAF4B9A


async def test_the_thinking_flag_takes_max(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On 7faf4b9a ``--thinking max`` warned "Invalid --thinking level: max" and
    was dropped."""

    seen = _stub_modes(monkeypatch)

    assert await _launch(env, "-p", "hi", "--thinking", "max", *_OPUS_46) == 0

    assert seen["thinking"] == "max"
    assert "Invalid --thinking level" not in capsys.readouterr().err
    assert await _anthropic_sends(seen["model"], seen["thinking"]) == _SENT_ON_7FAF4B9A


async def test_max_on_a_row_without_it_runs_at_the_rows_top_level(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """``claude-haiku-4-5`` offers neither top level: the default comes down to
    ``high`` (#286's clamp), never to ``off``."""

    _settings(env, defaultThinkingLevel="max")
    seen = _stub_modes(monkeypatch)

    assert (
        await _launch(env, *_MODES[mode], "--provider", "anthropic", "--model", "claude-haiku-4-5")
        == 0
    )

    assert seen["thinking"] == "high"


# === review round 2: every path a saved or profile level takes ===============
#
# Round 1 renamed the rows; three paths still asked "does the row list this level
# as written?" and so treated a saved ``xhigh`` on a row that now names ``max``
# as foreign. Each test below is red on round 1's code (fcd2e8d4,
# ``.omc/probes/433-live/r2/``) and green on 7faf4b9a's behaviour.


def test_the_thinking_help_line_names_every_level() -> None:
    """``--help`` is the one place the levels are spelled out by hand
    (``cli/args.py``); nothing else ties it to :data:`VALID_THINKING_LEVELS`."""

    import io

    from aelix_coding_agent.cli.args import VALID_THINKING_LEVELS, print_help

    buf = io.StringIO()
    print_help(buf)
    line = next(ln for ln in buf.getvalue().splitlines() if "--thinking <level>" in ln)
    named = [part.strip() for part in line.split("--thinking <level>", 1)[1].split("|")]
    assert named == list(VALID_THINKING_LEVELS)


_PROFILES_433 = {
    "xthinker": "---\nname: xthinker\ndescription: x\nthinking: xhigh\ninherit_extensions: true\n---\nx\n",
    "maxthinker": "---\nname: maxthinker\ndescription: m\nthinking: max\ninherit_extensions: true\n---\nm\n",
}


def _profiles(env: Path) -> None:
    agents = env / "agent" / "agents"
    agents.mkdir(exist_ok=True)
    for name, body in _PROFILES_433.items():
        (agents / f"{name}.md").write_text(body, encoding="utf-8")


async def test_agents_use_of_a_thinking_xhigh_profile_runs_at_max_on_a_renamed_row(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/agents use`` asked whether the row lists the profile's level as written:
    round 1 skipped ``xhigh`` on ``claude-opus-4-6`` and the request kept the
    previous level (``medium``: effort ``medium``). 7faf4b9a applied it and sent
    ``max``; it is fitted to the row now, lands on ``max`` and sends ``max``."""

    _profiles(env)
    seen = await _tui(
        monkeypatch, ["--session-dir", _sessions(env), *_OPUS_46], ["think:medium", "xthinker"]
    )

    assert seen["thinking"] == ["medium", "max"]
    assert await _anthropic_sends(_opus_46(), "max") == _SENT_ON_7FAF4B9A


async def test_agents_use_of_a_thinking_xhigh_profile_on_deepseek_v4_sends_max(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same path on a completions row of the 21: round 1 sent ``high``
    (the level left at ``medium``, which DeepSeek V4 clamps up to ``high``)."""

    _profiles(env)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-fake")
    argv = ["--session-dir", _sessions(env), "--provider", "deepseek", "--model", "deepseek-v4-pro"]
    seen = await _tui(monkeypatch, argv, ["think:medium", "xthinker"])

    assert seen["thinking"] == ["medium", "max"]
    model = get_model("deepseek", "deepseek-v4-pro")
    assert model is not None
    assert await _completions_sends(model, "max") == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }


async def test_agents_use_of_a_thinking_max_profile_on_a_row_without_it_runs_at_the_top(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``claude-sonnet-4-5`` names neither top level: ``thinking: max`` comes down
    to ``high`` (budget 16384), as the CHANGELOG says a level a row lacks does.
    Round 1 (and 7faf4b9a, for ``xhigh``) skipped it and kept ``medium``'s 8192."""

    _profiles(env)
    argv = [
        "--session-dir",
        _sessions(env),
        "--provider",
        "anthropic",
        "--model",
        "claude-sonnet-4-5",
    ]
    seen = await _tui(monkeypatch, argv, ["think:medium", "maxthinker"])

    assert seen["thinking"] == ["medium", "high"]
    model = get_model("anthropic", "claude-sonnet-4-5")
    assert model is not None
    assert (await _anthropic_sends(model, "high"))["thinking"]["budget_tokens"] == 16384


async def test_a_tui_default_of_xhigh_on_a_renamed_row_is_recorded_as_written(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """7faf4b9a's TUI recorded the default (``xhigh`` was listed on
    ``claude-opus-4-6``), so a reopen after the default changed came back at
    ``xhigh`` and sent ``max``. Round 1 recorded nothing (the launch moved it to
    ``max``, which is not the default as written), and the reopen followed the
    new default: ``low``. The record now writes ``xhigh`` again; the reopen
    clamps it to ``max`` and sends what 7faf4b9a sent."""

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    _settings(env, defaultThinkingLevel="xhigh")
    first = _stub_modes(monkeypatch, drive=_one_turn)
    assert await _launch(env, *_OPUS_46) == 0
    assert first["thinking"] == "max"
    assert _shape(_only_file(env)) == ["model_change", "thinking:xhigh", "user", "assistant"]

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    _settings(env, defaultThinkingLevel="low")
    seen = _stub_modes(monkeypatch)
    assert await _launch(env, "--continue", "--mode", "rpc", *_OPUS_46) == 0

    assert seen["thinking"] == "max"
    assert await _anthropic_sends(_opus_46(), seen["thinking"]) == _SENT_ON_7FAF4B9A


async def test_a_tui_default_of_xhigh_where_it_runs_at_max_is_recorded_too(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule is "``xhigh`` the launch moved up to ``max``", so it also covers a
    row that gained ``max`` without ``xhigh`` in #433: ``claude-sonnet-4-6``.
    7faf4b9a ran it at ``high`` and recorded nothing; it runs at ``max`` now and
    the session keeps ``xhigh``."""

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    _settings(env, defaultThinkingLevel="xhigh")
    seen = _stub_modes(monkeypatch)
    sonnet_46 = ["--provider", "anthropic", "--model", "claude-sonnet-4-6"]
    assert await _launch(env, *sonnet_46) == 0

    assert seen["thinking"] == "max"
    assert _shape(_only_file(env)) == ["model_change", "thinking:xhigh"]


async def test_rpc_cycle_from_an_unfitted_xhigh_wraps_to_off(env: Path) -> None:
    """``--thinking xhigh`` (and an RPC ``set_thinking_level``) leaves the level
    as written; ``cycle_thinking_level`` looked it up in the row's list, did not
    find it on ``claude-opus-4-6`` (which names ``max``), fell back to index 0 and
    answered ``minimal``. 7faf4b9a wrapped from ``xhigh``, the last level, to
    ``off``; fitted first, ``xhigh`` steps as ``max`` does - to ``off``. The
    ``/settings`` Thinking level row is the same harness call."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_thinking_level
    from aelix_coding_agent.rpc.rpc_types import RpcCommandCycleThinkingLevel

    harness = AgentHarness(AgentHarnessOptions(model=_opus_46(), thinking_level="xhigh"))
    try:
        response = await _handle_cycle_thinking_level(
            harness, RpcCommandCycleThinkingLevel(id="c1")
        )
        assert getattr(response, "data", None) == {"level": "off"}
        assert harness.state.thinking_level == "off"
    finally:
        await harness.dispose()
