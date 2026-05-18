# 0050. Phase 4.2 Strict Superset Closure

Status: Accepted (Sprint 6b / Phase 4.2 / W6 shipped)

## Context

ADR-0039 / ADR-0040 / ADR-0044 / ADR-0046 established the Aelix
strict-Pi-parity-superset invariant for Phases 2.1 / 2.2 / 3 / 4.1.
Each closure ADR pins a regression-guard test under `tests/pi_parity/`
that asserts every Pi-verified surface in scope has a corresponding
binding in Aelix, OR sits in a `DEFERRED_ALLOWLIST` with an owning ADR.

Sprint 6b lands the **second** of nine Pi `KnownApi` adapters
(`openai-completions`) plus shared infrastructure (`_transform_messages`,
`_sanitize_unicode`, `_streaming_json`, `_env_api_keys`). The W4 code
review + W5 Pi parity audit produced **5 BLOCKING + 6 MAJOR + 13 MINOR**
drift findings; Sprint 6b W6 applied the must-fix triage.

Closure date: **2026-05-18**. Pi SHA pinned by ADR-0034:
`734e08edf82ff315bc3d96472a6ebfa69a1d8016`.

## Decision

The Phase 4.2 strict-superset closure pin is
`tests/pi_parity/test_phase_4_2_strict_superset.py`. It asserts the
Sprint 6b roster (P-47 → P-82 + C-1 + M-1..M-6) PLUS the cumulative
invariant from ADR-0039/0040/0044/0046 (every Pi event has an emit site,
every Pi `KnownApi` is registered or deferred-with-ADR).

### Roster (Sprint 6b)

| Finding | Subject | Resolution |
|---|---|---|
| **P-47** | Sprint 6a sub-sprint LOC estimate was 2.5× too low | binding spec §0 acknowledges Pi reality (1,074 + 218 + 210 LOC) |
| **P-48** | OpenRouter is NOT a separate `KnownApi` | one adapter registration; compat auto-detection lives inside `_detect_compat` |
| **P-49** | Pi `KnownApi` cardinality drift in ADR-0034 | ADR-0034 amended (Sprint 6b: 2 of 9 live; 7 deferred) |
| **P-50** | `transformMessages` is cross-provider shared infra | NEW shared module `_transform_messages.py` (ADR-0048); Anthropic retrofit deferred (P-50-followup) |
| **P-51** | `mapStopReason` divergence between Pi providers | per-adapter `_map_stop_reason` shipped; no shared helper |
| **P-52** | `convertMessages` mutates `params` cache_control side-effect | return-by-value mutation preserved per Pi parity |
| **P-53** | Streaming SSE iteration order | lazy `*_start` + per-chunk `*_delta` + per-block `*_end` mirrored byte-for-byte |
| **P-54** | `parseStreamingJson` is a Pi utility | `_streaming_json.parse_streaming_json` shipped |
| **P-55** | `sanitizeSurrogates` is a Pi utility | `_sanitize_unicode.sanitize_surrogates` shipped |
| **P-56** | `headersToRecord` is a Pi utility | `dict(httpx_response.headers)` — no port needed |
| **W6 P-57** | `_map_stop_reason` returned `"tool_use"` instead of Pi `"toolUse"` | adapter returns `"toolUse"`; `_anthropic_transforms._ANTHROPIC_STOP_REASON_MAP` aligned; `_done_reason_from_stop_reason` paper-over deleted |
| **W6 P-58 / P-67** | `ThinkingContent` dataclass missing | added; OpenAI adapter populates `thinking_signature` from captured reasoning field name (ADR-0049) |
| **W6 P-59 / M-1** | OpenRouter routing dataclass path silently no-ops + operator precedence | explicit parens; `build_params` reads `compat.open_router_routing` directly; `_pick` accepts camelCase aliases |
| **W6 C-1 / P-60** | `_open_stream` called SDK with wrong shape; never used `.with_raw_response` | `client.chat.completions.with_raw_response.create(**params, **request_options)`; reads `raw.parse()` + `raw.http_response`; drops unsupported `signal` / `max_retries` per-request kwargs |
| **W6 P-61** | `ImageContent` missing `mime_type` / `data` fields | added (ADR-0049); OpenAI + Anthropic adapters prefer the split fields over legacy `source` |
| **W6 P-62** | `stream_simple_openai_completions` lazy-raised + `clamp_thinking_level` stub | sync factory raises auth eagerly (Pi parity); new `aelix_ai.models.clamp_thinking_level` helper |
| **W6 P-63** | `convert_tools` Anthropic-leak `input_schema` fallback | dropped; reads `tool.parameters` only |
| **W6 P-66 / M-4** | dead `cleaned` loop in error path | replaced with `cleaned = list(output_content)` + comment explaining Aelix scratch is off-block |
| **W6 P-68** | `_is_same_model` always returned False | added `AssistantMessage.api/provider/model` provenance trio (ADR-0049); `_is_same_model` reads directly |
| **W6 P-75** | Missing `tool_name` on synthetic toolResult in `_flush_synthetic` | added `ToolResultMessage.tool_name` (ADR-0049); orphan synthesis propagates it |
| **W6 P-76** | Closure pin lacks behavior assertions | parametrized `_map_stop_reason` against the fixture; `detect_compat` rows; `thinking_format` Literal exhaustiveness |
| **W6 P-79** | W0 fixture missing `minimax` / `minimax-cn` rows | added |
| **W6 M-2** | Qwen never detected; not in `COMPAT_DEFERRED_ALLOWLIST` | added `qwen` + `qwen-chat-template` entries with owning ADR |
| **W6 M-6** | `_normalize_tool_call_id` 40-char clamp unconditional | dropped `provider == "openai"` gate; clamps for every caller |

