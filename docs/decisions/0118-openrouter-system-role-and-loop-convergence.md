# 0118. OpenRouter `system` Role (not `developer`) + Agentic Convergence Guidance

Status: Accepted (W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

The user reported the TUI agent **stuck in an infinite loop** repeating the same
`git log` tool call on a vague request, and suspected a reference (pi) divergence.
Investigation (with a full pi reference comparison) found **two distinct bugs**:

### Bug 1 — `developer` role rejected by OpenRouter providers (blocking, intermittent)
For reasoning models, the adapter sent the system prompt as `role: "developer"`
(`convert_messages`, gated on `compat.supports_developer_role = not is_non_standard`).
This is **verbatim Pi behavior** (`openai-completions.ts:766-767,1104`). But the
`developer` role is an **OpenAI-native (api.openai.com o-series) feature**;
OpenRouter is a **proxy** to many providers, and several (verified: **Parasail**
serving `qwen/qwen3.6-35b-a3b`) reject it: `HTTP 400 "Unexpected message role"`.
Empirically confirmed: forced to Parasail, `role:"developer"` → 400,
`role:"system"` → 200. The ADR-0114 routing (`ignore: ["Ambient"]`) pinned the
model onto Parasail/AkashML and so **exposed this latent, pi-parity bug** —
turns failed intermittently depending on which provider the request landed on.

### Bug 2 — agentic non-convergence loop
On a vague request, the weak model (qwen3.6) kept re-calling `git log` and never
produced a final answer. The pi reference comparison was decisive: **pi has NO
loop/iteration cap** (no `maxSteps`/`maxIterations`; `shouldStopAfterTurn` is an
unused seam), **no repeated-tool-call dedup**, and its default system prompt is
**also minimal and convergence-silent** (leaner than Aelix's). So pi shares this
vulnerability — it is NOT an Aelix-vs-pi structural gap. The threading was also
verified clean (tool_call_id ↔ tool_result ids match; prior work is visible to
the model). The cause is purely the model failing to converge without explicit
guidance.

## The decisions

- **OpenRouter → `system` role** (`_openai_compat.py`): `supports_developer_role =
  not is_non_standard and not is_openrouter`. Reasoning models on OpenRouter now
  send `system` (accepted by every provider) instead of `developer`. Native
  `api.openai.com` o-series still uses `developer` (unchanged). **Deliberate
  OpenRouter-reality divergence from a verbatim Pi port** (same justification class
  as ADR-0114: OpenRouter proxies to providers with quirks Pi's `developer`
  assumption doesn't hold for).
- **Convergence guidance in the system prompt** (`cli/agent_context.py`): added a
  "Converging to an answer" section — stop calling tools once you can answer; never
  call the same tool with the same arguments twice; answer ambiguous requests with
  a stated assumption rather than digging indefinitely; prefer the fewest tool
  calls. This is **authored guidance, not a Pi port** (Pi's prompt has none — it
  relies on capable models). Net-new because Aelix targets weak local models that
  won't self-converge.
- **No hard loop cap added.** Pi has none; the prompt fix resolved the live
  repro. An Aelix-additive runaway backstop (max tool-iterations / repeated-call
  detection, graceful stop via the `should_stop_after_turn` seam) remains an
  optional follow-up — deferred to avoid a protected-`loop.py` divergence until
  shown necessary.

## Consequences

- **Live-verified** (PTY, qwen/qwen3.6-35b-a3b, the original looping request "tell
  me about the recent commits in more detail"): **0 errors** (no more 400), the
  model ran `git log` **twice then converged** to a rich final answer — no loop.
- All OpenRouter reasoning models are now robust to provider `developer`-role
  rejection. Minor: o-series-via-OpenRouter loses the (preferred-but-optional)
  `developer` precedence — acceptable, `system` works for them too.
- **Known/deferred**: the optional runaway backstop guard (above).

## Verification

- ruff clean; pyright 8-baseline (0 new); full pytest 2949 pass / 1 skip
  (+3 tests: OpenRouter reasoning uses `system`, native OpenAI keeps `developer`,
  prompt has convergence guidance); protected paths byte-unchanged (changes are in
  `packages/aelix-ai` + `packages/aelix-coding-agent`, not the protected core).
- Empirical: `developer`→400 / `system`→200 on Parasail; live convergence (2 tool
  calls → final answer).
