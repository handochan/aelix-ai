"""AUTO mode must not auto-allow through a shell the bash grammar can't read (#104).

The classifier binds the tree-sitter BASH grammar and nothing else. Its verdict
was previously honoured whatever shell the bash tool would actually spawn. A
destructive cmdlet name like ``Remove-Item -Recurse -Force C:\\`` was never the
risk — it matches no table and already falls to the unknown-command ASK. The
real mis-permissioning was the opposite shape: a KNOWN read-only name (``date``,
``sort``) whose arguments the ALLOW tier did not read, and whose meaning changes
under cmd (``date <value>`` sets the clock; ``sort /O <file>`` writes a file).
``0be16cd`` narrowed those names; the gate below is the backstop for the same
shape. ALLOW is the only tier whose name-alone decision is permissive (DENY and
ASK also match on the bare name, but in the fail-safe direction), so a verdict
the bash grammar produced for a shell it does not describe was not merely
unknown, it was misleading — and the ALLOW got downgraded to ASK.

**#204 replaced that downgrade for PowerShell and ``cmd``, and this module's
tests were rewritten around the replacement.** ``permission.py:685-711`` now
reads the DIALECT off the resolved shell: ``pwsh``/``powershell``/``cmd`` get
their own classifier with the bash DENY kept as a floor and ``competent`` set
outright, so ``is_classifiable_shell`` is never consulted for them and ``dir``
under ``cmd.exe`` reaches ALLOW (``test_cmd_read_only_internals_are_auto_allowed``
below). The downgrade is still the whole story for every OTHER unreadable
shell — ``fish``, and anything unresolvable — which
``test_dialect_dispatch.py::test_an_unknown_dialect_shell_keeps_todays_precheck``
pins, and ``is_classifiable_shell`` still means what it meant
(``test_windows_and_exotic_shells_are_not_classifiable`` below): a claim about a
grammar, not about a platform.

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
    """``ls -la`` classifies ALLOW to the BASH grammar, and still prompts here.

    The assertion is unchanged by #204; the REASON is now different per shell
    rather than one blanket downgrade, and after slice 3 no shell here reaches
    the downgrade at all — each dialect classifier answers for itself. Under
    PowerShell ``ls`` resolves to ``Get-ChildItem`` and ``-la`` prefixes no
    allowlisted parameter. Under ``cmd`` there is no ``ls`` name in any tier,
    so it falls to the unknown-name ASK. Both are ASK on their own merits.
    """

    resolved_shell(shell)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(_bash_event("ls -la"), ctx)  # type: ignore[arg-type]

    assert result is not None and result.block  # prompted, and the fake said No
    assert ctx.ui.select_calls == 1


async def test_powershell_removal_is_not_silently_allowed(resolved_shell) -> None:
    """The verdict got STRONGER: a block with no prompt, not a prompt (#204).

    The name stays true and the body had to change. Its old claim —
    ``Remove-Item -Recurse -Force C:\\`` "matches no classifier table" — was a
    fact about this repo when only the bash grammar existed (the corrected
    comment at bash_classifier.py:86-98 records that it really did fall to the
    unknown-command ASK, and that the mis-permissioning was elsewhere). Since
    slice 2 of #204 there IS a table that matches it: a recursive-force delete
    whose target is a drive root is DENY under the PowerShell dialect, so the
    user is never asked to approve it.
    """

    resolved_shell(_POWERSHELL)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(
        _bash_event(r"Remove-Item -Recurse -Force C:\\"),  # type: ignore[arg-type]
        ctx,
    )

    assert result is not None and result.block
    assert ctx.ui.select_calls == 0  # blocked outright, never prompted


async def test_cmd_read_only_internals_are_auto_allowed(resolved_shell) -> None:
    """AUTO mode stops prompting for ``dir`` under ``cmd`` — #204's deliverable.

    ``ask`` on ``main`` c6d424c, for a reason that was never about ``dir``:
    ``is_classifiable_shell("cmd.exe")`` is False, so the ALLOW tier's verdict
    was downgraded whatever it said. Slice 3 gives ``cmd`` a grammar of its own
    and the downgrade stops being the answer.
    """

    resolved_shell(_CMD)
    ctx = _FakeCtx()

    assert await _perm()._on_tool_call(_bash_event("dir"), ctx) is None  # type: ignore[arg-type]
    assert ctx.ui.select_calls == 0


async def test_cmd_recursive_delete_is_not_silently_allowed(resolved_shell) -> None:
    """``del /s /q C:\\Windows`` blocks with no prompt (#204).

    The counterpart of ``test_powershell_removal_is_not_silently_allowed``, and
    it corrects the same false premise: this command never DID auto-allow — it
    matched no table and fell to the unknown-command ASK (the corrected comment
    at ``bash_classifier.py:86-98``). What changed is that there is now a table
    that matches it, so the user is never asked to approve it. ``del x`` without
    ``/s`` stays a prompt, deliberately (#204 F13).
    """

    resolved_shell(_CMD)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(
        _bash_event(r"del /s /q C:\Windows"),  # type: ignore[arg-type]
        ctx,
    )

    assert result is not None and result.block
    assert ctx.ui.select_calls == 0


@pytest.mark.parametrize("shell", [_PWSH, _CMD])
async def test_a_posix_deny_still_blocks_under_both_windows_dialects(
    resolved_shell, shell: str
) -> None:
    """Criterion 4, end to end and on BOTH new dialects.

    ``rm`` is not a cmd name and ``rm -rf /`` is ASK to the cmd classifier, so
    the block here comes from the bash classifier's DENY kept as a FLOOR in
    ``_auto_classify_bash``. That floor is the compensating control the whole
    loosening is conditioned on: the dialect classifiers may raise ASK to ALLOW
    and may ADD verdicts, and can never lower a DENY.
    """

    resolved_shell(shell)
    ctx = _FakeCtx()
    result = await _perm()._on_tool_call(_bash_event("rm -rf /"), ctx)  # type: ignore[arg-type]

    assert result is not None and result.block
    assert ctx.ui.select_calls == 0


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
