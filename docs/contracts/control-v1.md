# control-v1 — who consumes what

**Status: derived from aelix-acp `16d6555b`, and #612's rows from aelix-acp PR #39 at `03b9e32`;
confirmation pending.**

- **Who writes it.** Owner, 2026-11-18: "aelix-ai가 쓰고 ACP가 확인 (권장)". aelix-ai wrote this
  register by reading aelix-acp's source (lane #623/#624), and aelix-acp confirms it in review.
  Until a maintainer of aelix-acp does, every row below is "derived".
- **Corrections.** A confirmation or a correction is an edit of this file plus the matching
  fixture under `tests/rpc/control_v1/fixtures/`. `tests/rpc/control_v1/test_consumer_list.py`
  fails while the two disagree (the §3.1 rows, the §4 rows and §6 against the fixtures'
  `consumers`), while a path read here is not in `tests/rpc/control_v1/floor_v1.json`, and while
  §3.2 or §4.1 differ from the floor's `forwarded` paths.
- **The rules.** What governs this list (what an addition is, what a change is, when version 2
  is needed) is ADR-0302. The envelope itself is ADR-0275.

**Refs.**
- aelix-ai `a1606f58`. The freeze baseline is the commit this lands on (ADR-0302 D1).
- aelix-acp `16d6555b` (main, PR #21, 2026-10-31). It pins aelix-ai `e19ab55e` in
  `runtime-reference.json`.
- aelix-acp PR #39, commit `03b9e32` (based on aelix-acp main `11fb4fe`; #612's lane L3), for
  #612's rows only: the six `children/` methods, `ui/enable`'s `childApprovals`, the
  `childApproval` dialog, `aelix_child` and `aelix_notice`. It pins aelix-ai `83714045`.
- Aelix Studio `0285f5c3` (main).

aelix-acp paths are under `src/aelix_acp/` unless they start with `scripts/` or `docs/`. Every
aelix-acp line below was re-read at `16d6555b` on 2026-11-18, except in #612's rows, whose lines
were read at `03b9e32` on 2026-11-20 and say so; a #612 row's fixture names `03b9e32` as its
`ref`.

**How to read the rows.** A consumer uses a field in one of two ways:
- **direct**: its code names the path. The row gives that path and the file:line.
- **forwarded whole**: it passes a result or a frame on without naming the fields, through:
  - `_aelix/manage` (`server.py:275-325`, returning native `data` unchanged);
  - `_aelix/capabilities.runtime` (`server.py:271-274`);
  - a slash command's JSON output (`runtime/commands.py:77-79`);
  - the unparented `_aelix/subagent` notification (`session.py:530-531`);
  - a dialog frame (`session.py:631`);
  - at `03b9e32` also `_aelix/manage` before the session lock for five `children/` methods
    (`server.py:228-231`), and the `_aelix/notice` notification (`session.py:734-741`).

  Every field there is consumed, by Studio or by an ACP client, even though no aelix-acp code
  names it. Its paths are the floor's `forwarded` set for that method: every path
  `scripts/check_control_v1.py --update` saw in the real answers of the fixtures' success cases,
  run in process on their rigs (`tests/rpc/control_v1/rigs.py`). §3.2 and §4.1 list them.

## 1. Who the consumers are

| Consumer | How it reaches control-v1 | Status |
|---|---|---|
| aelix-acp | Launches `aelix --mode rpc --offline --no-extensions …` (`session.py:169-170`), writes `{"id", "type":"aelix_control", "method", "params"}` (`session.py:396`), and reads responses and events (`session.py:426-446`) | derived, confirmation pending |
| Aelix Studio | No direct call in product code. It reaches control-v1 through aelix-acp's `_aelix/manage` passthrough (`server.py:275-325`) and `_aelix/capabilities` (`server.py:271-274`), which return native `data` unchanged, and through `_aelix/subagent` and dialog forwards (§4) | derived from aelix-acp + a Studio search, confirmation pending |
| Studio preview harness | Matches one request frame: `{"type":"aelix_control","method":"permissions/set","params":{"mode":"default"}}` (Studio `scripts/studio-preview/fixtures/nativeFailure.py:39-41`; not re-read in this lane, which had no Studio clone) | derived |

Not consumers today:
- aelix-studio-host reads aelix-acp's `_meta["aelix/subagents"]`, not native events.
- aelix itself is not a control-v1 client on `a1606f58`. #123's W5 may make it one; it is then
  added here.

## 2. The envelope (every method)

| What | Shape the consumer relies on | Where |
|---|---|---|
| Request | `{"id": <str>, "type": "aelix_control", "method": <str>, "params": <object>}` | `session.py:396` |
| Response | `type == "response"`, `id` echoed, `success` (bool), `data` (object; ACP reads `data or {}`), `error` (string), and `command` (read for its error label) | `session.py:437-444` |
| Errors | Free text. ACP reads only `success`/`data`/`error`, and every other error becomes `-32603 backend_error` | `session.py:443-444`, `wire/errors.py:25-27` |
| Error text matched | The substring `already open in another terminal`, case-insensitive. ACP matches it in any response `error` (`session.py:441-442`) and in a refused launch's stderr (the stderr tail, `session.py:304`). It is produced by aelix-ai `aelix_agent_core/runtime/agent_session_runtime.py:685` (an RPC error) and by `cli/entry.py:5767` (`_session_owned_message`, which a non-interactive launch prints to stderr at `:5899`). `cli/entry.py:5801` is the TUI's interactive prompt and is not read by any consumer. | `wire/errors.py:12-14`, `:42-44` |
| Frame size | ACP reads lines up to 16 MiB | `session.py:49`, `:211-214` |
| Unknown frames | A stdout line that is not JSON is ignored; an object with an unknown `type` goes to the event handler, which ignores unknown kinds | `session.py:428-435`, `:446`, `:463-464` |
| Capability gate | ACP checks `capabilities.methods` before most calls (`session.py:239-253`, `:320`; `runtime/commands.py:82`; `runtime/controls.py:173`, `:186`; `server.py:81`, `:304`), but not before `resources/list` (`session.py:322`, `:328`, `:342`; `runtime/extensions.py:81`) or `agents/show` (`session.py:353`). So those two must stay served wherever ACP runs. | — |

The envelope is `tests/rpc/control_v1/fixtures/envelope.json`, asserted on every answer of every
case in `test_contract.py` and `test_real_child.py`. The locked-session text is
`fixtures/error.session_locked.json`: a branch back onto a session a second holder owns (in
process), a launch onto a session a running child owns (a real child), and a check that the two
producers still hold the text.

## 3. Methods

### 3.1 What each consumer sends and reads

"Sends" is what ACP puts in `params`. "Direct reads" is what its code reads from `data`: each
item is a path, then where. "Forwarded whole through" says through what the whole result also
reaches Studio or an ACP client.

| Method | Sends | Direct reads | Forwarded whole through | Notes |
|---|---|---|---|---|
| `capabilities` | `{}` (`runtime/controls.py:60`, `:157`) | `version` (must be `1`: `session.py:236-238`); `methods[]` (the gate, §2); `thinkingLevels[]` (`runtime/controls.py:29`); `activeAgent` (`session.py:349`, `:370`) | `_aelix/capabilities.runtime`, the whole object (`server.py:271-274`) | `version ≠ 1` → ACP refuses the session with `-32004 runtime_control_required` |
| `ui/enable` | `{"enabled": true}`, only when the ACP client set `_meta["aelix/clientActions"]` and `methods` has it (`session.py:282-284`); at `03b9e32` also `"childApprovals": true` when `methods` lists `children/list` (`session.py:118-124`, `:399-400`) | — | — | at `03b9e32` the answer is not read |
| `agents/use` | `{"name": <str>}` (`session.py:257`; `runtime/commands.py:175`; manage) | — | manage (`server.py:309`); slash output | a failure on restore becomes `-32004 profile_unavailable` |
| `agents/show` | `{"name": <str>}` (`session.py:353`; `runtime/commands.py:175`) | `profile.tools` (null means every tool: `session.py:353-354`) | manage; slash output | `profile` is `asdict(AgentProfile)` (`rpc/control.py:857`): every field name is a wire name (ADR-0302 D6) |
| `agents/list` | `{}` (`runtime/commands.py:173`) | — | manage; slash output | `diagnostics[]` is `asdict` (`rpc/control.py:852`) |
| `agents/run` | `{"name", "task"}` (`runtime/commands.py:181-183`) | — | manage; slash output | ACP's doc names `allowProject?` (`docs/studio-integration.md:94`), which native refuses since #594 (`rpc/control.py:869-873`): a consumer-side mismatch, recorded, not drift. `result` is `asdict` (`rpc/control.py:910`). |
| `resources/list` | `{}` | `tools[].name` (`session.py:323`, `:330`, `:343`); `activeTools` (`session.py:329`); `extensionErrors` (`runtime/extensions.py:82`); `extensions[].source`, `extensions[].name` (`runtime/extensions.py:192-195`) | manage; `/tools`, `/skills`, `/hooks`, `/mcp` output the arrays `tools`, `skills`, `hooks`, `mcp` whole (`runtime/commands.py:145-152`) | |
| `resources/reload` | `{}` (`runtime/commands.py:141`; `runtime/extensions.py:52-53`) | — | manage; slash output | cancelled with `commands/cancel` on timeout (`session.py:403-407`) |
| `tools/set_active` | `{"names": [<str>…]}`, also `[]` (`session.py:325`, `:335`, `:339`, `:345`; `runtime/commands.py:147`) | `activeTools` (`runtime/commands.py:148`; `server.py:311-312`) | manage; slash output | |
| `mcp/configure` | `{"servers": [{"name", "type"?: "stdio"\|"http"\|"sse", "command", "args": [<str>], "env": [{"name", "value"}], "url", "headers": [{"name", "value"}]}]}`, with a 60 s timeout (`session.py:337`) | — | manage (through `session.configure_mcp`, `server.py:306-307`) | on failure ACP restores the tool selection (`session.py:338-340`) |
| `mcp/status` | `{}` | — | manage (`server.py:309`) | |
| `permissions/set` | `{"mode": "default"\|"yolo"}` (`runtime/controls.py:179`) | `mode` (must equal the request: `runtime/controls.py:180-183`) | — | Studio's harness frame above |
| `permissions/get` | `{}` (`runtime/controls.py:187`) | `mode` (`runtime/controls.py:187`) | manage | |
| `sessions/branch` | `{"kind": "clone"}` or `{"kind": "fork", "entryId": <str>}` (`runtime/commands.py:119-126`; `server.py:83`) | `cancelled` (`server.py:85`); `sessionFile` (must be an existing file: `server.py:87-89`); `title` (`server.py:97`) | — | |
| `settings/get` | `{}` (`runtime/commands.py:155`, `:165`) | `settings.enabled_models` (`runtime/commands.py:165`) | manage; `/settings` output | |
| `settings/set` | `{"key": <str>, "value": <json>}` for any key; `enabled_models` with a list (`runtime/commands.py:160`, `:163`) | — | manage; slash output | |
| `auth/login`, `auth/logout` | `{"provider": <str>}` (`runtime/commands.py:166-167`) | — | manage; slash output | `auth/login` opens dialogs (§4) and is cancelled on timeout (`session.py:403-407`) |
| `auth/status` | `{}` or `{"provider": <str>}` | — | manage only (`server.py:303-310`) | |
| `auth/set_api_key` | `{"provider": <str>, "key": <str>}` | — | manage only | answers `auth/status` for that provider |
| `extensions/list` | `{}` | — | manage only | no native test covered it before #623 |
| `commands/run` | `{"text": <str>}` (`runtime/commands.py:200`) | `outcome` (`prompt`, `not_a_command` and `error` are matched, any other value is treated as handled: `runtime/commands.py:203-208`); `prompt` (`runtime/commands.py:204`); `messages[].text` (`runtime/commands.py:201-202`) | slash output (the messages) | cancelled on timeout (`session.py:403-407`) |
| `commands/cancel` | `{}` (`session.py:406`, `:813`) | — | — | |
| `children/list` (#612) | at `03b9e32`, the client's `arguments`, unchanged (`server.py:228-231`, `:245-253`; `session.py:1218-1243`) | `epoch` (a newer one retires the older epoch's routes and voids its open child questions: `session.py:1234-1235`, `:891-896`) | manage before the session lock, so it is answered during `session/prompt` and `agents/run` (`server.py:228-231`) | 60 s, no cancel (`session.py:1228`); refused `-32000` with `data.kind: "session_changing"` while the worker is being replaced or ended, answered `-33002` when the worker's close ends it unanswered, `-32603` when the worker died first (ADR-0307's note of 2026-11-20) |
| `children/events` (#612) | at `03b9e32`, the client's `arguments`, unchanged (`server.py:228-231`, `:245-253`; `session.py:1218-1243`) | `epoch` (as for `children/list`: `session.py:1234-1235`) | manage before the session lock, so it is answered during `session/prompt` and `agents/run` (`server.py:228-231`) | 60 s, no cancel (`session.py:1228`); refused `-32000` with `data.kind: "session_changing"` while the worker is being replaced or ended, answered `-33002` when the worker's close ends it unanswered, `-32603` when the worker died first (ADR-0307's note of 2026-11-20) |
| `children/steer`, `children/wrap_up`, `children/stop` (#612) | at `03b9e32`, the client's `arguments`, unchanged (`server.py:228-231`, `:245-253`; `session.py:1218-1243`) | — | manage before the session lock, so it is answered during `session/prompt` and `agents/run` (`server.py:228-231`) | 60 s, no cancel (`session.py:1228`); refused `-32000` with `data.kind: "session_changing"` while the worker is being replaced or ended, answered `-33002` when the worker's close ends it unanswered, `-32603` when the worker died first (ADR-0307's note of 2026-11-20) |
| `children/notices` (#612) | at `03b9e32`, `{}` (`session.py:1407`) | — | manage, under the session lock, with the turn's `stopReason` merged in (`server.py:462-463`; `session.py:1388-1418`) | run as a prompt turn: the turn timeout, `session/cancel` (the answer is then `{"stopReason": "cancelled"}` alone); a failure after it was sent is `-33002` where aelix-acp's `after_dispatch` maps it (a `-32603` or `-32001` failure: `wire/errors.py:77-94`), and one with a code of its own keeps it, so a `get_state` timeout stays `-32007`; `-32000` "Session is busy" during a turn (`server.py:438-439`) |
| `permissions/evaluate` | not sent | — | — | named only in `READ_ONLY_CONTROLS` (`session.py:45-47`); frozen like every method (ADR-0302 D3), listed in §6 |

`_aelix/manage`'s allowlist (`server.py:303`) is 17 actions:
- `resources/list`, `resources/reload`;
- `settings/get`, `settings/set`;
- `tools/set_active`;
- `mcp/status`, `mcp/configure`;
- `auth/status`, `auth/set_api_key`, `auth/login`, `auth/logout`;
- `extensions/list`;
- `agents/list`, `agents/show`, `agents/use`, `agents/run`;
- `permissions/get`.

At `03b9e32` (PR #39) it is 23: these, `children/notices`, and the five `children/` methods that
are sent before the session lock (`server.py:40-43`). The five are also gated on
`capabilities.methods` and answered with native `data` unchanged, with 60 s and no cancel
(`server.py:251-252`; `session.py:1228`). `children/notices` differs: its answer has the turn's
`stopReason` merged in, and it is bounded by the turn timeout, not the 60 s
(`session.py:1413-1418`; its §3.1 row).

Each of the 17 is also gated on `capabilities.methods` (`server.py:304`). Each is answered with native
`data` unchanged. For the actions outside `auth/login`, `resources/reload` and `agents/run`,
the timeout is 60 s with no cancel (`server.py:309-310`).

`_aelix/manage` refuses with "Session is busy" whenever the session's `mutation_lock` is held
(`server.py:279-280`), and aelix-acp `16d6555b` holds that lock for the whole of a
`session/prompt` (`server.py:248-255`). So during a turn Studio gets "Session is busy" from
`_aelix/manage` before any of these actions reaches aelix-ai. aelix-acp PR #39 (`03b9e32`, #612's
lane L3) sends `children/list`, `children/events`, `children/steer`, `children/wrap_up` and
`children/stop` before that lock and the activity lease (`server.py:228-231`, `Server.intervene`
`:245-253`), so they are answered during `session/prompt` and `agents/run`; `children/notices`
stays under the lock (ADR-0307 D8 and its note of 2026-11-20). Under `16d6555b` none of the
`children/` methods reaches aelix-ai through aelix-acp.

### 3.2 Forwarded field paths

These are the floor's `forwarded` paths for each method that is forwarded whole, as
`scripts/check_control_v1.py --update` wrote them into `tests/rpc/control_v1/floor_v1.json`: every
path seen in the real answers of the method's success cases, run in process on their rigs.
`[]` marks an array's items. `test_consumer_list.py` fails while this table and the floor differ.
A path that no fixture's rig produces is not here: `skippedTools[]`, for one, stays empty in
every case.

| Method | Paths |
|---|---|
| `agents/list` | `diagnostics`, `diagnostics[]`, `diagnostics[].code`, `diagnostics[].message`, `diagnostics[].path`, `diagnostics[].type`, `profiles`, `profiles[]`, `profiles[].decidedBy`, `profiles[].description`, `profiles[].enabled`, `profiles[].filePath`, `profiles[].name`, `profiles[].scope` |
| `agents/run` | `result`, `result.changed_files`, `result.details`, `result.dropped_lines`, `result.dropped_tools`, `result.elapsed_ms`, `result.error`, `result.exit_code`, `result.files_overflow`, `result.id`, `result.model`, `result.note`, `result.ok`, `result.output_recorded`, `result.permission_mode`, `result.profile`, `result.provider`, `result.reason`, `result.session_id`, `result.session_path`, `result.started`, `result.status`, `result.stop_reason`, `result.summary`, `result.tool_trail`, `result.truncated`, `result.unconfirmed_files`, `result.usage`, `result.usage.cache_read`, `result.usage.cache_write`, `result.usage.cost`, `result.usage.input`, `result.usage.output`, `result.usage.tokens`, `result.usage.turns` |
| `agents/show` | `profile`, `profile.agents`, `profile.approval_mode`, `profile.body`, `profile.builtin_tools`, `profile.context_files`, `profile.description`, `profile.exclude_tools`, `profile.extensions`, `profile.file_path`, `profile.inherit_extensions`, `profile.inherit_skills`, `profile.model`, `profile.name`, `profile.output_cap`, `profile.provider`, `profile.role`, `profile.scope`, `profile.skills`, `profile.system_prompt`, `profile.thinking`, `profile.timeout_ms`, `profile.tools`, `profile.tools[]` |
| `agents/use` | `message` |
| `auth/login` | `providers`, `providers[]`, `providers[].configured`, `providers[].provider`, `providers[].source` |
| `auth/logout` | `providers`, `providers[]`, `providers[].configured`, `providers[].provider`, `providers[].source` |
| `auth/set_api_key` | `providers`, `providers[]`, `providers[].configured`, `providers[].provider`, `providers[].source` |
| `auth/status` | `providers`, `providers[]`, `providers[].configured`, `providers[].provider`, `providers[].source` |
| `capabilities` | `activeAgent`, `methods`, `methods[]`, `permissionModes`, `permissionModes[]`, `selectionKinds`, `selectionKinds[]`, `sessionLockErrors`, `subagentsEnabled`, `thinkingLevels`, `thinkingLevels[]`, `version` |
| `children/events` | `epoch`, `events`, `events[]`, `events[].data`, `events[].data.args`, `events[].data.background`, `events[].data.callId`, `events[].data.epoch`, `events[].data.id`, `events[].data.jobId`, `events[].data.name`, `events[].data.parentToolCallId`, `events[].data.profile`, `events[].data.seq`, `events[].data.sessionId`, `events[].data.state`, `events[].data.status`, `events[].data.text`, `events[].data.wrappingUp`, `events[].event`, `gap`, `lowSeq`, `nextSeq` |
| `children/list` | `children`, `children[]`, `children[].activity`, `children[].activity[]`, `children[].activity[].args`, `children[].activity[].name`, `children[].activity[].status`, `children[].approval`, `children[].approval.callId`, `children[].approval.toolName`, `children[].background`, `children[].cost`, `children[].currentTool`, `children[].elapsedMs`, `children[].end`, `children[].id`, `children[].jobId`, `children[].lastText`, `children[].lowSeq`, `children[].model`, `children[].parentToolCallId`, `children[].profile`, `children[].provider`, `children[].seq`, `children[].sessionId`, `children[].state`, `children[].stuck`, `children[].tokens`, `children[].wrappingUp`, `epoch` |
| `children/notices` | `cancelled`, `method`, `placed`, `reason`, `remaining`, `started` |
| `children/steer` | `ended`, `outcome`, `phase`, `reason`, `verb` |
| `children/stop` | `state` |
| `children/wrap_up` | `ended`, `outcome`, `phase`, `reason`, `verb` |
| `commands/run` | `command`, `messages`, `messages[]`, `messages[].text`, `messages[].type`, `outcome`, `prompt` |
| `extensions/list` | `extensions`, `extensions[]`, `extensions[].distribution`, `extensions[].entry`, `extensions[].name`, `extensions[].version` |
| `mcp/configure` | `servers`, `servers[]`, `servers[].clientName`, `servers[].connected`, `servers[].name`, `servers[].transport`, `skippedTools` |
| `mcp/status` | `servers`, `servers[]`, `servers[].clientName`, `servers[].connected`, `servers[].name`, `servers[].transport`, `skippedTools` |
| `permissions/get` | `mode` |
| `resources/list` | `activeTools`, `activeTools[]`, `commands`, `commands[]`, `commands[].description`, `commands[].name`, `extensionErrors`, `extensionErrors[]`, `extensionErrors[].message`, `extensionErrors[].source`, `extensions`, `extensions[]`, `extensions[].name`, `extensions[].source`, `hooks`, `hooks[]`, `hooks[].events`, `hooks[].events[]`, `hooks[].extension`, `mcp`, `mcp[]`, `mcp[].clientName`, `mcp[].connected`, `mcp[].name`, `mcp[].transport`, `projectTrusted`, `prompts`, `prompts[]`, `prompts[].description`, `prompts[].name`, `skills`, `skills[]`, `skills[].description`, `skills[].filePath`, `skills[].name`, `tools`, `tools[]`, `tools[].description`, `tools[].name` |
| `resources/reload` | `cancelled` |
| `settings/get` | `schema`, `schema[]`, `schema[].choices`, `schema[].choices[]`, `schema[].key`, `schema[].requiresReload`, `schema[].scope`, `schema[].type`, `settings`, `settings.agent_profiles`, `settings.check_for_updates`, `settings.check_model_catalog`, `settings.compaction_enabled`, `settings.default_model`, `settings.default_provider`, `settings.default_thinking_level`, `settings.enabled_models`, `settings.follow_up_mode`, `settings.hide_compaction_summary`, `settings.hide_thinking_block`, `settings.prompt_template_paths`, `settings.respect_gitignore`, `settings.retry_enabled`, `settings.skill_paths`, `settings.skill_paths[]`, `settings.steering_mode` |
| `settings/set` | `key`, `requiresReload`, `value`, `value[]` |
| `tools/set_active` | `activeTools`, `activeTools[]` |

## 4. Events

| Event | Path | Direct reads | Forwarded whole through | aelix-acp `16d6555b` (#612's rows: `03b9e32`) |
|---|---|---|---|---|
| `aelix_subagent` | with a parent `agent` tool call open | `event` (∈ `subagent_start`, `subagent_tool`, `subagent_end`); `data.id` (required); `data.profile`, `data.state`, `data.current_tool`, `data.model`, `data.provider`, `data.elapsed_ms`, `data.tokens`, `data.cost` (each bounded and type-checked into `_meta["aelix/subagents"]` on the parent's `tool_call_update`, at most 32 children per call) | — | `session.py:515-516`, `:524-536`; `runtime/subagents.py:17-41`, `:62-79` |
| `aelix_subagent` | no parent, or rejected by the tracker (`/agents run`, an unknown `event` value, data without a string `id`) | — | `_aelix/subagent`, as `{"jsonrpc": "2.0", "method": "_aelix/subagent", "params": {"sessionId", "event", "data"}}`, unbounded and unchecked, so every `data` field is consumed | `session.py:526-531`; `runtime/subagents.py:35-37`, `:66-69` |
| `extension_ui_request` | `authProgress` | — | `_aelix/notification`, the whole frame | `session.py:620-623` |
| `extension_ui_request` | `notify`, `setStatus`, `setWidget`, `setTitle` | `method` (which branch: `session.py:619`); `message`, `text` (read and shown as an agent message chunk: `session.py:624-628`) | — | `session.py:624-628` |
| `extension_ui_request` | any other method | — | the dialog client action (`server.client_action(self, "dialog", event)`), the whole frame; an unknown method is shown as a dialog too | `session.py:629-632` |
| `extension_ui_request` | `childApproval` (#612, ADR-0308): a delegated child's approval request | `id` (the answer's id); `title` (shown only to a client that did not declare `aelix/nestedToolCalls`); `child.id`, `child.epoch`, `child.cwd`, `child.parentToolCallId`, `child.profile`, `child.jobId`, `child.background`; `tool.callId`, `tool.name`, `tool.args`; `choices[].kind`, `choices[].id` (each type-checked into one `session/request_permission`; only an offered kind becomes an option, and only a selected offered option is answered with its choice's `id`) | — | `03b9e32`: `session.py:750`, `:871-878`, `:1020-1022`, `:1032-1092`, sent only after `ui/enable` with `childApprovals: true` (§3.1). Under `16d6555b` it is never sent: aelix-acp does not send `childApprovals` (`session.py:282-284`), and the row above would forward it as a dialog |
| `aelix_child` | any (#612, ADR-0307) | `event` (one of the seven; any other drops the frame); `data.id`, `data.seq`, `data.epoch` (the route's key and its dedupe: `runtime/children.py:41-49`); `data.parentToolCallId` (on `state`, the card the child's frames reach: `runtime/children.py:141-162`); `data.profile`, `data.state`, `data.wrappingUp`, `data.text`, `data.stuck`, `data.requested`, `data.end` (onto the child's entry in the parent card's `_meta["aelix/subagents"]`, for every client); `data.callId`, `data.name`, `data.args`, `data.status`, `data.step` (a child's own call: the entry's `currentTool`, a nested tool call for a client that declared `aelix/nestedToolCalls`, and a `withdrawn` or `denied` step ends an open question) | — | `03b9e32`: `session.py:732-733`, `:800-869`, `:898-915`; `runtime/children.py`. Under `16d6555b`, dropped: `Session.event` has no branch for it (`session.py:463-522`) |
| `aelix_notice` | any (#612, ADR-0307) | — | `_aelix/notice`, as `{"jsonrpc": "2.0", "method": "_aelix/notice", "params": {"sessionId", "event", "data"}}`, only to a client that declared `clientCapabilities._meta["aelix/notices"]` (a `data` that is not an object is sent as `{}`); not recorded | `03b9e32`: `session.py:734-741`. Under `16d6555b`, dropped: `Session.event` has no branch for it (`session.py:463-522`) |
| `aelix_input` | #865 (ADR-0310), §8 | — | — | Intended for ACP #41 phase 2 |
| `extension_ui_response` (client → host) | ACP writes `{"type": "extension_ui_response", "id", …answer}`: the client's whole dialog reply (`value`, `confirmed`, `cancelled`), or `{"cancelled": true}` | — | — | `session.py:629-636`; the write at `:635` |

The fixtures are `tests/rpc/control_v1/fixtures/event.aelix_subagent.json` (frames emitted while an
`agents/run` is their parent, and one emitted with no parent and only an `id`; all nine `data`
keys are required in both) and `fixtures/event.extension_ui.json` (`select`, `confirm`, `input`,
`editor`, `notify`, `setStatus`, `setWidget`, `setTitle`, `auth/login`'s `authProgress`, `oauth`,
`oauthSelect` and `input`, and a reply reaching the command; `confirm` is replayed on a real
child).
#612 adds `fixtures/event.aelix_child.json` (one case per `event` value, and the receipt's
`state` frame, a wrap-up taken back and the `no_result` ends of a run's open calls),
`fixtures/event.aelix_notice.json`, and three `childApproval` cases in
`fixtures/event.extension_ui.json` (answered `yes`, cancelled, and answered with a label).

### 4.1 Forwarded frame paths

The floor's `forwarded` paths for each event family: every path seen in the frames the fixture
cases produced, run in process. `test_consumer_list.py` fails while this table and the floor
differ.

| Frame | Paths |
|---|---|
| `aelix_subagent` | `data`, `data.cost`, `data.current_tool`, `data.elapsed_ms`, `data.id`, `data.model`, `data.profile`, `data.provider`, `data.state`, `data.tokens`, `event`, `type` |
| `aelix_notice` | `data`, `event`, `type` |
| `extension_ui_request` | `child`, `child.background`, `child.cwd`, `child.epoch`, `child.id`, `child.jobId`, `child.parentToolCallId`, `child.profile`, `child.sessionId`, `choices`, `choices[]`, `choices[].id`, `choices[].kind`, `choices[].label`, `id`, `instructions`, `key`, `kind`, `lines`, `lines[]`, `message`, `method`, `options`, `options[]`, `options[].id`, `options[].label`, `placeholder`, `placement`, `prefill`, `text`, `title`, `tool`, `tool.args`, `tool.args.command`, `tool.callId`, `tool.kind`, `tool.name`, `type`, `url` |

## 5. Classic commands — observed, not frozen

aelix-acp also sends 18 commands of the classic RPC roster. They are NOT part of control-v1. By
the owner's decision on the freeze scope (2026-11-17, "메서드 + 이벤트 2종, 기준 = 현재 main
(권장)"), they are recorded here only as observed use; ADR-0302 D7 says what governs them. Found
with `grep 'rpc("'` over `src/aelix_acp/`:

- `abort`, `compact`, `export_html`;
- `get_available_models`, `get_commands`, `get_fork_messages`, `get_last_assistant_text`,
  `get_messages`, `get_session_stats`, `get_state`;
- `prompt`, `set_model`, `set_session_name`, `set_thinking_level`;
- `set_steering_mode`, `set_follow_up_mode`, `set_auto_compaction`, `set_auto_retry`
  (`runtime/controls.py:148-154`).

### 5.1 Stored message linkage (#655, ADR-0309)

On a capable CLI-RPC binding, delivered `message_start`, `message_update`,
`message_end`, `tool_execution_start`, `tool_execution_update` and
`tool_execution_end` frames may include a top-level `transcript` object.
Its base fields are `nativeSessionId`, `bindingId` and `streamId`.
The tool execution frames use the stream ID reserved for that occurrence's
toolResult. Keep the native IDs opaque and scope each stream to its binding.
Placed notices contribute no transcript occurrence (ADR-0309 D1–D3).

A delivered end adds `status`. `stored` includes `entryId`,
`parentEntryId`, `sequence` and `replaced` when the same decoder and path
rules used by `transcript/list` can return that row on its own tip after
settlement and the binding has validated the file's readability and header
once. `replaced` identifies a different object
accepted by the end reducer. `storageOnly`, `notStored` and `unknown` omit
those four fields. They describe confirmed but unreturnable storage,
definite non-write and ambiguous current I/O, respectively. Cancellation
and delivery loss can leave a stream without an end. Live payload fields
continue to carry the classic observation. The transcript fixture checks
all four outcomes and the six event types.

When the low-level storage API writes a leaf marker before its target, later
receipts on that path are `storageOnly` even if its rows read back, and reopening
restores `stored` (ADR-0309 Limits, a write order the product's writers avoid).
A session file made unreadable after the binding validated it can still receive
`stored` receipts that fail readback.

## 6. Served, not yet consumed

These are part of version 1: they are in `tests/rpc/control_v1/floor_v1.json`, protected like
every other entry (ADR-0302 D3, D8), but no consumer reads them yet. A row moves to §3 when a
consumer starts reading it. Methods added later under ADR-0302 D3 are listed here first, the
same way. `test_consumer_list.py` fails while this table and the fixtures with no `consumers`
differ.

#612's rows (the six `children/` methods, `ui/enable`'s `childApprovals` and the `childApproval`
dialog, `aelix_child` and `aelix_notice`) moved to §3.1, §3.2, §4 and §4.1 on 2026-11-20: aelix-acp
PR #39 (`03b9e32`) reads them.

| Method or field | Added by | Expected consumer | Fixture |
|---|---|---|---|
| `permissions/evaluate` | #594 | none (aelix-acp names it only in `READ_ONLY_CONTROLS`) | `permissions.evaluate.json` |
| `selections/list`, `selections/set` (and `capabilities.selectionKinds`) | #624 (ADR-0305), in the freeze commit itself | Studio #7, through aelix-acp's `_aelix/manage`. Its allowlist (`server.py:303`) and `READ_ONLY_CONTROLS` (`session.py:45-47`) do not list them yet. When aelix-acp adds `selections/set`, its default 60 s timeout is short: the consent dialog alone is bounded at 45 s (`rpc/control_selection.py` `CONSENT_TIMEOUT_SECONDS`), and settings-lock contention can add 30 s (ADR-0305 D4). So the request is to give it at least 90 s, or to send `commands/cancel` with its `requestId` on timeout. | `selections.list.json`, `selections.set.json`, `capabilities.json` |
| `transcript/list` | #655 (ADR-0309) | aelix-acp #22 for CLI-to-Studio replay | `transcript.list.json` |
| `inputs/observe` | #865 (ADR-0310) | ACP #41 phase 2 for CLI-to-Studio user rows | `inputs.observe.json` |

### 6.1 `transcript/list`

Use `aelix_control` with `method:"transcript/list"` after verifying
`capabilities.version === 1` and membership in `capabilities.methods`.
Discovery requires a writer-owned persistent CLI-RPC installation whose
binding coordinates fit an empty reply. Memory, sessionless, unowned,
foreign read-only, stale and WebSocket bindings omit the method. Rediscover
after a binding change (ADR-0309 D5, `test_floor.py` and
`test_an_unencodable_header_keeps_classic_events_and_hides_transcript`).

| Parameter | Selection |
|---|---|
| Omitted `leafId` | Capture the active leaf. |
| `leafId` | Inclusive, nonempty entry ID, or null for the empty root. |
| `firstSequence` | Inclusive integer from 1 through `totalMessages+1`. Default 1. |
| `limit` | Integer from 1 through 100. Default 50. |
| `cursor` | Nonempty URL-safe ASCII continuation of at most 256 characters. Only `limit` may accompany it. |

Unknown keys, booleans or noninteger counts, missing tips and pruned tips
receive correlated errors. A successful page has `bindingId`,
`nativeSessionId`, `leafId`, `totalMessages`, `entries` and `nextCursor`.
Null cursor means exhausted. The cursor pins the binding, selected tip,
ordinal and recovery state. Refuse stale cursors on rebind, restart or a
change of recovery state. A reader can retry a cursor or restart with a
fresh selector. Recovery is signalled by `recovered:true` on each page of
the damaged source snapshot (ADR-0309 D2 and D4, transcript fixture).

Rows are oldest first. Each row has `entryId`, `parentEntryId` and
one-based `sequence` on the selected physical parent path. `message` is
the decoded native stored user, assistant or toolResult payload, including
content blocks and native snake_case fields. The parent can be metadata.
Compaction retains original rows, and withdrawal can leave them on an old
tip. If the strict codec fails, the row retains its coordinates and role
with `unavailable:"unencodable"`. Later rows continue normally. Oversized
encodable rows come in one-part pages as `messagePart:{offset,totalBytes,base64}`.
The bytes use compact UTF-8 JSON with sorted keys and standard padded
base64. Verify contiguous offsets, stable coordinates and length before
assembling the payload. Stage until the complete pinned scan succeeds
(ADR-0309 D2 and D4, transcript fixture).

Supply a nonempty scalar string request ID with at most 1,024 bytes in
ASCII-escaped quoted JSON. For admitted IDs, every envelope, including
its LF, is at most 1,048,576 UTF-8 bytes. A part contains at most 196,608
raw bytes. If the smallest required row or part cannot fit, the method
refuses with a bounded error. A read performs zero session writes.
`settlementPending` means writer settlement is still outstanding for this
file, including after switching away and back in this process.
`invalidTree` identifies malformed selected ancestry or duplicate decoded
entry IDs (ADR-0309 D2–D4,
`test_transcript_655.py`).

## 7. Changes since aelix-acp's pin, accepted as v1

aelix-acp pins aelix-ai `e19ab55e`. Between that pin and the freeze baseline:
- eight behaviours changed (D1–D8 in ADR-0302 D6);
- `agents/show.profile` gained two `asdict` fields, `exclude_tools` and `agents` (ADR-0302 D6's
  D9).

The owner took main at freeze time as the baseline on 2026-11-17 ("메서드 + 이벤트 2종, 기준 =
현재 main (권장)"), so all of them are v1. aelix-acp's native suite passed against `a1606f58`:
134 tests, with 1 Windows-only skip (the scout's log,
`.omc/probes/623-624/scout/evidence/acp-native-a1606f58.log`).

aelix-acp PR #39 (`03b9e32`) moves the pin to aelix-ai `83714045`, after the freeze baseline. Its
native suite passed against that commit: `scripts/test_native.py --check-ref`, 292 tests, with 1
Windows-only skip, macOS (`.omc/probes/612/l3/lead/native-03b9e32.txt`).

### Session-lock errors on opted-in stdio (#868, ADR-0316)

An artifact-qualified launcher passes `--rpc-session-lock-errors=<32 lowercase hex digits>`
with a fresh nonce for each worker. A Studio-managed launch also passes
`--session-writer-kind=studio` to declare the writer's origin. Ordinary native RPC starts
with unknown origin.

On true startup contention the process exits 1 with zero stdout bytes. After the
complete existing stderr diagnostic it emits one final ASCII line, at most 256
bytes including the literal LF. This frame shows a Studio holder.

```text
AELIX_RPC_STARTUP_ERROR/1 {"nonce":"581b49ec167bd24c36378b26ffcb9a3b","kind":"session_locked","holderKind":"studio"}
```

The launcher checks the nonce, exit, both EOFs and empty stdout, and requires one
reserved-prefix line at the end. A POSIX newline in a session path can put that
prefix in the preceding diagnostic. Ambiguous streams take the ordinary startup
failure path and retain the diagnostic. An unqualified worker continues to use
the existing text signal.

After a successful opted-in stdio start, `capabilities.data.sessionLockErrors`
is the integer `1`. The corresponding fields are absent for unopted stdio
and WebSocket. An opted-in classic switch refusal retains the error string and
adds a top-level sibling.

```json
{"type":"response","id":"s1","command":"switch_session","success":false,"error":"session is already open in another terminal: <target>","errorDetails":{"kind":"session_locked","holderKind":"cli"}}
```

For `sessions/branch`, only contention during the explicit return switch adds
the detail. The error still identifies the created branch and current binding.

```json
{"type":"response","id":"b1","command":"aelix_control","success":false,"error":"Created branch <branch>, but returning to <original> failed (session is already open in another terminal: <original>); the runtime is now on <branch>","errorDetails":{"kind":"session_locked"}}
```

The producer omits `holderKind` when it cannot verify a known holder. On
Windows in beta.3 all holders are unknown under ADR-0316. The startup frame
keeps the nonce and contention kind, and switch or branch-return errors carry
`"errorDetails":{"kind":"session_locked"}`. The Windows real-child test
`test_windows_marked_writer_keeps_generic_contention` covers startup and switch.

```text
AELIX_RPC_STARTUP_ERROR/1 {"nonce":"581b49ec167bd24c36378b26ffcb9a3b","kind":"session_locked"}
```

A consumer treats future string values as unknown. The ACP client opts into
non-null `holder` using integer `1` at
`initialize.clientCapabilities._meta["aelix/sessionLockHolder"]`. The adapter
matches typed replies by the pending request's ID and command. The existing
`-33001` error retains its shape, including null holder for unknown or unopted
clients. A branch failure or uncertain outcome requires settlement and an
original-binding readback before subsequent writes on the same worker.
## 8. Input observation on CLI stdio (#865)

An eligible CLI stdio host advertises `inputs/observe` in version 1
`capabilities.methods`. It uses the same stable, writer-owned JSONL binding
as `transcript/list` and requires runtime support for the binding-transition
fence. Server WebSocket and sessions without that binding omit the method.
A change requires idle with empty native input and notice
queues and no pending binding or session mutation. Repeat sets on a stable
binding preserve the current state and sequence. Discovery and rebind are
covered by the #865 control-v1 fixtures.

Send exactly one boolean `enabled` in params. Request IDs use the
`transcript/list` bound of 1,024 bytes for the quoted JSON token as well as
strict UTF-8 and a nonempty string. An invalid ID on this method is omitted
from its bounded error response. A clean enable acknowledges the epoch before
its first frame. A rejected enable leaves classic input unobserved. An enabled
epoch observes the input admitted by the host's public `prompt`, `steer` and
`follow_up` methods, including inputs transformed by overrides. The #865
embedding tests cover both guarantees. A clean disable returns the same
shape with `enabled: false` and both IDs null.

```jsonl
{"type":"aelix_control","id":"enable","method":"inputs/observe","params":{"enabled":true}}
{"type":"response","id":"enable","command":"aelix_control","success":true,"data":{"enabled":true,"subscriptionId":"s1","bindingId":"b1"}}
```

Each event has `type: "aelix_input"`, `event`, `subscriptionId`, `bindingId`
and a consecutive positive `seq` starting at 1 for the epoch. An input
occurrence has an `inputId` and an `attempt` starting at 1. A fresh submission
has a fresh ID even if its body or classic request ID repeats. Reinsertion
advances the attempt on the same input ID and keeps its original source,
route and classic correlation. Clients that need correlation should keep
classic request IDs unique across the subscription. Input origins are
`rpc`, `extension` and `native`. Routes are `prompt`, `steer`, `followUp`,
`nextTurn` and `prefix`. Optional `requestId` appears only for a valid classic
RPC correlation ID. These fields and routes are exercised by the #865
control-v1 fixtures and native RPC tests.

| Event | Event-specific fields and timing |
| --- | --- |
| `prompt_started` | `promptId`, `promptKind` of `prompt` or `notice`, and optional `input` for a direct slot. The slot has `inputId`, `attempt`, `source`, `route` and optional `requestId`. This event marks the actual native claim. |
| `queued` | The slot fields and `queuedPromptId`, the claim at actual enqueue or null if idle. An enqueue can occur before an error reply from a queue-update hook. |
| `consumed` | The slot fields, `queuedPromptId`, `consumedPromptId`, one-based `turnIndex` and `inputIndex`, and R9 `streamId`. `placement: "available"` carries a complete post-reducer user `message` from a verified position in the first generic model request. `placement: "unavailable"` carries a `reason` of `contextMismatch`, `noModelRequest`, `bodyTooLarge` or `bodyUnencodable` without a body. |
| `input_settled` | `inputId`, `attempt`, `promptId` and `outcome`. A placement's owning claim is its consuming claim. An idle removal uses a null `promptId`. `committed` adds `nativeSessionId`, `entryId`, `parentEntryId`, and `sequence` from the settled active path. Other outcomes are `withdrawn`, `notAdmitted`, `notStored`, `storageOnly` and `unknown`. |
| `prompt_settled` | `promptId` and `outcome` of `complete`, `aborted`, `failed`, `handled` or `noWork`. It follows all known placement and terminal decisions for the claim, before native idle and the next claim. Untouched queued inputs can outlive the claim that queued them. |
| `stream_gap` | `reason` of `bindingChanged`, `budgetExceeded` or `unverifiable` on the old epoch. Rediscover and enable on the new stable clean binding. |

For example, a steer queued under one claim can be placed under a later
claim. Its `consumed` precedes the next dependent assistant start, update or
end. The terminal describes the exact stored occurrence if the active path
qualifies it after native cleanup.

```jsonl
{"type":"aelix_input","event":"queued","subscriptionId":"s1","bindingId":"b1","seq":3,"inputId":"i2","attempt":1,"source":"rpc","route":"steer","requestId":"steer-2","queuedPromptId":"p1"}
{"type":"aelix_input","event":"consumed","subscriptionId":"s1","bindingId":"b1","seq":8,"inputId":"i2","attempt":1,"source":"rpc","route":"steer","requestId":"steer-2","queuedPromptId":"p1","consumedPromptId":"p2","turnIndex":1,"inputIndex":2,"streamId":"u2","placement":"available","message":{"role":"user","content":[{"type":"text","text":"Use the revised total.","text_signature":""}],"timestamp":null}}
{"type":"aelix_input","event":"input_settled","subscriptionId":"s1","bindingId":"b1","seq":10,"inputId":"i2","attempt":1,"promptId":"p2","outcome":"committed","nativeSessionId":"session","entryId":"entry-3","parentEntryId":"entry-2","sequence":3}
{"type":"aelix_input","event":"prompt_settled","subscriptionId":"s1","bindingId":"b1","seq":11,"promptId":"p2","outcome":"complete"}
```

The placement check includes the preceding context interval. A context
callback can retain the same user message and move it ahead of a preceding
assistant. That placement is unavailable even if the independently qualified
R9 body still commits. `withdrawn` covers a logically withdrawn occurrence
even if its old entry is still addressable. `notAdmitted` retires an announced
slot that never entered context. An extension prefix first appears in
`consumed`. A prefix cancelled before admission leaves no observation event.
`notStored` denotes a definitive write refusal.
`storageOnly` denotes confirmed active storage without a qualified complete
readback or terminal coordinates. `unknown` covers unproved write or active
membership. The writer checks membership by walking at most 8,192 entries
of its active index and uses the R9 readback qualification described in §5.
A consumer can independently read the settled entry with `transcript/list`.
The #865 reorder, abort and storage tests exercise these distinctions.

In the #865 partial-response abort test, the classic assistant
`message_start` and `message_update` are followed by `turn_end` with
`stop_reason: "aborted"` and then `agent_end`. If an assistant interval
still lacks `message_end`, observation ends the epoch with a
`stream_gap` of reason `unverifiable` at claim release. The interrupted
claim emits no settlement marker. Discard its staged rows and dependent
assistant output and keep publications from earlier settled claims.

The #865 byte-bound tests cover 1 MiB per complete strict UTF-8, finite JSON
line including LF. The observer has room for 128 outstanding attempts and
4 MiB for retained body snapshots and tracking metadata, including live
positional witnesses. The #865 retained-budget tests measure the backing
storage of four near-limit snapshots and check exhaustion while a context
hook holds the witness and more steers arrive. The 1 KiB gap frame has its
own limit. A sequence jump, invalid frame, gap or EOF before a claim
marker invalidates that unfinished publication. The native model call
continues when a placement body cannot be encoded or fit.

**ACP #41 phase 2 consumer policy.** Stage an available placement and its
order-dependent assistant and lifecycle suffix until the consuming claim's
marker and a `committed` terminal arrive in the same contiguous epoch.
Continue to drain stdout while staging. Require an assistant interval's
`message_end` before publishing its buffered updates. Before answering the
ACP prompt, release eligible staged updates in their original order. The
owner's 2026-11-21 decision was “RPC 사용자 입력만 (권장)”. Publish additional
user rows only for eligible RPC steer and follow-up inputs with request
correlation, including those routed from a busy prompt. Keep the existing
composer row once. Use `transcript/list` on the current binding to verify
the durable session, entry key, full body and active coordinates when
reconciling. After reopen use the new live binding to qualify the original
durable session and key. Live ACP/H1 consumption is a phase-2 integration
target in [ACP #41](https://github.com/handochan/aelix-acp/issues/41).

The native held-hook probe found that a pre-run extension hook can keep the
claim pending after abort (#851). Keep staging until native settlement. The
paused-reader probe found that a full pipe can stall native delivery and
cancellation (#852). Resume draining for delivery to progress and discard
unfinished rows after interruption. [ADR-0310](../decisions/0310-opted-in-rpc-input-observation.md)
