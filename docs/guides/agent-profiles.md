# Agent profiles

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
tools: [read, grep, glob, list]
role: leaf
---

You are reviewing someone else's work. Report what is wrong, with file and line.
Say "no findings" rather than inventing one.
```

## Where profiles live

Three directories are scanned, non-recursively, for `<name>.md`, in **ascending
order of precedence** — a later tier wins a name collision, and the collision is
reported as a warning naming both files:

| Tier | Location | Notes |
|---|---|---|
| `bundled` | inside the installed package | Ships with aelix. Always available; always loses a collision. |
| `user` | `$AELIX_CODING_AGENT_DIR/agents/`, default `~/.aelix/agent/agents/` | Yours. |
| `project` | `<cwd>/.aelix/agents/` | **Only when the directory is trusted** (`--approve`, or the trust dialog). |

Writing `~/.aelix/agent/agents/explorer.md` replaces the bundled `explorer`
outright — the bundled tier is a starting point, not something you have to work
around.

The project tier is gated because a profile is an **identity**: it can replace
the system prompt and swap the model. It is also why a model-chosen delegation
can never use a project-scoped profile — only a human naming it can.

Run `/agents list` to see what is discovered and from which tier.

## The frontmatter keys

All seventeen. `name` and `description` are required; everything else has a
default. An unknown key is a warning, not an error — your profile still loads.

| Key | Type | Default | What it does |
|---|---|---|---|
| `name` | string | — | The identity. **This field, not the filename**, is the name you delegate to; a mismatch warns. |
| `description` | string | — | What the model reads in the `agent` tool's schema to decide whether this profile fits the task. Write it for that reader. |
| `model` | string | inherit | Model id for the child. `inherit` and an absent key both mean "do not pass `--model`". |
| `provider` | string | inherit | Provider for the child. Set it whenever you set `model`. |
| `tools` | list \| `[]` \| absent | absent | **Three-valued.** Absent → the child inherits the ambient tool set. A list → exactly those tools. `[]` → **no tools at all**. |
| `builtin_tools` | bool | `true` | Whether the built-in tools are available at all. |
| `skills` | list of **paths** | `()` | Skill files, resolved **relative to the profile's own directory**, not your cwd. |
| `inherit_skills` | bool | `true` | Whether the parent's skills come along. |
| `extensions` | list of **paths** | `()` | Extension files, same relative-to-the-profile rule. **Forbidden at project scope.** |
| `inherit_extensions` | bool | `false` | Whether the parent's extensions come along. |
| `system_prompt` | `append` \| `replace` | `append` | `append` adds the body to the standard prompt; `replace` uses the body alone. |
| `context_files` | bool | `true` | Whether `AGENTS.md` and friends are loaded. |
| `thinking` | string | inherit | One of `off`, `minimal`, `low`, `medium`, `high`, `xhigh`. A bad value here **rejects the profile**. |
| `role` | `leaf` \| `orchestrator` | `leaf` | A `leaf` child cannot delegate further. See the note below. |
| `output_cap` | int | `51200` | Byte budget for the summary returned to the parent; past it the summary is truncated with a visible marker. |
| `timeout_ms` | int | none | Wall clock for the whole delegation. The `agent` tool bounds a caller-supplied value to 1 000 … 1 800 000 ms. |
| `approval_mode` | `inherit` \| `ask` \| `auto` \| `deny` | `inherit` | How the profile **declares** it needs write authority. |

### The ones that are easy to get wrong

**`tools: []` is not `tools: ""`.** An empty list means *no tools*. It is not the
same as the `--tools ''` command-line form, which inverts to *every* tool.

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
offer you the choice to widen it. You are still the one who answers.

## How the child authenticates

A delegated child is a separate process. It inherits your environment, so
provider keys set as environment variables (`ANTHROPIC_API_KEY`,
`OPENROUTER_API_KEY`, …) work without any further setup.

`--api-key` does **not** reach a child: it lives in the parent process's memory
only. If you plan to delegate, set the environment variable instead.

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
timeout_ms: 60000
---

You turn long input into a brief a busy reader can act on. No preamble.
Lead with the decision or the finding. Keep it under 200 words.
```

With `tools: []` and `builtin_tools: false` this agent can only read what it is
given in the task and reply — which is exactly what you want for something whose
job is text.

## Checking your work

- `/agents list` — what is discovered, and from which tier.
- `/agents show <name>` — the parsed fields, plus the exact command a delegation
  would run. Nothing spawns.
- `--agent <name>` — launch as that identity yourself, which is the fastest way
  to find out whether the prompt and tool set do what you intended.
