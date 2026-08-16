"""A parent's ``--no-context-files`` must reach the children it delegates to — #121.

MEASURED ON THE PRE-FIX TREE, and this is the whole reason the file exists:
``no_context_files`` occurred **nowhere** in the ``aelix_agents`` package, and
both argv builders emitted no ``--no-context-files`` for any parent state::

    build_child_argv(...)      → "--no-context-files" in argv: False
    build_rpc_child_argv(...)  → "--no-context-files" in argv: False

So a user who launched with ``-nc`` — the switch whose entire purpose is "do not
let this cloned repo's ``AGENTS.md`` steer the agent" — got exactly that the
moment the agent delegated, from a child whose command line they never saw.
Independent of #121's injection policy: on ANY policy the flag the user typed has
to survive one hop.

WHY THE FLAG IS NOT ASSERTED ON ``build_child_argv``'s OWN OUTPUT. It does not
belong there. ``resolver.profile_to_flags`` (``resolver.py:274-275``) already
owns the one place a profile becomes ``--no-context-files``, so the inherit is a
CLAMP on the profile that reaches the builder — see
:func:`~aelix_agents.print_channel.narrow_context_files`. The tests below
therefore follow the value along the real chain it travels:

    AgentsExtension.no_context_files  (the parent's live ``Args``)
      → SubagentHost.context_files
        → SpawnPlan.parent_context_files
          → narrow_context_files(profile)
            → resolver.profile_to_flags → the child's argv

Every argv assertion is a ``parse_args`` ROUND TRIP rather than a substring
match, because ``cli/args.py`` records an unrecognised ``--key`` into
``parsed.unknown_flags`` and emits nothing at all — a misspelled token would
leave this file green and the child unprotected, which is the exact failure mode
``test_child_argv_contract.py`` was written to pin.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.extension import AgentsExtension
from aelix_agents.print_channel import (
    PrintChannel,
    RunningChild,
    SpawnPlan,
    build_child_argv,
    narrow_context_files,
)
from aelix_agents.rpc_channel import RpcChannel, build_rpc_child_argv
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import apply_profile_to_args
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import Args, parse_args
from aelix_coding_agent.subagent_contract import ResolvedProfile

# ``build_child_argv``/``build_rpc_child_argv`` prefix the command line with
# ``[sys.executable, "-m", "aelix_coding_agent"]``; ``parse_args`` is handed
# ``sys.argv[1:]``, i.e. everything after those three.
_LAUNCH_PREFIX = 3


# === Fixtures =================================================================


def _profile(**kwargs: Any) -> AgentProfile:
    base: dict[str, Any] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
        "tools": ("read",),
    }
    base.update(kwargs)
    return AgentProfile(**base)


def _resolved(profile: AgentProfile | None = None) -> ResolvedProfile:
    p = profile if profile is not None else _profile()
    return ResolvedProfile(
        name=p.name, profile=p, source_path=p.file_path, scope=p.scope
    )


def _plan(tmp_path: Path, **kwargs: Any) -> SpawnPlan:
    base: dict[str, Any] = {
        "id": "sub-test",
        "resolved": _resolved(),
        "task": "do the thing",
        "cwd": str(tmp_path),
        "parent_cwd": str(tmp_path),
        "permission_mode": PermissionMode.PLAN,
    }
    base.update(kwargs)
    return SpawnPlan(**base)


# A child that produces one clean turn on each transport. Trimmed to what the
# envelope needs to finish, because nothing here is about the envelope: these
# tests only need ``run`` to reach its argv builder and then terminate.
_PRELUDE = textwrap.dedent(
    """
    import json, sys

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def turn(text):
        emit({"id": "stub-session", "created_at": "now"})
        emit({"type": "agent_start"})
        emit({"type": "turn_start"})
        emit({"type": "message_end", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": None, "provider": "stub", "model": "stub-1",
        }})
        emit({"type": "agent_end"})
    """
)

_PRINT_STUB = _PRELUDE + 'turn("done")\n'

_RPC_STUB = _PRELUDE + textwrap.dedent(
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        cmd = json.loads(line)
        rid, kind = cmd.get("id"), cmd.get("type")
        emit({"type": "response", "command": kind, "success": True,
              "id": rid, "data": {}})
        if kind == "prompt":
            turn("done")
    """
)


