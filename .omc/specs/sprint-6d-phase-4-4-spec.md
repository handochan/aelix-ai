# Sprint 6d · Phase 4.4 — RPC Mode + JSONL Protocol + RpcClient (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-19
Pi pin (ADR-0034): `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

This sprint ports Pi `packages/coding-agent/src/modes/rpc/` (the multi-language-client surface that ADR-0020 deferred from Phase 4): JSONL line protocol + full Pi-parity command/response types + minimal RpcMode dispatcher wired to AgentHarness + RpcClient subprocess wrapper.

---

## §0 — W0 INVESTIGATION FINDINGS (Pi drift verified at SHA 734e08e)

### P-105 — Pi RPC mode lives under `coding-agent/modes/rpc/`, not `agent/`

W0 verified Pi's RPC mode is NOT under `packages/agent/`; it's at `packages/coding-agent/src/modes/rpc/` with 4 files totaling ~1,155 LOC. The `packages/agent/src/proxy.ts` (10 KB) is a DIFFERENT concern (server-routed LLM call proxying), not the JSONL RPC client/server.

**Decision:** Sprint 6d ports into `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/`. The Pi `modes/index.ts` exports `runRpcMode`, `RpcClient`, `RpcCommand`, `RpcResponse`, `RpcSessionState` — Aelix mirrors as `aelix_coding_agent.rpc.run_rpc_mode`, `aelix_coding_agent.rpc.RpcClient`, etc.

### P-106 — JSONL framing is LF-only (Pi explicitly avoids Unicode line separators)

Pi `jsonl.ts:1-12, 14-58`:
- `serializeJsonLine(value)` = `JSON.stringify(value) + "\n"` (LF only)
- `attachJsonlLineReader(stream, onLine)` uses `StringDecoder("utf8")` + manual `\n`-scan; explicitly NOT `readline` because Node readline splits on U+2028/U+2029 which are valid inside JSON strings
- CR stripping: `line.endsWith("\r") ? line.slice(0, -1) : line`
- Trailing buffer on `end` is emitted as final line

**Decision:** Aelix uses incremental UTF-8 codec + `\n`-split + `rstrip("\r")`. Use `codecs.getincrementaldecoder("utf-8")` for chunk-boundary multi-byte safety. Implementation in `aelix_coding_agent/rpc/_jsonl.py`.

### P-107 — Pi `RpcCommand` has 28 variants; Aelix Phase 4 harness can satisfy only 9 of them

Pi `rpc-types.ts:19-69` defines 28 command variants. Aelix's current `AgentHarness` + `aelix-coding-agent` infrastructure can satisfy these directly:

| Pi command | Aelix mapping |
|---|---|
| `prompt` | `harness.prompt(message)` |
| `abort` | `harness.abort()` |
| `new_session` | construct new harness/session |
| `get_state` | inspect harness model + counts + flags |
| `get_messages` | harness session message list |
| `compact` | `harness.compact()` (Sprint 4b) |
| `bash` | builtin bash tool invocation (Sprint 5b) |
| `set_thinking_level` | harness ThinkingLevel setter (Sprint 3b) |
| `set_session_name` | session label write (Sprint 4) |

**The other 19 commands** require Aelix infrastructure that doesn't exist yet (steer/follow_up paths, ModelRegistry, fork/clone/switch_session, extension UI, slash commands, retry hooks, bash cancellation). Sprint 6d ships them as **server-side error-response stubs** per Pi parity — the wire shape is correct, the server says `success: false; error: "command not implemented in Aelix Sprint 6d"`. Future sprints wire each one as the underlying surface lands.

**Decision:** Spec §J enumerates the 19 deferred commands in `RPC_DEFERRED_COMMANDS` allowlist with owning ADR-0058 (Sprint 6d closure ADR). Closure pin asserts the allowlist.

### P-108 — Pi `RpcResponse` envelope shape is uniform; error path is a separate union member

Pi `rpc-types.ts:204-205`:
```typescript
| { id?: string; type: "response"; command: string; success: false; error: string }
```

Every command can fail; the failure shape is uniform. Aelix mirrors with a `RpcErrorResponse` dataclass; the success shape is per-command. The `id` echo is critical for the client's `pending_requests` map.

### P-109 — RpcMode is a fire-and-forget event subscriber, NOT a request-response RPC

Pi `rpc-mode.ts:86-87`:
```typescript
unsubscribe = session.subscribe((event) => { output(event); });
```

Session events (from `session.subscribe`) flow out via `output(event)` WITHOUT transformation. This means RpcMode is bidirectional but asymmetric:
- stdin: commands (request-response with id matching)
- stdout: command responses + session events (events are pushed, not requested)

**Decision:** Aelix mirrors. The harness's session event stream (Phase 2.2) is piped to stdout via `serialize_json_line(event.to_dict())`. Events do NOT carry the `type: "response"` envelope — they carry their native `type` (`assistant_start`, `text_delta`, etc.).

### P-110 — Pi RpcClient spawns Node child process with `--mode rpc`; Aelix needs Python equivalent

Pi `rpc-client.ts:62-82`:
```typescript
spawn("node", [cliPath, ...args], {
    cwd: this.options.cwd,
    env: { ...process.env, ...this.options.env },
    stdio: ["pipe", "pipe", "pipe"],
})
```

**Decision:** Aelix RpcClient uses `asyncio.create_subprocess_exec` to spawn `python -m aelix --mode rpc` (or the CLI entry point at `src/aelix/__main__.py`). Streams accessed via `proc.stdin`/`proc.stdout`/`proc.stderr` (asyncio StreamWriter/Reader). The 100ms startup grace + 30s send timeout + 1s SIGTERM → SIGKILL escalation port verbatim.

### P-111 — CLI must accept `--mode rpc` flag

Aelix CLI entry `src/aelix/__main__.py` currently runs the REPL/print-mode. Sprint 6d adds `--mode rpc` flag that calls `aelix_coding_agent.rpc.run_rpc_mode()` instead. Per Pi, the flag is mutually exclusive with interactive mode.

### P-112 — Pi event sender uses `takeOverStdout()` to hijack stdout

Pi `rpc-mode.ts` calls `takeOverStdout()` before any output. Reason: any stray `console.log` from the harness/tools must NOT corrupt the JSONL stream. Aelix Python equivalent: redirect `sys.stdout` to a duplicate of `sys.stderr` at entry, then write JSONL only via the RPC writer. Use `contextlib.redirect_stdout(sys.stderr)` at the run_rpc_mode entry.

**Verify in W2:** that `print()` calls from inside tools/extensions DON'T leak into the JSONL stream during RPC mode.

### P-113 — Pi RpcClient `id` generation is numeric monotonic counter

Pi `rpc-client.ts` increments a per-instance counter for each send. Response correlation uses the `id` field. Aelix mirrors with `itertools.count(1)` or instance `_next_id`.

### P-114 — Pi RpcClient default 60s `waitForIdle` watches for `agent_end` event

Pi waits for the harness's "end-of-prompt-stream" signal (event type `agent_end`). Aelix Phase 2.2 session events include `done` (per Sprint 6a Anthropic emission); the RPC layer subscribes to harness session events and treats `agent_end` (or `done` equivalent) as the idle marker.

**Verify in W2:** the exact session event name Aelix emits when a prompt completes. If it's `done`, use that; if it's `agent_end`, use that. The closure pin asserts the constant matches Pi.

---

## §A — Scope (binding)

| Component | LOC est (prod) | LOC est (test) |
|---|---|---|
| `aelix_coding_agent/rpc/__init__.py` | ~20 | — |
| `aelix_coding_agent/rpc/_jsonl.py` | ~70 | ~80 |
| `aelix_coding_agent/rpc/rpc_types.py` | ~280 | ~80 |
| `aelix_coding_agent/rpc/rpc_mode.py` | ~400 | ~280 |
| `aelix_coding_agent/rpc/rpc_client.py` | ~320 | ~220 |
| CLI `src/aelix/__main__.py` — add `--mode rpc` flag | ~30 | ~40 |
| Pi parity closure pin (`test_phase_4_4_strict_superset.py`) | — | ~80 |
| **Totals** | **~1,120** | **~780** |

### NOT in scope (deferred per §J)

Server-side handlers stubbed as error responses (per Pi `success: false; error: ...` shape):
- `steer`, `follow_up` — separate harness command paths (Sprint 6f)
- `set_model`, `cycle_model`, `get_available_models` — needs ModelRegistry (Sprint 6e — paired with Copilot/Codex OAuth)
- `cycle_thinking_level` — needs cycle logic (Sprint 6f)
- `set_steering_mode`, `set_follow_up_mode` — queue mode flags (Sprint 6f)
- `set_auto_compaction`, `set_auto_retry`, `abort_retry` — harness loop additions (Sprint 6f)
- `abort_bash` — bash cancellation token (Sprint 6f hardening of Sprint 5b tool)
- `get_session_stats`, `export_html` — session inspection (Sprint 6f)
- `switch_session`, `fork`, `clone`, `get_fork_messages`, `get_last_assistant_text` — full session tree navigation (Sprint 6f)
- `get_commands` — extension/skill/template aggregation (Sprint 6e)
- `extension_ui_request`/`extension_ui_response` — bidirectional UI bridge (Sprint 6f TUI/Web UI work)

Client-side: full method surface ports verbatim; methods may receive server error responses and re-raise.

NOT in scope (true deferral):
- TUI/Web UI integration (Phase 5)
- Multi-language sample clients (Java/Go/Rust — Phase 6)
- `print-mode.ts` port (Pi has it as a sibling of RPC mode — separate sprint, not RPC)
- `interactive-mode.ts` port (Pi has it — that's the REPL Aelix already has at `aelix-coding-agent/cli/repl.py`)

---

## §B — `aelix_coding_agent/rpc/_jsonl.py` (NEW)

Port Pi `jsonl.ts` (58 LOC) verbatim:

```python
import codecs
from collections.abc import Callable
from typing import IO


