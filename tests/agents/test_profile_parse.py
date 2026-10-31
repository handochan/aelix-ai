"""ADR-0196 — ``agents/profile.py`` parse + validation contract.

Every test here closes a defect the 4-lens review proved by execution, so the
assertions are about SEMANTICS (what the field means downstream), not about the
parser's internal shape.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from aelix_coding_agent.agents.profile import ProfileScope, parse_profile

_MINIMAL = """---
name: scout
description: Recon agent
---

You are scout.
"""


def _parse(
    content: str,
    *,
    file_path: str = "/w/agents/scout.md",
    scope: ProfileScope = "user",
):
    return parse_profile(content, file_path=file_path, scope=scope)


def _codes(result) -> list[str]:
    return [d.code for d in result.diagnostics]


def _errors(result) -> list[str]:
    return [d.code for d in result.diagnostics if d.type == "error"]


def test_minimal_profile_parses() -> None:
    """Required trio populated; every optional field at its documented default."""

    result = _parse(_MINIMAL)

    assert result.diagnostics == []
    profile = result.profile
    assert profile is not None
    assert profile.name == "scout"
    assert profile.description == "Recon agent"
    assert profile.body == "You are scout."
    assert profile.file_path == "/w/agents/scout.md"
    assert profile.scope == "user"

    assert profile.model is None
    assert profile.provider is None
    assert profile.tools is None
    assert profile.builtin_tools is True
    assert profile.skills == ()
    assert profile.inherit_skills is True
    assert profile.extensions == ()
    # Deliberate asymmetry (spec §2.3): extensions are code, skills are text.
    assert profile.inherit_extensions is False
    assert profile.system_prompt == "append"
    assert profile.context_files is True
    assert profile.thinking is None
    assert profile.role == "leaf"
    assert profile.output_cap == 51200
    # Deprecated and never read (#604, ADR-0276 D2), but still a field — it is
    # how RPC ``agents/show`` and the spawn-time warning see the key.
    assert profile.timeout_ms is None
    assert profile.approval_mode == "inherit"


def test_model_inherit_sentinel_parses_to_none() -> None:
    """``model: inherit`` is IDENTICAL to an absent key.

    Left as a literal id it reaches ``resolve_model('inherit', None)`` →
    ``Model(id='inherit', provider='', api='unknown')`` → the #98 unrunnable
    gate, i.e. the spec's own documented default would refuse to start.
    """

    inherited = _parse(
        """---
name: scout
description: d
model: inherit
provider: inherit
---
body
"""
    )
    absent = _parse(_MINIMAL)

    assert inherited.profile is not None
    assert absent.profile is not None
    assert inherited.profile.model is None
    assert inherited.profile.provider is None
    assert inherited.profile.model == absent.profile.model
    assert inherited.profile.provider == absent.profile.provider


def test_unclosed_frontmatter_is_error_not_giant_body() -> None:
    """An unclosed block must never become the system prompt.

    ``parse_frontmatter`` returns ``({}, <the whole file>, None)`` here
    (``_frontmatter.py:45-47``), so without the explicit check the profile's own
    YAML would be shipped verbatim to the model.
    """

    result = _parse("---\nname: x\ndescription: y\n")

    assert result.profile is None
    assert _errors(result) == ["parse_failed"]
    assert "not closed" in result.diagnostics[0].message


def test_crlf_profile_body_normalized() -> None:
    """CRLF input yields an ``\\n``-only body (pins ``_frontmatter.py:36``)."""

    content = "---\r\nname: scout\r\ndescription: d\r\n---\r\n\r\nLine one\r\nLine two\r\n"
    result = _parse(content)

    assert result.profile is not None
    assert "\r" not in result.profile.body
    assert result.profile.body == "Line one\nLine two"


def test_name_shaped_like_a_flag_is_rejected() -> None:
    """All FOUR ``skills.py:470-482`` rules, not just the regex.

    ``^[a-z0-9-]+$`` alone accepts ``--tools`` and ``-p``; a name that is
    indistinguishable from a flag would be injectable into
    ``resolver.profile_to_argv``.
    """

    for bad in ("--tools", "-p", "a--b", "-a", "a-"):
        result = _parse(
            f"---\nname: {bad}\ndescription: d\n---\nbody\n"
        )
        assert result.profile is None, bad
        assert "invalid_metadata" in _errors(result), bad

    # ...and a long name trips the ≤64 rule.
    long_name = "a" * 65
    result = _parse(f"---\nname: {long_name}\ndescription: d\n---\nbody\n")
    assert result.profile is None
    assert any("64 characters" in d.message for d in result.diagnostics)


def test_unicode_name_rejected_with_diagnostic() -> None:
    result = _parse("---\nname: 스카우트\ndescription: d\n---\nbody\n")

    assert result.profile is None
    assert "invalid_metadata" in _errors(result)
    assert any("invalid characters" in d.message for d in result.diagnostics)


def test_unknown_key_is_warning_not_failure() -> None:
    """P2+ fields stay forward-compatible but visible."""

    result = _parse(
        """---
