"""Sprint 6h₉e — subprocess hook dispatch tests (Tier 4b, ADR-0102).

Covers the 25 scenarios enumerated in the Sprint 6h₉e spec §7:

- ``run_hook_subprocess`` spawn core (1-6).
- ``serialize_hook_event`` stdin envelope (7-9).
- ``parse_hook_output`` stdout / exit-code mapping (10-17).
- ``validate_subprocess_hook_event`` + module invariant (18-21).
- Loader wiring with a real ``aelix-plugin.toml`` (22-24).
- End-to-end: subprocess deny composes with the in-process reducer (25).

``asyncio_mode = "auto"`` — plain ``async def test_*``, no decorator.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import aelix_coding_agent.extensions.subprocess_hooks as subprocess_hooks
import pytest
from aelix_agent_core.contracts import HookContrib
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    BashToolCallHookEvent,
    HookBus,
    InputHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.extensions.loader import (
    ExtensionManifestError,
    discover_and_load_extensions,
)
from aelix_coding_agent.extensions.subprocess_hooks import (
    SUBPROCESS_HOOK_EVENTS,
    HookSubprocessOutcome,
    make_subprocess_handler,
    parse_hook_output,
    run_hook_subprocess,
    serialize_hook_event,
    validate_subprocess_hook_event,
)

# === Helpers ===


def _make_ctx(cwd: str = "/tmp/work") -> ExtensionContext:
    return ExtensionContext(
        _ExtensionRuntime(),
        cwd=cwd,
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


def _outcome(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> HookSubprocessOutcome:
    return HookSubprocessOutcome(
        exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=timed_out
    )


def _write_hook_plugin(
    parent: Path,
    *,
    name: str,
    event: str,
    command: str,
    shell_exec: bool,
) -> None:
    """Write a hooks-only ``aelix-plugin.toml`` plugin under .aelix/extensions."""

    pkg_dir = parent / ".aelix" / "extensions" / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    caps = "\nshell_exec = true" if shell_exec else ""
    manifest = textwrap.dedent(
        f"""
        [plugin]
        id = "{name}"
        name = "Hook Plugin {name}"
        version = "0.1.0"
        description = "Subprocess hook test plugin"
        authors = ["Test <test@example.com>"]
        repository = "https://github.com/example/{name}"
        license = "MIT"

        [plugin.api]
        level = 1
        min_level = 1

        [capabilities]{caps}

        [activation]
        on_startup_finished = true

        [[contributes.hooks]]
        event = "{event}"
        command = {command!r}
        timeout_ms = 2000
        """
    ).strip()
    (pkg_dir / "aelix-plugin.toml").write_text(manifest, encoding="utf-8")


# === run_hook_subprocess (1-6) ===


async def test_run_subprocess_echo_stdin_to_stdout() -> None:
    """#1 — ``cat`` echoes stdin → stdout; exit 0."""

    payload = '{"hook_event_name": "tool_call"}'
    outcome = await run_hook_subprocess("cat", payload, timeout_ms=2000)
    assert outcome.exit_code == 0
    assert outcome.stdout == payload
    assert outcome.timed_out is False


async def test_run_subprocess_exit0_with_json_stdout() -> None:
    """#2 — exit 0 with JSON on stdout."""

    cmd = """python3 -c 'print(\"{\\"decision\\": \\"block\\"}\")'"""
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=2000)
    assert outcome.exit_code == 0
    assert '"decision"' in outcome.stdout


async def test_run_subprocess_exit2_with_stderr() -> None:
    """#3 — exit 2 with stderr captured; not timed out."""

    cmd = """python3 -c 'import sys; sys.stderr.write("nope"); sys.exit(2)'"""
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=2000)
    assert outcome.exit_code == 2
    assert "nope" in outcome.stderr
    assert outcome.timed_out is False


async def test_run_subprocess_timeout() -> None:
    """#4 — ``sleep 5`` with 200ms timeout → timed_out, exit 124, fast."""

    import time

    start = time.monotonic()
    outcome = await run_hook_subprocess("sleep 5", "", timeout_ms=200)
    elapsed = time.monotonic() - start
    assert outcome.timed_out is True
    assert outcome.exit_code == 124
    assert elapsed < 2.0


async def test_run_subprocess_nonexistent_command_no_raise() -> None:
    """#5 — nonexistent command via shell → non-zero exit, no raise."""

    outcome = await run_hook_subprocess(
        "this_cmd_does_not_exist_xyz", "", timeout_ms=2000
    )
    assert outcome.exit_code != 0
    assert outcome.timed_out is False


async def test_run_subprocess_stdout_cap() -> None:
    """#6 — stdout capped at 10k chars."""

    cmd = """python3 -c 'import sys; sys.stdout.write("x" * 20000)'"""
    outcome = await run_hook_subprocess(cmd, "", timeout_ms=5000)
    assert outcome.exit_code == 0
    assert len(outcome.stdout) == 10_000


# === serialize_hook_event (7-9) ===


