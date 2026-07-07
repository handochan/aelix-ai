"""Pi parity: ``cli/args.ts`` (354 LOC hand-rolled linear parser).

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-386). Hand-rolled — NOT argparse,
NOT click. Pi parses 30+ optional flags in a single linear ``for`` loop
with manual lookahead. Three features ``argparse`` / ``click`` cannot
cleanly express:

1. ``--print`` opportunistic positional eat (peek next token; swallow
   when it does NOT start with ``@`` / ``-``).
2. ``--list-models [search]`` ambiguous optional value.
3. Unknown ``--ext-flag value`` extension passthrough → recorded on
   :attr:`Args.unknown_flags`.

Pi line citation: ``cli/args.ts:1-354`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .config import APP_NAME, VERSION

if TYPE_CHECKING:
    from typing import TextIO

ModeLiteral = Literal["text", "json", "rpc"]
"""Pi parity: ``Args.mode`` union (``cli/args.ts``)."""

VALID_THINKING_LEVELS: tuple[str, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
"""Pi parity: ``cli/args.ts`` ``VALID_THINKING_LEVELS``."""

VALID_MODES: tuple[str, ...] = ("text", "json", "rpc")


@dataclass
class Args:
    """Pi parity: ``Args`` interface (``cli/args.ts``).

    Carries every flag from the Pi inventory. Defaults match Pi's
    ``undefined`` (Python :data:`None`) / ``false`` semantics. The
    :attr:`messages` list is mutated by :func:`build_initial_message`'s
    ``.shift()`` parity (Pi side-effect, P-388).
    """

    # Mode + IO
    mode: ModeLiteral = "text"
    """Pi parity: ``--mode <text|json|rpc>``. Default ``"text"``."""

    print_mode: bool = False
    """Pi parity: ``--print`` / ``-p``."""

    # Session
    continue_session: bool = False
    """Pi parity: ``--continue`` / ``-c``."""

    resume: bool = False
    """Pi parity: ``--resume`` / ``-r``."""

    resume_id: str | None = None
    """Optional ``--resume <id>`` session id/prefix. ``None`` = interactive
    picker (Pi ``--resume`` takes an optional value)."""

    no_session: bool = False
    """Pi parity: ``--no-session``."""

    session: str | None = None
    """Pi parity: ``--session <path>``."""

    fork: str | None = None
    """Pi parity: ``--fork <entry_id>``."""

    session_dir: str | None = None
    """Pi parity: ``--session-dir <path>``."""

    # Model
    provider: str | None = None
    """Pi parity: ``--provider <id>``."""

    model: str | None = None
    """Pi parity: ``--model <id>``."""

    models: list[str] = field(default_factory=list)
    """Pi parity: ``--models <csv>`` — comma-split."""

    api_key: str | None = None
    """Pi parity: ``--api-key <key>``."""

    thinking: str | None = None
    """Pi parity: ``--thinking <off|minimal|low|medium|high|xhigh>``."""

    # Prompt
    system_prompt: str | None = None
    """Pi parity: ``--system-prompt <text>``."""

    append_system_prompt: list[str] = field(default_factory=list)
    """Pi parity: ``--append-system-prompt <text>`` (repeatable)."""

    # Tools / Extensions
    no_tools: bool = False
    """Pi parity: ``--no-tools`` / ``-nt``."""

    no_builtin_tools: bool = False
    """Pi parity: ``--no-builtin-tools`` / ``-nbt``."""

    tools: list[str] = field(default_factory=list)
    """Pi parity: ``--tools <csv>``."""

    extensions: list[str] = field(default_factory=list)
    """Pi parity: ``--extension <name>`` / ``-e`` (repeatable)."""

    no_extensions: bool = False
    """Pi parity: ``--no-extensions`` / ``-ne``."""

    project_trust_override: bool | None = None
    """Pi parity: ``--approve`` / ``-a`` (True), ``--no-approve`` / ``-na``
    (False) — Sprint P0 #10 Project Trust (``args.ts:180-183``).

    :data:`None` = no override (resolve via the trust store / prompt /
    deny-by-default); :data:`True` = trust project-local ``.aelix``
    resources for this run; :data:`False` = ignore them for this run.
    Short-circuits :func:`resolve_project_trusted` (no prompt, no
    persistence)."""

    skills: list[str] = field(default_factory=list)
    """Pi parity: ``--skill <name>`` (repeatable)."""

    no_skills: bool = False
    """Pi parity: ``--no-skills`` / ``-ns``."""

    prompt_templates: list[str] = field(default_factory=list)
    """Pi parity: ``--prompt-template <name>`` (repeatable)."""

    no_prompt_templates: bool = False
    """Pi parity: ``--no-prompt-templates`` / ``-np``."""

    themes: list[str] = field(default_factory=list)
    """Pi parity: ``--theme <name>`` (repeatable)."""

    no_themes: bool = False
    """Pi parity: ``--no-themes``."""

    no_context_files: bool = False
    """Pi parity: ``--no-context-files`` / ``-nc``."""

    # Misc
    export: str | None = None
    """Pi parity: ``--export <path>``."""

    list_models: str | bool | None = None
    """Pi parity: ``--list-models [search]``.

    :data:`None` = absent, :data:`True` = no pattern supplied,
    :class:`str` = pattern.
    """

    verbose: bool = False
    """Pi parity: ``--verbose``."""

    offline: bool = False
    """Pi parity: ``--offline``."""

    help: bool = False
    """Pi parity: ``--help`` / ``-h``."""

    version: bool = False
    """Pi parity: ``--version`` / ``-v``."""

    # Always-present collections
    messages: list[str] = field(default_factory=list)
    """Pi parity: plain positional args (Pi ``messages``).

    Side-effect: :func:`build_initial_message` mutates this list via
    ``.pop(0)`` to mirror Pi's ``.shift()`` semantics (P-388).
    """

    file_args: list[str] = field(default_factory=list)
    """Pi parity: ``@file`` positional args (Pi ``fileArgs``)."""

    unknown_flags: dict[str, str | bool] = field(default_factory=dict)
    """Pi parity: unknown ``--ext-flag`` passthrough (Pi
    ``unknownFlags: Map<string, boolean | string>``)."""

    diagnostics: list[dict[str, str]] = field(default_factory=list)
    """Pi parity: ``diagnostics: Array<{type, message}>``.

    Each entry is ``{"type": "error" | "warning", "message": "..."}``.
    """


def parse_args(argv: list[str]) -> Args:
    """Pi parity: ``parseArgs`` (``cli/args.ts``).

    Hand-rolled linear loop with manual lookahead. Per P-386 the
    ``argparse`` / ``click`` ecosystems cannot cleanly express the
    three Pi-specific features (opportunistic positional eat,
    ambiguous optional ``--list-models`` value, unknown extension
    flag passthrough), so Aelix mirrors Pi byte-for-byte.
    """

    parsed = Args()
    i = 0
    n = len(argv)
    while i < n:
        arg = argv[i]
        if arg in ("--help", "-h"):
            parsed.help = True
        elif arg in ("--version", "-v"):
            parsed.version = True
        elif arg == "--mode":
            if i + 1 < n:
                mode_val = argv[i + 1]
                if mode_val in VALID_MODES:
                    parsed.mode = mode_val  # type: ignore[assignment]
                else:
                    parsed.diagnostics.append(
                        {
                            "type": "error",
                            "message": f"Invalid --mode value: {mode_val}",
                        }
                    )
                i += 1
            else:
                parsed.diagnostics.append(
                    {"type": "error", "message": "--mode requires a value"}
                )
        elif arg in ("--print", "-p"):
            parsed.print_mode = True
            # Pi parity: ``args.ts:123-129`` opportunistic positional eat.
            # Peek the next token — swallow as a message UNLESS it begins
            # with ``@`` (file arg). Flags (``-``) are excluded BUT the
            # ``---`` triple-dash escape (P-396) lets messages that
            # legitimately start with ``---`` pass through positionally.
            if i + 1 < n:
                next_tok = argv[i + 1]
                if not next_tok.startswith("@") and (
                    not next_tok.startswith("-") or next_tok.startswith("---")
                ):
                    parsed.messages.append(next_tok)
                    i += 1
        elif arg in ("--continue", "-c"):
            parsed.continue_session = True
        elif arg in ("--resume", "-r"):
            parsed.resume = True
            # Optional session id/prefix: opportunistically swallow the next
            # token as the id UNLESS it is a flag (``-``) or a ``@file``
            # positional (mirrors the ``--print`` peek). ``-r`` with no id →
            # interactive picker.
            if i + 1 < n:
                next_tok = argv[i + 1]
                if next_tok and not next_tok.startswith(("-", "@")):
                    parsed.resume_id = next_tok
                    i += 1
        elif arg == "--provider":
            if i + 1 < n:
                parsed.provider = argv[i + 1]
                i += 1
        elif arg == "--model":
            if i + 1 < n:
                parsed.model = argv[i + 1]
                i += 1
        elif arg == "--models":
            if i + 1 < n:
                parsed.models = [
                    s.strip() for s in argv[i + 1].split(",") if s.strip()
                ]
                i += 1
        elif arg == "--api-key":
            if i + 1 < n:
                parsed.api_key = argv[i + 1]
                i += 1
        elif arg == "--system-prompt":
            if i + 1 < n:
                parsed.system_prompt = argv[i + 1]
                i += 1
        elif arg == "--append-system-prompt":
            if i + 1 < n:
                parsed.append_system_prompt.append(argv[i + 1])
                i += 1
        elif arg == "--no-session":
            parsed.no_session = True
        elif arg == "--session":
            if i + 1 < n:
                parsed.session = argv[i + 1]
                i += 1
        elif arg == "--fork":
            if i + 1 < n:
                parsed.fork = argv[i + 1]
                i += 1
        elif arg == "--session-dir":
            if i + 1 < n:
                parsed.session_dir = argv[i + 1]
                i += 1
        elif arg in ("--no-tools", "-nt"):
            parsed.no_tools = True
        elif arg in ("--no-builtin-tools", "-nbt"):
            parsed.no_builtin_tools = True
        elif arg in ("--tools", "-t"):
            if i + 1 < n:
                parsed.tools = [
                    s.strip() for s in argv[i + 1].split(",") if s.strip()
                ]
                i += 1
        elif arg == "--thinking":
            if i + 1 < n:
                level = argv[i + 1]
                if level in VALID_THINKING_LEVELS:
                    parsed.thinking = level
                else:
                    parsed.diagnostics.append(
                        {
                            "type": "warning",
                            "message": f"Invalid --thinking level: {level}",
                        }
                    )
                i += 1
        elif arg in ("--extension", "-e"):
            if i + 1 < n:
                parsed.extensions.append(argv[i + 1])
                i += 1
        elif arg in ("--no-extensions", "-ne"):
            parsed.no_extensions = True
        elif arg in ("--approve", "-a"):
            # Pi parity: ``args.ts:180`` — trust project-local files this run.
            parsed.project_trust_override = True
        elif arg in ("--no-approve", "-na"):
            # Pi parity: ``args.ts:182`` — ignore project-local files this run.
            parsed.project_trust_override = False
        elif arg == "--skill":
            if i + 1 < n:
                parsed.skills.append(argv[i + 1])
                i += 1
        elif arg in ("--no-skills", "-ns"):
            parsed.no_skills = True
        elif arg == "--prompt-template":
            if i + 1 < n:
                parsed.prompt_templates.append(argv[i + 1])
                i += 1
        elif arg in ("--no-prompt-templates", "-np"):
            parsed.no_prompt_templates = True
        elif arg == "--theme":
            if i + 1 < n:
                parsed.themes.append(argv[i + 1])
                i += 1
        elif arg == "--no-themes":
            parsed.no_themes = True
        elif arg in ("--no-context-files", "-nc"):
            parsed.no_context_files = True
        elif arg == "--export":
            if i + 1 < n:
                parsed.export = argv[i + 1]
                i += 1
        elif arg == "--list-models":
            # Pi parity: ``args.ts:154-160`` ambiguous optional pattern.
            # Both ``-`` AND ``@`` are excluded (P-397) so ``@file`` args
            # are NOT eaten as the search pattern — they must remain in
            # ``file_args`` for downstream processing.
            if (
                i + 1 < n
                and not argv[i + 1].startswith("-")
                and not argv[i + 1].startswith("@")
            ):
                parsed.list_models = argv[i + 1]
                i += 1
            else:
                parsed.list_models = True
        elif arg == "--verbose":
            parsed.verbose = True
        elif arg == "--offline":
            parsed.offline = True
        elif arg.startswith("@"):
            # Pi parity: ``@file`` positional.
            parsed.file_args.append(arg[1:])
        elif arg.startswith("--"):
            # Pi parity: ``args.ts:167-180`` unknown extension flag.
            # Three sub-cases:
            #   1. ``--key=value`` — split on first ``=``.
            #   2. ``--key value`` — peek next token, swallow when it's
            #      neither a flag (``-``) nor a file arg (``@``); the
            #      ``@`` exclusion (P-398) keeps ``@file`` arguments in
            #      ``file_args`` instead of consuming them as the value.
            #   3. ``--key`` (boolean) — record as True.
            if "=" in arg:
                key, val = arg[2:].split("=", 1)
                parsed.unknown_flags[key] = val
            elif (
                i + 1 < n
                and not argv[i + 1].startswith("-")
                and not argv[i + 1].startswith("@")
            ):
                parsed.unknown_flags[arg[2:]] = argv[i + 1]
                i += 1
            else:
                parsed.unknown_flags[arg[2:]] = True
        elif arg.startswith("-") and len(arg) > 1:
            # Unknown short flag — Pi diagnostic.
            parsed.diagnostics.append(
                {"type": "error", "message": f"Unknown short flag: {arg}"}
            )
        else:
            # Plain positional → message.
            parsed.messages.append(arg)
        i += 1
    return parsed


def print_help(out: TextIO | None = None) -> None:
    """Pi parity: ``printHelp`` (``cli/args.ts``).

    Emits ``APP_NAME``-substituted help text. Aelix-additive note:
    extension-supplied help flags are NOT yet enumerated (Pi's
    ``extensionFlags`` plumbing depends on the extension loader, which
    is wired through the harness — out of scope for Sprint 6h₆).

    ``out`` defaults to the *current* :attr:`sys.stdout` (resolved at
    call time, NOT at definition time) so test harnesses that swap
    stdout via ``capsys`` capture the help text correctly.
    """

    stream: TextIO = out if out is not None else sys.stdout

    text = f"""Usage: {APP_NAME} [options] [@file ...] [message ...]

