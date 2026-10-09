# CI hang diagnostics

Status: Accepted

The six lint/test matrix jobs have a 25 minute limit on Ubuntu and a 50 minute
limit on Windows. Their pytest steps have 20 and 45 minute limits respectively
(#429). These limits leave headroom above the measured green runs and bound
failures outside an individual test, including collection and interpreter exit.

Pytest 9.0 or newer is required by the development dependency group. A test
whose setup, call or teardown takes 240 seconds dumps every thread's stack and
terminates the process with a nonzero exit. A separate 180 second watchdog is
armed after pytest unconfigures, so a stranded executor worker cannot leave a
green summary followed by an indefinitely hanging interpreter.

Read the first timeout traceback to identify the blocked worker and the main
thread's wait. A printed passing summary is insufficient when the process later
fails to exit. Job/step timeout can bound hangs outside the watchdog's coverage,
but it cannot promise a Python traceback in those phases.

For an intentional longer debugging session, `-o faulthandler_timeout=0`
disables the per-test timer; `-o faulthandler_exit_on_timeout=false` disables
forced exit and the final interpreter watchdog. CI uses the defaults.

This guard bounds and diagnoses hangs. The completion double-cancellation defect
that motivated it is tracked separately in #428.
