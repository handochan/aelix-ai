"""#392 (ADR-0255 §12, ADR-0200) — a catalog install's installer never runs in the cwd.

uv reads project configuration — ``uv.toml`` and ``pyproject.toml`` ``[tool.uv]`` — from
its working directory and every ancestor. ``discover install`` ran ``uv pip install
<name>`` in the user's cwd, so a cloned repository whose ``[tool.uv]`` set
``find-links = ["./w"]`` turned a TRUSTED catalog's package name into the repository's
own wheel (measured on 7dc0e05d and again on 402a8013: ``local-ext`` 9.9 CWD-DECOY
installed, exit 0). Owner decision (2026-10-07, option a): an install whose source came
from a catalog — ``discover install`` — and every ``update`` run the installer child in
``<agent dir>/installer-cwd``, a directory (``0700`` on POSIX) holding an aelix-written
``pyproject.toml`` with no ``[project]`` and a ``uv.toml`` that sets nothing. uv first
discovers a project (the nearest ``pyproject.toml``) and reads configuration from its
workspace root, so the ``uv.toml`` alone (round 1) was passed over whenever an ancestor
``pyproject.toml`` had a ``[project]`` table; with both files uv reads no project
configuration from there or from any ancestor. The user-level and system ``uv.toml``,
``UV_CONFIG_FILE`` and ``UV_*`` still apply. Whether pip.conf is translated for uv is
decided from that same directory, by uv's own rule (``_uv_project_config``: project-root
discovery, then the first config file). A typed ``extension install`` keeps running in
the cwd (the known limit ADR-0255 §12 states).

Most rows replace ``_default_runner`` with a recorder (no installer runs) and read the
``cwd`` it was handed. The ``real uv`` rows run the uv on PATH (CI's pinned one) against a
throwaway venv, offline — the measured attack and the ancestor-shape matrix of ADR-0255
§15 (review round 2) — and are skipped when no absolute ``uv`` is on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import stat as stat_mod
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli import extension_install as ei
from aelix_coding_agent.cli.extension_install import run_extension_command_async

from tests.env_sandbox import sandbox_home

#: Every env var that changes which index/config pip or uv uses — cleared per row.
_CONFIG_ENV = (
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_CONFIG_FILE",
    "PIP_FIND_LINKS",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_CONFIG_FILE",
    "UV_NO_INDEX",
    "UV_FIND_LINKS",
    "UV_NO_CONFIG",
    "UV_PYTHON",
    "UV_SYSTEM_PYTHON",
    "UV_PROJECT",
    "UV_WORKING_DIR",
    "UV_WORKING_DIRECTORY",
    "VIRTUAL_ENV",
)

_DECOY_PYPROJECT = (
    '[project]\nname = "clonedrepo"\nversion = "0"\n[tool.uv]\nfind-links = ["./w"]\n'
)


class _Recorder:
    """Stands in for ``_default_runner``: records ``(argv, cwd, translated env)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None, dict[str, str]]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        env = {k: os.environ[k] for k in ("UV_INDEX_URL", "UV_EXTRA_INDEX_URL") if k in os.environ}
        self.calls.append((list(argv), cwd, env))
        return subprocess.CompletedProcess(args=argv, returncode=0)


def _wheel(directory: Path, origin: str, version: str, name: str = "local-ext") -> Path:
    """A minimal installable wheel whose module records ``ORIGIN = <origin>``."""

    directory.mkdir(parents=True, exist_ok=True)
    dist = name.replace("-", "_")
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    info = f"{dist}-{version}.dist-info"
    files = {
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: t392\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist}.py": f"ORIGIN = {origin!r}\n",
    }
    files[f"{info}/RECORD"] = "".join(f"{p},,\n" for p in [*files, f"{info}/RECORD"])
    with zipfile.ZipFile(path, "w") as zf:
        for member, text in files.items():
            zf.writestr(member, text)
    return path


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """An isolated agent dir + HOME, a local catalog, and a HOSTILE cwd.

    The cwd is a "cloned repository": its ``pyproject.toml`` ``[tool.uv]`` points
    ``find-links`` at ``./w``, which holds ``local-ext`` 9.9 (CWD-DECOY).
    """

    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    home = sandbox_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("AELIX_DEFAULT_CATALOG", "")
    # pip's ambient config comes from the TEST (PIP_INDEX_URL), never the host.
    monkeypatch.setattr(ei, "_pip_config_candidates", lambda env=None: [])
    # nor uv's system-level config (/etc/uv/uv.toml on a developer box)
    monkeypatch.setattr(ei, "_uv_system_config_file", lambda env: None, raising=False)
    catdir = tmp_path / "cat"
    (catdir / "local-dir").mkdir(parents=True)
    (catdir / "local-dir" / "pyproject.toml").write_text(
        '[project]\nname = "local-dir"\nversion = "1.0"\n', encoding="utf-8"
    )
    _wheel(catdir, "CAT-WHEEL", "1.0", name="cat-wheel")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(_DECOY_PYPROJECT, encoding="utf-8")
    _wheel(repo / "w", "CWD-DECOY", "9.9")
    monkeypatch.chdir(repo)
    return {
        "root": tmp_path,
        "home": home,
        "catdir": catdir,
        "repo": repo,
        "neutral": tmp_path / "agent" / "installer-cwd",
    }


def _use_backend(monkeypatch: pytest.MonkeyPatch, lab: dict[str, Path], name: str) -> None:
    if name == "uv":
        backend = ei.InstallBackend(name="uv", uv_path=str(lab["root"] / "bin" / "uv"))
    else:
        backend = ei.PIP_BACKEND
    monkeypatch.setattr(ei, "resolve_install_backend", lambda _runner: backend)


def _recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(ei, "_default_runner", rec)
    return rec


async def _catalog(lab: dict[str, Path], source: str) -> SettingsManager:
    """Register a LOCAL catalog listing ``probe`` → ``source`` and refresh it."""

    cat = lab["catdir"] / "catalog.json"
    cat.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "trusted",
                "extensions": [{"name": "probe", "source": source}],
            }
        ),
        encoding="utf-8",
    )
    mem = SettingsManager.in_memory({"extensionSources": [{"spec": str(cat), "kind": "catalog"}]})
    assert await run_extension_command_async(["discover", "--refresh"], settings=mem) == 0
    return mem


def _same_dir(a: str | None, b: Path) -> bool:
    return a is not None and os.path.realpath(a) == os.path.realpath(b)


# === where the installer runs ================================================

_CATALOG_SOURCES = {
    "name": "local-ext",
    "name-with-extras": "local-ext[feature]",
    "path-dir": "./local-dir",
    "path-wheel": "./cat_wheel-1.0-py3-none-any.whl",
    "git": "local-ext @ git+https://git.example.invalid/team/local-ext.git",
    "url": "https://files.example.invalid/local_ext-1.0-py3-none-any.whl",
}


@pytest.mark.parametrize("backend", ["uv", "pip"])
@pytest.mark.parametrize("form", list(_CATALOG_SOURCES))
async def test_every_catalog_entry_runs_its_installer_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, backend: str, form: str
) -> None:
    """Every source form, both backends: the child's cwd is ``<agent>/installer-cwd``.

    pip reads no project config, but ``python -m pip`` puts its working directory
    first on ``sys.path`` (a cloned repo's ``pip/`` package ran as the installer,
    measured), so the pip backend runs there too."""

    _use_backend(monkeypatch, lab, backend)
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, _CATALOG_SOURCES[form])

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert len(rec.calls) == 1
    _argv, cwd, _env = rec.calls[0]
    assert _same_dir(cwd, lab["neutral"]), cwd
    assert not _same_dir(cwd, lab["repo"])


async def test_the_installer_directory_is_private_and_holds_aelixs_two_files(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory is user-only (POSIX), its ``uv.toml`` sets nothing and its
    ``pyproject.toml`` declares nothing — even when something else was written there
    first, aelix's text is restored."""

    lab["neutral"].mkdir(parents=True, mode=0o755)
    (lab["neutral"] / "uv.toml").write_text(
        f'find-links = ["{(lab["repo"] / "w").as_posix()}"]\n', encoding="utf-8"
    )
    (lab["neutral"] / "pyproject.toml").write_text(_DECOY_PYPROJECT, encoding="utf-8")
    _use_backend(monkeypatch, lab, "uv")
    _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    uv_toml = (lab["neutral"] / "uv.toml").read_text(encoding="utf-8")
    pyproject = (lab["neutral"] / "pyproject.toml").read_text(encoding="utf-8")
    assert uv_toml == ei.INSTALLER_CWD_UV_TOML
    assert pyproject == ei.INSTALLER_CWD_PYPROJECT_TOML
    assert tomllib.loads(uv_toml) == {}
    assert tomllib.loads(pyproject) == {}  # no [project], no [tool.uv]
    assert sorted(p.name for p in lab["neutral"].iterdir()) == ["pyproject.toml", "uv.toml"]
    if sys.platform != "win32":  # POSIX permission bits; Windows has no 0700
        assert (lab["neutral"].stat().st_mode & 0o777) == 0o700


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create a symlink")


@pytest.mark.parametrize("name", ["uv.toml", "pyproject.toml"])
@pytest.mark.parametrize("target_text", ["other", "exact-sentinel-text"])
def test_a_linked_sentinel_is_refused_never_followed(
    tmp_path: Path, name: str, target_text: str
) -> None:
    """A sentinel that is a link elsewhere is refused (review round 3) — even one
    whose target already holds aelix's exact text, because the target can change
    after the check (Codex round 2: the content-only check kept such a link, and a
    target rewritten during consent pointed uv at a decoy). The target is never
    written."""

    agent = tmp_path / "agent"
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    neutral.mkdir(parents=True)
    outside = tmp_path / "outside.toml"
    text = ei._INSTALLER_CWD_FILES[name] if target_text == "exact-sentinel-text" else "x = 1\n"
    outside.write_text(text, encoding="utf-8")
    _symlink_or_skip(neutral / name, outside)

    with pytest.raises(ei.InstallerDirRefused):
        ei.catalog_installer_cwd(str(agent))

    assert (neutral / name).is_symlink()
    assert outside.read_text(encoding="utf-8") == text


def test_a_sentinel_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    (neutral / "uv.toml").mkdir(parents=True)

    with pytest.raises(ei.InstallerDirRefused):
        ei.catalog_installer_cwd(str(agent))


def test_a_regular_sentinel_with_other_text_is_replaced(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    neutral.mkdir(parents=True)
    (neutral / "uv.toml").write_text("index-url = 'https://x.example.invalid/'\n", "utf-8")

    assert ei.catalog_installer_cwd(str(agent)) == str(neutral)

    assert (neutral / "uv.toml").read_bytes() == ei.INSTALLER_CWD_UV_TOML.encode("utf-8")
    assert (neutral / "pyproject.toml").read_bytes() == (
        ei.INSTALLER_CWD_PYPROJECT_TOML.encode("utf-8")
    )


async def test_a_linked_installer_directory_is_refused_and_the_cwd_untouched(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``installer-cwd`` as a link to the user's cwd (Codex round 2): the installer
    ran THERE and the cwd's pyproject.toml / uv.toml were overwritten with sentinels.
    Now: refused (2), nothing runs, nothing in the cwd changes."""

    lab["neutral"].parent.mkdir(parents=True, exist_ok=True)
    _symlink_or_skip(lab["neutral"], lab["repo"], directory=True)
    (lab["repo"] / "uv.toml").write_text("offline = true\n", encoding="utf-8")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    capsys.readouterr()

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 2
    assert rec.calls == []
    assert (lab["repo"] / "pyproject.toml").read_text(encoding="utf-8") == _DECOY_PYPROJECT
    assert (lab["repo"] / "uv.toml").read_text(encoding="utf-8") == "offline = true\n"
    assert "is a link or not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["uv", "pip"])
async def test_the_consent_block_names_the_directory_the_installer_runs_in(
    lab: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
) -> None:
    """The line names the directory the child really starts in — and no longer says
    "not the current directory", which was false when the two were the same."""

    _use_backend(monkeypatch, lab, backend)
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    capsys.readouterr()

    await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    out = capsys.readouterr().out
    started_in = rec.calls[0][1]
    assert started_in is not None
    assert f"  the installer runs in {started_in} (aelix's installer directory)" in out
    assert "not the current directory" not in out
    if backend == "uv":
        assert "uv reads no project configuration (uv.toml, pyproject.toml)" in out
        assert "from any directory above it" in out


async def test_pip_downloads_for_verification_in_the_installer_directory_too(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every child of the call: on pip, ``--verify-pypi`` runs ``pip download`` first."""

    _use_backend(monkeypatch, lab, "pip")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")

    await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--verify-pypi"], settings=mem
    )

    assert rec.calls, "pip download never ran"
    argv, cwd, _env = rec.calls[0]
    assert "download" in argv
    assert _same_dir(cwd, lab["neutral"]), cwd


async def test_no_installer_directory_means_no_install_never_the_cwd(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A FILE where the directory goes: refused (2), the installer never runs."""

    lab["neutral"].parent.mkdir(parents=True, exist_ok=True)
    lab["neutral"].write_text("not a directory", encoding="utf-8")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    capsys.readouterr()

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 2
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "installer directory" in err
    assert "never runs in the current directory" in err


async def test_a_typed_relative_index_url_is_anchored_at_the_cwd_for_a_catalog_install(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv reads ``--index-url ./simple`` against its own cwd (measured); the user typed
    it in theirs, so a catalog install hands uv the absolute path."""

    (lab["repo"] / "simple").mkdir()
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify", "--index-url", "./simple"],
        settings=mem,
    )

    assert code == 0
    argv = rec.calls[0][0]
    given = argv[argv.index("--index-url") + 1]
    assert os.path.isabs(given)
    assert os.path.realpath(given) == os.path.realpath(lab["repo"] / "simple")


# === update ==================================================================


@pytest.mark.parametrize(
    "record",
    [
        {"spec": "local-ext", "kind": "pypi", "name": "local-ext"},
        {"spec": "git+https://git.example.invalid/team/ext.git", "kind": "git", "name": "ext"},
        {"spec": "<catdir>/local-dir", "kind": "path", "name": "local-dir"},
    ],
    ids=["pypi", "git", "path"],
)
async def test_update_runs_every_record_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, record: dict[str, str]
) -> None:
    """A record does not say whether a catalog chose its source, and none was chosen
    in the directory ``update`` runs in: every update runs in the installer dir."""

    spec = record["spec"].replace("<catdir>", str(lab["catdir"]))
    mem = SettingsManager.in_memory({"extensionSources": [{**record, "spec": spec}]})
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)

    await run_extension_command_async(["update", "--yes", "--no-verify"], settings=mem)

    assert len(rec.calls) == 1
    assert _same_dir(rec.calls[0][1], lab["neutral"]), rec.calls[0][1]


