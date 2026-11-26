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

All four published packages share the same version (currently `0.1.1`). The
inter-package dependencies are pinned to that exact version (e.g. the meta
depends on `aelix-ai==0.1.1`), so installing `aelix` from PyPI always pulls a
matching, lock-step set.

The `aelix` console script is declared **twice**, by both `aelix-coding-agent`
and the `aelix` meta-package, and both point at the same target
(`aelix_coding_agent.cli.entry:main_sync`). Because the entry points are
identical the duplication is harmless — whichever wheel installs its script
last, the command behaves the same — but it is a duplication, not a deliberate
omission on the meta-package's side.

---

## One-time setup: PyPI Trusted Publishing (do this BEFORE the first release)

Publishing uses **PyPI Trusted Publishing** (OIDC). **No API token or password
is stored in this repository or in GitHub secrets** — PyPI mints a short-lived
token at publish time, scoped to this exact repository + workflow + environment.

Before the **first** release you must register a trusted publisher on PyPI for
**each** of the four published projects (`aelix-ai`, `aelix-agent-core`,
`aelix-coding-agent`, `aelix`).

> **Use the per-project publisher form, not the pending-publisher form.** The
> pending form is for names that do not exist on PyPI yet. All four of these now
> DO exist — the Gate-0 name reservation published a metadata-only `0.0.0a0`
> placeholder to each on 2026-08-07 (verified 2026-08-10: all four return HTTP
> 200). Add the publisher under **Manage → *project* → Publishing** instead, and
> use the same field values as the table below. The pending-publisher steps are
> kept here because they still apply to any genuinely new distribution name
> (`aelix-server` is still unpublished and would need them).

For a brand-new project name that does not yet exist on PyPI, use the
pending-publisher form:

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

## Before every release: fold the hosted catalog back

Between releases the model catalog reaches users from `handochan/aelix-catalog`
(ADR-0264 §4.2): a scheduled job runs this repository's generator on the newest
green `main` and publishes the rows it adds, without transport. Those rows
reach `main`, and so a release, **only through a commit**. Without one, the ids
new to the latest release grow by up to 100 at every slot until a client's
500-new-ids-per-provider bound refuses every revision for that release.

So the release prep (beta and GA alike), and the owner at any time, runs the
generator once by hand and commits the result as its own change, reviewed in
the diff:

```bash
uv run python scripts/refresh_catalog.py --fetch               # dry run: additions, skips, map sources
uv run python scripts/refresh_catalog.py --fetch --show-skips  # what it refused and why
uv run python scripts/refresh_catalog.py --fetch --apply       # write models_generated.json
uv run pytest -q tests/test_catalog_corrections_are_pinned.py tests/test_catalog_overlay.py \
    tests/test_hosted_catalog_contract.py tests/test_max_thinking_level_433.py
uv run pytest -p no:cacheprovider -q                            # then the whole suite
```

The four files are the catalog's own gates. The whole suite is not optional: the
2026-10-27 fold-back left ten tests elsewhere red. Some were counts to re-derive.
Others record behaviour the new rows move, such as ADR-0249's OpenRouter-namespace
partition and #149's output ceiling.

- `--fetch` downloads models.dev's `api.json` and OpenRouter's public
  `/api/v1/models`; no key is sent. `--apply` refuses to run without
  OpenRouter's list, because every new OpenRouter reasoning row takes its
  thinking map from it.
- Review the diff as rows, not lines (`git diff` re-anchors a file that is
  mostly `},`; ADR-0232). It may only append: `merge()` cannot write a key that
  exists, and the pin test fails if a correction moved.
- Read the transport each new row inherited (ADR-0232's ladder; its residual
  risk is the first model of a new wire on a provider) and each derived
  thinking map (ADR-0264 §7 Q2; a map is upstream's claim until `main` curates
  one). Fix by hand what is wrong, in the same commit.
