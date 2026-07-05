# ADR-0189 — #67: Ed25519 detached-signature provenance for extension packs (Approach B)

- **Status:** Accepted (2026-07-05) — scheme owner-confirmed. Lands with the #67
  implementation; this record precedes/accompanies the code (same pattern as
  ADR-0186/0187/0188).
- **Date:** 2026-07-05
- **Sprint:** Marketplace — Approach B of ADR-0187's phased hybrid, triggered now that a
  first-party / closed-site publisher signing workflow is real (owner-confirmed). A
  from-scratch aelix-original decision; there is NO pi parity anchor (pi has ZERO
  signing).
- **Pi pin:** `earendil-works/pi@734e08e`. CONFIRMED: pi has no pack signing of any kind
  (plain `npm install`, "review the source" doc warning). aelix's shipped source-level
  consent (#19) + hash-pin/TOFI (#64) are already stricter; #67 adds provenance on top.
- **Relates:** ADR-0187 (#64 — the SHA-256 hash-pin + TOFI integrity gate this extends;
  its inert `Pin.keyId`/`sig` seam is now live), ADR-0188 (#65 — the display-only
  catalog `sha256` invariant #67 must not weaken), ADR-0010 (**AMENDED here** — see
  Governance), ADR-0005 (Open Question Q2 — this completes what ADR-0187 partly
  resolved), ADR-0185/0186 (`install` primitive + marketplace core). GitHub #67.
  Follow-ups: the **authenticated-catalog fail-closed cross-check** — ADR-0188
  provisionally scoped this to #67, but on implementation it proved a DISTINCT
  catalog-document-signing effort (the catalog itself must be authenticated first), so it
  is **re-deferred to #68**; #67 delivers pack (path/pypi artifact) provenance only, and
  the ADR-0188 display-only-`sha256` invariant is unchanged. Also deferred: git-kind
  provenance, and a real first-party key rollout.

## Owner decisions (confirmed 2026-07-05)

The design (WF: recon → synthesis) recommended, and the owner confirmed:

1. **Scope — Full.** keygen + sign + trust + verify all ship in v1 (not verify-only) —
   the pure-crypto publisher verbs are cheap and are needed to dogfood/produce the
   first-party key and test fixtures.
2. **Covered kinds — path + pypi.** path via a sibling `<artifact>.aelixsig`; pypi via an
   out-of-band `--signature <path>` bound to the two-phase-downloaded artifact. **git is
   deferred** (a `git+URL@<sha>` already pins the commit tree; the statement schema is
   git-ready but the install-time branch is not wired). `--require-signature` on a git
   target refuses (fail-closed, no silent ignore).
3. **Fail-closed posture — always refuse.** A signature that is PRESENT but INVALID
   against a TRUSTED key (bad signature, or a statement that disagrees with the observed
   bytes) refuses ALWAYS via `VerifyRefusal` → exit 2, even without `--require-signature`
   — it is affirmative tampering evidence, categorically stronger than an absent
   signature. An ABSENT signature still degrades to the #64 TOFI consent path so no
   air-gap install is bricked.

## Context

ADR-0187 shipped Approach A (SHA-256 hash-pin + TOFI) as the integrity FLOOR, with an
inert forward-compat seam (`Pin.keyId`/`sig`, plus a promised `sha256Statement`) for
Approach B, and pre-committed that adopting Ed25519 MUST "explicitly revisit/supersede
ADR-0010's no-aelix-defined-signature-format first cut" and "promote `cryptography` to a
direct dep". Hash-only honestly detects "same bytes as recorded" but never "signed by
X" — no provenance, no revocation, and a legit version bump blind-re-TOFIs unknown new
bytes. #67 closes that gap.

The load-bearing constraints are unchanged from ADR-0187: **offline/air-gap is a hard
requirement** (no keyserver / OIDC / transparency-log at install time — the reason
Sigstore was rejected), and **pip runs the pack's build code**, so signing verifies
INTEGRITY/PROVENANCE, never execution safety. Consent remains the sole execution-trust
boundary.

## Decision

Ship an Ed25519 detached-signature provenance layer ON TOP of the #64 gate, entirely
pure-local, reusing the existing gate and exit-code contract.

**Signed statement (the crux).** The signature covers the UTF-8 canonical-JSON
serialization (`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True)` —
deterministic, dependency-free; RFC 8785 JCS would be the formal choice but no JCS lib
is in-env and the statement is ASCII strings/ints with no divergence) of:

```
{"v":1, "kind":<"path"|"pypi">, "keyId":<16hex>, "sha256":<hex>,
 "name":<str?>, "version":<str?>}          # + "gitSha" when git ships
```

The PRIMARY, machine-independent binding is `sha256` — the exact artifact bytes.
`name`/`version` bind package identity (cross-checked for pypi, PEP 503 canonicalized on
both sides). A path `identity` (install-target absolute path) is DELIBERATELY NOT bound —
it is machine-specific and a publisher cannot predict it; the digest already pins the
bytes. Verification is TWO-STEP and BOTH must hold: (1) `Ed25519.verify(sig, canon)`
passes against the trusted key's public bytes, AND (2) every statement field equals the
value the gate INDEPENDENTLY observed. Step (2) is what stops a valid signature over an
attacker-chosen statement.

**`.aelixsig` sidecar.** A self-describing JSON envelope
`{"aelixsig":1, "keyId":…, "statement":{…}, "sig":<base64>}` — carries the literal
signed statement + signature, but NEVER a public key (a key shipped with the artifact is
not a trust source). path → sibling `<artifact>.aelixsig` (its digest equals the staged
copy's, closing the same TOCTOU the #64 gate already closes); pypi/git → out-of-band
`--signature <path>` (the verifier never fetches a sidecar — air-gap purity preserved).

**Trust store.** `<agent_dir>/trusted_keys.json` — a SYNC sidecar (mirrors
`extension_pins.json` / `project_trust.json`; NOT `SettingsManager`, dodging the #32-A
async-flush landmine and inheriting `AELIX_CODING_AGENT_DIR` test isolation). Schema:
`{"version":1, "keys":{<keyId>:{"publicKey":<b64 raw-32>,"label","addedAt","source"}},
"revoked":[<keyId>]}`. Effective trust = an in-tree `FIRST_PARTY_KEYS` constant UNION
the user store MINUS `revoked` (revocation wins, even over first-party — air-gap-native,
no CRL/OCSP). keyId = `sha256(raw_pub_32)[:16]` (an index/label; the full public key is
the verification material). `FIRST_PARTY_KEYS` ships EMPTY in v1: a real first-party key
is provisioned out-of-band by a maintainer (`keygen` → commit the public key; the private
key stays in maintainer custody, never in the repo) — the mechanism ships ready, the
anchor is not tied to a key generated in a build environment.

**Gate wiring (no move, no exit-code change).** A new pure module `cli/extension_signing.py`
(the same pure/effectful split as `extension_pins`) owns keygen/sign/verify, the trust
store, and `gate_signature()`. `verify_and_pin` calls it INSIDE its existing per-kind
branches, after the gate has independently computed `sha256` — a valid trusted signature
stamps `keyId`/`sig`/`sha256Statement` onto the SAME `Pin` that already flows back and is
recorded atomically only on pip exit 0. Every refusal raises the EXISTING
`extension_pins.VerifyRefusal` → the existing catch → exit 2; a signature failure must
NEVER reach the generic-`Exception` swallow (which installs unpinned under tofi). A valid
trusted signature also makes `decide_generic`/`decide_pypi` treat the source as
vouched-for (`authenticated=True`): it satisfies strict-mode first-acquisition and a
version bump WITHOUT a blind re-TOFI — but does NOT bypass a same-identity same-version
byte change (still the drift/tamper signal, still needs `--repin`).

**Commands.** `extension keygen` (0600 PKCS8 PEM under `<agent_dir>/keys/`, prints only
keyId + public key), `extension sign <artifact> --key <keyId|pem>`, `extension trust
add|list|remove|revoke` (add is consent-gated), and `install/update/discover-install
--require-signature [--trusted-key ID] [--signature PATH]` (`--require-signature` opt-in,
modeled on `--strict`). `--no-verify` + `--require-signature` is a HARD error (a required
signature cannot be honored while skipping the gate).

## Governance (ADR-0010 amendment + dependency promotion)

1. **Narrow amendment of ADR-0010.** ADR-0010 Consequences ¶1 said "Aelix가 자체
   signature/hash format을 정의하지 않습니다"; `.aelixsig` IS that format, so ADR-0010's
   Status header is amended to point here. This is exactly the "통합 audit … 별도 ADR"
   escape ADR-0010 anticipated — walked item-by-item: signature_type = **Ed25519**;
   signer identity = **keyId** (sha256 of the public key); evidence = the detached
   `.aelixsig` + `sha256Statement`; standards relationship = the same primitive as
   **minisign / signify / SSH Ed25519** (`.aelixsig` is minisign-shaped, not novel
   crypto);
   internal-only sources = the admin provisions the trusted public key out-of-band, the
   same channel as strict-mode pins.
2. **Scope discipline.** ADR-0010's BROADER "no unified cross-source trust-verdict
   schema" decision stays DEFERRED — #67 is single-format provenance, not a cross-source
   verdict-comparison schema. Conflating them would over-scope.
3. **Seam completion.** ADR-0187 promised the `keyId`/`sig`/`sha256Statement` trio; only
   `keyId`/`sig` shipped. ADR-0189 defines `sha256Statement` as the canonical signed
   serialization and adds it as an append-only `Pin` field (round-trips through the
   forward-compat `extra` machinery; no reordering of the ~40 positional call sites).
4. **Dependency promotion.** `cryptography` is promoted from an incidental transitive dep
   (via `google-auth`) to a DIRECT dep of `aelix-coding-agent` (`>=42,<49`; in-env
   48.0.0), pre-authorized by ADR-0187. The import is lazy + guarded so a stripped env
   degrades to an actionable message, not an import crash.

## Consequences

**Threat-model delta over #64 (Approach A):**

| ADR-0187 gap | #67 outcome |
| --- | --- |
| provenance/authorship ("differs from recorded", never "signed by X") | **CLOSED** — signed-by-`<keyId>`, a stable signer identity |
| revocation (none) | **NEW** — local `revoked[]` list (air-gap-native; no online CRL/OCSP) |
| blind re-TOFI on a legit version bump | **CLOSED for signed sources** — a trusted signature authenticates new bytes |
| first-install TOFU blind trust | **CLOSED for pre-provisioned sources** — a first-party / trusted key verifies out of the box |
| active tamper under default tofi | **NEW** — a present-but-invalid signature from a trusted key fails closed even without `--require-signature` |
| execution safety | **STILL consent-only** — a signature verifies who/what-bytes, never "safe to run" |
| transitive dependencies | **STILL unpinned** — top-level-only, out of scope (per ADR-0187) |
| online/real-time revocation & transparency | **STILL not covered** — air-gap purity means a compromised key stays trusted until a local `trust revoke` |
| catalog pin-seeding | **UNCHANGED invariant** — a catalog may only FAIL-CLOSED cross-check the gate's own hash, NEVER seed `extension_pins.json` (ADR-0188 AST-guarded display-only stays green) |

**Preserved invariants (must not regress):** the gate is not moved and the exit-code
contract is intact (0 ok · pip returncode · 2 never-ran/refusal, ADR-0187); consent
remains the sole execution-trust boundary (a "signed" verdict never relaxes it);
verification stays 100% local/air-gap (no keyserver/OIDC/transparency-log); the catalog
NEVER writes pins (ADR-0188).

## Open risks (carry into maintenance)

- **First-party key custody.** `FIRST_PARTY_KEYS` ships empty; a real key rollout is a
  maintainer action (generate, commit the public key, guard the private key). Until then
  only user-added `trust add` keys authenticate.
- **Local pin/trust store is unauthenticated.** A local-write attacker can rewrite
  `trusted_keys.json` — acceptable only because such an attacker already controls the
  interpreter/site-packages (same posture as `extension_pins.json`).
- **Two-phase pypi fragility** (inherited from ADR-0187) applies to `--require-signature`
  pypi installs, which force the download; needs real-index integration testing (#61)
  before any default-on.
- **Operator habituation.** `--require-signature` is opt-in; the default path still TOFIs
  unsigned sources. Closed sites that want enforcement must adopt the flag (a persistent
  settings-backed toggle is a possible follow-up).
- **git provenance deferred.** `--require-signature` on git refuses today; wiring the
  git-kind branch (signing a `gitSha` statement) is a follow-up when the need is real.

**Gate (on implementation):** `uv run pytest` (full suite) pass · `uv run ruff check .`
clean · `uv run pyright` 0 errors on changed source.
