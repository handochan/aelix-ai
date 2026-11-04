# Turning tools on and off

Status: Accepted

Every tool the model can call — the built-ins (`read`, `bash`, `edit`, `write`,
`grep`, `find`, `ls`), an extension's, an MCP server's (`<server>__<tool>`) —
is on unless something turns it off. Three things can, and they differ in how
long they last and what can undo them:

| | Lasts | Can be turned back on |
| --- | --- | --- |
| `--exclude-tools` | this run | no — the tool is not loaded at all |
| `--no-tools`, `--no-builtin-tools`, `--tools` (or an agent profile's `tools:`) | this run | no — the panel shows it locked, with the flag |
| `defaultTools` in a settings file (the `/tool` panel writes it) | until you change it | yes — from `/tool`, or by editing the file |

When they disagree, the first one in this order wins: `--exclude-tools`,
`--no-tools`, `--no-builtin-tools`, `--tools`, then `defaultTools`.

## `--exclude-tools`

```bash
aelix -xt bash                     # everything but bash
aelix -xt 'github__*'              # no tool of the github MCP server
aelix -xt bash -xt 'github__*'     # both: the flag adds up when repeated
aelix --tools read,bash -xt bash   # read only — the exclusion wins
```

`--exclude-tools` (`-xt`) takes names and patterns, comma-separated. `*` is the
only wildcard and matches any run of characters; every other character is
itself (`a.b` is not `aXb`). Quote a pattern, or your shell may expand it
against file names (on Windows' Git Bash too).

An excluded tool is removed for the run: the model is not told about it, a
call to it fails as an unknown tool, and no extension, `/reload`, MCP
connection or agent profile can bring it back. `/tool` lists it as `excluded`.
A name or pattern that matches no tool loaded at launch is a warning at start
(a typo), not an error. Write the names after a space — `-xt bash`; the
`--exclude-tools=bash` form, and a value starting with `-` (`-xt --tools read`
takes `--tools` as the name), exclude nothing and say so at start. Delegated
agents are started with the same `--exclude-tools`.

## When you stop a tool

Press Esc or Ctrl+C while tools run and the model is told what happened, in the next
request and in the session when you reopen it. The same goes for RPC `abort`, a signal and
quitting.

| The tool | What the model reads |
| --- | --- |
| had finished | its result |
| was running | an error saying it was stopped and may have partly run; `bash` adds what it had printed |
| had not started | `No result provided` |

A process killed outright (`kill -9`) writes nothing. An extension's tool can say what it
got done: see [Writing an Extension](extension-authoring.md).

Stopping `bash` ends the command and the processes it started in its group. One it put in a
session of its own, with `setsid` or a daemon's double fork, keeps running. Past 4 MiB of output
the model reads the end of it and a note that its lines were not counted, and the whole of it
goes to the file that note names, as the raw bytes, finishing a moment later.

## The tool flags are locks

`--no-tools` turns every tool off, `--no-builtin-tools` the built-ins, and
`--tools a,b` everything but the named tools — for the whole run. Before #510
an extension could switch tools back on under them, and `/reload` did: under
`--no-tools` it turned the extension tools on again, and under `--tools` it
added every extension tool. Now nothing widens them: an extension's
`set_active_tools` can only narrow the set, and `/reload` keeps it.

An agent profile's `tools:` and `builtin_tools: false` (`--agent`, `/agents
use`) are locks in the same way, shown as `profile` until `/agents use --none`.

## The `/tool` panel

`/tool` (or `/tools`) lists every tool, grouped by where it came from — here
under `-xt bash`, with `grep` saved off:

```
── Tools ───────────────────────────────────── Save to [Global]  This project ──
 Search: █                                  (2/12) · 11 tools · 1 off · 1 locked
 BUILT-IN
   [✓] read          Read the contents of a file
 ▸ ( ) bash          Execute a bash command in the current working d…   excluded
   [✓] edit          Edit a single file using exact text replacement
   [✓] write         Write content to a file
   [ ] grep          Search file contents for a pattern                      off
   [✓] find          Search for files by glob pattern
   [✓] ls            List directory contents

────────────────────────────────────────────────────────────────────────────────
 bash
 Execute a bash command in the current working directory

 Locked off: --exclude-tools bash (this run only)
 ↑↓ move · Space toggle · ←→ save to · Enter save · Esc cancel · type to search
```

The keys are `/skill`'s ([skills-and-prompts](skills-and-prompts.md)): Space
turns a tool on or off, ← → picks the file Enter saves to (**Global**,
`~/.aelix/agent/settings.json`, or **This project**, `<cwd>/.aelix/settings.json`),
typing searches, Enter saves and applies, Esc discards.

Round brackets — `( )` — mark a row Space cannot change, and the right-hand
column says why: `excluded`, `--no-tools`, `--no-builtin-tools`, `--tools`,
`profile`. A server that is off has one row in its group, `server off` (its
`"enabled": false` in `mcp.json`) or `failed` (its connect at launch failed),
and an extension that failed to load has one too, as does one turned off in
`/extension` (`ext off`); none is counted as a tool
or a search match, and the line under the list starts with where to look
(`/mcp`, `/extension`). This panel never turns a server or an extension on.

Other words in that column: `off` (saved off), `* unsaved`, `off · pending` /
`on · pending` (the files say something this session is not using yet — a hand
edit, or another session's save; Enter applies it), `project` (this project's
file decides), and `off now` (on in the files, but an extension turned it off
for this run).

### When a change takes effect

From your **next message**: the model is sent the new tool list, and the
system prompt's list of tools changes with it. A change never lands inside a
turn; if one is running (an extension or a background agent started it), the
change waits for it to end and a line says when it applied — unless nothing
changed, in which case nothing waits. Only the tools you changed move — a tool
an extension turned off stays off. Extensions see the change as a
`resources_update` event with `resources["tools"]`.

A tool turned off in a settings file stays off even if an extension asks for
it; turn it back on here.

## `defaultTools`

The panel writes `defaultTools` in the settings file; you can edit it by hand.
The format is pi's, with one difference: every tool is on to begin with.

```json
{ "defaultTools": ["-bash", "-github__create_issue"] }
```

| Entry | Meaning |
| --- | --- |
| `-name` | this tool off |
| `+name` | this tool on |
| `name` (no sign) | the built-ins are exactly the ones named this way; extension and MCP tools stay on |

The entries apply in order. Names are exact: `*` and `!` are not used here, and
an entry with them is a warning at start and ignored (a list with nothing else
in it counts as absent). A list of only `+`/`-` entries in the project file adds
to the global file's list; one with a plain name replaces it. An absent
`defaultTools` turns nothing off. An empty one, `[]`, on its own turns the
built-ins off (as in pi); in the project file next to a global list it changes
nothing — it adds no entries — and the global list decides. A name no loaded
tool has is a warning at start.

When the panel saves, it changes the one tool you toggled and nothing else:
it removes that tool's `+`/`-` entries and adds one only if the list still
says otherwise, and it never turns a list of plain names into a list of `+`/`-`
entries (that would turn the other built-ins back on). Entries it ignores stay
where they are. A save to the global file also keeps every other tool where it
is in this project, when the project's list is only `+`/`-` entries or `[]`.
A project `[]` means no built-ins only while the global file has no list, so
there turning a tool off on Global would turn the built-ins on here; no change
of the global file can do both, and the panel saves nothing, says to save it
to This project instead and names the tools that would have turned on.

## Delegated agents

A delegated agent gets its parent's `--exclude-tools` patterns, and its
`--tools` is the parent's tools that its profile asks for. It reads
`defaultTools` itself — your global file always, the project's only if it runs
trusted — so every tool `defaultTools` keeps off in the parent is passed to it
as `--exclude-tools` too: a tool turned off only in this project's file stays
off in an untrusted child, extension and MCP tools included. This only ever
removes tools; one you turned back on in the parent is not passed. `/agents
show` shows the `--exclude-tools` a child would get. A child that would need
more than 256 entries, or a name that cannot be passed on (whitespace, a comma
or a control character in it, or a leading `-`) — one of your `--exclude-tools`
entries, or a tool `defaultTools` turns off, such as an MCP tool whose server
or tool name has a space — is not started, and the reason says so: turn the
server off in `mcp.json`, or cover the names with a `*` pattern in
`--exclude-tools`.

## Without the TUI

`-p`, `--mode json` and `--mode rpc` use the same flags and `defaultTools`. In
`--mode rpc`, `tools/set_active` refuses a tool the run removes or locks off,
with the reason, instead of leaving it out. A tool `defaultTools` turns off is
turned on, as the panel can — for this run, until the settings are read again
(`resources/reload`, a new session); nothing is written.
