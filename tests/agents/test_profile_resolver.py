"""ADR-0196 — ``agents/resolver.py``: the argv channel and the in-process
overlay must be the same profile.

``test_anti_drift_parity`` is the load-bearing test. It asserts on THREE planes
because an ``Args``-vs-``Args`` comparison is structurally blind: both sides can
write the same field that nothing reads, and both can collapse ``tools == ()``
onto "all tools active". The planes are

1. the ``Args`` fields a profile is allowed to touch (``PROFILE_OVERLAY_FIELDS``),
2. ``_resolve_active_tools`` — this is what catches the ``()`` hole,
3. the built ``AgentHarnessOptions`` — this is what catches an INERT field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.agents.discovery import ProfileError
from aelix_coding_agent.agents.profile import AgentProfile, parse_profile
from aelix_coding_agent.agents.resolver import (
    PROFILE_OVERLAY_FIELDS,
    apply_profile_to_args,
    profile_to_argv,
    profile_to_flags,
)
from aelix_coding_agent.cli.args import Args, parse_args
from aelix_coding_agent.cli.entry import (
    _apply_prompt_files,
    _build_harness_options,
    _resolve_active_tools,
)


def _profile(**overrides) -> AgentProfile:
    base = {
        "name": "scout",
        "description": "Recon agent",
        "body": "You are scout.",
        "file_path": "/w/agents/scout.md",
        "scope": "user",
    }
    base.update(overrides)
    return AgentProfile(**base)  # type: ignore[arg-type]


def _resolve_prompt_file_indirection(parsed: Args) -> None:
    """Fold the two file-taking prompt flags into their string twins.

    The argv channel names a FILE (a profile body overflows ``ARG_MAX`` and
    leaks in ``ps``) while the overlay channel carries the body inline, so the
    two are only comparable once the indirection is resolved.

    This calls the PRODUCTION normalizer rather than mirroring its rules. The
    mirror is what made the original parity test blind: it re-implemented the
    reader as a bare ``read_text``, so it could not see that the real reader was
    handing the profile's raw YAML frontmatter to the model whenever
    ``prompt_path`` was a profile ``.md`` — which is exactly what
    ``/agents show`` renders and invites the user to copy.
    """

    error = _apply_prompt_files(parsed)
    assert error is None, error


def _via_flags(profile: AgentProfile, prompt_path: str) -> Args:
    parsed = parse_args(profile_to_flags(profile, prompt_path=prompt_path))
    _resolve_prompt_file_indirection(parsed)
    return parsed


def _via_overlay(profile: AgentProfile) -> Args:
    parsed = Args()
    apply_profile_to_args(parsed, profile, provided=frozenset())
    return parsed


# === Emission table =========================================================


def test_flags_field_by_field() -> None:
    """The full D1.3 table on one maximal profile, flag order included."""

    profile = _profile(
        model="m1",
        provider="p1",
        tools=("read", "grep"),
        builtin_tools=False,
        skills=("/s/a", "/s/b"),
        inherit_skills=False,
        extensions=("/e/x.py",),
        inherit_extensions=False,
        system_prompt="replace",
        context_files=False,
        thinking="high",
    )

    assert profile_to_flags(profile, prompt_path="/tmp/prompt.md") == [
        "--model",
        "m1",
        "--provider",
        "p1",
        "--tools",
        "read,grep",
        "--no-builtin-tools",
        "--no-skills",
        "--skill",
        "/s/a",
        "--skill",
        "/s/b",
        "--no-extensions",
        "-e",
        "/e/x.py",
        "--no-context-files",
        "--thinking",
        "high",
        "--system-prompt-file",
        "/tmp/prompt.md",
    ]

    parsed = Args()
    application = apply_profile_to_args(parsed, profile, provided=frozenset())

    assert parsed.model == "m1"
    assert parsed.provider == "p1"
    assert parsed.tools == ["read", "grep"]
    assert parsed.no_tools is False
    assert parsed.no_builtin_tools is True
    assert parsed.no_skills is True
    assert parsed.skills == ["/s/a", "/s/b"]
    assert parsed.no_extensions is True
    assert parsed.extensions == ["/e/x.py"]
    assert parsed.no_context_files is True
    assert parsed.thinking == "high"
    assert parsed.system_prompt == "You are scout."
    assert parsed.append_system_prompt == []
    assert application.skipped == ()
    assert "model" in application.applied


def test_empty_tools_emits_no_tools_flag() -> None:
    """``tools: []`` → ``--no-tools``, NEVER ``--tools ''``.

    ``parse_args(['--tools',''])`` yields ``[]`` → ``_resolve_active_tools``
    returns ``None`` → every tool active. The inversion, proven.
    """

    profile = _profile(tools=())
    flags = profile_to_flags(profile, prompt_path="/tmp/p.md")

    assert "--no-tools" in flags
    assert "--tools" not in flags
    assert _resolve_active_tools(parse_args(flags)) == []
    assert _resolve_active_tools(parse_args(["--tools", ""])) is None


def test_absent_tools_emits_nothing() -> None:
    profile = _profile(tools=None)
    flags = profile_to_flags(profile, prompt_path="/tmp/p.md")

    assert "--no-tools" not in flags
    assert "--tools" not in flags

    parsed = Args()
    application = apply_profile_to_args(parsed, profile, provided=frozenset())
    assert parsed.tools == []
    assert parsed.no_tools is False
    assert "tools" not in application.applied
    assert "no_tools" not in application.applied


def test_model_inherit_emits_no_model_flag() -> None:
    result = parse_profile(
        "---\nname: scout\ndescription: d\nmodel: inherit\n---\nbody\n",
        file_path="/w/agents/scout.md",
        scope="user",
    )
    assert result.profile is not None

    flags = profile_to_flags(result.profile, prompt_path="/tmp/p.md")
    assert "--model" not in flags

    parsed = Args()
    apply_profile_to_args(parsed, result.profile, provided=frozenset())
    assert parsed.model is None


def test_tools_emitted_as_single_csv_token() -> None:
    """``--tools`` is NOT repeatable — ``args.py:494-498`` overwrites, so a
    repeated flag would silently keep only the last name."""

    flags = profile_to_flags(
        _profile(tools=("read", "grep", "bash")), prompt_path="/tmp/p.md"
    )

    assert flags.count("--tools") == 1
    assert flags[flags.index("--tools") + 1] == "read,grep,bash"
    assert parse_args(flags).tools == ["read", "grep", "bash"]


# === Precedence =============================================================


def test_apply_skips_fields_in_provided() -> None:
    """An explicit CLI flag ALWAYS wins; the skip is reported, not silent."""

    parsed = Args(model="cli-model", tools=["bash"])
    profile = _profile(model="profile-model", tools=("read",), thinking="low")

    application = apply_profile_to_args(
        parsed, profile, provided=frozenset({"model", "tools"})
    )

    assert parsed.model == "cli-model"
    assert parsed.tools == ["bash"]
    assert parsed.thinking == "low"
    assert set(application.skipped) == {"model", "tools"}
    assert "thinking" in application.applied


def test_apply_raises_when_no_extensions_conflicts() -> None:
    """Never silently widen the user's kill switch — and never half-apply."""

    parsed = Args(no_extensions=True)
    profile = _profile(extensions=("/e/x.py",), model="m1")

    with pytest.raises(ProfileError) as exc:
        apply_profile_to_args(
            parsed, profile, provided=frozenset({"no_extensions"})
        )

    assert "--no-extensions" in str(exc.value)
    # Validation runs BEFORE any mutation.
    assert parsed.model is None
    assert parsed.extensions == []


