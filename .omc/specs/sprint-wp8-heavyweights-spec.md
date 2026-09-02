# Sprint WP-8 — Heavyweight TUI Subsystems (master spec)

> Source of truth for the WP-8 implementation workflow. All contracts below are
> file:line-verified against the live tree (2026-06-21). Scope endorsed by the
> user: `/login`+`/logout` (API-key **and** OAuth), `/stats`, `/extension`,
> `/context` enrichment, multi-line statusline. **Subagent activity line is
> EXCLUDED — agent events carry empty payloads (genuinely blocked).**

## Hard constraints (every agent must obey)

- **PROTECTED, READ-ONLY:** `packages/aelix-ai/**` and
  `packages/aelix-agent-core/**`. NEVER edit them. All work is in
  `packages/aelix-coding-agent/src/aelix_coding_agent/tui/**` and
  `…/cli/entry.py` (pure consumer). Only CALL the protected APIs listed here.
- **Permission/security:** no change to the permission engine or trust posture.
- **Env / gate:** `cd /workspaces/aelix-ai`; if `tests/server` import-breaks after
  a `uv sync`, run `uv pip install -e packages/aelix-server --no-deps && uv pip
  install fastapi uvicorn`. Use `uv pip` / `python -m pip`, never bare `pip`.
  RPC SIGKILL-timing tests are flaky — ignore them.
- **Gate:** `ruff check <changed files>` clean, then
  `python -m pytest -q -p no:cacheprovider` green (note baseline pass count).
- **Degrade, never crash:** every handler/flow wraps failure in a committed
  `Text(..., style="bold red")` and returns — mirror the existing handlers in
  `tui/commands.py`. Never let a command kill the REPL.
- **Pattern fidelity:** match the shipped idiom — DI module (like
  `tui/model_picker.py`, `tui/scoped_models.py`, `tui/mcp_viewer.py`) holding the
  pure/testable flow; thin `_xxx_handler` in `commands.py`; `_open_xxx` flow in
  `shell.py::run_tui`; field on `CommandContext`; manager threaded from
  `entry.py`. Visual language = `_picker_frame` ANSI style (context.py:70-95).

## The command-add recipe (verified)

1. New DI module under `tui/` with a module-level `async def run_*` taking
   duck-typed deps (registry/manager/dialog-callables/commit) — unit-testable
   without prompt-toolkit (see `tui/model_picker.py`).
2. `tui/commands.py`: add `async def _xxx_handler(ctx, args)` (thin; delegates to
   a `CommandContext` callback) + a `BuiltinCommand("xxx", "desc", _xxx_handler)`
   in `BUILTIN_COMMANDS` (commands.py:926). Add any new `CommandContext` field
   (commands.py:54-172) with a docstring.
3. `tui/shell.py::run_tui`: define `async def _open_xxx()` near the others
   (shell.py:625-801) wiring the live deps into the DI module; pass it into the
   `CommandContext(...)` constructor (shell.py:803-826).
4. `cli/entry.py`: thread any new manager into the `run_tui(...)` call
   (entry.py:861-869).

## Dialog framework (context.py `AelixTUIContext`, all verified)

- `await select(title, options:list[str], opts=None, detail=None, initial_index=0) -> str|None`
  — single-choice; `detail(orig_idx)->list[str]` per-highlight footer.
- `await multiselect(title, options:list[(id,label,desc)], *, selected:set, extra_toggles=None, preview=None) -> (set,dict)|None`.
- `await confirm(title, message) -> bool`.
- `await input(title, placeholder=None) -> str|None`.
- `await editor(title, prefill=None) -> str|None`.
- `notify(message, kind="info") -> None`.
- `await custom(factory, options)` — NOTE: its Window has NO key_bindings, so it
  CANNOT handle Tab/arrows. **Do not use `custom` for tabbed UIs.**
- `show_modal(chrome, build_content, *, options=None, ...)` (overlay.py:259) — the
  shared in-flow modal primitive; `build_content(future)->Window` whose
  KeyBindings resolve the future. This is how `select` handles keys.
- ANSI style consts (context.py:70-74): `_PICK_SEL` bold-cyan, `_PICK_DIM` dim,
  `_PICK_BOLD`, `_PICK_RST`; framing via `_picker_frame(title, body, hint, width)`.

