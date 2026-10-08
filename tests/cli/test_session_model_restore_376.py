"""#376 — a session reopened WITHOUT model flags comes back on the model it last ran on.

Measured on ``8f7d98aa`` (``.omc/probes/376-live/impl/``): ``aelix --mode rpc
--continue`` reported ``get_state`` model ``''/''`` with ``api='unknown'``; a
prompt then failed ``No provider registered for api='unknown'`` and its message
was still written to the session; with settings ``defaultModel`` set, every mode
(rpc, ``-p``, ``--mode json``, the TUI, in-session ``/resume``, RPC
``switch_session``) resumed on that default instead of the session's model.
``core/model_resolver.py::restore_model_from_session`` had no caller, and the
model every response names was read by nothing.

These drive the real ``_async_main`` (the mode entry stubbed, so no turn runs and
nothing is sent) over a session written by a real ``JsonlSessionRepo``:

* every mode restores the session's model, over a settings ``defaultModel`` too,
  and the thinking level the session recorded is clamped against THAT model;
* ``--model``/``--provider`` and an agent profile's ``model:`` win, as in pi;
* a model that cannot be restored falls back to what the launch would have
  chosen, with pi's ``Could not restore model …`` line (``. Using …`` only when
  the fallback can run), on stderr and on the runtime for the TUI;
* an id no catalogue lists comes back only where the launch would send it
  (guard 2: to OpenRouter on the user's own key, not a ``.env`` one);
* in-session ``switch_session`` restores the target's model, ``new_session``
  keeps the launch's.

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
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, register_providers

from tests.cli.test_launch_route_344 import _FakePipedStdin, _FakeTTYStdin
from tests.env_sandbox import sandbox_home

_HAIKU = ("anthropic", "claude-haiku-4-5")
_SONNET = ("anthropic", "claude-sonnet-4-5")
_OR_SONNET = ("openrouter", "anthropic/claude-sonnet-4.5")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
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


def _settings(env: Path, **values: Any) -> None:
    (env / "agent" / "settings.json").write_text(json.dumps(values), encoding="utf-8")


def _sessions(env: Path) -> str:
    return str(env / "sessions")


async def _session(
    env: Path,
    *answered_by: tuple[str, str],
    model_change: tuple[str, str] | None = None,
    thinking: str | None = None,
) -> str:
    """A session in the cwd: one prompt answered by each of ``answered_by`` in turn."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=_sessions(env))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    if thinking is not None:
        await session.append_thinking_level_change(thinking)
    if model_change is not None:
        await session.append_model_change(*model_change)
    for provider, model_id in answered_by:
        await session.append_message(UserMessage(content=[TextContent(text="q")]))
        await session.append_message(
            AssistantMessage(
                content=[TextContent(text="a")],
                stop_reason="end_turn",
                provider=provider,
                model=model_id,
                api="anthropic-messages" if provider == "anthropic" else "openai-completions",
            )
        )
    return str(session.session_file)


def _ident(model: Any) -> tuple[str, str]:
    return (model.provider, model.id)


def _stub_modes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Every mode entry stubbed: record the model and runtime it was handed."""

    import aelix_coding_agent.tui as tui_pkg
    from aelix_coding_agent import modes

    seen: dict[str, Any] = {}

    def _record(runtime: Any) -> None:
        seen["model"] = runtime.harness.current_model
        seen["thinking"] = runtime.harness.state.thinking_level
        seen["fallback"] = runtime.model_fallback_message
        seen["runtime"] = runtime

    async def _print(runtime: Any, **kwargs: Any) -> int:
        _record(runtime)
        return 0

    async def _rpc(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        _record(runtime_host)

    async def _tui(runtime: Any, **kwargs: Any) -> int:
        _record(runtime)
        return 0

    monkeypatch.setattr(modes, "run_print_mode", _print)
    monkeypatch.setattr(modes, "run_rpc_mode", _rpc)
    monkeypatch.setattr(tui_pkg, "run_tui", _tui)
    return seen


_MODES = {
    "rpc": ["--mode", "rpc"],
    "print": ["-p", "hi"],
    "json": ["--mode", "json", "hi"],
    "tui": [],
}


def _argv(env: Path, mode: str, *extra: str) -> list[str]:
    return ["--continue", "--session-dir", _sessions(env), *_MODES[mode], *extra]


@pytest.fixture(params=list(_MODES))
def mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "tui":
        monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())
    return request.param


async def test_every_mode_restores_the_sessions_model_over_the_settings_default(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """THE REGRESSION: 8f7d98aa resumed every mode on settings ``defaultModel``."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, mode))

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _HAIKU, err
    assert seen["model"].api == "anthropic-messages"
    assert seen["fallback"] is None
    assert "Could not restore" not in err


