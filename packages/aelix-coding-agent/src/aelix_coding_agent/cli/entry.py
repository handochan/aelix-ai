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
import copy
import os
import select
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness._frontmatter import parse_frontmatter
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

from aelix_coding_agent.agents import (
    AgentProfile,
    ProfileError,
    apply_profile_to_args,
    resolve_profile,
)
from aelix_coding_agent.builtin.guardrail import GuardrailExtension
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.core.runnable_models import is_runnable, unsupported_message
from aelix_coding_agent.extensions.loader import (
    discover_and_load_extensions,
    gate_manifest_mcp_contribs,
    scan_extension_manifests,
)
from aelix_coding_agent.mcp import McpClientManager
from aelix_coding_agent.subagent_contract import MAX_SUBAGENT_DEPTH, subagent_depth
from aelix_coding_agent.tools import ALL_TOOL_NAMES, create_all_tools

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


async def _read_piped_stdin(*, required: bool = True) -> str | None:
    """Pi parity: ``readPipedStdin`` — plus an aelix-original hang guard.

    Returns :data:`None` when stdin is a TTY (interactive shell). When
    stdin is piped (file redirect, here-doc, etc.), reads the full
    payload and strips surrounding whitespace; empty content → :data:`None`.

    Issue #57 (aelix-original hardening — pi DECLINED the same report,
    pi#5571, workaround ``</dev/null``): the read-to-EOF used to block
    forever on a non-TTY pipe whose writer never closes, and the path is
    reachable with ZERO flags (any piped stdin promotes ``app_mode`` to
    ``"print"`` in :func:`resolve_app_mode`). On POSIX we now wait for
    FIRST-byte readiness under a deadline (``AELIX_STDIN_TIMEOUT``
    overrides, ``0`` waits forever); on timeout we proceed WITHOUT stdin
    input. ``select`` runs in a worker thread but always returns at the
    deadline, so no thread leaks (a bare ``wait_for`` around the blocking
    read would strand the reader thread — the OS read is uncancellable).
    Once data/EOF is ready, the read-to-EOF itself is unbounded: a producer
    that writes a byte and never closes still hangs, matching pi
    (pathological, user-error territory). Windows keeps the previous
    blocking read — ``select`` is socket-only there. A stdin without a real
    fd (pytest capture, embedders) skips the readiness gate and reads
    directly.

    ``required`` says whether stdin is the ONLY place a prompt can come
    from, and it picks the default deadline:

    * ``True`` (nothing on argv) — stdin IS the prompt, so waiting for it is
      the whole point. Deadline 30s, and a timeout warns on stderr because
      the run really is missing its input.
    * ``False`` (argv already carries a prompt or an ``@file``) — stdin is
      SUPPLEMENTARY. :func:`build_initial_message` concatenates
      ``stdin + file_text + argv`` (pi parity, and asserted by
      ``test_composition_stdin_plus_message``), so a piped payload must
      still be picked up — ``cat notes.txt | aelix -p "summarise this"`` is
      a supported shape, and skipping the read outright would silently drop
      ``notes.txt``. But an inherited-yet-idle pipe (a subprocess spawned
      with ``stdin=PIPE``, a CI harness) must not cost 30s to discover it has
      nothing to say, so the deadline drops to a 5s grace window.

    Both deadlines WARN on expiry. The supplementary path used to stay quiet
    on the theory that nothing was missing, which was wrong: it is impossible
    to tell "idle pipe, nothing coming" from "real producer that is still
    warming up" without waiting, so an expiry there may well have dropped a
    payload that was about to arrive (``curl slow-url | aelix -p …``,
    ``ssh host cmd | aelix -p …``). Dropping input is acceptable; dropping it
    SILENTLY is not, so the note always prints and names the knob to turn.

    An explicit ``AELIX_STDIN_TIMEOUT`` always wins over both defaults, so a
    deliberate ``0`` (wait forever) is honoured either way.
    """

    if sys.stdin.isatty():
        return None
    if sys.platform != "win32":
        timeout = _env_float("AELIX_STDIN_TIMEOUT")
        if timeout is None:
            timeout = 30.0 if required else 5.0
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
                # ALWAYS announce it. Expiring here is indistinguishable from
                # "a producer that had not written yet", so this branch can and
                # does discard input that was seconds away. Losing it is a
                # tolerable default; losing it silently is not — that turns a
                # slow `curl … | aelix -p …` into a wrong answer with no clue
                # why. Both texts name the knob that makes the wait longer.
                if required:
                    detail = (
                        "proceeding without stdin input (redirect </dev/null "
                        "if none was intended, or set AELIX_STDIN_TIMEOUT=0 "
                        "to wait indefinitely)"
                    )
                else:
                    detail = (
                        "continuing with the command-line prompt only. If a "
                        "slow producer was meant to feed stdin, raise "
                        "AELIX_STDIN_TIMEOUT (0 waits indefinitely); redirect "
                        "</dev/null to skip this wait entirely"
                    )
                # suppress: a DEAD STDERR must not abort a healthy run — an
                # unguarded warning print here raised BrokenPipeError, which
                # main_sync would misclassify as stdout death (exit 141) and
                # devnull the LIVE stdout (adversarial-review LOW).
                with contextlib.suppress(OSError):
                    print(
                        f"aelix: no data on piped stdin after {timeout:g}s; "
                        f"{detail}",
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

    ``--no-builtin-tools`` (built-ins off, extension/MCP tools on) is
    deliberately NOT expressed here: ``active_tool_names`` is seeded BEFORE
    extensions register their tools, so any filter written at this point would
    also disable every extension + MCP tool. ADR-0196 wires it as the only
    faithful alternative — a POST-registration ``set_active_tools`` inside
    ``_harness_factory``, right after ``AgentHarness(opts)`` — guarded on
    ``not parsed.no_tools`` so it can never re-open the kill switch this
    function returns ``[]`` for.
    """

    if parsed.no_tools:
        return []
    if parsed.tools:
        return list(parsed.tools)
    return None


def _agents_delegation_enabled(
    parsed: Args, settings_manager: SettingsManager | None
) -> bool:
    """Is agent delegation ON for this run? — ADR-0197 §(a)/§(b), P2.

    Precedence: ``--no-agents`` > ``--agents`` > the global ``[features] agents``
    setting (default **False**). ``parsed.agents_override`` is TRI-state
    (``args.py`` sets ``True``/``False``/leaves ``None``), so a run-scoped flag —
    in either direction — always beats the persisted value, and only the absent
    flag falls through.

    The settings read is GLOBAL-scope-only INSIDE the getter
    (``settings_manager.py``'s ``get_features_agents``, which reads
    ``_global_settings`` and never the merged view). That is a security property,
    not an implementation detail: a merged read would let any cloned repo switch
    delegation on from its own ``.aelix/settings.json`` — the same self-elevation
    defeat ``get_default_project_trust`` exists to prevent. It stops a cloned
    repo's ``.env`` too, but only because ``load_dotenv`` refuses the names in
    ``_DOTENV_LOCKED`` that decide WHICH file is the global one (ADR-0203);
    global-scope-only means nothing if the repo picks the file.

    No manager (embedders, tests) → ``False``: the conservative direction, and
    the same answer an unset setting gives.
    """

    if parsed.agents_override is not None:
        return parsed.agents_override
    if settings_manager is None:
        return False
    return settings_manager.get_features_agents()


_MAX_PROMPT_FILE_BYTES = 1 << 20
"""1 MiB ceiling for ``--system-prompt-file`` / ``--append-system-prompt-file``.

A system prompt past this size exceeds every shipping model's context window, so
the failure is better raised HERE — naming the offending path — than as an
opaque provider-side 400 on the first turn.
"""


class _PromptFileError(Exception):
    """Internal carrier for a prompt-file read failure (ADR-0196).

    Private to :func:`_apply_prompt_files`, which converts it back into the
    ``str | None`` error channel its caller expects. It exists only so the read
    pass can bail from a comprehension without ``(text, error)`` tuple plumbing.
    """


def _read_prompt_file(flag: str, raw_path: str) -> str:
    """Read one ``--*-system-prompt-file`` path, or raise :class:`_PromptFileError`.

    Three refusals, each a distinct failure the naive ``read_text`` would report
    badly or not at all:

    - not a regular file — catches a directory AND the ``/dev/stdin``-style
      character device that would otherwise BLOCK the whole startup on a read
      that never returns;
    - larger than :data:`_MAX_PROMPT_FILE_BYTES`;
    - unreadable / not UTF-8 (``OSError`` — which subsumes
      :class:`IsADirectoryError` — or :class:`UnicodeDecodeError`).
    """

    path = Path(raw_path)
    if not path.is_file():
        raise _PromptFileError(f"cannot read {flag} {raw_path}: not a regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _PromptFileError(f"cannot read {flag} {raw_path}: {exc}") from exc
    if size > _MAX_PROMPT_FILE_BYTES:
        raise _PromptFileError(
            f"cannot read {flag} {raw_path}: {size} bytes exceeds the "
            f"{_MAX_PROMPT_FILE_BYTES}-byte system-prompt limit"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _PromptFileError(f"cannot read {flag} {raw_path}: {exc}") from exc
    return _strip_prompt_frontmatter(text)


def _strip_prompt_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block from a prompt file (ADR-0196).

    A prompt file may BE an agent profile: ``resolver.profile_to_flags`` renders
    ``--append-system-prompt-file <profile>.md`` and ``/agents show`` prints that
    exact line for the user to copy and run. Reading such a file whole shipped the
    profile's own YAML header into the model's system prompt — a CONTENT
    divergence between the two channels a profile reaches the runtime through
    (the argv, and ``resolver.apply_profile_to_args``, whose body is the
    frontmatter-STRIPPED ``AgentProfile.body``). The anti-drift contract in
    ``agents/resolver.py`` exists precisely to forbid that.

    Deliberately narrow: the block is dropped only when it parses into a NON-EMPTY
    mapping, so an ordinary prompt that merely opens with ``---`` (a horizontal
    rule, a bare YAML document separator, an unclosed block) is returned
    byte-for-byte. The stripped result is ``parse_frontmatter``'s own body, which
    is what ``agents.profile.parse_profile`` stores — so the two channels now
    agree by construction rather than by coincidence.
    """

    frontmatter, body, error = parse_frontmatter(text)
    if error is not None or not frontmatter:
        return text
    return body


def _apply_prompt_files(parsed: Args) -> str | None:
    """ADR-0196 — normalize the two file-taking prompt flags into their string
    twins. Returns a user-facing error message, or :data:`None` on success.

    ``--system-prompt`` (a LITERAL string) always WINS over
    ``--system-prompt-file`` — hence the ``parsed.provided`` consult rather than
    an ``is None`` test: the seed/overlay machinery downstream also writes
    ``parsed.system_prompt``, so "did the user type it?" is the only honest
    question. The file-appends land AFTER the string-appends, preserving the
    flag channel's order semantics.

    Runs exactly ONCE: ``parse_args`` has a single call site (the top of
    :func:`_async_main`) and ``_build_harness_options`` re-reads
    ``parsed.append_system_prompt`` fresh on every build via
    :func:`_resolve_append_chunks` WITHOUT mutating it, so a once-only
    normalization cannot double-append across /new, /fork, /resume or reload.

    Reads everything BEFORE mutating anything, so a failure on the second of two
    files leaves ``parsed`` exactly as it was (same discipline as
    ``agents.resolver.apply_profile_to_args``).
    """

    try:
        replacement: str | None = None
        if parsed.system_prompt_file is not None:
            replacement = _read_prompt_file(
                "--system-prompt-file", parsed.system_prompt_file
            )
        appended = [
            _read_prompt_file("--append-system-prompt-file", raw)
            for raw in parsed.append_system_prompt_files
        ]
    except _PromptFileError as exc:
        return str(exc)

    if replacement is not None and "system_prompt" not in parsed.provided:
        parsed.system_prompt = replacement
        # ADR-0196 (review fix) — the file twin carries the SAME provenance as
        # its literal twin. ``apply_profile_to_args`` gates on the FIELD name
        # ``system_prompt`` (``agents/resolver.py``), so without this line a
        # ``system_prompt: replace`` profile silently overwrote a file the user
        # named on the command line — while ``--system-prompt`` correctly won.
        # ``--system-prompt-file`` is the flag a LONG prompt reaches for (that is
        # the whole reason it exists), so the asymmetry broke exactly the case it
        # was added to serve, and it broke D1.3's stated invariant "an explicit
        # CLI flag always wins over the profile".
        #
        # The APPEND twin deliberately gets no such line: ``append_system_prompt``
        # is an ungated accumulator on both channels (see the long comment in
        # ``apply_profile_to_args``), so marking it provided would make the
        # profile's identity vanish rather than make the user's chunk win.
        parsed.provided.add("system_prompt")
    parsed.append_system_prompt.extend(appended)
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


def _resolve_system_prompt(parsed: Args, cwd: str) -> str:
    """The BASE system prompt for one harness build (ADR-0196).

    Lifted verbatim out of :func:`_build_harness_options` — which now calls it —
    so ``/agents use`` can recompute a LIVE identity from the *identical* inputs
    the factory would use. The join itself is mirrored exactly once, in
    ``agents.prompt.compose_system_prompt`` (pinned against a real
    ``AgentHarness``); this extraction is what makes drift on the *inputs*
    structurally impossible too.
    """

    return (
        parsed.system_prompt
        if parsed.system_prompt is not None
        else build_system_prompt(cwd)
    )


def _resolve_append_chunks(parsed: Args, cwd: str) -> list[str]:
    """The APPEND chunks for one harness build (ADR-0196).

    Companion to :func:`_resolve_system_prompt`, lifted from the same function
    for the same reason. Returns a FRESH list every call and never mutates
    ``parsed.append_system_prompt`` — which is what lets ``_apply_prompt_files``
    normalize the file flags exactly once without any rebuild double-appending.
    """

    # Auto-discovered AGENTS.md project context (Pi ``--no-context-files`` gate),
    # then the explicit ``--append-system-prompt`` chunks. The harness joins all
    # of these onto the base system prompt with ``"\n\n"`` at ``__init__`` time.
    append: list[str] = []
    if not parsed.no_context_files:
        context = discover_context_files(cwd)
        if context:
            append.append(context)
    append.extend(parsed.append_system_prompt)
    return append


async def _build_harness_options(
    parsed: Args,
    session: Session,
    *,
    mcp_tools: list[AgentTool] | None = None,
    get_api_key_and_headers: Callable[..., Any] | None = None,
    project_trusted: bool = True,
    permission_ext: PermissionExtension | None = None,
    agents_ext: Any | None = None,
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

    ``agents_ext`` (ADR-0197, P2) is the bundled ``aelix-agents`` delegation
    extension, or :data:`None` when delegation is off — which is the P2 default.
    Typed ``Any`` deliberately: product-core must not import band 3 to name it.
    See the prepend site below for the ordering + depth invariants.
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
    system_prompt = _resolve_system_prompt(parsed, cwd)
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
    # ADR-0197 (P2) — the bundled ``aelix-agents`` delegation extension.
    # APPENDED, never inserted: Guardrail stays FIRST (first-block-wins, see the
    # DO NOT REORDER note above) and the permission gate second, so OUR
    # ``tool_call`` handler — which carries the spawn-time consent gate (§(i)) —
    # runs LAST. A guardrail hard-deny and a permission denial therefore both
    # win over delegation, and neither can be softened by it.
    #
    # The DEPTH GATE lives here as well as in the seam (``bind_subagents``,
    # finding I4) and in the tool itself: a process already at
    # ``MAX_SUBAGENT_DEPTH`` must not even LOAD the extension, so a nested child
    # physically has no ``agent`` tool regardless of ``inherit_extensions:`` or
    # an explicit ``-e``. ``agents_ext is None`` is the ordinary state — P2 is
    # default-off (``[features] agents``), and ``_agents_delegation_enabled``
    # decides that once, in ``_async_main``.
    prepend_extensions: list[Any] = [GuardrailExtension(), permission]
    if agents_ext is not None and subagent_depth() < MAX_SUBAGENT_DEPTH:
        prepend_extensions.append(agents_ext)
    loaded = await discover_and_load_extensions(
        [str(p) for p in parsed.extensions],
        cwd=Path(cwd),
        agent_dir=Path(get_agent_dir()),
        prepend=prepend_extensions,
        no_discovery=parsed.no_extensions,
        no_project_local=not project_trusted,
        # Issue #91 provenance gate — the ``--trust-extension-path`` override
        # (distribution names allowed despite resolving outside site-packages).
        trusted_ep_dists=frozenset(parsed.trust_extension_paths),
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
        # ADR-0196 — ``--thinking <level>`` (and therefore an agent profile's
        # ``thinking:``) was PARSED BUT UNCONSUMED: ``args.py`` was the only
        # writer of ``parsed.thinking`` and nothing in the product core ever
        # read it, so the flag silently did nothing on every launch. The kernel
        # seam already existed (``AgentHarnessOptions.thinking_level`` →
        # ``AgentState.thinking_level``, core.py:238 / :603-604), so wiring it
        # is this one kwarg. ``None`` leaves the ``"off"`` state default
        # (types.py:84) untouched, which is the pre-fix behaviour for everyone
        # who never passed the flag.
        thinking_level=parsed.thinking,
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

    options.append_system_prompt = _resolve_append_chunks(parsed, cwd)
    return options


async def _prompt_one_shot_select(body: str, options: list[str]) -> str | None:
    """A1 seam (Sprint P0 #10) — the one-shot pre-``run_tui`` selector.

    The bootstrap-order tension (spec §2.6): extensions load inside the
    harness factory and MCP connects BEFORE ``run_tui`` builds its chrome, so
    a security decision must be made before any project-local code runs — but
    the persistent TUI's ``ctx.ui.select`` is not yet bound. A1 resolves this
    with a tiny DEDICATED ``prompt_toolkit.Application`` (a one-shot full-screen
    selector) that runs to completion and returns BEFORE the harness factory /
    MCP connect, so the gate strictly precedes execution.

    Returns the CHOSEN LABEL, or :data:`None` on Esc / Ctrl+C. Returns
    :data:`None` too when the ``[tui]`` extra is missing (prompt-toolkit
    unavailable). Both are the same signal on purpose: every caller is a
    consent gate, and "no answer" must mean DENY, never "assume yes".

    ADR-0196 extracted this out of :func:`_prompt_project_trust_interactive`
    verbatim so :func:`_confirm_project_agent` reuses the exact same widget —
    and, more to the point, the exact same fail-closed paths — rather than
    growing a second hand-rolled consent dialog that could drift from it.
    """

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
    except ImportError:
        # No TUI extra → cannot prompt; caller denies by default.
        return None

    state = {"idx": 0}

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

    return chosen["label"]


async def _prompt_project_trust_interactive(
    cwd: Path,
) -> ProjectTrustPromptResult | None:
    """A1 seam (Sprint P0 #10) — the project-trust one-shot selector.

    A thin caller of :func:`_prompt_one_shot_select` (ADR-0196 extraction).
    Returns the user's :class:`ProjectTrustPromptResult`, or :data:`None` on
    Esc / Ctrl+C **and** when the ``[tui]`` extra is missing — the caller then
    denies by default, which is the safe direction.
    """

    label = await _prompt_one_shot_select(
        format_project_trust_prompt(cwd), project_trust_options(cwd)
    )
    if label is None:
        return None
    return interpret_trust_option(label, cwd)


PROJECT_AGENT_CONFIRM_OPTIONS: tuple[str, str] = ("Run this profile", "Cancel")
"""The two answers to the per-identity project-agent confirmation (ADR-0196).

Public (no leading underscore) because ADR-0197 §(f) gives that confirmation a
SECOND consumer — ``tui/commands.py``'s ``/agents run`` — and the two must offer
the same words. The affirmative is index 0 and is matched by IDENTITY against the
rendered option, never by substring: a profile is free to be named ``Cancel``.
"""


def project_agent_confirm_body(name: str, file_path: str) -> str:
    """The body text of the project-agent confirmation prompt (ADR-0196).

    Extracted from :func:`_confirm_project_agent` so ``/agents run`` (ADR-0197
    §(f)) can drive the SAME copy through a different transport. The two
    transports are not interchangeable and that is why only the copy is shared:
    :func:`_prompt_one_shot_select` builds a dedicated one-shot
    ``prompt_toolkit.Application`` for the pre-``run_tui`` window and cannot be
    driven while the REPL's own Application is running, so the in-session caller
    uses the extension UI seam (``harness.runtime.ui``) instead.
    """

    return (
        f"Run project agent profile {name!r}?\n"
        f"{file_path}\n\n"
        "This replaces or extends the agent's system prompt and may change "
        "its model and tools."
    )


async def _confirm_project_agent(profile: AgentProfile) -> bool:
    """pi ``confirmProjectAgents`` (spec §2.2 line 80) — ADR-0196.

    Project trust is a DIRECTORY-level, yes-once decision that ancestors
    inherit (``project_trust.py``'s nearest-ancestor walk); it is emphatically
    NOT consent to a specific identity file that showed up in the repo later.
    Since a project profile also WINS a ``name`` collision against the user's
    own ``~/.aelix/agent/agents/<name>.md``, that combination is an escalation
    primitive on its own — so the second, per-identity confirmation ships in
    the same change as the collision rule it mitigates.

    Fail-closed in every direction: no ``[tui]`` extra, Esc, or Ctrl+C all
    surface as :data:`None` from the selector and therefore ``False`` here,
    mirroring ``project_trust.py``'s own step-6 deny-by-default.
    """

    label = await _prompt_one_shot_select(
        project_agent_confirm_body(profile.name, profile.file_path),
        list(PROJECT_AGENT_CONFIRM_OPTIONS),
    )
    return label == PROJECT_AGENT_CONFIRM_OPTIONS[0]


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

    # === Prompt files → their string twins (ADR-0196) ========================
    # Deliberately placed HERE and not "right after arg validation": the nearest
    # validation boundary is ``_validate_resume_flag`` above, which precedes both
    # the ``--list-models`` short-circuit and the ``--export`` exit — so an
    # unreadable prompt file would hard-fail two do-a-thing-and-exit actions that
    # never consult a system prompt at all. Everything below this line does.
    prompt_file_error = _apply_prompt_files(parsed)
    if prompt_file_error is not None:
        print(f"Error: {prompt_file_error}", file=sys.stderr)
        return 1

    # ADR-0196 — the pristine baseline ``/agents use`` resets to. Taken here,
    # after the prompt files are normalized (so the baseline carries the user's
    # real CLI intent in its final string form) but BEFORE every mutator that
    # follows: the profile overlay, the settings default-model seed, and
    # ``build_initial_message``'s documented side effect on ``parsed.messages``.
    # ``use`` overlays a profile onto a deepcopy of THIS, never onto whatever the
    # previously-active profile left behind, so switching identities twice is not
    # cumulative (``apply_profile_to_args`` is accretive and latching by design).
    profile_baseline = copy.deepcopy(parsed)

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
        # Wait the full deadline for stdin only when it is the ONLY prompt
        # source. `aelix -p "hi"` that merely INHERITS an idle pipe (spawned
        # with stdin=PIPE, or under a CI harness) used to pay the whole 30s
        # before discovering the pipe had nothing to say — measured 35s vs
        # 1.2s with `</dev/null`. It is not dropped, only time-boxed: a real
        # producer still lands inside the grace window, so `cat notes.txt |
        # aelix -p "summarise"` keeps concatenating both (pi parity), and an
        # expiry always prints a note rather than losing input in silence.
        stdin_content = await _read_piped_stdin(
            required=not parsed.messages and not parsed.file_args
        )
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

    # (ADR-0196) The WP-2 settings default-model seed USED to sit here. It now
    # runs AFTER the agent-profile overlay further down — see the relocated block
    # — because a profile's ``model:`` has to occupy ``parsed.model`` before the
    # seed decides whether the gap needs filling. Nothing between here and there
    # reads ``parsed.model`` / ``parsed.provider`` / ``default_provider``.

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

    # ADR-0197 §(e) — CHILD-ONLY headless floor. A delegated child has no TUI, so
    # ``builtin/permission.py``'s ``not ctx.has_ui`` branch auto-ALLOWS every
    # mutating tool. Flipping it to block-with-reason ONLY when this process is
    # itself a subagent leaves every existing ``-p`` / ``--mode json`` /
    # ``--mode rpc`` user untouched.
    #
    # NOTE (finding B4): this floor is NOT sufficient on its own — the
    # AUTO_ACCEPT in-cwd write branch ``return None``s ABOVE it. The real
    # guarantee is the spawner-side posture CLAMP (``aelix_agents/posture.py``),
    # optionally raised by ONE explicit human answer at §(i)'s consent dialog.
    # Do not mistake this belt for the braces.
    if subagent_depth() > 0:
        permission_ext.headless_default = "block"
    # ``--permission-mode`` seeds the SAME held posture shift+tab cycles, so the
    # spawner's clamp output is what the child actually launches under. Applied
    # here, before any harness is built, because the posture object is threaded
    # by reference into every rebuild (ADR-0157). An invalid value never reaches
    # this line: ``args.py`` leaves the field ``None`` and files a diagnostic
    # instead, so a typo'd flag degrades to the default posture rather than to a
    # ``ValueError`` at startup.
    if parsed.permission_mode is not None:
        permission_posture.set(PermissionMode(parsed.permission_mode))

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
    # (ADR-0196) The ``--api-key`` runtime-override block USED to sit here, right
    # after the callback above. It now runs AFTER the profile overlay + the
    # relocated seed, because it RESOLVES the model to decide which provider the
    # key is pinned to: run at this point, ``--agent scout --api-key K`` would
    # resolve against a ``parsed.model`` the profile had not filled in yet and
    # either refuse the key outright or pin it to the WRONG provider (a live,
    # process-wide override on a vendor this run never talks to). Only the
    # ``_make_auth_callback`` wiring stays here — it takes no model.

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
                # Issue #91 provenance gate — honour the same override here.
                trusted_ep_dists=frozenset(parsed.trust_extension_paths),
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

    # === Agent delegation (ADR-0197 §(a)/§(b), P2) ============================
    # The bundled ``aelix-agents`` extension. Constructed ONCE and threaded by
    # held reference into every harness rebuild (the mirror of ``permission_ext``)
    # so the child registry, the session consent memo and ``stop_all`` survive
    # /new, /fork, /resume — a delegation left running across a session swap that
    # nothing could stop would be a leak, not a feature.
    #
    # The import is FUNCTION-LOCAL and this is the ONLY site in product-core that
    # names ``aelix_agents`` (the import-direction rule of §(a), pinned by
    # ``tests/cli/test_p2_import_direction.py``): band 2 declares the contract,
    # band 3 owns every line of spawn behaviour, and the dependency may only
    # point that way. It is also guarded so a broken or absent extension package
    # degrades to "no delegation" rather than bricking startup for the user.
    #
    # Placed AFTER the trust gate rather than beside ``permission_ext``: three of
    # the four constructor arguments are not knowable earlier. Nothing between
    # the two points touches ``agents_ext``, and ``_harness_factory`` (defined
    # below) reads this closure variable at CALL time.
    agents_ext: Any = None
    if _agents_delegation_enabled(parsed, settings_manager):
        try:
            from aelix_agents import AgentsExtension
        except Exception as exc:  # noqa: BLE001 — never fatal
            print(f"Warning: aelix-agents unavailable: {exc}", file=sys.stderr)
        else:
            agents_ext = AgentsExtension(
                # SECURITY: the SAME ``PermissionPosture`` object the gate reads,
                # not a copy. shift+tab mutates that one holder, and the child
                # posture CLAMP (§(e)) has to see the change — a copy would clamp
                # against a stale parent posture forever. Omitting it is safe but
                # inert: the clamp would read DEFAULT, whose child mode is
                # ``plan``, so every delegation would be silently read-only.
                posture=permission_posture,
                # The remaining three are PRE-HOOK fallbacks only: an
                # ``ExtensionContext`` reaches the extension via HOOKS, and
                # ``/agents run`` can be the very first thing a user types. A live
                # context always wins, so these can only ever answer the window
                # before the first hook has fired.
                agent_dir=get_agent_dir(),
                cwd=cwd,
                project_trusted=project_trusted,
            )

    # === Agent profile identity (ADR-0196) ===================================
    # Placed AFTER the trust gate — project profiles are inert until the
    # directory is trusted, and the trust predicate now knows about
    # ``.aelix/agents`` — and BEFORE ``scan_extension_manifests`` /
    # ``_resolve_skill_dirs`` / the harness factory, so the overlay is visible to
    # all three. Everything the profile can set (model, provider, tools, skills,
    # extensions, context files, thinking, system prompt) lands on ``parsed``,
    # which is the ONE object the factory closes over.
    #
    # Known ordering caveat, deliberate: the throwaway project-trust VOTE
    # extension load above runs before this, so a profile's ``--no-extensions``
    # cannot suppress it. That load is bounded (user/global tiers only, never
    # project-local), its runtime is bound to nothing, and it only happens at all
    # when trust is still unresolved.
    active_profile: AgentProfile | None = None
    if parsed.agent is not None and parsed.agent_file is not None:
        print(
            "Error: --agent and --agent-file are mutually exclusive",
            file=sys.stderr,
        )
        return 1
    profile_ref = parsed.agent_file if parsed.agent_file is not None else parsed.agent
    if profile_ref is not None:
        try:
            active_profile = resolve_profile(
                profile_ref,
                cwd=cwd,
                project_trusted=project_trusted,
                is_file=parsed.agent_file is not None,
            )
        except ProfileError as exc:
            # Fatal, never a warning: running under an identity OTHER than the
            # one the user named is a safety problem, not a degraded mode.
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        # pi ``confirmProjectAgents``. Directory trust is a yes-once decision
        # that ancestors inherit; it is not consent to a project-local identity
        # file, which additionally WINS a name collision against the user's own
        # profile of the same name. ``--approve`` is the pre-existing "trust
        # project-local files for this run" escape hatch — no new flag.
        if (
            active_profile.scope == "project"
            and parsed.project_trust_override is not True
        ):
            if app_mode != "interactive":
                print(
                    f"Error: project-scoped agent profile {active_profile.name!r} "
                    f"({active_profile.file_path}) requires confirmation; re-run "
                    "interactively, pass --approve, or move the profile to "
                    f"{Path(get_agent_dir()) / 'agents'}.",
                    file=sys.stderr,
                )
                return 1
            if not await _confirm_project_agent(active_profile):
                print("Error: project agent profile declined.", file=sys.stderr)
                return 1
        try:
            application = apply_profile_to_args(
                parsed, active_profile, provided=parsed.provided
            )
        except ProfileError as exc:
            # Raised only when the profile would silently WIDEN a kill switch the
            # user set explicitly (``--no-extensions`` vs ``extensions:``).
            # ``parsed`` is untouched on this path.
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        # Audit line on stderr (never stdout — ``--mode json`` / ``--print``
        # stdout is a machine-readable channel). Which identity a run assumed is
        # exactly the thing a transcript must be able to answer later.
        print(
            f"Agent profile: {active_profile.name} "
            f"({active_profile.scope}: {active_profile.file_path})",
            file=sys.stderr,
        )
        for notice in application.notices:
            # ``recompute-default-provider`` is consumed structurally by the
            # relocated seed block below (its ``elif`` hands the persisted
            # provider to ``resolve_model``'s lowest-precedence slot), so it is
            # machinery rather than something to tell the user about.
            if notice != "recompute-default-provider":
                print(f"Notice: agent profile: {notice}", file=sys.stderr)
        if application.skipped:
            # ADR-0196 (review fix) — ``skipped`` used to be computed and never
            # read anywhere in production, so a HALF-applied identity printed the
            # banner above and nothing else. A partially-applied identity is the
            # same class of problem as the wrong identity, which is why ``--agent``
            # is fatal-on-error; reporting it as a clean success is not an option.
            # It is a NOTICE rather than a refusal because the precedence itself is
            # correct and intended — the user's own flag won.
            print(
                "Notice: agent profile: CLI flags override "
                f"{', '.join(application.skipped)}",
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
    #
    # ADR-0196 RELOCATION: this block used to run immediately after the
    # SettingsManager was constructed, i.e. UPSTREAM of the profile overlay —
    # which made a profile's ``model:`` inert for anyone who had ever run /model,
    # because ``parsed.model`` was already occupied by the persisted default and
    # the overlay's explicit-CLI precedence had no way to tell the two apart.
    # Moving it here is what makes profile > settings true. The two
    # ``parsed.provided`` membership tests are the belt to that braces: they say
    # "the USER typed it", where the ``is None`` tests only say "something has
    # already filled it in". Note the split-pair case now arrives here by a
    # second route — a profile that names a model but no provider, which
    # ``apply_profile_to_args`` deliberately clears ``parsed.provider`` for — and
    # falls into the same ``elif``, which is exactly the #98 discipline the
    # paragraph above describes.
    default_provider: str | None = None
    with contextlib.suppress(Exception):
        default_model = settings_manager.get_default_model()
        persisted_provider = settings_manager.get_default_provider()
        user_named_a_model = (
            "model" in parsed.provided or "provider" in parsed.provided
        )
        if not user_named_a_model:
            # ADR-0196 (review fix) — BACK-FILL THE ``/agents use`` BASELINE.
            # ``profile_baseline`` was snapshotted far above (before the profile
            # overlay, deliberately: a second ``use`` must overlay the ORIGINAL
            # CLI intent), which is upstream of this relocated seed — so without
            # these two lines the baseline says "no model at all". ``use`` resets
            # ``parsed`` from it, its step-4 model block is gated on
            # ``parsed.model is not None or parsed.provider is not None`` and
            # therefore never fires, and the next /new, /fork or /resume rebuilds
            # ``resolve_model(None, None, …)`` → ``api="unknown"``: the exact #98
            # unrunnable state, reached from a plain ``/agents use <name>`` by
            # anyone who has ever run /model. Proven by driving ``_async_main``.
            #
            # When the user typed NEITHER flag, the persisted default IS the
            # ambient no-profile identity, so it belongs in the baseline. The
            # values are NOT added to ``parsed.provided``: a persisted default is
            # not an explicit flag and must still LOSE to a profile's ``model:``.
            # The CLI-explicit case is excluded by the guard (the baseline already
            # carries the flag), and the split-pair case needs nothing — the
            # persisted provider reaches every rebuild through the factory's
            # ``default_provider`` closure kwarg instead.
            if default_model:
                profile_baseline.model = default_model
            if persisted_provider:
                profile_baseline.provider = persisted_provider
        if (
            not user_named_a_model
            and parsed.model is None
            and parsed.provider is None
        ):
            if default_model:
                parsed.model = default_model
            if persisted_provider:
                parsed.provider = persisted_provider
        elif parsed.provider is None and persisted_provider:
            default_provider = persisted_provider

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
        #
        # ``--api-key`` DOES NOT REACH A DELEGATED CHILD, and the failure is
        # otherwise unreadable. The override is in-memory in THIS process
        # (``auth_storage.py``: "NOT persisted to auth.json"), while a child is a
        # fresh ``-m aelix_coding_agent`` that inherits only the environment. Its
        # own cascade then falls through to the config resolver, which returns an
        # unset ``models.json`` placeholder VERBATIM (pi parity), so the child
        # sends the placeholder NAME as its bearer token and the provider answers
        # 401 — a provider-side error with nothing pointing at the real cause.
        #
        # Deliberately NOT fixed by forwarding the key: argv is world-readable
        # via ``/proc/<pid>/cmdline``, and ``build_child_argv``'s output is
        # shell-quoted into the ``/agents show`` dry run for copy-paste. The env
        # handoff is specified and is the right fix; this warning is what stops
        # the silent version shipping in the meantime.
        if agents_ext is not None:
            print(
                f"Warning: --api-key is not forwarded to delegated agents "
                f"({model.provider}); set the provider's environment variable "
                "instead if you plan to delegate.",
                file=sys.stderr,
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
    #
    # "explicit user choice" is only true because ``AELIX_MCP_CONFIG`` is on
    # ``load_dotenv``'s ``_DOTENV_LOCKED`` list (ADR-0203) — the one branch the
    # ``AELIX_DOTENV_ALLOW`` hatch cannot open. NOT the ``^AELIX_`` rule: that
    # lives in ``_dotenv_key_allowed``, downstream of both the locked branch and
    # the hatch, so for this key it never runs — and on its own it is provably
    # insufficient, because one pasted ``export AELIX_DOTENV_ALLOW=AELIX_MCP_CONFIG``
    # bypasses it and restored the spawn in full. It was NOT true before: a cwd ``.env`` runs before
    # this gate exists, so a cloned repo could set ``AELIX_MCP_CONFIG`` itself
    # and land its server in the one tier that is deliberately never gated —
    # measured spawning ``sh -c`` at startup, even under ``--no-approve``. The
    # ungated tier is safe only for as long as the env var behind it really does
    # come from the user.
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
            # Issue #91 provenance gate — the scan path is what reaches
            # contributes.mcp_servers, so it MUST honour the same gate + override.
            trusted_ep_dists=frozenset(parsed.trust_extension_paths),
        )
    except Exception as exc:  # noqa: BLE001 — scan is additive, never fatal
        print(f"Warning: manifest scan: {exc}", file=sys.stderr)
        _scanned_manifests = []
    # REVERSED tier order among manifests (adversarial-review LOW): the scan
    # yields project → global → explicit, but McpClientManager is LAST-wins —
    # reversing makes the HIGHER-priority tier (project-local) win a
    # manifest-vs-manifest name collision, while .aelix/mcp.json (appended
    # after) still beats every manifest.
    #
    # Issue #91 capability gate: a manifest MCP server is EXECUTION —
    # transport=stdio exec's ``command``/``args`` as a child process with the
    # host environment, http/sse dials a plugin-chosen URL — so it is refused
    # unless the manifest opted in (stdio → capabilities.shell_exec, the same
    # flag [[contributes.hooks]] needs; http/sse → capabilities.net). The gate
    # MUST stay on this side of ``McpClientManager``: refusing after the
    # connect attempt would be refusing after the spawn. Refusals are printed
    # (never swallowed) and name only the plugin id + server name — ``env``
    # holds user tokens and is never echoed.
    #
    # The ALLOW path is printed too. Capabilities are per-manifest, so a pack
    # granted shell_exec for its hook also gets a stdio MCP spawn from the
    # same manifest for free; no other surface in the product ever shows a
    # user a capability flag, so this notice is the only place that spawn
    # becomes observable.
    _manifest_mcp, _mcp_notices, _mcp_refusals = gate_manifest_mcp_contribs(
        reversed(_scanned_manifests)
    )
    for _notice in _mcp_notices:
        print(f"Notice: {_notice}", file=sys.stderr)
    for _refusal in _mcp_refusals:
        print(f"Warning: MCP server refused: {_refusal}", file=sys.stderr)
    if _manifest_mcp:
        mcp_contribs = _manifest_mcp + mcp_contribs
    mcp_manager: McpClientManager | None = None
    mcp_tools: list[AgentTool] = []
    if mcp_contribs:
        mcp_manager = McpClientManager(mcp_contribs)
        for conn_err in await mcp_manager.connect_all():
            print(f"Warning: MCP server failed: {conn_err}", file=sys.stderr)
        mcp_tools = await mcp_manager.collect_agent_tools()

    # Non-interactive untrusted notice for the surfaces the gate suppresses
    # SILENTLY — extensions (inside the factory via ``no_project_local``), skills
    # (``_resolve_skill_dirs``) and, since ADR-0196, agent profiles
    # (``discover_profiles``). Interactive users already saw/answered the A1
    # prompt, so only warn for headless runs.
    #
    # The wording is deliberately generic: this fires on the SAME predicate that
    # ``has_trust_requiring_project_resources`` widened to include
    # ``.aelix/agents``, so naming only ``.aelix/extensions`` here would report
    # the wrong resource for an agents-only project. (``.aelix/mcp.json`` keeps
    # its own targeted notice above, which is emitted only when contribs were
    # actually dropped.)
    if (
        not project_trusted
        and app_mode != "interactive"
        and has_trust_requiring_project_resources(Path(cwd))
    ):
        print(
            "Notice: project-local .aelix resources (extensions, skills, agent "
            "profiles) skipped in an untrusted directory; pass --approve to "
            "trust.",
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
    #
    # ADR-0196 — held in a MUTABLE holder rather than a plain local. The dirs are
    # stable for the process only until ``/agents use`` swaps identity: a profile
    # can add ``skills:`` paths or set ``inherit_skills: false``, so the service
    # reloads and REPLACES ``skills_holder["result"]`` in place. The factory below
    # reads the holder on every (re)build, so the new set survives /new, /fork,
    # /resume and reload — which a captured local provably would not.
    skill_dirs = _resolve_skill_dirs(parsed, cwd, project_trusted)
    skills_holder: dict[str, Any] = {"result": load_skills(skill_dirs)}
    for diag in skills_holder["result"].diagnostics:
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
            # ADR-0197 — same hold-the-ref rule as ``permission_ext``: ONE
            # instance reaches every (re)build, so the live child registry and
            # the session consent memo survive /new, /fork, /resume and /reload.
            # ``None`` when delegation is off, which is the P2 default.
            agents_ext=agents_ext,
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
        # ADR-0196 — honor ``--no-builtin-tools`` (built-ins off, extension + MCP
        # tools on). ``active_tool_names`` is seeded BEFORE extensions register
        # their tools, so the only faithful expression is a POST-registration
        # filter, and this is the first point where it can be written: the
        # harness's ``__init__`` has already rebuilt the tool registry, so
        # ``state.tools`` holds app tools ∪ extension tools, and the MCP tools
        # arrived through ``opts.tools`` above.
        #
        # ``and not parsed.no_tools`` is MANDATORY, not defensive.
        # ``_resolve_active_tools`` returns ``[]`` for ``--no-tools``, and
        # ``_action_set_active_tools`` is explicitly NON-destructive (it records
        # the active filter, it does not drop tools), so ``state.tools`` still
        # holds the full registry — without this guard the filter below would
        # re-enable every extension and ``<server>__<tool>`` MCP tool the user
        # just killed.
        #
        # Names come from LIVE ``state.tools``, so the harness's raising
        # active-tool validator can never fire here (unlike the raw ``--tools``
        # path the issue #24-FU comment above warns about). Reload-safe too: the
        # reloaded build passes ``on_reload=True`` → unfiltered, this filter
        # re-applies, and ``AgentSessionRuntime.reload()`` step 6 then intersects
        # the pre-teardown active set (already built-in-free) with the rebuilt
        # registry plus the extension-tool union — built-ins are *app* tools
        # passed via ``options.tools``, never extension tools, so none can
        # reappear.
        if parsed.no_builtin_tools and not parsed.no_tools:
            names = [t.name for t in harness.state.tools if t.name not in ALL_TOOL_NAMES]
            if parsed.tools:
                # ``--no-builtin-tools --tools a,b`` is an INTERSECTION: the
                # allowlist still applies, it just cannot readmit a built-in.
                allow = set(parsed.tools)
                names = [n for n in names if n in allow]
            await harness.set_active_tools(names)
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
        # Re-apply the loaded skills on every (re)build (issue #12). Read through
        # the holder (ADR-0196) so a ``/agents use`` skill swap reaches rebuilds.
        harness.set_skills(skills_holder["result"].skills)
        return harness

    # ADR-0196 — the ``/agents list|show|use`` service. Built BEFORE the first
    # harness so it shares the SAME ``parsed`` object the factory closes over: a
    # live ``use`` overlays a profile onto that object in place, and every later
    # /new, /fork, /resume rebuild therefore inherits the switched identity. It
    # also holds the pristine ``profile_baseline`` (so a second ``use`` overlays
    # the ORIGINAL CLI intent, never the previous profile) and the mutable
    # ``skills_holder``.
    #
    # ``agents.service`` is the sibling deliverable of this wiring and may not
    # have landed yet; a missing module degrades to "/agents unavailable" rather
    # than a dead startup, matching how the ``[tui]`` extra is handled below.
    # Construction errors are NOT swallowed — only the import is guarded.
    async def _confirm_project_agent_in_session(profile: AgentProfile) -> bool:
        """Consent gate for a project-scoped ``/agents use`` (ADR-0196 review fix).

        The startup path prompts per identity (``_confirm_project_agent`` above)
        because directory trust is a yes-once decision ancestors inherit, and a
        project profile additionally WINS a ``name`` collision against the user's
        own. ``/agents use`` resolves through the very same tiers, so without this
        seam the in-session switch was the unguarded twin of a guarded flag: in a
        trusted directory, ``/agents use reviewer`` silently picked the repo's
        ``reviewer`` over ``~/.aelix/agent/agents/reviewer.md``.

        P1 answers only from ``--approve``, never by prompting.
        :func:`_prompt_one_shot_select` is a DEDICATED one-shot
        ``prompt_toolkit.Application`` built for the pre-``run_tui`` window; driving
        it while the REPL's own Application is running is not something P1 has a
        seam for. So this fails CLOSED and :meth:`AgentProfileService.use` raises
        with a message naming ``--agent`` (which does prompt) — the callback stays
        on the service so the TUI-native modal can replace it without touching the
        service's contract.
        """

        return parsed.project_trust_override is True

    agent_service: Any = None
    try:
        from aelix_coding_agent.agents.service import AgentProfileService
    except ImportError:
        pass
    else:
        agent_service = AgentProfileService(
            cwd=cwd,
            project_trusted=project_trusted,
            parsed=parsed,
            baseline=profile_baseline,
            skills_holder=skills_holder,
            model_registry=model_registry,
            active=active_profile,
            confirm_project=_confirm_project_agent_in_session,
        )
    # Forwarded to ``run_tui`` as a kwarg only when it exists: the TUI half of
    # ADR-0196 (the ``agent_service`` parameter + the ``/agents`` command) lands
    # with ``agents/service.py``, and passing an unknown keyword to the current
    # signature would TypeError. Collapses to a plain kwarg once both are in.
    agent_service_kwarg: dict[str, Any] = (
        {"agent_service": agent_service} if agent_service is not None else {}
    )

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
                # ADR-0196 — the /agents service (see its construction above for
                # why this is conditional rather than a plain kwarg).
                **agent_service_kwarg,
            )

        if app_mode == "rpc":
            from aelix_coding_agent.modes import run_rpc_mode

            await run_rpc_mode(
                harness,
                runtime_host=runtime,
                harness_factory=_harness_factory,
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
