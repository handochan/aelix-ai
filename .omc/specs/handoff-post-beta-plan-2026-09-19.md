# Handoff — 베타 이후 방향을 계획으로 옮김 (2026-09-19)

Status: 방향 문서 합본 + 오너 결정 기록 + 보드 반영까지 완료. **코드는 바꾸지 않았다.**

## 기준점

- `main` = `origin/main` = `026bd15` (2026-09-13, #263 머지). 세션 내내 움직이지 않았다.
- main CI run 34705932974: 여섯 레그 전부 success. **이 세션은 풀 스위트를 돌리지 않았다.**
  돌린 것: `uv run --no-sync pytest tests/agents tests/agents_ext -q` → 1712 passed / 11 skipped (96 s).
- 계획 정본: **`docs/05-post-beta-direction.md`** (Codex 초안 + Claude 초안 합본, §11에 오너 결정 기록과 실행 기록).
  세션 기록: `.omc/specs/direction-post-beta2-2026-09-15.md`, Codex 교차 검토 `.omc/specs/review-direction-post-beta2-codex-2026-09-15.md`.
- 문서 커밋은 브랜치 `docs/post-beta-direction`에 있다. **main에는 머지하지 않았다 — 오너 승인 대기.**
  커밋은 공유 체크아웃의 HEAD를 건드리지 않으려고 worktree에서 만들었다. 그래서 main 체크아웃에는 같은 내용이 **미추적/수정 상태로
  남아 있고**, 그대로 머지하면 git이 "untracked working tree files would be overwritten"으로 거부한다. 머지 절차(내용은 브랜치와 동일):
  `git checkout -- docs/README.md` → 미추적 다섯 파일(`docs/05-post-beta-direction.md`, `.omc/specs/direction-post-beta2-2026-09-15.md`,
  `.omc/specs/review-direction-post-beta2-codex-2026-09-15.md`, `.omc/specs/handoff-post-beta-direction-2026-09-15.md`,
  `.omc/specs/handoff-post-beta-plan-2026-09-19.md`) 삭제 → `git merge --no-ff docs/post-beta-direction` → `git push origin main`(각각 단독 명령으로).
- 보드: 열린 이슈 134. 마일스톤 `v0.1.0-beta.3`(open 22, 날짜 없음). 닫힌 12건은 Done.

## 바로 시작할 것

**#294 (SessionStorage 적합성 스위트 + 새 기록은 `CustomEntry`로) → 그 다음 #199 (자식 세션 기록).**
순서의 이유: #199가 부모 세션에 쓸 spawn 계보·사용량·operation record의 **운반 형식**을 #294가 고정한다. 모르는 entry `type`은
로더가 "손상"으로 건너뛰면서 **그 줄을 부모로 둔 entry까지 가지치기**하므로(확인함), 형식을 먼저 못 박지 않으면 #199가 옛 Aelix에서
대화를 잃게 만드는 파일을 쓴다. 둘 다 `session/`을 건드리므로 **같은 배치에 넣지 않는다**(CLAUDE.md 3항).

#199의 출발점은 이미 있다: `render_subagent_result`가 `summary`만 content에 넣고 원문을 `details`로 뺀다. `details` 자리에 자식
세션 링크를 두면 오너 요구("구체 내용은 부모 밖, 기록은 남김")가 그대로 성립한다. 설계 메모는 #199 코멘트에 있다.

## 이후 순서

- 흐름 A(안정성·내구성): #294 → #199(+#168) → #194 → #260/#261 → #131. 그 사이 독립 배치로 #285 · #286 · #259/#256 · #157.
- 흐름 B(Analytics pack + Pack 계약): #253 ADR(범위는 오너가 확정: agents·skills 기여 + extension tier + `aelix install`/`aelix run` +
  호환 버전 범위) → Analytics 어댑터의 실제 모델 E2E 하나. Analytics는 `requires-python >=3.12`이므로 본체 환경에 넣지 않는다.
- 게이트 #292 · #293 · #279는 **셋 다 `.github/workflows/ci.yml`을 건드린다** — 같은 배치 금지, 순차.
- #289 + #288은 `cli/entry.py` early-exit 체인(부록 A) 분리와 한 묶음. `cli/args.py` `parse_args`(Ruff 80)도 같은 뿌리.
- 문서 #266 → #265 → #267. #266에 #86 태그라인 정직성(원격 모델을 쓰면 "never leave your network"는 거짓)이 들어간다.
- 서버·웹은 #199 뒤. 첫 전제는 #290(승인 브리지·`list_sessions`·제한된 명령 실행), 웹 파일 미리보기는 #291.
- 오너 판단 대기: **#137·#188을 `v0.1.0`에서 `v0.1.0-beta.3`로 당길지**(둘 다 P0, 옮기지 않았다) · 영역 라벨을 못 붙인 35건 트리아지
  (목록은 docs/05 §11) · #142의 `pypi` environment required reviewer · Copilot 좌석 ToS(기능은 오너가 확인).

