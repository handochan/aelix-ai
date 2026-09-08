"""Windows-asserting tests for the bash tool's shell resolution (#104).

Every test here RUNS on Linux. The win32 arm is driven by injecting
``platform="win32"``, and the PATH probes use real executable files under
``tmp_path`` so the walk does the real work rather than being stubbed.

Injection rather than ``monkeypatch.setattr(sys, "platform", "win32")``: the
argument picks which CHAIN to build, and it deliberately does NOT pick the
naming rule the walk probes with, so a win32 chain can be asserted from a POSIX
box against extensionless fixtures. #241 replaced ``shutil.which`` here — the
older reason for the injection was that ``which`` branches on ``sys.platform``
and then touches ``_winapi``, which is ``None`` off Windows; that was never
true on 3.11 (which does not import ``_winapi`` in ``shutil`` at all) and after
#241 nothing on this path calls ``which``. The conclusion stands, the ground
under it changed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.utils._shell import command_flag_for, shell_basename
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
    """A PATH probe target the walk can actually find here.

    Under Windows naming the walk tries ``name + PATHEXT`` candidates and never
    the bare name — CPython's rule, adopted verbatim (``shutil.py:1543-1551``) —
    so an extensionless fixture is unreachable there and the win32 arm slides
    on to ``%COMSPEC%``. On POSIX the bare name is the only candidate, so the
    extension has to follow the running platform. Which naming rule applies is
    ``sys.platform``'s answer, NOT the injected ``platform=`` (#241).
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

    # ``SYSTEMROOT`` is spelled for the reason #241's section below gives: with
    # the key absent the floor synthesises ``C:\Windows\System32\cmd.exe``,
    # which exists on windows-latest and not here, so the row would assert two
    # different chains on two legs.
    resolved = _resolve_shell(
        {"PATH": str(tmp_path / "empty"), "SYSTEMROOT": str(tmp_path / "nosysroot")},
        platform="win32",
    )

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
    # chain falls through to the ``bash`` walk — which finds Git-Bash on
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

    from aelix_ai.utils._shell import _is_absolute, windows_command_shells

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

        # (e) #241: every candidate the user did not NAME is absolute. One
        # assertion catching a single-line reversion at any of the three sites
        # the issue moved — the PATH walk, the ``%COMSPEC%`` gate, the
        # ``%SystemRoot%`` floor — across all nine rows at once. The two
        # exemptions are the two candidates that are not resolutions: an
        # explicit ``$SHELL`` (the user named it; #227 made that the documented
        # way) and the bare ``cmd.exe`` floor, which must stay so ``[0]`` always
        # exists.
        for candidate in with_sh:
            if candidate.path in (env.get("SHELL"), "cmd.exe"):
                continue
            assert _is_absolute(candidate.path), (label, candidate.path)

    assert without_sh_rows == 5, without_sh_rows

    # (d) the one env that does NOT spell ``PATH`` — the guard on that rule.
    # The rule survives #241 with a new mechanism: the walk takes ``PATH`` as an
    # argument and makes no candidates from a missing one, where the old
    # ``shutil.which(..., path=None)`` read the HOST's ``PATH`` instead: on a
    # dev box that is a real ``/bin/sh`` AND any installed ``pwsh`` (measured:
    # ``[/bin/sh -c, /opt/homebrew/bin/pwsh -Command, cmd.exe /c]``), and on
    # windows-latest Git's ``sh.exe``. The leak hits the bash-tool call too,
    # not just ``include_posix_sh=True`` — a fixture meant as "stock Windows"
    # would be silently ``sh``/``pwsh``-present — and the trap is invisible
    # when it fires, so it is pinned here rather than left to fixture
    # discipline. The $SHELL/COMSPEC row shows the non-PATH candidates still
    # appear: it is the PATH probes alone that are skipped.
    # ``SYSTEMROOT`` is spelled here for the reason #241's section gives: it is
    # the second env key whose ABSENCE changes the chain rather than shortening
    # it, since the floor then synthesises ``C:\Windows\System32\cmd.exe`` —
    # real on windows-latest, absent here. The rule these rows guard is about
    # ``PATH``; pinning ``SYSTEMROOT`` keeps the equalities exact on both legs.
    no_sysroot = str(tmp_path / "nosysroot")
    assert windows_command_shells(
        {"SYSTEMROOT": no_sysroot}, include_posix_sh=True
    ) == [ShellConfig("cmd.exe", "/c")]
    assert windows_command_shells({"SYSTEMROOT": no_sysroot}) == [
        ShellConfig("cmd.exe", "/c")
    ]
    assert windows_command_shells(
        {"SHELL": str(real_shell), "COMSPEC": comspec, "SYSTEMROOT": no_sysroot},
        include_posix_sh=True,
    ) == [
        ShellConfig(str(real_shell), "-c"),
        ShellConfig(comspec, "/c"),
        ShellConfig("cmd.exe", "/c"),
    ]


