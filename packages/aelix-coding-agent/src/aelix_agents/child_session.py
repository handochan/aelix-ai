"""A delegated child's session file, and the parent's receipt for it — #199.

WHAT THIS MODULE OWNS: everything #199 writes, and nothing that decides whether a
delegation may run.

* WHERE a child's session lives (:func:`place_children`): beside its parent, in a
  directory named after the parent's file — ``<bucket>/<stem>.jsonl`` gets
  ``<bucket>/<stem>/<spawn id>.jsonl``. ``JsonlSessionRepo.list`` and
  ``find_most_recent`` read only the ``*.jsonl`` files directly inside a bucket
  and skip directories, so no picker, no ``--continue`` and no id-prefix lookup
  ever sees a child.
* the child file ITSELF (:meth:`SpawnReceipt.open`): published by the PARENT —
  a header and one ``aelix.child_origin`` record, in one atomic publish
  (ADR-0242 §4) — before the child process exists. The child is then launched
  with ``--session <that path>``, so even a child that dies before its first
  turn leaves a findable file, and the parent knows the path without asking.
* the PARENT's records: ``aelix.child_session`` (start, settle) and
  ``aelix.usage`` (pending, final), each one ``CustomEntry`` line with
  everything inside ``data`` (ADR-0242 rule 1).
* since #610 (ADR-0277), the records' FIRST READER: :func:`find_resume_target`
  turns the id a model was shown back into a child file the parent may resume,
  and :func:`resumable_agents` lists the ones it could. A resume run ADOPTS the
  child's file (:meth:`SpawnReceipt.attach`) instead of allocating one.

``runtime._run`` is the only writer. It decides WHEN each record is written;
this module decides WHAT is written and where.

THE RECORDS, EXACTLY. ``key`` is the spawn id (``sub-<12 hex>``), the one
per-RUN identifier: a ``tool_call_id`` is one per ``agent`` call and is not
unique on the Google adapter across restarts. A reader folds each type by
``key``, last line wins.

* ``aelix.child_session`` start — ``{v, key, phase: "start", tool_call_id,
  index, mode, profile, task_preview, child, requested_model, permission_mode,
  aelix_version, limits, resumes, error}``. ``child`` is ``{session_id, path,
  rel}`` or ``null``; ``error`` says why it is ``null`` when that is not simply
  "the parent has no session file" (:data:`PARENT_OUTSIDE_A_BUCKET`, a failed
  create), and is ``null`` otherwise. ``limits`` (#610 D8) is what bounded the
  child, as typed values (:class:`ChildLimits`); ``resumes`` is ``null`` on a
  delegation's first run and that run's ``key`` on every resume of it, whose
  own ``key`` is new (D15: the stats fold is last-line-wins per key, so reusing
  the first key would erase the first run's spend).
* ``aelix.usage`` pending — ``{v, key, state: "pending"}``, written right after
  the start and BEFORE the child can spend anything.
* ``aelix.child_session`` settle — every start field except ``error``, then
  ``phase: "settle"``, ``status`` (``ok`` / ``error`` / ``timeout`` /
  ``aborted`` / ``cancelled``), ``model``, ``provider``, ``usage: {input,
  output, cache_read, cache_write, cost}``, ``cost_known``, ``context_tokens``,
  ``turns``, ``elapsed_ms``, ``exit_code``, ``stop_reason``, ``truncated``,
  ``summary_bytes``, ``details_bytes`` and ``error`` (the run's own, short).
  A COMPLETE record rather than the second half of the start (ADR-0242: the
  file can end between the two, and the prefix must still make sense).
* ``aelix.usage`` final — ``{v, key, state: "final", usage, cost_known}``, the
  same two values the settle carries.

A start with no settle is a delegation whose outcome is UNKNOWN (the process
died, the machine died); a pending with no final is spend that is NOT
CONFIRMED. Neither is a zero.

RECORDING NEVER CHANGES A DELEGATION. Every append is logged at DEBUG and
swallowed (:func:`append_records`), and every allocation failure becomes
``child: null`` and a ``--no-session`` child. The record builders are total
(:func:`_utf8_len`), and ``runtime._run`` still falls back to
:meth:`SpawnReceipt.bare_records` if one raises. The one thing that propagates
is ``CancelledError``, which is not a failure to record but the delegation
ending.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import stat
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aelix_agent_core.session.entries import CustomEntry
from aelix_agent_core.session.fs import LocalFileSystem

# ``_parse_header_line`` is the child's OWN header rule, not a copy of it (#610
# F6): a header the session loader would refuse is refused here, before a
# resume spends anything. Private across packages on purpose, as
# ``cli/session_labels.py`` reads its sibling ``_parse_jsonl_content``: a public
# twin would be a second rule to drift.
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage, _parse_header_line
from aelix_agent_core.session.storage import SessionError
from aelix_coding_agent.agents.resolver import ModelPin
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.cli.args import VALID_THINKING_LEVELS
from aelix_coding_agent.cli.config import VERSION
from aelix_coding_agent.tools import ALL_TOOL_NAMES

from aelix_agents.envelope import child_line

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aelix_coding_agent.subagent_contract import SubagentResult

    from aelix_agents.stream import _StreamState

logger = logging.getLogger(__name__)

CHILD_SESSION_TYPE = "aelix.child_session"
"""The parent's receipt for one delegated child: a start and a settle."""

USAGE_TYPE = "aelix.usage"
"""The parent's usage ledger line for one child: a pending and a final.

Named for USAGE, not for delegation, on purpose: it is the one record type the
kernel's session stats read (``harness/_session_stats.py``), and the kernel may
not learn delegation vocabulary."""

CHILD_ORIGIN_TYPE = "aelix.child_origin"
"""The first entry of every child session file — who spawned it, and from where.

``cli/entry.py`` spells the same string to warn a human who opens a child file
with ``--session`` (it may not import this module). Pinned equal by a test."""

RECORD_VERSION = 1
"""``v`` on every record this module writes. A reader ignores a version it does
not know, as it ignores a ``customType`` it does not know (ADR-0242 rule 1.6)."""

TASK_PREVIEW_CHARS = 200
"""How much of the task the start record carries. The whole task is in the child
file (its first user message); this keeps the parent's record self-describing
without copying a long prompt into it."""

ERROR_PREVIEW_CHARS = 300
"""How much of an error a record carries. A record states WHAT went wrong; the
stderr tail and the full message stay with the envelope and the child file."""

CHILDREN_SUFFIX = ".children"
"""Appended to a parent file name that does not end in ``.jsonl``, so the child
directory can never share a name with the parent file itself."""

PARENT_OUTSIDE_A_BUCKET = "parent outside a bucket"
"""The start record's ``error`` when the parent file does not sit inside a
session bucket. See :func:`place_children` for why that refuses a child file."""

RECORD_BUILD_FAILED = "the record could not be built"
"""The settle's ``error`` when :meth:`SpawnReceipt.bare_records` stood in for a
builder that raised."""

_SESSION_SUFFIX = ".jsonl"


# === What bounded a child (#610 D8) ===========================================


class LimitsError(ValueError):
    """A recorded ``limits`` object this aelix cannot honour EXACTLY (#610 D8).

    ``reason`` names the field and what is wrong with it, and never the value:
    a record is untrusted input (critique B2), and the reason reaches the model
    inside a refusal.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_AGENT_TOOL = "agent"
"""``print_channel.AGENT_TOOL_NAME``, spelled here because that module imports
this one. A recorded tool list naming it is refused like any other foreign
name: no child may hold the delegation tool (the second anti-nesting layer)."""

_PIN_VALUE_MAX_CHARS = 256
"""Longest model id or provider name a recorded pin may carry. Real ones are a
few dozen characters; the bound is for a forged record."""

_PATH_MAX_CHARS = 4096
"""Longest ``cwd`` or ``profile_source`` a record may carry (``PATH_MAX``)."""

_SCOPE_MAX_CHARS = 64

_ROUTE_DIGEST = re.compile(r"[0-9a-f]{64}")
"""``model_registry.registration_route_digest`` — a sha256 hexdigest. The
child's own ``require_registration_route`` refuses anything else."""

_LIMIT_FIELDS = (
    "granted_mode",
    "tools",
    "context_files",
    "thinking",
    "model",
    "cwd",
    "profile_source",
    "profile_scope",
)

_PIN_FIELDS = ("model", "provider", "route")


