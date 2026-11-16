"""#775 — ``AgentHarness.abort_pending``: a read-only view of an asked-for abort (ADR-0298, R19).

Ctrl+B refuses to move a running delegation into a job while an abort of the
turn is pending (Codex M5). The harness's own latch is ``_abort_requested``,
which :meth:`AgentHarness.abort` sets before it cancels the turn task, and
``_turn_cancelled``, which that cancel sets; an extension's ``ctx.abort()``
sets the first and cancels nothing.

The property fails on ``c91cdc7b`` (no such attribute). The AST guard is a
guard: ``abort()`` is the only place the turn task is cancelled, and it sets
the latch first — so a turn cancel requested before the key can always be read.
"""

from __future__ import annotations

import ast
import inspect

from aelix_agent_core.harness import core

from tests.jobs.conftest import make_harness


async def test_abort_pending_reads_the_latch_and_the_cancel() -> None:
    harness = make_harness(None)
    assert harness.abort_pending is False
    harness._abort_requested = True  # an extension's ``ctx.abort()``: no cancel
    assert harness.abort_pending is True
    harness._abort_requested = False
    harness._turn_cancelled = True
    assert harness.abort_pending is True
    harness._turn_cancelled = False
    await harness.abort()
    assert harness.abort_pending is True


def test_abort_pending_is_read_only() -> None:
    prop = inspect.getattr_static(core.AgentHarness, "abort_pending")
    assert isinstance(prop, property) and prop.fset is None


def _cancels_the_turn(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "cancel"):
        return False
    target = node.func.value
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
    return name in ("turn_task", "_current_turn_task")


def test_the_turn_task_is_cancelled_only_inside_abort_after_the_latch() -> None:
    tree = ast.parse(inspect.getsource(core))
    sites: list[tuple[str, int]] = []
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(func):
                if _cancels_the_turn(node):
                    sites.append((func.name, node.lineno))
    assert [name for name, _ in sites] == ["abort"], sites
    abort = next(
        f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "abort"
    )
    latch = next(
        n.lineno
        for n in ast.walk(abort)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "_abort_requested" for t in n.targets)
    )
    assert latch < sites[0][1]
