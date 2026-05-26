# 0109. Sprint 6h₁₁ — Agent Enablement: tools + system prompt + AGENTS.md + Markdown

Status: Accepted (Sprint 6h₁₁ / Phase 5c-tui follow-up / W5 shipped)
Date: 2026-05-26
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context (how this was found)

Live-testing the shipped TUI (`uv run -m aelix_coding_agent`) revealed it behaved as a **bare
chat model** — no coding-agent identity ("자아인식 안됨"), no tool use, no markdown, with the
model's raw reasoning leaking as the response. Root-cause investigation found the CLI built
`AgentHarnessOptions` with **`tools=[]` and `system_prompt=""`** (`entry.py::_build_harness_options`)
— a pre-existing gap from the 6h₆ print/rpc scope ("Pi's rich model-resolution lands with the
SettingsManager port" was deferred and never completed). The 7 coding tools existed
(`tools/create_all_tools`) but were never wired; no base system prompt was ever authored; and the
TUI stream renderer used plain `Text` instead of Markdown. The prior 6h₁₀a–e sprints faithfully
built the TUI *frontend* but the *agent itself was never configured as a coding agent*.

## The decisions

### Wire the toolset + a real system prompt (entry.py)
`_build_harness_options` now sets `tools=list(create_all_tools(cwd).values())` (read/bash/edit/
write/grep/find/ls) and `system_prompt=build_system_prompt(cwd)` (unless `--system-prompt`
overrides, using `None` as the sentinel so `--system-prompt ""` honors an explicit blank). These flow
through the harness `_rebuild_tool_registry` → `AgentState.tools` / `.system_prompt` → the LLM
request. `cwd` is resolved to an absolute path (`Path.cwd()`) so the tool sandbox root + AGENTS.md
anchor survive a mid-session `chdir`.

### Coding-agent system prompt + AGENTS.md context (`cli/agent_context.py`, NEW)
- `build_system_prompt(cwd)` — Aelix identity + the 7-tool description + behavior guidance (do via
  tools, be concise, verify, don't invent, be careful with destructive commands) + environment
  (abs cwd, platform, UTC date). Only trusted values interpolated (no injection surface).
- `discover_context_files(cwd)` — auto-discovers `AGENTS.md` walking cwd→root (root-most first,
  cwd-most last = more specific wins), gated by `--no-context-files`, appended to the prompt via
  `append_system_prompt`. UTF-8-safe (skips binary/undecodable files — `UnicodeDecodeError` is a
  `ValueError`, not `OSError`), and a 32 KB budget **truncates** rather than silently dropping an
  oversized file.

### Markdown streaming (`tui/stream.py`)
`_render_lines` renders the accumulated assistant text as Rich `Markdown` (code blocks → syntax
highlight) instead of plain `Text`. The line-windowed commit (stable prefix → scrollback, trailing
`live_window` lines stay live) absorbs partial-markdown volatility — aider `mdstream` parity.

## Verification

- **Live (real OpenRouter turns)** — the decisive proof:
  - `--print` "say hi" → **"Hello, I'm Aelix!"** (identity restored).
  - `--print --model openai/gpt-4o-mini` "read pyproject.toml…" → **tool executed**, answered
    "The project name field is `aelix`" (markdown code span).
  - **TUI** `--model openai/gpt-4o-mini` "ls the dir…" → **`ls` tool ran**, directory listing +
    markdown answer rendered in the chrome, footer pinned, no traceback.
- Gate: ruff clean; `uv run pyright` 8-error baseline (0 new); full `pytest` green (8 new
  agent-context tests + the stream-renderer markdown update); protected paths byte-unchanged.
- **W5 code-reviewer (opus): APPROVE-WITH-NITS** (0 CRITICAL/HIGH). 2 MEDIUM fixed in-sprint
  (AGENTS.md `UnicodeDecodeError` crash; oversized-file silent drop → truncate). cwd→abspath (LOW)
  applied. Markdown reflow (LOW) accepted as aider-parity tradeoff (mitigated by the 6-line window).

## The model finding (separate from this sprint)

The model in the test `.env`, **`qwen/qwen3.6-35b-a3b`** (a small reasoning MoE), returns its entire
response as a **single empty `ThinkingContent` block** — no text, no tool call (confirmed by a
message-history diagnostic). This is why the user's session looked broken even though the wiring is
now correct. With a capable model (gpt-4o-mini) everything works. **Recommendation:** set
`OPENROUTER_DEFAULT_MODEL` to a tool-reliable model, or `--model` override.

### Deferred
- **Reasoning-model "empty thinking block" handling** — the provider adapter's `thinking_format`
  may mishandle reasoning-only responses (empty `ThinkingContent`, lost content/tool_calls). Affects
  any reasoning model; warrants a dedicated `openai_completions` adapter investigation.
- `entry.py` extension-loading (Tier-1 trusted-Python — product/security decision, ADR-0108).
- Markdown streaming reflow hardening (byte-stable-across-renders commit); a partial-fence
  regression test.
