"""``cli/entry.py`` must actually LOAD the status extension (#101).

WITHOUT THE WIRING THIS WHOLE TRACK IS DEAD CODE. ``aelix_status`` ships in the
wheel, imports cleanly, passes every test in ``tests/status/`` — and no user ever
sees the tool, because nothing constructs :class:`StatusExtension` or puts it in
``prepend_extensions``. That failure is silent in exactly the way this repo keeps
getting caught by.

AST, NOT A SUBSTRING SEARCH, and that is the load-bearing decision here. Every
identifier this file looks for is ALREADY PRESENT in ``entry.py`` for unrelated
reasons — ``app_mode`` appears eight times, ``prepend_extensions`` twice,
``project_trusted`` seven times — so a ``"app_mode=" in source`` check reads as
CLOSED against a tree where nothing was wired. The parse below asserts on
structure: this parameter on THIS function, this call inside THIS body, this
keyword at THIS call site.

NAMES ARE NOT ENOUGH, which is the M3 review finding this file was rewritten
for. The first revision asserted only that the keyword NAMES were present, so
all three arguments could be sabotaged with it green. Measured, one sabotage at
a time, against the revision that shipped at ``130acfd`` — 5 tests in this file,
5 green every time::

    mode=app_mode                            -> mode="interactive"      5 passed
    extensions=captured_extensions           -> extensions=[]           5 passed
    project_trusted=lambda: project_trusted  -> lambda: True            5 passed

The first was re-run over the whole ``tests/status/`` directory as well: 36
passed, i.e. nothing anywhere else saw it either.

The third is a security-relevant FAIL-OPEN: the whole point of
``resolve_project_trusted_fail_closed`` is that only an explicitly RESOLVED
decision counts, and ``lambda: True`` re-introduces exactly the default the
snapshot refuses to read off ``ctx.is_project_trusted()``. So every argument is
now pinned by VALUE, compared as normalised source through :func:`ast.unparse`
— which ignores whitespace, line breaks and comments while keeping the
expression exact.

WHAT IT DOES NOT ASSERT. That the wiring works — ``tests/status/test_status_tool.py``
covers behaviour against a real harness. This file only pins that the three
edits exist, are in the right places and pass the right expressions, because
``entry.py`` is held by another track and the edits land separately from this
package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aelix_coding_agent.cli.entry as _entry
import pytest

ENTRY_PATH = Path(_entry.__file__).resolve()
FACTORY = "_build_harness_options"
PREPEND_LIST = "prepend_extensions"
EXT_CLASS = "StatusExtension"
EXT_MODULE = "aelix_status"

# The three values ``StatusExtension`` cannot obtain from an ``ExtensionContext``
# and must therefore be handed, each mapped to the EXPRESSION that must supply
# it (normalised through ``ast.unparse``). Named here so a wiring that drops one
# — or quietly replaces one with a constant — fails loudly rather than shipping
# a snapshot that reports ``mode="unknown"``, an empty extension list, or a
# ``project_trusted`` nobody resolved.
#
# Each value is the ONLY honest source, and none of the three is substitutable:
#
# ``app_mode``
#     the single ``resolve_app_mode(parsed, stdin_is_tty)`` result, threaded in
#     from ``_async_main``. Re-deriving it inside the factory would sample a
#     stdin TTY state that has already changed.
# ``lambda: project_trusted``
#     a callable over the RESOLVED decision, read live. A bare ``True`` is the
#     fail-open; a non-callable would freeze a ``/trust`` flip out of the answer.
# ``captured_extensions``
#     the live holder ``_build_harness_options`` refills on every harness
#     rebuild — held BY REFERENCE, which is what makes ``/reload`` visible. A
#     list literal or a copy reports the startup set forever.
REQUIRED_KWARG_VALUES = {
    "mode": "app_mode",
    "project_trusted": "lambda: project_trusted",
    "extensions": "captured_extensions",
}
REQUIRED_KWARGS = set(REQUIRED_KWARG_VALUES)


@pytest.fixture(scope="module")
def entry_tree() -> ast.Module:
    return ast.parse(ENTRY_PATH.read_text(encoding="utf-8"), filename=str(ENTRY_PATH))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{name} not found in {ENTRY_PATH}")


def _status_constructions(scope: ast.AST) -> list[ast.Call]:
    """Every ``StatusExtension(...)`` call inside ``scope``."""

    return [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == EXT_CLASS
    ]


def test_entry_imports_the_status_extension(entry_tree: ast.Module) -> None:
    imports = [
        node
        for node in ast.walk(entry_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == EXT_MODULE
        and any(alias.name == EXT_CLASS for alias in node.names)
    ]
    assert imports, (
        f"cli/entry.py never imports {EXT_CLASS} from {EXT_MODULE}. Add, inside "
        f"{FACTORY} beside the {PREPEND_LIST} assembly:\n"
        f"    from {EXT_MODULE} import {EXT_CLASS}"
    )


def test_the_harness_factory_accepts_the_resolved_app_mode(
    entry_tree: ast.Module,
) -> None:
    """``mode`` has no other honest source inside the factory.

    ``resolve_app_mode`` needs the ``stdin_is_tty`` sampled once in
    ``_async_main``; re-deriving it here would be a second opinion that can
    disagree with the one the process acted on.
    """

    factory = _function(entry_tree, FACTORY)
    names = {
        a.arg for a in (*factory.args.args, *factory.args.kwonlyargs, *factory.args.posonlyargs)
    }
    assert "app_mode" in names, (
        f"{FACTORY} has no `app_mode` parameter. Add it to the keyword-only "
        "block:\n    app_mode: str | None = None,"
    )


def test_the_status_extension_is_appended_to_the_prepend_list(
    entry_tree: ast.Module,
) -> None:
    factory = _function(entry_tree, FACTORY)
    constructions = _status_constructions(factory)
    assert constructions, (
        f"{FACTORY} never constructs {EXT_CLASS}. Append it to {PREPEND_LIST} "
        "after the agents_ext block."
    )

    appended = [
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == PREPEND_LIST
        and any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == EXT_CLASS
            for arg in node.args
        )
    ]
    assert appended, (
        f"{EXT_CLASS} is constructed but never reaches {PREPEND_LIST}.append(...)"
    )

    (construction,) = constructions
    supplied = {kw.arg for kw in construction.keywords if kw.arg}
    missing = REQUIRED_KWARGS - supplied
    assert not missing, f"{EXT_CLASS}(...) is missing {sorted(missing)}"


def test_each_wired_argument_is_the_expression_it_has_to_be(
    entry_tree: ast.Module,
) -> None:
    """The names alone are worth nothing — see the module docstring.

    ``ast.unparse`` normalises the comparison to source with the formatting and
    the comments removed, so re-wrapping the call or moving a comment does not
    fail this, while changing WHAT is passed does.
    """

    factory = _function(entry_tree, FACTORY)
    (construction,) = _status_constructions(factory)
    actual = {
        kw.arg: ast.unparse(kw.value) for kw in construction.keywords if kw.arg
    }
    for name, expected in REQUIRED_KWARG_VALUES.items():
        assert actual.get(name) == expected, (
            f"{EXT_CLASS}({name}=...) is `{actual.get(name)}`, must be "
            f"`{expected}`. See REQUIRED_KWARG_VALUES for why this one has no "
            f"substitute."
        )


def test_the_trust_argument_cannot_be_a_constant(entry_tree: ast.Module) -> None:
    """The fail-open, called out separately from the table above.

    ``project_trusted=lambda: True`` — or any constant — makes the snapshot
    report a permission nobody granted, which is the exact defect
    ``resolve_project_trusted_fail_closed`` exists to remove. Pinned twice on
    purpose: this assertion survives a future edit that legitimately renames the
    local variable the lambda closes over.
    """

    factory = _function(entry_tree, FACTORY)
    (construction,) = _status_constructions(factory)
    (trust,) = [kw for kw in construction.keywords if kw.arg == "project_trusted"]
    assert isinstance(trust.value, ast.Lambda), ast.unparse(trust.value)
    assert not trust.value.args.args, "the getter takes no arguments"
    body = trust.value.body
    assert isinstance(body, ast.Name), (
        f"project_trusted returns `{ast.unparse(body)}` — a constant or an "
        f"expression, not the resolved decision. Only a name bound to "
        f"``_resolve_project_trust``'s result may be returned here."
    )


def test_the_safety_extensions_still_come_first(entry_tree: ast.Module) -> None:
    """APPENDED, never inserted into the literal.

    The ``prepend_extensions`` assembly documents Guardrail-first as an invariant
    (first-block-wins). The status extension subscribes to ``tool_call``, so
    landing it inside that literal would put a non-gate ahead of two gates.
    """

    factory = _function(entry_tree, FACTORY)
    for node in ast.walk(factory):
        # ``prepend_extensions: list[Any] = [...]`` is an AnnAssign, not an
        # Assign — the annotation is what carries the ``Any`` that lets an
        # untyped extension instance in. Both spellings are accepted so a later
        # author dropping the annotation does not silently skip this gate.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target] if isinstance(node.target, ast.Name) else []
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        else:
            continue
        if not any(t.id == PREPEND_LIST for t in targets):
            continue
        assert isinstance(node.value, ast.List), ast.dump(node.value)
        first = node.value.elts[0]
        assert isinstance(first, ast.Call)
        assert isinstance(first.func, ast.Name)
        assert first.func.id == "GuardrailExtension"
        assert not _status_constructions(node.value), (
            f"{EXT_CLASS} was placed INSIDE the {PREPEND_LIST} literal, ahead of "
            "the permission gate. Append it after instead."
        )
        return
    raise AssertionError(f"{PREPEND_LIST} is not assigned a list literal in {FACTORY}")


def test_the_call_site_passes_the_resolved_app_mode(entry_tree: ast.Module) -> None:
    """The parameter is worth nothing if the one caller never fills it.

    ``_build_harness_options`` is called from exactly one place in ``entry.py``
    (``_harness_factory``, inside ``_async_main``, where ``app_mode`` is in
    scope). A parameter added without the argument would default to ``None`` and
    the snapshot would report ``mode="unknown"`` on every real run while every
    other test stayed green.
    """

    calls = [
        node
        for node in ast.walk(entry_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == FACTORY
    ]
    assert calls, f"{FACTORY} is never called in {ENTRY_PATH}"
    for call in calls:
        supplied = {
            kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg
        }
        assert "app_mode" in supplied, (
            f"a {FACTORY}(...) call omits `app_mode=app_mode` "
            f"(line {call.lineno})"
        )
        # By VALUE for the same reason the constructor's arguments are (M3):
        # ``app_mode="interactive"`` would satisfy a name-only check and pin the
        # snapshot's ``mode`` to a lie on every headless run.
        assert supplied["app_mode"] == "app_mode", (
            f"a {FACTORY}(...) call passes `app_mode={supplied['app_mode']}` "
            f"(line {call.lineno}); it must forward the resolved "
            f"``resolve_app_mode`` result, not a literal."
        )
