#!/usr/bin/env python3
"""The import-direction gate: a package never imports one that sits above it.

Run: ``python scripts/check_import_direction.py``. ``--list`` also prints every
reverse-import site with its current line; ``--root DIR`` checks another
workspace laid out like this one (the tests' throwaway trees). CI runs it on
every leg through ``tests/test_import_direction_292.py``. Issue #292, ADR-0015
(amendment 2026-10-20). Exit: 0 clean, 1 a problem, each named with its fix.
The OK line counts the reverse-imported names and the distinct (file, line)
pairs they sit on, so two import statements on one line count as one line.

THE RULE. ADR-0015 fixes the direction ``aelix-ai`` <- ``aelix-agent-core`` <-
``aelix-coding-agent`` <- ``aelix-server`` / ``aelix``: the source of a
distribution may import its own top-level packages and those of the workspace
distributions it declares in ``[project]`` dependencies, directly or through
one of them, and no other package of this workspace. ``WORKSPACE`` below states
that graph; ``layering_problems`` holds it equal to what the ``pyproject.toml``
files declare, so declaring an upward dependency to quiet the gate goes red too.
A distribution's forbidden names are the top-level package directories and
``.py`` modules under the source roots of the workspace distributions it does
not declare, read from disk, so a new top-level package needs no edit here.

WHAT COUNTS. Every ``import`` and ``from ... import`` in a distribution's
source tree, in one of three kinds:

* ``module`` - runs when the module is imported (module level, a class body, a
  ``try`` block, any ``if`` that is not a type-checking guard);
* ``function`` - inside a ``def`` or ``lambda``: runs when that code runs. The
  10 runtime imports #292 counted are all of this kind;
* ``type_checking`` - in the body of ``if G:`` where ``G`` is typing's flag: a
  name bound by ``from typing import TYPE_CHECKING`` or ``from
  typing_extensions import TYPE_CHECKING`` (``as`` allowed), or
  ``M.TYPE_CHECKING`` where ``M`` is a name bound by ``import typing`` or
  ``import typing_extensions`` (``as`` allowed) - and only when nothing else in
  that file binds that name: no assignment, ``def``, ``class``, parameter,
  ``for``/``with``/``except``/``match`` target, ``del`` or other import of it,
  no star import, and no assignment to (or ``del`` of) an attribute called
  ``TYPE_CHECKING``.
  Anything else (``TYPE_CHECKING = True``; ``_EAGER = True`` then ``if
  _EAGER:``; ``if TYPE_CHECKING or X:``) is code that runs. The ``else`` of a
  guard runs too. Type-only imports never run, but the kernel's own signatures
  then name the higher package, and a type checker on an install without it
  cannot resolve them. pi's ``check-runtime-deps.mjs`` exempts ``import type``;
  this gate does not, because #292 pinned these three with the rest.

plus a call to ``import_module``, ``find_spec``, ``resolve_name`` or
``__import__`` - by that name under any receiver (``importlib.import_module``,
``il.import_module``), or through a name this file binds to one in these ways
only: a ``from ... import ... as`` alias (``from importlib import import_module
as im``), or an importer assigned to a bare name - plain (``im =
importlib.import_module``, each target of ``a = b = ...``), annotated (``im: T
= ...``) or ``(im := ...)`` with ``im(...)`` called later - and chains of those
(``load = im``). One table per file, so a local that shadows such a name is
read as the importer too. The module it names is read from string literals: the
name (first positional or ``name=``; for ``resolve_name`` the part before a
``:``) when it is absolute; a relative name resolved against the package (second
positional or ``package=``; not for ``__import__``, whose second argument is
``globals``) when that is a literal; and, when the name is not a literal, the
package literal on its own. ``__import__``'s ``level`` is not read: its name is
always taken as absolute. ``importlib.util.resolve_name`` only builds a name
and imports nothing; it is counted all the same. A relative ``import``
statement cannot leave its own top-level package, so it is not read.

Decorators and default values of a ``def`` or ``lambda`` get the kind of the
code around it, since they run when it runs; so do a ``def``'s annotations, and
a variable annotation gets the kind of the code it sits in. For annotations that
over-reports: under ``from __future__ import annotations`` or on Python 3.14
(PEP 649) an annotation is not evaluated where it sits, and one on a variable
inside a function is never evaluated, yet an import call in an annotation is
labelled as code that runs.

WHAT IT DOES NOT SEE - the forms known not to be seen, stated rather than
hidden (what reviews found; not a proof that nothing else exists): a module
name built at run time (an f-string, a concatenation, a variable) with no
package literal beside it, or a relative name whose package is not a literal;
an importer bound any other way than the ones above - by tuple or list
unpacking (``im, _ = importlib.import_module, None``), as a ``for`` or
``with`` target, as a parameter default (``def f(im=importlib.import_module)``),
or called in place through ``:=`` (``(im := importlib.import_module)(...)``);
an importer reached any other way (``getattr(importlib, "import_module")``,
``functools.partial``, a container of callables, an attribute such as
``self.im``, a name bound to one in another file and imported from there);
``__import__`` with ``level`` >= 1 (fifth positional or ``level=``), whose
relative name is read as absolute (``__import__("extensions.api",
{"__package__": "aelix_coding_agent"}, None, [], 1)``); a call other than the
four importers that imports a package or module it is given by name
(``importlib.resources.files`` and the ``importlib.resources`` helpers that take
a package, ``pkgutil.get_data``, ``pydoc.locate``, and the like); ``runpy``, a
loader or spec driven by hand; a ``sys.modules`` lookup; an entry-point string;
``exec``/``eval``; a ``TYPE_CHECKING`` changed where the AST does not show it
(``globals()``, ``setattr``, ``exec``, another module patching ``typing``); and
``.pyi`` stubs (there are none).

THE ALLOWLIST IS THE WHOLE RULE. On 7faf4b9a ``aelix-agent-core`` held 13 such
import statements (18 imported names), all of ``aelix_coding_agent``; the
other distributions held none. ``ALLOWLIST`` pins them by file, kind, module
and imported name - never by line, so lines added or removed around a site do
not matter - with a count, so a second site of the same import is a new site.
The tree must match it exactly: a new site fails until it is listed, and a
listed site that is gone fails until its entry is removed (or its count
lowered). Nothing else is compared. The gate cannot stop a change that edits
``ALLOWLIST`` itself; it makes every such change a diff to this file that a
reviewer sees. The policy (ADR-0015) is that the list only shrinks: an entry is
deleted or lowered when its import goes, and changes its file or kind only to
follow a site that moved to another file of the same distribution or under a
type-checking guard. Adding an entry to let a new import pass is what ADR-0015
forbids.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: distribution -> (its source root, relative to the repo; the workspace
#: distributions it declares, directly or transitively). ADR-0015.
WORKSPACE: dict[str, tuple[str, tuple[str, ...]]] = {
    "aelix-ai": ("packages/aelix-ai/src", ()),
    "aelix-agent-core": ("packages/aelix-agent-core/src", ("aelix-ai",)),
    "aelix-coding-agent": (
        "packages/aelix-coding-agent/src",
        ("aelix-ai", "aelix-agent-core"),
    ),
    "aelix-server": (
        "packages/aelix-server/src",
        ("aelix-ai", "aelix-agent-core", "aelix-coding-agent"),
    ),
    "aelix": ("src", ("aelix-ai", "aelix-agent-core", "aelix-coding-agent")),
}

_IMPORTERS = frozenset({"import_module", "__import__", "find_spec", "resolve_name"})
_TYPING = frozenset({"typing", "typing_extensions"})

_CORE = "packages/aelix-agent-core/src/aelix_agent_core/harness/"
_API = "aelix_coding_agent.extensions.api"

#: (file, kind, module, imported name) -> count. Exactly the reverse imports the
#: tree has. Delete or lower an entry when its import goes; change its file or
#: kind only to follow a site that moved or was demoted. Never add one to let a
#: new import pass - remove the import instead (ADR-0015, amendment 2026-10-20).
ALLOWLIST: dict[tuple[str, str, str, str], int] = {
    # The harness, its extension runner and its hook bus annotate with the
    # extension types.
    (_CORE + "_extension_runner.py", "type_checking", _API, "Extension"): 1,
    (_CORE + "_extension_runner.py", "type_checking", _API, "ExtensionSourceInfo"): 1,
    (_CORE + "_extension_runner.py", "type_checking", _API, "RegisteredCommand"): 1,
    (_CORE + "_extension_runner.py", "type_checking", _API, "_ExtensionRuntime"): 1,
    (_CORE + "core.py", "type_checking", _API, "Extension"): 1,
    (_CORE + "core.py", "type_checking", _API, "ExtensionContext"): 1,
    (_CORE + "core.py", "type_checking", _API, "_ExtensionRuntime"): 1,
    (_CORE + "hooks.py", "type_checking", _API, "ExtensionContext"): 1,
    # AgentHarness.__init__: the default runtime and the bind_core action table.
    (_CORE + "core.py", "function", _API, "_ExtensionRuntime"): 1,
    (_CORE + "core.py", "function", _API, "ExtensionRuntimeActions"): 1,
    (_CORE + "core.py", "function", _API, "_make_throwing_stub"): 1,
    # export_to_html, the context-usage readers, the context and command-context
    # factories, and the get_commands / get_all_tools views.
    (_CORE + "core.py", "function", "aelix_coding_agent._export_html", "export_html"): 1,
    (_CORE + "core.py", "function", _API, "ContextUsage"): 2,
    (_CORE + "core.py", "function", _API, "ExtensionContext"): 1,
    (
        _CORE + "core.py",
        "function",
        "aelix_coding_agent.extensions.command_context",
        "ExtensionCommandContext",
    ): 1,
    (_CORE + "core.py", "function", _API, "SlashCommandInfo"): 1,
    (_CORE + "core.py", "function", _API, "ToolInfo"): 1,
}


@dataclass(frozen=True)
class Site:
    """One imported name of a higher distribution, at one place in the tree."""

    dist: str
    file: str  # repo-relative, forward slashes on every platform
    line: int
    kind: str
    module: str
    name: str  # "" for ``import a.b``; "*" for a star; "import_module()" etc.

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.file, self.kind, self.module, self.name)

    def statement(self) -> str:
        if self.name.endswith("()"):
            return f"{self.name[:-2]}({self.module!r})"
        if not self.name:
            return f"import {self.module}"
        return f"from {self.module} import {self.name}"


def _type_checking_guards(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """The names in this file bound to typing's ``TYPE_CHECKING`` flag, and those
    bound to the ``typing`` / ``typing_extensions`` modules - each only when
    nothing else in the file binds it (the module docstring lists what counts)."""

    flags: set[str] = set()
    modules: set[str] = set()
    other: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return frozenset(), frozenset()  # it may bind any name
                if not node.level and node.module in _TYPING and alias.name == "TYPE_CHECKING":
                    flags.add(alias.asname or alias.name)
                else:
                    other.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _TYPING:
                    modules.add(alias.asname or alias.name)
                else:
                    other.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            other.add(node.id)
        elif isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            if node.attr == "TYPE_CHECKING":
                return frozenset(), frozenset()  # ``typing.TYPE_CHECKING = True``
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other.add(node.name)
        elif isinstance(node, ast.arg):
            other.add(node.arg)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            other.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            other.add(node.rest)
    return frozenset(flags - other - modules), frozenset(modules - other - flags)


def _importer_names(tree: ast.Module) -> dict[str, str]:
    """Each name in this file that calls an importer -> that importer."""

    names = {name: name for name in _IMPORTERS}

    def importer_of(value: ast.expr | None) -> str | None:
        if isinstance(value, ast.Name):
            return names.get(value.id)
        if isinstance(value, ast.Attribute) and value.attr in _IMPORTERS:
            return value.attr
        return None

    grew = True
    while grew:  # ``a = import_module`` then ``b = a``: until nothing new is bound
        grew = False
        for node in ast.walk(tree):
            bound: list[tuple[str, str]] = []
            if isinstance(node, ast.ImportFrom):
                bound = [(a.asname, a.name) for a in node.names if a.asname and a.name in _IMPORTERS]
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                importer = importer_of(node.value)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if importer:
                    bound = [(t.id, importer) for t in targets if isinstance(t, ast.Name)]
            for alias, importer in bound:
                if alias not in names:
                    names[alias] = importer
                    grew = True
    return names


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dynamic_target(called: str, node: ast.Call) -> str | None:
    """The module a call to an importer names with string literals, if any."""

    keywords = {k.arg: k.value for k in node.keywords if k.arg}
    name = _literal(node.args[0] if node.args else keywords.get("name"))
    package = None
    if called != "__import__":  # ``__import__``'s second argument is ``globals``
        package = _literal(node.args[1] if len(node.args) > 1 else keywords.get("package"))
    if name is None:
        return package  # the name is computed; a literal package still says where
    name = name.split(":")[0]  # ``pkgutil.resolve_name("pkg.mod:attr")``
    if not name.startswith("."):
        return name  # absolute: the package argument is ignored
    if package is None:
        return None
    level = len(name) - len(name.lstrip("."))
    base = package.rsplit(".", level - 1)[0]
    return f"{base}.{name[level:]}" if name[level:] else base


class _Finder(ast.NodeVisitor):
    def __init__(self, forbidden: frozenset[str], tree: ast.Module) -> None:
        self.forbidden = forbidden
        self.flags, self.typing_modules = _type_checking_guards(tree)
        self.importers = _importer_names(tree)
        self.type_checking = 0
        self.functions = 0
        self.found: list[tuple[int, str, str, str]] = []

    def _kind(self) -> str:
        if self.type_checking:
            return "type_checking"
        return "function" if self.functions else "module"

    def _higher(self, module: str) -> bool:
        return module.split(".")[0] in self.forbidden

    def _is_guard(self, test: ast.expr) -> bool:
        if isinstance(test, ast.Name):
            return test.id in self.flags
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in self.typing_modules
        )

    def visit_If(self, node: ast.If) -> None:
        if not self._is_guard(node.test):
            self.generic_visit(node)
            return
        self.visit(node.test)
        self.type_checking += 1
        for child in node.body:
            self.visit(child)
        self.type_checking -= 1
        for child in node.orelse:  # the ``else`` of a guard runs
            self.visit(child)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        # Decorators and defaults run where the ``def`` runs. Annotations are
        # read the same way, which over-reports where they are not evaluated
        # then (module docstring, WHAT COUNTS). Only the body waits for a call.
        body = node.body if isinstance(node.body, list) else [node.body]
        for child in ast.iter_child_nodes(node):
            if child not in body:
                self.visit(child)
        self.functions += 1
        for child in body:
            self.visit(child)
        self.functions -= 1

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_Lambda = _scope

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._higher(alias.name):
                self.found.append((node.lineno, self._kind(), alias.name, ""))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module or not self._higher(node.module):
            return
        for alias in node.names:
            self.found.append((node.lineno, self._kind(), node.module, alias.name))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        called = None
        if isinstance(func, ast.Attribute) and func.attr in _IMPORTERS:
            called = func.attr
        elif isinstance(func, ast.Name):
            called = self.importers.get(func.id)
        if called:
            module = _dynamic_target(called, node)
            if module and self._higher(module):
                self.found.append((node.lineno, self._kind(), module, f"{called}()"))
        self.generic_visit(node)


def top_level_packages(src: Path) -> frozenset[str]:
    """The import names a source root provides: its package dirs and modules."""

    names: set[str] = set()
    for child in src.iterdir() if src.is_dir() else ():
        if child.name.startswith((".", "__pycache__")) or child.name.endswith("-info"):
            continue
        if child.is_dir():
            names.add(child.name)
        elif child.suffix == ".py":
            names.add(child.stem)
    return frozenset(names)


def find_in_source(source: bytes, filename: str, forbidden: frozenset[str]) -> list[tuple[int, str, str, str]]:
    """``(line, kind, module, name)`` for every import of ``forbidden`` in ``source``."""

    with warnings.catch_warnings():
        # 3.12+ warns on an invalid escape in a string literal; that is not ours to judge.
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source, filename=filename)
    finder = _Finder(forbidden, tree)
    finder.visit(tree)
    return finder.found


def forbidden_packages(root: Path = REPO_ROOT) -> dict[str, frozenset[str]]:
    """Each distribution -> the top-level packages of this workspace it may not import."""

    provides = {dist: top_level_packages(root / src) for dist, (src, _) in WORKSPACE.items()}
    return {
        dist: frozenset().union(*(names for d, names in provides.items() if d not in {dist, *allowed}))
        for dist, (_, allowed) in WORKSPACE.items()
    }


def source_files(root: Path = REPO_ROOT) -> dict[str, list[Path]]:
    """Each distribution -> every ``.py`` file under its source root."""

    return {
        dist: sorted(p for p in (root / src).rglob("*.py") if "__pycache__" not in p.parts)
        for dist, (src, _) in WORKSPACE.items()
    }


def scan(root: Path = REPO_ROOT) -> list[Site]:
    """Every site in the workspace where a distribution imports one above it."""

    forbidden = forbidden_packages(root)
    sites: list[Site] = []
    for dist, paths in source_files(root).items():
        for path in paths:
            rel = path.relative_to(root).as_posix()
            for line, kind, module, name in find_in_source(path.read_bytes(), rel, forbidden[dist]):
                sites.append(Site(dist, rel, line, kind, module, name))
    return sites


def _requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def declared_closure(root: Path = REPO_ROOT) -> dict[str, frozenset[str]]:
    """Each workspace distribution -> the workspace distributions it declares,
    transitively, from ``[project]`` dependencies and optional-dependencies
    (dependency groups are development-only and do not count)."""

    projects = {root / "pyproject.toml"}
    workspace = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for pattern in workspace.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", []):
        projects.update(p / "pyproject.toml" for p in root.glob(pattern) if (p / "pyproject.toml").is_file())
    direct: dict[str, set[str]] = {}
    for pyproject in projects:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        requirements = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            requirements.extend(extra)
        direct[_requirement_name(project["name"])] = {_requirement_name(r) for r in requirements}
    names = set(direct)
    closure: dict[str, frozenset[str]] = {}
    for dist in names:
        seen: set[str] = set()
        stack = [d for d in direct[dist] if d in names]
        while stack:
            dep = stack.pop()
            if dep not in seen:
                seen.add(dep)
                stack.extend(d for d in direct[dep] if d in names)
        closure[dist] = frozenset(seen)
    return closure


HOW_TO_FIX = """\
How to fix (ADR-0015, #292). ALLOWLIST is in scripts/check_import_direction.py.
  NEW       Remove the import. Let the higher package hand the lower one what it
            needs (a Protocol or a callback it passes in, as bind_core does), or
            move the shared type down into the lower package. If you moved or
            demoted a listed import (another file of the same package, or under
            if TYPE_CHECKING), change its entry's file or kind instead of adding
            one; a GONE line names that entry. Adding an entry for a new import
            is the change ADR-0015 forbids: the list only shrinks.
  GONE      The import is gone, or moved (see NEW). Delete or lower its entry in
            the same change, so the list keeps matching the tree.
  DECLARED  WORKSPACE and the pyproject files disagree. An upward dependency is
            the cycle ADR-0015 forbids; if the layering itself changed, amend
            ADR-0015 and WORKSPACE in the same change."""


def problems(sites: list[Site], allowlist: dict[tuple[str, str, str, str], int] | None = None) -> list[str]:
    """Where the tree and ``allowlist`` differ, in either direction."""

    allowlist = ALLOWLIST if allowlist is None else allowlist
    out: list[str] = []
    found: dict[tuple[str, str, str, str], list[Site]] = {}
    for site in sites:
        found.setdefault(site.key, []).append(site)

    for key, here in sorted(found.items()):
        listed = allowlist.get(key, 0)
        if len(here) > listed:
            site = here[0]
            _, allowed = WORKSPACE[site.dist]
            lines = ", ".join(str(s.line) for s in here)
            out.append(
                f"NEW   {site.file} line {lines} ({site.kind}): {site.statement()}\n"
                f"      {site.dist} may import only {', '.join(sorted({site.dist, *allowed}))};"
                f" found {len(here)}, listed {listed}"
            )
    for key, listed in sorted(allowlist.items()):
        here = len(found.get(key, ()))
        if here < listed:
            file, kind, module, name = key
            shown = Site("", file, 0, kind, module, name).statement()
            out.append(f"GONE  {file} ({kind}): {shown}\n      listed {listed}, found {here}")
    return out


def layering_problems(root: Path = REPO_ROOT) -> list[str]:
    """Where ``WORKSPACE`` and the declared dependencies disagree."""

    declared = declared_closure(root)
    out: list[str] = []
    for dist, (src, allowed) in WORKSPACE.items():
        if not (root / src).is_dir():
            out.append(f"DECLARED  {dist}: its source root {src} does not exist")
        if declared.get(dist) != frozenset(allowed):
            closure = declared.get(dist, frozenset())
            out.append(
                f"DECLARED  {dist}: WORKSPACE lets it import {sorted(allowed)}; its"
                f" pyproject declares {sorted(closure)}" + (" - a cycle" if dist in closure else "")
            )
    for dist in sorted(set(declared) - set(WORKSPACE)):
        out.append(f"DECLARED  {dist} is a workspace distribution WORKSPACE does not place")
    return out


def report(found: list[str]) -> str:
    return f"import-direction gate: {len(found)} problem(s)\n\n" + "\n".join(found) + "\n\n" + HOW_TO_FIX


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--list", action="store_true", help="print every reverse-import site with its line"
    )
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="the workspace to check (default: this one)"
    )
    args = parser.parse_args(argv)

    sites = scan(args.root)
    if args.list:
        for site in sites:
            print(f"{site.file}:{site.line}  {site.kind:<13}  {site.statement()}")
    found = layering_problems(args.root) + problems(sites)
    if found:
        print(report(found), file=sys.stderr)
        return 1
    # Distinct (file, line) pairs: two import statements on one line count once.
    lines = len({(s.file, s.line) for s in sites})
    print(
        f"OK  import direction: {len(sites)} reverse-imported name(s) on {lines}"
        " line(s), exactly the ALLOWLIST (ADR-0015, #292)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