# === #241 — an unnamed candidate is taken only when it resolves absolutely ===
#
# Three of the four ways this chain used to name a shell could hand back a
# program sitting in the process's current directory. ``shutil.which`` prepends
# ``os.curdir`` on win32 *even when ``path=`` is passed explicitly* — the insert
# lives in the ``else`` of ``if path is None`` (``shutil.py:1528-1532`` on
# 3.12.13), unconditional on 3.11 and gated by
# ``NoDefaultCurrentDirectoryInExePath`` from 3.12; an empty or relative ``PATH``
# component means ``.`` on every platform; and a bare name handed to
# ``CreateProcess``/``execvp`` is searched against ``.`` as well. The chain now
# walks ``PATH`` itself and skips every component that is not absolute.
#
# EVERY injected env below that can reach the floor also spells ``SYSTEMROOT``,
# for the same reason every env in this file spells ``PATH``: with the key
# absent the floor synthesises ``C:\Windows\System32\cmd.exe``, which EXISTS on
# windows-latest and does not on the POSIX legs, so a row that omits it would
# assert two different chains on two legs.


_NO_SYSROOT = "nosysroot"  # a SYSTEMROOT that is absolute and deliberately absent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (r"C:\Windows\system32\cmd.exe", True),
        (r"\\srv\share", True),
        ("/tmp/x", True),
        ("cmd.exe", False),
        ("C:foo", False),  # drive-RELATIVE: the form a lazy check misses
        # ROOTLESS: what 3.11/3.12's ``ntpath.isabs`` calls absolute and 3.13+
        # does not. The drive clause answers False on all of them.
        ("\\dir", False),
        (".", False),
        ("", False),
        ("relbin", False),
        (r"..\tools", False),
    ],
)
def test_is_absolute_answers_the_same_on_every_leg(value: str, expected: bool) -> None:
    """``ntpath.isabs`` ∪ ``posixpath.isabs``, and deliberately not ``os.path``.

    Bare :func:`os.path.isabs` IS ``posixpath.isabs`` off Windows, so it calls
    ``C:\\Windows\\system32\\cmd.exe`` *relative* and the whole win32 chain
    becomes untestable from a POSIX box — measured in this worktree, that
    spelling turned five existing cases red on darwin (``5 failed, 88 passed``
    against the union's ``93 passed``) and would have turned none red on
    windows-latest. It is also version-stable where ``ntpath.isabs`` alone is
    not, in BOTH directions: 3.11/3.12 answer True for ``/tmp/x`` and 3.13+
    answer False (``ntpath``'s own *"LEGACY BUG"* comment), so an
    ``ntpath``-only gate would start rejecting this file's own POSIX fixtures on
    3.13 — and 3.11/3.12 answer True for a ROOTLESS ``\\dir`` where 3.13+ answer
    False, which is why the nt half requires a drive. Every row below therefore
    answers the same on 3.11.15, 3.12.13 and 3.13.

    Both halves are load-bearing here, so neither can be deleted with this case
    green: ``C:foo`` has a drive and is still relative (``ntpath.isabs``), and
    ``/tmp/x`` has no drive at all (``posixpath.isabs``).
    """

    from aelix_ai.utils._shell import _is_absolute

    assert _is_absolute(value) is expected


