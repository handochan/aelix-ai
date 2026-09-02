# Recovery report — 코드스페이스 컨테이너 소실과 로컬 복원 (2026-09-02)

원본: `/workspaces/aelix-ai` (GitHub Codespace). 컨테이너가 사고로 소멸했고, 남은 것은
`aelix-export-2026-09-02.tar.gz` (81 MB, 9,418 엔트리) 하나뿐이다. 아카이브 루트의
`RESTORE.md`가 절차를 남겼다 — 사본은 `.omc/restore-2026-09-02/RESTORE.md`.

복원 대상: `/Users/handochan/dev/aelix-ai` (macOS). 이 체크아웃은 **이미 존재했고**
`origin/main`만 따라오던 상태였다. 그래서 RESTORE.md 1단계(빈 곳에 풀기)를 그대로
쓰지 않고, **refs는 fetch로 / 추적 안 되는 파일은 병합으로** 들여왔다.

## 한 줄 결론

**git에 있던 것은 전부 돌아왔다.** `main` tip이 양쪽 모두 `2821b7f`로 동일했으므로
커밋 유실은 애초에 없었고, 실제로 위험했던 것은 **git 밖**에 있던 것들 —
stash 2개, 로컬 전용 브랜치 65개, 그리고 추적되지 않는 컨텍스트 파일 1,646개다.
그중 1,642개를 되살렸다. 영구 소실은 트랜스크립트뿐이며 그것은 이번 사고가 아니라
**2026-08-21 사고 때 이미 잃은 것**이다.

## 1. 복원 대조표

| 항목 | 복원 전 | 복원 후 | 근거 |
|---|---|---|---|
| 로컬 브랜치 | 1 (`main`) | **66** | `git for-each-ref refs/heads` |
| origin/main에 없는 커밋을 가진 브랜치 | — | 6 | `pr119`(2), `feat/89-default-catalog-url`(2), `feat/76-beta-release-track`(2), `feat/rpc-channel-selector`(1), `feat/88-catalog-sign-cli`(1), `docs/extension-authoring-honesty`(1) |
| 새 객체를 실제로 가져온 브랜치 | — | 1 | `pr119` tip `1a4e456e`만 로컬에 부재였다 |
| stash | 0 | **2** | reflog가 코드스페이스 사본과 **바이트 동일** |
| 태그 | `v0.1.0-beta.1` | 동일 | 이미 있었음 |
| 워크트리 | 0 | **9** | 8개 형제 + 임베디드 1개 |
| 추적 안 되는 파일 | — | **1,632 제자리 + 10 이전** | 4건은 의도적 제외 |

### stash — 가장 값나갔던 것

`stash@{1}` `probe99c`(2026-07-10)는 **17개 파일, +1,925 −160**이다. copilot OAuth,
`_error_hints`, entry router, runtime bootstrap, runnable models, TUI chrome/shell과
테스트 6종. origin 어디에도 없고 브랜치에도 없다. stash 커밋은 이전 stash와 부모로
연결되지 않으므로 `refs/stash` tip만 가져오면 `stash@{1}`은 따라오지 않는다 —
두 커밋(`c391d4dc`, `a536417b`)을 각각 fetch한 뒤 `.git/logs/refs/stash`를 원본 그대로
복사해 복원했다. `git update-ref`는 자체 reflog 엔트리를 덧붙이므로(3개가 됐다)
ref를 세운 **뒤** reflog를 다시 덮어써야 2개로 맞는다.

`stash@{0}`은 README 미커밋분이고 메시지가 스스로 `docs/beta-readme-refresh`에
의해 대체됐다고 적고 있다.

## 2. 되살린 컨텍스트 (git 밖)

