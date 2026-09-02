# Sprint 6h₄a · Phase 4.11 — Session Navigation (get_fork_messages + get_last_assistant_text) (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-20
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint wires **2 RPC commands** in `rpc_mode.py`: `get_fork_messages` + `get_last_assistant_text`. After Sprint 6h₄a: SUPPORTED 24→26, DEFERRED 5→3. 3 remaining session-tree commands (`switch_session`/`fork`/`clone`) defer to Sprint 6h₄b — they require porting Pi `AgentSessionRuntime` + `SessionManager.getLeafId()` + `rebindSession()` seam (P-126 / ADR-0074 carry-forward).

---

## §0 — W0 INVESTIGATION FINDINGS

### P-293 — Pi handler line drift (ADR-0074 estimates vs verified pin)

ADR-0074 cited `rpc-mode.ts:563-566` / `:568-571`. W0 verification against pinned SHA `734e08e` puts them at **`591-594`** (`get_fork_messages`) and **`596-599`** (`get_last_assistant_text`):

```ts
// rpc-mode.ts:591-594
case "get_fork_messages": {
    const messages = session.getUserMessagesForForking();
    return success(id, "get_fork_messages", { messages });
}

// rpc-mode.ts:596-599
case "get_last_assistant_text": {
    const text = session.getLastAssistantText();
    return success(id, "get_last_assistant_text", { text });
}
```

**Decision:** Closure pin locks the VERIFIED lines. ADR-0074 estimates superseded — fix forward in atomic commit 5 via ADR-0074 line-citation amend.

### P-294 — Sync Pi vs async Aelix for entry enumeration

Pi `getUserMessagesForForking` reads `this.sessionManager.getEntries()` sync (`agent-session.ts:2871`). Aelix `Session.get_entries()` is **async** (`session/session.py:109`).

**Decision:** `harness.get_user_messages_for_forking()` MUST be `async def`. Internal async-boundary leak; no wire-shape impact.

### P-295 — Pi inline anonymous return shape `Array<{entryId, text}>`

Pi declares return type inline at `agent-session.ts:2870`. No named TS interface.

**Decision:** Aelix introduces `ForkPointInfo` frozen dataclass at `aelix_agent_core/harness/_fork_point.py` for type clarity. Wire serializer emits Pi-camelCase `[{"entryId": ..., "text": ...}]`.

### P-296 — `_extractUserMessageText` content variant

Pi accepts `string | Array<{type, text?}>` (`agent-session.ts:2887`). Aelix `UserMessage.content` is always `list[TextContent | ImageContent]`.

**Decision:** Aelix `_extract_user_message_text` walks the list only; string branch kept as defensive (unreachable today).

### P-297 — `getLastAssistantText` aborted-empty filter

Pi reverse-walks `this.messages`, skips assistant messages where `stopReason === "aborted" && content.length === 0` (`agent-session.ts:3063-3070`).

**Decision:** Aelix mirrors verbatim using `self._state.messages` + `reversed()`.

### P-298 — Pi key-omission parity (SYNTHESIS, see §I Consensus Addendum)

Pi `success(id, "get_last_assistant_text", { text })` with `text === undefined` → `JSON.stringify` omits `text` key → `{"data": {}}` on wire. Existing `_session_stats_to_dict` (`rpc_mode.py:1069-1080`) ALREADY uses undefined-skip pattern for `sessionFile` / `contextUsage` — Sprint 6h₃ closure pin asserts this.

**Decision:** Mirror Pi key-omission. `_handle_get_last_assistant_text` builds `data = {"text": text} if text is not None else {}` — preserves Pi bit-for-bit and matches Aelix's own existing undefined-skip pattern. Closure pin asserts empty dict when None.

---

