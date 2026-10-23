# PR #593 runtime-control review: remediation record (#594)

Status: Draft (implementation pass, Codex round 1 fix and repair pass 2 done; root
review, commit, final full suite and CI pending)
Date: 2026-10-23
Reviewed head: `10854a2d4cfc089cdf021c3f2e251bce9f230d94` (base `7d0a2c0f`)
Review: `.omc/specs/review-rpc-pr593-claude-opus55-xhigh-2026-10-23.md`, Codex round 1
`.omc/specs/594-codex-review-r1.md` (both in the main checkout)
Decision record: [ADR-0275](../decisions/0275-acp-runtime-controls-use-a-separate-rpc-envelope.md)

How each disposition was established:

- **Reproduced.** A test or script failed on `10854a2d` because of the behaviour the review
  described, then passed after the fix: F1 (direct reload and, from round 1, a command's own
  replacement), F2, F3, F4, F5, F6, F7, F8 (the `allowProject` bypass), F9 (static
  capability list), F10 (ignored dialog timeout, blank error), F11, F12 (dotted name
  accepted), U3.
- **New API, red only by absence.** Some cases failed before the fix only because the API
  they call did not exist yet (`RpcMcpController(connect_timeout=...)`, `provider_safe_tools`,
  the launch-approval path of `agents/run`). They specify the fix rather than reproduce a
  defect.
- **Contract or documentation.** F13, plus the response contracts written for F6 (`restored`,
  explicit failure) and F9. These were checked against the code, not by a failing test.

## Dispositions

