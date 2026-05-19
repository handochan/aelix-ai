# 0034. Pi Reference Version Pin

Status: Accepted (Sprint 2.5 shipped)

## Context

ADR-0003 names pi agent as the primary reference but doesn't pin a version.
As Pi evolves on `main`, Aelix line citations drift and parity-audit
reproducibility breaks. Every Phase 1.x ADR that quotes Pi line numbers (e.g.
ADR-0017's "Pi `AgentHarnessEvent` at `types.ts:467-469`", ADR-0021's
"`packages/agent/src/harness/agent-harness.ts:369,381,391`") is silently
anchored to whatever SHA the contributor happened to read at authoring time.
Without an explicit pin, a critic-pass three weeks later can find the cited
line moved 20 lines down, breaking the audit chain.

## Decision

Pin Pi to a specific commit SHA per sprint.

**Current pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`**
(`main` HEAD as of 2026-05-17, commit message "chore: approve contributor
mattiacerutti").

**Sprint 6a amendment (P-37, 2026-05-17):** ADR-0034 originally cited the
repo slug as `badlogic/pi-mono`. Verified at SHA `734e08e…` against the
canonical upstream — the correct slug is `earendil-works/pi`. The SHA is
unchanged; the repo slug fix is a documentation-only correction so future
Pi-parity citations resolve cleanly via `gh repo view earendil-works/pi`
or `git clone https://github.com/earendil-works/pi`. The legacy
`badlogic/pi-mono` mirror remains accessible at the same SHA via
`https://raw.githubusercontent.com/badlogic/pi-mono/734e08e…/…` and is
the URL pattern most existing ADRs still cite — both are valid resolves
at this pin.

Update policy:

