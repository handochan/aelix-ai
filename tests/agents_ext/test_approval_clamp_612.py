"""#612 (ADR-0308; owner Q-A, 2026-11-18: "default로 돌며 물어봄") — the clamp's ``asks_parent`` column.

A child its parent answers (the TUI, or an RPC host that opted in) runs ``default``
under a ``default`` parent where it used to be clamped to ``plan``, and asks
before each change; every other cell is the base's, and every cell stays at or
below its parent (ADR-0199 §3.9). The column is read once, by the door that
produced the grant (``SpawnGrant.asks_parent``), and the row keeps it: its argv
(``--command-inbox=2``) and every later floor (``tighten_all``, a batch's later
waves) follow it. ``-p`` and json have no approver, so their children's clamp
and argv are the base's byte for byte (guards).

Fails on ``567753c0``: ``child_permission_mode`` takes no ``asks_parent``,
``SpawnGrant`` has no such field, ``build_child_argv`` no such keyword, and the
consent dialog has no ``default`` label.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.consent import _baseline_label
from aelix_agents.posture import child_permission_mode, posture_rank
from aelix_agents.print_channel import RunningChild, build_child_argv
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_child_session_records import _profile

P, D, AA, AU, Y = (
    PermissionMode.PLAN,
    PermissionMode.DEFAULT,
    PermissionMode.AUTO_ACCEPT,
    PermissionMode.AUTO,
    PermissionMode.YOLO,
)

#: (approval_mode, has_ui) for the four columns of r5 §7.2's table, plus ``ask`` with a UI.
_COLUMNS = {
    "inherit": ("inherit", False),
    "ask+ui": ("ask", True),
    "auto": ("auto", False),
    "ask": ("ask", False),
    "deny": ("deny", False),
}

#: r5 §7.2: parent → the child's posture per column, with ``asks_parent``.
_ASKS_PARENT = {
    P: {"inherit": P, "ask+ui": P, "auto": P, "ask": P, "deny": P},
    D: {"inherit": D, "ask+ui": D, "auto": D, "ask": P, "deny": P},
    AA: {"inherit": AA, "ask+ui": AA, "auto": AA, "ask": P, "deny": P},
    AU: {"inherit": AA, "ask+ui": AA, "auto": AA, "ask": P, "deny": P},
    Y: {"inherit": Y, "ask+ui": Y, "auto": AA, "ask": P, "deny": P},
}

#: The measured base table (the ``default`` row is ``plan`` everywhere): the guard.
_BASE = {**_ASKS_PARENT, D: {"inherit": P, "ask+ui": P, "auto": P, "ask": P, "deny": P}}


@pytest.mark.parametrize("parent", list(_ASKS_PARENT))
@pytest.mark.parametrize("column", list(_COLUMNS))
@pytest.mark.parametrize("scope", ["user", "project"])
def test_the_table_with_the_channel(parent: PermissionMode, column: str, scope: str) -> None:
    approval_mode, has_ui = _COLUMNS[column]
    asked = child_permission_mode(approval_mode, parent, scope, has_ui=has_ui, asks_parent=True)
    assert asked is _ASKS_PARENT[parent][column]
    assert posture_rank(asked) <= posture_rank(parent), "never looser than the parent"


@pytest.mark.parametrize("parent", list(_ASKS_PARENT))
@pytest.mark.parametrize("column", list(_COLUMNS))
@pytest.mark.parametrize("scope", ["user", "project"])
def test_without_the_channel_the_table_is_the_bases(parent: PermissionMode, column: str, scope: str) -> None:
    """Guard (passes on ``567753c0``): no ``asks_parent`` keyword, so it runs on the base too
    (fix round 1, verify V-N3: it shared a test with the column and failed there with a TypeError)."""

    approval_mode, has_ui = _COLUMNS[column]
    assert child_permission_mode(approval_mode, parent, scope, has_ui=has_ui) is _BASE[parent][column]


def test_a_project_scoped_auto_profile_under_a_default_parent_gets_the_column_too() -> None:
    """§7 review L2: the clamp's own recursion passes ``asks_parent``."""

    assert child_permission_mode("auto", D, "project", asks_parent=True) is D
    assert child_permission_mode("auto", D, "project") is P


def test_the_spawn_dialogs_first_option_names_the_default_clamp() -> None:
    assert _baseline_label(D) == "Run and ask before each change (default)"
    assert _baseline_label(P) == "Run read-only (plan)"
    assert _baseline_label(AA) == "Run with the inherited posture (auto-accept-edits)"


def _argv(**kwargs: Any) -> list[str]:
    return build_child_argv(
        _profile(),
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=D,
        child_cwd="/repo",
        parent_cwd="/repo",
        inbox=True,
        **kwargs,
    )


def test_a_row_that_asks_is_launched_on_inbox_version_2_and_every_other_is_byte_for_byte() -> None:
    plain, asking = _argv(), _argv(asks_parent=True)
    assert plain[-1] == "--command-inbox=1"
    assert asking[-1] == "--command-inbox=2"
    assert plain[:-1] == asking[:-1]
    assert _argv(asks_parent=False) == plain


