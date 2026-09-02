# P0 #7 Wave 3 — `message_end` Replacement Reducer (revert ADR-0018) — Design Spec

Pi pin `734e08e`. **HEAVILY PROTECTED** (`aelix-agent-core`): `harness/hooks.py`, `harness/core.py`,
`loop.py`, `types.py`. Supersedes ADR-0013 + ADR-0018. HIGH risk — the spike below is DONE; follow it
exactly. The whole point is the replacement must be REAL (persisted + seen next turn + returned), not a
dangling reducer — same failure class as the thinking no-op (ADR-0135).

## VERIFIED FACT: pi HAS the reducer (ADR-0018 was a layer mix-up)

pi's **extension-runner** layer implements it (aelix mirrors this layer); pi's agent-harness layer does
not (which ADR-0018 mistook for "pi has none").

### Verbatim pi — `runner.ts:714` `emitMessageEnd`
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
        const currentEvent = { ...event, message: currentMessage };
        const handlerResult = (await handler(currentEvent, ctx)) as MessageEndEventResult | undefined;
        if (!handlerResult?.message) continue;
        if (handlerResult.message.role !== currentMessage.role) {
          this.emitError({ extensionPath: ext.path, event: "message_end",
            error: "message_end handlers must return a message with the same role" });
          continue;
        }
        currentMessage = handlerResult.message;   // sequential chain
        modified = true;
      } catch (err) { this.emitError({ extensionPath: ext.path, event: "message_end", error: ... }); }
    }
  }
  return modified ? currentMessage : undefined;     // undefined = no replacement
}
```
### Consumption — `agent-session.ts:669-678`
```ts
} else if (event.type === "message_end") {
  const replacement = await this._extensionRunner.emitMessageEnd({ type: "message_end", message: event.message });
  if (replacement) { this._replaceMessageInPlace(event.message, replacement); }
}
```
`_replaceMessageInPlace` mutates the message object IN PLACE (delete keys + Object.assign) so the agent
state, later events, and the eventual `SessionManager.appendMessage(event.message)` all see the
replacement. Types: `MessageEndEventResult { message?: AgentMessage }` (jsdoc: "Replace the finalized
message. The replacement must keep the original message role.").

## AELIX SPIKE — verified facts (these drive the design)

1. `AgentMessage` (UserMessage/AssistantMessage) is `@dataclass(frozen=True)` → **NO in-place
   mutation**. Use loop-return + identity swap instead of pi's `_replaceMessageInPlace`.
2. The emitted `final` message object is the SAME object the loop appends to `new_messages`
   (loop.py: `_consume_stream` returns `final` @324; `_run_loop` does `new_messages.append(message)`),
   and `core.py:3809 self._state.messages.extend(new_messages)` (BOTH session + no-session paths) plus
   the session-attached path persists via `append_message` in the emit closure. So replacing the object
   the loop returns propagates to `_state.messages`; persisting the replacement covers the session.
3. emit closure (core.py:3680-3729) order for message_end TODAY: **(1) `append_message(event.message)`
   @3686 → (2) listeners @3700 → (3) hook fan-out `await self._hooks.emit(_to_hook_event(event))`
   @3719 (return DISCARDED).** To persist the REPLACEMENT, message_end must REORDER: run the hook
   reduction FIRST, compute the final message, THEN persist it.
4. `HookBus.emit` (hooks.py:2359-2387) RETURNS the reducer result (`result = await reducer(...)` @2387).
5. `AgentEventSink = Callable[[AgentEvent], Awaitable[None]]` (loop.py:64). Only the message_end call
   site needs the return; every other `await emit(...)` caller ignores it (returning None is harmless).
6. loop.py message_end emit sites: `_consume_stream` @304 (done) + @316 (error); also the harness
   hook-fail close-out emits `MessageEndEvent(message=failure)` @core.py:3800 (must tolerate the new
   return — ignore it).
7. Pin tests `tests/test_message_end_remains_observational.py` assert
   `HOOK_RESULT_TYPES["message_end"] is None` and `_REDUCERS["message_end"] is _reducer_observational`
   — these MUST be rewritten to the new behavior.

## DESIGN — loop-return + identity swap (the only frozen-dataclass-safe + persistence-correct option)

### A. `harness/hooks.py` (PROTECTED)
- Add `@dataclass MessageEndEventResult` with `message: AgentMessage | None = None` (pi
  `MessageEndEventResult`).
- `HOOK_RESULT_TYPES["message_end"] = MessageEndEventResult` (was `None`).
- Replace `_REDUCERS["message_end"]` with a new `_reducer_message_end(handlers, event, ctx)`:
  - `current = event.message; modified = False`.
  - For each `(handler, mode)`: invoke with an event carrying `current` (rebuild the event's `message`
    each iteration so the chain sees the prior replacement — pi `{ ...event, message: currentMessage }`).
    `raw = await _safe_invoke(...)`. If `raw is None` or `raw.message is None`: continue. If
    `raw.message.role != current.role`: **log a warning + skip** (aelix equivalent of pi's `emitError`;
    do NOT raise). Else `current = raw.message; modified = True`.
  - Return `current if modified else None` (None = "no replacement", matching pi `undefined`).
- Update `MessageEndHandler` return type to `MessageEndEventResult | None` (+ any `on("message_end", …)`
  typed overload result type at hooks.py:~1965).

### B. `harness/core.py` emit closure (PROTECTED) — change return type to `AgentMessage | None`
- For `message_end`: REORDER. First run the reduction: `reduced = await self._hooks.emit(_to_hook_event(event))`
  (the new reducer returns the replaced `AgentMessage` or `None`). Compute
  `final_message = reduced if (reduced is not None and reduced is not event.message) else event.message`.
  THEN persist: `if session is not None: await session.append_message(final_message)` (the REPLACEMENT,
  inside the same try/except). Then run listeners (with the original `event` — observers stay
  observational; do NOT change listener semantics). Then `return reduced` (the replacement or None) so
  the loop can apply it. Guard against double hook-emit: for message_end the hook fan-out is the
  reduction above — do NOT also run the generic `_to_hook_event` fan-out at @3719 again.
- For every OTHER event type: unchanged (persist N/A, listeners, generic hook fan-out) and `return None`.
- The hook-fail close-out @3798-3805 emits `MessageEndEvent(message=failure)`; it must tolerate the new
  return (ignore it) — a failure message has no extension to replace it, but the path must not crash.

### C. `loop.py` (PROTECTED)
- `AgentEventSink = Callable[[AgentEvent], Awaitable[AgentMessage | None]]`.
- At the two message_end emit sites (`_consume_stream` @304 done, @316 error):
  ```python
  replacement = await emit(MessageEndEvent(message=final))
  if replacement is not None and replacement is not final:
      if partial_index is not None:
          context.messages[partial_index] = replacement
      elif context.messages and context.messages[-1] is final:
          context.messages[-1] = replacement
      final = replacement
  ```
  `return final` (@324) then returns the replacement → `new_messages.append(...)` → `_state.messages`.
  (Apply ONLY at message_end sites; other `await emit(...)` calls keep ignoring the return.)

### D. Tests + ADR
- Rewrite `tests/test_message_end_remains_observational.py` (rename to e.g.
  `test_message_end_replacement_reducer.py`) to assert the NEW behavior:
  `HOOK_RESULT_TYPES["message_end"] is MessageEndEventResult`; `_REDUCERS["message_end"] is
  _reducer_message_end`; and behavioral tests:
  - replacement with same role → `_state.messages` (and, with a session, the PERSISTED message) reflect
    the replacement; the loop return reflects it.
  - role mismatch → original kept, warning logged, no raise.
  - sequential chain: 2 handlers, 2nd sees 1st's replacement.
  - no handler / `None` result → message untouched, emit returns None.
  - persistence: with a Session, the appended/persisted message is the REPLACEMENT, not the original.
- New ADR (next number) **superseding ADR-0013 + ADR-0018**, documenting the layer-mix-up correction +
  the frozen-dataclass loop-return design (vs pi's in-place mutation).

## RISKS / MUST-VERIFY (review lenses)
1. **Persistence correctness**: the SESSION receives the REPLACEMENT (reorder works) — not the original.
2. **No-session path**: `_state.messages` receives the replacement via `new_messages` (loop return).
3. **Observational no-regression**: every OTHER message_end-less event and all other hooks behave
   exactly as before; listeners still see events; no double hook-emit for message_end.
4. **Identity**: `is`-based swap (frozen dataclass) — `reduced is not event.message` guards the no-op.
5. **Error/abort message_end** (loop.py:316, core.py:3800 close-out) tolerate the new return.
6. **AgentEventSink type change** doesn't break other emit callers (they ignore the return).
7. Pin tests rewritten; ADR supersedes 0013/0018.
