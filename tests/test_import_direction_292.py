"""#292: no package of this workspace imports one above it (ADR-0015).

``aelix-ai`` <- ``aelix-agent-core`` <- ``aelix-coding-agent`` <- ``aelix-server``
/ ``aelix``. Until this gate nothing held that line: on 7faf4b9a a new
``from aelix_coding_agent... import`` inside the kernel, or an ``aelix_ai``
module importing ``aelix_agent_core``, passed ruff, the type gate and the
structural tests that read the kernel's source. The kernel already had 13 such imports (10 inside functions, 3
under ``TYPE_CHECKING``); ``AgentHarness`` cannot be built without
``aelix_coding_agent``. Those 13 are pinned in
``scripts/check_import_direction.py``'s ``ALLOWLIST``, which the tree must match
exactly: a new site fails until it is listed, a vanished one until its entry is
removed. Inverting them is a follow-up.

This file runs that script's checks on the real tree (the gate proper), then
proves on small throwaway workspaces that each way of breaking the rule is
caught and that the gate reports it in words a contributor can act on.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_import_direction.py"


def _load() -> ModuleType:
    # Registered before exec_module: the script defines a dataclass, which
    # looks its own module up in sys.modules.
    spec = importlib.util.spec_from_file_location("_check_import_direction", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load()

_TOP_LEVEL = {
    "aelix-ai": ("packages/aelix-ai/src", ("aelix_ai",)),
    "aelix-agent-core": ("packages/aelix-agent-core/src", ("aelix_agent_core",)),
    "aelix-coding-agent": (
        "packages/aelix-coding-agent/src",
        ("aelix_coding_agent", "aelix_agents", "aelix_status"),
    ),
    "aelix-server": ("packages/aelix-server/src", ("aelix_server",)),
    "aelix": ("src", ("aelix",)),
}
_DECLARED = {
    "aelix-ai": ["httpx>=0.27,<1.0"],
    "aelix-agent-core": ["aelix-ai==0.1.0b2", "PyYAML>=6.0"],
    "aelix-coding-agent": ["aelix-ai==0.1.0b2", "aelix-agent-core==0.1.0b2"],
    "aelix-server": ["aelix-ai", "aelix-agent-core", "aelix-coding-agent", "anyio>=4"],
    "aelix": ["aelix-ai==0.1.0b2", "aelix-agent-core==0.1.0b2", "aelix-coding-agent==0.1.0b2"],
}


def _workspace(
    tmp_path: Path,
    files: dict[str, str] | None = None,
    declared: dict[str, list[str]] | None = None,
) -> Path:
    """A workspace laid out like this one, with empty packages plus ``files``."""

    root = tmp_path / "ws"
    deps = {**_DECLARED, **(declared or {})}
    for dist, (src, packages) in _TOP_LEVEL.items():
        project = root if dist == "aelix" else root / "packages" / dist
        project.mkdir(parents=True, exist_ok=True)
        lines = ["[project]", f"name = {json.dumps(dist)}", f"dependencies = {json.dumps(deps[dist])}"]
        if dist == "aelix":
            lines += ["", "[tool.uv.workspace]", 'members = ["packages/*"]']
        (project / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for package in packages:
            (root / src / package).mkdir(parents=True, exist_ok=True)
            (root / src / package / "__init__.py").write_text("", encoding="utf-8")
    for rel, text in (files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


_KERNEL_FILE = "packages/aelix-agent-core/src/aelix_agent_core/harness/x.py"
_KERNEL_OTHER = "packages/aelix-agent-core/src/aelix_agent_core/harness/y.py"
_AI_FILE = "packages/aelix-ai/src/aelix_ai/x.py"


# === the gate proper =========================================================


def test_the_tree_holds_exactly_the_allowlisted_reverse_imports() -> None:
    found = gate.problems(gate.scan())
    assert not found, gate.report(found)


def test_what_may_import_what_is_what_the_pyproject_files_declare() -> None:
    found = gate.layering_problems()
    assert not found, gate.report(found)


def test_the_kernel_and_aelix_ai_are_forbidden_every_package_above_them() -> None:
    """The issue's two rules, stated as sets so a rename cannot empty them."""

    forbidden = gate.forbidden_packages()
    above_core = {"aelix_coding_agent", "aelix_agents", "aelix_status", "aelix_server", "aelix"}
    below = {"aelix_ai", "aelix_agent_core", "aelix_coding_agent", "aelix_agents", "aelix_status"}
    assert above_core <= forbidden["aelix-agent-core"]
    assert not forbidden["aelix-agent-core"] & {"aelix_ai", "aelix_agent_core"}
    assert above_core | {"aelix_agent_core"} <= forbidden["aelix-ai"]
    assert "aelix_ai" not in forbidden["aelix-ai"]
    assert {"aelix_server", "aelix"} <= forbidden["aelix-coding-agent"]
    assert not forbidden["aelix-coding-agent"] & below


