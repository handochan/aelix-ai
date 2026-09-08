# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The published distribution set is released in lock-step at a single shared
version: `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`, and the `aelix`
umbrella meta-package. (`aelix-server`, the Web-UI daemon, is deferred to a
later release and is not part of this publish set.)

<!--
No comparison / tag links are defined at the bottom of this file on purpose:
the repository has no tags and no releases yet, so every `.../compare/vX...HEAD`
and `.../releases/tag/vX` link would 404. Add them with the first pushed tag.
-->

## [Unreleased]

### Changed

- **A tool card now shows 5 lines of output, not 12.** A `read` of a 40-line
  file used to spend 14 rows of scrollback on an 80-column terminal — one
  header, twelve body lines and the elision footer — for output the model had
  already summarised; it now spends 7. The footer still carries the whole body:
  `… (+35 more lines · /expand 1)`, and `/expand 1` prints it. The 40-line cap
  on **error and diff** cards is deliberately untouched: a Python traceback's
  diagnostic tail sits at the bottom, so head-truncation eats exactly the line
  you need (ADR-0112).
  **What you give up:** any ordinary result between 6 and 12 lines used to
  render whole and now ends in `… (+N more lines · /expand N)`. A 12-line `bash`
  result goes from 13 screen rows and no footer to 7 rows plus a `+7`. At the
  bottom of that range you pay without saving anything — a 6-line result is 7
  rows either way, and the change buys you a `(+1 more lines · /expand 1)`
  footer in place of its last line; the saving only starts at 7 lines. A
  **successful** `agent` card whose result carries no extra note loses its
  `[agent … · yolo · …]` footer to the cap once the child's summary runs past 3
  lines (it already did past 10); a failed delegation renders on the 40-line
  error path and keeps it far longer. `/expand N` still prints it either way.
  And because every result over 5 lines now takes an `/expand` id instead of
  every result over 12, the 100-slot store turns over faster: in a long session
  an older `/expand N` expires sooner than it used to (measured at width 80:
  one 100-line `read` followed by 100 six-line `bash` results keeps `/expand 1`
  at cap 12 — 1 id minted — and loses it at cap 5 — 101 minted, 100 retained).
  **This reaches fresh installs and anyone whose `settings.json` has no
  `toolCardMaxLines` key** — a value you saved through `/settings` is kept
  exactly as it was. The row still spans 3-40 and still applies to the next card
  without a restart. See
  [#247](https://github.com/handochan/aelix-ai/issues/247) and ADR-0112.
- **The statusline says how hard the model is thinking, without being asked.** The
  🧠 segment — `off` / `low` / `medium` / `high` / `xhigh` — shipped on 2026-08-07
  and was off unless you found it in `/statusline`. It is on by default now, and it
  sits between the model and the context meter:
  `● default  ·  ⏵⏵ all  ·  📂 ~/dev/aelix-ai  ·  ✱ gpt-5.6-codex  ·  🧠 high  ·  ◔ 24% · 96.4K/400K  ·  ⎇ main`.
  That is where the multi-line statusline already put it — right after the model —
  and it is the position that survives a narrow terminal: at the end of the row,
  where the segment used to sit, it was clipped off the glass at 80 and at 100
  columns and did not render at all.

  **Who gets it:** anyone installing fresh, and anyone who has never saved
  `/statusline`. The enabled set is persisted and a file on disk is read verbatim,
  so if you have saved that picker even once the segment stays off until you check
  **Thinking level** yourself. Your file is not migrated, deliberately: it cannot
  tell "never saw this" apart from "turned this off". **If your saved file already
  has the segment on**, it does move — the footer renders in registry order, so the
  🧠 leaves the end of the row and lands after the model, and on a narrow terminal
  it may now be visible where it used to be cut off.

  **What you give up:** one more segment on a row that is clipped, not wrapped.
  Measured in this repository's own checkout (`📂 ~/dev/aelix-ai` — the numbers
  move with your cwd): at 80 columns the branch was already off the row before this
  change and the meter already read `◔ 24% · 96.4K`, and now the meter is down to a
  bare `◔`; at 100 columns the row loses `⎇ main` outright, which used to fit. And
  a session started on a model with no reasoning support now reads `🧠 off` rather
  than hiding the segment — its existing, deliberate behaviour, visible out of the
  box. Note that the segment reports the level you *set*: switching to a
  non-reasoning model mid-session keeps showing the last level until you change it
  — since #251, with the tier that model actually receives next to it, so the row
  reads `🧠 high (off)` rather than a bare `🧠 high`. See
  [#248](https://github.com/handochan/aelix-ai/issues/248) and ADR-0160.

- **`/thinking`, `/settings` and the statusline now name the tier the model
  actually receives.** Until now every one of those surfaces printed the level name
  and nothing else, which was wrong in two directions at once. Pick `xhigh` on
  `claude-opus-4-6` and the request has been carrying `max` all along — 24 catalog
  rows map `xhigh` onto `max` — while the screen said `xhigh`. Pick `xhigh` and
  then `/model` your way to `openai/gpt-5.1`, which has no `xhigh`, and the level
  you set survives the switch but the adapters clamp it down to `high`; the screen
  still said `xhigh`. Both now read `✱ 6. xhigh (max)` in the picker and
  `🧠 xhigh (max)` in the statusline, or `xhigh (high)` for the clamp. **The
  parenthesis is the tier that goes on the wire, and it can be above the level as
  well as below it** — `deepseek-v4-pro` supports neither `low` nor `medium`, so
  choosing `low` there reads `low (high)`.

  Run the rule over the whole vendored catalog and it adds the parenthesis to
  **2909 of the 8562 (model, level) pairs**: 51 renames across 37 models, 1013
  clamps across 937, and 1845 across the 369 models that do not reason at all —
  leave the level at `high`, switch to `openai/gpt-4.1`, and the statusline says
  `🧠 high (off)`, because that is what the request carries (those adapters send no
  reasoning field whatsoever). Thirteen catalog rows served by the Google adapter
  (the Gemini 1.5 / 2.0, Gemma and Vertex-hosted Llama entries) are a known
  exception in the other direction: that adapter turns a disabled level back into
  a thinking request, so `(off)` understates them. That is an adapter defect,
  tracked as [#256](https://github.com/handochan/aelix-ai/issues/256), not papered
  over here.

  The suffix appears **only** when the names genuinely differ. A model that calls
  the level what you called it gets one bare word, as do a mapping that differs in
  case alone (Google's `HIGH` / `LOW` / `MINIMAL`, 16 rows), a mapping to a number
  rather than a name, and any model the display cannot interrogate. `off` is always
  bare even where the catalog renames it to `none`: the harness folds `off` into
  "no reasoning requested" before an adapter ever sees it, so there is no true
  value to put in the parenthesis. (The cost of that fold is worth knowing: on the
  100 catalog models that do not support `off`, asking for `off` sends no reasoning
  field at all and the model thinks at its own default.)

  Two smaller consequences. The `✱` in the picker now marks the row you will
  actually get — open `/thinking` on gpt-5.1 while the session still holds `xhigh`
  and the marker sits on `high`, where before it sat on nothing. And the statusline
  segment grows by up to **ten** columns on a row that is clipped rather than
  wrapped — `🧠 high (default)` on `groq/qwen/qwen3-32b` and `🧠 minimal (medium)` on
  `openai-codex/gpt-5.1-codex-mini` are the two widest, and both are rows you can
  pick straight out of `/thinking`; the headline `xhigh (max)` costs only six. With
  #248 in the same release the 🧠 sits right after the model, so those columns push
  the tail of the row (`⎇ branch` first) rather than truncating the segment itself.
  What the picker sends to the setter is unchanged: the level name, never the
  label. See [#251](https://github.com/handochan/aelix-ai/issues/251) and
  ADR-0155.
- **A `models.json` `!command` credential helper now runs once per registry
  load, not once per request.** `get_api_key_and_headers` is the harness's
  per-request auth callback, and it re-forked a shell for the provider's
  `apiKey` and for every `!command` header value on every single request —
  measured on darwin, a provider with a `!command` key plus one `!command`
  header spent 8.44 ms of the event loop per call, running the *same* two
  commands each time. On a box that lands on PowerShell that is one shell start
  per distinct `!command`: 431.8 ms each, measured with pwsh 7.6.5 **on macOS**
  — Windows PowerShell 5.1, which is what a stock box actually resolves, is
  unmeasured and typically slower. A turn is one request plus one more per tool
  call, so that cost was paid over and over, in a synchronous call inside an
  async callback, with the TUI unable to repaint. The resolved value is now kept
  on the registry that resolved it; a repeat is a dict lookup.
  **What you give up:** a credential your helper mints is read once and reused
  until the registry reloads, so one rotated underneath a running session keeps
  going out until then. Any turn that ENDS IN AN ERROR drops it — in the TUI and
  in `-p` alike — so a rejected credential costs one bad turn rather than a
  session, and in the TUI `/reload`, `/login` and a restart drop it too. A
  headless run has none of those three, so a helper minting a short-lived token
  wants a lifetime longer than the run. Nothing is kept for a command that fails
  or prints nothing, so a helper that starts working is picked up on the next
  request; an `apiKey` naming an environment variable is still read every time,
  it was never the cost. See
  [#240](https://github.com/handochan/aelix-ai/issues/240) and ADR-0140.
- **Pre-release tags now publish to PyPI.** The `publish` job used to skip any
  tag with a hyphen, so the four PyPI names carried nothing but a `0.0.0a0`
  reservation placeholder — and because pip and uv take the newest pre-release
  when *every* candidate is one, `uv tool install aelix@latest` installed that
  placeholder, found no entry points, and **removed the working aelix** it was
  asked to upgrade. From `v0.1.0-beta.2` the tag publishes its PEP 440
  pre-version (`0.1.0b2`); a plain `pip install aelix` stops seeing it the
  moment a stable version exists, so `0.1.0` is protected by the version
  itself rather than by a job gate (ADR-0240). The version assert that runs
  before the build now also reads `[project.optional-dependencies]` — the
  root's `aelix-coding-agent[tui]==X` pin was outside its loop, so a bump that
  missed it would have shipped a `pip install 'aelix[tui]'` demanding a
  version not on the index (mutating that pin to `0.1.0b9` now fails the
  assert; the unmodified workspace passes). **What you give up:** a
  pre-version is permanent on PyPI and a half-failed eight-artifact upload is
  a half-release; the remedy for either is the next beta number, never a
  retry of the same one.
- **`install.sh` / `install.ps1` run `uv tool update-shell` themselves when
  `aelix` is not on PATH after the install**, instead of printing the command
  and hoping. It appends uv's tool bin to the shell rc (idempotent) and the
  installer then says to open a new terminal; the current shell's PATH is
  still not changed, and a failure of `update-shell` falls back to the old
  by-hand hint.

- **A command that backgrounds a server now comes back when the command does.**
  `npm run dev &`, `nohup … &`, anything that exits 0 while a helper keeps the
  pipe: the bash tool used to read that pipe until the last holder closed it, so
  the tool call stayed open for the helper's whole life, with **no ceiling at
  all** — measured 4.05 s for a 4 s helper, 13.98 s for a call that had asked
  for 10 s, and 4.06 s for one that had asked for 1 s and was told it had
  succeeded within its deadline. Typing `!npm run dev &` into the TUI was the
  same freeze with nothing to bound it, since `!` commands carry no timeout. It
  now returns once the output has been quiet for 0.1 s after the command exited
  — the rule Aelix's contained runner already uses, the rule this same tool
  already used after a kill, and the rule Pi uses — under a 2-second ceiling
  for a helper that never falls quiet, and, where you asked for a deadline, no
  more than one 0.1 s grace past it: a root that exits just inside its own
  deadline still keeps its own tail (measured on darwin, a root that exits
  0.985 s into a 1-second call with a helper still holding the pipe: back at
  1.088 s, `exit_code=0`).
  **What you give up:** output the helper writes after that point is not
  captured and the model does not see it — and because a backgrounded program's
  stdout is a pipe, it is block-buffered and usually flushes only when it exits,
  so in practice you see **none** of a backgrounded server's output, not just
  its tail (redirect it — `nohup … > dev.log 2>&1 &` — and read the file, or run
  it unbuffered). While that helper is alive the finished call also keeps one
  background reader and one pipe open for it; they go away when the helper does.
  That is the trade Pi makes too. The helper itself is untouched — it keeps
  running, as it did before. See
  [#232](https://github.com/handochan/aelix-ai/issues/232) and ADR-0238.

- **Seven `sort`/`date`/`hostname` spellings stop being auto-approved, and one
  read starts.** AUTO mode decided whether a command was read-only by looking at
  the *shape* of its arguments — a `-` in front meant "flag", a `/` in front
  meant "writes". Both premises are false on POSIX, and the measurement that
  ended them was taken on a developer machine, not a Windows one:
  `sort -o out.txt in.txt`, `sort -oout.txt in.txt`,
  `sort --output=out.txt in.txt`, `sort --output out.txt in.txt`,
  `date --set=2030-01-01` and `hostname -b` all ran without asking you, and each
  writes a file or sets the system clock. So did
  `sort --compress-program=/tmp/x`, which executes `/tmp/x` on every temporary
  file. All of them prompt now. `sort --files0-from=` and `sort --random-source=`
  prompt too, for the reason this gate already refuses `findstr /f:`: one
  argument becomes an unbounded set of reads.

  Going the other way, `sort /etc/hosts` is auto-approved again under a shell
  that resolved to `bash`. It is a plain read, and it was refused only because
  `/etc/hosts` looks like a `cmd` switch to a rule that could not tell which
  shell it was reading for. Under `fish`, or when the shell cannot be resolved
  at all, it still prompts — the same "assume nothing" answer, now stated as a
  default instead of guessed at. See ADR-0237 and
  [#204](https://github.com/handochan/aelix-ai/issues/204).

- **Your own turns are a thicker band.** The echo bar that marks a human turn
  now carries one painted row above and below the text, so the turn reads as an
  object with a top and a bottom edge rather than as a single coloured line. The
  blank lines that fence it off from the renderer above and below are unchanged
  — the new rows go *inside* the bar's own ground, not outside it.

  The cost is vertical: a turn is five rows instead of three, a fifth of an
  80x24 screen. That is the trade, and it is why this is a deliberate change
  rather than a tidy-up.

- **`yolo` no longer asks before delegating — it tells you.** Choosing `yolo`
  means "run mutating tools without a prompt", and a delegation was the one
  thing it still prompted for: on every `agent` call, a dialog whose only two
  options were "Run with the inherited posture (yolo)" and "Cancel". That is a
  confirmation with no real answer, which is the shape this project already
  removed everywhere else because it teaches you to dismiss the prompts that do
  matter. It now starts the child immediately and writes one line to the status
  line instead — the profile, the posture it runs at, the file it came from, and
  how many tasks it was given — before the child does anything, for as long as
  it runs, and cleared when it finishes. The finished tool card already names
  the posture in its `[agent … · yolo · …]` footer, so that is the part that
  stays in your transcript.

  `auto-accept-edits` and `auto` still ask. The `0.1.0-beta.1` section's own
  entry is corrected in place rather than superseded — that section has not
  shipped, and correcting an unreleased line rather than recording a removal is
  the convention `92b3f35` set for exactly this case.

  What does not change: the child is still clamped to at most your own posture,
  the guardrail still hard-blocks catastrophic patterns inside it, the
  per-prompt and per-session delegation caps still apply, the status line still
  shows it running, and a *project-local* profile still cannot widen itself.
  What you lose is the chance to say "Cancel" to one specific spawn before it
  starts; Ctrl+C and shift+tab are still live. See ADR-0231 and
  [#196](https://github.com/handochan/aelix-ai/issues/196).

- **The `@` menu uses the `fd` Aelix downloaded, and stops pretending both
  enumerators agree.** `@` completion enumerates the tree with `fd` when it can
  find one and with a plain `os.walk` when it cannot, and the code said the two
  "produce the same set of matchable paths on every machine". They never did.
  `fd` hides what git ignores and the walk does not, so what the menu offered
  depended on whether you had `fd`. Worse, it looked only on `PATH` — never at
  `~/.aelix/agent/bin/fd`, the copy Aelix downloads the first time you use
  `find` — so the machine this was measured on had `fd` and used the walk
  anyway; because that checkout keeps nine agent worktree copies of the repo,
  the menu was matching 12618 paths instead of 1482, spending three of the eight
  rows for `@completionpy` on stale copies and never offering the file actually
  named. It now looks there **first**, the same order `find` and `grep` use, so
  the two run the same `fd` — which also means that on a machine
  with no `fd` the menu can narrow part-way through a session, once your first
  `find` has fetched one. On a clean clone of this repository both arms offer the
  same list: the difference is as large as whatever git ignores that the shared
  exclude list does not already name.

  Three smaller disagreements went with it. The shared exclude list now really
  applies to both arms — without `fd` the menu had been offering the `.git`
  pointer file in every worktree, and any file named `build`/`dist`/`node_modules`;
  the cost of that consistency is that a root-level `build` *script* stops being
  fuzzy-matched on the walk arm too, matching what `fd` already did — the plain
  `@` listing still offers it, on either arm. `fd` was asked for files and
  directories only, so it dropped every symlink the walk listed — fixed, and the
  two sets now match exactly on a symlink tree. And one
  disagreement is bigger than it looks and is documented rather than fixed: both
  enumerators stop after 20 000 paths, and the files git ignores count toward that
  limit on the walk — and, since #238, on the `fd` arm whenever **Gitignore in @
  menu** is off — so in a repo with a large ignored build tree either arm can run
  out of budget inside it. On the walk that means never reaching your real source
  directories at all (measured: an ignored 22 000-file `target/` next to twelve
  real source files gave the walk two menu rows where `fd` — with the toggle on,
  and so never entering `target/` — gave twelve). On the off `fd` arm it means the
  kept set stops being stable, and where the ignored files are spread over many
  directories rather than one tree it can drop real directories too. An
  undecodable filename also still reaches `fd`'s list as `�`; what the walk
  arm does with it is untested (no filesystem here can hold such a name). Nothing
  you type is ever passed to the subprocess, and `fd` is still never required.
  See ADR-0193 and [#231](https://github.com/handochan/aelix-ai/issues/231).

- **A `!command` in `models.json` or `auth.json` that tries to prompt now fails
  at once, with a reason, instead of stalling for ten seconds without one.** A
  `!command` runs in a process group of its own, which is never the terminal's
  foreground group, so the kernel STOPS it the moment it reads the terminal
  (`SIGTTIN`) or turns echo off (`SIGTTOU`) — unless it blocks or ignores that
  signal, which POSIX permits. Aelix now watches for exactly those two stops
  while it waits, ends the tree when one arrives, and says what happened:
  `The command stopped reading the terminal (SIGTTIN): …`. Measured under a real
  pty on macOS **and on Linux**, the resolver detects the stop and kills in
  0.052–0.059 s; what you wait for is that plus Aelix's own startup. Before this,
  the same helper burned the full ten-second timeout and the message said only
  `Failed to resolve API key for provider "x" from shell command: …`, with
  nothing about a terminal in it.

  Who benefits: helpers that read the terminal **themselves** — `ssh` or
  `ssh-add` with no askpass, `sudo`, a git credential helper that does
  `read </dev/tty` or `stty -echo`, and `gpg --pinentry-mode loopback`
  (measured 0.357–0.359 s).

  Two things this does not catch, said here rather than found later. A
  passphrase prompt mediated by `gpg-agent` — the normal `gpg` and `pass`
  architecture — is invisible to it, because the agent is a daemon in its own
  session and it is the agent that forks `pinentry`, where no job-control check
  applies; measured with gnupg 2.5.22, `!gpg -d` and `!pass show` still take the
  full ten seconds with no named cause. And "the helper stopped before printing
  anything" describes the shell helpers that were measured: a background process
  can still *write* to the terminal, and `sudo` and `openssl` print their prompt
  before failing. An askpass program or a GUI prompt that nobody answers still
  costs the whole timeout too — it never reads the terminal, so it never stops.

  The process group itself does not change: `setsid` would be worse, not better.
  Measured, a `setsid` helper that opens the terminal by path faces no
  job-control check at all and took the line the user had typed at Aelix's own
  prompt — silence in place of a visible stop. On Windows there is no background
  process group and no stop to detect: a helper that reads the console directly
  can still prompt there and still burn the whole timeout unanswered, and nobody
  has watched that happen. See ADR-0238 and
  [#226](https://github.com/handochan/aelix-ai/issues/226).

### Added

- **102 models the catalog was missing since 2026-08-19**, refreshed from
  models.dev with `scripts/refresh_catalog.py` (1427 → 1529 rows across the
  same 35 providers; nothing removed, nothing already shipped changed —
  measured by diffing the two JSONs row by row). Selectable now on their
  direct providers: `claude-fable-5-1` (anthropic), `gpt-6-astra` (openai),
  `gemini-3.8-flash` (google), and on github-copilot `claude-fable-5.1` +
  `gemini-3.8-flash` under the Copilot conventions the pin tests enforce
  (200K cap on Claude, zero per-token cost). 399 upstream rows were refused
  by the script's transport rule, as before. **What a refresh cannot do:**
  models.dev carries no `thinkingLevelMap`, so every added row arrived
  without one — and a reasoning model without a map has no `xhigh`.
  `claude-fable-5-1` would have clamped `xhigh` to `high` while
  `claude-fable-5` beside it kept `xhigh`. Nine flagship rows got their
  predecessor's map copied by hand (fable-5-1 ← fable-5, gpt-6-astra ←
  gpt-5.5, gemini-3.8-flash ← gemini-3.5-flash, on each provider where the
  predecessor had one) and are pinned equal to it in
  `tests/test_catalog_corrections_are_pinned.py`; the 12 rows whose
  predecessor has no map either (bedrock, the aggregators) were left as
  upstream gives them. The refresh pipeline itself (#172) is still not built —
  this is the manual path it will replace.
- **`/settings` can put the git-ignored files back in the `@` menu.** Since the `@`
  menu started using the `fd` Aelix downloads, it stops offering what git ignores —
  which is what most people want and is wrong for anyone whose build output,
  vendored tree or worktree copies are the files they keep typing `@` at. The new
  **Gitignore in @ menu** row switches it off. Off, `fd` is asked for `--no-ignore`,
  which lifts `.gitignore` (root and nested), `.git/info/exclude`, `.ignore`,
  `.fdignore`, your global ignore file, and the same files in directories *above*
  the one you are in; the shared exclude list — `.git`, `node_modules`, `.venv`,
  `__pycache__`, `dist`, `build` — still applies, and so does the 20 000-path limit,
  which the wider list now spends on ignored files too, so a large ignored population
  can push the menu past that limit — and past it, which paths you get is decided by
  `fd`'s parallel walk: not stable between runs, and in a checkout with many ignored
  directories able to drop real ones. That is why it defaults to **on**. It applies
  immediately: the next `@` you type uses the new setting, no restart. With no `fd`
  — offline mode, Termux, or simply a session where the model has never run `find`
  — the menu already offers everything, so the row changes
  nothing until an `fd` exists; when one appears mid-session the setting you chose
  is honoured at the next `@`. Measured on the checkout this was built on: on, 1 498
  candidates; off, 12 630 — exactly what the no-`fd` fallback offers there. See
  ADR-0193 and [#238](https://github.com/handochan/aelix-ai/issues/238).

- **AUTO mode can read PowerShell and `cmd`, so it stops prompting for every
  line on Windows.** Until now the gate parsed every command with a *bash*
  grammar and then, on any shell that grammar does not describe, downgraded its
  own verdict to a prompt. That is why nothing was ever mis-run there — and also
  why AUTO on Windows was indistinguishable from not shipping it. `pwsh`,
  `powershell` and `cmd.exe` now get a classifier that reads their own syntax:
  `Get-ChildItem`, `dir`, `type a.txt`, `echo hello` and `date /t` run without a
  prompt, while `Remove-Item -Recurse -Force C:\`, `del /s /q C:\Windows`,
  `date 01-01-2030`, `iex`, `Set-ExecutionPolicy Bypass` and a redirect into
  `C:\Windows` are blocked outright rather than merely asked about.

  Reading is not the same claim as harmless to read: `type`, `Get-Content`,
  `Select-String` and `findstr` still prompt when the target is a credential
  store — `.ssh`, `.aws`, `.gnupg`, `.docker`, `.kube`, `.azure`, gcloud,
  Terraform, `gh`, `.git-credentials`, `.env`/`.env.local`, a private key — or a
  UNC path, where the read itself is an outbound SMB connection to whatever host
  the command names.

  The bash classifier's DENY is kept underneath as a floor, so `rm -rf /`,
  `find . -delete` and `curl … | sh` still block under every shell — including
  the ones this adds. The ALLOW lists are deliberately incomplete: an unlisted
  name, an unread argument or an unparsable line costs a prompt, which is the
  direction that is safe to be wrong in. Windows remains EXPERIMENTAL — this is
  covered by tests, and nobody has yet run it on a real Windows box. See
  ADR-0237 and [#204](https://github.com/handochan/aelix-ai/issues/204).

- **422 more models, including the ones released this month.** The catalog had
  not been refreshed since it was ported: of the models upstream lists with a
  2026-04 release date it carried 63%, and of the 2026-08 ones, 5%. It now
  ships 1427 models across the same 35 providers — `grok-4.6`, `qwen3.8`,
  `deepseek-v4-flash-0731`, `deepseek-v4-pro-0813`, `glm-5.3`, `kimi-k3` and
  the rest, each on the providers that actually serve it.

  Nothing that was already in the catalog changed. The refresh
  (`scripts/refresh_catalog.py`) can only append — the values a maintainer
  corrected by hand are not something it is careful about, they are outside
  what it can write.

  401 upstream models were deliberately left out. 325 of them cannot call a
  tool, and this is a coding agent: a model that cannot call a tool cannot edit
  a file, and offering it would sell you a session that fails on its first
  action. The other 76 have no transport we could establish without guessing,
  and a model that appears in `/model` and then cannot reach its provider is
  worse than one that is absent. See ADR-0232.

- **A stale `uv.lock` now fails the build instead of a stranger's install.**
  A dependency added to a `pyproject.toml` without re-running `uv lock` used to
  pass every test — the suite imports from a dev environment where the package
  was already present, and nothing in the repository read the lock at all. The
  cost landed on whoever built from a clean checkout, as a missing module at
  import time. The gate compares the lock against all five manifests in both
  directions and names `uv lock` as the fix. See ADR-0233.

- **Aelix tells you when a newer release exists.** At most once a day, an
  interactive launch reads a static file on this project's own GitHub Pages
  site and, if there is something newer, prints one line naming the version and
  **the upgrade command for how you actually installed it** — re-running
  `install.sh`, `uv tool upgrade`, or `pipx upgrade`, whichever applies. A git
  checkout is detected and left alone.

  There is no universal upgrade command, and the wrong one is destructive:
  `uv tool install aelix@latest` — uv's own suggested remedy — resolves the
  PyPI name reservation, finds no entry points, and **removes the tool**, so a
  user who follows it loses their install. That is why the command is detected
  rather than printed from a constant.

  The request carries no version, no operating system, and no identifier: the
  server learns an IP address and nothing else. It creates no telemetry sink.
  Turn it off in `/settings` → *Check for updates*, or skip it with `--offline`
  / `PI_OFFLINE=1`. Every failure — offline, DNS, timeout, a malformed
  response — is silent, because a startup complaint about a failed *check* is
  worse than no check. Nothing is downloaded or executed; installing the update
  is a command you run yourself.

- **Four current flagship models are selectable** — `claude-opus-5` on both
  `anthropic` and `github-copilot`, and `gemini-3.6-flash` / `gemini-3.7-flash`
  on `google`. Added by hand from the canonical source, following the
  conventions the catalog already encodes: the Copilot row keeps the documented
  200K seat cap and zero cost (a Copilot seat is a subscription, not metered),
  while the direct Anthropic row keeps its full 1M context. A refresh pipeline
  is still open (#172).

- **A bundled `general-purpose` agent profile.** Alongside the read-only
  `explorer`, a default install now ships a full-toolset worker that can read,
  edit, and run commands to take on a delegated task whole — so basic
  multi-agent delegation works out of the box for real work, not only for
  read-only investigation. It is a `leaf` profile (it does not delegate further)
  and inherits your approval policy (its edits and commands go through the same
  consent you are under). Delegation stays off by default (`--agents` /
  `features.agents`); user and project profiles still shadow it by name.

- **An installed extension's `aelix-plugin.toml` is now read** (#91, ADR-0204).
  A package that declares an `aelix.extensions` entry point — what
  `aelix extension install <pkg>` gives you, and how the marketplace ships
  packs — previously had its manifest ignored entirely, so every
  `contributes.*` family it declared (tools, commands, themes, TUI widgets,
  MCP servers, hooks) was silently inert. The manifest is now resolved from
  the distribution's installed metadata **without importing the package**, so
  an installed pack's contributions reach the runtime and its capability
  declarations are enforced before any of its code runs.
- **Optional `aelix.manifests` entry-point group** (#91). Declare
  `<same-name-as-your-aelix.extensions-entry> = "<dotted.package>"` to point
  the host at the directory holding your `aelix-plugin.toml`. Needed only if
  the manifest does not sit in the entry module's own package, or if your pack
  is a single module and so has no package directory. Purely optional: a pack
  that omits it behaves exactly as it does today.
- **`aelix extension verify`** (#91, ADR-0207) — an import-free command that
  reports, per installed `aelix.extensions` endpoint, whether its manifest will
  bind, and exits 0 only when every reported endpoint is `BOUND` (non-zero for
  `ABSENT` / `MALFORMED` / `MISPLACED` / `FENCED` / `UNPROVEN` / `UNTRUSTED` /
  API-incompatible). Built for a catalog-submission CI. It never imports the
  pack. The `ABSENT` report names the usual cause — a **setuptools default build
  silently drops `aelix-plugin.toml` and `themes/*.toml` from the wheel** — and
  the fix. `aelix extension list` now annotates the same verdict, so
  "installed, no manifest, declarations inert" is visible instead of silent.
- **A buildable starter extension** at
  `packages/aelix-coding-agent/src/aelix_coding_agent/examples/starter/` (#91,
  ADR-0207) — a hatchling package whose wheel actually ships its manifest and
  theme — plus a new "Packaging your extension" section in the authoring guide
  naming both build backends and the setuptools `package-data` requirement.
- **Catalog policy** (#91, ADR-0207): a pack listed in the official catalog must
  yield a **bound** manifest (`aelix extension verify` exits 0) — it need not
  declare any contribution, but a manifest-less entry-point pack is not
  catalog-eligible. An arbitrary `pip install <pkg>` pack keeps the manifest
  fully optional. `BOUND` is an auditability floor, not a safety verdict.
- **`--trust-extension-path` is documented** (#91). The flag shipped with zero
  doc coverage, which matters because it is the escape hatch for the one
  workflow every extension author uses: `pip install -e` loads a pack
  **without** its manifest, so declarative contributions silently vanish while
  imperative registration keeps working. The authoring guide now explains the
  downgrade, the flag (it takes the PEP 503 *distribution* name, is repeatable,
  and persists nothing), and the fact that a newly installed pack's
  `contributes.mcp_servers` needs a process restart — the manifest scan runs
  once at startup, before the first harness build.
- **"Upgrading / uninstalling" is documented** in the README and the
  getting-started guide. Neither word had appeared anywhere across README,
  README.ko, getting-started, `install.sh` or `RELEASING.md`.
- `aelix --help` now lists `extension verify`. It shipped in
  `aelix extension --help` but not in the top-level `Subcommands:` block, so
  the one command that explains why a manifest did not bind was
  undiscoverable from the main help.
- **`settings.json` accepts a `thinkingBudgets.xhigh` key** alongside the other
  four, matching the `xhigh` budget tier added below. Nothing writes it for
  you, on a fresh install or an existing one, and a file that has only `high`
  still reads and rewrites with only `high`. Like the other four it is **schema
  only today**: nothing in Aelix reads `thinkingBudgets` yet. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

### Changed

- **Three behaviour changes for packs installed via `aelix.extensions`** (#91).
  All three follow from the manifest being read where it previously was not,
  and each reports what to do:
  - A pack declaring `[[contributes.hooks]]` without
    `capabilities.shell_exec = true` is now **refused**, with none of its code
    executed. Add the capability if the pack genuinely needs to run
    subprocesses.
  - A pack whose only `[activation]` trigger is `on_command` is now
    **deferred**: its `setup()` runs on first use of one of those commands
    instead of at startup. Set `on_startup_finished = true` to keep the old
    behaviour. A warning naming the plugin and the escape hatch is logged when
    this happens.
  - A pack whose `[plugin.api] min_level` exceeds the host's API level is now
    **refused** instead of loading and misbehaving later. Upgrade aelix, or
    install a build of the plugin for this API level.
- A pack that is refused at load time no longer has its declared MCP servers
  started (#91). Previously the load-time refusal and the MCP gate keyed on
  different capability flags, so a denied plugin's `[[contributes.mcp_servers]]`
  were still spawned or dialled at every startup.
- An extension whose `aelix-plugin.toml` fails to parse cannot be installed
  via an entry point and silently do nothing: the pack loads **without** its
  manifest and reports an error naming the absolute path of the file. If the
  distribution ships a manifest the host could not attribute to the entry
  module, the error names that file too.
- Development installs (`pip install -e`) cannot be proved from installed
  metadata, so a pack installed that way loads without its manifest and says
  so on every start. Install the pack normally to exercise its manifest.
- **`install.sh` pins the version it installs** to the one named by the
  checksum-verified `SHA256SUMS` manifest. `--find-links` only *adds*
  candidates and the PyPI index stays enabled, so requesting the bare name
  `aelix` let an index release outrank the local wheels — the checksum gate
  could verify artifacts the next command discarded. It also names
  `GITHUB_TOKEN` and `AELIX_VERSION` when the anonymous GitHub API call fails,
  which on a shared NAT or CI address is usually a 403 rate-limit rather than a
  missing repository.
- **The release tag gate rejects dot-form pre-releases.** `v0.1.0-rc.1` is
  accepted; `v0.1.0.rc1` is refused. The whole pipeline decides "pre-release?"
  by testing the tag for a hyphen, so the dot form would have been treated as
  GA — marked a full GitHub release and carried into an irreversible PyPI
  upload. The workflow now also asserts, before building, that the tag
  normalizes to `pyproject.toml`'s version under PEP 440, and that the SBOM
  glob matches exactly one file.
- **Security and `--offline` claims now match the code.** The README, the
  Korean README and the website described extensions as *being verified* with
  Ed25519 provenance; in fact no first-party keys are provisioned and an
  unsigned pack is accepted unless you install with `--require-signature`.
  `--offline` was called "air-gap mode": it skips the `rg`/`fd` download, the
  extension-catalog fetch and index-less pypi installs, and does **not** affect
  provider or model calls. The `rg`/`fd` auto-download is now disclosed
  explicitly in the README and `SECURITY.md`.
- **Provider documentation is labelled.** Copilot Enterprise is marked
  unverified (live testing covered a paid individual seat and a Business seat
  only). The providers guide gained an adapter-coverage table: the bundled
  catalog spans nine wire protocols and this build ships six, so
  `amazon-bedrock`, `azure-openai-responses` and `mistral` cannot run — they
  are hidden from `--list-models` and `/model` rather than failing at turn one.
  `mistral` had been listed in the guide's primary environment-variable table
  and `azure-openai-responses` among "other supported providers".

### Removed

- **The `[images]` extra and `tui/images.py`.** The extra installed
  `rich-pixels` and the README offered `AELIX_EXTRAS=tui,images` for "inline
  terminal image rendering", but no production code path ever reached the
  renderer: its only importers were two test files. Installing it bought a
  dependency and zero behaviour. Inline images are not a shipped feature; see
  ADR-0223 for what wiring one would take, including the `term-image` /
  `Pillow>=11` conflict that leaves only the Unicode tier reachable.

- `--verbose`, `--no-themes` and `--no-prompt-templates` (and the `-np`
  spelling). All were parsed into `Args` fields that nothing outside
  `cli/args.py` ever read, while `--help` advertised them as working features.
  Passing one is now a hard argument error rather than a silent no-op: an
  unrecognised `--name` otherwise falls into the unknown-extension-flag branch,
  which swallows the following token as the flag's value, so
  `aelix --verbose "my prompt"` would have run with no prompt at all and no
  error. The positive forms `--theme` and `--prompt-template` are unaffected,
  as is `--no-skills`.

- **`aelix_coding_agent.tools._process_tree`.** It was a re-export shim, left
  behind when the containment primitive moved to `aelix_ai.utils._process_tree`
  so that the `aelix-ai` sites could share it (#202). Its last three importers
  — the `bash` tool, the `rg`/`fd` helper and the win32 process-tree test
  (`tests/tools/test_process_tree_win32.py`) — name the primitive directly now
  (#222); the remaining references were prose (`reaper.py`, ADR-0238,
  `SLICE-STATUS.md`) and moved with it. The path is private and undocumented,
  so no supported surface changes; this line exists because it was still
  importable, and an out-of-tree caller that reached for it deserves a written
  signal rather than an `ImportError` to bisect. `kill_process_tree` itself is
  unchanged and is still exported from `aelix_ai.utils._process_tree`.

- **`shell_basename` from `aelix_coding_agent.tools.bash`.** The Windows shell
  chain moved down to `aelix_ai.utils._shell` so a `models.json` / `auth.json`
  `!command` and the bash tool resolve through one primitive (#227), and
  `shell_basename` went with it — along with `_command_flag_for`,
  `_POWERSHELL_NAMES`, `_CMD_NAMES`, `_VERSION_SUFFIX_RE` and the three
  command-flag constants, all of which were private. Its three in-tree callers
  (`dialect_for_shell`, `is_classifiable_shell`, and the PowerShell classifier's
  name normaliser) import the new module directly. It was never in that module's
  `__all__`, so no supported surface changes; this line exists because it was
  still importable under a non-underscore name, and an out-of-tree caller that
  reached for it deserves a written signal rather than an `ImportError` to
  bisect. There is deliberately no re-export shim — the only lint-clean one
  would have to add the names to `__all__`, declaring as public a surface that
  never was; ADR-0238's #227 amendment records the reasoning. `ShellConfig` and
  `command_flag_for` still answer from the old path, but only because `bash.py`
  now imports them for its own use; import them from `aelix_ai.utils._shell`.

### Fixed

- **The context meter moves during a turn, and after `/model`.** The footer's
  `◔ 42% · 84K/200K` sat on the previous turn's number for a whole
  ten-minute multi-tool turn, and `/model` changed the denominator without
  recomputing anything. The refresh already ran once per provider round-trip —
  but each one estimated over a message list the harness does not extend until
  the turn ends (`core.py:4598`), so they all painted the same pre-turn figure,
  which on the first turn of a fresh session is literally `◔ 0%`. The
  mid-turn number now comes from the assistant message the provider just
  finished — its own reported usage, the same term the turn-end estimate
  anchors on, so the meter no longer jumps at the boundary — and every model
  change refreshes it through the harness's `model_select` hook, which covers
  `/model`, the picker, the post-`/login` pick and an extension's `set_model`
  alike. The meter's own per-round-trip stats read is now **skipped**
  while a live number is held, because it is measurably worse than what is
  already on screen: that removes work rather than adding it. It halves the
  per-round-trip reads rather than eliminating them — the `/stats` history row
  on the same event still takes one (the in-memory half of a read measured
  0.035 ms at 200 messages and 0.349 ms at 2000, before the session-branch disk
  read a persisted session adds on top), and the live paint costs **1.13 µs**.
  **What you give up:** the mid-turn figure counts the last round-trip only, so
  tool output produced *since* that response is not in it until the next one —
  a floor that steps, not a continuously rising bar; the optional
  input/output-token and cost segments (default-OFF) still update at turn end
  only, so mid-turn they sit one turn behind the context% beside them; and after
  a compaction the segment's deliberate blank window now ends at the first
  post-compaction assistant response instead of at the next turn end. See
  [#249](https://github.com/handochan/aelix-ai/issues/249), ADR-0116, ADR-0121
  §4.
- **A resumed session comes back at the thinking level you left it at.** Two
  things were wrong and either alone was enough. `/thinking high` outside a turn
  wrote **nothing** to the session file — the harness queued a
  `thinking_level_change` only while a turn was running, so a session whose level
  came from the picker held zero control entries and `build_context()` reported
  `off`. And when a session *did* carry the entry, neither place that builds a
  harness from a resumed session read it: a freshly built harness measured `off`
  against a session context saying `high`. So `--continue`, `--resume`,
  `--session`, `--fork` and the in-session `/resume` all started from scratch —
  and `/resume` additionally threw away the level set in that very process,
  because Aelix rebuilds the harness on a session swap (Pi does not, which is why
  Pi never needed this). The level is now written when it changes, restored at
  both seams, and clamped to the resumed model: `xhigh` on a `high`-max model
  comes back `high`, not `off`. An explicit `off` stays `off` —
  `defaultThinkingLevel` no longer overwrites it, because the TUI seed is now
  *told* a level was applied instead of guessing from the value. `--thinking`
  (and an agent profile's `thinking:`) still wins over the session at launch, and
  it is now written down too, so `aelix --thinking high` followed by `--continue`
  comes back at `high` instead of `off`; the session wins over
  `defaultThinkingLevel` (the ADR-0196 order). One place that order inverts: an
  in-session `/resume` takes the target session's own level over the launch flag,
  because you asked for that session.
  **What you give up:** the session file grows one control entry per level
  change, one the first time a session with no level of its own is opened while
  `defaultThinkingLevel` is set (after which that session out-ranks a later
  change to the global default), one the first time such a session is launched
  under `--thinking`, and one per `/new` or `/resume` into a session that has no
  level of its own. The model recorded in a session is still **not**
  restored — that is the follow-up. `/reload` is a third rebuild seam and is
  untouched here: it still drops the level. See
  [#198](https://github.com/handochan/aelix-ai/issues/198) and ADR-0239.
- **The `/settings` row for the update check could not be switched off, and the
  one for skill commands claimed a restart it never needed.** Selecting **Check
  for updates** printed `✖ Check for updates: 'check_for_updates'` and changed
  nothing: the row shipped as a toggle whose key was in neither of the two tables
  that dispatch a toggle, so the first keypress raised a `KeyError` the menu
  swallowed into a red line, and the only way to turn the check off was to
  hand-edit `settings.json` — which is not what either README said. It toggles
  now, and no key is needed in `settings.json` for the fix to reach you: a global
  pin is simply overwritten, so this is every install, not only fresh ones. A
  test now asserts that every boolean row's toggle actually **succeeds** — ten of
  the twelve were already driven, but the loop only checked the mirror payload,
  so a swallowed `KeyError` read as a pass. A boolean row also re-reads the value
  it wrote before printing it, so the one case it still cannot change — a project
  `.aelix/settings.json` carrying `checkForUpdates`, which wins over the global
  file this setting is written to — now says so instead of confirming a change
  that did not happen, and the global value it did write is flushed rather than
  left pending behind that message. **Thinking blocks** and **Compaction
  summary** under such a pin keep flipping for the session, as before, and now
  say that is all they do. Going the other way, **Skill commands** promised
  "takes effect after you restart aelix" while the gate is re-read on every line
  you type; it is now marked live and says so. **What you give up:** nothing at
  runtime — the `/skill:` gate covers the TUI surface, not the command list an
  RPC client is offered. See
  [#244](https://github.com/handochan/aelix-ai/issues/244) and ADR-0229.
- **Windows tool output is readable again, and the model is told which shell it
  is on.** Every child process this agent decodes itself — the bash tool, `!`
  commands, hooks, `rg`/`fd`, subagent stderr — was decoded as UTF-8 with
  replacement, so on a Korean Windows box, where PowerShell writes CP949,
  `위치 줄:1` arrived as `��ġ ��:1` — arithmetic, not a guess, and not even
  recognisable as corruption, because `c4 a1` is a *valid* UTF-8 sequence.
  Output is now decoded **run by run**: each stretch of non-ASCII bytes takes
  UTF-8 strict first, and only a run UTF-8 rejected is offered to the console
  output code page. So a UTF-8 run standing next to a legacy-code-page run in
  the same buffer decodes correctly on both sides — measured, `한글` beside a
  CP949 `오류` comes back as `한글 오류`, where flipping the whole buffer to
  CP949 would have returned `�븳湲� 오류` and destroyed the half that was
  already right. With **no** separator between them the two halves are one run,
  and there the promise is narrower than it sounds: the split saves the UTF-8
  half only when the code page rejects the run as a whole. `한글` glued to a
  CP949 `오류` still comes back `한글오류`, but `문자` glued to the same `오류`
  reads `臾몄옄오류` — CP949 accepts all ten bytes, so it takes them, and the
  half `U+FFFD` used to get right is now confidently wrong. Measured over 4000
  glued pairs per shape, that costs the UTF-8 half 13.3% of the time for one
  character on each side and 38.7% for two. It is left as it is on purpose:
  telling that buffer apart from `치위` — a real CP949 word whose first two
  bytes are also valid UTF-8, and the shape this issue was reported from —
  cannot be done from the bytes. Two
  details are what make the promise true rather than nearly true. CP932/936/949
  /950 use ASCII bytes as DBCS *trail* bytes (measured: 3288 of CP932's 9604
  lead/trail pairs), so a failed run is offered the ASCII that follows it as
  well — without that, Japanese `エラー` came back exactly as mojibake as
  before. And a code page that decodes all 256 single bytes (CP437/850/866 —
  every Western box) is offered nothing at all, because its accepting bytes is
  no evidence about them: not a whole run, and not the stretch inside a run
  that UTF-8 could not begin at. That second half is what the Windows CI leg
  taught us — `b"ok \xff\n"` came back as `ok \xa0`, because CP437 maps `0xFF`
  to a NO-BREAK SPACE, so the marker that says "these bytes were lost" was not
  wrong but *invisible*. The rule as it now stands means something simple and
  checkable: **on a Western Windows box this release changes nothing.** With no
  DBCS page in the chain the decoder is byte-for-byte the `errors="replace"`
  call it replaced — measured over 351 exhaustive windows and 20000 random
  buffers, against six single-byte chains and every combination of the cut-end
  claims: 488 424 decodes, none of which differs.
  And where the buffer's own END is a cut rather than something the child
  chose — a timed-out or aborted command, a stderr ring that scrolled, a child
  that died mid-line — the severed character stays a visible `U+FFFD` instead
  of being spelled by the code page: measured over 24 console lines cut at all
  297 offsets inside a character, 19 read as a confident wrong character on
  CP949 before that rule and none after. A buffer is only ever repaired at an
  end its reader really can cut, which is why a bash result claims its tail and
  not its head.
  A PowerShell that Aelix spawns itself is additionally asked for UTF-8
  (`[Console]::OutputEncoding`), but that preamble runs only *after* the shell
  has parsed your command — measured on pwsh 7.6.5, a parse error discards it
  unexecuted — so the decoder is what covers children that are not ours **and
  our own shell's parse errors, which is the failure this issue reported**.
  `cmd` is asked for nothing at all, and that is a correction made after the
  Windows CI leg ran: an earlier build of this release prepended
  `chcp 65001 >nul&` there, and on windows-latest (run 34272507388, py3.11 and
  py3.12) that prefix stopped an **unquoted space-containing executable path
  with no arguments** from running — `cmd` answered `'C:\…\a' is not
  recognized as an internal or external command`. `cmd /c` keeps the quotes
  `subprocess` puts around such a command only while the whole text between
  them is the name of an executable file, so any prefix loses them, and there
  is no spelling of a preamble that is not a prefix. The decoder covers those
  children instead. On macOS and Linux the decoded bytes are
  unchanged, provably: with no fallback codec the new path is the old call, and
  the test pins that through the platform default rather than by passing an
  empty chain. Separately, the bash tool now tells the model which shell
  actually resolved here, so it stops sending `a && b` into a Windows PowerShell
  parser; the sentence advises `;`, which is correct on 5.1, 6 and 7.
  **What you give up:** on a **CJK** Windows box a child writing genuinely
  binary bytes may decode as legacy-code-page text instead of `U+FFFD`, where
  those bytes happen to be a valid DBCS sequence (a buffer holding a NUL byte is
  exempt — that is a binary file, and it takes the old replacement path whole).
  On a **Western** box nothing decodes as legacy text, and that is a capability
  withheld rather than a regression: a German or French console app writing its
  OEM page reads as `U+FFFD`, exactly as it did under `0.1.0-beta.1`, whose
  `errors="replace"` marked the same characters — measured over 16 accented
  console lines, 16 of 16 marked now, 16 of 16 marked in beta.1, and the two
  decodes equal on all 16. What is given up is what an earlier cut of *this*
  release briefly did with those lines: offer them the OEM page, which read all
  16 correctly. It is withheld because the same 16 lines came back *confidently
  wrong* 16 times out of 16 when the child wrote the ANSI page instead
  (measured: cp1252 bytes read through cp850) — and on a Western box ANSI and
  OEM always differ (1252 against 850 or 437), where in the CJK locales this
  was reported from they are the same number. The single-byte answer is a coin flip the bytes cannot call, and it
  is the same guess that spelled a binary `0xFF` as an invisible character.
  Where a buffer's own end is a cut, the **head** claim cannot tell a severed
  UTF-8 tail from a whole legacy character whose two bytes both lie in
  `0x80-0xBF` — 31.4% of CP949-encodable Hangul, `가` among them — so the two
  readers that claim a cut head (subagent stderr, the RPC stderr window) lose
  such a character to `U+FFFD`, where this same release's *unclaimed* sites keep
  it. Not a loss against the previous release: both of those readers were
  `.decode("utf-8", errors="replace")` before this change and answered `U+FFFD
  U+FFFD` for CP949 `가` too (measured). Accepted because the
  same claim removes a confidently wrong head from 120 of 192 in-character head
  cuts on the same Korean corpus, and because only those two readers make it. A
  CJK run that happens to
  *open* with a valid UTF-8 pair is still read as CJK, which is right on the box
  this was reported from and wrong for a genuine one-character UTF-8 prefix; and
  the first command Aelix runs under PowerShell — the shell the Windows chain
  resolves first — switches that console to 65001 for the session
  (`SetConsoleOutputCP`, via `[Console]::OutputEncoding`); under `cmd` nothing
  does, so a `cmd` child's output stays on the console page and takes the
  decoder's route. **Against `0.1.0-beta.1` that is no change**, which is the
  same promise as above: beta.1 decoded those bytes `utf-8`/`errors="replace"`,
  so an OEM-page character read `U+FFFD` there too (measured: a cp850 `Grüße`
  comes back identically through both paths, as do 16 of 16 accented
  German/French console lines). The price the correction above books is
  therefore against an **intermediate build of this release** and not against
  anything published — while the `cmd` preamble existed those characters came
  back as UTF-8, and now they do not. Paid because one mojibake line is worth
  less than a command that does not run. MCP stdio servers are deliberately
  untouched: the `mcp` SDK decodes those, not this repo. `!command` credential
  resolution keeps its argv byte for byte — it gets the decoder and no preamble,
  because its stdout *is* the key. See
  [#239](https://github.com/handochan/aelix-ai/issues/239) and ADR-0238.
- **`xhigh` builds a bigger thinking budget than `high` — on the ten rows that
  can still receive one.** On models that think with a Claude-style token
  budget the two levels built the same request: measured on
  `anthropic/claude-opus-5`, both sent `thinking.budget_tokens: 16384` byte for
  byte while the optional 🧠 thinking-level statusline segment said `xhigh`.
  `xhigh` is now a budget tier of its own at **32768**, twice `high`.

  **What that changes on the wire, enumerated from the catalog this release
  ships: 21 rows.** Twenty-three rows offer `xhigh` and take the budget path;
  on 19 of them (output cap 128000) and on 2 more (`github-copilot`'s
  `claude-opus-4.8` and `claude-opus-5`, cap 64000) the request goes from
  `budget_tokens: 16384` to `32768`. Counted against the catalog AFTER the
  same release's refresh (#172), which is what ships: on `main` the numbers
  were 20 and 18, and the refresh added `anthropic/claude-fable-5-1` and
  `github-copilot/claude-fable-5.1` — rows that offer `xhigh` only because
  that refresh hand-copied their predecessor's `thinkingLevelMap`. The
  `github-copilot` rows are derived from the catalog, not measured on the
  wire: reaching that endpoint needs a Copilot seat, and nobody ran it. The
  other two **do not change**:
  `vercel-ai-gateway`'s `openai/gpt-5.2-chat` and `openai/gpt-5.3-chat` cap
  output at 16384, where the rule that leaves 1024 tokens for the visible
  answer shrinks either tier to 15360.

  **On 8 of those 18 the bigger budget never reaches the model — and neither
  did the old one.** The request Aelix builds was sent verbatim to
  `api.anthropic.com` on 2026-09-09, at `high` and at `xhigh`, and all eight
  return `400 invalid_request_error`: *"`thinking.type.enabled` is not
  supported for this model. Use `thinking.type.adaptive` and
  `output_config.effort` to control thinking behavior."* — `claude-opus-5`
  (`req_011CerQpNc69m6CAsK93KroH`, `req_011CerQpLuQC5P5Xaw7MzyVQ`),
  `claude-fable-5` (`req_011CerQpRVUJivivpJ9SmDHp`,
  `req_011CerQpQ3uQqfCeUsKaPg9M`), `claude-opus-4-8`
  (`req_011CerQpUWYMoCu4L9t8WS7w`, `req_011CerQpSw3MHtbeDFKJYEpi`) and
  `claude-sonnet-5` (`req_011CerQpXRAezGeumCSuhkSk`,
  `req_011CerQpVws2QN9E2uMrD6yC`). What is rejected is the `thinking.type`
  value, which is the same on every budget-path level, so **thinking has never
  worked on those four rows at any level** — not just `xhigh`. They take the
  budget path only because `supports_adaptive_thinking` is a literal whitelist
  of `opus-4-6` / `opus-4-7` / `sonnet-4-6`; the same 400 lands on 0985fcf, so
  this release neither introduces nor fixes it, and it is now filed as
  [#258](https://github.com/handochan/aelix-ai/issues/258) with the catalog
  field (`compat.forceAdaptiveThinking`) that already records the right answer.
  `claude-opus-4-7` — a whitelisted id — answered normally at both levels in
  the same run (`req_011CerQpYrVRK4GK5pf5sJ8x`,
  `req_011CerQpdX2jsWYUfDcBa1x6`), so the adaptive path is fine. The four
  `github-copilot` mirrors are the same models behind a proxy and were **not**
  measured.

  So the tier's larger budget is what actually ships on the **ten**
  `vercel-ai-gateway` `openai/gpt-5.2`…`gpt-5.5` rows served over the Anthropic
  Messages API. No request was made against that gateway; there, what is
  measured is the request Aelix builds. Opus 4.6/4.7 and Sonnet 4.6 use
  *adaptive* thinking and were never affected. On a call that carries its own
  `max_tokens` the request's `max_tokens` rises with the budget — with a 32000-token base on `claude-opus-5`, 48384 before and
  **64768** now. What you pass there caps the *visible answer*, not the
  payload: the thinking budget is added on top and the sum clamped to the
  model's own cap, so `max_tokens=16384` at `xhigh` sends `max_tokens: 49152`
  with `budget_tokens: 32768` and still returns you at most 16384 tokens of
  answer. That is unchanged behaviour, now written down on the field itself; no
  in-tree caller supplies both a `max_tokens` and a thinking level today, so
  this is for embedders.
  Asking for `xhigh` on one of the **252 rows that do not offer it** —
  `aelix --thinking xhigh --model anthropic/claude-opus-4-1`, or an agent
  profile's `thinking:` — still sends `high`'s 16384: the Anthropic adapter now
  clamps the level against the model row the way the Google and OpenAI adapters
  already did.
  That clamp has one other **embedder-visible** effect, on a path no CLI flag
  reaches: a literal `SimpleStreamOptions(reasoning="off")` is now clamped to
  `"minimal"` on the two catalog rows that declare `"off": null`
  (`anthropic/claude-fable-5`, `anthropic/claude-sonnet-5`), so those two send
  `budget_tokens: 1024` where they sent **8192** before. `off` as a string
  still does **not** turn thinking off here — `resolve_anthropic_thinking`
  gates on `if not reasoning` and `"off"` is truthy — it just buys a smaller
  budget than it used to. The harness collapses `off` to `None` long before any
  adapter sees it, so `--thinking off` and `/thinking off` are unaffected; only
  a direct caller of the provider API can reach this. The underlying defect is
  cross-adapter — both Google adapters map `"off"` to `"high"`, which is worse
  — so it is left to
  [#259](https://github.com/handochan/aelix-ai/issues/259) rather than patched
  in one adapter, and is characterised in the meantime by
  `test_off_passed_as_a_string_still_enables_thinking`.
  **What you give up:** a top tier that is slower and costs more than it did
  when it was `high` under another name. And if you override `maxTokens` or
  `contextWindow` in `models.json` so an *xhigh-offering* row's effective
  output cap lands between 16385 and 33792, `xhigh` leaves the answer exactly
  1024 tokens and no more. No shipped row that offers `xhigh` is in that range
  (their caps are 16384, 64000 and 128000). What can **no longer** happen is
  the inversion: a higher level never sends a smaller budget than a lower one
  at any cap. See ADR-0135 and
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A very long prompt no longer eats the answer on a self-inconsistent
  catalog row.** On the 46 reasoning rows whose `maxTokens` is at least their
  `contextWindow` — the `fireworks` deepseek/glm ids among them — the output
  cap is computed from what the window has left, so it moves with your prompt.
  Land it just above a thinking tier's budget and the rule that reserves 1024
  tokens for the visible answer did not fire: in the request Aelix builds for
  `accounts/fireworks/models/deepseek-v3p1`, a 583217-character prompt at
  `--thinking high` left the answer **626** tokens. The budget is now capped by
  the room the model's output cap leaves, so the answer keeps its 1024 whatever
  the prompt. No `models.json` override needed to hit the old behaviour, which
  is why this is listed on its own. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A tiny `maxTokens` override turns thinking off instead of building a
  request Anthropic rejects.** Override a Claude-style row to
  `"maxTokens": 1024` and Aelix sent `budget_tokens: 1024` alongside
  `max_tokens: 1024`; at 512 the budget was larger than the whole request.
  The API wants a thinking budget of at least 1024 that is strictly smaller
  than `max_tokens`, so both were 400s waiting to happen — reachable only
  through `models.json`, which checks that your number is positive and nothing
  else. Below **2048** there is no budget that is both valid and leaves the
  answer its 1024 tokens, so the request now goes out with thinking disabled
  and you get an answer. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).

- **A `thinkingLevelMap` override on a Gemini 2.x model no longer crashes the
  stream.** `"thinkingLevelMap": {"xhigh": "xhigh"}` on `gemini-2.5-pro` in
  `~/.aelix/agent/models.json` raised an unhandled `KeyError: 'xhigh'` out of
  the stream factory on both the Generative AI and the Vertex adapter — and
  because it escaped synchronously it never became an error event you could
  read. An effort the budget table does not know now sends `thinkingBudget:
  -1`, the API's *dynamic* budget — thinking stays on and Gemini decides how
  much of it to do. In practice that effort is always `xhigh`: the level is
  already clamped against the model row, so the four table keys cover every
  other spelling. Answering it with the family's `high` row was the first
  revision and was reverted, because for Gemini 2.5 that row already *is* the
  API's ceiling (32768 pro, 24576 flash) — reporting a request Aelix cannot
  honour by spending the most expensive one the family can make. For the same
  ceiling reason Gemini gets **no separate `xhigh` tier**: there is no room
  above `high`. On the Gemini 3 / Gemma 4 `thinkingLevel` rows the same `xhigh`
  instead resolves to `HIGH`, the top of that scale, because `thinkingLevel`
  has no dynamic value to hand back — same rule, different alphabet. See
  [#250](https://github.com/handochan/aelix-ai/issues/250).
- **A program sitting in the folder Aelix is running in can no longer become the
  shell.** The shell chain — what runs a `models.json` / `auth.json` `!command`,
  what the bash tool runs your commands under, and which grammar the AUTO
  permission gate reads them with — resolved its candidates with `shutil.which`,
  and on Windows that searches the current directory **before** `PATH`: read
  from CPython's own source, 3.11 unconditionally and 3.12-3.14 unless a Windows
  environment variable says otherwise, both *even when the search path is passed
  explicitly* — and then measured on CI's `windows-latest` runner under both
  interpreters, where stock `which` handed back the copy planted in the current
  directory. Cloning a repository and starting Aelix inside it was enough for a
  `pwsh.exe` there to be handed your `op read`. The same hole was open on macOS
  and Linux by another route — a `!command` was run by a bare `sh`, and an empty
  or `.` entry in `PATH` means the current directory; measured on darwin, a
  planted `sh` ran instead of `/bin/sh`.

  Aelix now walks `PATH` itself, skipping any entry that is not absolute, applies
  `PATHEXT` the way Windows does, resolves the POSIX `sh` and the bash tool's
  `bash` to absolute paths as well, and puts an existing
  `%SystemRoot%\System32\cmd.exe` ahead of the bare name `cmd.exe`, which
  Windows looks for in the application's own directory and then, unless
  `NoDefaultCurrentDirectoryInExePath` is set, in the current directory — both
  *before* the system directories and `PATH`. That step goes in
  **unconditionally**, so on a stock box, where `%COMSPEC%` already is that same
  path, the chain names one program twice: gating it on `%COMSPEC%` being unset,
  empty or relative would make the floor's presence depend on the very variable
  it exists to survive, and the duplicate costs at most one repeated spawn
  attempt, on the caller that falls through, after an attempt on an
  existence-checked path that already worked. When `%SystemRoot%` is unset,
  empty, or set to anything that is not an absolute path, the step falls back to
  the stock `C:\Windows` rather than to the bare name — a fixed system path,
  not one your working directory can influence, which is exactly what a relative
  `%SystemRoot%` resolves against, and the same default Aelix already uses to
  find `taskkill.exe`. CPython hardened its own `shell=True` spawn the same way
  (gh-101283); Aelix goes further and **drops** a
  relative `%COMSPEC%` instead of passing it on, because CPython's protection for
  that case is `executable=`, which a site that names a shell rather than
  spawning one cannot use.

  **What you give up:** a shell reached through a relative `PATH` entry (`.`,
  `bin`, `node_modules/.bin`) is no longer found — name the directory absolutely.
  On Windows that includes a rooted-but-drive-less entry such as `\tools`, which
  resolves against whatever drive you happen to be on rather than against a
  named one.
  A `$SHELL` you exported yourself is still taken verbatim: it is you naming a
  shell, not Aelix guessing one. Windows behaviour is **reasoned from CPython and
  Microsoft's documented search order and proven only by CI's `windows-latest`
  leg**; nobody has run it on a Windows desktop. See
  [#241](https://github.com/handochan/aelix-ai/issues/241) and ADR-0238.

- **Pressing Esc no longer kills a helper an extension's command left running.**
  `aelix.exec(...)` runs a command's tree contained, and after the command exits
  it keeps reading the pipes for a moment so a process the command backgrounded
  can finish writing. A cancellation that landed in that moment — Esc, ^C, or
  any cancelled turn — still ran the kill ladder, and on Linux and macOS that
  was a group kill aimed at a process this run had already reaped: it killed the
  backgrounded helper in every one of **eight** measured runs (six on macOS, two
  on Linux), while the command itself was reported as the success it was. The
  window was 0.1 s for a quiet helper and up to 2 s for one that kept writing.
  Esc there now ends **that reading** and nothing else: the helper survives,
  exactly as it does when you do not press Esc. One race is left, narrowed
  rather than closed — a cancellation that arrives between the command's own
  exit and this run noticing it can still reach the helper. That gap is under
  a millisecond ordinarily, but up to ~54 ms when the call was given a
  `timeout_ms`, because the wait polls at a 0.05 s cap: measured through
  `aelix.exec` with `timeout_ms`, an Esc 2 ms after the exit still killed the
  helper 6 times in 6, 1 in 6 at 20 ms and at 50 ms, and none at 80 ms.
  ADR-0238 records it as narrowed, not closed. The cancellation itself is
  unchanged — it is still what you get back, and the command's output is still
  discarded with it. See ADR-0238 and
  [#230](https://github.com/handochan/aelix-ai/issues/230).

- **A `models.json` / `auth.json` `!command` is no longer assumed to run under
  `sh` on Windows.** It ran `sh -c` on every platform, so on a stock Windows
  install the spawn failed and the value resolved to nothing — which you saw as
  `Failed to resolve … from shell command:`, blaming your command for the
  missing shell. Aelix now runs the shell it can find there, in the order
  `$SHELL` → `sh` → `pwsh` → `powershell` → `%COMSPEC%` → `cmd.exe`, so a box
  with an `sh` on `PATH` and no `SHELL` set keeps running `!command`s under it —
  and **write the command for the shell that will run it**: one that only
  invokes a program (`!op read op://vault/key`) is portable, one that uses shell
  syntax is not. PowerShell runs with `-NoProfile`, because a profile that
  prints a banner would otherwise be prepended to your API key (measured on
  PowerShell 7), and with `-NonInteractive`, so a prompt PowerShell itself
  raises — `Read-Host`, `Get-Credential` — is refused outright: its text can no
  longer end up inside your key, and a masked prompt can no longer sit waiting
  on a console you cannot see (the text half measured on PowerShell 7, the
  masked half read from its sources; Windows PowerShell 5.1, which is what a
  stock box actually resolves, is unmeasured). A helper that opens the console
  itself — `git`, `ssh`, `gpg` — is still not covered. The shell Aelix starts
  there is given no console window of its own. See ADR-0238 and
  [#227](https://github.com/handochan/aelix-ai/issues/227).

- **`auth.json`'s cached `!command` values are trimmed the way
  `resolve_config_value_uncached` always did** (`.strip()`), on every platform: surrounding
- **A spawn that fails before your command starts is now reported as a failed
  command, not as a broken tool.** The bash tool caught exactly two of the ways
  `Popen` fails — the shell is missing, or a path component is not a directory.
  A `$SHELL` (or a `shell_path` setting) naming a file that *exists* but is not
  a runnable program took neither branch and escaped, and so did a working
  directory you cannot enter. Instead of the `[bash] failed to spawn` line and
  exit 127 the tool already had, a `!command` typed into the **TUI** took the
  whole session down with it — that loop catches only end-of-input — the CLI
  REPL had nothing on the path either, and a tool call got `Tool 'bash' raised:
  …` with no exit code to reason about. Measured on macOS through the tool: a
  shell with the exec bit and no valid image escaped as `[Errno 8] Exec format
  error`, one with the bit cleared as `[Errno 13] Permission denied`, and an
  unreadable `cwd` as `[Errno 13]` — while a *missing* `cwd` already returned
  127, so the two now agree. On Windows this is the common case rather than the
  exotic one: a non-PE file on `%COMSPEC%` or `$SHELL` arrives as `[WinError
  193] %1 is not a valid Win32 application`, which CPython maps to an errno
  with no exception class of its own. All seven errnos now report exit 127,
  through the same allowlist a `models.json` / `auth.json` `!command` has used
  since #227.
  **What you give up:** a spawn failure that is *not* about the shell or the
  directory — the host is out of descriptors or memory, or the error carries no
  errno at all — still raises rather than claiming exit 127, because "127"
  reads to a model as *command not found* and it would keep retrying against a
  machine that cannot spawn anything. One seam: on Windows, CPython folds every
  error code its table does not name into `EINVAL`, which *is* in the
  allowlist, so a rarity like "this program needs elevation" is reported as
  exit 127 there. See ADR-0238 and
  [#243](https://github.com/handochan/aelix-ai/issues/243).

- **`auth.json`'s cached `!command` values are trimmed the way `models.json`'s
  uncached ones always were** (`.strip()`), on every platform: surrounding
  whitespace, a leading newline and a trailing `\r` or tab no longer survive
  into the key. Before this, a command that ended in CRLF left a bare carriage
  return inside an `Authorization` header.

- **`BashOperations.exec` no longer swallows a cancellation of its own task.**
  While tidying up the abort watcher after a command had finished — or after the
  tool had just timed it out and killed it — `exec` awaited the watcher inside a
  `suppress(CancelledError, Exception)`, so a cancellation of the task running
  `exec` that landed in that window was thrown away and `exec` returned a normal
  result. It waits for the watcher now instead of awaiting it, so only the
  caller's cancellation comes out, and a cancellation now wins over a timeout
  report. Only a caller that supplies an abort signal reaches that window — in
  this build the RPC `bash` command and embedders, not a model-issued bash call
  or the TUI's `!command`, both of which pass `signal=None`. Measured against a
  command that takes about 4 ms: roughly 2 % of the aborts that landed while the
  command was still running were lost (48 of 1967 across ten runs) and a cancel
  aimed at the window was lost every time; after, no aimed cancel and none of
  380 randomly-timed ones was lost. A command that exited successfully after
  backgrounding a helper still keeps that helper. Contributors only; see
  ADR-0238 and [#234](https://github.com/handochan/aelix-ai/issues/234).

- **A plain `uv sync` now produces an environment the whole test suite can run
  in.** `aelix-server` is a workspace member that nothing depends on — not the
  root project, not any other package — so `uv sync` resolved without it and
  `tests/server` and
  `tests/cli/test_stdio_encoding_win32.py::test_server_main_sync_hardens_before_boot`
  failed on the import in any fresh checkout or worktree, with nothing wrong in
  the product code. It is a dev-group dependency now, so `--all-packages` is no
  longer the difference between a green suite and a red one. Contributors only;
  dependency groups are not build input, and the published `aelix` wheel's
  `Requires-Dist` is unchanged (measured on the built wheel, and
  `tests/packaging_gate` covers it). See
  [#224](https://github.com/handochan/aelix-ai/issues/224).

- **A timed-out hook or an aborted delegation no longer leaves its command
  running.** Both teardown ladders ended the process they had spawned, and that
  process is routinely the least interesting thing in the tree: a `sh` holding a
  pipeline, a `cmd.exe` waiting on the command it launched, an RPC child whose
  grandchildren are the actual work. On POSIX it leaked whenever the shell
  forked — `sh -c "sleep 6 | cat"` left `sh`, `sleep` and `cat` behind, while
  `sh -c "sleep 5"` was fine because `sh` execs and there is nothing to leave.
  On Windows it leaked every time at all three sites, because
  `start_new_session=True` is accepted and silently ignored there (CPython names
  the parameter `unused_start_new_session`). The delegation case was measured on
  macOS, not Windows: aborting a turn ran the whole
  `terminate()` → 1s → `kill()` → 5s ladder and the child's grandchild was still
  running when it finished. The RPC delegation channel, subprocess hooks and
  `models.json`'s `!command` now put their child in a process group on POSIX and
  a Job Object on Windows, and end the tree instead of the root. A hook that
  deliberately backgrounds a helper and returns 0 still keeps it — only the
  timeout and cancellation paths kill.

  `!command` also keeps the terminal as its *controlling* terminal — which turns
  out to make a helper's tty read a **detectable stop**, not a working prompt;
  see the `!command` entry under Changed. The new sites ask for a process group
  inside the same session rather than a new session, and `!command` is the site
  where `gpg` / `pass` and pinentry run.

  On Windows the delegated child can now be asked to stop at all: it is spawned
  in its own console process group, `stop()` sends `CTRL_BREAK_EVENT`, and the
  child answers it the way it answers SIGTERM elsewhere. Letting it exit on its
  own also surfaced a crash nobody could reach before — the child's stdin
  reader held a lock the interpreter needs at shutdown — which is fixed in the
  same change. See ADR-0238 and
  [#202](https://github.com/handochan/aelix-ai/issues/202) — and the print
  channel and the reaper are converted too
  ([#220](https://github.com/handochan/aelix-ai/issues/220)): the delegation
  spawn puts its child in a job object of its own, the reaper asks that child to
  stop with `CTRL_BREAK_EVENT` when there is a grace to wait out and ends the
  job when there is not, and the print child grew the `SIGBREAK` handler that
  gives the asking somewhere to land. One
  consequence of that on Windows, deliberate and stated rather than discovered:
  a **successful** delegation now also ends whatever the child left running
  behind it, because finishing with the child is what closes its job — the same
  behaviour the RPC child has had since #202, and on POSIX still nothing at all.
  The print child's exit code on a signal is fixed in the same change: it was 1
  with two tracebacks, because the handler called `sys.exit` from inside an
  asyncio task and the loop's own teardown replaced that `SystemExit` with a
  `RuntimeError`; it now returns 143 on POSIX — measured against a real child —
  and, on Windows, the 149 that `128 + SIGBREAK` implies, which the
  `windows-latest` leg is what actually measures. That number is also the only
  thing that tells a cooperative exit apart from a hard job kill — both used to
  be 1.

- **A command Aelix runs on a timeout of its own now takes its whole tree with
  it — and a command that succeeded is reported as one.** Three places run a
  bounded command: an extension's `aelix.exec(...)`, the `git clone` behind
  `aelix extension discover --refresh`, and the `fd` scan behind `@`-mention
  completion. All three were `subprocess.run(..., timeout=)`, whose kill on a
  timeout reaches the root process and nothing else. Measured on macOS: every
  one of the three left behind a grandchild that had inherited the command's
  output pipe — and at the `exec` and `git clone` sites that survivor was
  measured re-parented to `launchd` and still sitting in Aelix's own process
  group, including through a real model at the `exec` surface. On Windows the
  same timeout did not merely leak: CPython follows its kill there with a
  `communicate()` that has no bound at all, and that call cannot return while
  any descendant still holds the pipe, so the worker was wedged for as long as
  the leftover lived. And a command that exited **0** straight away after
  backgrounding a helper was reported as a timeout once its entire deadline had
  passed — the wait was for the pipe to close and the helper was still holding
  it — so a successful `sh -c "helper & echo done"` came back to the extension
  as `code=124 killed=True` with `done` already in its output. All three sites
  now run through one contained runner: the timeout bounds the command itself,
  the kill ends the process group (POSIX) or the job object (Windows) it was
  given, and after the command exits its output is drained until the pipes fall
  idle rather than until they close. `@`-mention completion also stops crashing
  on a filename that is not valid UTF-8 — `fd`'s output is decoded with
  replacement instead of strictly under the locale codec, where a single
  undecodable byte raised out of the completer. See
  [#221](https://github.com/handochan/aelix-ai/issues/221) and ADR-0238.

- **A `bash` tool call that runs past its timeout — or that you stop with Esc —
  now takes its whole tree with it, and comes back at once instead of waiting
  out whatever the command left behind.** The tool spawned its command into a
  session of its own on macOS and Linux and a plain process group on Windows,
  and then killed by pid: `killpg` here, `taskkill /T /F` there. On Windows that
  is the leak Pi measured before we did (#9129). `taskkill /T` follows *live*
  parent links only, and MSYS `bash` runs each stage of a pipeline through a
  short-lived subshell, so by the time the kill lands the leaves have a dead
  parent — `taskkill` ends the layers it can still see, exits 0, and
  `find`/`xargs`/`head` keep running. At this site that is worse than a leak:
  the tool read the command's output until the pipe closed, and the survivors
  were holding the pipe, so **the tool call itself did not return**, past its own
  timeout. The command goes into a job object there now, which holds every
  descendant regardless of whether its parent is still alive, and the `rg`/`fd`
  helper behind the `grep` and `find` tools took the same change. As with the
  rest of this work, the Windows half is reasoned from CPython's and Pi's source
  and measured only by the suite — `windows-latest` is the one leg where a job
  object or `taskkill.exe` actually runs.

  On macOS and Linux the group kill already reached a pipeline, and what you get
  instead is **the wait**. A descendant that made a session of its own is
  outside the group by definition and survives the kill — that is what a process
  group is — and the tool was then waiting for *it* to close the command's
  output pipe. Measured: a command whose helper called `setsid` and slept 8 s
  returned at **8.02 s** under a 1.0 s timeout, and at 8.02 s and 8.03 s when it
  was stopped with Esc or by a cancelled turn. After a kill the output is now
  drained only until it falls idle for 0.1 s, and never for longer than 1 s from
  the kill — the same rule and the same constants the contained runner from #221
  uses — so stopping a stuck command costs about 0.1 s rather than the life of
  whatever it left behind. The trailing output of a descendant still writing
  after the idle window is cut: the command was killed, and its bytes are a
  courtesy. A command that exits **successfully** after backgrounding a helper
  was left alone here, because changing it changes what a model sees; it is
  decided now, under **Changed** above — see
  [#232](https://github.com/handochan/aelix-ai/issues/232).

  **The command's stdin is `/dev/null` now, not the terminal Aelix was started
  from** — the contract `aelix.exec(...)` took in #221, and what Pi does at this
  site. The child already had no controlling terminal, so it never stopped for
  input the way a background job does; it *competed* with the TUI for your
  keystrokes instead (measured: the child received what you typed and Aelix's
  own reader got nothing) and it could turn your terminal's echo off and leave
  it off after exiting. `!cat` typed into the TUI never returned, and ate the
  next key on its way. It now reads nothing and exits 0, and `git commit` with
  no `-m` fails with "Aborting commit due to empty commit message." instead of
  opening an editor you cannot see.
  **Both of those are macOS and Linux sentences.** On Windows there is no
  session to take away — the containment there is a new process group plus a job
  object — so the command keeps the console Aelix was started from: a program
  that reads its stdin gets EOF from `NUL`, but one that opens `CONIN$` directly
  (git's credential prompts) or opens the console for a masked prompt
  (`Read-Host -AsSecureString`, `Get-Credential`) still prompts on that console,
  and an unanswered prompt still costs the command's whole timeout — when there
  is one, since a configured timeout of `0` means unbounded. That half needs a
  human at a Windows console and is **unverified**, not fixed. See ADR-0238 and
  [#222](https://github.com/handochan/aelix-ai/issues/222).

- **`models.json`'s own documentation no longer breaks the file it describes.**
  Two of the guide's three examples carried a `cost` with only `input` and
  `output`, while the validator requires all four keys — and a schema error
  makes Aelix discard the **whole** `models.json`, not just that model. The
  guide ships inside the wheel, so the broken example was being distributed.
  The prose was also backwards (it said `cost` was required; in fact omitting
  it succeeds and a *partial* `cost` fails), and the page now states what you
  silently get for every field you leave out — a minimal entry for a flagship
  model yields an 8x-too-small context window, reasoning off, and no image
  support, with no warning.

- **`aelix -p "..."` no longer stalls ~30s on an inherited but idle stdin
  pipe.** Any piped stdin promotes the run to print mode, and the print path
  then waited for a first byte before continuing — even when the prompt was
  already on argv. A process spawned with `stdin=PIPE`, or run under a CI
  harness, paid the full deadline for input it would never use (measured 35.2s,
  now 6.5s). The wait is time-boxed rather than skipped: `build_initial_message`
  concatenates stdin with the argv prompt, so `cat notes.txt | aelix -p
  "summarise this"` still picks up both, and a producer that takes a couple of
  seconds to get going (`curl … | aelix -p …`, `ssh host cmd | aelix -p …`)
  still lands inside the 5s grace window. Expiry always prints a note naming
  `AELIX_STDIN_TIMEOUT`, because from inside the process an idle pipe is
  indistinguishable from a producer that was about to write — so input may be
  dropped, but never silently. With nothing on argv the behaviour is unchanged,
  since there stdin *is* the prompt.
- **`aelix extension install` and `update` no longer report success for a pack
  this build already knows cannot load** (#154). The host decides — offline, and
  without importing a line of the pack — whether an installed extension's
  `aelix-plugin.toml` will bind; `verify` and `list` read that verdict, but
  `install` never asked for it and printed an unconditional "Installed. Restart
  aelix" even for a pack whose manifest demands a newer API level than this
  build provides. `install`, `discover install` and `update` now report the
  verdict for the distribution they just wrote — and only that one, so a
  pre-existing broken endpoint elsewhere in the environment is not blamed on the
  new pack — in the same wording `verify` uses. A pack that binds prints exactly
  what it printed before — including on a reinstall, on an update, and when the
  target is a symlink to the pack directory. A bare `aelix extension update`
  closes with a one-line summary naming the packs that will not bind, so the
  outcome does not scroll away behind the per-pack reports; across many packs the
  exit code is the hardest outcome, not the first (`1` and `2` outrank `3`, since
  `3` is the one code that asserts the installer succeeded). When the command
  *cannot* tie the
  install to an installed extension distribution — a repeat `git+URL` install, a
  repeat install of a source tree whose name cannot be read without executing it
  (setup.py-only, a `[tool.poetry]` name, a `dynamic` name), or a target that is
  not an extension at all — it now says it has **no verdict** and names `verify`,
  rather than printing the success line for a distribution it never identified.
  Attribution also reads pip's own PEP 610 `direct_url.json` record, which
  answers the first two of those with the real verdict instead of a shrug.
  **New exit code 3**: pip installed the distribution, but some endpoint of it
  will not bind. `0` still means installed-and-bindable (and is also what "no
  verdict" returns — something *is* on disk, and unknown is not failure); `2`
  still means the installer never ran; **`1` now means the installer ran and
  failed**, with its own exit code printed rather than returned, because pip's
  codes are not disjoint from this verb's — pip defines `VIRTUALENV_NOT_FOUND =
  3` (raised under `PIP_REQUIRE_VIRTUALENV`) and `UNKNOWN_ERROR = 2`, so a
  passthrough left "installed but inert" and "pip refused to run" reading
  identically. Nothing is ever auto-uninstalled — the report says the package is
  on disk and hands over the exact `aelix extension remove` command. The
  packaging hint printed for a manifest missing from a wheel now names the
  starter scaffold at a path that exists — `aelix_coding_agent/examples/starter/`
  in an installed environment — instead of a bare `examples/starter/`, which is
  not a directory at the repo root and does not exist once installed. `install`
  also accepts
  `--trust-extension-path DIST` now, so a pack installed outside the
  environment's real site directories (`pip --target` plus a matching
  `PYTHONPATH`) is not called broken when it is merely unvouched; the flag means
  exactly what it means to `verify` and to `aelix` itself.
- A malformed `aelix-plugin.toml` no longer echoes the manifest's contents
  into the error printed on startup (#91). Pydantic's validation errors
  interpolate the whole parsed document, which for a manifest declaring MCP
  servers includes `contributes.mcp_servers[].env` — plugin-supplied API
  tokens. Errors now carry the failing field and message only. Affects both
  directory-installed and package-installed extensions.
- The same extension discovered both as an installed package and through a
  scanned extensions directory now loads once rather than twice (#91). Its
  `setup()` previously ran twice against the same runtime.

### Known behaviour changes

- **`aelix extension discover --refresh` no longer prompts on your terminal for
  git or ssh credentials, on macOS and Linux** (#221). The catalog clone runs
  in a session of its own there, so that a timeout can end the clone *and*
  everything it spawned: measured, `git remote-http` blocked in libcurl against
  a server that accepts and never answers outlives a kill aimed at `git` alone
  and is still running three seconds later. A process in a session of its own
  has no controlling terminal, and the terminal is where git, ssh and a
  credential helper ask you for a password, a passphrase or a host-key
  confirmation. **Configure a credential helper (https) or an ssh agent and a
  known host key (ssh) — the clone now fails at once with git's or ssh's own
  message (`fatal: could not read Username for '…'`, `Host key verification
  failed.`; the errno text git appends after that colon is your platform's
  `strerror(ENXIO)` — `Device not configured` on macOS, `No such device or
  address` on Linux) instead of waiting at a prompt you cannot see, and the
  timeout error names the cause.** Askpass programs and GUI/keychain helpers
  need no terminal and keep working — including asking you: if `GIT_ASKPASS` or
  `SSH_ASKPASS` is set (VS Code exports `GIT_ASKPASS` unconditionally in its
  integrated terminal) git still calls it, and a dialog nobody answers still
  costs the full 60 s clone timeout before the clone fails, exactly as it did
  before. **On Windows there is no session to take away**: the containment
  there is a new process group plus a job object, the clone keeps the console
  Aelix was started from, and a prompt can still appear on it — one nobody
  answers costs the same full 60 s (adversary-1; Windows is not a supported
  host, see the README). Stopping a refresh by hand now takes two `^C`: the
  clone runs outside the terminal's foreground group, and `asyncio.run`'s
  `Runner._on_sigint` cancels the main task and returns on the first —
  measured, one `^C` left the clone running to its full bound (the timeout
  ladder ended it) and two ran the interrupt ladder at 0.25 s
  (callers-census-1).
- **An extension's `aelix.exec(...)` no longer inherits your terminal; it now
  decodes output as UTF-8, and a timeout now takes its whole tree with it**
  (#221). Its stdin is `/dev/null` rather than the terminal Aelix was started
  from, which is the contract this surface has upstream. Output is decoded
  UTF-8 with replacement, so bytes that are not valid text become `U+FFFD`
  instead of raising out of the extension — and line endings still normalise to
  `\n` as they did. (This bullet used to add "on Windows, output in a legacy
  code page that used to decode correctly under your locale now shows
  replacement characters". #239 pays that back in the same unreleased block:
  this site decodes run-wise through the console code page now, so the clause
  was struck rather than shipped contradicting the entry above.) A
  successful exit kills nothing — a helper the command backgrounded before
  exiting **successfully** still survives — while the timeout, an interrupt raised
  inside the call, and a cancelled turn *that lands while the command is still
  running* each end the whole tree (docs-adr-2). A cancelled turn that lands
  once the command has been reaped — inside that reading — kills nothing: it
  ends the reading of its output, and the helper survives (#230). **A command
  that expects a terminal — a prompt,
  a pager, an editor — now fails immediately instead of stalling until its
  timeout; one that reaches for an askpass-style program instead of the
  terminal can still block until its timeout, as it always could.** On Windows
  that first half does not hold: the child gets no session (there are none),
  only a new process group and a job object, so it keeps the console Aelix was
  started from and a program that opens the console directly — rather than
  reading the stdin we set to `NUL` — can still prompt and still stall until
  its timeout (adversary-1). Output written by a *descendant* more than two
  seconds after the command itself exited is no longer waited for — the command
  still reports its own exit code, but that trailing output is cut. And a
  cancelled turn now ends a command that is **still running**: **Esc, or ^C in
  `aelix -p`, kills the command's tree** instead of leaving it to run out its
  timeout (measured: on the old code a ^C reached the command through the
  terminal in 0.02 s; with a session of its own and no such handle it would
  have waited the command's whole remaining life, 28.58 s of a 30 s sleeper).
  Once the command has been reaped, Esc kills nothing — see the `### Fixed`
  entry for #230. `code=124 killed=True` on a
  timeout is unchanged.
- **AUTO mode now prompts instead of auto-allowing when your `$SHELL` is one
  the safety classifier cannot read** (#104). The AUTO posture decides whether
  to auto-run a `bash` command by parsing it with a tree-sitter **bash**
  grammar. That verdict is only sound for the POSIX shell family (`bash`,
  `sh`, `dash`, `ksh`, `mksh`, `zsh` — version suffixes such as `bash-5.2` are
  recognised). Under any other shell — `fish`, `nushell`, or PowerShell/`cmd`
  on the experimental Windows track — the grammar reads the command line as
  unremarkable words and returns "allow", which is active mis-permissioning
  rather than a missed detection. Such commands now fall through to the
  approval prompt. **If you use fish or another non-POSIX shell on Linux or
  macOS, you will see prompts in AUTO mode where commands previously ran
  unattended.** `DENY` verdicts are still enforced for every shell, and the
  other permission postures are unchanged.

## [0.1.0-beta.1] — not yet released

The first published release of Aelix, cut as the tag `v0.1.0-beta.1`
(distribution version `0.1.0b1`).

Nothing was published before it. This file previously carried a
`## [0.1.0] - 2026-06-20` entry describing an "initial public release" — no such
tag and no such GitHub Release ever existed, and its contents are the first
block of *Added* below. The *Changed* entries therefore describe changes made
during development, relative to the state of the repository rather than to any
earlier published version.

### Added

- **Agent runtime (`aelix-agent-core`)** — stateful `Agent`, hook-aware
  `AgentHarness`, typed `HookBus`, and the low-level async agent loop.
- **AI primitives (`aelix-ai`)** — provider-agnostic message, streaming, and
  tool types with pi-ai parity.
- **Providers** — Anthropic and OpenAI-compatible backends (incl. OpenRouter),
  with reasoning/thinking wiring, custom-model loading from `models.json`, and
  config-value auth indirection (env-var / command).
- **Built-in tools** — bash, read, write, edit, ls, grep, and find, with
  pi-parity schemas and behavior (including image read/resize and `rg`/`fd`
  acquisition).
- **Compaction** — context summarization with entry-level cut-points,
  split-turn handling, file-op preservation, and a token cap.
- **Extensions API (`aelix-coding-agent`)** — 4-tier extension architecture,
  extension loader, built-in policy/guardrail extensions, runtime tool
  registration, and example tools.
- **Project Trust** — running in an untrusted directory gates project-local
  extensions (`.aelix/extensions/`) and MCP servers (`.aelix/mcp.json`) behind a
  trust prompt with on-disk persistence; deny-by-default in headless mode.
- **Cooperative abort** — `Esc` cancels in-flight tools (bash, grep, find, read,
  write, edit, ls) without orphaning processes, and the RPC `abort_bash` kills
  the running shell.
- **TUI** — an interactive terminal shell (optional `[tui]` extra) with slash
  commands, streaming Markdown output, compact tool cards, a status footer and
  context meter, steer/queue, session resume/fork, and an external-editor
  binding.
- **CLI** — the real `aelix` command (session, fork, export, and model flags)
  plus a headless RPC mode and OAuth credential management.
- **Release engineering** — CI (ruff + pytest on Python 3.11 / 3.12) and a
  tag-triggered PyPI publish workflow using Trusted Publishing (OIDC).

- **Beta / pre-release track** — pre-releases (beta/rc/alpha) are cut as
  **GitHub Releases only** and installed via a checksum-verified `install.sh`
  one-liner (`uv`-based, wheels verified against a published `SHA256SUMS`
  manifest); PyPI publishing is reserved for GA. The `release.yml` workflow now
  attaches a `SHA256SUMS` manifest to each Release and skips the PyPI `publish`
  job for hyphenated (pre-release) tags. First beta version: `0.1.0b1`
  (tag `v0.1.0-beta.1`). See `RELEASING.md` → *Beta / pre-release track*.

- **One delegation can now carry several tasks** — the `agent` tool gains
  `mode: "parallel"` (run up to 8 tasks at once, at most 4 at a time) and
  `mode: "chain"` (run them in order, where a later task can write `{previous}`
  to insert the previous one's summary). Every task in one call runs under one
  agent profile in one working directory, results come back in the order you
  listed them, and a chain stops at the first failure and names the steps that
  never ran. Asking for more tasks than the limit is **refused** — the list is
  never quietly trimmed to fit. A single-task delegation is unchanged in every
  respect. See ADR-0199.

- **A multi-task delegation asks once, showing every task** — one consent
  prompt for the whole call, listing each task and the one directory they share,
  rather than one prompt per subagent or one prompt standing in for tasks you
  never saw. If that prompt would be taller than your terminal, the whole call
  is refused with an explanation of how many tasks would fit, instead of being
  drawn with its `Cancel` option pushed off the bottom of the screen. An
  ordinary read-only delegation still shows no prompt at all. A chain is never
  offered the "allow file edits for this run" option: its later steps are fed
  text written by an earlier subagent, which no one has read, so the dialog says
  so and declines to grant write authority for it. See ADR-0199.

- **A running batch is visible while it runs** — one status-line row for the
  whole batch (`agent scout ×4 · 2 running · 1 done · 1 queued · 33s`), one line
  per subagent in the tool card that stays in the transcript afterwards, and a
  small panel above the input box while two or more subagents are live. A single
  delegation keeps exactly the display it had. See ADR-0199.

- **The agent now knows how to extend itself** (#117) — the default system
  prompt gained a short "Extending yourself" block, scoped to the case where you
  ask for a tool, command or hook to be added to Aelix itself. It names the real
  contract (one Python file with `def setup(aelix)`, imported and run
  in-process — no manifest, no build step), gives both extension directories as
  absolute paths, and points at two files that ship inside the wheel — a worked
  example and the full API — so the agent reads the real API instead of guessing
  at one. Asked in plain language to add a tool, a fresh install previously
  failed *and* claimed success, inventing a `tools_definition.json` manifest
  that exists nowhere in Aelix; the word "extension" did not appear anywhere in
  the prompt it was given. The block adds about 1,200 characters of fixed text
  to the base system prompt, plus the four absolute paths it embeds — so the
  exact size depends on your working directory and where Aelix is installed.

  The global directory (`~/.aelix/agent/extensions/`, or
  `$AELIX_CODING_AGENT_DIR`) is offered first because it is not trust-gated; the
  project-local one (`<project>/.aelix/extensions/`) is offered second and
  labelled with its condition, because an untrusted project drops its
  project-local extensions with no error and no warning. The block also says a
  `.aelix/` write may ask for approval and that the prompt is not a refusal,
  and that a declined *or* policy-blocked write ends the attempt — it must not
  be retried through `bash` or written somewhere else to get around it.

  Every instruction the block emits is executed as part of the test suite
  rather than string-matched, so a command the agent is told to run cannot ship
  broken: the API grep hint is re-run through the real bash tool, ripgrep and
  the Python fallback, and the file it says to read is read back through the
  real `read` tool to prove the content actually arrives.

  The block is **omitted** whenever you supply your own prompt:
  `--system-prompt` / `--system-prompt-file`, or an agent profile with
  `system_prompt: replace`. Those remain full overrides — nothing is silently
  appended to them.

### Changed

- **The per-prompt delegation cap now counts subagents, not `agent()` calls, and
  a call that does not fit what is left is refused whole.** The limit of 12 has
  not moved, but one call can now start up to 8 subagents, so the two readings
  are no longer the same thing — counting calls would have allowed 96 subagents
  from a single prompt. A call asking for more subagents than the prompt has
  left is refused before anything is shown or started, and the refusal tells the
  model how many it may still ask for; nothing is trimmed and no subagent is
  started only for its siblings to come back as refusals. `/agents run` is still
  not rate-limited. See ADR-0199.

- **`timeout_ms` now has a maximum, and one `agent()` call is capped in total.**
  A task may ask for at most 30 minutes, and one call — however many tasks it
  carries — is bounded by the same 30 minutes, so an eight-step chain at the
  default per-task timeout can no longer occupy the session for 80 minutes.
  Previously only a *minimum* was checked, so a model could ask for a timeout of
  any size. A task that runs out of the call's remaining time returns a readable
  refusal instead of being silently dropped, and every task that already
  finished still reports its result. See ADR-0199.

- **Tightening the permission posture with shift+tab during a delegation now
  applies to the subagents that have not started yet.** In a batch of eight,
  subagents five to eight begin only after the first four finish, which can be
  much later; until now they would have launched at the posture that was in
  effect when the call began. Tightening always applies; loosening never raises
  a subagent above what was approved at the prompt. See ADR-0199.

- **`auto-accept-edits` / `auto` no longer auto-approve writes under `.aelix/`** —
  editing an agent profile (`.aelix/agents/*.md`), a project extension
  (`.aelix/extensions/*.py`), the project MCP config (`.aelix/mcp.json`) or
  project settings (`.aelix/settings.json`) now shows the usual approval prompt
  instead of being written silently. Those files **execute on a later run**, so an
  unattended agent that could write them could author what the next session runs.
  Ordinary in-project writes are unaffected. See ADR-0197 §(i).
- **`auto-accept-edits` / `auto` now resolve symlinks before deciding whether a
  write may be auto-approved.** Both of that gate's rules — "inside the project
  root" and "not a security-sensitive path" — were previously decided on the
  path as written, so a symlink checked into the repository (git stores them as
  mode 120000) could land an auto-approved write in `.aelix/`, `~/.ssh/` or the
  home directory. Such writes now show the usual approval prompt. A symlinked
  project root still works, and a symlink that stays inside the tree is still
  auto-approved.
- **`--no-agents` now beats `--agents` wherever the two appear on one command
  line**, which is what both flags have always been documented to do. The parse
  loop was really last-flag-wins, so a wrapper script or shell alias pinning
  `--no-agents` could be silently re-opened by a later `--agents`. A lone
  `--agents` is unaffected.
- **The delegation consent dialog now appears only when write authority is
  actually at stake** — when the subagent would run with a permission mode that
  can change files without asking, or when the agent profile itself declares it
  needs one (`approval_mode: auto` or `ask`) and you can therefore grant it at
  the dialog. An ordinary read-only delegation — a profile with no
  `approval_mode:` line, under an ordinary permission posture — now starts
  without a prompt: the dialog there could only offer "Run read-only (plan)" or
  "Cancel", and a confirmation with no real answer teaches you to dismiss the one
  that matters. Nothing gains authority — the subagent runs at exactly the mode
  that dialog would have granted, still cannot mutate anything, and is still
  bounded by the per-prompt and per-session delegation caps and shown in the
  status line. Running a *project-local* agent profile still takes its own
  explicit confirmation, which is unchanged. See ADR-0197 §(i) and residual R7.

  **`yolo` is the exception, and the only one** (#196, ADR-0231): there no
  dialog appears at all, because `yolo` means "run mutating tools without a
  prompt" and a delegation was the last prompt it had. The child is named in
  the status line while it runs instead — profile, posture, source file, task
  count — and the finished tool card's `[agent … · yolo · …]` footer records
  the posture afterwards.
- **A profile's `approval_mode:` now decides whether a delegation may be widened
  at the dialog.** `auto` and `ask` declare that the agent needs write authority,
  so their dialog offers "Allow file edits for this run (auto-accept-edits)";
  `inherit` (the default) and `deny` declare nothing and are never offered it —
  which is why a plain read-only delegation no longer prompts at all. Authority
  follows what the profile *declares*: the way to let an agent write is to edit
  its profile, a file you can review and sign, rather than to upgrade it from a
  modal in the middle of a turn. `deny` can never be widened, and a project-local
  profile can never be widened whatever it says. See ADR-0197 §(i).
- **A delegation dialog answer that matches none of the offered options is now a
  decline.** Previously only `Esc` and `Cancel` declined and any other string
  granted the delegation at the inherited posture. Not reachable from the shipped
  TUI, which can only return an offered option or `None`, but `ctx.ui` is a public
  extension seam. See ADR-0197 §(i).
- **Delegation is now capped per prompt and per session** — at most 12 subagents
  started by the model in one user prompt, and at most 4 live at once. Both
  return a readable refusal to the model rather than an error. `/agents run` is
  not rate-limited. See ADR-0197 §(i) residual R1.
- **A delegated child that finishes right at its timeout is no longer reported as
  a timeout.** The gap between the child closing its output and the OS reporting
  its exit status was being charged to the caller's budget, so a completed run
  with the correct answer could come back as `status=timeout, ok=False`.
- **The temporary file holding a delegated agent's system prompt is now reclaimed
  after a crash.** It was already deleted on every normal, error, timeout, kill
  and cancellation path, but a parent killed outright (SIGKILL, OOM) left it in
  the system temp directory permanently — one per delegation, mode 0600. Aelix
  now sweeps prompt files belonging to processes that are gone, at startup.

### Fixed

- **`aelix --list-models` with no credentials no longer points at a command that
  does not exist.** It advised running `aelix auth`; the `aelix` console script
  parses that as the first user message, so the one instruction a brand-new user
  received could not work. It now names the provider environment variables and
  the `/login` command inside the TUI, both of which exist.
- **The headless "No model selected." message no longer ends with a TUI-only
  instruction.** `--print` and `--mode json` print it when no provider can be
  resolved, and it closed with `Then use /model to select a model.` — `/model`
  is a TUI command, so a headless reader had no prompt to type it at. It now
  leads with `--model <id>`, which works on the invocation that just failed, and
  offers `/model` only as the interactive alternative.
