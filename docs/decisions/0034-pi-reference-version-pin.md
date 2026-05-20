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

### Sprint 6f amendment (ModelRegistry runtime + Model field shape, 2026-05-19)

Sprint 6f shipped the ModelRegistry runtime (14 methods + 2 factory
constructors) per ADR-0065, the Pi `Model` field shape expansion
(`cost` / `thinking_level_map` / `max_tokens` / `context_window` /
`headers` — 5 new fields) per ADR-0064, the 7 Pi-parity helpers in
`aelix_ai.models`, a 13-model seed catalog (Anthropic + OpenAI +
GitHub Copilot), and the 3 RPC model commands (`set_model` /
`cycle_model` / `get_available_models`) from ADR-0058's deferred set.

| Component | Status | Owner ADR |
|---|---|---|
| `ModelCost` (per-million rate, frozen) + `UsageCost` (resolved, mutable) + `Usage` + `Cost = ModelCost` back-compat alias | shipped | ADR-0064 |
| `Model.thinking_level_map` + `Model.max_tokens` + `Model.context_window` + `Model.headers` (P-178 wire) | shipped | ADR-0064 |
| `aelix_ai.models` 7 Pi-parity helpers + `EXTENDED_THINKING_LEVELS` (6-value Pi parity) | shipped | ADR-0066 |
| `ModelRegistry` 14-method runtime + `create` / `in_memory` factories + `ResolvedRequestAuth` (P-180 bool) + `ProviderConfigInput` | shipped | ADR-0065 |
| `set_current_model` writes `_state.model` directly (P-187 — no override layer); `current_model` is a thin reader | shipped | ADR-0065 |
| `set_model` / `cycle_model` / `get_available_models` RPC commands (moved from `DEFERRED_COMMANDS` → live) | shipped | ADR-0058 / 0066 |
| `models_generated.py` 13-model seed catalog (≥3 providers) | shipped | ADR-0064 |
| Full `models.generated.ts` catalog port (428 KB → `models_generated.json`) | deferred to Sprint 6g | ADR-0066 |
| `model-resolver.ts` port (~530 LOC, partial-id matching + provider auto-detect) | deferred to Sprint 6g | ADR-0066 |
| `applyProviderConfig` for `register_provider.config.models` + `models.json` schema validation | deferred to Sprint 6g | ADR-0066 |
| `enableGitHubCopilotModel()` post-login POST automation | deferred to Sprint 6g | ADR-0066 |
| `get_commands` RPC command (extension/skill/template aggregation) | deferred to Sprint 6g | ADR-0066 |
| 16 remaining RPC commands (ADR-0058 minus the 3 shipped here) | deferred to Sprint 6g | ADR-0066 |
| Pi `Model.compat` / `knowledgeCutoff` / `releaseDate` fields | deferred to Sprint 6g | ADR-0064 |
| `image-models.ts` / `image-models.generated.ts` Pi parallel registry | deferred to Sprint 6g | ADR-0066 |

