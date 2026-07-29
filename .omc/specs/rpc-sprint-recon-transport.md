# RPC transport recon — the long-lived `RpcChannel`

> **Provenance.** Recovered from the P3 recon workflow's journal
> (`~/.claude/projects/<proj>/subagents/workflows/wf_fa42eb2d-e07/journal.jsonl`) after the session
> scratchpad holding the original prose note was wiped — the exact landmine
> `p3-kickoff-handoff.md` warned about. The structured findings below are the recon agent's own
> return value verbatim; the surrounding prose of the original note did not survive.
>
> Every `path:line` was opened against **`6d7ec9a`**, which is P3's base. P3 landed on top of it and
> moved things in `aelix_agents/`; the `rpc/` and kernel citations are untouched by P3 (its
> product-core and kernel deltas are both byte-empty). **Re-verify before relying on a line number.**

**Scope read:** `rpc/rpc_client.py` (602 lines), `rpc/rpc_mode.py` (2142), `rpc/rpc_types.py` (875), `rpc/_jsonl.py` (117), plus `tests/rpc/*`

## Summary

RpcClient is a complete, structurally long-lived asyncio JSONL client: start/_send/stop, id-correlated request futures, a chunked stdout reader task plus a concurrent stderr drain task, 100ms startup-death check, 30s send timeout, 60s idle timeout, SIGTERM→1s→SIGKILL. Server side is at full pi parity (29 supported / 0 deferred commands), and set_model exists end to end. Both channels emit the SAME events through the SAME `dataclasses.asdict` serializer, so the field map and `stream.reduce_line` work unmodified on rpc output — the parity test is feasible.

What is missing is the turn contract and the containment. Events carry NO id/sequence: `agent_end` is the sole terminator, and it is never emitted when a prompt task fails (rpc_mode.py:310-320, an unfulfilled Sprint-6f carry-forward) nor when `abort` cancels a turn — so prompt_and_wait hangs 60s on both. `prompt` acknowledges `success:true` before validating, so a busy-phase rejection is invisible. The rpc child never exits, so exit_code is always 0. No per-line budget, no `limit=`, no `start_new_session`, no pdeathsig, no kill_tree. And the default argv `-m aelix` (rpc_client.py:466) launches a MOCK-stream demo, not the product.

## Contradictions — committed docs vs the code

The highest-value entries: an accepted document asserting something the code does not do. Several of these are why this sprint was split out of P3.

- **Doc says:** Spec §8 preamble: 'The existing RpcClient test harness (tests/rpc/*, tests/pi_parity/test_phase_4_4_strict_superset.py) already drives real --mode rpc children and is the ready-made bed.'
  <br>**Code says:** All three tests/rpc files subclass RpcClient purely to REPLACE _build_argv with an inline `python -c` echo stub (lifecycle:99-100, timeout:41-42, shutdown:52-53). test_phase_4_4_strict_superset.py touches RpcClient only to assert four numeric constants (:316-319). The default argv at rpc_client.py:466 has ZERO test coverage. No test starts a real aelix rpc child, and no test sends two prompts on one client.
  <br>`tests/rpc/test_rpc_client_lifecycle.py:93-100`
- **Doc says:** Spec §2.5 line 151: 'long-lived→ ["--mode","rpc"]   # RpcClient, rpc_client.py' — i.e. use RpcClient's own launcher for the long-lived child.
  <br>**Code says:** rpc_client.py:466 builds `[sys.executable, '-m', 'aelix', '--mode', 'rpc']`, and src/aelix/__main__.py:131-147 shows what that is: _run_rpc() builds an AgentHarness with a MOCK stream_fn (_make_mock_stream_fn(), :143-145) — an echo demo, not the product. ADR-0197:505 and aelix_agents/print_channel.py:342-345 both already call it a live bug. `cli_path` is not an escape: rpc_client.py:464 splices it as a script path, not as `-m <module>`.
  <br>`src/aelix/__main__.py:142-147`
