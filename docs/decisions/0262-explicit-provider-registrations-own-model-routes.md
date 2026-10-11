# 0262. Explicit provider registrations own model routes

Status: Accepted (2026-10-11)
Date: 2026-10-11
Issue: #365
Supersedes: ADR-0249's extension model merge and provider-name-only child pin;
ADR-0250's registration universe exception and builtin-name `/model` limit;
ADR-0251 §2.3's deferred extension key order and replacement of JSON headers.

## Problem and evidence

An extension registering `openai` with private models left OpenAI's catalog rows
in the registry. `/model` could select a vendor row and authenticate it with
the extension's provider-wide key. Some historical launch cases were already
guarded by #362; they are not all current reproductions.

Root's isolated real-harness/SDK baseline on `8eaf4401` observed a previously
selected vendor Model receiving extension A's key, and a stale same-id Model
on endpoint A receiving a re-registration's B key. Explicit `models={}` kept
48 OpenAI rows. A further same-route baseline observed stale Model.headers
Authorization A overriding the new configured key B. The SDK's prepared HTTPX
request was intercepted and answered synthetically; no external request or
actual credential was used. These measurements establish the request boundary,
not actual CLI/TUI/child behavior. Root keeps the baseline/fixed comparison kit
and separate full/live evidence in the issue handoff.

## Decision

1. `ProviderConfigInput.models=None` augments auth/display/headers and retains
   builtin/models.json rows. An explicit map replaces all lower rows for the
   exact registration name, including an empty map. Empty ownership MUST remain
   empty through launch lookup, registry access, refresh/reset and catalog refill.
   A JSON baseUrl does not cancel explicit registration ownership.
