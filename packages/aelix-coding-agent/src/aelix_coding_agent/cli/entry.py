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
import select
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.skills import load_skills
from aelix_agent_core.runtime import ReloadSeed
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
from aelix_coding_agent.builtin.permission_mode import PermissionPosture
from aelix_coding_agent.core.runnable_models import is_runnable, unsupported_message
from aelix_coding_agent.extensions.loader import (
    discover_and_load_extensions,
    scan_extension_manifests,
)
from aelix_coding_agent.mcp import McpClientManager
from aelix_coding_agent.tools import create_all_tools

from .agent_context import build_system_prompt, discover_context_files
from .args import Args, parse_args, print_help
from .auth_guidance import (
    format_no_api_key_found_message,
    format_no_model_selected_message,
)
from .config import (
    CONFIG_DIR_NAME,
    VERSION,
    get_agent_dir,
    get_session_dir,
    load_mcp_server_contribs,
)
from .file_processor import process_file_arguments
from .initial_message import build_initial_message
from .project_trust import (
    DefaultProjectTrust,
    ProjectTrustPromptResult,
    ProjectTrustStore,
    format_project_trust_prompt,
    has_trust_requiring_project_resources,
    interpret_trust_option,
    project_trust_options,
    resolve_project_trusted,
)
from .runtime_bootstrap import (
    enrich_copilot_base_url,
    load_dotenv,
    register_providers,
    resolve_model,
)

if TYPE_CHECKING:
    from aelix_ai.settings import SettingsManager
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
    """Pi parity: ``readPipedStdin`` — plus an aelix-original hang guard.

    Returns :data:`None` when stdin is a TTY (interactive shell). When
    stdin is piped (file redirect, here-doc, etc.), reads the full
    payload and strips surrounding whitespace; empty content → :data:`None`.

    Issue #57 (aelix-original hardening — pi DECLINED the same report,
    pi#5571, workaround ``</dev/null``): the read-to-EOF used to block
    forever on a non-TTY pipe whose writer never closes, and the path is
    reachable with ZERO flags (any piped stdin promotes ``app_mode`` to
    ``"print"`` in :func:`resolve_app_mode`). On POSIX we now wait for
    FIRST-byte readiness under a deadline (default 30s;
    ``AELIX_STDIN_TIMEOUT`` overrides, ``0`` waits forever); on timeout we
    warn on stderr and proceed WITHOUT stdin input. ``select`` runs in a
    worker thread but always returns at the deadline, so no thread leaks
    (a bare ``wait_for`` around the blocking read would strand the reader
    thread — the OS read is uncancellable). Once data/EOF is ready, the
    read-to-EOF itself is unbounded: a producer that writes a byte and
    never closes still hangs, matching pi (pathological, user-error
    territory). Windows keeps the previous blocking read — ``select`` is
    socket-only there. A stdin without a real fd (pytest capture,
    embedders) skips the readiness gate and reads directly.
    """

    if sys.stdin.isatty():
        return None
    if sys.platform != "win32":
        timeout = _env_float("AELIX_STDIN_TIMEOUT")
        if timeout is None:
            timeout = 30.0
        stdin_fd: int | None
        try:
            stdin_fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            stdin_fd = None  # fake/captured stdin — nothing selectable
        if timeout > 0 and stdin_fd is not None:
            try:
                ready, _, _ = await asyncio.to_thread(
                    select.select, [stdin_fd], [], [], timeout
                )
            except (ValueError, OverflowError, OSError):
                # Fail OPEN to the pre-guard blocking read (adversarial-review
                # LOW x2): ``select`` rejects fds >= FD_SETSIZE (a replaced
                # stdin in a many-fd embedder), closed fds (EBADF), and
                # inf/huge timeouts (time_t OverflowError). Crashing any of
                # these would regress a path that used to work — and an
                # unbounded wait is exactly the old behavior AND the caller's
                # intent for an inf-like timeout.
                ready = True
            if not ready:
                # suppress: a DEAD STDERR must not abort a healthy run — an
                # unguarded warning print here raised BrokenPipeError, which
                # main_sync would misclassify as stdout death (exit 141) and
                # devnull the LIVE stdout (adversarial-review LOW).
                with contextlib.suppress(OSError):
                    print(
                        f"aelix: no data on piped stdin after {timeout:g}s; "
                        "proceeding without stdin input (redirect </dev/null "
                        "if none was intended, or set AELIX_STDIN_TIMEOUT=0 "
                        "to wait indefinitely)",
                        file=sys.stderr,
                    )
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


def _validate_resume_flag(parsed: Args) -> str | None:
    """``--resume`` argument-compatibility validation (mirrors --continue).

    ``--resume`` is a session SOURCE, so it is mutually exclusive with the
    other source flags. Returns a Pi-shape error message, or :data:`None`
    when the combination is OK.
    """

    if not parsed.resume:
        return None
    if parsed.no_session:
        return "--resume is incompatible with --no-session"
    if parsed.session is not None:
        return "--resume is incompatible with --session"
    if parsed.fork is not None:
        return "--resume is incompatible with --fork"
    if parsed.continue_session:
        return "--resume is incompatible with --continue"
    return None


def _resume_choice_label(meta: object) -> str:
    """A one-line picker label for the startup ``--resume`` menu.

    ``JsonlSessionMetadata`` carries id + created_at (no title / message
    count), so the label is ``{created} · {short-id}`` — same shape as the
    in-session ``/resume`` picker (``tui/shell.py`` ``_format_session_choice``).
    Defensive getattr — never raises on an odd metadata shape.
    """

    short_id = (getattr(meta, "id", "") or "")[:8]
    created = (getattr(meta, "created_at", "") or "").replace("T", " ")[:16]
    if created and short_id:
        return f"{created} · {short_id}"
    return created or short_id or "session"


def _read_resume_line() -> str:
    """Read one line of picker input from stdin (indirection for tests)."""

    return input()


async def _prompt_resume_choice(
    sessions: list[JsonlSessionMetadata],
) -> JsonlSessionMetadata | None:
    """Render the startup ``--resume`` picker and return the chosen session.

    The menu + prompt go to STDERR (stdout stays clean); the selection is read
    from stdin off the event loop. An empty line, EOF (Ctrl-D), a non-number,
    or an out-of-range choice all return :data:`None` — the caller then starts
    a fresh session. ``sessions`` is newest-first (``repo.list`` order).
    """

    lines = ["Resume which session? (newest first)"]
    for idx, meta in enumerate(sessions, start=1):
        lines.append(f"  [{idx}] {_resume_choice_label(meta)}")
    lines.append("Enter a number, or press Enter to start a new session: ")
    print("\n".join(lines), file=sys.stderr)

    try:
        loop = asyncio.get_running_loop()
        raw = (await loop.run_in_executor(None, _read_resume_line)).strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return None
    if not raw:
        return None
    try:
        choice = int(raw)
    except ValueError:
        print(f"'{raw}' is not a number; starting a new session.", file=sys.stderr)
        return None
    if 1 <= choice <= len(sessions):
        return sessions[choice - 1]
    print(f"{choice} is out of range; starting a new session.", file=sys.stderr)
    return None


