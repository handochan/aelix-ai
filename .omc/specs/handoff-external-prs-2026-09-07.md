# 다음 세션 핸드오프 — 외부 기여자 PR 3건 정리 (2026-09-07)

이 세션은 코드 이슈가 아니라 **외부 기여 대응**을 작업 단위로 잡았다. 기록상 처음이다.

## 0. 기준점

- `main` = `df79ceb` (푸시 완료). CI run `34131320894` **6잡 전부 success** — ubuntu
  py3.11/3.12, **windows py3.11/3.12**, install.ps1 e2e (pwsh/powershell).
- 로컬 전체 게이트(darwin, py3.12, `uv sync --all-packages`, import 출처 워크트리 검증):
  `ruff` clean · 타입 게이트 `0 errors across 276 files` · **10262 passed, 12 skipped, 0 failed**.
- 계획 정본 없음 — 이 작업은 보드 항목이 아니었다(§3 참조).

## 1. 이번에 한 것

| PR | 기여자 | 결과 |
| --- | --- | --- |
| #119 | `Mr-Neutr0n` | **머지** (`e109749` + `df79ceb`). #92 close, 보드 Done |
| #217 | `amasen02` | 이미 기여자가 닫음 → 감사·설명 코멘트만 |
| #223 | `mikemikimike` | 코멘트 후 close (`778d352`로 대체됨) |

### #119 — 레포 최초의 외부 기여, 36일 만에 머지

기여자 커밋 2개(`d926c80`, `8353178`)를 **리라이트 없이** 머지했다. `_harness_factory`가
`reload_seed`에서만 `flag_values`를 시드해서 첫 빌드에 `None`이 가던 것을
`parsed.unknown_flags or None`로 고친다.

- **라이브 확인(규칙 10)**: 실제 `uv run aelix -e probe.py --print`로
  플래그 없음 → `'DEFAULT'`/`False`, `--probe-flag FROM_CLI --probe-bool` → `'FROM_CLI'`/`True`.
- **negative control**: `entry.py` 훅만 되돌리면 기여자 테스트 4개 중 3개가
  `assert 'DEFAULT' == 'hello'`로 red. 4번째는 반대 방향(선언 기본값 보존)을 지켜서 양쪽 다 green.
- 머지 커밋에 `Co-Authored-By: Mr-Neutr0n` — **이 레포 최초의 인간 공동저자**
  (기존 708개 트레일러는 전부 모델).
- 문서: `extension-authoring.md`의 "Flags are declared but not settable" 절과 API 표 행을
  라이브 측정값으로 재작성. `docs/guides/`와 휠 번들 사본 **양쪽**(ADR-0218 sync 게이트).
- ADR-0218 Consequences의 `register_flag` 불릿에 blockquote 주석 추가 —
  **결정은 무효화되지 않았고**, 인용된 사실 2개가 stale해진 것만 표시.

## 2. 바로 시작할 것

**오너 판단이 필요한 것 하나 — fork CI 승인 정책.** 이게 3건 전부를 물었다.

```
gh api repos/handochan/aelix-ai/actions/permissions/fork-pr-contributor-approval
  → {"approval_policy":"first_time_contributors"}
```

완화 명령(되돌리기도 한 줄):

```bash
gh api -X PUT repos/handochan/aelix-ai/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=first_time_contributors_new_to_github
```

**완화가 안전한 근거(측정)**: `ci.yml`은 `pull_request`로만 트리거되고
(`pull_request_target` 없음), `secrets.` 참조가 **0건**이다. 즉 fork 런은 read-only 토큰에
크레덴셜 없이 돈다. 남는 리스크는 런너 분(公개 레포는 무료)의 남용뿐이고,
`..._new_to_github`는 그 프로필(신규 계정)을 여전히 막는다.

그 다음:

1. `CONTRIBUTING.md` 커밋 (이 세션에서 작성, 아직 미커밋).
2. **브랜치 보호** — `gh api repos/handochan/aelix-ai/branches/main/protection` → 404.
   CI가 머지를 막지 않는 건 `next-session-handoff-pr119-and-91.md`가 07-31에 이미 지적했고
   아직 그대로다. 오너 설정이라 손대지 않았다.
3. 원래 다음 작업이던 **#230 / #232** (`7fa6796` 핸드오프 기준).

## 2.5. 보류된 오너 결정 2건 — 시한 없음, 그러나 하나는 지금 살아 있다

세션 끝에 오너가 **의도적으로 미룬** 것이다. 급하지 않지만 잊으면 안 된다.

### (a) fork CI 승인 정책 — 현행 `first_time_contributors`

세 선택지와 이 레포 기준의 결과:

