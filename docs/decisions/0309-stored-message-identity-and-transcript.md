# 0309. A stored message has one session-scoped identity across RPC replay and live output

Status: Accepted
Date: 2026-11-20
Issue: #655 (Studio G05, native request R9)

## Context

Studio needs to read CLI-added turns after the CLI releases a session. The
handoff order is native #655, aelix-acp #22, aelix-studio-host #4 scope 1, then
aelix-studio #30. Classic `get_messages` reads the harness's model context,
which can omit compacted turns. Classic `get_fork_messages` lists user fork
points. Pi at `1e34c3b4` has these classic reads plus a flat `get_entries` and
`get_tree`. Its ordinary message-end path emits before appending. This design
uses Pi's parent walk, with a pinned selection and a receipt after the write
(ADR-0235, #655). [Classic reads](../../packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py)

On 2026-11-20 the owner answered Q1 "새 메서드 transcript/list (권장)", Q2
"유휴 시점에 다시 맞추기 (권장)", Q3 "저장된 세 역할 모두 (권장)", Q4
"조각으로 나눠 손실 없이 (권장)", Q5 "성한 경로 + 복구 표시 + 자리표시 (권장)",
and Q6 "저장된 그대로 보여 주기 (권장)". The following design includes the
lead's scope calls. [Issue and published wire examples](https://github.com/handochan/aelix-ai/issues/655#issuecomment-7017576778)

## Decision

### D1. Use the native header and stored entry IDs

The durable key is `(nativeSessionId, entryId)`. Both values are opaque,
case-sensitive, nonempty strings read from the native header and entry. An
ordinary reopen of an intact file preserves the pair. Two appends with equal
payloads still allocate separate IDs. The JSONL version and message encoding
stay the same (#655, `test_transcript_repeats_equal_payloads_with_distinct_keys`).

The projection includes stored user, assistant and toolResult messages. It
keeps the decoded native payload with its snake_case fields, ordered content
and `toolCall` and `toolResult` discriminators. Custom entries, visible or
hidden custom messages, placed notices, compaction, branch summaries, labels,
model/thinking changes and leaf markers keep their physical entry IDs and
contribute zero rows. A same-role reducer replacement contributes one row with
the accepted payload (`test_compacted_branch_cursor_pins_history_and_rebind_fences_it`
and `test_live_frames_bind_the_decoded_write_and_tool_occurrences`).
[Projection and codec](../../packages/aelix-agent-core/src/aelix_agent_core/session/transcript.py)

### D2. Number the messages on one inclusive parent path

Walk from a selected tip to the root and reverse the path. Number qualifying
rows from 1, independent of page offset. Each row carries the immediate
physical `parentEntryId`, which can be null or name a filtered entry. Common
ancestors preserve sequence across branches, and sibling messages can share
an ordinal. Compaction leaves prior messages on the physical path.
Navigation and withdrawal select branches. Re-appended survivors have new
IDs, and an old tip still selects the old path (ADR-0286, #655,
`test_compacted_branch_cursor_pins_history_and_rebind_fences_it` and
`test_same_file_reopen_refuses_every_selector_until_writer_settles`).

Fork-before copies through a user entry's parent, fork-at copies through the
selected entry, and clone forks at the current leaf. The copied prefix keeps
entry IDs and ordinals under a fresh header ID. A clone taken during active
delegation copies the pre-settlement prefix. The cancellation result remains
on the outgoing file. Runtime JSONL import preserves the source header and
entry IDs while repo `fork_from` allocates a new header (ADR-0307 D3 and
#655). [Native session replacement](../../packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py)

The transcript reader checks decoded IDs before map collapse. A duplicate ID
in the file or a forward parent or cycle on the selected path returns
`invalidTree` before recovery can hide it. An unrelated dangling or cyclic
sibling can coexist with an intact tip. In a clean file a dangling parent or
leaf target on the selected path returns `invalidTree`. Recovered reads prune
orphans and markers whose targets were lost, then recover the active tip.
Classic open, picker, fork, clone and import keep their existing load behavior
(`test_cli_opens_legacy_damage_and_only_transcript_refuses_the_selection`,
`test_import_accepts_duplicates_but_transcript_refuses_them` and
`test_recovery_prunes_lost_leaf_markers_and_invalidates_old_cursor`).
[JSONL decode](../../packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_storage.py#L505)

### D3. Pass a private occurrence through the sink and the write

An installation has a fresh opaque bindingId. Each eligible message occurrence
has one streamId before its first frame. Each tool occurrence reserves the
result's streamId before execution and carries it through start, partials,
execution end and the result lifecycle. Parallel tools can end in completion
order while result rows commit in source order. Hooks can change a call ID
without affecting the link (`test_live_frames_bind_the_decoded_write_and_tool_occurrences`).
[Occurrence sink](../../packages/aelix-agent-core/src/aelix_agent_core/loop.py#L107)

The six annotated classic types and end fields are listed in
[control-v1 §5](../contracts/control-v1.md#5-classic-commands--observed-not-frozen).
The reducer runs, the accepted message enters model state and the write
completes before the subscriber sees the original event. A `stored` outcome
means the committed line can be read back at
`{leafId:entryId,firstSequence:sequence,limit:1}` on a settled reader of the
same intact lineage. Its coordinates and `replaced` come from that append.
Qualification uses message counts indexed from the reader's decode at open
and updated from each appended line, with no whole-file read during ordinary
commits (`test_a_tool_turn_qualifies_receipts_without_whole_session_reads`).
The binding validates the file's readability and header once, at installation
or on the first receipt append if the file was absent.
The wire reserves room for the largest admitted request ID, recovery
indication and continuation.
`storageOnly` names a confirmed append without qualified readback, including a
failed header check, a skipped ancestor or coordinates that exceed the bound
(`test_receipt_with_an_undecodable_ancestor_is_storage_only` and
`test_unreturnable_coordinates_keep_the_classic_end_with_storage_only`).
`notStored` identifies a definite refusal, including refusal to resolve an
earlier write before this one starts. `unknown` identifies an ambiguous
current write. The latter three omit coordinates and `replaced`. A cancelled
reducer or append can leave the occurrence without an end. The control-v1
cases in `transcript.list.json` exercise all four statuses on encodable
original frames. [Commit and observation](../../packages/aelix-agent-core/src/aelix_agent_core/harness/core.py#L5847)

An unanswered input can retain its receipt on an abandoned path. The
in-memory abort marker has no stored row. Retry and overflow can leave a
stored failure on the path and generate a new assistant occurrence. Queued
input starts a lifecycle on drain. Direct deferred appends and withdrawal
survivors can be replay-only (ADRs 0286, 0290, 0307 and #655).

A #800 report can be the cancelled `agent` call's tool result or a separately
admitted user message on the next prompt. The first takes the parent's
result stream and the parent session's ordinal. The second gets a separate
user stream ahead of drained and submitted input. A recognized committed
carrier or admitted copy suppresses reinjection. Redaction of the tool
carrier alone can leave the separately rendered owed copy when recognition
fails. A hook can replace the copy on its own lifecycle (ADR-0307 D1–D2,
`test_report_carriers_have_distinct_live_keys_and_directed_readback`; see #839
for the separate redaction follow-up).

Lifecycle admission fences reads before the seal, while output stays
attached through outgoing disposal on a capable binding. A half-closed
client on such a binding can receive a committed outgoing report while
stdout accepts writes. Every binding stops writing on the first broken
pipe (`test_outgoing_report_receipt_is_delivered_during_disposal`). A successor receives a
fresh bindingId before its setup and session-start events. A pre-invalidation
abandonment retains the old binding and waits for the seal to lift. A stale
pre-settlement same-file fallback suppresses discovery and live linkage. On
a successful reopen, including a return after an intermediate session,
the lead's call is to keep the successor's read fenced until the outgoing
writer settles the file. The next read uses the settled bytes
(`test_outgoing_report_receipt_is_delivered_during_disposal` and
`test_same_file_reopen_refuses_every_selector_until_writer_settles`).
[Lifecycle admission](../../packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py#L1285)

ACP #22 should stage by binding, compare the complete ordered membership by
native key and reconcile at settled idle. Compare the binding, native header,
leaf, total, public recovery flag and membership across the idle boundary.
Stage early successor events until their file lineage is known. Reconcile
after receipt loss without replaying actions. Queue readback outside ACP's
inline stdout callback. A submitted composer echo ends on completion,
rejection or cancellation. Build native-backed user bubbles from native
occurrences, including injected and drained ones. Attach execution details
when the result receipt establishes the key. The lead's recommendation for
load is to send only new keys and the full membership for a matching,
complete scan, including an empty membership. #22 and host #4 own that
publication and its acceptance (#655 wire examples and handoff).

### D4. Read one pinned branch with zero session writes

`transcript/list` reads the producing store's detached file snapshot under
its writer lock. It refuses during a lifecycle transition or pending
settlement, including for an explicit null leaf. Writer-side settlement
resolves pending storage at the end of a turn and at disposal. The final
reply writer checks the admission fence before it emits success
(`test_same_file_reopen_refuses_every_selector_until_writer_settles`).
[Snapshot](../../packages/aelix-agent-core/src/aelix_agent_core/session/jsonl_storage.py#L843)
[Admission](../../packages/aelix-coding-agent/src/aelix_coding_agent/rpc/control.py#L540)

The parameter, row, cursor and byte rules are in
[control-v1 §6.1](../contracts/control-v1.md#61-transcriptlist). A cursor is
opaque and binding-local. An ordinary append or navigation preserves the
pinned path. Restart, rebind or a change in skipped lines, pruned IDs or the
unterminated tail indication invalidates continuation. Public
`recovered:true` describes the source file, including unaffected siblings.
A codec failure produces a role and coordinates with
`unavailable:"unencodable"`, then the scan continues
(`test_cursor_refuses_changed_recovery_with_unchanged_tip_and_flag` and
`transcript.list.json`).

The strict compact UTF-8 message codec sorts keys and rejects nonfinite
numbers and invalid Unicode. An encodable message that exceeds an empty page
is split into successive offset-based padded base64 parts. One part page
contains up to 196,608 raw bytes. The actual reply, including LF, is capped
at 1,048,576 bytes for caller IDs with at most 1,024 bytes of ASCII-escaped
quoted JSON. Mandatory metadata that cannot fit gives a short correlated
error. An encodable row that cannot fit a one-byte fragment gives the same
refusal. Replay assembles a whole pinned scan before publishing it
(`test_wire_reply_budget_fragments_reassemble_and_keep_the_next_row` and
`test_request_id_bound_and_encoding_fallback_remain_correlated`).
[Part packing](../../packages/aelix-coding-agent/src/aelix_coding_agent/rpc/control_transcript.py#L348)
[Final byte check](../../packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py#L2657)

### D5. Extend control-v1 discovery on the writer-owned CLI binding

A consumer gates on `version === 1` and `capabilities.methods` containing
`transcript/list`. Rediscover on rebinding. The service requires a
writer-owned, persistent CLI-RPC binding with its observer and reader
installed and binding coordinates that fit an empty reply. Discovery omits
WebSocket, memory, sessionless, unowned, foreign read-only and stale bindings
and bindings with unencodable or oversized native IDs
(`test_an_unencodable_header_keeps_classic_events_and_hides_transcript`). Temporarily pending storage can still refuse a
read on a discoverable binding. Classic responses, on-disk payload, public
event dataclasses and print/JSON retain their existing meaning (ADR-0275,
ADR-0302 D9, `test_floor.py` and `tests/pi_parity/`).

## Alternatives considered

The design considered widening classic `get_messages`, exposing Pi's whole
tree or flat tail, reserving a durable ID before the write, and returning
oversized placeholders. A separate control keeps the classic model context
intact. Branch selection follows ancestry directly. Commit-time receipt
coordinates come from the store's chosen parent, and parts carry the payload
of oversized encodable rows (#655).

## Consequences

A complete pinned scan establishes membership and content for that view.
The consumer can keep older off-branch keys and retain the previous clean
view when displaying recovered history. Fragmentation adds base64 overhead.
Classic live output stays provisional until a receipt arrives. The acceptance
for actual ACP publication and Studio DB reconciliation belongs to #22 and
host #4 scope 1, respectively (#655).

## Limits

- A disconnected reader has no durable stream journal. A read can recover a
  known intact path, with lost live joins and potentially unknown former tips
  (ADR-0307 D2 and #655 D3).
- The classic observation can expose data a later reducer replaces, and a
  large or unencodable classic frame can be lost. Readback carries the
  accepted stored payload or placeholder (ADR-0198 and #655 D3–D4).
- Same-header replacement imports and diverged aliases need fresh or
  quarantined consumer mappings. A confirmed ordinary reopen reuses its
  mapping (#655 D2).
- Power-loss durability, file repair, simultaneous access to a foreign
  writer and lock-owner information remain outside #655.
- Historical native tool results lack execution details and unobserved
  child metadata. The lead's call defers permission and child-progress
  occurrence keys and exhaustive caller-block placement. Ambiguous
  annotations stay detached in the consumer (#655 D3).
- Settlement across a reopen is process-local. A process exit before an
  owed withdrawal replay or #800 reinjection can leave only the selection
  represented by the surviving bytes (ADR-0242 and ADR-0307).
- When an old writer settles inside `session_before_switch` after the successor has
  opened the file, the successor can append from its stale leaf, a classic storage
  behavior present on base `83714045` in the #655 reopen probe, while the transcript
  can still read the survivor by selecting its old branch.
- When the low-level storage API writes a leaf marker before its target, later
  receipts on that path are `storageOnly` even if its rows read back, and reopening
  restores `stored` (#655 marker probe, a write order the product's writers avoid).
- A session file made unreadable after the binding validated it can still receive
  `stored` receipts that fail readback.
