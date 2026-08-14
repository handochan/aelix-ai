"""#101 — the bundled-documentation block in the base system prompt.

A pi forward-port (``system-prompt.ts`` names ``getDocsPath()`` /
``getReadmePath()`` / ``getExamplesPath()``; aelix bundled guides but never
pointed the model at them).

HELD TO ``agent_context``'s OWN STANDARD, which its module docstring states: an
overclaim in a doc misleads a reader who can check it, an overclaim HERE is
ACTED ON by the model. So these tests do not merely assert that the block is
present — each one re-derives from disk the fact the block asserts:

- every ``<name>`` it lists resolves to a real file (a hallucinated path in the
  prompt is the #117 failure re-created from the docs side);
- "small enough for ``read`` to return whole" is checked against the real
  truncation cap, because that exact promise was already broken once by
  ``api.py``;
- the highlight clause is checked against the guide's headings.

The PACKAGING half — that ``aelix_coding_agent/docs/*.md`` is really in the
wheel, and that ``bundled_docs_dir()`` resolves in an installed layout and not
only in this checkout — is not re-derived here. It is already asserted against a
built wheel by ``tests/packaging/test_docs_bundle.py``
(``::test_every_guide_is_in_the_wheel``,
``::test_the_docs_resolve_from_an_INSTALLED_layout_not_the_source_tree``), and
this block emits the path that same function returns. A third wheel build for
the same fact would cost ~2.5s a run and prove nothing new.
"""

from __future__ import annotations

import re
from pathlib import Path

from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_coding_agent.cli import agent_context as _agent_context
from aelix_coding_agent.cli.agent_context import build_system_prompt
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import _build_harness_options
from aelix_coding_agent.help import bundled_docs_dir, topic_names


# Issue #120 — ``build_system_prompt`` takes the ACTIVE tool set and it is
# REQUIRED, so every call here has to say which session it is describing. The
# blocks these tests assert on are themselves tool-gated: the docs signpost
# follows ``read`` and the self-extension signpost follows ``write``, so a bare
# call would emit neither and the assertions would fail for a reason that has
# nothing to do with what they are testing.
def _all_builtin_tools() -> list:
    from aelix_coding_agent.tools import create_all_tools

    return list(create_all_tools(".").values())


_ALL_TOOL_NAMES_SET = {"read", "bash", "edit", "write", "grep", "find", "ls"}

_HEADER = "Aelix documentation"
#: The block emits ``<dir>/<name>.md`` once and then a bare comma-separated
#: list. Recovered here rather than hardcoded so the test reads the same names
#: the model does.
_LIST_MARKER = "<name> is one of: "


def _docs_block(prompt: str) -> str:
    """The emitted block, sliced out of the assembled prompt.

    Sliced from the real prompt rather than taken from ``_docs_signpost(_ALL_TOOL_NAMES_SET)``
    directly: a block that is correct in isolation and never reaches the prompt
    is the failure this slice makes impossible to miss.
    """

    start = prompt.index(_HEADER)
    end = prompt.index("Extending yourself", start)
    return prompt[start:end]


def _listed_names(block: str) -> list[str]:
    line = next(ln for ln in block.splitlines() if _LIST_MARKER in ln)
    return [n.strip() for n in line.split(_LIST_MARKER, 1)[1].split(",")]


def test_the_block_is_in_the_prompt_and_names_the_bundled_directory() -> None:
    prompt = build_system_prompt("/some/project", tools=_all_builtin_tools())
    block = _docs_block(prompt)

    docs = bundled_docs_dir()
    assert docs.is_dir(), docs
    assert f"{docs}/<name>.md" in block


def test_every_name_the_block_lists_resolves_to_a_file_on_disk() -> None:
    """The whole point. A prompt that cites a guide the ``read`` tool cannot
    open reproduces, from the docs side, the confidently-wrong answer #117
    exists to stop.

    The names are recovered from the EMITTED text and re-resolved against the
    filesystem — not compared to ``topic_names()``, which would only prove the
    block agrees with the function it calls.
    """

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    names = _listed_names(block)
    assert names, block

    docs = bundled_docs_dir()
    for name in names:
        assert (docs / f"{name}.md").is_file(), f"{name} listed but not on disk"

    # ...and nothing bundled is silently missing from the list either, or the
    # model is told a guide does not exist.
    assert sorted(names) == sorted(topic_names())


def test_the_header_is_scoped_to_questions_about_aelix_itself() -> None:
    """The block is emitted on EVERY turn. An unconditional "here is the
    documentation" pulls the model toward reading docs when the user asked for
    ordinary work — the same finding that put a trigger clause on the extension
    signpost's header (review MINOR 6 there). pi scopes its block too."""

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    header = block.splitlines()[0]
    assert "read one only when the user asks about Aelix itself" in header


