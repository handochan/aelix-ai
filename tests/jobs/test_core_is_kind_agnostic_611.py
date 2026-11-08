"""The gate's negative half: the core job mechanism names no kind (#722, ADR-0008 as amended by ADR-0293).

An AST scan over ``aelix_coding_agent/jobs/**``, the kernel inbox
(``harness/inbox.py`` and the inbox methods of ``harness/core.py``), the print
drain (``print_mode._drain_background``, ``_FirstFailure`` and ``_interrupt_flush``) and the TUI's
jobs code (``tui/jobs_ui.py``). It finds no:

* import of ``aelix_agents``, ``tools.bash`` or product-core's own
  ``aelix_coding_agent.agents`` — written as an import statement or as
  ``importlib.import_module`` / ``__import__`` with a literal;
* string equal to a built-in kind name or settings key, or containing
  ``subagent``, ``delegat`` or ``agent_end`` — also when it is put together
  from literals (``'ag' + 'ent'``);
* comparison of a kind-name attribute with a string, or with a name the module
  imported or spells as a constant (``job.kind == DELEGATION_KIND``);
* ``startswith`` / ``endswith`` on a kind-name attribute (a prefix test is a
  branch on the kind);
* ``hasattr`` / ``getattr`` on a runner, an adapter or a registration — duck
  typing a client's object for something its contract does not declare;
* ``isinstance`` against a client class;
* ``Literal`` or ``match`` over kind names;
* ``SpawnGrant`` or ``request_spawn_consent``;
* ``-m aelix_coding_agent`` argv.

Docstrings are prose, not behaviour or wording a model or user reads, and are
skipped (the same rule ``test_p2_band_boundaries._code_only`` applies).
:func:`test_the_scan_catches_what_it_looks_for` proves each rule fires, verify
N1's six evasions included (``.omc/specs/722-verify-r1-probes/probes/ast_evasion.py``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PC = ROOT / "packages/aelix-coding-agent/src/aelix_coding_agent"
KERNEL = ROOT / "packages/aelix-agent-core/src/aelix_agent_core"

_KIND_NAMES = {"agent", "agents", "bash", "delegation", "subagent", "shell"}
_WORDS = re.compile(r"subagent|delegat|agent_end", re.IGNORECASE)
_CLIENT_CLASS = re.compile(r"Delegat|Subagent|Bash|AgentsExtension|PrintChannel")
_KIND_ATTRS = {"kind", "name", "kind_name", "settings_key", "label"}
_CLIENT_MODULES = ("aelix_agents", "aelix_coding_agent.agents")
_CLIENT_OBJECTS = {"runner", "adapter", "registration", "runner_factory"}


def _client_module(name: str) -> bool:
    return name.startswith(_CLIENT_MODULES) or "tools.bash" in name


def _folded(node: ast.AST) -> str | None:
    """A string put together from literals with ``+``, or ``None``."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _names_in(node: ast.AST) -> set[str]:
    return {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Name | ast.Attribute)
    }
