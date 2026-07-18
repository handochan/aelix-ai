#!/usr/bin/env python3
"""Generate the CycloneDX SBOM for the aelix distribution set.

Run from the repo root inside the dev environment::

    uv run python scripts/generate_sbom.py

Scope: the locked runtime closure of the uv workspace — all extras (tui,
images) included, dev group excluded. Two provenance components that no
lockfile scanner can discover are injected on top:

- pi (earendil-works/pi @ 734e08e, MIT) — substantial portions of aelix are
  a TypeScript-to-Python port of pi (see NOTICE / THIRD-PARTY-NOTICES.md).
- models.dev (MIT) — the generated model catalog data's original source.

License fields are backfilled from installed package metadata because
cyclonedx-py's requirements mode has no environment to read them from; the
OVERRIDES table covers packages that publish no usable license metadata.

Output: sbom/aelix-<version>.cdx.json (older aelix-*.cdx.json are removed).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SBOM_DIR = ROOT / "sbom"

FIRST_PARTY = ["aelix-ai", "aelix-agent-core", "aelix-coding-agent", "aelix-server"]

# Packages whose PyPI/installed metadata carries no usable license declaration.
# Each entry was confirmed by hand against the license text shipped in the
# package's own distribution — extend it if `--- N components still have no
# license` reports new names.
OVERRIDES = {
    "rich-pixels": "MIT",  # LICENSE in the wheel; no metadata field, no classifier
    # Windows-only conditional deps: never installed in the Linux dev env, so
    # importlib.metadata cannot see them. Confirmed against upstream releases.
    "colorama": "BSD-3-Clause",
    "pywin32": "PSF-2.0",
}

_CLASSIFIER_TO_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "License :: OSI Approved :: Historical Permission Notice and Disclaimer (HPND)": "HPND",
}

_SPDX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")

VALIDATE_SNIPPET = """\
import sys
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
err = JsonStrictValidator(SchemaVersion.V1_6).validate_str(open(sys.argv[1]).read())
if err:
    sys.exit(f"schema validation FAILED: {err}")
