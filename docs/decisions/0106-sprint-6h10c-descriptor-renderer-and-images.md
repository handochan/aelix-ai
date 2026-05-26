# 0106. Sprint 6h₁₀c — Tier-2 Descriptor Renderer + Inline Images (Phase 5c-tui)

Status: Accepted (Sprint 6h₁₀c / Phase 5c-tui sprint 3 of ~4 / W6 shipped)
Date: 2026-05-26
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — consumer-only)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context

ADR-0095 defined the Tier-2 cross-surface **descriptor protocol** (`DescriptorEnvelope` +
8 `DescriptorKind` payloads + the `ui:list-modules` synchronous probe) but **no consumer
ever existed** — the 6h₉d slot that would have built it went to the MCP client, and 6h₁₀b
deliberately scoped descriptors out (ADR-0105:83) while building the *destinations*
(chrome status/footer/header/widget regions, overlay floats/modals). So 6h₁₀c is genuinely
net-new **consumer wiring**, not a fix to broken code: a host-side probe emitter, a stateful
registry, and an 8-kind renderer that dispatches onto the 6h₁₀b chrome. It also lands inline
image rendering (the second 6h₁₀c deliverable per ADR-0105:88).

The contracts package (`aelix-agent-core/.../contracts/`) and `docs/contracts/*.schema.json`
are byte-frozen by `scripts/generate_contracts_schemas.py --check`; the renderer **imports and
reads** the contracts only.

## The decision (the crux)

### Descriptor delivery seam
A standalone **`tui/descriptors.py`** (`DescriptorRegistry` + `DescriptorRenderer`), **owned by
`run_tui`**, fed by a **one-shot `ui:list-modules` probe** emitted over `runtime.event_bus`
(`api.py` `EventBus`) right after `bind_ui`:

```
run_tui ──bind_ui──► event_bus.on("ui:list-modules", registry.collect)
                     event_bus.emit("ui:list-modules", ListModulesProbe())   # synchronous
   T1 extensions append descriptors during the emit ─► registry.collect
       └─ DescriptorEnvelope.model_validate (dict|model; invalid logged+dropped)
          └─ registry.apply ─► on_apply/on_remove ─► DescriptorRenderer dispatch ─► chrome
```

Rationale: descriptors are a **different delivery axis** than the Pi-parity 27-method
`ExtensionUIContext` (so NOT a method on `AelixTUIContext`); they are **stateful** (identity +
idempotent replace + explicit removal), so a pull-only footer registry can't cover
toast/modal/scrollback; and `run_tui` already owns all live-component wiring (chrome/footer/
context/`EventRenderer` subscription), so the probe subscription belongs there, parallel to
`harness.subscribe`. The probe is **synchronous and completes before** the chrome/pump async
tasks start, so descriptor state is populated before the first repaint (no race).

### Per-kind mapping (ADR-0095:166-186 intent → 6h₁₀b destinations)
| Kind | Mult. | Destination | Scope |
|---|---|---|---|
| `footer-segment` | many | `footer.set_status` + shared composer (`context._refresh_footer`) | FULL |
| `status-item` | many | `chrome.set_status`; level→color | FULL |
| `toast` | many | non-capturing `make_float` + `add_float`; `loop.call_later` auto-dismiss; level→border | FULL |
| `tool-renderer-desc` | one/`tool_name` | Rich `Table`/`Columns`/form/`Panel` → `print_above` | FULL renderer (live tool-result interception deferred — no event source) |
| `management-modal` | one/`command` | `show_modal` on-demand via `open_modal` | FULL render; auto-open + action dispatch deferred |
| `command-route` | one/`command` | metadata stored (`command_routes`) | PARTIAL (live autocomplete deferred) |
| `breadcrumb` | many | `chrome.set_header_line(" › " chain)` | DEGRADE (no dedicated `Panel.top`) |
| `agent-metric` | many | `chrome.set_widget(key, [line])` | DEGRADE (no sidebar Columns) |

### Multiplicity / dedup / removal (ADR-0095:151-164, `slots.py:17-26`)
`many` kinds keyed by `(kind, ns, id)` and rendered in emission-counter order; `one`-per-subkey
kinds (`tool-renderer-desc`/`command-route`/`management-modal`) dedup on the **payload
discriminator** (`tool_name`/`command`), not `id`. `removed=True` drops the key and clears the
chrome state. Unknown kind / invalid probe item → logged + dropped (forward-compat).

