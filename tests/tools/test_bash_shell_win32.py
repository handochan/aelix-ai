"""Windows-asserting tests for the bash tool's shell resolution (#104).

Every test here RUNS on Linux. The win32 arm is driven by injecting
``platform="win32"``, and the PATH probes use real executable files under
``tmp_path`` so ``shutil.which`` does the real work rather than being stubbed.

Injection rather than ``monkeypatch.setattr(sys, "platform", "win32")``:
``shutil.which`` itself branches on ``sys.platform`` and then calls ``_winapi``,
which is ``None`` off Windows, so patching the global raises ``AttributeError``
inside the very PATH probe these tests exist to cover.
"""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_ai.utils._shell import (
    POWERSHELL_UTF8_PREAMBLE,
    command_flag_for,
    shell_basename,
    utf8_output_preamble,
)
from aelix_coding_agent.builtin.bash_classifier import is_classifiable_shell
from aelix_coding_agent.tools import bash as bash_mod
from aelix_coding_agent.tools.bash import ShellConfig, _resolve_shell


def _executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _on_path(directory: Path, name: str) -> Path:
    """A PATH probe target that ``shutil.which(name)`` can actually find here.

    Windows' ``shutil.which`` only tries ``name + PATHEXT`` candidates and
    never the bare name, so an extensionless fixture is unreachable there and
    the win32 arm slides on to ``%COMSPEC%``. On POSIX the bare name is the
    only candidate, so the extension has to follow the running platform.
    """

    return _executable(directory, f"{name}.exe" if sys.platform == "win32" else name)


# === the bug: win32 had no arm and fell through the POSIX chain =============


def test_win32_never_resolves_a_posix_shell(tmp_path: Path) -> None:
    """Regression for the actual #104 failure mode.

    With no win32 arm the chain ran ``$SHELL`` → ``/bin/bash`` → ``bash`` on
    PATH → ``/bin/sh``, so on a stock Windows box every bash call resolved
    ``/bin/sh`` — a path that does not exist there — and the tool returned exit
    127 for *every* command.
    """

    resolved = _resolve_shell({"PATH": str(tmp_path / "empty")}, platform="win32")

    assert not resolved.path.startswith("/bin/")
    assert resolved == ShellConfig("cmd.exe", "/c")


# === the win32 chain: $SHELL → pwsh → powershell → %COMSPEC% → cmd.exe ======


