"""Live agent-profile service — ``/agents list|show|use`` (ADR-0196).

AELIX-ORIGINAL. The three read/write operations the TUI needs, held over the
ONE mutable ``Args`` the harness factory closes over (``cli/entry.py:3676-3948``)
so an in-session identity switch is durable across every later rebuild.

**Nothing spawns.** This is Phase 1: one identity at a time, applied to the one
running agent. There is no subagent runtime, no ``agent`` tool and no
delegation — those are Phase 2.

``use`` = **Option D** (durable ``parsed`` overlay + live public-state apply).
The three rejected alternatives and why they fail:

* **A — rebuild the harness** (``AgentSessionRuntime.reload``): drops the live
  provider/stream bindings and silently reverts an in-session ``/model`` or
  ``/thinking`` choice the user made deliberately.
* **B — mutate the private ``_state`` only**: ``_current_system_prompt``
  (``core.py:4453-4458``) already reads ``_state`` as its fallback, but a
  rebuild (``/new``, ``/fork``, ``/resume``) re-derives everything from
  ``parsed`` — so the swap would evaporate on the next session action.
* **C — a kernel ``set_system_prompt``**: the kernel is read-only for P1
  (``grep -rn "set_system_prompt" packages/`` → zero hits, and it stays that
  way).

Option D writes BOTH halves: ``parsed`` (durable — survives rebuilds) and the
live ``AgentState`` + public setters (immediate — no reload, no dropped
stream). The only mirrored kernel logic is the prompt join, isolated in
:func:`.prompt.compose_system_prompt` and pinned against a real ``AgentHarness``.

Two limitations are SURFACED rather than hidden (see :meth:`AgentProfileService.use`):
a profile with a non-empty ``extensions:`` is refused in-session, and
``inherit_extensions: false`` cannot unload code that is already imported.
"""

from __future__ import annotations

import contextlib
import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aelix_ai.models import get_supported_thinking_levels

from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.runtime_bootstrap import (
    LateRoute,
    LateRouteHold,
    enrich_copilot_base_url,
    resolve_route,
    set_settings_seed_active,
    settings_seed_active,
)
from aelix_coding_agent.core.runnable_models import (
    is_runnable,
    turn_refusal,
    unsupported_message,
)
from aelix_coding_agent.core.skill_selection import effective_skills
from aelix_coding_agent.tools import ALL_TOOL_NAMES

from .discovery import (
    ProfileDiscoveryResult,
    ProfileError,
    discover_profiles,
    resolve_profile,
)
from .profile import AgentProfile
from .prompt import compose_system_prompt
from .resolver import apply_profile_to_args


