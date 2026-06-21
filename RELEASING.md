# Releasing Aelix

This document describes how to cut a release of the Aelix distribution set and
the one-time PyPI configuration the maintainer must perform first.

## What gets published

The release publishes **four** packages, in lock-step at a single shared
version, in dependency order:

1. `aelix-ai`
2. `aelix-agent-core`
3. `aelix-coding-agent`
4. `aelix` (umbrella meta-package)

`aelix-server` (the Web-UI daemon) is **excluded** from this publish set — it is
deferred to a later release. The release workflow builds it as part of
`uv build --all-packages` and then drops its artifacts before upload.

All four published packages share the same version (currently `0.1.0`). The
inter-package dependencies are pinned to that exact version (e.g. the meta
depends on `aelix-ai==0.1.0`), so installing `aelix` from PyPI always pulls a
matching, lock-step set. The `aelix` console script is owned by
`aelix-coding-agent` (the real CLI); the meta-package deliberately does not
define one.

---

## One-time setup: PyPI Trusted Publishing (do this BEFORE the first release)

Publishing uses **PyPI Trusted Publishing** (OIDC). **No API token or password
is stored in this repository or in GitHub secrets** — PyPI mints a short-lived
token at publish time, scoped to this exact repository + workflow + environment.

Before the **first** release you must register a *pending publisher* on PyPI for
**each** of the four published projects (`aelix-ai`, `aelix-agent-core`,
`aelix-coding-agent`, `aelix`). For a brand-new project name that does not yet
exist on PyPI, use the pending-publisher form:

1. Sign in to <https://pypi.org/> with an account that will own the projects.
2. Go to **Account settings → Publishing → Add a pending publisher**
   (<https://pypi.org/manage/account/publishing/>).
3. For each of the four project names, create a publisher with these values:

   | Field             | Value                          |
   | ----------------- | ------------------------------ |
   | PyPI Project Name | `aelix-ai` / `aelix-agent-core` / `aelix-coding-agent` / `aelix` |
   | Owner             | `handochan`                    |
   | Repository name   | `aelix-ai`                     |
   | Workflow name     | `release.yml`                  |
   | Environment name  | `pypi`                         |

   (Repeat the form once per project name — four pending publishers total.)

4. (Recommended) In this GitHub repository, create the `pypi`
   **Environment** (Settings → Environments) and add protection rules
   (e.g. required reviewers) so a human approves each publish. The environment
   name must match the `environment: name: pypi` in `release.yml` and the
   "Environment name" you entered on PyPI.

After the first successful publish, PyPI converts each pending publisher into a
normal trusted publisher attached to the now-existing project. No further setup
is needed for subsequent releases.

> Optional but recommended: do a dry run against **TestPyPI** first by
> configuring the same trusted publishers on <https://test.pypi.org/> and
> temporarily pointing the publish step at the TestPyPI repository.

---

## Cutting a release

1. **Bump the version** in every published package to the new `X.Y.Z`. Keep them
   identical, and update the pinned inter-package constraints to match:

   - `pyproject.toml` (meta) — `version` **and** the `aelix-ai==`,
     `aelix-agent-core==`, `aelix-coding-agent==` pins.
   - `packages/aelix-ai/pyproject.toml` — `version`.
   - `packages/aelix-agent-core/pyproject.toml` — `version` **and** the
     `aelix-ai==` pin.
   - `packages/aelix-coding-agent/pyproject.toml` — `version` **and** the
     `aelix-ai==` / `aelix-agent-core==` pins.

   (You may also bump `aelix-server` to keep the workspace coherent, even though
   it is not published.)

2. **Update `CHANGELOG.md`** — move items out of `Unreleased` into a new
   `## [X.Y.Z] - YYYY-MM-DD` section, and refresh the compare/links at the
   bottom.

3. **Verify locally**:

   ```bash
   uv sync --all-packages
   uv run ruff check .
   uv run pytest -p no:cacheprovider -q
   uv build --all-packages   # confirms all wheels + sdists build
   ```

4. **Commit** the version bump + changelog on the default branch (via PR; CI
   must be green).

5. **Tag and push**:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

6. The **`release.yml`** workflow runs automatically on the `vX.Y.Z` tag:
   it builds all workspace packages, drops the `aelix-server` artifacts, and —
   after the `pypi` environment gate — publishes the four-package set to PyPI
   via Trusted Publishing.

7. **Verify** the new versions appear on PyPI and that
   `pip install aelix==X.Y.Z` resolves the full lock-step set.

> The tag is the single source of truth for triggering a publish. The version in
> the tag (`vX.Y.Z`) should match the `version` in the published pyproject files.
> The workflow does not auto-bump versions — step 1 is manual and deliberate.