### Closure invariant

```
PHASE_4_2_DEFERRED_APIS = {
    "openai-responses": "ADR-0050 §J — separate sprint",
    "openai-codex-responses": "ADR-0050 §J — separate sprint",
    "azure-openai-responses": "ADR-0050 §J — separate sprint",
    "mistral-conversations": "ADR-0050 §J — separate sprint",
    "google-generative-ai": "ADR-0050 §J — separate sprint",
    "google-vertex": "ADR-0050 §J — separate sprint",
    "bedrock-converse-stream": "ADR-0050 §J — separate sprint",
}

COMPAT_DEFERRED_ALLOWLIST = {
    "cloudflare-workers-ai": "ADR-0050 §J — Sprint 6d compat zoo",
    "cloudflare-ai-gateway": "ADR-0050 §J — Sprint 6d compat zoo",
    "github-copilot": "ADR-0050 §J — Sprint 6d compat zoo",
    "vercel-ai-gateway": "ADR-0050 §J — Sprint 6d compat zoo",
    "qwen": "ADR-0050 §J — qwen detection deferred",
    "qwen-chat-template": "ADR-0050 §J — qwen detection deferred",
}
```

**Invariant**: 2 of 9 `KnownApi` adapters live + 7 owned by
`PHASE_4_2_DEFERRED_APIS` + 4 compat zoo targets + 2 thinking-format
detection paths in `COMPAT_DEFERRED_ALLOWLIST`. Every value in
`OpenAICompletionsCompat.thinking_format` is either reachable from
`detect_compat` OR present in `COMPAT_DEFERRED_ALLOWLIST` (the
`test_thinking_format_literal_exhaustively_covered` trip-wire enforces
this mechanically).

### What ships

- `_env_api_keys.py` + `_sanitize_unicode.py` + `_streaming_json.py` +
  `_openai_client.py` + `_openai_compat.py` + `_transform_messages.py`
  + `openai_completions.py` (~1,170 prod LOC).
- `aelix_ai.models.clamp_thinking_level` (Sprint 6d will extend with
  `Model.thinking_level_map` per ADR-0050 §Carry-forward).
- 17-field `OpenAICompletionsCompat` dataclass (Pi parity verbatim).
- `OPENAI_COMPLETIONS_PROVIDER` registered under
  `source_id="aelix-ai.builtin"` (Sprint 6a precedent).
