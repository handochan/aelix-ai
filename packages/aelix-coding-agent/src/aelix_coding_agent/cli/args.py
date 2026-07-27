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

from aelix_coding_agent.builtin.permission_mode import PermissionMode

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

VALID_PERMISSION_MODES: tuple[str, ...] = tuple(m.value for m in PermissionMode)
"""Aelix-original (ADR-0197 §(e)) — accepted ``--permission-mode`` values.

DERIVED from :class:`PermissionMode` instead of re-spelled, so the parser can
never accept a posture the gate does not know (or reject one it does): a new
cycle entry lands in both places at once. Mirrors the
:data:`VALID_THINKING_LEVELS` / :data:`VALID_MODES` house pattern.
"""


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

    system_prompt_file: str | None = None
    """Aelix-original (ADR-0196): ``--system-prompt-file <path>`` — replace
    semantics. ``--system-prompt`` takes a LITERAL string (the parser branch
    below), which overflows ``ARG_MAX`` and leaks in ``ps`` for a long body —
    and an agent profile's body routinely IS a long body.

    Normalized into :attr:`system_prompt` by ``entry._apply_prompt_files``;
    the literal ``--system-prompt`` always wins when both are supplied."""

    append_system_prompt_files: list[str] = field(default_factory=list)
    """Aelix-original (ADR-0196): ``--append-system-prompt-file <path>``
    (repeatable). Normalized into :attr:`append_system_prompt` by
    ``entry._apply_prompt_files``, landing AFTER the string-appends."""

    # Agent profile (aelix-original, ADR-0196)
    agent: str | None = None
    """Aelix-original (ADR-0196): ``--agent <name>`` — whole-session identity.

    Resolved against ``~/.aelix/agent/agents/`` then ``<cwd>/.aelix/agents/``
    (the latter only when the project is trusted). Mutually exclusive with
    :attr:`agent_file`; an unresolvable name is fatal, never a warning —
    running under an identity other than the one requested is a safety
    problem."""

    agent_file: str | None = None
    """Aelix-original (ADR-0196): ``--agent-file <path>``.

    Scope is decided by resolved-path containment, NOT by this flag, so a
    path under ``<cwd>`` is still a *project* profile and still faces the
    trust gate + confirmation prompt."""

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

    permission_mode: str | None = None
    """Aelix-original (ADR-0197 §(e)) — seeds :class:`PermissionPosture` at
    startup. :data:`None` keeps the DEFAULT posture. A bogus value WARNS via
    :attr:`diagnostics` and drops (mirrors ``--thinking``, ``args.py:413-430``)
    — a typo must not abort a session already launching.

    SECURITY: a delegated child receives this from the spawner, which computes
    it with ``aelix_agents.posture.child_permission_mode`` — a CLAMP against the
    parent's live posture — optionally raised by ONE explicit human answer at
    the spawn-time consent dialog, never above ``auto-accept-edits`` and never
    for a project-scoped profile (ADR-0197 §(e)/§(i)). The whole child-authority
    guarantee is therefore argv-shaped, which is why
    ``tests/cli/test_permission_mode_flag.py`` pins that this exact spelling
    PARSES: the unknown-``--``-flag branch below swallows a typo into
    :attr:`unknown_flags` with NO diagnostic (contrast the ``Unknown short
    flag`` error), so a renamed flag would silently hand the child an
    auto-approving DEFAULT posture (review finding B10)."""

    agents_override: bool | None = None
    """Aelix-original (ADR-0197 §(b)) — ``--agents`` → :data:`True`,
    ``--no-agents`` → :data:`False`, absent → :data:`None`.

    :data:`None` falls through to the global ``[features] agents`` setting
    (default :data:`False` in P2). The spawner passes ``--no-agents`` to every
    child so a delegated agent cannot itself delegate even if the user's global
    flag is on — belt to the ``MAX_SUBAGENT_DEPTH`` guard, not a replacement."""

    project_trust_override: bool | None = None
    """Pi parity: ``--approve`` / ``-a`` (True), ``--no-approve`` / ``-na``
    (False) — Sprint P0 #10 Project Trust (``args.ts:180-183``).

    :data:`None` = no override (resolve via the trust store / prompt /
    deny-by-default); :data:`True` = trust project-local ``.aelix``
    resources for this run; :data:`False` = ignore them for this run.
    Short-circuits :func:`resolve_project_trusted` (no prompt, no
    persistence)."""

    skills: list[str] = field(default_factory=list)
    """Pi parity: ``--skill <path>`` (repeatable).

    Aelix divergence (ADR-0196): entries are **paths**, not installable
    names. ``entry._resolve_skill_dirs`` (``cli/entry.py:547-577``) resolves
    each entry against ``cwd`` when relative and scans it as a skill
    directory (or the parent of a ``SKILL.md``); Aelix has no skill package
    manager, so a bare *name* silently resolves to a non-existent directory
    that :func:`load_skills` skips without a diagnostic. The help text said
    ``<name>`` until ADR-0196 and that lie is what produced the agent-profile
    spec's ``skills: [python-recon]`` example."""

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

    provided: set[str] = field(default_factory=set)
    """Aelix-original (ADR-0196): names of :class:`Args` fields the user
    EXPLICITLY set on the command line.

    Required because every other field is a plain default with no "unset"
    sentinel (``--tools ''`` yields ``[]``, indistinguishable from "not
    supplied"; ``--no-tools`` yields ``False``, ditto), so the rule "an
    explicit CLI flag always wins over an agent profile" is otherwise
    unenforceable — the overlay cannot tell a user's choice from a default.

    Only branches an agent profile can overlay are recorded, plus the two
    prompt-file flags and ``--agent`` / ``--agent-file`` themselves.
    ``--thinking`` is recorded ONLY when the level validates, matching the
    parser's warn-and-drop behaviour: a rejected level leaves the field at
    its default, so claiming the user "provided" it would let a bad flag
    veto the profile's valid one.

    NOT part of the parsed-value surface — provenance, not a value. Exclude
    it from whole-``Args`` comparisons."""

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
                parsed.provided.add("provider")
                i += 1
        elif arg == "--model":
            if i + 1 < n:
                parsed.model = argv[i + 1]
                parsed.provided.add("model")
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
                parsed.provided.add("system_prompt")
                i += 1
        elif arg == "--append-system-prompt":
            if i + 1 < n:
                parsed.append_system_prompt.append(argv[i + 1])
                parsed.provided.add("append_system_prompt")
                i += 1
        elif arg == "--system-prompt-file":
            # Aelix-original (ADR-0196). Sibling of ``--system-prompt``: the
            # file's TEXT is normalized into ``system_prompt`` downstream
            # (``entry._apply_prompt_files``), not here, because parse_args
            # must stay pure — it has no cwd, no stderr contract and no way
            # to fail a run.
            if i + 1 < n:
                parsed.system_prompt_file = argv[i + 1]
                parsed.provided.add("system_prompt_file")
                i += 1
        elif arg == "--append-system-prompt-file":
            # Aelix-original (ADR-0196), repeatable like its string twin.
            if i + 1 < n:
                parsed.append_system_prompt_files.append(argv[i + 1])
                parsed.provided.add("append_system_prompt_files")
                i += 1
        elif arg == "--agent":
            # Aelix-original (ADR-0196) — named agent profile. Resolution,
            # the trust gate and the confirmation prompt all live in
            # ``cli/entry.py``; the parser only records the request.
            if i + 1 < n:
                parsed.agent = argv[i + 1]
                parsed.provided.add("agent")
                i += 1
        elif arg == "--agent-file":
            # Aelix-original (ADR-0196) — profile at an explicit path.
            if i + 1 < n:
                parsed.agent_file = argv[i + 1]
                parsed.provided.add("agent_file")
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
            parsed.provided.add("no_tools")
        elif arg in ("--no-builtin-tools", "-nbt"):
            parsed.no_builtin_tools = True
            parsed.provided.add("no_builtin_tools")
        elif arg in ("--tools", "-t"):
            if i + 1 < n:
                parsed.tools = [
                    s.strip() for s in argv[i + 1].split(",") if s.strip()
                ]
                # Recorded even when the CSV is empty (``--tools ''`` → []):
                # provenance is about the FLAG, not the value, and that empty
                # case is precisely the one a plain default cannot express.
                parsed.provided.add("tools")
                i += 1
        elif arg == "--thinking":
            if i + 1 < n:
                level = argv[i + 1]
                if level in VALID_THINKING_LEVELS:
                    parsed.thinking = level
                    # Only a VALID level counts as provided (ADR-0196) — the
                    # invalid branch below warns and drops, leaving the field
                    # at its default, so recording it would let a typo veto an
                    # agent profile's valid ``thinking:``.
                    parsed.provided.add("thinking")
                else:
                    parsed.diagnostics.append(
                        {
                            "type": "warning",
                            "message": f"Invalid --thinking level: {level}",
                        }
                    )
                i += 1
        elif arg == "--permission-mode":
            # Aelix-original (ADR-0197 §(e)). Seeds the startup posture. The
            # spawner ALWAYS passes this explicitly to a delegated child, so
            # this branch is the enforcement point for the child-authority
            # clamp — see the SECURITY note on ``Args.permission_mode``.
            if i + 1 < n:
                value = argv[i + 1]
                if value in VALID_PERMISSION_MODES:
                    parsed.permission_mode = value
                    parsed.provided.add("permission_mode")
                else:
                    # Mirrors --thinking (args.py:423-429): warn + drop, never
                    # abort. A typo must not kill a session already launching,
                    # and must NOT record as "provided" — a rejected value
                    # leaves the field at its default, so claiming the user
                    # supplied it would let a bad flag veto a valid overlay.
                    parsed.diagnostics.append(
                        {
                            "type": "warning",
                            "message": f"Invalid --permission-mode: {value}",
                        }
                    )
                i += 1
        elif arg == "--agents":
            # Aelix-original (ADR-0197 §(b)) — run-scoped delegation override.
            # Precedence (applied in ``cli/entry.py``): ``--no-agents`` >
            # ``--agents`` > the global ``[features] agents`` setting.
            #
            # ``--no-agents`` IS STICKY, and this guard is what makes that
            # documented precedence true (P2 review, MEDIUM #11). The loop used
            # to assign in both branches, so the real rule was last-flag-wins:
            # measured, ``['--no-agents', '--agents']`` with the global setting
            # ON yielded ``enabled=True`` where both this comment and
            # ``entry.py::_agents_delegation_enabled`` promise ``False``. Not
            # reachable through the spawner — ``build_child_argv`` appends
            # ``--no-agents`` last and no profile field can emit ``--agents`` —
            # but a wrapper script or shell alias pinning ``--no-agents`` could
            # be silently re-opened by a later ``--agents``, and OFF is the
            # direction that must win a contradiction.
            if parsed.agents_override is not False:
                parsed.agents_override = True
        elif arg == "--no-agents":
            parsed.agents_override = False
        elif arg in ("--extension", "-e"):
            if i + 1 < n:
                parsed.extensions.append(argv[i + 1])
                parsed.provided.add("extensions")
                i += 1
        elif arg in ("--no-extensions", "-ne"):
            parsed.no_extensions = True
            parsed.provided.add("no_extensions")
        elif arg in ("--approve", "-a"):
            # Pi parity: ``args.ts:180`` — trust project-local files this run.
            parsed.project_trust_override = True
        elif arg in ("--no-approve", "-na"):
            # Pi parity: ``args.ts:182`` — ignore project-local files this run.
            parsed.project_trust_override = False
        elif arg == "--skill":
            if i + 1 < n:
                parsed.skills.append(argv[i + 1])
                parsed.provided.add("skills")
                i += 1
        elif arg in ("--no-skills", "-ns"):
            parsed.no_skills = True
            parsed.provided.add("no_skills")
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
            parsed.provided.add("no_context_files")
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
  --system-prompt-file <path>     Replace the system prompt from a file
  --append-system-prompt-file <path>
                                  Append to the system prompt from a file (repeatable)

Agent profiles:
  --agent <name>                  Run under a named agent profile
  --agent-file <path>             Run under a profile at an explicit path

Tools / Extensions:
  --no-tools, -nt                 Disable all tools
  --no-builtin-tools, -nbt        Disable built-in tools only
  --tools, -t <csv>               Restrict tools to this comma-separated list
  --extension, -e <path>          Load extension (repeatable)
  --no-extensions, -ne            Disable all extensions
  --approve, -a                   Trust project-local files for this run
  --no-approve, -na               Ignore project-local files for this run
  --permission-mode <mode>        default | auto-accept-edits | plan | yolo | auto
  --agents / --no-agents          Enable/disable agent delegation for this run
  --skill <path>                  Enable skill (repeatable)
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
    "VALID_PERMISSION_MODES",
    "VALID_THINKING_LEVELS",
    "parse_args",
    "print_help",
]
