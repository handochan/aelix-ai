# Sprint 6h₁₀d — pyte Snapshots + command-route Autocomplete + Image real-PTY (Phase 5c-tui finale)

**Status:** DRAFT (W2). Builds on 6h₁₀a/b/c (ADR-0104/0105/0106). Closure ADR = 0107.
Likely the final Phase 5c-tui sprint (~4/4). Three deliverables: (1) pyte snapshot test harness
+ snapshots of the real rendered terminal buffer; (2) command-route **live autocomplete** (the
6h₁₀c `command_routes` were stored-only); (3) image **Unicode-tier real-PTY validation** via pyte.

## §1 — Grounded seams (W1)
- **Input Buffer** (`chrome.py:113`): `self.buffer = Buffer(name="input", multiline=False, history=history)` — no completer. `BufferControl(self.buffer)` at `chrome.py:155`. `Buffer.completer` + `complete_while_typing` are settable post-construction. The chrome owns custom `_build_key_bindings`, so a completion-trigger key (Tab / c-space) must be confirmed/added, and a `CompletionsMenu` Float added to the layout's `FloatContainer` (`chrome.py:169`) for the dropdown to show.
- **6h₁₀c `command_routes`**: `DescriptorRenderer.command_routes: dict[str, CommandRoutePayload]` (keyed `ns:id`), populated at the session-start probe. The completer reads it **live by reference** (dict mutated in place).
- **pyte capture**: chrome accepts `pt_output: Output` (`chrome.py:87` → `Application(output=)`). Inject a `Vt100_Output` writing to a capture buffer (`StringIO`) with a fixed `Size(rows,cols)`; feed `buffer.getvalue()` to `pyte.Stream`→`pyte.Screen`; assert on `screen.display`. This validates the ACTUAL escape-stream layout (catches cursor/region issues that `DummyOutput` headless tests miss). pyte is **not yet a dep** — pure-Python, no Pillow conflict → add to root dev group.
- **6h₁₀b autocomplete storage** (`context.py:325` `add_autocomplete_provider`) is the *extension-provider* surface — separate from descriptor command-routes; left as-is (provider dispatch still deferred).

## §2 — Deliverables

### §2.1 command-route live autocomplete
- `tui/completion.py` (NEW): `DescriptorCommandCompleter(Completer)` — ctor `get_routes: Callable[[], Mapping[str, CommandRoutePayload]]`. `get_completions(document, event)`: only when the line starts with `/`; match the typed word against each route's `command`; yield `Completion(text="/"+command, start_position=..., display=command, display_meta=description[/keybind])`. Pure-ish (source injected), unit-testable with a fake routes dict + a `Document`.
- `chrome.py` (EDIT): (a) add a `CompletionsMenu`/`MultiColumnCompletionsMenu` Float to the layout so completions render; (b) ensure a completion-trigger keybinding (Tab → `complete_next`, c-space → `start_completion`) without breaking existing bindings; (c) `set_command_completer(completer)` method → sets `self.buffer.completer` + `complete_while_typing`. Keep all existing behavior; menu is inert when no completer is set.
- `shell.py` (EDIT): after `_wire_descriptors` builds the renderer, `chrome.set_command_completer(DescriptorCommandCompleter(lambda: renderer.command_routes))`. `_wire_descriptors` returns the renderer (or the completer) so run_tui can wire it; no-op path when no event_bus.

### §2.2 pyte snapshot harness + tests
- `tests/tui/_pyte.py` (NEW helper, or a conftest fixture): `async def render_chrome_to_screen(*, rows, cols, build_state) -> list[str]` — builds an `AelixChrome(pt_input=pipe, pt_output=Vt100_Output(capture, size))`, applies `build_state` (set_status/footer/descriptor apply/etc.), drives one render, returns `pyte` `screen.display` (stripped rows). No real TTY; deterministic size.
- `tests/tui/test_snapshots.py` (NEW): snapshot the rendered buffer for — base chrome (input prompt + footer `⎇ branch`), status-item + footer-segment via the descriptor probe, a toast Float, a breadcrumb header line. Assert on `screen.display` row content/positions (not pixel-exact — assert key substrings at expected rows).

### §2.3 image Unicode-tier real-PTY validation
- term-image is **dormant** (Pillow<11) → graphics tiers cannot be validated. Validate the **Unicode (`rich-pixels`) tier + placeholder** through pyte: `render_image(tmp_png, max_cells=(w,h), capability=UNICODE)` → a `rich-pixels` renderable → print via a Rich Console to the capture buffer → pyte screen shows a non-empty colored block; `capability=NONE` / missing path → `[image: … ]` placeholder visible. (Graphics-tier real-PTY validation stays deferred until term-image is co-installable.)

## §3 — Module layout
```
tui/completion.py   # DescriptorCommandCompleter                              (NEW)
tui/chrome.py       # CompletionsMenu float + completion keybind + set_command_completer (EDIT)
tui/shell.py        # wire the completer from renderer.command_routes         (EDIT)
tests/tui/_pyte.py            # pyte render harness                           (NEW)
tests/tui/test_snapshots.py   # chrome/descriptor/image snapshots             (NEW)
tests/tui/test_completion.py  # completer unit tests                          (NEW)
pyproject.toml (root)         # pyte in dev group                             (EDIT)
```

## §4 — Constraints
- Contracts / `docs/contracts` / `rpc` / `harness` / `mcp` byte-unchanged. pyright 8-baseline (0 new).
- Full suite stays green (2779 baseline + new). Snapshot tests deterministic (fixed rows/cols, injected clock for spinner, no real TTY/sleeps).
- `Vt100_Output` + `pyte` are the validation path; if the chrome's escape stream proves hard to snapshot deterministically, fall back to asserting on the captured ANSI substrings (still real-stream, just not full-grid) — but prefer the pyte grid.

## §5 — Test plan
- Completer: `/de`→ yields `/deploy` when a command-route `deploy` exists; non-slash line → no completions; description in display_meta; live update (mutate routes dict → new completion appears).
- pyte snapshots per §2.2; image per §2.3.
- Regression: existing `tests/tui/` stays green; a real-PTY qa smoke (autocomplete dropdown shows a descriptor command; agent still streams).

## §6 — Atomic commit plan (await authorization)
| # | § | message |
|---|---|---|
| 1 | §A | `feat(tui): DescriptorCommandCompleter — command-route autocomplete (Sprint 6h₁₀d §A)` |
| 2 | §B | `feat(tui): chrome completion menu + set_command_completer wiring (Sprint 6h₁₀d §B)` |
| 3 | §C | `test(tui): pyte snapshot harness + chrome/descriptor/image snapshots (Sprint 6h₁₀d §C)` |
| 4 | §D | `docs: ADR-0107 pyte snapshots + autocomplete closure (Sprint 6h₁₀d §D)` |
Trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Spec stays local in `.omc/`.
