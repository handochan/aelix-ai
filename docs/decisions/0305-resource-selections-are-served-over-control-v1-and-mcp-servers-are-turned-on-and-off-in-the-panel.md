# 0305. Resource selections are served over control-v1, and `/mcp` turns an `mcp.json` server on and off

Status: Accepted (2026-11-18, #624). Designed 2026-11-17 ("round 0"), revision 1 2026-11-18 after
a Codex and an opus review; implemented 2026-11-18. What holds each decision is in "How it is held",
each with the condition it was measured under; where the build differs from the design draft,
"Where the build differs" says how and why. Fix round 1 (2026-11-18), after an independent
verify pass and a Codex review of the implementation, changed D3, D4, D13, D14, D16 and Limits;
each changed paragraph says "fix round 1", and its evidence is in `.omc/probes/623-624/fix-r1/`.
Date: 2026-11-18
Issue: #624. Lands in one commit with #623 (ADR-0302). Lead's call: one lane and one PR, Batch
B6, P1. Refs #644 (open; not decided here).
Design: `.omc/specs/623-624-adr-drafts-2026-11-17.md` (section B, revision 1); implementation
contract: `.omc/specs/623-624-impl-contract-2026-11-17.md`; evidence:
`.omc/probes/623-624/impl/` (`writer/`, `core/`, `framework/`, `lane/`).
Line numbers: the Context section's aelix-ai lines are at `a1606f58`, the commit the design was
read against. From the Decision on they are at this change, and a sentence about code this
change replaced says "at `a1606f58`". aelix-acp lines are at `16d6555b`, pi's at `5f89cc0a`.
Supersedes: ADR-0282 D8's second bullet, "A `/mcp` toggle that writes `enabled` back to the
file". Owner, 2026-11-17: "쓰기까지 지금 구현".
Amends, each with a dated note:
- ADR-0275: two methods, a `capabilities` field, and `selections/set` among the methods that
  open dialogs;
- ADR-0279: served over RPC; an MCP kind through an adapter; `Effect` gains `restart`; the
  panel's edit refusals, its save-then-apply and the no-op drop move into the core module; a
  project file is created only with consent, checked inside the write;
- ADR-0282: D1's warning names a type, not a value; D8 (above); D6's table;
- ADR-0304 D14 (done);
- ADR-0155: `/mcp` opens a panel, and `/mcp list` keeps the table;
- ADR-0284 D8: the `/extension` MCP rows stay status-only.

Relates: ADR-0302 (these methods are version 1 from the freeze commit), #644 (open), #612.

Owner:
- 2026-11-16 on #624: "beta.3 필수로 확정";
- 2026-11-16, O3: "추가 (권장)". Agents are among #624's targets (#624's body);
- 2026-11-17: "779 기다리고 612 123 623 모두 베타버전에 넣을겁니다";
- 2026-11-17 on MCP: "쓰기까지 지금 구현". As the lead's brief summarises the option: build the
  `mcp.json` writer, a TUI `/mcp` toggle and the RPC method now;
- 2026-11-18 on Studio #7 extensions: "aelix-ai는 바꾸지 않음 (권장)" (D8);
- 2026-11-18 on #644: "beta.3 뒤에 결정 (권장)" (D12).

## Context

This section describes `a1606f58`, the commit the design was read against; its aelix-ai line
numbers are at that commit.

- **The selection service.** ADR-0279's service, `ResourceSelection`
  (`core/resource_selection.py:366-691`):
  - reads both settings files (`snapshot`, `:466-512`);
  - writes one (`save`, `:524-615`);
  - applies (`apply`, `:628-691`).

  Four kinds plug in: `SkillKind`, `ToolKind`, `ExtensionKind` and `AgentKind`. The TUI panels
  call `snapshot`, `save` and `apply` in that order (`tui/selection_panel.py:1125`, `:1247`,
  `:1270`).
- **No control-v1 method reads or writes those selections.**
  - `settings/set` writes three of the lists whole, in the global file only
    (`rpc/control.py:667-679`; `RESOURCE_LIST_KEYS`, `:84-88`).
  - `tools/set_active` changes the session only.
- **No headless selection CLI exists (opus L7).** #624's body says "TUI 패널·headless CLI와 같은
  서비스와 의미를 쓴다(#511 완료 조건)". aelix's root commands are `extension`, `docs`, `status`
  and `update` (`cli/args.py:115-119`), and `aelix extension` installs, verifies and lists;
  it does not turn anything on or off. "The same service" here is therefore the TUI panels'
  service. A headless selection CLI would be its own issue, and could reuse what D10 moves into
  the core module.
- **Where the controls are built.**
  - `SkillControl`, `ToolControl` and `ExtensionControl` are built only in the TUI branch
    (`cli/entry.py:5138-5225`).
  - `agent_control` is built for every mode (`:4810-4827`; ADR-0304 D14: "#624 passes the same
    object to RPC").
  - `run_rpc_mode` receives none of them (`:5378-5388`; `rpc/rpc_mode.py:2290-2293`).
- **MCP today.**
  - An `mcp.json` entry's `enabled` (ADR-0282 D1) lives in a different file from the settings
    lists.
  - A launch reads ONE file: `$AELIX_MCP_CONFIG`; else `<cwd>/.aelix/mcp.json` if it exists,
    trusted or not; else `~/.aelix/agent/mcp.json` (`cli/config.py:235-297`, ADR-0282 D2).
  - The loader PARSES a project file whatever the trust (`:284-297`). Its warnings carry that
    file's server names, and it quotes a non-boolean `enabled` value (`:342-345` via `_shown`,
    `:200-207`).
  - Nothing writes `mcp.json`.
  - `/mcp` is a read-only table (`tui/mcp_viewer.py:46-131`).
  - `/extension` lists the servers as status rows whose refusal already says "MCP servers are
    turned on and off in mcp.json · /mcp" (`core/extension_selection.py:1054`), under "MCP
    SERVERS status only · /mcp manages them" (`:1101`, `:1131`).
  - #644 (open) asks whether to merge the two files as pi does.
- **pi** (`earendil-works/pi` `5f89cc0a`, `packages/coding-agent/`):
  - `/mcp` is a per-server action menu. "Enable"/"Disable" write the server's file, and
    "Enable/Disable in this project" writes a project override, which works because pi merges
    the files (`src/extensions/mcp/index.ts:736-760`, `:828-855`).
  - Its writer re-serialises the whole file with `JSON.stringify`
    (`src/extensions/mcp/config.ts:161-185`, `:216-232`). Turning a server on deletes `enabled`
    (`:174-176`).
  - The change connects or disconnects the server at once (`index.ts:741-758`).
  - pi's RPC has no selection method (`src/modes/rpc/rpc-types.ts:22-74`).

## Decision

### D1. Two methods and one `capabilities` field

Lead's call: generic `selections/list {kind}` and `selections/set {kind, scope, changes}`, plus
`capabilities.selectionKinds`. ADR-0275's two-segment `<noun>/<verb>` naming is kept, and
`extensions/list` and `agents/list` keep their meaning.

Refined here (this ADR):

- **The noun.** `selections` is #511's own noun ("one settings contract", ADR-0279's
  `ResourceSelection`). `resources/…` was not used: `resources/list` is the live inventory,
  with another row shape.
- **`capabilities.selectionKinds`** lists the kinds this host serves, sorted, or `[]` where it
  serves none. It is an OPEN enum (ADR-0302 D3): a client ignores a kind it does not know.
- **Listing the methods.** The two methods are in `capabilities.methods` only when that list is
  not empty. Where it is empty, a call is answered "This RPC host serves no selections", as
  `auth/*` answers "This RPC host has no authentication storage" (`rpc/control.py:738`). That
  is opus L3: the round-0 text would have read "…(this host serves: )".
- **The kind names** are the kinds' own `ResourceKind.kind` values: `skill`, `tool`,
  `extension`, `agent` (`core/skill_selection.py:286`, `core/tool_selection.py:589`,
  `core/extension_selection.py:893`, `core/agent_selection.py:264`), plus `mcp`.
- Prompt templates are not a kind: they have no panel (ADR-0279 D10).
- Both methods are version 1 from the freeze commit (ADR-0302 D1).
- **As built.** `aelix --mode rpc` serves `skill`, `tool`, `extension` and `mcp` always, and
  `agent` when the launch has an agent profile service (`SelectionControls.by_kind`,
  `cli/entry.py:6074-6086`); `test_capabilities_lists_no_agent_kind_without_an_agent_service`
  (`tests/rpc/test_selections_rpc_624.py`) holds the second half. `capabilities.selectionKinds`
  is always present, `[]` on a host given no kind (`rpc/control.py:434`), and `aelix-server`'s
  WebSocket host is such a host (ADR-0302 D10).

### D2. `selections/list`

Read-only: it bypasses the mutation lock (ADR-0275 §2). Params: `{"kind": <string>}`.

```json
{
  "kind": "skill",
  "effect": "next_request",
  "scopes": {
    "global":  {"path": "/home/u/.aelix/agent/settings.json", "writable": true,  "reason": null, "exists": true},
    "project": {"path": "/w/.aelix/settings.json", "writable": false, "reason": "this folder is not trusted", "exists": false}
  },
  "items": [ /* rows, D3 */ ],
  "problems": [],
  "notices": [],
  "unreadable": [],
  "policyPending": false
}
```

- **`effect`** is `next_request` (skill, tool, agent), `reload` (extension) or `restart` (mcp).
  It is an OPEN enum (ADR-0302 D3); a client handles an unknown value as written there.
- **`scopes`** gives `ScopeStatus` (`core/resource_selection.py:152-160`) for both scopes,
  always both.
- **`problems`, `notices`, `unreadable`, `policyPending`** are `Snapshot`'s
  (`core/resource_selection.py:163-184`). A non-empty `unreadable` means nothing will be
  applied from what the files say now (ADR-0279 D6).
- **Refusals** (`rpc/control_selection.py:186-194`):
  - a kind this host does not serve: "Unknown selection kind: x (this host serves: agent,
    extension, mcp, skill, tool)". A string `x` is cut at 120 characters and ends in "…";
  - a `kind` that is not a string is named by its Python type name instead, so a missing `kind`
    reads "Unknown selection kind: NoneType (this host serves: …)" (as built; the draft quoted
    only the string case);
  - the host serves no kind: D1's text.

### D3. A row

There is one explicit serializer (`rpc/control_selection.py`), camelCase like `decidedBy` and
`activeTools`. It is not `dataclasses.asdict`, which would make every future dataclass field a
wire field by accident. That was ADR-0198's lesson: kernel field names had silently become a
compatibility surface. ADR-0304 D14's `json.dumps(asdict(state))` test stays, as the guard that
every field CAN be serialised.

| Wire | From | Type |
|---|---|---|
| `key` | `ResourceItem.key`: opaque, the id `selections/set` takes | string |
| `label` | `.label` | string |
| `role` | `.role` (OPEN enum: `item`, `placeholder`, `info` today) | string |
| `invocation`, `description`, `group`, `groupLabel`, `location` | same names | string |
| `identities` | `.identities` | string[] |
| `path`, `scope`, `sourceBase`, `packagePath` | `.path`, `.scope`, `.source_base`, `.package_path` | string or null |
| `saved`, `applied` | `ItemState.saved`, `.applied` | bool |
| `inForce`, `pending` | the properties `in_force`, `pending` | bool |
| `decidedBy` | `.decided_by`: `global`, `project` or null (closed) | string or null |
| `lock` | `Lock` (`core/resource_selection.py:103-119`): `{value, reason, tag, kind, refusal}`. `kind` is an OPEN enum, `""`, `cli`, `always_on`, `unavailable`, `profile`, `shadowed`, `untrusted`, `status` today | object or null |
| `problem` | `.problem` | string or null |
| `live` | `.live` | bool or null |
| `liveNote` | `.live_note`, `[status word, clause]` (ADR-0304 D14) | string[2] or null |
| `refusal` | what `selections/set` refuses a change of this row with (D4); null exactly when the row may change | string or null |

