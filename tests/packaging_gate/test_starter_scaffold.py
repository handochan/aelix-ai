"""The starter scaffold must BUILD to a wheel that SHIPS its manifest (ADR-0207).

A scaffold that drops the manifest is worse than no scaffold: it teaches the
exact footgun `aelix extension verify` exists to catch. So this builds
`examples/starter/` for real and asserts the wheel contains
`aelix_starter/aelix-plugin.toml` and `aelix_starter/themes/example.toml`, then
extracts that wheel into a throwaway site and asserts the #91 resolver binds it —
proving the shipped file is not just present but usable as installed metadata.

The build needs pip + hatchling. The interpreter pytest runs under may lack pip
(a `--no-pip` venv is common), so this discovers ANY interpreter on PATH that
has both and builds with it; if none does, it SKIPS rather than fails — an
environment without a build toolchain cannot be asked to prove a build. Where the
toolchain exists (CI images generally have it), the guarantee is enforced for
real.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from aelix_coding_agent.cli import extension_install as EI
from aelix_coding_agent.extensions import loader as L
from aelix_coding_agent.extensions.ep_manifest import EpOutcome

SCAFFOLD = (
    Path(__file__).resolve().parents[2]
    / "packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter"
)

MANIFEST_IN_WHEEL = "aelix_starter/aelix-plugin.toml"
THEME_IN_WHEEL = "aelix_starter/themes/example.toml"


def _candidate_interpreters() -> list[str]:
    """Every distinct python we can reach — dedup by realpath.

    The interpreter running the suite (``sys.executable``), everything named
    ``python*`` on PATH, and a few well-known absolute locations that a venv's
    PATH shuffle can hide (a devcontainer keeps the full toolchain outside the
    project venv). Non-existent entries are harmless — they simply fail the probe.
    """

    names = ("python3.12", "python3.11", "python3", "python")
    found: list[str] = [sys.executable]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for name in names:
            found.append(str(Path(directory) / name))
    found += [
        "/home/codespace/.python/current/bin/python",
        "/usr/local/bin/python3",
        "/usr/local/python/bin/python3",
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for path in found:
        if not (Path(path).is_file() or Path(path).is_symlink()):
            continue
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            unique.append(path)
    return unique


def _probe(py: str, module_expr: str) -> bool:
    try:
        res = subprocess.run(  # noqa: S603 — fixed args, discovered interpreter
            [py, "-c", module_expr], capture_output=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _pick_builder() -> tuple[str, bool] | None:
    """Choose a build interpreter: ``(python, no_isolation)``.

    Prefer one that ALREADY has hatchling — it builds with
    ``--no-build-isolation``, deterministically and offline. Otherwise fall back
    to any interpreter with pip and let build isolation fetch hatchling (needs
    network). ``None`` means no interpreter can build at all.
    """

    pip_capable: list[str] = []
    for py in _candidate_interpreters():
        if not _probe(py, "import pip"):
            continue
        if _probe(py, "import hatchling"):
            return py, True  # pip + hatchling → no isolation needed
        pip_capable.append(py)
    if pip_capable:
        return pip_capable[0], False  # pip only → build isolation fetches hatchling
    return None


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    chosen = _pick_builder()
    if chosen is None:
        pytest.skip("no interpreter with pip available; cannot build the scaffold wheel")
    py, no_isolation = chosen
    out = tmp_path_factory.mktemp("starter-wheel")
    cmd = [py, "-m", "pip", "wheel", str(SCAFFOLD), "-w", str(out), "--no-deps"]
    if no_isolation:
        cmd.append("--no-build-isolation")
    res = subprocess.run(  # noqa: S603 — discovered interpreter, fixed subcommand
        cmd, capture_output=True, text=True, timeout=300
    )
    if res.returncode != 0:
        tail = (res.stdout + res.stderr).lower()
        # An isolated build that could not reach an index is an environment
        # limitation, not a scaffold defect — skip rather than red. A build that
        # failed for any OTHER reason is a real failure and must surface.
        if not no_isolation and any(
            s in tail for s in ("network", "could not find", "temporary failure",
                                "connection", "retries", "resolve", "offline")
        ):
            pytest.skip(f"build isolation could not fetch the backend (offline?):\n{res.stderr[-800:]}")
        pytest.fail(f"pip wheel failed:\nSTDOUT\n{res.stdout}\nSTDERR\n{res.stderr}")
    wheels = list(out.glob("aelix_starter_ext-*.whl"))
    assert len(wheels) == 1, f"expected exactly one starter wheel, got {wheels}"
    return wheels[0]


def test_wheel_ships_the_manifest_and_theme(built_wheel: Path) -> None:
    """THE anti-footgun guarantee: the manifest and theme are IN the wheel."""

    with zipfile.ZipFile(built_wheel) as zf:
        names = set(zf.namelist())

    assert MANIFEST_IN_WHEEL in names, (
        f"the scaffold wheel DROPPED its manifest — this is exactly the footgun; "
        f"wheel contained: {sorted(names)}"
    )
    assert THEME_IN_WHEEL in names, f"the scaffold wheel dropped its theme; wheel: {sorted(names)}"


def test_wheel_declares_the_entry_point(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as zf:
        eps = next(
            zf.read(n).decode()
            for n in zf.namelist()
            if n.endswith(".dist-info/entry_points.txt")
        )
    assert "[aelix.extensions]" in eps
    assert "aelix_starter:setup" in eps


@pytest.fixture
def installed_scaffold(
    built_wheel: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Extract the REAL built wheel into a throwaway site, declared trusted.

    A wheel's on-disk layout IS the installed layout — package dir plus a
    dist-info carrying entry_points.txt and RECORD — so extracting it and putting
    it on sys.path gives ``importlib.metadata`` a genuine Distribution whose
    manifest the #91 resolver can bind from RECORD alone.
    """

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    with zipfile.ZipFile(built_wheel) as zf:
        zf.extractall(site_dir)
    sys.path.insert(0, str(site_dir))

    real_ep = importlib.metadata.entry_points

    def scoped(**kwargs: object) -> importlib.metadata.EntryPoints:
        found = real_ep(**kwargs)  # type: ignore[arg-type]
        return importlib.metadata.EntryPoints(
            ep
            for ep in found
            if str(getattr(ep, "dist", None) and ep.dist.locate_file("")).startswith(
                str(site_dir)
            )
        )

    monkeypatch.setattr(importlib.metadata, "entry_points", scoped)
    real_env = EI.environment_site_dirs
    trusted = lambda: real_env() | frozenset({site_dir.resolve()})  # noqa: E731
    monkeypatch.setattr(EI, "environment_site_dirs", trusted)
    monkeypatch.setattr(L, "environment_site_dirs", trusted)

    try:
        yield site_dir
    finally:
        sys.path.remove(str(site_dir))
        root = str(site_dir)
        for name, mod in list(sys.modules.items()):
            origin = getattr(mod, "__file__", None) or ""
            if origin.startswith(root):
                del sys.modules[name]


def test_installed_scaffold_binds_and_verify_is_green(
    installed_scaffold: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: the real wheel installs, the manifest binds, verify exits 0."""

    statuses = EI.classify_installed_endpoints()
    starter = [s for s in statuses if s.ep_name == "aelix-starter"]
    assert len(starter) == 1, f"scaffold endpoint not discovered; saw {[s.ep_name for s in statuses]}"
    assert starter[0].outcome is EpOutcome.BOUND
    assert starter[0].plugin_id == "aelix-starter"

    rc = EI.run_extension_command(["verify", "aelix-starter"])
    assert rc == 0
    assert "BOUND" in capsys.readouterr().out
