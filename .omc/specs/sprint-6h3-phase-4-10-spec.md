# Sprint 6h₃ · Phase 4.10 — Session Inspection (get_session_stats + export_html) (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-20
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint wires **2 RPC commands** in `rpc_mode.py`: `get_session_stats` + `export_html`. After Sprint 6h₃: SUPPORTED 22→24, DEFERRED 7→5. 5 session tree commands (`switch_session`/`fork`/`clone`/`get_fork_messages`/`get_last_assistant_text`) defer to Sprint 6h₄ per spec §J — they require porting Pi `AgentSessionRuntime` + `SessionManager.getLeafId` + `rebindSession` seam (P-126 Sprint 6f carry-forward).

---

## §0 — W0 INVESTIGATION FINDINGS

### P-268 — Pi `SessionStats` shape (10 fields, optional `contextUsage`)

Pi `agent-session.ts:212-223`:
```typescript
export interface SessionStats {
  sessionFile: string | undefined;
  sessionId: string;
  userMessages: number;
  assistantMessages: number;
  toolCalls: number;
  toolResults: number;
  totalMessages: number;
  tokens: { input: number; output: number; cacheRead: number; cacheWrite: number; total: number };
  cost: number;
  contextUsage?: ContextUsage;
}
```

`ContextUsage` already exists in Aelix `extensions/api.py:481` (Sprint 6 — `usedTokens` / `limitTokens` / `remainingTokens`).

**Decision:** Aelix adds `SessionStats` dataclass at `aelix_agent_core/harness/_session_stats.py`. Sub-dict `tokens` = nested dataclass `SessionStatsTokens`. `contextUsage` reused from existing `ContextUsage` dataclass (snake_case mapped: `used_tokens`/`limit_tokens`/`remaining_tokens`).

### P-269 — Pi `get_session_stats` handler reads `session.getSessionStats()` — Pi aggregator counts message types from the in-memory `session.messages` array + sums `usage.cost.total` across assistant messages

W0 fetched Pi handler at `rpc-mode.ts:553-556`:
```typescript
case "get_session_stats": {
    const stats = session.getSessionStats();
    return success(id, "get_session_stats", stats);
}
```

The `session.getSessionStats()` method at `agent-session.ts:2901-2945` does the aggregation. Pi's algorithm (inferred from SessionStats fields):
- Walk `session.messages` (or equivalent)
- Count by role: `userMessages` (user role), `assistantMessages` (assistant role), `toolResults` (toolResult role)
- For assistant messages: sum `content` blocks of `type === "toolCall"` → `toolCalls`; sum `usage.cost.total` → `cost`; accumulate `usage.input/output/cacheRead/cacheWrite` → `tokens`
- `totalMessages = userMessages + assistantMessages + toolResults`
- `contextUsage = this.getContextUsage()` (line 2797 — optional, may be undefined)
- `sessionFile` = path to JSONL or undefined if in-memory
- `sessionId` = session UUID

**Decision:** Aelix `harness.get_session_stats() -> SessionStats` aggregates from existing session machinery (Sprint 4 Session V3 + harness message tracking).

### P-270 — Pi `export_html` handler signature

Pi `rpc-mode.ts:558-561`:
```typescript
case "export_html": {
    const path = await session.exportToHtml(command.outputPath);
    return success(id, "export_html", { path });
}
```

Returns `{ path: string }` — Pi resolves `outputPath` (when given) OR generates a default temp/session-dir-relative path.

Pi's full HTML export under `coding-agent/src/core/export-html/` is a substantial subsystem (CSS + syntax highlighting + responsive layout). **Sprint 6h₃ scope decision:** Ship a **MINIMAL HTML emitter** that satisfies the wire contract:
- Emits a syntactically valid HTML5 document
- Each user/assistant message as `<section>` with role-class + content
- Tool calls + tool results rendered as `<pre>` blocks (raw JSON)
- No CSS frameworks, no syntax highlighting — Pi visual fidelity deferred to Sprint 6h₅+
- Default `outputPath` (when omitted): `<session-dir>/<session-id>.html` or `tempfile.NamedTemporaryFile(suffix=".html", delete=False)`