@dataclass(frozen=True)
class ChildLimits:
    """What bounded one delegated child, as typed values (#610 D8, ADR-0277).

    Computed ONCE per run, before the run is admitted
    (``print_channel.fresh_limits`` for a new delegation,
    ``print_channel.resume_limits`` for a resume), and both launched from and
    recorded in the parent's receipt — so the child's argv and the record of
    what bounded it cannot disagree. A resume reads the FIRST run's copy back
    (:meth:`from_record`) as its ceiling.

    Values, never argv fragments: a stored argv replayed verbatim is arbitrary
    argv for whoever can write the parent's file (critique B2).
    """

    granted_mode: PermissionMode
    """The posture the human (or the clamp, when nobody was asked) GRANTED —
    ``SpawnGrant.mode``, before a batch member's live floor tightened it. The
    resume ceiling (critique m3): a floor applied because the parent was
    briefly in ``plan`` must not shrink the agent forever. What actually ran
    stays the record's own top-level ``permission_mode``."""
    tools: tuple[str, ...]
    """The child's ``--tools`` (``narrow_tools``' result); empty is
    ``--no-tools``."""
    context_files: bool
    """``False`` is ``--no-context-files``."""
    thinking: str | None
    """The ``--thinking`` the argv carried — the profile's own level or the
    parent's inherited one (#354) — or ``None`` for none."""
    model: ModelPin
    """The model flags (``resolver.child_model_pin``); ``ModelPin()`` is none."""
    cwd: str
    """The child's working directory, absolute and resolved."""
    profile_source: str | None
    """The profile file the identity came from (``ResolvedProfile.source_path``).
    A resume refuses a profile that now resolves to another file."""
    profile_scope: str

    def to_record(self) -> dict[str, Any]:
        """The JSON value the receipt carries: lists, not tuples (ADR-0242 rule 1.7)."""

        pin = self.model
        return {
            "granted_mode": self.granted_mode.value,
            "tools": list(self.tools),
            "context_files": self.context_files,
            "thinking": self.thinking,
            "model": (
                None
                if pin == ModelPin()
                else {"model": pin.model, "provider": pin.provider, "route": pin.route}
            ),
            "cwd": self.cwd,
            "profile_source": self.profile_source,
            "profile_scope": self.profile_scope,
        }

    @classmethod
    def from_record(cls, record: object) -> ChildLimits:
        """Read a recorded ``limits`` back, or raise :class:`LimitsError`.

        EVERY field is checked, exactly, because the parent's file is the only
        thing between a forged record and a child's argv (critique B2):

        * ``granted_mode`` — a :class:`PermissionMode` value;
        * ``tools`` — distinct built-in tool names (``ALL_TOOL_NAMES``), never
          ``agent``;
        * ``context_files`` — a bool;
        * ``thinking`` — one of ``VALID_THINKING_LEVELS``, or null;
        * ``model`` — null, or ``{model, provider, route}`` where a name does not
          start with ``-`` and holds no whitespace or control character, and
          ``route`` is a 64-hex digest that comes with both names;
        * ``cwd`` — an absolute path with no control character;
        * ``profile_source`` — the same, or null; ``profile_scope`` — a string.

        A field this aelix does not know is refused too, rather than ignored as
        ADR-0242 rule 1.6 would ignore an unknown record key: a later aelix that
        adds a field may add it to bound the child further, and a reader that
        dropped it would resume the child WIDER than it ran.
        """

        if not isinstance(record, dict):
            raise LimitsError("limits is not an object")
        for name in record:
            if name not in _LIMIT_FIELDS:
                raise LimitsError("limits holds a field this aelix does not know")
        for name in _LIMIT_FIELDS:
            if name not in record:
                raise LimitsError(f"{name} is missing")
        mode = record["granted_mode"]
        try:
            granted = PermissionMode(mode) if isinstance(mode, str) else None
        except ValueError:
            granted = None
        if granted is None:
            raise LimitsError("granted_mode is not a permission mode")
        tools = record["tools"]
        if (
            not isinstance(tools, list)
            or not all(isinstance(t, str) for t in tools)
            or any(t not in ALL_TOOL_NAMES or t == _AGENT_TOOL for t in tools)
            or len(set(tools)) != len(tools)
        ):
            raise LimitsError("tools is not a set of built-in tool names")
        if not isinstance(record["context_files"], bool):
            raise LimitsError("context_files is not a boolean")
        thinking = record["thinking"]
        if thinking is not None and (
            not isinstance(thinking, str) or thinking not in VALID_THINKING_LEVELS
        ):
            raise LimitsError("thinking is not a thinking level")
        cwd = record["cwd"]
        if not _is_path(cwd) or not os.path.isabs(cwd):
            raise LimitsError("cwd is not an absolute path")
        source = record["profile_source"]
        # Absolute, as ``cwd`` is (#610 F15): ``fresh_limits`` records
        # ``print_channel.profile_identity`` of the profile's ``source_path``,
        # which is its ``os.path.abspath`` — discovery keeps a relative
        # ``AELIX_CODING_AGENT_DIR`` relative, so ``source_path`` itself can be
        # relative — and a relative one here was written by something else.
        if source is not None and (not _is_path(source) or not os.path.isabs(source)):
            raise LimitsError("profile_source is not an absolute path")
        scope = record["profile_scope"]
        if not isinstance(scope, str) or not scope or len(scope) > _SCOPE_MAX_CHARS:
            raise LimitsError("profile_scope is not a scope")
        return cls(
            granted_mode=granted,
            tools=tuple(tools),
            context_files=record["context_files"],
            thinking=thinking,
            model=_pin_from_record(record["model"]),
            cwd=cwd,
            profile_source=source,
            profile_scope=scope,
        )


def _is_path(value: object) -> bool:
    """A non-empty string a record may carry as a path: bounded, and no control
    character — the dialog renders it and the child is launched in it."""

    return (
        isinstance(value, str)
        and 0 < len(value) <= _PATH_MAX_CHARS
        and not any(unicodedata.category(ch) == "Cc" for ch in value)
    )


def _is_pin_value(value: object) -> bool:
    """A model id or provider name as a recorded pin may carry one.

    Not argv-shaped: no leading ``-``, no whitespace, no control or format
    character. The child's parser would take a ``-x`` value as the value
    anyway (``REQUIRED_VALUE_FLAGS``); this refuses the record that tried.
    """

    return (
        isinstance(value, str)
        and 0 < len(value) <= _PIN_VALUE_MAX_CHARS
        and not value.startswith("-")
        and not any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in value)
    )


def _pin_from_record(value: object) -> ModelPin:
    if value is None:
        return ModelPin()
    if not isinstance(value, dict) or set(value) != set(_PIN_FIELDS):
        raise LimitsError("model is not a model pin")
    for name in ("model", "provider"):
        if value[name] is not None and not _is_pin_value(value[name]):
            raise LimitsError(f"model.{name} is not a model name")
    route = value["route"]
    if route is not None and (
        not isinstance(route, str)
        or not _ROUTE_DIGEST.fullmatch(route)
        or value["model"] is None
        or value["provider"] is None
    ):
        raise LimitsError("model.route is not a route digest")
    return ModelPin(model=value["model"], provider=value["provider"], route=route)


# === Why a resume does not run (#610 D11) =====================================
#
# One place for every refusal a resume can get, so the hook (``extension.py``),
# the runtime's belt (``runtime.resume_granted``) and the tests read the same
# words. Each is complete on its own and names no path: a model never sees a
# session file's location (ADR-0243 §5).

