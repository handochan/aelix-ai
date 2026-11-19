# When a delegated agent asks

Status: Accepted

A delegated agent runs in its own process, behind its own permission gate. Before #612 a child
of a parent in `default` mode had no one to ask, so it ran in `plan` mode: read-only. Now, in the
TUI and on an RPC host that turned it on, that child runs in `default` mode and asks **you**,
through its parent, before each change its gate would ask about (owner, 2026-11-18: "default로 돌며
물어봄 (권장)"). The design is
[ADR-0308](https://github.com/handochan/aelix-ai/blob/main/docs/decisions/0308-a-delegated-child-asks-a-human-through-its-parent-before-each-change.md).
How a child is started, and what it may do at all, is in [agent-profiles.md](agent-profiles.md).

## What asks

A child asks about exactly what its gate would ask a person about in its mode:

| The child's mode | What asks |
| --- | --- |
| `plan` | nothing: every change is refused before anything is asked |
| `default` | every file change, shell command, and MCP or extension tool |
| `auto-accept-edits` | `bash`, a write outside the project or to a sensitive path, and any tool aelix did not build |
| `yolo` | nothing |

The guardrail runs first, as for your own calls. The child's mode is its parent's, clamped by its
profile's `approval_mode`; with someone to ask, a `default` parent's child gets `default` instead
of `plan` (the full table is ADR-0308 D3). A child never runs above its parent.

**Where it asks** (`tests/agents_ext/test_approval_clamp_612.py`):

- **The TUI** always has someone to ask: you.
- **`--mode rpc`** asks only after the host sends `ui/enable {"enabled": true, "childApprovals": true}`
  ([below](#over-rpc)).
- **`-p` and `--mode json`** have no one to ask: a `default` parent's child runs `plan`, as before.

Whether a child asks is decided when it starts and kept for its whole run.

## The dialog

The child's question opens the approval dialog you know, with one more row on top that names who
asks:

```
general-purpose agent sub-3f2a… asks · background job 7f9a61… · in pkg/web
Run shell command?
```

` · background job <id>` is there for an agent running in the background; ` · in <dir>` when the
agent works in another folder than yours, since its commands and relative paths are judged there.
The choices are **Yes**, **Yes, for this agent** and **No**; Esc refuses that one call
(`tests/tui/test_child_approval_dialog_612.py`; live, `.omc/probes/612/impl/C/live/`).

- **Yes, for this agent** lets that agent make the same kind of call again without asking: a
  command with the same first two words (only when it has no `;`, `&&`, `|`, redirect or other
  separator — otherwise that exact command), a write in the same folder or under it, or the same
  other tool. It is the agent's own: it never applies to you or to another agent, and a resumed
  agent asks again.
- **No** — the agent's model reads "Denied by the user."; Esc — "Denied by the user (cancelled).".
- An agent's question opens only when nothing else is open: no other dialog, consent question or
  panel (such as `/jobs`). Otherwise it waits until you close that one. Your own questions and other
  agents' wait behind it, in order.
- **An agent in the background** asks at once when nothing else is open, also while you are idle
  between turns. It starts no turn and wakes no model (owner, 2026-11-18: "즉시 대화창 (권장)").
- For half a second after the question appears, Enter, `y`, `s`, `1` and `2` do nothing. They are
  dropped, not kept, so a key you were typing for something else does not approve a change you have
  not read. The same half second starts again when the question is back after a dialog that opened
  over it closes (a consent question, an extension's dialog). `n` and Esc work at once.
- While it waits, `/jobs` and `job(action="list")` show it as `awaiting approval: <tool>`, also on
  an open `/jobs` panel.

(`tests/tui/test_child_approval_dialog_612.py`; pty captures with a scripted model,
`.omc/probes/612/fix-r1/R1-b/tui/`.)

## Answers you do not give

Some answers are given for you; the agent's model reads "Not approved: <reason>.":

| What happened | The reason it reads |
| --- | --- |
| you switched to `plan` (shift+tab before the question, or RPC `permissions/set`) | the parent is in plan mode |
| the agent was asked to wrap up, with a question open or later | the agent was asked to wrap up |
| it asked again while a question of its own was open | another approval request is open |
| an RPC host that turned this off, with the question open or later | no one can answer approvals here |
| an RPC host that did not answer within 300 s | no answer from the host within 300 s |
| an RPC host that cancelled the dialog, or answered something that is not a choice | the host cancelled the request |
| the RPC host went away | the host is gone |

shift+tab does nothing while a dialog is open, so in the TUI a switch to `plan` lands before or
after a question. When the agent is stopped, Esc stops your turn, the session changes or the agent
ends while its question is open, the dialog closes and, unless your answer was already on its way,
nothing is answered: the call never runs.

A change too large to show, over 1 MiB with its whole content, is refused without asking: "This
change is too large to show for approval (<n> bytes; the limit is 1 MiB)."

## Over RPC

`ui/enable {"enabled": true, "childApprovals": true}` answers `{"enabled": true, "childApprovals":
true}` when the host runs delegated agents; then every agent started from that point asks the host.
Without `childApprovals`, `ui/enable` answers as it always did. A question is one
`extension_ui_request`:

```json
{"type": "extension_ui_request", "id": "ui-…", "method": "childApproval",
 "title": "general-purpose agent sub-3f2a… asks: Run shell command?",
 "child": {"id": "sub-3f2a…", "profile": "general-purpose", "sessionId": "…", "jobId": null,
           "parentToolCallId": "call_…", "background": false, "cwd": "/abs/path", "epoch": "…"},
 "tool": {"callId": "…", "name": "bash", "kind": "bash", "args": {"command": "…"}},
 "choices": [{"id": "yes", "label": "Yes", "kind": "allow_once"},
             {"id": "yes_session", "label": "Yes, for this agent", "kind": "allow_always"},
             {"id": "no", "label": "No", "kind": "reject_once"}]}
```

Answer with `extension_ui_response {"id": …, "value": "yes"}` (or `yes_session`, `no`), or `{"id":
…, "cancelled": true}`. Any other value refuses the call, so a client that shows it as a plain
dialog can approve nothing, and neither can a reply marked `cancelled`. Each question also shows
up as `aelix_child` `approval` events — `asked`, then `allowed`, `denied` or `withdrawn` — which say
when a dialog was withdrawn ([background-jobs.md](background-jobs.md#over-rpc-and-acp)). A dialog
whose `child.epoch` is not the session's current epoch is stale: close it. aelix ignores its answer
because the session change withdrew the question first, so the reply's `id` is no longer open;
aelix does not compare epochs itself. A `ui/enable` that does not turn it on (anything but
`{"enabled": true, "childApprovals": true}`) turns it off and ends the questions that are open, as
"no one can answer approvals here"
(`tests/rpc/test_child_approvals_rpc_612.py`; live with a real model, `.omc/probes/612/impl/C/live/`).

Over RPC your own approvals do not wait for an agent's: a question of yours and an agent's can be
open together.

**ACP** needs an aelix-acp change (`childApprovals`, and each question as a
`session/request_permission`). aelix-acp `16d6555b` does not send it, so under it a child runs as
before.

## Limits

- An agent first run without anyone to ask (before #612, or under `-p` or json) keeps its read-only
  ceiling when it is resumed where someone can be asked.
- When aelix cannot tell whether your answer reached the agent, the agent may have run the call; the
  `/jobs` row then says the delivery is unknown. When it knows the answer was not sent, the agent
  runs nothing, and the row says to stop or resume it. After the agent's inbox broke (an earlier
  line could not be delivered in time), a new question is not shown to you at all: the row says
  the same.
- A question aelix cannot read at all cannot be answered: the agent waits, runs nothing, and its row
  says to stop it.
- An agent launched by an aelix that was replaced on disk while it ran (an editable checkout that
  changed) may fail to start: it is not started under the old rules instead.
- An extension hook that changes a call's arguments does so after your answer, as for your own
  calls.
- Not run: ACP; "Yes, for this agent" with a real model; Windows (the tests that start a real
  agent are POSIX only).
