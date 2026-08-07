"""The child's command line IS the security boundary — ADR-0197 §(l), finding B10.

Every child-authority guarantee in P2 is argv-shaped. The posture clamp (§(e)),
the consent grant (§(i)), the tool intersection (§(l)) and the project-trust rule
(§(g)) all end up as tokens on one command line, and if any of those tokens is
misspelled the child starts anyway — silently, with the WIDER default.

That is not hypothetical. ``cli/args.py`` records any unrecognised ``--key
value`` pair into ``parsed.unknown_flags`` and emits **nothing** — no warning, no
diagnostic, no non-zero exit (contrast the ``Unknown short flag: {arg}`` error a
single-dash typo gets). So a renamed or mistyped ``--permission-mode`` yields a
completely green test suite and an auto-approving child.
:func:`test_a_typo_would_be_swallowed_silently` pins that mechanism so the rest
of this file is understood as load-bearing rather than pedantic.

These tests are pure: they build argv and parse it back. The one test that
observes what a REAL child does with the same argv lives in
``test_child_realized_posture.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aelix_agents.print_channel import (
    AGENT_TOOL_NAME,
    build_child_argv,
    narrow_tools,
)
from aelix_agents.rpc_channel import build_rpc_child_argv
from aelix_agents.trust import child_trust_argv
from aelix_ai.streaming import Model
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import profile_to_argv
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import parse_args

# The three leading tokens ``build_child_argv`` always emits. Split out because
# ``parse_args`` takes the argv WITHOUT them (it is handed ``sys.argv[1:]``).
_LAUNCH_PREFIX = 3


def _profile(**kwargs: object) -> AgentProfile:
    base: dict[str, object] = {
        "name": "scout",
        "description": "Reads things.",
        "body": "You are a scout.",
        "file_path": "/home/u/.aelix/agent/agents/scout.md",
        "scope": "user",
    }
    base.update(kwargs)
    return AgentProfile(**base)  # pyright: ignore[reportArgumentType]


def _argv(
    tmp_path: Path,
    *,
    profile: AgentProfile | None = None,
    mode: PermissionMode = PermissionMode.PLAN,
    task: str = "list the files",
) -> list[str]:
    return build_child_argv(
        profile if profile is not None else _profile(tools=("read", "ls")),
        prompt_path=str(tmp_path / "prompt-scout.md"),
        task=task,
        permission_mode=mode,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
    )


# === The launch vector ========================================================


def test_child_is_launched_as_a_module_not_as_the_console_script(tmp_path: Path) -> None:
    """``[sys.executable, "-m", "aelix_coding_agent"]`` and nothing else.

    NOT ``-m aelix`` (``rpc/rpc_client.py:466`` does that and it is a live bug —
    ``aelix`` is the umbrella meta-package demo) and NOT the ``aelix`` console
    script, which in a worktree resolves to whichever tree owns the editable
    install rather than the code the parent is running.
    """

    argv = _argv(tmp_path)
    assert argv[:_LAUNCH_PREFIX] == [sys.executable, "-m", "aelix_coding_agent"]


def test_the_oneshot_json_channel_is_requested(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    for flag in ("--mode", "json", "-p", "--no-session"):
        assert flag in argv


def test_the_task_keeps_its_prefix(tmp_path: Path) -> None:
    """``"Task: "`` is load-bearing, not decoration.

    A bare task beginning with ``--`` is swallowed into ``unknown_flags`` with no
    diagnostic and the child runs with an EMPTY prompt; one beginning with a
    single ``-`` produces ``Unknown short flag``. The prefix makes the whole
    thing a positional no matter what the model wrote.
    """

    argv = _argv(tmp_path, task="--help me")
    assert "Task: --help me" in argv
    # The spawner's own flags are appended AFTER the positional
    # ``profile_to_argv`` emits, so the task is not the last token — assert it
    # survives the parse rather than its position.
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.messages == ["Task: --help me"]
    assert parsed.unknown_flags == {}
    # …and the flags that follow it are still parsed, i.e. a positional in the
    # middle does not terminate flag parsing.
    assert parsed.permission_mode == "plan"
    assert parsed.agents_override is False


# === The B10 anti-typo gate ===================================================


def test_child_argv_parses_clean(tmp_path: Path) -> None:
    """The EXACT argv the spawner builds parses with nothing left over."""

    parsed = parse_args(_argv(tmp_path)[_LAUNCH_PREFIX:])
    assert parsed.unknown_flags == {}
    assert [d for d in parsed.diagnostics if d.get("type") == "error"] == []


def test_a_typo_would_be_swallowed_silently(tmp_path: Path) -> None:
    """WHY the test above exists — the failure mode it is guarding against.

    A misspelled long flag is captured with NO diagnostic at all, so the child
    starts with its default (auto-approving) posture and every other test in the
    suite still passes.
    """

    argv = _argv(tmp_path)
    typo = [a.replace("--permission-mode", "--permision-mode") for a in argv]
    parsed = parse_args(typo[_LAUNCH_PREFIX:])
    assert parsed.permission_mode is None
    assert parsed.unknown_flags == {"permision-mode": "plan"}
    assert parsed.diagnostics == []


@pytest.mark.parametrize("mode", list(PermissionMode))
def test_child_argv_realizes_the_intended_posture(
    tmp_path: Path, mode: PermissionMode
) -> None:
    """Every posture the clamp can produce survives the argv round trip."""

    parsed = parse_args(_argv(tmp_path, mode=mode)[_LAUNCH_PREFIX:])
    assert parsed.permission_mode == mode.value


def test_a_widened_grant_round_trips_too(tmp_path: Path) -> None:
    """The dialog's ceiling (§(i)) is what a widened child is launched with.

    ``auto-accept-edits`` is the ONLY posture a human answer can add, so it is
    the one value in the table that is not produced by the clamp alone.
    """

    parsed = parse_args(
        _argv(tmp_path, mode=PermissionMode.AUTO_ACCEPT)[_LAUNCH_PREFIX:]
    )
    assert parsed.permission_mode == "auto-accept-edits"


def test_the_child_cannot_delegate_further(tmp_path: Path) -> None:
    parsed = parse_args(_argv(tmp_path)[_LAUNCH_PREFIX:])
    assert parsed.agents_override is False


# === Composition — the dry run must stay the truth ============================


def test_profile_to_argv_matches_spawner_argv(tmp_path: Path) -> None:
    """The spawner's argv is ``profile_to_argv`` plus exactly three additions.

    ``/agents show`` renders its dry run from ``profile_to_argv``. If the
    spawner ever composes differently, the command a user copies out of that
    panel stops being the command that actually ran — and the panel is the only
    auditable description of a delegation there is.
    """

    profile = _profile(tools=("read", "ls"))
    prompt = str(tmp_path / "prompt-scout.md")
    argv = build_child_argv(
        profile,
        prompt_path=prompt,
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
    )
    assert argv == [
        sys.executable,
        "-m",
        "aelix_coding_agent",
        *profile_to_argv(profile, prompt_path=prompt, oneshot=True, task="go"),
        "--permission-mode",
        "plan",
        *child_trust_argv(tmp_path, tmp_path),
        "--no-agents",
    ]


def test_trust_flags_come_from_the_one_shared_rule(tmp_path: Path) -> None:
    """§(g) clause 2 — a relocated child carries ``--no-approve``.

    Same call as the dry run, so the two can never disagree about whether a
    given delegation is allowed to load project-local code.
    """

    nested = tmp_path / "vendor" / "sdk"
    nested.mkdir(parents=True)
    argv = build_child_argv(
        _profile(),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(nested),
        parent_cwd=str(tmp_path),
    )
    assert "--no-approve" in argv
    assert argv.index("--no-approve") > argv.index("--permission-mode")


def test_a_clean_same_cwd_child_carries_no_trust_flag(tmp_path: Path) -> None:
    """§(g) clause 1 — nothing to gate means nothing is withheld.

    Forcing ``--no-approve`` here would strip ``.aelix/skills/`` (which the
    trust gate never guarded) and silently regress the ``inherit_skills: true``
    default, for zero security gain.
    """

    argv = _argv(tmp_path)
    assert "--no-approve" not in argv
    assert "--approve" not in argv


# === The tool intersection, as it reaches argv ================================


def test_the_intersection_is_rendered_as_one_csv_token(tmp_path: Path) -> None:
    narrowing = narrow_tools(_profile(tools=("read", "write", "bash")), ["read", "ls"])
    assert narrowing.profile.tools == ("read",)
    assert narrowing.dropped == ("bash", "write")
    argv = _argv(tmp_path, profile=narrowing.profile)
    assert "--tools" in argv
    assert argv[argv.index("--tools") + 1] == "read"


def test_an_empty_intersection_emits_no_tools_never_an_empty_string(
    tmp_path: Path,
) -> None:
    """``--tools ''`` is the EXACT INVERSION of what an empty grant means.

    ``parse_args(['--tools', ''])`` yields ``[]``, which
    ``_resolve_active_tools`` reads as falsy → ``None`` → **every** tool active.
    ``--no-tools`` is the only spelling that means what it says.
    """

    narrowing = narrow_tools(_profile(tools=("write",)), ["read"])
    assert narrowing.profile.tools == ()
    argv = _argv(tmp_path, profile=narrowing.profile)
    assert "--no-tools" in argv
    assert "--tools" not in argv
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.no_tools is True
    assert parsed.tools == []


def test_the_agent_tool_is_never_in_a_child_grant() -> None:
    """The second, independent anti-nesting layer (§(d)).

    The depth env var stops the extension LOADING and ``bind_subagents`` refuses
    at depth; this makes it true of the argv as well, so a child cannot delegate
    even if some future build forgets one of the other two.
    """

    narrowing = narrow_tools(
        _profile(tools=("read", AGENT_TOOL_NAME)), ["read", AGENT_TOOL_NAME]
    )
    assert AGENT_TOOL_NAME not in (narrowing.profile.tools or ())
    assert AGENT_TOOL_NAME in narrowing.dropped


def test_an_inheriting_profile_gets_exactly_the_parents_grant() -> None:
    """``tools: None`` means inherit — and inherit is a CEILING, not a wish."""

    narrowing = narrow_tools(_profile(tools=None), ["read", "ls"])
    assert narrowing.profile.tools == ("ls", "read")
    assert narrowing.dropped == ()


def test_an_inheriting_profile_reports_structural_exclusions_as_dropped() -> None:
    """DOCUMENTED CONSEQUENCE of the plan's ``dropped = requested - allowed``.

    An inheriting profile's "request" IS the parent's grant, so anything the
    child structurally cannot have — ``agent`` (anti-nesting) and any extension
    or MCP tool the child cannot build — is reported as dropped even though no
    profile author ever asked for it. That is noise on the common path rather
    than a shortfall, and it rides back to the parent's model on every single
    delegation.

    Pinned rather than fixed: the formula is ADR-0197 §(l) verbatim, and
    narrowing it is a product decision about what ``dropped_tools`` MEANS, not
    an implementation detail this layer may take alone.
    """

    narrowing = narrow_tools(
        _profile(tools=None), ["read", AGENT_TOOL_NAME, "mcp__github__list"]
    )
    assert narrowing.profile.tools == ("read",)
    assert narrowing.dropped == (AGENT_TOOL_NAME, "mcp__github__list")


def test_an_unknown_parent_grant_falls_back_to_the_builtin_set() -> None:
    """``None`` is the harness's pre-materialisation sentinel, not "no tools"."""

    narrowing = narrow_tools(_profile(tools=("read", "write")), None)
    assert narrowing.profile.tools == ("read", "write")


