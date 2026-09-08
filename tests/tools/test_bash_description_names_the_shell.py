"""#239 — on Windows the bash tool tells the model which shell it resolved.

Cost of the gap, from the issue: the model sent ``uv --version && uv cache
dir``, PowerShell answered ``InvalidEndOfLine``, and a whole turn went on
discovering ``;``. The tool's PARAMETERS have been per-tool since #11 (the
``timeout`` text states the resolved default and cap); only the description was
a frozen literal naming no shell at all.

Driven through ``_bash_shell_sentence``'s ``platform=`` seam, the one
``_resolve_shell``'s own docstring supports: ``monkeypatch.setattr(sys,
"platform", "win32")`` crashes inside ``shutil.which``, which branches on
``sys.platform`` and then calls ``_winapi`` — ``None`` off Windows (#222
critique WIN32-8, recorded at ``tests/tools/test_bash_tool.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_coding_agent.tools import bash
from aelix_coding_agent.tools.bash import (
    _BASH_DESCRIPTION,
    _bash_shell_sentence,
    create_bash_tool,
)

# The description as it stood before #239, frozen here. On POSIX the tool's
# description must still be exactly this string — the win32 resolution sits
# behind a platform test taken BEFORE ``_resolve_shell`` is called, so there is
# no filesystem probe and no ``ValueError`` risk off Windows.
_PRE_239_DESCRIPTION = (
    "Execute a bash command in the current working directory. Returns "
    "stdout and stderr. Output is truncated to last 2000 lines or 50KB "
    "(whichever is hit first). If truncated, full output is saved to a "
    "temp file. Provide a timeout in seconds for long-running commands "
    "(see the timeout parameter)."
)


def _shell_file(tmp_path: Path, name: str) -> str:
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def test_the_frozen_literal_is_still_the_frozen_literal() -> None:
    assert _BASH_DESCRIPTION == _PRE_239_DESCRIPTION


@pytest.mark.parametrize("name", ["powershell.exe", "pwsh.exe", "pwsh-6.2.exe"])
def test_the_powershell_arm_advises_the_semicolon_and_never_the_double_ampersand(
    tmp_path: Path, name: str
) -> None:
    """``;`` is right on 5.1, 6 and 7; ``&&`` is a 7.0 feature.

    "The name is the version" does not hold: PowerShell 6.0-6.2 also ship as
    ``pwsh``/``pwsh.exe`` and reject ``&&`` with 5.1's parser error, and
    ``shell_basename`` strips version suffixes so ``pwsh-6.2.exe`` lands on this
    same arm. Advising ``;`` needs no version probe and is correct on all three;
    a ``$PSVersionTable`` probe would buy one operator for a spawn per tool
    creation.
    """

    sentence = _bash_shell_sentence({}, _shell_file(tmp_path, name), platform="win32")

    assert name in sentence
    assert "PowerShell" in sentence
    assert "';'" in sentence
    assert "&&" not in sentence


def test_the_cmd_arm_names_cmd_and_still_never_says_double_ampersand(tmp_path: Path) -> None:
    sentence = _bash_shell_sentence({}, _shell_file(tmp_path, "cmd.exe"), platform="win32")

    assert "cmd.exe" in sentence
    assert "&&" not in sentence


def test_an_unrecognised_win32_shell_is_still_named(tmp_path: Path) -> None:
    """A Git-Bash ``$SHELL`` on Windows resolves a real ``bash``.

    Naming it is still worth a sentence — it is the case where the model's
    POSIX habits are correct, and saying so is cheaper than letting it guess.
    """

    sentence = _bash_shell_sentence({}, _shell_file(tmp_path, "bash.exe"), platform="win32")

    assert "bash.exe" in sentence
    assert "&&" not in sentence


def test_posix_gets_no_sentence_and_no_shell_resolution(tmp_path: Path) -> None:
    """The platform test comes first, so nothing is probed off win32."""

    assert _bash_shell_sentence({}, _shell_file(tmp_path, "pwsh.exe"), platform="linux") == ""
    assert _bash_shell_sentence({}, None, platform="darwin") == ""


def test_a_missing_custom_shell_path_still_creates_a_tool(tmp_path: Path) -> None:
    """Tool CREATION must not start failing where only exec failed before.

    ``_resolve_shell`` raises ``ValueError: Custom shell path not found`` for a
    ``shell_path`` setting that points nowhere. Before #239 that surfaced at the
    first bash call; the sentence must not move it to startup.
    """

    tool = create_bash_tool(str(tmp_path), {"shell_path": str(tmp_path / "nope.exe")})

    assert tool.description.startswith(_PRE_239_DESCRIPTION)


def test_a_supplied_operations_gets_no_sentence(tmp_path: Path) -> None:
    """A remote/SSH ``operations`` runs a shell this process cannot name."""

    class _Ops:
        async def exec(self, command, cwd, **kwargs):  # pragma: no cover - never called
            raise AssertionError

    tool = create_bash_tool(str(tmp_path), {"operations": _Ops()})

    assert tool.description == _PRE_239_DESCRIPTION


def test_a_supplied_spawn_hook_gets_no_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#239 cross-review finding 7: a hook can make the named shell the wrong one.

    The sentence is resolved ONCE, at creation, from ``get_shell_env()``.
    ``_resolve_spawn_context`` calls ``spawn_hook(base)`` on every request and
    the hook may rewrite ``PATH`` or ``COMSPEC`` — ``tests/tools/
    test_bash_tool.py``'s parity cases do exactly that — so the shell the
    description names need not be the shell that runs. The tool then tells the
    model to write PowerShell for a ``cmd`` that will parse it, which is worse
    than the frozen description that named no shell at all.

    The platform is forced here for the same reason and with the same safety as
    ``test_a_shell_env_that_raises_still_creates_a_tool``: the stub stands in
    for ``get_shell_env`` before anything can reach ``shutil.which``'s
    ``_winapi``. ``calls == []`` is the load-bearing half — it witnesses the
    conjunct at the CALL SITE, so deleting it fails here even though
    ``contextlib.suppress(Exception)`` would hide what came next.
    """

    calls: list[int] = []
    monkeypatch.setattr(bash.sys, "platform", "win32")
    monkeypatch.setattr(bash, "get_shell_env", lambda: calls.append(1) or {})

    tool = create_bash_tool(str(tmp_path), {"spawn_hook": lambda ctx: ctx})

    assert tool.description == _PRE_239_DESCRIPTION
    assert calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX byte-identity claim")
