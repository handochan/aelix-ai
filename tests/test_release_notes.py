"""The GitHub Release body fits GitHub's limit, ends on an entry, and keeps its Known limitations.

``release.yml``'s ``github-release`` job sends the CHANGELOG section for the tag, then
GitHub's generated "What's Changed", as the Release body. GitHub refuses a body over
125,000 characters; ``## [0.1.0]`` was 461,151 when the 0.1.0 prep measured it (beta.2's
section is 101,166). Over the limit ``gh release create --notes-file`` fails, so
``publish``, which needs ``github-release``, never starts. A manual run (the dry run) stops
before that job, so only a tag push would have shown it.

``scripts/release_notes.py`` composes the body as the step's shell did (awk + ``sed
'/./,$!d'`` + ``printf '\\n'``) and cuts one over its limit at an entry boundary, ending on a
link to the full CHANGELOG.md at the tag and the compare view. These rows run it on this
repository's own CHANGELOG.md, in process and as release.yml runs it (``python -I``).
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "release_notes.py"
RELEASE = REPO_ROOT / ".github" / "workflows" / "release.yml"
REPO = "handochan/aelix-ai"

#: GitHub's own limit for a Release body, in characters.
GITHUB_LIMIT = 125_000


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


notes = _load_script()


def _changelog() -> str:
    return (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _generated(previous: str, tag: str, prs: int) -> str:
    """A body shaped like GitHub's ``generate-notes`` output."""

    lines = ["## What's Changed"]
    lines += [
        f"* fix(core): change number {n} by @someone in https://github.com/{REPO}/pull/{n}"
        for n in range(1, prs + 1)
    ]
    lines += ["", f"**Full Changelog**: https://github.com/{REPO}/compare/{previous}...{tag}"]
    return "\n".join(lines) + "\n"


def _known_limitations(section: str) -> str:
    start = section.index(notes.KNOWN_LIMITATIONS)
    rest = section[start + len(notes.KNOWN_LIMITATIONS) :]
    following = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return section[start : start + len(notes.KNOWN_LIMITATIONS) + (following.start() if following else len(rest))]


# === the real sections ======================================================


def test_the_0_1_0_section_is_cut_under_the_limit_on_an_entry_boundary() -> None:
    section = notes.changelog_section(_changelog(), "0.1.0")
    assert section is not None
    body = notes.compose(section, _generated("v0.1.0-beta.2", "v0.1.0", 400))
    assert len(body) > GITHUB_LIMIT, "the row exists for a section over the limit"

    fitted = notes.fit(body, repo=REPO, tag="v0.1.0", previous_tag="v0.1.0-beta.2")
    assert fitted.cut
    out = fitted.body
    assert len(out.encode("utf-8")) <= notes.LIMIT < GITHUB_LIMIT
    assert len(out) < GITHUB_LIMIT

    # The kept text is the section's own prefix, and what follows it there is an entry.
    kept, _, closing = out.rpartition("\n---\n\n")
    kept = kept.rstrip("\n")
    assert section.startswith(kept)
    following = section[len(kept) :].lstrip("\n")
    assert following.startswith(("- ", "### ")), following[:80]

    # The closing line names the full notes at the tag and the compare view.
    assert f"https://github.com/{REPO}/blob/v0.1.0/CHANGELOG.md" in closing
    assert f"https://github.com/{REPO}/compare/v0.1.0-beta.2...v0.1.0" in closing
    assert out.endswith(".*\n")
    assert "## What's Changed" not in out, "the cut reached the section, so part 2 is dropped"


def test_the_0_1_0_known_limitations_block_opens_the_section_and_survives_the_cut() -> None:
    section = notes.changelog_section(_changelog(), "0.1.0")
    assert section is not None
    headings = re.findall(r"^### .*$", section, re.MULTILINE)
    assert headings[0] == notes.KNOWN_LIMITATIONS, headings[:3]
    block = _known_limitations(section)
    assert "Windows" in block and "experimental" in block
    listed = re.findall(r"^- #\d+:", block, re.MULTILINE)
    assert len(listed) > 30, "the block lists the open issues (tests/docs pins which)"

    body = notes.compose(section, _generated("v0.1.0-beta.2", "v0.1.0", 400))
    out = notes.fit(body, repo=REPO, tag="v0.1.0", previous_tag="v0.1.0-beta.2").body
    # Byte for byte: everything from the section's first line to the end of the block
    # opens the Release body, every listed line with it.
    through_block = section[: section.index(block) + len(block)]
    assert out.startswith(through_block)
    assert re.findall(r"^- #\d+:", out[: len(through_block)], re.MULTILINE) == listed


