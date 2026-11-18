# control-v1 — who consumes what

**Status: derived from aelix-acp `16d6555b`; confirmation pending.**

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
- Aelix Studio `0285f5c3` (main).

aelix-acp paths are under `src/aelix_acp/` unless they start with `scripts/` or `docs/`. Every
aelix-acp line below was re-read at `16d6555b` on 2026-11-18.

**How to read the rows.** A consumer uses a field in one of two ways:
- **direct**: its code names the path. The row gives that path and the file:line.
- **forwarded whole**: it passes a result or a frame on without naming the fields, through:
  - `_aelix/manage` (`server.py:275-325`, returning native `data` unchanged);
  - `_aelix/capabilities.runtime` (`server.py:271-274`);
  - a slash command's JSON output (`runtime/commands.py:77-79`);
  - the unparented `_aelix/subagent` notification (`session.py:530-531`);
  - a dialog frame (`session.py:631`).

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
| `ui/enable` | `{"enabled": true}`, only when the ACP client set `_meta["aelix/clientActions"]` and `methods` has it (`session.py:282-284`) | — | — | |
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

Each is also gated on `capabilities.methods` (`server.py:304`). Each is answered with native
`data` unchanged. For the actions outside `auth/login`, `resources/reload` and `agents/run`,
the timeout is 60 s with no cancel (`server.py:309-310`).

`_aelix/manage` refuses with "Session is busy" whenever the session's `mutation_lock` is held
(`server.py:279-280`), and aelix-acp `16d6555b` holds that lock for the whole of a
`session/prompt` (`server.py:248-255`). So during a turn Studio gets "Session is busy" from
`_aelix/manage` before any of these actions reaches aelix-ai. #612's aelix-acp PR changes this.

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
| `capabilities` | `activeAgent`, `methods`, `methods[]`, `permissionModes`, `permissionModes[]`, `selectionKinds`, `selectionKinds[]`, `subagentsEnabled`, `thinkingLevels`, `thinkingLevels[]`, `version` |
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

| Event | Path | Direct reads | Forwarded whole through | aelix-acp `16d6555b` |
|---|---|---|---|---|
| `aelix_subagent` | with a parent `agent` tool call open | `event` (∈ `subagent_start`, `subagent_tool`, `subagent_end`); `data.id` (required); `data.profile`, `data.state`, `data.current_tool`, `data.model`, `data.provider`, `data.elapsed_ms`, `data.tokens`, `data.cost` (each bounded and type-checked into `_meta["aelix/subagents"]` on the parent's `tool_call_update`, at most 32 children per call) | — | `session.py:515-516`, `:524-536`; `runtime/subagents.py:17-41`, `:62-79` |
| `aelix_subagent` | no parent, or rejected by the tracker (`/agents run`, an unknown `event` value, data without a string `id`) | — | `_aelix/subagent`, as `{"jsonrpc": "2.0", "method": "_aelix/subagent", "params": {"sessionId", "event", "data"}}`, unbounded and unchecked, so every `data` field is consumed | `session.py:526-531`; `runtime/subagents.py:35-37`, `:66-69` |
| `extension_ui_request` | `authProgress` | — | `_aelix/notification`, the whole frame | `session.py:620-623` |
| `extension_ui_request` | `notify`, `setStatus`, `setWidget`, `setTitle` | `method` (which branch: `session.py:619`); `message`, `text` (read and shown as an agent message chunk: `session.py:624-628`) | — | `session.py:624-628` |
| `extension_ui_request` | any other method | — | the dialog client action (`server.client_action(self, "dialog", event)`), the whole frame; an unknown method is shown as a dialog too | `session.py:629-632` |
| `extension_ui_response` (client → host) | ACP writes `{"type": "extension_ui_response", "id", …answer}`: the client's whole dialog reply (`value`, `confirmed`, `cancelled`), or `{"cancelled": true}` | — | — | `session.py:629-636`; the write at `:635` |

The fixtures are `tests/rpc/control_v1/fixtures/event.aelix_subagent.json` (frames emitted while an
`agents/run` is their parent, and one emitted with no parent and only an `id`; all nine `data`
keys are required in both) and `fixtures/event.extension_ui.json` (`select`, `confirm`, `input`,
`editor`, `notify`, `setStatus`, `setWidget`, `setTitle`, `auth/login`'s `authProgress`, `oauth`,
`oauthSelect` and `input`, and a reply reaching the command; `confirm` is replayed on a real
child).

### 4.1 Forwarded frame paths

The floor's `forwarded` paths for each event family: every path seen in the frames the fixture
cases produced, run in process. `test_consumer_list.py` fails while this table and the floor
differ.

| Frame | Paths |
|---|---|
| `aelix_subagent` | `data`, `data.cost`, `data.current_tool`, `data.elapsed_ms`, `data.id`, `data.model`, `data.profile`, `data.provider`, `data.state`, `data.tokens`, `event`, `type` |
| `extension_ui_request` | `id`, `instructions`, `key`, `kind`, `lines`, `lines[]`, `message`, `method`, `options`, `options[]`, `options[].id`, `options[].label`, `placeholder`, `placement`, `prefill`, `text`, `title`, `type`, `url` |

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

## 6. Served, not yet consumed

These are part of version 1: they are in `tests/rpc/control_v1/floor_v1.json`, protected like
every other entry (ADR-0302 D3, D8), but no consumer reads them yet. A row moves to §3 when a
consumer starts reading it. Methods added later under ADR-0302 D3 are listed here first, the
same way. `test_consumer_list.py` fails while this table and the fixtures with no `consumers`
differ.

| Method or field | Added by | Expected consumer | Fixture |
|---|---|---|---|
| `permissions/evaluate` | #594 | none (aelix-acp names it only in `READ_ONLY_CONTROLS`) | `permissions.evaluate.json` |
| `selections/list`, `selections/set` (and `capabilities.selectionKinds`) | #624 (ADR-0305), in the freeze commit itself | Studio #7, through aelix-acp's `_aelix/manage`. Its allowlist (`server.py:303`) and `READ_ONLY_CONTROLS` (`session.py:45-47`) do not list them yet. When aelix-acp adds `selections/set`, its default 60 s timeout is short: the consent dialog alone is bounded at 45 s (`rpc/control_selection.py` `CONSENT_TIMEOUT_SECONDS`), and settings-lock contention can add 30 s (ADR-0305 D4). So the request is to give it at least 90 s, or to send `commands/cancel` with its `requestId` on timeout. | `selections.list.json`, `selections.set.json`, `capabilities.json` |

## 7. Changes since aelix-acp's pin, accepted as v1

aelix-acp pins aelix-ai `e19ab55e`. Between that pin and the freeze baseline:
- eight behaviours changed (D1–D8 in ADR-0302 D6);
- `agents/show.profile` gained two `asdict` fields, `exclude_tools` and `agents` (ADR-0302 D6's
  D9).

The owner took main at freeze time as the baseline on 2026-11-17 ("메서드 + 이벤트 2종, 기준 =
현재 main (권장)"), so all of them are v1. aelix-acp's native suite passed against `a1606f58`:
134 tests, with 1 Windows-only skip (the scout's log,
`.omc/probes/623-624/scout/evidence/acp-native-a1606f58.log`).
