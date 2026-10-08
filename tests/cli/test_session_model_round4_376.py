"""#376 review round 4 — the prompt check asks no credential; ``--resume`` restores.

* **Request-level auth runs as on ``main``** (owner decision 2026-10-08). Round
  3's check also refused a model whose provider ``has_configured_auth`` did not
  count, and that predicate does not see auth the adapters accept without an
  API key: an auth header in ``models.json`` (or a registration's ``headers``),
  ``ANTHROPIC_CUSTOM_HEADERS`` on anthropic (ADR-0254 §2.2),
  ``ANTHROPIC_AUTH_TOKEN``, google-vertex Application Default Credentials. All
  four ran on ``main`` in the TUI and RPC and were refused there (``No API key
  found for <provider>.``, nothing sent — verify r3, ``.omc/probes/376-live/
  r3verify/``). Each row here goes through the real ``_async_main`` and RPC's
  ``_handle_prompt``: the prompt is accepted and its request leaves for a local
  :class:`~tests.route_wire.WireRecorder` (a POST to the ``baseUrl``, or a
  ``CONNECT`` through ``HTTPS_PROXY`` for Vertex — nothing leaves the machine).
  The TUI asks the same harness question (``set_prompt_check``), so the row's
  ``check(model) is None`` is its half too.
* **``--resume <id>`` restores** (Codex r3 cat4: the mutant ``if not
  _user_named_a_model() and not parsed.resume:`` passed every changed test; a
  resumed haiku session came up on the settings default).

Hermetic: fake credentials, an isolated agent dir and home, loopback only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import RpcCommandPrompt, RpcSuccessResponse

from tests.cli import test_session_model_record_376 as _record
from tests.cli.test_session_model_restore_376 import (
    _HAIKU,
    _ident,
    _session,
    _sessions,
    _settings,
    _stub_modes,
)
from tests.route_wire import WireRecorder

env = _record.env  # the sandbox fixture: no key, an isolated agent dir and home


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    recorder = WireRecorder("r4")
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, recorder.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    yield recorder
    recorder.close()


def _models_json(env: Path, providers: dict[str, Any]) -> None:
    (env / "agent" / "models.json").write_text(
        json.dumps({"providers": providers}), encoding="utf-8"
    )


def _headers_gateway(env: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    _models_json(
        env,
        {
            "mygw": {
                "baseUrl": url,
                "api": "anthropic-messages",
                "headers": {"x-api-key": "fake-gw-header"},
                "models": [
                    {
                        "id": "gw-1",
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    }
                ],
            }
        },
    )
    return ["--provider", "mygw", "--model", "gw-1"]


def _custom_headers(env: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    _models_json(env, {"anthropic": {"baseUrl": url}})
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: fake-custom-header")
    return ["--provider", "anthropic", "--model", "claude-haiku-4-5"]


def _auth_token(env: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    _models_json(env, {"anthropic": {"baseUrl": url}})
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake-auth-token")
    return ["--provider", "anthropic", "--model", "claude-haiku-4-5"]


def _vertex_adc(env: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    adc = env / "adc.json"
    adc.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "fake",
                "client_secret": "fake",
                "refresh_token": "fake",
                "token": "fake-adc-access-token",
                "expiry": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    for name in ("GOOGLE_CLOUD_API_KEY", "GCLOUD_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fake-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(adc))
    return ["--provider", "google-vertex", "--model", "gemini-2.5-flash"]


_SHAPES = {
    "headers-gateway": (_headers_gateway, ("post", "gw-1")),
    "custom-headers": (_custom_headers, ("post", "claude-haiku-4-5")),
    "auth-token": (_auth_token, ("post", "claude-haiku-4-5")),
    "vertex-adc": (_vertex_adc, ("connect", "us-central1-aiplatform.googleapis.com")),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
async def test_request_level_auth_is_not_refused_by_the_prompt_check(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: WireRecorder, shape: str
) -> None:
    """Round 3 (``4df1b2bf``): RPC ``success: false``, ``No API key found for
    <provider>.``, nothing sent — for all four; ``main`` sent each one."""

    setup, (kind, where) = _SHAPES[shape]
    argv = setup(env, monkeypatch, wire.url)
    _settings(env, retry={"enabled": False})
    seen: dict[str, Any] = {}

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen["check"] = live._prompt_check(live.current_model)
        seen["response"] = await _handle_prompt(live, RpcCommandPrompt(message="hi", id="p1"))
        await asyncio.wait_for(
            asyncio.gather(*list(live._pending_tasks), return_exceptions=True), 30
        )
        seen["last"] = live.state.messages[-1] if live.state.messages else None

    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    assert await entry_mod._async_main([*argv, "--no-session", "--mode", "rpc"]) == 0

    assert seen["check"] is None
    assert isinstance(seen["response"], RpcSuccessResponse), seen["response"]
    failure = str(getattr(seen["last"], "error_message", "") or "")
    assert "No API key" not in failure, failure
    if kind == "post":
        assert [model for _path, model in wire.posts] == [where], (wire.posts, failure)
    else:
        assert wire.hosts() == [where], (wire.connects, failure)


async def test_a_resume_without_flags_comes_up_on_the_sessions_model(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex r3 cat4: ``if not _user_named_a_model() and not parsed.resume:``
    passed every changed test file; under it ``--resume`` of a haiku session
    came up on the settings default (sonnet)."""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-fake")
    _settings(env, defaultProvider="anthropic", defaultModel="claude-sonnet-4-5")
    path = await _session(env, _HAIKU)
    with open(path, encoding="utf-8") as handle:
        session_id = json.loads(handle.readline())["id"]
    seen = _stub_modes(monkeypatch)

    code = await entry_mod._async_main(
        ["--resume", session_id, "--session-dir", _sessions(env), "--mode", "rpc"]
    )

    err = capsys.readouterr().err
    assert code == 0, err
    assert _ident(seen["model"]) == _HAIKU, err
    assert seen["fallback"] is None
    assert seen["runtime"].harness.session.session_file == path  # the session itself
