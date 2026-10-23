# 0275. ACP runtime controls use a separate RPC envelope

Status: Accepted (2026-10-23); review remediation (2026-10-23, #594: cancellation,
EOF, classic/control exclusion, branching, permission evaluation, project-profile
consent, capabilities, dialogs, settings persistence and MCP selection made
exact); review round 1 (2026-10-23: an extension command's own session
replacement is a protected transition too)
Date: 2026-10-23
Issue: #593 (the envelope), #594 (review remediation)

## Context

Aelix Studio drives Aelix through an ACP adapter (`aelix-acp`). It needs live
model, reasoning and permission selection, slash handlers, settings,
authentication, resource inventory, client MCP servers and agent profiles. The
RPC contract has exactly the 29 commands of pi's roster. Adding every host
operation to that discriminated union would change the roster, and copying
native services into the adapter would give it different settings, auth and
command semantics.

## Decision

### 1. One additive envelope beside the roster

RPC mode accepts a separate JSONL request:

```json
{"id":"request-id","type":"aelix_control","method":"capabilities","params":{}}
```

It answers with the existing correlated response: `type:"response"`, `id`,
`command:"aelix_control"`, `success`, then `data` or `error`. An error message is
never empty, and a result the wire cannot carry is answered as an error rather
than left unanswered. The `RpcCommand` union and its 29 commands are unchanged.

The CLI composition root injects the held permission posture, settings manager,
`AuthStorage`, model registry and agent-profile service. The control plane follows
the live runtime's current harness after any replacement and owns no second
runtime. No kernel module imports a host module.

`capabilities` (version 1) lists only the methods this host can serve: settings
methods need a settings manager, `permissions/*` a posture, `auth/*` an
`AuthStorage`, `agents/list|show|use` the profile service, and `agents/run` a
bound subagent runtime. `aelix-server`'s WebSocket `/rpc` carries the same
envelope with none of those services, so it advertises the remaining methods
only.

### 2. Operations, cancellation and shutdown

- Read-only methods (`capabilities`, `resources/list`, `settings/get`,
  `permissions/get`, `permissions/evaluate`, `mcp/status`, `extensions/list`,
  `auth/status`, `agents/list`, `agents/show`) bypass the mutation lock.
- Mutations serialize on one lock. Except `ui/enable`, each needs an idle
  harness and no classic RPC command in flight.
- A session lifecycle transition is any harness replacement the control surface
  starts. Each one disposes the old harness before the factory builds the new
  one, so it is never interrupted half-way and holds the mutation lock until the
  new harness is installed and rebound. Transitions are:
  - `resources/reload` and `sessions/branch`;
  - an extension command's own `ctx.new_session`, `ctx.fork`,
    `ctx.switch_session` and `ctx.reload`. Command contexts dispatched by
    `commands/run` drive the runtime through a guarded view, so these calls are
    protected wherever the handler makes them. Protection is decided by the
    runtime call, not by the slash command's name.
- `commands/cancel` cancels one operation: the one named by `requestId`, or the
  running one.
  - Running `commands/run`, `agents/run`, `auth/login` and `mcp/configure` are
    cancellable, because each undoes itself.
  - A running `resources/reload` or `sessions/branch` finishes, and the cancel
    answers `{"cancelled": false, "reason": "not_cancellable"}`.
  - A `commands/run` whose own transition is running answers
    `{"cancelled": true, "deferred": true}`: the transition finishes, then the
    command stops at the point its `ctx` call returns.
  - A queued operation of any kind can be cancelled by its id.
- When stdin closes (or on SIGTERM), the control plane refuses new mutations,
  cancels queued and cancellable operations (a cancelled command mid-transition
  stops after the transition, as above), settles open dialogs as cancelled and
  lets a running lifecycle transition finish. A mutation queued at EOF does not
  run after the client has gone.

### 3. Classic commands and control mutations exclude each other

The 29 commands and the control mutations share one runtime:

- While a control mutation holds the lock, a classic command that starts a turn
  or replaces the session (`prompt`, `new_session`, `switch_session`, `fork`,
  `clone`, `compact`) is refused with an error.
- While a lifecycle transition runs, every classic command except reads
  (`get_*`, `export_html`) and aborts is refused. That includes a transition
  started by an extension command.
- A control mutation is refused while a classic command (other than a read or
  an abort) is still running. A classic `prompt` counts until it is accepted,
  so the gap before its turn starts is covered.
- An extension command's transition is refused if a classic command admitted
  beside the command is still running. The handler's `ctx` call raises, and the
  command reports an error.

Not covered: an extension that keeps a replaced-session context (the
`with_session` handle) and uses it after its callback returns. That handle is the
kernel's own runtime; retaining it escapes the control plane's operation boundary.
Calls made inside the callback run within the protected transition.

### 4. Command dialogs are not tool approval

`ui/enable` turns on bounded, correlated dialogs (`select`, `confirm`, `input`,
`editor`, OAuth) for `commands/run`, `agents/run` and `auth/login`. The UI is
bound to the runtime only while one of those runs. Client replies use
`extension_ui_response` with the generated id.