## 이 레포에서 실제로 물린 규칙

- **zsh는 따옴표 없는 변수를 단어로 쪼개지 않는다.** 이 세션에서 두 번 물렸다: `for s in "a b"; set -- $s`와
  `${4:+--milestone "$4"}`. 후자는 `gh issue create` 다섯 중 셋을 실패시켰고 **나머지 둘은 성공해서** 번호가 어긋났다(#290·#291이 먼저).
  루프 안에서 부분 성공이 나면 만든 목록을 다시 읽는다.
- 이슈를 닫으면 보드는 자동으로 Done, 새 이슈는 자동으로 Backlog. 우선순위는 라벨이 아니라 보드 필드(P0 `79628723`).
- `radon cc -j`는 클래스의 methods와 함수의 closures를 **중첩**해서 준다. 최상위만 세면 3,592, 재귀로 세면 4,774다.
  Ruff C901은 중첩 함수를 부모에 합산한다(`run_tui` 248) — 두 점수를 섞지 않는다.
- `docs/05-*.md`는 citation gate 밖이다(`GATED_PREFIXES` = `packages/`·`tests/`·`docs/guides/` + 루트 `*.md`). 거기 적은 줄 번호는
  아무도 지켜 주지 않으므로 부록 A는 스냅샷이라고 적어 뒀다 — 착수할 때 심볼로 다시 찾는다.
- 위임 라이브 체크는 scratch cwd + `--session-dir <scratch>` + `--permission-mode plan` +
  `uv run --project /Users/handochan/dev/aelix-ai --no-sync aelix --agents --mode json -p …`. 오너의 전역 설정은 이미 `features.agents: true`다.
- 서브에이전트 보고는 idle 알림에서 약 3.5K자에서 잘린다. 300단어 이하 조각으로 다시 요청한다.
- `uv run --no-sync`로 테스트를 돌린다 — 다른 세션이 `.venv`를 재동기화하는 중일 수 있다.

## 반증된 것 — 다시 믿지 말 것

- **"beta.2에서 멀티에이전트가 깨졌다(#262)"** — 아니다. Python 3.14 × `openai<2`다(ADR-0241이 #262를 이름으로 적어 뒀다).
  3.12에서 같은 모양(explorer · parallel · 3)이 3 ok / 0 failed.
- **"열린 이슈 수 = 남은 일"** — 141건 중 12건은 이미 출하된 것이었다. 반대로 CHANGELOG에 번호가 있다고 다 끝난 것도 아니다(#142·#172는 부분).
- **"`SessionInfoEntry.parent_id`는 fork 계보다"** — 모든 entry가 갖는 트리 부모다. 세션 간 관계는 `parent_session_path`.
- **"RPC에 승인 핸들러가 없다"** — 타입은 있고 `_on_line`이 `extension_ui_response`를 인식한 뒤 버린다(ADR-0058이 미룬 브리지).
- **"C901 40 초과는 두 함수"** — 여섯이다. 그리고 `# noqa`로 숨기면 악화를 못 본다.
- **"pi가 JSONL을 버렸다"** — 버린 것은 legacy repo다. format 4는 Memory·JSONL·SQLite가 한 적합성 스위트를 통과하고 coding agent는 여전히 JSONL.
- **"SQLite로 가면 #137이 풀린다"** — pi 명세가 직접 적는다: 어느 백엔드도 두 번째 writer를 감지하지 못하고 소유권은 호스트 책임이다.
- **"로더가 모르는 줄을 건너뛰니 새 entry type은 하위 호환이다"** — 건너뛰면서 후손을 가지치기한다. `CustomEntry`로 싣는다.
- **"자식 비용을 재지 않는다"** — 잰다(`SubagentUsage`, footer에 표시). 저장과 세션 통계 롤업이 없을 뿐이다.
- **"자식의 구체 내용이 부모 컨텍스트를 오염시킨다"** — 아니다. 부모 모델은 최종 요약만 받는다(라이브 129자). 다만 상한 기본값이 51,200바이트다.