def test_win32_prefers_pwsh(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    pwsh = _on_path(bin_dir, "pwsh")

    resolved = _resolve_shell(
        {"PATH": str(bin_dir), "COMSPEC": r"C:\Windows\system32\cmd.exe"},
        platform="win32",
    )

    # Compared as ``Path``: on Windows ``which`` echoes back PATHEXT's own
    # casing (``pwsh.EXE``), and only path equality is case-insensitive there.
    assert Path(resolved.path) == pwsh
    assert resolved.command_flag == "-Command"


def test_win32_falls_back_to_windows_powershell(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    powershell = _on_path(bin_dir, "powershell")

    resolved = _resolve_shell({"PATH": str(bin_dir)}, platform="win32")

    assert Path(resolved.path) == powershell
    assert resolved.command_flag == "-Command"


def test_win32_falls_back_to_comspec_with_the_cmd_flag(tmp_path: Path) -> None:
    comspec = r"C:\Windows\system32\cmd.exe"

    resolved = _resolve_shell(
        {"PATH": str(tmp_path / "empty"), "COMSPEC": comspec}, platform="win32"
    )

    # ``/c``, NOT ``-c`` — cmd.exe rejects the POSIX spelling outright.
    assert resolved == ShellConfig(comspec, "/c")


def test_win32_ignores_a_shell_that_does_not_exist(tmp_path: Path) -> None:
    """Git-Bash exports an MSYS ``$SHELL`` that ``Popen`` cannot spawn.

    ``SHELL=/usr/bin/bash`` is not a Win32 path, so honouring it verbatim (as
    the POSIX arm does) would trade one guaranteed spawn failure for another.
    """

    resolved = _resolve_shell(
        {
            # An MSYS-style path: shaped like a shell, absent from the filesystem.
            "SHELL": str(tmp_path / "msys" / "usr" / "bin" / "bash"),
            "PATH": str(tmp_path / "empty"),
            "COMSPEC": r"C:\Windows\system32\cmd.exe",
        },
        platform="win32",
    )

    assert resolved.path == r"C:\Windows\system32\cmd.exe"


def test_win32_honours_a_real_shell_from_the_env(tmp_path: Path) -> None:
    """A user who exports a genuine ``bash.exe`` keeps bash — and keeps AUTO mode."""

    real_bash = _executable(tmp_path / "git" / "bin", "bash.exe")

    resolved = _resolve_shell(
        {"SHELL": str(real_bash), "PATH": str(tmp_path / "empty")}, platform="win32"
    )

    assert resolved == ShellConfig(str(real_bash), "-c")


# === POSIX is unchanged =====================================================


def test_posix_chain_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """``platform="linux"`` is injected exactly as the win32 arm injects win32.

    The old body opened with ``assert sys.platform != "win32"``, which turned
    red the moment the suite started running on a real Windows box — a guard,
    not an assertion about the POSIX chain.
    """

    assert _resolve_shell({"SHELL": "/usr/bin/fish"}, platform="linux") == ShellConfig(
        "/usr/bin/fish", "-c"
    )
    # The ``/bin/bash`` step probes the real filesystem, which no ``platform=``
    # argument can move: pin it so the chain — not the host — decides. Compare
    # via ``as_posix()``, not ``str()``: on Windows ``str(Path("/bin/bash"))``
    # is ``\\bin\\bash``, so a ``str()`` predicate never fires there and the
    # chain falls through to ``shutil.which("bash")`` — which finds Git-Bash on
    # windows-latest and cannot be pinned from here (it probes via
    # ``os.path.exists``, not ``Path.exists``). What is asserted is therefore
    # the chain's ORDERING, not the presence of a real ``/bin/bash``.
    monkeypatch.setattr(
        Path, "exists", lambda self, **_kw: self.as_posix() == "/bin/bash"
    )
    assert _resolve_shell({}, platform="linux") == ShellConfig("/bin/bash")


def test_explicit_shell_path_still_validates(tmp_path: Path) -> None:
    real = _executable(tmp_path, "myshell")
    assert _resolve_shell({}, str(real)) == ShellConfig(str(real), "-c")
    with pytest.raises(ValueError, match="Custom shell path not found"):
        _resolve_shell({}, str(tmp_path / "absent"))


# === basename / flag mapping ================================================


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        ("/bin/bash", "bash"),
        ("/usr/bin/zsh", "zsh"),
        (r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "powershell"),
        (r"C:\Program Files\PowerShell\7\pwsh.EXE", "pwsh"),
        (r"C:\Windows\system32\cmd.exe", "cmd"),
        ("bash", "bash"),
    ],
)
def test_shell_basename_handles_both_separators(shell: str, expected: str) -> None:
    """Split on ``\\`` too: the permission gate reasons about Windows paths
    while running on POSIX, where ``posixpath.basename`` would return the
    whole string."""

    assert shell_basename(shell) == expected


@pytest.mark.parametrize(
    ("shell", "flag"),
    [
        ("/bin/bash", "-c"),
        ("/usr/bin/fish", "-c"),
        (r"C:\Program Files\PowerShell\7\pwsh.exe", "-Command"),
        (r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-Command"),
        (r"C:\Windows\system32\cmd.exe", "/c"),
    ],
)
def test_command_flag_for(shell: str, flag: str) -> None:
    assert command_flag_for(shell) == flag


# === version suffixes ========================================================


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        ("/usr/local/bin/bash-5.2", "bash"),  # Homebrew / distro side-install
        ("bash-5.2p26", "bash"),  # OpenBSD-style patch level
        ("bash5", "bash"),  # no separator
        ("/usr/bin/zsh-5.9", "zsh"),
        ("ksh93", "ksh"),  # AT&T ksh, digits with no separator
        ("ksh88", "ksh"),
        ("pwsh-7.4", "pwsh"),
        ("/usr/bin/fish-3.6", "fish"),
    ],
)
def test_shell_basename_strips_a_version_suffix(shell: str, expected: str) -> None:
    """A version-suffixed genuine bash must still read as ``bash``.

    Without this, ``$SHELL=/usr/local/bin/bash-5.2`` fell outside the
    classifiable set and AUTO mode prompted for every command a real bash was
    about to run — a spurious force-ASK on a supported platform.
    """

    assert shell_basename(shell) == expected