| 경로 | 파일 수 | 내용 |
|---|---|---|
| `.omc/recovered-91/` | 217 | #91 죽은 세션 복구 산출물 (`recovery-report-dead-session-91.md`가 참조) |
| `.omc/state/` | 126 | mission-state, agent-replay, 세션별 상태 |
| `.omc/wiki/` | 88 | 세션 로그 85건 + `index.md` + `log.md`(422줄) |
| `.omc/sessions/` | 63 | 세션 기록 |
| `.omc/specs/` | 50 | handoff·sprint spec·backlog audit·release roadmap |
| `.omc/internal-marketplace-kit/` | 29 | airgap / catalog-repo / client / homepage |
| `.omc/preview/` | 12 | 마켓플레이스 프리뷰 |
| `docs/assets/` | 20 | 터미널 로고(ansi/txt), `demo.mp4`, 로고 아카이브 |
| `.omc/notepad.md`, `project-memory.json` + `.tmp` 스냅샷 18 | 20 | 5/22 이후 프로젝트 메모리 |

### 제외 4건 (판단)

`ax.html`(lander 리다이렉트 114 B), `te.html`(0 B), `uk4414917.html`(GOV.UK 캡차 페이지
717 KB), `CLAUDE.md`(0 B). 전부 내용이 없거나 프로젝트와 무관한 잔해다.

### 이전 10건

엉뚱한 cwd에서 실행된 OMC가 `docs/assets/`, `packages/aelix-coding-agent/src/…/`,
`.omc/specs/` 안에 만들어 둔 `.omc/state/` 9건 — 제자리에 복원하면 **소스 트리를
오염시키므로** `.omc/restore-2026-09-02/misplaced-omc-state/`에 경로 구조를 보존해
옮겼다. 루트의 `aelix-session-*.html`(8 KB)도 같은 폴더로.

## 3. 충돌 4건과 처리

| 파일 | 로컬 | 코드스페이스 | 처리 |
|---|---|---|---|
| `AGENTS.md` | 10,622 B, 6/22, 한국어 | 4,446 B, 6/21, 영어 | **로컬 유지.** 더 새롭고 더 크다. 코드스페이스본은 `.omc/restore-2026-09-02/AGENTS.codespace-2026-06-21.md` |
| `.omc/project-memory.json` | 3,953 B | 11,570 B | **코드스페이스본 채택** — `hotPaths` 50건과 `directoryMap` 12건이 여기에만 있다. `projectRoot`는 로컬 경로로 교체 |
| `.claude/settings.local.json` | `ps -Ao …` | `bash *` | **합집합** |
| `.omc/state/hud-stdin-cache.json` | 진행 중 세션 | 죽은 세션 | **로컬 유지** |

`project-memory.json`의 `testCommand`는 양쪽 다 못 썼다 — 코드스페이스본은 마지막
세션의 일회성 `-k` 필터에 macOS에 없는 `timeout`을 쓰고 있었고, 로컬본은 복원 작업 중
실행한 셸 명령이 그대로 기록돼 있었다. `AGENTS.md:87-89`가 적어둔
`uv run pytest` / `uv run ruff check .`로 정정했다.

## 4. 워크트리 — RESTORE.md와 다르게 한 부분

RESTORE.md 3단계는 형제 디렉터리(`../aelix-site` 등)를 만든다. 대신 **9개 전부
`.claude/worktrees/` 아래**에 두었다. 이유는 이 저장소가 스스로 적어둔 것이다:

- `.gitignore`와 **pyproject 5개 × exclude 목록 2개**가 이미 `.claude`를 전부 막고 있다.
- `.gitignore` 주석: *"The agent worktrees this repo is developed in LIVE under `.claude/worktrees/`"* — 원래 관행이 그것이었다.
- #143에서 `.claude/worktrees/` 아래 전체 체크아웃이 빌드 산출물로 새어나간 전례가 있다.
  루트에 `.worktrees/`를 새로 만들면 **10곳**에 exclude를 추가해야 같은 수준으로 막힌다.

8개 형제 워크트리의 HEAD는 코드스페이스와 **전부 일치**한다:

| 워크트리 | 브랜치 | HEAD |
|---|---|---|
| `aelix-site` | `feat/site-light-dark` | `b716994` |
| `aelix-catalog` | `feat/172-catalog-overlay` | `bff797f` |
| `aelix-lockgate` | `chore/lock-manifest-gate` | `6138f4d` |
| `aelix-readme` | `docs/demo-gif-2x` | `e5f32fb` |
| `aelix-beta-release` | `feat/89-default-catalog-url` | `7bf0a78` |
| `aelix-docs-fix` | `docs/extension-authoring-honesty` | `7b8ffbc` |
| `aelix-137` | `fix/137-session-concurrent-writers` | `2ba3261` |
| `aelix-phaseB` | `phaseB-providers` | `c5263ba` |

