"""The one activation policy for background jobs (#722, ADR-0293 §activation).

``ACTIVE`` is ``False`` on main until #611 S3 flips it. While it is off nothing
in :mod:`aelix_coding_agent.jobs` is reachable from the product: the CLI builds
no service, registers no ``job`` tool and no ``/jobs`` command, draws no footer
segment, runs no liveness loop and no reconciliation, the print drain and the
TUI waker are not wired, ``ExtensionAPI.jobs`` and ``post_notice`` are absent
(a plain ``AttributeError``, as before #722), the kernel's notice inbox is off
(``AgentHarnessOptions.notice_inbox``), and the ``backgroundJobs`` settings are
never read.

**The test seam.** Tests turn it on by setting the module attribute
(``monkeypatch.setattr(activation, "ACTIVE", True)``; the ``jobs_active``
fixture in ``tests/jobs/conftest.py`` does exactly that). Every check reads the
attribute at call time through :func:`is_active`, so the seam needs nothing
else. A scratch live run turns it on the same way, from a launcher script that
sets the attribute before it calls the CLI's ``main_sync``: there is no
environment variable, flag or setting that does it, so nothing a user or a
repository can write reaches the feature before S3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

#: Off until #611 S3. Read through :func:`is_active`, never copied.
ACTIVE = False

_service: JobService | None = None


class JobsInactive(AttributeError):
    """Background jobs are not available in this build (``ACTIVE`` is off).

    An :class:`AttributeError`, so that with the feature off a lookup through
    the service reads as absent, as ``ExtensionAPI.jobs`` and ``post_notice``
    do (they raise a plain ``AttributeError`` with the interpreter's wording,
    verify S1).
    """

    def __init__(self, message: str | None = None, *, name: str | None = None, obj: object = None) -> None:
        super().__init__(message or "background jobs are not available in this build")
        self.name = name
        self.obj = obj


def is_active() -> bool:
    """Whether background jobs are on, read now."""

    return bool(ACTIVE)


def require_active() -> None:
    if not is_active():
        raise JobsInactive


def install_service(service: JobService | None) -> None:
    """The process's one service, or ``None`` to remove it (the CLI, tests)."""

    global _service
    _service = service


def current_service() -> JobService:
    """The process's service; :class:`JobsInactive` when off or not built."""

    require_active()
    if _service is None:
        raise JobsInactive
    return _service


__all__ = [
    "ACTIVE",
    "JobsInactive",
    "current_service",
    "install_service",
    "is_active",
    "require_active",
]