- **Per-kind values today (Codex 14), recorded as they are and fixtured as they are. Nothing
  here changes them.**

  | Row | `role` | `lock.kind` | `lock.tag` | Where |
  |---|---|---|---|---|
  | skill, untrusted project summary | `item` (`identities` empty) | `""` | `""` | `core/skill_selection.py:417-434`, `:477` |
  | skill forced by `--skill` or a profile | `item` | `""` (not `cli`) | `""` | `:467` |
  | extension, untrusted summary | `item` | `untrusted` | `untrusted` | `core/extension_selection.py:1007-1016`, `:1183-1185` |
  | extension, the built-in `JobsExtension` (#722; fix round 1) | `item` (`identities` empty) | `always_on` | `/settings` | `:1165-1169`; the `refusal` bullet below |
  | extension, an MCP server's status row (#25) | `info` | `status` | its state word | D16: `saved` and `applied` both say whether this launch started it (fix round 1) |
  | agent, untrusted summary | `placeholder` | `""` | `""` | `core/agent_selection.py:387-403`, `:441` |
  | mcp, a skipped entry (new) | `placeholder` | `unavailable` | `skipped` | D16 |
  | mcp, under `$AELIX_MCP_CONFIG` (new) | `item` | `cli` | `$AELIX_MCP_CONFIG` | D12 |
  | mcp, untrusted project file (new) | `placeholder` | `untrusted` | `untrusted` | D12 |
  | mcp, a name a delegating parent keeps off (new, as built) | `item` | `cli` | `inherited` | D12 |
  | mcp, a server an extension declares (new) | `info` | `status` | its state word | D16; `saved` and `applied` both say whether this launch started it (`tests/core/test_mcp_selection_624.py`) |

  `lock.kind` is open from the freeze (opus M6). It grew with #510 and #25 (`LockKind`,
  `core/resource_selection.py:41-43`), and a closed enum would make every new lock kind a
  version 2. The `$AELIX_MCP_CONFIG` lock uses the existing `cli`, not a new value. The
  variable is the user's explicit launch-time choice of file (`cli/config.py` docstring:
  "explicit user choices"), as `--skill` is a launch-time choice, and `cli`'s state line
  ("Locked on/off: …") reads right for it.
- **`refusal` (opus L3).** It is computed by D10's `edit_refusal(state)`. It is null only for a
  row with `role == "item"`, non-empty `identities` and no lock. Every other row gets a
  refusal: a lock's refusal, an `info` row's "… is status only", or "<label> cannot be turned
  on or off here" for a `placeholder` or an identity-less row with no lock. So
  `ResourceSelection.save`'s silent drop of non-item rows (`core/resource_selection.py:587-594`)
  can no longer be reached through RPC with an answer that looks like success.
  - **An identity-less row with no lock (corrected in fix round 1, verify live-RPC 3).** Round 0
    said none existed at `a1606f58`. One did: the built-in `JobsExtension` (#722), which the
    extension kind lists while background jobs are on, was an `item` with no identity and no
    lock, and the panel's Space recorded an edit that the save then dropped (measured in process
    at `a1606f58`, `.omc/probes/623-624/fix-r1/R/probes/jobs_row.base.out`; every live RPC child
    of the verify pass listed it at `beadfac4` with `identities: []`, `lock: null`). It now has
    the lock that says what decides it, as `builtin:agents` already had ("follows [features]
    agents (/settings)"): `{value: <loaded>, reason: "follows backgroundJobs.enabled
    (/settings)", tag: "/settings", kind: "always_on"}`. Any other built-in with no identity is
    locked "built into aelix" (`_UNNAMED_BUILTINS`, `core/extension_selection.py:95-97`, used at
    `:1165-1169`). Its refusal, as a Space or a `selections/set` change of it says, is
    "JobsExtension is locked: follows backgroundJobs.enabled (/settings)", in the words the
    other built-ins' refusals use (fix round 2, verify N5; it said "Locked: …" before). The rule
    is `edit_refusal`'s: a row with no identity locked `always_on` is named by its label. `test_the_jobs_built_in_is_locked_and_no_identity_less_row_is_unlocked`
    (`tests/rpc/test_selections_rows_624.py`) asserts over RPC, with `JobsExtension` listed,
    that no `item` without identities lacks a lock; another test asserts that every placeholder
    row in the rigs carries one.
- **Strings.** Strings come from files (frontmatter, `mcp.json`, paths) and are sent as they
  are. The TUI flattens them for the terminal (ADR-0279 D11); a client renders them as text.
  The mcp kind's rows carry no endpoint, command, argument, environment, header or url (D14).
- **The `mcp` kind's keys cannot collide (fix round 1, Codex CX-11).** A file entry's key is its
  name, or `file:<name>` when the name starts with `extension:`, `untrusted:` or `file:`; a
  server an extension declares is `extension:<name>`; the untrusted project file's placeholder
  is `untrusted:project` (`file_key`, `extension_key`, `UNTRUSTED_KEY` in
  `core/mcp_selection.py`). No two rows can share a key, whatever the names, and no row is left
  out because of a key: at `beadfac4` a file entry named `extension:foo` hid an extension's
  server `foo`. `applied`, `live` and `liveNote` are matched by row key. The one server left
  out is an extension's whose NAME the file in force declares, because the launch does not
  start it (`cli/entry.py:4002-4017`), so the file's entry is that name's row. Held by
  `test_a_file_server_named_like_another_namespace_hides_no_row`,
  `test_a_skipped_entry_named_with_a_reserved_prefix_keeps_its_own_key` and
  `test_an_extension_server_named_like_a_file_entry_is_the_files`
  (`tests/core/test_mcp_selection_624.py`), and over RPC by
  `test_a_file_server_named_extension_colon_is_listed_and_set_by_its_key`
  (`tests/rpc/test_selections_rpc_624.py`).
- **The trust pointer is not sent over RPC (opus L8).** The texts "this folder is not trusted
  (/trust to decide)" (`UNTRUSTED_SCOPE_REASON`, `core/resource_selection.py:49`) and "this
  folder is not trusted; /trust to decide" (`UNTRUSTED_LOCK_REASON`, `:47`; used at
  `core/skill_selection.py:477`, `core/agent_selection.py:441`,
  `core/extension_selection.py:1173` and by the mcp kind) name the TUI's `/trust`. At
  `a1606f58` each was a literal in its file.
  - An RPC client decides trust elsewhere. aelix-acp relaunches with `--approve`/`--deny`
    (`session.py:173-174`), through `_aelix/manage` `workspace/trust` (`server.py:287-293`) or
    its own `/trust on|off`.
  - So the two texts are constants in `core/resource_selection.py`, the kinds use them, and
    the RPC serializer replaces each exactly, wherever it appears in a reason, a refusal, a
    problem, a notice, a mismatch, an apply warning or a `scopes` reason, with
    `RPC_UNTRUSTED`, "this folder is not trusted" (`rpc_words`,
    `rpc/control_selection.py:70-78`). A refusal `selections/set` raises goes through it too.
  - `test_an_untrusted_folder_lists_its_per_kind_rows_without_the_trust_pointer`
    (`tests/rpc/test_selections_rpc_624.py`) asserts that no record on the wire contains
    `/trust` in an untrusted folder, for every kind.
- **Save and refusal errors carry RPC words (fix round 1, verify F4 and live-RPC 1).** The rule:
  an error `selections/set` sends names no TUI-only action. Besides the trust texts above, the
  `mcp` kind's "the file aelix reads here changed (now <file>); nothing written; reopen /mcp"
  (`REOPEN_MCP`, `core/mcp_selection.py`) ends "list again" over RPC (`RPC_LIST_AGAIN`, in
  `rpc_words`): the client takes a fresh `selections/list`. At `beadfac4` it reached the client
  as "reopen /mcp" (the verify pass's live RPC child, a project `.aelix/mcp.json` created while
  the save waited for the lock). `test_the_file_changed_error_says_list_again_over_rpc`
  (`tests/rpc/test_selections_rpc_624.py`) drives that race. Every other save or refusal text the
  five kinds can send was read for a TUI pointer and has none: the shape and unknown-row errors,
  D12's scope reasons, the settings manager's errors, the writer's refusals ("edit it by hand",
  "edit the file by hand", "try again", "another aelix has been saving …", "now resolves to
  another file", "is no longer there"), "the save was cancelled; nothing written" and the consent
  texts (`.omc/probes/623-624/fix-r1/M/`, fork M's inventory).
- **The texts that keep a TUI pointer, and why.** Lock reasons and group labels keep theirs, and
  a row refusal error is the row's own lock text (`edit_refusal`, the same string as the row's
  listed `refusal`), so a client can match the error to the row. Each names an aelix panel whose
  RPC counterpart is in this same contract, and a client shows them as text or reads
  `lock.kind`/`tag` (Limits). Kept, each seen on the verify pass's live RPC child ("wire") or
  read in the kind's code ("code"):
  - extension kind, MCP status rows: the group label "MCP SERVERS  status only · /mcp manages
    them" and the refusal "MCP servers are turned on and off in mcp.json · /mcp" (wire);
  - extension kind, built-ins: "(/permissions)", "(/settings)" (wire);
  - tool kind: "see /mcp · …", a disabled server's placeholder (wire); "see /extension · failed
    to load: …" and "(/agents use --none clears it)" (code);
  - agent kind: "see /agents list · did not load: …" (code);
  - `mcp` kind, an extension's server: "<name> is declared by <plugin> · /extension turns the
    plugin on and off" (code);
  - skill kind, the notice "Run one with /skill:<name>; /skill: lists them." (wire).

  The extension kind's apply warning "/reload applies extension changes" never reaches RPC: its
  effect is `reload`, so `apply` is `null` (D8).

### D4. `selections/set`

A mutation: it takes the mutation lock, and needs an idle harness and no classic command in
flight (ADR-0275 §2; D7). Params:

```json
{"kind": "skill", "scope": "global", "changes": [{"key": "pdf-tools", "on": false}, {"key": "review", "on": true}]}
```

- **`changes` is an ARRAY of `{key, on}` (revision 1, opus M7).**
  - It is applied in array order. JSON object member order is not a contract: parsers need not
    keep it, and JavaScript moves integer-like keys first.
  - At most 1,024 entries; a key is a string of at most 8,192 characters, and `on` a boolean.
  - The same key twice is refused.
  - The TUI's toggle order is its edit order. `ResourceSelection.save` keeps it, after its
    own-scope-first stable sort (`core/resource_selection.py:600`), and a grammar that writes a shared name
    (`ExtensionGrammar`) depends on it.
- **`scope`** is `global` or `project`, required when `changes` is not empty. ONE file is
  written per call (ADR-0279 C20). As built (`_scope`, `rpc/control_selection.py:273-288`): a
  missing `scope` is refused only when `changes` is not empty; a `scope` that is neither word is
  refused always, also with an empty `changes`; and whether the scope is writable is checked
  only when there are changes, so `{"changes": []}` with any known scope writes nothing and
  applies (the fixture case "no changes with a scope write nothing either").
- **No-ops are dropped inside the shared save (revision 1, Codex 12).**
  - `ResourceSelection.save` drops every edit whose wanted value equals the row's `saved` value
    in the snapshot `save` itself takes at its start (`before`, `core/resource_selection.py:581`).
  - As built, the drop is judged only from files that were read: when the snapshot has an
    unreadable file, no edit is dropped as a no-op, and the save goes on to refuse it
    (`:583-586`).
  - Both callers reach the same function with the same arguments. So for the same bytes on
    disk when `save` starts and the same ordered changes, the TUI and RPC write the same bytes.
    That holds also when the file changed between the panel opening and Enter.
  - **The drop reads the row's `saved`, which may be the other file's (verify N2; the lead kept
    the behaviour in fix round 1).** `saved` is the merged value: this project's file, else the
    global one. So with `-x` in the global file and `+x` in this project's, the row is saved on,
    decided by `project`, and `selections/set {scope: "global", changes: [{key: x, on: true}]}`
    answers success with `written: []`, `changed: []` and `mismatches: []`, and the global file
    keeps its `-x` (`test_a_global_set_to_what_this_projects_file_decides_writes_nothing`,
    `tests/rpc/test_selections_rpc_624.py`; the verify pass's `noop_scope_probe`,
    `.omc/probes/623-624/verify-r1/probes/`). The panel cannot ask for this: its Space starts
    from the row's own value, and the TUI sends the same edit to the same function.
  - Round 0 dropped no-ops in the RPC handler against a fresh snapshot, while the panel kept an
    edit made against the snapshot it opened with. Codex 12 reproduced the two writing
    different bytes after another writer changed the list to `["+s","-s"]`.
  - The panel's own double-toggle cancel (`tui/selection_panel.py:364-367`) stays as it is: it
    is an edit being withdrawn, not a save rule.
  - Without the drop, an RPC no-op would rewrite the list: the per-item edit drops and re-adds
    exact entries (ADR-0279 D4).
  - The one TUI-visible effect is in that concurrent case. An edit that the file now already
    says is not written, and the summary says the files already said it.
- **An empty `changes`** writes nothing and applies what the files say now. That is the panel's
  Enter with no edits: both call `commit` (`core/resource_selection.py:905-`), which saves only
  `if edits` and then applies (D10).

