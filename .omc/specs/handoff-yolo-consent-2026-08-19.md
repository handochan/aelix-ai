# 핸드오프 — YOLO 스폰 동의 (#196) · 커밋 완료, 병합 대기

**2026-08-19.** 앞선 트랙(업데이트 알림 + #172 A+)은 **이미 main에 병합·푸시됨** (`c97fa12`).

---

## 0. 상태

| | |
|---|---|
| 워크트리 | `/workspaces/aelix-upd` |
| 브랜치 | `fix/yolo-spawn-consent` (기준 `origin/main = c97fa12`) |
| HEAD | `8c2f266` — **5커밋, 전부 커밋됨, 트리 클린** |
| 남은 것 | 최종 전체 스위트 → 병합 → 푸시 → #196 코멘트+닫기 → 베타 태그 |

커밋:
```
8c2f266 docs: ADR-0231 records the two sentences that stayed false, and the sabotage tally
e50d1a4 test(agents): gate the two sentences the last commit fixed
ae61d4f test(agents): three more blind gates, and two sentences that were left false
a0015ce fix(agents): the disclosure that replaced the YOLO dialog was invisible
8aa5137 feat(agents): a YOLO parent is told about a delegation, not asked (#196)
```

**모든 명령은 `source /workspaces/aelix-ai/.venv/bin/activate &&`로 시작.**
🔴 **PYTHONPATH 없이 pytest를 돌리면 메인 체크아웃을 임포트해 false GREEN이 난다** (venv의 editable
`.pth`가 `/workspaces/aelix-ai`를 가리킴). 반드시:
```
export PYTHONPATH="/workspaces/aelix-upd/packages/aelix-ai/src:/workspaces/aelix-upd/packages/aelix-coding-agent/src:/workspaces/aelix-upd/packages/aelix-agent-core/src:/workspaces/aelix-upd/packages/aelix-server/src"
```

---

## 1. 오너 결정 (재논의 금지)

**YOLO 부모면 스폰 승인 모달 없음 — single·parallel·chain 전부.** 대신 **비차단 고지 한 줄**.
`auto-accept-edits`/`auto`는 그대로 물어봄. 이는 **OC-1의 YOLO 셀만 반전**하는 것.

---

## 2. 착지한 것

- `consent_is_required(resolved, clamped, parent, *, has_ui)` — `parent is YOLO → False`.
  호출처 3곳(consent.py 두 도어 + `extension._grant_for`) 모두 `parent`를 이미 갖고 있었음.
- `disclosure_is_required(parent, clamped)` = `parent is YOLO and grants_write_authority(clamped)`
- `build_disclosure_line(...)` → `SpawnGrant.disclosure` (`_sanitize_field` 적용)
- 고지 = **statusline 행** (`SubagentProgressBridge.announce` / `disclosure_status_key`),
  두 도어 모두: 모델 도어는 `extension.py`, `/agents run`은 **`SubagentHost.on_disclosure`** 훅
- 문서: CHANGELOG(Unreleased + beta.1 **제자리 정정**) · README(.ko) · agent-profiles.md(양쪽 사본) ·
  SECURITY.md Scope · `MODE_META[YOLO].description` · **`tool.py`의 모델용 설명** · ADR-0231 · ADR-0199 정정

---

## 3. 🔴 이번에 배운 것 (되풀이 금지)

1. **`ctx.on_partial`은 TUI에 아무것도 안 그린다.** `render.py:756`이 `tool_execution_update`를
   명시적으로 no-op. positive control로 실측: `tool_execution_start` 1 commit / update **0 commit**.
   ADR-0199 §(l)의 "permanent record" 주장은 **거짓**이었고 인라인으로 정정해 뒀음.
2. **확장 밴드에서 트랜스크립트에 사전 기록할 방법이 없다.** `ExtensionUIContext`에 commit 동사 없음,
   `append_entry`는 replay에서만 렌더, `_commit_update_notice`는 `run_tui` 클로저.
   → 사전=상태줄, 사후=툴 카드 푸터(`_usage_line`이 이미 `yolo`를 적음).
3. **`_clear_row`는 `ui.set_status(key, None)`을 무조건 호출한다** → 회수를 기록과 같은 조건으로
   가드하지 않으면 위임마다 유령 키가 생김(기존 테스트가 잡음).
4. **인용 `--fix`는 4건을 엉뚱한 곳으로 옮겼다** — 앵커가 약하거나(`"""`) 같은 텍스트가 여러 개면
   그럴듯하고 틀린 곳에 앉는다. 손으로 확인해 재지정함.
5. 사보타주 3라운드: 16/19 → 10/13 → 4/5(+1은 내 사보타주가 약했던 것, 문단 통째 제거는 RED).

---

## 4. 남은 일

1. 전체 스위트 (`scratchpad/mine/yolo_final2.log`의 `EXIT=`). 직전 실행은 프로세스 종료로 유실.
2. `git fetch` → `origin/main`이 **`c97fa12`**인지 확인 → `--no-ff` 병합 → 푸시(force 금지).
3. **#196에 결과 코멘트 후 닫기.** #172는 열어 둘 것(파이프라인 미해결). #195는 이미 닫음.
4. 베타 태그 `v0.1.0-beta.1` — **오너 액션**. 태그 전에 `site/latest-version.json`을 Pages에 발행해야
   업데이트 알림이 동작함(RELEASING.md의 베타 절차 2단계).
