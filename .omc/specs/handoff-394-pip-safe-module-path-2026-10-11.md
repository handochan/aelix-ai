# Handoff — #394 pip 모듈 검색 경로 보호 (2026-10-11)

## 기준점

- 구현 기준 main: `162bc5136517436fca365fd73d33ebee2eb32c3c` (#407 / PR #438 머지).
- 소유 작업 트리: `/Users/handochan/dev/aelix-ai-worktrees/codex-394`.
- 브랜치: `fix/394-pip-safe-module-path`; 이 변경은 #394 하나만 다룬다.
- Root가 확보한 issue lock: `.git/codex-issue-locks/394/owner.json`, run
  `beta3-security-394-2026-10-11`. GitHub/Project/PR와 lock 갱신은 root가 담당한다.
- 원본 `/Users/handochan/dev/aelix-ai/{CLAUDE.md,AGENTS.md}`와 #407 handoff를 읽었다.
  새 트리에 ignored 정책 파일을 복사하거나 커밋하지 않았다.
- 사전 조사 두 `/tmp/aelix-394-*` 문서는 계획이다. 이번 실행 증거와 구분한다.
- 자기 venv: CPython 3.12.13, pip 26.2.1, hatchling 1.32.4. `uv sync --all-packages`
  이후 `uv pip install --python .venv/bin/python 'pip==26.2.1' 'hatchling==1.32.4'`.
  빌더는 로컬 검증 준비이며 production dependency/lock 변경은 없다.

## 변경과 검증

`InstallBackend.install_prefix`, `uninstall_args`, `build_download_args`의 pip
prefix에 `-m` 앞 `-P`를 추가했다. 검증 후 로컬 설치는 기존 install prefix를
재사용한다. Typed 상대 경로/cwd, catalog·record의 owned cwd, pip flags/config,
사용자 Python 환경 및 uv argv/선정 정책은 그대로다. #289 라우팅과 #402 Git parser를
바꾸지 않았다. ADR-0255 §12·§15·새 §17, ADR-0200, ADR 색인, private-catalog guide와
bundled mirror, CHANGELOG를 갱신했다.

실행 로그: `.omc/probes/394-implementation/` (ignored, 작업 트리에 보존).

- `uv run pytest tests/cli/test_pip_module_search_path_394.py tests/cli/test_extension_install.py tests/cli/test_catalog_install_neutral_cwd_392.py tests/cli/test_record_spec_never_read_from_cwd_405.py tests/cli/test_git_revision_402.py tests/cli/test_extension_install_verdict.py tests/cli/test_catalog_relative_source_131.py -q`
  → **1145 passed, 2 skipped in 33.58s**, exit 0 (`focused.log`).
- 새 9개 회귀는 실제 CPython child에 production argv를 그대로 전달한다. 절대 trusted
  `PYTHONPATH` shim과 hostile cwd `pip/`를 놓고 install/remove/download/local rewrite의
  `-P` 제거 대조군 모두 hostile 실행·marker를 확인한다. 수정 경로는 trusted module,
  safe_path true, 같은 cwd·pip tail/config를 확인한다. Install verification on/off,
  remove record cleanup, download→pin→local install·temp cleanup도 관찰한다.
  Shim은 설치 증거가 아니다. 실제 distribution/entry point/BOUND 확인은 아래 미실행 범위다.
- `uv run ruff check .` → **All checks passed**, exit 0 (`ruff.log`).
- `uv run ruff format --check tests/cli/test_pip_module_search_path_394.py`
  → **1 file already formatted**, exit 0. 기존 두 Python 파일의 format check는 base에서도
  exit 1이므로 넓은 formatter를 적용하지 않았다.
- `uv run python scripts/check_types.py` → **288 files / 0 errors / 3 inverse
  assertions alive**, exit 0 (`types.log`).
- `uv run pyright --pythonplatform Windows` → **0 errors, 1 warning**, exit 0
  (`types-windows.log`): 기존 SessionStorage TypeVar 공변성 warning이다.
- `uv run python scripts/sync_bundled_docs.py` → **9 guides / 1 updated / 0 removed**.
- 변경된 target과 현재 anchor를 확인한 뒤 `uv run python scripts/check_citations.py --fix`
  → **0 relocated / 547 anchors / 934 gated**; `--check` → **none drifted**, exit 0.
- `uv run pytest tests/test_docs_bundle_sync.py tests/packaging_gate/test_docs_bundle.py tests/docs tests/test_citation_drift.py tests/test_env_sandbox_windows.py -q`
  → **65 passed in 5.45s**, exit 0 (`docs-citation-env.log`).
- `git diff --check` → exit 0.

Pi 최신 main을 `gh api`로 다시 확인했다: `ea448f454af474f3e2fe9733b331e17b0662f5d6`.
Pinned package-manager source와 #10202를 읽었다. `gh search issues 'pip cwd' --repo
earendil-works/pi`는 0건. Python 3.11 공식 `-P`와 `PYTHONPATH` 계약을 확인했다.

## 바로 시작할 것

Root 전달 기준: 초기 구현 `b9beb974adc9918e361ffd44db7ae8f611b3fbaa`에 대해 독립 Codex
리뷰는 blocker 없음(실제 CPython probe 51개 및 테스트 667개), Claude 정적 리뷰도
blocker 없음이다. Claude가 제안한 현재형 docstring 정정은 이번 amend에 반영했다:
module 소개의 pip dialect와 `catalog_installer_cwd`·#392 테스트의 `python -m pip`
설명을 #394 이전의 측정으로 한정했다. Runtime 코드·argv는 바꾸지 않았다.
`_backend_missing_message`의 실제 현재 안내는 uv 재설치(`--with pip`), uv 설치,
pip 있는 일반 virtualenv의 세 선택지임을 확인했다. 잘못된 finding에 따라 바꾸지 않았다.

리뷰 정정 검증: 두 Python 파일의 docstring 제거 AST는 초기 구현과 동일
(`review-runtime-ast.log`). Targeted ruff와 `git diff --check`는 exit 0.
`uv run pytest tests/cli/test_catalog_install_neutral_cwd_392.py tests/test_docs_bundle_sync.py tests/docs tests/test_citation_drift.py -q`
→ **331 passed, 1 skipped in 19.86s**, exit 0 (`review-focused.log`). Citation
`--fix`는 **0 relocated**, `--check`는 **934 gated / none drifted**, exit 0.

Root는 같은 초기 구현을 scratch 실제 pip CLI로 실행해 typed wheel install/remove,
download→verified local install·default verification의 marker 부재와 실제 metadata/BOUND를
확인했다고 전달했다. 증거는 `.omc/probes/394-root/`에 root가 관리한다. 이 레인은 그
실행을 직접 수행했다고 주장하지 않는다. Root의 catalog/update owned cwd와 uv control
검증은 계속 진행 중이다. Root가 최종 검증 결과를 별도로 기록한다.
그 뒤 root 지시가 있을 때 frozen source로 full pytest를 실행한다. **구현 레인은
전체 pytest를 시작하지 않았다.** 모델/TUI 변경은 없으며 실제 모델/TUI를 검증했다고
주장하지 않는다. Push/PR/CI/merge도 이 레인에서 수행하지 않았다.

## 이후 순서와 실제 규칙

사용자 승인된 보안 배치의 다음 이슈는 #289, 이후 #365→#390→#395→#179→#416.
Root가 각 이슈를 별도 branch/commit/review로 처리한다. 미래 PR 머지는 별도 명시 승인이
필요하다. Full run 중 source/index/rebase/venv를 바꾸지 않는다. Canonical `uv run`의
PATH를 자식에게 전달하고 venv Python symlink를 resolve하지 않는다. Owner `.env`,
credentials, `~/.aelix`에 접근하지 않고 HOME/CODEX_HOME을 덮어쓰지 않았다.

## 반증된 것 — 다시 믿지 말 것

- `-P`는 Python 전체 격리가 아니다. 명시적으로 cwd를 넣은 `PYTHONPATH` 대조군에서
  safe_path true여도 hostile pip가 실행된다. 사용자/site 설정은 기존 신뢰 경계 안이다.
- 반환 코드 0이나 shim 선택만으로 실제 extension 설치/BOUND를 주장할 수 없다.
- pip availability를 찾는 parent `find_spec`나 CLI 시작 경로는 이번 수정 대상이 아니다.
- Pi의 npm `--prefix`는 설치 root 선택이며 Python `-m` 모듈 검색의 해결책이 아니다.
- 준비 문서에 적힌 라이브/전체 검증 후보 명령은 실행 증거가 아니다.


## Root 검증 패스 — 최종 소스 1c5e7a62

- Full 명령: `uv run --no-sync pytest -p no:cacheprovider -q`. **15650 passed, 25 skipped, 104 warnings in 664.74s, exit0**. 소스/venv/index/rebase를 실행 중 바꾸지 않았다. `.omc/probes/394-final/full-suite-py312.out`/`full-command.json`.
- 독립 Codex: 51개 CPython3.11~3.14 boundary/counterfactual 관찰 통과, focused667passed/2skipped. 실제3.14 interpreter probe는-P의 의미만 확인하며 Aelix3.14 지원을 주장하지 않는다. 새head의 docstring 제외AST 동일도 독립확인. 기존focusedfixture가임시venv real-uv/HOME monkeypatch를 사용한 범위를 report에 구분했다. 독립 probe 자체는 설치/HOME변경 없고 owner파일접근 없음.
- 별도 fresh Claude CLI: safe-mode/restricted/Read·Grep·Glob만, source-only no blocker. 테스트/실제CLI/Windows 실행은 수행하지 않았다. 원본규칙을 prompt로 전달했고 authorhandoff/rootlive를 읽지 않고 판단했다. `.omc/probes/394-claude-review/`.
- root 실제 standalone CLI, bare Aelix/pip26.2.1의 disposablevenv: base162 install/remove/download 모두 hostilecwd pip 실행. install rc0인데dist없음, remove rc0인데dist남음. Fix는-P를 포함한 원래productionargv로 실제wheel install/EP, remove/dist부재, loopbackPyPI download→verifiedlocalinstall 두 단계 및 임시dircleanup/pin저장을 확인. 가짜pip marker없음, 실제manifestCLI verify BOUND/rc0.
- root 실제 catalog index→등록→refresh→install→recordedupdate→BOUND 전부rc0. pipchild 모두owned agent/installer-cwd, -P prefix. Actualpip없는venv에서uv install/BOUND/remove rc0, PyPI verification은예정된failclosed exit2. uv argv에-P없음/정확한--python/typedcwd확인.
- root live는 scratch agent/settings/cwd/config/cache, providercredentials/env읽기없음, HOME변경없음, owner~/.aelix접근없음. subprocessargvlogger는 관찰만 하고 실제명령을재구성하지 않았다. Kit `.omc/probes/394-root/`.
- 첫 catalog harness는schema name을id로오해했고, 기존sameversiondist가있던fixture의record name이없어update가manualPyPI로해석됐다. freshdist/새fixture로고쳐실제recordedupdate검증. 앞선실패로그 controls-run-attempt2.out 보존. production수정으로해결한실패가아니다.
- Parentpip availability의cwd-only falsepositive는safechild실패로끝나는별도한계다. 직접사용자ensurepip안내는이번생산pipargv범위밖으로유지했다.

Root추가뒤 amend는이handoff만바꾼다. 소스/tests와실제검증head1c5의동일성을gitdiff로확인한다. 다음은finalhead push/PR/각event8CI잡이다. 이후같은검토커밋을기준으로289를별도branch/tree/context에서진행한다. 미래batchmerge는아직명시승인되지않았다.