async def test_update_of_an_unrecorded_name_runs_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)

    await run_extension_command_async(
        ["update", "never-recorded", "--yes", "--no-verify"],
        settings=SettingsManager.in_memory(),
    )

    assert len(rec.calls) == 1
    assert _same_dir(rec.calls[0][1], lab["neutral"]), rec.calls[0][1]


# === the typed install: the known limit ======================================


async def test_a_typed_install_still_runs_in_the_cwd(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0255 §12 known limit: the user typed the source here."""

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)

    code = await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"], settings=SettingsManager.in_memory()
    )

    assert code == 0
    assert rec.calls[0][1] is None  # inherited: the process cwd
    assert not lab["neutral"].exists()


# === pip.conf translation is decided from where uv runs =====================


async def test_a_cwd_uv_index_no_longer_switches_the_pip_conf_translation_off(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cwd's ``[tool.uv]`` index is not read by the uv a catalog install runs, so
    it must not suppress the org's pip index either (before: suppressed → uv fell back
    to its default index for a catalog name)."""

    (lab["repo"] / "uv.toml").write_text(
        'index-url = "https://decoy.example.invalid/simple"\n', encoding="utf-8"
    )
    monkeypatch.setenv("PIP_INDEX_URL", "https://org.example.invalid/simple")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")

    await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert rec.calls[0][2].get("UV_INDEX_URL") == "https://org.example.invalid/simple"

    # the typed install in the same cwd: uv reads that uv.toml, so aelix leaves it be
    rec.calls.clear()
    await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"], settings=SettingsManager.in_memory()
    )
    assert "UV_INDEX_URL" not in rec.calls[0][2]