Modes:
  (default)             Interactive mode (Phase 5b — TUI carry-forward)
  --print, -p [msg]     One-shot print mode (stdout response)
  --mode text           Same as --print
  --mode json           Line-delimited JSON event stream
  --mode rpc            Headless JSONL command/response protocol

Session:
  --continue, -c        Continue the most recent session
  --resume, -r [<id>]   Resume a session by id/prefix, or pick interactively
  --no-session          Run with an in-memory session (not persisted)
  --session <path>      Open a specific session file
  --fork <entry_id>     Fork a session at the given entry
  --session-dir <path>  Override the sessions root directory

Model:
  --provider <id>       Provider id (e.g., anthropic, openai)
  --model <id>          Model id
  --models <csv>        Comma-separated model id list
  --api-key <key>       Inline API key
  --thinking <level>    off | minimal | low | medium | high | xhigh

Prompt:
  --system-prompt <text>          Replace the default system prompt
  --append-system-prompt <text>   Append to the system prompt (repeatable)

Tools / Extensions:
  --no-tools, -nt                 Disable all tools
  --no-builtin-tools, -nbt        Disable built-in tools only
  --tools, -t <csv>               Restrict tools to this comma-separated list
  --extension, -e <name>          Load extension (repeatable)
  --no-extensions, -ne            Disable all extensions
  --approve, -a                   Trust project-local files for this run
  --no-approve, -na               Ignore project-local files for this run
  --skill <name>                  Enable skill (repeatable)
  --no-skills, -ns                Disable all skills
  --prompt-template <name>        Enable prompt template (repeatable)
  --no-prompt-templates, -np      Disable all prompt templates
  --theme <name>                  Enable theme (repeatable)
  --no-themes                     Disable all themes
  --no-context-files, -nc         Skip auto-discovered AGENTS.md context

