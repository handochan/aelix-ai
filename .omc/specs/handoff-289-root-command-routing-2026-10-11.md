# #289 root-command routing — author handoff (2026-10-11)

## 기준점

- Worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-289`.
- Branch: `fix/289-root-command-routing`.
- Stacked base / parent: `8eaf4401b29261eca47a1120bfef7f7f779708b6` (#394 final head).
- Remote main read through GitHub API at handoff: `162bc5136517436fca365fd73d33ebee2eb32c3c`.
- Frozen implementation: the single commit containing this handoff. Its exact
  resulting SHA is delivered separately to root (a commit cannot contain its
  own hash). The staged source/docs/tests tree before adding this handoff was
  `d81b13e5ad5fee2e327a9add8de414165f0311b0`.
- Plan: root's accepted #289 decisions; `/tmp/aelix-289-preflight-2026-10-11.md`
  was a design candidate, not measured execution evidence.
- Python 3.12.13; own editable workspace venv; pip 26.2.1, hatchling 1.32.4.
- Original checkout's `CLAUDE.md` and `AGENTS.md` read, never copied or committed.

## 변경과 계약

- Shared lexical option arity in `cli/args.py` feeds both the existing flat
  parser and the pure root classifier. Required literal values, optional
  resume/list-models values, immediate print messages, `@file`, and a started
  ordinary prompt keep their prior consumption rules.
- Only exact leading `--offline` tokens (repeatable) may precede
  `extension`/`docs`/`status`. Accepted command argv is sliced separately,
  offline is exported before dispatch, and the original agent argv is retained.
- Other launch prefixes and unknown-long-option/command ambiguity exit 2 before
  parser/handler/runtime entry. Diagnostics show registered names or a generic
  unknown-option label, never values, arbitrary tokens, paths, prompts or keys.
- Bare `--` terminates both root classification and agent option parsing;
  subsequent messages and `@files` retain their lexical roles. This is the only
  intentional flat-parser grammar addition. Short clusters and known-option
  equals forms remain unsupported as agent options.
- Command-first extension raw `"--offline" in argv` export, including after its
  separator, is preserved (#288). Status still performs real trusted extension
  discovery; `--no-extensions` skips it. Status/docs postfix offline rejection
  and docs search's literal offline token remain unchanged.
- Main-sync stdio/truststore/dotenv/provider registration remains before async
  routing. No bootstrap, provider/MCP offline semantics, or internal child flag
  emission contract was changed.
- ADR-0261, related ADR amendments/index, READMEs, CHANGELOG, guides/help, and
  bundled guides move together.
- Every change in agents extension/profile/resolver/service, loader, TUI shell,
  server rpc_ws, project-trust guide, and the other existing tests is a citation
  number update only. No feature behavior was changed in those files. Eight
  edited parser anchors were re-derived from the actual thinking/tools/system
  prompt constructs; remaining 29 citation sites moved mechanically and were
  inspected. `_truncate.py` and `read.py` were not changed.

## 실제 명령과 출력

All commands ran in this owned worktree, with its own venv. No full suite,
live model, push, PR, Project mutation, merge, or user credential read ran here.

```text
uv sync --python 3.12 --all-packages --all-extras
  Using CPython 3.12.13; own .venv created; 78 packages installed.
uv pip install --python .venv/bin/python pip==26.2.1 hatchling==1.32.4
  Installed pip 26.2.1, hatchling 1.32.4 and build dependencies.

.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py
  Initial grammar/dispatch/subprocess pass: 154 passed in 7.40s.
