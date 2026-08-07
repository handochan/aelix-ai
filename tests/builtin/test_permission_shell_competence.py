"""AUTO mode must not auto-allow through a shell the bash grammar can't read (#104).

The classifier binds the tree-sitter BASH grammar and nothing else. Its verdict
was previously honoured whatever shell the bash tool would actually spawn, so
on Windows — where ``_resolve_shell`` now resolves PowerShell or ``cmd`` — a
PowerShell command line parsed as a run of harmless words and came back ALLOW.
That is mis-permissioning, not a missed detection: ``Remove-Item -Recurse
-Force C:\\`` would have run with no prompt.

These tests RUN on Linux. The resolved shell is injected at the seam the
permission gate uses, so the Windows verdicts are asserted rather than skipped.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_coding_agent.builtin.bash_classifier import is_classifiable_shell
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.tools import bash as bash_mod
from aelix_coding_agent.tools.bash import ShellConfig

_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
_PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
_CMD = r"C:\Windows\system32\cmd.exe"


@pytest.fixture
def resolved_shell(monkeypatch: pytest.MonkeyPatch):
    """Pin what ``_resolve_shell`` reports to the permission gate.

    The gate imports it lazily from ``tools.bash``, so patching the module
    attribute is what the call actually sees.
    """

    def _set(path: str) -> None:
        monkeypatch.setattr(
            bash_mod, "_resolve_shell", lambda *_a, **_k: ShellConfig(path, "-c")
        )

    return _set


class _FakeUI:
    def __init__(self) -> None:
        self.select_calls = 0

    async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
        self.select_calls += 1
        return "No"  # any prompt → block, so an auto-allow is unmistakable

    async def input(
        self, title: str, placeholder: str | None = None, opts: Any = None
    ) -> str | None:
        return None


class _FakeCtx:
    def __init__(self) -> None:
        self.has_ui = True
        self.ui = _FakeUI()
        self.cwd = "/proj"


def _bash_event(command: str) -> ToolCallHookEvent:
    return ToolCallHookEvent(tool_call_id="t1", tool_name="bash", args={"command": command})


# === is_classifiable_shell ==================================================


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/sh", "/usr/bin/zsh", "/bin/dash"])
def test_posix_family_is_classifiable(shell: str) -> None:
    assert is_classifiable_shell(shell) is True


@pytest.mark.parametrize("shell", [_POWERSHELL, _PWSH, _CMD, "/usr/bin/fish", "nu"])
def test_windows_and_exotic_shells_are_not_classifiable(shell: str) -> None:
    assert is_classifiable_shell(shell) is False


# === the gate ===============================================================


def _perm() -> PermissionExtension:
    return PermissionExtension(posture=PermissionPosture(PermissionMode.AUTO))


async def test_auto_still_allows_readonly_under_bash(resolved_shell) -> None:
    """The POSIX behaviour is untouched — this is the control."""

    resolved_shell("/bin/bash")
    ctx = _FakeCtx()
    assert await _perm()._on_tool_call(_bash_event("ls -la"), ctx) is None  # type: ignore[arg-type]
    assert ctx.ui.select_calls == 0


@pytest.mark.parametrize(
    "shell", ["/usr/local/bin/bash-5.2", "bash5", "/usr/bin/zsh-5.9", "ksh93"]
)
async def test_a_version_suffixed_posix_shell_still_auto_allows(
    resolved_shell, shell: str
) -> None:
    """A Homebrew/distro ``bash-5.2`` is a real bash and must not be demoted.

    Guards a narrow but real regression the force-ASK gate could otherwise
    introduce on a SHIPPING platform: the filename failed to match ``bash``, so
    AUTO mode prompted for every command a genuine bash was about to run.
    """

    resolved_shell(shell)
    ctx = _FakeCtx()

    assert await _perm()._on_tool_call(_bash_event("ls -la"), ctx) is None  # type: ignore[arg-type]
    assert ctx.ui.select_calls == 0


@pytest.mark.parametrize("shell", [_POWERSHELL, _PWSH, _CMD])
async def test_windows_shell_forces_ask_instead_of_auto_allow(
    resolved_shell, shell: str
) -> None:
    """``ls -la`` classifies ALLOW, but the shell that would run it isn't bash."""

    resolved_shell(shell)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(_bash_event("ls -la"), ctx)  # type: ignore[arg-type]

    assert result is not None and result.block  # prompted, and the fake said No
    assert ctx.ui.select_calls == 1


async def test_powershell_removal_is_not_silently_allowed(resolved_shell) -> None:
    """The concrete damage case: the bash grammar sees only harmless words."""

    resolved_shell(_POWERSHELL)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(
        _bash_event(r"Remove-Item -Recurse -Force C:\\"),  # type: ignore[arg-type]
        ctx,
    )

    assert result is not None and result.block
    assert ctx.ui.select_calls == 1


async def test_deny_is_still_honoured_on_a_non_bash_shell(resolved_shell) -> None:
    """DENY survives the downgrade: a bash-shaped destructive command is worth
    blocking whatever shell would run it. Only ALLOW is demoted to ASK."""

    resolved_shell(_POWERSHELL)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(_bash_event("rm -rf /"), ctx)  # type: ignore[arg-type]

    assert result is not None and result.block
    assert ctx.ui.select_calls == 0  # blocked outright, never prompted


async def test_shell_resolution_failure_falls_back_to_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-safe: an exception resolving the shell must not become an allow."""

    def _boom(*_a: Any, **_k: Any) -> ShellConfig:
        raise RuntimeError("no shell")

    monkeypatch.setattr(bash_mod, "_resolve_shell", _boom)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(_bash_event("ls -la"), ctx)  # type: ignore[arg-type]

    assert result is not None and result.block
    assert ctx.ui.select_calls == 1
