"""Profile → CLI surface projection (ADR-0196). Pure: no I/O, no globals.

AELIX-ORIGINAL. One profile has to reach the runtime through TWO channels that
must never disagree:

* :func:`profile_to_flags` / :func:`profile_to_argv` — an argv, used in P1 only
  to RENDER a dry run (``/agents show``) and, in P2, to launch a child process.
* :func:`apply_profile_to_args` — an in-process overlay onto the
  :class:`~aelix_coding_agent.cli.args.Args` the harness factory closes over
  (``cli/entry.py:2190-2197``).

The emission table below is written once and both functions follow it row for
row; ``tests/agents/test_profile_resolver.py::test_anti_drift_parity`` pins the
equivalence on THREE planes (Args fields, resolved active tools, built harness
options) because an ``Args``-vs-``Args`` assertion alone is structurally blind:
two sides can agree on a field that nothing reads, and both can collapse
``tools == ()`` onto "all tools active".

| condition                | flag                            | overlay                                  |
|--------------------------|---------------------------------|------------------------------------------|
| ``model`` not None       | ``--model <m>``                 | ``model = m``                            |
| ``provider`` not None    | ``--provider <p>``              | ``provider = p``                         |
| NEITHER, ``parent_model``| ``--model <id> --provider <p>`` | *(nothing — see below)*                  |
| ``tools == ()``          | ``--no-tools``                  | ``no_tools = True``                      |
| ``tools`` non-empty      | ``--tools a,b`` (ONE token)     | ``tools = [a, b]``                       |
| ``tools is None``        | *(nothing)*                     | *(nothing)*                              |
| ``not builtin_tools``    | ``--no-builtin-tools``          | ``no_builtin_tools = True``              |
| ``not inherit_skills``   | ``--no-skills``                 | ``no_skills = True``                     |
| each ``skills`` entry    | ``--skill <abs>`` (repeatable)  | ``skills += [...]``                      |
| ``not inherit_extensions``| ``--no-extensions``            | ``no_extensions = True``                 |
| each ``extensions`` entry| ``-e <abs>`` (repeatable)       | ``extensions += [...]``                  |
| ``not context_files``    | ``--no-context-files``          | ``no_context_files = True``              |
| ``thinking`` not None    | ``--thinking <level>``          | ``thinking = level``                     |
| ``system_prompt=replace``| ``--system-prompt-file <p>``    | ``system_prompt = body``                 |
| ``system_prompt=append`` | ``--append-system-prompt-file`` | ``append_system_prompt.insert(0, body)`` |

Two flag choices are not cosmetic:

* ``--tools`` takes ONE comma-separated token and is NOT repeatable
  (``args.py:494-503`` overwrites), so the list is joined, never repeated.
* ``--system-prompt`` takes a LITERAL string (``args.py:434-438``); a profile
  body routinely exceeds what is safe to put in an argv (``ARG_MAX``, and it
  leaks in ``ps``), so the file-taking twins are used instead.

The ``append_system_prompt`` row is the sole exemption from "an explicit CLI
flag beats the profile": it is an ACCUMULATOR, so the profile's body and the
user's ``--append-system-prompt`` chunks coexist rather than compete. See
:func:`apply_profile_to_args`.

**The ``parent_model`` row is the one table entry with no overlay counterpart,
and that asymmetry is correct rather than drift.** The argv channel builds the
command line of a SEPARATE PROCESS, which runs its own model cascade from
scratch; the overlay channel edits the ``Args`` of the process that is ALREADY
running and therefore already holds a resolved model. A parent launched as
``aelix --provider anthropic --model claude-haiku-4-5 --agents`` carries its
model only in run scope — no profile declares it and nothing persists it — so
before this row the child was spawned with no model at all and died in about a
second with ``No model selected``. Forwarding is gated on the profile declaring
NEITHER ``model`` NOR ``provider``: a profile that names either is expressing an
intent about the child's identity, and pairing the parent's model id with the
profile's provider (or vice versa) would ship a combination neither side asked
for. See :func:`parent_model_flags`.

**The two channels agree on CONTENT unconditionally, and on ORDER for the argv
each is actually used with.** Two things had to be true for that, and both were
one-line divergences before the ADR-0196 review:

* ``prompt_path`` may be an agent profile's own ``.md`` — that is what
  ``/agents show`` renders and invites the user to copy — so
  ``cli/entry.py``'s prompt-file reader strips a leading frontmatter block
  (``_strip_prompt_frontmatter``). Reading the file whole put the profile's raw
  YAML into the system prompt on the argv side while the overlay side used the
  stripped ``AgentProfile.body``.
* ORDER agrees for a FRESH argv (``profile_to_argv`` builds one; a blank
  ``Args`` overlay is its counterpart): both put the body first because there is
  nothing before it. It cannot also agree when the profile's flags are appended
  to an argv that ALREADY carries ``--append-system-prompt`` — the overlay
  ``insert(0, body)``s (identity precedes the user's chunks) while a file-append
  flag lands last by construction. Neither rule may move: the first is asserted
  by ``tests/cli/test_agent_flag_end_to_end.py``, the second by
  ``tests/cli/test_prompt_file_flags.py``. Merging profile flags into a
  user-authored argv is therefore NOT a supported composition — the in-process
  overlay is what serves that case, and it is the one ``--agent`` uses.
"""

