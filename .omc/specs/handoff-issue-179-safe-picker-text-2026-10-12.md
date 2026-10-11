# #179 — caller picker text before owned styling

작성일: 2026-10-12. Author 구현/집중 검증 기록이며, 독립 리뷰·전체 스위트·실제 CLI TUI·최종 CI 완료 기록이 아니다.

## 기준점과 승인 범위

- 전용 worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-179`; branch `fix/179-safe-picker-text`.
- 측정한 순차 parent: `3b3adb4264b02ea45296f5a37515740c535ea67e` (#395까지의 구현 base). Root가 보유한 #179 atomic lock 안에서 author 단독 작업.
- Root가 전달한 original seven-issue 순서와 2026-10-12 “승인합니다. 이어서 진행하세요” 승인 안의 #179 구현. Merge 승인은 없음. GitHub writes, 다른 tree/venv/ref, 실제 모델/owner 자격 증명·설정에는 접근하지 않았다.
- 원본 `AGENTS.md`/`CLAUDE.md`, `aelix-issue-to-pr` skill, `/tmp/aelix-179-preflight-2026-10-11.md`를 읽었다. 현재 #179 read-only 조회: OPEN, trusted author `handochan`, labels `bug`, `area/tui`; root가 Project In progress와 경쟁 PR 없음 확인을 맡았다.
- 공식 Pi 최신 main `cd8bdf7518c0f9a381deef49bff95163bff127d8`을 read-only 조회했다. SelectItem의 value/label/description와 theme 분리, utils의 visibleWidth/stripTerminalSequences, 최신 control-character 관련 issues를 읽었다. Pi의 renderer/single-line 정규화를 Aelix 계약으로 옮기지 않았다(ADR-0235).

## 구현과 문서 정본

- `select` option/detail, `tabbed` name/body/exception text를 `_picker_payload`에서 먼저 이름으로 바꾼다. approval prompt의 `_shown`/`_ansi_named`를 재사용해 caret/Unicode 이름에 reverse style을 준다. Newline은 행으로 유지하고 CJK/combining/ZWJ를 보존한다.
- 표시된 안전한 문자열을 width와 filter에서 함께 쓴다. `_visible_len`은 owned SGR을 제외한 가장 넓은 newline 행을 shared cluster measure로 잰다. Own select도 실제 prefix가 있는 행만 측정하므로 여러 줄 label의 후속 행에 2칸을 허위로 더하지 않는다.
- 원본 option/index는 따로 보존한다. Enter는 원본 문자열 객체, detail은 원본 index, `cancel_options`는 원본 identity로 처리한다. Display collision이나 duplicate label이 이를 바꾸지 않는다.
- 내부 stats dim note는 private `_PickerText`의 structured fragments로 옮겼다. 그 text도 이름 처리한다. Raw ANSI string과 `own=True`는 trust switch가 아니며 public protocol/dependency를 늘리지 않는다.
- #399 title naming/wrap, first-paint/all-seen hold, cancel/Esc/Ctrl+C/resize/own namespace를 유지한다. #424 viewport, multiselect preview, auth/model semantics는 이 구현 범위 밖이다.
- 정본: ADR-0227 #179 amendment. ADR-0154/0163/0253의 필요한 부분만 수정했다. ADR-0227의 일반 OSC/title 및 unequal-rule 과거 오판을 정정하되 반증 기록은 보존했다. Guide/bundled copy/CHANGELOG 갱신. Extension guide 49,334 B < 51,200 B; #416에 1,866 B 여유를 남긴다.

## 재현과 검증 — 명령/결과

모든 명령의 cwd는 전용 author tree, Python은 그 tree의 `.venv/bin/python` (3.12.13). GH/Pi 공식 read-only 조회 외 runtime/test에서 외부 network/DNS를 사용하지 않았다. 아래 `/tmp` 파일은 로컬 증거이고 durable 회귀는 committed tests다.

| 명령/관측 | 결과 |
| --- | --- |
| 수정 전 `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py -k 'selected_unselected or active_inactive or positive' --tb=no -q` | 30 failed, 1 genuine-positive passed, 7 deselected; `/tmp/aelix-179-author-red-2026-10-12.log` |
| `.venv/bin/python /tmp/aelix-179-author-predecessor-tests-2026-10-12.py` (exact `git show 3b3adb4:context.py`를 임시 module에서 실행; 같은 final tests의 raw-byte rows) | 7 failed, 1 positive passed, 37 deselected; 각 failure에 실제 emitted OSC 0 bytes. `/tmp/aelix-179-author-real-bytes-red-2026-10-12.log` |
| `.venv/bin/python /tmp/aelix-179-author-parser-2026-10-12.py --baseline`, 같은 명령 without `--baseline` | 실제 Window → Renderer/Vt100_Output → real `openpty` → pyte paired probe. Genuine raw positive는 양쪽 TITLE179. Ordinary OSC option은 양쪽 0/title empty. SOH/STX + `_END` option own False/True, detail own False/True, tab name/body/error는 base 각각 1/title TITLE179/ZeroWidthEscape 1; fixed는 모두 0/title empty/fragments 0. `/tmp/aelix-179-author-parser-{base,fixed}-2026-10-12.json` |
| `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py -q --tb=short` | 45 passed in 1.21s |
| `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py tests/tui/test_picker_display_width.py tests/tui/test_select_title_held.py tests/tui/test_own_pickers_marked.py tests/tui/test_stats_dashboard.py tests/tui/test_context.py -q --tb=short` | 606 passed in 31.66s; `/tmp/aelix-179-author-focused-2026-10-12.log` |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check` (7 manually touched Python files) | 7 files already formatted |
| `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/check_types.py` | 289 files analysed, 0 errors, all 3 inverse assertions still error; `/tmp/aelix-179-author-types-2026-10-12.log` |
| `PATH="$PWD/.venv/bin:$PATH" .venv/bin/pyright --pythonplatform Windows --pythonpath .venv/bin/python --outputjson` | 289 analysed, 0 errors, 1 warning. Static Windows model only, native Windows runtime은 미실행. 초기 PATH 미지정 실행은 193 unresolved-import errors였으며, 구현 결함으로 취급하지 않는다.`/tmp/aelix-179-author-windows-types-2026-10-12.json` |
| `.venv/bin/python scripts/sync_bundled_docs.py` | 9 guides, 1 updated, 0 removed |
| `PATH="$PWD/.venv/bin:$PATH" UV_OFFLINE=true UV_NO_BUILD_ISOLATION=true UV_PYTHON="$PWD/.venv/bin/python" .venv/bin/python -m pytest tests/test_docs_bundle_sync.py tests/packaging_gate/test_docs_bundle.py -q --tb=short` | 16 passed in 1.46s, actual sdist→wheel docs gate; `/tmp/aelix-179-author-bundle-2026-10-12.log` |
| `.venv/bin/python scripts/check_citations.py --fix` / `--check` | relocated 24 citations across 9 files; 547 anchors/933 gated/0 out of range. Check: 933 gated, none drifted; 18 generic/nonunique anchors recorded, not gated. |
| `.venv/bin/python /tmp/aelix-179-author-ast-proof-2026-10-12.py` / `git diff --check` | Product behavior changes only context `_visible_len`/`_picker_payload`/select/tabbed, stats `_dim`, new private `_PickerText.__new__`; 5 other product files AST-identical after docstring removal (citation/comment/format only). Diff whitespace clean. |

