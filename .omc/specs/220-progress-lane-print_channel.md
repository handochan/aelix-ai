# #220 — lane report: `print_channel` (spec §A.1, §A.5–A.9, §B.2, §D.2, §D.4)

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Host: darwin 25.4.0, CPython 3.12.13, `/tmp/wt-220/.venv`. Wave 1 (`reaper.py`, `_process_tree.py`,
`print_mode.py`) was already landed when this lane started and was read as it stands, not as revision 2
describes it.

## Files changed

- `packages/aelix-coding-agent/src/aelix_agents/print_channel.py` (edited)
- `tests/agents_ext/test_print_channel_spawn.py` (edited)
- `tests/print_mode_child.py` (**NEW**)
- `tests/cli/test_print_mode_json_contract.py` (edited — import switch only)

Nothing else was touched. `reaper.py` was mutated and restored byte-for-byte for one mutation check
(§"Mutation checks" below); `git diff --stat` on it is unchanged from wave 1 (168 insertions, 18
deletions) after the restore.

## What went in

### `print_channel.py`

1. **Imports / logger.** `import logging`, `from aelix_ai.utils._process_tree import ProcessTree,
   _retained_handle, containment_spawn_kwargs`, and a module `logger = logging.getLogger(__name__)`
   with a docstring saying why exactly one thing is logged from this module (a degraded containment is
   the only failure with no envelope of its own — the delegation itself succeeds). `aelix_agents` had
   NO logger before; the shape is the one `extensions/subprocess_hooks.py` and the `tui/` modules use.
   `ProcessTree` is imported at RUNTIME, not under `TYPE_CHECKING`, because §D.2's tests patch
   `print_channel.ProcessTree.attach`.
2. **Row (§B.2).** `RunningChild.tree: ProcessTree | None = None` and `tree_owned: bool = False`, with
   the spec's docstrings.
3. **Spawn (§A.1, §B.2).** `start_new_session=True` → `**containment_spawn_kwargs(new_session=True)`.
   The comment block is rewritten: it now names both platforms' forms, says the win32 half is TWO fixes
   (the group is the address `soft_kill`'s console event needs, AND it disables console Ctrl+C for the
   child and its descendants), and marks the second half documentation-derived rather than measured.
   `print_channel.py:970`'s locked anchor text — `` # ``None`` on Windows, where subprocess REJECTS
   the `` — is **byte-identical** (verified by grep after the edit; it moved to `:1018`). The two lines
   that followed it were false after the change ("`start_new_session` above is not so loud") and were
   rewritten.
4. **Attach (§B.2, R3).** Its own `try/except Exception` OUTSIDE the spawn's, between `row.proc = proc`
   and `row.state = "running"`, degrading to `row.tree = None` + `logger.warning`. The comment records
   why it is not inside the spawn's `except` (an error envelope for a running child that never enters
   `_stream_and_reap`, gets no reaper, and whose row `runtime.py` then pops) and why it must precede the
   `if row.stopped:` re-read (`abort_child` reads `child.tree`).
5. **`run()`'s `finally` (§A.6 rule 2).** Verbatim, including nulling `row.tree` in the same statement
   and the `row.tree is None` / `tree_owned` guards.
6. **Call sites.** `_reap` → `tree=row.tree`; `_eager_abort` → `kill_tree(..., tree=row.tree)`;
   `abort_child` → `reap(..., tree=child.tree)`; `_drain_after_exit(proc, pumps, tree)` with the caller
   passing `row.tree`. `_eager_abort`'s docstring states the win32 loop-thread `taskkill` cost (§A.7)
   and why both proposed remedies are worse; `_drain_after_exit`'s states the two bounds (§A.5) —
   `TerminateJobObject` carries it because `/T` follows live parent links and the root is dead, and it
   is not a stale-pid kill because the branch's own precondition is that a descendant still holds the
   pipes.

### `tests/print_mode_child.py` (NEW)

`_CHILD` lifted from `tests/cli/test_print_mode_json_contract.py` and extended. The default path is
byte-for-byte the old behaviour — verified mechanically, not by eye:

