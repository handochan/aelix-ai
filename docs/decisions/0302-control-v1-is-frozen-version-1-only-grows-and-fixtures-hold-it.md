# 0302. control-v1 is frozen: version 1 only grows, and a consumer list and contract fixtures hold it

Status: Accepted (2026-11-18, #623). Designed 2026-11-17 ("round 0"), revision 1 2026-11-18 after
a Codex and an opus review; implemented 2026-11-18; fix round 1 2026-11-18 after Codex's review
of the implementation (its findings CX-5 to CX-9: D3, D4, D8, D9, Limits). What holds each decision is in "How it is held",
each with the condition it was measured under; where the build differs from the design draft,
"Where the build differs" says how and why. Amended 2026-11-19 (#797): a bundled package that
failed to import is in `resources/list`'s `extensionErrors` and `selections/list`'s extension
rows, a compatible correction (the lead's call); see the note of that date.
Date: 2026-11-18
Issue: #623. Lands in one commit with #624 (ADR-0305). Lead's call: one lane and one PR,
because #624's done-conditions include #623's contract tests and both change `rpc/control.py`.
Design: `.omc/specs/623-624-adr-drafts-2026-11-17.md` (section A, revision 1); implementation
contract: `.omc/specs/623-624-impl-contract-2026-11-17.md`; evidence:
`.omc/probes/623-624/impl/`.
Amends: ADR-0275 (the compatibility rule it did not have), ADR-0198 ("Partial fulfilment": the
control-v1 half of the RPC contract is here). Each carries a dated note.
Relates: ADR-0286 (clients ignore an event type they do not know), ADR-0278 (the
`aelix_subagent` fields; #612 extends them), ADR-0300 (the complexity ratchet, whose
merge-base check this ADR reuses), ADR-0305 (the first methods added under this rule),
ADR-0235 (pi is a reference, not a parity mandate).
pi: `earendil-works/pi` `5f89cc0a` has no versioned control envelope; its RPC is the classic
roster, which this ADR records and does not freeze (D7).
Line numbers: the Context section's aelix-ai lines are at `a1606f58`, the commit the design was
read against. From the Decision on they are at this change, and a sentence about code this
change replaced says "at `a1606f58`". aelix-acp lines are at `16d6555b`, Studio's at `0285f5c3`.

## Context

- ADR-0275 put a versioned envelope beside the classic roster. `capabilities` answers
  `version: 1` (`rpc/control.py:413`) and lists the methods this host serves (`:388-410`). It
  says nothing about what may change while the version stays 1. The only related rule is
  ADR-0286's: clients ignore an event type they do not know (`0286:172-174`).
- aelix-acp drives aelix through control-v1:
  - it refuses to run against any version but 1 (aelix-acp `session.py:236-238`);
  - it checks `capabilities.methods` before most calls (`session.py:239-253`,
    `runtime/commands.py:82`, `server.py:304`), but it calls `resources/list`
    (`session.py:322`, `:328`) and `agents/show` (`:353`) without that check;
  - Aelix Studio reaches 17 methods through aelix-acp's `_aelix/manage`, which returns native
    `data` unchanged (`server.py:303-310`).

  Studio's first beta bundles the beta.3 wheel (#623's body).
- No list of what those consumers read existed. #623's body expects aelix-acp and Studio to
  supply it by PR, and neither has (the scout, 2026-11-17: aelix-acp PRs #1–#21 merged, none a
  list; Studio's `readiness.md:7,17,21` lists #623 as pending).
- aelix-acp pins aelix-ai `e19ab55e`. Between that pin and `a1606f58`, eight behaviours changed
  on the native side (D6). Two `asdict` result shapes also gained fields (D6). aelix-acp's
  native suite passes against `a1606f58`: 134 tests, OK, 1 Windows-only skip
  (`.omc/probes/623-624/scout/evidence/acp-native-a1606f58.log`).
- Several result shapes have no test. `extensions/list` has none at all. `permissions/get`,
  `auth/status`, `auth/login`, `mcp/status` and `resources/list` are only partly tested (the
  scout's inventory).
- ADR-0198 recorded the `--mode json` contract and named the RPC half as a follow-up
  (`0198:256-268`). This ADR is the control-v1 part of that follow-up.

Owner: 2026-11-16 on #623, "beta.3 필수로 확정"; 2026-11-17, "779 기다리고 612 123 623 모두
베타버전에 넣을겁니다".

## Decision

### D1. What is frozen, and from which commit

Owner, 2026-11-17: "메서드 + 이벤트 2종, 기준 = 현재 main (권장)". As the lead's brief
summarises the option chosen:
- freeze the control-v1 methods plus the `aelix_subagent` and `extension_ui` events;
- record aelix-acp's classic commands only as observed use;
- take main at freeze time as the baseline, and accept the changes already made (D6) as v1.

So version 1 is:

- the `aelix_control` request and its correlated response (ADR-0275 §1);
- `capabilities`, which answers `version: 1`;
- every method the freeze commit serves, with its params, result fields and refusals. That is
  the 24 names `capabilities`, `commands/run`, `commands/cancel`, `resources/list`,
  `resources/reload`, `tools/set_active`, `ui/enable`, `mcp/configure`, `mcp/status`,
  `extensions/list`, `sessions/branch`, `settings/get`, `settings/set`, `permissions/get`,
  `permissions/set`, `permissions/evaluate`, `auth/status`, `auth/set_api_key`,
  `auth/logout`, `auth/login`, `agents/list`, `agents/show`, `agents/use`, `agents/run`
  (`rpc/control.py:400-422`, `:90-124`). Because the freeze commit also lands #624, it adds
  `selections/list` and `selections/set` (ADR-0305; listed at `:423-424`). They are v1 from
  their first day, and no consumer reads them yet;
- two event families: `aelix_subagent` (`rpc/control.py:803-831`) and `extension_ui_request`
  with the client's `extension_ui_response` (`rpc/control_ui.py:43-161`,
  `rpc/rpc_mode.py:2453-2455`);
- one error text a consumer matches: `already open in another terminal` (D2).

The baseline is the commit this change lands on, not `a1606f58`. The freeze is of main at
freeze time, so anything that lands on main before this change is in it. The implementation
lane re-reads the inventory at its rebase and regenerates `floor_v1.json` before merge (D8).

As built (2026-11-18, on `a1606f58`): `tests/rpc/control_v1/floor_v1.json`, written by
`scripts/check_control_v1.py --update`, holds 26 methods (the 24 above and the two selection
methods), 2 event families, 1 pinned error and the envelope, every entry `"since": "v1"`, and no
withdrawal. If anything lands on main before this change does (B7's #612 is the one named), its
methods are in the freeze too, and the landing rebase adds their fixtures with `"since": "v1"`
and regenerates the floor.

### D2. What a version 1 client may rely on

1. **The envelope.** A request `{"id", "type": "aelix_control", "method", "params"}` is answered
   by one response with:
   - `type: "response"`, `command: "aelix_control"` and the same `id`;
   - `success`;
   - then `data` (an object) or `error` (a non-empty string).

   A result the wire cannot carry is answered as an error. A cancelled operation answers the
   error `Command cancelled`.
2. **What is served.** `capabilities.methods` lists what this host serves, and a host serves
   nothing it does not list. `capabilities` itself is always served and is not listed.
3. **Each method:**
   - the names and types of its params, and that an input it accepted is still accepted with
     the same meaning (D3);
   - the names, types and meanings of its result fields;
   - its refusals being refusals.

   Error texts are free text and may be reworded, except the one in item 5.
4. **Each event family:**
   - the `type`;
   - the discriminating field: `event` for `aelix_subagent`, `method` for
     `extension_ui_request`;
   - the fields the fixtures list.
5. **The session-lock text.** `already open in another terminal` stays in every refusal of a
   session another process owns:
   - an RPC error, `aelix_agent_core/runtime/agent_session_runtime.py:685`;
   - a refused launch's stderr: `cli/entry.py:5719` (`_session_owned_message`), which a
     non-interactive launch prints to stderr at `:5851`.

   The TUI's interactive prompt at `cli/entry.py:5753` carries it too, but no consumer reads
   that prompt. aelix-acp recognises a native session lock by this text and nothing else (aelix-acp
   `wire/errors.py:12-14`, `:42-44`; read from responses at `session.py:441-442` and from the
   stderr tail).

### D3. Additions, changes, and enums (this ADR's definitions)

An **addition** lands inside version 1 with its fixture and no version change:

- a new method, listed in `capabilities.methods` only on a host that serves it;
- a new optional param whose absence keeps the old behaviour;
- a new field in a result, in an event's data, or in `capabilities`;
- a new event `type` (clients ignore unknown types, ADR-0286);
- a new `extension_ui_request` `method`. It must be safe to show as a dialog, because aelix-acp
  forwards a method it does not know to its client as one (aelix-acp `session.py:618-636`);
- a new value in an OPEN enum (below);
- accepting an input that used to be refused (a widening);
- a new or reworded error text for an input that was already refused (not D2.5's text).

A **change** needs version 2 (D4):

- removing or renaming a method, a param, a result field, an event field or an event type;
- changing a field's type, or what its value means when the shape stays. D6's D1 is the
  example: `settings/get`'s lists kept their shape and changed which file they come from;
- making an optional param required;
- refusing, or answering differently, an input that used to succeed. D6's D3, D4 and D6 are
  the examples. A verdict that changes for the same input is one too: D6's D7,
  `permissions/evaluate`;
- a new value in a CLOSED enum. A consumer that branches on it reads the new value as the
  wrong case: aelix-acp branches on `commands/run`'s `outcome` (`runtime/commands.py:203-208`);
- changing the envelope, the correlation, or D2.5's text;
- no longer listing a method a host served with the same services.

**Whether a consumer reads a method today decides who a change would hurt. It does not decide
whether it is a change.** Revision 1 removes the round-0 exception that let
`permissions/evaluate` pin its shape only. Codex 13 found that the exception does not follow
from the owner's decision to freeze every method. aelix-acp names `permissions/evaluate` only in
`READ_ONLY_CONTROLS` (`session.py:45-47`). That changes who is exposed, not what is frozen. Its
fixture pins representative verdicts like every other method's.

**Enums.** Every enum is closed unless the floor marks it open. A client handles an unknown
value of an open enum by the rule written next to it. The open enums at the freeze are:

| Enum | Where | A client that meets an unknown value |
|---|---|---|
| `capabilities.selectionKinds[]` | ADR-0305 D1 | ignores that kind |
| `selections/list` row `role` | ADR-0305 D3 | shows the row as status only; whether it can change is `refusal` alone, and the host never gives a non-`item` row a null `refusal` (ADR-0305 D3) |
| row `lock.kind` | ADR-0305 D3 | shows `lock.reason` as "Locked: …"; `refusal` decides changeability |
| `selections/list.effect` and `selections/set.effect` | ADR-0305 D2 | treats the change as not applied by the call. It reads `pending` from a later `selections/list`, and reads `apply`, which may be null, only as `ApplyResult` |
| `extension_ui_request.method` | above | shows it as a dialog or ignores it (aelix-acp forwards it as a dialog) |

**What "open" means to a test (fix round 1, Codex CX-7).** A fixture lists an open enum's known
values as an `enum` marked `"open": true`. The case runner validates a v1 client's way: an open
`enum` restricts nothing, and the value's type still does (`tests/rpc/control_v1/rigs.py`
`opened`). A case that needs a value pins it with `const` or `contains`, so it pins membership,
not the whole set: `capabilities.selectionKinds` is held by one `contains` per kind of the
freeze, not by the exact array. Each kind's own `effect` stays pinned with `const` in its
`selections/list` and `selections/set` cases, because a kind's effect changing is a change; a
new `effect` value can only come with a new kind, in that kind's own case. Measured, on the
`full` rig in process (macOS, Python 3.12): a sixth `selectionKinds` value passes the existing
`capabilities` case, and a row with a new `role` and a new `lock.kind` passes the existing
`selections/list` case (`test_ratchet_rules.py::test_a_sixth_selection_kind_passes_the_existing_capabilities_case`,
`::test_a_new_row_role_and_lock_kind_pass_the_existing_listing_case`); at `beadfac4` both failed
(`['agent', 'extension', 'mcp', 'skill', 'tool'] was expected`, `'a-later-lock-kind' is not one
of [...]`; fix round 2 renamed that made-up value `zz-synthetic-lock-kind`, D9).

Every other enum is closed. That covers `PermissionMode` (`permissions/*.mode`,
`capabilities.permissionModes`), `commands/run.outcome`, `aelix_subagent.event`,
`sessions/branch.kind`, the `scope` param and `scopes` keys of the selection methods, and row
`decidedBy`. `aelix_subagent.event` stays closed although aelix-acp forwards an unknown value raw
on `_aelix/subagent` (D5's consumer list, §4), because what Studio does with that forward is
unconfirmed. #612 adding an event value is therefore a change (D9).

As built (`floor_v1.json`, 2026-11-18), the floor states these value sets:
- `closed`: `permissions/get` and `permissions/set` `data.mode` and
  `capabilities.permissionModes[]` (`auto`, `auto-accept-edits`, `default`, `plan`, `yolo`);
  `commands/run` `data.outcome` (`error`, `handled`, `not_a_command`, `prompt`) and
  `data.messages[].type`; `agents/list` `data.diagnostics[].type`; `settings/get`
  `data.schema[].type`; `aelix_subagent` `event` (`subagent_start`, `subagent_tool`,
  `subagent_end`); `selections/list` row `decidedBy` (`global`, `project`, null) and the
  `scopes` key set (`global`, `project`; the floor writes a key set as `data.scopes{}`);
- `open`: `capabilities.selectionKinds[]`, the selection methods' `effect`, row `role`, row
  `lock.kind` and `extension_ui_request.method`;
- a param's legal values, as the param's own `enum`: `permissions/set` `mode`,
  `sessions/branch` `kind`, `settings/set` `key`, `mcp/configure` `servers[].type`, and the
  selection methods' `kind` and `scope`. These are closed: a value refused later fails R1.

`test_input_coverage.py::test_every_enum_is_the_codes_value_set` compares each with the value
set the code names (equal for a closed one, a subset for an open one). Three are literals the
code does not name, and the floor alone pins them: `commands/run` `data.messages[].type`,
`sessions/branch` `kind` and `mcp/configure` `servers[].type`.

**Row `decidedBy` and the `scopes` keys (fix round 1, Codex CX-8).** Every `selections/list`
case that lists rows gives `decidedBy` the closed `enum` `["global", "project", null]` and
`scopes` `propertyNames: {"enum": ["global", "project"]}`. The floor holds both as closed sets,
and `test_every_enum_is_the_codes_value_set` compares them with `ItemState.decided_by`'s
`Scope` literal plus `None` and with `rpc/control_selection.py`'s `SCOPES`
(`test_input_coverage.py::test_the_row_and_scope_vocabularies_are_closed_sets`, which failed at
`beadfac4`). On the `full` rig in process, a row whose `decidedBy` is a made-up
`zz-synthetic-scope`, or a third key under `scopes`, fails the existing `lists the skills` case, while a new field in a row or in
a scope's object still passes it
(`test_ratchet_rules.py::test_the_closed_row_vocabularies_refuse_a_new_value_and_allow_a_new_field`).

### D4. Version 2, and the one way an accepted input may be refused inside version 1

- Only a change moves `capabilities.version` to 2. Additions never do.
- aelix-acp refuses every version but 1, so a bump breaks every shipped adapter. A version 2
  lands in this order:
  1. an aelix-ai issue names the change and the consumer-list rows it breaks;
  2. the consumers agree: an aelix-acp issue or PR, and Studio where `_aelix/manage` passes the
     method through;
  3. aelix-acp ships support for version 2;
  4. aelix-ai lands the change with `version: 2`, new fixtures and a new `floor_v2.json`.
     `floor_v1.json` is kept, unchanged, marked superseded.
- Whether a host serves 1 and 2 side by side is for the ADR that introduces 2.
- **A safety refusal inside version 1 (lead's call, Q3).** An input that v1 accepted may be
  newly refused, or a `permissions/evaluate` verdict tightened, for safety only with ALL of:
  - a per-case owner decision;
  - a dated note on this ADR that names the case;
  - aelix-acp's native suite green against the change;
  - the consumer list updated.

  Otherwise the change needs version 2. The floor records such a case as a *withdrawal* (D8),
  which never disappears from the floor. CI accepts a floor entry that changed only when its
  withdrawal record lands in the same change and this ADR has the dated note that the record
  names.
- **Correcting a frozen case (lead's call, fix round 1).** A `since: "v1"` case is frozen as
  written (D8): any edit to it fails R1–R3, an added assertion as much as a dropped one. A
  correction of a case that asserted something the host never promised goes through the same
  record, `scripts/check_control_v1.py --update --withdraw <method>:<case> --note <YYYY-MM-DD>
  --issue '#<n>'`, with the dated note here saying it is a correction, and re-pins
  `V1_SHA256` in `test_additive_only.py`. A case that only needs to show more is not
  corrected: a later lane adds a case of its own (D9).

### D5. The consumer list

- **Where:** `docs/contracts/control-v1.md` (that file's header says why it lives there).
- **Who writes it.** Owner, 2026-11-18: "aelix-ai가 쓰고 ACP가 확인 (권장)". As the lead's brief
  summarises it: aelix-ai lands the derived list marked "confirmation pending", and aelix-acp
  confirms it in review. A confirmation or a correction is an edit of that file and of the
  matching fixture's `consumers` entry. `tests/rpc/control_v1/test_consumer_list.py` fails
  while they differ. No redesign follows from a correction: reading more of version 1 never
  needs a version change, and reading less needs nothing.
- **Granularity (revision 1, Codex 9).** The list has one row per method and per event family,
  plus field rows of two kinds:
  - **direct reads** — a path a consumer's code names, with its file:line (`resources/list`
    `tools[].name`; `agents/show` `profile.tools`; `commands/run` `outcome`);
  - **forwarded whole** — a result aelix-acp passes on without naming its fields (`_aelix/manage`,
    `_aelix/capabilities.runtime`, a slash command's JSON output, the unparented
    `_aelix/subagent` forward, a dialog's whole frame). Every field there is consumed. The
    row's field paths are the floor's paths for that method. They are generated from the
    `full` rig's real responses (D8), never typed by hand, so a renamed forwarded field fails
    the floor like a direct read.

  Params are listed with their shapes (`auth/set_api_key {provider, key}`; `mcp/configure`
  entries with `env`/`headers` as `[{name, value}]`).
- **Not consumed yet.** Methods that are served and not read are listed apart ("served, not yet
  consumed"), so a first consumer moves a row instead of adding one.
- **The #623 done-condition "R 시작 전에 소비 목록과 main이 일치하는지 확인했다"** is held two
  ways: `test_consumer_list.py` on every CI run, plus a re-read of aelix-acp's main before R.
- **As built (2026-11-18).** `docs/contracts/control-v1.md` lands with the status "derived from
  aelix-acp `16d6555b`; confirmation pending", and `docs/contracts/README.md` lists it as
  hand-written. `test_consumer_list.py` fails while its §3.1 and §4 rows, or its §6 table,
  differ from the fixtures' `consumers`, while a path it says is read is not a floor path, and
  while its §3.2 or §4.1 forwarded paths differ from the floor's.

### D6. The changes since aelix-acp's pin are version 1 (owner)

Owner, 2026-11-17, "메서드 + 이벤트 2종, 기준 = 현재 main (권장)". The baseline is main at freeze
time, so the following, all made between aelix-acp's pin `e19ab55e` and `a1606f58`, are v1.
aelix-acp's native suite passes on them (Context; this ADR's measurement, not part of the
owner's words).

| | What changed | Where (`rpc/control.py` at `a1606f58`) | By | Kind under D3 |
|---|---|---|---|---|
| D1 | `settings/get`'s `skill_paths` / `prompt_template_paths` read the GLOBAL file only (a trusted project's list used to show instead) | `:461-465`, `:707-719` | #511 `04683650` | change (meaning) |
| D2 | new settings key `agent_profiles` in `settings` and `schema` | `:77-78`, `:86-87` | #795 `c77fb5ad` | addition |
| D3 | `settings/set` refuses every key when the global file did not load; list keys go through `edit_list` | `:684-693`, `:667-679` | #511 | change (new refusal) |
| D4 | `tools/set_active` refuses a name the run keeps off (`Cannot turn on X: …`); a name only saved settings turned off is turned on for the session | `:501-513`, `:517-526` | #510 `74f9214c` | change (new refusal) |
| D5 | `agents/list` rows gain `enabled` and `decidedBy` | `:830-849` | #795 | addition |
| D6 | `agents/run` refuses an unsendable task and a stopped harness | `:880-885`, `:886-890` | #627 `6bc82811`, #663 `5b977b03` | change (new refusal) |
| D7 | `permissions/evaluate` verdicts for writes into loader roots | `:607-609` | #646 `04dd5c14` | change (verdict) |
| D8 | mutating classic commands are refused during a classic session swap | `:152-155`, `:363-368` | #663 | classic gate; aelix-acp sends none of the swap commands |
| D9 (rev1) | `AgentProfile` gained `exclude_tools` and `agents`, so `agents/show.profile` gains both through `asdict` (`agents/profile.py`; `rpc/control.py:872`) | `agents/profile.py` | between `e19ab55e` and `a1606f58` (`git diff e19ab55e a1606f58 -- …/agents/profile.py`) | addition |

`agents/show.profile`, `agents/run.result` and `agents/list.diagnostics[]` are `asdict` of
dataclasses (`rpc/control.py:867`, `:872`, `:925`). Their field names are therefore v1 wire
names. A new dataclass field is an addition; renaming or removing one is a change. The floor
lists them from real responses (D8), so a rename fails.

### D7. The classic commands are recorded, not frozen

- The classic roster (ADR-0275 §1: "its 29 commands are unchanged") is not control-v1. By the
  owner's decision (D1), aelix-acp's 18 classic commands are recorded only as observed use.
- They are listed in the consumer list's section 5, with how they were found.
- This ADR's own statement: they stay governed by what governs the roster today:
  - pi's RPC protocol as the reference (ADR-0235: a reference, not a mandate; pi has since
    added `clear_queue`, `get_entries` and `get_tree`, `rpc-types.ts:26`, `:65-66` at
    `5f89cc0a`);
  - the roster's ADRs;
  - `tests/rpc/test_rpc_mode_*.py` and `tests/pi_parity/`.
- This ADR's process rule: a lane that changes one of those 18 commands reruns aelix-acp's
  native suite against its branch and says so in its PR. That is a check, not a freeze.

### D8. The contract tests (revision 1: the floor is a ratchet, and accepted inputs are run)

Lead's call: #623 lands fixtures for today's methods and the two event families. The layout is
this ADR's.

**The fixtures:** `tests/rpc/control_v1/fixtures/`. There is one JSON file per method (`/`
becomes `.`, as in `permissions.set.json`), one per event family (`event.aelix_subagent.json`,
`event.extension_ui.json`), plus `envelope.json` and `error.session_locked.json`. Each file
holds:

- the method or event name;
- `since`: `"v1"` for what the freeze commit serves, `"#<issue>"` for what is added later;
- `hosts` (D10);
- `requires`: the service `capabilities` lists it under, or null;
- `params`: the input contract — each param's type, whether it is required, its legal values
  where they are an enum, and what its absence means;
- `consumers`: rows of the consumer list;
- `cases`: each with a named rig, the request, and the response as a JSON Schema (draft
  2020-12, validated with `jsonschema`, already an `aelix-ai` dependency).

A schema requires the fields v1 promises and never sets `additionalProperties: false`.
`const` or `enum` pins a value where the meaning is the contract.

**What the cases must cover (Codex 5, opus M5).** `test_input_coverage.py` enforces each rule:
- **Every accepted enum value.** There is a success case for every legal value of every enum
  param, for example `permissions/set` with each `PermissionMode` the host accepts. A change
  that refuses `plan` therefore fails a case, not only a schema.
- **Optional params.** A case omits each optional param, and a case sends it.
- **Collections.** A case sends each accepted empty collection (`tools/set_active
  {"names": []}`, `selections/set {"changes": []}`). Each boundary the code enforces is sent
  at the bound, for example a 256-entry `settings/set` list, and refused one past it.
- **Populated arrays.** The rigs populate every array a schema reaches into: an extension
  loaded, a skill, a prompt, an MCP lease, a hook. A `required` item field is then exercised;
  the test fails if the array was empty in every case that names it.
- **Param sensitivity.** A case marks a param `"sensitive"` with another legal value. The test
  sends the request again with that value and asserts the case's own schema rejects the
  answer. That proves the host still reads the param, and every floor param needs one.
- **Code value sets.** Each closed enum's floor values equal the value set the code defines:
  `[m.value for m in PermissionMode]`, the command-outcome enum
  (`extensions/command_dispatch.py:52`) plus `prompt`, the `aelix_subagent` channels
  (`rpc/control.py:820`). For an open enum, the floor's values must be a subset of the code's
  (`typing.get_args` of `Effect`, `Role`, `LockKind`). A new value in code for a closed enum
  then fails at once, whether or not a case reaches it.
- **`permissions/evaluate`.** Representative verdicts pinned with `const`: a read inside the
  workspace, a write under `plan`, and a write into a loader root (D6's D7). Which verdict each
  gets is read from the code at freeze time.

**`floor_v1.json` — the ratchet (Codex 4).** The floor is generated by
`scripts/check_control_v1.py --update` from the fixtures and the `full` rig's real responses;
nobody edits it by hand. It holds, for every method and event family:
- its `since`;
- its params with types and legal values;
- every response path a success case requires, with its type;
- the forwarded-whole paths seen in real responses;
- each closed enum's values and each open enum's known values;
- every case's name, its request, its kind and rig, its digest, and what it pins.

**What a case pins (fix round 1, Codex CX-6).** Each case's `facts` are its execution steps
(`before`, `during`, `setup`, `check`, `trigger`, `replay`, each `sensitive` alternative) and
every assertion its schemas make: each declared type, each required member at any depth, each
`const`, enum, key set (`propertyNames`), `contains` and bound, and each `allOf`, `anyOf` and
`not` kept whole. A schema keyword or a case key outside the set the floor reads makes
`--update` exit 2 naming the fixture and the case, instead of dropping it
(`scripts/check_control_v1.py` `SCHEMA_KEYWORDS`, `CASE_KEYS`). Each case's `digest` is the
sha256 of the whole case as written, with the fixture-level schemas it runs with (an event
family's `frame` and `reply`, the envelope's `request` and `response`, the pinned error's
`text`). **A `since: "v1"` case is frozen as written:** R1–R3 refuse any change to its digest
unless a withdrawal names it (D4). A later case (`since: "#<issue>"`) is held by its facts: it
may gain one, and loses none.

Three rules hold it:
- **R1 (`test_additive_only.py`, no git).** The fixtures still satisfy every floor entry, at
  least as strictly: the same paths required with the same types, a closed enum with exactly
  its values, every case still present with the same kind, rig and request and pinning at
  least what it pinned, and every since-v1 case with the same digest.
- **R2 (`test_additive_only.py`).** Everything in the fixtures is in the floor: whoever adds a
  fixture, a case or a path runs `--update` in the same change. Additions, #612's first, enter
  the floor and are protected from then on.
- **R3 (`scripts/check_control_v1.py --check --require-base --github-event`, every CI leg).**
  The floor at HEAD contains the floor at `git merge-base HEAD origin/main`; on a push to
  `main`, it contains the floor of the commit the push replaced. These are ADR-0300's
  merge-base mechanics (`scripts/check_complexity.py`). A change that deletes a fixture and
  its floor entry together therefore fails CI. A withdrawal (D4) is the only accepted change
  of an entry.
- **The freeze snapshot.** `test_additive_only.py` also pins, as `V1_SHA256`, the sha256 of the
  since-v1 cases' digests by entry. An entry with no since-v1 case is left out (fix round 1,
  Codex CX-5), so a method, event family or case a later lane adds moves it by nothing. That
  signal works without git.

**The tests:**
- `test_contract.py` runs every case in process, through `run_rpc_mode` over an in-memory
  JSONL wire (the `Wire` of `tests/rpc/test_runtime_control_lifecycle_594.py:107-150`), so the
  envelope is on every assertion. Each case gets a fresh rig.
- `test_floor.py`:
  - a host given every service advertises at least the floor's names;
  - `aelix-server`'s WebSocket host advertises exactly the subset D10 names.
- `test_additive_only.py`: R1, R2 and the snapshot hash, and `contains`'s refusal of each kind
  of shrinkage on a hand-made floor.
- `test_ratchet_rules.py` (fix round 1): the growth and weakening rules end to end, in a scratch
  git repository under `tmp_path` with the fixtures copied in, `--update` run with the in-process
  capture replaced by an empty one (a case's facts and digest come from its fixture alone), and
  R3 as `--check --base <the commit before> --require-base`; and the open and closed
  vocabularies on the `full` rig in process.
- `test_case_runner.py` (fix round 1): a `during` command's own answer, before any cleanup.
- `test_input_coverage.py`: the coverage rules above.
- `test_consumer_list.py`:
  - the consumer list's method, event and field rows and the fixtures' `consumers` entries
    are the same set;
  - every "reads" path is a floor path;
  - rows that sit only under "served, not yet consumed" are accepted.
- `test_real_child.py` replays every `"replay": true` case on one real
  `python -m aelix_coding_agent --mode rpc` child:
  - The child gets a scratch HOME, agent dir, settings path and cwd, as
    `tests/rpc/test_rpc_real_child.py:41-53` does.
  - **Which rig it is (opus M5).** The child corresponds to rig `child`: the `full` rig's FILES,
    written by the same `rigs.seed_files(root)` function (settings, a user profile, a skill, an
    extension, an `mcp.json`), with no in-process-only service (no fake subagent runtime).
  - A case may set `"replay": true` only if its rig is `bare` or `child`.
  - **Order (opus M5).** Each replay case establishes every state it reads with its own
    `before` requests. The test runs them in a seeded random order, prints the seed, and runs
    a second order, which shows they do not depend on order.
  - ADR-0198 D7's reason applies: the wire is the contract, and an in-process capture cannot
    fail the way a pipe can. The child boots once (about 25 s,
    `tests/rpc/test_rpc_real_child.py:15-20`).
- `error.session_locked` cases are mandatory (opus M5). There are three:
  - in process, a second holder takes `session_lock` on a session file, and a
    `sessions/branch` onto it answers an error with the text;
  - on a real child, a launch with `--session <locked file>` exits non-zero and its stderr
    has the text;
  - a source check that the text is still in the two producers D2.5 names.

**A `during` command (fix round 1, Codex CX-9).** A case may start a command before its own
request and leave it running while the request is sent. With `"ended_by_case": true` the
case's own request must end it (a `commands/cancel`, or a reload whose hold the case's answer
releases): the runner reads the command's OWN answer before anything else, and a check reads
only that. Any `during` command still running afterwards, after a `sensitive` re-send, or after
a failure, is ended by a cancel of the runner's own in a `finally`, whose answer reaches no
assertion (`rigs.py` `run_case`). Every case whose check reads the `during` answer carries the
flag (`test_case_runner.py::test_every_case_a_during_check_reads_waits_for_the_commands_own_answer`).

**The rigs (as built).** `tests/rpc/control_v1/rigs.py` names four: `bare` (the `server-ws`
shape, no service), `full` (every service, a trusted project, populated collections), `turn`
(`full` with a model turn held open, for the idle refusals) and `child` (what the real child
gets). The selection controls of `full`, `turn` and `child` are built by the CLI's own
`_selection_controls` (ADR-0305 D10), not by a copy.

**The bite is shown, not assumed.** Each ran on a scratch copy; the outputs are in
`.omc/probes/623-624/impl/framework/bites/` and `.omc/probes/623-624/impl/lane/parity-bites/`:
- `activeTools` renamed `active_tools` in `tools/set_active`'s result, on the lane's step-1
  tree (`53a06ada`): 4 of the 8 `tools/set_active` cases of `test_contract.py` fail, while
  `tests/rpc/test_runtime_control.py`, `test_runtime_control_lifecycle_594.py` and
  `test_runtime_control_surfaces_594.py` pass (54 passed) (`bite1-activeTools.final.out`);
- `permissions/set` refusing `plan`, same tree: the `sets plan` case fails, and so does
  `sets auto-accept-edits`, whose `before` step sets `plan` (`bite2-plan-refused.final.out`);
- a value added to `PermissionMode`, same tree: `test_every_enum_is_the_codes_value_set` fails
  for its four rows (`capabilities.permissionModes[]`, `permissions/get` and `permissions/set`
  `data.mode`, `permissions/set` `params.mode`) (`bite3-permission-mode-value.final.out`);
- `extensions.list.json`, its floor entry and the pinned hash deleted together in a scratch
  commit: `test_additive_only.py` passes (25 passed), and
  `scripts/check_control_v1.py --check --base 53a06ada` exits 1 with
  `methods extensions/list: removed` (`bite4-r3.final.out`);
- `selections/set` with `commit`'s apply skipped: 12 of the 20 cases of ADR-0305's parity test
  fail, every `skill`, `tool` and `agent` case; the `extension` and `mcp` kinds apply nothing in
  place, so theirs pass (`parity-bites/apply-skipped.out`).

Fix round 1's bites (`.omc/probes/623-624/fix-r1/F/bites/`). Each test failed on the tree at
`beadfac4`, before the fix, and passes after it (macOS, Python 3.12; `before-fix.txt`,
`base-beadfac4-run2.txt`, `after-fix.txt`):
- a method added with only `"since": "#612"` cases moved the v1 digest (CX-5: `a later method
  moved the v1 digest`; the digest became `c25509c3…`, Codex's number);
- both `cancelled_the_during_command` checks dropped from `commands.cancel.json`, and the
  `not` dropped from `capabilities.json`'s server-ws case: `--update` wrote the floor
  unchanged (`control-v1 floor unchanged: … v1 digest f6c087b3…`). Now `--update` exits 1
  naming the case, and a floor regenerated from scratch fails R3 against the commit before
  (CX-6, `test_weakening_a_v1_case_is_refused_by_update_and_by_r3`);
- a later case losing its `before`, its `check`, a `not` or an `anyOf`: written at
  `beadfac4`, refused now (`test_a_later_case_that_loses_a_step_or_an_assertion_is_refused`);
- `minimum`, `oneOf` or an unknown case key `after`: written at `beadfac4`, exit 2 now
  (`test_what_the_floor_does_not_capture_is_refused`);
- a no-id `commands/cancel` that answers `cancelled: true` without ending the command: the
  `cancels the running command` case and the envelope's cancelled case passed at `beadfac4`
  (`DID NOT RAISE`); now each fails, because the command's own answer never comes
  (`test_case_runner.py::test_a_cancel_that_answers_without_ending_the_command_fails_its_case`).
  The cleanup's answer reached `result.during` at `beadfac4` (`'error': 'Command cancelled'`
  after a re-send that cancelled nothing); now it does not
  (`::test_the_cleanup_cancel_never_answers_for_the_case`).

### D9. How a later lane adds to version 1 (#612 first)

Lead's call, agreed with B7 (aelix-ai-3b): #612 adds its fixtures under this framework.
Revision 1 changes the rule. Additions now enter the floor instead of staying outside it. The
rule for #612 and every later lane:

- new fixture files, or new cases in an existing file, with `"since": "#<issue>"`;
  `consumers` stays empty until a consumer reads them (then the consumer list's "served, not
  yet consumed" table names them);
- the cases meet D8's coverage rules (every accepted enum value, optional absence, empty
  collections, boundaries, populated arrays, one `"sensitive"` alternative per param);
- `scripts/check_control_v1.py --update` adds the new entries to `floor_v1.json` in the same
  change. From then on R1–R3 protect them: an entry, once in the floor, is never removed or
  loosened inside version 1;
- **Admission and routing are two steps.** Whether a method is admitted, and under which lock,
  is decided first, in `RpcControlPlane.handle` (`rpc/control.py:270-`), by the method sets it
  checks: `READ_ONLY_METHODS`, `MUTATING_METHODS`, `CANCELLABLE_METHODS`, `LIFECYCLE_METHODS`
  and `TURN_SAFE_METHODS` (`:90-132`). A new method goes into the sets that say how it is
  admitted. Only after admission does `RpcControlPlane._families()` (`:1033-1047`, ADR-0305
  D10) route it: the table maps a method's first segment to a bound
  `async (method, params) -> dict` handler, and the handler refuses with its own "no service"
  text when its service is absent (as `auth_action` does, `:737-738`). The table routes and
  admits nothing. Routing a new family through it costs `_dispatch` no complexity (ADR-0300).
  The lead's note for B7 (2026-11-18): #612 adds its own lock-free admission path in `handle`
  for its child controls; that path is #612's to argue in its ADR, and its methods still enter
  the floor by the rules above;
- new `aelix_subagent` data fields are optional properties in `event.aelix_subagent.json` (an
  addition, ADR-0278:91). A new `event` VALUE is a change under D3, because that enum is
  closed; if #612 needs one, its ADR must argue it.
- `tests/rpc/control_v1/rigs.py` is shared, so #612 adds its services to the `full` rig.
- **A since-v1 case is never edited (fix round 1).** Its digest is in the floor (D8), so an
  edit fails R1–R3. A new value of an open enum passes the v1 cases unedited (D3); a lane that
  wants it known lists it in its own case's `"open": true` enum, which `--update` records as an
  addition. A new method, a new field or a new value goes in a case of its own. `V1_SHA256` in
  `test_additive_only.py` does not change: `test_ratchet_rules.py` shows a method added with
  only `"since": "#612"` cases leaving it alone and passing R1–R3, and a sixth open
  `selectionKinds` value recorded as an addition the same way.
- **A schema keyword or a case key the floor does not read** is refused by `--update` (exit 2).
  If #612 needs one, it teaches `schema_facts` or `execution_facts` to state it as a fact in
  the same change.
- **Made-up names start with `zz-synthetic` (fix round 2, verify F4).** Every method, field,
  enum value, row and case name that `test_ratchet_rules.py` adds to its copy of the real
  fixtures or to a real answer starts with `zz-synthetic` (a field, `zzSynthetic`), and so do
  the methods of `test_additive_only.py`'s digest test; a lane gives nothing real that prefix.
  `test_additive_only.py`'s hand-built floors, written to a fresh repository that holds no real
  fixture, keep their short names. `test_ratchet_rules.py` writes a
  made-up fixture or case only where no real one of that name exists (`_add_synthetic_fixture`,
  `_add_synthetic_case`; `test_a_synthetic_fixture_or_case_never_replaces_a_real_one`). At
  `3d4e7364` the made-up method was `children/list`: with a real `children.list.json` added, a
  name #612 may take, nine of those tests wrote theirs over it and failed. The same experiment on
  this change fails none of them (`.omc/probes/623-624/fix-r2/freeze-grow/`, macOS 3.12:
  `tests/rpc/control_v1` 13 failed at `3d4e7364`, 4 failed here). The four left fail at both
  commits, and they are the gates a real addition must also meet, which the experiment's own
  product patch does not: the consumer list's "served, not yet consumed" table and its
  forwarded `aelix_subagent` paths (`test_consumer_list.py`), the WebSocket host's method list
  (`test_floor.py`), and the code's own set of selection kinds (`test_input_coverage.py`).

### D10. Hosts

- `cli-rpc` is `aelix --mode rpc` with every service the CLI injects.
- `server-ws` is `aelix-server`'s WebSocket `/rpc`. It injects no posture, settings, auth or
  agent service and no selections (`aelix_server/rpc_ws.py:28-30`), so it serves the ten
  unconditional methods and `capabilities`. As built, the floor's `server-ws` methods are
  exactly those eleven, and `test_floor.py` holds that the WebSocket host advertises them and
  no more.

A fixture's `hosts` names the hosts that serve it; v1 is per host.

## Consequences

- A consumer of control-v1 can rely on everything D2 lists until a coordinated version 2.
- Every addition comes with a fixture and enters the floor. From then on, removing or loosening
  it fails CI against the merge base.
- aelix-acp's reading is written down for the first time, marked derived until aelix-acp
  confirms it.

## Limits

- The consumer list is derived, not confirmed (D5).
- A meaning change is caught only where a case pins the value (`const`, `enum`, a case's own
  check) or where a `"sensitive"` alternative shows the param is read. D6's D1 would have
  passed a shape-only fixture.
- Error texts other than D2.5's are free. A consumer that starts matching another text is not
  protected until the list names it.
- The real child has no model, so `agents/run`, a branch from a real conversation and
  `auth/login` are in process only.
- R3 needs git and `origin/main`. Locally, without them, the check says so and passes, as
  ADR-0300's does; CI runs it with `--require-base`.
- Nothing here shows Studio's UI. aelix-acp's native suite against the branch is the
  consumer-side check (the lane's live checks).
- The floor holds what a case says, not what the rig code it names does. A `setup` or `check`
  is held by its name; an edit to its body in `rigs.py` (`SETUPS`, `CHECKS`) is not a floor
  change, and review is what catches one.
- A since-v1 case cannot gain an assertion either (D8): what it should also have shown goes in
  a later case, or through a withdrawal (D4).
- An open enum restricts nothing when a case is validated (D3): a case that needs a value pins
  it with `const` or `contains`.
- The consumer list's aelix-ai line numbers are at `a1606f58`, as its header says; this change
  moved some of them (`cli/entry.py`'s session-lock producers are at `:5719` and `:5851` here).
  `test_consumer_list.py` checks rows and paths, not line numbers.

## How it is held

Each item names what was run and where; nothing here was measured on Windows except where a CI
run is named.

- **The fixtures and their tests** (D8): `tests/rpc/control_v1/` — `test_contract.py`,
  `test_floor.py`, `test_additive_only.py`, `test_input_coverage.py`, `test_consumer_list.py`,
  `test_real_child.py`, and fix round 1's `test_ratchet_rules.py` and `test_case_runner.py`.
  `pytest tests/rpc/control_v1`: 393 passed on macOS, Python 3.12, at the lane's commit
  `7d96de62`, the real `--mode rpc` child included; 425 passed at fix round 1's step 2d, same
  platform, with `-W error::pytest.PytestUnraisableExceptionWarning` (measured on the sub-lane's
  commit; its `tests/rpc/control_v1` and `scripts/check_control_v1.py` are this branch's step 2d,
  `70123096`); 428 passed at fix round 2's `2e4b38c7`, same platform and warning
  filter (`.omc/probes/623-624/fix-r2/runs/`), with three new cases: the guard that a made-up
  fixture or case never replaces a real one, and an open enum under `anyOf` and under `allOf`
  restricting nothing while a closed one still refuses (D3's open enums; the verify pass's
  mutant G04 of `rigs.opened`, now killed). Before the selection methods
  were added, the framework sub-lane ran the six files on its step-1 tree on Python 3.11, 3.12
  and 3.14, all passing (`.omc/probes/623-624/impl/framework/runs/final-py3.*-*.log`).
- **The floor**: `floor_v1.json`, written by `scripts/check_control_v1.py --update`, holds 26
  methods, 2 event families, 1 error and the envelope at this change. CI runs `scripts/check_control_v1.py --check
  --require-base --github-event` on every leg of the `test` job (step "control-v1 floor
  ratchet", `.github/workflows/ci.yml`), and `scripts/preflight.py` runs `--check` and
  `tests/rpc/control_v1` for a change that touches `rpc/control*.py`, `rpc/rpc_mode.py`,
  `tests/rpc/control_v1/` or `docs/contracts/control-v1.md`.
- **The bites**: D8's five and fix round 1's, with their outputs.
- **Fix round 1's mutants** (`.omc/probes/623-624/fix-r1/F/mutants/`): 22 on the lines this
  round changed in `scripts/check_control_v1.py`, `rigs.py` and `test_input_coverage.py`, all
  killed. 20 are killed by the rule tests (`test_ratchet_rules.py`, `test_case_runner.py`,
  `test_additive_only.py`'s unit tests, `test_input_coverage.py`), three of them only after
  this round added the tests that kill them; the other two (the key set's closed fact dropped,
  and `allOf`'s recursion dropped, which its whole-subschema fact makes redundant) are killed
  by R1 and R2 against the committed floor.
- **The consumer side**: aelix-acp's native suite (a scratch clone at `16d6555b`,
  `HOME=<scratch> TMPDIR=<scratch> python scripts/test_native.py --core <this worktree>`) at
  `7d96de62`: `Ran 134 tests … OK (skipped=1)`. The skip is Windows-only
  (`test_forced_adapter_death_kills_owned_worker_and_descendant`)
  (`.omc/probes/623-624/impl/lane/acp-native-7d96de62.log`). The scout's run on `a1606f58` gave
  the same (`.omc/probes/623-624/scout/evidence/acp-native-a1606f58.log`).
- **Complexity** (ADR-0300; ruff 0.15.13): `RpcControlPlane._dispatch` 27 → 26, written to
  `complexity-baseline.json` with `scripts/check_complexity.py --update`;
  `RpcControlPlane.capabilities` 6 → 7 and `RpcControlPlane.cancel` 5 → 5, measured on a
  scratch copy of `a1606f58`'s `rpc/control.py` against this change.

## Where the build differs from the design draft

- **A fourth rig, `turn`** (D8): `full` with a model turn held open. The draft named `bare`,
  `full` and `child`; the idle refusals needed a rig of their own.
- **The enum value sets the floor states** are listed under D3 "As built". The draft's list of
  closed enums named `sessions/branch.kind`, the `scope` param, the `scopes` keys and
  `decidedBy`; the first two are param enums in the floor, and the last two are closed sets in
  `selections/list`'s cases since fix round 1 (D3).
- **Fix round 1 (Codex's implementation review, CX-5 to CX-9).** The draft's floor held a
  case's request and the facts of its schemas; it held no execution step, read neither `not`
  nor `anyOf`, and dropped a keyword it did not know. The build now digests every case and
  freezes the since-v1 ones as written (lead's suggestion, taken), states every step and
  assertion as a fact, refuses what it cannot state, leaves entries with no since-v1 case out
  of the v1 digest, validates open enums as open, states `decidedBy` and the `scopes` keys,
  and reads a `during` command's own answer before its cleanup (D3, D4, D8, D9).
- **`_families()`'s docstring and D9 say admission and routing are two steps.** The draft
  described the table as the way a new method is added; it is only the routing step.

## Alternatives considered

- **A dated amendment of ADR-0275** — rejected. ADR-0275 decided an envelope. This ADR decides
  what ADR-0275 never did: what may change inside version 1, what forces version 2, who the
  consumers are and the tests that hold both. Other records cite this rule as a rule (ADR-0305
  now, #612's ADR next), and a numbered ADR is a stable thing to cite; ADR-0198 set the
  precedent with the `--mode json` contract. ADR-0275 gets a dated note that states the rule,
  because #623's done-condition reads "ADR-0275에 호환 규칙이 있다".
- **Whole-response golden files** — rejected: results differ by host, by session and by what
  is installed, and a v1 client must accept extra fields.
- **Error codes now** — not in v1. A `code` field can be added later as an addition (D3).
- **A hand-written baseline file pinned by its hash, with additions outside it** (round 0) —
  replaced. Codex 4 showed that it protects the freeze commit and not a later addition, which
  a later change could delete together with its fixture. The generated floor with the
  merge-base ratchet protects both.
- **Freezing the 18 classic commands too** — not the owner's option (D1).

## Note (2026-11-19, #797): a bundled package's failed import is a compatible correction

With #797, a launch whose bundled `aelix-agents` or `aelix-status` cannot be imported answers
`resources/list` with an `extensionErrors` item `{"source": "<name> (bundled)", "message":
"<ExceptionType>: <text>"}`; before, the same launch answered `[]` (verify of #797, F4:
`rpc_broken_head.out`). `selections/list` with kind `extension` gains a row keyed
`bundled:<name> (bundled)`, role `placeholder`, lock `unavailable` with tag `failed`.

**Classification (the lead's call, 2026-11-19): a compatible correction, not a version 1 change.**
The item shape and the field's meaning, "a pack that did not load", are unchanged; `source` held
labels that are not paths before (`extensions/loader.py`), and a placeholder row with a `failed`
lock is a shape `selections/list` already served (its kind `tool`). What changed is that a bundled
pack's failure was omitted, which is the defect #797 reported. The selection row follows the same
reasoning; extending it there is fix round 1's reading, not a separate call. No fixture is added:
the contract fixtures run a launch whose bundled packages import.

Consequence, as the verify of #797 read aelix-acp `16d6555` (not run): `src/aelix_acp/runtime/extensions.py:81-88`
answers `"ready": not errors and …` after `/extension enable|disable`, so in such a launch it
reports `ready: false`, which is true there.

In the same change an RPC reply that UTF-8 cannot encode (a lone surrogate) is answered by its id
with an error instead of not at all (`rpc_mode._reply_bytes`; `tests/rpc/test_reply_encoding_797.py`),
inside the existing error envelope; a bundled error's text has its lone surrogates replaced by
U+FFFD first, so that launch still answers both lists. The floor check (`scripts/check_control_v1.py
--check --base 1c2a4a7c`) and `tests/rpc/control_v1` were run on this change.