집중 검증 중 26/3/21 failures를 관측했다. 테스트의 global first-letter selector가 option/filter 문자를 detail/footer로 오인한 점, UIContent 선두 empty fragment, 예전 raw ANSI 기대값, 짧은 footer hint와 full note 혼동을 수정했다. 실제 fragments `('', ''), ('dim','D')` / `('', ''), ('ansicyan bold','▸')` 를 확인하고 empty fragment를 제외하는 line-local style assertion으로 고쳤다. 최종 606 pass만 성공 결과로 사용한다.

## source freeze proof

`/tmp/aelix-179-author-ast-proof-2026-10-12.json`는 exact parent에 대한 AST 비교와 변경 product 8개 파일의 hash를 보존한다. 주요 source SHA-256:

- `tui/context.py`: `db6db13ff5770d0d4f44473fbff78b926267b67993dbcf8d4243681363e51baf`
- `tui/picker_text.py`: `7b7458e36a688c708a3f063144568f4e7e933b143ce5567e03e8ae8491b1df7f`
- `tui/stats_dashboard.py`: `da1f01a20449cf9fa7e27ac10290ea62174ac3a9cc13baf0575dde3fdb644c01`

Citation relocation touched consent/print_channel/progress/extension_manager/footer_segments only in comments/docstrings, plus corresponding test citations and lockfile; those product ASTs are identical to parent. `_TitleHeldControl`, `_title_rows`, confirm/input/runtime/auth code has no behavioral AST change. Formatting differences are separate from behavior.

## 바로 시작할 것 / 이후 순서