RESUME_NEEDS_ID = (
    'Resume refused: action="resume" needs id — the Session id of an agent this '
    'conversation delegated, as its result shows it: on its "Session:" line, or '
    'after "session" in a batch member\'s "did not finish" heading.'
)
RESUME_UNKNOWN_ID = (
    "Resume refused: no agent this conversation delegated has that id. Use the "
    'id the agent\'s result shows: on its "Session:" line, or after "session" in '
    'a batch member\'s "did not finish" heading.'
)
RESUME_NO_SESSION = (
    "Resume refused: this conversation is not saved in aelix's sessions "
    "directory (--no-session, or a file opened from elsewhere), so no agent it "
    "delegated has a session to continue. Delegate the task anew."
)
RESUME_UNREADABLE = (
    "Resume refused: this conversation's record could not be read, so that "
    "agent cannot be found. Delegate the task anew."
)
RESUME_NOT_YOURS = (
    "Resume refused: that agent was not delegated by you — the user started it "
    "(/agents run) or a client did — and only an agent you delegated can be "
    "resumed. Delegate the task anew."
)
RESUME_FIRST_RUN_MISSING = (
    "Resume refused: the delegation that started that agent is not in this "
    "conversation's record, so the limits it ran with are unknown here. "
    "Delegate the task anew."
)
RESUME_BEFORE_LIMITS = (
    "Resume refused: that agent was delegated before aelix recorded an agent's "
    "limits, so the limits it ran with cannot be restored. Delegate the task anew."
)
RESUME_FILE_GONE = (
    "Resume refused: that agent's session file no longer exists. Delegate the "
    "task anew."
)
RESUME_FORKED = (
    "Resume refused: that agent belongs to the conversation this one was forked "
    "from. Resume it there, or delegate the task anew."
)
RESUME_NEVER_STARTED = (
    "Resume refused: that agent never started — its session holds no task. "
    "Delegate the task anew."
)
RESUME_DAMAGED = (
    "Resume refused: that agent's session record is damaged ({detail}). "
    "Delegate the task anew."
)
RESUME_RUNNING = "Resume refused: that agent is already running. Wait for its result."
RESUME_IN_USE = (
    "Resume refused: that agent's session is in use by another process — it is "
    "still running elsewhere, or someone opened it with aelix --session. Try "
    "again once that has finished."
)
RESUME_LOCK_ERROR = (
    "Resume refused: aelix could not check whether that agent's session is in "
    "use ({error}). Delegate the task anew."
)
RESUME_CWD_GONE = (
    "Resume refused: the directory that agent ran in no longer exists, was moved "
    "or replaced, or is no longer inside this conversation's working directory. "
    "Delegate the task anew."
)
RESUME_ROUTE_MOVED = (
    "Resume refused: the provider registration that agent's model runs on has "
    "changed since it was delegated, so it cannot be resumed on the same route. "
    "Delegate the task anew."
)
RESUME_PROFILE_CHANGED = (
    "Resume refused: the agent profile that agent ran as has changed — it was "
    "removed, renamed, or now resolves to another file. Delegate the task anew."
)
RESUME_CONVERSATION_CHANGED = (
    "Resume refused: the conversation changed before the resume could start, so "
    "that agent is not this conversation's to resume. Delegate the task anew."
)
RESUME_UNSUPPORTED_CHANNEL = (
    "Resume refused: this session delegates through a channel that cannot resume "
    "an agent inside the limits it ran with. Delegate the task anew."
)


def resume_damaged(detail: str) -> str:
    """:data:`RESUME_DAMAGED` with what was found wrong. ``detail`` is this
    module's own words (a :class:`LimitsError` reason, a fixed phrase) — never
    a value read from a record or a file."""

    return RESUME_DAMAGED.format(detail=detail)


class ResumeRefused(Exception):
    """A resume that must not run; ``str(exc)`` is the model-facing ``RESUME_*``
    sentence. Raised where a refusal is found inside a computation
    (``print_channel.resume_limits``) and returned as a result before any
    process exists."""