def _runtime() -> _SubagentRuntimeImpl:
    return _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: ".", session=lambda: None))


@pytest.mark.parametrize(
    ("asks", "granted", "parent", "after"),
    [
        (True, AA, D, D),  # the column's floor under ``default`` is ``default``
        (False, AA, D, P),  # without it, ``plan`` (the base)
        (True, D, D, D),  # unchanged
        (True, D, P, P),
        (True, Y, AU, AA),
    ],
)
def test_tighten_all_takes_each_rows_own_floor(
    asks: bool, granted: PermissionMode, parent: PermissionMode, after: PermissionMode
) -> None:
    runtime = _runtime()
    row = RunningChild(id="r", profile="p", granted_mode=granted)
    row.asks_parent = asks
    runtime._children["r"] = row
    assert runtime.tighten_all(parent) == [], "no process: lowered, nothing sent"
    assert row.granted_mode is after


async def test_the_model_doors_grant_reads_the_channel_once_and_carries_it(tmp_path: Path) -> None:
    from aelix_agents.extension import AgentsExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture

    from tests.agents_ext.test_child_session_records import _resolved

    seen: list[bool] = []

    def asks() -> bool:
        seen.append(True)
        return True

    for getter, mode, carried in ((asks, D, True), (None, P, False), (lambda: 1 / 0, P, False)):
        ext = AgentsExtension(posture=PermissionPosture(mode=D), cwd=str(tmp_path), asks_parent=getter)
        grant = await ext._grant_for(None, _resolved(), ("t",), cwd=str(tmp_path), mode="single")
        assert (grant.mode, grant.asks_parent, grant.consented) == (mode, carried, True)
    assert seen == [True]


@pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")
async def test_the_runtime_door_consents_with_the_hosts_channel_and_the_row_keeps_it(tmp_path: Path) -> None:
    """``/agents run`` and RPC ``agents/run`` (``runtime.spawn``): the host's ``asks_parent``."""

    import os

    from aelix_agents.print_channel import PrintChannel

    from tests.agents_ext.test_print_channel_spawn import _resolved as _channel_resolved
    from tests.agents_ext.test_print_channel_spawn import _stub

    seen_argv: list[list[str]] = []
    script = _stub("start()\nsay('ok')\ndone()\n")
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME", "TMPDIR", "SYSTEMROOT")}

    def record(*args: Any, **kwargs: Any) -> list[str]:
        seen_argv.append(build_child_argv(*args, **kwargs))
        return [sys.executable, "-c", script]

    channel = PrintChannel(grace=1.0, argv_builder=record, env_builder=lambda *_a, **_k: dict(base))
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), session=lambda: None, asks_parent=lambda: True),
        channel=channel,
    )
    result = await runtime.spawn(_channel_resolved(), "go")
    assert result is not None
    (argv,) = seen_argv
    assert argv[-1] == "--command-inbox=2"
    assert argv[argv.index("--permission-mode") + 1] == "default"


@pytest.mark.parametrize(
    ("start", "then", "granted", "later"),
    [
        (D, P, D, P),  # a tighten to plan between waves reaches the later ones
        (AA, D, AA, D),  # a tighten to default gives the column's floor, not plan
        (D, D, D, D),  # no change: the later wave runs ``default`` as the first did
    ],
)
async def test_a_batchs_later_waves_take_the_grants_column_both_at_start_and_live(
    tmp_path: Path, start: PermissionMode, then: PermissionMode, granted: PermissionMode, later: PermissionMode
) -> None:
    """ADR-0199 §3.9's live floor, with the channel the grant carries (``batch.py``, both clamp reads)."""

    import dataclasses

    from tests.agents_ext.test_batch_executor import _grant, _two_wave_postures

    grant = dataclasses.replace(_grant(granted), asks_parent=True)
    modes = await _two_wave_postures(tmp_path, start=start, then=then, grant=grant)
    assert modes[:4] == [granted] * 4
    assert modes[4:] == [later] * 4


async def test_an_ask_profiles_later_waves_lose_the_column_when_the_ui_goes(tmp_path: Path) -> None:
    """The batch's reference clamp is taken with the grant's channel too (``clamp_at_start``):
    an ``ask`` profile under an unchanged ``default`` parent whose UI went away mid-batch
    clamps to ``plan`` for its later waves, which only a column-aware reference sees."""

    import dataclasses

    from tests.agents_ext.test_batch_executor import _grant, _profile, _two_wave_postures

    grant = dataclasses.replace(_grant(D), asks_parent=True)
    modes = await _two_wave_postures(
        tmp_path, start=D, then=D, grant=grant, profile=_profile(approval_mode="ask"), ui_start=True, ui_then=False
    )
    assert modes == [D] * 4 + [P] * 4
