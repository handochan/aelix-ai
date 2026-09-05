"""Drift guard: every ``<file>.py:NNN`` citation still points at what it cited.

WHY THERE IS A GATE FOR THIS AT ALL. This repository explains itself by citing
its own source with line numbers, and it does so heavily — 798 gated citations
when this file was written. Line numbers rot the instant anything above them
moves, silently and in bulk, and the repo had been burned three times before
anyone counted:

* #101 broke 9 of the 65 citations it touched, by inserting above them;
* the #120/#167/#161 batch displaced five constructs and broke about 19 more;
* and when the whole tree was finally read against its own prose, **551 of 793
  were wrong** — 69%, most of them predating both of the above, and 189 wrong
  on the day they were written.

The rule the repo kept re-deriving by hand is *"re-derive the citation, never
add a delta to it"*, and half of it is mechanisable: a citation claims that
specific TEXT lives at a line, so ``citations.lock.json`` pins the text and
``scripts/check_citations.py`` re-finds the line. The other half is not — see
(4).

WHAT THIS FILE ADDS OVER RUNNING THE SCRIPT. Four things the script cannot
assert about itself:

1. the tree is green **now** (the gate proper);
2. the ungated set — pi's TypeScript, httpx internals, ambiguous bare stems —
   cannot grow quietly, because its size is pinned;
3. history is genuinely excluded rather than accidentally missed;
4. the detector actually detects, and — the part that matters more — that it
   REFUSES to guess when a block was edited rather than moved.

(4) is the load-bearing one. An earlier revision of the relocator anchored on a
block's most distinctive surviving line and subtracted the offset. Measured
across 479 drifted citations, that put an ``agents/resolver.py`` range on a bare
``profile: AgentProfile,`` parameter line. A wrong number under a GREEN gate is
worse than the rot the gate exists to stop, so exactness is a property with a
test rather than a style choice.

NOTE ON THE EXAMPLES ABOVE. They name files without line numbers on purpose:
this file's own prose is a gated site, and a historical "here is what a stale
citation looked like" would otherwise be checked as a live pointer and fail.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check_citations.py"
LOCK = REPO / "citations.lock.json"

#: Printed in every failure message so the repair does not have to be
#: reconstructed from a diff.
_REPAIR = "uv run python scripts/check_citations.py --fix"


def _load() -> ModuleType:
    """``scripts/`` is not a package, so the gate is loaded by path.

    Registered in ``sys.modules`` BEFORE ``exec_module``: the script defines
    dataclasses, and ``@dataclass`` resolves each field's type by looking its
    own module up by name. An unregistered module makes that lookup return
    ``None`` and the decorator dies on import.
    """

    spec = importlib.util.spec_from_file_location("_check_citations", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cc = _load()


# --- (1) the gate ---------------------------------------------------------------


def test_no_citation_has_drifted(capsys: pytest.CaptureFixture[str]) -> None:
    """The gate. Every gated citation still holds the text the lock recorded."""

    assert LOCK.exists(), f"citations.lock.json is missing — run `{_REPAIR}`"
    rc = cc.cmd_check()
    report = capsys.readouterr().out
    assert rc == 0, f"{report}\n\nRepair with: {_REPAIR}"


# --- (2) the ungated set is pinned ----------------------------------------------


def test_the_ungated_set_has_not_grown() -> None:
    """Citations that resolve to no single tracked file are not gated.

    Those are pi's ``.ts`` sources, the mcp SDK, httpx internals, and a handful
    of bare stems that two files in the tree share. They are legitimate, but a
    silently GROWING ungated set is how coverage evaporates: rename a module so
    two files share a stem and thirty citations stop being checked with nothing
    printed. So the count is pinned to the lock, both ways.
    """

    stats = json.loads(LOCK.read_text(encoding="utf-8"))["stats"]
    live = cc.build_stats(cc.iter_citations())
    assert live["unresolved"] <= stats["unresolved"], (
        f"{live['unresolved'] - stats['unresolved']} more citation(s) are now UNGATED "
        f"(was {stats['unresolved']}). A new ambiguous stem stops being checked "
        f"silently — see `--report`. If intended, run `{_REPAIR}`."
    )
    assert live["gated"] >= stats["gated"] - 5, (
        f"gated coverage dropped from {stats['gated']} to {live['gated']}. "
        f"If citations were legitimately deleted, run `{_REPAIR}`."
    )


# --- (3) history is excluded on purpose -----------------------------------------


@pytest.mark.parametrize(
    ("rel", "gated"),
    [
        ("packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py", True),
        ("tests/cli/test_agent_context.py", True),
        ("docs/guides/project-trust.md", True),
        ("SECURITY.md", True),
        ("SLICE-STATUS.md", True),
        # A dated record of a decision. Rewriting its numbers to match today's
        # tree would make it claim it was written about code that did not exist.
        ("docs/decisions/0220-anything.md", False),
        (".omc/specs/some-sprint-plan.md", False),
        # Prose that is not a guide: the vision/requirements documents describe
        # intent, not the current tree.
        ("docs/01-product-vision.md", False),
        ("uv.lock", False),
    ],
)
def test_the_scope_rule_is_the_one_that_was_argued_for(rel: str, gated: bool) -> None:
    assert cc.is_gated_site(rel) is gated


def test_a_stale_citation_in_an_adr_does_not_fail_the_gate() -> None:
    """The exclusion has to be real, not merely "we did not happen to look".

    ADRs are full of citations that were correct on their date and are wrong
    now — by design. Proven by checking that no ADR appears among the scanned
    sites at all, rather than by trusting the prefix list.
    """

    scanned = {c.citing_file for c in cc.iter_citations()}
    offenders = sorted(
        f for f in scanned if f.startswith("docs/decisions/") or f.startswith(".omc/")
    )
    assert offenders == [], offenders


# --- (4) the detector detects, and refuses to guess ------------------------------


def _fake_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setattr(cc, "REPO", tmp_path)
    return cc


def _cite(target: str, start: int, end: int) -> Any:
    return cc.Citation(
        citing_file="tests/fake.py",
        citing_line=1,
        col=0,
        num_text=f"{start}-{end}" if end != start else str(start),
        target_raw=target,
        target=target,
        start=start,
        end=end,
    )


def test_a_construct_pushed_down_is_found_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case: something was inserted above, nothing was rewritten."""

    _fake_tree(monkeypatch, tmp_path)
    target = tmp_path / "mod.py"
    target.write_text("# pad\n" * 4 + "def the_thing() -> int:\n    return 7\n")

    tree = cc.Tree()
    cite = _cite("mod.py", 5, 6)
    anchors = {cite.key: ["def the_thing() -> int:", "return 7"]}
    assert cc.find_drift([cite], anchors, tree) == ([], [])

    target.write_text("# pad\n" * 9 + "def the_thing() -> int:\n    return 7\n")
    drifted, _ = cc.find_drift([cite], anchors, cc.Tree())
    assert len(drifted) == 1
    assert drifted[0].suggestion == (10, 11)