- A `~` line is a shipped row whose map has no `off` while its model takes no
  `none` by the same evidence (OpenRouter's `reasoning.mandatory`, models.dev's
  effort list; #507, ADR-0264 §10): `--apply` writes `off: null` into its map,
  because otherwise off is sent as `effort: "none"` and answered 400. A `!` line
  is a shipped `off` string that evidence contradicts; nothing overwrites it, so
  correct it by hand. A `~ <row>: <level>: null` line is a level under a hidden
  `off` that the judging API's effort list leaves out (#397): without it a
  fitted `off` lands on a level the model refuses (OpenAI's o-series and
  `minimal`); a `!` line there says why the floor needs a hand (a level named
  under it, or a floor the map does not name). `--apply --off-only` writes the
  `~` fills and no addition.
- It commits upstream as of now, not exactly what was published, with no
  100-id cap, and it proposes again a row a hold rolled back: the diff review
  is the gate. Drop such a row by hand.
- What the generator does not do yet, so the review does (the 2026-10-27
  fold-back, ADR-0264 §10):
  - `tests/test_max_thinking_level_433.py` fails until ADR-0266's family rules
    are merged onto the rows they cover. The DeepSeek V4 rule applies only to a
    row that came with no map.
  - A new Claude or MiniMax generation arrives with no map, and as a budget row
    if its id escapes the adaptive family fallback. Check `off` (some refuse
    `disabled`), `xhigh`, `max`, `forceAdaptiveThinking` and
    `supportsTemperature` against the API or pi.dev's row
    (`https://pi.dev/api/models/providers/<id>?types=chat`).
  - The evidence for a row's wire:
    - models.dev's per-model `provider.npm` (`@ai-sdk/openai`, `@ai-sdk/anthropic`,
      `@ai-sdk/google`, `@ai-sdk/openai-compatible`) where a provider's shipped
      rows follow it;
    - Copilot's `GET /models` (`supported_endpoints`) for Copilot rows;
    - pi.dev's row.

    An added row whose `api` disagrees with all of them is the first-wire case
    to measure or correct.
  - Build each added row's request at every level, as committed and with
    pi.dev's compat, and read the rows where the two differ. pi.dev can be
    wrong for this build. On 2026-10-27 it offered `off` on Claude Sonnet 5.5
    mirrors, where this build's `disabled` is a 400, and hid `off` on Mercury
    2.5, which runs tools at `off`. So a measurement beats it.
  - Correct a row with a wrong transport rather than dropping it: the generator
    proposes a dropped id again.

The next slot publishes from the new `main`, whose catalog is then the
lineage's last entry.

---

## Cutting a release

