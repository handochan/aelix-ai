"""#386 — the update notice's installer commands, run by real shells.

THE DEFECT. The notice printed ``AELIX_VERSION=<tag> curl … | sh``. That
assignment is ``curl``'s alone; the ``sh`` on the other side of the pipe never
sees it, so the installer resolved the newest release instead of the one the
notice named. Measured on 0447024d with the real raw.githubusercontent.com
install.sh: asked for ``v0.1.0-beta.1``, it printed "resolving the newest
release from GitHub...", installed ``0.1.0b2`` and exited 0. The only test was
two substring checks, which that line passed.

So what is asserted here is behaviour, not spelling: each command the notice
can print is handed to a real outer shell, that shell fetches the REAL
installer from this checkout (a stub ``curl`` for install.sh, a loopback HTTP
server for install.ps1), and the installer itself has to report the pinned tag.
Nothing leaves the machine: the installer's own downloads are stubbed to fail,
so a pinned run ends at Step 4's "SHA256SUMS not found for '<tag>'", which
names the tag the installer took.

Each harness also has a control that resolves the newest release — the POSIX
harness runs the shape it replaced, the PowerShell one the ``$env:`` spelling
the line avoids — so a harness that could not tell pinned from unpinned would
fail.
"""

from __future__ import annotations

import base64
import http.server
import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import aelix_coding_agent.update_check as uc
import pytest
from aelix_coding_agent.update_check import InstallMethod, upgrade_command

REPO = Path(__file__).resolve().parents[1]
TAG = "v0.1.0-beta.2"
PINNED = f"using pinned release tag: {TAG}"
NEWEST = "resolving the newest release from GitHub"


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


#: Startup files a non-interactive shell reads from the environment rather than
#: from HOME: bash's ``BASH_ENV``, ksh's ``ENV``, zsh's ``ZDOTDIR``, fish's and
#: pwsh's config under ``XDG_CONFIG_HOME``. An inherited one could define a
#: ``curl`` that beats the stub (a reviewer measured exactly that with
#: ``BASH_ENV``), so they go along with exported bash functions.
_STARTUP_VARS = ("BASH_ENV", "ENV", "ZDOTDIR", "XDG_CONFIG_HOME")


def _env(tmp_path: Path, stub_bin: Path | None) -> dict[str, str]:
    """A clean HOME, no inherited shell startup files, none of the installer's knobs."""

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AELIX_", "BASH_FUNC_"))
        and k not in ("GITHUB_TOKEN", "UV_VERSION", *_STARTUP_VARS)
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
    if stub_bin is not None:
        env["PATH"] = str(stub_bin) + os.pathsep + env.get("PATH", "")
    return env


# === the commands themselves ===============================================


def test_the_install_sh_command_puts_the_pin_on_the_sh_side_of_the_pipe() -> None:
    assert upgrade_command(InstallMethod.INSTALL_SH, TAG) == (
        "curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh"
        f" | env AELIX_VERSION={TAG} sh"
    )


def test_the_install_ps1_command_mirrors_the_documented_line_and_has_no_dollar() -> None:
    """No ``$``: from PowerShell, ``$env:…`` inside the double-quoted ``-c``
    string is expanded by the OUTER session, and the child runs unpinned (the
    second control below measures that). ``Set-Item env:`` needs none."""

    cmd = upgrade_command(InstallMethod.INSTALL_PS1, TAG)
    assert cmd is not None
    assert cmd == (
        f"powershell -ExecutionPolicy Bypass -c \"Set-Item env:AELIX_VERSION '{TAG}'; "
        'irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"'
    )
    assert "$" not in cmd


