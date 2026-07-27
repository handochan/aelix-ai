"""``--permission-mode`` / ``--agents`` / ``--no-agents`` (ADR-0197 §(e), §(b)).

``--permission-mode`` is the argv end of the child-authority guarantee: the
spawner computes a CLAMPED posture with ``aelix_agents.posture.
child_permission_mode`` and hands it to the child on the command line. Every
security assertion downstream is therefore argv-shaped, and ``parse_args``
swallows an unrecognised ``--`` flag into :attr:`Args.unknown_flags` with **no
diagnostic** (``args.py:493-513``; contrast the ``Unknown short flag`` error at
``:514-518``). A rename or a typo would consequently ship a child that silently
runs at the DEFAULT posture — i.e. auto-approving, since ``ctx.has_ui`` is
:data:`False` there — behind a fully green test suite.

:func:`test_child_argv_parses_clean` is the generic anti-typo gate for that
failure mode (review finding B10). It feeds the real child argv shape through
the real parser and insists nothing landed in ``unknown_flags``.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.resolver import profile_to_argv
from aelix_coding_agent.builtin.permission_mode import CYCLE_ORDER, PermissionMode
from aelix_coding_agent.cli.args import VALID_PERMISSION_MODES, parse_args, print_help

# ============================================================
# --permission-mode
# ============================================================


@pytest.mark.parametrize("mode", [m.value for m in PermissionMode])
def test_every_permission_mode_parses(mode: str) -> None:
    """Each posture the gate knows is accepted verbatim and recorded as provided."""

    parsed = parse_args(["--permission-mode", mode])
    assert parsed.permission_mode == mode
    assert "permission_mode" in parsed.provided
    assert parsed.diagnostics == []
    assert parsed.unknown_flags == {}
    # The parsed string must round-trip into the enum the gate reads.
    assert PermissionMode(parsed.permission_mode) in CYCLE_ORDER


def test_valid_values_are_derived_from_the_enum() -> None:
    """The accept-list cannot drift from :class:`PermissionMode`.

    A hand-spelled tuple would let a new posture be gate-known but
    parser-rejected (or vice versa) — the exact class of silent divergence this
    flag cannot afford.
    """

    assert tuple(m.value for m in PermissionMode) == VALID_PERMISSION_MODES


def test_absent_flag_leaves_permission_mode_none() -> None:
    """No flag → :data:`None` → ``cli/entry.py`` keeps the DEFAULT posture."""

    parsed = parse_args(["hello"])
    assert parsed.permission_mode is None
    assert "permission_mode" not in parsed.provided


def test_invalid_value_warns_and_drops() -> None:
    """A bogus value mirrors ``--thinking`` (``args.py:413-430``): warn, drop, continue.

    It must NOT abort a session already launching, and it must NOT record as
    "provided" — a rejected value leaves the field at its default, so claiming
    the user supplied it would let a typo veto a valid overlay.
    """

    parsed = parse_args(["--permission-mode", "bogus", "hello"])
    assert parsed.permission_mode is None
    assert "permission_mode" not in parsed.provided
    assert parsed.diagnostics == [
        {"type": "warning", "message": "Invalid --permission-mode: bogus"}
    ]
    # Warn, do not error: parsing continues and the positional survives.
    assert parsed.messages == ["hello"]
    # The value was CONSUMED, not left to be re-read as a message.
    assert "bogus" not in parsed.messages


def test_invalid_value_is_not_captured_as_an_unknown_flag() -> None:
    """The warning path must not ALSO leak into the extension passthrough map."""

    parsed = parse_args(["--permission-mode", "bogus"])
    assert parsed.unknown_flags == {}


def test_trailing_flag_without_a_value_is_inert() -> None:
    """``--permission-mode`` as the last token consumes nothing and stays ``None``."""

    parsed = parse_args(["--permission-mode"])
    assert parsed.permission_mode is None
    assert parsed.unknown_flags == {}
    assert parsed.diagnostics == []


def test_last_permission_mode_wins() -> None:
    """Linear parse, last-write-wins — pinned so a child argv is unambiguous."""

    parsed = parse_args(["--permission-mode", "yolo", "--permission-mode", "plan"])
    assert parsed.permission_mode == "plan"


def test_permission_mode_help_line_present() -> None:
    """The flag is documented; a user can discover the accepted values."""

    import io

    buf = io.StringIO()
    print_help(buf)
    text = buf.getvalue()
    assert "--permission-mode <mode>" in text
    for mode in VALID_PERMISSION_MODES:
        assert mode in text


# ============================================================
# --agents / --no-agents
# ============================================================


def test_agents_override_tristate() -> None:
    """``--agents`` → True, ``--no-agents`` → False, absent → ``None``.

    ``None`` is load-bearing: it is what lets ``cli/entry.py`` fall through to
    the global ``[features] agents`` setting (default ``False`` in P2) instead
    of a CLI default overriding it.
    """

    assert parse_args(["--agents"]).agents_override is True
    assert parse_args(["--no-agents"]).agents_override is False
    assert parse_args([]).agents_override is None


def test_no_agents_wins_when_it_comes_last() -> None:
    """Linear parse; ``cli/entry.py`` additionally ranks ``--no-agents`` highest."""

    assert parse_args(["--agents", "--no-agents"]).agents_override is False


def test_agents_flags_are_not_unknown_flags() -> None:
    """Both spellings really parse — see the module docstring on B10."""

    for flag in ("--agents", "--no-agents"):
        parsed = parse_args([flag])
        assert parsed.unknown_flags == {}
        assert parsed.diagnostics == []


def test_agents_help_line_present() -> None:
    import io

    buf = io.StringIO()
    print_help(buf)
    assert "--agents / --no-agents" in buf.getvalue()


# ============================================================
# The child argv contract (B10) — the generic anti-typo gate
# ============================================================


def _profile(**overrides: object) -> AgentProfile:
    base: dict[str, object] = {
        "name": "scout",
        "description": "Recon agent",
        "body": "You are scout.",
        "file_path": "/w/agents/scout.md",
        "scope": "user",
    }
    base.update(overrides)
    return AgentProfile(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "trust_flags",
    [[], ["--no-approve"]],
    ids=["trust-clause-1-same-cwd", "trust-clause-2-no-approve"],
)
@pytest.mark.parametrize("mode", [m.value for m in PermissionMode])
def test_child_argv_parses_clean(trust_flags: list[str], mode: str) -> None:
    """The EXACT argv shape a spawned child receives parses with nothing left over.

    Parametrized over both ``child_trust_argv`` outcomes (ADR-0197 §(g)) and
    every posture so neither argv shape can rot silently. ``unknown_flags`` is
    the assertion that matters: a flag this parser does not recognise is
    recorded there with no diagnostic at all, so an empty map is the only proof
    that ``--permission-mode`` / ``--no-agents`` are actually WIRED and not just
    spelled.

    ``profile_to_argv`` is called rather than mirrored — the spawner builds the
    child argv from that same function (``agents/resolver.py:183-206``), so a
    change to the prefix or the prompt-file flags is caught here too.
    """

    argv = [
        *profile_to_argv(
            _profile(model="m1", tools=("read", "write")),
            prompt_path="/tmp/aelix-subagent-x/prompt-scout.md",
            oneshot=True,
            task="list the files in packages/",
        ),
        "--permission-mode",
        mode,
        *trust_flags,
        "--no-agents",
    ]

    parsed = parse_args(argv)

    assert parsed.unknown_flags == {}
    assert [d for d in parsed.diagnostics if d["type"] == "error"] == []
    assert parsed.diagnostics == []
    # And the security-relevant values really landed.
    assert parsed.permission_mode == mode
    assert parsed.agents_override is False
    assert parsed.mode == "json"
    assert parsed.print_mode is True
    assert parsed.no_session is True
    assert parsed.project_trust_override is (False if trust_flags else None)
    # The task rides as the single positional (``resolver.py:204-205``).
    assert parsed.messages == ["Task: list the files in packages/"]


def test_a_typo_would_be_swallowed_silently() -> None:
    """The failure mode :func:`test_child_argv_parses_clean` exists to catch.

    Executable evidence that the parser is silent about a misspelled long flag:
    the value lands in ``unknown_flags``, ``permission_mode`` stays ``None``
    (DEFAULT posture → a headless child that auto-allows), and NOTHING is
    reported. Contrast the short-flag branch, which does emit an error.
    """

    parsed = parse_args(["--permision-mode", "plan"])
    assert parsed.permission_mode is None
    assert parsed.unknown_flags == {"permision-mode": "plan"}
    assert parsed.diagnostics == []

    short = parse_args(["-x"])
    assert short.diagnostics == [
        {"type": "error", "message": "Unknown short flag: -x"}
    ]
