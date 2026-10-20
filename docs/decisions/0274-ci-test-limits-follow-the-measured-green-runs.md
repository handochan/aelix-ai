# 0274. CI's test limits follow the measured green runs

Status: Accepted (2026-10-20); review round 2 (2026-10-21: the step that #429's
limit killed now counts at its projected time, "Steps that ended on their
limit" below, so Decisions 1 and 2 give 35/40 on Ubuntu and Decision 5's
re-measure trigger counts the projected step; the per-day medians and the pi
paragraph are corrected); narrowed finish (2026-10-21: the range of slowest
steps that gives 35 and the slow-runner figure are corrected, and Windows'
smaller room for a slow runner is recorded under "Known limits")
Date: 2026-10-20
Issue: #482 (this change), #429 (the limits it replaces)
Amends: `docs/development/ci-hang-diagnostics.md`, #429's record of the limits.
No ADR held them. The per-test 240 s watchdog, the 180 s interpreter-exit
watchdog and the rest of that page stand.

## Context

#429 bounded `ci.yml`'s lint/test jobs at 25 minutes on Ubuntu and 50 on
Windows, and their `Test gate (pytest)` step at 20 and 45. GitHub's default is
six hours, and run 37806642579 had spent nearly six hours on a hang (#428). The
only stated basis was the slowest green *jobs* then, 15.6 and 31 minutes.

The suite has grown since. B2 added tests and the Python 3.14 legs (ADR-0265).
All eight lint + test legs are required checks on `main` (its branch
protection, read 2026-10-21, requires exactly those eight plus the four
installer end-to-end jobs). Run 38610624957 attempt 1 (`ubuntu-latest`,
py3.12) ended with `##[error]The action 'Test gate (pytest)' has timed out
after 20 minutes.` at 85%. Its last progress line, at 20.17 minutes, came 2.5 s
before the kill at 20.21 minutes.
No test had failed or was stuck: its 201 progress lines hold 14,472 results
with no `F` or `E`, and the gaps between them were at most 55 s, well under the
240 s watchdog. The same SHA passed the same step in 18.4 minutes on its PR
run. A required check that fails green blocks a merge until someone re-runs it.

## Measurement

`gh` read calls only (`run list`, `actions/runs/{id}/jobs?filter=all`). These
were the last 150 completed `ci.yml` runs, created from 2026-10-08 to
2026-10-19, including main `7faf4b9a`'s run 39287804079. Round 2 pulled them
again on 2026-10-21: no newer `ci.yml` run existed, and every number matched.
129 of the runs reached the test jobs. A job that a re-run of failed jobs
carried into the next attempt has the same timestamps there, and every number
below counts it once. The data is the step's `completed_at - started_at`, for
green steps only, in minutes:

| leg | n | median | p90 | max |
|---|---|---|---|---|
| ubuntu, all | 325 | 15.5 | 18.0 | **19.5** |
| ubuntu py3.11 / 3.12 / 3.13 / 3.14 | 104 / 102 / 104 / 15 | 15.5 / 16.1 / 15.1 / 14.8 | 17.7 / 18.3 / 17.0 / 17.9 | 18.4 / 19.5 / 18.3 / 18.1 |
| windows, all | 302 | 32.4 | 36.2 | **40.0** |
| windows py3.11 / 3.12 / 3.13 / 3.14 | 93 / 98 / 96 / 15 | 32.1 / 32.4 / 32.4 / 33.1 | 36.5 / 37.1 / 35.5 / 36.2 | 40.0 / 38.6 / 36.8 / 36.6 |

(p90 is `statistics.quantiles` with its default exclusive method.) The same
jobs, as whole jobs: Ubuntu median 16.1, max 20.0; Windows median 33.2, max
40.9. The rest of a green job outside pytest (checkout, uv, sync, ruff,
pyright, post steps) took a median of 0.5 and 0.8 minutes, and at most 3.2 and
2.6. Main `7faf4b9a`'s own run: Ubuntu 18.3 / 15.1 / 18.0 / 13.7 and Windows
39.1 / 38.6 / 36.6 / 35.5 for py3.11 / 3.12 / 3.13 / 3.14.

The slowest green steps were 19.5 minutes (run 39268812124, main `c02517ca`,
py3.12) and 40.0 minutes (run 38438296281, py3.11). #429's limits left 0.5 and
5.0 minutes above them, 2% and 12%. The suite keeps growing. A least-squares
fit of green step time against run date gives +0.27 min/day on Ubuntu and
+0.61 on Windows (r = 0.45 and 0.48, which is only a rough trend). The per-day
median, by run creation date (UTC), rose from 13.0 to 17.9 on Ubuntu and from
28.7 to 36.0 on Windows between 2026-10-08 and 2026-10-19.

### Steps that ended on their limit

A green step can never show more than the limit it ran under, so the slowest
green Ubuntu step is capped by #429's 20 minutes. Every pytest step in the
window that did not pass was compared with the limits in force (20 and 45 for
the step, 25 and 50 for the job); a step ended on its limit only if it ran
that long. Exactly one did: run 38610624957 attempt 1 above (`ef1b6311`, 20.2
minutes). No Windows step did. The longest failed Windows step took 37.2
minutes and the longest cancelled one 7.5; the other failed Ubuntu steps took
at most 16.4 and the cancelled ones at most 13.9, so counting them changes no
maximum. The one longer step, run 37806642579's 350.5 minutes (#428's hang),
ran on a commit from before #429's limits.

