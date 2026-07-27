# 0198. Print-mode JSON event-envelope stability contract (`--mode json`)

Status: Accepted (2026-07-26) — lands with the P2 implementation.
Date: 2026-07-26
Relates: ADR-0056 (RPC JSONL framing — the same LF-only, one-object-per-line
discipline this stream inherits), ADR-0057 (RPC types & envelope — the serializer
this mode reuses), ADR-0071 (RPC command surface), ADR-0089 (the non-interactive
CLI entrypoint that owns `--mode json`), ADR-0197 (the P2 subagent runtime, the
first in-tree consumer of this stream).
Source spec: `.omc/specs/multiagent-profiles-teams-architecture-spec.md` §9
(bullet 3 — the third ADR; §"Partial fulfilment" below records what it does not
cover).
Pi pin: `earendil-works/pi@734e08e`.

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적
목표입니다."** — the *transport* is pi-parity (`rpc-mode.ts:86-87`, events flow
out untransformed). The *field spelling* is not, and cannot be: pi emits
TypeScript objects, aelix emits Python dataclasses. That single fact is what this
ADR exists to write down.

**Anchor convention.** Every `file:line` was re-read against the P2 baseline
`ab1932a`.

## Context

`aelix --mode json -p` has shipped since ADR-0089. Until P2 it had **exactly one
in-tree consumer discipline: none.** The only assertion on its shape was
`tests/cli/test_print_mode.py:220-224`, which checks that each emitted line
parses as JSON and nothing more.

P2 (ADR-0197) makes a second aelix process the primary consumer of that stream:
the parent spawns `-m aelix_coding_agent --mode json -p --no-session`, reads the
child's stdout, and reduces it into a `SubagentResult`. That turns an
undocumented debugging output into a **load-bearing internal wire format**, and
four measured properties of it are traps that a reasonable implementer walks into:

```
first stdout line of a JSON run          -> {"id": …, "created_at": …}   # NO "type" key
message_update.partial                   -> repeats the WHOLE assistant message per delta
                                            (measured stream-to-result ratios: 329x, 2773x)
bogus-model run, JSON mode               -> exit code 0, empty stderr,
                                            stream carries stop_reason: "error"
startup failure (no API key)             -> exit 1, ZERO stdout bytes (not even the header)
message_end with one 199 246-byte read   -> ~207 KB on one line
asyncio StreamReader default limit       -> 65536  → readline() raises, UNRECOVERABLY
```

Each of those, taken alone, produces a silent wrong answer in a consumer that
assumed the obvious thing.

## Decision

### D1. The wire format is `dataclasses.asdict` over kernel event dataclasses — therefore **snake_case**

`modes/print_mode.py:161` emits `json.dumps(_event_to_dict(event)) + "\n"`;
`_event_to_dict` (`print_mode.py:59-68`) delegates to
`rpc/rpc_mode.py::_dataclass_to_dict` (`:258-268`), which is a bare
`dataclasses.asdict(value)`.

Consequence, stated so nobody re-derives it: **the kernel's event and message
dataclasses are an invisible wire format.** Renaming a field on
`AssistantMessage`, `ToolExecutionStartEvent` or any `AgentEvent` subclass is a
**breaking change to `--mode json`**, even though no serializer names it.

Concretely, and this is where a line-for-line port of pi's parser fails: the
fields are `stop_reason`, `error_message`, `cache_read`, `cache_write`,
`total_tokens`, and tool-call blocks carry `tool_name` / `input` — **not** pi's
`stopReason` / `name` / `arguments`. A camelCase reader silently reads `None`
everywhere, so every failure looks like a success with zero usage. What *does*
match pi: the content-block discriminators `"text"` / `"toolCall"`, and the
`{type, …}` event envelope itself.

### D2. `usage` is **explicitly OUT** of the D1 guarantee — consumers must dual-read

`AssistantMessage.usage` is typed `dict[str, Any] | None`
(`packages/aelix-ai/src/aelix_ai/messages.py:127`). It is passed through `asdict`
untransformed, so **its keys are whatever the provider adapter wrote** — they are
not protected by the dataclass shape and they are not stable across providers.

This is not a hypothetical: the kernel itself dual-reads
(`session/compaction.py:1036-1043`):

```python
total = usage.get("total_tokens") or usage.get("totalTokens") or 0
… (usage.get("cache_read") or usage.get("cacheRead") or 0)
… (usage.get("cache_write") or usage.get("cacheWrite") or 0)
```

Every consumer read of `usage` MUST go through a dual-spelling helper of the same
shape. `usage` is additionally **`null` on errored messages**, so every read must
also tolerate absence. Cost is `usage["cost"]["total"]` when present, else
computed consumer-side from the message's `provider` + `model` — openrouter and
openai-completions emit no `cost` key at all, so the fallback is the common path,
not the exceptional one.

