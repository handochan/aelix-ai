"""#376 review round 5 — the prompt check asks no Vertex auth either.

Round 4 took the credential arm out of the prompt check (owner decision
2026-10-08), but ``turn_refusal`` still answered ``None`` only when
:func:`is_runnable` did, and for ``google-vertex`` that predicate asks
``_vertex_config_missing`` — a GCP-auth question read from the environment
alone (``GOOGLE_CLOUD_API_KEY``, or a project and a location). Over RPC, the
one mode where ``main`` has no such gate, round 4 (``619f7524``) therefore
refused, before writing, what ``main`` handled at request time (verify r4,
``.omc/probes/376-live/r4verify/``):

* a keyless Vertex prompt — ``main`` accepts and writes it, and the adapter
  fails with ``Vertex AI requires a project ID``;
* a Vertex key from ``--api-key``, ``auth.json`` or a ``models.json``
  ``apiKey`` with no ``GOOGLE_CLOUD_*`` set — ``main`` sends the request
  (``CONNECT aiplatform.googleapis.com:443``).

The check now judges only provider, adapter and base URL. Each row goes
through the real ``_async_main`` and RPC's ``_handle_prompt``; every request
leaves for a local :class:`~tests.route_wire.WireRecorder` through
``HTTPS_PROXY`` (nothing leaves the machine). :func:`is_runnable` itself — the
TUI #189 gate, the ``-p`` #98 gate, the restore predicate — is unchanged, and
the rows say so.

Hermetic: fake credentials, an isolated agent dir and home, loopback only.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.streaming import Model
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.core import runnable_models
from aelix_coding_agent.core.runnable_models import is_runnable, turn_refusal
from aelix_coding_agent.rpc.rpc_mode import _handle_prompt
from aelix_coding_agent.rpc.rpc_types import RpcCommandPrompt, RpcSuccessResponse

from tests.cli import test_session_model_record_376 as _record
from tests.cli.test_session_model_restore_376 import _settings
from tests.route_wire import WireRecorder

env = _record.env  # the sandbox fixture: no key, an isolated agent dir and home

_VERTEX = ["--provider", "google-vertex", "--model", "gemini-2.5-flash"]
_GCP_ENV = (
    "GOOGLE_CLOUD_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    recorder = WireRecorder("r5")
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, recorder.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    yield recorder
    recorder.close()


@pytest.fixture
def no_gcp(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GCP_ENV:
        monkeypatch.delenv(name, raising=False)


def _keyless(env: Path) -> list[str]:
    return list(_VERTEX)


def _api_key_flag(env: Path) -> list[str]:
    return [*_VERTEX, "--api-key", "fake-vertex-flag-key"]


def _auth_json(env: Path) -> list[str]:
    path = env / "agent" / "auth.json"
    path.write_text(
        json.dumps({"google-vertex": {"type": "api_key", "key": "fake-vertex-stored-key"}}),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return list(_VERTEX)


def _models_json_key(env: Path) -> list[str]:
    (env / "agent" / "models.json").write_text(
        json.dumps({"providers": {"google-vertex": {"apiKey": "fake-vertex-models-key"}}}),
        encoding="utf-8",
    )
    return list(_VERTEX)


# shape -> (setup, hosts the request reached, the turn's error contains)
_SHAPES: dict[str, tuple[Any, list[str], str | None]] = {
    "keyless": (_keyless, [], "Vertex AI requires a project ID"),
    "api-key-flag": (_api_key_flag, ["aiplatform.googleapis.com"], None),
    "auth-json": (_auth_json, ["aiplatform.googleapis.com"], None),
    "models-json-apikey": (_models_json_key, ["aiplatform.googleapis.com"], None),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
async def test_a_vertex_prompt_over_rpc_is_handled_as_on_main(
    env: Path, monkeypatch: pytest.MonkeyPatch, wire: WireRecorder, no_gcp: None, shape: str
) -> None:
    """Round 4 (``619f7524``): ``success: false``, "needs Google Cloud
    configuration", nothing written or sent, for all four. ``main``: accepted
    and written; keyless fails at request time with the adapter's own error,
    a key from any of the three sources is sent."""

    setup, hosts, error = _SHAPES[shape]
    argv = setup(env)
    _settings(env, retry={"enabled": False})
    seen: dict[str, Any] = {}

    async def drive(harness: Any, *, runtime_host: Any, harness_factory: Any) -> None:
        live = runtime_host.harness
        seen["model"] = live.current_model
        seen["check"] = live._prompt_check(live.current_model)
        seen["response"] = await _handle_prompt(live, RpcCommandPrompt(message="vx hi", id="p1"))
        await asyncio.wait_for(
            asyncio.gather(*list(live._pending_tasks), return_exceptions=True), 30
        )
        seen["messages"] = list(live.state.messages)

    from aelix_coding_agent import modes

    monkeypatch.setattr(modes, "run_rpc_mode", drive)
    assert await entry_mod._async_main([*argv, "--no-session", "--mode", "rpc"]) == 0

    assert seen["model"].api == "google-vertex"
    # is_runnable keeps its Vertex arm (the TUI and -p gates); only the prompt
    # check leaves it out.
    assert is_runnable(seen["model"]) is False
    assert seen["check"] is None
    assert isinstance(seen["response"], RpcSuccessResponse), seen["response"]
    roles = [getattr(m, "role", None) for m in seen["messages"]]
    assert roles[:1] == ["user"], roles  # written, as on main
    failure = str(getattr(seen["messages"][-1], "error_message", "") or "")
    assert "Google Cloud configuration" not in failure, failure
    if error is not None:
        assert error in failure, failure
    assert wire.hosts() == hosts, (wire.connects, failure)


def test_the_check_judges_provider_adapter_and_base_url_only(
    monkeypatch: pytest.MonkeyPatch, no_gcp: None
) -> None:
    """The arms of ``is_runnable`` the check keeps, and the one it leaves out."""

    from aelix_ai.models import get_model
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    for name in list(os.environ):
        if name.startswith("CLOUDFLARE_"):
            monkeypatch.delenv(name)
    register_providers()
    vertex = get_model("google-vertex", "gemini-2.5-flash")
    workers = get_model("cloudflare-workers-ai", "@cf/meta/llama-4-scout-17b-16e-instruct")
    assert vertex is not None and workers is not None

    # Vertex auth (env only): is_runnable refuses, the check does not.
    assert is_runnable(vertex) is False
    assert turn_refusal(vertex) is None
    # No provider: the placeholder.
    assert str(turn_refusal(Model(id="", provider=""))).startswith("No model selected.")
    # No adapter (api 'unknown').
    assert "model-x" in str(turn_refusal(Model(id="model-x", provider="newlab")))
    # No base URL: an unexpanded {CLOUDFLARE_ACCOUNT_ID}, and a declared-empty one.
    assert "CLOUDFLARE_ACCOUNT_ID" in str(turn_refusal(workers))
    hostless = Model(id="corp-x", provider="mycorp", api="anthropic-messages", base_url="")
    assert "declares no base URL" in str(turn_refusal(hostless))

    # A Vertex model with no adapter registered is refused, and named for that,
    # not for its Google Cloud configuration.
    monkeypatch.setattr(runnable_models, "supported_apis", lambda: {"anthropic-messages"})
    said = str(turn_refusal(vertex))
    assert "which this build has no adapter for" in said, said
    assert "Google Cloud configuration" not in said
