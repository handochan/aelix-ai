"""ADR-0196 — ``--agent`` / ``--agent-file`` end to end through ``_async_main``.

The overlay sits at ``cli/entry.py:1406-1486``: AFTER the Project Trust gate
(project profiles are inert until the directory is trusted) and BEFORE
``scan_extension_manifests`` / ``_resolve_skill_dirs`` / the harness factory, so
everything a profile can set lands on the ONE ``Args`` the factory closes over.

Assertions are made on the built :class:`AgentHarnessOptions` (and the harness),
never on ``Args``. That is deliberate and load-bearing: an ``Args``-level
assertion cannot tell a wired field from an inert one — which is exactly how
``--thinking`` shipped with one writer and zero readers, and how ``tools: ()``
collapsed onto "all tools active".

Two tests here pin defects the review PROVED rather than hypothesised, and both
are about ORDER, not features:

* ``test_profile_model_beats_persisted_default_model`` — the settings
  default-model seed used to run upstream of the overlay, so a profile's
  ``model:`` was inert for anyone who had ever run ``/model``. The seed now runs
  below the overlay and consults ``parsed.provided``.
* ``test_agent_with_api_key_pins_profile_provider`` — ``--api-key`` resolved and
  PINNED the runtime key upstream of the overlay too, so it either refused a run
  the profile could have driven or attached the key to the wrong provider.

The run is stopped at ``create_agent_session_runtime`` (``cli/entry.py:1833``),
after the factory has built the harness and before any turn: no network, no API
keys, no registry auth.
"""

from __future__ import annotations

import importlib
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.cli import entry as entry_mod
from aelix_coding_agent.cli.project_trust import ProjectTrustStore

_BUILT = 42
"""Sentinel exit code: the run reached the first harness build (see
:func:`_run_to_harness`). Any other value means it bailed earlier, which is the
assertion for every refusal test below."""


class _StopAfterBuild(Exception):
    """Raised by the runtime spy once the harness + options exist."""