def test_the_gate_reads_every_distributions_source() -> None:
    """A gate whose glob matched nothing would read as a clean tree."""

    files = gate.source_files()
    floor = {"aelix-ai": 30, "aelix-agent-core": 19, "aelix-coding-agent": 85, "aelix-server": 3, "aelix": 2}
    for dist, minimum in floor.items():
        assert len(files[dist]) >= minimum, (dist, len(files[dist]))
    core = [p.relative_to(REPO).as_posix() for p in files["aelix-agent-core"]]
    assert "packages/aelix-agent-core/src/aelix_agent_core/harness/core.py" in core


def test_the_script_says_ok_on_this_tree() -> None:
    proc = _cli()
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK  import direction:"), proc.stdout
    assert "exactly the ALLOWLIST" in proc.stdout


# === what counts as an import, and of which kind ============================

_HIGHER = frozenset({"aelix_coding_agent", "aelix_server"})
_TC = "from typing import TYPE_CHECKING\n"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "from aelix_coding_agent.extensions.api import A, B as C\n",
            [("module", "aelix_coding_agent.extensions.api", "A"), ("module", "aelix_coding_agent.extensions.api", "B")],
            id="from-import-each-name",
        ),
        pytest.param(
            "import aelix_coding_agent.cli.entry as e\n",
            [("module", "aelix_coding_agent.cli.entry", "")],
            id="plain-import",
        ),
        pytest.param(
            "import os, aelix_coding_agent, aelix_server.app\n",
            [("module", "aelix_coding_agent", ""), ("module", "aelix_server.app", "")],
            id="plain-import-every-name",
        ),
        pytest.param(
            "from aelix_coding_agent import *\n",
            [("module", "aelix_coding_agent", "*")],
            id="star",
        ),
        pytest.param(
            "try:\n    import aelix_server\nexcept ImportError:\n    pass\n",
            [("module", "aelix_server", "")],
            id="guarded-try-is-module",
        ),
        pytest.param(
            "class K:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="class-body-is-module",
        ),
        pytest.param(
            "def f():\n    from aelix_coding_agent import x\n",
            [("function", "aelix_coding_agent", "x")],
            id="def",
        ),
        pytest.param(
            "class K:\n    async def f(self):\n        import aelix_coding_agent\n",
            [("function", "aelix_coding_agent", "")],
            id="async-method",
        ),
        pytest.param(
            "f = lambda: __import__('aelix_coding_agent.cli')\n",
            [("function", "aelix_coding_agent.cli", "__import__()")],
            id="lambda",
        ),
        pytest.param(
            "import importlib\n@register(importlib.import_module('aelix_coding_agent'))\ndef f(x=__import__('aelix_server')):\n    pass\n",
            [("module", "aelix_coding_agent", "import_module()"), ("module", "aelix_server", "__import__()")],
            id="decorator-and-default-run-at-def-time",
        ),
        # --- type-checking guards: typing's flag, and nothing else ---
        pytest.param(
            _TC + "if TYPE_CHECKING:\n    from aelix_coding_agent import x\nelse:\n    from aelix_server import y\n",
            [("type_checking", "aelix_coding_agent", "x"), ("module", "aelix_server", "y")],
            id="type-checking-and-its-else",
        ),
        pytest.param(
            "import typing\nif typing.TYPE_CHECKING:\n    def f():\n        import aelix_coding_agent\n",
            [("type_checking", "aelix_coding_agent", "")],
            id="typing-dot-type-checking-wins-over-def",
        ),
        pytest.param(
            "import typing_extensions as te\nif te.TYPE_CHECKING:\n    import aelix_coding_agent\n",
            [("type_checking", "aelix_coding_agent", "")],
            id="typing-extensions-module-aliased",
        ),
        pytest.param(
            "from typing_extensions import TYPE_CHECKING as TC\nif TC:\n    import aelix_coding_agent\n",
            [("type_checking", "aelix_coding_agent", "")],
            id="typing-extensions-flag-aliased",
        ),
        pytest.param(
            _TC + "def f():\n    if TYPE_CHECKING:\n        import aelix_coding_agent\n    import aelix_server\n",
            [("type_checking", "aelix_coding_agent", ""), ("function", "aelix_server", "")],
            id="type-checking-inside-def",
        ),
        pytest.param(
            _TC + "if not TYPE_CHECKING:\n    import aelix_coding_agent\n",
            [("module", "aelix_coding_agent", "")],
            id="not-type-checking-runs",
        ),
        pytest.param(
            "_EAGER = True\nif _EAGER:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="any-other-flag-runs",
        ),
        pytest.param(
            "TYPE_CHECKING = True\nif TYPE_CHECKING:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="a-local-type-checking-runs",
        ),
        pytest.param(
            "if TYPE_CHECKING:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="type-checking-never-imported-runs",
        ),
        pytest.param(
            "import config\nif config.TYPE_CHECKING:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="another-objects-type-checking-runs",
        ),
        pytest.param(
            "import typing\ntyping.TYPE_CHECKING = True\nif typing.TYPE_CHECKING:\n    from aelix_coding_agent import x\n",
            [("module", "aelix_coding_agent", "x")],
            id="typing-patched-in-this-file-runs",
        ),
        # --- dynamic imports with literals ---
        pytest.param(
            "import importlib.util, pkgutil\n"
            "def f():\n"
            "    importlib.import_module('aelix_coding_agent.extensions.api')\n"
            "    importlib.util.find_spec('aelix_server')\n"
            "    pkgutil.resolve_name('aelix_coding_agent.cli.entry:main')\n"
            "    importlib.import_module(name='aelix_server.app')\n",
            [
                ("function", "aelix_coding_agent.extensions.api", "import_module()"),
                ("function", "aelix_server", "find_spec()"),
                ("function", "aelix_coding_agent.cli.entry", "resolve_name()"),
                ("function", "aelix_server.app", "import_module()"),
            ],
            id="dynamic-imports-with-a-literal",
        ),
        pytest.param(
            "import importlib\nimportlib.import_module('.extensions.api', 'aelix_coding_agent')\n",
            [("module", "aelix_coding_agent.extensions.api", "import_module()")],
            id="relative-name-package-positional",
        ),
        pytest.param(
            "import importlib\nimportlib.import_module('.api', package='aelix_coding_agent.extensions')\n",
            [("module", "aelix_coding_agent.extensions.api", "import_module()")],
            id="relative-name-package-keyword",
        ),
        pytest.param(
            "import importlib.util\nimportlib.util.find_spec('.api', 'aelix_coding_agent.extensions')\n",
            [("module", "aelix_coding_agent.extensions.api", "find_spec()")],
            id="find-spec-package-positional",
        ),
        pytest.param(
            "import importlib.util\nimportlib.util.resolve_name('..api', 'aelix_coding_agent.extensions.sub')\n",
            [("module", "aelix_coding_agent.extensions.api", "resolve_name()")],
            id="two-dots-climb-one-package",
        ),
        pytest.param(
            "import importlib\nimportlib.import_module(name, 'aelix_server')\n",
            [("module", "aelix_server", "import_module()")],
            id="computed-name-literal-package",
        ),
        pytest.param(
            "from importlib import import_module as im\nim('aelix_coding_agent')\n",
            [("module", "aelix_coding_agent", "import_module()")],
            id="aliased-import-module",
        ),
        pytest.param(
            "import importlib as il\nil.import_module('aelix_server')\n",
            [("module", "aelix_server", "import_module()")],
            id="aliased-importlib",
        ),
        pytest.param(
            "from importlib.util import find_spec as fs\ndef f():\n    fs('.api', 'aelix_coding_agent.extensions')\n",
            [("function", "aelix_coding_agent.extensions.api", "find_spec()")],
            id="aliased-find-spec",
        ),
        pytest.param(
            "import importlib\nim = importlib.import_module\nload = im\nload('aelix_coding_agent')\n",
            [("module", "aelix_coding_agent", "import_module()")],
            id="importer-bound-by-assignment",
        ),
        pytest.param(
            '"""Mentions aelix_coding_agent.extensions.api in prose."""\n'
            "from . import sibling\n"
            "from .aelix_coding_agent import x\n"
            "import aelix_coding_agent_extras\n"
            "from aelix_ai import y\n"
            "importlib.import_module(name)\n"
            "importlib.import_module('aelix_ai')\n"
            "importlib.import_module('aelix_ai.x', 'aelix_coding_agent')\n"
            "importlib.import_module('.x', 'aelix_ai')\n"
            "importlib.import_module('.x', __package__)\n"
            "__import__('aelix_ai', 'aelix_coding_agent')\n"
            "__import__(name, 'aelix_coding_agent')\n"
            "load('aelix_coding_agent')\n",
            [],
            id="not-imports-of-a-higher-package",
        ),
        pytest.param(
            "import importlib, runpy, functools\n"
            "getattr(importlib, 'import_module')('aelix_coding_agent')\n"
            "functools.partial(importlib.import_module, 'aelix_coding_agent')()\n"
            "importlib.import_module('aelix_' + 'coding_agent')\n"
            "importlib.import_module('.api', base)\n"
            "runpy.run_module('aelix_coding_agent.cli')\n"
            "exec('import aelix_coding_agent')\n"
            "sys.modules['aelix_coding_agent']\n",
            [],
            id="blind-spots-the-docstring-names",
        ),
    ],
)
def test_each_import_is_found_with_its_kind(source: str, expected: list[tuple[str, str, str]]) -> None:
    found = [(kind, module, name) for _, kind, module, name in gate.find_in_source(source.encode(), "x.py", _HIGHER)]
    assert sorted(found) == sorted(expected)