async def _resume_session_startup(
    parsed: Args, repo: JsonlSessionRepo, fs: LocalFileSystem, cwd: str
) -> Session:
    """Resolve the ``--resume`` session at startup (Issue #28).

    - ``--resume <id>`` → resolve the id/prefix (reusing the ``--session``
      resolver) and open it; a miss raises :class:`SessionError` (``not_found``)
      which the caller surfaces as a clean startup diagnostic.
    - ``--resume`` (no id) → an interactive picker over the cwd's sessions.
      The caller guarantees this branch is only reached in interactive mode
      (a picker needs a TTY). No sessions, or a cancelled/invalid pick, starts
      a fresh session.
    """

    if parsed.resume_id is not None:
        meta = await _resolve_session_metadata(repo, fs, parsed.resume_id, cwd)
        if meta is None:
            raise SessionError(
                "not_found", f"No session matching --resume {parsed.resume_id!r}"
            )
        return await repo.open(meta, cwd_override=cwd)

    sessions = await repo.list(JsonlSessionListOptions(cwd=cwd))
    if not sessions:
        print(
            "No previous sessions in this folder; starting a new one.",
            file=sys.stderr,
        )
        return await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    chosen = await _prompt_resume_choice(sessions)
    if chosen is None:
        return await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    return await repo.open(chosen, cwd_override=cwd)


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


def _env_float(name: str) -> float | None:
    """Read a non-negative float from the environment (issue #11).

    Returns ``None`` when unset or unparseable so the tool factory falls back
    to its own default. ``.env`` values are already loaded into ``os.environ``
    by :mod:`runtime_bootstrap`, so this picks up both real env and ``.env``.
    """

    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _tool_options_from_env() -> dict[str, dict[str, float]]:
    """Build the per-tool ``options`` for :func:`create_all_tools` from env vars
    (issue #11). Only keys with a configured value are included, so each tool
    keeps its own module default otherwise.

    - ``AELIX_BASH_DEFAULT_TIMEOUT`` / ``AELIX_BASH_MAX_TIMEOUT`` → bash
      (0 disables the default / lifts the cap respectively).
    - ``AELIX_TOOL_SEARCH_TIMEOUT`` → grep + find subprocess timeout.
    """

    options: dict[str, dict[str, float]] = {}
    bash: dict[str, float] = {}
    default_timeout = _env_float("AELIX_BASH_DEFAULT_TIMEOUT")
    if default_timeout is not None:
        bash["default_timeout"] = default_timeout
    max_timeout = _env_float("AELIX_BASH_MAX_TIMEOUT")
    if max_timeout is not None:
        bash["max_timeout"] = max_timeout
    if bash:
        options["bash"] = bash
    search_timeout = _env_float("AELIX_TOOL_SEARCH_TIMEOUT")
    if search_timeout is not None and search_timeout > 0:
        options["grep"] = {"timeout": search_timeout}
        options["find"] = {"timeout": search_timeout}
    return options


def _resolve_skill_dirs(
    parsed: Args, cwd: str, project_trusted: bool
) -> list[str | Path]:
    """Compose the skill directories to scan (issue #12).

    - Explicit ``--skill <path>`` entries are always included (resolved against
      ``cwd`` when relative). Aelix has no skill package-manager, so ``--skill``
      is a path to a skill directory (or a ``SKILL.md`` whose parent is
      scanned) rather than an installable name.
    - Unless ``--no-skills`` is set, the global agent skills dir
      (``~/.aelix/agent/skills``) is scanned, plus the project-local
      ``<cwd>/.aelix/skills`` ONLY when the project is trusted — a malicious
      project ``SKILL.md`` is a prompt-injection vector once skills reach the
      model, so it is gated like project-local extensions/MCP.

    Missing directories are silently skipped by :func:`load_skills`.
    """

    dirs: list[str | Path] = []
    for entry in parsed.skills:
        path = Path(entry)
        if not path.is_absolute():
            path = Path(cwd) / path
        if path.name == "SKILL.md":
            path = path.parent
        dirs.append(str(path))
    if not parsed.no_skills:
        dirs.append(str(Path(get_agent_dir()) / "skills"))
        if project_trusted:
            dirs.append(str(Path(cwd) / CONFIG_DIR_NAME / "skills"))
    return dirs