def _capture(real: Any, script: str, seen: list[list[str]], *tail: str) -> Any:
    """An argv builder that RECORDS what production would spawn, then stubs it.

    The recorded list comes from the REAL builder, called with the arguments the
    channel actually passed — not from a reconstruction assembled in the test.
    A double that fabricated its own argv would prove nothing about the channel;
    the stub command line is only there so ``run`` terminates in milliseconds
    instead of launching a real ``-m aelix_coding_agent``.
    """

    def _build(*args: Any, **kwargs: Any) -> list[str]:
        seen.append(real(*args, **kwargs))
        return [sys.executable, "-c", script, *tail]

    return _build


def _parsed_child(argv: list[str]) -> Args:
    return parse_args(argv[_LAUNCH_PREFIX:])


# === The clamp itself =========================================================


def test_a_parent_with_nc_drops_context_files_from_the_child_profile() -> None:
    profile = _profile()
    assert profile.context_files is True

    clamped = narrow_context_files(profile, parent_context_files=False)

    assert clamped.context_files is False
    # Nothing else moved: this is a clamp on one field, not a rebuild.
    assert clamped.name == profile.name
    assert clamped.tools == profile.tools
    assert clamped.body == profile.body


def test_a_parent_without_nc_returns_the_very_same_profile_object() -> None:
    """Identity, not equality. A parent that loads context files must not be
    able to force them onto a child, and the cheapest proof that no rewrite
    happened at all is that no new object was made."""

    profile = _profile()
    assert narrow_context_files(profile, parent_context_files=True) is profile


def test_the_clamp_cannot_grant_context_files_a_profile_declined() -> None:
    """One direction only. ``context_files: false`` in the profile survives a
    parent that loads them — otherwise the inherit would silently WIDEN the
    child, which is the opposite of what it is for."""

    declined = _profile(context_files=False)
    assert narrow_context_files(declined, parent_context_files=True) is declined
    assert (
        narrow_context_files(declined, parent_context_files=False).context_files
        is False
    )


# === The clamp reaches a real argv ===========================================


@pytest.mark.parametrize(
    ("builder", "oneshot"),
    [(build_child_argv, True), (build_rpc_child_argv, False)],
    ids=["print", "rpc"],
)
def test_the_clamped_profile_puts_a_flag_a_real_child_understands_on_the_argv(
    builder: Any, oneshot: bool
) -> None:
    del oneshot  # both builders take the same arguments; that is the point

    kwargs: dict[str, Any] = {
        "prompt_path": "/tmp/p.md",
        "task": "t",
        "permission_mode": PermissionMode.PLAN,
        "child_cwd": "/tmp",
        "parent_cwd": "/tmp",
    }
    inherited = builder(
        narrow_context_files(_profile(), parent_context_files=False), **kwargs
    )
    untouched = builder(
        narrow_context_files(_profile(), parent_context_files=True), **kwargs
    )

    assert _parsed_child(inherited).no_context_files is True
    # The negative control. Without it "the flag is present" would also be
    # satisfied by a builder that emits it unconditionally.
    assert _parsed_child(untouched).no_context_files is False


@pytest.mark.parametrize(
    "builder", [build_child_argv, build_rpc_child_argv], ids=["print", "rpc"]
)
def test_the_flag_lands_exactly_once_when_the_profile_also_declined(
    builder: Any,
) -> None:
    """The reason the inherit is a profile clamp and not a second append.

    A parent with ``-nc`` delegating to a ``context_files: false`` profile has
    two independent reasons to emit the flag. Appending it in the argv builder
    would emit it twice — harmless to ``parse_args``, but it would mean two
    emission sites for one rule, which is exactly the drift
    ``resolver.py``'s single emission table exists to prevent.
    """

    argv = builder(
        narrow_context_files(_profile(context_files=False), parent_context_files=False),
        prompt_path="/tmp/p.md",
        task="t",
        permission_mode=PermissionMode.PLAN,
        child_cwd="/tmp",
        parent_cwd="/tmp",
    )

    assert argv.count("--no-context-files") == 1


