"""Product-core subagent-runtime CONTRACT (spec §6.1, ADR-0197).

INTERFACE ONLY. This module declares types, a Protocol, event channel names,
and a version range. It contains no behavior and must never grow any: the spawn
implementation lives in the bundled ``aelix_agents`` extension, which binds an
implementation onto ``_ExtensionRuntime`` via ``bind_subagents``.

Why the seam is drawn here rather than in the kernel or in the extension: the
3-band rule (ADR-0008, amended by ADR-0197). ``aelix-agent-core`` gets zero
bytes of delegation surface; product-core owns the CONTRACT so ``/agents run``
(``tui/commands.py``) and any future consumer can be typed against it without
importing an orchestration engine; every process-spawning line lives in
``aelix_agents``. ``tests/agents/test_p2_band_boundaries.py`` is the machine
gate for all three bands.

IMPORT RULE: nothing from ``aelix_coding_agent`` at runtime. The only
product-core name this module needs is :class:`~aelix_coding_agent.agents.profile.AgentProfile`,
and it is a ``TYPE_CHECKING``-only forward reference so that importing the
contract never drags in the profile/discovery/resolver chain. Pinned by
``test_contract_imports_nothing_from_product_core``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_coding_agent.agents.profile import AgentProfile


CONTRACT_VERSION = 1
"""Bumped only when an EXISTING field changes shape or a required parameter is
added. Adding a DEFAULTED dataclass field is additive and does NOT bump."""

MIN_SUPPORTED_CONTRACT_VERSION = 1
"""Oldest runtime this build will bind. ``bind_subagents`` accepts
``MIN_SUPPORTED_CONTRACT_VERSION <= v <= CONTRACT_VERSION``.

Rationale (P2 review, finding B9): an exact-equality gate is either dead code
(a runtime importing the live constant can never mismatch) or a hard break for
every third-party runtime the day P3 bumps the version. A range gives an
additive-compat window with a documented floor."""

MAX_SUBAGENT_DEPTH = 1
"""P2 ships single-level delegation only. A process at this depth does not get
``aelix-agents`` prepended (``cli/entry.py``), cannot BIND a runtime
(``_ExtensionRuntime.bind_subagents``), and its ``agent`` tool refuses.

NOTE: spec §8 lists the depth mechanism under Phase 3. It is pulled into P2 as
a GUARD, not a feature — hardcoded, not configurable, no ``max_depth`` profile
field. Without it P2 is a fork bomb."""

DEPTH_ENV_VAR = "AELIX_SUBAGENT_DEPTH"

# Event-bus channel names (``extensions/api.py`` ``EventBus.emit``). Declared
# here, not in the extension, so a consumer can subscribe without importing
# ``aelix_agents`` — which is exactly the module a depth-suppressed child never
# loads. The payload of every one of them is :class:`SubagentProgress`.
SUBAGENT_START = "subagent_start"
SUBAGENT_TOOL = "subagent_tool"
SUBAGENT_END = "subagent_end"

SubagentState = Literal["starting", "running", "done", "error", "stopped"]
SubagentOutcome = Literal["ok", "error", "timeout", "aborted", "declined"]
# Only "single" is live in P2 — ``spawn`` raises ``ValueError`` on the other
# two. The literal ships whole so the on-the-wire vocabulary never changes
# shape between phases (same reasoning as ``agents/profile.py:208``'s ``role``).
SubagentMode = Literal["single", "parallel", "chain"]


class SubagentStatus(TypedDict):
    id: str
    profile: str
    state: SubagentState
    current_tool: str | None
    elapsed_ms: int
    tokens: int
    cost: float


@dataclass(frozen=True)
class SubagentUsage:
    """Aggregated child spend. All fields default to 0 — provider ``usage``
    dicts are shape-divergent and are ``null`` entirely on errored messages,
    so every read goes through the dual-spelling helper (ADR-0198)."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    tokens: int = 0
    """Context LEVEL, not a sum — last message wins."""
    turns: int = 0