.venv/bin/python -m pytest -q tests/cli/test_args.py tests/cli/test_entry_router.py tests/cli/test_docs_command.py tests/cli/test_extension_install.py tests/status/test_status_cli.py tests/util/test_offline.py
  506 passed in 5.89s.
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py tests/cli/test_args.py tests/cli/test_entry_router.py tests/cli/test_docs_command.py tests/cli/test_extension_install.py tests/status/test_status_cli.py tests/util/test_offline.py tests/agents/test_profile_parse.py tests/agents/test_profile_resolver.py tests/cli/test_permission_mode_flag.py tests/test_citation_drift.py tests/test_docs_bundle_sync.py tests/test_env_sandbox_windows.py tests/docs tests/packaging_gate/test_docs_bundle.py
  Final staged-input pass: 788 passed in 15.81s; root-routing module now 158 cases.

uv run --no-sync python scripts/check_types.py
  OK gate: 288 files analysed (floor 220)
  OK gate: 0 errors across 288 files
  OK spike: all 3 inverse assertions still error (narrowing alive)
  PASS: type gate clean, narrowing spike still inverted
uv run --no-sync pyright --pythonplatform Windows packages/aelix-coding-agent/src/aelix_coding_agent/cli/args.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/docs.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/status.py
  0 errors, 0 warnings, 0 informations

uv run --no-sync python scripts/check_citations.py --fix
  relocated 29 citation(s) across 16 file(s); 8 newly re-derived citations locked;
  547 anchors over 934 gated citations; 43 ungated, 0 out of range, 18 too generic.
uv run --no-sync python scripts/check_citations.py --check
  citations OK — 934 gated, none drifted (18 existing ambiguous anchors recorded).
uv run --no-sync python scripts/sync_bundled_docs.py
  9 guide(s) bundled — 0 updated, 0 removed (after the initial two guide copies).
.venv/bin/ruff check .
  All checks passed!
.venv/bin/ruff format --check packages/aelix-coding-agent/src/aelix_coding_agent/cli/args.py tests/cli/test_root_command_routing.py
  2 files already formatted
git diff --check; git diff --cached --check
  No output, exit 0.