```
$ .venv/bin/python -  # difflib against the _CHILD literal parsed out of the contract test on HEAD
--- old
+++ new
-"""Driven as a REAL child process by tests/cli/test_print_mode_json_contract.py."""
+"""Driven as a REAL child process by tests/print_mode_child.py's consumers."""
 import asyncio
+import os
+WEDGED = "--wedge" in sys.argv
+READY = (sys.argv[sys.argv.index("--ready") + 1] if "--ready" in sys.argv else None)
+        if WEDGED:  … os.close(os.open(READY, …)) … while True: await asyncio.sleep(0.05)
```

Three additions, all behind `--wedge` / `--ready`, none on the wire for the contract consumer. The
breadcrumb is written from INSIDE the stream because that is the only line that attests to the whole
precondition at once: `run_print_mode` installs its handlers before it prompts, so a stream that has
begun is a child whose handler is armed and whose turn is in flight.

### `tests/cli/test_print_mode_json_contract.py`

Import switch only (`from tests.print_mode_child import CHILD_SOURCE as _CHILD`), the `_CHILD` literal
and the now-unused `import textwrap` deleted, and `ruff check --select I --fix` for the one blank line
isort wanted. No assertion changed. 10 passed.

### `tests/agents_ext/test_print_channel_spawn.py`