Sprint 6f shipped ModelRegistry runtime (14 methods) + 3 model RPC
commands. `set_current_model` writes `_state.model` directly (P-187).
Headers field added to Model (P-178). Full catalog port +
`model-resolver.ts` + `get_commands` + 16 remaining RPC commands
deferred to Sprint 6g per ADR-0066 forward-compat clause. The closure
pin (`tests/pi_parity/test_phase_4_6_strict_superset.py`) asserts
`DEFERRED_COMMANDS` shrinks 20 → 17 and `SUPPORTED_COMMANDS` rises
9 → 12, with `SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved.

### Sprint 6g₁ amendment (model-resolver port + full Pi catalog + KnownProvider + Model.compat, 2026-05-20)

Sprint 6g₁ ports `coding-agent/src/core/model-resolver.ts` (637 LOC,
7 public functions + 3 private helpers + `defaultModelPerProvider`
32-row map) and transfers the full **16,386-line
`models.generated.ts` catalog** (32 providers, **942 models**) into
Aelix as `models_generated.json` loaded at module import. Adds
`KnownProvider` Literal in Pi semantic order (P-208) +
`Model.compat: dict[str, Any] | None` passthrough (P-200/P-210 —
`_openai_compat.get_compat` merge confirmed wired) + ports
`isValidThinkingLevel` + `DEFAULT_THINKING_LEVEL = "medium"` (P-205
— the W1 spec draft incorrectly said `"off"`; the actual Pi value
at the pinned SHA is `"medium"`).

| Component | Status | Owner ADR |
|---|---|---|
| `aelix_ai.streaming.KnownProvider` Literal (32 strings, Pi semantic order — P-208) | shipped | ADR-0067 |
| `aelix_ai.streaming.Model.compat: dict[str, Any] \| None` passthrough field (P-200) | shipped | ADR-0067 / ADR-0064 |
| `aelix_ai.models_generated.json` (32 providers / 942 models / fail-fast load — P-209) | shipped | ADR-0067 |
| `aelix_ai.models_generated.py` JSON loader (replaces Sprint 6f₁ seed) | shipped | ADR-0067 |
| `aelix_coding_agent.core.defaults` (`DEFAULT_THINKING_LEVEL = "medium"` + `is_valid_thinking_level`) | shipped | ADR-0067 |
| `aelix_coding_agent.core.model_resolver` (7 functions + 3 helpers ported verbatim) | shipped | ADR-0067 |
| `RestoreModelResult` frozen dataclass (P-206 — mirrors other 4 return shapes) | shipped | ADR-0067 |
| `_glob_match_pi_minimatch` per-segment helper (P-207 — `/`-boundary semantics) | shipped | ADR-0067 |
| `_openai_compat.get_compat` catalog `Model.compat` merge (P-210 — confirmed wired) | shipped | ADR-0067 |
| `tests/pi_parity/test_phase_4_7_strict_superset.py` closure pin (32 tests) | shipped | ADR-0068 |
| Typed `Model.compat` discriminated union (`OpenAICompletionsCompat \| OpenAICodexResponsesCompat \| …`) | deferred to Sprint 6g₂ | ADR-0068 |
| `get_commands` RPC command + prompt-templates + skills surface | deferred to Sprint 6g₂ | ADR-0068 |
| 16 remaining RPC commands (queue / session tree / extension UI bridge / auto modes / retry / etc.) | deferred to Sprint 6g₂ | ADR-0068 |
| `image-models.ts` + `image-models.generated.ts` parallel image-model registry | deferred to Sprint 6g₃ | ADR-0068 |
| `chalk`-colored CLI output | deferred to Sprint 6h / Phase 5 TUI | ADR-0067 |
| Workspace-scoped model selection (`isScoped: true` path) | deferred to Sprint 6g₂ | ADR-0068 |
| `applyProviderConfig` for `register_provider.config.models` + `models.json` schema | deferred to Sprint 6g₂ | ADR-0068 |
| `enableGitHubCopilotModel` POST automation | deferred to Sprint 6g₂ | ADR-0068 |
| `Model.knowledgeCutoff` / `Model.releaseDate` (Pi-untyped runtime additions) | deferred — Pi types catch up | ADR-0068 |

Sprint 6g₁ closes the model resolver + full catalog + `KnownProvider`
+ `Model.compat` passthrough layer. The closure pin
(`tests/pi_parity/test_phase_4_7_strict_superset.py`) asserts
`KnownProvider` Literal byte-equivalent Pi semantic order (P-208),
`DEFAULT_THINKING_LEVEL == "medium"` (P-205), `Model.compat` field
present + default `None` (P-200), `RestoreModelResult` frozen
dataclass with `{model, fallback_message}` shape (P-206), and the
`_glob_match_pi_minimatch` `/`-boundary semantics (P-207). The
Sprint 6f₁ seed `>= 10 models` invariant still passes against the
full 942-model catalog.

### Sprint 6h₁ amendment (prompt-templates + skills + `get_commands` RPC, 2026-05-20)

Sprint 6h₁ ports Pi `harness/prompt-templates.ts` (~380 LOC) +
`harness/skills.ts` (~540 LOC) + wires the `get_commands` RPC
handler (`rpc-mode.ts:622-653`). W4 code review + W5 Pi parity audit
produced **3 BLOCKING + 1 MAJOR + 3 MINOR + 2 W4 fixes**; Sprint 6h₁
W6 applied the must-fix triage in 5 atomic commits.

| Component | Status | Owner ADR |
|---|---|---|
| `aelix_agent_core.harness.prompt_templates` (Pi port: 5 functions + 4 types — `loadPromptTemplates` + `parseCommandArgs` + `substituteArgs` + `formatPromptTemplateInvocation` + `PromptTemplate` + 2 diagnostic types + `LoadPromptTemplatesResult`) | shipped | ADR-0069 |
| `aelix_agent_core.harness.skills` (Pi port: 2 public functions + 4 types — `loadSkills` + `formatSkillInvocation` + `Skill` + 2 diagnostic types + `LoadSkillsResult` + `.gitignore`/`.ignore`/`.fdignore` honouring via pathspec) | shipped | ADR-0069 |
| `aelix_agent_core.harness._frontmatter` (shared YAML parser between prompt_templates + skills — W4 m4) | shipped | ADR-0069 |
| `aelix_agent_core.harness._extension_runner.ExtensionRunner` + `ResolvedCommand` (Pi `runner.ts:512-551` `resolveRegisteredCommands` with `{name}:{N}` disambiguation per W6 P-224 BLOCKING) | shipped | ADR-0069 |
| `aelix_agent_core.harness.core.AgentHarness` wires `extension_runner` / `prompt_templates` / `skills` properties + 2 setters | shipped | ADR-0069 |
| `aelix_coding_agent.extensions.api.ExtensionSourceInfo` adds Pi `path` / `scope` / `origin` fields with sensible defaults (W6 P-225 BLOCKING) | shipped | ADR-0069 |
| `aelix_coding_agent.rpc.rpc_mode._handle_get_commands` (Pi `rpc-mode.ts:622-653` aggregates 3 sources; Pi `{path, source, scope, origin, baseDir?}` wire `sourceInfo` per W6 P-225 BLOCKING; reads `ResolvedCommand.invocation_name` per W6 P-224 BLOCKING) | shipped | ADR-0069 |
| `PromptTemplate.description` / `content` default `""` (W6 P-226 MAJOR) | shipped | ADR-0069 |
| YAML parse failures surface `yaml.YAMLError` text in `parse_failed` diagnostic (W6 P-233 MINOR) | shipped | ADR-0069 |
| Case-insensitive `.md` extension strip (W6 P-234 MINOR) | shipped | ADR-0069 |
| Pi `pi_get_commands_734e08e.json` fixture name-regex text correction (W6 P-227 MINOR) | shipped | ADR-0069 / 0070 |
| Tautological `disable-model-invocation` test → real sentinel (integer `1` is truthy but not `is True`) (W6 W4 m2) | shipped | ADR-0069 |
| `PyYAML>=6.0` + `pathspec>=0.12` added to `aelix-agent-core` pyproject (P-222) | shipped | ADR-0069 |
| `tests/pi_parity/test_phase_4_8_strict_superset.py` closure pin (22+ tests — 3-source aggregation + Pi name regex + Pi disambiguation + Pi-shape sourceInfo wire + PromptTemplate empty default + shared _frontmatter helper + YAML error surface + fixture P-227 correction) | shipped | ADR-0070 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin updated: SUPPORTED 12 → 13, DEFERRED 17 → 16) | shipped | ADR-0070 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` strengthening (Sprint 6f closure pin updated: SUPPORTED 12 → 13, DEFERRED 17 → 16) | shipped | ADR-0070 |
| 16 remaining RPC commands (steer / follow_up / cycle_thinking_level / queue / auto / abort_bash / session inspection / session tree / extension UI bridge) | deferred to Sprint 6h₂ | ADR-0070 |
| Workspace-scoped model selection (`isScoped: true` path) | deferred to Sprint 6h₂ | ADR-0070 |
| `applyProviderConfig` for `register_provider.config.models` | deferred to Sprint 6h₂ | ADR-0070 |
| `enableGitHubCopilotModel` POST automation | deferred to Sprint 6h₂ | ADR-0070 |
| `loadSourcedPromptTemplates` / `loadSourcedSkills` source-tagged variants | deferred to Sprint 6h₂ | ADR-0070 |
| `image-models.ts` + `image-models.generated.ts` parallel image-model registry | deferred to Sprint 6h₃ | ADR-0070 |
| Typed `Model.compat` discriminated union | deferred to Sprint 6h₃ | ADR-0070 |
| pathspec `gitwildmatch` → `gitignore` flavour cutover when pathspec 0.13 lands | tracked | ADR-0070 |