This is honest — Pi wire shape `{path: str}` is preserved; the file contents are a Pi-shape minimal subset documented in ADR-0073 carry-forward.

### P-271 — Aelix `harness._session` access pattern

Aelix already exposes `harness.get_state()` style introspection (Sprint 6f public properties: `pending_message_count` / `session_file` / `session_name`). Sprint 6h₃ uses the SAME pattern:
- Read `harness._session` for SessionStats aggregation (encapsulated via a new `harness.get_session_stats()` method)
- Read `harness.session_file` (existing Sprint 6f public property) for `sessionFile` field
- Read `harness._session.session_id` (or equivalent — verify in W2) for `sessionId`

### P-272 — `tokens.cacheRead` / `cacheWrite` aggregation

Pi's assistant message carries `usage: { input, output, cacheRead, cacheWrite, totalTokens, cost: { ... } }`. The Aelix `Usage` dataclass (Sprint 6f from `streaming.py`) has matching fields: `input` / `output` / `cache_read` / `cache_write` / `cost: UsageCost`. Aggregation: for each assistant message in session, accumulate `msg.usage.input`/`output`/`cache_read`/`cache_write`. Total = sum of 4 fields.

### P-273 — `contextUsage` may be `None` when model unknown

Pi `getContextUsage()` returns `ContextUsage | undefined` when the current model is unknown or the message history is empty. Aelix mirrors with `SessionStats.context_usage: ContextUsage | None = None`.

### P-274 — `_handle_get_state` already provides some overlap; `get_session_stats` is the inventory deep-dive

Sprint 6f W6 P-118 exposed `session_file` / `session_name` / `pending_message_count` on `harness`. Sprint 6h₃ adds richer aggregation (token totals, cost, per-role message counts). Both coexist.

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_agent_core/harness/_session_stats.py` (NEW — SessionStats + SessionStatsTokens dataclasses + aggregator) | ~150 | ~120 |
| `aelix_agent_core/harness/core.py` AMEND — `get_session_stats()` method | ~60 | ~80 |
| `aelix_coding_agent/_export_html.py` (NEW — minimal HTML emitter) | ~200 | ~80 |
| `aelix_agent_core/harness/core.py` AMEND — `export_to_html(output_path)` method | ~40 | ~60 |
| `rpc/rpc_mode.py` AMEND — 2 handlers + drop from DEFERRED, add to SUPPORTED | ~50 | ~80 |
| `rpc/rpc_types.py` AMEND — `SessionStats` wire serialization (Pi camelCase) | ~30 | — |
| Pi parity closure pin (`test_phase_4_10_strict_superset.py`) | — | ~80 |
| Sprint 6d/6f/6h₁/6h₂ closure pin updates (counts) | — | ~10 |
| **Totals** | **~530** | **~510** |

**Total ~1,040 LOC** — fits Sprint 6c envelope.

### NOT in scope (deferred per §J)

- **5 session tree commands** (`switch_session`, `fork`, `clone`, `get_fork_messages`, `get_last_assistant_text`) — Sprint 6h₄
- **Pi `AgentSessionRuntime` port** — Sprint 6h₄
- **Pi `SessionManager.getLeafId()`** — Sprint 6h₄
- **Pi `rebindSession()` seam** (P-126 carry-forward) — Sprint 6h₄
- **Full Pi HTML visual fidelity** — Sprint 6h₅+ (CSS, syntax highlighting, responsive layout)
- **Image content in HTML export** — Sprint 6h₅
- **Pi exact `outputPath` default resolution rules** — Sprint 6h₅

---

## §B — `aelix_agent_core/harness/_session_stats.py` (NEW)

```python
"""Pi parity: ``agent-session.ts:212-223`` SessionStats + ``:2901-2945`` getSessionStats."""