### Footer ownership (single composer)
A footer-segment descriptor publishes to `footer.set_status(ns:id, …)` and triggers the **one**
footer composer `context._refresh_footer` (`⎇ branch` + all extension statuses). The descriptor
renderer never writes `set_footer_line` itself when wired — eliminating a second-writer that
would have dropped the git branch. (A standalone descriptor-only `_recompose_footer` remains as
the unwired fallback for headless unit tests.)

### Inline images
**`tui/images.py`**: a pure, injectable `detect_image_capability(*, isatty, env)`
(KITTY / ITERM2 / SIXEL / UNICODE / NONE; precedence: not-a-TTY → Kitty env → WezTerm → iTerm2 →
sixel → Unicode → none) and `render_image(path, *, max_cells, capability)` that **degrades inside
the function** (graphics → Unicode → text placeholder) and never raises into the output pump.
Graphics tiers are driven by **`term-image`** (emits a raw escape-string sized to a whole-cell box
via `str(img)`, printable through `chrome.print_above`→`in_terminal`); the Unicode tier uses
`rich-pixels`; failure / non-TTY → `[image: <path> W×H]`. Shipped behind an `[images]` extra.

**Notable constraint: `term-image` is dormant in this workspace.** term-image 0.7.2 (latest) hard-
caps `pillow<11`, conflicting with the coding-agent's `Pillow>=11` core pin — unsatisfiable. The
`[images]` extra therefore ships only `rich-pixels` (Pillow≥10, the always-available Unicode tier);
term-image is documented as a manual add (with a compatible Pillow). The graphics-tier code is
complete and guarded (`_HAS_TERM_IMAGE`), activating automatically if/when term-image becomes
installable. term-image 0.7.2 also ships no `SixelImage`, so a detected SIXEL terminal routes
through the Unicode tier.

## Consequences

- The TUI is now a real consumer of the Tier-2 descriptor protocol: a loaded T1 extension that
  registers `api.on("ui:list-modules", probe.modules.append(...))` sees its descriptors rendered
  in the live chrome at session start. Web (Phase 6) consumes the same envelopes per ADR-0095:188.
- Render exceptions are **contained + logged** (`_log.warning(exc_info=True)`) so one malformed
  descriptor can't abort a probe batch nor vanish silently.
- `pyright` holds the 8-error baseline (0 new); protected paths byte-unchanged.

### Deferred (explicit)
1. `ctx.ui.invalidate_descriptors()` live re-probe — contract-touching (`ExtensionUIContext`
   Protocol + `AELIX_API_LEVEL` bump); 6h₁₀c does a one-shot session-start probe only.
2. `command-route` live autocomplete completion (autocomplete dispatch deferred since ADR-0105:86).
3. `ActionDescriptor` reverse-channel (`plugin_action` back to a plugin) + `management-modal`
   command-triggered auto-open.
4. Dedicated `breadcrumb` `Panel.top` region + `agent-metric` sidebar Columns (degraded for now).
5. Live tool-result interception for `tool-renderer-desc` (no event source this sprint).
6. **term-image graphics tiers** (Kitty/iTerm2) dormant until upstream supports Pillow ≥11.
7. Real-PTY image validation (pyte snapshots → 6h₁₀d, ADR-0105:89); manual smoke only.
8. LOW nits (deferred, tracked): `term-image` `forced_support` process-global mutation; rich-pixels
   cells→pixels width approximation under-fills (tune in 6h₁₀d); `run_tui` reaches the private
   `context._refresh_footer` (same-package seam; could be promoted to a public alias).

## Verification (W5)
- Gate green: ruff clean; `uv run pyright` 8-error baseline (0 from `tui/`); **`uv run pytest`
  2778+ pass / 1 skip** (incl. 25 descriptor + 21 image tests); `generate_contracts_schemas.py
  --check` exit 0; protected paths (`contracts`/`docs/contracts`/`rpc`/`harness`/`mcp`) byte-unchanged.
- **W5 code-reviewer (opus): APPROVE-WITH-NITS** (0 CRITICAL/HIGH; empirically reproduced
  dedup/removal/footer/toast behaviors). Two MEDIUMs fixed in-sprint: command-route stale-key on
  same-command replace; silent render-exception swallow → logged.
- **W5 qa-tester real-PTY: 4/4 PASS** — live OpenRouter turn streams in the chrome (no regression
  from the probe wiring), footer pinned, `ui:list-modules` probe is a clean no-op with zero
  extensions, bash passthrough + `/quit` intact.

Next: Sprint 6h₁₀d (pyte snapshot tests + image/graphics-tier real-PTY validation + autocomplete).