# === The channels carry the plan's answer ====================================


async def test_the_print_channel_carries_the_plans_nc_onto_the_childs_argv(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []
    channel = PrintChannel(
        argv_builder=_capture(
            build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
        )
    )

    await channel.run(
        _plan(tmp_path, parent_context_files=False),
        child=RunningChild(id="sub-test", profile="scout"),
    )

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is True


async def test_the_print_channel_leaves_a_parent_without_nc_alone(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []
    channel = PrintChannel(
        argv_builder=_capture(
            build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
        )
    )

    await channel.run(_plan(tmp_path), child=RunningChild(id="s", profile="scout"))

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is False


async def test_the_rpc_channel_carries_the_plans_nc_onto_the_childs_argv(
    tmp_path: Path,
) -> None:
    """The second transport, driven through its own ``run``.

    Both channels narrow through the SAME function, and this is what stops that
    from being an untested claim: the rpc channel builds its argv at a different
    line, from a different builder, with the task deliberately dropped.
    """

    seen: list[list[str]] = []
    channel = RpcChannel(
        argv_builder=_capture(build_rpc_child_argv, _RPC_STUB, seen)
    )

    await channel.run(
        _plan(tmp_path, parent_context_files=False),
        child=RunningChild(id="sub-test", profile="scout"),
    )

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is True


async def test_the_rpc_channel_leaves_a_parent_without_nc_alone(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []
    channel = RpcChannel(
        argv_builder=_capture(build_rpc_child_argv, _RPC_STUB, seen)
    )

    await channel.run(_plan(tmp_path), child=RunningChild(id="s", profile="scout"))

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is False


# === The runtime reads the host, per spawn ===================================


async def test_the_hosts_answer_reaches_the_childs_argv_through_the_runtimes_own_channel(
    tmp_path: Path,
) -> None:
    """The seam end to end, WITHOUT injecting a channel.

    Same discipline as the ``parent_model`` test in
    ``test_print_channel_spawn.py``: the channel tests above construct their own
    ``PrintChannel`` / ``RpcChannel`` and would stay green with
    ``SubagentHost.context_files`` wired to nothing at all. This one lets
    ``_SubagentRuntimeImpl.__post_init__`` build the channel and only patches
    its argv builder, so the host → plan hop is under test too.

    It also pins that the getter is called PER SPAWN rather than captured — the
    flag really does move mid-session, because ``/agents use`` rewrites the
    parent's ``Args`` in place.
    """

    seen: list[list[str]] = []
    answers = [False, True]
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(
            cwd=lambda: str(tmp_path),
            context_files=lambda: answers[len(seen)],
        )
    )
    assert runtime.channel is not None, "__post_init__ must build the channel"
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )

    await runtime.spawn(_resolved(), "go")
    await runtime.spawn(_resolved(), "again")

    assert [_parsed_child(a).no_context_files for a in seen] == [True, False]


async def test_an_unwired_host_leaves_the_child_loading_context_files(
    tmp_path: Path,
) -> None:
    """``SubagentHost.context_files`` defaults to ``lambda: True``.

    Deliberately not fail-closed: #121 settled that context-file injection is
    trust-INDEPENDENT, so this is parent-authority inheritance and not a
    security gate, and a ``False`` default would strip ``AGENTS.md`` out of
    every child of every host that never wired the getter.
    """

    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: str(tmp_path)))
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: do the thing"
    )

    await runtime.spawn(_resolved(), "go")

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is False


# === The extension reads the parent's Args ===================================