Scope note, deliberate: the conformance test (D7) does **not** pin `usage`'s inner
keys. Pinning them would assert a guarantee the type does not make.

### D3. The first line has no `type`, and may not be there at all

`print_mode.py:174-185` emits a session-metadata header — `{"id": …,
"created_at": …}` from `dataclasses.asdict(metadata)` — **before** any event, and
does so under a best-effort `try/except` that swallows everything except
`BrokenPipeError`.

Two rules follow: **use `event.get("type")`, never `event["type"]`**, and rely on
neither the header's presence nor its absence.

### D4. `message_update` is O(n²) and must be ignored

Its `partial` repeats the entire assistant message on every delta (measured
stream-to-result ratios of 329× and 2773× on ordinary turns), while the outer
`message.content` stays `[]` for the duration of streaming.

A consumer accumulates from **`message_end`** and treats **`agent_end`** as a
terminator only — `agent_end` carries the whole message array again on a single
line. The five event types worth consuming are `agent_start`, `turn_start`,
`message_end`, `tool_execution_start`, `agent_end`. `tool_result_end` **does not
exist** in either pi's live event table or aelix; do not write the branch.

Summary extraction takes the last `role == "assistant"` message that has a text
part and concatenates its `content[]` blocks where `type == "text"`, skipping
`type == "thinking"`. pi returns only the *first* text part; concatenating is a
deliberate, better divergence.

Token counts are a **level, not a flow** — `tokens` overwrites, it does not sum,
while `input` / `output` / `cache_read` / `cache_write` do sum.

### D5. JSON mode never exits non-zero on an assistant error, and a startup failure emits nothing

`print_mode.py`'s `stop_reason in ("error","aborted") → exit_code = 1` mapping is
guarded by `if mode == "text"`. A bogus-model JSON run therefore **exits 0 with
empty stderr** while the stream carries `stop_reason: "error"`.

The inverse also holds: a child that dies before the loop starts — no API key, an
unreadable prompt file, an import failure — exits non-zero having written **zero
stdout bytes**, not even the D3 header.

Therefore a consumer's failure predicate must be a disjunction over exit code,
`stop_reason` and its own outcome; and its message-fallback chain must include a
**stderr rung**, or a startup death reports as an empty success. Because normal
SIGTERM teardown prints `Task exception was never retrieved` /
`future: <Task finished … exception=SystemExit(143)>` to stderr — that is
`_signal_cleanup_and_exit` (`print_mode.py:259-269`) calling `sys.exit(128 + sig)`
inside a coroutine, and it is not an error — that rung must be sanitized before it
is shown to a model, with the raw text preserved elsewhere.

Unparseable lines are **silently skipped**. aelix deliberately declines pi's
`takeOverStdout` (`print_mode.py:23-26`), so an extension or MCP server inside the
process can `print()` straight into the stream.

### D6. The transport must be **chunked**, not line-based — a routine event exceeds asyncio's ceiling

`asyncio.create_subprocess_exec`'s `StreamReader`s default to `limit=65536`, and
`readline()` **raises** past it instead of truncating. Executed against a real
child emitting a 200 000-byte line followed by a valid `{}` line:

```
limit = 65536
RAISED        ValueError: Separator is not found, and chunk exceed the limit
second RAISED ValueError: Separator is found, but chunk is longer than limit
```

The oversize bytes stay in the buffer, so **every subsequent `readline()` raises
the same error** — "increment a counter and discard" is unimplementable on
`readline()`, and the terminating `agent_end` is lost with it.