- **Doc says:** Spec lines 275 and 289: 'the runtime threads AELIX_SUBAGENT_DEPTH via child env (_build_env, rpc_client.py:474-477)'.
  <br>**Code says:** rpc_client._build_env (:474-477) is `dict(os.environ)` updated with options.env and nothing else — it does not and must not thread the depth var, because rpc_client.py is PRODUCT-CORE. P2 correctly put the depth threading in the extension at aelix_agents/print_channel.py:390-399. Implementing the spec literally would put spawn policy in product-core, violating ADR-0197 §1.2 (though the AST gate at test_p2_band_boundaries.py:150-180 would not catch it, since it only looks for spawn CALLS and rpc/rpc_client.py is on _SPAWN_ALLOWLIST at :41).
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:474-477`
- **Doc says:** Spec line 226: 'Long-lived rpc child (RpcChannel): RpcClient.stop() SIGTERM → 1 s → SIGKILL (the established rpc_client.py:158-204 contract). Settings: agents.rpc_kill_grace_ms=1000' — presented as directly comparable to the 5 s one-shot grace.
  <br>**Code says:** SHUTDOWN_SIGTERM_TIMEOUT_MS=1000 is only the SIGTERM→SIGKILL window (:181-188). stop() then waits a FURTHER bounded 5.0 s for the final reap at :192-193, so worst case is ~6 s. PrintChannel's DEFAULT_GRACE_SECONDS is 5.0 (reaper.py:80). The two numbers measure different segments and are not comparable as written.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:186-193`
- **Doc says:** Spec line 427: 'both flow through the single _build_harness_options build (entry.py:1366)'.
  <br>**Code says:** _build_harness_options is defined at cli/entry.py:793 and called at :1928. entry.py:1366 is `sessions_root = parsed.session_dir or get_session_dir()`. The underlying CLAIM is true and I verified its security-relevant half — entry.py:1435-1436 applies the `subagent_depth() > 0 → headless_default='block'` floor with no mode branch — but the citation is stale.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:793`
- **Doc says:** ADR-0197 §(i) blocker (a), lines 1173-1174: 'the child's stdin cannot carry it — cli/entry.py:1266-1267 → _read_piped_stdin → sys.stdin.read() (entry.py:208) reads to EOF, so an open pipe hangs the child forever'.
  <br>**Code says:** _read_piped_stdin is defined at entry.py:145 and called at entry.py:1347, guarded by `if app_mode in ('print', 'json')` at :1346. It is therefore a PRINT-MODE-ONLY property. On --mode rpc, entry.py skips it entirely and run_rpc_mode attaches its own connect_read_pipe reader (rpc_mode.py:1884-1889) plus a chunked pump (:2078-2092) — stdin is a live bidirectional JSONL channel. The citation is stale AND the conclusion does not generalise to the rpc child. (Out of P3 scope, but it materially lowers the cost of the deferred approval back-channel; blocker (b), the dropped extension_ui_response at rpc_mode.py:2048-2049, still stands and I verified it.)
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1346-1347`
- **Doc says:** rpc_mode.py:316-320 in-code note: 'Pi parity (rpc-mode.ts:379-401) also emits a synthetic terminal event so the client's wait_for_idle listener unblocks... Sprint 6f wires that bridge per ADR-0058 carry-forward.'
  <br>**Code says:** Sprint 6f never wired it. `grep 'AgentEndEvent('` over the kernel returns exactly four construction sites — loop.py:221, loop.py:260, loop.py:271 and harness/core.py:4310 — none of which is reachable from rpc_mode. A prompt task that raises therefore prints to stderr and emits nothing, so RpcClient.prompt_and_wait blocks for its full 60 s default.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:310-320`
- **Doc says:** ADR-0198 'Partial fulfilment' (:238-247) frames the remaining P3 work as 'the rpc half of the envelope contract' plus 'the cross-channel parity test', implying the rpc envelope is a separate but analogous shape.
  <br>**Code says:** The rpc EVENT envelope is not a separate shape at all — print_mode._event_to_dict (:59-68) explicitly imports and delegates to rpc_mode._dataclass_to_dict (:258-268), a bare dataclasses.asdict, and both modes subscribe the same harness stream. The genuine divergences are elsewhere: interleaved {'type':'response'} command envelopes (rpc_types.py:477-483), the absent session header, exit_code always 0 because the rpc child never self-terminates (entry.py:2157-2167), and dropped_lines being unpopulatable (no per-line budget in _jsonl.py).
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:59-68`

## Findings (22)

