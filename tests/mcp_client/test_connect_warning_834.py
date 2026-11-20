"""Connection diagnostics at the transport and launch boundaries (#834)."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import subprocess
import sys
from collections.abc import Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Literal
from urllib.parse import urlsplit

import httpx
import pytest
from aelix_agent_core.contracts import McpServerContrib
from aelix_coding_agent.mcp import McpClientManager, McpConnectionError, McpServerConnection
from aelix_coding_agent.rpc.control_mcp import RpcMcpController

from tests.env_sandbox import child_env
from tests.rpc.control_v1.test_real_child import ChildWire

_SERVER_TEXT = "server says \x1b[31mRED\x1b[0m\nSECOND LINE " + "B" * 240
_STDIO_SCRIPT = """
import json
import sys

request = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                  "error": {"code": -32603, "message": sys.argv[1]}}), flush=True)
for _ in sys.stdin:
    pass
"""
_DISCOVERY_SCRIPT = """
import json
import sys
from pathlib import Path

mode, detail = sys.argv[1:]
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if request["method"] == "initialize":
        reply["result"] = {
            "protocolVersion": detail if mode == "version" else request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "inventory", "version": "1"},
        }
    elif mode == "reject":
        reply["error"] = {"code": -32603, "message": detail}
    else:
        if mode == "record":
            Path(detail).write_text(request["method"], encoding="utf-8")
        reply["result"] = {"tools": [{"name": "inventory", "inputSchema": {"type": "object"}}]}
    print(json.dumps(reply), flush=True)
