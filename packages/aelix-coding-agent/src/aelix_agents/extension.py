"""``AgentsExtension`` — the bundled delegation extension — ADR-0197 §(a)/§(i).

This is the only object product-core ever names from ``aelix_agents``, and it is
named at exactly one site: a function-level import inside
``cli/entry.py::_async_main`` (the import-direction rule of §(a), enforced by
``tests/cli/test_p2_import_direction.py``). Everything it does, it does through
the public extension API.

FOUR REGISTRATIONS, and the ORDER of the whole extension matters more than the
order of these. ``cli/entry.py`` APPENDS this extension after ``Guardrail`` and
``Permission`` (``entry.py:860-873`` documents Guardrail-first as a security
invariant — DO NOT REORDER), so under the kernel's first-block-wins reduction
our ``tool_call`` handler runs LAST: a guardrail hard-deny and a permission
denial both win over us, and neither can be softened by anything here.

1. ``bind_subagents`` — installs :class:`~aelix_agents.runtime._SubagentRuntimeImpl`
   in product-core's seam, which is what makes ``/agents run`` work. The seam
   refuses the bind at ``MAX_SUBAGENT_DEPTH`` (finding I4), so a delegated child
   physically cannot hold a runtime even if this extension is somehow loaded.
2. ``register_tool`` — the ``agent`` tool, re-stamped with a fresh roster on
   every ``before_agent_start``.
3. ``on("tool_call")`` — THE CONSENT GATE (§(i)). See :mod:`aelix_agents.tool`
   for why it cannot live in ``execute()``.
4. ``add_cleanup`` — kills every live child and releases the seam on harness
   dispose. ADR-0197 forbids any task outliving session teardown, and a reaper
   is a detached task (finding B1), so ``stop_all`` JOINS them.

WHY THE HOST IS ALL CALLABLES. Every value the runtime needs is time-varying:
``ctx.has_ui`` flips during ``bootstrap()`` and again on TUI exit (OC-7), the
parent's posture is one shift+tab away from changing, the active tool set is
rebuilt by every ``register_tool``, and the model registry is rebound on
``/reload``. Nothing is captured; everything is read at the moment it is used.

WHY ``self._ctx`` EXISTS. ``ExtensionContext`` is only handed to HOOKS, and
``execute()`` needs the cwd, the tool grant and the trust decision that live on
it. The context of the most recent hook is therefore kept — the OBJECT, never
any value read off it, so ``has_ui`` and friends stay live. The ``tool_call``
hook that approves a spawn always runs immediately before the ``execute()``
that performs it, so the context an ``agent`` call uses is always the one from
its own call.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_agent_core.harness.hooks import ToolCallResult
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolResult
from aelix_coding_agent.agents.discovery import discover_profiles
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.extensions.api import ExtensionError
from aelix_coding_agent.subagent_contract import (
    MAX_SUBAGENT_DEPTH,
    subagent_depth,
)

from aelix_agents.consent import (
    SpawnGrant,
    consent_is_required,
    request_spawn_consent,
)
from aelix_agents.posture import child_permission_mode
from aelix_agents.print_channel import AGENT_TOOL_NAME, PrintChannel, resolve_child_cwd
from aelix_agents.progress import SubagentProgressBridge
from aelix_agents.prompt_file import sweep_stale_prompt_dirs
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_agents.tool import (
    AgentCallError,
    PendingSpawn,
    build_description,
    build_roster,
    create_agent_tool,
    format_partial,
    parse_agent_call,
    render_subagent_result,
    with_description,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.hooks import (
        BeforeAgentStartHookEvent,
        SessionShutdownHookEvent,
        ToolCallHookEvent,
    )
    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture
    from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext
    from aelix_coding_agent.subagent_contract import SubagentProgress

_DEPTH_REFUSAL = (
    "Delegation is not available inside a delegated agent (max depth "
    f"{MAX_SUBAGENT_DEPTH}). Do the work yourself and report back to the "
    "parent agent."
)

_DECLINED = (
    "The user declined this delegation. Do not retry it — either do the work "
    "yourself or ask the user what they would prefer."
)

_NOT_APPROVED = (
    "Delegation was not approved. The spawn-time consent gate is the only "
    "place a grant is issued, and this call reached execution without one."
)

_IDENTITY_MISMATCH = (
    "Delegation refused: the approved profile and the resolved profile do not "
    "match. Nothing was started."
)

_UNAVAILABLE = (
    "Delegation is unavailable in this session. Report the work you would have "
    "delegated instead."
)


@dataclass
class AgentsExtension:
    """The bundled ``aelix-agents`` extension. One instance per process.

    Constructed ONCE in ``cli/entry.py`` and threaded by held reference into
    every harness rebuild — the mirror of ``permission_ext`` — so the child
    registry and ``stop_all`` both survive ``/new`` / ``/fork`` / ``/resume``.

    Every field is optional so ``AgentsExtension()`` is valid (that is the
    literal call site in ``entry.py``), and every default is the conservative
    one: no posture means :attr:`PermissionMode.DEFAULT`, whose clamp is
    ``plan`` — an unwired host gets READ-ONLY children.
    """

    posture: PermissionPosture | None = None
    """The SAME :class:`PermissionPosture` instance ``cli/entry.py`` threads
    into :class:`PermissionExtension`. It must be shared, not copied: shift+tab
    mutates that one object, and the clamp has to see the change (ADR-0157)."""

    agent_dir: str | None = None
    """The agent ROOT (``~/.aelix/agent``), not the agents dir — the same
    convention ``agents/discovery.user_agents_dir`` uses. ``None`` lets
    discovery resolve its own default."""

    cwd: str | None = None
    """PRE-HOOK fallback for the project root. A live ``ExtensionContext``
    always wins; this only answers before the first hook has run, which is
    precisely when ``/agents run`` can be the very first thing a user types.
    ``None`` falls back to :func:`os.getcwd`."""

    project_trusted: bool | None = None
    """PRE-HOOK fallback for the Project-Trust decision, same precedence rule.

    It gates the ``.aelix/agents`` DISCOVERY tier exactly as it gates
    ``.aelix/skills`` and ``.aelix/extensions``. ``None`` means "no evidence" and
    resolves to ``False``, matching ``project_trust.py``'s own step-6
    deny-by-default — a wrong ``True`` here would make project identities merely
    VISIBLE, not usable (``resolve_profile`` still refuses them for the model
    door), but the conservative direction is still the right default."""

    channel: PrintChannel = field(default_factory=PrintChannel)

    _pending: dict[str, PendingSpawn] = field(default_factory=dict, init=False)
    """``tool_call_id`` → the approved spawn. Popped with a ``None`` default in
    :meth:`_execute`, which is the anti-bypass invariant: a call that skipped
    the hook finds nothing and is refused. The grant is deliberately NOT
    smuggled through ``event.args`` even though ``harness/core.py:3723-3725``
    permits mutation — that would put an unvalidated key in the transcript."""

    _api: Any | None = field(default=None, init=False)
    _ctx: Any | None = field(default=None, init=False)
    _runtime: Any | None = field(default=None, init=False)
    _progress: SubagentProgressBridge | None = field(default=None, init=False)
    _tool: Any | None = field(default=None, init=False)
    _degraded: bool = field(default=False, init=False)
    """True when the seam refused our bind — at depth, or because another
    runtime already owns it. The extension then registers NOTHING: a tool that
    cannot spawn is worse than no tool, because the model would keep calling
    it."""

    # ── setup ─────────────────────────────────────────────────────

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Extension factory entry point (``ExtensionFactory``)."""

        self._api = aelix
        self._progress = SubagentProgressBridge(aelix)
        self._runtime = _SubagentRuntimeImpl(host=self._host(), channel=self.channel)

        runtime = aelix.runtime
        try:
            # ``replace=False``, EXPLICITLY (P2 review, MEDIUM #13). This read
            # ``replace=runtime.subagents is self._runtime``, which is provably
            # constant False: ``self._runtime`` was assigned a FRESH
            # ``_SubagentRuntimeImpl`` three lines above, so it cannot already
            # occupy the slot. Written as a live expression it invited the
            # reading that this extension can take a seam over; it never could,
            # and it must not — an occupied slot means a second orchestration
            # engine is running, and displacing it silently is exactly the
            # split-brain ``bind_subagents``'s double-bind refusal exists to
            # prevent. Being refused and standing down is the correct outcome.
            runtime.bind_subagents(self._runtime, replace=False)
        except ExtensionError:
            # Depth (finding I4) or a foreign runtime already holding the seam.
            # Both are legitimate states, and in both the correct behaviour is
            # to stand down completely rather than to publish a broken tool.
            self._degraded = True
            self._runtime = None
            return

        self._degraded = False
        # Reclaim prompt directories orphaned by a PREVIOUS aelix that died hard
        # (MEDIUM #5). Done here, after the bind succeeded, because that is the
        # one place we know this process is a delegation PARENT: a child never
        # gets here (the seam refuses the bind at depth), so a child can never
        # sweep the directory its own parent is currently using. Suppressed
        # wholesale — a temp directory we could not tidy is never a reason to
        # fail a startup.
        with contextlib.suppress(Exception):
            sweep_stale_prompt_dirs()
        self._tool = create_agent_tool(
            description=build_description(""), execute=self._execute
        )
        aelix.register_tool(self._tool)
        aelix.on("tool_call", self._on_tool_call)
        aelix.on("before_agent_start", self._on_before_agent_start)
        aelix.on("session_shutdown", self._on_session_shutdown)

        # The cleanup CLOSES OVER this build's api and runtime rather than
        # reading ``self`` later. One instance is threaded through every harness
        # rebuild, so ``self._runtime`` may already point at the NEXT harness's
        # runtime by the time an older bus disposes — and an identity-scoped
        # unbind (finding B7) is only identity-scoped if it names the right
        # identity. ``HookBus`` dedups cleanups by ``id``, so a fresh closure per
        # build is exactly right.
        own_runtime = self._runtime

        def _cleanup() -> Any:
            return self._release(aelix, own_runtime)

        aelix.add_cleanup(_cleanup)

    @property
    def runtime(self) -> Any | None:
        """The bound :class:`SubagentRuntime`, or ``None`` when degraded."""

        return self._runtime

    @property
    def degraded(self) -> bool:
        """True when the seam refused our bind and nothing was registered."""

        return self._degraded

    def _still_holds_the_seam(self) -> bool:
        """Is OUR runtime the one product-core would call? (MEDIUM #13.)

        Read LIVE, per tool call, because a takeover is a runtime event: the
        seam is a single slot, ``bind_subagents(..., replace=True)`` succeeds
        silently, and the displaced runtime is neither returned nor notified.
        This is the only way an extension can notice.

        Errs toward TRUE on any failure: a context we cannot interrogate must
        not silently disable a delegation the user asked for — the checks that
        follow (consent, depth, identity) are the ones that must fail closed,
        and they all still run.
        """

        api, mine = self._api, self._runtime
        if api is None or mine is None:
            return False
        try:
            return api.runtime.subagents is mine
        except Exception:  # noqa: BLE001 — unreadable seam is not evidence
            return True

    # ── host wiring (every value read LIVE) ───────────────────────

    def _host(self) -> SubagentHost:
        return SubagentHost(
            cwd=self._host_cwd,
            posture=self._host_posture,
            active_tools=self._host_active_tools,
            consent_context=lambda: self._ctx,
            project_trusted=self._host_project_trusted,
            agent_dir=lambda: self.agent_dir,
            model_registry=self._host_model_registry,
            on_progress=self._publish_progress,
        )

    def _host_cwd(self) -> str:
        ctx = self._ctx
        if ctx is not None:
            try:
                return str(ctx.cwd)
            except Exception:  # noqa: BLE001 — a stale ctx must not brick a spawn
                pass
        return self.cwd or os.getcwd()

    def _host_posture(self) -> PermissionMode:
        if self.posture is None:
            # Not wired → DEFAULT → the clamp is ``plan``. Read-only children
            # are the safe failure, and they are a visible one: the child says
            # so in its own output.
            return PermissionMode.DEFAULT
        return self.posture.get()

    def _host_active_tools(self) -> list[str] | None:
        ctx = self._ctx
        if ctx is None:
            return None
        try:
            return list(ctx.get_active_tools())
        except Exception:  # noqa: BLE001
            return None

    def _host_project_trusted(self) -> bool:
        ctx = self._ctx
        if ctx is None:
            return bool(self.project_trusted)
        try:
            return bool(ctx.is_project_trusted())
        except Exception:  # noqa: BLE001 — no evidence of trust means untrusted
            return False

    def _host_model_registry(self) -> Any | None:
        api = self._api
        if api is None:
            return None
        try:
            return api.runtime.model_registry
        except Exception:  # noqa: BLE001
            return None

    def _publish_progress(self, progress: SubagentProgress) -> None:
        bridge = self._progress
        if bridge is not None:
            bridge(progress)

    # ── the roster ────────────────────────────────────────────────

    def _roster(self) -> str:
        """The profiles this parent may delegate to, as prompt text.

        Discovery is re-run per prompt rather than cached: a user who writes a
        new profile file mid-session expects the next turn to see it, and
        ``discover_profiles`` is a non-recursive glob of at most two
        directories.

        PROJECT-SCOPED PROFILES ARE LISTED BUT NOT REACHABLE from this tool —
        ``resolve_profile(..., allow_project=False)`` refuses them (finding B5).
        Listing them with their scope is deliberate: the model sees why its call
        was refused and can tell the user to run ``/agents run`` themselves,
        instead of retrying a name that will never work.
        """

        try:
            discovered = discover_profiles(
                self._host_cwd(),
                project_trusted=self._host_project_trusted(),
                agent_dir=self.agent_dir,
            )
        except Exception:  # noqa: BLE001 — a broken profile dir must not kill a turn
            return ""
        return build_roster(sorted(discovered.profiles, key=lambda p: p.name))

    # ── hooks ─────────────────────────────────────────────────────

    async def _on_before_agent_start(
        self, event: BeforeAgentStartHookEvent, ctx: ExtensionContext
    ) -> None:
        """Refresh the roster, reset the delegation budget, drop stale grants.

        ``before_agent_start`` (``harness/core.py:1242``) runs BEFORE the
        per-turn ``AgentContext`` is assembled (``:4117-4133``), so a
        description replaced here is the one this prompt's model sees. A
        ``turn_start`` handler would be one turn too late.

        THE BUDGET RESET BELONGS HERE, next to ``self._pending.clear()`` and for
        the same reason (P2 review, MEDIUM #2): one prompt, one set of
        approvals, one delegation ceiling. ``before_agent_start`` fires once per
        USER prompt, not once per model turn, so an injected instruction cannot
        refresh its own budget by taking another turn.

        LANDMINE (documented rather than discovered): ``register_tool`` →
        ``refresh_tools`` → ``_refresh_extension_tools`` (``core.py:847-906``)
        MATERIALISES ``active_tool_names`` from its ``None`` sentinel into a
        concrete list. Everything downstream that reads ``get_active_tools()``
        — including the child's tool narrowing — therefore sees a real list from
        the first prompt onward, never ``None``.
        """

        self._ctx = ctx
        self._pending.clear()
        runtime = self._runtime
        if runtime is not None:
            # ``suppress`` because the seam is structural: a P4 host that swapped
            # in its own runtime need not implement a budget, and a missing
            # method must not cost the parent its prompt.
            with contextlib.suppress(Exception):
                runtime.reset_delegation_budget()
        api, tool = self._api, self._tool
        if api is None or tool is None:
            return
        description = build_description(self._roster())
        if description == tool.description:
            return
        self._tool = with_description(tool, description)
        with contextlib.suppress(Exception):
            api.register_tool(self._tool)

    async def _on_tool_call(
        self, event: ToolCallHookEvent, ctx: ExtensionContext
    ) -> ToolCallResult | None:
        """THE CONSENT GATE. Runs sequentially; ``execute()`` does not.

        Everything that can refuse a delegation refuses HERE, where the kernel
        renders the refusal as a model-readable blocked tool call
        (``loop.py:518-531``) and where no process has been created yet. On
        success the approval is filed under ``event.tool_call_id`` and the hook
        returns ``None`` — observationally identical to not having run.
        """

        # Kept for the host on EVERY tool call, not just ours: it is the only
        # channel through which ``execute()`` and ``/agents run`` ever see a cwd,
        # a tool grant or a trust decision.
        self._ctx = ctx
        if event.tool_name != AGENT_TOOL_NAME:
            return None
        runtime = self._runtime
        if runtime is None:
            return ToolCallResult(block=True, reason=_UNAVAILABLE)
        if not self._still_holds_the_seam():
            # A second engine took the seam over with ``replace=True``
            # (P2 review, MEDIUM #13). ``bind_subagents`` returns the displaced
            # runtime to nobody and calls no ``on_replaced``, so without this
            # check our ``agent`` tool would keep spawning into the DISPLACED
            # registry while ``/agents run`` drove the new one — two live child
            # registries, which is precisely the split-brain the double-bind
            # refusal exists to prevent, arriving through the one spelling that
            # is allowed. We stand down instead: the seam's current holder is
            # the only runtime that may start children.
            return ToolCallResult(block=True, reason=_UNAVAILABLE)
        if subagent_depth() >= MAX_SUBAGENT_DEPTH:
            return ToolCallResult(block=True, reason=_DEPTH_REFUSAL)

        try:
            call = parse_agent_call(event.args)
        except AgentCallError as exc:
            return ToolCallResult(block=True, reason=str(exc))

        try:
            # ALWAYS ``allow_project=False`` (finding B5), and there is no code
            # path that may pass True here. The MODEL chose this string, so repo
            # content — a README, a comment, a fixture — must not be able to
            # select a project-authored identity with a replaced system prompt.
            # ``/agents run`` is the door where a human may consent to one.
            resolved = runtime.resolve_profile(call.profile, allow_project=False)
        except Exception as exc:  # noqa: BLE001 — every failure is a refusal
            # ``ProfileError``'s own message already lists the available names
            # (``agents/discovery._unknown_name_message``), so the roster
            # reaches the model without being rebuilt here.
            return ToolCallResult(block=True, reason=f"Delegation refused: {exc}")

        try:
            child_cwd = resolve_child_cwd(call.cwd, self._host_cwd())
        except Exception as exc:  # noqa: BLE001
            return ToolCallResult(block=True, reason=f"Delegation refused: {exc}")

        grant = await self._grant_for(ctx, resolved, call.task, cwd=child_cwd)
        if not grant.consented:
            return ToolCallResult(block=True, reason=_DECLINED)

        self._pending[event.tool_call_id] = PendingSpawn(
            grant=grant, resolved=resolved, call=call, cwd=child_cwd
        )
        return None

    async def _grant_for(
        self, ctx: Any, resolved: Any, task: str, *, cwd: str
    ) -> SpawnGrant:
        """The MODEL door's pre-filter — prompting only when there is something to ask.

        ONE CONDITION, AND IT IS NOT SPELLED HERE.
        :func:`~aelix_agents.consent.consent_is_required` is the single source of
        truth for "is there anything to ask a human about this spawn", and both
        this door and ``request_spawn_consent``'s own early-out call it. They
        used to be two different expressions — this one read
        ``not grants_write_authority(want) and approval_mode != "ask"`` while the
        dialog read ``grants_write_authority(clamped) or _may_widen(...)``, so a
        read-only user-scoped delegation prompted on one door and not the other.
        Two hand-maintained spellings of a consent rule is how one door drifts
        open; there is now nothing to keep in agreement.

        What the shared predicate answers (§(i), owner amendment 2026-07-27):

        * the clamp GRANTS WRITE AUTHORITY — the cells where the parent is
          already loose and every model-chosen delegation would otherwise
          inherit that authority silently (finding OC-1). Unchanged by the
          amendment;
        * or the profile itself DECLARED it needs write authority
          (``approval_mode: auto`` / ``ask``), so the bounded widening option is
          real. ``ask`` is its author asking for a human decision REGARDLESS of
          the clamp, which is what retires the "validated, not read" deferral at
          ``agents/profile.py:218-220``; ``auto`` under a tight parent is
          finding B4's case, where the profile asked for writes and only a human
          may grant them.

        Everything else is a READ-ONLY child from a profile that asked for
        nothing more — the out-of-the-box outcome for a ``default`` parent, and
        therefore the common case. Asking a human to approve a reader would train
        them to click through the dialog that matters.
        ``MAX_DELEGATIONS_PER_PROMPT`` is the bound on how often this branch may
        be taken silently.

        "READ-ONLY" IS ASKED OF THE LADDER, NOT SPELLED ``is PLAN``.
        :func:`~aelix_agents.posture.grants_write_authority` is derived from
        ``builtin/permission.py``'s own branch order for a child
        (``headless_default="block"``, no UI); the previous ``want is
        PermissionMode.PLAN`` agreed with it on every value the clamp can
        currently return only because the clamp tightens a clamped ``DEFAULT``
        to ``PLAN``, which is a different decision that could be relaxed
        independently.

        The condition cannot produce a REFUSAL without a UI: with no live UI
        ``request_spawn_consent`` returns the clamp consented and un-widened
        without prompting, so ``-p`` / ``--mode json`` / RPC delegation keeps
        working exactly as §(e) describes and ``ask`` silently downgrades to
        ``plan`` (OC-8).

        ``has_ui`` is read LIVE here and again inside ``request_spawn_consent``
        (OC-7). The duplication is deliberate: this call decides only WHETHER
        there is a question to ask, and the authority decision stays in one
        place, in ``consent.py``.
        """

        parent = self._host_posture()
        has_ui = bool(getattr(ctx, "has_ui", False))
        want = child_permission_mode(
            resolved.profile.approval_mode,
            parent,
            resolved.scope,
            has_ui=has_ui,
        )
        if not consent_is_required(resolved, want, has_ui=has_ui):
            return SpawnGrant(
                profile=resolved.name,
                source_path=resolved.source_path,
                scope=resolved.scope,
                mode=want,
                widened=False,
                consented=True,
            )
        # No memo argument, and there is no memo to pass: P2 asks EVERY time
        # (ADR-0197:613-616). The model-driven door is the one that must never
        # hold a standing grant — it is the door where the model, not the human,
        # chose the profile, the task and the directory.
        return await request_spawn_consent(ctx, resolved, task, parent, cwd=cwd)

    async def _on_session_shutdown(
        self, event: SessionShutdownHookEvent, ctx: ExtensionContext
    ) -> None:
        """Kill the children, drop any un-spent grant, clear the statusline.

        Fires on ``/new`` / ``/resume`` / ``/fork`` as well as ``quit``
        (``harness/core.py`` ``_teardown_current`` / ``dispose``). Only
        :attr:`_pending` — grants taken in the hook for a tool call whose
        ``execute()`` never ran — needs clearing; nothing else survives a spawn,
        because P2 takes one grant per spawn and keeps no session memo
        (ADR-0197:613-616).
        """

        await self._shutdown()

    async def _release(self, api: Any, runtime: Any) -> None:
        """Harness dispose (LIFO). Releases the seam as well as the children."""

        await self._shutdown(runtime)
        if api is not None and runtime is not None:
            with contextlib.suppress(Exception):
                # IDENTITY-SCOPED (finding B7). ``bind_subagents(None)`` would
                # null the slot for whatever runtime happens to hold it, which
                # on a rebuild is somebody else's.
                api.runtime.unbind_subagents(runtime)

    async def _shutdown(self, runtime: Any | None = None) -> None:
        """Kill this runtime's children and forget every session-scoped decision.

        ``runtime`` is explicit on the teardown path so an older harness's
        cleanup stops ITS OWN children rather than whichever runtime happens to
        be current — see the closure in :meth:`__call__`.
        """

        self._pending.clear()
        target = runtime if runtime is not None else self._runtime
        if target is not None:
            with contextlib.suppress(Exception):
                await target.stop_all()
        bridge = self._progress
        if bridge is not None:
            bridge.clear()

    # ── the tool body ─────────────────────────────────────────────

    async def _execute(
        self, args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        """Spawn the approved child. FAILS CLOSED on a missing grant.

        The ``pop`` default is ``None`` and that is the whole anti-bypass
        invariant: a call that reached execution without going through the hook
        finds nothing here and is refused rather than spawned. Nothing in
        ``args`` is re-read — the human approved the profile, the task and the
        directory that were on screen, and those are what
        :class:`PendingSpawn` carries.

        The ONE thing that IS re-read is the identity, deliberately: the profile
        name is re-resolved and its source path compared against the one the
        grant was issued for, so a profile file swapped between the hook and here
        is refused rather than run (MEDIUM #10).
        """

        pending = self._pending.pop(ctx.tool_call_id, None)
        if pending is None or not pending.grant.consented:
            return _error(_NOT_APPROVED)
        if subagent_depth() >= MAX_SUBAGENT_DEPTH:
            # Third and last layer of the depth guard (§(d)): build-time
            # suppression, the seam's own refusal, and this. Returns, never
            # raises — a raise would abort the parent's turn.
            return _error(_DEPTH_REFUSAL)
        runtime = self._runtime
        if runtime is None:
            return _error(_UNAVAILABLE)
        # ANTI-SUBSTITUTION, and it is a live gate now (P2 review, MEDIUM #10).
        # It used to read ``pending.grant.source_path !=
        # pending.resolved.source_path``, which is DEAD: ``PendingSpawn`` is
        # built at one site from the same ``resolved`` the grant was taken
        # against, and every ``SpawnGrant`` constructor sets
        # ``source_path=resolved.source_path``, so the two fields were equal by
        # construction on every path. It sat directly above the spawn and read
        # like a defence that was not there.
        #
        # Re-resolving BY NAME is what makes it real: the hook and this method
        # are separated by the kernel's parallel execute phase, and the profile
        # search path is a directory the model's own tools can write. If the
        # name now resolves to a DIFFERENT file — a project-scoped profile that
        # appeared and wins the collision (``agents/service.py:99-100``), a
        # user-scope file replaced by one somewhere else on the search path —
        # then what the human approved is not what would run.
        #
        # ``allow_project=False`` and a bare ``except`` are both deliberate: this
        # is the model-driven door (finding B5), and ANY failure to re-establish
        # the identity — the file was deleted, discovery raised, a project
        # profile now shadows the approved one — is a refusal, never a spawn.
        try:
            recheck = runtime.resolve_profile(
                pending.call.profile, allow_project=False
            )
            still_the_same = recheck.source_path == pending.grant.source_path
        except Exception:  # noqa: BLE001 — unresolvable identity is a refusal
            still_the_same = False
        if not still_the_same:
            return _error(_IDENTITY_MISMATCH)

        def _on_event(progress: SubagentProgress) -> None:
            # The parent's own tool card. The event bus and the statusline are
            # driven separately by ``host.on_progress``; both ride the same
            # reduce step and neither replaces the other.
            if ctx.on_partial is None:
                return
            with contextlib.suppress(Exception):
                ctx.on_partial(format_partial(_partial_text(progress)))

        result = await runtime.spawn_granted(
            pending.grant,
            pending.resolved,
            pending.call.task,
            cwd=pending.cwd,
            timeout_ms=pending.call.timeout_ms,
            on_event=_on_event,
        )
        return render_subagent_result(result)


def _partial_text(progress: SubagentProgress) -> str:
    tool = f" · {progress.current_tool}" if progress.current_tool else ""
    return (
        f"agent {progress.profile} [{progress.state}]{tool} · "
        f"{progress.elapsed_ms / 1000:.0f}s"
    )


def _error(message: str) -> ToolResult:
    """A refusal the MODEL reads. Returned, never raised — a raise inside
    ``execute`` aborts the parent's whole turn."""

    return ToolResult(content=[TextContent(text=message)], is_error=True)


__all__ = [
    "AgentsExtension",
]