1. Each new sprint that imports new Pi features MAY move the pin forward.
2. The sprint spec MUST cite the new SHA in its preamble.
3. Every ADR that quotes Pi MUST cite the SHA (either inline or by reference
   to this ADR's "current pin").
4. When the pin moves, the previous pin's SHA is appended to the "Pin history"
   table below for traceability.

### Pin history

| Sprint | Pin SHA | Date | Reason |
|--------|---------|------|--------|
| 2.5 (Phase 1.4) | `734e08edf82ff315bc3d96472a6ebfa69a1d8016` | 2026-05-17 | initial pin; spec citations anchored |

### Sprint 6b amendment (Pi `KnownApi` cardinality, 2026-05-18)

Pi at this pin exposes **9 `KnownApi` values** (`types.ts:7-16`):
`anthropic-messages`, `openai-completions`, `openai-responses`,
`azure-openai-responses`, `openai-codex-responses`,
`mistral-conversations`, `google-generative-ai`, `google-vertex`,
`bedrock-converse-stream`.

| Sprint | Adapter | Status | Owner ADR |
|---|---|---|---|
| 6a | `anthropic-messages` | shipped | ADR-0045 |
| 6b | `openai-completions` | shipped (W6 closure 2026-05-18) | ADR-0047 |
| deferred | the 7 above | tracked in `PHASE_4_2_DEFERRED_APIS` | ADR-0050 §J |

Sprint 6b is **2 of 9** adapters live. The 7 deferred apis remain in
`PHASE_4_2_DEFERRED_APIS` per ADR-0050 §J forward-compat clause: any
future PR that lands an adapter MUST drop it from the allowlist in the
same PR (enforced by the closure pin).

### Sprint 6c amendment (OAuth surface partition, 2026-05-18)

Sprint 6c adds the OAuth surface (Anthropic only).

| Component | Status | Owner ADR |
|---|---|---|
| OAuth client framework (types + PKCE + page + callback server + registry + high-level) | shipped | ADR-0051 |
| Anthropic OAuth flow (`oauth/anthropic.py`) | shipped | ADR-0052 |
| `AuthStorage` JSON layer (atomic write + 0o600 + flock) | shipped | ADR-0053 |
| GitHub Copilot OAuth | deferred to Sprint 6e | ADR-0053 |
| OpenAI Codex OAuth | deferred to Sprint 6e | ADR-0053 |
| `AuthStorage` layered cascade (runtime-override + env + fallback resolver) | deferred to Sprint 6e | ADR-0053 |
| RPC mode (`rpc-mode.ts` + `rpc-client.ts` + `rpc-types.ts` + `jsonl.ts`) | deferred to Sprint 6d | ADR-0054 |

Sprint 6c is **1 of 3** OAuth providers live (Anthropic). The 2
deferred providers remain in `_OAUTH_DEFERRED_PROVIDERS` per ADR-0055
forward-compat clause; the closure pin
(`tests/pi_parity/test_phase_4_3_strict_superset.py::test_pi_oauth_provider_total_equals_3`)
asserts `live ∪ deferred == {anthropic, github-copilot, openai-codex}`
exactly.

### Sprint 6d amendment (RPC mode JSONL protocol, 2026-05-19)

Sprint 6d adds the RPC mode surface (JSONL protocol + 29-variant Pi
RpcCommand union + dispatcher + subprocess client + CLI `--mode rpc`
flag). The Pi `rpc-mode.ts` / `rpc-client.ts` / `rpc-types.ts` /
`jsonl.ts` files at this pin total ~1,155 LOC.

| Component | Status | Owner ADR |
|---|---|---|
| `aelix_coding_agent.rpc._jsonl` (LF framing + StringDecoder + CR strip + tail emit) | shipped | ADR-0056 |
| `aelix_coding_agent.rpc.rpc_types` (29-variant RpcCommand + envelope + SessionState + 9-method UI request + 3-shape UI response) | shipped | ADR-0057 |
| `aelix_coding_agent.rpc.rpc_mode` (9 supported handlers + 20 deferred error stubs + event pipe + signal handlers) | shipped | ADR-0058 |
| `aelix_coding_agent.rpc.rpc_client` (subprocess wrapper + 29-method command surface + `wait_for_idle`/`collect_events`/`prompt_and_wait`) | shipped | ADR-0058 |
| CLI `--mode {interactive,rpc}` flag | shipped | ADR-0058 |
| `AgentHarness` public properties (`pending_message_count` / `session_file` / `session_name` / `steering_mode` / `follow_up_mode`) | shipped | ADR-0058 |
| Sub-sprints 6e (ModelRegistry / extension+skill aggregation) + 6f (steer/follow_up / session-tree / bash cancel / UI bridge) | deferred | ADR-0058 |

Sprint 6d is **9 of 29** RpcCommand variants live. The 20 deferred
commands remain in `rpc_mode.DEFERRED_COMMANDS` per ADR-0058
forward-compat clause; the closure pin
(`tests/pi_parity/test_phase_4_4_strict_superset.py`) asserts
`SUPPORTED_COMMANDS ∪ DEFERRED_COMMANDS == RPC_COMMAND_TYPES` and
`len(RPC_COMMAND_TYPES) == 29`.

### Sprint 6e amendment (OAuth catalog complete + AuthStorage cascade, 2026-05-19)

Sprint 6e closes the OAuth catalog (Anthropic + Copilot + Codex —
3 of 3 Pi providers live), ships the 12-method `AuthStorage`
layered cascade, wires the `modify_models` Protocol callback
(Copilot first consumer), and adds the `aelix auth login/logout/
status/list` CLI subcommand.

| Component | Status | Owner ADR |
|---|---|---|
| GitHub Copilot OAuth (device-code grant + `proxy-ep` base URL + `modify_models` callback) | shipped | ADR-0059 |
| OpenAI Codex OAuth (PKCE callback port 1455 + JWT account_id extraction + `originator=pi`) | shipped | ADR-0060 |
| `AuthStorage` layered cascade (12 methods: `set_runtime_api_key` / `remove_runtime_api_key` / `set_fallback_resolver` / `has_auth` / `get_auth_status` / `list` / `has` / `get_all` / `drain_errors` / `login` / `logout` / `get_api_key_cascade`) | shipped | ADR-0061 |
| `resolveConfigValue` helper port (`!cmd` + env-ref expansion) | shipped | ADR-0061 |
| `AuthStatus` (frozen) + `AuthSource` Literal (6 values) + `FallbackResolver` type alias | shipped | ADR-0061 |
| `OAuthProvider.modify_models` Protocol callback wired (Sprint 6c P-102 forward-compat closed) | shipped | ADR-0059 |
| `aelix auth login/logout/status/list` CLI subcommand (preserves `--mode rpc` back-compat) | shipped | ADR-0062 |
| Shared `_helpers.maybe_await` (drained duplicate definitions across providers, P-157) | shipped | ADR-0059 / 0060 |
| ADR-0053 layered-resolution carry-forward marked **RESOLVED** | shipped | ADR-0053 / 0061 |
| `enableGitHubCopilotModel()` automation (per-model `/models/{id}/policy` POST) | deferred to Sprint 6f | ADR-0063 |
| Codex `chatgpt_account_id` header propagation (paired with OpenAI Responses adapter) | deferred to Sprint 6f | ADR-0063 |
| `--api-key <provider>:<key>` CLI flag (surfaces `set_runtime_api_key`) | deferred to Sprint 6f | ADR-0063 |
| `models_json_key` / `models_json_command` AuthSource consumers | deferred to Sprint 6f | ADR-0063 |

Sprint 6e is **3 of 3** Pi OAuth providers live. The
`_OAUTH_DEFERRED_PROVIDERS` allowlist is **drained to `{}`** per
ADR-0063 forward-compat clause; the closure pin
(`tests/pi_parity/test_phase_4_5_strict_superset.py`) asserts
`live ∪ deferred == {anthropic, github-copilot, openai-codex}` and
`len(live) == 3`. Pi key names (`accountId` / `enterpriseUrl`) are
preserved verbatim in the persisted `auth.json` shape so a Pi-
written file opens cleanly in Aelix. The Pi `resolveConfigValue`
helper is ported as `aelix_ai.oauth._resolve_config`.

## Consequences

- Parity audits become reproducible — the W5 audit lane can `git checkout`
  the pinned SHA to validate every Pi citation.
- Forward-port effort becomes visible per-sprint as the delta between the
  previous and new pin.
- Existing ADRs (0017, 0018, 0019, 0021, 0022, 0023, 0025) are silently
  anchored to this SHA going forward; if a quote breaks against a newer SHA,
  that's a Phase 2.x action item, not a Phase 1.4 bug.
- Phase 2.1 specs MAY introduce a `PI_PIN` constant in `pyproject.toml` or
  `docs/` to make the pin machine-readable for future tooling — out of scope
  for Phase 1.4.

## Related

- ADR-0003 — pi agent as primary reference (this ADR refines the binding).
- ADR-0029 — Pi-parity acceptance test harness (will consume this pin once
  vendored fixtures are introduced).
- ADR-0032 — Sprint workflow review + Pi parity audit (W5 audit consumer).

## Phase

Sprint 2.5 / Phase 1.4 (shipped).