@pytest.mark.parametrize(
    "guide",
    [
        REPO / "docs" / "guides" / "getting-started.md",
        REPO / "packages" / "aelix-coding-agent" / "src" / "aelix_coding_agent" / "docs" / "getting-started.md",
    ],
    ids=["docs", "packaged"],
)
@pytest.mark.parametrize("method", [InstallMethod.INSTALL_SH, InstallMethod.INSTALL_PS1])
def test_the_guide_shows_the_line_the_notice_prints(guide: Path, method: InstallMethod) -> None:
    """The guide spells both lines out by hand; this keeps them the notice's."""

    command = upgrade_command(method, "vX.Y.Z-beta.N")
    assert command is not None
    assert command in guide.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("method", [InstallMethod.INSTALL_SH, InstallMethod.INSTALL_PS1])
@pytest.mark.parametrize("tag", ["v1 ; rm -rf ~", "v1'", 'v1"', "$(id)", "", "-v1", "v1|x"])
def test_a_tag_that_cannot_sit_in_the_command_gets_no_command(
    method: InstallMethod, tag: str
) -> None:
    """The feed is ours, but a tag with a space or a quote in it would print a
    command that does something other than install that tag. The release link
    still shows."""

    assert upgrade_command(method, tag) is None


def test_a_windows_install_with_the_installers_receipt_is_install_ps1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.ps1 writes the same receipt as install.sh (``==`` pin and
    ``--find-links``), so until #386 Windows was told to run ``curl … | sh``."""

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "aelix", specifier = "==0.1.0b2" }]\n'
        '[tool.options]\nfind-links = ["C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\Temp\\\\aelix-install-1"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(uc, "_dist_files", lambda: (None, None))
    monkeypatch.setattr(uc.sys, "prefix", str(prefix))
    assert uc.detect_install_method(platform="win32") is InstallMethod.INSTALL_PS1
    assert uc.detect_install_method(platform="linux") is InstallMethod.INSTALL_SH
    assert uc.detect_install_method(platform="darwin") is InstallMethod.INSTALL_SH


# === install.sh, through every POSIX shell on the machine ==================


_POSIX_SHELLS = ["sh", "bash", "dash", "zsh", "ksh", "csh", "tcsh", "fish"]


def _posix_box(tmp_path: Path) -> Path:
    """Stubs that let the real install.sh run to Step 4 with no network.

    ``curl`` serves this checkout's install.sh for the command's own fetch and
    fails every other URL; ``uv`` exists so Step 2 does not bootstrap one;
    ``mktemp`` keeps the installer's download dir inside the test's tree.
    """

    stub_bin = tmp_path / "stubs"
    log = tmp_path / "curl-calls"
    _write_exe(
        stub_bin / "curl",
        f'printf "%s\\n" "$*" >> {shlex.quote(str(log))}\n'
        "for last; do :; done\n"
        'case "$last" in\n'
        f"  */install.sh) cat {shlex.quote(str(REPO / 'install.sh'))} ;;\n"
        "  *) exit 22 ;;\n"
        "esac\n",
    )
    _write_exe(stub_bin / "uv", "exit 0\n")
    temp_root = tmp_path / "mktemp-root"
    temp_root.mkdir()
    real_mktemp = shutil.which("mktemp") or "/usr/bin/mktemp"
    _write_exe(
        stub_bin / "mktemp",
        f"exec {shlex.quote(real_mktemp)} -d {shlex.quote(str(temp_root))}/tmp.XXXXXXXX\n",
    )
    return stub_bin


def _run_posix(tmp_path: Path, shell: str, command: str) -> str:
    stub_bin = _posix_box(tmp_path)
    proc = subprocess.run(
        [shell, "-c", command],
        env=_env(tmp_path, stub_bin),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.stdout + proc.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells; install.ps1 is measured below")
@pytest.mark.parametrize("shell", _POSIX_SHELLS)
def test_install_sh_takes_the_pin_from_the_command_the_notice_prints(
    tmp_path: Path, shell: str
) -> None:
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} is not installed")
    out = _run_posix(tmp_path, exe, upgrade_command(InstallMethod.INSTALL_SH, TAG) or "")

    assert PINNED in out, out
    assert NEWEST not in out, out
    assert f"SHA256SUMS not found for '{TAG}'" in out, out
    first_fetch = (tmp_path / "curl-calls").read_text(encoding="utf-8").splitlines()[0]
    assert first_fetch == f"-fsSL {uc._INSTALL_SH_URL}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells")
