# #610 lane B (tool surface) — notes for lane C and the lead (2026-10-30)

Base: lane A's WIP commit `f912bb00` on `feat/610-agent-resume` (worktree `claude-610`). Everything here is in the lane B WIP commit.
Prefixes: `A/` = `packages/aelix-coding-agent/src/aelix_agents/`, `T/` = `tests/`.

## 1. What the model and the human now see (exact strings, for the docs)

### Schema (`A/tool.py` `AGENT_TOOL_PARAMETERS`)
- New properties, after `cwd`: `action` (`enum ["run","resume"]`, `default "run"`) and `id` (string). `required` is still `["profile"]`.
- `action` description: "run (default): delegate new work, as 'mode' describes. resume: continue ONE agent you delegated earlier, in its own conversation — pass its 'id', the 'profile' it ran as, and its next instruction in 'task'; no 'mode', 'tasks' or 'cwd'."
- `id` description: "action=resume only: which agent to continue — the id on the Session line of its result. Not a file path."
- `task` description gained: "With action=resume it is instead the NEXT instruction for that agent, which keeps its own conversation."

### Description head (`_DESCRIPTION_HEAD`, 1525 of 2048 bytes; was 1170)
- "There is no conversation:" removed from paragraph 2.
- New last paragraph: `All of that is action="run", the default. action="resume" continues ONE agent that finished or was stopped: pass the 'id' on the Session line of its result, the 'profile' it ran as, and its next instruction as 'task'. It goes on in its own conversation, on the same profile and model, and its limits never widen. Resume it rather than delegating the same work again from scratch.`
- `test_the_contract_text_stays_inside_its_own_budget` now also pins `action="<a>"` for every enum action.