Misc:
  --export <path>                 Export the current session to HTML
  --list-models [pattern]         List available models (optional filter)
  --verbose                       Verbose logging
  --offline                       Disable startup network operations (same as PI_OFFLINE=1)
  --help, -h                      Show this help
  --version, -v                   Show version ({VERSION})

Subcommands:
  extension install <target>      Install an extension via pip (path | git-url |
                                  package[==version]); --yes --index-url --offline
  extension source add <src>      Register an install source (path | git-url |
                                  index-url); register-only (add ≠ install)
  extension source list|remove    List / remove registered sources
  extension list                  List installed extensions (entry-point ledger)
  extension update [<name>]       Reinstall recorded source(s) with --upgrade
  extension remove <name>         Uninstall the extension's distribution
  extension keygen                Generate a publisher Ed25519 signing key (#67)
  extension sign <artifact>       Write a detached .aelixsig signature (--key <id>)
  extension trust add|list|       Manage trusted verification keys
    remove|revoke                 (install --require-signature to enforce provenance)

File arguments:
  @<path>                         Inline file content into the first message
"""
    print(text, file=stream)


__all__ = [
    "Args",
    "ModeLiteral",
    "VALID_MODES",
    "VALID_THINKING_LEVELS",
    "parse_args",
    "print_help",
]
