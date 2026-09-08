"""#239 — the one shell family asked for UTF-8 output, and the one that stopped.

The preamble is a NARROWING, not the fix: PowerShell parses the whole
``-Command`` script before it runs any of it, so a parse error discards the
preamble with it — measured on pwsh 7.6.5, ``pwsh -Command 'Write-Output
"PREAMBLE-RAN"; echo a | | echo b'`` prints the ``ParserError`` and never
``PREAMBLE-RAN``. #239's reported failure is that exact shape, so the DECODER
(``aelix_ai.utils._child_output``) is what fixes the reported bug. These cases
pin the preamble's spelling; the win32 ones at the bottom are the only place its
effect on a real console can be observed.

THE ``cmd`` ARM IS GONE AS OF 2026-09-09, and half of this file is what pins
that. It was ``chcp 65001 >nul&`` (on this branch only: never on ``main``,
never in a release), and the win32 probe at the bottom —
written by #239's cross-review because nobody on this project can run Windows,
and the only thing in the repo that drives a real ``cmd.exe`` — fired on its
FIRST execution: CI run 34272507388, windows-latest, py3.11 and py3.12,
``[bare]`` only, ``'C:\\…\\a' is not recognized as an internal or external
command``. :func:`aelix_ai.utils._shell.utf8_output_preamble` carries the
mechanism, the two shapes the same run cleared, and the price of the removal.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from aelix_ai.utils._child_output import decode_child_output
from aelix_ai.utils._shell import (
    POWERSHELL_UTF8_PREAMBLE,
    utf8_output_preamble,
    windows_command_shells,
)
from aelix_coding_agent.tools.bash import create_local_bash_operations


def test_the_powershell_preamble_cannot_fail_the_command() -> None:
    """``[Console]::OutputEncoding`` is the setter that reaches native children.

    It calls ``SetConsoleOutputCP``, so ``git``/``uv`` sharing the console
    follow it, which ``$OutputEncoding`` (PowerShell's own pipeline encoding)
    would not. The ``try{}catch{}`` is not decoration: a console-less host can
    throw there, and a preamble that fails the user's command is worse than the
    mojibake it was added to prevent.
    """

    for shell in ("pwsh", "powershell", r"C:\Program Files\PowerShell\7\pwsh.exe", "pwsh-6.2.exe"):
        preamble = utf8_output_preamble(shell)
        assert "[Console]::OutputEncoding" in preamble
        assert preamble.startswith("try{")
        assert "}catch{}" in preamble
        assert preamble.endswith(";")


def test_the_cmd_family_gets_nothing_prepended() -> None:
    """The ``cmd`` arm is a MEASURED removal, not a simplification.

    It returned ``chcp 65001 >nul&`` until 2026-09-09. ``cmd /c``'s rule 1
    keeps the quotes ``subprocess.list2cmdline`` puts around a lone spaced
    executable path ONLY while the whole text between them is the name of an
    executable file, so a prefix — any prefix, the ``&`` and the ``>`` are
    incidental — moves the command to rule 2, which strips them. CI run
    34272507388 measured what is left: ``cmd`` answers ``'C:\\…\\a' is not
    recognized as an internal or external command`` and the command does not
    run, on windows-latest under both py3.11 and py3.12.

    So there is no safe spelling and the arm is gone. What covers a ``cmd``
    child's non-UTF-8 output now is
    :func:`aelix_ai.utils._child_output.decode_child_output`, which is what
    #239 is about; the price is written down in
    :func:`aelix_ai.utils._shell.utf8_output_preamble`.
    """

    for shell in ("cmd", "cmd.exe", r"C:\Windows\system32\cmd.exe", "CMD.EXE"):
        assert utf8_output_preamble(shell) == ""


def test_posix_gets_no_preamble_at_all() -> None:
    """POSIX children already speak UTF-8 by locale.

    Exporting ``LC_ALL`` here would be a behaviour change with no bug behind
    it, and it is what keeps the POSIX spawn byte-identical to before #239.
    """

    for shell in ("/bin/sh", "/bin/bash", "bash-5.2", "/usr/bin/zsh", "fish", "/usr/bin/ksh93"):
        assert utf8_output_preamble(shell) == ""
    # ``command.com`` never matched the ``cmd`` row even while there was one,
    # for the reason ``_shell_argv`` records: ``shell_basename`` strips only
    # ``.exe``, so the name never matches. Kept as a case because the naming gap
    # itself is still live in ``command_flag_for`` (#104) — here it is now
    # answered by the same fall-through as every POSIX name above.
    assert utf8_output_preamble("command.com") == ""


def test_the_choice_is_the_shell_family_and_not_the_platform() -> None:
    """``SHELL=/usr/local/bin/pwsh`` gets the preamble on macOS too (#239 review).

    Nothing on the path from ``_resolve_shell`` (which takes ``$SHELL``
    verbatim off Windows) to :func:`utf8_output_preamble` (which keys off
    :func:`shell_basename`) tests the platform, and the spawn comment used to
    say "Empty on POSIX". It is not — and that is the wanted answer, because
    pwsh's output encoding is pwsh's wherever it runs. What IS platform-free is
    the POSIX shell FAMILIES, which is what the case above pins.
    """

    assert utf8_output_preamble("/usr/local/bin/pwsh") == POWERSHELL_UTF8_PREAMBLE
    assert utf8_output_preamble("/opt/homebrew/bin/pwsh") == POWERSHELL_UTF8_PREAMBLE


def test_the_credential_command_argv_is_unchanged_on_every_win32_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``!command`` gets the decoder but NOT the preamble.

    Its stdout is a credential returned verbatim, and ``_resolve_config``
    records what one stray byte costs there: a PowerShell profile that writes a
    banner was measured landing inside the key as ``profile-banner\\nsk-KEY``.
    A preamble that ever printed would corrupt a key silently, and there is no
    such thing as a non-ASCII API key — so the risk is one-sided and this site
    keeps #227's argv byte for byte.

    THE COUNT IS DERIVED, AND THAT IS THE POINT OF THE SECOND ASSERTION. This
    case was written as "…on all six candidates" against a hard-coded ``6``,
    and #241 — landing in the same release — added the
    ``%SystemRoot%\\System32\\cmd.exe`` candidate, so the integration branch's
    win32 leg failed it with ``assert 7 == 6`` (CI run 34272507388). The chain's
    length is :func:`~aelix_ai.utils._shell.windows_command_shells`'s to state;
    what this case is actually about is that EVERY candidate it yields is
    preamble-free, so it asks that function how many there are and the next
    candidate does not repeat the failure. The floor stays a literal on purpose:
    it guards the FIXTURE, not the chain — if this env stopped exercising the
    optional ``$SHELL`` and ``sh`` steps the loop below would still pass while
    covering less, and six is what those steps produced when this was written.
    """

    import aelix_ai.oauth._resolve_config as rc

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("sh", "pwsh", "powershell"):
        exe = bin_dir / (f"{name}.exe" if sys.platform == "win32" else name)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    shell_exe = bin_dir / ("zsh.exe" if sys.platform == "win32" else "zsh")
    shell_exe.write_text("#!/bin/sh\n", encoding="utf-8")
    shell_exe.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    cmd = "printf x"
    env = {
        "PATH": str(bin_dir),
        "SHELL": str(shell_exe),
        "COMSPEC": r"C:\Windows\system32\cmd.exe",
    }
    candidates = rc._shell_argv_candidates(cmd, platform="win32", env=env)

    # ``include_posix_sh=True`` because that is what ``_shell_argv_candidates``
    # passes — a ``!command`` was written for ``sh``, where the bash tool must
    # not take one (ADR-0237/#204).
    assert len(candidates) == len(windows_command_shells(env, include_posix_sh=True))
    assert len(candidates) >= 6, "the fixture stopped exercising $SHELL and sh"
    for candidate in candidates:
        rendered = candidate if isinstance(candidate, str) else " ".join(candidate)
        assert "chcp" not in rendered
        assert "OutputEncoding" not in rendered
        if isinstance(candidate, list):
            assert candidate[-1] == cmd
        else:
            assert candidate.endswith(f'"{cmd}"')


# === win32-only, and real spawns — see ``§E`` of the design =================
#
# These carry a ``skipif`` where the rest of this repo's Windows coverage
# deliberately does not, because what they assert is the state of a REAL
# console and of a REAL ``cmd``'s quote handling: no injection can produce
# either. CI's win32 leg resolves ``pwsh`` 7, which already defaults to UTF-8 —
# so without forcing the shell here the ``cmd`` arm and the PowerShell-5.1
# preamble would never be executed anywhere.
#
# THIS SECTION IS WHY THE ``cmd`` ARM IS GONE. It was written on darwin, could
# not be run on darwin, and on its first leg (CI run 34272507388) it reported
# the product regression its author had reasoned might exist. The rewrite below
# keeps every case pointed at the same surface, with the assertions inverted to
# the answer the leg gave.


@pytest.mark.skipif(sys.platform != "win32", reason="a real Windows console is the assertion")
async def test_win32_powershell_51_parses_the_preamble(tmp_path: Path) -> None:
    powershell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    powershell = powershell / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.exists():
        pytest.skip(f"no Windows PowerShell 5.1 at {powershell}")

    ops = create_local_bash_operations(str(powershell))
    chunks: list[bytes] = []
    result = await ops.exec("echo hi", str(tmp_path), on_data=chunks.append)

    assert result.exit_code == 0
    assert decode_child_output(b"".join(chunks)).strip() == "hi"


@pytest.mark.skipif(sys.platform != "win32", reason="a real Windows console is the assertion")
async def test_win32_a_cmd_command_prints_only_its_own_output(tmp_path: Path) -> None:
    """Nothing is prepended, so nothing extra can reach the model.

    Until 2026-09-09 this case earned its keep by proving ``>nul`` had eaten
    ``chcp``'s "Active code page: 65001" banner. There is no banner to eat now,
    and the case is kept for the property that outlived the preamble: whatever a
    future contributor prepends here has to be silent, and a print-visible one
    turns this red on the leg.
    """

    comspec = os.environ.get("COMSPEC") or r"C:\Windows\system32\cmd.exe"

    ops = create_local_bash_operations(comspec)
    chunks: list[bytes] = []
    result = await ops.exec("echo hi", str(tmp_path), on_data=chunks.append)

    assert result.exit_code == 0
    assert decode_child_output(b"".join(chunks)).strip() == "hi"


@pytest.mark.skipif(sys.platform != "win32", reason="a real Windows console is the assertion")
async def test_win32_a_cmd_command_does_not_move_the_console_code_page(tmp_path: Path) -> None:
    """The bash tool's ``cmd`` spawn now reports the page a plain spawn does.

    This case is the old ``…actually_reaches_code_page_65001`` with its
    assertion inverted, and it is the one that would have gone red the moment
    somebody put the preamble back. It asks nothing about WHICH page the console
    is on — the runner's own default is not this repo's business — only that
    Aelix's spawn and a bare :func:`subprocess.run` agree, which is exactly the
    property "nothing is prepended" means for a REAL ``cmd``.

    IT ONLY MEANS THAT FROM A KNOWN START, and the reason is the one the
    preamble's own case recorded (#239 review): children spawn with
    ``containment_spawn_kwargs()`` — ``CREATE_NEW_PROCESS_GROUP``, no
    ``CREATE_NEW_CONSOLE`` — so on a host WITH a console they share this
    process's, and a case that ran earlier could have left it on 65001. Anything
    still reading 65001 after the reset would make the comparison vacuous
    against the old behaviour, so that SKIPS. Restored in a ``finally``: the
    console is shared with everything that runs after.
    """

    comspec = os.environ.get("COMSPEC") or r"C:\Windows\system32\cmd.exe"

    def _page(out: bytes) -> str | None:
        # ``chcp``'s banner is localised ("Active code page: 437", "활성 코드
        # 페이지: 949"), so the NUMBER is the only portable part of it.
        found = re.search(r"\d{3,5}", decode_child_output(out))
        return found.group(0) if found else None

    def _plain_page() -> str | None:
        probe = subprocess.run(  # noqa: S603
            [comspec, "/c", "chcp"], capture_output=True, check=False
        )
        return _page(probe.stdout)

    before = _plain_page()
    try:
        subprocess.run(  # noqa: S603
            [comspec, "/c", "chcp 437 >nul"], capture_output=True, check=False
        )
        control = _plain_page()
        if control is None or control == "65001":
            pytest.skip(f"the reset off 65001 did not take ({control!r}); this proves nothing")

        ops = create_local_bash_operations(comspec)
        chunks: list[bytes] = []
        result = await ops.exec("chcp", str(tmp_path), on_data=chunks.append)

        assert result.exit_code == 0
        assert _page(b"".join(chunks)) == control
    finally:
        if before is not None:
            subprocess.run(  # noqa: S603
                [comspec, "/c", f"chcp {before} >nul"], capture_output=True, check=False
            )


@pytest.mark.skipif(sys.platform != "win32", reason="a real Windows console is the assertion")
def test_win32_the_console_page_is_what_the_fallback_names() -> None:
    """``win32_output_fallbacks`` asks the live console, not a cached constant.

    A child that ran ``chcp`` changed the page this process shares with it, so
    the answer has to be re-read; ``os.device_encoding(1)`` is that read, and
    under pytest (stdout captured, no console) it is expected to be ``None``,
    leaving ``oem``.
    """

    from aelix_ai.utils._child_output import win32_output_fallbacks

    assert win32_output_fallbacks()[-1] == "oem"


@pytest.mark.skipif(sys.platform != "win32", reason="only a real cmd.exe can settle the quoting rules")
@pytest.mark.parametrize("shape", ["bare", "unquoted-with-arg", "quoted-with-arg"])
async def test_win32_the_cmd_arm_does_not_break_a_spaced_executable_path(
    tmp_path: Path, shape: str
) -> None:
    """#239 cross-review finding 8, and the case that DID fire (run 34272507388).

    It was written as an open question and came back with an answer, so the
    docstring is now a record rather than a hypothesis. ``list2cmdline`` wraps
    the whole command in quotes, so the bare shape reaches ``cmd`` as
    ``/c "C:\\a dir\\probe.exe"`` — exactly two quote characters, no ``&<>()@^|``
    between them, whitespace between them, and the string between them the name
    of an executable file. That is ``cmd /?``'s rule 1 and ``cmd`` KEEPS the
    quotes. ``chcp 65001 >nul&`` in front of it moved the command to rule 2,
    which strips them, and on windows-latest under both py3.11 and py3.12 the
    ``[bare]`` parameter failed::

        'C:\\Users\\runneradmin\\AppData\\Local\\Temp\\…\\a' is not recognized as
        an internal or external command, operable program or batch file.

    So this case is why :func:`utf8_output_preamble` has no ``cmd`` arm any
    more, and it now guards the removal: the command Aelix hands ``cmd`` is the
    control's command, byte for byte, and any prefix a future change puts in
    front of it fails here again rather than in a user's terminal.

    The other two shapes are the controls that made this finding-shaped rather
    than shotgun, and the same run cleared both: with an argument the string
    between the quotes is no longer the name of an executable file, so rule 2
    already applied before the preamble existed; and a QUOTED path arrives with
    FOUR quote characters, because ``list2cmdline`` escapes the model's inner
    pair as ``\\"``, so rule 1 never applied to it either — which REFUTES the
    cross-review's own reading, that the quoted shape was the one at risk. Both
    must behave exactly as they do without a preamble, which is what the
    ``subprocess.run`` control below establishes per shape — a control failure
    SKIPS instead of failing, because it means the shape is broken for reasons
    this test is not the witness of.
    """

    system32 = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    comspec = os.environ.get("COMSPEC") or str(system32 / "cmd.exe")
    # ``whoami`` for the bare shape (it runs and exits 0 with no arguments);
    # a copy of ``cmd`` for the shapes that need one, so ``/c echo`` proves
    # the ARGUMENTS survived too. Both are self-contained System32 binaries
    # whose imports are KnownDLLs, so a copy elsewhere still starts.
    source = system32 / ("whoami.exe" if shape == "bare" else "cmd.exe")
    if not source.exists():
        pytest.skip(f"no probe executable at {source}")

    spaced = tmp_path / "a dir with spaces"
    spaced.mkdir()
    probe = spaced / "probe tool.exe"
    shutil.copyfile(source, probe)

    if shape == "bare":
        command = str(probe)
    elif shape == "unquoted-with-arg":
        command = f"{probe} /c echo MARKER-OK"
    else:
        command = f'"{probe}" /c echo MARKER-OK'

    control = subprocess.run(  # noqa: S603
        [comspec, "/c", command], capture_output=True, check=False
    )
    if control.returncode != 0:
        pytest.skip(
            "this shape does not reach cmd intact through a bare subprocess.run either, "
            "so the bash tool cannot be what broke it: "
            f"{control.returncode} {decode_child_output(control.stdout + control.stderr)!r}"
        )

    ops = create_local_bash_operations(comspec)
    chunks: list[bytes] = []
    result = await ops.exec(command, str(tmp_path), on_data=chunks.append)
    out = decode_child_output(b"".join(chunks), ragged_tail=True)

    assert result.exit_code == 0, f"the bash tool's cmd spawn broke {command!r}: {out!r}"
    if shape == "bare":
        assert out.strip(), "whoami printed nothing, so exit 0 proves nothing"
    else:
        assert "MARKER-OK" in out
