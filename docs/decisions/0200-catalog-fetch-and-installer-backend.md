# 0200. Catalog fetch classification and the installer backend (W1-A)

Status: Accepted (2026-07-31) — owner-ratified lane selection and owner-ratified
merge. Design record that lands with the W1-A implementation (same pattern as
ADR-0196/0197/0198/0199).
Date: 2026-07-31
Closes: **#111 A-1** (default marketplace catalog 100% unfetchable) and **#113**
(no extension install can succeed on the official install path). Both were
tag-blocking for `v0.1.0-beta.1`: both are shipped Python inside the wheel, and
both are already-shipped-broken **default-on** features.
Builds on: ADR-0005 (marketplace multi-source indexes — this ADR repairs the
classification its source model depends on), ADR-0186 (#32-A marketplace core:
`extension_sources` + install/list/update/remove), ADR-0188 (#65
discover-catalog), ADR-0192 (#76 default-catalog opt-out — **this ADR preserves
the opt-out its design promised, which the naive repair would have silently
revoked**), ADR-0187 (#64 extension-pack signing — the verification path this
ADR refuses to downgrade).
Relates: ADR-0002 (small kernel — **byte-unchanged here**; every edit is in
`aelix_coding_agent/cli/`). ADR-0199 (P3) and the in-flight rpc sprint are
disjoint: this lane touched no file either of them touches.
Source issues: #111 §A-1, #113. Discovered by the 2026-07-26 self-awareness
audit (see #101) as two of the three broken layers of the marketplace leg; the
third, #91, is deliberately **out of scope** (see *Deferred deliberately*).
Implementation plan: `.omc/specs/w1a-catalog-fetch-and-install-backend-plan.md`
(reviewed by two adversarial lenses BEFORE implementation; §11 of this ADR is
the audit trail).
Pi pin: `earendil-works/pi@734e08e`. **AELIX-ORIGINAL** — pi has no marketplace,
no catalog, no `extension install`, and no installer backend. There is no pi
source to be faithful to; every decision below was made here.

---

## 1. Context

Two default-on features shipped broken and stayed broken because nothing in the
test suite could see them.

**#111 A-1.** `classify_target()` decided "is this a git URL?" with a bare
substring test:

```python
or (low.startswith(("http://", "https://")) and ".git" in low)
```

`.git` was matched against the WHOLE URL, so the host
`handochan.` **`git`** `hub.io` matched. `DEFAULT_CATALOG_URL`
(`extension_catalog.py`) is exactly such a host, so `_normalize_catalog_spec`
rewrote it to `git+https://…/catalog.json`, `discover --refresh` tried to
`git clone` a JSON document, and exited 2. The default catalog is the only
registered source and is default-on, so **marketplace discover was 100%
non-functional out of the box**. `raw.githubusercontent.com` breaks identically,
so every third-party GitHub-hosted catalog was affected too.

It survived review and shipped because **every one of the 180 existing
catalog/install tests uses a synthetic host containing no `.git`** — e.g.
`_OFFICIAL = "https://official/catalog.json"`. The suite was green over a dead
feature.

**#113.** `install.sh` installs aelix with `uv tool install`, and **a `uv tool`
venv ships without pip**. `_pip_available()` resolved `importlib.util.find_spec("pip")`
to `None` and the guard aborted with exit 2 *before the consent prompt*, so the
user never even saw what they were about to install. Every
`extension install` / `update` / `discover install` was dead for everyone who
followed the README. The guidance message then advised `python -m ensurepip`,
which is not the remedy for a uv tool venv and is documented nowhere.

## 2. Decision — classification tests the URL PATH, never the netloc

`.git` is now matched against the parsed path only:

```python
path = urlsplit(low).path            # netloc can never contribute
path = _strip_trailing_rev(path)     # pip's "@<rev>" pin
return path.rstrip("/").endswith(".git")
```

The clause is **repaired, not deleted**: `low.endswith(".git")` on the line
above already covers the plain case, but the http branch is what catches
`…/r.git/` (trailing slash) and `…/r.git?ref=main` (query), which `endswith`
misses.

**The blast radius is exactly three classes, and the reverse direction is
empty.** An exhaustive 5,760-URL differential sweep of the old function against
the new one found changes only where `.git` appeared in the netloc
(`*.github.io`, `raw.githubusercontent.com`, and any other `.git`-containing
host). No input moved from `pypi` to `git`; nothing outside that class moved at
all.

### Regression tests pin the real constant

The regression tests **import `DEFAULT_CATALOG_URL`** rather than retyping a
github.io literal. This is the whole lesson of §1: a literal would keep passing
if the constant later moved to a host of a different shape. The end-to-end
assertions (`_normalize_catalog_spec` returns the URL verbatim with no `git+`
prefix; `_catalog_identity` is stable) are pinned the same way.

### The suite became non-hermetic, and that is now gated

Once the default catalog is no longer corrupted, `_effective_default_identity()`
injects a **live https URL** into the effective locations of every discover test
that does not pin the env — and the live fetch SUCCEEDS, so
`test_discover_refresh_all_failed_exits_2` flipped from exit 2 to exit 0. The
pre-implementation review caught this by executing the candidate fix rather than
reading it.

`tests/conftest.py` gains an autouse fixture emptying `AELIX_DEFAULT_CATALOG`
for the whole suite; tests that want a default opt in explicitly (as
`test_extension_discover.py` already did 21 times). This is the one edit outside
the lane's declared file ownership and is ratified here: **the repair is what
made the suite capable of reaching the internet, so the repair owns the fix.**

## 3. Decision — an install backend, with pip preferred

`InstallBackend` selects between two implementations:

| backend | selected when | capabilities |
| --- | --- | --- |
| `pip` | `find_spec("pip")` resolves | install, uninstall, **download-and-verify** |
| `uv` | pip absent **and** an ABSOLUTE `uv` is on PATH | install, uninstall |
| — | neither | actionable abort naming both remedies |

The guard now runs so the user learns **what** was being installed before any
abort, and `_backend_missing_message` names the real remedies
(`uv tool install --with pip aelix`, or a venv that has pip) instead of
`ensurepip`.

`_pip_available()` is **preserved as the named seam it has always been** —
including the injected-runner short-circuit, on which the existing 114-test
cluster depends — and now delegates to `resolve_install_backend()`. `PipRunner`
and its signature are unchanged, because they are an established public seam.

### Verification fails CLOSED on the uv backend

`uv pip` offers `compile / sync / install / uninstall / freeze / list / show /
tree / check`. **There is no `uv pip download`** (verified against the shipped
uv 0.11.14). `build_download_args` — the download → verify integrity pin /
signature → install-from-local-dir path — therefore cannot be reproduced with
uv.

**A verification-requiring install on the uv backend REFUSES with a clear
error.** It does not proceed unverified. Silently skipping a signature check the
user explicitly demanded is worse than failing, and ADR-0187 exists precisely to
make provenance enforceable.

Consequence, stated plainly: a user who installed via `curl | sh` cannot use
`--require-signature` until they add pip. That is a real limitation and it is
the right side to err on.

## 4. Decision — ambient index config travels by ENV, never by argv

The first review round asked that an org's pip index pin survive the switch to
the uv backend (uv ignores `PIP_*` and `pip.conf` entirely). The fix pass
implemented that by translating the ambient config into `--index-url` /
`--extra-index-url` argv — **and thereby created a worse defect than the one it
fixed.**

A corporate `index-url` commonly carries basic-auth
(`https://deployer:hunter2@nexus.corp/simple` is the standard Nexus/Artifactory
form). Moving it from `pip.conf` — a 0600 file pip never exposes — onto argv puts
it in `/proc/<pid>/cmdline` (**mode 0444, readable by every user on the host**)
and into the printed consent block, hence terminal scrollback, the TUI
transcript, and CI job logs.

The final decision:

- **Secrets travel in the child's environment**, via `UV_INDEX_URL` /
  `UV_EXTRA_INDEX_URL`. Env is not world-readable the way `/proc/<pid>/cmdline`
  is, and the org's pin still reaches uv.
- **argv carries no ambient index value at all.**
- Anything rendered to screen goes through `_redact_auth`, which rewrites
  userinfo to `****` (pip's own convention).
- The pip backend gets **no env injection and byte-identical argv to
  pre-#113** — pip reads its own config natively, and emitting flags there would
  be redundant and could diverge from pip's real parsing.

A second review finding on the same surface: a `pip.conf` value can span
configparser continuation lines, letting a hostile or careless config **inject
lines into the consent block** — including a forged `Proceed? [y/N] y` and a
decoy argv line, on the one screen the module treats as the sole consent
surface. Ambient values are now gated by `_is_index_url` (must be printable
http(s)-with-netloc or `file:`-with-path) and rendered through `display_argv`.

## 5. Decision — reproduce pip's precedence model, not an approximation

The translation is only correct if it computes what pip would have computed.
Verified **differentially against a live pip 24.0 oracle**
(`create_command("install").parse_args`), not against the docstring:

- Config files merge LOW→HIGH into one `{section}.{key}` dict, a later file
  **overwriting** a key outright. `extra-index-url` must **not** accumulate
  across files — pip replaces per key, and accumulating re-admits a
  decommissioned mirror into the resolution set, which is a
  dependency-confusion vector the pip backend does not have.
- Section order (`global` → `install` → env) resolves **last**, over the
  already-merged dict. Resolving it per-file inverted the intended outcome: an
  org's system-wide `[install] index-url` lost to any user `[global] index-url`
  — the exact opposite of the feature's stated purpose.
- `PIP_CONFIG_FILE` pointing at an **existing** file makes pip skip the per-user
  config entirely; the candidate list now mirrors pip's `should_load_user_config`.
- Suppression when the user has configured uv deliberately now covers uv's
  **primary** mechanism — `uv.toml`, `pyproject.toml [tool.uv]`, `UV_CONFIG_FILE`
  — not only four env vars.

## 6. Decision — the default-catalog opt-out survives the repair

ADR-0192 gave users an opt-out (`extension source remove <default>`), stored as a
tombstone keyed by catalog identity. Before this repair the default's identity
was the **corrupted** `git+https://…` form. After it, the identity is the clean
`https://…` form — so a naive repair makes the stored tombstone stop matching and
**silently re-enables a network fetch the user deliberately disabled.**

The plan initially wrote this off as acceptable beta churn. The pre-implementation
review refused that, correctly: it is not churn, it is a silent reversal of an
explicit consent decision. `_legacy_suppression_keys` / `_default_is_suppressed`
therefore treat a pre-repair `git+<identity>` tombstone as matching the repaired
identity, and `_effective_catalog_locations` repairs a stored legacy row in
place (one pass, folded with dedupe so the broken row cannot survive an early
return).

The alias is scoped to **the corruption class the bug could actually produce** —
a default whose netloc contains `.git` — rather than being applied to every
non-git default. An enterprise that deliberately points `AELIX_DEFAULT_CATALOG`
at a genuine `git+…` catalog is not collapsed into an https GET.

## 7. Deferred deliberately

- **#91 — entry-point installs drop the pack's `aelix-plugin.toml`.** This is the
  third broken layer of the marketplace leg: even with #111 A-1 and #113 fixed,
  a catalog-installed pack's `contributes.*` families are silently inert, so a
  successful install yields a non-functional extension. Out of scope here
  because the live catalog is `{"extensions": []}` — there are no victims today —
  and because its acceptance criteria need synthetic `.dist-info` test
  infrastructure that has **zero precedent** in the 410-file suite.
  **The marketplace must not be announced as working until #91 lands.**
- Catalog `keywords` / `provides` schema (so "find what registers a Jira tool" is
  answerable by data contract), catalog seeding, and Ed25519 key provisioning
  (`FIRST_PARTY_KEYS` is empty, so catalogs are trust-on-first-use).
- `--json` output for `extension list|discover|source list` — belongs with the
  #101 M3 milestone, which is what makes aelix scriptable by other agents.
- The `_upsert_source` half of the legacy repair (see *Residual risks*).

## 8. Consequences

- `aelix extension discover --refresh` reaches the real catalog over HTTPS. The
  live catalog is empty, so **"empty catalog" is now the success condition**, not
  an error.
- `extension install` works on the `curl | sh` install path for the first time.
- `--require-signature` is unavailable on a pip-less uv tool venv, by design.
- Ambient index precedence on the uv backend now ranks below an explicitly passed
  `--extra-index-url` (env < CLI in uv's model). Previously ambient extras were
  emitted first on argv. Believed a correction, recorded as a behaviour change.
- Test suite: **7,102 → 7,245 passed**, 1 skipped, ruff clean. +143 tests, all in
  `tests/cli/`.

## 9. Residual risks

1. `_patched_environ` mutates process-global `os.environ` for the duration of the
   installer call. Safe today (the CLI install path is single-threaded); a
   threaded installer would see cross-talk. The durable fix is a runner signature
   accepting `env=`, avoided here because `PipRunner` is an established public
   seam used by dozens of tests.
2. Ambient values are restricted to printable http(s)/file URLs. pip is more
   permissive, so an exotic-but-pip-valid `index-url` is dropped rather than
   translated. **Fails safe** (uv falls back to its own default) but is a
   deliberate narrowing versus pip parity.
3. `_uv_config_files` collects every `uv.toml` / `pyproject.toml` walking up from
   cwd, not just the nearest, so an ancestor `[tool.uv]` index key suppresses the
   translation. Conservative direction (suppression = aelix does not override the
   user) but broader than uv's own nearest-project rule.
4. A user who already accumulated **both** catalog rows keeps a dead
   `git+<default>` row in `extensionSources`. `discover` no longer fetches it,
   but `source list` still shows it and `source remove <default>` will not clear
   it by the repaired identity. Cosmetic-plus-confusing, not functional.
5. **Not verified live against a network install**: that `UV_INDEX_URL` /
   `UV_EXTRA_INDEX_URL` actually change the host uv contacts. They are documented
   as the env equivalents in uv 0.11.14's own `--help`, and the earlier round
   proved uv ignores `PIP_*` entirely, but no real install was executed against a
   live index.
6. `_uv_has_own_index_config` adds a few stat/read syscalls per install on the uv
   backend. Negligible except on a pathological deeply-nested cwd over a slow
   network filesystem.

## 10. Landmines for the next reader

- **The venv is an editable install pointing at `/workspaces/aelix-ai`.** Running
  pytest in a worktree WITHOUT `PYTHONPATH` set to that worktree's package `src`
  dirs imports the MAIN repo's source, and a green run proves nothing. The plan's
  own §5 gate had this bug and the pre-implementation review caught it by
  execution. Always print `aelix_coding_agent.__file__` first.
- **Timing tests flake under host load** — `tests/rpc/test_rpc_client_shutdown.py::
  test_stop_escalates_to_sigkill_when_sigterm_ignored` (measured ~2/5 passes at
  idle, 0/2 under CPU load), `tests/agents_ext/test_print_channel_spawn.py::
  test_a_child_that_finishes_at_the_deadline_is_not_a_timeout`, and assorted tui
  timing tests. Pre-existing on main and **not** owned by this lane. Re-run a
  named test alone before calling anything a regression.
- Do not "simplify" the `.git` test back to a substring, and do not strip the
  `@<rev>` pin with `split("@", 1)` — that splits on the FIRST `@` anywhere in the
  path, so `https://gitea.corp/team@eu/ext.git/` classifies as `pypi`. That
  regression was introduced here and caught by review; four regression tests pin
  it.

## 11. Audit trail

Sprint protocol: worktree → committed plan → pre-implementation adversarial
review → implement → verification gate → post-implementation adversarial review
(fresh context) → fix pass → **delta review of the fix pass** → close-out.

The delta review was added after observing that the fix pass had produced ~500
unreviewed lines on a security-adjacent surface. It was not optional: it found
the credential disclosure of §4.

Findings, by round:

| round | findings | notable |
| --- | --- | --- |
| plan review (×2 lenses) | 5 (2 blocking) | the gate command itself was wrong; the fix makes the suite non-hermetic; the opt-out reversal of §6 |
| code review (×2 lenses) | 5 (3 important) | `shutil.which("uv")` returns a RELATIVE path when any PATH entry is relative — a repo carrying `node_modules/.bin/uv` would have its binary executed |
| delta review (×2 lenses) | 8 (1 blocking) | the credential disclosure of §4; three pip-precedence divergences proven against a live pip oracle; consent-block line injection |

**Zero findings were rejected**; all reproduced exactly as reported. Two proposed
*fixes* were replaced with stronger ones (redaction alone does not close the
`/proc/<pid>/cmdline` half; the alias scope was narrowed rather than removed).

Two of the three most serious defects — the PATH hijack and the credential
disclosure — were **introduced by this sprint** and would have shipped had the
review passes been skipped. That is the case for keeping authoring and review in
separate contexts, and it is why the delta round exists.
