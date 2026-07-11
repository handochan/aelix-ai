"""Release version-consistency gate.

Asserts that every published Aelix package carries the exact shared release
version and that every inter-package ``==`` pin (including the umbrella's
``[tui]`` / ``[images]`` extras) points at that same version. A drifted version
or a stale cross-pin would ship a self-inconsistent set that cannot resolve
from a single find-links download, so this is enforced as a test.

When cutting a new release, bump ``EXPECTED_VERSION`` here in lock-step with the
pyproject files (see ``RELEASING.md``).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# The shared beta release version (PEP 440 normalized form of tag v0.1.0-beta.1).
EXPECTED_VERSION = "0.1.0b1"

REPO_ROOT = Path(__file__).resolve().parents[1]

# The four packages published to the release set (aelix-server is deferred and
# not published, but is bumped in lock-step for workspace coherence).
PUBLISHED_PYPROJECTS = {
    "aelix": REPO_ROOT / "pyproject.toml",
    "aelix-ai": REPO_ROOT / "packages" / "aelix-ai" / "pyproject.toml",
    "aelix-agent-core": REPO_ROOT / "packages" / "aelix-agent-core" / "pyproject.toml",
    "aelix-coding-agent": REPO_ROOT / "packages" / "aelix-coding-agent" / "pyproject.toml",
}
SERVER_PYPROJECT = REPO_ROOT / "packages" / "aelix-server" / "pyproject.toml"

# Parse `aelix-name[extra,extra]==version` requirement strings.
_AELIX_PIN_RE = re.compile(r"^(?P<name>aelix[\w-]*)(?:\[[^\]]*\])?==(?P<ver>.+)$")


def _load(pyproject: Path) -> dict:
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)


def _iter_aelix_pins(data: dict):
    """Yield (raw_requirement, pinned_version) for every aelix-* ``==`` pin.

    Covers both ``[project].dependencies`` and every list under
    ``[project.optional-dependencies]`` (the umbrella's ``[tui]``/``[images]``).
    Non-aelix requirements and unpinned aelix requirements are skipped.
    """
    project = data.get("project", {})
    reqs: list[str] = list(project.get("dependencies", []))
    for extra_reqs in project.get("optional-dependencies", {}).values():
        reqs.extend(extra_reqs)
    for req in reqs:
        if not req.startswith("aelix"):
            continue
        match = _AELIX_PIN_RE.match(req.strip())
        if match is None:
            # An aelix requirement that is not an `==` pin (e.g. workspace-only
            # bare `aelix-ai`). Cross-pin consistency only governs `==` pins.
            continue
        yield req, match.group("ver")


def test_published_package_versions_are_expected():
    """Every published package (and aelix-server) declares EXPECTED_VERSION."""
    for name, path in PUBLISHED_PYPROJECTS.items():
        version = _load(path)["project"]["version"]
        assert version == EXPECTED_VERSION, (
            f"{name} version is {version!r}, expected {EXPECTED_VERSION!r} ({path})"
        )

    server_version = _load(SERVER_PYPROJECT)["project"]["version"]
    assert server_version == EXPECTED_VERSION, (
        f"aelix-server version is {server_version!r}, expected {EXPECTED_VERSION!r} "
        f"(bump it in lock-step for workspace coherence)"
    )


def test_inter_package_pins_match_expected_version():
    """Every aelix-* `==` pin across the published set points at EXPECTED_VERSION."""
    seen_any = False
    for name, path in PUBLISHED_PYPROJECTS.items():
        data = _load(path)
        for raw, pinned in _iter_aelix_pins(data):
            seen_any = True
            assert pinned == EXPECTED_VERSION, (
                f"{name}: pin {raw!r} points at {pinned!r}, "
                f"expected {EXPECTED_VERSION!r} ({path})"
            )
    # Guard against a silently-passing test if parsing ever finds nothing.
    assert seen_any, "no aelix inter-package pins found — parser or layout changed"


def test_expected_pins_are_present():
    """The known cross-pins exist (catches a pin being dropped, not just drifted)."""
    root = _load(PUBLISHED_PYPROJECTS["aelix"])
    root_deps = set(root["project"]["dependencies"])
    for dep in ("aelix-ai", "aelix-agent-core", "aelix-coding-agent"):
        assert f"{dep}=={EXPECTED_VERSION}" in root_deps, (
            f"umbrella is missing pin {dep}=={EXPECTED_VERSION}"
        )
    extras = root["project"]["optional-dependencies"]
    assert f"aelix-coding-agent[tui]=={EXPECTED_VERSION}" in extras["tui"]
    assert f"aelix-coding-agent[tui,images]=={EXPECTED_VERSION}" in extras["images"]

    core = _load(PUBLISHED_PYPROJECTS["aelix-agent-core"])
    assert f"aelix-ai=={EXPECTED_VERSION}" in core["project"]["dependencies"]

    coding = _load(PUBLISHED_PYPROJECTS["aelix-coding-agent"])
    coding_deps = set(coding["project"]["dependencies"])
    assert f"aelix-ai=={EXPECTED_VERSION}" in coding_deps
    assert f"aelix-agent-core=={EXPECTED_VERSION}" in coding_deps
