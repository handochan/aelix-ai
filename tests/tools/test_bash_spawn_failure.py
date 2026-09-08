"""A spawn that fails before the command runs is exit 127, not an exception (#243).

A SEPARATE FILE from ``test_bash_shell_win32.py`` on purpose. That file's own
docstring promises *"Every test here RUNS on Linux. The win32 arm is driven by
injecting ``platform='win32'``"* — currently exactly true (``grep -n skipif``
finds nothing in it) — and at ``test_bash_shell_win32.py:147-149`` it records
deliberately deleting a platform guard of this shape. A spawn VERDICT is the one
thing that injection cannot fake: whether ``Popen`` raises ``ENOEXEC`` for a file
that is not a loadable image is the operating system's answer, not a branch. So
the cases that need a real ``fork``/``CreateProcess`` live here, and this file's
contract is the opposite one: **some tests here are platform-guarded**, and each
guard says which platform it needs and why.

The ``fork``-free cases (3, 4, 5, 8 in the design's numbering) carry no guard, so
CI's windows-latest leg executes the allowlist, its complement, the errno-less
rule and the shared-identity invariant rather than inferring them.
"""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from aelix_ai.utils import _shell
from aelix_coding_agent.tools import bash as bash_mod

# Every member, spelled as a LITERAL and deliberately not
# ``sorted(NOT_A_RUNNABLE_SHELL)``. #227 measured why at the other spawn site:
# parametrizing over the set under test is self-referential, so deleting a
# member deletes its own case and the suite stays green (``6 passed`` where the
# literal goes RED).
_EVERY_MEMBER = [
    errno.ENOENT,
    errno.ENOTDIR,
    errno.EACCES,
    errno.EPERM,
    errno.ENOEXEC,
    errno.ELOOP,
    errno.EINVAL,
]


def _not_a_loadable_image(directory: Path) -> Path:
    """A file that exists, is executable, and is not a program.

    Named ``.exe`` so ONE test is real on both legs: POSIX ignores the
    extension and still answers ``ENOEXEC`` (measured on darwin — ``[Errno 8]
    Exec format error``), win32 needs it before ``CreateProcess`` will get as
    far as reading the header and answering ``ERROR_BAD_EXE_FORMAT``.
    """

    exe = directory / "notashell.exe"
    exe.write_bytes(b"this is not a program\n")
    exe.chmod(0o755)
    return exe


async def _exec(shell_path: str, cwd: str) -> tuple[int | None, bytes]:
    """Run one command through a bash-tool operations object.

    Callers that FAKE ``Popen`` pass :data:`sys.executable` rather than
    ``/bin/sh``: #241 (same release) made ``create_local_bash_operations``
    reject a ``shell_path`` that does not resolve, so on win32 the hardcoded
    POSIX path raised ``ValueError: Custom shell path not found: /bin/sh``
    before the fake could run — measured as 11 failures on both windows legs of
    CI run 34272507388. The spawn never happens in those cases, so the only
    thing the path has to be is present.
    """


    chunks: list[bytes] = []
    ops = bash_mod.create_local_bash_operations(shell_path)
    result = await ops.exec("echo hi", cwd, on_data=chunks.append, env={})
    return result.exit_code, b"".join(chunks)


# === the real thing: a spawn the operating system refuses ====================


async def test_a_shell_that_is_not_a_loadable_image_is_exit_127(
    tmp_path: Path,
) -> None:
    """The headline, with no mock anywhere: a real spawn of a real non-program.

    Measured RED before the fix on darwin — ``OSError [Errno 8] Exec format
    error`` escaped ``_LocalBashOperations.exec`` entirely, because the tuple it
    used to catch, ``(FileNotFoundError, NotADirectoryError)``, covers only
    ``ENOENT``/``ENOTDIR``. ``ENOEXEC`` has no ``OSError`` subclass at all.
    """

    exit_code, output = await _exec(str(_not_a_loadable_image(tmp_path)), str(tmp_path))

    assert exit_code == 127
    assert b"[bash] failed to spawn" in output
    # The ``: {exc}`` half is the payload — it is what tells the model and the
    # user WHY — and review measured it deletable while every assertion here
    # checked only the prefix (``53 passed`` with the message cut back to a bare
    # ``b"[bash] failed to spawn"``). Pinned as the bracket both legs produce
    # rather than as a number: POSIX says ``[Errno 8] Exec format error: '…'``
    # (measured on darwin), win32 phrases the same refusal as ``[WinError 193]``
    # (unmeasured here — see the win32 case at the bottom of this file).
    assert b"[Errno " in output or b"[WinError " in output