from __future__ import annotations

# ``collections.abc.Set`` IS ``typing.AbstractSet`` (the deprecated alias); the
# ``AbstractSet`` spelling is kept because it reads unambiguously next to the
# builtin ``set`` that callers actually pass.
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aelix_coding_agent.cli.args import Args

from .discovery import ProfileError
from .profile import AgentProfile

if TYPE_CHECKING:
    from aelix_ai.streaming import Model

_UNRESOLVED = "unknown"
"""The value ``Model``'s ``id`` / ``provider`` fields default to.

A bare ``Model()`` is a real thing to be handed — it is what a harness that
never resolved a model holds (#98/#100) — and it is NOT a model. Forwarding
``--model unknown`` would replace the child's honest ``No model selected`` with
a lookup failure for a model nobody named."""

PROFILE_OVERLAY_FIELDS: frozenset[str] = frozenset(
    {
        "model",
        "provider",
        "tools",
        "no_tools",
        "no_builtin_tools",
        "skills",
        "no_skills",
        "extensions",
        "no_extensions",
        "system_prompt",
        "append_system_prompt",
        "no_context_files",
        "thinking",
    }
)
""":class:`Args` field names a profile is allowed to touch.

Doubles as the comparison surface for the anti-drift test — the whole dataclass
cannot be compared because ``provided`` and ``diagnostics`` legitimately differ
between the argv channel and the overlay channel."""

_RECOMPUTE_DEFAULT_PROVIDER = "recompute-default-provider"
"""Notice emitted when a profile supplies ``model:`` but not ``provider:``.

The overlay CLEARS ``parsed.provider`` in that case rather than leaving a
persisted default in place: a settings ``defaultProvider`` merged into
``parsed.provider`` impersonates an explicit ``--provider`` and hijacks both the
``<provider>/<model>`` shorthand and the OpenRouter-env path (#98,
``cli/entry.py:1289-1294``). The caller re-feeds it through ``resolve_model``'s
lowest-precedence ``default_provider`` slot instead."""


@dataclass(frozen=True)
class ProfileApplication:
    """What :func:`apply_profile_to_args` actually did.

    ``skipped`` is the audit trail the user needs when a profile appears to be
    ignored: an explicit CLI flag beat it.
    """

    applied: tuple[str, ...]
    skipped: tuple[str, ...]
    notices: tuple[str, ...]


def parent_model_flags(parent_model: Model | None) -> list[str]:
    """The parent's run-scope model as child flags, or ``[]`` if it has none.

    Reads structurally (``getattr``) because the value arrives through the
    ``SubagentHost`` seam as ``Any``: it is ``ExtensionContext.model``, which a
    P4 host is free to supply differently, and a host that hands us something
    unreadable must cost the child its model inheritance, not its spawn.

    The provider half is emitted only alongside a usable id, and only when it is
    itself resolved. ``--model`` on its own is a supported invocation (the
    child's cascade infers the provider); ``--provider unknown`` is not.
    """

    model_id = getattr(parent_model, "id", None)
    if not model_id or model_id == _UNRESOLVED:
        return []
    flags = ["--model", model_id]
    provider = getattr(parent_model, "provider", None)
    if provider and provider != _UNRESOLVED:
        flags += ["--provider", provider]
    return flags


def child_model_flags(
    profile: AgentProfile, parent_model: Model | None = None
) -> list[str]:
    """The ``--model`` / ``--provider`` pair a child is launched with.

    The emission table's first row group, lifted into its own function so that
    :func:`child_model_id` can READ the decision instead of restating it. The
    precedence is unchanged and is the table's: a profile that names EITHER
    ``model`` or ``provider`` is expressing an intent about the child's identity
    and is taken at its word; a profile that names NEITHER inherits the parent's
    run-scope model (:func:`parent_model_flags`).
    """

    if profile.model is None and profile.provider is None:
        # The profile says nothing about the child's model, so the child would
        # run its own cascade and find whatever the PARENT's cascade would have
        # found WITHOUT the parent's run-scope flags — i.e. nothing, for a
        # parent whose model came from ``--model`` or ``/model``. Inherit.
        return parent_model_flags(parent_model)
    flags: list[str] = []
    if profile.model is not None:
        flags += ["--model", profile.model]
    if profile.provider is not None:
        flags += ["--provider", profile.provider]
    return flags


