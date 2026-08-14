"""The bundled user guides, as topics — the single source both the CLI and any
future in-agent tool read (#101).

Stdlib only. No ``rich``, no ``prompt_toolkit``, no ``aelix_coding_agent.cli``
import: the point of this module is that a tool running inside a turn can ask
"what guides do we ship, and what is in them" without paying for the TUI extra
or importing the CLI's import graph.

Measured twice, because the two halves are different claims.
``tests/help/test_help_registry.py`` asserts in a CHILD interpreter that
importing this package leaves ``rich`` and ``prompt_toolkit`` out of
``sys.modules`` — with a companion test proving both ARE importable there, so
``CLEAN`` is earned rather than a consequence of them being absent. Separately,
the wheel was installed into a fresh venv WITHOUT the ``tui`` extra
(``rich``/``prompt_toolkit`` both ``find_spec -> None``) and
``aelix docs security`` printed the guide.

TOPICS ARE DERIVED FROM THE FILES, never listed here. A hardcoded list goes
stale in the one direction nobody notices: a guide is added, the list is not
updated, and ``aelix docs`` silently does not offer it while every sync test
stays green (the sync tests compare ``docs/guides/`` against the bundled copies;
neither of them knows this module exists).

WHY ``Path(__file__).parents[1]`` AND NOT ``importlib.resources``.

The depth is measured, not reasoned — it is the one thing here that is easy to
get subtly wrong, because a wrong depth yields a path that EXISTS and is simply
empty, which reads as "no guides are bundled" rather than as an error. From
``.../aelix_coding_agent/help/registry.py``:

    parents[0] = .../aelix_coding_agent/help          -> /docs does not exist
    parents[1] = .../aelix_coding_agent               -> /docs, 8 .md files  <-
    parents[2] = .../site-packages (or .../src)       -> /docs does not exist

Same idiom as :func:`aelix_coding_agent.cli.config.packaged_skills_dir`, which
resolves the packaged skills directory the same way and for the same reason:
one form that is correct both for a source checkout and for site-packages.

``importlib.resources`` was NOT rejected as unavailable — that claim was checked
and is false. It is stdlib, and ``importlib.resources.files`` is present in a
``python -m venv`` with nothing installed (measured, CPython 3.12.1: ``has
files: True``). It is not used because the repo already has a working idiom for
exactly this and two idioms for one job is how they drift apart. The one thing
``importlib.resources`` would buy — reading out of a zipimport — does not apply:
wheels install unpacked.

RESOLVED AT CALL TIME, never at import. A module-level constant would freeze the
path at first import, which is wrong for tests that relocate the package and
wrong for anything that reinstalls under a running process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class Topic:
    """One bundled guide.

    :param name: the topic name — the filename stem, which is what a user types.
    :param path: absolute path to the ``.md`` file inside the installed package.
    :param title: the document's first ``# `` heading, or the name if it has
        none. Read from the file rather than stored, so it cannot disagree with
        what ``aelix docs <topic>`` prints.
    """

    name: str
    path: Path
    title: str


#: Names that mean an existing topic. Two sources feed this: the issue's own
#: vocabulary (#101 names `extension`, `extension-api`, `tools`, `skills`,
#: `security`), and the words a user reaches for that are not the filename.
#:
#: `tools` maps to the extension-authoring guide deliberately: that guide's
#: "Registering a tool" section is the only document in the repo about tools as
#: a thing you can add. The built-in tool LIST is not documented in a guide at
#: all — it lives in `aelix --help` under `--tools` — so a `tools` topic
#: promising otherwise would be the invented document this module refuses to
#: create.
ALIASES: dict[str, str] = {
    "extension": "extension-authoring",
    "extension-api": "extension-authoring",
    "extensions": "extension-authoring",
    "extension-authoring": "extension-authoring",
    "tools": "extension-authoring",
    "plugin": "extension-authoring",
    "agents": "agent-profiles",
    "agent": "agent-profiles",
    "profiles": "agent-profiles",
    "subagents": "agent-profiles",
    "security": "project-trust",
    "trust": "project-trust",
    "models": "providers-and-models",
    "providers": "providers-and-models",
    "auth": "providers-and-models",
    "login": "providers-and-models",
    "models.json": "models-json",
    "custom-models": "models-json",
    "catalog": "private-catalog",
    "marketplace": "private-catalog",
    "install": "getting-started",
    "start": "getting-started",
    "quickstart": "getting-started",
    "index": "README",
    "readme": "README",
}

#: Names a user will plausibly type that NO bundled guide answers, mapped to an
#: honest pointer. Kept apart from :data:`ALIASES` on purpose: aliasing one of
#: these to the nearest document would print a guide that does not answer the
#: question, which is worse than saying there is none.
#:
#: `skills` is here rather than in ALIASES because aelix has no skills-authoring
#: guide. The word appears in two guides — `agent-profiles` documents the
#: `skills:` frontmatter key, `project-trust` documents why a project
#: `.aelix/skills/` is gated — and neither tells you how to write one. That is a
#: real content gap, recorded here so `aelix docs skills` reports it instead of
#: quietly handing over a document about something else.
NEAR_MISSES: dict[str, str] = {
    "skills": (
        "no guide covers writing a skill yet. The closest coverage is "
        "`agent-profiles` (the `skills:` frontmatter key, which takes paths) "
        "and `project-trust` (why a project-local .aelix/skills/ is gated)."
    ),
    "mcp": (
        "no guide covers MCP servers yet. `extension-authoring` documents the "
        "`contributes.mcp_servers` manifest family and the capability flags "
        "that gate it; `project-trust` covers the project-local .aelix/mcp.json "
        "gate."
    ),
    "hooks": (
        "no standalone hooks guide. `extension-authoring` covers both hook "
        "surfaces: `aelix.on(...)` for in-process handlers and the "
        "`contributes.hooks` manifest family for subprocess ones."
    ),
}


def bundled_docs_dir() -> Path:
    """The directory of bundled guides inside the installed package.

    Returns the path whether or not it exists — a stripped-down or partially
    installed package should get an empty topic list, not an exception, for the
    same reason ``packaged_skills_dir`` returns a possibly-missing path.
    """

    return Path(__file__).resolve().parents[1] / "docs"


def _title_of(path: Path) -> str:
    """First ``# `` heading, or the stem. Never raises on an unreadable file:
    a doc that cannot be read still deserves to be listed as a topic, because
    the listing is how a user discovers the name to report."""

    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


def topics() -> list[Topic]:
    """Every bundled guide, sorted by name.

    ``README`` sorts to the front of the ASCII ordering, which is the right
    place for it — it is the index.
    """

    docs = bundled_docs_dir()
    if not docs.is_dir():
        return []
    return [
        Topic(name=p.stem, path=p, title=_title_of(p))
        for p in sorted(docs.glob("*.md"))
    ]


def topic_names() -> list[str]:
    return [t.name for t in topics()]


def resolve_topic(name: str) -> Topic | None:
    """Resolve a user-typed name to a topic, or ``None``.

    Case- and extension-insensitive (``Extension``, ``extension.md`` and
    ``extension`` all work) because a user who has just seen a filename in the
    listing will type the filename.
    """

    key = name.strip().lower()
    if key.endswith(".md"):
        key = key[:-3]
    if not key:
        return None
    available = topics()
    by_name = {t.name.lower(): t for t in available}
    if key in by_name:
        return by_name[key]
    target = ALIASES.get(key)
    if target is not None:
        return by_name.get(target.lower())
    return None


def read_topic(topic: Topic) -> str:
    """The document's text. Errors are the caller's to report — a docs command
    that swallowed a read failure would print an empty page and exit 0."""

    return topic.path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class SearchHit:
    """One matching line. ``line`` is 1-indexed, matching every editor and the
    ``file:line`` form the rest of this repo cites."""

    topic: Topic
    line: int
    text: str


def search_topics(term: str, *, limit_per_topic: int = 5) -> list[SearchHit]:
    """Case-insensitive substring search across every bundled guide.

    Substring rather than regex: a user searching for ``models.json`` or
    ``[capabilities]`` should not have to know that ``.`` and ``[`` are
    metacharacters, and a docs search is not worth a regex-injection failure
    mode. ``limit_per_topic`` keeps one guide that mentions a common word on
    every line from burying the other seven.
    """

    needle = term.strip().lower()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for topic in topics():
        found = 0
        try:
            text = read_topic(topic)
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(SearchHit(topic=topic, line=number, text=line.strip()))
                found += 1
                if found >= limit_per_topic:
                    break
    return hits