def serialize_json_line(value: object) -> str:
    """Pi parity: jsonl.ts:10-12.

    LF-only framing. Payload strings MAY contain U+2028/U+2029 (valid
    inside JSON); clients MUST split on ``\\n`` only.
    """
    import json
    return json.dumps(value, ensure_ascii=False) + "\n"


class JsonlLineReader:
    """Pi parity: jsonl.ts:21-58 (``attachJsonlLineReader``).

    Streaming line reader that:
    - Decodes UTF-8 incrementally (multi-byte chunk-boundary safe)
    - Splits on LF (``\\n``) only — NOT U+2028/U+2029
    - Strips trailing CR (CRLF tolerance)
    - Emits any non-empty buffer at end-of-stream
    """

    def __init__(self, on_line: Callable[[str], None]) -> None:
        self._on_line = on_line
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, chunk: bytes | str) -> None: ...
    def end(self) -> None: ...
```

Asyncio variant `attach_jsonl_line_reader(stream: asyncio.StreamReader, on_line)` for client-side use.

### Tests
- Round-trip: `serialize_json_line({"x": "U+2028 inside string"})` then split-and-parse — same dict back
- Incremental: feed 4-byte UTF-8 char split across two chunks — single character emerges intact
- CR strip: feed `"hi\r\n"` → emit `"hi"`
- End-of-stream tail: feed `"partial"` then end() → emit `"partial"`
- Embedded LF inside JSON string: stringify `{"x": "a\nb"}` ROUND-TRIP via JSON gives `\\n` escape (literal `\n` inside JSON string is unambiguous because JSON quotes it)

---

## §C — `aelix_coding_agent/rpc/rpc_types.py` (NEW)

Port Pi `rpc-types.ts:1-262` verbatim. Python representation:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal, Union

# ── Commands (stdin) ─────────────────────────────────────────────────

@dataclass
class RpcCommandPrompt:
    type: Literal["prompt"] = "prompt"
    message: str = ""
    images: list[dict[str, Any]] = field(default_factory=list)
    streaming_behavior: Literal["steer", "followUp"] | None = None
    id: str | None = None

# ... 27 more dataclasses, one per Pi RpcCommand variant ...

RpcCommand = Union[
    RpcCommandPrompt, RpcCommandSteer, RpcCommandFollowUp, RpcCommandAbort,
    RpcCommandNewSession, RpcCommandGetState, RpcCommandSetModel,
    # ... 28 total ...
]

def parse_rpc_command(payload: dict[str, Any]) -> RpcCommand:
    """Dispatch on ``payload['type']`` to the matching dataclass.
    Raises ValueError on unknown type or missing required fields."""

# ── Responses (stdout) ───────────────────────────────────────────────

@dataclass
class RpcSuccessResponse:
    command: str
    success: Literal[True] = True
    data: Any | None = None
    id: str | None = None
    type: Literal["response"] = "response"

@dataclass
class RpcErrorResponse:
    command: str
    error: str
    success: Literal[False] = False
    id: str | None = None
    type: Literal["response"] = "response"

# ── Session State ────────────────────────────────────────────────────

@dataclass
class RpcSessionState:
    """Pi parity: rpc-types.ts:90-103."""
    thinking_level: str = "off"
    is_streaming: bool = False
    is_compacting: bool = False
    steering_mode: Literal["all", "one-at-a-time"] = "all"
    follow_up_mode: Literal["all", "one-at-a-time"] = "all"
    session_id: str = ""
    message_count: int = 0
    pending_message_count: int = 0
    auto_compaction_enabled: bool = True
    model: dict[str, Any] | None = None
    session_file: str | None = None
    session_name: str | None = None

# ── Extension UI ─────────────────────────────────────────────────────
# All 9 RpcExtensionUIRequest methods + 3 RpcExtensionUIResponse shapes.
# Sprint 6d ports the types but does NOT implement the bridge (deferred).
```