def child_model_id(
    profile: AgentProfile, parent_model: Model | None = None
) -> str | None:
    """The model id that will be ON THE CHILD'S ARGV, or ``None`` if none will.

    READ OFF THE FLAGS THEMSELVES rather than re-deriving the precedence, which
    is the whole reason :func:`child_model_flags` exists as a separate function.
    Its callers are display surfaces — the delegation statusline row, the widget
    panel, the spawn consent dialog — and a display that states a model the
    child was not asked to run is worse than one that states nothing: the
    consent dialog in particular is a claim a human acts on.

    ``None`` is the honest answer whenever the flag is absent: a profile that
    declares only a ``provider``, and a parent that has no resolved model of its
    own, both leave the child to its own model cascade, whose outcome is not
    knowable from here. Every caller omits the term rather than inventing one.

    THIS IS NOT WHAT THE CHILD REPORTS. The child's own ``message_end`` is the
    authoritative statement of what it actually ran, and it wins wherever both
    are available (``aelix_agents.runtime._publish``). This is what we ASKED
    for, which is the only thing knowable before the process exists.
    """

    flags = child_model_flags(profile, parent_model)
    return flags[flags.index("--model") + 1] if "--model" in flags else None


def profile_to_flags(
    profile: AgentProfile,
    *,
    prompt_path: str,
    parent_model: Model | None = None,
) -> list[str]:
    """Render the profile as CLI flags (the emission table, top to bottom).

    ``prompt_path`` is the file the system-prompt body will be read from. In P1
    this is always the profile's OWN ``file_path`` — the rendering is a dry run
    for ``/agents show``, nothing spawns, and there is no temp-prompt writer
    (deferred to P2 with the spawn path that needs it). Passing the profile
    itself is SOUND rather than a shortcut: ``cli/entry.py``'s prompt-file reader
    strips a leading frontmatter block, so the flag yields the same body the
    overlay uses. Without that the rendered command — which the panel shell-quotes
    for copy-paste — would inject the profile's raw YAML into the system prompt.
    """

    flags: list[str] = child_model_flags(profile, parent_model)

    if profile.tools is not None:
        if not profile.tools:
            # ``()`` means NO tools. ``--tools ''`` would mean the OPPOSITE:
            # ``parse_args`` yields ``[]``, which ``_resolve_active_tools``
            # (``entry.py:627-649``) reads as falsy → ``None`` → every tool active.
            flags.append("--no-tools")
        else:
            flags += ["--tools", ",".join(profile.tools)]

    if not profile.builtin_tools:
        flags.append("--no-builtin-tools")
    if not profile.inherit_skills:
        flags.append("--no-skills")
    for skill in profile.skills:
        flags += ["--skill", skill]
    if not profile.inherit_extensions:
        flags.append("--no-extensions")
    for extension in profile.extensions:
        flags += ["-e", extension]
    if not profile.context_files:
        flags.append("--no-context-files")
    if profile.thinking is not None:
        flags += ["--thinking", profile.thinking]

    if profile.system_prompt == "replace":
        flags += ["--system-prompt-file", prompt_path]
    else:
        flags += ["--append-system-prompt-file", prompt_path]

    return flags


def profile_to_argv(
    profile: AgentProfile,
    *,
    prompt_path: str,
    oneshot: bool,
    task: str | None = None,
    parent_model: Model | None = None,
) -> list[str]:
    """Full argv for a profile-driven aelix invocation.

    ``oneshot`` → the JSON one-shot channel (``--mode json -p --no-session``);
    otherwise the long-lived headless channel (``--mode rpc``).

    P1 uses this ONLY for ``/agents show`` dry-run rendering — nothing spawns
    (that is P2). It exists in P1 so the rendered command is the same command
    P2 will run, which is the whole point of an auditable dry run.
    """

    prefix = (
        ["--mode", "json", "-p", "--no-session"] if oneshot else ["--mode", "rpc"]
    )
    argv = [
        *prefix,
        *profile_to_flags(
            profile, prompt_path=prompt_path, parent_model=parent_model
        ),
    ]
    if oneshot and task:
        argv.append(f"Task: {task}")
    return argv


