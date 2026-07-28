# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The published distribution set is released in lock-step at a single shared
version: `aelix-ai`, `aelix-agent-core`, `aelix-coding-agent`, and the `aelix`
umbrella meta-package. (`aelix-server`, the Web-UI daemon, is deferred to a
later release and is not part of this publish set.)

## [Unreleased]

### Added

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

## [0.1.0] - 2026-06-20

Initial public release of the Aelix agent runtime — a pi-faithful, Python-native
agent platform.

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

[Unreleased]: https://github.com/handochan/aelix-ai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/handochan/aelix-ai/releases/tag/v0.1.0