| # | Disposition | Evidence |
|---|---|---|
| F1 cancel cut reload/branch | **Fixed, reproduced.** On the unfixed tree a cancel during the factory build left `runtime.harness` disposed (`is_stale` true). Now `commands/cancel` targets one operation (`requestId`, or the running one). Only `commands/run`, `agents/run`, `auth/login` and `mcp/configure` are cancellable while running. A reload or branch finishes and answers `not_cancellable`. No kernel change. | `test_cancel_does_not_interrupt_a_reload`, `test_cancel_by_request_id_targets_a_queued_mutation`; live: cancel sent with a reload → `not_cancellable`, reload completed |
| F1 round 1: a command's own replacement | **Fixed, reproduced** (Codex probe `.omc/probes/594-extension-lifecycle-cancel.py`: `cancelled:true`, `CancelledError`, `live_is_previous:true`, `live_runtime_stale:true`). The fix in the first pass protected only the direct methods. `run_command` now hands command contexts a guarded runtime view (`_GuardedSessionRuntime`). Its `new_session`/`fork`/`switch_session`/`reload` run through `RpcControlPlane.transition`, the same boundary the direct lifecycle methods use: run to completion, lock held, classic mutators refused, refused while an admitted classic command is still answering. A cancel or EOF that lands inside takes effect when the replacement returns (`deferred: true`). The command stays cancellable before and after it. Protection is decided by the runtime call, not the command name. No kernel or `command_context.py` change. | `test_cancel_waits_out_a_commands_session_replacement[new_session/fork/switch_session/reload]`, `test_eof_waits_out_a_commands_session_replacement`, `test_classic_mutators_wait_out_a_commands_session_replacement`, `test_a_command_stays_cancellable_after_its_replacement`, `test_a_commands_replacement_waits_for_a_classic_command_to_answer` (red: 6 failed before the fix); probe after the fix: `deferred:true`, `CancelledError`, `live_is_previous:false`, `live_runtime_stale:false`; live: `/slowfresh` cancelled inside its replacement, then a real turn on the new session |
| F2 MCP vs explicit selection | **Fixed, reproduced.** Removing a selected client tool raised `AgentHarnessError`, and a reload dropped the selected client tool. Now a lease passes an explicit selection: old client names are dropped, only same-named selected tools are carried, and new tools are never added. A reload re-selects client tools that were selected; other replacements start from launch. | `test_removing_a_selected_client_mcp_tool_succeeds`, `test_new_client_mcp_tools_never_widen_an_explicit_selection`, `test_reload_keeps_a_selected_client_mcp_tool_active`; live: selection survived reload and branch |
| F3 command UI vs approval | **Fixed, reproduced.** `set_widget` under `ui/enable` raised `NotImplementedError`, and the gate would block a built-in write (#395) or prompt over the dialog channel. `RpcCommandUI.provides_tool_approval = False`, so the gate keeps its headless verdict (the adapter's bridge hook stays the approver). Terminal-only methods are no-ops, as in pi's RPC mode. `has_ui` itself is unchanged. | `test_command_dialogs_leave_the_headless_permission_verdict_alone`; tests/builtin 1075 passed |
| F4 EOF ran queued work | **Fixed, reproduced.** A mutation queued at EOF ran after the client left. `begin_shutdown` refuses new mutations, cancels queued and cancellable ones, settles dialogs as cancelled, and lets a running lifecycle transition finish. | `test_eof_cancels_queued_mutations_instead_of_running_them`, `test_eof_lets_a_running_reload_finish`; live: exit 0 after EOF |
| F5 classic/control race | **Fixed, reproduced.** `new_session` succeeded while a control command held the lock, and `permissions/set` passed beside a classic prompt whose turn had not started. Now transition commands are refused while a mutation holds the lock, everything but reads and aborts is refused during a lifecycle op, and mutations are refused while a counted classic command (a prompt until it is accepted) is in flight. The `mcp/configure` lease also reads the live harness when it applies. | `test_classic_session_replacement_waits_out_a_control_mutation`, `test_control_mutation_refuses_a_classic_prompt_still_starting`, `test_a_reload_admits_only_classic_reads_and_aborts` |
| F6 branch return | **Fixed + contract stated, reproduced.** A vetoed return was reported as success, and the selection was lost. The return is a real `switch_session` (hooks fire, as on `/resume`). The selection is snapshotted and restored, and success carries `restored: true`. A vetoed or failed return is an error naming the branch file and the live session. | `test_branch_restores_the_original_session_and_tool_selection`, `test_branch_reports_a_vetoed_return`; live: `restored: true`, same session file, same selection |
| F7 evaluate blind | **Fixed, reproduced** (PLAN + bash `ls` returned `allow`). `permissions/evaluate {tool?, args}` reads provenance from the live tool object, and its verdict comes from `posture_decision`, which the gate's `_on_tool_call` now also uses. Without `tool` the call is treated as aelix `bash`, the shape the shipped adapter sends. | `test_permission_evaluation_uses_tool_provenance_and_posture`; live: MCP tool → `ask` |
| F8 `allowProject` | **Fixed, reproduced.** `allowProject` is rejected. A project profile runs only with `--approve` (the service's `confirm_project`) or the TUI's confirmation sent as an RPC `select` naming the file that will run. | `test_a_client_boolean_is_not_project_profile_consent`, `test_project_profile_runs_after_the_rpc_confirmation`, `test_project_profile_runs_under_launch_approval` |
| F9 static capabilities | **Fixed, reproduced.** Methods follow the injected services, and `agents/run` needs a bound subagent runtime. The WebSocket bridge advertises the reduced set, and its docstring is corrected. | `test_capabilities_follow_the_injected_services`; live: 22 methods on the CLI host |
| F10 timeouts/blank errors | **Fixed, reproduced.** Dialog timeout, `opts.timeout`, `opts.signal` and shutdown all resolve as cancelled. Control errors fall back to the exception class name. The MCP connect timeout names itself. | `test_dialog_timeout_signal_and_shutdown_resolve_as_cancelled`, `test_mcp_connect_timeout_names_itself`, `test_control_errors_are_never_blank` |
| F11 settings not flushed | **Fixed, reproduced.** `settings/set` awaits `flush()` and fails on `drain_errors()`. Errors queued before the write go to stderr, as at startup. | `test_settings_are_flushed_and_write_errors_reported`; live: value on disk at response time |
| F12 MCP names/diagnostics | **Fixed, reproduced** (dotted names produced provider-invalid tool names). Names are mapped, not refused: `[^A-Za-z0-9_-]` → `_`, cut to 32 characters, and keys must be unique. Statuses carry `clientName`. Tools whose qualified name breaks `[A-Za-z0-9_-]{1,64}` are skipped and listed in `skippedTools`. A failed connect names the server and the error class only. | `test_mcp_server_names_map_to_provider_safe_keys`, `test_tools_with_unsafe_qualified_names_are_skipped_with_diagnostics`; live: `"live fixture.dev"` → `acp_live_fixture_dev`, tool called by a real model |
| Repair pass 2: picker inventory | **Fixed (test inventory only).** Root's full run caught the new RPC consent `select` missing from `tests/tui/test_own_pickers_marked.py::_EXPECTED`. The row is registered as `[False]` with its reason (RPC dialog, `RpcCommandUI.select` has no `own`). No production, contract or other test change. | 84 targeted tests passed (keys unset) |
| F13 docs | **Fixed.** ADR-0275 rewritten (status per `docs/00-conventions.md`, decisions match code, validation stated as synthetic plus real-provider) and indexed. CHANGELOG entry added. extension-authoring guide (and its bundled copy) explains RPC dialogs. Repo-wide formatting not touched (ADR-0152); only the three new rpc modules and the two new test files were formatted. | gates below |

## Unconfirmed items

- **U1 (MCP transport failure): disproved for the scope.** A probe exited one of two stdio servers after the handshake. The lease survived, the other server kept answering, and the owner task stayed alive. Residual, unchanged: `connected` stays true for an exited stdio server, because it means "session established", not liveness.
- **U2 (branch writer contention): not reproduced.** If the return to the original contends for its writer lock or fails, the branch now reports the error and the live session instead of claiming success.
- **U3 (unserializable result): hardened.** The result is serialised inside the guard, so the id is always answered (`test_an_unserializable_result_still_answers`). No current method returns non-JSON data.
- **U4 (companion usage): resolved by reading `handochan/aelix-acp#1` @ `7f49cab`.** The adapter approves through its `bridge.py` `tool_call` hook. It sends `commands/cancel {}` on `session/cancel`, which made F1 reachable from a Studio stop. It calls `permissions/evaluate` only for bash in `auto`, never sends `allowProject`, re-applies its selection after `mcp/configure` and reload, and feature-detects through `capabilities`.
  - Compatible changes: new response keys, and errors where a write or return failed.
  - Visible change: `/agents run` without `--enable-agents` now meets "needs Aelix runtime control v1 (agents/run)" in the adapter, because `agents/run` is no longer advertised without delegation. That is adapter wording, not a native change.

## Verification

These are this implementer's runs on the round-1 candidate. They are evidence for
that candidate only. Codex re-runs the full suite, the companion suite and CI on the
final code.

| Check | Result |
|---|---|
| New tests on `10854a2d` (red phase, first pass) | lifecycle 7/7 failed, surfaces 15/16 failed (the passing case is a guard that also held before) |
| Round-1 cases on the first candidate (red) | 6 failed (4 parametrised cancel cases, EOF, classic mutators) |
| `pytest tests/rpc/test_runtime_control*.py` (3 files), provider keys unset | 53 passed |
| Focused suites, host environment with every provider `*_API_KEY` unset (rpc, server, mcp_client, builtin, agents, agents_ext, runtime, session, extensions, pi_parity, contracts, cli, docs + citation/docs-bundle/guide/import/set_active/command-context, tui permission-dialog files, extension discovery, builtin policy/guardrail, google adapter) | **9866 passed, 34 skipped, 0 failed** (320.8 s) |
| The same set under `env -i HOME=… PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=…` | 9777 passed, 122 skipped, 1 failed. The failure is `tests/mcp_client/test_stdio_child_env.py::test_the_parent_environment_is_genuinely_larger`, a fixture self-check that needs a parent environment of more than 30 variables (`env -i` gives 9). It is an artifact of the stripped environment. More skips because tools are off PATH. |
| Host-environment sensitivity, untouched code | `tests/cli/test_runtime_bootstrap.py::test_default_provider_resolves_a_bare_id_nothing_else_claimed` fails only with a host `ANTHROPIC_API_KEY` set. `tests/rpc/test_rpc_mode_get_available_models.py` fails only with a host `OPENROUTER_API_KEY` set. Both pass with the key unset; neither file nor its code is in this diff. |
| `ruff check .` | All checks passed |
| `scripts/check_types.py` | 298 files, 0 errors, spike inverted |
| `scripts/check_citations.py --check` | 932 gated, none drifted |
| `scripts/generate_contracts_schemas.py --check` | no drift |
| Codex probe `.omc/probes/594-extension-lifecycle-cancel.py` on this candidate | `deferred:true`, `CancelledError`, `live_is_previous:false`, `live_runtime_stale:false` |
| Real provider (`.omc/specs/594-live-rpc-check.py`, Anthropic `claude-haiku-4-5`, key from the process environment, never printed) | 15/15 PASS. Covers: real turn; MCP tool turn with a mapped name; evaluate; reload under cancel; selection kept across reload and branch; branch restored; settings on disk; a command's `ctx.new_session` then a real turn on the new session; a command's `ctx.switch_session`; a cancel landing inside a command's replacement (`deferred`, command cancelled, new session live, real turn after); exit 0 at EOF |
| Root (Codex) on the first candidate, for reference only | native control group 45 passed / 1.87 s; companion `aelix-acp@7f49cab` (real native subprocesses, synthetic model) 73 passed / 17.113 s. Neither was re-run on this candidate. |
| Root full `pytest` on the round-1 candidate (source fingerprint stable during the run) | **18382 passed, 48 skipped, 1 failed** (863.18 s). The failure was `tests/tui/test_own_pickers_marked.py::test_no_picker_call_site_is_missing_from_the_table`, the inventory of every product `select`/`confirm` call site. It was missing `('rpc/control.py', '_consent_to_project_profile', 'ui.select'): [False]`. That call goes to `RpcCommandUI.select`, which takes no `own=`; `own` is a TUI picker layout flag. So the fix is the inventory row, with its reason, not a runtime change. The scanner and assertions are unchanged. |
| Repair pass 2 (inventory row only), provider keys unset: `pytest tests/tui/test_own_pickers_marked.py tests/rpc/test_runtime_control_lifecycle_594.py tests/rpc/test_runtime_control_surfaces_594.py tests/rpc/test_runtime_control.py` | 84 passed. Targeted only: the full suite was not re-run after the row was added. The final full suite and remote CI on the commit are still pending. |

Not established yet:
- a passing full `pytest` on the final commit (root's run above had 1 failure, which the
  inventory row addresses; only targeted tests were re-run since);
- remote CI, including Windows;
- the WebSocket server against a real client;
- Aelix Studio;
- the ACP adapter suite on this candidate;
- real-provider OAuth (`auth/login`).

These acceptance levels stay open.

## Coordinator verification and delivery

Codex independently re-ran the extension-command cancellation probe after repair:
the cancellation was deferred, the session replacement completed, and the live
harness was new and not stale. The frozen companion adapter at `7f49cab` was
retested against the repaired native workspace: **73 tests passed in 17.605 s**.
Those native subprocess tests use a local synthetic provider, distinct from the
15/15 real-provider scenarios above.

The final inventory-only correction leaves production code unchanged. Its
picker registry and runtime-control group passed **84 tests in 2.60 s** in the
implementation pass. The first full-suite result and its one corrected inventory
failure remain recorded above; they are not presented as a final pass.

For the completed repeat of the local full suite, exact commit, and remote CI,
see the maintained verification table on [PR #593](https://github.com/handochan/aelix-ai/pull/593).
The companion's committed runtime reference still pins the earlier `10854a2d`;
these compatibility runs explicitly supplied the corrected native workspace.
