# Agent profiles

Status: Accepted

An **agent profile** is one markdown file that describes a sub-agent: what it is
called, what it may do, and what it is told. Aelix reads it when you delegate
work with the `agent` tool or `/agents run`, and again when you launch under an
identity with `--agent <name>`.

A profile is YAML frontmatter followed by a body. The body becomes the agent's
system prompt.

```markdown
---
name: reviewer
description: Reviews a diff for correctness and reports findings. Read-only.
tools: [read, grep, find, ls]
role: leaf
---

You are reviewing someone else's work. Report what is wrong, with file and line.
Say "no findings" rather than inventing one.
```

## Enabling delegation

**Delegation is off by default.** With it off, the `agent` tool is never built
and `/agents run` refuses — profiles still load and `/agents list` still lists
them, but nothing can spawn.

Turn it on for a single run with `--agents`:

```console
$ aelix --agents
```

Or persistently, with the **Agent delegation** row in `/settings` (it writes
`features.agents`). That one **takes effect the next time you launch aelix**,
not immediately: the flag is read once, while the harness is being built.

`--agents` and `--no-agents` are both run-scoped and both beat the persisted
setting, so `--no-agents` still shuts delegation off for one run when the
setting is on. The setting is read from your global settings file only — a
repository cannot switch delegation on by shipping its own
`.aelix/settings.json`.

## Where profiles live

Three directories are scanned, non-recursively, for `<name>.md`, in **ascending
order of precedence** — a later tier wins a name collision, and the collision is
reported as a warning naming both files:

| Tier | Location | Notes |
|---|---|---|
| `bundled` | inside the installed package | Ships with aelix. Always available; always loses a collision. |
| `user` | `$AELIX_CODING_AGENT_DIR/agents/`, default `~/.aelix/agent/agents/` | Yours. |
| `project` | `<cwd>/.aelix/agents/` | **Only when the directory is trusted** (`--approve`, or the trust dialog). |

Aelix ships two bundled starters: **`explorer`**, a read-only investigator
(`read`, `grep`, `find` and `ls`, as listed in the bundled `explorer.md`), and
**`general-purpose`**, a full-toolset worker that reads, edits, and runs commands
to take on a task whole. Both are `leaf` profiles (they do not delegate further)
and both declare `approval_mode: inherit`, which caps the child at your mode.
Under a `default` parent it asks about changes its gate still needs approved when
someone can answer (the TUI, or an RPC host that opted in) and runs `plan`
otherwise; under `auto` it runs
`auto-accept-edits` (`test_approval_clamp_612.py`,
[agent approvals](agent-approvals.md)). Delegation is off by default.

Writing `~/.aelix/agent/agents/explorer.md` replaces the bundled `explorer`
outright — the bundled tier is a starting point, not something you have to work
around.

The project tier is gated because a profile is an **identity**: it can replace
the system prompt and swap the model. It is also why a model-chosen delegation
can never use a project-scoped profile — only a human naming it can.

Run `/agents list` to see what is discovered and from which tier.

## Turning profiles on and off

A bare `/agents` in the TUI opens a panel like `/skill`'s: one row per profile,
grouped as PROJECT, GLOBAL and BUNDLED. Space turns the row on or off, ←/→ picks
the file to save to (global or this project), Enter saves and applies, Esc
discards. `/agents list` still prints the table, now with an `ON` column. `-p`,
`--mode json` and RPC have no panel and no slash command: in `-p`, `/agents` is
sent to the model as a prompt, and RPC `commands/run` answers `not_a_command`
(RPC reads the same state through `agents/list`, and lists and changes it
through `selections/list` and `selections/set` with kind `agent`).

