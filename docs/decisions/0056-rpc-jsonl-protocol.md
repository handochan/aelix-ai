# 0056. RPC JSONL Protocol

Status: Accepted (Sprint 6d / Phase 4.4 / W6 shipped)

## Context

Pi 1.0 exposes a JSONL stdin/stdout protocol behind `pi --mode rpc` that
multi-language clients use to drive the agent runtime. ADR-0020 named
`packages/aelix-rpc/` as the future port target; Sprint 6d ships the
JSONL framing primitive at
`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py` (Pi
parity P-105 — Pi's RPC mode is under `coding-agent/modes/rpc/`, not
`agent/`).

Pi `jsonl.ts` (58 LOC) is uncompromising about the framing rules:

- LF (`\n`) is the **only** record separator. U+2028 / U+2029 are
  legitimate code points inside JSON string payloads and MUST NOT be
  treated as separators (the spec preamble explicitly avoids Node
  `readline` because it splits on those code points).
- The transport is UTF-8 with an incremental `StringDecoder`, so a
  multi-byte UTF-8 character split across two TCP/pipe chunks is
  emitted intact.
- Trailing `\r` on a line is stripped before the listener fires (CRLF
  tolerance for Windows pipes).
- When the stream ends with a non-empty residual buffer, the buffer is
  emitted as a final line — partial-last-line preservation.

## Decision

Aelix mirrors Pi's framing rules verbatim in
`aelix_coding_agent.rpc._jsonl`:

```python
def serialize_json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


class JsonlLineReader:
    def __init__(self, on_line: Callable[[str], None]) -> None: ...
    def feed(self, chunk: bytes | str) -> None: ...
    def end(self) -> None: ...
```

The UTF-8 incremental decode uses `codecs.getincrementaldecoder("utf-8")`
so multi-byte chunk-boundary safety is identical to Pi's
`StringDecoder`. The asyncio-side helper
`attach_jsonl_line_reader(stream: asyncio.StreamReader, on_line)` is the
client-side adapter for piping a subprocess's stdout into the same line
reader.

**Pi parity invariants (closure-pinned):**

1. `serialize_json_line(value).count("\n") == 1` — exactly one LF per
   record; the framing LF.
2. `serialize_json_line` uses `ensure_ascii=False` so U+2028 / U+2029
   inside string payloads survive the round-trip.
3. `JsonlLineReader` strips trailing `\r` before emitting.
4. `JsonlLineReader.end()` flushes any residual buffer as a final line.

## Consequences

- Aelix can speak the Pi JSONL protocol byte-for-byte; multi-language
  clients written against Pi's wire format work against Aelix without
  modification.
- The `ensure_ascii=False` policy means JSON output may contain
  non-ASCII code points (U+2028, U+2029, emoji, CJK). Clients MUST decode
  with UTF-8 and MUST NOT split on anything other than LF.
- Tests live at `tests/rpc/test_jsonl.py` (foundation behavior) +
  `tests/pi_parity/test_phase_4_4_strict_superset.py` (P-127 U+2028
  round-trip + P-128 per-variant field-set).

## Related

- ADR-0020 — RPC Mode for Multi-Language Clients (Accepted Sprint 6d).
- ADR-0034 — Pi reference version pin (amended Sprint 6d).
- ADR-0057 — RPC types and envelope.
- ADR-0058 — Phase 4.4 strict superset closure (closure pin).

## Phase

Sprint 6d / Phase 4.4 (shipped — closure pin Green).