"""

_METADATA_MARKER = "private-initialize-metadata"


class _Server(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/headers-http", "/headers-sse"}:
            self.received.put((path, self.headers.get("X-MCP-Fixture")))
        self.send_error(499 if path == "/nonstandard-status" else 401, "FAKE-reason")

    received: Queue[tuple[str, str | None]] = Queue()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/jsonrpc", "/metadata"}:
            self.do_GET()
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                **(
                    {"result": {"serverInfo": {"name": _METADATA_MARKER, "version": "1"}}}
                    if path == "/metadata"
                    else {"error": {"code": -32603, "message": _SERVER_TEXT}}
                ),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def local_http() -> Iterator[str]:
    with ThreadingHTTPServer(("127.0.0.1", 0), _Server) as httpd:
        worker = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            worker.join()


@pytest.fixture(scope="module")
def launch_stderr(tmp_path_factory: pytest.TempPathFactory, local_http: str) -> bytes:
    with socket.socket() as refused:
        refused.bind(("127.0.0.1", 0))
        refused_port = refused.getsockname()[1]
    stderr = _launch(
        tmp_path_factory.mktemp("mcp-launch"),
        {
            "unauth-http": {"url": f"{local_http}/auth?token=FAKE"},
            "unauth-sse": {"url": f"{local_http}/auth?token=FAKE", "transport": "sse"},
            "nonstandard": {"url": f"{local_http}/nonstandard-status?token=FAKE"},
            "refused": {"url": f"http://127.0.0.1:{refused_port}/?token=FAKE"},
            "badport": {"url": "http://127.0.0.1:99999/?token=FAKE"},
            "badchar": {"url": f"{local_http}/bad path?token=FAKE"},
            "badscheme": {"url": "ftp://127.0.0.1/?token=FAKE"},
            "remote-http": {"url": f"{local_http}/jsonrpc"},
            "remote-stdio": {
                "command": sys.executable,
                "args": ["-c", _STDIO_SCRIPT, _SERVER_TEXT],
            },
            "metadata": {"url": f"{local_http}/metadata"},
            "legacy": _stdio_spec("version", "unsupported-version-from-peer"),
        },
    )
    assert b"No model selected." in stderr
    return stderr


def _stdio_spec(mode: str, detail: str = "") -> dict[str, object]:
    return {"command": sys.executable, "args": ["-c", _DISCOVERY_SCRIPT, mode, detail]}


def _launch(tmp_path: Path, servers: dict[str, object]) -> bytes:
    agent = tmp_path / "agent"
    agent.mkdir()

    (agent / "mcp.json").write_text(
        json.dumps({"mcpServers": servers}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", "--offline", "--no-session", "-p", "hi"],
        cwd=tmp_path,
        env=child_env(
            tmp_path / "home", AELIX_CODING_AGENT_DIR=str(agent), PYTHONIOENCODING="utf-8"
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    return result.stderr


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        (b"unauth-http", b"HTTP 401 Unauthorized"),
        (b"unauth-sse", b"HTTP 401 Unauthorized"),
        (b"nonstandard", b"connect failed: HTTP 499"),
        (b"refused", b"connection refused (ConnectError)"),
        (b"badport", b"invalid URL"),
        (b"badchar", b"invalid URL"),
        (b"badscheme", b"invalid URL"),
    ],
)
def test_launch_names_remote_failure_kind(launch_stderr: bytes, name: bytes, reason: bytes) -> None:
    (warning,) = [
        line
        for line in launch_stderr.splitlines()
        if line.startswith(b"Warning: MCP") and b"'" + name + b"'" in line
    ]
    assert reason in warning, warning
    assert b"FAKE" not in warning


@pytest.mark.parametrize("name", [b"remote-http", b"remote-stdio"])
def test_launch_bounds_and_sanitizes_server_text(launch_stderr: bytes, name: bytes) -> None:
    (warning,) = [
        line
        for line in launch_stderr.splitlines()
        if line.startswith(b"Warning: MCP") and b"'" + name + b"'" in line
    ]
    assert b"\x1b" not in warning
    assert b"SECOND LINE" in warning
    _, _, reason = warning.decode(encoding="utf-8", errors="replace").partition("connect failed: ")
    assert len(reason) <= 201
    assert reason.endswith("…")


def test_launch_does_not_log_sdk_initialize_payload(launch_stderr: bytes) -> None:
    assert _METADATA_MARKER.encode() not in launch_stderr
    assert b"Raw result:" not in launch_stderr
    assert b"Failed to parse initialization response" not in launch_stderr
    assert b"MCP server 'metadata' (http) connect failed: ValidationError" in launch_stderr


def test_launch_preserves_fixed_protocol_version_diagnosis(launch_stderr: bytes) -> None:
    assert b"MCP server 'legacy' (stdio) connect failed: unsupported MCP protocol version" in (
        launch_stderr
    )
    assert b"unsupported-version-from-peer" not in launch_stderr


def test_cli_startup_continues_after_discovery_rejection(tmp_path: Path) -> None:
    marker = tmp_path / "healthy-tools-listed"
    stderr = _launch(
        tmp_path,
        {
            "inventory": _stdio_spec("reject", _SERVER_TEXT),
            "healthy": _stdio_spec("record", str(marker)),
        },
    )
    assert b"No model selected." in stderr
    assert b"Traceback" not in stderr
    assert b"\x1b" not in stderr
    (warning,) = [line for line in stderr.splitlines() if line.startswith(b"Warning: MCP")]
    prefix = b"Warning: MCP server failed: MCP server 'inventory' (stdio) tools/list failed: "
    assert warning.startswith(prefix)
    assert b"SECOND LINE" in warning
    excerpt = warning.removeprefix(prefix).decode("utf-8")
    assert len(excerpt) == 201 and excerpt.endswith("…")
    assert marker.read_text(encoding="utf-8") == "tools/list"


async def test_rpc_discovery_failure_is_safe_and_preserves_existing_tools(tmp_path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "aelix_coding_agent",
        "--offline",
        "--no-session",
        "--mode",
        "rpc",
        "--provider",
        "openrouter",
        "--model",
        "openai/gpt-4o-mini",
        cwd=tmp_path,
        env=child_env(
            tmp_path / "home",
            AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
            OPENROUTER_API_KEY="local-fixture-only",
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    wire = ChildWire(proc)
    try:
        for mode in ("healthy", "reject"):
            wire.send(
                {
                    "type": "aelix_control",
                    "id": mode,
                    "method": "mcp/configure",
                    "params": {
                        "servers": [
                            {"name": mode, "type": "stdio", **_stdio_spec(mode, _SERVER_TEXT)}
                        ]
                    },
                }
            )
            reply = await asyncio.wait_for(wire.response(mode), 30)
            if mode == "healthy":
                assert reply["success"]
        rejected = reply
        assert rejected["success"] is False
        assert (
            rejected["error"] == "MCP server(s) failed to connect: 'reject' (tools/list: McpError)"
        )
        wire.send({"type": "aelix_control", "id": "list", "method": "resources/list", "params": {}})
        listed = await asyncio.wait_for(wire.response("list"), 30)
        assert "acp_healthy__inventory" in {tool["name"] for tool in listed["data"]["tools"]}
    finally:
        wire.send(eof=True)
        try:
            await asyncio.wait_for(proc.wait(), 10)
        finally:
            await wire.stop()


@pytest.mark.parametrize(
    ("root", "kind"),
    [
        (socket.gaierror(socket.EAI_NONAME, "FAKE hostname"), "DNS resolution failed"),
        (ssl.SSLCertVerificationError(1, "FAKE cert"), "TLS certificate verification failed"),
    ],
)
async def test_remote_failure_kind_follows_nested_causes(
    monkeypatch: pytest.MonkeyPatch, root: OSError, kind: str
) -> None:
    wrapper = OSError(root)  # httpcore can retain the error solely as an argument.
    fault = httpx.ConnectError("FAKE transport error")
    fault.__cause__ = wrapper

    @asynccontextmanager
    async def transport(**_kwargs: object):
        raise ExceptionGroup("outer", [ExceptionGroup("inner", [fault])])
        yield  # pragma: no cover

    monkeypatch.setattr("aelix_coding_agent.mcp.client.sse_client", transport)
    conn = McpServerConnection(
        McpServerContrib(name="remote", transport="sse", url="https://localhost/?token=FAKE")
    )
    with pytest.raises(McpConnectionError, match=kind) as caught:
        await conn.connect()
    assert "FAKE" not in str(caught.value)


async def test_unclassified_http_failure_uses_transport_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def transport(**_kwargs: object):
        raise httpx.RemoteProtocolError("FAKE URL and headers")
        yield  # pragma: no cover

    monkeypatch.setattr("aelix_coding_agent.mcp.client.streamablehttp_client", transport)
    conn = McpServerConnection(
        McpServerContrib(name="remote", transport="http", url="http://localhost/?token=FAKE")
    )
    with pytest.raises(McpConnectionError, match=r"connect failed: RemoteProtocolError$"):
        await conn.connect()


async def test_only_stdio_gets_the_handshake_exit_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel(_connection: McpServerConnection):
        raise asyncio.CancelledError

    monkeypatch.setattr(McpServerConnection, "_open_transport", cancel)
    expectations: dict[Literal["stdio", "http"], str] = {
        "stdio": "the server exited during the initialize handshake",
        "http": "initialize handshake cancelled",
    }
    for kind, expected in expectations.items():
        conn = McpServerConnection(McpServerContrib(name="cancelled", transport=kind))
        with pytest.raises(McpConnectionError, match=f"connect failed: {expected}$"):
            await conn.connect()


async def test_rpc_rejects_malformed_endpoint_without_parser_text() -> None:
    # urlsplit includes the invalid bracketed host in its ValueError.
    servers = [{"name": "remote", "type": "http", "url": "http://[FAKE]:123/?token=FAKE"}]
    with pytest.raises(ValueError, match="Invalid MCP HTTP URL") as caught:
        await RpcMcpController().configure(servers, lambda: None)
    assert "FAKE" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://localhost/mcp",
        "http://:443/mcp",
        "http://localhost:0/mcp",
        "http://FAKE:password@localhost/mcp",
    ],
)
async def test_rpc_rejects_invalid_endpoints_before_connecting(url: str) -> None:
    def unexpected_manager(_contribs: list[McpServerContrib]) -> McpClientManager:
        pytest.fail("RPC constructed a manager for an invalid endpoint")

    controller = RpcMcpController(manager_factory=unexpected_manager)
    servers = [{"name": "remote", "type": "http", "url": url}]
    with pytest.raises(ValueError, match="MCP HTTP URL") as caught:
        await controller.configure(servers, lambda: None)
    assert "FAKE" not in str(caught.value)


@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_endpoint_receives_forwarded_headers(local_http: str, transport: str) -> None:
    path = f"/headers-{transport}"
    conn = McpServerConnection(
        McpServerContrib(
            name="remote",
            transport=transport,
            url=f"{local_http}{path}?token=FAKE",
            headers={"X-MCP-Fixture": "fixture-only"},
        )
    )
    with pytest.raises(McpConnectionError):
        await conn.connect()
    assert _Server.received.get_nowait() == (path, "fixture-only")


async def test_remote_failure_kind_follows_implicit_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def transport(**_kwargs: object):
        try:
            raise socket.gaierror(socket.EAI_NONAME, "FAKE hostname")
        except socket.gaierror:
            raise httpx.ConnectError("FAKE request")  # noqa: B904 — exercise implicit chaining
        yield  # pragma: no cover

    monkeypatch.setattr("aelix_coding_agent.mcp.client.sse_client", transport)
    conn = McpServerConnection(
        McpServerContrib(name="remote", transport="sse", url="https://localhost/")
    )
    with pytest.raises(McpConnectionError, match=r"DNS resolution failed \(ConnectError\)$"):
        await conn.connect()


async def test_stdio_os_error_uses_bounded_terminal_excerpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_connection: McpServerConnection) -> None:
        raise OSError(_SERVER_TEXT)

    monkeypatch.setattr(McpServerConnection, "_open_transport", fail)
    conn = McpServerConnection(McpServerContrib(name="stdio", transport="stdio"))
    with pytest.raises(McpConnectionError) as caught:
        await conn.connect()
    reason = str(caught.value).partition("connect failed: ")[2]
    assert "\x1b" not in reason and "\n" not in reason
    assert "SECOND LINE" in reason
    assert len(reason) == 201 and reason.endswith("…")
