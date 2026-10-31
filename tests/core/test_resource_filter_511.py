"""#511 (ADR-0279 D1, D2) — the settings filter grammar, as pure functions.

The pi cases are ported from ``coding-agent/test/package-manager.test.ts`` @
``318d9add6`` ("pattern filtering in top-level arrays", "force-include
patterns", "force-exclude patterns"), with pi's resolved path sets restated as
one item's on/off answer. The identity cases, the scope precedence and
:func:`edit` are aelix's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from aelix_coding_agent.core.resource_filter import (
    ScopeList,
    Target,
    edit,
    effective,
    evaluate,
    glob_matches,
    glob_problem,
    matches_any,
    source_path,
    targets_exactly,
    validate_entries,
)

from tests.env_sandbox import sandbox_home

BASE = "/home/u/.aelix/agent"


def skill(name: str, base: str = BASE) -> Target:
    return Target((name,), f"{base}/skills/{name}/SKILL.md")


def file_item(rel: str, base: str = BASE) -> Target:
    name = Path(rel).stem
    return Target((name,), f"{base}/{rel}")


# --- pi's top-level array cases --------------------------------------------


def test_tilde_expands_in_exclusion_and_exact_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """pi #10765."""

    sandbox_home(monkeypatch, "/home/u")
    entries = ["~/extra/skills", "!~/extra/skills/glob-*", "-~/extra/skills/exact-excluded"]
    root = "/home/u/extra/skills"
    keep = Target(("keep",), f"{root}/keep/SKILL.md")
    globbed = Target(("glob-excluded",), f"{root}/glob-excluded/SKILL.md")
    exact = Target(("exact-excluded",), f"{root}/exact-excluded/SKILL.md")
    assert evaluate(keep, entries, BASE) is None
    assert evaluate(globbed, entries, BASE) is False
    assert evaluate(exact, entries, BASE) is False


def test_globstar_exclusion_matches_a_nested_file() -> None:
    entries = ["extensions", "!**/remove.ts"]
    assert evaluate(file_item("extensions/keep.ts"), entries, BASE) is None
    assert evaluate(file_item("extensions/remove.ts"), entries, BASE) is False


def test_basename_exclusion() -> None:
    entries = ["prompts", "!explain.md"]
    assert evaluate(file_item("prompts/review.md"), entries, BASE) is None
    assert evaluate(file_item("prompts/explain.md"), entries, BASE) is False


def test_skill_exclusion_matches_its_parent_directory() -> None:
    entries = ["skills", "!**/bad-skill"]
    assert evaluate(skill("good-skill"), entries, BASE) is None
    assert evaluate(skill("bad-skill"), entries, BASE) is False


def test_force_include_beats_a_glob_exclusion() -> None:
    entries = ["extensions", "!extensions/*.ts", "+extensions/force-back.ts"]
    assert evaluate(file_item("extensions/other.ts"), entries, BASE) is False
    assert evaluate(file_item("extensions/force-back.ts"), entries, BASE) is True


def test_force_include_several_after_excluding_everything() -> None:
    pkg = "/pkg"
    entries = ["!**/*", "+skills/skill-a", "+skills/skill-c"]
    assert evaluate(skill("skill-a", pkg), entries, pkg) is True
    assert evaluate(skill("skill-b", pkg), entries, pkg) is False
    assert evaluate(skill("skill-c", pkg), entries, pkg) is True


def test_force_include_after_a_specific_exclusion_of_the_same_path() -> None:
    entries = ["extensions", "!extensions/b.ts", "+extensions/b.ts"]
    assert evaluate(file_item("extensions/b.ts"), entries, BASE) is True


def test_force_exclude_beats_force_include_whatever_the_order() -> None:
    assert (
        evaluate(
            file_item("extensions/alpha.ts"), ["-extensions/alpha.ts", "+extensions/alpha.ts"], BASE
        )
        is False
    )
    assert (
        evaluate(
            file_item("extensions/alpha.ts"), ["+extensions/alpha.ts", "-extensions/alpha.ts"], BASE
        )
        is False
    )