The killed step's full time is projected from its progress lines. Each line
holds 72 results of the same collection, so its lines align one to one with
those of the same commit's two passes on the same leg (PR run 38610632362, 18.4
minutes; run 38610624957 attempt 2, 18.2 minutes). At its last line, 201 of
234 (85%), it had taken 20.17 minutes where the passes had taken 14.54 and
14.42, and from 40% on it ran 1.38 to 1.44 times as long as either. Three
projections:

- **Multiplicative**, the passes' end over their time at line 201 applied to
  the killed run: 25.4 and 25.3, so **25.3 minutes**. This is the figure the
  rule uses.
- **Additive**, the passes' last 3.7 minutes added without the slowdown: 23.9
  and 23.8.
- **The highest ratio** carried to the end: 26.1.

So the killed step would have taken about 25.3 minutes, between 23.8 and 26.1.
The uncertainty is in the last 15%, which it never ran. Every value in that
band gives the same limit below: × 1.3 rounded up to 5 minutes gives 35 for
anything from about 23.1 to 26.9 minutes (above 30 / 1.3 = 23.08, up to 35 /
1.3 = 26.92). The band's low end, 23.8, is 0.7 minutes above that edge; below
it the rule gives 30, as it does for the slowest green step, 19.5, alone.

The killed run was a slow runner, not a slow version: it ran 1.39 times as
long as the median of the same commit and leg. Over groups of two or more green
steps of one commit and leg, green steps reached 1.30 times their group's
median on Ubuntu and 1.26 on Windows. The slowest green Ubuntu step, 19.5, was
already 1.19 times the median of its own commit and leg (`c02517ca`, py3.12:
16.0, 16.4 and 19.5), so it is not multiplied again: on a runner like the
killed one, that commit and leg would take about 22.8 minutes (16.4 × 1.39).

## Decision

1. **The pytest step's limit is the slowest step × 1.3, rounded up to the next
   5 minutes, per OS.** The slowest step is the larger of the slowest green
   step and the projected full time of any step that the old limit killed with
   no test failed or stuck. Ubuntu: the larger of 19.5 and 25.3 is 25.3, and
   25.3 × 1.3 = 32.9 → **35**. Windows: no step ended on its limit, and 40.0 ×
   1.3 = 52.0 → **55**.
2. **The job's limit is the step's limit + 5 minutes: 40 and 60.** The rest of
   a green job took at most 3.2 minutes, so a hang inside pytest still meets
   the step's limit first. The step's limit names the step that hung. A stall
   before pytest (sync, pyright) still meets the job's limit.
