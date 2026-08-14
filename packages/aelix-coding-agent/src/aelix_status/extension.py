"""``StatusExtension`` — the bundled read-only introspection extension (#101).

WHY AN EXTENSION AND NOT A ``tools/`` BUILT-IN. Every built-in coding tool
receives a :class:`~aelix_ai.tools.ToolExecutionContext`, and that dataclass has
exactly four fields — ``tool_call_id``, ``signal``, ``on_partial``, ``model``
(``aelix_ai/tools.py:77-84``). None of them reaches the harness, the extension
runtime, the loaded-extension list or the trust decision, so a tool under
``tools/`` structurally cannot see the runtime state this tool exists to report.
An extension can: ``ExtensionAPI`` and ``ExtensionContext`` are the only objects
that carry it. ``aelix_agents`` already does exactly this for the ``agent`` tool
(``extension.py:314`` ``register_tool``), and this follows it.

WHERE THIS PACKAGE SITS, AND THE RULE APPLIED. ADR-0197 §(a) splits the tree into
kernel / product-core / bundled-extension, and ADR-0208 states what that split
actually asks: "the only question the band rule asks is whether the change is
*delegation policy*". This spawns nothing, authors no consent and declares no
cap, so the band rule neither forces band 3 nor forbids band 2 — it does not
decide this package at all. What decided it is narrower: ``aelix_agents`` is
defined by ADR-0197's own table as "the only band that spawns", and
``tests/agents/test_p2_band_boundaries.py`` treats every file under it as
delegation surface. A read-only introspection extension living there would make
both the label and the gate imprecise, so it gets its own top-level package in
the same wheel — the arrangement ADR-0197 measured as costing one line of
packaging (``[tool.hatch.build.targets.wheel] packages``) and nothing else.

THE ONE THING THAT LINE BUYS, AND THE ONLY WAY TO CHECK IT. Without the
``packages`` entry this package is ABSENT FROM THE WHEEL while every source
checkout keeps passing — the exact failure ADR-0197's pyproject comment shouts
about. ``tests/status/test_status_wheel.py`` therefore builds a real wheel and
looks inside it; a test that reads the source tree cannot see this class of bug.

ANCHOR CONVENTION (ADR-0197's, verbatim in spirit): every ``file:line`` below was
re-read against this tree. They are the EVIDENCE for a decision, not a
maintenance contract — ``cli/entry.py`` in particular moves under every
concurrent track, so nothing there is cited by line at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import TextContent
from aelix_ai.tools import Tool as AgentTool
from aelix_ai.tools import ToolResult
from aelix_coding_agent.cli.config import VERSION, get_agent_dir

from aelix_status.snapshot import (
    RuntimeSnapshot,
    resolve_project_trusted_fail_closed,
    summarise_extensions,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_ai.tools import ToolExecutionContext
    from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext

STATUS_TOOL_NAME = "aelix_status"

UNKNOWN_MODE = "unknown"
"""What ``mode`` reports when nothing wired the resolved app mode.

An honest gap rather than a guess. The alternatives available inside an
extension — ``sys.argv``, ``stdin.isatty()``, ``ctx.has_ui`` — are each a SECOND
opinion that can disagree with the one ``entry.py`` acted on, and a status tool
whose job is to end guessing must not be the thing guessing.
"""

STATUS_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}
"""The no-argument schema, spelled out — a bare ``{}`` does NOT survive the wire.

Measured through the three shipped converters with ``parameters={}`` and with the
form above:

    convert_responses_tools    {}  -> "parameters": {}
    google convert_tools       {}  -> "parametersJsonSchema": {}
    openai_completions         {}  -> {"type": "object", "properties": {}}

Only the Chat-Completions path normalises, and it does so by accident of
``getattr(tool, "parameters", None) or {...}`` (``openai_completions.py:500``) —
``or`` is falsy-triggered, so ``{}`` takes the fallback. The Responses adapter
tests ``if params is None`` (``_openai_responses_shared.py:171``) and ``{}`` is
not ``None``, so it ships the empty object verbatim; ``_google_shared.py:478``
assigns it with no test at all. Declaring the schema here makes all three agree,
which is why ``tools/ls.py:56-69`` — an all-optional tool — writes it out too.