```

The first direct `check_types.py` invocation failed because `.venv/bin` was not
on PATH; switching to `uv run --no-sync` fixed the gate environment. The first
Windows pyright invocation additionally exposed two Literal inference errors;
validated membership plus explicit typed casts fixed them. Final gates above
were rerun after the source changes. Citation check initially reported 37
drifted sites; manually repaired edited anchors and then `--fix`/`--check`
resolved them. No test failure was hidden or treated as green.

The real CLI subprocess matrix runs `python -m aelix_coding_agent` with
`tests.env_sandbox.child_env`, isolated cwd/agent/settings, socket/DNS and
provider/model/harness/session/loader tripwires, explicit/global extension
import sentinels, and a no-TUI import blocker. It covers valid offline commands,
literal docs search, invalid launch prefixes, and existing postfix rejection.
No network tripwire or extension sentinel fired and no session/auth file was
created. A separate in-process valid status test intentionally executed the
global extension sentinel and then proved `--no-extensions` suppressed it.

## 바로 시작할 것

Root performs independent review and boundary execution on the exact delivered
head, then full pytest and the actual-model positive `-p status` / `-p -- status`
checks in isolated state. Root owns GitHub/Project updates, push/PR and later
integration. Do not infer approval from author-focused green gates.

## 이후 순서

Approved security batch: #394 → #289 → #365 → #390 → #395 → #179 → #416.
This work contains #289 only. Recheck live main and predecessor ancestry before
integration; concurrent owner sessions can move main.

## 실제로 물린 규칙 / 반증된 것

- Stage new tests before ls-files-backed citation/HOME gates. Use relative repo
  scripts so they gate this worktree. `uv run --no-sync` avoids environment churn
  after the own venv is prepared. Formatting touched args/new-test only.
- No other tree/index/ref/venv, owner `.env`, keys, auth/settings, HOME, or
  CODEX_HOME was changed. Test child HOME values go through `child_env` only.
- Pi main at `ea448f454af474f3e2fe9733b331e17b0662f5d6` was read live: its raw
  offline export precedes package routing, package routing still checks first
  token, and args supports bare `--`. #9183's closed state is the automated
  new-contributor action, not a maintainer fix.
- `-p` is not a simple boolean; root words may be literal option values.
  Searching all argv for a verb is wrong. Status may execute trusted extensions.
  Offline does not block the configured provider or MCP endpoint.
- A new ADR need not have a table row to be indexed: current ADR-0260 uses an
  Index list entry, so ADR-0261 follows that existing format.
- Actual Windows execution and actual model transport are unrun in the author
  lane; Windows pyright is only static platform evidence.


## Root verification pass — frozen source 7f577d42

- Independent fresh Codex, source-first detached review: no blocker. 400 focused pytest passed,29,575 raw-parent parser differential vectors and300 classifier vectors passed,50 actual CLI subprocess privacy/trust/offline-tail/intentional-agent cases passed. Citation934/Ruff passed; no full/model/Windows/TUI claim by this lane. Report codex-review-289/.omc/probes/289-independent/review.md.
- Fresh Claude restricted static review: no blocker; no commands/tests/live. Newdiagnostics fixedknownnames/genericunknownplaceholder containno values/keys/controlpayload. Nonblockers: known-option equals errorlabelUX, inheritedungatedstalecommentcitation, documented synchronousbootstrap beforeasync. .omc/probes/289-claude-review/.
- Root actual standaloneCLI comparedbase8eaf withfixed7f:9cases. Baselinewrongprefix invokesharness/SDKrequest or-e importmarker; HTTPXpreparedrequests answeredsynthetic401beforeactualnetwork. Fixedofflinecommands0/badprefix2; no harness/HTTPX/marker/sessionfiles. Postfixstatusstill2/literaldocssearch0. .omc/probes/289-root/.
- Root actualOpenRouter/openai/gpt-4o-mini: -p status / -p -- status both userpayloadstatus, echo-systeminstructionpresent, exit0/nonemptycompletedresponse/no-session. Scratchagent/settings/cwd, dotenvonekeyinprocessonly, no owner~/.aelix/auth/settingsread/write, noHOMEoverride. Firstmodelassertion demandedingliteralresponse wasincorrect; actualpayload isroutingcriterion. v1preserved/v2rerunpassed.
- Fullattempt1 source7f:15807passed,25skipped,104warnings and1failed in685.87s(exit1). Rootallowlistparentenvhad15vars; existingMCPleakcontrolrequires>30. Thiswasverificationenvironmentfailure; no sourcefix. Preserve .omc/probes/289-final/attempt1-environment-*.
- Rootrunnercorrected toremovecredentials/provider/config controls while retaining58realnon-secretambientmetadata, HOMEunchanged/no fabricatedpadding. FailedMCPfile6passed0.29s. Canonicaluvrun fullattempt2 on SAMEsource7f: `uv run --no-sync pytest -p no:cacheprovider -q` → **15808 passed,25 skipped,104 warnings in641.61s,exit0**. No source/venv/index/rebasechangesduringrun. Logs .omc/probes/289-final/full-suite-py312.out andfull-command.json.

Finalamendrecordsonlythishandoff. Source/testsidenticaltofull/review/livehead7f; source-freeze-proof.json recordsfinalhead/diff. Nextfinalheadpush/PRstackedon394/own8jobsperpush+PRevent. Integration remains394→289→365→390→395→179→416, newbatchmergeapprovalstillrequired.

## Windows CI fixture correction (2026-10-11, after PR #440)

The preceding root verification record applies to the earlier source/test
snapshot. Before this repair, HEAD was
`3bd1f9def2e1b8121fd529e2f539baa876b02796`, with the same product source as
the reviewed/live/full-suite source `7f577d42`.

Both CI events (push 38125116937, PR 38125119993) failed the 12 new real-CLI
subprocess cases on Windows Python 3.11/3.12/3.13. Every process exited before
async CLI routing. The injected test fixture replaced all socket connects with
a failing tripwire, but Windows' Proactor creates its self-pipe using
`socket.socketpair`, whose stdlib fallback connects a temporary loopback TCP
pair. The reported missing `_ssock` is the later cleanup symptom of that
failed construction, not a product-router exception.

Diagnosis checked the [CPython socket fallback](https://github.com/python/cpython/blob/59d1bd5f0beb95f26771a96fe11eac1653d69ffa/Lib/socket.py)
and [Proactor self-pipe](https://github.com/python/cpython/blob/59d1bd5f0beb95f26771a96fe11eac1653d69ffa/Lib/asyncio/proactor_events.py)
at the current 3.11 reference. The original fixture was also reproduced locally
against the real `socket._fallback_socketpair`: its connect tripwire raised
before a pair could be returned. CI failure snapshots remain in
`/tmp/aelix-ci-289-2026-10-11/`, including
`push-windows-py3.11-job-114427759372.log`.

Only `tests/cli/test_root_command_routing.py` and this handoff changed. The
injected guard captures the original socketpair and original connect,
connect_ex, and getaddrinfo methods. A thread-local active flag allows these
originals only while that captured socketpair call is creating its pair, and
`finally` restores the previous flag even when creation fails. Other loopback
requests and provider-shaped DNS requests remain forbidden; another thread
does not inherit the permission. gethostbyname remains unconditionally blocked.
No broad address allowlist, event-loop-policy replacement, or product change
was introduced. Actor/model/harness/session/loader tripwires, no-TUI guard,
sentinels, privacy assertions and original argv cases remain.

All 12 CLI cases now run with the normal stdlib socketpair and a forced real
stdlib TCP fallback on every platform (24 cases). Four direct checks of the
same injected guard cover native/fallback pair communication, another thread's
blocked requests during creation, and guard restoration after creation fails.
The fallback check measures exactly one real connect in the creating thread;
ordinary connect/connect_ex and DNS calls fail before and after creation.

Author commands and results, all in this owned worktree/venv:

```text
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py -k 'real_cli_root_commands or network_tripwire'
  Initial repair: 16 passed, 146 deselected in 5.12s.
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py tests/cli/test_entry_router.py tests/cli/test_docs_command.py tests/status/test_status_cli.py tests/util/test_offline.py tests/test_citation_drift.py tests/test_env_sandbox_windows.py
  Expanded native/fallback matrix and related gates: 368 passed in 17.61s.
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py
  Final staged fixture after Ruff's blank-line-only correction: 174 passed in 9.69s.
