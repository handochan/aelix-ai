"""Offline access to the user guides bundled inside the wheel (#101).

Stdlib only, and no import of :mod:`aelix_coding_agent.cli` or
:mod:`aelix_coding_agent.tui`: this package is the SINGLE source of the topic
list, and both the ``aelix docs`` subcommand and any future in-agent tool read
it. A second copy of the topic list is the defect this package exists to
prevent, so anything that would make it expensive to import from a tool context
does not belong here.
"""

from .registry import (
    ALIASES,
    NEAR_MISSES,
    SearchHit,
    Topic,
    bundled_docs_dir,
    read_topic,
    resolve_topic,
    search_topics,
    topic_names,
    topics,
)

__all__ = [
    "ALIASES",
    "NEAR_MISSES",
    "SearchHit",
    "Topic",
    "bundled_docs_dir",
    "read_topic",
    "resolve_topic",
    "search_topics",
    "topic_names",
    "topics",
]