@dataclass(frozen=True)
class SubagentResult:
    """What the parent gets back. Always returned — never raised."""

    id: str
    profile: str
    ok: bool
    status: SubagentOutcome
    summary: str
    truncated: bool = False
    usage: SubagentUsage = field(default_factory=SubagentUsage)
    error: str | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    elapsed_ms: int = 0
    dropped_tools: tuple[str, ...] = ()
    """Tools the profile asked for that the parent's own grant did not carry."""
    details: str | None = None
    """UNCAPPED raw material behind ``summary`` — the full extracted text plus,
    on a failure path, the raw (unsanitized) stderr tail.

    Added in review (finding B8): ``summary`` is capped at ``output_cap`` and
    its truncation marker promises "full output preserved in tool details".
    Without this field that promise is false on the ``/agents run`` door, which
    never builds a ``ToolResult``, and a P4 dashboard or Web UI — both of which
    consume ``SubagentResult``, not ``ToolResult`` — could never show it.
    Consumers must treat this as potentially large; it is NOT sent to the model
    by the ``agent`` tool (it rides ``ToolResult.details`` there)."""
    dropped_lines: int = 0
    """Child stdout lines discarded for exceeding the 4 MiB per-line budget."""
    tool_trail: str | None = None
    """What the child DID — a rendered, bounded, sanitised one-line-per-call
    record of every tool it ran and whether that call failed.

    ``summary`` is the child's CONCLUSION and nothing else: the last
    text-bearing assistant message. Every tool call, every argument and every
    tool-level failure crossed the wire and was discarded, which mattered most
    in chain mode — step k+1 received step k's answer with no way to know what
    ground was already covered, and a child whose every tool errored still came
    back ``ok`` because status is derived from the exit code and the stream
    terminator, neither of which observes a tool error.

    NOT raw material: it is capped and its arguments are stripped of control
    characters and newlines before it is built, because it is composed into the
    next child's prompt. ``details`` remains the uncapped, unsanitised field.

    Additive + defaulted: does NOT bump ``CONTRACT_VERSION``."""

    permission_mode: str | None = None
    """The posture the child actually ran under (ADR-0197 §(e)/§(i)). Recorded
    so ``/agents run``, a P4 dashboard and the ADR's audit story can all show
    what authority was granted, including when a human widened it at the
    spawn-time consent dialog. Additive + defaulted: does NOT bump
    ``CONTRACT_VERSION``."""

    model: str | None = None
    provider: str | None = None
    """WHICH MODEL THE CHILD ACTUALLY RAN, read off its own ``message_end``.

    A profile that declares no ``model:`` emits no ``--model`` flag, so the child
    falls through to the PERSISTED default rather than the parent's run-scope
    model — a different model at a different price. (``model: inherit``
    normalises to :data:`None` and takes the same path, so the word currently
    means the opposite of what it says.) That substitution was completely
    invisible: nothing in the envelope, the footer or the ``/agents run`` grid
    named the child's model, so the only way to notice was the bill.

    Recording it does not decide the precedence question — it makes the answer
    observable, which is the half that has to exist first either way.

    Populated from ``_StreamState.provider``/``.model``, which the reducer
    already fills from every ``message_end`` (``stream.py``), so no new parsing.
    Additive + defaulted: does NOT bump ``CONTRACT_VERSION``."""


@dataclass(frozen=True)
class SubagentProgress:
    id: str
    profile: str
    state: SubagentState
    current_tool: str | None = None
    elapsed_ms: int = 0
    tokens: int = 0
    cost: float = 0.0

    model: str | None = None
    provider: str | None = None
    """WHICH MODEL THE CHILD IS RUNNING, carried LIVE so every progress surface
    can name it — the statusline row, the batch panel, the aggregate — instead of
    only the final ``/agents run`` grid (:attr:`SubagentResult.model`).

    Same value, same provenance as :attr:`SubagentResult.model`: read off the
    child's own ``message_end`` into ``_StreamState.provider``/``.model``
    (``stream.py``) and fanned out by the sole producer ``runtime._publish``. It
    is :data:`None` until the child's first ``message_end`` resolves the run
    model — a surface must render cleanly (omit the term) while it is, never
    print ``None`` — and, for a profile that declared no ``model:``, it names the
    PERSISTED default the child fell through to rather than the parent's
    run-scope model, which is exactly the substitution worth making visible.

    CHILD-AUTHORED, therefore untrusted: a renderer bounds and sanitises it the
    same way it treats ``current_tool`` (``panel._flatten``). Additive +
    defaulted: does NOT bump :data:`CONTRACT_VERSION` (same precedent as
    :attr:`SubagentResult.model`/``.provider``)."""


@dataclass(frozen=True)
class ResolvedProfile:
    name: str
    profile: AgentProfile
    source_path: str
    """The human-owned file the identity came from. ALWAYS shown at the
    spawn-time consent dialog (ADR-0197 §(i), residual R2): the task text is
    model-authored and therefore injectable, this path is not."""
    scope: str


class ProjectScopeRefused(Exception):
    """``resolve_profile`` refused a PROJECT-SCOPED identity (ADR-0197 §(f)).

    THE ONE REFUSAL A CALLER CAN CURE, and therefore the one that has to be
    typed (P2 review, MEDIUM #8). ``/agents run`` offers a per-identity
    confirmation and re-resolves with ``allow_project=True``; every other
    failure is surfaced verbatim. Before this class existed the distinction was
    made by SUBSTRING — ``tui/commands.py``'s ``_PROJECT_SCOPE_MARKER =
    "per-identity confirmation"`` — against a phrase produced by exactly one
    implementation, so a second runtime that implemented ``resolve_profile``
    exactly as the Protocol docstring instructs got no confirmation path at all
    and its project-scoped profiles were simply unrunnable.

    Raise it (or a subclass) instead of a bare error when, and only when, a
    caller supplying ``allow_project=True`` would succeed. An untrusted
    DIRECTORY is not this: no per-identity answer cures it, and prompting for a
    decision that changes nothing is how users learn to click through consent
    dialogs.

    A plain ``Exception`` subclass rather than a member of some hierarchy: the
    contract is interface-only, and an implementation is free to combine this
    with its own error type by multiple inheritance — which is exactly what the
    bundled ``aelix_agents`` runtime does, so that callers catching
    ``ProfileError`` keep working unchanged.
    """


