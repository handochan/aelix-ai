"""#376 review round 2 — what the session records, what a prompt may write, and the restore's edges.

Drives the real ``_async_main`` (the mode entry replaced by a driver; no turn
reaches a provider) over sessions a real ``JsonlSessionRepo`` wrote, with the
fixtures of ``test_session_model_restore_376.py``:

* an IDLE ``set_model`` (the ``/model`` picker, ``/model <id>``) is recorded, so
  ``/model X`` then quit then ``--continue`` comes back on X, and ``/reload`` and
  ``/clone`` after the pick keep it (pi's ``setModel`` appends ``model_change``,
  ``agent-session.ts:2485`` at ``pi@1cedd3272``); the late-provider hold's
  placeholder (#367) is never recorded;
* a prompt on a model with no credential runs as on ``main`` (review round 4,
  owner decision 2026-10-08: no credential refusal): RPC accepts and writes it
  and the turn fails ``No API key for provider``; ``-p`` and ``--mode json``
  keep ``main``'s launch gate (exit 1, nothing written); the fallback line's
  ``Using`` half names only a model that can run and has a credential (pi's
  ``findInitialModel`` falls back only to a model with auth);
* rows for the restore edges no test held (review round 1's surviving mutants):
  a ``models.json`` ``baseUrl`` and ``OPENROUTER_BASE_URL`` reach a restored
  model, an uncatalogued id on a user-defined provider comes back on any
  configured key and not without one, a catalogued model that cannot run is not
  restored and is not named in ``Using``, a lone ``--provider`` is a pick, and a
  restored Copilot model takes the registry's proxy-ep host.

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
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.runtime_bootstrap import (
    load_dotenv,
    register_providers,
    restore_session_route,
)
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import RpcCommandPrompt, RpcSuccessResponse

from tests.cli.test_session_model_restore_376 import (
    _HAIKU,
    _OR_SONNET,
    _SONNET,
    _argv,
    _ident,
    _session,
    _sessions,
    _settings,
    _stub_modes,
)

_EXT_SONNET = ("sessext", "m1")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``test_session_model_restore_376.py``'s sandbox: no key, an empty agent dir."""

    from tests.cli.test_launch_route_344 import _FakePipedStdin
    from tests.env_sandbox import sandbox_home

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


def _get(provider: str, model_id: str) -> Any:
    from aelix_ai.models import get_model

    model = get_model(provider, model_id)
    assert model is not None
    return model


async def _rpc(env: Path, monkeypatch: pytest.MonkeyPatch, drive: Any, *argv: str) -> int:
    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    return await entry_mod._async_main(
        list(argv) or ["--continue", "--session-dir", _sessions(env), "--mode", "rpc"]
    )


async def _pick(harness: Any, provider: str, model_id: str) -> None:
    """What ``tui/model_picker.py`` does on an idle pick: ``set_model`` + the default."""

    await harness.set_model(_get(provider, model_id))
    settings = harness.settings_manager
    if settings is not None:
        settings.set_default_model_and_provider(provider, model_id)
        await settings.flush()