@pytest.mark.skipif(
    os.name != "posix", reason="chmod does not gate executability on win32"
)
async def test_a_shell_that_is_not_executable_is_exit_127(tmp_path: Path) -> None:
    """``EACCES`` -> ``PermissionError``, also uncaught by the old tuple.

    Without this case, widening that tuple to
    ``(FileNotFoundError, NotADirectoryError, PermissionError)`` — the spelling
    #227 measured and rejected at the other spawn site, because it leaves the
    headline ``ENOEXEC`` escaping — would be indistinguishable from the fix.
    """

    shell = tmp_path / "noexec.sh"
    shell.write_bytes(b"#!/bin/sh\necho hi\n")
    shell.chmod(0o644)

    exit_code, output = await _exec(str(shell), str(tmp_path))

    assert exit_code == 127
    assert b"[bash] failed to spawn" in output


@pytest.mark.skipif(os.name != "posix", reason="0o000 does not deny entry on win32")
async def test_an_unreadable_cwd_is_also_exit_127(tmp_path: Path) -> None:
    """The verdict is on the SPAWN, whose inputs are argv[0] AND ``cwd``.

    A MISSING ``cwd`` already returned 127 before this change (``ENOENT``, via
    the old tuple), while an unreadable one escaped as ``PermissionError``;
    measured both on darwin. Widening by errno makes the two agree, which is the
    reason the message and the CHANGELOG say "a spawn that fails before the
    command starts" rather than "a shell Aelix cannot run". This is the only
    test that catches a later re-narrowing to a shell-only allowlist.
    """

    denied = tmp_path / "noperm"
    denied.mkdir()
    denied.chmod(0o000)
    try:
        exit_code, output = await _exec("/bin/sh", str(denied))
    finally:
        # Restored unconditionally: pytest's tmp_path cleanup cannot remove a
        # directory it may not enter.
        denied.chmod(0o755)

    assert exit_code == 127
    assert b"[bash] failed to spawn" in output


# === the allowlist and its complement, on every platform =====================


