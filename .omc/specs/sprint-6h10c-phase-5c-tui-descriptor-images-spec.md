# Sprint 6h₁₀c — Tier-2 Descriptor Renderer + Inline Images (Phase 5c-tui)

**Status:** DRAFT (W2). Builds on 6h₁₀a (ADR-0104) + 6h₁₀b (ADR-0105). Closure ADR = 0106.
**Crux:** the Tier-2 descriptor renderer (8 `DescriptorKind` → live chrome) is genuinely net-new
*consumer* wiring — neither the host-side `ui:list-modules` emitter nor any descriptor surface code
exists today. Contracts package + `docs/contracts` are PROTECTED (byte-frozen by
`scripts/generate_contracts_schemas.py --check`); the renderer imports/reads them only.

---

## §1 — Design intent (grounded, W1 architect #21 + verification)

### §1.1 Descriptor flow is dual-channel; neither is wired today
- **Static manifest declaration** — `DescriptorContrib(kind, id)` (`manifest.py:135-139`) carries
  **no payload**; it is a `(kind,id)` slot *reservation* gated by `capabilities.ui_descriptor`
  (`manifest.py:86`, forces `entry.python`). Cannot render anything alone.
- **Dynamic runtime emit** — the live `DescriptorEnvelope` (with payload) is produced by the
  **`ui:list-modules` synchronous probe** (ADR-0095:130-149): host emits a mutable probe object;
  T1 extensions append descriptors during the synchronous emit; host partitions by `kind` and
  dispatches. Channel = the **`EventBus`** (`api.py:257-293`, `emit(channel,data)` /
  `on(channel,handler)->unsubscribe`), exposed via `runtime.event_bus` (`api.py:425-426`) — **not**
  the typed hook `api.on` overloads.
- **Verified absent**: no `ui:list-modules` emitter anywhere; `ext_ui.py` has **zero** descriptor
  methods (no `invalidate_descriptors`); no existing `Probe`/`.modules` type (net-new).

### §1.2 Seam (recommended, Option A)
A standalone **`tui/descriptors.py`** with `DescriptorRegistry` (keyed stateful store) +
`DescriptorRenderer` (per-kind dispatch), **owned by `run_tui`** and fed by
`runtime.event_bus.on("ui:list-modules", registry.collect)` + a **one-shot probe after `bind_ui`**
(`shell.py:119`). Rationale: descriptors are a *different delivery axis* than the Pi-parity 27-method
`ExtensionUIContext` (so NOT a method on `AelixTUIContext`); they are *stateful* (identity +
idempotent replace + `removed`) so a pull-only footer registry can't cover toast/modal/scrollback;
`run_tui` already owns all live-component wiring (chrome/footer/context/`EventRenderer` subscription),
so the probe subscription belongs there, parallel to `harness.subscribe`.

Probe object (net-new, defined in `tui/descriptors.py`): a small `ListModulesProbe` with
`modules: list[Any]`. Host: `probe = ListModulesProbe(); event_bus.emit("ui:list-modules", probe)`;
then for each item `DescriptorEnvelope.model_validate(item)` (tolerate dict or model) → `registry.apply`.
Invalid items: log + drop (forward-compat, ADR-0095:162-164).

### §1.3 Per-kind mapping (ADR-0095:166-186 intent → existing 6h₁₀b destinations)
| Kind | Multiplicity (`slots.py`) | Destination (existing chrome/overlay/footer API) | 6h₁₀c scope |
|---|---|---|---|
| `footer-segment` | many | `chrome.set_footer_line(composed)` via `footer_data.set_status(ns+id, text)` recomposed; emission-ordered | **FULL** (tooltip ignored — no hover) |
| `status-item` | many | `chrome.set_status(ns+id, styled)`; level→theme color (`info/warning/error`) | **FULL** |
| `toast` | many | `overlay.make_float(non_capturing)` + `chrome.add_float`/`remove_float`; auto-dismiss via `loop.call_later(auto_dismiss_ms/1000, ...)` (precedent `context.notify` :179); level→color | **FULL** |
| `tool-renderer-desc` | one per `tool_name` | Rich `Table`(table)/`Columns`(grid)/form/`Panel`(text) → `widgets.RichComponent.render(width)` → `chrome.print_above` (commit path). Applied when a matching tool result arrives. | **FULL** (renderer); tool-result interception = new logic |
| `management-modal` | one per `command` | `overlay.show_modal` driven by registered `command`; fields/columns rendered into the Float | **FULL** (render + open); `ActionDescriptor` reverse-channel dispatch = **DEFER** |
| `command-route` | one per `command` | Register route metadata in registry; live autocomplete completion | **PARTIAL**: store metadata only; **DEFER** live completion (ADR-0105:86 autocomplete deferred) |
| `breadcrumb` | many | **No breadcrumb region.** Degrade → `chrome.set_header_line(chain)` (header line exists :305). Dedicated `Panel.top` = DEFER | **DEGRADE** |
| `agent-metric` | many | **No sidebar.** Degrade → `chrome.set_widget(key, lines)` or a status-row metric string. Dedicated sidebar Columns = DEFER | **DEGRADE** |