| what | subject |
| --- | --- |
| `escalation_unreachable` / `escalation_reachable` / the inline `if not escalation_unreachable:` | **all three retired together**, replaced by a comment block saying why the premise stopped being true and why retiring only the marker would have left the win32 exit-code assertion silently skipped |
| `sigterm_ignoring_fixture_buildable` | **STAYS**, with the cross-reference sentence: #220 retired the sibling and deliberately did not retire this one, because no change to the win32 kill legs can conjure a `/bin/sh` |
| `_sigterm_ignoring_stub(marker)` | the spec's exact body: POSIX `SIG_IGN`, win32 a real `SIGBREAK` handler, 0.05 s loop, file breadcrumb written from the LOOP on a flag the handler sets |
| `_nonsetsid_grandchild_stub` + `_GRANDCHILD_BODY` | the `subprocess.run`-shaped descendant, with its own `SIGBREAK` handler and 0.05 s steps for the win32 arm |
| `_wait_for_tree(row)`, `_spy_hard_kill(tree)` | the bound-method-on-the-instance recipe (T13), with the reason rebinding `row.tree` is a silent miss |
| `_stub_channel(..., env=)` | new seam, for the one child that imports aelix |
| `test_the_spawn_asks_for_containment_kwargs_per_platform` | injected win32 + linux, TWO fakes (non-delegating `create_subprocess_exec` spy + `ProcessTree.attach` recorder), literals asserted |
| `test_the_spawn_attaches_the_tree_before_the_first_await` | REAL platform, delegating spy, asserts `row.proc` published / `state == "starting"` / `reaper_task is None` read from INSIDE the attach |
| `test_the_print_site_asks_for_kill_on_close` | the site-policy pin (#202 MUT-6) |
| `test_the_child_is_contained` | POSIX-wide (`contained is True`, `getpgid(pid) == pid`, `job is None`) AND the win32 `IsProcessInJob` arm, no skip |
| `test_the_soft_signal_is_delivered_and_survived` | the breadcrumb, asserted here and nowhere else |
| `test_reap_still_escalates_after_the_signal_change`, `test_sigterm_then_sigkill` | instance `hard_kill` spy (exactly once) as the load-bearing half, platform-keyed exit code as corroboration, stderr tail + `elapsed` in every message |
| `test_a_non_setsid_grandchild_dies_on_the_escalation` | Q1's regression guard, POSIX-wide + win32 arm; `test_bash_grandchild_killed_on_sigkill_leg` left `@linux_only` and unchanged |
| `test_the_drain_after_exit_leg_is_the_job_on_win32_only` | injected both arms; the POSIX arm is a NEGATIVE (`hard_kill` NOT called) — the P1 guard |
| `test_the_tree_is_closed_after_the_reapers_escalation_never_before` | order + `tree.closed` read from inside `hard_kill` |
| `test_the_tree_is_closed_when_a_run_returns_with_no_reaper` | the `reaper_task is None` arm (R5) |
| `test_a_successful_delegation_closes_its_tree_and_signals_nothing_here` | §A.9's consequence, and its POSIX half |
| `test_a_real_print_child_reports_the_signal_exit_code` | §D.4 |

## Commands run, with output tails

```
$ .venv/bin/ruff check packages/aelix-coding-agent/src/aelix_agents/print_channel.py \
    tests/agents_ext/test_print_channel_spawn.py tests/print_mode_child.py \
    tests/cli/test_print_mode_json_contract.py
All checks passed!

$ .venv/bin/ruff format --check tests/print_mode_child.py      # NEW file only (ADR-0152)
1 file already formatted

$ /Users/handochan/dev/aelix-ai/.venv/bin/pyright --pythonplatform Windows \
    packages/aelix-coding-agent/src/aelix_agents/print_channel.py
0 errors, 0 warnings, 0 informations

$ /Users/handochan/dev/aelix-ai/.venv/bin/pyright packages/.../print_channel.py     # Linux default
0 errors, 0 warnings, 0 informations

$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/agents_ext tests/agents \
    tests/cli/test_print_mode_json_contract.py
1715 passed, 11 skipped, 2 warnings in 74.20s (0:01:14)
  UserWarning: real print child soft-kill on darwin: 0.023s, returncode=143
  UserWarning: real rpc child orderly shutdown on darwin: 0.082s, returncode=0

$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/cli/test_print_mode_json_contract.py
10 passed in 1.81s
```

Stability of the new real-process cases, 5 consecutive runs of
`-k "real_print_child or non_setsid or soft_signal_is_delivered or closed_after_the_reapers or
child_is_contained or escalat or sigterm_then_sigkill"`:

```
7 passed, 1 skipped, 85 deselected, 1 warning in 20.39s
7 passed, 1 skipped, 85 deselected, 1 warning in 20.40s
7 passed, 1 skipped, 85 deselected, 1 warning in 20.43s
7 passed, 1 skipped, 85 deselected, 1 warning in 20.40s
7 passed, 1 skipped, 85 deselected, 1 warning in 20.41s
```

0/5 flakes. `ps -Ao pid,ppid,pgid,command | grep -E "sleep 120|print_child"` after the suite: no strays.

Timings of the new real-process tests (`--durations`, this box):

| test | wall |
| --- | --- |
| `test_a_real_print_child_reports_the_signal_exit_code` | 8.04 s (8 s of it is the delegation deadline; the soft kill itself was **0.023–0.038 s**, returncode **143**, over 8 runs) |
| `test_the_soft_signal_is_delivered_and_survived` | 4.01 s |
| `test_the_tree_is_closed_after_the_reapers_escalation_never_before` | 3.01 s |
| `test_a_non_setsid_grandchild_dies_on_the_escalation` | 2.02 s |
| `test_sigterm_then_sigkill` | 2.01 s |

Measured order inside the close-ordering test (temporary `warnings.warn`, removed again):

```
UserWarning: ORDER=['hard_kill', 'close'] closed_at_hard_kill=[False]
```

— i.e. exactly §D.2's expectation on Darwin.

## Mutation checks actually performed

| mutation | expected red | measured |
| --- | --- | --- |
| delete `kill_on_close=True` from the spawn's `ProcessTree.attach` | site pin | `test_the_print_site_asks_for_kill_on_close` **FAILED** (`KeyError: 'kill_on_close'`) and `test_a_successful_delegation_closes_its_tree_and_signals_nothing_here` **FAILED**. 2 failed. |
| delete the direct `tree.close()` in `run()`'s `finally` (the `task is None or task.done()` arm) | close-on-normal-path | `test_the_tree_is_closed_when_a_run_returns_with_no_reaper` **FAILED** and `test_a_successful_delegation_closes_its_tree_and_signals_nothing_here` **FAILED**; the whole file otherwise green (81 passed). This is R5's finding, confirmed: without the new case the deletion is invisible. |
| move the close out of the done-callback into an unconditional `finally` close | ordering | `test_the_tree_is_closed_after_the_reapers_escalation_never_before` **FAILED** at `assert tree.closed is False, "the tree was disarmed mid-grace"`. |
| delete `tree=row.tree` from `_reap`'s `reap(...)` call | the whole win32/Q1 chain | 4 **FAILED**: both escalation tests (`hard_kill` spy at 0), `test_a_non_setsid_grandchild_dies_on_the_escalation` (the Darwin grandchild survives → `TimeoutError` at 94 s), and the close-ordering test. |
| delete Q1's `tree.hard_kill()` from `reap`'s escalation (**wave-1 file — mutated and restored**) | Q1 guard | the same 4 **FAILED**, including `test_a_non_setsid_grandchild_dies_on_the_escalation` timing out on Darwin. `reaper.py` restored from a byte copy taken before the mutation; `git diff --stat` back to wave 1's `168 insertions(+), 18 deletions(-)`. |

**`main`'s exit code, not re-measured here.** The task's instruction was to confirm by reading rather
than by reverting `print_mode`. `.omc/specs/220-progress-2026-09-05.md` §1 records, twice, on the real
product and in both `-p` text and `--mode json`: `SIGTERM -> exit in 0.172s, returncode=1` /
`SIGTERM -> exit in 0.139s, returncode=1`, with `SystemExit: 143` replaced by
`RuntimeError: Event loop stopped before Future completed` on stderr. So on `main`
`test_a_real_print_child_reports_the_signal_exit_code` fails twice over: `assert 1 == 143`, and again on
`"Traceback" not in tail`. That is the test's mutation property and it is **[M]** for `main` and **[M]**
for this branch (143 measured here, 8/8 runs).

## Deviations from the spec, and why

1. **`_SIGTERM_IGNORING` (the module constant) is gone; every site calls `_sigterm_ignoring_stub`.**
   The spec gives the stub as a function taking a `marker: Path`, which cannot be a module constant.
   Four of the five old uses became `_sigterm_ignoring_stub(tmp_path / "soft-signal.seen")`; the fifth
   is a `@pytest.mark.parametrize` value, which has no `tmp_path` at collection time, so its entry is
   now `("killed", None, 600)` and the test body builds the stub. No assertion in that test changed.
2. **`test_the_soft_signal_is_delivered_and_survived` asserts the marker's ABSENCE on POSIX.** The
   spec says to assert the marker in a dedicated test with a generous timeout. On POSIX the stub's
   disposition is `SIG_IGN` — the kernel declining to run any code — so the marker can never appear
   there, and asserting its presence unconditionally would be a test that only ever ran on Windows. The
   POSIX arm therefore asserts `not marker.exists()` (a guard that the stub has not quietly grown a
   handler and stopped being #207's fixture) plus `exit_code == -SIGKILL` as the evidence that the
   signal was delivered and survived. Stated in the test's docstring.
3. **`test_the_spawn_attaches_the_tree_before_the_first_await` keys `handle` by platform.** §C.1/§D.2
   say "a non-`None` handle". `_retained_handle` returns `None` on POSIX by construction (there is no
   handle to have; the pgid is the address), so on this host a non-`None` assertion is simply false. The
   real-platform test asserts `handle is None` on POSIX and `is not None` on win32; the INJECTED win32
   case asserts the exact value `0xF00D` off the stub proc's `_handle`, which is the assertion that
   actually pins "the stdlib's retained handle is forwarded rather than left to `OpenProcess`".
4. **The `kill_on_close` assertion was removed from the "before the first await" case.** Three tests
   asserting the same literal is not three tests. `..._before_the_first_await` now asserts only the
   ordering facts; `test_the_print_site_asks_for_kill_on_close` is the policy pin; the success-path case
   asserts the consequence.
5. **`_grandchild_stub` (the `setsid` one) is UNCHANGED**, per §D.2's "keep it `@linux_only` and
   unchanged". W7's "give the grandchild its own SIGBREAK handler + 0.05 s loop" is applied to
   `_nonsetsid_grandchild_stub` only — that is the stub whose test has a win32 arm.
6. **`test_the_child_is_contained` carries the win32 `IsProcessInJob` arm** (§C.1's "windows leg only"
   bullet, tests/T10). No lane in this issue was assigned it explicitly and it is the one assertion
   without which every other containment claim passes vacuously after an `_attach_win32` fallback, so it
   went in here, in the shape `tests/rpc/test_rpc_client_containment.py` already uses (both arms in one
   body, no skip). It is **UNVERIFIED on this host**.
7. **`_stub_channel` gained an `env=` seam.** §D.4 says to drive the real child through the existing
   `_stub_channel` seam, but that seam hands the child `build_child_env`, i.e. the runner's own `HOME`.
   The real `print_mode` child writes session state; finding I10 says a child interpreter is outside the
   reach of `tests/conftest.py`'s guards. So the one child that imports aelix gets `_hermetic_env`.
8. **`_drain_after_exit`'s `tree` parameter is positional and required**, matching §A.5's signature.
   No test outside this lane calls it (`grep -rn "_drain_after_exit" tests/` → only my new case).
9. **§D.2's close-ordering fixture is spelled out rather than assumed.** The spec says "cancel the run
   while the reaper is inside its grace" and expects `order == ["hard_kill", "close"]`. With any of this
   file's existing stubs that is unreachable: `_eager_abort` SIGKILLs the child on the cancel, so
   `proc.wait()` resolves, the reaper returns INSIDE its grace and never escalates — the order would be
   `["close"]` and the test would measure nothing. A new `_pipe_holding_daemon_stub` builds the window
   for real: it double-forks a stdio-inheriting holder (so `_eager_abort`'s `/proc` walk cannot name it,
   which is what would otherwise make Linux and Darwin behave differently here) that stays in the
   child's process GROUP, so `proc.wait()` does not resolve, the reaper burns its whole grace, escalates
   for real, and Q1's `killpg` is what finally releases the pipes. Measured order on Darwin:
   `['hard_kill', 'close']`, `closed_at_hard_kill=[False]` — the spec's expectation, now constructible.
   The `hard_kill` count is asserted `>= 1`, not `== 1`, per posix/P7: a cancelled run reaches
   `kill_tree` from `_eager_abort` twice as well, and on win32 both of those are `hard_kill`.

## Could not verify

- **Every win32 arm.** `test_the_child_is_contained`'s `IsProcessInJob`, the `149`/`1` exit codes, the
  `SIGBREAK` breadcrumb, the grandchild dying to the job, and `CREATE_NEW_PROCESS_GROUP` disabling
  console Ctrl+C. Injected arms (the spawn kwargs, `_drain_after_exit`) run here and prove the DISPATCH;
  the behaviour is the windows-latest leg's measurement. The `warnings.warn` in the real-child test is
  what will put the number in that leg's `-q` log.
- **`_attach_win32` falling back to `contained=False`.** Not constructible on demand; the
  `IsProcessInJob` assertion is what would catch it happening for real.
- **The assignment window itself** (a descendant created between `CreateProcess` and
  `AssignProcessToJobObject`). Its OUTCOME is asserted; the race is not reproducible.
- **`ProcessTree.attach` raising in the product.** The degrade-and-log path is written and typed but
  never executed on this host — on a real platform `attach` swallows its own failures. It is reachable
  only under a global platform patch, which §D.1/§D.2 forbid near a real spawn.

## Needs another lane / the integration pass

- **Citations.** `print_channel.py:970`'s one-line anchor is byte-identical and will relocate
  automatically; it now sits at `:1018`. `print_channel.py:967` (the `modes/print_mode.py:114-131`
  citation) and `print_channel.py:931` (a bare `try:`) are §F's known-stuck anchors and need the
  hand-re-derivation §F step 3 describes. I ran no `check_citations.py` step — §H puts it after every
  lane lands.
- **`tests/print_mode_child.py` is a new test-tree module**, so anything that enumerates `tests/*.py`
  (packaging excludes, the citation lock) should be re-checked by the integration pass.
- The ADR-0238 amendment, CHANGELOG, README and SLICE-STATUS edits §G asks for are the docs lane's.
