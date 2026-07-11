# ADR-0192 — #76: beta release track (v0.1.0-beta.1) + default marketplace catalog (opt-out, signed)

- **Status:** Accepted (2026-07-10) — scheme owner-confirmed (Run 2). Design record;
  lands with the #76 beta implementation, precedes the code and seeds it (same pattern
  as ADR-0186/0187/0188/0189).
- **Date:** 2026-07-10
- **Sprint:** #76 first public release = **beta**. Run 1 was recon; this is Run 2
  (design). Two coupled tracks: **(A)** a `curl | sh` + GitHub Releases beta channel
  with PyPI deferred to GA, and **(D)** a signature-gated default marketplace catalog
  (opt-out). A from-scratch aelix-original decision on both tracks; there is NO pi
  parity anchor.
- **Supersedes (partial):** **ADR-0188 §4(c)** — the *registration deny-by-default*
  clause only (the "(iii)" row). §4(c)'s **(i) no-hardcoded-URL** and **(ii) no-auto-fetch /
  no-auto-discovery** prohibitions stay **binding**; §4(a) display-only-`sha256` pin
  invariant, §4(b) resolved-spec-at-consent, and §4(d) deferred cross-check are
  **untouched**.
- **Pi pin:** `earendil-works/pi@734e08e`. CONFIRMED: pi has neither a release track of
  this shape nor a marketplace catalog — its distribution is `npm`/npx and its
  `packages` model registers KNOWN sources only. So #76 ports nothing on both tracks;
  it designs schemes. No parity obligation.
- **Relates:** #76 (this), ADR-0188 (#65 discover-catalog — §4(c) partially superseded
  here; the `kind="catalog"` source model, the `extension_catalog.py` module, and the
  `--refresh`/`--offline`/cache-only seams are reused verbatim), ADR-0187 (#64 hash-pin
  + TOFI — the two-seed pin invariant this must not weaken), ADR-0189 (#67 Ed25519
  provenance — the `extension_signing` trust store + `_verify_raw`/`resolve_public_key`
  the guardrail-⑤ shim is extracted from), ADR-0186 (#32-A `extension_sources` +
  `SettingsManager` persistence seam), ADR-0185 (#19 install primitive + consent
  boundary), ADR-0031 (hatchling build backend), ADR-0015 (monorepo — the four
  mutually-`==0.1.0`-pinned first-party dists the installer must carry), ADR-0010 (trust
  stays source-specific — a LIVE constraint), ADR-0005 (**REINTERPRETED**, not
  superseded — see Governance). GitHub #76. Follow-ups: #73 (PyPI pending-publisher —
  deferred to GA), #68 (authenticated-catalog per-entry `sha256` fail-closed cross-check
  — still deferred; #76 delivers document-level verify only).

## Owner decisions (confirmed 2026-07-10, Run 2)

1. **Beta tag = `v0.1.0-beta.1` (PEP 440 prerelease).** The PyPI `publish` job is gated
   `if: ${{ !contains(github.ref_name, '-') }}` → a hyphenated (prerelease) tag SKIPS
   PyPI entirely, and only the new `github-release` job fires, creating a
   `--prerelease` GitHub Release. **#73 PyPI pending-publisher setup is NOT needed for
   beta** (no PyPI publish happens); it is deferred until the first GA (non-hyphen) tag.
2. **Catalog signing = a real verification shim, in beta scope.** A public
   `verify_signed_document(...)` shim is extracted from the #67 `extension_signing` code
   and used to **actually verify** the fetched `catalog.json` bytes of the OFFICIAL
   catalog against a trusted key. The signature is a **real defense (fail-closed)**, not
   decoration: an official catalog whose bytes do not verify is dropped, never rendered.

## Context

Run 1 recon (2026-07-10) established the ground truth on both tracks.