class _FakePipedStdin:
    """Non-tty stdin → ``_async_main`` resolves to print mode (no TUI)."""

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return ""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A hermetic agent dir + settings file + project cwd.

    ``OPENROUTER_*`` are cleared because ``resolve_model``'s FIRST branch is
    OpenRouter-from-env, which would replace the model these profiles name (and
    make the suite depend on a developer's real environment). ``AELIX_MCP_CONFIG``
    likewise, so no server is ever contacted.
    """

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)

    user_agents = agent_dir / "agents"
    user_agents.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    return {
        "root": tmp_path,
        "agent_dir": agent_dir,
        "user_agents": user_agents,
        "proj": proj,
        "settings": tmp_path / "settings.json",
    }


def _write_profile(directory: Path, name: str, frontmatter: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


async def _run_to_harness(
    argv: list[str],
    cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
) -> int:
    """Run ``_async_main`` in ``cwd``, stopping at the first harness build.

    Captures the ``AgentHarnessOptions`` the factory produced AND the harness it
    produced them into, so a test can assert on either without re-deriving them.
    """

    monkeypatch.chdir(cwd)
    real_build = entry_mod._build_harness_options

    async def _spy_build(parsed: Any, session: Any, **kwargs: Any) -> Any:
        options = await real_build(parsed, session, **kwargs)
        captured["options"] = options
        captured["parsed"] = parsed
        # Kept so a test can REBUILD after mutating ``parsed`` — which is what a
        # later /new, /fork or /resume does, and the only way to see a durable
        # half that ``/agents use`` left unrunnable.
        captured["rebuild"] = lambda: real_build(parsed, session, **kwargs)
        return options

    async def _spy_create(harness: Any, factory: Any, **kwargs: Any) -> Any:
        captured["harness"] = harness
        raise _StopAfterBuild

    # ``entry.py`` imports the service INSIDE ``_async_main`` (the import is
    # guarded so a missing module degrades to "/agents unavailable"), so patching
    # the module attribute is what reaches it. Capturing the real instance is the
    # only way to assert on ``baseline``, which is built from a snapshot taken
    # ~370 lines above the construction site.
    service_mod = importlib.import_module("aelix_coding_agent.agents.service")
    real_service = service_mod.AgentProfileService

    def _spy_service(**kwargs: Any) -> Any:
        service = real_service(**kwargs)
        captured["service"] = service
        return service

    monkeypatch.setattr(service_mod, "AgentProfileService", _spy_service)
    monkeypatch.setattr(entry_mod, "_build_harness_options", _spy_build)
    monkeypatch.setattr(entry_mod, "create_agent_session_runtime", _spy_create)
    try:
        return await entry_mod._async_main(argv)
    except _StopAfterBuild:
        return _BUILT


def _print_argv(*extra: str) -> list[str]:
    return ["--no-session", "--print", *extra]


# === Happy path ==============================================================


async def test_agent_flag_builds_profile_harness_options(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prompt, model, provider, tools and skills all reach the harness.

    The skill directory is a RELATIVE ``skills:`` entry resolved against the
    profile's own directory (never cwd — a profile has to mean the same thing
    from any working directory), and it holds a real ``SKILL.md`` so the
    assertion is "the skill loaded", not "a path was appended to a list".
    """

    skills_dir = env["user_agents"] / "scout-skills"
    (skills_dir / "recon").mkdir(parents=True)
    (skills_dir / "recon" / "SKILL.md").write_text(
        "---\nname: recon\ndescription: recon skill\n---\nrecon body",
        encoding="utf-8",
    )
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        model: claude-sonnet-4-5
        provider: anthropic
        tools: [read, grep]
        skills: [./scout-skills]
        system_prompt: replace
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout"), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT

    options = captured["options"]
    assert options.system_prompt == "You are SCOUT."
    assert options.model.id == "claude-sonnet-4-5"
    assert options.model.provider == "anthropic"
    assert options.active_tool_names == ["read", "grep"]
    # #115 — the profile's skill, PLUS the two that ship in the wheel. Asserted
    # as membership rather than equality so adding or renaming a packaged skill
    # is not a false failure here; the packaged tier itself is pinned by
    # ``tests/cli/test_skills_in_system_prompt.py``.
    loaded = [s.name for s in captured["harness"].skills]
    assert "recon" in loaded
    assert {"writing-skills", "extending-aelix"} <= set(loaded)


async def test_agent_file_loads_an_explicit_path(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--agent-file`` accepts a profile OUTSIDE both discovery tiers."""

    path = _write_profile(
        env["root"] / "elsewhere",
        "loose",
        """
        name: loose
        description: Not in any tier
        """,
        "You are LOOSE.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent-file", str(path)), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    # ``append`` is the default, so the body joins the appends rather than
    # replacing the base coding-agent prompt.
    assert "You are LOOSE." in captured["options"].append_system_prompt


async def test_thinking_from_profile_reaches_harness_options(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``thinking:`` is WIRED, not merely validated.

    ``--thinking`` had exactly one writer (``args.py``) and zero readers before
    ADR-0196, so a profile field that rendered in ``/agents show`` would have
    done nothing at runtime. This asserts through ``AgentHarnessOptions``; an
    ``Args``-level check would have passed against the broken build.
    """

    _write_profile(
        env["user_agents"],
        "deep",
        """
        name: deep
        description: Thinks hard
        thinking: high
        """,
        "You are DEEP.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "deep"), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    assert captured["options"].thinking_level == "high"
    assert captured["harness"].state.thinking_level == "high"


# === Precedence: an explicit CLI flag always wins ============================


async def test_agent_flag_loses_to_explicit_model(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        model: claude-sonnet-4-5
        provider: anthropic
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout", "--model", "cli-chosen-model"),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.model.id == "cli-chosen-model"
    # Only the field the user actually typed is skipped — the profile's provider
    # still applies. Field-by-field precedence, not all-or-nothing.
    assert options.model.provider == "anthropic"


async def test_agent_flag_loses_to_explicit_provider(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        model: claude-sonnet-4-5
        provider: anthropic
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout", "--provider", "openai"),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.model.provider == "openai"
    assert options.model.id == "claude-sonnet-4-5"


# === Regression: profile model vs the persisted settings default =============


async def test_profile_model_beats_persisted_default_model(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persisted ``defaultModel`` must NOT stomp a profile's ``model:``.

    MEASURED defect, not hypothetical: the WP-2 settings seed
    (``if parsed.model is None and parsed.provider is None``) used to run
    immediately after the ``SettingsManager`` was constructed, i.e. UPSTREAM of
    the overlay. By the time the profile was applied, ``parsed.model`` was
    already occupied by the persisted default and the overlay's explicit-CLI
    precedence had no way to tell "the user typed it" from "settings filled it
    in" — so ``model:`` was inert for every user who had ever run ``/model``.

    The fix is the RELOCATION (seed below the overlay) plus the two
    ``parsed.provided`` membership tests. Move the block back above the overlay
    and this fails with ``persisted-default-model``.
    """

    env["settings"].write_text(
        json.dumps({"defaultModel": "persisted-default-model",
                    "defaultProvider": "openai"}),
        encoding="utf-8",
    )
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        model: claude-sonnet-4-5
        provider: anthropic
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout"), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.model.id == "claude-sonnet-4-5"
    assert options.model.provider == "anthropic"


class _RollbackHarness:
    """A ``harness`` stand-in for driving ``AgentProfileService.use`` after startup.

    ``use``'s live half only needs public state plus four setters; the real
    harness the run built is reused for ``state`` so the tool registry is real.
    The point of the test is the DURABLE half, so the setters just record.
    """

    def __init__(self, real: Any) -> None:
        self.state = real.state
        self.current_model = getattr(real, "current_model", None)
        self.models: list[Any] = []

    def set_skills(self, skills: Any) -> None:
        pass

    async def set_active_tools(self, names: list[str]) -> None:
        pass

    async def set_model(self, model: Any) -> None:
        self.models.append(model)

    async def set_thinking_level(self, level: str) -> None:
        pass


@pytest.mark.parametrize("switch_to", [None, "scout"])
async def test_agents_use_keeps_the_persisted_default_model(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch, switch_to: str | None
) -> None:
    """``/agents use`` must not wipe the model the process actually launched with.

    MEASURED defect. ``profile_baseline = copy.deepcopy(parsed)`` is snapshotted
    right after the prompt files are normalized, but ADR-0196 RELOCATED the WP-2
    settings default-model seed to BELOW the profile overlay — ~370 lines further
    down. So the baseline never carried ``defaultModel``/``defaultProvider``,
    ``use`` reset them away (``self.parsed.__dict__.update(...)``), and its
    step-4 model block is gated on ``parsed.model is not None or parsed.provider
    is not None`` — False for any profile that inherits the model, so nothing put
    them back.

    The live session survived by accident (no ``set_model``, so the running
    harness kept its model); the damage showed up on the next ``/new``, ``/fork``
    or ``/resume``, which re-derives everything from ``parsed`` and resolved
    ``Model(id='', api='unknown')`` — the #98 unrunnable state, whose startup
    warning is startup-only.

    Both arms matter: ``--none`` is advertised as "baseline CLI identity
    restored", and a model-less profile is the ordinary case.
    """

    env["settings"].write_text(
        json.dumps(
            {"defaultModel": "persisted-default-model", "defaultProvider": "openai"}
        ),
        encoding="utf-8",
    )
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon, inherits the model
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(_print_argv(), env["proj"], monkeypatch, captured)
    assert code == _BUILT
    assert captured["options"].model.id == "persisted-default-model"

    service = captured["service"]
    await service.use(switch_to, harness=_RollbackHarness(captured["harness"]))

    # THE assertion: what a /new would build next.
    rebuilt = await captured["rebuild"]()
    assert rebuilt.model.id == "persisted-default-model"
    assert rebuilt.model.provider == "openai"
    assert rebuilt.model.api != "unknown"


async def test_cli_flags_that_beat_the_profile_are_reported(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A HALF-applied identity must not be reported as a clean success.

    ``ProfileApplication.skipped`` was computed and read by nothing in
    production: ``--skill X --agent reviewer`` dropped the profile's own
    ``skills:`` and printed only the ``Agent profile: reviewer`` banner. The
    precedence is correct and intended, so this is a notice rather than a
    refusal — but running under a partially-applied identity is the same class of
    problem that makes ``--agent`` fatal-on-error, and silence is not an option.
    """

    (env["user_agents"] / "reviewer-skills" / "review").mkdir(parents=True)
    (env["user_agents"] / "reviewer-skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: review skill\n---\nreview body",
        encoding="utf-8",
    )
    own_skill = env["root"] / "my-own"
    own_skill.mkdir()
    _write_profile(
        env["user_agents"],
        "reviewer",
        """
        name: reviewer
        description: Reviews code
        skills: [./reviewer-skills]
        """,
        "You are REVIEWER.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "reviewer", "--skill", str(own_skill)),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT

    err = capsys.readouterr().err
    assert "Agent profile: reviewer" in err
    assert "CLI flags override" in err
    assert "skills" in err
    # ...and the notice is telling the truth: the profile's skill really is gone.
    #
    # #115 added a PACKAGED skills tier that always loads, so this asserts the
    # profile's OWN skill is absent rather than that the list is empty. ``== []``
    # would now fail for a reason unrelated to the override this test is about —
    # and, worse, would have kept passing if the override silently stopped
    # working while the packaged tier happened to be empty.
    assert "review" not in [s.name for s in captured["harness"].skills]


async def test_persisted_default_still_applies_without_a_profile(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control arm: relocating the seed must not DISABLE it.

    Without this, "profile wins" could be satisfied by a seed that never runs —
    which would silently break the ``/settings`` → "Default model" feature the
    block exists for (WP-2, ADR-0160).
    """

    env["settings"].write_text(
        json.dumps({"defaultModel": "persisted-default-model",
                    "defaultProvider": "openai"}),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv(), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.model.id == "persisted-default-model"
    assert options.model.provider == "openai"


# === Regression: --api-key is pinned to the PROFILE's provider ===============


async def test_agent_with_api_key_pins_profile_provider(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--agent p --api-key K`` runs, and the key lands on the PROFILE's provider.

    The ``--api-key`` block used to resolve the model and call
    ``set_runtime_api_key`` UPSTREAM of the overlay, which produced two provable
    failures:

    (A) with no persisted default, ``resolve_model(None, None, …)`` returned an
        empty provider → the hard ``--api-key requires a model`` refusal, even
        though the profile names one;
    (B) with a persisted default of a DIFFERENT vendor, the key was pinned to
        that vendor — a process-wide override on the wrong provider, since the
        same ``AuthStorage`` object is threaded on to the TUI.

    This test reproduces (A) exactly (no persisted default at all) and asserts
    the pin from (B). ``AuthStorage`` is constructed inside ``_async_main`` via a
    function-level import, so the CLASS method is patched rather than the module
    attribute.
    """

    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        model: claude-sonnet-4-5
        provider: anthropic
        """,
        "You are SCOUT.",
    )

    pinned: list[tuple[str, str]] = []

    def _record(self: Any, provider: str, key: str) -> None:
        pinned.append((provider, key))

    monkeypatch.setattr(AuthStorage, "set_runtime_api_key", _record)

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout", "--api-key", "sk-test-key"),
        env["proj"],
        monkeypatch,
        captured,
    )

    err = capsys.readouterr().err
    # Arm (A): the run got PAST the api-key gate to the harness build.
    assert "--api-key requires a model" not in err
    assert code == _BUILT
    # Arm (B): the key is attached to the profile's provider, not a leftover.
    assert pinned == [("anthropic", "sk-test-key")]


# === System-prompt composition ===============================================


async def test_replace_prompt_still_appends_agents_md(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``system_prompt: replace`` swaps the BASE; context files still append.

    ``replace`` is about the agent's identity, not about the project's rules —
    dropping ``AGENTS.md`` too would silently un-brief every profile-run agent
    about the repository it is working in. ``context_files: false`` is the
    separate, explicit switch for that (see the test below).
    """

    (env["proj"] / "AGENTS.md").write_text("PROJECT_RULES", encoding="utf-8")
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        system_prompt: replace
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout"), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.system_prompt == "You are SCOUT."
    assert any("PROJECT_RULES" in chunk for chunk in options.append_system_prompt)


async def test_profile_body_precedes_user_appends_which_precede_context_files(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append order: profile body → the user's own appends → context files.

    ``apply_profile_to_args`` does ``insert(0, body)`` on
    ``parsed.append_system_prompt`` — "FIRST among the appends", per its own
    comment — and ``_resolve_append_chunks`` appends the discovered context
    files AFTER that whole accumulator. Asserting the LIST rather than the
    joined string is what makes an off-by-one in any half fail loudly.

    The context-files position is pi's (``system-prompt.ts``: base →
    ``appendSystemPrompt`` → project context → skills, in both branches) and
    was corrected to match in #121 / ADR-0217. Until then aelix PREPENDED the
    context files, so a cloned repo's ``AGENTS.md`` — injected
    trust-independently by decision — outranked both the profile's identity and
    the chunk the user typed on their own command line.

    Why the profile body must survive a user ``--append-system-prompt`` at all:
    the appends are an ACCUMULATOR, not a scalar. The overlay's
    explicit-CLI-wins rule exists so a typed ``--model`` is not overwritten;
    applied to an accumulator it does not mean "the user's value wins", it means
    "the profile's identity is silently discarded" while stderr still reports
    ``Agent profile: scout``. The argv channel agrees with that reading —
    ``profile_to_flags`` emits ``--append-system-prompt-file``, a SEPARATE
    accumulator that ``_apply_prompt_files`` extends unconditionally — so the two
    channels the anti-drift test is meant to keep aligned diverge here whenever
    ``provided`` is non-empty (the existing parity test only exercises
    ``provided=frozenset()``, which is why it does not see this).
    """

    (env["proj"] / "AGENTS.md").write_text("PROJECT_RULES", encoding="utf-8")
    _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout", "--append-system-prompt", "USER_CHUNK"),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    appends = captured["options"].append_system_prompt

    # Severity first: the identity must be PRESENT. Losing it means the run is
    # not the agent the user asked for, which is the exact failure class
    # ``--agent`` is fatal-on-error to prevent.
    assert "You are SCOUT." in appends, (
        "the profile body vanished from the system prompt: "
        "apply_profile_to_args gates the append ACCUMULATOR on `provided`, so a "
        "user --append-system-prompt discards the profile's identity while "
        f"stderr still reports the profile as active (appends={appends!r})"
    )

    # Then the order.
    #
    # #115 appends the skills catalog as a FOURTH chunk, and its position is
    # part of the contract rather than noise: pi puts the skills section after
    # BOTH the appends and the context files (``system-prompt.ts:65-67`` /
    # ``:155-157``), so asserting it is last also pins that ordering decision.
    assert len(appends) == 4
    assert appends[0] == "You are SCOUT."
    assert appends[1] == "USER_CHUNK"
    assert "PROJECT_RULES" in appends[2]
    assert "<available_skills>" in appends[3]
    # The context chunk is pi's fence, not the pre-#121 markdown header.
    assert appends[2].startswith("<project_context>")
    assert appends[2].endswith("</project_context>\n")


async def test_context_files_false_drops_agents_md_under_replace(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``context_files: false`` + ``replace`` ⇒ the body is the WHOLE prompt.

    The only combination that yields a hermetic identity, and the one a P2
    subagent will want. Nothing from the project leaks in.
    """

    (env["proj"] / "AGENTS.md").write_text("PROJECT_RULES", encoding="utf-8")
    _write_profile(
        env["user_agents"],
        "sealed",
        """
        name: sealed
        description: Hermetic identity
        system_prompt: replace
        context_files: false
        """,
        "You are SEALED.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "sealed"), env["proj"], monkeypatch, captured
    )
    assert code == _BUILT
    options = captured["options"]
    assert options.system_prompt == "You are SEALED."
    # ``context_files: false`` drops the AGENTS.md chunk — the thing under test.
    # #115's skills catalog is a separate chunk that this flag does NOT gate,
    # and deliberately so: pi appends the skills section even under a custom
    # system prompt (``system-prompt.ts:53-77``, measured). So assert the
    # AGENTS.md content is gone rather than that the list is empty.
    assert not any("PROJECT_RULES" in chunk for chunk in options.append_system_prompt)
    # #121: the WRAPPER must be gone too. Asserting only on the content would
    # still pass if the fence were emitted around an empty body, which announces
    # project rules that are not there.
    assert not any(
        "<project_context>" in chunk for chunk in options.append_system_prompt
    )
    assert all(
        "<available_skills>" in chunk for chunk in options.append_system_prompt
    )


# === Refusals (all fatal — running the WRONG identity is a safety problem) ===


async def test_agent_and_agent_file_conflict_exits_1(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write_profile(
        env["user_agents"],
        "scout",
        """
        name: scout
        description: Read-only recon
        """,
        "You are SCOUT.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "scout", "--agent-file", str(path)),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == 1
    assert "mutually exclusive" in capsys.readouterr().err


async def test_unknown_profile_exits_1_and_lists_names(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unresolvable ``--agent`` is FATAL and the message is actionable.

    Warning-and-continuing would run the agent under a DIFFERENT identity than
    the user asked for, which is the whole class of problem this feature is
    supposed to make impossible.
    """

    for name in ("alpha", "beta"):
        _write_profile(
            env["user_agents"],
            name,
            f"""
            name: {name}
            description: profile {name}
            """,
            f"You are {name.upper()}.",
        )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "nope"), env["proj"], monkeypatch, captured
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "nope" in err
    assert "alpha" in err and "beta" in err
    # The harness was never built — the refusal precedes the factory.
    assert "options" not in captured


async def test_project_profile_inert_when_untrusted(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An untrusted ``.aelix/agents`` profile is unresolvable, and says why.

    Headless + no ``--approve`` → deny-by-default, so the project tier is never
    scanned. The message still names ``--approve`` (``_unknown_name_message``
    peeks at the suppressed tier, parse-only) because "unknown profile" for a
    file the user can plainly see on disk is a useless diagnostic.
    """

    _write_profile(
        env["proj"] / ".aelix" / "agents",
        "local",
        """
        name: local
        description: Project-local identity
        """,
        "You are LOCAL.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "local"), env["proj"], monkeypatch, captured
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "--approve" in err
    assert "options" not in captured


async def test_project_profile_denied_in_print_mode(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Directory trust is NOT consent to a project-local identity file.

    The setup is the one that isolates the confirmation gate: the directory is
    trusted via the PERSISTED store (so ``resolve_profile`` succeeds and
    ``project_trust_override`` is still ``None``), which is precisely the state a
    user reaches by answering "Trust" once — possibly for an ancestor, possibly
    months before the profile was committed. Non-interactive cannot prompt, so it
    refuses and names the three ways forward.
    """

    _write_profile(
        env["proj"] / ".aelix" / "agents",
        "local",
        """
        name: local
        description: Project-local identity
        """,
        "You are LOCAL.",
    )
    ProjectTrustStore(env["agent_dir"]).set(env["proj"], True)

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "local"), env["proj"], monkeypatch, captured
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "requires confirmation" in err
    assert "--approve" in err
    assert "options" not in captured


async def test_project_profile_runs_with_approve(
    env: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive arm: ``--approve`` is the pre-existing escape hatch (no new flag).

    Without this, the two refusal tests above would also pass against an
    implementation that simply never runs a project profile.
    """

    _write_profile(
        env["proj"] / ".aelix" / "agents",
        "local",
        """
        name: local
        description: Project-local identity
        system_prompt: replace
        """,
        "You are LOCAL.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "local", "--approve"),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == _BUILT
    assert captured["options"].system_prompt == "You are LOCAL."


async def test_no_extensions_conflict_exits_1(
    env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A profile must never silently RE-OPEN the user's ``--no-extensions``.

    Widening an explicit kill switch is the one overlay outcome that cannot be
    resolved by precedence — "the flag wins" would mean ignoring the profile's
    extensions silently, and "the profile wins" would mean executing code the
    user just switched off. So it is a refusal, raised before ``parsed`` is
    touched at all.
    """

    extension = env["root"] / "tool_ext.py"
    extension.write_text("def setup(aelix):\n    pass\n", encoding="utf-8")
    _write_profile(
        env["user_agents"],
        "tooled",
        f"""
        name: tooled
        description: Brings its own extension
        extensions: [{extension}]
        """,
        "You are TOOLED.",
    )

    captured: dict[str, Any] = {}
    code = await _run_to_harness(
        _print_argv("--agent", "tooled", "--no-extensions"),
        env["proj"],
        monkeypatch,
        captured,
    )
    assert code == 1
    assert "--no-extensions" in capsys.readouterr().err
    assert "options" not in captured
