# 0240. A pre-release tag publishes to PyPI too

Status: Accepted (2026-09-08; owner decision for `v0.1.0-beta.2`)
Date: 2026-09-08
Supersedes/relates: ADR-0192 **owner decision 1** ("a hyphenated tag SKIPS PyPI
entirely … #73 is deferred until the first GA tag") — that sentence is
superseded here; the rest of ADR-0192 (GitHub Release as the checksum channel,
`install.sh`, the hyphen convention for `--prerelease`) stands. ADR-0147
(release engineering; the `publish` job this reopens).
Issue: #142 (GA blockers, item 1 — the pin assert; item 3 — the environment
gate this ADR makes exercisable before GA), #73 (Trusted Publishers, registered
by the owner on 2026-09-08 for all five names).
Design spec: `.omc/specs/release-beta-2-plan-2026-09-08.md` §3.

ADR-0192 kept pre-releases off PyPI so that a `0.1.0` upload — which PyPI never
lets you replace — would not be burned on a rehearsal. The gate did its job,
and it also produced the release's one shipped trap.

## What the gate left on the index

All four published names (`aelix`, `aelix-ai`, `aelix-agent-core`,
`aelix-coding-agent`) hold exactly one file each on pypi.org: the `0.0.0a0`
reservation placeholder, summary "Placeholder, no code yet". pip and uv both
apply the same PEP 440 rule: a plain `install aelix` excludes pre-releases
**unless every candidate is a pre-release**, in which case the newest one is
taken. So today the placeholder is what `uv tool install aelix@latest` and
`pip install -U aelix` resolve to — and uv, finding no entry points in it,
prints "No executables … removing tool" and **removes the working aelix** the
user was trying to upgrade (measured; `update_check.py`'s module docstring
carries the reproduction). `update_check.py` exists largely to steer users
around that hole.

## Decision

1. **The `publish` job runs for every tag the build job accepts**, pre-release
   or not. The `if: !contains(github.ref_name, '-')` line is removed.
2. **What protects `0.1.0` is now the version itself, not the job gate.**
   `v0.1.0-beta.2` publishes `0.1.0b2`. A PEP 440 pre-version is invisible to
   a plain install the moment any stable version exists, and until then it is
   strictly better than the placeholder it displaces. The irreversible
   `0.1.0` upload still needs someone to push a tag with no hyphen, through the
   same dot-form guard and the same pin assert as before.
3. **Every pre-release is therefore a full rehearsal of GA publishing** —
   Trusted Publishing, the `pypi` environment approval, attestations, and the
   4-name × 2-artifact upload with `skip-existing: false`. #142's "GA day is
   the first unattended publish" finding closes by construction.
4. **The pin assert reads `[project.optional-dependencies]`.** The root's
   `tui = ["aelix-coding-agent[tui]==X"]` was outside the assert's loop, so a
   bump that missed it would have passed and shipped a `pip install
   'aelix[tui]'` that demands a version not on the index. Measured on the
   worktree: mutating that pin to `0.1.0b9` now fails the assert with
   `pyproject.toml: pins aelix-coding-agent==0.1.0b9, expected 0.1.0b1`; the
   unmodified workspace passes.

## What you give up

- The four names can no longer be rehearsed *without* touching pypi.org. A
  wrong pre-release version is permanently occupied (`0.1.0b2` can never be
  re-uploaded), which is the same cost GA always had — now paid one beta
  number at a time instead of once, blind.
- A half-failed upload (the fourth of eight artifacts 403s) is a real
  half-release on the index. `skip-existing: false` makes the re-run fail
  rather than paper over it; the remedy is the next version number, not a
  retry. This is the #142-4 risk, now visible on a beta rather than on GA.
- `uv tool upgrade aelix` for a plain `uv tool install aelix` install will
  start finding real versions — good — while an `install.sh` install still
  cannot use it (the `--find-links` receipt problem in `update_check.py` is
  unchanged by this ADR).

## Not decided here

- TestPyPI. The plan proposed a TestPyPI rehearsal first; it needs a second
  set of Trusted Publishers on test.pypi.org and a repository-URL switch in the
  workflow. `0.1.0b2` on the real index *is* the rehearsal this ADR chooses;
  if the owner wants TestPyPI as well it is a workflow input, not a decision
  reversal.
- `aelix-server` stays out of the publish set (ADR-0147). Its PyPI name also
  holds a placeholder; that trap is not reachable from any documented install
  path and is left for the v1.1 daemon work.
