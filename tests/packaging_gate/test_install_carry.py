"""#926 (ADR-0321): a re-run of the install line keeps the installed extensions.

``uv tool install --force`` rebuilds the aelix tool environment from its
``uv-receipt.toml``; an extension ``aelix extension install`` put there with
``uv pip install`` is not in that receipt, so the reinstall dropped it (measured
on uv 0.11.19 and 0.13.10, the issue's reproduction). Both installers now carry
one Python program (``carry.py``) that reads the CURRENT environment before the
reinstall (``collect``) and the NEW one after it (``check``), and a few lines of
shell around it. This file:

* holds the two copies of the program equal (install.ps1 cannot be executed here),
* executes the program on fake environments, one test per PEP 610 shape it reads,
  per receipt shape, and for the hash check that takes a differing copy out,
* executes install.sh's own Step 5 lines with a stub ``uv`` and stub environment
  programs, for every path through them (carried, taken out, failed, nothing to
  carry), because those lines decide whether an update can fail on an extension.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import child_env

_ROOT = Path(__file__).resolve().parents[2]
_SH = _ROOT / "install.sh"
_PS1 = _ROOT / "install.ps1"
_SHA = "6f4b00a7340b44135afe726152663097fe304758"


def _sh_program() -> str:
    text = _SH.read_text(encoding="utf-8")
    m = re.search(r"<<'AELIX_CARRY_PY'\n(.*?)^AELIX_CARRY_PY$", text, re.DOTALL | re.MULTILINE)
    assert m, "install.sh carries no carry.py heredoc"
    return m.group(1)


def _ps1_program() -> str:
    text = _PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"\$carrySource = @'\n(.*?)^'@$", text, re.DOTALL | re.MULTILINE)
    assert m, "install.ps1 carries no carry.py here-string"
    return m.group(1)


def test_both_installers_carry_the_same_program() -> None:
    assert _sh_program() == _ps1_program()
    assert all(ord(ch) < 128 for ch in _sh_program()), "install.ps1 must stay ASCII"


@pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff is a dev dependency")
def test_the_program_passes_the_repository_lint(tmp_path: Path) -> None:
    """Verify r2 N5: carry.py lives only as installer text, outside every lint gate.
    (pyright is not run here: it needs its node runtime; it was run by hand in #926's
    fix round 2 and reported none.)"""

    program = tmp_path / "carry.py"
    program.write_text(_sh_program(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--config",
            str(_ROOT / "pyproject.toml"),
            str(program),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# === the program, on fake environments ======================================


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _dist(
    site: Path,
    name: str,
    version: str = "1.0.0",
    *,
    extension: bool = True,
    direct_url: dict[str, Any] | None = None,
    body: bytes = b"def setup(api=None):\n    return None\n",
    installer: str = "uv",
    claimed: bytes | None = None,
    record: bool = True,
) -> None:
    """A dist-info as ``installer`` leaves it. pip records the bytecode it compiled,
    with hashes, and writes its own console-script launcher; uv compiles nothing by
    default and writes another launcher. Neither is the extension's code.

    ``claimed``: the bytes the RECORD row claims for the module, when it lies about
    ``body`` (uv 0.11.19 and 0.13.10 install such a wheel and keep its RECORD as it
    came, measured). ``record=False``: no RECORD at all."""

    mod = name.replace("-", "_")
    info = site / f"{mod}-{version}.dist-info"
    info.mkdir(parents=True)
    (site / mod).mkdir(exist_ok=True)
    (site / mod / "__init__.py").write_bytes(body)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8"
    )
    if extension:
        (info / "entry_points.txt").write_text(
            f"[aelix.extensions]\n{name} = {mod}:setup\n", encoding="utf-8"
        )
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    rows = [
        f"{mod}/__init__.py,{_record_hash(body if claimed is None else claimed)},{len(body)}",
        f"../../../bin/{mod},{_record_hash(installer.encode())},6",
        f"{info.name}/INSTALLER,{_record_hash(installer.encode())},3",
        f"{info.name}/RECORD,,",
    ]
    if installer == "pip":
        rows.append(f"{mod}/__pycache__/__init__.cpython-313.pyc,{_record_hash(b'pyc')},3")
        (site / mod / "__pycache__").mkdir()
        (site / mod / "__pycache__" / "__init__.cpython-313.pyc").write_bytes(b"pyc")
    (site / ".." / ".." / ".." / "bin").mkdir(exist_ok=True)
    (site / ".." / ".." / ".." / "bin" / mod).write_bytes(installer.encode())
    (info / "INSTALLER").write_bytes(installer.encode())
    rows.append(f"{mod}/gone.py,{_record_hash(b'gone')},4")  # named, not on disk
    if record:
        (info / "RECORD").write_text("\n".join(rows) + "\n", encoding="utf-8")


class _Carry:
    """The program loaded in-process, reading ``site`` as its environment."""

    def __init__(self, site: Path, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
        self.ns: dict[str, Any] = {"__name__": "carry"}
        exec(compile(_sh_program(), "carry.py", "exec"), self.ns)  # noqa: S102 - the installer's own program
        self.site = site
        self.env_dir = tmp / "env"
        self.env_dir.mkdir(exist_ok=True)
        self.state = tmp / "state"
        self.state.mkdir()
        self.agent = tmp / "agent"
        self.agent.mkdir()
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(self.agent))
        monkeypatch.setattr(
            importlib.metadata,
            "distributions",
            lambda: importlib.metadata.Distribution.discover(path=[str(site)]),
        )

    def receipt(self, *entries: str) -> None:
        body = '[tool]\nrequirements = [\n    { name = "aelix", extras = ["tui"], specifier = "==0.1.0" },\n'
        body += "".join(f"    {e},\n" for e in entries) + ']\npython = ">=3.11,<3.15"\n'
        (self.env_dir / "uv-receipt.toml").write_text(body, encoding="utf-8")

    def collect(self, capsys: pytest.CaptureFixture[str]) -> tuple[list[str], str]:
        assert self.ns["collect"](str(self.env_dir), str(self.state)) == 0
        out = capsys.readouterr().out
        return (self.state / "with.txt").read_text(encoding="utf-8").splitlines(), out

    def check(self, taken: Path | None = None) -> int:
        return int(self.ns["check"](str(self.state), str(taken or self.state / "with.txt")))

    def restore(self) -> list[str]:
        return (self.state / "restore.txt").read_text(encoding="utf-8").splitlines()


@pytest.fixture
def carry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Carry:
    site = tmp_path / "env" / "lib" / "python3" / "site-packages"
    site.mkdir(parents=True)
    c = _Carry(site, monkeypatch, tmp_path)
    c.receipt()
    return c


def test_an_index_install_is_carried_as_its_exact_version(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "acme-tools", "1.2.0")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "carrying acme-tools 1.2.0 as acme-tools==1.2.0" in out


def test_a_git_install_is_carried_at_its_commit(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "https://github.com/handochan/aelix-memory.git"
    vcs = {"vcs": "git", "commit_id": _SHA, "requested_revision": _SHA}
    _dist(carry.site, "aelix-memory", direct_url={"url": url, "vcs_info": vcs})
    _dist(carry.site, "sub-ext", direct_url={"url": url, "vcs_info": vcs, "subdirectory": "pkg"})
    lines, _ = carry.collect(capsys)
    assert lines == [
        f"aelix-memory @ git+{url}@{_SHA}",
        f"sub-ext @ git+{url}@{_SHA}#subdirectory=pkg",
    ]
    assert carry.restore()[0] == (
        f"aelix-install: not carried: aelix-memory 1.0.0 - reinstall it with: "
        f"aelix extension install 'git+{url}@{_SHA}'"
    )


def test_a_checkout_that_is_not_git_is_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(
        carry.site,
        "hg-ext",
        direct_url={"url": "https://h/r", "vcs_info": {"vcs": "hg", "commit_id": _SHA}},
    )
    lines, out = carry.collect(capsys)
    assert lines == []
    assert (
        "not carried: hg-ext 1.0.0 - it came from a hg checkout this installer cannot pin, "
        "so there is no command to reinstall it from here"
    ) in out


def test_a_commit_that_is_not_a_full_sha_is_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(
        carry.site,
        "short-ext",
        direct_url={"url": "https://h/r.git", "vcs_info": {"vcs": "git", "commit_id": "abc1234"}},
    )
    lines, out = carry.collect(capsys)
    assert lines == []
    assert "not carried: short-ext 1.0.0 - it came from a git checkout this installer" in out


def test_a_directory_is_carried_while_it_exists(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    src = tmp_path / "src dir"
    src.mkdir()
    _dist(carry.site, "dir-ext", direct_url={"url": src.as_uri(), "dir_info": {}})
    _dist(carry.site, "gone-ext", direct_url={"url": (tmp_path / "gone").as_uri(), "dir_info": {}})
    _dist(carry.site, "edit-ext", direct_url={"url": src.as_uri(), "dir_info": {"editable": True}})
    lines, out = carry.collect(capsys)
    assert lines == [f"dir-ext @ {src.as_uri()}", f"-e {src.as_uri()}"]
    assert "not carried: gone-ext 1.0.0 - its source directory" in out
    assert "is gone or has a '#' in it, so there is no command to reinstall it from here" in out
    py = carry.env_dir / "bin" / "python"
    assert carry.restore() == [
        f"aelix-install: not carried: dir-ext 1.0.0 - reinstall it with: "
        f"aelix extension install '{src}'",
        f"aelix-install: not carried: edit-ext 1.0.0 - reinstall it with: "
        f"uv pip install --python '{py}' -e '{src.as_uri()}'",
    ]


def test_a_file_is_carried_from_where_it_is_or_from_its_pin(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    kept = tmp_path / "kept-1.0.0-py3-none-any.whl"
    kept.write_bytes(b"wheel")
    _dist(carry.site, "kept", direct_url={"url": kept.as_uri(), "archive_info": {}})
    # `aelix extension install <wheel>` installs a staged copy in a temp dir that is
    # gone; the path pin names the original and its sha256 (ADR-0187).
    original = tmp_path / "orig" / "staged-1.0.0-py3-none-any.whl"
    original.parent.mkdir()
    original.write_bytes(b"approved bytes")
    staged = tmp_path / "aelix-verify-x" / original.name
    _dist(carry.site, "staged", direct_url={"url": staged.as_uri(), "archive_info": {}})
    changed = tmp_path / "orig" / "changed-1.0.0-py3-none-any.whl"
    changed.write_bytes(b"other bytes")
    _dist(
        carry.site,
        "changed",
        direct_url={"url": (tmp_path / "t" / changed.name).as_uri(), "archive_info": {}},
    )
    other = tmp_path / "orig" / "gitpinned-1.0.0-py3-none-any.whl"
    other.write_bytes(b"g")
    _dist(
        carry.site,
        "gitpinned",
        direct_url={"url": (tmp_path / "t2" / other.name).as_uri(), "archive_info": {}},
    )
    _dist(
        carry.site,
        "remote",
        direct_url={"url": "https://e.invalid/remote-1.0.whl", "archive_info": {}},
    )
    pins = {
        str(original): {
            "kind": "path",
            "mode": "tofi",
            "sha256": hashlib.sha256(b"approved bytes").hexdigest(),
        },
        str(changed): {
            "kind": "path",
            "mode": "tofi",
            "sha256": hashlib.sha256(b"approved").hexdigest(),
        },
        # Only a PATH pin vouches for a file's bytes.
        str(other): {"kind": "git", "mode": "tofi", "sha256": hashlib.sha256(b"g").hexdigest()},
    }
    (carry.agent / "extension_pins.json").write_text(
        json.dumps({"version": 1, "pins": pins}), encoding="utf-8"
    )
    lines, out = carry.collect(capsys)
    assert (
        "not carried: gitpinned 1.0.0 - the file it was installed from is gone "
        "(gitpinned-1.0.0-py3-none-any.whl), so there is no command to reinstall it from here"
    ) in out
    assert lines == [
        f"kept @ {kept.as_uri()}",
        "remote @ https://e.invalid/remote-1.0.whl",
        f"staged @ {original.as_uri()}",
    ]
    assert "not carried: changed 1.0.0 - the file it was installed from is gone" in out
    py = carry.env_dir / "bin" / "python"
    assert carry.restore()[1] == (
        "aelix-install: not carried: remote 1.0.0 - reinstall it with: "
        f"uv pip install --python '{py}' 'remote @ https://e.invalid/remote-1.0.whl'"
    )


def test_a_path_with_a_hash_sign_is_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """uv 0.11.19 reads %23 in a file URI as '#', then as a fragment: measured,
    ``Distribution not found at: file:///.../sp%20ace#x/editext``."""

    src = tmp_path / "a#b"
    src.mkdir()
    _dist(carry.site, "hash-ext", direct_url={"url": src.as_uri(), "dir_info": {}})
    lines, out = carry.collect(capsys)
    assert lines == []
    assert "not carried: hash-ext 1.0.0" in out