### Parser refusals (`parse_agent_call`, all before any process)
- unknown / empty action: `action='<x>' is not an action. Use one of: run, resume.`
- run + non-absent `id`: `'id' is only for action='resume'. Pass action='resume' to continue that agent, or leave 'id' out to delegate new work.`
- resume without profile: `action='resume' needs 'profile': the profile that agent ran as — the last line of its result names it ([agent <profile> · …]).`
- resume + `tasks`: `action='resume' takes no 'tasks': it gives ONE agent its next instruction, in 'task'.`
- resume + mode other than null/"single": `action='resume' takes no mode='<m>': it continues ONE agent, so leave 'mode' out.`
- resume + `cwd` (any non-null, "" included): `action='resume' takes no 'cwd': the agent goes on in the directory it ran in.`
- resume + truthy `background`: the existing background sentence (now one constant for both actions).
- resume without task: `action='resume' needs 'task': the next instruction for that agent. It keeps its own conversation, so say what to do next rather than the whole job again.`
- id not a string: `'id' must be the agent's Session id, as a string.`
- path-shaped id (`/`, `\`, or a `.jsonl` name, any case): `'id' is the agent's Session id — the id on the Session line of its result — not a file path.`
- instruction over budget: `'task': the instruction is N bytes; a resume instruction may be at most 64512 bytes — 65536 for any task, less 1024 for the note aelix puts in front of it. Shorten it.` (`RESUME_TASK_MAX_BYTES = MAX_TASK_BYTES - RESUME_PREAMBLE_RESERVE`, strict UTF-8 bytes.)
- instruction with a lone surrogate: `'task' holds half of a character (a lone surrogate), which cannot be sent to the agent. Resend the instruction as plain text.` Without it the instruction passed the parser and failed only at `create_subprocess_exec`, after the budget and the records.
- PRE-EXISTING, NOT CHANGED (run path, #604-era `chain.check_task_size`): a `run` task with a lone surrogate raises `UnicodeEncodeError` out of `parse_agent_call` instead of `AgentCallError` (probed: `parse_agent_call({"profile":"scout","task":"\ud83d"})`), so the hook does not turn it into a blocked call. Worth its own issue.
- Missing / null / "" / blank id is NOT refused by the parser: the hook answers with lane A's `RESUME_NEEDS_ID` + listing.
- Absent spellings accepted: `action: null`; for run `id: null/""/[]`; for resume `tasks: null/""/[]`, `mode: null/"single"`, `cwd: null`, `background: false`, any `timeout_ms`.

### Hook refusal added by lane B (`A/extension.py` `_not_its_profile`)
- `Resume refused: that agent ran as profile "<recorded>", and it is resumed under that profile only. Pass profile="<recorded>" with its id, or delegate the task anew.` (recorded name through `tool.call_literal`: `child_line`, lone surrogates dropped, JSON-quoted.)
- Any failure to re-resolve the recorded profile name (`allow_project=False`) → lane A's `RESUME_PROFILE_CHANGED` verbatim.
- Budget at the hook: the existing `_over_budget(1, remaining)` text.
- Decline: the existing `_DECLINED`.

### Results (D2, D4)
- Single success with a session: `<summary>\n\n[notes…\n\n]Session: <id>\n\n[agent …]` — the Session block is after every note, before the footer. No session → byte-identical to before.
- Batch ok member with a session: `[i/N ok] <body>\n[Error/Note/dropped lines…\n]Session: <id>\n[agent …]` — own line, outside the member's byte share. Failed members unchanged (`· session <id>` in the heading, no guidance).
- Single started-not-finished with a session and work to point at, line 2: `Do not delegate the same task again from scratch — it already did the work below. Check the changed files and its tool calls, then continue it with agent(action="resume", profile="<profile>", id="<id>", task=…).` (`tool.RESUME_GUIDANCE`, values through `call_literal`). Without a session: the old `DO_NOT_REDELEGATE`. Same print condition (`did_work`). The single-mode Esc report (ADR-0276 D14) carries it too.

### Resume dialog (`A/consent.py` `build_resume_consent_title`), 13 rows, 14 with Model
```
Resume agent '<name>'?

Session:    <child session id>
Profile:    <name> (<scope> scope)
Source:     <source path, middle-elided>
Directory:  <recorded cwd, whole>
Permission: <resume_clamp(first grant, clamp now)>
Model:      <recorded pin's --model>        (only when there is one)

Earlier task (written by the model):
<first run's task_preview, flattened; "…" when it is 200 chars (the record's cut); "(not recorded)" when empty>

Next instruction (written by the model, not by you):
<the whole instruction, flattened>
```
Options are `build_options(clamped, may_widen=_may_widen(..., ceiling=first grant))`. Every field through `_sanitize_field` / `_sanitize_path`.

## 2. Where the ceiling is applied (critique B1, M1)
1. `extension._grant_for`: `want = resume_clamp(target.limits.granted_mode, child_permission_mode(...))` before the pre-filter; `consent_is_required(..., ceiling=)`; the pre-filter's disclosure is built from the capped `want`.
2. `consent.request_resume_consent`: same `resume_clamp` right after the clamp, ABOVE the no-UI return; `consent_is_required` and `_may_widen` get `ceiling=`.
3. Lane A's `resume_granted` floor (`permission_floor=granted_mode`) — unchanged.
`_may_widen` constraint 7: with a ceiling, widening needs `rank(AUTO_ACCEPT) <= rank(ceiling)`. `ceiling=None` (every fresh spawn) changes nothing — pinned over the whole matrix.

## 3. Mutation check (each restored from saved bytes, sha256 verified)
Driver `/tmp/610-lane-b/mutate.py` (scratch). Tests run: `test_agent_resume_tool_610.py`, `test_stopped_child_reports_604.py`, `test_tool_and_security.py`.
All 20 KILLED (run before the lone-surrogate refusal was added; that refusal has its own test, `test_an_instruction_that_cannot_be_sent_is_refused_before_anything_is_spent`): ceiling off in `_grant_for`; ceiling off in `_may_widen`; ceiling not passed to the pre-filter's `consent_is_required` (survived first — behaviourally covered by `request_resume_consent`'s own early-out — killed by the added `test_the_pre_filter_asks_the_same_predicate_with_the_ceiling`); ceiling off in `request_resume_consent`; profile mismatch unchecked; run+id ignored; unknown action runs; resume budget = full task limit; resume cwd allowed; path id allowed; no Session line on a success; no Session line on an ok member; guidance always the old sentence; guidance profile unsanitised; `resume_refusal` skipped at the hook; budget unchecked at the hook; resume dispatched as a fresh spawn; dialog preview unsanitised; no cut marker; resume routed to the fresh dialog.

## 4. Citations
`--fix` relocated 58 (line numbers only, incl. lane A's runtime.py and `aelix_status/extension.py`, `agents/profile.py`). Re-derived by hand, then `--lock` (it reported no overwritten anchor): `consent.py` → `extension.py:1200-1212` (the pre-filter's `SpawnGrant(...)`); `tool.py` AgentCall docstring → `consent.py:591-682` (`consent_is_required`) and `consent.py:493-588` (`_may_widen`); `test_tool_and_security.py:1481` → `tool.py:1351` (the error-repeat check). Too-generic anchors 18 → 17.