uv run --no-sync python scripts/check_types.py
  288 files, 0 errors; all 3 inverse assertions fire; PASS.
uv run --no-sync pyright --pythonplatform Windows packages/aelix-coding-agent/src/aelix_coding_agent/cli/args.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/docs.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/status.py
  0 errors, 0 warnings, 0 informations.
.venv/bin/ruff check .
  All checks passed (an initial I001 blank line before the new constant was fixed).
.venv/bin/ruff format --check tests/cli/test_root_command_routing.py
  1 file already formatted.
uv run --no-sync python scripts/check_citations.py --check
  934 gated, none drifted; no citation relocation required.
git diff --check; git diff --cached --check
  Empty, exit 0.
```

The first AST comparison accidentally used system Python 3.9, which cannot
parse product match statements. It stopped before creating its proof/log
directory; the following redirected test command consequently did not start.
Rerunning with the owned Python 3.12 venv produced the proof and final test log.

`.omc/probes/289-windows-fixture/author-source-proof.json` compares all 277
product Python files against pre-repair head 3bd: zero byte or AST differences.
The entire packages/src/scripts diff is empty. The test SHA-256 is
`e28ef7345178585f9c25ede6131944e0c9b85a158b06f6afbee11de43949bb35`.
Final module output is preserved in the same directory's `final-root-routing.out`.

Root must review this test-only delta and obtain the fresh exact-head Windows
CI legs; author has not executed Windows runtime or rerun full pytest after
this repair, and has not pushed or mutated GitHub. Root owns any full rerun and
PR update. The separate existing bash containment timeout failure is not mixed
into #289; its source/test was left untouched. #365's separate worktree and
refs/index/venv were not accessed or changed.

## Public socketpair fixture portability repair (2026-10-11)

This is the second fixture candidate after the original CI finding. The first
repair candidate `1e9556c5e7e42cf70f48002a4830e70ff643269b` failed CI: Ubuntu
Python 3.12's installed socket module has no `_fallback_socketpair` attribute.
That premise failed independently of the original 3bd Windows Proactor
bootstrap guard problem. Twelve forced-fallback CLI cases and the one direct
fallback case failed before their fixture was established, while native cases
did not depend on that attribute. The earlier statement that forcing that
private helper worked on every supported build was false.

Failure evidence retained outside this tree:

- PR run 38128329144, job 114437251990:
  `/tmp/aelix-ci-289-repair-2026-10-11/pr-ubuntu-py3.12-job-114437251990.log`;
  13 failed, 15800 passed, 36 skipped, 104 warnings in 895.67s, exit 1.
- Push run 38128326339, job 114437243338:
  `/tmp/aelix-ci-289-repair-2026-10-11/push-ubuntu-py3.12-job-114437243338.log`;
  13 failed, 15800 passed, 36 skipped, 104 warnings in 972.68s, exit 1.
- Exact-head/job/failure extraction:
  `/tmp/aelix-ci-289-repair-2026-10-11/first-failure-evidence.json`.

The test now defines its controlled TCP-pair producer entirely through public
socket APIs: create an IPv4 stream listener, bind loopback with an OS-assigned
ephemeral port, listen, connect a client to that listener, accept its server
peer, validate both endpoint identities, restore blocking mode, and close the
listener. Construction errors close either returned-end candidate before they
escape. Operations have bounded timeouts. This is a real connected TCP pair
that models the Windows self-pipe shape; it does not replace the event-loop
policy or fabricate a pair. It deliberately supports only the no-argument or
IPv4 TCP calls used in this controlled bootstrap probe.

All original CLI arguments still run in native and forced-TCP bootstrap
subprocesses. Forced-TCP setup removes the private helper if present, so its
absence is exercised on every platform without an availability skip. The four
direct guard checks retain native, TCP, other-thread, and creation-failure
coverage; the latter two now create their successful pair through the same
public producer and assert exactly one connect in the creating thread.
The network guard's source is unchanged: thread-local permission only inside
the captured pair creation, finally restoration, and no address/port allowlist.
Ordinary local/external connect/connect_ex and DNS negatives, actor/model/
harness/loader/session tripwires, no-TUI checks, privacy assertions and import
sentinels remain. No package, product source, or existing bash test changed.

Author verification on staged input in the owned worktree:

```text
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py -k 'real_cli_root_commands or network_tripwire'
  28 passed, 146 deselected in 9.34s.