def test_an_extension_tool_the_child_cannot_build_is_dropped() -> None:
    """``& ALL_TOOL_NAMES`` — naming an extension/MCP tool would kill the child.

    The child harness validates its tool list at startup; an unbuildable name
    aborts it, and the parent would report that as a delegation failure with a
    cause nobody could act on.
    """

    narrowing = narrow_tools(
        _profile(tools=("read", "some_mcp_tool")), ["read", "some_mcp_tool"]
    )
    assert narrowing.profile.tools == ("read",)
    assert narrowing.dropped == ("some_mcp_tool",)


# === The parent's model, inherited (M1) =======================================
#
# A parent launched as ``aelix --provider anthropic --model claude-haiku-4-5
# --agents`` holds its model in RUN SCOPE only: no profile declares it and
# nothing persists it. Before this rule the child was spawned with no model
# token at all and died in about a second with ``No model selected`` — the exact
# invocation the quickstart teaches.


def _parent_model(**kwargs: object) -> Model:
    base: dict[str, object] = {"id": "claude-haiku-4-5", "provider": "anthropic"}
    base.update(kwargs)
    return Model(**base)  # pyright: ignore[reportArgumentType]


def test_a_profile_with_no_model_inherits_the_parents(tmp_path: Path) -> None:
    """The bundled profiles declare no model; the parent's has to reach them."""

    argv = build_child_argv(
        _profile(),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(),
    )
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.model == "claude-haiku-4-5"
    assert parsed.provider == "anthropic"