name: scout
description: d
deny_tools: [bash]
---
body
"""
    )

    assert result.profile is not None
    assert _codes(result) == ["unknown_field"]
    assert result.diagnostics[0].type == "warning"
    assert "deny_tools" in result.diagnostics[0].message


def test_empty_tools_list_is_not_none() -> None:
    """``tools: []`` → ``()`` (NO tools); absent → ``None`` (inherit).

    Collapsing the two is the E3/F2 defect: ``()`` rendered as ``--tools ''``
    yields ``parsed.tools == []`` → ``_resolve_active_tools`` returns ``None`` →
    EVERY tool active, the exact inversion of the profile's intent.
    """

    empty = _parse("---\nname: scout\ndescription: d\ntools: []\n---\nbody\n")
    absent = _parse(_MINIMAL)

    assert empty.profile is not None
    assert absent.profile is not None
    assert empty.profile.tools == ()
    assert absent.profile.tools is None
    assert empty.profile.tools is not None


def test_csv_and_list_forms_equivalent() -> None:
    csv = _parse("---\nname: scout\ndescription: d\ntools: read,grep\n---\nbody\n")
    seq = _parse(
        "---\nname: scout\ndescription: d\ntools: [read, grep]\n---\nbody\n"
    )

    assert csv.profile is not None
    assert seq.profile is not None
    assert csv.profile.tools == ("read", "grep")
    assert csv.profile.tools == seq.profile.tools


def test_skill_paths_resolve_against_profile_dir(tmp_path: Path) -> None:
    """Relative entries anchor to the PROFILE's directory, never cwd.

    aelix's ``--skill`` takes a PATH (``entry.py:1463-1506``), so a profile must
    mean the same thing from any working directory. The decoy below is what a
    cwd-relative implementation would have picked.
    """

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    correct = agents_dir / "skills" / "recon"
    correct.mkdir(parents=True)
    decoy = tmp_path / "skills" / "recon"
    decoy.mkdir(parents=True)

    profile_path = agents_dir / "scout.md"
    result = _parse(
        "---\nname: scout\ndescription: d\nskills: [skills/recon]\n---\nbody\n",
        file_path=str(profile_path),
    )

    assert result.profile is not None
    assert result.profile.skills == (str(correct),)
    assert result.profile.skills != (str(decoy),)
    assert Path(result.profile.skills[0]).is_absolute()


def test_missing_skill_path_is_warning_at_parse(tmp_path: Path) -> None:
    """WARNING here so ``/agents list`` never breaks.

    ``discovery.resolve_profile`` escalates it to fatal — see
    ``test_profile_discovery.py::test_resolve_profile_escalates_missing_skill_path_to_fatal``.
    """

    profile_path = tmp_path / "agents" / "scout.md"
    result = _parse(
        "---\nname: scout\ndescription: d\nskills: [nope]\n---\nbody\n",
        file_path=str(profile_path),
    )

    assert result.profile is not None
    assert _codes(result) == ["missing_path"]
    assert result.diagnostics[0].type == "warning"


def test_project_scope_extensions_is_error() -> None:
    """THE RCE CUT.

    ``extensions/loader.py:796-860`` exec_module's explicit extension paths
    outside BOTH the ``no_discovery`` and ``no_project_local`` guards, so a
    checked-in project profile declaring ``extensions:`` would run arbitrary
    code that Project Trust never sees.
    """

    result = _parse(
        "---\nname: scout\ndescription: d\nextensions: [x.py]\n---\nbody\n",
        scope="project",
    )

    assert result.profile is None
    assert "scope_forbidden" in _errors(result)


def test_user_scope_extensions_allowed() -> None:
    result = _parse(
        "---\nname: scout\ndescription: d\nextensions: [x.py]\n---\nbody\n",
        scope="user",
    )

    assert result.profile is not None
    assert "scope_forbidden" not in _errors(result)
    # ``_parse_path_list`` resolves against ``Path(file_path).parent`` and
    # returns ``str(path)``, so the expectation is built the same way rather
    # than spelled with POSIX separators the parser never emits on Windows.
    assert result.profile.extensions == (str(Path("/w/agents") / "x.py"),)


def test_invalid_thinking_is_error() -> None:
    """Deliberate asymmetry with ``args.py:659-676``, which warns and drops.

    A typo on the command line must not abort a session already launching; a
    checked-in profile is read before anything starts, so it is fatal.
    """

    result = _parse("---\nname: scout\ndescription: d\nthinking: bogus\n---\nbody\n")

    assert result.profile is None
    assert "invalid_metadata" in _errors(result)


def test_p2_fields_validated_not_consumed() -> None:
    """Parsed + validated in P1 so the format never changes shape in P2.

    ``timeout_ms`` left this list with #604: it is ignored now, so a bad value is
    a warning and the profile still loads
    (:func:`test_timeout_ms_is_ignored_with_one_warning_and_never_drops_the_profile`).
    """

    for frontmatter in (
        "role: overlord",
        "approval_mode: whatever",
        "output_cap: -1",
        "output_cap: 0",
    ):
        result = _parse(
            f"---\nname: scout\ndescription: d\n{frontmatter}\n---\nbody\n"
        )
        assert result.profile is None, frontmatter
        assert "invalid_metadata" in _errors(result), frontmatter

    good = _parse(
        """---
