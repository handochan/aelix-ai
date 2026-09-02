# 핸드오프 — 베타 태그 직전 (2026-08-19, 2차 갱신)

**오너 미결정 2건이 둘 다 처리돼 main에 병합됨. 남은 건 태그와 그 전제 하나.**

---

## 0. 상태

| | |
|---|---|
| `main` = `origin/main` | **`94ecf4c`** (푸시 완료) |
| 태그 | **없음** |
| 스위트 | **9214 passed / 1 skipped** (직전 9170) · ruff clean · 타입 270파일 0오류 |
| 카탈로그 | **1427 모델 / 35 프로바이더** (직전 1005) |
| 워크트리 | `/workspaces/aelix-catalog` · `/workspaces/aelix-lockgate` (둘 다 병합 완료, 남겨 둠) |

⚠️ 메인 체크아웃의 **미커밋 `README.md`**(포매터 리플로)는 여전히 그대로. 내 것 아니고 안 건드림.

## 1. 이번에 나간 것

```
94ecf4c docs: ADR-0232 says which half of #172 it did not do
7ae822d Merge 'chore/lock-manifest-gate'    — ADR-0233, 락↔매니페스트 게이트
3b89561 Merge 'feat/172-catalog-overlay'    — ADR-0232, 모델 422개 추가
bff797f test(models): gate build_row        — 눈먼 게이트 6건 수정
92eb50a feat(models): 422 models …
```
(그 이전: `bda2d28` 에코 바 · `ad5b0cc` #196 · `c97fa12` 업데이트 알림)

## 2. 🔴 태그 전에 반드시 할 것 (변함없음)

**`site/latest-version.json`을 GitHub Pages에 발행.** 안 하면 업데이트 알림이 **조용히**
아무것도 안 한다 — 모든 실패가 무음 설계라 증상이 안 보인다. `RELEASING.md` 베타 트랙 **2단계**.

그 다음 `v0.1.0-beta.1` 태그(오너 액션). 휠은 `0.1.0b1`, 태그는 `v0.1.0-beta.1` — 같은 릴리즈의
다른 표기이고 업데이트 알림이 그걸 이해한다.

## 3. 카탈로그(ADR-0232) — 다음 사람이 알아야 할 것

- 갱신 명령: `python scripts/refresh_catalog.py --fetch --apply` (드라이런은 `--apply` 없이,
  규칙 점수는 `--audit`, 거부 목록은 `--show-skips`).
- 🔴 **models.dev에는 전송이 없다.** `api`/`baseUrl`/`headers`/`compat`은 `api.json` 어디에도
  없고 pi의 프로바이더별 파일에서 온다. 그래서 스크립트는 전송을 **기존 형제 행에서 상속**하고
  근거가 없으면 거부한다. 이게 #172가 다운로드가 아니었던 이유.
- 🔴 **잔여 위험**: 프로바이더가 **처음 쓰는 와이어**로 모델을 내면 상속할 게 없어 틀린다.
  정방향 오답 11건이 전부 그 역사적 순간(copilot 첫 anthropic-messages, opencode 첫
  google-generative-ai)이다. 그래서 실행이 추가·거부 목록을 찍고 사람이 읽는다.
- 🔴 **`git diff`가 11209줄 삭제라고 한다. 실제 삭제는 0.** 파일이 대부분 `},`라 git이
  재정렬한 것 — **줄 수로 이 파일을 보지 말 것**, 파싱해서 행을 비교할 것.
- 401건은 일부러 뺐다(툴 호출 불가 325 · 전송 미확정 76). `--show-skips`로 볼 수 있다.
- 🔴 **#172는 열어 뒀다.** 이슈가 요구하는 두 축 중 **빌드타임 생성만** 했고 **런타임 원격
  오버레이**(`models-store.json`·ETag/304·4시간 창)는 없다. 실측: `--list-models`가
  openrouter 418개를 보이는데 카탈로그엔 335개 — 나머지 83개는 **오너가 손으로 채운
  `~/.aelix/agent/models.json`**이다. 즉 그 비용을 이미 사람이 내고 있다.
- 별칭(`UPSTREAM_IDS`)은 손으로 쓴다. **겹침률은 프로바이더가 아니라 카탈로그를 잰다** —
  `openai-codex`↔`opencode`가 93%인데 완전히 다른 서비스다. 임계값 쓰지 말 것.
- 낡은 카운트 4건을 **재유도**했다(올려 맞춘 게 아님). anthropic UNSAT 상한 32000의 근거인
  p25는 309개 중 `sat[77]`로 **여전히 32000**이고 여유는 오히려 넓어졌다.

## 4. 락 게이트(ADR-0233)

`tests/packaging_gate/test_lock_matches_manifests.py`. `uv.lock`의
`requires-dist`/`provides-extras`/`requires-dev`를 매니페스트 5개와 양방향 대조.
c97fa12의 실제 락으로 되돌려 RED 확인함. 못 잡는 것 2개는 docstring에 적혀 있다
(잠긴 버전이 스펙을 만족하는지 · 워크스페이스 `==0.1.0b1` 핀).

## 5. 다음 세션 첫 명령

```
git -C /workspaces/aelix-ai log --oneline -1        # 94ecf4c 인지
git -C /workspaces/aelix-ai status -sb | head -1    # origin/main과 동기 확인
```
그 다음 곧장 §2 (Pages 발행 → 태그).

## 6. 되풀이 금지

- 🔴 **PYTHONPATH 없이 워크트리에서 pytest 금지** — venv의 editable `.pth`가 메인 체크아웃을
  가리켜 false GREEN이 난다. `export PYTHONPATH=$(ls -d <wt>/packages/*/src | tr '\n' ':')`.
- 🔴 **백그라운드 pytest의 "exit code 0"을 믿지 말 것** — 뒤에 붙인 `echo`/`tail`의 코드다.
  **로그 마지막 줄을 읽을 것**.
- 🔴 **출력이 이미 맞는 데이터면 게이트가 눈이 먼다** — `build_row` 사보타주 6건이 GREEN이었다.
  카탈로그를 단언하는 것과 카탈로그를 **쓰는 코드**를 단언하는 것은 다른 일이다.
- 🔴 **사보타주 테스트를 약하게 쓰면 통과한다** — 와이어 필터 테스트의 첫 판은 형제가 전부
  한 와이어라 필터를 지워도 GREEN이었다. 구분되는 입력을 줄 것.
- 🔴 **`ctx.on_partial`은 TUI에 아무것도 안 그린다** · **인용 `--fix`를 믿지 말 것** ·
  **사보타주는 커밋 이후에, 복원은 `cp`, 검증은 git 대조**.