def test_a_section_that_fits_passes_through_unchanged() -> None:
    """beta.2's section, the largest one released so far, with a generated part."""

    section = notes.changelog_section(_changelog(), "0.1.0-beta.2")
    assert section is not None and len(section) > 100_000
    generated = _generated("v0.1.0-beta.1", "v0.1.0-beta.2", 60)
    body = notes.compose(section, generated)
    assert body == section + "\n" + generated

    fitted = notes.fit(body, repo=REPO, tag="v0.1.0-beta.2", previous_tag="v0.1.0-beta.1")
    assert not fitted.cut
    assert fitted.body == body



def test_the_0_1_1_section_fits_and_opens_with_its_known_limitations() -> None:
    """0.1.1, a patch on 0.1.0: its section is far under the limit, so the body is the
    section and the generated part byte for byte, Known limitations first, as RELEASING.md
    step 2 asks."""

    section = notes.changelog_section(_changelog(), "0.1.1")
    assert section is not None
    assert section.startswith("Cut as the tag `v0.1.1`")
    headings = re.findall(r"^### .*$", section, re.MULTILINE)
    assert headings[0] == notes.KNOWN_LIMITATIONS, headings[:3]
    body = notes.compose(section, _generated("v0.1.0", "v0.1.1", 60))
    fitted = notes.fit(body, repo=REPO, tag="v0.1.1", previous_tag="v0.1.0")
    assert not fitted.cut
    assert fitted.body == body
    assert _known_limitations(section) in fitted.body

# === the extraction release.yml's awk did ===================================


def test_a_line_ends_at_a_newline_only_and_drops_a_carriage_return() -> None:
    """As awk read the file, a last line with no newline gets one, and U+2028 or a form
    feed inside a line does not end it. Unlike awk, the section drops the ``\\r`` of a
    ``\\r\\n``: on ``crlf`` the base release.yml's awk and ``sed '/./,$!d'`` wrote
    ``\\r\\n- a\\r\\n- b\\n`` with macOS awk 20200816 and with Linux mawk 1.3.4, keeping
    every ``\\r`` and so a leading line that is only ``\\r``."""

    crlf = "# C\r\n\r\n## [1.0.0] - 2026-01-01\r\n\r\n- a\r\n- b"
    assert notes.changelog_section(crlf, "1.0.0") == "- a\n- b\n"
    odd = "## [1.0.0]\n\n- one\u2028## not a heading\n- two\x0c## nor this\n## [0.9.0]\n- old\n"
    assert notes.changelog_section(odd, "1.0.0") == (
        "- one\u2028## not a heading\n- two\x0c## nor this\n"
    )


def test_the_section_is_matched_with_its_closing_bracket() -> None:
    text = (
        "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-11-25\n\n\nfinal\n\n### Added\n\n- a\n"
        "## [0.1.0-beta.1] - 2026-08-20\n\nbeta\n"
    )
    assert notes.changelog_section(text, "0.1.0") == "final\n\n### Added\n\n- a\n"
    assert notes.changelog_section(text, "0.1.0-beta.1") == "beta\n"
    assert notes.changelog_section(text, "0.1") is None
    assert notes.changelog_section(text, "Unreleased") is None, "an empty section is none"
    assert notes.changelog_section(text, "9.9.9") is None


def test_compose_writes_what_the_shell_wrote() -> None:
    assert notes.compose("s\n", "g\n") == "s\n\ng\n"
    assert notes.compose(None, "g\n") == "g\n"
    assert notes.compose("s\n", "") == "s\n"
    assert notes.compose(None, "") == ""


# === the cut's rules ========================================================


def _entries(n: int, width: int = 200) -> str:
    return "".join(f"- **Entry {i}.** " + "x" * width + "\n  more text\n" for i in range(n))


def _closing(tag: str, previous: str) -> str:
    return "\n\n---\n\n" + notes.trailer(REPO, tag, previous)


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def test_a_body_of_exactly_the_limit_passes_through() -> None:
    body = "Intro.\n\n### Added\n\n" + _entries(20)
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(body))
    assert not fitted.cut and fitted.body == body
    assert notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(body) - 1).cut


def test_a_cut_body_never_exceeds_the_limit_by_a_byte() -> None:
    """Every limit across several boundaries: the closing line is counted in full."""

    body = "Intro.\n\n### Added\n\n" + "".join(f"- e{i} é " + "a" * (i % 7) + "\n" for i in range(60))
    floor = _size(_closing("v9.9.9", "v9.9.8")) + 8
    fitted_any = 0
    for limit in range(floor, _size(body)):
        try:
            fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=limit)
        except notes.CutError:
            continue
        fitted_any += 1
        assert fitted.kept_bytes == _size(fitted.body) <= limit, limit
    assert fitted_any > 100


