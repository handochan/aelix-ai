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

Python 3.11+ is required; CI runs 3.11, 3.12, 3.13 and 3.14 on Ubuntu and
Windows — every interpreter the installers can give a user of 0.1.0b3 or later
(`tests/test_ci_python_matrix.py` holds the matrix equal to that range,
`>=3.11,<3.15`). For 0.1.0b2 and earlier, which pin `openai<2.0`, the installers
keep `>=3.11,<3.14` (#278, ADR-0265).

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

### Four gates that are easy to miss

**1. The citation lock.** Comments and tests in this repo cite source locations
by path and line number, and `citations.lock.json` pins the text each citation
pointed at. Any patch that *inserts or deletes lines* shifts every citation
anchored below it and fails `tests/test_citation_drift.py`, in files you never
touched. (Do not write a made-up path-and-line example in prose either — the
scanner reads those as real citations and they become permanently ungated.)
This has caught two of the three external PRs so far. The repair starts with one
command:

```bash
uv run python scripts/check_citations.py --fix
```

Commit the result with your change; that diff is line numbers only.

`--fix` moves a citation only where it can still find the cited text in exactly
one place. If your patch *edited* a cited block rather than moving it, or the
text it cited now appears twice, there is no mechanical answer — so `--fix`
prints those citations by name, exits non-zero, and leaves the lock holding the
old text. **The gate stays red on purpose until you fix them, and that is the
end of the automatic part.** (It used to re-pin them against whatever line had
drifted into their place, which made the next run green over a citation nobody
had repaired; ADR-0248.)

For each one it names: open the file, find the construct the sentence is about,
and use that line number. Never add a delta to the old one. If the block was
edited where it stands — same lines, new words, so the number is still right —
`uv run python scripts/check_citations.py --lock` re-pins the text and prints
every anchor it replaced. Check that list before committing it.

**2. The docs bundle is duplicated.** `docs/guides/` also ships inside the wheel
at `packages/aelix-coding-agent/src/aelix_coding_agent/docs/`, and
`tests/test_docs_bundle_sync.py` gates the two copies as byte-identical
(ADR-0218). If you edit a guide, edit both copies.

**3. `.gitignore` and the `exclude` lists move together.** hatchling appends
`.gitignore` to its own exclude spec, so "it is ignored, therefore it will not
ship" does not hold. If you touch either, run `tests/packaging_gate/`.

**4. Imports only go down.** The packages stack as `aelix-ai` ←
`aelix-agent-core` ← `aelix-coding-agent` ← `aelix-server` and `aelix`
(ADR-0015): a package may import only itself and what its `pyproject.toml`
declares, directly or through one of those. So the kernel never imports
`aelix_coding_agent`, `aelix_agents` or `aelix_status`, and `aelix_ai` never
imports `aelix_agent_core`. That holds inside a function, under
`if TYPE_CHECKING:` and through `importlib.import_module("...")` with a literal
name too. `tests/test_import_direction_292.py` fails on a new one and names the
file and the statement; run the check alone with:

```bash
uv run python scripts/check_import_direction.py
```

The kernel still has 13 such imports from before the gate. They are listed in
that script's `ALLOWLIST`, and the tree must match it exactly: a new import
fails until it is listed, and a listed one that is gone fails until its entry
is removed. Do not list a new one: let the higher package pass in what the
kernel needs (a Protocol or a callback), or move the shared type down. The
gate cannot stop an edit to the list, so review does: ADR-0015 lets an entry
change only to follow a listed import that moved to another file or under
`TYPE_CHECKING` (change its file or kind), or to go away with its import
(delete it, or lower its count, in the same commit).

### Tests are not optional here

The suite carries about 1.6 lines of test for every line of production code
(190k vs 121k, measured 2026-09-07) and every recent fix is pinned by a test. A
behaviour change without one will be asked for one. Make it a real test: revert
your production hunk and confirm the test goes red. A test that passes with and
without your change is documentation, not a gate.

**Keep tests out of your home's `~/.aelix`.** You can run the suite with your
own HOME: nothing in it should open, list or change anything there.

Sessions: `tests/conftest.py` gives every
test its own `AELIX_CODING_AGENT_SESSION_DIR`, unless a module-, class- or
session-scoped fixture has set the variable to a non-empty value: that value
then reaches its tests, and stays in effect until the fixture is torn down. For
a session-scoped fixture that is not autouse, that is every test that runs
after it until the run ends, whether or not the test requests it. An empty
value is replaced like an unset one, since the CLI reads it as unset. Code in
the test process that reads the variable, and every child built from
`os.environ`, uses it. A `JsonlSessionRepo` built without `sessions_root` does
not read it; pass one of the test's own.

The agent dir (`~/.aelix/agent`: `auth.json`, `settings.json`, extensions,
skills) is not pinned. A test whose code resolves it must isolate it: sandbox
the home (`tests/env_sandbox.py` `sandbox_home`) when it runs the CLI
(`_async_main` loads `auth.json` before anything else), or put the module on the
`own_agent_dir` fixture (`pytestmark = pytest.mark.usefixtures("own_agent_dir")`)
when it builds harness options, which list and load `<agent dir>/extensions`.
Do not call `monkeypatch.undo()` while the code under test still runs: it
undoes every conftest pin too. Use `pytest.MonkeyPatch.context()` for a patch
you need to take back.

A guard watches the test process. Under `~/.aelix` (and an exported
`AELIX_CODING_AGENT_SESSION_DIR` or `AELIX_CODING_AGENT_DIR`) an open in any
mode, a directory listing, `mkdir`, rename, remove, `rmdir`, `chmod`, link,
symlink, truncate or `rmtree` is blocked, and the test fails with a `#458/#497`
message that says which fix applies. Do not turn the guard off. It is best
effort, not a proof: it does not see `os.stat` and what is built on it
(`Path.exists`), so a read the code makes only when a file exists fails on a
machine that has the file and passes on CI's empty home. Nor does it see a
rename of the home or, on POSIX, an `rmtree` of it, `touch` or `utime`, `chflags`, `mkfifo`, `sqlite3`, sockets, paths relative to a
directory descriptor, or a symlink into the root from outside it
(`tests/session_root_guard.py` lists what it judges and what it misses).
Children are not checked. A child whose environment you build by hand must
sandbox the home (`child_env`) or pass `--session-dir`. A shell, `env` or
`uv run` wrapper that drops the variable, and an `aelix-server` child (its
`/rpc` ignores the variable), would write to the real root unchecked.

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