@dataclass
class AgentProfileService:
    """The state ``/agents`` operates on, constructed once in ``cli/entry.py``.

    :param cwd: the session working directory — the project tier root and the
        anchor every rebuilt harness resolves context files against.
    :param project_trusted: the resolved Project Trust verdict. Gates the
        ``.aelix/agents`` tier exactly as it gates ``.aelix/skills`` and
        ``.aelix/extensions``; a ``/agents use`` of a project profile in an
        untrusted directory raises rather than prompting, because the trust
        decision was already made (and declined) at startup.
    :param parsed: the SAME :class:`Args` object the harness factory closes over
        (``cli/entry.py:3950-3952``). :meth:`use` mutates it IN PLACE — that is
        what makes a switched identity survive ``/new``, ``/fork`` and
        ``/resume``.
    :param baseline: a pristine :func:`copy.deepcopy` of ``parsed`` taken before
        any mutator ran (``cli/entry.py:2715``). Every :meth:`use` resets to it
        first, so a second switch overlays the ORIGINAL CLI intent rather than
        the previous profile.
    :param skills_holder: the ``{"result": SkillCatalog}`` box (#511: a
        ``LoadSkillsResult`` whose ``skills`` are the enabled ones) the factory
        reads on every (re)build (``cli/entry.py:3556`` / ``:3897``).
        :meth:`use` replaces its contents so a profile's ``skills:`` /
        ``inherit_skills:`` reach rebuilds too — a plain local provably would
        not, because the factory captured it once.
    :param agent_dir: agent ROOT override (``~/.aelix/agent``) for the user
        tier; :data:`None` uses ``get_agent_dir()``. Present for tests and
        embedders, mirroring ``discovery.user_agents_dir``.
    :param model_registry: the live ``ModelRegistry``, so a profile's ``model:``
        resolves through the same ladder (custom providers, extension-registered
        providers, the copilot proxy-ep host) the startup path uses.
    :param active: the profile the process launched under (``--agent`` /
        ``--agent-file``), or :data:`None`. Updated by every :meth:`use`.
    :param confirm_project: consent gate for switching to a PROJECT-scoped
        profile in-session. ``--agent`` prompts per identity at startup
        (``cli/entry.py``'s ``_confirm_project_agent``) because directory trust is
        a yes-once decision ancestors inherit and a project profile additionally
        WINS a ``name`` collision against the user's own; :meth:`use` resolves
        through the same tiers, so it needs the same gate or it is the unguarded
        twin of a guarded flag. :data:`None` (the default, and what every headless
        embedder gets) means "cannot consent" and REFUSES — fail-closed, matching
        ``project_trust.py``'s own step-6 deny-by-default.
    :param late_route: #367 — the process's late-provider rule
        (``cli/entry.py``'s ``late_rule``, the object the harness factory
        reads). A profile whose own ``model:`` or ``provider:`` applies is an
        explicit pick and switches (``provider:`` alone names the route too),
        and marks the rule explicit so the rebuilds after it keep that choice.
        Any other :meth:`use` — no ``model:``/``provider:`` of the profile
        applies (none is named, or a typed CLI flag beats each one named), or
        ``--none`` — clears the mark and, since #412, keeps the live model (see
        :meth:`use`). Only when it drops the route of the profile it replaces
        (that profile's ``model:`` or ``provider:`` applied) does it re-resolve
        the launch-derived baseline, an implicit re-resolution: where that
        lands on a provider ``session_start`` registered after the launch, it
        holds the session instead of switching there (round 3, D1). A baseline
        with no model and no provider (no ``--model``/``--provider``, no settings
        default) has nothing to re-resolve: the dropped profile's model stays.
    """

    cwd: str
    project_trusted: bool
    parsed: Args
    baseline: Args
    skills_holder: dict[str, Any]
    agent_dir: str | None = None
    model_registry: Any | None = None
    active: AgentProfile | None = None
    confirm_project: Callable[[AgentProfile], Awaitable[bool]] | None = None
    late_route: LateRoute | None = None

    def _route_applies(self, profile: AgentProfile | None) -> bool:
        """Whether ``profile``'s own ``model:`` / ``provider:`` applies over the baseline (#412).

        The question :meth:`use` asks of the profile it replaces: did that
        profile put its own route on the live model? Answered by the SAME
        overlay every application runs — the launch's ``--agent`` and each
        :meth:`use` apply onto the baseline with the baseline's ``provided`` —
        on a throwaway copy, so the answer cannot drift from what was applied.
        A profile the overlay refuses (``--no-extensions`` against its
        ``extensions:``) never applied, at launch or here: ``False``.
        """

        if profile is None:
            return False
        scratch = copy.deepcopy(self.baseline)
        try:
            application = apply_profile_to_args(scratch, profile, provided=scratch.provided)
        except ProfileError:
            return False
        return bool({"model", "provider"} & set(application.applied))

    def _live_model_notice(self, model: Any) -> str | None:
        """Why prompts on the model a :meth:`use` keeps will be refused, or ``None`` (#412).

        Asked wherever :meth:`use` leaves the live model in place: a use where
        no route of the profile applies and none is dropped, and a use that
        drops a route with nothing to re-resolve (an empty baseline). A
        late-provider hold's placeholder gets its own notice back (#367), as
        the re-resolve used to say it. Any other model with a provider is asked
        what the CLI's prompt check asks before every prompt
        (``cli/entry.py``'s ``_make_prompt_check``, which the harness factory
        installs with ``set_prompt_check``): :func:`turn_refusal` — no turn can
        run on the model at all (#370's not-found placeholder, ``api
        'unknown'``; a base URL whose ``{ENV_VAR}`` placeholders are unfilled,
        cloudflare's) — and then ``ModelRegistry.has_configured_auth``, so a
        model with no credential source gets that check's "No API key found"
        text. The note is that check's reason, not every surface's wording:
        the TUI's prompt line refuses a model :func:`turn_refusal` refuses
        BEFORE that check, with its own line (``tui/shell.py``, #189) — the
        hold's notice, the same reason (``unsupported_message``) plus "Run
        /model to select a working model." while any model is available, and
        "No provider configured. Run
        /login to add one." when none is. A service built without a registry
        (an embedder's) asks :func:`turn_refusal` alone, the structural half
        that needs none. The empty placeholder (no provider: nothing was ever
        chosen) says nothing here, as before.
        """

        held = self.late_route.held(model) if self.late_route is not None else None
        if held is not None:
            return held.notice
        if not getattr(model, "provider", ""):
            return None
        if self.model_registry is not None:
            # Function-level import, as in :meth:`use`: ``cli/entry.py`` imports
            # this package at module scope. The SAME composition the factory
            # installs with ``set_prompt_check``, so the note gives the reason
            # that check refuses with; the TUI prompt line's own gate (#189)
            # refuses a structurally unrunnable model before it, in its words
            # (see the docstring).
            from aelix_coding_agent.cli.entry import _make_prompt_check

            refusal = _make_prompt_check(self.model_registry)(model)
        else:
            refusal = turn_refusal(model)
        if refusal is None:
            return None
        return f"{refusal} No prompt will be sent for it; run /model to select a working model."

    def list(self) -> ProfileDiscoveryResult:
        """Every discoverable profile plus the scan diagnostics (``/agents list``).

        Never raises: the render path must survive a broken profile, so parse
        errors arrive as diagnostics and the good profiles still list. This is
        the deliberate counterpart to :meth:`show` / :meth:`use`, which are
        fatal.
        """

        return discover_profiles(
            self.cwd,
            project_trusted=self.project_trusted,
            agent_dir=self.agent_dir,
        )

    def show(self, name: str) -> AgentProfile:
        """Resolve ONE profile by name, or raise :class:`ProfileError`.

        Deliberately the SAME resolve ``--agent <name>`` performs, escalations
        included (unknown name, untrusted project scope, a ``skills:`` path that
        no longer exists). "What would this profile do if I ran it" is only an
        honest answer if the question is asked through the identical gate.

        The ``/agents show`` TUI handler renders from :meth:`list` instead, so a
        profile it just listed is always renderable — including the broken one
        the user is trying to debug. This method is the strict programmatic
        resolve (P2's spawn path, RPC).
        """

        return resolve_profile(
            name,
            cwd=self.cwd,
            project_trusted=self.project_trusted,
            agent_dir=self.agent_dir,
        )

    async def use(self, name: str | None, *, harness: Any) -> str:
        """Switch the LIVE agent identity; ``name=None`` restores the baseline.

        Returns the status line the caller commits into scrollback. Raises
        :class:`ProfileError` for an unresolvable profile or one this path
        genuinely cannot honour — both are reported to the user, never silently
        half-applied.

        Order of operations, all five steps load-bearing:

        1. **Resolve + refuse FIRST**, before anything mutates. A rejected
           switch must leave both ``parsed`` and the live harness exactly as
           they were — the same discipline ``apply_profile_to_args`` and
           ``_apply_prompt_files`` already follow.
        1b. **Snapshot, and roll back on ANY failure.** Refusing first is not
           sufficient, because step 4 talks to the kernel and the kernel can say
           no: ``harness.set_active_tools`` validates every name against
           ``state.tools`` and raises (``core.py:4468-4476``) for a typo, for an
           MCP tool whose server never connected, or for an extension tool under
           ``inherit_extensions: false`` — none of which profile PARSING can
           check. Without the rollback that raise left ``parsed.tools`` poisoned
           and the prompt already swapped, so the failure was reported in red
           while the next ``/new`` — which disposes the live harness BEFORE
           rebuilding (``agent_session_runtime.py:752``) — died in
           ``AgentHarness.__init__`` against the same validator and left the REPL
           with a disposed harness. Proven end to end.
        2. **Reset to the pristine baseline.** Mandatory, not hygiene:
           ``apply_profile_to_args`` is ACCRETIVE (``append_system_prompt
           .insert``, ``skills``/``extensions`` ``+=``) and LATCHING
           (``no_extensions``/``no_skills`` only ever go ``True``), so overlaying
           b onto an a-overlaid ``Args`` would concatenate the two identities.
           The ``__dict__`` update is IN PLACE because the harness factory closed
           over this exact object and a nested handler cannot rebind
           ``_async_main``'s local.
        3. **Overlay the new profile** onto the reset ``Args`` (durable half).
        4. **Apply to the live harness** through public state + setters
           (immediate half) — no reload, so the stream, the message history and
           the extension runtime all survive.

        Two honest limitations, both surfaced to the user rather than hidden:

        * A profile with a non-empty ``extensions:`` is REFUSED. Loading new code
          genuinely needs the factory (``extensions/loader.py`` runs at harness
          build time), and quietly ignoring the field would hand the user an
          identity that is missing exactly the tools it advertises.
        * ``inherit_extensions: false`` with an EMPTY ``extensions:`` applies
          everything else and returns a notice: already-imported extensions
          cannot be unloaded in-session. Refusing it instead would refuse nearly
          every profile, since ``inherit_extensions`` defaults ``False``.

        A ``/model`` choice is NOT reverted when no route of the new profile
        applies — the deliberate difference from the rejected rebuild option,
        which stomped it. #412 (owner decision 2026-10-14, option A): a use
        where no ``model:``/``provider:`` of the profile applies (none is named,
        or a typed CLI flag beats each one named), and ``--none``, keeps the
        LIVE model, whatever put it there: the session's restored record
        (#376), a ``/model`` pick, or the launch model. No resolve, no
        ``set_model``, so no ``model_change`` is recorded and a reopen comes
        back on the same model. A profile whose ``model:`` a typed ``--model``
        beats but whose ``provider:`` applies DOES name a route: it re-resolves
        (the typed model on the profile's provider) and switches, as before.
        ``parsed`` still returns to the baseline, so ``/new`` follows the
        launch inputs and ``/resume`` / ``/reload`` the session's record, as
        before. The one exception is a use that DROPS a route — the profile it
        replaces (the launch's ``--agent``, or an earlier use) had its own
        ``model:``/``provider:`` applied: that model is the profile's, so the
        baseline is re-resolved and set, held where it lands late (#367), as
        before — resolved without the settings ``defaultProvider`` a rebuild
        passes, so a bare ``--model`` id several providers serve can land
        elsewhere than ``/new`` would. With an empty baseline (no
        ``--model``/``--provider``, no settings default) there is nothing to
        re-resolve and the dropped profile's model stays, as before; since
        review round 3 that path asks for the same note as a kept model. The
        ``--none`` status says "the model is unchanged" wherever the model was
        left in place, and only "baseline CLI identity restored" where the drop
        put a model on the session (its re-resolve, or a hold's placeholder).
        ``/thinking`` is not covered: a level the baseline or the
        profile names (``--thinking``, ``thinking:``) is re-applied below on
        every path, and ``set_thinking_level`` records a
        ``thinking_level_change`` when that moves the level (a ``/thinking``
        pick reverted — a known corner left to a follow-up).
        """

        # Function-level import: ``cli/entry.py`` imports this package at module
        # scope (``entry.py:69-74``), so importing it back here at module scope
        # would close an import cycle. Reusing ITS helpers (rather than
        # re-deriving the same values) is the point — a live identity computed
        # from different inputs than a rebuilt one would silently diverge on the
        # first /new.
        from aelix_coding_agent.cli.entry import (
            _load_skill_catalog,
            _resolve_active_tools,
            _resolve_append_chunks,
            _resolve_system_prompt,
            _visible_tools,
        )

        # === 1. Resolve + refuse, BEFORE any mutation =======================
        notices: list[str] = []
        profile: AgentProfile | None = None
        if name is not None:
            profile = resolve_profile(
                name,
                cwd=self.cwd,
                project_trusted=self.project_trusted,
                agent_dir=self.agent_dir,
            )
            if profile.scope == "project":
                # The per-identity consent ``--agent`` performs at startup. A
                # trusted DIRECTORY is not consent to a project-local identity
                # file — which, on a ``name`` collision, additionally beats the
                # user's own ``~/.aelix/agent/agents/<name>.md``. Without this the
                # in-session switch was the unguarded twin of a guarded flag.
                confirmed = False
                if self.confirm_project is not None:
                    confirmed = await self.confirm_project(profile)
                if not confirmed:
                    raise ProfileError(
                        f"agent profile {profile.name!r} ({profile.file_path}) is "
                        "project-local and needs a per-identity confirmation this "
                        "session cannot ask for; relaunch with "
                        f"--agent {profile.name} (which prompts), or start aelix "
                        "with --approve."
                    )
            if profile.extensions:
                raise ProfileError(
                    f"{profile.name!r} declares extensions:, which cannot be "
                    "loaded in-session; relaunch with "
                    f"--agent {profile.name}"
                )
            if not profile.inherit_extensions:
                notices.append(
                    "inherit_extensions: false cannot unload the extensions "
                    "already imported into this process; relaunch with "
                    f"--agent {profile.name} to apply it."
                )

        # === 1b. Snapshot BOTH halves so any failure below is undoable =======
        # ``deepcopy`` of ``__dict__`` rather than of the dataclass: the point is
        # to restore the SAME ``Args`` object the factory closed over, so the
        # rollback is an in-place ``update`` exactly like the reset in step 2.
        durable_snapshot = copy.deepcopy(self.parsed.__dict__)
        previous_seed_active = settings_seed_active()
        previous_active = self.active
        previous_skills = self.skills_holder["result"]
        live_prompt = harness.state.system_prompt
        live_active_tools = (
            list(harness.state.active_tool_names)
            if harness.state.active_tool_names is not None
            else None
        )
        live_model = harness.state.model
        live_thinking = harness.state.thinking_level
        live_explicit = self.late_route.explicit if self.late_route is not None else False
        # Whether this use put a model on the harness (a drop's re-resolve or
        # hold) — the ``--none`` status says "the model is unchanged" otherwise.
        model_set = False

        try:
            # === 2. Reset to the pristine CLI baseline ======================
            self.parsed.__dict__.update(copy.deepcopy(self.baseline).__dict__)

            # === 3. Overlay (durable half) ==================================
            # #367 — whether the profile itself named the route (its ``model:``
            # or ``provider:`` applied, not beaten by a CLI flag). When neither
            # did, the model block below keeps the live model — unless this use
            # drops the route of the profile it replaces, which re-resolves the
            # launch inputs (#412).
            profile_named_route = False
            profile_named_model = False
            if profile is None:
                self.active = None
            else:
                # ``provided`` came back with the baseline, so an explicit CLI
                # flag still beats the profile on the second switch exactly as it
                # did on the first.
                #
                # The overlay's own ``ProfileError`` (an explicit
                # ``--no-extensions`` colliding with the profile's
                # ``extensions:``) is UNREACHABLE from here: step 1 already
                # refused every profile with a non-empty ``extensions:``.
                application = apply_profile_to_args(
                    self.parsed, profile, provided=self.parsed.provided
                )
                if application.skipped:
                    # The audit trail for "the profile looks ignored": an explicit
                    # CLI flag beat it. Committed with the status line because a
                    # half-applied identity reported as a clean success is the
                    # same class of problem as the wrong identity.
                    notices.append(
                        "CLI flags override "
                        f"{', '.join(application.skipped)}"
                    )
                profile_named_route = bool({"model", "provider"} & set(application.applied))
                profile_named_model = "model" in application.applied
                self.active = profile
            # #434 (ADR-0264 §4.4) — whose model string ``parsed`` now holds: the
            # baseline's (the settings default, when the launch seeded it) unless
            # the profile named a model. A provider-only profile keeps the
            # default's model string; ``resolve_route`` skips a removed default
            # only when the pair still equals it.
            set_settings_seed_active(not profile_named_model)

            # === 4. Apply to the live harness (immediate half) ==============
            #
            # Skills FIRST, because the prompt is built from them (#115). A
            # profile can add ``skills:`` paths or set ``inherit_skills: false``,
            # so the set has to be re-scanned before anything reads it — composing
            # first would install a catalog describing the PREVIOUS profile's
            # skills. Both halves of the write are required: the holder for later
            # rebuilds, ``set_skills`` for this session.
            # #511 — the same scan, dedupe and settings filter as launch and
            # ``/reload``; only the enabled skills reach the harness.
            self.skills_holder["result"] = _load_skill_catalog(
                self.parsed,
                self.cwd,
                self.project_trusted,
                getattr(harness, "settings_manager", None),
            )
            harness.set_skills(effective_skills(self.skills_holder))

            # Tools: the same three-way ``_resolve_active_tools`` result the
            # factory would compute, plus the same post-registration
            # ``--no-builtin-tools`` filter — written twice only because one runs
            # pre-build and one post-build; the emission rules are identical.
            #
            # THIS is the call that raises on a profile naming a tool the running
            # process does not have (``core.py:4468-4476``), which is why
            # everything from step 2 down sits inside the try.
            names = _resolve_active_tools(self.parsed)
            if self.parsed.no_builtin_tools and not self.parsed.no_tools:
                live = [
                    t.name for t in harness.state.tools if t.name not in ALL_TOOL_NAMES
                ]
                names = [n for n in live if n in set(names)] if names else live
            # ``None`` means "all tools active", which the kernel expresses as the
            # full registry rather than a null filter — this is what makes
            # ``/agents use --none`` actually restore the tools a prior profile
            # cut.
            await harness.set_active_tools(
                names if names is not None else [t.name for t in harness.state.tools]
            )

            # Then the prompt — AFTER the tools, which is a reordering issue
            # #120 forced and a strict improvement. It used to run first and
            # therefore composed an identity from a tool set that was about to
            # change; now the tool list it emits is the one the harness just
            # took.
            #
            # It is the one place a mirror is unavoidable: there is no kernel
            # setter, so the exact ``__init__`` join is reproduced from the exact
            # ``_build_harness_options`` inputs — and ``skills=`` is one of those
            # inputs. Omitting it is not a cosmetic slip, it is #115 reopening on
            # this path: ``set_skills`` above keeps ``/skills`` and the status
            # panel listing the profile's skills while the model is told about
            # none of them, which is precisely the human/model split #115 exists
            # to close. Measured before that argument was passed: the factory
            # build produced a 3885-char prompt containing ``<available_skills>``
            # and ``/agents use`` produced 3345 chars containing neither.
            #
            # NOT REDUNDANT with the rebuild ``set_active_tools`` just ran. That
            # rebuild only happens on a harness the factory gave a
            # ``system_prompt_rebuilder``; a harness built without one (SDK
            # callers, and every test that constructs ``AgentHarness`` directly)
            # would otherwise keep a stale prompt here, which is the regression
            # this line existed to prevent in the first place. When a rebuilder
            # IS installed the two produce the same string — same helpers, same
            # ``parsed``, same holder — and that equality is pinned by
            # ``tests/cli/test_prompt_tool_honesty.py::
            # test_the_rebuilder_and_the_agents_use_path_compose_the_same_string``.
            # (``tests/agents/test_prompt_composition.py`` pins something else:
            # that ``compose_system_prompt`` matches the kernel's own join.)
            harness.state.system_prompt = compose_system_prompt(
                _resolve_system_prompt(
                    self.parsed,
                    self.cwd,
                    project_trusted=self.project_trusted,
                    tools=_visible_tools(
                        harness.state.tools, harness.state.active_tool_names
                    ),
                ),
                _resolve_append_chunks(
                    self.parsed,
                    self.cwd,
                    skills=effective_skills(self.skills_holder),
                    project_trusted=self.project_trusted,
                ),
            )

            # #412 (owner decision 2026-10-14, option A) — a use where no
            # ``model:``/``provider:`` of the profile applies (none named, or a
            # typed flag beats each one named; ``--none``) KEEPS the live model,
            # unless it drops the route of the profile it replaces. The baseline
            # carries the launch inputs (the settings pair is back-filled into
            # it when no flag was typed, ``cli/entry.py``), and re-resolving it
            # here moved a restored session (#376) off its record onto the
            # settings model, recorded that move (``set_model`` appends
            # ``model_change``), so the next reopen came back there too, and
            # reverted a ``/model`` pick against ADR-0196 D8 (with a typed
            # ``--model`` the baseline is that flag: the re-resolve reverted a
            # pick to it and recorded it again). The profile being replaced is
            # ``previous_active`` (the launch's ``--agent`` or an earlier use):
            # when its own ``model:``/``provider:`` applied, the live model is
            # the profile's and nothing else takes it away, so that use
            # re-resolves the baseline below exactly as before.
            keep_live = not profile_named_route and not self._route_applies(previous_active)
            held: LateRouteHold | None = None
            if self.late_route is not None:
                # #367 round 3, D1 — the landing rule. A profile whose own route
                # applied is the user's pick (and the rebuilds after it keep it);
                # dropping a profile's route re-resolves the launch-derived
                # baseline, and where that lands on a late provider the session
                # holds — asked with the settings ``defaultProvider`` as a
                # rebuild resolves it, and once more below as this resolve does
                # (without it). A use that keeps the live model resolves nothing,
                # so it asks nothing; it only clears the explicit mark, so the
                # rebuilds after it hold where the launch inputs land, as before.
                self.late_route.explicit = profile_named_route
                if not keep_live:
                    held = self.late_route.hold_for(self.parsed.model, self.parsed.provider)
                    if held is None and not profile_named_route:
                        held = self.late_route.hold_for(
                            self.parsed.model, self.parsed.provider, use_default=False
                        )
            if keep_live:
                # No model is resolved and none is set: the live model stays,
                # and ``parsed.provider`` is not completed (``/new`` resolves the
                # baseline exactly as the launch did). What the user still needs
                # is a reason when the next prompt on that model will be refused
                # — a launch held on a late provider's placeholder (#367: its
                # notice again, as the re-resolve used to say it), one held on
                # the placeholder of a route nothing places (#370's not-found,
                # which the re-resolve used to refuse with, rolling the whole
                # profile back), or a model the prompt check refuses for another
                # reason (unfilled base URL, no credential source). The thinking
                # block after this one still runs on this path.
                notice = self._live_model_notice(harness.state.model)
                if notice is not None:
                    notices.append(notice)
            elif held is not None:
                # #367 verify round 1, B1 / round 2 — the reset above put back
                # launch inputs that land on a provider ``session_start``
                # registered; switching there would send the next prompt to it
                # though nobody picked a model. Hold, as every rebuild does (the
                # harness factory), and say why. ``/model``, or a profile whose
                # own ``model:`` / ``provider:`` applies, leaves it. Not completed
                # into ``parsed.provider``: the inputs stay the launch's.
                notices.append(held.notice)
                if not held.holds(harness.state.model):
                    # Verify round 5 — the launch hold's helper: a
                    # ``model_select`` handler's own ``set_model`` cannot leave
                    # the placeholder; a refusal rolls the switch back below.
                    # Verify round 6, B1 — and it holds the turns while it
                    # runs: no gate is up here (the session's was lifted long
                    # ago), and a handler that set the late model and
                    # triggered a turn sent it before the placeholder came back.
                    refused = await held.apply(harness)
                    if refused is not None:
                        raise refused
                    model_set = True
            elif self.parsed.model is not None or self.parsed.provider is not None:
                # #370 — when this re-resolves the LAUNCH inputs (``--none``, or
                # a profile whose ``model:``/``provider:`` did not apply, after
                # one whose route did — #412 keeps the live model otherwise), it is
                # told about ``--api-key`` as the launch and every rebuild's
                # factory are (ADR-0250 §2.1 steps 2 and 3b): without it, a
                # launch string no provider places (pi's not-found under
                # ``--api-key``, the key attached nowhere) re-resolved here by
                # guard 2 to OpenRouter on the user's own key, and the next
                # ``/new`` put the placeholder back. A profile's OWN route is a
                # new pick, not the string the key was typed with: it resolves
                # as before #370 (round 2, ADR-0250 §2.7) — guard 2 on the
                # user's own OpenRouter credential; the key stays where the
                # launch attached it.
                route = resolve_route(
                    self.parsed.model,
                    self.parsed.provider,
                    self.model_registry,
                    None,
                    typed_key=(
                        not profile_named_route
                        and getattr(self.parsed, "api_key", None) is not None
                    ),
                )
                if route.error is not None and self.model_registry is not None:
                    # ADR-0250 — pi's ambiguity / not-found refusal, before the
                    # switch, rolled back below like every other refusal. Not
                    # without a registry: the resolver then sees no credential
                    # and no user-defined provider, as ``/model``'s fallback.
                    raise ProfileError(route.error)
                model = enrich_copilot_base_url(route.model, self.model_registry)
                if model.provider:
                    # #152 — REFUSE a model this build has no adapter for, here,
                    # rather than letting the first turn raise ``No provider
                    # registered for api=…`` (an internal developer string, printed
                    # twice, after the switch has already been reported as a
                    # success). The gate is not new and this path is not special:
                    # ``/model`` (``tui/commands.py``), the model picker and the
                    # ``--agent`` CLI path all already refuse on exactly this
                    # predicate with exactly this message. This was the one caller
                    # that had not adopted it.
                    #
                    # Raising inside the try is deliberate: the rollback below
                    # restores BOTH halves, so a refused profile leaves ``parsed``
                    # and the live harness exactly as they were — which is what the
                    # red line the user sees then truthfully means.
                    if not is_runnable(model):
                        raise ProfileError(unsupported_message(model))
                    # Complete the pair on ``parsed`` so a later /new or /fork
                    # rebuild cannot resolve a model without its provider (#98)
                    # — BOTH halves, as the model this use set (#398). The
                    # rebuild reads them as ``--provider`` and ``--model``, and
                    # beside a provider the string as typed can name another
                    # id: ``openrouter/auto`` resolved without one is
                    # OpenRouter's ``auto``, with ``openrouter`` the catalogue's
                    # literal ``openrouter/auto``. pi, too, names a session's
                    # model by this pair (``restoreModelFromSession`` reads it
                    # back with ``getModel(provider, id)``).
                    if model.id:
                        self.parsed.model = _pinned_model_spelling(model)
                    self.parsed.provider = model.provider
                    await harness.set_model(model)
                    model_set = True
            else:
                # Review round 3 — a use that drops a route with an EMPTY baseline
                # (no ``--model``/``--provider`` and no settings default, so
                # neither ``parsed.model`` nor ``parsed.provider``): there is
                # nothing to re-resolve, and the dropped profile's model stays
                # live, as before #412. It is kept as the keep branch keeps one,
                # so it gets the same note when its prompts will be refused
                # (53ecb70b never asked here: a profile's model with no credential
                # kept silently while the keep branch's twin said why).
                notice = self._live_model_notice(harness.state.model)
                if notice is not None:
                    notices.append(notice)

            if self.parsed.thinking:
                # #433 review round 2 — the level is FITTED to the model first, as
                # the launch fits ``defaultThinkingLevel`` (``cli/entry.py``
                # ``_seed_default_thinking_level``, mirrored here: clamp only on a
                # settled model). A membership test alone dropped ``thinking:
                # xhigh`` on a row that now names ``max`` and no ``xhigh`` (Opus
                # 4.6, DeepSeek V4, GLM 5.2; ADR-0266), so the request kept the
                # previous level; clamped, it lands on ``max`` and sends what it
                # sent before #433, and ``thinking: max`` on a row without ``max``
                # runs at the row's top level. A held launch's placeholder (not
                # settled) supports only ``off`` and says nothing about the model
                # the run ends on: there a level it does not list is still
                # skipped, and a harness with no model has nothing to fit to.
                from aelix_ai.models import clamp_thinking_level

                from aelix_coding_agent.cli.entry import _settled_model

                current_model = getattr(harness, "current_model", None)
                if current_model is not None:
                    level = self.parsed.thinking
                    if _settled_model(current_model):
                        level = clamp_thinking_level(current_model, level) or level
                    if level in get_supported_thinking_levels(current_model):
                        await harness.set_thinking_level(level)
        except BaseException:
            # ROLL BACK BOTH HALVES, then re-raise for the caller to report.
            # Leaving the durable half applied is the dangerous direction: the
            # handler commits the error in red and the user reasonably concludes
            # nothing happened, while ``parsed`` now carries an identity the
            # kernel already rejected once — and the next ``/new`` tears the live
            # harness down BEFORE it discovers that.
            #
            # The live half is restored by writing ``AgentState`` directly rather
            # than through the setters: these are values the harness already held,
            # so they need no re-validation, and ``active_tool_names=None`` ("no
            # filter") has no setter that can express it. Undoing a ``set_model``
            # / ``set_thinking_level`` this way also avoids re-emitting their
            # hooks, which already fired for the value being reverted.
            #
            # #249 GAP, closed in the TUI by #559 review round 2: the context
            # meter listens on ``model_select`` (``tui/shell.py``
            # ``_model_select_hook``), so the forward ``set_model`` above moved
            # the footer's denominator to the model this rollback is abandoning,
            # and no event tells it to move back. Left as-is here rather than
            # re-emitting, because re-emitting is the thing this block was
            # written not to do (ADR-0116 #249 amendment). Instead the TUI's
            # ``/agents use`` handler repaints after a failed use
            # (``tui/commands.py``, its ``finally``) through the helper that
            # re-derives the meter from the model in force
            # (``_repaint_footer_for_model_in_force``) and recomposes the
            # footer's ``✱`` id and ``🧠`` level (#534), so all three read the
            # values restored here. Another host that caches a meter must do
            # the same, since nothing re-emits (ADR-0116, the #559
            # amendment's review round 2).
            self.parsed.__dict__.update(durable_snapshot)
            set_settings_seed_active(previous_seed_active)
            self.active = previous_active
            self.skills_holder["result"] = previous_skills
            harness.state.system_prompt = live_prompt
            harness.state.active_tool_names = live_active_tools
            harness.state.model = live_model
            harness.state.thinking_level = live_thinking
            if self.late_route is not None:
                self.late_route.explicit = live_explicit
            with contextlib.suppress(Exception):
                harness.set_skills(effective_skills(self.skills_holder))
            raise

        # Review round 3 (#412) — ``--none`` restores the baseline's prompt,
        # tools and skills, but the model only where a dropped route was
        # re-resolved or a hold applied; everywhere else the live model stays (a
        # ``/model`` pick over a typed ``--model`` among them), and the line
        # says so rather than claim the baseline identity whole.
        if self.active is not None:
            status = f"Agent profile: {self.active.name} ({self.active.scope})"
        elif model_set:
            status = "Agent profile cleared (baseline CLI identity restored)."
        else:
            status = (
                "Agent profile cleared (baseline CLI identity restored; the model is unchanged)."
            )
        if notices:
            return "\n".join([status, *(f"Note: {n}" for n in notices)])
        return status


