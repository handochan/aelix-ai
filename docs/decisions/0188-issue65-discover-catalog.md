# ADR-0188 — #65: extension discover-catalog (advisory JSON catalog over air-gap transports)

- **Status:** Accepted (2026-07-05) — scheme owner-confirmed. Lands with the #65
  implementation; this record precedes the code and seeds it (same pattern as
  ADR-0186/0187).
- **Date:** 2026-07-05
- **Sprint:** Marketplace — the discover-catalog follow-up split from #32
  (ADR-0186 shipped the pi-parity core; Discover/catalog-browse deferred here).
  A from-scratch aelix-original decision; there is NO pi parity anchor.
- **Pi pin:** `earendil-works/pi@734e08e`. CONFIRMED: pi has NO discover-catalog
  of any kind — its `packages` model registers KNOWN sources; there is no
  "browse a marketplace by name" surface. So #65 ports nothing; it designs a
  scheme.
- **Relates:** #65 (this), #7 (parent epic — the hard air-gap/폐쇄망 constraint),
  ADR-0185 (`install` primitive), ADR-0186 (#32-A marketplace core —
  `extension_sources`, the Sources/Discover tabs), ADR-0187 (#64 hash-pin + TOFI
  integrity gate — the invariant this must not break), ADR-0010 (trust stays
  source-specific; no unified aelix verdict — a LIVE constraint), ADR-0005
  (marketplace must not presume a public registry). Follow-ups: #67 (Ed25519
  authenticated catalog + the fail-closed hash cross-check), the B-graft
  `aelix extension index <dir>` generator + registered-index `/simple/`
  enumeration.

## Owner decisions (confirmed 2026-07-05)

