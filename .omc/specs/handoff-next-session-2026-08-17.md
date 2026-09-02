# 다음 세션 핸드오프 — 2026-08-17

`main` = `51d26fa`. 스위트 **8926 passed / 1 skipped**. 계획 정본 = `.omc/specs/backlog-clusters-2026-08-17.md`.

---

## 0. 시작 전 3분 (건너뛰지 말 것)

```bash
git fetch origin --quiet
git rev-parse --short origin/main       # 51d26fa 가 아니면 아래 전부 재확인
```

🔴 **오너가 세션을 동시에 여러 개 돌린다. 이번 세션에서 실제로 작업 도중 main이
`2ba3261` → `ef2286b` → `51d26fa`로 두 번 움직였다.** 오래 도는 팬아웃 뒤에는 반드시 다시 확인하고,
검증을 돌린 커밋이 아직 HEAD인지 대조할 것.

**PyPI 준비도 측정은 끝났다.** 5축 전부 완료, 결과는 `.omc/specs/release-roadmap-2026-08-18.md`에
정리돼 있고 원문은 `/tmp/claude-1000/-workspaces-aelix-ai/b882fe46-.../scratchpad/pypi_reports/`
(pipeline·blockers·firstrun·honesty·strategy). **그 측정이 이 문서의 §1을 바꿨다 — 아래를 그대로 따를 것.**

**로드맵이 프로젝트 #1에 들어가 있다** (마일스톤 2개 + 17건, Start/Target date 포함, 17/17 검증):
* `v0.1.0-beta.1` — **2026-08-20**, 6.2 AH — #184 #186 #189 #111 #190 #84
* `v0.1.0` — **2026-08-29**, 20.2 AH — #73 #142 #103 #188 #137 #177 #178 #179 #180 #138 #75

에이전트 시간 단위는 [[reference_agent_time_calibration]]: 🔴 **실효 병렬성은 2다**(박스가 2코어).
팬아웃 폭은 토큰엔 공짜지만 wall-clock엔 선형 — 12개 팬아웃 = 85분. 스위트(7~14분)는 팬아웃과 겹치지 말 것.

---

## 1. 바로 시작할 것 — S1 = #184 (github-copilot 로그인)

오너가 **지금 이것 때문에 로그인을 못 한다.** 작고, 베타를 막고 있고, 우회로가 아니다 —
(b)가 클러스터 [20] 전체가 쓸 공유 살균 헬퍼를 만든다.

```bash
git worktree add -b fix/184-copilot-oauth /workspaces/aelix-184 origin/main
```

### 🔴 순서가 중요하다 — 섀도잉을 먼저

`tui/login_wizard.py`:302-305
```python
    except RuntimeError as exc:
        # AuthStorage.login raises RuntimeError("Unknown OAuth provider: ...").
        commit(Text(f"✖ {exc}", style="bold red"))
```
`auth_storage.py:552`를 위해 쓰였지만, **copilot 디바이스 플로우의 모든 에러가 `RuntimeError`**
(`github_copilot.py`:166,172,187,241,264,278,283,322,328,334,508)라서 :306-313의 살균 분기를
**가린다**. f-string 3개를 먼저 고치면 **고친 것처럼 보이면서** `describe_provider_error`를 계속 우회한다.

### 세 조각 (이슈도 이미 3분할됨)

| | 이슈 | 내용 |
|---|---|---|
| (a) | **#184** | 폴링·교환·디바이스코드 POST가 첫 non-2xx에 즉시 포기. 오너 결정: **transient(5xx/네트워크) 재시도**, 기존 `expires_in` 데드라인 + 연속실패 캡으로 경계. `github_copilot.py`:240 / :321 |
| (b) | **#186** | `f"{status} {reason}: {response.text}"` 3곳(`:167 :242 :323`)이 5.4KB HTML을 그대로 화면에. 🔴 **Rich는 CR·BEL은 죽이지만 CSI는 통과시킨다**(실측) |
| (c) | **#187** | MCP 자식 stderr가 부모 fd2로 샘(`mcp/client.py`:231-233, `errlog=` 미지정). 오너 결정: **`/mcp` 뷰어에 링 버퍼**. → 0.1.1, #130과 함께 |

### 🔴 베타 마일스톤에 #184와 같이 있는 것 (측정으로 새로 나옴)