JSON serialization: snake_case Python ↔ camelCase Pi via `to_json()` / `from_json()` per dataclass. Field name remapping: `thinking_level` ↔ `thinkingLevel`, etc. Provide a single `_camel(key: str) -> str` helper.

---

## §D — `aelix_coding_agent/rpc/rpc_mode.py` (NEW)

Port Pi `rpc-mode.ts:1-492` with the harness mapping table from P-107.

```python
import asyncio
import contextlib
import signal
import sys
from collections.abc import AsyncIterator
from aelix_coding_agent.rpc._jsonl import (
    JsonlLineReader,
    serialize_json_line,
)
from aelix_coding_agent.rpc.rpc_types import (
    parse_rpc_command,
    RpcSuccessResponse,
    RpcErrorResponse,
    RpcSessionState,
)


async def run_rpc_mode(
    harness: AgentHarness,
    *,
    stdin: asyncio.StreamReader | None = None,
    stdout: asyncio.StreamWriter | None = None,
) -> None:
    """Pi parity: rpc-mode.ts::runRpcMode (lines 1-51 entry).

    1. Take over stdout: redirect ``sys.stdout`` to ``sys.stderr`` so
       stray ``print()`` from tools doesn't corrupt the JSONL stream.
    2. Attach JSONL line reader to stdin.
    3. Subscribe to harness session events; pipe each event verbatim
       to stdout via ``serialize_json_line``.
    4. Per incoming command: dispatch to handler; emit response.
    5. SIGTERM/SIGHUP signals trigger graceful shutdown.

    Default ``stdin``/``stdout`` use ``sys.stdin``/``sys.stdout``
    binary streams via ``asyncio.streams``.
    """


# Supported commands (P-107 table) ────────────────────────────────────

async def _handle_prompt(harness, cmd: RpcCommandPrompt) -> RpcResponse:
    """Pi parity: rpc-mode.ts:237-245 prompt handler."""

async def _handle_abort(harness, cmd: RpcCommandAbort) -> RpcResponse: ...
async def _handle_new_session(harness, cmd: RpcCommandNewSession) -> RpcResponse: ...
async def _handle_get_state(harness, cmd: RpcCommandGetState) -> RpcResponse: ...
async def _handle_get_messages(harness, cmd: RpcCommandGetMessages) -> RpcResponse: ...
async def _handle_compact(harness, cmd: RpcCommandCompact) -> RpcResponse: ...
async def _handle_bash(harness, cmd: RpcCommandBash) -> RpcResponse: ...
async def _handle_set_thinking_level(harness, cmd) -> RpcResponse: ...
async def _handle_set_session_name(harness, cmd) -> RpcResponse: ...

# Deferred commands (P-107) ────────────────────────────────────────────

DEFERRED_COMMANDS: dict[str, str] = {
    "steer":                   "ADR-0058 — Sprint 6f harness command paths",
    "follow_up":               "ADR-0058 — Sprint 6f harness command paths",
    "set_model":               "ADR-0058 — Sprint 6e ModelRegistry",
    "cycle_model":             "ADR-0058 — Sprint 6e ModelRegistry",
    "get_available_models":    "ADR-0058 — Sprint 6e ModelRegistry",
    "cycle_thinking_level":    "ADR-0058 — Sprint 6f",
    "set_steering_mode":       "ADR-0058 — Sprint 6f",
    "set_follow_up_mode":      "ADR-0058 — Sprint 6f",
    "set_auto_compaction":     "ADR-0058 — Sprint 6f",
    "set_auto_retry":          "ADR-0058 — Sprint 6f",
    "abort_retry":             "ADR-0058 — Sprint 6f",
    "abort_bash":              "ADR-0058 — Sprint 6f",
    "get_session_stats":       "ADR-0058 — Sprint 6f",
    "export_html":             "ADR-0058 — Sprint 6f",
    "switch_session":          "ADR-0058 — Sprint 6f",
    "fork":                    "ADR-0058 — Sprint 6f",
    "clone":                   "ADR-0058 — Sprint 6f",
    "get_fork_messages":       "ADR-0058 — Sprint 6f",
    "get_last_assistant_text": "ADR-0058 — Sprint 6f",
    "get_commands":            "ADR-0058 — Sprint 6e extension/skill/template aggregation",
}


def _make_deferred_handler(cmd_type: str, owner_adr: str):
    """Return a handler that emits ``RpcErrorResponse`` with the
    Pi-shape envelope. The closure pin asserts every key in
    ``DEFERRED_COMMANDS`` returns this shape.
    """

    async def _handler(harness, cmd) -> RpcErrorResponse:
        return RpcErrorResponse(
            id=getattr(cmd, "id", None),
            command=cmd_type,
            error=f"{cmd_type} not implemented in Sprint 6d ({owner_adr})",
        )
    return _handler
```

