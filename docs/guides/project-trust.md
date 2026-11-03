# Project Trust

Status: Accepted

You `git clone` a repository and run `aelix` in it. That repository may carry
files that tell aelix to load code, spawn processes, or write into the agent's
own instructions. **Project Trust** is the one-question gate that stands in front
of those files.

This guide covers what it protects, what it deliberately does not, and how to
answer it.

## The one sentence

A `.aelix/` directory inside your working directory — and a `.agents/skills`
directory in it or in a folder above it, up to the repository root — is content
someone else wrote. Nothing in it is loaded until you say so — its
`settings.json` included — except that trust is decided once per directory and
then remembered.

## Exactly which resources are gated

The complete list comes from
`has_trust_requiring_project_resources` in `cli/project_trust.py`. `.aelix` is `CONFIG_DIR_NAME` (`cli/config.py:36`).

| Resource | Why it is gated |
| --- | --- |
| `<cwd>/.aelix/settings.json` | Your settings, overridden by the repository's: it can choose the default model and provider your prompts go to, and almost every other setting. The exceptions are read from your global settings only, trusted or not: `defaultProjectTrust`, `features.agents`, `extensionSources`, `suppressedDefaultCatalogs`, `respectGitignore` and `checkModelCatalog`. Read only when the project is trusted (#369). |
| `<cwd>/.aelix/extensions/` | `importlib` `exec_module`s arbitrary Python with your privileges. |
| `<cwd>/.aelix/mcp.json` | Declares MCP servers; a `stdio` one spawns a subprocess on connect. |
| `<cwd>/.aelix/SYSTEM.md` | Replaces the generated system prompt, including the agent identity and tool guidance (#287, ADR-0257). |
| `<cwd>/.aelix/APPEND_SYSTEM.md` | Adds instructions to the system prompt before AGENTS.md context (#287, ADR-0257). |
| `<cwd>/.aelix/agents/` | An agent profile is an **identity** — it can replace the system prompt and swap the model and tool allow-list. |
| `<cwd>/.aelix/skills/` | A skill's name, description and location go into the system prompt verbatim. |
| `<cwd>/.aelix/prompt-templates/` | A template body becomes a user turn verbatim on `/<name>`. |
| `<dir>/.agents/skills/` for `<cwd>` and each folder above it, up to the repository root (the filesystem root outside a repository) | The Agent Skills location other agents use (#509, ADR-0281): the same as `.aelix/skills/`. Your own `~/.agents/skills/` is never one of these — it is read in every folder, ungated, and a folder under your home directory does not ask because of it. |

Two structural details that decide whether you are asked at all:

- The directory entries (`extensions/`, `agents/`, `skills/`,
  `prompt-templates/`) require **at least one entry** to count. An empty
  `.aelix/extensions/` loads nothing, so it does not trip the gate
  (`project_trust.py:225`, `:260`, `:308`). A `.agents/skills/` counts when its
  listing shows something a skill can be read from: a `SKILL.md`, or a
  subfolder that is not hidden and not `node_modules` (even an empty one —
  nothing below is listed). One holding only a `README.md` loads nothing and
  does not ask; nor does one that cannot be listed.
- The `.agents/skills/` folders the gate counts are exactly the ones a trusted
  run reads (`core/skill_dirs.py`, one walk for both): from your cwd up to the
  nearest folder holding `.git` (a worktree's `.git` file counts), or to the
  filesystem root outside a repository. A `.agents/skills/` above the
  repository is never read and never asks. pi's check walks every folder
  above, past the repository root; aelix asks only about what it would load.
- `settings.json`, `SYSTEM.md` and `APPEND_SYSTEM.md` count if **anything**
  exists at that path — a FIFO or a directory asks too, as in pi (`existsSync`); a file test let a FIFO skip a
  saved denial. `mcp.json` counts when it is a file.
- If none of these resources is present, the directory is trusted **without a prompt**
  (`project_trust.py:1013-1014`). There is nothing to gate, so there is no
  question to ask.

**Saving a project choice creates the gate.** `/skill`'s "This project" target
(#511, ADR-0279 D3) writes `<cwd>/.aelix/settings.json`. In a folder that had
no such file, that write is what puts the folder under this gate: from the next
launch the folder is decided like any other with a gated resource — an
extension's `project_trust` answer, then a saved decision, then
`defaultProjectTrust`, then the question (`project_trust.py`'s order; `-p`
denies, so needs `--approve`, where nothing earlier decides) — so the panel says this and waits
for a second Enter before creating it. In an untrusted folder the
target cannot be chosen at all, and the project's skills appear in `/skill` as
one locked row per directory — `.aelix/skills/` and each `.agents/skills/` a
trusted run would read — whose count comes from listing it: no `SKILL.md` in
it is opened. See [skills-and-prompts](skills-and-prompts.md).

`.aelix/teams/` is reserved and deliberately **not** checked — that clause lands
with the loader that reads team descriptors, not before it
(`project_trust.py:255-257`).

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
(`cli/agent_context.py:224-225`), so a hostile file can no longer forge its way
out of the fence and speak as the host. The whole block is capped at 32768 bytes.

**The switch for `AGENTS.md` is `--no-context-files` / `-nc`**, not
`--no-approve`. The `--help` line used to read "Ignore project-local files for
this run", which a user wanting *"do not let this cloned repo influence the
agent"* would reasonably read as covering `AGENTS.md`. It does not, and the help
text now says so (`cli/args.py:947-948`).

**Trust is not a sandbox.** Answering "Trust" runs that repository's Python in
your process, with your files and your credentials. It is a decision about
provenance — do you know who wrote this — not a containment boundary.

**These are never gated, because they are your choices, not the project's:**
explicit `-e <path>` extensions, `$AELIX_MCP_CONFIG`, global MCP config,
global SYSTEM.md / APPEND_SYSTEM.md and explicit system prompt flags,
`--agent-file` profiles outside the project, and installed entry-point
extensions (`project_trust.py:29-37`).

Because they are your choices, the folder you are in must not be able to answer
them. A module **name** — `-e acme`, a global pack's `[plugin.entry] python`,
an installed entry point's module — is imported from the installed environment
only: when the way aelix was started put the project folder on the import path
(`python -m`, `python -c`, an empty `PYTHONPATH` segment), it is taken off for
that import wherever it sits, and a module of that name already imported
through it is refused instead of reused (a folder your own `PYTHONPATH` or a
`.pth` names is your environment and stays) (#114, #641,
[ADR-0285](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0285-a-module-name-the-user-chose-is-never-answered-by-the-project-folder.md)).
Delegated agents start as `python -P -m aelix_coding_agent`, so their working
folder is not on the import path at all; before #641 a file in it named like
one of your extensions' modules ran in place of that module in every delegated
agent, trusted project or not.

One `-e` spelling is the exception to "never gated": a module-shaped name such
as `-e acme` when the folder you are in holds an `acme` file or directory. An
existing path wins over the module reading, so there the folder, not you,
decides what `acme` means. So that entry loads the folder only when **you**
trusted it: `--approve`, a saved "Trust" for the folder or a parent, or
`defaultProjectTrust: "always"` in your global settings. Anywhere else it is
**refused** before anything in it is opened — `--no-approve`, a saved "Do not
trust", an undecided folder under `-p`, the trust vote that runs before the
decision (which no longer prints it), and also a folder with nothing to gate,
which is trusted without a question (above), and "Trust (this session only)",
a yes to this session rather than to the folder. The message starts with both
ways to say what you meant: `-e ./acme` for the folder, `-e acme:setup` for the
installed module (#638). With `--approve`, a saved "Trust" or "always" it warns
and loads the folder, as before. One saved "Trust" is not yours: when a folder
that had nothing to gate gains `.aelix/` resources and you `/reload`, aelix
saves trust for it without asking ("Saved project trust for this folder"), and
from the next launch that counts too (a known gap).

That list rests on a premise that had to be *made* true, which is the next
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
  `_SECRET`, `_PASSWORD` (`cli/runtime_bootstrap.py:124`), because supplying your
  own provider key from your own project is the workflow this must not break;
- a short list of **provider-configuration names whose values are shape-checked**
  (a base URL must look like a URL, and so on).

Everything else is refused. A subtraction rule (`_DOTENV_NEVER`,
`runtime_bootstrap.py:146-149`) takes back the credential-shaped names that are
really paths, URLs, programs, or aelix's own knobs — so a future aelix variable
named `*_KEY` cannot become repo-settable by accident.

There is a per-key escape hatch, `AELIX_DOTENV_ALLOW`, read from the **real**
environment only — a `.env` cannot widen the gate it is judged by. Underneath it
sits a floor of 14 names (`_DOTENV_LOCKED`, `runtime_bootstrap.py:358-385`) that
the hatch cannot open, on one criterion: *the hatch may let a repo redirect; it
may never let a repo execute, and never let a repo choose the global settings or
auth store.* `AELIX_SETTINGS_PATH`, `AELIX_CODING_AGENT_DIR`, `HOME`,
`AELIX_MCP_CONFIG`, `BASH_ENV`, `LD_PRELOAD`, `EDITOR` and friends are on it.

**A key from a repo `.env` authenticates a route; it never chooses one**
([ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md),
#362). `load_dotenv` records the names it admitted in `AELIX_DOTENV_ADMITTED` —
which a `.env` cannot set, even through the hatch, and which delegated agents
and an `aelix` the bash tool starts inherit — and every judgement that picks
*where* a prompt goes (which of several providers serves a bare id, whether
`openai/…` goes to OpenAI or OpenRouter, whether an id this build does not know
goes to OpenRouter, what `/model` switches to, the model picked after `/login`,
the next model RPC `cycle_model` rotates to) counts only credentials you
exported, stored with `/login`, typed with `--api-key`, or wrote into your
`models.json` — on any provider, including one that lists no models. Before this, a cloned repo's `OPENROUTER_API_KEY` sent
`--model anthropic/claude-haiku-4-5` to its owner's OpenRouter account although
you had exported `ANTHROPIC_API_KEY`. What remains: when you hold **no**
credential of your own for the route your flags chose — or the route a
`/model <provider>/<id>` naming a provider you defined (models.json, an
extension) chose — the file's key is the one used — the `Notice: loaded
credentials from …` line names it. (When your `models.json` gives a provider an
`apiKey`, that key now comes before the file's key for the provider — a
gateway re-pointed with `providers.openai.baseUrl` receives the entry's
`apiKey`, not the file's, ADR-0251 — unless the `apiKey` names the very
variable the file sets.) An untrusted
project's `.aelix/settings.json` is not read at all (#369, below). A trusted
one that sets `defaultProvider` alone does not choose between providers for a
`--model` id while you hold a key of your own (it counts only for a provider
your own key authenticates, or one you defined). One that sets the
`defaultProvider`/`defaultModel` pair picks the launch model when you pass no
`--model` — and the `.env` key then authenticates it: trusting the repository
made its settings yours (ADR-0252 §2.F); the model picked after `/login` reads
only your global saved default. A key name that is
not a plain environment name (`X,ANTHROPIC_API_KEY`) is refused outright. Nor does a
`.env` value **address** a request: a `{NAME}` placeholder in a base URL — your own
`models.json` template included — is filled from a repo `.env` only for Cloudflare's
two shape-checked ids; any other name (`TENANT_KEY`, admitted as a credential) leaves
the token unfilled, so the model is not runnable and aelix tells you to export it.
Before this, `https://{TENANT_KEY}.owner-gateway.invalid/v1` plus a `.env`
`TENANT_KEY=repo-chosen.invalid/v1#` sent the prompt and the repo's key to
`repo-chosen.invalid`.

## Answering the question

### Interactively

The first time you run `aelix` in a directory carrying any of these
resources, you get a selector (`project_trust.py:500-526`):

```
Trust project folder?
/path/to/repo/pkg/app

This allows Aelix to load .aelix extensions, MCP servers, and agent profiles,
which can execute arbitrary code on your machine — to load .aelix skills and
prompt templates, whose text is placed into the agent's instructions — and to
apply .aelix/settings.json, which can choose the model and provider your
prompts are sent to.

Trusting also loads the skills in .agents/skills here and in the folders above,
up to the repository root; found in ../.agents/skills and ../../.agents/skills.
```

The last sentence is always there; "found in …" names the `.agents/skills`
folders that made it ask, relative to the folder printed at the top (at most
three, then a count — `/skill` lists every one), so a folder name the
repository chose never reaches the question. More than three folders up, the
`..` are counted — `../×5/.agents/skills` — so the sentence does not grow with
the depth. The folder's own path at the top and the `Trust parent folder`
option still do: at 80x24, a path of about 160 characters fills the screen,
and a longer one, or one with a line break or wide characters in a folder name,
can push the last options below the bottom edge.

A write under `.agents/` is never auto-accepted, as one under `.aelix/` is not:
in the TUI `auto-accept-edits` and `auto` ask for it, and a delegated agent
without a UI is refused it, so neither leaves a skill there that a later run
loads unasked. (`yolo`, and a plain `-p` run, which have no one to ask, write
it as they write anything.) The rule goes by a `.agents` folder anywhere in
the path, as the `.aelix` one does, so it also asks about files that are not
skills (`.agents/plans/x.md`) and about every write in a project that itself
sits inside a `.agents` folder (`/work/.agents/proj/src/main.py`).

It also goes by where a write lands, not only by its name (#646): the file the
write and edit tools open, so a leading `@` or a `..` after a link changes
nothing. A write into a folder that aelix reads skills, extensions, settings,
agent profiles or prompt templates from is asked about the same way when that
folder is behind a link: the folder a `.agents`, `.agents/skills` or `.aelix`
symlink points at, the folder your `~/.agents/skills` or `~/.aelix` points at
when you work inside it (a dotfiles repository, say — you are asked when
editing those folders there, not elsewhere in it), the folder a link inside
one of these points at, the folders the `skills` and `prompts` paths in your
global or this project's `settings.json` name, the `skills:` and
`extensions:` paths of your and this project's agent profiles, the file
`AELIX_MCP_CONFIG` names, and the folder your `~/.aelix/agent/bin` points at
(bash runs the programs in it). A delegated agent started in a subfolder is
held to the folders of the project it was started from too. A `--skill`,
`--prompt-template` or `--agent-file` path is not one of them: you named it
for that run. Nor are a hard link to a skill file and the session folder. A
repository can make every write ask (a link to itself, `"skills": ["/"]`, more
entries and folders than one write may check, or agent profiles with more YAML
than one write may parse), which is the safe side. So can a tree of your own:
if one of these folders, or the folder it links to, holds more than 8,192
entries and links — a large checkout linked as your `~/.agents/skills`, say —
every write asks, in every project.

| Option | Effect |
| --- | --- |
| `Trust` | Trusted, and **remembered** for this directory. |
| `Trust parent folder (<parent>)` | Remembered against the parent, so every repo under it inherits it. |
| `Trust (this session only)` | Trusted now, **nothing written**. |
| `Do not trust` | Refused, and remembered. |
| `Do not trust (this session only)` | Refused now, nothing written. |

Cancelling (Esc / Ctrl+C) denies (`project_trust.py:1058-1060`). The text breaks
between words at your terminal's width, as pi's does (#380).

### `/trust`, after startup

The startup selector runs before the TUI exists, so declining once used to leave
editing `trust.json` as the only way to change your mind. `/trust` re-opens the
selector from inside a session (`tui/commands.py:1819`, registered at `:2369`)
and **saves** your answer — it does not change the session you are in. Restart
aelix for it to take effect; the message says so, as pi's does. A `/reload` does
not apply it either: the session's trust is decided once, at launch, and every
rebuild reuses it. (Until #369 the message said "Run /reload to apply it to
project-local resources."; measured in a pty, that was false.) For the same
reason `/trust` offers no "this session only" answers — one would change
nothing.

`/trust` shows what pi's trust selector shows (#380), not the risk text:

```
Project trust
/path/to/repo
Saved decision: trusted (inherited from /path/to)
Current session: trusted
    Trust
▸ ✓ Trust parent folder (/path/to)
    Do not trust
```

`Saved decision` is `none` when nothing is saved, and names the folder whose entry
decided. Where `.agents/skills` folders make the folder ask, a fifth row names
them, as the start-up question does: `Skills found in: ../.agents/skills`. `✓` marks the option that saved it, and the cursor starts there. `Trust parent folder` also removes the
folder's own entry, so a saved `Do not trust` here no longer outranks the parent.

### `--approve` / `-a` and `--no-approve` / `-na`

```bash
aelix --approve        # trust this project's .aelix resources for this run
aelix --no-approve     # ignore them for this run
```

Both **short-circuit** the whole resolution: no prompt, and **nothing is
persisted** (`project_trust.py:1008-1010`). They are per-run overrides, so they are
the right tool for CI and for a one-off look at an unfamiliar repository — and
the wrong tool for recording a decision.

Remember that `--no-approve` does not cover `AGENTS.md`. To refuse both, pass
both: `aelix --no-approve --no-context-files`.

### Headless

In `--print`, `--mode json` and `--mode rpc` there is no UI to prompt with, so an
undecided directory is **denied** (`project_trust.py:1052-1054`, pi parity). The
project-local resources are dropped and a notice naming them goes to stderr
(`cli/entry.py:3900-3905`, the text at `:996-1001`), because a silent drop looks identical to a
misconfiguration:

```
Notice: project-local .aelix resources (extensions, skills, agent profiles, settings) and .agents/skills skipped in an untrusted directory; pass --approve to trust.
```

A repository that carries only `.aelix/settings.json` is now in this group
(#369): a script that relied on such a file under `-p` must pass `--approve`,
or save a decision once (`aelix` interactively, or `/trust`).
`--list-models` decides the same way without asking — `--approve` /
`--no-approve`, then a saved decision, then your **global**
`defaultProjectTrust` (a repository's own value never counts), otherwise
untrusted — so a repository's `enabledModels` scopes the list only in a trusted
directory. When it was skipped, the same notice goes to stderr; the list on
stdout is unchanged.

## Where the answer is stored

`~/.aelix/agent/trust.json` — more precisely `<agent_dir>/trust.json`
(`project_trust.py:668`, `:682-684`). A JSON object mapping an absolute
canonical path to `true` / `false` / `null`, keys sorted, written atomically via
a temp file plus `os.replace` (`project_trust.py:740-760`).

Lookup walks **up** from your cwd to the first decided ancestor
(`project_trust.py:762-784`), which gives two useful properties:

- trusting `~/work` transitively trusts everything under it;
- a `false` on a child beats a `true` on an ancestor.

`null` means undecided: skipped by the walk, and never written by `set`.

To revoke, delete the entry (or the file). A malformed store is treated as *no
decision* rather than an error — it falls through to the prompt-or-deny path,
which is the safe direction (`project_trust.py:1038-1044`).

## The full resolution order

`resolve_project_trusted` (`project_trust.py:957-1067`), in order. The first step
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
   repository cannot set this by shipping its own settings file (which is not
   read until trust is decided anyway).
6. UI present → prompt. No UI → **deny**.
7. Persist the answer unless it was a "session only" one, and return it.

## Project settings follow the answer

`.aelix/settings.json` is the project scope of your settings: whatever it sets
overrides your global `settings.json` for runs in that directory. Since #369
([ADR-0252](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0252-project-settings-follow-project-trust.md),
pi `89a92207f`), an untrusted directory's file is neither read nor written —
aelix builds its settings without the project scope and adds it only once the
directory is trusted, before anything reads a setting. Before #369 it was read
whatever the answer, and a repository whose file named
`{"defaultProvider": "openai", "defaultModel": "gpt-4o-mini"}` and whose `.env`
held `OPENAI_API_KEY` sent a plain `aelix -p hi` to `api.openai.com` on its own
key — with `--no-approve` too — although you had exported your own
`OPENROUTER_API_KEY`.

What still holds in a **trusted** directory:

- **Its pair is applied** — the launch runs it when you pass no `--model`, and a
  `.env` key may authenticate it. Trusting a repository already lets its
  extensions run code; its settings are treated as yours, as pi treats them.
  This is a stated residual, not an oversight: guard 1 (a `.env` key
  authenticates a route, never chooses one) is **not** extended to a trusted
  project's pair, because the file, not the key, chose the route (ADR-0252 §2.F).
- **It never becomes your default elsewhere.** The model picked after `/login`
  starts from your **global** saved default only and is saved to your global
  settings, so a repository's pair can no longer be laundered into the
  default every other directory uses.
- **Some settings are global-only whatever the trust:** `defaultProjectTrust`,
  `features.agents`, `respectGitignore`, `checkModelCatalog`, and the extension
  install sources (`extensionSources`, `suppressedDefaultCatalogs`, which
  `aelix extension source …` reads and writes). A repository cannot choose them
  by shipping its own file.

## One sharp edge, on the record

If a directory had nothing to gate at startup, you were never asked, and step 2
trusted it. If trust-requiring resources appear *later* — a `git pull` that adds
`.aelix/extensions/` — and a `/reload` picks them up, aelix writes that implicit
trust to the store rather than asking
(`maybe_save_implicit_project_trust_after_reload`, `project_trust.py:829-894`).

It runs **after** the reload, so it does not stop the new resources from loading;
by the time it is called they already have. It converts "never asked" into
"granted, and remembered".

Issue #112 proposed making it a gate instead. That was measured to work and was
**declined** in favour of pi parity. The residual risk is real and is recorded
here rather than only in the tracker: a repository whose `.aelix/extensions/`
arrives mid-session via `git pull` is trusted without a prompt, and durably. If
you pull into a directory you have not read, restart rather than `/reload`.
The same holds for a `.aelix/settings.json` that arrives that way: the session
was trusted, so `/reload` reads it (pi's reload keeps the trust it started with).

## Related

- [extension-authoring.md](extension-authoring.md) — the project-local extension
  channel this gate protects.
- [agent-profiles.md](agent-profiles.md) — why the project tier of profiles is
  gated while the other two are not.
- [ADR-0149](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0149-v1-sprint2-project-trust.md)
  — the trust model.
- [ADR-0252](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0252-project-settings-follow-project-trust.md)
  — project settings follow project trust.
- [ADR-0203](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0203-dotenv-admission-control.md)
  — `.env` admission control.
- [ADR-0217](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0217-project-context-files-follow-pis-published-policy-and-forward-sync-to-its-fence.md)
  — why `AGENTS.md` stays ungated, and the fence that replaced verbatim
  injection.
