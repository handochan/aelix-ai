#!/usr/bin/env python3
"""Build placeholder distributions that claim the Aelix names on PyPI.

WHY THIS EXISTS
---------------
Every Aelix name is still unclaimed on PyPI, and PyPI has no way to hold a name
without publishing. Its own docs are explicit that the obvious candidate does
not work: a Trusted Publishing "pending publisher" *"does not create a project
or reserve a project's name until it is actually used to publish"*. Configuring
one and walking away leaves the name open to anyone.

So the only reservation is an upload. This script builds the smallest honest
upload that does it: a metadata-only distribution at version 0.0.0 whose
description says exactly what it is and where the real code is.

This is not name-squatting. Every name here is already in this repository's
release pipeline (`.github/workflows/release.yml` publishes aelix, aelix-ai,
aelix-agent-core and aelix-coding-agent; aelix-server is the deferred v1.1
daemon). The placeholders exist to keep those names available to the project
that is actually building them.

DESIGN NOTES
------------
* Version 0.0.0 sorts below every real release, so once the GA release lands,
  `pip install aelix` resolves to the real package and never to a placeholder.
* The placeholders ship NO importable module. A stub module would shadow the
  real package's import name for anyone who installed it in the meantime; empty
  metadata cannot.
* `Development Status :: 1 - Planning` and the description both say "placeholder"
  so nobody who lands on the PyPI page is misled.

USAGE
-----
    python scripts/reserve_pypi_names.py            # build into dist-reservation/
    python scripts/reserve_pypi_names.py --check    # only report what is free

Then upload (needs a PyPI API token — Trusted Publishing cannot do a first
upload for a project that does not exist yet):

    uv publish --token pypi-XXXX dist-reservation/*

Afterwards, add the GitHub Actions trusted publisher to each new project on
PyPI so the real release keeps working without a token.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO = "https://github.com/handochan/aelix-ai"
HOMEPAGE = "https://handochan.github.io/aelix-ai/"

# name -> one line describing what the name is FOR, shown on the PyPI page.
NAMES: dict[str, str] = {
    "aelix": "the Aelix agent runtime (meta-package: installs the CLI and TUI)",
    "aelix-ai": "provider-agnostic messages, streaming primitives and tool definitions",
    "aelix-agent-core": "the agent loop, Agent, AgentHarness and the typed HookBus",
    "aelix-coding-agent": "ExtensionAPI, the extension loader and the built-in extensions",
    "aelix-server": "the Aelix Web UI daemon (deferred to v1.1)",
}

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist-reservation"
WORK = ROOT / ".reservation-build"


def is_available(name: str) -> bool | None:
    """True if free, False if taken, None if PyPI could not be reached.

    Uses the JSON API. Do NOT use pypi.org/project/<name>/ for this — it sits
    behind a bot wall that answers 200 with a challenge page for every name,
    free or not.
    """
    url = f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status != 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        return None
    except OSError:
        return None


def real_version() -> str:
    """The version the real packages are at, quoted in the placeholder text."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def write_placeholder(name: str, purpose: str, target: str) -> Path:
    pkg = WORK / name
    pkg.mkdir(parents=True, exist_ok=True)

    readme = f"""# {name}

**This is a placeholder release. It contains no code.**

`{name}` is a reserved distribution name for
[Aelix]({HOMEPAGE}) — an agent runtime and extension platform in pure Python.
Within the project it is {purpose}.

The real package is `{target}` and is not on PyPI yet. Until it is, installing
this gives you an empty distribution; it is published only so the name stays
with the project that is building it.

* Source: {REPO}
* Homepage: {HOMEPAGE}
* Install today: `curl -fsSL {REPO.replace("github.com", "raw.githubusercontent.com")}/main/install.sh | sh`

Licensed under Apache-2.0.
"""
    (pkg / "README.md").write_text(readme)

    pyproject = f"""[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.0.0"
description = "Placeholder, no code yet — name reserved for Aelix: {purpose}."
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
authors = [{{ name = "Aelix Contributors" }}]
classifiers = [
  "Development Status :: 1 - Planning",
  "Intended Audience :: Developers",
  "Operating System :: OS Independent",
  "Programming Language :: Python :: 3",
]

[project.urls]
Homepage = "{HOMEPAGE}"
Source = "{REPO}"

# No packages: a placeholder must not ship an importable module, or it would
# shadow the real package's import name for anyone who installed it early.
[tool.hatch.build.targets.wheel]
bypass-selection = true

[tool.hatch.build.targets.sdist]
only-include = ["README.md", "pyproject.toml"]
"""
    (pkg / "pyproject.toml").write_text(pyproject)
    return pkg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="only report availability")
    args = ap.parse_args()

    version = real_version()
    print(f"Real packages are at {version}; placeholders build at 0.0.0.\n")

    free: list[str] = []
    for name in NAMES:
        avail = is_available(name)
        state = {True: "available", False: "TAKEN", None: "could not check"}[avail]
        print(f"  {name:<22} {state}")
        if avail is True:
            free.append(name)
        elif avail is None:
            print("     ^ PyPI unreachable — resolve this before uploading.")

    if args.check:
        return 0
    if not free:
        print("\nNothing to build.")
        return 0

    if shutil.which("uv") is None:
        print("\nuv not found; it is what release.yml builds with.", file=sys.stderr)
        return 1

    if WORK.exists():
        shutil.rmtree(WORK)
    OUT.mkdir(exist_ok=True)

    print()
    for name in free:
        pkg = write_placeholder(name, NAMES[name], name)
        # Note: uv writes a .gitignore into the build directory as it runs, so
        # each sdist carries one alongside README.md and pyproject.toml. It is
        # inert; deleting it before the build does not help, since uv recreates
        # it. The wheel is unaffected — metadata only, as intended.
        proc = subprocess.run(
            ["uv", "build", "--out-dir", str(OUT), str(pkg)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"  build FAILED for {name}:\n{proc.stderr}", file=sys.stderr)
            return 1
        print(f"  built {name}")

    shutil.rmtree(WORK)
    built = sorted(p.name for p in OUT.iterdir())
    print(f"\n{len(built)} files in {OUT.relative_to(ROOT)}/:")
    for b in built:
        print(f"  {b}")
    print(
        "\nUpload with a PyPI API token (Trusted Publishing cannot make the"
        "\nfirst upload for a project that does not exist yet):"
        f"\n\n    uv publish --token pypi-XXXX {OUT.relative_to(ROOT)}/*\n"
        "\nThen add the GitHub Actions trusted publisher to each new PyPI"
        "\nproject so the real release needs no token."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
