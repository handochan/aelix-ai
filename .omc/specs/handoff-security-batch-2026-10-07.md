# Handoff — beta.3 보안 배치: #288 · #387 · #188 · #374 · #186 · #177 · #131 — 2026-10-07

실행 기록의 정본은 `.omc/specs/batch-security-374-131-177-186-288-188-plan-2026-10-06.md`다. 이 배치 전의 pi 방향 배치 핸드오프는 `.omc/specs/handoff-pi-direction-batch-2026-10-06.md`다. 라운드별 결과 JSON은 `.omc/specs/batch-security-*-results.json`에 있고, beta.3 재판정은 `.omc/specs/beta3-remaining-assessment-2026-10-06.json`에 있다.

## 기준점

- **main의 마지막 코드 커밋은 `e67cb1b1`**(#131)이다.

  | 커밋 | 이슈 | 한 줄 | 브랜치 CI | main CI |
  | --- | --- | --- | --- | --- |
  | `9084699d` | #387 | 루트 sdist에서 demo.gif(2.3 MB)를 빼고, README는 절대 URL로 바꿨다. sdist는 5.97 MB에서 3.67 MB로 줄었다 | 37513810009 6/6 (attempt 2) | — |
  | `339f8735` | #288 | 오프라인 판정을 `util/offline.py` 하나로 모았다. `AELIX_OFFLINE`이 rg/fd 다운로드를 막고, 모든 동사 앞에서 export한다 | 37519709822 6/6 | 37523069083 6/6 |
  | `790968b9` | #188 | 승인 게이트를 이름이 아니라 도구 출처(provenance)로 판단한다. MCP와 확장 도구는 묻고, PLAN은 막고, 대화상자는 인자를 숨기지 않는다 | 37538017682 6/6 | (스택) |
  | `0f9ee54d` | #374 | SDK가 다른 프로바이더의 env 키나 credential chain을 읽지 않는다. pi의 "No API key for provider"를 따른다 | 37538020774 6/6 | (스택) |
  | `7f410441` | #186 | OAuth와 모델 요청 오류에서 서버나 프록시가 보낸 텍스트를 정화하고 512자로 자른다. 분류는 자르기 전 원문으로 한다 | 37538024011 6/6 | (스택) |
  | `00667c94` | #177 | TUI의 도구 줄, 결과, descriptor, OSC 8, bidi를 정화한다. descriptor 조회 키는 바이트 그대로 둔다 | 37538027052 6/6 | 37540683336 6/6 |
  | `e67cb1b1` | #131 | 카탈로그 source를 허용 목록으로 받고, 로컬 경로는 file URI로 넘긴다. cwd에서 해석하지 않는다 | 37565807086 6/6 | 37567767988 6/6 |

- 전체 스위트(main `e67cb1b1`, `.aelix/`가 없는 새 워크트리, `AELIX_*`와 `VIRTUAL_ENV` 해제): **13188 passed, 43 skipped, 104 warnings, 522.41 s, exit 0 (실패 0)**. skip 중 20개는 `AELIX_TEST_PIP_PYTHON`이 없어 건너뛴 #131의 pip 행이다. 출력 사본은 `.omc/probes/final-suite-e67cb1b1.out`
- **ADR**: 새로 쓴 것은 0253(#188), 0254(#374), 0255(#131)다. 0112(#177), 0139·0185(#288), 0188(#131), 0229(#387), 0251(#186)에는 amendment를 달았다. **다음 빈 번호는 0256이다.**
- **원격 브랜치**: 이번 배치의 7개 브랜치가 원격에 남아 있다. 삭제할 때 `git ls-remote --heads origin`으로 머지 여부를 확인한다. 오너가 명시적으로 요청하면 메인 루프가 직접 지울 수 있다(10-06 실측).
- **키트**: `.omc/probes/{288,387,188,374,186,177,131}-live/`, `.omc/probes/security-stack/`(스택 rebase와 메인 루프 TUI·라이브 확인), `.omc/probes/ci-flakes/`.

## 바로 시작할 것

**beta.3의 필수 항목 중 보안 P1 두 건**:

- **#389**(P1): aelix 자신의 bash 승인 대화상자가 긴 명령의 꼬리를 숨긴 채 Yes를 받는다. #188의 `_ArgumentViewport`를 kind=bash에도 쓰고, write와 edit의 diff에도 필요한지 정한다. pi의 bash 승인 렌더링을 먼저 확인한다.
- **#392**(P1): uv 백엔드가 cwd의 `uv.toml`이나 `pyproject.toml`의 `[tool.uv]` find-links를 읽어서, 신뢰하는 카탈로그의 패키지 설치를 레포의 wheel로 돌린다. ADR-0200이 사용자 uv 설정을 따르기로 한 데서 생긴 결과다. **시작 전에 오너가 (a)~(d) 중 하나를 정해야 한다**(이슈 본문 참조).

그다음은 beta.3 필수 코드 항목이다. 파일이 겹치는지 보고 배치를 나눈다.

- 기본 경험:
  - #354: 위임 자식이 thinking 레벨을 물려받지 않아 400으로 끝난다.
  - #376: `--continue`가 세션 모델을 복원하지 않는다(`restore_model_from_session`을 부르는 곳이 없다).
  - #289: `--offline`이 서브커맨드 앞에 오면 argv 라우팅이 틀린다.
  - #375: `OPENROUTER_BASE_URL`이 `/model` 선택에 적용되지 않는다.
  - #289와 #376은 둘 다 `cli/entry.py`를 고치므로 같은 배치에 넣지 않는다.
- 설치와 CI:
  - #279: install.sh e2e
  - #192: 3.13 레그
  - #393: CI의 uv 고정을 0.11.27 이상으로 올린다. #192와 같은 `ci.yml`을 고치므로 차례로 한다.
- Windows CI 신뢰도: #381과 #388(타이밍 가정 플레이크 2건). 출구 조건 (3)에 걸린다.
- 릴리즈 출구 조건은 맨 끝에 한다: #382(publish 전 후보 설치), #383(업그레이드), #384(라이브, Windows는 오너), #385(CHANGELOG Known limitations와 README 정리).

## 이후 순서 (있으면 좋은 것, beta.3 마일스톤)

#379(OAuth refresh 재시도, 오너가 pi처럼 하기로 결정), #168, #178, #179, #365, #371, #380, #386, #390, #391, #286, #285, #278, #292, #293, #266.

## 오너 판단

### 이번 배치에서 받은 것

- 마일스톤 정리와 보안 배치를 권고대로 한다(2026-10-06).
- 결정할 것은 권고대로 한다:
  - 빈 stored 키는 엄격하게 실패시킨다.
  - #379 refresh 재시도는 pi처럼 한다.
  - #367의 pi와 다른 두 가지는 유지한다.
- **#131은 "7차로 마무리"**(2026-10-07): 단순한 거부 규칙을 쓰고, 위협 모델을 명시하고, verify 1회 뒤 Codex 없이 머지한다.

### 아직 오너에게 물을 것

- **#392 (a)~(d)**: 중립 디렉터리에서 uv 실행, `--no-config`, 경고 또는 거부, 문서만 남기기 중 하나.
- **#374**:
  - `-p`/json의 `has_configured_auth`가 `ANTHROPIC_CUSTOM_HEADERS`의 인증 헤더를 셀지.
  - pi와 다른 점(소문자 헤더와 cf-aig 헤더를 인증으로 세고, 이 변수를 anthropic에만 적용)을 유지할지.
- **#188**(ADR-0253 §8):
  - 세션 grant가 모든 인자를 덮는 것.
  - headless 부모가 MCP 도구를 PLAN 밖에서 실행하는 것.
  - PLAN이 읽기 전용 MCP 도구도 막는 것(#29 전까지).
  - 위임 자식이 MCP 도구를 막는 것.
- **#186**: 분류를 #186 이전 답에 고정했다(Codex 500자, Google `str(exc)`). 넓힐지.
- **#177**: replay가 descriptor 뷰를 쓰지 않는다(원래 있던 동작).
- **#288**: 원격 MCP(http, sse)를 오프라인에서도 연결한다. `extension --offline` 탐지는 argv 어디서든 한다.
- **#387**: README 이미지 URL을 main에 묶어 두었다(태그 고정 여부).
- **#131**: typed path와 update의 `#` 경로는 설치했던 그대로 넘긴다(§12 제한 목록).

## 이 레포에서 실제로 물린 규칙

- **새 테스트 파일은 `git add`한 뒤에 스위트를 돌린다.** `test_citation_drift`와 `test_env_sandbox_windows`는 `git ls-files`만 본다. #131과 #177 r1이 untracked 상태로 "통과"를 보고했지만 커밋은 빨갰다(메모리 `ls-files-gates-miss-untracked-tests`).
- **type gate는 CI처럼 `env -u VIRTUAL_ENV`로 돌린다.** #188 r3 레인이 VIRTUAL_ENV를 둔 채로 PASS를 보고했고, CI 방식으로 돌리면 실패했다.
- **sdist 크기 게이트는 Windows(CRLF)에서 먼저 터진다.** LF 기준으로 20 KB 여유가 있던 main이 #288의 Windows 레그에서 4 KB 넘었다. 문서가 배치마다 늘어나므로 `packaging_gate`를 각 레인 게이트에 넣었다.
- **문서가 겹치는 레인은 스택으로 머지한다.** 검증을 마친 4건(#188, #374, #186, #177)을 한 줄로 rebase했다. range-diff로 검증하고, 4개 브랜치 CI를 동시에 돌린 뒤 main을 맨 위로 한 번에 ff했다. 하나씩 push하면 main CI가 per-ref cancel로 서로를 취소한다.
- **Codex 프롬프트에 escape 시퀀스나 "dependency confusion" 같은 표현을 그대로 넣으면 OpenAI 필터에 걸린다**(#177 r1 보고 잘림, #131 r4 리뷰 0). 중립적인 표현("an OSC 52 sequence"를 문장으로 설명)으로 바꾸면 통과했다.
- **멈춤 규칙(#131)**: 6라운드 뒤에도 Codex가 P1을 계속 찾으면, 남은 결함 부류(설치기가 문자열을 다시 해석하는 것)를 구조적으로 막고, 위협 모델을 문서로 좁히고, 오너에게 묻는다. 이번에는 "7차로 마무리"로 끝냈다.
- **Windows CI 실패는 대부분 테스트의 POSIX 가정이었다**: `:`가 든 이름, 끝 공백, symlink 뒤 `..`의 lexical 처리, `json.dumps`의 백슬래시. #131에서 5건 중 4건이 그랬다. 남은 1건은 uv 버그(#393)였다.

## 반증된 것 — 다시 믿지 말 것

- "#256: Google 어댑터가 non-reasoning 행에 thinking을 켠다" → 와이어에서는 `_google_shared.py:860`이 막는다. 닫았다.
- "sdist 상한은 문서가 자라도 여유가 있다" → Windows CRLF에서 6 MB를 넘었다. demo.gif를 뺐다(#387).
- "#186: 512자로 자르기만 하면 된다" → 컨텍스트 초과 감지(`context_length_exceeded`)가 잘려서 compaction과 retry가 꺼졌다. 분류는 원문으로 해야 한다.
- "#131: 상대 참조를 다시 쓰면 된다" → 설치기(uv, pip)가 넘겨받은 문자열을 다시 해석한다. `#`, `[]`, 공백, 대문자 scheme, `name @` 상대 경로가 각각 cwd로 새는 경로였다. 해석이 끝난 로컬 경로는 percent-encoded file URI로 넘겨야 한다.
- "#131 위협 모델: cwd는 신뢰하는 카탈로그 항목을 만족시킬 수 없다" → uv의 cwd `[tool.uv]` 설정이 그것을 만족시킨다(#392).
- "Windows의 `name @ git+file:///C:/…` 실패는 aelix 탓" → uv 버그다(astral-sh/uv#19887, 0.11.27에서 고쳐졌다).
