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
import sys
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
from aelix_ai.streaming import Model

from .args import Args, parse_args, print_help
from .config import VERSION
from .file_processor import process_file_arguments
from .initial_message import build_initial_message

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
    from pathlib import Path

    cwd = str(Path.cwd())
    return await repo.create(JsonlSessionCreateOptions(cwd=cwd))


def _build_harness_options(parsed: Args, session: Session) -> AgentHarnessOptions:
    """Assemble :class:`AgentHarnessOptions` from parsed CLI args.

    Sprint 6h₆ is print + JSON + RPC only — the ``SettingsManager`` port
    is deferred (ADR-0089 §"Carry-forward"). Model defaults derive from
    ``--provider`` / ``--model`` flags via a bare :class:`Model` — Pi's
    rich model-resolution path (provider lookup, cost map, thinking
    levels, etc.) lands with the ``SettingsManager`` port.
    """

    model = Model(
        id=parsed.model or "",
        provider=parsed.provider or "",
    )
    options = AgentHarnessOptions(
        model=model,
        session=session,
        cwd=".",
    )
    if parsed.system_prompt is not None:
        options.system_prompt = parsed.system_prompt
    # Sprint 6h₇a (Phase 5a-iii-α, ADR-0090, §D): minimal text-only
    # ``--append-system-prompt`` wire. ``parsed.append_system_prompt``
    # is already a :class:`list[str]` accumulator (args.py:101). The
    # harness joins on ``"\n\n"`` after the base system prompt at
    # ``__init__`` time.
    options.append_system_prompt = list(parsed.append_system_prompt)
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

    # === Help / version short-circuit ========================================
    if parsed.help:
        print_help()
        return 0
    if parsed.version:
        print(VERSION)
        return 0

    # === --list-models — Sprint 6h₇a (ADR-0090) wired =======================
    if parsed.list_models is not None:
        # Lazy imports — defer ``ModelRegistry`` + ``AuthStorage``
        # construction cost off the ``--version`` / ``--help`` fast paths
        # (~10ms saved on cold start). Hoisting these to module scope
        # would trigger auth file I/O on every invocation.
        from pathlib import Path

        from aelix_ai.oauth import AuthStorage

        from ..model_registry import ModelRegistry
        from .config import get_agent_dir
        from .list_models import list_models

        auth_storage = AuthStorage(Path(get_agent_dir()) / "auth.json")
        await auth_storage.load()
        model_registry = ModelRegistry.create(auth_storage)
        await list_models(model_registry, parsed.list_models)
        return 0

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

    # === Interactive mode → Phase 5b carry-forward ===========================
    if app_mode == "interactive":
        print(
            "Error: interactive mode not implemented "
            "(Phase 5b — TUI carry-forward; see ADR-0088).",
            file=sys.stderr,
        )
        raise NotImplementedError(
            "Interactive mode is deferred to Phase 5b (ADR-0088)."
        )

    # === Stdin + file processing (non-RPC) ===================================
    stdin_content: str | None = None
    file_text = ""
    file_images: list[object] | None = None

    if app_mode != "rpc":
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
    session = await _build_session(parsed, repo)

    async def _harness_factory(new_session: Session) -> AgentHarness:
        opts = _build_harness_options(parsed, new_session)
        return AgentHarness(opts)

    harness = await _harness_factory(session)
    runtime = await create_agent_session_runtime(
        harness, _harness_factory, repo=repo, fs=fs
    )

    try:
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


def main_sync() -> None:
    """Sync entry for ``[project.scripts] aelix = '...:main_sync'``.

    Wraps :func:`_async_main` in :func:`asyncio.run` and forwards the
    exit code to :func:`sys.exit`.
    """

    exit_code = asyncio.run(_async_main(sys.argv[1:]))
    sys.exit(exit_code)


__all__ = [
    "AppMode",
    "_async_main",
    "main_sync",
    "resolve_app_mode",
    "to_print_output_mode",
]
