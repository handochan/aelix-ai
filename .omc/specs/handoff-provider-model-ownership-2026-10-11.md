# #365 author handoff — 2026-10-11

**Current boundary: repair 2 preserves typed fatal closing classification; see the final section. The after-hook repair is on parent `1e9556c5e7e42cf70f48002a4830e70ff643269b`; see the final section. Earlier 815f/3bd1 measurements below are historical, not the current freeze verdict.**

## 기준점과 상태

- Own tree: `/Users/handochan/dev/aelix-ai-worktrees/codex-365`, branch
  `fix/365-provider-model-ownership`; base `3bd1f9def2e1b8121fd529e2f539baa876b02796` (#289).
- Lock/run assigned by root: issue 365, `beta3-security-365-2026-10-11`.
- This is the author implementation/focused-check boundary, not issue completion.
  Root owns independent review, full pytest, actual model/TUI, remote PR/CI/Project and merge.
- Author source is frozen in the single commit containing this handoff. Record its exact
  SHA with `git rev-parse HEAD`; a commit cannot embed its own SHA.
- Root reported #289 Windows CI fixture failures (Proactor's internal socketpair hit its
  tripwire). The predecessor's runtime source is unchanged; root/289 author are repairing
  its test fixture. Do not rebase/checkout/sync this tree during root measurements.
  Root will rebase the single #365 commit onto the final predecessor and revalidate the delta.

## 구현 계약

- Explicit registration maps replace the exact provider's builtin/JSON rows; {} owns zero,
  None augments. Models/config containers are copied at API queue and registry boundaries,
  Model.provider is projected to the registration namespace, and last full config wins.
- Launch, registry access and /model read the same owned rows. Own unanimous API/nonempty
  base URL backfill remains; bare ids do not invent owners and scoped exclusions stay excluded.
- Request auth checks current route plus Model.headers before auth, then checks generation
  after awaits. Metadata-only changes remain valid. Old headers cannot be erased merely by
  returning current auth headers: the adapters merge Model.headers again, so mismatch refuses.
- Configured extension key precedes env, with JSON key retained if absent. Provider headers
  merge, extension collisions win, and only auth_header=None inherits JSON; False clears it.
  Replaced JSON per-model headers are removed. Stored-owning/379 retry/.env routing remain.
- Child argv carries only a SHA-256 full-route digest plus the explicit-owner requirement.
  Validation occurs after setup bind before runtime key attachment, and remains sticky in
  every auth/summary request through unregister/None re-registration/hooks/rebuilds. Fresh
  spawn decisions do not copy an ancestor receipt. Ordinary builtin, explicit profile -e and
  unresolved child-only extension controls remain supported. No raw base URL/key/header
  enters the receipt.
- API.bind-contained load failure cannot publish old owned rows under a new config: load
  starts by pruning previous projected owner names union current explicit names. First and
  replacement registration failures keep explicit names known and their failed projection
  empty; None/unregister failure prunes old rows without adding a new generic Model denial.
- ADR-0262 partially supersedes 0249/0250/0251. Guides/bundled copies, README key order and
  CHANGELOG were updated. #416 pre-record auth and #434 dynamic catalog were not implemented.

## 실행 증거

Own toolchain: Python 3.12.13, uv all-packages/all-extras; pip 26.2.1, hatchling 1.32.4.
Commands below run from the own tree with `.venv/bin` tools. Test subprocess environment
removes secret/config variables while retaining HOME; fixtures use the repository's scratch
home/agent/settings helpers. No .env or owner auth/settings file was read, no actual key/model
request was used, and no external model request was transmitted.

- Focused pytest command is captured by the author tool call and selects model_registry,
  model argument, CLI routing/auth/late-provider/args, profile resolution, real print/RPC
  channels, extension API, bundle and citations: **1418 passed, 11 skipped, 2 warnings,
  138.91 s, exit 0**. Log: `/tmp/aelix-365-author-2026-10-11/focused-final.out`.
  New SDK/child wire records expose only host/path/model and known fake-key tags.
