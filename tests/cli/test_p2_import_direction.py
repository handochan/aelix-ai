"""ADR-0197 §(a) — the import-direction gate between band 2 and band 3.

The 3-band rule is a placement claim, and half of it is a DIRECTION claim: the
bundled ``aelix_agents`` extension may import ``aelix_coding_agent`` freely (it
reuses ``profile_to_argv``, ``has_trust_requiring_project_resources``,
``PermissionMode``, the contract dataclasses…), while product-core may name
``aelix_agents`` at exactly ONE site — a function-level import inside
``cli/entry.py::_async_main``.

That single exception is what keeps the coupling one-way in every way that
matters. A module-scope import would make band 3 a hard dependency of every
startup path, so a broken or absent extension package would brick the CLI for
users who have delegation switched off (which is everyone by default in P2), and
it would let the compiler-visible dependency graph point backwards even though
the runtime one does not.

Structural on purpose (finding I9 moved this file from WS-A to WS-E, where the
code it asserts on lives): it reads the source tree, so it stays meaningful in
any checkout and after the branch merges.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC = REPO_ROOT / "packages" / "aelix-coding-agent" / "src"
PRODUCT_CORE_SRC = PACKAGE_SRC / "aelix_coding_agent"
EXTENSION_SRC = PACKAGE_SRC / "aelix_agents"

_THE_ONE_SITE = "cli/entry.py"
"""The single product-core file allowed to name ``aelix_agents`` (plan §2a)."""


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_names(tree: ast.AST) -> list[tuple[ast.stmt, str]]:
    """Every ``import``/``from`` statement paired with its root module name."""

    found: list[tuple[ast.stmt, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node, alias.name.split(".")[0]))
        # ``level > 0`` is a relative import and can never reach a sibling
        # top-level package, so it is irrelevant here by construction.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node, node.module.split(".")[0]))
    return found


def _module_scope_imports(tree: ast.Module) -> set[str]:
    """Root module names imported at MODULE scope (``if TYPE_CHECKING:`` counts).

    A ``TYPE_CHECKING`` guard would be safe at runtime, but naming band 3 in a
    product-core type annotation is still the wrong direction — the contract
    exists precisely so product-core never has to.
    """

    names: set[str] = set()
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            # Descend through ``if TYPE_CHECKING:`` / ``try: import`` wrappers,
            # which are still module scope. Deliberately NOT descending into
            # FunctionDef / ClassDef — a function-local import is the exception
            # this whole file is about.
            stack.extend(node.body)
            stack.extend(node.orelse)
            for handler in getattr(node, "handlers", []):
                stack.extend(handler.body)
            stack.extend(getattr(node, "finalbody", []))
    return names


def test_product_core_imports_aelix_agents_at_one_site_only() -> None:
    """Exactly one product-core file names ``aelix_agents``, inside a function."""

    offenders: list[str] = []
    sites: list[str] = []
    for path in _python_files(PRODUCT_CORE_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        if "aelix_agents" in _module_scope_imports(tree):
            offenders.append(f"{rel}: module-scope import of aelix_agents")
            continue
        for node, root in _imported_names(tree):
            if root == "aelix_agents":
                sites.append(f"{rel}:{node.lineno}")

    assert offenders == [], (
        "product-core must never import the bundled extension at module scope "
        f"(ADR-0197 §(a)): {offenders}"
    )
    assert len(sites) == 1, (
        "exactly one product-core site may name aelix_agents; found "
        f"{len(sites)}: {sites}"
    )
    assert sites[0].startswith(_THE_ONE_SITE), (
        f"the one aelix_agents import must live in {_THE_ONE_SITE}, not "
        f"{sites[0]}"
    )


def test_the_one_site_is_inside_a_function_body() -> None:
    """It is ``_async_main``'s local import, not a top-of-file one.

    Pinned separately from the count above because the count alone would stay
    green if somebody hoisted the import to module scope inside the same file —
    which is exactly the regression that would make a broken ``aelix_agents``
    brick startup for every user who has delegation off.
    """

    path = PRODUCT_CORE_SRC / "cli" / "entry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    enclosing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(root == "aelix_agents" for _, root in _imported_names(node)):
            enclosing.append(node.name)

    assert enclosing == ["_async_main"], (
        "the aelix_agents import must sit in cli/entry.py::_async_main; found "
        f"{enclosing}"
    )


def test_the_one_site_is_guarded_so_a_broken_extension_cannot_brick_startup() -> None:
    """The lazy import is wrapped in ``try:`` — degradation, not a traceback.

    P2 ships delegation default-off, so the overwhelmingly common launch never
    reaches this import at all; but a user who switched ``[features] agents`` on
    and then broke their environment must get a warning and a working CLI, not a
    dead one.
    """

    path = PRODUCT_CORE_SRC / "cli" / "entry.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for _, root in _imported_names(ast.Module(body=node.body, type_ignores=[])):
            if root == "aelix_agents":
                guarded = True

    assert guarded, "the aelix_agents import in cli/entry.py must be in a try block"


def test_aelix_agents_may_import_product_core() -> None:
    """The permitted direction, asserted so nobody "fixes" it symmetrically.

    A future reader seeing the gate above could reasonably conclude the two
    packages must not know each other at all. They must: band 3 is built ON band
    2 and reuses its resolver, its trust predicate and its permission enum. What
    is banned is the reverse edge.
    """

    importers: list[str] = []
    for path in _python_files(EXTENSION_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if "aelix_coding_agent" in _module_scope_imports(tree):
            importers.append(path.relative_to(EXTENSION_SRC).as_posix())

    assert importers, (
        "aelix_agents is expected to build ON product-core; finding zero "
        "imports means the package moved or this gate is pointed at nothing"
    )


def test_product_core_never_imports_the_extensions_private_modules() -> None:
    """Not even the ONE site may reach past ``aelix_agents``'s public name.

    ``aelix_agents/__init__.py`` exports exactly ``AgentsExtension``. Everything
    else — ``SpawnGrant`` above all — is an implementation detail product-core
    must not learn, because learning it is how consent policy leaks back into
    band 2 (``test_protocol_has_no_consent_parameter``).
    """

    reached: list[str] = []
    for path in _python_files(PRODUCT_CORE_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(PRODUCT_CORE_SRC).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module.startswith("aelix_agents."):
                    reached.append(f"{rel}:{node.lineno} -> {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("aelix_agents."):
                        reached.append(f"{rel}:{node.lineno} -> {alias.name}")

    assert reached == [], (
        "product-core may only import the package root's public name: " f"{reached}"
    )
