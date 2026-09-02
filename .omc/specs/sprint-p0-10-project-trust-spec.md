# Sprint P0 #10 — Security Gates / Project Trust — Recon & Edit-Map Spec

**Status:** Recon complete (read-only). Gates a multi-agent implementation workflow + a user scope decision.
**Author:** recon/mapping agent. **Date:** 2026-06-20.
**Repo root:** `/workspaces/aelix-ai`.

---

## 0. Framing & ground truth

Project Trust is a **SINCE-PIN feature**: pi added it AFTER aelix's pin `734e08e`. Ground truth is therefore
pi **HEAD** (`earendil-works/pi`), not the pin. This is a documented since-pin *adoption* (like the model-catalog
refresh), not a pin port. All pi citations below are fetched from `?ref=HEAD` and quoted verbatim.

### Pi source files (HEAD, verbatim, fetched this session)
| pi path | role | LOC |
|---|---|---|
| `packages/coding-agent/src/core/trust-manager.ts` | `ProjectTrustStore` (disk persistence), `hasTrustRequiringProjectResources`, `getProjectTrustOptions` | 244 |
| `packages/coding-agent/src/core/project-trust.ts` | `resolveProjectTrusted()` — the gate orchestrator + `AppMode` + `DefaultProjectTrust` consumer | 96 |
| `packages/coding-agent/src/cli/project-trust.ts` | `createProjectTrustContext()` — the UI bridge (TUI vs non-interactive) | 62 |
| `packages/coding-agent/src/modes/interactive/components/trust-selector.ts` | in-session `/trust`-style TUI selector component | 134 |
| `packages/coding-agent/examples/extensions/project-trust.ts` | `project_trust` event example extension | 64 |
| `packages/coding-agent/test/trust-manager.test.ts` | store + resource-detection tests | 67 |
| `packages/coding-agent/src/core/extensions/types.ts` | `ProjectTrustEvent` / `ProjectTrustEventResult` / `ProjectTrustContext` / `isProjectTrusted` | (503-525, 318, 1533) |
| `packages/coding-agent/src/core/extensions/runner.ts` | `emitProjectTrustEvent()` (197-227); `ctx.isProjectTrusted()` (653-655) | (197-227) |
| `packages/coding-agent/src/core/settings-manager.ts` | `defaultProjectTrust` getter/setter (870-877); `projectTrusted` refusal to load (344-347, 529-531) | |
| `packages/coding-agent/src/main.ts` | the bootstrap wiring (471, 585-647) | |
| `packages/coding-agent/src/cli/args.ts` | `--approve`/`-a`, `--no-approve`/`-na` (180-183, 274-275) | |

---

## 1. How pi Project Trust ACTUALLY works (verbatim mechanism)