1. **Bump the version** in every published package to the new `X.Y.Z`. Keep them
   identical, and update the pinned inter-package constraints to match (fold
   the hosted catalog back first, in its own commit: **Before every release**
   above):

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

   Open the section with its `### Known limitations` block, the first `###`
   heading in it (#385). The Release body is this section, then GitHub's
   generated notes, and `scripts/release_notes.py` cuts a body over 120,000
   bytes at an entry boundary, because GitHub refuses one over 125,000
   characters (`## [0.1.0]` was over 460,000). The block survives any cut
   that leaves room for it, and a cut that would drop it fails `github-release`
   before anything is published. `tests/test_release_notes.py` runs the cut on
   the committed CHANGELOG.

   Write the complexity trend value into that section too (#293, ADR-0300 §9).
   It is not a gate, only the number each release is compared by:

   ```bash
   uv run python scripts/check_complexity.py --trend
   ```

   It runs radon 6.0.1 through `uvx` (fetched on first use; nothing is added
   to `uv.lock`) over `packages/` and `src/` and prints how many functions,
   methods and closures there are and how many score over 20 and over 50,
   counted two ways. Copy both lines. The first is the trend value: each
   function, method and closure counted once, as the owner decided on
   2026-11-09 (3,320 on `026bd15`). The second is the trend value as the owner
   set it on 2026-09-19 (docs/05, 4,774 then), radon's recursive count, which
   lists every method twice; the 2026-11-09 decision keeps it written beside
   the trend value for comparison, not instead of it.

3. **Update the update-check feed** — `site/latest-version.json` is what tells
   every already-installed user that this release exists. It is published to
   GitHub Pages from `site/`, so it goes live when this commit lands on `main` —
   before the tag, which is the right order: the file may name a release whose
   tag is minutes away, and nobody is offered a download by it (the notice
   prints a command; it installs nothing). Since #382 that gap also covers the
   candidate smoke: the GitHub Release appears only after `build` and every
   `candidate-smoke` leg passed, and while a leg is red the feed names a version
   that has no Release until the fix is merged and the tag moved (step 7 of
   **Cutting a beta**). The dry run before the tag (step 5 there) is what
   keeps that gap rare.

   Set `latest` to this release. For a stable release set `latestStable` to the
   same values; for a prerelease leave `latestStable` on the newest stable, so a
   user already on a stable is not offered a beta.

   `tests/test_latest_version_feed.py` fails until this matches the version in
   `pyproject.toml`, so it cannot be skipped by accident — deliberately, because
   forgetting it fails SILENTLY in production: the check just keeps reporting
   the previous release, and "no update available" is also what a correct check
   says most of the time.

4. **Refresh compliance artifacts** — the SBOM is versioned, so regenerate it
   after the version bump and commit it with the release:

   ```bash
   uv run python scripts/generate_sbom.py   # writes sbom/aelix-X.Y.Z.cdx.json
   ```

   `tests/test_license_sync.py` (part of the normal pytest run below) guards the
   rest: `LICENSE` / `NOTICE` / `THIRD-PARTY-NOTICES.md` present and identical in
   every package dir, and PEP 639 `license-files` wired in every pyproject. If
   you touched any of those files at the repo root, re-copy them into
   `packages/*/` before committing.

5. **Verify locally**:

   ```bash
   uv sync --all-packages
   uv run ruff check .
   uv run pytest -p no:cacheprovider -q
   uv build --all-packages   # confirms all wheels + sdists build
   # spot-check the license bundle in every wheel (stdlib only — no unzip dependency):
   for w in dist/*.whl; do for f in LICENSE NOTICE THIRD-PARTY-NOTICES.md; do
     python -m zipfile -l "$w" | grep -q "licenses/$f" || echo "MISSING $f: $w"
   done; done
   ```

6. **Commit** the version bump + changelog on the default branch (via PR; CI
   must be green).

7. **Dry-run the release on that commit, then tag and push.** The dry run is
   step 5 of **Cutting a beta** below with `-f tag=vX.Y.Z`: `build` and
   `candidate-smoke` on the commit, no Release, no upload. Before the tag, run
   the upgrade and downgrade check against the previous release on that run's
   build (**Upgrading and downgrading between releases** below). Tag that
   commit once every leg is green:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

8. The **`release.yml`** workflow runs automatically on the `vX.Y.Z` tag, in
   this order (ADR-0269, #382) — nothing is published before the candidate ran:

   1. `build` builds all workspace packages, drops the `aelix-server`
      artifacts and writes `SHA256SUMS`;
   2. `candidate-smoke` installs **that** build — the four `aelix` wheels
      never come from PyPI or a Release; third-party dependencies do come from
      PyPI, as for a user — on Ubuntu, macOS and Windows with the installers'
      own Step 5 command and the interpreter request they make for this
      version, then runs it: `aelix --version` must print the tag's version
      and an `aelix -p` turn against a local stub must succeed (a fake key;
      the turn reaches no network). Each leg runs on the CPython uv downloads for a user with no
      Python in that range — the newest inside it (3.14.x for 0.1.0 and
      later); ci.yml's legs cover 3.11–3.14 from source. What a red leg shows
      is under step 7 of **Cutting a beta**;
   3. `github-release` (needs `build` + `candidate-smoke`) creates the Release
      and attaches the wheels, the sdists, `SHA256SUMS` and the SBOM. Its body
      is composed by `scripts/release_notes.py` (step 2): a cut body ends on a
      link to `CHANGELOG.md` at the tag and the compare view;
   4. `publish` (needs all three) waits for the `pypi` environment approval,
      then uploads the four-package set to PyPI via Trusted Publishing.

   The approval is asked for only once the Release exists. Use that window as
   the beta flow does (step 8 of **Cutting a beta** below): run the installer
   with `AELIX_VERSION=vX.Y.Z`, then approve.

9. **Verify** the new versions appear on PyPI and that
   `pip install aelix==X.Y.Z` resolves the full lock-step set.

> The tag is the single source of truth for triggering a publish. The version in
> the tag (`vX.Y.Z`) should match the `version` in the published pyproject files.
> The workflow does not auto-bump versions — step 1 is manual and deliberate.

---

## Upgrading and downgrading between releases

This section records what moving between two releases keeps, as measured. For
0.1.0, #383 took beta.2 (`0.1.0b2`) up to 0.1.0, down to beta.2 and up again, on
the candidate `d9aa80f3`, whose tree is the `v0.1.0` tag's (`aceeb29c`), before
the tag. It ran on macOS (uv-managed CPython 3.13.13; 0.1.0's fresh installs in
F on 3.14.5) and in Linux docker (CPython 3.12.3), twice each (runs 3 and 4),
against a stub model on 127.0.0.1, with a scratch `HOME`, `UV_TOOL_DIR`,
`UV_TOOL_BIN_DIR` and `TMPDIR`. Every aelix run was `--offline` except
`aelix --version` (B0, U4), which returns before any fetch (code reading), and
C3's `aelix update --models`, which fetched the hosted catalog. All 236 checks
passed on both legs in both runs: no step lost data and none crashed. The
letters and numbers below are its step ids.

**Upgrade, 0.1.0b2 → 0.1.0.**

| Route | Result |
| --- | --- |
| Re-run the install line | Works (B, E). beta.2's sessions continue with every message on the chain (B1, B2); its `auth.json` gives the key and its `trust.json` still applies the project settings (B1); `/resume`, `/stats` and `/cost` open on its sessions (B3). |
| `uv tool upgrade aelix` over a PyPI install made with the old installer's interpreter range (`uv tool install --force --python '>=3.11,<3.14' 'aelix[tui]'`, U3) | Works (U4): every distribution reports 0.1.0, the tool environment keeps its interpreter (3.13.13 on macOS, 3.12.3 on Linux), the sessions continue. 0.1.0 was offered through `UV_FIND_LINKS` beside PyPI, not from PyPI. |
| `uv tool upgrade aelix` over an install-line install | Exits 1 on both legs in runs 3 and 4 (U2): the receipt names install.sh's `--find-links` temporary directory, which install.sh deleted, and pins `==0.1.0b2` (U1). Nothing changes: the tool stays on 0.1.0b2 and `aelix -c` still works. Re-run the line instead (#464 item 2). |

After the upgrade, background jobs are on: beta.2's `settings.json` has no
`backgroundJobs`, and 0.1.0 offers `job`, `job_stop` and `run_in_background`
(C0a). An `auth.json` entry with an empty key makes 0.1.0 exit 1, naming
`auth.json` and `/login`, where beta.2 used the environment variable (the
empty-key probe in A and B).

**Downgrade, 0.1.0 → 0.1.0b2.** The route measured is the install line with
`AELIX_VERSION=v0.1.0-beta.2` reaching `sh` (D, F2, U5; the getting-started
guide's *Upgrading and uninstalling* has the form). Nothing was lost:

- the sessions beta.2 was given continue with no message off the chain: C1's
  delegated session (D1), the job session (D2b), J1 and J2 (D9), F1's session
  (F2) and the U box's session (U5); C1's child file opens on beta.2 (D2).
  Before beta.2's own turn, each line
  0.1.0 wrote is unchanged: as parsed values in the run's checks (D2b, D9, F2),
  and byte for byte for the sessions in D when the run's C and D snapshots were
  compared afterwards (F2 has no earlier snapshot). The background-spawned child
  and its `.result` file stay byte-identical (D2b); beta.2 did not continue that
  child;
- beta.2's runs leave `settings.json` byte-identical (D3b, and D4's twelve
  settings probes), and beta.2's own settings writer keeps `agents`,
  `completedThinkingDisplay`, `backgroundJobs` and `retry.maxAgentDelayMs` with
  their values (D5);
- `auth.json`, `trust.json`, `models.json`, `models-store.json`, `mcp.json` and
  the profile files are byte-identical (D's state check, D6, D7);
- a fresh 0.1.0 install on CPython 3.14.5 went to 3.13.13 for beta.2, below the
  3.14 on which beta.2's `openai<2.0` dies (#262), and back to 3.14.5 on the way
  up (F1–F3, macOS);
- back on 0.1.0, the sessions reopen (E1), the settings keys read back (E3), and
  `/jobs` lists the jobs as done with no notice repeated (E4, E5j).

What beta.2 does with what only 0.1.0 writes (measured unless a row says code
reading; none loses data):

| 0.1.0 wrote | beta.2 | Steps |
| --- | --- | --- |
| A profile turned off in the `agents` setting | Offers it again: with `agents: ["-explorer"]` it offers explorer | D4 (C5 on 0.1.0) |
| `agents:` in a profile's frontmatter | Ignores it, with no warning on `-p` stderr: `agents: []` or `agents: [helper383]` still delegates to general-purpose, and the child runs | D6 (C6) |
| `"enabled": false` in `mcp.json` | Launches the server | D7 (C7, E8) |
| `completedThinkingDisplay` | Has no such setting (code reading of `v0.1.0-beta.2`); the file keeps the key | D4, D5 |
| Job notices | Shows each as a raw `[aelix.job_notice]` block, and sends it to its model as a user message that points at `job(action="result")` and `agent(action="result"/"resume")`. beta.2 offers no `job` tool (D2b), and its `agent` tool has no `action` (code reading of `v0.1.0-beta.2`) | D3j, D2b |
| A job stopped at `/quit` | Does not deliver its "was stopped" notice; 0.1.0 does, at the next turn on that session | D9, E7 |
| A job left by a killed aelix | Says nothing; 0.1.0's next start reports it "was lost" | D9, E7 |
| A default model from the 516 catalog rows beta.2 lacks (168 of them from the fold-back) | `get_model` returns None and `--list-models` does not show it. With no key, both releases exit 1 with `No API key found for OpenRouter.` and leave `settings.json` as written; with a key: not measured | C8, D8 (one row, `openrouter/cohere/command-a`) |
| Delegated children's spend | `/stats` leaves it out. On C1's session after D1, beta.2 shows 3,000 tokens in (the parent's three requests, not the child's 1,000), cost `n/a` and no delegated line. 0.1.0 on that session two turns later shows 6,000 in, cost `≥ $0.0037`, and a `Tools & delegated agents: 1.0k in / 50 out · $0.0037 (1 run)` line for the child | D3, E2 |
| Session-lock files: a `.jsonl.lock` beside each session and, on macOS, a 73-byte locator per session path in the account's `~/Library/Caches/aelix-session-locks-v2/` | Both stay; beta.2's code names neither (code reading of `v0.1.0-beta.2`) | Z: 12 locators per macOS run, none in docker |

With each of 0.1.0's other new settings keys and values set, beta.2 runs a turn
and leaves the file as written (D4); what it makes of them was not checked.

**Not measured for 0.1.0:**

- Windows: install.ps1 was not run on any platform.
- An install from the published `v0.1.0` Release or from PyPI's `0.1.0`: the
  run predates the tag. The install line took the candidate's wheels from a
  local copy of the Release's download path, with install.sh's `SHA256SUMS`
  check unchanged, and U4 took them through `UV_FIND_LINKS`. The bytes are the
  same: after the publish, the Release's `SHA256SUMS` and PyPI's sha256 digests
  for 0.1.0 equal the tested build's, all eight files.
- A downgrade by any route other than the install line, such as
  `uv tool install --force 'aelix[tui]==0.1.0b2'`. It does not apply the install
  line's interpreter range, so on a machine with 3.14 it would put beta.2 on 3.14
  (#262; not run). `uv tool install aelix@latest` now resolves to 0.1.0.
- `uv tool upgrade` over a 0.1.0 install-line install: its receipt has the same
  shape (code reading of install.sh).
- A real model: the stub answered every turn.
- mcp.json's locking writer, the `/mcp` and `/agents` panels' own keys, and a
  delegation cancelled with Esc (ADR-0307) read by beta.2.

**For the next release:** before the tag, repeat this from the previous release
to the candidate on macOS and Linux, and on Windows through install.ps1, and
replace this section's results with what that run measured. The harness #383
used is not in this repository, so the procedure is:

1. Keep every run inside a scratch `HOME`, `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR` and
   `TMPDIR`, with a stub model on 127.0.0.1 named in `models.json` and every
   aelix run `--offline` except a catalog refresh you mean to test. On macOS
   that does not cover the session-lock locators, which go to the account's real
   `~/Library/Caches` whatever `HOME` says (Z above), nor install.sh's temporary
   directory, which `mktemp -d` puts under `/var/folders/…/T` whatever `TMPDIR`
   says.
2. Install the previous release with the install line and write with it: its
   settings, `auth.json` and `trust.json`, a plain session, a delegated session.
3. Install the candidate (the dry run's build, served at the Release's download
   path, or `uv tool install --find-links <dist>`), continue every old session,
   and write what only the candidate writes: new settings keys, frontmatter,
   `mcp.json` fields, background jobs, including one left by a quit and one by
   a kill.
4. Install the previous release again with `AELIX_VERSION=<previous tag>`,
   continue the candidate's sessions, open them in the TUI, and compare each
   file under the agent directory with step 3's copy, byte for byte: whole for
   configuration, up to beta.2's own turn for a session.
5. Install the candidate again and reopen everything. Separately, run
   `uv tool upgrade` over a PyPI install and over an install-line install, and
   take a fresh candidate install down and up.

---

## Beta / pre-release track

Aelix ships pre-releases (beta, rc, alpha) as GitHub Releases — the package
body is distributed as checksum-verified wheels attached to the Release and
installed via the [`install.sh`](install.sh) one-liner — **and, since
`v0.1.0-beta.2`, to PyPI as the PEP 440 pre-version** (`0.1.0b2`). ADR-0240
records why: until then the four PyPI names held only a `0.0.0a0` placeholder,
and because pip/uv take the newest pre-release when *every* candidate is one,
`uv tool install aelix@latest` installed that placeholder and removed the
user's working aelix. A pre-version on the index closes that hole and is
invisible to a plain `pip install aelix` the moment a stable version exists.

### The hyphen convention

A single signal drives the GitHub side: **a tag that contains a hyphen is a
pre-release.** The `github-release` job passes `--prerelease` to
`gh release create` when the tag contains one, so GitHub marks it as such.
The `publish` (PyPI) job no longer reads the hyphen at all (it did until
beta.2 — `if: ${{ !contains(github.ref_name, '-') }}`, removed by ADR-0240):
the PEP 440 normalisation of the tag decides what kind of version lands on
the index, and PyPI's own rules decide who sees it.

So `v0.1.0-beta.2` (has `-`) → `0.1.0b2` on PyPI, GitHub pre-release.
`v0.1.0` (no `-`) → `0.1.0` on PyPI, full GitHub release.

> **A pre-release must use a hyphen, never a dot.** The tag gate in the `build`
> job rejects the dot form outright: `v0.1.0-rc.1` is accepted, `v0.1.0.rc1` is
> refused. This matters because the dot form contains no hyphen, so it would
> have been read as GA — published to PyPI irreversibly (`skip-existing: false`)
> and marked a full release. The gate also asserts, before the build runs, that
> the tag normalizes to the version in `pyproject.toml` under PEP 440
> (`v0.1.0-beta.1` → `0.1.0b1`), so a tag pushed without the version bump fails
> the job instead of publishing a mismatched artifact.

> All four jobs — `build`, `candidate-smoke`, `github-release`, `publish` — run
> for **every** release tag (beta and GA), in that order. A manual run (the dry
> run, step 5 of **Cutting a beta**) runs the first two and nothing else. The
> `github-release` job attaches the four wheels + four sdists + the `SHA256SUMS`
> manifest — that Release is exactly what `install.sh` consumes, and
> `candidate-smoke` has installed and run those same wheels on Ubuntu, macOS and
> Windows before it.

### Every pre-release is a GA rehearsal

Because `publish` runs for a hyphenated tag, a beta cut exercises the whole
PyPI path — Trusted Publishing (the #73 publishers, registered 2026-09-08),
the `pypi` environment's required-reviewer approval, PEP 740 attestations, and
the eight-artifact upload with `skip-existing: false`. Two consequences worth
holding in mind before you push the tag:

- **A pre-version is permanent.** PyPI never lets a version be re-uploaded;
  a mistaken `0.1.0b2` is occupied for good and the fix is `0.1.0b3`.
- **A half-failed upload is a half-release.** If the fourth of eight artifacts
  is rejected, the first three are on the index. Do not retry the same
  version — cut the next one.
- **A local `twine check` does not predict the publish job.** `twine check`
  runs inside `gh-action-pypi-publish`, against the twine *that action pins*,
  before anything is uploaded. Rehearsing with `uv run --with twine` resolves
  whatever is newest instead. The beta.2 rehearsal passed 8/8 on twine 7.0.0
  and the tagged run then died on the action's twine 6.1.0:

  ```
  InvalidDistribution: Invalid distribution metadata:
    '2.5' is not a valid metadata version
  ```

  hatchling emits `Metadata-Version: 2.5`; packaging 25.0 (bundled with twine
  6.1.0) does not know it, packaging 26.2 does, and PyPI itself accepts it.
  Nothing was uploaded — `twine check` precedes the upload, so this failure
  mode is safe and the version stays free. To rehearse honestly, read
  `requirements/runtime.txt` at the `pypa/gh-action-pypi-publish` SHA pinned in
  `release.yml` and install that exact twine.

Because the second point above means a genuine half-release cannot be retried,
it is worth being precise about which failures are which: anything that fails
**before** the upload (the tag gate, the version/pin assert, `candidate-smoke`,
`github-release`, `twine check`) leaves the index untouched and the same
version can be re-cut after a retag.
Only a rejection **during** the upload occupies versions. Check the index
before assuming the worse case:

```bash
for p in aelix aelix-ai aelix-agent-core aelix-coding-agent; do
  curl -s "https://pypi.org/pypi/$p/json" | python3 -c \
    "import json,sys; print('$p', sorted(json.load(sys.stdin)['releases']))"
done
```

#### Retagging after a pre-upload failure

A re-run of the failed job is **not** the fix: GitHub replays a run against the
workflow file *as it stood at the triggering commit*, keeping that run's
`GITHUB_SHA` and `GITHUB_REF`, so the correction on `main` never loads. Land the
fix, wait for CI, then move the tag.

🔴 **Move it with a single force-update. Do not delete and recreate it.**
Deleting a tag is itself a `push` whose payload says `deleted: true`, the tag
filter still matches the deleted ref, and `GITHUB_SHA` reverts to the default
branch — which is carrying the very version you just tagged, so the pin assert
would pass on it. `release.yml` fails closed on that (`if: !github.event.deleted`
on `build`, inherited by every job downstream of it), but the guard is a
backstop, not the plan.

```bash
git tag -f -a v0.1.0-beta.2 -m "…" <new-sha>   # move it locally
git push --force origin v0.1.0-beta.2          # ONE ref update, re-triggers Release
```

There is nothing to clean up on the GitHub Release page: `github-release`
updates an existing release in place rather than failing on the taken name.

### Cutting a beta

1. **Bump the version to the PEP 440 beta form** (`0.1.0b2` for the tag
   `v0.1.0-beta.2`) in every published package and its inter-package pins (same
   files as step 1 above; `release.yml` asserts all of them, the `[tui]` extra
   pin included, before it builds; fold the hosted catalog back first, in its
   own commit: **Before every release** above):

   - `pyproject.toml` (meta) — `version`, the `aelix-ai==` / `aelix-agent-core==`
     / `aelix-coding-agent==` pins, **and** the `[tui]` extra pin.
   - `packages/aelix-ai/pyproject.toml` — `version`.
   - `packages/aelix-agent-core/pyproject.toml` — `version` + `aelix-ai==` pin.
   - `packages/aelix-coding-agent/pyproject.toml` — `version` + `aelix-ai==` /
     `aelix-agent-core==` pins.
   - `packages/aelix-server/pyproject.toml` — `version` (workspace coherence).

2. **Update `site/latest-version.json`** exactly as in step 3 of the GA flow —
   `latest` gets this beta, `latestStable` keeps the newest stable (or stays
   `null` while none has shipped). This is what tells an existing beta user that
   the next beta exists, so it matters more here than anywhere.

3. **Verify locally** (same commands as the GA flow):

   ```bash
   uv sync --all-packages
   uv run ruff check .
   uv run pytest -p no:cacheprovider -q
   uv build --all-packages
   ```

4. **Commit** on the default branch (via PR; CI green).

5. **Dry-run the release on that commit, before you tag it** (#535,
   ADR-0269). `release.yml` also runs by hand. Given the tag you are about to
   push as its `tag` input, it runs `build` and `candidate-smoke` on the commit
   it is started on: the same tag gate and version/pin assert, the same
   install on Ubuntu, macOS and Windows, the same checks. Then it stops.
   `github-release` and `publish` run for a tag push only, so a manual run
   creates no Release and uploads nothing to PyPI.

   ```bash
   gh workflow run release.yml --ref main -f tag=v0.1.0-beta.2
   gh run list --workflow release.yml --event workflow_dispatch --limit 1   # its run id
   gh run watch <run-id> --exit-status
   ```

   Every leg must be green before step 6, and the commit you tag must be the
   one the run built: the `Validate release tag` step prints its SHA. A red
   leg reads as in step 7. Fix it on `main` and dry-run again; no tag exists
   yet, so there is nothing to move. This is also the first run of a new
   Windows or macOS path: until #535 that first run was the tag itself.

   - Dry-run the version you are about to release. For a version already on
     PyPI (`main` before its bump), the index holds another file of the same
     version. uv 0.11.19 took the build's (ADR-0269); a uv that takes the
     index's fails `installed` on the bytes, which is the check working, not
     a defect in the build. An unreleased version has no such file.
   - The feed (step 2) is live from step 4 on. To dry-run before the feed
     names the version, start the run on the release branch instead
     (`--ref <branch>`), before it lands; then tag the commit that lands, if
     its tree is the one the run built.

6. **Tag with the hyphenated pre-release form and push**:

   ```bash
   git tag v0.1.0-beta.2
   git push origin v0.1.0-beta.2
   ```

7. **Wait for the candidate smoke** (`release.yml`, ADR-0269, #382). `build`
   runs, then `candidate-smoke` installs that build on Ubuntu, macOS and
   Windows exactly as the installers install a Release and runs it. Nothing
   exists yet that a user can install: no Release, no PyPI upload. The dry
   run in step 5 ran the same legs on this commit. A leg that is red here
   and was green there is a flake, or a change between the two runs: a new
   uv, or a third-party dependency released in between.

   Each leg installs on the CPython uv downloads for a user with no Python in
   the installers' range — the newest inside it (3.14.x for 0.1.0 and later,
   3.13.x for older versions); the step log prints the version and where uv put
   it. The runner's own Python is not used, and other interpreters are not
   tried here: ci.yml's legs run 3.11–3.14 from source on Ubuntu and Windows.

   **If a `candidate-smoke` leg fails, nothing was published.** What the log
   shows depends on the step that failed:

   - one of the two checks (`The environment holds the candidate's own bytes…`,
     `aelix --version, then one turn…`): an `::error::` line naming the check
     (the interpreter request, `SHA256SUMS`, the interpreter, a wheel installed
     from somewhere other than this build, an import, `aelix --version`, or the
     `-p` turn and its output). An error no check anticipated (the stub could
     not bind, a file could not be read) ends in a Python traceback instead;
   - the uv bootstrap or `Install the candidate as the installers do`: no
     `::error::` line of ours — uv's own message (a resolution failure, an
     interpreter it could not download) and the step's non-zero exit, as a
     user's installer would show it; or bash's `build.outputs.version is
     empty` / `build.outputs.python_request is empty` if `build` handed over an
     empty one.

   Then:

   - A flake (a download that timed out, a runner that died): **Re-run failed
     jobs** on that run. The workflow file is unchanged, so replaying it is
     right here (unlike a fix to the workflow itself, see *Retagging* above).
   - A defect in the candidate: fix it on `main` via PR, wait for CI, and move
     the tag with the single force-update in *Retagging after a pre-upload
     failure*. The version number is still free: there is no Release to clean
     up and nothing reached PyPI.

8. **The window: check the installers against the new Release, before you
   approve `publish`.** Once every smoke leg is green, `github-release` creates
   the Release, and only then does `publish` start and wait for the `pypi`
   environment approval. In between, the Release is up and PyPI does not have
   the version yet — and the installers do not need it to: they take the four
   `aelix*` wheels from the Release (checksum-verified, `--find-links`) and
   only third-party dependencies from PyPI.

   - The GitHub Release `v0.1.0-beta.2` is marked **Pre-release** and carries
     the four `aelix*` wheels, the four sdists, `SHA256SUMS`, and the SBOM —
     the `github-release` job refuses to create the Release unless exactly one
     `sbom/aelix-<version>.cdx.json` exists and its version matches
     `pyproject.toml`, so a forgotten step 4 above fails that job (and,
     since #382, `publish` never starts).
   - The one-liner installs and smoke-tests. **Mind the shape.** An
     `AELIX_VERSION=… curl … | sh` prefix sets the variable for **`curl`**, not
     for the `sh` on the other side of the pipe — the installer would never see
     it and would resolve the newest release instead of the pinned tag, so the
     check would pass while measuring the wrong thing:

     ```bash
     AELIX_VERSION=v0.1.0-beta.2 \
       sh -c "$(curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh)"
     aelix --version
     ```

   - On Windows, the same check through `install.ps1`. `iex` runs the script in
     the current session, so `$env:` assignments before the pipe do reach it:

     ```powershell
     $env:AELIX_VERSION = 'v0.1.0-beta.2'
     irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex
     aelix --version
     ```

   Keep the window short. The Release is already what `install.sh` and
   `install.ps1` install for anyone who does **not** set `AELIX_VERSION` (the
   newest tag, betas included). If the check fails: **reject** the `pypi`
   deployment, so nothing reaches PyPI and the version stays free; delete the
   Release so the installers stop resolving it (`gh release delete
   v0.1.0-beta.2 --yes` keeps the tag); then fix and move the tag as above.

9. **Approve `publish`**, then confirm the four names on pypi.org show the new
   pre-version.

A later pre-release takes its own version's suffix (`0.2.0b1` /
`v0.2.0-beta.1`, etc.). A final release uses the un-hyphenated tag (`v0.1.0`,
`v0.2.0`) and follows the **Cutting a release** flow above. `0.1.0` was planned
as `0.1.0b3` and cut as a final release instead (2026-11-25); no `0.1.0b3`
exists.