def test_a_cut_that_fits_exactly_ends_on_the_block_and_drops_the_generated_part() -> None:
    """The last boundary that fits is taken even when it fills the limit to the byte,
    and the ``## `` heading of the generated part ends the Known limitations block."""

    section = "Intro.\n\n### Known limitations\n\n- #1: one.\n- #2: two.\n"
    generated = "## What's Changed\n* " + "x" * 3000 + "\n"
    body = notes.compose(section, generated)
    expected = section.rstrip() + _closing("v9.9.9", "v9.9.8")
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(expected))
    assert fitted.body == expected
    assert fitted.cut and fitted.kept_bytes == _size(expected)


def test_a_body_that_no_boundary_fits_is_refused() -> None:
    with pytest.raises(notes.CutError, match="no entry boundary"):
        notes.fit("- " + "z" * 5000 + "\n", repo=REPO, tag="v9.9.9", previous_tag="", limit=1000)
    with pytest.raises(notes.CutError, match="no entry boundary"):
        notes.fit("Intro. " * 300 + "\n\n- a\n", repo=REPO, tag="v9.9.9", previous_tag="", limit=1000)


def test_a_cut_inside_the_known_limitations_block_is_refused() -> None:
    """The block need not be the first heading; a cut that lands in it fails closed."""

    block = "### Known limitations\n\n" + "".join(f"- #{n}: a limit " + "k" * 80 + "\n" for n in range(40))
    body = (
        "Intro.\n\n### Highlights\n\n- h\n\n" + block + "\n### Added\n\n"
        + "".join(f"- e{i} " + "a" * 80 + "\n" for i in range(40))
    )
    limit = body.index(block) + len(block) // 2
    with pytest.raises(notes.CutError, match="would drop the '### Known limitations' block"):
        notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="", limit=limit)
    # Past the block, the same body cuts and keeps it whole.
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="", limit=body.index(block) + len(block) + 600)
    assert block in fitted.body


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_a_fence_closes_and_the_entries_after_it_are_boundaries(fence: str) -> None:
    body = (
        "Intro.\n\n### Added\n\n- **Fenced.** look:\n\n"
        f"{fence}\n- not an entry\n## nor a heading\n{fence}\n" + _entries(40, width=50)
    )
    prefix = body[: body.index("- **Entry 6.**")]
    expected = prefix.rstrip() + _closing("v9.9.9", "v9.9.8")
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(expected) + 10)
    assert fitted.body == expected


def test_an_indented_sub_bullet_is_not_a_boundary() -> None:
    body = "Intro.\n\n### Added\n\n" + "".join(
        f"- **Entry {i}.** text\n  - sub {i}a\n  - sub {i}b\n" for i in range(40)
    )
    prefix = body[: body.index("- **Entry 6.**")]
    expected = prefix.rstrip() + _closing("v9.9.9", "v9.9.8")
    # Room for "- **Entry 6.** text" too, but the cut keeps whole entries only.
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(expected) + 25)
    assert fitted.body == expected


@pytest.mark.parametrize("separator", ["\u2028", "\x0c", "\x85"])
def test_a_cut_lands_only_after_a_newline(separator: str) -> None:
    """U+2028, a form feed or U+0085 inside an entry's line does not start a boundary."""

    body = "Intro.\n\n### Added\n\n" + "".join(
        f"- **Entry {i}.** text{separator}- the same line " + "y" * 40 + "\n" for i in range(40)
    )
    prefix = body[: body.index("- **Entry 6.**")]
    expected = prefix.rstrip() + _closing("v9.9.9", "v9.9.8")
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=_size(expected) + 25)
    assert fitted.body == expected


def test_a_cut_never_lands_inside_a_fenced_block() -> None:
    fenced = "- **Fenced.** look:\n\n```\n- not an entry\n" + "y\n" * 300 + "```\n"
    body = "Intro.\n\n### Added\n\n" + _entries(5) + fenced + _entries(5)
    out = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=len(_entries(5)) + 900).body
    assert out.count("```") % 2 == 0, out


def test_the_known_limitations_block_is_never_cut() -> None:
    block = "### Known limitations\n\n" + "".join(f"- #{n}: a limit.\n" for n in range(200))
    body = "Intro.\n\n" + block + "\n### Added\n\n" + _entries(50)
    size = len(body.encode("utf-8"))
    out = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="", limit=size // 2).body
    assert block in out

    # A block placed where the cut reaches fails closed instead of going missing.
    late = "Intro.\n\n### Added\n\n" + _entries(50) + "\n" + block
    with pytest.raises(notes.CutError, match="Known limitations"):
        notes.fit(late, repo=REPO, tag="v9.9.9", previous_tag="", limit=len(late.encode("utf-8")) // 2)


def test_a_first_release_links_no_compare_view() -> None:
    body = "Intro.\n\n### Added\n\n" + _entries(50)
    out = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="", limit=3000).body
    assert "/compare/" not in out
    assert f"https://github.com/{REPO}/blob/v9.9.9/CHANGELOG.md" in out


