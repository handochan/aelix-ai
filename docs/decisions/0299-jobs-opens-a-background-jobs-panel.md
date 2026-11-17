# 0299. `/jobs` opens a background-jobs panel; `/jobs list` keeps the table

Status: Accepted (2026-11-17, #776; fix round 1 2026-11-17: the panel closes with the application
and on a frame it cannot draw, so SIGTERM ends aelix with it open; Ctrl+C closes it and Ctrl+J is
Enter; the arm ends at 3 s and redraws; a stop's note keeps the waiting text; `jobs/cells.py` reads
U+FE0F, keycaps and joined sequences; fix round 2 2026-11-17: `jobs/cells.py` counts each side of
a zero width joiner and every control character, so it is never under `rich` 13.7.1–14.3.4 on the
measured corpus, and a cut stops reading once past the width). **Active since #611 S3e (#779,
2026-11-17):** `aelix_coding_agent/jobs/activation.py` `ACTIVE = True` (ADR-0293 §12). With it off
there is no `/jobs` at all (`tests/cli/test_jobs_command_gate_776.py`, a guard that passes on
`7e2feb50` too).
Date: 2026-11-17
Issue: #776 (#611 slice S3b). Parent: #611. Contract: `.omc/specs/611-s3-contract-r4-2026-11-12.md`
§4 ("as r3", "as r2": r2 §4 is the text), with its 정오표 E3. Sub-issue body:
`.omc/specs/611-s3-subissues/S3b-body.md`.
Notes on: ADR-0293 (§10 surfaces, §11 the waker's G2), ADR-0295 and ADR-0297 (each kind's rows),
ADR-0159 (in-flow modals), ADR-0298 (Ctrl+B under a dialog).
Owner decisions this implements, quoted from the brief:
- 2026-11-12, OQ-S3-1: "B: /jobs로 여는 패널 (권장)".
- 2026-11-12, OQ-S3-2: "↑↓·Enter·r·s s·Esc/q (권장)".

