# 0230. The feed we own, the command that uninstalls you, and a catalog nobody was checking

Status: Accepted (2026-08-19).
Date: 2026-08-19
Relates: ADR-0203 (no new environment names), ADR-0218 (the guides that ship inside
the wheel), ADR-0224 (text-pinned citations), ADR-0229 (the gate that was pointed at
the wrong thing).
GitHub: #172 (partially — the pipeline stays open). The update notifier had no issue.

Two tracks before the beta tag: tell a user when a newer release exists, and stop
`models_generated.json` and its guide from being wrong in ways nothing measured.

## The update check speaks; it does not act

The owner asked for a wizard that performs the upgrade. This ships the half that is
safe to ship now — one line naming the version and the command — and defers the
performing half to beta.2, for a reason that is measured rather than cautious.

**A running process cannot be replaced under itself here.** 376 of this tree's imports
are deferred, 304 of them unguarded, and 38% of shipped modules are still unloaded when
the launch settles. A post-swap import loads NEW code into the OLD process, and
`importlib.metadata.version()` flips mid-process while it does. All 43 post-swap
imports probed still succeeded, so the drift never announces itself — which is the
problem, not the reassurance. Doing this properly means a re-exec, and `os.execv` from
inside a live `prompt_toolkit` app leaves the pty at lflag 2608 — ECHO, ICANON and ISIG
all off, so Ctrl+C is dead in whatever runs next. The correct order is `app.exit()`,
wait for `run_async()` to return, then `execv` (lflag 35387). That is beta.2's work.

### Why the feed is a file we publish, not the releases API

Three measurements, each independently disqualifying:

* `/releases/latest` **excludes prereleases**. For a repository whose only releases are
  betas — this one, for the whole beta — it 404s.
* The list endpoint is ordered by **publish time, not version**, so `[0]` is not the
  newest release. zed's most recent 100 releases contain 21 inversions.
* The anonymous limit is **60/hour per IP, and a 304 still spends one**. GitHub's docs
  say conditional requests do not count; that is true only when authenticated. Behind a
  corporate NAT that budget is shared by everyone in the building.

So `site/latest-version.json` on this project's own Pages, written by the release
ritual and gated by `tests/test_latest_version_feed.py` against the version the repo
actually declares. pi solves it the same way, for what is presumably the same reason.

### The comparator is not optional, and it was not declared

`0.1.0b1` and `v0.1.0-beta.1` are the same release spelled two ways. A string compare
is wrong in four of the eight orderings that matter, and one of those errors is
permanent: the user is told to upgrade to what they already have, on every launch,
forever. That is pi's own fallback bug in `version-check.js`, which does not bite pi
because its tag spelling happens to match its version spelling.

`packaging` supplies the comparator. It was not declared by any package in this repo —
it was merely PRESENT, because pytest depends on it. An `install.sh` user would have
got a module whose guarded import failed, which answers "no opinion" to every
comparison and never speaks. Deleting the declaration left 89 tests green.

**And `tests/packaging/` was shadowing the distribution.** pytest puts the rootdir on
`sys.path` and that directory had an `__init__.py`, so `import packaging` succeeded
inside the session while `packaging.version` did not — the shape a `try: import x`
availability probe answers "yes" to. It is `tests/packaging_gate/` now. The lesson
generalises past this feature: a test package named after a distribution shadows it for
everything in the session.

### Why the upgrade command is detected rather than printed

There is no universal upgrade command for this product, and the plausible one is
destructive. `uv tool install aelix@latest` — which **uv itself suggests** — resolves
the PyPI name reservation, finds no entry points, and removes the tool. The user's
aelix is gone. `pip install -U aelix` lands in the same place for the same reason.

So the method is read off the installation: `uv-receipt.toml` (and its contents
separate an `install.sh` install, which pins `==` and passes `--find-links`, from a
plain `uv tool install`), `pipx_metadata.json`, PEP 610 `direct_url.json` for a
checkout. A checkout and an unrecognised install get the release link and **no
command**, because a guess here uninstalls someone.

One detail cost a measurement: a non-editable local-directory install writes
`"dir_info": {}` with no `editable` key at all, so indexing it raises `KeyError`.

### What it sends, and the two ways to turn it off