def test_a_cut_that_reaches_the_generated_part_keeps_the_section() -> None:
    section = "Intro.\n\n### Added\n\n" + _entries(3)
    generated = _generated("v9.9.8", "v9.9.9", 400)
    body = notes.compose(section, generated)
    out = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="v9.9.8", limit=len(section) + 5000).body
    assert out.startswith(section)
    kept = out.rpartition("\n---\n\n")[0].rstrip("\n")
    assert body.startswith(kept) and body[len(kept) :].lstrip("\n").startswith("* ")


def test_the_limit_counts_utf8_bytes() -> None:
    """A Korean or em-dash entry counts its bytes, never fewer than its characters."""

    body = "Intro.\n\n### Added\n\n" + "".join(f"- 항목 {i} — " + "가" * 100 + "\n" for i in range(100))
    limit = len(body) + 10  # fits in characters, not in bytes
    fitted = notes.fit(body, repo=REPO, tag="v9.9.9", previous_tag="", limit=limit)
    assert fitted.cut and len(fitted.body.encode("utf-8")) <= limit


# === the script as release.yml runs it ======================================


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,
        check=False,
    )


def test_the_cli_writes_the_cut_body_for_the_real_0_1_0(tmp_path: Path) -> None:
    generated = tmp_path / "generated.md"
    generated.write_bytes(_generated("v0.1.0-beta.2", "v0.1.0", 400).encode("utf-8"))
    out = tmp_path / "notes.md"
    proc = _run(
        tmp_path,
        "--changelog", str(REPO_ROOT / "CHANGELOG.md"),
        "--tag", "v0.1.0",
        "--repo", REPO,
        "--previous-tag", "v0.1.0-beta.2",
        "--generated", str(generated),
        "--out", str(out),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Using the CHANGELOG.md section for [0.1.0]." in proc.stdout
    assert "cut at an entry boundary" in proc.stdout
    body = out.read_bytes()
    assert len(body) <= notes.LIMIT
    assert b"\r\n" not in body
    assert body.decode("utf-8").startswith("Cut as the tag `v0.1.0`")


def test_the_cli_warns_on_a_missing_section_and_fails_on_a_lost_block(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_bytes(b"# Changelog\n\n## [1.0.0] - 2026-01-01\n\n- a\n")
    out = tmp_path / "notes.md"
    proc = _run(tmp_path, "--changelog", str(changelog), "--tag", "v2.0.0", "--repo", REPO, "--out", str(out))
    assert proc.returncode == 0
    assert "::warning::CHANGELOG.md has no '## [2.0.0]' section" in proc.stdout
    assert out.read_bytes() == b"", "the job falls back to unscoped generated notes on an empty file"

    late = "# Changelog\n\n## [1.0.0] - 2026-01-01\n\n### Added\n\n" + _entries(60)
    late += "\n### Known limitations\n\n- #1: a limit.\n"
    changelog.write_bytes(late.encode("utf-8"))
    proc = _run(
        tmp_path,
        "--changelog", str(changelog), "--tag", "v1.0.0", "--repo", REPO, "--out", str(out),
        "--limit", "4000",
    )
    assert proc.returncode == 1
    assert "::error::" in proc.stdout and "Known limitations" in proc.stdout


# === release.yml calls it ===================================================


def _compose_step() -> dict[str, Any]:
    jobs = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]
    steps = [s for s in jobs["github-release"]["steps"] if str(s.get("name", "")).startswith("Compose release notes")]
    assert len(steps) == 1
    return steps[0]


def test_the_release_job_composes_its_body_with_the_script() -> None:
    run = _compose_step()["run"]
    assert "python3 -I scripts/release_notes.py" in run
    for flag in ("--changelog CHANGELOG.md", '--tag "$TAG"', '--previous-tag "$prev"', '--generated "$generated"', '--out "$notes"'):
        assert flag in run, flag
    assert "awk -v ver=" not in run, "one extraction, the tested one"
    assert run.index("generate-notes") < run.index("scripts/release_notes.py")
    assert 'echo "NOTES_FILE=$notes"' in run

    # The whole command, continuation lines joined: no --limit, so the script's own
    # limit, the one these rows measure, is the one the job uses.
    start = run.index("python3 -I scripts/release_notes.py")
    command = re.match(r"(?:[^\n]*\\\n)*[^\n]*", run[start:])
    assert command is not None
    assert shlex.split(command.group(0).replace("\\\n", " ")) == [
        "python3", "-I", "scripts/release_notes.py",
        "--changelog", "CHANGELOG.md",
        "--tag", "$TAG",
        "--repo", "$REPO",
        "--previous-tag", "$prev",
        "--generated", "$generated",
        "--out", "$notes",
    ]
