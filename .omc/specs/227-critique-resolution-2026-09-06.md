# #227 design — revision 2 resolution table

Base: `.omc/specs/227-design-2026-09-06.md` (revision 1, now overwritten in place by revision 2),
main `7fa6796`, worktree `/tmp/wt-227`. Four lenses (layering-and-shape, windows-semantics,
tests-and-legs, docs) filed **31** findings: 10 got a measured refuter pass, 8 were MINOR ("not
refuted; apply if it costs nothing"), and on 13 the refuter died before returning a verdict
(WIN-6, TL-1…TL-5, DOCS-1…DOCS-7 — treated as UPHELD-pending and applied conservatively).

**Count: 5 UPHELD, 13 PARTIAL, 13 UPHELD-pending (refuter died), 0 wholly REFUTED.** No finding's
topic is dropped; what was refuted is individual sub-claims *inside* otherwise-adopted findings, and
those are listed at the bottom so the next round does not re-litigate them.

The revision's centre of gravity moved twice. Revision 1 was "move a chain down, spawn through it,
harden PowerShell". Revision 2 is that **plus a fall-through predicate that actually works on the
only platform the loop has more than one candidate on** (the class-based tuple aborted the chain on
the exact win32 error a `sh.cmd` produces — the #227 symptom, restored by the fix for #227), **plus
a cmd.exe command line that cmd can parse**, **plus two cases that really execute a PowerShell and a
cmd argv on the windows leg** — because the leg has Git's `sh.exe`, so every Windows-specific
sentence in the ADR and the CHANGELOG had zero Windows evidence. And one of revision 1's central
claims was simply false: `-NonInteractive` does not shorten a `Read-Host` hang, because
`stdin=DEVNULL` already ended it in #222.

---

## Findings

| id | lens | sev | verdict | one-line evidence | disposition in revision 2 |
|---|---|---|---|---|---|
| WIN-1 | windows-semantics | BLOCKING | **PARTIAL** (blocking half stands, its rationale and fix text do not) | `stdin=DEVNULL`, pwsh 7.6.5: plain `Read-Host` rc 0 in **0.652 s** against a 0.45 s no-prompt baseline — there is no hang for the flag to remove. But "only the shape of the failure" is wrong: the prompt text lands **inside the resolved key** (`'give me a key: \nGOT:'` vs `'GOT:'`), and on win32 the masked read opens `CONIN$` directly | §A.2's `-NonInteractive` paragraph rewritten from timing to the three things stdin cannot reach (prompt-into-the-key, the `CONIN$` secure read, the `CONOUT$` write), each labelled measured or source-read. §D.1(b)/§D.3/§D.4/§D.5 all re-worded off "fails at once instead of holding the timeout". New §H M8/M9. `-NonInteractive` **stays**; no code change follows |
| LS-1 | layering-and-shape | MAJOR | **UPHELD** | §B.3's `(FileNotFoundError, NotADirectoryError, PermissionError)` tuple misses `ENOEXEC`, which has no `OSError` subclass; transcribed verbatim, a candidate that exists but is not an image aborts the chain — `result=None`, **1 spawn** — while the design's own case 6 (an *absent* candidate) passes | New §A.9 (classify by intent, by errno). §B.3's loop split into `except ValueError` / `except OSError as exc`. §A.1 and §A.2's row 6 reworded so no candidate's spawn failure can end the chain. §C cases 7/9/10 replace rev 1's single case 7; mutation rows (e1)/(e2)/(e3) |
| WIN-3 | windows-semantics | MAJOR | **UPHELD** | Independently reproduced with §B.3 transcribed verbatim: a real `+x` non-image file → `OSError(errno=8)`, chain aborted at candidate 1. `PC/errmap.h` v3.12.13 (sha256 `1419fa1f…`, byte-identical to the copy LS-1 fetched) maps winerror 11 and 188..202 → `ENOEXEC`; its `default:` arm → `EINVAL`, also subclass-less, and `%COMSPEC%` has no `exists()` gate | Same §A.9. WIN-3's **widening adopted over LS-1's simplification**: the allowlist is `{ENOENT, ENOTDIR, EACCES, EPERM, ENOEXEC, ELOOP, EINVAL}`, which keeps the EMFILE "one spawn" pin LS-1's blanket `except OSError` would have destroyed. §H M10 |
| LS-2 | layering-and-shape | MAJOR | **UPHELD** (and the fix improved) | §B.2's compat re-export measured under the repo's own ruff config: **6× F401 + I001**, and `uv run ruff check .` is a CI job. Also `import re` must go (one use, which moves) and `permission.py` is **not** a consumer of any moved name | §B.2 rewritten: no re-export, six consumer sites repointed. Went one further than the finding: **`ShellConfig` moves too**, which makes every surviving import in `bash.py` a used one — measured `All checks passed!` and **86 lines** out of `bash.py` (M12) — and settles LS-6 at the same time. §F gains the citation-drift warning for the four line-anchored `bash.py` citations |
| WIN-2 | windows-semantics | MAJOR | **PARTIAL** | Rendering confirmed: `list2cmdline` gives `cmd.exe /d /c "op read \"op://v/k\""`, and cmd implements no `\"`. Refuted: CPython's win32 `shell=True` is `'{} /c "{}"'.format(comspec, args)` — no `/d`, no `/s` — and the finding's bare f-string leaves a spaced `%COMSPEC%` unquoted | The cmd family emits a **raw command line**, `f'"{shell.path}" /d /s /c "{cmd}"'`, path quoted; `_shell_argv` returns a `list[str]` or a `str`; `_Failure.argv` and `_StoppedByTerminal.cmd` widened. §C case 14 asserts the rendered line per candidate incl. a spaced-`%COMSPEC%` row; mutations (l)/(m). §G-1 rewritten with the corrected attribution and the reachability scoping |
| WIN-4 | windows-semantics | MAJOR | **UPHELD** (and sharper than filed) | `list2cmdline` is reachable only from the Windows `_execute_child` (lines 1455–1462 under `if _mswindows:`), so M4/M5/M6 exercise none of it. And case 1's branch is not "undetermined": run 34028961224 job 101474893576 at `7fa6796` shows a real `sh -c` spawn passing on windows-latest, and the image adds `C:\Program Files\Git\usr\bin` | §C case 1 demoted to "a regression pin on the `sh` path only", with the CI evidence in its cell. New cases **16** (a forced PowerShell candidate really spawns on the leg) and **17** (the `cmd.exe` floor really spawns), both no-skipif with a POSIX arm. §E rewritten: 1 pins `sh`, 16/17 are the Windows evidence for what #227 adds. §H M4 relabelled, M14 added |
| WIN-5 | windows-semantics | MAJOR | **UPHELD** | `containment_spawn_kwargs(platform="win32")` is `{"creationflags": 0x200}` and nothing else; CPython's only `SW_HIDE` assignment is inside `_execute_child`'s `if shell:`; Pi passes `windowsHide: true` and Node's default is false. And the flag must NOT move into the shared helper — three callers' `soft_kill()` → `ctrl_break()` needs the shared console | New §A.8 and `_spawn_kwargs()` in §B.3 (`CREATE_NO_WINDOW` OR'd at this call site only), §B.4 amends `containment_spawn_kwargs`'s closing docstring line. §C case 15 is the discriminator (the runner *has* a console, so no behavioural case could see it); the existing group case is amended; mutations (n)/(o). §H M13 |
| LS-4 | layering-and-shape | MAJOR | **PARTIAL** (gap real, three evidence details wrong) | A literal `{"pwsh","powershell"}` against `Path(p).stem.lower()` passes design cases 2, 3 **and** 4 — the mutation is unkilled — and diverges on **18 of 66** candidate paths: `pwsh-7.5.0.exe` would lose `-NoProfile`, i.e. M5's banner inside the key. Refuted: §B.1 *does* export the accessor triple, `_resolve_config.py` and `_shell.py` are the **same** package, and it would be a second copy, not a third | §B.3's `_shell_argv` docstring names its source (the moved `shell_basename`/`POWERSHELL_NAMES`/`CMD_NAMES`, the same three `dialect_for_shell` imports) and says why not `shell.command_flag`. New §C case 13 (a `$SHELL` named `pwsh-7.5.0.exe`, measured RED against both re-hardcode spellings), mutation (k). §G-8 names `command.com`'s pre-existing `-c` |
| LS-3 | layering-and-shape | MAJOR | **PARTIAL** | The "exactly one entry, after `$SHELL`" invariant is **false-failing**: without an `sh` fixture the two lists are equal (5 of 9 rows), and "after `$SHELL`" holds in 1 of the 4 rows where the entry appears. Refuted: mutation (i) *is* killed — by half (a), on any table row with `sh` and no `$SHELL` | §C case 18 restated in three halves: (a) a Git-for-Windows row where the two callers must answer **differently**, (b) the order/drift equality, (c) the `sh` delta stated conditionally. Mutation (i) now names half (a) and its precondition. LS-3's F2 (an env row without `PATH` probes the host's) is fixed **in the primitive**, not in fixture discipline — see LS-8/TL-3 |
| LS-5 | layering-and-shape | MAJOR | **PARTIAL** | Upheld: the `Read-Host` clause belongs to the **#222 bash-tool** bullet, whose timeout is 600–3600 s, not a `!command`'s ten; `models.json` never mentions `sh`. Refuted: §D.1's drafted replacement did carry the "ten seconds / nobody has watched" sentence — what it dropped was the detector-inertness clause | §D.3 leaves the #222 bullet's shape alone and attaches the new clause to the **next** (`!command`/#226) sentence, both languages. §D.1 becomes add-then-**amend**, not replace. §D.6 gains `_run_shell_command`'s own win32 paragraph as the third copy of the claim. **LS-5's own M8 is not adopted** — see "do not re-raise" |
| WIN-6 | windows-semantics | MAJOR | **UPHELD-pending** (refuter died) | Re-measured here: 3.12's `which` prepends `os.curdir` when `_win_path_needs_curdir`, and `files = [cmd + ext for ext in pathext]` over `.COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC` — the bare name is never tried | The finding's **hazard** is adopted as §G-2 with its mechanism measured (M11); its **suffix-filter fix is not**, because the `PATHEXT` half is already defused by §A.9 (CreateProcess refuses a `.BAT`/`.CMD`/`.VBS`, the chain moves on) and a filter would fork the two callers' answers, which is what §A.4 exists to prevent. The CWD half is the pre-existing `_resolve_shell` shape; ORCHESTRATOR follow-up, and §G-2 carries the owner's open question |
| TL-1 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | Same defect WIN-4 measured with CI job ids | Resolved once with WIN-4: cases 16/17 |
| TL-2 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | Same defect WIN-2 measured | Resolved with WIN-2, taking its option **(a)** (a raw command line for the cmd family), not (b) |
| TL-3 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | `shutil.which('sh', path=None)` → `/bin/sh` on this box, so a row meant as "stock Windows" is silently `sh`-present | Adopted at its stronger end: `windows_command_shells` now reads `PATH` once and **skips PATH probing entirely when the key is absent** (§B.1), so the fallback cannot happen; §C's fixture paragraph still states "no case may omit `PATH`". Production is unaffected (`env` is `os.environ`) |
| TL-4 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | Case 8 could not tell `.strip()` from the `rstrip('\r\n')` the design rejects, and `.strip()` also trims leading whitespace on the cached path | §C case 11 parametrized with `" sk-abc \t\r\n"`; mutation (g) names both spellings. §A.3 restated as a **cross-platform** change and §D.5 gives it its own platform-neutral CHANGELOG paragraph (with DOCS-7) |
| TL-5 | tests-and-legs | MAJOR | **UPHELD-pending** (refuter died) | Same F401 defect LS-2 measured | Resolved with LS-2 — and its redundant-alias remedy is **not needed**: with `ShellConfig` moved there is no shim at all |
| DOCS-1 | docs | BLOCKING | **UPHELD-pending** (refuter died) | Same misplacement LS-5 measured against the README's line structure | Resolved with LS-5. DOCS-1's extra target — the closing Windows sentence of the **#226 CHANGELOG entry** — is not amended: a CHANGELOG entry is a record of what shipped then, and §D.5's new entry carries the correction |
| DOCS-2 | docs | BLOCKING | **UPHELD-pending** (refuter died) | "a box that resolves one today keeps the same one" is false ($SHELL is candidate 1), and a command written for `sh` is silently mis-executed by PowerShell or cmd | §D.5's lead rewritten to "no longer assumed to run under `sh`", the absolute claim narrowed to "a box with an `sh` on `PATH` and no `SHELL` set", and **write the command for the shell that will run it** added to both the CHANGELOG and §D.1a. The deeper half is the §G-2 open question for the owner |
| DOCS-3 | docs | BLOCKING | **UPHELD-pending** (refuter died) | M4 was pwsh 7 on darwin; a stock box lands on Windows PowerShell **5.1** | Folded into WIN-1's rewrite: every `-NonInteractive`/`-NoProfile` statement in §A.2, §D.1(b), §D.4 and §D.5 now says which implementation was measured and that 5.1 is unmeasured; §D.4 puts M5 in the reasoned-not-measured set beside `/d` and the quoting |
| DOCS-4 | docs | MAJOR | **UPHELD-pending** (refuter died) | `docs/decisions/README.md`'s ADR-0238 row carries the win32 sentence #227 narrows, and every prior amendment moved that row | §D.4 adds the index row: extend it with the #227 landing the way it records #222/#226, and narrow its win32 clause the way §D.3 narrows the README's |
| DOCS-5 | docs | MAJOR | **UPHELD-pending** (refuter died) | `_resolve_config.py`'s module docstring and `_execute_command_uncached`'s both say the command runs "via `sh -c`" | §D.6 now names all of them, including `_run_shell_command`'s #226 win32 paragraph (LS-5's third copy), plus the two comments elsewhere that name `tools/bash.shell_basename` |
| DOCS-6 | docs | MAJOR | **UPHELD-pending** (refuter died) | The guide's own mitigation (`export $SHELL=bash.exe`) also flips the AUTO permission dialect on that box — the ADR-0237/#204 consequence §A.2 refuses to cause | §D.1a states the coupling in the same clause as the advice; §D.4's Consequences records that #227 is what makes one env var govern two surfaces |
| DOCS-7 | docs | MAJOR | **UPHELD-pending** (refuter died) | The trim is a cross-platform change to the cached path, not a CRLF fix | Resolved with TL-4: its own CHANGELOG paragraph, platform-neutral, plus §A.3 and the ADR Consequences |
| LS-6 | layering-and-shape | MINOR | **PARTIAL (adopted, in full)** | Two frozen dataclasses for one `(path, flag)` pair compare unequal silently, which is why case 10 had to say "field-for-field" | Cost nothing once LS-2's move was widened: `CommandShell` is **deleted from the design** and `ShellConfig` itself moves into `aelix_ai/utils/_shell.py`. One dataclass, `==` works, no conversion site, and the five existing `ShellConfig` importers are untouched |
| LS-7 | layering-and-shape | MINOR | **PARTIAL (adopted)** | The CWD-first search was booked only as test hygiene | Promoted to §G-2 (with WIN-6), as a production risk in a credential path, with the `PATHEXT` half's mitigation named |
| LS-8 | layering-and-shape | MINOR | **PARTIAL (adopted)** | Same host-PATH fallback TL-3 names | Fixed in the primitive (§B.1's `path is not None`), so it is not a fixture rule anyone can forget |
| WIN-7 | windows-semantics | MINOR | **PARTIAL (adopted as documentation, not code)** | Re-measured here: with `-NonInteractive` and a later successful statement, `Read-Host` fails but the command exits **rc 0** with empty output; `_execute_command_uncached` ends `out or None`, `resolve_config_value` does not and caches `""` | §G-4 states it with the measurement. **Not fixed here** — that resolver's contract is *raise*, not `None`, so aligning them changes an exception contract; ORCHESTRATOR follow-up. The user-facing text deliberately promises "refused", never "a named error" |
| TL-6 | tests-and-legs | MINOR | **PARTIAL (adopted)** | Two existing cases assert `len(seen) == 1`, which after #227 is conditional on candidate 1 spawning | Both named in mutation row (j), with M14 as the evidence that the condition holds on the runner |
| TL-7 | tests-and-legs | MINOR | **PARTIAL (adopted)** | A shell-start budget on the runner is a known flake (pwsh 0.5–0.57 s there) | §C case 1 says the elapsed is `warnings.warn`ed, never asserted |
| TL-8 | tests-and-legs | MINOR | **PARTIAL (adopted)** | 5.1's redirected stdout uses the console/OEM code page; the decode is UTF-8 `errors="replace"` | §G-5. Case 16 pins an ASCII `x` byte-for-byte on the leg; a non-ASCII key stays unmeasured and is said to be |
| DOCS-8 | docs | MINOR | **PARTIAL (adopted)** | ADR-0238's subject is containment; the ADR that recorded these two resolvers is **ADR-0140** | §D.4 amends both — ADR-0238 for the spawn and the containment flag, ADR-0140 with a one-line amendment and a backlink for the `!command` shell and the trim |

---

## Conflicts between refuters, settled by re-measurement in this lane

1. **Fall through on *every* `OSError` (LS-1) or on an errno allowlist (WIN-3)?** Both fix the real
   hole; they differ on breadth. Measured here (`OSError(n,'x').__class__` over eleven errnos):
   the allowlist covers every win32 "not a runnable shell" error including `errmap.h`'s `default:`
   arm, while `EMFILE`/`ENOMEM`/`EBADF` stay outside it — so **WIN-3's allowlist is adopted**,
   because LS-1's blanket clause would delete the design's own EMFILE "one spawn" pin and spend two
   useless spawns on a failure the next candidate cannot fix. LS-1's *rationale* (classify by
   intent; the errmap-before-errnomap chain) is adopted verbatim into §A.9. LS-1's alternative —
   a **winerror** allowlist — is refuted: measured, `exc.winerror` is `reportAttributeAccessIssue`
   on the host leg and clean only under `--pythonplatform Windows`, while `exc.errno` is **0 errors
   on both** (M10).
2. **Does the bash tool's `Read-Host` clause need narrowing (WIN-1) or protecting (LS-5)?** LS-5
   measured the bash-tool argv shape still running at 8 s — with a **pty** on stdin. Re-measured
   here in both stdin shapes: `DEVNULL` plain **rc 0 in 0.652 s**, pty plain **still running at
   9.6 s**. The bash tool has passed `stdin=subprocess.DEVNULL` since #222 (its own spawn comment
   says "a real stdin reader gets EOF from `NUL`"), so LS-5's number does not describe production.
   Settled as a merge: LS-5's structural point holds (do not narrow that bullet on `-NonInteractive`'s
   strength, and do not merge a 600 s timeout with a 10 s one), and WIN-1's factual point holds
   (`calls Read-Host` is wrong, on **#222's** basis). §D.3 makes exactly one word-level correction
   and leaves the bullet's shape and hedge intact.
3. **LS-2's export list vs LS-6's two dataclasses.** Measured on the real `bash.py` under the repo's
   ruff: cutting the moved names *and* `ShellConfig`, deleting `import re`, and importing the three
   survivors gives `All checks passed!` (86 lines removed). Moving `ShellConfig` is therefore
   strictly cheaper than the alias/`__all__`/`noqa` variants LS-2 measured green, and it dissolves
   LS-6 instead of documenting it.
4. **Does case 18's equality half still kill mutation (i)?** LS-3 measured that it does — against
   *today's* `_resolve_shell`. But §B.2 makes `_resolve_shell_win32` **delegate** to the primitive,
   so after this change an equality assertion compares the mutant to itself. Case 18 half (a) is
   therefore restated as a **value** assertion on a Git-for-Windows row (the bash tool must answer
   pwsh where the `!command` chain answers `sh`); half (b) keeps the equality as an order/drift pin.
5. **WIN-6's suffix filter vs §A.4's one-chain rule.** Not adopted as code: the `.BAT`/`.CMD`/`.VBS`
   half is already dead once §A.9 lands (CreateProcess refuses a non-image, the chain moves on), and
   a filter present in one caller and absent in the other is the drift §A.4 exists to prevent. The
   hazard is recorded in §G-2 with its measured mechanism and an ORCHESTRATOR follow-up.
6. **Fixture discipline (TL-3/LS-8) vs a primitive that cannot leak.** Fixed in `windows_command_shells`
   rather than in §C's prose, because the trap is invisible when it fires (a "stock Windows" row
   quietly finds this box's `/bin/sh`). Production is unaffected: `env` is `os.environ`.

---

## Do not re-raise — refuted sub-claims, with the reason

Each was filed inside a finding that is otherwise adopted, and each was measured false here.

1. **"A winerror allowlist is the fix"** (LS-1's alternative). It costs a pyright ignore plus a
   hand-kept copy of `errmap.h`'s classification; `exc.errno` needs neither (M10).
2. **"`except OSError: continue` — retry everything"** (LS-1's primary). It changes no outcome
   (still `None`) but deletes the EMFILE pin and adds two useless spawns; the errno set is the
   discriminating form.
3. **"`permission.py` is a consumer of a moved name"** (LS-2). It imports `_resolve_shell`, which
   stays in `tools/bash.py`. The real consumers are `dialect.py`, `bash_classifier.py`,
   `powershell.py` and three test modules.
4. **"§B.1 exports no family accessor" / "reaching into another package's private frozensets" /
   "a THIRD copy"** (LS-4). §B.1 moves `shell_basename` and both name sets; `oauth/_resolve_config.py`
   and `utils/_shell.py` are the **same** package (`aelix_ai`); a re-hardcode would be the second
   copy, not the third. The core gap — `_shell_argv`'s unstated dispatch source — is real and fixed.
5. **"Half (b) of case 10 lost the coverage of mutation (i)"** (LS-3). Half (b) never calls
   `_resolve_shell`, so it never killed (i); fixing the delta wording restores nothing. The real
   threat to (i) is the delegation (conflict 4).
6. **"§D.1's replacement deletes a still-true safety statement"** (LS-5's half b). The drafted
   replacement carried the "still costs the whole ten seconds and nobody has watched that" sentence
   verbatim; what it dropped was the *detector-inertness* clause, which the amend-don't-replace form
   keeps.
7. **"The bash tool's `[pwsh, -Command, 'Read-Host …']` still hangs 8.6 s"** (LS-5's M8). Measured
   with a pty on stdin. Under the `stdin=DEVNULL` the bash tool has passed since #222: rc 0 in
   0.652 s.
8. **"`-NonInteractive` only changes the shape of the failure, into a stderr that is DEVNULL"**
   (WIN-1's rationale), and its proposed "turns that silent empty into a named error". Measured:
   the unguarded prompt lands in **stdout**, i.e. inside the key; and `stderr=DEVNULL` means no
   named error reaches anyone, so the design promises "refused", not "named". Its CredUI claim is
   also dropped as a *benefit* — 7.6.5 has no CredUI branch at all, and the 5.1-era snapshot's is
   not behind the guard.
9. **"CPython's `shell=True` uses `%ComSpec% /d /s /c`"** (WIN-2's evidence paragraph). It uses
   `'{} /c "{}"'.format(comspec, args)`. Node's shell spawn is the one documented `/d /s /c`.
10. **"`f'{path} /d /s /c \"{cmd}\"'` is the fix"** (WIN-2's proposed code). It leaves the path
    unquoted, which breaks a spaced `%COMSPEC%`/`$SHELL` that `list2cmdline` quotes today
    (measured). The adopted form quotes it.
11. **"`errno.ELOOP` covers a win32 case"** (WIN-3's set). `ERROR_CANT_RESOLVE_FILENAME` maps to
    `EINVAL`, not `ELOOP`. Kept anyway — inert on win32, correct on POSIX.
12. **"No Windows leg ever executes any of the new win32 argv"** (WIN-4's headline). After #227 the
    leg's existing real spawns do go through the new arm, with an absolute `sh.exe` instead of a
    bare `"sh"`. The true, sharper claim — no leg executes a **PowerShell or cmd** argv — is what
    cases 16/17 answer.
13. **"Case 8's stub cannot pin the CRLF rule"** (WIN-4). `.strip()` is a `str` rule and the stub
    pins it fully. The gap is that no case saw a CRLF a real shell produced; cases 16/17 close it.
14. **"Keep the list form and land a warn-case"** (TL-2's option b). Superseded: the raw command
    line is the fix, and case 14 pins it on every leg for free.
15. **"Spell the shim as redundant aliases"** (TL-5). There is no shim once `ShellConfig` moves.

---

## Open question carried into §G — only the owner can answer

Every `!command` in existence was written for `sh`, because `sh` was hard-coded. On a stock Windows
box the new chain hands it to PowerShell or cmd, which can run it to a **zero exit and a wrong
value** — a POSIX `$VAR` expansion is empty under PowerShell, literal under cmd, and the resolver
takes whatever a zero-exit command printed. Revision 2 proceeds on "run it, and say plainly in the
guide and the CHANGELOG to write the command for the shell that will run it", because the
alternative — stop the chain at `sh` and raise a named "no POSIX shell for this `!command`" —
leaves the stock box as broken as #227 found it, only with a better message. If the owner prefers
the loud failure, it is one branch in `_shell_argv_candidates`, cases 16/17 invert, and nothing else
in this design moves. §G-2 carries it.