The design (WF-1: 5-agent recon → 3-approach proponents → 3-judge panel →
synthesis, 12 agents) recommended Approach A as the v1 spine. The judge panel
scored A and B within ~1 point across all three panels (A 90/88/85, B 89/90/90)
and rejected C (git-forge, ~57–64 — it hard-requires a running forge REST API,
which a forge-less file-share/bare-git air-gap does not have → fails #7). Every
judge's synthesized hybrid was the same shape: **JSON catalog as the spine +
index-enumeration/generator as a graft.** The owner confirmed four gated
decisions:

1. **v1 core — Approach A (self-contained JSON catalog document), B as a
   fast-follow.** A is the only option that works with ZERO server (the hardest
   폐쇄망 shape — shared drive / burned image / `file://`), uniquely carries
   human descriptions (the payoff of evaluating an UNKNOWN extension), and
   surfaces all `path|git|pypi` kinds. B (registered-index `/simple/` enumeration
   + `aelix-ext-*` convention + an `extension index` generator) is a separately
   tracked graft, not v1.
2. **Catalog hash policy — display-only advisory.** An entry `sha256` stays in
   the schema but catalog code NEVER writes `extension_pins.json`. (Seeding the
   #64 pin store from an unauthenticated network catalog is trust-laundering —
   `verify_and_pin` would re-hash the attacker's download, find it EQUALS the
   attacker-seeded pin, and print a false green "integrity verified" over
   attacker code. All three judges flagged this a dealbreaker.) The field is a
   forward-compat seam for an authenticated catalog (#67), meaningful only once
   the catalog ITSELF is authenticated.
3. **v1 surface — CLI + a FILTERABLE TUI Discover tab (owner chose the fuller
   scope over the read-only recommendation).** `discover`/`search` +
   `discover install` on the CLI, AND the TUI Discover tab goes live with an
   in-tab type-to-filter (not merely read-only). The filter reuses the existing
   type-to-filter machinery already in `context.py` (the `select`/picker's
   `state["filter"]` + `filtered()` + backspace + `<any>` char-catch); it is a
   contained extension of `tabbed()`, not a viewer rewrite.
4. **Subcommand name — `discover` (with `search` as an alias).** Matches the TUI
   "Discover" tab label and the #65 framing (find UNKNOWN extensions, distinct
   from #32-A register-a-KNOWN-source).

Taken as decided defaults (all agents agreed, minimal churn, not separately
gated): **registration model** = reuse `extension_sources` with a new
`kind="catalog"` via `source add --catalog` (ZERO `types.py` schema change — the
dataclass `kind` is a plain `str`; only the coding-agent `SourceKind` Literal
gains `"catalog"`); **new code** = ONE pure module `extension_catalog.py`
mirroring `extension_pins.py` (stdlib only, injectable for tests).

## Context

#65 asks to browse/search a marketplace BY NAME and install from it, feeding the
TUI Discover tab (today honest-static `build_discover_lines()` → "No registry
configured"). This is DISTINCT from #32-A `extension_sources` (register a KNOWN
source) — discover finds UNKNOWN ones.

Epic #7 (owner, hard constraint): the marketplace MUST work on CLOSED CORPORATE
INTRANETS (air-gapped / 폐쇄망). Any path that hard-requires pypi.org or
github.com reachability is disqualified for the core.

Recon established the ground truth that forces Approach A:

- There is NO server-side search on any PEP 503 / PEP 691 Simple index (the
  XML-RPC search that backed `pip search` was disabled in 2020 and removed in
  2021). "Browse by name" against an index can ONLY be
  download-full-`/simple/`-and-filter — fine against a small self-hosted index
  (a few KB), unfit for public pypi.org (500k+ projects, tens of MB).
- DESCRIPTIONS are NOT carried by a bare PEP 503/691 index (names + file links
  only). Rich metadata needs PEP 658 (Nexus, not universal) or the Warehouse
  JSON API (pypi.org-only) — never air-gap-guaranteed.
- The hardest #7 shape is "no server at all" (a `catalog.json` on a shared drive
  / a burned image / a git repo the org already runs). No index-based or
  forge-based design serves it.

## Decision

Adopt a self-contained JSON CATALOG DOCUMENT as the v1 core, registered like an
`extension_sources` entry (`kind="catalog"`), resolvable via `file://` / local
path / self-hosted intranet `https` (TLS-required) / git shallow-clone. The
catalog is strictly ADVISORY: it chooses only WHAT to install; the EXISTING
gated install (source-level `y/N` consent + `verify_and_pin` #64) remains the
sole trust boundary.

1. **FORMAT.** A JSON document
   `{schemaVersion:1, name?, updated?, extensions:[…]}`, each entry
   `{name, source, description?, version?|versions?, sha256?, homepage?}`.
   `source` is a `path | git+url[@40-hexsha] | pypi-name[==ver]` spec —
   `classify_target()` already routes it and it is the ONLY field the installer
   consumes. Parsing is LENIENT and forward-compatible (unknown top-level /
   per-entry keys ignored; `schemaVersion` gates ONLY breaking changes; an entry
   missing name-or-source is skipped with a warning, never aborts the catalog),
   with a size/entry-count cap (reject `> ~2 MB` or `> ~5000` entries; bounded
   `urllib` read) so a hostile or mis-registered public-index-scale document
   cannot OOM the parser. Chosen over TOML for stdlib-json parity with
   `extension_pins.json` and the camelCase settings boundary; over a bare
   PEP 503 name-list because a name-list carries no descriptions and cannot
   express git/path entries. Borrows Helm `index.yaml`'s per-entry shape,
   rendered in JSON.

2. **COMPOSITION.**
   - `aelix extension discover [<query>] [--refresh]` — `--refresh` fetches every
     registered `catalog` source and rewrites a merged cache sidecar
     `agent_dir/extension_catalog_cache.json` (atomic `os.replace`, exactly like
     `extension_pins.json`); without `--refresh` it reads the cache. Lists
     matching entries (case-insensitive substring/prefix on name, optionally
     description) with a `fetchedAt` staleness hint. `search` is an alias.
   - `aelix extension discover install <name> [install-flags…]` — resolves
     `name → entry.source` across cached catalogs, then delegates to the
     UNCHANGED `_cmd_install([source_spec, *flags])` → `classify_target` →
     consent → `verify_and_pin` (#64) → pip → `_record_install`. An AMBIGUOUS
     name across catalogs REFUSES with a candidate list (requires `--catalog` to
     disambiguate) — NEVER a silent first-match. Install flags
     (`--yes/--index-url/--offline/--no-verify/--strict/--repin/--verify-pypi`)
     pass straight through.
   - ONE new pure module `extension_catalog.py` (`fetch_catalog` for
     path/file/http/git + lenient parse + cap + merged-cache atomic read/write +
     `load_cached_catalog`), injectable like `extension_pins.py` so it unit-tests
     with no live network.
   - `SourceKind` gains `"catalog"`; `types.py` schema UNCHANGED (reuse
     `ExtensionSourceObject` / `extensionSources`); registration via
     `source add --catalog <url|file|git>` (explicit kind — `classify_source`
     cannot infer catalog vs index from a plain http URL); writes reuse
     `_persist()` (`await settings.flush()` — the #32-A async-flush landmine).
     `entry.py` UNCHANGED (it already forwards every `extension <sub>`). The
     `update` path's `installable=[kind in git/path/pypi]` filter naturally
     excludes catalogs.

3. **TUI — a filterable Discover tab (owner decision 3).**
   `run_extension_manager` gains a `catalog_getter` (mirrors `sources_getter`);
   a `_discover()` closure reads it live inside render (try/except → honest
   empty-state), reading the cache sidecar SYNCHRONOUSLY (the per-tab render
   callable is re-invoked on every open/tab-switch/keypress — it MUST NOT do
   network I/O; refresh is the CLI `--refresh` concern). `tabbed()` is extended
   to support per-tab type-to-filter: when the active tab is filterable, printable
   keys append to a per-tab filter, `backspace` pops, and the body is filtered
   case-insensitively; the close-on-`q` binding YIELDS to the filter on a
   filterable tab (close stays on `Esc`/`Ctrl-C`), reusing the picker's existing
   filter pattern. `build_discover_lines` is rewritten from the honest-static
   block to render cached entries grouped by catalog (`name  version  —
   description`) with an honest empty-state pointing at `source add --catalog`.
   `shell.py` wires `catalog_getter=(lambda: extension_catalog.load_cached_catalog(agent_dir))`.

4. **TRUST — catalog is strictly ADVISORY.**
   - (a) An entry `sha256` is DISPLAY-ONLY and NEVER seeds `extension_pins.json`
     (owner decision 2). Catalog code never imports `save_pins`/`_record_pin`;
     `extension_pins.save_pins` stays the sole writer. Enforced by a test that
     `extension_catalog.py` never touches the pin store. ADR-0187's two-seed-only
     invariant (local TOFI bytes OR out-of-band admin provisioning) is preserved.
   - (b) Consent receives the RESOLVED spec (e.g. `git+ssh://evil/x.git`), NEVER
     the friendly catalog name, so a `name → spec`/typosquat redirection is
     visible. Consent remains the sole execution-trust boundary, deny-by-default.
   - (c) TLS required for remote `http(s)` fetch (plain `http` is
     MITM-rewritable); `file://` and `git+file`/`ssh` unconditional for the
     air-gap. No hardcoded/auto-discovered default catalog — deny-by-default
     operator config, exactly like `extension_sources`.

     > **Amendment (2026-07-10, ADR-0192 — partial supersession).** This §4(c)
     > sentence bundles THREE distinct prohibitions; ADR-0192 (#76 default
     > marketplace catalog) reverses exactly ONE. Clause **(iii) registration
     > deny-by-default** — the "No hardcoded/auto-discovered default catalog —
     > deny-by-default operator config, exactly like `extension_sources`" default
     > (i.e. "nothing is registered until the operator adds it") — is **PARTIALLY
     > SUPERSEDED**: a single first-party OFFICIAL catalog is now registered **by
     > default, opt-out** (durably via a persisted identity tombstone
     > `suppressed_default_catalogs` that `source remove` writes and `source add
     > --catalog` clears; per-run via `--no-default-catalog`). The other two clauses
     > of this sentence remain **BINDING**: **(i) no-hardcoded-URL** — the default URL
     > is a settings default resolved from `AELIX_DEFAULT_CATALOG`, never a literal
     > baked into code — and **(ii) no-auto-fetch / no-auto-discovery** — the default
     > catalog is refresh-only and `--offline`-inert. ADR-0192 further adds fail-closed
     > Ed25519 document verification for the OFFICIAL catalog, so a "default" catalog is
     > never unauthenticated. §4(a) (display-only `sha256`; `extension_catalog.py` never
     > touches the pin store), §4(b) (resolved-spec-at-consent), and §4(d) (deferred
     > #68 cross-check) are UNTOUCHED. See ADR-0192 "The six binding guardrails" for the
     > conditions of this reinterpretation. (Text above intentionally left intact.)
   - (d) No unified cross-source verdict (ADR-0010). An authenticated-catalog
     fail-closed hash cross-check is deferred to #67 over the inert
     `Pin.key_id`/`sig` seam — meaningful only once the catalog itself is
     authenticated.

## Consequences

**POSITIVE:** works fully air-gapped incl. the zero-server case; carries human
descriptions and all `path|git|pypi` kinds; maximal reuse (one pure module, no
new PyPI deps — stdlib `json`/`urllib`/`hashlib` + git shallow-clone); the
honest-static Discover tab goes live and gains type-to-filter; ADR-0187 pin
invariant and ADR-0010 no-verdict rule preserved intact; forward-compatible with
#67 authentication.

**NEGATIVE / RESIDUAL:** introduces an untrusted display-name → arbitrary-spec
map in front of the installer (typosquat/redirection) — mitigated by TLS +
deny-by-default registration + resolved-spec-at-consent, with consent
habituation as a pre-existing residual (ADR-0187); manual curation/drift burden
(a JSON catalog does not auto-reflect what an index serves — an entry can name a
version pip can no longer resolve; mitigated by the fast-follow generator +
`fetchedAt` staleness display; v1 accepts hand-authoring); aelix-original format
carries bikeshed/fragmentation risk (mitigated by `schemaVersion` + lenient
parse); the TUI shows a cached snapshot (removed/yanked entries linger until
`--refresh`); the filterable-tab extension adds keybinding surface to `tabbed()`
(the `q`-yields-to-filter branch must not regress the read-only Installed/Sources
tabs — covered by tests).

**NEUTRAL:** pi has no equivalent — no parity obligation. The B-graft
(registered-index enumeration + `aelix extension index` generator) and the #67
authenticated-catalog cross-check are separately tracked follow-ups.

## Review (WF-3, 2026-07-05)

A 5-dimension adversarial review (correctness ×3 lenses + security-trust +
test-adequacy, per-finding skeptic verification) raised 22 findings, 21 confirmed.
All were fixed in-pass except one accepted NIT:

- **Security/transport (fixed):** a git `OSError`/`TimeoutExpired` (missing `git`
  binary — the air-gap case) escaping `fetch_all` and aborting the whole refresh →
  degraded to a per-source error row; `git+http://` / `http://…​.git` bypassing the
  TLS refusal → refused at both fetch and `source add`; untrusted catalog
  name/description/version/label rendered verbatim into the ANSI frame
  (newline-forged group labels, SGR-spoofed "verified" badge) → control chars
  stripped at parse **and** cache-load, control-char `source` specs skipped; an
  `https`→`http` redirect downgrade → a redirect handler that refuses non-HTTPS
  targets + a final-scheme re-assertion; a symlinked/escaping git `catalog.json`
  → refused; a `file://` remote host silently dropped → refused.
- **Correctness (fixed):** a resolved `source` beginning with `-` misparsed as a
  flag → delegated as `[*flags, "--", source]`; `discover --refresh` with every
  catalog failed now exits 2; a name duplicated within ONE catalog gets a distinct
  "fix the catalog" message; a bare bareword / plaintext-http catalog is refused at
  `source add`; a git-clone wall-clock timeout; multi-char paste/IME into the
  filter.
- **Accepted NIT (not fixed):** the in-tab filter is a flat substring over the
  rendered lines, so filtering can orphan a matched entry from its catalog-label
  header. Making the generic `tabbed()` viewer group-aware would either change the
  grouped display format (breaking the tab's format-asserting tests) or couple the
  viewer to the discover data model — disproportionate for a cosmetic issue on a
  working filter. Revisit if a structured filterable-list widget lands.

+96 tests (module 49, CLI discover 25, TUI discover-tab 17 + context/manager
additions); gate 4919 passed / 1 skipped, ruff + pyright clean.
