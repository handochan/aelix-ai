# 0206. A declared `contributes.*` family is refused BEFORE the pack's code is imported

Status: Accepted (2026-07-31). Design record written after the fact, for
commit `878004b`, which shipped without one.
Date: 2026-08-01 (recording a 2026-07-31 change)
Amends: **ADR-0102 §"Trust gate"** (its "The gate in `_invoke_factory` raises
`ExtensionManifestError`" sentence, and §"Decision" item 2's "`_invoke_factory`
gains the `shell_exec` trust gate" — that gate's primary site is now
`_resolve_factory`, ahead of the import; `_invoke_factory` keeps a fence but is
no longer where the refusal is decided).
Builds on: ADR-0182 (which hoisted the `tui_widgets`/`ui_tui_trusted` gate
ahead of the import first — the half of the pair that was already correct, and
the asymmetry this ADR closes), ADR-0102 (the `hooks`/`shell_exec` gate itself).
Relates: ADR-0205 (which cites this commit at its `ui_tui_trusted` row, its
`_ManifestEntry` repr leak class, and its "data before code" requirement on
#91), ADR-0204 (issue #91 — inherits this helper and adds no new gate code).
GitHub #91.
Pi pin: `earendil-works/pi@734e08e`.

**Provenance.** pi has no manifest and no capability vocabulary, so both the
gate and its ordering property are **aelix-original**. Nothing here is parity
restoration or a parity divergence.

---

## The problem

Two sibling gates, the same class of decision, in two different phases.

`[[contributes.tui_widgets]]` requires `capabilities.ui_tui_trusted`; ADR-0182
put that check in `_resolve_factory`, **before** `_factory_from_module` imports
the entry module. ADR-0182 §Decision states the property outright — *"Fires
BEFORE `_factory_from_module` imports the entry module, so a denied plugin
executes NO code at all"* — and the loader carried the same sentence in a
comment.

`[[contributes.hooks]]` requires `capabilities.shell_exec`; ADR-0102 put that
check in `_invoke_factory` — **downstream of the import, and downstream of
`setup()`**. So a pack declaring hooks with `shell_exec = false` was refused
only after it had already run arbitrary Python in the user's process. The
docstring's promise was true for one family and false for its sibling.

Measured on the same fixture, via import-marker files:

```
ep_widget.imported? False   <- ui_tui_trusted denies before any import
ep_hook.imported?   True    <- shell_exec denies only after setup() ran
```

The refusal was real; the *ordering* was not. A gate that fires after the code
it is gating has run is an audit log, not a gate.

## Decision

### D1 — HOIST, do not MOVE. One helper, two call sites.

Both checks collapse into a single `_enforce_declarative_capability_gates(manifest)`
helper, called from **two** places:

| call site | position | what it is for |
| --- | --- | --- |
| `_resolve_factory`, in the `_ManifestEntry` branch | before `_factory_from_module` | the PRIMARY gate — data before code |
| `_invoke_factory` | before `factory(api)` | covers manifests that reach the factory without passing that branch |

(Where *within* the branch the first call sits is load-bearing and changed
later — see D2.)

One function means the class has exactly one shape, so the **next**
`contributes.*` family cannot be added with its gate wired into the wrong
phase — which is precisely the defect being closed.

### D2 — The late call was LOAD-BEARING, not ceremony. Measured, not argued.

The obvious simplification is to delete the `_invoke_factory` call now that the
early one exists. **As `878004b` shipped, that would have opened a hole.**
`_resolve_factory` has an early return for a hooks-only pack — one with
`[[contributes.hooks]]` and no `[plugin.entry] python`:

```python
if entry.manifest.contributes.hooks:
    return _noop_factory, entry.manifest.plugin.id, entry.manifest
```

At `878004b` the gate call sat **below** the `py_entry is None` block, so that
return was reached first. It is the canonical Tier-4b shape (ADR-0102
§"Hooks-only plugin support"): a manifest, no Python, subprocess hooks. Such a
pack arrived at `_invoke_factory` carrying a real manifest and having never met
the early gate, and the second call was the only thing between it and its hook
wiring.

**This is no longer the route that makes it load-bearing.** Issue #91
(ADR-0204) hoisted the gate call to the **top** of the `_ManifestEntry` branch,
above `py_entry` resolution — for an unrelated reason, namely that the
entry-point fallback #91 added below is another way to reach an import. As a
side effect the `_noop_factory` return is now *also* covered by the early gate.
The second call site is kept regardless: it still covers any future direct
caller threading a manifest into `_invoke_factory` without entry resolution, and
deleting a fence because today's callers happen not to need it is how the
original defect was introduced. Recorded explicitly so a reader diffing the
current file against this ADR does not conclude the ordering argument was wrong
— it was correct at the commit this ADR records, and #91 moved the line.

There is in fact a **third** fence, inside the hooks wiring block itself, also
kept deliberately. It is the only copy that sees the manifest **as
`factory(api)` left it** — a plugin that declares no hooks passes both earlier
gates and can append a `HookContrib` from `setup()`. Its comment is explicitly
forbidden from claiming this holds against a determined plugin (see D4).

### D3 — The refusal must not print the manifest it refused.

Hoisting the hooks refusal moved it from the `path=<name>` load-error handler
into the `path=str(entry)` one. `_ManifestEntry` is a dataclass, and its
default repr expands the whole `PluginManifest` — including
`contributes.mcp_servers[].env`, which holds plugin-supplied API tokens.
`cli/entry.py` prints that to stderr verbatim.

Measured: a **1261-char** warning containing a live `ghp_...` token. The error
is now labelled by plugin id (**191 chars, no secret**), and
`_ManifestEntry.__repr__` is redacted so the payload cannot escape through any
other stringification. This also repaired the **pre-existing** widget and
import-error cases, which took the same handler (measured: 212 chars, no
secret). ADR-0205 §D5 cites this fix by name as *"the same leak class"* when
arguing its own two messages are leak-safe by construction.

### D4 — Behaviour-preserving by construction.

Message text and exception type are unchanged **byte-for-byte** (they are
user-facing and asserted by tests). The widget gate is still evaluated first,
preserving the pre-existing precedence when one manifest trips both.
`_is_lazy_eligible` is untouched, so **which** packs defer does not change — a
gated family that quietly became lazy-eligible would relocate its refusal out
of load time and into mid-session command dispatch.

## Consequences

### The property this buys — and the one it does not

**Buys:** for a manifest-discovered pack, every declarative `contributes.*`
family whose payload is *executed* is refused before anything is imported.
After the fix both markers read `False`.

**Does not buy: this is not a sandbox.** It is a **load-time check on declared
data**. `Capabilities` is a non-frozen pydantic model reachable through the
same `api` the plugin is handed, so a `setup()` that flips `shell_exec = True`
and appends a hook contrib passes **all three** fences and wires its hook —
measured, not theorised. The gates buy the naive half only.

This is recorded because the third fence's own comment originally claimed it
held against mutation, and a review caught it. The comment now says what the
fence buys — the naive half — instead of what it does not. The honest property
is *"a pack that never executes cannot self-grant"* — which is exactly why
moving the gate ahead of the import is the load-bearing change, and why no
amount of downstream fencing substitutes for it. ADR-0205 §"Not a sandbox"
carries the cross-plugin poisoning measurement that bounds the whole
vocabulary.

Consistently: `_enforce_declarative_capability_gates`' docstring no longer
states "executes NO code at all" as an absolute. It carries a SCOPE section
naming where the promise holds and where it does not.

### What #91 inherited

ADR-0204 adds **no new gate code**. Because the fix landed as a helper at the
`_ManifestEntry` seam rather than as an inline check, routing entry-point packs
through `_ManifestEntry` (ADR-0204 §D1–D3) made them gated automatically, and
`scan_extension_manifests` calls the same function (ADR-0204 §D7). The
docstring's SCOPE section was updated rather than rewritten: the
`entry_points` tier moved from "does NOT hold — `ep.load()` imports during
discovery" to "holds, by the same route".

## What shipped, and the gate it passed

- **Shipped in `878004b`** (2026-07-31), on `main`: `extensions/loader.py`
  (+150/−31) and `tests/extensions/test_hooks_gate_manifest.py` (+351, **6
  tests**).
- The tests pin the ordering through **negative** marker assertions
  (`assert not import_marker.exists()`), so they distinguish *refused* from
  *refused before executing anything*, and cover both families in one loop so a
  future family wired into the wrong phase shows up as an asymmetry.
- **Falsifiability measured, not asserted:** flipping the fixture's
  `shell_exec` false→true turns the suite red on the load assertion, and
  reverting the production hoist turns **four** tests red on
  `assert not import_marker.exists()` — the reported defect signature,
  reproduced inside the standard suite.
- **Gate (at the shipping commit):** `tests/extensions` + `tests/cli` +
  `tests/subprocess_hooks` + `test_extension_loader` → **1279 passed** · ruff
  clean on both changed files · run in the worktree with PYTHONPATH provenance
  proof printed.
- **Gate (re-measured at this ADR's writing, post-`e3365cd` merge):** pytest
  **7702 pass / 1 skip** (full suite, 372s) · ruff clean · pyright 8
  pre-existing `scripts/pyright_spike.py` errors only (0 new).

## Open — deliberately not done, and what needs an owner decision

None of the following is a regression of this change; each was known at
shipping time and left out as a different defect class.

- **Post-`setup()` mutation is not defended, and will not be by this
  mechanism.** A load-time data check cannot bind code that is already running
  (see Consequences). Closing it means a real capability boundary — process
  isolation or a frozen capability object — which is a separate design, not a
  fourth fence. **No owner decision pending**; recorded so the three fences are
  not mistaken for defence in depth against a determined plugin.
- **`tui/ext_widgets.py` re-reads widget contribs after `setup()` without
  re-checking `ui_tui_trusted`.** Same class as the third hooks fence, without
  the fence. **Needs its own issue.**
- **`cli/entry.py` discards `loaded.errors` before the `/extension` viewer sees
  them**, so a refused pack looks *uninstalled* rather than *refused*. This
  blunts every message in D3 and D4 at the surface the user actually reads.
  **Needs its own issue.**
- **`contributes.mcp_servers` had no capability gate at all** when this shipped
  — listed here as a follow-up at the time. **CLOSED since**, by ADR-0205; it
  is gated at its own seam by `gate_manifest_mcp_contribs`, which returns
  refusals instead of raising, because it runs at CLI startup before any
  load-time gate is reachable.
- **The `entry_points` tier had no declarative gate** when this shipped.
  **CLOSED since**, by ADR-0204 — see "What #91 inherited".

## Alternatives rejected

- **Move the hooks gate, deleting the `_invoke_factory` call** — a measured hole
  at the time: the hooks-only `_noop_factory` early return never met the early
  gate. #91 has since hoisted the call above that return, so this specific route
  is now covered twice; the fence is kept anyway (D2).
- **Leave the hooks gate where it was and document the asymmetry** — the
  docstring already promised the property, and the promise is the useful thing.
  A gate that fires after the code it gates is an audit log.
- **Two separate hoisted checks instead of one helper** — reproduces the
  original defect's *cause* (two sites drifting into different phases) while
  fixing only its instance.
- **Make `Capabilities` frozen so post-`setup()` self-granting fails** — does
  not hold. A plugin can rebind the attribute on its own manifest object, and
  ADR-0205 measured a *cross-plugin* variant that never touches an instance at
  all: poisoning `Capabilities.model_fields['shell_exec'].default` and rebuilding
  `PluginManifest` makes a **different** pack, with no `[capabilities]` table,
  pass the gate. Freezing would buy a false sense of a boundary that in-process
  Python cannot provide.