def _pinned_model_spelling(model: Any) -> str:
    """The ``--model`` string meant to name exactly ``model.id`` under ``--provider model.provider``.

    #398 — :meth:`AgentProfileService.use` writes the pair it put on the session
    back onto ``parsed``, so that a later rebuild that resolves ``parsed``
    (``/new``; ``/fork``, ``/resume`` and ``/reload`` where they do not restore
    the session's own record) lands on the same model. It hands that pair to
    ``resolve_route``'s explicit-provider branch (its step E), which looks the
    string up as an id of the provider first, then (not found) strips one
    ``<provider>/`` — case-insensitively, pi's ``resolveCliModel`` strip — and
    then builds a custom id. So the id itself is the spelling, except for an
    id that begins with its own provider's prefix: a custom ``mygw/x`` under
    ``mygw`` would lose that prefix to the strip, so it is spelled
    ``mygw/mygw/x``, which strips back to it. A catalogued id of that shape
    gets the prefixed spelling too (``openrouter/openrouter/auto`` is no id, so
    it strips back to ``openrouter/auto``), and the rule takes no registry.

    That costs one corner (ADR-0196's #398 Known corners, review round 2): a
    provider that also lists the prefixed spelling as an id of its own —
    ``mygw/x`` beside ``mygw/mygw/x`` in a models.json — has step E find that
    other id before any strip, so ``/new`` moves to ``mygw/mygw/x``, as it did
    before #398. A lookup (the bare id when step E finds it as itself, else this
    form) would close it. The provider half has a corner of its own: step E
    reads the provider through ``_named_provider``, which takes a user-defined
    provider equal to it up to case first, so a use that landed on the
    catalogue's ``openai`` beside a models.json ``OpenAI`` is rebuilt on
    ``OpenAI`` — no string written here names the catalogue's one then.

    The string as typed is NOT the spelling. Without ``--provider``, the launch
    reads ``openrouter/auto`` as the prefix ``openrouter`` and the id ``auto``;
    beside ``--provider openrouter`` the same string is the catalogue's literal
    id ``openrouter/auto`` (ADR-0250 §2). Writing the typed string back with the
    provider therefore moved ``/new`` from ``auto`` to ``openrouter/auto`` — and
    from a models.json ``mygw`` model ``gpt`` to that provider's ``mygw/gpt``.
    """

    provider = str(getattr(model, "provider", "") or "")
    model_id = str(getattr(model, "id", "") or "")
    if provider and model_id.lower().startswith(f"{provider.lower()}/"):
        return f"{provider}/{model_id}"
    return model_id


__all__ = ["AgentProfileService"]