def apply_profile_to_args(
    parsed: Args,
    profile: AgentProfile,
    *,
    provided: AbstractSet[str] | None = None,
) -> ProfileApplication:
    """Overlay the profile onto ``parsed`` IN PLACE, losing to explicit flags.

    ``provided`` is ``Args.provided`` — the set of field names the user set on
    the command line. Real provenance is REQUIRED: every other field is a plain
    default (``--tools ''`` yields ``[]``, indistinguishable from "not
    supplied"), so "an explicit CLI flag always wins over a profile" is
    otherwise unenforceable. :data:`None` means "treat nothing as explicit" —
    callers that have an ``Args`` pass ``provided=parsed.provided``.

    ONE field is exempt: ``append_system_prompt`` is an accumulator, not a slot,
    and the profile body always joins it (see the branch's comment).

    Mutates in place because the harness factory closes over this exact object
    (``cli/entry.py:2592-2596``); rebinding a fresh ``Args`` would not reach it.

    Raises :class:`ProfileError` when the profile would silently WIDEN a kill
    switch the user set explicitly (``--no-extensions`` vs ``extensions:``).
    Validation happens BEFORE any mutation so a rejected profile leaves
    ``parsed`` untouched.
    """

    explicit: frozenset[str] = frozenset(provided or ())

    # Fail before mutating. ``--no-extensions`` is a kill switch; a profile that
    # names extensions must never re-open it behind the user's back.
    if profile.extensions and "no_extensions" in explicit:
        raise ProfileError(
            "--no-extensions conflicts with the profile's extensions: "
            f"({', '.join(profile.extensions)}); drop one of the two."
        )

    applied: list[str] = []
    skipped: list[str] = []
    notices: list[str] = []

    def _fill(field_name: str) -> bool:
        """Claim ``field_name`` for the profile unless the CLI already set it."""

        if field_name in explicit:
            skipped.append(field_name)
            return False
        applied.append(field_name)
        return True

    if profile.model is not None and _fill("model"):
        parsed.model = profile.model
    if profile.provider is not None and _fill("provider"):
        parsed.provider = profile.provider

    if profile.tools is not None:
        if not profile.tools:
            if _fill("no_tools"):
                parsed.no_tools = True
        elif _fill("tools"):
            parsed.tools = list(profile.tools)

    if not profile.builtin_tools and _fill("no_builtin_tools"):
        parsed.no_builtin_tools = True
    if not profile.inherit_skills and _fill("no_skills"):
        parsed.no_skills = True
    if profile.skills and _fill("skills"):
        # ``+=`` not ``=``: explicit ``--skill`` paths and the profile's are
        # both user intent, and ``_resolve_skill_dirs`` scans the union.
        parsed.skills += list(profile.skills)
    if not profile.inherit_extensions and _fill("no_extensions"):
        parsed.no_extensions = True
    if profile.extensions and _fill("extensions"):
        parsed.extensions += list(profile.extensions)
    if not profile.context_files and _fill("no_context_files"):
        parsed.no_context_files = True
    if profile.thinking is not None and _fill("thinking"):
        parsed.thinking = profile.thinking

    if profile.system_prompt == "replace":
        # SCALAR: ``--system-prompt`` and the profile body compete for one slot,
        # so the explicit-CLI-wins rule applies unchanged.
        if _fill("system_prompt"):
            parsed.system_prompt = profile.body
    else:
        # ACCUMULATOR, therefore NOT gated on ``provided`` — deliberately, and
        # this is the one row of the emission table where the ``_fill`` rule
        # does not apply. ``--append-system-prompt`` does not compete with the
        # profile body for a slot; it adds a chunk beside it. Gating here would
        # not mean "the user's value wins", it would mean "the profile's
        # IDENTITY is silently discarded" while stderr still reports
        # ``Agent profile: <name>`` — the precise failure ``--agent`` is
        # fatal-on-error to prevent (ADR-0196).
        #
        # It is also what keeps the two channels in agreement: the flag channel
        # emits ``--append-system-prompt-file`` (a SEPARATE accumulator) and
        # ``_apply_prompt_files`` (``cli/entry.py``) extends
        # ``append_system_prompt`` with it unconditionally, consulting
        # ``provided`` only for the scalar ``system_prompt``. A gate here would
        # split the channels apart for every non-empty ``provided``.
        #
        # FIRST among the appends: the identity precedes the user's own
        # ``--append-system-prompt`` chunks (the context files stay ahead of
        # both — ``_resolve_append_chunks`` prepends them at build time).
        applied.append("append_system_prompt")
        parsed.append_system_prompt.insert(0, profile.body)

    # #98 split-pair guard. A profile that names a model but not a provider must
    # not inherit a persisted ``defaultProvider`` as if it were ``--provider``.
    if (
        profile.model is not None
        and profile.provider is None
        and "provider" not in explicit
    ):
        parsed.provider = None
        notices.append(_RECOMPUTE_DEFAULT_PROVIDER)

    return ProfileApplication(
        applied=tuple(applied), skipped=tuple(skipped), notices=tuple(notices)
    )


__all__ = [
    "PROFILE_OVERLAY_FIELDS",
    "ProfileApplication",
    "apply_profile_to_args",
    "child_model_flags",
    "child_model_id",
    "parent_model_flags",
    "profile_to_argv",
    "profile_to_flags",
]