**Cancelling (revision 1, opus M2).**
- **During the consent dialog (D6):** `selections/set` can be cancelled. `commands/cancel`, with
  its `requestId` or with none while it is the active operation, ends the wait, nothing is
  written, the answer is `Command cancelled`, and the mutation lock is released. It is not in
  `CANCELLABLE_METHODS` (`rpc/control.py:126`). Instead, the operation record carries a
  `cancellable` flag that the handler sets for the dialog's duration, and
  `RpcControlPlane.cancel` (`:328-353`) honours either.
- **Once the save starts** (the settings lock wait, at most 30 s, `_LOCK_TIMEOUT_S`
  `aelix_ai/settings/storage.py:68`, then the write): `commands/cancel` answers
  `not_cancellable`.
  - The reason in round 0 ("a worker thread that cannot be stopped") was inaccurate:
    `edit_list` abandons a write cancelled while it waits for the lock
    (`aelix_ai/settings/settings_manager.py:1111-1112`, `:1182`).
  - The real reason: a cancel that arrives after the list was built cannot stop the write
    (`:1085-1088`). Answering `Command cancelled` could then follow a write that happened, and
    the answer has to be exact.
  - A queued `selections/set` can always be cancelled by its id.
- **The bound.** The consent dialog waits at most 45 s, passed as the dialog's own timeout
  (`rpc/control_ui.py:57-60` reads `opts.timeout`, capped by `DIALOG_TIMEOUT_SECONDS = 300`,
  `:28`). An unanswered dialog is "Not saved: creating <path> was not confirmed".
- **aelix-acp's 60 s.** aelix-acp gives `_aelix/manage` actions other than `auth/login`,
  `resources/reload` and `agents/run` a 60 s timeout and sends no cancel for them
  (`server.py:309-310`, `session.py:403-407`).
  - `selections/*` are not in its allowlist yet (`server.py:303`).
  - When they are, an unanswered dialog ends at 45 s, inside those 60 s.
  - Under settings-lock contention, a call can take up to 45 + 30 s. The consumer list's row
    therefore asks aelix-acp to give `selections/set` at least 90 s, or to send
    `commands/cancel` with the `requestId` when it times out. aelix-acp's change, recorded.

**Refused, and nothing written.** Each is an error, checked in this order
(`set_selection`, `rpc/control_selection.py:329-358`). The texts are the code's; a quoted
client string is cut at 120 characters and ends in "…":

1. `kind` not served (D2's texts).
2. `changes`: "changes must be an array of {key, on}" (also when it is missing);
   "changes has <n> entries; at most 1024"; "changes[<i>] must be {key: string, on: boolean}";
   "changes[<i>].key is longer than 8192 characters: <key>"; "changes names <key> twice".
3. A key that is no row: "Unknown <kind> row: <key>", as in "Unknown skill row: x". When a file
   of the kind did not load, the error goes on with the listing's reason for it (fix round 1,
   verify live-RPC 2: a set against an `mcp.json` that does not parse answered only "Unknown mcp
   row: second", while `selections/list` said why): "Unknown mcp row: second; <file> did not
   load: not valid JSON at line 1 column 27 (JSONDecodeError)" (`_unknown_row`;
   `test_a_set_against_an_mcp_json_that_does_not_load_says_why`, and
   `test_an_unknown_row_with_every_file_loaded_says_only_that` for the plain text, both
   `tests/rpc/test_selections_rpc_624.py`).
4. A row whose `refusal` is not null (D3): that refusal, the panel's Space words from the one
   function both call (`edit_refusal`, `core/resource_selection.py:846-871`; D10), with the
   trust pointer replaced as in D3.
5. The scope: "scope is required with changes: global or project"; "Unknown scope: <x> (global
   or project)"; and a scope that is not writable, in the panel's ←/→ words from the same
   shared function (`scope_refusal`, `:874-883`): "Cannot save to This project: <reason>".
6. A save that would create `.aelix/settings.json` without consent (D6).
7. A failed save: "Not saved: <the SaveResult error>", the panel's red line
   (`tui/selection_panel.py:1372`) without its "✖ ", in RPC words (D3).

Result:

```json
{
  "kind": "skill", "scope": "global", "effect": "next_request",
  "written": ["/home/u/.aelix/agent/settings.json"],
  "changed": [{"label": "pdf-tools", "saved": false}],
  "mismatches": [],
  "apply": {"applied": true, "deferred": false, "turnedOn": [], "turnedOff": ["pdf-tools"], "warning": null}
}
```

- `written`, `changed` and `mismatches` are `SaveResult`'s (`core/resource_selection.py:187-196`);
  all three are `[]` when nothing was saved.
- `apply` is `ApplyResult`'s (`:199-205`), or `null` when the kind's effect is `reload` or
  `restart`: nothing is applied in place.
- A save that succeeded with an apply that raised is a success with `apply.warning` set to
  "saved, but not applied (<the error>)" and `applied: false` (`commit`'s default). The panel
  passes `commit` its own `on_apply_error`, which writes its "⚠ <title>: saved, but not applied
  (…)." line and reports no apply (D10;
  `test_an_apply_that_raises_is_a_warning_or_the_callers_answer`,
  `tests/core/test_resource_selection_624.py`).

### D5. Scope and trust

Unchanged from ADR-0279 D2/D3, because the service is the same:
- A project scope is writable only when the folder is trusted at launch AND the settings
  manager's live flag still says so (`core/resource_selection.py:398-407`).
- An untrusted project's file is never opened. Its rows are the kinds' locked rows (D3's
  table), and its `ScopeStatus` says why, without the TUI's pointer over RPC (D3).
- The settings writer checks trust again inside its transaction (`settings_manager.py:1108-1114`).

### D6. Creating `.aelix/settings.json` over RPC: a dialog, never a boolean, and checked inside the write (this ADR)

ADR-0275 §8 refuses a client boolean as project consent, and accepts the TUI's own confirmation
sent as an RPC dialog. The same here:

- **The dialog.** The save would create the project file when `needs_new_project_file`
  (`core/resource_selection.py:539-545`) is true. With `ui/enable` on, the host then sends one `confirm` dialog
  (`extension_ui_request`, `method: "confirm"`).
  - Its title and message are the panel's sentences: "This creates .aelix/settings.json: the
    folder falls under project trust." and "next launch may ask; -p may need --approve". They
    are now the constants `CREATE_PROJECT_FILE_NOTE` and `CREATE_PROJECT_FILE_HINT`
    (`core/resource_selection.py:54-58`), which the panel's state line and hint and the RPC
    dialog read.
  - The dialog waits at most `CONSENT_TIMEOUT_SECONDS = 45.0` (`rpc/control_selection.py:55`).
  - Only `confirmed: true` saves. No, cancel, the 45 s bound or shutdown: "Not saved: creating
    <path> was not confirmed". A cancel: `Command cancelled` (D4).
- **With dialogs off:** refused: "Saving to this project creates <path>, which puts the folder
  under project trust; turn on dialogs (ui/enable) to confirm it, or create the file first".
- No param is ever read as consent.
- **Consent is checked inside the write (revision 1, Codex 6).**
  - `SettingsManager.edit_list` gains `may_create: bool = True`. Its builder, which runs after
    the lock (`settings_manager.py:1108-1119`), raises when the file does not exist now and
    `may_create` is false: "<the file's path> does not exist now; creating it puts this folder
    under project trust and needs a confirmation".
  - `ResourceSelection.save` passes `may_create=True` for the global scope, and for the project
    scope only when the caller says the creation was confirmed. The panel says so after its
    own confirmation; RPC after `confirmed: true`.
  - That closes the race in both paths. At `a1606f58`, a project file that existed when the
    panel checked and was deleted before the locked read was re-created with no question
    (`settings_manager.py:1101-1137` at `a1606f58`, `storage.py:342-358`).
  - The panel then shows "Not saved: …", and a second Enter asks
    (`test_the_panel_says_not_saved_and_the_next_enter_asks`,
    `tests/tui/test_selection_panel_624.py`). The panel passes `may_create_project_file=True`
    only on the Enter that answers its question (`tui/selection_panel.py:1344`).
- ADR-0275 §4's list of methods that open dialogs gains `selections/set`.

### D7. Idle: control-v1's rule, not the panel's pending-until-idle (lead's call, Q2)

Lead's call: in v1, `selections/set` keeps control-v1's idle rule. Mid-turn support and a
completion event can come later, as additions.

- **During a turn** `selections/set` is refused ("Runtime mutations require an idle session",
  `rpc/control.py:325-326`), like every other mutation; the client sets again at idle.
  aelix-acp's own guide already tells Studio "Mutations require idle sessions" (aelix-acp
  `docs/studio-integration.md:98`).
- **The TUI is different on purpose.** `/skill`, `/tool`, `/mcp` and `/agents` open while the
  model answers (ADR-0292; `MID_TURN_RUNS`, `tui/commands.py:2650-2678`), save at once, and
  apply when the turn ends (ADR-0279 D6).
- **A deferred apply can still happen over RPC:** when a turn an extension starts runs while the
  save's write runs off the loop, or when a harness build holds the `ApplyGate`.
  - The response then has `apply.deferred: true` and the labels it will turn on and off.
  - The deferred apply is the panel's, unchanged. In version 1 no event reports its end;
    `selections/list` shows the rows no longer `pending`.
  - **Last writer wins (revision 1, opus M8).** A deferred `tool` apply that lands after a later
    `tools/set_active` moves the session's active set by its delta
    (`core/tool_selection.py:855-`), over what `tools/set_active` set, and no event says so. A
    client that keeps its own list (aelix-acp, `session.py:317-325`) reconciles after
    `selections/list` shows the rows settled.
    `test_the_last_writer_wins_a_deferred_tool_apply_over_tools_set_active` drives this order,
    and `test_a_deferred_apply_answers_deferred_and_lands_when_the_gate_opens` the deferred
    answer (both `tests/rpc/test_selections_rpc_624.py`, in process). The idle refusal is the
    `selections.set.json` case "refused during a turn", on the `turn` rig.
- Both later options, a turn-safe `selections/set` and an end event, would be additions
  (ADR-0302 D3).

### D8. The extension kind applies on reload

- `ExtensionKind.effect` is `reload` (`core/extension_selection.py:895`). The save happens and
  nothing is applied (`apply: null`).
- Its rows stay `pending` until `resources/reload`. The client sends that itself: it is a
  lifecycle transition (ADR-0275 §2), not chained into `selections/set`.
- Under `--no-extensions`, every discovered extension row is locked off ("--no-extensions: none
  is loaded this run", `:1172-1178`). aelix-acp launches with `--no-extensions` (aelix-acp
  `session.py:169`), so over aelix-acp as it launches today, the extension kind can be listed
  but not changed. aelix-acp turns extensions on and off by restarting with `-e`
  (`runtime/extensions.py`).
- Owner, 2026-11-18: "aelix-ai는 바꾸지 않음 (권장)". As the lead's brief summarises it: Studio
  uses aelix-acp's `/extension enable|disable`, or aelix-acp decides about the flag. Nothing in
  aelix-ai changes for it.

### D9. The five kinds

| `kind` | Service and kind | What is written | `effect` |
|---|---|---|---|
| `skill` | `ResourceSelection` + `SkillKind` | `skills` in a settings file | `next_request` |
| `tool` | `ResourceSelection` + `ToolKind` (set grammar, ADR-0283) | `defaultTools` | `next_request` |
| `extension` | `ResourceSelection` + `ExtensionKind` | `extensions` | `reload` |
| `agent` | `ResourceSelection` + `AgentKind` (owner O3, "추가 (권장)") | `agents` | `next_request` |
| `mcp` | `McpSelection` + `McpKind` (D11) | `enabled` in the `mcp.json` a launch here reads | `restart` |

- **`tools/set_active` keeps its meaning:** the session's active set, refusing what the run keeps
  off (ADR-0283 D7).
  - The `tool` kind sits beside it: it saves `defaultTools` and applies it.
  - Applying it moves the session's active set as a delta (`core/tool_selection.py:855-`). A
    client that keeps its own list and re-sends it with `tools/set_active` reconciles the two
    itself; aelix-acp does, after a reload or a branch (`session.py:317-325`).
- **`settings/set`** with `skill_paths`, `prompt_template_paths` or `agent_profiles` stays what
  it is: a second, whole-list write of the GLOBAL file's list (ADR-0279 D4, ADR-0304 D12). It is
  version 1 (ADR-0302) and cannot be removed; the guides say which to use.

### D10. One set of objects for the TUI and RPC