| 값 | 승인 필요 대상 | 결과 |
| --- | --- | --- |
| `all_external_contributors` | 외부인 전원, 매번 | 오늘보다 나쁨. 권하지 않음 |
| `first_time_contributors` (현행) | 이 레포에 커밋 없는 사람 | 3명 전부를 문 값. 단 Mr-Neutr0n은 `e109749`로 커밋이 들어가 **다음 PR부터 자동 실행** |
| `first_time_contributors_new_to_github` | GitHub 계정 자체가 신규인 사람 | 권고값. 기성 계정은 즉시 실행, 일회용 신규 계정은 계속 차단 |

권고 근거(측정): `ci.yml`은 `pull_request`만 쓰고 `secrets.` 참조가 **0건**이라,
게이트가 막도록 설계된 위험(시크릿 탈취)이 **여기엔 없다**. 남는 건 무료 런너 남용뿐.
반대 논거도 유효함 — 이 레포 CI는 `install.ps1`을 실제 실행하고 테스트가 서브프로세스를
스폰하므로 "첫 실행만은 사람이 본다"는 선택도 방어 가능하다.

### (b) auto-merge — 현행 `allow_auto_merge=false`

**주의: 이건 이번 변경으로 꺼진 게 아니라 원래부터 레포 설정으로 꺼져 있었다.**
브랜치 보호는 auto-merge를 막는 게 아니라 **쓸모 있게 만드는** 쪽이다(요구조건이 없으면
기다릴 게 없음). 켜면 외부 PR에 auto-merge를 한 번 걸어두고 CI 초록이면 사람 개입 없이
머지된다. 리뷰 승인은 어느 쪽이든 불필요하다.

```bash
gh api -X PATCH repos/handochan/aelix-ai -F allow_auto_merge=true
```

### (c) Private Vulnerability Reporting — 현재 **꺼짐**, (d)와 독립적으로 켤 수 있다

```
gh api repos/handochan/aelix-ai/private-vulnerability-reporting  ->  {"enabled": false}
```

이건 전용 주소 결정과 **묶이지 않는다.** 켜는 것만으로 보안 쪽 이메일 필요가 사라진다:

- 무료, 설정 하나, 유지비 0. 구조화된 비공개 스레드.
- **CVE 발행과 advisory 크레딧을 지원한다.** `SECURITY.md:213`이 이미
  *"Reporters will be credited in any advisory that does get published"* 라고
  약속해 뒀는데 **이 설정 없이는 지킬 수 없는 약속**이다.
- `SECURITY.md:35-40`에 오너 본인이 *"the right long-term route"* 라고 적어 뒀고,
  같은 blockquote가 *"this section should be rewritten the day it changes"* 라고 못박아 뒀다.

```bash
gh api -X PUT repos/handochan/aelix-ai/private-vulnerability-reporting
```

켠 날 할 일: `SECURITY.md`의 "How to report, right now" 절(내용 없는 공개 이슈로 노크)과
35-43행 blockquote 2개를 지우고 "Report a vulnerability 버튼"으로 교체.

### (d) CoC 전용 연락 주소 — 아직 없음

**개인 Gmail을 쓰지 말 것.** 측정: 오너 커밋 이메일 분포는
noreply `65016626+handochan@…` **735건** vs `handc96@gmail.com` **12건**
(2026-07-31~08-07 한 구간뿐, 최근 100커밋 중 0건). 즉 gmail 노출은 **선택이 아니라 누출**이고,
그걸 근거로 문서화된 창구로 승격시키는 것은 방향이 거꾸로다. CoC 창구는 성격상
적대적인 메일이 오는 곳이므로 더더욱 그렇다.

| 방법 | 비용 | 평가 |
| --- | --- | --- |
| 도메인 + Cloudflare Email Routing | 도메인만(~$10-15/년), 포워딩 무료 | **권고.** CoC 창구 + 보안 백업 + Pages 커스텀 도메인(현재 `handochan.github.io`)을 한 번에 |
| 전용 Gmail 신규 | 무료, 5분 | 실용적. CoC에 필요한 건 "실제로 읽히는 비공개 주소"이지 도메인이 아니다 |
| SimpleLogin/addy.io 별칭 | 무료 | 가장 빠르지만 창구로는 신뢰도가 낮게 읽힘 |

주소가 생기면 들어갈 곳 2개: `CODE_OF_CONDUCT.md`의 Enforcement 절,
그리고 `SECURITY.md:42-43`의 *"A dedicated security e-mail address has not been chosen either."*

**왜 CoC가 SECURITY.md보다 더 급한가 (이 논리를 잃지 말 것):** 두 문서가 같은
"내용 없는 공개 이슈로 노크" 경로를 쓰지만 성격이 다르다. **보안 신고는 코드에 대한
것**이라 내용 없는 이슈가 아무것도 드러내지 않는다. **CoC 신고는 사람에 대한 것**이라,
작은 프로젝트에서 "비공개 채널을 요청합니다"라는 공개 이슈는 신고자가 사실상 특정되고
신고 대상에게 미리 알리는 효과까지 난다. 보안 쪽에 없는 위축 효과다. 현재 CoC 문안은
**정직하지만 차선**이며, 그건 알고 택한 것이다.