### 1.1 What "trust" gates (the resources)
`trust-manager.ts:29-37` — the set of project-local config resources that REQUIRE trust:
```ts
const TRUST_REQUIRING_PROJECT_CONFIG_RESOURCES = [
	"settings.json", "extensions", "skills", "prompts", "themes", "SYSTEM.md", "APPEND_SYSTEM.md",
] as const;
```
`hasTrustRequiringProjectResources(cwd)` (`trust-manager.ts:184-206`) returns `true` when:
- `cwd/.pi/<any of the 7 above>` exists, OR
- `cwd/.agents/skills` (or any ancestor's `.agents/skills`, EXCEPT `$HOME/.agents/skills`) exists.

Otherwise `false` → **no trust prompt** (the directory has nothing dangerous to gate). The user/global
`~/.agents/skills` is always a trusted user resource and never triggers the gate.

**Note:** AGENTS.md / CLAUDE.md are NOT in the 7-resource list and are NOT a standalone trigger of
`hasTrustRequiringProjectResources`. The example extension docstring (`examples/.../project-trust.ts:13`)
says "Try it in a project containing .pi, AGENTS.md/CLAUDE.md, or .agents/skills" — but the actual
`hasTrustRequiringProjectResources` code only checks `.pi/<resource>` and `.agents/skills`. **So pi does
NOT gate AGENTS.md reading.** (See §4 imprecision correction.)

### 1.2 The gate orchestrator — `resolveProjectTrusted` (`project-trust.ts:46-96`, verbatim)
```ts
export async function resolveProjectTrusted(options: ResolveProjectTrustedOptions): Promise<boolean> {
	if (options.trustOverride !== undefined) {            // --approve / --no-approve
		return options.trustOverride;
	}
	if (!hasTrustRequiringProjectResources(options.cwd)) { // nothing dangerous → trust
		return true;
	}
	if (options.extensionsResult) {                        // project_trust EVENT (extensions decide)
		const { result, errors } = await emitProjectTrustEvent(...);
		for (const error of errors) { options.onExtensionError?.(...); }
		if (result) {
			const trusted = result.trusted === "yes";
			if (result.remember === true) { options.trustStore.set(options.cwd, trusted); }
			return trusted;
		}
	}
	const decision = options.trustStore.get(options.cwd); // persisted trust.json decision
	if (decision !== null) { return decision; }
	switch (options.defaultProjectTrust ?? "ask") {        // global default setting
		case "always": return true;
		case "never":  return false;
		case "ask":    break;
	}
	if (!options.projectTrustContext.hasUI) { return false; }  // NON-INTERACTIVE DEFAULT = DENY
	const selected = await selectProjectTrustOption(options.cwd, options.projectTrustContext);  // PROMPT
	if (selected !== undefined) {
		saveProjectTrustPromptResult(options.trustStore, selected);
		return selected.trusted;
	}
	return false;  // cancelled → deny
}
```
**Resolution order (decisive):** `--approve/--no-approve` → no-dangerous-resources(=trust) →
`project_trust` extension event → persisted `trust.json` → `defaultProjectTrust` setting (ask/always/never) →
**hasUI? prompt : DENY** → cancel=deny.

### 1.3 The prompt text (`project-trust.ts:24-26`, verbatim)
```ts
function formatProjectTrustPrompt(cwd: string): string {
	return `Trust project folder?\n${cwd}\n\nThis allows pi to load ${CONFIG_DIR_NAME} settings and resources, install missing project packages, and execute project extensions.`;
}
```
The prompt options come from `getProjectTrustOptions(cwd, { includeSessionOnly: true })`
(`trust-manager.ts:65-95`): `Trust` / `Trust parent folder (<parent>)` / `Trust (this session only)` /
`Do not trust` / `Do not trust (this session only)`. Each option carries `updates: ProjectTrustUpdate[]` —
the disk writes to apply (`session only` options have empty `updates` → not persisted).

### 1.4 Persistence — `ProjectTrustStore` (on-disk, `trust-manager.ts:208-244`)
- File: `join(agentDir, "trust.json")` — i.e. pi `~/.pi/agent/trust.json` (aelix would be `~/.aelix/agent/trust.json`).
- Shape: `Record<absCanonicalPath, boolean | null>` (a map of project-dir → decision), JSON, keys sorted.
- `get(cwd)` walks UP the directory tree to the nearest entry (`findNearestTrustEntry`, 43-57) — so trusting a
  parent transitively trusts children; a child `false` overrides an ancestor `true`; `null` deletes (un-decides).
- Lock: `proper-lockfile` sync lock on `<trustPath>.lock`, 10 attempts × 20ms (136-166). (Aelix would use a
  `filelock` / `fcntl` equivalent or accept single-process best-effort for v1.)
- Validation (`readTrustFile`, 97-122): must be an object; every value must be `true|false|null` else throw.

### 1.5 The `project_trust` extension event (types + dispatch)
`types.ts:503-525` (verbatim):
```ts
export interface ProjectTrustEvent { type: "project_trust"; cwd: string; }
export type ProjectTrustEventDecision = "yes" | "no" | "undecided";
export interface ProjectTrustEventResult { trusted: ProjectTrustEventDecision; remember?: boolean; }
export interface ProjectTrustContext { cwd: string; mode: ExtensionMode; hasUI: boolean;
	ui: Pick<ExtensionUIContext, "select" | "confirm" | "input" | "notify">; }
```
`runner.ts:197-227` `emitProjectTrustEvent`: iterate extensions in order; first handler returning `yes`/`no`
wins (suppresses built-in prompt); `undecided` falls through; handler errors are collected (one bad handler
doesn't abort).

### 1.6 `ctx.isProjectTrusted()` (extension-facing read)
`types.ts:318` (in `ExtensionContext`) + `:1533` (in the context-actions struct); `runner.ts:273` default
`= () => true`; `runner.ts:653-655` exposes it on the live ctx. Lets any extension query the resolved trust
state for the active cwd.

### 1.7 CLI flags (`args.ts`, verbatim)
```
180  } else if (arg === "--approve" || arg === "-a") {  result.projectTrustOverride = true;
182  } else if (arg === "--no-approve" || arg === "-na") { result.projectTrustOverride = false;
274  --approve, -a                  Trust project-local files for this run
275  --no-approve, -na              Ignore project-local files for this run
```
`projectTrustOverride?: boolean` (`args.ts:49`). When set, it short-circuits `resolveProjectTrusted` (no prompt,
no persist). This is the **non-interactive escape hatch** (CI / scripts pass `--approve` or `--no-approve`).

### 1.8 `defaultProjectTrust` setting (`settings-manager.ts`)
```
61   export type DefaultProjectTrust = "ask" | "always" | "never";
95   defaultProjectTrust?: DefaultProjectTrust; // default: "ask"; global setting only
870  getDefaultProjectTrust(): DefaultProjectTrust { ... ?? "ask" }
875  setDefaultProjectTrust(...) { ...; this.markModified("defaultProjectTrust"); }
```
Global-only (not per-project). Read at `main.ts:631` and passed to `resolveProjectTrusted`.

### 1.9 How `projectTrusted` actually SUPPRESSES loading (the enforcement)
`settings-manager.ts:344-347` (verbatim):
```ts
private static loadFromStorage(storage, scope, projectTrusted = true): Settings {
	if (scope === "project" && !projectTrusted) { return {}; }   // ← untrusted → project settings IGNORED
	...
}
```
and `:529-531` refuses to WRITE project settings when untrusted:
```ts
if (!this.projectTrusted) { throw new Error("Project is not trusted; refusing to write project settings"); }
```
The `SettingsManager` is constructed with `{ projectTrusted }` (`main.ts:471` bootstrap=false; `:617` runtime=
resolved value). The resource loader's `resolveProjectTrust` callback (`main.ts:624-647`) is what gates the
extensions/skills/prompts/themes load — only invoked when `shouldResolveProjectTrust` (i.e. there ARE dangerous
resources, no `--approve/--no-approve`, not yet cached). When trust resolves false, the resource loader skips
loading the project-local extensions/skills/etc.

### 1.10 The non-interactive UI bridge (`cli/project-trust.ts:7-62`)
`createProjectTrustContext` returns a `ProjectTrustContext` whose `ui.select/confirm/input` return
`undefined`/`false` whenever `!hasUI` OR `mode !== "interactive"` — i.e. in print/json/rpc there is no prompt
and `resolveProjectTrusted` hits the `!hasUI → return false` branch (DENY). `ui.notify` still prints to stderr
(colored) in non-interactive mode.

---

## 2. Aelix side — exact current state (read-only map)

### 2.1 The two arbitrary-code-execution surfaces (UNGATED today)

**(A) Project-local EXTENSIONS** — `extensions/loader.py:142-271` `discover_and_load_extensions`.
- Discovery tier 1 (`loader.py:219-223`): `cwd/.aelix/extensions/` — project-local `.py` files / packages,
  loaded via `importlib.util.spec_from_file_location(...).exec_module(module)` (`loader.py:671-684`,
  `_factory_from_file`) → **runs arbitrary Python with full user privileges, no gate**.
- Called from `entry.py:363-369` (`_build_harness_options`):
  ```python
  loaded = await discover_and_load_extensions(
      [str(p) for p in parsed.extensions], cwd=Path(cwd), agent_dir=Path(get_agent_dir()),
      prepend=[GuardrailExtension(), PermissionExtension()], no_discovery=parsed.no_extensions)
  ```
- **Current "security":** `entry.py:372-381` only PRINTS a stderr warning (`"loaded N on-disk extension(s)
  with full system permissions…"`) AFTER loading. It does NOT gate. This is the headline gap.

**(B) Project-local MCP servers** — `cli/config.py:121-185` `load_mcp_server_contribs` +
`entry.py:611-620`.
- `config.py:148-150`: project-local config is `cwd/.aelix/mcp.json` (Claude-Code-style
  `{"mcpServers": {...}}`).
- `entry.py:611-620`: connects each server (`mcp_manager.connect_all()` → `manager.py:47-59` →
  `conn.connect()`), which **spawns the declared subprocess** (`command` + `args`) — arbitrary code
  execution from a project-local file, **no gate**.

### 2.2 SettingsManager — EXISTS but NOT wired into the coding-agent
- `aelix_ai.settings.SettingsManager` is a FULL pi port: `settings_manager.py` (project scope, load errors,
  `default_project_settings_path`, `from_storage`, `create`, per-scope locks). Referenced by the
  gate-protected harness core (`harness/core.py:122,257,533,916`) as an OPTIONAL attribute.
- BUT: grep across `packages/aelix-coding-agent/src` shows **NO construction** of `SettingsManager` and **NO
  call to `default_project_settings_path`** in the CLI bootstrap — the only hits (`entry.py:318,322,567`,
  `file_processor.py:88`) are doc-comments. **So aelix does NOT load `.aelix/settings.json` at all today.**
  → The pi "settings.json" trust surface (one of the 7) is **moot in aelix v1** — there is nothing to gate
  because nothing loads it. (This SHRINKS the parity surface; see §3.)
- `aelix_ai.settings` has NO `projectTrusted` parameter, NO `defaultProjectTrust` field, NO refusal-to-load.
  Those would have to be added IF/when project settings are wired (FULL parity only).

### 2.3 AGENTS.md context — `cli/agent_context.py:74-113` `discover_context_files`
- Walks `cwd` up to filesystem root reading `AGENTS.md`, capped at 32 KiB, appended to the system prompt
  (`entry.py:398-403`, gated only by `--no-context-files`).
- **Is this a trust surface? Per pi: NO** (§1.1 — AGENTS.md is not in pi's 7-resource list and is not a
  `hasTrustRequiringProjectResources` trigger). Reading a markdown file is not code execution. → For parity,
  aelix should NOT gate AGENTS.md. (User decision flagged in §5 anyway, since it IS untrusted project content
  injected into the prompt — a prompt-injection vector, even if pi doesn't gate it.)

### 2.4 CLI args — `cli/args.py`
- NO `--approve` / `-a`, NO `--no-approve` / `-na`. `Args` dataclass (45-183) has no `project_trust_override`.
- Add point: the linear parser loop (`args.py:199-383`) + the `Args` dataclass + `print_help` text
  (`args.py:430-442`, "Tools / Extensions" block).

### 2.5 Trust state — ZERO today
- Grep `is_project_trusted|project_trusted|isProjectTrusted|trust_store|trust.json|--approve` across all 3
  packages: **no hits** (the `tests/` "trust" hits are compaction/contracts false positives). No
  `ProjectTrustStore`, no `trust.json`, no event, no ctx method.

### 2.6 Bootstrap ORDER (where a gate sits) — `entry.py:_async_main`
```
410 parse_args → 488 resolve_app_mode → 528-534 fs/repo → 552-554 AuthStorage+ModelRegistry
594-605 _build_session → 611-620 MCP connect  ←★ MCP gate here
622-631 _harness_factory → _build_harness_options → 363 discover_and_load_extensions  ←★ extension gate here
632-634 create_agent_session_runtime → 637 dispatch (interactive/rpc/print)
```
Problem: in the current order, `_build_harness_options` (which loads extensions) runs INSIDE
`_harness_factory` BEFORE the TUI is up, and MCP connects even earlier (line 611). To prompt interactively,
the trust decision must be made/threaded BEFORE these load steps, but the interactive UI only exists inside
`run_tui` (`shell.py:956` bootstrap). **This is the central architectural tension** (pi solves it with a
`resolveProjectTrust` callback the resource-loader invokes lazily; aelix's loader does not have that seam).

### 2.7 The interactive prompt primitive — REUSABLE
- `tui/context.py:137-254` `AelixTUIContext.select(title, options)` — arrow-key + type-to-filter modal,
  returns `str | None` (Esc/c-c → None). `:256-282` `confirm`, `:284-311` `input`. These are exactly the pi
  `ui.select/confirm/input` shape.
- `PermissionExtension` (`builtin/permission.py:143-237`) is the proof-of-pattern: it gates `tool_call` via
  `ctx.ui.select(title, _OPTIONS)` with a 4-option dialog, `not ctx.has_ui → ALLOW`, fail-safe deny-on-error,
  session-scoped allow-list. **The trust UX can reuse this exact `ctx.ui.select` primitive.** Note the trust
  prompt is at STARTUP (before/at `run_tui` bootstrap), whereas permission is per-`tool_call` mid-turn — they
  do NOT overlap functionally (different surfaces: load-time vs run-time), but they SHARE the dialog
  primitive.
- `run_tui` (`shell.py:129-987`): real UI is bound at `shell.py:959` (`bind_ui(context)`) right after
  `harness.bootstrap()` (`:956`). A startup trust prompt would run here — but extensions/MCP are already
  loaded by then (built in `_build_harness_options` at factory time, line 631). See §3 for the seam options.

### 2.8 Protected-core footprint
- **`packages/aelix-agent-core/**` is GATE-PROTECTED** (confirmed: prior specs
  `.omc/specs/sprint-p0-6-…:31`, `sprint-p0-7-…:307,743-745`). Touching it needs explicit user approval.
- **`packages/aelix-coding-agent/**` is NOT gate-protected** (free to edit).
- **`packages/aelix-ai/**`** (where `SettingsManager` lives) — not flagged protected in the specs, but it is
  a shared library; treat changes there as higher-blast-radius.
- **Good news:** the MINIMAL slice (extensions + MCP gate) lives ENTIRELY in `aelix-coding-agent` →
  **ZERO protected-core footprint** if we don't add `ctx.isProjectTrusted()` (which would touch
  `aelix-agent-core/extensions` types + runner). FULL parity DOES touch protected core (the event +
  `isProjectTrusted` live in `aelix-agent-core`'s extension runner/types).

---

## 3. SCOPE OPTIONS (clearly costed)

### OPTION A — MINIMAL v1 ship-blocker subset (RECOMMENDED for v1)
**Goal:** make running aelix in an UNTRUSTED directory safe for a single-user-local product. Gate the **two
arbitrary-code surfaces only**: project-local extensions + project-local MCP servers. Sane non-interactive
default. Smallest safe slice.

**What ships:**
1. `Args.project_trust_override: bool | None` + parse `--approve/-a` (True) / `--no-approve/-na` (False)
   + help text. (`cli/args.py`)
2. A small `aelix_coding_agent/cli/project_trust.py` module (NEW, non-protected):
   - `has_trust_requiring_project_resources(cwd) -> bool` — aelix-narrowed: returns True iff
     `cwd/.aelix/extensions/` exists (has on-disk entries) OR a project-local `cwd/.aelix/mcp.json` exists.
     (Drop pi's settings.json/skills/prompts/themes/SYSTEM.md checks — aelix doesn't load those yet, §2.2.)
   - `resolve_project_trusted(cwd, *, override, has_ui, ui_select, default="ask"|"deny") -> bool` — the
     orchestrator: override → no-resources(=True) → [v1: skip event + skip disk] → `has_ui? prompt : DENY`.
   - The prompt reuses `ctx.ui.select` / a startup selector with pi-faithful wording (§1.3 adapted).
3. **Enforcement wiring** in `entry.py`:
   - Resolve trust ONCE in `_async_main` BEFORE MCP connect (line 611) and BEFORE the first
     `_build_harness_options`. Thread the resolved `project_trusted: bool` into `_build_harness_options` and
     the MCP block.
   - When `project_trusted is False`: pass `no_discovery=True`-equivalent for project-local extensions
     (keep explicit `-e` paths per pi `--approve` semantics? — decide: pi's `--no-approve` ignores ALL
     project-local files but `-e` is an explicit user choice → KEEP `-e`, gate only auto-discovered local)
     AND skip project-local `cwd/.aelix/mcp.json` servers (keep `$AELIX_MCP_CONFIG` / global, since those are
     user-chosen not project-local).
   - **Interactive seam problem (§2.6):** in interactive mode the UI isn't up at `_async_main` load time.
     Two sub-options:
     - **A1 (simplest):** resolve trust with a SHORT pre-TUI prompt using a minimal prompt-toolkit
       selector (a one-shot `prompt_toolkit` `Application` before `run_tui` builds its chrome), OR
     - **A2 (cleaner, pi-shaped):** add a `resolve_project_trust` callback seam to
       `discover_and_load_extensions` + the MCP connect, invoked lazily once the UI is bound in `run_tui`
       (`shell.py:956-959`). For print/json/rpc, resolve eagerly in `_async_main` with `has_ui=False` →
       deny-by-default (or `--approve`).
     → **Recommend A1 for v1** (a one-shot startup selector is the least invasive; A2 requires threading a
       callback through the harness factory which is fiddly). The selector can be a tiny dedicated
       `prompt_toolkit.Application` (aelix already depends on prompt-toolkit for `[tui]`).
4. Replace the `entry.py:372-381` post-hoc warning with the real gate (warning becomes redundant once gated;
   keep a one-line "loaded N trusted on-disk extensions" info if desired).

**Effort:** **M** (1 new ~150-line module + args + entry.py wiring + the startup selector + tests).
**Files touched (all `aelix-coding-agent`, NON-protected):**
- `cli/args.py` (flags + help)
- `cli/project_trust.py` (NEW)
- `cli/entry.py` (resolve + thread + MCP gate + extension gate)
- `tui/shell.py` (only if A2; for A1, a pre-`run_tui` selector lives in entry.py or a new helper)
- `tests/` (new test module)
**Protected-core footprint:** **ZERO** (no `aelix-agent-core` change; no `ctx.isProjectTrusted`).
**Blocks/enables:** Closes the arbitrary-code-execution hole = the actual ship-blocker. Does NOT persist
decisions (re-prompts each run in untrusted dirs) and does NOT expose the extension event — acceptable for a
single-user-local v1.

### OPTION B — FULL pi Project Trust parity
**Adds on top of A:**
1. **On-disk persistence** — port `ProjectTrustStore` (`trust.json` under `~/.aelix/agent/`), nearest-ancestor
   walk, parent-trust option, `Trust (this session only)` options, file lock. (`cli/project_trust.py` grows.)
2. **`defaultProjectTrust` setting** — add `default_project_trust: "ask"|"always"|"never"` to
   `aelix_ai.settings.Settings` (global scope) + getter/setter; read it in `resolve_project_trusted`. **Also
   requires actually CONSTRUCTING a `SettingsManager` in the coding-agent bootstrap** (it's never built today,
   §2.2) — a meaningful new wiring task by itself.
3. **Wire project `settings.json` loading + the `projectTrusted` suppression** — construct `SettingsManager`
   with `projectTrusted=<resolved>` and add the pi `if scope=="project" && !projectTrusted → {}` refusal to
   `aelix_ai.settings` (TOUCHES `aelix-ai`). Add `skills/prompts/themes/SYSTEM.md/APPEND_SYSTEM.md` to the
   resource-detection list (only meaningful once those subsystems exist in aelix — most do NOT yet).
4. **`project_trust` extension event** — port `ProjectTrustEvent`/`Result`/`Context` types +
   `emit_project_trust_event` (first-yes/no-wins, undecided-falls-through) into the extension runner. **This
   TOUCHES gate-protected `aelix-agent-core/extensions` (types + runner)** — needs user approval.
5. **`ctx.is_project_trusted()`** — add the read-only method to `ExtensionContext` + the runner default
   `() => True` + live binding. **TOUCHES gate-protected `aelix-agent-core`** — needs user approval.
6. **In-session trust selector** (`trust-selector.ts` analogue) — a `/trust` TUI command to view/change the
   saved decision mid-session (optional even within FULL).

**Effort:** **XL** (persistence + lockfile + settings field + SettingsManager construction + protected-core
event/ctx port + in-session command + tests for each).
**Files touched:** all of A, PLUS `aelix_ai.settings` (Settings field + projectTrusted suppression),
`aelix-agent-core/src/aelix_agent_core/.../extensions/types.py` + `runner` (PROTECTED), a new
`/trust` command in `tui/commands.py`.
**Protected-core footprint:** **NON-ZERO** — items 4 & 5 require approved diffs to `aelix-agent-core`.
**Blocks/enables:** Full pi behavioral parity (persisted trust, configurable default, extension-overridable
trust, extension-queryable trust state). Most of items 3's resource list is INERT in aelix (skills/prompts/
themes/SYSTEM.md loaders don't exist), so much of FULL parity gates resources that aelix doesn't load — i.e.
**FULL parity is largely speculative surface for v1**.

### Recommendation
Ship **OPTION A (MINIMAL)** for v1: it closes the real security hole (arbitrary code from untrusted dirs) with
zero protected-core risk and M effort. Defer **B** until aelix actually loads project settings/skills/prompts/
themes (then the FULL resource list and `projectTrusted` suppression become load-bearing rather than
speculative). Persistence (B.1) is the single most user-noticeable B feature (avoids re-prompting); it can be
cherry-picked into A as "A+" (M→L) without touching protected core, since the store is pure `aelix-coding-agent`.

---

## 4. Gap-inventory imprecisions found & corrected

1. **AGENTS.md is NOT a pi trust surface.** Any inventory line implying pi gates AGENTS.md/CLAUDE.md reading
   is WRONG: `hasTrustRequiringProjectResources` (`trust-manager.ts:184-206`) checks only `.pi/<7 resources>`
   and `.agents/skills` — markdown context files are not gated by pi. (The example-extension docstring's
   "AGENTS.md/CLAUDE.md" mention is misleading; the actual code doesn't include them.)
2. **`SettingsManager` exists in aelix but is never wired.** The trust "settings.json" surface is moot in v1 —
   nothing loads `.aelix/settings.json` in the coding agent today (§2.2). FULL parity must first BUILD a
   `SettingsManager` in the bootstrap (a prerequisite task often elided).
3. **Most of pi's 7 trust-requiring resources don't exist in aelix.** `skills`, `prompts`, `themes`,
   `SYSTEM.md`, `APPEND_SYSTEM.md` have no aelix loaders. Only `extensions` (real) + `mcp.json` (aelix-shaped,
   pi-divergent path) are live → the MINIMAL detection set is just those two.
4. **The aelix `entry.py:372-381` warning is NOT a gate.** It logs after loading. Any inventory treating it as
   partial trust enforcement is incorrect — it's purely cosmetic.
5. **Non-interactive default = DENY (pi).** `resolveProjectTrusted` returns `false` when `!hasUI` and no
   override/persisted/default-non-ask (`project-trust.ts:86-88`). So print/json/rpc in an untrusted dir
   silently DROP project-local resources unless `--approve`. v1 must match this (deny-by-default) — see §5.

---

## 5. DECISIONS THE USER MUST MAKE (surface explicitly)

1. **Scope: A (MINIMAL) vs A+ (MINIMAL + persistence) vs B (FULL)?** Recommend **A** for v1 ship; **A+** if
   re-prompting every run is unacceptable. **B** only when project settings/skills/etc. actually load.

2. **Non-interactive default (print/json/rpc — no prompt possible).** Pi = **deny-by-default** (drop
   project-local resources, require `--approve` to opt in). Options:
   - (a) **Pi parity: deny-by-default** + `--approve` to enable. Safest. *(Recommended.)*
   - (b) Trust-cwd-by-default in non-interactive (convenience, less safe — a piped `aelix -p` in a hostile
     repo would run its extensions/MCP). Not recommended for a security gate.
   Decision needed: **(a) or (b)?**

3. **Trust persistence: ephemeral per-session vs on-disk.** Pi persists to `~/.pi/agent/trust.json`
   (per-project map, nearest-ancestor inheritance). Options:
   - (a) **Ephemeral (A):** prompt each run in an untrusted dir; nothing written. Simplest, no lockfile.
   - (b) **On-disk (A+/B):** `~/.aelix/agent/trust.json`, pi-shape, with `Trust parent` + `session-only`
     options. More UX-friendly, needs a lock strategy (pi uses `proper-lockfile`; aelix could use `filelock`
     or accept single-process best-effort).
   Decision needed: **ephemeral or persisted? and if persisted, the lock strategy.**

4. **Trust-prompt UX (TUI dialog).** Reuse `ctx.ui.select` (the PermissionExtension pattern). Proposed wording
   (pi-faithful, `.aelix` substituted):
   > `Trust project folder?` / `<cwd>` / `This allows Aelix to load .aelix extensions and MCP servers, which
   > can execute arbitrary code on your machine.`
   Options for MINIMAL: `Trust` / `Do not trust` (+ `Trust this session only` if persistence ships). Decision
   needed: **exact wording + which options (2-option minimal vs pi's 5-option set).**

5. **Is AGENTS.md reading gated?** Pi: **NO** (§4.1). aelix injects up to 32 KiB of untrusted project markdown
   into the system prompt (prompt-injection vector, even if not code-exec). Decision needed: **follow pi
   (don't gate AGENTS.md) — recommended for parity — or add an aelix-additive gate/cap?** (Default: don't
   gate; it's out of the security-gate scope and would diverge from pi.)

6. **`--no-approve` and explicit `-e`/`$AELIX_MCP_CONFIG`.** Pi's `--no-approve` ignores ALL project-local
   files. But explicit `-e <path>` and `$AELIX_MCP_CONFIG` are USER choices, not project-local. Decision
   needed: **does the trust gate apply only to auto-discovered `cwd/.aelix/*` (recommended), or also to
   explicit `-e` / env-configured MCP (stricter, pi-literal)?** (Recommend: gate auto-discovered project-local
   only; trust explicit user flags.)

7. **`project_trust` event + `ctx.is_project_trusted()` (protected-core).** Only needed for FULL (B). Decision
   needed: **defer (A) or approve the protected `aelix-agent-core` diff (B)?**

8. **Single-user-local scope confirmation.** This spec assumes the product is single-user-local (one human at a
   terminal). If multi-tenant / server-hosted (aelix-server) is in scope, trust semantics differ (no
   interactive prompt; policy-driven). Decision needed: **confirm single-user-local scope for v1 trust.**

---

## 6. Per-item edit map (MINIMAL / Option A)

| # | Item | (a) pi source | (b) aelix file:line + gap | (c) precise change | (d) Protected | (e) test | (f) risks |
|---|---|---|---|---|---|---|---|
| 1 | `--approve/--no-approve` flags | `args.ts:180-183,274-275` | `cli/args.py` `Args` (45-183) + loop (199-383) + help (430-442) — none exist | Add `project_trust_override: bool|None=None`; parse `--approve/-a`→True, `--no-approve/-na`→False; help lines | N | parse test: `-a`→True, `-na`→False, absent→None | trivial; mind `-a` collision (none today) |
| 2 | `has_trust_requiring_project_resources` | `trust-manager.ts:184-206` | none | NEW `cli/project_trust.py`: True iff `cwd/.aelix/extensions/` has entries OR `cwd/.aelix/mcp.json` exists | N | tmp-dir matrix (empty/ext/mcp) | narrow vs pi (intentional, §2.2) |
| 3 | `resolve_project_trusted` | `project-trust.ts:46-96` | none | NEW: override→ no-resources(True)→ `has_ui? prompt : deny` (v1 skips event+disk) | N | override short-circuit; no-UI→deny; no-resources→True | seam (§2.6) — pick A1 |
| 4 | Trust prompt UX | `project-trust.ts:24-26` + `getProjectTrustOptions` | `tui/context.py:137` `select` reusable | One-shot startup selector (A1) or `ctx.ui.select` (A2); wording §5.4 | N | headless select returns option→trusted bool | wording = user decision |
| 5 | Extension load gate | `main.ts:624-647` resolveProjectTrust callback | `entry.py:363-369` loads ungated; `:372-381` warns post-hoc | Resolve trust before factory; when False, suppress auto-discovered `cwd/.aelix/extensions` (keep `-e`) | N | untrusted dir → 0 on-disk ext loaded | order (§2.6); keep explicit `-e` (decision §5.6) |
| 6 | MCP load gate | (pi: same resolveProjectTrust over resources) | `entry.py:611-620` connects ungated; `config.py:148-150` project-local path | When False, drop the project-local `cwd/.aelix/mcp.json` contribs (keep `$AELIX_MCP_CONFIG`/global) | N | untrusted dir → project mcp.json skipped, global kept | distinguish project vs global source in `load_mcp_server_contribs` (return source tag) |
| 7 | Replace post-hoc warning | n/a (aelix-additive) | `entry.py:372-381` | Remove/relegate the warning once gate is real | N | n/a | none |

### Additional FULL-only (Option B) items
| # | Item | pi source | aelix target | Protected |
|---|---|---|---|---|
| B1 | `ProjectTrustStore` (trust.json + nearest-ancestor + lock) | `trust-manager.ts:208-244,43-95,124-175` | `cli/project_trust.py` (grow) | N |
| B2 | `default_project_trust` setting | `settings-manager.ts:61,95,870-877` | `aelix_ai.settings.Settings` + getter/setter | aelix-ai (shared) |
| B3 | `projectTrusted` suppression + SettingsManager construction | `settings-manager.ts:344-347,529-531`; `main.ts:471,617` | `aelix_ai.settings` + `entry.py` (build SettingsManager) | aelix-ai (shared) |
| B4 | `project_trust` event (types + emit) | `types.ts:503-525`; `runner.ts:197-227` | `aelix-agent-core` extensions types + runner | **YES** |
| B5 | `ctx.is_project_trusted()` | `types.ts:318,1533`; `runner.ts:273,653-655` | `aelix-agent-core` ExtensionContext + runner | **YES** |
| B6 | In-session `/trust` selector | `trust-selector.ts` (1-134) | `tui/commands.py` + a selector component | N |

---

## 7. Test plan (MINIMAL)
1. `cli/args.py` parse: `--approve`→True, `-a`→True, `--no-approve`→False, `-na`→False, absent→None.
2. `has_trust_requiring_project_resources`: tmp dir matrix — empty(False), `.aelix/extensions/x.py`(True),
   `.aelix/mcp.json`(True), both(True).
3. `resolve_project_trusted`: override=True→True (no prompt), override=False→False (no prompt),
   no-resources→True, `has_ui=False`+resources→False (deny), `has_ui=True`→prompt result honored,
   cancel(None)→False.
4. Integration (entry-level, headless): untrusted dir (resources present, no `--approve`, non-interactive) →
   on-disk extensions NOT loaded AND project `.aelix/mcp.json` servers NOT connected; global/`-e`/`$AELIX_MCP_CONFIG`
   still load.
5. `--approve` in same untrusted dir → resources loaded.
6. Headless TUI select round-trip (reuse the PermissionExtension test harness pattern) → option maps to bool.
7. Regression: trusted dir / no dangerous resources → behavior byte-identical to today (no prompt, all loads).

---

## 8. Risks
- **Bootstrap-order seam (§2.6):** extensions load inside the harness factory and MCP connects before any UI
  exists. A1 (pre-`run_tui` one-shot selector) is the safest fix; A2 (lazy callback seam) is cleaner but
  threads a callback through `discover_and_load_extensions` + MCP connect + the harness factory (more surface).
- **Distinguishing project-local vs user-global resources:** `load_mcp_server_contribs` currently flattens
  source (override/project/global) into one list. The gate must know which contribs came from
  `cwd/.aelix/mcp.json` vs global → small refactor to return/tag the source. Same for extensions (the loader
  already separates tiers internally — `no_discovery` disables tiers 1+2 but also entry_points; gating ONLY
  tier-1 project-local needs a finer flag than the existing `no_discovery`).
- **`--no-approve` vs explicit `-e`:** literal pi `--no-approve` ignores all project-local files; aelix users
  expect explicit `-e <path>` to be honored. Resolve via decision §5.6 (recommend: gate auto-discovered only).
- **Non-interactive deny surprises users:** print/json/rpc in an untrusted dir silently drops project
  extensions/MCP (pi parity). Mitigate with a clear stderr notice ("project-local resources skipped; pass
  --approve to trust").
- **FULL-parity speculative surface:** B3's resource list gates loaders that don't exist in aelix yet → adding
  them now is dead code. Defer until those subsystems land.
- **Protected core (B only):** B4/B5 require approved `aelix-agent-core` diffs; out of scope for MINIMAL.
