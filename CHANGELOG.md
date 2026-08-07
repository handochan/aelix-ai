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

### Added

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
- A malformed `aelix-plugin.toml` no longer echoes the manifest's contents
  into the error printed on startup (#91). Pydantic's validation errors
  interpolate the whole parsed document, which for a manifest declaring MCP
  servers includes `contributes.mcp_servers[].env` — plugin-supplied API
  tokens. Errors now carry the failing field and message only. Affects both
  directory-installed and package-installed extensions.
- The same extension discovered both as an installed package and through a
  scanned extensions directory now loads once rather than twice (#91). Its
  `setup()` previously ran twice against the same runtime.

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
  binding. Inline image rendering via the optional `[images]` extra.
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
