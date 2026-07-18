"""Compliance guard: license/notice files stay present, in sync, and wired
into packaging.

Every published wheel/sdist must bundle LICENSE + NOTICE + THIRD-PARTY-NOTICES.md
(PEP 639 ``license-files``). hatchling resolves ``license-files`` relative to each
package directory, so the four sub-packages carry byte-identical copies of the
root files — this test is the drift guard for those copies.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Globbed, not hardcoded, so a future sub-package cannot ship without notices.
PACKAGES = sorted(p.parent for p in (REPO / "packages").glob("*/pyproject.toml"))
LICENSE_FILES = ["LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md"]


def test_workspace_package_discovery() -> None:
    assert len(PACKAGES) >= 4, PACKAGES


def test_root_notice_files_exist_and_attribute_pi() -> None:
    root_license = (REPO / "LICENSE").read_text()
    assert "Apache License" in root_license and "Version 2.0" in root_license

    notice = (REPO / "NOTICE").read_text()
    assert "Mario Zechner" in notice
    assert "earendil-works/pi" in notice
    assert "models.dev" in notice

    third_party = (REPO / "THIRD-PARTY-NOTICES.md").read_text()
    # The MIT condition is notice preservation: both upstream MIT texts must be
    # reproduced in full (pi + models.dev).
    assert third_party.count("Permission is hereby granted, free of charge") >= 2
    assert "Copyright (c) 2025 Mario Zechner" in third_party
    assert "Copyright (c) 2025 models.dev" in third_party


def test_package_copies_match_root() -> None:
    for pkg in PACKAGES:
        for name in LICENSE_FILES:
            root_bytes = (REPO / name).read_bytes()
            pkg_file = pkg / name
            assert pkg_file.exists(), f"{pkg_file} missing — copy it from the repo root"
            assert pkg_file.read_bytes() == root_bytes, (
                f"{pkg_file} differs from the root copy — re-run: "
                f"for p in packages/*/; do cp {' '.join(LICENSE_FILES)} $p; done"
            )


def test_pyprojects_declare_spdx_license_and_license_files() -> None:
    for project_dir in [REPO, *PACKAGES]:
        with open(project_dir / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        project = data["project"]
        assert project["license"] == "Apache-2.0", project_dir
        assert project["license-files"] == LICENSE_FILES, project_dir
        # PEP 639: license classifiers are deprecated alongside an SPDX
        # license expression and hatchling rejects the combination.
        for classifier in project.get("classifiers", []):
            assert not classifier.startswith("License ::"), (project_dir, classifier)
        # The SPDX-string + license-files form needs hatchling >= 1.27; a bare
        # "hatchling" requirement would only surface as a build-time break.
        hatchling = [r for r in data["build-system"]["requires"] if r.startswith("hatchling")]
        floor = re.match(r"hatchling>=(\d+)\.(\d+)", hatchling[0]) if hatchling else None
        assert floor and (int(floor[1]), int(floor[2])) >= (1, 27), (project_dir, hatchling)


def test_committed_sbom_matches_project_version() -> None:
    with open(REPO / "pyproject.toml", "rb") as f:
        version = tomllib.load(f)["project"]["version"]
    sbom_path = REPO / "sbom" / f"aelix-{version}.cdx.json"
    assert sbom_path.exists(), (
        f"{sbom_path} missing — regenerate: uv run python scripts/generate_sbom.py"
    )
    bom = json.loads(sbom_path.read_text())
    assert bom["metadata"]["component"]["version"] == version