name: scout
description: d
role: orchestrator
approval_mode: deny
output_cap: 4096
timeout_ms: 30000
---
body
"""
    )
    assert good.profile is not None
    assert good.profile.role == "orchestrator"
    assert good.profile.approval_mode == "deny"
    assert good.profile.output_cap == 4096
    assert good.profile.timeout_ms == 30000


_IGNORED = "timeout_ms is ignored: delegated agents run without a time limit (#604)"


@pytest.mark.parametrize(
    ("line", "stored", "not_an_integer"),
    [
        ("timeout_ms: 30000", 30000, False),
        ("timeout_ms: -5", -5, False),
        ("timeout_ms: 0", 0, False),
        ("timeout_ms:", None, False),
        ("timeout_ms: soon", None, True),
        ("timeout_ms: true", None, True),
        ("timeout_ms: 1.5", None, True),
    ],
)
def test_timeout_ms_is_ignored_with_one_warning_and_never_drops_the_profile(
    line: str, stored: int | None, not_an_integer: bool
) -> None:
    """#604 (ADR-0276 D2) — the key does nothing, so it can no longer cost a profile.

    It used to be validated as a positive integer, and ``timeout_ms: -5`` DROPPED
    THE WHOLE PROFILE: a user lost their agent over a value that, since #604,
    would do nothing either way. Every spelling now loads with exactly ONE
    warning — the key stays in ``_KNOWN_KEYS``, so the generic "unknown
    frontmatter key" warning does not fire a second time for the same line —
    and only a real integer is stored, for the spawn-time warning to name.
    """

    result = _parse(f"---\nname: scout\ndescription: d\n{line}\n---\nbody\n")

    assert result.profile is not None, result.diagnostics
    assert _errors(result) == []
    assert result.profile.timeout_ms == stored
    warnings = [d for d in result.diagnostics if d.type == "warning"]
    assert [d.code for d in warnings] == ["unknown_field"]
    assert warnings[0].message.startswith(_IGNORED)
    assert ("not an integer" in warnings[0].message) is not_an_integer


def test_the_timeout_ms_field_stays_on_the_profile_hosts_read() -> None:
    """RPC ``agents/show`` returns ``asdict(AgentProfile)`` (``rpc/control.py``).

    Deleting the field would drop a key every host already sees, so it is kept
    (deprecated, never read) and still carries the parsed value. The RPC door
    itself is pinned in ``tests/rpc/test_runtime_control.py``.
    """

    result = _parse("---\nname: scout\ndescription: d\ntimeout_ms: 60000\n---\nbody\n")
    assert result.profile is not None
    shown = dataclasses.asdict(result.profile)
    assert shown["timeout_ms"] == 60000
