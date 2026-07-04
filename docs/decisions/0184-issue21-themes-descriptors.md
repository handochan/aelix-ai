# ADR-0184 — #21 remainder: `contributes.themes` activation + `contributes.descriptors` close-out

- **Status:** Accepted — **LIVE**.
- **Date:** 2026-07-04
- **Sprint:** #21 declarative-surface remainder (the last two contributes.* families after mcp_servers/on_command W1 ADR-0181, tui_widgets W2 ADR-0182, and the #62 renderer ADR-0183).
- **Pi pin:** `earendil-works/pi@734e08e`.
- **Relates:** ADR-0096 (manifest v1 — `ThemeContrib`/`DescriptorContrib` were validated-then-dropped), ADR-0182 (the tui_widgets adapter pattern this mirrors), ADR-0095 (the UI descriptor protocol — the runtime `ui:list-modules` probe). GitHub #21.

## Context

Two families remained inert. A design-recon (5 parallel agents + pi-oracle) settled both:

- **themes** has a **faithful pi precedent** — pi declares package themes as `package.json`'s `pi.themes: string[]`, discovered by `resource-loader.ts` into a name-keyed `registeredThemes` map (`setRegisteredThemes`, theme.ts:684). aelix's `ThemeContrib {path}` is one entry of that array. The imperative half is already pi-parity in aelix (`context.set_theme`, `--theme`, the `THEMES` registry). **But** the theme-FILE loader never existed in aelix (`tui/themes.py` was built-ins only; the settings `themes`/`--theme` paths were parsed-but-dead), so this family required real new code, not just an adapter.
- **descriptors** has **no pi analog at all** — "descriptor" does not exist in pi coding-agent @734e08e (the oracle is Pi-dashboard, a separate project). aelix descriptors are runtime-emitted via the `ui:list-modules` probe; `DescriptorContrib {kind, id}` carries no `payload`, but the renderable `DescriptorEnvelope` REQUIRES a kind-matched payload of runtime data (tool-result rows, live metrics, dynamic status). A static `{kind, id}` cannot describe a renderable descriptor, and widening it would inline the 8-payload union into the manifest (maximum schema-drift surface) for static-only value.

## Decision

### themes — BUILD (file-based, aelix-native minimal)