def test_an_edited_block_is_reported_rather_than_guessed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE property that makes the gate safe to trust.

    The block moved AND part of it was rewritten. Its ``def`` line survived, so
    a relocator willing to anchor on the one surviving line and subtract the
    offset would answer confidently — and would be wrong whenever the rewrite
    also changed how many lines precede that one. The right answer is "I could
    not find it", so a human re-derives.
    """

    _fake_tree(monkeypatch, tmp_path)
    target = tmp_path / "mod.py"
    target.write_text("# pad\n" * 4 + "def the_thing() -> int:\n    return 7\n")
    cite = _cite("mod.py", 5, 6)
    anchors = {cite.key: ["def the_thing() -> int:", "return 7"]}

    target.write_text("# pad\n" * 9 + "def the_thing() -> int:\n    return compute()\n")
    drifted, _ = cc.find_drift([cite], anchors, cc.Tree())
    assert len(drifted) == 1
    assert drifted[0].suggestion is None, "an edited block must not be relocated blind"
    # ...but the human is not left with nothing: the advisory hint still points
    # at where the surviving line went. Advisory, and never applied.
    assert cc.Tree().relocate_hint("mod.py", anchors[cite.key]) == [10]


def test_an_ambiguous_block_is_not_relocated_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boilerplate that appears twice must not be resolved by picking the first."""

    _fake_tree(monkeypatch, tmp_path)
    body = "def helper() -> None:\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(body)
    cite = _cite("mod.py", 1, 2)
    anchors = {cite.key: ["def helper() -> None:", "pass"]}

    target.write_text("# moved\n" + body + "\n" + body)
    drifted, _ = cc.find_drift([cite], anchors, cc.Tree())
    assert len(drifted) == 1
    assert drifted[0].suggestion is None