3. **One limit per OS, not per Python version.** The slowest green leg differs
   from the fastest by 1.4 minutes (7%) on Ubuntu and 3.45 minutes (9%) on
   Windows. Both are well inside the 30% margin. The killed py3.12 step does not
   make py3.12 slower: the same commit's py3.12 leg passed in 18.4 and 18.2
   minutes. The 3.14 legs have only 15 green steps each, but they are not the
   slowest on either OS.
4. **The per-test `faulthandler_timeout = 240` and the 180 s exit watchdog stay
   as they are.** They catch a single stuck test or a stranded thread in
   minutes. The step and job limits are the backstop for what they do not
   cover.
5. **The numbers move together.** `tests/test_ci_hang_guard_429.py` pins the two
   measured green maxima, the projected killed step, the rest-of-job maximum
   and the factor as constants, and checks that the limits are this rule
   applied to them. It also checks, per OS, the sentences that state the
   maxima, the projection, the rule, the step limits and the job limits in
   `ci.yml`'s comment, in `docs/development/ci-hang-diagnostics.md` (with the
   number of jobs) and in the CHANGELOG entries for #429 (the limits) and #482
   (the limits and the measurement). A swapped OS or another factor in place
   of the right sentence fails it. Those checks are for presence; on top of
   them, #429's old limits (job 25/50, step 20/45) stated as current, in
   #429's own wording or in the pinned one, fail it in `ci.yml`'s comment, the
   diagnostics page and #429's CHANGELOG entry even next to the new sentences.
   Other prose (this ADR, its index entry, the step's comment) is not pinned.
   The test also fails if the job or its pytest step has `continue-on-error`,
   which would let a red or hung leg through. A new measurement updates the
   constants, the limits and those sentences in one change and adds its own
   CHANGELOG entry. Measure again when the slowest step, green or projected,
   comes within 15% of its limit (29.75 minutes on Ubuntu, 46.75 on Windows),
   or when a step ends on its limit again.

### Why × 1.3 rounded up, and not p90 plus a fixed amount

- **The two errors cost different amounts.** A limit that is too tight fails a
  green required check. Someone then re-runs 20 to 40 minutes of CI and the
  merge waits. That has already happened once, and the suite keeps growing. A
  limit that is too loose lets a hang outside a test run longer before it
  fails: 15 minutes longer than #429's limits on Ubuntu and 10 on Windows. That
  is still nowhere near the six hours #429 ended.
- **The slowest step has to include the one that was cut off.** #429's limit
  capped the green Ubuntu maximum at 20 minutes, so the slowest green step
  alone understated the runner noise. The killed run was 1.39 times its
  commit's passes, more than any green step (1.30 on Ubuntu, 1.26 on Windows).
  Counting it at its projected time puts that noise into the Ubuntu basis, and
  the × 1.3 factor then leaves room for growth on top. No Windows step ended on
  its limit, so the Windows basis holds no runner that slow (see "Known
  limits"). A limit set from p90 (18.0 on Ubuntu) would sit inside the tail
  that this run fell into.
- **Rounding up to 5 minutes** keeps the limits readable and stable across
  small re-measurements: any slowest step from about 23.1 to 26.9 minutes
  gives 35. The projection band, 23.8 to 26.1, is inside that range, though
  its low end is only 0.7 minutes above the edge.

### Known limits

- **Windows has less room than Ubuntu for a runner as slow as the killed
  one.** That runner was 1.39 times the median of its commit and leg, and it
  is in the Ubuntu basis only. A 1.39× runner on today's slowest Windows
  commit and leg with two or more green steps (`c02517ca`, py3.12, median
  37.9) would take about 52.7 of the 55 minutes, 2.3 to spare. On Ubuntu's
  slowest such group (`c862e080`, py3.12, median 18.4) it would take about
  25.5 of 35. At the fitted +0.61 min/day the Windows room is gone within
  days. The limit does not cover that case; Decision 5's trigger does: such a
  run would itself be past the 46.75 minutes that call for a new measurement,
  and one that ran into 55 would be a step ending on its limit. Making the
  suite faster is the lasting answer.

### Not this change

- **Making the suite faster.** The lasting fix is to run tests in parallel
  (pi's vitest runs test files in parallel by default; pytest needs
  `pytest-xdist`) and to fix the slowest tests. At the fitted slopes, counting
  from the end of the window (2026-10-19), the slowest step reaches the new
  limits in about 36 days on Ubuntu (around 2026-11-24) and 25 days on Windows
  (around 2026-11-13). It reaches the re-measure threshold of Decision 5
  sooner: in about 16.5 days on Ubuntu (around 2026-11-05) and 11 days on
  Windows (around 2026-10-30). These dates are approximate. The slopes fit all
  green steps, not the slowest one (r = 0.45 and 0.48), and the Ubuntu basis
  is a projection. The speed-up is a follow-up issue, filed when this merges.
  A map of the slowest files per OS, from the progress-line timestamps of run
  39287804079, is in the kit.
- **pi** (`fdce48eb4`) runs its main CI (`.github/workflows/ci.yml`, `npm
  test`) on `ubuntu-latest` only. Its `env.yml`, path-filtered to the
  execution-environment packages, runs vitest, the durable env tests and
  `cargo test` on `ubuntu-latest`, `ubuntu-24.04-arm`, `macos-latest`,
  `windows-latest` and `windows-11-arm`, and `build-binaries.yml` smoke-tests
  the release binaries on Ubuntu, macOS and Windows. None of these test
  workflows sets `timeout-minutes`; the only one in pi's workflows is the
  issue-analysis job's 45 minutes. Per test, only the agent, ai and
  coding-agent packages set vitest's `testTimeout: 30000`. pi's other vitest
  packages keep vitest's 5 s default, and tui and the root scripts run under
  `node --test` with no per-test timeout. aelix keeps #429's job and step
  bounds as well as the per-test watchdog, and diverges on purpose (ADR-0235).
  #428 was a hang outside any single test: run 37806642579's Ubuntu py3.13 step
  ran 5 h 50 min before it was cancelled.

## Consequences

- A green run's pytest step now has 9.7 minutes (Ubuntu) and 15.0 minutes
  (Windows) to spare over the slowest step measured, green or projected
  (Ubuntu: 8.9 to 11.2 over the projection band, and 15.5 over the slowest
  green step). #429's limits left 0.5 and 5.0 over the slowest green step, and
  none over the killed one.
- A hang that the watchdogs miss now costs at most 40 or 60 minutes of runner
  time, against 25 or 50.
- The limits will need to move again unless the suite gets faster. The
  re-measure threshold in Decision 5 and the speed-up follow-up are how that is
  caught before a green run fails again.

## Evidence

Kit: `.omc/probes/482-live/impl/` (round 1) and `.omc/probes/482-live/r2/`
(round 2). `impl/` holds `measure.py` (the `gh` reads), `raw-150.json` (one
row per test job and attempt), `summarise.py` with `summary-all.txt` and
`summary-since-1016.txt`, `probe_limits.py` with `probe-before.txt` and
`probe-after.txt`, `segments.py` and the `slowest-*.txt` files (the
slowest-files map), and `run38610624957-att1-failed.log`. `r2/` holds the
re-pull (`measure2.py`, `raw-r2.json`), `summarise_r2.py` with
`summary-r2.txt` (the maxima, the steps that ended on their limit, the rule,
the slopes and dates, the per-day medians, the runner noise),
`project_killed.py` with `project_killed.out` and the three job logs it reads,
`probe_r2.py` (RED for round 1's 30/55: Ubuntu step 30 < 35; GREEN for
35/55), `pi_ci_r2.py` with `pi_ci_r2.out`, and `sabotage_r2.py` with its
results. The last line of `r2/project_killed.out` gives the range that rounds
to 35 as (19.23, 26.92], which is wrong; `.omc/probes/482-live/narrow/` (the
narrowed finish) holds `project_killed.py` and its output with the range
(23.08, 26.92], and `windows_room.py` with `windows_room.out` (the 22.8, the
room per OS for a 1.39× runner, and the rounding at each edge).
