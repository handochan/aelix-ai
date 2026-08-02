"""Issue #91 — ``contributes.mcp_servers`` capability gate.

``[[contributes.mcp_servers]]`` was the one *executing* declarative family
with NO capability gate at all. ``cli/entry.py`` harvested every discovered
manifest's servers and handed them to ``McpClientManager``, which for
``transport = "stdio"`` exec's ``command`` + ``args`` with
``{**os.environ, **env}``. A pack needing neither a ``[capabilities]`` table
nor a ``[plugin.entry] python`` — the most auditable-looking manifest it is
possible to write — therefore ran arbitrary commands on the next start, with
a benign "MCP server failed: Connection closed" warning as its only trace.

Mirrors ``test_hooks_gate_manifest.py`` in structure, and the load-bearing
assertion is the same shape: NEGATIVE and OBSERVABLE. The fixture's command
writes a marker file, so the tests distinguish "connect returned an error"
(which the ungated code ALSO did — after spawning) from "the subprocess never
ran". An exception alone would not tell those apart.

Gate mapping under test (``loader._MCP_TRANSPORT_CAPABILITY``):
``stdio`` → ``shell_exec`` (a spawned subprocess — the same primitive
``contributes.hooks`` is gated on); ``http``/``sse`` → ``net`` (an outbound
connection, no child process).
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import textwrap
from pathlib import Path

from aelix_agent_core.contracts import parse_manifest_toml
from aelix_coding_agent.extensions.loader import (
    gate_manifest_mcp_contribs,
    scan_extension_manifests,
)
from aelix_coding_agent.mcp import McpClientManager

_MARKER_PAYLOAD = "SPAWNED"


def _manifest(
    *,
    capabilities: str,
    server: str,
) -> str:
    return textwrap.dedent(f"""
        [plugin]
        id = "mcp-gate-plug"
        name = "MCP Gate Plugin"
        version = "0.1.0"
        description = "Declares an MCP server"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/mcp-gate-plug"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [activation]
        on_startup_finished = true

        {server}

        {capabilities}
    """).strip()


def _stdio_server(marker: Path) -> str:
    """A stdio server whose ``command`` proves execution by side effect.

    The marker is written to a scratch path and ``mv``'d into place, so it is
    published ATOMICALLY. Writing straight to ``marker`` makes ``sh`` create
    and truncate it before ``printf`` fills it, leaving a window in which
    ``exists()`` is already true but ``read_text()`` returns ``""`` — measured
    at ~2% (60/3000) on this machine, i.e. a flake in the payload assertion.
    Rename is the only step the poller can observe.
    """
    script = f"printf {_MARKER_PAYLOAD} > {marker}.part && mv {marker}.part {marker}"
    return textwrap.dedent(f"""
        [[contributes.mcp_servers]]
        name = "gate-probe"
        transport = "stdio"
        command = "/bin/sh"
        args = ["-c", "{script}"]
    """).strip()


def _write_pack(tmp_path: Path, manifest_text: str) -> tuple[Path, Path]:
    """Project cwd + isolated agent_dir (never touch the real ~/.aelix)."""
    cwd = tmp_path / "proj"
    pkg = cwd / ".aelix" / "extensions" / "mcp-gate-plug"
    pkg.mkdir(parents=True)
    agent_dir = tmp_path / "agent"
    (agent_dir / "extensions").mkdir(parents=True)
    pkg.joinpath("aelix-plugin.toml").write_text(manifest_text, encoding="utf-8")
    return cwd, agent_dir


async def _startup_path(
    tmp_path: Path, manifest_text: str
) -> tuple[list[str], list[str]]:
    """Replay the cli/entry.py startup sequence for manifest MCP servers.

    scan → gate → ``McpClientManager.connect_all()``. Returns
    ``(connected-or-attempted server names, refusal messages)``. Deliberately
    calls the REAL manager: the property under test is that nothing spawns,
    which only a real connect attempt can falsify.
    """
    cwd, agent_dir = _write_pack(tmp_path, manifest_text)
    manifests = scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)
    allowed, notices, refusals = gate_manifest_mcp_contribs(reversed(manifests))
    del notices  # asserted separately; irrelevant to the spawn question
    if allowed:
        manager = McpClientManager(allowed)
        await _connect_and_teardown_quietly(manager)
        # Drop the transport while the event loop is still OPEN. Left to the
        # GC, the abandoned subprocess transport is finalized after teardown
        # and raises an unraisable "Event loop is closed" — which this repo
        # turns into a test failure, attributed to whichever test happens to
        # be running. Deterministic cleanup instead of a timing lottery.
        del manager
        gc.collect()
    return [c.name for c in allowed], refusals


async def _connect_and_teardown_quietly(manager: McpClientManager) -> None:
    """Drive a real connect/disconnect and absorb the fallout, in a CHILD task.

    Errors are EXPECTED: the probe is a plain command, not an MCP server, so
    the handshake always fails. The spawn is what matters, and the spawn has
    already happened by the time anything fails.

    Two properties of the containment are load-bearing, both learned by
    measurement rather than reasoning:

    * ``BaseException``, not ``Exception``. A stdio child that exits at once
      makes the MCP SDK unwind ``stdio_client``'s cancel scope from a task
      other than the one that entered it. anyio's response is sometimes
      ``RuntimeError("Attempted to exit cancel scope in a different task")``
      — an ``Exception`` — but under CPU contention it is usually
      ``asyncio.CancelledError``, which has been a ``BaseException`` since
      3.8 and therefore walked straight through ``suppress(Exception)``.
      Measured on the first version of this file: 6/20 failures with six
      busy-loops running, 0/20 idle. A green half that is only green on an
      idle machine gets muted, and then the refusal half looks fine because
      everything is refused.
    * A dedicated ``Task``. Suppressing ``CancelledError`` in the *test's own*
      task is not enough — a cancellation delivered to that task can leave it
      flagged, so the next ``await`` (the marker poll) re-raises. Isolating
      the connect in a child task means any foreign cancel scope lands there,
      and ``asyncio.wait`` reports the child's fate as data instead of
      re-raising it.

    Test-local containment ONLY. The product-side gap — ``connect_all`` lets
    ``CancelledError`` escape, so ``cli/entry.py`` can abort startup on a
    crash-on-spawn MCP server — is PRE-EXISTING (reproduced identically via
    ``.aelix/mcp.json`` at HEAD) and is reported as its own issue, not
    papered over here.
    """

    async def _drive() -> None:
        with contextlib.suppress(BaseException):
            await manager.connect_all()
        with contextlib.suppress(BaseException):
            await manager.disconnect_all()

    task = asyncio.ensure_future(_drive())
    # Bounded: neither connect nor disconnect has a timeout of its own, so a
    # hang here would hang the suite rather than fail it.
    done, pending = await asyncio.wait({task}, timeout=30.0)
    for stuck in pending:
        stuck.cancel()
        with contextlib.suppress(BaseException):
            await stuck
    for finished in done:
        if not finished.cancelled():
            # Retrieve any exception so asyncio does not log it as never-retrieved.
            finished.exception()


async def _wait_for_marker(marker: Path, timeout: float = 2.0) -> bool:
    """Poll for the marker — the child process is spawned asynchronously."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if marker.exists():
            return True
        await asyncio.sleep(0.02)
    return marker.exists()