And 200 KB is routine, not exotic: a `message_end` carrying one `read` of
`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (199 246 bytes)
serializes to roughly 207 KB — and `message_end` is D4's **sole** source of the
summary and the usage.

The contract is therefore on the *reader*: pass an explicit `limit=`, read fixed
chunks (`await reader.read(65536)`), and assemble lines yourself, dropping an
in-progress line that exceeds a stated per-line budget and resynchronising at the
next newline. Never raise. The mirror rule is on the process side: **both pipes
must be pumped concurrently for the child's whole lifetime.** Draining stdout to
EOF and then reading stderr deadlocks — reproduced with a child writing 1 MiB to
stderr between two stdout lines, which hung for a full two-minute budget and did
not unwedge even after `proc.kill(); await proc.wait()`. A real aelix child writes
to stderr routinely (`print_mode.py:225`, `:239`, provider SDK/httpx logging, any
extension `print(..., file=sys.stderr)`).

### D7. `tests/cli/test_print_mode_json_contract.py` is mandatory

The shape above is pinned by a dedicated conformance test, because
`tests/cli/test_print_mode.py:220-224` asserts only JSON-parseability. It pins:
the sequence contains
`agent_start` / `turn_start` / `message_start` / `message_end` / `turn_end` /
`agent_end` in order; `agent_end` is last; `message_end.message` carries
`stop_reason` / `error_message` / `usage` / `model` / `provider` / `role` in
**snake_case**; the discriminators are `"text"` / `"toolCall"`; tool-call blocks
carry `tool_name` / `input`. It does **not** pin `usage`'s inner keys (D2).

**The file was missing until the P2 review (MEDIUM #9)** — `ls tests/cli/ | grep
-i print` returned only `test_print_mode.py`, i.e. an accepted ADR asserted a
gate that did not exist — and it now ships in the shape this section describes,
with two corrections that came out of writing it:

* **It consumes a REAL child process.** Every assertion is made on bytes that
  crossed a real pipe from a second interpreter, assembled by the same
  `LineAssembler` a delegation uses. An in-process capture cannot fail the way a
  wire can (encoding, buffering, line framing, a stray write to the wrong
  stream), and the whole point of this section is that the WIRE is the contract.
  Only the provider is stubbed, because a real child needs a real model and the
  suite is offline.
* **"the first line has no `type`" is corrected.** The typeless session-metadata
  header is emitted under `if session is not None`, and a subagent runs
  `--no-session` — so for the delegation case, which is the case this ADR exists
  for, there is no preamble and the first line is `agent_start`. Both facts are
  pinned. The consumer rule is unchanged and is what actually matters: read
  `event.get("type")`, never `event["type"]`, and never index line 0.

Its value is demonstrable rather than asserted: injecting a pi-style camelCase
rename into the emitter fails 6 of its assertions while
`tests/cli/test_print_mode.py` stays entirely green.

A second test drives a real print-mode emitter in-process and feeds its captured
stdout through the consumer's reducer, so the field map and the emitter's shape
cannot become two agreeing fictions. The conformance file closes the triangle by
folding a real CHILD's bytes through that same reducer, so a coordinated rename
of emitter and reader together is caught by the shape assertions above.

### D8. `SubagentResult` grows only by **defaulted** fields

The versioning rule that makes ADR-0197 §(b)'s
`MIN_SUPPORTED_CONTRACT_VERSION..CONTRACT_VERSION` window real is stated here as
well, because it governs the consumer side of this stream:

> Adding a DEFAULTED dataclass field to `SubagentResult` / `SubagentUsage` /
> `SubagentProgress` is **additive** and does NOT bump `CONTRACT_VERSION`.
> Changing an existing field's shape, or adding a required parameter, does.

`details`, `dropped_lines` and `permission_mode` were all added under this rule
inside P2 itself, and a test constructs a `SubagentResult` from a v1 kwarg set to
keep it honest.

## Partial fulfilment of spec §9

Spec §9's third ADR asked for a channel-envelope record covering **both**
`--mode json` and `--mode rpc`, plus a cross-channel parity test. This ADR
delivers the `json` half, which is the half P2 consumes. Named as P3 follow-ups:

- the `rpc` half of the envelope contract (`--mode rpc` shares the serializer at
  `rpc_mode.py:258-268` but has its own command/response envelope, ADR-0057);
- the **cross-channel parity test** asserting that the same turn produces
  equivalent event sequences on both channels.

## Consequences

- The `--mode json` stream is a documented contract for the first time, with a
  conformance test behind it.
- Kernel dataclass field names are now, explicitly, a compatibility surface for
  anyone consuming `--mode json`. This is a *documented* constraint, not a new
  one — it has been true since ADR-0089 and was simply unwritten.
- Consumers get four rules that each close a silent-wrong-answer bug: read with
  `.get("type")`, ignore `message_update`, dual-read `usage`, and never trust the
  exit code alone.
- The 64 KiB `readline()` ceiling is recorded as a transport property rather than
  discovered per consumer. ADR-0197's spawner is the first to obey it.

## Known limitations / follow-ups

- **`usage` remains genuinely unstable**, and this ADR does not fix that — it
  documents it and mandates dual-reading. A typed `Usage` dataclass in
  `aelix-ai` would close it properly and is a candidate follow-up; it would be a
  breaking change for every adapter, so it needs its own decision.
- **The `rpc` channel is uncovered** (see *Partial fulfilment*).
- **No schema artifact is published.** The contract is a test plus this record;
  a JSON-Schema or `.d.ts` emission for external consumers is not in scope, and
  no external consumer is claimed.
- **The header line is best-effort by construction** (D3). Making it mandatory
  would mean failing a run because session metadata could not be read, which is
  the wrong trade.
