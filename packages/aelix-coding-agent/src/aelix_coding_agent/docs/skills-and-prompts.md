# Turning skills and prompt templates on and off

Status: Accepted

Aelix finds skills and prompt templates in a few directories and, by default,
uses all of them. This guide covers choosing which ones it uses: the `/skill`
panel, the `skills` and `prompts` lists in `settings.json` that the panel writes,
and when a change takes effect. Writing a skill is covered by the packaged
`writing-skills` skill (`aelix docs skills` prints where it is).

## Where skills come from, and which one wins

| Tier | Directory | Notes |
| --- | --- | --- |
| this run | `--skill <path>`, an agent profile's `skills:` | always on for the run |
| project | `<cwd>/.aelix/skills/` | only when the folder is trusted ([project-trust](project-trust.md)) |
| global | `~/.aelix/agent/skills/` | every project |
| bundled | inside Aelix (`writing-skills`, `extending-aelix`) | |

A path in a settings file's `skills` list adds a directory to that file's tier
(see below) — this is how you add `~/.claude/skills`.

**Two skills with the same name:** the first tier in the table wins
(`--skill` > project > global > bundled). The others are not loaded; `/skill`
lists them as locked "shadowed" rows that name the winner. A skill of your own
called `writing-skills` therefore replaces the bundled one.

## The `/skill` panel

`/skill` (or `/skills`) opens a checklist of every skill found, on or off,
grouped by where it came from:

```
── Skills ──────────────────────────────────── Save to [Global]  This project ──
 Search: █                                          7 skills · 2 off · 1 locked
 PROJECT  .aelix/skills/
   [✓] repo-conventions  This repository's commit, ADR and test …       project
 GLOBAL  ~/.aelix/agent/skills/
 ▸ [ ] pdf-tools         Extract text and tables from PDF files;…     * unsaved
   [ ] deploy-check      Pre-deploy checklist for the staging cl… off · pending
 THIS RUN  --skill
   (✓) ci-triage         Triage a failing CI run: fetch logs, fi…        locked
────────────────────────────────────────────────────────────────────────────────
 pdf-tools · ~/.aelix/agent/skills/pdf-tools/SKILL.md
 Extract text and tables from PDF files; fill PDF forms.
 Saved on · now on · off after Enter (Global)
 ↑↓ move · Space toggle · ←→ save to · Enter save · Esc cancel · type to search
```

| Key | Does |
| --- | --- |
| ↑ ↓, PgUp PgDn, Home End | move |
| Space | turn the skill on or off (a locked row says why it cannot change) |
| ← → | choose where Enter saves: **Global** (`~/.aelix/agent/settings.json`) or **This project** (`<cwd>/.aelix/settings.json`) |
| typing, Backspace | search names and descriptions |
| Enter | save, apply, close; what changed is left in the transcript |
| Esc | discard the unsaved changes and close |

The right-hand column: `* unsaved` (changed here, not saved yet), `off`,
`project` (this project's file decides it), `off · pending` / `on · pending`
(the files say something this session is not using yet — another session or a
hand edit changed them; Enter applies it), `locked` and `shadowed`. Round
brackets — `(✓)` — mark a row that cannot be toggled: a `--skill` skill, a
shadowed duplicate, or the one row standing for an untrusted project's skills
(counted from the directory listing; none of them is read).

If a save fails — a settings file that does not parse, a read-only file — the
panel stays open with your changes and a red line naming the file, and nothing
is applied.

**Saving to This project the first time creates `.aelix/settings.json`.** A
folder with that file falls under project trust: unless an extension answers,
a decision is already saved for it (or a parent), or `defaultProjectTrust`
decides, the next launch asks, and `-p` runs there need `--approve`
([project-trust](project-trust.md)). The panel says so and saves only on a
second Enter. In an untrusted folder This project cannot be chosen, and nothing
in the project's settings file is read.

### When a change takes effect

From your **next message**: the model is sent the new `<available_skills>`
list. A change never lands in the middle of a turn — if one is running (an
extension or a background agent started it), the change waits for it to end and
a line says when it applied. A skill the model already read stays in the
conversation; turning it off does not remove it. A change rewrites the skill
list in the system prompt, so the next request usually cannot reuse the
provider's cached prompt (with the `read` tool off there is no list, and the
prompt may not change at all).

