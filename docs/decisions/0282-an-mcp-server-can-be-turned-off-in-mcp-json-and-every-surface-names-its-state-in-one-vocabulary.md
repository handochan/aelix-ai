# 0282. An MCP server can be turned off in `mcp.json`, and every surface names its state in one vocabulary

Status: Accepted (2026-10-31, #512). Amended 2026-11-01 (review rounds 1 and 2:
D9, and the corrections listed under it).
Date: 2026-10-31
Issue: #512 (phase 1 of its checklist). Deferred: the global/project merge and a
`/mcp` toggle (D8).
Amends: ADR-0101 (the `mcp.json` entry shape gains `enabled`; an entry with no
`command`/`url` is reported at load), ADR-0155 (`/mcp`'s states and rows),
ADR-0181 (the `mcp.json`-wins name rule is explicit and covers disabled and
skipped entries). Leaves ADR-0279 D16 as it is: the extension filter is #25's
and is not applied here.

## Context

`cli/config.py` `load_mcp_server_contribs` reads ONE file — `$AELIX_MCP_CONFIG`,
else `<cwd>/.aelix/mcp.json` (whose servers start only in a trusted project;
an untrusted one is still the file picked, and then dropped), else
`~/.aelix/agent/mcp.json` — and builds an `McpServerContrib` from `command`,
`args`, `url`, `env` and `transport`. Every other key was ignored (`headers`
and `type` included). So the only way to stop a server was to delete its entry,
losing its command, arguments and environment.

Measured on a checkout of `0e97bd28`, with the fixtures of
`tests/cli/test_mcp_enabled_512.py` and of the live check: with a manifest
server `shared` and an `mcp.json` entry `shared` carrying `"enabled": false`, the
`mcp.json` command still reached `McpClientManager`, and the manifest's
`Notice: plugin 'shadow-pack' starts MCP server 'shared'` printed although that
server is never started — the manager's name-keyed dict is last-wins and the
`mcp.json` entries are appended after the manifests'. In the real TUI the same
build started all three of a live fixture's servers — one with
`"enabled": false` and one with `"enabled": "false"` included — each writing its
start marker.

pi (`earendil-works/pi`, `packages/coding-agent/`): `enabled?: boolean` — "Set to
false to keep the entry without connecting" (`src/core/mcp-servers.ts:74-75`);
`validateMcpServerConfig` rejects a non-boolean (`:275`, "enabled must be a
boolean"), and an invalid entry is "reported and skipped" (`docs/mcp.md`).
`loadMcpConfig` keeps disabled entries "so they can be enabled again"
(`src/extensions/mcp/config.ts`). pi merges the global and project files and
lets a project entry with no `command`/`url`/`type` override only `enabled`,
`exposure` and `toolExposure` of a global server (`config.ts:1-12`,
`OVERRIDE_KEYS`). A server in `mcp.json` "takes precedence over a registered
server of the same name" (`src/extensions/mcp/index.ts`, `registeredServers`).
`/mcp` is interactive and lists disabled servers last (`attentionRank`).

Three surfaces list servers in aelix — `/mcp` (`tui/mcp_viewer.py`), the
`/extension` Installed tab (`tui/extension_manager.py`) and, from this change,
`aelix status` — and #25's Installed tab will be a fourth. The two TUI surfaces
each turned `connection.connected` into their own `connected` / `disconnected`;
`aelix status` listed no server at all.

## Decision

### D1. `enabled`

- Per server entry, default `true`. `false` keeps the entry whole (an
  `McpServerContrib`, so a later toggle has everything it would connect): it is
  not connected, its tools are not registered, and it is listed.
- A non-boolean `enabled` (`"false"`, `0`, `null`, …) is a warning — `server
  'x': "enabled" must be true or false, not "false"; not started` — and the
  entry is **skipped**, as pi skips it. This is also the safe side: treating
  `"false"` as truthy would start the server its author meant to keep off.
  Startup continues with the other servers.
- `load_mcp_config(cwd) -> McpConfig` is the loader (`contribs` to connect,
  `disabled`, `warnings`, `declared`, `source`, `path`).
  `load_mcp_server_contribs` keeps its 3-tuple and returns the connect list
  only, so no caller of it can ever start a disabled server.

### D2. One file stays; an entry needs `command` or `url`

The one-file rule is kept: no merge in this change (D8). Two consequences are
now said instead of left to surprise:

- A stdio entry with no `command` and an http/sse entry with no `url` are
  reported at load and skipped (pi: `needs either "command" (stdio) or "url"`).
  Before, such an entry reached the manager and failed at connect with a
  transport error; a disabled one would have been listed as a server.
- In the project file, an entry with none of `command`, `url`, `transport` is
  pi's override shape. Here it overrides nothing — the project file replaces the
  global one in that directory — so its warning adds: `aelix reads only this
  file here, not ~/.aelix/agent/mcp.json as well, so an entry in it cannot
  change or turn off a global server`.

### D3. One state vocabulary: `core/mcp_servers.py`

The words, what each means, the rule that picks one and the sentence that says
how to turn a server back on live in `aelix_coding_agent/core/mcp_servers.py`;
the surfaces only render its rows. It imports neither `rich` nor the MCP SDK
(the SDK imports `rich`, the optional `[tui]` extra), so `aelix status` can read
it; a subprocess test pins that.

| State | Means | Where |
|---|---|---|
| `connected` | connected at launch; its tools are registered | session |
| `failed` | its launch connect raised; no tools this session | session |
| `disconnected` | not connected now, and its launch connect did not fail | session |
| `disabled` | `"enabled": false`; not started, no tools, still listed | session and config |
| `enabled` | the next launch starts it (`aelix status` starts no server) | config |

- `McpServerState` (the `Literal`), `STATE_MEANINGS` (the middle column).
- `McpServerStatus(name, transport, state, endpoint="", config=None)` — one row;
  `config` is the label of the file that turns a disabled server off.
- `runtime_statuses(manager)` — a running session's rows: the manager's
  connections through `connection_status` (connected wins; otherwise a name in
  `manager.failed` is `failed`, any other `disconnected`), then its `disabled`
  entries, last, as pi orders them. Duck-typed and getattr-guarded.
- `config_statuses(contribs, disabled)` — the config-only rows (`enabled`,
  `disabled`), for `aelix status`.
- `config_label(path, source, cwd=)` — the project file relative to the cwd
  (`.aelix/mcp.json`), a file under home with `~`, an `$AELIX_MCP_CONFIG` path as
  given.
- `disabled_note(row)` — `"enabled": false in <file>`: the key and the file.
- `enable_hint(rows)` — per distinct file, two rows under 78 columns for the
  usual labels: `Turned off by "enabled": false in <file>.` / `Set it to true,
  or delete the key, and the next aelix start connects it.`

`/mcp` renders `runtime_statuses(mcp_manager)` (the title adds `· N disabled`,
`enable_hint` follows the table); `aelix status` renders `config_statuses` and
`enable_hint`. `/extension` passes the same rows to `build_installed_lines` and
puts `disabled_note` on the disabled server's own row instead of the two hint
rows: its tabbed modal does not scroll and bottom-truncates (ADR-0199), and
measured at 80x24 with this fixture the base build already lost its last rows
there; the tab's footer already says servers load at startup. **#25 reads
`runtime_statuses(mcp_manager)`** for its Installed tab and adds no word of its
own; a new state is added to the `Literal` and `STATE_MEANINGS` together (a test
holds them equal).

### D4. The manager is the session's inventory

`McpClientManager(contribs, *, disabled=())` holds the disabled entries as data
— no `McpServerConnection` is created for them, and a contribution whose name is
disabled is not given one either — and records in `failed` the names whose
`connect_all` attempt raised. A manager is built when there is anything to list,
so a file of only disabled servers is listed rather than "No MCP servers
configured.".

### D5. The name rule

A name the `mcp.json` declares is the `mcp.json`'s — whether its entry is
enabled, disabled, or skipped as invalid. A manifest server
(`contributes.mcp_servers`) of that name is dropped before the manager, and a
notice says so: `plugin 'p' declares MCP server 'x', which is not started:
<file> defines a server of the same name` (`… and turns it off` for a disabled
entry, `(skipped: see its warning above)` for an invalid one). This replaces the
false "starts MCP server" notice. pi gives the name only to valid entries;
aelix also gives it to skipped ones, so a mistyped `"enabled": "false"` cannot
hand the name to an extension's server and start it. An untrusted project file
has no say: none of its names count (amended 2026-11-01: the global file's do,
there — D9).

This is the existing name rule made explicit, not the extension filter: a
manifest server has no `enabled` key (`McpServerContrib` is `extra="forbid"`, so
a manifest that sets one does not validate), and turning a whole extension off
stays ADR-0279 D16's, owned by #25 and not applied.

### D6. Every entry point

| Entry point | How it reaches MCP | Effect of `enabled: false` |
|---|---|---|
| interactive, `-p`, `--mode json`, `--mode rpc` | the one block in `cli/entry.py` `_async_main` before the first harness build | not connected, no tools; listed by `/mcp` and `/extension` |
| `/reload`, `/new`, `/resume`, `/fork` | reuse `mcp_tools`; nothing reconnects | unchanged until the next start |
| delegated agent | a fresh `aelix -p` / `--mode rpc` process; `AELIX_MCP_CONFIG` is removed from its environment and it usually runs `--no-approve` (`aelix_agents/print_channel.py`, `trust.py`) | reads its OWN file, usually not the parent's; off for it only through D9's record (amended 2026-11-01 — before, a parent's project or env-file entry came back on in the child) |
| RPC `mcp/configure`, `mcp/status`, `resources.mcp` | the client's own ACP lease (ADR-0275), not `mcp.json` | none: those list only client servers, which have no `enabled` |
| the status tool's snapshot | tools only | a disabled server's tools are absent because they were never registered |
| `aelix status` | `load_mcp_config` + the launch's trust rule; starts nothing | listed as `disabled` with the hint |

### D7. `aelix status`

A new block, printed under `--no-extensions` too (reading the file executes
nothing): `MCP servers (<file>):`, one row per server (name, transport, state),
the hint, then the file's warnings without their leading path (the header names
the file; the path alone ate the 128-character bound) bounded at 600
characters. Endpoints are not printed — a url can carry a token and this output
is pasted into bug reports. An untrusted project file is reported as skipped and
not listed. JSON: `mcp: {config, config_label, source, skipped_untrusted,
servers: [{name, transport, state}], warnings, trust_note}` (`trust_note`: D9's
amendment). Manifest servers are not listed
here: which of them a launch starts is decided by the scan, the capability gate
and D5, and that inventory is #25's.

### D8. Deferred

- **The global/project merge.** pi merges and lets a project entry override only
  `enabled` / `exposure` of a global server. That would make "off in one
  project" possible without copying the global file (and its credentials) into
  the project, but it moves the trust boundary — what a project file may change
  about a server it did not define — and needs its own ADR. A follow-up issue.
- **A `/mcp` toggle** that writes `enabled` back to the file, in #25's shape.
- A failed server's reason in `/mcp` (pi shows its first line); it is printed at
  startup today.

## Consequences

- A server can be kept in `mcp.json` and not started; a typo in `enabled` never
  starts it.
- `/mcp`, `/extension` and `aelix status` say `disabled` (and `failed` where the
  launch connect raised) in the same words, and say which file and key turn a
  server back on.
- An extension's server that loses its name to `mcp.json` is said, instead of a
  notice claiming it starts.
- A pi user's project override (`{"x": {"enabled": false}}`) is told it does
  nothing here, instead of silently hiding every global server.

## Limits

- A change to `enabled` takes effect at the next start; `/reload` does not
  reconnect MCP servers.
- `disconnected` is reachable for http/sse after the retries give up; a crashed
  stdio server keeps `connected` until its next call fails (pre-existing:
  `McpServerConnection.connected` is "a session exists").
- `/extension` clips rows at the terminal edge, so the row note is cut whenever
  the row is wider than the terminal: at 80 columns, with the default
  `~/.aelix/agent/mcp.json` label, a name of 14 or more cells is enough (the rest
  of the row is 67 columns), and so is a long `$AELIX_MCP_CONFIG` path. The note
  is not moved to a row of its own: measured at 80x24 (review round 1), the
  Installed tab was already bottom-truncated with the fixture's seven servers, so
  a second row per disabled server would push more rows out. `/mcp` and
  `aelix status` carry the full hint.
- An untrusted project `.aelix/mcp.json` holding only disabled entries still
  makes the directory ask for trust (the trust predicate counts the file).
- Warnings from an untrusted project file are still printed at startup
  (pre-existing); its servers and names are not used.

## Amendment 2026-11-01 — review round 1

The Codex cross-review and the independent verify of the first commit found
that the name rule (D5) and `"enabled": false` did not hold where the user's
intent lives, and five display or wording defects. Each is measured in
`.omc/specs/512-impl-codex-review.md` and `.omc/specs/512-verify-report.md`.
Round 2 (`.omc/specs/512-verify-r1-report.md`, R1–R7) bounded and narrowed D9's
record and corrected three display or wording items; each is marked `round 2`
below. A malformed record that nests too deep for `json.loads`
(`RecursionError`) is read as empty, like any other malformed one (R1).

### D9. A delegated child inherits the parent's names, to suppress only

- **The hole (512-01, V3).** A child is launched with `--no-approve` wherever the
  project has trust-requiring resources (ADR-0197 §(g)) and without
  `$AELIX_MCP_CONFIG`, so the file it reads is usually not its parent's. A trusted
  project file's or an env file's `"enabled": false` came back on in the child;
  a global extension's server of that name started there; a global file's server
  that the env file turned off started too. Measured with real children on both
  channels (`.omc/specs/512-fix-r1-probes/children-base/`).
- **The record.** After applying its own file, `cli/entry.py` records what the
  launch accepted, `core.mcp_servers.McpNameRecord`: `off` — the names its
  `mcp.json` starts nothing for (disabled and skipped entries) — and `claimed` —
  every name that file declares. `aelix_agents.print_channel.build_child_env`,
  which `build_rpc_child_env` calls, writes it as `$AELIX_MCP_PARENT_NAMES`
  (JSON, ASCII) for both channels. A process that recorded nothing passes an
  inherited record through untouched.
- **The bound (round 2, verify R5).** The value is at most 16 KiB
  (`core/mcp_servers._RECORD_MAX_BYTES`, private: an encoder limit, not a delegation cap — ADR-0197), several hundred names; `claimed` is written
  without the `off` names, which the child adds back. An environment string has
  a ceiling (Linux 128 KiB for one string, Windows 32,767 characters for a
  variable, macOS `ARG_MAX` for the whole environment), and with no bound about
  1.24 MB of names failed every delegation's spawn with `Argument list too
  long`. Past the bound the child still spawns: the names that do not fit are
  left out, `off` names kept first, and the launch prints `Warning: MCP config:
  N server name(s) are not passed to delegated agents (the list is capped at 16
  KiB), so an agent may start a server of one of those names.`
- **The child.** Its MCP block reads the record before the manifest gate and
  removes the variable from `os.environ` (round 2, verify R4: left there, the
  child's bash tool and `api.exec` handed it to everything they ran). A server
  of its own file whose name is in `off` is not started (`$AELIX_MCP_PARENT_NAMES
  (the names a delegating aelix passes on) keeps it off`); an extension's server
  whose name is in `claimed` is dropped like any D5 loser (`… reserves that
  name`). It then records the union, which its own children get through
  `build_child_env`.
- **Suppression only.** The record holds names and nothing else — no command,
  url, environment or credential — and the child uses it only to drop servers;
  nothing in it can start one. A name the parent claimed but did not turn off
  does not stop the child's own entry of that name. `AELIX_` is a prefix a cwd
  `.env` cannot set (`runtime_bootstrap._DOTENV_NEVER`) unless your
  `AELIX_DOTENV_ALLOW` lists it, so a cloned repo cannot use the variable to
  switch the user's servers off. Because the user's own environment or a hatched
  `.env` can set it, the notices name the variable rather than say a parent did
  (round 2, verify R3; the smaller change than putting it on `_DOTENV_LOCKED`,
  whose criterion is redirect-not-execute and which it does not meet — it only
  suppresses).
- **Why this does not conflict with ADR-0279 D14** ("delegated children resolve
  their own selection"). D14 is about what a child turns ON — its skills and
  prompts, resolved under its own trust, cwd and profile, so a parent's trust
  or unsaved edits never widen what it loads. The child here still resolves its
  own `mcp.json` under its own trust, and that is still the upper bound of what
  it starts; the record can only lower it. The parent's config said "off", and a
  delegated agent is the parent's work: it must not get a server its parent's
  config turned off — the same direction as `narrow_tools`, which never gives a
  child a tool its parent lacks.

### D5, amended: the global file's names count in an untrusted directory

An untrusted project `.aelix/mcp.json` is the file `load_mcp_config` picks, so
it used to make NO file's names count: with `{"mcpServers": {}}` in a cloned
repo, a global extension's server that the user's global file keeps off by name
started (V1). Now the untrusted branch reads the global file
(`load_global_mcp_config`) for its NAMES only: nothing from either file starts,
the global file's warnings are not printed, and an extension's server whose name
it declares is dropped with a notice naming it (`(an entry it skips as
invalid)` for a skipped one). A TRUSTED project file with its own entries still
replaces the global file, names included — the one-file rule (D2) — so there the
guide says to put the entry in the project file too.

### Corrections

- **Warnings name the field, never its value (512-02).** `str()` of a pydantic
  `ValidationError` quotes `input_value`, and a url given as a list reached the
  startup warning and `aelix status` with its token. `cli/config._invalid_reason`
  renders each error as `<location>: <message>` from `errors()`. Every surface
  that prints loader warnings — the startup `Warning: MCP config:` line and the
  `aelix status` block — reads the loader's strings, so the one change covers
  both.
- **`/mcp` cells are text (512-03, V2, V5, V7).** Each name, endpoint and hint
  row is a `rich.Text` of `safe_for_terminal(...)`: a `str` cell is markup
  (`close[/]` raised MarkupError and blanked the panel), and escape codes and
  line feeds are the config author's. The rows keep the real values; only the
  rendering is cleaned. A url is shown as its scheme, host and port with the
  rest elided — `https://example.test/…` — because a token rides the query and
  some services carry the key in the path (`/mcp/<key>/sse`, round 2, verify R7);
  a stdio command is shown as it is (`core.mcp_servers.display_endpoint`;
  `aelix status` prints none). The name column has `max_width=28` with an
  ellipsis. The endpoint column wraps after the transport word, and a path or
  url longer than the column is cut with `…` (round 2, verify R6).
- **`aelix status` trust (512-04).** It passes the global `defaultProjectTrust`
  the launch passes; it cannot reproduce the `project_trust` extension vote or
  an interactive prompt, so for a project file the MCP block says so
  (`trust_note` in the JSON). It no longer claims to be launch-equivalent there.
- **`aelix status` rows by display cells (512-05, V6).** The transport and state
  columns are reserved first and the name gets the rest of an 80-cell row,
  counted with `unicodedata` (the command runs without the `[tui]` extra).
- **`"disabled"` (V8).** Cline's key is not honoured — `enabled` stays the one
  key — but an entry with `"disabled"` set to anything but `false` and no
  `"enabled": false` gets a warning: `server 'x': "disabled" does not turn it
  off — aelix reads "enabled" (write "enabled": false to keep it off)`. It does
  not say the server starts: trust and D9's record decide that later (round 2,
  verify R2).
- **Overstated text.** The guide's "which file is read", "use the name" and
  delegated-agent paragraphs, D6's delegated-agent row, the Context's
  "trusted" and "headers", and the CHANGELOG line that said delegated agents
  "read the same file".