- **Building the controls.** `cli/entry.py` builds the five controls in one module-level helper,
  `_selection_controls` (`cli/entry.py:6088-`, returning `SelectionControls`, `:6058-6086`),
  which the TUI branch (`:5145-5160`) and the RPC branch (`:5313-5328`) each call. A process
  runs one of them, so both modes get the same objects from the same code. The helper is what
  the TUI branch built inline at `a1606f58` (`cli/entry.py:5141-5229` there), moved.
  - `agent_control` stays the object built before the branch (ADR-0304 D14).
  - The calls sit inside branches that already exist.
  - `RpcControlPlane` takes `selections` (kind → control), and `run_rpc_mode` passes it
    through.
  - Under `--no-extensions` the extension kind lists the carriers discovery would find, locked
    off. As built, one cached closure reads that listing once: the TUI reads it at once, before
    it draws, so a manifest warning cannot land on its screen (`eager_listing=True`); RPC reads
    it at the first `extension` snapshot (`eager_listing=False`).
  - The control-v1 rigs `full`, `turn` and `child` build their controls through this same
    `_selection_controls` (`tests/rpc/control_v1/rigs.py`, `selection_controls`), so the
    fixtures and the parity test exercise the CLI's own wiring.
- **Shared pieces.** These move out of `tui/selection_panel.py` into
  `core/resource_selection.py`, so the panel and RPC cannot drift:
  - `edit_refusal(state)`: the Space refusal, plus D3's placeholder rule;
  - `scope_refusal(snapshot, scope)`: the ←/→ refusal;
  - `commit(selection, kind, scope, edits, *, may_create_project_file=False, on_deferred=None,
    on_apply_error=None)` (`core/resource_selection.py:905-`): save, then apply unless the
    effect is `reload` or `restart`. As built it takes a third keyword the draft did not have:
    `on_apply_error`, called when the apply raises, whose return is the apply result. The panel
    passes one that writes its own "⚠ <title>: saved, but not applied (…)." line and returns
    `None`; RPC passes none, so the answer carries `apply.warning` "saved, but not applied (…)"
    (D4). An exception from the save is a failed save in both.
  - `SCOPE_WORD` ("Global", "This project"), which the panel kept private as `_SCOPE_WORD`, is
    public beside them, for `scope_refusal`'s words.

  The no-op drop moves into `ResourceSelection.save` (D4), and the trust-pointer and
  create-the-file constants sit beside these (D3, D6). The panel calls them, and its words do
  not change: `tests/tui/test_selection_panel_624.py` and `tests/core/test_resource_selection_624.py`
  (`test_edit_refusal_is_the_panels_old_text_plus_the_placeholder_rule`,
  `test_scope_refusal_is_the_panels_arrow_text`) compare them with the panel's texts at
  `a1606f58`. One combination is the exception, and no row reached it at `a1606f58`: a row with
  no identity locked `always_on` is named "<label> is locked: …" where the old text said
  "Locked: …" (fix round 2, verify N5; D3).
- **Routing (lead's call).** New `rpc/control.py` code goes in new functions after the last
  cited line, and is routed through a table, because `RpcControlPlane._dispatch` is listed at
  27 and may not rise (ADR-0300).
  - `_families()` (`rpc/control.py:1033-1047`) maps a method's first segment to a bound
    handler: `{"auth": auth_action, "agents": agent_action, "selections": selection_action}`.
  - It replaces `_dispatch`'s two `startswith` branches. Each handler refuses when its service
    is absent (D1).
  - **It routes; it does not admit.** Whether a method is admitted, and under which lock, is
    decided before it, in `RpcControlPlane.handle` (`:270-`), by the method sets:
    `READ_ONLY_METHODS` (which gains `selections/list`), `MUTATING_METHODS` (which gains
    `selections/set`), `CANCELLABLE_METHODS`, `LIFECYCLE_METHODS` and `TURN_SAFE_METHODS`
    (`:90-132`); `selections/set` is in none of the last three. The table only routes what was
    admitted (its docstring says so, and ADR-0302 D9). The lead's note for B7 (2026-11-18):
    #612 adds its own lock-free admission path in `handle` for its child controls.
  - Measured with ruff 0.15.13 C901: `_dispatch` 27 → 26, written to `complexity-baseline.json`
    with `scripts/check_complexity.py --update` in this change; `capabilities` 6 → 7 and
    `cancel` 5 → 5 against a scratch copy of `a1606f58`'s `rpc/control.py`;
    `cli/entry.py::_async_main` 156 → 156, and `scripts/check_complexity.py --check` reported
    nothing else stale or raised. The new functions of `rpc/control_selection.py` score at most
    7 (`_validate_changes`).

### D11. MCP is a fifth kind on the wire, served through an adapter (this ADR, on this evidence)

MCP cannot be a fifth `ResourceKind` under `ResourceSelection`:

- The service reads a settings LIST (`manager.read_list(scope, key)`,
  `core/resource_selection.py:428`) and writes one through `SettingsManager.edit_list`
  (`:638-644`). An MCP server's state is a boolean inside `mcpServers.<name>` of another file
  (`cli/config.py:357-367`).
- The service's value is "project file, else global file, else on" (ADR-0279 D2; the project
  edit's inherited value is the global file's, `core/resource_selection.py:283`). `mcp.json` is
  one file whose project version REPLACES the global one (`resolve_mcp_config`,
  `cli/config.py:302-318`), so there is no inheritance to evaluate.
- The service's writability comes from the settings files' load errors
  (`core/resource_selection.py:450-462`). `mcp.json` has a third source, `$AELIX_MCP_CONFIG`,
  that is neither scope.
- The service's `Effect` was `next_request` or `reload` (`core/resource_selection.py:37` at
  `a1606f58`). An MCP server connects only at launch (ADR-0282 Limits).

So `core/mcp_selection.py` holds `McpKind` and `McpSelection`:
- `McpSelection` has `ResourceSelection`'s four public methods (`snapshot`,
  `needs_new_project_file`, `save`, `apply`).
- It returns the same `Snapshot`, `ItemState`, `SaveResult` and `ApplyResult`.
- The serializer, the panel and the parity test take it unchanged.
- `Effect` gains `restart`.

### D12. MCP: which file is written

Owner, 2026-11-17: "쓰기까지 지금 구현". Owner, 2026-11-18, on #644: "beta.3 뒤에 결정 (권장)". As
the lead's brief summarises it: write only the `mcp.json` in force; turning a global server off
in one project is refused with a pointer to #644, and becomes an addition later. #644 stays
open, and nothing here merges the files. This ADR's rule (the file-in-force rule is this ADR's,
not the owner's words):

- **The file in force.** The file written is the one a launch in this directory would read now:
  the launch's resolution, run fresh at each snapshot and again inside the write's lock (D13).
  It is the only file whose `enabled` the next start obeys here. At most one scope is
  writable: that file's.
- **No project file.**
  - The global file is written when it exists. With no file at all there is no row to change,
    and `global` says "<the global file> does not exist".
  - `project` is not writable: "aelix reads one mcp.json: a .aelix/mcp.json here would replace
    ~/.aelix/agent/mcp.json in this folder, so its other servers would stop here too; turning a
    global server off in one project waits for #644".
  - The writer never creates `.aelix/mcp.json` (ADR-0282 D2): one with a single entry would
    stop every global server in that folder.
- **A trusted project file.** That file is written. `global` is not writable: "aelix reads
  .aelix/mcp.json in this folder, not ~/.aelix/agent/mcp.json".
- **An untrusted project file.** Nothing is written, and no server is listed: the file is not
  used (ADR-0282 D5), and it is never opened (D13). One placeholder row says so, locked
  `untrusted`, and both scopes say why.
- **`$AELIX_MCP_CONFIG`.** Nothing is written. Its rows are listed, locked with kind `cli` and
  tag `$AELIX_MCP_CONFIG`: "$AELIX_MCP_CONFIG chooses the file this run; edit <path> by hand".
  The variable is the user's explicit pointer to a file outside both scopes. aelix-acp does not
  set it (no hit for `AELIX_MCP_CONFIG` in aelix-acp `16d6555b`).
- **A name a delegating parent keeps off (as built).** In a delegated aelix, a server whose name
  the parent passed in `$AELIX_MCP_PARENT_NAMES` as off (ADR-0282 D9) is locked for this run,
  kind `cli`, tag `inherited`: "$AELIX_MCP_PARENT_NAMES (the names a delegating aelix passes
  on) keeps it off this run" (`test_a_name_the_parent_keeps_off_is_locked_for_this_run`,
  `tests/core/test_mcp_selection_624.py`). The draft did not list this row.
- **A file in force that did not load** (no entry could be read). Its scope is not writable:
  "<file> did not load: <the loader's warning without the path>", and the warning is in
  `unreadable` and `problems` (`test_an_unreadable_file_is_named_and_nothing_is_writable`, a
  file that is not UTF-8). A `selections/set` naming one of its servers says the same (D4 item
  3, fix round 1).
- **A global server off in one project only** is not possible under the one-file rule. Both
  surfaces say so with the text above. If #644 adds project overrides, `project` becomes
  writable in that case, an addition under ADR-0302 D3.

### D13. MCP: trust, and what is re-checked inside the lock (revision 1)

- **Writing a project file.** A project `mcp.json` is written only when the folder is trusted at
  launch AND the settings manager's live flag still says so. That is the predicate
  `ResourceSelection` uses (`core/resource_selection.py:398-407`), so a `/trust` revocation is
  honoured. The global file needs no trust.
- **An untrusted project's `mcp.json` is never opened (opus M4).**
  - `McpSelection` does not call `load_mcp_config` there, because the loader parses a project
    file whatever the trust (`cli/config.py:284-297` at `a1606f58`).
  - It resolves the file in force with a new `resolve_mcp_config(cwd) -> (source, path)`
    (`cli/config.py:302-318`), which `load_mcp_config` now also uses (`:298-299`), so the two
    cannot drift. That resolver stats and never opens: `is_file()` (`:316`), as the launch did.
  - `test_an_untrusted_project_file_is_never_opened_and_lists_no_server`
    (`tests/core/test_mcp_selection_624.py`) makes every open of that file fail and lists
    nothing from it; over RPC,
    `test_an_untrusted_folder_lists_its_per_kind_rows_without_the_trust_pointer` checks that
    nothing under `.aelix/` is opened.
  - It parses the file only when the source is `env` or `global`, or `project` and trusted.
  - An untrusted project file's warnings never reach a row, `problems` or the panel.
- **Inside the lock (Codex 3, opus M3).** The write runs in a worker thread
  (`asyncio.to_thread`), so the lock wait never blocks the event loop. Since fix round 1 the
  save's own reads of `mcp.json`, before and after the write, run in a worker thread too
  (Limits). After the lock is taken, and before anything is read for the edit, it calls an
  `authorize` callback that the selection passes in. The callback:
  - runs `resolve_mcp_config(cwd)` again, and refuses unless (source, path) is the one the
    save's snapshot read: "the file aelix reads here changed (now <label>); nothing written;
    reopen /mcp" ("list again" over RPC, D3). That is opus M3's case: a checkout brings a
    `.aelix/mcp.json` under an open panel that targets the global file;
  - refuses the same path resolving to another real file than that snapshot's, and a lock taken
    on another real file: "<file> now resolves to another file; nothing written; try again"
    (fix round 1, below). Until fix round 2 a link retargeted after the worker resolved it and
    before the lock got the first text, naming the same path as "now"; it gets this one since
    (verify r2, N6:
    `test_a_retarget_after_the_writer_resolves_says_the_file_resolves_elsewhere`, which failed
    at `3d4e7364`);
  - re-checks the trust predicate for a project file: "this folder is not trusted (/trust to
    decide); nothing written" (over RPC without the pointer, D3);
  - is followed by a check that the target still resolves to the real path it locked (Codex 8,
    symlink retargeting): "<file> now resolves to another file; nothing written; try again".
- **The real path the save read (fix round 1, Codex CX-3).** At `beadfac4` the snapshot kept
  only the lexical path, and both checks compared the worker's own resolution: a symlinked
  `mcp.json` retargeted from A to B after `save` read A and before the worker resolved it had
  B edited. The save now keeps the real path its snapshot read (`_InForce.real`) and passes it
  to the writer (`write_enabled(expected_real=)`), which refuses a target that resolves
  elsewhere before it takes any lock, with the "now resolves to another file" words; inside the
  lock the callback compares the file in force and the locked path with it. Held by
  `test_a_retarget_before_the_writer_starts_is_refused` and
  `test_the_authorizer_refuses_a_lock_taken_on_another_real_file`
  (`tests/core/test_mcp_selection_624.py`).

  A cancel is honoured while waiting for the lock, after it, and again just before the
  replace; a cancelled save answers "the save was cancelled; nothing written". Held by
  `tests/core/test_mcp_selection_624.py` (`…_refused_inside_the_lock`, three cases;
  `test_cancelling_the_save_sets_the_writers_abandoned_event`) and
  `tests/core/test_mcp_write_624.py` (`test_trust_revoked_during_the_lock_wait_is_refused`,
  `test_symlink_retargeted_during_the_wait_is_refused`, the three cancel cases), on macOS and in
  Linux docker.

