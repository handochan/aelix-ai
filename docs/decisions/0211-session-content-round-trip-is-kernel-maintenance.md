# 0211. A persisted content block must come back whole — kernel maintenance

Status: Accepted (2026-08-10).
Date: 2026-08-10
Relates: ADR-0197 (the band rule — the kernel `aelix-agent-core` carries no delegation
policy; a kernel edit that adds no delegation surface is authorised by exception, naming
its ADR). ADR-0208 / ADR-0209 / ADR-0210 (the same exception mechanism for
session-durability, resume-stats hydration, and session-cost correctness). ADR-0210 in
particular **names this work as the gap it deliberately left open**. ADR-0190 (thinking
replay). ADR-0049 (the `mime_type`/`data` split and `ThinkingContent` itself).
GitHub: #135.

**Provenance.** `_message_from_dict` is aelix-original — pi has no message decoder at
all. This is a bug fix to aelix's own divergence, not a parity change.

---

## The problem

`_message_to_dict` is `dataclasses.asdict`, so **every** field of every content block
reaches the JSONL. `_message_from_dict` rebuilt those blocks field-by-field and read back
only some of them. The values were written correctly, sat on disk intact, and died at the
reload boundary.

ADR-0210 closed the message-level scalars. Enumerated by driving a sentinel value through
`dataclasses.fields()` into every field of every content type and diffing both object
graphs, **nine losses remained, all inside content blocks**:

| Lost on reload | Why it matters |
| --- | --- |
| `ImageContent.mime_type`, `.data` (user + toolResult) | the pi-canonical pair |
| `ThinkingContent` — the **whole type**, returned as a raw `dict` | ADR-0190 replay |
| `ToolCallContent.thought_signature` | Gemini multi-turn continuity |
| `TextContent.text_signature` (all three roles) | Responses + Gemini replay |

**The image loss is a hard failure, not degraded fidelity, and the issue understates it.**
`tools/read.py` builds `ImageContent(mime_type=..., data=...)` with `source=""` — so the
only populated fields were exactly the two the reader ignored. Measured end to end against
a real 24,331-byte PNG through the real storage layer:

```
source sha256   : 99f6b4be…40f7      restored bytes  : 0
jsonl on disk   : 32853 bytes        restored sha256 : e3b0c442…b855  (empty)
```

The resulting request carries `image.source.base64: ""`. Both payloads were sent to the real
Anthropic API to confirm the consequence rather than infer it:

```
pre-fix  (data='')       HTTP 400  invalid_request_error
                         "messages.0.content.1.image.source.base64: image cannot be empty"
                         request_id req_011CdtWPMVuS7jG4Tb7zgKdn
post-fix (data=32444 ch) HTTP 200  "**Black** dominates this image, forming the background."
```

So **resuming any session containing an image hard-failed the next turn** — an HTTP 400, not
a quality regression — and after the fix the same restored block answers normally.

`ThinkingContent` returning as a `dict` never satisfied the `isinstance(block,
ThinkingContent)` test at `_transform_messages.py:174`, so ADR-0190's signed-thinking
replay was dead on every resumed session — precisely as ADR-0210's "What this ADR does NOT
claim" section recorded. `resolve_thought_signature` drops anything falsy, so `""` silently
ended Gemini's chain of thought across a resume.

**Nothing was ever lost on disk.** The same JSONL measured 32,853 bytes before and after
the fix; only the reader dropped fields. Confirmed on real data: 149 pre-existing session
files on a live machine yielded **846 typed `ThinkingContent` blocks and zero raw dicts**,
read-only, with no migration and no file rewritten.

## The decision

Authorise **one** file in `_KERNEL_CHANGE_ALLOWLIST` (`tests/agents/test_p2_band_boundaries.py`):

- `session/entries.py` — make `_message_from_dict` **total**: read back `text_signature`,
  `mime_type`/`data`, `thought_signature`, and add the missing `"thinking"` branch.

The path is **already on the allowlist**, bought by ADR-0210 for the provenance fields; it
is named here because this ADR is the reason it changed again, and the gate would otherwise
pass with no written reason. **No path is added or removed.**

Two deliberate constraints:

- **`_wire_str` / `_wire_dict`, not `.get(key, default)`.** The default only fires when a
  key is *absent*; an explicit JSON `null` returns `None` and violates the declared `str`
  contract. Nothing would crash — `is_valid_thought_signature(None)` is simply `False`, so
  the signature is dropped exactly as before. The bug would look fixed while replay stayed
  broken. This is the recorded bytes-vs-str landmine class, and the type is the contract.
- **The raw-dict passthrough stays.** A block type only a newer aelix writes passes through
  verbatim rather than crashing an older reader. This is what pi does structurally.

**Not done, on purpose.** No `image` branch was added to `_assistant_content`: pi's
`AssistantMessage.content` union excludes images. Adding one would look harmless and be a
silent parity divergence.

**Non-delegation.** The file gains no `aelix_agents` import, no spawn behaviour, no cap, no
registry and no consent policy — it decodes JSON into dataclasses. `test_kernel_has_no_subagent_surface`
passes. The band rule isolates delegation POLICY from the kernel; reading back a field the
kernel itself wrote is not delegation policy, and the defective code *is* the kernel.

## Why this decoder exists at all

pi cannot have this bug. `jsonl-storage.ts:90-106` validates four envelope fields and then
`return parsed as unknown as SessionTreeEntry` — a TypeScript assertion erased at runtime,
so pi's parsed JSON *is* the entry, and `session.ts:41` casts again. pi never touches
message content on load.

Python cannot copy that shape: the entire downstream contract is `isinstance`-based
(`_transform_messages.py:174`, `_google_shared.py:296`, `_anthropic_transforms.py:155`), so
a passthrough would make every block a `dict` and break more than it fixes. The decoder must
exist — so the obligation it takes on is to be **total**. A test walks `dataclasses.fields()`
of all four content types and fails if any field does not survive a round trip, which buys
back pi's can't-lose-a-field property without pi's untyped shape. That test, not the four
one-line reads, is the durable fix for this bug class.

## Consequences

- Existing sessions become correct on next load. No migration, no rewrite, no data recovery
  step — the bytes were always there.
- Resuming a session containing an image no longer hard-fails the next turn.
- ADR-0190 thinking replay now has a typed block to preserve. **This ADR does not claim
  replay is thereby delivered end to end**: `_transform_messages` also requires `same_model`
  to be true, which depends on ADR-0210's provenance. Provenance decides *whether* the
  same-model branch is taken; this change decides whether a typed block survives for it to
  preserve. Both are necessary; neither alone is sufficient, and the end-to-end path on a
  live resumed session is verified by test but not yet by a driven TUI session.
- The stale comment in `entries.py` that asserted thinking blocks return as raw dicts was
  rewritten — it became false with this change, and a comment that lies is worse than none.