@pytest.mark.parametrize(
    "rebinding",
    [
        "TYPE_CHECKING = True",
        "TYPE_CHECKING: bool = True",
        "TYPE_CHECKING |= True",
        "(TYPE_CHECKING := True)",
        "for TYPE_CHECKING in (True,): pass",
        "with open(p) as TYPE_CHECKING: pass",
        "def TYPE_CHECKING(): pass",
        "class TYPE_CHECKING: pass",
        "def f(TYPE_CHECKING=True): pass",
        "try:\n    pass\nexcept Exception as TYPE_CHECKING:\n    pass",
        "match v:\n    case TYPE_CHECKING:\n        pass",
        "del TYPE_CHECKING",
        "from config import TYPE_CHECKING",
        "from helpers import *",
    ],
)
def test_a_type_checking_flag_bound_any_other_way_in_the_file_runs(rebinding: str) -> None:
    """Only typing's flag, bound once by its own import, marks code that never runs."""

    source = f"{_TC}{rebinding}\nif TYPE_CHECKING:\n    from aelix_coding_agent import x\n"
    found = [(kind, name) for _, kind, _, name in gate.find_in_source(source.encode(), "x.py", _HIGHER)]
    assert found == [("module", "x")]


# === sabotage: every way of breaking the rule goes red =======================


