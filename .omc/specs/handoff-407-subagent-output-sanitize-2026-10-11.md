# Handoff — #407 subagent output sanitisation (2026-10-11)

## 기준점

- Issue: https://github.com/handochan/aelix-ai/issues/407
- 구현 전 기준 main: `bc6fd3fbbe4b9d7ff3300a141c117be578720b95`.
- Worktree: `/Users/handochan/dev/aelix-ai-worktrees/codex-407`.
- Branch: `fix/407-subagent-output-sanitize`. 이 handoff를 포함한 issue 단일
  커밋을 구현자가 만들고, frozen SHA를 root에 전달한다.
- 정본 scope/authorization:
  `/Users/handochan/dev/aelix-ai/.omc/specs/handoff-beta3-next-security-2026-10-11.md`.
  원본의 ignored `CLAUDE.md`/`AGENTS.md`는 읽기만 했고 복사/변경하지 않았다.

## 구현 범위

- `/agents run` 결과의 summary/error, profile/decline, status, permission,
  model/provider, dropped tool 이름을 공통 `terminal_text` helper로 정화한다.
- resolve, 프로젝트 확인 뒤 재resolve, spawn의 예외 문자열도 같은 표시 경계를
  지난다. consent 판정/권한 posture/transport/history는 바꾸지 않는다.
- `_CONTROL_KILL` 사본을 제거하고 기존 public `sanitize_for_terminal`을
  compatibility wrapper로 유지한다. 고정 열은 기존 공백 collapse/40 codepoint
  cap을 유지하고, summary/error는 개행·탭·들여쓰기를 보존한다. 기존
  `tui.stream.drop_format_controls`를 조합해 BiDi marks 3개도 제거한다.
- 정상 한·일·accented Latin·Arabic/Hebrew RTL 문자열과 `👩‍💻`의 ZWJ는
  regression과 ADR로 보존 계약을 명시했다. global helper 정책은 바꾸지 않았다.
- child Rich markup은 literal `Text`로 출력한다. Aelix가 붙인 panel/label/error
  styling과 truncation 표시는 남는다. 정화된 빈 summary는 `(no output)`이다.
- ADR-0197 §(l)을 개정하고 ADR-0199의 #407 known limit을 갱신했다.
  CHANGELOG/ADR index와 source citation relocation도 함께 반영했다.
- Pi 현재 참조는 `ea448f454af474f3e2fe9733b331e17b0662f5d6`
  (2026-10-10T19:29:35Z, 2026-10-11 확인). example subagent result renderer와
  issue #5996(단일 footer 이름의 개행)을 확인했다. Pi parity 자체는 목표가 아니다.

## 실제 실행한 검증

- `uv sync --all-packages`: Python 3.12.13, 자기 editable .venv, 78 packages.
- `uv run pytest -p no:cacheprovider -q --tb=no tests/tui/test_agents_run_terminal_bytes_407.py tests/tui/test_agents_run_command.py tests/providers/test_terminal_text.py`
  → **309 passed in 1.31s** (최종 marks 포함).
- `env -u NO_COLOR TERM=xterm-256color uv run pytest -p no:cacheprovider -q --tb=no tests/tui/test_commands.py tests/tui/test_agents_command.py tests/tui/test_own_pickers_marked.py tests/agents/test_p2_band_boundaries.py tests/agents_ext/test_child_authored_strings_178.py tests/tui/test_subagent_rows_terminal_bytes_178.py tests/tui/test_render_terminal_safety_177.py tests/test_docs_bundle_sync.py`
  → **700 passed in 5.43s** (최종 source).
- `uv run pytest -p no:cacheprovider -q --tb=no tests/cli/test_entry_router.py tests/cli/test_docs_command.py tests/test_docs_bundle_sync.py`
  → **63 passed in 6.16s** (citation/doc-only 변경 확인).
- `uv run ruff check .` → **All checks passed!**
- `uv run python scripts/check_types.py` → **288 files, 0 errors; inverse spike
  3/3 expected errors; PASS**.
- `uv run python scripts/check_citations.py --fix` → **7 citations relocated
  across 4 files**, 547 anchors. 결과 commit loop, `/trust` handler,
  login/trust registry의 target 문맥을 실제 확인했다. guide 사본도 byte-identical.
- `uv run python scripts/check_citations.py --check` → **934 gated, none drifted**
  (기존 18 ambiguous anchors는 recorded/ungated).
- `git diff --check` → exit 0.

## Counterfactual