def _lines(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# === 1. an idle pick is the session's record ==================================


async def test_an_idle_pick_then_quit_then_continue_comes_back_on_the_pick(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION of round 1: ``/model sonnet`` (idle, no prompt after), quit,
    ``aelix --continue`` came back on haiku — the last answer's model — while the
    parent came back on sonnet through the settings default the picker writes."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    path = await _session(env, _HAIKU)
    seen: list[tuple[str, str]] = []

    async def pick(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        seen.append(_ident(runtime_host.harness.current_model))
        await _pick(runtime_host.harness, *_SONNET)

    async def look(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        seen.append(_ident(runtime_host.harness.current_model))

    assert await _rpc(env, monkeypatch, pick) == 0
    assert await _rpc(env, monkeypatch, look) == 0

    assert seen == [_HAIKU, _SONNET]
    changes = [(e["provider"], e["modelId"]) for e in _lines(path) if e["type"] == "model_change"]
    assert changes == [_SONNET]


async def test_an_idle_pick_without_a_settings_default_comes_back_too(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record alone carries it: ``/model <id>`` writes no settings default."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-3-5-haiku-latest")
    await _session(env, _HAIKU)
    seen: list[tuple[str, str]] = []

    async def pick(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        await runtime_host.harness.set_model(_get(*_SONNET))

    async def look(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        seen.append(_ident(runtime_host.harness.current_model))

    assert await _rpc(env, monkeypatch, pick) == 0
    assert await _rpc(env, monkeypatch, look) == 0
    assert seen == [_SONNET]


@pytest.mark.parametrize("op", ["reload", "clone"])
async def test_reload_and_clone_after_an_idle_pick_keep_it(
    env: Path, monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    """Both rebuild from the session's record; on 42ce9646 both went back to haiku."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    await _session(env, _HAIKU)
    seen: list[tuple[str, str]] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        await _pick(runtime_host.harness, *_SONNET)
        if op == "reload":
            await runtime_host.reload()
        else:
            leaf = await runtime_host.session.get_leaf_id()
            await runtime_host.fork(leaf, position="at")
        seen.append(_ident(runtime_host.harness.current_model))

    assert await _rpc(env, monkeypatch, drive) == 0
    assert seen == [_SONNET]


async def test_the_late_provider_holds_placeholder_is_never_recorded(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0250 §2.11 (#367): the launch inputs land on a provider ``session_start``
    registers, so the session is put on ``Model('m1', 'sessext')`` (``api='unknown'``),
    at launch and again on ``/new``. Recording that would hand the next
    ``--continue`` the late provider the hold keeps the session off. A real pick
    afterwards is recorded."""

    from tests.cli.test_launch_route_344 import _SESSION_START_EXTENSION

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="sessext", defaultModel="m1")
    ext = env / "sessext.py"
    ext.write_text(_SESSION_START_EXTENSION, encoding="utf-8")
    seen: list[Any] = []

    async def _changes(runtime_host: Any) -> list[tuple[str, str]]:
        return [
            (e.provider, e.model_id)
            for e in await runtime_host.session.get_entries()
            if e.type == "model_change"
        ]

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        model = runtime_host.harness.current_model
        seen.append((_ident(model), model.api, await _changes(runtime_host)))
        await runtime_host.new_session()
        model = runtime_host.harness.current_model
        seen.append((_ident(model), model.api, await _changes(runtime_host)))
        await runtime_host.harness.set_model(_get(*_HAIKU))
        seen.append(await _changes(runtime_host))

    code = await _rpc(
        env,
        monkeypatch,
        drive,
        "--session-dir",
        _sessions(env),
        "--mode",
        "rpc",
        "-e",
        str(ext),
    )

    assert code == 0
    assert seen == [
        (_EXT_SONNET, "unknown", []),
        (_EXT_SONNET, "unknown", []),
        [_HAIKU],
    ]


# === 2. a keyless prompt runs as on main (review round 4) ====================


async def _keyless_continue(env: Path) -> str:
    """A haiku session reopened with no credential at all, settings on anthropic sonnet."""

    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    return await _session(env, _HAIKU)


async def test_rpc_accepts_a_keyless_prompt_as_main_does(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review round 4 (owner decision 2026-10-08): the round-3 commit refused
    it (``No API key found for Anthropic.``, nothing written), which also
    refused auth the adapters accept without a key. As on ``main``: accepted,
    written, and the turn fails with the adapter's own message at request time
    (no request leaves: the adapter has no key to send)."""

    path = await _keyless_continue(env)
    before = [e for e in _lines(path) if e.get("type") == "message"]
    responses: list[Any] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        import asyncio

        live = runtime_host.harness
        responses.append(await _handle_prompt(live, RpcCommandPrompt(message="hi", id="p1")))
        await asyncio.wait_for(
            asyncio.gather(*list(live._pending_tasks), return_exceptions=True), 10
        )
        responses.append(live.phase)

    assert await _rpc(env, monkeypatch, drive) == 0

    response, phase = responses
    assert isinstance(response, RpcSuccessResponse), response
    assert phase == "idle"
    after = [e for e in _lines(path) if e.get("type") == "message"]
    assert len(after) == len(before) + 2, after
    assert after[-2]["message"]["role"] == "user"
    last = after[-1]["message"]
    assert last["role"] == "assistant"
    failure = str(last.get("errorMessage") or last.get("error_message") or "")
    assert "No API key for provider: anthropic" in failure, last
    err = capsys.readouterr().err
    # The fallback has no credential either: pi's first half alone.
    assert "Warning: Could not restore model anthropic/claude-haiku-4-5" in err.splitlines(), err
    assert "Using" not in err


@pytest.mark.parametrize("mode", ["print", "json"])
async def test_print_and_json_keep_mains_launch_gate_for_a_keyless_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """``main``'s own print/json no-usable-model gate (``cli/entry.py``,
    ``has_configured_auth`` before any prompt), unchanged by #376: exit 1,
    ``No API key found for Anthropic.``, nothing written."""

    path = await _keyless_continue(env)
    before = _lines(path)
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(_argv(env, mode))

    err = capsys.readouterr().err
    assert code == 1
    assert "model" not in seen  # no turn
    assert "No API key found for Anthropic." in err
    assert "Warning: Could not restore model anthropic/claude-haiku-4-5" in err.splitlines(), err
    assert "Using" not in err
    assert _lines(path) == before


async def test_using_names_no_fallback_without_a_credential(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``Using`` half for a keyless fallback: absent (TUI and RPC read the runtime)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="openrouter", defaultModel="anthropic/claude-sonnet-4.5")
    await _session(env, ("openrouter", "openai/gpt-4o-mini"))  # no OpenRouter key
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _OR_SONNET
    assert seen["fallback"] == "Could not restore model openrouter/openai/gpt-4o-mini"


# === 4. the restore's edges (review round 1's surviving mutants) ==============


async def test_a_restored_model_keeps_the_models_json_base_url(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: a static catalogue lookup sent the restored model to api.anthropic.com
    instead of the user's ``baseUrl``."""

    (env / "agent" / "models.json").write_text(
        json.dumps({"providers": {"anthropic": {"baseUrl": "http://127.0.0.1:9/override"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _HAIKU
    assert seen["model"].base_url == "http://127.0.0.1:9/override"


def test_the_registry_less_restore_applies_openrouter_base_url(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2, at the function: ``restore_session_route`` re-points an OpenRouter
    record itself (``_openrouter_base``). Through the CLI the registry's copy is
    already re-pointed (``compose_built_in``), so the row below holds either
    way; with no registry this call is the only one that does it."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9/or/v1")

    route = restore_session_route(_OR_SONNET, None)

    assert route is not None
    assert route.model.base_url == "http://127.0.0.1:9/or/v1"


async def test_a_restored_openrouter_model_keeps_openrouter_base_url(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2 (#375): ``OPENROUTER_BASE_URL`` reaches a restored OpenRouter model."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://127.0.0.1:9/or/v1")
    await _session(env, _OR_SONNET)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _OR_SONNET
    assert seen["model"].base_url == "http://127.0.0.1:9/or/v1"


def _localbox(env: Path, *, keyed: bool) -> None:
    provider: dict[str, Any] = {
        "api": "anthropic-messages",
        "baseUrl": "http://127.0.0.1:9/localbox",
        "models": [{"id": "listed-model"}],
    }
    if keyed:
        provider["apiKey"] = "LOCALBOX_API_KEY"
    (env / "agent" / "models.json").write_text(
        json.dumps({"providers": {"localbox": provider}}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "key", ["shell", "dotenv", None], ids=["shell-key", "dotenv-key", "no-key"]
)
async def test_an_uncatalogued_id_on_a_user_defined_provider(
    env: Path, monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    """M3/M4: a ``models.json`` provider is the user's own route, so an id it does
    not list comes back as a custom id on any configured key (as ``--provider
    localbox --model unlisted-model`` would at launch), and never without one
    (a provider entry with no ``apiKey`` and no key in the environment). A key
    from the project ``.env`` counts here, unlike guard 2's OpenRouter case: the
    launch's ``--provider localbox`` takes it too (ADR-0239 decision 4)."""

    _localbox(env, keyed=key is not None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    if key == "shell":
        monkeypatch.setenv("LOCALBOX_API_KEY", "box-fake")
    elif key == "dotenv":
        path = env / "cwd" / ".env"
        path.write_text("LOCALBOX_API_KEY=box-dotenv-fake\n", encoding="utf-8")
        for name in ("LOCALBOX_API_KEY", "AELIX_DOTENV_ADMITTED"):
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
        load_dotenv(str(path))
    await _session(env, ("localbox", "unlisted-model"))
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    if key is not None:
        assert _ident(seen["model"]) == ("localbox", "unlisted-model")
        assert seen["model"].base_url == "http://127.0.0.1:9/localbox"
        assert seen["fallback"] is None
    else:
        assert _ident(seen["model"]) == _SONNET
        assert seen["fallback"] == (
            "Could not restore model localbox/unlisted-model. Using anthropic/claude-sonnet-4-5"
        )


_CF = ("cloudflare-ai-gateway", "claude-3-5-haiku")


async def test_a_catalogued_model_that_cannot_run_is_not_restored(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5: a Cloudflare gateway model with its key but no account/gateway id (its
    base URL keeps ``{CLOUDFLARE_ACCOUNT_ID}``) cannot run: the fallback runs."""

    monkeypatch.setenv("CLOUDFLARE_API_KEY", "cf-fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    await _session(env, _CF)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _SONNET
    assert seen["fallback"] == (
        "Could not restore model cloudflare-ai-gateway/claude-3-5-haiku. "
        "Using anthropic/claude-sonnet-4-5"
    )


async def test_using_names_no_fallback_that_cannot_run(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M7: the fallback (the settings default) is the unrunnable Cloudflare model,
    keyed: the line stops at its first half."""

    monkeypatch.setenv("CLOUDFLARE_API_KEY", "cf-fake")
    _settings(env, defaultProvider=_CF[0], defaultModel=_CF[1])
    await _session(env, _HAIKU)  # no anthropic key
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == _CF
    assert seen["fallback"] == "Could not restore model anthropic/claude-haiku-4-5"


async def test_a_lone_provider_flag_is_a_pick(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M9: ``--provider`` without ``--model`` names the run's model too. It is let
    through only as ADR-0250 §2.6's ``OPENROUTER_DEFAULT_MODEL`` exemption, which
    picks that model; the session's haiku is not restored over it."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fake")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "openai/gpt-4o-mini")
    await _session(env, _HAIKU)
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc", "--provider", "openrouter")) == 0
    assert _ident(seen["model"]) == ("openrouter", "openai/gpt-4o-mini")
    assert seen["fallback"] is None


async def test_a_restored_copilot_model_takes_the_proxy_ep_host(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M15: a Business/Enterprise seat's host comes from the token's ``proxy-ep``,
    which only the registry's copy carries (``enrich_copilot_base_url``)."""

    from aelix_ai.oauth import AuthStorage
    from aelix_ai.oauth.types import OAuthCredentials

    storage = AuthStorage(path=env / "agent" / "auth.json")
    await storage.load()
    await storage.set_oauth(
        "github-copilot",
        OAuthCredentials(
            refresh="ghr-fake",
            access="tid=fake;exp=9999999999;proxy-ep=proxy.enterprise.example.com;",
            expires=10**13,
            extra={},
        ),
    )
    await _session(env, ("github-copilot", "claude-haiku-4.5"))
    seen = _stub_modes(monkeypatch)

    assert await entry_mod._async_main(_argv(env, "rpc")) == 0
    assert _ident(seen["model"]) == ("github-copilot", "claude-haiku-4.5")
    assert seen["model"].base_url == "https://api.enterprise.example.com"


# === 3c. RPC says why a swap is not on the session's model ====================


async def test_rpc_session_swaps_print_the_fallback_line_on_stderr(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """At startup RPC says it on stderr; ``switch_session``, ``fork`` and ``clone``
    rebuild through the same factory, and on 42ce9646 fell back silently. A swap
    that restores says nothing."""

    from aelix_coding_agent.rpc.rpc_mode import (
        _handle_clone,
        _handle_fork,
        _handle_switch_session,
    )
    from aelix_coding_agent.rpc.rpc_types import (
        RpcCommandClone,
        RpcCommandFork,
        RpcCommandSwitchSession,
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    target = await _session(env, _HAIKU)
    # Two exchanges, so a fork before the last prompt keeps a conversation (a
    # fork before the only one is an empty session: the launch inputs, no line).
    gone = await _session(env, _OR_SONNET, _OR_SONNET)  # no OpenRouter key
    line = (
        "Warning: Could not restore model openrouter/anthropic/claude-sonnet-4.5. "
        "Using anthropic/claude-sonnet-4-5"
    )
    said: list[int] = []

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        def _count() -> None:
            said.append(capsys.readouterr().err.splitlines().count(line))

        _count()
        await _handle_switch_session(runtime_host, RpcCommandSwitchSession(session_path=gone))
        _count()
        entries = await runtime_host.session.get_entries()
        user = [e.id for e in entries if e.type == "message" and e.message.role == "user"][-1]
        await _handle_fork(runtime_host, RpcCommandFork(entry_id=user))
        _count()
        await _handle_switch_session(runtime_host, RpcCommandSwitchSession(session_path=gone))
        await _handle_clone(runtime_host, RpcCommandClone())
        _count()
        await _handle_switch_session(runtime_host, RpcCommandSwitchSession(session_path=target))
        _count()

    code = await _rpc(env, monkeypatch, drive, "--session-dir", _sessions(env), "--mode", "rpc")

    assert code == 0
    # startup (a new session): nothing; switch: once; fork of it: once; switch +
    # clone: twice; switch to the haiku session (restored): nothing.
    assert said == [0, 1, 1, 2, 0]