An **off** profile is hidden from the `agent` tool's list and refused when the
model names it. It is not gone: you can still run it yourself with
`/agents run`, take it as your identity with `/agents use` or `--agent`, and an
extension or RPC host can still start it (see
[What this does not stop](#what-this-does-not-stop)).

The panel writes the `agents` key of the settings file you chose, in the same
grammar as `skills` (see
[skills-and-prompts](skills-and-prompts.md#the-settings-lists)):

```json
{
  "agents": ["-general-purpose", "!draft-*"]
}
```

| Entry | Meaning |
| --- | --- |
| `-name` / `+name` | that profile off / on, by its `name` field |
| `!glob` | off: every profile whose name or file matches |
| `-<path>` / `+<path>` | the exact file; a path in the project file reaches project profiles only |
| a plain entry | **not supported** here: warned about once at launch and ignored (it would add a profile directory) |

Three keys sound alike and are different:

- `agents` (this one) turns single profiles on and off;
- `features.agents` turns delegation itself on or off, from your global file only
  ([Enabling delegation](#enabling-delegation));
- `backgroundJobs.limits.agents` is a count of background jobs.

**Scope and trust are the skills' rules.** A profile's value is the project
file's if it says anything about it, else the global file's, else on. A trusted
project's file can therefore turn on a profile your global file turned off; it
cannot turn delegation itself on. An untrusted project's file is never read, and
its profiles are not loaded at all: the panel shows one locked row,
"N profiles not loaded", counted from a directory listing without opening a file.

**When a change takes effect.** Enter applies the change at once when no turn is
running; the model gets the new list from your next message. Saved during a
turn, it applies when the turn ends. A hand edit of `settings.json`, or another
session's save, is pending until you press Enter in the panel or run `/reload`;
when it moves no row (`!rev*` replacing `-reviewer`), the panel says in a line
of its own that the saved list is not the one in use yet. `"agents": []` turns
nothing off.

**Rows that are not plain on/off.**

- A profile that lost a name collision is a locked `shadowed` row under its own
  folder.
- A file that did not load is one locked `failed` row, whatever the number of
  errors in it; its state line starts with "see /agents list".
- A row that is on but that the model cannot start now says why in its status
  column: `not listed` (outside your identity's `agents:` list) or `/agents run`
  (a project profile; the model never starts one). A row this project's file
  turned off reads `project`, as a skill row does; its state line says off.

**A settings file that does not load** at launch turns nothing off: aelix says
once on stderr which file did not load and that its `agents` list is not
applied, and every profile it turns off is on for that run. Fix the file, then
`/reload`.

## Whom a profile may delegate to

A profile can name the profiles it may hand work to, with `agents:`:

```markdown
---
name: orchestrator
description: Plans the work and hands the reading to explorer.
agents: [explorer]
---
```

| Written | Means |
| --- | --- |
| no `agents:` key (or `agents:` with no value) | every profile that is on |
| `agents: []` or `agents: ""` | no profile: the session delegates to nothing |
| `agents: [explorer, reviewer]` or `agents: explorer, reviewer` | only those names |

It applies while the profile is the session's identity: `--agent`,
`--agent-file`, `/agents use`, RPC `agents/use`. It is read when the identity is
taken, so an edit of the file applies at the next `/agents use` or launch. A
delegated agent cannot delegate, so the key means nothing while the profile is a
delegation target.

Entries are profile **names**: lowercase letters, digits and single hyphens, as
`name` is. A glob (`expl*`) or any other entry that is not a name is an error,
and the profile does not load at all — not as an identity and not as a target;
`/agents list` shows the error. A name that no profile has is only a warning
(`agents: names 'reviewr', which no agent profile found here has`): a profile
created later is allowed from then on.

Neither list widens the other: a profile on the identity's list that is turned
off stays off, and a profile that is on but not on the list is refused.

## What the model reads

When the model names a profile it may not start, the `agent` tool refuses before
any dialog, before the per-prompt budget is charged and before anything starts.
The same refusal can also come once more just before the child starts, after you
answered the dialog: that re-check refuses a profile whose file changed under a
`-<path>` entry in between, still before the budget is charged and before
anything starts.

| Why | The refusal |
| --- | --- |
| turned off | `Delegation refused: agent profile 'reviewer' is turned off in the settings, and only the user can turn it on (with /agents); available: explorer, general-purpose` |
| not on the identity's list | `Delegation refused: agent profile 'reviewer' is not one this session's profile 'orchestrator' may delegate to; available: explorer` |
| the identity's list is empty | `Delegation refused: this session runs as agent profile 'orchestrator', whose agents: list is empty, so it delegates to no profile` |
| no such profile | `Delegation refused: unknown agent profile 'x'; available: …` |

`available` lists exactly what the model may use now. A resume of an agent whose
profile is turned off or not listed is refused in the same terms
(`Resume refused: the agent profile 'reviewer' that agent ran as is turned off …`),
and the list of resumable agents a resume without a known id gets leaves such
agents out, ending with `- N more ran as an agent profile this session may not
delegate to now; they cannot be resumed.`

When nothing at all may be delegated to, the `agent` tool's description says
why instead of listing profiles — for example `No agent profile is available:
every agent profile found is turned off in the settings. Only the user can turn
one on (with /agents).`

**An approved call keeps running.** Turning a profile off, or switching to an
identity that does not list it, applies from the next delegation the model
starts. A call already approved runs as approved, the members of a parallel
batch or chain still waiting for their turn included; a background job, or a run
Ctrl+B moved into one, runs to its end and its result still arrives. A later
`resume` of that agent is a new start and is refused.

### What this does not stop

The `agents` list and `agents:` decide what aelix's own `agent` tool offers and
starts. They are a policy on aelix's doors, not a security boundary. They do not
stop an installed extension that starts a profile through
`api.runtime.subagents.resolve_profile` and `spawn`, an RPC host that hands
`agents/run` to a model, or a model with `bash` that runs
`aelix --agent <profile> -p …`, which starts a new process with that identity.
Extension authors are asked to use the filtered door
([below](#extension-tools-the-model-calls)); nothing enforces it.

### Extension tools the model calls

With delegation on, `aelix.runtime.subagents` is the bound delegation runtime
(`None` when delegation is off: degrade). Its `resolve_profile` and `spawn` are
the doors a **human** uses — `/agents run`, RPC `agents/run` — and they do not
apply the user's `agents` settings list or the session identity's `agents:`
([Turning profiles on and off](#turning-profiles-on-and-off)).

A tool **the model can call** that starts a delegated agent must get its profile
from `resolve_for_model` instead, and hand its refusal back to the model. A
runtime that does not offer it is not a `ModelDelegation` and cannot say which
profiles the model may start, so such a tool refuses:

```python
from aelix_coding_agent.subagent_contract import ModelDelegation

runtime = aelix.runtime.subagents
if not isinstance(runtime, ModelDelegation):
    return error("this aelix cannot check which agent profiles the model may start")
try:
    resolved = runtime.resolve_for_model(profile)
except Exception as exc:  # every exception is a refusal; its text is for the model
    return error(f"Delegation refused: {exc}")
result = await runtime.spawn(resolved, task)  # still asks its own consent
```

`resolve_for_model` raises `DelegationNotAllowed` (from `subagent_contract`)
for a profile the settings turn off or the identity does not list, and an error
of its own for an unknown name or a project profile, which no model may start.
It is not consent: `spawn` still asks, and with no UI (`-p`, RPC without dialogs)
it does not ask. And it is a policy on aelix's own doors, not a security
boundary — nothing makes an extension use it.

### Older aelix

An aelix from before these keys (0.1.0b2 and earlier) ignores both. It keeps a
profile's `agents:` as a diagnostic, `unknown frontmatter key 'agents'
(ignored)`, that `/agents list` shows — a `--agent … -p` launch printed no such line —
and then lets that profile's session delegate to every profile. It drops a
top-level `agents` settings key without a word, so nothing is turned off, and a
later write of another setting by that aelix keeps the `agents` value in the
file. That launch was run on the commit this change starts from; the
rest is read from that code. Neither is measured on an installed 0.1.0b2: #383
owns that check.

## The frontmatter keys

All eighteen. `name` and `description` are required; everything else has a
default. An unknown key is a warning, not an error — your profile still loads.

| Key | Type | Default | What it does |
|---|---|---|---|
| `name` | string | — | The identity. **This field, not the filename**, is the name you delegate to; a mismatch warns. |
| `description` | string | — | What the model reads in the `agent` tool's schema to decide whether this profile fits the task. Write it for that reader. |
| `model` | string | inherit | Model id for the child. `inherit` and an absent key both mean "use the parent's model" — see below. |
| `provider` | string | inherit | Provider for the child. Set it whenever you set `model` — or write `model: <provider>/<id>`, which resolves exactly like `--model` (see [How the child reaches its model](#how-the-child-reaches-its-model)). |
| `tools` | list \| `[]` \| absent | absent | **Three-valued.** Absent → the child inherits the ambient tool set. A list → exactly those tools. `[]` → **no tools at all**. |
| `builtin_tools` | bool | `true` | Whether the built-in tools are available at all. |
| `skills` | list of **paths** | `()` | Skill files, resolved **relative to the profile's own directory**, not your cwd. |
| `inherit_skills` | bool | `true` | Whether the parent's skills come along. |
| `extensions` | list of **paths** | `()` | Extension files, same relative-to-the-profile rule. **Forbidden at project scope.** |
| `inherit_extensions` | bool | `false` | Whether the parent's extensions come along. |
| `system_prompt` | `append` \| `replace` | `append` | `append` adds the body to the standard prompt; `replace` uses the body alone. |
| `context_files` | bool | `true` | Whether `AGENTS.md` and friends are loaded. |
| `thinking` | string | inherit | One of `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Absent → the parent's level if the profile also names no `model` or `provider`, otherwise the `defaultThinkingLevel` in your global `settings.json` (see below). A bad value here **rejects the profile**. |
| `role` | `leaf` \| `orchestrator` | `leaf` | A `leaf` child cannot delegate further. See the note below. |
| `output_cap` | int | `51200` | Byte budget for the summary returned to the parent; past it the summary is truncated with a visible marker. A parallel or chain call also shares one 64 KiB budget, split evenly across its tasks (8 192 bytes each for eight) and covering each task's summary and error note together, so a member can be cut shorter than its own cap. A single delegation gets this cap alone. When the child has a session file, the full text is kept there. |
| `timeout_ms` | int | none | **Ignored (#604).** A delegated agent has no time limit; it runs until it finishes or you stop it (Esc, Ctrl+C). The key still loads, with one warning in `/agents list`, so an old profile keeps working. |
| `approval_mode` | `inherit` \| `ask` \| `auto` \| `deny` | `inherit` | How the profile **declares** it needs write authority. |
| `agents` | list \| `[]` \| absent | absent | **Three-valued.** The profiles this one may delegate to while it is the session's identity: absent → every profile that is on, `[]` → none, a list → only those names. An entry that is not a profile name fails the whole profile. See [Whom a profile may delegate to](#whom-a-profile-may-delegate-to). |

### The ones that are easy to get wrong

**A profile that names no model inherits the parent's.** Leave `model` and
`provider` out and the child runs on whatever the parent is using at the moment
it delegates — including a model the parent was launched with (`--model`) or
switched to mid-session with `/model`. This is what lets the bundled profiles,
which declare no model, work under any provider.

Inheritance is all-or-nothing: set **either** `model` **or** `provider` and the
parent's model stops being forwarded entirely. Pairing your `provider` with the
parent's model id would produce a combination neither of you asked for, so a
profile that names half a model is treated as naming a model. If you set one,
set both.

**A profile that inherits the parent's model inherits its thinking level
too.** Leave `model`, `provider` and `thinking` out and the child runs at the
level the parent is using when it delegates, whatever set it: `--thinking`, the
`thinking` of the profile the parent runs as (`--agent`, `/agents use`), the
level a resumed session recorded (`--continue`, `--resume`, `--session`,
`--fork`), `/thinking` or an RPC client's `set_thinking_level`, and
`defaultThinkingLevel` when nothing else set one — in every mode, the TUI,
`-p`, `--mode json` and `--mode rpc` alike. A parent at `off` hands its child
`off`. A profile's own `thinking: <level>` always wins. A profile that names
its own `model` or `provider` gets no level from the parent — a level is
chosen for a model, and the parent's says nothing about yours — so it runs at
its own `thinking`, or, if it sets none, at the `defaultThinkingLevel` in your
global `settings.json` fitted to its model (`xhigh` becomes `high` on a model
that tops out there, anything becomes `off` on a model that cannot reason).
With no default set that is `off`, or the lowest level of a model that cannot
switch reasoning off. A `defaultThinkingLevel` in the project's
`.aelix/settings.json` does not reach it, even in a project you trust: a
delegated agent is started with `--no-approve` whenever that file exists, so it
never loads the project's settings (unlike `aelix -p --model …` run by hand in
that project). Give such a profile a `thinking` when its model needs a level
your default does not give it — above all when the model will not switch reasoning off:
before the parent's level was passed on, every delegation on such a model
failed on its first request with `Reasoning is mandatory for this endpoint`.

**`tools: []` is not `tools: ""`.** An empty list means *no tools*. It is not the
same as the `--tools ''` command-line form, which inverts to *every* tool.

**`tools:` and `builtin_tools: false` are locks**, like the `--tools` and
`--no-builtin-tools` flags they become: no extension and no `/reload` can turn
on a tool they leave out, and `/tool` shows those rows locked as `profile` until
`/agents use --none`. A delegated agent also gets its parent's
`--exclude-tools`, as typed, and every tool the parent's `defaultTools` keeps
off; `/agents show` lists them among the flags ([tools](tools.md)).

**`skills:` and `extensions:` take paths, not names**, and they resolve against
the directory the profile file is in. This differs from pi. A missing path is a
hard error, so a profile that names a file you later moved will refuse to load
rather than run with less than it declared.

**`extensions:` is refused for a project-scoped profile.** An extension is code,
and a profile in a repository you cloned is content you did not write. Skills are
allowed there because a skill is prompt text.

That asymmetry is also why the two `inherit_*` defaults differ:
`inherit_skills` is `true`, `inherit_extensions` is `false`.

**`thinking` is stricter here than on the command line.** `--thinking bogus`
warns and continues; `thinking: bogus` in a profile is fatal. A profile is
persistent configuration — a typo that silently downgrades reasoning on every
future run is worse than a refusal now.

**`system_prompt: append` puts the body first**, ahead of anything you pass with
`--append-system-prompt`.

**`role: orchestrator` is parsed and validated but not yet honoured.** Every
child is currently spawned with delegation switched off, so no profile can start
a third generation regardless of what it declares. Tracked in
[#123](https://github.com/handochan/aelix-ai/issues/123).

**`approval_mode` cannot grant authority by itself.** A child is always clamped
to at most the parent's own posture. What `approval_mode: auto` does is *declare*
that the profile needs to write, which is what lets the spawn-consent dialog
offer you the choice to widen it. You are still the one who answers — with one
exception, below.

**In `yolo` posture you are told, not asked.** `yolo` means "run mutating tools
without a prompt", and a delegation is the one thing it used to prompt for
anyway. It no longer does: a write-capable child starts immediately and the
status line names it while it runs — the profile, the posture it runs at, the
file it came from, and how many tasks were handed to it. When it finishes, the
tool card's own `[agent … · yolo · …]` footer names the posture again — for as
long as the card has room for it. That footer is the last block of the result
text, so the tool-card line cap takes it first, and how long it lasts depends on
how the delegation ended. On a **successful** one carrying no extra note it
stays until the child's summary passes 3 lines, at the default cap of 5 (#247);
a `dropped_tools` or `dropped_lines` note costs two more rows, so it goes
sooner; a **failed** delegation renders on the separate 40-line error path and
keeps it much longer. Past whichever threshold applies the card ends in
`… (+N more lines · /expand N)`, and `/expand N` prints the footer with the rest
of the body. Nothing else changes; the child is still
clamped to your posture, still hard-blocked by the guardrail, still bounded by
the delegation caps, and still shown in the delegation panel while it runs. Every
other posture — including `auto-accept-edits` and `auto` — still asks.

## How the child reaches its model

The child is launched with `--model` / `--provider` flags and resolves its route
in its own process, with the environment it inherited — your keys, and the
record of which ones a project `.env` supplied (`AELIX_DOTENV_ADMITTED`), so it
weighs them exactly as the parent does — and, by default, **no extensions**
(`inherit_extensions: false`). That is why the parent does one thing for it: it
resolves the profile's `model:` (or its own model, when the profile names none)
exactly as its own `--model` would, and when that lands on a
provider **you** defined — a `models.json` provider, a built-in you re-pointed
with a `models.json` `baseUrl`, or one an extension registered — the child gets
`--model <id> --provider <provider>` instead of the string as written
([ADR-0249](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0249-a-model-openrouter-cannot-serve-is-resolved-before-openrouter-from-env.md),
[ADR-0250](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0250-model-routing-follows-pi-and-a-dotenv-credential-cannot-choose-a-route.md)).
The child then reaches that provider, or — for an explicit model registration it
did not reproduce — stops with an error naming it. This also applies when the
extension registered a **built-in** name (`openai`, say): the vendor's catalog
cannot substitute for the parent's explicit owner. Enable `inherit_extensions`
or supply the same registration in profile `extensions` (explicit `-e` still
loads with discovery disabled). A same-name/id registration on another host/API
is refused too. The child checks its requirement after setup and on every
request, so unregister, auth-only re-registration and model switches in hooks
cannot remove it; closing checks also run after request/payload hooks finish.
Ordinary builtin children and child-only extension selections
retain their behavior. `/agents show` and consent use the same model decision;
the internal route requirement contains a digest, never a URL or credential.

With `inherit_extensions: true` the child loads your global and installed
extensions the way the parent does. It runs as `python -P -m aelix_coding_agent`
in its working folder, so a file there named like one of those extensions'
modules is not imported in its place (#641).

Everything else reaches the child as written and is resolved by the same rule as
`--model` (see
[providers-and-models.md](providers-and-models.md#how-a---model-string-becomes-a-provider)):
`model: openai/gpt-4o-mini` runs on OpenRouter when the inherited keys include an
OpenRouter key and no OpenAI key of your own, and on OpenAI when they include an
OpenAI key; add `provider: openai` (or write `openrouter/openai/gpt-4o-mini`) to
pin it. A profile that brings its **own** `extensions:` is left to resolve its
`model:` with them — the parent never loaded them, so it does not guess. A route
that only the parent's `--api-key` would decide is not pinned either: the key
never reaches the child, and a child pinned to that provider would authenticate
it with whatever it has — a project `.env` key included.

## How the child authenticates

A delegated child is a separate process, and it resolves its own credential
rather than being handed the parent's. Two of the four setup paths carry over
without any further work: keys set as environment variables (`ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`, …) are inherited, and anything `/login` wrote into
`auth.json` is read by the child directly.

`--api-key` does **not** reach a child — it lives in the parent process's memory
only, and it is deliberately not forwarded: putting a key on the child's command
line would publish it to every process on the machine through `/proc`, and
handing it over in the environment would put it somewhere the child's own `bash`
tool can read. Measured: with `--api-key` as the *only* credential, the child
stops before its first turn with `No API key found for <provider>` and that
message reaches you in the delegation's summary. Set the environment variable or
run `/login` as well.

## What you see while a child runs

Every delegation the model makes — one child, a batch, a chain or a resume —
draws one panel above the input
([#132](https://github.com/handochan/aelix-ai/issues/132), ADR-0278):

```
│ ● agent worker · s-1 · running                     idle 7s · 1m12s · 1.5k tok
│   ✓ read notes.txt
│   ✗ $ pytest -q tests/test_retry.py
│   ▶ $ sleep 12 · idle 7s
│   “Read the notes; one test fails, re-running the slow step.”
```

One child is five rows: its state, model and numbers; its last three tool calls,
each marked done (`✓`), failed (`✗`), cut off (`■`) or running (`▶`); and its
last reply. Two to four children are a header with the counts and two rows
each — the task, then the calls and what it is doing now — and five or more,
or a terminal too short for that, one row each. The panel keeps its height for
the whole call, is as wide as the transcript, and its clock moves once a second
while a child is quiet (`idle 7s` after five seconds). When the terminal is too
narrow for a whole header, it keeps the elapsed time to the last and, beside
it, whatever fits of the profile, the model, the tokens, the idle time and the
cost, in that order — so a long model id that does not fit leaves the tokens
on the row — and for the first ten minutes what it shows does not change as the
clock ticks; a batch's counts are always shown. A task label that fits beside
its numbers is shown whole, and for the first ten minutes the clock alone cuts
it at most once, except on a narrow terminal, where telling the tasks apart
comes first. A label can still be cut again when the numbers beside it change
width for another reason: a child's first token count, a count that falls
after a compaction, the children coming to agree on a model, or the tokens
passing 100k. A call
still running keeps its row however many calls finish after it. Children the model
started add no status-line rows; a child you start with `/agents run` still
shows one row in the status line, `agent <profile> · <model> · <tool> · 12s ·
…`. If you press `Esc`, the panel's last frame stays until your next prompt:
each child marked stopped, the call it was cut off in, the files it changed,
and — when the child has a session — that you can ask the model to continue it
(a `--no-session` parent's children have none, and the frame says they cannot
be resumed). On a narrow terminal these phrases are left out whole rather than
cut mid-word. It refits within a second if you resize the terminal.

The model, the tool names and arguments, and the last reply on the panel are
what the child reports about itself — a tool name is whatever the child's model
asked for, real tool or not — so aelix treats them as untrusted, and the task
text the model wrote too. The profile name is the profile's `name` field, which
aelix has already checked (lowercase letters, digits and hyphens, at most 64),
so there is nothing in it to clean; it goes through the same cleaning anyway,
because an extension that supplies its own subagent runtime can report any
profile name it likes. The progress card an `agent` call streams to extensions
and to `--mode json`/`rpc` consumers, and the `[agent … ]` footer of its
result, carry the same names and are cleaned the same way
([#178](https://github.com/handochan/aelix-ai/issues/178)):

- Control characters and BiDi overrides are removed. That defangs an escape
  sequence rather than removing it: its ESC character goes and the rest of the
  sequence stays on the row as plain text, so a tool name carrying `\x1b[31m`
  shows `[31m` instead of turning the row red.
- A lone UTF-16 surrogate, which no UTF-8 output can write, becomes `�`
  (U+FFFD), so an RPC client is never sent a panel it cannot encode.
- Every line break, tab, run of spaces and non-ASCII space (a no-break space,
  the ideographic space U+3000 and the like) becomes one ordinary space, and
  leading and trailing spaces go, including any a removed character leaves
  behind. A tool called `read\u00a0file` (with a no-break space) shows as
  `read file`, so a tool name that relies on a no-break space or U+3000 to read
  right loses it.
- A name too wide for where it is shown is cut with `…`. On a `/agents run`
  child's status row and in the progress card that is 78 columns for the
  profile and the tool name, and 40 for the model on the status row; a name of
  emoji that has to be cut is cut to fit as the terminal draws it, so it can
  come out a little shorter than 78 or 40. On the
  panel the profile and the model get at most 32 columns each, an agreeing
  batch's model is named in the header only when it fits beside the counts,
  and a row stops at the terminal's width as a whole — measured the way the
  terminal draws it, so a row of flags, skin tones or joined emoji is cut
  with `…` rather than run off the edge — and a task label that must be cut
  is cut in the middle, so the end that tells two tasks apart stays.
  The result footer gives the profile and the model at most 40 each. A name
  that is narrow but made of more than four characters per column of that
  limit — a letter carrying hundreds of combining accents — is cut too.

A name in any script with no control character or BiDi override, no spaces
other than single ordinary ones between words, and that fits where it is shown,
is shown as written.

## What a delegation leaves behind

Each delegated child gets a session file of its own, next to yours: a session
saved as `<name>.jsonl` keeps its children in a directory beside it,
`<name>/sub-<id>.jsonl`, one per delegation (a resumed agent goes on in the
same file). The child's whole transcript is there. Your own session keeps only
a receipt for each run of a child — the profile, the limits it ran under, the
model that ran, how it ended, what it spent — and the summary it handed back;
the child's intermediate steps never enter your context.

- `--continue`, `/resume` and the session pickers never offer a child file.
- To read one, export it: `aelix --export <child file>` writes an HTML page and
  runs nothing. Opening it with `--session` prints a warning: the child file
  does not carry what bounded the child — its permission clamp, its narrowed
  tools — so opened by hand it runs as an ordinary session at your own
  posture. Those limits are in your session's receipt, and only a resume from
  the session that delegated it (below) runs the agent inside them again.
- What the children spent is part of your session's totals in `/cost` and
  `/stats`, broken out on a line of its own. While one is still running, the
  cost reads as a floor (`≥ $…`).
- A session started with `--no-session`, or whose file is not inside a
  sessions directory's per-project folder (`--session /some/where/file.jsonl`,
  or a file placed straight in the sessions directory), delegates without child
  files, as before. So does a delegation whose child file could not be created
  — a disk error, or a path too long.
- Nothing deletes child files. They stay until you remove them, as sessions do.

### Resuming an agent

When a delegated agent has a session file, its result names it. A single
agent's result has a `Session: <id>` line whether the agent finished or was
stopped or failed after it started. In a parallel or chain call, a member that
finished has that line in its block, and a member that did not finish has the
id in its heading instead (`… did not finish: <reason> · session <id>`). The
model can hand that id back: `agent(action="resume", id=…, profile=…, task=…)`
continues the same agent in its own session file, with `task` as its next
instruction, instead of starting the work again. When a single agent stopped
or failed after doing some work, its result tells the model not to start that
work again from scratch: to check the files it changed and its tool calls,
and, if it should go on, to resume it — the call is spelled out with the
agent's profile and id. A member of a parallel or chain call gets no such
line. When you stop a delegation with Esc, the report the model gets says the
stop was intended, and tells it to resume or repeat that work only if you ask. Any stop — Esc, quitting, a SIGTERM or Ctrl+C to aelix —
also ends the command the agent's `bash` tool was running, what that command
started, and what it put in the background with `(cmd &)` while it was still
running. What a command that had already finished left in the background
with `cmd &`, `(cmd &)` or `nohup cmd &` — a dev server started with
`npm run dev &`, say — stops too, when the delegation ends and when it is
stopped, on macOS and Linux (#719, ADR-0296); on Linux so does one that started
a session of its own and cleared its environment, such as a daemon's double
fork. On macOS such a process — one that left its process group and cleared its
environment, or left its group and is a system binary like `/bin/sleep` — is
not found and keeps running. Your own `bash` calls, outside a delegation, still
leave such helpers running. A delegation's result can come up to a few seconds
later while what it left is stopped. On macOS, killing aelix with SIGKILL
closes the delegated child's command inbox (#123). See the
[parent-death measurements](background-jobs.md#if-aelix-itself-dies) for
delegated processes and a top-level background `bash`.
A resumed agent is told that it is being resumed, that it
should not redo work it already finished, and that a tool call showing no
result may or may not have run. The model can use `agent(action="resume")`.
An idle RPC host can send `children/resume {id, task}` when its runtime supplies
`HostResume` and advertises the method (#836). Both paths require a model-delegated
agent, not one started with `/agents run` or `agents/run`.
See [Over RPC and ACP](background-jobs.md#over-rpc-and-acp).

**A resumed agent runs inside the limits it was first delegated with, and they
never widen.** Each delegation records them in your session: the permission
posture it was granted, its tools, whether it loads context files, its thinking
level, its model and provider, and its directory. A resume takes the narrower of
those and what a new delegation would get from your session now, item by item:
if you have since moved to `yolo` or allowed more tools, the agent does not get
them; if you have narrowed, it narrows too. It keeps the model it ran with, even
after a `/model`. It keeps its thinking level too when its first run was given
one — the profile's `thinking:`, or your level when the agent runs on your
model (above). A profile that names its own `model` or `provider` and no
`thinking:` is given none, so each of its runs, a resume included, starts at
the `defaultThinkingLevel` in your global `settings.json` as it is then, as a
new delegation does. Its prompt, skills and extensions are the profile file as
it is now.

**You are asked again, as for a new delegation.** The same rules decide whether
a dialog opens — in `yolo` you are told, not asked (for an agent that may
write, the status line reads `resuming <name> at <posture>, from <file>`), and
`-p` never asks — and the dialog reads `Resume agent '<name>'?`, with the
agent's session id, its profile and the file it came from, its directory, the
posture it will run at (with its first run's, when that was wider:
`Permission: plan (first run: auto-accept-edits)`), its model, the task it was
first given and its next instruction. It can be widened to file edits, as a new
delegation can, only if its first run was allowed them.

**The posture is the lower of the two.** A resume runs at the lower of the
posture its first run was granted and the one a new delegation would get from
your session now. A `-p` run started without `--permission-mode` gives a new
delegation `plan`, so it resumes the agent read-only even if the agent first
ran under `yolo`. Start the second `-p` run with the same `--permission-mode`
as the first to resume the agent as it ran then.

A resume is refused before anything runs, and the model is told why, when:

- the id is not one this conversation's model delegated — the refusal then
  lists up to ten agents it can resume, newest first, with how each last ended;
- you started the agent yourself with `/agents run`, or an RPC client did;
- it was delegated before aelix recorded an agent's limits
  ([#610](https://github.com/handochan/aelix-ai/issues/610)) — delegate the
  task again;
- this conversation was forked (`/fork`, `--fork`) from the one that delegated
  it: the agent stays with the original conversation, and is resumed there;
- your session is not saved in a sessions directory (`--no-session`, or a file
  opened from elsewhere);
- its session file is gone, damaged or under a second name (a hard link), it
  or the `<name>/` directory that holds it is a link (a link higher up, such
  as a sessions directory on another volume, is not refused), or the agent
  never received its task;
- its profile is gone, renamed, now resolves to a different file, or is a
  project profile;
- the directory it ran in is gone, was moved or replaced, or is no longer
  inside yours;
- the provider registration its model was pinned to has changed;
- it is already running — in this session, in another process, or because its
  file is open in `aelix --session`.

All of this is checked when the call is approved and again right before the
agent starts, so a change made while the dialog was open is refused too.

Each resume is a run of its own in `/cost` and `/stats`, added to your
session's totals with the rest of that agent's spend. The rules in full are in
[ADR-0277](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0277-a-delegated-agent-can-be-resumed-inside-the-limits-it-ran-with.md).

## Background delegation

In the TUI, `-p` and `--mode json`, with delegation on, the model can also run an agent
in the background and go on with something else (#611,
[ADR-0295](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0295-a-delegated-agent-can-run-in-the-background-as-a-job-of-its-session.md)).
The agent is a background job of your session; what all jobs share — the footer,
`/jobs`, the settings, what is left when aelix is killed — is in
[background-jobs.md](background-jobs.md).

`agent` then takes seven more actions:

| Action | What it does |
| --- | --- |
| `spawn` | Starts one task in the background, with the same approval a single run has, and returns once the child has started, with its agent id and job id. |
| `wait` | Waits up to `timeout` seconds (30 to 1800, default 120). It never stops an agent and never returns a result: the notice brings it. A third wait in a row that sees nothing end, in one prompt, is refused. |
| `status` | One agent, by `id`, or every agent of this session. |
| `stop` | Stops one agent, by `id`; it ends `stopped` once its process is confirmed gone, and its notice carries what it had said. |
| `result` | Reads a finished agent's whole result again, 16 KiB a page, from `offset`. |
| `send` | A note to a running agent, by `id`, in `task`; `mode="follow_up"` lands it after its current answer (#123, below). |
| `wrap_up` | Asks a running agent, by `id`, to finish now: one more reply with no tools (#123, below). |

When a background agent ends, the model gets one notice with its answer, cut to
16 KiB; a longer one says to read the rest with `agent(action="result", id=…)`
(`tests/agents_ext/test_agent_background_611.py`). `background: true` is not a key of
`agent()`: the call is refused and told to use `spawn`
(`::test_where_the_kind_is_registered_background_true_points_to_spawn`).

### Moving a running agent with Ctrl+B

In the TUI, while one `agent` call with one task — or a resume — runs in the
foreground, **Ctrl+B** moves it into a background job without starting it again
([ADR-0298](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0298-ctrl-b-moves-a-running-delegation-into-a-background-job.md)).
The call returns at once with "Moved to the background at the user's request: …",
the agent keeps running, and its result comes back once as a notice
(`tests/agents_ext/test_detach_611.py`). With nothing to move — no running call or
several, an interrupt or abort pending, a session change or reload running, the
completion menu open — Ctrl+B is the cursor-left key it always was; under a dialog it
does nothing (`tests/tui/test_detach_key_611.py`).

**In tmux, press it twice:** Ctrl+B is tmux's own prefix key, and tmux passes it on
only the second time (owner OQ-S3-3, 2026-11-12: "Ctrl+B만 + 가이드에 '두 번' (권장)").
An extension's own `ctrl+b` shortcut is skipped while background jobs are on, as any
shortcut that collides with a built-in key is; `/hotkeys` lists Ctrl+B.

### Steering a running agent

Every delegated child keeps a command inbox on its stdin while it runs (#123, ADR-0306);
it is still the `--mode json -p` process it was, with the same result.

- **A note** — `agent(action="send", id=…, task=…)`, `/jobs steer <id> <text>` or the panel's
  `m` key: read at the child's next turn boundary, after the tool calls it is already
  running (`mode="follow_up"` / `--after`: after its current answer). The answer is `queued`,
  `unknown` (no answer in 30 s: do not resend; the child ended: resume it), `not_sent` or a refusal naming
  the state, never "not delivered" (`tests/agents_ext/test_agent_send_123.py`).
- **A wrap-up** — `agent(action="wrap_up", id=…)`, `/jobs wrap <id>` or the panel's `w` `w`:
  its next model call has no tools (nor an automatic retry of it), then the run ends `aborted`, reason "wrapped up at the
  user's request" (`agent`: model's), `ok=False`, its own text as the summary, "all done" or
  not (`tests/agents_ext/test_command_wire_123.py`).
- **A tighter posture** — shift+tab reaches every running child above the new rank, governs its
  approvals from then on, and stops one that does not apply it within 30 s
  (`tests/agents_ext/test_permission_floor_123.py`); so does RPC `permissions/set`, idle or with `tighten: true`.
- **A stuck mark** — four identical calls and results, or three identical errors: `stuck:
  repeat` / `stuck: error loop` on the panel, one note, never a stop
  (`tests/agents_ext/test_stuck_123.py`); in the background the parent model is told once.

`stop` is unchanged (ADR-0291). An RPC host reaches these doors too ([background-jobs.md](background-jobs.md#over-rpc-and-acp)).
A `default` parent's TUI child asks about changes its gate still needs approved ([agent-approvals.md](agent-approvals.md)).

### How many run at once

| Host | Bound on live delegated agents | Can you change it |
| --- | --- | --- |
| TUI, `-p`, json, `--mode rpc` | `backgroundJobs.limits.agents` (default 4), foreground and background together, and `backgroundJobs.maxTotal` if you set it | yes, in your global settings (1 to 16; `maxTotal` 1 to 32) |
| `backgroundJobs.enabled: false` | `MAX_LIVE_CHILDREN = 4` | no |

`--mode rpc` has the background actions and the settings' bound since #612
(owner, 2026-11-18: "전부 열기 (권장)"; ADR-0307 D7; ACP:
[background-jobs.md](background-jobs.md#over-rpc-and-acp)).
Where the bound is reached, a call is refused before its dialog, and the refusal
names the setting.

### If aelix is killed while an agent runs

See the [platform measurements](background-jobs.md#if-aelix-itself-dies).
In the #123 macOS probe, the child, shell and sleeper exited in 0.4 s or less
in 10 of 10 runs per delegation case. The top-level background `bash` survived.

## A second example

Exercising `system_prompt: replace`, an empty tool set, and an absolute skill
path:

```markdown
---
name: summarizer
description: Rewrites long text into a short brief. Does not touch the filesystem.
tools: []
builtin_tools: false
system_prompt: replace
context_files: false
skills: [/opt/team/skills/house-style.md]
inherit_skills: false
thinking: low
output_cap: 8192
---

You turn long input into a brief a busy reader can act on. No preamble.
Lead with the decision or the finding. Keep it under 200 words.
```

With `tools: []` and `builtin_tools: false` this agent can only read what it is
given in the task and reply — which is exactly what you want for something whose
job is text.

## Checking your work

- `/agents list` — what is discovered, and from which tier.
- `/agents show <name>` — the parsed fields, plus the flags the profile puts on
  a delegated child's command line. Nothing spawns. The rest of that command
  line — the child's session file, `--permission-mode`, the trust flags,
  `--no-agents` — is decided when a delegation actually runs.
- `--agent <name>` — launch as that identity yourself, which is the fastest way
  to find out whether the prompt and tool set do what you intended.
- `/agents use <name>` — switch the running session to that identity without
  restarting (`/agents use --none` drops it). A profile whose `model:` or
  `provider:` applies — one you did not override with `--model` / `--provider`
  — switches the model too; `provider:` alone is enough, so a profile whose
  `model:` your `--model` overrides but whose `provider:` applies switches to
  your `--model` on that provider. When a use switches the model, `/new`
  after it starts on that same provider and model, and so do `/fork` and
  `/resume` where they do not bring back a session's own model (`/reload`
  keeps the model the session is on):
  a profile's `model: openrouter/auto` stays OpenRouter's `auto`, not its other
  id `openrouter/auto` (that one is what `provider: openrouter` beside your
  `--model openrouter/auto` names, as `--provider openrouter` does). Two
  corners, both from a `models.json` or extension provider (one you defined in
  `models.json`, or one an extension registered with `register_provider`),
  where `/new` moves anyway: such a provider whose name differs from a
  built-in one only in case (`OpenAI` beside `openai`) is the one `/new`
  starts on after a use that landed on the built-in one, since a named
  provider means yours first (as `--provider openai` does); and such a
  provider listing both `mygw/x` and `mygw/mygw/x` runs `mygw/x` for
  `model: mygw/mygw/x`, then `mygw/mygw/x` after `/new`. When
  nothing of it applies (the profile names neither, or your flags override
  each one it names), and with `--none`,
  you stay on the model you are on — the model a reopened session came back on,
  a `/model` pick, or the launch model — and no model change is written into
  the session; `--none` then reports "the model is unchanged". The exception
  is replacing a profile whose own `model:` or `provider:` applied: that
  re-derives the model from your launch flags or settings default. It is not
  quite what a launch with those flags does: it does not use your settings
  `defaultProvider` to choose among providers that serve a bare `--model` id,
  so such an id can land on a different provider (and `/new` after it stays
  on that one); and with no `--model`/`--provider` and no settings
  default there is nothing to re-derive, so the replaced profile's model stays
  (where `/new` starts as a launch with no model would). A `thinking:` the
  profile names, or your `--thinking`, is applied either way, fitted to the
  model as `defaultThinkingLevel` is (`xhigh` runs at `max` on a model that
  names `max` and no `xhigh`), so a `/thinking` pick does not survive it yet. If the next prompt on the model you keep would
  be refused (no API key, an id no provider serves, configuration missing), the
  status line says why, with the reason the check before every prompt gives. In
  the TUI, a model that cannot run at all (the last two) is refused before that
  check: with the same reason while any model is available, and with "No
  provider configured. Run /login to add one." when none is. The footer's `✱`
  model and `🧠` level change as soon as the status line appears, and a use
  that fails leaves them on the model and level you stayed on.
