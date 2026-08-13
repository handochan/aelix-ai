"""Coding-agent system prompt + AGENTS.md context discovery (Sprint 6h₁₁).

The CLI harness previously ran with an EMPTY ``system_prompt`` and zero ``tools``
— a bare chat model with no coding-agent identity (it could not read or edit
files and had no idea it was a coding agent). This module supplies:

- :func:`build_system_prompt` — the base coding-agent system prompt (identity +
  environment + tool-use guidance + the self-extension signpost), injected
  unless ``--system-prompt`` overrides.
- :func:`discover_context_files` — auto-discovered ``AGENTS.md`` project context
  (Pi ``--no-context-files`` / ``-nc`` gate), walked from the cwd up to the
  filesystem root and appended to the system prompt.

Tools themselves are wired in :func:`aelix_coding_agent.cli.entry._build_harness_options`
via :func:`aelix_coding_agent.tools.create_all_tools`.

SELF-EXTENSION SIGNPOST (issue #117). Measured against a freshly built
``0.1.0b1`` wheel in an empty directory: asked in natural language to add a
tool, the agent failed 2/2 and failed *confidently* — it invented a
``tools_definition.json`` manifest that exists nowhere in Aelix and announced
"the tool is now ready to use". Root cause: the complete system prompt a fresh
user receives was 1847 characters in which the word "extension" appeared ZERO
times, and the only worked example the demo GIF ever used was a hand-staged
``extension_guide.md`` that does not ship (``docs/assets/demo.tape:9-11,62``).
The README hero ("The agent extends itself") was therefore not deliverable to
an installed user. :func:`_extension_signpost` closes that: it names the
``setup(aelix)`` contract, gives both write targets as ABSOLUTE paths, and
points at two files that really ship inside the wheel so the model reads the
API instead of hallucinating one.

EVERY SENTENCE IN THAT BLOCK IS LOAD-BEARING, so it is held to a harder
standard than prose elsewhere: an overclaim in a doc misleads a reader who can
check it, but an overclaim HERE is acted on by the model. The first shipped
draft asserted that a ``.aelix/`` write "always prompts ... even in
auto-accept-edits"; driving the real permission ladder showed it prompts in 3
of 15 posture × surface cells (YOLO returns before the check; a headless run
has no approver). Each claim now carries the probe that establishes it in the
comment beside it — see :func:`_extension_signpost`. Rewordings must re-run the
probe, not merely sound safer.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# ``skills_prompt`` is stdlib-only at module scope (its only import is a
# ``TYPE_CHECKING``-guarded ``collections.abc``), so this cannot cycle back
# through ``cli``. Verified by importing this module first in a fresh
# interpreter::
#
#     python -c "import aelix_coding_agent.cli.agent_context"   -> EXIT=0
#     python -c "import aelix_coding_agent.cli.skills_prompt"   -> EXIT=0
#
# NOT re-derived here on purpose: ``_escape_xml``'s replacement ORDER is
# load-bearing (``&`` first, or the ampersands the later replacements introduce
# get double-escaped) and a second copy is a second thing to get wrong. It
# already covers ``"`` and ``'``, which is what makes it safe for an ATTRIBUTE
# value and not just for element text — see :func:`discover_context_files`.
from .skills_prompt import _escape_xml

_CONTEXT_FILENAME = "AGENTS.md"
_MAX_CONTEXT_BYTES = 32_768

# C0 + DEL + C1, deleted from any path before it is PRINTED (#121 / ADR-0217).
#
# POSIX permits every byte but ``/`` and NUL in a path component, so a directory
# name that arrives with a ``git clone`` steers the terminal of anyone who runs
# aelix inside it. The two warnings below interpolate an absolute path, and they
# fire BEFORE any TUI exists — on the ``cli/entry.py`` discovery call, in every
# mode including ``-p`` / ``--mode json`` / ``--mode rpc``. Measured on the real
# function with a directory named ``proj\x1b]0;pwned\x07\x1b[31mZ``::
#
#     control chars reaching stderr: ['0x7', '0x1b']
#     Warning: AGENTS.md truncated ... (/tmp/.../proj\x1b]0;pwned\x07\x1b[31mZ/AGENTS.md)
#
# i.e. an unterminated OSC title-set sequence, which then eats every byte the
# terminal prints after it until some terminator turns up.
#
# DUPLICATED, not imported. ``tui/commands.py`` exposes ``sanitize_for_terminal``
# with this identical table, but ``cli`` must not import ``tui`` — and this
# module is reached by headless runs that never build a TUI at all. A fourth
# copy of the table is the cost of the band rule; the repo already carries three
# (``commands._sanitize_child_field``, ``consent._sanitize_field``,
# ``panel._flatten``).
#
# DELETION, not escaping, matching those three: what survives ``\x1b[8m`` is the
# inert literal ``[8m``, which renders as itself and reads as visibly odd. The
# range covers ``\x9b``, the one-byte CSI, which an ESC-only filter would miss.
_CONTROL_KILL = dict.fromkeys([*range(0x20), 0x7F, *range(0x80, 0xA0)])


def _safe_path(path: Path) -> str:
    """One path, de-fanged for printing. See :data:`_CONTROL_KILL`."""

    return str(path).translate(_CONTROL_KILL)


# Pi's project-context fence, verbatim from ``system-prompt.ts:144-152`` on pi
# main (identical at ``:53-61``, the ``customPrompt`` branch; introduced by pi
# e2fd651e → v0.75.0 and 7577d3b8 → v0.75.4, present in every release since,
# currently v0.84.1). Pi writes::
#
#     prompt += "\n\n<project_context>\n\n";
#     prompt += "Project-specific instructions and guidelines:\n\n";
#     for (const { path: filePath, content } of contextFiles) {
#         prompt += `<project_instructions path="${filePath}">\n${content}\n</project_instructions>\n\n`;
#     }
#     prompt += "</project_context>\n";
#
# Pi's leading ``"\n\n"`` is deliberately NOT reproduced: this function returns
# an append CHUNK and the harness already joins chunks with ``"\n\n"``
# (``harness/core.py:572-573``). Emitting it here would double the gap.
_FENCE_OPEN = "<project_context>\n\nProject-specific instructions and guidelines:\n\n"
_FENCE_CLOSE = "</project_context>\n"
_INSTRUCTIONS_CLOSE = "\n</project_instructions>\n\n"

# A truncation can land in the middle of an entity ``_escape_xml`` just wrote
# (``&am``). Escaped text contains no bare ``&`` — every ``&`` starts an entity
# that ends in ``;`` — so a trailing ``&`` + 0-4 letters with no ``;`` is
# necessarily a cut one, and dropping it is not lossy in any other case. The
# longest entity in play is ``&apos;`` / ``&quot;`` (``&`` + 4 letters + ``;``),
# hence ``{0,4}``. Anchored at ``$`` and requiring no ``;``, so a COMPLETE
# ``&amp;`` at the end is left alone (``sub("", "a&amp;")`` -> ``a&amp;``).
#
# Not defensive-only: a 40000-``&`` AGENTS.md truncated against the 32768-byte
# budget lands mid-entity in 4 of the 6 possible byte alignments (varied by
# padding the file 0-5 bytes), and in none of the 6 does the emitted body end
# in a partial entity. This is cosmetic rather than a safety property — the
# escape has already removed every ``<`` and ``>``, so a cut entity could not
# reopen markup — which is why the fence balance is enforced structurally
# below and not by this regex.
_PARTIAL_ENTITY_TAIL = re.compile(r"&[a-z]{0,4}$")

# Files that ship inside the wheel (verified against a built
# ``aelix_coding_agent-0.1.0b1-py3-none-any.whl``) and are worth READING before
# authoring an extension, as path parts relative to the ``aelix_coding_agent``
# package root. Kept as data so a rename shows up as a *missing* pointer
# (silently omitted, see :func:`_package_pointer`) rather than as a dead path in
# every user's prompt — and so the regression test can point one entry at a
# non-existent file and assert the omission.
_EXAMPLE_PARTS = ("examples", "echo", "echo.py")
_API_PARTS = ("extensions", "api.py")


def _package_pointer(*parts: str) -> str | None:
    """Absolute path to a file shipped inside this package, or ``None``.

    Resolved from the package location at CALL time so it is correct for an
    installed user (site-packages) as well as a source checkout. Returns
    :data:`None` unless the path is really a file — a signpost that names a
    path the reader cannot open is worse than no signpost, so callers omit
    missing pointers instead of emitting them dead.

    WHY ABSOLUTE FILE PATHS, NEVER AN IMPORT INCANTATION. An earlier draft of
    this docstring blamed ``PATH``, claiming the bash tool's environment "is
    only ``<agent dir>/bin``". That is FALSE:
    :func:`aelix_coding_agent.util.shell_env.get_shell_env` PREPENDS the bin
    dir to the INHERITED ``PATH`` (``shell_env.py:41-43``) — measured through
    the real bash tool, 43 entries with all 42 inherited ones intact.

    The true reason is the INTERPRETER, not the ``PATH``. ``install.sh:165``
    installs via ``uv tool install``, which puts Aelix in a per-tool
    virtualenv; only the ``aelix`` console script lands on ``PATH``, and its
    shebang points into that venv. A bare ``python`` in a shell still resolves
    through ``PATH`` to a DIFFERENT interpreter whose ``sys.path`` excludes the
    tool venv's site-packages. Measured by driving the real bash tool against a
    real ``uv tool install`` of a throwaway package::

        which mypkg              -> <uv bin>/mypkg          # script: found
        mypkg                    -> runs under <uv tools>/mypkg/bin/python
        which python             -> /usr/.../bin/python      # a DIFFERENT one
        python -c "import mypkg" -> ModuleNotFoundError   EXIT=1

    So the prompt hands the model absolute file paths for its ``read`` tool.
    The conclusion the old docstring reached still holds; its reason did not.
    """

    try:
        path = Path(__file__).resolve().parents[1].joinpath(*parts)
        return str(path) if path.is_file() else None
    except (OSError, IndexError):  # pragma: no cover — defensive
        return None


def _extension_signpost(cwd_abs: str) -> str:
    """The "Extending yourself" block (issue #117).

    ``cwd_abs`` must already be absolute. The global target is derived from
    :func:`aelix_coding_agent.cli.config.get_agent_dir` **at call time**, so
    ``$AELIX_CODING_AGENT_DIR`` set after import is still honoured — and so the
    emitted path is the directory the loader actually scans
    (``get_agent_dir()/extensions`` → ``~/.aelix/agent/extensions``, see
    ``extensions/loader.py:455-457``), never the plausible-but-wrong
    ``~/.aelix/extensions``.

    WHY THE GLOBAL TARGET IS LISTED FIRST (adversarial review, MAJOR 2). The
    project-local tier is TRUST-GATED and fails **silently**. Measured with the
    real loader against the exact emitted paths (one project-local + one global
    extension on disk)::

        no_project_local=False -> extensions=2  errors=[]
        no_project_local=True  -> extensions=1  errors=[]   # local one GONE

    No error, no warning — precisely the confident-failure mode this block
    exists to kill. ``cli/entry.py:928`` passes
    ``no_project_local=not project_trusted``, so an untrusted project drops the
    file the agent just wrote while still reporting success. The global tier
    (``loader.py:455-457``) is not trust-gated at all, so it is the target that
    works in the most cases and is therefore advertised first.

    NOT "always loaded", deliberately. ``--no-extensions`` / ``-ne`` sets
    ``no_discovery=True`` (``entry.py:927``), which skips BOTH directory tiers.
    The wording says "no trust gate" — the property actually measured — rather
    than an "always" that flag would falsify.

    ``resolve_project_trusted`` short-circuits to ``True`` when the project has
    no trust-requiring resources (``project_trust.py:526-527``), so in a FRESH
    project the local path does work for the session that creates it; it is the
    next run (which now sees a non-empty ``.aelix/extensions/``) that must
    answer the trust prompt. Both cases are covered by "loaded only if this
    project is trusted".
    """

    # Local import: ``cli.config`` is stdlib-only at module scope, and Python
    # imports the submodule even while ``cli/__init__`` is still executing, so
    # this cannot cycle. Kept local anyway to keep import cost off the path of
    # callers that never build a prompt.
    from .config import get_agent_dir

    lines = [
        # SCOPED HEADER (review MINOR 6): the block is emitted on EVERY turn and
        # sits last in the base prompt, i.e. in the most recency-weighted slot.
        # Unconditional "here is how to extend yourself" there biases the model
        # toward writing an extension when the user asked for ordinary work.
        # One clause naming the trigger costs ~11 words and removes that pull.
        "Extending yourself (when the user asks to add a tool/command/hook to "
        "Aelix itself):\n",
        # ``register_flag`` is DELIBERATELY not advertised. The registration call
        # itself works, but its user-facing half does not exist: issue #92 —
        # extension flags never reach the first-build runtime because
        # ``parsed.unknown_flags`` is not threaded into ``flag_values``, so no CLI
        # invocation can ever set one. Naming a surface the user cannot drive is
        # the same overclaim this block exists to remove. Re-add it when #92 lands.
        "- You add your own tools/commands in plain Python, not a plugin format: "
        "ONE file defining `def setup(aelix): ...` that calls "
        "`aelix.register_tool(...)` (also register_command and "
        '`aelix.on("tool_call", handler)` for hooks). Aelix imports and RUNS IT IN '
        "THIS PROCESS — no manifest, no JSON, no build step, nothing to install. "
        "Never invent a config format for it.\n",
        # MINOR 4: ``tools/write.py:78-83`` mkdirs the parent (``parents=True,
        # exist_ok=True``) before EVERY write, so "mkdir if missing" only bought
        # a redundant bash call. Stated as a fact about the tool instead.
        "- Write it to ONE absolute path (write creates missing dirs):\n",
        f"  - every project, no trust gate: {os.path.join(get_agent_dir(), 'extensions', '<name>.py')}\n",
        # NOTE each label carries NO ``": "`` of its own — the tests (and any
        # future parser) recover the emitted path with ``split(": ", 1)[1]``.
        "  - this project only, and only if the project is trusted "
        f"(untrusted = skipped silently): {os.path.join(cwd_abs, '.aelix', 'extensions', '<name>.py')}\n",
    ]

    pointers: list[str] = []
    example = _package_pointer(*_EXAMPLE_PARTS)
    if example:
        # The ONLY read-it-whole target: 75 lines, well inside the 50KB cap, and
        # it contains a complete working ``setup(aelix)`` (``examples/echo/echo.py:55``).
        pointers.append(f"  - worked example, read this one: {example}\n")
    api = _package_pointer(*_API_PARTS)
    if api:
        # THE PATTERN. The original hint was ``grep 'def register_'`` — 10 hits,
        # NONE of them the hook surface, which is spelled ``def on(...)`` (the
        # typed overloads at ``extensions/api.py:1273-1600``). A model told
        # "hooks" exist and handed a grep that cannot find them invents a name.
        #
        # MIND THE PAREN. The obvious widening ``def (register_|on)\(`` is a
        # TRAP: ``\(`` binds to the whole alternation, so it demands ``(``
        # immediately after ``register_`` and matches NOTHING named
        # ``register_tool``. Measured on this file:
        #
        #     def register_            -> 10 hits (0 hooks)
        #     def (register_|on)\(     -> 38 hits (0 register_)   <-- the trap
        #     def (register_|on\()     -> 48 hits (10 + 38)       <-- shipped
        #
        # THE ``-E`` IS LOAD-BEARING (truth audit MAJOR 1). The pattern needs
        # alternation, so it is an ERE. Plain ``grep`` is BRE, where ``(`` is a
        # literal and ``\(`` opens a group — the emitted command therefore died
        # with ``grep: Unmatched ( or \(`` and EXIT=2 when run through the real
        # bash tool. ``-E`` costs three characters and makes the command the
        # model is handed actually run. Verified 48/10/38 identically in all
        # three engines the model can reach: real ``/bin/grep -E`` via the bash
        # tool, ripgrep via ``tools/grep.py:219``, and the Python ``re``
        # fallback at ``tools/grep.py:272-274``. The ``-E`` sits outside the
        # quotes so the quoted pattern is still copy-pastable verbatim into the
        # grep TOOL's ``pattern`` argument, which takes no flags.
        #
        # WHY "then read at the line it reports" AND NOT "READ THIS FILE"
        # (truth audit MAJOR 2). api.py is 2131 lines / 84KB and ``read``
        # truncates at ``DEFAULT_MAX_BYTES`` = 50KB (``tools/_truncate.py:12``).
        # A plain read through the real read tool returns:
        #
        #     [Showing lines 1-1183 of 2132 (50.0KB limit). Use offset=1184...]
        #
        # — a window whose only ``register_*`` hits are the ``ModelRegistry``
        # protocol's ``register_provider`` (:202, :220). ``register_tool``,
        # ``register_command`` and ``register_flag`` all live past :1655 and are
        # ALL absent, so "READ this first" was a dead instruction that silently
        # returned a plausible wrong answer. A baked-in ``offset=1655`` was
        # rejected: it works today but rots on the next edit to api.py, and
        # unlike a missing file (which ``_package_pointer`` drops) a stale
        # offset is undetectable — it returns a confident wrong window, the
        # exact failure mode this block exists to kill. Letting grep supply the
        # line number keeps the pointer correct by construction.
        # THE ``-n`` IS LOAD-BEARING TOO (round-3 audit MAJOR 1). Without it the
        # command prints 48 bare source lines, 38 of them the identical string
        # ``    def on(``, and the very next clause — "then read at the line it
        # reports" — has no line to consume. ``grep -nE`` prefixes ``<line>:`` so
        # the instruction closes: grep reports ``1655:    def register_tool(...)``
        # and the model reads at 1655. Aelix's own grep tool always emits
        # ``path:line:`` regardless, so ``-n`` only fixes the bash surface.
        pointers.append(
            "  - full API, too big to read whole "
            f"(grep -nE 'def (register_|on\\()' it, then read at the line it reports): {api}\n"
        )
    if pointers:
        lines.append("- Do not recall or import the API — read the shipped source:\n")
        lines.extend(pointers)

    # MEASURED, not assumed (review MAJOR 1). The old line claimed a write under
    # ``.aelix/`` "always prompts ... even in auto-accept-edits". Driving
    # ``PermissionExtension._on_tool_call`` with a real ``write`` to
    # ``<cwd>/.aelix/extensions/x.py`` gives, across every posture:
    #
    #   posture             interactive   headless      headless (delegated)
    #   default             PROMPT        allow         BLOCK
    #   auto-accept-edits   PROMPT        allow         BLOCK
    #   auto                PROMPT        allow         BLOCK
    #   yolo                allow         allow         allow
    #   plan                BLOCK         BLOCK         BLOCK
    #
    # i.e. it prompts in 3 of those 15 cells. YOLO returns at branch (e)
    # (``permission.py:438-439``) BEFORE the write check, and a headless run
    # (``-p`` / ``--mode json`` / ``--mode rpc``) has no approver at all —
    # branch (d) at ``:486-489`` allows (or, for a delegated child, blocks).
    # The prompt is reached only via branch (h) at ``:491-498``, because
    # ``.aelix`` is in ``_SENSITIVE_DIR_COMPONENTS`` (``:253-255``) so
    # ``_is_auto_allowable_write`` refuses to short-circuit (f)/(g).
    #
    # So the bullet asserts only the conditional ("may ask"), and spends its
    # remaining words on the part that is actually load-bearing for behaviour:
    # a prompt is not a refusal, and a refusal must not be routed around. The
    # anti-workaround clause matters — an agent that reads a denial as an
    # obstacle will happily retry the same write through `bash`, which is a
    # different tool and a different rule key.
    #
    # MINOR 7 (truth audit): the shipped bullet said the prompt "is expected",
    # which the table above falsifies — it appears in 3 of 15 cells, so on most
    # surfaces no prompt comes at all. Comment and text now agree on "may ask".
    #
    # MINOR 3 (truth audit): a DECLINE is not the only refusal. The table shows
    # BLOCK for every plan-mode cell (``permission.py:414-418``, which returns
    # above the read-only short-circuit so it binds headless too) and for a
    # delegated headless child on default / auto-accept-edits / auto
    # (``:486-489``, ``headless_default == "block"``). Those are policy, not a
    # human saying no, and retrying cannot change them — so the clause names
    # "blocked" alongside "declines" and routes both to the same stop.
    # Scoped to "either path", not to "`.aelix/`" (round-3 audit): the global
    # target lives under ``<agent_dir>`` which is ``~/.aelix/agent`` by default,
    # so it too contains a ``.aelix`` component and its permission table is
    # cell-for-cell identical to the project-local one. Saying "`.aelix/` write"
    # reads as project-only and would leave the recommended-first target
    # unexplained.
    lines.append(
        "- A write to either path may ask for approval; that is not a refusal. If "
        "the user declines or the write is blocked, stop and say so — do not retry "
        "it via bash and do not write elsewhere to dodge it.\n"
    )
    # MINOR 3: ``/reload`` is implemented ONLY in the TUI (``tui/input.py:47-48``)
    # and the basic REPL (``cli/repl.py:99``). This block is also emitted for
    # ``--print`` / ``--mode json`` / ``--mode rpc`` and for delegated subagents,
    # where telling the user to "run /reload" names a command that does not
    # exist. Made mode-agnostic: the fallback is the one thing that is always
    # correct — report the absolute path and stop.
    #
    # MINOR 4 (truth audit): /reload does not ALWAYS re-discover. ``shell.py:2448``
    # gates the factory rebuild on ``_reload_rebuild_enabled()``; with the
    # documented kill-switch ``AELIX_RELOAD_REBUILD`` set to a falsy value
    # (0/false/no/off, ``shell.py:109-120``) /reload routes to
    # ``harness.reload_resources()``, which only re-emits a resources discover
    # (``harness/core.py:2955-2962``) and never re-scans the extension dirs.
    # Measured:
    #
    #   AELIX_RELOAD_REBUILD=''      -> True  -> runtime_host.reload()   [re-discovers]
    #   AELIX_RELOAD_REBUILD='0'     -> False -> harness.reload_resources() [does NOT]
    #
    # Naming the restart fallback costs six words and turns a silent no-op into
    # a recoverable one; the model would otherwise report success on a file the
    # session never picked up.
    lines.append(
        "- You cannot load it yourself: in an interactive session ask the user to "
        "run /reload (or restart aelix if /reload does not pick it up); otherwise "
        "report the absolute path you wrote and stop.\n"
    )
    return "".join(lines)


def build_system_prompt(cwd: str) -> str:
    """The base coding-agent system prompt (identity + environment + tools)."""

    cwd_abs = os.path.abspath(cwd)
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return (
        "You are Aelix, an interactive CLI coding agent. You help the user with "
        "software-engineering tasks directly in their terminal and working "
        "directory.\n\n"
        "You act by USING TOOLS to inspect and modify the codebase — you do the "
        "work, you do not merely describe it. Available tools: read (read a file), "
        "write (create or overwrite a file), edit (precise string replacements), "
        "bash (run a shell command), grep (search file contents), find (find files "
        "by name), ls (list a directory).\n\n"
        "Guidelines:\n"
        "- Be concise and direct. Prefer doing over explaining; lead with results.\n"
        "- Use tools to gather context before answering questions about the code — "
        "read files and run commands rather than guessing at their contents.\n"
        "- Make the smallest change that solves the problem and match the "
        "surrounding code's style and conventions.\n"
        "- After editing, verify your work (run the relevant tests or build via "
        "bash when appropriate).\n"
        "- Never invent file paths, APIs, or command output — read or run to "
        "confirm before relying on them.\n"
        "- Be careful with destructive or irreversible shell commands; do not run "
        "them unless the intent is clear.\n\n"
        "Converging to an answer:\n"
        "- When you have gathered enough information, STOP calling tools and give "
        "your final answer directly. Tools gather context; they are not the "
        "answer.\n"
        "- Never call the same tool with the same arguments twice. If a tool "
        "already returned a result, use that result — do not re-run it hoping for "
        "something different.\n"
        "- If a request is ambiguous, answer with your best interpretation (and "
        "state the assumption) rather than looping or gathering data "
        "indefinitely. (A single clarifying lookup is fine; endless re-fetching "
        "is not.)\n"
        "- Prefer the fewest tool calls that get the job done; once you can "
        "answer, answer.\n\n"
        "Environment:\n"
        f"- Working directory: {cwd_abs}\n"
        f"- Platform: {platform.system()}\n"
        f"- Today's date: {today}\n"
        "\n" + _extension_signpost(cwd_abs)
    )


def discover_context_files(cwd: str) -> str:
    """Concatenate ``AGENTS.md`` files from cwd up to the filesystem root.

    Returns ``""`` when none are found. Root-most context comes first and the
    cwd-most last (nearer = more specific). The whole emitted chunk is capped at
    :data:`_MAX_CONTEXT_BYTES` to bound the prompt size.

    TRUST-INDEPENDENT BY DECISION (#121, ADR-0217). Nothing here consults
    project trust, and that is the policy, not an oversight. Pi's
    ``docs/security.md:27`` states context files "are loaded regardless of
    project trust", and ``:37`` calls context-file prompt injection an
    "expected local-agent risk [that] cannot be reliably prevented by pi". Pi
    tried the other way and reverted it: context files were ADDED to its trust
    manager at ``89a9220`` (v0.79.0) and REMOVED at ``5cb4f59`` (v0.79.1), four
    days later. ``--no-context-files`` / ``-nc`` is the switch that suppresses
    this; ``--no-approve`` is not (see ``args.Args.project_trust_override``).

    THE FENCE (pi forward-sync). Files are wrapped in pi's ``<project_context>``
    / ``<project_instructions path="...">`` fence — see :data:`_FENCE_OPEN` for
    the verbatim pi source. Aelix used to emit a per-file markdown header
    ``# Project context ({path})`` with no wrapper at all; that shape never
    matched pi's either, so nothing pi-parity was lost by replacing it.

    THE ONE DECLARED DELTA: aelix XML-ESCAPES both the content and the ``path``
    attribute; pi interpolates both raw. The argument is NOT "safer than pi" —
    it is that an unescaped fence lets a hostile ``AGENTS.md`` close the
    boundary early, so its payload reads to the model as being OUTSIDE the
    project-context block. Tag counts IN THE STRING THIS FUNCTION RETURNS,
    measured on the pre-change build with one hostile ``AGENTS.md`` whose body
    carried all three forgeries at once::

        '<project_context>'        0     '</project_context>'        1
        '<project_instructions'    0     '</project_instructions>'   1
        '<available_skills>'       1     '</available_skills>'       2
        '# Project context ('      2   <- one aelix's label, one the body's

    (The ``<available_skills>`` row is 1 open / 2 close *here* because the real
    catalog is a different append chunk; in the assembled prompt the body's
    forged pair simply became a second, indistinguishable catalog.)

    An unbalanced fence makes aelix ASSERT to the model a provenance boundary it
    is not keeping, which is the overclaim this module's own docstring already
    forbids ("an overclaim HERE is acted on by the model"). We are not claiming
    pi erred; we are declining to ship a label we cannot honour.
    :func:`aelix_coding_agent.cli.skills_prompt._escape_xml` is reused rather
    than re-derived, and it covers ``"``/``'``, so the same call is correct for
    the attribute value as for the element text.

    WHAT THE 32 KiB BOUNDS (changed by this commit, read before touching the
    loop). It now bounds the ENTIRE returned string — fence, per-file tags,
    and ESCAPED content — because escaping is what reaches the model and
    escaping GROWS the text. Budgeting the raw text and escaping afterwards
    would leave the cap unenforced by up to 5x (``&`` → ``&amp;``), which is
    the same class of defect as the join-separator bug the previous revision
    fixed ("without this the result came back at 32769 for a 32768 budget").
    The cap is aelix-original: **pi has no cap at all** — it concatenates every
    context file it loaded. Do not describe it as parity.

    TRUNCATION NEVER CUTS A TAG. Only the escaped CONTENT of the first file that
    does not fit is trimmed; its ``</project_instructions>`` and the closing
    ``</project_context>`` are always emitted whole. Truncating the assembled
    block instead would make aelix emit the unbalanced fence ITSELF —
    manufacturing the exact defect the escaping above exists to prevent.
    A trim is also walked back off a half-written entity
    (:data:`_PARTIAL_ENTITY_TAIL`).

    The budget is spent NEAREST-FIRST and the result is emitted root-most first
    (#159). Those are two different orders on purpose, and conflating them was
    the bug: the loop used to spend the budget in emission order, so a large
    ancestor consumed it all and the project's OWN ``AGENTS.md`` — the most
    specific file, and the only one the project actually ships — was dropped
    entirely with no diagnostic. Measured before the fix, with a 48KB ancestor:

        returned bytes             : 32768
        ancestor present           : True
        PROJECT'S OWN file present : False

    Anything dropped or truncated now emits a ``Warning:`` to stderr, because a
    silent omission here is indistinguishable from a project that simply has no
    instructions — and the user would have no way to tell that the file they
    wrote is not reaching the model.
    """

    here = Path(os.path.abspath(cwd))
    found: list[tuple[Path, str]] = []
    for directory in [here, *here.parents]:
        path = directory / _CONTEXT_FILENAME
        if not path.is_file():
            continue
        try:
            # ``UnicodeDecodeError`` is a ValueError, NOT an OSError — a stray
            # binary file named AGENTS.md on the walk-up path must be skipped,
            # not crash CLI startup.
            found.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    if not found:
        return ""

    # ``found`` is nearest-first (cwd, then its parents), which is exactly
    # priority order — so the budget is spent over it as-is.
    kept: list[tuple[Path, str]] = []
    dropped: list[Path] = []
    truncated: list[Path] = []
    # The wrapper reaches the model too, so it is charged to the budget up
    # front rather than added to a full 32 KiB of content afterwards. Measured:
    # ``_FENCE_OPEN`` is 66 bytes and ``_FENCE_CLOSE`` 19, so omitting this
    # overshoots the cap by 85 — small, but exactly the "approximate bound" the
    # previous revision removed.
    total = len(_FENCE_OPEN.encode("utf-8")) + len(_FENCE_CLOSE.encode("utf-8"))
    for path, text in found:
        # ESCAPE FIRST, THEN BUDGET (see the docstring). ``_escape_xml`` can
        # grow the text ~5x in the worst case, so budgeting the raw bytes would
        # leave the cap unenforced by that factor.
        # ``_safe_path`` BEFORE ``_escape_xml``: the two close different
        # channels and neither covers the other. Escaping neutralises ``"`` and
        # ``>`` so the attribute cannot be broken out of; it leaves control
        # bytes untouched. A directory name may legally carry any byte but
        # ``/`` and NUL, and XML 1.0's ``Char`` production forbids C0 outright
        # (tab/LF/CR excepted) — so a raw ``\x1b`` here would make the fence
        # we just started asserting malformed, in the one attribute an attacker
        # picks. Order matters only in that dropping the bytes first keeps
        # ``_escape_xml`` from having to reason about them at all.
        open_tag = f'<project_instructions path="{_escape_xml(_safe_path(path))}">\n'
        body = _escape_xml(text.strip())
        # The tags are charged separately from the body precisely so the body is
        # the ONLY thing a truncation can reach.
        tag_bytes = len(open_tag.encode("utf-8")) + len(
            _INSTRUCTIONS_CLOSE.encode("utf-8")
        )
        body_bytes = body.encode("utf-8")
        room = _MAX_CONTEXT_BYTES - total - tag_bytes
        if len(body_bytes) > room:
            # Truncate the first file that does not fit — a large ancestor still
            # contributes what it can — then stop: everything further from the
            # cwd is lower priority than what has already been kept.
            if room > 0:
                body = _PARTIAL_ENTITY_TAIL.sub(
                    "", body_bytes[:room].decode("utf-8", errors="ignore")
                )
                kept.append((path, f"{open_tag}{body}{_INSTRUCTIONS_CLOSE}"))
                truncated.append(path)
                # Dead on this iteration (the ``break`` is two lines below) but
                # kept so the loop invariant "``total`` is the size of what
                # ``kept`` will emit" holds unconditionally — a later edit that
                # continues instead of breaking must not have to rediscover it.
                total += tag_bytes + len(body.encode("utf-8"))
            dropped.extend(p for p, _ in found[len(kept) :])
            break
        kept.append((path, f"{open_tag}{body}{_INSTRUCTIONS_CLOSE}"))
        total += tag_bytes + len(body_bytes)

    for path in truncated:
        print(
            f"Warning: {_CONTEXT_FILENAME} truncated to fit the "
            f"{_MAX_CONTEXT_BYTES}-byte context budget ({_safe_path(path)})",
            file=sys.stderr,
        )
    for path in dropped:
        print(
            f"Warning: {_CONTEXT_FILENAME} skipped — context budget exhausted "
            f"by nearer files ({_safe_path(path)})",
            file=sys.stderr,
        )

    if not kept:
        # Nothing survived the budget. Pi opens the fence only when it has at
        # least one file (``system-prompt.ts:145``, ``contextFiles.length > 0``)
        # and an empty ``<project_context>`` would announce project rules that
        # are not there — the same overclaim the escaping above exists to stop.
        # So return the "" that every no-files caller already handles.
        #
        # Unreachable on Linux today, and stated as measured rather than as
        # "never": ``room`` only goes non-positive once the source path exceeds
        # 32626 bytes (32768 - 85 fence - 57 fixed tag bytes), and ``PATH_MAX``
        # is 4096, so ``path.is_file()`` above would have failed first. The
        # invariant "the fence is opened only for a file we really kept" should
        # hold by construction, not by an OS limit.
        return ""

    # Emit root-most first / cwd-most last, the pre-existing document order:
    # the nearest and most specific instructions land closest to the model's
    # most recent context. Only the BUDGETING order changed.
    #
    # No join separator: each block already ends with pi's ``"\n\n"``
    # (:data:`_INSTRUCTIONS_CLOSE`), so concatenation reproduces pi's spacing
    # exactly, including the blank line before ``</project_context>``.
    return _FENCE_OPEN + "".join(block for _, block in reversed(kept)) + _FENCE_CLOSE


__all__ = ["build_system_prompt", "discover_context_files"]