def test_a_highlight_naming_a_guide_that_is_not_bundled_is_omitted(
    monkeypatch,
) -> None:
    """``_DOC_HIGHLIGHTS`` is the one place in the block with a HARDCODED guide
    name, so it is the one place that can rot into a dead pointer. It must drop
    the line, exactly as ``_package_pointer`` drops a missing file pointer."""

    monkeypatch.setattr(
        _agent_context,
        "_DOC_HIGHLIGHTS",
        (("guide-that-does-not-exist", "covers nothing"),),
    )
    prompt = build_system_prompt("/some/project", tools=_all_builtin_tools())
    block = _docs_block(prompt)

    assert "guide-that-does-not-exist" not in prompt
    assert "covers nothing" not in prompt
    # The rest of the block survives — an unresolvable highlight is not fatal.
    assert _HEADER in block
    assert _listed_names(block) == topic_names()


def test_the_whole_block_disappears_when_nothing_is_bundled(
    monkeypatch, tmp_path
) -> None:
    """A stripped or partially installed package must get NO block, never a
    header over an empty list. ``bundled_docs_dir`` deliberately returns a path
    whether or not it exists, so the emptiness has to be handled here."""

    from aelix_coding_agent.help import registry

    empty = tmp_path / "no-docs"
    empty.mkdir()
    monkeypatch.setattr(registry, "bundled_docs_dir", lambda: empty)

    assert _agent_context._docs_signpost(_ALL_TOOL_NAMES_SET) == ""
    prompt = build_system_prompt("/some/project", tools=_all_builtin_tools())
    assert _HEADER not in prompt
    # ...and the rest of the base prompt is untouched.
    assert "Extending yourself" in prompt
    assert "Working directory" in prompt


def test_a_guide_added_to_the_bundle_appears_with_no_code_change(
    monkeypatch, tmp_path
) -> None:
    """The list is globbed at call time. A hardcoded list would go stale in the
    direction nobody notices: a guide ships, the prompt never mentions it, and
    every other test stays green."""

    from aelix_coding_agent.help import registry

    fake = tmp_path / "docs"
    fake.mkdir()
    (fake / "brand-new-guide.md").write_text("# Brand New\n", encoding="utf-8")
    monkeypatch.setattr(registry, "bundled_docs_dir", lambda: fake)

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    assert _listed_names(block) == ["brand-new-guide"]


def test_every_bundled_guide_fits_in_one_read() -> None:
    """The block promises "small enough for ``read`` to return whole".

    This is the claim most likely to rot, and it has already rotted once on the
    other side of this file: the extension signpost told the model to read
    ``extensions/api.py``, which is 84KB, so ``read`` truncated at 50KB and
    returned a window containing NONE of the ``register_*`` definitions — a
    confident wrong answer. A guide crossing the cap would do the same.

    The fix if this fails is to split the guide or drop the promise, not to
    raise the cap.

    BOTH CAPS, which is #101's L2 review. ``read`` calls ``truncate_head(
    selected, max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES)``
    (``tools/read.py:221-223``) and truncates when EITHER binds — the details
    even carry ``truncated_by`` to say which. The first revision of this test
    asserted only the byte cap, so a guide could pass it while the prompt told
    the model something false. Measured with one planted 2603-line / 33 834-byte
    guide in the bundled directory::

        old byte-only assertion   PASSES (33834 < 51200)
        truncate_head(...)        truncated=True truncated_by='lines'
                                  kept_lines=2000 of 2603

    The promise is asserted FIRST on purpose: a corpus-only size check would
    pass with no block in the prompt at all, i.e. it would be green before the
    change it exists to guard.
    """

    from aelix_coding_agent.tools._truncate import (
        DEFAULT_MAX_BYTES,
        DEFAULT_MAX_LINES,
    )

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    assert "small enough for `read` to return whole" in block

    docs = bundled_docs_dir()
    guides = sorted(docs.glob("*.md"))
    # Guard: an empty corpus would make every assertion below vacuous.
    assert guides, f"no bundled guides under {docs}"
    for guide in guides:
        size = guide.stat().st_size
        assert size < DEFAULT_MAX_BYTES, f"{guide.name} is {size}B, over the read cap"
        # ``read`` splits on "\n", so the line count that matters is
        # ``text.split("\n")`` — one more than the number of newlines when the
        # file ends in one. Counted the same way here rather than with
        # ``splitlines()``, which would be off by one against the tool.
        lines = len(guide.read_text(encoding="utf-8").split("\n"))
        assert lines <= DEFAULT_MAX_LINES, (
            f"{guide.name} is {lines} lines, over `read`'s "
            f"DEFAULT_MAX_LINES={DEFAULT_MAX_LINES}. It is under the byte cap "
            f"and `read` would still truncate it (truncated_by='lines')."
        )


