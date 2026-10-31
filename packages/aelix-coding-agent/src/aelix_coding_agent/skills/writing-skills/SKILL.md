---
name: writing-skills
description: How to write, place and invoke an Aelix skill (SKILL.md format, frontmatter fields, the directories skills load from and which one wins, the project-trust gate, and why the description field is the part that matters). Read this when asked to create, edit, debug or explain a skill.
---

# Writing an Aelix skill

A skill is one markdown file with YAML frontmatter. Aelix puts its **name,
description and absolute path** into the system prompt at startup, and nothing
else. You read the file with the `read` tool when a task matches. That is the
whole mechanism, and it is why the description does almost all the work.

## The file

```markdown
---
name: my-skill
description: One or two sentences saying when to use this. This is the only part always in context.
---

The instructions. Plain markdown. No length limit that matters in practice.
```

Rules the loader actually enforces:

- `name` — **must equal the parent directory name**. Lowercase letters, digits
  and hyphens only; 64 characters max; no leading, trailing or doubled hyphens.
  A mismatch or a bad character means the skill is not loaded.
- `description` — required, non-empty, 1024 characters max. Longer is truncated
  when the catalog is assembled, so put the trigger condition first.
- Everything after the frontmatter is the body, and it is loaded on demand.

## Where to put it

Aelix scans these tiers. When two skills share a name, the first tier in this
list wins and the others are not loaded (`/skill` shows them as "shadowed").
Within a directory, the first `SKILL.md` found wins and that directory is not
descended further.

1. **`--skill <path>`** (and an agent profile's `skills:`) — added explicitly
   and never gated: an explicit flag is your choice, not something a clone
   decided for you. Always on for that run.
2. **Project** — `<project>/.aelix/skills/<name>/SKILL.md`. Shared with whoever
   clones the repo, and **gated**: because a skill's description goes into the
   system prompt, a project that carries skills is a project that can write
   instructions into the agent. Aelix asks once before trusting the directory
   and skips it if you decline.
3. **User** — `~/.aelix/agent/skills/<name>/SKILL.md`. Available in every
   project. This is where a skill you want to keep should go.
4. **Packaged** — ships inside Aelix. This file is one of them. Not writable;
   a user or project skill of the same name replaces it.

`--no-skills` turns tiers 2-4 off. A path in the `skills` list of a
`settings.json` adds another directory (for example `~/.claude/skills`), and
`!glob`, `-name` and `+name` entries in that list turn skills off and on — which
is what the `/skill` panel writes (`aelix docs skills-and-prompts`).

`.gitignore`, `.ignore` and `.fdignore` are honoured during the scan, and
hidden entries and `node_modules` are skipped.

## Making the model actually use it

Progressive disclosure has a known failure mode: the model sees the description
and decides not to read the file. Two fixes, in order of reliability:

- **Write a description that names the trigger, not the topic.** "Read this
  when asked to create, edit or debug a skill" beats "About skills". The
  description is a routing instruction, so write it as one.
- **`/skill:<name>` forces it.** That command injects the full body immediately,
  as a turn, no decision involved. Use it when the skill must apply. It is
  controlled by the `enableSkillCommands` setting.

Add `disable-model-invocation: true` to the frontmatter to keep a skill out of
the system prompt entirely while leaving `/skill:<name>` working — for skills
that are too situational to advertise on every turn.

## Checking your work

- `/skill` (or `/skills`) lists every skill found, on or off, with its source
  path, and turns them on and off.
- The startup panel shows a `[Skills]` count of the skills in use.
- `/reload` rescans the directories, so an edited `SKILL.md` needs no restart.
- If a skill is missing from `/skill`, the cause is almost always the
  `name`-vs-directory rule, an empty `description`, or an untrusted project
  directory. If it is listed but off, a `skills` settings entry turned it off.
  Aelix prints a `Warning: skill load:` line to stderr for parse and read
  failures, and a `Warning: settings:` line for an entry that matches nothing.

## Related

Prompt templates are the sibling mechanism: `<project>/.aelix/prompt-templates/<name>.md`
or `~/.aelix/agent/prompt-templates/<name>.md` becomes a `/<name>` command whose
body is sent as your turn, with `$1`, `$2`, `$ARGUMENTS` and `${@:2}` substituted
from what you typed. Use a template for a prompt you re-type; use a skill for
knowledge the model should apply on its own.
