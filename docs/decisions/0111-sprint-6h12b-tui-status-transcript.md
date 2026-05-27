# 0111. Sprint 6h₁₂b — TUI Status Footer + User-Message Echo + Esc-to-Interrupt

Status: Accepted (TUI completeness Sprint B / W4 shipped)
Date: 2026-05-27
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance — pure tui/ consumer)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

## Context
From the 6h₁₂ TUI audit (P0 #4, #6; P1 #13). The footer showed only `⎇ branch`; the user's own
messages never appeared in the transcript (assistant replies floated with no visible question); and
interrupt was Ctrl-C only with no on-screen hint. Reference: `⏵⏵ default · 📂 ~/.deepsight · ✱ <model>`.

## The decisions (pure `tui/` consumer)
- **Footer** (`context.py`): `AelixTUIContext.__init__` gains keyword-only `model_provider` /
  `cwd` / `mode`. `_refresh_footer`'s default branch composes the present segments joined by
  `"  ·  "`: `⏵⏵ {mode}` · `📂 {cwd}` (home-abbreviated to `~`) · `✱ {model}` · `⎇ {branch}` ·
  extension statuses — each omitted when its source is absent (so it degrades to today's branch-only
  footer for headless tests / no model). The **single-composer invariant** (6h₁₀c) is preserved:
  `_refresh_footer` remains the sole `set_footer_line` writer and still spreads
  `get_extension_statuses()`, and the `footer_factory` (extension footer) branch + precedence are
  unchanged. `run_tui` wires `model_provider = live harness.current_model.id` (so a future `/model`
  reflects), `cwd`, `mode="default"`, and repaints the footer after `bind_ui`.
- **User-message echo** (`shell.py`): `_input_loop` commits `» {text}` (bold) immediately before
  `harness.prompt` — the model-prompt path ONLY (bash/`!`, slash-commands, empty, quit, reload all
  `continue`/`return` earlier, so they are not echoed). The transcript now reads `» <question>` then
  the assistant's markdown reply.
- **Esc-to-interrupt** (`chrome.py`): a `_running`-gated `escape` keybinding mirrors the Ctrl-C
  running branch (→ `on_interrupt` → abort); inert when idle (no interference with editing /
  completion-menu dismissal). `_render_working` appends a dim `" · esc to interrupt"` while running.

## Consequences
- The footer matches the reference (`⏵⏵ default · 📂 /workspaces/aelix-ai · ✱ openai/gpt-4o-mini ·
  ⎇ main`), the transcript shows the user's questions, and turns are interruptible with an on-screen
  hint — all live-verified. pyright 8-baseline; protected paths byte-unchanged.
- **Known (LOW, dead path)**: `descriptors._recompose_footer` (the descriptor-only fallback used
  ONLY when the renderer is unwired — never in `run_tui`, which always wires
  `context._refresh_footer`) still joins with `"  "` rather than `"  ·  "`. No live effect; left as-is
  to avoid churning a 6h₁₀c unit test for a non-executed path.

## Verification (W4)
- Gate: ruff clean; `uv run pyright` 8-baseline (0 new); full `pytest` **2861 passed**/1 skipped
  (+ footer/echo/esc tests); protected paths byte-unchanged.
- **W4 code-reviewer (opus): APPROVE-WITH-NITS** (0 CRITICAL/HIGH/MEDIUM) — rigorously verified the
  footer single-composer invariant + the echo prompt-path-only barrier + esc running-gate.
- **W4 qa-tester real-PTY (gpt-4o-mini): 5/5 PASS** — footer 4-segment `·`-joined; `» <msg>` echo
  above the reply; esc-to-interrupt hint + mid-stream abort without crash; bash/commands not echoed;
  `/quit` clean.

Next: Sprint C (compact tool cards — result truncation + per-tool headers; spec ready at
`.omc/specs/sprint-6h12c-tui-tool-cards-spec.md`), then D (model/context commands), E (polish).