def test_exact_entries_take_no_globs() -> None:
    """``-vue*`` matches nothing, as in pi."""

    vue = skill("vue-helper")
    assert evaluate(vue, ["-vue*"], BASE) is None
    assert evaluate(vue, ["+vue*"], BASE) is None
    assert evaluate(vue, ["!vue*"], BASE) is False


def test_dot_relative_exact_entry() -> None:
    assert evaluate(skill("pdf"), ["-./skills/pdf"], BASE) is False


def test_absolute_exact_entry_and_skill_file_entry() -> None:
    target = skill("pdf")
    assert evaluate(target, [f"-{BASE}/skills/pdf"], BASE) is False
    assert evaluate(target, [f"-{BASE}/skills/pdf/SKILL.md"], BASE) is False
    assert evaluate(target, ["-skills/pdf/SKILL.md"], BASE) is False


# --- minimatch subset -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "pattern", "expected"),
    [
        ("a/b/c.ts", "**/*.ts", True),
        ("c.ts", "**/*.ts", True),
        ("a/b/c.ts", "*.ts", False),
        ("a/.hidden/c.ts", "**/c.ts", False),
        (".env", "*", False),
        (".env", ".*", True),
        ("deploy-check", "{deploy,release}-*", True),
        ("release-notes", "{deploy,release}-*", True),
        ("pdf-tools", "{deploy,release}-*", False),
        ("x1", "x[0-9]", True),
        ("xa", "x[!0-9]", True),
        ("xa", "x[^0-9]", True),
        ("x", "x?", False),
    ],
)
def test_glob_subset(value: str, pattern: str, expected: bool) -> None:
    assert glob_matches(value, pattern) is expected


# --- identity (aelix) -------------------------------------------------------


def test_identity_exact_and_glob() -> None:
    target = skill("pdf-tools")
    assert evaluate(target, ["-pdf-tools"], BASE) is False
    assert evaluate(target, ["!pdf-*"], BASE) is False
    assert evaluate(target, ["!pdf-*", "+pdf-tools"], BASE) is True
    assert evaluate(Target(("ext", "ext-dist"), None), ["-ext-dist"], BASE) is False


def test_identity_matches_a_project_item_from_the_global_list() -> None:
    """D2: a global name entry reaches an item found in the project."""

    project_item = Target(("pdf-tools",), "/work/app/.aelix/skills/pdf-tools/SKILL.md")
    global_list = ScopeList("global", ("-pdf-tools",), BASE)
    assert effective(project_item, global_list, None) == (False, "global")


def test_a_global_path_entry_does_not_reach_a_project_item() -> None:
    project_item = Target(("pdf",), "/work/app/.aelix/skills/pdf/SKILL.md")
    global_list = ScopeList("global", ("-skills/pdf",), BASE)
    assert effective(project_item, global_list, None) == (True, None)


# --- scopes -----------------------------------------------------------------


def test_project_opinion_beats_global_beats_default() -> None:
    target = skill("pdf")
    g_off = ScopeList("global", ("-pdf",), BASE)
    p_on = ScopeList("project", ("+pdf",), "/work/app/.aelix")
    p_none = ScopeList("project", ("-other",), "/work/app/.aelix")
    assert effective(target, None, None) == (True, None)
    assert effective(target, g_off, None) == (False, "global")
    assert effective(target, g_off, p_on) == (True, "project")
    assert effective(target, g_off, p_none) == (False, "global")


# --- edit -------------------------------------------------------------------


def test_edit_twice_returns_the_list() -> None:
    target = skill("pdf")
    start = ["~/.claude/skills", "!old-*"]
    off = edit(start, target, False, True, BASE)
    assert off == ["~/.claude/skills", "!old-*", "-pdf"]
    assert edit(off, target, True, True, BASE) == start


def test_edit_keeps_a_glob_and_beats_it_with_an_exact_entry() -> None:
    target = skill("pdf-tools")
    assert edit(["!pdf-*"], target, True, True, BASE) == ["!pdf-*", "+pdf-tools"]
    assert edit(["!pdf-*", "+pdf-tools"], target, False, True, BASE) == ["!pdf-*"]