Sprint 6h₁ moves `get_commands` from deferred → supported.
`DEFERRED_COMMANDS` shrinks 17 → 16; `SUPPORTED_COMMANDS` rises
12 → 13. The closure pin
(`tests/pi_parity/test_phase_4_8_strict_superset.py`) asserts
`SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29.

### Sprint 6h₂ amendment (9 RPC commands + harness setters, 2026-05-20)

Sprint 6h₂ ports the next 9 Pi `RpcCommand` discriminators from
`rpc-mode.ts:483-547` (W5-audited line range at SHA `734e08e`;
prior W1 draft said 528-635 — corrected per P-258 BLOCKING). The 9
commands: `steer` / `follow_up` (queue paths with `images`),
`cycle_thinking_level`, `set_steering_mode` / `set_follow_up_mode`
(queue mode setters), `set_auto_compaction` / `set_auto_retry`
(auto-mode flags), `abort_retry` / `abort_bash` (best-effort
cancellation flags). W4 code-review + W5 Pi parity audit produced
**6 BLOCKING + 4 MAJOR + 3 MINOR** must-fix items; Sprint 6h₂ W6
applied the tractable fixes in 5 atomic commits.

| Component | Status | Owner ADR |
|---|---|---|
| 9 RPC handlers (steer / follow_up / cycle_thinking_level / set_steering_mode / set_follow_up_mode / set_auto_compaction / set_auto_retry / abort_retry / abort_bash) | shipped | ADR-0071 |
| Harness setters (5 new + 2 new public properties + 4 new AgentState fields + 1 new `_MessageQueue.set_mode` helper) | shipped | ADR-0071 |
| `cycle_thinking_level` `supportsThinking()` guard (W6 P-254 BLOCKING) | shipped | ADR-0071 |
| `images` keyword-only marker on `steer` / `follow_up` (W6 P-263 MAJOR) | shipped | ADR-0071 |
| Strict `_decode_images` (camelCase only + required-field validation, W6 P-262 BLOCKING) | shipped | ADR-0071 |
| `_MessageQueue.set_mode` defensive runtime validation (W6 P-265 BLOCKING) | shipped | ADR-0071 |
| `auto_retry_enabled` public property + `RpcSessionState.auto_retry_enabled` wire field (W6 P-264 BLOCKING) | shipped | ADR-0071 |
| Line citation corrections — `rpc-mode.ts:483-547` + `agent-session.ts` method sites (W6 P-258 BLOCKING) | shipped | ADR-0071 |
| W4 LOW-1/LOW-3 + 3 NIT closures (typing.cast + docstring counts + deferred handler error string + closure-pin test renames) | shipped | ADR-0071 |
| `tests/pi_parity/test_phase_4_9_strict_superset.py` closure pin (28 tests including 11 W6 regressions) | shipped | ADR-0072 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (12 → 13 RpcSessionState field count + fixture extension) | shipped | ADR-0072 |
| `tests/pi_parity/test_phase_4_6_strict_superset.py` W4 NIT renames | shipped | ADR-0072 |
| `tests/pi_parity/fixtures/pi_rpc_9_commands_734e08e.json` W6 P-258 line-number corrections | shipped | ADR-0072 |
| Pi `SettingsManager` disk persistence (P-255 / P-256) | deferred to Sprint 6h₃ | ADR-0072 |
| Pi `agent-harness.ts` retry loop with `AbortController` (P-257) | deferred to Sprint 6h₃ | ADR-0072 |
| `queue_update` event payload Pi-shape `string[]` vs Aelix `list[UserMessage]` (P-259) | deferred to Sprint 6h₃ | ADR-0072 |
| `_throwIfExtensionCommand` + `_expandSkillCommand` + `expandPromptTemplate` expanders (P-260) | deferred to Sprint 6h₃ | ADR-0072 |
| `cycle_thinking_level` sync vs async asymmetry (P-266 — documented divergence) | tracked | ADR-0072 |
| 5 session-tree commands (switch_session / fork / clone / get_fork_messages / get_last_assistant_text) | deferred to Sprint 6h₃ | ADR-0072 |
| 2 session-inspection commands (get_session_stats / export_html) | deferred to Sprint 6h₃ | ADR-0072 |

Sprint 6h₂ moves 9 commands from deferred → supported. `DEFERRED_COMMANDS`
shrinks 16 → 7; `SUPPORTED_COMMANDS` rises 13 → 22. The closure pin
(`tests/pi_parity/test_phase_4_9_strict_superset.py`) asserts
`SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29 and
pins the W5-audited Pi line numbers at SHA `734e08e`.

