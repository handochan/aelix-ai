"""Background jobs: a generic product-core facility its clients register kinds with (#722, ADR-0293).

Inert until #611 S3: see :mod:`aelix_coding_agent.jobs.activation`. The kinds
are clients — delegation, background ``bash``, an extension's work — and
nothing in this package names one.
"""

from __future__ import annotations

from aelix_coding_agent.jobs.activation import JobsInactive, is_active
from aelix_coding_agent.jobs.contract import (
    ClientOutcome,
    HelperCleanup,
    JobContext,
    JobKindAdapter,
    JobRunner,
    JobSnapshot,
    KindCapabilities,
    NoticeBody,
    OutputSlice,
    Recovered,
    RelationSpec,
)

__all__ = [
    "ClientOutcome",
    "HelperCleanup",
    "JobContext",
    "JobKindAdapter",
    "JobRunner",
    "JobSnapshot",
    "JobsInactive",
    "KindCapabilities",
    "NoticeBody",
    "OutputSlice",
    "Recovered",
    "RelationSpec",
    "is_active",
]
