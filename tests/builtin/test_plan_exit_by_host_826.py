"""#826 — plan mode's refusal tells the model the way out that applies on its host.

``_plan_block_reason`` ended every refusal with "shift+tab to exit plan mode.",
a TUI key no model can press, on every host: a delegated child (whose mode only
ever tightens, ADR-0306 §6), a ``-p``/``--mode json`` run and an RPC/ACP host
read it as their tool result too. The refusal now ends in
``PermissionExtension.plan_exit``, which ``cli/entry.py`` sets from the run's
host (``permission_mode.plan_exit_for``); the TUI keeps "shift+tab to exit plan
mode.".

Fails on ``1c2a4a7c``: ``plan_exit_for`` and the ``PLAN_EXIT_*`` sentences do not
exist, and the real ``--mode json -p`` child's tool result ends in "shift+tab to
exit plan mode.".

Fix round 1 (Codex M7): the sentence follows the host, not ``AELIX_SUBAGENT_DEPTH``,
which an RPC or TUI process can inherit; the child's sentence is for a print/json
run serving a command inbox. Fails on ``1099de2b``: ``plan_exit_for`` takes
``delegated=``, an RPC launch at depth 1 is told the child's sentence, and so is a
json run at depth 1 with no inbox.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.builtin import permission_mode as pm
from aelix_coding_agent.builtin.permission import PermissionExtension, _plan_block_reason
from aelix_coding_agent.cli import entry as entry_mod

from tests.cli.test_job_tools_builtin_779 import (  # noqa: F401 — scratch is a fixture
    _built,
    _dispose,
    _FakeTTYStdin,
    scratch,
)
from tests.env_sandbox import child_env
from tests.jobs.test_gated_cli_611 import ModelServer, Scratch, call, say, text_of

_TUI = "shift+tab to exit plan mode."
_PRINT = "This run was started in plan mode and stays in it: give the plan in your answer."
_HOST = "Plan mode can be left only by the application running aelix; until it is, give the plan in your answer."
_CHILD = (
    "You are a delegated agent and your parent set plan mode, which a delegated agent's run only "
    "ever tightens: give the plan in your answer."
)


@pytest.mark.parametrize(
    ("app_mode", "inbox", "tail"),
    [
        ("interactive", False, _TUI),
        ("print", False, _PRINT),
        ("json", False, _PRINT),
        ("rpc", False, _HOST),
        # Fix round 1 (Codex M7): the host decides; the child's words are the
        # print/json run's whose command inbox only ever tightens.
        ("print", True, _CHILD),
        ("json", True, _CHILD),
        ("rpc", True, _HOST),
        ("interactive", True, _TUI),
    ],
    ids=["tui", "print", "json", "rpc", "inbox-print", "inbox-json", "inbox-rpc", "inbox-tui"],
)
def test_each_host_is_told_its_own_way_out(app_mode: str, inbox: bool, tail: str) -> None:
    exit_line = pm.plan_exit_for(app_mode, inbox=inbox)
    assert exit_line == tail
    for provenance, tool in (("bash", "bash"), ("write", "edit"), (None, "mcp_search")):
        reason = _plan_block_reason(tool, provenance, exit_line)  # type: ignore[arg-type]
        assert reason.startswith("Plan mode is active: ") and reason.endswith(f" {tail}"), reason
        assert ("shift+tab" in reason) is (tail == pm.PLAN_EXIT_TUI)


def test_the_tui_sentence_is_unchanged() -> None:
    assert pm.MODE_META[pm.PermissionMode.PLAN].block_reason == (
        "Plan mode is active: file edits and shell commands are blocked so you can investigate "
        "and propose a plan first. shift+tab to exit plan mode."
    )
    assert _plan_block_reason("bash", "bash") == pm.MODE_META[pm.PermissionMode.PLAN].block_reason
    assert PermissionExtension().plan_exit == pm.PLAN_EXIT_TUI, "an embedder's gate keeps the TUI words"


class _Seen:
    gates: list[PermissionExtension] = []


@pytest.fixture
def seen_gates(monkeypatch: pytest.MonkeyPatch) -> list[PermissionExtension]:
    """Every permission gate ``_async_main`` builds."""

    _Seen.gates = []

    class _Spy(PermissionExtension):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            _Seen.gates.append(self)

    monkeypatch.setattr(entry_mod, "PermissionExtension", _Spy)
    return _Seen.gates


@pytest.mark.parametrize(
    ("mode", "depth", "flags", "expected"),
    [
        (("--print",), None, (), pm.PLAN_EXIT_PRINT),
        (("--mode", "json"), None, (), pm.PLAN_EXIT_PRINT),
        (("--mode", "rpc"), None, (), pm.PLAN_EXIT_HOST),
        (("--mode", "json"), "1", ("--command-inbox=1",), pm.PLAN_EXIT_CHILD),
        # Codex M7: the depth marker alone names no way out. An RPC process that
        # inherited it still takes ``permissions/set``; a json run with no
        # inbox has nothing that changes its mode.
        (("--mode", "rpc"), "1", (), pm.PLAN_EXIT_HOST),
        (("--mode", "json"), "1", (), pm.PLAN_EXIT_PRINT),
    ],
    ids=["print", "json", "rpc", "delegated-json", "rpc-with-depth", "json-with-depth-no-inbox"],
)
async def test_the_launch_sets_the_gates_way_out_from_its_host(
    scratch: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    seen_gates: list[PermissionExtension],
    mode: tuple[str, ...],
    depth: str | None,
    flags: tuple[str, ...],
    expected: str,
) -> None:
    if depth is not None:
        monkeypatch.setenv("AELIX_SUBAGENT_DEPTH", depth)
    runtime = await _built(scratch, monkeypatch, *flags, mode=mode)
    try:
        assert [gate.plan_exit for gate in seen_gates] == [expected]
    finally:
        await _dispose(runtime)


async def test_the_tui_launch_keeps_shift_tab(
    scratch: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    seen_gates: list[PermissionExtension],
) -> None:
    import aelix_coding_agent.tui as tui_pkg

    monkeypatch.setattr(sys, "stdin", _FakeTTYStdin())

    async def _stub_run_tui(runtime: Any, **_rest: Any) -> int:
        return 0

    monkeypatch.setattr(tui_pkg, "run_tui", _stub_run_tui)
    argv = ["--no-session", "-e", str(scratch / "probe_ext.py"), "--provider", "anthropic", "--model", "probe-model"]
    assert await entry_mod._async_main(argv) == 0
    assert [gate.plan_exit for gate in seen_gates] == [pm.PLAN_EXIT_TUI]


def _tool_result_texts(body: dict[str, Any]) -> list[str]:
    return [text_of(m) for m in body.get("messages", []) if m.get("role") == "tool"]


_POSIX_PIPES = pytest.mark.skipif(sys.platform == "win32", reason="a real child's pipes are POSIX here")


@pytest.mark.parametrize(
    ("depth", "inbox", "expected"),
    [
        pytest.param("1", True, pm.PLAN_EXIT_CHILD, marks=_POSIX_PIPES, id="delegated"),
        # Codex M7: the depth marker without the inbox is a json run nothing tightens.
        pytest.param("1", False, pm.PLAN_EXIT_PRINT, id="depth-no-inbox"),
        pytest.param(None, False, pm.PLAN_EXIT_PRINT, id="top-level"),
    ],
)
def test_a_real_json_print_run_in_plan_mode_reads_its_way_out(
    tmp_path: Path, depth: str | None, inbox: bool, expected: str
) -> None:
    def parent(_body: dict[str, Any], index: int) -> dict[str, Any]:
        return call("bash", {"command": "echo hi"}) if index == 0 else say("planned")

    sc = Scratch(tmp_path)
    with ModelServer(parent) as server:
        sc.point_at(server)
        extra = {"AELIX_SUBAGENT_DEPTH": depth} if depth is not None else {}
        proc = subprocess.Popen(
            [sys.executable, "-P", "-m", "aelix_coding_agent", "--mode", "json", "-p", "go"]
            + ["--no-session", "--permission-mode", "plan", "--provider", "mockp", "--model", "mockm"]
            + (["--command-inbox=1"] if inbox else []),
            cwd=sc.cwd,
            env=child_env(
                sc.home,
                XDG_CONFIG_HOME=str(sc.home / ".config"),
                AELIX_CODING_AGENT_DIR=str(sc.agent_dir),
                PI_OFFLINE="1",
                MOCK_KEY="not-a-real-key",
                AELIX_MAX_RETRIES="0",
                **extra,
            ),
            # The inbox is held open, silent, until the child exits, as a parent
            # holds it: EOF on it before the verdict ends the run with 1 (#123).
            stdin=subprocess.PIPE if inbox else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            with ThreadPoolExecutor(2) as pool:
                out_read, err_read = pool.submit(proc.stdout.read), pool.submit(proc.stderr.read)  # type: ignore[union-attr]
                returncode = proc.wait(timeout=120)
                stdout = out_read.result(10).decode("utf-8", errors="replace")
                stderr = err_read.result(10).decode("utf-8", errors="replace")
        finally:
            if proc.poll() is None:
                proc.kill()
            if proc.stdin is not None:
                proc.stdin.close()
    assert returncode == 0, stderr[-3000:]
    assert len(server.parent) == 2, (len(server.parent), stderr[-2000:])
    (result,) = _tool_result_texts(server.parent[1])
    assert result.startswith("Plan mode is active: ") and result.endswith(expected), result
    assert "shift+tab" not in result
    assert json.dumps(expected)[1:-1] in stdout, "the json event stream carries the same words"
