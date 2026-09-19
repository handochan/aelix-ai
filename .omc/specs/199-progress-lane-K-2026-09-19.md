# #199 레인 K — 커널 쪽 사용량 합산(`aelix.usage` fold)과 표시 두 줄

- 워크트리 `/tmp/wt-199`, 브랜치 `fix/199-child-session-records`, 기준 `0f57a80`(#294). 작업 종료 시점에도
  `git rev-parse HEAD` = `0f57a805638bd6f951d36648c080393be2333cbd` — 커밋하지 않았다(통합자가 커밋).
- 정본 설계: `.omc/specs/199-design-2026-09-19.md` rev 1 §A.3(레코드 모양)·§A.4(fold). 비평:
  `.omc/specs/199-design-critique-2026-09-19.md`. 레인 D(위임 레코드 작성 쪽)와 같은 트리에서 동시에
  작업했고, 레인 D 파일은 읽기만 했다(쓰지 않음).

## 1. 만든 것

| 파일 | 내용 |
| --- | --- |
| `packages/aelix-agent-core/src/aelix_agent_core/harness/_session_stats.py` | `USAGE_RECORD_TYPE = "aelix.usage"`, `ToolUsage(tokens, cost, cost_known, runs, pending)`, `SessionStats.tool_usage`(기본값 `ToolUsage()`), `fold_usage_records(records)`, `aggregate_session_stats(..., usage_records=())` — fold 결과를 `tokens`/`cost`에 더하고 `cost_known`에 AND |
| `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` | `get_session_stats`가 브랜치를 **한 번** 읽어 `_cost_is_complete(branch)`와 레코드 수집에 같이 쓴다. `_cost_is_complete`는 `async def (self)` → `def (self, branch)`로 바뀜(유일한 호출자가 `get_session_stats`). **줄 수 불변(74→74)** |
| `packages/aelix-coding-agent/src/aelix_coding_agent/tui/stats_dashboard.py` | `TOOL_USAGE_LABEL = "Tools & delegated agents"`, `format_tool_usage(stats) -> str \| None`, `_compact_count`, 세션 탭에 한 줄. `format_session_cost` docstring에 "도구 보고 run이 pending이거나 가격이 없을 때"라는 한 구절 추가 |
| `packages/aelix-coding-agent/src/aelix_coding_agent/tui/commands.py` | `/cost` 표에 `tools & delegated agents` 한 행. **줄 수 불변** |
| `tests/harness/test_tool_usage_stats.py` (신규, 56개) | fold 규칙, SessionStats 병합, RPC wire 키 고정, `roll_up_usage` 동치, 하네스 경유 브랜치/압축/`/tree`/`/fork`/디스크 왕복 |
| `tests/tui/test_tool_usage_display.py` (신규, 18개) | 표시 문자열, 세션 탭 위치, `/cost` 행, 실제 fold→표시, 실제 하네스 경유 `/cost` |
| `tests/pi_parity/test_phase_4_10_strict_superset.py` | Aelix 추가 필드 집합을 `{"cost_known", "tool_usage"}`로 **의도적으로** 이동 + 사유 주석 |
| `tests/harness/test_session_stats.py` | 같은 필드 집합 핀(아래 §3 편차 1) |
| `tests/agents/test_p2_band_boundaries.py` | `_KERNEL_CHANGE_ALLOWLIST` 안에 ADR-0243/#199 주석 블록(두 경로 모두 이미 등재 — ADR-0211/0242 형식의 "changed AGAIN" 기록) |

표시 결과(폭 80, `no_color`로 실제 렌더링해 본 출력):

```
│ cost (USD)                0.0110                                             │
│ tools & delegated agents  12.3k in / 1.1k out · $0.0110 (2 runs)             │
...
Cost          ≥ $0.0110
Tools & delegated agents: 12.3k in / 1.1k out · ≥ $0.0110 (3 runs, 1 pending)
```

## 2. 읽는 계약 — 레코드에서 무엇을, 어떻게 읽는가

수집(`core.py` `get_session_stats`): 현재 브랜치(root→leaf, `Session.get_branch()` 한 번)의 엔트리 중
`isinstance(entry, CustomEntry) and entry.custom_type == "aelix.usage"` 인 것의 `entry.data`만, 브랜치 순서대로.
`custom_message`는 같은 타입 이름이어도 레코드가 아니다(컨텍스트에 들어가는 것 — ADR-0242 규칙 1.1).

fold(`_session_stats.fold_usage_records`)가 읽는 필드는 정확히 다음뿐이다.

1. `data` — dict가 아니면 그 줄은 건너뛰고 cost unknown.
2. `data["state"]` — `"pending"` 또는 `"final"`만 안다. 그 외 값/없음 → 그 줄은 건너뛰고(마지막 줄 경쟁에도
   참여하지 않음) cost unknown.
3. `data["key"]` — 비어 있지 않은 문자열이면 그 키로 묶고 **마지막 줄이 이긴다**(중복 final은 1회, final이
   pending을 대체). 문자열이 아니거나 없거나 `""`이면 "키 없음" → 줄마다 한 번씩 센다.
4. 마지막 줄이 `pending`인 키 → 아무것도 더하지 않고 `pending += 1`, cost unknown.
5. `final`의 `data["usage"]` — dict가 아니면 더하지 않고 cost unknown. dict이면
   `usage["input"]`, `["output"]`, `["cache_read"]`, `["cache_write"]` 네 flow와 `usage["cost"]`만 읽는다.
   - 토큰 수: `int`(bool 제외) ≥ 0, 또는 유한·비음수·정수값 `float`(`12.0`→12). 그 외(없음, NaN, ±inf, 음수,
     bool, 문자열, `12.5`) → 그 숫자만 무시(0), cost unknown. 같은 레코드의 다른 숫자는 그대로 더한다.
   - 비용: `int|float`(bool 제외), 유한, ≥ 0. float로 못 바꾸는 거대 정수는 거부(예외 없음). 그 외 → 무시, cost unknown.
6. `final`의 `data["cost_known"]` — `True`가 아니면(없음 포함) cost unknown. 가격이 매겨진 부분은 그대로 더한다
   ("≥ $X"로 보이게).
7. **읽지 않는 것**: `v`(버전), 그 밖의 모든 키. 컨텍스트 레벨(`tokens`, `context_tokens`, `total`)은 어디에
   있든 합산하지 않는다 — 네 flow만.

`runs` = fold 후 레코드 수(키마다 1 + 키 없는 줄마다 1, 읽지 못한 줄 제외), `pending` = 그중 마지막이 pending인 수.
`tokens.total` = 네 flow의 합. 합산 순서는 키가 처음 나타난 순서(파이썬 dict 재할당이 위치를 유지).

병합(`aggregate_session_stats`): `SessionStats.tokens.{input,output,cache_read,cache_write,total}`과 `cost`에
fold 결과를 더하고, `cost_known = unpriced == 0 and cost_complete and tool.cost_known`, `tool_usage = tool`.
`cost_complete`(압축 여부)는 도구 몫에 영향을 주지 않는다 — 레코드는 압축 이전 엔트리까지 브랜치 전체에서 읽는다.
RPC `_session_stats_to_dict`는 건드리지 않았고 키 목록이 그대로임을 테스트로 고정했다(병합된 합계는 `tokens`/`cost`에 실림).

레인 D 작성기와의 정합(레인 D의 진행 중 파일 `aelix_agents/child_session.py`를 **읽기만** 해서 확인):
`USAGE_TYPE = "aelix.usage"`, pending `{"v": RECORD_VERSION(=1), "key", "state": "pending"}`, final
`{"v", "key", "state": "final", "usage": {input, output, cache_read, cache_write, cost}, "cost_known"}` —
위 계약과 필드 이름·모양이 일치한다. 작성기는 카운터를 int ≥ 0, 비용을 유한 ≥ 0으로 정리해서 쓴다.

## 3. 편차와 설계에 없던 결정

1. **`tests/harness/test_session_stats.py`도 고쳤다(지정 파일 목록 밖).** `test_session_stats_has_ten_fields`가
   pi_parity 핀과 똑같이 `fields - pi_fields == {"cost_known"}`를 고정하고 있어서, 필드를 추가하면 반드시 깨진다.
   같은 집합으로 옮기고 주석을 달았다. 레인 D 파일이 아니다.
2. **`core.py`와 `commands.py`를 줄 수 불변으로 고쳤다.** 이 두 파일 뒤쪽 줄을 인용하는 사이트가 많고, 그중
   레인 D 파일이 있다(`aelix_agents/{batch,extension,rpc_channel,tool,aggregate}.py`). 줄이 밀리면
   `check_citations.py --fix`가 레인 D 파일을 고쳐야 해서 "레인 D 파일을 건드리지 말 것"과 충돌한다.
   - `core.py`: `get_session_stats`/`_cost_is_complete` 블록(2589–2662)을 같은 74줄로 다시 썼다(docstring과
     주석 정리 — 옛 주석의 `:1591-1595`, `line 673` 같은 낡은 참조는 빠졌다).
   - `commands.py`: `/cost`의 토큰 세 행을 `for name in ("input", "output", "total")` 루프로 바꿔 두 줄을
     확보하고, 그 자리에 도구 행(조건부 2줄)을 넣었다. 출력은 도구 행 외에 동일(`test_cost_renders_stats` 통과).
3. **설계가 정하지 않은 fold 규칙(모두 fail-closed 방향)**: 알 수 없는 `state`/dict 아닌 `data` → 건너뜀 +
   cost unknown; `cost_known` 없음 → unknown; `usage`의 필드 누락 → unknown; `v`는 읽지 않음(ADR-0242 규칙 1.3:
   이후 버전은 이 필드들의 의미를 지키거나 다른 customType이어야 한다); 키는 비어 있지 않은 문자열만; 정수값
   float 토큰 허용; 마지막 줄이 pending이면 앞선 final도 무효(설계 문구 "last line wins"를 예외 없이 적용).
4. **`runs`의 정의**: pending 중인 것도 run으로 센다. 그래서 표시가 "(2 runs, 1 pending)" = "2개 중 1개 미확정"으로
   읽히고, 첫 위임이 아직 도는 중(runs=1, pending=1)에도 줄이 보인다("0 in / 0 out · n/a (1 run, 1 pending)").
5. **`/cost` 표시 형태**: 표(라벨 열 + 값 열)라서 설계 예시 문장을 행으로 옮겼다 — 라벨 `tools & delegated agents`
   (다른 `/cost` 라벨처럼 소문자), 값은 세션 탭과 같은 `format_tool_usage` 문자열. 세션 탭은 예시 그대로
   `Tools & delegated agents: 12.3k in / 1.1k out · $0.0110 (2 runs)`를 `Cost` 바로 아래에 한 줄.
6. **토큰 표기**: `_compact_count`(1,000부터 소수 한 자리 `k`, 100만부터 `M`) — 배치 `[total]` 줄
   (`aggregate._format_count`)·statusline과 100만 미만에서 같은 표기. 기존 `_compact_tokens`(History 탭용, 10,000부터 `k`)와는
   일부러 다르다(예시가 `1.1k out`).

## 4. 검증 — 실행한 명령과 출력 끝부분

모든 명령은 `cd /tmp/wt-199` 후 `env -u VIRTUAL_ENV uv run --no-sync …`(부모 셸의 `VIRTUAL_ENV`가 메인 체크아웃
`.venv`를 가리켜서 경고만 나고 무시되지만, 확실히 하려고 뺐다). `.pytest_cache`를 트리에 남기지 않으려고 `-p no:cacheprovider`.

| 명령 | 결과 |
| --- | --- |
| (변경 전 기준) `pytest -q tests/harness/test_session_stats.py tests/harness/test_context_usage.py tests/pi_parity/test_phase_4_10_strict_superset.py tests/tui/test_stats_dashboard.py tests/tui/test_commands.py tests/rpc/test_rpc_mode_get_session_stats.py tests/agents/test_p2_band_boundaries.py` | `237 passed in 1.09s` |
| (변경 후, 핀 이동 전) 같은 명령 | `2 failed, 235 passed` — 예상한 두 필드 핀만 실패 |
| `pytest -q tests/harness/test_tool_usage_stats.py tests/tui/test_tool_usage_display.py tests/harness/test_session_stats.py tests/pi_parity/test_phase_4_10_strict_superset.py tests/agents/test_p2_band_boundaries.py` | `126 passed in 0.77s` (skip 0) |
| 위 + `test_context_usage` `test_harness_get_session_stats` `test_stats_dashboard` `test_commands` `test_rpc_mode_get_session_stats` | `317 passed in 1.00s` |
| 넓은 집합: `tests/harness tests/rpc` + pi_parity 4.4/4.6/4.10 + `cli/test_startup_resume_stats_122` `runtime/test_resume_thinking_level_198` `runtime/test_switch_session_stats_122` `session/test_storage_conformance` + tui의 stats·cost·footer·meter·smoke·replay·history·agents_run + 밴드 게이트 (`-rs`) | `1091 passed, 1 skipped, 10 warnings in 33.99s` — skip은 `tests/rpc/test_rpc_mode_deferred.py:123: got empty parameter set` (기존) |
| `ruff check` (소스 4 + 테스트 5) | `All checks passed!` |
| `pyright` 소스 4개 | `0 errors, 0 warnings, 0 informations` (변경 전 기준도 0) |
| `pytest -q tests/agents/test_p2_band_boundaries.py` | `7 passed` — `test_kernel_has_no_subagent_surface`, `test_kernel_untouched_vs_merge_base`(skip 아님) 포함 |
| `grep -rni "subagent\|aelix_agents"` 커널 두 파일 | 결과 없음 |
| `python scripts/check_citations.py --report` | `sites 954 / gated 911 / unresolved 43 / citing_files 150` — lock의 stats와 동일 |
| `python scripts/check_citations.py --check` | **exit 1** — 드리프트 대상은 전부 레인 D 파일(`cli/entry.py` 41, `print_channel.py` 32, `runtime.py` 26, `stream.py` 19, `entry.py` 18, `extension.py` 16, `envelope.py` 10, `tool.py` 9, `batch.py` 7, `agents/resolver.py` 6, `aggregate.py` 4, `resolver.py` 3, `subagent_contract.py` 2, `aelix_agents/extension.py` 1). **내 네 소스 파일을 가리키는 드리프트: 없음** |
| `pytest -q tests/test_citation_drift.py` | `1 failed, 21 passed` — 실패는 `test_no_citation_has_drifted`, 위 레인 D 드리프트 때문 |

`--fix`/`--lock`은 **돌리지 않았다**: 레인 D 파일과 공유 `citations.lock.json`을 다시 쓰게 되고, 레인 D가 편집
중이다. 두 레인이 끝난 뒤 통합자가 `--fix` 한 번이면 된다. 참고로 `tui/commands.py:1021`(내 파일)은 레인 D가 옮긴
블록(`stream.py:573-577`, `envelope.py:398-399`)을 인용하는 사이트라서, 그 `--fix`가 이 한 줄을 고칠 것이다(정상).

**뮤테이션 확인**(원본을 복사해 두고 한 가지씩 바꿔 새 테스트 2개 파일을 돌린 뒤 복원, `cmp`로 복원 확인):

| 뮤테이션 | 잡은 테스트 |
| --- | --- |
| pending이 cost_known을 지우지 않음 | `test_a_key_left_pending_adds_nothing_and_makes_the_cost_unknown` |
| last-wins → first-wins | `test_the_exact_design_records_fold_as_one_settled_run` |
| `usage.tokens`(레벨)도 합산 | `test_a_context_level_is_never_summed` |
| 브랜치 대신 파일의 모든 엔트리(pi 방식) | `test_a_tree_move_stops_counting_records_off_the_path`, `test_an_unreadable_branch_yields_a_floor_and_no_records` |
| 브랜치를 두 번 읽음 | `test_the_branch_is_read_once_for_the_records_and_the_compaction_check` |
| 도구 비용을 총액에 안 더함 | `test_tool_usage_is_added_to_the_session_totals_and_broken_out` |
| final의 `cost_known` 무시 | `test_a_final_that_says_its_cost_is_unknown_still_adds_what_was_priced` |

7/7 잡힘. 결과 줄 예: `M4 records read over every entry (pi-style): … ['FAILED …test_a_tree_move_stops_counting_records_off_the_path', '2 failed, 54 passed']`.

**Pi 확인(규칙 11)**: `~/dev/pi` `origin/main` `36b60d2e8`에서 `git show`로
`packages/coding-agent/src/core/agent-session.ts`를 읽었다. `SessionStats`에는 도구 사용량 필드가 없고,
`getSessionStats()`는 `sessionManager.getEntries()`(파일의 **모든** 엔트리, 모든 브랜치, 압축된 이력 포함)를 돌며
assistant usage + toolResult `usage` + branch_summary/compaction `usage`를 같은 합계에 더한다. 우리는 브랜치만 읽는다
— ADR-0235 divergence(설계 §A.4)로 `core.py` docstring에 적었다.

**하지 않은 것**: TUI 라이브 확인(규칙 9 — 리드 몫), 실제 모델 라이브 실행, Codex 교차 리뷰(규칙 8), 전체 스위트,
Windows 실행(windows-latest CI가 한다). 테스트는 Windows 안전하게 썼다: POSIX 전용 호출 없음, JSONL 테스트는
`tmp_path/"s"` + 짧은 cwd(`/p`, `/f`), Rich 렌더링은 기존 `test_commands`와 같은 `StringIO` 콘솔.

## 5. 통합자에게

- ADR-0243 번호는 임시. 내가 쓴 언급: `_session_stats.py` 5, `core.py` 1, `stats_dashboard.py` 3,
  `test_p2_band_boundaries.py` 1, `test_session_stats.py` 1, `test_tool_usage_stats.py` 1,
  `test_phase_4_10_strict_superset.py` 2, `test_tool_usage_display.py` 1 (`grep -rn ADR-0243`).
- ADR/CHANGELOG에 넣을 사실: fold 규칙(§2), "브랜치만, 압축 이전 포함, `/tree`로 경로 밖이면 안 셈, fork는 그 브랜치의
  레코드를 복사" (테스트로 고정), pi와의 차이(파일 전체 vs 브랜치), `aelix.usage`는 ADR-0242 규칙 1의 `CustomEntry`
  레코드이지 pi v4의 별도 ledger 저장소가 아님(비평 NIT 23), 합계가 #199부터 자식 지출만큼 오름(History 포함).
- 레인 D의 `USAGE_TYPE`은 커널의 `USAGE_RECORD_TYPE`과 같은 문자열 — 원하면 `aelix_agents`가 커널 상수를
  import하게 합칠 수 있다(방향상 허용: 확장 → 커널).

## 6. 열린 질문 (라이브 확인 때 판단할 것)

1. **80열에서 `/cost` 도구 행이 줄바꿈된다** — pending + floor 상태의 값(`12.3k in / 1.1k out · ≥ $0.0110 (3 runs,
   1 pending)`, 51자)이 라벨 열 24자와 합쳐 패널 안쪽 76자를 넘는다(위 렌더링 참고). 정상 상태(38자 안팎)는 들어간다.
   라벨을 `tools & agents`로 줄이면 80열에서도 들어간다. 설계 예시 문구를 지키느라 바꾸지 않았다.
2. **"이미 합계에 포함"이 줄에 쓰여 있지 않다.** 사용자가 `Cost`와 도구 줄을 더할 수 있다. `incl.` 같은 접두어를
   붙일지는 문구 결정 — 예시 그대로 두었다.
3. **푸터의 cost 세그먼트(기본 OFF)는 `cost_known`을 보지 않는다**(기존 동작, `shell._refresh_context_usage` →
   `set_usage_stats`). pending 중에도 `≥` 없이 숫자를 보인다. 내 범위 밖이라 손대지 않았다.
4. **확장도 `aelix.usage`를 쓸 수 있다** — ADR-0242 규칙 1.5의 `aelix.` 네임스페이스는 문서상 약속이고 강제되지
   않는다. 커널 fold는 그것도 "도구가 보고한 사용량"으로 센다. ADR에 한 줄 적어 둘 만하다.
