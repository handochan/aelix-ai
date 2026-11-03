# 0284. Extensions are turned on and off through the settings `extensions` filter, in `/extension`'s Installed tab

Status: Accepted (2026-11-03, #25).
Date: 2026-11-03
Issue: #25. Design, owner decisions (2026-10-31: 7, 8, 9, 10, 11 = layout A, 12)
and the Codex design review's triage (F06, F08–F12 land here; F01 was #641):
`.omc/specs/510-25-design-2026-10-31.md`; the review:
`.omc/specs/510-25-design-codex-review.md`. Review round 1 of the
implementation (Codex R01–R09, `.omc/specs/25-impl-codex-review.md`; the
independent verify V1–V12, `.omc/specs/25-verify-report.md`) is folded into
D1, D2, D6, D7, D8, D10 and the new D11, each marked with its finding.
Amends: ADR-0279 D1 (plain path entries in `extensions` are deferred — Codex
F12; a path entry compares case-insensitively on Windows), D8 (the `extensions`
"not supported yet" warning is replaced) and D16 (the extension half is done) —
dated notes there; ADR-0165 D5 (the Installed tab is the panel; dated note);
ADR-0283 Limits (the raw `/reload` print is gone, an extension turned off gets a
`/tool` placeholder; dated note) and its `/reload` step 6 (a tool the rebuild
newly registers is unioned like an extension's, then clamped — D7); ADR-0181 (a pack the settings turn off is not
scanned, so its MCP servers are never dialled).
Relates: ADR-0282 (#512, the MCP state words the read-only rows use; its D9
record is D11's model), ADR-0285
(#641/#638, the import guard phase G builds on), ADR-0179 FU3 (a provider
outlives the extension that registered it), ADR-0177 (the factory-rebuild
`/reload`), ADR-0194 / ADR-0219 (one-frame close, the width), ADR-0235 (pi is
the reference, not a parity mandate).
pi: `earendil-works/pi` `origin/main` `cf74b6765` (2026-11-02),
`packages/coding-agent/src/core/package-manager.ts` — every resource resolved
with an `enabled` flag (`:86-93`), `isEnabledByOverrides` (`:772`), built-ins as
`builtin:<name>` resources a `-builtin:<name>` entry disables and a project
entry overrides (`:1025-1040`), applied at startup and on `/reload`.

## Context

The `extensions` arrays of both settings files carried pi's grammar and nothing
read them: until this change a non-empty one was only warned about
(ADR-0279 D8). The only ways to turn one extension off were deleting its file,
`pip uninstall`, or `--no-extensions` for all of them. `/extension`'s Installed
tab was a read-only list that ended "Read-only: extensions + MCP servers load at
startup".

Measured on `0e97bd28` (the design lane's pty grids, design §1):

- **M3** — a `setup()` that raised, then `/reload`: the error was printed raw
  onto the input row (`❯ /reloadWarning: extension load: ExtensionLoadError(…)`),
  and `/extension` still showed the extension with `✓`, because `tui/shell.py`
  copied the extension lists once at TUI start while the factory refilled them
  on every build.
- **M4** — the Installed tab showed a legacy extension by its absolute path and
  the built-ins by class name.

Four loaders import extension code, and each has to filter
(ADR-0279 D16, Codex C5): the trust **vote load** (user and global extensions
run `setup()` to answer `project_trust` before trust is decided), the **factory**
(every harness build, `/reload` included), the **manifest scan** (which dials a
pack's MCP servers once, at launch) and **`aelix status`**.

## Decision

### D1. Identities and the grammar (owner decision 7)

The key is `extensions`, with ADR-0279 D1's per-item grammar: `!glob`, `+X`,
`-X`, the project file's opinion over the global file's over on (D2), a path
form reaching only its own scope. An extension's identities, the first being
what the panel writes (`core/extension_selection.py` `describe`):

| Extension | Identities |
|---|---|
| a manifest pack | `plugin.id`, then (installed) its entry-point name and its PEP 503 distribution name |
| a manifest-less entry point | its name, its distribution name |
| a legacy `.py` file | its stem (`weather` for `weather.py`) |
| a package directory (`__init__.py`) | the directory name |
| a file a `pyproject.toml` package declares (`[tool.aelix] extensions`) | the package directory's name, then the file's stem, then its row's label `bundle/worker.py` (round-1 verify R7: the label as it reads named nothing) |
| a built-in | `builtin:guardrail`, `builtin:permission`, `builtin:status`, `builtin:agents` |

An installed pack that the directory tier also finds — a symlink in
`~/.aelix/agent/extensions` into site-packages — loads once, from the copy
found first, and keeps its entry-point and distribution names (review R01:
the dedup used to drop them, and `-ep-alias` then imported it).

A path entry's form is the file (a `.py`, a file a package declares) or the
directory (a pack, a package; a `pyproject.toml` package's directory names
every file it declares) — `-extensions/weather.py` in the global file. Both
the item's path and the settings file's folder are compared as given and as
resolved (verify V3: discovery resolves the extension directories, so with a
symlinked `~/.aelix` or macOS `/tmp` a path entry named nothing). On Windows a
path entry compares case-insensitively (`resource_filter.matches_exact`; `\`
was already `/` on both sides): NTFS names are, and
`-C:\Users\Me\…\Weather.py` names `weather.py`. This applies to `skills` and
`prompts` path entries too.

The panel writes the first identity **no other row has** (verify V2:
`ExtensionGrammar`). A global and a project `weather.py` are both `weather`,
and writing `-weather` for one turned both off; it writes the row's path form
then, which reaches only its own file's scope (ADR-0279 D2). A save to the
other file writes the name they share when that moves no other row — both rows
off in one save is `-weather` (round-1 verify R3; a save makes its own file's
rows' edits first, so the order they were toggled in does not matter) — and is
refused otherwise, with which file can name it. When an edit drops an entry
that also named another row (`-weather`, turning one back on), an entry naming
that row alone keeps its value; when only the other file can name it, the save
is refused and names the entry to add there by hand — `add
"-extensions/weather.py" to .aelix/settings.json, then save again — turning
weather.py (global) on would also turn weather.py (project) on, which only
that file can name` — because the panel cannot write it (R2): that row already
reads off there, so it has no edit to save. Action first, as #510's refusals
are; at 50 columns it wraps whole into the four rows a refusal gets. No other row's saved value moves. Rows that share a
label are shown with their tier (`weather.py (project)`), in the list, the save
summary, `/reload`'s line and `/tool`'s group titles (R6).

### D2. Two phases, every loader (ADR-0279 C5)

`extension_filter(lists, phase)` is the loader's `entry_filter`
(`extensions/loader.py`), between discovery and the load. An entry it answers
`False` for is never imported: `load_extensions` never sees it.

| Loader | Phase | Lists read | Test |
|---|---|---|---|
| the trust vote load (`cli/entry.py`, before trust) | G | global only — the manager is untrusted, the project file is not read | `test_a_project_entry_cannot_keep_an_extension_from_voting`, sentinel step 1 |
| the factory (`_build_harness_options`, every build and `/reload`) | P | global, and the project's when trusted (ADR-0279 D2) | sentinel step 3 |
| the manifest scan (`scan_extension_manifests`, launch) | P | the same | sentinel step 2 |
| `aelix status` (`cli/status.py`) | P | the same, trust as `-p` decides it | sentinel step 4 |
| the panel (`ExtensionKind`) | — | reads the last build's inventory; imports nothing | sentinel step 5 |

A delegated child also applies its parent's off-set in both phases (D11).

`-e` entries and the prepended built-ins are never filtered — and neither is a
path `-e` names that another tier found first (Codex F06): discovery keeps the
first copy of a path, and `-e ~/.aelix/agent/extensions/x.py` used to dedup onto
the global tier's carrier with nothing saying the user named it. The carrier now
records its tier and an `explicit` flag (`DiscoveredEntry`). `--no-extensions`
is unchanged. The filter never raises: an entry whose evaluation fails loads,
as before the filter, because a factory that raises on `/reload` leaves the
session with no harness.

The sentinel (`tests/extensions/test_extension_selection_25.py`) plants a legacy
file and a manifest pack whose module writes a file when imported, turns both
off in the global file, and runs all five; the file is never written.

### D3. Listing without importing

The factory publishes an `ExtensionInventory` with every build: each carrier
discovery found, on or off, with its tier, whether `-e` named it, whether the
filter selected it and what the load made of it (an `Extension`, a deferred
shell, an error). A row's text comes from the carrier and manifest TOML alone —
a pack's `plugin.description`, version and declared tools, commands, hooks and
MCP servers; an installed distribution's `Summary` from its metadata; a legacy
file is "(no manifest)" — plus, for a loaded extension, the live counts. An
untrusted project's `.aelix/extensions` is one locked row whose count comes from
listing the directory; no file in it is opened. Under `--no-extensions` the
carriers discovery would find are listed once, at TUI start, locked off.

### D4. Locks (owner decision 9)

Guardrail, Permission and Status: locked on, "always on" (their always-on
policy is unchanged; pi lets `-builtin:<name>` disable its built-ins, aelix does
not). AgentsExtension: locked, following `[features] agents` (`/settings`), on
when it loaded. `-e`: locked on "for this run". `--no-extensions`: locked off.
An untrusted project: one locked summary row. A pack discovery could not
produce (a malformed manifest, a refused `-e`) is a locked `refused` row.

### D5. Saved, applied, pending, failed (Codex F08, F09)

*Applied* is what the last COMMITTED build loaded. The factory fills its
holders — the extensions, the load errors and the inventory — on staging lists
and publishes them as its last step, after which the runtime installs the
harness with no `await` in between; a build that raises publishes nothing, so
`/extension` never shows a set no harness runs. `run_tui` keeps the holders
themselves, not copies (M3).

After Enter a row is *pending* ("off · /reload", "Saved off · now on · /reload
applies it"), and the summary says "Not applied yet: /reload (or a restart)
loads the new set." *Failed* is saved on and not loaded: its own `failed` tag
and an error line, never *pending*. A pure `on_command` pack is *deferred*
until its first command, and a deferred pack whose activation raised is failed
too (`_ExtensionRuntime.activation_failures`; Codex F09: before, the failure
reached only the dispatch layer, and the pack read as loaded).

### D6. What `/reload` says (M3; Codex F08)

Under the TUI the factory no longer prints load errors; `/reload` commits ONE
transcript entry when something changed or failed, and nothing otherwise:

```
Extensions after /reload: weather.py off · jira-tools on · lint.py failed
    ✖ lint.py failed to load: RuntimeError: boom (see /extension)
    ✖ broken was not loaded: Invalid manifest …/aelix-plugin.toml: … (see /extension)
    jira (MCP) stays connected until restart; its tools are hidden
```

An extension saved on that no longer loads reads `failed`, never `off`
(verify V1). A pack discovery refused — a malformed manifest, new or edited —
gets its own line (review R06; it read `broken removed`, or nothing). The
first line's continuation is indented 2 and every later line 4, its own
continuation 6, so a wrapped line never reads as the next one (verify V9).

A `/reload` that raises is caught instead of escaping the input loop, and says
how far it got: `AgentSessionRuntime.reload_phase` (aelix-additive) records the
step — `teardown`, `settings`, `build`, `tools`, `rebind`, `session_start`,
`resources`, `done`. Raised in `build` or before it, the old harness is already
torn down and the new one was never installed: "✖ /reload failed during the
rebuild: … — nothing applied; /reload again or restart". Raised after it, the
new harness runs: "⚠ /reload applied the new extensions, but <step> failed: …".
The panel repeats the failure as a banner until a build succeeds.

*2026-11-03 note (#663, ADR-0289):* "nothing applied" was not true — the old
harness was already torn down, its Guardrail and Permission handlers cleared,
and the session kept running on it. A raise in `settings` or `build` now builds
the session again on the settings it was running with ("… — kept the previous
settings; fix the cause, then /reload again"), or, when that build fails too,
stops ("… — aelix is stopped until /reload succeeds: …"); the line never says
"nothing applied".

*2026-11-03 note (#664):* "caught instead of escaping" held for `Exception`
only. An extension whose import or `setup()` raised `SystemExit` (`sys.exit(1)`)
or `KeyboardInterrupt` went past the loader's `except Exception` and ended
aelix — `-p` with no output, the TUI on `/reload`. The loader now makes
`SystemExit` an ordinary load error naming the extension ("✖ flip.py failed to
load: SystemExit: sys.exit(1) (see /extension)"; a `sys.exit("message")` says its
message) and loads the rest, at launch and on every rebuild. A
`KeyboardInterrupt` still ends the run, since a Ctrl+C during a slow `setup()`
looks the same, but as one line naming the extension and the step ("aelix:
interrupted while the extension … was running its setup()", exit 130) instead
of a traceback. `asyncio.CancelledError` passes through unchanged. A deferred
pack's first command treats `sys.exit` the same way.

### D7. What waits for a restart (owner decision 10; Codex F10, F11)

- **MCP servers a pack declares** are dialled once, at launch. Which plugin
  declared each, and whether the launch started it, is fixed then
  (`McpLaunch`, built from the scan's records; a name `mcp.json` declares is
  the file's, #512). The owner is the pack whose contribution the MCP manager
  KEPT — the last of a name, so a project pack over a global one of the same
  server name (review R03: the first found was recorded, the losing one). On a
  build where the plugin is off now, that server's tool wrappers are left out
  of the harness (`McpLaunch.visible`): its tools are gone from the schema, the
  prompt and execution, and its connection stays until restart. A server's
  wrappers are the ones the manager made for it (`McpClientManager.
  tools_by_server`), not every tool its name prefixes — a server may be named
  `jira__admin` (review R07). Turned back on, the same wrappers return — the
  launch's list is never changed — and are active again, under the tool policy
  (review R04: `/reload`'s step 6 now unions the tools the rebuild newly
  registers, as it does an extension's; ADR-0283's policy clamps them). A
  plugin that was off at launch has nothing started: turned on, its servers
  connect at the next launch.
- **"Hidden" is what the build applied.** The factory records the servers it
  hid with the inventory (`ExtensionInventory.mcp_hidden`), and the panel and
  `/reload`'s line read that, never whether the pack loaded (verify V1: a pack
  saved on whose Python entry raised was said to have its tools hidden while
  `/tools` still offered them). Such a pack's server stays connected AND its
  tools stay in use — decided, not left: the server is the manifest's, started
  at launch from metadata alone (ADR-0181), and the next launch starts it again
  whatever `setup()` does, so hiding it at `/reload` would make the session
  disagree with the launch. The row reads `connected`, "tools in use", and
  `/reload` says `jira (MCP) stays connected; its tools stay in use`.
- **Providers** an extension registered stay in `/model` after `/reload` drops
  the extension, as in pi (ADR-0179 FU3): the runtime records which extension
  registered each (`provider_owners`).
- **Shortcut keys** are bound once, when the chrome is built: a key not among
  them — on an extension `/reload` turned on, or a new one on an extension that
  was already on (review R08) — is told it binds at the next launch, once.

Each is said per extension: in the save summary (`jira-tools: its MCP servers
connect or disconnect only at the next launch.`) and in `/reload`'s entry.

### D8. The Installed tab (layout A, owner decision 11; Codex C21)

`/extension`'s Installed tab is ADR-0279 D11's panel (`SelectionPanelBody`)
over `ExtensionKind`, embedded in `tabbed` (`AelixTUIContext._tabbed_interactive`):

- The tab row is the panel's title row:
  `── Extensions  [Installed] Discover Sources ── Save to [Global]  This project ──`;
  at 50 columns `─ [Installed] Discover Sources ── [Global] Project`.
- Groups BUILT-IN, PROJECT, GLOBAL, INSTALLED, THIS RUN (`-e`), REFUSED, and
  MCP SERVERS — read-only rows drawn with a dot, #512's state word, and the
  declaring plugin; Space on one says "MCP servers are turned on and off in
  mcp.json · /mcp". Extension on/off and server state are kept apart: a server
  whose plugin is off reads `connected · hidden`. Why is in the row's
  description, which wraps; the state line is one row (verify V10). A search
  counts the MCP rows it shows (V12).
- A banner (a failed `/reload`, `--no-extensions`) wraps to at most four rows
  instead of being cut (V10). Under `--no-extensions` every row is locked, so
  the notice says to run without it to turn the others on and off — not that a
  choice still saves (V4: a lock is "a value this run keeps", as on every
  panel, and making this one lock edit the saved value would be a new panel
  mode for one flag). A file `-e` loaded under `--no-extensions` is one row,
  not also a locked-off listing row (review R09).
- The height is fixed when the modal opens — the tallest tab's, under the modal
  cap — and every tab draws exactly that many rows.
- Tab / Shift-Tab switch tabs everywhere. On Installed, ←/→ move "Save to",
  typing searches (`q` included), Space toggles, Enter saves (claimed in the key
  handler), Esc / Ctrl+C discard with the one-line note. Discover and Sources
  keep their keys: ←/→ switch, Discover filters, `q` closes Sources, Enter closes.
- Unsaved edits live in the panel body, so they survive a tab switch; closing
  from another tab says they were discarded. A save that fails keeps the modal
  open on Installed with its edits and the reason. A close prints and unmounts
  in one `print_above_many` suspend.

Enter writes the `extensions` list through `ResourceSelection.save`
(`SettingsManager.edit_list`): the same entry a hand edit would write
(`-weather`), and a save error is shown and nothing applied.

### D9. Plain path entries are deferred (owner decision 8; Codex F12)

ADR-0279 D1 gives a plain entry (no prefix) the meaning "adds a source" — for
`skills` and `prompts`, a directory to scan. For `extensions` that would be a
new loading tier, with its own trust question, and it is NOT implemented here:
a plain entry in `extensions` is warned about once per launch ("would add an
extension source by path, which is not supported yet; it is ignored") and
ignored. This AMENDS ADR-0279 D1 for the `extensions` key — it is not "D1
unchanged" — and is tracked as a follow-up issue; #25's acceptance ends at
turning discovered extensions on and off.

### D10. The rest

- **Launch warnings** (ADR-0279 D8), once, after the first build: a list's
  malformed entries, a plain path entry (D9), an entry naming a built-in
  (`-builtin:guardrail`, `!builtin:*` — pi's way to turn its built-ins off;
  aelix's are locked, D4, so it is ignored and said, verify V8), and an `!` /
  `+` / `-` entry that names no extension found at launch (not under
  `--no-extensions`, where nothing was discovered). An entry-point pack's own
  discovery warning is dropped when the pack is off.
- **`/new`, `/fork`, `/resume`** under the TUI no longer print `Warning:
  extension load:` over the input either (verify V11); what failed to load is
  committed below the new session's banner (`Extensions in this session:`).
  A row's error leads with the exception's type (`RuntimeError: boom`), as the
  examples here show (V7).
- **`/tool`** gets one locked placeholder per extension the settings turned
  off — `extension off: its tools are not loaded`, `ext off`, "see /extension ·
  off in settings" — through ADR-0283's `ToolKind(extra_rows=…)`. When two
  extensions that are on share a label, each group title carries its tier
  (`EXTENSION  weather.py (global)`, `ToolKind(extension_tiers=…)`; round-1
  verify R6), as the placeholder's does.
- **`aelix status`** applies phase P: an extension turned off is listed under
  "Turned off in settings (N; not imported)" and in the JSON's
  `off_extensions`, and is not loaded.

### D11. A delegated child never loads what its parent kept off (verify V5)

The owner's rule — a child never gets more than its parent (#510 decision B,
#512 D9). A child with `inherit_extensions: true` reads its own settings,
usually untrusted (`--no-approve`, ADR-0197 §(g)), so it imported, ran
`setup()` of, and dialled the MCP servers of every extension the trusted
parent's PROJECT file turned off (measured on both channels,
`.omc/specs/25-verify-report.md` V5).

- **The record.** After each committed build the parent records what its
  settings turned off (`ExtensionOffRecord`: each carrier's resolved path, or
  `ep:<name>` for an entry point; `every` under `--no-extensions`), with what it
  inherited. `aelix_agents.print_channel.build_child_env` — which both channels
  and a resume use — writes it as `$AELIX_EXTENSIONS_PARENT_OFF` (JSON, ASCII,
  at most 16 KiB; past that it says `every`, which is less than the parent,
  never more).
- **The child** reads it once at launch, removes it from `os.environ` (its bash
  tool and `api.exec` do not hand it on), and applies it in phases G and P —
  the vote load, every build and the manifest scan — before anything is
  imported: a carrier it covers is off whatever the child's files say, and its
  MCP servers are not dialled. It records the union for its own children.
- **Suppress only.** The record names carriers and nothing else; it can turn
  an extension off, never on, and it names nothing to load. `-e` and the
  built-ins are never covered (as for the settings, D2). `AELIX_` is a prefix a
  cwd `.env` cannot set (`runtime_bootstrap._DOTENV_NEVER`), so a project cannot
  inject it — and could only switch the user's extensions off with it.

## Consequences

- An extension can be turned off without deleting it, from `/extension` or by
  writing `-name` in either settings file; the next `/reload` or launch does
  not import it, its tools, commands, hooks and skills are gone, and its MCP
  tools are hidden.
- A failed `/reload` no longer prints over the input or claims success; a
  `/reload` that raises no longer escapes the input loop. (*2026-11-03, #663:*
  and what runs after it is said — ADR-0289.)
- Opening `/extension` and closing it now leaves one line ("Extensions: closed,
  nothing changed."), as `/skill` and `/tool` do.

## Limits

- A plain path entry in `extensions` does not add a source (D9).
- An extension's MCP connections, providers and newly introduced shortcut keys
  wait for a restart (D7); hot-unload is not in scope (owner decision 10).
- A delegated child resolves `extensions` itself (ADR-0279 D14), within D11's
  record: what the parent kept off is matched by path, so a child running in a
  subdirectory with its own `.aelix/extensions` is not covered for those, and a
  profile's own `-e` entries load as `-e` always does.
- The inventory is the last build's: an extension installed after it is listed
  from the next `/reload` or launch.
- A legacy extension's tool, command and hook counts are known only once it has
  loaded; an off one says "(no manifest)".
- A `/reload` that fails in `build` leaves the session on a torn-down harness,
  as before; the line now says so and points at `/reload` or a restart.
  *2026-11-03 note (#663, ADR-0289):* no longer — the line did not say so (it
  said "nothing applied"), and the torn-down harness ran tool calls with no
  gate. The runtime now rebuilds on the previous settings or stops.
