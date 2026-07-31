# W1-A — Catalog fetch + installer backend (implementation plan)

Branch `feat/w1a-catalog-fetch-and-install-backend`, worktree `/workspaces/aelix-w1a`, from `main` @ `da61337`.

Closes: **#111 A-1** (default catalog 100% unfetchable) and **#113** (no extension install can succeed on the official install path). Both are tag-blocking for `v0.1.0-beta.1` — they are shipped Python inside the wheel, and both are already-shipped-broken *default-on* features.

## 0. Why these two together, and why this lane now

They live in the SAME file (`cli/extension_install.py`) and share the same network-free test cluster (`tests/cli/test_extension_install.py` 114 tests + `tests/cli/test_extension_catalog.py` 66). Shipping them as separate sprints pays the fixed sprint overhead twice and guarantees a merge conflict.

Lane isolation — an rpc sprint and multi-agent work are in flight. This lane owns **only** `cli/extension_install.py` + `cli/extension_catalog.py` + their tests. It does not touch `cli/entry.py` (contested: the rpc kickoff handoff leaves "how much product-core may this sprint touch" as an open owner fork and cites `cli/entry.py:1346`), nor `tui/`, nor `agents/`, nor `extensions/loader.py`.

**Explicitly deferred to W1-B to keep lanes disjoint:** the `extension discover` omission in `cli/args.py`'s Subcommands block, and the "dormant" narrative in `tui/shell.py:1233,1269` + `tui/extension_manager.py:248,262,328`. This plan corrects only the `cli/` occurrences.

## 1. Defect A — `classify_target` misroutes every GitHub-hosted catalog

`cli/extension_install.py:151-171`. The final clause is

```python
or (low.startswith(("http://", "https://")) and ".git" in low)
```

`.git` is tested as a bare SUBSTRING over the whole URL, so the host `handochan.` **`git`** `hub.io` matches. Live on `da61337`:

```
classify_target("https://handochan.github.io/aelix-marketplace/catalog.json")  -> "git"
classify_target("https://raw.githubusercontent.com/o/r/main/catalog.json")     -> "git"
```

`DEFAULT_CATALOG_URL` (`cli/extension_catalog.py:107`) is exactly the first of those, so `_normalize_catalog_spec` (`:968` — it calls `classify_target`) rewrites it to `git+https://…/catalog.json`, `discover --refresh` git-clones a JSON document, and exits 2. The default catalog is the only registered source and is default-on ⇒ **marketplace discover is 100% dead out of the box**, and `source list` displays the corrupted `git+` identity that `suppressed_default_catalogs` tombstones key off.

Note `low.endswith(".git")` already sits on the line ABOVE and is the correct test for the common case; the substring clause is pure defect. It is not entirely redundant, though — it is what catches `…/r.git/` (trailing slash) and `…/r.git?ref=x` (query), which `endswith` misses. So the clause is *repaired*, not deleted.

### Fix

Test `.git` against the **parsed path only**, never the netloc:

```python
if low.startswith(("http://", "https://")):
    path = urlparse(low).path.rstrip("/")
    if path.endswith(".git"):
        return "git"
```

`urlparse` is stdlib; no new dependency. Add `from urllib.parse import urlparse` (verify it is not already imported).

### Acceptance

| input | expected |
|---|---|
| the REAL `DEFAULT_CATALOG_URL` constant (imported, not retyped) | `pypi` → `classify_source` → `index` |
| `https://raw.githubusercontent.com/o/r/main/catalog.json` | `pypi` |
| `https://foo.github.io/x.json` | `pypi` |
| `https://github.com/o/r.git` | `git` |
| `https://github.com/o/r.git/` | `git` |
| `https://github.com/o/r.git?ref=main` | `git` |
| `git+https://…`, `ssh://…`, `git@host:path`, `…/r.git` (non-http) | `git` (unchanged) |

**The regression test MUST import `DEFAULT_CATALOG_URL` and assert on it.** All 180 existing catalog/install tests pass today over a dead feature precisely because every one of them uses a synthetic host containing no `.git`. A test that retypes a github.io URL as a literal would still pass if the constant later moved to a host with a different shape — pin the constant.

Also assert end-to-end: `_normalize_catalog_spec(DEFAULT_CATALOG_URL)` returns the URL verbatim (no `git+` prefix), and `_catalog_identity(DEFAULT_CATALOG_URL)` is stable.

## 2. Defect B — no install can succeed on the official install path

`install.sh:165` installs aelix with `uv tool install`. **A `uv tool` venv ships without pip.** `_pip_available` (`:299`) → `importlib.util.find_spec("pip")` → `None`, and the guard at `:360-361` / `:1628-1629` aborts with exit 2 **before the consent prompt** — so the user never even sees what they were about to install. Every `extension install` / `update` / `discover install` is dead for everyone who followed the README.

`_pip_missing_message` (`:312`) then advises `python -m ensurepip --upgrade`, which is not the right move for a uv tool venv and is documented nowhere.

### The fork, settled here

Four argv builders hardcode `sys.executable -m pip`:

| line | purpose | uv equivalent |
|---|---|---|
| `:255` | `pip install` base (`build_install_args`) | `uv pip install --python <interp>` ✅ |
| `:551` | `pip install --no-index --find-links` (`_rewrite_pypi_local`) | ✅ |
| `:1632` | `pip uninstall -y` | `uv pip uninstall --python <interp>` ✅ |
| `:540` | **`pip download`** (`build_download_args`) | ❌ **DOES NOT EXIST** |

