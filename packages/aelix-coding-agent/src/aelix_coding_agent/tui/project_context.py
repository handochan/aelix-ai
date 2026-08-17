"""Which project context is REALLY in the assembled system prompt (issue #121).

Two TUI surfaces answer the question "does this session have ``AGENTS.md``
loaded?" — the startup banner's ``[Context]`` row and ``/context``'s estimated
composition. Both used to answer it by re-running
:func:`aelix_coding_agent.cli.agent_context.discover_context_files` against the
cwd, which asks the FILESYSTEM a question only the PROMPT can answer. Discovery
knows neither of the two things the callers need:

* it does not know about ``--no-context-files`` / ``-nc``. That gate lives one
  level up, at ``cli/entry.py:1243``, so discovery happily hands back text that
  was never injected into anything; and
* it does not know its own output is ALREADY inside the system prompt the caller
  is separately counting — ``cli/entry.py:1244-1246`` appends the chunk to
  ``options.append_system_prompt`` and ``harness/core.py:596-602`` joins those
  onto the base prompt with ``"\\n\\n"``.

Measured on the pre-change build with one 7175-char ``AGENTS.md`` (1794
estimated tokens) against a 200000-token window::

    /context  without -nc : System prompt 2.6K + Memory files 1.8K  <- 1794 counted TWICE
    /context  with    -nc : System prompt 837  + Memory files 1.8K  <- 1794 PHANTOM
    banner    with    -nc : "[Context]    AGENTS.md"                <- nothing was loaded

CONTAINMENT, NOT PARSING. :func:`split_project_context` decides by asking
whether the discovered chunk is a SUBSTRING of the assembled prompt. It
deliberately looks for no header, no fence and no marker of any kind: the
chunk's internal shape belongs to ``agent_context``, and it changed inside this
very issue (a ``# Project context ({path})`` markdown header became pi's
``<project_context>`` / ``<project_instructions>`` fence). A reader that
pattern-matched the old spelling would have gone silently wrong the moment it
moved, and silently is the whole problem here. The question this module has is
not "what does project context look like" but "is THIS text inside THAT
prompt", and containment answers precisely that — exactly, not heuristically,
because ``entry.py`` appends the chunk VERBATIM and nothing reformats it on the
way in.

Consequently a stale answer degrades in the SAFE direction. If the AGENTS.md on
disk is edited mid-session the re-read no longer matches what was injected, so
the context is reported as absent: the row disappears and its tokens stay
charged to ``System prompt``. That is a understatement of one row, not a phantom
and not a double count, and it is the failure this module exists to avoid.
"""

from __future__ import annotations

import contextlib
import io

__all__ = ["split_project_context"]


def _discover_without_re_warning(cwd: str) -> str:
    """:func:`discover_context_files` with its stderr warnings CAPTURED and dropped.

    Discovery is not a pure read: it prints a ``Warning:`` line to stderr for
    every context file it truncated or dropped against the 32 KiB budget
    (``cli/agent_context.py:1250-1261``). The injection path at
    ``cli/entry.py:1244`` has already called it once and those warnings have
    already been printed, so a second copy is duplicate noise — measured at 115
    bytes re-emitted per banner render on a single oversized ``AGENTS.md``.

    Dropping rather than re-printing is safe because discovery's stderr is a
    pure function of the same cwd and the same files, so this call cannot say
    anything the startup call did not already say. It is also the half of the
    fix that keeps a hostile path off the terminal: the warning interpolates the
    absolute path RAW, and a directory named
    ``proj\\x1b]0;pwned\\x07`` put a live OSC title-set sequence on the parent's
    stderr — confirmed by rendering the banner over such a directory and finding
    both ``\\x1b`` and ``\\x07`` in the captured stream.

    KNOWN COST, stated rather than hidden: :func:`contextlib.redirect_stderr`
    rebinds ``sys.stderr`` process-wide, so anything ELSE that writes to stderr
    during this call is swallowed too. The window is one filesystem walk of the
    cwd's ancestors, and the alternative — recognising discovery's own warnings
    by their text so the rest could be forwarded — would couple this module to a
    string in a file it does not own. The narrower coupling was preferred.
    """

    from aelix_coding_agent.cli.agent_context import (  # noqa: PLC0415
        discover_context_files,
    )

    swallowed = io.StringIO()
    try:
        with contextlib.redirect_stderr(swallowed):
            return discover_context_files(cwd)
    except Exception:  # noqa: BLE001 — a report surface must never take the TUI down
        return ""


def split_project_context(system_prompt: str | None, cwd: str) -> tuple[str, str]:
    """Split ``system_prompt`` into ``(everything else, the project context)``.

    Returns ``(system_prompt or "", "")`` when this session's prompt does not
    contain the project context discovered under ``cwd`` — which is the ``-nc``
    answer, the no-``AGENTS.md`` answer, and the harness-cannot-report-its-prompt
    answer all at once, and all three deserve the same "there is no project
    context in the prompt" from a reporting surface.

    The two halves are disjoint by construction, so a caller may charge each to
    its own row without counting anything twice. Only the first occurrence is
    removed: ``entry.py`` appends the chunk exactly once, and removing more would
    be attributing text this function has not identified.

    The ``"\\n\\n"`` separators that ``harness/core.py:596-602`` puts around the
    chunk stay in the first half. They belong to the prompt's scaffolding rather
    than to either side, they are worth about one estimated token, and moving
    them would make the split depend on how the harness joins.

    NOT DISAMBIGUATED, and it does not need to be: if a user's
    ``--append-system-prompt`` happened to carry byte-identical text, this would
    attribute that copy to project context. The arithmetic stays right — the
    halves are still disjoint and still sum to the whole — so the only error
    would be a label on text the user typed themselves.
    """

    if not system_prompt:
        return ("", "")
    chunk = _discover_without_re_warning(cwd)
    if not chunk or chunk not in system_prompt:
        return (system_prompt, "")
    return (system_prompt.replace(chunk, "", 1), chunk)
