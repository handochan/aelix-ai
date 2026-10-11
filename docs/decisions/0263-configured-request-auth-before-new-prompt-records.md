# 0263. Configured request auth before new prompt records

Status: Accepted (2026-10-12)
Date: 2026-10-12
Issue: #416
Supersedes: ADR-0239 decision 5's header/token/ADC restoration limit and decision
6's credential deferral for definite absence; ADR-0251 §2.4's configured union.
Amends: ADR-0254 §2.2's print/json header-only limit.

## Decision

The CLI harness factory binds its existing `set_prompt_check` with structural
`turn_refusal` followed by configured request-source presence for the **current
actual Model**. The harness already asks it after InputHandled/InputTransform
and before `on_accept("started")`, the new UserMessage, turn events and session
records. InputHandled succeeds without credentials. Core has no registry member
or upward import. Every rebuild binds the same check. Print/json and TUI retain
their structural/route gates but no longer ask auth before the input hook.

The registry uses the request's #365 effective owner model and the same static
model < provider < per-model header projection. Only provider/per-model values
use config indirection, as in the request; inspection leaves `!command` as
configured intent. A shared lower Anthropic acceptance helper and the Responses
client-key resolver own their accepted case-insensitive nonblank header sets.
Anthropic's own bearer/custom-header environment sources stay provider-scoped.
Completions, Codex and generative-ai still require a key. A custom/replacement
stream's unknown auth requirement defers to the request instead of being denied.

Runtime overrides, stored records and raw configured keys remain configured even
if invalid, empty, malformed or expired. The predicate does not execute commands,
refresh OAuth, build SDK clients, inspect credential files or use network. #379's
fatal/transient retry, events and stored-provider ownership remain request-time.
Registration OAuth is login capability, and fallback resolvers are routing
capability, not request key sources. `has_route_auth` and cwd `.env` route
admissibility are unchanged; no fallback credential is newly sent.

Vertex's request and inspection share key normalization and project/location
selection. Keys from runtime/storage/JSON/registration work without GCP env.
Project plus location selects the SDK's deferred ADC mode, including ambient
metadata/WIF. Offline absence of a local ADC file does not prove absent auth;
the guard accepts this configuration intent without discovery. Consequently a
Vertex prompt with missing/invalid actual ADC can be recorded and then fail in
the SDK. This is not a universal pre-record credential-validity guarantee.
Saved-model restore and fallback guidance ask their actual candidate, preserving
per-model headers and judging Vertex independently of the environment-only
picker runnability filter.

## Reference and validation boundaries

Latest Pi inspected before implementation: `78a3c10ba7f99a12015d6100be264c847abdba53`.
Its [prompt](https://github.com/earendil-works/pi/blob/78a3c10ba7f99a12015d6100be264c847abdba53/packages/coding-agent/src/core/agent-session.ts#L2032)
checks auth after input. [Pi #5323](https://github.com/earendil-works/pi/issues/5323)
shows why its file-only ADC heuristic blocks metadata-only credentials;
[Pi #10729](https://github.com/earendil-works/pi/issues/10729) documents synchronous
credential commands freezing the model picker. Aelix therefore inspects sources
without running Pi's async auth check or credential commands here.

Focused tests drive real CLI factories for four modes and real Anthropic/GenAI
SDK HTTP encoding under MockTransport, asserting URL/path/body/model/auth tags,
successful replies, new records and events. Interactive factory rows are not
native TUI rendering evidence. Simulated ambient ADC uses dummy credentials at
the SDK discovery seam; no metadata/real credential discovery is claimed.
Independent review, full gates, actual TUI/model and native Windows evidence
belong to the coordinated delivery pass and issue handoff.
