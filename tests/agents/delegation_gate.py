"""The delegation filter's structural gate (#795, #796, ADR-0304 D5).

NOT COLLECTED: no ``test_`` prefix and no test functions. The test
(``tests/agents/test_delegation_filter_gate_795.py``) feeds :func:`check` the
real tree and, as its self-test, synthetic trees held as strings there, so the
positive controls run in CI on every push.

WHAT IT IS: structural regression protection, not a proof that every
model-originated start is checked (Codex 2, opus 2). It fails when a profile
source, an identity object or a start sink appears outside its named place, or
when the object a sink runs can come from anywhere but ``resolve_for_model``.
It does NOT follow calls through helpers outside its rules, data through
containers, ``exec``/``eval``, or code outside the two packages (an
extension's tool, an RPC host, a shell ``aelix --agent``); the door tests
(``tests/agents_ext/test_delegation_filter_795_796.py``) and ADR-0304's Limits
carry those.

F3's refusal is checked by SHAPE only (verify r2 F2): an ``if`` between the
re-check and the first sink whose test reads a name the re-check's statement
binds and whose body ends in ``return``. A refusal that can never fire passes
it — ``if not still_the_same and False:``, ``still_the_same = True`` before
the ``if``, a ``DelegationRefused`` handler that sets ``True`` — and the door
tests that turn a profile off between the hook and execute catch those. Also
outside M2's string forms (ADR-0304 Limits): ``operator.methodcaller("<sink>",
…)``, ``inspect.getattr_static``, ``vars(<module>)["<name>"]``, and
``object.__setattr__(pending, "resolved", …)``.

MATCHING (every rule):

* M1 — loads, not calls: a reference is an ``ast.Name`` in ``Load`` context
  whose id is the gated name or an import alias of it, or an
  ``ast.Attribute`` in ``Load`` context whose ``attr`` is the gated name. A
  call, ``f = runtime.resolve_profile``, ``functools.partial(...)`` and a
  reference passed as an argument all count. (Exception: :data:`_ATTR_ONLY`
  names are matched as attributes only — ``_run`` and ``spawn`` are common
  local names.)
* M2 — string forms: ``getattr(_, "<gated>")``, ``operator.attrgetter("<gated>")``
  and, inside ``aelix_agents``, ``importlib.import_module`` /
  ``__import__`` of ``aelix_coding_agent.agents.discovery`` or ``.profile``
  are violations wherever they appear.
* M3 — receivers: ``X.resolve_profile`` whose ``X`` is a name bound to the
  discovery MODULE is the discovery function; any other receiver is the
  runtime method.
* Import aliases are resolved: ``from dataclasses import replace as swap``
  makes ``swap(...)`` B1's ``dataclasses.replace``, ``import copy as c`` makes
  ``c.copy(...)`` ``copy.copy``, and ``from operator import attrgetter as ag``
  makes ``ag(...)`` M2's ``attrgetter``. A function reached through a plain
  assignment (``g = getattr``) is not followed.
* M4 — where: ``file::Class.method``, ``file::function``, nested definitions
  by their full qualname (so a nested helper cannot hide a load); module
  level is ``file::<module>``.
* Constructors match ``ast.Call`` only.

A rule's expected set is compared with what the given sources hold, file by
file: an expected entry in a file the sources do not include is not required
(the self-tests give a few files). The real-tree test also requires every file
an expected set names to be present, so a rename fails here.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

AGENTS = "aelix_agents"
CORE = "aelix_coding_agent"
_DISCOVERY = "aelix_coding_agent.agents.discovery"
_PROFILE = "aelix_coding_agent.agents.profile"

#: Matched as attributes only (M1's exception): these are common local names.
_ATTR_ONLY = frozenset({"_run", "spawn", "_spawn", "spawn_granted", "resume_granted"})


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


@dataclass(frozen=True)
class Rule:
    """One exact set: where *names* may be loaded inside *scope*."""

    rule: str
    scope: tuple[str, ...]
    names: tuple[str, ...]
    expected: frozenset[str]
    kind: str = "load"
    """``load`` (M1), ``call`` (constructors), ``runtime_resolve`` (M3's
    non-discovery receiver), ``discovery_resolve`` (M3's discovery receiver or
    an imported ``resolve_profile``), ``replace`` (B1's copy forms)."""
    line: int = 0


def _rule(
    rule: str,
    scope: tuple[str, ...],
    names: tuple[str, ...],
    expected: set[str],
    kind: str = "load",
) -> Rule:
    frame = inspect.currentframe()
    line = frame.f_back.f_lineno if frame is not None and frame.f_back is not None else 0
    return Rule(rule, scope, names, frozenset(expected), kind, line)


BOTH = (AGENTS, CORE)

# THE TABLE. Each line is one exact set; a failure names the line.
# ``aelix_agents`` paths are relative to that package, product-core ones to its.
RULES: tuple[Rule, ...] = (
    _rule("A1", (AGENTS,), ("discover_profiles",), {"runtime.py::delegation_for"}),
    _rule(
        "A2",
        (AGENTS,),
        ("resolve_profile",),
        {"runtime.py::_SubagentRuntimeImpl.resolve_profile"},
        "discovery_resolve",
    ),
    _rule(
        "A3",
        (AGENTS,),
        ("complete_profile",),
        {"runtime.py::_SubagentRuntimeImpl.resolve_for_model"},
    ),
    _rule(
        "A3",
        (AGENTS,),
        ("load_profile_file", "parse_profile", "_scan_dir", "AgentProfileService"),
        set(),
    ),
    _rule("B1", BOTH, ("ResolvedProfile",), {"aelix_agents/runtime.py::_resolved"}, "call"),
    _rule("B1", BOTH, ("replace", "copy"), set(), "replace"),
    # B2-B4 hold across BOTH packages (verify r2 F1): product-core builds none of
    # these identity objects, and ``ext._spawn`` / ``facade.start`` from there are
    # not themselves gated (D4 is aelix_agents' only), so the object is the door.
    _rule(
        "B2",
        BOTH,
        ("PendingSpawn",),
        {
            "aelix_agents/extension.py::AgentsExtension._on_tool_call",
            "aelix_agents/extension.py::AgentsExtension._approve_resume",
        },
        "call",
    ),
    _rule("B3", BOTH, ("_Launch",), {"aelix_agents/extension.py::AgentsExtension._spawn"}, "call"),
    # B4 — the one ``ResolvedProfile`` constructor helper (verify r2 F1, X6): only
    # the runtime's two resolvers call it, so B1's exact set cannot be walked
    # around by calling the helper from somewhere else.
    _rule(
        "B4",
        BOTH,
        ("_resolved",),
        {
            "aelix_agents/runtime.py::_SubagentRuntimeImpl.resolve_profile",
            "aelix_agents/runtime.py::_SubagentRuntimeImpl.resolve_for_model",
        },
    ),
    _rule("C1", (AGENTS,), ("resolve_profile",), set(), "runtime_resolve"),
    _rule(
        "C2",
        (AGENTS,),
        ("resolve_for_model",),
        {
            "extension.py::AgentsExtension._on_tool_call",
            "extension.py::AgentsExtension._approve_resume",
            "extension.py::AgentsExtension._execute_pending",
        },
    ),
    _rule(
        "C3",
        (AGENTS,),
        ("delegation_for", "delegation"),
        {
            "extension.py::AgentsExtension._roster",
            "runtime.py::_SubagentRuntimeImpl.delegation",
            "runtime.py::_SubagentRuntimeImpl.resolve_for_model",
            "runtime.py::_SubagentRuntimeImpl._listed_predicate.listed",
        },
    ),
    # D1-D3, D5, D7 and D8 hold across BOTH packages (Codex 5 r1, verify r2 F1):
    # product-core loads none of these today. D4's ``._spawn`` is aelix_agents' only:
    # product-core has four unrelated ``._spawn`` methods (``tui/descriptors.py``,
    # ``tools/bash_background.py``), so an exact set there would list them.
    _rule(
        "D1",
        BOTH,
        ("spawn_granted",),
        {
            "aelix_agents/extension.py::AgentsExtension._execute_pending",
            "aelix_agents/batch.py::_member",
            "aelix_agents/detach.py::product_allowlist",
        },
    ),
    _rule(
        "D2",
        BOTH,
        ("resume_granted",),
        {
            "aelix_agents/extension.py::AgentsExtension._execute_pending",
            "aelix_agents/detach.py::product_allowlist",
        },
    ),
    _rule(
        "D3",
        BOTH,
        ("run_batch",),
        {"aelix_agents/extension.py::AgentsExtension._execute_pending"},
    ),
    _rule("D4", (AGENTS,), ("_spawn",), {"extension.py::AgentsExtension._execute_pending"}),
    _rule(
        "D5",
        BOTH,
        ("_run",),
        {
            "aelix_agents/runtime.py::_SubagentRuntimeImpl._spawn_consented",
            "aelix_agents/runtime.py::_SubagentRuntimeImpl.spawn_granted",
            "aelix_agents/runtime.py::_SubagentRuntimeImpl.resume_granted",
            "aelix_agents/jobs_client.py::DelegationRunner.run",
            "aelix_agents/detach.py::product_allowlist",
        },
    ),
    _rule("D6", (AGENTS,), ("spawn",), set()),
    _rule(
        "D7",
        BOTH,
        ("_execute_pending",),
        {"aelix_agents/extension.py::AgentsExtension._execute"},
    ),
    # D8 — the user-typed door's consented launch (verify r2 F1, X5): ``spawn``
    # (``/agents run``, RPC ``agents/run``) is its one caller in both packages.
    _rule(
        "D8",
        BOTH,
        ("_spawn_consented",),
        {"aelix_agents/runtime.py::_SubagentRuntimeImpl.spawn"},
    ),
    _rule(
        "E1",
        (CORE,),
        ("resolve_profile",),
        {"tui/commands.py::_agents_run", "rpc/control.py::RpcControlPlane.run_agent"},
        "runtime_resolve",
    ),
    _rule(
        "E2",
        (CORE,),
        ("spawn",),
        {"tui/commands.py::_agents_run", "rpc/control.py::RpcControlPlane.run_agent"},
    ),
    _rule("E3", (CORE,), ("resolve_for_model",), set()),
    _rule(
        "E4",
        BOTH,
        ("delegation_view",),
        {
            "aelix_agents/runtime.py::delegation_for",
            "aelix_coding_agent/agents/service.py::AgentProfileService.delegation",
        },
    ),
)

#: M2's names, by package: every name a rule of that package gates.
_STRING_NAMES: dict[str, frozenset[str]] = {
    AGENTS: frozenset(
        n for r in RULES if AGENTS in r.scope and r.kind != "replace" for n in r.names
    ),
    CORE: frozenset(n for r in RULES if CORE in r.scope and r.kind != "replace" for n in r.names),
}

_SINKS = ("spawn_granted", "resume_granted", "run_batch", "_spawn")  # D1-D4


def expected_files() -> set[str]:
    """Every source file an expected set names, as ``<package>/<path>``."""

    out: set[str] = set()
    for rule in RULES:
        for where in rule.expected:
            path = where.split("::", 1)[0]
            if len(rule.scope) == 1:
                path = f"{rule.scope[0]}/{path}"
            out.add(path)
    return out


# === Reading one file =======================================================


@dataclass
class _File:
    package: str
    short: str
    tree: ast.Module
    aliases: dict[str, str] = field(default_factory=dict)
    """Local name → the gated symbol name it was imported as."""
    discovery_modules: set[str] = field(default_factory=set)
    """Local names bound to the discovery MODULE (M3)."""
    qualified: dict[str, str] = field(default_factory=dict)
    """Local name → ``module.name`` for every ``from module import name [as local]``."""
    modules: dict[str, str] = field(default_factory=dict)
    """Local name → module for every ``import module [as local]``."""


def _absolute(module: str | None, level: int, path: str) -> str:
    if level == 0:
        return module or ""
    parts = path.removesuffix(".py").split("/")[:-1]
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    return ".".join([*base, module] if module else base)


def _read(path: str, text: str) -> _File:
    package, _, short = path.partition("/")
    tree = ast.parse(text, filename=path)
    file = _File(package, short, tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _absolute(node.module, node.level, path)
            for alias in node.names:
                local = alias.asname or alias.name
                if f"{module}.{alias.name}" == _DISCOVERY:
                    file.discovery_modules.add(local)
                file.aliases[local] = alias.name
                file.qualified[local] = f"{module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _DISCOVERY and alias.asname:
                    file.discovery_modules.add(alias.asname)
                if alias.asname:
                    file.modules[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    file.modules[head] = head
    return file


def _walk(node: ast.AST, qual: list[str]) -> Iterator[tuple[ast.AST, str]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _walk(child, [*qual, child.name])
            continue
        yield child, ".".join(qual) or "<module>"
        yield from _walk(child, qual)


def _name_of(file: _File, node: ast.AST) -> str | None:
    """The gated symbol a ``Name`` load stands for (its id, or what it was imported as)."""

    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return file.aliases.get(node.id, node.id)
    return None


def _receiver_is_discovery(file: _File, node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id in file.discovery_modules


def _matches(rule: Rule, file: _File, node: ast.AST) -> bool:
    if rule.kind == "call":
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        name = (
            _name_of(file, func)
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        return name in rule.names
    if rule.kind == "replace":
        return _is_copy_of_resolved(file, node)
    if rule.kind == "discovery_resolve":
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            return node.attr == "resolve_profile" and _receiver_is_discovery(file, node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return file.aliases.get(node.id) == "resolve_profile"
        return False
    if rule.kind == "runtime_resolve":
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr == "resolve_profile"
            and not _receiver_is_discovery(file, node)
        )
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        return node.attr in rule.names
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        name = _name_of(file, node)
        return name in rule.names and name not in _ATTR_ONLY
    return False


#: B1's copy forms, by the function's qualified name.
_COPY_FORMS = frozenset({"dataclasses.replace", "copy.copy", "copy.replace", "copy.deepcopy"})


def _qualified_call(file: _File, func: ast.AST) -> str | None:
    """``module.name`` of a call's function through the file's imports (Codex 5).

    ``swap`` after ``from dataclasses import replace as swap`` is
    ``dataclasses.replace``; ``c.copy`` after ``import copy as c`` is
    ``copy.copy``. A bare name that no import binds is itself.
    """

    if isinstance(func, ast.Name):
        return file.qualified.get(func.id, func.id)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        owner = func.value.id
        return f"{file.modules.get(owner, file.qualified.get(owner, owner))}.{func.attr}"
    return None


def _is_copy_of_resolved(file: _File, node: ast.AST) -> bool:
    """B1's copy forms: ``dataclasses.replace`` / ``copy.copy`` / ``copy.replace`` /
    ``copy.deepcopy``, by any import alias, or a bare ``replace``, whose first
    argument is a name or attribute ending in ``resolved``."""

    if not isinstance(node, ast.Call) or not node.args:
        return False
    called = _qualified_call(file, node.func)
    if called not in _COPY_FORMS and called != "replace":
        return False
    first = node.args[0]
    text = (
        first.id
        if isinstance(first, ast.Name)
        else (first.attr if isinstance(first, ast.Attribute) else "")
    )
    return text.endswith("resolved")


def _where(rule: Rule, file: _File, qual: str) -> str:
    return (
        f"{file.package}/{file.short}::{qual}" if len(rule.scope) > 1 else f"{file.short}::{qual}"
    )


# === The checks =============================================================


def check(sources: Mapping[str, str]) -> list[Violation]:
    """Every rule over *sources* (``<package>/<path>`` → text). ``[]`` is clean."""

    files = [_read(path, text) for path, text in sorted(sources.items())]
    violations: list[Violation] = []
    present = {f"{f.package}/{f.short}" for f in files}
    found: dict[int, set[str]] = {i: set() for i in range(len(RULES))}
    for file in files:
        for node, qual in _walk(file.tree, []):
            for index, rule in enumerate(RULES):
                if file.package in rule.scope and _matches(rule, file, node):
                    found[index].add(_where(rule, file, qual))
            violations.extend(_string_forms(file, node, qual))
    for index, rule in enumerate(RULES):
        expected = {
            e
            for e in rule.expected
            if (
                f"{rule.scope[0]}/{e.split('::', 1)[0]}"
                if len(rule.scope) == 1
                else e.split("::", 1)[0]
            )
            in present
        }
        if found[index] != expected:
            violations.append(
                Violation(
                    rule.rule,
                    f"{'/'.join(rule.names)} ({rule.kind}) is loaded in {sorted(found[index])}; "
                    f"expected exactly {sorted(expected)} "
                    f"(tests/agents/delegation_gate.py line {rule.line})",
                )
            )
    by_path = {f"{f.package}/{f.short}": f for f in files}
    violations.extend(_dataflow(by_path))
    return violations


def _string_forms(file: _File, node: ast.AST, qual: str) -> list[Violation]:
    """M2: a gated name reached by a string."""

    if not isinstance(node, ast.Call):
        return []
    func = node.func
    name = (
        file.aliases.get(func.id, func.id)
        if isinstance(func, ast.Name)
        else (func.attr if isinstance(func, ast.Attribute) else "")
    )
    where = f"{file.package}/{file.short}::{qual}"
    strings = [
        a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]
    gated = _STRING_NAMES.get(file.package, frozenset())
    if name == "getattr" and len(node.args) >= 2:
        second = node.args[1]
        if isinstance(second, ast.Constant) and second.value in gated:
            return [Violation("M2", f"getattr(_, {second.value!r}) in {where}")]
    if name == "attrgetter" and any(s.split(".")[-1] in gated for s in strings):
        return [Violation("M2", f"attrgetter({strings}) in {where}")]
    if (
        file.package == AGENTS
        and name in ("import_module", "__import__")
        and any(
            s in (_DISCOVERY, _PROFILE) or s.endswith((".discovery", ".profile")) for s in strings
        )
    ):
        return [Violation("M2", f"{name}({strings}) in {where}")]
    return []


# === Dataflow (F1-F5) =======================================================


def _function(file: _File | None, qual: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if file is None:
        return None
    node: ast.AST = file.tree
    for part in qual.split("."):
        nxt = next(
            (
                c
                for c in ast.iter_child_nodes(node)
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and c.name == part
            ),
            None,
        )
        if nxt is None:
            return None
        node = nxt
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _own_nodes(fn: ast.AST) -> Iterator[ast.AST]:
    """Every node of *fn*'s own scope (nested functions, lambdas and classes left out)."""

    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _own_nodes(child)


def _nested_scopes(fn: ast.AST) -> Iterator[ast.AST]:
    for node in ast.walk(fn):
        if node is not fn and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node


def _target_names(target: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _bindings(fn: ast.AST, name: str) -> list[ast.AST]:
    """Every statement or expression of *fn*'s own scope that binds *name*."""

    out: list[ast.AST] = []
    for node in _own_nodes(fn):
        names: set[str] = set()
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names |= _target_names(target)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor)):
            names |= _target_names(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            names |= _target_names(node.optional_vars)
        elif (
            isinstance(node, ast.ExceptHandler)
            and node.name
            or isinstance(node, (ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                names |= _target_names(target)
        elif isinstance(node, (ast.comprehension,)):
            continue  # a comprehension's target is its own scope
        if name in names:
            out.append(node)
    return out


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    names = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    return names


def _declared_outside(fn: ast.AST, name: str) -> bool:
    """``global name`` in *fn*, or ``nonlocal name`` in a scope nested in it."""

    for node in ast.walk(fn):
        if isinstance(node, ast.Global) and name in node.names:
            return True
        if isinstance(node, ast.Nonlocal) and name in node.names:
            return True
    return False


def _is_call_of(node: ast.AST | None, attr: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    )


def _attr_of(node: ast.AST | None, owner: str, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == owner
    )


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _dataflow(by_path: Mapping[str, _File]) -> list[Violation]:
    out: list[Violation] = []
    extension = by_path.get(f"{AGENTS}/extension.py")
    if extension is not None:
        for qual in ("AgentsExtension._on_tool_call", "AgentsExtension._approve_resume"):
            out.extend(_f1(extension, qual))
        out.extend(_f2_f3(extension))
        out.extend(_f4_spawn(extension))
    batch = by_path.get(f"{AGENTS}/batch.py")
    if batch is not None:
        out.extend(_f4_batch(batch))
    jobs = by_path.get(f"{AGENTS}/jobs_client.py")
    if jobs is not None:
        out.extend(_f5(jobs))
    return out


def _f1(file: _File, qual: str) -> list[Violation]:
    """F1: every ``PendingSpawn(resolved=n)`` reads a local only ``resolve_for_model`` binds."""

    fn = _function(file, qual)
    where = f"extension.py::{qual}"
    if fn is None:
        return [Violation("F1", f"{where} is gone; the rule must move with it")]
    out: list[Violation] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            _name_of(file, func)
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else None)
        )
        if called != "PendingSpawn":
            continue
        value = _kw(node, "resolved")
        if not isinstance(value, ast.Name):
            out.append(
                Violation("F1", f"PendingSpawn in {where} does not pass resolved=<a local name>")
            )
            continue
        name = value.id
        if name in _params(fn) or _declared_outside(fn, name):
            out.append(
                Violation("F1", f"{where}: {name!r} is a parameter or declared global/nonlocal")
            )
            continue
        binds = _bindings(fn, name)
        if not binds:
            out.append(Violation("F1", f"{where}: {name!r} is never bound here"))
            continue
        for bind in binds:
            good = (
                isinstance(bind, ast.Assign)
                and len(bind.targets) == 1
                and isinstance(bind.targets[0], ast.Name)
                and _is_call_of(bind.value, "resolve_for_model")
            ) or (
                isinstance(bind, ast.AnnAssign)
                and isinstance(bind.target, ast.Name)
                and _is_call_of(bind.value, "resolve_for_model")
            )
            if not good:
                line = getattr(bind, "lineno", "?")
                out.append(
                    Violation(
                        "F1",
                        f"{where}: {name!r} is bound at line {line} by something other than "
                        "`<expr>.resolve_for_model(...)`",
                    )
                )
    return out


def _loads_sink(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in _SINKS:
            return True
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id == "run_batch":
            return True
    return False


def _f2_f3(file: _File) -> list[Violation]:
    qual = "AgentsExtension._execute_pending"
    fn = _function(file, qual)
    where = f"extension.py::{qual}"
    if fn is None:
        return [Violation("F2", f"{where} is gone; the rule must move with it")]
    out: list[Violation] = []
    # F2 — ``pending`` is a parameter never rebound, and every sink runs it.
    if "pending" not in _params(fn) or _bindings(fn, "pending") or _declared_outside(fn, "pending"):
        out.append(Violation("F2", f"{where}: 'pending' is not a parameter, or is rebound"))
    calls = {id(n.func): n for n in ast.walk(fn) if isinstance(n, ast.Call)}
    for sub in ast.walk(fn):
        sink = (
            sub.attr
            if isinstance(sub, ast.Attribute) and sub.attr in _SINKS
            else ("run_batch" if isinstance(sub, ast.Name) and sub.id == "run_batch" else None)
        )
        if sink is None:
            continue
        call = calls.get(id(sub))
        if call is None:
            out.append(Violation("F2", f"{where}: {sink} is loaded without being called"))
            continue
        if sink in ("spawn_granted", "resume_granted"):
            ok = (
                len(call.args) >= 2
                and _attr_of(call.args[0], "pending", "grant")
                and _attr_of(call.args[1], "pending", "resolved")
            )
        elif sink == "run_batch":
            ok = _attr_of(_kw(call, "grant"), "pending", "grant") and _attr_of(
                _kw(call, "resolved"), "pending", "resolved"
            )
        else:
            ok = (
                bool(call.args)
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "pending"
            )
        if not ok:
            out.append(
                Violation(
                    "F2",
                    f"{where}: {sink} at line {call.lineno} runs something other than `pending`",
                )
            )
    # F3 — one re-check, as a statement of the body, before every sink, then its refusal.
    rechecks = [n for n in ast.walk(fn) if _is_call_of(n, "resolve_for_model")]
    if len(rechecks) != 1 or not _attr_of_chain(rechecks[0].args[0] if rechecks[0].args else None):
        out.append(
            Violation(
                "F3", f"{where}: expected exactly one resolve_for_model(pending.call.profile)"
            )
        )
        return out
    body = fn.body
    holder = next(
        (i for i, stmt in enumerate(body) if any(n is rechecks[0] for n in ast.walk(stmt))), None
    )
    first_sink = next((i for i, stmt in enumerate(body) if _loads_sink(stmt)), None)
    if (
        holder is None
        or first_sink is None
        or holder >= first_sink
        or not _runs_unconditionally(body[holder], rechecks[0])
    ):
        out.append(
            Violation(
                "F3",
                f"{where}: the re-check is not a statement of the body, run on every path, "
                "before every sink",
            )
        )
        return out
    bound: set[str] = set()
    for node in ast.walk(body[holder]):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    refusal = any(
        isinstance(stmt, ast.If)
        and {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)} & bound
        and stmt.body
        and isinstance(stmt.body[-1], ast.Return)
        for stmt in body[holder + 1 : first_sink]
    )
    if not refusal:
        out.append(
            Violation(
                "F3", f"{where}: no `if <what the re-check bound>: ... return` before the sinks"
            )
        )
    return out


def _runs_unconditionally(stmt: ast.stmt, call: ast.AST) -> bool:
    """Whether *call* is reached every time *stmt* runs (dominance inside one statement).

    ``<name> = <call>`` with the call as the WHOLE right-hand side, or a
    ``try`` whose BODY holds that statement directly: a branch, a loop, a
    ``with``, a handler, an ``else`` or a ``finally`` on the way is not. Line
    order across nested blocks is not dominance (Codex 2). Inside one
    expression, ``pending.resolved or <call>`` and ``x if True else <call>``
    can skip the call (Codex 2 r1), so a call that is only PART of the value
    does not count: the rule does not analyse an expression's control flow.
    """

    if isinstance(stmt, ast.Try):
        return any(_runs_unconditionally(inner, call) for inner in stmt.body)
    if isinstance(stmt, ast.Assign):
        return (
            stmt.value is call and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        )
    if isinstance(stmt, ast.AnnAssign):
        return stmt.value is call and isinstance(stmt.target, ast.Name)
    return False


def _attr_of_chain(node: ast.AST | None) -> bool:
    """``pending.call.profile``."""

    return (
        isinstance(node, ast.Attribute)
        and node.attr == "profile"
        and _attr_of(node.value, "pending", "call")
    )


def _f4_spawn(file: _File) -> list[Violation]:
    qual = "AgentsExtension._spawn"
    fn = _function(file, qual)
    where = f"extension.py::{qual}"
    if fn is None:
        return [Violation("F4", f"{where} is gone; the rule must move with it")]
    out: list[Violation] = []
    if "pending" not in _params(fn) or _bindings(fn, "pending") or _declared_outside(fn, "pending"):
        out.append(Violation("F4", f"{where}: 'pending' is not a parameter, or is rebound"))
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and _name_of(file, node.func) == "_Launch"
            and not (
                _attr_of(_kw(node, "grant"), "pending", "grant")
                and _attr_of(_kw(node, "resolved"), "pending", "resolved")
            )
        ):
            out.append(
                Violation("F4", f"{where}: _Launch is not built from pending.grant/resolved")
            )
    return out


def _f4_batch(file: _File) -> list[Violation]:
    out: list[Violation] = []
    member = _function(file, "_member")
    if member is None:
        return [Violation("F4", "batch.py::_member is gone; the rule must move with it")]
    for node in ast.walk(member):
        if _is_call_of(node, "spawn_granted"):
            assert isinstance(node, ast.Call)
            if not (
                len(node.args) >= 2
                and _attr_of(node.args[0], "batch", "grant")
                and _attr_of(node.args[1], "batch", "resolved")
            ):
                out.append(
                    Violation(
                        "F4", "batch.py::_member: spawn_granted does not run batch.grant/resolved"
                    )
                )
    runner = _function(file, "run_batch")
    if runner is None:
        return [*out, Violation("F4", "batch.py::run_batch is gone; the rule must move with it")]
    params = _params(runner)
    for node in ast.walk(runner):
        if isinstance(node, ast.Call) and _name_of(file, node.func) == "_Batch":
            grant, resolved = _kw(node, "grant"), _kw(node, "resolved")
            ok = (
                isinstance(grant, ast.Name)
                and grant.id == "grant"
                and isinstance(resolved, ast.Name)
                and resolved.id == "resolved"
                and {"grant", "resolved"} <= params
                and not _bindings(runner, "grant")
                and not _bindings(runner, "resolved")
            )
            if not ok:
                out.append(
                    Violation(
                        "F4", "batch.py::run_batch: _Batch is not built from its own parameters"
                    )
                )
    return out


def _f5(file: _File) -> list[Violation]:
    fn = _function(file, "frozen_host")
    if fn is None:
        return [Violation("F5", "jobs_client.py::frozen_host is gone; the rule must move with it")]
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _name_of(file, node.func) == "SubagentHost":
            keys = {k.arg for k in node.keywords}
            if keys & {"agent_lists", "active_profile"}:
                return [
                    Violation(
                        "F5",
                        "jobs_client.py::frozen_host wires agent_lists/active_profile; the job "
                        "runner resolves nothing, and A1-A3/C1-C3 must say so first",
                    )
                ]
    return []


__all__ = ["RULES", "AGENTS", "CORE", "Rule", "Violation", "check", "expected_files"]
