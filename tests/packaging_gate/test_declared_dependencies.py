"""Every third-party module the shipped code imports must be a DECLARED dependency.

MEASURED DEFECT this exists for. The launch-time update check needs
``packaging.version`` to know that ``0.1.0b1`` and the tag ``v0.1.0-beta.1``
are the same release. ``packaging`` was not declared by any package here — it
was merely PRESENT, because pytest depends on it. So the whole suite proved the
feature worked while an ``install.sh`` user would have got a module that
imported nothing, answered "no opinion" to every comparison, and never spoke.
Deleting the declaration from ``pyproject.toml`` left 89 tests green.

That is the general shape, not a one-off: a dev checkout installs test and lint
tooling that a user's install does not, so ANY module reachable from a dev
environment can look available. The only place the difference is visible is the
dependency list, which is why this test reads that rather than trying to import
anything.

WHAT IT DOES NOT CATCH. Version floors (``packaging>=23`` vs ``packaging``),
and a dependency declared on the wrong package of the four. Both are narrower
failures than the one above, and both are visible in review; "declared nowhere"
is the one nobody sees.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = REPO_ROOT / "packages"

#: The distributions in this repo. Imports of these are first-party.
FIRST_PARTY = {
    "aelix_ai",
    "aelix_agent_core",
    "aelix_agents",
    "aelix_coding_agent",
    "aelix_server",
    "aelix_status",
}

#: Import name -> distribution name, for the ones that differ. A distribution
#: is free to publish a module under any name, so this mapping cannot be
#: derived from the strings; each entry is a fact about a specific package.
_DIST_FOR_MODULE = {
    "markdown_it": "markdown-it-py",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "google": "google-genai",
    "tree_sitter_bash": "tree-sitter-bash",
}

#: Imports that are deliberately NOT declared, each with the reason. An entry
#: here is a claim that the code works when the module is absent.
_UNDECLARED_ON_PURPOSE = {
    "certifi": (
        "optional and guarded — providers/_trust_store.py imports it inside a "
        "try/except and reports (None, False) when it is missing, which is a "
        "line in `aelix status` rather than a failure. Its own comment says "
        "'certifi is a transitive dep, not a promise'."
    ),
    "starlette": (
        "imported directly by aelix-server's rpc_ws.py while only `fastapi` is "
        "declared. fastapi vendors nothing and re-exports starlette, so this "
        "works today and would break if fastapi ever swapped it. Recorded here "
        "rather than fixed silently: declaring it is a change to a shipped "
        "package's dependency list and belongs in its own commit."
    ),
}


def _pyprojects() -> list[Path]:
    found = sorted(PACKAGES.glob("*/pyproject.toml")) + [REPO_ROOT / "pyproject.toml"]
    return [p for p in found if p.is_file()]


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _declared() -> set[str]:
    names: set[str] = set()
    for path in _pyprojects():
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        specs = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            specs.extend(group)
        for spec in specs:
            head = spec.split(";")[0]
            for sep in (">", "<", "=", "!", "~", "[", " "):
                head = head.split(sep)[0]
            if head:
                names.add(_normalise(head))
    return names


def _imported() -> dict[str, set[str]]:
    """``{top-level module: {file, ...}}`` for every non-stdlib, non-first-party import."""

    found: dict[str, set[str]] = {}
    for package in sorted(PACKAGES.iterdir()):
        src = package / "src"
        if not src.is_dir():
            continue
        for file in sorted(src.rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # ``level > 0`` is a relative import — first-party by
                    # construction and it has no distribution to declare.
                    modules = (
                        [(node.module or "").split(".")[0]] if node.level == 0 else []
                    )
                else:
                    continue
                for module in modules:
                    if not module or module in FIRST_PARTY:
                        continue
                    if module in sys.stdlib_module_names:
                        continue
                    found.setdefault(module, set()).add(
                        str(file.relative_to(REPO_ROOT))
                    )
    return found


def test_the_scan_finds_the_imports_it_is_supposed_to_check() -> None:
    """A zero here would make the assertion below vacuous.

    Twenty-five third-party modules at the time of writing. Asserted loosely,
    because the exact number is not the point and pinning it would make every
    new dependency edit this line.
    """

    imported = _imported()
    assert len(imported) >= 15, f"only {len(imported)} third-party imports found"
    assert "httpx" in imported, "the scan missed a dependency that is certainly there"
    assert "packaging" in imported, (
        "the scan no longer sees `packaging` — either the update check stopped "
        "using it, or the scan broke"
    )


def test_every_third_party_import_is_declared_somewhere() -> None:
    declared = _declared()
    undeclared = {
        module: sorted(files)
        for module, files in _imported().items()
        if _normalise(_DIST_FOR_MODULE.get(module, module)) not in declared
        and module not in _UNDECLARED_ON_PURPOSE
    }
    assert not undeclared, (
        "these modules are imported by shipped code but declared by no package "
        "in this repo. They work in a dev checkout because the test and lint "
        "tooling pulls them in; a user's install would not have them, and a "
        "guarded import would turn the feature into a silent no-op: "
        f"{undeclared}"
    )


@pytest.mark.parametrize("module", sorted(_UNDECLARED_ON_PURPOSE))
def test_the_deliberate_exemptions_are_still_imported(module: str) -> None:
    """An exemption for an import that no longer exists is a stale claim.

    It costs nothing to keep and it makes the list read as though someone
    checked. Deleting the import should delete the entry.
    """

    assert module in _imported(), (
        f"{module!r} is exempted here but nothing imports it any more — "
        "delete the exemption"
    )
