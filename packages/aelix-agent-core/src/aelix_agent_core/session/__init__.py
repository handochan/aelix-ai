"""Aelix session subsystem (Sprint 4a / Phase 2.2.1 — ADR-0022).

Pi parity: Session class wraps a SessionStorage Protocol. ``JsonlSessionRepo``
manages on-disk JSONL session files (version 3) compatible with Pi
``packages/agent/src/harness/session/`` at SHA ``734e08e``.
"""

from aelix_agent_core.session.context import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    build_session_context,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
)
from aelix_agent_core.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomEntry,
    CustomMessageEntry,
    LabelEntry,
    LeafEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionInfoEntry,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
    entry_from_json,
    entry_to_json,
)
from aelix_agent_core.session.fs import (
    FileInfo,
    FileKind,
    FileSystem,
    LocalFileSystem,
)
from aelix_agent_core.session.jsonl_repo import (
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import (
    JsonlSessionStorage,
    load_jsonl_session_metadata,
)
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session, SessionContext
from aelix_agent_core.session.storage import (
    JsonlSessionMetadata,
    SessionError,
    SessionErrorCode,
    SessionMetadata,
    SessionStorage,
)

__all__ = [
    "BRANCH_SUMMARY_PREFIX",
    "BRANCH_SUMMARY_SUFFIX",
    "BranchSummaryEntry",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "CompactionEntry",
    "CustomEntry",
    "CustomMessageEntry",
    "FileInfo",
    "FileKind",
    "FileSystem",
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionMetadata",
    "JsonlSessionRepo",
    "JsonlSessionStorage",
    "LabelEntry",
    "LeafEntry",
    "LocalFileSystem",
    "MemorySessionStorage",
    "MessageEntry",
    "ModelChangeEntry",
    "Session",
    "SessionContext",
    "SessionError",
    "SessionErrorCode",
    "SessionInfoEntry",
    "SessionMetadata",
    "SessionStorage",
    "SessionTreeEntry",
    "ThinkingLevelChangeEntry",
    "build_session_context",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
    "entry_from_json",
    "entry_to_json",
    "load_jsonl_session_metadata",
]
