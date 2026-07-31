#!/usr/bin/env python3
"""Build defensive npm placeholders for the Aelix names.

READ THIS BEFORE RUNNING IT
---------------------------
Aelix is a Python project. It has no npm package and no plan for one, so these
placeholders are purely defensive: they stop someone else publishing `aelix` on
npm and trading on the name — a real and common supply-chain pattern, and one
that would be especially damaging here because there IS already an unrelated
"Aelix" crypto agent in circulation.

That defence has a cost. npm's policy discourages holding names you do not
intend to use, and a maintainer can dispute a package that is only a
placeholder. The mitigation is honesty: each package says in its README exactly
what it is and points at the real project, so nobody is misled and a dispute has
nothing to bite on.

This is optional. The PyPI reservation is not — that is where Aelix actually
ships. If you would rather not hold npm names you will never use, skip this.

USAGE
-----
    python scripts/reserve_npm_names.py          # write packages under npm-reservation/
    npm login
    for d in npm-reservation/*/; do (cd "$d" && npm publish --access public); done
"""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path

REPO = "https://github.com/handochan/aelix-ai"
HOMEPAGE = "https://handochan.github.io/aelix-ai/"
NAMES = ["aelix", "aelix-ai"]

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "npm-reservation"


def is_available(name: str) -> bool | None:
    try:
        with urllib.request.urlopen(f"https://registry.npmjs.org/{name}", timeout=15) as r:
            return r.status != 200
    except urllib.error.HTTPError as exc:
        return True if exc.code == 404 else None
    except OSError:
        return None


def write(name: str) -> None:
    pkg = OUT / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.0.0",
                "description": (
                    "Placeholder. Aelix is a Python agent runtime and has no "
                    "npm package; this name is held by the project."
                ),
                "keywords": ["aelix", "placeholder"],
                "homepage": HOMEPAGE,
                "repository": {"type": "git", "url": f"git+{REPO}.git"},
                "license": "Apache-2.0",
                "files": ["README.md"],
                "private": False,
            },
            indent=2,
        )
        + "\n"
    )
    (pkg / "README.md").write_text(
        f"""# {name}

**Placeholder. There is no npm package for Aelix and none is planned.**

[Aelix]({HOMEPAGE}) is an agent runtime and extension platform written in
**pure Python**. It installs from PyPI or from the project's own installer:

```bash
curl -fsSL {REPO.replace("github.com", "raw.githubusercontent.com")}/main/install.sh | sh
```

This npm name is held so that nothing unrelated can be published under it and
mistaken for the project. If you need the name for something genuine, open an
issue at {REPO}/issues — the project would rather transfer it than sit on it.

Apache-2.0.
"""
    )


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    made = []
    for name in NAMES:
        avail = is_available(name)
        state = {True: "available", False: "TAKEN", None: "could not check"}[avail]
        print(f"  {name:<12} {state}")
        if avail is True:
            write(name)
            made.append(name)
    if not made:
        print("\nNothing to do.")
        return 0
    print(f"\nWrote {len(made)} package(s) under {OUT.relative_to(ROOT)}/. Publish with:")
    print("\n    npm login")
    print(
        f"    for d in {OUT.relative_to(ROOT)}/*/; do "
        '(cd "$d" && npm publish --access public); done\n'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