1. Root가 frozen author commit을 독립 adversarial review한다. 별도 reviewer tree에서 source hashes와 exact base를 다시 확인한다.
2. #395 writer release 후 root가 최종 #395 handoff-only parent에 rebase한다. Citation drift를 재검증하고 exact final head에서 gates를 측정한다.
3. Root는 실제 격리 `uv run aelix` TUI에서 동일 delimiter/ordinary control, own/external select, original return/index, tabs/detail, long title/resize/keys를 직접 확인한다. Author의 static parser PTY는 이 gate를 대체하지 않는다.
4. Root의 전체 스위트, batch actual-model 정상 제어군, 최종 PR/Project In review/16 CI가 남아 있다. Author는 full suite, actual CLI TUI/model, native Windows, GH writes/PR/merge를 실행하지 않았다.
5. 이 경계까지 검증한 뒤 승인된 다음 이슈 #416을 잇는다. Author는 다음 이슈에 착수하지 않는다.

## 반증된 것 — 다시 믿지 말 것

- 일반 OSC fixture는 수정 전후 모두 raw OSC/title change 0이었다. “picker의 모든 OSC가 제목을 바꾼다”는 주장은 틀리다. 새 positive는 SOH/STX delimiter-specific parser 경로다.
- Root actual CLI baseline (`/tmp/aelix-179-root-2026-10-12-baseline-v1/result.json`, frozen `8eaf440`)은 option own False/True 각2, tab name1 OSC/title change를 관측했고 detail/tab body는0이었다. Author의 `_END` 붙인 별도 static parser fixture가 detail/body/error에서1인 것을 그 CLI fixture에 옮겨 말하지 않는다.
- ZeroWidthEscape가 행 끝에 pending이고 뒤 cell이 없으면 renderer가 그 raw write를 내보내지 않을 수 있다. 첫 probe의 no-OSC는 fix 증거가 아니었으며, `_END`를 붙인 동일 paired fixtures와 genuine positive로 교체했다.
- 두 rule은 원래 하나의 width에서 그려져 같았다. Rule equality를 새 보안 성과로 쓰지 않는다.
- `own=True`는 text trust가 아니다. Private stats fragments도 text 안전 처리를 건너뛰지 않는다.
- String-level sanitizer assertions와 helper output 복사만으로 terminal parser fix를 입증하지 않는다. Actual UIContent, screen, Renderer bytes, positive control, predecessor red를 함께 보존한다.

## First repair — 독립 리뷰 P2 Installed built-in dim (2026-10-12)

위 author 기록은 최초 `18e8046a`/`3b3adb4` 측정의 역사다. Root가 최종 #395 handoff-only parent `48fabeb3736ca12a84ab475947c44916d0e34189` 위로 rebase한 후보 `ba8716349cc8eef82c398e4163d9f6200ae702ac`에서 독립 리뷰가 P2를 확인했다. 이 first repair는 그 parent를 유지하며, 이전 source/증거 로그를 덮어쓰지 않는다.

**확인된 결함:** `extension_manager.build_installed_lines`가 내부 built-in 행을 raw `_DIM`/`_RST`로 감싼 채 `tabbed`에 전달했다. 새로운 caller 정화는 그 ANSI도 정확히 이름으로 표시했기 때문에 화면에 `  ^[[2m● GuardrailExtension^[[0m`가 나오고 실제 Attrs.dim이 false였다. 최초 author가 놓친 internal producer migration이다. 최초 freeze가 “모든 internal styled producer를 보존했다”는 근거로 쓰이면 안 된다.

**좁은 수정:** Installed built-in 행만 기존 private `_PickerText` structured dim fragment로 생성한다. Raw dim/reset 상수는 없앴다. Caller text 처리는 건너뛰지 않고, public protocol/own trust/viewport/auth/model semantics는 바꾸지 않았다. `context.py`, `picker_text.py`, `stats_dashboard.py` source hash는 이전 freeze와 동일하다. 새 regression은 Guardrail/Permission/Status 행 각각의 actual Window/Renderer bytes와 Attrs.dim, hidden=false, ANSI names 부재를 검사하고, 같은 Installed 화면의 hostile user extension 이름은 여전히 reverse control names/no raw OSC인지 확인한다.

**Internal producer audit:** 전체 coding-agent source의 `tabbed` call/wiring 및 TUI SGR constants를 조사했다. 새 boundary를 받는 internal tabbed producers는 `run_stats`의 build-tab helpers와 `run_extension_manager`의 Installed/Discover/Sources builders다. Stats는 이미 `_PickerText`로 옮겼고, raw dim이 남은 것은 Installed built-in 행이었다. Select/detail producers인 model/thinking/login 및 shell call sites에 별도 internal raw SGR formatting을 발견하지 않았다. Footer sample의 raw styling은 multiselect preview 경로이며 이 select/tabbed boundary를 거치지 않는다; 원래 명시한 범위 밖이다.

