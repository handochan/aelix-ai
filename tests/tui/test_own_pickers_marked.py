"""#399: every aelix picker passes ``own=True``, and nothing else does.

``AelixTUIContext.select`` / ``confirm`` default to an extension's or the
model's question (an extension's ``ctx.ui.select`` reaches that object
itself), so aelix's own pickers are the ones marked. A picker that loses its
``own=True`` changes layout and holds keys it used to take; review round 2's
verify removed it from ``/trust`` and from the ``/model`` partial and every
test stayed green. The descriptor confirm carries an extension's text and must
NOT be marked (the same round's v-descriptor-confirm-own stayed green too).

Review round 3 (verify round 2, blocking): this table read ``tui/shell.py``
only, so the two aelix questions that reach ``runtime.ui.select`` from
``tui/commands.py`` (``/extension new``'s placement question and ``/agents
run``'s project-agent confirm) went unmarked, wrapped and held Enter, and no
row saw them. It now reads EVERY product source file.

One row per call site, read from the syntax trees: the file, the enclosing
function, what is called, and the ``own=True`` of each such call in order.
What is called is ``context.select`` / ``context.confirm`` (shell.py's TUI
context), ``partial(context.…)`` (handed to a picker module), a call through
``select_declared`` or ``select_with_cancel`` (``extensions/ext_ui.py``, how
aelix reaches ``runtime.ui.select``), any ``<x>.ui.select`` / ``.confirm``
attribute call, and a bare ``select(...)`` / ``confirm(...)`` (a callable a
caller handed in, or one fetched from ``runtime.ui``). The last row fails when
a call is added or removed anywhere without this table moving with it.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "packages"

#: (file, enclosing function, call) -> ``own=True`` of each such call, in order.
#: The file is relative to ``aelix_coding_agent/`` (or to ``src/`` for the
#: other modules).
_EXPECTED: dict[tuple[str, str, str], list[bool]] = {
    # tui/shell.py: aelix's own pickers, on the TUI context.
    ("tui/shell.py", "_resume_session", "context.select"): [True],  # /resume
    ("tui/shell.py", "_settings_theme_action", "context.select"): [True],  # theme picker
    ("tui/shell.py", "_open_settings", "context.select"): [True],  # /settings (#404 too)
    ("tui/shell.py", "_open_model_picker", "partial(context.select)"): [True],  # /model
    ("tui/shell.py", "_open_thinking_picker", "partial(context.select)"): [True],  # /thinking
    ("tui/shell.py", "_open_trust", "context.select"): [True],  # /trust
    ("tui/shell.py", "_open_login", "partial(context.select)"): [True],  # /login
    ("tui/shell.py", "_open_login", "partial(context.confirm)"): [True],
    ("tui/shell.py", "_open_logout", "partial(context.select)"): [True],  # /logout
    ("tui/shell.py", "_open_logout", "partial(context.confirm)"): [True],
    ("tui/shell.py", "_ask_session_contended", "context.select"): [True],  # session in use
    ("tui/shell.py", "_wire_descriptors", "context.confirm"): [False],  # an extension's text
    # tui/commands.py: aelix's own questions through runtime.ui.select (review round 3).
    ("tui/commands.py", "_confirm_project_agent_for_run", "select_declared"): [True],
    ("tui/commands.py", "_extension_new", "select_declared"): [True],
    # The model's words: spawn consent and the permission gate's fallback. Not
    # own; they name their own cancel rows.
    ("aelix_agents/consent.py", "_ask", "select_with_cancel"): [False],
    ("builtin/permission.py", "_prompt", "select_with_cancel"): [False],
    # extensions/ext_ui.py: the helpers themselves.
    ("extensions/ext_ui.py", "select_declared", "select()"): [False],  # **passed
    ("extensions/ext_ui.py", "select_with_cancel", "select_declared"): [False],
    # Callables handed in: own is decided where shell.py builds them (above).
    ("tui/descriptors.py", "_dispatch_with_confirm", "confirm()"): [False],  # _wire_descriptors
    ("tui/login_wizard.py", "run_login", "select()"): [False],
    ("tui/login_wizard.py", "_run_oauth", "select()"): [False],
    ("tui/login_wizard.py", "on_select", "select()"): [False],
    ("tui/login_wizard.py", "_run_api_key", "select()"): [False, False],
    ("tui/login_wizard.py", "_run_custom", "select()"): [False],
    ("tui/login_wizard.py", "run_logout", "select()"): [False],
    ("tui/login_wizard.py", "run_logout", "confirm()"): [False],
    ("tui/model_picker.py", "run_model_picker", "select()"): [False],
    ("tui/thinking_picker.py", "run_thinking_picker", "select()"): [False],
}

_HELPERS = ("select_declared", "select_with_cancel")


def _short(path: Path) -> str:
    parts = path.relative_to(_SRC).parts  # <package>, "src", <module>, ...
    rel = Path(*parts[3:]) if parts[2] == "aelix_coding_agent" else Path(*parts[2:])
    return rel.as_posix()


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _HELPERS:
        return func.id
    if isinstance(func, ast.Name) and func.id in ("select", "confirm"):
        return f"{func.id}()"
    if isinstance(func, ast.Attribute) and func.attr in ("select", "confirm"):
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id == "context":
            return f"context.{func.attr}"
        if (isinstance(owner, ast.Attribute) and owner.attr == "ui") or (
            isinstance(owner, ast.Name) and owner.id == "ui"
        ):
            return f"ui.{func.attr}"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "partial"
        and call.args
        and isinstance(call.args[0], ast.Attribute)
        and isinstance(call.args[0].value, ast.Name)
        and call.args[0].value.id == "context"
        and call.args[0].attr in ("select", "confirm")
    ):
        return f"partial(context.{call.args[0].attr})"
    return None


@functools.cache
def _sites() -> dict[tuple[str, str, str], list[bool]]:
    found: dict[tuple[str, str, str], list[bool]] = {}

    def own_of(call: ast.Call) -> bool:
        return any(
            k.arg == "own" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in call.keywords
        )

    def visit(node: ast.AST, short: str, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = function
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                callee = _callee(child)
                if callee is not None:
                    found.setdefault((short, function, callee), []).append(own_of(child))
            if (
                isinstance(child, ast.keyword)
                and child.arg in ("select", "confirm")
                and isinstance(child.value, ast.Attribute)
                and isinstance(child.value.value, ast.Name)
                and child.value.value.id == "context"
            ):
                # ``select=context.select`` hands the bare method on: unmarked.
                key = (short, function, f"bare context.{child.value.attr}")
                found.setdefault(key, []).append(False)
            visit(child, short, name)

    for path in sorted(_SRC.glob("*/src/**/*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), _short(path), "<module>")
    return found


def test_the_scan_reads_every_product_package() -> None:
    files = {_short(p) for p in _SRC.glob("*/src/**/*.py")}
    assert {"tui/shell.py", "tui/commands.py", "aelix_agents/consent.py"} <= files
    assert any(f.startswith("aelix_server/") for f in files)
    assert any(f.startswith("aelix_agent_core/") for f in files)


@pytest.mark.parametrize(("site", "own"), sorted(_EXPECTED.items()))
def test_each_picker_call_site_passes_own_as_expected(
    site: tuple[str, str, str], own: list[bool]
) -> None:
    sites = _sites()
    assert site in sites, f"{site} is gone: move this table with it"
    assert sites[site] == own, f"{site[2]} in {site[0]}:{site[1]} must pass own={own}"


def test_no_picker_call_site_is_missing_from_the_table() -> None:
    assert _sites() == _EXPECTED
