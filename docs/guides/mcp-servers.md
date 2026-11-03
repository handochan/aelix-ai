# MCP servers

Aelix connects to [Model Context Protocol](https://modelcontextprotocol.io)
servers when it starts and gives their tools to the model, named
`<server>__<tool>`. This guide covers the `mcp.json` file, turning one server
off without deleting it, and how to see what each server is doing.

## Which file is read

A launch reads **one** `mcp.json` for its servers, the first of these that
exists:

1. the file `$AELIX_MCP_CONFIG` names;
2. `<project>/.aelix/mcp.json`;
3. `~/.aelix/agent/mcp.json`.

The files are not merged. A project `.aelix/mcp.json` **replaces** your global
file in that directory, trusted or not: none of the global servers start there.
(pi merges the two and lets a project entry override a global server's
`enabled`; aelix does not yet — an entry that sets only `enabled` in a project
file changes nothing and gets a warning that says so.)

A project file's servers start only in a [trusted](project-trust.md) project.
In an untrusted one the file is skipped whole — its servers are neither started
nor listed, and its server names count for nothing — and aelix prints a notice
when that skipped a server that would have started. Your global file's servers
do not start there either, since the project file is the one picked; only its
server names still count (see [Use the name](#servers-an-extension-declares)).

## Entries

The file is the `mcpServers` object other MCP clients use, but aelix reads only
six keys of an entry: `command`, `args`, `env`, `url`, `transport` and
`enabled`.

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "docs": { "url": "https://example.com/mcp" }
  }
}
```

- `command` and `args` start a **stdio** server: `command` is one executable,
  not a shell line. `env` adds variables to the small default environment the
  MCP SDK gives the child; your own environment, provider keys included, is
  not passed through.
- `url` connects to an **http** server. `transport` is optional — `command`
  selects `stdio`, otherwise `http`; set `"transport": "sse"` for an SSE
  endpoint.
- An entry with neither `command` (stdio) nor `url` (http/sse) is reported and
  skipped.

Keys other clients use that aelix does not read:

- `type` — aelix's key is `transport`. `{"type": "sse", "url": "…"}` loads as
  an `http` server; write `"transport": "sse"`.
- `disabled` — aelix's key is `enabled`. `"disabled": true` does **not** turn
  a server off; a warning says to write `"enabled": false`.
- `headers` — not read, so a server that needs an auth header cannot be
  configured in `mcp.json` yet.

A bad entry is reported as a `Warning: MCP config:` line when aelix starts and
skipped; the other servers still start.

## Turning a server off

`"enabled": false` keeps an entry — command, arguments, environment — without
starting it:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "enabled": false
    }
  }
}
```

A disabled server is not started and its tools are not given to the model, but
`/mcp`, `/extension` and `aelix status` still list it, with the file to edit.
To turn it back on, set `"enabled": true` or delete the key; `enabled` defaults
to `true`.

`enabled` must be JSON `true` or `false`. Anything else — `"false"` in quotes,
`0`, `null` — is reported as a warning and that server is **not started**, the
same as pi, so a typo can never start a server you meant to keep off.

## When a change takes effect

Servers connect once, when aelix starts. Edit `mcp.json`, then start aelix
again: `/reload` does not reconnect MCP servers.

A delegated agent is a new aelix process and reads its own `mcp.json`, which
is usually not the one its parent read: it does not get `$AELIX_MCP_CONFIG`
(the parent removes the variable), and wherever the project has
trust-requiring resources it runs without trusting the project, so a project
file's servers do not start for it. What the parent's file turned off stays
off anyway: the parent passes the agent the server names its file turned off
or declared, and the agent starts no server its parent kept off, and no
extension's server of a name its parent's file declares. Only names are
passed, and they can only keep a server off — never start one. They travel in
`$AELIX_MCP_PARENT_NAMES`, which the agent removes from its environment once
read, and the list is capped at 16 KiB (several hundred names): past that the
agent still starts, without the names that did not fit, and aelix warns at
launch how many were left out.

## Seeing what each server is doing

`/mcp` shows each server with its transport and endpoint (a url as its scheme,
host and port only — `https://example.test/…` — since a token can ride its
query or its path; a stdio command as written, cut with `…` when it is longer
than the column), its state and its tool count;
`/extension`'s **Installed** tab lists the same servers with the same words;
`aelix status` lists the servers of the file a launch in this directory would
read, without starting any of them (and without printing endpoints). It decides
trust as `aelix -p` does — a saved decision, then your `defaultProjectTrust` —
and says when that is not the whole answer: a launch also asks extensions'
`project_trust` handlers, and an interactive one asks you.

| State | Means |
|---|---|
| `connected` | connected at launch; its tools are registered |
| `failed` | did not connect at launch; it has no tools this session (the reason was printed when aelix started) |
| `disconnected` | not connected now, though its launch connect did not fail — the connection closed later |
| `disabled` | `"enabled": false`; not started, no tools, still listed |
| `enabled` | (`aelix status` only) the next launch starts it |

Disabled servers are listed last.

## Servers an extension declares

An extension can declare servers in its manifest (`[[contributes.mcp_servers]]`,
see [extension-authoring](extension-authoring.md)). They start next to the
`mcp.json` servers, behind the capability flags that guide describes, and are
listed by `/mcp` and `/extension` the same way — `/extension` also names the
extension that declared each. `aelix status` does not list them yet.

Turning the extension off in `/extension` (#25) turns its servers off too, at
the next launch: an extension that is off at launch has nothing started. After
`/reload` turns it off, its servers' tools are hidden from the model and the
connection stays until restart (`connected · hidden` in `/extension`); turned
back on, their tools come back (or, if they were never started, they connect at
the next launch). An extension that is on but fails to load keeps its servers
and their tools: `/extension` says `tools in use`. The servers themselves are
turned on and off only in `mcp.json` — `/extension` lists them, it does not
toggle them.

They have no `enabled` key — a manifest that sets one does not validate. What
you can do:

- **Use the name.** A server name in your `mcp.json` is yours: an extension's
  server of the same name is not started, whether your entry is enabled,
  disabled, or skipped with a warning. So an entry with that name and
  `"enabled": false` keeps the extension's server off as well. Aelix prints a
  notice naming the extension and the file whenever an extension's server is not
  started for this reason. Whose names count:
  - the names of the file the launch reads;
  - in an untrusted project, your global file's — not the project file's;
  - in a trusted project with its own `.aelix/mcp.json`, that file's only: it
    replaces the global one there, so put the entry in the project file too;
  - for a delegated agent, also every name its parent's file declares.
- **Turning a whole extension off** is the `extensions` settings list, which is
  not applied yet: aelix warns about a non-empty one rather than half-applying
  it.

## Not covered by `mcp.json`

Servers an RPC client configures with `mcp/configure` (`--mode rpc`) belong to
that client's connection: they are not read from or written to `mcp.json`, and
`mcp/status` lists only them.