- The spec's Phase 3 citation 'Reuses: rpc_client.py:425-450,158-204,267' is CORRECT on all three: 425-450 is prompt_and_wait exactly, 158-204 is stop() exactly, 267 is the `async def set_model` line exactly.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:425-450`
- RpcClient's concurrency model is pure asyncio, one process and three tasks: caller coroutine, stdout JSONL reader task, stderr drain task. Both pipes are pumped concurrently for the child's whole lifetime (satisfies ADR-0198 D6's deadlock rule).
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:134-139`
- The stdout reader uses chunked `await stream.read(4096)`, not readline() — so ADR-0198 D6's 64 KiB readline ceiling never fires on the rpc channel and needs no change.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:92-110`
- RpcClient is structurally long-lived and multi-request (three sequential get_state calls on one live client are tested), but no test anywhere sends two prompts on one client, and every existing call site overrides _build_argv to spawn a `python -c` echo stub.
  <br>`tests/rpc/test_rpc_client_lifecycle.py:93-100`
- There is NO request-id correlation on the event stream. `id` correlation exists only for command→response envelopes; session events are emitted as bare `dataclasses.asdict(event)` with no id, no turn number, no sequence. Turn completion is signalled solely by an event whose type == 'agent_end'.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1927-1943`
- A failed prompt emits NO terminal event, so prompt_and_wait blocks for its full 60 s. The code documents the gap itself and blames a Sprint 6f bridge that was never wired — `AgentEndEvent(` has exactly four construction sites, all in the kernel loop/harness, none reachable from rpc_mode.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:310-320`
- `_handle_prompt` is fire-and-forget: it returns RpcSuccessResponse BEFORE the turn begins, so a prompt rejected by AgentHarness as busy (phase != idle) is reported to the client as a success and then never terminates.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:322-330`
- `abort` produces no wire event and no agent_end. AbortHookEvent goes to `self._hooks.emit`, not to `self._listeners` (which subscribe() feeds), and the cancelled turn never reaches loop.py's emit(AgentEndEvent) because the harness swallows the CancelledError and returns [].
  <br>`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:4287-4294`
- set_model exists end to end: RpcClient.set_model → RpcCommandSetModel (model_id→modelId remap) → _handle_set_model, which searches the auth-filtered registry.get_available() and returns 'Model not found' or the camelCase Model dict. Requires a ModelRegistry; entry.py's production path supplies one, src/aelix/__main__.py:147 does not.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:606-647`
- The spec's '1 s rpc kill grace' understates stop() by 5 s: SHUTDOWN_SIGTERM_TIMEOUT_MS=1000 is only the SIGTERM→SIGKILL window; stop() then waits a further bounded 5.0 s for the final reap, so worst case is ~6 s (vs PrintChannel's DEFAULT_GRACE_SECONDS = 5.0).
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:186-193`
- Both channels emit byte-identical events from the identical serializer: print_mode._event_to_dict explicitly imports and delegates to rpc_mode._dataclass_to_dict, which is a bare dataclasses.asdict. Field spelling, discriminators and shapes therefore match, and stream.reduce_line works unmodified on rpc output.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:59-68`
- rpc mode redirects stdout→stderr (pi's takeOverStdout) while print mode deliberately declines it — so a stray extension/MCP print() cannot corrupt the rpc event stream but DOES pollute the stderr ring, which is envelope._select_summary's fallback rung. The trap is inverted, not removed.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:1847-1849`
- An rpc child never exits on its own — run_rpc_mode blocks on shutdown_event/stdin_eof and entry.py unconditionally returns 0 afterwards. So exit_code is always 0 and carries no information, making build_result's `exit_code != 0` failure clause dead on this channel and leaving agent_end as the sole terminator.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:2157-2167`
- The rpc read path has no per-line budget (JsonlLineReader's buffer grows unbounded) and create_subprocess_exec passes no limit=, whereas PrintChannel has MAX_LINE_BYTES=4 MiB plus limit=8 MiB and a dropped_lines counter. SubagentResult.dropped_lines can therefore never be non-zero on the rpc side.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:61-73`
- RpcClient.start() omits start_new_session, preexec_fn=pdeathsig and limit= — so the rpc child joins the PARENT's process group (the exact hazard print_channel.py:806-810 documents) and run_rpc_mode installs handlers only for SIGTERM/SIGHUP, never SIGINT. stop() kills one process, not a tree.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:120-127`
- `_handle_prompt` silently drops cmd.images (it calls harness.prompt(cmd.message, source='rpc') only), while _handle_steer and _handle_follow_up both decode them via _decode_images. A live asymmetry.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:309`
- The security invariants carry to rpc for free: entry.py applies the `subagent_depth() > 0 → headless_default='block'` floor with no mode branch, and bind_ui is called only from tui/shell.py, so an rpc child's ctx.ui raises and has_ui is False — identical to a print child.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py:1435-1436`
- profile_to_argv already emits ['--mode','rpc'] for oneshot=False and correctly appends no positional task in that branch — so the RpcChannel argv can reuse it unchanged; only build_child_argv's hardcoded oneshot=True needs a sibling.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/agents/resolver.py:200-206`
- The RPC server drops every extension_ui_response unconditionally and never sends an extension_ui_request (the nine request types are marked TYPES ONLY), confirming ADR-0197 §(i) blocker (b) verbatim.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:2045-2049`
- Command dispatch spawns one detached task per stdin line, so responses may be emitted out of request order; and a response arriving after a client-side timeout falls through to the EVENT listeners after printing a 'stale response' line to the parent's stderr.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:519-536`
- rpc mode never emits a session-metadata header, whereas print mode emits a typeless header gated on `session is not None` — and rpc stdout additionally interleaves {'type':'response',...} envelopes with the events, which reduce_line ignores but a sequence assertion must filter.
  <br>`packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py:171-183`
- There is no channel abstraction: _SubagentRuntimeImpl.channel is typed as the concrete PrintChannel and is constructed as such in the extension, so a second channel cannot be selected without changing that type.
  <br>`packages/aelix-coding-agent/src/aelix_agents/runtime.py:220`

## Gaps — what this sprint must build or change (16)

| What | Where | Kind |
|---|---|---|
| Create `aelix_agents/rpc_channel.py` with an `RpcChannel` whose `run(plan, *, child, on_stream) -> SubagentResult` mirrors PrintChannel.run, reusing RunningChild/SpawnPlan, reduce_line, build_result and apply_cost_fallback so the envelope is produced by the same code on both channels. | `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py` | new-file |
| Subclass RpcClient inside aelix_agents and override `_build_argv` to `[sys.executable, '-m', 'aelix_coding_agent', *profile_to_argv(profile, prompt_path=..., oneshot=False), '--permission-mode', mode, *child_trust_argv(...), '--no-agents']`. The default `-m aelix` argv is unusable (it launches a mock-stream demo) and `cli_path` is spliced as a script path, not as `-m <module>`, so it is not an escape either. | `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py (vs packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:454-472)` | new-file |
| Write an rpc env builder in aelix_agents carrying build_child_env's four amendments (AELIX_SUBAGENT_DEPTH, AELIX_MCP_CONFIG pop, PYTHONPATH repair) and RE-REASON `AELIX_STDIN_TIMEOUT=1`, which is wrong for rpc because stdin IS the transport. It must NOT go into rpc_client._build_env — that is product-core. | `packages/aelix-coding-agent/src/aelix_agents/rpc_channel.py (vs print_channel.py:388-420)` | new-file |
| Decide and implement the authoritative turn terminator. agent_end alone is provably insufficient: a failed prompt, an aborted turn, and a busy-phase rejection all produce no terminator and hang prompt_and_wait for 60 s. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:310-320` | modification |
| Emit a synthetic terminal event when the fire-and-forget prompt task raises. This is the unfulfilled ADR-0058/Sprint-6f carry-forward the code itself documents (pi parity rpc-mode.ts:379-401). Product-core edit, but not spawn behaviour. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:310-320` | modification |
| Emit a wire-visible terminator after harness.abort() so an automated parent learns the turn ended. The harness half (core.py:4287-4294 returning [] on CancelledError) is KERNEL and forbidden, so a server-side synthetic event in rpc_mode is the only in-band fix. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:333-340` | modification |
| Introduce a channel Protocol (or union) so `_SubagentRuntimeImpl.channel` and `AgentsExtension.channel` can hold either PrintChannel or RpcChannel. Both are currently typed as the concrete PrintChannel. | `packages/aelix-coding-agent/src/aelix_agents/runtime.py:220 (and extension.py:161,188)` | modification |
| Relax `_reject_unsupported`, which currently raises ValueError for any mode != 'single' and for background=True. This is where P3 items 1 and 2 (parallel/chain, semaphore, MAX_PARALLEL_TASKS, over-limit errors) land. | `packages/aelix-coding-agent/src/aelix_agents/runtime.py:540-546` | modification |
| Give the rpc read path a per-line byte budget and a dropped-lines counter, or explicitly document SubagentResult.dropped_lines as print-only. JsonlLineReader's buffer is unbounded and create_subprocess_exec passes no limit=. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/_jsonl.py:61-73` | modification |
| Add process containment to the rpc child: start_new_session=True, preexec_fn=pdeathsig, an explicit limit=, kill_tree + descendant_pids on stop, and a post-exit pipe-holder drain. RpcClient.start() hardcodes its create_subprocess_exec call with no seam, so this is either a product-core edit or a full start() override in the extension subclass. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_client.py:120-127` | modification |
| Fix `_handle_prompt` dropping cmd.images while steer/follow_up decode them. One line. | `packages/aelix-coding-agent/src/aelix_coding_agent/rpc/rpc_mode.py:309` | modification |
| Add a sibling of build_child_argv for the long-lived channel; the existing one hardcodes oneshot=True. | `packages/aelix-coding-agent/src/aelix_agents/print_channel.py:366-368` | modification |
| Write the cross-channel parity test (ADR-0198's named P3 gate). It must normalise the four real divergences: interleaved {'type':'response'} envelopes, the absent header, exit_code always 0 on rpc, and dropped_lines being unpopulatable on rpc. | `tests/agents/test_cross_channel_parity.py` | new-file |
| Build a real-child rpc test bed. The spec claims one already exists; it does not — every tests/rpc client subclasses RpcClient to replace _build_argv with a `python -c` stub, and the default argv has zero coverage. | `tests/rpc/` | test-only |
| Add multi-turn RpcClient coverage (two prompts on one live client), which does not exist anywhere today and is the precondition for chain mode over the rpc channel. | `tests/rpc/test_rpc_client_lifecycle.py` | test-only |
| Pin that stop()'s worst case is 1 s SIGTERM grace PLUS a 5 s final reap, so the '1 s rpc kill grace' claim stops being re-derived from the constant alone. | `tests/rpc/test_rpc_client_shutdown.py` | test-only |

## Open questions the plan author must settle

1. Which terminator does RpcChannel treat as authoritative? agent_end alone is provably insufficient (abort, busy-rejection, non-AgentHarnessError escapes all produce none). Options: (a) fix the server at rpc_mode.py:310-320 and :333-340; (b) poll get_state().is_streaming, which rpc_mode.py:411 sets from `harness.phase != 'idle'`; (c) a channel-side deadline only. Only (a) is honest.
2. Does the owner accept ANY edit to product-core's rpc_client.py / rpc_mode.py? Three needed fixes live there (process containment at rpc_client.py:120-127, the synthetic terminal event at rpc_mode.py:310-320, the abort terminator at :333-340). Note rpc/rpc_client.py IS explicitly allowlisted for spawning at tests/agents/test_p2_band_boundaries.py:41, so the machine gate stays green — but the task brief's 'product-core gains ZERO spawn behaviour' rule argues the other way. This is the biggest scoping fork in the lane.
3. Is an rpc child even the right shape for a subagent? It never exits (entry.py:2157-2167 returns 0 unconditionally after run_rpc_mode), so the parent owns its entire lifetime and exit_code carries no information. The parity test must define what 'the same task' means across a channel that terminates and one that does not.
4. What replaces AELIX_STDIN_TIMEOUT=1 (print_channel.py:405) for the rpc child? The print-mode reasoning inverts completely — there stdin is a hazard, here it is the transport.
5. dropped_lines under rpc: add a per-line budget to the rpc read path, or document SubagentResult.dropped_lines as print-only? Either way the parity test must assert the chosen answer rather than leave the field silently always-zero.
6. STDERR_MAX_BYTES (10 MiB, rpc_client.py:94) vs STDERR_RING_BYTES (64 KiB, print_channel.py:120). The envelope fallback chain (envelope.py:112-146) and _build_details behave very differently at those two scales. Align them, or pin the difference deliberately.
7. Should the P3 parity test filter the interleaved {'type':'response'} envelopes out of the rpc stream before comparing, or should RpcChannel route responses and events onto two separate taps at the channel boundary? The client already splits them at rpc_client.py:508-536, but a response with an untracked id deliberately falls through to the EVENT listeners.
8. Does chain mode need `steer`/`follow_up` (which DO decode images and DO enqueue while a turn is live) rather than `prompt` (which rejects on busy and drops images)? That choice changes the whole turn-boundary design for {previous} substitution.