def test_the_extension_reads_the_parents_flag_live() -> None:
    """A callable, not a captured bool — and against a REAL ``Args``.

    ``/agents use`` overlays a profile onto the same ``Args`` object
    ``cli/entry.py``'s harness factory closed over: ``agents/service.py:281``
    resets it with ``__dict__.update`` and ``apply_profile_to_args`` then sets
    ``no_context_files = True`` for a ``context_files: false`` profile. Both
    halves are exercised below on ONE object, so a captured bool fails in both
    directions rather than only the convenient one.
    """

    parsed = parse_args(["--no-session"])
    ext = AgentsExtension(no_context_files=lambda: parsed.no_context_files)
    host = ext._host()

    assert host.context_files() is True

    # /agents use <a profile with ``context_files: false``>
    apply_profile_to_args(
        parsed, _profile(context_files=False), provided=parsed.provided
    )
    assert parsed.no_context_files is True
    assert host.context_files() is False

    # /agents use <a profile that says nothing> — step 2's reset comes first.
    parsed.__dict__.update(Args().__dict__)
    assert host.context_files() is True


def test_an_unwired_extension_reports_that_the_parent_loads_context_files() -> None:
    """Every field is optional, and ``tests/agents_ext`` builds the bare form
    everywhere. Inventing a ``-nc`` nobody typed would strip project context out
    of all of them, and out of any embedder that never wired the getter."""

    assert AgentsExtension()._host().context_files() is True


def test_a_wired_getter_that_raises_denies_rather_than_widens() -> None:
    """The asymmetry between the two no-evidence cases.

    A getter EXISTING is evidence that a parent is there whose flag we were
    meant to honour. The two ways of being wrong are not symmetric: a child
    without project context is degraded and visible in its own output, whereas a
    child that ignores a flag the user typed is the silent hole #121 closes.
    """

    def _boom() -> bool:
        raise RuntimeError("the parent's Args went away")

    assert AgentsExtension(no_context_files=_boom)._host().context_files() is False


# === The wiring that makes the chain live ====================================


def test_entry_py_hands_the_extension_the_parents_nc() -> None:
    """The tripwire for the one line this fix is still missing.

    AST rather than a substring search: ``no_context_files`` appears in
    ``entry.py`` already (the ``--no-context-files`` gate on the context-file
    discovery itself), so a text match would be satisfied by the wrong
    occurrence and report the hole as closed.
    """

    entry = (
        Path(__file__).resolve().parents[2]
        / "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py"
    )
    tree = ast.parse(entry.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentsExtension"
    ]

    assert calls, "entry.py no longer constructs an AgentsExtension at all"
    assert any(
        kw.arg == "no_context_files" for call in calls for kw in call.keywords
    ), "AgentsExtension is built without the parent's -nc; the inherit is inert"


@pytest.mark.parametrize(
    ("launch_argv", "expected"),
    [(["--no-context-files"], True), ([], False)],
    ids=["-nc", "plain"],
)
async def test_the_users_launch_flag_reaches_the_delegated_childs_argv(
    tmp_path: Path, launch_argv: list[str], expected: bool
) -> None:
    """THE BUG, END TO END — everything except the one line in ``cli/entry.py``.

    A real ``Args`` parsed from a real command line, handed to the extension the
    way the missing wiring line must hand it over, through the extension's own
    host, through the runtime's own channel, out to the argv a real child would
    be exec'd with — and parsed back.

    ``AgentsExtension.no_context_files`` is spelled in the SAME polarity as
    ``Args.no_context_files`` precisely so that call site is a forwarding lambda
    with nowhere to invert it. This is that lambda.

    Run against the pre-fix tree with the extension built bare (its only
    available shape), the same chain answered ``no_context_files: False`` for a
    parent that launched with ``-nc``.
    """

    parsed = parse_args(launch_argv)
    ext = AgentsExtension(
        cwd=str(tmp_path), no_context_files=lambda: parsed.no_context_files
    )

    seen: list[list[str]] = []
    runtime = _SubagentRuntimeImpl(host=ext._host())
    assert runtime.channel is not None
    runtime.channel._argv_builder = _capture(  # pyright: ignore[reportAttributeAccessIssue]
        build_child_argv, _PRINT_STUB, seen, "Task: go"
    )

    await runtime.spawn(_resolved(), "go")

    assert len(seen) == 1
    assert _parsed_child(seen[0]).no_context_files is expected
