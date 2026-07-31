# #111 remainder — the pre-beta cleanup batch

Branch `chore/111-pre-beta-cleanup`, worktree `/workspaces/aelix-111`, from `main` @ `554df2e`.

Advances **#111** (the v0.1.0-beta.1 pre-announce track). Does NOT close it — A-4 (the tag launch),
B-5, B-8, B-9 and B-10 are owner actions and stay open.

## 0. Current state, re-measured today — the issue is 12 days old and some of it is already fixed

| item | 2026-07-19 claim | measured now | in scope |
| --- | --- | --- | --- |
| A-2 README `o0Read` typo | present in working tree | **already fixed** — grep finds nothing | no |
| A-3 CHANGELOG phantom `0.1.0` | present | **still broken** — `## [0.1.0] - 2026-06-20` at `CHANGELOG.md:175`, two dead footer links, and `git ls-remote --tags origin` returns **0 tags** | YES |
| A-4 tag launch | pending | still 0 tags, 0 releases | owner |
| B-1 `aelix auth` dead end | present | **still broken** — `cli/list_models.py:131` | YES |
| B-2 `enable_install_telemetry` | dead flag | **still present**, and consumers are still exactly zero (only its own def + one test) | YES |
| B-3 known-limitations note | missing | **still missing** — grep over both READMEs finds nothing | YES |
| B-4 SECURITY.md / templates | missing | **still missing** — `.github/` contains only `workflows/` | YES |
| B-6 release-notes curation | `--generate-notes` only | **unchanged** — `release.yml:176` | YES |
| B-7 packaging hygiene | leak reproduced | **LIVE, reproduced in a fresh build today** (see §1) | YES |
| B-11 inert `/settings` rows | 11 rows | **still present** | YES (copy only) |
| B-5 / B-8 / B-9 / B-10 | — | owner actions, or blocked on #91 | no |

## 1. B-7 is the one with teeth — and the one that can break the product

A fresh `uv build --package aelix-coding-agent` today puts **four developer-scratch files inside the
published wheel**:

```
aelix_coding_agent/.omc/state/agent-replay-719f820c-….jsonl   1308 B
aelix_coding_agent/.omc/state/last-tool-error.json             574 B
aelix_coding_agent/.omc/state/mission-state.json              3857 B
aelix_coding_agent/.omc/state/subagent-tracking.json          1795 B
```

They are untracked by git yet present on disk, and no package declares any
`exclude` / `targets.sdist`, so hatchling sweeps them in. The `agent-replay-*.jsonl` can carry
transcript content from the maintainer's own sessions — an information-disclosure problem on top of
the hygiene one, in a product whose stated wedge is "auditable".

**THE LANDMINE — read before touching any exclude pattern.** The wheel is now a content carrier, not
just code. Verified present in today's build and REQUIRED to stay:

```
aelix_coding_agent/examples/INDEX.md                4926 B   (#114)
aelix_coding_agent/examples/echo/aelix-plugin.toml  9188 B   (#114)
aelix_coding_agent/examples/echo/echo.py            2315 B   (#117 signpost points at this)
aelix_coding_agent/extensions/api.py                         (#117 signpost points at this)
```

An over-broad exclude (`*.md`, `*.toml`, a bare `.omc` glob that catches more than intended) silently
drops these. The failure mode is not "broken", it is **unshippable**: the #117 signpost cites absolute
paths, so an installed agent would confidently name files that are not on disk. Mitigating factor
already in place — `_package_pointer` (`cli/agent_context.py:56,63`) drops a pointer whose file is
missing rather than emitting a dead path — but a silently thinner corpus is still a regression.

**This must be proven by a real build plus a zip listing, never by reading pyproject.**

Scope for B-7:
- Explicit wheel `exclude` for `.omc` and `__pycache__` in every package that has a
  `[tool.hatch.build.targets.wheel]`, plus explicit sdist configuration at the root (the sdist is the
  worse leak: a local `uv build` swept `CLAUDE.md`, `AGENTS.md`, `.omc/wiki/` session logs and root
  `aelix-session-*.html` into it).
- Delete the four untracked `.omc/state/` files from the package tree.
- Extend `.gitignore` so `.omc/` as a whole, `CLAUDE.md`, `AGENTS.md` and `aelix-session-*.html`
  cannot reach the public repo with one `git add -A`.
- **A test that builds and asserts wheel contents** — must contain the four content files above, must
  NOT contain `.omc` or `__pycache__`. Without this the fix rots the moment someone adds a pattern.

CI is safe today (`actions/checkout` is a clean clone, so the untracked files do not exist there); the
exposure is an owner running `uv build` locally. Fix it anyway — that is exactly how the beta would
have been cut.

## 2. Work breakdown — three DISJOINT lanes, safe to run in parallel

### Lane A — packaging hygiene (B-7)
Owns: `pyproject.toml` (root + all `packages/*/pyproject.toml`), `.gitignore`, the deleted
`packages/aelix-coding-agent/src/aelix_coding_agent/.omc/**`, and one new packaging test.

### Lane B — honesty and messaging (A-3, B-1, B-3, B-6)
Owns: `CHANGELOG.md`, `packages/aelix-coding-agent/src/aelix_coding_agent/cli/list_models.py`,
`README.md`, `README.ko.md`, `.github/workflows/release.yml`.

- **A-3** — move the beta content under a `## [0.1.0-beta.1]` heading (or relabel the phantom entry);
  the current `## [0.1.0] - 2026-06-20` describes a release that has never existed, and both footer
  links 404. Decide the heading with the tag name `v0.1.0-beta.1` in mind.