- `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/check_types.py`: **288 files,
  0 errors, all 3 inverse assertions still error**, exit 0. `types-final.out`.
- `.venv/bin/pyright --pythonpath "$PWD/.venv/bin/python" --pythonplatform Windows
  --outputjson`: **288 files, 0 errors, 1 existing covariance warning**, exit 0.
  `windows-types-explicit-python.json`. A prior invocation omitted explicit pythonpath and
  produced 193 missing-import/derived errors; this was interpreter discovery, not a source fix.
- `.venv/bin/ruff check .`: all checks passed, exit 0 (`ruff-final.out`). Only the three new
  test files were formatted; existing source was not broadly reformatted.
- `scripts/sync_bundled_docs.py`: four guides updated; bundle test passed in focused.
- `.venv/bin/python scripts/check_citations.py --fix`: 73 references relocated across 33
  files, 547 locked anchors. `--check`: **934 gated, zero drift; 18 generic anchors recorded**,
  exit 0. `citations-fix.out`/`citations-final.out`. Citation changes also reach the server and
  TUI comments; AST comparison ignoring inert doc expressions proves 29 Python files have
  only citation/doc changes. Six product Python files change behavior; six tests change/add
  behavior. Detail: `ast-scope.json`.
- `git diff --check`: exit 0.
- Earlier focused 703 passed; a later 447 selection had two expected child argv assertion
  failures (receipt option), repaired by asserting its digest as well. Intentional #363 tests
  were updated for catalog removal, configured extension priority and merged headers.

## 바로 시작할 것 / 이후 순서

1. Root records the author freeze SHA, runs fixed `registry_wire.py --fixed` against it,
   and compares root baseline vendor/stale endpoint/stale same-route header cases with no-send
   plus the fresh-current positive control.
2. Independent Codex/Claude review; root real CLI/TUI/model controls, full suite and exact-head
   remote checks. Author did not run full pytest, actual TUI/models, push, PR, CI or GH writes.
3. Repair review findings within #365 only. Preserve the batch order
   394 → 289 → 365 → 390 → 395 → 179 → 416, including the final #289 Windows fixture rebase.

## 반증된 것 / 다시 믿지 말 것

- Historical issue launch examples do not all reproduce today; #362 already guarded several.
  Root baseline SDK evidence proves the residual registry/current-Model request boundary.
- A provider/id-only child pin does not pin ownership: builtin names can reach a vendor when
  the child loads no owner. Startup-only receipt validation is also insufficient after hooks.
- Model frozen=True does not freeze nested headers/compat/input containers; copy input.
- A current auth-header map alone cannot remove stale Model.headers below the callback.
- Pruning only current explicit owner names misses A → None/unregister load failures; prune
  previous published owners too. None's intentional generic caller-Model contract still stands.
- Bare Windows pyright without own interpreter is not a measured Windows source verdict.
- Parent #289's green macOS full run does not imply its Proactor tripwire fixture is green.
- Fake-wire regressions and static Windows types do not establish actual-model/TUI behavior or
  physical Windows CI; those remain root gates.

## Current independent-review repair and freeze

- Root rebased the initial single commit onto #289 `1e9556c5e7e42cf70f48002a4830e70ff643269b`,
  yielding `41d5e54184c16d0434f6e7c343b42977566987ad`. Root's rebase proof reports unchanged
  #365 product/tests, with only inherited #289 fixture/handoff delta. The author repair starts
  from 41d and is an amend of the same #365 commit. Root/coordinator may later rebase it onto
  another predecessor test-fixture repair; author has not mixed that repair into this tree.
- Independent frozen 815f report:
  `/Users/handochan/dev/aelix-ai-worktrees/codex-review-365/.omc/probes/365-independent/review.md`.
  Its `test_independent.py` confirmed 2 real-harness and 4 actual print/RPC child failures:
  `api.unregister_provider('openai')` in before_provider_request or before_provider_payload
  ran after auth returned, yet SDK prepared a request. Earlier auth-boundary success did not
  cover this interval and was insufficient for approval.