.venv/bin/python -m pytest -q tests/cli/test_root_command_routing.py tests/cli/test_entry_router.py tests/cli/test_docs_command.py tests/status/test_status_cli.py tests/util/test_offline.py tests/test_citation_drift.py tests/test_env_sandbox_windows.py
  368 passed in 17.40s (includes all 174 root-routing cases).
uv run --no-sync python scripts/check_types.py
  288 files, 0 errors, all 3 inverse assertions fire; PASS.
uv run --no-sync pyright --pythonplatform Windows packages/aelix-coding-agent/src/aelix_coding_agent/cli/args.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/docs.py packages/aelix-coding-agent/src/aelix_coding_agent/cli/status.py
  0 errors, 0 warnings, 0 informations.
.venv/bin/ruff check .
  All checks passed!
.venv/bin/ruff format --check tests/cli/test_root_command_routing.py
  1 file already formatted.
uv run --no-sync python scripts/check_citations.py --check
  934 gated, none drifted; existing 18 ambiguous anchors remain recorded.
git diff --check; git diff --cached --check
  Empty, exit 0.
```

`.omc/probes/289-public-socketpair-fixture/author-source-proof.json` compares
277 product Python files against 1e: zero byte or AST differences and an empty
packages/src/scripts diff. The test SHA-256 is
`7cf118b53e8770d00b88b1543452a280cabda3fa55ccd79ffec709d90d3a2c72`.
Only this handoff and the root-routing test are amended. The exact resulting
head is delivered to root/coordinator and recorded in the local proof after
amend; a commit cannot contain its own hash.

No full suite, actual Windows runtime, model, remote/GitHub operation, rebase,
or other worktree/index/ref/venv access ran in this repair. Coordinator owns
fresh independent review, exact-head full/CI/PR verification, and the new #289
parent for #365. The issue-to-PR skill's repeated-failure rule was re-read:
first failed repair cycle 1e is recorded, and a second failed repair cycle
must be reported for root/coordinator's issue-specific needs-human decision.
Do not begin a blind third automatic repair.

## Coordinator final verification — public TCP fixture (2026-10-11)

Frozen measured source/test head: `04f9467000a72f6920e9671547e9c4fb2c43e8ec`.
The final amendment adds only this verification record; all other tracked blobs
must remain identical, recorded by `final-source-freeze-proof.json`.

- Fresh independent source-first Codex review: PASS, no blocker. Final valid
  tests were **748 passed, 0 skipped** (220 root/independent probes in 19.55s;
  528 adjacent cases in 5.04s). The private-helper-absent parent counterfactual
  intentionally failed all 13 affected cases. The fixed native/TCP child matrix
  actually created pairs with the private helper removed. Independent probes
  checked public signatures, 14 real-FD construction faults, peer validation,
  unrelated connect/DNS/thread denial and scope restoration. Reviewer invocation
  environment failures are preserved and excluded from product claims.
  Report: `codex-review-289-public/.omc/probes/289-public-independent/report.md`.
- Coordinator canonical full command, from this tree with its own prepared
  venv: `uv run --no-sync pytest -p no:cacheprovider -q`.
  **15824 passed, 25 skipped, 104 warnings in 674.56s, exit 0**;
  wall-clock 679.85s, 10:03:00–10:14:19 UTC. HEAD stayed 04f94670 and tracked
  source/index stayed clean. The parent retained 58 real benign ambient metadata
  variables with provider/credential/configuration controls removed; HOME was
  unchanged. Source, venv and index were not mutated during the full run.
  Logs: `.omc/probes/289-final/full-command.json` and
  `.omc/probes/289-final/full-suite-py312.out`. The preceding completed 1e full
  was archived under `full-attempts/` before this run; no failed log was deleted.
- Completed preceding 1e CI is **12 successes / 4 failures** across push
  38128326339 and PR 38128329144. Each event failed Ubuntu Python 3.12 and
  Windows Python 3.11 on the same absent private helper (13 fixture cases).
  No bash containment failure occurred in these four jobs. This is one failed
  repair cycle, not four cycles. Earlier 3bd Proactor and bash-premise evidence
  stays separate. Evidence: `/tmp/aelix-ci-289-repair-2026-10-11/`.
- Product source is still byte/AST-identical to the prior independently reviewed
  and actual-model-tested parser candidate. This repair changes the test fixture
  only, so prior actual CLI/model routing evidence remains bounded by that proof.
  Physical Windows execution on the **new final head** is still pending CI.

Next: exact-old-1e feature lease push, PR #440 update and fresh final-head
push/PR CI (eight jobs each), then the #365 test-only-parent rebase. No main
force, checkout synchronization, or merge was performed. New batch merge
approval remains with the user after all seven PRs are reviewable.