def test_a_profile_that_declares_a_model_still_wins(tmp_path: Path) -> None:
    """Inheritance is a FALLBACK. A profile's own identity is not overridable
    by the parent that happens to be delegating to it."""

    argv = build_child_argv(
        _profile(model="gpt-5.6", provider="openai"),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(),
    )
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.model == "gpt-5.6"
    assert parsed.provider == "openai"
    assert "claude-haiku-4-5" not in argv


def test_a_profile_naming_only_a_model_does_not_take_the_parents_provider(
    tmp_path: Path,
) -> None:
    """Half the parent's pair is worse than none of it.

    ``model: gpt-5.6`` with the parent's ``anthropic`` glued on is a
    combination no one asked for, and it would fail in a way that blames the
    profile author."""

    argv = build_child_argv(
        _profile(model="gpt-5.6"),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(),
    )
    assert "--provider" not in argv
    assert parse_args(argv[_LAUNCH_PREFIX:]).model == "gpt-5.6"


def test_a_profile_naming_only_a_provider_does_not_take_the_parents_model(
    tmp_path: Path,
) -> None:
    """The mirror case: ``provider: openai`` must not be handed an anthropic id."""

    argv = build_child_argv(
        _profile(provider="openai"),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(),
    )
    assert "--model" not in argv
    assert parse_args(argv[_LAUNCH_PREFIX:]).provider == "openai"