def _iso_now() -> str:
    """The session store's own timestamp spelling (``jsonl_storage._iso_now``)."""

    return (
        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _short(text: str | None) -> str | None:
    """``text`` cut to :data:`ERROR_PREVIEW_CHARS`, or ``None`` when empty."""

    if not text:
        return None
    stripped = text.strip()
    return stripped[:ERROR_PREVIEW_CHARS] or None


def _utf8_len(text: str | None) -> int:
    """The UTF-8 size of ``text``, and it never raises.

    A lone surrogate is a legal ``str``, and a child's stream can carry one:
    the child writes JSON with ``ensure_ascii`` on, so ``"\\ud83d"`` comes back
    into :attr:`_StreamState.summary` as that code point. A bare
    ``encode("utf-8")`` raised ``UnicodeEncodeError`` from ``_run``'s
    ``finally``, which turned a Ctrl+C into that error and lost the settle.
    ``surrogatepass`` counts the code point as the three bytes it takes.
    """

    if not isinstance(text, str) or not text:
        return 0
    return len(text.encode("utf-8", "surrogatepass"))


def _is_bucket(name: str) -> bool:
    """Does a directory NAME have the shape ``JsonlSessionRepo`` gives a bucket?

    ``_encode_cwd`` always wraps the encoded cwd in ``--…--`` (``--``, the path
    with its separators turned into ``-``, ``--``), and it is the only thing
    that creates the directories a sessions root holds.
    """

    return len(name) >= 4 and name.startswith("--") and name.endswith("--")


@dataclass(frozen=True)
class Placement:
    """Where one parent's children go."""

    directory: str
    """ABSOLUTE path of the children's directory."""
    rel_dir: str
    """Its name, which is its path relative to the parent file's directory."""


def place_children(parent_path: str) -> Placement | None:
    """The directory a parent's children live in, or ``None`` to refuse one.

    ``<dir>/<stem>.jsonl`` → ``<dir>/<stem>``. Only an exact ``.jsonl`` suffix
    is stripped; any other name gets :data:`CHILDREN_SUFFIX` appended instead,
    so the directory can never be the parent file itself.

    RESOLVED AGAINST THE PARENT'S CWD, HERE. A parent opened with a relative
    ``--session sessions/…/x.jsonl`` holds a relative path, and a child that
    runs in a subdirectory would resolve the same string against its own cwd
    and find nothing (measured in the design critique: ``Failed to read session
    header``, exit 1). So the directory is derived from ``os.path.abspath`` in
    the parent, and every path this module writes is absolute.

    REFUSED OUTSIDE A BUCKET. A parent sitting directly in a sessions root would
    turn ``<stem>/`` into a new top-level bucket, which the global ``list()``
    scans — every child file would then feed ``--session <id prefix>`` and
    ``--resume`` lookups. The sessions root itself is not visible from here
    (``--session-dir`` is not), so the test is the conservative one: the
    parent's own directory must have a bucket's shape (:func:`_is_bucket`). A
    parent the repo created always passes; a hand-placed parent anywhere else
    gets no child file, and the start record says
    :data:`PARENT_OUTSIDE_A_BUCKET` — never a directory a picker might scan.
    """

    absolute = os.path.abspath(parent_path)
    parent_dir, name = os.path.split(absolute)
    if not _is_bucket(os.path.basename(parent_dir)):
        return None
    if name.endswith(_SESSION_SUFFIX) and len(name) > len(_SESSION_SUFFIX):
        stem = name[: -len(_SESSION_SUFFIX)]
    else:
        stem = name + CHILDREN_SUFFIX
    return Placement(directory=os.path.join(parent_dir, stem), rel_dir=stem)


@dataclass(frozen=True)
class ChildSessionFile:
    """The child session file one spawn was given."""

    session_id: str
    path: str
    """ABSOLUTE — see :func:`place_children`."""
    rel: str
    """``<stem>/<spawn id>.jsonl``, relative to the PARENT file's directory and
    always ``/``-separated, so a parent that moved together with its
    ``<stem>/`` directory still finds its children (#199 design §A.7).

    The resume reader (:func:`find_resume_target`) reads NEITHER this nor
    :attr:`path` as a location (#610 D6, critique B2): it derives the file from
    where the parent is now (:func:`place_children` and the first run's key)
    and only compares ``rel`` with that, so a forged record cannot point a
    resume at a file of its choosing."""

    def as_record(self) -> dict[str, str]:
        return {"session_id": self.session_id, "path": self.path, "rel": self.rel}


def usage_record(
    *, input: int, output: int, cache_read: int, cache_write: int, cost: float
) -> dict[str, Any]:
    """The ``usage`` object a settle and a final carry — the FLOWS only.

    ``SubagentUsage.tokens`` is a context LEVEL (the last message's whole
    context) and is deliberately not here; it rides the settle as
    ``context_tokens``, where nothing sums it. Counters are ints and ``cost`` a
    finite float, because ADR-0242 rule 1.7 leaves JSON hygiene to the writer.
    """

    return {
        "input": _count(input),
        "output": _count(output),
        "cache_read": _count(cache_read),
        "cache_write": _count(cache_write),
        "cost": _money(cost),
    }


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _money(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


#: Outcomes in which the PARENT cut the run: Ctrl+C (``cancelled``), a
#: ``stop``/``stop_all`` (``aborted``) or the delegation's own deadline
#: (``timeout``). The parent stops reading the child's stream at once there.
CUT_BY_THE_PARENT = frozenset({"cancelled", "aborted", "timeout"})


def usage_is_complete(state: _StreamState, *, status: str | None = None) -> bool:
    """Did every model call the child made reach the parent with its usage?

    A child that never began a run (no ``agent_start``, which precedes the first
    model request) spent nothing, so its zeros are complete. Otherwise:

    * **the parent cut the run** (``status`` in :data:`CUT_BY_THE_PARENT`) —
      never complete. The parent stopped reading, and a request in flight then
      may have been billed without its ``message_end`` arriving. Not even an
      ``agent_end`` proves otherwise: the child's own abort path emits one
      without a ``message_end`` for the request it abandoned (Codex review).
    * **anything else** — complete only when the child's LATEST run closed on its
      own (``run_open`` false). ``run_open`` is last-event-wins, so a stream cut
      during an auto-retry, which is a second run after an ``agent_end``, still
      reads as unfinished; a child that died mid-turn leaves it open too.

    Before this check a Ctrl+C before any usage arrived recorded zeros as a
    confirmed "nothing spent" (Codex review of #199).
    """

    if not state.saw_agent_start:
        return True
    if status in CUT_BY_THE_PARENT:
        return False
    return not state.run_open


def cost_is_known(
    state: _StreamState,
    *,
    usage: dict[str, Any],
    cost: float,
    status: str | None = None,
) -> bool:
    """A.3b: is the recorded ``cost`` the whole bill, or only a floor?

    Never known while the usage itself is incomplete
    (:func:`usage_is_complete`, given the run's ``status``).
    Otherwise known when ANY of: a ``message_end`` carried a cost of its own
    (:attr:`_StreamState.cost_reported`); the registry fallback priced the run
    (:attr:`_StreamState.cost_priced`); a positive cost exists at all — which in
    the shipped channels can only have come from one of the first two, and is
    stated so an injected channel that reports a cost is read the same way; or
    every token counter is zero, i.e. nothing was spent.

    Never known when ``cost`` itself is not a finite, non-negative number: a
    wrong bill is worse than an absent one (``SessionStats.cost_known``).
    """

    if not usage_is_complete(state, status=status):
        return False
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return False
    try:
        finite = math.isfinite(float(cost))
    except (OverflowError, ValueError):
        return False
    if not finite or cost < 0:
        return False
    if state.cost_reported or state.cost_priced or cost > 0:
        return True
    return not any(
        usage[key] for key in ("input", "output", "cache_read", "cache_write")
    )


@dataclass
class SpawnReceipt:
    """Everything one ADMITTED spawn records, and the session it records into.

    Built by ``runtime._run`` synchronously, right after the registry insert and
    the first progress publish, so it exists on every path a registry row
    exists on — the only spawns that get records at all. Refusals before the
    insert (drain, live cap, budget), consent declines and batch refusals spent
    nothing, and their tool-result text already persists them.

    MUTABLE, and filled in by :meth:`open`: the parent :attr:`session` is
    captured ONCE there and never re-read, so a settle that arrives after
    ``/new``, ``/resume`` or quit still lands in the session that ran the child.
    """

    key: str
    tool_call_id: str | None
    index: int | None
    mode: str | None
    profile: str
    task_preview: str
    requested_model: str | None
    permission_mode: str
    limits: dict[str, Any] | None = None
    """:meth:`ChildLimits.to_record` of what bounds THIS run (#610 D8), on the
    start and on the settle. Built by ``runtime._run`` before the run is
    admitted, so the record builders stay total, and never mutated after."""
    resumes: str | None = None
    """The first run's ``key`` when this run is a resume of it, else ``None``
    (#610 D15). On every start and settle, so one record shape serves both."""
    aelix_version: str = VERSION
    session: Any | None = None
    """The parent ``Session`` every record goes to, or ``None`` (nothing is
    recorded). A session with no file — ``--no-session``, in-memory — still
    takes the records; only its child runs ``--no-session``."""
    parent_session_id: str | None = None
    parent_path: str | None = None
    """ABSOLUTE path of the parent's file, or ``None`` when it has none."""
    child: ChildSessionFile | None = None
    child_error: str | None = None
    opening_attempted: int = 0
    """How many of the opening records (start, then pending) were ATTEMPTED.

    Counted one record at a time, after its append returns. A cancel that
    lands while one is being written leaves that one, and every one after it,
    to the ``finally``, which writes them ahead of the settle (at least once:
    a record the store had already taken lands twice, and a reader folds by
    key). Attempted, not confirmed: a storage error that swallowed one is
    logged, and re-trying it from the ``finally`` would only log it again."""

    # ── the parent, captured once ─────────────────────────────────

    async def open(self, session: Any | None, *, cwd: str) -> None:
        """Capture the parent, allocate the child file, write start + pending.

        ``cwd`` is the CHILD's working directory — it goes into the child's
        header, as ``repo.create`` would have written it.
        """

        self.session = session
        if session is not None:
            await self._describe_parent(session)
            await self._allocate(cwd=cwd)
        await self._write_opening()

    async def attach(self, session: Any | None, *, child: ChildSessionFile) -> None:
        """Capture the parent, ADOPT an existing child file, write start + pending.

        The resume run's :meth:`open` (#610 D12). :meth:`_allocate` refuses a
        name that is taken — the guard against a colliding spawn id silently
        taking over another child's transcript — so a resume cannot go through
        it and adopts ``child`` instead: the file a resume target resolved
        (:func:`find_resume_target`), under this run's NEW key.

        NOTHING IS WRITTEN TO THE CHILD'S FILE. The child holds that file's
        writer lock while it runs and appends its own transcript; a record the
        parent slipped in between the dangling tool call a stopped child left
        and the new prompt is what made every later turn fail with 400 in pi
        (#8476, #8502). The start and the pending go to the PARENT, counted in
        :attr:`opening_attempted` exactly as :meth:`open` counts them.
        """

        self.session = session
        self.child = child
        if session is not None:
            await self._describe_parent(session)
        await self._write_opening()

    async def _write_opening(self) -> None:
        for record in self.opening_records():
            await append_records(self.session, [record])
            self.opening_attempted += 1

    async def _describe_parent(self, session: Any) -> None:
        try:
            raw_path = session.session_file
        except Exception:  # noqa: BLE001 — no path is a parent with no file
            raw_path = None
        if isinstance(raw_path, str) and raw_path:
            self.parent_path = os.path.abspath(raw_path)
        try:
            metadata = await session.get_metadata()
        except Exception:  # noqa: BLE001 — the id is descriptive, never required
            metadata = None
        session_id = getattr(metadata, "id", None)
        self.parent_session_id = session_id if isinstance(session_id, str) else None

    async def _allocate(self, *, cwd: str) -> None:
        """Publish the child's session file, or record why there is none.

        ONE atomic publish (``JsonlSessionStorage.create(entries=…)``, ADR-0242
        §4): the header and the origin appear together or not at all, 0600 in a
        0700 directory (``LocalFileSystem``). The header's ``parentSession``
        stays unset — that field is FORK lineage, and a delegated child is not a
        fork of its parent; the link is the origin record instead.

        EVERY failure is a result, never a raise: an ``OSError``, a path over
        Windows' ``MAX_PATH`` (the child name is the 22-character spawn id for
        exactly that reason), a name that is already taken. The child then runs
        ``--no-session`` and :attr:`child_error` says why.
        """

        if self.parent_path is None:
            return
        placement = place_children(self.parent_path)
        if placement is None:
            self.child_error = PARENT_OUTSIDE_A_BUCKET
            return
        name = f"{self.key}{_SESSION_SUFFIX}"
        path = os.path.join(placement.directory, name)
        session_id = str(uuid.uuid4())
        origin = CustomEntry(
            id=uuid.uuid4().hex[:8],
            # ``None``, and it has to be: the child's first ``get_branch()``
            # walks from its leaf to the root, and a parent id naming an entry
            # that is not in the file raises ``invalid_session`` before the
            # child's first turn.
            parent_id=None,
            timestamp=_iso_now(),
            custom_type=CHILD_ORIGIN_TYPE,
            data=self.origin_record(),
        )
        try:
            # ``os.replace`` would silently take over an existing file, so a
            # collision (two 48-bit spawn ids in one parent's directory) is a
            # refusal rather than a lost child record.
            if os.path.lexists(path):
                self.child_error = "a child session file with this name already exists"
                return
            await JsonlSessionStorage.create(
                LocalFileSystem(),
                path,
                cwd=cwd,
                session_id=session_id,
                entries=[origin],
            )
        except Exception as exc:  # noqa: BLE001 — allocation never fails a delegation
            self.child_error = _short(f"could not create the child session file: {exc}")
            return
        self.child = ChildSessionFile(
            session_id=session_id, path=path, rel=f"{placement.rel_dir}/{name}"
        )

    # ── the records ───────────────────────────────────────────────

    def origin_record(self) -> dict[str, Any]:
        """``aelix.child_origin`` — the child file's first entry."""

        return {
            "v": RECORD_VERSION,
            "key": self.key,
            "parent": {"session_id": self.parent_session_id, "path": self.parent_path},
            "tool_call_id": self.tool_call_id,
            "index": self.index,
            "mode": self.mode,
            "profile": self.profile,
            "permission_mode": self.permission_mode,
            "aelix_version": self.aelix_version,
        }

    def _identity(self, phase: str) -> dict[str, Any]:
        return {
            "v": RECORD_VERSION,
            "key": self.key,
            "phase": phase,
            "tool_call_id": self.tool_call_id,
            "index": self.index,
            "mode": self.mode,
            "profile": self.profile,
            "task_preview": self.task_preview,
            "child": self.child.as_record() if self.child is not None else None,
            "requested_model": self.requested_model,
            "permission_mode": self.permission_mode,
            "aelix_version": self.aelix_version,
            "limits": self.limits,
            "resumes": self.resumes,
        }

    def start_record(self) -> dict[str, Any]:
        return {**self._identity("start"), "error": self.child_error}

    def pending_record(self) -> dict[str, Any]:
        return {"v": RECORD_VERSION, "key": self.key, "state": "pending"}

    def opening_records(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (CHILD_SESSION_TYPE, self.start_record()),
            (USAGE_TYPE, self.pending_record()),
        ]

    def _unwritten_opening(self) -> list[tuple[str, dict[str, Any]]]:
        """The opening records :meth:`open` never got as far as attempting."""

        return self.opening_records()[self.opening_attempted :]

    def _closing(
        self, settle: dict[str, Any], usage: dict[str, Any], cost_known: bool
    ) -> list[tuple[str, dict[str, Any]]]:
        final = {
            "v": RECORD_VERSION,
            "key": self.key,
            "state": "final",
            "usage": usage,
            "cost_known": cost_known,
        }
        return [(CHILD_SESSION_TYPE, settle), (USAGE_TYPE, final)]

    def outcome_records(
        self, result: SubagentResult, *, state: _StreamState
    ) -> list[tuple[str, dict[str, Any]]]:
        """Settle + final for a delegation that returned an envelope.

        The envelope is what ACTUALLY happened — its ``model`` and ``provider``
        are what the child ran, not what was asked for — and the stream state
        is the evidence behind ``cost_known`` (:func:`cost_is_known`).
        """

        spend = result.usage
        usage = usage_record(
            input=spend.input,
            output=spend.output,
            cache_read=spend.cache_read,
            cache_write=spend.cache_write,
            cost=spend.cost,
        )
        known = cost_is_known(state, usage=usage, cost=spend.cost, status=result.status)
        settle = {
            **self._identity("settle"),
            "status": result.status,
            "model": result.model,
            "provider": result.provider,
            "usage": usage,
            "cost_known": known,
            "context_tokens": _count(spend.tokens),
            "turns": _count(spend.turns),
            "elapsed_ms": _count(result.elapsed_ms),
            "exit_code": result.exit_code,
            "stop_reason": result.stop_reason,
            "truncated": bool(result.truncated),
            "summary_bytes": _utf8_len(result.summary),
            "details_bytes": _utf8_len(result.details),
            "error": _short(result.error),
        }
        return self._closing(settle, usage, known)

    def unsettled_records(
        self,
        state: _StreamState,
        *,
        status: str,
        error: str | None,
        elapsed_ms: int,
        exit_code: int | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """What the ``finally`` writes for a delegation that got no envelope.

        A cancel (``status="cancelled"``) or an exception out of the channel
        (``"error"``): the only evidence is the live stream state the channel
        was folding into, so that is what is recorded — partial usage included,
        which the caller has priced first. ``summary_bytes`` is 0 because no
        result reached the parent; ``details_bytes`` is the partial answer the
        child had streamed. Whatever of the start and the pending :meth:`open`
        never got as far as attempting comes first.
        """

        usage = usage_record(
            input=state.input,
            output=state.output,
            cache_read=state.cache_read,
            cache_write=state.cache_write,
            cost=state.cost,
        )
        known = cost_is_known(state, usage=usage, cost=state.cost, status=status)
        settle = {
            **self._identity("settle"),
            "status": status,
            "model": state.model,
            "provider": state.provider,
            "usage": usage,
            "cost_known": known,
            "context_tokens": _count(state.tokens),
            "turns": _count(state.turns),
            "elapsed_ms": _count(elapsed_ms),
            "exit_code": exit_code if isinstance(exit_code, int) else None,
            "stop_reason": state.stop_reason,
            "truncated": False,
            "summary_bytes": 0,
            "details_bytes": _utf8_len(state.summary),
            "error": _short(error),
        }
        return [*self._unwritten_opening(), *self._closing(settle, usage, known)]

    def bare_records(
        self, spend: Any, *, status: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Settle + final for when building the real ones RAISED.

        The fallback that keeps "recording never changes a delegation" true
        whatever a builder does: the identity, the status and the flows, read
        from ``spend`` (the envelope's usage or the live stream) through the
        total :func:`usage_record`; every other settle field is ``null``. And
        ``cost_known`` is ``false``: a record that could not be built is no
        evidence that its cost is the whole bill.
        """

        usage = usage_record(
            input=getattr(spend, "input", 0),
            output=getattr(spend, "output", 0),
            cache_read=getattr(spend, "cache_read", 0),
            cache_write=getattr(spend, "cache_write", 0),
            cost=getattr(spend, "cost", 0.0),
        )
        settle = {
            **self._identity("settle"),
            "status": status,
            "model": None,
            "provider": None,
            "usage": usage,
            "cost_known": False,
            "context_tokens": None,
            "turns": None,
            "elapsed_ms": None,
            "exit_code": None,
            "stop_reason": None,
            "truncated": None,
            "summary_bytes": None,
            "details_bytes": None,
            "error": RECORD_BUILD_FAILED,
        }
        return [*self._unwritten_opening(), *self._closing(settle, usage, False)]


async def append_records(
    session: Any | None, records: Sequence[tuple[str, dict[str, Any]]]
) -> None:
    """Append each record as one ``CustomEntry`` line. Logs and swallows failure.

    ``Session.append_custom_entry`` is awaited directly rather than through
    ``ExtensionAPI.append_entry``, which is fire-and-forget (its order against
    the tool result is not fixed) and raises when no session is attached.

    NO LOCK. ``JsonlSessionStorage`` already serialises its appends and
    ``LocalFileSystem`` never yields inside one, so a record lands whole and in
    order. A lock would add a suspension point on the cancel path, which is
    where a second Ctrl+C measurably lost both settles of a batch (design
    critique, probe P.3 case C).

    One record's failure does not stop the next: a settle that could not be
    written is still worth a final.
    """

    if session is None:
        return
    for custom_type, data in records:
        try:
            await session.append_custom_entry(custom_type, data)
        except Exception:  # noqa: BLE001 — recording never changes a delegation
            logger.debug(
                "could not record %s for %s", custom_type, data.get("key"), exc_info=True
            )


# === The first reader: which child may this parent resume (#610 D5–D7) =========
#
# Every value below comes out of a session file, which is UNTRUSTED input
# (critique B2): a forged parent record must not be able to name a file, an
# argv fragment or a model-facing sentence of its choosing. So the record is
# read for identity only — the child FILE is derived from where the parent is
# now, its contents are checked against the record, the limits are validated
# field by field (:meth:`ChildLimits.from_record`), and nothing a record says
# reaches the model except through :func:`~aelix_agents.envelope.child_line`
# and a byte budget. Every helper is total over whatever JSON it is handed.

SPAWN_KEY = re.compile(r"sub-[0-9a-f]{12}")
"""``runtime._new_id``'s shape. A delegation's first key names its child file,
so a key of any other shape is refused rather than joined onto a path."""

_SESSION_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}")
"""What a listed child id may look like: the uuid4 :meth:`SpawnReceipt._allocate`
mints, with room to spare. A record whose id is anything else is not listed."""

_PROFILE_NAME_MAX_CHARS = 256

CHILD_SCAN_BYTES = 2 * 1024 * 1024
"""How much of a child file the resume check reads (#610 D6).

The header, the origin and the child's first user message — the task, at most
``chain.MAX_TASK_BYTES`` plus the resume preamble — are the first entries of
every child file, so 2 MiB covers them with room for whatever a child's
startup records before its first prompt. The rest of a long transcript is
never read."""

LISTED_AGENTS = 10
"""At most this many agents in a resume refusal's listing (#610 D7)."""

LISTING_BYTES = 2048
"""The listing's whole UTF-8 budget, head and overflow line included."""

LISTING_TASK_CHARS = 80
LISTING_PROFILE_CHARS = 40

RESUMABLE_HEAD = "Agents this conversation can resume, newest first:"
RESUMABLE_NONE = "This conversation has no delegated agent it can resume."

_LAST_STATUS = {
    "ok": "finished",
    "aborted": "stopped",
    "cancelled": "stopped",
    "timeout": "stopped",
    "error": "failed",
}
"""A settle's ``status`` as the listing and the resume dialog say it. A run
with no settle on this branch — the parent died mid-run, or the run is on
another branch — is ``unknown``: a start alone is no outcome (ADR-0243)."""


@dataclass(frozen=True)
class ResumeTarget:
    """One child this parent may resume, as :func:`find_resume_target` found it.

    Everything here was either derived (the path) or checked against the
    child's own file (the ids, the origin) — except :attr:`task_preview` and
    :attr:`profile`, which are the first run's record as written and must be
    sanitised before they are shown.
    """

    session_id: str
    """The child session id — the ``Session:`` line a model was shown, and the
    header id of :attr:`path`. Stable across every resume of the child."""
    first_key: str
    """The first run's spawn key: the child file's name and every resume's
    ``resumes``."""
    path: str
    """ABSOLUTE, derived as allocation placed it: ``place_children(<the
    parent's file, as its session names it now>).directory / <first_key>.jsonl``
    (#610 D6, G1) — the ``--session`` the first run was launched with, for a
    parent opened by the same name."""
    rel: str
    limits: ChildLimits
    """The FIRST run's limits, read off its START record — the ceiling of every
    resume (#610 D9, F2)."""
    profile: str
    """The profile name the first run resolved. A resume re-resolves THIS name
    (model door, no project scope) and refuses another file."""
    task_preview: str
    """The first run's task, as far as the record kept it (≤ 200 characters)."""
    last_status: str
    """``finished`` / ``stopped`` / ``failed`` / ``unknown`` — the latest run
    as THIS branch records it. Another branch may have resumed the child since
    (ADR-0277 limit 2), which is why the resume preamble does not depend on it."""
    parent_session_id: str
    """The parent session the target was checked against. ``resume_granted``
    refuses if the parent's session is no longer that one."""

    @property
    def child_file(self) -> ChildSessionFile:
        return ChildSessionFile(session_id=self.session_id, path=self.path, rel=self.rel)


@dataclass(frozen=True)
class _Run:
    """One run as this branch records it.

    Two lines of it matter, for two different questions. HOW IT ENDED is its
    key's LAST ``aelix.child_session`` line (:attr:`data` — ADR-0243: a settle
    is complete and supersedes its start). WHAT IT WAS — the profile, the task,
    the child file, the limits that bounded it — is its key's FIRST START line
    (:attr:`start`), written before the child existed (#610 F2): a later line
    of the same key must not be able to rewrite the ceiling a resume runs
    under, so a line that disagrees about the limits is damage, not news.
    """

    key: str
    data: dict[str, Any]
    """The key's LAST line — the outcome."""
    position: int
    """Where that line sits on the branch — newer runs are further down."""
    start: dict[str, Any] | None
    """The key's FIRST ``phase: "start"`` line, or ``None`` when the branch has
    none for it."""
    limits_agree: bool
    """Does every line of the key carry the ``limits`` :attr:`start` carries?
    ``False`` with no start. A genuine run's lines always agree: the start, the
    settle and a start the ``finally`` re-wrote all copy one receipt's
    ``_identity``."""

    @property
    def identity(self) -> dict[str, Any]:
        """The line that says what this run WAS: its start, else its last line
        (a run whose start is not on this branch)."""

        return self.start if self.start is not None else self.data


def _fold_runs(entries: Sequence[Any]) -> dict[str, _Run]:
    lines: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for position, entry in enumerate(entries):
        if getattr(entry, "type", None) != "custom":
            continue
        if getattr(entry, "custom_type", None) != CHILD_SESSION_TYPE:
            continue
        data = getattr(entry, "data", None)
        # A version this module does not know is ignored, like an unknown
        # ``customType`` (ADR-0242 rule 1.6).
        if not isinstance(data, dict) or data.get("v") != RECORD_VERSION:
            continue
        key = data.get("key")
        if isinstance(key, str) and key:
            lines.setdefault(key, []).append((position, data))
    runs: dict[str, _Run] = {}
    for key, seen in lines.items():
        position, data = seen[-1]
        start = next((line for _at, line in seen if line.get("phase") == "start"), None)
        runs[key] = _Run(
            key=key,
            data=data,
            position=position,
            start=start,
            limits_agree=start is not None
            and all(line.get("limits") == start.get("limits") for _at, line in seen),
        )
    return runs


def _group_by_child(runs: dict[str, _Run]) -> dict[str, list[_Run]]:
    """Runs keyed by the child SESSION they ran in — a first run and its resumes.
    A run with no child file (``child: null``) has nothing to resume. The child
    a run ran in is what its start says (:attr:`_Run.identity`)."""

    groups: dict[str, list[_Run]] = {}
    for run in runs.values():
        child = run.identity.get("child")
        session_id = child.get("session_id") if isinstance(child, dict) else None
        if isinstance(session_id, str) and session_id:
            groups.setdefault(session_id, []).append(run)
    return groups


def _lineage(runs: list[_Run]) -> tuple[_Run, _Run] | str:
    """``(first run, latest run)`` of one child, or why it has none (#610 D5, F2).

    The first run is the one whose ``resumes`` is null — the run that allocated
    the file — and every resume must name it. A record from before #610 has no
    ``resumes`` key, which reads as null: it is a first run, and is refused
    later for having no ``limits``.

    The first run is READ FROM ITS START (:attr:`_Run.start`): its limits are
    the ceiling of every resume (D9), and the start is the line written before
    the child existed. A first run with no start on this branch is a
    delegation this conversation's record does not hold
    (:data:`RESUME_FIRST_RUN_MISSING`); one whose lines disagree about its
    limits is damaged — a later line naming wider limits is how a ceiling would
    be rewritten (Codex review 1, verify M1).
    """

    firsts = [run for run in runs if run.identity.get("resumes") is None]
    if not firsts:
        return RESUME_FIRST_RUN_MISSING
    if len(firsts) > 1:
        return resume_damaged("two delegations claim one session")
    first = firsts[0]
    for run in runs:
        resumed = run.identity.get("resumes")
        if resumed is not None and resumed != first.key:
            return resume_damaged("its runs do not chain to one delegation")
    if first.start is None:
        return RESUME_FIRST_RUN_MISSING
    if not first.limits_agree:
        return resume_damaged("its records disagree about the limits it ran with")
    return first, max(runs, key=lambda run: run.position)


def _by_the_model(data: dict[str, Any]) -> bool:
    """Was this run started by the MODEL's ``agent`` tool (#610 D5, critique m4)?

    ``/agents run`` and RPC ``agents/run`` record ``tool_call_id: null``. Their
    task was typed by a person, the model never saw it, and the resume dialog's
    "written by the model" would be false of it.
    """

    tool_call_id = data.get("tool_call_id")
    return isinstance(tool_call_id, str) and bool(tool_call_id)


def _last_status(data: dict[str, Any]) -> str:
    if data.get("phase") != "settle":
        return "unknown"
    status = data.get("status")
    return _LAST_STATUS.get(status, "unknown") if isinstance(status, str) else "unknown"


def _session_file_of(session: Any) -> str | None:
    try:
        raw = session.session_file
    except Exception:  # noqa: BLE001 — no path is a parent with no file
        return None
    return raw if isinstance(raw, str) and raw else None


async def session_id_of(session: Any) -> str | None:
    """The id of ``session`` (its header's), or ``None`` when it has none or it
    cannot be read. Never raises."""

    try:
        metadata = await session.get_metadata()
    except Exception:  # noqa: BLE001 — an unreadable id is no id
        return None
    session_id = getattr(metadata, "id", None)
    return session_id if isinstance(session_id, str) and session_id else None


async def _branch_runs(session: Any) -> dict[str, list[_Run]] | None:
    """The parent's CURRENT branch, folded and grouped — the records ``/stats``
    reads too (``harness/core.py``'s branch walk). ``None`` when unreadable."""

    try:
        branch = await session.get_branch()
    except Exception:  # noqa: BLE001 — an unreadable record finds nothing
        logger.debug("could not read the parent's branch", exc_info=True)
        return None
    return _group_by_child(_fold_runs(branch))


async def find_resume_target(session: Any | None, child_id: str | None) -> ResumeTarget | str:
    """The child ``child_id`` names, if THIS parent may resume it; else the refusal.

    #610 D5–D7. ``session`` is the parent's live session (``SubagentHost.session``)
    and ``child_id`` the id the model passed: a child SESSION id, the one on
    an agent result's ``Session:`` line. In order, the answer is a refusal when:

    1. the parent has no session file, or it sits outside a bucket — no child
       of it has a file (:func:`place_children`, as allocation decides);
    2. no ``child_id`` was given, or no run on the parent's CURRENT branch ran
       in that child session — both carry the listing (:func:`resumable_agents`
       words), because a stop the parent never saw the result of (``/new``,
       quit) leaves a child the model has no id for (D7);
    3. the child's runs do not chain to one first run on this branch, the
       first run's START is not on it, or its lines disagree about its limits
       (:func:`_lineage`; F2 — everything below is read off that start);
    4. the first run was not the model's (``/agents run``, RPC);
    5. it has no ``limits`` (recorded before #610), or they do not validate;
    6. the file derived as allocation placed it — :func:`place_children` of
       the parent's file as its session names it, links and all (G1) — does
       not match the record, the record points under another parent (a fork),
       or the file is missing, not a regular file, one with a second name (a
       hard link), or reached through a link in either of the two components
       below the parent's directory: the children's directory and the file
       (F3, F4);
    7. its header is not one the session loader would open, or not that
       session's (F6); its origin is not that run's, or names another parent
       session (a fork, or a copied file);
    8. it holds no user message: the child never received its task (m5).

    Only the chosen child's file is opened, and only its first
    :data:`CHILD_SCAN_BYTES`. NOTHING IS WRITTEN anywhere. The refusals are
    complete, model-facing sentences (``RESUME_*``).

    ``runtime.resume_granted`` runs THIS function again right before the
    launch (F3), so what it checks holds at the hook and at execute alike.
    """

    parent_path = _session_file_of(session) if session is not None else None
    # Placed EXACTLY as the first run placed it (G1): ``_allocate`` takes
    # ``place_children`` of this same ``session_file`` (its ``abspath``), so a
    # resume is launched with the ``--session`` its first run had. A link at
    # or above the parent's directory — a linked sessions root or bucket, the
    # parent file itself a link — is the user's own setup and is taken as
    # allocation took it. The two components below the parent's directory,
    # the children's directory and the file, are this module's, and
    # :func:`_check_child_file` refuses a link in either.
    placement = place_children(parent_path) if parent_path is not None else None
    parent_id = await session_id_of(session) if placement is not None else None
    if placement is None or parent_id is None:
        return RESUME_NO_SESSION
    groups = await _branch_runs(session)
    if groups is None:
        return RESUME_UNREADABLE
    if not child_id:
        return f"{RESUME_NEEDS_ID}\n\n{_listing(groups)}"
    runs = groups.get(child_id)
    if runs is None:
        return f"{RESUME_UNKNOWN_ID}\n\n{_listing(groups)}"
    lineage = _lineage(runs)
    if isinstance(lineage, str):
        return lineage
    first, latest = lineage
    # The first run's START (F2), which ``_lineage`` has just required — never
    # its last line, and never the latest run's.
    data = first.identity
    if not _by_the_model(data):
        return RESUME_NOT_YOURS
    if data.get("limits") is None:
        return RESUME_BEFORE_LIMITS
    try:
        limits = ChildLimits.from_record(data.get("limits"))
    except LimitsError as exc:
        return resume_damaged(f"its recorded limits are invalid: {exc.reason}")
    if not SPAWN_KEY.fullmatch(first.key):
        return resume_damaged("its record's key is not a delegation's")
    profile = data.get("profile")
    if not isinstance(profile, str) or not 0 < len(profile) <= _PROFILE_NAME_MAX_CHARS:
        return resume_damaged("its record names no profile")
    # THE FILE IS DERIVED, NEVER READ OFF THE RECORD (D6, critique B2). The
    # record's ``rel`` is only compared: under another stem it is a fork's
    # copy of the original parent's record, and the child belongs there.
    name = f"{first.key}{_SESSION_SUFFIX}"
    rel = f"{placement.rel_dir}/{name}"
    child = data.get("child")
    recorded = child.get("rel") if isinstance(child, dict) else None
    if recorded != rel:
        if isinstance(recorded, str) and recorded.split("/", 1)[0] != placement.rel_dir:
            return RESUME_FORKED
        return resume_damaged("its recorded file does not match its key")
    path = os.path.join(placement.directory, name)
    refusal = _check_child_file(
        path,
        placement.directory,
        child_id=child_id,
        first_key=first.key,
        parent_id=parent_id,
    )
    if refusal is not None:
        return refusal
    preview = data.get("task_preview")
    return ResumeTarget(
        session_id=child_id,
        first_key=first.key,
        path=path,
        rel=rel,
        limits=limits,
        profile=profile,
        task_preview=preview[:TASK_PREVIEW_CHARS] if isinstance(preview, str) else "",
        last_status=_last_status(latest.data),
        parent_session_id=parent_id,
    )


def _check_child_file(
    path: str, directory: str, *, child_id: str, first_key: str, parent_id: str
) -> str | None:
    """Steps 6–8 of :func:`find_resume_target` on the derived file. ``None`` = fine.

    ``path`` is ``directory`` / the file's name, and ``directory`` the
    children's directory inside the parent's, as allocation placed them. What
    is at or above the parent's directory is the user's and may be a link
    (G1). The two components below it are this module's and may not (F3):
    both are ``lstat``-ed, and then the file's ``realpath`` must be the
    ``realpath`` of the parent's directory with those two names appended —
    the whole rule in one comparison, which also catches what ``lstat`` does
    not call a link (a Windows junction) and a ``..``. A file with a second
    NAME is refused too (F4): another path to the same bytes is another writer
    lock (``SessionWriterLock`` locks by name), and so another writer.

    The header is read by the session loader's own parser (F6), so a file the
    child would refuse to open — a version it does not know, a header with no
    timestamp — is refused here, before the resume spends anything.
    """

    try:
        if stat.S_ISLNK(os.lstat(directory).st_mode):
            return resume_damaged("its session directory is a symbolic link")
        info = os.lstat(path)
    except FileNotFoundError:
        return RESUME_FILE_GONE
    except OSError:
        return resume_damaged("its session file cannot be read")
    if stat.S_ISLNK(info.st_mode):
        return resume_damaged("its session file is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        return resume_damaged("its session file is not a regular file")
    # ``> 1``, not ``!= 1``: a platform that reports no link count (0) is no
    # evidence of a second name.
    if info.st_nlink > 1:
        return resume_damaged("its session file has another name (a hard link)")
    parent_dir, children = os.path.split(directory)
    unlinked = os.path.join(os.path.realpath(parent_dir), children, os.path.basename(path))
    if os.path.normcase(os.path.realpath(path)) != os.path.normcase(unlinked):
        return resume_damaged("its session file is reached through a link")
    try:
        lines = _head_lines(*_read_head(path))
    except OSError:
        return resume_damaged("its session file cannot be read")
    header = None
    if lines and lines[0] is not None:
        # The loader's message names the path; the model reads ours instead.
        with contextlib.suppress(SessionError):
            header = _parse_header_line(lines[0], path)
    if header is None:
        return resume_damaged("its session header is not valid")
    if header.id != child_id:
        return resume_damaged("its session header does not match")
    entries = [_parse_line(line) for line in lines]
    origin = entries[1] if len(entries) > 1 else None
    data = (
        origin.get("data")
        if isinstance(origin, dict)
        and origin.get("type") == "custom"
        and origin.get("customType") == CHILD_ORIGIN_TYPE
        else None
    )
    if not isinstance(data, dict) or data.get("key") != first_key:
        return resume_damaged("its origin record does not match")
    parent = data.get("parent")
    if not isinstance(parent, dict) or parent.get("session_id") != parent_id:
        # Another parent session spawned it. A fork copies the receipts but
        # not the children, which stay under — and belong to — the original.
        return RESUME_FORKED
    if not any(_is_user_message(entry) for entry in entries[2:]):
        return RESUME_NEVER_STARTED
    return None


def _read_head(path: str) -> tuple[bytes, bool]:
    """The first :data:`CHILD_SCAN_BYTES` of ``path``, and whether that is all of it.

    ``O_NOFOLLOW`` where the platform has it, and a regular-file check on the
    descriptor itself, so a file swapped for a symlink after the ``lstat`` is
    refused rather than followed.
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        chunks: list[bytes] = []
        size = 0
        while size < CHILD_SCAN_BYTES:
            chunk = os.read(fd, min(65536, CHILD_SCAN_BYTES - size))
            if not chunk:
                return b"".join(chunks), True
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks), not os.read(fd, 1)
    finally:
        os.close(fd)


_LINE_BREAK = re.compile(rb"\r\n|\r|\n")
"""The line ends the session loader sees: it reads the file as text with
universal newlines (``LocalFileSystem.read_text_file`` → ``Path.read_text``),
which turns ``\\r\\n`` and a lone ``\\r`` into ``\\n`` before it splits (F6)."""


def _head_lines(head: bytes, complete: bool) -> list[str | None]:
    """The head's non-blank lines, split and decoded as the session loader would.

    In order; ``None`` for a line that is not UTF-8 (the loader decodes
    strictly). A blank line is skipped, as the loader skips it, so the first
    entry is the line the loader takes for the header. The scan bound may cut
    the last line, and that one is dropped.
    """

    pieces = _LINE_BREAK.split(head)
    if not complete:
        pieces = pieces[:-1]
    lines: list[str | None] = []
    for raw in pieces:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            lines.append(None)
            continue
        if text.strip():
            lines.append(text)
    return lines


def _parse_line(line: str | None) -> Any:
    """One line's JSON value, or ``None`` for a line that is not JSON.

    A torn line — the tail a SIGKILL left, healed with a newline on the next
    open (``jsonl_storage``) — stays a ``None`` in its place, so the header and
    the origin keep their positions.
    """

    if line is None:
        return None
    try:
        return json.loads(line)
    except (ValueError, RecursionError):
        return None


def _is_user_message(entry: Any) -> bool:
    if not isinstance(entry, dict) or entry.get("type") != "message":
        return False
    message = entry.get("message")
    return isinstance(message, dict) and message.get("role") == "user"


async def resumable_agents(session: Any | None) -> str:
    """The listing a resume refusal carries (#610 D7), for a caller with no id.

    :func:`find_resume_target` already appends it to its own "needs an id" and
    "unknown id" refusals; this is the same text on its own.
    """

    if session is None:
        return RESUMABLE_NONE
    groups = await _branch_runs(session)
    return _listing(groups) if groups is not None else RESUMABLE_NONE


def _listing(groups: dict[str, list[_Run]]) -> str:
    """Children this branch could resume, newest first, from the RECORDS ONLY.

    No candidate's file is opened (critique cut): the listing is a pointer, and
    the one id the model then picks is checked in full. So an entry can still be
    refused — a deleted file, a fork — and the cost of listing stays the fold
    the stats already do. Only model-door runs with recorded limits are listed
    (D5, D9). At most :data:`LISTED_AGENTS` entries inside
    :data:`LISTING_BYTES`; the rest are counted, not shown.
    """

    rows: list[tuple[int, str, _Run, _Run]] = []
    for session_id, runs in groups.items():
        if not _SESSION_ID.fullmatch(session_id):
            continue
        lineage = _lineage(runs)
        if isinstance(lineage, str):
            continue
        first, latest = lineage
        if not _by_the_model(first.identity) or not isinstance(
            first.identity.get("limits"), dict
        ):
            continue
        rows.append((latest.position, session_id, first, latest))
    if not rows:
        return RESUMABLE_NONE
    rows.sort(key=lambda row: row[0], reverse=True)
    lines = [RESUMABLE_HEAD]
    size = _utf8_len(RESUMABLE_HEAD)
    # Room for the overflow line, so adding it can never break the budget.
    reserve = _utf8_len(f"\n- … and {len(rows)} more not shown")
    for _position, session_id, first, latest in rows[:LISTED_AGENTS]:
        line = _listing_line(session_id, first, latest)
        cost = 1 + _utf8_len(line)
        if size + cost + reserve > LISTING_BYTES:
            break
        lines.append(line)
        size += cost
    more = len(rows) - (len(lines) - 1)
    if more:
        lines.append(f"- … and {more} more not shown")
    return "\n".join(lines)


def _listing_line(session_id: str, first: _Run, latest: _Run) -> str:
    # What the first run WAS is its start (F2); how the latest one ended, its
    # last line.
    profile = first.identity.get("profile")
    preview = first.identity.get("task_preview")
    shown_profile = _one_line(profile if isinstance(profile, str) else "", LISTING_PROFILE_CHARS)
    shown_task = _one_line(preview if isinstance(preview, str) else "", LISTING_TASK_CHARS)
    task = json.dumps(shown_task, ensure_ascii=False)
    return f"- {session_id} ({shown_profile or '?'}, {_last_status(latest.data)}): {task}"


def _one_line(text: str, limit: int) -> str:
    """Record text as a listing shows it: :func:`child_line`'s cleaning, no lone
    surrogate, whitespace collapsed, at most ``limit`` characters."""

    cleaned = "".join(ch for ch in child_line(text) if unicodedata.category(ch) != "Cs")
    flat = " ".join(cleaned.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


__all__ = [
    "CHILDREN_SUFFIX",
    "CHILD_ORIGIN_TYPE",
    "CHILD_SCAN_BYTES",
    "CHILD_SESSION_TYPE",
    "ERROR_PREVIEW_CHARS",
    "LISTED_AGENTS",
    "LISTING_BYTES",
    "PARENT_OUTSIDE_A_BUCKET",
    "RECORD_BUILD_FAILED",
    "RECORD_VERSION",
    "RESUMABLE_HEAD",
    "RESUMABLE_NONE",
    "RESUME_BEFORE_LIMITS",
    "RESUME_CONVERSATION_CHANGED",
    "RESUME_CWD_GONE",
    "RESUME_DAMAGED",
    "RESUME_FILE_GONE",
    "RESUME_FIRST_RUN_MISSING",
    "RESUME_FORKED",
    "RESUME_IN_USE",
    "RESUME_LOCK_ERROR",
    "RESUME_NEEDS_ID",
    "RESUME_NEVER_STARTED",
    "RESUME_NOT_YOURS",
    "RESUME_NO_SESSION",
    "RESUME_PROFILE_CHANGED",
    "RESUME_ROUTE_MOVED",
    "RESUME_RUNNING",
    "RESUME_UNKNOWN_ID",
    "RESUME_UNREADABLE",
    "RESUME_UNSUPPORTED_CHANNEL",
    "SPAWN_KEY",
    "TASK_PREVIEW_CHARS",
    "USAGE_TYPE",
    "ChildLimits",
    "ChildSessionFile",
    "LimitsError",
    "Placement",
    "ResumeRefused",
    "ResumeTarget",
    "SpawnReceipt",
    "append_records",
    "CUT_BY_THE_PARENT",
    "cost_is_known",
    "find_resume_target",
    "resumable_agents",
    "resume_damaged",
    "session_id_of",
    "usage_is_complete",
    "place_children",
    "usage_record",
]
