# #395 author handoff — file preview runner required (2026-10-12)

## 기준점과 범위

- Author tree: `/Users/handochan/dev/aelix-ai-worktrees/codex-395`.
- Branch: `fix/395-file-preview-required`; exact author base/predecessor:
  `858bc06ce40eaf2668103c065c0fae4fea0a6008` (#390 source-reviewed predecessor).
  The coordinator owns its pending documentation amendment and any final-parent rebase.
- Authorization: approved beta.3 ordered continuation, 2026-10-12. #395 only;
  no merge authorization. Trusted-owner/open-issue/no-competing-PR/Project In progress
  preflight was supplied by the coordinator, not independently refreshed by this lane.
- Original AGENTS.md and CLAUDE.md, issue-to-pr skill and
  `/tmp/aelix-395-preflight-2026-10-11.md` were read before implementation.
- Runtime: own Python 3.12.13 environment (supported Python); no peer/original
  source, index, refs or environment mutated. No GitHub writes, provider calls,
  credentials, `.env`, owner Aelix auth or settings accessed by the author.

## 결정과 구현

Prompted factory-provenance built-in `write`/`edit` without `approval_runner` now
returns a blocking, actionable missing-preview reason before extension-tier
redirect resolution or generic `select`. The denial reads no target file, parses
no edit, asks for no reason, creates no session grant and changes no argument.
The public select protocol has no complete-preview/hold guarantee, so this uses
the approved narrow fail-closed choice; no protocol or dependency is added.

Wired `ApprovalRequest` runners keep Yes, session, No, cancellation, raising and
redirect behavior. Existing session authority, AUTO/AUTO_ACCEPT in-project writes,
YOLO, PLAN, read-only tools and parent/child headless floors remain as before.
Bash/unknown argument fallback remains whole; same-name third-party write/edit
remain unknown by object provenance. Old vulnerable write/select test pins now
use purpose-built runner controls or explicitly require select-only refusal.
Optional generic reason input remains covered with bash and file `NO_REASON`
gets a separate wired-runner denial row.

ADR-0253 §11.4 resolves the historical missing-content residual with a link from
the historical text. The extension guide and bundled copy explain embedding
integration; CHANGELOG records the intentional behavior change. Guide size is
49,061 bytes, leaving 2,139 under the 51,200-byte help limit for later #179/#416.

Pi latest official source and issues were re-read on 2026-10-12 before editing:
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/permission-gate.ts
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/extensions/types.ts
- https://github.com/earendil-works/pi/issues/9610
- https://github.com/earendil-works/pi/issues/9228
The example is bash-only; public select has signal/timeout, no preview contract.
Internal selector description and closed-as-not-planned issues do not establish
that a file preview reached the user.

## 실행한 검증과 출력

Logs are isolated under `.omc/probes/395-author/` (ignored local evidence).

1. Paired predecessor red before the fix; final 74-row test file was rerun against
   the exact `git show 858bc06c:.../permission.py` bytes with `try/finally` restoration:
   `.venv/bin/python -m pytest -p no:cacheprovider -q tests/builtin/test_permission_preview_required_395.py`
   → **50 failed, 24 passed in 0.55s**, `predecessor-red.log`.
   The 24 controls include all genuine runner verdicts and authorization policies.
   Refusal rows demonstrate the predecessor actually wrote/edited through the
   real loader → bound public select-only UI → harness hook → loop, not just a
   mismatching error string.
2. Same test command on repaired source → **74 passed in 0.22s**, `new-green.log`.
   Includes new/overwrite writes, short/decisive-long-tail edits, unparsed edit
   shapes at the permission hook, alias/tier redirects, file-IO observations via
   supported real factory operations, next-model refusal receipts, exact
   provenance-namespaced session grants and repeat once/session controls.
3. Focused final command:
   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -q \
     tests/builtin/test_permission.py \
     tests/builtin/test_permission_unknown_tools_188.py \
     tests/builtin/test_permission_extension_redirect.py \
     tests/builtin/test_permission_child_floor.py \
     tests/builtin/test_sensitive_aelix_dir.py \
     tests/builtin/test_permission_preview_required_395.py \
     tests/builtin/test_permission_shell_competence.py \
     tests/tui/test_approval_dialog.py tests/tui/test_select_title_held.py
   ```
   → **845 passed in 17.23s**, `focused.log`.
   An earlier optional filename typo aborted collection with 0 tests; corrected
   command above is the measured result (`focused-path-error.log` preserves it).
4. `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_docs_bundle_sync.py tests/test_citation_drift.py tests/test_guide_examples_are_valid.py tests/help/test_help_registry.py`
   → **63 passed in 3.67s**, `docs.log`.
5. `uv run --no-sync --python 3.12 ruff check .`
   → **All checks passed!**, `ruff.log`.
6. `uv run --no-sync --python 3.12 python scripts/check_types.py`
   → **288 files analysed, 0 errors; all 3 inverse assertions still error;
   PASS**, `types-canonical.log`.
7. `uv run --no-sync --python 3.12 pyright --pythonplatform Windows --outputjson`
   → **288 analysed, 0 errors, 1 existing SessionStorage covariance warning**,
   `types-windows.json`. This is Windows static modeling, not a Windows run.
8. `.venv/bin/python scripts/sync_bundled_docs.py`
   → **9 guides bundled, 1 updated, 0 removed**.
   `scripts/check_citations.py --fix` → **0 relocated, 547 locked anchors over
   934 gated citations; 0 out of range**; subsequent `--check` →
   **934 gated, none drifted**, `citations-fix.log` / `citations-check.log`.
9. Mechanical source proof, `source-proof.json`: the only changed product Python
   file is `builtin/permission.py`. Remove the one added `_prompt` refusal `If`
   and the entire parsed AST equals the predecessor AST. Both AST SHA256 values:
   `6430acbdb919a691adfd818640f8800253805fb6b704d4eae79501c4332a2deb`.
   Product source SHA256:
   `36bdb7e6272ae7ef80c3a25eb291ba118512e03965368bd82ed3066c86e72c47`.
   There is no other product Python or docstring/comment-only citation churn.
10. `git diff --check` → exit 0.

## 바로 시작할 것 — coordinator-only gates

The author lane is complete and source-frozen after its single commit. The
coordinator must verify the exact author receipt SHA and clean state, rebase onto
any final #390 documentation parent without changing the source contract, and
obtain independent adversarial verification/cross-review. Stop automatic repair
after two failed cycles of the same cause; no author self-approval substitutes.

Not run by this lane: full pytest suite, live real-model SDK/embedding calls,
actual TUI inspection, actual Windows/CI, PR creation or required-check state,
issue/project closeout and merge. These are coordinator-owned before completion.
Live changed-path evidence needs a real-model bound select-only embedding with
real permission loader/hook/loop: built-in write/edit are refused, select is not
called, files remain absent/unchanged and the model receives the refusal. Also
exercise the already-wired TUI's full write/edit/redirect approval and rejection
at 80×24 and 120×40 using isolated fixture files and isolated agent/settings dirs.
Do not use owner auth/settings; the coordinator owns the authorized credentials.

## 이후 순서와 실제로 물린 규칙

- One issue, isolated tree/atomic lock, one commit and its own Windows CI leg.
- Runtime change still requires direct real-model execution; scripted tests are
  model-free regression evidence. Independent review remains a separate pass.
- Full/CI measurements must match the final rebased PR head and parent ancestry.
- Update Project/issue/ADR documentation only after coordinator completion;
  merge remains explicitly human-controlled.

## 반증된 것 — 다시 믿지 말 것

- Sending a path or whole title proves a complete file preview: false.
- Concrete TUI #399 guarantees transfer to arbitrary bound select UIs: false.
- Name `write`/`edit` establishes built-in provenance: false.
- Fixing this fallback requires changing AUTO/YOLO/headless/session policy: false.
- A raising preview runner may retry weaker select: false; it denies.
- `tool_execution_start` proves execution: false; the loop emits it before the
  permission prep. Real factory file operations plus affirmative file effects
  distinguish blocked execution from a merely emitted lifecycle event.
- Passing a malformed SDK edit without `edits` reaches permission: false; schema
  validation rejects it first. SDK rows provide the required edit list, while
  raw shape refusal is covered directly at the permission hook.

## Coordinator current gates — 2026-10-12 KST

Author72ebfdc2 rebased onto final390 ac564708db5ef4bd421a5011fda1a8767400ec8d,
yielding0b8e2bd4402855e83a55befc29684d7aec974426 with no conflicts. The only
old/new tree difference was390's handoff; permission and all test blobs match.
Fresh independent verdict at0b8: no blocker,948unique passed(118own+830related).
Predecessor and single-if sabotage each70failed/48unchanged controls, identical
failed node IDs. Report .omc/probes/395-independent/review.md. Initial fixture
used an uninjected SDK client:53test executions(52+diagnostic1), at least1
confirmed unauthorized dummy-key response, actual external HTTP count unknown;
no successful model inference/realkey or ownercredentials used. Those failures
remain separate from final tests. The corrected3variants use explicit
MockTransport plus nonloopback socket/DNS tripwire. Do not claim all historical
fixture execution was network-free.

The minor stale approval_runner field comment was corrected in3b3adb4264b02ea45296f5a37515740c535ea67e.
Only comments changed from0b8; the entire Python AST and every other tracked
blob are identical, .omc/probes/395-final/comment-clarification-proof.json.

Actual real OpenRouter SDK/public-select-only host: write/edit each1 genuine
factory/provenancewrite, select0, previewrefusal tool result, owned file
absent/unchanged,agent_end,exit0,noSession/3scratchroots/key absence.
Actual normal uvTUI at80x24→120x40: full34line write preview holds earlyYes
before target exists; paging through finalWRITE395_LAST releases Yes. Write
then edit succeed on the owned file. A further write answered No preserves
that file. Coordinator directly inspected7 virtual-terminal PNGs for preview,
paging tail, full edit old/new and refusal. These are actual PTY captures,
not OS window screenshots. Source0b8 behavior is bound to3b by exact AST proof.
Commands/results: /tmp/aelix-395-root-2026-10-12-fixed-select-v1 and
/tmp/aelix-395-root-2026-10-12-normal-tui-v1, copied ownprobes at closeout.

Canonical frozen full at3b, uv run --no-sync pytest -p no:cacheprovider -q:
16286passed/25skip/104warnings/678.81s,exit0; wrapper684.40s,
ended19:19:51UTC; HEAD/index/source clean before and after.
Author related845, docs/help63, Ruff, types288/0+inverse3,
Windowsstatic288/0(existing covariancewarning1), citations934/no drift.
Guide49061B leaves2139B below unchanged51200 cap.

Strengthened fresh Claude/OpenRouter static lane remains separate/pending.
Sonnet quota and in-flight budget failures are no verdict, not product failures.
A cheaper official Haiku5.5 read/grep-only lane with explicit reasoningoff,
4096payload cap, exactGitblobs, lexical/symlink/untracked denials,
41scope+7actual-tool-boundary tests is being measured; its bounded final result
will be recorded in ownprobes/PR without changing product source.
Final amendment after this section changes this handoff only; all other
tracked blobs must match3b. PR base fix/390-literal-descriptors. Own finalhead16CI
still required, no merge approval. Author179 runs separately on3b and rebases
only after writer freeze onto this final handoff parent before final gates.