def test_an_unresolved_parent_model_is_not_forwarded(tmp_path: Path) -> None:
    """A bare ``Model()`` is what a harness that never resolved one holds.

    ``--model unknown`` would replace the child's honest ``No model selected``
    with a lookup failure for a model nobody named."""

    argv = build_child_argv(
        _profile(),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=Model(),
    )
    assert "--model" not in argv
    assert "unknown" not in argv


def test_an_unresolved_provider_still_forwards_the_model(tmp_path: Path) -> None:
    """``--model`` alone is a supported invocation — the child's cascade infers
    the provider. ``--provider unknown`` is not."""

    argv = build_child_argv(
        _profile(),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(provider="unknown"),
    )
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.model == "claude-haiku-4-5"
    assert "--provider" not in argv


def test_no_parent_model_leaves_the_argv_exactly_as_it_was(tmp_path: Path) -> None:
    """The default is inert: an unwired host costs the child nothing it had."""

    common: dict[str, object] = {
        "prompt_path": str(tmp_path / "p.md"),
        "task": "go",
        "permission_mode": PermissionMode.PLAN,
        "child_cwd": str(tmp_path),
        "parent_cwd": str(tmp_path),
    }
    assert build_child_argv(_profile(), **common) == build_child_argv(  # pyright: ignore[reportArgumentType]
        _profile(), parent_model=None, **common  # pyright: ignore[reportArgumentType]
    )


def test_the_rpc_channel_forwards_it_the_same_way(tmp_path: Path) -> None:
    """The two builders are interchangeable through the ``argv_builder`` seam,
    so a child that inherits on one channel must inherit on the other."""

    argv = build_rpc_child_argv(
        _profile(),
        prompt_path=str(tmp_path / "p.md"),
        task="go",
        permission_mode=PermissionMode.PLAN,
        child_cwd=str(tmp_path),
        parent_cwd=str(tmp_path),
        parent_model=_parent_model(),
    )
    parsed = parse_args(argv[_LAUNCH_PREFIX:])
    assert parsed.model == "claude-haiku-4-5"
    assert parsed.provider == "anthropic"
