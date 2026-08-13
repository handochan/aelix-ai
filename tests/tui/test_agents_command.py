"""ADR-0196 — the ``/agents`` TUI command over a LIVE harness.

``/agents use`` is Option D: a durable overlay onto the ONE ``Args`` the harness
factory closes over, PLUS an immediate apply to the running harness through
public state + public setters. No reload — a reload would drop the live
provider/stream bindings and silently revert an in-session ``/model`` or
``/thinking`` choice — and no kernel edit (there is no ``set_system_prompt`` to
delegate to, and there is not going to be one).

That design has three properties that are easy to get wrong and are pinned here:

* the swap is OBSERVABLE through the harness's own reader
  (``_action_get_system_prompt`` → ``_current_system_prompt``), not merely
  written into a field nothing consults — the observability gap that rules out
  "mutate ``_state`` and hope";
* a SECOND ``use`` is not cumulative — ``apply_profile_to_args`` is accretive
  (``append_system_prompt.insert``, ``skills``/``extensions`` ``+=``) and
  latching (``no_extensions``/``no_skills`` only ever go ``True``), so overlaying
  b onto an a-overlaid ``Args`` would concatenate two identities;
* history survives — the whole point of not reloading.

Handlers are driven through the real ``BUILTIN_COMMANDS`` dispatch with a fake
``CommandContext`` whose ``commit`` records renderables (the house idiom from
``tests/tui/test_commands.py``). The harness is real but offline: a bare
``Model(id=…, provider=…)``, no stream function, no session, no network.

No ``__init__.py`` in ``tests/tui`` — local convention.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.skills import load_skills
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Model
from aelix_coding_agent.agents import compose_system_prompt
from aelix_coding_agent.agents.resolver import apply_profile_to_args
from aelix_coding_agent.agents.service import AgentProfileService
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _resolve_active_tools,
    _resolve_append_chunks,
    _resolve_system_prompt,
)
from aelix_coding_agent.tools import ALL_TOOL_NAMES
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    match_command,
)
from rich.console import Console

_EXT_TOOL = "ext_probe"


def _render(renderable: object) -> str:
    buffer = io.StringIO()
    # Wide on purpose: the ``/agents show`` dry-run line is a shell-quoted flag
    # string containing an absolute tmp path, and Rich would WRAP it at a
    # narrower width — turning a substring assertion into a function of how long
    # pytest happened to make ``tmp_path``.
    Console(file=buffer, width=300, no_color=True).print(renderable)
    return buffer.getvalue()


class _FakeChrome:
    pass


def _write_profile(directory: Path, name: str, frontmatter: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(f"---\n{frontmatter.strip()}\n---\n{body}\n", encoding="utf-8")
    return path


class _Bench:
    """A live harness + service + ``CommandContext``, all wired to each other.

    ``parsed`` is the same object the service mutates, mirroring production
    where the harness factory closed over it — a copy here would make every
    "survives a rebuild" claim untestable.
    """

    def __init__(self, tmp_path: Path) -> None:
        # ``tests/tui/conftest.py`` already points AELIX_CODING_AGENT_DIR at
        # ``tmp_path/"agent"``; the service is given the same root explicitly so
        # the user tier is unambiguous rather than ambient.
        self.agent_dir = tmp_path / "agent"
        self.user_agents = self.agent_dir / "agents"
        self.user_agents.mkdir(parents=True, exist_ok=True)
        self.cwd = tmp_path / "proj"
        self.cwd.mkdir(exist_ok=True)

        # Seven built-in NAMES plus one extension-shaped tool: the
        # ``--no-builtin-tools`` filter partitions purely on ``ALL_TOOL_NAMES``,
        # so real tool closures would add nothing but startup cost.
        tools = [AgentTool(name=name, description=name) for name in sorted(ALL_TOOL_NAMES)]
        tools.append(AgentTool(name=_EXT_TOOL, description="extension tool"))

        self.harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(id="probe-model", provider="probe"),
                system_prompt="BASELINE_BASE",
                tools=tools,
            )
        )

        self.parsed = Args()
        self.baseline = copy.deepcopy(self.parsed)
        self.skills_holder: dict[str, Any] = {"result": load_skills([])}
        self.service = AgentProfileService(
            cwd=str(self.cwd),
            project_trusted=True,
            parsed=self.parsed,
            baseline=self.baseline,
            skills_holder=self.skills_holder,
            agent_dir=str(self.agent_dir),
            model_registry=None,
        )
        self.committed: list[Any] = []
        self.ctx = CommandContext(
            chrome=_FakeChrome(),  # type: ignore[arg-type]
            harness=self.harness,
            commit=self.committed.append,
            cwd=str(self.cwd),
            commands=list(BUILTIN_COMMANDS),
            agent_profiles=self.service.list,
            use_agent=self._use_agent,
        )

    async def _use_agent(self, name: str | None) -> str:
        return await self.service.use(name, harness=self.harness)

    async def run(self, line: str) -> str:
        """Dispatch ``/agents …`` and return everything it committed, rendered."""

        self.committed.clear()
        command = match_command(line, self.ctx.commands)
        assert command is not None and command.handler is not None
        args = line.split(maxsplit=1)
        await command.handler(self.ctx, args[1] if len(args) > 1 else "")
        return "".join(_render(item) for item in self.committed)

    def expected_prompt_for(self, profile: Any) -> str:
        """The prompt an overlay of ``profile`` onto the PRISTINE baseline yields.

        Computed through the same two entry helpers the harness factory uses, so
        this is "what a rebuild would produce", not a re-implementation of it.

        ``skills=`` is MANDATORY here, and its absence was not a small bug. This
        helper is a DOUBLE for production, and a double that omits the same
        argument production omits compares a broken build against a broken
        expectation and passes forever. That is exactly what happened: #115's
        catalog was silently dropped by ``/agents use`` and this file stayed
        green, because both sides took the same ``None`` default.

        The holder is read rather than a captured list, because ``use``
        REPLACES it in place — so this sees the post-``use`` skills, which is
        what production composes from.
        """

        reference = copy.deepcopy(self.baseline)
        apply_profile_to_args(reference, profile, provided=reference.provided)
        return compose_system_prompt(
            _resolve_system_prompt(reference, str(self.cwd)),
            _resolve_append_chunks(
                reference,
                str(self.cwd),
                skills=self.skills_holder["result"].skills,
            ),
        )


@pytest.fixture()
def bench(tmp_path: Path) -> _Bench:
    return _Bench(tmp_path)


# === /agents list ============================================================


async def test_agents_list_renders_table(bench: _Bench) -> None:
    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon\nmodel: claude-sonnet-4-5\n"
        "tools: [read, grep]",
        "You are SCOUT.",
    )
    _write_profile(
        bench.user_agents,
        "builder",
        "name: builder\ndescription: Writes code",
        "You are BUILDER.",
    )

    out = await bench.run("/agents list")
    for token in ("NAME", "SCOPE", "MODEL", "TOOLS", "DESCRIPTION"):
        assert token in out
    assert "scout" in out and "builder" in out
    assert "Read-only recon" in out
    assert "user" in out


async def test_agents_list_renders_unset_fields_as_dashes(bench: _Bench) -> None:
    """A profile with no ``model:``/``tools:`` must never print ``None``.

    ``tools`` is THREE-valued and the renderer must not flatten it: absent
    (inherit the ambient set) and ``tools: []`` (no tools at all) mean opposite
    things, and showing both as the same cell is the same collapse that made
    ``--tools ''`` enable everything.
    """

    _write_profile(
        bench.user_agents,
        "plain",
        "name: plain\ndescription: Nothing set",
        "You are PLAIN.",
    )
    _write_profile(
        bench.user_agents,
        "toolless",
        "name: toolless\ndescription: No tools at all\ntools: []",
        "You are TOOLLESS.",
    )

    out = await bench.run("/agents list")
    assert "None" not in out
    assert "—" in out  # the unset-model / inherit-tools cell
    assert "(none)" in out  # the explicit empty allowlist, distinct from "—"


async def test_agents_list_surfaces_scan_diagnostics(bench: _Bench) -> None:
    """A profile that failed to parse is ABSENT from the table by definition.

    The diagnostics panel is its only surface — without it, a typo'd profile is
    indistinguishable from one that was never written.
    """

    _write_profile(
        bench.user_agents,
        "broken",
        "name: broken\ndescription: Missing its body",
        "",
    )

    out = await bench.run("/agents list")
    assert "diagnostics" in out.lower()
    assert "body is required" in out


# === /agents show ============================================================


async def test_agents_show_renders_resolved_flags(bench: _Bench) -> None:
    """The dry run is the auditable half: the EXACT flags the profile projects to."""

    path = _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon\nmodel: claude-sonnet-4-5\n"
        "provider: anthropic\ntools: [read, grep]\nthinking: high",
        "You are SCOUT.",
    )

    out = await bench.run("/agents show scout")
    assert "--model claude-sonnet-4-5" in out
    assert "--provider anthropic" in out
    # ``--tools`` is ONE comma-separated token: args.py's branch overwrites
    # rather than accumulating, so emitting it twice would silently drop a tool.
    assert "--tools read,grep" in out
    assert "--thinking high" in out
    assert "--append-system-prompt-file" in out
    assert str(path) in out
    # The body preview — "what will this identity actually say".
    assert "You are SCOUT." in out


async def test_agents_show_omits_unset_fields(bench: _Bench) -> None:
    """No literal ``None`` in either panel, and no flag for an unset field."""

    _write_profile(
        bench.user_agents,
        "plain",
        "name: plain\ndescription: Nothing set",
        "You are PLAIN.",
    )

    out = await bench.run("/agents show plain")
    assert "None" not in out
    assert "--model" not in out
    assert "--provider" not in out
    assert "--thinking" not in out
    assert "--tools" not in out


async def test_agents_show_unknown_name_is_a_message_not_a_raise(
    bench: _Bench,
) -> None:
    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon",
        "You are SCOUT.",
    )

    out = await bench.run("/agents show nope")
    assert "nope" in out
    assert "scout" in out  # the available names


# === /agents use — the live swap =============================================


async def test_agents_use_swaps_system_prompt_live(bench: _Bench) -> None:
    """Both the state field AND the harness's own reader reflect the swap.

    ``_action_get_system_prompt`` → ``_current_system_prompt`` is what the
    extension API and the RPC surface read. Asserting only
    ``state.system_prompt`` would pass against a write that the harness itself
    never observes — the observability gap that kills the "mutate the private
    ``_state``" option.
    """

    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon",
        "You are SCOUT.",
    )

    before = bench.harness.state.system_prompt
    out = await bench.run("/agents use scout")

    assert "scout" in out
    assert bench.harness.state.system_prompt != before
    assert "You are SCOUT." in bench.harness.state.system_prompt
    assert (
        bench.harness._action_get_system_prompt()
        == bench.harness.state.system_prompt
    )
    # The DURABLE half: the same ``Args`` a rebuild would read now carries the
    # identity, so /new, /fork and /resume inherit it.
    assert "You are SCOUT." in bench.parsed.append_system_prompt


async def test_agents_use_keeps_the_skills_catalog_in_the_live_prompt(
    bench: _Bench,
) -> None:
    """#115 REOPENED ON THIS PATH, and this is the test that was missing.

    ``/agents use`` rebuilds ``harness.state.system_prompt`` itself (there is no
    kernel setter), and it was calling ``_resolve_append_chunks`` with no
    ``skills=``. So the model lost the catalog while ``set_skills`` — three
    lines below in the same function — kept ``/skills`` and the status panel
    listing the very same skills. That is the human/model split #115 exists to
    close, reintroduced by #115's own commit.

    Measured before the fix: factory build 3885 chars containing
    ``<available_skills>``, the same identity after ``/agents use`` 3345 chars
    containing neither the block nor the skill's description.

    The whole file stayed green through it because ``expected_prompt_for``
    omitted ``skills=`` too — see its docstring. A real skill on disk is what
    gives this assertion something to bite on; with the bench's default empty
    ``load_skills([])`` it would pass against the broken build.
    """

    skill_dir = bench.cwd / ".aelix" / "skills" / "zorb-probe"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: zorb-probe\ndescription: ZORBLAX-9137 marker.\n---\nBODY_MARKER\n",
        encoding="utf-8",
    )
    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon",
        "You are SCOUT.",
    )

    await bench.run("/agents use scout")

    prompt = bench.harness.state.system_prompt
    assert "You are SCOUT." in prompt, "precondition: the identity was applied"
    assert "<available_skills>" in prompt
    assert "ZORBLAX-9137" in prompt
    # Progressive disclosure holds here too — the catalog carries metadata only.
    assert "BODY_MARKER" not in prompt
    # And the human-facing half still agrees, which is the point of the pairing.
    assert "zorb-probe" in [s.name for s in bench.harness.skills]


async def test_agents_use_twice_is_not_cumulative(bench: _Bench) -> None:
    """``use a`` then ``use b`` ⇒ exactly ``overlay(baseline, b)``.

    Equality against a reference overlay computed from the PRISTINE baseline is
    the assertion that matters: a containment check ("b's body is present")
    would also pass on a prompt that carried BOTH identities, which is the
    failure the baseline reset exists to prevent. ``apply_profile_to_args``
    inserts into ``append_system_prompt`` and ``+=``s ``skills``/``extensions``,
    so without the reset the second switch concatenates rather than replaces.
    """

    _write_profile(
        bench.user_agents,
        "alpha",
        "name: alpha\ndescription: First identity",
        "You are ALPHA.",
    )
    path_b = _write_profile(
        bench.user_agents,
        "beta",
        "name: beta\ndescription: Second identity",
        "You are BETA.",
    )

    from aelix_coding_agent.agents.discovery import load_profile_file

    profile_b = load_profile_file(
        str(path_b), cwd=bench.cwd, agent_dir=str(bench.agent_dir)
    ).profile
    assert profile_b is not None

    await bench.run("/agents use alpha")
    await bench.run("/agents use beta")

    assert bench.harness.state.system_prompt == bench.expected_prompt_for(profile_b)
    assert "You are ALPHA." not in bench.harness.state.system_prompt
    assert bench.parsed.append_system_prompt == ["You are BETA."]


async def test_agents_use_none_restores_baseline(bench: _Bench) -> None:
    """``/agents use --none`` returns to the identity the process launched with."""

    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon\nbuiltin_tools: false",
        "You are SCOUT.",
    )

    await bench.run("/agents use scout")
    assert "You are SCOUT." in bench.harness.state.system_prompt

    out = await bench.run("/agents use --none")
    assert "cleared" in out.lower()
    assert "You are SCOUT." not in bench.harness.state.system_prompt
    assert bench.parsed.append_system_prompt == []
    assert bench.service.active is None
    # ``None`` means "all tools active", which the kernel expresses as the full
    # registry rather than a null filter — otherwise clearing a profile could
    # not give back the tools it cut.
    assert bench.harness.state.active_tool_names is not None
    assert set(bench.harness.state.active_tool_names).issuperset(ALL_TOOL_NAMES)


async def test_agents_use_preserves_history(bench: _Bench) -> None:
    """No reload ⇒ no lost messages. This is the whole reason for Option D."""

    bench.harness.state.messages.append(
        UserMessage(content=[TextContent(text="earlier turn")])
    )
    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon",
        "You are SCOUT.",
    )

    await bench.run("/agents use scout")

    assert len(bench.harness.state.messages) == 1
    assert bench.harness.state.messages[0].content[0].text == "earlier turn"


async def test_agents_use_no_builtin_tools_excludes_builtins(bench: _Bench) -> None:
    """``builtin_tools: false`` applies the SAME filter the factory applies.

    Written twice (pre-build in the factory, post-build here) only because one
    runs before the tool registry exists and one after; the emission rule is
    identical, and a divergence would mean ``/agents use scout`` and
    ``--agent scout`` produced different tool sets from one file.
    """

    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon\nbuiltin_tools: false",
        "You are SCOUT.",
    )

    await bench.run("/agents use scout")

    active = bench.harness.state.active_tool_names
    assert active is not None
    assert ALL_TOOL_NAMES.isdisjoint(active)
    assert active == [_EXT_TOOL]
    # Non-destructive: the built-ins are inactive, not deregistered.
    registered = {tool.name for tool in bench.harness.state.tools}
    assert registered.issuperset(ALL_TOOL_NAMES)


async def test_agents_use_rejects_profile_with_extensions(
    bench: _Bench, tmp_path: Path
) -> None:
    """A profile that declares ``extensions:`` is REFUSED in-session, loudly.

    Loading new code genuinely needs the harness factory
    (``extensions/loader.py`` runs at build time), and quietly ignoring the field
    would hand the user an identity missing exactly the tools it advertises. The
    message names ``--agent`` as the way to get it.
    """

    extension = tmp_path / "tool_ext.py"
    extension.write_text("def setup(aelix):\n    pass\n", encoding="utf-8")
    _write_profile(
        bench.user_agents,
        "tooled",
        f"name: tooled\ndescription: Brings its own extension\n"
        f"extensions: [{extension}]",
        "You are TOOLED.",
    )

    before = bench.harness.state.system_prompt
    out = await bench.run("/agents use tooled")

    assert "extensions" in out
    assert "--agent tooled" in out
    # Refused BEFORE any mutation — neither half of Option D was applied.
    assert bench.harness.state.system_prompt == before
    assert bench.parsed.append_system_prompt == []
    assert bench.service.active is None


async def test_failed_use_leaves_args_and_harness_untouched(bench: _Bench) -> None:
    """A ``use`` that the KERNEL rejects mid-apply must roll back BOTH halves.

    Regression for the review's proven half-commit. Refusing early is not enough:
    ``harness.set_active_tools`` validates every name against ``state.tools`` and
    raises (``core.py:3525-3532``) for a typo, an MCP tool whose server never
    connected, or an extension tool under ``inherit_extensions: false`` — none of
    which profile PARSING can catch. Without the rollback the raise left
    ``parsed.tools`` carrying the rejected names and the prompt already swapped,
    the handler committed a red line the user read as "nothing happened", and the
    next ``/new`` — which disposes the live harness BEFORE rebuilding — died in
    ``AgentHarness.__init__`` against the same validator.

    The last assertion is the one that matters: the durable half must still build.
    """

    _write_profile(
        bench.user_agents,
        "typo",
        "name: typo\ndescription: Names a tool that does not exist\n"
        "tools: [reed, bash]",
        "You are TYPO.",
    )

    before_prompt = bench.harness.state.system_prompt
    before_tools = bench.harness.state.active_tool_names
    before_skills = bench.skills_holder["result"]

    out = await bench.run("/agents use typo")
    assert "reed" in out  # the kernel's own message reached the user

    assert bench.parsed.tools == []
    assert bench.parsed.append_system_prompt == []
    assert bench.service.active is None
    assert bench.harness.state.system_prompt == before_prompt
    assert bench.harness.state.active_tool_names == before_tools
    assert bench.skills_holder["result"] is before_skills

    # The consequence the rollback exists for: a rebuild from ``parsed`` works.
    AgentHarness(
        AgentHarnessOptions(
            model=Model(id="probe-model", provider="probe"),
            tools=list(bench.harness.state.tools),
            active_tool_names=_resolve_active_tools(bench.parsed),
        )
    )


async def test_agents_use_refuses_project_scope_without_approval(
    bench: _Bench,
) -> None:
    """In-session switching to a PROJECT profile needs the same consent ``--agent``
    asks for.

    Directory trust is a yes-once decision ancestors inherit; it is not consent to
    a project-local identity file, which additionally WINS a ``name`` collision
    against the user's own. ``--agent`` therefore prompts per identity — and
    ``/agents use`` resolves through the very same tiers, so without a gate it was
    the unguarded twin of a guarded flag. ``confirm_project`` is :data:`None` on
    this bench (exactly as it is for any headless embedder), and "cannot ask"
    means refuse.
    """

    _write_profile(
        bench.cwd / ".aelix" / "agents",
        "reviewer",
        "name: reviewer\ndescription: The REPO's reviewer",
        "You are the PROJECT reviewer.",
    )

    before = bench.harness.state.system_prompt
    out = await bench.run("/agents use reviewer")

    assert "project-local" in out
    assert "--agent reviewer" in out
    assert bench.service.active is None
    assert bench.harness.state.system_prompt == before


async def test_agents_use_allows_project_scope_once_confirmed(
    bench: _Bench,
) -> None:
    """The gate is a CONSENT gate, not a ban — a granted callback lets it through.

    Pins the seam ``cli/entry.py`` fills from ``--approve`` (and that a TUI-native
    modal can fill later) rather than the refusal alone, so a future
    "just delete the callback" simplification fails loudly.
    """

    _write_profile(
        bench.cwd / ".aelix" / "agents",
        "reviewer",
        "name: reviewer\ndescription: The REPO's reviewer",
        "You are the PROJECT reviewer.",
    )

    asked: list[str] = []

    async def _grant(profile: Any) -> bool:
        asked.append(profile.name)
        return True

    bench.service.confirm_project = _grant

    await bench.run("/agents use reviewer")

    assert asked == ["reviewer"]
    assert bench.service.active is not None
    assert "You are the PROJECT reviewer." in bench.harness.state.system_prompt


async def test_agents_use_reports_flags_that_beat_the_profile(
    bench: _Bench, tmp_path: Path
) -> None:
    """A half-applied identity is REPORTED, never silently returned as success.

    ``ProfileApplication.skipped`` used to be computed and read by nothing in
    production, so a profile whose ``model:``/``skills:``/``tools:`` lost to an
    explicit CLI flag applied partially and said so nowhere.
    """

    bench.parsed.model = "cli-model"
    bench.parsed.provided.add("model")
    bench.baseline.model = "cli-model"
    bench.baseline.provided.add("model")

    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon\nmodel: profile-model",
        "You are SCOUT.",
    )

    out = await bench.run("/agents use scout")

    assert "model" in out
    assert "override" in out.lower()
    assert bench.parsed.model == "cli-model"


async def test_agents_use_unknown_name_is_a_message_not_a_raise(
    bench: _Bench,
) -> None:
    """``ProfileError`` reaches the user as a committed line; the REPL survives."""

    out = await bench.run("/agents use nope")
    assert "nope" in out
    assert bench.service.active is None


async def test_agents_use_without_a_name_does_not_clear(bench: _Bench) -> None:
    """A bare ``/agents use`` asks for a name rather than clearing the identity.

    Clearing is a real change to what the agent is; it has to be asked for by
    name (``--none``), not fall out of a typo.
    """

    _write_profile(
        bench.user_agents,
        "scout",
        "name: scout\ndescription: Read-only recon",
        "You are SCOUT.",
    )
    await bench.run("/agents use scout")

    out = await bench.run("/agents use")
    assert "--none" in out
    assert bench.service.active is not None
    assert "You are SCOUT." in bench.harness.state.system_prompt


# === Headless degradation ====================================================


async def test_agents_commands_noop_without_service() -> None:
    """Both callbacks are optional; every branch degrades to a message.

    ``run_tui`` leaves them ``None`` when ``entry.py`` built no service, so a
    handler that assumed them would take down the REPL rather than the feature.
    """

    committed: list[Any] = []
    ctx = CommandContext(
        chrome=_FakeChrome(),  # type: ignore[arg-type]
        harness=object(),  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/work",
        commands=list(BUILTIN_COMMANDS),
    )
    command = match_command("/agents", ctx.commands)
    assert command is not None and command.handler is not None

    for args in ("", "list", "show scout", "use scout", "use --none"):
        committed.clear()
        await command.handler(ctx, args)
        rendered = "".join(_render(item) for item in committed)
        assert "unavailable" in rendered.lower()


async def test_agents_is_registered_exactly_once() -> None:
    """``/agents`` is in the registry and collides with nothing."""

    names = [command.name for command in BUILTIN_COMMANDS]
    assert names.count("agents") == 1
    command = match_command("/agents", BUILTIN_COMMANDS)
    assert command is not None and command.handler is not None


# === #152 — a profile whose model this build cannot run =======================


@pytest.fixture
def _adapters_registered() -> Any:  # noqa: PT004
    """Register a real adapter, and prove the fixture actually bites.

    ``is_runnable`` returns **True** when NO provider is registered — the
    deliberate "adapters not wired, so do not filter" degrade that
    ``model_picker`` relies on. A test for this gate that skips this step
    therefore passes against the BROKEN build too, which is the
    passes-by-construction trap this repo has been bitten by before. The
    assertions below are the fixture's own health check.
    """

    from aelix_ai import api_registry
    from aelix_ai.providers import anthropic
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
    from aelix_coding_agent.core.runnable_models import is_runnable

    # ``register_all`` writes a PROCESS-GLOBAL dict that other modules hold a
    # reference to, so it is restored IN PLACE on teardown. Without this, every
    # later test in the same process sees a registry it never asked for: the
    # first version of this fixture turned 21 unrelated tests red, which is the
    # #129 cross-suite-pollution shape, self-inflicted.
    saved = dict(api_registry._PROVIDERS)
    try:
        unrunnable = resolve_model("mistral-large-latest", "mistral", None, None)
        # Idempotent: by the second test in this file the adapters are there.
        if is_runnable(unrunnable):
            anthropic.register_all()
        assert is_runnable(unrunnable) is False, (
            "the fixture model must be unrunnable once an adapter is registered "
            "— otherwise every assertion below passes against the broken build"
        )
        yield unrunnable
    finally:
        api_registry._PROVIDERS.clear()
        api_registry._PROVIDERS.update(saved)


async def test_agents_use_refuses_a_model_this_build_cannot_run(
    tmp_path: Path, _adapters_registered: Any
) -> None:
    """#152 — the refusal comes BEFORE the switch is reported as a success.

    Previously ``/agents use`` reported "Agent profile: badmodel (user)" and the
    first turn then raised ``No provider registered for api='mistral-conversations'``
    — an internal developer string, printed twice, which ``runnable_models``'
    own docstring names as the thing it exists to prevent.
    """

    bench = _Bench(tmp_path)
    _write_profile(
        bench.user_agents,
        "badmodel",
        "name: badmodel\ndescription: Unrunnable\n"
        "model: mistral-large-latest\nprovider: mistral",
        "BADMODEL BODY",
    )
    before = bench.harness.state.model

    rendered = await bench.run("/agents use badmodel")

    assert "no adapter for" in rendered
    assert "mistral-conversations" in rendered
    # The internal string must not be what the user reads.
    assert "No provider registered" not in rendered
    assert "Agent profile: badmodel" not in rendered
    # Both halves rolled back: nothing was half-applied.
    assert bench.harness.state.model is before
    assert bench.service.active is None
    assert "BADMODEL BODY" not in (bench.harness.state.system_prompt or "")


async def test_agents_use_still_applies_a_runnable_model(
    tmp_path: Path, _adapters_registered: Any
) -> None:
    """The gate must refuse ONLY the unrunnable — a profile on a live adapter
    still switches, or the fix would have closed the command instead of the
    hole."""

    bench = _Bench(tmp_path)
    _write_profile(
        bench.user_agents,
        "good",
        "name: good\ndescription: Runnable\n"
        "model: claude-sonnet-4-5\nprovider: anthropic",
        "GOOD BODY",
    )

    rendered = await bench.run("/agents use good")

    assert "Agent profile: good" in rendered
    assert "no adapter for" not in rendered
    assert bench.harness.state.model.id == "claude-sonnet-4-5"


async def test_agents_list_flags_an_unrunnable_model(
    tmp_path: Path, _adapters_registered: Any
) -> None:
    """The row says so before the user picks it, not only after."""

    bench = _Bench(tmp_path)
    _write_profile(
        bench.user_agents,
        "badmodel",
        "name: badmodel\ndescription: Unrunnable\n"
        "model: mistral-large-latest\nprovider: mistral",
        "BADMODEL BODY",
    )
    _write_profile(
        bench.user_agents,
        "good",
        "name: good\ndescription: Runnable\n"
        "model: claude-sonnet-4-5\nprovider: anthropic",
        "GOOD BODY",
    )

    rendered = await bench.run("/agents list")

    assert "badmodel" in rendered
    assert "no adapter in this build" in rendered
    # Annotated, never hidden — the profile is still the user's file and still
    # usable under a build that has the adapter.
    assert "mistral-large-latest" in rendered
    # And the runnable one carries no annotation.
    good_line = next(
        line for line in rendered.splitlines() if "claude-sonnet-4-5" in line
    )
    assert "no adapter" not in good_line


async def test_is_runnable_degrades_to_true_with_no_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason the fixture above has to register an adapter at all.

    With an EMPTY provider registry ``is_runnable`` answers True for everything —
    the deliberate "adapters not wired, so do not filter" degrade the model picker
    depends on. Pinning it here means a future change that removes the degrade
    shows up as this test failing, rather than as the #152 gate silently
    tightening in environments that never registered a provider.
    """

    from aelix_ai import api_registry
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_model
    from aelix_coding_agent.core.runnable_models import is_runnable

    monkeypatch.setattr(api_registry, "_PROVIDERS", {})
    model = resolve_model("mistral-large-latest", "mistral", None, None)
    assert is_runnable(model) is True