def test_apply_empty_tools_sets_no_tools_not_empty_list() -> None:
    parsed = Args()
    apply_profile_to_args(parsed, _profile(tools=()), provided=frozenset())

    assert parsed.no_tools is True
    assert parsed.tools == []
    assert _resolve_active_tools(parsed) == []


def test_apply_model_without_provider_emits_recompute_notice() -> None:
    """#98 split-pair guard: a persisted ``defaultProvider`` must not be able to
    impersonate an explicit ``--provider`` under a profile's model."""

    parsed = Args(provider="persisted-provider")
    application = apply_profile_to_args(
        parsed, _profile(model="m1"), provided=frozenset()
    )

    assert parsed.model == "m1"
    assert parsed.provider is None
    assert "recompute-default-provider" in application.notices


# === Anti-drift =============================================================


_ANTI_DRIFT_PROFILES = {
    "replace-with-model-and-tools": {
        "model": "claude-sonnet-4",
        "provider": "anthropic",
        "tools": ("read", "grep"),
        "skills": ("/skills/recon",),
        "thinking": "high",
        "system_prompt": "replace",
    },
    "append-with-no-tools": {
        "tools": (),
        "builtin_tools": False,
        "inherit_skills": False,
        "context_files": False,
        "system_prompt": "append",
    },
    "bare-inherit-everything": {},
}