from __future__ import annotations
from dataclasses import dataclass, field

@dataclass(frozen=True)
class SessionStatsTokens:
    """Pi parity: ``SessionStats.tokens`` sub-shape."""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0

@dataclass(frozen=True)
class SessionStats:
    """Pi parity: ``agent-session.ts:212-223``."""
    session_id: str = ""
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    total_messages: int = 0
    tokens: SessionStatsTokens = field(default_factory=SessionStatsTokens)
    cost: float = 0.0
    session_file: str | None = None
    context_usage: Any | None = None  # ContextUsage | None — avoid import cycle
```

### B.1 Aggregator (pure function)

```python
def aggregate_session_stats(
    session_id: str,
    messages: list[Message],
    session_file: str | None = None,
    context_usage: Any | None = None,
) -> SessionStats:
    """Pi parity: ``agent-session.ts:getSessionStats``.

    Walks ``messages``, accumulates counts/tokens/cost per Pi algorithm.
    """
    user = 0
    assistant = 0
    tool_results = 0
    tool_calls = 0
    tokens_in = 0
    tokens_out = 0
    cache_r = 0
    cache_w = 0
    cost = 0.0
    for msg in messages:
        if isinstance(msg, UserMessage):
            user += 1
        elif isinstance(msg, AssistantMessage):
            assistant += 1
            for block in (msg.content or []):
                if isinstance(block, ToolCallContent):
                    tool_calls += 1
            usage = getattr(msg, "usage", None)
            if usage is not None:
                tokens_in += getattr(usage, "input", 0)
                tokens_out += getattr(usage, "output", 0)
                cache_r += getattr(usage, "cache_read", 0)
                cache_w += getattr(usage, "cache_write", 0)
                msg_cost = getattr(usage, "cost", None)
                if msg_cost is not None:
                    cost += getattr(msg_cost, "total", 0.0)
        elif isinstance(msg, ToolResultMessage):
            tool_results += 1
    tokens_total = tokens_in + tokens_out + cache_r + cache_w
    return SessionStats(
        session_id=session_id,
        user_messages=user,
        assistant_messages=assistant,
        tool_calls=tool_calls,
        tool_results=tool_results,
        total_messages=user + assistant + tool_results,
        tokens=SessionStatsTokens(
            input=tokens_in, output=tokens_out,
            cache_read=cache_r, cache_write=cache_w, total=tokens_total,
        ),
        cost=cost,
        session_file=session_file,
        context_usage=context_usage,
    )
```

---

## §C — `aelix_agent_core/harness/core.py` AMEND — `get_session_stats()`

```python
def get_session_stats(self) -> SessionStats:
    """Pi parity: ``session.getSessionStats()`` (``agent-session.ts:2901-2945``).

    Sprint 6h₃ (ADR-0073, P-269) aggregates per-role message counts,
    token totals, cost, and context_usage from the in-memory session.
    """
    from aelix_agent_core.harness._session_stats import aggregate_session_stats

    session = self._session
    messages = list(session.messages) if session is not None else []
    session_file = self.session_file  # Sprint 6f P-118 public property
    session_id = (
        getattr(session, "session_id", "") or ""
        if session is not None
        else ""
    )
    # contextUsage — defer to Sprint 6h₄ if model registry not wired
    context_usage = self._get_context_usage_safe()  # NEW helper, returns None when unknown
    return aggregate_session_stats(
        session_id=session_id,
        messages=messages,
        session_file=session_file,
        context_usage=context_usage,
    )
```

`_get_context_usage_safe()` reads existing harness model + last assistant message tokens. Default `None` is acceptable for Sprint 6h₃ (Pi parity — Pi's `getContextUsage` also returns undefined when unknown).

---

## §D — `aelix_coding_agent/_export_html.py` (NEW — minimal HTML emitter)

```python
"""Pi parity: ``packages/coding-agent/src/core/export-html/`` minimal port.

Sprint 6h₃ (ADR-0073) ships a syntactically valid HTML5 document with
the Pi wire-shape contract. Visual fidelity (CSS, syntax highlighting,
responsive layout) deferred to Sprint 6h₅+ per ADR-0074 carry-forward.
"""