### Bash handler (P-107 #7)

`_handle_bash` invokes the builtin bash tool registered in Sprint 5b. Returns `BashResult`-shaped dict matching Pi `BashResult` from `coding-agent/core/bash-executor.ts`.

### Event subscription wiring

```python
async def _pipe_session_events(harness, write):
    async for event in harness.session.subscribe():
        line = serialize_json_line(_event_to_dict(event))
        write(line.encode("utf-8"))
```

Pi uses sync `session.subscribe((event) => { output(event); })`. Aelix uses async iterator; the same effect.

---

## §E — `aelix_coding_agent/rpc/rpc_client.py` (NEW)

Port Pi `rpc-client.ts:1-343` verbatim.

```python
import asyncio
import itertools
import os
import signal
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from aelix_coding_agent.rpc._jsonl import (
    JsonlLineReader,
    serialize_json_line,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommand,
    RpcResponse,
    parse_rpc_command,
)


@dataclass
class RpcClientOptions:
    """Pi parity: rpc-client.ts::RpcClientOptions."""
    cli_path: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    args: list[str] = field(default_factory=list)


class RpcClient:
    """Pi parity: rpc-client.ts:RpcClient.

    Spawns ``python -m aelix --mode rpc`` (or the configured cli_path)
    as a subprocess; wraps stdin/stdout with JSONL framing; correlates
    responses by ``id``; broadcasts session events to listeners.
    """

    DEFAULT_SEND_TIMEOUT_MS: int = 30_000
    DEFAULT_WAIT_FOR_IDLE_MS: int = 60_000
    STARTUP_GRACE_MS: int = 100
    SHUTDOWN_SIGTERM_TIMEOUT_MS: int = 1_000

    def __init__(self, options: RpcClientOptions | None = None) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def on_event(self, listener: Callable[[dict], None]) -> Callable[[], None]: ...

    # Full Pi command surface (28 methods); deferred ones will receive
    # error responses from the server which re-raise as RpcCommandError.
    async def prompt(self, message: str, images: list | None = None) -> None: ...
    async def steer(self, message: str, images: list | None = None) -> None: ...
    async def follow_up(self, message: str, images: list | None = None) -> None: ...
    async def abort(self) -> None: ...
    async def get_state(self) -> RpcSessionState: ...
    async def get_messages(self) -> list[dict]: ...
    async def compact(self, custom_instructions: str | None = None) -> dict: ...
    async def bash(self, command: str) -> dict: ...
    # ... 28 total ...

    # Utility helpers (Pi parity)
    def get_stderr(self) -> str: ...
    async def wait_for_idle(self, timeout_ms: int | None = None) -> None: ...
    async def collect_events(self, timeout_ms: int | None = None) -> list[dict]: ...
    async def prompt_and_wait(self, message: str, *, timeout_ms: int | None = None) -> list[dict]: ...
```

