"""Text a delegated agent can be handed (#627, ADR-0277).

Two characters a Python ``str`` can hold that no delegated agent can be handed:

* a LONE SURROGATE (U+D800-U+DFFF). JSON's ``"\\ud83d"``, half an emoji, decodes
  to one, and so does YAML's; no UTF-8 encoder writes it;
* NUL. UTF-8 encodes it, but no argv element can carry it:
  ``create_subprocess_exec`` raises ``embedded null byte``.

Let through, either one failed the launch AFTER the delegation budget was
charged, the run's records were written and the child's file was created; a
lone surrogate in the ``agent`` tool's task raised out of the ``tool_call``
hook and ended the turn. So every door asks first: the ``agent`` tool's parser
(``aelix_agents.tool``), the runtime's doors (``aelix_agents.runtime``),
``/agents run`` (``tui/commands.py``), RPC ``agents/run`` (``rpc/control.py``)
and a profile's parse (:mod:`.profile`).

TEXT AND PATHS ARE HELD TO DIFFERENT RULES. Text — a task, an instruction, a
profile's values as written — is held to :func:`unsendable_character`: no NUL,
no lone surrogate. A string the OS is handed as it stands — a ``cwd``, an argv
element, a ``skills:`` path — is held to what the launch does with it
(:func:`unencodable_character`): no NUL, and nothing ``os.fsencode`` refuses.
On POSIX that still refuses ``"\\ud83d"`` but passes U+DC80-U+DCFF, which is how
a directory whose name is not UTF-8 decodes (surrogateescape) and which encodes
back to its own bytes; on Windows ``os.fsencode`` uses surrogatepass, so every
lone surrogate passes there and the OS answers for the name.

HERE, in product core, rather than in the ``aelix_agents`` extension: the human
and RPC doors and the profile parser live in product core, which never imports
the extension (ADR-0197); the extension imports this. PURE — ``re`` and
``os.fsencode`` only.
"""

from __future__ import annotations

import os
import re

_SURROGATE = re.compile(r"[\ud800-\udfff]")
_UNSENDABLE = re.compile(r"[\x00\ud800-\udfff]")

_LONE_SURROGATE = "half of a character (a lone surrogate)"
_NUL = "a NUL character (U+0000)"


def unsendable_character(text: str) -> str | None:
    """What in ``text`` no delegated agent can be handed, named for the reader
    (``"a NUL character (U+0000)"``) — else ``None``. A text holding both is
    named for its lone surrogate, as the resume parse always did."""

    if _SURROGATE.search(text) is not None:
        return _LONE_SURROGATE
    if "\x00" in text:
        return _NUL
    return None


def unencodable_character(text: str) -> str | None:
    """What in ``text`` the OS cannot be handed as an argv element, a ``cwd`` or
    a path — a NUL, or a lone surrogate ``os.fsencode`` refuses — named as
    :func:`unsendable_character` names it, else ``None``. See the module
    docstring for why a path is not held to the text rule."""

    if "\x00" in text:
        return _NUL
    try:
        os.fsencode(text)
    except UnicodeEncodeError:
        return _LONE_SURROGATE
    return None


def unsendable_refusal(text: str, *, where: str, resend: str) -> str | None:
    """The sentence refusing ``text`` when it holds a character no delegated
    agent can be handed — else ``None``.

    ``where`` names the argument and ``resend`` what to send again. With
    ``"'task'"`` and ``"the instruction"`` the sentences are the resume
    refusals #610 shipped (ADR-0277 D1), byte for byte. The text never contains
    the character it refuses.
    """

    return _refusal(unsendable_character(text), where=where, resend=resend)


def unencodable_refusal(text: str, *, where: str, resend: str) -> str | None:
    """:func:`unsendable_refusal`'s sentence, for a string the OS is handed as
    it stands (:func:`unencodable_character`)."""

    return _refusal(unencodable_character(text), where=where, resend=resend)


def _refusal(what: str | None, *, where: str, resend: str) -> str | None:
    if what is None:
        return None
    fix = "as plain text" if what == _LONE_SURROGATE else "without it"
    return f"{where} holds {what}, which cannot be sent to the agent. Resend {resend} {fix}."


def join_surrogate_pairs(text: str) -> str:
    """``text`` with every HIGH-then-LOW surrogate pair joined into the one
    character it spells; lone and reversed halves are left as they are.

    JSON's decoder joins ``"\\ud83d\\ude42"`` into one character and PyYAML's
    does not, so a profile value written as ``json.dumps`` writes it would read
    as two lone halves (#627 review round 2).
    """

    if _SURROGATE.search(text) is None:
        return text
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def sendable(text: str) -> str:
    """``text`` with every character :func:`unsendable_character` names
    replaced by U+FFFD — for text no one can resend.

    A chain step's ``{previous}`` is written by a CHILD, mid-call: the model
    that made the call did not write it and cannot send it again, so refusing
    it would end the chain on a defect nobody in the loop can fix. Replaced, not
    dropped: U+FFFD shows the reader that a character was there, as the
    delegation panel shows a lone surrogate.
    """

    return _UNSENDABLE.sub("\ufffd", text)


__all__ = [
    "join_surrogate_pairs",
    "sendable",
    "unencodable_character",
    "unencodable_refusal",
    "unsendable_character",
    "unsendable_refusal",
]