새 raw Console 회귀는 249건이다. 최종 동일 테스트 파일을 기준 main의
`commands.py` 모듈 내용으로만 실행하면 **176 failed / 73 passed in 1.24s**.
fix를 import하면 같은 테스트가 모두 통과한다. 실행 중 source 파일은 바꾸지 않았다.
아래 명령의 실패 출력은 `/tmp/aelix-407-baseline-final-pytest.log`에만 저장했다.

```bash
uv run python - <<'PY' > /tmp/aelix-407-baseline-final-pytest.log 2>&1
import subprocess
import pytest
import aelix_coding_agent.tui.commands as commands
source = subprocess.check_output([
    'git', 'show',
    'bc6fd3fbbe4b9d7ff3300a141c117be578720b95:packages/aelix-coding-agent/src/aelix_coding_agent/tui/commands.py',
], text=True)
exec(compile(source, commands.__file__, 'exec'), commands.__dict__)
raise SystemExit(pytest.main([
    '-p', 'no:cacheprovider', '-q', '--tb=no',
    'tests/tui/test_agents_run_terminal_bytes_407.py',
]))
PY
```

## 구현자가 freeze할 당시 미실행 범위 — root 완료 기록은 아래

- root가 frozen SHA에서 독립 Codex 검증과 Claude CLI 교차 review를 진행한다.
  구현 맥락의 테스트·소스 점검을 독립 review로 주장하지 않는다.
- root가 실제 `uv run aelix` TUI 및 real-model delegation/terminal raw output을
  직접 확인한다. 구현자는 실제 TUI/모델, owner credentials를 실행/읽지 않았다.
- root 요청 전 **broad full pytest를 시작하지 않았다**. 독립 review/fix 뒤 final
  frozen source에서 root가 지시한 full model-free gate를 한 번 실행한다.
- 이 구현자는 push/PR/merge/보드/GitHub 이슈 상태를 변경하지 않았다.
- shared helper의 기존 계약은 BiDi **overrides/isolates**를 제거하되 marks
  U+200E/U+200F/U+061C는 보존한다. #407도 #177과 같은 기존
  `drop_format_controls`를 조합하여 표시 경계에서 marks까지 제거한다.
  global helper 정책 변경과 별도 control table 추가 없이 경계를 맞췄다.

## 이후 순서

#394 → #289 → 인증 판정 #416 → 설치 CI #279/flake #381 → 후보 고정 뒤
release exits #382~385. 이번 변경에는 #179 picker나 #424 layout을 섞지 않았다.

## 반증된 것 — 다시 믿지 말 것

- Rich `Text`/plain `Console`만으로 raw ESC/C1/BiDi가 차단되지는 않는다.
- 색상 양성 대조군은 `force_terminal=True`만으로 충분하지 않다. 이 shell은
  `TERM=dumb`, `NO_COLOR=1`을 상속해 기존 #177 SGR/link 테스트가 30건 실패했다
  (670 passed). TERM만 바꾼 재실행도 동일했다. 두 환경을 모두 바로잡은 위 명령은
  700/700 통과했다. #407 새 회귀는 `color_system`도 explicit로 설정한다.
- 최초 counterfactual 161 failed/70 passed에는 이 환경에서 자체색상 양성
  대조군 1건 실패가 섞였다. 160/71은 그 보정 뒤 marks 추가 전 결과이다.
  최종 위 counterfactual의 **176/73**만 근거로 쓴다.
- 바이트 회귀 green이 실제 TUI/live gate 완료를 뜻하지 않는다.
- 공격 escape가 든 실패 stdout는 owner terminal로 출력하지 않는다. scratch log를
  사용할 때 summary만 `ascii()`로 읽는다. keys/owner `~/.aelix`를 읽거나 바꾸지 않는다.

## Root 검증 패스 (구현과 별도)

검증 대상 source SHA는 `a878ae6548dd39cee9d88ddcba34d4b5db177783`이다.
최종 root 결과를 handoff에 추가하는 amend는 이 파일만 바꾸며, source/tests가
동일한지를 `git diff a878ae6..HEAD --name-only`로 대조한다.

- 새 컨텍스트 Codex 독립 리뷰: 31개 자체 boundary probe와 **377 passed in
  4.08s**, 추가 base/head 대조 17개. 작성자의 대화/결과를 먼저 읽어 승인한
  것이 아니다. ESC/C1/BiDi/Rich markup, 실제 numeric reducer, 정상 RTL/ZWJ/
  combining/multiline/own style, 원본 result 불변을 확인했다. blocking 없음.
  detached `codex-review-407/.omc/probes/407-independent/review-report.md`.
