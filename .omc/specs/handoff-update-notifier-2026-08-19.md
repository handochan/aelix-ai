# 핸드오프 — 업데이트 알림 + #172 A+ (진행 중) + YOLO 스폰 동의 (조사 중)

**2026-08-19. 컨텍스트 컴팩션 직전에 기록.** 이 파일 하나로 이어서 할 수 있게 씀.

---

## 0. 지금 어디에 무엇이 있나

| | |
|---|---|
| 기준 | `origin/main = afdabfe` (베타 잔여 4건 SHIPPED 직후) |
| 워크트리 | **`/workspaces/aelix-upd`** |
| 브랜치 | **`feat/update-notifier-and-catalog`** |
| 상태 | 🔴 **전부 미커밋** — 32 files, +524/−78 |
| 메인 체크아웃 | `/workspaces/aelix-ai` — tracked 변경 없음, 건드리지 말 것 |

**돌고 있던 백그라운드 2개** (컴팩션 후 결과만 확인하면 됨):

1. 전체 스위트 → `/tmp/claude-1000/-workspaces-aelix-ai/62eecac8-bf6e-4a01-9efe-3882d55e1ce4/scratchpad/mine/upd_suite1.log`
   (마지막 확인 시 81%. 첫 줄에 BUILD CHECK + HEAD, 마지막 줄에 `EXIT=`)
2. YOLO 레콘 워크플로 → transcript `…/subagents/workflows/wf_f735fb7f-7bc/`
   (결과가 `"Complete."` 같이 잘려 오면 `agent-<id>.jsonl`에서 가장 긴 텍스트 블록을 꺼낼 것 —
   [[reference_workflow_journal_last_text]])

**모든 명령은 `source /workspaces/aelix-ai/.venv/bin/activate &&`로 시작.**
PYTHONPATH:
```
export PYTHONPATH="/workspaces/aelix-upd/packages/aelix-ai/src:/workspaces/aelix-upd/packages/aelix-coding-agent/src:/workspaces/aelix-upd/packages/aelix-agent-core/src:/workspaces/aelix-upd/packages/aelix-server/src"
```

---

## 1. 오너가 결정한 것 (재논의 금지)

- **#172는 A+** — 문서 수정 + 플래그십 손 추가 + 핀. 생성기/오버레이(#172 전체)는 **안 함**.
- **업데이트: beta.1에 알림, beta.2에 수행.** 수행은 오너가 "직접 수행"으로 결정했고,
  그건 beta.2 몫. beta.1은 **명령어 안내**.
- **피드는 자체 GitHub Pages** (`site/latest-version.json`). GitHub API 안 씀.
- **옵트아웃 = 기본 켜짐 + `--offline` + 설정 키.** 환경변수 신설 금지(ADR-0203).
- 서명 키(Ed25519)는 **GA**로 미룸.
- 그 다음 = **YOLO 스폰 동의** 한 건 더 하고 베타 태그.

---

## 2. 착지한 것 (미커밋)

### 업데이트 알림
- **NEW** `packages/aelix-coding-agent/src/aelix_coding_agent/update_check.py`
  피드 읽기·PEP440 비교·설치방식 판별·24h 캐시. 전부 절대 raise 안 함.
- **NEW** `site/latest-version.json` (schemaVersion 1, `latest` / `latestStable`)
- `tui/shell.py` — `_start_update_check()`(배너 **앞**에서 task 시작) + `_commit_update_notice()`
  (트랜스크립트 재생 **뒤**, first-run 마법사 **앞**에서 4초 타임아웃으로 소비)