def test_control_the_old_shape_hands_curl_the_pin_and_sh_installs_the_newest(
    tmp_path: Path,
) -> None:
    old = f"AELIX_VERSION={TAG} curl -fsSL {uc._INSTALL_SH_URL} | sh"
    out = _run_posix(tmp_path, "/bin/sh", old)

    assert NEWEST in out, out
    assert PINNED not in out, out


# === install.ps1, from cmd.exe and from PowerShell =========================


#: Prepended to the real install.ps1 the loopback server hands ``irm``. Function
#: lookup beats cmdlet lookup, so these shadow the installer's own downloads
#: (and stand in for uv, so Step 2 never bootstraps one) for that session only.
_PS_STUBS = (
    "function uv { }\n"
    "function Invoke-WebRequest { throw 'offline: test stub' }\n"
    "function Invoke-RestMethod { throw 'offline: test stub' }\n"
)


@pytest.fixture
def ps1_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    body = (_PS_STUBS + (REPO / "install.ps1").read_text(encoding="utf-8")).encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the stdlib's name
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/install.ps1"
    monkeypatch.setattr(uc, "_INSTALL_PS1_URL", url)
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


def _powershell_box(tmp_path: Path) -> tuple[Path | None, list[str]]:
    """``(stub_bin, outer shells)``: Windows has ``powershell``; elsewhere a
    ``powershell`` shim runs ``pwsh``, so the same line is measured."""

    if sys.platform == "win32":
        outers = ["cmd"] + [s for s in ("powershell", "pwsh") if shutil.which(s)]
        return None, outers
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("no pwsh on this machine")
    stub_bin = tmp_path / "stubs"
    _write_exe(stub_bin / "powershell", f'exec {shlex.quote(pwsh)} "$@"\n')
    return stub_bin, ["sh", "pwsh"]


def _run_from(tmp_path: Path, outer: str, command: str, stub_bin: Path | None) -> str:
    env = _env(tmp_path, stub_bin)
    if outer == "cmd":
        # What typing the line into cmd.exe does: ``cmd /c "<line>"``.
        argv: list[str] | str = command
        shell = True
    elif outer == "sh":
        argv, shell = ["/bin/sh", "-c", command], False
    else:
        # What typing the line into a PowerShell session does: the session
        # parses exactly this text, so no layer of quoting sits in between.
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        exe = shutil.which(outer, path=env["PATH"]) or outer
        argv, shell = [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], False
    proc = subprocess.run(
        argv,
        shell=shell,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.stdout + proc.stderr


def test_install_ps1_takes_the_pin_from_the_command_the_notice_prints(
    tmp_path: Path, ps1_url: str
) -> None:
    stub_bin, outers = _powershell_box(tmp_path)
    command = upgrade_command(InstallMethod.INSTALL_PS1, TAG)
    assert command is not None and ps1_url in command
    for outer in outers:
        out = _run_from(tmp_path, outer, command, stub_bin)
        assert PINNED in out, (outer, out)
        assert NEWEST not in out, (outer, out)


def test_control_env_dollar_in_the_quoted_line_is_lost_from_powershell(
    tmp_path: Path, ps1_url: str
) -> None:
    """Why the command spells the assignment without ``$``: the obvious
    ``$env:`` spelling, typed into PowerShell, installs the newest release.
    (The shape the Windows line replaced was the POSIX one, which PowerShell
    does not run at all.)"""

    stub_bin, outers = _powershell_box(tmp_path)
    naive = (
        'powershell -ExecutionPolicy Bypass -c "'
        f"$env:AELIX_VERSION='{TAG}'; irm {ps1_url} | iex\""
    )
    outer = "powershell" if "powershell" in outers else "pwsh"
    out = _run_from(tmp_path, outer, naive, stub_bin)
    assert NEWEST in out, out
    assert PINNED not in out, out