def test_first_party_and_plain_libraries_are_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "aelix-coding-agent", "0.1.0")
    _dist(carry.site, "rich", "13.0.0", extension=False)
    lines, out = carry.collect(capsys)
    assert lines == []
    assert out == ""


def test_the_receipt_is_carried_for_what_is_still_installed(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _dist(carry.site, "pip", "25.0", extension=False)
    _dist(carry.site, "lib-git", extension=False)
    _dist(carry.site, "lib-dir", extension=False)
    _dist(carry.site, "lib-file", extension=False)
    _dist(carry.site, "lib-edit", extension=False)
    _dist(carry.site, "lib-url", extension=False)
    _dist(carry.site, "lib-tag", extension=False)
    _dist(carry.site, "lib-branch", extension=False)
    _dist(carry.site, "lib-cr", extension=False)
    _dist(carry.site, "lib-marker", extension=False)
    _dist(carry.site, "odd", extension=False)
    _dist(carry.site, "lib-extra", extension=False)
    _dist(carry.site, "lib-lfs", extension=False)
    _dist(carry.site, "lib-edit-extra", extension=False)
    # An extension the receipt names at an OLD commit, updated since with
    # `aelix extension install`: what is installed wins, not the receipt.
    vcs = {"vcs": "git", "commit_id": _SHA}
    _dist(carry.site, "moved-ext", direct_url={"url": "https://h/m.git", "vcs_info": vcs})
    carry.receipt(
        '{ name = "pip" }',
        '{ name = "lib-git", git = "https://h/r.git?rev=abc&subdirectory=s" }',
        f'{{ name = "lib-dir", directory = "{tmp_path / "d"}" }}',
        f'{{ name = "lib-file", path = "{tmp_path / "f.whl"}" }}',
        f'{{ name = "lib-edit", editable = "{tmp_path / "e"}" }}',
        '{ name = "lib-url", extras = ["x"], url = "https://h/u.whl" }',
        '{ name = "lib-tag", git = "https://h/t.git?tag=v1" }',
        '{ name = "lib-branch", git = "https://h/b.git?branch=main" }',
        '{ name = "lib-cr", specifier = "==1\\n" }',
        '{ name = "lib-marker", specifier = ">=1", marker = "python_full_version >= \'3.11\'" }',
        '{ name = "odd", index = "https://private.invalid/simple" }',
        '{ name = "removed-since" }',
        '{ name = "lib-extra", extras = ["a", "b"], specifier = "==2" }',
        '{ name = "lib-lfs", git = "https://h/l.git?rev=abc&lfs=true" }',
        f'{{ name = "lib-edit-extra", extras = ["a"], editable = "{tmp_path / "e"}" }}',
        '{ name = "moved-ext", git = "https://h/m.git?rev=0000000000000000000000000000000000000000" }',
    )
    lines, out = carry.collect(capsys)
    assert lines == [
        "pip",
        "lib-git @ git+https://h/r.git@abc#subdirectory=s",
        f"lib-dir @ {(tmp_path / 'd').as_uri()}",
        f"lib-file @ {(tmp_path / 'f.whl').as_uri()}",
        f"-e {(tmp_path / 'e').as_uri()}",
        "lib-url[x] @ https://h/u.whl",
        "lib-tag @ git+https://h/t.git@v1",
        "lib-branch @ git+https://h/b.git@main",
        "lib-marker>=1 ; python_full_version >= '3.11'",
        "lib-extra[a,b]==2",
        f"moved-ext @ git+https://h/m.git@{_SHA}",
    ]
    assert (
        "not carried: odd - uv recorded it in a form this installer cannot read back, so "
        "there is no command to reinstall it from here"
    ) in out
    assert (
        "not carried: removed-since - the uv receipt names it, but it is not installed now, "
        "so there is nothing to reinstall"
    ) in out
    for name in ("lib-lfs", "lib-edit-extra", "lib-cr"):
        assert f"not carried: {name} - uv recorded it in a form this installer cannot" in out
    # One line to print per requirement, in the same order: the shell prints line N
    # when requirement N is not carried.
    py = carry.env_dir / "bin" / "python"
    restore = carry.restore()
    assert len(restore) == len(lines)
    assert restore[0] == (
        f"aelix-install: not carried: pip - reinstall it with: uv pip install --python '{py}' 'pip'"
    )
    assert restore[4] == (
        f"aelix-install: not carried: -e {(tmp_path / 'e').as_uri()} - reinstall it with: "
        f"uv pip install --python '{py}' -e '{(tmp_path / 'e').as_uri()}'"
    )
    assert restore[-1].startswith(
        "aelix-install: not carried: moved-ext 1.0.0 - reinstall it with:"
    )


def test_check_takes_out_an_extension_whose_files_differ(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _dist(carry.site, "pip", "25.0", extension=False)
    carry.receipt('{ name = "pip" }')
    _dist(carry.site, "same-ext", "1.0.0", installer="pip")
    _dist(carry.site, "acme-tools", "1.2.0")
    _dist(carry.site, "lost-ext", "2.0.0")
    carry.collect(capsys)
    # The new environment: same-ext identical, acme-tools answered with other bytes
    # (an index's same-name, same-version impostor), lost-ext not installed at all.
    new = tmp_path / "new" / "lib" / "python3" / "site-packages"
    new.mkdir(parents=True)
    _dist(new, "pip", "25.0", extension=False)
    _dist(new, "same-ext", "1.0.0")
    _dist(new, "acme-tools", "1.2.0", body=b"import os  # not the reviewed code\n")
    carry.ns["importlib"].metadata.distributions = lambda: importlib.metadata.Distribution.discover(
        path=[str(new)]
    )
    assert carry.check() == 3
    out = capsys.readouterr().out
    assert "not carried: acme-tools 1.2.0 - its files differ in the new environment" in out
    assert (
        "reinstall it with: aelix extension install 'acme-tools==1.2.0' (from PyPI or an index "
        "`aelix extension source add` registered; add --index-url <index> for another)"
    ) in out
    assert "not carried: lost-ext 2.0.0 - it is missing from the new environment" in out
    assert (carry.state / "kept.txt").read_text(encoding="utf-8").splitlines() == [
        "pip",
        "same-ext==1.0.0",
    ]
    assert (carry.state / "extensions.txt").read_text(encoding="utf-8").splitlines() == [
        "same-ext 1.0.0"
    ]


def test_check_keeps_everything_when_the_files_match(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "same-ext", "1.0.0")
    carry.collect(capsys)
    assert carry.check() == 0
    assert (carry.state / "kept.txt").read_text(encoding="utf-8").splitlines() == [
        "same-ext==1.0.0"
    ]


def test_check_takes_out_a_wheel_whose_record_claims_the_old_bytes(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """uv installs a wheel whose RECORD lies about a file and keeps that RECORD as it
    came (uv 0.11.19 and 0.13.10, measured), so comparing the two RECORDs passes an
    impostor that copies the original RECORD. ``check`` hashes the bytes on disk."""

    _dist(carry.site, "acme-tools", "1.2.0")
    carry.collect(capsys)
    new = tmp_path / "new" / "lib" / "python3" / "site-packages"
    new.mkdir(parents=True)
    _dist(
        new,
        "acme-tools",
        "1.2.0",
        body=b"IMPOSTOR = 1\n",
        claimed=b"def setup(api=None):\n    return None\n",
    )
    carry.ns["importlib"].metadata.distributions = lambda: importlib.metadata.Distribution.discover(
        path=[str(new)]
    )
    assert carry.check() == 3
    assert "not carried: acme-tools 1.2.0 - its files differ" in capsys.readouterr().out
    assert (carry.state / "kept.txt").read_text(encoding="utf-8") == ""


class _UnfilteredDist(importlib.metadata.PathDistribution):
    """``files`` as Python 3.11 gives it: every RECORD row, on disk or not. 3.12+
    drops the rows whose file is not on disk (``skip_missing_files``)."""

    @property
    def files(self) -> list[importlib.metadata.PackagePath]:  # type: ignore[override]
        out = []
        for row in csv.reader((self.read_text("RECORD") or "").splitlines()):
            item = importlib.metadata.PackagePath(row[0])
            item.hash = importlib.metadata.FileHash(row[1]) if row[1] else None
            item.size = int(row[2]) if row[2] else None
            item.dist = self
            out.append(item)
        return out


class _FilteredDist(_UnfilteredDist):
    @property
    def files(self) -> list[importlib.metadata.PackagePath]:  # type: ignore[override]
        return [f for f in super().files if f.locate().exists()]


def test_a_recorded_file_not_on_disk_counts_on_neither_side(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """The old environment on 3.11 lists ``gone.py`` (named in RECORD, not on disk) and
    the new one on 3.12+ does not: the extension is still the same."""

    _dist(carry.site, "acme-tools", "1.2.0")
    info = carry.site / "acme_tools-1.2.0.dist-info"
    carry.ns["importlib"].metadata.distributions = lambda: [_UnfilteredDist(info)]
    carry.collect(capsys)
    carry.ns["importlib"].metadata.distributions = lambda: [_FilteredDist(info)]
    assert carry.check() == 0
    assert (carry.state / "extensions.txt").read_text(encoding="utf-8") == "acme-tools 1.2.0\n"


def test_an_extension_with_no_record_is_named_and_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """Carried with nothing to compare, any bytes the new install brought would pass."""

    _dist(carry.site, "bare-ext", "1.0.0", record=False)
    _dist(carry.site, "acme-tools", "1.2.0")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert (
        "not carried: bare-ext 1.0.0 - it records no installed files to compare; reinstall it "
        "with: aelix extension install 'bare-ext==1.0.0'"
    ) in out


@pytest.mark.parametrize(
    "pins",
    [
        '{"pins": ["bad"]}',
        '["not", "a", "store"]',
        '{"pins": {"/x/a.whl": {"kind": "path", "sha256": 7}}}',
        "{not json",
        "",
    ],
)
def test_a_pins_file_in_another_shape_drops_nothing(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path, pins: str
) -> None:
    """Codex r1 L1: ``{"pins": ["bad"]}`` raised AttributeError out of ``collect``, and
    the installer then carried nothing and named nothing."""

    wheel = tmp_path / "named_ext-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    _dist(carry.site, "named-ext", "1.0", direct_url={"url": wheel.as_uri(), "archive_info": {}})
    _dist(carry.site, "acme-tools", "1.2.0")
    (carry.agent / "extension_pins.json").write_text(pins, encoding="utf-8")
    lines, _ = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0", f"named-ext @ {wheel.as_uri()}"]


@pytest.mark.parametrize(
    "direct_url",
    [
        ["a", "list"],
        {"url": "https://h/r.git", "vcs_info": "git"},
        {"url": Path(Path.cwd().anchor).as_uri(), "dir_info": "yes"},
        {"no": "url"},
    ],
)
def test_a_direct_url_in_another_shape_names_that_one_extension(
    carry: _Carry, capsys: pytest.CaptureFixture[str], direct_url: Any
) -> None:
    _dist(carry.site, "odd-ext", "1.0.0", direct_url=direct_url)
    _dist(carry.site, "acme-tools", "1.2.0")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "not carried: odd-ext 1.0.0 - this installer could not read it (" in out
    assert "), so there is no command to reinstall it from here" in out


def test_unreadable_metadata_names_that_one_extension(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "bad-entry", "1.0.0")
    (carry.site / "bad_entry-1.0.0.dist-info" / "direct_url.json").write_bytes(b"\xff\xfe\x00")
    _dist(carry.site, "acme-tools", "1.2.0")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "not carried: bad-entry 1.0.0 - this installer could not read it (" in out


def test_a_record_importlib_cannot_parse_compares_nothing(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "torn-ext", "1.0.0")
    (carry.site / "torn_ext-1.0.0.dist-info" / "RECORD").write_text(
        "torn_ext/__init__.py,sha256=x,1,a fourth field\n", encoding="utf-8"
    )
    lines, out = carry.collect(capsys)
    assert lines == []
    assert "not carried: torn-ext 1.0.0 - it records no installed files to compare" in out


@pytest.mark.parametrize(
    "receipt",
    [
        '[tool]\npython = "3.12"\n',
        '[tool]\nrequirements = "pip"\n',
        "not = [toml\n",
        'tool = "x"\n',
    ],
)
def test_a_receipt_in_another_shape_still_carries_the_extensions(
    carry: _Carry, capsys: pytest.CaptureFixture[str], receipt: str
) -> None:
    _dist(carry.site, "acme-tools", "1.2.0")
    (carry.env_dir / "uv-receipt.toml").write_text(receipt, encoding="utf-8")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "could not read the uv receipt of the current aelix (" in out


def test_a_receipt_entry_in_another_shape_is_named_and_the_rest_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "pip", "25.0", extension=False)
    _dist(carry.site, "lib-ints", extension=False)
    _dist(carry.site, "acme-tools", "1.2.0")
    carry.receipt(
        '"just a string"',
        "{ name = 7 }",
        '{ name = "lib-ints", extras = [1, 2] }',
        '{ name = "pip" }',
        '{ name = "two\\nlines" }',
    )
    lines, out = carry.collect(capsys)
    assert lines == ["pip", "acme-tools==1.2.0"]
    # A name with a line break is not printed: it would paint a line of its own.
    assert out.count("not carried: a uv receipt entry this installer cannot read") == 3
    assert "two\nlines" not in out
    assert "not carried: lib-ints - uv recorded it in a form this installer cannot read back" in out


def test_a_source_with_a_line_break_is_named_and_not_carried(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """A line break would split one requirement into two in the requirements file."""

    _dist(
        carry.site,
        "nl-ext",
        direct_url={"url": "https://h/r\n.git", "vcs_info": {"vcs": "git", "commit_id": _SHA}},
    )
    _dist(carry.site, "acme-tools", "1.2.0")
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "not carried: nl-ext 1.0.0 - its source has a control character" in out


def test_a_file_url_on_another_host_is_carried_as_the_url(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "file://fileserver/share/unc_ext-1.0-py3-none-any.whl"
    _dist(carry.site, "unc-ext", "1.0", direct_url={"url": url, "archive_info": {}})
    lines, _ = carry.collect(capsys)
    assert lines == [f"unc-ext @ {url}"]


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX quoting, through sh")
def test_a_printed_command_survives_a_quote_in_the_path(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    src = tmp_path / "it's ext"
    src.mkdir()
    _dist(carry.site, "q-ext", direct_url={"url": src.as_uri(), "dir_info": {}})
    carry.collect(capsys)
    cmd = carry.restore()[0].split("reinstall it with: ", 1)[1]
    shown = subprocess.run(
        ["sh", "-c", "printf '%s\\n' " + cmd.removeprefix("aelix extension install ")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    assert shown == f"{src}\n"


def test_the_windows_quoting_doubles_a_quote(carry: _Carry) -> None:
    carry.ns["WINDOWS"] = True
    assert carry.ns["quote"]("C:\\it's ext") == "'C:\\it''s ext'"


def test_an_entry_points_file_importlib_cannot_parse_is_still_an_extension(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """importlib raises TypeError on a line with no '=' (measured, 3.12); the text
    still names the group, so it is carried like any other, not dropped unnamed."""

    _dist(carry.site, "torn-eps", "1.0.0")
    (carry.site / "torn_eps-1.0.0.dist-info" / "entry_points.txt").write_text(
        "[aelix.extensions]\nno equals sign here\n", encoding="utf-8"
    )
    lines, _ = carry.collect(capsys)
    assert lines == ["torn-eps==1.0.0"]


def test_a_walk_that_breaks_keeps_what_it_found(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "acme-tools", "1.2.0")
    found = list(importlib.metadata.Distribution.discover(path=[str(carry.site)]))

    def walk() -> Any:
        yield from found
        raise OSError("a directory went away")

    carry.ns["importlib"].metadata.distributions = walk
    lines, out = carry.collect(capsys)
    assert lines == ["acme-tools==1.2.0"]
    assert "could not read every distribution of the environment (OSError)" in out


def test_main_names_a_failure_it_cannot_carry_past(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = carry.ns["main"](["carry.py", "collect", str(carry.env_dir), str(tmp_path / "no-state")])
    assert code == 1
    assert "aelix-install: carry.py collect stopped: FileNotFoundError" in capsys.readouterr().out


# === fix round 2: dist-info, unreadable METADATA, receipt extras, messages ===


def _new_site(carry: _Carry, tmp_path: Path) -> Path:
    new = tmp_path / "new" / "lib" / "python3" / "site-packages"
    new.mkdir(parents=True)
    carry.ns["importlib"].metadata.distributions = lambda: importlib.metadata.Distribution.discover(
        path=[str(new)]
    )
    return new


def test_check_takes_out_a_wheel_that_keeps_the_files_and_drops_its_entry_points(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Codex r2 M3: same name, same version, the same module bytes and no
    entry_points.txt: all three shells kept it and printed "every carried extension
    binds" with 0 endpoints."""

    _dist(carry.site, "keep-ext", "1")
    carry.collect(capsys)
    new = _new_site(carry, tmp_path)
    _dist(new, "keep-ext", "1", extension=False)
    assert carry.check() == 3
    out = capsys.readouterr().out
    assert (
        "not carried: keep-ext 1 - its entry points or what it depends on (Requires-Dist, "
        "Provides-Extra) differ in the new environment, so it is taken out; reinstall it with: "
        "aelix extension install 'keep-ext==1'"
    ) in out
    assert (carry.state / "extensions.txt").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "header",
    ["Requires-Dist: evil-926\n", "Provides-Extra: more\n"],
)
def test_check_takes_out_a_wheel_whose_metadata_pulls_in_more(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path, header: str
) -> None:
    """Verify r2 N2: the impostor kept the original's files and RECORD and added a
    Requires-Dist to METADATA; evil-926 was installed beside it."""

    _dist(carry.site, "idxext-926", "0.1.0")
    carry.collect(capsys)
    new = _new_site(carry, tmp_path)
    _dist(new, "idxext-926", "0.1.0")
    meta = new / "idxext_926-0.1.0.dist-info" / "METADATA"
    meta.write_text(meta.read_text(encoding="utf-8") + header, encoding="utf-8")
    assert carry.check() == 3
    assert "not carried: idxext-926 0.1.0 - its entry points or what it depends on" in (
        capsys.readouterr().out
    )


def test_check_keeps_one_whose_metadata_differs_only_in_spelling(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A git or directory extension is rebuilt by its build backend, and backends spell
    one requirement in more than one way."""

    old_meta = 'Requires-Dist: Foo_Bar (<2,>=1)\nRequires-Dist: baz ; python_version < "3.12"\n'
    new_meta = "Requires-Dist: foo-bar>=1,<2\nRequires-Dist: baz; python_version<'3.12'\n"
    _dist(carry.site, "spell-ext", "1.0.0")
    meta = carry.site / "spell_ext-1.0.0.dist-info" / "METADATA"
    meta.write_text(meta.read_text(encoding="utf-8") + old_meta, encoding="utf-8")
    carry.collect(capsys)
    new = _new_site(carry, tmp_path)
    _dist(new, "spell-ext", "1.0.0")
    meta = new / "spell_ext-1.0.0.dist-info" / "METADATA"
    meta.write_text(meta.read_text(encoding="utf-8") + new_meta, encoding="utf-8")
    assert carry.check() == 0, capsys.readouterr().out
    assert (carry.state / "extensions.txt").read_text(encoding="utf-8") == "spell-ext 1.0.0\n"


@pytest.mark.parametrize("shape", ["missing", "empty"])
def test_an_extension_whose_metadata_cannot_be_read_is_named_from_its_directory(
    carry: _Carry, capsys: pytest.CaptureFixture[str], shape: str
) -> None:
    """Codex r2 M4: readable entry_points.txt, unreadable METADATA: collect exited 0
    and the reinstall removed it, named nowhere."""

    _dist(carry.site, "unread-ext", "1.0.0")
    meta = carry.site / "unread_ext-1.0.0.dist-info" / "METADATA"
    if shape == "missing":
        meta.unlink()
    else:
        meta.write_text("", encoding="utf-8")
    _dist(carry.site, "acme-tools", "1.2.0")
    _dist(carry.site, "aelix-coding-agent", "0.1.0")  # aelix's own: never named
    (carry.site / "aelix_coding_agent-0.1.0.dist-info" / "METADATA").unlink()
    carry.receipt('{ name = "unread-ext" }')  # in the receipt too: named once, not as gone
    import warnings

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        lines, out = carry.collect(capsys)
    # metadata["Name"] on a missing METADATA warns on 3.12+, and the installer runs
    # carry.py as __main__, where that warning prints.
    assert [w for w in seen if issubclass(w.category, DeprecationWarning)] == []
    assert lines == ["acme-tools==1.2.0"]
    assert (
        "not carried: unread_ext 1.0.0 - its METADATA cannot be read, so this installer knows "
        "it only by its directory (unread_ext-1.0.0.dist-info) and its entry points "
        "(unread-ext), and there is no command to reinstall it from here"
    ) in out
    assert out.count("not carried:") == 1 and "not installed now" not in out


def test_an_extension_the_receipt_asks_for_with_extras_keeps_them(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Codex r2 S1: `Keep_Ext[feature]>=1,<2` with an active marker was carried as
    `keep-ext==1`; the extra and its dependency were dropped and the update said so
    nowhere."""

    _dist(carry.site, "keep-ext", "1")
    vcs = {"vcs": "git", "commit_id": _SHA}
    _dist(carry.site, "git-ext", direct_url={"url": "https://h/g.git", "vcs_info": vcs})
    src = tmp_path / "edit"
    src.mkdir()
    _dist(carry.site, "edit-ext", direct_url={"url": src.as_uri(), "dir_info": {"editable": True}})
    _dist(carry.site, "odd-ext")
    carry.receipt(
        '{ name = "Keep_Ext", extras = ["feature"], specifier = ">=1,<2", '
        "marker = \"python_full_version >= '3.11'\" }",
        '{ name = "git-ext", extras = ["a", "b"], git = "https://h/g.git?rev=old" }',
        '{ name = "edit-ext", extras = ["dev"], editable = "/old/place" }',
        '{ name = "odd-ext", extras = [1] }',
    )
    lines, out = carry.collect(capsys)
    assert lines == [
        f"edit-ext @ {src.as_uri()}".replace("edit-ext @ ", "-e "),
        f"git-ext[a,b] @ git+https://h/g.git@{_SHA}",
        "keep-ext[feature]==1 ; python_full_version >= '3.11'",
        "odd-ext==1.0.0",
    ]
    assert "(an editable line cannot carry the receipt's extras dev; carried without them)" in out
    assert (
        "carrying odd-ext 1.0.0 as odd-ext==1.0.0 (its uv receipt asks for extras or a marker "
        "this installer cannot read back; carried without them)"
    ) in out


def test_the_command_for_one_left_out_keeps_the_receipt_s_extras(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Verify r3 F4: keepext-926 was carried as `keepext-926[feature]==0.1.0`, and when
    uv could not install it the command it was given, `aelix extension install
    'keepext-926==0.1.0'`, installed it without featdep-926, its extra's dependency.
    The check-time "taken out" line gave the same command."""

    vcs = {"vcs": "git", "commit_id": _SHA}
    src = tmp_path / "src"
    src.mkdir()
    wheel = tmp_path / "file_ext-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    original = tmp_path / "orig" / "staged_ext-1.0.0-py3-none-any.whl"
    original.parent.mkdir()
    original.write_bytes(b"approved bytes")
    pins = {
        str(original): {
            "kind": "path",
            "sha256": hashlib.sha256(b"approved bytes").hexdigest(),
        }
    }
    (carry.agent / "extension_pins.json").write_text(
        json.dumps({"version": 1, "pins": pins}), encoding="utf-8"
    )
    staged = (tmp_path / "aelix-verify-x" / original.name).as_uri()
    shapes = {
        "keep-ext": None,
        "git-ext": {"url": "https://h/g.git", "vcs_info": vcs},
        "dir-ext": {"url": src.as_uri(), "dir_info": {}},
        "file-ext": {"url": wheel.as_uri(), "archive_info": {}},
        "staged-ext": {"url": staged, "archive_info": {}},
        "url-ext": {"url": "https://e.invalid/url_ext-1.0.whl", "archive_info": {}},
    }
    for name, direct_url in shapes.items():
        _dist(carry.site, name, direct_url=direct_url)
    carry.receipt(*(f'{{ name = "{name}", extras = ["feature"] }}' for name in shapes))
    lines, _ = carry.collect(capsys)
    assert all("[feature]" in line for line in lines), lines
    py = carry.env_dir / "bin" / "python"
    expected = {
        "dir-ext": f"aelix extension install '{src}[feature]'",
        "file-ext": f"aelix extension install '{wheel}[feature]'",
        "git-ext": f"aelix extension install 'git-ext[feature] @ git+https://h/g.git@{_SHA}'",
        "keep-ext": "aelix extension install 'keep-ext[feature]==1.0.0'" + carry.ns["INDEX_NOTE"],
        "staged-ext": f"aelix extension install '{original}[feature]'",
        "url-ext": (
            f"uv pip install --python '{py}' 'url-ext[feature] @ https://e.invalid/url_ext-1.0.whl'"
        ),
    }
    assert carry.restore() == [
        f"aelix-install: not carried: {name} 1.0.0 - reinstall it with: {how}"
        for name, how in sorted(expected.items())
    ]
    failed = (carry.state / "failed.txt").read_text(encoding="utf-8")
    assert f"reinstall it with: {expected['keep-ext']}\n" in failed
    new = _new_site(carry, tmp_path)
    _dist(new, "keep-ext", body=b"other bytes")
    taken = carry.state / "taken.txt"
    taken.write_text("keep-ext[feature]==1.0.0\n", encoding="utf-8")
    assert carry.check(taken) == 3
    assert f"taken out; reinstall it with: {expected['keep-ext']}\n" in capsys.readouterr().out


def test_an_empty_extras_list_adds_nothing_to_the_command(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    _dist(carry.site, "plain-ext", "1")
    carry.receipt('{ name = "plain-ext", extras = [] }')
    lines, _ = carry.collect(capsys)
    assert lines == ["plain-ext==1"]
    assert carry.restore() == [
        "aelix-install: not carried: plain-ext 1 - reinstall it with: aelix extension install "
        "'plain-ext==1'" + carry.ns["INDEX_NOTE"]
    ]


def test_a_receipt_marker_with_no_extras_stays_on_the_carried_line(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r3 F5 C20: with the marker check dropped, a marker alone was lost."""

    _dist(carry.site, "mark-ext", "1")
    carry.receipt('{ name = "mark-ext", marker = "sys_platform != \'emscripten\'" }')
    lines, _ = carry.collect(capsys)
    assert lines == ["mark-ext==1 ; sys_platform != 'emscripten'"]


def test_check_keeps_one_whose_entry_point_differs_only_in_spaces(
    carry: _Carry, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Verify r3 F5 C4: build backends write `mod:setup [extra]` and `mod:setup[extra]`;
    one rebuilt by another backend is the same extension."""

    _dist(carry.site, "space-ext", "1.0.0")
    eps = carry.site / "space_ext-1.0.0.dist-info" / "entry_points.txt"
    eps.write_text("[aelix.extensions]\nspace-ext = space_ext:setup [feature]\n", encoding="utf-8")
    carry.collect(capsys)
    new = _new_site(carry, tmp_path)
    _dist(new, "space-ext", "1.0.0")
    eps = new / "space_ext-1.0.0.dist-info" / "entry_points.txt"
    eps.write_text("[aelix.extensions]\nspace-ext = space_ext:setup[feature]\n", encoding="utf-8")
    assert carry.check() == 0, capsys.readouterr().out
    assert (carry.state / "extensions.txt").read_text(encoding="utf-8") == "space-ext 1.0.0\n"


def test_a_failed_line_says_why_and_gives_a_command_only_where_one_can_work(
    carry: _Carry, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r2 N1: an extension that failed on its own in the one-at-a-time pass was
    printed with collect's command, which failed the same way, and no reason."""

    _dist(carry.site, "pip", "25.0", extension=False)
    carry.receipt('{ name = "pip" }')
    _dist(carry.site, "acme-tools", "1.2.0")
    vcs = {"vcs": "git", "commit_id": _SHA}
    _dist(carry.site, "git-ext", direct_url={"url": "https://h/g.git", "vcs_info": vcs})
    lines, _ = carry.collect(capsys)
    failed = (carry.state / "failed.txt").read_text(encoding="utf-8").splitlines()
    assert len(failed) == len(lines) == 3
    why = " - uv could not install it with aelix @VERSION@ (its message is above)"
    none = ", so there is no command that reinstalls it here"
    assert failed == [
        f"aelix-install: not carried: pip{why}{none}",
        f"aelix-install: not carried: acme-tools 1.2.0{why}; from an index this installer does "
        "not use, reinstall it with: aelix extension install 'acme-tools==1.2.0' (from PyPI or an "
        "index `aelix extension source add` registered; add --index-url <index> for another)",
        f"aelix-install: not carried: git-ext 1.0.0{why}{none}",
    ]


def test_a_name_the_console_cannot_encode_prints_escaped(
    carry: _Carry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r2 N3 C23: a cp1252 console (Windows' default for a pipe) and a name
    outside it. Without the reconfigure, the first `say` raised UnicodeEncodeError and
    main's own report of it raised again."""

    import io

    _dist(carry.site, "caf\u00e9-\u0444-ext", "1.0.0")
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
    monkeypatch.setattr(sys, "stdout", console)
    code = carry.ns["main"](["carry.py", "collect", str(carry.env_dir), str(carry.state)])
    console.flush()
    assert code == 0
    assert "carrying caf\u00e9-\\u0444-ext 1.0.0 as" in raw.getvalue().decode("cp1252")


# === Step 5's lines of both installers, executed with stubs ================
#
# install.sh's lines run under sh; install.ps1's under pwsh where it is installed
# (CI's ubuntu image carries it; its windows legs run the real script in the e2e
# jobs instead, since these stubs are shell scripts). Each scenario is the same
# for both: a stub uv whose `tool install` exits with the next code it is given and
# records the requirements file it got, and a stub current environment.


def _step5_sh() -> str:
    text = _SH.read_text(encoding="utf-8")
    return text[text.index("install_aelix() {") : text.index("# ── Step 6")]


def _step5_ps1() -> str:
    text = _PS1.read_text(encoding="utf-8").replace("\r\n", "\n")
    return text[text.index("    function Install-Aelix {") : text.index("    # -- Step 6")]


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _printf(lines: list[str]) -> str:
    return "".join(x.replace("%", "%%") + "\\n" for x in lines)


class _Step5:
    """Step 5 with a stub uv and a stub current environment."""

    def __init__(self, root: Path, shell: str, *, existing: bool = True) -> None:
        self.root = root
        self.shell = shell
        self.tmp = root / "tmp"
        self.tmp.mkdir(parents=True)
        self.calls = root / "uv-calls"
        self.codes = root / "codes"
        self.took = root / "check-took"
        tools = root / "tools"
        _exe(
            root / "stubs" / "uv",
            f'case "$1 $2" in\n'
            f'  "tool dir") echo "{tools}" ;;\n'
            f'  "tool install") shift 2; line="$*"; prev=""\n'
            f'    for a in "$@"; do\n'
            f'      if [ "$prev" = "--with-requirements" ]; then line="$line | $(tr "\\n" ";" < "$a")"; fi\n'
            f'      prev="$a"\n'
            f"    done\n"
            f'    echo "$line" >> "{self.calls}"\n'
            f'    code=$(head -n 1 "{self.codes}"); sed 1d "{self.codes}" > "{self.codes}.n"; mv "{self.codes}.n" "{self.codes}"\n'
            f'    if [ "${{code:-0}}" != 0 ] && [ -f "{root / "uv-says"}" ]; then cat "{root / "uv-says"}" >&2; fi\n'
            f'    exit "${{code:-0}}" ;;\n'
            f"  *) exit 2 ;;\n"
            f"esac\n",
        )
        self.env = tools / "aelix"
        if existing:
            self.env.mkdir(parents=True)
            (self.env / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
        self.python = "ok"  # or "broken" (exits 1) or "missing"
        self.collect_code = 0
        self.check_code = 0
        self.verify_code = 0
        self.restore_help_code = 0
        self.with_lines = ["acme-tools==1.2.0"]
        self.kept_lines: list[str] | None = None  # None: every requirement check was given
        self.dist_infos: dict[str, bool] = {}  # dist-info stem -> has [aelix.extensions]
        self.metadata: dict[str, str] = {}  # dist-info stem -> its METADATA text

    def run(
        self, *codes: int, merged: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        self.codes.write_text("".join(f"{c}\n" for c in codes), encoding="utf-8")
        restore = [
            f"aelix-install: not carried: {x} - reinstall it with: X" for x in self.with_lines
        ]
        failed = [
            f"aelix-install: not carried: {x} - uv could not install it with aelix @VERSION@"
            for x in self.with_lines
        ]
        kept = (
            'cp "$5" "$state/kept.txt"'
            if self.kept_lines is None
            else f'printf "{_printf(self.kept_lines)}" > "$state/kept.txt"'
        )
        # Python's own answer to `-c` with no program after it: exit 2. Windows
        # PowerShell 5.1 and pwsh's Legacy argument passing drop an empty argument, so
        # `-I -c ''` reached python as `-I -c` (Codex review round 2 M2).
        python = (
            f'if [ "$2" = "-c" ]; then\n'
            f'  if [ "$#" -lt 3 ]; then echo "Argument expected for the -c option" >&2; exit 2; fi\n'
            f"  exit {0 if self.python == 'ok' else 1}\n"
            f"fi\n"
            f'case "$3" in\n'
            f'  collect) state="$5"; printf "{_printf(self.with_lines)}" > "$state/with.txt"\n'
            f'    printf "{_printf(restore)}" > "$state/restore.txt"\n'
            f'    printf "{_printf(failed)}" > "$state/failed.txt"\n'
            f"    exit {self.collect_code} ;;\n"
            f'  check) state="$4"; cp "$5" "{self.took}"; {kept}\n'
            f'    cp "$state/kept.txt" "$state/extensions.txt"; exit {self.check_code} ;;\n'
            f"esac\nexit 9\n"
        )
        aelix = (
            f'if [ "$2" = "restore" ]; then exit {self.restore_help_code}; fi\n'
            f'echo "verify ran: $*"\nexit {self.verify_code}\n'
        )
        for scripts, py, launcher, site in (
            ("bin", "python", "aelix", "lib/python3.12/site-packages"),
            ("Scripts", "python.exe", "aelix.exe", "Lib/site-packages"),
        ):
            if self.python != "missing":
                _exe(self.env / scripts / py, python)
            _exe(self.env / scripts / launcher, aelix)
            for stem, extension in self.dist_infos.items():
                info = self.env / site / f"{stem}.dist-info"
                info.mkdir(parents=True, exist_ok=True)
                group = "aelix.extensions" if extension else "console_scripts"
                (info / "entry_points.txt").write_text(f"[{group}]\nx = y:z\n", encoding="utf-8")
                if stem in self.metadata:
                    (info / "METADATA").write_text(self.metadata[stem], encoding="utf-8")
        env = child_env(
            self.root, PATH=f"{self.root / 'stubs'}{os.pathsep}/usr/bin{os.pathsep}/bin"
        )
        if self.shell == "sh":
            script = (
                'log() { printf "%s\\n" "aelix-install: $*"; }\n'
                'die() { printf "%s\\n" "aelix-install: ERROR: $*"; exit 1; }\n'
                f'tmp="{self.tmp}"; target="aelix[tui]==0.1.1"; AELIX_PYTHON=">=3.11,<3.15"; version=0.1.1\n'
                "set -eu\n" + _step5_sh()
            )
            argv = ["sh", "-c", script]
        else:
            # ps1-legacy: Windows PowerShell 5.1's way of passing native arguments, which
            # drops an empty one (Codex r2 M2). ps1-native-stop: pwsh 7.3+'s preference
            # that makes a non-zero native exit raise under Stop (Codex r2 S2).
            mode = {
                "ps1": "",
                "ps1-legacy": "$PSNativeCommandArgumentPassing = 'Legacy'\n",
                "ps1-native-stop": "$PSNativeCommandUseErrorActionPreference = $true\n",
            }[self.shell]
            path = self.root / "step5.ps1"
            path.write_text(
                "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n"
                + mode
                + 'function Write-Log { param([string]$Message) Write-Host "aelix-install: $Message" }\n'
                'function Stop-WithError { param([string]$Message) Write-Host "aelix-install: ERROR: $Message"; exit 1 }\n'
                f"$tmp = '{self.tmp}'; $target = 'aelix[tui]==0.1.1'; $AelixPython = '>=3.11,<3.15'; $version = '0.1.1'\n"
                + _step5_ps1(),
                encoding="utf-8",
            )
            argv = [str(shutil.which("pwsh")), "-NoProfile", "-NonInteractive", "-File", str(path)]
        result = subprocess.run(
            argv,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merged else subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        calls = self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []
        return result, calls

    def call(self, *requirements: str, file: str = "in.txt") -> str:
        base = f"--force --find-links {self.tmp} --python >=3.11,<3.15 aelix[tui]==0.1.1"
        if not requirements:
            return base
        return f"{base} --with-requirements {self.tmp}/carry/{file} | {''.join(r + ';' for r in requirements)}"

    def checked(self) -> list[str]:
        return self.took.read_text(encoding="utf-8").splitlines() if self.took.exists() else []


_NO_PWSH = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("pwsh") is None or shutil.which("sh") is None,
    reason="the stubs are shell scripts; pwsh runs install.ps1's lines where it is installed",
)
_SHELLS = [
    pytest.param(
        "sh",
        marks=pytest.mark.skipif(
            sys.platform == "win32" or shutil.which("sh") is None, reason="install.sh is POSIX sh"
        ),
    ),
    pytest.param("ps1", marks=_NO_PWSH),
    pytest.param("ps1-legacy", marks=_NO_PWSH),
    pytest.param("ps1-native-stop", marks=_NO_PWSH),
]
_A, _B, _C = "a-ext==1", "b-ext==1", "c-ext==1"


def _not_carried(req: str) -> str:
    return f"aelix-install: not carried: {req} - reinstall it with: X"


def _failed(req: str) -> str:
    """The line for one uv could not install with the release (verify r2 N1)."""

    return f"aelix-install: not carried: {req} - uv could not install it with aelix 0.1.1"


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_with_no_current_environment_installs_as_before(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell, existing=False)
    result, calls = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [box.call()]
    assert "carried over" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_dies_when_aelix_itself_cannot_install_with_nothing_to_carry(
    tmp_path: Path, shell: str
) -> None:
    box = _Step5(tmp_path, shell, existing=False)
    result, calls = box.run(1)
    assert result.returncode == 1
    assert "ERROR: uv tool install failed for 'aelix[tui]==0.1.1'" in result.stdout
    assert calls == [box.call()]


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_carries_and_asks_the_new_aelix_for_its_verdict(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    result, calls = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [box.call("acme-tools==1.2.0")]
    assert box.checked() == ["acme-tools==1.2.0"]
    assert "carried over: acme-tools==1.2.0. Checking them with the new aelix:" in result.stdout
    assert "verify ran: extension verify" in result.stdout
    assert "every carried extension binds on aelix 0.1.1." in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_reports_a_verdict_that_does_not_bind_and_keeps_the_update(
    tmp_path: Path, shell: str
) -> None:
    box = _Step5(tmp_path, shell)
    box.verify_code = 1
    result, _ = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not bind on aelix 0.1.1. The update is kept" in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_names_the_one_requirement_whose_install_failed(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    result, calls = box.run(1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [box.call("acme-tools==1.2.0"), box.call()]
    assert _failed("acme-tools==1.2.0") in result.stdout.splitlines()
    assert _not_carried("acme-tools==1.2.0") not in result.stdout
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize("carried", [1, 2])
def test_step5_starts_the_failed_line_on_a_line_of_its_own(
    tmp_path: Path, shell: str, carried: int
) -> None:
    """Verify r3 F6: uv's build-failure hint ends with no newline, and the line naming
    the extension it could not install went on at the end of it; so did the line that
    says the reinstall with the carried requirements failed."""

    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B][:carried]
    (tmp_path / "uv-says").write_text("re-run with `--no-build-isolation`.", encoding="utf-8")
    result, _ = box.run(1, 0, *([1] * carried), 0, merged=True)
    assert result.returncode == 0, result.stdout
    lines = result.stdout.splitlines()
    assert _failed(_A) in lines, result.stdout
    glued = [ln for ln in lines if "aelix-install:" in ln and not ln.startswith("aelix-install:")]
    assert glued == [], result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_starts_the_line_after_a_failed_kept_reinstall_on_its_own(
    tmp_path: Path, shell: str
) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    box.check_code = 3
    box.kept_lines = [_A]
    (tmp_path / "uv-says").write_text("re-run with `--no-build-isolation`.", encoding="utf-8")
    result, _ = box.run(0, 1, 0, merged=True)
    assert result.returncode == 0, result.stdout
    lines = result.stdout.splitlines()
    assert "aelix-install: that reinstall failed; installing aelix with nothing carried over." in (
        lines
    ), result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_keeps_the_others_when_one_cannot_be_installed(tmp_path: Path, shell: str) -> None:
    """F5: a git, a wheel and an index extension; the index one does not resolve. The
    other two go back in, one at a time, and only the failing one is named."""

    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B, _C]
    result, calls = box.run(1, 0, 0, 1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        box.call(_A, _B, _C),
        box.call(),
        box.call(_A, file="try.txt"),
        box.call(_A, _B, file="try.txt"),
        box.call(_A, _C, file="try.txt"),
    ]
    assert box.checked() == [_A, _C]
    assert _failed(_B) in result.stdout.splitlines()
    assert "not carried: " + _A not in result.stdout and "not carried: " + _C not in result.stdout
    assert _not_carried(_B) not in result.stdout
    assert f"carried over: {_A}, {_C}. Checking them" in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_installs_what_went_in_again_when_the_last_try_failed(
    tmp_path: Path, shell: str
) -> None:
    """A failed build removes the environment (uv 0.11.19 and 0.13.10, measured)."""

    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B, _C]
    result, calls = box.run(1, 0, 0, 0, 1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-1] == box.call(_A, _B)
    assert len(calls) == 6
    assert box.checked() == [_A, _B]
    assert _failed(_C) in result.stdout.splitlines()


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_installs_bare_when_none_goes_back_in(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    result, calls = box.run(1, 0, 1, 1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [
        box.call(_A, _B),
        box.call(),
        box.call(_A, file="try.txt"),
        box.call(_B, file="try.txt"),
        box.call(),
    ]
    assert _failed(_A) in result.stdout and _failed(_B) in result.stdout
    assert box.checked() == []
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_names_what_went_in_when_that_last_install_fails(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    result, calls = box.run(1, 0, 0, 1, 1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-2:] == [box.call(_A), box.call()]
    # B failed on its own; A went in, and only the reinstall of what went in failed.
    assert result.stdout.count(_not_carried(_A)) == 1
    assert result.stdout.count(_failed(_B)) == 1 and _not_carried(_B) not in result.stdout
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_reinstalls_with_what_matched_when_check_takes_one_out(
    tmp_path: Path, shell: str
) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    box.check_code = 3
    box.kept_lines = [_A]
    result, calls = box.run(0, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[1] == box.call(_A, file="kept.txt")
    assert f"carried over: {_A}. Checking" in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_reinstalls_bare_when_check_kept_nothing(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.check_code = 3
    box.kept_lines = []
    result, calls = box.run(0, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[1] == box.call()
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_names_what_check_kept_when_that_reinstall_fails(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    box.check_code = 3
    box.kept_lines = [_A]
    result, calls = box.run(0, 1, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-1] == box.call()
    assert "that reinstall failed; installing aelix with nothing carried over." in result.stdout
    assert _not_carried(_A) in result.stdout and _not_carried(_B) not in result.stdout
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_installs_bare_when_check_cannot_compare(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = [_A, _B]
    box.check_code = 1
    result, calls = box.run(0, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-1] == box.call()
    assert "could not compare the carried extensions (exit 1)" in result.stdout
    assert _not_carried(_A) in result.stdout and _not_carried(_B) in result.stdout
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_installs_bare_when_there_is_nothing_to_carry(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    box.with_lines = []
    result, calls = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [box.call()]
    assert "verify ran" not in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
@pytest.mark.parametrize("python", ["fails", "missing", "broken"])
def test_step5_names_what_it_can_when_the_environment_cannot_be_read(
    tmp_path: Path, shell: str, python: str
) -> None:
    """Codex r1 L1/L2: carry.py failing, or the old python not running at all, used to
    carry nothing and name nothing; a python that is gone also left the shell's own
    `.../bin/python: not found` line in the output."""

    box = _Step5(tmp_path, shell)
    if python == "fails":
        box.collect_code = 1
    else:
        box.python = python
    box.dist_infos = {
        "named_ext-1.0": True,
        "bare_ext-2.0": True,
        "aelix_coding_agent-0.1.0": True,
        "aelix-0.1.0": True,
        "rich-13.0": False,
    }
    # Verify r2 N7: by the project name METADATA gives, not the directory's stem.
    box.metadata = {
        "named_ext-1.0": "Metadata-Version: 2.1\nName: Named.Ext\nVersion: 1.0\n\nName: no\n"
    }
    result, calls = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == [box.call()]
    assert (
        "aelix-install: not carried: Named.Ext 1.0 - the current environment could not be read"
        in result.stdout
    )
    assert (
        "aelix-install: not carried: bare_ext 2.0 (named from its directory: its METADATA cannot "
        "be read) - the current environment could not be read"
    ) in result.stdout
    assert result.stdout.count("not carried:") == 2
    assert (
        "'aelix extension restore --all' puts back each one 'aelix extension install' recorded "
        "at a pin; the next interactive aelix start does it too."
    ) in result.stdout
    assert "not found" not in result.stderr and "not found" not in result.stdout
    if python == "fails":
        assert (
            "could not read the current aelix environment; nothing in it is carried over."
            in result.stdout
        )
    else:
        assert "python does not run, so nothing in it can be carried over." in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_gives_the_manual_command_when_the_new_aelix_has_no_restore(
    tmp_path: Path, shell: str
) -> None:
    box = _Step5(tmp_path, shell)
    box.python = "missing"
    box.restore_help_code = 2
    box.dist_infos = {"named_ext-1.0": True}
    result, _ = box.run(0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "aelix 0.1.1 has no restore command: reinstall each with 'aelix extension install "
        "<the git URL, path or name you installed it from>'."
    ) in result.stdout


@pytest.mark.parametrize("shell", _SHELLS)
def test_step5_still_dies_when_aelix_itself_cannot_install(tmp_path: Path, shell: str) -> None:
    box = _Step5(tmp_path, shell)
    result, calls = box.run(1, 1)
    assert result.returncode == 1
    assert "ERROR: uv tool install failed for 'aelix[tui]==0.1.1'" in result.stdout
    assert len(calls) == 2
