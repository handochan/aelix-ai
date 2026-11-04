"""#657 — the argument-reading classifier change, exercised through the REAL
permission gate and the RPC ``permissions/evaluate`` entry point.

``test_bash_classifier.py`` pins the pure ``classify`` verdicts; this file
proves the gate ACTS on them: under ``auto`` a newly-ASKed git form prompts
interactively and blocks a headless delegated child, while a plain ``git
status`` still runs without a prompt. It also pins the injected
``safe.bareRepository`` env guard the bash tool adds to the model's child.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.builtin.permission import (
    PermissionExtension,
    evaluate_tool_call,
)
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.tools import bash as bash_module
from aelix_coding_agent.tools.bash import (
    ShellConfig,
    _inject_git_bare_repository_guard,
    create_bash_tool,
)
from aelix_coding_agent.tui.approval_dialog import ApprovalDecision, ApprovalRequest

from tests.builtin.gate_tools import BUILTIN_CONTEXT


class _Recorder:
    """Counts approval prompts; answers NO so an ASK becomes a visible block."""

    def __init__(self) -> None:
        self.asked = 0

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        self.asked += 1
        return ApprovalDecision.NO


class _Ctx:
    def __init__(self, *, has_ui: bool, cwd: str = "/proj") -> None:
        self.has_ui = has_ui
        self.ui: Any = _Ui() if has_ui else None
        self.cwd = cwd


class _Ui:
    """A minimal UI that records whether the bash prompt was shown (answers No)."""

    def __init__(self) -> None:
        self.select_calls = 0

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.select_calls += 1
        return "No"

    async def input(self, *a: Any, **k: Any) -> str | None:
        return None


def _event(command: str) -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="bash",
        args={"command": command},
        context=BUILTIN_CONTEXT,
    )


@pytest.fixture
def bash_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the shell the gate resolves to bash, so it judges by the bash grammar.

    windows-latest has no ``$SHELL``, so the win32 chain resolves PowerShell,
    whose dialect asks on ``git status`` and every row here would ask for that
    reason alone (PR #688's first CI run). The gate imports ``_resolve_shell``
    lazily from ``tools.bash``, as ``test_permission_shell_competence`` pins it.
    """

    monkeypatch.setattr(
        bash_module, "_resolve_shell", lambda *_a, **_k: ShellConfig("/bin/bash", "-c")
    )


# A plain read that must STILL run untouched, and the newly-ASKed forms.
_STILL_ALLOW = "git status"
_NOW_ASK = [
    "git -c core.fsmonitor=./evil.sh status",
    "git -c user.name=x log",
    "git -C vendor ls-remote origin",
    "GIT_DIR=cache git status",
    "git ls-remote origin",
    "git config core.hooksPath hooks",
    "env touch x",
    "LD_PRELOAD=./x.so cat a",
    # Review round 1: each was ALLOW (ran with no prompt) on 1023850d.
    "git --bare status",
    "export GIT_DIR=cache; git status",
    'GIT_CONFIG_KEY_0=core.fsmonitor; GIT_CONFIG_VALUE_0="sh ./mark.sh"; git status',
    "echo $((GIT_CONFIG_COUNT=0)); git status",
    'git config --comment --get core.fsmonitor "sh ./external.sh"',
    "git diff --no-index --out\\put=tool.out a b",
    "uniq - out",
    # Review round 2: each was ALLOW (ran with no prompt) on 9fb15a12.
    "echo $path[GIT_CONFIG_COUNT=0]; git status",  # B1 zsh subscript
    "test -v $(cat f); git status",  # B2 a word the shell evaluates as an operand
    "true <<-EOF\n\t$((GIT_CONFIG_COUNT=0))\n\tEOF\ngit status",  # B3 unparsed body
    "git log --decorate-refs -- --output=../x",  # B4 a value option swallows --
    "git blame -- a.txt --output=../x",  # B4 blame re-reads its legacy -- form
    "tree -aI -- -o V .",  # B4 tree has no -- terminator
    "file --mag -- -C",  # B4 an abbreviated value option swallows --
]
# Reads that run unasked again. Round 1 (F09/F10) and round 2's should-fix.
_ALLOW_AGAIN = [
    "git branch -av",
    "git config user.name",
    "git remote get-url origin",
    "git ls-files *.md",
    "fd -tx",
    "declare -p",
]


@pytest.mark.usefixtures("bash_gate")
@pytest.mark.parametrize("command", [_STILL_ALLOW, *_ALLOW_AGAIN])
async def test_auto_interactive_allows_plain_git_status_without_a_prompt(command: str) -> None:
    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    ctx = _Ctx(has_ui=True)
    result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
    assert result is None
    assert ctx.ui.select_calls == 0


@pytest.mark.usefixtures("bash_gate")
@pytest.mark.parametrize("command", _NOW_ASK)
async def test_auto_interactive_prompts_on_the_new_forms(command: str) -> None:
    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.AUTO))
    ctx = _Ctx(has_ui=True)
    result = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
    # The gate reached the interactive prompt (ALLOW would have returned None
    # with no prompt). Answered "No" → blocked.
    assert ctx.ui.select_calls == 1, command
    assert result is not None and result.block, command


@pytest.mark.usefixtures("bash_gate")
async def test_auto_headless_child_allows_plain_status_blocks_the_new_forms() -> None:
    """A delegated child (ADR-0197 §(e)) has no UI and defaults to block."""

    perm = PermissionExtension(
        posture=PermissionPosture(mode=PermissionMode.AUTO), headless_default="block"
    )
    ctx = _Ctx(has_ui=False)
    allowed = await perm._on_tool_call(_event(_STILL_ALLOW), ctx)  # type: ignore[arg-type]
    assert allowed is None
    for command in _NOW_ASK:
        blocked = await perm._on_tool_call(_event(command), ctx)  # type: ignore[arg-type]
        assert isinstance(blocked, ToolCallResult) and blocked.block, command