### §1.4 Multiplicity / dedup / removal (ADR-0095:151-164, `slots.py:17-26`)
- Key every descriptor by `(kind, namespace, id)`.
- `removed: true` → drop the key + clear its chrome state (`set_status(key,None)`/`remove_float`/
  `set_widget(key,None)`); **no implicit removal**.
- Idempotent replace: re-emit same key overwrites atomically.
- `many` kinds: keep all, ordered by **emission counter**.
- `one`-per-subkey kinds (tool-renderer-desc/command-route/management-modal): dedup on the **payload
  discriminator** (`tool_name`/`command`), not `id` — a new `(kind, sub-key)` replaces the prior even
  if `id` differs (`slots.py:20-24`).
- Unknown `kind`: log + drop (the `DescriptorEnvelope` validator already rejects payload/kind mismatch).

### §1.5 Hard constraints
- `aelix-agent-core/.../contracts/` + `docs/contracts/*.schema.json` byte-unchanged
  (`generate_contracts_schemas.py --check` exit 0). Renderer = pure consumer.
- Protected (byte-unchanged) per ADR-0105:101: `rpc/`, `harness/`, `mcp/`, `pyright_spike.py`,
  `docs/contracts`. `tui/` + `cli/` are free.
- pyright holds the 8-error baseline (0 new from `tui/`).

### §1.6 Deferred (explicit, scope hygiene)
1. `ctx.ui.invalidate_descriptors()` live re-probe — would add a method to the `ExtensionUIContext`
   Protocol (contract-touching, `AELIX_API_LEVEL` bump). 6h₁₀c does a **one-shot session-start probe**.
2. `command-route` live autocomplete completion (autocomplete dispatch deferred since ADR-0105:86).
3. `ActionDescriptor` reverse-channel (`plugin_action` emit back to plugin) — EventBus exists (not
   contract-touching) but is new bidirectional wiring; defer interactive modal actions.
4. Dedicated `breadcrumb` `Panel.top` region + `agent-metric` sidebar Columns — degrade for 6h₁₀c.
5. Strict manifest `DescriptorContrib` allowlist enforcement — permissive (log undeclared, render).

---

## §2 — Inline images (W1 research grounded)
**Library:** `term-image` (primary) — the only mature Python lib that auto-detects + natively emits
Kitty / iTerm2 / sixel AND falls back to Unicode half-block, and crucially returns an **escape-string
you print yourself** (fits `chrome.print_above`→`in_terminal`→`Console.print`, `chrome.py:246-251`).
`rich-pixels` (optional) for the Unicode tier (it IS a Rich renderable). Text placeholder
`[image: name W×H]` for non-TTY / no-graphics. Do NOT hand-roll the protocols. Ship behind an
`[images]` extra (mirrors `[tui]`, ADR-0105:131).

**`tui/images.py` (NEW):**
- `class ImageCapability(Enum)`: KITTY / ITERM2 / SIXEL / UNICODE / NONE.
- `detect_image_capability(*, isatty=None, env=None) -> ImageCapability` — **PURE + injectable**
  (mirrors `parse_input_line` purity, `footer_data.AelixFooterData(cwd=)` injection). Precedence:
  not-a-TTY→NONE; `KITTY_WINDOW_ID`/`TERM~kitty`/`TERM_PROGRAM=ghostty`→KITTY; `TERM_PROGRAM=WezTerm`
  →KITTY; `iTerm.app`/`LC_TERMINAL=iTerm2`→ITERM2; `TERM~sixel`/foot/mlterm→SIXEL (only if
  term-image build supports sixel); else `NO_COLOR` unset & TERM not dumb→UNICODE; else NONE.
