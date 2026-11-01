# 0283. Tools are turned on and off through one policy: `--exclude-tools`, the tool flags as locks, `defaultTools` and the `/tool` panel

Status: Accepted (2026-11-01, #510).
Date: 2026-11-01
Issue: #510. Design, owner decisions (2026-10-31: 1–6, 12, 13) and the Codex
design review's triage: `.omc/specs/510-25-design-2026-10-31.md`; the review:
`.omc/specs/510-25-design-codex-review.md` (F02–F05 and F07 land here). Fix
round 1 (2026-11-02): the Codex code review
(`.omc/specs/510-impl-codex-review.md`, F01–F04), the independent verify
(`.omc/specs/510-verify-report.md`, V1–V9) and the owner's answers to the
implementation's two questions (2026-11-01, A and B, in
`.omc/specs/510-impl-report.md`).
Amends: ADR-0279 D15 and D16 (the tool half is done; dated notes), ADR-0179
(FU2's reload union of every extension tool no longer happens under `--tools`;
dated note), ADR-0196 (a profile's `tools:` / `builtin_tools: false` are run
locks, not a one-time `set_active_tools`).
Relates: ADR-0281 (#509, the top-of-window header rule this extends to the
bottom), ADR-0282 (#512, the MCP state vocabulary the placeholder rows use),
ADR-0277 (resumed limits), ADR-0219 / ADR-0194 (the panel's width and
one-frame close), ADR-0235 (pi is the reference), #25 (`/extension`, which
reuses the panel pieces in D10).
pi: `earendil-works/pi` `origin/main` `2c8d1feb1` (2026-11-01),
`packages/coding-agent/` — `src/cli/args.ts` (`--exclude-tools` / `-xt`),
`src/core/mcp-servers.ts` (`createToolNameMatcher`), `src/core/settings-manager.ts`
(`resolveDefaultTools`, `mergeDefaultTools`), `src/core/agent-session.ts`
(`_isAllowedTool`, applied on every `_refreshToolRegistry`).

## Context

Turning one tool off meant naming every other tool — extension and MCP tools
included — in `--tools`. `/tools` listed the active tools and nothing more;
settings had no key. Measured on `0e97bd28` (the design lane's pty grids and
wire captures):

- **M1** — `--no-tools` then `/reload` put `aelix_status` and an extension's
  `weather` on the wire: step 6 of `AgentSessionRuntime.reload()` unioned every
  extension tool and consulted no flag.