- Pi parity closure pin
  `tests/pi_parity/test_phase_4_2_strict_superset.py` with
  parametrized behavior assertions (P-76 fix).
- Sprint 6b W6 regression tests
  `tests/providers/test_w6_regressions.py` (9 tests pinning P-57..P-75
  / C-1 / M-6 explicitly).
- Sprint 6b W0 fixture
  `tests/pi_parity/fixtures/pi_openai_completions_734e08e.json`
  amended with `minimax` / `minimax-cn` rows (P-79 fix).

### Forward-compat clause

Phase 4.2 adapter coverage is now at 2 of 9. Any future Pi sprint that
adds:

1. A new Pi `KnownApi` MUST either:
   - Land the corresponding Aelix adapter in the same PR.
   - Add an entry to `PHASE_4_2_DEFERRED_APIS` with an owning ADR.
2. A new compat detection path MUST either:
   - Land the Aelix detection branch in the same PR.
   - Add an entry to `COMPAT_DEFERRED_ALLOWLIST` with an owning ADR.

The forward-compat clauses from ADR-0039 / ADR-0046 continue to apply:
any deferred entry that subsequently gains the missing binding MUST be
dropped from the allowlist in the same PR (enforced by the closure
pin's exhaustiveness assertions).

## Consequences

### Carry-forward — Sprint 6d (cross-adapter hygiene)

The following findings were triaged as deferred — they sit outside the
W6 must-fix scope and ship in Sprint 6d alongside the Anthropic
retrofit (P-50-followup):

- **P-50-followup** — Retrofit Sprint 6a Anthropic adapter onto the
  shared `_transform_messages.py` boundary; delete
  `_anthropic_transforms.transform_messages` after the retrofit.
- **Anthropic adapter ADR-0049 wiring** — Populate
  `AssistantMessage.api/provider/model` + `ThinkingContent` blocks on
  the Anthropic adapter's end-of-stream output. The ADR-0049 dataclass
  fields are additive so nothing breaks today; Sprint 6d closes the
  gap.
- **P-65** — `thinking_level_map` field on `Model` + lookup in
  `build_params`. Sprint 6b ships the simple `xhigh → high` fallback
  in `aelix_ai.models.clamp_thinking_level`; the full Pi-shape map
  lands when other `Model` fields do.
- **P-71** — `_streaming_json` control-character repair pass
  (a Sprint 6d hardening item; the Sprint 6b parser handles JSON
  truncation but not embedded control chars).
- **P-72** — `on_response` integration test against the real SDK
  (downstream of P-60 fix; Sprint 6d hardening).
- **W4 M-3** — thinking-signature `reasoning_field` name on the wire
  — the assistant-message field name (compat-determined string) is a
  Sprint 6d wire-format hardening item.
- **W4 m-1 .. m-13** — code-quality cleanups (Sprint 6d hygiene).
- **W4 NITS** — naming + comments (Sprint 6d hygiene).

### Immediate consequences

- The `_transform_messages` shared module is the durable boundary every
  adapter will route through; Sprint 6d retrofit of Anthropic is the
  single remaining adapter-side cleanup.
- The `aelix-ai.builtin` source_id now removes both Anthropic and
  OpenAI Completions adapters on
  `unregister_providers_by_source("aelix-ai.builtin")`.
- The W6 regression test suite means a future PR that reintroduces the
  `"tool_use"` spelling drift trips
  `test_map_stop_reason_matches_pi_fixture` mechanically.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6b).
- ADR-0045 — Provider Adapter Interface (amended Sprint 6b §F.2).
- ADR-0046 — Phase 4.1 strict superset closure (forward-compat clause
  inherited).
- ADR-0047 — OpenAI Completions adapter.
- ADR-0048 — Pi shared utilities.
- ADR-0049 — Message dataclass extensions.

## Phase

Sprint 6b / Phase 4.2 (shipped — closure pin Green; 2 of 9 KnownApi
adapters live; 7 + 4+2 deferred with owning ADR-0050).