async def test_stdio_server_without_shell_exec_never_spawns(
    tmp_path: Path,
) -> None:
    """THE regression test. No ``[capabilities]`` table at all → the declared
    command must not run. Asserted on the marker, not on an exception."""
    marker = tmp_path / "spawned.marker"
    allowed, refusals = await _startup_path(
        tmp_path,
        _manifest(capabilities="", server=_stdio_server(marker)),
    )

    # FIRST assertion on purpose: the marker is the load-bearing evidence.
    # Everything below only describes HOW the refusal was reported; this line
    # is the one that says the command never ran.
    assert not await _wait_for_marker(marker, timeout=0.5)
    assert allowed == []
    (refusal,) = refusals
    assert "shell_exec" in refusal
    assert "'mcp-gate-plug'" in refusal
    assert "'gate-probe'" in refusal
    assert "NOT started" in refusal


async def test_stdio_server_with_shell_exec_still_spawns(
    tmp_path: Path,
) -> None:
    """MUTATION COUNTERPART of the test above, and the "legitimate pack still
    works" proof: flipping ``shell_exec`` to true makes the marker appear. If
    the gate ever degrades into "refuse everything", this goes red."""
    marker = tmp_path / "spawned.marker"
    allowed, refusals = await _startup_path(
        tmp_path,
        _manifest(
            capabilities="[capabilities]\nshell_exec = true",
            server=_stdio_server(marker),
        ),
    )

    assert refusals == []
    assert allowed == ["gate-probe"]
    assert await _wait_for_marker(marker)
    assert marker.read_text() == _MARKER_PAYLOAD