## §A — Scope (binding) — LOC table

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_agent_core/harness/_fork_point.py` (NEW — `ForkPointInfo`) | ~25 | ~30 |
| `aelix_agent_core/harness/core.py` AMEND — 2 methods + helper | ~100 | ~110 |
| `aelix_coding_agent/rpc/rpc_mode.py` AMEND — 2 handlers + serializer + dispatch | ~90 | ~100 |
| Closure pin `test_phase_4_11_strict_superset.py` (NEW) | — | ~120 |
| Cascade updates 4.4/4.6/4.8/4.9/4.10 | — | ~20 |
| **Totals** | **~215** | **~380** |

### NOT in scope (deferred to Sprint 6h₄b)

- 3 session-tree commands (`switch_session`, `fork`, `clone`)
- Pi `AgentSessionRuntime` port
- Pi `SessionManager.getLeafId()`
- Pi `rebindSession()` seam (P-126)
- Pi HTML visual fidelity / `_get_context_usage_safe` real impl (Sprint 6h₅+)

---

## §B — `aelix_agent_core/harness/_fork_point.py` (NEW)

```python
"""Pi parity: anonymous inline return shape from
``agent-session.ts:2870`` — ``Array<{entryId, text}>``.

Sprint 6h₄a (ADR-0075, P-295) names the Pi-anonymous shape as a frozen
dataclass for type clarity. The wire serializer in :mod:`rpc.rpc_mode`
emits Pi-camelCase keys (``entryId`` / ``text``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForkPointInfo:
    """One user-message fork point — Pi parity for inline `{entryId, text}`."""

    entry_id: str
    text: str


__all__ = ["ForkPointInfo"]
```

---

## §C — `aelix_agent_core/harness/core.py` AMEND

### C.1 `get_user_messages_for_forking` (async)

```python
async def get_user_messages_for_forking(self) -> list[ForkPointInfo]:
    """Pi parity: ``session.getUserMessagesForForking()``
    (``agent-session.ts:2870-2885``).

    Sprint 6h₄a (ADR-0075, P-294) — Aelix ``Session.get_entries()`` is
    async so this method must be async. Pi behavior verbatim.
    """

    from aelix_agent_core.harness._fork_point import ForkPointInfo
    from aelix_agent_core.session.entries import MessageEntry

    if self._session is None:
        return []
    entries = await self._session.get_entries()
    result: list[ForkPointInfo] = []
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        msg = entry.message
        if getattr(msg, "role", None) != "user":
            continue
        text = self._extract_user_message_text(getattr(msg, "content", []))
        if text:
            result.append(ForkPointInfo(entry_id=entry.id, text=text))
    return result
```

### C.2 `get_last_assistant_text` (sync)

```python
def get_last_assistant_text(self) -> str | None:
    """Pi parity: ``session.getLastAssistantText()``
    (``agent-session.ts:3059-3081``).

    Sprint 6h₄a (ADR-0075, P-297/P-298). Reverse-walk, aborted-empty
    skip, TextContent concat, trim-empty → None.
    """

    from aelix_ai.messages import AssistantMessage, TextContent

    last_assistant: AssistantMessage | None = None
    for msg in reversed(self._state.messages):
        if not isinstance(msg, AssistantMessage):
            continue
        if msg.stop_reason == "aborted" and len(msg.content) == 0:
            continue
        last_assistant = msg
        break
    if last_assistant is None:
        return None
    text = "".join(
        block.text for block in last_assistant.content
        if isinstance(block, TextContent)
    )
    trimmed = text.strip()
    return trimmed if trimmed else None
```

### C.3 `_extract_user_message_text` (private)

```python
def _extract_user_message_text(self, content: Any) -> str:
    """Pi parity: ``_extractUserMessageText``
    (``agent-session.ts:2887-2896``).

    Sprint 6h₄a (ADR-0075, P-296). Pi accepts string-or-array. Aelix
    list-only path; string branch defensive.
    """

    from aelix_ai.messages import TextContent

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.text for block in content if isinstance(block, TextContent)
    )
```

---

## §D — `aelix_coding_agent/rpc/rpc_mode.py` AMEND

### D.1 Drop 2 from DEFERRED_COMMANDS (5→3)

```python
DEFERRED_COMMANDS: dict[str, str] = {
    "switch_session": "ADR-0076 — Sprint 6h₄b session tree runtime",
    "fork": "ADR-0076 — Sprint 6h₄b session tree runtime",
    "clone": "ADR-0076 — Sprint 6h₄b session tree runtime",
}
```

### D.2 Add 2 to SUPPORTED_COMMANDS (24→26)

Update module docstring count comment to `26 supported / 3 deferred / 29 total`.

### D.3 Two new handlers — Pi key-omission parity (P-298 SYNTHESIS)

```python
async def _handle_get_fork_messages(
    harness: AgentHarness,
    cmd: Any,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:591-594``."""

    points = await harness.get_user_messages_for_forking()
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_fork_messages",
        data={"messages": _fork_points_to_dict(points)},
    )