### 미루는 비용

만료될 fork CI 런이 없으므로 **시한은 없다**. 살아 있는 영향은 하나뿐:
브랜치 보호가 이미 켜져 있어서 **새 외부 PR은 승인 클릭 전까지 머지가 차단**된다.
이전에는 "빨간 X"였던 것이 지금은 "차단"이다.

## 3. 이 레포에서 이번에 물린 것

- **`git status --porcelain`을 awk로 거르면 안 된다.** unstaged는 `" M file"`이라
  `$1==" M"`이 절대 매칭 안 되고, awk는 공백을 접어서 `$1`이 `"M"`이 된다. 이것 때문에
  머지 커밋이 **lock만 갱신되고 주석은 안 갱신된 불일치 상태**로 한 번 만들어졌다.
- **citation 게이트는 워킹 트리를 읽는다, HEAD가 아니라.** 그래서 커밋이 깨져 있어도
  트리에 수정본이 있으면 green이 나온다. 커밋 자기일관성을 보려면 나머지 변경을
  `git stash`해서 트리를 그 커밋과 동일하게 만든 뒤 돌려야 한다.
- **`tests/cli`만 돌리면 citation drift를 못 잡는다.** 첫 검증에서 이걸 놓쳤고 CI가 잡았다.
  줄 수를 바꾸는 패치는 전부 `scripts/check_citations.py --fix`가 필요하다.
- **worktree에서 `uv sync`는 부족하다 — `--all-packages`가 있어야 한다.** 없으면
  수집 단계에서 `ModuleNotFoundError: No module named 'aelix_server'`로 죽는다.
  (기존 handoff의 PYTHONPATH 함정과는 **다른** 함정이다. 둘 다 있다.)
- **워크플로 서브에이전트가 브랜치·worktree를 남긴다.** 프롬프트에 정리를 지시해도
  남았다. 세션 끝에 `git worktree list` / `git branch -a`로 확인할 것.

## 4. 반증된 것 — 다시 믿지 말 것

- **"1.8x test:prod"** — 오너가 #119 리뷰문에 쓴 수치. 실측은 **1.57x**
  (tests 190,260줄 / packages/*/src 121,371줄, 2026-09-07). `CONTRIBUTING.md`에는
  실측값을 넣었다.
- **"#223은 스팸 계정"이 아니다.** fork 610/614개에 PR 910건이지만 **머지율 35.4%**이고,
  타깃 테스트는 실제로 통과하며 손자 프로세스 회귀 테스트는 목이 아니라 진짜다.
  본문 주장 중 `206 passed`·ruff·타입 게이트는 **정확히 재현됐다**.
- **"#223 본문의 Windows 9719 passed"는 검증 불가.** 9719+73=9792인데 head 수집은 10057 —
  265개가 설명되지 않고, 제시된 사유(POSIX terminal / symlink)가 그 수를 못 채운다
  (`pty|termios` 테스트 0개, symlink 24개). 같은 base의 실제 windows CI는 **9982/71**.
  동기가 아니라 산술 문제다.
- **"오너가 #223을 스쿱했다"는 틀렸다.** #221 본문이 PR보다 **2시간 13분 먼저** 헬퍼 이름과
  모듈(`aelix_ai.utils._process_tree`의 `run_contained`)을 지정했다. 다만 **구현이 앞선 건
  아니다** — `778d352` author date는 PR보다 15.7시간 뒤다. "이미 진행 중이었다"고 쓰면 안 된다.
- **머지를 깬 건 기여자가 아니다.** `b403f1c`는 열릴 당시 `39549b9`에 대해 merge-tree clean이었다.
  `6509ea8`(#202)이 깼고 `fc567ad`(#222)가 파일을 삭제했다.

## 5. 하지 말 것

- **#217·#223을 재오픈하지 말 것.** 둘 다 정확한 이유로 닫혔다. 재오픈은 사과가 아니라 부담이다.
- **`305a92d`를 리라이트해서 amasen02에게 소급 크레딧을 넣지 말 것.** 이미 main에 있고
  기여자 코드가 실제로 쓰이지도 않았다. 크레딧은 코멘트로 줬다.
- **`CONTRIBUTING.md`에 응답 SLA를 쓰지 말 것.** SECURITY.md의 선례를 따라
  "약속하지 않는다 + 그 이유"로 썼다. 대신 **"답 없이 닫거나 방치하지 않는다"**를 의무로 적었다.
- **ADR-0218의 원래 측정 기록을 지우지 말 것.** 작성 시점에 참이었고, blockquote 주석으로만
  표시했다. 결정이 바뀐 게 아니다.