A plain GET. No version, no operating system, no identifier — the server learns an IP
address and nothing else. There is no telemetry sink to build later, because there is
nothing to receive. Off via `/settings` → *Check for updates*, or `--offline` /
`PI_OFFLINE`. **No new environment variable** (ADR-0203: every `os.environ` read is a
consumer a hostile cwd `.env` may try to drive).

Every failure is silent, including the ones that look like bugs. A startup complaint
about a failed *check* is worse than no check.

## #172, narrowed to what did not need a pipeline

The issue asks for a refresh pipeline. The measurement that decided against building it
now: `models_generated.json` is **not a snapshot of upstream, it is a corrected fork**
— 41 hand edits across six commits, with the rationale in commit bodies and nowhere
else. A pi-faithful regeneration reverts 19 of 32 corrections and drops 376 of 847
runnable models. The generator is a real project; it is not a beta blocker.

What was fixed instead:

**The guide broke the file it documents.** Two of its three `models.json` examples
carried a `cost` with only `input` and `output` while the validator requires all four
keys — and `load_custom_models` answers a schema error with
`empty_custom_models_result`, so a user who copy-pastes the documented example loses
their **whole** `models.json`. The guide ships inside the wheel. The prose had the rule
backwards too (it said `cost` was required; omitting it succeeds and a *partial* `cost`
is the error), and the page now states what silence buys you — a minimal entry yields
an 8x-too-small context window, reasoning off, and no image support, with no warning.

A probe of mine got this wrong first: reading `res.errors` from `load_custom_models`
said "three examples, all fine". The real validator is `validate_models_config`, and it
rejected two.

**Four flagships were missing** — `claude-opus-5` on `anthropic` and `github-copilot`,
`gemini-3.6-flash` and `gemini-3.7-flash` on `google`. 102 lines of pure addition; the
file is not sorted (github-copilot never was), so a blanket re-sort moved 142 lines and
was reverted for an in-place insertion beside each sibling.

**The corrections now have tests.** Reverting all fourteen `openai-codex` context
windows to the stale 272000 that `f422ae9` explicitly corrected left the full suite
green — nothing checked. What is pinned is only what this repo chose that DIFFERS from
upstream and is user-visible: the codex windows, the canonical-provider values, the
Copilot 200K normalisation, and the zero cost of a Copilot seat (upstream prices
`claude-opus-4.8` at 5.0/25.0; a seat is a subscription and `/cost` would report money
nobody spent). Adding a model does not belong there.

The anthropic p25 output ceiling was **re-derived, not nudged**: the two new
`anthropic-messages` rows land above the quartile, so `sat[57]` is still 32000. The
margin is two rows, and the assertion is left tight on purpose.

## The sabotage round is the part that changed the work

27 sabotages, 19 RED on the first pass. The catalog and the guide were fully gated. The
notifier had **seven real holes, and they were in the promises the feature makes
loudest**:

* the byte cap, the HTTPS-only redirect handler, and the check on the URL the response
  actually came from — all three asserted in prose, in this ADR's own source material,
  and by nothing executable. Removing all three left 38 tests green;
* `uv tool upgrade aelix` could be edited into the command that uninstalls the user
  with every test passing;
* `packaging` undeclared, above.

Two are worth generalising.

**An exception is not a sentinel inside a function that swallows exceptions.**
`test_a_source_tree_run_is_never_notified` used a `fetch` that raised to mean "must not
be called". `check_for_update` catches everything `fetch` throws — that IS its contract
— so with the guard deleted the fetch ran, the complaint was swallowed, and the
function returned `None` anyway. Green, for the wrong reason. It counts calls now.

**A layered defence has to be broken one layer at a time.** `check_for_update` never
raising and `_commit_update_notice` swallowing anything that does are two separate
promises. Turning the launch path's `except` into `raise` left all seven TUI tests
green, because the inner layer caught everything first. The test that measures the
outer layer has to make the TASK explode.

The eighth green was a weak sabotage of mine, not a hole: it edited the settings row's
label, and the row's key and read callable are both gated. Removing the row is RED.

## What this does not do

* No performing of upgrades (beta.2), and no signature on the feed (GA). The feed is
  advisory: worst case a hostile response names a version that does not exist and the
  user's package manager fails to find it. Nothing is downloaded or executed here.
* No refresh pipeline for the catalog. #172 stays open.
* `starlette` is imported directly by `aelix-server` while only `fastapi` is declared.
  Recorded in the new dependency gate's exemption list with the reason rather than
  fixed silently — it is a change to a shipped package's dependency list.
