"""``aelix docs`` — read the bundled user guides offline (#101).

AELIX-ORIGINAL: pi has no ``docs`` subcommand. What is forward-ported from pi is
the *bundling* (pi ships ``files: ['dist','docs','examples',…]`` and its system
prompt names ``getDocsPath()``); the CLI verb is ours.

Shaped after ``cli/extension_install.py``, which is the product's only other
subcommand and therefore IS the convention:

- a ``_USAGE`` string printed for ``-h``/``--help`` and appended to a usage error;
- **exit 2** for anything the user got wrong (``_EXIT_USAGE`` here mirrors
  ``extension_install._EXIT_DIDNT_RUN``: "we did not do the thing you asked");
- diagnostics on **stderr**, product output on **stdout**.

The stdout/stderr split is not cosmetic. ``aelix docs extension | head`` has to
put markdown into the pipe and nothing else, and an unknown-topic message on
stdout would be indistinguishable from a very short guide.

No rich, no prompt-toolkit: this runs with no ``[tui]`` extra installed.
"""

from __future__ import annotations

import sys
from typing import TextIO

from aelix_coding_agent.help import (
    near_miss,
    read_topic,
    resolve_topic,
    search_topics,
    topics,
)

#: The user got it wrong and nothing was printed. Same value and same meaning as
#: ``extension_install._EXIT_DIDNT_RUN``; a second subcommand inventing its own
#: number is how a script that wraps both stops being able to branch on either.
_EXIT_USAGE = 2

_USAGE = (
    "usage: aelix docs [<topic>]\n"
    "  aelix docs                       list the bundled guides\n"
    "  aelix docs <topic>               print one guide to stdout\n"
    "  aelix docs --search <term>       search every guide (-s)\n"
)


def _print_topics(stream: TextIO) -> None:
    """The topic listing. Goes to whichever stream the caller is using —
    stdout when it is the answer, stderr when it is the correction after an
    unknown topic."""

    available = topics()
    if not available:
        print(
            "No guides are bundled in this installation.",
            file=stream,
        )
        return
    width = max(len(t.name) for t in available)
    print("Guides (read one with `aelix docs <topic>`):", file=stream)
    for topic in available:
        print(f"  {topic.name.ljust(width)}  {topic.title}", file=stream)


def run_docs_command(args: list[str]) -> int:
    """Dispatch ``aelix docs …``. Returns the process exit code.

    Sync on purpose: nothing here awaits. ``extension`` is async only because it
    has to flush an async settings write, and copying that shape without the
    reason would be cargo.
    """

    if args and args[0] in ("-h", "--help"):
        print(_USAGE, end="")
        return 0

    if args and args[0] in ("--search", "-s"):
        term = " ".join(args[1:]).strip()
        if not term:
            print("Error: --search needs a term.", file=sys.stderr)
            print(_USAGE, end="", file=sys.stderr)
            return _EXIT_USAGE
        hits = search_topics(term)
        if not hits:
            # stderr, and exit 0: an empty result is a true answer to a
            # well-formed question, not a usage error. A script doing
            # `aelix docs -s foo | wc -l` gets 0 lines and a clean exit.
            print(f"No guide mentions {term!r}.", file=sys.stderr)
            return 0
        for hit in hits:
            print(f"{hit.topic.name}:{hit.line}: {hit.text}")
        return 0

    if not args:
        _print_topics(sys.stdout)
        return 0

    name = args[0]
    if name.startswith("-"):
        print(f"Error: unknown option {name!r}.", file=sys.stderr)
        print(_USAGE, end="", file=sys.stderr)
        return _EXIT_USAGE

    topic = resolve_topic(name)
    if topic is None:
        # A near miss gets its own sentence rather than the generic one: telling
        # a user who typed `skills` that "skills is not a topic" is true and
        # useless, when what they need is what in their install DOES answer it.
        # ``near_miss`` (not the raw NEAR_MISSES dict) because it resolves the
        # packaged files to absolute paths — #101's L10 review: this message
        # used to omit `skills/writing-skills/SKILL.md`, which ships in the same
        # wheel and is the actual answer.
        near = near_miss(name)
        if near is not None:
            print(f"No guide for {name!r}: {near}", file=sys.stderr)
        else:
            print(f"Error: unknown topic {name!r}.", file=sys.stderr)
        _print_topics(sys.stderr)
        return _EXIT_USAGE

    try:
        text = read_topic(topic)
    except OSError as exc:
        # Never swallowed: a caught read error printed as an empty document
        # would exit 0 and look like a guide with no content.
        print(f"Error: cannot read {topic.path}: {exc}", file=sys.stderr)
        return 1

    print(text, end="" if text.endswith("\n") else "\n")
    return 0