- Every dialog ends: with the reply, or as cancelled on the dialog's own
  `timeout` or `signal`, after 300 s, or on shutdown.
- `notify`, `set_status`, `set_title`, line-list `set_widget` and OAuth
  progress are id-carrying notices that expect no reply.
- Terminal-only methods (`custom`, editor control, working indicators, themes)
  do nothing, as in pi's RPC mode. They do not raise.
- This channel does not approve tool calls. The UI marks itself
  `provides_tool_approval = False`, and the built-in permission gate keeps its
  headless verdict for any tool call made while it is bound. The host approves
  tools through its own `tool_call` hook (the adapter's bridge extension).

Diverges from pi, whose RPC mode binds its UI context for the whole session:
here the RPC host opts in, and only around these calls.

### 5. Permissions

`permissions/get` and `permissions/set` read and write the same held posture the
gate reads, as the TUI's shift+tab does.

`permissions/evaluate {tool?, args}` returns the built-in gate's verdict for a
call with no session grant: `allow`, `deny`, or `ask` (the gate leaves the call
to an approver). The verdict comes from the same function as the gate's own
posture branches. `tool` names a tool in the live registry, and its provenance
is read from the object, never the name (ADR-0253). Without `tool` the
arguments are taken as a call to aelix's own `bash`, which is the shape the
shipped adapter sends.

### 6. Settings and authentication

Settings use an explicit typed allowlist. No setting can write credentials,
trust or delegation. `settings/set` reports success only after the write is
flushed, and a failed write is an error. Auth replies expose status, never
secrets.

### 7. Client MCP servers

ACP MCP servers are validated stdio, HTTP or SSE configurations. ACP server
names are human-readable, so each is mapped to a key rather than refused: runs
of characters outside `[A-Za-z0-9_-]` become `_`, the key is cut to 32
characters, and keys must be unique. The server registers as `acp_<key>`, and
statuses carry both names. A server tool whose qualified name
`acp_<key>__<tool>` would break the provider pattern `[A-Za-z0-9_-]{1,64}` is
skipped and reported in `skippedTools`. A failed connect names the server and
the error class.

One owner task per configuration lease enters and exits every transport, so
AnyIO contexts close on the task that entered them. A new configuration
replaces the old lease only after it is connected, and EOF closes leases.

An explicit tool selection is the user's:

- New client tools never widen it.
- A same-named tool that was selected stays selected across a replacement.
- Removing a selected client tool narrows the selection instead of failing.
- A reload keeps selected client tools selected, as it keeps every other
  selection. Other session replacements start from the launch selection.

HTTP headers are excluded from manifest serialization and repr, and statuses
omit environment values, headers and URLs. This is trusted client
configuration, not remote authorization or network isolation.

### 8. Agent profiles and delegation

Profiles use `AgentProfileService`. Delegation is off unless the launch enables
it, and native child restrictions apply. `agents/run` does not accept a client
boolean for a project-scoped profile. It needs the per-identity consent
`/agents run` takes: launch approval (`--approve`), or the same confirmation
the TUI asks, sent as an RPC dialog naming the file that will run. Bounded
native subagent progress is forwarded. No durable queue or exactly-once task
semantics are introduced.

### 9. Branching

`sessions/branch` creates a branch file through the native fork or clone and then
returns with a real `switch_session`. The original session's harness is rebuilt
(`session_shutdown` and `session_start` fire, as on `/resume`), and the tool
selection made before the branch is restored. A return that an extension vetoes,
or that fails, is an error that names the branch file and the session the
runtime is actually on.

## Consequences

The ACP adapter keeps ACP framing, user consent for tool calls, persistent
indexing, retries and client presentation. Extension installation stays in the
existing CLI. No pip execution is added to the native RPC server.

## Validation

The synthetic regression coverage below is run locally and included in the
repository CI suite. Exact-head CI results are tracked on the PR:

- `tests/rpc/test_runtime_control.py`: roster coexistence, shared command
  dispatch, unknown and throwing commands, skill and template preparation,
  typed settings, posture, auth and profile service use, dialog correlation,
  and a real stdio MCP child that reattaches to a replacement harness and is
  closed.
- `tests/rpc/test_runtime_control_lifecycle_594.py`: real `AgentSessionRuntime`
  objects and `run_rpc_mode` dispatch. It covers the reload cancel, `requestId`,
  EOF, classic/control exclusion and branch cases. Factory-barrier cases cover
  an extension command's `ctx.new_session`, `ctx.fork`, `ctx.switch_session` and
  `ctx.reload` under cancel, EOF during `ctx.new_session`, and classic
  mutators during such a transition.
- `tests/rpc/test_runtime_control_surfaces_594.py`: MCP selection, the
  permission verdict, project-profile consent, capabilities, dialogs, settings
  persistence and MCP names. Its MCP cases use a real stdio server.

Real-provider evidence and remaining acceptance gaps are recorded in
`docs/reviews/pr593-runtime-control-remediation.md`. None of the above
establishes Studio UI behaviour, real-provider OAuth, a Windows packaged
installation or field acceptance. Keep those levels separate.
