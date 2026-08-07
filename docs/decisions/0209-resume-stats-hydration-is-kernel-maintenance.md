# 0209. Seeding a resumed session's stats is kernel maintenance, not delegation policy

Status: Accepted (2026-08-07).
Date: 2026-08-07
Relates: ADR-0197 (the band rule — the kernel `aelix-agent-core` carries no delegation
policy; a kernel edit that adds no delegation surface is authorised by exception, naming
its ADR). ADR-0208 (the same exception mechanism for the session-durability kernel edits —
this ADR is the direct analogue for a session-stats correctness fix).
GitHub: #122.

**Provenance.** The session-stats path is aelix-original; this is a bug fix to it.

---

## The problem

Bug #122: after `/resume` (and after startup `aelix --continue`/`--resume`), the TUI
usage indicators — `/context`, `/cost`, `/session`, `/stats`, and the footer meter — read
**zero or stale** until the next turn. Root cause: `get_session_stats` and
`_get_context_usage_safe` aggregate `self._state.messages`, but on a session replacement
the runtime left `_state.messages == []` because the message-hydration line ran only inside
`if setup is not None:`. The resumed session's persisted history was never folded into the
stats source.

The in-session `/resume` fix has to run in the kernel: `_finish_session_replacement`
(`packages/aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`) is the
one place that owns `_state.messages` at replacement time. (The startup `--continue` half of
#122 is fixed entirely in product-core — `cli/entry.py` seeds `initial_messages`, which
`AgentState.messages` already consumes — and needs no kernel change.)

## The decision

Authorise `runtime/agent_session_runtime.py` in `_KERNEL_CHANGE_ALLOWLIST`
(`tests/agents/test_p2_band_boundaries.py`). The change moves an existing
`_state.messages = list(build_context.messages)` rebuild OUT of the `if setup is not None:`
guard so it runs on **every** replacement (resume included). It is:

- a **replace** assignment, not an append — idempotent (a re-switch stays at N, not 2N), and
  the next turn still `extend`s only its delta, so the resumed history is counted exactly once;
- **byte-identical for the setup path** (which already ran the same assignment after `setup()`);
- **non-delegation**: it adds no `aelix_agents` import, no spawn behaviour, no cap, no
  registry and no consent policy. `test_kernel_has_no_subagent_surface` still passes, and an
  independent review confirmed the diff carries no delegation vocabulary.

This is the same category as ADR-0208's session-durability entries: kernel-band data/stats
correctness, authorised by exception because the defective code *is* the kernel.

## Consequences

- The kernel freeze stays *by exception* with a written reason, per ADR-0197.
- No behaviour change for a fresh session (the rebuild source is empty → no-op) and no
  double-count on the next turn (verified by test).
- The footer cross-session leak half of #122 (Session A's cached usage bleeding into
  Session B) is fixed in the TUI (`_rebind` re-runs the existing turn-end refresh against the
  newly-seeded harness) — product-core, not authorised here.
