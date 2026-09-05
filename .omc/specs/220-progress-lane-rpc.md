# #220 — lane `rpc` progress (2026-09-05)

Worktree `/tmp/wt-220`, branch `fix/220-agents-process-tree`, base `main` `6586f8a`.
Spec: `.omc/specs/220-design-2026-09-05.md` revision 2, §A.4 / §B.4 / §B.5 / §C.4 / §D.5.
Host: darwin 25.4.0, `/tmp/wt-220/.venv/bin/python` (3.12.13).

## Files changed

| file | what |
| --- | --- |
| `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py` | §B.4: the read-only `tree` property beside `process` (+18 lines) |
| `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py` | §B.5: `row.tree = client.tree` / `row.tree_owned = False` beside `row.proc`; `_reap` passes `tree=row.tree`; `_eager_abort` passes `tree=row.tree`; docstrings on `_reap` and `_eager_abort` |
| `tests/agents_ext/test_rpc_channel.py` | §D.5's four tests + two additions (see Deviations) |

Nothing else was touched. `_shutdown`'s ordering is unchanged, as §B.5 requires. No signature changed.

## §B.4 — `RpcClient.tree`

Inserted after the `process` property (new `rpc_client.py:348-364`). The spec gives the docstring
verbatim with two citations, `:495-503` and `:619-624`; those are PRE-insertion numbers and the
property shifts everything below it by +18, so both were re-derived against the current tree and
written as the shifted equivalents of the SAME blocks:

- `:495-503` → **`:513-521`** — verified: 513 is `if self._exited.is_set():`, 521 is `self._tree = None`
  (`start`'s premature-exit path).
- `:619-624` → **`:637-642`** — verified: 642 is `self._tree = None` in `stop()`, 641 `tree.close()`,
  640 `if tree is not None:`, 637-639 the tail of the "A RELEASE, not a kill" comment. (The spec's
  619 already started mid-comment; the translation preserves the same 6-line window.)

`ProcessTree` is imported at runtime here already (`rpc_client.py:112-117`), so no `TYPE_CHECKING`
dance was needed and the annotation is real.

## §B.5 — `rpc_channel.py`

- `run()` at the `row.proc = client.process` statement group: `row.tree = client.tree`,
  `row.tree_owned = False`, with the comment giving the two WHYs the spec names — the client owns the
  tree (`rpc_client.py:637-642` closes and nulls it), and the ROW is the only carrier the three kill
  doors share.
- `_reap`: `tree=row.tree` in the `reap(...)` it builds, plus a docstring paragraph on why the walk
  cannot carry Windows and why the row is re-read rather than captured.
- `_eager_abort`: `kill_tree(proc, descendant_pids(proc.pid), tree=row.tree)`, plus §A.7's stated cost
  (synchronous `taskkill` on the loop thread, the `rpc_client.py:610` precedent, and why both proposed
  remedies are worse). Wording deliberately kept parallel to the print_channel lane's sibling
  docstring at `print_channel.py:1369-1382`, which landed concurrently.

## §D.5 — tests

All six live in a new section `# === #220 — the tree the CLIENT attached is the tree the reaper
reaches ===`, inserted before the parity test.

Shared machinery, all new in this file:

- `_TreeSpy` — wraps the BOUND `soft_kill` / `hard_kill` / `close` on the tree INSTANCE and calls
  through (tests/T13, rpc/RPC-8). `close` records its CALLER's module via `sys._getframe(1)`, because
  §A.6's claim is about the OWNER, not about the fact of closing.
- `_spy_the_clients_tree(monkeypatch)` — subclasses `RpcClient` and patches
  `aelix_agents.rpc_channel.RpcClient`; hands back the real client and the spy.
- `_record_os_kill(monkeypatch)` — records `(caller module, pid, sig)` and DELEGATES.
- `_wait_for(probe, what)` — polling helper.

**Platform injection: `reaper._is_win32`, not `reaper.sys.platform`.** Finding R1 is binding here in a
way it is not for §D.1: every test in this file needs a REAL child inside a REAL `ProcessTree`, and a
global `sys.platform == "win32"` makes `containment_spawn_kwargs()` return `creationflags=` (POSIX
`subprocess` raises `ValueError`) and `ProcessTree.attach` construct `_KernelApi()` →
`AttributeError: module 'ctypes' has no attribute 'WinDLL'`. `_is_win32` is a function precisely so the
read can be lent late; on the windows leg patching it to `True` is a no-op, so these run for real there.
The kills are NOT stubbed — `hard_kill` really `killpg(pgid, SIGKILL)`s on POSIX — except in test 3,
which needs a pending reaper and says so.

The four named tests:

1. `test_the_rpc_row_carries_the_clients_tree` — during a live turn: `row.tree is client.tree`,
   `client.tree is not None`, `row.tree_owned is False`, `tree.closed is False`.
2. `test_stop_reaches_the_clients_tree_on_win32` — injected win32, real runtime, `runtime.stop(id)`:
   the same tree instance recorded exactly one `hard_kill`, zero `soft_kill` (Q4), and NO `os.kill`
   from `aelix_agents.reaper`; envelope `aborted`, registry empty.
3. `test_a_pre_created_reaper_task_still_kills_through_the_tree` — deaf tree so `abort_child`'s reaper
   stays pending; `channel._reap` then REUSES it (`row.reaper_task is first`), one `hard_kill` total,
   no pid signal from the reaper. Cleanup calls the real `ProcessTree.hard_kill(tree)`.
4. `test_the_channel_never_closes_the_clients_tree` — full clean run: `closed_by ==
   ["aelix_coding_agent.rpc.rpc_client"]`, no kills at all, `clients[0].tree is None`, and
   `row.tree.closed is True` — the row keeps a CLOSED tree and what makes that safe is
   `reaper._usable`, not an empty row.

## Deviations from the spec

1. **Two tests ADDED beyond §D.5's four** (not substitutions; all four are present and unchanged in
   intent). The lane rule "for every product line you add, know which test goes red without it" is not
   satisfiable with only the four: `_reap`'s own `tree=row.tree` and `_eager_abort`'s are both product
   lines #220 adds and all four named tests reach the tree through `abort_child`'s reaper instead.
   - `test_the_reaper_the_channel_builds_carries_the_rows_tree` — a child that answers then ignores
     stdin EOF is the one bed where `_shutdown`'s `wait_for_exit` returns `None` and `_reap` builds the
     reaper itself. Asserts `soft == [True]`, `hard == []`, no reaper `os.kill`.
   - `test_a_cancelled_delegation_kills_through_the_tree` — the `_eager_abort` door. `>= 1` on the
     `hard_kill` count, per §A.8's count scope (posix/P7).
2. **The `tree` property's two citations were translated, not copied** — see §B.4 above. Copying the
   spec's pre-insertion numbers would have cited the wrong lines in the shipped file.
3. **"the docstring at the site that today cites print_channel's `_eager_abort` gets the #220
   sentence"** — the only site in `rpc_channel.py` that cites print_channel's `_eager_abort` is the
   COMMENT at `:387` (`print_channel.py:1106-1113`), which is about progress-tap terminal states, not
   about killing; a #220 sentence there would be off-topic. I read the instruction as naming the
   product-change site and put the #220 sentences on `_eager_abort`'s and `_reap`'s own docstrings.
   Flagging in case the intent was the other reading.

## Needs another lane / the integrator

- **Stale citation in my file, caused by the print_channel lane.** `rpc_channel.py:387` cites
  `print_channel.py:1106-1113` for "PrintChannel … cancelling its pumps next to its own
  `_eager_abort`". That block is now at **`print_channel.py:1224-1231`**; `1106-1113` is today the
  `run()`-finally tree-close block. The anchor TEXT is unchanged, only shifted, so §F step 2's `--fix`
  should relocate it automatically — but it needs to be looked at, not assumed.