- Repair: ResolvedRequestAuth optionally carries a pure validator of the same successful
  auth snapshot (load generation and copied Model route/header identity). CLI passes it in
  the auth dict. Core combines it with the live harness's model identity. AI stream dispatch
  validates before entering the stream and wraps on_payload to validate again after its await
  and before the built-in SDK call. Compaction main/prefix and branch summary forward it too.
  No core/AI import of ModelRegistry/CLI, no OAuth refresh/!command/key lookup for validation,
  and no TUI automatic model replacement. Closing failures are fatal/nonretryable; auth-phase
  #379 transient/fatal handling remains. Metadata-only changes and explicit stream-option
  header overrides remain valid. Validator-only auth retains native/env no-credential opinion;
  legacy callbacks without validators retain their prior None/dict behavior.
- None/unregister generic caller-Model behavior is explicitly outside the owned-model guard.
  Sticky child requirements still apply. Parent stale-currentModel spawn attestation was a
  separate reviewer observation, not a confirmed blocker or newly implemented policy.
- Exact independent original file was copied to author /tmp (review tree untouched):
  `.venv/bin/python -m pytest -c pyproject.toml -q
  /tmp/aelix-365-author-2026-10-11/test_independent_original.py
  -k 'removed_in_request_hook or real_delegated_child_request_hook'`:
  **6 passed / 14 deselected / 2.49 s / exit 0**.
  `independent-six-fixed-configured.out`. First /tmp invocation omitted repository pytest
  config and reported async unsupported in all six without executing bodies; original log
  preserved as `independent-six-fixed.out`, an environment setup failure, not product evidence.
- Own closing first selection: **75 passed / 10.82 s**. Expanded legacy/no-op/sync+async
  validators/#379 selection: **287 passed / 12.09 s**. Wider final focused command includes
  the previous 1418 boundary plus all providers, summary/hook/dispatch/protocol/legacy auth and
  #379 files: **2841 passed / 11 skipped / 2 warnings / 162.48 s / exit 0**.
  `closing-broad-focused.out`. Changed lifecycle/header/model requests send nothing, unchanged
  and metadata/header-override controls send once; auth getter count remains exactly one.
- Canonical type gate: **288 files / 0 errors / all 3 inverse errors preserved**;
  `closing-types-corrected.out`. A first OptionalCall diagnostic was repaired by a callable
  guard before invoking the generic validator. Windows static type model with explicit own
  Python: **288 files / 0 errors / 1 existing covariance warning / exit 0**;
  `closing-windows-types.json`.
- Full lint passes. Bundle sync updated three guides; post-core/AI line edit citation fix:
  **81 refs / 39 files**, 547 locked anchors; check **934 gated / no drift / 18 generic recorded**.
  `closing-ruff-final.out`, `closing-citations-fix.out`, `closing-citations-final.out`.
  AST boundary ignoring inert documentation expressions is in `closing-ast-scope.json`;
  changed reaper/prompt/service/TUI/server surfaces outside the explicit product list are
  documentation/citation-only. New executable lower-package changes are the generic core
  validator plumbing and AI dispatch wrapper, not provider/SDK architecture changes.
- Source/code checks ran in the own 365 venv/tree only. No actual key/model, external model
  request, owner .env/auth/settings access, full pytest, actual TUI/model live, GitHub write,
  push/PR/CI/merge. Root's earlier 41d live results are not the repaired-head live verdict.
  Root/coordinator own independent repaired-head review/full/live/CI and future parent rebase.

**Again disproved:** checking only at auth return does not cover sanctioned request/payload
hooks. Validators must remain tied to the original successful auth snapshot and survive all
those awaits; resolving auth again is not validation. A copied probe outside the repo needs
explicit pytest config to run async bodies. A parent test-fixture CI repair must not be inferred
from a green macOS full run or mixed into this issue's implementation.

## Current repair 2 — fatal closing classification

