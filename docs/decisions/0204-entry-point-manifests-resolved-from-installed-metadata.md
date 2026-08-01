# 0204. An installed pack's `aelix-plugin.toml` is resolved from metadata, not by importing it

Status: Accepted (2026-08-01). Design record landing with the issue #91
implementation.
Date: 2026-08-01
Amends: **ADR-0181 §"Slice A"** (its "the entry_points tier is EXCLUDED from
the scan because `ep.load()` is an import" clause is no longer true and is
corrected there), **ADR-0028 §"entry_points"** (its "each endpoint's
`.load()` result is treated as an inline factory" and its
"`ep.name=ep.value` string key" dedup rule are both replaced).
Relates: ADR-0096 (manifest v1), ADR-0102 (Tier-4b subprocess hooks),
ADR-0182 (`ui_tui_trusted`), ADR-0200 (marketplace catalog + installer — this
ADR closes the announcement gate that one recorded), ADR-0203 (manifest
capability gates are enforced — this is item ①/②/③/⑤ of its "#91 must
inherit" list). GitHub #91.

## The problem

`aelix extension install <pkg>` installs a package that declares an
`aelix.extensions` entry point. Until now the loader resolved such a pack by
calling `ep.load()` **during discovery**. Two consequences, both measured:

1. **The pack's `aelix-plugin.toml` was never read.** An installed pack got a
   bare factory and nothing else, so every `contributes.*` family it declared
   — tools, commands, themes, TUI widgets, MCP servers, hooks — was silently
   inert. The marketplace is the channel that installs packs this way, so the
   product's own distribution path was the one path where a manifest did
   nothing. ADR-0200 recorded the consequence: *"마켓플레이스는 #91 착지 전까지
   '동작함'으로 공지 금지."*
2. **`ep.load()` is an import.** It ran the pack's module-level code before any
   gate had seen a byte of its manifest — the same defect 878004b had just
   closed one tier up, where a `[[contributes.hooks]]` pack with
   `shell_exec = false` was imported and only then refused. Fixing (1) by
   reading the manifest *after* `ep.load()` would have reintroduced it.

So the manifest has to be found **without importing anything**, from installed
metadata alone.

## Decision

### D1 — There is no `find_spec` tier. The ladder stops at metadata.

A "hardened `find_spec` fallback" was designed, built and then **refuted by
measurement**. With a real `pip install -e`, a *src-layout* editable writes a
literal path into the `.pth`, so a containment predicate is enforceable — but
a *flat-layout* editable writes an import hook
(`import __editable___flatpack_0_1_0_finder; ...`), which makes the predicate
unenforceable, and a flat-layout attacker pack sailed straight through
inheriting `shell_exec = true`. An attacker chooses their own layout, so the
tier is not a tier. `extensions/ep_manifest.py` decides everything from the
distribution's `RECORD` plus the bytes of the manifest file.

### D2 — Bind on proof, degrade on doubt, refuse only on evidence of attack.

`resolve_entry_point_manifest(ep)` returns one of six outcomes: `BOUND`,
`ABSENT`, `UNPROVEN`, `MISPLACED`, `FENCED`, `MALFORMED`. Only `BOUND` carries
a manifest and a `pkg_dir`.

Degrading is safe, and this is *why*, not a hope: `capabilities.*` is consumed
in exactly two places — the loader's declarative gates and the manifest's own
self-consistency validator. **No manifest means zero declarative
contributions, which means no privilege is gained.** Resolving to "no
manifest" cannot grant anything; it can only cost the pack its declarative
features. A pack that cannot be proved therefore loads manifest-less plus a
visible error, rather than being refused.

### D3 — Bind to the entry module's PATH, never to "the dist".

One distribution can carry several packages' manifests. Naive first-match
binds the wrong one — measured on the recovered `aelix-ep-danger` fixture,
which ships two packages' manifests *and* attacker-chosen entry-point names:
`ep-widget` received `aelix_ep_hook`'s manifest. The ladder walks
`ep.module`'s dotted path most-specific-first, so `ep-hook` gets
`aelix_ep_hook` and `ep-widget` gets `aelix_ep_widget`.

### D4 — The containment fence is mandatory, and both sides are resolved.

`dist.locate_file("")` returns the **site-packages root**, not the package
directory; `RECORD` legitimately contains `..` entries; and `locate_file` does
**not** normalise them — they resolve outside the root with `exists() is
True`. Without a fence a `RECORD` line is a read primitive over the whole
environment. Every candidate must: not be absolute, contain no `..`, resolve
(both sides) inside the root, be a file, and have `parent != root`.

That last rule is not cosmetic. `pkg_dir` is handed to `tui/ext_themes.py`,
which fences contributed theme paths with `target.is_relative_to(pkg_dir)`; a
`pkg_dir` equal to site-packages would turn that fence into a read of any file
under site-packages. `pkg_dir` has exactly **one** producer,
`manifest_path.resolve().parent`, and `locate_file("")` is used only as the
fence root.

### D5 — Three accepted behaviour breaks, each with guidance at the point of failure.

Reading a manifest that was previously ignored necessarily changes what some
installed packs do. Three breaks were accepted by the owner; each ships a
message that says what to do, because a break with a bare exception is not the
approved decision.

| Break | Behaviour | Guidance |
| --- | --- | --- |
| `[[contributes.hooks]]` + `shell_exec = false` | Hard refusal, **zero code executed** | Names the family, the flag and the required value — byte-identical to the directory tier's text |
| Pure-`on_command` pack | Deferred to first command use instead of running at startup | `logger.warning` at the deferral site naming the plugin, the trigger commands and the `on_startup_finished = true` escape hatch |
| `api.min_level > host` | Refused, not degraded — the one case that is "incompatible", not "unproven" | Names the required and actual level, the absolute path, and the three remedies |

The middle row is the one this ADR's implementation nearly shipped silently.
It was accepted on the stated basis that the loader already warned; measured,
it did not — the cited warning is the shortcut-key notice inside
`activate_pending_extension`, which fires only *after* activation. The warning
now exists, and only for endpoint packs: the directory tier has behaved this
way since ADR-0181, so warning there would be noise about nothing that
changed.

### D6 — `MISPLACED` is named, and `aelix.manifests` is the remedy.

A silently-inert install is the failure mode this whole change exists to end,
so the resolver must not reintroduce a quiet one. If `RECORD` carries an
`aelix-plugin.toml` the ladder did not bind, the reason **names every such
absolute path** and points at the remedy.

The remedy is a new OPTIONAL `aelix.manifests` entry-point group: a pack may
declare `<same-ep-name> = "<dotted.package>"` to point at the directory
holding its manifest. It is also the only answer for a **single-module** pack
(`soloext.py` has no package directory to hold one). *Optional* means
optional: the declaration is consulted strictly **after** the ladder fails, so
a pack that omits it takes a bit-identical code path to a world without the
feature. The declared path is subject to the same fence and must be present in
the **owning dist's own** `RECORD`, so it is not a cross-dist reach.

### D7 — The scan applies the same gate the loader does.

`scan_extension_manifests` flips to metadata-only and now reaches installed
packs, which is how an installed pack's `contributes.mcp_servers` becomes
real. That created a new exposure, caught in review: `gate_manifest_mcp_contribs`
keys on `shell_exec`/`net`, **different flags** from the ones the load-time
gate refuses on, so a pack the loader denied outright still had its MCP server
dialled at every startup — a refusal and an allow-notice for the same manifest
on adjacent stderr lines. The scan now runs
`_enforce_declarative_capability_gates` and drops refused manifests, uniformly
for both tiers: a pack that cannot load contributes nothing, whichever tier
found it. No new gate logic — the same function `_resolve_factory` calls.

## Consequences

**What now works that did not:** an installed pack's `contributes.*` reaches
the runtime. Measured on the recovered `ep91` image — `ep-probe` registers
`ep_probe_tool` / `probe-hello`, adds the theme `EP Probe Midnight`, and its
`ep-probe-mcp` server becomes visible to the MCP connect list, all from a pack
found only through `aelix.extensions`.

**The no-import property holds.** Ten pack shapes covering all six outcomes:
no module whose top level belongs to a plugin enters `sys.modules`, zero
import-marker files appear, and a warm second pass has a `sys.modules` delta
of exactly `set()`. A positive control calls `ep.load()` and asserts the
marker *does* appear, so the absence assertions are not vacuous. On the
recovered image the two denied packs (`ep-hook`, `ep-widget`) produce no
marker at all: refused with zero code executed.

**Cross-tier dedup arrives free.** `BOUND` entries dedup on
`pkg_dir.resolve()` through the existing `_push_entry`, so a pack found by
*both* an endpoint and a directory scan now loads once. Measured before:
`setup() ran: 2 time(s)`; after: `1`. Endpoints additionally dedup on
`module:attr`, which is what stops a second endpoint aimed at an
already-bound target from running the same factory again.

**A class-valued endpoint keeps working.** `ep.load()`'s two call shapes had
to survive its deletion: the attribute is walked DOTTED (`mod:Cls.method` is
legal), and a class object is instantiated so `Cls()(api)` works. Review found
the second one restored on the manifest-less path only, so a class-valued
endpoint broke **the moment its manifest bound** — forking on install shape
(wheel broke, editable worked). The carve-out is on both paths now, scoped to
endpoint packs so the directory tier's long-standing `Cls(api)` meaning is
untouched.

**Manifest parse errors no longer echo the manifest.** `str(ValidationError)`
interpolates `input_value=`, i.e. the entire parsed manifest dict including
`contributes.mcp_servers[].env` — plugin-supplied API tokens — into a string
`cli/entry.py` prints. Rendering goes through `redact_manifest_error`
(`include_input=False`) on **both** tiers; location and message are kept, so
the error did not get vaguer.

**Honest limits.** The scan runs once at CLI startup and MCP connects before
the first harness build, so a newly installed pack's MCP servers still need a
process restart; themes and widgets do pick up `/reload`. `pip install -e`
cannot be proved from metadata, so an author's own pack loads manifest-less
and says so on every start — the population that pays for strictness here is
authors, not published packs (measured: the official catalog ships
`extensions: []` and no `pyproject.toml` anywhere declares the group, so the
installed base is zero).

**Deliberately left open.** ADR-0203 item ④ — the **trust asymmetry** — is not
decided here. The directory tier is gated by Project Trust; the entry-point
tier is not, which makes it the only tier whose manifest MCP servers can be
spawned from an untrusted directory. #91 does not widen that gap (an installed
pack is installed by the user, not supplied by a repository), but it does make
the tier carry manifests for the first time, so the question is now live and
needs its own owner decision.

## Alternatives rejected

* **Hardened `find_spec` fallback** — refuted by measurement (D1). Not a
  judgement call; a flat-layout editable defeats the predicate and the
  attacker picks the layout.
* **Refuse on `UNPROVEN`/`MALFORMED` instead of degrading** — would break
  every `pip install -e` author over a file no previous release read at all,
  and buys no safety: a missing manifest grants nothing (D2).
* **Read the manifest after `ep.load()`** — reintroduces the exact defect
  878004b fixed, one tier down. Data before code.
* **A separate `read_entry_point_manifests()` wired straight into
  `cli/entry.py`** — ADR-0203 item ① warned this reopens the hole one tier
  below with no failing test to catch it. Endpoint manifests come out of
  `scan_extension_manifests` as `_ManifestEntry`, through the same seam as
  everything else.