- **M2** — `--tools read` then `/reload` added every extension tool (ADR-0179
  FU2, the #53 moat).
- An extension's `set_active_tools` could turn on any registered tool under
  `--no-tools`; a child's extension could turn on a built-in its parent
  withheld; a lazy `register_tool` auto-activated past any filter.
- Eleven places wrote the registry or the active set (the constructor at
  `AgentState(tools=…)` among them — Codex F02), each with its own rules.

## Decision

### D1. One policy, one writer

`aelix_agent_core.harness.tool_policy.ToolPolicy` holds the run's rules:
`exclude` (patterns), the locks (`no_tools`, `no_builtin_tools` with the
built-in names, `allow`), each lock's tag (the flag, or `profile`), and
`saved_off`. `AgentHarnessOptions.tool_policy` installs it; the default allows
everything, so an SDK harness behaves as before. `AgentHarness._commit_tools`
is the ONLY writer of `state.tools` and `state.active_tool_names`: it keeps an
excluded tool out of the registry (recording it for `excluded_tools()`), drops
a locked-off or saved-off name from the set, keeps `None` ("every tool") while
nothing is restricted, and rebuilds the prompt. Through it go: the constructor
(the state is SEEDED EMPTY and the merged tools committed — F02),
`set_active_tools` and the extension action, the `register_tool` refresh (so
the `agent` tool's re-registration and a lazy pack cannot widen anything),
`set_tools` (an MCP attach), `set_tool_policy` (a delta: the live set clamped
to the new policy plus the names turned back on), `restore_active_tools`
(`/agents use`'s rollback, `/reload`'s round trip, RPC's restore). An AST gate
(`tests/cli/test_prompt_tool_honesty.py`) fails on any other assignment,
in-place mutation, `AgentState(tools=…)` or replacement of the harness state
anywhere in `packages/`.

Precedence: excluded > `--no-tools` > `--no-builtin-tools` > `--tools` /
profile > `defaultTools` > on.

### D2. `--exclude-tools` / `-xt`

Names and `*` patterns (pi's matcher: `*` is any run of characters, everything
else literal), comma-separated, over built-in, extension, launch-MCP
(`<server>__<tool>`) and RPC-client MCP tools. Repeating the flag adds to the
list (pi keeps the last; owner decision 4). An excluded tool is out of the
registry — not in the prompt, not on the wire, not callable, not revivable. It
beats `--tools`: `--tools bash -xt bash` has no `bash` and says so once. An
entry that matches nothing is a launch warning, not an error — worded "matches
no tool loaded at launch", since a lazy `register_tool` or an RPC client's MCP
tool can match it later and is excluded then (verify V6). pi's parse is kept,
and where it excludes nothing it now says so (verify V7): `--exclude-tools=bash`
(an unknown extension flag to pi) and a value starting with `-` (`-xt --tools
read` takes `--tools` as the name) each warn at start; the warning names the
value only when it is a registered flag.

### D3. The tool flags are locks (owner decision 1)

`--no-tools`, `--no-builtin-tools` and `--tools` — and an agent profile's
`tools:` / `builtin_tools: false`, tagged `profile` — hold for the whole run.
Nothing widens them: an extension may narrow, never widen; `/reload` restores
`(before ∩ registry) ∪ extension tools ∪ tools newly saved on` and the policy
clamps it, so M1 and M2 are gone. ADR-0179's union survives where no flag
locks the set. Locked tools stay registered: the panel lists them and
`--tools` may still name any loaded tool (`UnknownToolNamesError` is
unchanged).

### D4. `defaultTools` (owner decision 3; ADR-0279 D15; Codex F03, F04)

The key is `defaultTools` in the global file and a trusted project's. pi's
grammar with the aelix base of every tool on: plain names replace the BUILT-IN
set and nothing else; `+name` / `-name` turn any tool on or off, in order; a
project list with a plain name replaces the global one, one of only `±` is
appended to it. An absent key turns nothing off; `[]` is pi's "no built-ins" —
kept apart from absent through `ScopeList.present` and
`SettingsManager.edit_list(nullable=True)`, which writes `[]` and removes the
key for `None`. A project `[]` adds nothing to a global list and is "no
built-ins" alone (pi's merge). Entries with `*` or `!` are warned about and
ignored, and a list whose every entry is refused reads as ABSENT, never as `[]`.
The editor changes one tool: it returns a satisfied list unchanged, then tries
dropping that name's `±` entries (a modifier list that empties is removed),
dropping its plain entry, appending `±name`, and for `[]` as the base `[name]`
or `[name, -name]` — and takes the first candidate that, checked against every
name the change can reach, moves that tool alone.

The editor is handed the file's RAW entries and reads the list and every
candidate through the same validation as the launch (Codex F01, verify V2): a
refused entry (`*`, `!`, blank, a bare `+`/`-`) is never a plain name, entries
match by their stripped form, and refused entries stay in the file. "Moves that
tool alone" covers every way the file is read (verify V1): a project edit, the
two files together; a global edit, the global file alone (every folder without
a project list) AND, when this project has a list, the two together — a
project list of only `±` entries, `[]` included, builds on the global one. The
target is the edited file's own view; a project list that overrides it is the
save's reported mismatch. When no candidate holds both — global absent, project
`[]`, `weather` off on Global — the editor raises `EditRefused` inside the
`edit_list` transaction, nothing is written, and the panel says to save to This
project instead. `SelectionGrammar.checks_both_files` asks the save to read the
project file for a global edit and to refuse when it cannot; D1's grammar does
not. A property test drives 3 × 4,000 random pairs of raw lists (refused and
unstripped entries mixed in) in both scopes: the target, every other tool in
every view, a satisfied request, the toggle back; a refusal only ever for a
global edit under a present project list.

### D5. Saved off is not locked (owner decision 2)

A saved-off tool is registered and inactive. An extension cannot turn it on;
the panel can, and `/reload` re-reads the files. The panel's apply is a DELTA:
only the tools whose saved value moved change, so an extension's runtime
narrowing survives.

### D6. `/tool` (alias `/tools`)

The ADR-0279 D11 panel over a `ToolKind`: groups BUILT-IN, `EXTENSION <name>`,
`MCP <server>`, OTHER; excluded and locked rows in round brackets with a
status tag (`excluded`, `--no-tools`, `--no-builtin-tools`, `--tools`,
`profile`); a disabled or failed server (#512's `runtime_statuses`) and an
extension that failed to load get one placeholder row each, counted neither as
a tool nor as a search match, whose state line STARTS with where to look
(`see /mcp · …`, `see /extension · …`) so a narrow terminal cuts the path, not
the pointer (verify V5); a tool an extension turned off reads `off now`.
Banners say when a flag fixes the set. Enter saves to `defaultTools`, applies
from the next model request (deferred past a running turn, ADR-0279 D6, under
the one shared `ApplyGate` — and not deferred at all when nothing saved differs
from what is applied, so an Enter that changed nothing promises nothing), and emits
`resources_update` with `resources["tools"]`; `set_active_tools` still emits
nothing (`tests/pi_parity`). Headless, `/tool` lists the active tools as
`/tools` did.

### D7. RPC `tools/set_active` refuses (owner decision 5)

A name the RUN keeps off — excluded, or locked off by a flag or a profile — is
refused with the reason (`Cannot turn on bash: bash is removed for this run by
--exclude-tools bash`); nothing changes (`ToolPolicy.run_refusal`). A name
`defaultTools` turns off is turned on, as the panel can (owner decision A,
2026-11-01): it leaves the policy's saved-off set for this run, until the
settings are read again (`resources/reload`, a new session), and nothing is
written. Before fix round 1 a saved-off name was refused too.

### D8. Delegation (owner decision 6; Codex F07)

Children get the parent's `--exclude-tools` patterns as typed, through
`SubagentHost.exclude_tools` → `SpawnPlan.parent_exclude_tools` →
`narrow_tools(…, parent_exclude)` → `AgentProfile.exclude_tools` (not read
from frontmatter) → `resolver.profile_to_flags`, the one place a profile
becomes flags — so the print channel (through `ChildLimits.exclude_tools`) and
the rpc channel (which never reads `plan.limits`) both emit it, and `/agents
show` shows it. `ChildLimits.exclude_tools` is written to the record only when
non-empty (a record without it is what an earlier aelix wrote and reads),
validated on read (no whitespace, comma or control character, no leading `-`,
at most 256 entries of 256 characters), and a resume runs with both runs'
patterns. The child's `--tools` is a lock now, so a child-side extension cannot
turn on a withheld built-in, and its registry lacks the excluded tools.

The parent's FINAL saved-off set rides the same path (owner decision B,
2026-11-01): `delegated_exclusions(policy)` is the run's patterns, then — sorted
— every name `defaultTools` keeps off now (`ToolPolicy.saved_off_in_force`: a
`--tools` that names one keeps it on, and one `/tool` or RPC turned back on is
on), less a name a pattern already covers and a name no entry can carry
(`carriable_pattern`, the record reader's own rule). `cli/entry.py` reads it
live off the runtime host's harness for `SubagentHost.exclude_tools`, and
`/agents show` shows it. A child reads its own settings — an untrusted one
never the project's — so a tool turned off only in the parent's project file,
extension and MCP tools included, would otherwise come back in it.
Suppress-only, like #512's MCP names: it only ever removes.

The list is checked against the record reader's bound BEFORE a child is
admitted or recorded, fresh and after a resume's union
(`exclude_tools_refusal`; Codex F03): more than 256 entries, or one the reader
would refuse, is an error result with the reason — never a truncated list,
never a record its own resume refuses.

### D9. The prompt (pi #10635; Codex F05)

Every tool-dependent part of the prompt follows the committed set: the first
build's skills-catalog gate reads the policy, `/agents use` gates its catalog
on the live set as the rebuilder does, and when a `before_agent_start` handler
changes the tools while the chain returns `event.system_prompt` with text added
— after, before, or both (one handler prepending, another appending; Codex
F02) — the added text is put around the rebuilt prompt
(`_rechain_system_prompt`) so the request's prompt and its tool schema agree.
The original is found inside the returned prompt only when it occurs exactly
once; a prompt that repeats it is matched at its start or end only. The first
build's tool list is pinned by a test of the options it composes
(`test_the_first_builds_prompt_is_composed_without_what_the_policy_keeps_off`;
verify V9), since the factory's rebuild hides it from every later check.

### D10. The shared panel pieces, built for #25

`Lock.tag` / `Lock.kind` (the status word; `cli`, `profile`, `always_on`,
`unavailable`, `shadowed`, `untrusted` pick the state line's wording);
`ResourceItem.role` (`item`, `placeholder`, `info` — the last drawn with a
dot); `ItemState.problem` and `ItemState.live`; a `SelectionGrammar` hook on the
kind (D1's per-item grammar by default; `defaultTools` plugs in);
`effect == "reload"` wording ("/reload applies it", Enter saves only);
`SelectionPanelBody` (keys, render, save/close) apart from the standalone host
`run_selection_panel`, with an optional title-row renderer; `_window` never
leaves a header alone at the bottom either (#509 verify R5) — drawn blank, the
list does not scroll for it; `ApplyGate` moved to `resource_selection` and held
once by the factory for every selection.

### D11. Launch warnings (ADR-0279 D8)

Once per launch, after the first build: an `--exclude-tools` entry that
matches no tool loaded at launch, a `--tools` name an exclusion removes, a
malformed `defaultTools` entry, and a `defaultTools` name no loaded tool has.

## Consequences

- One tool can be turned off for a run (`-xt bash`) or for good (`/tool`)
  without naming every other tool.
- The tool flags mean the same thing before and after `/reload` and whatever an
  extension does; ADR-0179's "a just-written extension tool is usable under
  `--tools`" no longer holds for that flag.
- The schema, the prompt's tool list and the callable set agree on every path
  that changes them, and the gate keeps a new writer from going around them.

## Limits

- A `before_agent_start` handler that REPLACES the prompt (rather than adding
  to `event.system_prompt`) while the tools change keeps its own tool list; the
  schema follows the committed set (pinned by
  `tests/harness/test_tool_policy_510.py`).
- An open session sees a hand edit of `defaultTools` as pending rows; one that
  names a tool with no row (not loaded) applies at the next `/reload` or launch.
- A delegated child resolves `defaultTools` itself (ADR-0279 D14); D8 forwards
  what the parent keeps off, never what it keeps on — a tool the parent's
  project file turns ON is not forced on in an untrusted child.
- A global save the project list makes impossible to confine (D4) is refused
  rather than written to one file and reported; the panel names the project
  file as where to save it.
- Placeholder rows cover disabled and failed MCP servers and extensions that
  failed to load; an extension turned off in settings is #25's (its inventory
  plugs into `ToolKind(extra_rows=…)`). A `disconnected` server gets no row.
- `/reload` still prints an extension load error raw onto the input row (the
  design's M3); #25 replaces it with a transcript line.
- `PolicyExtension(deny_tools=…)` stays an unwired library gate.
