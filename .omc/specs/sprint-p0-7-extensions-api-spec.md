# P0 #7 EXTENSIONS-API — Recon / Edit Map (pi pin `734e08e`)

Repo root: `/workspaces/aelix-ai`. Pi pin SHA: `734e08e` (repo `earendil-works/pi`).
This is a recon deliverable: a precise, verbatim-grounded, item-by-item EDIT MAP gating a
multi-agent implementation workflow. No files were edited during recon.

---

## Critical corrections to the gap-inventory premises (read first)

1. **`get_flag` (item 1) — gap claim CONFIRMED, but the pi shape differs from the ADR-0093 sketch.**
   Pi `getFlag` (`loader.ts:263-267`) reads `runtime.flagValues.get(name)` and returns
   `undefined` if the extension never registered the flag — it does **NOT** fall back to
   `flag.default` at read time. The default is seeded into `flagValues` at **registration**
   time (`loader.ts:251-253`: `if (default !== undefined && !flagValues.has(name)) flagValues.set(name, default)`).
   Aelix `register_flag` does NOT seed `flag_values`, and `get_flag` reads `flag.default`.
   The faithful fix is **two-part**: seed on register + read from `flag_values`.

2. **`assert_active` coverage (item 2) — the gap-inventory's "8 registration methods" framing is WRONG/inverted.**
   Pi calls `runtime.assertActive()` at the top of **every** `ExtensionApi` method — including all
   register* methods (`on`, `registerTool`, `registerCommand`, `registerShortcut`, `registerFlag`,
   `registerMessageRenderer`, `getFlag`) AND all action methods. The methods the prompt lists
   (`ui/cwd/sessionManager/.../newSession/fork/...`) are guarded by a **separate** mechanism — the
   lazy getters in `createContext()`/`createCommandContext()` (`runner.ts:573-668`), which is what
   aelix's `ExtensionContext.__getattribute__` mirrors. Aelix's gap is the **opposite** of what the
   prompt implies: aelix's `ExtensionAPI` register*/get_flag methods do **NOT** call `assert_active`
   at all (verified: `register_tool@1316`, `register_flag@1329`, `get_flag@1339`, `on@1290`,
   `register_command@1372`, `register_shortcut@1392`, `register_message_renderer@1403` — none call
   `self._runtime.assert_active()`; only the action methods like `send_message@1446` do). The minimal
   pi-faithful fix is to add `self._runtime.assert_active()` as the first line of the 7
   registration/flag-read methods.

3. **`message_end` reducer (item 5) — ADR-0018's deprecation rationale is a LAYER MIX-UP, confirmed.**
   ADR-0018 correctly states pi's **agent-harness** layer has no message_end reducer — but the
   reducer lives in the **extension-runner** layer (`runner.ts:714 emitMessageEnd`), consumed by
   `agent-session.ts:675` via `_replaceMessageInPlace`. Aelix's architecture collapses both pi layers
   into one emit closure (`core.py:3616 emit`), so the reducer has to be grafted onto that closure.
   **This is the only item that touches protected `aelix-agent-core`'s message flow.**

4. **`--api-key` (item 6) — the harness ALREADY consumes auth; only the CLI populates it.**
   `AgentHarnessOptions.get_api_key_and_headers` exists (`core.py:240`) and `_make_stream_fn` consumes
   it (`core.py:3447-3472`). `AuthStorage.set_runtime_api_key` exists (`auth_storage.py:401`) and
   `get_api_key_cascade` checks the runtime override first. So item 6 is **entirely in unprotected
   coding-agent code** (`_build_harness_options`) — no core change.

5. **`register_tool` refresh (item 3) — gap CONFIRMED.**
   Pi `registerTool` (`loader.ts:217-225`) calls `runtime.refreshTools()`. Aelix `register_tool`
   (`api.py:1309-1316`) just stores. Aelix has NO `refresh_tools` action in the runtime action table.
   The fix needs a new action + a PROTECTED harness binding.

6. **`ExtensionCommandContext` (item 4) — has zero live callers.**
   It's defined (`command_context.py`) but never instantiated in the CLI. The runtime plumbing it
   needs already exists (`AgentSessionRuntime.new_session/switch_session/fork`,
   `harness.create_replaced_session_context(runtime=...)`).

---

## ITEM 1 — `get_flag` → `flag_values` precedence

### (a) Verbatim pi source — `packages/coding-agent/src/core/extensions/loader.ts`

```ts
// registerFlag (246-255):
registerFlag(
    name: string,
    options: { description?: string; type: "boolean" | "string"; default?: boolean | string },
): void {
    runtime.assertActive();
    extension.flags.set(name, { name, extensionPath: extension.path, ...options });
    if (options.default !== undefined && !runtime.flagValues.has(name)) {
        runtime.flagValues.set(name, options.default);
    }
},

// getFlag (262-267):
// Flag access - checks extension registered it, reads from runtime
getFlag(name: string): boolean | string | undefined {
    runtime.assertActive();
    if (!extension.flags.has(name)) return undefined;
    return runtime.flagValues.get(name);
},
```

### (b) Current aelix code + file:line — `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`