### D14. MCP: what the writer changes and what it keeps (revision 1)

`core/mcp_config_edit.py`:
- `edit_enabled(data: bytes, changes: Sequence[tuple[str, bool]]) -> bytes`, pure;
- `write_enabled(target, changes, *, authorize, abandoned, expected_real=None) ->
  McpWriteOutcome`, the locked I/O, run in a worker thread (`expected_real` since fix round 1,
  D13).

**Only `enabled`, at the byte level.**
- **Off:** `true` becomes `false`; with no key, the member `"enabled": false` is inserted after
  the entry's last member.
- **On:** `false` becomes `true`. This ADR keeps the key where pi deletes it (pi
  `config.ts:174-176`): the edit stays one token and reversible, and ADR-0282's own hint
  already says "set it to true, or delete the key".
- **Every other byte is kept**: other keys and servers, their order, whitespace, escapes,
  number spellings, line endings and the trailing newline. pi re-lays the whole file; here
  nothing outside the `enabled` member moves.

**The inserted member's style (opus H1).** The style is copied:
- the separator is copied from the last member when it is `[ \t]*:[ \t]*`, else `": "`;
- when the last member STARTS its line (only `[ \t]*` before its key), the indent is that
  whitespace, and the newline is the one that ends the previous line (`\r\n` stays `\r\n`);
- otherwise the inline style is used: the gap between the last two members when it is
  `,[ \t]*`, else `", "`; for a single member, `","` or `", "` after the brace's spacing.

Round 0 took the indent as everything on the line before the last key. That duplicated a member
when two members shared a line (opus H1's reproducer).

**Four checks, and any failure refuses and writes nothing.** "Edit the file by hand" ends every
text.
1. **Strict input.** The bytes decode as UTF-8 with no BOM. They parse with an
   `object_pairs_hook` that refuses a duplicate key ANYWHERE and a `parse_constant` that
   refuses `NaN`/`Infinity`. Every `ValueError` and `RecursionError` from parsing or from the
   span scanner becomes a refusal, never an exception.
2. **Shape.** `mcpServers` is an object, the server is in it as a non-empty object, and an
   existing `enabled` is the literal `true` or `false`.
3. **The byte guard.** The new text equals the old text with EXACTLY the recorded splices at
   the recorded offsets, at most one per server, non-overlapping, and every other byte
   identical. Each splice is either a `true`/`false` literal replaced by the other, or an
   insertion that matches `^,(\r?\n[ \t]*|[ \t]*)"enabled"[ \t]*:[ \t]*false$`. An insertion
   must sit right after a value, with only whitespace before an object's closing brace.
4. **The value guard.** The new text parses with the same duplicate-refusing hook, and its value
   equals the old value with only the asked `enabled` members set. The comparison is type by
   type (`True` is not `1`, `1.0` is not `1`) and includes key order.

So a splice bug can refuse; it cannot write a wrong file.

Round 0's guard was parsed-value equality alone. Codex 2 reproduced it accepting `true → 1`
elsewhere and `1.50 → 1.5`. Opus H1 reproduced it accepting a duplicated member. Both were
accepted because `True == 1` and `json.loads` keeps the last of two keys.

**Measured on the shipped module** (`core/mcp_config_edit.py`, sha256 `d721bbfa…`, the writer
sub-lane's runs on 2026-11-18; `.omc/probes/623-624/impl/writer/run_real_corpus.out`). The
revision-1 prototype (`.omc/probes/623-624/rev1/`) gave the same counts. Fix round 1 changed
only the write half (the identity, the checks before each replace attempt, the Windows
descriptors); `edit_enabled` and its four checks are as measured here.
- **Hand corpus.** 32 hand-formatted files, each toggled off and then on: 64 edits.
  - They include opus H1's three shapes (two members on one line, `"command": "x", "args": []`
    on one line, a member after a closing brace), an object opening on its key line,
    Prettier-style inline objects, a separator with newlines, mixed newlines, an escaped server
    name and secrets in every value.
  - Every one was edited with only the `enabled` bytes changed, or refused where it should be.
    The 15 refusal shapes are a duplicate `enabled`, server, `env` key or `mcpServers`; a
    non-boolean `enabled`; `NaN`; invalid UTF-8; a BOM; a trailing comma; a comment;
    100,000-deep nesting; an empty server; a server that is not an object; no `mcpServers`;
    and a top-level array.
  - No refusal text contained `SECRET-624`. Result: 64 edits, 0 unexpected.
- **Generators.**
  - seed 624: 200 `json.dumps` files;
  - seed 6240: 2,000 files whose every whitespace gap between tokens was re-chosen at random
    (removed, a space, a newline with a random indent, inlined), with 1 to all servers changed
    per file;
  - seed 6242: 2,000 mixed.

  Two independent oracles judged every file: the value oracle, with duplicate refusal and
  type-by-type comparison; and an inverse-edit oracle (deleting exactly the needed insertions
  and flipping exactly the needed literals gives back the old tokens and whitespace). Result:
  of 4,200 files, 2,875 edited and 1,325 unchanged; none refused and no oracle failure printed.
- **Guard mutants** over 306 inputs (seed 6241 and 6 by hand), each a splice bug planted in the
  edit: every input a mutant reached was refused, and none was written wrong.

  | Mutant | Reached and refused | Written wrong | Round-0 guard, written wrong (design) |
  |---|---|---|---|
  | round 0's whole-line indent | 61 | 0 | 16 |
  | another `true` → `1` | 142 | 0 | 239 |
  | `1.50` → `1.5` | 55 | 0 | 92 |
  | insertion offset + 1 | 72 | 0 | 53 |
  | `true` written for `false` | 128 | 0 | 0 |
  | a member inserted twice | 72 | 0 | 72 |
  | trailing newline dropped | 153 | 0 | 250 |

- **The loader agrees with the edits**: `tests/core/test_mcp_write_624.py`
  (`test_the_loader_agrees_with_each_edit`, `test_the_loader_agrees_on_re_laid_out_files`)
  reads edited files back through the loader with each asked server on or off as asked. The
  design measured 500 re-laid-out files against the `a1606f58` loader, 500 / 0.
- **Mutants of the module** (the aelix-tests-that-bite `scripts/mutants.py`): 58 of 62 killed on
  macOS, and the 4 survivors killed after tests were added (62 of 62); 8 of 9 Linux-only mutants
  killed in docker, and the survivor killed after a test was added (9 of 9)
  (`writer/mutants.out`, `mutants_survivors.out`, `mutants_linux.out`,
  `mutants_linux_survivors.out`). Fix round 1's write-half mutants are in "How it is held".

**The write (revision 1: Codex 7, Codex 8, opus M3, opus L4).**
- **The lock domain is the file's directory, not the agent dir.**
  - **POSIX:** `flock` on an `O_RDONLY` descriptor of the REAL target's directory, polled to
    ADR-0279 D4's 30 s deadline. Every aelix that edits one `mcp.json` takes the same lock,
    whatever its agent dir, and nothing is created in the folder.
  - Round 0 used `<agent dir>/locks/…`. Measured on the shipped module, three rounds each on
    macOS (Python 3.12.13), Linux docker as root and as uid 1000: two processes with two agent
    dirs and 20 offs each lost 0 of 40 under the directory lock, and 19 or 20 of 40 with round
    0's agent-dir lock patched in (`writer/race_bite.macos.out`, `docker_root.out`,
    `docker_1000.out`). `test_two_processes_with_two_agent_dirs_lose_no_update` keeps the
    directory-lock half.
  - **Windows:** a named mutex, `Local\aelix-mcp-<sha256 of the normcased real path, 32 hex>`
    (ctypes `CreateMutexW`, polled `WaitForSingleObject` to the same deadline, released in the
    same worker thread). It is shared by every process in the logon session and creates no
    file. Designed, first measured in CI: `test_two_processes_with_two_agent_dirs_lose_no_update`
    is not skipped on Windows, so `windows-latest` is its first real run there; no Windows run
    is recorded here.
