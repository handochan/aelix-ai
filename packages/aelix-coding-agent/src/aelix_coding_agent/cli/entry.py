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
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime.agent_session_runtime import (
    create_agent_session_runtime,
)
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import load_jsonl_session_metadata
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_agent_core.session.storage import JsonlSessionMetadata, SessionError
from aelix_agent_core.types import AgentTool

from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.extensions.loader import discover_and_load_extensions
from aelix_coding_agent.mcp import McpClientManager
from aelix_coding_agent.tools import create_all_tools

from .agent_context import build_system_prompt, discover_context_files
from .args import Args, parse_args, print_help
from .config import (
    VERSION,
    get_agent_dir,
    get_session_dir,
    load_mcp_server_contribs,
)
from .file_processor import process_file_arguments
from .initial_message import build_initial_message
from .runtime_bootstrap import load_dotenv, register_providers, resolve_model

if TYPE_CHECKING:
    from aelix_ai.streaming import Model

    from ..model_registry import ModelRegistry

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


async def _resolve_session_metadata(
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
    arg: str,
    cwd: str,
) -> JsonlSessionMetadata | None:
    """Resolve a ``--session`` / ``--fork`` argument to session metadata.

    Pi parity: ``resolveSessionPath`` (``main.ts``) — a value that looks
    like a file path (contains a path separator or ends in ``.jsonl``) is
    loaded directly via :func:`load_jsonl_session_metadata`; otherwise it
    is treated as a session-id prefix and matched against the cwd-local
    sessions first, then globally across projects. Returns :data:`None`
    when an id prefix matches nothing (path-like inputs raise
    :class:`SessionError` for a bad/missing file).

    The classification uses ONLY structural cues (Pi's exact heuristic) —
    no on-disk existence check — so a separator-free session-id that
    happens to collide with a file name in ``cwd`` is still resolved as an
    id, not mis-routed to the path loader.
    """

    looks_like_path = "/" in arg or "\\" in arg or arg.endswith(".jsonl")
    if looks_like_path:
        return await load_jsonl_session_metadata(fs, arg)
    # Session-id prefix: cwd-local first (Pi searches local before global).
    for opts in (JsonlSessionListOptions(cwd=cwd), JsonlSessionListOptions()):
        for meta in await repo.list(opts):
            if meta.id == arg or meta.id.startswith(arg):
                return meta
    return None


async def _build_session(
    parsed: Args, repo: JsonlSessionRepo, fs: LocalFileSystem, cwd: str
) -> Session:
    """Build a :class:`Session` per the session-source flags.

    - ``--no-session`` → in-memory :class:`MemorySessionStorage` (not
      persisted to disk).
    - ``--session <path|id>`` → open the resolved session (Pi
      ``SessionManager.open``); ``cwd`` is rewritten onto the loaded
      session via ``cwd_override``.
    - ``--fork <path|id>`` → fork the resolved session into ``cwd`` (Pi
      ``SessionManager.forkFrom``).
    - otherwise → a fresh session rooted at ``cwd``.

    ``--session`` / ``--fork`` raise :class:`SessionError` (``not_found``)
    when the argument resolves to nothing; the caller surfaces it as a
    startup diagnostic.
    """

    if parsed.no_session:
        return Session(MemorySessionStorage())
    if parsed.session is not None:
        meta = await _resolve_session_metadata(repo, fs, parsed.session, cwd)
        if meta is None:
            raise SessionError(
                "not_found",
                f"No session matching --session {parsed.session!r}",
            )
        return await repo.open(meta, cwd_override=cwd)
    if parsed.fork is not None:
        meta = await _resolve_session_metadata(repo, fs, parsed.fork, cwd)
        if meta is None:
            raise SessionError(
                "not_found", f"No session matching --fork {parsed.fork!r}"
            )
        return await repo.fork_from(meta, cwd)
    return await repo.create(JsonlSessionCreateOptions(cwd=cwd))


