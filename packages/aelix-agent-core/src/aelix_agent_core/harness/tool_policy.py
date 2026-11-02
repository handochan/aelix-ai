"""What a run's tools may be: the one policy every registry and active-set writer goes through.

#510, ADR-0283. The harness holds one :class:`ToolPolicy` and commits every
change to its tool registry or its active set through it
(``AgentHarness._commit_tools``), so a writer cannot forget a rule. Three
things are kept apart:

* **excluded** (``--exclude-tools``) — never enters the registry. The model is
  not shown it, nothing can call it, and no registration (an extension's
  ``register_tool``, the ``agent`` tool's re-registration, an MCP attach, a
  ``/reload``) brings it back. pi filters its registry the same way on every
  ``_refreshToolRegistry`` (``agent-session.ts`` ``_isAllowedTool``).
* **locked** (``--no-tools``, ``--no-builtin-tools``, ``--tools`` and an agent
  profile's ``tools:`` / ``builtin_tools: false``) — registered, so the panel
  lists it and ``--tools`` can name any loaded tool, but its value is fixed for
  the run. A locked-off tool is never active, whoever asks.
* **saved off** (the ``defaultTools`` setting) — registered and inactive. Only a
  new policy (the ``/tool`` panel's apply, ``/reload``) turns it back on; an
  extension's ``set_active_tools`` cannot (owner decision 2, 2026-10-31).

Precedence: excluded > ``--no-tools`` > ``--no-builtin-tools`` > ``--tools`` /
profile > saved > on.

Nothing here knows which tools are built in: the product layer passes the
names (:attr:`ToolPolicy.builtin`), so the kernel stays free of the tool set.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

__all__ = [
    "ToolLock",
    "ToolPolicy",
    "carriable_pattern",
    "tool_name_matcher",
    "tool_pattern_matches",
]

_CARRIABLE = re.compile(r"[^\s,\x00-\x1f\x7f-][^\s,\x00-\x1f\x7f]{0,255}")


def carriable_pattern(value: object) -> bool:
    """Whether *value* can travel as ONE ``--exclude-tools`` entry to a delegated child.

    No whitespace, comma or control character (the flag splits on commas and
    strips), not starting with ``-`` (a forged record entry must not read as
    a flag), at most 256 characters (#510 critique B2). The delegation
    record's reader and the parent's forwarding of its saved-off names use this
    one rule.
    """

    return isinstance(value, str) and _CARRIABLE.fullmatch(value) is not None


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """pi ``toolPatternRegExp`` (``core/mcp-servers.ts``): ``*`` is any run of characters.

    Every other character is literal — ``.``, ``+``, ``?``, brackets — so
    ``github__*`` matches every tool of the ``github`` server and ``a.b`` only
    ``a.b``.
    """

    return re.compile("^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$", re.S)


def tool_pattern_matches(pattern: str, name: str) -> bool:
    """Whether one ``--exclude-tools`` entry names *name*: equal, or a ``*`` pattern that matches."""

    if "*" not in pattern:
        return pattern == name
    return _pattern_regex(pattern).match(name) is not None


def tool_name_matcher(patterns: Iterable[str]) -> Callable[[str], bool]:
    """pi ``createToolNameMatcher``: one predicate over exact names and ``*`` patterns."""

    entries = [p for p in patterns if p]
    names = {p for p in entries if "*" not in p}
    compiled = [_pattern_regex(p) for p in entries if "*" in p]

    def _match(name: str) -> bool:
        return name in names or any(c.match(name) is not None for c in compiled)

    return _match


def _source(tag: str) -> str:
    """A lock's tag as the subject of a sentence: the flag, or ``the agent profile``."""

    return "the agent profile" if tag == "profile" else tag


@dataclass(frozen=True)
class ToolLock:
    """A value the run keeps whatever a setting or an extension asks, and why."""

    value: bool
    reason: str
    tag: str
    """The word a panel's status column shows: the flag that set it, or ``profile``."""


@dataclass(frozen=True)
class ToolPolicy:
    """The run's rules for its tools. The default allows everything (an SDK harness)."""

    exclude: tuple[str, ...] = ()
    """``--exclude-tools`` entries, as typed: names and ``*`` patterns."""
    no_tools: bool = False
    no_tools_tag: str = "--no-tools"
    no_builtin_tools: bool = False
    no_builtin_tag: str = "--no-builtin-tools"
    allow: frozenset[str] | None = None
    """``--tools`` (or a profile's ``tools:``): only these may be on. ``None`` = no allowlist."""
    allow_tag: str = "--tools"
    builtin: frozenset[str] = frozenset()
    """The built-in tool names, for ``--no-builtin-tools``."""
    saved_off: frozenset[str] = frozenset()
    """Names the ``defaultTools`` setting turns off, as last applied."""
    _match: Callable[[str], bool] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_match", tool_name_matcher(self.exclude))

    def excluded(self, name: str) -> str | None:
        """Why *name* is kept out of the registry, or ``None``."""

        if not self.exclude or not self._match(name):
            return None
        hit = next((p for p in self.exclude if tool_pattern_matches(p, name)), name)
        return f"--exclude-tools {hit}"

    def lock(self, name: str) -> ToolLock | None:
        """The run lock on *name*, if a flag or a profile fixed it (exclusion aside)."""

        if self.no_tools:
            tag = self.no_tools_tag
            return ToolLock(False, f"{_source(tag)} turns every tool off", tag)
        if self.no_builtin_tools and name in self.builtin:
            tag = self.no_builtin_tag
            return ToolLock(False, f"{_source(tag)} turns the built-in tools off", tag)
        if self.allow is not None:
            tag = self.allow_tag
            if name in self.allow:
                return ToolLock(True, f"named by {_source(tag)}", tag)
            return ToolLock(False, f"not named by {_source(tag)}", tag)
        return None

    def allows(self, name: str) -> bool:
        """Whether *name* may be active (it is registered: exclusion is checked apart)."""

        lock = self.lock(name)
        if lock is not None:
            return lock.value
        return name not in self.saved_off

    def run_refusal(self, name: str) -> str | None:
        """Why *name* cannot be on at all this run — excluded or locked off — or ``None``.

        What RPC ``tools/set_active`` refuses (owner decision 5): a saved-off
        name is not here, because a client may turn it on, as the panel can.
        """

        reason = self.excluded(name)
        if reason is not None:
            return f"{name} is removed for this run by {reason}"
        lock = self.lock(name)
        if lock is not None and not lock.value:
            return f"{name} is locked off for this run: {lock.reason}"
        return None

    def refusal(self, name: str) -> str | None:
        """Why *name* cannot be turned on now, in a sentence, or ``None`` when it can."""

        reason = self.run_refusal(name)
        if reason is not None:
            return reason
        if self.lock(name) is None and name in self.saved_off:
            return f"{name} is turned off in settings (defaultTools); /tool turns it on"
        return None

    def saved_off_in_force(self) -> frozenset[str]:
        """The saved-off names that are off: those no run lock turns back on.

        #510 (owner decision B): what a delegated child is started without, as
        ``--exclude-tools``, beside the run's own exclusions.
        """

        return frozenset(n for n in self.saved_off if not self.allows(n))

    def restricts(self, names: Iterable[str]) -> bool:
        """Whether any of *names* may not be active — i.e. "every tool on" is not true."""

        return any(not self.allows(n) for n in names)
