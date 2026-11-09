# CI hang diagnostics

Status: Accepted

The eight lint/test matrix jobs have two limits. The job limit is 40 min on
Ubuntu and 60 min on Windows, and the pytest step limit is 35 min on Ubuntu and
55 min on Windows. These limits bound failures outside an individual test,
including collection and interpreter exit (#429).

The limits come from measured runs (#482, ADR-0274). Over 129 `ci.yml` runs
from 2026-10-08 to 2026-10-19 (627 green pytest steps), the slowest green pytest
step took 19.5 min on Ubuntu and 40.0 min on Windows. #429's first step limits,
20 and 45 minutes, capped what a green Ubuntu step could show: run 38610624957
was killed on the 20 minute limit at 85%, with no test failed or stuck, on a
runner about 1.4 times slower than the same commit's two passes. Projected from
its progress lines it would have taken about 25.3 min on Ubuntu (23.8 to 26.1 by
other projections). No Windows step ended on its limit. The step limit is the
slowest step, green or projected, x 1.3, rounded up to 5 min. The rest of a
green job took at most 3.2 min, so the job limit is 5 minutes more and a hang
inside pytest is reported by its step, not by the job. The Python versions
differ by less than this margin, so each OS has one limit. When the slowest
step, green or projected, comes within 15% of its limit (29.75 min on Ubuntu,
46.75 min on Windows), or a step ends on its limit again, measure again and move
the limits, `tests/test_ci_hang_guard_429.py`, the `ci.yml` comment and this
page together.

Pytest 9.0 or newer is required by the development dependency group. A test
whose setup, call or teardown takes 240 seconds dumps every thread's stack and
terminates the process with a nonzero exit. A separate 180 second watchdog is
armed after pytest unconfigures, so a stranded executor worker cannot leave a
green summary followed by an indefinitely hanging interpreter.

Read the first timeout traceback to identify the blocked worker and the main
thread's wait. A printed passing summary is insufficient when the process later
fails to exit. Job/step timeout can bound hangs outside the watchdog's coverage,
but it cannot promise a Python traceback in those phases.

The dump names the test that hung (#689). `tests/conftest.py` keeps one idle
thread whose top frame reads
`File "<pytest: the test running now (#689)>", line N in <nodeid>` while a test
runs. After a test ends it reads
`no test running; the last one was <nodeid>`, which is also what the final
watchdog's dump shows. Search the dump for `the test running now`. Do not
count progress dots: the dots of an unfinished progress line are printed after
the dump, and #689's second count missed 57 of them. When the main thread is
idle in the event loop's `select` it has no test frame, and this line is the
only place the test's name appears. The thread takes each new label
asynchronously, so a hang that holds the GIL from the very start of a test can
leave an earlier label in the dump, even the previous test's nodeid: with a
backtracking regex, which never releases the GIL, run first in a test's setup,
3 of 30 child dumps on macOS (ten each on py3.11, 3.12 and 3.14) named the
previous test. Run as the first line of the test body, it did so less often:
0 of 30 in that run, and 1 of 90 in a later rerun on macOS (1 of 30 at a load
average of 23-35, 0 of 60 at 7.7-9.2). The thread writes nothing, so a passing
`-q` log is unchanged.
`tests/test_ci_hang_guard_429.py` checks that the line is its thread's top
frame for a hung call, an idle event loop and a hung teardown, and after the
summary.

For an intentional longer debugging session, `-o faulthandler_timeout=0`
disables the per-test timer; `-o faulthandler_exit_on_timeout=false` disables
forced exit and the final interpreter watchdog. CI uses the defaults.

This guard bounds and diagnoses hangs. The completion double-cancellation defect
that motivated it is tracked separately in #428.