- The `tree` property added 18 lines to `rpc_client.py`, so every locked anchor below `:347` in that
  file has shifted by +18. Same `--fix` step.

## Commands run (tail of each)

```
$ .venv/bin/ruff check tests/agents_ext/test_rpc_channel.py \
    packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py \
    packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py
All checks passed!
```
(`ruff format --check` deliberately NOT run: all three files pre-exist, ADR-0152.)

```
$ /Users/handochan/dev/aelix-ai/.venv/bin/pyright --pythonplatform Windows \
    packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py \
    packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py
0 errors, 0 warnings, 0 informations
```

```
$ .venv/bin/python scripts/check_types.py
OK  gate: 277 files analysed (floor 220)
OK  gate: 0 errors across 277 files
OK  spike: all 3 inverse assertions still error (narrowing alive)
PASS: type gate clean, narrowing spike still inverted
```

```
$ .venv/bin/python -m pytest -p no:cacheprovider -q tests/agents_ext/test_rpc_channel.py \
    tests/rpc tests/agents/test_rpc_sprint_pins.py
266 passed, 2 skipped, 1 warning in 36.56s
  (warning: real rpc child orderly shutdown on darwin: 0.084s, returncode=0)
```

```
$ for i in 1 2 3; do .venv/bin/python -m pytest -p no:cacheprovider -q \
      tests/agents_ext/test_rpc_channel.py; done
37 passed, 1 skipped in 26.25s
37 passed, 1 skipped in 26.29s
37 passed, 1 skipped in 26.30s
$ ps -ax -o pid,ppid,command | grep -E "aelix_coding_agent|python .*-c " | grep -v grep
   (no output — no strays)
```

## Mutation checks — every product line this lane adds, deleted for real

Each mutation was applied to the file, the six tests run, and the file restored byte-identically from
a snapshot (`diff` confirmed after each).

| # | deletion | result |
| --- | --- | --- |
| A | `row.tree = client.tree` in `run()` (the line the lane brief names) | **6 failed**, incl. `test_stop_reaches_the_clients_tree_on_win32`. Tail: `AssertionError: the cancellation never reached the tree / assert 0 >= 1` |
| B | `tree=row.tree` in `_reap` | **1 failed, 5 passed** — `test_the_reaper_the_channel_builds_carries_the_rows_tree`: `AssertionError: leg 1 did not go through the tree: [] / assert [] == [True]` |
| C | `tree=row.tree` in `_eager_abort` | **1 failed, 5 passed** — `test_a_cancelled_delegation_kills_through_the_tree`: `assert 0 >= 1` |
| D | `RpcClient.tree` returns `None` instead of `self._tree` | **6 failed** — `AssertionError: the tree never appeared within 20s` |

B and C each isolate exactly one test, which is the discrimination the four named tests alone did not
have; A and D are broad because everything downstream depends on the row carrying the tree.

## Not verified by this lane

- **Everything win32.** `soft_kill` is `CTRL_BREAK_EVENT` and `hard_kill` is `taskkill /T /F` +
  `TerminateJobObject` only on a real Windows host; on this box the injected-win32 tests exercise the
  reaper's DISPATCH with POSIX kill primitives underneath. §H.7's windows-latest leg is the
  measurement, including the rpc site's exit-status move 15 → 1/149 that §1 predicts.
- **The `taskkill` wall time** §A.7 accepts as a bounded stall on the loop thread. Not measurable here.
- **The live rpc driver script** (§H.6(C)) — `RpcChannel` is not wired into the product
  (`runtime.py:369-372`), so rule 10's live check needs a direct driver. Not run by this lane; the
  scripted-child tests above are real processes but not a real model.
- **`_shutdown`'s missing `try/finally`** (rpc/RPC-5) — a cancellation before `client.stop()` still
  leaves the tree to `weakref.finalize`. Out of scope (§I), untouched, and not covered by any test here.
- **Citations** — §F's five-step procedure was not run (it is an integration step and other lanes were
  editing the same tree). See "Needs another lane" above.