- **`tui/themes.py`:** `build_theme_from_data(name, roles)` reuses the built-in `_make_fg`/`_make_bg`/`_make_bold`/`_make_italic` factories, honoring only the six `THEME_ROLES` (assistant/tool/error/dim/accent/thinking — the aelix `Theme` styles nothing else; pi's ~50-token JSON schema is the fidelity ceiling, not adopted). A color Rich cannot parse is **validated at load** (`Style(color=c).render`) and dropped so it can never raise mid-transcript-render. A SEPARATE `_REGISTERED` dict (kept out of the built-in `THEMES` so built-ins always win) is replaced wholesale by `register_themes(list)` (pi `setRegisteredThemes` — the reconcile primitive); `get_theme` reads built-ins first then registered; `all_theme_names()` feeds the `/settings` picker (previously read `THEMES.keys()` directly, which would have missed manifest themes).
- **`tui/ext_themes.py` (new):** `apply_manifest_themes(runner, *, pending)` walks loaded extensions, resolves each `ThemeContrib.path` against the plugin dir with a **path-traversal fence** (must stay inside the dir) + a **256 KB read cap** + robust TOML parse, builds the Theme, and `register_themes()` the full current list. Never-raises per contrib; `pending` fenced.
- **`loader.py`:** `contributes.themes` forces EAGER (`_is_lazy_eligible`) — the adapter reads only loaded extensions, so deferral would keep a plugin theme out of the picker until first command (the silent-vanish class). The loaded Extension now records `resolved_path = pkg_dir` — a declared-but-unset Pi-parity field that themes is the first family to need (a theme is a plugin-relative FILE, unlike a `module:attr` factory).
- **`shell.py`:** `_apply_ext_themes` is a sibling of `_apply_ext_widgets` in `_rebind` — startup / resume / fork / #24 reload all reconcile. Themes are only **registered** (available), never auto-selected: the user's persisted theme is untouched.
- **NO trust gate (deliberate divergence from ADR-0182):** the theme CONTRIBUTION is pure DATA — the theme file is a TOML color table parsed by the host; nothing in it is imported or evaluated (contrast a `tui_widgets` `factory`, which is plugin code). So the `ui_tui_trusted`-before-code rationale does not transfer to the theme *file*. (A theme-declaring plugin may still ship an `entry.python` factory for its *other* contributions, governed by the normal Project-Trust load path — that is orthogonal to the theme data.) The theme-specific fences are path-traversal + size/parse caps + per-color-and-name validation, not a capability. Documented so it is not mistaken for an oversight.

### descriptors — CLOSE-OUT (runtime-only, documented)

- **No adapter, no model change** (`DescriptorContrib {kind, id}` unchanged → no schema-drift-gate hit). The manifest slot is **reserved/inert by design**: descriptors are emitted at runtime by subscribing to the `ui:list-modules` probe (ADR-0095).
- **`loader.py`** logs a load-time warning when a plugin declares `[[contributes.descriptors]]` — the same silent-vanish concern ADR-0182 fixed for widgets, but here there is no render path to force eager, so a friendly diagnostic (not a render path) is the correct mitigation.
- `docs/guides/extension-authoring.md` gains the runtime-probe pointer.

## Consequences

- A manifest can ship color themes end-to-end; the imperative `ctx.ui.set_theme` (select-only) and this declarative register path now cover the full pi theme surface's aelix-relevant half.
- Still dead (out of this scope, additive later if wanted): the settings `themes` array + `--theme` CLI discovery (pi has these; aelix parses-but-ignores them). Documented, not wired.
- `contributes.descriptors` is closed as intentionally-inert; a future static-descriptor need would be a deliberate, kind-restricted, schema-breaking decision — explicitly not folded in here.
- **#21 is now substantially complete:** mcp_servers, lazy on_command, tui_widgets, themes activated; descriptors documented-closed; the #62 renderer shipped. Remaining open items are lazy `on_tool_call` (needs schema-carrying ToolContribs) and manifest-MCP on `/reload` — both separate design efforts.
- **Gate:** pytest **4641 pass / 1 skip** · ruff clean · pyright 8 pre-existing `scripts/pyright_spike.py` errors only.

## Adversarial review

A 4-lens adversarial review (correctness / security / pi-parity+design / test-adequacy — Opus, over the uncommitted diff) ran before commit. The design was **validated**; three real defects and a handful of hardening gaps were fixed in-pass.

**Verdicts (all upheld the ADR's design calls):**

- **No-trust-gate for themes → SOUND.** A theme file is genuinely pure data: nothing imports/evaluates plugin content, and the only attacker-controlled values reaching a library are role-color strings, which the Rich color parser handles safely (confirmed ReDoS-safe — a 500 KB adversarial color parses in ~2 ms). A `ui_tui_trusted` (code-execution) gate would add nothing; the path/size/color/name fences are the correct controls.
- **Path-traversal fence → AIRTIGHT.** `(pkg/rel).resolve()` + `is_relative_to(pkg.resolve())` is a proper path-component check (not a string prefix); `..`, absolute paths, out-of-dir symlinks, and null bytes are all rejected, and even a hypothetical bypass exfiltrates nothing (content becomes local colors, never returned to plugin/network).
- **pi-parity → CONFIRMED.** `register_themes` is a genuine wholesale-replace setter == pi `setRegisteredThemes` (the reconcile primitive); the 6-`THEME_ROLES` cap is a faithful reduction to what the aelix `Theme` actually styles. Caveat (disclosed in Context): the file format is aelix-native TOML, so the precedent is *architectural*, not file-level pi-compatibility.

**Findings fixed in-pass:**

- **[HIGH] Persisted plugin theme reverted to default on every relaunch** — found independently by the correctness *and* design lenses. The WP-2 persisted-theme seed (`shell.py`) runs *before* the startup `_rebind` registers manifest themes, so a persisted plugin-theme name resolved to `None` at seed time and the context fell back to `DEFAULT_THEME`. Fixed by re-applying the persisted theme once after the initial `_rebind` (guarded on a name mismatch; `set_theme` still no-ops on a since-removed name). A regression test was added **and verified red without the fix**.
- **[MED] Size cap enforced after the full read** (`ext_themes.py`) — `read_bytes()` loaded the whole file, *then* checked the length, so a multi-GB regular theme file inside the plugin dir would OOM every startup/resume/fork/reload (defeating the ADR's own stated fence). Fixed with a `stat().st_size` guard *before* reading plus a bounded `open().read(cap+1)` (TOCTOU-safe against a file that grows between stat and read).
- **[LOW] Theme `name` unsanitized** while colors were validated — the name reaches the `/settings` picker, so an SGR/newline-laden name could inject styling or spoof a built-in row. Fixed by rejecting C0/DEL control chars and capping the length, symmetric with the per-color validation.
- **[LOW] descriptors inert-warning skipped for pure-`on_command` (lazy) plugins** — the warning sat after the lazy `continue` in `loader.py`, so a deferred plugin never emitted it. Moved ahead of the `continue` so every declaring plugin is warned.
- **[NIT] `ThemeInfo.path` stored the unresolved join** — now stores the resolved, fenced target (`_load_theme` returns `(theme, resolved_path)`).

**Test-adequacy gaps closed (13 new tests):** oversize-file skip, absolute-path & symlink escape, control-char name rejection, good-sibling-survives-bad-sibling, non-string color drop, roles-not-a-table degrade, raising-runner clears, `list_theme_infos` resolved path, first-wins asserted by rendered color (not just count), descriptors-warn-when-lazy, the persisted-plugin-theme relaunch regression, **the `/settings` Theme picker driven end-to-end to select a manifest theme** (MEDIUM — the picker's `all_theme_names()` read was untested, so a revert to `THEMES.keys()` would drop plugin themes yet pass every unit test), and **an autouse registry-clear fixture in `test_themes.py`** (its built-in-count invariants are coupled to the process-global `_REGISTERED`). These directly target this repo's documented false-green failure mode (named safety fences with no regression-catching test).

**One rationale-precision fix (LOW):** the "NO trust gate" justification originally read "a theme is pure DATA — no plugin code executes", which overstated the case (a theme-declaring plugin may still run an `entry.python` factory for its other contributions). Reworded to scope the "data, not code" claim to the theme *file/contribution* specifically — the axis that actually governs the no-gate decision — with the plugin's normal Project-Trust load path called out as orthogonal.
