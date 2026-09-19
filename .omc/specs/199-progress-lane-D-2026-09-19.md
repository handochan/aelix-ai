# #199 lane D 진행 기록 — 자식 세션 파일, 부모의 영수증 레코드, argv, 배선, 배치 예산, 자식 파일 경고

작성: lane D 구현 에이전트, 2026-09-19. 커밋하지 않음(통합자가 커밋).

## 기준점

- worktree `/tmp/wt-199`, 브랜치 `fix/199-child-session-records`, base `0f57a80`(#294 커밋).
- 설계 정본: `.omc/specs/199-design-2026-09-19.md` rev 1(A.9 = (c), A.10 = 후속 이슈). 비평: `199-design-critique-2026-09-19.md`.
- 같은 트리에서 lane K가 동시에 작업 중(커널 `harness/_session_stats.py`, `harness/core.py`, `tui/commands.py`,
  `tui/stats_dashboard.py`, pi_parity 핀, band gate 테스트, `tests/harness/*`, `tests/tui/test_tool_usage_display.py`).
  lane D는 그 파일들을 편집하지 않았다 — 예외 하나: 인용 `--fix`가 `tui/commands.py` 한 줄의 인용 번호(내 파일
  `stream.py`/`envelope.py`를 가리키는 것)를 옮겼다. 스냅숏 대비 diff로 그 한 줄 외 변화 없음을 확인(아래 검증 §6).

## 만든 것 (설계 절 기준)

| 절 | 구현 |
| --- | --- |
| A.1 배치 | `aelix_agents/child_session.py` `place_children`: `<bucket>/<stem>.jsonl` → `<bucket>/<stem>/<spawn_id>.jsonl`. `.jsonl`만 정확히 떼고 그 외엔 `.children`. 경로는 부모 프로세스에서 `os.path.abspath`. 부모 디렉터리가 bucket 모양(`--…--`)이 아니면 거부 → `child: null`, `error: "parent outside a bucket"`, 자식 `--no-session`. 파일 0600 / 디렉터리 0700(#294 `LocalFileSystem`). |
| A.2 할당 | `SpawnReceipt._allocate`: `JsonlSessionStorage.create(LocalFileSystem(), path, cwd=<자식 cwd>, session_id=<uuid4>, entries=[origin])` 한 번의 원자적 publish. origin `parent_id=None`. 헤더 `parentSession` 미설정. 이미 있는 이름이면 거부(`os.replace` 덮어쓰기 방지). 모든 실패(`OSError`, 긴 경로, `SessionError`)는 `child: null` + 짧은 error, 위임은 계속. |
| A.3 레코드·순서 | `runtime._run`: 승인 블록·첫 publish 그대로 → 동기 `SpawnReceipt` 생성 → `try:` 세션 1회 캡처 → 할당 → start+pending → `dataclasses.replace(plan, session_path=…)` → `channel.run` → settle+final → `settled=True`. `finally:` 미정산이면 settle+final(필요 시 start+pending 포함)을 `asyncio.shield`로(별도 task, 강한 참조 보관) 기록한 뒤 **중첩 finally에서** row pop + terminal publish. per-runtime 락 없음. 모든 append는 DEBUG 로그 후 삼킴. |
| A.3a 세션 1회 캡처 | `SubagentHost.session` 게터, `AgentsExtension.session` 필드, `cli/entry.py`에서 `session=lambda: _live_session_of(session_host)`(런타임 호스트 생성 후 `session_host["runtime"] = runtime`). 폴백은 `ctx.session_manager.get_session()` under `suppress(ExtensionError)`. |
| A.3b cost_known | `_StreamState.cost_reported`(reducer가 `usage`에 유한한 `cost` 키를 보면 True, 0 포함) + `cost_priced`(`apply_cost_fallback`이 설정, 이제 `bool` 반환). 취소/예외 경로는 `_run` finally가 `apply_cost_fallback(child.stream, host.model_registry())`를 먼저 호출. |
| A.3c 배관 | `runtime.SpawnRecordMeta(tool_call_id, mode, index)`; `spawn_granted(record=)`, `batch.run_batch(record=)`(멤버마다 `dataclasses.replace(…, index=index)`). Protocol의 `spawn`은 무변경, `/agents run`은 `record=None` → 세 필드 null. |
| A.5 마커 | `envelope._TRUNCATION_MARKER` = `"\n\n[Output truncated: {omitted} bytes omitted.{where}]"`, 파일이 있고 요약이 자식 자신의 스트림(답 또는 error_message)에서 왔을 때만 `where = " The full output is recorded in the delegated session."`. 경로는 절대 넣지 않음. |
| A.6 argv | `resolver.profile_to_argv(session_path=None)`: one-shot 접두 `--mode json -p` + `--session <p>` 또는 `--no-session`. rpc 접두는 무변경, `build_rpc_child_argv`가 자기 `--no-session`을 `--session <p>`로 교체. 두 stale docstring(`resolver.profile_to_argv`, `print_channel.build_child_argv`) 수정. rpc는 stub 테스트만(#123) — 모듈 docstring에 명시. |
| A.7 | 레코드에 `path`(절대) + `rel`(`<stem>/<name>`, 항상 `/`) + `session_id`. |
| A.8 | 새로 컨텍스트에 들어가는 것 없음(레코드는 `custom`). live `details`는 그대로. |
| A.9 (c) | `aggregate.BATCH_OUTPUT_BUDGET_BYTES = 64 * 1024`, `member_output_budget(n) = budget // max(n, 1)`; `render_batch_result`가 `envelope.recap_summary`로 멤버별 재-cap(채널 마커를 떼고 omitted 합산). single 모드는 51 200 그대로. chain `{previous}`는 envelope summary를 그대로 받음. |
| A.11 | `cli/entry.py` `_warn_if_delegated_child`: 첫 엔트리가 `aelix.child_origin`이면 stderr 한 줄 경고 + `aelix --export <path>`. depth > 0(자식 자신)이면 침묵. `--session`과 `--fork` 둘 다. |

## 기록하는 레코드 모양 (정확히)

`key` = spawn id `sub-<12hex>`. 모든 레코드 = `CustomEntry` 한 줄, 내용은 전부 `data`. 키 순서도 이대로 쓴다.

부모 세션:

```text
aelix.child_session start:
  {v:1, key, phase:"start", tool_call_id, index, mode, profile, task_preview, child, requested_model,
   permission_mode, aelix_version, error}
  child = {session_id, path, rel} | null
  error = null | "parent outside a bucket" | "could not create the child session file: …" | "a child session file with this name already exists"
aelix.usage pending:
  {v:1, key, state:"pending"}
aelix.child_session settle:
  {v:1, key, phase:"settle", tool_call_id, index, mode, profile, task_preview, child, requested_model,
   permission_mode, aelix_version, status, model, provider,
   usage:{input, output, cache_read, cache_write, cost}, cost_known, context_tokens, turns, elapsed_ms,
   exit_code, stop_reason, truncated, summary_bytes, details_bytes, error}
  status ∈ ok | error | timeout | aborted | cancelled   (envelope 경로는 envelope.status 그대로)
aelix.usage final:
  {v:1, key, state:"final", usage:{input, output, cache_read, cache_write, cost}, cost_known}
```

자식 파일(헤더 다음 첫 엔트리, `parentId: null`):

```text
aelix.child_origin:
  {v:1, key, parent:{session_id, path}, tool_call_id, index, mode, profile, permission_mode, aelix_version}
```

값 규칙: `usage`의 카운터는 음수 아닌 int, `cost`는 유한한 음수 아닌 float(아니면 0.0이고 `cost_known=false`).
settle의 `usage`/`cost_known`과 final의 것은 항상 동일. `context_tokens`는 LEVEL(합산 금지). `task_preview` =
task 앞 200자, `error` = 300자 이내. settle은 start의 모든 필드(error 제외)를 반복하는 **완결 레코드**다.
미정산(취소/예외) settle은 `summary_bytes: 0`, `details_bytes` = 자식이 흘린 부분 답의 바이트, `truncated: false`.

종료 경로 표(구현 그대로): ok/error/timeout/aborted/exec 실패 → try에서 4개; `channel.run` 이전·내부 예외 →
finally에서 settle(`error`, `"<ExcType>: <msg>"`) — 예외는 그대로 전파; 취소 → finally에서 settle(`cancelled`,
error null); 프로세스가 죽으면 start+pending만(결과 불명/미확정 지출); 승인 안 된 spawn(drain, live cap, budget,
consent decline, batch 거부) → 레코드 없음.

## 편차 (설계와 다르게 한 것, 이유)

1. **계약 필드 추가**: `SubagentResult.output_recorded: bool = False`(additive, `CONTRACT_VERSION` 유지). 배치
   렌더러가 멤버 요약을 재-cap할 때 마커가 "기록됨"을 주장해도 되는지 알 방법이 달리 없다(렌더러는 순수 함수).
   `SpawnPlan.session_path`만으로는 부족: stderr에서 온 요약(조기 종료 자식)은 파일에 없다.
2. **stale `_ctx`로 `/agents run` 이 크래시하던 기존 결함 발견·수정**: `request_spawn_consent`의
   `getattr(ctx, "has_ui", False)`가 stale `ExtensionContext`에서 `ExtensionError("stale")`를 던진다(`getattr` 기본값은
   `AttributeError`만 잡음). `/new` 직후 첫 훅 전의 `/agents run`이 레코드는커녕 실행도 못 했다(스크래치 probe로
   측정: `spawn raised: ExtensionError stale`). `AgentsExtension._host_consent_context`가 stale ctx를 None(헤드리스
   기본값, 새 세션의 첫 `/agents run`과 같은 상태)으로 취급. 새로운 권한 상태는 없다.
3. **루트 배치 판정**: 설계는 "부모가 sessions root 바로 아래"면 거부. 확장은 `--session-dir`를 볼 수 없어서 "부모
   디렉터리가 bucket 모양(`--…--`)이 아니면 거부"로 구현 — 보수적. 결과: 임의 디렉터리의 부모
   (`aelix --session /x/y.jsonl`)도 자식은 `--no-session`, start `error: "parent outside a bucket"`. 어떤 picker가
   스캔하는 디렉터리도 만들지 않는다. (열린 질문 1)
4. **start에 `error` 키**: A.3의 start 목록엔 없지만 A.1/A.2가 `child: null, error: …`를 요구. start에 항상 넣음(null
   가능). settle의 `error`는 실행 자체의 오류이고 할당 오류를 복사하지 않는다.
5. **settle = 완결 레코드**: "start's identity fields" 를 start의 모든 필드(error 제외)로 해석. ADR-0242 "두 개의
   완결 레코드, 한 레코드의 두 반쪽이 아님"과 last-wins 폴드에 안전.
6. **cost_known의 네 번째 disjunct `cost > 0`**: 출하 채널에서는 앞의 두 조건(보고된 cost, fallback 가격)에 함의된다.
   주입된 채널(테스트)이 cost만 보고해도 같은 답을 주려고 명시.
7. **`--fork <자식 파일>`도 경고**: fork는 origin 레코드를 복사하므로 같은 무제한 실행.
8. **`tests/agents/test_rpc_sprint_pins.py`의 두 `--no-session`은 옮기지 않음**: 실제 rpc 자식을 띄우는 테스트용
   하드코딩 argv이지 제품 argv 핀이 아니다. rpc argv의 세션 플래그 핀은 `test_child_argv_contract.py`에 추가
   (`test_the_rpc_channel_swaps_its_own_no_session_for_the_same_file`).
9. **`cli/entry.py` 배치**: 인용 drift를 줄이려고 헬퍼(`_live_session_of`, `_CHILD_ORIGIN_TYPE`,
   `_warn_if_delegated_child`)를 모듈 끝으로, `_build_session`은 라인 중립(한 줄 래핑). first-run 주석 한 줄도 사실에
   맞게 수정(`--no-session` → "a session flag").
10. `roll_up_usage` = 커널 폴드 동일성 테스트(§C)는 커널 폴드가 lane K 소관이라 작성하지 않았다. `roll_up_usage`
    docstring에 "display only"만 명시.

## 추가·이동한 테스트

- 새 파일 `tests/agents_ext/test_child_session_records.py`(43): 배치(stem/`.children`/bucket 밖 거부), picker 비가시성
  (자식을 가장 최신 mtime으로 만든 뒤 `find_most_recent`/`list(cwd)`/`list()`/id-prefix), header+origin(`parentId`
  None, 재오픈 후 `get_branch`), 상대 `--session` 부모 + 하위 디렉터리 자식 cwd(하위 디렉터리에서 로드), 루트 배치 →
  `--no-session`(실제 builder argv), 할당 실패, `--no-session` 부모 + `AELIX_CODING_AGENT_SESSION_DIR` → 어디에도
  파일 없음, 정확한 레코드 모양·순서·리로드, 모든 envelope status, 채널 예외(재-raise + settle error), 실행 중
  start+pending만, 실제 task cancel(가격 매긴 부분 지출), 가격 불가 cancel, **두 번째 cancel**(느린 세션으로 결정적
  재현 — task 종료 시점엔 settle 미기록, 이후 기록), 거부된 spawn 무기록, 기록 실패가 결과를 바꾸지 않음, 게터
  예외, **/new 후 settle이 원래 세션에**(게터 호출 1회), `/agents run` `_ctx` None / stale ctx, 미배선 확장의 stale
  폴백, 모델 도어의 tool_call_id·mode·index(single + parallel 3), chain index, CLI 상수 동일성, cost fallback/reducer/
  cost_known 규칙, receipt 단위 테스트, task_preview, argv 파싱.
- 새 파일 `tests/agents_ext/test_output_budget_and_marker.py`(10): 마커 정확한 문구(두 변형), envelope가 자식 스트림일
  때만 주장, recap의 omitted 합산, 몫 이하 요약 불변, 에코된 마커 비파싱, 64 KiB/8 = 8192, 8멤버 배치 상한, 잘린
  에러 노트 비복원, single 모드 51 200 유지, chain hand-off 무재-cap.
- 새 파일 `tests/agents_ext/test_child_session_real_child.py`(2, POSIX): 실제 `-m aelix_coding_agent` 자식(stub provider
  확장)이 부모가 만든 파일에 transcript를 origin 뒤에 쓰고 settle(ok, stubmodel/stubprov, 16/28 토큰, 레지스트리
  가격 → `cost_known: true`); 존재하지 않는 provider의 조기 종료 자식 → 파일은 header+origin만, settle `error`.
- 새 파일 `tests/cli/test_child_session_warning.py`(6): 경고 1줄 + `aelix --export`, fork 경고, depth>0 침묵, 일반
  세션 무경고, entry의 `session=` 람다 배선(AST), `_live_session_of`가 호스트를 따라감.
- 의도적으로 옮긴 핀: `test_child_argv_contract.py`(`--no-session`은 폴백 + `--session` 변형 2개 추가),
  `test_reduce_consumes_real_print_mode_output.py`(세션 있는 자식은 헤더가 첫 줄, 리듀스 불변, 자식 transcript가 파일에),
  `tests/agents/test_profile_resolver.py`(기본값 그대로 green + 세션 파일 변형 추가).

## 검증 (명령과 출력 꼬리)

모든 명령은 `/tmp/wt-199`에서 `env -u VIRTUAL_ENV uv run --no-sync …`.

1. `pytest tests/agents_ext/test_child_session_records.py -q` → `43 passed in 0.34s`
2. 변이 검사(되돌림, `cmp`로 원복 확인): `_shielded_append`의 shield 제거 → `test_a_second_cancel_cannot_lose_the_settle`
   FAILED; settle을 `self._parent_session()`로 재조회 → `test_a_settle_after_new_lands_in_the_session_that_ran_the_child`
   FAILED. 원복 후 `cmp … runtime.py.bak` → identical.
3. `pytest tests/agents_ext/test_output_budget_and_marker.py -q` → `10 passed`;
   `test_child_session_real_child.py` → `2 passed in 0.91s`(실제 자식 0.37 s, 파일 내용 출력으로 실체 확인);
   `tests/cli/test_child_session_warning.py` → `6 passed`; `test_child_argv_contract.py` → `32 passed`;
   `test_reduce_consumes_real_print_mode_output.py` → `6 passed`; `tests/agents/test_profile_resolver.py` → `17 passed`.
4. `ruff check` (변경 파일 전부) → `All checks passed!`; `pyright` (변경 소스 + 새/변경 테스트) → `0 errors, 0 warnings, 0 informations`.
5. 인용: `check_citations.py --check` → 194 → (entry.py 라인 중립화 후) 154 drifted; `--fix` → `relocated 140 … 14 could
   NOT be relocated`; 앵커가 앞 8줄뿐이라 범위 끝이 틀릴 수 있어 base(`0f57a80`)→현재 difflib 라인 매핑으로 내 파일을
   가리키는 모든 인용을 감사 → 15건 불일치 + 1건 stuck(`:981`) 손으로 재도출(17건) → `--lock` →
   `citations OK — 911 gated, none drifted.`
6. lane K 파일 무손실: `--fix` 전 스냅숏과 비교 → `tui/commands.py` 한 줄(`stream.py:573-577 → 611-615`,
   `envelope.py:398-399 → 483-484`)만 다르고 나머지 lane K 파일은 동일.
   인용 번호만 바뀐 범위 밖 파일 32개(`docs/guides/project-trust.md`, 번들 `docs/project-trust.md`, `chain.py`,
   `consent.py`, `panel.py`, `progress.py`, `prompt_file.py`, `reaper.py`, `agents/profile.py`, `agents/service.py`,
   `tui/shell.py`, `aelix_status/extension.py`, `aelix_server/rpc_ws.py`, `tests/conftest.py`,
   `tests/test_citation_drift.py`, `tests/tui/test_agents_run_command.py`, `tests/agents_ext/*`·`tests/cli/*` 일부):
   base 대비 모든 변경 줄이 숫자만 다름을 스크립트로 확인(`non-digit changes: 0`).
7. 광역: `pytest tests/agents tests/agents_ext tests/cli tests/tui/test_agents_run_command.py tests/test_citation_drift.py -q`
   → 결과는 아래 "최종 실행" 참고. (전체 스위트는 돌리지 않음 — 리드 몫.)

### 최종 실행

- `pytest tests/agents tests/agents_ext tests/cli tests/tui/test_agents_run_command.py tests/test_citation_drift.py -q -p no:cacheprovider`
  → `3479 passed, 11 skipped, 3 warnings in 127.57s (0:02:07)` (경고 3개는 기존 진단용 `warnings.warn` — rpc/print
  자식 종료 시간). 같은 HEAD `0f57a80`, 작업 트리 기준.
- 그 뒤 `child_session.py` docstring 한 줄(`ADR-0243 §A.7` → `#199 design §A.7`)만 고친 다음 재확인:
  `ruff check …` → `All checks passed!`; `check_citations.py --check` → `citations OK — 911 gated, none drifted.`;
  `pytest` 새 파일 4개 + `tests/agents/test_p2_band_boundaries.py` + `tests/cli/test_p2_import_direction.py` →
  `73 passed in 2.02s`.
- 참고: 주요 소스 편집 직후, 새 테스트를 쓰기 전(그리고 stale-ctx 수정·entry.py 재배치 전)에 기존 테스트만
  돌린 `pytest tests/agents tests/agents_ext -q -x` → `1712 passed, 11 skipped in 95.52s`. 편집 전 기준선은 따로
  측정하지 않았다.

## 라이브 체크 (CLAUDE.md 규칙 10)

스크래치 cwd `…/scratchpad/live199-lane-d`(체크아웃 아님), `note.txt` 첫 줄 = `lane-d live check: the first line of the note`.

```bash
set -a; . /Users/handochan/dev/aelix-ai/.env; set +a
uv run --no-sync --project /tmp/wt-199 aelix --agents --provider openrouter --model anthropic/claude-haiku-4.5 \
  --permission-mode plan --session-dir "$S/sessions" --mode json \
  -p "Use the agent tool exactly once: delegate ONE task to the explorer profile asking it to read the file note.txt in the current directory and report its first line verbatim. Then tell me the first line it reported." < /dev/null
```

본 것:
- exit 0. `agent` 호출 1회(profile explorer), 결과 `lane-d live check: the first line of the note`, 푸터
  `[agent explorer · anthropic/claude-haiku-4.5 · ok · plan · $0.0059 · 3.3s]`.
- 부모 `sessions/--…-live199-lane-d--/2026-09-19T11-39-36-161Z_1f5ccf32-….jsonl`, 자식
  `…/2026-09-19T11-39-36-161Z_1f5ccf32-…/sub-bbb216301009.jsonl` — 자식 0600, 디렉터리 0700.
- 자식: 헤더(`type session, version 3`, `parentSession` 없음, cwd = 스크래치) → `aelix.child_origin`(`parentId: null`,
  data `{v:1, key:"sub-bbb216301009", parent:{session_id:"1f5ccf32-…", path:<부모 절대 경로>}, tool_call_id:"toolu_bdrk_…",
  index:0, mode:"single", profile:"explorer", permission_mode:"plan", aelix_version:"0.1.0b2"}`) → user → assistant →
  toolResult → assistant(자식 transcript가 origin에 연결).
- 부모 엔트리 순서: user, assistant(toolCall), `aelix.child_session` start(child {session_id, path, rel}, error null),
  `aelix.usage` pending, settle(`status ok`, `model anthropic/claude-haiku-4.5`, `provider openrouter`,
  `usage {input 5473, output 83, cache 0/0, cost 0.005888}`, `cost_known true`, `context_tokens 2808`, `turns 2`,
  `elapsed_ms 3276`, `exit_code 0`, `stop_reason stop`, `summary_bytes 85`), final(같은 usage, `cost_known true`),
  toolResult, assistant. openrouter는 cost 키를 안 보내므로 `cost_known`은 레지스트리 fallback(`cost_priced`)에서 옴.
- `--continue`(같은 `--session-dir`, 같은 cwd) — 자식 파일 mtime을 **가장 최신**으로 올린 뒤 실행 → 헤더 id
  `1f5ccf32-…`(부모), 답 `The explorer reported: "lane-d live check: the first line of the note"`.
- 추가(무비용): `aelix --provider nope --model nope --session <자식 파일> --mode json -p hi` → stderr에 경고 1줄
  (`… is a delegated agent's session record; … To read it without running it: aelix --export <path>`) 후 모델
  게이트에서 exit 1, 자식 파일 바이트 2934 → 2934(무변경). `aelix --export <자식 파일> child.html` → 9073바이트 HTML,
  note 첫 줄 포함.

실행하지 않은 것: TUI(`/cost`·`/stats`는 lane K), Ctrl+C·quit 중 위임, `/agents run` 직후 `/new`, 병렬 3개 배치의
라이브 — §D의 나머지는 리드가 돈다. rpc 채널은 실제 모델로 돌린 적 없음(#123, stub 전용).

## 열린 질문

1. 루트 배치 판정(편차 3): bucket 모양 규칙 유지? 아니면 `cli/entry.py`에서 sessions root를 배선해 "root 바로 아래"만
   거부할지(그러면 임의 디렉터리 부모도 자식 파일을 얻는다).
2. `SubagentResult.output_recorded` 필드(편차 1)를 계약에 두는 것 — 통합자/ADR-0243에서 명시 필요.
3. stale ctx 수정(편차 2)은 #199 범위를 약간 넘는 기존 결함 수정 — ADR/CHANGELOG에 한 줄.
4. ADR-0243 / 문서(CHANGELOG, ADR-0201·0197 주석, `agent-profiles.md` `output_cap` 행, docs/05)는 통합자 몫 —
   `output_cap` 행에 (c)의 64 KiB 공유 예산을 적어야 한다.