async def test_with_no_default_the_session_is_not_left_on_a_placeholder(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """The issue as filed: no flags, no default — 8f7d98aa sat on ``''/'' api='unknown'``.

    In print/json the no-model gate judges the restored route, not ``parsed``
    (which names nothing here): it used to refuse "No model selected"."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, mode))

    assert code == 0, capsys.readouterr().err
    assert _ident(seen["model"]) == _HAIKU
    assert seen["model"].api == "anthropic-messages"


async def test_the_latest_record_wins_a_model_change_or_a_response(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pi's ``getBranchSelection``: walking back, the first ``model_change`` or response."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    # A /model pick recorded AFTER the last response (haiku) — the pick wins.
    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=_sessions(env))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(Path.cwd())))
    await session.append_message(UserMessage(content=[TextContent(text="q")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="a")],
            provider="anthropic",
            model="claude-haiku-4-5",
            api="anthropic-messages",
        )
    )
    await session.append_model_change(*_SONNET)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _SONNET


async def test_explicit_flags_win_over_the_session(
    env: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """pi: ``options.model`` from ``--model`` is never overridden (``core/sdk.ts:224``)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        _argv(env, mode, "--provider", "anthropic", "--model", "claude-sonnet-4-5")
    )

    assert code == 0
    assert _ident(seen["model"]) == _SONNET


async def test_a_profile_model_wins_over_the_session(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0196: a profile's ``model:`` is a pick, like the flag it stands in for."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    await _session(env, _HAIKU)
    profile = env / "picker.md"
    profile.write_text(
        "---\nname: picker\ndescription: d\nprovider: anthropic\nmodel: claude-sonnet-4-5\n---\nbody\n",
        encoding="utf-8",
    )
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, "rpc", "--agent-file", str(profile)))

    assert code == 0
    assert _ident(seen["model"]) == _SONNET


async def test_the_recorded_thinking_level_pairs_with_the_restored_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#198's seed clamps against the harness model — the restored one, not the default.

    On 8f7d98aa the harness sat on the non-reasoning default, so the session's
    ``high`` was clamped to ``off``.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-3-5-haiku-latest")
    await _session(env, _HAIKU, thinking="high")
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _HAIKU
    assert seen["thinking"] == "high"


async def test_an_unrestorable_model_falls_back_with_pis_line(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """No anthropic credential: pi's ``Could not restore model … . Using …`` (sdk.ts:230,249)."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    _settings(env, defaultProvider="openrouter", defaultModel="anthropic/claude-sonnet-4.5")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, mode))

    err = capsys.readouterr().err
    line = (
        "Could not restore model anthropic/claude-haiku-4-5. "
        "Using openrouter/anthropic/claude-sonnet-4.5"
    )
    assert code == 0, err
    assert _ident(seen["model"]) == _OR_SONNET
    assert seen["fallback"] == line
    if mode == "tui":
        # The TUI shows it under the banner (run_tui); stderr is repainted away.
        assert line not in err
    else:
        assert err.splitlines().count(f"Warning: {line}") == 1, err


async def test_with_nothing_to_fall_back_to_print_sends_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No credential at all: the first half of pi's line, then the existing refusal."""

    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, "print"))

    err = capsys.readouterr().err
    assert code == 1
    assert "model" not in seen  # no turn
    assert "Warning: Could not restore model anthropic/claude-haiku-4-5" in err.splitlines()