def test_a_new_citation_is_flagged_rather_than_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A citation with no locked anchor has never been checked by anything.

    Passing it silently would let every newly written citation be born wrong,
    which is how the tree reached 490.
    """

    _fake_tree(monkeypatch, tmp_path)
    (tmp_path / "mod.py").write_text("x = 1\n")
    cite = _cite("mod.py", 1, 1)
    drifted, unlocked = cc.find_drift([cite], {}, cc.Tree())
    assert drifted == []
    assert len(unlocked) == 1


# --- the scanner ------------------------------------------------------------------


def test_a_continuation_inherits_the_file_from_the_citation_before_it() -> None:
    """``(`project_trust.py:161`, `:196`, `:244`)`` is three citations.

    Thirty of them exist in the tree. A scanner that saw only the first would
    leave the other two to rot beside a number it kept correct — which reads
    worse than leaving all three alone.
    """

    line = "see (``project_trust.py:161``, ``:196``, ``:244``) for the walk"
    assert [(m.group(1), int(m.group(2))) for m in cc.FULL.finditer(line)] == [
        ("project_trust.py", 161)
    ]
    assert [int(m.group(1)) for m in cc.CONT.finditer(line)] == [196, 244]

    # And end to end, on the real tree: the guide that carries this exact
    # sentence yields all three, not one.
    guide = [
        c
        for c in cc.iter_citations()
        if c.citing_file == "docs/guides/project-trust.md"
        and c.target_raw == "project_trust.py"
    ]
    assert len(guide) > len({c.citing_line for c in guide}), (
        "no line in the guide carries more than one citation — "
        "the continuation path is not being exercised"
    )


def test_line_numbers_are_counted_the_way_every_other_tool_counts_them() -> None:
    """``str.splitlines()`` is not line numbering, and this tree proves it.

    Python also breaks on U+2028, U+2029, form feed and vertical tab; ``sed``,
    ``awk``, ``git blame``, editors and the tracebacks a reader compares against
    break only on ``\\n``. FIVE tracked files here contain such a character, and
    one of them is ``cli/agent_context.py`` — the most-cited target in the repo —
    so the first version of this tool placed every citation into it exactly one
    line low, in silence.

    Asserted against the real tree, because the hazard being live *here* is the
    whole reason the helper exists.
    """

    divergent = []
    for rel in cc._tracked_files():
        if not rel.endswith((".py", ".md")):
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text:
            continue
        expected = text.count("\n") + (0 if text.endswith("\n") else 1)
        # The helper agrees with `\n` counting on EVERY tracked file...
        assert len(cc.split_lines(text)) == expected, rel
        if len(text.splitlines()) != expected:
            divergent.append(rel)

    # ...and the divergence it protects against is real in this repo today.
    assert divergent, (
        "no tracked file makes splitlines() disagree any more — if that is "
        "genuinely true, keep split_lines() anyway and relax this assertion"
    )


def test_the_reader_and_the_anchor_use_that_counting_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Having the right helper is not the same as calling it.

    The previous test pins ``split_lines`` and nothing else — measured, putting
    ``splitlines()`` back inside :meth:`Tree.lines` left it GREEN, which is the
    false-green shape this repo keeps rediscovering. What a citation resolves
    against is ``Tree``, so ``Tree`` is what has to be asserted on.

    The probe file carries a form feed, the cheapest of the characters Python
    splits on and ``awk`` does not; ``TARGET`` sits on line 4 by every ordinary
    count and on line 5 by ``splitlines()``.
    """

    _fake_tree(monkeypatch, tmp_path)
    rel = "probe.py"
    text = "a = 1\n# pad \x0c more\nc = 3\nTARGET = 4\n"
    (tmp_path / rel).write_text(text, encoding="utf-8")

    assert text.count("\n") == 4  # what sed/awk/git would call the last line
    assert len(text.splitlines()) == 5  # ...and what Python would call it

    tree = cc.Tree()
    assert tree.lines(rel)[3] == "TARGET = 4"
    assert tree.anchor(rel, 4, 4) == ["TARGET = 4"]