async def _handle_get_last_assistant_text(
    harness: AgentHarness,
    cmd: Any,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:596-599`` + key-omission parity (P-298)."""

    text = harness.get_last_assistant_text()
    data: dict[str, Any] = {"text": text} if text is not None else {}
    return RpcSuccessResponse(
        id=cmd.id,
        command="get_last_assistant_text",
        data=data,
    )
```

### D.4 Wire serializer

```python
def _fork_points_to_dict(points: list[ForkPointInfo]) -> list[dict[str, str]]:
    """Pi parity: serialize to Pi-camelCase wire dicts.

    Sprint 6h₄a (ADR-0075, P-295).
    """

    return [{"entryId": p.entry_id, "text": p.text} for p in points]
```

### D.5 Dispatch table

Add to `_SUPPORTED_HANDLERS_HARNESS_ONLY`:
```python
"get_fork_messages": _handle_get_fork_messages,
"get_last_assistant_text": _handle_get_last_assistant_text,
```

---

## §E — Tests (binding plan)

### E.1 `tests/harness/test_get_user_messages_for_forking.py` (~60 LOC)
- session None → `[]`
- empty entries → `[]`
- single user MessageEntry → `[ForkPointInfo(entry_id, text)]`
- mixed roles (user + assistant + toolResult) → only user kept
- mixed entry types (MessageEntry / LabelEntry / SessionInfoEntry) → only MessageEntry of user role kept
- empty user text → entry SKIPPED (Pi parity)
- multi-block content `[TextContent("a"), ImageContent, TextContent("b")]` → `text == "ab"`
- entries iteration order preserved

### E.2 `tests/harness/test_get_last_assistant_text.py` (~70 LOC)
- no messages → `None`
- only user messages → `None`
- single assistant `[TextContent("hi")]` → `"hi"`
- mixed blocks `[TextContent("a"), ThinkingContent("..."), TextContent("b")]` → `"ab"`
- aborted-empty assistant SKIPPED, fallback to earlier
- aborted-with-content NOT skipped → returned
- multiple assistants → last emittable
- whitespace-only `"   \n  "` → `None`
- `"  hi  "` → `"hi"` (trimmed)
- only ToolCallContent (no TextContent) → `None`

### E.3 `tests/harness/test_extract_user_message_text.py` (~30 LOC)
- empty list → `""`
- `[TextContent("a"), TextContent("b")]` → `"ab"`
- with ImageContent interleaved → only text concatenated
- all ImageContent → `""`
- string input (Pi defense) → returned as-is
- non-list, non-string → `""`

### E.4 `tests/rpc/test_rpc_mode_get_fork_messages.py` (~50 LOC)
- empty harness → `response.data == {"messages": []}`
- with points → `{"messages": [{"entryId": "...", "text": "..."}]}`
- each dict has EXACTLY 2 keys (`entryId`, `text`)
- `response.command == "get_fork_messages"`
- dispatch table integration

### E.5 `tests/rpc/test_rpc_mode_get_last_assistant_text.py` (~50 LOC)
- with text: `response.data == {"text": "hello"}`
- without text: **`response.data == {}`** (P-298 SYNTHESIS — Pi key-omission parity)
- `response.command == "get_last_assistant_text"`
- dispatch table integration

### E.6 `tests/pi_parity/test_phase_4_11_strict_superset.py` (NEW, ~120 LOC)
- counts: 26 supported / 3 deferred / 29 total
- `get_fork_messages` / `get_last_assistant_text` in SUPPORTED, not in DEFERRED
- dispatcher routes both to non-deferred handlers
- `set(DEFERRED_COMMANDS) == {"switch_session", "fork", "clone"}`
- every deferred owner cites `"ADR-0076"`
- `ForkPointInfo.__dataclass_fields__.keys() == {"entry_id", "text"}`, frozen=True
- wire serializer camelCase verified
- P-298 lock: empty-text handler returns `response.data == {}`
- fixture `pi_session_navigation_734e08e.json` (NEW): pi_sha, handler line citations (`591-594` / `596-599`), agent_session_lines (`2870-2885` / `2887-2896` / `3059-3081`), drift_record documenting ADR-0074 estimate supersession

### E.7 Cascade updates (~20 LOC)
- `test_phase_4_4_strict_superset.py` — counts 24→26 / 5→3
- `test_phase_4_6_strict_superset.py` — same
- `test_phase_4_8_strict_superset.py` — same
- `test_phase_4_9_strict_superset.py` — same
- `test_phase_4_10_strict_superset.py` — same + narrow remaining-deferred to `{"switch_session", "fork", "clone"}` + ADR allowlist `"ADR-0076"`

---

## §F — ADRs

### F.1 NEW: ADR-0075 — `0075-session-navigation-read-only.md`
- Pi parity port of 2 read-only nav commands
- Document P-293 line drift discovery
- Document P-298 SYNTHESIS (Pi key-omission parity preserved)
- Sprint 6h₄b carry-forward enumerated

### F.2 NEW: ADR-0076 — `0076-phase-4-11-strict-superset-closure.md`
- Closure pin owner
- Carry-forward roster: `switch_session`/`fork`/`clone` + `AgentSessionRuntime` + `SessionManager.getLeafId` + `rebindSession` seam + Pi HTML fidelity + `_get_context_usage_safe` real impl + ImageContent rendering + outputPath default rules

### F.3 AMEND: ADR-0034
Add Sprint 6h₄a row to sprint ledger.

### F.4 AMEND: ADR-0074
Line-citation correction note (P-293 supersession).

### F.5 README
Add 0075 + 0076 rows + Sprint 6h₄a sub-table.

---

## §G — Atomic commit plan (W6, EXACTLY 5)

1. `feat: harness/_fork_point — ForkPointInfo frozen dataclass (P-295)`
2. `feat: harness/core — get_user_messages_for_forking + get_last_assistant_text + _extract_user_message_text (P-294/P-296/P-297/P-298)`
3. `feat: rpc/rpc_mode — 2 handlers + camelCase wire serializer + dispatch + DEFERRED 5→3 (P-293/P-298)`
4. `test: Sprint 6h₄a unit + RPC + closure pin (P-293 line citations verified)`
5. `test+docs: closure pin cascade + ADRs 0075/0076 + ADR-0034 amend + ADR-0074 line-citation amend + README`

---

## §H — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1619 baseline + ~25 new ≥ 1644; 0 fail |
| ruff | clean |
| pyright | 8 baseline (no regression) |
| Closure pins 4.4/4.6/4.8/4.9/4.10/4.11 | counts 26 / 3 / 29 |
| Atomic commits | exactly 5 |
| Pi line citations | `591-594` / `596-599` / `2870-2885` / `2887-2896` / `3059-3081` |
| ForkPointInfo lock | `{"entry_id", "text"}` frozen |
| Wire camelCase | `entryId` / `text` keys |
| P-298 key-omission | `data == {}` when text None |

---

## §I — Consensus Addendum (P-298 SYNTHESIS)

**Antithesis:** Pi parity 1st principle dictates bit-for-bit wire match. Pi `JSON.stringify({text: undefined})` → `{}` (key omitted). Original P-298 draft `{"text": None}` was a measurable drift.

**Synthesis (binding):** Mirror Pi key-omission. `_session_stats_to_dict` (`rpc_mode.py:1069-1080`) ALREADY uses undefined-skip for `sessionFile`/`contextUsage`. Consistency with existing Aelix pattern + Pi parity both point to omitting `text` when None. **Binding decision:** `data = {"text": text} if text is not None else {}`.

**Principle violations:** None remain after synthesis.

---

## §J — Workflow (ADR-0032)
- W0 ✓ DONE
- W1 binding spec (this doc)
- W2 executor opus
- W3 verification
- W4 code-reviewer opus (parallel W5)
- W5 architect Pi parity audit (parallel W4)
- W6 apply must-fixes + 5 commits + ADRs

---

**End of binding spec. Architect READ-ONLY until W6.**