def test_a_new_function_local_import_in_the_kernel_is_red(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {_KERNEL_FILE: "def f():\n    from aelix_coding_agent.extensions.api import ToolInfo\n"})
    found = gate.problems(gate.scan(root), allowlist={})
    assert len(found) == 1, found
    assert found[0].startswith(f"NEW   {_KERNEL_FILE} line 2 (function):")
    assert "from aelix_coding_agent.extensions.api import ToolInfo" in found[0]
    assert "aelix-agent-core may import only aelix-agent-core, aelix-ai" in found[0]


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("from aelix_coding_agent import x\n", "module"),
        (_TC + "if TYPE_CHECKING:\n    from aelix_coding_agent import x\n", "type_checking"),
        ("import importlib\nimportlib.import_module('aelix_coding_agent.cli')\n", "module"),
        ("import importlib\nimportlib.import_module('.cli', 'aelix_coding_agent')\n", "module"),
        ("from importlib import import_module as im\ndef f():\n    im('aelix_coding_agent')\n", "function"),
        ("import aelix_agents\n", "module"),
        ("import aelix_server\n", "module"),
        ("import aelix\n", "module"),
    ],
)
def test_any_new_kernel_import_of_a_higher_package_is_red(tmp_path: Path, source: str, kind: str) -> None:
    root = _workspace(tmp_path, {_KERNEL_FILE: source})
    found = gate.problems(gate.scan(root), allowlist={})
    assert len(found) == 1 and found[0].startswith("NEW ") and f"({kind})" in found[0], found


def test_a_top_level_module_of_a_higher_distribution_is_forbidden_too(tmp_path: Path) -> None:
    """A distribution's names are its package directories and its top-level ``.py`` modules."""

    root = _workspace(
        tmp_path,
        {"packages/aelix-coding-agent/src/aelix_shim.py": "X = 1\n", _KERNEL_FILE: "import aelix_shim\n"},
    )
    assert "aelix_shim" in gate.forbidden_packages(root)["aelix-agent-core"]
    found = gate.problems(gate.scan(root), allowlist={})
    assert found and found[0].startswith(f"NEW   {_KERNEL_FILE} line 1 (module): import aelix_shim"), found