async def _build_harness_options(
    parsed: Args,
    session: Session,
    *,
    mcp_tools: list[AgentTool] | None = None,
    get_api_key_and_headers: Callable[..., Any] | None = None,
    project_trusted: bool = True,
    permission_ext: PermissionExtension | None = None,
    captured_extensions: list[Any] | None = None,
    settings_manager: SettingsManager | None = None,
    flag_values: Mapping[str, bool | str] | None = None,
    on_reload: bool = False,
    model_registry: Any | None = None,
    default_provider: str | None = None,
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
    # ``model_registry`` is threaded so a models.json custom provider — invisible
    # to the build-time catalog — resolves its real ``api`` instead of the
    # ``"unknown"`` that raises at the first turn (#98). Extension
    # ``register_provider`` models are NOT yet visible here: they only land on
    # the registry at ``bind_model_registry``, after the harness is built, so the
    # post-build ``is_runnable`` gate is what reports those.
    # ``default_provider`` carries settings.json ``defaultProvider`` as a VALUE
    # rather than being read from ``settings_manager`` here: a per-call read is
    # free to drift between the three resolve sites, and it must reach
    # ``resolve_model`` as its own argument (never merged into ``parsed.provider``)
    # or it impersonates an explicit ``--provider`` and hijacks the
    # ``<provider>/<model>`` shorthand + the OpenRouter-env path (#98).
    # ``enrich_copilot_base_url`` adopts the registry's modify_models-injected
    # proxy-ep base_url for github-copilot (the enterprise/business host), which
    # ``resolve_model``/``get_model`` leaves at the static individual default.
    model = enrich_copilot_base_url(
        resolve_model(parsed.model, parsed.provider, model_registry, default_provider),
        model_registry,
    )
    # Resolve to an absolute path so the tool sandbox root + AGENTS.md anchor are
    # stable even if something later chdir's the process (e.g. a bash-tool ``cd``).
    cwd = str(Path.cwd())

    # Sprint 6h₁₁: wire the coding toolset + a real coding-agent system prompt.
    # Previously the harness ran with EMPTY tools + EMPTY system prompt (a bare
    # chat model with no identity and no ability to touch files). The 7 built-in
    # tools (read/write/edit/bash/grep/find/ls) + the base prompt make it an
    # actual coding agent. An explicit ``--system-prompt`` still overrides.
    tools = list(create_all_tools(cwd, _tool_options_from_env()).values())
    # MCP (Tier 4) tools, connected once in _async_main and shared across
    # harness rebuilds, join the built-in toolset (``<server>__<tool>`` names).
    if mcp_tools:
        tools.extend(mcp_tools)
    # --tools / --no-tools gating (Pi ``main.ts:369-375``).
    #
    # Issue #24-FU (adversarial-review MEDIUM): on RELOAD, do NOT re-apply the raw
    # ``--tools`` filter through the harness's RAISING active-tool validator. If
    # ``--tools`` named an extension tool whose extension was since removed,
    # ``_action_set_active_tools`` raises inside ``AgentHarness`` construction
    # (core.py) — and reload() has ALREADY disposed the old harness by then, so the
    # raise would BRICK the session (not a clean error). Pi's reload never crashes
    # here because it re-seeds the LIVE ``getActiveToolNames()`` and
    # ``setActiveToolsByName`` SILENTLY filters unknown names. aelix instead builds
    # the reloaded harness UNFILTERED (all tools active) and lets
    # ``AgentSessionRuntime.reload()`` step-6 restore the pre-reload filter,
    # intersected with the rebuilt registry (dropping the removed name) + the
    # extension-tool union. ``_resolve_active_tools`` still applies on first build /
    # /new / /fork / /resume.
    active_tool_names = None if on_reload else _resolve_active_tools(parsed)
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
    #
    # Project Trust gate (Sprint P0 #10): when ``project_trusted`` is False the
    # auto-discovered ``cwd/.aelix/extensions/`` tier is SUPPRESSED
    # (``no_project_local=True``) so untrusted project-local .py is never
    # exec_module'd — but explicit ``-e`` paths and the global tier still load
    # (they are user-chosen, not project-local). The trust decision is resolved
    # in ``_async_main`` BEFORE this factory runs, so the gate precedes any
    # project-local code execution.
    # Hold-the-ref (WP-0 STEP 3, ADR-0157): the ONE ``permission_ext`` built in
    # ``_async_main`` is threaded in so the posture + ``_session_allows`` survive
    # ``/resume`` / ``/new`` / ``/fork`` rebuilds — a security requirement (a
    # fresh per-rebuild instance would silently reset posture to DEFAULT AND lose
    # the session-approve set). The Guardrail stays FIRST so its hard-deny
    # patterns short-circuit BEFORE the permission gate (first-block-wins); DO
    # NOT reorder — YOLO posture relies on Guardrail running first.
    permission = permission_ext if permission_ext is not None else PermissionExtension()
    loaded = await discover_and_load_extensions(
        [str(p) for p in parsed.extensions],
        cwd=Path(cwd),
        agent_dir=Path(get_agent_dir()),
        prepend=[GuardrailExtension(), permission],
        no_discovery=parsed.no_extensions,
        no_project_local=not project_trusted,
        # Issue #24-FU — on reload, carry the user's restored extension flag
        # values into the fresh runtime BEFORE each ``setup()`` re-runs (``None``
        # on first build / /new / /fork / /resume, where fresh defaults are
        # correct). See :class:`ReloadSeed`.
        flag_values=flag_values,
    )
    for err in loaded.errors:
        print(f"Warning: extension load: {err}", file=sys.stderr)
    # WP-8 (Feature 3) — capture the discovered extensions ONCE for the TUI's
    # /extension viewer. ``discover_and_load_extensions`` runs per harness build
    # (it is called here, inside the factory), so the caller passes a mutable
    # holder and reads back the list AFTER the first build. The list is stable
    # across rebuilds (same on-disk set); a fresh holder is repopulated each
    # build, which is harmless (the TUI captured the first one).
    if captured_extensions is not None:
        captured_extensions.clear()
        captured_extensions.extend(loaded.extensions)
    # Seed the message-queue modes from persisted settings so a ``/settings``
    # steering / follow-up change SURVIVES restart (and reaches /new / /fork /
    # /resume). Both had get/set pairs on SettingsManager but no startup
    # consumer, so the harness always booted the AgentHarnessOptions default
    # ("one-at-a-time") and every persisted change silently reverted on relaunch
    # — unlike theme / thinking-level / default-model, which are all seeded.
    # The getter returns "one-at-a-time" when unset, matching the dataclass
    # default, so no-SettingsManager / unset stays behaviourally unchanged.
    steering_mode = "one-at-a-time"
    follow_up_mode = "one-at-a-time"
    if settings_manager is not None:
        steering_mode = settings_manager.get_steering_mode()
        follow_up_mode = settings_manager.get_follow_up_mode()
    options = AgentHarnessOptions(
        model=model,
        session=session,
        cwd=cwd,
        tools=tools,
        system_prompt=system_prompt,
        extensions=loaded.extensions,
        runtime=loaded.runtime,
        active_tool_names=active_tool_names,
        steering_mode=steering_mode,
        follow_up_mode=follow_up_mode,
        get_api_key_and_headers=get_api_key_and_headers,
        # Issue #5 (Lane C): surface the resolved trust state to extensions via
        # ``ctx.is_project_trusted()``.
        project_trusted=project_trusted,
        # Issue #44 — thread the ONE startup ``SettingsManager`` (built at
        # ``_async_main`` and shared with the TUI) into the harness so
        # ``harness.settings_manager`` is non-None and ``harness.reload()`` stops
        # raising ``invalid_state`` (core.py guard). The aelix-agent-core seam
        # (field/threading/property/reload) already exists (commit 4659a99); this
        # is the dormant coding-agent glue mirroring pi ``main.ts`` constructing
        # the AgentSession with its ``settingsManager``. Pure threading: no
        # production caller invokes ``reload()`` yet (TUI/CLI ``/reload`` calls
        # ``reload_resources()``), so this changes no observable behavior until
        # the moat-chain reload (#24) consumes it. Same hold-the-ref pattern as
        # ``permission_ext`` / ``model_registry`` — one shared instance reaches
        # every rebuild so reload survives ``/new`` / ``/fork`` / ``/resume``.
        settings_manager=settings_manager,
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


async def _prompt_project_trust_interactive(
    cwd: Path,
) -> ProjectTrustPromptResult | None:
    """A1 seam (Sprint P0 #10) — one-shot pre-``run_tui`` trust selector.

    The bootstrap-order tension (spec §2.6): extensions load inside the
    harness factory and MCP connects BEFORE ``run_tui`` builds its chrome, so
    the trust decision must be made before any project-local code runs — but
    the persistent TUI's ``ctx.ui.select`` is not yet bound. A1 resolves this
    with a tiny DEDICATED ``prompt_toolkit.Application`` (a one-shot full-screen
    selector) that runs to completion and returns BEFORE the harness factory /
    MCP connect, so the gate strictly precedes execution.

    Returns the user's :class:`ProjectTrustPromptResult`, or :data:`None` on
    Esc / Ctrl+C (→ deny). Returns :data:`None` if the ``[tui]`` extra is
    missing (prompt-toolkit unavailable) — the caller then denies by default,
    which is the safe direction.
    """

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except ImportError:
        # No TUI extra → cannot prompt; caller denies by default.
        return None

    options = project_trust_options(cwd)
    state = {"idx": 0}

    body = format_project_trust_prompt(cwd)

    def _render() -> str:
        rows = [body, ""]
        for i, label in enumerate(options):
            marker = "→ " if i == state["idx"] else "  "
            rows.append(f"{marker}{label}")
        rows.append("")
        rows.append("↑/↓ to move · Enter to choose · Esc to cancel")
        return "\n".join(rows)

    kb = KeyBindings()
    chosen: dict[str, str | None] = {"label": None}

    @kb.add("up")
    def _up(_e: object) -> None:
        state["idx"] = (state["idx"] - 1) % len(options)

    @kb.add("down")
    def _down(_e: object) -> None:
        state["idx"] = (state["idx"] + 1) % len(options)

    @kb.add("enter")
    @kb.add("c-j")
    def _enter(event: Any) -> None:
        chosen["label"] = options[state["idx"]]
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event: Any) -> None:
        chosen["label"] = None
        event.app.exit()

    app: Any = Application(
        layout=Layout(
            Window(FormattedTextControl(_render, focusable=True, key_bindings=kb))
        ),
        full_screen=False,
    )
    await app.run_async()

    label = chosen["label"]
    if label is None:
        return None
    return interpret_trust_option(label, cwd)


async def _resolve_project_trust(
    parsed: Args,
    cwd: str,
    app_mode: AppMode,
    *,
    extensions: list[Any] | None = None,
    default_project_trust: DefaultProjectTrust = "ask",
) -> bool:
    """Resolve project trust ONCE, before any project-local code executes.

    ``has_ui`` is True only in interactive mode (print/json/rpc cannot prompt →
    deny-by-default). The interactive prompt is the A1 one-shot selector. On a
    denied + non-interactive run with resources present, prints a clear stderr
    notice (replacing the old post-hoc warning).

    Issue #5 bootstrap: ``extensions`` is the USER/GLOBAL-only vote surface (NEVER
    project-local — those are what's being gated) loaded before this call, and
    ``default_project_trust`` is the persisted global setting. Threading both makes
    ``resolve_project_trusted``'s ``project_trust`` extension event (step 3) fire
    and its ``defaultProjectTrust`` branch (step 5) take effect — both were inert
    in the shipped CLI while this caller omitted them.
    """

    cwd_path = Path(cwd)
    has_ui = app_mode == "interactive"
    trusted = await resolve_project_trusted(
        cwd_path,
        override=parsed.project_trust_override,
        has_ui=has_ui,
        prompt=_prompt_project_trust_interactive if has_ui else None,
        store=ProjectTrustStore(get_agent_dir()),
        extensions=extensions,
        default_project_trust=default_project_trust,
        on_extension_error=lambda msg: print(
            f"Warning: project_trust extension: {msg}", file=sys.stderr
        ),
    )
    return trusted


async def _async_main(argv: list[str]) -> int:
    """Pi parity: ``main()`` body (``main.ts:423-716`` reduced for scope)."""

    # Issue #19 (ADR-0185) / #32-A (ADR-0186) — ``aelix extension <subcommand>``
    # verb dispatch, BEFORE parse_args: the hand-rolled flat flag parser would
    # swallow ``extension``/``install`` as chat-prompt positionals. A
    # do-a-thing-and-exit action, in the spirit of the ``--list-models`` /
    # ``--export`` early exits. Awaits the ASYNC dispatch directly (we are
    # already inside the ``asyncio.run`` loop — a nested ``asyncio.run`` shim
    # would raise) so the marketplace subcommands can ``await`` the async
    # settings-write flush that persists ``extension_sources``.
    if argv and argv[0] == "extension":
        from aelix_coding_agent.cli.extension_install import (
            run_extension_command_async,
        )

        return await run_extension_command_async(argv[1:])

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

    resume_error = _validate_resume_flag(parsed)
    if resume_error is not None:
        print(f"Error: {resume_error}", file=sys.stderr)
        return 1

    # === --list-models — Sprint 6h₇a (ADR-0090) wired =======================
    if parsed.list_models is not None:
        # Lazy imports — defer ``ModelRegistry`` + ``AuthStorage``
        # construction cost off the ``--version`` / ``--help`` fast paths
        # (~10ms saved on cold start). Hoisting these to module scope
        # would trigger auth file I/O on every invocation. (``Path`` is a
        # zero-cost stdlib import and lives at module scope.)
        from aelix_ai.oauth import AuthStorage
        from aelix_ai.settings import SettingsManager

        from ..model_registry import ModelRegistry
        from .list_models import list_models

        auth_storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
        await auth_storage.load()
        model_registry = ModelRegistry.create(auth_storage)
        # ADR-0162: scope --list-models to the persisted enabled_models
        # allow-list for parity with the /model picker. MUST pass
        # ``agent_dir=get_agent_dir()`` (same as the main path at ~684) so both
        # read the same settings.json. An empty-match list degrades to all.
        list_settings = SettingsManager.create(
            cwd=str(Path.cwd()), agent_dir=Path(get_agent_dir())
        )
        await list_models(model_registry, parsed.list_models, list_settings)
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

    # Issue #28 — a no-id ``--resume`` is an interactive picker, which needs a
    # TTY. In print/json/rpc it is a clean argument error (never a traceback,
    # and NOT a silent most-recent open — that is ``--continue``). Checked here,
    # BEFORE any stdin is read, so it fails fast.
    if parsed.resume and parsed.resume_id is None and app_mode != "interactive":
        print(
            "Error: --resume without a session id needs an interactive "
            "terminal; pass --resume <id>, or use --continue.",
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

    # === SettingsManager (WP-2, ADR-0160) — constructed ONCE, threaded to the TUI
    # as a PURE CONSUMER (construct via the factory + call the existing
    # get_*/set_*/flush API; no edit to the pi-parity-pinned aelix_ai settings).
    # MUST pass ``agent_dir=get_agent_dir()`` explicitly: the create() default is
    # XDG ~/.config/aelix, which would split settings.json from the agent's
    # auth.json/mcp.json (open-risk: path divergence). ``create`` is synchronous +
    # side-effect-free on read (load errors are captured into drain_errors, never
    # raised). Surface any load errors as a startup warning (MCP/extension parity).
    from aelix_ai.settings import SettingsManager

    settings_manager = SettingsManager.create(
        cwd=str(Path.cwd()), agent_dir=Path(get_agent_dir())
    )
    for setting_err in settings_manager.drain_errors():
        print(
            f"Warning: settings ({setting_err.scope}): {setting_err.error}",
            file=sys.stderr,
        )

    # WP-2 (ADR-0160) — seed the startup model from the PERSISTED default when the
    # user passed NO ``--model``/``--provider`` flag. This is what makes the
    # /settings → "Default model" choice actually apply on the next launch (not
    # only the session that set it). The explicit flags always win (pi parity:
    # CLI > settings); we only fill the gap. Mutating ``parsed`` here means EVERY
    # downstream ``resolve_model(parsed.model, parsed.provider)`` (the harness
    # build + the --api-key guard + the print/json no-model guard) inherits the
    # default uniformly — no per-call seeding to drift. Guarded so a malformed
    # settings file never blocks launch.
    #
    # ``defaultModel`` + ``defaultProvider`` are ONE PAIR, written together by
    # /model and /settings (pi parity: setModel → setDefaultModelAndProvider) to
    # name a single chosen model. They are therefore seeded as a UNIT, under the
    # both-flags-absent condition: the pair describes a model the user really
    # picked, so its provider half rightly behaves like an explicit choice (it
    # outranks the OpenRouter-from-env path, whose id would otherwise replace the
    # persisted model).
    #
    # #98 is what happens when that pair is SPLIT — ``--model <id>`` supplied with
    # no ``--provider``. The condition above then suppresses the provider half
    # entirely, leaving an empty provider that resolves to api="unknown" and
    # raises at the first turn. But the persisted provider ALSO cannot simply be
    # written into ``parsed.provider``: it is now a leftover from a DIFFERENT
    # model than the one being requested, and ``resolve_model`` reads
    # ``provider_flag`` as "the user explicitly named this provider" — gating both
    # the ``<provider>/<model>`` shorthand and the OpenRouter-env path on its
    # absence. Impersonating the flag hijacks both and silently reroutes the turn
    # to the persisted vendor. So the split case hands the value to
    # ``resolve_model`` as its own lowest-precedence argument instead.
    #
    # The MIRROR split (``--provider`` with no ``--model``) deliberately does NOT
    # inherit ``defaultModel``: seeding ``parsed.model`` unconditionally would
    # override ``OPENROUTER_DEFAULT_MODEL`` for anyone running
    # ``--provider openrouter``, sending the persisted id of some other vendor's
    # model to OpenRouter. Filling that gap needs a ``default_model`` rung on
    # ``resolve_model`` (the OpenRouter branch picks the id BEFORE any settings
    # value is consultable); until then it stays unfilled and the is_runnable
    # gate below reports it.
    default_provider: str | None = None
    with contextlib.suppress(Exception):
        default_model = settings_manager.get_default_model()
        persisted_provider = settings_manager.get_default_provider()
        if parsed.model is None and parsed.provider is None:
            if default_model:
                parsed.model = default_model
            if persisted_provider:
                parsed.provider = persisted_provider
        elif parsed.provider is None and persisted_provider:
            default_provider = persisted_provider

    # === Permission posture (WP-0, ADR-0157) — built ONCE, held by reference ===
    # The shift+tab-cycled posture + the PermissionExtension are constructed here
    # and threaded into EVERY harness rebuild via the factory closure (mirror of
    # ``model_registry`` / ``mcp_tools``). This is a security requirement: a
    # fresh per-rebuild PermissionExtension would silently reset the posture to
    # DEFAULT and lose the session-approve set across ``/resume`` / ``/new`` /
    # ``/fork``. The same ``posture`` object is also passed to ``run_tui`` so a
    # shift+tab cycle and the gate read/write the SAME holder.
    permission_posture = PermissionPosture()
    permission_ext = PermissionExtension(posture=permission_posture)

    # ALWAYS wire the auth callback so credentials stored in auth.json (via
    # ``/login``) AND models.json provider ``apiKey`` entries resolve at runtime —
    # NOT only when ``--api-key`` is passed. (Previously this was gated behind
    # ``--api-key``, so a ``/login``-stored key or a custom models.json provider
    # was never consulted by the harness — it fell through to env vars only, which
    # is why a custom provider like ``openwebui`` failed with "No API key for
    # provider". WP-8 follow-up.) ``_make_auth_callback`` returns "no opinion"
    # (``None``) for a provider with no stored key, so env-only providers keep
    # working via the adapter's ``get_env_api_key`` fallback.
    get_api_key_and_headers: Callable[..., Any] | None = _make_auth_callback(
        model_registry
    )
    if parsed.api_key is not None:
        # Pi parity (main.ts:574-582): ``--api-key`` is meaningless without a
        # model whose provider we can attach the runtime key to. It adds a
        # RUNTIME OVERRIDE layer (highest cascade precedence) on top of the
        # always-wired callback above.
        model = resolve_model(
            parsed.model, parsed.provider, model_registry, default_provider
        )
        # ``resolve_model`` now parses the ``<provider>/<model>`` slash shorthand
        # (Pi ``resolveModelFromCli`` main.ts:303-304) and enriches from the
        # catalog, so ``model.provider`` is populated for every pi-valid
        # invocation (``--provider x --model y``, ``--model x/y``, or the
        # OpenRouter-from-env path). This guard now fires only when NO model
        # resolves at all — an empty/unknown provider — matching pi.
        # The registry + settings default are passed so the provider this run
        # will REALLY use is the one the runtime key gets attached to: without
        # them a registry-only provider or a persisted default resolved empty
        # here and rejected an ``--api-key`` the run could have used (#98).
        if not model.provider:
            print(
                "Error: --api-key requires a model to be specified via "
                "--model, --provider/--model, or --models",
                file=sys.stderr,
            )
            return 1
        auth_storage.set_runtime_api_key(model.provider, parsed.api_key)
        # (the auth callback is already wired above — the runtime override now
        # takes precedence in the cascade.)

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
        if parsed.resume:
            # Issue #28 — startup ``--resume`` (id-open, or interactive picker
            # for the no-id case). The no-id-in-non-interactive guard already
            # ran above, before any stdin was read.
            session = await _resume_session_startup(parsed, repo, fs, cwd)
        elif parsed.continue_session:
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

    # === Project Trust gate (Sprint P0 #10) — resolve ONCE, BEFORE any =======
    # project-local code executes (MCP subprocess spawn + extension
    # exec_module). The interactive prompt is the A1 one-shot selector; print/
    # json/rpc cannot prompt → deny-by-default. The resolved bool gates BOTH
    # the project-local MCP contribs (below) AND the auto-discovered
    # ``cwd/.aelix/extensions`` tier (threaded into ``_build_harness_options``).
    #
    # Issue #5 — load the USER/GLOBAL-only extension surface as the project_trust
    # VOTE surface BEFORE resolving trust (so the ``project_trust`` event can fire).
    # SECURITY: ``no_project_local=True`` so an untrusted ``cwd/.aelix/extensions``
    # is NEVER exec_module'd before the gate; NO ``prepend`` built-ins (Guardrail /
    # permission_ext have no project_trust handler and the held-ref permission_ext
    # must be instantiated exactly once, by the factory). This is a THROWAWAY load
    # (de-dup OPTION B, ADR-pending): its fresh runtime is bound to nothing, so a
    # vote extension's ``register_provider`` only QUEUES onto a discarded runtime
    # and is never applied — the factory re-discovers + binds the real set later.
    # Cost: user/global ``setup()`` side-effects run twice (documented; OPTION A
    # reuse is a deferred efficiency refinement that would collide with the factory).
    trust_vote_extensions: list[Any] = []
    # Only pay for the (expensive) vote-load when the orchestrator will actually
    # consult it — the ``project_trust`` event (step 3) is UNREACHABLE when an
    # override is set (``--approve``/``--no-approve`` short-circuits at step 1) or
    # the cwd has no trust-requiring resources (step 2 returns trusted). Gating on
    # the SAME predicate the orchestrator uses is behavior-identical and keeps the
    # full user/global extension load (+ its ``setup()``) off the common startup
    # path (most directories have no ``.aelix/extensions``/``.aelix/mcp.json``).
    if parsed.project_trust_override is None and has_trust_requiring_project_resources(
        Path(cwd)
    ):
        try:
            _vote_loaded = await discover_and_load_extensions(
                [str(p) for p in parsed.extensions],
                cwd=Path(cwd),
                agent_dir=Path(get_agent_dir()),
                no_discovery=parsed.no_extensions,
                no_project_local=True,  # SECURITY: never the project-local tier
            )
            trust_vote_extensions = list(_vote_loaded.extensions)
            for _err in _vote_loaded.errors:
                print(f"Warning: project_trust vote-load: {_err}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — vote-load failure must not block startup
            print(f"Warning: project_trust vote-load failed: {exc}", file=sys.stderr)

    project_trusted = await _resolve_project_trust(
        parsed,
        cwd,
        app_mode,
        extensions=trust_vote_extensions,
        default_project_trust=settings_manager.get_default_project_trust(),
    )

    # MCP servers (Tier 4): connect ONCE here, share the connected tools across
    # every harness rebuild (the tool closures hold live connections, so they
    # survive rebuilds), and dispose ONCE in the finally block. A failed server
    # warns and is skipped — one bad server never aborts the agent.
    mcp_contribs, mcp_warnings, mcp_source = load_mcp_server_contribs(
        str(Path.cwd())
    )
    for warning in mcp_warnings:
        print(f"Warning: MCP config: {warning}", file=sys.stderr)
    # Project Trust gate: drop ONLY the auto-discovered project-local
    # ``cwd/.aelix/mcp.json`` contribs from an untrusted directory. ``$AELIX_MCP_CONFIG``
    # (``env``) and the user-global config are explicit user choices and are
    # NEVER gated. When dropped in a non-interactive run, print a clear stderr
    # notice (replaces the old post-hoc "loaded N on-disk extensions" warning).
    if mcp_contribs and mcp_source == "project" and not project_trusted:
        print(
            "Notice: project-local .aelix/mcp.json servers skipped in an "
            "untrusted directory; pass --approve to trust.",
            file=sys.stderr,
        )
        mcp_contribs = []
    # Issue #21 (W1) — manifest-declared MCP servers (``contributes.mcp_servers``).
    # A metadata-ONLY scan (no plugin code executes — parses aelix-plugin.toml
    # through the same 4-tier discovery) because MCP connects HERE, before the
    # first harness build where the full extension load runs. Trust is inherited:
    # untrusted project-local plugin dirs are skipped via ``no_project_local``,
    # mirroring the mcp.json project gate above. Ordering: manifest contribs go
    # FIRST so an explicit .aelix/mcp.json entry WINS a server-name collision
    # (McpClientManager keys connections by name, last-wins). W1 limitation
    # (documented, ADR-0181): MCP connects once at startup — a manifest written
    # later takes effect on the next process start, not on /reload.
    try:
        _scanned_manifests = scan_extension_manifests(
            [str(p) for p in parsed.extensions],
            cwd=Path.cwd(),
            agent_dir=Path(get_agent_dir()),
            no_discovery=parsed.no_extensions,
            no_project_local=not project_trusted,
        )
    except Exception as exc:  # noqa: BLE001 — scan is additive, never fatal
        print(f"Warning: manifest scan: {exc}", file=sys.stderr)
        _scanned_manifests = []
    # REVERSED tier order among manifests (adversarial-review LOW): the scan
    # yields project → global → explicit, but McpClientManager is LAST-wins —
    # reversing makes the HIGHER-priority tier (project-local) win a
    # manifest-vs-manifest name collision, while .aelix/mcp.json (appended
    # after) still beats every manifest.
    _manifest_mcp = [
        contrib
        for manifest in reversed(_scanned_manifests)
        for contrib in manifest.contributes.mcp_servers
    ]
    if _manifest_mcp:
        mcp_contribs = _manifest_mcp + mcp_contribs
    mcp_manager: McpClientManager | None = None
    mcp_tools: list[AgentTool] = []
    if mcp_contribs:
        mcp_manager = McpClientManager(mcp_contribs)
        for conn_err in await mcp_manager.connect_all():
            print(f"Warning: MCP server failed: {conn_err}", file=sys.stderr)
        mcp_tools = await mcp_manager.collect_agent_tools()

    # Non-interactive untrusted notice for the EXTENSION surface (the gate
    # itself happens inside the factory via ``no_project_local``). Interactive
    # users already saw/answered the A1 prompt, so only warn for headless runs.
    if (
        not project_trusted
        and app_mode != "interactive"
        and has_trust_requiring_project_resources(Path(cwd))
    ):
        print(
            "Notice: project-local .aelix/extensions skipped in an "
            "untrusted directory; pass --approve to trust.",
            file=sys.stderr,
        )

    # WP-8 (Feature 3) — a stable holder the factory fills with the discovered
    # extensions on the FIRST build so run_tui's /extension viewer gets the live
    # list (default empty when nothing loaded / non-interactive).
    discovered_extensions: list[Any] = []

    # Issue #12: load skills ONCE (the dirs are stable for the process) and
    # re-apply them on every harness build below, so ``harness.skills`` is never
    # empty after a /resume, /new, or /fork rebuild. Diagnostics are emitted
    # here (once) rather than per-rebuild.
    skill_dirs = _resolve_skill_dirs(parsed, cwd, project_trusted)
    skills_result = load_skills(skill_dirs)
    for diag in skills_result.diagnostics:
        print(
            f"Warning: skill load: {diag.message} ({diag.path})",
            file=sys.stderr,
        )

    async def _harness_factory(
        new_session: Session, *, reload_seed: ReloadSeed | None = None
    ) -> AgentHarness:
        opts = await _build_harness_options(
            parsed,
            new_session,
            mcp_tools=mcp_tools,
            get_api_key_and_headers=get_api_key_and_headers,
            project_trusted=project_trusted,
            permission_ext=permission_ext,
            captured_extensions=discovered_extensions,
            model_registry=model_registry,
            # #98 — settings.json ``defaultProvider``, resolved ONCE above and
            # passed as a value so every (re)built harness resolves the turn model
            # from the identical provider ladder.
            default_provider=default_provider,
            # Issue #44 — forward the shared startup SettingsManager (the
            # ``_async_main`` closure var built above) into every (re)built
            # harness so ``harness.reload()`` is functional across /new, /fork,
            # /resume.
            settings_manager=settings_manager,
            # Issue #24-FU — the reload path (AgentSessionRuntime.reload) hands a
            # ReloadSeed carrying the user's prior flag values; pre-seed them into
            # the rebuilt extension runtime BEFORE ``setup()`` re-runs. ``None`` on
            # every non-reload (re)build.
            flag_values=reload_seed.flag_values if reload_seed is not None else None,
            # Issue #24-FU: on reload, build UNFILTERED and defer the active-tool
            # set to reload() step-6 (avoids the raising validator bricking the
            # session when --tools named a since-removed extension tool).
            on_reload=reload_seed is not None,
        )
        harness = AgentHarness(opts)
        # Issue #22 — replay pending provider registrations into the LIVE
        # ModelRegistry. Extensions that call ``ctx.api.register_provider``
        # during setup queue onto ``runtime.pending_provider_registrations``;
        # without this bind they are silently dropped (the runtime defaults to a
        # ``_StubModelRegistry``), so an extension/custom-registered provider
        # never resolves in ``/model`` or at stream time. Pi parity:
        # ``ExtensionRunner.bindCore`` flushes ``runtime.pendingProviderRegistrations``
        # into ``modelRegistry`` and rebinds register/unregister to apply
        # immediately (``runner.ts:344-377``). Aelix threads the registry
        # separately from the harness (see ``run_tui`` docstring on why the
        # harness must NOT hold it), so the bind lands here — the single
        # bootstrap point shared by every mode and re-run on each harness
        # rebuild (a fresh runtime re-queues, so the replay stays correct).
        # ``bind_model_registry`` only replays via ``register_provider`` /
        # ``unregister_provider`` (both present on the concrete registry); the
        # protocol's ``get_models`` is an unimplemented stub member (pi's real
        # ModelRegistry has no ``getModels`` either — only ``getAll`` /
        # ``getAvailable``), so the bind is correct at runtime even though the
        # concrete registry does not structurally satisfy the stub protocol.
        harness.runtime.bind_model_registry(model_registry)  # pyright: ignore[reportArgumentType]
        # Issue #77 — replay queued extension login providers onto the
        # process-global login registry so they appear in the /login method list
        # (guarded: alternate runtimes without the method are a no-op).
        _bind_login = getattr(harness.runtime, "bind_login_registries", None)
        if callable(_bind_login):
            _bind_login()
        # Issue #77 follow-up — replay custom wire-protocol adapters onto the
        # api registry (re-applies after reset_api_providers() on /reload).
        _bind_adapters = getattr(harness.runtime, "bind_api_adapters", None)
        if callable(_bind_adapters):
            _bind_adapters()
        # Re-apply the loaded skills on every (re)build (issue #12).
        harness.set_skills(skills_result.skills)
        return harness

    harness = await _harness_factory(session)
    runtime = await create_agent_session_runtime(
        harness, _harness_factory, repo=repo, fs=fs
    )

    # === Unrunnable-startup-model gate (#98) ===
    # Placed AFTER the harness build so ``bind_model_registry`` has replayed the
    # extension-registered providers onto ``model_registry``: a provider only an
    # extension knows is invisible to the resolve in ``_build_harness_options``,
    # so this is the FIRST point at which every provider source can be judged.
    # Gating on the harness's live ``current_model`` (not a re-resolve) means the
    # verdict is about the model that will really drive turns.
    #
    # Interactive deliberately does NOT refuse to launch: ``/model`` is the
    # in-session cure (it hands the picker's live registry Model straight to
    # ``set_model``), so a loud warning beats a dead end. print/json has no such
    # cure and is refused at its dispatch below.
    #
    # ``is_runnable`` fails OPEN when no api adapter is registered — embedders and
    # tests reach ``_async_main`` without ``register_providers`` (that runs in
    # ``main_sync``), so this stays silent for them rather than warning falsely.
    startup_model = harness.current_model
    if (
        app_mode == "interactive"
        and startup_model is not None
        and not is_runnable(startup_model)
    ):
        print(
            f"Warning: {unsupported_message(startup_model)}\n"
            "         Run /model to select a working model.",
            file=sys.stderr,
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

            return await run_tui(
                runtime,
                cwd=str(Path.cwd()),
                model_registry=model_registry,
                mcp_manager=mcp_manager,
                permission_ext=permission_ext,
                permission_posture=permission_posture,
                settings_manager=settings_manager,
                # WP-8 (Feature 1) — the SAME AuthStorage object the
                # ModelRegistry was built over (line ~680), so /login storing a
                # key is visible to model resolution immediately (no reload).
                auth_storage=auth_storage,
                # WP-8 (Feature 3) — the extensions discovered on the first
                # harness build (empty when none loaded), for /extension.
                extensions=discovered_extensions,
            )

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

        # === No-usable-model guard (ITEM #2, Pi main.ts ``!session.model``) ===
        # Pi aborts a NON-INTERACTIVE run with an auth-guidance message BEFORE
        # the first turn when no usable model is available. Placed at the print/
        # json dispatch (turn time, mirroring Pi's ``!session.model``) AFTER the
        # session build + ``--models`` warning + ``--session`` resolution so none
        # of those paths is shadowed; the ``finally`` still disposes the runtime.
        #
        # ``resolve_model`` is TOTAL (always returns a Model), so the real
        # "unusable" conditions are:
        #   (a) provider empty — a bare ``--model`` with no ``--provider``
        #       and no OpenRouter env: nothing to authenticate against → emit
        #       ``formatNoModelSelectedMessage``; OR
        #   (a·2) provider NON-empty but unresolvable to an ``api`` (#98) → emit
        #       the ``unsupported_message`` reason; OR
        #   (b) provider set but NO API key resolvable for it via the auth cascade
        #       (runtime override / stored / OAuth / env / models.json), checked
        #       sync via ``ModelRegistry.has_configured_auth`` (P0 #7 Wave 1
        #       registry reuse) → emit ``formatNoApiKeyFoundMessage(provider)``.
        # When ``--api-key`` was supplied it already set a runtime override above
        # (so has_configured_auth is True) AND owns the empty-provider diagnostic,
        # so condition (a) cannot wrongly fire for it.
        if app_mode in ("print", "json"):
            turn_model = resolve_model(
                parsed.model, parsed.provider, model_registry, default_provider
            )
            if not turn_model.provider:
                print(format_no_model_selected_message(), file=sys.stderr)
                return 1
            # (a·2) #98 — provider set but nothing could name an ``api`` for it
            # (an uncatalogued models.json custom, an extension-registered
            # provider, or a plain typo). Such a provider is NON-EMPTY, so the
            # emptiness check above cannot see it and the run reached the raw
            # "No provider registered for api='unknown'" at the first turn.
            # Checked before auth: no key fixes a missing adapter. Fails OPEN
            # when no adapter is registered, so embedders keep their behaviour.
            if not is_runnable(turn_model):
                print(f"Error: {unsupported_message(turn_model)}", file=sys.stderr)
                return 1
            if not model_registry.has_configured_auth(turn_model):
                provider_display = model_registry.get_provider_display_name(
                    turn_model.provider
                )
                print(
                    format_no_api_key_found_message(provider_display),
                    file=sys.stderr,
                )
                return 1

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


def _stdout_to_devnull() -> None:
    """Point the stdout fd at devnull (issue #57 EPIPE hygiene).

    After a BrokenPipeError the text buffer may still hold undeliverable
    bytes; the interpreter's shutdown flush would re-raise into
    "Exception ignored in ... BrokenPipeError" noise (exit 120). Rewiring
    the fd makes that flush a harmless no-op. Best-effort: a stdout with
    no real fd (embedders, pytest capture) just skips.
    """

    with contextlib.suppress(Exception):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def _inject_truststore() -> None:
    """Trust the OS certificate store for every TLS connection (issue #99).

    Python verifies against certifi's bundle, which by construction holds only
    public root CAs. A corporate root CA — installed system-wide, and the reason
    VS Code / Copilot (Node, OS trust store) keep working on the same network —
    is therefore invisible to aelix, and every provider request dies as an
    opaque ``APIConnectionError("Connection error.")``. ``truststore`` rebinds
    ``ssl.SSLContext``, so it must run BEFORE the first SSL context is built.

    Process-wide by design: httpx builds its context from ``ssl.create_default_
    context()``, so this ONE call covers all ~10 client construction sites (both
    SDKs, the OAuth flows, the bespoke Codex adapter) without a ``verify=``
    argument on any of them, and covers any client an extension opens too.

    CLI-entry ONLY: rebinding a stdlib class is a process-global side effect
    that a library import must never impose on an embedder — hence here beside
    :func:`register_providers` rather than at any module scope.

    Best-effort: a missing wheel, an unsupported platform, or a truststore
    backend that cannot reach the platform store must degrade to certifi (the
    previous behavior), never block launch.
    """

    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - launch must survive any trust-store defect
        pass


def main_sync() -> None:
    """Sync entry for ``[project.scripts] aelix = '...:main_sync'``.

    Trusts the OS certificate store, loads a cwd ``.env`` + registers provider
    adapters (real-turn enablement; done here rather than in :func:`_async_main`
    so tests/embedders that call ``_async_main`` directly stay side-effect-free),
    then wraps :func:`_async_main` in :func:`asyncio.run` and forwards the exit
    code.
    """

    _inject_truststore()
    load_dotenv()
    register_providers()
    try:
        exit_code = asyncio.run(_async_main(sys.argv[1:]))
        # Flush INSIDE the guard: if the stdout consumer vanished mid-run
        # (``aelix -p ... | head -1``), buffered bytes surface here as
        # BrokenPipeError instead of as the interpreter's noisy
        # "Exception ignored in ... BrokenPipeError" shutdown flush (which
        # forced exit code 120). Issue #57. suppress(ValueError): a stdout
        # already CLOSED (not a pipe death) has nothing left to flush —
        # exiting normally is correct (review NIT).
        with contextlib.suppress(ValueError):
            sys.stdout.flush()
    except BrokenPipeError:
        # Issue #57: stdout consumer went away. Point stdout at devnull so
        # the interpreter's shutdown flush of any still-buffered bytes
        # cannot raise again, then exit with the shell pipeline convention
        # 128+SIGPIPE. (pi's analogue: dead-terminal EPIPE → quiet
        # ``process.exit(129)``; Python pipelines conventionally use 141.)
        _stdout_to_devnull()
        sys.exit(141)
    except BaseException:
        # Ctrl+C / SystemExit / crashes keep their exact semantics — but
        # flush NOW and devnull stdout on EPIPE so a dirty buffer + dead
        # pipe cannot resurface as interpreter shutdown-flush noise on
        # these exit paths either (adversarial-review LOW).
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            _stdout_to_devnull()
        except Exception:
            pass
        raise
    sys.exit(exit_code)


__all__ = [
    "AppMode",
    "_async_main",
    "_inject_truststore",
    "main_sync",
    "resolve_app_mode",
    "to_print_output_mode",
]