async def test_http_server_requires_net_not_shell_exec(tmp_path: Path) -> None:
    """http/sse spawn no process; they dial a plugin-chosen URL. They are
    gated on ``net``, and ``shell_exec`` must NOT unlock them — otherwise the
    stdio flag would silently grant an unrelated capability."""
    http_server = textwrap.dedent("""
        [[contributes.mcp_servers]]
        name = "remote-probe"
        transport = "http"
        url = "http://127.0.0.1:1/mcp"
    """).strip()

    allowed, refusals = await _startup_path(
        tmp_path,
        _manifest(
            capabilities="[capabilities]\nshell_exec = true",
            server=http_server,
        ),
    )
    assert allowed == []
    (refusal,) = refusals
    assert "capabilities.net is false" in refusal
    assert "'remote-probe'" in refusal


async def test_http_server_with_net_is_allowed(tmp_path: Path) -> None:
    """The ``net`` flag really is the http/sse key (mutation counterpart of
    the test above). Gate-level assertion only — no connect attempt, so the
    test stays hermetic and never touches the network."""
    cwd, agent_dir = _write_pack(
        tmp_path,
        _manifest(
            capabilities="[capabilities]\nnet = true",
            server=textwrap.dedent("""
                [[contributes.mcp_servers]]
                name = "remote-probe"
                transport = "sse"
                url = "http://127.0.0.1:1/sse"
            """).strip(),
        ),
    )
    manifests = scan_extension_manifests([], cwd=cwd, agent_dir=agent_dir)
    allowed, notices, refusals = gate_manifest_mcp_contribs(manifests)

    assert refusals == []
    assert [c.name for c in allowed] == ["remote-probe"]
    (notice,) = notices
    assert "'remote-probe'" in notice and "capabilities.net=true" in notice


def test_refusal_never_echoes_server_env_secrets() -> None:
    """``McpServerContrib.env`` carries plugin-supplied API tokens; the
    refusal is printed to stderr by ``cli/entry.py`` and lands in terminal
    scrollback, CI logs and pasted bug reports. Same leak class as the
    ``_ManifestEntry`` repr fix (878004b) — pinned so it cannot come back."""
    secret = "ghp_TESTONLY_MCP_GATE_SECRET_0123456789"
    manifest = parse_manifest_toml(
        _manifest(
            capabilities="",
            server=textwrap.dedent(f"""
                [[contributes.mcp_servers]]
                name = "secretsrv"
                transport = "stdio"
                command = "node"
                args = ["--inspect", "/opt/secret/path.js"]
                [contributes.mcp_servers.env]
                GITHUB_TOKEN = "{secret}"
            """).strip(),
        )
    )
    allowed, notices, refusals = gate_manifest_mcp_contribs([manifest])

    assert allowed == []
    assert notices == []
    (refusal,) = refusals
    # This is the exact string cli/entry.py writes to stderr.
    rendered = f"Warning: MCP server refused: {refusal}"
    assert secret not in rendered
    assert "GITHUB_TOKEN" not in rendered
    # The command line is attacker-authored too and is not echoed either.
    assert "/opt/secret/path.js" not in rendered
    assert "McpServerContrib(" not in rendered and "PluginManifest(" not in rendered


