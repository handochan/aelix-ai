# 0319. Choose how completed thinking appears in the TUI

Status: Accepted (2026-11-24, #909).

Date: 2026-11-24

Issue: [#909](https://github.com/handochan/aelix-ai/issues/909)

Related decisions: ADR-0115, ADR-0123, ADR-0221, ADR-0235.

## Owner decision

On 2026-11-24 the owner asked, “thinking 카드에서 렌더링 후 전체 thinking 다 보여줄지 렌더링 후 thinking 숨기기 옵션도 있으면 좋겠습니다”.
Asked where it should go, the owner answered “TUI만”.

## Decision

The lead's call in #909 is to add `completedThinkingDisplay` to settings and
expose it as **Completed thinking** in the TUI's `/settings` menu. `full` is the
default, including for older settings files and unrecognized values.
`test_completed_thinking_display_round_trips` checks the on-disk key, both
values, and the fallback. `test_run_tui_seeds_and_applies_completed_thinking_display`
checks startup and the live menu change.

With `collapsed`, each reasoning part with visible reasoning that completes in
the live TUI gets a `💭 Thinking… (/expand N)` line whose body can be expanded;
a part of whitespace alone gets none, live or on replay, in the
`view-collapsed-blank` and `replay-collapsed-blank` PTYs. The `menu` and
`menu-full` 120×40 PTYs exercise both choices through `/settings`. The ordinary
pre-answer reasoning tail is visible with both choices when `hideThinkingBlock`
is off, as those captures show. In the `held-late` PTY, the answer owns the live
window and reasoning that resumes during the answer appears at completion,
as it does on `9227275b`. The `view-collapsed-late` and
`replay-collapsed-late` PTYs show two live placeholders around the answer and
one when replaying the same saved block. Ctrl+T continues to toggle the
`hideThinkingBlock` override for the current session. The
`test_collapsed_completed_thinking_keeps_the_right_live_tail_and_replays`
renderer cases and the `display-hidden` / `display-ctrl-t` PTYs exercise this
interaction.

## Alternatives and consequences

The lead retained the existing hide override because it also covers the active
tail, as measured by `display-hidden`. The renderer chooses the completed
representation before it commits to scrollback, whose rows are already owned
by the terminal after commit (ADR-0194 and ADR-0221).

Pi's `interactive-mode.ts` uses one `hideThinkingBlock` flag. Ctrl+T saves that
flag and updates rendered assistant message components. Pi
at the inspected revision has no completed-only choice, so the separate
setting is an Aelix-additive divergence under ADR-0235.
[Pi interactive mode at `fe1c0810`](https://github.com/earendil-works/pi/blob/fe1c08109d76a21c36f2ed64a41e854c05220d29/packages/coding-agent/src/modes/interactive/interactive-mode.ts#L4823-L4837)
· [Pi keybinding at `fe1c0810`](https://github.com/earendil-works/pi/blob/fe1c08109d76a21c36f2ed64a41e854c05220d29/packages/coding-agent/src/core/keybindings.ts#L51-L53).