- `register_flag` @1319-1334: stores `ExtensionFlag` on `self._extension.flags[name]`; does **not**
  seed `self._runtime.flag_values`.
- `get_flag` @1336-1342:
  ```python
  def get_flag(self, name: str) -> bool | str | None:
      """Return the flag's current value (Phase 1.2: always the default)."""
      flag = self._extension.flags.get(name)
      if flag is None:
          return None
      return flag.default
  ```
  Reads the static default, ignoring `self._runtime.flag_values`.
- `self._runtime` is reachable from `ExtensionAPI` (constructor @934: `self._runtime = runtime`).
- `flag_values` lives on `_ExtensionRuntime` (@409: `self.flag_values: dict[str, bool | str] = {}`);
  `set_flag_value`@520, `get_flag_values`@510 already manage it (ADR-0093, never wired into `get_flag`).

### (c) Precise change (api.py)

- In `register_flag`, after storing the flag, append:
  ```python
  if default is not None and name not in self._runtime.flag_values:
      self._runtime.flag_values[name] = default
  ```
  (Match pi `default !== undefined` → aelix `default is not None`. The `default` param is `bool | str | None`.)
- Rewrite `get_flag`:
  ```python
  def get_flag(self, name: str) -> bool | str | None:
      self._runtime.assert_active()  # item 2
      if name not in self._extension.flags:
          return None
      return self._runtime.flag_values.get(name)
  ```

### (d) PROTECTED?
No — `extensions/api.py` is in `aelix-coding-agent`.

### (e) Test plan
- `register_flag("x", type="bool", default=True)` then `get_flag("x") == True`.
- After `runtime.set_flag_value("x", False)`, `get_flag("x") == False` (precedence over default).
- `get_flag("unregistered") is None`.
- Register with no default → `get_flag` returns `None` (flag_values unseeded).
- A second `register_flag` with a different default does NOT overwrite an already-set `flag_values`
  entry (pi `!has(name)` guard).

### (f) Risks
Low. Behavior change for any existing test asserting `get_flag` returns the static default after a
`set_flag_value`. Grep shows the flag_values machinery (6h₇c / ADR-0093) was added but `get_flag` was
never updated to read it — this is finishing an incomplete port. Verify no test pins the old
"always default" behavior.

---

## ITEM 2 — `assert_active` coverage on register*/get_flag

### (a) Verbatim pi source — `loader.ts` (createExtensionAPI)

Every method begins with `runtime.assertActive();` — representative excerpts:

```ts
on(event: string, handler: HandlerFn): void {
    runtime.assertActive();
    const list = extension.handlers.get(event) ?? [];
    list.push(handler);
    extension.handlers.set(event, list);
},

registerTool(tool: ToolDefinition): void {
    runtime.assertActive();
    extension.tools.set(tool.name, { definition: tool, sourceInfo: extension.sourceInfo });
    runtime.refreshTools();
},

registerCommand(name, options): void {
    runtime.assertActive();
    extension.commands.set(name, { name, sourceInfo: extension.sourceInfo, ...options });
},

registerShortcut(shortcut, options): void {
    runtime.assertActive();
    extension.shortcuts.set(shortcut, { shortcut, extensionPath: extension.path, ...options });
},

registerFlag(name, options): void {
    runtime.assertActive();
    /* ...flag store + default seed... */
},

registerMessageRenderer<T>(customType, renderer): void {
    runtime.assertActive();
    extension.messageRenderers.set(customType, renderer as MessageRenderer);
},

getFlag(name: string): boolean | string | undefined {
    runtime.assertActive();
    if (!extension.flags.has(name)) return undefined;
    return runtime.flagValues.get(name);
},
```

(The lazy context getters `createContext()`@573-668 use a SEPARATE `runner.assertActive()` per getter
— that is the mechanism aelix's `ExtensionContext.__getattribute__` already mirrors, NOT what this item
is about.)

### (b) Current aelix code + file:line — `api.py`

These `ExtensionAPI` methods do NOT call `assert_active` (verified):
- `on`@1290 (body: `if event not in HOOK_RESULT_TYPES: ...; bucket.append(handler)` — no guard)
- `register_tool`@1316
- `register_flag`@1329
- `get_flag`@1339
- `register_command`@1372
- `register_shortcut`@1392
- `register_message_renderer`@1403
- `add_cleanup`@1347 (aelix-additive; pi has no `addCleanup`)

Action methods DO guard (e.g. `send_message`@1446 begins `self._runtime.assert_active()`).
The lazy-getter guarding is already correct via `ExtensionContext.__getattribute__`@748.

### (c) Precise change (api.py)
Add `self._runtime.assert_active()` as the first statement of: `on`, `register_tool`, `register_flag`,
`get_flag`, `register_command`, `register_shortcut`, `register_message_renderer`. `add_cleanup` is
aelix-additive — guarding it is optional/discretionary (recommend guarding for consistency, flag as
aelix-additive, not a parity requirement).

### (d) PROTECTED?
No — `aelix-coding-agent`.

### (e) Test plan
- After `runtime.invalidate()`, each of the 7 register*/get_flag methods raises
  `ExtensionError("stale", …)`.
- Confirm a non-stale runtime still registers normally (no regression during load).