def test_a_path_component_that_is_not_absolute_contributes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four spellings of "the current directory", all skipped.

    The last two are what separates a real absoluteness test from a
    ``component in ("", os.curdir)`` check: ``relbin`` is a relative directory
    that really does hold a ``pwsh``, and ``C:foo`` is Windows' drive-RELATIVE
    form. The answer must be the ``pwsh`` under the absolute entry every time,
    and the ``sh`` planted in the cwd must not be candidate 1 either.
    """

    from aelix_ai.utils._shell import windows_command_shells

    cwd = tmp_path / "repo"
    _on_path(cwd, "sh")
    _on_path(cwd, "pwsh")
    _on_path(cwd / "relbin", "pwsh")
    monkeypatch.chdir(cwd)

    bin_dir = tmp_path / "bin"
    pwsh = _on_path(bin_dir, "pwsh")

    for component in ("", os.curdir, "relbin", "C:foo"):
        env = {
            "PATH": component + os.pathsep + str(bin_dir),
            "SYSTEMROOT": str(tmp_path / _NO_SYSROOT),
        }
        chain = windows_command_shells(env, include_posix_sh=True)
        assert Path(chain[0].path) == pwsh, component
        assert chain[0].command_flag == "-Command", component


def test_each_path_directory_is_probed_once_and_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First absolute hit wins, and a repeated directory is entered once.

    Dedupe is invisible in the return value — the walk stops at the first hit
    either way — so the second half counts probes through
    :func:`os.path.exists`, which is the symbol the walk's access rule names.
    The ``assert probes`` comes first: a counter over an empty list passes every
    assertion that follows it.
    """

    from aelix_ai.utils import _shell

    first = tmp_path / "first"
    second = tmp_path / "second"
    in_first = _on_path(first, "pwsh")
    in_second = _on_path(second, "pwsh")

    assert Path(
        _shell.windows_command_shells(
            {"PATH": os.pathsep.join([str(first), str(second)])}
        )[0].path
    ) == in_first
    assert Path(
        _shell.windows_command_shells(
            {"PATH": os.pathsep.join([str(second), str(first)])}
        )[0].path
    ) == in_second

    probes: list[str] = []
    real_exists = os.path.exists

    def counting_exists(path: Any) -> bool:
        probes.append(str(path))
        return bool(real_exists(path))

    monkeypatch.setattr(os.path, "exists", counting_exists)

    empty = tmp_path / "empty"
    empty.mkdir()
    found = _shell._which_on_path(
        "pwsh",
        path=os.pathsep.join([str(empty), str(empty), str(second)]),
        env={},
        windows=sys.platform == "win32",
    )
    assert probes, "nothing was probed at all — the counter proves nothing"
    assert found is not None and Path(found) == in_second
    # No candidate path is probed twice: ``PATH`` names ``empty`` twice and the
    # walk enters it once. Asserted over the RAW probe list. An earlier spelling
    # collapsed consecutive duplicates first and then compared the directory
    # sequence to ``[empty, second]``, which is vacuous — without the dedupe the
    # sequence is ``[empty, empty, second]``, which collapses to the very same
    # list, and the whole ``seen``/``normcase`` block could be deleted with this
    # case green (measured: ``122 passed, 3 skipped``). This spelling also bites
    # on windows-latest, where ``PATHEXT`` gives each directory eight probes
    # instead of one and no literal directory sequence would hold on both legs.
    assert len(probes) == len(set(probes)), f"a directory was re-entered: {probes}"
    assert os.path.dirname(probes[0]) == str(empty)
    assert os.path.dirname(probes[-1]) == str(second)


