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
upload that does it: a metadata-only distribution whose description says exactly
what it is and where the real code is.

This is not name-squatting. Every name here is already in this repository's
release pipeline (`.github/workflows/release.yml` publishes aelix, aelix-ai,
aelix-agent-core and aelix-coding-agent; aelix-server is the deferred v1.1
daemon). The placeholders exist to keep those names available to the project
that is actually building them.

THE VERSION NUMBER IS NOT COSMETIC
----------------------------------
The placeholder version must itself be a PRE-RELEASE. Measured against pip
26.0.1 with a local PEP 503 index, `pip install aelix` resolves like this:

    placeholder      alone          + 0.1.0b1 beta      + 0.1.0 GA
    ---------------- -------------- ------------------- -----------
    (none)           —              beta                GA
    0.0.0   stable   placeholder    PLACEHOLDER  <-- !  GA
    0.0.0   yanked   ERROR  <-- !   ERROR        <-- !  GA
    0.0.0a0 pre      placeholder    beta                GA

A *stable* 0.0.0 outranks a pre-release, so it would shadow the beta with an
empty package for the whole beta period. Yanking is worse, not better: pip
still counts the yanked stable when deciding whether any final release exists,
so it declines to fall back to the pre-release and `pip install aelix` fails
outright. Only a pre-release placeholder gets out of the way of a pre-release
beta, which is why PLACEHOLDER_VERSION is an alpha.

DESIGN NOTES
------------
* The placeholders ship NO importable module. A stub module would shadow the
  real package's import name for anyone who installed it in the meantime; empty
  metadata cannot.
* `Development Status :: 1 - Planning` and the description both say "placeholder"
  so nobody who lands on the PyPI page is misled.
* Do NOT yank these after uploading — see the table above.

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

#: Must be a PRE-RELEASE — see "THE VERSION NUMBER IS NOT COSMETIC" above.
#: An alpha loses to the 0.1.0b1 beta and to every later release, so the
#: placeholder can never be what `pip install aelix` selects once real code
#: exists.
PLACEHOLDER_VERSION = "0.0.0a0"

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
version = "{PLACEHOLDER_VERSION}"
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
    print(
        f"Real packages are at {version}; "
        f"placeholders build at {PLACEHOLDER_VERSION} (a pre-release, on purpose).\n"
    )

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
