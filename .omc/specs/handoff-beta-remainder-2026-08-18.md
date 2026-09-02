# 핸드오프 — 베타 잔여 `#189 · #111 · #190 · #84` SHIPPED (2026-08-18)

**읽을 순서: §0(반증된 전제) → §5(남은 것) → §6(다음).**
직전 핸드오프는 `.omc/specs/handoff-s1-184-186-2026-08-18.md`(#184/#186), 정본 일정은
`.omc/specs/release-roadmap-2026-08-18.md`.

- 브랜치 `beta/189-111-190-84` (워크트리 `/workspaces/aelix-beta`), 시작 `origin/main = a5b45ac`
- 스위트 **baseline 8995 → FINAL 9026** passed / 1 skipped
- 사보타주 **18/18 RED** (1라운드 15/17 → 눈먼 게이트 2건 수정 후 18/18)
- 게이트: `ruff check .` clean · 인용 **834** gated 무표류 · 밴드 7 · 타입 **268파일 / 0오류**
- ADR-**0229** (0228 인덱스 행도 백필)

---

## 0. 🔴 실측이 반증한 전제 — 이 트랙의 핵심

네 이슈 중 **세 개의 헤드라인이 틀렸습니다.** 모양은 매번 같습니다: 게이트는 있었고, 자기가
만들어진 일을 정확히 했고, **엉뚱한 곳을 보고 있었습니다.**

| 전제 | 실측 |
|---|---|
| #190: sdist 10.2MB, GOV.UK 캡차 + 로고초안 3.7MB + 스크래치 HTML | **전부 미추적.** `release.yml`은 `actions/checkout`(클린 클론)에서 빌드 → 릴리즈 산출물엔 **0건**. 실제 6,343,886 B. 잡파일을 되돌려 넣고 재빌드하면 10,308,093 B로 이슈 숫자가 재현됨 = **더러운 작업트리 빌드**였다. 진짜 초과분은 `tests/` 551개(24.5%) |
| #190: "레포에서 잡파일 3개를 삭제하라" | git에 **지울 게 없다.** 메인 체크아웃의 미추적 파일이고 `rm`은 오너 몫 (§5) |
| #188: "가드레일 바닥은 남는다 — catastrophic 패턴은 여전히 하드 차단" | **반증.** 가드레일 규칙 **7개 전부**가 permission과 **동일한 8개 바닥이름** 집합에 묶여 있고 `applies_to_tools=None`은 **하나도 없다**. MCP 경로에선 두 그물 다 없다 |
| #84: "11개 inert 행의 문구를 완화하라" | **범위가 역전.** 완화는 2026-07-31에 이미 shipped. 12일 뒤 #115(871a6be)가 `enable_skill_commands`를 **배선**하고 문구를 안 되돌렸고, **테스트가 그 거짓 문장을 요구**하고 있었다(`assert "not yet wired" in help_text`). 10개는 여전히 inert |
| #189: `_outcome_reported`가 준비된 seam | **아니다.** `_render_turn_abort`가 `turn_end`에서 **read-and-clear**하고, 그건 재raise가 셸에 닿기 **전에** 발화한다. 그 플래그로 만든 수정은 맞아 보이는 no-op이었을 것 |
| #111 B-1 `aelix auth` 안내 | 이미 `fc91cdd`에서 제거됨. A-1/2/3, B-1/2/3/5/6/7 전부 **완료돼 있고 체크만 안 됨** |

## 1. #189 — 진짜 결함은 문자열이 아니라 **게이트 부재**

TUI 제출 경로가 **runnability 게이트가 없는 유일한 턴 진입점**이었습니다. `/model`·픽커·
`/agents use`·print/json은 전부 막는데, 사용자가 타이핑해서 닿는 그 경로만 루프로 직행했습니다.

**중복 출력은 #189 고유가 아닙니다.** `harness/core.py`가 루프를 탈출하는 예외를 잡아
`AssistantMessage(stop_reason="error")`를 합성해 `message_end`를 쏘고(렌더러가 출력)
**같은 예외를 재raise**합니다(셸이 또 출력). 그 블록 주석은 "추가되는 건 이벤트뿐"이라고
적어 두었는데, **그 이벤트가 두 번째 사본**입니다. 스택 프로브 + 격리 사다리(2/1/1/0)로 측정.

- 커널 메시지는 짧아지고, 배선 설명은 `raise` 위 **docstring**으로 이동(터미널에 절대 못 감)
- 셸은 렌더러가 커밋한 **텍스트**를 비교해 중복만 억제 — bool이 아니라 텍스트라서, 다르면
  **다른 오류**이므로 여전히 출력. 실패 방향이 "두 번"이지 "안 나옴"이 아님
- 게이트 2레인: 행동(`run_tui` 실물 구동) + 정적(`raise` 인자 AST)

🔴 **정적 게이트를 `raise` 인자로 좁힌 이유는 미학이 아니라 측정입니다.** 전 리터럴
16,973개에 대해 4개 토큰은 ~24건 매치·**오탐 ~83%**(`__all__`의 `register_all` 6개, private
dict, pydantic `Field(description=)`, 기계용 RPC 페이로드). `raise` 인자로 좁히면 774개 중
**3건, 오탐 0**. 3건 다 이 배치에서 고쳤으므로 **allowlist가 비어 있습니다.**
알려진 갭(간접호출 비가시 · glass에 닿는 다른 3개 모양 · **횟수는 정적으로 표현 불가**)은
파일에 적혀 있습니다.

**미수정(별건)**: 합성 실패 메시지가 같은 텍스트를 `content`와 `error_message` 양쪽에 담고
**영속**되므로 `/resume`하면 그 쌍이 또 보입니다. 게이트가 있으니 #189 경로는 이제 영속되지
않고, 합성 메시지 모양 변경은 세션 데이터 변경이라 정직성 배치의 범위가 아닙니다.

## 2. #190 — 🔴 위생 게이트가 **오너의 `.env`를 tarball에 쓸고 있었다**

`build_tree`가 복사본의 `.gitignore`를 **의도적으로** 지웁니다(그래야 pyproject `exclude`가
유일한 방어선이 되어 실제로 시험됩니다). 그 귀결을 아무도 `.env`까지 따라가지 않았습니다 —
억제가 풀리는데 `_is_developer_state`가 안 잡았습니다. **로컬에서 스위트를 돌릴 때마다
`ANTHROPIC_API_KEY`·`GH_TOKEN`·`PYPI_TOKEN`이 든 tarball이 pytest 임시 디렉터리에 쓰였고
모든 단언이 통과**했습니다. CI는 `.env`가 없어서 못 봤고, 그게 살아남은 이유입니다.

3중으로 닫음: `_COPY_SKIP`이 진짜 파일을 복사하지 않고, **가짜**를 심어 단언이 잡을 대상을
유지하고, `.env`/`.env.local`을 10개 exclude 리스트 + `REQUIRED_EXCLUDES`에 추가.

**새 5번째 픽스처가 필요했던 이유**: 기존 4개는 라이브 작업트리 `copytree`라 크기/인벤토리가
비결정적입니다(같은 커밋으로 6,341,061 B와 10,325,521 B를 실측). 파일 자신의 docstring이
"HOW BIG IS NOT A FIXED NUMBER"라고 적은 건 **그 소스에 대해 옳습니다.** `git ls-files` 시드
빌드는 추적 상태의 순수 함수라 둘 다 단언 가능해지고, **`release.yml`이 퍼블리시하는 것을
빌드하는 유일한 픽스처**이기도 합니다.

인벤토리는 **정확한 allow-list**(known-bad 아님). `_is_developer_state`는 진짜 게이트지만
`tests/`는 developer state처럼 안 생겨서 551개를 통과시켰습니다. 이력 93일 기준 allow-list는
**약 13일에 한 줄** 수정 — 언제나 의도적인 새 최상위 파일 옆에서.

`tests` 제외 결과: 6,343,886 → **4,756,148 B / 605 멤버**, 그리고 그 sdist에서 만들어지는
휠은 **바이트 동일**(멤버 리스트도 동일). 남은 큰 것은 `docs/assets/demo.gif` **2.0MB**.

## 3. #84 — 배선된 행이 12일간 자기를 inert라고 말했다

`settings_rows.py`의 블록 주석이 대문자로 경고하던 바로 그 역전입니다.
고친 것은 한 행 + **그걸 잡았을 것**: 손으로 관리하던 두 집합(`INERT_ROWS`,
`WIRED_PERSIST_BLOCK_ROWS`)을 **production 호출부 AST 스캔과 대조**합니다. grep이 아니라
AST인 이유 — `tui/shell.py` docstring이 `get_enable_skill_commands()`를 산문으로 언급해서
substring 스캔은 inert 행을 wired로 보고합니다(정확히 이 게이트가 막는 역전).

## 4. #111 — 문서 정직성

1. **README의 "두 보증"이 양쪽 다 거짓** (§0). EN/KO 둘 다 수정. `tests/docs/`가 좁힌 주장을
   **코드에 고정** → #188을 고치면 테스트가 RED가 되고 문구를 같은 커밋에서 갱신하게 됨.
   빠져 있던 한계 2건 추가: **#137**(한 세션에 터미널 하나) · **#138**(기록은 살균 없이 영구).
2. 🔴 **`aelix --export`가 0600을 0646으로 넓히고 있었다.** `path.write_text` 한 줄. #138의
   구체적 잔여물이고, #138 헤드라인("세션 JSONL 노출")은 과장 — 저장소는 멀쩡하고 export가
   문제였습니다. mode로 **생성**(chmod 후처리 아님) + `O_TRUNC`가 기존 mode를 남기므로 chmod도.
3. 헤드리스 안내 2개에 **상호작용 경로가 없었다.** `aelix -p`는 `/model`만 말했는데 그건
   자격증명이 여는 모델 픽커라 그 독자에겐 비어 있고, `--provider X` + 무키는 **아무 경로도**
   말하지 않았습니다. `cli/list_models.py`가 이미 "run 'aelix' and use /login"으로 풀어둔
   문제였습니다. 🔴 `test_entry_router.py`의 단언 2개가 *"Aelix does not register one"*이라며
   `/login` **부재를 고정**하고 있었습니다 — `tui/commands.py`에 `BuiltinCommand("login", …)`이
   있고 첫 실행 마법사가 #23부터 그걸 안내해 왔습니다.
4. `docs/guides/tls-and-corporate-ca.md` 신규(B-9 문서 절반) — 휠에 실려 폐쇄망에서도 읽힘.

## 5. 남은 것 / 오너 판단

- **오너 전용**: A-4 태그 발사 · B-5(About — 이미 채워짐) · B-8(카탈로그 비어 있고 미서명,
  `FIRST_PARTY_KEYS = {}`) · B-10(Copilot 좌석 ToS 서면) · B-9의 #98/#99 리포터 재확인
- **B-4**: 필수 3개(SECURITY.md, bug_report.yml, config.yml)는 완료. `CONTRIBUTING.md`는
  이슈에서 `(선택)`, CODE_OF_CONDUCT·PR 템플릿은 체크박스가 아니었음 → 베타 범위 밖으로 둠
- 🔴 **메인 체크아웃의 미추적 잡파일 (삭제는 오너가)**: `/workspaces/aelix-ai/`에
  `uk4414917.html` 717,716 B · `ax.html` 114 B · `te.html` 0 B · `docs/assets/archive/` 8파일
  3.6MB. **git에 없으므로 릴리즈엔 안 실립니다.** 로컬 `uv build`만 집어갑니다
- **미해결 신규**: 합성 실패 메시지의 replay 중복(§1) · `docs/assets/demo.gif` 2.0MB 트림
- **GA로 남김**: #188(capability 게이팅) · #137(세션 분기) · #138(리댁션 설계)

## 6. 다음

로드맵상 베타 태그 `v0.1.0-beta.1`의 코드/문서 블로커는 **이제 없습니다**. A-4(태그 발사)는
오너 작업이고, 그 직후가 `install.sh` curl|sh 경로의 첫 실검증입니다(현재 `releases/latest`가
404). 그다음은 GA 트랙 = **#73 → #142 → #103 → #188 → #137**.