@pytest.mark.parametrize("prompt_source", ["body-only", "profile-file"])
@pytest.mark.parametrize("case", sorted(_ANTI_DRIFT_PROFILES))
async def test_anti_drift_parity(
    case: str, prompt_source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    profile = _profile(**_ANTI_DRIFT_PROFILES[case])

    # TWO prompt sources, because P1 and P2 pass different ones and only one of
    # them was ever tested:
    #
    # * ``body-only`` — what P2's deferred temp-prompt writer will emit.
    # * ``profile-file`` — the profile's OWN ``.md``, frontmatter and all. This
    #   is what ``/agents show`` renders and shell-quotes for the user to copy,
    #   and running that command used to inject the profile's raw YAML into the
    #   system prompt. The original parity test could not see it because it
    #   wrote a body-only ``body.md`` instead of using ``profile.file_path``.
    if prompt_source == "body-only":
        prompt_file = tmp_path / "body.md"
        prompt_file.write_text(profile.body, encoding="utf-8")
    else:
        prompt_file = tmp_path / f"{profile.name}.md"
        prompt_file.write_text(
            f"---\nname: {profile.name}\ndescription: {profile.description}\n"
            f"---\n{profile.body}\n",
            encoding="utf-8",
        )

    flagged = _via_flags(profile, str(prompt_file))
    overlaid = _via_overlay(profile)

    # --- Plane 1: the Args fields a profile may touch ----------------------
    # Never the whole dataclass: ``provided`` and ``diagnostics`` legitimately
    # differ between the argv channel and the overlay channel.
    for name in sorted(PROFILE_OVERLAY_FIELDS):
        assert getattr(flagged, name) == getattr(overlaid, name), name

    # --- Plane 2: resolved active tools ------------------------------------
    # The ``tools == ()`` hole is invisible on plane 1 alone.
    assert _resolve_active_tools(flagged) == _resolve_active_tools(overlaid)

    # --- Plane 3: the actually-built harness options -----------------------
    # An inert field (a profile value that reaches ``Args`` and stops there)
    # can only be caught here. ``inherit_extensions`` defaults False → both
    # sides carry ``--no-extensions``, so this stays hermetic (no on-disk
    # extension discovery).
    assert flagged.no_extensions is True
    left = await _build_harness_options(flagged, Session(MemorySessionStorage()))
    right = await _build_harness_options(overlaid, Session(MemorySessionStorage()))

    assert left.system_prompt == right.system_prompt
    assert left.append_system_prompt == right.append_system_prompt
    assert left.active_tool_names == right.active_tool_names
    # NOTE: ``thinking_level`` is wired onto ``AgentHarnessOptions`` by plan
    # D3.7 (``entry.py``), owned by another agent this phase. The equality holds
    # either way; it only becomes a *meaningful* assertion once D3.7 lands.
    assert left.thinking_level == right.thinking_level


def test_argv_channel_prefix_oneshot_and_rpc() -> None:
    profile = _profile(model="m1")

    oneshot = profile_to_argv(
        profile, prompt_path="/tmp/p.md", oneshot=True, task="recon X"
    )
    assert oneshot[:4] == ["--mode", "json", "-p", "--no-session"]
    assert oneshot[-1] == "Task: recon X"
    assert "--model" in oneshot

    rpc = profile_to_argv(profile, prompt_path="/tmp/p.md", oneshot=False)
    assert rpc[:2] == ["--mode", "rpc"]
    assert not any(token.startswith("Task: ") for token in rpc)

    # A one-shot with no task carries no trailing message.
    bare = profile_to_argv(profile, prompt_path="/tmp/p.md", oneshot=True)
    assert not any(token.startswith("Task: ") for token in bare)