``required: []`` is carried for the same reason ``ls`` carries it: it is the
explicit statement that there are no required arguments, as opposed to an
omission a strict-schema provider may read either way.
"""


def create_status_tool(execute: Any) -> AgentTool:
    """Build the ``aelix_status`` tool.

    NO ``execution_mode="sequential"``. The ``agent`` tool sets it as a security
    control (``aelix_agents/tool.py:580-589``: it downgrades the whole batch so a
    modal consent dialog cannot race), and this tool has no dialog, no process
    and no mutation to protect. Declaring it here would silently serialise every
    batch that happens to contain a status call — a real cost for a fabricated
    reason.
    """

    return AgentTool(
        name=STATUS_TOOL_NAME,
        description=(
            "Report this aelix process's own runtime state: version, working "
            "directory, run mode, whether the project is trusted, the active "
            "and available tool names, the loaded extensions, and the plugin "
            "manifest API level. Read-only, no arguments. Call it before "
            "writing or installing an extension instead of assuming."
        ),
        parameters=dict(STATUS_TOOL_PARAMETERS),
        execute=execute,
    )


@dataclass
class StatusExtension:
    """Registers ``aelix_status`` and answers it from live runtime sources.

    The first three fields are WIRING and there is no fallback for them: an
    :class:`ExtensionContext` never carries the resolved app mode, never carries
    a trustworthy trust decision (see :attr:`project_trusted`) and never carries
    the sibling extensions. The last two are FALLBACKS — a live context or the
    shipped ``get_agent_dir`` answers them, and the fields exist only for the
    window before the first hook has fired.
    """

    mode: str | None = None
    """The resolved app mode from ``entry.py``'s ``resolve_app_mode``.

    A plain ``str``, not a callable, and that is measured rather than assumed:
    ``app_mode`` is ASSIGNED exactly once in ``entry.py`` — the single
    ``app_mode = resolve_app_mode(parsed, stdin_is_tty)`` in ``_async_main`` — and
    every other occurrence of the name in that file is a comparison or a
    parameter. Contrast
    ``AgentsExtension.no_context_files``, which has to be a callable because
    ``/agents use`` rewrites the ``Args`` object it reads in place.
    """

    project_trusted: Callable[[], bool] | None = None
    """The resolved Project-Trust decision, read live.

    ``None`` — the unwired default — resolves to ``False``. See
    :func:`~aelix_status.snapshot.resolve_project_trusted_fail_closed` for why
    ``ctx.is_project_trusted()`` is not consulted instead.
    """

    extensions: list[Any] | None = None
    """The LIVE discovered-extension list, held by reference.

    This is ``entry.py``'s ``discovered_extensions`` holder — the same object
    ``/extension``'s viewer reads. ``_build_harness_options`` clears and refills
    it on every harness (re)build, so holding the LIST rather than a copy is what
    makes ``/reload`` and ``/new`` visible here.
    """

    cwd: str | None = None
    """Pre-hook fallback for the project root; a live ``ctx.cwd`` always wins."""

    agent_dir: str | os.PathLike[str] | None = None
    """Where the global extension tier lives, for the ``scope`` label only.

    ``None`` resolves through :func:`~aelix_coding_agent.cli.config.get_agent_dir`
    — the same function ``entry.py`` passes to the loader as ``agent_dir=``, so
    the classifier and the discovery it is describing read one source.
    """

    _api: Any | None = field(default=None, init=False)
    _ctx: Any | None = field(default=None, init=False)
    _tool: Any | None = field(default=None, init=False)

    # ── setup ────────────────────────────────────────────────────

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Extension factory entry point (``ExtensionFactory``)."""

        self._api = aelix
        self._tool = create_status_tool(self._execute)
        aelix.register_tool(self._tool)
        aelix.on("tool_call", self._on_tool_call)

    async def _on_tool_call(
        self, event: ToolCallHookEvent, ctx: ExtensionContext
    ) -> None:
        """Keep the context. Returns ``None`` — it decides nothing.

        Subscribed for EVERY tool call, not just ours, because the context is the
        only channel through which ``execute()`` ever sees a cwd or an active
        tool list, and because the ``tool_call`` hook for a call always runs
        immediately before the ``execute()`` that performs it — so the context an
        ``aelix_status`` call answers from is the one from its own call.

        Returning ``None`` rather than a :class:`ToolCallResult` is the whole
        contract of this handler: a read-only tool must not be able to block,
        allow or rewrite anybody else's call.
        """

        self._ctx = ctx

    # ── the snapshot ─────────────────────────────────────────────

    def snapshot(self) -> RuntimeSnapshot:
        """Assemble the snapshot from whatever is live, degrading per source.

        Public because #101 wants one runtime-introspection source with a
        possible ``aelix status`` CLI adapter over it, not two implementations
        that drift.
        """

        cwd = self._resolve_cwd()
        agent_dir = self._resolve_agent_dir()
        return RuntimeSnapshot(
            version=VERSION,
            cwd=cwd,
            mode=self.mode or UNKNOWN_MODE,
            project_trusted=resolve_project_trusted_fail_closed(self.project_trusted),
            active_tools=self._active_tools(),
            all_tools=self._all_tools(),
            loaded_extensions=summarise_extensions(
                self.extensions, cwd=cwd, agent_dir=agent_dir
            ),
        )

    def _resolve_cwd(self) -> str:
        ctx = self._ctx
        if ctx is not None:
            try:
                return str(ctx.cwd)
            except Exception:  # noqa: BLE001 — a stale ctx must not fail the tool
                pass
        if self.cwd:
            return str(self.cwd)
        return os.getcwd()

    def _resolve_agent_dir(self) -> str | None:
        if self.agent_dir is not None:
            return str(self.agent_dir)
        try:
            return str(get_agent_dir())
        except Exception:  # noqa: BLE001 — only a display label depends on this
            return None

    def _active_tools(self) -> tuple[str, ...]:
        """``ctx.get_active_tools()``, or empty when nothing is bound.

        The unbound action is a THROWING stub (``extensions/api.py:377``,
        ``_make_throwing_stub``), unlike ``get_all_tools`` which the API itself
        already converts to ``[]``. So this one has to catch, and an empty tuple
        is the right degradation: "we could not determine the active tools" is
        better rendered as an empty list the model can see than as a tool error
        it has to interpret.
        """

        ctx = self._ctx
        if ctx is None:
            return ()
        try:
            return tuple(str(name) for name in ctx.get_active_tools())
        except Exception:  # noqa: BLE001
            return ()

    def _all_tools(self) -> tuple[str, ...]:
        """``api.get_all_tools()`` reduced to names.

        NAMES ONLY. ``ToolInfo`` also carries ``description``, and for an
        extension-registered tool that string is author-controlled — re-emitting
        it here would widen this tool's output surface to text this package never
        reviewed, for no gain the model does not already get from its own tool
        definitions.
        """

        api = self._api
        if api is None:
            return ()
        try:
            return tuple(str(getattr(info, "name", "")) for info in api.get_all_tools())
        except Exception:  # noqa: BLE001 — a stale runtime raises from assert_active
            return ()

    # ── the tool ─────────────────────────────────────────────────

    async def _execute(
        self, args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        """Serialise the snapshot. ``args`` is ignored, deliberately.

        The tool declares no parameters, but ``validate_tool_arguments`` is
        LENIENT by design — "unknown keys are preserved" (``aelix_ai/tools.py:290``)
        — so a model that invents an argument reaches here with it. Ignoring the
        dict entirely means there is no input to this tool at all, which is what
        makes "read-only, no mutation" checkable rather than asserted.
        """

        payload = self.snapshot().to_dict()
        return ToolResult(
            content=[TextContent(text=json.dumps(payload, indent=2))],
        )