@pytest.mark.usefixtures("bash_gate")
def test_rpc_evaluate_matches_the_gate() -> None:
    """``permissions/evaluate`` shares ``_auto_classify_bash`` — same verdicts."""

    assert evaluate_tool_call(PermissionMode.AUTO, "bash", {"command": _STILL_ALLOW}, "/proj") == "allow"
    for command in _NOW_ASK:
        assert (
            evaluate_tool_call(PermissionMode.AUTO, "bash", {"command": command}, "/proj")
            == "ask"
        ), command


def test_bash_tool_injects_bare_repository_guard_into_child_env() -> None:
    """The tool's child env carries the appended ``safe.bareRepository=explicit``.

    Not through the REPL ``!`` escape or raw ``operations.exec`` — only the
    tool's own spawn env, so the guard is the helper's responsibility to add.
    """

    env: dict[str, str] = {"PATH": "/usr/bin"}
    _inject_git_bare_repository_guard(env)
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "safe.bareRepository"
    assert env["GIT_CONFIG_VALUE_0"] == "explicit"


def test_bare_repository_guard_appends_without_clobbering_user_entries() -> None:
    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "preset",
    }
    _inject_git_bare_repository_guard(env)
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "user.name"  # kept
    assert env["GIT_CONFIG_VALUE_0"] == "preset"
    assert env["GIT_CONFIG_KEY_1"] == "safe.bareRepository"
    assert env["GIT_CONFIG_VALUE_1"] == "explicit"


@pytest.mark.parametrize("count", ["not-a-number", "-1", "1 ", "0x1", "1.0"])
def test_bare_repository_guard_leaves_a_count_git_refuses(count: str) -> None:
    """F08: git stops with "bogus count" / "too many entries" (measured, 2.54)
    before it runs anything, so the env is left as is — and the user's slot 0
    is never overwritten (1023850d reset onto it)."""

    env = {"GIT_CONFIG_COUNT": count, "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "u"}
    before = dict(env)
    _inject_git_bare_repository_guard(env)
    assert env == before


@pytest.mark.parametrize(("count", "index"), [("", 0), (" 1", 1), ("+1", 1), ("01", 1)])
def test_bare_repository_guard_reads_the_count_as_git_does(count: str, index: int) -> None:
    env = {"GIT_CONFIG_COUNT": count}
    if index:
        env |= {"GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "u"}
    _inject_git_bare_repository_guard(env)
    assert env["GIT_CONFIG_COUNT"] == str(index + 1)
    assert env[f"GIT_CONFIG_KEY_{index}"] == "safe.bareRepository"
    assert env.get("GIT_CONFIG_KEY_0") == ("user.name" if index else "safe.bareRepository")


@pytest.mark.parametrize(
    "env",
    [
        {"GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "u"},
        {"GIT_CONFIG_COUNT": "0", "GIT_CONFIG_VALUE_0": "u"},
        {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "a.b", "GIT_CONFIG_KEY_1": "c.d"},
    ],
)
def test_bare_repository_guard_never_overwrites_an_occupied_slot(env: dict[str, str]) -> None:
    """F08: an entry already sitting at index COUNT is someone's; it is kept."""

    before = dict(env)
    _inject_git_bare_repository_guard(env)
    assert env == before


def test_bare_repository_guard_folds_case_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """F07: a spawn hook's mixed-case names are the same variables on Windows."""

    monkeypatch.setattr(bash_module, "_env_names_fold_case", lambda: True)
    env = {"git_config_count": "1", "git_config_key_0": "user.name", "git_config_value_0": "u"}
    _inject_git_bare_repository_guard(env)
    assert env == {
        "git_config_key_0": "user.name",
        "git_config_value_0": "u",
        "GIT_CONFIG_KEY_1": "safe.bareRepository",
        "GIT_CONFIG_VALUE_1": "explicit",
        "GIT_CONFIG_COUNT": "2",
    }
    # Two spellings of one name: which one the child would see is unknowable.
    clash = {"GIT_CONFIG_COUNT": "1", "Git_Config_Count": "2"}
    _inject_git_bare_repository_guard(clash)
    assert clash == {"GIT_CONFIG_COUNT": "1", "Git_Config_Count": "2"}


def test_bare_repository_guard_is_case_sensitive_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bash_module, "_env_names_fold_case", lambda: False)
    env = {"git_config_count": "5"}
    _inject_git_bare_repository_guard(env)
    assert env["GIT_CONFIG_COUNT"] == "1" and env["git_config_count"] == "5"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git on PATH")
async def test_the_bash_tool_child_reads_safe_bare_repository_explicit(tmp_path: Any) -> None:
    """S1: the REAL tool's child sees the guard — not just the helper in isolation."""

    tool = create_bash_tool(str(tmp_path))
    assert tool.execute is not None
    result = await tool.execute(
        {"command": "git config --get safe.bareRepository"},
        ToolExecutionContext(tool_call_id="t657"),
    )
    text = "".join(getattr(part, "text", "") for part in result.content)
    assert not result.is_error, text
    assert text.strip().splitlines()[0] == "explicit"


def test_bash_tool_is_constructible_with_the_guard() -> None:
    """Smoke: the helper is wired into the tool's execute path (import-safe)."""

    tool = create_bash_tool("/proj")
    assert tool.name == "bash"
