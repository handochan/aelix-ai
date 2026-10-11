"""#289: root commands never become unintended agent turns after launch flags."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.cli import args, entry
from aelix_coding_agent.cli.args import classify_root_command, parse_args

from tests.env_sandbox import child_env

# Force the TCP self-pipe shape through public APIs on every platform. Private
# CPython fallback helpers are absent in some otherwise supported builds.
_TCP_SOCKETPAIR_SOURCE = textwrap.dedent("""
    import socket
    def _tcp_socketpair(family=None, type=socket.SOCK_STREAM, proto=0):
        if family not in (None, socket.AF_INET) or type != socket.SOCK_STREAM or proto != 0:
            raise ValueError('controlled socketpair requires IPv4 TCP')
        client = server = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.settimeout(5)
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.settimeout(5)
                client.connect(listener.getsockname())
                server, peer = listener.accept()
                if peer != client.getsockname() or server.getsockname() != client.getpeername():
                    raise ConnectionError('unexpected socketpair peer')
                client.settimeout(None)
                server.settimeout(None)
                return server, client
        except BaseException:
            if client is not None:
                client.close()
            if server is not None:
                server.close()
            raise
""")

# Windows asyncio creates its self-pipe through stdlib's TCP socketpair
# fallback. Permit its captured implementation only during that call on the
# creating thread; loopback destinations elsewhere are still forbidden.
_NETWORK_GUARD_SOURCE = textwrap.dedent("""
    import socket, threading
    _pair_creation = threading.local()
    _original_socketpair = socket.socketpair
    def _pair_only(original):
        def guarded(*args, **kwargs):
            if getattr(_pair_creation, 'active', False):
                return original(*args, **kwargs)
            return forbidden(*args, **kwargs)
        return guarded
    def _guarded_socketpair(*args, **kwargs):
        previous = getattr(_pair_creation, 'active', False)
        _pair_creation.active = True
        try:
            return _original_socketpair(*args, **kwargs)
        finally:
            _pair_creation.active = previous
    socket.socket.connect = _pair_only(socket.socket.connect)
    socket.socket.connect_ex = _pair_only(socket.socket.connect_ex)
    socket.getaddrinfo = _pair_only(socket.getaddrinfo)
    socket.gethostbyname = forbidden
    socket.socketpair = _guarded_socketpair
