# Contributing to Aelix

Outside contributions are welcome, and this file exists because the first three
of them ran into things that were true about this repository but written down
nowhere. Everything below is here because it actually cost someone time.

Read `AGENTS.md` for the project's structure, invariants and conventions. This
file is only about the mechanics of getting a change reviewed and merged.

## Before you write code

**Comment on the issue before you start.** This matters here more than in most
repositories, because the maintainer often works an issue the same day it is
filed, and an issue's body is frequently a full design — helper name, module
path, and the call sites to change. That means two things:

- If an issue reads like a finished design, treat it as one that may already be
  in flight. Say you are taking it and wait for a reply before spending real
  time.
- It has already gone wrong twice. In #217 and #223 a contributor implemented an
  issue correctly and the maintainer landed an independent fix for the same
  issue within a day, so the contributor's work could not be used. Neither was
  anyone's fault, and both were avoidable by a one-line comment first.

Small, obviously-correct fixes (a typo, a broken link, a clearly wrong error
message) do not need this. Open the PR.

## Setting up

```bash
uv sync
```

That is enough. This section used to insist on `--all-packages`, because a bare
`uv sync` left `aelix-server` out and the suite then died at collection with
`ModuleNotFoundError: No module named 'aelix_server'`. #224 closed that by
listing `aelix-server` in the root's `dev` dependency group, so what `uv sync`
produces and what the tests need are the same environment again — measured on a
fresh copy of this tree, a bare `uv sync` installs all five workspace members.
CI runs `uv sync --all-packages`, a superset that is still safe to use.

The rule that replaces it: **a new workspace member nothing depends on goes in
that `dev` group**, or the next fresh checkout will be missing it.

Python 3.11+ is required; CI runs 3.11 and 3.12 on Ubuntu and Windows.

## The gates your PR has to pass

CI runs exactly these three, in this order. Run them locally before pushing:

```bash
uv run ruff check .                    # linter only — `ruff format` is NOT enforced
uv run python scripts/check_types.py   # the pyright gate, not a bare `uv run pyright`
uv run pytest -p no:cacheprovider -q   # the full suite, ~6 min on a laptop
```

`ruff format --check` is deliberately not enforced — the codebase relies on
hand-laid formatting that `ruff format` would collapse. Match the surrounding
code; do not reformat files you are not changing.

### Three gates that are easy to miss

**1. The citation lock.** Comments and tests in this repo cite source locations
by path and line number, and `citations.lock.json` pins the text each citation
pointed at. Any patch that *inserts or deletes lines* shifts every citation
anchored below it and fails `tests/test_citation_drift.py`, in files you never
touched. (Do not write a made-up path-and-line example in prose either — the
scanner reads those as real citations and they become permanently ungated.)
This has caught two of the three external PRs so far. The repair is one command:

```bash
uv run python scripts/check_citations.py --fix
```

Commit the result with your change. The diff is line numbers only.

**2. The docs bundle is duplicated.** `docs/guides/` also ships inside the wheel
at `packages/aelix-coding-agent/src/aelix_coding_agent/docs/`, and
`tests/test_docs_bundle_sync.py` gates the two copies as byte-identical
(ADR-0218). If you edit a guide, edit both copies.

**3. `.gitignore` and the `exclude` lists move together.** hatchling appends
`.gitignore` to its own exclude spec, so "it is ignored, therefore it will not
ship" does not hold. If you touch either, run `tests/packaging_gate/`.

### Tests are not optional here

The suite carries about 1.6 lines of test for every line of production code
(190k vs 121k, measured 2026-09-07) and every recent fix is pinned by a test. A
behaviour change without one will be asked for one. Make it a real test: revert
your production hunk and confirm the test goes red. A test that passes with and
without your change is documentation, not a gate.

Some paths cannot be unit-tested and have to be exercised for real:

- **TUI changes** — run `uv run aelix` and look at it. Rendering, colour,
  layout and key handling are not covered by unit tests.
- **Provider, streaming, session or tool-execution changes** — run against a
  real model, not a mock.

## Fork CI needs the maintainer to press a button

This is the thing no outside contributor could have known, and all three hit it.

Workflows on pull requests from forks do not start on their own. They sit as
`action_required` with **zero jobs** until the maintainer approves the run. Two
consequences:

- **A PR from a fork shows no checks at all until then.** That is the gate, not
  a problem with your branch.
- **An unapproved run expires after 30 days and flips to a red ✗** that has
  nothing to do with your code. One PR sat with a misleading red X for five
  weeks for exactly this reason.

You cannot approve it yourself; the endpoint needs admin. If a run is sitting
unapproved, say so in the PR — that is a legitimate and useful thing to post,
and it will not be read as nagging.

Fork CI here is safe to approve: `ci.yml` triggers on `pull_request` (never
`pull_request_target`) and references no secrets, so a fork run gets a read-only
token and no credentials.

## Claims, and what "it passes" means

Write down the command you ran and what it printed. If you did not measure
something, say you did not measure it. If a test fails, say it failed. A PR body
that says "all tests pass" and, two sentences later, "could not run the suite
locally" costs the reviewer more time than saying nothing would have.

This is not a formality — a maintainer scales on whether a contributor's claims
can be taken at face value, and that is the whole basis on which a review gets
shorter over time.

If you used an AI assistant, that is fine and common in this repository; say so,
and hold its output to the same standard. Numbers it reports that you did not
see yourself are not measurements.

## Commits and PRs

- One issue, one branch, one PR. Do not mix unrelated changes.
- Write commit messages that say *what you measured*, not just what you changed.
  `git log` here is the project's memory; skim a few before writing yours.
- Link the issue (`Fixes #123`).
- Do not force-push after a review has started unless asked — it makes the
  review comments hard to follow.

## What we do and do not promise

This is a small project with a single maintainer, so, in the same spirit as
`SECURITY.md`:

- There is **no response-time commitment**. Promising one that is not staffed
  would be its own kind of dishonesty, and the record does not support one.
- What we do commit to: **a PR will not be closed, superseded or left to rot
  without a reply.** If your work is overtaken by something landed directly, you
  will be told that, and told why. Every one of the first three external PRs
  failed this, which is why it is written here as an obligation rather than an
  aspiration.
- If a contributor's fix is used, they are credited as a co-author on the merge.

## Licence

By contributing you agree that your contributions are licensed under the same
terms as the project (see `LICENSE`).