@pytest.mark.parametrize("module", ["aelix_agent_core", "aelix_coding_agent.cli", "aelix_status"])
def test_aelix_ai_importing_anything_above_it_is_red(tmp_path: Path, module: str) -> None:
    root = _workspace(tmp_path, {_AI_FILE: f"def f():\n    import {module}\n"})
    found = gate.problems(gate.scan(root), allowlist={})
    assert len(found) == 1 and found[0].startswith(f"NEW   {_AI_FILE}"), found
    assert "aelix-ai may import only aelix-ai;" in found[0]


def test_allowed_directions_are_not_reported(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            _KERNEL_FILE: "import aelix_ai\nfrom aelix_agent_core import y\n",
            "packages/aelix-coding-agent/src/aelix_agents/x.py": "import aelix_coding_agent, aelix_status, aelix_agent_core\n",
            "packages/aelix-server/src/aelix_server/x.py": "import aelix_coding_agent\n",
            "src/aelix/x.py": "import aelix_coding_agent, aelix_ai\n",
        },
    )
    assert gate.scan(root) == []


def test_sibling_tops_may_not_import_each_other(tmp_path: Path) -> None:
    """``aelix-server`` and the ``aelix`` umbrella both sit on coding-agent; neither declares the other."""

    root = _workspace(
        tmp_path,
        {"src/aelix/x.py": "import aelix_server\n", "packages/aelix-server/src/aelix_server/x.py": "import aelix\n"},
    )
    assert sorted(s.dist for s in gate.scan(root)) == ["aelix", "aelix-server"]


# === the allowlist: the tree must match it exactly ===========================

_SITE = "def f():\n    from aelix_coding_agent import x\n"
_ENTRY = (_KERNEL_FILE, "function", "aelix_coding_agent", "x")


def test_a_second_site_of_an_allowlisted_import_is_red(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {_KERNEL_FILE: _SITE + "\ndef g():\n    from aelix_coding_agent import x\n"})
    found = gate.problems(gate.scan(root), allowlist={_ENTRY: 1})
    assert any(p.startswith(f"NEW   {_KERNEL_FILE} line 2, 5 (function):") for p in found), found
    assert "found 2, listed 1" in found[0]


