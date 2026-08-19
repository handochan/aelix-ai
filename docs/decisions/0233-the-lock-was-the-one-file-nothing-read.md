# 0233. The lock was the one file nothing read

Status: Accepted (2026-08-19).
Date: 2026-08-19
Relates: ADR-0230 (the `packaging` dependency this failed on, and the gate that could
not see it).

`c97fa12` added `packaging>=23` to `packages/aelix-coding-agent/pyproject.toml` and
shipped without regenerating `uv.lock`. The suite was green — 9168 tests. CI was green.
It took a follow-up commit, `a5e14ef`, to sync the lock, and the only reason anyone
looked was that an unrelated worktree happened to be dirty.

## Why nothing caught it, and why that was structural

`tests/packaging_gate/test_declared_dependencies.py` exists for the neighbouring
failure — an import declared nowhere — and it reads `pyproject.toml` **deliberately**,
because a dev checkout has packages a user's install does not and the manifest is the
only place that difference is visible. Its own docstring names the limit:

> WHAT IT DOES NOT CATCH. Version floors (`packaging>=23` vs `packaging`), and a
> dependency declared on the wrong package of the four.

That made it blind to this one in a way no amount of care would fix: **the manifest was
the side that was right.** Everything else in the suite imports from the dev
environment, where `uv sync` had already installed the package the lock omitted. There
was no reader of `uv.lock` anywhere in the repository.

## What a stale lock costs

`uv sync`, `uv run`, and any install resolved from the lock get the old set. So the
failure does not land on the person who caused it — it lands on a cold CI cache, a
contributor's first `uv sync`, or a release build, and it lands as a missing module at
import time, far from the commit responsible.

## The gate

`uv.lock` already echoes each workspace package's declared dependencies under
`package[].metadata.requires-dist`, plus `provides-extras` and `requires-dev`. The gate
canonicalises both sides with `packaging` — name, extras, specifier, marker — and
compares them as sets, in both directions, for all five manifests.

It was checked against the defect rather than assumed to catch it: with `c97fa12`'s
`uv.lock` restored over the current manifests, the gate fails naming `packaging`, the
package, the file, and `uv lock` as the fix.

Two things it does not check, both stated in the module docstring so the next person
does not assume otherwise:

- **Whether the locked versions satisfy the specifiers.** A different question, and one
  `uv lock` answers when it runs.
- **The `==0.1.0b1` pins on the workspace packages.** `[tool.uv.sources]` rewrites those
  to `editable` paths, so the lock does not carry a pin to compare;
  `test_release_version_consistency.py` holds that one.

## Consequences

- A commit that changes a dependency and not the lock now fails locally and in CI, with
  the remedy in the assertion text.
- Adding a fifth package to `packages/` fails `test_the_scan_found_every_package_in_the_repo`
  until it is listed — deliberately, because a silently unscanned package is the same
  failure one level up.