Not the owner's: the `s s` arm's rules (contract r2 §4.2, Codex r1 #8), the revised rows and the
table fix (the issue's design proposal), and "no dedicated key opens the panel in S3" (contract r1
§8's recommendation). The lead's calls (리드 판단), fix round 1: SIGTERM with the panel open ends
aelix, fixed in the panel only (§2); Ctrl+C closes the panel as every other modal's does; a stop
from the panel names the panel, or the nearest form the stop reason allows — which is "(/jobs)"
(§4). Fix round 2: `jobs/cells.py` counts each side of a zero width joiner rather than raise the
`rich` floor of the `[tui]` extra, and `/jobs stop` keeps the shared "(/jobs)" text (§1, §6). This lane's calls: `Enter` writes the job's detail — what `/jobs <id>`
prints — above the panel and keeps it open; an ended job's activity is its brief, as the table's
is; the detail area from 40 terminal rows; the state marks (`●` live, `○` stopping, `!`
unresolved, `✓` done/recovered, `✗` failed/lost, `■` stopped, `◆` a result waiting); the column
widths; the generic widget's shape (§2).

pi: `earendil-works/pi` `b7bb7ca90` (`origin/main`, read 2026-11-17) has no background-jobs list and
no `/jobs`; its `SelectList` (`packages/tui/src/components/select-list.ts`) is a filterable picker,
not a redrawn list with a stop key.

## Context

ADR-0293 §10 gave background jobs a footer segment, `/jobs` (a table, `<id>`, `result <id>`,
`stop <id>`, `send`) and the `job`/`job_stop` tools, whose `list` is the same table. Measured in a
pty before this change (gate forced on, scripted model,
`.omc/probes/611-s3-design/live/r2/snaps.txt` and `live/r3/snaps.txt`, again at `7e2feb50` in
`.omc/probes/775-776/776-impl/tui/before-7e2feb50-*`): ACTIVITY repeated the title
(`0s ago · agent prober · pr…`) because the table showed a kind's row 0, which held the label, the
profile and the title; at 80 columns the fixed columns left TITLE 5 cells (`probe`, `sleep`); a
Hangul title made a 120-column row wrap, because rows were cut by characters; and `/jobs list`
answered "no job list in this session".

## Decision

### 1. Bare `/jobs` in the TUI opens the panel; everything else keeps the table

`JobService.panel_hook` is what a bare `/jobs` awaits when a host has a panel; `None` prints the
table. `tui/jobs_ui.py` `JobsUi` sets it to `open_panel` when the TUI builds it (only with the gate
on) and takes it back in `close()`. The command returns what the panel closed with: `""` (nothing
printed) after Esc or `q`, the result page after `r` — printed by the dispatch exactly as
`/jobs result <id>` prints it. With no job, `/jobs` says "No background jobs in this session." and
opens nothing. `/jobs list` is the table, never the panel. RPC's `commands/run` has no hook and
prints the table; `-p` and `--mode json` do not run `/jobs` as a command at all — the text goes to
the model, as before this change (verify round 1, F3). The `job` tool's `list` is the table whatever
is hooked
(`tests/tui/test_jobs_panel_611.py::test_bare_jobs_opens_the_hook_list_prints_the_table`,
`tests/jobs/test_jobs_table_776.py::test_guard_job_list_is_the_table_whatever_the_tui_hooks`;
the real `run_tui`'s wiring: `::test_run_tui_hooks_the_panel_and_forwards_modal_changes`).
`/jobs send` and `result` are unchanged. `/jobs stop` is unchanged but for the text a stopped job's
notice and result give: "stopped by the user (/jobs)", where it was "(/jobs stop)" — the stop reason
is one fixed value the panel shares (§4). `/jobs <id>` and `job(action="status")` print the same
lines with the revised kind rows (§6). The entry-point sweeps:
`.omc/probes/775-776/776-impl/entry-point-sweep.md` and, for the ways the panel closes,
`.omc/probes/775-776/776-fix-r1/entry-point-sweep-close-paths.md`.

### 2. A generic list panel (`tui/list_panel.py`)

The panel is built on a widget that names no job, for #795's agents panel to reuse as it is
(`::test_the_widget_names_no_job`;
`::test_a_vanished_selection_goes_to_the_row_now_at_its_place_and_the_host_hears_it`): a host hands `PanelRow(key, columns, mark, detail)`s, a
`PanelLayout(rows, detail)` and `Column(width | None, min, drop)`s, its title, corner text, key row
and key handlers. The widget owns the selection — held by key, so a re-sort keeps it and a row that
vanished hands it to the row now at its place (`ListPanelModel.update` says so) — the scrolling
with `k/N` in the title row once there are more rows than fit, the fixed height, the drawing in
terminal cells, ↑/↓, the close keys and a redraw every `interval` (1 s). It mounts through
`show_modal` (ADR-0159), so a dialog a turn opens over it hands the slot back afterwards. Esc, `q`
and Ctrl+C close it; a host's `enter` is Ctrl+J too; a key nothing binds stops in the panel; a key
the application binds keeps the application's own filters (Ctrl+B is inert under a focused modal,
§5). Measured through the real key processor, Ctrl+C under the panel during a turn closes it,
reaches no interrupt and leaves the input as it was (`::test_ctrl_c_and_ctrl_j_under_the_panel_mid_turn`;
on `3f11a358` it ended the turn and left the panel open, Codex `keys-arm`, verify C120).
`AelixChrome.modal_on_top(content)` says whether a mounted modal is the one on screen.

It also closes by itself (리드 판단: the narrowest fix, in this widget, not in `show_modal`):
- when the application's run ends — `run_tui`'s SIGTERM/SIGHUP handler calls `chrome.exit()` — it
  watches `app.future` and closes, so the input loop leaves `/jobs` and `run_tui` reaches its
  teardown (`::test_sigterm_with_the_panel_open_at_idle_ends_run_tui`: `run_tui` done, no ticker,
  the runtime disposed; on `3f11a358` `run_tui` never finished, Codex `probe_shutdown.py`;
  `::test_the_app_ending_closes_the_panel_and_its_ticker`). In a pty aelix exited 0 in 0.27–0.47 s
  after SIGTERM with the panel open, idle and mid-turn, at 120x40 and 80x24; on `3f11a358` it had
  not exited after 40 s (`.omc/probes/775-776/776-fix-r1/tui/term-*`);
- when a host callback raises while a frame is drawn (the title, the corner text, the key row): the
  frame is blank rows of the fixed height and the panel closes; a raising `rows()` read keeps the
  last rows and the panel open (`::test_a_host_that_cannot_draw_closes_the_panel_and_a_failed_read_keeps_it`;
  Codex's `lifecycle` probe raised in the title, verify's in `rows()`).
Other modals are unchanged: an idle `/model` picker still outlives SIGTERM, as on base (verify P1).

### 3. The jobs panel (`tui/jobs_panel.py`)

- **Rows**, kind-agnostic: a state mark, the short id, the label, row 0, the state, the age and the
  activity — row 1 when the kind has one, else `Ns ago` or `quiet Nm`; an unresolved job's reason,
  `result waiting`, and an ended job's brief. When row 0 would get fewer than 20 cells the state
  column goes first (gone at 80 columns, shown at 120). It paints no background, and the selected
  row is bold cyan, as pyte reads the cells
  (`::test_the_panel_paints_no_background_and_marks_the_selection_in_bold_cyan`). Every row is
  measured in cells (`::test_the_rows_are_laid_out_in_cells[120]`, `[80]`, with a Hangul
  title cut with `…`).
- **Height**, fixed while open: the title row, 3 job rows, a rule, 3 detail rows and the key row
  from 40 terminal rows (9 rows); the title row, 3 job rows and the key row below (5 rows), whatever
  the number of jobs or their states
  (`::test_the_height_is_fixed_while_it_is_open`, terminals of 24, 39, 40 and 60 rows).
- **Detail** (40+ rows): the unresolved reason, `render_rows[1:]` and the notice state
  (`::test_the_activity_by_state`, `::test_the_mark_and_the_detail`).
- **Data**: `service.snapshots()` read at open and every second
  (`::test_it_reads_the_jobs_again_every_second`: a job started after the open shows with no key,
  and three redraws come 0.9 to 5 s apart),
  which includes the ended jobs #769 let go, read back from their records: such a job stays listed,
  keeps its selection and `r` pages its persisted result
  (`::test_an_ended_job_the_service_let_go_stays_listed_and_reads_back`).

### 4. The keys (owner OQ-S3-2) and the `s s` arm (contract r2 §4.2)

`↑`/`↓` select; `Enter` writes the detail above and keeps the panel open
(`::test_enter_writes_the_detail_above_and_keeps_the_panel`); `r` closes it and pages the result
into the transcript (`::test_r_closes_the_panel_with_the_result_page`); `Esc`/`q` close it and never
interrupt a running turn — the panel has the focus — 25 times in one process
(`::test_esc_and_q_close_it_and_never_end_the_turn_25_times`), and in a pty during a turn held by a
scripted model, which then ends `✓ done` (`.omc/probes/775-776/776-impl/tui/mid-120x40`).

The first `s` arms `(owner, job id)` of the selected job and the key row says "press s again to
stop <id>". The second `s` stops THAT job with `request_stop(reason="user")` only while the arm
holds: its owner is the service's owner now, the service is bound to the same harness (no reload,
no session change), the job is listed and not ended, less than 3 s (`ARM_SECONDS`) have passed —
at exactly 3 s it has ended (Codex L4) — and the panel is on top. The panel reads the jobs again
when the arm runs out, so "press s again" does not outlive it (`::test_the_arm_redraws_when_it_runs_out`,
verify N1). A stop's note (`stopping <id>`) takes the key list's place on the key row, never the
waiting text's, and a move clears it (`::test_after_s_s_the_key_row_still_says_results_wait` at 80,
100 and 120 — on `3f11a358` the note pushed the waiting text off at 80 and 100, verify F6 —
`::test_moving_after_a_stop_clears_its_note`). The stop's reason is the user's, whose text names
`/jobs` for both ways to it: "stopped by the user (/jobs)" (리드 판단 asked for "(/jobs panel)" or
the nearest form; a separate reason would be a new `StopReason` every kind must map, so the one
text covers `/jobs stop` and the panel). Moving the selection, the armed job leaving the list or ending, a dialog mounted
over the panel (JobsUi forwards the chrome's modal change to the panel) and the panel closing drop
it; a second `s` with no arm arms again and stops nothing. A job that left the list between the
presses, with or without a redraw between, is never stopped through the row that took its place
(`::test_a_second_s_after_the_armed_job_left_stops_nothing`,
`::test_a_second_s_before_any_redraw_after_the_job_left_stops_nothing`,
`::test_the_arm_goes_on_move_cover_reload_timeout_and_end` — a cover seen at a redraw too, 3.1 s,
exactly 3 s — with `nothing` and 2.999 s as the positive controls,
`::test_a_session_change_stops_nothing`,
`::test_closing_or_a_dialog_over_it_drops_the_arm_in_the_tui`).

### 5. Coexistence

The footer segment is unchanged. The panel is a modal, so the waker holds a notice turn while it is
open (G2, ADR-0293 §11) and its key row says "◆ waiting results go when this closes" while a
result waits; closing runs the waker through the chrome's modal change
(`::test_the_wake_waits_while_it_is_open_and_goes_when_it_closes`). Ctrl+B (ADR-0298) does nothing
under the open panel and works again once it is closed
(`::test_ctrl_b_under_the_open_panel_asks_nothing`, with the closed press as the positive control).

### 6. The table and the rows

The `/jobs` table's ACTIVITY is row 1 when the kind has one, never row 0 again
(`tests/jobs/test_jobs_table_776.py::test_activity_is_row_one_not_the_title_again`); before TITLE
gets fewer than 20 cells AGE goes, then ACTIVITY narrows to no less than 12 cells — the full table
needs 93 columns; at 80 AGE is gone and ACTIVITY has 21
(`::test_at_80_columns_title_keeps_20_cells_and_age_goes`); every cell is cut by cells with `…`
(`::test_a_hangul_title_is_cut_by_cells` at 80–160). From 100 columns the header is byte-identical
(`::test_guard_the_header_from_100_columns_is_as_before`), and TITLE starts in one column whatever
ACTIVITY holds (`::test_title_starts_in_one_column_whatever_activity_holds`).

The rows (the issue's design proposal) leave the label to the surfaces and are cut by cells in
product-core's `jobs/cells.py` (product-core cannot import `rich`). It reads widths from
`unicodedata`, with U+FE0F widening a narrow character before it (as `rich` 14.3 does), and it
counts more wherever the `rich` versions the `[tui]` extra allows (`rich>=13.7,<15`) differ: each
side of a zero width joiner on its own (`👨‍👩‍👧` is 6, as `rich` 13.7.1–14.2.0 read it, where 14.3
reads 2 — fix round 2, 리드 판단, rather than raise the floor), a control character as one (as
`rich` 14.3.0 does; the others give none), the marks Unicode 16 added as one (Python 3.14 reads
them as marks, `rich` before 14.3 gives them a cell), U+FE0F after a narrow character with no emoji
form (`"a\ufe0f"` is 2, 1 to `rich`), 452 spacing marks (`Mc`), 33 format characters, two Hangul
fillers, the line and paragraph separators and the code points this Python has not assigned (2).
Measured with `uv run --isolated --no-project --with rich==X` for 13.7.1, 13.9.4, 14.0.0, 14.1.0,
14.2.0, 14.3.0 and 14.3.4 on Python 3.12, and 13.7.1, 14.2.0, 14.3.0 and 14.3.4 on 3.11 and 3.14,
over the 34 strings of `tests/jobs/test_jobs_table_776.py`'s `RICH_CORPUS` (CJK, fullwidth,
combining marks, `☺️ ❤️ ✔️ ⚠️ ♥️ 1️⃣ #️⃣`, zero width joiner families, skin tones, a tag-sequence
flag, controls, a lone surrogate, decomposed and Extended-B Hangul, format characters, a Unicode 16
emoji), 5,000 random strings and every code point: no string and no code point is counted fewer,
and no cut is wider than its width (`.omc/probes/775-776/776-fix-r2/richver-out.txt`,
`richver-pythons.txt`; `rich` 14.3.0 itself raises on 139 of the random strings, a joiner at the
end, which were skipped). In the suite: equal to the locked `rich` 14.3.4 on the strings with no
joiner or control and more on the rest (`::test_cells_reads_the_widths_rich_reads`); never under
the widths recorded for 13.7.1 and 14.2.0 (`::test_cells_never_counts_fewer_than_an_older_rich_the_extra_allows`,
on `b27037db` 👨‍👩‍👧 read 2 against their 6); 2,000 random strings
(`::test_where_cells_and_rich_differ_cells_counts_more`). A `/jobs list` row of joined families is
80 cells at width 80 under `rich` 14.2.0, where it was 100 and wrapped on `b27037db`
(`.omc/probes/775-776/776-fix-r2/v1-zwj-table-wrap.txt`). A cut reads no further than it needs
(`::test_a_cut_reads_no_further_than_it_needs`: a 1 MB title is cut at 40 after fewer than 200
characters read; on `b27037db` the whole text was split and measured, 657 ms for 1 MB, verify N4). A table row with a
U+FE0F title is one printed line at 80 and 120 (`::test_a_vs16_title_row_does_not_wrap_when_the_tui_prints_it`;
on `3f11a358` it wrapped, verify F1). The service's own generic row, for a kind that raised or is no
longer registered, is the title, not `label · title` (verify N5,
`::test_a_kind_no_longer_registered_reads_as_its_title`):
- delegation (`aelix_agents/jobs_client.py`): the title `<profile>: <task>`; while it runs
  `running <tool>` or `thinking`, with `· <n> tok`; its last text on one line through `child_line`;
  `id <agent id>` (`tests/agents_ext/test_delegation_rows_776.py`);
- bash (`tools/bash_background.py`): the command; `<bytes> → <path>`, with
  `· output may be incomplete` when its output is unconfirmed
  (`tests/tools/test_bash_background_611.py::test_the_rows_mark_an_unconfirmed_output_and_are_cut_by_cells`).

## Consequences

- The table and `job(action="list")` show what a job does in ACTIVITY instead of its title twice,
  and `job(action="status")`/`/jobs <id>` show the revised rows. Three tests were changed for it,
  each to the new rows (`test_the_rows_name_the_output_file`, `test_agent_lone_surrogate_782`'s row
  check, `test_monitoring_611::test_jobs_layout`, which now asserts row 0 is not in ACTIVITY).
- #795's agents panel reuses `tui/list_panel.py`.

## Limits

- **No notice turn while the panel is open.** A result that ends while it is open waits until it
  closes (G2); the key row says so.
- **No dedicated key opens the panel** (contract r1 §8's recommendation, not an owner decision):
  `/jobs` does.
- **Ctrl+B does nothing inside the panel** (it is a modal).
- **Enter writes above the panel.** At 24 rows there is no detail area; the detail goes to the
  transcript and scrolls the screen.
- **Rendering measured in a pty only**: macOS, 120x40 and 80x24, a scripted model, through the
  gated launcher (`.omc/probes/775-776/776-impl/tui/`). Not measured with a real model (the lead's
  L7b), in tmux, the VS Code terminal or a Windows console.
- **`jobs/cells.py`** counts more than `rich` where they differ (§6), so a row can end short of the
  width: a Devanagari title cut at 40 is 30 cells to `rich` 14.3.4 and a run of `a️` 20, a family
  joined by zero width joiners is 6 cells here and 2 to `rich` 14.3
  (`.omc/probes/775-776/776-fix-r2/overcount-detail-head.txt`). It never ran past the width for
  `rich` 13.7.1–14.3.4 on the corpus §6 names; other `rich` versions, other strings and a terminal
  that draws a sequence differently from `rich` are not measured. The panel fits
  its own columns with the TUI's grapheme widths.
- **SIGTERM with another modal open** (an idle `/model` picker, say) still outlives it, as before
  this change (verify P1); only this panel closes with the application.
- **Mutants**, fix round 2: see `.omc/probes/775-776/776-fix-r2/mutants/summary.txt`.
- **Mutants**, round 0 (the lane's own set): 67 of 72 in the lane's set killed; the 5 left are
  judged equivalent (`.omc/probes/775-776/776-impl/mutants/summary.txt`): the arm's owner check (the list is the
  current owner's already), `on_lost` and the selected-row check (the arm is always on the selected
  row and a lost row drops it), the widget's `<any>` binding (it swallows only unbound keys), and
  the exact height (every frame draws exactly that many rows). Fix round 1
  (`.omc/probes/775-776/776-fix-r1/mutants/`): the round's own set on its new lines, 28 of 30
  killed — the 2 left are judged equivalent: the app-exit callback left registered after a close
  (it closes a closed panel) and the arm's redraw left scheduled after a drop (one more read of the
  jobs); the round-0 set on the lines this round touched, 3 of 3 killed (5 more no longer apply:
  their lines were rewritten); verify round 1's 43 survivors, 39 of which still apply, 22 of 39
  killed — all eleven verify named as consequential among them. The 17 left are judged equivalent or
  invisible, as verify judged 15 of them (the arm's owner and selected-row checks and `on_lost`, the
  re-entrance guard and the unhook check in `JobsUi`, the 100-column clause, the minimum widths, the
  `<any>` binding, a key press's own redraw, `KeyError` for `JobNotFound`, the close's disarm, the
  arm's row key, row 0 for the title); two more by this lane: the label cut with `[:10]` (real labels
  are under 10 cells) and `on_stop`'s own note reset (a move, Enter and every arm drop clear it).

## CHANGELOG

One entry under the "built and switched off" entries; #611 S3e's guide (`background-jobs.md`)
describes the panel.