- **Identity.** The target is resolved once, and the directory locked is the real target's.
  After the lock, the target must still resolve to that real path, and D13's `authorize`
  passes. The read is BYTES (`os.read`, never `read_text`, whose universal newlines would turn
  CRLF into LF; opus M3, measured). Just before the replace, the file must still be the one
  that was read, so a non-aelix editor's write made before that check is refused, not lost; one
  made between the check and the rename is not (fix round 1 narrowed round 0's claim; Limits). As built,
  "the one that was read" is the same identity (device, inode, size and mtime) AND the same
  bytes, read again, because a coarse mtime can hide a write (`_check_unchanged`):
  "<file> changed while it was being saved; nothing written; try again"
  (`test_a_change_after_the_read_is_refused_and_kept`,
  `test_a_change_after_the_temp_is_written_is_refused`).
  - **Fix round 1 (Codex CX-1, CX-4).** The identity is now device, inode, size, mtime, the whole
    `st_mode`, owner, group and ctime (`_identity`), and the check runs before EVERY replace
    attempt, each Windows retry included (`_check_before_replace`, called from `_replace`).
    At `beadfac4` a `chmod 600` made after the read left content and mtime alone, so the save
    went on and put the `0644` it had read back on (`McpWriteOutcome(status='written')`, mode
    `0o644` after; at this change `refused`, mode `0o600`:
    `.omc/probes/623-624/fix-r1/lead/probes/chmod_undo_probe.*.out`, macOS); and a Windows retry after a sharing
    violation installed the prepared temp over an editor's write that came in meanwhile.
    `test_the_identity_holds_each_field` (each of the four new fields alone),
    `test_each_replace_retry_rechecks_the_file_first` and
    `test_a_retry_after_a_sharing_violation_still_writes` (the retry path forced on, macOS)
    hold it (`tests/core/test_mcp_write_624.py`).
- **The replacement keeps who may read the file as it was read, or nothing is written (Codex 7;
  narrowed in fix round 1).** Round 0 said "who may read it afterwards is unchanged". That was
  wider than the code: a permission change another program made between the read and the
  replace was undone (Codex CX-1). Now, on POSIX, a chmod, chown, ACL or xattr change made after
  the read moves ctime, and the check before the replace refuses the save ("<file> changed while
  it was being saved; nothing written; try again") and leaves the file as that program left it:
  `test_a_chmod_after_the_read_is_refused_and_not_undone`,
  `test_a_chmod_to_the_same_mode_after_the_read_is_refused` (only ctime moves),
  `test_a_group_change_after_the_read_is_refused_and_not_undone`,
  `test_macos_an_acl_added_after_the_read_is_refused_and_kept` and
  `test_macos_an_xattr_added_after_the_read_is_refused_and_kept` (macOS),
  `test_linux_an_xattr_added_after_the_read_is_refused_and_kept` (Linux docker). A save with no
  such change does not move the original's ctime: the read and the carry only read it
  (`.omc/probes/623-624/fix-r1/M/probes/ctime_probe.out`, macOS). What is left is the moment
  between the last check and the rename (Limits). The temp file is `mkstemp` in the same
  directory. Before it is exposed by the replace:
  - **POSIX:** `fchown(tmp, uid, gid)` of the original, with a failure refused; then the
    original's exact mode bits.
  - **Linux:** every xattr, the POSIX ACL (`system.posix_acl_access`) included, copied by
    `listxattr`/`getxattr`/`setxattr`, with a failure refused
    (`test_linux_posix_acl_and_user_xattr_are_kept`,
    `test_linux_an_xattr_that_cannot_be_kept_is_refused`; Linux docker, root and uid 1000).
  - **macOS:** the ACL and the xattrs, copied by `fcopyfile(COPYFILE_ACL | COPYFILE_XATTR)`,
    with a non-zero return refused (`test_macos_acl_is_kept`,
    `test_macos_acl_that_cannot_be_kept_is_refused`). A `deny delete` ACL makes any replacement
    impossible: the replace's `PermissionError` is refused ("<file> cannot be replaced: its
    access rules or another program prevent it; edit it by hand"), the temp's ACL is cleared
    and the temp removed (`test_macos_deny_delete_acl_is_refused_and_the_temp_removed`). The
    three macOS cases passed on macOS with Python 3.12 at `16c0e779`, and the writer's two test
    files on 3.11, 3.12 and 3.14 (`writer/macos_pythons.out`: 204 passed, 8 skipped each).
  - **Windows:** the temp, created in the same directory, inherits that directory's inheritable
    ACEs and gets this process's owner, as a file created there by any editor does. Up front, the
    writer refuses a file whose DACL is protected (inheritance off) or holds an explicit ACE:
    "mcp.json has its own access rules; edit it by hand". It checks with ctypes
    `GetFileSecurityW`: `SE_DACL_PROTECTED`, and each ACE's `INHERITED_ACE` flag. As built it
    also refuses a DACL that is absent or empty (with the same words), and one it cannot read
    ("mcp.json's access rules cannot be read; edit it by hand").
  - **Windows, the replacement compared (fix round 1, Codex CX-2).** "Inherited only" does not
    prove the temp inherits the SAME ACEs (a restored file, or ACEs changed through an API, can
    differ from what the parent gives now), nor that it gets the same owner. So before each
    replace attempt the writer reads the owner, the group and the DACL of the original and of the
    temp (`GetNamedSecurityInfoW` and `ConvertSecurityDescriptorToStringSecurityDescriptorW`
    with `OWNER | GROUP | DACL`, both buffers freed with `LocalFree`) and writes only when the two
    SDDL strings are equal; a difference is refused "mcp.json has its own access rules; edit it
    by hand", and a descriptor it cannot read "mcp.json's access rules cannot be read; edit it by
    hand" (`_check_access_kept`, switched on by `_COMPARE_DESCRIPTORS` on Windows). The
    comparison is tested on every platform with the reader stubbed
    (`test_a_replacement_with_another_owner_group_or_dacl_is_refused`,
    `test_a_replacement_with_the_same_owner_group_and_dacl_is_written`,
    `test_a_descriptor_that_cannot_be_read_is_refused`). The real reader and the rest of the
    Windows rules are designed and first measured in CI: `test_windows_the_replacement_keeps_owner_group_and_dacl`,
    `test_windows_explicit_ace_is_refused` and
    `test_windows_inherited_only_file_is_written_with_the_same_acl` (with `icacls`) are skipped
    off Windows, so `windows-latest` is their first real run, and no Windows run is recorded
    in this ADR.
- **Refused up front, nothing written (opus L4):**
  - not a regular file;
  - another hard link (`st_nlink > 1`; a replacement would split them);
  - the owner's write bit off, or this process may not write it: "<file> is read-only; edit it
    by hand". The owner bit: `test_read_only_file_is_refused` and
    `test_owner_write_bit_off_is_refused_even_when_writable` (which passed as root in Linux
    docker). This process may not write it (fix round 1, verify F2; round 0 cited the owner-bit
    test for this half): `test_a_file_this_process_may_not_write_is_refused_whatever_its_mode_bits`
    (`os.access` stubbed, every platform) and
    `test_macos_an_acl_that_denies_this_user_write_is_refused` (a real `user:<me> deny write`
    ACL with the owner's write bit on, macOS);
  - on Windows, the read-only attribute, refused before the slow replace retry. Designed, first
    measured in CI (`test_windows_read_only_attribute_is_refused_at_once`, Windows only).
- **The rest:** the write-all loop, then fsync, a cancel check, `os.replace`, and the Windows
  replace retry, as `aelix_ai/settings/storage.py:423-441` does it, with the identity, bytes and
  (Windows) descriptor checks before every attempt (above). A symlinked file is written
  through, and the link is kept (`test_a_symlink_is_written_through_and_kept`). CRLF is kept
  byte for byte (`test_crlf_is_kept_byte_for_byte`). The lock wait runs off the event loop:
  with the lock held 1 s by another process, the loop's largest tick gap was 30 ms in Linux
  docker (root, 38 ticks; uid 1000, 40 ticks) (`test_a_held_lock_keeps_the_event_loop_ticking`;
  over RPC, `test_the_loop_keeps_answering_while_another_process_holds_the_mcp_lock`).
- **Where the code lives.** All of this is new code in `core/mcp_config_edit.py`; nothing was
  added to `aelix_ai/settings/storage.py`. The settings writer itself is not changed by D14
  (D6 changes `edit_list` for consent).

**Nothing printed (revision 1, Codex 1).**
- **The writer's refusals** name the file, the server and a key, never a value.
- **The loader's warnings.** Round 0 claimed the loader's warnings quote no value. That was false
  at `a1606f58`. `"enabled": "SECRET-624"` gives `… "enabled" must be true or false, not
  "SECRET-624"; not started`, and a list value is quoted too
  (`.omc/probes/623-624/rev1/loader_secret_probe.out`).
  - The warning now names the value's JSON type: `"enabled" must be true or false, not a
    string; not started` (or `a number`, `null`, `a list`, `an object`;
    `test_an_invalid_enabled_is_named_by_its_type`). `SECRET-624` planted in `enabled` (a
    string, a list), `env`, `args`, a url's query and path, `headers`, `transport` and
    `command` reaches neither the launch's stderr nor `aelix status` nor its `--json`
    (`test_no_secret_reaches_the_launch_status_or_its_json`, a real `_async_main` with the MCP
    manager replaced by a spy).
  - `_shown` goes. The same probe found no other warning that quotes a value, over malformed
    `args`, `env`, `transport`, `command`, `url`, a non-object entry, a missing command and
    `disabled`.
  - This is the one change to the loader's words. ADR-0282 D1's example text gets a dated note.
- **Two loader crashes become warnings (opus L4).** At `a1606f58` the loader caught only
  `OSError` and `JSONDecodeError` (`cli/config.py:319-322` there). Measured at `a1606f58`:
  invalid UTF-8 raises `UnicodeDecodeError`, and 100,000-deep nesting raises `RecursionError`
  on macOS, out of `_read_mcp_config`. Both crashed a launch, and would have crashed every
  snapshot. It now catches `ValueError` and `RecursionError` too (`cli/config.py:342`), with a
  value-free warning: "<path>: not valid JSON (<the exception's class>)", never the bytes
  (`tests/cli/test_mcp_loader_624.py`). A JSON syntax error says where, as two numbers from
  `JSONDecodeError.lineno` and `.colno`: "<path>: not valid JSON at line N column M
  (JSONDecodeError)" (fix round 1, the lead's call; round 0 had dropped the line and column
  with the decoder's message). `test_a_json_error_gives_its_line_and_column_and_no_value` puts
  `SECRET-624` at the error's line and column, and
  `test_no_secret_from_a_json_syntax_error_reaches_the_launch_or_status` checks the launch's
  stderr, `aelix status` and its `--json`. Which class deep nesting raises depends on the
  platform (`RecursionError` on macOS, `JSONDecodeError` on Linux with Python 3.14), so the test
  pins the shape, not the class.
- **What rows show.** The mcp rows, on the wire and in the panel, carry the server name, the
  transport word, the state and the file label: no endpoint, command, argument, environment,
  header or url. `/mcp list` keeps showing `display_endpoint` (ADR-0282's corrections), as
  today.

### D15. MCP: when a change takes effect

- **`effect: "restart"`.** MCP connects at launch, and `/reload` does not reconnect (ADR-0282
  Limits).
  - `saved` is what the file says now, and `applied` is what this launch started. A row whose
    two differ is `pending` until the next start.
  - Nothing is applied in place: the server is not disconnected or its tools hidden now. pi
    disconnects at once (pi `index.ts:741-758`); here that would be a live MCP lifecycle aelix
    does not have (ADR-0282 Limits), so this ADR does not add one.
- **The panel's words for `restart` (revision 1, opus M1).**
  - The status column says `off · restart` or `on · restart`.
  - The state line of a pending row says "· the next aelix start applies it". Round 0's
    `_state_line` (`tui/selection_panel.py:758-763` at `a1606f58`) would have said "Enter
    applies the saved value", which is wrong for `restart`. As built, the words come from two
    tables beside `_SCOPE_WORD`, `_PENDING_WORD` and `_PENDING_CLAUSE`
    (`tui/selection_panel.py:78-86`).
  - An Enter whose edits the file already holds, so the save writes nothing, says "MCP servers:
    mcp.json already said this", not "the settings files already said this" (`:1018` at
    `a1606f58`). `summary` takes the kind's file word as a keyword with the settings default;
    `McpKind.files_word` is `mcp.json`. An Enter with no edit at all says "MCP servers: nothing
    to save", as every panel's does (live check, `.omc/probes/623-624/impl/live/`, t10 and t11;
    the contract's §4.1 had this case wrong).
  - Held by `tests/tui/test_mcp_panel_624.py`
    (`test_a_save_says_the_next_start_and_leaves_the_row_pending`: after a save the reopened
    row says `off · restart` and "Saved off · now on · the next aelix start applies it";
    `test_an_enter_that_writes_nothing_says_mcp_json_already_said_this`;
    `test_an_enter_with_no_edit_says_nothing_to_save_and_writes_nothing`, added in fix round 1
    because the guide had the two cases mixed up, verify F1) and
    `tests/tui/test_selection_panel_624.py`.
  - Measured with ruff 0.15.13 on a scratch copy with these three changes: `status` 11 → 11,
    `_state_line` 15 → 15, `summary` 19 → 19, `save` 10 → 10. C901 does not count conditional
    expressions or dictionary lookups.
- **Live state.** `live` / `liveNote` carry the session's state words
  (`core/mcp_servers.py:32-48`). A server started but not connected is `live: false`, with
  `["failed", …]` or `["disconnected", …]`.
- **Delegated agents.** A delegated agent started later reads its own file (ADR-0282 D9). A
  server turned off in the file the child also reads is off for it. One turned on stays off for
  children of this process while the launch's name record says off: the record only
  suppresses, and a toggle does not rewrite it.

### D16. MCP in the TUI: `/mcp` opens a panel

- **The panel.** A bare `/mcp` opens ADR-0279 D11's panel (layout A) over `McpSelection`,
  titled "MCP servers", noun "server":
  - a checkbox per server;
  - the status column, with the state words and D15's `off · restart`;
  - the "Save to" control fixed on the one writable file; ←/→ to the other says D12's reason;
  - Enter saves and applies nothing, and the summary names the file and says "the next aelix
    start applies it; /reload does not reconnect MCP servers".
- **`/mcp list`** keeps today's table, endpoints and tool counts included (`tui/mcp_viewer.py`),
  as `/agents list` (ADR-0304 D11) and `/jobs list` keep theirs.
- **Rows:**
  - the file's entries (items);
  - an entry the loader skips: a placeholder, locked `unavailable` with tag `skipped` (opus
    L3), its `problem` the loader's warning, now value-free (D14);
  - a server an extension declares: an `info` row, "declared by <plugin> · /extension turns the
    plugin on and off";
  - an untrusted project file: one placeholder, locked `untrusted` (D12), with no warning from
    the file (D13).

  ACP's client servers are not rows: they are `mcp/status`'s (ADR-0275 §7).
- **`/extension`'s MCP rows stay status-only** (ADR-0284 D8). Making them toggleable there would
  put two files behind one Enter, and one save writes one file (ADR-0279 C20). Their existing
  text, "/mcp manages them", is now true.
  - **Their `saved` and `applied` (fix round 1, verify live-RPC 5).** They say alike whether this
    launch started the server (`connected`, `failed` or `disconnected`: yes; `disabled` or not
    started: no), as the `mcp` kind's status rows do. At `beadfac4` they said `saved: true,
    applied: false` beside the tag `connected`. `ExtensionKind.applied` now includes a started
    server's `mcp:<name>` row, and `ResourceSelection.snapshot` gives every `info` row `saved`
    equal to `applied` and no `decidedBy`, since a status row is saved in no settings file
    (`test_the_extension_kinds_mcp_rows_say_saved_and_applied_alike`,
    `tests/rpc/test_selections_rows_624.py`, the four states over RPC).
- `/mcp` already runs mid-turn (ADR-0292); the panel writes `mcp.json` only.
- **As built.** Anything after `/mcp` other than `list` answers "Usage: /mcp [list]". A launch
  with no MCP control (headless tests) keeps the table on a bare `/mcp`. The command's
  description is "Turn MCP servers on or off (/mcp list: status, tool counts)". The panel's
  summary after a save is "✓ MCP servers saved to <file>", the changed rows, and "the next aelix
  start applies it; /reload does not reconnect MCP servers" (`tui/mcp_panel.py`
  `EFFECT_NOTE`). Held by `tests/tui/test_mcp_panel_624.py` (21 test functions, 32 cases,
  including `test_the_arrows_refuse_with_the_one_file_words`,
  `test_a_trusted_project_file_refuses_the_global_arrow` and
  `test_both_arrows_refuse_where_nothing_is_writable` for `$AELIX_MCP_CONFIG` and an untrusted
  folder).
- **The panel's words (fix round 1: impl F1, verify N5 and the live captures).**
  - **A long reason is shown whole.** At `beadfac4` the ←/→ refusal was one row, so "…waits for
    #644" never showed at 80 or 120 columns, and the `$AELIX_MCP_CONFIG` lock line cut its path
    with "…" at 120. Now a refused Space or ←/→ wraps over the detail rows in the warning's
    yellow, as a refused save already did in red, and a locked row's state line wraps upward
    into the description rows. When the panel opens, `plan_layout` gives the description rows
    as many rows as the longest ←/→ refusal, Space refusal or lock line needs at that width,
    keeping three list rows (`_LIST_KEPT`). This holds for every selection panel. A refused
    save's text is not known at open, and wraps into the rows planned. Held by
    `test_the_arrow_refusal_is_drawn_whole_at_80_and_120_columns` and
    `test_the_aelix_mcp_config_locked_line_is_drawn_whole` (`tests/tui/test_mcp_panel_624.py`),
    four tests in `tests/tui/test_selection_panel_624.py` (any kind's long lock line, a load
    error after the lock, the three list rows kept, a short lock line keeping the description),
    and the live captures at 80x24 and 120x40 in `.omc/probes/623-624/fix-r1/`.
  - **What the reserve costs (fix round 2, verify r2 F2). Keeping it is the lead's call.** The
    rows are reserved at open for the longest reason the panel could show, whether it is ever
    shown or not, so a panel with a long lock line or ←/→ reason opens with fewer list rows.
    The reserve grows with the length of the paths inside those reasons; the verify pass's
    fixture paths are about 60 characters. Its captures
    (`.omc/probes/623-624/verify-r2/live-tui/`):
    - at 80x24, `/tool` shows 5 list rows above three blank description rows, where
      `beadfac4` and `a1606f58` show 7 (`cap8-{head,prior,main}-80x24/out/p2-tool.screen.txt`);
    - at 80x24, `/mcp` opens scrolled at `(1/5)`, its EXTENSIONS group off-screen and one
      list row blank, where `beadfac4` showed every row (`cap9-global-80x24/run/g0-open.screen.txt`);
    - at 120x40, `/tool`, `/extension` and `/agents` open one blank row taller; `/skill` is
      unchanged (`cap8-{head,prior}-120x40/out/`).

    The lead keeps the behaviour: it is what draws `/mcp`'s refusals whole (impl F1, above).
  - **A resize does not re-plan (verify r2 N4).** The wrap is planned at open, at that width.
    A terminal made smaller while the panel is open does not re-wrap, so the reason is cut
    again until the panel is reopened: 120x40 shrunk to 80x24 with `/mcp` open ends the →
    reason "…turning a global …" (`cap9-global-120x40/run/g14-resized-right.screen.txt`).
  - **Save to marks a scope only when Enter can write it.** Under `$AELIX_MCP_CONFIG` and in an
    untrusted project, `[Global]` was marked beside "Cannot save to Global"; now neither scope
    is (`test_the_save_to_control_marks_a_scope_only_when_one_is_writable`, four cases).
  - **The count.** `/mcp` counts an extension's server as a server, as `/mcp list` does, so an
    untrusted folder that lists one says "1 server", not "0 servers"; placeholders are not
    counted, and `/extension`'s count is unchanged (`SelectionModel.count_info`;
    `test_an_untrusted_folder_counts_the_extension_servers_it_lists`,
    `test_the_server_count_takes_the_extension_rows_as_mcp_list_does`).
  - **An entry the loader skips says `skipped`**, its lock tag, in red, not `failed`: it never
    started (`test_an_entry_that_never_started_says_skipped_not_failed`).
  - **Kept: `off`, not `disabled`, for a saved-off row after a restart.** Every selection panel's
    column says `off`, and the row's state line says "Saved off · now off"; `disabled` is the
    word of the read-only tables (`/mcp list`, `/extension`, `aelix status`). Changing `/mcp`'s
    column alone would make it disagree with its own state line (fix round 1's call, not the
    owner's).

### D17. `aelix status`

- Unchanged, except D14's warning words.
- It reads the file without starting anything (`config_statuses`), so a toggle shows at once as
  `enabled` or `disabled`.
- Its JSON gains nothing.
- Its hint, "Set it to true, or delete the key, and the next aelix start connects it."
  (`core/mcp_servers.py:211-212`), stays true.

### D18. `mcp/status` and `resources/list.mcp` keep their meaning

They list the ACP client's own servers (ADR-0282 D6, ADR-0275 §7), and ADR-0302 freezes that.
The `mcp` selection kind is the `mcp.json` view; the two are different lists by design.

## Consequences

- Studio can list and change what is on for skills, tools, agents and `mcp.json` servers, with
  the TUI's rules and words, and see locks and pending rows.
- An `mcp.json` server is turned on and off without hand-editing. The file keeps every other
  byte, its owner, group, mode, ACL and xattrs as they were read, or nothing is written; a
  metadata change another program makes during the save, before the writer's last check of
  the file, is refused, not undone (D14); one made between that check and the rename is
  undone (Limits).
- The TUI and RPC share the refusal texts, the save-then-apply sequence and the no-op drop.
  The exceptions are the TUI's `/trust` pointer and "reopen /mcp", which RPC sends as "this
  folder is not trusted" and "list again" (D3).
- A project settings file is created only with consent, checked inside the write, in the panel
  too.

## Limits

- No per-project MCP toggle until #644 (D12; the owner decides it after beta.3).
- Over aelix-acp's `--no-extensions` launch, the extension kind is read-only (D8).
- No event reports a deferred apply's end, and a deferred `tool` apply can land over a later
  `tools/set_active` (D7).
- A project `mcp.json` created after this launch is the one the next start reads. The panel and
  `selections/list` show it, but `applied` is still this launch's.
- **Windows:**
  - the DACL rule (refuse a protected, absent, empty or explicit DACL), the owner, group and
    DACL comparison of the replacement (fix round 1), the read-only attribute, the named-mutex
    lock and the checks before each replace retry are designed, and first measured in CI: their
    tests run only on Windows, or with the Windows path forced on or stubbed elsewhere (the
    two-process lock test runs everywhere), and no Windows run is recorded in this ADR;
  - Windows' `st_ctime` is the creation time, so there the identity sees a permission change
    only through the descriptor comparison; `st_uid` and `st_gid` are 0;
  - an `mcp.json` with explicit access rules, or whose replacement would get another owner,
    group or DACL, cannot be toggled; it is refused.
- **The check-to-rename window (fix round 1, Codex CX-4).** The bytes and identity are checked
  before every replace attempt, but a program that does not take aelix's lock (an editor) and
  writes between that check and the rename is overwritten, and a permission change made there
  is undone. The verify pass measured both on macOS, by landing them inside the writer's
  `os.replace`: a `chmod 0600` left the file at `0644`, and an editor's added server was gone
  (`.omc/probes/623-624/verify-r2/probes/window_probe.py` and `.out`). The window runs from the
  end of that check (on Windows, of the descriptor comparison after it) to the rename; aelix
  has no compare-and-rename to close it. The user docs say a change made during a save is
  refused only when it is made before that check (fix round 2, verify r2 F1).
- **The `mcp` kind reads off the event loop; the settings kinds do not (fix round 1, Codex
  CX-10).** `selections/list`, `selections/set` and the save's reads before and after the write
  resolve, read and parse `mcp.json` in a worker thread (`McpSelection.snapshot_off_loop`); the
  rows, which read what this launch started, are built on the loop. With every read made 0.5 s,
  `settings/get` answered within 0.25 s and the loop never stalled 0.25 s, for a list and for a
  set (`test_a_slow_mcp_json_read_does_not_hold_up_another_request`, macOS 3.12, also under the
  coarse Windows clock); at `beadfac4` the other request waited 0.508 s behind a list and 1.013 s
  behind a set. The four settings kinds read their files on the loop, as every other control-v1
  method does: with `SettingsManager.read_list` made 0.3 s, `selections/list` stalled the loop
  607 to 617 ms for skill, tool, extension and agent (two reads each) and `settings/get` 909 ms
  (three reads); without the delay they answer in 0.4 to 9 ms and 0.3 ms
  (`.omc/probes/623-624/fix-r1/M/probes/other_kinds_on_loop.out`, macOS 3.12). Moving the
  settings reads off the loop is not this change.
- **macOS:** a `deny delete` ACL on `mcp.json` makes it impossible to toggle; it is refused.
- A hard-linked `mcp.json` is refused, not split.
- The settings writer's sidecar lock is still per agent dir (`aelix_ai/settings/storage.py:244-254`).
  Two processes with different agent dirs editing one project `settings.json` are not
  serialised. That is ADR-0279's existing behaviour, unchanged here; it is a follow-up
  candidate (the contract's §8).
- Lock reasons, group labels and the row refusals that repeat them may name a TUI command
  other than `/trust` (D3 lists them).
- The `mcp` kind's `$AELIX_MCP_CONFIG` case writes nothing (D12).

- The live TUI captures and the real `--mode rpc` child checks of the implementation contract
  (§4) are the lane's live-check pass; this ADR records none of their screens or wire logs.

## How it is held

Each item names what was run and the condition it held under. Windows: nothing is measured
here; the Windows-only cases' first real run is `windows-latest` CI.

- **RPC, in process** (`tests/rpc/test_selections_rpc_624.py`, 29 test functions, 45 cases
  since fix round 1, on the control-v1 rigs): refusals write nothing (`test_a_refused_set_writes_nothing`); every locked or
  placeholder row of every kind is refused with `edit_refusal`'s words
  (`test_every_row_that_cannot_change_is_refused_with_its_own_words`); an untrusted folder's
  per-kind rows, with no `/trust` on the wire and nothing under `.aelix/` opened; cancelling a
  queued set, a running save (`not_cancellable`) and the consent dialog (then the mutation lock
  is free); a dialog declined, cancelled or timed out (the 45 s bound patched to 0.2 s) creates
  nothing, a confirmed one creates the file and a client boolean never does; the deferred apply
  and the last writer; an unreadable global file; the event loop answering while another
  process holds the `mcp.json` lock; no `mcp.json` secret on the wire; no `agent` kind without
  an agent service; a non-string key, the 120-character quote, the kind refused before the
  changes, the dialog's 45 s bound, no cancel once the dialog is answered, the deleted project
  file over RPC, and `_selection_controls` itself (the `--no-extensions` listing read once,
  eagerly for the TUI and at the first snapshot for RPC; the `mcp` kind's launch trust). Fix
  round 1 added: the did-not-load answer and its plain-text guard, "list again" for the "file
  changed" error, an `mcp.json` server named `extension:foo` listed and set by its key, a slow
  `mcp.json` read not holding up `settings/get` (a list and a set), and the global set that this
  project's file already decides (D4).
- **Rows and snapshots against hand-written values** (`tests/rpc/test_selections_rows_624.py`,
  7 tests, fix round 1, verify F3): `inForce`, `mismatches`, `policyPending` and `liveNote` over
  RPC, each expected value written from what the test set up, not computed by the serializer;
  the `JobsExtension` lock and no identity-less `item` without a lock (D3); the extension kind's
  MCP rows (D16); and every text field of a listing and a save in RPC words, `problems`
  included (D3).
- **Every touched test file** (23 files): passed on macOS with Python 3.11, 3.12 and 3.14 and in
  Linux docker (Python 3.12, as root; the two writer files also as uid 1000 on tmpfs) at
  `16c0e779`; the timing tests 5 of 5 under the coarse Windows clock on 3.11 and 3.12
  (`.omc/probes/623-624/impl/verify/`). Mutants on the lane's RPC and CLI lines: 72, of which
  65 are killed and 7 are equivalent (`.omc/probes/623-624/impl/verify/mutants/`,
  `.omc/probes/623-624/impl/lane/survivors-log.json`).
- **Fix round 1** (`.omc/probes/623-624/fix-r1/`). The 66 test files that the round touched or
  that import what it changed (listed in `.omc/probes/623-624/fix-r1/lead/touched-tests.txt`,
  written by `testlist.py` there), whole, with
  `-W error::pytest.PytestUnraisableExceptionWarning`: 3,017 passed and 13 skipped on macOS with
  Python 3.11, 3.12 and 3.14 each at `2e1831ba`, and in Linux docker (Python 3.12, as root,
  tmpfs `/tmp`) 3,010 passed and 21 skipped at `16e0304b`; the writer, mcp-kind and
  selections-RPC files (the five in `.omc/probes/623-624/fix-r1/lead/docker/u1000-tests.txt`)
  also as uid 1000 on tmpfs, 301 passed and 13 skipped. The timing files
  (`test_selections_rpc_624.py`, `test_mcp_write_624.py`) passed 3 of 3 under the coarse Windows
  clock on 3.12 and once on 3.11. Mutants: 70 new on the round's changed lines, 69 killed (the
  survivor drops `rpc_words` from the did-not-load reasons, which carry no TUI text, so it is
  equivalent), and the verify pass's eight survivors (B05, B07, B27, B30, B31, C12, M09, M26)
  all killed on the integrated tree.
- **Fix round 2** (`.omc/probes/623-624/fix-r2/`). The same 66 files as fix round 1 (its list;
  this round's 7 touched test files and the 41 that import or call what it changed are all among
  them: `runs/touched-tests.txt`, `runs/related-tests.txt`), whole, with
  `-W error::pytest.PytestUnraisableExceptionWarning`, at `2e4b38c7`: 3,024 passed and 13
  skipped on macOS with Python 3.11, 3.12 and 3.14 each; in Linux docker (Python 3.12, as root,
  tmpfs `/tmp`) 3,016 passed and 21 skipped, and the five uid-1000 files as uid 1000, 304 passed
  and 13 skipped. The tests this round added for N5 and N6 fail at `3d4e7364` (10 failures,
  `runs/new-tests-on-3d4e7364.txt`); the round's pins are shown by mutants instead (`mutants/`).
  The verify pass's survivors S11, S12 and G04 are killed; of 14 new mutants on the round's
  lines, 13 are killed, the four of the `mcp` kind's other-file refusal (the panel's arrow,
  RPC's set and both reason texts) among them, which all survive `3d4e7364`'s parity test. The
  survivor, N6b, drops the authorizer's same-path retarget check: the writer's own check after
  its lock then refuses the save with the same words, `mcp.json now resolves to another file;
  nothing written; try again` (41 files, 1,624 passed with it in place;
  `mutants/byhand-N6b.out`), so it is equivalent.
- **Live** (CLAUDE.md rules 9 and 10; `.omc/probes/623-624/impl/live/`): `/mcp` at 120x40 and
  80x24 (a toggle, the restart that reads it, `$AELIX_MCP_CONFIG`, a trusted and an untrusted
  project, `/mcp list`, mid-turn with a real model); `/skill`, `/tool`, `/extension` and
  `/agents` identical on `a1606f58` and on the implementation as it stood before fix round 1
  but for the install path (fix round 1's differences are in the next item); a real
  `--mode rpc` child with a real model whose next request no longer carried the tool and skill
  turned off, the `mcp` toggle read by the next start, the create-file dialog confirmed and
  cancelled over real pipes, and the idle refusal during a real turn. `SECRET-624` appeared in
  no capture of this change.
- **Live, fix round 1** (no model; `.omc/probes/623-624/fix-r1/lead/live-tui/` and
  `live-rpc/`). `/mcp` at 120x40 and 80x24: the ←/→ reason whole, "…waits for #644" included,
  the `$AELIX_MCP_CONFIG` lock line whole with its path, no scope marked where nothing is
  writable, `skipped` for the invalid entry, and an Enter with no edit saying "✓ MCP servers:
  nothing to save" with the file unchanged. `/skill`, `/tool`, `/extension` and `/agents` against
  `beadfac4` at 120x40, one scratch path: `/skill` identical; `/tool` and `/agents` one
  description row taller, because a row's reason in that fixture needs it; `/extension` lists
  `JobsExtension` locked `/settings`. The verify pass measured the 80x24 cost too: `/tool` shows
  5 list rows where it showed 7, and `/mcp` opens scrolled (D16, "What the reserve costs"). A
  real `--mode rpc` child: the "file changed" error ends "list again" with nothing written; a
  set against an `mcp.json` that does not parse names "did not load: not valid JSON at line 22
  column 1 (JSONDecodeError)"; the extension kind's
  `mcp:` rows say `saved` and `applied` alike; `JobsExtension` carries its lock and no
  identity-less `item` lacks one; and with resolving `mcp.json` slowed by 0.5 s, `settings/get`
  sent after `selections/list` kind `mcp` answered in at most 0.001 s, 3 of 3, where
  `beadfac4`'s child answered it 0.504 to 0.511 s later, after the list.
- **TUI and RPC write the same bytes** (`tests/rpc/test_selections_same_bytes_624.py`): 40
  cases since fix round 1 (verify F5), the 5 kinds × 4 scenarios (`plain`, `concurrent-noop`,
  `concurrent-real`, `none`) × 2 targets. For skill, tool, extension and agent the edits are made
  with the panel on Global and the target is then really switched with the arrows (→ to This
  project, or → then ← back), from one seeded state on both sides; the `mcp` kind has one
  writable file, so its project case opens on a project `.aelix/mcp.json` and asserts the other
  arrow's refusal. A save that would create `.aelix/settings.json` is not in this test; the
  consent tests hold it. Two rigs from one seed under two roots, the panel driven by keys and
  RPC over a wire; every
  settings and `mcp.json` file byte-identical, the snapshots, save and apply results equal, and
  the runtime effects equal. Its bites, on scratch copies
  (`.omc/probes/623-624/impl/lane/parity-bites/`): `commit`'s apply skipped in
  `set_selection` → 12 of 20 fail; the `changes` reversed in `_validate_changes` → 5 fail
  (`extension-plain` among them, where the result depends on the order); the no-op drop moved
  from `ResourceSelection.save` into the RPC handler → 4 fail (`concurrent-noop` for skill,
  extension and agent, and skill `concurrent-real`). Fix round 1's two bites: the panel saving to
  `global` whatever its target, and RPC's scope forced to `global`, each survive the
  global-only cases and fail the project ones (`.omc/probes/623-624/fix-r1/R/mutants/`).
- **The fixtures** (ADR-0302 D8): `selections.list.json` (10 cases) and `selections.set.json`
  (30 cases), and `capabilities.json`'s `selectionKinds`, all `"since": "v1"`, in
  `tests/rpc/control_v1/`: 393 passed on macOS, Python 3.12, at `7d96de62`, the real child
  included; 425 since fix round 1's framework changes, and 428 at fix round 2's `2e4b38c7`
  (ADR-0302).
- **The shared core** (`tests/core/test_resource_selection_624.py`): the consent race (a project
  file deleted after the lock and before the read is not re-created; a confirmed save
  re-creates it; the global file is still created), the no-op drop with another writer's list,
  `edit_refusal` and `scope_refusal` against the panel's `a1606f58` texts for every role and
  lock kind (but the identity-less `always_on` row of D3, which no `a1606f58` row was), and
  `commit`.
- **The panel** (`tests/tui/test_selection_panel_624.py`, `tests/tui/test_mcp_panel_624.py`).
- **The mcp kind** (`tests/core/test_mcp_selection_624.py`): the file in force and its scopes,
  trust, the re-checks inside the lock, the rows, `aelix status --json` flipping a server's
  state with no new key, no secret in any row, problem, refusal or save error.
- **The writer** (`tests/core/test_mcp_config_edit_624.py`, `tests/core/test_mcp_write_624.py`)
  and its measurements, D14. The two files passed on macOS with Python 3.11, 3.12 and 3.14
  (204 passed, 8 skipped), and in Linux docker as root on tmpfs and as uid 1000 on tmpfs (206
  passed, 6 skipped); `test_mcp_write_624.py` also as uid 1000 on overlay (55 passed, 6
  skipped) (`.omc/probes/623-624/impl/writer/`). Since fix round 1 the two files hold 237 cases:
  227 passed and 10 skipped on macOS, Python 3.12; their Linux runs are in the fix-round-1
  item above.
- **The loader** (`tests/cli/test_mcp_loader_624.py`): the type word, no secret at launch, in
  `aelix status` or its `--json`, the two crashes as warnings, and `resolve_mcp_config`.
- **Complexity** (ADR-0300; ruff 0.15.13): D10's numbers. Fix round 1 raised no listed
  function and added none over 20 (`scripts/check_complexity.py --check`: OK). Ruff C901 at
  `beadfac4` and at this change (`.omc/probes/623-624/fix-r1/lead/complexity-changes.txt`):
  `_authorize` 3 → 4 and `_authorizer` 4 → 5 (`core/mcp_selection.py`), `write_enabled` 4 → 5
  (`core/mcp_config_edit.py`), `locks` 9 → 10, `applied` 1 → 2 (`core/extension_selection.py`),
  `ResourceSelection.snapshot` 2 → 3, `_unread` 2 → 3 (`cli/config.py`), and in
  `tui/selection_panel.py` `_state_line` 15 → 2 (its body is now `_state_text`, 15) and
  `status` 11 → 12. Every product function fix round 1 added, but `_state_text`, scores 4 or
  less. Fix round 2
  changed no score: `_authorize` 4, `_authorizer` 5 and `edit_refusal` 7 at `3d4e7364` and
  here (`.omc/probes/623-624/fix-r2/runs/c901-changed-functions.txt`), and
  `scripts/check_complexity.py --check` is OK.
- **The consumer side**: aelix-acp's native suite against this change at `7d96de62`:
  `Ran 134 tests … OK (skipped=1)`, the skip Windows-only, as on `a1606f58`
  (`.omc/probes/623-624/impl/lane/acp-native-7d96de62.log`), and again at fix round 1's
  `2e1831ba` (`.omc/probes/623-624/fix-r1/lead/acp-native-2e1831ba.log`), and at fix round 2's
  `52ff7529` (`.omc/probes/623-624/fix-r2/runs/acp-native-52ff7529.log`). aelix-acp does not call the
  selection methods yet (ADR-0302's consumer list, §6), so this shows that nothing it does call
  changed.

## Where the build differs from the design draft

Each follows the code; none changes a decision above.

- **`commit` takes `on_apply_error`** (D10). The draft's `commit` had two keywords; the panel
  needs its own "saved, but not applied" line, and RPC needs the warning in `apply`.
- **`scope` is checked as D4's "As built" says**: missing is refused only with changes, unknown
  always, unwritable only with changes. The draft's item 5 refused all three in every case.
- **The refusal texts** are quoted from `rpc/control_selection.py` in D2 and D4. A `kind` that
  is not a string is named by its Python type name (`NoneType` when it is missing).
- **`CONSENT_TIMEOUT_SECONDS = 45.0`** is a named constant in `rpc/control_selection.py`.
- **The `--no-extensions` listing** is read through one cached closure in
  `_selection_controls`, eagerly for the TUI and at the first `extension` snapshot for RPC (D10).
- **The control-v1 rigs build the selection controls through the CLI's own
  `_selection_controls`** (D10), not a copy of it.
- **`SCOPE_WORD` is public** in `core/resource_selection.py` (D10).
- **The no-op drop is not judged against an unreadable file** (D4): with one, the edit is not
  dropped, and the save refuses it.
- **Two more mcp rows**: a name a delegating parent keeps off is locked `cli`/`inherited` (D12),
  and an extension's server is an `info` row locked `status` with its state word as the tag
  (D3, D16).
- **"The file is still the one that was read"** compares the bytes too, read again, not only the
  identity (D14).
- **The Windows DACL rule** also refuses an absent or empty DACL and one it cannot read (D14).
- **A JSON syntax error's warning** was "<path>: not valid JSON (JSONDecodeError)", without the
  decoder's line and column; fix round 1 put the two numbers back (D14).
- **No helper in `aelix_ai/settings/storage.py`**: the write-all loop lives in
  `core/mcp_config_edit.py` (D14).

Fix round 1 (2026-11-18) changed the build, not a decision: the writer's identity and its checks
before each replace attempt, the Windows descriptor comparison and the real path a save keeps
(D13, D14); the `mcp` kind's collision-free keys (D3); its reads off the event loop (Limits);
RPC words for save errors and the unknown-row text (D3, D4); the `JobsExtension` lock (D3); the
extension kind's MCP rows' `saved` (D16); and the panel's words (D16). Each was a finding of the
verify pass (`.omc/specs/623-624-verify-r1-report.md` and its live helpers) or of Codex
(`.omc/specs/623-624-codex-impl-review.md`).

Fix round 2 (2026-11-18) changed two refusal texts, not a decision: the `JobsExtension` row's
(D3) and a retarget's after the writer resolved the path (D13). Each was a finding of the
second verify pass (`.omc/specs/623-624-verify-r2-report.md`).

## Alternatives considered

- One method pair per kind (option B) and `resources/…` names (option C): the lead chose A; see
  D1 for the naming.
- `changes` as an object `{key: bool}` (round 0): replaced by an array (D4, opus M7).
- MCP as a `ResourceKind` behind a settings-shaped facade: rejected on D11's evidence.
- Toggling MCP in `/extension`: rejected (D16).
- Writing `mcp.json` by re-serialising it, as pi and the settings writer do: rejected. It
  re-lays arrays and escapes in a file the user wrote by hand and that holds credentials.
- **Writing `mcp.json` in place** (truncate and rewrite the same inode), which would keep every
  ACL, owner and link: rejected. It is not atomic: a launch that reads the file mid-write gets
  half a file and starts no server, and a crash leaves a broken credential file. D14 keeps the
  metadata it can carry, and refuses where it cannot.
- **A sidecar lock under the agent dir** (round 0): rejected on Codex 8's case and the measured
  lost updates (D14).
- Creating `.aelix/mcp.json` to turn a global server off in one project: rejected. Under the
  one-file rule it stops every other global server there (D12).
- A turn-safe `selections/set` with an end event: later, as additions (D7, lead's Q2).
