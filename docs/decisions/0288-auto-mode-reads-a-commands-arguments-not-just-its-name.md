# 0288. AUTO mode reads a command's arguments, not just its name

Status: Accepted (2026-11-03; owner decisions 1–4 settled 2026-11-03; review
rounds 1 and 2 2026-11-04)
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
  value is not bounded.
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
