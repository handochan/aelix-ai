# ADR-0187 — #64: extension pack signing / hash verification (pre-pip integrity gate)

- **Status:** Accepted (2026-07-05) — scheme owner-confirmed. Lands with the #64
  implementation (Phase 1); this record precedes the code and seeds it.
- **Date:** 2026-07-05
- **Sprint:** Marketplace — resolving ADR-0005 Open Question Q2 ("구체 신뢰 모델은
  후속 ADR에서 결정합니다"). A from-scratch aelix-original trust-model decision;
  there is NO pi parity anchor.
- **Pi pin:** `earendil-works/pi@734e08e`. CONFIRMED: pi has ZERO signing/hash
  verification — its install is a plain `npm install` (no `--ignore-scripts`), and
  its only trust model is a "review source before installing" doc warning. aelix's
  shipped source-level consent (#19) is already stricter. So #64 ports nothing; it
  designs a scheme.
- **Relates:** ADR-0005 (marketplace multi-source; Open Question Q2 — the doc this
  resolves), ADR-0010 (trust stays source-specific; no unified aelix verdict/format
  — a LIVE constraint), #19/ADR-0185 (the `install` primitive), #32-A/ADR-0186
  (marketplace core; split signing to this follow-up, "verify must be pre-pip").
  GitHub #64. Follow-ups: #65 (discover-catalog).

## Owner decisions (confirmed 2026-07-05)

The design (WF-1: recon → 3-approach judge panel → synthesis) recommended a phased
hybrid; the owner confirmed all four gated decisions as recommended:

1. **Scheme — Phased hybrid.** Ship Approach A (SHA-256 hash-pinning + TOFI, zero
   new deps, stays inside ADR-0010) as the #64 deliverable now, with a
   forward-compatible seam for Approach B (Ed25519 provenance) deferred to a later
   ADR. Approach C (Sigstore keyless) rejected as a default.
2. **Trust-model default — `tofi`** (record-then-verify; unsigned sources degrade to
   today's consent-only so no air-gap path is bricked), with `strict`/`require-pin`
   opt-in for locked-down sites.
3. **Verify scope — per-kind asymmetric accepted.** PATH = independent hash (built
   artifacts only; directory/editable → consent-only), PYPI = aelix-owned two-phase
   download→hash→install-from-local (top-level artifact only; transitive deps
   unverified), GIT = commit-SHA pin (tree immutability, not build output).
4. **Trust store — plain-JSON sidecar** `<agent_dir>/extension_pins.json` (sync
   write, avoids the #32-A async-flush landmine, covers one-off path/git installs).
   Air-gapped sites: out-of-band admin provisioning of that sidecar.

## Context

#19 (ADR-0185) shipped `aelix extension install <path|git|pypi>` and #32-A
(ADR-0186) shipped the persisted `extension_sources` marketplace core on top. In
that pipeline all three target kinds collapse to ONE `sys.executable -m pip install
<spec>`, and the ONLY trust boundary is a source-level `y/N` consent prompt
(deny-by-default, `--yes` headless, closed stdin denies). ADR-0186 deliberately
deferred signing to this ADR, noting the verify gate "must be pre-pip".

The load-bearing reality: **pip runs the package's build/setup code — arbitrary code
execution — at install time.** Signing/hashing therefore verifies
INTEGRITY/PROVENANCE (you received the exact bytes the signer intended) but does NOT
sandbox execution: a malicious-but-correctly-signed author still runs code. This ADR
adds an integrity layer IN FRONT OF consent; it does not replace it. The consent
prompt remains the execution-trust boundary.

Three constraints frame the decision:

1. **Offline / air-gap is a HARD requirement** (ADR-0005 Principle 6 + the #19
   air-gap design). Verification cannot require a network call to a public
   transparency log, OIDC issuer, or keyserver at install time. This disqualifies
   Sigstore keyless as a default (its sign step hard-requires OIDC + Fulcio) and
   PEP 458/480 TUF-for-PyPI (not deployed on PyPI as of 2026).

2. **Artifact availability is asymmetric across kinds** — the design crux. PATH: a
   local artifact exists on disk BEFORE pip runs, so aelix can hash it
   independently. PYPI: no local artifact exists before `pip install`; pip fetches
   from the index (native anchor = pip's own `--require-hashes`, or a two-phase
   `pip download`→verify→`pip install --no-index`). GIT: no distributable artifact;
   the closest anchor is a pinned commit SHA. One uniform scheme cannot cover all
   three at equal strength.

3. **ADR-0010 is a live constraint**: its first cut defines NO unified aelix
   trust-verdict schema and NO aelix-defined signature/hash format. A hash-only
   scheme stays inside 0010 (leans on local pinning + pip-native mechanisms);
   introducing an aelix signature format (e.g. Ed25519 `.aelixsig`) would require
   explicitly superseding 0010.

Three approaches were evaluated across a security lens, an offline/operator-burden
lens, and an implementation-cost lens:

- **A — SHA-256 hash-pinning with Trust-On-First-Install (TOFI):** zero new deps,
  100% local, integrity-only. Offline lens 9, cost lens 9, security lens 6.
- **B — Ed25519 detached-signature provenance:** one near-zero dep (`cryptography`,
  already in-env), pure-local, real provenance. Security lens 8, offline lens 6,
  cost lens 6. Requires superseding ADR-0010.
- **C — Sigstore keyless (Fulcio + Rekor):** strongest provenance in the abstract
  but hard-fails offline at sign time; heaviest deps; unanimous dealbreaker as a
  primary scheme.

## Decision

**Ship Approach A now as the #64 floor, with a forward-compatible seam for Approach
B later. Reject Approach C as a default.**

**The scheme (Approach A).** A pre-pip integrity gate records a SHA-256 pin the
first time a source is installed (Trust-On-First-Install) and refuses any later
install/update whose bytes no longer match the recorded pin. Zero new dependencies —
stdlib `hashlib` plus pip's own mechanisms. It honestly detects "same bytes as
recorded", never "safe to run" and never "who authored this".

**Verify hook point.** A `verify_and_pin()` call inside `install_extension()`
(`extension_install.py`), slotted AFTER the consent block (ends line 321) and
IMMEDIATELY BEFORE `run(pip_args)` (lines 323–324). `kind` is already computed
(line 283) and `target`/`pip_args` are in scope, so the gate branches per-kind
without reclassifying. On any verification refusal it returns `_EXIT_DIDNT_RUN`
(2, "pip never ran") — the same class as user-abort, distinct from pip's own failure
returncode. Because `_upgrade_source`/`_upgrade_pypi_name` call `install_extension()`
directly, one gate here covers `install` AND `update`/`--upgrade`.

**Per-kind behavior.**

- **PATH** — a local artifact exists on disk before pip runs. A built distribution
  file (`.whl`/`.tar.gz`) gets a strong exact `hashlib.sha256` pin. A directory /
  editable source has no single stable artifact and, in v1, degrades to consent-only
  with a loud "unverifiable source tree" warning rather than a fragile recursive
  tree digest. TOFI records on first install, byte-compares thereafter. Fully
  offline.
- **PYPI** — no local artifact exists pre-pip. aelix owns a two-phase flow (chosen
  over bare `pip --require-hashes`, which is all-or-nothing and would demand every
  transitive dep be hash-pinned): `pip download <spec> -d <tmp>` (respects
  `--index-url`/offline) → `hashlib.sha256` the resolved top-level wheel/sdist → on
  match, `pip install --no-index --find-links <tmp> <spec>` so the installed bytes
  are exactly the verified bytes. HONEST GAP: only the top-level artifact is pinned;
  pip still resolves+builds transitive deps from the index unverified, and a
  version-float `pip install pkg` (no `==`) re-TOFUs each new version.
- **GIT** — no distributable artifact. The only anchor is a pinned full 40-hex
  commit SHA in the `git+…@<sha>` spec; verified modes refuse mutable refs
  (branch/tag/HEAD) so no `git ls-remote` network resolve is needed. This guarantees
  the same source TREE, not the built bytes, and inherits git's SHA-1 caveat (git is
  migrating to SHA-256).

**Trust material storage.** A plain-JSON sidecar at `<agent_dir>/extension_pins.json`,
keyed by a canonical **pin identity** (`_pin_identity` — path→absolute, git→repo URL
with the `@<sha>` stripped, pypi→PEP 503 canonical name). This is deliberately
DISTINCT from `_source_identity` (used for the #32-A source list): dropping the git
`@<sha>` and canonicalizing the pypi name is precisely what makes a ref move / a
version bump map onto the SAME entry so it is caught as a re-pin rather than a fresh
blind trust. Each entry is `{kind, name, version, sha256, gitSha, pinnedAt, mode}`. A sidecar (not `SettingsManager`) is
chosen deliberately: it keeps the pi-shaped `Settings` schema clean, covers one-off
path/git installs that never became a registered source, and — critically —
sidesteps the #32-A async-write landmine (a missed `await settings.flush()` silently
drops state). `agent_dir` resolves via the existing `get_agent_dir()`/`_load_settings`
path; tests isolate via `AELIX_CODING_AGENT_DIR` / `AELIX_SETTINGS_PATH` (NOT
`AELIX_AGENT_DIR`). An OS keyring is rejected (adds a runtime dep; headless/air-gapped
boxes often lack a backend).

**Modes / enforcement.** Default `tofi` (record-then-verify; first acquisition
trusted blindly with a one-line "unverified first acquisition" notice).
`strict`/`require-pin` refuses any identity lacking a pre-provisioned pin — for a
locked-down site whose admin ships `extension_pins.json` out-of-band, converting
TOFI into "install only what the admin already vouched for by digest". `--no-verify`
is a dev escape hatch; `--repin` accepts an expected change; `update --upgrade`
re-pins on a new `(name,version)` by default or refuses without `--repin` in strict
mode. Unsigned/unpinned sources in `tofi` degrade to today's consent-only flow — the
#19 air-gap path is never bricked.

**Forward-compatible seam (ships now, cheap).** The pin-store entry and the
verify-gate signature are shaped so an optional `keyId`/`sig`/`sha256Statement`
field and an Ed25519 verify branch (Approach B) can be added later WITHOUT moving the
gate or changing the exit-code contract. No keygen/sign/keyring is built in #64. The
seam MUST be reviewed against a concrete Approach-B sketch before #64 lands (see Open
risks).

## Consequences

**What it protects (integrity/provenance, after a source is pinned):** artifact
tamper / MITM on a pypi download; an index or mirror re-serving swapped bytes for an
already-pinned `(name,version)` (a private index CAN republish the same version with
different content); silent mutation of a relied-on git ref (branch force-push / tag
move); accidental corruption or a stale/wrong CDN file; tampering of a local path
artifact between installs; and drift detection on update (an unexpected byte change
on a supposedly-unchanged version is surfaced, not silently installed).

**What it explicitly does NOT protect (must not be sold as more):**

- **Execution safety.** pip runs the pack's build/setup code (arbitrary RCE) AFTER
  the hash passes. A correctly-hashed malicious author still executes. **The `y/N`
  consent prompt REMAINS the sole execution-trust boundary; a "verified" result must
  never relax it.**
- **The first install** is unverified by construction (TOFU blind-first-trust): a
  day-one channel attacker gets their malicious digest recorded as ground truth.
  Strict mode with a pre-provisioned pins file is the mitigation for locked-down
  sites.
- **A compromised-but-legitimate publisher** shipping a new version — `update
  --upgrade` re-pins on a version bump and installs the new bytes.
- **Transitive dependencies** — only the top-level artifact is pinned; pip
  resolves+builds the pack's own deps unverified, each running its own build code
  (the largest practical hole).
- **Provenance / authorship** — a bare hash says "differs from recorded", never
  "signed by X" — no cross-source audit, no revocation. This is why hash-only is a
  FLOOR, not the whole answer.

**Offline story.** Verification never contacts external trust infrastructure — no
Sigstore/Rekor, no OIDC, no keyserver, no TUF root. It is `hashlib.sha256` + a string
compare against a local sidecar, satisfying ADR-0005 Principle 6 unconditionally. The
only network any step touches is the same index/git remote the install already needs
(offline pypi still requires `--index-url`, per the existing refusal at
`extension_install.py:284`). Air-gap provisioning of strict-mode pins is a file copy,
not a network call.

**Operator workflow.** No keygen, no key custody, no rotation. `install`/`update`
gain `--no-verify`, `--repin`, and a `--strict` flag. v1 ships the `--strict` flag
ONLY; the `--require-pin` alias and a persistent settings-backed strict toggle are
deferred to a follow-up. A closed-site admin curates `extension_pins.json`
out-of-band and runs strict mode. (`--no-verify` overrides `--strict` with a stderr
warning — it disables all verification; consent still runs, so there is no
execution-trust impact.)

**ADR-0010 reconciliation.** Approach A stays INSIDE ADR-0010: it defines no aelix
signature format and leans on local pinning + pip-native mechanisms. No supersession
is required for #64. **If** the owner later adopts Approach B (Ed25519 `.aelixsig`),
that follow-up ADR MUST explicitly revisit/supersede ADR-0010's "no aelix-defined
signature format" first cut.

**Exit codes & consent.** Inherit #19/#32-A: `0` ok · pip returncode on pip failure ·
`2` never-ran (now also covers a verify refusal). Consent stays exactly as shipped and
runs FIRST; verify is strictly downstream (no point verifying bytes the operator
declined).

**Follow-ups.** Approach B (Ed25519 provenance) is deferred to a later ADR, triggered
only when a first-party/closed-site publisher signing workflow is real (it must
supersede ADR-0010 and promote `cryptography` to a direct dep — near-zero, already
in-env at 48.0.0). Approach C (Sigstore) is a watch-item only — a possible
bundle-carrying VERIFY-only mode for the path kind behind an optional extra, never the
air-gap default. Track PEP 458/480 PyPI deployment as an ecosystem watch item.

## Open risks (carry into implementation)

- **TOFU blind-first-trust:** security is zero on the first install of any source in
  default `tofi` mode. Only strict-mode pre-provisioned pins close this; provisioning
  discipline is the operator's responsibility, not enforced by the tool.
- **Two-phase pypi fragility:** `pip download`→hash→`pip install --no-index
  --find-links` runs a second dependency resolution that can diverge from a direct
  install (build-isolation, sdist-vs-wheel selection, platform tags) and can break
  transitive-dep resolution when deps are absent locally. Needs real-index
  integration testing before enabling by default.
- **sdist metadata-build window:** for an sdist-only pypi package, `pip download` may
  invoke the PEP 517 build backend for metadata BEFORE the hash gate runs — a narrow
  pre-verify code-execution window. Prefer `--only-binary=:all:` where possible;
  document the residual window.
- **Transitive dependencies remain unpinned in v1** — the largest structural gap; not
  closed by A (nor by a top-level-only Ed25519).
- **Operator habituation:** frequent legitimate updates require `--repin`, and `tofi`
  re-pins on version bumps by default — training rubber-stamping. `--no-verify` and
  directory/editable degrade-to-consent add bypass pressure.
- **Unauthenticated local pin store:** `extension_pins.json` is plain local state a
  local-write attacker can rewrite — acceptable only because such an attacker already
  controls the interpreter/site-packages.
- **git SHA-1 caveat:** a pinned commit SHA relies on git's SHA-1 object naming and
  trusts whatever remote serves that SHA — the weakest kind; document as
  tree-immutability, not provenance. Additionally, git permits a branch/tag named
  exactly 40 hex chars, which `_extract_git_sha` cannot distinguish from a real
  commit object (it does not fetch to check the object type); such a same-shaped
  mutable ref would be treated as pinned. Accepted for v1 as a sharper instance of
  the already-accepted git-ref-mutation risk.
- **Deferred-B seam:** if the forward-compat seam is under-specified now, adding
  Ed25519 later could still force a gate/exit-contract change. Review the seam against
  a concrete Approach-B sketch before #64 lands.

**Gate (on implementation):** pytest (full suite) pass · ruff clean · pyright 0
errors on changed source.
