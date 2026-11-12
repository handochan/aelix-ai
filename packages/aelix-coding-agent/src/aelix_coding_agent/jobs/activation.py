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

**The off switch (#778).** With the gate on, the user can still turn background
jobs off for a launch with ``backgroundJobs.enabled: false`` in the GLOBAL
settings. The CLI reads it once, through :func:`enabled_at_start`, as soon as its
settings exist and before any extension loads; false makes :func:`is_active`
answer false from then on in this process, so the service is not built and
every reader of the gate agrees. A delegated child reads the same global file at its
own start. A change takes effect at the next launch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aelix_coding_agent.jobs.service import JobService

_log = logging.getLogger(__name__)

#: Off until #611 S3. Read through :func:`is_active`, never copied.
ACTIVE = False

_service: JobService | None = None

#: ``backgroundJobs.enabled: false`` read at start (:func:`enabled_at_start`, #778).
_switched_off = False


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
    """Whether background jobs are on, read now: the gate, and not switched off at start (#778)."""

    return bool(ACTIVE) and not _switched_off


def gate_on() -> bool:
    """Whether this build has background jobs at all (``ACTIVE``), whatever the setting says.

    The ``/settings`` row reads this, so a user who switched jobs off can switch
    them on again, and so that while the gate is off there is no row at all.
    """

    return bool(ACTIVE)


def enabled_at_start(settings_manager: Any, *, warn: Callable[[str], None] | None = None) -> bool:
    """Read ``backgroundJobs.enabled`` once, at start: whether background jobs are on (#778).

    The CLI calls it as soon as its settings exist, before any extension loads,
    and ``aelix status`` before its own load, so that no extension's ``setup()``
    reads the gate before the switch; the CLI's service condition then reads
    :func:`is_active`. While the gate is off it reads nothing and answers false,
    as before. With it on, it records the GLOBAL setting
    (``SettingsManager.get_background_jobs_enabled``: a repository's file cannot
    change it), so that :func:`is_active` gives every later reader the same
    answer. A ``backgroundJobs`` that is not an object, or an ``enabled`` that is
    not a bool, counts as absent — on — with one warning.
    """

    global _switched_off
    if not ACTIVE:
        return False
    say = warn or _log.warning
    whole = settings_manager.get_global_settings().background_jobs
    if whole is not None and not isinstance(whole, dict):
        say("settings backgroundJobs must be an object; using its defaults")
    raw = settings_manager.get_background_jobs_settings().get("enabled", True)
    if not isinstance(raw, bool):
        say("settings backgroundJobs.enabled must be true or false; using true")
    _switched_off = not settings_manager.get_background_jobs_enabled()
    return not _switched_off


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
    "enabled_at_start",
    "gate_on",
    "install_service",
    "is_active",
    "require_active",
]