_CORE_INBOX_METHODS = {
    "post_notice",
    "pending_notices",
    "undelivered_notices",
    "held_notices",
    "add_notice_listener",
    "notice_changed",
    "is_notice_message",
    "unanswered_notice_at_leaf",
    "prompt_notices",
    "_place_notices",
    "_place_one",
    "_drain_steering",
    "_drain_follow_up",
    "notices_enabled",
    "_require_notices",
}


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def scan(tree: ast.AST, where: str) -> list[str]:
    found: list[str] = []
    skip = _docstrings(tree)
    imported = {
        (a.asname or a.name).split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in n.names
    }
    for node in ast.walk(tree):
        line = f"{where}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.BinOp):
            text = _folded(node)
            if text is not None and (text in _KIND_NAMES or _WORDS.search(text)):
                found.append(f"{line}: kind name put together from literals {text!r}")
        if isinstance(node, ast.Import):
            found += [f"{line}: import {a.name}" for a in node.names if _client_module(a.name)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if (
                _client_module(module)
                or (module.endswith("tools") and any(a.name == "bash" for a in node.names))
                or (module == "aelix_coding_agent" and any(a.name == "agents" for a in node.names))
            ):
                found.append(f"{line}: from {module} import …")
        elif isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
            or (isinstance(node.func, ast.Name) and node.func.id in ("__import__", "import_module"))
        ):
            target = _folded(node.args[0]) if node.args else None
            if target is not None and _client_module(target):
                found.append(f"{line}: dynamic import of {target!r}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("startswith", "endswith")
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in _KIND_ATTRS
        ):
            found.append(f"{line}: prefix test on a kind attribute")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("hasattr", "getattr")
            and node.args
            and _names_in(node.args[0]) & _CLIENT_OBJECTS
        ):
            found.append(f"{line}: {node.func.id} on a client's object")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if node.value in _KIND_NAMES:
                found.append(f"{line}: kind-name string {node.value!r}")
            if _WORDS.search(node.value):
                found.append(f"{line}: client wording {node.value[:40]!r}")
            if "aelix_coding_agent" in node.value and "-m" in node.value:
                found.append(f"{line}: child argv {node.value[:40]!r}")
        elif isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(isinstance(s, ast.Attribute) and s.attr in _KIND_ATTRS for s in sides):
                if any(_folded(s) is not None for s in sides):
                    found.append(f"{line}: compares a kind attribute with a string")
                elif any(
                    isinstance(s, ast.Name) and (s.id in imported or s.id.isupper()) for s in sides
                ):
                    found.append(f"{line}: compares a kind attribute with an imported constant")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance" and len(node.args) == 2:
            names = [n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(node.args[1]) if isinstance(n, ast.Name | ast.Attribute)]
            found += [f"{line}: isinstance against {n}" for n in names if _CLIENT_CLASS.search(n)]
        elif isinstance(node, ast.Name) and node.id in ("SpawnGrant", "request_spawn_consent"):
            found.append(f"{line}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in ("SpawnGrant", "request_spawn_consent"):
            found.append(f"{line}: {node.attr}")
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal":
            consts = [c.value for c in ast.walk(node.slice) if isinstance(c, ast.Constant)]
            found += [f"{line}: Literal over kind name {c!r}" for c in consts if c in _KIND_NAMES]
        elif isinstance(node, ast.Match):
            consts = [c.value for c in ast.walk(node) if isinstance(c, ast.MatchValue) for c in [c.value] if isinstance(c, ast.Constant)]
            found += [f"{line}: match on kind name {c!r}" for c in consts if c in _KIND_NAMES]
    return found


def _functions(path: Path, names: set[str]) -> list[tuple[ast.AST, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name in names:
            out.append((node, f"{path.relative_to(ROOT)}::{node.name}"))
    return out


def _targets() -> list[tuple[ast.AST, str]]:
    files = sorted((PC / "jobs").rglob("*.py")) + [KERNEL / "harness/inbox.py", PC / "tui/jobs_ui.py"]
    targets = [(ast.parse(f.read_text(encoding="utf-8")), str(f.relative_to(ROOT))) for f in files]
    core = _functions(KERNEL / "harness/core.py", _CORE_INBOX_METHODS)
    assert {n.split("::")[1] for _, n in core} == _CORE_INBOX_METHODS, "a scanned kernel method was renamed"
    drain = _functions(
        PC / "modes/print_mode.py", {"_drain_background", "_FirstFailure", "_interrupt_flush"}
    )
    assert len(drain) == 3
    return targets + core + drain


def test_the_core_job_mechanism_names_no_kind() -> None:
    found = [hit for tree, where in _targets() for hit in scan(tree, where)]
    assert found == [], "\n".join(found)


@pytest.mark.parametrize(
    "source",
    [
        "import aelix_agents",
        "from aelix_agents.runtime import x",
        "from aelix_coding_agent.tools.bash import run",
        "from aelix_coding_agent.tools import bash",
        "x = 'bash'",
        "y = 'the subagent finished'",
        "if job.kind == 'whatever': pass",
        "isinstance(x, DelegationRunner)",
        "SpawnGrant()",
        "K = Literal['agent', 'other']",
        "match kind:\n    case 'agent': pass",
        "argv = ['python', '-m aelix_coding_agent']",
        # verify N1's evasions
        "import importlib\nm = importlib.import_module('aelix_agents.tool')",
        "m = __import__('aelix_agents')",
        "from aelix_coding_agent.agents.service import AgentService",
        "import aelix_coding_agent.agents.resolver",
        "from aelix_coding_agent import agents",
        "if job.kind.startswith('agen'): pass",
        "if job.kind == 'ag' + 'ent': pass",
        "x = 'sub' + 'agent'",
        "from x import DELEGATION_KIND\nif job.kind == DELEGATION_KIND: pass",
        "if job.kind == KIND: pass",
        "if hasattr(job.runner, 'child_session_path'): pass",
        "v = getattr(registration.adapter, 'spawn_grant', None)",
    ],
)
def test_the_scan_catches_what_it_looks_for(source: str) -> None:
    assert scan(ast.parse(source), "<probe>") != []


def test_a_docstring_is_prose_and_is_skipped() -> None:
    assert scan(ast.parse('"""Delegation and bash are clients."""\n'), "<probe>") == []
