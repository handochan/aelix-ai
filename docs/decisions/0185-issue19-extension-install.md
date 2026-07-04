# ADR-0185 — #19: `aelix extension install <path|git|pypi>` (minimal, pip-based)

- **Status:** Accepted — **LIVE**.
- **Date:** 2026-07-04
- **Sprint:** Marketplace foundations — the minimal install command (registry index + marketplace UI deferred to #32).
- **Pi pin:** `earendil-works/pi@734e08e`. Ecosystem-swap of pi's npm-based package manager (see Decision).
- **Relates:** #7 (marketplace epic, parent), #9 (ext command dispatch), ADR-0149 (Project Trust), ADR-0181/0182/0184 (manifest + loader discovery tiers). GitHub #19; follow-up #63.

## Context

`aelix extension install <target>` should place an extension so the existing loader picks it up, support **self-hosted git/index sources** (closed-network is a HARD requirement), and stay minimal (no registry/UI). A 5-axis design recon surfaced two premise corrections that reshaped the approach:

1. **Target dir was wrong in the issue.** The issue said `~/.aelix/extensions/`, but the shipped CLI passes `agent_dir=get_agent_dir()` into discovery, so the *real* global scan dir is `~/.aelix/agent/extensions/` (honoring `$AELIX_CODING_AGENT_DIR`). `~/.aelix/extensions/` is a dead path discovery never reads.
2. **Directory-drop silently no-ops for the plugins that matter.** The loader resolves a manifest's `entry.python` via `importlib.import_module` and **never adds the plugin dir to `sys.path`**. So git-cloning/copying a manifest+module plugin into the extensions dir does NOT make its module importable — the import fails and is swallowed per-entry into `LoadExtensionsResult.errors` (no crash, no load). Directory-drop only works for self-contained single-file `.py` extensions (loaded via `spec_from_file_location`), which forfeit the manifest.

## Decision

**pip-based, unified across all three source kinds** (owner-approved). `install` resolves path/git/pypi to a single `sys.executable -m pip install …` into the running interpreter's environment:

- **path** → `pip install <abspath>` · **git** → `pip install "git+<url>"` (scheme auto-prefixed) · **pypi** → `pip install <spec> [--index-url <url>]`.
- pip makes the module importable AND the loader's **Tier-4 `entry_points(group="aelix.extensions")`** pass discovers the plugin's factory — no bespoke registry, no `sys.path` machinery. **`importlib.metadata` is the discovery ledger** for v1: a future `aelix extension list` enumerates installed `aelix.extensions` entry points and `remove` shells `pip uninstall` — no separate record file is needed *for discovery*. (Divergence from pi, which persists a declarative package *source* list to `settings.json` and re-installs on resolve — aelix v1 deliberately defers that source-list, so `update`/reinstall-from-source is a follow-up, not a v1 guarantee. Review LOW: the earlier "pip is the ledger" framing overstated parity.)
- **Closed-network:** pip's own `--index-url` (self-hosted PyPI mirror), `--find-links`, `git+file://`, and `ssh://` git URLs carry the air-gapped/self-hosted requirement natively — pi delegates to `.npmrc`; aelix delegates to pip config, no invented index layer (pi-faithful).
- **Consent is source-level, deny-by-default.** pip runs the package's build/setup code (arbitrary, at install time, *before* any manifest is visible), so a capability gate is structurally impossible on this path — the shown source + `y/N` (with `--yes` for headless; a closed stdin denies) IS the trust boundary. Placing into the global env is an implicit trust grant, consistent with the loader treating global/entry-point extensions as ungated (ADR-0149 gates only project-local).
- **`--offline`** (+ `PI_OFFLINE`/`AELIX_OFFLINE` env) refuses a pypi-without-`--index-url` install (the one clearly-public network path) with an actionable message; path / `git+file` / `ssh` are offline-safe.

**CLI surface:** `aelix extension install <target>` **verb**, dispatched at the top of `_async_main` BEFORE `parse_args` (the hand-rolled flat flag parser — pi byte-parity — would otherwise swallow `extension`/`install` as chat positionals). A do-a-thing-and-exit action in the spirit of `--list-models` / `--export`. New module `cli/extension_install.py` owns classification, pip-arg building, consent, and the sub-arg parse; zero change to the flag parser's architecture.

**Pi parity:** the Python-ecosystem swap of pi's `package-manager.ts` — pip replaces `npm install`, `entry_points` replaces the `PiManifest` package root, `--index-url` replaces `.npmrc`. Faithful in shape; ecosystem-specific in mechanism.

## Consequences

- `aelix extension install` works end-to-end for pip-installable extensions (packages exposing the `aelix.extensions` entry point) from a local path, a self-hosted/`file`/`ssh` git URL, or a (self-hosted) index. Restart / `/reload` picks them up.
- **Scope boundary (documented):** this installs *entry-point packages*. Pure manifest-declarative plugins (a bare `aelix-plugin.toml` dir with `contributes.*` and no package/entry-point) are still added the manual way (drop the dir in the extensions path) — automating that needs `sys.path`/`.pth` machinery the loader deliberately doesn't own; deferred.
- **Prerequisite: pip on the running interpreter.** `install` shells `sys.executable -m pip`, so a pip-less environment (notably a **uv-managed venv, which ships without pip by default**) cannot install — the command detects this up front and exits with an actionable hint (`python -m ensurepip` / `uv pip install pip`) rather than a confusing pip-missing traceback. A future `uv pip` fallback is possible.
- **Exit codes:** `0` installed · pip's own returncode (usually `1`) = pip ran and failed · `2` = never ran pip (usage error, guard refusal, user abort, missing pip). The 3-way split lets a script tell "pip failed" from "never ran" — a deliberate divergence from the repo's return-1-for-errors idiom toward the standard-CLI usage-error code.
- **pip config/env is honored, by design.** The consent line shows the pip argv, but `PIP_INDEX_URL` / `PIP_CONFIG_FILE` / a `pip.conf` still apply (as with any pip run) — this is *wanted* for the closed-network case (a site's `PIP_INDEX_URL` points pip at the self-hosted mirror), so the env is NOT sanitized. `--offline` is advisory (it refuses only index-less pypi — the clear public-network path); it does not sandbox git/`--index-url` hosts.
- Deferred to follow-ups: `list` / `remove` / `update` + a persisted source-list (pi's `settings.json` model), a bespoke self-hosted-index config surface (pip config suffices for v1), a `uv pip` fallback, and manifest-dir install.
- **Gate:** pytest 4695 pass / 1 skip · ruff clean · pyright 8 pre-existing `scripts/pyright_spike.py` errors only.

## Adversarial review

A 4-lens adversarial review (correctness / security / consistency / test-adequacy — Opus, 27 agents, over the uncommitted diff) ran before commit: 23 findings survived skeptic-verify, 0 refuted; all addressed. The design (pip-based, source-level consent, verb dispatch) was upheld.

**Confirmed defects fixed:**
- **[MEDIUM] Empty-string target installed the whole cwd** — `Path("").exists()` is true and resolves to the cwd, so `aelix extension install "" --yes` (realistic from an unexpanded `$EXT` in automation) pip-installed the current directory. Fixed: empty/whitespace targets are rejected (classification, `install_extension`, and the arg parser all guard it) + tests.
- **[LOW] scp-style git URL was unparseable by pip** — `git@host:path` (no `://`) got a bare `git+` prefix, which pip rejects at requirement-parse time. Fixed: `_normalize_git_spec` rewrites the scp shorthand to `git+ssh://git@host/path`.
- **[LOW] Dead missing-pip branch** — a missing pip makes `python -m pip` exit nonzero *without* raising, so the `FileNotFoundError` catch never fired and the user got a generic "pip install failed (exit 1)". Fixed: an up-front `importlib.util.find_spec("pip")` pre-check (default-runner only) → actionable exit 2. This surfaced the **uv-venv-has-no-pip** prerequisite now documented above.
- **[LOW] Abort ≡ pip-failure exit code** — both were `1`. Fixed with the 3-way scheme above (abort → 2).
- **[LOW] `--index-url=` empty value silently dropped** — now rejected like the space-separated form.
- **[NIT] `PI_OFFLINE=0` engaged offline** — loose `bool()` truthiness; now strict `1/true/yes/on`.
- **[NIT] No `--` end-of-options** — a path starting with `-` couldn't be installed; `--` now forces positional.
- **[NIT] No `__all__`** — added.

**Documented, not code-changed** (design calls upheld): PIP_* env is honored on purpose (closed-network), `--offline` is advisory, local-path-wins can shadow a bare pypi name (consent shows the resolved abspath), and exit-2-for-usage is a deliberate standard-CLI choice — all captured in Consequences above. **Tests +25** (54 total): empty-target, scp-git, missing-pip, consent accept/decline matrices, offline path pass-through, `PI_OFFLINE=0`-off, `--index-url=` empty, `--offline`/`--`/`--help` parsing, and a non-`extension` argv fall-through regression.