def test_the_highlight_clause_is_true_of_the_guide_it_describes() -> None:
    """The one editorial sentence in the block. It claims
    ``extension-authoring`` covers three things; each is checked against the
    guide's own headings.

    An earlier draft of this clause also claimed "hooks", on the reasoning that
    the source files the signpost names do not cover them. Measured, that was
    false — ``grep -ci hook extensions/api.py`` -> 105 — and the word was cut.
    The surviving three are the ones the source files really do leave to the
    guide; ``echo.py`` is re-measured below because it is the file the signpost
    tells the model to read WHOLE, so anything in it is genuinely covered.
    """

    highlights = dict(_agent_context._DOC_HIGHLIGHTS)
    assert set(highlights) == {"extension-authoring"}, highlights
    clause = highlights["extension-authoring"]
    assert clause == "covers the aelix-plugin.toml manifest, capabilities and publishing"

    guide = (bundled_docs_dir() / "extension-authoring.md").read_text(encoding="utf-8")
    headings = [ln for ln in guide.splitlines() if ln.startswith("#")]
    assert any("Manifest contributions" in h for h in headings), headings
    assert any("Capabilities" in h for h in headings), headings
    assert any("Publishing" in h for h in headings), headings
    assert "aelix-plugin.toml" in guide

    example = Path(_agent_context._package_pointer(*_agent_context._EXAMPLE_PARTS))
    assert "aelix-plugin" not in example.read_text(encoding="utf-8")


def test_the_block_does_not_repeat_the_extension_signposts_files() -> None:
    """Two blocks about extensions sit next to each other in the same prompt.
    The signpost owns the API-signature pointers (``echo.py``, ``api.py``);
    this block must not re-emit them, or the reader gets four paths for two
    jobs and no ordering between them."""

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    assert "echo.py" not in block
    assert "api.py" not in block


def test_the_signposts_no_manifest_clause_does_not_contradict_this_block() -> None:
    """Adding this block put ``aelix-plugin.toml`` directly above a signpost
    sentence that read "— no manifest, no JSON, no build step, nothing to
    install".

    True of the ONE file that sentence describes, false of Aelix (the format
    ships as ``examples/echo/aelix-plugin.toml``), and now sitting two lines
    under a block that names it. A model resolving that the wrong way refuses
    to write a legitimate manifest, which is the mirror image of the #117
    failure the signpost exists to fix.
    """

    prompt = build_system_prompt("/some/project", tools=_all_builtin_tools())
    assert "for that file there is no manifest" in prompt
    assert "aelix-plugin.toml" in _docs_block(prompt)

    manifest = Path(_agent_context._package_pointer("examples", "echo", "aelix-plugin.toml") or "")
    assert manifest.is_file(), "the format the scoping clause concedes must ship"


def test_the_docs_block_comes_before_the_extension_signpost() -> None:
    """The signpost is the block the model ACTS on, and its budget test's
    premise is that it holds the most recency-weighted slot of the base prompt.
    Documentation is a place to look, not a thing to do."""

    prompt = build_system_prompt("/some/project", tools=_all_builtin_tools())
    assert prompt.index(_HEADER) < prompt.index("Extending yourself")


def test_the_block_prose_stays_within_its_measured_budget() -> None:
    """A permanent per-turn tax, budgeted the way the signpost's is: on the
    PROSE, because the one emitted path's length depends on where the package
    is installed (a deep site-packages path is much longer than a checkout).

    Measured at authoring: 632 chars emitted, 555 of them prose, taking the
    prompt from 3362 to 3994. The budget is not style policing — it is there so
    that growing this block into a chapter has to be a decision.
    """

    block = _agent_context._docs_signpost(_ALL_TOOL_NAMES_SET)
    docs_dir = str(bundled_docs_dir())
    assert docs_dir in block
    prose = len(block) - len(docs_dir)
    assert prose < 640, prose


def test_the_names_line_carries_no_path_the_model_must_assemble_twice() -> None:
    """pi needs an extra prompt line ("resolve ``docs/...`` under Additional
    docs, not the current working directory") because it prints topic files as
    repo-relative paths. Emitting one absolute ``<dir>/<name>.md`` form removes
    that ambiguity, so no ``docs/<something>.md`` shape may appear here."""

    block = _docs_block(build_system_prompt("/some/project", tools=_all_builtin_tools()))
    docs_dir = str(bundled_docs_dir())
    stripped = block.replace(f"{docs_dir}/<name>.md", "")
    assert not re.search(r"\bdocs/[\w.-]+\.md", stripped), stripped


async def test_the_block_reaches_the_real_harness_prompt() -> None:
    """Not just the helper — the prompt the harness is actually built with."""

    options = await _build_harness_options(Args(), Session(MemorySessionStorage()))
    assert _HEADER in options.system_prompt
    assert "extension-authoring" in options.system_prompt


async def test_explicit_system_prompt_still_drops_the_block() -> None:
    """``--system-prompt`` is a full override; #101 must not smuggle a block
    back in, for the same reason the signpost does not.

    ``append_system_prompt`` is checked as well as ``system_prompt``, and that
    is the whole test. The rejected alternative was to emit this block as an
    APPEND chunk, which ``test_the_catalog_survives_a_custom_system_prompt``
    proves survives the override — so a version of this test that looked only
    at ``system_prompt`` would have been green under exactly the design it is
    supposed to reject (verified: wrapping ``entry._resolve_append_chunks`` to
    append ``_docs_signpost(_ALL_TOOL_NAMES_SET)`` leaves ``system_prompt`` clean and is caught
    only by the second assertion).
    """

    options = await _build_harness_options(
        Args(system_prompt="CUSTOM"), Session(MemorySessionStorage())
    )
    assert _HEADER not in options.system_prompt
    assert _HEADER not in "".join(options.append_system_prompt or [])