def test_an_import_that_is_gone_but_still_listed_is_red(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    found = gate.problems(gate.scan(root), allowlist={_ENTRY: 1})
    assert found == [
        f"GONE  {_KERNEL_FILE} (function): from aelix_coding_agent import x\n      listed 1, found 0"
    ]


def test_one_of_two_listed_sites_gone_is_red(tmp_path: Path) -> None:
    """The count is compared, not only the key: core.py lists ContextUsage twice."""

    root = _workspace(tmp_path, {_KERNEL_FILE: _SITE})
    found = gate.problems(gate.scan(root), allowlist={_ENTRY: 2})
    assert found == [
        f"GONE  {_KERNEL_FILE} (function): from aelix_coding_agent import x\n      listed 2, found 1"
    ]


def test_moving_a_site_by_lines_does_not_matter(tmp_path: Path) -> None:
    """Other lanes move code every day: the key carries no line number."""

    for padding in ("", "\n" * 40):
        root = _workspace(tmp_path / str(len(padding)), {_KERNEL_FILE: padding + _SITE})
        assert gate.problems(gate.scan(root), allowlist={_ENTRY: 1}) == []


def test_a_site_moved_to_another_file_is_red_until_its_entry_follows(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {_KERNEL_OTHER: _SITE})
    found = gate.problems(gate.scan(root), allowlist={_ENTRY: 1})
    assert [p.split()[0] + " " + p.split()[1] for p in found] == [f"NEW {_KERNEL_OTHER}", f"GONE {_KERNEL_FILE}"]
    moved = (_KERNEL_OTHER, *_ENTRY[1:])
    assert gate.problems(gate.scan(root), allowlist={moved: 1}) == []


def test_a_site_demoted_under_type_checking_is_red_until_its_entry_follows(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {_KERNEL_FILE: _TC + "if TYPE_CHECKING:\n    from aelix_coding_agent import x\n"})
    found = gate.problems(gate.scan(root), allowlist={_ENTRY: 1})
    assert [p.split("(")[1].split(")")[0] for p in found] == ["type_checking", "function"], found
    demoted = (_KERNEL_FILE, "type_checking", *_ENTRY[2:])
    assert gate.problems(gate.scan(root), allowlist={demoted: 1}) == []


def test_the_gate_cannot_stop_an_edit_to_the_list_itself(tmp_path: Path) -> None:
    """The limit, pinned so no doc overstates it again: a new site that arrives
    with its own new entry matches the list, and passes. ADR-0015 forbids that
    edit; review of the ALLOWLIST diff is what enforces it."""

    root = _workspace(tmp_path, {_KERNEL_FILE: _SITE})
    assert gate.problems(gate.scan(root), allowlist={_ENTRY: 1}) == []
    assert gate.problems(gate.scan(root), allowlist={}) != []


# === the layering itself ======================================================


def test_declaring_an_upward_dependency_is_red(tmp_path: Path) -> None:
    """Quieting the gate by declaring coding-agent in the kernel is the cycle ADR-0015 forbids."""

    root = _workspace(tmp_path, declared={"aelix-agent-core": ["aelix-ai==0.1.0b2", "aelix-coding-agent==0.1.0b2"]})
    found = gate.layering_problems(root)
    assert (
        "DECLARED  aelix-agent-core: WORKSPACE lets it import ['aelix-ai']; its pyproject"
        " declares ['aelix-agent-core', 'aelix-ai', 'aelix-coding-agent'] - a cycle"
    ) in found, found


def test_a_dependency_dropped_from_a_pyproject_is_red(tmp_path: Path) -> None:
    """Equal, not a subset: a WORKSPACE that allows more than is declared is wrong too."""

    root = _workspace(tmp_path, declared={"aelix-agent-core": ["PyYAML>=6.0"]})
    assert gate.layering_problems(root) == [
        "DECLARED  aelix-agent-core: WORKSPACE lets it import ['aelix-ai']; its pyproject declares []"
    ]


@pytest.mark.parametrize("spelling", ["aelix_coding_agent==0.1.0b2", "Aelix.Coding_Agent>=0", "AELIX-coding-agent[x]; python_version>'3'"])
def test_a_dependency_is_matched_by_its_normalised_name(tmp_path: Path, spelling: str) -> None:
    """PEP 503: these all name aelix-coding-agent, so each is the cycle."""

    root = _workspace(tmp_path, declared={"aelix-agent-core": ["aelix-ai==0.1.0b2", spelling]})
    assert any(p.startswith("DECLARED  aelix-agent-core:") and p.endswith(" - a cycle") for p in gate.layering_problems(root))


def test_an_extra_counts_as_a_declared_dependency(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    pyproject = root / "packages" / "aelix-ai" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + '\n[project.optional-dependencies]\nx = ["aelix-agent-core"]\n',
        encoding="utf-8",
    )
    assert "DECLARED  aelix-ai" in [p.split(":")[0] for p in gate.layering_problems(root)]


def test_a_workspace_member_the_gate_does_not_place_is_red(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    member = root / "packages" / "aelix-web"
    member.mkdir()
    (member / "pyproject.toml").write_text('[project]\nname = "aelix-web"\ndependencies = []\n', encoding="utf-8")
    assert gate.layering_problems(root) == [
        "DECLARED  aelix-web is a workspace distribution WORKSPACE does not place"
    ]


# === the command a contributor runs ==========================================


def test_the_report_says_how_to_fix_each_kind_of_problem(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {_KERNEL_FILE: "from aelix_coding_agent import x\n"})
    proc = _cli("--root", str(root))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"NEW   {_KERNEL_FILE} line 1 (module): from aelix_coding_agent import x" in proc.stderr
    # The real ALLOWLIST names kernel files this throwaway tree does not have.
    assert "GONE  " in proc.stderr
    for words in (
        "NEW       Remove the import.",
        "change its entry's file or kind instead of adding",
        "GONE      The import is gone, or moved",
        "DECLARED  ",
    ):
        assert words in proc.stderr, proc.stderr


def test_the_command_checks_the_layering_too(tmp_path: Path) -> None:
    """The script CONTRIBUTING.md tells contributors to run, not only the test, reports DECLARED."""

    root = _workspace(tmp_path, declared={"aelix-agent-core": ["aelix-ai==0.1.0b2", "aelix-coding-agent==0.1.0b2"]})
    proc = _cli("--root", str(root))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "DECLARED  aelix-agent-core:" in proc.stderr and " - a cycle" in proc.stderr, proc.stderr