The first repaired candidate 64985 was still blocked. Fresh review original:
`/Users/handochan/dev/aelix-ai-worktrees/codex-review-365-closing/.omc/probes/365-closing-independent/test_fresh_closing.py`.
Both OpenAI adapters converted a generic validator's `synthetic 503 closing refusal` into
AssistantErrorEvent, after which the provider retry regex re-entered auth. A variant that
accepted the next auth snapshot actually prepared one SDK request. No external request or
actual credential was used. Read-only reviewer original was copied to own ignored probe
`.omc/probes/365-fatal-author/test_fresh_closing_original.py`; reviewer tree stayed untouched.

- Before repair, selected original three tests: **3 failed / 43 deselected / 0.57 s**
  (`fatal-closing-before-configured.out`). An earlier shallow /tmp copy failed collection on
  its ROOT parents[3] expression, not a product failure; `fatal-closing-before.out` retained.
- Minimum fix: closing `_validate_request` rejection sets the existing authoritative
  `_SetupFailure(message=None, retry_reason=None)` record before raising. The adapter's error
  event and final message_end replacement are bound to this record by the existing pipeline.
  Classification uses that identity and retry_reason, not text/type. The displayed refusal
  is unchanged; neither provider errors nor all retries are broadly disabled. New request
  attempts clear the record as before. No OAuth/key/command re-resolution was added.
- Same original three tests after repair: **3 passed / 43 deselected / 0.42 s**
  (`fatal-closing-independent-fixed.out`). Completion/responses × message_end replacement
  own four regressions: fatal/no retry/no SDK/getter once, then a new user turn sends on its
  fresh snapshot. The next-snapshot counterexample is blocked. Actual SDK 503 positive controls
  on both adapters retain two requests/auth lookups and one retry start/end. Original #379
  OAuth transient/fatal, no-auth/legacy/custom streams, metadata/header overrides, hook and
  summary forwarding remain in the selected regression suite.
- Related final focus: **344 passed / 15.06 s / exit 0**
  (`fatal-closing-final-corrected.out`). The earlier run had 343 pass/1 citation-gate failure:
  six repeated anchors could not be auto-relocated after the Core docstring grew. Original
  target statements were re-read and their actual new ranges (2225, 2402-2406, 2828) re-anchored
  explicitly. That was a documentation gate, not recurrence of closing retry. First own test
  collection also used the wrong result type name MessageEndResult; corrected to the real
  MessageEndEventResult and preserved first log. No failing fixture was counted as product proof.
- Canonical types: **288 files / 0 errors / inverse 3 maintained** (`fatal-closing-types.out`).
  Windows static model: **288 files / 0 errors / existing warning 1** (`fatal-closing-windows.json`).
  Full Ruff passes. Bundle/citations test passes in the 344 selection. Citation gate:
  **934 gated / zero drift / 18 generic recorded**; six manually moved references locked.
  `fatal-closing-citations-fix.out` (partial auto failure), `fatal-closing-citations-reanchored.out`,
  `fatal-closing-citations-corrected.out`. This repair's executable AST delta from 64985 is
  only harness/core.py and test_provider_ownership_wire_365.py; other Python diffs are citation
  or inert doc expressions. `fatal-closing-ast-scope.json` records the exact list.
- This is repair 2 per root/coordinator: 815f first finding cycle 0, 64985 first repair failed
  cycle 1, current repair 2. If the next repaired candidate fails for this same actual cause,
  do not continue blind automatic fixes; perform the issue-specific needs-human determination
  with root/coordinator. Routine collection/type/citation repairs are not such product cycles.
- Current parent remains 1e9556c5e7e42cf70f48002a4830e70ff643269b; no new #289 fixture changes
  were mixed in. Author amends the one #365 commit and freezes. Root/coordinator own repaired-
  head independent review, full, actual-model/TUI live, future parent rebase, remote/GH/merge.
  No full or live-model/TUI/remote work was done by the author. Previous candidate passes/live
  cannot substitute for the upcoming repaired-head verdict. Source/venv/index/refs freeze
  holds until explicit repair/rebase ownership is assigned again.
