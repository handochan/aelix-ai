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

import subprocess
import sys
from pathlib import Path

import pytest
from aelix_coding_agent.builtin.bash_classifier import is_classifiable_shell
from aelix_coding_agent.tools import bash as bash_mod
from aelix_coding_agent.tools.bash import (
    ShellConfig,
    _command_flag_for,
    _resolve_shell,
    shell_basename,
)


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
    assert _command_flag_for(shell) == flag


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


async def test_spawn_argv_uses_the_resolved_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spawn site used to hard-code ``-c``; ``cmd.exe -c`` is not a thing."""

    recorded: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        recorded.append(list(argv))
        # Short-circuit into the tool's existing spawn-failure branch so the
        # test needs no fake process object.
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(bash_mod, "_resolve_shell", lambda *_a, **_k: ShellConfig("cmd.exe", "/c"))
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ops = bash_mod.create_local_bash_operations()
    result = await ops.exec("dir", ".", on_data=lambda _b: None, env={})

    assert recorded == [["cmd.exe", "/c", "dir"]]
    assert result.exit_code == 127