- 별도 fresh Claude CLI 교차 리뷰: source-only, blocking 없음. 테스트/TUI/
  Windows 실행 및 git diff를 이 리뷰가 수행했다고 세지 않는다. 프로필 이름
  BiDi에 대한 추측은 실제 validator 대조로 반증했다.
  `.omc/probes/407-claude-review/response.json`.
- root 직접 실제 `uv run aelix` TUI 및 OpenRouter `openai/gpt-4o-mini` child를
  기준 main과 fix에서 실행했다. child 완료 후 renderer 입력에 controlled
  hostile fixture를 삽입했다. 모델이 공격 문자열을 직접 생성했다고 주장하지
  않는다. OSC52/CSI/DCS/C1/RLO/isolate/marks의 7종 누출은 base에서 모두 true,
  fix에서 모두 false. summary/error/앱 스타일 보존 및 직접 화면 확인,
  자연 종료 exit 0 (base 0.102s, fix 0.113s).
  `codex-402/.omc/probes/407-root/{base-v2,fix-v1}/{result.json,terminal.raw}`.
- 실제 모델 확인은 scratch `AELIX_CODING_AGENT_DIR`/`AELIX_SETTINGS_PATH`/cwd로
  격리했다. 원본 `.env`의 OPENROUTER_API_KEY 한 변수만 선택했고 키는 출력하지
  않았다. HOME을 덮어쓰거나 owner `~/.aelix/auth.json/settings`를 읽지 않았다.
- 전체 model-free: **15641 passed, 25 skipped, 104 warnings in 636.31s, exit 0**.
  명령 `uv run --no-sync pytest -p no:cacheprovider -q`, CPython 3.12.13,
  own venv, TERM=xterm-256color, NO_COLOR/provider keys/AELIX_*를 제거했다.
  `.omc/probes/407-final/full-suite-uvrun-py312.out`, `full-command-uvrun.json`.

첫 full driver의 symlink resolve는 venv 밖 Python을 호출해 pytest import도
못했다. 이후 직접 venv Python으로 실행한 전체는 **15617 passed, 45 skipped,
1 failed, 3 errors, 104 warnings in 654.92s, exit 1**이었다. 자식 프로세스가
`python`을 못 찾았고 scaffold builder가 시스템 Python 3.9를 골랐다.
실행 환경의 PATH를 전달하지 못한 root driver 오류이며 대상 source/tests는
base와 동일하다. 실패 기록은 보존했다. own venv에 기준 tree와 같은
pip26.2.1/hatchling1.32.4만 준비한 뒤 canonical uv run의 관련 6건은
**6 passed in 1.20s**. repo dependency/lock/source 변경은 없다.

라이브 최초 시도의 `approval_mode: plan`도 harness 설정 오류였다. 실제
profile enum은 inherit/ask/auto/deny이며, child permission mode plan과 다르다.
project 확인은 [y/n]이었다. 가상 Yes/No 메뉴로 Enter를 누르는 driver 가정은
반증됐다. 성공 근거는 교정된 base-v2/fix-v1만 쓴다.

## 남은 작업과 독립 후속

- 다음은 final head push/PR와 해당 head의 CI이다. Ubuntu/Windows Python
  3.11/3.12/3.13 + install.ps1 pwsh/powershell의 8개 required check를 확인한다.
  Windows interactive-host 실행은 이 로컬 검증에서 수행하지 않았다.
- 이 root 기록 시점에 새 PR/merge/issue close는 하지 않았다. 이슈는 In progress.
  최종 PR 본문과 root 조율 handoff가 이후 원격 CI/승인 상태의 정본이다.
- 이전 8개 PR의 merge 승인을 이번 새 PR까지 확장해 해석하지 않는다.
- 독립 리뷰의 기존 별도 결함은 #435(프로필 list/show/use 출력),
  #436(error 표시 예산), #437(lone surrogate UTF-8 bytes 계산)으로 등록했다.
  Project Backlog, milestone 없음. #407 범위로 구현하지 않았다.
  실제 protocol은 JSON line 4 MiB/stderr 64 KiB로 유한하므로 "무한 오류"라
  부르지 않는다. YAML profile.name의 BiDi는 validation이 차단한다.
- 실제 project identity picker의 raw-path BiDi는 기존 #179와 함께 판정한다.
  #390 descriptor markup, #424 layout도 이번 변경에 포함하지 않았다.
