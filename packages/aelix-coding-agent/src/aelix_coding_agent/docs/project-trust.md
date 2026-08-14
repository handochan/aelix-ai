# Project Trust

Status: Accepted

You `git clone` a repository and run `aelix` in it. That repository may carry
files that tell aelix to load code, spawn processes, or write into the agent's
own instructions. **Project Trust** is the one-question gate that stands in front
of those files.

This guide covers what it protects, what it deliberately does not, and how to
answer it.

## The one sentence

A `.aelix/` directory inside your working directory is content someone else
wrote. Nothing in it is loaded until you say so — except that trust is decided
once per directory and then remembered.

## Exactly which resources are gated

Five, and they are the complete list — read from
`has_trust_requiring_project_resources` in `cli/project_trust.py:124-249`, not
from memory. `.aelix` is `CONFIG_DIR_NAME` (`cli/config.py:33`).

| Resource | Why it is gated |
| --- | --- |
| `<cwd>/.aelix/extensions/` | `importlib` `exec_module`s arbitrary Python with your privileges. |
| `<cwd>/.aelix/mcp.json` | Declares MCP servers; a `stdio` one spawns a subprocess on connect. |
| `<cwd>/.aelix/agents/` | An agent profile is an **identity** — it can replace the system prompt and swap the model and tool allow-list. |
| `<cwd>/.aelix/skills/` | A skill's name, description and location go into the system prompt verbatim. |
| `<cwd>/.aelix/prompt-templates/` | A template body becomes a user turn verbatim on `/<name>`. |

Two structural details that decide whether you are asked at all:

- The three directory entries require **at least one entry** to count. An empty
  `.aelix/extensions/` loads nothing, so it does not trip the gate
  (`project_trust.py:161`, `:196`, `:244`).
- If none of the five is present, the directory is trusted **without a prompt**
  (`project_trust.py:679-680`). There is nothing to gate, so there is no
  question to ask.

`.aelix/teams/` is reserved and deliberately **not** checked — that clause lands
with the loader that reads team descriptors, not before it
(`project_trust.py:191-193`).

## What the gate does not protect

This is the half worth reading twice.

**`AGENTS.md` is not gated.** Aelix walks from your cwd to the filesystem root,
reads every `AGENTS.md`, and appends the text to the system prompt — and that
path never consults trust, in any mode. `--no-approve` does not stop it;
measured, the repository's `AGENTS.md` still reaches the prompt. This is a
deliberate decision, not an oversight: **ADR-0217** settled it as pi parity.
pi publishes the policy in `packages/coding-agent/docs/security.md`, and pi
itself tried the other way — it added context files to its trust manager in
`89a9220` (v0.79.0) and removed them again in `5cb4f59` (v0.79.1), four days
later. Diverging here would make aelix stricter than the reference at a surface
pi keeps permissive on purpose, and would have to be re-argued at every upstream
sync.

What ADR-0217 *did* change is the boundary. The text is no longer injected
verbatim: it is XML-escaped inside pi's `<project_context>` fence
(`cli/agent_context.py:158-159`), so a hostile file can no longer forge its way
out of the fence and speak as the host. The whole block is capped at 32768 bytes.

**The switch for `AGENTS.md` is `--no-context-files` / `-nc`**, not
`--no-approve`. The `--help` line used to read "Ignore project-local files for
this run", which a user wanting *"do not let this cloned repo influence the
agent"* would reasonably read as covering `AGENTS.md`. It does not, and the help
text now says so (`cli/args.py:741-742`).

**Trust is not a sandbox.** Answering "Trust" runs that repository's Python in
your process, with your files and your credentials. It is a decision about
provenance — do you know who wrote this — not a containment boundary.

**These are never gated, because they are your choices, not the project's:**
explicit `-e <path>` extensions, `$AELIX_MCP_CONFIG`, global MCP config,
`--agent-file` profiles outside the project, and installed entry-point
extensions (`project_trust.py:16-24`).

That last list rests on a premise that had to be *made* true, which is the next
section.

## ADR-0203: a repo `.env` is default-deny

`cli/entry.py::main_sync` calls `load_dotenv()` before the trust gate exists, by
construction — the gate lives inside `_async_main`. So a cwd `.env` used to be
able to inject arbitrary environment variables into the process **before any
trust decision existed**, which is how "`$AELIX_MCP_CONFIG` is a user choice"
stopped being true: a cloned repo could set it itself and land a server in the
one tier that is deliberately never gated. Measured, that spawned `sh -c
<payload>` at startup — *even under `--no-approve`*.

ADR-0203 replaced the unfiltered read with **default-deny admission control**
inside `load_dotenv`. A repo `.env` may set:

- **credential-shaped names** — anything ending `_API_KEY`, `_KEY`, `_TOKEN`,
  `_SECRET`, `_PASSWORD` (`cli/runtime_bootstrap.py:128`), because supplying your
  own provider key from your own project is the workflow this must not break;
- a short list of **provider-configuration names whose values are shape-checked**
  (a base URL must look like a URL, and so on).

Everything else is refused. A subtraction rule (`_DOTENV_NEVER`,
`runtime_bootstrap.py:150-153`) takes back the credential-shaped names that are
really paths, URLs, programs, or aelix's own knobs — so a future aelix variable
named `*_KEY` cannot become repo-settable by accident.

