"""Aelix session subsystem (Sprint 4a / Phase 2.2.1 — ADR-0022).

Pi parity: Session class wraps a SessionStorage Protocol. ``JsonlSessionRepo``
manages on-disk JSONL session files (version 3) compatible with Pi
``packages/agent/src/harness/session/`` at SHA ``734e08e``.
"""

from aelix_agent_core.session.branch_summarization import (
    BranchSummarizerOverride,
    BranchSummaryPreparation,
    SummaryEntry,
    collect_entries_for_branch_summary,
    generate_branch_summary,
)
from aelix_agent_core.session.compaction import (
    CompactionPreparation,
    CompactResult,
    SummarizerOverride,
    compact,
    prepare_compaction,
)
from aelix_agent_core.session.context import (
    BRANCH_SUMMARY_PREFIX,
    BRANCH_SUMMARY_SUFFIX,
    COMPACTION_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_SUFFIX,
    CustomMessage,
    build_display_messages,
    build_session_context,
    create_branch_summary_message,
    create_compaction_summary_message,
    create_custom_message,
    create_display_custom_message,
    select_display_entries,
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
    ForkOptions,
    JsonlSessionCreateOptions,
    JsonlSessionListOptions,
    JsonlSessionRepo,
)
from aelix_agent_core.session.jsonl_storage import (
    JsonlSessionStorage,
    load_jsonl_session_metadata,
)
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.repo_utils import (
    ForkPosition,
    get_entries_to_fork,
)
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
    "BranchSummarizerOverride",
    "BranchSummaryEntry",
    "BranchSummaryPreparation",
    "COMPACTION_SUMMARY_PREFIX",
    "COMPACTION_SUMMARY_SUFFIX",
    "CompactResult",
    "CompactionEntry",
    "CompactionPreparation",
    "CustomEntry",
    "CustomMessage",
    "CustomMessageEntry",
    "FileInfo",
    "FileKind",
    "FileSystem",
    "ForkOptions",
    "ForkPosition",
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
    "SummarizerOverride",
    "SummaryEntry",
    "ThinkingLevelChangeEntry",
    "build_display_messages",
    "build_session_context",
    "collect_entries_for_branch_summary",
    "compact",
    "create_branch_summary_message",
    "create_compaction_summary_message",
    "create_custom_message",
    "create_display_custom_message",
    "entry_from_json",
    "entry_to_json",
    "generate_branch_summary",
    "get_entries_to_fork",
    "load_jsonl_session_metadata",
    "prepare_compaction",
    "select_display_entries",
]