Verified against the shipped uv (0.11.14): `uv pip` offers compile/sync/install/uninstall/freeze/list/show/tree/check. There is no `download`.

`build_download_args` is the **verification** path: download the full closure → check the integrity pin / signature → install from the local dir. It cannot be reproduced with `uv pip`.

**DECISION — fail closed, never silently downgrade.** Introduce an `InstallBackend` with two implementations:

- `pip` — full capability (install, uninstall, download-and-verify). Preferred whenever `importlib.util.find_spec("pip")` resolves.
- `uv` — install + uninstall only, via `uv pip … --python sys.executable`. Selected when pip is absent and a `uv` executable is on PATH (`shutil.which("uv")`).

A verification-requiring install (`--require-signature`, or any path routed through `build_download_args`) on the `uv` backend must **refuse with a clear, actionable error** — not proceed unverified. Silently skipping a signature check the user explicitly demanded is worse than failing.

Non-verifying installs on the `uv` backend proceed normally.

### Also fix

- **Guard placement.** Move the availability check so the target and its source are shown before the abort, or at minimum name them in the error. Aborting before consent hides what was being attempted.
- **`_pip_missing_message`** → a backend-aware message naming the real remedies: `uv` is on PATH but unusable / neither pip nor uv found → `uv tool install --with pip aelix`, or install into a venv that has pip.
- **Naming.** `_pip_available` / `PipRunner` keep their names for API stability where they are already exported (`:130`), but the selector must not be pip-shaped internally. Prefer adding `resolve_backend()` beside them over renaming the exported symbols in this sprint.

### Testability

`PipRunner = Callable[[list[str]], CompletedProcess[bytes]]` (`:125`) is an injectable seam, and `_pip_available` already short-circuits to `True` when a custom runner is injected — so every existing test bypasses the real check. **Preserve that short-circuit**: injecting a runner must continue to mean "the caller owns backend availability", or the whole 114-test cluster breaks.

New tests must cover backend RESOLUTION (pip present → pip; pip absent + uv present → uv; neither → actionable error) by monkeypatching `find_spec` / `which`, and argv SHAPE per backend. Zero network.

## 3. Narrative corrections (this lane's files only)

Four sites still assert the default catalog is an empty dormant placeholder. All false since `00af324` wired `DEFAULT_CATALOG_URL`, and all of them actively steer a reader away from finding Defect A — which is how it survived to ship.

- `cli/extension_catalog.py:165,170` — *"In beta the placeholder is empty, so … returns None (dormant — mechanism only)"*
- `cli/extension_install.py:1003-1004` — *"placeholder; empty = dormant"*

Correct the prose to describe the live default. Also correct `classify_target`'s own docstring (`:152-158`), which currently documents the buggy substring rule as intended behaviour.

## 4. Work breakdown — DISJOINT file ownership

| WS | Owns | Scope |
|---|---|---|
| **WS-1** | `cli/extension_install.py` §classify + `cli/extension_catalog.py` + `tests/cli/test_extension_catalog.py` | Defect A + its regression tests + catalog-side narrative |
| **WS-2** | `cli/extension_install.py` §backend + `tests/cli/test_extension_install.py` | Defect B + backend tests + install-side narrative |

Both edit `cli/extension_install.py`, so they are **sequential, not parallel**: WS-1 first (smaller, and its docstring edit sits directly above WS-2's region), then WS-2 rebases on it. Do not run them concurrently.

## 5. Verification gate

- `source /workspaces/aelix-ai/.venv/bin/activate && python -m pytest -q` — full suite green (baseline at `da61337` to be captured BEFORE any edit)
- `ruff check .` clean
- Live probe, recorded in the PR body: `classify_target(DEFAULT_CATALOG_URL)` → `pypi`, and `aelix extension discover --refresh` reaches HTTPS (the live catalog is `{"extensions": []}`, so "empty catalog" is the SUCCESS condition here, not an error)
- Backend probe: with `find_spec("pip")` monkeypatched to `None` and `uv` on PATH, an install builds `uv pip install --python …` argv
- A `--require-signature` install on the `uv` backend refuses rather than proceeding

## 6. Out of scope (stated so review does not drag it in)

- **#91** (entry-point installs drop `aelix-plugin.toml`) — Layer 3 of the marketplace break. A successful install still yields an inert extension for `contributes.*` packs. Deliberately NOT in this sprint; the catalog is empty so there are no victims today.
- Catalog `keywords`/`provides` schema, catalog seeding, Ed25519 key provisioning (`FIRST_PARTY_KEYS` is empty).
- `--json` output — belongs with M3.
- Everything in W1-B and W2.

## 7. Landmines

- `DEFAULT_CATALOG_URL` flows through `_normalize_catalog_spec` → `_catalog_identity` → tombstone keys. Changing the classification changes the stored identity, so **a user who already has a tombstone or a stored source under the corrupted `git+…` identity will not match the repaired one.** Decide and document: is a migration needed, or is beta churn acceptable? Given no release has been tagged, beta churn is acceptable — but say so explicitly in the ADR rather than leaving it silent.
- Preserve `_pip_available`'s injected-runner short-circuit or the 114-test cluster breaks.
- `classify_source` delegates to `classify_target`; confirm the repaired classification still maps a plain https catalog URL to `index` and not to something else.
