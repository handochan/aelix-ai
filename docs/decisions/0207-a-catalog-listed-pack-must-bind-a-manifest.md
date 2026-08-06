# 0207. A catalog-listed pack MUST bind a manifest; `aelix extension verify` is the gate

Status: Accepted (2026-08-06).
Date: 2026-08-06
Builds on: ADR-0204 (issue #91 — an installed pack's `aelix-plugin.toml` is
resolved from installed metadata WITHOUT importing the pack; this ADR turns that
per-endpoint resolution into an operator-facing gate and a policy), ADR-0206
(the declarative gates that make a bound manifest meaningful), ADR-0188 (the
advisory catalog — "listed" never meant "reviewed" or "safe", and still does
not).
Relates: ADR-0205 (the capability table a bound manifest is checked against).
GitHub #91.
Pi pin: `earendil-works/pi@734e08e`.

**Provenance.** pi has no manifest, no capability vocabulary, and no catalog, so
the contract, the verb, and the policy are all **aelix-original**. Nothing here
is parity restoration or a parity divergence.

---

## The problem

Issue #91 made an installed pack's manifest *readable* without importing the
pack. It left three gaps.

1. **The failure is invisible.** A `setuptools` build with default configuration
   ships `*.py` and silently DROPS `aelix-plugin.toml` and `themes/*.toml` from
   the wheel (MEASURED — hatchling ships them, setuptools default does not). The
   pack then installs, `setup()` runs, the product reports success — and every
   declarative contribution is inert, because the manifest is not in the wheel to
   be read. The `#91` resolver classifies this precisely (`EpOutcome.ABSENT`),
   but at startup ABSENT is the ordinary "ships no manifest" case and stays
   quiet, so nothing tells the operator their contributions vanished.

2. **There is no primitive to gate on.** A marketplace distributes *declarative,
   auditable, gated* contributions. An entry-point pack that yields no manifest
   is a black box: it can still register anything imperatively from `setup()`,
   but nothing about it is inspectable before its code runs. Nothing existed to
   assert "this installed pack yields a bound manifest".

3. **There is no scaffold.** The authoring guide named no build backend, and
   `examples/` had no packageable project, so an author's most likely first
   build is the one that drops the manifest.

## The decision

### The verb — `aelix extension verify`

A new subcommand runs the loader's own import-free path — the sys.path
provenance fence (`entry_point_provenance`) then the metadata-only resolver
(`resolve_entry_point_manifest`) — over every installed `aelix.extensions`
endpoint (or one named target), reports each endpoint's binding verdict, and
returns a **stable exit code**:

- **0** — every reported endpoint is `BOUND`.
- **1** — at least one is not: `ABSENT`, `MALFORMED`, `MISPLACED`, `FENCED`,
  `UNPROVEN`, `UNTRUSTED`, or API-incompatible — i.e. the loader would drop that
  pack's declarative contributions.
- **2** — it did not get as far as verifying: a usage error, or a named target
  that matches nothing installed.

It **does not import the pack** (asserted by an import-marker test), so it is
safe to run in CI against an untrusted candidate. The `ABSENT` report carries a
hint naming the real cause — the setuptools default drop — and the fix
(package-data, or hatchling). `aelix extension list` annotates the same verdict,
so "installed, no manifest, declarations inert" is visible there too. Startup
stays quiet: `list`/`verify` are the surfaces, not a per-start nag.

### The policy — mandatory for a catalog listing, optional otherwise

An `aelix-plugin.toml` is:

- **MANDATORY** for a pack listed in the **official catalog**. "Mandatory" means
  the installed distribution must yield a **BOUND** manifest — it parses and is
  schema-valid — NOT that it must declare `>= 1` contribution. A manifest that
  binds and declares nothing is fine; a pack that yields no bound manifest is
  not catalog-eligible.
- **OPTIONAL** for an arbitrary `pip install <pkg>` pack and for every non-catalog
  channel (`-e <file>`, a directory, a git URL). These are unchanged: a bare
  `.py` with a top-level `setup(aelix)` is still a complete extension.

Rationale: a marketplace distributes declarative, auditable, gated contributions;
a manifest-less entry-point pack is a black box. Requiring a bound manifest is
what lets the catalog promise its entries are inspectable before their code runs.

### The reversibility asymmetry (why now, and why this direction)

The extension-pack population is **0** — there are no installed packs and no
catalog listings today. Tightening the catalog rule now is therefore **free**:
it breaks nothing, because there is nothing to break. And the two directions are
not symmetric:

- **Loosening later never breaks anyone.** If we start mandatory and later allow
  manifest-less catalog entries, every existing listing (all of which have a
  bound manifest) still satisfies the looser rule.
- **Tightening later breaks existing listings.** If we start optional and later
  require a manifest, every already-listed manifest-less pack becomes
  non-compliant at once.

So the only cost-free, only-loosenable-later choice is to require the manifest
now. This is a one-way door taken while the room behind it is empty.

## The `BOUND != safe` caveat

A bound manifest is **not** a safety verdict and `verify` is **not** a security
scan. `BOUND` means only: a RECORD-owned, fence-passing, parseable,
schema-valid `aelix-plugin.toml` sits on the entry module's path, and the
loader will read it. It says nothing about what the pack's `setup()` does when
imported — a plugin is in-process Python, and `capabilities.fs_write = false` is
a *declaration of intent*, not a sandbox (ADR-0205). Six of the nine capabilities
are documentation, not gates. "Listed in the catalog" continues to mean exactly
what ADR-0188 says it means: present in an advisory index, never "reviewed" or
"safe". `verify` raises the floor from "black box" to "auditable declaration";
it does not certify the code behind the declaration.

## The external-marketplace-repo CI handoff

The catalog (`catalog.json`) is hosted in a **separate** repository —
`handochan/aelix-marketplace` — not in this one. This repo has no catalog CI
(`.github/workflows/` holds `ci.yml`, `pages.yml`, `release.yml` only), so the
submission gate **cannot be wired here**. This ADR ships the *mechanism*; the
*wiring* is a handoff to that repo.

The submission check that repo must run, per candidate, in a clean environment:

```bash
python -m venv /tmp/verify-env
/tmp/verify-env/bin/pip install "<the submission's source spec>"
/tmp/verify-env/bin/pip install aelix-coding-agent
/tmp/verify-env/bin/aelix extension verify "<entry-point-or-dist-name>"
# exit 0 => BOUND, admit the listing; non-zero => reject with the printed reason
```

The verify run must happen in the environment the pack was installed into (so
its distribution and entry point are visible to `importlib.metadata`), against
the exact `source` spec the catalog entry declares. `verify` imports no plugin
code, so running it on an untrusted submission is safe. Reject on any non-zero
exit and echo the tool's per-endpoint reason into the PR.

## Consequences

- `aelix extension verify` and the `list` annotation exist and are import-free.
- A copyable hatchling scaffold ships at
  `packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/`, proven
  by test to build a wheel that contains its manifest and to resolve `BOUND`.
- The authoring guide gained a "Packaging your extension" section naming both
  backends and the setuptools `package-data` requirement.
- The catalog policy is documented but its CI is a handoff to
  `handochan/aelix-marketplace`; until that wiring exists, the rule is advisory
  in practice and enforced by review.
