# 0318. An RPC host resumes a stopped delegated agent with `children/resume`

Status: Accepted (2026-11-22, #836). Fix round 1 of the same day changed D4, D5 and the limits;
each changed paragraph says "fix round 1".
Date: 2026-11-22
Issue: #836 (a host resumes a stopped delegated child directly). Lane b8 836-853-857 of session
aelix-ai-92.
Amends: ADR-0277 (D16 "the model's `agent` tool is the only door" and limit 8, "no host surface
resumes"), ADR-0307 (D4's `children/` family gains a seventh method). Each carries a dated note.
Relates: ADR-0302 (D3, D9: a new method is an addition and enters the floor), ADR-0304 D17 (a
human-typed door is not filtered by the `agents` lists), ADR-0197 (consent and the band rule),
ADR-0298 (Ctrl+B moves a running resume), #612 (the owner's "#612에서 빼기 (권장)", 2026-11-18, which
left host resume out of #612 and to this issue).
pi: `earendil-works/pi` `origin/main` `780bf31fc`. pi's coding agent has no host method that resumes
a delegated agent. Its `apps/p2` subagents are durable conversations a person can open and keep
using, archived ones included (`apps/p2/plugins/subagents/contract.ts`, `ChildSchema`), so there
"resume" is sending input to a conversation that still exists. Aelix's delegated agent is a print
child that ended, and resuming it launches it again on its own session file inside the limits it
first ran with (ADR-0277); the host door here keeps that model.

## Context

Before this change only the model could resume a stopped delegated agent:
`agent(action="resume", id=<its session id>, profile=…, task=…)` (ADR-0277). An RPC host (and so
aelix-acp and Aelix Studio) could see a stopped agent in `children/list` (ADR-0307 D4) but could
resume it only by asking the model to. #612 left a host door out by the owner's decision of
2026-11-18, "#612에서 빼기 (권장)", and #836 is that door. Every choice below is the lead's call
for this lane (the brief of 2026-11-22), except where a line says otherwise.

## Decision

**D1. The method.** `aelix_control` `children/resume {id, task}` answers `{result}`, the shape of
`agents/run`'s: `result` is `asdict(SubagentResult)`, its `details` and `tool_trail` cut at 100,000
characters with `detailsTruncated` or `tool_trailTruncated: true` beside them, as `agents/run` cuts them. `id` is the agent's
child session id (a `children/list` row's `sessionId`, a result's `session_id`, the `Session:` line
the model sees); `task` is the next instruction. A refusal known before anything ran is an error
response with the runtime's sentence; a declined consent is a `declined` result, as for
`agents/run`.

**D2. Admission.** `MUTATING_METHODS` and `CANCELLABLE_METHODS`, as `agents/run`: it needs an idle
session (refused "Runtime mutations require an idle session" during a turn), it holds the mutation
lock while the resumed agent runs in the foreground, and `commands/cancel` ends it, which stops the
agent as a cancelled `agents/run` does. The lock-free `children/steer`, `children/wrap_up` and
`children/stop` reach the resumed agent while it runs (ADR-0307 D4). `capabilities.methods` lists
it only where the bound delegation runtime implements the seam's optional `HostResume`
(`subagent_contract.py`), kept off `SubagentRuntime` as `SubagentControls` and `ModelDelegation`
are, so `CONTRACT_VERSION` stays 1; the bundled runtime implements it.

**D3. The targets.** Those the model's resume finds: `find_resume_target`, an agent the MODEL
delegated, on the parent's current branch, with its records, file and limits as ADR-0277 D5–D7
require. An agent a person started (`/agents run`, `agents/run`) is refused with the host's own
sentence, `HOST_RESUME_NOT_YOURS`: "Resume refused: children/resume resumes only an agent the model
delegated, and a person started that one (/agents run, or agents/run). Start it again with
agents/run." The model's `RESUME_NOT_YOURS` says "not delegated by you", which is false addressed to
a host. Including those agents would change the lookup rule; that is limit L1.

**D4. The checks, run again for this door** (`_SubagentRuntimeImpl.resume_for_host`), in the model
door's order (`AgentsExtension._approve_resume`), each before a dialog and before anything is
spent: the instruction (a NUL, half of a character, more than 64,512 UTF-8 bytes, the `agent`
parser's bound for a resume, `HOST_RESUME_TASK_MAX_BYTES`); the target (D3); the recorded profile
re-resolved on the HUMAN door, `resolve_profile(name, allow_project=False)`, refused
`RESUME_PROFILE_CHANGED` when it cannot be found; and `resume_refusal`: this channel resumes, the
profile is the first run's file and not a project one, nothing in this process runs that session,
its recorded directory is still that directory inside the parent's, its model route has not moved,
and no other process holds the child file's writer lock. As on `agents/run`, the session's `agents`
on/off lists and the identity's `agents:` rule do not filter this door (ADR-0304 D17); the
structural gate's C1 row names `resume_for_host` as the one place inside `aelix_agents` that loads
the unfiltered resolver (`tests/agents/delegation_gate.py`). So the model's door and this one
differ for an agent whose profile those lists turn off: the model's resume is refused with
ADR-0304 D7's text, and the host's runs, as a user-typed `/agents run` of that profile runs after
its note (`tests/tui/test_agents_panel_795.py::test_agents_run_of_a_profile_the_model_could_not_start_runs_after_a_note`).
Since fix round 1 (verify V-F2) the listing a missing or unknown id gets on this door is not
filtered either (`find_resume_target(id, policy=False)`): it names such an agent like any other.
In round 1 it left the agent out and said "they cannot be resumed" while the same door resumed it
(`tests/agents_ext/test_host_resume_836.py::test_the_hosts_listing_names_an_agent_the_agents_lists_turn_off`).

**D5. Consent, as for a user-typed spawn.** The slot is reserved before the dialog while background
jobs are on, as `spawn` reserves it; then `request_resume_consent`, so the first run's grant is
still the ceiling (ADR-0277 D10) and a `plan` agent cannot be widened by a `yolo` host. The dialog
body is the resume dialog with one line changed: the instruction is labelled "Next instruction
(sent by the application running aelix):" (`RESUME_HOST_INSTRUCTION_LABEL`) instead of "written by
the model, not by you". The dialog appears on the host's RPC dialogs when it sent
`ui/enable {enabled: true}`; without them the clamp is consented, as `agents/run` is headless.

Fix round 1 (Codex C1): the plane hands its dialogs to the runtime (`resume_for_host(..., ui=)`)
as well as binding them. In round 1 it only bound them, and the consent read the extension's last
hook context. A process that had reopened its session and had not prompted yet has no such
context, so the resume was consented without a dialog and launched. The fix was measured in
process on a fresh binding (`tests/rpc/test_children_resume_836.py::test_a_fresh_binding_asks_the_hosts_dialog_before_its_first_prompt`)
and in a real `--mode rpc` process opened with `--session`
(`test_a_fresh_rpc_process_asks_before_it_resumes`): the first frame after `children/resume` is
the `select`, and `Cancel` answers `declined`. In round 1 both launched with no `select`.
RPC `agents/run` in the same state is limit L5.

Fix round 1 (Codex C2): after the dialog the recorded profile is resolved again, with the resolver
loaded once (the C1 row above), and must still be the file the human approved. Otherwise the result
is `IDENTITY_MISMATCH`, "Delegation refused: the approved profile and the resolved profile do not
match. Nothing was started.", the model door's sentence (`extension._execute_pending`), and nothing
is recorded or launched. In round 1 a profile removed or replaced while the dialog was open was
resumed with the stale object
(`test_host_resume_836.py::test_a_profile_that_moved_while_the_dialog_was_open_is_refused_after_it`).

**D6. The run.** `resume_granted`, the model door's own: the conversation is still the one the
target was found in, the lookup runs again and must find the same target, `resume_refusal` passes
again, and then one `_run` on a new key, attached to the child's file. Two differences from the
model's call, both the user-typed door's: it is not charged against the per-prompt delegation
budget (`charge_budget=False`, a keyword `resume_granted` gained with the model door's `True` as its
default), and its record carries `tool_call_id: null`. The lookup reads the FIRST run, so the model
can still resume the agent afterwards.

**D7. control-v1.** An addition under ADR-0302 D3 and D9: `fixtures/children.resume.json` (14
cases, `"since": "#836"`, in process on the `full`, `turn` and `bare` rigs, through the rig's fake
`HostResume`, which raises the bundled runtime's own sentences), two `capabilities.json` cases, and
the floor entry `scripts/check_control_v1.py --update` wrote; the v1 digest is unchanged. The
contract lists it under "Served, not yet consumed" until an aelix-acp that forwards it is merged.

**D8. aelix-acp.** Branch `feat/836-children-resume` of aelix-acp adds `children/resume` to
`_aelix/manage`'s allowlist, gated on `capabilities.methods` like every action there, with
`agents/run`'s deadline (`--command-timeout`, 600 s) and its `commands/cancel` on timeout
(`tests/test_management.py::ChildrenResumeTests`). It is not merged here.

## Consequences

- An RPC host, and through aelix-acp's `_aelix/manage` an ACP client, can resume an agent the model
  delegated without asking the model, under the same checks and the same ceiling.
- The model's door is unchanged: `extension._approve_resume` and `resume_granted`'s default are as
  before, and a Ctrl+B of a running resume still crosses at `resume_granted` (`detach.product_allowlist`;
  `tests/agents_ext/test_detach_611.py`, `test_detach_crossing_611.py`).

## Limits

- **L1.** An agent first started with `/agents run` or `agents/run` cannot be resumed, by the host or
  by the model: its record has no tool call, and the lookup refuses it (D3). Including it would
  change ADR-0277 D5's rule and the resume dialog's "Earlier task (written by the model)" line.
- **L2.** The host door runs in the foreground under the mutation lock: no background resume, and a
  host waits for it as it waits for `agents/run` (aelix-acp's 600 s deadline). The lock-free
  `children/` interventions, and since #853's fix round 1 a `permissions/set {tighten: true}` that
  narrows, are answered while it holds the lock
  (`tests/rpc/test_permissions_tighten_853.py::test_a_tighten_is_answered_while_a_run_holds_the_lock`).
- **L3.** The instruction bound is the bundled runtime's (64,512 UTF-8 bytes); product core sets no
  size bound of its own (ADR-0197's band rule), so a third-party `HostResume` may choose another.
- **L4.** Not run: Windows and Linux live, Studio, and a resume of a background (`spawn`) agent
  live. A consent dialog answered over RPC dialogs was run live in fix round 1 (below).
- **L5.** RPC `agents/run` is not changed here, and it has the gap D5's fix round 1 closes for this
  door. It binds the plane's dialogs, and `spawn` takes its consent context from the extension's
  last hook (`host.consent_context()`). On a fresh binding with dialogs on, before any hook, an
  `agents/run` under an `auto-accept-edits` parent launched its child at `auto-accept-edits` with no
  `select`, at `0436193c` and with this change (an in-process probe on `test_agent_background_611`'s
  rig, run in fix round 1). Handing `spawn` the dialogs would change the `SubagentRuntime` seam, so
  it is left to a follow-up.

## How it is held

Tests (macOS, Python 3.12, each failing at `0436193c`, where `children/resume` was "Unsupported
runtime control method" and the runtime had no `resume_for_host`):

- `tests/agents_ext/test_host_resume_836.py` — the runtime door over a real parent session on disk:
  a model-delegated agent resumed on its own file, unbudgeted, its run recorded with
  `tool_call_id: null` and `resumes` the first key; the first grant capping a `yolo` host; an
  `agents/run` agent refused with the host's sentence; each check (unknown id, writer lock held,
  directory gone, running here, a NUL, one byte over) refused before a dialog with nothing
  launched; a profile that cannot be found; an instruction at the bound; the dialog's host label,
  a decline that launches nothing, and the headless clamp. Fix round 1 adds: the host's dialogs
  asked where the hook context has none (C1); a profile removed or replaced while the dialog was
  open refused after it (C2); the unfiltered listing (V-F2); the slot already reserved when the
  dialog opens, so moving the consent above `reserve(1)` fails the test (C7); an agent resumed
  under a `default` parent with child approvals on runs `default` and asks its parent (ADR-0308
  D3; verify V-F5, mutant M35); and the disclosure a `yolo` parent's resume carries shown and taken
  down (V-F6, mutant M40). Each fix-round test that is not a mutant's fails on round 1's build.
- `tests/rpc/test_children_resume_836.py` — admission sets, the plane over the bundled runtime, a
  refusal passed through, the plane's dialogs bound only while it runs and handed to the runtime,
  the cut of long texts, the param checks before the runtime is asked, and no listing or service
  without `HostResume`; since fix round 1, a fresh binding and a fresh `--mode rpc` process asked
  before they resume (C1).
- `tests/rpc/control_v1` with `children.resume.json` and the floor (`scripts/check_control_v1.py
  --check`).

Live, macOS, `openai/gpt-4.1-mini` through OpenRouter, a scratch HOME, agent dir and cwd, the
lane's own tree, with the lane's drivers:

- a real `--mode rpc` parent: the model delegated eight one-file writes to a `worker` profile; when
  `r1.txt` existed the driver aborted the turn (the agent ended `stopped`, `cancelled`); then
  `children/resume {id: <its sessionId>, task: "Write the file resumed.txt …"}` answered
  `ok: true, status: "ok", session_id: <the same id>`, `resumed.txt` was written, and the parent's
  records hold the resume's start with `tool_call_id: null` and `resumes` the first key. An
  `agents/run` agent's id was refused with `HOST_RESUME_NOT_YOURS`, an unknown id with the listing;
- the same through aelix-acp's `_aelix/manage children/resume` (`--enable-agents`,
  `--permission-mode yolo`, the model's turn ended with `session/cancel`): `ok: true`, the same
  session id, `resumed.txt` written; an unknown id answered `-32603` `backend_error` with the
  native sentence;
- fix round 1, a fresh process: a parent in `auto-accept-edits` delegated, was aborted after
  `r1.txt` and exited; a second `--mode rpc` process opened its session with `--session`, sent
  `ui/enable` and then `children/resume` before any prompt. The first frame was the consent
  `select`, with the host label and the options "Run with the inherited posture
  (auto-accept-edits)" and "Cancel"; answered with the first, the agent wrote `resumed.txt` at
  `auto-accept-edits`;
- fix round 1, the same through aelix-acp: adapter 1 ran `yolo` with a `worker` profile declaring
  `approval_mode: auto` (its first run was granted `auto-accept-edits`), was cancelled after
  `r1.txt` and exited; adapter 2 `session/load`ed the session, so a fresh native reopened it, and
  set `default`. `_aelix/manage children/resume` then reached the client as one `_aelix/client_action`
  `dialog` before the agent ran, with "Run and ask before each change (default)", "Allow file edits
  for this run (auto-accept-edits)" and "Cancel". Answered with the first, the agent asked its
  parent before its write (`session/request_permission`) and wrote `resumed.txt`. A positive
  control with round 1's native asked no dialog. aelix-acp offers only `default` and
  `yolo`: under `yolo` nobody is asked (ADR-0197, #196), and under `default` an `inherit` agent
  holds no write authority, so only a profile that declares it and a first grant above `default`
  give the dialog something to ask.

## Note (2026-11-24, #910)

ACP PR #55 merged at `5268d8f`, after the branch probe described in D8.
The merged adapter forwards `children/resume` when `capabilities.methods`
advertises it (`src/aelix_acp/server.py` at that revision). Its
`runtime-reference.json` still pins native `83714045`, which predates #836.
See the [control-v1 consumer register](../contracts/control-v1.md).
