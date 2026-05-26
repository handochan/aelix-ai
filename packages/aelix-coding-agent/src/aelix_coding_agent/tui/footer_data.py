"""Sprint 6h₁₀b (ADR-0104) — footer data provider implementation.

:class:`AelixFooterData` implements the :class:`ReadonlyFooterDataProvider`
Protocol. It reads the git branch from ``.git/HEAD`` (plain worktrees and
detached-HEAD both supported), holds an extension-status store, and manages
a branch-change callback list.

No filesystem watching is performed this sprint; the host calls
:meth:`notify_branch_change` when it detects a branch transition.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from pathlib import Path

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)


class AelixFooterData:
    """Concrete :class:`ReadonlyFooterDataProvider` for the Aelix TUI.

    Parameters
    ----------
    cwd:
        The working directory whose ``.git/HEAD`` is inspected.  May be any
        path string; ``Path(cwd)`` is resolved lazily on each call so tests
        can point it at a ``tmp_path`` fixture directory.
    """

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._statuses: dict[str, str] = {}
        self._callbacks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # ReadonlyFooterDataProvider
    # ------------------------------------------------------------------

    def get_git_branch(self) -> str | None:
        """Return the current git branch name (or short sha for detached HEAD).

        Reads ``<cwd>/.git/HEAD``.  Returns ``None`` on any error (missing
        file, unreadable, ``.git`` is a plain file without a HEAD sibling,
        unexpected content).  Never raises.
        """
        try:
            git = Path(self._cwd) / ".git"
            if not git.exists():
                return None
            # worktree: .git is a file containing "gitdir: <path>"
            if git.is_file():
                return None
            head_path = git / "HEAD"
            if not head_path.is_file():
                return None
            raw = head_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

        prefix = "ref: refs/heads/"
        if raw.startswith(prefix):
            return raw[len(prefix):]

        if _HEX40_RE.match(raw):
            return raw[:7]

        return None

    def get_extension_statuses(self) -> dict[str, str]:
        """Return a shallow copy of the current extension-status map."""
        return dict(self._statuses)

    def on_branch_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register *callback* for branch-change notifications.

        Returns an unsubscribe callable; calling it removes the callback.
        Idempotent: calling the unsubscribe a second time is a no-op.
        """
        self._callbacks.append(callback)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._callbacks.remove(callback)

        return _unsubscribe

    # ------------------------------------------------------------------
    # Mutable host API (not in the read-only Protocol)
    # ------------------------------------------------------------------

    def set_status(self, key: str, text: str | None) -> None:
        """Add or update *key* in the status store; ``None`` removes it."""
        if text is None:
            self._statuses.pop(key, None)
        else:
            self._statuses[key] = text

    def notify_branch_change(self) -> None:
        """Invoke all registered branch-change callbacks."""
        for cb in list(self._callbacks):
            cb()


__all__ = ["AelixFooterData"]
