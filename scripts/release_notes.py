#!/usr/bin/env python3
"""Compose the GitHub Release body for a tag, and keep it under GitHub's size limit.

release.yml's ``github-release`` job runs this before ``gh release create`` / ``gh release
edit --notes-file``. Stdlib only, Python 3.11+, run as ``python3 -I``.

The body is what that job always sent (#111 B-6):

1. the CHANGELOG.md section for exactly this version: tag ``vX.Y.Z[-pre]`` reads the heading
   ``## [X.Y.Z[-pre]]``, matched up to and including the closing bracket, so ``v0.1.0``
   never absorbs ``## [0.1.0-beta.1]``; the section stops at the next ``## `` heading, keeps
   its ``### `` subheadings and drops its leading blank lines;
2. a blank line, then GitHub's generated "What's Changed" for the previous tag, when the
   job fetched one (``--generated``).

GitHub refuses a Release body over 125,000 characters ("body is too long (maximum is 125000
characters)"), and ``## [0.1.0]`` alone is over 460,000. Without a cut, ``github-release`` fails,
so ``publish`` never starts and nothing is released. So a body over :data:`LIMIT` is cut:

* at an entry boundary: before a line that starts a list item (``- ``, ``* ``) or a heading
  (``## ``, ``### ``) at the left margin, outside a fenced code block, so the body ends on a
  complete entry;
* at the last such boundary that leaves room for :func:`trailer`, a closing line that links
  to the full CHANGELOG.md at the tag and, when there is a previous tag, to the compare
  view, so the cut body, closing included, is at most :data:`LIMIT`; the generated part, if
  the cut reaches it, is what is dropped first;
* never inside the section's ``### Known limitations`` block. A cut that would land in it,
  or before it, is refused (exit 1, nothing written), so no Release goes out without that
  block: put it at the top of the section, where the cut cannot reach it.

A body at or under :data:`LIMIT` passes through byte for byte. Sizes are counted in UTF-8
bytes, which is never less than the characters GitHub counts, however it counts them. A
line ends at ``\\n`` and only there, as in the awk this replaced; the section drops a ``\\r``
before it.

Usage (release.yml)::

    python3 -I scripts/release_notes.py --changelog CHANGELOG.md --tag v0.1.0 \\
        --repo handochan/aelix-ai --previous-tag v0.1.0-beta.2 \\
        --generated generated.md --out release-notes.md

``--previous-tag`` may be empty (a first release), and ``--generated`` may be an empty file.
Exit 0 with ``--out`` written (possibly empty, when there is neither a section nor generated
notes; the job then falls back to unscoped generated notes); exit 1 when the cut would drop
the Known limitations block or no entry fits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

#: GitHub refuses a Release body over 125,000 characters; this leaves room for a
#: miscount and keeps the cut well clear of it.
LIMIT = 120_000

#: The heading of the block that must reach the Release page whatever the cut.
KNOWN_LIMITATIONS = "### Known limitations"

_HEADINGS = ("## ", "### ")
_BOUNDARY_PREFIXES = ("- ", "* ", *_HEADINGS)
_FENCES = ("```", "~~~")


class CutError(Exception):
    """The body cannot be cut under the limit without losing what must survive."""


def _lines(text: str) -> list[str]:
    """``text`` split after each ``\\n``, and only there, each line keeping its ``\\n``.

    Not :meth:`str.splitlines`, which also breaks at U+2028, U+0085, a form feed and
    more, so a ``## `` after one of those would end a section that awk kept going.
    """

    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def changelog_section(changelog: str, version: str) -> str | None:
    """The ``## [version]`` section, as release.yml's awk extracted it, or ``None``.

    ``None`` when the heading is missing or its section holds nothing; leading blank lines
    are dropped (the job's ``sed '/./,$!d'``), and every line keeps its own newline.
    """

    header = f"## [{version}]"
    found = False
    kept: list[str] = []
    for line in _lines(changelog):
        if line.startswith("## "):
            if found:
                break
            if line.startswith(header):
                found = True
            continue
        if found:
            kept.append(line.rstrip("\r\n") + "\n")
    while kept and not kept[0].strip():
        kept.pop(0)
    if not found or not kept:
        return None
    return "".join(kept)


def compose(section: str | None, generated: str) -> str:
    """Part 1, a blank line, part 2: what the job's shell wrote before the cut existed."""

    body = section or ""
    if body and generated:
        body += "\n"
    return body + generated


def trailer(repo: str, tag: str, previous_tag: str) -> str:
    """The closing paragraph of a cut body: where the rest of the notes are."""

    changelog = f"https://github.com/{repo}/blob/{tag}/CHANGELOG.md"
    text = (
        f"*These notes stop after the last entry that fits GitHub's limit for a Release body."
        f" The full notes are [`CHANGELOG.md` at `{tag}`]({changelog})"
    )
    if previous_tag:
        compare = f"https://github.com/{repo}/compare/{previous_tag}...{tag}"
        text += f", and every change since `{previous_tag}` is in [the compare view]({compare})"
    return text + ".*\n"


class Fitted(NamedTuple):
    body: str
    cut: bool
    kept_bytes: int
    total_bytes: int


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _boundaries(body: str) -> tuple[list[int], tuple[int, int] | None]:
    """Offsets where a cut may fall, and the Known limitations block's (start, end).

    The block is the first ``### Known limitations`` heading outside a fence, up to the
    next ``## `` or ``### `` heading outside a fence, or to the end of the body.
    """

    offsets: list[int] = []
    start: int | None = None
    end: int | None = None
    fence: str | None = None
    position = 0
    for line in _lines(body):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith(_FENCES):
            fence = stripped[:3]
        elif line.startswith(_BOUNDARY_PREFIXES):
            offsets.append(position)
            if line.startswith(_HEADINGS):
                if start is None:
                    if line.startswith(KNOWN_LIMITATIONS):
                        start = position
                elif end is None:
                    end = position
        position += len(line)
    if start is None:
        return offsets, None
    return offsets, (start, len(body) if end is None else end)


def fit(body: str, *, repo: str, tag: str, previous_tag: str, limit: int = LIMIT) -> Fitted:
    """``body`` unchanged when it fits ``limit``; otherwise cut at an entry boundary."""

    total = _size(body)
    if total <= limit:
        return Fitted(body, False, total, total)
    # Everything after the kept text: its own line end, a blank line, the rule and the
    # trailer. ``room`` is what the kept text, without its trailing whitespace, may take,
    # so ``kept`` below is at most ``limit`` bytes.
    closing = "\n\n---\n\n" + trailer(repo, tag, previous_tag)
    room = limit - _size(closing)
    offsets, block = _boundaries(body)
    cut_at = None
    for offset in offsets:
        if offset > 0 and _size(body[:offset].rstrip()) <= room:
            cut_at = offset
    if cut_at is None:
        raise CutError(f"no entry boundary leaves the body under {limit} bytes")
    if block is not None and cut_at < block[1]:
        raise CutError(
            f"the cut at byte {_size(body[:cut_at])} would drop the '{KNOWN_LIMITATIONS}'"
            f" block (it ends at byte {_size(body[: block[1]])}); move it to the top of the"
            " section or shorten it"
        )
    kept = body[:cut_at].rstrip() + closing
    return Fitted(kept, True, _size(kept), total)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compose the GitHub Release body for a tag, under GitHub's size limit."
    )
    parser.add_argument("--changelog", required=True, type=Path)
    parser.add_argument("--tag", required=True, help="the release tag, e.g. v0.1.0")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--previous-tag", default="", help="empty for a first release")
    parser.add_argument("--generated", type=Path, help="GitHub's generated notes, if any")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=LIMIT)
    args = parser.parse_args(argv)

    version = args.tag[1:] if args.tag.startswith("v") else args.tag
    section = changelog_section(args.changelog.read_text(encoding="utf-8"), version)
    if section is None:
        print(
            f"::warning::CHANGELOG.md has no '## [{version}]' section; the release notes will"
            " carry only the generated commit log. Add the section and edit the release."
        )
    else:
        print(f"Using the CHANGELOG.md section for [{version}].")
    generated = (
        args.generated.read_text(encoding="utf-8") if args.generated is not None else ""
    )
    try:
        fitted = fit(
            compose(section, generated),
            repo=args.repo,
            tag=args.tag,
            previous_tag=args.previous_tag,
            limit=args.limit,
        )
    except CutError as exc:
        print(f"::error::The release notes cannot be cut under {args.limit} bytes: {exc}.")
        return 1
    if fitted.cut:
        print(
            f"The release notes were {fitted.total_bytes} bytes; cut at an entry boundary to"
            f" {fitted.kept_bytes} (limit {args.limit}), ending on a link to CHANGELOG.md."
        )
    # Bytes, so no platform's text mode turns a ``\n`` into ``\r\n``.
    args.out.write_bytes(fitted.body.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
