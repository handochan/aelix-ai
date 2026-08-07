# 0210. Session stats must report what was actually spent — kernel maintenance

Status: Accepted (2026-08-07).
Date: 2026-08-07
Relates: ADR-0197 (the band rule — the kernel `aelix-agent-core` carries no delegation policy;
a kernel edit that adds no delegation surface is authorised by exception, naming its ADR).
ADR-0208 / ADR-0209 (the same exception mechanism for session-durability and resume-stats
correctness). ADR-0190 (thinking replay — see "What this ADR does NOT claim").
GitHub: #135 (the remaining content round-trip gap this does not close).

**Provenance.** The session-stats and session-entry paths are aelix-original; these are bug
fixes to them.

---

## The problem

A live pre-beta QA pass found `/stats` reporting **`$0.0000` for every main session**, while
delegated-child cards priced correctly. Two independent causes, both in the kernel:

1. **No aelix provider adapter ever calls `calculate_cost`.** pi prices a turn inside the
   adapter; ours do not. A persisted `usage` therefore carries `input`/`output`/`cache_*` and
   **no `cost` key at all**, so `aggregate_session_stats` summing `usage.cost.total` was
   structurally `0.0`. Verified on real session files. Delegated children looked healthy only
   because that path calls the pricer directly.

2. **`_message_from_dict` (`session/entries.py`) never read back four fields `asdict` had
   written** — `AssistantMessage.api` / `.provider` / `.model` / `.response_id` and
   `ToolResultMessage.tool_name`. Measured: a JSONL carrying `provider: anthropic,
   model: claude-haiku-4-5` restored as `provider=None, model=None, api=None`. Without that
   provenance a message cannot be priced at all, so fixing (1) alone still returned `$0.0000`
   on real files.

A third defect was found during review: after `/compact`, `get_session_stats` sums a
`_state.messages` that `select_display_entries` has already truncated to the post-compaction
survivors, so twenty turns costing $4.00 report ≈$0.15 — and, once (1) and (2) were fixed,
report it **confidently**. Turning a harmlessly-zero field into a confidently wrong one is
the failure mode this work exists to remove.

## The decision

Authorise three files in `_KERNEL_CHANGE_ALLOWLIST` (`tests/agents/test_p2_band_boundaries.py`):

- `harness/_session_stats.py` — price a message from **its own** provenance via
  `calculate_cost` (so a model-switching or resumed session stays honest, and the
  cache-write/1h rules live in exactly one place). A cost the provider *did* resolve still
  wins. Additive `SessionStats.cost_known: bool = True`.
- `harness/core.py` — `get_session_stats` computes `_cost_is_complete()`. **Already on the
  allowlist** (ADR-0205 family); named here because this ADR is the reason it changed again.
- `session/entries.py` — restore the four dropped round-trip fields.

**`cost_known` contract.** `False` means token usage was seen that no price could be found
for, **or** that the priced range is provably incomplete — so the figure is a floor, not a
bill. The UI renders `≥ $X` when something was priced and `n/a` only when nothing was. It
**fails closed** on an unreadable branch: "at least $X" stays true either way, while an exact
figure might not. `True` on an all-zero session correctly means "nothing was spent", which is
why the default is `True` and why a sparse/duck-typed stats object still renders a figure.

**Non-delegation.** None of the three files gains spawn behaviour, a cap, a registry or a
consent policy, and none references `aelix_agents`. `test_kernel_has_no_subagent_surface`
passes; an independent adversarial review confirmed the same. The band rule isolates
delegation POLICY from the kernel — reporting what a session actually cost is not delegation
policy, and the defective code *is* the kernel.

## What this ADR does NOT claim

An earlier draft of this work asserted that the missing provenance also **silently defeated
ADR-0190**, by making every resumed turn look cross-model to `_transform_messages._is_same_model`
and downgrading signed thinking blocks. **That claim is false and is not made here.** Measured
against a control that never hit disk: a persisted `ThinkingContent` returns from disk as a
**raw dict** (`_assistant_content` has no `"thinking"` branch), so it never reaches the
`isinstance(block, ThinkingContent)` test at `_transform_messages.py:174` and falls through the
defensive passthrough at `:225` — **before and after** this change. Signed thinking blocks were
never being downgraded on resume, and are still not restored.

The provenance fix is the **prerequisite**; #135 (the content round-trip as a whole — thinking
blocks, `text_signature`, `thought_signature`, and image `mime_type`/`data`, the last of which
is real data loss on resume) is what would deliver correct replay. The one behavioural delta
here is that same-model resumed turns no longer receive cross-model `tool_call_id`
renormalisation, which is pi-correct.

## Consequences

- `/stats`, `/cost` and `/session` report a real figure instead of `$0.0000`; five real session
  files went from `$0.0000` / `known=False` to `$0.0159`, `$0.0017`, `$0.0045`, `$0.0222`,
  `$0.0180`, all `known=True`.
- A compacted or resumed-compacted session renders `≥ $X` rather than a wrong exact figure. The
  compaction entry records `tokens_before` — a context *level*, not a per-model spend — so the
  summarised-away prefix cannot be reconstructed and is deliberately not invented.
- Known inconsistency inside one `/stats` panel, documented where a reader meets it: Turns and
  Active time span the full history (they read the raw branch) while tokens/cost span only the
  survivors. Anyone changing one side should move both.
- The pi-parity pins (`test_session_stats_has_ten_fields`, `..._pi_shape`) now assert the pi 10
  **plus a named additive set** (`== {"cost_known"}`), which is stricter than the previous
  equality: an unplanned field still fails. `_session_stats_to_dict` enumerates its keys, so the
  RPC wire shape is unchanged.