def test_the_scanner_numbers_citing_lines_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And so does the third place that counts lines.

    ``citing_line`` is what ``--fix`` edits. Getting it one low means rewriting
    a number on the WRONG line of the citing file — a silent corruption of prose
    the tool was pointed at to protect. Gated separately from :meth:`Tree.lines`
    because they are separate call sites: measured, fixing one and reverting the
    other left every other test in this file green.
    """

    _fake_tree(monkeypatch, tmp_path)
    base = "packages/aelix-coding-agent/src/aelix_coding_agent"
    ref = f"{base}/probe.py"
    target = f"{base}/mod.py"
    (tmp_path / ref).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ref).write_text(
        "# pad \x0c more\n# see mod.py:7 for the thing\n", encoding="utf-8"
    )
    (tmp_path / target).write_text("x = 1\n" * 20, encoding="utf-8")

    found = [c for c in cc.iter_citations([ref, target]) if c.citing_file == ref]
    assert len(found) == 1
    assert found[0].citing_line == 2, "the form feed shifted the citing line"


def test_a_continuation_does_not_steal_a_file_named_after_it() -> None:
    """``:189-193`` belongs to the file named BEFORE it, not beside it.

    Measured on ``aelix_agents/reaper.py`` (line 278 at the time), which read

        ``(:189-193). Since ``agents/resolver.py:314-315`` makes …``

    where the bare number continues ``modes/print_mode.py`` from four lines up.
    Attributing it to "the last full citation on the line" pointed it at
    ``resolver.py`` and rewrote a CORRECT citation into a wrong one — the exact
    harm this gate exists to prevent, arriving through a different door.

    A continuation with nothing before it on its own line is left ungated rather
    than guessed at: cross-line inheritance is what the author meant there, and
    nothing here can tell that from a coincidence.
    """

    base = "packages/aelix-coding-agent/src/aelix_coding_agent"
    ref = f"{base}/reaper_probe.py"
    # INTERPOLATED, so the fixture and the assertion below cannot drift apart.
    # They were literals, and twice now ``check_citations.py --fix`` relocated
    # the one inside ``body`` — it looks like a real citation into a real file —
    # while leaving the assertion's tuple alone, turning a green gate into a red
    # test for no product reason. Interpolation also takes this synthetic out of
    # the gate's sight entirely, which is what it should always have been
    # (#220 review round 2, fix lane).
    lead = (221, 228)
    tail = (314, 315)
    body = (
        '"""doc.\n'
        "\n"
        f"    ``modes/print_mode.py:{lead[0]}-{lead[1]}`` records it, and the\n"
        "    acting break (``:198-205``) is strictly after the prompt call\n"
        f"    (``:189-193``). Since ``agents/resolver.py:{tail[0]}-{tail[1]}``\n"
        "    makes it so.\n"
        '    """\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ref).parent.mkdir(parents=True, exist_ok=True)
        (root / ref).write_text(body, encoding="utf-8")
        stubs = [f"{base}/modes/print_mode.py", f"{base}/agents/resolver.py"]
        for stub in stubs:
            (root / stub).parent.mkdir(parents=True, exist_ok=True)
            (root / stub).write_text("x = 1\n" * 400, encoding="utf-8")

        original = cc.REPO
        cc.REPO = root
        try:
            found = [
                c
                for c in cc.iter_citations([ref, *stubs])
                if c.citing_file == ref
            ]
        finally:
            cc.REPO = original

    stolen = {(c.start, c.end) for c in found if "resolver" in (c.target or "")}
    assert stolen == {tail}, (
        f"a bare continuation was attributed to resolver.py: {sorted(stolen)}"
    )
    # The full citation on its own line is still picked up, so this is not
    # passing by scanning nothing.
    assert lead in {(c.start, c.end) for c in found}


def test_a_port_number_is_not_mistaken_for_a_citation() -> None:
    """``localhost:8080`` and ``:8`` are not line references.

    The continuation pattern needs at least two digits and must not be preceded
    by a word character, or every URL in a docstring becomes a citation into
    whatever file was named earlier on the line.
    """

    assert [m.group(1) for m in cc.CONT.finditer("see foo.py:12 then http://x:8080/y")] == []
    assert [m.group(1) for m in cc.CONT.finditer("foo.py:12 and :9 too")] == []
    assert [m.group(1) for m in cc.CONT.finditer("foo.py:12 and :90 too")] == ["90"]
