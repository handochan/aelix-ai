"""Built-in GuardrailExtension — hardcoded danger patterns on ``tool_call``.

Per ADR-0004, guardrails are a built-in *extension* — not core. The default
rule set covers the obvious "do not delete the repo" cases inherited from
pi-coding-agent's bash/write guardrails:

- ``rm -rf`` / ``rm -fr`` / ``/bin/rm -rf`` in a ``bash`` or ``shell`` command,
- ``sudo rm -r`` variants,
- the classic fork-bomb pattern (``:(){:|:&};:`` plus a few equivalents),
- writes to ``.env`` / ``.env.*`` files,
- writes inside ``.git/``, ``node_modules/``, ``__pycache__/``.

Rules are simple predicates; users can disable defaults via
``disabled_default_rules`` and append project-specific patterns via
``additional_patterns``. Strengthened regexes per D.1.13 M-7.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult

from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext


@dataclass(frozen=True)
class GuardrailRule:
    """A single deny-pattern.

    ``applies_to_tools=None`` means "any tool". ``predicate`` returns the
    reason string when the call must be blocked, ``None`` otherwise.
    """

    name: str
    applies_to_tools: frozenset[str] | None
    predicate: Callable[[ToolCallHookEvent], str | None]
    description: str = ""


# === Default predicates ===


_BASH_TOOLS = frozenset({"bash", "shell", "sh", "execute_command"})
_WRITE_TOOLS = frozenset({"write", "edit", "create_file", "write_file"})


# rm -rf / rm -fr / rm -r -f with optional path prefix (e.g. /bin/rm).
_RM_RF_PATTERN = re.compile(
    r"(?:^|[\s;&|`(])(?:[\w./]*/)?(?:sudo\s+)?rm\s+"
    r"(?:-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|"
    r"-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*|"
    r"-[rR]\s+-[fF]|-[fF]\s+-[rR])"
)
# sudo rm -r anything (covers -r alone without -f).
_SUDO_RM_R_PATTERN = re.compile(
    r"(?:^|[\s;&|`(])sudo\s+(?:[\w./]*/)?rm\s+-[a-zA-Z]*[rR]"
)
# Fork-bomb. Both the iconic shell ":(){...};:" and a few aliases.
_FORK_BOMB_PATTERN = re.compile(
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"
)


def _command_from_event(event: ToolCallHookEvent) -> str:
    """Best-effort extraction of the command string from the args dict."""

    for key in ("command", "cmd", "shell_command", "script"):
        value = event.args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _path_from_event(event: ToolCallHookEvent) -> str:
    # ``file_path`` is the edit-family arg name (ADR-0120 W4 LOW): without it the
    # guardrail's dotenv/.git/node_modules checks read an empty path for `edit`
    # calls and silently never fire. Keep in sync with permission._path_from_args.
    for key in ("path", "file_path", "file", "filename", "filepath", "target"):
        value = event.args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _bash_rm_rf(event: ToolCallHookEvent) -> str | None:
    command = _command_from_event(event)
    if not command:
        return None
    if _RM_RF_PATTERN.search(command):
        return (
            "[guardrail] refusing 'rm -rf' style command: "
            f"{command.strip()[:80]}"
        )
    return None


def _bash_sudo_rm_r(event: ToolCallHookEvent) -> str | None:
    command = _command_from_event(event)
    if not command:
        return None
    if _SUDO_RM_R_PATTERN.search(command):
        return (
            "[guardrail] refusing 'sudo rm -r' command: "
            f"{command.strip()[:80]}"
        )
    return None


def _bash_fork_bomb(event: ToolCallHookEvent) -> str | None:
    command = _command_from_event(event)
    if not command:
        return None
    if _FORK_BOMB_PATTERN.search(command):
        return "[guardrail] refusing fork-bomb pattern in shell command."
    return None


def _guard_key(component: str) -> str:
    """Fold one path component to the form the sensitive-name sets are keyed in.

    Comparing a raw component against those sets is not enough, because the
    filesystems Aelix supports resolve several distinct spellings to the same
    file. Measured against the unfolded check, every one of these was
    auto-allowed while its canonical spelling was correctly refused::

        .ENV    ".env " (trailing space)    .env::$DATA
        Id_Rsa    .SSH/AUTHORIZED_KEYS    .AELIX/agents/e.md

    Three foldings, in the order the OS applies them:

    * ``name:stream`` — an NTFS alternate data stream. ``.env::$DATA`` opens
      the DEFAULT stream of ``.env``, i.e. the file itself.
    * trailing spaces and dots — Win32 strips them while resolving a name, so
      ``".env "`` and ``".env."`` both reach ``.env``.
    * case — NTFS and macOS's default APFS are case-insensitive, so ``.ENV``
      and ``.env`` are one file.

    THE CASE HALF IS NOT A WINDOWS CONCERN. It reproduces on Linux (the guard
    is string comparison, so it folds open whatever the host does) and it is
    live on macOS today, which ``install.sh`` explicitly targets. Issue #108
    files it under Windows; that triage parks a shipped-platform bypass behind
    an unshipped-platform roadmap.

    A casefold-only fix would be the worst outcome available: it closes the
    case family, looks fixed, and silently leaves the trailing-shape and stream
    families open. All three fold here, or none do.

    Deliberately fail-closed on POSIX, where a colon is a legal filename
    character: a file genuinely named ``.env:notes`` folds to ``.env`` and gets
    a prompt it does not strictly need. Extra prompting is the safe direction;
    a missed sensitive name is not.

    The sensitive-name sets this feeds MUST stay lowercase — see
    :data:`~aelix_coding_agent.builtin.permission._SENSITIVE_BASENAMES`.
    """

    key = component.split(":", 1)[0]
    stripped = key.rstrip(" .")
    # "." and ".." fold to the empty string. ``normpath`` removes them before
    # this is reached, but a caller that skips it must not get a key that
    # silently matches nothing.
    if stripped:
        key = stripped
    return key.casefold()


def _write_dotenv(event: ToolCallHookEvent) -> str | None:
    path = _path_from_event(event)
    if not path:
        return None
    # Normalise the separator first, exactly like every sibling rule below.
    # Without it ``C:\proj\.env`` holds no "/" to split on, so the whole path
    # became the "basename" and no dotenv write spelled that way was refused.
    basename = _guard_key(path.replace("\\", "/").rsplit("/", 1)[-1])
    if basename == ".env" or basename.startswith(".env."):
        return f"[guardrail] refusing write to dotenv file: {path}"
    return None


def _write_into_git(event: ToolCallHookEvent) -> str | None:
    path = _path_from_event(event)
    if not path:
        return None
    parts = path.replace("\\", "/").split("/")
    if ".git" in parts:
        return f"[guardrail] refusing write inside .git/: {path}"
    return None


def _write_into_node_modules(event: ToolCallHookEvent) -> str | None:
    path = _path_from_event(event)
    if not path:
        return None
    parts = path.replace("\\", "/").split("/")
    if "node_modules" in parts:
        return f"[guardrail] refusing write inside node_modules/: {path}"
    return None


def _write_into_pycache(event: ToolCallHookEvent) -> str | None:
    path = _path_from_event(event)
    if not path:
        return None
    parts = path.replace("\\", "/").split("/")
    if "__pycache__" in parts:
        return f"[guardrail] refusing write inside __pycache__/: {path}"
    return None


# Guardrail patterns are conservative — they may produce false positives on
# commands that contain dangerous substrings inside quoted args or comments.
# This is intentional. Apps requiring fine-grained control should disable the
# default rule and provide their own predicate.
DEFAULT_GUARDRAIL_RULES: tuple[GuardrailRule, ...] = (
    GuardrailRule(
        name="bash.rm_rf",
        applies_to_tools=_BASH_TOOLS,
        predicate=_bash_rm_rf,
        description="Refuse 'rm -rf' (and -fr, /bin/rm, sudo prefix) variants.",
    ),
    GuardrailRule(
        name="bash.sudo_rm_r",
        applies_to_tools=_BASH_TOOLS,
        predicate=_bash_sudo_rm_r,
        description="Refuse 'sudo rm -r' even without -f.",
    ),
    GuardrailRule(
        name="bash.fork_bomb",
        applies_to_tools=_BASH_TOOLS,
        predicate=_bash_fork_bomb,
        description="Refuse the classic ':(){:|:&};:' fork-bomb pattern.",
    ),
    GuardrailRule(
        name="write.dotenv",
        applies_to_tools=_WRITE_TOOLS,
        predicate=_write_dotenv,
        description="Refuse writes to .env / .env.* files.",
    ),
    GuardrailRule(
        name="write.git_dir",
        applies_to_tools=_WRITE_TOOLS,
        predicate=_write_into_git,
        description="Refuse writes inside any .git/ directory.",
    ),
    GuardrailRule(
        name="write.node_modules",
        applies_to_tools=_WRITE_TOOLS,
        predicate=_write_into_node_modules,
        description="Refuse writes inside any node_modules/ directory.",
    ),
    GuardrailRule(
        name="write.pycache",
        applies_to_tools=_WRITE_TOOLS,
        predicate=_write_into_pycache,
        description="Refuse writes inside any __pycache__/ directory.",
    ),
)


@dataclass
class GuardrailExtension:
    """Default-on safety rules registered as a built-in extension.

    ``additional_patterns`` is appended after the default rule set, so a
    custom rule may further restrict but cannot relax the defaults (use
    ``disabled_default_rules`` for that).
    """

    disabled_default_rules: frozenset[str] = field(default_factory=frozenset)
    additional_patterns: tuple[GuardrailRule, ...] = ()

    def __call__(self, aelix: ExtensionAPI) -> None:
        aelix.on("tool_call", self._on_tool_call)

    def _active_rules(self) -> tuple[GuardrailRule, ...]:
        defaults = tuple(
            rule
            for rule in DEFAULT_GUARDRAIL_RULES
            if rule.name not in self.disabled_default_rules
        )
        return defaults + tuple(self.additional_patterns)

    def _on_tool_call(
        self,
        event: ToolCallHookEvent,
        _ctx: ExtensionContext,
    ) -> ToolCallResult | None:
        for rule in self._active_rules():
            if (
                rule.applies_to_tools is not None
                and event.tool_name not in rule.applies_to_tools
            ):
                continue
            reason = rule.predicate(event)
            if reason:
                return ToolCallResult(block=True, reason=reason)
        return None


__all__ = [
    "DEFAULT_GUARDRAIL_RULES",
    "GuardrailExtension",
    "GuardrailRule",
]
