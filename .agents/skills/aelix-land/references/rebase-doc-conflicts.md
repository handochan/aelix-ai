# Rebase and merge conflicts in docs and the citation lock

Branches that land one after another conflict in the same few places every time.
In a rebase, "upstream" is the commit you are rebasing onto. Git calls it HEAD, or
"ours", while it replays your commits. Your branch's side is "theirs".

## ADR `Status:` line

Each issue that amends an ADR adds its own `Amended <date> (#n): ...` clause, or an
`**#n amendment <date>** — ...` clause inside the parenthesis. Take the upstream line
and append your branch's clause, in landing order. Never drop either side's clause.

## ADR body sections

"What stays open" bullets and "Consequences" paragraphs: keep both sides, in landing
order. The exception is when the later issue's text rewrites the earlier one's, and
its design says so. Then take the later text.

## The ADR index row (`docs/decisions/README.md`)

One ADR's entry is one paragraph, and each issue splices its own clause into it.
Prefix or suffix splicing duplicates clauses. It has produced the same clause twice,
in two separate batches. Do a token-level three-way merge instead:

1. Base = the commit your branch was cut from, not `main`'s current tip.
2. Split base, ours and theirs into words.
3. Take the `difflib.SequenceMatcher` opcodes of base→ours and of base→theirs.
4. Apply the edits that do not overlap. Concatenate inserts at the same point, ours
   then theirs.
5. Read the result. Expect to need this whenever three or more issues touched one row.

## CHANGELOG `[Unreleased]`

Keep both entries, with a blank line between them, in the section each one belongs in.

## Commit messages

A rebase re-edits each message through git's comment stripping, so lines starting with
`#` vanish. Before rebasing, save each message (`git log --format=%B -1 <sha>`). After,
diff them and restore what was lost with
`git commit --amend --cleanup=verbatim -F <file>`. Write that file as
`text.rstrip() + "\n"`: `verbatim` keeps trailing blank lines, and the trailer must
stay the last line. Better still, never start a message line with `#`.

## The citation lock across a rebase

Per replayed commit:

1. **Number-only hunks** (a comment or test that differs only in a cited line number):
   keep the upstream side.
2. **`citations.lock.json`**: take the upstream side whole. It is valid JSON under
   `anchors`.
3. **`git add` every resolved file** before running any gate. Unmerged paths appear
   three times in `git ls-files`, and the gate then counts them three times.
4. **`uv run python scripts/check_citations.py --fix`**, using the worktree's own
   script by its relative path. It relocates your branch's citations and locks your
   branch's new anchors.
5. **If it prints "N new citation(s) had no anchor; they are locked as written", stop.**
   That happens on a line where upstream changed one citation and your branch another.
   The sound merge of a one-citation hunk loses your number. Merge both numbers by
   hand, read each cited line, and rerun `--fix`.
6. **Check the result yourself.** Run `git diff citations.lock.json`, and confirm the
   changed keys are the ones you just resolved. Then run `--check` and
   `tests/test_citation_drift.py`.

Test the gate's own exit code: in `if ! cmd | tail -1`, the `if` tests `tail`.

## The citation lock across a merge of `main` into a branch

A merged tree has two parents, and one lock cannot describe both: a number can mean
different lines on the two sides. Taking `main`'s lock and running `--fix` once moved
two citations onto the wrong code, and `--check` was green (#654).

1. Resolve number-only hunks to your side, take `main`'s `citations.lock.json`, and
   `git add` everything.
2. Re-derive each citation from the lock of the side its citing line came from. For
   each one, find which parent the citing line came from, read the cited text in that
   parent, and find that text in the merged file.
3. Run `--lock` to pin the result. Only then make further code edits, followed by
   `--fix`.
4. Before pushing, audit every citation: each one must land on the same text it landed
   on in one of the two parents. Read by hand the ones that carry two numbers on one
   line.

Prefer a rebase, before anything is pushed, whenever the branch allows it.