`/skill:<name>` still inserts an enabled skill's text. For a skill that is off
it answers `<name> is off — /skill to turn it on` and sends nothing.

If the `read` tool is off, the model is shown no skill list at all (it could not
open one); the panel says so, and your choices still save.

## The settings lists

The panel writes ordinary settings entries, which you can also edit by hand —
the format is pi's, so a pi settings file works:

```json
{
  "skills": ["~/.claude/skills", "!old-*", "-pdf-tools"],
  "prompts": ["!scratch-*"]
}
```

| Entry | Meaning |
| --- | --- |
| a path | add a directory, relative to the file's own directory (`~/.aelix/agent/` or `<cwd>/.aelix/`); `~` works |
| `!glob` | off: matched against the name, the path relative to the file's directory, the file name and the absolute path (for a skill, also its directory) |
| `-name` / `+name` | exactly this one off / on — a skill or template name, or an exact path |

Globs are minimatch's, as in pi: `*`, `?`, `**`, `[a-z]`, `{a,b}`, ranges like
`{1..3}`, and `@(a|b)`-style groups all work. A trailing `/**` needs something
after it: `!pdf-tools/**` does not match a skill folder named `pdf-tools`. A
glob whose braces expand to more than 256 alternatives, that is longer than 512
characters or whose groups nest more than 16 deep is not used; the launch warns
about it ("is too complex to match").

Within one list `!` applies first, then `+`, then `-`, whatever the order:
`["!pdf-*", "+pdf-tools"]` leaves `pdf-tools` on. `-` and `+` take no wildcards,
so `-pdf*` matches nothing.

**Both files:** a skill's value is the project file's if it says anything about
it, else the global file's, else on. A name or glob in the global file applies to
project skills too. `--skill` skills are on whatever the files say.

The panel edits the list on disk at the moment you press Enter, holding a lock
other sessions take too: it removes the entries that name exactly that skill,
and adds `-name` or `+name` only if the rest of the list does not already give
the value you chose. Your globs, your path entries and every other key keep
their values, and so does anything another session saved in the meantime —
though the file is rewritten in a standard layout (2-space indent; numbers such
as `1.50` written as `1.5`). If another session holds that lock for 30 seconds,
the save fails and says so instead of waiting forever.
One Enter writes one file — the one "Save to" points at. Toggling a skill twice
before Enter is no change at all, and an Enter with no changes writes nothing.

A path entry reaches only skills from its own file's side: a path in the project
file can name a project skill, not one of yours, and the other way round. Names
and name globs (`-pdf-tools`, `!pdf-*`) reach every skill — and so does a glob
that can match a name: `!**/pdf-tools` in the project file turns your own
`pdf-tools` off there, because `**` can match nothing.

If a settings file cannot be read when you press Enter (it was broken by hand,
say), the panel applies nothing and keeps what the session was using, rather
than treating the unreadable file as empty.

Mistakes are reported once, on stderr, when Aelix starts: an entry that matches
nothing, a path that is not a directory, a value that is not a list of strings.
The `themes`, `packages` and `extensions` keys are not read yet; a non-empty one
is reported rather than silently ignored (`-e <path>` adds an extension).

## Prompt templates

Templates (`~/.aelix/agent/prompt-templates/`, `<cwd>/.aelix/prompt-templates/`,
`--prompt-template <path>`) use the same `prompts` list and the same rules. There
is no panel for them: edit the list, then `/reload` or restart.
`--prompt-template` templates are always on; a `.md` file given there is that
one template, not every template in its folder.

## `/reload`

`/reload` re-reads both settings files and rescans the skill and template
directories, so an edited `SKILL.md`, a new skill directory or a hand-edited
list takes effect without a restart.

## Without the TUI

`-p`, `--mode json`, RPC and delegated agents apply the same lists when they
start. A delegated agent shares your global settings file but works out its own
skills when it starts, under its own trust, folder and profile: it reads the
project file only if it runs trusted (it usually runs with `--no-approve` where
the project has a settings file), its profile's `skills:` are always on, and it
never sees unsaved panel edits. A long-running RPC child keeps what it worked
out until it restarts. `--no-skills` scans no skill directory, so only
`--skill` skills load.

RPC `settings/set` with `skill_paths` or `prompt_template_paths` replaces the
GLOBAL file's list the same careful way the panel edits it, and fails — rather
than reporting success — when that file cannot be read or written.
`settings/get` reads the same list.