def test_edit_drops_every_exact_entry_for_the_item_whatever_its_prefix() -> None:
    target = skill("pdf")
    entries = ["+pdf", "-skills/pdf", "!pdf", "-other"]
    assert edit(entries, target, False, True, BASE) == ["-other", "-pdf"]


def test_edit_writes_nothing_that_equals_the_inherited_value() -> None:
    target = skill("pdf")
    # A project override equal to what the global file already says is dropped.
    assert edit(["+pdf"], target, True, True, "/work/app/.aelix") == []
    assert edit([], target, False, False, "/work/app/.aelix") == []
    assert edit([], target, True, False, "/work/app/.aelix") == ["+pdf"]


def test_targets_exactly_spares_globs_and_sources() -> None:
    target = skill("pdf")
    assert targets_exactly("-pdf", target, BASE)
    assert targets_exactly("!pdf", target, BASE)
    assert not targets_exactly("!pd*", target, BASE)
    assert not targets_exactly("skills/pdf", target, BASE)


# --- validation, unknown entries, sources ----------------------------------


def test_validate_entries_reports_and_drops_malformed_values() -> None:
    assert validate_entries(None) == ([], [])
    kept, problems = validate_entries(["-a", 3, "", "!", " +b "])
    assert kept == ["-a", "+b"]
    assert len(problems) == 3
    assert validate_entries("-a") == ([], ["expected a list of strings, got str"])


def test_matches_any_for_unknown_entry_warnings() -> None:
    targets = [skill("pdf"), skill("deploy")]
    assert matches_any("-pdf", targets, BASE)
    assert matches_any("!dep*", targets, BASE)
    assert not matches_any("-pdff", targets, BASE)
    assert not matches_any("!zzz*", targets, BASE)