### Subprocess spawn

```python
async def start(self) -> None:
    cli_path = self._options.cli_path or self._resolve_default_cli()
    args = [cli_path, "--mode", "rpc", *self._options.args]
    self._proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "aelix",
        *args[1:],  # if cli_path is overridden, replace -m aelix accordingly
        cwd=self._options.cwd,
        env={**os.environ, **self._options.env},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # 100ms startup grace
    try:
        await asyncio.wait_for(self._proc.wait(), timeout=0.1)
        # If wait_for returns within 100ms, process exited early — error
        raise RuntimeError(f"RPC server exited prematurely: {self._proc.returncode}")
    except asyncio.TimeoutError:
        pass  # expected — process is still running
```

### Shutdown

```python
async def stop(self) -> None:
    if not self._proc:
        return
    try:
        self._proc.terminate()  # SIGTERM
        await asyncio.wait_for(self._proc.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        self._proc.kill()  # SIGKILL
        await self._proc.wait()
    finally:
        self._pending_requests.clear()
        self._proc = None
```

---

## §F — CLI `src/aelix/__main__.py` — `--mode rpc` flag

Existing entry needs:

```python
def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.mode == "rpc":
        from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode
        harness = _build_harness_from_args(args)
        asyncio.run(run_rpc_mode(harness))
        return 0
    # else: interactive REPL (existing path)
```

