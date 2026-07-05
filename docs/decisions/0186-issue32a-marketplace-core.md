# ADR-0186 — #32-A: extension marketplace core (sources + list/update/remove, pip-based)

- **Status:** Accepted — **LIVE**.
- **Date:** 2026-07-05
- **Sprint:** Marketplace — the pi-parity core (Option A, owner-approved 2026-07-05). Signing/hash + a discover-catalog are split to separate follow-up ADRs.
- **Pi pin:** `earendil-works/pi@734e08e`. Ecosystem-swap of pi's package model: `extension_sources` mirrors pi's `settings.packages` `PackageSource[]`; pip replaces npm.
- **Relates:** #7 (marketplace epic, parent), #19/ADR-0185 (the `install` primitive this builds on), #44/ADR-0174 (settings_manager seam), ADR-0149 (Project Trust), WP-8 (the `/extension` TUI viewer). GitHub #32.

## Context

#19 (ADR-0185) shipped `aelix extension install <path|git|pypi>` — a pip-based installer where pip is the discovery ledger (`entry_points(group="aelix.extensions")`). #32 asks for the *marketplace* on top. A 5-agent design recon established that the real net-new is only three pieces, and only ONE is pi-parity:

1. **pip already provides** install + version/dependency resolution + a self-hosted index (`--index-url`) + closed-network — so there is nothing to build there.
2. **A persisted source list** (register sources; resolve bare-name installs against them; `list`/`update`/`remove` the installed set) — this is the pi-parity core (`settings.packages`). **Option A** (owner-approved).
3. **A discover-catalog** (browse a marketplace by name) and **signing/hash** (ADR-0005's abandoned Q2 blocker; pi has neither) are aelix-original, each needing its own format/crypto decision → **split to separate follow-up ADRs**, out of scope here.

Premise the recon corrected: ADR-0005 is prose-only and abandoned its own bespoke pack/index classes; pi has ZERO signing. So A is not "port pi's signed marketplace" — pi has none. A is: give pip a persisted, aelix-shaped source list and the `list`/`update`/`remove` verbs pip alone doesn't track by *source*.

## Decision

**A persisted `extension_sources` list on `SettingsManager` + five new `aelix extension` verbs, all resolving to pip.** (Owner-approved Option A.)

**Persistence — a NEW `extension_sources` field, DISTINCT from pi's `packages`.** A source is `{spec, kind}` where `kind ∈ {index, git, path}` (+ an internal `pypi` for a recorded bare-name install, and an optional `name` = the dist captured on install). Stored GLOBAL-scope on `SettingsManager` (mirroring `set_enabled_models`), serialized as `extensionSources` (camelCase) through the same JSON boundary as `packages`. It is deliberately NOT pi's `packages`: pi's `PackageSource` is an npm-package-with-sub-resources model (`{source, extensions, skills, prompts, themes}`); an aelix source records only WHERE to install FROM. Conflating them would put a `kind` on the npm shape and a sub-resource filter on the pip-source shape — both nonsensical.

**Command surface** (all under the `extension` verb dispatched at the top of `_async_main`, extending #19's seam):
- `source add <path|git-url|index-url>` — **register-only** (owner-decided pure 2-step: add ≠ install). Classified by `classify_source` (path/git/index; a plain http(s) URL → `index`, a bare name is rejected — that is an install *target*, not a source). Idempotent (dedupe by resolved identity). Runs no pip, prompts nothing.
- `source list` / `source remove <spec>` — manage the list.
- `install <target>` — #19's installer ENHANCED: a bare NAME with no explicit `--index-url` folds the registered **index** sources into pip's index set (first → `--index-url`, rest → `--extra-index-url`); git/path/url install directly. On success the install is RECORDED (so `update` can reinstall it) — best-effort, never fails the install.
- `list` — the installed inventory, read straight from `entry_points(group="aelix.extensions")` (the pip ledger — no separate record).
- `update [<name>]` — reinstall a recorded source with `--upgrade` (git → `git+url`; path → the path; pypi → the bare name + index sources). No name = every recorded installable; an `index`-kind source is a resolution hint and is skipped. An unrecorded `<name>` is upgraded as a bare pypi package (covers pre-feature installs).
- `remove <name>` — map `<name>` (entry-point OR distribution, loosely canonicalized) → its distribution via `EntryPoint.dist`, `pip uninstall -y`, then drop any matching recorded source.

**Async seam (net-new machinery).** `SettingsManager` writes are async (`_enqueue_write` schedules on the loop; `flush()` awaits). A do-and-exit CLI must therefore `await settings.flush()` after any mutation or the write never lands. So the dispatch is `run_extension_command_async` (awaited directly from `_async_main`, already in the `asyncio.run` loop — a nested `asyncio.run` would raise); a thin sync `run_extension_command` shim (`asyncio.run(...)`) preserves the direct/test entry. `settings` is injectable (`SettingsManager.in_memory()` in tests) so the surface is unit-testable off disk.

**Consent** stays #19's model: `install`/`update` run pip build code, so source-level `y/N` deny-by-default (`--yes` headless); `source add`/`list`/`remove`/`list` touch no build code and never prompt.

**TUI (`/extension`).** The **Sources** tab renders the LIVE persisted list (read at open time via a `SettingsManager`-backed getter, so a CLI-added source shows on the next open); the viewer stays **read-only** (no runtime enable/disable API exists — extensions load once at startup). **Discover** stays honest static text (the deferred catalog); **Installed** stays the read-only entry-point inventory.

## Consequences

- A user can register a self-hosted index / a git repo / a local path once, then `install`/`update`/`remove`/`list` extensions against it — the pi-parity package-management loop, over pip, closed-network-native.
- **`extension_sources` ≠ `packages`.** Two coexisting fields; the aelix-original one is the install-source model. pi's `packages` field is untouched (it was already present but dormant for install).
- **Recording is best-effort.** The dist-name captured on a git/path install uses a before/after `entry_points` diff (`importlib.invalidate_caches()` first — a same-process install is otherwise invisible to `importlib.metadata`); if the diff misses, `name` is `None` and `update <name>` falls back to treating `<name>` as a bare pypi package. A missed record only degrades `update`, never the install/removal.
- **Scope boundary.** No signing/hash (ADR-0005 Q2 — separate follow-up ADR; verify must be pre-pip), no discover-catalog (separate follow-up — needs a catalog format/hosting decision), no runtime enable/disable toggle (no such API; only coarse `/reload`). The Sources tab is read-only for the same reason.
- **Prerequisite: pip** (inherited from #19 — a uv venv ships without pip; `install`/`update`/`remove` detect it up front → exit 2 + hint).
- **Exit codes** inherit #19's 3-way split (0 ok · pip returncode · 2 never-ran).
- **Gate:** pytest (full suite) pass · ruff clean · pyright 0 errors on changed source.

## Adversarial review

A 4-lens adversarial review (correctness / security / pi-parity-consistency / test-adequacy — Opus, high-effort, 9 agents over the uncommitted diff, every finding skeptic-verified by an independent agent) ran before commit. **The correctness, security, and pi-parity lenses each returned ZERO findings** — the control flow (source dedupe/resolution/recording, update aggregation, remove mapping), the consent/offline gates, and the four-path JSON serialization were upheld. The test-adequacy lens filed 5 findings (4 confirmed, 1 refuted); all 5 were addressed.

**Confirmed test-adequacy gaps fixed (+7 tests):**
- **[MEDIUM] The `await flush()` invariant was unguarded at the handler layer.** `set_extension_sources` updates the merged view SYNCHRONOUSLY, so every in-memory handler test passed even if a handler dropped `await settings.flush()` — yet on a real disk-backed process the write would be lost on exit. Fixed: two disk-backed round-trip tests (`source add` + install-record) that reopen a FRESH manager over the same file and assert presence — these fail if any handler drops the flush.
- **[MEDIUM] Install-record dist-name capture (the `detected != None` branch) was never exercised** — the fake runner installs nothing, so the before/after ledger diff was always empty. A regression inverting the diff would ship `name=None` and break `update <name>`. Fixed: a test that stubs `list_installed_extensions` to change across the install and asserts the recorded `name`.
- **[MEDIUM] `update`-all failure aggregation was untested** — no test had two installable sources where the first fails, so a regression to last-wins or stop-on-first-failure would pass. Fixed: a two-source failing-runner test asserting exit `1` AND that both sources were attempted.
- **[LOW] The malformed-entry decode drop had no load test.** Fixed: a decode test proving non-dict / spec-less junk entries are dropped and only well-formed sources survive.

**Refuted (documented, hardened anyway):**
- **[LOW] project-scope test hermeticity** — the autouse fixture pinned only the GLOBAL settings path; the project scope (`cwd/.aelix/settings.json`) was unredirected. Verify ruled it inert (no repo-root `.aelix/` exists), but the fixture now also `chdir`s into the throwaway dir to fully decouple `settings=None` tests from the developer's cwd.

**Additional self-found fix (not from the panel):** git specs were normalized inconsistently — `source add <raw-url>` stored the raw URL while an install-record stored `git+<url>`, so registering then installing the same repo produced a duplicate entry and dedupe missed. Fixed: `_source_identity` normalizes git via `_normalize_git_spec`, `source add` stores the normalized spec, and `_remove_source` matches a target against its raw / path-resolved / git-normalized candidate identities (so removal works whichever form the user types). +2 regression tests.

**Gate:** pytest 4750 pass / 1 skip · ruff clean · pyright 0 errors on changed source.