| first repair 명령/관측 | 결과 |
| --- | --- |
| `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py -k installed_builtin -q --tb=short` on unchanged `ba871634` product source | Guardrail/Permission/Status 3 failed, original 45 deselected. `/tmp/aelix-179-repair1-builtins-red-2026-10-12.log` |
| `.venv/bin/python /tmp/aelix-179-repair1-owned-dim-pty-2026-10-12.py --baseline` / same without `--baseline` | Frozen independent P2 probe copied into OWN ignored probe directory and executed through the same actual Window → Renderer/Vt100_Output → openpty → pyte. Exact `git show ba871634:extension_manager.py` builder vs fixed builder, same context. Base row `  ^[[2m● GuardrailExtension^[[0m`, Attrs.dim=false, P2 assertion failed; fixed row `  ● GuardrailExtension`, Attrs.dim=true, P2 passed. Genuine raw positive title INDEPENDENT179 worked in both; no model/credentials. `/tmp/aelix-179-repair1-owned-dim-pty-{base,fixed}-2026-10-12.json` |
| `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py tests/tui/test_extension_manager.py -q --tb=short` | 83 passed in 1.30s (security file now 48: original45 + three P2 variants). `/tmp/aelix-179-repair1-security-extension-2026-10-12.log` |
| `.venv/bin/python -m pytest -c pyproject.toml -o asyncio_mode=auto .omc/probes/179-repair1-independent/test_independent.py -k 'not predecessor and not no_clean and not raw_sgr_allowed and not blanket_plain' -q --tb=short` | 31 candidate probes passed, 23 intentional predecessor/mutant rows deselected; exact independent P2 included. The original reviewer tree/report/probe remained immutable. `/tmp/aelix-179-repair1-independent-candidate-2026-10-12.log` |
| `.venv/bin/python -m pytest tests/tui/test_picker_caller_text.py tests/tui/test_picker_display_width.py tests/tui/test_select_title_held.py tests/tui/test_own_pickers_marked.py tests/tui/test_stats_dashboard.py tests/tui/test_context.py tests/tui/test_extension_manager.py -q --tb=short` | 644 passed in 31.42s. `/tmp/aelix-179-repair1-focused-2026-10-12.log` |
| `.venv/bin/ruff check .` / `.venv/bin/ruff format --check` for the 8 manually touched Python files | All checks passed; 8 already formatted. |
| `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python scripts/check_types.py` | 289 analysed, 0 errors, all3 inverse assertions still error. `/tmp/aelix-179-repair1-types-2026-10-12.log` |
| `PATH="$PWD/.venv/bin:$PATH" .venv/bin/pyright --pythonplatform Windows --pythonpath .venv/bin/python --outputjson` | 289 analysed, 0 errors, 1 warning; static model only. `/tmp/aelix-179-repair1-windows-types-2026-10-12.json` |
| Original offline/no-build-isolation bundle command, `tests/test_docs_bundle_sync.py tests/packaging_gate/test_docs_bundle.py` | 16 passed in 1.33s; actual wheel gate. `/tmp/aelix-179-repair1-bundle-2026-10-12.log` |
| `.venv/bin/python scripts/check_citations.py --fix` / `--check` | relocated1 across1 file; 546 anchors/932 gated/0 out of range; check no drift, same18 nongated generic/nonunique anchors. One old context citation was removed with the obsolete raw dim constants. |
| `/tmp/aelix-179-repair1-ast-proof-2026-10-12.py` / overall proof against `48fabeb3` | Since `ba871634`, only product function `extension_manager.build_installed_lines` changes behavior, plus the private fragment import/raw private constants removal. `aelix_status/snapshot.py` changes only a citation comment and has identical AST. `/tmp/aelix-179-repair1-ast-proof-2026-10-12.json`, `/tmp/aelix-179-repair1-overall-ast-proof-2026-10-12.json`. |

Current repair source SHA-256:

- `tui/extension_manager.py`: `4025d386a44409c6a85375a41dded20304d0b2a99ea76b7aec666cd2271e6693`
- `aelix_status/snapshot.py` (citation-only): `9ca34a9244304c7766502e7eaec14475ca5af5beb5b512e3aa7bd525ed912608`