print("enriched BOM: CycloneDX 1.6 strict validation passed")
"""


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def project_version() -> str:
    import tomllib

    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["version"]


def installed_license(name: str) -> str | None:
    """Best-available license string for an installed distribution."""
    from importlib import metadata

    try:
        meta = metadata.distribution(name).metadata
    except metadata.PackageNotFoundError:
        return OVERRIDES.get(name)
    # PackageMetadata lacks ``.get`` in the 3.11 typeshed protocol; membership
    # checks + __getitem__ are the typed way to read optional headers.
    if "License-Expression" in meta:
        return meta["License-Expression"]
    for value in meta.get_all("Classifier") or []:
        if value in _CLASSIFIER_TO_SPDX:
            return _CLASSIFIER_TO_SPDX[value]
    # Legacy free-text License field: usable only when it is a short token,
    # not a pasted license body or the setuptools "UNKNOWN" placeholder.
    legacy = meta["License"] if "License" in meta else None  # noqa: SIM401 — PackageMetadata has no .get in the 3.11 typeshed
    if legacy and legacy != "UNKNOWN" and len(legacy) < 64 and "\n" not in legacy:
        return legacy
    return OVERRIDES.get(name)


def as_licenses_field(expr: str) -> list[dict]:
    if any(f" {op} " in expr for op in ("AND", "OR", "WITH")):
        return [{"expression": expr}]
    if _SPDX_ID_RE.match(expr):
        return [{"license": {"id": expr}}]
    return [{"license": {"name": expr}}]


def first_party_component(name: str, version: str) -> dict:
    return {
        "type": "library",
        "bom-ref": f"pkg:pypi/{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{name}@{version}",
        "licenses": [{"license": {"id": "Apache-2.0"}}],
    }


PI_COMPONENT = {
    "type": "library",
    "bom-ref": "pkg:github/earendil-works/pi@734e08e",
    "name": "pi (earendil-works)",
    "version": "734e08e",
    "purl": "pkg:github/earendil-works/pi@734e08e",
    "description": (
        "Upstream TypeScript implementation; substantial portions of aelix are "
        "a TypeScript-to-Python port of pi. Not an installable dependency — "
        "recorded for derivation provenance. See THIRD-PARTY-NOTICES.md."
    ),
    "licenses": [{"license": {"id": "MIT"}}],
    "copyright": "Copyright (c) 2025 Mario Zechner",
    "externalReferences": [{"type": "vcs", "url": "https://github.com/earendil-works/pi"}],
}

MODELS_DEV_COMPONENT = {
    "type": "data",
    "bom-ref": "pkg:github/sst/models.dev",
    "name": "models.dev (model catalog data)",
    "description": (
        "Original source of the generated model catalog data "
        "(aelix_ai/models_generated.json), via pi's models.generated.ts. "
        "Recorded for data provenance. See THIRD-PARTY-NOTICES.md."
    ),
    "licenses": [{"license": {"id": "MIT"}}],
    "copyright": "Copyright (c) 2025 models.dev",
    "externalReferences": [{"type": "vcs", "url": "https://github.com/sst/models.dev"}],
}


def main() -> int:
    version = project_version()
    SBOM_DIR.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        req = Path(tmp) / "requirements.txt"
        raw = Path(tmp) / "raw.cdx.json"
        run([
            "uv", "export", "--frozen", "--no-hashes", "--no-emit-workspace",
            "--all-extras", "--no-dev", "--format", "requirements-txt",
            "-o", str(req),
        ])
        run([
            "uvx", "--from", "cyclonedx-bom", "cyclonedx-py", "requirements",
            str(req), "--sv", "1.6", "--of", "JSON", "--validate", "-o", str(raw),
        ])
        bom = json.loads(raw.read_text())

    # -- enrich ---------------------------------------------------------
    bom["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"
    meta = bom.setdefault("metadata", {})
    meta["timestamp"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta["component"] = {
        "type": "application",
        "bom-ref": f"pkg:pypi/aelix@{version}",
        "name": "aelix",
        "version": version,
        "purl": f"pkg:pypi/aelix@{version}",
        "licenses": [{"license": {"id": "Apache-2.0"}}],
    }

    components = bom.setdefault("components", [])
    present = {c["name"] for c in components}
    for name in FIRST_PARTY:
        if name not in present:
            components.append(first_party_component(name, version))
    for provenance in (PI_COMPONENT, MODELS_DEV_COMPONENT):
        if provenance["name"] not in present:
            components.append(provenance)

    missing = []
    for comp in components:
        if comp.get("licenses"):
            continue
        expr = installed_license(comp["name"])
        if expr:
            comp["licenses"] = as_licenses_field(expr)
        else:
            missing.append(comp["name"])

    # -- validate + write ----------------------------------------------
    # All checks run BEFORE the committed artifact is touched, so a failed
    # run never clobbers the last good SBOM.
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.6"
    if missing:
        print(f"--- {len(missing)} components have no license "
              f"(add to OVERRIDES after checking by hand): {', '.join(sorted(missing))}")
        print("aborting: sbom/ left untouched")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "enriched.cdx.json"
        candidate.write_text(json.dumps(bom, indent=2, sort_keys=False) + "\n")
        # cyclonedx-py's --validate above only covered the raw pre-enrichment
        # BOM (zero license entries); the backfill can emit a non-SPDX id, so
        # strict-validate the final document too.
        try:
            run([
                "uvx", "--from", "cyclonedx-bom", "python", "-c",
                VALIDATE_SNIPPET, str(candidate),
            ])
        except subprocess.CalledProcessError:
            print("aborting: enriched BOM failed CycloneDX 1.6 strict validation "
                  "(likely a non-SPDX license id — see as_licenses_field/OVERRIDES); "
                  "sbom/ left untouched")
            return 1

        out = SBOM_DIR / f"aelix-{version}.cdx.json"
        for stale in SBOM_DIR.glob("aelix-*.cdx.json"):
            if stale != out:
                stale.unlink()
                print(f"removed stale {stale.name}")
        out.write_text(candidate.read_text())

    licensed = sum(1 for c in components if c.get("licenses"))
    print(f"wrote {out.relative_to(ROOT)}: {len(components)} components, "
          f"{licensed} with license data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