임베디드 `wf_fb25c427-bb1-1`(1,011 파일)은 `.git` 파일이 **절대경로**
(`gitdir: /workspaces/…`)를 담고 있어 그대로는 깨진다. 아카이브의
`.git/worktrees/wf_fb25c427-bb1-1` 관리 디렉터리를 복사한 뒤 `git worktree repair`로
양방향 포인터를 고쳤다. 미커밋 작업 3건이 살아 있다:

```
 M packages/aelix-agent-core/src/aelix_agent_core/harness/core.py
 M packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py
?? .omc/specs/rpc-review-kernel-risk.md
```

아카이브 `stray/`의 미커밋 2건도 제자리에 넣었다 —
`aelix-137/.gate-137-baseline.txt`, `aelix-phaseB/.omc/specs/phase-b-provider-adapters-handoff.md`.

## 5. `.gitignore` 2줄 추가 — 유일한 추적 파일 변경

`env.txt`(프로바이더 키·PyPI 토큰 평문)와 `aelix-export-*.tar.gz`(81 MB) **둘 다
어떤 ignore 규칙에도 걸리지 않았다.** sdist의 기본 파일 집합은 저장소 루트 전체이므로
후자는 릴리스 타르볼에 실려 나갈 수 있었다. 두 패턴을 "Local environment" 블록에 추가.

타르볼 패턴은 `/`로 **앵커했다** — 이 파일의 `.claude` 블록이 기록한 이유와 같다.
앵커 없는 규칙은 워크트리 안에서 돈 빌드의 루트 경로 자체와 매칭돼 hatchling이
이 파일 전체를 버리게 만든다.

## 6. 검증

```
.venv/bin/python -m pytest tests/packaging_gate/test_build_hygiene.py tests/cli/test_entry_router.py -q
53 passed in 7.65s
```

`test_build_hygiene.py`를 함께 돌린 것은 hatchling이 `.gitignore` 줄들을 자기
exclude 스펙에 이어 붙이기 때문이다 — 이 파일을 건드리면 빌드가 무엇을 싣는지가 바뀐다.
`test_entry_router.py`는 RESTORE.md 5단계가 지정한 스모크다.

추적 안 되는 파일 집합을 코드스페이스와 대조한 결과 **차이는 의도한 4건뿐**이다
(제외한 junk HTML 3건 + 로컬에만 있는 아카이브 타르볼 1건).

`.venv`는 재생성하지 않았다. RESTORE.md 5단계는 코드스페이스의 깨진 venv를 전제하지만
로컬 `.venv`는 Python 3.12.13에 editable 설치가 이미 정상이었다 — 세 패키지 모두 import 확인.

## 7. 영구 소실 — 이번 사고와 무관

**2026-08-22 09:42 이전 Claude Code 세션 트랜스크립트 전부.** 2026-08-21 18:51
컨테이너 종료 후 자동 복구가 `$HOME`을 갈아엎었고 `~/.claude/projects/*/*.jsonl`은
서버 사본이 없다. 이번 아카이브가 만들어지기 전에 이미 없어진 것이므로 이번 복원으로
되찾을 수 있는 것이 아니다.

대체 기록은 남아 있고 전부 위에서 복원했다 — `.omc/specs/handoff-*.md`,
`.omc/wiki/` 세션 로그 85건, `.omc/sessions/` 63건, `.omc/state/mission-state.json`,
`project-memory.json` 스냅샷 18개, 그리고 git 히스토리.

로컬에서는 홈 디렉터리가 컨테이너 레이어가 아니므로 이 사고 자체가 재발하지 않는다.

## 8. 남은 수동 작업

RESTORE.md 4단계 — `aelix-marketplace`는 워크트리가 아니라 **독립 리포**이고 미커밋이
없다. 이 저장소 밖의 일이라 손대지 않았다:

```bash
git clone https://github.com/handochan/aelix-marketplace.git ~/dev/aelix-marketplace
```