def test_notice_announces_an_allowed_server_without_leaking_env() -> None:
    """The ALLOW path must be visible AND as leak-safe as the refuse path.

    Visible, because capabilities are per-manifest: a pack the user granted
    ``shell_exec`` for its hook also gets a stdio MCP spawn from the same
    manifest, and this line is the only place in the product where that
    becomes observable. Leak-safe, because it prints on the path where ``env``
    actually holds live tokens — the refuse path never even connects."""
    secret = "ghp_TESTONLY_MCP_NOTICE_SECRET_0123456789"
    manifest = parse_manifest_toml(
        _manifest(
            capabilities="[capabilities]\nshell_exec = true",
            server=textwrap.dedent(f"""
                [[contributes.mcp_servers]]
                name = "secretsrv"
                transport = "stdio"
                command = "node"
                args = ["--inspect", "/opt/secret/path.js"]
                [contributes.mcp_servers.env]
                GITHUB_TOKEN = "{secret}"
            """).strip(),
        )
    )
    allowed, notices, refusals = gate_manifest_mcp_contribs([manifest])

    assert refusals == []
    assert [c.name for c in allowed] == ["secretsrv"]
    (notice,) = notices
    rendered = f"Notice: {notice}"  # exactly what cli/entry.py writes
    assert "'mcp-gate-plug'" in rendered
    assert "'secretsrv'" in rendered
    assert "capabilities.shell_exec=true" in rendered
    assert secret not in rendered
    assert "GITHUB_TOKEN" not in rendered
    assert "/opt/secret/path.js" not in rendered


def test_refusal_escapes_control_characters_in_server_name() -> None:
    """``name`` is a free-form, attacker-chosen string with no pattern
    constraint. Interpolating it raw would let a pack inject newlines / ANSI
    into the warning and forge host output ("...  Server started."). ``!r``
    escapes them; this pins that the message keeps using it."""
    manifest = parse_manifest_toml(
        _manifest(
            capabilities="",
            server=textwrap.dedent("""
                [[contributes.mcp_servers]]
                name = "evil\\u001b[2K\\rServer started, all good"
                transport = "stdio"
                command = "/bin/sh"
            """).strip(),
        )
    )
    _allowed, _notices, (refusal,) = gate_manifest_mcp_contribs([manifest])

    assert "\x1b" not in refusal
    assert "\n" not in refusal and "\r" not in refusal
    assert "shell_exec" in refusal


def test_gate_preserves_caller_order_for_allowed_servers() -> None:
    """``McpClientManager`` keys connections by name and is LAST-wins, so
    ``cli/entry.py`` hands the manifests over in a deliberate precedence
    order. The gate must filter, never reorder."""

    def _pack(plugin_id: str, server_name: str) -> str:
        return textwrap.dedent(f"""
            [plugin]
            id = "{plugin_id}"
            name = "P"
            version = "0.1.0"
            description = "d"
            authors = ["T <t@example.com>"]
            repository = "https://github.com/example/{plugin_id}"
            license = "MIT"

            [plugin.api]
            level = 1
            min_level = 1

            [activation]
            on_startup_finished = true

            [capabilities]
            shell_exec = true

            [[contributes.mcp_servers]]
            name = "{server_name}"
            transport = "stdio"
            command = "true"
        """).strip()

    manifests = [
        parse_manifest_toml(_pack("pack-a", "srv-a")),
        parse_manifest_toml(_pack("pack-b", "srv-b")),
        parse_manifest_toml(_pack("pack-c", "srv-c")),
    ]
    allowed, notices, refusals = gate_manifest_mcp_contribs(manifests)

    assert refusals == []
    assert [c.name for c in allowed] == ["srv-a", "srv-b", "srv-c"]
    # Notices are parallel to ``allowed`` — same count, same order.
    assert [n.split("'")[3] for n in notices] == ["srv-a", "srv-b", "srv-c"]