There is a per-key escape hatch, `AELIX_DOTENV_ALLOW`, read from the **real**
environment only — a `.env` cannot widen the gate it is judged by. Underneath it
sits a floor of 14 names (`_DOTENV_LOCKED`, `runtime_bootstrap.py:366-393`) that
the hatch cannot open, on one criterion: *the hatch may let a repo redirect; it
may never let a repo execute, and never let a repo choose the global settings or
auth store.* `AELIX_SETTINGS_PATH`, `AELIX_CODING_AGENT_DIR`, `HOME`,
`AELIX_MCP_CONFIG`, `BASH_ENV`, `LD_PRELOAD`, `EDITOR` and friends are on it.

## Answering the question

### Interactively

The first time you run `aelix` in a directory carrying any of the five
resources, you get a selector (`project_trust.py:317-332`):

```
Trust project folder?
/path/to/repo

This allows Aelix to load .aelix extensions, MCP servers, and agent profiles,
which can execute arbitrary code on your machine — and to load .aelix skills
and prompt templates, whose text is placed into the agent's instructions.
```

| Option | Effect |
| --- | --- |
| `Trust` | Trusted, and **remembered** for this directory. |
| `Trust parent folder (<parent>)` | Remembered against the parent, so every repo under it inherits it. |
| `Trust (this session only)` | Trusted now, **nothing written**. |
| `Do not trust` | Refused, and remembered. |
| `Do not trust (this session only)` | Refused now, nothing written. |

Cancelling (Esc / Ctrl+C) denies (`project_trust.py:724-726`).

### `/trust`, after startup

The startup selector runs before the TUI exists, so declining once used to leave
restarting as the only way to change your mind. `/trust` re-opens the same
selector from inside a session (`tui/commands.py:1643`, registered at `:2073`).

### `--approve` / `-a` and `--no-approve` / `-na`

```bash
aelix --approve        # trust this project's .aelix resources for this run
aelix --no-approve     # ignore them for this run
```

Both **short-circuit** the whole resolution: no prompt, and **nothing is
persisted** (`project_trust.py:674-676`). They are per-run overrides, so they are
the right tool for CI and for a one-off look at an unfamiliar repository — and
the wrong tool for recording a decision.

Remember that `--no-approve` does not cover `AGENTS.md`. To refuse both, pass
both: `aelix --no-approve --no-context-files`.

### Headless

In `--print`, `--mode json` and `--mode rpc` there is no UI to prompt with, so an
undecided directory is **denied** (`project_trust.py:718-720`, pi parity). The
project-local resources are dropped and a notice naming them goes to stderr
(`cli/entry.py:2370-2380`), because a silent drop looks identical to a
misconfiguration.

## Where the answer is stored

`~/.aelix/agent/trust.json` — more precisely `<agent_dir>/trust.json`
(`project_trust.py:365`, `:379-381`). A JSON object mapping an absolute
canonical path to `true` / `false` / `null`, keys sorted, written atomically via
a temp file plus `os.replace` (`:433-453`).

Lookup walks **up** from your cwd to the first decided ancestor
(`:455-477`), which gives two useful properties:

- trusting `~/work` transitively trusts everything under it;
- a `false` on a child beats a `true` on an ancestor.

`null` means undecided: skipped by the walk, and never written by `set`.

To revoke, delete the entry (or the file). A malformed store is treated as *no
decision* rather than an error — it falls through to the prompt-or-deny path,
which is the safe direction (`project_trust.py:704-710`).

## The full resolution order

`resolve_project_trusted` (`project_trust.py:623-733`), in order. The first step
that produces an answer wins:

1. `--approve` / `--no-approve` — returns immediately. No prompt, no write.
2. No trust-requiring resources → trusted.
3. The `project_trust` extension event. Already-loaded extensions — your
   explicit `-e`, global, and entry-point packs, i.e. code *you* chose — get a
   vote before the project does. The first `yes`/`no` wins; `undecided` falls
   through. A handler that raises is reported, never fatal.
4. The persisted `trust.json` decision (nearest ancestor).
5. The global `defaultProjectTrust` setting: `always` → trusted, `never` →
   refused, `ask` (the default) → fall through. Global settings only — a
   repository cannot set this by shipping its own settings file.
6. UI present → prompt. No UI → **deny**.
7. Persist the answer unless it was a "session only" one, and return it.

## One sharp edge, on the record

If a directory had nothing to gate at startup, you were never asked, and step 2
trusted it. If trust-requiring resources appear *later* — a `git pull` that adds
`.aelix/extensions/` — and a `/reload` picks them up, aelix writes that implicit
trust to the store rather than asking
(`maybe_save_implicit_project_trust_after_reload`, `project_trust.py:495-560`).

It runs **after** the reload, so it does not stop the new resources from loading;
by the time it is called they already have. It converts "never asked" into
"granted, and remembered".

Issue #112 proposed making it a gate instead. That was measured to work and was
**declined** in favour of pi parity. The residual risk is real and is recorded
here rather than only in the tracker: a repository whose `.aelix/extensions/`
arrives mid-session via `git pull` is trusted without a prompt, and durably. If
you pull into a directory you have not read, restart rather than `/reload`.

## Related

- [extension-authoring.md](extension-authoring.md) — the project-local extension
  channel this gate protects.
- [agent-profiles.md](agent-profiles.md) — why the project tier of profiles is
  gated while the other two are not.
- [ADR-0149](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0149-v1-sprint2-project-trust.md)
  — the trust model.
- [ADR-0203](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0203-dotenv-admission-control.md)
  — `.env` admission control.
- [ADR-0217](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0217-project-context-files-follow-pis-published-policy-and-forward-sync-to-its-fence.md)
  — why `AGENTS.md` stays ungated, and the fence that replaced verbatim
  injection.