def test_source_path_resolves_against_the_files_base(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox_home(monkeypatch, "/home/u")
    assert source_path("~/.claude/skills", BASE) == os.path.normpath("/home/u/.claude/skills")
    assert source_path("extra", BASE) == os.path.normpath(f"{BASE}/extra")


def test_a_backslash_entry_names_the_same_path() -> None:
    """A file written on Windows keeps working on POSIX (and on the Windows leg)."""

    assert evaluate(skill("pdf"), ["-skills\\pdf"], BASE) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_windows_paths() -> None:  # pragma: no cover — runs on the Windows CI leg
    base = "C:\\Users\\u\\.aelix\\agent"
    target = Target(("pdf",), f"{base}\\skills\\pdf\\SKILL.md")
    assert evaluate(target, ["-skills\\pdf"], base) is False
    assert evaluate(target, ["-skills/pdf"], base) is False
    assert evaluate(target, ["!**/pdf"], base) is False
    assert evaluate(target, [f"-{base}\\skills\\pdf"], base) is False
    # Another drive has no relative form; the absolute one still matches.
    assert evaluate(target, ["-skills/pdf"], "D:\\elsewhere") is None


# --- C15: a path reaches only its own file's scope; names reach everything ---------


GLOBAL_SKILL = Target(("pdf",), f"{BASE}/skills/pdf/SKILL.md", "global")
PROJECT_BASE = "/work/app/.aelix"
PROJECT_SKILL = Target(("conv",), f"{PROJECT_BASE}/skills/conv/SKILL.md", "project")


def test_a_project_path_entry_cannot_reach_a_global_skill() -> None:
    project = ScopeList(
        "project", (f"-{BASE}/skills/pdf", "-../../../home/u/.aelix/agent/skills/pdf"), PROJECT_BASE
    )
    assert effective(GLOBAL_SKILL, None, project) == (True, None)
    # ... while its NAME does, by design: the project file decides for this project.
    assert effective(GLOBAL_SKILL, None, ScopeList("project", ("-pdf",), PROJECT_BASE)) == (
        False,
        "project",
    )


def test_a_global_path_entry_cannot_reach_a_project_skill() -> None:
    global_ = ScopeList("global", (f"-{PROJECT_BASE}/skills/conv", "!/work/app/**"), BASE)
    assert effective(PROJECT_SKILL, global_, None) == (True, None)
    assert effective(GLOBAL_SKILL, ScopeList("global", (f"-{BASE}/skills/pdf",), BASE), None) == (
        False,
        "global",
    )


def test_a_name_glob_still_reaches_across_scopes() -> None:
    global_ = ScopeList("global", ("!con*",), BASE)
    assert effective(PROJECT_SKILL, global_, None) == (False, "global")


def test_edit_never_removes_an_entry_it_could_not_have_matched() -> None:
    entries = [
        f"-{BASE}/skills/pdf"
    ]  # a path in the PROJECT file: it never reached the global skill
    assert edit(entries, GLOBAL_SKILL, True, False, PROJECT_BASE, list_scope="project") == [
        f"-{BASE}/skills/pdf",
        "+pdf",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlinked_skill_matches_either_spelling(tmp_path: Path) -> None:
    real = tmp_path / "real" / "pdf"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    via_link = Target(("pdf",), str(tmp_path / "link" / "pdf" / "SKILL.md"), "global")
    base = str(tmp_path)
    assert evaluate(via_link, [f"-{real}"], base, list_scope="global") is False
    assert evaluate(via_link, ["-link/pdf"], base, list_scope="global") is False


def test_duplicate_names_share_their_identity_entries() -> None:
    winner = Target(("dup",), f"{BASE}/skills/dup/SKILL.md", "global")
    loser = Target(("dup",), "/pkg/skills/dup/SKILL.md", "bundled")
    global_ = ScopeList("global", ("-dup",), BASE)
    assert effective(winner, global_, None)[0] is False
    assert effective(loser, global_, None)[0] is False
    assert matches_any("-skills/dup", [winner, loser], BASE, list_scope="global")


# --- X6: the glob is minimatch's, checked against minimatch itself ---------------


def _minimatch_cases() -> list[tuple[str, str, bool]]:
    """Every (value, pattern) of a corpus run through minimatch 10.2.6, pi's pinned copy.

    Regenerate with that version's ``minimatch(value, pattern)`` (no options, as
    pi calls it in ``package-manager.ts``) when the corpus changes; the JSON
    records each pattern's matching values, so every other value is a miss.
    """

    data = json.loads((Path(__file__).parent / "minimatch_corpus_511.json").read_text("utf-8"))
    cases = []
    for corpus in data["corpora"]:
        for pattern, hits in corpus["matches"].items():
            for value in corpus["values"]:
                cases.append((value, pattern, value in hits))
    return cases


def test_glob_matches_agrees_with_minimatch_on_the_whole_corpus() -> None:
    cases = _minimatch_cases()
    assert len(cases) > 5900
    wrong = [(v, p, want) for v, p, want in cases if glob_matches(v, p) is not want]
    assert wrong == []


@pytest.mark.parametrize(
    ("value", "pattern"),
    [
        ("skill2", "skill{1..3}"),
        ("skill02", "skill{01..03}"),
        ("foo", "@(foo|bar)"),
        ("bar.md", "*.@(md|ts)"),
        ("foobaz", "foo!(bar)"),
        (".env", "@(.env|x)"),
    ],
)
def test_the_syntax_the_review_named_now_matches(value: str, pattern: str) -> None:
    assert glob_matches(value, pattern)


# --- round 2: a trailing ``**``, and globs that cannot stall a launch -----------


@pytest.mark.parametrize(
    ("value", "pattern", "want"),
    [
        ("pdf-tools", "pdf-tools/**", False),
        ("pdf-tools/SKILL.md", "pdf-tools/**", True),
        ("a", "a/**", False),
        ("a", "**/a/**", False),
        ("x/a", "**/a/**", False),
        ("x/a/b", "**/a/**", True),
        ("a/b", "a/**/b", True),
        ("a/b", "**/**", True),
    ],
)
def test_a_trailing_globstar_needs_a_segment_as_in_minimatch(
    value: str, pattern: str, want: bool
) -> None:
    """R1: minimatch's ``#matchGlobstar`` — a pattern ending in ``/**`` must
    have *something* after its head; a ``**`` with more pattern after it still
    takes zero segments."""

    assert glob_matches(value, pattern) is want


#: The verifier's slow cases (round-1 delta, ``redos_cmp.py``) and wider ones:
#: round 1's regex took from 0.1 s to minutes on each; a pattern past the brace
#: limit is refused instead of expanded.
_ADVERSARIAL = r"""
import json, sys, time
from aelix_coding_agent.core.resource_filter import glob_matches
seg = "pdf-tools-for-everything-else"
abs12 = "/Users/someone/.aelix/agent/skills/team/shared/tools/" + seg + "/SKILL.md"
cases = [
    ("a" * 64, "*a*a*a*a*a*a*a*a*b"), ("a" * 30, "*a*a*a*a*a*a*a*a*a*a*a*a*b"),
    (seg, "*a*b*c*d*e*f*"), (seg, "*-*-*-*-*-*-*x"), (seg, "*e*e*e*e*e*x"),
    (abs12, "**/*e*e*e*e*e*x"), (seg, "*(*(?))x"), (seg, "+(?)x"), (seg, "*(?)*(?)x"),
    (seg, "*(*)x"), (seg, "+(*-)x"), (seg, "@(*|*)@(*|*)@(*|*)x"), ("a" * 30, "*(*(a))b"),
    ("a" * 30, "+(+(a))b"), ("a" * 64, "*(a|aa)b"), (abs12, "**/**/**/**/x"),
    (abs12, "**/**/**/**/**/**/x"), ("/".join(["a"] * 40), "**/**/**/**/**/**/**/x"),
    ("a", "{a,b}" * 16), ("a", "{1..100000}"), ("skill5", "skill{1..10000000}"),
    ("a", "{1..1000}{1..1000}"),
]
for n in (64, 200):
    a = "a" * n
    cases += [(a, p) for p in (
        "*(a|aa)b", "+(a|aa)b", "*(*(*(*(a))))b", "@(*|*)@(*|*)@(*|*)@(*|*)b",
        "!(!(!(b)))c", "!(*)!(*)!(*)b", "*(?)*(?)*(?)b", "*!(b)*!(b)*!(b)c",
        "*(+(a|b)|*(a|c))d", "[a]*[a]*[a]*[a]*[a]*[a]*b",
    )]
times = []
for value, pattern in cases:
    start = time.perf_counter()
    glob_matches(value, pattern)
    times.append([pattern, len(value), time.perf_counter() - start])
print(json.dumps(times))
"""


def test_adversarial_globs_answer_in_milliseconds_not_minutes() -> None:
    """M1: in a child process, so a regression fails on the timeout rather than
    hanging the run. Measured at most 11 ms a case on the author's machine; the
    bound is a hundred times that."""

    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_ADVERSARIAL)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    times = json.loads(done.stdout)
    assert len(times) == 42
    slow = [(pattern, n, round(t, 3)) for pattern, n, t in times if t > 1.0]
    assert slow == []


def test_a_glob_past_the_limits_is_refused_and_warned_not_evaluated() -> None:
    assert glob_problem("pdf-*") is None
    assert glob_problem("skill{1..256}") is None
    problem = glob_problem("skill{1..10000000}")
    assert problem is not None and "256" in problem
    assert glob_problem("{a,b}" * 16) is not None
    assert glob_problem("*{" + ",".join(f"x{i}" for i in range(40)) + "}") is not None
    assert glob_problem("x" * 600) is not None
    assert glob_problem("@(" * 20 + "a" + ")" * 20) is not None
    # matches nothing — not even through minimatch's own (second) negation
    assert glob_matches("skill5", "skill{1..10000000}") is False
    assert glob_matches("skill5", "!skill{1..10000000}") is False
    kept, problems = validate_entries(["!skill{1..10000000}", "!pdf-*", "-skill{1..10000000}"])
    assert kept == ["!pdf-*", "-skill{1..10000000}"]
    assert len(problems) == 1
    assert problems[0].startswith("'!skill{1..10000000}' is too complex to match: ")


def test_literal_brace_alternatives_are_a_lookup() -> None:
    """``skill{1..256}`` expands to plain names: no glob is matched for them."""

    assert glob_matches("skill200", "skill{1..256}")
    assert not glob_matches("skill257", "skill{1..256}")
    assert glob_matches("a/b", "{a/b,c}")
