"""Live agent-profile service — ``/agents list|show|use`` (ADR-0196).

AELIX-ORIGINAL. The three read/write operations the TUI needs, held over the
ONE mutable ``Args`` the harness factory closes over (``cli/entry.py:2610-2771``)
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
  (``core.py:3676-3681``) already reads ``_state`` as its fallback, but a
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

from aelix_agent_core.harness.skills import load_skills
from aelix_ai.models import get_supported_thinking_levels

from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.runtime_bootstrap import (
    enrich_copilot_base_url,
    resolve_model,
)
from aelix_coding_agent.core.runnable_models import is_runnable, unsupported_message
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
        (``cli/entry.py:2610-2614``). :meth:`use` mutates it IN PLACE — that is
        what makes a switched identity survive ``/new``, ``/fork`` and
        ``/resume``.
    :param baseline: a pristine :func:`copy.deepcopy` of ``parsed`` taken before
        any mutator ran (``cli/entry.py:1873``). Every :meth:`use` resets to it
        first, so a second switch overlays the ORIGINAL CLI intent rather than
        the previous profile.
    :param skills_holder: the ``{"result": LoadSkillsResult}`` box the factory
        reads on every (re)build (``cli/entry.py:2589`` / ``:2764``).
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
           ``state.tools`` and raises (``core.py:3691-3699``) for a typo, for an
           MCP tool whose server never connected, or for an extension tool under
           ``inherit_extensions: false`` — none of which profile PARSING can
           check. Without the rollback that raise left ``parsed.tools`` poisoned
           and the prompt already swapped, so the failure was reported in red
           while the next ``/new`` — which disposes the live harness BEFORE
           rebuilding (``agent_session_runtime.py:485``) — died in
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

        ``/model`` and ``/thinking`` chosen live are NOT reverted when the new
        profile is silent about them — that is the deliberate difference from
        the rejected rebuild option, which stomped both.
        """

        # Function-level import: ``cli/entry.py`` imports this package at module
        # scope (``entry.py:64-69``), so importing it back here at module scope
        # would close an import cycle. Reusing ITS helpers (rather than
        # re-deriving the same values) is the point — a live identity computed
        # from different inputs than a rebuilt one would silently diverge on the
        # first /new.
        from aelix_coding_agent.cli.entry import (
            _resolve_active_tools,
            _resolve_append_chunks,
            _resolve_skill_dirs,
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

        try:
            # === 2. Reset to the pristine CLI baseline ======================
            self.parsed.__dict__.update(copy.deepcopy(self.baseline).__dict__)

            # === 3. Overlay (durable half) ==================================
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
                self.active = profile

            # === 4. Apply to the live harness (immediate half) ==============
            #
            # Skills FIRST, because the prompt is built from them (#115). A
            # profile can add ``skills:`` paths or set ``inherit_skills: false``,
            # so the set has to be re-scanned before anything reads it — composing
            # first would install a catalog describing the PREVIOUS profile's
            # skills. Both halves of the write are required: the holder for later
            # rebuilds, ``set_skills`` for this session.
            self.skills_holder["result"] = load_skills(
                _resolve_skill_dirs(self.parsed, self.cwd, self.project_trusted)
            )
            harness.set_skills(self.skills_holder["result"].skills)

            # Tools: the same three-way ``_resolve_active_tools`` result the
            # factory would compute, plus the same post-registration
            # ``--no-builtin-tools`` filter — written twice only because one runs
            # pre-build and one post-build; the emission rules are identical.
            #
            # THIS is the call that raises on a profile naming a tool the running
            # process does not have (``core.py:3691-3699``), which is why
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
                    tools=_visible_tools(
                        harness.state.tools, harness.state.active_tool_names
                    ),
                ),
                _resolve_append_chunks(
                    self.parsed,
                    self.cwd,
                    skills=self.skills_holder["result"].skills,
                ),
            )

            if self.parsed.model is not None or self.parsed.provider is not None:
                model = enrich_copilot_base_url(
                    resolve_model(
                        self.parsed.model,
                        self.parsed.provider,
                        self.model_registry,
                        None,
                    ),
                    self.model_registry,
                )
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
                    # rebuild cannot resolve a model without its provider (#98).
                    self.parsed.provider = model.provider
                    await harness.set_model(model)

            if self.parsed.thinking:
                # Mirrors the TUI's own startup thinking seed in ``tui/shell.py``:
                # a level the bound model does not support is skipped rather than
                # forced, and a harness with no model yet has nothing to validate
                # against.
                current_model = getattr(harness, "current_model", None)
                if current_model is not None and self.parsed.thinking in (
                    get_supported_thinking_levels(current_model)
                ):
                    await harness.set_thinking_level(self.parsed.thinking)
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
            self.parsed.__dict__.update(durable_snapshot)
            self.active = previous_active
            self.skills_holder["result"] = previous_skills
            harness.state.system_prompt = live_prompt
            harness.state.active_tool_names = live_active_tools
            harness.state.model = live_model
            harness.state.thinking_level = live_thinking
            with contextlib.suppress(Exception):
                harness.set_skills(previous_skills.skills)
            raise

        status = (
            f"Agent profile: {self.active.name} ({self.active.scope})"
            if self.active is not None
            else "Agent profile cleared (baseline CLI identity restored)."
        )
        if notices:
            return "\n".join([status, *(f"Note: {n}" for n in notices)])
        return status


__all__ = ["AgentProfileService"]