from __future__ import annotations
import html
import json
import tempfile
from pathlib import Path
from typing import Any
from aelix_ai.messages import (
    AssistantMessage, Message, TextContent, ToolCallContent, ToolResultMessage, UserMessage,
)

_HTML_DOCUMENT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 800px; margin: 2em auto; padding: 1em; }}
section.user {{ background: #f0f4f8; padding: 1em; border-radius: 8px; margin: 1em 0; }}
section.assistant {{ background: #ffffff; border: 1px solid #e0e6ed; padding: 1em; border-radius: 8px; margin: 1em 0; }}
section.tool_result {{ background: #fdf6e3; padding: 1em; border-radius: 8px; margin: 1em 0; }}
pre {{ background: #2d2d2d; color: #ccc; padding: 0.5em; border-radius: 4px; overflow-x: auto; }}
.role {{ font-weight: bold; color: #586e75; margin-bottom: 0.5em; }}
</style>
</head>
<body>
<h1>{title}</h1>
{messages}
</body>
</html>
"""


def export_html(
    messages: list[Message],
    output_path: str | None = None,
    *,
    title: str = "Aelix Session",
) -> str:
    """Pi parity: ``session.exportToHtml(outputPath)``.

    Sprint 6h₃ minimal renderer. Returns the resolved output path.
    """
    body_sections: list[str] = []
    for msg in messages:
        body_sections.append(_render_message(msg))
    body = "\n".join(body_sections)
    doc = _HTML_DOCUMENT_TEMPLATE.format(title=html.escape(title), messages=body)

    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8",
        )
        tmp.write(doc)
        tmp.close()
        return tmp.name
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
    return str(path.resolve())


def _render_message(msg: Message) -> str:
    """Render a single message as HTML section."""
    if isinstance(msg, UserMessage):
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        return f'<section class="user"><div class="role">user</div>{body}</section>'
    if isinstance(msg, AssistantMessage):
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        return f'<section class="assistant"><div class="role">assistant</div>{body}</section>'
    if isinstance(msg, ToolResultMessage):
        # ToolResultMessage carries `content` (list[TextContent | ImageContent])
        body = "\n".join(_render_content_block(b) for b in (msg.content or []))
        tool_id = html.escape(getattr(msg, "tool_call_id", "") or "")
        return (
            f'<section class="tool_result">'
            f'<div class="role">tool_result <code>{tool_id}</code></div>'
            f'{body}</section>'
        )
    return f"<!-- unknown message type: {type(msg).__name__} -->"


def _render_content_block(block: Any) -> str:
    """Render TextContent / ToolCallContent / ImageContent."""
    if isinstance(block, TextContent):
        return f"<p>{html.escape(block.text)}</p>"
    if isinstance(block, ToolCallContent):
        args = html.escape(json.dumps(block.input or {}, indent=2, ensure_ascii=False))
        name = html.escape(block.tool_name or "")
        return f'<pre><code>tool_call: {name}\n{args}</code></pre>'
    # ImageContent or other — best-effort
    type_name = getattr(block, "__class__", type(block)).__name__
    return f"<!-- unrendered block: {type_name} -->"
```

---

## §E — `aelix_agent_core/harness/core.py` AMEND — `export_to_html()`

```python
def export_to_html(self, output_path: str | None = None) -> str:
    """Pi parity: ``session.exportToHtml(outputPath?)``.

    Sprint 6h₃ (ADR-0073, P-270) ships a minimal HTML emitter. Full
    Pi visual fidelity deferred to Sprint 6h₅+ per ADR-0074.
    """
    from aelix_coding_agent._export_html import export_html

    session = self._session
    messages = list(session.messages) if session is not None else []
    title = self._cached_session_name or "Aelix Session"
    return export_html(messages, output_path, title=title)
```

---

## §F — `rpc/rpc_mode.py` AMEND

### F.1 Drop 2 from DEFERRED_COMMANDS

```python
DEFERRED_COMMANDS: dict[str, str] = {
    # Sprint 6h₃ (ADR-0073) drops 2 entries — remaining 5 session-tree
    # commands defer to Sprint 6h₄ (ADR-0074).
    "switch_session": "ADR-0074 — Sprint 6h₄ session tree navigation",
    "fork": "ADR-0074 — Sprint 6h₄ session tree navigation",
    "clone": "ADR-0074 — Sprint 6h₄ session tree navigation",
    "get_fork_messages": "ADR-0074 — Sprint 6h₄ session tree navigation",
    "get_last_assistant_text": "ADR-0074 — Sprint 6h₄ session tree navigation",
}
```

### F.2 Add to SUPPORTED_COMMANDS

```python
SUPPORTED_COMMANDS: frozenset[str] = frozenset(
    {
        # ... existing 22 ...
        "get_session_stats",
        "export_html",
    }
)
```

Module docstring: `22→24 supported / 7→5 deferred`.

### F.3 Two new handlers

```python
async def _handle_get_session_stats(harness, cmd) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:553-556``."""
    stats = harness.get_session_stats()
    return RpcSuccessResponse(
        id=cmd.id, command="get_session_stats",
        data=_session_stats_to_dict(stats),
    )


async def _handle_export_html(harness, cmd) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:558-561``."""
    path = harness.export_to_html(cmd.output_path)
    return RpcSuccessResponse(
        id=cmd.id, command="export_html", data={"path": path},
    )
```

`_session_stats_to_dict(stats)` — Pi camelCase serializer:
```python
def _session_stats_to_dict(stats: SessionStats) -> dict[str, Any]:
    out: dict[str, Any] = {
        "sessionId": stats.session_id,
        "userMessages": stats.user_messages,
        "assistantMessages": stats.assistant_messages,
        "toolCalls": stats.tool_calls,
        "toolResults": stats.tool_results,
        "totalMessages": stats.total_messages,
        "tokens": {
            "input": stats.tokens.input,
            "output": stats.tokens.output,
            "cacheRead": stats.tokens.cache_read,
            "cacheWrite": stats.tokens.cache_write,
            "total": stats.tokens.total,
        },
        "cost": stats.cost,
    }
    if stats.session_file is not None:
        out["sessionFile"] = stats.session_file
    if stats.context_usage is not None:
        # ContextUsage dataclass → camelCase dict
        out["contextUsage"] = {
            "usedTokens": getattr(stats.context_usage, "used_tokens", 0),
            "limitTokens": getattr(stats.context_usage, "limit_tokens", 0),
            "remainingTokens": getattr(stats.context_usage, "remaining_tokens", 0),
        }
    return out
```

### F.4 Wire handlers into dispatch table

Add `get_session_stats` → `_handle_get_session_stats`, `export_html` → `_handle_export_html`.

---

## §G — Tests (binding plan, ~510 LOC)

### Unit
- `tests/harness/test_session_stats.py` (~120 LOC):
  - `aggregate_session_stats` with empty messages → all zeros
  - With 3 user + 2 assistant + 1 tool_result → counts correct
  - Assistant message with 2 toolCall blocks → tool_calls=2
  - Assistant message with usage (input=100, output=50, cache_read=10, cache_write=5, cost.total=0.005) → tokens accumulate, cost summed
  - `tokens.total = input + output + cache_read + cache_write`
  - `totalMessages = userMessages + assistantMessages + toolResults`

- `tests/harness/test_harness_get_session_stats.py` (~80 LOC):
  - `harness.get_session_stats()` returns SessionStats with correct fields from session + harness
  - `session_file` reflects current path (or None)
  - `context_usage` is None when model unknown (Pi parity)

### Integration
- `tests/coding_agent/test_export_html.py` (~80 LOC):
  - `export_html([])` → returns path, file exists, valid HTML5 structure
  - `export_html` with user + assistant + tool_result → sections rendered with correct classes
  - `output_path=None` → tempfile created
  - `output_path="/tmp/foo.html"` → file at that exact path
  - Tool call → `<pre>` block with JSON args (escaped)
  - Empty content → no crash

- `tests/harness/test_harness_export_to_html.py` (~60 LOC):
  - `harness.export_to_html()` returns path; file at default location
  - `harness.export_to_html("/tmp/x.html")` returns "/tmp/x.html"

### RPC handler integration
- `tests/rpc/test_rpc_mode_get_session_stats.py` (~50 LOC):
  - RPC dispatch returns `SessionStats` in Pi camelCase
  - Empty session → all-zero stats
  - With messages → counts match

- `tests/rpc/test_rpc_mode_export_html.py` (~40 LOC):
  - RPC dispatch with `outputPath` → response `{path}` matches
  - Without `outputPath` → tempfile path returned

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_10_strict_superset.py` (~80 LOC):
  - Assert `DEFERRED_COMMANDS` 5 entries; `SUPPORTED_COMMANDS` 24; total 29
  - Assert `SessionStats` 10 fields match Pi
  - Assert `SessionStats` wire-shape camelCase
  - Assert `RpcSessionStats` aggregator algorithm matches Pi
  - Assert `export_html` returns valid HTML5 document
  - Assert response wire shape `{path: str}`

### Closure pin updates
- `tests/pi_parity/test_phase_4_4_strict_superset.py` — counts: DEFERRED 7→5, SUPPORTED 22→24
- `tests/pi_parity/test_phase_4_6_strict_superset.py` — same
- `tests/pi_parity/test_phase_4_8_strict_superset.py` — same
- `tests/pi_parity/test_phase_4_9_strict_superset.py` — same

---

## §H — ADRs

### Amend
- **ADR-0034** — add row: "Sprint 6h₃ wired 2 session inspection RPC commands (get_session_stats + export_html). DEFERRED 7→5, SUPPORTED 22→24. Remaining 5 session-tree commands defer to Sprint 6h₄ per ADR-0074."

### NEW
- **ADR-0073** — `0073-session-stats-and-html-export.md` — Pi parity port of `SessionStats` shape + aggregator + minimal HTML emitter + 2 RPC handlers. Documents minimal-HTML scope decision (Pi visual fidelity → Sprint 6h₅+).
- **ADR-0074** — `0074-phase-4-10-strict-superset-closure.md` — closure pin. Roster: P-268 ~ P-274. Sprint 6h₄ carry-forward enumerated (5 session-tree + AgentSessionRuntime + SessionManager.getLeafId + rebindSession seam + full HTML visual fidelity).

### README
Add 2 new ADR rows + Sprint 6h₃ sub-table.

---

## §I — Sprint workflow (ADR-0032)

- W0 — research ✓ DONE
- W1 — this spec (binding)
- W2 — executor opus implements §B~§F
- W3 — verification
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: harness/_session_stats — SessionStats + SessionStatsTokens + aggregator (ADR-0073, P-268/P-269/P-272)`
2. `feat: coding-agent/_export_html — minimal HTML emitter (ADR-0073, P-270)`
3. `feat: harness/core — get_session_stats + export_to_html methods (ADR-0073, P-271/P-274)`
4. `feat: rpc/rpc_mode — 2 handlers + camelCase wire serializer + dispatch (P-269/P-270)`
5. `test: Sprint 6h₃ — closure pin + Sprint 6d/6f/6h₁/6h₂ count updates + new tests + docs`

---

## §J — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 1550 baseline + ~50 new ≈ 1600+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Sprint 6d/6f/6h₁/6h₂ closure pins | DEFERRED 7→5, SUPPORTED 22→24 |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