async def _run_export(
    parsed: Args, repo: JsonlSessionRepo, fs: LocalFileSystem
) -> int:
    """Pi parity: ``--export <src> [out]`` (``main.ts`` ``exportFromFile``).

    Loads the JSONL session at ``parsed.export``, renders its messages to
    a standalone HTML document, and writes it to the optional output path
    (the first positional, ``parsed.messages[0]``; default
    ``aelix-session-<basename>.html``). Prints the resolved output path.
    Raises :class:`SessionError` when the source can't be loaded (the
    caller surfaces it as a startup diagnostic).
    """

    assert parsed.export is not None  # guarded by the caller
    # Lazy import — ``export_html`` pulls Pygments; keep it off the cold
    # path for every non-export invocation.
    from aelix_coding_agent._export_html import export_html

    meta = await load_jsonl_session_metadata(fs, parsed.export)
    session = await repo.open(meta)
    context = await session.build_context()
    output_path = parsed.messages[0] if parsed.messages else None
    basename = Path(parsed.export).stem or "untitled"
    resolved = export_html(
        context.messages, output_path, session_basename=basename
    )
    print(resolved)
    return 0


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


def _make_auth_callback(
    model_registry: ModelRegistry,
) -> Callable[[Model], Awaitable[dict[str, Any] | None]]:
    """Adapt :meth:`ModelRegistry.get_api_key_and_headers` to the harness
    callback contract (``AgentHarnessOptions.get_api_key_and_headers``).

    The harness (``core.py:_make_stream_fn`` @3447-3472) expects a callable
    returning a ``dict`` with ``"apiKey"`` / ``"headers"`` keys (or
    :data:`None` = "no opinion" — Pi ``types.ts:808-811``). The registry
    instead returns a :class:`ResolvedRequestAuth` dataclass, so a thin
    adapter converts it:

    - ``ok=False`` → raise (the harness wraps it as an ``"auth"`` error;
      Pi treats a resolution failure as fatal).
    - ``ok=True`` with a key or headers → ``{"apiKey": ..., "headers": ...}``.
    - ``ok=True`` with NEITHER a key NOR headers → :data:`None` so the
      harness's "neither apiKey nor headers" guard (@3463) is not tripped
      and the adapter's env fallback (``get_env_api_key``) still resolves.
      This keeps OAuth-only / env-only providers working.
    """

    async def _resolve(model: Model) -> dict[str, Any] | None:
        auth = await model_registry.get_api_key_and_headers(model)
        if not auth.ok:
            # Surfaced by the harness as an ``"auth"`` AgentHarnessError.
            raise RuntimeError(auth.error or "auth resolution failed")
        if not auth.api_key and not auth.headers:
            # "No opinion" — let the adapter's env fallback take over.
            return None
        return {"apiKey": auth.api_key, "headers": auth.headers}

    return _resolve


