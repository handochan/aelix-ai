# #226 design — revision 2 resolution table

Base: `.omc/specs/226-design-2026-09-06.md` (revision 1 → **rewritten in place as revision 2**),
main `b94a6db`, worktree `/tmp/wt-226`. 24 findings across four lenses. Every row is UPHELD (change
the design), PARTIAL (change part of it; the refuted half is recorded so the next reader does not
spend a round on it), or **NO VERDICT** (the refuter lane died; applied conservatively per the
orchestrator's instruction, and marked so a later round knows it is unverified by measurement).

**Counts: UPHELD 6 · PARTIAL 11 · NO VERDICT / conservatively applied 7.**
The decision did not move: `process_group=0` stays, the `WIFSTOPPED` detector stays (§A.4).
What moved is the rationale, the reason string, the test synchronisation, the measured platform set,
and two residues nobody had written down.

---

## §A (decision) and §B (code)

| id | lens | sev | verdict | evidence in one line | disposition |
| --- | --- | --- | --- | --- | --- |
| POSIX-1 | posix-job-control | MAJOR | **UPHELD** | The 50 ms probe REAPS an exited leader: darwin `main` `claim_at_kill=ERR:1 members='… Z <defunct>'` vs branch `ESRCH members=EMPTY` (3/3), Linux `CLAIMED 'Z sh'` vs `ESRCH EMPTY`; reap at 0.055 s, `killpg` at 10.01 s | §A.6-b rewritten (two hazards, not one); §B.2 widened to the **THE PID/PGID HAZARD** paragraph; §D.3 amends ADR-0238's "The hazard this accepts"; new §G bullet. `waitid(WNOWAIT)` narrowing **declined**, with the reason written down (below) |
| POSIX-2 | posix-job-control | MAJOR | **PARTIAL** | `trap '' TTOU; stty -echo </dev/tty` exits **0** with echo left off; ignore-then-read is detected at 0.051 s but echo is already off and our `SIGKILL` prevents the restore — identical on darwin and Linux/dash | Rule narrowed to the conditional form everywhere it is stated (§D.3-3, §D.7, §A); new §G residue bullet incl. `stty sane`; §E.1 now runs **both** arms and the false parenthetical is deleted; new §C case 5 pins the boundary on its own pty; termios snapshot/restore **explicitly declined** |
| POSIX-3 | posix-job-control | MAJOR | **PARTIAL** | Dropping the kwarg altogether: backgrounded Aelix + helper **both `T`, still `T` at 20 s**, timeout never fires, `SIGCONT` does not recover it. Same run with `process_group=0`: Aelix `S`, bounded at 10.01 s | §A.1 gains leg (c) with those numbers; §D.3-5 carries the sentence-pair; §C mutation list gains `M5` **with no new test** |
| PRODUCT-2 | product-and-pi | MAJOR | **UPHELD** | The cached caller (`resolve_config_value`) has exactly two production callers, both in `auth_storage.py` — an **`auth.json`** `key`, not `models.json`; and today's `-1` renders `died with <Signals.SIGHUP: 1>` next to `(SIGTTIN)` | Reason string no longer names a config file and no longer denies the terminal; `_Failure` gains `returncode`, set **only on the stop branch** (measured `-9`); §G's "untouched" bullet amended; §E gains the cached-path live case; §C case 1 gains the `SIGKILL`/no-`SIGHUP`/no-`models.json` assertions |
| PRODUCT-3 | product-and-pi | MAJOR | **PARTIAL** | Option 2 does work (`rc 0`, typed line, 1.026 s) **and** is a sub-ms race, produces no stop to detect when the prompt is abandoned (full timeout + echo off), and stops Aelix itself in 21 ms in the TUI | §A.3 replaced by a measured rejection with four named costs; the `^C` objection **deleted as wrong**; §D.3-5 and §D.5 carry the product sentence ("Aelix never prompts from a `!command`", a stated divergence from Pi); §G gains the three-level-recipe warning |

## §C (tests) and §F (gate)

| id | lens | sev | verdict | evidence in one line | disposition |
| --- | --- | --- | --- | --- | --- |
| TESTS-1 | tests-and-ci | **BLOCKING** | **UPHELD** | §B written into the real file: host pyright **0 errors**, `--pythonplatform Windows` **4 errors**, `check_types.py` EXIT=1 — red at step 3 of 4 on the gating leg, before pytest | Three `# pyright: ignore[reportAttributeAccessIssue]` in §B.1 (one covers both names on the `_WAIT_STOP_OPTIONS` line); §F replaces bare `uv run pyright` with `check_types.py` + the Windows lens and says why the host run is structurally blind; §G risk bullet |
| TESTS-2 | tests-and-ci | MAJOR | **UPHELD** | `signal.SIGSTOP` is absent on Windows CPython (`#ifdef SIGSTOP`); the row as worded raises `AttributeError` on the gating leg | §C row 4 split on `sys.platform` so **both arms are real assertions**; the bare `getattr` one-liner rejected (passes vacuously, `None not in {}`) and `monkeypatch.delattr` rejected (the map is built at import — false failure); `M3` noted as killed by the POSIX arm only |
| TESTS-3 | tests-and-ci | MAJOR | **UPHELD** | `proc.wait()` / `communicate()` / `poll()` before the detector → `ChildProcessError` **120/120** → mutant M1 survives; and `!exit 7` reaches the probe **0 times** | §C row 3 becomes **behavioural** through `_run_shell_command` (grandchild holds the pipe, root `SystemExit(7)`, 5/5 deterministic at 0.43–0.45 s); the unit variant, if kept, carries the explicit prohibition list; `M1` line and §A.6/§D.3 gain the reachability condition |
| TESTS-4 | tests-and-ci | MAJOR | **UPHELD** | `ci.yml:43` `os: [ubuntu-latest, windows-latest]` — **no macOS leg**, and rev 1's whole POSIX evidence base was darwin while its "not measured" note named only win32 | §0 gains a CI-leg row; §H reorganised into "owner's box (#1–#21)" vs "this round"; new Linux rows #37–#40 (5 combos: py3.11/3.12 × root/non-root × aarch64/amd64); §H #9 and #21 scoped as darwin-only facts; §D.3-4/§D.6/§D.7 say "macOS **and Linux**"; §C row 1 records the no-ctty precondition |
| TESTS-5 + DOC-8 | tests-and-ci / docs-adr | MINOR | **PARTIAL** | Re-measured here: `citations.lock.json` contains **0** anchors for `_process_tree.py:NNN` or `_resolve_config.py:NNN` (only `tests/process_tree/test_run_contained_real_processes.py:461-465`) | §F drops the `--fix`/`--lock` pair and its false premise; keeps `tests/test_citation_drift.py -q` as the check, with the rule that a `--lock` after a *rewrite* enshrines an unrelocatable citation |
| TESTS-6 | tests-and-ci | MINOR | **PARTIAL** | Case 1's win32 arm as drafted asserted a fact already asserted twice elsewhere | Replaced with the non-duplicate assertion: on a leg where the detector is inert the message is **today's Pi-verbatim string with no dangling `—`** — i.e. the `reason is None` branch §B introduces |
| TESTS-7 | tests-and-ci | MINOR | **PARTIAL** | `pty`/`termios`/`fcntl` at module scope take the whole 23-case file down at collection on win32 | §C states the import placement rule up front and cites ci.yml's own "the next `fcntl` import" sentence |

## §D (docs) — the lane whose refuter died

Every row below is **NO VERDICT**: applied conservatively as instructed, and none of it is
measured by a refuter. Two of the seven I could check cheaply from the files, and did — noted.

| id | lens | sev | verdict | evidence in one line | disposition |
| --- | --- | --- | --- | --- | --- |
| PRODUCT-4 | product-and-pi | MAJOR | NO VERDICT → applied | `subprocess_hooks.py` carries verbatim the rationale §A.1 is retiring; its timeout is 60 s default / 600 s max, per event | §D.8 fixes the comment (no behaviour change); §D.3 adds a "What stays open" bullet with the magnitude **and** why the port is not trivial (`ThreadedChildWatcher` reaps with a blocking `waitpid(pid, 0)`); the follow-up issue is filed with that text, not a one-line title |
| DOC-1 | docs-adr | MAJOR | NO VERDICT → applied (**premise verified here**) | Checked: the #221 amendment is at ADR-0238 ~576, inside `## Consequences` (384) — the new paragraph appends **below** it, so rev 1's "below" pointer was backwards | §D.3-1 cites the #221 amendment as the origin of the narrowing instead of re-deriving it, and fixes below→**above**; §D.3-2 states the discriminator so #221 is visibly not reopened |
| DOC-2 | docs-adr | MAJOR | NO VERDICT → applied (**premise verified here**) | Checked: `CHANGELOG.md:351-356` still says "`!command` also keeps its controlling terminal, which is what a credential helper needs", unreleased | §D.7 corrects that paragraph **in place** first (the repo's convention for an unshipped entry) and lets the new entry carry the detector; §D.4 narrows the `docs/decisions/README.md` 0238 bullet in place rather than appending after it |
| DOC-3 | docs-adr | MAJOR | NO VERDICT → applied | "~0.05 s" is a resolver-internal detect-and-kill placed at a surface where the reader reads it as end-to-end | §D.6 README carries **no figure**; §D.7 labels the number as the resolver's and asks for §E's `time` figure separately; §D.5's guide sentence says "plus Aelix's own startup" |
| DOC-4 | docs-adr | MAJOR | NO VERDICT → applied, and **PRODUCT-1 made it decisive** | rev 1 attributed the stop to "`pass`, `gpg`, an `op read` that falls through to `pinentry-tty`" while §H said no real binary was run | §D.7 strikes `pass`/`gpg`/`op read` from the beneficiaries; §D.3-3 keeps only the names that were **measured** and drops "every pinentry call" |
| DOC-5 | docs-adr | MAJOR | NO VERDICT → applied | `test_resolve_config_spawns_a_new_group_in_the_same_session`'s docstring says the tty consequence "is not reproducible headless" — the new case reproduces it headless in the same file | §C row 6 replaces the **whole** docstring (corrected reason, consequence now pinned elsewhere, this case pins the kwarg only); §B.2 extends the same qualifier to `_process_tree.py`'s "so the terminal survives" / "SAME session, tty kept" |
| DOC-6 | docs-adr | MAJOR | NO VERDICT → applied | The ADR gets a #226 *landed* bullet but none for the residue it knowingly leaves | §D.3's "What stays open" gets **two** bullets, the second being the hooks site — routing it to a GitHub issue alone would repeat the miss #226 was born from |
| DOC-7 | docs-adr | MINOR | PARTIAL | "…has no terminal to prompt on" is the retired shorthand pointed the other way; the remedy shipped without the "unanswered askpass still burns 10 s" caveat | Wording fixed in §B.1 and §D.5. 🔴 The caveat is **deliberately kept out of the message** (already ~250 chars, and §E.5-c doubts it reaches the TUI screen intact) and lives in the guide and CHANGELOG — recorded as a choice in §G |
| POSIX-4 | posix-job-control | MINOR | PARTIAL | dash does **not** exec a single simple command in `-c`; the leader stays `sh` | §H #9 rescoped as darwin/bash-3.2-only, new row #40, and §A.5 turns it into the reason the group-delivery argument matters **more** on the gating leg |
| PRODUCT-5 | product-and-pi | MINOR | PARTIAL | The resolve is synchronous on the event-loop thread, so today the whole TUI freezes for the 10 s; and the reason string arrives triple-wrapped | §E.5 now asks for three observations: the `main` freeze as contrast, no freeze on the branch, and **that the ~250-char string reaches the screen intact** (screenshot/pane capture) |

---

## Two apparent conflicts between refuters, settled

**1. Is a `pinentry` detected or not?** POSIX-2 measured `pinentry-tty` (0.355 s) and
`pinentry-curses` (0.05 s) **stopped and detected**; PRODUCT-1 measured `!gpg -d` and `!pass show`
going the **full 10 s with the detector silent**. Both are right and the discriminator is *who forks
the pinentry*: POSIX-2 ran it as a descendant of the `!command` shell — inside our group, so job
control applies — while the normal `gpg`/`pass` architecture has `gpg-agent` (a pre-existing daemon
in its own session) fork it, where no job-control check applies at all. The design states it that
way in §A.8, §D.3-6, §D.7 and §G rather than picking one number.

**2. What should the reason string say?** POSIX-3 wanted "a models.json `!command` … which is never
the terminal's foreground group"; PRODUCT-2 measured that the cached path serves **`auth.json`** and
that "it has no terminal to prompt on" contradicts the ADR written in the same commit. Synthesised
in §B.1: keep POSIX-3's *"a process group of its own, which is never the terminal's foreground
group"* (the one sentence that tells a maintainer why the group is there), drop the file name, and
replace the terminal-denial with "the kernel stops it the moment it touches the terminal — it cannot
prompt you". The `stopped {what} ({name})` prefix stays byte-identical for the tests.

---

## Do not re-raise — refuted by measurement

1. **"The detector has a silent hole for the class of helper the ADR names most loudly" (POSIX-2).**
   Measured against the real binaries: `pinentry-tty`, `pinentry-curses`, `ssh`'s `readpassphrase`
   (`ssh-keygen -y`), `sudo` and a `stty`-based git helper **all** leave `SIGTTOU` at its default and
   were **all** detected in 0.05–0.36 s with the terminal untouched. The names in the ADR are right;
   only the unconditional POSIX *rule* around them was over-broad, and that is now narrowed.
2. **"The design's test set does not hold the group decision" (POSIX-3).** Measured: deleting
   `**containment_spawn_kwargs()` turns 23 passed into **3 failed, 20 passed**, one of which is the
   very case §C retains, and the new pty case goes red too (bounded, 10.05 s). **No new test.**
3. **"Dropping the kwarg is strictly worse" (POSIX-3).** Not strictly: with Aelix in the
   **foreground** the same mutant does not stop at all (`S+`), reads the terminal successfully and
   returns the typed line as the key — Pi's shipped behaviour. It is worse in the backgrounded case
   and in the TUI; §A.1 says that rather than overclaiming.
4. **"The ADR and CHANGELOG drafts misstate the mechanism" (POSIX-3).** They already said
   "foreground group". Only §B.1's reason string and §A.1's one-liner used the shorthand.
5. **"The reap-handoff line consumes the zombie" (POSIX-1).** It does not — `os.waitpid(pid,
   WNOHANG|WUNTRACED)` reaps whether or not the status is handed back (measured: delete the handoff,
   still `ESRCH`/`EMPTY`). Any future reader tempted to "restore the pin" by dropping the handoff
   would only restore the exit-7-reads-as-0 corruption.
6. **"The released pgid is a lost kill" (POSIX-1, implied).** In the only shape that releases it, the
   stdout holder had already left the group and `killpg` was **already** a no-op on `main`
   (`descendant_alive_after_kill=YES` on both arms). What is gained is a ~9.95 s window in which
   `killpg` could reach an *innocent* recycled group — say it that way, not as a lost kill.
7. **"Four `pyright: ignore` comments" (TESTS-1).** Three: one comment covers both names on the
   `os.WNOHANG | os.WUNTRACED` line. `os.waitpid` and `os.waitstatus_to_exitcode` need none.
8. **"pytest reports this as an ERROR" (TESTS-2).** A body exception is a **FAILED**; the leg is red
   either way. Its own proposed `set(MAP) == {getattr(signal, n, None) …}` alternative cannot be
   produced with `monkeypatch.delattr` (the map is built at import — measured `{21,22} == set()`).
9. **"Read stdout to EOF, then call the detector once, and assert the loop saw the reap" (TESTS-3).**
   Measured unsafe: the single call misses the reap in ~2% of runs (3/120, 2/120; max lag 98 µs), and
   the "saw the reap" assertion **fails on the gating windows leg**, where the detector is inert by
   design (20/20) and the loop burns its whole deadline.
10. **"The pinentry survives our kill, holding the terminal in echo-off" (PRODUCT-1).** Measured
    false: `gpg-agent` tears it down 35–37 ms after our `killpg` and ECHO is back at 0.0 s. The
    finding's simulated pinentry simply had no teardown path.
11. **"The gpg path already works today in ~0.36 s" (PRODUCT-1).** That figure came from the same
    simulated daemon. On the real stack the answered path takes as long as the human does (1.853 s
    in the driver); what is true and kept is that it **does** resolve and the detector does not break it.
12. **"Scope option 2 and hand the foreground over" (PRODUCT-3, fix (b)).** Three measurements kill
    it: the abandoned prompt produces no stop to detect and leaves echo off; the handover is a
    sub-millisecond race that needs `SIGCONT` — i.e. it needs option 3's detector rather than
    composing with it; and in the TUI it stops Aelix in 21 ms. Fix (a), the missing product sentence,
    is what was taken.
13. **"`^C` going to the child is a reason to reject option 2" (rev 1's own §A.3).** Measured false:
    `^C` cancelled the prompt and the agent survived. The sentence is deleted from the design.

---

## Decided here, not by a refuter — and the owner may overrule

| decision | why | where |
| --- | --- | --- |
| **No `os.waitid(…, WNOWAIT)` platform fork**, even though POSIX-1 measured that it works on Linux (zombie and pgid retained, stop reported repeatedly) and `os.waitid` is absent on darwin | The gating POSIX leg is Linux **only**. A fork would leave the more dangerous path — darwin's reap-and-repair — untested in CI forever, and would need a second arm on §C case 3. One path, exercised on both legs; the cost is two sentences of documentation | §A.6, §D.3-7, §G open question (2) |
| **No termios snapshot/restore around the spawn**, leaving POSIX-2's ignore-SIGTTOU echo-off hole open | Restoring on the failure path would also undo settings a *successful* `!command` changed on purpose, and that is outside a message-delta issue. `stty sane` is documented instead | §G residue bullet + open question (3) |
| **The "an unanswered askpass still costs 10 s" caveat is not in the message** | The reason string is already ~250 characters and §E.5-c exists precisely because nobody has confirmed it reaches the TUI screen intact | §G, §D.5, §D.7 |
| **The hooks site is not ported in this commit** — only its comment and an ADR bullet | Its asyncio ladder reaps with a blocking `waitpid(pid, 0)` that never returns on a stop, so the synchronous detector cannot be called from it. Magnitude recorded (60 s default, 600 s max, per event) so the follow-up is filed with substance | §D.3, §D.8, §G |

---

## Measurements the implementer must still take — nobody could take them here

1. **Anything on win32** (no host): that `_WAIT_STOP_OPTIONS == 0`, that §C's win32 arms are real,
   that the `CONIN$` clause holds, and the `warnings.warn` elapsed for cases 1/3/4 from the `-q` log.
2. **On an actual `ubuntu-latest` runner.** Linux is now measured, but on Debian-trixie dash under an
   OrbStack kernel via docker, aarch64 (plus one emulated amd64 run).
3. **The live checks in §E** — all five, including the two that are expected to record a *negative*
   (the ignore-`SIGTTOU` arm that leaves echo off, and the gpg-agent-mediated 10 s with no cause).
4. **The end-to-end `time`** for the CLI path, which is the number `CHANGELOG` may quote as
   user-facing — distinct from the resolver's 0.054 s.
5. **A GUI pinentry** (`pinentry-mac`) and `op read`: named in the guide, unmeasured, and the guide
   says so.
</content>