@runtime_checkable
class SubagentRuntime(Protocol):
    """Bound onto ``_ExtensionRuntime`` by the ``aelix-agents`` extension.

    Product-core CALLS this. Product-core must never implement it, and must
    never encode any consent or posture POLICY — ``spawn`` takes its own
    consent internally (ADR-0197 §(i)); the grant type is deliberately NOT on
    this Protocol.
    """

    contract_version: int

    def resolve_profile(
        self, name_or_path: str, *, allow_project: bool = False
    ) -> ResolvedProfile:
        """SECURITY (finding B5): a ``scope == "project"`` profile is REFUSED
        unless ``allow_project`` is True. Directory trust is a yes-once decision
        ancestors inherit (``cli/project_trust.py:60-61``); it is not consent to
        a project-local IDENTITY, which additionally wins a name collision
        against the user's own. Mirrors ``agents/service.py:231-247``.
        Model-driven callers must always pass False.

        THE REFUSAL IS TYPED: raise :class:`ProjectScopeRefused` (or a subclass)
        for it, so a caller able to obtain a per-identity confirmation can tell
        it apart from every other failure without matching on message text.
        Any other error may be any exception you like."""
        ...

    async def spawn(
        self,
        resolved: ResolvedProfile,
        task: str,
        *,
        cwd: str | None = None,
        mode: SubagentMode = "single",
        timeout_ms: int | None = None,
        background: bool = False,
        on_event: Callable[[SubagentProgress], None] | None = None,
    ) -> SubagentResult:
        """The USER-TYPED door (``/agents run``).

        The implementation takes spawn-time consent itself before any process
        exists (ADR-0197 §(i)); a declined dialog yields
        ``SubagentResult(status="declined", ok=False)`` — never an exception,
        never a silent spawn. The model-driven ``agent`` tool does NOT come
        through here: it uses an implementation-private entry point carrying a
        grant already taken in the ``tool_call`` hook."""
        ...

    # THE REGISTRY IS LIVE-ONLY, and a consumer has to know that (P2 review,
    # MEDIUM #16). A finished delegation leaves NO row: the implementation
    # deregisters in a ``finally`` before ``spawn`` returns. So the terminal
    # :data:`SubagentState` values ``"done"`` / ``"error"`` / ``"stopped"`` are
    # unreachable through :meth:`list` and :meth:`status`, and ``status(id)``
    # raises ``KeyError`` for any run that has completed. Terminal state is
    # observable on the :data:`SUBAGENT_END` channel and nowhere else — a P4
    # dashboard must subscribe BEFORE a spawn starts, not poll after it.
    #
    # All four members ship with no product-core consumer in P2, and that is
    # deliberate rather than an oversight: ``/agents run`` awaits ``spawn``
    # inside the command handler and the ``agent`` tool is
    # ``execution_mode="sequential"``, so there is no moment in a P2 session at
    # which a user could type ``/agents list`` or ``/agents stop`` — the REPL is
    # blocked for exactly as long as a child is live. They exist so the
    # vocabulary is stable for the P3/P4 surfaces that CAN reach them
    # (ADR-0197 §(m)); ``stop_all`` is the one the extension teardown calls.

    def list(self) -> list[SubagentStatus]:
        """Every LIVE delegation. A finished run leaves no row behind."""
        ...

    def status(self, id: str) -> SubagentStatus:
        """One LIVE delegation. ``KeyError`` once it has finished."""
        ...

    async def stop(self, id: str) -> None: ...
    async def stop_all(self) -> None: ...


def subagent_depth(env: dict[str, str] | Any | None = None) -> int:
    """Current delegation depth from ``AELIX_SUBAGENT_DEPTH`` (0 when unset).

    Malformed / negative values read as 0 — fail toward "I am the root", the
    value that makes the depth GATE most restrictive downstream.
    """

    # Function-local so that importing the CONTRACT costs nothing beyond the
    # dataclasses above; this is the only line in the module that reads the
    # process environment.
    import os

    source = os.environ if env is None else env
    try:
        value = int(str(source.get(DEPTH_ENV_VAR, "0")).strip() or "0")
    except (TypeError, ValueError):
        return 0
    return max(0, value)


__all__ = [
    "CONTRACT_VERSION",
    "DEPTH_ENV_VAR",
    "MAX_SUBAGENT_DEPTH",
    "MIN_SUPPORTED_CONTRACT_VERSION",
    "SUBAGENT_END",
    "SUBAGENT_START",
    "SUBAGENT_TOOL",
    "ProjectScopeRefused",
    "ResolvedProfile",
    "SubagentMode",
    "SubagentOutcome",
    "SubagentProgress",
    "SubagentResult",
    "SubagentRuntime",
    "SubagentState",
    "SubagentStatus",
    "SubagentUsage",
    "subagent_depth",
]