async def _build_harness_options(
    parsed: Args,
    session: Session,
    *,
    mcp_tools: list[AgentTool] | None = None,
    get_api_key_and_headers: Callable[..., Any] | None = None,
) -> AgentHarnessOptions:
    """Assemble :class:`AgentHarnessOptions` from parsed CLI args.

    Sprint 6h₆ is print + JSON + RPC only — the ``SettingsManager`` port
    is deferred (ADR-0089 §"Carry-forward"). Model defaults derive from
    ``--provider`` / ``--model`` flags via a bare :class:`Model` — Pi's
    rich model-resolution path (provider lookup, cost map, thinking
    levels, etc.) lands with the ``SettingsManager`` port.

    ``get_api_key_and_headers`` (P0 #7 / ITEM 6) is threaded onto the
    harness options so the provider-auth cascade (runtime ``--api-key``
    override → stored → OAuth → env) reaches ``_make_stream_fn``. It is
    :data:`None` on the env-only path (no ``--api-key``), preserving the
    adapter's direct ``get_env_api_key`` resolution (no regression).
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
        get_api_key_and_headers=get_api_key_and_headers,
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
        from .list_models import list_models

        auth_storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
        await auth_storage.load()
        model_registry = ModelRegistry.create(auth_storage)
        await list_models(model_registry, parsed.list_models)
        return 0

    # === --export <src> [out] — early-exit action (Pi ``exportFromFile``) =====
    # Renders a saved JSONL session to standalone HTML and exits, before any
    # mode resolution / stdin processing (Pi runs export as a terminal action).
    if parsed.export is not None:
        fs = LocalFileSystem()
        repo = JsonlSessionRepo(
            fs=fs, sessions_root=parsed.session_dir or get_session_dir()
        )
        try:
            return await _run_export(parsed, repo, fs)
        except SessionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

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
    # Pi parity (``--session-dir``): flag > ``AELIX_CODING_AGENT_SESSION_DIR``
    # env (Pi ``PI_SESSION_DIR``) > the JsonlSessionRepo default
    # (``~/.aelix/sessions``). ``get_session_dir`` returns the tilde-expanded
    # env value or :data:`None`; ``None`` lets the repo apply its own default.
    sessions_root = parsed.session_dir or get_session_dir()
    repo = JsonlSessionRepo(fs=fs, sessions_root=sessions_root)

    cwd = str(Path.cwd())

    # === Provider auth wiring (P0 #7 / ITEM 6, Pi main.ts:574-582) ===========
    # Build the AuthStorage + ModelRegistry ONCE (reuses the --list-models
    # pattern @402-404) so the provider-auth cascade reaches the harness. The
    # registry is threaded into every harness rebuild via the factory below.
    #
    # ``--api-key`` (Pi ``main.ts:574-582``): requires a model with a
    # resolvable provider; the key becomes a runtime override (cascade layer 1,
    # wins over stored/OAuth/env). On the env-only path (no ``--api-key``) the
    # harness callback stays ``None`` so the adapter's direct ``get_env_api_key``
    # resolution is preserved — no regression (design (i)).
    from aelix_ai.oauth import AuthStorage

    from ..model_registry import ModelRegistry

    auth_storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
    await auth_storage.load()
    model_registry = ModelRegistry.create(auth_storage)

    get_api_key_and_headers: Callable[..., Any] | None = None
    if parsed.api_key is not None:
        # Pi parity (main.ts:574-582): ``--api-key`` is meaningless without a
        # model whose provider we can attach the runtime key to.
        model = resolve_model(parsed.model, parsed.provider)
        # NOTE: this ``not model.provider`` guard is STRICTER than pi only
        # because ``resolve_model`` does not yet parse the ``<provider>/<pattern>``
        # shorthand (it returns ``Model(provider="")`` for a bare
        # ``--model openrouter/gpt-4`` with no separate ``--provider``). Pi's
        # ``resolveModelFromCli`` (main.ts:303-304) splits that form so its
        # ``model.provider`` is always populated, and its guard fires only when
        # NO model resolves at all. When the SettingsManager / resolveModelFromCli
        # port lands, add the ``provider/pattern`` split here so this guard stops
        # rejecting pi-valid invocations. The OpenRouter-from-env path already
        # populates ``provider``, so the common case works.
        if not model.provider:
            print(
                "Error: --api-key requires a model to be specified via "
                "--model, --provider/--model, or --models",
                file=sys.stderr,
            )
            return 1
        auth_storage.set_runtime_api_key(model.provider, parsed.api_key)
        get_api_key_and_headers = _make_auth_callback(model_registry)

    if parsed.models:
        print(
            "Warning: --models (scoped models) is not yet implemented; the "
            "patterns were ignored.",
            file=sys.stderr,
        )

    # Sprint 6h₈ §D: ``--continue`` / ``-c`` auto-resume short-circuit.
    # When set, attempt to open the most-recent session in cwd; if none
    # exist, fall back to ``_build_session`` silently (Pi parity per
    # ``main.ts:280-281`` ``SessionManager.continueRecent`` semantics).
    # ``--session`` / ``--fork`` resolution failures surface as a startup
    # diagnostic rather than a traceback.
    try:
        if parsed.continue_session:
            most_recent = await repo.find_most_recent(cwd)
            if most_recent is not None:
                session = await repo.open(most_recent)
            else:
                session = await _build_session(parsed, repo, fs, cwd)
        else:
            session = await _build_session(parsed, repo, fs, cwd)
    except SessionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

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
            parsed,
            new_session,
            mcp_tools=mcp_tools,
            get_api_key_and_headers=get_api_key_and_headers,
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
