"""``uv.lock`` must still describe the dependencies the manifests declare.

MEASURED DEFECT this exists for. ``c97fa12`` added ``packaging>=23`` to
``packages/aelix-coding-agent/pyproject.toml`` and shipped without
regenerating the lock. The suite was green — 9168 tests — CI was green, and
nothing in either reads ``uv.lock``, so the only reason it surfaced at all was
that an unrelated worktree happened to be dirty. It took a follow-up commit,
``a5e14ef``, to sync it.

WHY NOTHING ELSE CATCHES IT. ``test_declared_dependencies.py`` reads
``pyproject.toml`` — deliberately, because a dev checkout has packages a user's
install does not, and the manifest is the only place that difference is
visible. That makes it structurally blind to this: the manifest is the side
that was RIGHT. Everything else in the suite imports from the dev environment,
where ``uv sync`` had already installed the package the lock omitted.

WHAT A STALE LOCK COSTS. ``uv sync``, ``uv run`` and any install resolved from
the lock get the old set. So the failure lands on whoever builds from a clean
checkout — CI on a cold cache, a contributor's first ``uv sync``, a release
build — and it lands as a missing module at import time, far from the commit
that caused it.

WHAT THIS DOES NOT CHECK. Whether the locked VERSIONS still satisfy the
specifiers, and the ``==0.1.0b1`` pins on the workspace packages: uv rewrites
those to ``editable`` paths through ``[tool.uv.sources]``, so the lock does not
carry the pin to compare. ``test_release_version_consistency.py`` holds that
one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO_ROOT / "uv.lock"

_RELOCK = "uv lock"

#: Requirement -> ``(name, extras, specifier, marker)``, comparable across the
#: two spellings. A workspace member arrives from the manifest with a version
#: pin and from the lock as an ``editable`` path, so its specifier is dropped
#: on BOTH sides rather than compared — see the module docstring.
Canonical = tuple[str, tuple[str, ...], str, str]


def _members() -> set[str]:
    """The workspace packages, whose requirements uv rewrites as editable paths."""

    return {
        canonicalize_name(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["name"])
        for path in sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))
    }


MEMBERS = _members()


def _canonical(name: str, extras: Any, specifier: str, marker: str | None) -> Canonical:
    key = canonicalize_name(name)
    return (
        key,
        tuple(sorted(canonicalize_name(e) for e in (extras or ()))),
        "" if key in MEMBERS else str(SpecifierSet(specifier or "")),
        str(Marker(marker)) if marker else "",
    )


def _from_manifest(raw: str, extra: str | None = None) -> Canonical:
    requirement = Requirement(raw)
    marker = str(requirement.marker) if requirement.marker else None
    if extra is not None:
        # uv folds ``[project.optional-dependencies]`` into ``requires-dist``
        # under an ``extra ==`` marker. No optional dependency in this repo
        # carries a marker of its own; ``test_an_extra_never_hides_a_marker``
        # is what keeps this branch honest.
        assert marker is None, f"{raw} in extra {extra!r} has its own marker"
        marker = f'extra == "{extra}"'
    return _canonical(requirement.name, requirement.extras, str(requirement.specifier), marker)


def _from_lock(entry: dict[str, Any]) -> Canonical:
    return _canonical(
        entry["name"], entry.get("extras"), entry.get("specifier", ""), entry.get("marker")
    )


def _manifests() -> dict[str, tuple[Path, dict[str, Any]]]:
    paths = [REPO_ROOT / "pyproject.toml", *sorted(REPO_ROOT.glob("packages/*/pyproject.toml"))]
    found = {}
    for path in paths:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        found[canonicalize_name(data["project"]["name"])] = (path, data)
    return found


MANIFESTS = _manifests()

LOCKED: dict[str, dict[str, Any]] = {
    canonicalize_name(package["name"]): package
    for package in tomllib.loads(LOCK_PATH.read_text(encoding="utf-8"))["package"]
}


def _ids() -> list[str]:
    return sorted(MANIFESTS)


def test_the_scan_found_every_package_in_the_repo() -> None:
    """A gate that silently scanned nothing would pass forever.

    Five manifests: the umbrella and the four packages. If a package is added
    and this number is not updated, the new package's lock entry is unchecked —
    which is the failure this file exists to prevent, one level up.
    """

    assert set(MANIFESTS) == {
        "aelix",
        "aelix-agent-core",
        "aelix-ai",
        "aelix-coding-agent",
        "aelix-server",
    }, sorted(MANIFESTS)
    assert set(MANIFESTS) > MEMBERS


@pytest.mark.parametrize("package", _ids())
def test_every_declared_dependency_is_in_the_lock(package: str) -> None:
    """The direction that failed: a manifest gained a dependency, the lock did not."""

    path, data = MANIFESTS[package]
    project = data["project"]
    declared = {_from_manifest(raw) for raw in project.get("dependencies", [])}
    for extra, requirements in (project.get("optional-dependencies") or {}).items():
        declared |= {_from_manifest(raw, extra) for raw in requirements}

    assert package in LOCKED, f"{package} has no entry in {LOCK_PATH.name} — run `{_RELOCK}`"
    locked = {_from_lock(entry) for entry in LOCKED[package]["metadata"]["requires-dist"]}

    missing = declared - locked
    assert not missing, (
        f"{path.relative_to(REPO_ROOT)} declares {sorted(m[0] for m in missing)} and "
        f"{LOCK_PATH.name} does not. This is exactly the c97fa12 defect: a clean "
        f"`uv sync` installs the old set and the module is absent at import time. "
        f"Fix: `{_RELOCK}`, and commit the lock in the same change."
    )


@pytest.mark.parametrize("package", _ids())
def test_the_lock_never_carries_a_dependency_nobody_declares(package: str) -> None:
    """The other direction: a dependency was dropped and the lock kept resolving it.

    Less costly than the first — a leftover is installed, not missing — but it
    is how a dependency nobody has declared since keeps arriving in a build,
    and it makes the lock stop being evidence of anything.
    """

    path, data = MANIFESTS[package]
    project = data["project"]
    declared = {_from_manifest(raw) for raw in project.get("dependencies", [])}
    for extra, requirements in (project.get("optional-dependencies") or {}).items():
        declared |= {_from_manifest(raw, extra) for raw in requirements}

    locked = {_from_lock(entry) for entry in LOCKED[package]["metadata"]["requires-dist"]}
    stale = locked - declared
    assert not stale, (
        f"{LOCK_PATH.name} still requires {sorted(s[0] for s in stale)} for {package}, "
        f"which {path.relative_to(REPO_ROOT)} no longer declares. Fix: `{_RELOCK}`."
    )


def test_the_dev_group_is_locked_too() -> None:
    """``[dependency-groups] dev`` is what CI installs before it runs anything.

    A drift here does not reach a user, but it reaches every contributor and
    every CI leg, and it is stored in the same file by the same command.
    """

    declared = {
        _from_manifest(raw)
        for raw in MANIFESTS["aelix"][1].get("dependency-groups", {}).get("dev", [])
    }
    assert declared, "the dev group is empty — the scan is reading the wrong table"
    locked = {
        _from_lock(entry)
        for entry in LOCKED["aelix"]["metadata"].get("requires-dev", {}).get("dev", [])
    }
    assert declared == locked, (
        f"dev group drift — only in the manifest: {sorted(d[0] for d in declared - locked)}; "
        f"only in the lock: {sorted(x[0] for x in locked - declared)}. Fix: `{_RELOCK}`."
    )


@pytest.mark.parametrize("package", _ids())
def test_the_locked_extras_are_the_declared_extras(package: str) -> None:
    """``provides-extras`` is what ``pip install 'aelix[tui]'`` resolves against."""

    declared = sorted(MANIFESTS[package][1]["project"].get("optional-dependencies") or {})
    locked = sorted(LOCKED[package]["metadata"].get("provides-extras", []))
    assert declared == locked, f"{package}: extras {declared} vs locked {locked}"


def test_an_extra_never_hides_a_marker() -> None:
    """The assumption :func:`_from_manifest` makes, checked rather than assumed.

    If an optional dependency ever gains its own marker, uv combines the two
    and the string this file builds stops matching — which would read as lock
    drift and send someone to re-run `uv lock` at a green lock.
    """

    for _, data in MANIFESTS.values():
        extras = data["project"].get("optional-dependencies") or {}
        for name, requirements in extras.items():
            for raw in requirements:
                assert Requirement(raw).marker is None, (
                    f"{raw} in extra {name!r} carries a marker; teach "
                    "_from_manifest how uv combines it"
                )