@pytest.mark.parametrize("code", _EVERY_MEMBER)
async def test_every_not_a_runnable_shell_errno_becomes_exit_127(
    code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each member is a separate way back to an exception out of the tool.

    Faked rather than provoked because most of these errnos cannot be produced
    on demand from a POSIX box (``ELOOP`` needs a symlink cycle, ``EINVAL`` is
    where ``errmap.h``'s ``default:`` arm sends every winerror CPython's table
    does not name). The two that CAN be provoked have real-spawn cases above.
    """

    def fake_popen(argv, **_kwargs):  # noqa: ANN001, ANN003, ARG001
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    exit_code, output = await _exec(sys.executable, str(tmp_path))

    assert exit_code == 127
    assert b"[bash] failed to spawn" in output
    # And the exception's own text reaches the caller. This is the cheapest home
    # for that pin: the ``OSError`` is constructed here, so the expected string
    # is exact on windows-latest too, and one line covers all seven members.
    assert os.strerror(code).encode() in output


@pytest.mark.parametrize("code", [errno.EMFILE, errno.ENOMEM, errno.EBADF])
async def test_a_spawn_error_that_is_not_a_missing_shell_still_escapes(
    code: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowness is deliberate, and this is what pins it.

    A full descriptor table or an out-of-memory host is not "command not
    found" — and 127 is exactly how a model reads it, so it would retry against
    a machine that cannot spawn anything. Without this case, a bare
    ``except OSError`` -> 127 would look like a green simplification.
    """

    def fake_popen(argv, **_kwargs):  # noqa: ANN001, ANN003, ARG001
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(OSError) as excinfo:
        await _exec(sys.executable, str(tmp_path))
    assert excinfo.value.errno == code


async def test_a_spawn_error_with_no_errno_still_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing the old class tuple caught and the errno guard does not.

    ``FileNotFoundError("cmd.exe")`` — the one-argument form — has ``errno
    None`` (measured), and ``None not in frozenset[int]`` is True, so it
    re-raises. That is the wanted answer: a ``Popen`` failure carrying no errno
    is not a verdict on the shell. Pinned here so the next reader does not
    "fix" the guard to ``exc.errno is None or exc.errno in …`` and silently
    re-widen it to catch anything at all.
    """

    def fake_popen(argv, **_kwargs):  # noqa: ANN001, ANN003, ARG001
        raise FileNotFoundError("cmd.exe")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(FileNotFoundError) as excinfo:
        await _exec(sys.executable, str(tmp_path))
    assert excinfo.value.errno is None


def test_both_spawn_sites_share_one_allowlist() -> None:
    """Identity, not equality — #243 exists to have ONE of these.

    Equality would stay green after someone re-typed the frozenset literal into
    a second module, which is the exact regression this issue fixes: #227
    measured that dropping five members from a duplicate was invisible to the
    whole suite (10268 passed). ``EINVAL`` is also why the set must not be
    forked *by subtraction* at either site — see the constant's own comment.
    """

    import aelix_ai.oauth._resolve_config as rc

    assert bash_mod.NOT_A_RUNNABLE_SHELL is _shell.NOT_A_RUNNABLE_SHELL
    assert rc.NOT_A_RUNNABLE_SHELL is _shell.NOT_A_RUNNABLE_SHELL


# === win32 evidence, asserting nothing =======================================


@pytest.mark.skipif(sys.platform != "win32", reason="a real CreateProcess verdict")
async def test_win32_spawn_failures_are_recorded(tmp_path: Path) -> None:
    """Evidence into the ``-q`` job log; the behaviour is asserted above.

    Asserting the NUMBER would fail for a reason unrelated to the fix if
    ``CreateProcess`` answered something else, while the behaviour stayed
    correct: the plausible alternatives (``ERROR_ACCESS_DENIED`` -> ``EACCES``;
    ``ERROR_INVALID_PARAMETER`` 87 and everything else on ``errmap.h``'s
    ``default:`` arm -> ``EINVAL``) are both in the allowlist too. So the first
    case above asserts 127 on this leg and this one only records what the
    message actually said. Precedent: ``test_resolve_config.py``'s ``#227 cmd
    floor`` warning.

    The ``.cmd`` shim is the open question of the design: ``CreateProcessW``
    with a NULL ``lpApplicationName`` — what CPython passes when handed a list —
    launches batch files through the command interpreter, which is the basis of
    the 2024 BatBadBut class (CVE-2024-24576), so a ``sh.cmd`` most likely
    SPAWNS rather than failing. Unmeasured until this runs.
    """

    exit_code, output = await _exec(str(_not_a_loadable_image(tmp_path)), str(tmp_path))
    warnings.warn(f"#243 win32 non-PE: {exit_code} {output!r}", stacklevel=1)

    shim = tmp_path / "shim.cmd"
    shim.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    try:
        shim_exit, shim_output = await _exec(str(shim), str(tmp_path))
    except OSError as exc:
        # Guarded because this leg is the UNKNOWN one: if ``CreateProcess``
        # refuses a ``.cmd`` with an errno outside the allowlist, the product
        # re-raises by design (``bash.py`` — everything else still escapes) and
        # an unguarded call would turn this evidence-only test red for a reason
        # unrelated to the fix, which is exactly what the docstring above
        # promises it cannot do. The errno is the answer, so it is recorded.
        warnings.warn(
            f"#243 win32 .cmd shim RAISED: {exc!r} errno={exc.errno}", stacklevel=1
        )
    else:
        warnings.warn(
            f"#243 win32 .cmd shim: {shim_exit} {shim_output!r}", stacklevel=1
        )
