# 0310. Opted-in RPC hosts place admitted inputs before assistant output and publish after settlement

Status: Accepted

Date: 2026-11-21

Issue: [#865](https://github.com/handochan/aelix-ai/issues/865)

## Context

ACP #41 phase 1 reports queue receipts. Studio phase 2 needs the origin, admitted content and final eligibility of each additional user row in assistant output order. The gated native CLI at `b7e9827b` reproduced three identical classic user ends with different reduced bodies, indistinguishable classic source-collision arms, a steer that crossed claims, and an R9 append later withdrawn from the active path. Additional probes found a generic request whose body differed from storage, and two admitted occurrences sharing one reducer object whose active R9 membership differed after abort. [ACP decision](https://github.com/handochan/aelix-acp/issues/41#issuecomment-7037569111) · [Native issue](https://github.com/handochan/aelix-ai/issues/865). A second stdio probe moved the same unique steer ahead of the prior assistant while active R9 order held. A pre-admission queue fault also restored a next-turn input ahead of the successor's direct prompt in both the first model request and active R9. [Companion native tests](../../tests/rpc/test_input_observation_865.py)

The owner confirmed these decisions on 2026-11-21.

- “(b) 최소 범위 + 제한 문서화 (권장)”
- “이대로 확정 (권장)”
- “1 MiB + unavailable (권장)”
- “RPC 사용자 입력만 (권장)”

## Decision

1. The CLI advertises `inputs/observe` on its stable, writer-owned R9 JSONL binding when the runtime supports the transition fence. An enable acknowledgement precedes event 1. A clean idle binding with empty input and notice queues admits a change. A repeat set keeps the epoch. A binding transition ends it with `bindingChanged`. The #865 embedding tests exercise a fence install rejection followed by classic input and host overrides with observation off and on. [Native event and discovery fixtures](../../tests/rpc/control_v1/fixtures/event.aelix_input.json) · [Embedding tests](../../tests/rpc/test_input_observation_865.py)
2. Track each occurrence at ingress with one `inputId`. A native reinsertion advances its attempt and preserves source, route and classic correlation. A queue record names the actual enqueue claim. An admission carries the consuming claim and the R9 stream identity. Post-reducer bodies and the ordered context interval qualify an available placement before the next assistant frame. [Placement and request tests](../../tests/rpc/test_input_observation_865.py)
3. Qualify committed input against the writer's settled active R9 path after writes and withdrawal. The #865 release tests cover placements, owned decisions and the claim marker before idle, including a late queue attempt that outlives its enqueue claim. When classic abort emits `turn_end` and `agent_end` with an assistant interval still open, the partial-response test checks for `stream_gap: unverifiable` before idle. [Native lifecycle tests](../../tests/harness/test_input_observation_865.py)
4. The #865 bound tests exercise strict finite JSON and UTF-8 with a 1 MiB line limit including LF and a bodyless placement when the full body cannot fit. Retained body backing storage and positional witnesses share the 4 MiB epoch budget with tracking metadata. The tests hold four near-limit snapshots and enqueue more input with a live witness. The storage-admission test measures all retained body buffers at each write and checks retirement before the over-budget fifth body can stay live. The record-count test reaches the separate 128-attempt limit. A budget overrun or unprovable claim boundary ends observation with a bounded gap. [Native bound tests](../../tests/harness/test_input_observation_865.py)
5. ACP #41 phase 2 stages the order-dependent suffix until the consuming claim closes. It should add eligible RPC steer and follow-up rows with request correlation and keep the composer row once. The native epoch observes every admitted source. [ACP #41](https://github.com/handochan/aelix-acp/issues/41)

## Limits

A parked ordinary pre-run extension hook can keep its claim open after abort, as the gated #865 hook probe measured. ACP stages its pending input until native settlement. The paused-reader probe measured delivery stalling at a full stdout pipe. ACP continues to drain stdout during staging and discards unfinished observed publication on interruption. #851 and #852 address progress separately.

The scope covers inline placement and terminal decisions for submitted occurrences. General history projection, multipart placement and automatic rebind remain deferred. The observer certifies the generic model request boundary. Provider-specific serialization and ACP/H1 publication need their own integration evidence. [Native issue](https://github.com/handochan/aelix-ai/issues/865)

## Compatibility for hosts that do not opt in

The owner confirmed the design on 2026-11-21 (“이대로 확정”) and kept it in round 6 by choosing “마지막 좋은 라운드 (권장)” on 2026-11-22. Control-v1 discovery lists `inputs/observe` in `capabilities.methods` on eligible CLI stdio bindings before the client enables observation. The writer-owned capability fixture covers this addition. ACP can expose that method in its mirrored runtime capabilities. [Control-v1 discovery fixtures](../../tests/rpc/control_v1/fixtures/capabilities.json)

The rest of the compatibility bar is the lane lead's call. For an embedder, host, subclass or extension that never sends `inputs/observe`, preserve the base commit's public classes, functions, methods and constructor parameters and their documented meaning. Preserve the number and order of host-overridable calls, what reaches the provider, what is written to session JSONL, and RPC and ACP wire bytes apart from the discovery addition above.

`agent_loop` reads `config.convert_to_llm` once per request and both classifies and calls that one value, because a host config may bind a fresh converter on every read (`tests/rpc/test_input_observation_host_compat_865.py::test_the_loop_resolves_a_host_converter_once_per_request`, which records `resolve 1 → convert 1` and the provider receiving `request 1`; pi's `agent-loop.ts` also calls `config.convertToLlm` once per turn). Every attribute or helper this change adds to a pre-existing public class carries a leading underscore — `RpcControlPlane._input_observation`, `TranscriptService._input_fence`, `JsonlSessionStorage._settled_input_coordinates` — so a host subclass that already owns the public name (a read-only `input_observation` or `input_fence` property, a `settled_input_coordinates` attribute) constructs and settles as it did before (the three `..._with_its_own_...` tests in the same module). A subclass attribute that collides with one of those underscore names is outside the bar.
