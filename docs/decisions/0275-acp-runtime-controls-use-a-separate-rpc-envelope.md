# ADR-0275: ACP runtime controls use a separate RPC envelope

Date: 2026-10-23. Status: proposed implementation for Aelix Studio integration.

## Context

Aelix Studio needs live model/reasoning/permission selection, slash handlers,
settings, authentication, resource inventory, MCP and native profiles through
its ACP adapter. The existing RPC contract intentionally has 29 commands.
Adding every host operation to that discriminated union would alter the Pi
compatibility roster. Copying native services into an external adapter would
create different settings/auth/command semantics.

## Decision

Accept a separate additive JSONL request envelope in RPC mode:

```json
{"id":"request-id","type":"aelix_control","method":"capabilities","params":{}}
```

Return the existing correlated response shape: `type:response`, `id`,
`command:aelix_control`, `success`, and `data` or `error`. Capability version
is 1, discovered before using other methods. The original RpcCommand union
and its roster remain unchanged.

Inject held permission, settings, AuthStorage, model-registry and profile
services at the CLI composition root. The control plane follows the live
runtime's current harness after reload/switch; it does not own a second
runtime. There are no new kernel-to-host imports.

Methods cover native commands/cancellation, resource discovery/reload, typed
user settings, permission posture/classification, active tools, client MCP,
provider auth, installed extension inventory, profiles/delegation and
non-destructive branching. Unsupported actions error rather than falling
through to a language model. Prompt-template and skill invocations are prepared
by the existing native formatters before the adapter sends a prompt.

Mutations serialize and require an idle native session. Read-only queries and
cancellation bypass the mutation lock, including permission classification
while a command is waiting for a tool. EOF cancels the owned operation.
Settings use an explicit typed allowlist/schema; clients cannot write arbitrary
fields, credentials or trust through a generic setter. Auth replies expose
status, not secrets. OAuth uses correlated transient dialogs.

Explicit `ui/enable` activates bounded select/confirm/input/editor/OAuth
dialogs. Client replies use `extension_ui_response` and the generated ID.
Dialogs cancel on disconnect or timeout. Bind this UI only around command and
delegation calls so ordinary parent tools retain the adapter's approval path.
Arbitrary terminal widgets remain unavailable on this headless surface.

ACP MCP servers use validated stdio/HTTP/SSE configurations. The host owns one
task per transport lease so AnyIO contexts close on their entering task.
Configuration replacement is transactional; native runtime replacement
reattaches client tools; EOF closes leases. HTTP headers are supported but
excluded from manifest serialization and repr. Status omits environment,
headers and URLs. This is explicit trusted client configuration, not remote
authorization or network isolation.

Profiles use AgentProfileService. Delegation is disabled unless explicitly
enabled by the caller's launch policy. Existing native child restrictions
still apply. Forward bounded native subagent progress; no durable queue or
exactly-once task semantics are introduced. Branch through native fork/clone
and restore the original session before returning the new file.

## Consequences and validation

The ACP adapter remains responsible for ACP framing, user consent, persistent
indexing, retries and client presentation. Extension installation continues
through the existing CLI into a separately managed environment, with its own
consent/integrity policy. No pip execution is added to the native RPC server.

Tests cover roster coexistence, shared command dispatch, unknown/throwing
commands, native skill/template preparation, typed settings, live posture,
auth/profile service use, dialog correlation/cancellation and a real stdio
MCP child with reconnect and cleanup. The separate adapter suite exercises
actual native processes against a local synthetic model endpoint. Those tests
do not establish Studio UI, real-provider OAuth, Windows packaged installation
or field acceptance. Keep these acceptance levels separate.