async def test_serialize_tool_call_envelope() -> None:
    """#7 — tool_call envelope: snake_case common + tool keys."""

    ctx = _make_ctx(cwd="/proj")
    event = ToolCallHookEvent(
        tool_call_id="tc-1", tool_name="bash", args={"command": "ls"}
    )
    payload = serialize_hook_event(event, ctx)
    assert payload["hook_event_name"] == "tool_call"
    assert payload["tool_name"] == "bash"
    assert payload["tool_use_id"] == "tc-1"
    assert payload["tool_input"] == {"command": "ls"}
    assert payload["cwd"] == "/proj"
    assert payload["session_id"] == ""


async def test_serialize_typed_subclass_routes_through_tool_call() -> None:
    """#8 — ``BashToolCallHookEvent`` routes through the tool_call branch."""

    ctx = _make_ctx()
    event = BashToolCallHookEvent(tool_call_id="tc-2", args={"command": "pwd"})
    payload = serialize_hook_event(event, ctx)
    assert payload["hook_event_name"] == "tool_call"
    assert payload["tool_name"] == "bash"
    assert payload["tool_input"] == {"command": "pwd"}


async def test_serialize_input_event_and_non_serializable_arg() -> None:
    """#9 — input → prompt/source; non-serializable arg degrades via default=str."""

    import json

    ctx = _make_ctx()
    event = InputHookEvent(text="hello", source="interactive")
    payload = serialize_hook_event(event, ctx)
    assert payload["prompt"] == "hello"
    assert payload["source"] == "interactive"

    # A non-JSON-serializable arg value degrades to its str() rather than raising.
    class _Unserializable:
        def __str__(self) -> str:
            return "<obj>"

    tc = ToolCallHookEvent(
        tool_call_id="tc-3", tool_name="custom", args={"x": _Unserializable()}
    )
    tc_payload = serialize_hook_event(tc, ctx)
    encoded = json.dumps(tc_payload, default=str)
    assert "<obj>" in encoded


# === parse_hook_output (10-17) ===


async def test_parse_timeout_returns_none() -> None:
    """#10 — timeout outcome → None (fail-open)."""

    assert parse_hook_output("tool_call", _outcome(timed_out=True, exit_code=124)) is None


async def test_parse_exit2_tool_call_blocks_with_stderr() -> None:
    """#11 — exit 2 + tool_call → ToolCallResult(block=True, reason=stderr)."""

    result = parse_hook_output("tool_call", _outcome(exit_code=2, stderr="  denied  "))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "denied"


async def test_parse_exit2_non_tool_call_returns_none() -> None:
    """#12 — exit 2 on a non-tool_call event → None (not actionable in v1)."""

    assert parse_hook_output("tool_result", _outcome(exit_code=2, stderr="x")) is None


async def test_parse_exit0_permission_deny_blocks() -> None:
    """#13 — permissionDecision deny + tool_call → block with reason."""

    stdout = (
        '{"hookSpecificOutput": {"permissionDecision": "deny", '
        '"permissionDecisionReason": "nope"}}'
    )
    result = parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "nope"


async def test_parse_exit0_decision_block() -> None:
    """#14 — top-level decision:block + tool_call → block with reason."""

    stdout = '{"decision": "block", "reason": "x"}'
    result = parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "x"


async def test_parse_exit0_permission_allow_observational() -> None:
    """#15 — permissionDecision allow → None (observational in v1)."""

    stdout = '{"hookSpecificOutput": {"permissionDecision": "allow"}}'
    assert parse_hook_output("tool_call", _outcome(exit_code=0, stdout=stdout)) is None


async def test_parse_exit0_empty_invalid_and_nondict_json() -> None:
    """#16 — empty stdout / invalid JSON / non-dict JSON all → None."""

    assert parse_hook_output("tool_call", _outcome(exit_code=0, stdout="")) is None
    assert (
        parse_hook_output("tool_call", _outcome(exit_code=0, stdout="{not json"))
        is None
    )
    assert (
        parse_hook_output("tool_call", _outcome(exit_code=0, stdout="true")) is None
    )


async def test_parse_exit1_non_blocking_returns_none() -> None:
    """#17 — exit 1 (non-blocking error) → None (fail-open)."""

    assert parse_hook_output("tool_call", _outcome(exit_code=1, stderr="boom")) is None


# === validate_subprocess_hook_event (18-21) ===


async def test_validate_valid_event_no_raise() -> None:
    """#18 — a valid allowlisted event does not raise."""

    validate_subprocess_hook_event("tool_call")


async def test_validate_unknown_event_raises() -> None:
    """#19 — unknown event → ExtensionManifestError."""

    try:
        validate_subprocess_hook_event("nope")
    except ExtensionManifestError as exc:
        assert "unknown hook event" in str(exc)
    else:
        raise AssertionError("expected ExtensionManifestError")