- `settings/types.py` + `settings_manager.py` — `check_for_updates` (기본 ON) / `checkForUpdates`
- `tui/settings_rows.py` — "Check for updates" 행 (**소비자와 같은 커밋**, #84 규칙)
- `pyproject.toml`(coding-agent) — **`packaging>=23` 선언** (없으면 기능이 무음 no-op)
- README.md / README.ko.md / SECURITY.md / `args.py` `--offline` 도움말 / RELEASING.md(GA·베타 양쪽에 피드 단계)
- **NEW 테스트**: `tests/test_update_check.py`(38) · `tests/tui/test_update_notice.py`(7, 실물 `run_tui`) ·
  `tests/test_latest_version_feed.py`(4, 피드↔pyproject 대조 게이트)

### #172 A+
- `docs/guides/models-json.md` + 휠 사본 — 깨진 예제 2개 수정, `cost` 규칙 정정, **무음 기본값 표**, 완전한 레시피
- `models_generated.json` — 플래그십 **4개 추가**(`anthropic/claude-opus-5`,
  `github-copilot/claude-opus-5`, `google/gemini-3.6-flash`, `google/gemini-3.7-flash`).
  **102줄 순수 추가, 삭제 0** (재정렬 금지 — copilot은 원래 정렬돼 있지 않음)
- **NEW 테스트**: `tests/test_catalog_corrections_are_pinned.py`(21) ·
  `tests/test_guide_examples_are_valid.py`(6)
- CHANGELOG `[Unreleased]`에 Added 2건 + Fixed 1건

### 🔴 도중에 찾아 고친 것 2개
1. **`tests/packaging/`가 진짜 `packaging` 배포판을 가리고 있었다.** pytest가 `tests/`를
   sys.path에 넣고 그 디렉터리엔 `__init__.py`가 있어서 `import packaging`은 성공,
   `packaging.version`만 실패. `_parse`가 그걸 삼켜 **기능 전체가 무음 no-op**이 될 뻔했고
   테스트는 초록이었을 것. → **`tests/packaging_gate/`로 개명**(git mv), 라이브 참조 7파일 갱신
   (ADR 안의 옛 경로는 사료라 그대로 둠). `test_update_check.py`의 **첫 테스트가 비교기 실재를 직접 단언**.
2. **`packaging`이 선언된 의존성이 아니었다** — 개발 venv엔 pytest 때문에 있었을 뿐.

### 게이트 상태 (마지막 확인)
`ruff` clean · 인용 **834** 무표류(재배치 17건, 내가 안 건드린 9파일은 **줄번호만** 바뀐 것으로 기계 증명) ·
타입 **269파일 / 0오류**

---

## 3. 남은 일 (순서대로)

1. **스위트 결과 확인** (`upd_suite1.log`의 `EXIT=`). baseline은 편집 중에 돌아 **무효**였으므로
   이 실행이 첫 유효 측정. 실패가 있으면 그것부터.
2. **사보타주 라운드.** 하네스 재사용:
   `/tmp/.../scratchpad/mine/sabotage.py` (경로의 `REPO`를 `/workspaces/aelix-upd`로 바꿀 것).
   규칙: 커밋 **후**에, `cp` 백업/복원, **git 대조로 복원 검증**, 프로브 첫 줄 빌드 체크.
   겹친 방어는 **층마다** 따로 — [[reference_sabotage_layered_defences]].
   최소 사보타주 목록: 피드 게이트 무력화 · `_parse` 무력화 · 설정 OFF 무시 ·
   `--offline` 무시 · 캐시 무시(매 실행 fetch) · copilot 비용 0 깨기 · codex 컨텍스트 되돌리기 ·
   가이드 예제 되돌리기.
3. **커밋** (주제별로: 알림 / #172 A+ / `packaging` 섀도잉 / 인용 재배치).
4. **ADR-0230** — 피드를 자체 소유한 이유(GitHub API의 3함정 실측), 알림/수행 분리,
   `packaging` 섀도잉, #172을 A+로 좁힌 근거(재생성이 교정 19/32를 되돌린다는 실측).
5. **머지 + 푸시** — `git fetch` 후 `origin/main`이 `afdabfe`인지 재확인(오늘 두 번 움직인 전례 있음),
   ancestry 확인, force-push 금지.
6. **이슈**: #172에 A+ 결과 코멘트(닫지 말 것 — 파이프라인은 미해결).
   업데이트 알림은 **신규 이슈로 등록**(현재 이슈 없음) 후 닫기.
7. **YOLO 건** (§4) — 이게 끝나면 베타 태그.

---

## 4. YOLO 스폰 동의 (조사 중, 미착수)

**제보**: `--permission-mode yolo`인데도 멀티에이전트 스폰마다 승인 패널이 뜬다.
**확인됨(내가 직접 측정)**: `grants_write_authority(yolo) == True` →
`consent_is_required`의 첫 분기가 참 → 다이얼로그가 항상 뜬다. **기존 이슈 없음.**

읽어둘 것:
- `packages/aelix-coding-agent/src/aelix_agents/consent.py::consent_is_required` — "THE SINGLE
  SOURCE OF TRUTH", 두 문이 반드시 이걸 호출. 시그니처 `(resolved, clamped, *, has_ui)`.
- `aelix_agents/posture.py::grants_write_authority` — 랭크 비교.
  default/plan False, auto-accept-edits/yolo/auto True.
- ⚠️ `consent_is_required` docstring의 *"IT TAKES NO `mode`, AND THAT IS THE DECISION"* 에서
  `mode`는 **SubagentMode(single/chain/batch)**이지 PermissionMode가 아니다. 혼동 금지.

**설계상 유리한 점**: ADR-0197이 consent를 **확장 정책**으로 못박아 뒀으므로 `aelix_agents/`
안에서 끝난다(밴드 게이트 `tests/agents/test_p2_band_boundaries.py`가 강제).
헤드리스 부모는 **이미 스스로 동의**한다 — 같은 기전을 재사용할 수 있는지 확인 중.

**결정해야 할 것 2개** (레콘 결과 나오면 오너에게):
1. 키를 **부모가 YOLO**로 잡을지, **자식이 YOLO로 clamp**될 때로 잡을지.
   프로필 `approval_mode`가 자식을 낮게 조일 수 있어 둘이 갈린다. 사용자 기대는 거의 확실히 전자.
2. **체인(chain)도 없앨지.** 코드 주석이 "체인은 자식이 이전 자식이 만든 텍스트를 받으므로
   `Cancel`이 진짜 답"이라고 명시적으로 반대 논거를 적어 뒀다. 단일 스폰과 다른 위험 구조.

**바뀌면 안 되는 것** (레콘이 하나씩 구동 검증 중): clamp(자식이 부모를 못 넘음) ·
자식 안에서의 가드레일 하드 차단 · `plan` 자식의 무변경 · 프로젝트 신뢰 게이트.

---

## 5. 되풀이하지 말 것

- 🔴 **편집 중에 스위트를 돌리지 말 것.** 오늘 그래서 baseline 하나를 버렸다.
- 🔴 **`models_generated.json`을 재정렬하지 말 것.** github-copilot은 정렬돼 있지 않아
  블랭킷 sort가 142줄을 움직였다. 형제 **바로 뒤에 삽입**할 것.
- 🔴 **`load_custom_models`의 반환 필드로 검증 여부를 판단하지 말 것.** 내 첫 프로브가
  "예제 3개 다 OK"라고 읽었지만 실제 검증기(`validate_models_config`)는 2개를 거부했다.
- 🔴 스키마 오류는 **models.json을 통째로 버린다**(`empty_custom_models_result`).
- 오너 결정을 다시 묻지 말 것(§1).