- `render_image(path, *, max_cells, capability) -> object|str` — graphics tier: build the matching
  term-image class, size to a WHOLE-CELL box, capture the escape-string; UNICODE: `rich-pixels`
  Pixels; NONE/failure: `text_placeholder`. **Degrade INSIDE images.py** (try graphics → Unicode →
  placeholder); never raise into the output pump.
- Emit graphics escape-string raw via `Console.print(s, markup=False, highlight=False, end="")`
  (Rich must pass APC/OSC through untouched — VERIFY on real PTY).

**Integration:** route the renderable/string through the existing `output_queue` "commit" path
(`shell.py` pump) so images land in scrollback in order. Optional convenience: a `/image <path>`
input route, or expose via the print path — keep minimal for 6h₁₀c.

**Implementation caveats (from research — MUST address):**
1. **VERIFY `term-image` API names** (`KittyImage`/`ITerm2Image`/`BlockImage`, `auto_image_class`,
   `from_file`, sizing) against the actually-installed/pinned version — the research used
   training-knowledge names. If the API differs, adapt; if `term-image` is unavailable, the
   `[images]` extra guards the import (NONE/placeholder when absent).
2. **Kitty cursor-advance vs chrome repaint** under `in_terminal`: size in whole cells + correct
   trailing-newline accounting so the chrome repaint doesn't clobber the image's bottom rows.
3. **sixel** support is term-image-version-dependent — gate the SIXEL tier on an actual capability
   check, don't assume.
4. Real-PTY Kitty/iTerm2 validation is **manual smoke** for 6h₁₀c (pyte snapshots deferred to 6h₁₀d,
   ADR-0105:89).

## §2.1 — Footer-ownership fix (applied during W2, pre-W3b)
W3a's `DescriptorRenderer._recompose_footer` overwrote `chrome.set_footer_line` with ONLY descriptor
footer-segments, **dropping the `⎇ branch`** — a second writer conflicting with the sole owner
`context._refresh_footer` (context.py:359-367). FIXED: `DescriptorRenderer` gained a `refresh_footer`
callback; `_render_footer_segment`/`clear` now publish to `footer.set_status` then trigger the shared
composer (falling back to descriptor-only recompose only when unwired, for standalone tests).
`run_tui` wires `refresh_footer=context._refresh_footer`. Regression test added. (ruff/pyright/tests green.)

---

## §3 — Module layout
```
tui/
  descriptors.py   # ListModulesProbe + DescriptorRegistry + DescriptorRenderer   (NEW)
  images.py        # capability detection + image→renderable/placeholder          (NEW, pending §2)
  shell.py         # run_tui: build registry+renderer, subscribe probe, initial emit (EDIT)
  chrome.py/overlay.py/footer_data.py/widgets.py/themes.py  # consumed, not changed in contract terms
```

## §4 — Test plan (headless, no real TTY / no sleeps)
- Registry: apply/replace/remove per `(kind,ns,id)`; `one`-subkey dedup on discriminator; emission order.
- Renderer per kind → asserts the right chrome setter called (fake/inspectable chrome): footer-segment→
  footer compose, status-item→set_status w/ level color, toast→add_float + scheduled dismiss (inject
  loop/time), tool-renderer-desc table/grid/form/text → print_above lines, management-modal→show_modal,
  command-route→registry entry, breadcrumb→set_header_line, agent-metric→set_widget.
- Probe seam: emit `ui:list-modules`, a fake extension appends descriptors, assert they render; invalid
  item logged+dropped.
- Images: capability detection matrix (env/term) + fallback placeholder (pending §2).
- Full suite stays green (2733 baseline + new); protected paths byte-unchanged.

## §5 — Atomic commit plan (await user authorization to commit)
| # | § | message |
|---|---|---|
| 1 | §A | `feat(tui): DescriptorRegistry — keyed store + multiplicity/dedup/removal (Sprint 6h₁₀c §A)` |
| 2 | §B | `feat(tui): DescriptorRenderer — 8-kind dispatch to chrome/overlay/footer (Sprint 6h₁₀c §B)` |
| 3 | §C | `feat(tui): run_tui ui:list-modules probe + descriptor wiring (Sprint 6h₁₀c §C)` |
| 4 | §D | `feat(tui): inline images — capability detection + render/fallback (Sprint 6h₁₀c §D)` |
| 5 | §E | `docs: ADR-0106 descriptor renderer + inline images closure (Sprint 6h₁₀c §E)` |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local in `.omc/`.