""")


@pytest.mark.parametrize("command", args.ROOT_COMMANDS)
@pytest.mark.parametrize("prefix", [[], ["--offline"], ["--offline", "--offline"]])
def test_root_command_accepts_only_offline_prefix(command: str, prefix: list[str]) -> None:
    route = classify_root_command([*prefix, command, "--help"])
    assert route is not None
    assert (route.command, route.index, route.rejected_options) == (command, len(prefix), ())


@pytest.mark.parametrize(
    "argv",
    [
        ["-e", "policy.py", "status"],
        ["--offline", "--provider", "anthropic", "--model", "haiku", "status"],
        ["--no-extensions", "status"],
        ["--approve", "docs"],
        ["--print", "--offline", "status"],
        ["--help", "status"],
        ["--version", "status"],
        ["--custom", "status"],
        ["--custom", "value", "docs"],
        ["--custom=value", "extension", "list"],
        ["--offline=true", "status"],
        ["--provider=anthropic", "status"],
        ["-epolicy.py", "status"],
        ["-ph", "status"],
        ["--verbose", "status"],
        ["---", "status"],
    ],
)
def test_launch_prefixes_and_unknown_option_ambiguity_are_rejected(argv: list[str]) -> None:
    route = classify_root_command(argv)
    assert route is not None and route.rejected_options


@pytest.mark.parametrize("flag", sorted(args.REQUIRED_VALUE_FLAGS | args.OPTIONAL_VALUE_FLAGS))
@pytest.mark.parametrize("command", args.ROOT_COMMANDS)
def test_command_words_are_literal_known_option_values(flag: str, command: str) -> None:
    assert classify_root_command([flag, command]) is None
    route = classify_root_command([flag, command, "status"])
    assert route is not None
    assert (route.command, route.index) == ("status", 2)
    assert route.rejected_options == (flag,)


@pytest.mark.parametrize(
    "argv, messages, files, fields",
    [
        (["-p", "status"], ["status"], [], {"print_mode": True}),
        (["--offline", "-p", "status"], ["status"], [], {"offline": True}),
        (["-p", "hello", "status"], ["hello", "status"], [], {}),
        (["hello", "--offline", "status"], ["hello", "status"], [], {"offline": True}),
        (["@input", "status"], ["status"], ["input"], {}),
        (["--provider", "status"], [], [], {"provider": "status"}),
        (["--model", "docs"], [], [], {"model": "docs"}),
        (["--resume", "status"], [], [], {"resume_id": "status"}),
        (["--list-models", "extension"], [], [], {"list_models": "extension"}),
        (["--custom=status"], [], [], {"unknown_flags": {"custom": "status"}}),
        (["--custom", "value", "hello", "status"], ["hello", "status"], [], {}),
        (["--", "status"], ["status"], [], {"offline": False}),
        (["--offline", "--", "status"], ["status"], [], {"offline": True}),
        (["-p", "--", "status"], ["status"], [], {"print_mode": True}),
        (
            ["--", "@input", "--offline", "--help", "-e", "path"],
            ["--offline", "--help", "-e", "path"],
            ["input"],
            {"offline": False, "help": False},
        ),
        (["-p", "---", "status"], ["---", "status"], [], {"print_mode": True}),
        (["--provider", "--", "-p", "status"], ["status"], [], {"provider": "--"}),
        (["--resume", "", "status"], ["", "status"], [], {"resume_id": None}),
        (["--list-models", "", "hello"], ["hello"], [], {"list_models": ""}),
    ],
)
def test_agent_routes_preserve_existing_values_and_explicit_prompts(
    argv: list[str], messages: list[str], files: list[str], fields: dict[str, Any]
) -> None:
    assert classify_root_command(argv) is None
    parsed = parse_args(argv)
    assert parsed.messages == messages
    assert parsed.file_args == files
    for field, value in fields.items():
        assert getattr(parsed, field) == value


def test_the_shared_arity_inventory_covers_every_registered_parser_flag() -> None:
    """A newly added branch must make an explicit routing/arity decision."""
    tree = ast.parse(Path(args.__file__).read_text(encoding="utf-8"))
    parser = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_args")
    registered: set[str] = set()
    for node in ast.walk(parser):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "arg"
        ):
            registered.update(
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and n.value.startswith("-")
            )
    assert registered - {"--"} == (
        args.REQUIRED_VALUE_FLAGS
        | args.PRINT_FLAGS
        | args.OPTIONAL_VALUE_FLAGS
        | args.FLAG_ONLY_FLAGS
    )


def test_equals_and_short_clusters_gain_no_new_agent_option_support() -> None:
    parsed = parse_args(["--offline=true", "--provider=status", "-epolicy.py"])
    assert not parsed.offline and parsed.provider is None and parsed.extensions == []
    assert parsed.unknown_flags == {"offline": "true", "provider": "status"}
    assert parsed.diagnostics == [{"type": "error", "message": "Unknown short flag: -epolicy.py"}]


def _forbid(*unused_args: Any, **unused_kwargs: Any) -> Any:
    pytest.fail("command routing crossed into a forbidden runtime boundary")


@pytest.fixture
def runtime_tripwires(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_ai.providers import anthropic
    from aelix_coding_agent import model_registry
    from aelix_coding_agent.extensions import loader

    for name in ("AgentHarness", "Session", "_build_harness_options", "resolve_project_trusted"):
        monkeypatch.setattr(entry, name, _forbid)
    monkeypatch.setattr(entry, "discover_and_load_extensions", _forbid)
    monkeypatch.setattr(loader, "discover_and_load_extensions", _forbid)
    monkeypatch.setattr(model_registry, "ModelRegistry", _forbid)
    monkeypatch.setattr(anthropic, "stream_anthropic", _forbid)


@pytest.mark.parametrize("command", args.ROOT_COMMANDS)
async def test_offline_dispatch_precedes_agent_parser_and_exports_before_handler(
    command: str, monkeypatch: pytest.MonkeyPatch, runtime_tripwires: None
) -> None:
    from aelix_coding_agent.cli import docs, extension_install, status

    calls: list[list[str]] = []
    monkeypatch.delenv("PI_OFFLINE", raising=False)
    monkeypatch.delenv("AELIX_OFFLINE", raising=False)
    monkeypatch.setattr(entry, "parse_args", _forbid)

    def record(argv: list[str]) -> int:
        assert os.environ["PI_OFFLINE"] == "1"
        calls.append(argv)
        return 17

    async def record_async(argv: list[str]) -> int:
        return record(argv)

    monkeypatch.setattr(docs, "run_docs_command", record)
    monkeypatch.setattr(status, "run_status_command", record_async)
    monkeypatch.setattr(extension_install, "run_extension_command_async", record_async)
    assert await entry._async_main(["--offline", "--offline", command, "--help"]) == 17
    assert calls == [["--help"]]


@pytest.mark.parametrize("tail", [["list", "--offline"], ["install", "--", "--offline"]])
async def test_command_first_extension_keeps_raw_tail_offline_export(
    tail: list[str], monkeypatch: pytest.MonkeyPatch, runtime_tripwires: None
) -> None:
    from aelix_coding_agent.cli import extension_install

    async def record(argv: list[str]) -> int:
        assert argv == tail
        assert os.environ["PI_OFFLINE"] == "1"
        return 0

    monkeypatch.setattr(entry, "parse_args", _forbid)
    monkeypatch.setattr(extension_install, "run_extension_command_async", record)
    assert await entry._async_main(["extension", *tail]) == 0


@pytest.mark.parametrize("command", args.ROOT_COMMANDS)
@pytest.mark.parametrize(
    "prefix", [["-e", "secret/path.py"], ["--api-key", "secret-token"], ["--unknown"]]
)
async def test_rejected_prefix_never_reaches_handlers_parser_or_runtime(
    command: str,
    prefix: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runtime_tripwires: None,
) -> None:
    from aelix_coding_agent.cli import docs, extension_install, status

    monkeypatch.setattr(entry, "parse_args", _forbid)
    monkeypatch.setattr(docs, "run_docs_command", _forbid)
    monkeypatch.setattr(status, "run_status_command", _forbid)
    monkeypatch.setattr(extension_install, "run_extension_command_async", _forbid)
    assert await entry._async_main(["--offline", *prefix, command]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "only --offline may precede" in captured.err
    assert "secret" not in captured.err


async def test_intentional_agent_prompt_reaches_parser_unchanged(
    monkeypatch: pytest.MonkeyPatch, runtime_tripwires: None
) -> None:
    seen: list[args.Args] = []

    def capture(argv: list[str]) -> args.Args:
        parsed = parse_args(argv)
        seen.append(parsed)
        parsed.version = True  # stop before runtime; routing and parsing stay real
        return parsed

    monkeypatch.setattr(entry, "parse_args", capture)
    for argv in (["--offline", "-p", "status"], ["-p", "--", "status"], ["hello", "docs"]):
        assert await entry._async_main(argv) == 0
    assert [p.messages for p in seen] == [["status"], ["status"], ["hello", "docs"]]


async def test_leading_offline_status_keeps_real_extension_discovery_and_its_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from aelix_coding_agent import model_registry

    agent = tmp_path / "agent"
    extensions = agent / "extensions"
    extensions.mkdir(parents=True)
    marker = tmp_path / "loaded"
    (extensions / "probe.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
        "def setup(api): pass\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(entry, "AgentHarness", _forbid)
    monkeypatch.setattr(entry, "Session", _forbid)
    monkeypatch.setattr(model_registry, "ModelRegistry", _forbid)
    assert await entry._async_main(["--offline", "status", "--json"]) == 0
    assert marker.read_text() == "loaded"
    assert json.loads(capsys.readouterr().out)["discovered_extensions"]
    marker.unlink()
    assert await entry._async_main(["--offline", "status", "--json", "--no-extensions"]) == 0
    assert not marker.exists()
    assert json.loads(capsys.readouterr().out)["discovered_extensions"] is None


@pytest.mark.parametrize("socketpair_impl", ["native", "fallback"])
@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--offline", "docs"], 0),
        (["--offline", "docs", "--search", "--offline"], 0),
        (["--offline", "status", "--no-extensions", "--json"], 0),
        (["--offline", "extension", "list"], 0),
        (["--offline", "--provider", "anthropic", "--model", "haiku", "status"], 2),
        (["-e", "policy.py", "status"], 2),
        (["-e", "policy.py", "docs"], 2),
        (["-e", "policy.py", "extension", "list"], 2),
        (["--approve", "docs"], 2),
        (["--unknown", "status"], 2),
        (["status", "--offline"], 2),
        (["docs", "--offline"], 2),
    ],
)
def test_real_cli_root_commands_do_not_launch_a_session_or_make_network_requests(
    socketpair_impl: str, argv: list[str], expected: int, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    agent = tmp_path / "agent"
    extensions = agent / "extensions"
    extensions.mkdir(parents=True)
    sentinel = tmp_path / "executed"
    planted = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n"
    (tmp_path / "policy.py").write_text(planted, encoding="utf-8")
    (extensions / "global.py").write_text(planted, encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    tripwire = tmp_path / "tripwire"
    (probe / "sitecustomize.py").write_text(
        textwrap.dedent(f"""
        import importlib.abc, pathlib, socket, sys
        def forbidden(*args, **kwargs):
            pathlib.Path({str(tripwire)!r}).write_text('runtime or network')
            raise RuntimeError('forbidden runtime or network')
        """)
        + _TCP_SOCKETPAIR_SOURCE
        + textwrap.dedent(f"""
        if {socketpair_impl == "fallback"!r}:
            socket.__dict__.pop('_fallback_socketpair', None)
            socket.socketpair = _tcp_socketpair
        """)
        + _NETWORK_GUARD_SOURCE
        + textwrap.dedent("""
        class NoTui(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.split('.')[0] in ('rich', 'prompt_toolkit', 'term_image'):
                    raise ImportError('optional TUI dependency was imported')
        sys.meta_path.insert(0, NoTui())
        from aelix_coding_agent.cli import entry
        from aelix_coding_agent.extensions import loader
        from aelix_coding_agent import model_registry
        entry.AgentHarness = forbidden
        entry.Session = forbidden
        entry._build_harness_options = forbidden
        entry.discover_and_load_extensions = forbidden
        loader.discover_and_load_extensions = forbidden
        model_registry.ModelRegistry = forbidden
        """),
        encoding="utf-8",
    )
    roots = [
        str(probe),
        *[str(Path(m.__file__).resolve().parent.parent.parent) for m in (args, entry)],
    ]
    # Editable installations use this tree; the probe must be the first import.
    env = child_env(
        home,
        PYTHONPATH=os.pathsep.join(roots),
        AELIX_CODING_AGENT_DIR=str(agent),
        AELIX_SETTINGS_PATH=str(tmp_path / "settings.json"),
    )
    proc = subprocess.run(
        [sys.executable, "-m", "aelix_coding_agent", *argv],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert proc.returncode == expected, (proc.stdout, proc.stderr)
    assert "Error in sitecustomize" not in proc.stderr, proc.stderr
    assert not sentinel.exists(), "an explicitly supplied or global extension ran"
    assert not tripwire.exists(), (proc.stdout, proc.stderr)
    assert not list(tmp_path.rglob("*.jsonl"))
    assert not (agent / "auth.json").exists()
    if expected == 2:
        assert proc.stdout == ""
    elif "status" in argv:
        assert json.loads(proc.stdout)["discovered_extensions"] is None
    elif "docs" in argv:
        assert proc.stdout.strip()


@pytest.mark.parametrize("mode", ["native", "fallback", "thread", "failure"])
def test_network_tripwire_allows_only_socketpair_creation(mode: str, tmp_path: Path) -> None:
    """Exercise the exact injected guard and a public TCP self-pipe producer."""
    probe = (
        textwrap.dedent("""
        import socket, sys, threading
        blocked = []
        allowed_connects = []
        real_connect = socket.socket.connect
        def record_connect(*args, **kwargs):
            allowed_connects.append(threading.get_ident())
            return real_connect(*args, **kwargs)
        socket.socket.connect = record_connect
        def forbidden(*args, **kwargs):
            blocked.append(threading.get_ident())
            raise RuntimeError('network tripwire')
        """)
        + _TCP_SOCKETPAIR_SOURCE
        + textwrap.dedent("""
        if sys.argv[1] != 'native':
            # Reproduce the CI build's absent private helper without skipping
            # the TCP path or depending on that helper to implement it.
            socket.__dict__.pop('_fallback_socketpair', None)
            socket.socketpair = _tcp_socketpair
        """)
        + _NETWORK_GUARD_SOURCE
        + textwrap.dedent("""
        def assert_blocked(call):
            before = len(blocked)
            try:
                call()
            except RuntimeError as exc:
                assert str(exc) == 'network tripwire'
            else:
                raise AssertionError('network was admitted outside socketpair')
            assert len(blocked) == before + 1
        def outside_checks():
            for host in ('127.0.0.1', '192.0.2.1'):
                with socket.socket() as sock:
                    assert_blocked(lambda: sock.connect((host, 9)))
                    assert_blocked(lambda: sock.connect_ex((host, 9)))
                assert_blocked(lambda: socket.getaddrinfo(host, 9))
            assert_blocked(lambda: socket.getaddrinfo('provider.invalid', 443))
            assert_blocked(lambda: socket.gethostbyname('provider.invalid'))
        outside_checks()
        before = len(blocked)
        stdlib_pair = _original_socketpair
        if sys.argv[1] == 'thread':
            failures = []
            def worker():
                try:
                    outside_checks()
                except BaseException as exc:
                    failures.append(exc)
            def held_pair(*args, **kwargs):
                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(5)
                assert not thread.is_alive(), 'network guard worker did not finish'
                assert not failures, failures
                return stdlib_pair(*args, **kwargs)
            _original_socketpair = held_pair
        elif sys.argv[1] == 'failure':
            def failed_pair(*args, **kwargs):
                raise ValueError('socketpair creation failed')
            _original_socketpair = failed_pair
            try:
                socket.socketpair()
            except ValueError as exc:
                assert str(exc) == 'socketpair creation failed'
            else:
                raise AssertionError('socketpair failure was hidden')
            assert not _pair_creation.active
            outside_checks()
            before = len(blocked)
            _original_socketpair = stdlib_pair
        left, right = socket.socketpair()
        try:
            left.settimeout(5)
            right.settimeout(5)
            left.sendall(b'pair')
            assert right.recv(4) == b'pair'
        finally:
            left.close()
            right.close()
        assert not _pair_creation.active
        assert len(blocked) == before + (8 if sys.argv[1] == 'thread' else 0)
        if sys.argv[1] != 'native':
            assert allowed_connects == [threading.get_ident()]
        outside_checks()
        print('socketpair allowed; unrelated connect/connect_ex/DNS blocked; scope restored')
        """)
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, mode],
        cwd=tmp_path,
        env=child_env(tmp_path / "home"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (mode, proc.stdout, proc.stderr)
    assert "scope restored" in proc.stdout