Argument parsing adds `--mode` with choices `["interactive", "rpc"]` (default `"interactive"`).

---

## §G — Tests (binding plan, ~780 LOC)

### Unit
- `tests/rpc/test_jsonl.py` — round-trip, U+2028 inside string, CR strip, multi-byte chunk-split, EOL tail
- `tests/rpc/test_rpc_types.py` — every RpcCommand variant round-trips through to_json/from_json; field name camelCase ↔ snake_case
- `tests/rpc/test_rpc_mode_dispatch.py` — `parse_rpc_command` happy path + bad type → ValueError
- `tests/rpc/test_rpc_mode_handlers.py` — each of 9 supported handlers invoked with fake harness; assert response shape
- `tests/rpc/test_rpc_mode_deferred.py` — every command in `DEFERRED_COMMANDS` returns `RpcErrorResponse` with success=false + matching error message

### Integration
- `tests/rpc/test_rpc_mode_stdin_stdout.py` — spawn `run_rpc_mode` in subprocess; feed JSONL commands via stdin; assert JSONL responses on stdout
- `tests/rpc/test_rpc_mode_event_pipe.py` — fake harness emits session events; assert events serialized to stdout per Pi shape
- `tests/rpc/test_rpc_client_lifecycle.py` — `RpcClient.start()` + `prompt()` + `wait_for_idle()` + `stop()` against a fake CLI subprocess
- `tests/rpc/test_rpc_client_timeout.py` — `send_timeout_ms` triggers when server doesn't respond
- `tests/rpc/test_rpc_client_shutdown.py` — SIGTERM → wait 1s → SIGKILL; stderr captured

