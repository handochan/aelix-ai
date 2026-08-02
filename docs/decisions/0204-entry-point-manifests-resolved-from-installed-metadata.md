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
ADR closes the announcement gate that one recorded), ADR-0205 (manifest
capability gates are enforced — this is item ①/②/③/⑤ of its "#91 must
inherit" list), ADR-0206 (`878004b`, the gate-ordering fix this change inherits
and extends to the entry-point tier). GitHub #91.

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
   gate had seen a byte of its manifest — the same defect `878004b` (ADR-0206)
   had just closed one tier up, where a `[[contributes.hooks]]` pack with
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

## What shipped, and the gate it passed

Implemented and verified. Every number below was measured, not projected.

- **`extensions/ep_manifest.py`** (+596) plus loader/scan rewiring. The six
  outcomes, the fence, the `aelix.manifests` remedy and the D5 messages are all
  live.
- **Real `pip install`, not only fixtures.** Ten pack shapes were built as
  genuine wheels by **two** PEP-517 backends (hatchling, setuptools) and
  installed by **both** installer dialects `cli/extension_install.py` can choose
  (`python -m pip install`, `uv pip install --python`) into throwaway venvs.
  Outcomes were byte-identical across the two dialects.
  - A hatchling wheel **binds**; its declared **tool, command, theme and MCP
    server all reach the runtime**, and the MCP gate emits its allow-notice.
  - `pkg_dir == site-packages root` printed **False** for every bound pack.
  - **Nothing is imported during discovery**: the import-marker set is empty
    after both `resolve_entry_point_manifest` and `scan_extension_manifests`,
    and non-empty (27 markers across 9 packs) only after the load — so the
    absence assertions are not vacuous.
  - A `contributes.hooks` pack with `shell_exec = false` is **refused with its
    module never imported** (zero markers), while its byte-identical
    `shell_exec = true` twin loads and wires `handlers=['tool_call']` — the gate
    discriminates, it is not a load failure in disguise.
  - `pip install -e` is `UNPROVEN` with a visible, actionable error in **both**
    src and flat layouts — confirming D1's refutation reaches the metadata floor
    and degrades rather than binds.
  - `FENCED` and `MISPLACED` reproduce from real wheels with the same messages
    and the same named absolute paths as the fixtures.
- **Gate:** pytest **7702 pass / 1 skip** (full suite, 372s) · ruff clean ·
  pyright 8 pre-existing `scripts/pyright_spike.py` errors only (0 new).

## Open — deliberately not done, and what needs an owner decision

### ① Trust asymmetry — OWNER DECISION, and it gates the marketplace announcement

The directory tier is gated by Project Trust; the entry-point tier is not, which
makes it the only tier whose manifest MCP servers can be spawned from an
untrusted directory.

**Frame this accurately: it is a standing decision to ratify or reverse, not an
unnoticed hole.** The exclusion is deliberate and documented in the comment at
`loader.py:703-708` — under an untrusted directory the caller passes
`no_project_local=True` so the project-local tier's arbitrary `.py` is *"NEVER
exec_module'd, while the global/explicit/entry_point tiers below still load
(they are user-chosen, not project-local)"*. ADR-0205 item ④ states the
author's stance just as plainly: *"Probably correct to leave ungated — it is a
user-level `pip install`."*

What #91 changes is not the gap but its consequence: the tier now carries
manifests for the first time, so "ungated" newly means "ungated declarative
contributions" rather than "ungated factory". #91 does not widen it (an
installed pack is installed by the user, not supplied by the repository). The
owner needs to **write the decision down**, either way, before the marketplace
is announced as working.

### ② A newly installed pack's MCP servers need a process restart

The scan runs once at CLI startup and MCP connects before the first harness
build. Themes and widgets do pick up `/reload` for free; MCP servers do not.
Not fixed here — it is a startup-ordering change, not a resolver change.

### ③ setuptools' DEFAULT configuration silently drops the manifest from the wheel

Found by the real-`pip` smoke test; **invisible to every existing test, because
no existing test builds a wheel.** The fixtures hand `install_dist` a `files=`
dict and thereby decide for pip what ships. Real `pip wheel` decides that
itself, and the two backends disagree on the same source tree:

```
aelix_hatchpack-...whl              aelix_stpack-...whl
    hatchpack/__init__.py               stpack/__init__.py
    hatchpack/aelix-plugin.toml  <--    stpack/ext.py
    hatchpack/ext.py                    (aelix-plugin.toml   ABSENT)
    hatchpack/themes/midnight.toml      (themes/midnight.toml ABSENT)
```

A plain `[tool.setuptools.packages.find]` ships **only `*.py`**. Downstream the
resolver is correct — the installed artifact genuinely has no manifest, so
`ABSENT` takes the deliberately-quiet branch (`loader.py:1475`). But the user
experience is: the pack installs, its Python `setup()` runs, its declared tool,
command, theme and MCP server all vanish, and **nothing anywhere says a word** —
precisely the silently-inert failure D6 exists to end, arriving through the
build system instead of through the resolver.

This is a **packaging-template / documentation gap, not a resolver bug**: the
remedy (`[tool.setuptools.package-data]` naming `aelix-plugin.toml` and
`themes/*.toml`) binds perfectly. It is nonetheless the single most likely way a
real author's pack lands half-dead. **Needs its own issue** — an authoring-doc
section, and possibly a non-quiet diagnostic when a dist declares the
`aelix.extensions` group but ships no manifest.

### ④ The `aelix.manifests` remedy relocates where theme files must live

A pack using the D6 remedy gets `pkg_dir = <the DATA package>`, because
`pkg_dir` has one producer (`manifest_path.parent`). Measured: `declpack`
resolved to `.../site-packages/declpack_data`. That follows the stated contract,
but it means such a pack must put its `contributes.themes` files under the data
package, and **nothing documents that consequence.** Doc fix, not a code fix.

### Checked and dismissed — not defects

- **All manifest-less endpoints load under the display name `setup`**
  (`_resolve_factory` uses the factory's `__qualname__`). Cosmetic only:
  `Extension.tools`/`.commands` are per-instance and stayed distinct, and
  `ExtensionRunner` holds a list, not a name-keyed map. INFERRED from reading
  `03c6470^` (not executed): pre-#91 the `callable(entry)` branch computed the
  same `__qualname__`, so **#91 did not introduce it**.
- **`pip install -e` loading manifest-less on every start** is intended, not a
  gap: metadata cannot prove an editable. The population paying for the
  strictness is authors, not published packs — measured, the installed base is
  zero (the official catalog ships `extensions: []` and no `pyproject.toml`
  anywhere declares the group).

## Alternatives rejected

* **Hardened `find_spec` fallback** — refuted by measurement (D1). Not a
  judgement call; a flat-layout editable defeats the predicate and the
  attacker picks the layout.
* **Refuse on `UNPROVEN`/`MALFORMED` instead of degrading** — would break
  every `pip install -e` author over a file no previous release read at all,
  and buys no safety: a missing manifest grants nothing (D2).
* **Read the manifest after `ep.load()`** — reintroduces the exact defect
  `878004b` (ADR-0206) fixed, one tier down. Data before code.
* **A separate `read_entry_point_manifests()` wired straight into
  `cli/entry.py`** — ADR-0205 item ① warned this reopens the hole one tier
  below with no failing test to catch it. Endpoint manifests come out of
  `scan_extension_manifests` as `_ManifestEntry`, through the same seam as
  everything else.
