"""Spawn children running, as a host without ``NtResumeProcess`` does (#777 fix round 2).

Background ``bash`` (#611 S2d), the print channel (#777) and ``RpcClient.start``
(#772) create their child ``CREATE_SUSPENDED`` on win32, and only the real
``ProcessTree.attach(suspended=True)`` resumes it. A test that replaces
``attach`` with a stub, or hands the real one an injected api that does not
resume, leaves a real child suspended for ever there: windows-latest run
44012650633 hung at 8% on all four legs, in
``test_a_successful_delegation_closes_its_tree_and_signals_nothing_here``.
:func:`unsuspended` pins ``ProcessTree.suspend_flags`` to 0, the shape a host
without ``NtResumeProcess`` takes; off win32 it is 0 already and nothing changes.
"""

from __future__ import annotations

import pytest
from aelix_ai.utils._process_tree import ProcessTree


def unsuspended(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every spawn in this test is created running: ``ProcessTree.suspend_flags`` answers 0."""

    monkeypatch.setattr(ProcessTree, "suspend_flags", staticmethod(lambda **_kwargs: 0))
