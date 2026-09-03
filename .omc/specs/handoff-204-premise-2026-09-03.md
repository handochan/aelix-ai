# 다음 세션 핸드오프 — #204 전제 정정 완료 (2026-09-03, 3차)

`handoff-windows-leg-2026-09-03b.md` §2의 "🔴 먼저" 항목을 끝냈다. 그 문서의 나머지(잔여 143건
클러스터 이슈, 이후 순서)는 그대로 유효하다.

## 0. 기준점

| | |
| --- | --- |
| `main` | `5d26d73` (origin/main은 `9cd7e53` — **push 안 했다**, 오너 판단) |
| 로컬 스위트 (darwin) | `1 failed / 9370 passed / 12 skipped` @ `9cd7e53` — 실패 1건은 여전히 `test_config_from_env_overrides`(darwin `/private/tmp`), 이 세션과 무관 |
| 게이트 @ `5d26d73` | ruff 0 · check_types 0 · check_citations 0 · `tests/builtin/` 275 passed |
| 계획 정본 | `handoff-windows-leg-2026-09-03b.md` §2 (이 문서는 그 첫 항목의 종결 기록) |

`5d26d73`은 동작 변경이 없는 docstring·spec 정정이다. 전체 스위트는 `9cd7e53`에서 돌렸고
`5d26d73`에서는 `tests/builtin/`만 돌렸다.

## 1. 이번에 한 것

- **#204 이슈 본문 교체** (gh issue edit). 정정 이력 블록 + 실측표 + 수용 조건 5개 추가.
- 거짓 주장("`Remove-Item` → ALLOW")이 남아 있던 4곳 정정: `permission.py` `_auto_classify_bash`
  docstring, `test_permission_shell_competence.py` 두 docstring, `windows-support-prep-2026-09-03.md`
  (제자리 수정), `handoff-n3-stdio`·`handoff-windows-p0` (원문 보존 + 날짜 붙인 🔴 정정 노트).
- 리뷰 3패스: Claude verifier → **Codex `codex exec`**(BLOCKING 7) → Claude critic(BLOCKING 2). 전부 반영.

## 2. 바로 시작할 것

**잔여 windows 143건의 클러스터 이슈 생성** — `handoff-windows-leg-2026-09-03b.md` §2 표 그대로.
가장 싼 것은 `SystemRoot` 14건. 제품 결함 0건이라는 판단은 그 문서 기준이며 이 세션은 건드리지 않았다.

## 3. 이후 순서

`#108 F-4 → #105 → #202 → #107 후반 → #201 → #46`. 병렬 트랙 #204는 이제 본문이 맞으니 착수 가능
— ADR은 **0237**부터(착수 시 `docs/decisions/` 재확인, 오너가 세션을 동시에 돌린다).

## 4. 이 레포에서 이번에 물린 것

1. **정정문 자체가 새 오류를 담는다.** 첫 초안은 "#204가 DENY 제거를 제안했다"(설계 패스가 했다,
   이슈는 언급 없음)와 "ALLOW 외 모든 티어는 인자를 본다"(`_DENY_COMMANDS`·`_ALWAYS_ASK_COMMANDS`도
   이름만 본다 — ALLOW는 이름만으로 **허용**하는 유일한 티어)를 넣었다. 둘째는 `0be16cd`의
   `_READ_ONLY_ONLY_WHEN_BARE` docstring 원문부터 틀려 있었고 그대로 복사됐다. **정정을 쓸 때도
   각 문장을 grep/실행으로 확인할 것.** 리뷰어가 아니었으면 그대로 커밋됐다.
2. **Codex 교차 리뷰가 실제로 잡았다.** Claude verifier가 통과시킨 초안에서 Codex가 시제(과거 결함을
   현재형), 범위("AUTO 강등" ≠ "ALLOW 판정 강등"), dialect 혼합(`hostname`은 모든 플랫폼에서 변이,
   `date` 시계 변경은 cmd 전용 — PowerShell `date`는 `Get-Date`), 미측정 별칭 목록, 수용 조건 부재를
   짚었다. 형태: `codex exec --sandbox read-only "$(cat prompt.md)" < /dev/null`. 21분 걸렸다.
3. **이슈 본문에 줄 번호를 넣지 말 것.** 같은 커밋의 docstring 편집이 인용한 줄을 밀어냈다
   (`permission.py:675` → 커밋 후 68x). 심볼(`_auto_classify_bash`)로 인용했다. `check_citations.py`가
   잠그는 코드 내 인용과 달리 이슈 본문은 아무도 relock하지 않는다.
4. `citations.lock.json` relock은 마지막에 **한 번**만 (`--fix`). 새 인용 1건이 그렇게 잠겼다.

## 5. 반증된 것 — 다시 믿지 말 것

- ❌ "ALLOW 티어만 이름으로 판정하고 다른 티어는 전부 인자를 본다." — DENY·ASK에도 이름만 보는
  테이블이 있다. 맞는 문장은 "이름만으로 **허용**하는 티어는 ALLOW뿐".
- ❌ "`curl … | iex`는 pipe-into-shell DENY에 걸린다." — `iex`가 `_SHELLS`에 없어 **ASK**다
  (`curl … | sh`는 DENY). 이슈 본문의 ASK→DENY 목록에서 가장 구체적인 항목으로 기록했다.
- ❌ "PowerShell 별칭이 오권한을 만든다." — `_READ_ONLY` 중 PowerShell 기본 별칭인 이름은 전부
  읽기 전용 cmdlet으로 매핑된다(critic 실측). 측정된 별칭 오권한은 0건. 정밀도 항목이지 안전성
  항목이 아니다.

## 6. 변하지 않은 금지 사항

🔴 PUBLIC 저장소. `.omc/specs/recovery-report-dead-session-91.md`와 `docs/assets/*` 10건은
여전히 **의도적으로 미커밋**. `git add -A` 금지, 경로를 명시할 것.