* **#189** — Esc로 첫 실행 마법사를 건너뛰고 아무 말이나 치면 `api_registry.py:149-158`이
  *"Sprint 6a ships … call `register_all()` … OR pass a mock stream_fn"* 를 사용자에게 **두 번** 출력한다.
  첫 실행 경로 전체에서 traceback은 0건인데(양성 대조 확인), **이 문자열 하나가 지웠을 이유다.**
* **#190** — 루트 sdist 10.2MB / 1,168 멤버(테스트 547개 · 죽은 로고 초안 3.7MB ·
  **GOV.UK 캡차 페이지** 717KB). 비밀 유출은 0건. 기존 게이트가 못 잡은 이유 =
  `_is_developer_state()`가 **벌크와 루트 잡파일을 안 본다**.
* **#111** — 정직성 패치. 🔴 `README.md:241`이 *"`--permission-mode plan` blocks every mutating
  tool on the headless path too"* 라고 약속하는데 #188이 반증했다. 베타는 **문구 정정으로 갈음**.

**#184를 재시도만 고치고 닫지 말 것** — 오너가 신고한 건 실패가 아니라 **화면**이다. #186이 남으면
다음 transient에 똑같은 화면이 다시 나온다.

---

## 2. 그 다음 — 베타 발사 (S1 직후, 며칠)