2. Models MUST be copied and placed in the registration's namespace, including
   a caller Model that declares a different provider. Caller containers are
   copied at both API queue and registry boundaries. Re-registration remains
   **last full config wins**, rather than Pi's partial merge. Refresh/reset retain
   the registration; unregister restores the builtin/JSON layers. Failed reloads
   keep the prior registration as before (#367).
   Before loading, previous and current explicit owners' projections are removed;
   a contained loader failure cannot publish their old rows under new credentials.
   Ownership names remain known when a new explicit scope could not be loaded.
3. Exact registered models and the existing custom-id escape hatch remain
   available. A custom id uses only the effective owner's siblings, requiring
   one API and one nonempty host; split/unknown/hostless projections are refused.
   This is replacement, not a strict listed-id allowlist. A bare id does not
   license inventing an owner. Scoped exclusions cannot be resurrected as backfills.
   OAuth can project the owner's declared models, but cannot repopulate removed rows.
4. For an explicit-model owner, before resolving request auth the registry MUST verify that the Model's
   provider/id/API/base URL matches the current owner or its safe backfill.
   It MUST also verify Model.headers against that effective projection: adapters
   merge Model.headers again, so returning current headers alone would retain a
   stale credential header that the new registration removed. Name, cost and
   context metadata may differ. Re-selecting the current model restores normal
   operation, including after OAuth projects a different route or header map.
   Releasing ownership with None/unregister retains the generic caller-Model
   contract; this rule does not deny ordinary nonowned models. Nonowned/None-augmentation Model headers and intentional request
   options headers keep their existing behavior.
5. The load generation is captured before auth and checked after awaits. A
   registration change during resolution refuses the request without attaching
   the resolved key/headers or suggesting a retry. Ownership refusal returns
   `ok=False`; returning no opinion would allow SDK environment fallback. The
   common callback carries an optional pure validator of that same successful
   auth snapshot. Core checks the live model's route/header identity, and AI
   dispatch checks before entering the stream and again after the payload hook
   completes, before the built-in adapter opens its SDK request. Request and
   payload hooks can unregister/re-register/switch models, but that cancels the
   pending request rather than sending its old auth snapshot. Validation never
   resolves keys, refreshes OAuth or runs credential commands again. Closing
   failure is nonretryable. Core records its fatal classification independently
   of adapter error events and the message_end replacement, reusing the typed
   request-setup failure record with no retry reason. A status-like string in
   its text cannot restart auth/dispatch. Ordinary SDK 503 and #379's original
   auth-resolution retries stand, and a later user turn gets a fresh snapshot.
   Compaction, turn-prefix and branch summaries forward the same validator.
   Existing callbacks without validators and streams without payload hooks keep
   their behavior; a validator-only result gives no credential opinion, preserving
   native/env auth. No coding-agent import is introduced into lower packages.
6. The key order is runtime → stored-owning → configured extension key (or JSON
   key if absent) → provider environment. Stored-entry failures and #379's
   transient OAuth retry retain their contract. JSON provider headers merge
   with extension headers, extension collisions win, and only `auth_header=None`
   inherits JSON; explicit False clears it. JSON per-model headers belonging to
   replaced rows are removed. Status reports the configured source at the same
   priority. Command caches and `.env` route provenance remain unchanged.
7. Print and RPC delegation MUST carry a requirement for an explicit owner
   whenever the same child-model decision selected one in the parent. The
   internal argv contains only a SHA-256 route digest: base URLs can contain
   credentials and MUST NOT be copied into argv/environment/error text. The
   child validates after setup registration bind and before runtime key
   attachment. The requirement stays in its registry for every subsequent auth
   request, including closing checks after request/payload hooks, summaries, unregister, auth-only re-registration, model
   switches and rebuilds. Same name/id on another route and missing registration
   refuse. Fresh child flags do not inherit an ancestor requirement. Ordinary
   builtin children, explicit profile extensions reproducing the owner, and
   unresolved child-only extension selections remain supported.

## Kernel boundary

The authorized kernel paths are `harness/core.py` (snapshot validation and typed
failure identity), `session/compaction.py` (main and turn-prefix validator
forwarding), `session/branch_summarization.py` (branch validator forwarding),
`types.py` (the appended optional terminal-response predicate), and `loop.py`
(the predicate boundary after `message_end`, before tools and queue consumption).
These are generic callbacks: provider ownership and child policy remain in
coding-agent. No registry import, delegation cap, spawn or consent policy enters
the kernel. The P2 kernel-change gate allows these specific paths under this ADR.

## Reference and verification boundary

Pi primary source at `f44707e7f7190a8cc86ce105790c72c6b0f5fd5f`:
[provider-composer.ts](https://github.com/earendil-works/pi/blob/f44707e7f7190a8cc86ce105790c72c6b0f5fd5f/packages/coding-agent/src/core/provider-composer.ts)
replaces explicit models, stamps provider ids and composes configured keys and
headers. [model-runtime.ts](https://github.com/earendil-works/pi/blob/f44707e7f7190a8cc86ce105790c72c6b0f5fd5f/packages/coding-agent/src/core/model-runtime.ts)
merges re-registration fields; Aelix deliberately preserves its full replacement
contract. Its async/dynamic runtime is outside #365 (#434).

Regression sources: `test_provider_ownership_365.py` (scope/lifecycle/API.bind),
`test_provider_ownership_wire_365.py` (actual SDK requests and no-send),
`test_child_ownership_365.py` (real print/RPC subprocesses with HTTPX interception),
and existing routing/auth/late-registration suites. Fake keys and isolated
agent/settings/cwd fixtures are used; no test requires actual credentials.
Root runs actual TUI/model controls and full/CI separately. #416's auth availability
and prompt-recording gate are not implemented here.

## Independent review repair

The first frozen candidate `815fc4db` passed its initial auth-boundary tests, but
independent real-harness (2) and print/RPC subprocess (4) probes showed that a
normal `before_provider_request` or `before_provider_payload` hook could call
`api.unregister_provider("openai")` after auth returned and still initiate the
SDK request. The closing validator above repairs that confirmed interval.
Source snapshot validation at auth return alone was insufficient. The reviewer
used HTTPX interception and fake tags; this is not an actual credential/network
claim. A stale parent's spawn-time ownership attestation is a separate policy;
this change retains the agreed selected-route/child explicit-owner requirement.

The first repair `64985bda` closed the hook interval, but a fresh generic validator
that refused with `synthetic 503 closing refusal` was converted by both OpenAI
adapters into an error event. The retry regex then requested a second auth
snapshot; an independent variant that accepted that next snapshot sent an SDK
request. The fatal setup-state record above preserves the original classification
through that conversion and message replacement. It does not suppress ordinary
provider retries or alter the displayed refusal text.

## Approved repair 3: terminal response boundary

Repair 2 stopped regex retries, but its final message could still be replaced
with `stop` or `tool_use`. The low-level loop then executed tools or drained
steering/follow-ups before the harness classified the failure, obtaining a new
auth snapshot in the same user prompt. Independent real SDK, actual registry
and print/RPC child probes confirmed 14 failing rows on `e854648a`.

The user approved additional #365 repair and independent validation on
2026-10-12 KST after the two-failed-repair automatic-stop boundary. Those failures
remain recorded; this approval resumes repair 3 rather than erasing the history.

The kernel's optional pure synchronous `should_stop_after_response` predicate
runs on the final `message_end` result before scanning tools or consuming
queues. Core supplies the authoritative setup-failure identity. Presentation
hooks still replace the message, but cannot turn that refused response into
execution. Fatal closing refusals end the same loop with queues retained for a
later user prompt. Transient OAuth setup failures also end their current loop;
the existing outer harness alone decides their retry. Ordinary provider retries,
new user turns and default kernel callers retain their behavior. The optional
field is appended to preserve existing positional construction. Committed
inputs remain once; unconsumed queued input retains its ordering (#320).