**다음 경계:** First repair를 같은 single commit에 amend하고 clean freeze한 뒤 root에게 넘긴다. Parent는 이미 `48fabeb3`이므로 이전 `3b3adb4`로 되돌리지 않는다. Root가 독립 재검토·실제 CLI TUI·전체 suite·final exact-head PR/CI를 맡는다. Author는 full/live/model/native Windows/GH writes/merge를 실행하지 않았다. 이번 repair에는 red P2 reproduction 이후 검증 실패나 두 번의 blind repair가 없었다. Guide는 그대로49,334B이며 #416 여유1,866B 유지.

## Coordinator final local gates — 2026-10-12

Measured source `234bd99028e3f50b6b37b5d2b9d297302316abca`, parent final #395
`48fabeb3736ca12a84ab475947c44916d0e34189`. The author released its writer before
these independent/actual/full gates; source/index/venv stayed frozen during full.

- Fresh source-first first-repair review: no blocking finding. Newly authored
  21 probes passed; reused prior independent 49 passed/5 deliberately deselected;
  related committed tests 680 passed. Behaviors overlap and are not summed as
  distinct coverage. The old ba871634 Installed producer is red under the same
  contract; three deliberate security/style mutants are red. Physical Window →
  Renderer → Vt100 → PTY/pyte confirms all three built-in bullets/dim preserved,
  candidate nine attack surfaces emit raw OSC0, genuine/raw baseline positives
  emit OSC1. Original REQUEST CHANGES report/tree at ba871634 stays immutable.
- Actual `uv run --project <tree> --no-sync aelix` suffix-v3 runs: 80×16 and
  80×22 each resize to120×40/exit0. Own/external option, detail, tab name/body
  controls appear by name; raw target OSC/SOH/STX0, original return/detail index
  retained. Ordinary OSC had raw0 even before the fix; this result does not
  revive the disproved ordinary-OSC/window-rule claims. Baseline suffix-v3
  detail/body positive is a trailing-visible-cell paint condition.
- Actual built-in `/extension` and `/stats`: three Installed `● name` rows
  and Session/Activity/Efficiency notes retain emitted SGR dim. Literal SGR
  names absent; tab switching/Enter closure/resize/exit0. The first name-only
  observer mixed in the plain startup banner; two Escape-timing driver failures
  remain archived. The final v4 uses bullet-specific observation and Enter to
  close between slash commands. No product edits were made for those diagnostics.
- Actual OpenRouter `openai/gpt-4o-mini` normal TUI control returns NORMAL179_OK,
  agent_end/exit0,80×22→120×40. Only the selected original .env OpenRouter
  assignment was read into process memory. Four actual runs use explicit
  agent/settings/session scratch roots and cwd; no session JSONL or key in owned
  evidence. Nine virtual-terminal PNGs were directly inspected. Menlo bitmap
  glyph gaps do not describe raw UTF-8/pyte content or native OS rendering.
- Canonical frozen full command `uv run --no-sync pytest -p no:cacheprovider -q`:
  **16332 passed,25 skipped,104 warnings,683.66s**, exit0; wrapper688.98s,
  ended2026-10-11T20:13:30Z, unchanged clean234bd990. Ambient credential/provider
  settings were scrubbed,54 benign ambient variables retained,HOME unchanged.
- Fresh bounded Claude Haiku5.5 static: conditional pass/no concrete blocker
  within four supplied production diffs and five actual allowed read/grep calls.
  Guard41/payload2/genuine review-tool boundary7,4096 cap/reasoningFalse verified.
  External naming/cell helper implementation, whole caller chain, ADRs/tests,
  multiline/frame behavior, runtime/TUI/full are outside that model's scope.
  Readranges/grep patterns were not logged; no whole-file coverage is implied.
  Owned files and ignored mirror contain no selected actual key. Prior native
  Claude quota/Sonnet402 and old find enumeration limits are retained separately.
- Author Ruff/type289/0+inverse3/Windows static289/0(existingwarning1),8 touched
  files formatted,bundle16,citations932/no drift remain bounded to this source.
  Native Windows UI/runtime is unrun locally; own final-head Actions legs remain
  required. Guide49334B stays below unchanged51200B cap.

Independent and actual command records are in own ignored `.omc/probes/179-root`
and `.omc/probes/179-final`. Final amendment changes this handoff only; coordinator
records all-other-tracked-blob equality in `final-source-freeze-proof.json`.

Next: final stacked PR on #395/own exact16 CI, then #416 final author/review/live/
full/PR/CI. #416 has its own tree/lock/branch and starts after #179 critical gates;
its future parent-only rebase waits for writer release. No batch merge is approved.