### (f) Risks
Low. Extensions load by calling the factory with a fresh (non-stale) API, so the guard is a no-op
during normal load — it only bites if an extension captures `pi` and calls register* after a session
replacement (pi's exact intent). Verify the loader doesn't invalidate the runtime mid-load (it doesn't
— invalidate is only on dispose/replace).
**Gap-inventory "8 registration methods" correction:** there are 7 register*/flag methods that need
the guard (not 8), and the framing that aelix "guards 8" was inverted — aelix guards **zero** today.

---

## ITEM 3 — `register_tool` refresh

### (a) Verbatim pi source

`loader.ts:217-225` (registerTool):
```ts
registerTool(tool: ToolDefinition): void {
    runtime.assertActive();
    extension.tools.set(tool.name, { definition: tool, sourceInfo: extension.sourceInfo });
    runtime.refreshTools();
},
```

`loader.ts:171` (pre-bind runtime stub — NO-OP, not throwing):
```ts
// registerTool() is valid during extension load; refresh is only needed post-bind.
refreshTools: () => {},
```

`runner.ts:284` (bindCore wires the real action):
```ts
this.runtime.refreshTools = actions.refreshTools;
```

`agent-session.ts:2192` (the action provider):
```ts
refreshTools: () => this._refreshToolRegistry(),
```

`agent-session.ts:2238-…` (`_refreshToolRegistry` — rebuilds `_toolRegistry` from
`getAllRegisteredTools()` + base defs + custom tools, recomputes active-set, re-wraps). Key active-set
logic (≈2308-2320 region):
```ts
const nextActiveToolNames = (
    options?.activeToolNames ? [...options.activeToolNames] : [...previousActiveToolNames]
).filter((name) => isAllowedTool(name));
// ...
} else if (!options?.activeToolNames) {
    for (const toolName of this._toolRegistry.keys()) {
        if (!previousRegistryNames.has(toolName)) {
            nextActiveToolNames.push(toolName);   // newly-registered tools auto-activate
        }
    }
}
```

### (b) Current aelix code + file:line

- `register_tool` @api.py:1309-1316 just stores `self._extension.tools[tool.name] = tool`.
- `ExtensionRuntimeActions` dataclass (api.py:300-334) has NO `refresh_tools` field; `_default_actions`
  @350-368 doesn't create one.
- Harness binds actions at `harness/core.py:612-633` via `_RuntimeActions(...)` (NO refresh_tools).
- Harness has `_rebuild_tool_registry`@739 (merges `extension.tools` + `options.tools`) and `set_tools`
  @2270 (re-assigns `self._state.tools` with strict active-name validation).
  `_state.tools = list(tools)` is the live tool list the loop reads (core.py:3601
  `active_tools = list(self._state.tools)`); per-turn `AgentContext(... tools=active_tools)` built once
  per turn @3610.

### (c) Precise change

1. `extensions/api.py` (UNPROTECTED):
   - Add `refresh_tools: Callable[[], None]` to `ExtensionRuntimeActions`.
   - Add `refresh_tools=lambda: None` to `_default_actions()` — **NO-OP default, not a throwing stub**
     (faithful to pi's `() => {}` so `register_tool` is valid during load before bind). This is the one
     place aelix's throwing-stub convention must diverge to match pi.
   - In `register_tool` (after the item-2 `assert_active` and the store), call
     `self._runtime.actions.refresh_tools()`.
2. `harness/core.py` (**PROTECTED**):
   - Add `refresh_tools=self._refresh_extension_tools` to the `_RuntimeActions(...)` call @612.
   - Add a harness method `_refresh_extension_tools` that recomputes
     `self._state.tools = self._rebuild_tool_registry()` and refreshes the active set, mirroring pi's
     `_refreshToolRegistry` active-set recompute: newly-registered tools become active when no explicit
     filter (`active_tool_names is None`); an explicit filter is preserved. Use **direct
     `_state.tools` assignment**, NOT the public `set_tools` (whose strict validator @2310 raises on
     stale active names).

### (d) PROTECTED?
**YES — partially.** The action-field + `register_tool` change are unprotected (`api.py`). The
**binding** (`bind_core` call site core.py:612) and the new `_refresh_extension_tools` method are in
**protected `aelix-agent-core/harness/core.py`**. Requires user approval for the protected diff.
There is no clean unprotected path: `bind_core` is called inside `AgentHarness.__init__`, and the
action implementations are harness methods, so a faithful refresh that re-reads `extension.tools` and
updates `_state.tools` must live on the harness.

### (e) Test plan
- Build a harness with one extension; from a hook handler call `register_tool(new_tool)`; assert
  `harness.state.tools` now contains `new_tool` and (no active filter) it's in the active set.
- With an explicit `active_tool_names` filter, assert the new tool is NOT auto-activated (pi keeps the
  explicit filter).
- Assert load-time `register_tool` (pre-bind) does not raise (no-op refresh default).

### (f) Risks
MEDIUM. (1) Active-set recompute must match pi exactly or tools register but never become callable.
(2) Must bypass `set_tools`'s strict validator (direct `_state.tools` assignment). (3) Re-entrancy:
`register_tool` during a turn mutating `_state.tools` while the loop snapshotted it — pi snapshots
per-turn (`AgentContext` built once @3610), so mid-turn registration only takes effect next turn;
verify aelix matches.

---

## ITEM 4 — `ExtensionCommandContext.new_session` / `switch_session` (stop raising)

### (a) Verbatim pi source

`types.ts:333-364` (`ExtensionCommandContext`):
```ts
export interface ExtensionCommandContext extends ExtensionContext {
    waitForIdle(): Promise<void>;
    newSession(options?: {
        parentSession?: string;
        setup?: (sessionManager: SessionManager) => Promise<void>;
        withSession?: (ctx: ReplacedSessionContext) => Promise<void>;
    }): Promise<{ cancelled: boolean }>;
    fork(
        entryId: string,
        options?: { position?: "before" | "at"; withSession?: (ctx: ReplacedSessionContext) => Promise<void> },
    ): Promise<{ cancelled: boolean }>;
    navigateTree(
        targetId: string,
        options?: { summarize?: boolean; customInstructions?: string; replaceInstructions?: boolean; label?: string },
    ): Promise<{ cancelled: boolean }>;
    switchSession(
        sessionPath: string,
        options?: { withSession?: (ctx: ReplacedSessionContext) => Promise<void> },
    ): Promise<{ cancelled: boolean }>;
    reload(): Promise<void>;
}
```

`runner.ts:636-668` (`createCommandContext` overlays + delegates):
```ts
context.newSession = (options) => { this.assertActive(); return this.newSessionHandler(options); };
context.fork = (entryId, options) => { this.assertActive(); return this.forkHandler(entryId, options); };
context.switchSession = (sessionPath, options) => { this.assertActive(); return this.switchSessionHandler(sessionPath, options); };
context.navigateTree = (targetId, options) => { this.assertActive(); return this.navigateTreeHandler(targetId, options); };
context.reload = () => { this.assertActive(); return this.reloadHandler(); };
```

`agent-session-runtime.ts` (`switchSession`@175, `newSession`@200, `fork`@237) each:
`emitBeforeSwitch` → `teardownCurrent` → `apply(createRuntime(...))` → `finishSessionReplacement(withSession)`.
`finishSessionReplacement` (asr.ts:166):
```ts
private async finishSessionReplacement(withSession?: (ctx: ReplacedSessionContext) => Promise<void>): Promise<void> {
    if (this.rebindSession) { await this.rebindSession(this.session); }
    if (withSession) { await withSession(this.session.createReplacedSessionContext()); }
}
```
`ReplacedSessionContext` (types.ts:371) `extends ExtensionCommandContext` (overlays sendMessage/sendUserMessage).

### (b) Current aelix code + file:line — `command_context.py`

- `new_session`@91 and `switch_session`@100 RAISE `ExtensionError("invalid_state", "...deferred...")`.
- `fork`@60 delegates to `repo.fork(source: JsonlSessionMetadata, options: ForkOptions)` — **signature
  diverges from pi `fork(entryId: string, options)`**.
- `navigate_tree`@75 → `harness.navigate_tree`; `reload`@86 → `harness.reload_resources`;
  `wait_for_idle`@55 → `harness.wait_for_idle`.
- Runtime plumbing ALREADY exists: `AgentSessionRuntime.switch_session`@514 / `new_session`@575 /
  `fork`@639 (in protected `runtime/agent_session_runtime.py`).
- `harness.create_replaced_session_context(runtime=...)`@2839 already wires
  `new_session=runtime.new_session`, `fork=runtime.fork`, `switch_session=runtime.switch_session`
  @2945-2957 and returns a `SimpleNamespace` conforming to `ReplacedSessionContext`.
- `ReplacedSessionContext(Protocol)` exists at `runtime/_types.py:85`.
- `ExtensionCommandContext` is **never instantiated** in the CLI (verified grep — no live callers).

### (c) Precise change (command_context.py — unprotected)

- Add an optional `runtime: AgentSessionRuntime | None` to `ExtensionCommandContext.__init__` (store
  via `object.__setattr__(self, "_runtime_session", runtime)` — note: avoid clobbering the existing
  `_runtime` (the `_ExtensionRuntime`); pick a distinct slot name).
- `new_session(options)` → `await self._runtime_session.new_session(...)` (raise a clear error if
  unbound, consistent with the `fork`/`repo is None` pattern — NOT the "deferred" message).
- `switch_session(target, options)` → `await self._runtime_session.switch_session(...)`.
- Align `fork` to pi's signature `fork(entry_id: str, options=...)` delegating to
  `runtime.fork(entry_id, options)` when `_runtime_session` present; keep the `repo.fork` path as the
  unattached fallback. **Flag the fork-signature realignment** (current
  `fork(source: JsonlSessionMetadata, options: ForkOptions)` → pi `fork(entryId, options)`).
- `navigate_tree` already aligns (harness delegate) — leave as-is.
- **ReplacedSessionContext threading:** `options.with_session` (callback taking a
  `ReplacedSessionContext`) flows into `AgentSessionRuntime.new_session/switch_session/fork`, which
  already produce the handle via `finishSessionReplacement` → `create_replaced_session_context`.
  Verify `AgentSessionRuntime.*` signatures accept `with_session`/options (pi options:
  newSession `{parentSession?, setup?, withSession?}`; switch `{withSession?}`;
  fork `{position?, withSession?}`).

### (d) PROTECTED?
Mostly NO — `command_context.py` is in `aelix-coding-agent`. The delegated `AgentSessionRuntime`
methods are in protected core but **already exist**. Only protected IF those signatures lack the
`with_session`/options shape and must be extended (verify first; if extension needed, that's a
protected diff).

### (e) Test plan
- `ExtensionCommandContext(runtime=fake_runtime)`; call `new_session()` / `switch_session(path)` →
  assert delegates to `runtime.new_session/switch_session` (spy).
- `runtime=None` → assert a clear non-"deferred" error.
- If `with_session` supported: assert the callback receives a `ReplacedSessionContext`-conforming
  object.
- One end-to-end test wiring a real `AgentSessionRuntime` (no live caller exists today → low blast
  radius but also no integration coverage).

### (f) Risks
LOW-MEDIUM. (1) No live caller → low blast radius, but also no coverage; add an integration test.
(2) The `fork` signature realignment is a public-API change to `ExtensionCommandContext` — grep shows
no live caller, so safe, but confirm no test relies on the old shape. (3) Must verify
`AgentSessionRuntime.*` signatures match pi options before delegating.

---

## ITEM 5 — `message_end` replacement reducer (revert ADR-0018)

### (a) Verbatim pi source

`runner.ts:714-754` (`emitMessageEnd`):
```ts
async emitMessageEnd(event: MessageEndEvent): Promise<AgentMessage | undefined> {
    const ctx = this.createContext();
    let currentMessage = event.message;
    let modified = false;
    for (const ext of this.extensions) {
        const handlers = ext.handlers.get("message_end");
        if (!handlers || handlers.length === 0) continue;
        for (const handler of handlers) {
            try {
                const currentEvent: MessageEndEvent = { ...event, message: currentMessage };
                const handlerResult = (await handler(currentEvent, ctx)) as MessageEndEventResult | undefined;
                if (!handlerResult?.message) continue;
                if (handlerResult.message.role !== currentMessage.role) {
                    this.emitError({
                        extensionPath: ext.path,
                        event: "message_end",
                        error: "message_end handlers must return a message with the same role",
                    });
                    continue;
                }
                currentMessage = handlerResult.message;
                modified = true;
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                const stack = err instanceof Error ? err.stack : undefined;
                this.emitError({ extensionPath: ext.path, event: "message_end", error: message, stack });
            }
        }
    }
    return modified ? currentMessage : undefined;
}
```

Consumption — `agent-session.ts:669-678`:
```ts
} else if (event.type === "message_end") {
    const extensionEvent: MessageEndEvent = { type: "message_end", message: event.message };
    const replacement = await this._extensionRunner.emitMessageEnd(extensionEvent);
    if (replacement) {
        this._replaceMessageInPlace(event.message, replacement);
    }
}
```

`_replaceMessageInPlace` — `agent-session.ts:618-632`:
```ts
private _replaceMessageInPlace(target: AgentMessage, replacement: AgentMessage): void {
    // Agent-core stores the finalized message object in its state before emitting message_end.
    // SessionManager persistence happens later in _processAgentEvent() with event.message.
    // Mutating this object in place keeps agent state, later turn/agent events, listeners,
    // and the eventual SessionManager.appendMessage(event.message) persistence in sync.
    if (target === replacement) { return; }
    const targetRecord = target as unknown as Record<string, unknown>;
    for (const key of Object.keys(targetRecord)) { delete targetRecord[key]; }
    Object.assign(targetRecord, replacement);
}
```

Types — `types.ts:676` `MessageEndEvent { type: "message_end"; message: AgentMessage }`;
`types.ts:1004` `MessageEndEventResult { message?: AgentMessage }` (jsdoc: "Replace the finalized
message. The replacement must keep the original message role.").

### (b) Current aelix code + file:line

- `harness/hooks.py`: `_REDUCERS["message_end"] = _reducer_observational` (@1703);
  `HOOK_RESULT_TYPES["message_end"] = None` (@1168); `MessageEndHookEvent` class @424
  (docstring: "Observational only in Phase 1.2 — replacement reducer is deferred");
  `MessageEndHandler` type @1758. No `MessageEndEventResult` result class.
- `loop.py`: `MessageEndEvent` emitted @89, 151, 304, 316, 801 via `await emit(...)` where
  `AgentEventSink = Callable[[AgentEvent], Awaitable[None]]` (@64) — **returns None, fire-and-forget.**
  At @300-304 the stream consumer does `context.messages[partial_index] = final` then
  `await emit(MessageEndEvent(message=final))`.
- `harness/core.py`: the `emit` closure @3616: on `message_end` it (1) `session.append_message(event.message)`@3624,
  (2) fans to listeners@3636, (3) projects to HookBus via `_to_hook_event(event)`@3655 (where extension
  handlers actually run). **Return values are discarded.** Note `context.messages` passed to the loop is
  `list(turn_messages)` (@3612 — a per-turn COPY, NOT `_state.messages` by reference).
- Pin tests: `tests/test_message_end_remains_observational.py` asserts
  `HOOK_RESULT_TYPES["message_end"] is None` and `_REDUCERS["message_end"] is _reducer_observational`
  — **these will fail and must be updated.**
- ADRs: `docs/decisions/0013-message-end-observational-in-1-2.md` (Accepted, superseded),
  `docs/decisions/0018-message-end-replacement-reducer.md` (Deprecated — its "Why rejected" rests on
  the layer mix-up). Both need superseding by a new ADR.

### (c) Precise change

1. `harness/hooks.py` (**PROTECTED**): add `MessageEndEventResult` dataclass
   (`message: AgentMessage | None = None`); set `HOOK_RESULT_TYPES["message_end"] = MessageEndEventResult`;
   replace `_REDUCERS["message_end"]` with a new `_reducer_message_end` that walks handler results
   sequentially, applies role-preservation (assistant→assistant / user→user; mismatch → emit error +
   skip), and returns the final (possibly replaced) message or a "no replacement" sentinel (mirror pi's
   `modified` flag — return None/unchanged when no handler replaced). Update `MessageEndHandler` return
   type + the `on("message_end", ...)` overload result type.
2. `harness/core.py` (**PROTECTED**): in the `emit` closure @3616, for `message_end`, capture the
   HookBus emit RETURN value (the reduced message); if it differs from `event.message`, write the
   replacement back into `_state.messages` (by identity-search/replace, since AgentMessage is a frozen
   dataclass — pi's in-place key-mutation does not translate) AND re-persist. The two faithful ordering
   options:
   - **Option A (pi-faithful order):** run the message_end reduction FIRST, then persist the replaced
     message, then re-emit observationally. Requires the closure to update `_state.messages` itself
     (the closure does not receive `partial_index`).
   - **Option B (loop-level):** change `AgentEventSink` to return `AgentMessage | None` so `loop.py`
     (where `partial_index` is in scope @300-316) applies the replacement. Signature change to the
     loop↔harness contract — ripples to every emit call site.
   - **Recommended:** Option A-variant — closure runs the hook reduction; if replaced, finds the object
     in `_state.messages` that `is event.message` and swaps it, then persists the replacement. **The
     critical unknown is whether the loop's per-turn `context.messages` copy (@3612) means the replaced
     message must be written into `_state.messages` AND the session for the next turn to see it.**
3. Update `tests/test_message_end_remains_observational.py` to assert the NEW result type/reducer.
4. New ADR superseding 0013/0018, documenting the layer-mix-up correction.

### (d) PROTECTED?
**YES — heavily.** `harness/hooks.py` (reducer + result type) AND `harness/core.py` (emit-closure
consumption) are both in `aelix-agent-core`. Possibly `loop.py` too if Option B. Highest-risk,
most-protected item. Requires explicit user approval AND a pre-coding spike (see risks).

### (e) Test plan
- `test_message_end_replacement_preserves_role`: handler returns
  `MessageEndEventResult(message=<same-role replacement>)` → `_state.messages` and the persisted
  session reflect the replacement.
- `test_message_end_role_mismatch_skips`: different-role message → original kept + error emitted.
- `test_message_end_sequential_chain`: two handlers, second sees the first's replacement.
- `test_message_end_no_replacement_unchanged`: handler returns `None` → message untouched.
- Persistence test: the replaced message is what's written to the JSONL session.
- Update the two pin tests to the new result type/reducer.

### (f) Risks
**HIGH.** (1) Per-turn `context.messages` is a COPY of `_state.messages` (core.py:3612) — the
replacement must write back to `_state.messages` + the session, not just the turn-local list.
(2) Persistence ordering: aelix appends on message_end BEFORE the hook fan-out (@3624) — to persist the
REPLACED message, reduction must run before append, reordering the closure (risk of double-append).
(3) Frozen-dataclass `AgentMessage` → no in-place mutation; identity-based list replacement needed.
(4) Pin tests + 2 ADRs to revise. (5) Every message_end emit site (loop.py:89/151/304/316/801) flows
through the same closure — tool-result and prompt message_end also become replaceable; confirm pi
allows replacement on ALL message_end emits (it does — `emitMessageEnd` runs for every message_end in
`_processAgentEvent`).
**Pre-coding spike (do BEFORE implementation):** confirm whether `context.messages` writes back to
`_state.messages`, and whether `AgentMessage` is frozen.

---

## ITEM 6 — `--api-key` harness-auth wiring

### (a) Verbatim pi source — `main.ts:574-582`

```ts
if (parsed.apiKey) {
    if (!sessionOptions.model) {
        diagnostics.push({
            type: "error",
            message: "--api-key requires a model to be specified via --model, --provider/--model, or --models",
        });
    } else {
        authStorage.setRuntimeApiKey(sessionOptions.model.provider, parsed.apiKey);
    }
}
```
`authStorage` created @main.ts:521 (`AuthStorage.create()`), passed into `services` (@531). The session
runtime resolves keys via `modelRegistry.getApiKeyAndHeaders` (reads the AuthStorage cascade — runtime
override first).

### (b) Current aelix code + file:line

- `entry.py:490-495`: `--api-key` only prints a "not yet wired" warning.
- `entry.py:396-404`: `AuthStorage` + `ModelRegistry` are constructed ONLY on the `--list-models`
  branch (`auth_storage = AuthStorage(Path(get_agent_dir())/"auth.json"); await auth_storage.load();
  model_registry = ModelRegistry.create(auth_storage)`).
- `_build_harness_options`@265 builds `AgentHarnessOptions` WITHOUT `get_api_key_and_headers` — so on
  the agent-run path it's `None`, and the adapter falls back to `get_env_api_key(provider)`
  (`openai_completions.py:1363-1369`; `_env_api_keys.py` ENV_API_KEYS map).
- Harness DOES consume `get_api_key_and_headers` (`_make_stream_fn`@3447-3472; when None, `api_key=None`
  → adapter env fallback).
- `ModelRegistry.get_api_key_and_headers`@model_registry.py:265 EXISTS (reads
  `AuthStorage.get_api_key_cascade(include_fallback=False)` → models.json apiKey indirection).
- `AuthStorage.set_runtime_api_key`@auth_storage.py:401 EXISTS; `get_api_key_cascade`@566 checks the
  runtime override FIRST (docstring @577: "1. Runtime override (set_runtime_api_key)").
- `resolve_model` (`runtime_bootstrap.py:66`) returns `Model(id=..., provider=provider_flag or "")` for
  the bare-flags path — provider may be `""` if `--model foo` given without `--provider`.

### (c) Precise change (entry.py + runtime_bootstrap.py — all unprotected)

1. In `_async_main` (BEFORE the harness factory, once), construct
   `AuthStorage(Path(get_agent_dir())/"auth.json")` + `await load()` + `ModelRegistry.create(auth_storage)`
   (reuse the exact `--list-models` pattern @402-404). Build once; thread into
   `_build_harness_options`/`_harness_factory` (avoid reconstructing per harness rebuild).
2. If `parsed.api_key is not None`: enforce pi's "requires a model" rule — resolve the model; if
   `model.provider` is empty/unknown, emit the pi-verbatim error diagnostic and `return 1`; else
   `auth_storage.set_runtime_api_key(model.provider, parsed.api_key)`.
3. Set `options.get_api_key_and_headers = model_registry.get_api_key_and_headers` on
   `AgentHarnessOptions` (the harness already consumes it).
4. Remove the `--api-key` deferral warning block @490-495.

**Compatibility note:** Once `get_api_key_and_headers` is populated, resolution SWITCHES from
"adapter reads env directly via get_env_api_key" to "registry cascade". Two safe designs:
- **(i)** Only set `get_api_key_and_headers` when `--api-key` is present (preserves the env path
  otherwise). Simplest, lowest-risk. RECOMMENDED for the first cut.
- **(ii)** Set it always, but first confirm `get_api_key_cascade` falls back to `ENV_API_KEYS` so
  existing env-based runs (e.g. `OPENROUTER_API_KEY`) keep working. More pi-faithful IF the cascade
  reads env (read auth_storage.py:566-620 during impl to confirm).

### (d) PROTECTED?
**NO.** All changes in `aelix-coding-agent` (`entry.py`, `runtime_bootstrap.py`). The harness
consumption (`_make_stream_fn`) already exists in core and is untouched.

### (e) Test plan
- `--api-key sk-xxx --model openrouter/...` → `set_runtime_api_key("openrouter","sk-xxx")` called;
  `get_api_key_and_headers(model)` returns `{apiKey: "sk-xxx"}`; stream sees it.
- `--api-key` with no resolvable provider → pi-verbatim error + exit 1.
- No `--api-key`, only `OPENROUTER_API_KEY` env → still authenticates (regression guard).
- `--api-key` overrides the env var (runtime override wins in cascade).

### (f) Risks
MEDIUM — the env-vs-registry resolution switch (above; mitigate with design (i)). AuthStorage
construction does `auth.json` file I/O (currently deferred off fast paths) — building on every agent
run adds ~10ms + a read (acceptable; pi does the same). Confirm `model.provider` is populated for the
bare-Model path (`resolve_model` may return `provider=""` → mirror pi's guard requiring a model with a
provider).

---

## RECOMMENDED IMPLEMENTATION DECOMPOSITION

**Conflict-prone shared files:** `extensions/api.py` (items 1, 2, 3), `harness/core.py` (items 3, 5 —
PROTECTED), `harness/hooks.py` (item 5 — PROTECTED), `cli/entry.py` (item 6).

**Commit / sprint grouping (recommended order):**

1. **Commit A — "extension API hardening" (items 1 + 2), unprotected, low-risk.** Both touch only
   `extensions/api.py` register*/flag methods; item 2's `assert_active` line and item 1's flag_values
   changes land in the same methods. Do first — zero protected surface, fast review.

2. **Commit B — item 6 (`--api-key`), unprotected.** Independent of A (different files). Can run in
   PARALLEL with A. Self-contained; closes a P0 #5 carry-forward.

3. **Commit C — item 4 (`new_session`/`switch_session`), mostly unprotected.** Touches
   `command_context.py`; depends on VERIFYING (not changing) `AgentSessionRuntime` signatures.
   Independent of A/B. Can run in parallel. Sequence after confirming whether `AgentSessionRuntime`
   needs `with_session` extension (if yes, it pulls in a protected diff → group with protected work).

4. **Commit D — item 3 (`register_tool` refresh), SPLIT protected/unprotected.** The api.py
   action-field + `register_tool` call belong with the harness binding + `_refresh_extension_tools`
   method (protected core.py). Must be ONE commit (the action is meaningless without its binding).
   Touches core.py — conflicts with item 5. Do D before E (smaller, more localized).

5. **Commit E — item 5 (`message_end` reducer), HEAVILY PROTECTED, highest risk.** Touches
   `harness/hooks.py` + `harness/core.py` + pin tests + supersedes ADR-0013/0018. Do LAST, alone, with
   the most review. Requires explicit user approval AND resolution of the per-turn-copy /
   frozen-dataclass / persistence-ordering spike BEFORE coding. Conflicts with D in core.py — land D
   first, rebase E.

**Wave plan:** **Wave 1 = A + B + C (parallel, disjoint files)**, **Wave 2 = D**, **Wave 3 = E**.

---

## Gap-inventory claims found IMPRECISE / WRONG (corrections)

1. **"register_tool never refreshes tools"** — TRUE, but the fix is NOT purely in api.py; it needs a
   new runtime action + a PROTECTED harness binding (`_refresh_extension_tools` + `bind_core` line).
   The inventory implies an api.py-only fix.

2. **"get_flag returns flag.default ignoring runtime.flag_values"** — TRUE, but the complete fix also
   requires SEEDING `flag_values` at `register_flag` time (pi loader.ts:251-253). The inventory only
   flags the read side; the write-side seed is equally required or `get_flag` returns `None` for every
   defaulted flag.

3. **assert_active "8 registration methods" framing** — WRONG/inverted. Pi guards EVERY ExtensionApi
   method (register* AND actions) via `runtime.assertActive()`; the lazy-getter set the prompt lists
   (ui/cwd/newSession/…) is a SEPARATE mechanism (createContext getters) that aelix ALREADY mirrors via
   `__getattribute__`. Aelix's actual gap: its 7 register*/get_flag methods call assert_active ZERO
   times (not "guards 8"). Action methods already guard.

4. **ADR-0018 "pi has no message_end reducer at 734e08e"** — WRONG (layer mix-up). The reducer is
   absent from `agent-harness.ts` but PRESENT in the extension-runner layer (`runner.ts:714
   emitMessageEnd` + `agent-session.ts:675` consumption + `_replaceMessageInPlace`). The aelix
   deprecation conflated the harness-event layer with the extension-runner layer. Reverting is
   pi-faithful.

5. **"--api-key ... agent-run path constructs no AuthStorage" / "harness provider-auth is deferred"** —
   IMPRECISE. The harness `get_api_key_and_headers` consumption ALREADY EXISTS (core.py:3447),
   `AuthStorage.set_runtime_api_key` EXISTS (auth_storage.py:401), and
   `ModelRegistry.get_api_key_and_headers` EXISTS (model_registry.py:265). NOTHING in protected core
   needs adding — only `_build_harness_options` must populate the existing option. The "deferred"
   framing overstates the work.

6. **Prompt's "ModelRegistry.get_api_key_and_headers already exists (model_registry.py:265) and just
   needs threading"** — CONFIRMED correct. It threads via the EXISTING
   `AgentHarnessOptions.get_api_key_and_headers` field (core.py:240), not a new field. One caveat
   (the env-vs-registry resolution switch) must be handled so env-based auth doesn't regress.

7. **`ExtensionCommandContext` "delegate to the existing create_replaced_session_context path"** — the
   delegation target for `new_session`/`switch_session` is `AgentSessionRuntime.new_session/switch_session`
   (which `create_replaced_session_context` ALSO wires). `create_replaced_session_context` is the
   WITH-SESSION handle FACTORY, not the session-switch entry point — the command_context should call
   `AgentSessionRuntime` directly (the runtime internally calls `create_replaced_session_context` via
   `finishSessionReplacement`). Minor framing precision.

---

## Files touched (all absolute)

- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` (items 1,2,3 — unprotected)
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/extensions/command_context.py` (item 4 — unprotected)
- `/workspaces/aelix-ai/packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` + `.../cli/runtime_bootstrap.py` (item 6 — unprotected)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (items 3,5 — **PROTECTED**)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/harness/hooks.py` (item 5 — **PROTECTED**)
- `/workspaces/aelix-ai/packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py` (item 4 — read-only verify; **PROTECTED** if signatures need extension)
- `/workspaces/aelix-ai/packages/aelix-ai/src/aelix_ai/oauth/auth_storage.py` (item 6 — read-only verify cascade reads env)
- `/workspaces/aelix-ai/tests/test_message_end_remains_observational.py` (item 5 — update)
- `/workspaces/aelix-ai/docs/decisions/0013-message-end-observational-in-1-2.md`, `0018-message-end-replacement-reducer.md` (item 5 — supersede with new ADR)
