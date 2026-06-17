"""Pi parity: ``main.ts`` entry point (716 LOC reduced for 5a-i + 5a-ii scope).

Sprint 6h₆ (Phase 5a-i + 5a-ii, ADR-0089, P-385 / P-391 / P-392).

Top-level lifecycle:

1. :func:`parse_args` (Pi parity hand-rolled parser).
2. Diagnostic flush (errors → exit 1; warnings → stderr only).
3. ``--help`` / ``--version`` short-circuit.
4. :func:`resolve_app_mode` (Pi ``main.ts:96-113``).
5. Interactive mode → :class:`NotImplementedError` (Phase 5b carry-forward).
6. RPC + ``@file`` guard.
7. Piped stdin read (non-RPC).
8. :func:`process_file_arguments` (text-only — image branch deferred).
9. :func:`build_initial_message` (with Pi ``.shift()`` side effect).
10. Harness + runtime construction (in-memory or JSONL session).
11. Dispatch to :func:`run_rpc_mode` or :func:`run_print_mode`.
12. Cleanup (runtime dispose in ``finally``).

Pi citation: ``main.ts:1-716`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016`` (resolve_app_mode at lines
96-113; main entry at lines 423-716).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Literal

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime.agent_session_runtime import (
    create_agent_session_runtime,
)
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_agent_core.types import AgentTool

from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_coding_agent.mcp import McpClientManager
from aelix_coding_agent.tools import create_all_tools

from .agent_context import build_system_prompt, discover_context_files
from .args import Args, parse_args, print_help
from .config import VERSION, get_agent_dir, load_mcp_server_contribs
from .file_processor import process_file_arguments
from .initial_message import build_initial_message
from .runtime_bootstrap import load_dotenv, register_providers, resolve_model

AppMode = Literal["interactive", "print", "json", "rpc"]


def resolve_app_mode(parsed: Args, stdin_is_tty: bool) -> AppMode:
    """Pi parity: ``resolveAppMode`` (``main.ts:96-113``).

    Resolution order:
      1. ``--mode rpc`` → ``"rpc"``.
      2. ``--mode json`` → ``"json"``.
      3. ``--print`` OR piped stdin → ``"print"``.
      4. Otherwise → ``"interactive"``.
    """

    if parsed.mode == "rpc":
        return "rpc"
    if parsed.mode == "json":
        return "json"
    if parsed.print_mode or not stdin_is_tty:
        return "print"
    return "interactive"


def to_print_output_mode(app_mode: AppMode) -> Literal["text", "json"]:
    """Pi parity: ``toPrintOutputMode``.

    Print mode handles both ``"print"`` (text) and ``"json"`` output
    variants. The mapping below mirrors Pi's helper used at the
    :func:`run_print_mode` call site.
    """

    return "json" if app_mode == "json" else "text"


async def _read_piped_stdin() -> str | None:
    """Pi parity: ``readPipedStdin``.

    Returns :data:`None` when stdin is a TTY (interactive shell). When
    stdin is piped (file redirect, here-doc, etc.), reads the full
    payload and strips surrounding whitespace; empty content → :data:`None`.
    """

    if sys.stdin.isatty():
        return None
    data = await asyncio.to_thread(sys.stdin.read)
    stripped = data.strip()
    return stripped or None


async def _build_session(parsed: Args, repo: JsonlSessionRepo) -> Session:
    """Build a :class:`Session` per ``--no-session`` semantics.

    - ``--no-session`` → in-memory :class:`MemorySessionStorage` (not
      persisted to disk).
    - Otherwise → :meth:`JsonlSessionRepo.create` rooted at cwd.

    Pi has the same branch (``main.ts``) — Aelix mirrors via the
    workspace-local :class:`JsonlSessionRepo`.
    """

    if parsed.no_session:
        return Session(MemorySessionStorage())
    cwd = str(Path.cwd())
    return await repo.create(JsonlSessionCreateOptions(cwd=cwd))


def _validate_continue_flag(parsed: Args) -> str | None:
    """Sprint 6h₈ §D — ``--continue`` argument-compatibility validation.

    Pi parity: ``main.ts:280-281`` dispatches ``--continue`` only when
    no other session-source flag is set. Aelix surfaces the conflicts
    explicitly with Pi-shape error messages.

    Returns
    -------
    str | None
        Error message when ``--continue`` is incompatible with another
        already-set flag, or :data:`None` when the combination is OK.
    """

    if not parsed.continue_session:
        return None
    if parsed.no_session:
        return "--continue is incompatible with --no-session"
    if parsed.session is not None:
        return "--continue is incompatible with --session"
    if parsed.fork is not None:
        return "--continue is incompatible with --fork"
    return None


def _resolve_active_tools(parsed: Args) -> list[str] | None:
    """Pi ``main.ts:369-375`` tool gating → harness ``active_tool_names``.

    - ``--no-tools`` → ``[]`` (disable every tool).
    - ``--tools a,b`` → ``[a, b]`` (explicit allowlist; the harness's F-9
      validator rejects unknown names after full tool registration).
    - else → ``None`` (all tools active — the Aelix default).

    ``--no-builtin-tools`` (built-ins off, extension/MCP tools on) is NOT wired
    here: ``active_tool_names`` is seeded before extensions register their
    tools, so expressing it faithfully needs post-load tool knowledge. Deferred
    (tracked) rather than shipped as a divergent approximation that would also
    disable extension tools.
    """

    if parsed.no_tools:
        return []
    if parsed.tools:
        return list(parsed.tools)
    return None


async def _build_harness_options(
    parsed: Args, session: Session, *, mcp_tools: list[AgentTool] | None = None
) -> AgentHarnessOptions:
    """Assemble :class:`AgentHarnessOptions` from parsed CLI args.

    Sprint 6h₆ is print + JSON + RPC only — the ``SettingsManager`` port
    is deferred (ADR-0089 §"Carry-forward"). Model defaults derive from
    ``--provider`` / ``--model`` flags via a bare :class:`Model` — Pi's
    rich model-resolution path (provider lookup, cost map, thinking
    levels, etc.) lands with the ``SettingsManager`` port.
    """

    # Resolve the turn model (OpenRouter-from-env aware; falls back to a bare
    # model from --model/--provider). Providers are registered in main_sync.
    model = resolve_model(parsed.model, parsed.provider)
    # Resolve to an absolute path so the tool sandbox root + AGENTS.md anchor are
    # stable even if something later chdir's the process (e.g. a bash-tool ``cd``).
    cwd = str(Path.cwd())

    # Sprint 6h₁₁: wire the coding toolset + a real coding-agent system prompt.
    # Previously the harness ran with EMPTY tools + EMPTY system prompt (a bare
    # chat model with no identity and no ability to touch files). The 7 built-in
    # tools (read/write/edit/bash/grep/find/ls) + the base prompt make it an
    # actual coding agent. An explicit ``--system-prompt`` still overrides.
    tools = list(create_all_tools(cwd).values())
    # MCP (Tier 4) tools, connected once in _async_main and shared across
    # harness rebuilds, join the built-in toolset (``<server>__<tool>`` names).
    if mcp_tools:
        tools.extend(mcp_tools)
    # --tools / --no-tools gating (Pi ``main.ts:369-375``).
    active_tool_names = _resolve_active_tools(parsed)
    system_prompt = (
        parsed.system_prompt
        if parsed.system_prompt is not None
        else build_system_prompt(cwd)
    )
    # Extensions: built-in safety (Guardrail FIRST so hard-deny patterns like
    # ``rm -rf`` short-circuit via first-block-wins BEFORE the permission
    # prompt) PREPENDED ahead of on-disk + explicit ``--extension`` paths.
    # ``--no-extensions`` disables auto-discovery (project-local + global +
    # entry_points) but keeps explicit ``-e`` paths — Pi ``noExtensions``
    # (``resource-loader.ts:395-399``). All build against ONE shared runtime
    # the harness reuses (``runtime=``) so ``ctx.ui`` / ``ctx.has_ui`` bindings
    # reach the handlers.
    loaded = await discover_and_load_extensions(
        [str(p) for p in parsed.extensions],
        cwd=Path(cwd),
        agent_dir=Path(get_agent_dir()),
        prepend=[GuardrailExtension(), PermissionExtension()],
        no_discovery=parsed.no_extensions,
    )
    for err in loaded.errors:
        print(f"Warning: extension load: {err}", file=sys.stderr)
    # Security (Aelix-additive hardening; Pi only warns in docs): on-disk
    # extensions import + run arbitrary .py with FULL user privileges. Warn
    # when any load beyond the 2 prepended built-ins.
    on_disk = max(0, len(loaded.extensions) - 2)
    if on_disk > 0:
        print(
            f"Warning: loaded {on_disk} on-disk extension(s) with full system "
            "permissions from .aelix/extensions (project-local + global).",
            file=sys.stderr,
        )
    options = AgentHarnessOptions(
        model=model,
        session=session,
        cwd=cwd,
        tools=tools,
        system_prompt=system_prompt,
        extensions=loaded.extensions,
        runtime=loaded.runtime,
        active_tool_names=active_tool_names,
    )

    # Auto-discovered AGENTS.md project context (Pi ``--no-context-files`` gate),
    # then the explicit ``--append-system-prompt`` chunks. The harness joins all
    # of these onto the base system prompt with ``"\n\n"`` at ``__init__`` time.
    append: list[str] = []
    if not parsed.no_context_files:
        context = discover_context_files(cwd)
        if context:
            append.append(context)
    append.extend(parsed.append_system_prompt)
    options.append_system_prompt = append
    return options


async def _async_main(argv: list[str]) -> int:
    """Pi parity: ``main()`` body (``main.ts:423-716`` reduced for scope)."""

    parsed = parse_args(argv)

    # === Diagnostics flush ====================================================
    for diag in parsed.diagnostics:
        prefix = "Error: " if diag["type"] == "error" else "Warning: "
        print(f"{prefix}{diag['message']}", file=sys.stderr)
    if any(d["type"] == "error" for d in parsed.diagnostics):
        return 1

    # === --offline (Pi main.ts:425-427) ======================================
    # Mirror Pi: ``--offline`` OR a pre-set ``PI_OFFLINE`` env both engage
    # offline mode (Pi reads the flag with ``||`` over the env). Inert today
    # (Aelix has no startup network operations) but preserves the contract.
    if parsed.offline or os.environ.get("PI_OFFLINE"):
        os.environ["PI_OFFLINE"] = "1"

    # === Help / version short-circuit ========================================
    if parsed.help:
        print_help()
        return 0
    if parsed.version:
        print(VERSION)
        return 0

    # === --continue flag validation (Sprint 6h₈ §D) =========================
    # Sprint 6h₈ W5 MAJOR-2 fold-in: validate BEFORE the ``--list-models``
    # short-circuit so that incompatible combos (e.g. ``--list-models
    # --continue --no-session``) emit the spec-mandated stderr diagnostic
    # rather than silently succeeding on the list-models exit path.
    continue_error = _validate_continue_flag(parsed)
    if continue_error is not None:
        print(f"Error: {continue_error}", file=sys.stderr)
        return 1

    # === --list-models — Sprint 6h₇a (ADR-0090) wired =======================
    if parsed.list_models is not None:
        # Lazy imports — defer ``ModelRegistry`` + ``AuthStorage``
        # construction cost off the ``--version`` / ``--help`` fast paths
        # (~10ms saved on cold start). Hoisting these to module scope
        # would trigger auth file I/O on every invocation. (``Path`` is a
        # zero-cost stdlib import and lives at module scope.)
        from aelix_ai.oauth import AuthStorage

        from ..model_registry import ModelRegistry
        from .config import get_agent_dir
        from .list_models import list_models

        auth_storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
        await auth_storage.load()
        model_registry = ModelRegistry.create(auth_storage)
        await list_models(model_registry, parsed.list_models)
        return 0

    # Sprint 6h₈ §D: --resume picker is deferred to Phase 5b TUI work.
    if parsed.resume:
        print(
            "Error: --resume (interactive picker) deferred to Phase 5b — "
            "TUI work (ADR-0088).",
            file=sys.stderr,
        )
        raise NotImplementedError(
            "--resume picker deferred to Phase 5b (ADR-0088)."
        )

    # === Mode resolution =====================================================
    stdin_is_tty = sys.stdin.isatty()
    app_mode = resolve_app_mode(parsed, stdin_is_tty)

    # Pi guard: ``--mode rpc`` is incompatible with ``@file`` positional.
    if app_mode == "rpc" and parsed.file_args:
        print(
            "Error: --mode rpc cannot be combined with @file arguments",
            file=sys.stderr,
        )
        return 1

    # === Interactive mode is dispatched post-construction (Sprint 6h₁₀a) ======
    # The Phase 5b NotImplementedError carry-forward (ADR-0088) is replaced by
    # run_tui; see the dispatch branch below. Interactive needs the harness +
    # runtime built first (parity with the rpc / print branches).

    # === Stdin + file processing (print / json only) =========================
    # Interactive starts at an empty prompt (no piped stdin in a TTY; @file /
    # -m initial messages are a Sprint 6h₁₀b carry-forward). rpc feeds input
    # over the JSONL transport, so neither path reads stdin here.
    stdin_content: str | None = None
    file_text = ""
    file_images: list[object] | None = None

    if app_mode in ("print", "json"):
        stdin_content = await _read_piped_stdin()
        if parsed.file_args:
            processed = await process_file_arguments(parsed.file_args)
            file_text = processed.text
            file_images = processed.images or None

    # === Initial message build (SIDE EFFECT on parsed.messages) ==============
    initial = build_initial_message(
        parsed,
        file_text=file_text,
        file_images=file_images,
        stdin_content=stdin_content,
    )

    # === Harness + runtime construction ======================================
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs)

    # Sprint 6h₈ §D: ``--continue`` / ``-c`` auto-resume short-circuit.
    # When set, attempt to open the most-recent session in cwd; if none
    # exist, fall back to ``_build_session`` silently (Pi parity per
    # ``main.ts:280-281`` ``SessionManager.continueRecent`` semantics).
    if parsed.continue_session:
        most_recent = await repo.find_most_recent(str(Path.cwd()))
        if most_recent is not None:
            session = await repo.open(most_recent)
        else:
            session = await _build_session(parsed, repo)
    else:
        session = await _build_session(parsed, repo)

    # MCP servers (Tier 4): connect ONCE here, share the connected tools across
    # every harness rebuild (the tool closures hold live connections, so they
    # survive rebuilds), and dispose ONCE in the finally block. A failed server
    # warns and is skipped — one bad server never aborts the agent.
    mcp_contribs, mcp_warnings = load_mcp_server_contribs(str(Path.cwd()))
    for warning in mcp_warnings:
        print(f"Warning: MCP config: {warning}", file=sys.stderr)
    mcp_manager: McpClientManager | None = None
    mcp_tools: list[AgentTool] = []
    if mcp_contribs:
        mcp_manager = McpClientManager(mcp_contribs)
        for conn_err in await mcp_manager.connect_all():
            print(f"Warning: MCP server failed: {conn_err}", file=sys.stderr)
        mcp_tools = await mcp_manager.collect_agent_tools()

    async def _harness_factory(new_session: Session) -> AgentHarness:
        opts = await _build_harness_options(
            parsed, new_session, mcp_tools=mcp_tools
        )
        return AgentHarness(opts)

    harness = await _harness_factory(session)
    runtime = await create_agent_session_runtime(
        harness, _harness_factory, repo=repo, fs=fs
    )

    try:
        if app_mode == "interactive":
            try:
                from aelix_coding_agent.modes import run_tui
            except ImportError as exc:
                # The [tui] extra (prompt-toolkit + rich) is not installed.
                print(
                    "Error: interactive mode requires the TUI extra. Install "
                    "with: pip install 'aelix-coding-agent[tui]' "
                    f"(missing: {exc.name}).",
                    file=sys.stderr,
                )
                return 1

            return await run_tui(runtime, cwd=str(Path.cwd()))

        if app_mode == "rpc":
            from aelix_coding_agent.modes import run_rpc_mode

            await run_rpc_mode(
                harness,
                runtime_host=runtime,
                harness_factory=_harness_factory,
                repo=repo,
                fs=fs,
            )
            return 0

        from aelix_coding_agent.modes import run_print_mode

        return await run_print_mode(
            runtime,
            mode=to_print_output_mode(app_mode),
            messages=parsed.messages,
            initial_message=initial.initial_message,
            initial_images=initial.initial_images,
        )
    finally:
        # run_print_mode already disposes the runtime, but we also dispose
        # for the rpc / early-return paths. ``_safe_dispose`` style: any
        # error here is swallowed because the run already completed.
        with contextlib.suppress(Exception):
            await runtime.dispose()
        # Tear down MCP connections (LIFO via each connection's AsyncExitStack).
        if mcp_manager is not None:
            with contextlib.suppress(Exception):
                await mcp_manager.disconnect_all()


def main_sync() -> None:
    """Sync entry for ``[project.scripts] aelix = '...:main_sync'``.

    Loads a cwd ``.env`` + registers provider adapters (real-turn enablement;
    done here rather than in :func:`_async_main` so tests/embedders that call
    ``_async_main`` directly stay side-effect-free), then wraps
    :func:`_async_main` in :func:`asyncio.run` and forwards the exit code.
    """

    load_dotenv()
    register_providers()
    exit_code = asyncio.run(_async_main(sys.argv[1:]))
    sys.exit(exit_code)


__all__ = [
    "AppMode",
    "_async_main",
    "main_sync",
    "resolve_app_mode",
    "to_print_output_mode",
]