### Sprint 6h₃ amendment (session inspection — get_session_stats + export_html, 2026-05-20)

Sprint 6h₃ ports the next 2 Pi `RpcCommand` discriminators from
`rpc-mode.ts:553-561` (W5-audited line range at SHA `734e08e`;
prior W1 draft said 475-483 — corrected per P-277/P-278/P-286). The
2 commands: `get_session_stats` (Pi `rpc-mode.ts:553-556` →
`agent-session.ts:2901-2945`) and `export_html` (Pi
`rpc-mode.ts:558-561` → `coding-agent/src/core/export-html/`). W4
code-review + W5 Pi parity audit produced **2 BLOCKING + 2 MAJOR +
1 W4 HIGH + 2 W4 MEDIUM** must-fix items; Sprint 6h₃ W6 applied
every BLOCKING + MAJOR + W4 fix.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₃ | Phase 4.10 | get_session_stats + export_html wire | SUPPORTED 22→24, DEFERRED 7→5 | ADR-0073, ADR-0074 |
| `SessionStats` + `SessionStatsTokens` dataclasses + aggregator (`harness/_session_stats.py`) | shipped | ADR-0073 |
| Minimal HTML emitter (`_export_html.py`, Pi-shape default path + Pi error parity) | shipped | ADR-0073 |
| `harness.get_session_stats()` + `harness.export_to_html()` methods | shipped | ADR-0073 |
| 2 RPC handlers (`_handle_get_session_stats` / `_handle_export_html`) + Pi-shape `_session_stats_to_dict` (W6 P-275 BLOCKING — Pi-shape `contextUsage {tokens, contextWindow, percent}`) | shipped | ADR-0073 |
| Aggregator `totalMessages = len(messages)` (W6 P-276 BLOCKING — matches Pi `state.messages.length`) | shipped | ADR-0073 |
| Pi error parity on `export_to_html` (W6 P-279 MAJOR — `export-html.ts:242-248`) | shipped | ADR-0073 |
| Pi-shape default `outputPath = aelix-session-<basename>.html` cwd-relative (W6 P-281 MAJOR — `export-html.ts:273-277`) | shipped | ADR-0073 |
| Aggregator dict-shape `usage` fallback via `_read` helper (W6 P-283) | shipped | ADR-0073 |
| Drop dead `hasattr(session, "messages")` branch (W6 W4 HIGH P-292) | shipped | ADR-0073 |
| Drop `getattr(cmd, "output_path", None)` in `_handle_export_html` (W4 M1) | shipped | ADR-0073 |
| Drop `path.parent` truthiness tautology in `_export_html` (W4 M2) | shipped | ADR-0073 |
| Line citation corrections — `rpc-mode.ts:553-561` + `agent-session.ts:2901-2945` (W6 P-277/P-278/P-286) | shipped | ADR-0073 |
| `tests/pi_parity/test_phase_4_10_strict_superset.py` closure pin | shipped | ADR-0074 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin: SUPPORTED 22 → 24, DEFERRED 7 → 5) | shipped | ADR-0074 |
| `tests/pi_parity/test_phase_4_6/4_8/4_9_strict_superset.py` count updates | shipped | ADR-0074 |
| Pi `AgentSessionRuntime` port (runtimeHost.switchSession / fork) | deferred to Sprint 6h₄ | ADR-0074 |
| `SessionManager.getLeafId` for `clone` command | deferred to Sprint 6h₄ | ADR-0074 |
| `rebindSession` seam (P-126 Sprint 6f carry-forward) | deferred to Sprint 6h₄ | ADR-0074 |
| 5 session-tree commands (switch_session / fork / clone / get_fork_messages / get_last_assistant_text) | deferred to Sprint 6h₄ | ADR-0074 |
| `_get_context_usage_safe` real implementation (P-282 — model registry + per-turn tracking) | deferred to Sprint 6h₄ | ADR-0074 |
| Pi HTML visual fidelity + session-tree entry source (P-280) | deferred to Sprint 6h₅ | ADR-0074 |
| Live `session_id` read via session manager (P-291) | deferred to Sprint 6h₄ | ADR-0074 |
| Pi-source-grep verification tooling (P-286) | deferred to Sprint 6h₄ | ADR-0074 |

Sprint 6h₃ moves 2 commands from deferred → supported.
`DEFERRED_COMMANDS` shrinks 7 → 5; `SUPPORTED_COMMANDS` rises 22 →
24. The closure pin
(`tests/pi_parity/test_phase_4_10_strict_superset.py`) asserts
`SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29 and
pins the W5-audited Pi line numbers at SHA `734e08e`.

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