def test_on_posix_the_description_is_byte_identical_to_before(tmp_path: Path) -> None:
    assert create_bash_tool(str(tmp_path)).description == _PRE_239_DESCRIPTION


@pytest.mark.skipif(sys.platform != "win32", reason="the win32 end-to-end answer")
def test_on_win32_the_description_names_the_resolved_shell(tmp_path: Path) -> None:
    description = create_bash_tool(str(tmp_path)).description

    assert description.startswith(_PRE_239_DESCRIPTION)
    assert len(description) > len(_PRE_239_DESCRIPTION)
    assert "&&" not in description


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX no-probe claim")
def test_posix_does_not_even_read_the_shell_env_at_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``sys.platform == "win32"`` conjunct at the CALL SITE, pinned (#239 review).

    ``_bash_shell_sentence`` tests the platform itself, so the conjunct is
    redundant for the string — but not for the work: without it every tool
    creation off Windows calls ``get_shell_env()``, which walks the
    environment. The review measured that removing it left the suite green, so
    the intent needed a witness.
    """

    calls: list[int] = []
    monkeypatch.setattr(bash, "get_shell_env", lambda: calls.append(1) or {})

    create_bash_tool(str(tmp_path))

    assert calls == []


def test_a_shell_env_that_raises_still_creates_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``contextlib.suppress(Exception)`` around the sentence, pinned.

    It is unreachable off Windows because of the conjunct above, so the only
    way to witness it is to force the platform — which is safe HERE and nowhere
    else in this file, because ``get_shell_env`` raises before anything that
    would reach ``shutil.which``'s ``_winapi``.
    """

    def _boom() -> dict[str, str]:
        raise RuntimeError("no environment on this box")

    monkeypatch.setattr(bash.sys, "platform", "win32")
    monkeypatch.setattr(bash, "get_shell_env", _boom)

    assert create_bash_tool(str(tmp_path)).description == _PRE_239_DESCRIPTION