async def test_a_user_level_uv_index_still_suppresses_the_translation(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0200's org pin in the user-level ``uv.toml`` is still uv's to own."""

    for user in (
        lab["home"] / ".config" / "uv",  # $XDG_CONFIG_HOME (POSIX, macOS too)
        lab["home"] / "AppData" / "Roaming" / "uv",  # %APPDATA% (Windows)
    ):
        user.mkdir(parents=True)
        (user / "uv.toml").write_text(
            'index-url = "https://org-uv.example.invalid/simple"\n', encoding="utf-8"
        )
    monkeypatch.setenv("PIP_INDEX_URL", "https://org-pip.example.invalid/simple")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")

    await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert "UV_INDEX_URL" not in rec.calls[0][2]


#: A hostile config's find-links, ``@W@`` replaced by the wheel directory it offers.
_PROJ = '[project]\nname = "planted"\nversion = "0"\n'
_TL = '[tool.uv]\nfind-links = ["@W@"]\n'
_FL = 'find-links = ["@W@"]\n'
_MEMBERS_MISS = '[tool.uv.workspace]\nmembers = ["packages/*"]\n'

#: ADR-0255 §15 (review round 2): the ancestor shapes. Each places files in a directory ABOVE the
#: installer directory; every one is read by uv from a bare directory there.
ANCESTOR_SHAPES: dict[str, dict[str, str]] = {
    "uv-toml": {"uv.toml": _FL},
    "tool-uv-only": {"pyproject.toml": _TL},
    "project+tool-uv": {"pyproject.toml": _PROJ + _TL},
    "project+uv-toml": {"pyproject.toml": _PROJ, "uv.toml": _FL},
    "workspace-root": {"pyproject.toml": _PROJ + _MEMBERS_MISS + _TL},
    "virtual-workspace-root": {"pyproject.toml": _MEMBERS_MISS + _TL},
    "workspace-glob-hits-agent-dir": {
        "pyproject.toml": _PROJ
        + '[tool.uv.workspace]\nmembers = [".aelix/agent/installer-*", "agentrel/installer-*"]\n'
        + _TL
    },
    "project-in-the-agent-dir": {"@AGENT@/pyproject.toml": _PROJ + _TL},
    "managed-false+tool-uv": {
        "pyproject.toml": _PROJ + '[tool.uv]\nmanaged = false\nfind-links = ["@W@"]\n'
    },
    "unparsable-pyproject+uv-toml": {"pyproject.toml": "not [[[ toml\n", "uv.toml": _FL},
}


def _place(where: Path, files: Mapping[str, str], wheel_dir: Path, agent_rel: str) -> None:
    """Write a shape's files under ``where``; ``@AGENT@`` is the agent dir there."""

    for rel, text in files.items():
        target = where / rel.replace("@AGENT@", agent_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace("@W@", wheel_dir.as_posix()), encoding="utf-8")


def _no_system_uv_config(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, str]:
    """An env whose user/system uv config is empty, for the model rows."""

    monkeypatch.setattr(ei, "_uv_system_config_file", lambda env: None, raising=False)
    return {
        "XDG_CONFIG_HOME": str(root / "xdg"),
        "APPDATA": str(root / "appdata"),
        "XDG_CONFIG_DIRS": str(root / "no-system-config"),
    }


@pytest.mark.parametrize("shape", list(ANCESTOR_SHAPES))
def test_from_the_installer_directory_no_ancestor_config_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """Every ancestor shape: the project file uv reads from the installer directory is
    aelix's empty ``uv.toml``, so an index above it never switches the pip.conf
    translation off — and from the agent dir itself (a typed install run there) the
    index does count. Round 1 (``uv.toml`` alone) named the ancestor for the
    ``[project]`` shapes."""

    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    env = _no_system_uv_config(monkeypatch, tmp_path)
    home = tmp_path / "home"
    agent = home / ".aelix" / "agent"
    agent.mkdir(parents=True)
    _place(home, ANCESTOR_SHAPES[shape], tmp_path / "w", ".aelix/agent")
    neutral = ei.catalog_installer_cwd(str(agent))

    files = ei._uv_config_files(env, cwd=neutral)

    assert files[0] == str(Path(neutral, "uv.toml").resolve())
    assert ei._uv_has_own_index_config(env, cwd=neutral) is False
    assert ei._uv_has_own_index_config(env, cwd=str(agent)) is True


def test_uv_no_config_and_uv_config_file_are_modeled(tmp_path: Path) -> None:
    """``UV_CONFIG_FILE`` alone is read (it wins even over ``UV_NO_CONFIG``, as in uv's
    own order); ``UV_NO_CONFIG`` reads nothing."""

    (tmp_path / "uv.toml").write_text('index-url = "https://p.example.invalid/s"\n', "utf-8")
    assert ei._uv_config_files({"UV_NO_CONFIG": "1"}, cwd=str(tmp_path)) == []
    assert ei._uv_config_files({"UV_NO_CONFIG": "true"}, cwd=str(tmp_path)) == []
    # A NATIVE absolute path (round 5b): a POSIX '/x/uv.toml' is absolute to ntpath
    # only on Python <= 3.12 (3.13 needs a drive or a share).
    pinned = str(tmp_path / "x" / "uv.toml")
    assert ei._uv_config_files(
        {"UV_NO_CONFIG": "1", "UV_CONFIG_FILE": pinned}, cwd=str(tmp_path)
    ) == [pinned]
    assert ei._uv_has_own_index_config({"UV_NO_CONFIG": "1"}, cwd=str(tmp_path)) is False


def test_the_user_config_is_uvs_per_platform(tmp_path: Path) -> None:
    """uv's user ``uv.toml``: ``%APPDATA%`` on Windows, ``$XDG_CONFIG_HOME`` (absolute)
    elsewhere — macOS included (uv's ``etcetera`` XDG strategy)."""

    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), "APPDATA": str(tmp_path / "appdata")}
    got = ei._uv_user_config_file(env)
    if sys.platform == "win32":
        assert got == os.path.join(str(tmp_path / "appdata"), "uv", "uv.toml")
    else:
        assert got == os.path.join(str(tmp_path / "xdg"), "uv", "uv.toml")
        relative = ei._uv_user_config_file({"XDG_CONFIG_HOME": "rel/xdg"})
        assert relative == os.path.join(os.path.expanduser("~"), ".config", "uv", "uv.toml")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="uv's system config on Windows is "
    "%SYSTEMDRIVE%\\ProgramData\\uv\\uv.toml, a machine-wide path a test must not write",
)
def test_the_system_config_is_the_first_existing_xdg_config_dirs_file(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    (second / "uv").mkdir(parents=True)
    (second / "uv" / "uv.toml").write_text("", "utf-8")
    env = {"XDG_CONFIG_DIRS": f"{first}:{second}"}
    assert ei._uv_system_config_file(env) == os.path.join(str(second), "uv", "uv.toml")
    (first / "uv").mkdir(parents=True)
    (first / "uv" / "uv.toml").write_text("", "utf-8")
    assert ei._uv_system_config_file(env) == os.path.join(str(first), "uv", "uv.toml")


#: Layouts whose project file a NEAREST-only walk gets wrong, or right — each file's
#: find-links offers ``local-ext`` at its own version, so uv's pick names the file.
#: ``(files: {relpath: text with @W<n>@}, cwd relpath, expected file relpath | None)``
MODEL_LAYOUTS: dict[str, tuple[dict[str, str], str, str | None]] = {
    "subdir-of-a-project": (
        {
            "proj/pyproject.toml": _PROJ + '[tool.uv]\nfind-links = ["@W2@"]\n',
            "proj/sub/uv.toml": 'find-links = ["@W3@"]\n',
        },
        "proj/sub",
        "proj/pyproject.toml",
    ),
    "project-without-tool-uv-under-tool-uv": (
        {
            "a/uv.toml": 'find-links = ["@W1@"]\n',
            "a/b/pyproject.toml": '[tool.uv]\nfind-links = ["@W2@"]\n',
            "a/b/c/pyproject.toml": _PROJ,
        },
        "a/b/c",
        "a/b/pyproject.toml",
    ),
    "workspace-member": (
        {
            "ws/pyproject.toml": _PROJ + _MEMBERS_MISS + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/packages/m/pyproject.toml": '[project]\nname = "m"\nversion = "0"\n',
            "ws/packages/m/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "ws/packages/m",
        "ws/pyproject.toml",
    ),
    "workspace-member-excluded": (
        {
            "ws/pyproject.toml": _PROJ
            + '[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/m"]\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/packages/m/pyproject.toml": '[project]\nname = "m"\nversion = "0"\n',
            "ws/packages/m/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "ws/packages/m",
        "ws/packages/m/uv.toml",
    ),
    "not-a-workspace-member": (
        {
            "ws/pyproject.toml": _PROJ
            + '[tool.uv.workspace]\nmembers = ["libs/*"]\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/packages/m/pyproject.toml": '[project]\nname = "m"\nversion = "0"\n',
            "ws/packages/m/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "ws/packages/m",
        "ws/packages/m/uv.toml",
    ),
    "unmanaged-project": (
        {
            "x/pyproject.toml": _PROJ + '[tool.uv]\nmanaged = false\nfind-links = ["@W1@"]\n',
            "x/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "x/sub",
        "x/sub/uv.toml",
    ),
    "managed-project-same-shape": (
        {
            "x/pyproject.toml": _PROJ + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "x/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "x/sub",
        "x/pyproject.toml",
    ),
    "virtual-workspace-root": (
        {
            "v/pyproject.toml": "[tool.uv.workspace]\nmembers = []\n"
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "v/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "v/sub",
        "v/pyproject.toml",
    ),
    "no-project-pyproject-nearest": (
        {
            "d/pyproject.toml": "# nothing\n",
            "uv.toml": 'find-links = ["@W1@"]\n',
        },
        "d",
        "uv.toml",
    ),
    "unparsable-pyproject-above-a-uv-toml": (
        {
            "u/pyproject.toml": "not [[[ toml\n",
            "u/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "u/sub",
        "u/sub/uv.toml",
    ),
    "project-table-without-a-name": (
        {
            "n/pyproject.toml": '[project]\nversion = "0"\n[tool.uv]\nfind-links = ["@W1@"]\n',
            "n/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "n/sub",
        "n/sub/uv.toml",
    ),
    # review round 3 — each a mutant of the model that survived round 2, checked here
    # against the real uv's pick:
    "uv-toml-beside-a-tool-uv-pyproject": (
        {
            "s/pyproject.toml": '[tool.uv]\nfind-links = ["@W1@"]\n',
            "s/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "s",
        "s/uv.toml",
    ),
    "uv-toml-beside-a-project-with-tool-uv": (
        {
            "p/pyproject.toml": _PROJ + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "p/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "p",
        "p/uv.toml",
    ),
    "project-in-a-non-member-project-in-a-workspace": (
        {
            "ws/pyproject.toml": _PROJ
            + '[tool.uv.workspace]\nmembers = ["other/*"]\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/other/pyproject.toml": '[project]\nname = "other"\nversion = "0"\n'
            + '[tool.uv]\nfind-links = ["@W2@"]\n',
            "ws/other/inner/pyproject.toml": '[project]\nname = "inner"\nversion = "0"\n',
        },
        "ws/other/inner",
        "ws/other/pyproject.toml",
    ),
    "project-under-a-no-project-pyproject-in-a-workspace": (
        {
            "ws/pyproject.toml": _PROJ
            + '[tool.uv.workspace]\nmembers = ["mid/*"]\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/mid/pyproject.toml": '[tool.uv]\nfind-links = ["@W2@"]\n',
            "ws/mid/p/pyproject.toml": '[project]\nname = "p"\nversion = "0"\n',
        },
        "ws/mid/p",
        "ws/mid/pyproject.toml",
    ),
    "project-name-not-a-package-name": (
        {
            "n/pyproject.toml": '[project]\nname = "-not-a-name-"\nversion = "0"\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "n/sub/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "n/sub",
        "n/sub/uv.toml",
    ),
    "project-name-not-a-package-name-tool-uv-still-read": (
        {
            "n/pyproject.toml": '[project]\nname = "not a name"\nversion = "0"\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
        },
        "n/sub",
        "n/pyproject.toml",
    ),
    "member-under-a-project-named-badly": (
        {
            "ws/pyproject.toml": _PROJ
            + '[tool.uv.workspace]\nmembers = ["bad/*"]\n'
            + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "ws/bad/pyproject.toml": '[project]\nname = "bad!"\nversion = "0"\n',
            "ws/bad/p/pyproject.toml": '[project]\nname = "p"\nversion = "0"\n',
            "ws/bad/p/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "ws/bad/p",
        "ws/bad/p/uv.toml",
    ),
    "installer-directory-under-a-project": (
        {
            "home/pyproject.toml": _PROJ + '[tool.uv]\nfind-links = ["@W1@"]\n',
            "home/uv.toml": 'find-links = ["@W2@"]\n',
        },
        "<installer>",
        "home/.aelix/agent/installer-cwd/uv.toml",
    ),
}


def _wheel_dirs(root: Path) -> dict[str, Path]:
    """``@W<n>@`` -> a directory offering ``local-ext`` version ``<n>.0``."""

    out = {}
    for n in (1, 2, 3):
        out[f"@W{n}@"] = root / "wheels" / f"w{n}"
        _wheel(out[f"@W{n}@"], f"W{n}", f"{n}.0")
    return out


def _layout(root: Path, files: Mapping[str, str]) -> dict[str, str]:
    """Write a layout; return ``file relpath -> the @W<n>@ it offers``."""

    wheels = _wheel_dirs(root)
    offers: dict[str, str] = {}
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        for token, directory in wheels.items():
            if token in text:
                offers[rel] = token
                text = text.replace(token, directory.as_posix())
        target.write_text(text, encoding="utf-8")
    return offers


@pytest.mark.parametrize("layout", list(MODEL_LAYOUTS))
def test_the_model_names_the_project_file_uv_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    """``_uv_project_config`` follows uv: project-root discovery, then the first
    config file from that root up. (Round 1's nearest-file walk named
    ``proj/sub/uv.toml`` for ``subdir-of-a-project`` while uv read
    ``proj/pyproject.toml``, measured.)"""

    files, cwd_rel, expected = MODEL_LAYOUTS[layout]
    root = tmp_path / "m"
    _layout(root, files)
    if cwd_rel == "<installer>":
        cwd = Path(ei.catalog_installer_cwd(str(root / "home" / ".aelix" / "agent")))
    else:
        cwd = root / cwd_rel
    cwd.mkdir(parents=True, exist_ok=True)

    got = ei._uv_project_config(cwd.resolve())

    want = None if expected is None else str((root / expected).resolve())
    assert got == want


@pytest.mark.parametrize("layout", list(MODEL_LAYOUTS))
def test_real_uv_reads_the_project_file_the_model_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    """The model row above, against the uv on PATH: the version uv would install
    (``--dry-run``, offline) is the one the model's file offers."""

    uv = _real_uv()
    files, cwd_rel, _expected = MODEL_LAYOUTS[layout]
    root = tmp_path / "m"
    offers = _layout(root, files)
    if cwd_rel == "<installer>":
        cwd = Path(ei.catalog_installer_cwd(str(root / "home" / ".aelix" / "agent")))
    else:
        cwd = root / cwd_rel
    cwd.mkdir(parents=True, exist_ok=True)
    home = sandbox_home(monkeypatch, tmp_path / "fakehome")
    venv = _throwaway_venv(uv, tmp_path)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("UV_", "PIP_")) and k not in ("VIRTUAL_ENV", "XDG_CONFIG_DIRS")
    }
    env.update(
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CONFIG_DIRS=str(tmp_path / "no-system-config"),
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        NO_COLOR="1",
    )

    picked = _uv_dry_run_pick(uv, venv, cwd, env)

    model = ei._uv_project_config(cwd.resolve())
    model_rel = None if model is None else Path(model).relative_to(root.resolve()).as_posix()
    model_offer = offers.get(model_rel) if model_rel is not None else None
    want = None if model_offer is None else f"local-ext=={model_offer[2:-1]}.0"
    assert picked == want, (model_rel, picked)


# === the measured attack, with the real uv ===================================


def _real_uv() -> str:
    uv = shutil.which("uv")
    if uv is None or not os.path.isabs(uv):
        pytest.skip("no absolute uv on PATH")
    return uv


def _venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _throwaway_venv(uv: str, root: Path) -> Path:
    """A venv for uv to install into (never this interpreter's); its python."""

    clean = root / "clean"
    clean.mkdir(exist_ok=True)
    venv = root / "venv"
    made = subprocess.run(
        [uv, "venv", "-q", "--python", sys.executable, str(venv)],
        cwd=clean,
        env={**os.environ, "UV_NO_CONFIG": "1", "UV_OFFLINE": "1"},
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"uv venv failed: {made.stderr[-300:]!r}")
    return _venv_python(venv)


def _uv_dry_run_pick(uv: str, python: Path, cwd: Path, env: Mapping[str, str]) -> str | None:
    """``local-ext==<version>`` uv would install from ``cwd``, or ``None``."""

    out = subprocess.run(
        [uv, "pip", "install", "--dry-run", "--python", str(python), "local-ext"],
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        check=False,
    )
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    # uv names the [tool.uv] fields it passes over for a uv.toml beside them — the
    # pick this row asserts; any other warning fails the row.
    lines, skipping = [], False
    for line in text.splitlines():
        if line.startswith("warning: Found both a `uv.toml` file and a `[tool.uv]`"):
            skipping = True
            continue
        if skipping and line.startswith("- "):
            continue
        skipping = False
        lines.append(line)
    assert "warning" not in "\n".join(lines).lower(), text
    for line in text.splitlines():
        if "local-ext==" in line:
            return line.strip().lstrip("+").strip()
    return None


@pytest.fixture
def real_uv_lab(lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """The real uv, offline, installing into a throwaway venv (never this one).

    The org's pin is a user-level ``uv.toml`` ``find-links`` to ``local-ext`` 1.0
    (ORG-PIN); the hostile cwd's ``[tool.uv]`` adds ``./w`` with 9.9 (CWD-DECOY). uv
    merges both when it reads both, and 9.9 wins."""

    uv = _real_uv()
    target = str(_throwaway_venv(uv, lab["root"]))
    clean = lab["root"] / "clean"
    org = lab["root"] / "org"
    _wheel(org, "ORG-PIN", "1.0")
    pin = f'find-links = ["{org.as_posix()}"]\n'
    for user_dir in (
        lab["home"] / ".config" / "uv",  # XDG_CONFIG_HOME (POSIX)
        lab["home"] / "AppData" / "Roaming" / "uv",  # %APPDATA% (Windows)
    ):
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "uv.toml").write_text(pin, encoding="utf-8")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(lab["root"] / "uv-cache"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(lab["root"] / "no-system-config"))
    monkeypatch.setattr(
        ei,
        "resolve_install_backend",
        lambda _runner: ei.InstallBackend(name="uv", uv_path=uv),
    )
    real = ei._default_runner

    def into_the_throwaway_venv(
        argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        argv = [target if a == sys.executable else a for a in argv]
        assert target in argv  # never this interpreter's environment
        return real(argv, cwd=cwd) if cwd is not None else real(argv)

    monkeypatch.setattr(ei, "_default_runner", into_the_throwaway_venv)
    return {**lab, "venv_python": Path(target), "clean": clean}


def _installed_origin(lab: dict[str, Path]) -> str:
    out = subprocess.run(
        [str(lab["venv_python"]), "-I", "-c", "import local_ext; print(local_ext.ORIGIN)"],
        cwd=lab["clean"],
        capture_output=True,
        check=False,
    )
    return out.stdout.decode("utf-8", "replace").strip() or "nothing"


async def test_real_uv_a_catalog_name_installs_the_org_pin_not_the_cwd_wheel(
    real_uv_lab: dict[str, Path],
) -> None:
    mem = await _catalog(real_uv_lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(real_uv_lab) == "ORG-PIN"

    # update of that record, from the same hostile cwd, stays on the org pin
    code = await run_extension_command_async(["update", "--yes", "--no-verify"], settings=mem)
    assert code == 0
    assert _installed_origin(real_uv_lab) == "ORG-PIN"


async def test_real_uv_config_above_the_agent_dir_is_not_read_either(
    real_uv_lab: dict[str, Path],
) -> None:
    """Without a project uv walks every ancestor of ITS cwd; the installer directory's
    own ``uv.toml`` ends that walk, so a ``uv.toml`` above the agent dir (here the test
    root — on a real machine ``~``, or ``/tmp`` for an agent dir placed there) is not
    read. (The matrix below covers the ancestors that carry a project.)"""

    _wheel(real_uv_lab["root"] / "above", "ABOVE-AGENT-DIR", "9.8")
    (real_uv_lab["root"] / "uv.toml").write_text(
        f'find-links = ["{(real_uv_lab["root"] / "above").as_posix()}"]\n', encoding="utf-8"
    )
    mem = await _catalog(real_uv_lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(real_uv_lab) == "ORG-PIN"


async def test_real_uv_control_the_cwd_is_hostile_for_a_typed_install(
    real_uv_lab: dict[str, Path],
) -> None:
    """The control row (and the known limit, ADR-0255 §12): the same cwd DOES redirect
    a typed install — so the row above is not passing on a harmless fixture."""

    code = await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"], settings=SettingsManager.in_memory()
    )

    assert code == 0
    assert _installed_origin(real_uv_lab) == "CWD-DECOY"


# === ADR-0255 §15 (review round 2): every ancestor shape, the real CLI and real uv ===


@pytest.mark.parametrize("placement", ["above-the-agent-dir", "relative-agent-dir-in-the-cwd"])
@pytest.mark.parametrize("shape", list(ANCESTOR_SHAPES))
async def test_real_uv_no_ancestor_shape_reaches_a_catalog_install(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, shape: str, placement: str
) -> None:
    """A hostile config ABOVE the installer directory — under the agent dir's
    ancestor (``~`` here), or in the cloned repository a RELATIVE
    ``AELIX_CODING_AGENT_DIR`` puts the agent dir in — offers ``local-ext`` 9.x; the
    user-level ``uv.toml`` pin (1.0, ORG-PIN) must win, for ``discover install`` and
    for ``update``. Round 1 (``uv.toml`` alone) installed the hostile wheel for every
    shape with a ``[project]`` or a workspace root (measured, uv 0.11.14 / 0.11.19 /
    0.12.23)."""

    lab = real_uv_lab
    if placement == "above-the-agent-dir":
        agent = lab["home"] / ".aelix" / "agent"
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
        hostile = lab["root"] / "above"
        _wheel(hostile, "ABOVE-AGENT-DIR", "9.7")
        _place(lab["home"], ANCESTOR_SHAPES[shape], hostile, ".aelix/agent")
    else:
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", "agentrel")
        (lab["repo"] / "pyproject.toml").unlink()
        _place(lab["repo"], ANCESTOR_SHAPES[shape], lab["repo"] / "w", "agentrel")  # 9.9
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"
    code = await run_extension_command_async(["update", "--yes", "--no-verify"], settings=mem)
    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


def _drop_user_pin(lab: dict[str, Path]) -> Path:
    for user_dir in (lab["home"] / ".config" / "uv", lab["home"] / "AppData" / "Roaming" / "uv"):
        (user_dir / "uv.toml").unlink()
    return lab["root"] / "org"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="uv's system config on Windows is %SYSTEMDRIVE%\\ProgramData\\uv\\uv.toml, "
    "a machine-wide path a test must not write",
)
async def test_real_uv_a_system_level_pin_still_applies(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0200's org pin can live in the SYSTEM ``uv.toml`` (``$XDG_CONFIG_DIRS``):
    still read from the installer directory, past a ``[project]`` ancestor."""

    lab = real_uv_lab
    org = _drop_user_pin(lab)
    system = lab["root"] / "system-config"
    (system / "uv").mkdir(parents=True)
    (system / "uv" / "uv.toml").write_text(f'find-links = ["{org.as_posix()}"]\n', "utf-8")
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(system))
    agent = lab["home"] / ".aelix" / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    _wheel(lab["root"] / "above", "ABOVE-AGENT-DIR", "9.7")
    _place(lab["home"], ANCESTOR_SHAPES["project+tool-uv"], lab["root"] / "above", ".aelix/agent")
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


async def test_real_uv_a_uv_config_file_pin_still_applies(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``UV_CONFIG_FILE`` (absolute) is the user's own: uv reads it alone, from any cwd."""

    lab = real_uv_lab
    org = _drop_user_pin(lab)
    explicit = lab["root"] / "explicit-uv.toml"
    explicit.write_text(f'find-links = ["{org.as_posix()}"]\n', "utf-8")
    monkeypatch.setenv("UV_CONFIG_FILE", str(explicit))
    agent = lab["home"] / ".aelix" / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    _wheel(lab["root"] / "above", "ABOVE-AGENT-DIR", "9.7")
    _place(lab["home"], ANCESTOR_SHAPES["project+tool-uv"], lab["root"] / "above", ".aelix/agent")
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


# === review rounds 3 and 5: UV_CONFIG_FILE, the one variable checked ============

#: ``UV_CONFIG_FILE`` values a catalog install refuses (uv backend): uv reads the
#: variable exactly as written, from the directory it runs in (measured, uv 0.11.19
#: and 0.12.23, ADR-0255 §15) — a relative name opens a file in the installer
#: directory (``uv.toml`` there is aelix's empty sentinel: the org pin is dropped
#: without a word), ``file:`` and ``~`` are relative to uv, and review round 5 (Codex):
#: ``' /abs'`` with a leading space is relative too — round 4 stripped it and let it
#: through, and uv read a decoy at ``installer-cwd/' /abs'``. A trailing space, an
#: empty value and spaces alone are refused as well (uv fails on each, loudly).
_UV_CONFIG_FILE_REFUSED = [
    "uv.toml",
    "./uv.toml",
    "pyproject.toml",
    "file:pin.toml",
    "file:///srv/pin.toml",
    "~/uv.toml",
    " @ABS@/uv.toml",
    "@ABS@/uv.toml ",
    "\t@ABS@/uv.toml",
    "",
    "   ",
]


@pytest.mark.parametrize("source", ["local-ext", "./local-dir"])
@pytest.mark.parametrize("verb", ["discover-install", "update"])
@pytest.mark.parametrize("value", _UV_CONFIG_FILE_REFUSED)
async def test_a_uv_config_file_that_is_not_a_bare_absolute_path_is_refused(
    lab: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verb: str,
    value: str,
    source: str,
) -> None:
    """Refused (2) before anything runs, for every catalog source form (a path entry
    too — claude cross r4 M2), the message naming the variable and asking for an
    absolute path, no URL."""

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, source)
    monkeypatch.setenv("UV_CONFIG_FILE", value.replace("@ABS@", lab["repo"].as_posix()))
    capsys.readouterr()

    args = (
        ["discover", "install", "probe", "--yes", "--no-verify"]
        if verb == "discover-install"
        else [
            "update",
            "local-ext" if source == "local-ext" else "local-dir",
            "--yes",
            "--no-verify",
        ]
    )
    code = await run_extension_command_async(args, settings=mem)

    assert code == 2
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "Error: UV_CONFIG_FILE (" in err
    assert "Set UV_CONFIG_FILE to an absolute path." in err
    assert "or a URL" not in err


async def test_an_absolute_uv_config_file_is_not_refused(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    monkeypatch.setenv("UV_CONFIG_FILE", str(lab["repo"] / "uv.toml"))

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert len(rec.calls) == 1


#: ``@ABS@`` is a NATIVE absolute path (round 5b): a POSIX literal like ``/srv/pin.toml``
#: has no drive, so the Windows rule refuses it and the row failed on win32.
@pytest.mark.parametrize(
    ("backend", "value", "refused"),
    [
        ("uv", "@ABS@", False),
        ("uv", " @ABS@", True),
        ("uv", "@ABS@ ", True),
        ("uv", "@ABS@\n", True),
        ("uv", "~/pin.toml", True),
        ("uv", "file:///srv/pin.toml", True),
        ("uv", "", True),
        ("uv", None, False),
        # pip never runs uv: the variable means nothing to it
        ("pip", "uv.toml", False),
        ("pip", " @ABS@", False),
    ],
)
def test_the_uv_config_file_rule_reads_the_value_exactly_as_uv_does(
    tmp_path: Path, backend: str, value: str | None, refused: bool
) -> None:
    chosen = (
        ei.InstallBackend(name="uv", uv_path=sys.executable) if backend == "uv" else ei.PIP_BACKEND
    )
    native = str(tmp_path / "pin.toml")
    env = {} if value is None else {"UV_CONFIG_FILE": value.replace("@ABS@", native)}
    got = ei._uv_config_file_refusal(chosen, env)
    assert (got is not None) is refused, got


@pytest.mark.parametrize(
    ("value", "refused"),
    [
        ("C:\\srv\\pin.toml", False),
        ("C:/srv/pin.toml", False),
        ("\\\\host\\share\\pin.toml", False),
        ("\\srv\\pin.toml", True),  # rooted, no drive: the CURRENT drive's \srv
        ("C:pin.toml", True),  # drive-relative
        (" C:\\srv\\pin.toml", True),
        ("C:\\srv\\pin.toml ", True),
    ],
)
def test_on_windows_the_uv_config_file_rule_needs_a_drive_or_a_share(
    monkeypatch: pytest.MonkeyPatch, value: str, refused: bool
) -> None:
    uv = ei.InstallBackend(name="uv", uv_path=sys.executable)  # absolute on this host
    monkeypatch.setattr(ei.sys, "platform", "win32")
    assert (ei._uv_config_file_refusal(uv, {"UV_CONFIG_FILE": value}) is not None) is refused


#: Round 5 (owner decision, "narrow and finish"): the user's own environment is inside
#: the trust boundary — a repository cannot set it — and reaches uv or pip exactly as
#: set. Round 4 refused each of these (rc 2); verify r4 measured two as regressions
#: against 61f03b67 (a named ``UV_DEFAULT_INDEX``, which uv's per-index credential
#: variables need; ``PIP_FIND_LINKS=~/x``, which pip expands), Codex r4 a third (a
#: relative ``UV_WORKING_DIRECTORY`` that uv ignores beside ``UV_WORKING_DIR``). A
#: RELATIVE path among them is read from the installer directory now (the known limit
#: ADR-0255 §12 records).
_USER_ENV_PASSES = [
    ("uv", {"UV_DEFAULT_INDEX": "corp=file:///srv/simple"}),
    ("uv", {"UV_DEFAULT_INDEX": "corp=https://nexus.example.invalid/simple"}),
    ("uv", {"UV_INDEX": "team-a=./simple"}),
    ("uv", {"UV_INDEX_URL": "./simple"}),
    ("uv", {"UV_FIND_LINKS": "./w"}),
    ("uv", {"UV_PROJECT": "repo"}),
    ("uv", {"UV_WORKING_DIR": "@ABS@", "UV_WORKING_DIRECTORY": "./ignored"}),
    ("uv", {"UV_EXCLUDE": "x.txt"}),
    ("uv", {"SSL_CERT_FILE": "ca.pem"}),
    ("uv", {"PIP_INDEX_URL": "./simple"}),
    ("pip", {"PIP_FIND_LINKS": "~/wheels"}),
    ("pip", {"PIP_FIND_LINKS": "file:wheels"}),
    ("pip", {"PIP_CONFIG_FILE": "pip.conf"}),
    ("pip", {"PIP_TARGET": "tgt"}),
    ("pip", {"PIP_CERT": "~/ca.pem"}),
]


@pytest.mark.parametrize("verb", ["discover-install", "update"])
@pytest.mark.parametrize(("backend", "env"), _USER_ENV_PASSES)
async def test_the_users_own_installer_environment_is_not_policed(
    lab: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    backend: str,
    env: dict[str, str],
) -> None:
    _use_backend(monkeypatch, lab, backend)
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    for name, value in env.items():
        monkeypatch.setenv(name, value.replace("@ABS@", lab["repo"].as_posix()))

    args = (
        ["discover", "install", "probe", "--yes", "--no-verify"]
        if verb == "discover-install"
        else ["update", "local-ext", "--yes", "--no-verify"]
    )
    code = await run_extension_command_async(args, settings=mem)

    assert code == 0
    assert len(rec.calls) == 1
    assert _same_dir(rec.calls[0][1], lab["neutral"])
    for name, value in env.items():  # handed on untouched
        assert os.environ[name] == value.replace("@ABS@", lab["repo"].as_posix())


async def test_a_typed_install_keeps_a_relative_value_it_runs_in_the_cwd(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typed install runs in the cwd, where the relative value means what it says."""

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    monkeypatch.setenv("UV_CONFIG_FILE", "uv.toml")
    mem = SettingsManager.in_memory({})

    code = await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert rec.calls[0][1] is None


def test_a_relative_uv_config_file_is_read_from_the_childs_cwd(tmp_path: Path) -> None:
    """uv opens a relative ``UV_CONFIG_FILE`` in ITS working directory — in the
    installer directory, aelix's empty ``uv.toml`` (measured, ``uv -v``: "Using
    configuration file: uv.toml"). The round-2 model returned the bare name, which
    this process then opened in the user's cwd. (A catalog install now refuses a
    relative value before this matters; a typed install's child shares this cwd.)"""

    user = tmp_path / "user"
    user.mkdir()
    neutral = ei.catalog_installer_cwd(str(tmp_path / "agent"))
    for value in ("uv.toml", os.path.join(".", "uv.toml")):
        env = {"UV_CONFIG_FILE": value}
        assert ei._uv_config_files(env, cwd=neutral) == [os.path.join(neutral, value)]
        assert ei._uv_config_files(env, cwd=str(user)) == [os.path.join(str(user), value)]
    absolute = str(user / "pinned.toml")
    assert ei._uv_config_files({"UV_CONFIG_FILE": absolute}, cwd=neutral) == [absolute]


def test_a_system_uv_index_suppresses_the_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv reads the system ``uv.toml`` from any cwd: an index there is uv's own, so
    pip's ambient index is not translated over it (a model that dropped the system
    file survived round 2)."""

    system = tmp_path / "system" / "uv" / "uv.toml"
    system.parent.mkdir(parents=True)
    system.write_text('index-url = "https://org.example.invalid/simple"\n', "utf-8")
    monkeypatch.setattr(ei, "_uv_system_config_file", lambda env: str(system))
    monkeypatch.setattr(ei, "_pip_config_candidates", lambda env=None: [])
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    sandbox_home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "xdg"))
    monkeypatch.setenv("PIP_INDEX_URL", "https://pip.example.invalid/")
    neutral = ei.catalog_installer_cwd(str(tmp_path / "agent"))
    backend = ei.InstallBackend(name="uv", uv_path=str(tmp_path / "uv"))

    assert ei._uv_config_files(cwd=neutral)[-1] == str(system)
    assert ei._uv_has_own_index_config(cwd=neutral) is True
    assert ei.uv_ambient_index_env(backend, "pypi", cwd=neutral) == {}
    monkeypatch.setattr(ei, "_uv_system_config_file", lambda env: None)  # the control
    assert ei.uv_ambient_index_env(backend, "pypi", cwd=neutral) == {
        "UV_INDEX_URL": "https://pip.example.invalid/"
    }


@pytest.mark.skipif(sys.platform == "win32", reason="XDG_CONFIG_DIRS is not read on Windows")
def test_an_empty_xdg_config_dirs_entry_is_skipped_as_uv_0_12_does(tmp_path: Path) -> None:
    """uv 0.12 (CI's pin) skips an empty entry; uv 0.11 stopped at the first one
    (measured: ``none::sys`` — 0.12.23 reads sys's file, 0.11.19 reads none)."""

    (tmp_path / "sys" / "uv").mkdir(parents=True)
    (tmp_path / "sys" / "uv" / "uv.toml").write_text("", "utf-8")
    want = os.path.join(str(tmp_path / "sys"), "uv", "uv.toml")
    for dirs in (f"{tmp_path / 'none'}::{tmp_path / 'sys'}", f":{tmp_path / 'sys'}"):
        assert ei._uv_system_config_file({"XDG_CONFIG_DIRS": dirs}) == want


class _CwdRunner:
    """An injected runner that follows the protocol: records the ``cwd`` it is given."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(
        self, argv: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((list(argv), cwd))
        return subprocess.CompletedProcess(args=argv, returncode=0)


@pytest.mark.parametrize(
    "args",
    [
        ["discover", "install", "probe", "--yes", "--no-verify"],
        ["update", "--yes", "--no-verify"],
        ["update", "local-ext", "--yes", "--no-verify"],
    ],
)
async def test_an_injected_runner_is_given_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """``run_extension_command_async(..., runner=...)`` (Codex round 2): the runner
    received argv only and its child ran in the cwd. Now it is called with
    ``cwd=<installer-cwd>``."""

    mem = await _catalog(lab, "local-ext")
    runner = _CwdRunner()
    if args[0] == "update" and len(args) == 3:  # every record: install one first
        assert (
            await run_extension_command_async(
                ["discover", "install", "probe", "--yes", "--no-verify"],
                settings=mem,
                runner=runner,
            )
            == 0
        )
        runner.calls.clear()

    code = await run_extension_command_async(args, settings=mem, runner=runner)

    assert code == 0
    assert runner.calls
    for _argv, cwd in runner.calls:
        assert _same_dir(cwd, lab["neutral"]), cwd


async def test_the_default_runner_injected_runs_its_child_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex's exact input: ``runner=ei._default_runner``. A cwd ``pip/`` package
    prints where it runs; it must not run at all (the child starts elsewhere)."""

    (lab["repo"] / "pip").mkdir()
    (lab["repo"] / "pip" / "__init__.py").write_text("", "utf-8")
    marker = lab["root"] / "pip-ran-in.txt"
    (lab["repo"] / "pip" / "__main__.py").write_text(
        f"import os, pathlib\npathlib.Path({str(marker)!r}).write_text(os.getcwd())\n", "utf-8"
    )
    seen: list[str | None] = []

    def runner(argv: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
        seen.append(cwd)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    monkeypatch.setattr(ei, "subprocess", _SubprocessSpy(seen))
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"],
        settings=mem,
        runner=ei._default_runner,
    )

    assert code == 0
    assert len(seen) == 1 and _same_dir(seen[0], lab["neutral"]), seen
    assert not marker.exists()


class _SubprocessSpy:
    """Stands in for the ``subprocess`` module inside ``extension_install``: the
    real ``_default_runner`` calls ``subprocess.run(argv, check=False, cwd=cwd)``."""

    CompletedProcess = subprocess.CompletedProcess
    TimeoutExpired = subprocess.TimeoutExpired
    SubprocessError = subprocess.SubprocessError

    def __init__(self, seen: list[str | None]) -> None:
        self._seen = seen

    def run(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        cwd = kwargs.get("cwd")
        self._seen.append(None if cwd is None else str(cwd))
        return subprocess.CompletedProcess(args=argv, returncode=0)


async def test_an_injected_runner_that_takes_no_cwd_is_refused_for_a_catalog_install(
    lab: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    mem = await _catalog(lab, "local-ext")
    ran: list[list[str]] = []

    def argv_only(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        ran.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    capsys.readouterr()
    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem, runner=argv_only
    )

    assert code == 2
    assert ran == []
    assert "the installer runner takes no cwd= argument" in capsys.readouterr().err


async def test_an_argv_only_runner_still_drives_a_typed_install(lab: dict[str, Path]) -> None:
    ran: list[list[str]] = []

    def argv_only(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        ran.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    code = await run_extension_command_async(
        ["install", "local-ext", "--yes", "--no-verify"],
        settings=SettingsManager.in_memory({}),
        runner=argv_only,
    )

    assert code == 0
    assert len(ran) == 1


async def test_an_injected_runner_gets_the_directory_for_the_verify_download_too(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    mem = await _catalog(lab, "local-ext")
    runner = _CwdRunner()

    await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--verify-pypi"], settings=mem, runner=runner
    )

    assert runner.calls and "download" in runner.calls[0][0]
    assert _same_dir(runner.calls[0][1], lab["neutral"])


@pytest.mark.parametrize(
    ("given", "anchored"),
    [
        ("file:/srv/simple", False),
        ("file:///srv/simple", False),
        ("file://localhost/srv/simple", False),
        ("https://pypi.example.invalid/simple", False),
        ("git+file:///srv/x", False),
        ("./simple", True),
        ("simple", True),
        ("C:\\idx\\simple", sys.platform != "win32"),  # a drive path, never a scheme
        ("C:/idx/simple", sys.platform != "win32"),
    ],
)
def test_an_index_url_with_a_scheme_is_never_anchored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, given: str, anchored: bool
) -> None:
    """Any ``scheme ":"`` (RFC 3986) is a URL — ``file:/abs`` was anchored as a path
    in round 2 (Codex: a valid single-slash file URI broke catalog installs). A
    one-letter "scheme" is a Windows drive: a path, absolute on Windows only."""

    monkeypatch.chdir(tmp_path)

    got = ei._anchor_typed_index_url(given)

    assert got == (os.path.abspath(given) if anchored else given)


@pytest.mark.skipif(
    sys.platform == "win32", reason="a file:/ URI for a drive path is not measured here"
)
@pytest.mark.parametrize("form", ["file:/", "file:///", "file://localhost/"])
async def test_real_uv_a_file_index_url_works_on_a_catalog_install(
    real_uv_lab: dict[str, Path], form: str
) -> None:
    """Round 2 anchored ``file:/abs`` under the cwd and the catalog install found
    nothing (rc 1, Codex); every file URI form reaches uv unchanged."""

    lab = real_uv_lab
    org = _drop_user_pin(lab)
    wheel = next(org.glob("local_ext-*.whl"))
    simple = lab["root"] / "simple"
    (simple / "local-ext").mkdir(parents=True)
    (simple / "local-ext" / "index.html").write_text(
        f'<a href="{wheel.as_uri()}">{wheel.name}</a>\n', "utf-8"
    )
    index = form + simple.as_posix().lstrip("/")
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify", "--index-url", index],
        settings=mem,
    )

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


def _uv_minor(uv: str) -> tuple[int, int]:
    out = subprocess.run([uv, "--version"], capture_output=True, check=False).stdout
    parts = out.decode("ascii", "replace").split()[1].split(".")
    return int(parts[0]), int(parts[1])


@pytest.mark.skipif(sys.platform == "win32", reason="XDG_CONFIG_DIRS is not read on Windows")
async def test_real_uv_an_empty_xdg_config_dirs_entry_is_skipped(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model's 0.12 reading, against the real uv: the system pin past an empty
    entry applies. uv 0.11 stopped at the empty entry (ADR-0255 §15) — skipped there."""

    if _uv_minor(_real_uv()) < (0, 12):
        pytest.skip("uv < 0.12 stops at the first empty XDG_CONFIG_DIRS entry")
    lab = real_uv_lab
    org = _drop_user_pin(lab)
    system = lab["root"] / "system-config"
    (system / "uv").mkdir(parents=True)
    (system / "uv" / "uv.toml").write_text(f'find-links = ["{org.as_posix()}"]\n', "utf-8")
    monkeypatch.setenv("XDG_CONFIG_DIRS", f"{lab['root'] / 'none'}::{system}")
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


# === review round 4 ==========================================================


@pytest.mark.parametrize(
    ("value", "absolute"),
    [
        ("C:\\srv\\pin.toml", True),
        ("C:/srv/pin.toml", True),
        ("\\\\host\\share\\pin.toml", True),
        ("\\srv\\pin.toml", False),  # rooted, no drive: the CURRENT drive's \srv
        ("C:pin.toml", False),  # drive-relative
        ("file:pin.toml", False),
        ("~\\pin.toml", False),
    ],
)
def test_on_windows_an_absolute_path_needs_a_drive_or_a_share(
    monkeypatch: pytest.MonkeyPatch, value: str, absolute: bool
) -> None:
    monkeypatch.setattr(ei.sys, "platform", "win32")
    assert ei._is_absolute_path(value) is absolute


async def test_a_scheme_looking_uv_config_file_is_refused_not_read_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex round 3's exact input, through the CLI: a ``file:pin.toml`` in the
    installer directory is never consulted — the install and the update are refused."""

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    monkeypatch.setenv("UV_CONFIG_FILE", "file:pin.toml")
    capsys.readouterr()

    for args in (
        ["discover", "install", "probe", "--yes", "--no-verify"],
        ["update", "local-ext", "--yes", "--no-verify"],
    ):
        assert await run_extension_command_async(args, settings=mem) == 2
    assert rec.calls == []
    err = capsys.readouterr().err
    assert "Set UV_CONFIG_FILE to an absolute path." in err
    assert "or a URL" not in err


def test_the_api_sweep_types_carry_the_catalog_origin(lab: dict[str, Path]) -> None:
    """What the resolver returns carries its origin; a typed string does not."""

    from aelix_coding_agent.cli import extension_catalog as ec

    cat = str(lab["catdir"] / "catalog.json")
    by_form = {
        "local-ext": ec.CatalogSpec,
        "https://files.example.invalid/local_ext-1.0-py3-none-any.whl": ec.CatalogSpec,
        "./local-dir": ec.ResolvedPath,
    }
    for source, kind in by_form.items():
        entry = ec.CatalogEntry(name="probe", source=source, catalog_location=cat)
        target = ec.resolve_entry_target(entry)
        assert type(target) is kind, (source, type(target))
        assert ei._from_catalog(target)
    assert not ei._from_catalog("local-ext")
    assert str(ec.CatalogSpec("local-ext")) == "local-ext"
    assert type(str(ec.CatalogSpec("local-ext"))) is str


@pytest.mark.parametrize("source", ["local-ext", "./local-dir"])
@pytest.mark.parametrize("neutral_kw", [False, True])
def test_the_python_api_runs_a_resolved_catalog_target_in_the_installer_directory(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, source: str, neutral_kw: bool
) -> None:
    """Codex round 3: ``install_extension(resolve_entry_target(entry))`` ran in the
    caller's cwd (``neutral_cwd`` defaulted to False). The cwd now follows the
    target's origin — there is no flag left for a caller to forget or pass."""

    from aelix_coding_agent.cli import extension_catalog as ec

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    entry = ec.CatalogEntry(
        name="probe", source=source, catalog_location=str(lab["catdir"] / "catalog.json")
    )
    target = ec.resolve_entry_target(entry)
    kwargs: dict[str, object] = {"neutral_cwd": False} if neutral_kw else {}

    if neutral_kw:  # the round-3 keyword is gone: a caller cannot turn the rule off
        with pytest.raises(TypeError):
            ei.install_extension(target, yes=True, no_verify=True, **kwargs)  # type: ignore[arg-type]
        return
    code = ei.install_extension(target, yes=True, no_verify=True)

    assert code == 0
    assert len(rec.calls) == 1
    assert _same_dir(rec.calls[0][1], lab["neutral"]), rec.calls[0][1]

    rec.calls.clear()  # a typed string from the same API: the caller's cwd
    assert ei.install_extension("local-ext", yes=True, no_verify=True) == 0
    assert rec.calls[0][1] is None


def test_verify_and_pin_downloads_a_catalog_target_in_the_installer_directory(
    lab: dict[str, Path],
) -> None:
    """The other public entry point that starts an installer child: ``verify_and_pin``'s
    ``pip download``. Called directly with a catalog target and a runner, the runner
    is given the directory; an argv-only runner is refused, as in ``install_extension``."""

    from aelix_coding_agent.cli import extension_catalog as ec
    from aelix_coding_agent.cli import extension_pins

    agent = str(lab["root"] / "agent")
    runner = _CwdRunner()
    common: dict[str, object] = {
        "strict": False,
        "repin": False,
        "verify_pypi": True,
        "index_url": None,
        "extra_index_urls": None,
        "agent_dir": agent,
    }
    ei.verify_and_pin(ec.CatalogSpec("local-ext"), "pypi", ["x"], runner=runner, **common)  # type: ignore[arg-type]
    assert runner.calls and "download" in runner.calls[0][0]
    assert _same_dir(runner.calls[0][1], lab["neutral"])

    runner.calls.clear()  # typed: the process cwd, as before
    ei.verify_and_pin("local-ext", "pypi", ["x"], runner=runner, **common)  # type: ignore[arg-type]
    assert runner.calls and runner.calls[0][1] is None

    ran: list[list[str]] = []

    def argv_only(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        ran.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    with pytest.raises(extension_pins.VerifyRefusal, match="takes no cwd= argument"):
        ei.verify_and_pin(ec.CatalogSpec("local-ext"), "pypi", ["x"], runner=argv_only, **common)  # type: ignore[arg-type]
    assert ran == []


def _wheel_requiring(directory: Path, name: str, requires: str) -> Path:
    """A wheel ``name`` 1.0 (ORIGIN TRUSTED-TOPLEVEL) that requires ``requires``."""

    directory.mkdir(parents=True, exist_ok=True)
    dist = name.replace("-", "_")
    path = directory / f"{dist}-1.0-py3-none-any.whl"
    info = f"{dist}-1.0.dist-info"
    files = {
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\nRequires-Dist: {requires}\n"
        ),
        f"{info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: t392\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        f"{dist}.py": "ORIGIN = 'TRUSTED-TOPLEVEL'\n",
    }
    files[f"{info}/RECORD"] = "".join(f"{p},,\n" for p in [*files, f"{info}/RECORD"])
    with zipfile.ZipFile(path, "w") as zf:
        for member, text in files.items():
            zf.writestr(member, text)
    return path


async def test_real_uv_the_python_api_installs_a_catalog_wheels_dependency_from_the_org_pin(
    real_uv_lab: dict[str, Path],
) -> None:
    """Codex round 3's ``catalog_python_api.py`` shape: a catalog wheel requires
    ``local-ext``; the hostile cwd's ``[tool.uv]`` offers 9.9 (CWD-DECOY). Through the
    Python API the dependency came from the cwd (round 3: CWD-DECOY); now ORG-PIN."""

    from aelix_coding_agent.cli import extension_catalog as ec

    lab = real_uv_lab
    wheel = _wheel_requiring(lab["catdir"], "trusted-ext", "local-ext")
    entry = ec.CatalogEntry(
        name="probe",
        source=f"./{wheel.name}",
        catalog_location=str(lab["catdir"] / "catalog.json"),
    )

    code = ei.install_extension(ec.resolve_entry_target(entry), yes=True, no_verify=True)

    assert code == 0
    assert _installed_origin(lab) == "ORG-PIN"


def _simple_index(directory: Path, origin: str, version: str, *, empty: bool = False) -> str:
    """A PEP 503 file index offering ``local-ext`` (or nothing); its ``file://`` URL."""

    package = directory / "local-ext"
    package.mkdir(parents=True, exist_ok=True)
    if empty:
        (package / "index.html").write_text("", encoding="utf-8")
    else:
        artifact = _wheel(package, origin, version)
        (package / "index.html").write_text(
            f'<a href="{artifact.name}">{artifact.name}</a>\n', encoding="utf-8"
        )
    return directory.as_uri()


@pytest.mark.parametrize("stale", ["offers-9.9", "empty"])
async def test_real_uv_a_uv_project_index_still_beats_a_stale_pip_index(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, stale: str
) -> None:
    """Codex round 3 (``project_env_pin_regression.py``), a regression against
    61f03b67: a project chosen with ``UV_PROJECT`` whose ``[tool.uv]`` pins the index
    is read by the uv child from the installer directory too, but the model ignored
    ``UV_PROJECT``, so the pip index was translated over it — round 3 installed
    STALE-PIP-PIN (or failed, rc 1). 61f03b67 and now: UV-PROJECT-ORG."""

    lab = real_uv_lab
    _drop_user_pin(lab)
    org = _simple_index(lab["root"] / "org-simple", "UV-PROJECT-ORG", "1.0")
    pip_index = _simple_index(
        lab["root"] / "pip-simple", "STALE-PIP-PIN", "9.9", empty=stale == "empty"
    )
    project = lab["root"] / "owner-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "owner-project"\nversion = "0"\n[tool.uv]\nindex-url = "{org}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("UV_PROJECT", str(project))
    monkeypatch.setenv("PIP_INDEX_URL", pip_index)
    mem = await _catalog(lab, "local-ext")

    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
    )

    assert code == 0
    assert _installed_origin(lab) == "UV-PROJECT-ORG"


async def test_a_uv_project_or_working_dir_index_suppresses_the_translation(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorder view of the same rule, both variables."""

    project = lab["root"] / "owner-project"
    project.mkdir()
    (project / "uv.toml").write_text('index-url = "https://org.example.invalid/simple"\n', "utf-8")
    monkeypatch.setenv("PIP_INDEX_URL", "https://stale.example.invalid/simple")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    args = ["discover", "install", "probe", "--yes", "--no-verify"]

    await run_extension_command_async(args, settings=mem)
    assert rec.calls[-1][2].get("UV_INDEX_URL") == "https://stale.example.invalid/simple"
    for var in ("UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY"):
        monkeypatch.setenv(var, str(project))
        await run_extension_command_async(args, settings=mem)
        assert "UV_INDEX_URL" not in rec.calls[-1][2], var
        monkeypatch.delenv(var)


#: ``(env, the directory whose config uv reads)`` — every pick measured against the
#: real uv 0.11.19 and 0.12.23 (``.omc/probes/392-live/r4/uv-project-env-after.txt``).
#: ``@R@`` is the row root; uv starts in ``@R@/agent/installer-cwd``.
_PROJECT_ENV_LAYOUTS: dict[str, tuple[dict[str, str], dict[str, str], str]] = {
    "project-dir": ({"repo/pyproject.toml": _PROJ + _TL}, {"UV_PROJECT": "@R@/repo"}, "repo"),
    "project-pyproject-file": (
        {"repo/pyproject.toml": _PROJ + _TL},
        {"UV_PROJECT": "@R@/repo/pyproject.toml"},
        "repo",
    ),
    "project-dir-uv-toml-only": ({"repo/uv.toml": _FL}, {"UV_PROJECT": "@R@/repo"}, "repo"),
    "project-sub-of-tool-uv": (
        {"repo/pyproject.toml": _TL, "repo/sub/.keep": ""},
        {"UV_PROJECT": "@R@/repo/sub"},
        "repo",
    ),
    "working-dir": ({"repo/pyproject.toml": _PROJ + _TL}, {"UV_WORKING_DIR": "@R@/repo"}, "repo"),
    "working-directory": (
        {"repo/pyproject.toml": _PROJ + _TL},
        {"UV_WORKING_DIRECTORY": "@R@/repo"},
        "repo",
    ),
    "working-dir-wins-over-working-directory": (
        {"a/uv.toml": _FL, "b/uv.toml": _FL},
        {"UV_WORKING_DIR": "@R@/a", "UV_WORKING_DIRECTORY": "@R@/b"},
        "a",
    ),
    "project-lexical-dotdot": (
        {"x/uv.toml": _FL, "far/uv.toml": _FL, "far/deep/.keep": "", "x/link@link": "far/deep"},
        {"UV_PROJECT": "@R@/x/link/.."},
        "x",
    ),
    "config-file-relative-to-working-dir": (
        {"wd/pin.toml": _FL},
        {"UV_WORKING_DIR": "@R@/wd", "UV_CONFIG_FILE": "pin.toml"},
        "wd",
    ),
    "project-relative-to-working-dir": (
        {
            "wd/pyproject.toml": "# no project\n",
            "wd/uv.toml": "",
            "wd/proj/pyproject.toml": _PROJ + _TL,
        },
        {"UV_WORKING_DIR": "@R@/wd", "UV_PROJECT": "proj"},
        "wd/proj",
    ),
}


def _project_env_row(root: Path, name: str) -> tuple[dict[str, str], Path, dict[str, Path]]:
    files, env, _want = _PROJECT_ENV_LAYOUTS[name]
    wheels: dict[str, Path] = {}
    for rel, text in files.items():
        if rel.endswith("@link"):  # a directory symlink: ``text`` is its target
            link = root / rel.removesuffix("@link")
            link.parent.mkdir(parents=True, exist_ok=True)
            _symlink_or_skip(link, root / text, directory=True)
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        owner = str(Path(rel).parent.as_posix())
        if "@W@" in text:
            wheels[owner] = root / "wheels" / owner.replace("/", "_")
            _wheel(wheels[owner], owner, f"1.{len(wheels)}")
            text = text.replace("@W@", wheels[owner].as_posix())
        path.write_text(text, encoding="utf-8")
    neutral = Path(ei.catalog_installer_cwd(str(root / "agent")))
    return {k: v.replace("@R@", str(root)) for k, v in env.items()}, neutral, wheels


def _model_pick(env: Mapping[str, str], neutral: Path) -> str | None:
    """The directory of the first file in the model's list that pins find-links."""

    for path in ei._uv_config_files(dict(env), cwd=str(neutral)):
        links = ei._read_uv_config_table(path).get("find-links")
        if isinstance(links, list) and links:
            return Path(str(links[0])).name
    return None


@pytest.mark.parametrize("layout", list(_PROJECT_ENV_LAYOUTS))
def test_the_model_follows_uv_project_and_working_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    for var in ("UV_PROJECT", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_CONFIG_FILE"):
        monkeypatch.delenv(var, raising=False)
    root = tmp_path.resolve()
    env, neutral, _wheels = _project_env_row(root, layout)
    want = _PROJECT_ENV_LAYOUTS[layout][2].replace("/", "_")
    base = {"XDG_CONFIG_HOME": str(root / "xdg"), "XDG_CONFIG_DIRS": str(root / "nosys")}

    assert _model_pick({**base, **env}, neutral) == want


@pytest.mark.parametrize("layout", list(_PROJECT_ENV_LAYOUTS))
def test_real_uv_reads_the_project_the_model_names_under_uv_project_and_working_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    uv = _real_uv()
    root = tmp_path.resolve()
    python = _throwaway_venv(uv, root)
    env, neutral, wheels = _project_env_row(root, layout)
    want = _PROJECT_ENV_LAYOUTS[layout][2]
    child = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("UV_", "PIP_", "VIRTUAL_ENV"))},
        "XDG_CONFIG_HOME": str(root / "xdg"),
        "XDG_CONFIG_DIRS": str(root / "nosys"),
        "APPDATA": str(root / "appdata"),
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": str(root / "uv-cache"),
        "UV_PYTHON_DOWNLOADS": "never",
        **env,
    }

    pick = _uv_dry_run_pick(uv, python, neutral, child)

    version = next(p.name for p in wheels[want].iterdir()).split("-")[1]
    assert pick == f"local-ext=={version}", (pick, layout)
    assert _model_pick(child, neutral) == want.replace("/", "_")


@pytest.mark.skipif(sys.platform == "win32", reason="'file:pin.toml' is an NTFS stream name")
async def test_real_uv_a_scheme_looking_uv_config_file_never_reaches_the_installer_directory(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round 3's ``relative_config_scheme.py``: a decoy ``file:pin.toml`` in the
    installer directory was read (INSTALLER-CONFIG-DECOY, rc 0). Now refused (2) and
    nothing installed."""

    lab = real_uv_lab
    decoy = lab["root"] / "decoy"
    _wheel(decoy, "INSTALLER-CONFIG-DECOY", "9.9")
    lab["neutral"].mkdir(parents=True)
    (lab["neutral"] / "file:pin.toml").write_text(
        f'find-links = ["{decoy.as_posix()}"]\n', encoding="utf-8"
    )
    mem = await _catalog(lab, "local-ext")
    monkeypatch.setenv("UV_CONFIG_FILE", "file:pin.toml")

    for args in (
        ["discover", "install", "probe", "--yes", "--no-verify"],
        ["update", "local-ext", "--yes", "--no-verify"],
    ):
        assert await run_extension_command_async(args, settings=mem) == 2
        assert _installed_origin(lab) == "nothing"


def test_a_hard_linked_sentinel_is_replaced_and_its_other_name_unchanged(tmp_path: Path) -> None:
    """Verify M11: rewriting a sentinel in place (``O_TRUNC``) writes through a hard
    link — the user's file sharing its inode would be overwritten. aelix writes a new
    file (``O_EXCL``) and renames it over the name (review round 5)."""

    agent = tmp_path / "agent"
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    neutral.mkdir(parents=True)
    users = tmp_path / "users-pyproject.toml"
    users.write_text('[project]\nname = "mine"\nversion = "1"\n', encoding="utf-8")
    try:
        os.link(users, neutral / "pyproject.toml")
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem cannot make a hard link")

    ei.catalog_installer_cwd(str(agent))

    assert users.read_text(encoding="utf-8") == '[project]\nname = "mine"\nversion = "1"\n'
    assert (neutral / "pyproject.toml").read_bytes() == (
        ei.INSTALLER_CWD_PYPROJECT_TOML.encode("utf-8")
    )
    assert not os.path.samefile(users, neutral / "pyproject.toml")


@pytest.mark.parametrize("name", ["uv.toml", "pyproject.toml"])
def test_sentinel_text_with_more_after_it_is_replaced(tmp_path: Path, name: str) -> None:
    """Verify M12: comparing only the first ``len(sentinel)`` bytes kept a file that
    holds aelix's text AND a ``find-links`` line after it — uv read the line (measured
    under the mutant: CWD-DECOY installed)."""

    agent = tmp_path / "agent"
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    neutral.mkdir(parents=True)
    text = ei._INSTALLER_CWD_FILES[name]
    extra = (
        'find-links = ["/srv/decoy"]\n'
        if name == "uv.toml"
        else '[tool.uv]\nfind-links = ["/srv/decoy"]\n'
    )
    (neutral / name).write_bytes((text + extra).encode("utf-8"))

    ei.catalog_installer_cwd(str(agent))

    assert (neutral / name).read_bytes() == text.encode("utf-8")


async def test_a_runner_whose_cwd_is_keyword_only_is_accepted(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify M33: ``runner(argv, *, cwd=None)`` is exactly the ``PipRunner`` shape and
    must be given the directory, not refused."""

    seen: list[str | None] = []

    def keyword_only(
        argv: list[str], *, cwd: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        seen.append(cwd)
        return subprocess.CompletedProcess(args=argv, returncode=0)

    mem = await _catalog(lab, "local-ext")
    code = await run_extension_command_async(
        ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem, runner=keyword_only
    )

    assert code == 0
    assert len(seen) == 1 and _same_dir(seen[0], lab["neutral"]), seen
    for shape, takes in (
        (keyword_only, True),
        (lambda argv, cwd=None: None, True),
        (lambda argv, **kw: None, True),
        (lambda argv: None, False),
        (lambda argv, *, where=None: None, False),
    ):
        assert ei._runner_takes_cwd(shape) is takes


# === review round 5 ==========================================================


def test_resolve_entry_source_keeps_the_catalog_origin(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r4 (``legacy_resolver.py``): ``resolve_entry_source`` returned a plain
    ``str``, which ``install_extension`` takes for a typed source — uv ran in the
    caller's cwd and installed the repository's 9.9 (CWD-DECOY). It is a
    :class:`CatalogSpec` for every form now, and its install runs in the installer
    directory."""

    from aelix_coding_agent.cli import extension_catalog as ec

    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    cat = str(lab["catdir"] / "catalog.json")
    for source, is_path in (
        ("local-ext", False),
        ("https://files.example.invalid/local_ext-1.0-py3-none-any.whl", False),
        ("./local-dir", True),
        ("./cat_wheel-1.0-py3-none-any.whl", True),
    ):
        entry = ec.CatalogEntry(name="probe", source=source, catalog_location=cat)
        spec, got_is_path = ec.resolve_entry_source(entry)
        assert type(spec) is ec.CatalogSpec, (source, type(spec))
        assert got_is_path is is_path
        assert spec == str(ec.resolve_entry_target(entry))  # still the string shown
        rec.calls.clear()
        assert ei.install_extension(spec, yes=True, no_verify=True) == 0
        assert len(rec.calls) == 1 and _same_dir(rec.calls[0][1], lab["neutral"]), (
            source,
            rec.calls,
        )


def test_verify_and_pin_anchors_a_relative_index_url_for_a_catalog_target(
    lab: dict[str, Path],
) -> None:
    """Verify r4 S24: the public ``verify_and_pin`` seam, called directly with a
    catalog target and a typed relative ``--index-url``, must anchor that URL at the
    caller's cwd itself — its download runs in the installer directory, where
    ``./simple`` names another directory. ``install_extension`` anchors first, so only
    a direct call shows it. A typed target keeps the value: it runs in the cwd."""

    from aelix_coding_agent.cli import extension_catalog as ec

    runner = _CwdRunner()
    common: dict[str, object] = {
        "strict": False,
        "repin": False,
        "verify_pypi": True,
        "extra_index_urls": None,
        "agent_dir": str(lab["root"] / "agent"),
        "runner": runner,
    }
    relative = os.path.join(".", "simple")
    ei.verify_and_pin(ec.CatalogSpec("local-ext"), "pypi", ["x"], index_url=relative, **common)  # type: ignore[arg-type]
    argv, cwd = runner.calls[0]
    assert _same_dir(cwd, lab["neutral"])
    assert os.path.abspath(relative) in argv, argv
    assert relative not in argv

    runner.calls.clear()
    ei.verify_and_pin("local-ext", "pypi", ["x"], index_url=relative, **common)  # type: ignore[arg-type]
    argv, cwd = runner.calls[0]
    assert cwd is None
    assert relative in argv


@pytest.mark.skipif(not ei._INSTALLER_DIR_FD, reason="no O_NOFOLLOW / dir_fd on this platform")
def test_a_sentinel_swapped_for_a_link_after_the_check_is_never_read_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r4 mutant: ``_ensure_sentinel`` opening the file without ``O_NOFOLLOW``
    passed every row. Between ``lstat`` (a regular file) and ``open``, ``uv.toml`` is
    moved away and replaced by a symlink to it — same inode, same text, so without
    ``O_NOFOLLOW`` aelix accepts a sentinel that is a link the swapper controls. With
    it, ``open`` fails (``ELOOP``) and the directory is refused."""

    agent = tmp_path / "agent"
    neutral = Path(ei.catalog_installer_cwd(str(agent)))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real_open = os.open
    swapped: list[bool] = []

    def swap_then_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == "uv.toml" and not flags & os.O_CREAT and not swapped:
            swapped.append(True)
            os.rename(neutral / "uv.toml", elsewhere / "uv.toml")
            os.symlink(elsewhere / "uv.toml", neutral / "uv.toml")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_then_open)
    try:
        ei.catalog_installer_cwd(str(agent))
    except OSError:  # InstallerDirRefused (ELOOP) — _prepare_installer_cwd refuses on any
        pass
    else:
        pytest.fail("a sentinel that became a link was accepted")
    finally:
        monkeypatch.undo()
    assert swapped
    assert (neutral / "uv.toml").is_symlink()  # left as found: refused, not repaired


def test_a_name_surrogate_reparse_point_counts_as_a_link() -> None:
    """Claude cross r4 M1: no row reached the Windows junction branch. A junction's
    ``lstat`` is a DIRECTORY with tag ``IO_REPARSE_TAG_MOUNT_POINT`` (name surrogate);
    a cloud-file placeholder's tag has no surrogate bit and is not a link."""

    import types

    directory = stat_mod.S_IFDIR | 0o700
    junction = types.SimpleNamespace(st_mode=directory, st_reparse_tag=0xA0000003)
    win_symlink = types.SimpleNamespace(st_mode=directory, st_reparse_tag=0xA000000C)
    cloud = types.SimpleNamespace(st_mode=directory, st_reparse_tag=0x9000001A)
    plain = types.SimpleNamespace(st_mode=directory)
    assert ei._is_link_like(junction)  # type: ignore[arg-type]
    assert ei._is_link_like(win_symlink)  # type: ignore[arg-type]
    assert not ei._is_link_like(cloud)  # type: ignore[arg-type]
    assert not ei._is_link_like(plain)  # type: ignore[arg-type]


def test_an_installer_directory_that_lstat_reports_as_a_junction_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import types

    agent = tmp_path / "agent"
    target = os.path.abspath(agent / ei.INSTALLER_CWD_DIRNAME)
    real_lstat = os.lstat

    def junction_lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        st = real_lstat(path, *args, **kwargs)
        if os.fspath(path) == target:
            return types.SimpleNamespace(
                st_mode=st.st_mode, st_dev=st.st_dev, st_ino=st.st_ino, st_reparse_tag=0xA0000003
            )
        return st

    monkeypatch.setattr(os, "lstat", junction_lstat)
    with pytest.raises(ei.InstallerDirRefused):
        ei.catalog_installer_cwd(str(agent))


def _stale(agent: Path) -> None:
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    neutral.mkdir(parents=True)
    for name in ei._INSTALLER_CWD_FILES:
        (neutral / name).write_text("# what an older aelix wrote\n", encoding="utf-8")


def _assert_prepared(agent: Path) -> None:
    neutral = agent / ei.INSTALLER_CWD_DIRNAME
    assert sorted(p.name for p in neutral.iterdir()) == sorted(ei._INSTALLER_CWD_FILES)
    for name, text in ei._INSTALLER_CWD_FILES.items():
        assert (neutral / name).read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("state", ["first-use", "older-text"])
def test_threads_preparing_the_directory_at_once_are_never_refused(
    tmp_path: Path, state: str
) -> None:
    """Claude cross r4: unlink-then-create (``O_EXCL``) refused a concurrent first use
    (``FileExistsError``) or an upgrade's rewrite (``FileNotFoundError``) at random —
    measured 46 and 101 refusals in 120 process starts. Every sentinel is now written
    to a temporary file and renamed over the name; another writer's identical result
    is success."""

    import threading

    for attempt in range(25):
        agent = tmp_path / f"agent-{attempt}"
        if state == "older-text":
            _stale(agent)
        gate = threading.Barrier(8)
        errors: list[BaseException] = []

        def prepare(
            agent: Path = agent,
            gate: threading.Barrier = gate,
            errors: list[BaseException] = errors,
        ) -> None:
            gate.wait()
            try:
                ei.catalog_installer_cwd(str(agent))
            except BaseException as exc:  # noqa: BLE001 — collected for the assertion
                errors.append(exc)

        threads = [threading.Thread(target=prepare) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], (attempt, errors)
        _assert_prepared(agent)


_PREPARE_AT = """
import sys, time
from aelix_coding_agent.cli import extension_install as ei
start = float(sys.argv[2])
while time.time() < start:
    pass
try:
    ei.catalog_installer_cwd(sys.argv[1])
except OSError as exc:
    print("REFUSED", type(exc).__name__, exc)
    sys.exit(3)
"""


@pytest.mark.parametrize("state", ["first-use", "older-text"])
def test_processes_preparing_the_directory_at_once_are_never_refused(
    tmp_path: Path, state: str
) -> None:
    """The same with 8 aelix PROCESSES started at one instant (several ``discover
    install`` / ``update`` runs, the shape Claude cross r4 measured). Plain
    ``subprocess`` — no multiprocessing start method to differ on Windows."""

    import time

    for attempt in range(2):
        agent = tmp_path / f"agent-{attempt}"
        if state == "older-text":
            _stale(agent)
        start = time.time() + 2.0
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _PREPARE_AT, str(agent), repr(start)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            for _ in range(8)
        ]
        outs = [(p.wait(timeout=120), p.communicate()[0]) for p in procs]
        assert all(code == 0 for code, _ in outs), outs
        _assert_prepared(agent)


@pytest.mark.parametrize("named", [True, False])
async def test_real_uv_a_default_index_in_the_environment_still_applies(
    real_uv_lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, named: bool
) -> None:
    """Verify r4 blocking 1: ``UV_DEFAULT_INDEX=corp=<url>`` — the named form uv's
    ``UV_INDEX_CORP_USERNAME`` / ``_PASSWORD`` need — was refused as a relative path
    (rc 2); 61f03b67 installed from it. It reaches uv as set again."""

    lab = real_uv_lab
    _drop_user_pin(lab)
    org = _simple_index(lab["root"] / "corp-simple", "ORG-CORP-INDEX", "1.0")
    monkeypatch.setenv("UV_DEFAULT_INDEX", f"corp={org}" if named else org)
    monkeypatch.setenv("UV_INDEX_CORP_USERNAME", "fake-user")
    monkeypatch.setenv("UV_INDEX_CORP_PASSWORD", "fake-r5-password")
    mem = await _catalog(lab, "local-ext")

    for args in (
        ["discover", "install", "probe", "--yes", "--no-verify"],
        ["update", "local-ext", "--yes", "--no-verify"],
    ):
        assert await run_extension_command_async(args, settings=mem) == 0
        assert _installed_origin(lab) == "ORG-CORP-INDEX"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows refuses to replace a file another handle holds open; a reader that "
    "never stops holds it open (aelix retries a brief hold, the row would not end)",
)
def test_a_reader_never_sees_a_sentinel_missing_or_half_written(tmp_path: Path) -> None:
    """Why the rewrite is a rename, not unlink-then-create (round 4): another aelix's uv
    child may be reading ``uv.toml`` while this process rewrites it. Between an unlink
    and the new file's last byte, uv would find no ``uv.toml`` there (and go on to the
    directories above) or a partial one. With a rename, every read sees one whole file:
    the old text or aelix's."""

    import threading

    agent = tmp_path / "agent"
    neutral = Path(ei.catalog_installer_cwd(str(agent)))
    want = ei.INSTALLER_CWD_UV_TOML.encode("utf-8")
    older = b"# what an older aelix wrote\n" * 40
    seen: set[bytes | None] = set()
    done = threading.Event()

    def read() -> None:
        while not done.is_set():
            try:
                seen.add((neutral / "uv.toml").read_bytes())
            except FileNotFoundError:
                seen.add(None)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for _ in range(400):
            tmp = neutral / ".older.tmp"
            tmp.write_bytes(older)
            os.replace(tmp, neutral / "uv.toml")  # an older aelix's text, put atomically
            ei.catalog_installer_cwd(str(agent))
    finally:
        done.set()
        reader.join()
    assert seen <= {want, older}, sorted(repr(s)[:60] for s in seen - {want, older})


# === review round 5b: a relative PIP_CONFIG_FILE is read where the child runs =====


def _pip_conf(directory: Path, url: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pip.conf").write_text(f"[global]\nindex-url = {url}\n", "utf-8")


def _only_the_explicit_pip_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """pip's search path reduced to ``PIP_CONFIG_FILE`` alone (no host file leaks in)."""

    monkeypatch.setattr(
        ei,
        "_pip_config_candidates",
        lambda env=None: (
            [env["PIP_CONFIG_FILE"]]
            if env and env.get("PIP_CONFIG_FILE") not in (None, "", os.devnull)
            else []
        ),
    )


def test_a_relative_pip_config_file_is_read_from_the_childs_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r5: the pip.conf -> uv translation opened a relative ``PIP_CONFIG_FILE``
    in THIS process's cwd (the user's repository) while the child ran in the installer
    directory. pip opens it from its own cwd, so the child's cwd places it; ``None``
    (a typed install, child in this cwd) keeps the old reading; absolute and
    ``os.devnull`` values are untouched."""

    _only_the_explicit_pip_config(monkeypatch)
    here, child = tmp_path / "here", tmp_path / "child"
    _pip_conf(here, "https://here.example.invalid/simple")
    _pip_conf(child, "https://child.example.invalid/simple")
    monkeypatch.chdir(here)
    env = {"PIP_CONFIG_FILE": "pip.conf"}

    got = ei.read_pip_index_config(env, cwd=str(child))
    assert got.index_url == "https://child.example.invalid/simple"
    assert got.origin == os.path.join(str(child), "pip.conf")
    assert ei.read_pip_index_config(env).index_url == "https://here.example.invalid/simple"
    absolute = {"PIP_CONFIG_FILE": str(here / "pip.conf")}
    assert ei.read_pip_index_config(absolute, cwd=str(child)).index_url == (
        "https://here.example.invalid/simple"
    )
    assert not ei.read_pip_index_config({"PIP_CONFIG_FILE": os.devnull}, cwd=str(child))
    assert not ei.read_pip_index_config(env, cwd=str(tmp_path / "empty"))


async def test_a_catalog_install_on_uv_takes_no_index_from_the_repos_pip_conf(
    lab: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify r5's ``probe_pipconf_rel.py`` shape through the CLI: the repository (cwd)
    holds a ``pip.conf`` naming a decoy index and the user's env has
    ``PIP_CONFIG_FILE=pip.conf``. A catalog install and an update hand uv no
    ``UV_INDEX_URL`` from it (red on 1e51422a: both carried the decoy); a
    ``pip.conf`` in the installer directory IS read, as the child would; a typed
    install still reads the cwd's file, where its child runs."""

    _only_the_explicit_pip_config(monkeypatch)
    decoy = "https://decoy.example.invalid/simple"
    _pip_conf(lab["repo"], decoy)
    monkeypatch.setenv("PIP_CONFIG_FILE", "pip.conf")
    _use_backend(monkeypatch, lab, "uv")
    rec = _recorder(monkeypatch)
    mem = await _catalog(lab, "local-ext")
    capsys.readouterr()

    for args in (
        ["discover", "install", "probe", "--yes", "--no-verify"],
        ["update", "local-ext", "--yes", "--no-verify"],
    ):
        rec.calls.clear()
        assert await run_extension_command_async(args, settings=mem) == 0
        assert len(rec.calls) == 1 and _same_dir(rec.calls[0][1], lab["neutral"])
        assert rec.calls[0][2] == {}, (args, rec.calls[0][2])
        assert "decoy.example.invalid" not in capsys.readouterr().out

    org = "https://org.example.invalid/simple"
    _pip_conf(lab["neutral"], org)
    rec.calls.clear()
    assert (
        await run_extension_command_async(
            ["discover", "install", "probe", "--yes", "--no-verify"], settings=mem
        )
        == 0
    )
    assert rec.calls[0][2] == {"UV_INDEX_URL": org}

    # the typed control: its uv reads the repo's [tool.uv] (find-links), which would
    # switch the translation off, so that file is set aside with UV_NO_CONFIG
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    rec.calls.clear()
    assert (
        await run_extension_command_async(
            ["install", "local-ext", "--yes", "--no-verify"], settings=SettingsManager.in_memory({})
        )
        == 0
    )
    assert rec.calls[0][1] is None
    assert rec.calls[0][2] == {"UV_INDEX_URL": decoy}
