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

### Added

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

### Fixed

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

  `!command` also keeps its controlling terminal, which is what a credential
  helper needs: the new sites ask for a process group inside the same session
  rather than a new session, because a child of a new session that opens the
  terminal gets `sh: /dev/tty: Device not configured` — measured with a shell
  under a real pty, and `!command` is the site where `gpg` / `pass` and pinentry
  run.

  On Windows the delegated child can now be asked to stop at all: it is spawned
  in its own console process group, `stop()` sends `CTRL_BREAK_EVENT`, and the
  child answers it the way it answers SIGTERM elsewhere. Letting it exit on its
  own also surfaced a crash nobody could reach before — the child's stdin
  reader held a lock the interpreter needs at shutdown — which is fixed in the
  same change. See ADR-0238 and
  [#202](https://github.com/handochan/aelix-ai/issues/202). The print channel and
  the reaper are not converted yet
  ([#220](https://github.com/handochan/aelix-ai/issues/220)).

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