@pytest.mark.parametrize("own_key", [True, False], ids=["own-key", "dotenv-key"])
async def test_an_uncatalogued_id_comes_back_only_on_the_users_own_key(
    env: Path, monkeypatch: pytest.MonkeyPatch, own_key: bool
) -> None:
    """Guard 2's condition: an id no catalogue lists reaches OpenRouter on the user's key only.

    The session ran ``openrouter/newlab/model-x`` (guard 2 sent it there). With
    the OpenRouter key from a cwd ``.env`` alone, it is not restored: the
    fallback (here the settings default on anthropic) runs instead.
    """

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    if own_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    else:
        path = env / "cwd" / ".env"
        path.write_text("OPENROUTER_API_KEY=or-dotenv-fake\n", encoding="utf-8")
        for name in ("OPENROUTER_API_KEY", "AELIX_DOTENV_ADMITTED"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(path))
    await _session(env, ("openrouter", "newlab/model-x"))
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    if own_key:
        assert _ident(seen["model"]) == ("openrouter", "newlab/model-x")
        assert seen["model"].api == "openai-completions"
    else:
        assert _ident(seen["model"]) == _SONNET
        assert seen["fallback"] == (
            "Could not restore model openrouter/newlab/model-x. Using anthropic/claude-sonnet-4-5"
        )


async def test_switch_session_restores_and_new_session_keeps_the_launch_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-session swaps build through the same factory: RPC ``switch_session``,
    the TUI's ``/resume``; ``/new`` is an empty session, so the launch inputs.
    The runtime's ``model_fallback_message`` follows each swap (the TUI reads it)."""

    from aelix_coding_agent import modes

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    target = await _session(env, _HAIKU)
    gone = await _session(env, ("openrouter", "anthropic/claude-sonnet-4.5"))  # no key for it
    seen: list[tuple[tuple[str, str], str | None]] = []

    async def _drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        def _note() -> None:
            seen.append(
                (_ident(runtime_host.harness.current_model), runtime_host.model_fallback_message)
            )

        _note()
        await runtime_host.switch_session(gone)
        _note()
        await runtime_host.switch_session(target)
        _note()
        await runtime_host.new_session()
        _note()

    monkeypatch.setattr(modes, "run_rpc_mode", _drive)
    code = await entry_mod._async_main(["--session-dir", _sessions(env), "--mode", "rpc"])

    assert code == 0
    assert seen == [
        (_SONNET, None),
        (
            _SONNET,
            "Could not restore model openrouter/anthropic/claude-sonnet-4.5. "
            "Using anthropic/claude-sonnet-4-5",
        ),
        (_HAIKU, None),
        (_SONNET, None),
    ]


async def test_a_record_without_a_conversation_is_not_restored(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pi restores only when the session has messages (``core/sdk.ts:210-211,224``)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    await _session(env, model_change=_HAIKU)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _SONNET


async def test_a_typed_api_key_keeps_the_launch_model(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--api-key`` is typed for the launch model; pi refuses it without ``--model``
    (``main.ts:827-834``), so it never meets a restore there. Here a settings default
    stands in for ``--model``, and the key stays with it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")  # so haiku COULD be restored
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, "rpc", "--api-key", "typed-fake"))

    assert code == 0
    assert _ident(seen["model"]) == _SONNET


async def test_a_rebuild_that_restores_is_not_held_for_the_launch_inputs(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0250 §2.11 (#367) holds a rebuild where the LAUNCH INPUTS land on a
    provider a ``session_start`` registered. A rebuild that restored the session's
    own model did not re-resolve them: neither the factory nor the
    after-``session_start`` seam may put it on the hold's placeholder.

    Here the settings default names ``sessext/m1``, which the extension registers
    in ``session_start``; the session ran on haiku. ``/reload`` rebuilds over a
    registry that now holds ``sessext``.
    """

    from aelix_coding_agent import modes

    from tests.cli.test_launch_route_344 import _SESSION_START_EXTENSION

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="sessext", defaultModel="m1")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    await _session(env, _HAIKU)
    seen: list[tuple[tuple[str, str], str | None]] = []

    async def _drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        h = runtime_host.harness
        seen.append((_ident(h.current_model), h.turns_held))
        await runtime_host.reload()
        h = runtime_host.harness
        seen.append((_ident(h.current_model), h.turns_held))

    monkeypatch.setattr(modes, "run_rpc_mode", _drive)
    code = await entry_mod._async_main(_argv(env, "rpc", "-e", str(ext)))

    assert code == 0
    assert seen == [(_HAIKU, None), (_HAIKU, None)]