`#111`의 **"태그 전 필수" A-1/A-2/A-3은 이미 전부 고쳐져 있다**(체크만 안 됨, 이 세션에서 HEAD 실측).
B 11건 중 **7건 완료**. 남은 코드는 **B-11(#84 문구 완화)** 하나, 나머지 3건(B-8/B-9/B-10)은 오너 수동.

체크리스트가 모르는 **베타 블로커 3건**:

1. 🔴 **README가 지금 거짓말을 한다** (`README.md:241`) — #188이 반증. 베타는 문구 정정으로 갈음.
2. **#184 / #186 / #189** — 위 S1 + 베타 마일스톤.
3. **#137**을 Known limitations에 추가. **#138은 과장이었다** — 세션 `.jsonl`=0600,
   디렉터리=0700, `auth.json`=0600으로 이미 잠겨 있고 잔여는 **export/백업 노출뿐**이다(실측).

🔴 **지금 두 설치 경로가 다 실패한다**: `install.sh`는 `releases/latest`에서 **404**(태그 0개),
`pip install aelix`는 **플레이스홀더**(`0.0.0a0`, 실행파일 없음, `import aelix` 실패). 그리고 동봉 문서가
`AELIX_VERSION=v0.1.0-beta.1`을 안내하는데 **그 태그가 없다.** 베타 태그가 셋을 동시에 해결한다.

발사:
```bash
git tag v0.1.0-beta.1 && git push origin v0.1.0-beta.1
```
`release.yml:234`의 `if: ${{ !contains(github.ref_name, '-') }}` 때문에 **베타는 PyPI에 아무것도 안 올린다** —
GitHub Releases까지만. #73(Trusted Publisher 등록)도 불필요. **되돌릴 수 있다.**
그리고 `install.sh`의 curl|sh 경로는 **릴리즈가 0개라 지금은 검증 자체가 불가능하다** — 태그가 그 테스트다.

---

## 3. 이후 순서

**S2** [20] 렌더 경계(#177 #178 #179 → #180) → **S3** 세션 안전(#137 **D+C** → #138 **하이브리드** → #139)
→ **S4** 멈춤(#146 #130 #187) → **S5** 권한(#188 #127 #128) → **S6** [21] 폭(#183 #176 #182)
→ **S7** [19] 커넥션(**#173 → #175**, 순서 고정) → **S8** 컨트롤 → **S9** Windows #103

PyPI `0.1.0`은 **#188 실제 수정 · #137 · #103** 이후. #103은 PyPI 메타데이터에 박히는
`Operating System :: OS Independent` 거짓 선언이라 GA 전용 블로커다.
🔴 **PyPI 이름 5개는 이미 선점돼 있다**(`0.0.0a0`, 08-07/08-11) — 서두를 외부 이유가 없다.
🔴 **#73의 지시가 낡았을 수 있다**: "pending publisher"는 프로젝트가 없을 때의 흐름인데
지금은 5개 다 존재한다 → 각 프로젝트 설정의 일반 trusted publisher일 가능성. `strategy` 축이 확인 중.

---

## 4. 작업 규칙 (이 레포에서 실제로 물린 것들)

- **항상 워크트리**. 메인 체크아웃 직접 편집 금지. 커밋 전 헝크 확인, 푸시 전 ancestry 확인, force-push 금지.
- **Python은 `.venv`**: `source /workspaces/aelix-ai/.venv/bin/activate`. **`uv run` 금지**(릴리즈 도구인 `uv build`는 예외).
- **gh는 `.env`의 `GH_TOKEN`**: `set -a && source .env && set +a`. 기본 `GITHUB_TOKEN` 금지. 토큰 값 출력 금지.
- **백그라운드 대기는 내 출력 파일로**. 전역 `pgrep` 금지(다른 세션의 pytest에 걸려 3시간 좀비를 만든 적 있음).
  패턴은 `grep -qE "[0-9]+ (passed|failed)"` — `"passed"`만 쓰면 ruff의 "All checks passed!"에 즉시 걸린다.
- **사보타주는 커밋 이후에. 복원은 `cp`, `git checkout --` 금지**(미커밋 4파일을 실제로 날린 적 있음).
  **복원도 검증할 것 — 백업 대조가 아니라 `git diff` 0 변경으로.**
- **게이트**: `ruff check .` · `python scripts/check_citations.py --check`(현재 828건) ·
  `tests/agents/test_p2_band_boundaries.py`(커널 변경 감시, 주석은 통과·코드는 RED) · 전체 스위트.
- **인용 `file.py:NNN`은 `citations.lock.json`이 감시한다.** 새로 쓸 거면 줄 번호 대신 심볼명을 쓰는 편이 안전하다.
- **ADR 번호는 사흘에 세 번 충돌했다.** 파일명으로 선점하고, 쓰기 전에 `ls docs/decisions/ | tail`.
- 보고·질문은 **한국어**, 코드·커밋·이슈·ADR은 **영어**.

---

## 5. 이번 세션에서 반증된 것 (다시 믿지 말 것)

- 🔴 **#173 본문 헤드라인이 거짓** — google 소켓은 HEAD에서 0.3~0.9ms에 닫힌다. 진짜 결함은
  **호출자 제공 클라이언트**에서만 남고 CPython 리팩카운트가 가린다. 본문이 "안 된다"던
  **핸들 2(`client.aio.aclose()`)는 작동한다**. **그래도 닫지 말 것 — #175가 그 가림막을 없앤다.**
- **#181은 사이트 2→5개, #182는 1→6개** (본문 과소).
- 🔴 **#137 "기반이 이미 있다"는 절반만 맞다.** 입양 기계(`runtime.fork(id,"at")`)는 있고 `/clone`이 이미 쓴다 —
  고아 id를 넣으면 입양된다(실측). 하지만 **aelix `/tree`는 세션 *파일* 계보**이지 엔트리 숲이 아니다
  (pi의 `/tree`와 이름만 같다). leaf 열거 0줄. 🔴 `--fork`/`/import`는 고아를 복사해 **거기서 다시 고아로 만든다**.
- 🔴 **leaf 열거의 순진한 규칙은 오경보** — 평범한 rewind가 2 leaves로 잡힌다(대조군 실측).
  올바른 규칙: `parented = {parent_id} ∪ {leaf 엔트리의 target_id}`, 후보에서 `type=="leaf"` 제외.
- **#111 체크박스 32개는 전부 미체크지만 실제로는 10건이 완료돼 있다.** 체크박스를 믿지 말 것.
- **#23 첫 실행 온보딩은 이미 작동한다**(2026-08-10 종료, 실측 발화 확인) — 미구현이라 적힌 문서를 믿지 말 것.
- **#74(문서 정직성)는 완료됨**(2026-08-08), 산출물 3개 다 HEAD에서 확인.
- **#141 import 비용은 1.4~2.2초가 아니라 1.06초**(클린 venv `env -i`).
- **마켓플레이스는 비어 있다** — `extensions: []`, 2026-07-31 이후 미변경. 배관 자체는 정직하다.
- 🔴 **`gh project item-list --format json`은 date 필드를 출력하지 않는다.** 뮤테이션이 성공해도
  `—`로 보인다. 날짜 확인은 GraphQL `ProjectV2ItemFieldDateValue`로 할 것.