def test_win32_naming_applies_pathext_the_way_windows_does(tmp_path: Path) -> None:
    """``PATHEXT`` comes from ``env``, defaults to shutil's, and gates the bare name.

    Six rows. (iii) is the only legal way the bare-name branch can fire for a
    name this module ever asks for, so it is driven at the primitive; (iv) is
    the row with teeth, because ``_resolve_shell_win32`` has no fall-through and
    a directory taken as a candidate would become the bash tool's shell outright.
    """

    from aelix_ai.utils import _shell

    def powershell_paths(env: dict[str, str]) -> list[str]:
        return [
            c.path
            for c in _shell.windows_command_shells(env, platform="win32")
            if c.command_flag == "-Command"
        ]

    no_root = str(tmp_path / _NO_SYSROOT)

    # (i) the listed extension beats the bare file.
    both = tmp_path / "both"
    _executable(both, "pwsh")
    with_ext = _executable(both, "pwsh.EXE")
    assert powershell_paths({"PATH": str(both), "SYSTEMROOT": no_root}) == [
        str(with_ext)
    ]

    # (ii) ``PATHEXT`` is read from ``env`` — and it REPLACES the default, so a
    # ``pwsh.EXE`` is unfindable once the list no longer mentions ``.EXE``.
    custom = tmp_path / "custom"
    _executable(custom, "pwsh.EXE")
    foo = _executable(custom, "pwsh.FOO")
    assert powershell_paths(
        {"PATH": str(custom), "PATHEXT": ".FOO", "SYSTEMROOT": no_root}
    ) == [str(foo)]
    only_exe = tmp_path / "only_exe"
    exe_only = _executable(only_exe, "pwsh.EXE")
    assert (
        powershell_paths(
            {"PATH": str(only_exe), "PATHEXT": ".FOO", "SYSTEMROOT": no_root}
        )
        == []
    )

    # (iii) the bare name is tried first ONLY when the name already carries a
    # listed extension (``shutil.py:1549-1551``). No name this module resolves
    # does, so the branch is a deliberate mirror of CPython rather than live
    # behaviour, and this is what pins it.
    named = tmp_path / "named"
    exact = _executable(named, "pwsh.exe")
    assert _shell._which_on_path(
        "pwsh.exe", path=str(named), env={}, windows=True
    ) == str(exact)
    # …and the CONDITION, not just the branch: with ``.EXE`` off the list the
    # bare name is not inserted, the only candidate becomes ``pwsh.exe.FOO``,
    # and the same file is unreachable. Spelled this way rather than by asking
    # for ``pwsh`` here, because a case-insensitive filesystem (darwin, and
    # Windows itself) would match ``pwsh.EXE`` against ``pwsh.exe`` and the row
    # would assert two different things on two legs.
    assert (
        _shell._which_on_path(
            "pwsh.exe", path=str(named), env={"PATHEXT": ".FOO"}, windows=True
        )
        is None
    )

    # (iv) a DIRECTORY is not a shell. Spelled under both naming rules so the
    # row bites on the POSIX legs and on windows-latest alike.
    dirs = tmp_path / "dirs"
    for name in ("pwsh", "pwsh.EXE", "powershell", "powershell.EXE"):
        (dirs / name).mkdir(parents=True)
    assert powershell_paths({"PATH": str(dirs), "SYSTEMROOT": no_root}) == []
    assert _shell.windows_command_shells(
        {"PATH": str(dirs), "SYSTEMROOT": no_root}
    ) == [ShellConfig("cmd.exe", "/c")]

    # (vi) ``PATHEXT`` absent falls back to shutil's own default list, which is
    # byte-identical across 3.11-3.14.
    assert powershell_paths({"PATH": str(only_exe), "SYSTEMROOT": no_root}) == [
        str(exe_only)
    ]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Win32 path resolution drops a trailing dot, so the strip is invisible there",
)
def test_a_trailing_dot_in_a_pathext_entry_is_stripped(tmp_path: Path) -> None:
    """(vii) of the naming rows: ``[ext.rstrip(".") …]``, CPython 3.12's rule.

    Unreachable through the chain — a ``PATHEXT`` entry with a trailing dot is
    what the strip is for, and no name this module resolves needs it — so it is
    driven at the primitive. It can only be asserted off Windows: there
    ``pwsh.EXE.`` would open ``pwsh.EXE`` anyway, because Win32 drops the
    trailing dot itself, which is why CPython strips it before probing.

    3.11's ``shutil`` has no strip at all (``shutil.py:1531-1543`` on 3.11.15);
    the newer rule is the one mirrored, and this row is what would go red if
    somebody "simplified" it back.
    """

    from aelix_ai.utils import _shell

    bin_dir = tmp_path / "bin"
    exe = _executable(bin_dir, "pwsh.EXE")

    assert _shell._which_on_path(
        "pwsh", path=str(bin_dir), env={"PATHEXT": ".EXE."}, windows=True
    ) == str(exe)