async def test_validate_known_but_not_allowlisted_raises() -> None:
    """#20 — known-but-not-allowlisted (message_update) → ExtensionManifestError."""

    try:
        validate_subprocess_hook_event("message_update")
    except ExtensionManifestError as exc:
        assert "message_update" in str(exc)
        assert "subprocess" in str(exc).lower()
    else:
        raise AssertionError("expected ExtensionManifestError")


async def test_allowlist_is_subset_of_hook_result_types() -> None:
    """#21 — module invariant: SUBPROCESS_HOOK_EVENTS <= HOOK_RESULT_TYPES."""

    assert set(HOOK_RESULT_TYPES) >= SUBPROCESS_HOOK_EVENTS


# === Loader wiring (22-24) ===


async def test_loader_hooks_only_plugin_loads(tmp_path: Path) -> None:
    """#22 — hooks-only plugin + shell_exec=true → loads, handler registered."""

    _write_hook_plugin(
        tmp_path,
        name="hookplug",
        event="tool_call",
        command="cat",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert result.errors == []
    assert len(result.extensions) == 1
    ext = result.extensions[0]
    assert ext.manifest is not None
    assert "tool_call" in ext.handlers
    assert len(ext.handlers["tool_call"]) == 1


async def test_loader_hooks_without_shell_exec_errors(tmp_path: Path) -> None:
    """#23 — hooks declared but shell_exec=false → ExtensionLoadError re shell_exec."""

    _write_hook_plugin(
        tmp_path,
        name="noshellplug",
        event="tool_call",
        command="cat",
        shell_exec=False,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert len(result.extensions) == 0
    assert len(result.errors) == 1
    assert "shell_exec" in result.errors[0].error


async def test_loader_unknown_hook_event_errors(tmp_path: Path) -> None:
    """#24 — hook on a non-allowlisted event → load error mentioning the event."""

    _write_hook_plugin(
        tmp_path,
        name="badeventplug",
        event="message_update",
        command="cat",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert len(result.extensions) == 0
    assert len(result.errors) == 1
    assert "message_update" in result.errors[0].error


# === End-to-end integration (25) ===


async def test_e2e_subprocess_deny_composes_with_reducer(tmp_path: Path) -> None:
    """#25 — a tool_call hook that denies blocks the in-process reducer.

    Loads a hooks-only plugin whose ``tool_call`` command prints a deny control
    JSON and exits 0. Builds a :class:`HookBus` from the loaded extension's
    handlers (mirror ``test_hooks.py`` wiring), emits a
    :class:`ToolCallHookEvent`, and asserts the reduced result is
    ``ToolCallResult(block=True)`` with the reason — proving the subprocess
    lane composes with the in-process reducer.
    """

    # Write the deny control JSON to a file and ``cat`` it — avoids nested
    # quoting that would break the TOML ``command`` string literal.
    deny_json = (
        '{"hookSpecificOutput":{"permissionDecision":"deny",'
        '"permissionDecisionReason":"blocked"}}'
    )
    deny_file = tmp_path / "deny.json"
    deny_file.write_text(deny_json, encoding="utf-8")
    _write_hook_plugin(
        tmp_path,
        name="denyplug",
        event="tool_call",
        command=f"cat {deny_file}",
        shell_exec=True,
    )
    result = await discover_and_load_extensions(
        [], cwd=tmp_path, agent_dir=tmp_path / "no_global"
    )
    assert result.errors == []
    assert len(result.extensions) == 1
    ext = result.extensions[0]

    runtime = result.runtime
    ctx = ExtensionContext(
        runtime,
        cwd=str(tmp_path),
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    bus = HookBus(ctx_factory=lambda: ctx)
    for handler in ext.handlers.get("tool_call", []):
        bus.on("tool_call", handler, error_mode="continue")

    reduced = await bus.emit(
        ToolCallHookEvent(tool_call_id="tc-1", tool_name="bash", args={"command": "rm"})
    )
    assert isinstance(reduced, ToolCallResult)
    assert reduced.block is True
    assert reduced.reason == "blocked"


# === Additional coverage (26-27) ===


async def test_run_subprocess_aelix_project_dir_injected(tmp_path: Path) -> None:
    """#26 — AELIX_PROJECT_DIR is set in the child env to the cwd value."""

    outcome = await run_hook_subprocess(
        'printf "%s" "$AELIX_PROJECT_DIR"',
        "",
        timeout_ms=2000,
        cwd=str(tmp_path),
    )
    assert outcome.exit_code == 0
    assert str(tmp_path) in outcome.stdout


async def test_make_subprocess_handler_fail_open_on_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#27 — handler returns None (never raises) when run_hook_subprocess raises."""

    async def _boom(*args: object, **kwargs: object) -> HookSubprocessOutcome:
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess_hooks, "run_hook_subprocess", _boom)

    contrib = HookContrib(event="tool_call", command="x", timeout_ms=1000)
    handler = make_subprocess_handler(contrib)

    event = ToolCallHookEvent(tool_call_id="tc-99", tool_name="bash", args={})
    ctx = _make_ctx(cwd="/tmp")
    result = await handler(event, ctx)
    assert result is None