@pytest.mark.parametrize("shell", ["bash", "sh", "dash", "zsh", "pwsh", "powershell", "cmd"])
def test_unversioned_names_are_untouched(shell: str) -> None:
    assert shell_basename(shell) == shell


def test_version_strip_cannot_admit_a_new_shell() -> None:
    """The strip only ever SHORTENS a name, so it cannot widen the gate.

    ``fish-3.6`` must resolve to ``fish`` and stay out of the classifiable
    set — the normalization is there to stop false prompts, not to start
    granting auto-allow to shells the grammar cannot read.
    """

    assert shell_basename("/usr/bin/fish-3.6") == "fish"
    assert is_classifiable_shell("/usr/bin/fish-3.6") is False
    assert is_classifiable_shell("pwsh-7.4") is False
    assert is_classifiable_shell("/usr/local/bin/bash-5.2") is True


def test_a_bare_version_is_not_stripped_to_nothing() -> None:
    """Degenerate input must not become an empty name."""

    assert shell_basename("5") == "5"


# === the flag reaches argv ==================================================


async def test_spawn_argv_uses_the_resolved_flag_and_hands_cmd_the_command_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn site used to hard-code ``-c``; ``cmd.exe -c`` is not a thing.

    And on the ``cmd`` family the third argv element is the model's command
    with NOTHING in front of it. This is the only assertion off Windows that
    can say so through the real spawn path — CI's win32 leg resolves ``pwsh``,
    so only forcing the resolution here reaches the ``cmd`` arm at all.

    It is a regression guard with a measured cost behind it. From this branch's
    first #239 commit until 2026-09-09 — never on ``main``, never in a release
    — this line read ``"chcp 65001 >nul&dir"``, and CI run
    34272507388 measured that prefix breaking an unquoted spaced executable
    path on windows-latest (``'C:\\…\\a' is not recognized``): the prefix costs
    ``cmd /c``'s rule 1 the quotes ``list2cmdline`` put around the command. Any
    future preamble on this family turns this red on darwin instead of on the
    leg. What this case can NO LONGER say, now that the arm's preamble is
    ``""``, is that the spawn site applies :func:`utf8_output_preamble` at all
    — every assertion below is satisfied with the prepend deleted, measured.
    The case after this one is what pins that. See
    :func:`aelix_ai.utils._shell.utf8_output_preamble`.
    """

    recorded: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        recorded.append(list(argv))
        # Short-circuit into the tool's existing spawn-failure branch so the
        # test needs no fake process object. The errno is load-bearing: #243
        # (same release) classifies a spawn failure BY errno, and an OSError
        # carrying none deliberately escapes the tool
        # (``test_a_spawn_error_with_no_errno_still_escapes``). A real
        # ``Popen`` miss always carries ENOENT, so a fake without one is not
        # the failure this case means to stage — measured: without it these two
        # pass alone and fail once #243 is in the tree.
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", argv[0])

    monkeypatch.setattr(bash_mod, "_resolve_shell", lambda *_a, **_k: ShellConfig("cmd.exe", "/c"))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ops = bash_mod.create_local_bash_operations()
    result = await ops.exec("dir", ".", on_data=lambda _b: None, env={})

    assert recorded == [["cmd.exe", "/c", "dir"]]
    assert utf8_output_preamble("cmd.exe") == ""
    assert recorded[0][2] == utf8_output_preamble("cmd.exe") + "dir"
    assert result.exit_code == 127


async def test_spawn_argv_prepends_the_powershell_preamble(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn site really applies :func:`utf8_output_preamble`, pinned here.

    The ``cmd`` case above can no longer say so. Once that arm returned ``""``
    its assertions became satisfiable with the prepend deleted, and the review
    of the removal MEASURED that: with ``utf8_output_preamble(shell.path) +``
    taken out of the ``Popen`` argv in ``tools/bash.py`` the whole darwin suite
    stayed green. PowerShell is the one family with a non-empty preamble, so it
    is the only family whose spawn can carry that guard off Windows — and CI's
    win32 leg resolves ``pwsh``, so this is the arm that ships.

    Re-measured for this test: with the prepend removed from ``tools/bash.py``
    this case fails on darwin with ``recorded[0][2] == "dir"`` against an
    expected ``try{[Console]::OutputEncoding=…}catch{};dir``; restored, it
    passes.
    """

    recorded: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        recorded.append(list(argv))
        # ENOENT for the reason the sibling above gives: #243 classifies by errno.
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", argv[0])

    monkeypatch.setattr(
        bash_mod, "_resolve_shell", lambda *_a, **_k: ShellConfig("pwsh.exe", "-Command")
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ops = bash_mod.create_local_bash_operations()
    result = await ops.exec("dir", ".", on_data=lambda _b: None, env={})

    assert recorded == [["pwsh.exe", "-Command", POWERSHELL_UTF8_PREAMBLE + "dir"]]
    # Spelled out as well as composed: a preamble that silently became ``""``
    # would satisfy the line above on its own.
    assert POWERSHELL_UTF8_PREAMBLE
    assert recorded[0][2].startswith(POWERSHELL_UTF8_PREAMBLE)
    assert recorded[0][2].endswith("dir")
    assert result.exit_code == 127


# === #227 — one win32 chain, two callers with different needs ================


def test_the_bash_tool_and_command_resolution_share_one_windows_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One primitive answers both callers, and the ``sh`` step is the difference.

    #227 moved this chain down into ``aelix_ai.utils._shell`` so a ``!command``
    and the bash tool cannot drift into two win32 answers that agree only by
    review — the failure mode this repo has already been bitten by (ADR-0193).
    ``_resolve_shell_win32`` is now ``windows_command_shells(env)[0]``.

    The one deliberate difference is ``include_posix_sh``. A ``!command`` was
    written for ``sh``, so the resolver that SPAWNS takes an ``sh`` on ``PATH``
    ahead of PowerShell. The bash tool must NOT: ``sh`` is in
    ``_CLASSIFIABLE_SHELLS``, so taking it there would flip AUTO mode on every
    MSYS box from "ask about everything" to "read it with the bash grammar",
    which is ADR-0237/#204's decision to make.

    Three halves, because equality alone cannot see the difference any more:
    (a) a Git-for-Windows row where the two callers must answer DIFFERENTLY —
    the only assertion here that dies when ``_resolve_shell_win32`` starts
    passing ``include_posix_sh=True``; (b) the order/drift equality on every
    row; (c) the ``sh`` delta, stated conditionally, because without an ``sh``
    fixture the two lists are simply equal.

    Every row spells ``PATH``, and (d) is the one deliberate exception — the
    guard on that rule: with the key absent the primitive makes no PATH
    candidates at all, so a row that forgot ``PATH`` under-asserts silently
    instead of reading the HOST's ``PATH``.
    """

    from aelix_ai.utils._shell import windows_command_shells

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    sh_only = tmp_path / "sh_only"
    sh_in_sh_only = _on_path(sh_only, "sh")
    pwsh_only = tmp_path / "pwsh_only"
    _on_path(pwsh_only, "pwsh")
    powershell_only = tmp_path / "powershell_only"
    _on_path(powershell_only, "powershell")
    git_for_windows = tmp_path / "gfw"
    sh_in_gfw = _on_path(git_for_windows, "sh")
    pwsh_in_gfw = _on_path(git_for_windows, "pwsh")
    real_shell = _executable(tmp_path / "git", "bash.exe")
    comspec = r"C:\Program Files\Nope\cmd.exe"

    #: ``(label, env, the sh fixture that env exposes or None)``.
    rows: list[tuple[str, dict[str, str], Path | None]] = [
        ("empty PATH", {"PATH": str(empty)}, None),
        ("pwsh only", {"PATH": str(pwsh_only)}, None),
        ("powershell only", {"PATH": str(powershell_only)}, None),
        ("%COMSPEC% only", {"PATH": str(empty), "COMSPEC": comspec}, None),
        (
            "$SHELL exists, empty PATH",
            {"PATH": str(empty), "SHELL": str(real_shell)},
            None,
        ),
        ("sh only", {"PATH": str(sh_only)}, sh_in_sh_only),
        ("Git for Windows: sh + pwsh", {"PATH": str(git_for_windows)}, sh_in_gfw),
        ("sh + %COMSPEC%", {"PATH": str(sh_only), "COMSPEC": comspec}, sh_in_sh_only),
        (
            "$SHELL exists + sh",
            {"PATH": str(sh_only), "SHELL": str(real_shell)},
            sh_in_sh_only,
        ),
    ]

    # (a) the Git-for-Windows row: the bash tool takes pwsh, the !command takes sh.
    gfw_env = {"PATH": str(git_for_windows)}
    tool_answer = _resolve_shell(gfw_env, platform="win32")
    assert Path(tool_answer.path) == pwsh_in_gfw
    assert tool_answer.command_flag == "-Command"
    command_first = windows_command_shells(gfw_env, include_posix_sh=True)[0]
    assert Path(command_first.path) == sh_in_gfw
    assert command_first.command_flag == "-c"

    without_sh_rows = 0
    for label, env, sh_fixture in rows:
        # (b) same chain, same order: the bash tool's answer IS candidate 0.
        assert windows_command_shells(env)[0] == _resolve_shell(
            env, platform="win32"
        ), label

        # (c) the delta, stated conditionally.
        with_sh = windows_command_shells(env, include_posix_sh=True)
        without = windows_command_shells(env)
        if sh_fixture is None:
            without_sh_rows += 1
            assert with_sh == without, label
            continue
        # ``Path("")`` is ``Path(".")`` and EXISTS — the empty default cannot
        # stand in for "no $SHELL" here.
        env_shell = env.get("SHELL")
        index = 1 if env_shell and Path(env_shell).exists() else 0
        assert len(with_sh) == len(without) + 1, label
        assert Path(with_sh[index].path) == sh_fixture, label
        assert with_sh[index].command_flag == "-c", label
        assert with_sh[:index] + with_sh[index + 1 :] == without, label

    assert without_sh_rows == 5, without_sh_rows

    # (d) the one env that does NOT spell ``PATH`` — the guard on that rule.
    # Without ``if path is not None`` the two probes run as
    # ``shutil.which(..., path=None)``, which reads the HOST's ``PATH``: on a
    # dev box that is a real ``/bin/sh`` AND any installed ``pwsh`` (measured:
    # ``[/bin/sh -c, /opt/homebrew/bin/pwsh -Command, cmd.exe /c]``), and on
    # windows-latest Git's ``sh.exe``. The leak hits the bash-tool call too,
    # not just ``include_posix_sh=True`` — a fixture meant as "stock Windows"
    # would be silently ``sh``/``pwsh``-present — and the trap is invisible
    # when it fires, so it is pinned here rather than left to fixture
    # discipline. The $SHELL/COMSPEC row shows the non-PATH candidates still
    # appear: it is the PATH probes alone that are skipped.
    assert windows_command_shells({}, include_posix_sh=True) == [ShellConfig("cmd.exe", "/c")]
    assert windows_command_shells({}) == [ShellConfig("cmd.exe", "/c")]
    assert windows_command_shells(
        {"SHELL": str(real_shell), "COMSPEC": comspec}, include_posix_sh=True
    ) == [
        ShellConfig(str(real_shell), "-c"),
        ShellConfig(comspec, "/c"),
        ShellConfig("cmd.exe", "/c"),
    ]
