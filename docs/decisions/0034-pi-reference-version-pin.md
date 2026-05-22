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

### Sprint 6h₄a amendment (session navigation read-only — get_fork_messages + get_last_assistant_text, 2026-05-20)

Sprint 6h₄a wired 2 read-only session-navigation RPC commands
(`get_fork_messages` + `get_last_assistant_text`). DEFERRED 5→3,
SUPPORTED 24→26. Remaining 3 session-tree commands (`switch_session`
/ `fork` / `clone`) defer to Sprint 6h₄b per ADR-0076 — they require
porting Pi `AgentSessionRuntime` + `SessionManager.getLeafId()` +
`rebindSession()` seam (P-126 Sprint 6f multi-sprint carry-forward).

Sprint 6h₄a ports the next 2 Pi `RpcCommand` discriminators from
`rpc-mode.ts:591-599` (W0-verified line range at SHA `734e08e`;
ADR-0074 estimated `:563-566` / `:568-571` — drift captured per
P-293 and corrected via ADR-0074 line-citation amendment in this
sprint). The 2 commands: `get_fork_messages` (Pi
`rpc-mode.ts:591-594` → `agent-session.ts:2867-2900`
`getUserMessagesForForking`) and `get_last_assistant_text` (Pi
`rpc-mode.ts:596-599` → `agent-session.ts:3063-3070`
`getLastAssistantText`). W4 code-review + W5 Pi parity audit
returned a **CLEAN verdict** — zero BLOCKING / MAJOR / MINOR;
only INFO observations (P-299/P-300/P-301) mapping to documented
Aelix-additive divergences (P-294/P-295/P-296) already captured
in the binding spec §0.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₄a | Phase 4.11 | get_fork_messages + get_last_assistant_text wire | SUPPORTED 24→26, DEFERRED 5→3 | ADR-0075, ADR-0076 |
| `aelix_agent_core.harness._fork_point.ForkPointInfo` (NEW — `@dataclass(frozen=True)` for Pi inline anonymous `{entryId, text}` shape, P-295) | shipped | ADR-0075 |
| `AgentHarness.get_user_messages_for_forking()` (Pi parity `agent-session.ts:2867-2900`; async per P-294 — Aelix `Session.get_entries()` is async) | shipped | ADR-0075 |
| `AgentHarness.get_last_assistant_text()` (Pi parity `agent-session.ts:3063-3070`; reverse-walk + aborted-empty filter `stop_reason == "aborted" AND len(content) == 0`, P-297) | shipped | ADR-0075 |
| `AgentHarness._extract_user_message_text` (Pi parity `agent-session.ts:2887-2898`; list-only walk with defensive string branch per P-296 — unreachable under Aelix type system) | shipped | ADR-0075 |
| 2 RPC handlers (`_handle_get_fork_messages` / `_handle_get_last_assistant_text`) + camelCase wire serializer (Pi-shape `{entryId, text}`) + dispatch entries | shipped | ADR-0075 |
| `_handle_get_last_assistant_text` Pi key-omission parity — `data = {"text": text} if text is not None else {}` (P-298 SYNTHESIS — matches Pi `JSON.stringify({text: undefined}) → {}` and existing `_session_stats_to_dict` undefined-skip pattern) | shipped | ADR-0075 |
| `tests/pi_parity/test_phase_4_11_strict_superset.py` closure pin (26 supported / 3 deferred + ForkPointInfo shape + Pi-camelCase wire + async harness method + aborted-empty filter + Pi key-omission parity + W0-verified line numbers `:591-594` / `:596-599`) | shipped | ADR-0076 |
| `tests/pi_parity/test_phase_4_4_strict_superset.py` strengthening (Sprint 6d closure pin: SUPPORTED 24 → 26, DEFERRED 5 → 3) | shipped | ADR-0076 |
| `tests/pi_parity/test_phase_4_6/4_8/4_9/4_10_strict_superset.py` count cascade updates (SUPPORTED 24 → 26, DEFERRED 5 → 3) | shipped | ADR-0076 |
| `tests/pi_parity/fixtures/pi_session_navigation_734e08e.json` W0 fixture (verified Pi handler bodies at `:591-594` / `:596-599`) | shipped | ADR-0075 |
| ADR-0074 line-citation correction note appended (P-293 — `:563-566` / `:568-571` → `:591-594` / `:596-599`) | shipped | ADR-0074 |
| Pi `AgentSessionRuntime` port (runtimeHost.switchSession / fork / fork({position: "at"})) | deferred to Sprint 6h₄b | ADR-0076 |
| `SessionManager.getLeafId()` for `clone` command | deferred to Sprint 6h₄b | ADR-0076 |
| `rebindSession()` seam (P-126 Sprint 6f multi-sprint carry-forward) | deferred to Sprint 6h₄b | ADR-0076 |
| 3 session-tree commands (switch_session / fork / clone) | deferred to Sprint 6h₄b | ADR-0076 |
| `_get_context_usage_safe` real implementation (P-282 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | ADR-0076 |
| Live `session_id` read via session manager (P-291 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | ADR-0076 |
| Pi-source-grep verification tooling (P-286 carry-forward from ADR-0074) | deferred to Sprint 6h₄b+ | ADR-0076 |
| Pi HTML visual fidelity + session-tree entry source (P-280 carry-forward from ADR-0074) | deferred to Sprint 6h₅ | ADR-0076 |

Sprint 6h₄a moves 2 commands from deferred → supported.
`DEFERRED_COMMANDS` shrinks 5 → 3; `SUPPORTED_COMMANDS` rises 24 →
26. The closure pin
(`tests/pi_parity/test_phase_4_11_strict_superset.py`) asserts
`SUPPORTED ∪ DEFERRED == RPC_COMMAND_TYPES` preserved at 29 and
pins the W0-verified Pi line numbers at SHA `734e08e`. This is
the first sprint since 6a where W4+W5 returned a 0-finding
CLEAN verdict.

### Sprint 6h₄b amendment (`AgentSessionRuntime` foundation + `rebindSession` seam, 2026-05-21)

Sprint 6h₄b ported Pi `AgentSessionRuntime`
(`packages/coding-agent/src/core/agent-session-runtime.ts:67-374`) +
the `rebindSession` closure seam (`rpc-mode.ts:310-349`) as the
**FOUNDATION-ONLY** layer per ADR-0077. **NO new RPC commands wired
in this sprint.** SUPPORTED stays at 26; DEFERRED stays at 3 (owner
rebrand to ADR-0078 per spec §D.5); total stays at 29. The 3
session-tree commands (`switch_session` / `fork` / `clone`) wire in
Sprint 6h₄c per ADR-0078 on top of this foundation.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₄b | Phase 4.12 | `AgentSessionRuntime` Pi port + `rebindSession` seam — FOUNDATION ONLY | SUPPORTED 26 (unchanged), DEFERRED 3 (unchanged) | ADR-0077, ADR-0078 |
| `aelix_agent_core.runtime` package (NEW) | shipped | ADR-0077 |
| `aelix_agent_core.runtime._types` (NEW — `HarnessFactory` + `RuntimeReplaceResult` frozen + `AgentSessionRuntimeDiagnostic` frozen) | shipped | ADR-0077 |
| `aelix_agent_core.runtime.agent_session_runtime.AgentSessionRuntime` (NEW — 7 public methods + 5 read-only getters + private replace seam `_apply` / `_teardown_current` / `_finish_session_replacement`) | shipped | ADR-0077 |
| Harness-rebuild pattern (P-302 BINDING — `HarnessFactory: Callable[[Session], Awaitable[AgentHarness]]` preserves `_state.session_id` + action bindings + merged tools + cached session-name invariants) | shipped | ADR-0077 |
| `rebind_session` closure in `rpc_mode.py` (P-303 — Pi `rpc-mode.ts:310-349` subset: re-subscribe only; `bindExtensions` / `commandContextActions` waveform deferred to 6h₄c) | shipped | ADR-0077 |
| `run_rpc_mode` signature shim accepts optional `runtime_host: AgentSessionRuntime \| None = None` + `harness_factory: HarnessFactory \| None = None` (P-309 — backward-compat for 26 already-wired handlers) | shipped | ADR-0077 |
| `_make_passthrough_runtime(harness, harness_factory)` helper with `_noop_factory` that RAISES `RuntimeError` (W4 LOW-3 — fail loudly on accidental replace) | shipped | ADR-0077 |
| 4 stubbed public replace APIs (`switch_session` / `new_session` / `fork` / `import_from_jsonl`) raise `NotImplementedError("Sprint 6h₄c — ADR-0078")` (P-310) | shipped | ADR-0077 |
| `_emit_before_switch` / `_emit_before_fork` async no-op stubs return `False` (P-308 — Aelix has no `session_before_switch` / `session_before_fork` hook events today) | shipped | ADR-0077 |
| DEFERRED owner rebrand 0076 → 0078 in `DEFERRED_COMMANDS` (W4 MEDIUM-1 + W5 P-312 + W5 P-319 per spec §D.5) | shipped | ADR-0078 |
| Cascade pin allowlist updates (`test_phase_4_4`/`4_9`/`4_10`/`4_11_strict_superset.py` extended with ADR-0078 prefix) | shipped | ADR-0078 |
| `tests/pi_parity/test_phase_4_12_strict_superset.py` closure pin (26 / 3 / 29 unchanged + DEFERRED owners cite ADR-0078 only + 15 Pi member line-range pins + runtime class shape pins + frozen dataclass field locks + `run_rpc_mode` signature pin + Pi fixture immutability) | shipped | ADR-0078 |
| `tests/pi_parity/fixtures/pi_agent_session_runtime_734e08e.json` (W0 fixture — 15 Pi member line-ranges + architecture decision = `"harness-rebuild"`) | shipped | ADR-0077 |
| `tests/runtime/test_agent_session_runtime.py` (29+ unit tests — constructor + 5 getters + 2 seam setters + 4 stub returns + `_apply_for_test` replace seam exercise + P-306 `_state.session_id` invariant + dispose order + frozen dataclass locks) | shipped | ADR-0077 |
| `tests/rpc/test_rpc_mode_rebind.py` (rebind closure integration — subscription rebalance + listener-count balance per replace + smoke test for `runtime_host` kwarg) | shipped | ADR-0077 |
| `tests/rpc/test_rpc_mode_runtime_shim.py` (NEW — 7 P-309 / P-311 back-compat regression tests: bare-harness call works + passthrough identity + raising no-op factory + dispatch reads `capture.harness` + `runtime_host` harness wins + wired handlers still callable + 3 deferred return ADR-0078 in error string) | shipped | ADR-0078 |
| `tests/rpc/test_rpc_mode_deferred.py` + `test_rpc_mode_stdin_stdout.py` extended ADR allowlists for ADR-0078 rebrand | shipped | ADR-0078 |
| ADR-0076 amendment — Sprint 6h₄b foundation update note (DEFERRED ownership rebrands from ADR-0076 → ADR-0078) | shipped | ADR-0076 |
| 3 session-tree commands (switch_session / fork / clone) — wire on 6h₄b runtime foundation | deferred to Sprint 6h₄c | ADR-0078 |
| Real `_emit_before_switch` / `_emit_before_fork` extension cancel hooks (P-308 fill-in) | deferred to Sprint 6h₄c | ADR-0078 |
| P-307 `session_shutdown` extension event emit from `AgentHarness.dispose()` | deferred to Sprint 6h₄c | ADR-0078 |
| P-313 widen `HarnessFactory` for full Pi field refresh (`_services` / `_diagnostics` / `_modelFallbackMessage`) | deferred to Sprint 6h₄c | ADR-0078 |
| P-314 `with_session: Callable[[ReplacedSessionContext], Awaitable[None]] \| None = None` 2-stage callback | deferred to Sprint 6h₄c | ADR-0078 |
| P-315 `set_rebind_session` / `set_before_session_invalidate` optional-cb signature widening | deferred to Sprint 6h₄c | ADR-0078 |

Sprint 6h₄b is a FOUNDATION sprint — `SUPPORTED ∪ DEFERRED ==
RPC_COMMAND_TYPES` preserved at 29 with the **same 26 / 3 split**.
The new closure pin (`tests/pi_parity/test_phase_4_12_strict_superset.py`)
asserts the foundation invariants (runtime class shape + Pi line
citations + harness-rebuild architecture decision + DEFERRED
ADR-0078 ownership) without moving any of the count needles.

### Sprint 6h₄c amendment (session-tree handlers wired — PHASE 4 RPC CLOSURE, 2026-05-21)

Sprint 6h₄c wired 3 session-tree RPC commands (`switch_session` /
`fork` / `clone`) on top of the 6h₄b `AgentSessionRuntime`
foundation, filled 3 of the 4 stubbed replace API bodies
(`switch_session` / `new_session` / `fork` — `import_from_jsonl`
stays stubbed per ADR-0080 carry-forward), and replaced the Sprint
6d `_handle_new_session` stub (which rejected `parent_session`)
with a runtime-host route that persists lineage through
`repo.create(parent_session_path=...)`. **PHASE 4 RPC ROSTER
CLOSED** at SUPPORTED **29** / DEFERRED **0** / total **29** =
full Pi parity for the `RpcCommand` discriminator union.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₄c | Phase 4.13 | session-tree handlers wired + runtime body fills | SUPPORTED 26 → **29**, DEFERRED 3 → **0** | ADR-0079, ADR-0080 |
| `AgentSessionRuntime.__init__` constructor extended with required keyword-only `repo: JsonlSessionRepo` + `fs: FileSystem` (P-324 BINDING) | shipped | 0079 |
| `AgentSessionRuntime.switch_session` real body — `repo.open(load_jsonl_session_metadata(fs, path))` → `_finish_session_replacement` (P-325) | shipped | 0079 |
| `AgentSessionRuntime.new_session` real body — `repo.create(JsonlSessionCreateOptions(cwd, parent_session_path))` → `_finish_session_replacement` (P-325 / P-330 — replaces Sprint 6d stub at `rpc_mode.py:309-347` that rejected `parent_session`) | shipped | 0079 |
| `AgentSessionRuntime.fork` real body — `repo.fork(source, ForkOptions(cwd, entry_id=target_leaf_id, position="at", parent_session_path))` — Aelix persisted-only (drops Pi in-memory branch `:303-319`) (P-325) | shipped | 0079 |
| `AgentSessionRuntime.import_from_jsonl` STAYS STUBBED — no Pi `RpcCommand` discriminator maps to it at SHA `734e08e` (Pi TUI `/import` doesn't go through RPC; carry-forward per ADR-0080) | shipped | 0079 |
| `_extract_user_message_text` module-private helper (Pi `agent-session-runtime.ts:49-58`) | shipped | 0079 |
| `_apply_for_test` test seam REMOVED — 6h₄b unit tests migrated to drive `switch_session` via real public API (P-331) | shipped | 0079 |
| NEW `_SUPPORTED_HANDLERS_RUNTIME_HOST` arity class — 4 handlers `(new_session, switch_session, fork, clone)` taking `(runtime_host, cmd)` instead of `(harness, cmd)` (P-326) | shipped | 0079 |
| `_bind_runtime_host(handler, runtime_host)` adapter closure (P-326) | shipped | 0079 |
| `_make_missing_runtime_handler(cmd_type)` Pi-shape error stub for `build_dispatch_table(runtime_host=None)` test path (P-326-DRIFT ratified) | shipped | 0079 |
| `build_dispatch_table(model_registry, *, runtime_host)` — `runtime_host` Optional with missing-runtime stub fallback (P-326-DRIFT ratified) | shipped | 0079 |
| `_make_passthrough_runtime(harness, harness_factory, *, repo=None, fs=None)` — Pi-defaults via `LocalFileSystem` + `JsonlSessionRepo(fs=...)` when caller omits (P-324-DRIFT ratified) | shipped | 0079 |
| `run_rpc_mode(..., repo=None, fs=None)` signature extension — when `runtime_host` explicit, caller MUST NOT supply `repo` / `fs` (the runtime owns them); when `runtime_host=None`, passthrough threads defaults (P-324) | shipped | 0079 |
| `_handle_switch_session` real handler (Pi `rpc-mode.ts:563-569`) — wire shape `{cancelled}` (Pi line 568) | shipped | 0079 |
| `_handle_fork` real handler (Pi `rpc-mode.ts:571-577`) — wire shape `{cancelled, text?}` with `text` key OMITTED when `selected_text is None` (P-327 / P-298 pattern — `selectedText → text` rename) | shipped | 0079 |
| `_handle_clone` real handler (Pi `rpc-mode.ts:579-589`) — leaf_id captured BEFORE OLD harness dispose (P-328 ordering); wire shape `{cancelled}` only (Pi line 588 drops `selectedText`) | shipped | 0079 |
| `_handle_new_session` REPLACED — Sprint 6d stub deleted; routes through `runtime_host.new_session(parent_session=cmd.parent_session)`; ADR-0058 carry-forward CLOSES (P-330) | shipped | 0079 |
| P-329 deliberate convergence — Aelix handlers MUST NOT call rebind manually (Pi belt-and-braces `await rebindSession()` at `rpc-mode.ts:566`/`:574`/`:586` NOT mirrored — runtime's `_finish_session_replacement` is single source of truth) | shipped | 0079 |
| W4 MINOR-1 — Double-catch collapse in `_handle_fork` + `_handle_clone` (keep `ValueError` arm Pi-documented at `:247`; drop blanket `except Exception`) | shipped | 0079 |
| W4 MINOR-2 — Blanket `except Exception` dropped from `_handle_switch_session` (outer `_handle_command` wraps) | shipped | 0079 |
| W4 MINOR-3 — `test_handle_fork_wire_shape_omits_text_when_none` rewritten to drive handler via mocked runtime returning `RuntimeReplaceResult(selected_text=None)` | shipped | 0079 |
| W4 MINOR-4 — `_handle_new_session` blanket `except RuntimeError` dropped (avoid masking noop-factory leaks) | shipped | 0079 |
| `tests/pi_parity/test_phase_4_13_strict_superset.py` closure pin (29 / 0 / 29 + handler invariants + `_apply_for_test` removed + wire shapes + rebind exactly-once + leaf_id pre-capture ordering + line citations) | shipped | 0080 |
| `tests/pi_parity/fixtures/pi_runtime_wire_734e08e.json` W0 fixture | shipped | 0079 |
| `tests/runtime/test_agent_session_runtime_replace_apis.py` (NEW — real switch_session / new_session / fork over tmp-path `JsonlSessionRepo`) | shipped | 0079 |
| `tests/runtime/test_switch_session.py` / `test_fork.py` / `test_new_session_real.py` (NEW — runtime-layer unit tests for each replace API) | shipped | 0079 |
| `tests/rpc/test_rpc_mode_switch_fork_clone.py` (NEW — 3 handler integration tests + arity / dispatch wiring + rebind invocation count + leaf_id pre-capture ordering + W4 MINOR-3 rewrite) | shipped | 0079 |
| `tests/rpc/test_rpc_mode_new_session_parent.py` (NEW — Sprint 6d stub removal regression; asserts `parent_session_path` actually persists) | shipped | 0079 |
| Cascade pin allowlist updates (4.4 / 4.6 / 4.8 / 4.9 / 4.10 / 4.11 / 4.12 count cascades to 29 supported / 0 deferred) | shipped | 0080 |
| 6h₄b test migrations — `tests/runtime/test_agent_session_runtime.py` + `tests/rpc/test_rpc_mode_*.py` drop `_apply_for_test` usage and migrate to real `switch_session` (P-331) | shipped | 0079 |
| ADR-0076 / ADR-0078 amendments — Sprint 6h₄c PHASE 4 RPC CLOSURE note + P-323 line-citation correction (`:528-557` / `:566`/`:574`/`:586` → verified `:563-569` / `:571-577` / `:579-589`) | shipped | 0076 / 0078 |
| P-307 `session_shutdown` extension event emit | deferred to Sprint 6h₅+ | 0080 |
| P-308 real `session_before_switch` / `session_before_fork` extension cancel hooks | deferred to Sprint 6h₅+ | 0080 |
| P-313 `HarnessFactory` 4-field refresh | **DROPPED** (harness-rebuild encapsulates services + diagnostics + model_fallback_message via factory closure; redundant for Aelix) | 0080 |
| P-314 `with_session` 2-stage callback | deferred to Sprint 6h₅+ | 0080 |
| P-315 optional-cb signatures | deferred to Sprint 6h₅+ | 0080 |
| `assertSessionCwdExists` Pi parity (cwd-on-disk validation) | deferred to Sprint 6h₅+ | 0080 |
| `previousSessionFile` / `sessionStartEvent` tracking | deferred to Sprint 6h₅+ | 0080 |
| Pi `forkFrom` cross-cwd import | deferred to Sprint 6h₅+ | 0080 |
| Pi `setup` callback in `new_session` | deferred to Sprint 6h₅+ | 0080 |
| `import_from_jsonl` real runtime body (no RPC wire today) | deferred to Sprint 6h₅+ | 0080 |

Sprint 6h₄c moves 3 commands from deferred → supported AND
replaces the Sprint 6d `_handle_new_session` stub. `DEFERRED_COMMANDS`
shrinks 3 → **0**; `SUPPORTED_COMMANDS` rises 26 → **29**. The
closure pin (`tests/pi_parity/test_phase_4_13_strict_superset.py`)
asserts `SUPPORTED_COMMANDS == RPC_COMMAND_TYPES` (full set
equality, NOT just superset union) and `DEFERRED_COMMANDS == {}`
literal empty. **This is the LAST Phase 4 RPC sprint** —
remaining Pi parity gaps are runtime / extension polish per the
Sprint 6h₅+ roster in ADR-0080.

### Sprint 6h₅a amendment (extension event Pi parity — RPC roster UNCHANGED, 2026-05-22)

Sprint 6h₅a (Phase 4.14) wires the 4 Pi extension session lifecycle
events (`session_start` / `session_before_switch` /
`session_before_fork` / `session_shutdown`) end-to-end on top of the
6h₄c `AgentSessionRuntime` foundation, ports
`packages/agent/src/harness/session/session-cwd.ts:1-59` to
`session/session_cwd.py` (async-adapted), and applies a W5 BLOCKING
dispose-ordering correction (P-355 — the W2 §J "intentional
asymmetry" claim was a Pi misread; Pi `:366-373` has no asymmetry —
dispose now matches `_teardown_current` order **EMIT → INVALIDATE →
DISPOSE**). **RPC roster STAYS CLOSED** at SUPPORTED **29** /
DEFERRED **0** / total **29** — extension polish does not change the
dispatch table.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₅a | Phase 4.14 | extension session lifecycle events wired (4 events + session_cwd helper) | SUPPORTED 29 → **29**, DEFERRED 0 → **0** | ADR-0081, ADR-0082 |
| `HookEventName` Literal widened 31 → **35** names (`session_start` / `session_before_switch` / `session_before_fork` / `session_shutdown`) | shipped | 0081 |
| `AgentHarnessEventName` Literal widened 18 → **22** names (same 4 extension session lifecycle events folded into harness own bucket) | shipped | 0081 |
| 4 new `@dataclass(frozen=True)` event payloads (`SessionStartHookEvent` / `SessionBeforeSwitchHookEvent` / `SessionBeforeForkHookEvent` / `SessionShutdownHookEvent`) | shipped | 0081 |
| 2 new result types (`SessionBeforeSwitchResult` / `SessionBeforeForkResult` — Pi `extensions/types.ts:1015-1022` with `cancel?` + `skipConversationRestore?` per P-345) | shipped | 0081 |
| 4 new `@overload` declarations on `HookBus.on` / `ExtensionAPI.on` (31 → **35** overloads — matches `HookEventName`) | shipped | 0081 |
| `_REDUCERS` registry shares `_reducer_session_before` across 4 cancellable events (`session_before_compact` / `session_before_tree` / `session_before_switch` / `session_before_fork`) — reducer return type widened to 4-arm union (P-335) | shipped | 0081 |
| `_emit_session_shutdown_event` module-private helper (Pi `runner.ts:177-189` — gated on `has_handlers("session_shutdown")`) | shipped | 0081 |
| `AgentSessionRuntime._emit_before_switch` real body — Pi `agent-session-runtime.ts:115-130` (replaces 6h₄b no-op stub returning `False`) — W4 MINOR-3: `reason` + `target_session_file` required (no defaults) | shipped | 0081 |
| `AgentSessionRuntime._emit_before_fork` real body — Pi `agent-session-runtime.ts:132-147` (replaces 6h₄b no-op stub) — W4 MINOR-3: `entry_id` + `position` required (no defaults) | shipped | 0081 |
| `AgentSessionRuntime._teardown_current` ORDERING CORRECTION (P-340) — Pi `agent-session-runtime.ts:149-157`: **EMIT → INVALIDATE → DISPOSE** (Sprint 6h₄b shipped reversed order with NO shutdown emit) | shipped | 0081 |
| `AgentSessionRuntime.dispose` adds missing `session_shutdown` emit with `reason="quit"` (P-341) | shipped | 0081 |
| `AgentSessionRuntime.dispose` ORDERING CORRECTION (P-355 BLOCKING FIX) — Pi `agent-session-runtime.ts:366-373`: **EMIT → INVALIDATE → DISPOSE** (matches `_teardown_current`; the W2 §J "intentional asymmetry" rationale was a Pi misread) | shipped | 0081 |
| `AgentSessionRuntime.switch_session` ORDERING CORRECTION (W4 MEDIUM) — lifts `repo.open` + `assert_session_cwd_exists` BEFORE `_emit_before_switch` to match Pi `:184-189` (Pi asserts cwd before extensions can cancel) | shipped | 0081 |
| `previous_session_file` snapshot captured BEFORE `_teardown_current` at all 3 replace sites (`switch_session` / `new_session` / `fork`) + threaded into `_finish_session_replacement` for the `session_start` payload (P-342) | shipped | 0081 |
| `AgentSessionRuntime._finish_session_replacement` emits `session_start` on the NEW harness's runner AFTER `rebind_session` (the OLD bus is disposed by step 1) (P-343) — bootstrap `session_start` defers to Sprint 6h₅b | shipped | 0081 |
| `session/session_cwd.py` (NEW — Pi port of `session-cwd.ts:1-59`) — `SessionCwdIssue` + `MissingSessionCwdError` + `get_missing_session_cwd_issue` + `assert_session_cwd_exists` (async-adapted: Pi sync `existsSync` → Aelix all-async `FileSystem.exists`) | shipped | 0081 |
| `format_missing_session_cwd_error` Pi-verbatim format (P-346 BLOCKING FIX) — Pi `session-cwd.ts:30-37`: "Stored session working directory does not exist: ..." + conditional "Session file: ..." + unconditional "Current working directory: ..." | shipped | 0081 |
| `format_missing_session_cwd_prompt` Pi port (P-347 BLOCKING FIX) — Pi `session-cwd.ts:40-42` TUI confirmation prompt | shipped | 0081 |
| `SessionCwdIssue` field shape change (P-346) — `session_file: str \| None` (optional, default None) + `fallback_cwd: str` (required non-optional) — matches Pi `string \| undefined` + `string` | shipped | 0081 |
| `assert_session_cwd_exists` wired into `AgentSessionRuntime.switch_session` AFTER `repo.open` (P-337) — factory bootstrap site (Pi `:391`) + `importFromJsonl` site (Pi `:352`) defer to Sprint 6h₅c | shipped | 0081 |
| Pi line citation corrections (P-344 W5 BLOCKING FIX) — verified at SHA `734e08e`: `SessionStartEvent` → `:513-519`, `SessionBeforeSwitchEvent` → `:522-526`, `SessionBeforeForkEvent` → `:529-533`, `SessionShutdownEvent` → `:552-557` | shipped | 0081 |
| `tests/pi_parity/test_phase_4_14_extension_events.py` closure pin (14 invariants — 35-name `HookEventName` + 35-overload count + Pi line citation drift + cancel-aggregation + exception isolation + reducer type widening + fixture pin) | shipped | 0082 |
| `tests/pi_parity/fixtures/pi_extension_events_734e08e.json` (NEW W0 fixture — Pi SHA + 4 line citations + reducer pin + AgentSessionRuntime member citations + overload counts) | shipped | 0081 |
| `tests/pi_parity/fixtures/pi_agent_harness_event_names_734e08e.json` (AMEND — 4 new extension session lifecycle events + 4 P-344 line citations) | shipped | 0081 |
| `tests/runtime/test_agent_session_runtime_extension_events.py` (NEW — 9 wiring tests including P-355 dispose ordering pin) | shipped | 0081 |
| `tests/runtime/test_agent_session_runtime_session_cwd.py` (NEW — 3 wiring tests including assert-after-open ordering pin) | shipped | 0081 |
| `tests/session/test_session_cwd_helper.py` (NEW — 10 unit tests covering P-337 + P-346 + P-347 + field shape change) | shipped | 0081 |
| `tests/session/test_session_file_property.py` (NEW — P-349 `Session.session_file` cross-reference pin) | shipped | 0081 |
| `tests/extensions/test_extension_runner_emit_delegate.py` (NEW — P-333 ExtensionRunner emit/has_handlers bridge tests) | shipped | 0081 |
| `tests/pi_parity/test_hook_event_name_literal_pi_parity.py` (AMEND — 35-name cascade pin) | shipped | 0081 |
| `tests/pi_parity/test_phase_3_1_strict_superset.py` (AMEND — cascade count update for 35-name fixture) | shipped | 0081 |
| `tests/test_hook_payload_roundtrip.py` (AMEND — 4 new events added to roundtrip coverage) | shipped | 0081 |
| `tests/test_overloads_extension_api.py` (AMEND — 35-overload count assertion) | shipped | 0081 |
| ADR-0034 amendment — Sprint 6h₅a Phase 4.14 row (extension event wiring; RPC roster UNCHANGED) | shipped | 0034 |
| ADR-0080 amendment — Sprint 6h₅a P-307 / P-308 carry-forward CLOSE note | shipped | 0080 |
| `with_session` 2-stage callback (P-314 from ADR-0080) | deferred to Sprint 6h₅b | 0082 |
| `setup` callback in `new_session` (P-314 sibling) | deferred to Sprint 6h₅b | 0082 |
| `set_rebind_session` / `set_before_session_invalidate` optional-cb signature widening (P-315 from ADR-0080) | deferred to Sprint 6h₅b | 0082 |
| `forkFrom` cross-cwd import (no RPC wire today; P-314 carry-forward) | deferred to Sprint 6h₅b | 0082 |
| `import_from_jsonl` real runtime body (no RPC wire today; P-310 carry-forward) | deferred to Sprint 6h₅b | 0082 |
| `session_start` bootstrap emit (`reason="startup"` / `"reload"`; factory pattern change required) | deferred to Sprint 6h₅b | 0082 |
| P-351 `ExtensionRunner.invalidate` semantics (Pi `runner.ts` `invalidated` flag) | deferred to Sprint 6h₅b | 0082 |
| Pi HTML visual fidelity (CSS / syntax highlighting / responsive layout — ADR-0074 carry-forward) | deferred to Sprint 6h₅c | 0082 |
| `ImageContent` rendering in HTML export (ADR-0074 carry-forward) | deferred to Sprint 6h₅c | 0082 |
| `_get_context_usage_safe` real implementation (P-282 from ADR-0074) | deferred to Sprint 6h₅c | 0082 |
| Live `session_id` read via session manager (P-291 from ADR-0074) | deferred to Sprint 6h₅c | 0082 |
| Pi-source-grep verification tooling (P-286 from ADR-0074) | deferred to Sprint 6h₅c | 0082 |
| Factory bootstrap `assertSessionCwdExists` call site (Pi `:391`) | deferred to Sprint 6h₅c | 0082 |
| `importFromJsonl` `assertSessionCwdExists` call site (Pi `:352`) | deferred to Sprint 6h₅c | 0082 |

Sprint 6h₅a closes the P-307 / P-308 carry-forward roster from
ADR-0080 (`session_shutdown` emit + real `_emit_before_*` cancel
hooks) plus the `previousSessionFile` / `sessionStartEvent`
tracking gap plus the `assertSessionCwdExists` partial wiring (the
`switch_session` site). The closure pin
(`tests/pi_parity/test_phase_4_14_extension_events.py`) asserts the
35-name `HookEventName` cascade, the shared
`_reducer_session_before` across 4 cancellable arms, the 35-overload
count on `HookBus.on` / `ExtensionAPI.on`, the Pi line citation
drift detector, the cancel-aggregation first-cancel-wins
short-circuit, the exception isolation under `error_mode="continue"`,
and the W0 fixture pin. **RPC roster STAYS CLOSED** —
extension polish does not change the dispatch table.

### Sprint 6h₅b amendment (runtime callback Pi parity — RPC roster UNCHANGED, 2026-05-22)

Sprint 6h₅b (Phase 4.15) wires the runtime callback Pi parity surface
on top of the 6h₅a extension event lifecycle wiring. Closes the
ADR-0082 §"Sprint 6h₅b carry-forward" subset — `with_session` /
`setup` 2-stage callbacks on the 3 replace APIs +
`import_from_jsonl` real body + :meth:`JsonlSessionRepo.fork_from`
cross-cwd import surface + :meth:`ExtensionRunner.invalidate` runtime
bridge with :data:`PI_STALENESS_MESSAGE` single source of truth.
W4/W5 audit returned **3 MAJOR + 2 MINOR (no BLOCKING)** — every
must-fix applied (P-364 ~ P-368). **RPC roster STAYS CLOSED** at
SUPPORTED **29** / DEFERRED **0** / total **29** — runtime / extension
polish does not change the dispatch table.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₅b | Phase 4.15 | runtime callback Pi parity (with_session / setup / import_from_jsonl / fork_from / ExtensionRunner.invalidate) | SUPPORTED 29 → **29**, DEFERRED 0 → **0** | ADR-0083, ADR-0084 |
| `ReplacedSessionContext` Protocol in `runtime/_types.py` (P-356 BINDING — Protocol over `SimpleNamespace` factory output; structural conformance via :data:`typing.runtime_checkable`) | shipped | 0083 |
| `AgentHarness.create_replaced_session_context` factory (P-357 BINDING — returns :class:`types.SimpleNamespace` Pi `Object.defineProperties` clone idiom; W6 P-364 fix adds optional `runtime` kwarg wiring 6 `ExtensionCommandContext` methods) | shipped | 0083 |
| `with_session: Callable[[ReplacedSessionContext], Awaitable[None]] \| None = None` plumbed onto `switch_session` / `new_session` / `fork` / `_finish_session_replacement` (P-358 BINDING) | shipped | 0083 |
| `setup: Callable[[ReadonlySessionManager], Awaitable[None]] \| None = None` in `new_session`; runs AFTER `_apply` BEFORE rebind; rebuilds `harness._state.messages` from `new_session.build_context()` (P-359 BINDING — Pi `:226-229`) | shipped | 0083 |
| `AgentSessionRuntime.import_from_jsonl` real body — Pi `:329-364` port; replaces Sprint 6h₄c `NotImplementedError` stub; raises :class:`SessionImportFileNotFoundError` for missing path; cancel short-circuit; copy when different; cwd override via :func:`dataclasses.replace`; `assert_session_cwd_exists` after `repo.open`; NO `with_session` plumbing (Pi confirms) (P-360 BINDING) | shipped | 0083 |
| :class:`SessionImportFileNotFoundError` aligned to Pi `:39-47` verbatim — message `File not found: {file_path}` + `file_path` attribute (W6 P-366 W5 MAJOR fix) | shipped | 0083 |
| :data:`PI_STALENESS_MESSAGE` constant in `runtime/_types.py` (Pi verbatim from `runner.ts:467`) — single source of truth shared by :meth:`ExtensionRunner.invalidate` and :meth:`_ExtensionRuntime.invalidate` (P-362 BINDING) | shipped | 0083 |
| :meth:`JsonlSessionRepo.fork_from` cross-cwd import (P-361 BINDING — Pi `session-manager.ts:1353-1394`; loads ALL source entries no leaf truncation; W6 P-368 W5 MINOR fix adds optional `session_dir` keyword arg mirroring Pi 3rd parameter) | shipped | 0083 |
| :meth:`JsonlSessionRepo.open` `cwd_override` keyword arg (W6 P-367 W5 MINOR fix — replaces `storage._metadata` mutation from outside repo; centralizes Pi `SessionManager.open(path, dir, cwdOverride)` parity on a single owner) | shipped | 0083 |
| :meth:`ExtensionRunner.invalidate` + `_invalidate_runtime` runtime bridge (P-362 BINDING — SYNTHESIS per spec §J; runner is Pi-named entry point that delegates; runtime is single source of truth; default arg falls back to :data:`PI_STALENESS_MESSAGE`) | shipped | 0083 |
| :meth:`ExtensionRunner.assert_active` delegates to runtime via bridge (W6 P-365 W5 MAJOR fix — raises plain :class:`RuntimeError` to avoid `aelix_agent_core → aelix_coding_agent` reverse import; :class:`ExtensionError` continues to fire via :meth:`ExtensionContext.__getattribute__`) | shipped | 0083 |
| `ExtensionRunner` dataclass drops `frozen=True` to allow bridge field rebind by tests (per spec §J SYNTHESIS the runner holds NO `_stale_message` field) (P-362 BINDING) | shipped | 0083 |
| :meth:`_ExtensionRuntime.invalidate` default arg aligned to :data:`PI_STALENESS_MESSAGE` (P-362 BINDING — bypass-runner callers see SAME message as routed callers) | shipped | 0083 |
| `runner.invalidate(PI_STALENESS_MESSAGE)` call inserted in `_teardown_current` + `dispose` between EMIT and `before_session_invalidate` (P-363 BINDING — Pi `runner.ts:466-473`) | shipped | 0083 |
| :class:`ReplacedSessionContext` Protocol widened 13 → 19 members — adds 6 `ExtensionCommandContext` methods (`wait_for_idle` / `new_session` / `fork` / `navigate_tree` / `switch_session` / `reload`) per Pi `extensions/types.ts:371` `extends ExtensionCommandContext` (W6 P-364 W5 MAJOR fix) | shipped | 0083 |
| `AgentHarness.create_replaced_session_context(runtime=...)` factory wires 6 ExtensionCommandContext methods — harness-side (`wait_for_idle` / `navigate_tree`) + runtime-side (`new_session` / `fork` / `switch_session`) + Aelix-additive stub (`reload`) (W6 P-364 W5 MAJOR fix) | shipped | 0083 |
| :class:`FileSystem.copy_file` Protocol method + :class:`LocalFileSystem` impl backed by :func:`shutil.copy2` (P-360 supporting infra) | shipped | 0083 |
| `tests/runtime/test_replaced_session_context.py` (NEW — 8 tests: factory `SimpleNamespace` + Protocol conformance + baseline fields + send_message routing + send_user_message routing + 6 ExtensionCommandContext method exposure + unbound-runtime raise + reload stub) | shipped | 0083 / 0084 |
| `tests/runtime/test_with_session_callback.py` (NEW — 6 tests: 3 replace APIs accept with_session + bound-to-NEW-harness + raises propagate + runs after rebind) | shipped | 0083 / 0084 |
| `tests/runtime/test_setup_callback_new_session.py` (NEW — 4 tests: invoked with NEW session_manager + runs before rebind + appends visible in rebuilt messages + optional path) | shipped | 0083 / 0084 |
| `tests/runtime/test_import_from_jsonl_real.py` (NEW — 6 tests: missing path raises + Pi-verbatim message + same-dir skips copy + cwd override + cancel short-circuits + different-dir copies) | shipped | 0083 / 0084 |
| `tests/session/test_jsonl_fork_from.py` (NEW — 7 tests: ALL entries no truncation + target cwd matches + parent_session_path + new id/path + round-trip + optional session_dir + default preserved) | shipped | 0083 / 0084 |
| `tests/harness/test_extension_runner_invalidate.py` (NEW — 9 tests: bridge propagation + default PI_STALENESS_MESSAGE + idempotent + no-bridge no-op + harness wires bridge + runtime default aligned + teardown invokes + dispose invokes + assert_active SYNTHESIS no-op) | shipped | 0083 / 0084 |
| `tests/extensions/test_extension_runner_emit_delegate.py` (AMEND — frozen=True drop verified per P-362) | shipped | 0083 |
| `tests/runtime/test_agent_session_runtime.py` (AMEND — `import_from_jsonl` stub coverage migrated to `test_import_from_jsonl_real.py`) | shipped | 0083 |
| ADR-0034 amendment — Sprint 6h₅b Phase 4.15 row (runtime callback Pi parity; RPC roster UNCHANGED) | shipped | 0034 |
| ADR-0082 amendment — Sprint 6h₅b carry-forward CLOSE note (5 items CLOSED per ADR-0083) | shipped | 0082 |
| `session_start` bootstrap emit (`reason="startup"` / `"reload"`; factory pattern change required) | deferred to Sprint 6h₅c | 0084 |
| Factory bootstrap `assertSessionCwdExists` call site (Pi `:391`) | deferred to Sprint 6h₅c | 0084 |
| Pi HTML visual fidelity (CSS / syntax highlighting / responsive layout — ADR-0074 carry-forward) | deferred to Sprint 6h₅c | 0084 |
| `ImageContent` rendering in HTML export (ADR-0074 carry-forward) | deferred to Sprint 6h₅c | 0084 |
| `_get_context_usage_safe` real implementation (P-282 from ADR-0074) | deferred to Sprint 6h₅c | 0084 |
| Live `session_id` read via session manager (P-291 from ADR-0074) | deferred to Sprint 6h₅c | 0084 |
| Pi-source-grep verification tooling (P-286 from ADR-0074) | deferred to Sprint 6h₅c | 0084 |

Sprint 6h₅b closes the runtime callback Pi parity carry-forward
roster from ADR-0082 (`with_session` / `setup` / `import_from_jsonl`
body / `forkFrom` cross-cwd + P-351 `ExtensionRunner.invalidate`).
The closure pin lane sits on the 6 new unit-test files this sprint;
no new `tests/pi_parity/` closure pin file lands (no new
`HookEventName` literal, no new RPC commands). **RPC roster STAYS
CLOSED** — runtime / extension polish does not change the dispatch
table. Sprint 6h₅c carries forward the bootstrap emit + HTML visual
fidelity + `_get_context_usage_safe` + `ImageContent` items.

### Sprint 6h₅c amendment (visual fidelity + context_usage + bootstrap session_start + factory cwd + ImageContent — A 단계 closure, 2026-05-22)

Sprint 6h₅c (Phase 4.16) closes the 5 binding carry-forward items
from ADR-0084 §"Sprint 6h₅c carry-forward" end-to-end:
:meth:`AgentHarness._get_context_usage_safe` real async impl over Pi
`compaction.ts:135-279` + `agent-session.ts:2946-2990`, module-level
:func:`create_agent_session_runtime` factory with bootstrap
`session_start(reason="startup")` emit + factory-bootstrap
:func:`assert_session_cwd_exists` site (Pi `:391`), `_export_html/`
directory restructure with Pygments + markdown-it-py syntax
highlighting + curated dark theme, and :class:`ImageContent` inline
base64 `<img>` rendering per Pi `template.js:909`. W4/W5 audit
returned **1 MAJOR + 1 MEDIUM + 3 MINOR + 1 NIT** — every load-bearing
must-fix landed in W6 closure (P-374 + W4 MEDIUM + P-377 + W4 NIT).
**RPC roster STAYS CLOSED** at SUPPORTED **29** / DEFERRED **0** /
total **29** — runtime / visual polish does not change the dispatch
table. **A 단계 (Phase 4 strict Pi-parity superset) CLOSED** — see
ADR-0086 for the full delivery ledger.

| Component | Status | Owner ADR |
|---|---|---|
| 6h₅c | Phase 4.16 | visual fidelity + context_usage real + bootstrap session_start + factory cwd + ImageContent | SUPPORTED 29 → **29**, DEFERRED 0 → **0** | ADR-0085, ADR-0086 |
| `session/compaction.py` 4 Pi-parity helpers — `calculate_context_tokens` / `estimate_tokens` / `estimate_context_tokens` / `get_latest_compaction_entry` (P-369 BINDING — Pi `compaction.ts:135-279`) | shipped | 0085 |
| `estimate_tokens` :class:`ThinkingContent` branch BEFORE catch-all (W4 MEDIUM fix — Pi treats every content block uniformly; W2 catch-all `hasattr(block, "text")` missed `block.thinking`) | shipped | 0085 |
| `AgentHarness._get_context_usage_safe` real async impl — full Pi `getContextUsage` algorithm (Pi `agent-session.ts:2946-2990`); 4-branch logic (no-model → None / no-session → heuristic / compaction-no-usage → sentinel / default → full triple); 3 callers updated with `await` (P-369 BINDING) | shipped | 0085 |
| `_ExtensionContext.get_context_usage` real sync bridge via heuristic estimate path (W6 P-374 W5 MAJOR fix — W2 left Sprint 5a `return None` stub; bridge stays sync because Pi `getContextUsage` returns sync; full async algorithm reachable via async harness method — Aelix-additive divergence #3 per ADR-0085) | shipped | 0085 |
| `runtime/agent_session_runtime.py` module-level :func:`create_agent_session_runtime` async factory (P-370 BINDING — Pi `agent-session-runtime.ts:382-400`) | shipped | 0085 |
| Factory bootstrap `assert_session_cwd_exists` site (P-370 BINDING — Pi `:391`; runs against `harness._session` BEFORE :class:`AgentSessionRuntime` construction; skipped silently when `harness._session is None` for in-memory tests) | shipped | 0085 |
| Factory bootstrap `session_start(reason="startup")` emit (P-371 BINDING — Pi `:326` + `:2050`; optional `session_start_event=None` kwarg mirrors Pi `??` default; gated on `ExtensionRunner.has_handlers`; raises caught + logged matching :meth:`_finish_session_replacement` P-343 emit policy) | shipped | 0085 |
| `runtime/__init__.py` re-exports :func:`create_agent_session_runtime` | shipped | 0085 |
| `_export_html/` directory restructure — `__init__.py` (re-exports :func:`export_html`) + `template.py` (`_THEME_CSS` constant ~240 LOC curated dark theme + Pygments token classes via `HtmlFormatter.get_style_defs(".pyg")` + `_HTML_TEMPLATE` HTML5 skeleton) + `format.py` (renderer pipeline: markdown-it-py commonmark + table + breaks; Pygments fenced code highlight; role-section dispatch; content-block renderer) (P-372 BINDING — Pi `coding-agent/src/core/export-html/`) | shipped | 0085 |
| `_export_html.py` single-file Sprint 6h₃ minimal renderer DELETED (replaced by 3-module package) | shipped | 0085 |
| `pygments>=2.18` + `markdown-it-py>=3.0` added to `packages/aelix-coding-agent/pyproject.toml` (P-372 supporting infra) | shipped | 0085 |
| :class:`ImageContent` HTML rendering — inline base64 `<img>` tag with `data:{mime};base64,{data}` URI mirroring Pi `template.js:909`; non-tool-result variant `class="message-image"`; tool-result variant `class="tool-image"` ONLY (Pi strict literal per P-377) (P-373 BINDING) | shipped | 0085 |
| Strict Pi `tool-image` class literal (W6 P-377 W5 MINOR fix — Pi `template.js:909` uses `class="tool-image"` ONLY for tool-result images; W2 emitted combined `class="message-image tool-image"`; W6 emits literal `"tool-image"` byte-for-byte) | shipped | 0085 |
| W4 NIT — dead code drop in `tests/harness/test_context_usage.py` (`chars_tool = msg_tool.content[0]; _ = chars_tool` removed) | shipped | 0085 |
| `tests/harness/test_context_usage.py` (NEW — 9 tests: ThinkingContent branch + `_ExtensionContext.get_context_usage` real-bridge tests + Pi-shape helper assertions) | shipped | 0085 |
| `tests/harness/test_harness_get_session_stats.py` (AMEND — async `_get_context_usage_safe` migration) | shipped | 0085 |
| `tests/test_factory_assert_session_cwd.py` (NEW — 3 tests: cwd-assertion fires BEFORE construction + skips when no session + uses harness session for cwd) | shipped | 0085 |
| `tests/test_bootstrap_session_start.py` (NEW — 5 tests: factory emits with `reason="startup"` + custom event override + skip-when-no-handlers + replacement uses `reason="new"`/`"resume"` regression + bootstrap runs after construction) | shipped | 0085 |
| `tests/test_export_html_visual_fidelity.py` (NEW — 7 tests: base64 img tag + Pi-strict tool-image class + XSS-safe escape + markdown paragraph + Pygments token classes + unknown-lang fallback + theme CSS includes Pygments styles) | shipped | 0085 |
| ADR-0034 amendment — Sprint 6h₅c Phase 4.16 row (visual fidelity + context_usage + bootstrap + cwd + ImageContent; RPC roster UNCHANGED; **A 단계 closure milestone**) | shipped | 0034 |
| ADR-0084 amendment — Sprint 6h₅c carry-forward CLOSE note (5 items CLOSED per ADR-0085 / ADR-0086) | shipped | 0084 |
| ANSI → HTML pipeline (Pi `ansi-to-html.ts`) | deferred to Sprint 6h₅d | 0085 |
| Tool-renderer per-tool templates (bash / read / write / edit / ls) | deferred to Sprint 6h₅d | 0085 |
| Client-side JS port (sidebar / tree navigation) | deferred to Sprint 6h₅d | 0085 |
| Pi color-derivation math (luminance-based theme) | deferred to Sprint 6h₅d | 0085 |
| `reload()` bootstrap emit branch (Pi `:2401` — `reason="reload"`) | deferred to Sprint 6h₅d | 0085 |
| Pixel-perfect HTML closure pin tests | deferred to Sprint 6h₅d | 0085 |
| P-375 monkeypatch fragility in `tests/test_factory_assert_session_cwd.py` | deferred to Sprint 6h₅d | 0085 |
| MINOR-1 f-string assembly polish in `_export_html/format.py` | deferred to Sprint 6h₅d | 0085 |
| MINOR-3 `harness._session` private-attribute reads (read-through property or factory accessor) | deferred to Sprint 6h₅d | 0085 |
| Live `session_id` read via session manager (P-291 from ADR-0074) | deferred to Sprint 6h₅d | 0085 |
| Pi-source-grep verification tooling (P-286 from ADR-0074) | deferred to Sprint 6h₅d | 0085 |

Sprint 6h₅c closes ALL 5 binding carry-forward items from ADR-0084
§"Sprint 6h₅c carry-forward" + applies every load-bearing W4/W5
audit triage item. The closure pin lane sits on the 4 new unit-test
files this sprint (no new `tests/pi_parity/` closure pin file lands —
no new `HookEventName` literal, no new RPC commands). **RPC roster
STAYS CLOSED** — runtime / visual polish does not change the
dispatch table. **A 단계 (Phase 4 strict Pi-parity superset) CLOSED**
— ADR-0086 records the full 14-row delivery ledger across the
6a → 6h₅c sprint chain. Sprint 6h₅d carries forward visual polish
+ grep tooling + minor cleanups (no RPC dispatch impact).

### Sprint 6h₆ amendment (Aelix CLI entrypoint — Phase 5a-i + 5a-ii / B 단계 opens, 2026-05-22)

Sprint 6h₆ ports the **non-interactive** half of the Pi CLI entry
(`main.ts` 716 LOC reduced) — `--print` / `--mode text|json|rpc` /
`--help` / `--version` paths plus the supporting hand-rolled arg
parser (`cli/args.ts` 354 LOC), file-arg processor
(`cli/file-processor.ts`), initial-message builder
(`cli/initial-message.ts`), and the print-mode lifecycle
(`modes/print-mode.ts` 158 LOC). Interactive mode raises
:class:`NotImplementedError` with a stderr diagnostic pointing to
ADR-0088 (Phase 5b TUI library decision is deferred). **B 단계
formally opens** with Sprint 6h₆; Pi pin advances are permitted
starting B 단계 per ADR-0034 update policy but the Sprint 6h₆ scope
does NOT advance the pin (stays at `734e08e…`).

| Component | Status | Owner ADR |
|---|---|---|
| 6h₆ | Phase 5a-i + 5a-ii | non-interactive CLI entrypoint | RPC roster UNCHANGED (29 / 0 / 29) | ADR-0088, ADR-0089 |
| `aelix_coding_agent.cli.config` (`APP_NAME = "aelix"` + `VERSION = "0.1.0"`) | shipped | ADR-0089 |
| `aelix_coding_agent.cli.args` (Pi `cli/args.ts` 354 LOC hand-rolled linear parser; 30+ flags; `Args` dataclass with `messages` + `file_args` + `unknown_flags` + `diagnostics`) | shipped | ADR-0089 |
| `aelix_coding_agent.cli.file_processor` (Pi `cli/file-processor.ts` text branch; image branch deferred to 5a-iii) | shipped | ADR-0089 |
| `aelix_coding_agent.cli.initial_message` (Pi `cli/initial-message.ts` w/ `.shift()` side effect on `parsed.messages`) | shipped | ADR-0089 |
| `aelix_coding_agent.modes.print_mode` (Pi `modes/print-mode.ts` 158 LOC 9-step lifecycle) | shipped | ADR-0089 |
| `aelix_coding_agent.cli.entry.main_sync` + `_async_main` (Pi `main.ts:96-113` + `:423-716` reduced for non-interactive scope) | shipped | ADR-0089 |
| `aelix_coding_agent.__main__` (wires `python -m aelix_coding_agent` to `main_sync`) | shipped | ADR-0089 |
| `[project.scripts] aelix = "aelix_coding_agent.cli.entry:main_sync"` (wires `aelix` console script) | shipped | ADR-0089 |
| `--print` `---` triple-dash escape (W6 P-396 MAJOR Pi parity `args.ts:123-129`) | shipped | ADR-0089 |
| `--list-models` `@` exclusion (W6 P-397 MAJOR Pi parity `args.ts:154-160`) | shipped | ADR-0089 |
| Unknown-flag `@` exclusion (W6 P-398 MAJOR Pi parity `args.ts:167-180`) | shipped | ADR-0089 |
| `print_help(out: TextIO \| None)` typing upgrade (W6 W4 MAJOR) | shipped | ADR-0089 |
| Phase 5b TUI library decision analysis (textual / rich / prompt-toolkit / blessed evaluation + library-agnostic `Component` Protocol invariant) | proposed (DEFERRED) | ADR-0088 |
| `SettingsManager` / `--list-models` real wire / image-resize / migrations / session-picker / `--append-system-prompt` harness wire / `ResourceLoader` / `takeOverStdout` / Pi `print_help` full text / Session.subscribe surface | deferred to Sprint 5a-iii / 5a-iv | ADR-0089 |
| Interactive TUI mode (textual + rich PRIMARY recommendation, prompt-toolkit + rich ALTERNATIVE, textual alone CONTINGENCY) | deferred to Phase 5b | ADR-0088 |
| TTY second-pass demotion (interactive mode prerequisite) | deferred to Phase 5b | ADR-0089 |
| `killTrackedDetachedChildren` (Bash extension tracker prerequisite) | deferred — demand-driven | ADR-0089 |

Sprint 6h₆ ships the non-interactive CLI **without** moving the RPC
roster needle (SUPPORTED stays at 29, DEFERRED stays at 0, total
stays at 29) and **without** advancing the Pi pin. The Aelix-additive
CLI surface (entrypoint + arg parser + print mode + file processor +
initial-message builder + config) lands as a strict Pi-parity port
modulo the documented divergences (APP_NAME = "aelix", `argparse` /
`click` rejected, interactive deferred, etc. — see ADR-0089
§"Aelix-additive divergences from Pi"). The 3 Pi-parity regression
tests landed in this sprint (P-396 / P-397 / P-398) raise the test
count to **2077 passed + 1 skipped**.

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