- **B-1** — `list_models.py:131` tells a credential-less first-time user to run `aelix auth`, which
  does not exist (it is parsed as a message and exits 1). Replace with the real path: set a provider
  env key, or run `aelix` and use `/login` (TUI-only). Check every sibling onboarding string for the
  same lie rather than fixing only this one.
- **B-3** — a Known-limitations note in BOTH READMEs: no iteration cap / dedup / spend ceiling
  (#14/#6/#52 — backstops exist: Esc abort, guardrail floor, auto-compaction, 600 s bash timeout, but
  token spend is unbounded), and headless `--print` / `--mode json` / rpc **auto-approve mutating
  tools including bash** while the README advertises headless embedding. State it plainly.
- **B-6** — `release.yml:176` uses `--generate-notes` alone. With no previous tag, GitHub will scrape
  every merged PR since the repo began, so the first release page the announcement links to becomes a
  100+ PR dump and the hand-written CHANGELOG is never referenced.

### Lane C — dead code and community health (B-2, B-4, B-11)
Owns: `packages/aelix-ai/src/aelix_ai/settings/types.py`,
`packages/aelix-ai/src/aelix_ai/settings/settings_manager.py`,
`tests/settings_manager/test_settings_manager_getters_setters.py`, `SECURITY.md`,
`.github/ISSUE_TEMPLATE/**`, `packages/aelix-coding-agent/src/aelix_coding_agent/tui/settings_rows.py`.

- **B-2** — remove `enable_install_telemetry` (field at `types.py:237`, JSON key map at `:331`,
  getter/setter at `settings_manager.py:1284-…`, and its test). Runtime behaviour is already
  truthful — the README's "never phones home" is TRUE, there is no network sink — but a user auditing
  the settings schema of a tool that sells auditability finds `enableInstallTelemetry` and reads a
  contradiction. **VERIFY FIRST, do not assume**: settings are plain `@dataclass` (not Pydantic), so
  an unknown key in an existing `settings.json` is probably ignored — but prove it by writing a
  settings file containing `enableInstallTelemetry`, removing the field, and confirming the load does
  not raise. If it does raise, STOP and report; silently breaking every existing settings file is far
  worse than a cosmetic schema wart.
- **B-4** — `SECURITY.md` (private reporting route; the owner picks the channel, so write it to use
  GitHub private security advisories and leave a clearly-marked placeholder for any e-mail),
  `.github/ISSUE_TEMPLATE/bug_report.yml` collecting `aelix --version` / provider / OS / repro, and
  `config.yml` routing security reports to SECURITY.md. A product fronting security opening a public
  beta with no private disclosure route is the gap.
- **B-11** — copy softening ONLY. Verify which of the 11 rows are still consumer-less rather than
  trusting the 2026-07-19 list; several may have been wired since. For each still-inert row, stop the
  help text promising "Persisted; applies next launch". Do NOT wire features here, and do NOT touch
  `enable_skill_commands`'s underlying mechanism — that is #115.

## 3. Out of scope

A-4 (tag launch), B-5 (repo About metadata), B-8 (marketplace positioning — blocked on **#91**),
B-9 (#98/#99 reporter re-verification), B-10 (Copilot ToS). All owner actions.
Also out: #115 skills wiring, #91, and any CI branch-protection change (an owner decision surfaced by
#118 — a red matrix leg does not block a merge, which is how py3.11 stayed broken for three days).

## 4. Verification gate

**MANDATORY test command** — the venv is an editable install pointing at `/workspaces/aelix-ai`;
without this PYTHONPATH a worktree run imports the MAIN repo and green means nothing:

```
cd /workspaces/aelix-111 && source /workspaces/aelix-ai/.venv/bin/activate && \
PYTHONPATH="/workspaces/aelix-111/packages/aelix-coding-agent/src:/workspaces/aelix-111/packages/aelix-agent-core/src:/workspaces/aelix-111/packages/aelix-ai/src:/workspaces/aelix-111/packages/aelix-server/src:/workspaces/aelix-111/src" \
python -m pytest -q -p no:cacheprovider
```

Print `aelix_coding_agent.__file__` and assert it starts with `/workspaces/aelix-111` first.

Acceptance:

- [ ] Fresh `uv build --package aelix-coding-agent`: wheel contains `examples/INDEX.md`,
      `examples/echo/aelix-plugin.toml`, `examples/echo/echo.py`, `extensions/api.py`; contains NO
      `.omc` and no `__pycache__`. Paste the zip listing.
- [ ] Fresh root `uv build`: sdist contains no `.omc`, no `CLAUDE.md`, no `AGENTS.md`, no
      `aelix-session-*.html`. Paste the listing.
- [ ] `uv build --all-packages` still succeeds for all four packages (this is what `release.yml` runs).
- [ ] A settings file carrying `enableInstallTelemetry` still loads after the field is removed.
- [ ] `aelix --list-models` with no credentials prints a path that actually exists.
- [ ] Full suite green vs baseline; ruff clean.

## 5. Known pre-existing flakes — never report as a regression, never fix here

`tests/rpc/test_rpc_client_shutdown.py::test_stop_escalates_to_sigkill_when_sigterm_ignored`,
`tests/rpc/test_rpc_client_containment.py::test_start_raises_a_typed_error_when_the_child_dies_in_the_grace`,
`tests/agents_ext/test_print_channel_spawn.py::test_a_child_that_finishes_at_the_deadline_is_not_a_timeout`,
and assorted tui timing tests under host load. Re-run a named test alone before concluding anything.
Do not run heavy jobs concurrently — that is what makes them fire.