**Track A — the release pipeline cannot ship a beta.** `release.yml` today has a `build`
job (`uv build --all-packages`, then `rm -f dist/aelix_server-*` to drop the deferred
Web-UI daemon, then `upload-artifact`) and a `publish` job (`pypa/gh-action-pypi-publish`,
`environment: pypi`, `id-token: write`, Trusted Publishing). The tag glob
(`v[0-9]+.[0-9]+.[0-9]+*`) and the build-job validate regex
(`^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.]+)?$`) already ACCEPT `v0.1.0-beta.1`. But:
zero tags have ever been pushed; there is **no `if:` gate on `publish`** (a beta tag
would push straight to PyPI); and there is **no GitHub Release creation, no checksum
step, and no `install.sh`** at all. The console script is `aelix` →
`aelix_coding_agent.cli.entry:main_sync` (declared by `aelix-coding-agent`, re-declared
by the root umbrella `aelix`). The four first-party dists (`aelix`, `aelix-coding-agent`,
`aelix-agent-core`, `aelix-ai`) are mutually pinned at `==0.1.0`; third-party deps
(`cryptography`, `pillow`, `pydantic-core`, `tree-sitter`, …) are NOT vendored and must
come from PyPI, so any installer must be a **hybrid** — local first-party wheels via
`--find-links` + third-party from the index — and `--no-index` is FORBIDDEN.

**Track D — a fresh install has an empty marketplace.** ADR-0188 shipped the
discover-catalog (`kind="catalog"` sources, `discover [--refresh]`, the filterable TUI
Discover tab), but its §4(c) pins "**No hardcoded/auto-discovered default catalog —
deny-by-default operator config**". So out of the box, `source list` is empty and the
Discover tab shows the honest empty-state — a new user has nothing to browse. #76 wants
a first-party default catalog present by default **without** re-opening the air-gap /
폐쇄망 constraint that §4(c) was protecting, and **without** turning ADR-0188's
display-only-`sha256` advisory model into a fake "green" trust signal.

The crux: §4(c)'s single sentence bundles **three** distinct prohibitions, and #76 needs
to reverse exactly **one** of them.

## Decision

Ship both tracks. Track A gates the existing PyPI path behind a prerelease check and adds
a GitHub Releases beta channel. Track D reverses ONLY §4(c)'s registration
deny-by-default (to opt-out), keeps the hardcoding- and auto-fetch-prohibitions binding,
and adds signature verification so "default" never means "unauthenticated".

### Part A — beta release track

1. **Beta tag naming = `v0.1.0-beta.1`.** Matches the existing tag glob and validate
   regex unchanged. The presence of a `-` (hyphen) in the tag name is the prerelease
   discriminator.
2. **PyPI publish gate.** Add `if: ${{ !contains(github.ref_name, '-') }}` to the
   `publish` job. Hyphenated tag ⇒ prerelease ⇒ PyPI is skipped; a clean GA tag
   (`v0.1.0`, no hyphen) ⇒ `publish` fires as today. The `build` job stays
   **unconditional** (beta still builds the wheels the GitHub Release ships). Documented
   divergence: hyphen-presence is a pragmatic discriminator, not full PEP 440 parsing —
   a non-hyphen prerelease spelling (`1.0rc1`) would slip through, but aelix's tag
   convention always spells prereleases `-beta.N` / `-rc.N`, so hyphen-presence is
   sufficient and simple.
3. **New `github-release` job** (`needs: build`, runs on every release tag, per-job
   `permissions: contents: write`): downloads the `dist` artifact (the four first-party
   sdists+wheels; `aelix-server` already dropped by `build`), generates a `SHA256SUMS`
   over the published set, and ships the repo-checked-in `install.sh` as a release asset.
   It runs `gh release create "$TAG" --prerelease dist/* SHA256SUMS install.sh` for a
   hyphenated tag, and `--latest` (non-prerelease) for a GA tag, with `--generate-notes`.
4. **`install.sh` (`curl | sh`)** — a hybrid installer. It fetches the release's four
   first-party wheels into a temp dir (`gh release download` / asset URLs), then
   `pip install --find-links <tmpdir> 'aelix==0.1.0'` (or `'aelix[tui]'`). The four
   `==0.1.0`-mutually-pinned first-party wheels resolve from `--find-links`; third-party
   deps resolve from the default PyPI index. **`--no-index` is FORBIDDEN** (it would
   break third-party resolution). For beta this is the ONLY install path — the
   first-party wheels exist ONLY as GitHub Release assets (never on PyPI), which is the
   entire point of the beta channel. (A truly air-gapped operator supplies their own
   wheelhouse / `--index-url` for the third-party deps; that is out of scope for the
   public `curl | sh`.)