### NEW shared primitive — `AelixTUIContext.tabbed` (add to context.py)

A reusable tabbed-viewer modal (the missing piece for `/stats` + `/extension`).
Build it like `select()` (uses `show_modal` + a local `KeyBindings`), do NOT use
`custom()`.

```python
async def tabbed(
    self,
    title: str,
    tabs: list[tuple[str, Callable[[], list[str]]]],  # (tab_name, render->lines)
    *,
    initial: int = 0,
) -> None:
    """A framed tabbed viewer: a tab header row (active tab bold-cyan), the
    active tab's rendered lines, and a dim hint. Keys: Tab / Right = next tab,
    Shift-Tab / Left = prev tab (wraps), Esc / Ctrl-C / q = close. Each
    render() is guarded (a raising tab shows an error line, never breaks the
    modal). Empty `tabs` returns immediately."""
```

- Header: `  Installed   Discover   Sources   (Tab / ←→ to switch)` with the
  active tab `_PICK_SEL`, others plain; body = active `render()` lines; hint dim
  `Tab/←→ switch · Esc close`. Frame via `_picker_frame`.
- This is read-only (no row selection) — viewers only. (If a tab later needs row
  actions, extend separately; not in scope.)
- Add a unit test that drives the key bindings headlessly (mirror the existing
  select/multiselect tests' approach in tests/tui/).

---

## Feature 1 — `/login` + `/logout` (auth wizard)

**Backing (all in `aelix_ai.oauth`, READ-ONLY):**
- `AuthStorage` (oauth/auth_storage.py): `set_api_key(provider, key)` (persists
  immediately), `await login(provider_id, callbacks)` (OAuth), `await
  logout(provider)` / `remove`, `list()->list[str]` (stored ids; call `load()`
  first), `await get_auth_status(provider)->AuthStatus(configured,source,label)`,
  `await has_auth(provider)`, `get_all()`.
- OAuth registry (oauth/_registry.py): `get_oauth_providers()->list[OAuthProvider]`
  → built-ins **anthropic, github-copilot, openai-codex** (each `.id`,`.name`).
- `OAuthLoginCallbacks` (oauth/types.py:140): `on_auth(OAuthAuthInfo{url,instructions})`,
  `on_prompt(OAuthPrompt{message,placeholder,allow_empty})->str`,
  `on_progress(str)`, `on_manual_code_input()->str`, `on_select(OAuthSelectPrompt
  {message,options:[OAuthSelectOption{id,label}]})->str|None`, `signal`.
- Builtin API-key providers: `aelix_ai.providers._env_api_keys.ENV_API_KEYS`
  keys (provider -> env var names).

**Threading:** `auth_storage` is built in entry.py:678 but NOT passed to
`run_tui` — add an `auth_storage` kw param to `run_tui` and pass it
(entry.py:861). It is the SAME object inside `ModelRegistry.create(auth_storage)`
so a stored key is visible to model resolution immediately (no reload).

**Module `tui/login_wizard.py`:**
```python
async def run_login(*, auth_storage, select, prompt_input, confirm, notify, commit) -> None
async def run_logout(*, auth_storage, select, confirm, commit) -> None
```
- `run_login` step 1 — method `select`: `["Using OAuth (sign in to a subscription
  /account)", "Using an API key (built-in provider)", "Custom provider (OpenAI /
  Anthropic / Gemini-compatible endpoint)"]`.
  - **OAuth:** `select` over `get_oauth_providers()` (label `name`), build
    `OAuthLoginCallbacks` mapping `on_auth`→commit a Panel with the URL +
    best-effort `webbrowser.open(url)` (guarded); `on_prompt` /
    `on_manual_code_input`→`prompt_input`; `on_select`→`select`;
    `on_progress`→`notify`. `await auth_storage.login(id, callbacks)`. On success
    commit green `signed in to {name}`; surface RuntimeError("Unknown provider")
    + any exception as a red line.
  - **API key:** `select` provider from sorted `ENV_API_KEYS` keys →
    `prompt_input("API key for {provider}")` (non-empty) →
    `auth_storage.set_api_key(provider, key)` → commit green +
    `await get_auth_status` confirmation. (set_api_key persists; no save() needed.)
  - **Custom:** protocol `select` `[OpenAI-compatible, Anthropic-compatible,
    Gemini-compatible]` → `prompt_input` provider-id → base-url → api-key →
    `set_api_key(provider_id, key)` → commit green + an HONEST note that the model
    itself must be added via models.json / `--models` to be selectable (auth half
    works; model wiring is models.json's job — do NOT claim the model is ready).
- `run_logout`: `await auth_storage.load()`; `ids = auth_storage.list()`; if empty
  → commit "No stored credentials." ; else `select` an id → `confirm` →
  `auth_storage.logout(id)` → commit green.

**Wiring:** `CommandContext.login_action` / `logout_action` callbacks;
`_login_handler` / `_logout_handler` (no-arg; degrade when callback None);
`BuiltinCommand("login", "Sign in / add a provider API key", _login_handler)`,
`BuiltinCommand("logout", "Remove a provider's stored credentials",
_logout_handler)`. `_open_login` / `_open_logout` in shell.py wire
`auth_storage` + `context.select/input/confirm/notify` + `_commit`.

**Tests** `tests/tui/test_login_wizard.py`: a FakeAuthStorage records
set_api_key/logout/login; fake dialog callables script the choices; assert the
key is stored / removed; assert the OAuth callback wiring calls login with a
callbacks bundle; assert empty-logout + cancel + unknown-provider degrade paths.

---

## Feature 2 — `/stats` (usage dashboard) + session activity tracker

**Backing:** `SessionStats` (harness `await get_session_stats()`):
`tool_calls, tool_results, user_messages, assistant_messages, total_messages,
tokens{input,output,cache_read,cache_write,total}, cost`. NO per-tool success,
durations, per-model, or cross-session history → build a **TUI-side tracker**.

**Agent events (verified, render.py:274-289):** `tool_execution_start`,
`tool_execution_end` (`.tool_name`, `.result`, `.is_error`), `message_start`,
`message_update`, `message_end` (`.message` w/ `.usage`), `turn_end`. (`turn_start`
may also exist — handle defensively via getattr on `.type`.)

**Module `tui/activity_tracker.py`:**
```python
@dataclass(frozen=True) class ToolStat: name:str; calls:int; failures:int
@dataclass(frozen=True) class ModelStat: model:str; requests:int; input:int; output:int; cache_read:int
@dataclass(frozen=True) class ActivitySnapshot:
    tool_calls:int; tool_failures:int; per_tool:list[ToolStat]
    per_model:list[ModelStat]; turns:int; wall_seconds:float
    @property success_rate -> float|None
class SessionActivityTracker:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, model_provider=None): ...
    def on_event(self, event: object) -> None  # dispatch on getattr(event,"type")
    def snapshot(self) -> ActivitySnapshot
```
- `tool_execution_end` → inc per-tool `calls`, inc `failures` if `is_error`,
  accumulate duration (paired with start time by tool name; best-effort).
- `message_end` → read `event.message.usage` (use the `_session_stats._read`
  pattern: dict-or-attr) for tokens; model id from
  `getattr(event.message,"model",None)` or `model_provider()`; accumulate per-model.
- turn count + wall = first-event clock → last-event clock (monotonic).
- Pure + injected clock → fully unit-testable.

**Wiring in shell.py:** instantiate `tracker = SessionActivityTracker(
model_provider=_model_id)`; call `tracker.on_event(event)` at the TOP of
`_on_agent_event` (shell.py:977) BEFORE the renderer; reset/re-point on `_rebind`
(new session → fresh tracker, or `tracker.reset()`); thread `tracker` into
`CommandContext` (`activity_tracker` field) + `_open_stats`.

**Module `tui/stats_dashboard.py`:**
```python
def build_session_tab(stats, snapshot) -> list[str]
def build_activity_tab(snapshot) -> list[str]
def build_efficiency_tab(snapshot) -> list[str]
async def run_stats(*, stats_getter, snapshot, tabbed, commit) -> None
```
- Session tab: tool calls `N (✓ok ✗fail)`, success %, +tokens in/out/cached, cost,
  messages, wall time. Activity tab: turns, per-model table (reqs/in/out/cache).
  Efficiency tab: cache-hit %, tool success %, per-tool leaderboard
  (calls/failures). **Honestly state** that cross-session heatmap/trend are
  omitted (no history retained) — a dim footer line, not silent.
- `run_stats` calls `await stats_getter()` (guard) + `snapshot` then
  `await tabbed("Usage statistics", [("Session", …), ("Activity", …),
  ("Efficiency", …)])`.

**Wiring:** `CommandContext.stats_action`; `_stats_handler`;
`BuiltinCommand("stats", "Session usage statistics (tools, tokens, models)",
_stats_handler)`; `_open_stats` in shell.py.

**Tests** `tests/tui/test_activity_tracker.py` (feed scripted events → assert
counts/failures/success-rate/per-model/wall) + `test_stats_dashboard.py` (the
three build_*_tab formatters over a fixture snapshot).

---

## Feature 3 — `/extension` (manager)

**Backing:** loaded extensions = `loaded.extensions` from
`discover_and_load_extensions(...)` (entry.py:390-406) — a `list[Extension]`
(each has a manifest: name/version; inspect `extensions/api.py::Extension`).
MCP = `McpClientManager.connections` (already threaded as `mcp_manager`). Runtime
enable/disable is NOT supported (extensions load at startup) → **read-only
viewer** with an honest note. No marketplace → Discover/Sources honest-empty.

**Threading:** capture the `LoadExtensionsResult` in entry.py (the
`discover_and_load_extensions` call) and thread the `extensions` list into
`run_tui` as a new kw param `extensions`. (If the load happens per harness-build
inside `_build_harness_options`, hoist the discovered list or capture it once at
top-level so run_tui gets a stable list; an empty list is the safe default.)

**Module `tui/extension_manager.py`:**
```python
def build_installed_lines(extensions, mcp_conns) -> list[str]
def build_discover_lines() -> list[str]
def build_sources_lines() -> list[str]
async def run_extension_manager(*, extensions, mcp_manager, tabbed, commit) -> None
```
- Installed: one line per extension (`✓ {name} {version}` — degrade missing
  fields), then MCP servers (`{name} — {transport} — connected/disconnected`).
  Empty → `No plugins or MCP servers installed.`
- Discover/Sources: honest static text per mockup H (`No registry configured…`).
- `run_extension_manager` → `await tabbed("Extensions", [("Installed", …),
  ("Discover", …), ("Sources", …)])`.

**Wiring:** `CommandContext.extension_action`; `_extension_handler`;
`BuiltinCommand("extension", "Manage installed extensions + MCP servers",
_extension_handler)`; `_open_extension` in shell.py (wires `extensions` +
`mcp_manager` + `context.tabbed` + `_commit`).

**Tests** `tests/tui/test_extension_manager.py`: build_installed_lines over fake
extensions + fake mcp conns; empty-state; build_discover/sources static.

---

## Feature 4 — `/context` enrichment

Upgrade the EXISTING `_context_handler` (commands.py:741) — keep the shipped
Used/Free/Autocompact-buffer table + `_context_bar` — and ADD an **estimated
per-category composition** section when the sources are reachable.

**Module `tui/context_usage.py`:**
```python
def estimate_tokens(text: str) -> int            # heuristic: ceil(len/4), labeled "est."
@dataclass(frozen=True) class Category: name:str; tokens:int
def estimate_categories(*, system_prompt:str|None, tool_schemas:list, messages:list, memory_text:str|None) -> list[Category]
def build_category_lines(categories, window:int) -> list[str]   # per-cat bar + tokens + %
```
- Categories: System prompt, Built-in tools, Memory files, Messages (Skills if a
  source exists, else omit). Each = estimate over its text; render a small bar +
  `Nk tokens (X%)` of the window.
- **Honesty:** label the section `Estimated composition (≈, may not sum to the
  measured total)`. The MEASURED total stays from `SessionStats.context_usage`.
- Sources (the handler gathers, all guarded — omit a category whose source is
  unreachable): system prompt — find the seam (cli/agent_context.py builds it;
  check `harness.system_prompt` / options); tool schemas —
  `harness._action_get_all_tools()` (already used by `_tools_handler`); messages —
  `harness.messages`; memory — AGENTS.md text if loaded (guard, may be absent).

**Wiring:** modify `_context_handler` in place to append the category section when
`estimate_categories` yields ≥1 category; keep current behaviour otherwise. No
new CommandContext field needed (handler reads `ctx.harness`).

**Tests** `tests/tui/test_context_usage.py`: estimate_tokens monotonic;
estimate_categories over fixtures (omits unreachable); build_category_lines % math
+ never overflows the window.

---

## Feature 5 — Multi-line statusline (chrome footer)

Render the footer as the mockup-A multi-line block when enabled; keep the current
single-line footer as the default. **Gate it behind a persisted setting** so it is
opt-in and reversible.

**Layout (mockup A), built from the SAME segment registry:**
```
[provider] model | ⎇ branch | Context NN% left
📂 cwd | Context NN% used
<permission badge> <steering>           (omitted when both empty)
```
Map registry segments → rows; omit empty rows. Reuse the segment producers
(footer_segments.py) — do not re-derive values.

**chrome.py:** the footer is a single fixed `height1` row
(`_ansi_row(lambda: self._footer_line)`, chrome.py:435) and `set_footer_line`
strips `\n` (chrome.py:832). Change ONLY the footer row to a multi-line-capable
Window: keep a 1-row min, drop the fixed `max=1`, `dont_extend_height=True`, and
let `self._footer_line` carry `\n`. Add `set_footer_block(lines: list[str])` OR
make `set_footer_line` preserve newlines for the footer specifically (KEEP
header/breadcrumb single-line — they still strip `\n`). Provide a
`footer_line_count()` accessor.

**overlay.py reserve:** `_MODAL_STATUS_FOOTER_ROWS = 2` (overlay.py:59) assumes a
1-row footer. With a taller footer the modal reserve must grow by the extra footer
rows or a near-cap modal clips. Update `_reserve_rows` to read the live footer
line count (`chrome.footer_line_count()`), guarded with the current floor.

**context.py `_refresh_footer`:** when multi-line mode is on, compose grouped rows
(join within a row by `  ·  `, join rows by `\n`) and call the footer block
setter; else keep the existing single-line `"  ·  ".join(parts)`. Read the mode
from the statusline store / settings (a new bool).

**Setting:** add a persisted bool (e.g. `statusLineMultiline`) — prefer the
existing `StatuslineStore` (statusline_store.py) so `/statusline` owns it, or a
SettingsManager field + a `/settings` row ("Multi-line status line"). Pick the
lighter seam; persist + apply live (`refresh_footer`).

**Tests** `tests/tui/`: footer composer emits N rows in multi-line mode and 1 in
single; `set_footer_line`/block round-trips newlines for the footer only; overlay
reserve grows with footer rows (a focused unit test on `_reserve_rows`).

---

## Integration order + conflict map

Shared files touched by multiple features → integrate in this ORDER to avoid
collisions (modules are separate files; author them in parallel first):

1. **entry.py** — thread `auth_storage` (F1) + `extensions` (F3) into `run_tui`.
2. **context.py** — add `tabbed()` (F2,F3) + multi-line `_refresh_footer` (F5).
3. **chrome.py** + **overlay.py** — multi-line footer + reserve (F5).
4. **commands.py** — F1/F2/F3 handlers + CommandContext fields + BUILTIN_COMMANDS;
   F4 in-place edit to `_context_handler`.
5. **shell.py** — `_open_login/_logout/_stats/_extension` flows + tracker
   instantiation/reset + `command_ctx` wiring + footer-mode hookup (F5).

A single integration pass should own steps 1-5 coherently (it imports the
parallel-authored modules by their LOCKED signatures above). New BUILTIN_COMMANDS
order suggestion: insert `login, logout` after `model`; `stats` after `cost`;
`extension` after `mcp`.

## Definition of done

- All five features wired; `ruff` clean on changed files; full pytest green
  (record before/after pass counts; the 3 known-flaky RPC SIGKILL tests excepted).
- Each feature degrades (no REPL crash) on its failure paths.
- Live smoke (`python -m aelix_coding_agent --model openai/gpt-4o-mini`):
  `/login` (API-key path stores a key), `/logout`, `/stats` (3 tabs switch),
  `/extension` (3 tabs), `/context` (category section), and the multi-line
  statusline toggle render without clipping in a short terminal.
- Code-review (correctness + the F5 modal-reserve regression + the F1 auth
  handling) APPROVE/addressed.