### Pi parity closure pin
- `tests/pi_parity/test_phase_4_4_strict_superset.py`:
  - Assert 28 RpcCommand variants accounted for (9 implemented + 19 deferred = 28)
  - Assert every key in `DEFERRED_COMMANDS` returns `RpcErrorResponse`
  - Assert JSONL framing constants (LF only; CR tolerant)
  - Assert RpcSessionState fields ⊇ Pi 12 fields
  - Assert RpcExtensionUIRequest 9 methods covered in dataclass surface (deferred but present)
  - Assert RpcClient default constants (30s send, 60s idle, 100ms grace, 1s SIGTERM)

---

## §H — ADRs

### Amend
- **ADR-0020** — `0020-rpc-mode-multi-language-clients.md` — status Draft → Accepted; point to ADR-0058 closure
- **ADR-0034** — add row: "Sprint 6d shipped RPC mode JSONL protocol + 9 implemented commands + 19 deferred via error responses. Full command coverage in Sprints 6e/6f."

### NEW
- **ADR-0056** — `0056-rpc-jsonl-protocol.md` — LF-only framing + StringDecoder + CR strip + tail emit (Pi parity)
- **ADR-0057** — `0057-rpc-types-and-envelope.md` — RpcCommand + RpcResponse + RpcSessionState shapes (28 commands + uniform error envelope)
- **ADR-0058** — `0058-phase-4-4-strict-superset-closure.md` — closure pin. Roster: P-105 ~ P-114. `RPC_DEFERRED_COMMANDS` ownership table.

## §I — README
Add Sprint 6d sub-table + 3 new ADR rows.

---

## §J — Forward-compat clause (binding)

After Sprint 6d:
- `DEFERRED_COMMANDS` dict in `rpc_mode.py` enumerates all 19 deferred commands with owning ADR.
- Any future PR that wires a deferred command MUST drop it from the dict in the same PR (enforced by closure pin).
- `RpcExtensionUIRequest` + `RpcExtensionUIResponse` dataclasses ship in 6d as TYPES only — no bridge. Sprint 6f wires the bridge as part of TUI/Web UI work.

---

## §K — Sprint workflow (ADR-0032)

- W0 — research (this section's findings) ✓ DONE
- W1 — this spec (binding)
- W2 — executor opus implements §B~§F
- W3 — verification (pytest + ruff + pyright spike — preserve 8-error baseline)
- W4 — code-reviewer opus (parallel with W5)
- W5 — architect opus Pi parity audit (parallel with W4)
- W6 — apply must-fixes + atomic commits + ADRs accepted

**Atomic commit plan (W6, 5 commits):**
1. `feat: rpc — JSONL protocol + serialize/reader (ADR-0056, P-106)`
2. `feat: rpc — RpcCommand + RpcResponse + RpcSessionState types (ADR-0057, P-107/P-108)`
3. `feat: rpc — rpc_mode dispatcher + 9 supported commands + 19 deferred stubs (ADR-0058, P-107/P-109/P-112)`
4. `feat: rpc — RpcClient subprocess wrapper + CLI --mode rpc flag (P-110/P-111/P-113/P-114)`
5. `test: Sprint 6d — N new tests + 1 Pi-parity fixture + Phase 4.4 closure pin + docs (ADRs amend + NEW 0056/0057/0058 + README + spec)`

---

## §L — Verification gates

| Gate | Threshold |
|---|---|
| pytest | 923 baseline + ~80 new ≈ 1003+; 0 fail |
| ruff check | clean |
| pyright spike | 8 errors (baseline preserved) |
| Pi parity closure | `DEFERRED_COMMANDS` populated (19 entries); 9 implemented commands routed |
| Atomic commit count | exactly 5 |

---

**End of binding spec. Architect READ-ONLY until W6.**
