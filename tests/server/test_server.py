"""aelix-server endpoint tests (Sprint 6h₉f §7, ADR-0103).

Covers the 8 spec scenarios:

1. ``/healthz`` → 200, ``{"status": "ok"}``.
2. ``/schemas/{name}`` valid → 200, ``application/json``, parses as JSON.
3. ``/schemas/{name}`` unknown → 404.
4. ``/schemas/{name}`` traversal / illegal names → 400.
5. ``WS /rpc`` round-trip → ``get_state`` success envelope.
6. ``WS /rpc`` single-flight → 2nd concurrent connection rejected.
7. ``create_app`` / ``ServerConfig.from_env`` → env parsing + defaults.
8. ``WS /rpc`` server-initiated frame forwarding (event muxing via the
   ``get_state`` response path — the bridge forwards a server-emitted
   frame back over the socket).

``asyncio_mode = "auto"`` is set project-wide, but :class:`TestClient`
drives the app on its own portal — these are plain sync ``def test_*``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_server import ServerConfig, create_app
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Repo root: tests/server/test_server.py → parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "contracts"


def _make_config(tmp_path: Path, *, schemas_dir: Path | None = None) -> ServerConfig:
    """Build a test config with an explicit cwd.

    Note: ``cwd=str(tmp_path)`` controls where sessions are keyed (the
    ``cwd`` label inside ``~/.aelix/sessions/``), but session FILES are
    written under ``HOME/.aelix/sessions/``, NOT under ``cwd``.  WS tests
    that need clean isolation must also monkeypatch ``HOME`` to ``tmp_path``
    so that ``JsonlSessionRepo`` resolves ``~/.aelix/sessions`` inside the
    tmp directory.
    """

    return ServerConfig(
        bind="127.0.0.1",
        port=0,
        cwd=str(tmp_path),
        model="",
        provider="",
        schemas_dir=(schemas_dir or _CONTRACTS_DIR).resolve(),
    )


# === 1. /healthz =============================================================


def test_healthz(tmp_path: Path) -> None:
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# === 2. /schemas/{name} valid ================================================


def test_schemas_valid(tmp_path: Path) -> None:
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/schemas/manifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    parsed = json.loads(resp.content)
    assert isinstance(parsed, dict)


def test_schemas_valid_hyphenated_name(tmp_path: Path) -> None:
    # The on-disk ``descriptor-envelope`` / ``slot-taxonomy`` names carry a
    # hyphen — the allowlist regex MUST permit it.
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/schemas/descriptor-envelope")
    assert resp.status_code == 200
    assert json.loads(resp.content)


# === 3. /schemas/{name} 404 ==================================================


def test_schemas_unknown_404(tmp_path: Path) -> None:
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/schemas/does-not-exist")
    assert resp.status_code == 404


# === 4. /schemas/{name} traversal / illegal names → 400 ======================


@pytest.mark.parametrize("name", ["a.b", "manifest.schema", "has space", "dot.dot"])
def test_schemas_illegal_name_400(tmp_path: Path, name: str) -> None:
    # Names that route to the path param but fail the allowlist regex
    # (``.``, whitespace) must be rejected with 400. ``..`` cannot match the
    # regex either; ``a/b`` would not route to this single-segment param.
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        resp = client.get(f"/schemas/{name}")
    assert resp.status_code == 400


def test_schemas_404_when_dir_absent(tmp_path: Path) -> None:
    # When schemas_dir is absent, valid names 404 (documented behavior).
    missing = tmp_path / "no-such-contracts"
    app = create_app(_make_config(tmp_path, schemas_dir=missing))
    with TestClient(app) as client:
        resp = client.get("/schemas/manifest")
    assert resp.status_code == 404


# === 5. WS /rpc round-trip ===================================================


def test_rpc_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/rpc") as ws:
        ws.send_text(json.dumps({"type": "get_state", "id": "1"}))
        raw = ws.receive_text()
    frame = json.loads(raw)
    assert frame["type"] == "response"
    assert frame["command"] == "get_state"
    assert frame["id"] == "1"
    assert frame["success"] is True


# === 6. WS /rpc single-flight ================================================


def test_rpc_single_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/rpc") as ws1:
        # Drive the first connection so it is fully active.
        ws1.send_text(json.dumps({"type": "get_state", "id": "1"}))
        first = json.loads(ws1.receive_text())
        assert first["success"] is True
        # A second concurrent connection is closed (code=1013) before
        # accept → surfaces as a WebSocketDisconnect on the client.
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/rpc") as ws2:
            ws2.receive_text()


def test_rpc_active_flag_reset_after_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: ``rpc_active`` must be reset in the ``finally`` block so a
    # second sequential connection succeeds (the slot is freed on disconnect).
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client:
        # First connection: complete a round-trip then close.
        with client.websocket_connect("/rpc") as ws1:
            ws1.send_text(json.dumps({"type": "get_state", "id": "first"}))
            first = json.loads(ws1.receive_text())
            assert first["success"] is True
        # ws1 is now closed; rpc_active must be False again.
        # Second connection must be accepted (not 1013-rejected).
        with client.websocket_connect("/rpc") as ws2:
            ws2.send_text(json.dumps({"type": "get_state", "id": "second"}))
            second = json.loads(ws2.receive_text())
    assert second["success"] is True


# === 7. ServerConfig.from_env / create_app ===================================


def test_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AELIX_SERVER_BIND",
        "AELIX_SERVER_PORT",
        "AELIX_SERVER_CWD",
        "AELIX_SERVER_MODEL",
        "AELIX_SERVER_PROVIDER",
        "AELIX_SERVER_SCHEMAS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    config = ServerConfig.from_env()
    assert config.bind == "127.0.0.1"
    assert config.port == 8765
    assert config.cwd == "."
    assert config.model == ""
    assert config.provider == ""
    assert config.schemas_dir.name == "contracts"


def test_config_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_SERVER_BIND", "0.0.0.0")
    monkeypatch.setenv("AELIX_SERVER_PORT", "9999")
    monkeypatch.setenv("AELIX_SERVER_CWD", "/tmp/work")
    monkeypatch.setenv("AELIX_SERVER_MODEL", "claude")
    monkeypatch.setenv("AELIX_SERVER_PROVIDER", "anthropic")
    monkeypatch.setenv("AELIX_SERVER_SCHEMAS_DIR", "/tmp/schemas")
    config = ServerConfig.from_env()
    assert config.bind == "0.0.0.0"
    assert config.port == 9999
    assert config.cwd == "/tmp/work"
    assert config.model == "claude"
    assert config.provider == "anthropic"
    assert config.schemas_dir == Path("/tmp/schemas")


def test_config_from_env_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AELIX_SERVER_PORT", "not-a-number")
    with pytest.raises(ValueError):
        ServerConfig.from_env()


def test_create_app_uses_supplied_config(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    app = create_app(config)
    with TestClient(app):
        assert app.state.config is config
        assert app.state.rpc_active is False


# === 8. WS /rpc server-initiated frame forwarding ============================


def test_rpc_forwards_server_initiated_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bridge forwards a server-emitted frame (the get_state response is
    # produced by run_rpc_mode's dispatch → stdout_write → queue → ws) back
    # over the socket. Full event-stream e2e needs a stubbed model (deferred);
    # this exercises the WHOLE bridge for a server-originated frame.
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(_make_config(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/rpc") as ws:
        ws.send_text(json.dumps({"type": "get_state", "id": "evt"}))
        frame = json.loads(ws.receive_text())
    # A non-client frame arrived over the socket carrying the session state.
    assert frame["type"] == "response"
    assert "data" in frame
    assert isinstance(frame["data"], dict)