@pytest.mark.skipif(
    sys.platform == "win32", reason="X_OK is meaningless on Windows — every file has it"
)
def test_a_non_executable_file_is_not_a_shell(tmp_path: Path) -> None:
    """(v) of the naming rows: the ``X_OK`` clause, unreachable on Windows."""

    from aelix_ai.utils._shell import windows_command_shells

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pwsh.EXE").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "pwsh.EXE").chmod(0o644)

    chain = windows_command_shells(
        {"PATH": str(bin_dir), "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)},
        platform="win32",
    )

    assert [c for c in chain if c.command_flag == "-Command"] == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="the POSIX-naming half needs a POSIX host"
)
def test_the_chain_platform_and_the_naming_platform_are_two_seams(
    tmp_path: Path,
) -> None:
    """``platform=`` picks which CHAIN; the naming rule is decided separately.

    Every existing win32-arm case in this file drives the chain from a POSIX box
    with extensionless fixtures. If ``_resolve_shell(platform="win32")`` forwarded
    its platform into the naming rule, all of them would break at once and the
    obvious "fix" would be to forward it here too — so the split is pinned.
    """

    from aelix_ai.utils._shell import windows_command_shells

    bin_dir = tmp_path / "bin"
    pwsh = _executable(bin_dir, "pwsh")
    env = {"PATH": str(bin_dir), "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)}

    assert Path(_resolve_shell(env, platform="win32").path) == pwsh
    assert Path(windows_command_shells(env)[0].path) == pwsh
    # …and asking for win32 NAMING really does change the answer.
    assert [
        c
        for c in windows_command_shells(env, platform="win32")
        if c.command_flag == "-Command"
    ] == []


def test_a_cwd_copy_loses_to_the_path_copy_on_every_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One search path, two answers: ``which`` takes the cwd copy, the chain does not.

    Both halves are handed the SAME ``PATH`` — ``os.curdir`` in front of one
    absolute directory — and that is what makes the second half falsifiable
    here. Measured on darwin: with the pre-#241 body restored
    (``shutil.which(exe, path=path)``) the chain answers ``./pwsh`` and this
    case goes red; with the walk in place it answers the absolute copy. The
    first half is the negative control that keeps the second from being vacuous
    — stock ``which`` really does return the planted file off that same path,
    and returns it as a RELATIVE name, which is the shape that reaches a caller.

    What it covers is the empty/``.``-component hole, which is live on darwin
    and Linux as much as on Windows, and which this case SPELLS in ``PATH``. It
    is NOT win32's implicit prepend — ``which`` inserting ``os.curdir`` at the
    front of a search path that never named it (``shutil.py:1528-1532`` on
    3.12.13; unconditional on 3.11) — because no POSIX ``which`` does that, so
    no POSIX leg could fail on it. That half is asserted on the leg itself by
    :func:`test_win32_the_answer_does_not_depend_on_the_curdir_variable`, which
    never skips, and it was measured on run 34238824791.
    """

    import shutil

    from aelix_ai.utils._shell import windows_command_shells

    cwd = tmp_path / "repo"
    planted = _on_path(cwd, "pwsh")
    bin_dir = tmp_path / "bin"
    real = _on_path(bin_dir, "pwsh")
    monkeypatch.chdir(cwd)
    reaches_cwd = os.pathsep.join([os.curdir, str(bin_dir)])

    stock = shutil.which("pwsh", path=reaches_cwd)

    assert stock is not None
    assert Path(stock).resolve() == planted.resolve()
    assert not Path(stock).is_absolute()

    chain = windows_command_shells(
        {"PATH": reaches_cwd, "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)}
    )

    assert Path(chain[0].path) == real
    assert Path(chain[0].path).is_absolute()


@pytest.mark.skipif(
    sys.platform != "win32", reason="the curdir prepend is win32 behaviour"
)
def test_win32_a_pwsh_in_the_current_directory_is_not_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real thing, on the only leg that can run it.

    The negative control is the point of the case: stock ``shutil.which`` with
    an explicit ``path=`` must still find the planted file. What it hands back
    is CWD-RELATIVE — measured on run 34238824791, ``'.\\pwsh.EXE'`` on
    windows-latest under BOTH 3.11 and 3.12, which is ``os.curdir`` joined to
    the name. So the control resolves it against the cwd before comparing: the
    row that compared the returned string verbatim could not pass on ANY
    Windows runner — not the vulnerable one (``pwsh.EXE`` is not the absolute
    planted path) and not a hardened one (which answers the ``PATH`` copy) — and
    its failure message blamed ``NoDefaultCurrentDirectoryInExePath`` for what
    was the comparison's own bug. That run therefore MEASURED the hole #241
    closes, on the leg, on both interpreters.

    When the control cannot be established the case SKIPS, naming what ``which``
    answered instead. The ``delenv`` in the body is meant to make that
    unreachable — CPython's Windows ``unsetenv`` has been
    ``SetEnvironmentVariableW(name, NULL)`` since 3.9, so it clears the process
    block ``NeedCurrentDirectoryForExePath`` reads on 3.12+, and 3.11 does not
    consult it at all — but whether the prepend happens is the OS's and the
    interpreter's answer, not this repo's, and a case that can never go green on
    the one platform it is for is worse than one that says why it could not
    measure. The product's half is not skipped with
    it: the row below asserts the same answer on THIS leg — a planted
    ``pwsh.exe`` in the cwd, the ``PATH`` copy resolved — with no control and no
    runner policy in the loop. Off the leg, what survives is the
    ``.``-component form of the claim, in
    :func:`test_a_cwd_copy_loses_to_the_path_copy_on_every_leg` above and in
    :func:`test_a_path_component_that_is_not_absolute_contributes_nothing`; the
    implicit prepend itself is not assertable where no ``which`` performs it.
    """

    import shutil

    from aelix_ai.utils._shell import windows_command_shells

    cwd = tmp_path / "repo"
    planted = _executable(cwd, "pwsh.exe")
    bin_dir = tmp_path / "bin"
    real = _executable(bin_dir, "pwsh.exe")
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)

    control = shutil.which("pwsh", path=str(bin_dir))
    if control is None or Path(control).resolve() != planted.resolve():
        pytest.skip(
            f"stock shutil.which answered {control!r}, not the planted cwd "
            "copy: the curdir prepend did not happen on this runner (3.12+ "
            "consults NeedCurrentDirectoryForExePath; 3.11 inserts "
            "unconditionally), so the control this case rests on cannot be "
            "established here"
        )
    assert not Path(control).is_absolute()

    chain = windows_command_shells(
        {"PATH": str(bin_dir), "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)}
    )

    assert Path(chain[0].path) == real


@pytest.mark.skipif(
    sys.platform != "win32", reason="the curdir prepend is win32 behaviour"
)
def test_win32_the_answer_does_not_depend_on_the_curdir_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting ``NoDefaultCurrentDirectoryInExePath`` was the rejected fix.

    It is dead on 3.11 (a leg this repo gates on) and is a process-global
    mutation inside a resolver two threads can enter. Same answer set and unset.
    """

    from aelix_ai.utils._shell import windows_command_shells

    cwd = tmp_path / "repo"
    _executable(cwd, "pwsh.exe")
    bin_dir = tmp_path / "bin"
    real = _executable(bin_dir, "pwsh.exe")
    monkeypatch.chdir(cwd)
    env = {"PATH": str(bin_dir), "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)}

    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    unset = windows_command_shells(env)
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    was_set = windows_command_shells(env)

    assert unset == was_set
    assert Path(unset[0].path) == real


def test_the_system_root_floor_sits_immediately_before_the_bare_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``%SystemRoot%\\System32\\cmd.exe`` before the bare ``cmd.exe``.

    Spelled the way production spells it — ``os.path.join`` on an uppercase
    ``SYSTEMROOT``, the shape ``_process_tree.py``'s ``taskkill`` already ships.
    Existence-checked, unlike that sibling: it falls through to the bare name
    after a failed SPAWN, while this site only NAMES a shell and has no
    fall-through, so a stale ``%SystemRoot%`` would turn every bash tool call
    into the #104 failure in the very environment where the bare name works.
    """

    from aelix_ai.utils._shell import windows_command_shells

    root = tmp_path / "winroot"
    real_cmd = _executable(root / "System32", "cmd.exe")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(tmp_path)

    chain = windows_command_shells({"PATH": str(empty), "SYSTEMROOT": str(root)})
    assert chain == [
        ShellConfig(str(real_cmd), "/c"),
        ShellConfig("cmd.exe", "/c"),
    ]

    # UNCONDITIONAL, and on a stock box that means the same program twice:
    # ``%COMSPEC%`` there IS ``%SystemRoot%\\System32\\cmd.exe``. Deliberate —
    # gating the step on "was ``%COMSPEC%`` dropped" would make the floor's
    # presence depend on the very variable it exists to survive, and the
    # duplicate costs at most one repeated spawn attempt, on the caller that
    # falls through, after an attempt on an existence-checked path that already
    # succeeded. Pinned so the gated shape cannot be introduced silently.
    assert windows_command_shells(
        {"PATH": str(empty), "COMSPEC": str(real_cmd), "SYSTEMROOT": str(root)}
    ) == [
        ShellConfig(str(real_cmd), "/c"),
        ShellConfig(str(real_cmd), "/c"),
        ShellConfig("cmd.exe", "/c"),
    ]

    # Absolute but absent adds nothing, and it is the ONLY way to reach the
    # bare name: an absent, empty or relative ``%SystemRoot%`` gets the
    # ``C:\\Windows`` default instead of being dropped, which is the case below.
    assert windows_command_shells(
        {"PATH": str(empty), "SYSTEMROOT": str(tmp_path / _NO_SYSROOT)}
    ) == [ShellConfig("cmd.exe", "/c")]


def test_the_floor_defaults_to_c_windows_on_every_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent, empty AND relative ``%SystemRoot%`` all answer ``C:\\Windows``.

    The default is the one ``_process_tree.py``'s ``taskkill`` resolution
    already uses. It fires on a non-ABSOLUTE value and not merely a missing one,
    which is the divergence from that sibling: ``winroot`` is planted here and
    really holds a ``System32\\cmd.exe``, and the chain must still refuse it,
    because a relative ``%SystemRoot%`` resolves against the current directory —
    the input this issue exists to distrust — and because the step below it is
    the bare name, whose resolution ``CreateProcess`` starts in the application
    and current directories. Without the absoluteness rule the third row answers
    ``[cmd.exe]`` alone.

    This runs on EVERY leg, and that is the point of the shape: the default used
    to have exactly one prover and it was win32-only, so a review could mutate
    ``r"C:\\Windows"`` away and watch the whole targeted suite stay green on
    darwin (measured, 125 passed). Off Windows ``os.path.join(r"C:\\Windows", …)``
    is a RELATIVE name — one path component literally called ``C:\\Windows`` — so
    the default can be planted under the cwd and the ``exists()`` gate answers
    exactly as it does on the leg, where the real system directory is already
    there and needs no planting.
    """

    from aelix_ai.utils._shell import windows_command_shells

    default_cmd = os.path.join(r"C:\Windows", "System32", "cmd.exe")
    empty = tmp_path / "empty"
    empty.mkdir()
    _executable(tmp_path / "winroot" / "System32", "cmd.exe")
    monkeypatch.chdir(tmp_path)
    if sys.platform != "win32":
        _executable(tmp_path / os.path.dirname(default_cmd), "cmd.exe")

    for spelling in ({}, {"SYSTEMROOT": ""}, {"SYSTEMROOT": "winroot"}):
        assert windows_command_shells({"PATH": str(empty), **spelling}) == [
            ShellConfig(default_cmd, "/c"),
            ShellConfig("cmd.exe", "/c"),
        ], spelling


def test_env_keys_are_folded_on_windows_and_only_on_windows(tmp_path: Path) -> None:
    """All five keys, both spellings, one answer — and no fold off win32.

    The fold exists for hand-built and ``spawn_hook``-rewritten dicts: CPython
    upper-cases every key of ``os.environ`` on nt, so production's two mapping
    types already answer the UPPER spelling, but a hook that sets ``Path`` on
    Windows really would reach the child as ``PATH``. It must NOT apply on
    POSIX, where ``Path`` and ``PATH`` are different variables and the bash
    tool hands the very same dict to :class:`subprocess.Popen`: resolver and
    spawn would then disagree about which shell is on which path.
    """

    from aelix_ai.utils._shell import windows_command_shells

    root = tmp_path / "winroot"
    system_cmd = _executable(root / "System32", "cmd.exe")
    bin_dir = tmp_path / "bin"
    pwsh = _executable(bin_dir, "pwsh.FOO")
    shell = _executable(tmp_path / "git", "bash.exe")
    comspec = r"C:\Windows\system32\cmd.exe"
    values = {
        "SHELL": str(shell),
        "PATH": str(bin_dir),
        "PATHEXT": ".FOO",
        "COMSPEC": comspec,
        "SYSTEMROOT": str(root),
    }
    lower = {key.lower(): value for key, value in values.items()}

    upper_chain = windows_command_shells(values, platform="win32")

    assert upper_chain == windows_command_shells(lower, platform="win32")
    # Non-trivial: every one of the five keys contributed a candidate, so the
    # equality above is pinning five ``_env_get`` calls and not one.
    assert upper_chain == [
        ShellConfig(str(shell), "-c"),
        ShellConfig(str(pwsh), "-Command"),
        ShellConfig(comspec, "/c"),
        ShellConfig(str(system_cmd), "/c"),
        ShellConfig("cmd.exe", "/c"),
    ]


@pytest.mark.skipif(
    sys.platform == "win32", reason="the no-fold half needs a non-Windows naming rule"
)
def test_env_keys_are_not_folded_off_windows(tmp_path: Path) -> None:
    """The other half of the fold: a lower-case spelling contributes nothing."""

    from aelix_ai.utils._shell import windows_command_shells

    bin_dir = tmp_path / "bin"
    _executable(bin_dir, "pwsh")
    lower = {
        "shell": str(_executable(tmp_path / "git", "bash.exe")),
        "path": str(bin_dir),
        "comspec": r"C:\Windows\system32\cmd.exe",
        "systemroot": str(tmp_path / "winroot"),
    }

    assert windows_command_shells(lower) == [ShellConfig("cmd.exe", "/c")]


@pytest.mark.parametrize(
    ("comspec", "kept"),
    [
        (r"C:\Windows\system32\cmd.exe", True),
        (r"\\srv\share\cmd.exe", True),
        ("/opt/cmd.exe", True),
        # Absolute but absent is still KEPT — asymmetric with the SystemRoot
        # floor above on purpose: ``%COMSPEC%`` is a shell somebody NAMED, and
        # the caller that spawns falls through a missing one, whereas the
        # floor's path is synthesised and its absence is a bug.
        (r"C:\nope\cmd.exe", True),
        ("cmd.exe", False),
        ("C:cmd.exe", False),
        (r"..\cmd.exe", False),
        (".", False),
        ("", False),
    ],
)
def test_comspec_is_taken_only_when_it_is_absolute(
    tmp_path: Path, comspec: str, kept: bool
) -> None:
    """A relative ``%COMSPEC%`` is DROPPED, which is stricter than CPython.

    ``subprocess.py:1505-1529`` consults ``%SystemRoot%`` when ``%ComSpec%`` is
    unset or empty — never because it is relative — and uses ``isabs`` only to
    decide ``executable=``, so a set-but-relative value still reaches the
    command line there. Aelix cannot inherit that: this site NAMES a shell and
    both spawn sites pass ``lpApplicationName = None``, so no ``executable=``
    can protect it.
    """

    from aelix_ai.utils._shell import windows_command_shells

    empty = tmp_path / "empty"
    empty.mkdir()
    chain = windows_command_shells(
        {
            "PATH": str(empty),
            "COMSPEC": comspec,
            "SYSTEMROOT": str(tmp_path / _NO_SYSROOT),
        }
    )

    expected = (
        [ShellConfig(comspec, "/c"), ShellConfig("cmd.exe", "/c")]
        if kept
        else [ShellConfig("cmd.exe", "/c")]
    )
    assert chain == expected


def test_bash_on_path_is_resolved_absolutely_from_the_given_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The POSIX chain's ``bash`` step: a walk, not a filter over ``which``.

    Rejecting a non-absolute ``which()`` hit would defuse the prepend but keep
    the worse half of the bug: ``which`` returns the FIRST hit and stops, so a
    planted ``bash`` in the cwd makes a real ``/usr/bin/bash`` invisible and
    demotes the box to ``/bin/sh`` — which also flips the AUTO gate's grammar,
    since ``permission.py`` reads this same resolution through
    ``dialect_for_shell``.

    Both rows drive ``env``: before #241 this line called
    ``shutil.which("bash")`` with no ``path=`` at all, reading the process
    environment, so a dict-only assertion would have been vacuous. The
    ``Path.exists`` patch is the technique the POSIX-chain case above already
    uses to pin ``/bin/bash`` out of the way.
    """

    cwd = tmp_path / "repo"
    _executable(cwd, "bash")
    monkeypatch.chdir(cwd)
    bin_dir = tmp_path / "bin"
    real = _executable(bin_dir, "bash")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(Path, "exists", lambda self, **_kw: False)

    assert _resolve_shell(
        {"PATH": os.pathsep + str(bin_dir)}, platform="linux"
    ) == ShellConfig(str(real))
    assert _resolve_shell({"PATH": str(empty)}, platform="linux") == ShellConfig(
        "/bin/sh"
    )