5. **Version marker.** The four dists carry static `version = "0.1.0"`; `uv build`
   ignores the git tag, so beta wheels are `aelix-0.1.0-*.whl`. The **prerelease status
   is carried by the git tag + the `--prerelease` GitHub Release label + the fact that
   these bytes live only on GitHub Releases**, NOT by a PEP 440 prerelease version
   segment. This deliberately avoids introducing dynamic (tag-derived) versioning, which
   would break the four-package static `==0.1.0` cross-pin. (See Open questions.)
6. **#73 deferral.** The one-time PyPI Trusted-Publishing pending-publisher setup
   (RELEASING.md / the `release.yml` header) becomes active only when the first
   non-hyphen (GA) tag is pushed. Beta requires none of it.

### Part D — default marketplace catalog (opt-out, signed)

1. **A single OFFICIAL catalog, registered by default (opt-out).** Modeled as an
   existing `kind="catalog"` source (ADR-0188 — **no new `SourceKind`**). Its presence
   is opt-out: disable per-invocation with `--no-default-catalog`, or durably with
   `source remove <official-url>`.
2. **URL is a SETTING default, never hardcoded (guardrail ②).** The official catalog URL
   resolves from `AELIX_DEFAULT_CATALOG` (env) → a settings-constant fallback
   (`DEFAULT_CATALOG_URL`, empty in beta) — it is NOT a literal baked into installer/CLI
   code. Durable opt-out is recorded as an **identity tombstone, not a boolean toggle**: a
   persisted GLOBAL `Settings.suppressed_default_catalogs: list[str]` (serialized camelCase
   `suppressedDefaultCatalogs` through the `SettingsManager` JSON boundary — the ADR-0186
   `extensionSources` seam) holds the NORMALIZED URL identities the operator has opted out
   of. `source remove <default-url>` WRITES the default's identity into that list; `source
   add --catalog <default-url>` CLEARS it (re-activation); an `AELIX_DEFAULT_CATALOG`
   repoint to a different URL ESCAPES a stale tombstone (the new identity is absent from
   the list, so the default renders). Because the tombstone is identity-scoped, a user's
   durable opt-out and an enterprise env repoint COMPOSE cleanly instead of one silently
   overriding the other. `--no-default-catalog` (and an empty-string `AELIX_DEFAULT_CATALOG`)
   is instead an EPHEMERAL this-run-only suppression that leaves the tombstone untouched.
   So the opt-out survives restarts as a stored identity, not as a re-materialized boolean.
   §4(c)(i) — no hardcoded default catalog — remains satisfied: the value is
   operator-overridable config.
3. **No auto-fetch; refresh-only (guardrail ①) + offline-inert (guardrail ④).** The
   default catalog is REGISTERED but never fetched at startup or at registration. Its
   entries appear only after an explicit `discover --refresh`, exactly like operator
   catalogs (ADR-0188 §2); the TUI Discover tab reads the cache sidecar SYNCHRONOUSLY and
   never does network I/O. Under `--offline` the default catalog is fully inert (no fetch
   attempt, no error row). §4(c)(ii) — no auto-fetch/auto-discovery — remains satisfied.
   **Together ①+④ preserve the closed-network / 폐쇄망 principle: a box that never runs
   `--refresh`, or always runs `--offline`, never touches the network for the default
   catalog.**
4. **Official-catalog signature verification, fail-closed (guardrail ⑤).** Extract a
   PUBLIC `verify_signed_document(raw_bytes, sidecar, trust_store) -> bool` shim into
   `extension_signing.py`, composing the EXISTING `read_aelixsig` / `build_statement` /
   `canonical_bytes` / `resolve_public_key` / `_verify_raw`. `fetch_catalog`, for the
   OFFICIAL catalog, verifies the fetched `catalog.json` bytes against a detached
   `.aelixsig` using the merged trust set (`FIRST_PARTY_KEYS ∪` user `trusted_keys.json`,
   minus `revoked`). On a missing signature, an untrusted/absent key, or a bad signature,
   the official catalog's entries are **DROPPED with an error row — never rendered**
   (fail-closed; the signature is a real defense). The signature REQUIREMENT is scoped to
   the OFFICIAL/default catalog only — operator-registered catalogs stay ADR-0188
   advisory (may be unsigned), so closed-network internal unsigned catalogs still work.
5. **AST-purity + pin-invariant preserved.** The shim lives in `extension_signing.py`;
   `extension_catalog.py` imports it and reads the SIGNING trust store
   (`trusted_keys.json` + `FIRST_PARTY_KEYS`), **NOT** `extension_pins.json`. ADR-0188
   §4(a)'s invariant — `extension_catalog.py` never touches the pin store — is
   **PRESERVED** (document-signature verification is orthogonal to the display-only
   per-entry `sha256`), and ADR-0187's two-seed pin invariant is untouched.
6. **Consent unchanged; curation (guardrail ⑥).** Installing from the default catalog
   still routes the RESOLVED spec (ADR-0188 §4(b)) to the deny-by-default consent prompt —
   "default catalog" never relaxes consent. Curation = the official catalog is
   hand-curated and each entry's resolved spec is what the operator sees at consent.

### The six binding guardrails (conditions of the §4(c) reinterpretation)

The §4(c)(iii) → opt-out reversal is valid ONLY while all six hold. They are the binding
terms of this partial supersession:

- **① No auto-fetch.** Refresh-only; `discover`/TUI read the cache; refresh is the
  explicit `--refresh` CLI concern. (Preserves §4(c)(ii).)
- **② No hardcoded URL.** The default URL is a settings default from
  `AELIX_DEFAULT_CATALOG`; disable via `--no-default-catalog` (ephemeral) /
  `source remove` (durable identity tombstone). (Preserves §4(c)(i).)
- **③ `source list` shows it as built-in.** The default catalog is rendered with a
  BUILT-IN / default label in `source list`, visibly distinct from operator-added
  sources (transparency; never a silent hidden source).
- **④ `--offline` inert.** Offline makes the default catalog a no-op (no fetch, no error).
  (Together with ① this is the air-gap closure.)
- **⑤ Official-catalog signature verification.** Fail-closed Ed25519 document
  verification via the extracted `verify_signed_document` shim (owner decision 2).
- **⑥ Curation = resolved-spec-at-consent.** Consent receives the resolved spec, never
  the friendly name (ADR-0188 §4(b)); the official catalog is curated.

**Air-gap invariant.** ① (no-auto-fetch) + ④ (offline-inert) mean the default catalog is
never a network dependency: the closed-network principle ADR-0188 §4(c) protected is
preserved by keeping (i)+(ii) binding — only the "nothing is registered until the
operator adds it" default is reversed.

## Governance / supersession

- **ADR-0188 §4(c) — partial supersession.** ONLY the registration deny-by-default clause
  (the "(iii)" concept) is reversed, to opt-out. §4(c)(i) no-hardcoded-URL and
  §4(c)(ii) no-auto-fetch/auto-discovery remain binding (guardrails ② and ①+④). §4(a)
  (display-only `sha256`, `extension_catalog.py` never touches the pin store), §4(b)
  (resolved-spec-at-consent), and §4(d) (deferred authenticated-catalog cross-check —
  still #68) are UNTOUCHED. The inline amendment footnote lands on §4(c) itself (text not
  deleted).
- **ADR-0005 — REINTERPRETATION, not supersession (require ≠ default).** ADR-0005 /
  Principle 6 requires the marketplace to not **presume / require** a public registry
  (offline / customer-site must work). A single **removable, signature-gated,
  refresh-only, offline-inert** default catalog does not **require** a public registry:
  the air-gap operator opts out (or simply never `--refresh`es / stays `--offline`) and
  the marketplace still functions on internal `kind="catalog"` sources. Providing a
  DEFAULT that can be disabled is not PRESUMING one. ADR-0005 is therefore reinterpreted,
  not superseded — a footnote, no status change.
- **ADR-0010 / ADR-0187 / ADR-0189 — preserved.** No unified cross-source verdict is
  introduced; the two-seed pin invariant and the display-only per-entry `sha256`
  advisory model are intact. The catalog-DOCUMENT signature (guardrail ⑤) is the
  "catalog itself must be authenticated first" precondition ADR-0189 named when it
  re-deferred the per-entry cross-check to #68; #76 delivers that document-level verify
  for the official catalog only — the per-entry fail-closed cross-check remains #68.

## Consequences

**POSITIVE:** beta ships through a real `curl | sh` + GitHub Releases channel with
`SHA256SUMS` integrity, cleanly deferring PyPI (and all #73 pending-publisher setup) to
GA; a fresh install gets a populated, signature-verified Discover tab out of the box; the
air-gap / 폐쇄망 principle is preserved intact (guardrails ① + ④ + opt-out ②); maximal
reuse — the release change is additive (one `if:` + one job + one `install.sh`), and the
catalog change reuses ADR-0188's `kind="catalog"` model and ADR-0189's crypto with a thin
public shim; ADR-0187/0188/0189 invariants all preserved.

**NEGATIVE / RESIDUAL:** the hyphen-presence PyPI gate is a heuristic (documented
divergence); `install.sh` depends on PyPI reachability for third-party deps, so the
public installer is not itself air-gapped (an air-gap operator uses a wheelhouse —
out of scope); **`FIRST_PARTY_KEYS` is `{}` today**, so the official catalog cannot be
served signed until the owner runs keygen and embeds the public key — until then the
official-catalog seam is inert (fail-closed ⇒ empty), which is correct but means the
default catalog is invisible pre-keygen; the opt-out adds a persisted settings field +
the shim's fail-closed branch adds test surface; official-catalog curation/drift inherits
ADR-0188's manual-curation residual; beta wheels carry static `0.1.0` (prerelease status
lives only in the tag/label), so a user who side-loads a beta wheel by filename cannot
tell it from GA by version alone.

**NEUTRAL:** pi has neither a release track of this shape nor a marketplace catalog — no
parity obligation on either track.

## Open questions / risks (carry into implementation)

- **`FIRST_PARTY_KEYS` population + official-catalog hosting.** Owner must run
  `extension keygen`, embed the first-party public key, stand up the official
  `catalog.json` (GitHub Pages) and sign it before beta can serve a signed default
  catalog. Until then guardrail ⑤ keeps the seam inert (fail-closed).
- **Beta version semantics.** Confirm the decision to keep static `0.1.0` for beta (vs
  introducing tag-derived PEP 440 `0.1.0b1`, which would require dynamic versioning and
  break the four-package `==0.1.0` cross-pin).
- **`install.sh` version discovery.** Pin to an exact release tag vs "latest prerelease"?
  Recommend exact-tag for reproducibility during beta.
- **Opt-out surface — RESOLVED (owner-locked).** Durable opt-out is a persisted GLOBAL
  identity tombstone `suppressed_default_catalogs: list[str]` (NOT a boolean toggle):
  `source remove` writes the default's URL identity and `source add --catalog` clears it,
  and an `AELIX_DEFAULT_CATALOG` repoint escapes a stale tombstone. `--no-default-catalog`
  (and an empty `AELIX_DEFAULT_CATALOG`) is a separate EPHEMERAL this-run suppression that
  leaves the tombstone intact.
- **README index is frozen at ADR-0093** — 0094–0191 are not indexed. The provided index
  line matches the frozen table's format, but placement/back-fill of the whole
  0094–0192 range is an owner call.
- **GA cutover.** When the first non-hyphen tag lands, #73 pending-publisher setup +
  RELEASING.md become active; confirm PyPI Trusted-Publishing config timing before that
  tag.

## Follow-ups

- **#68** — authenticated-catalog per-entry `sha256` fail-closed cross-check (still
  deferred; #76 delivers document-level verify only).
- **#73 / GA** — drop the hyphen gate for the GA tag; complete PyPI pending-publisher
  setup; the four dists publish to PyPI so `pip install aelix` works without
  `--find-links`.
- Official marketplace homepage (GitHub Pages) + catalog hosting/signing pipeline.
