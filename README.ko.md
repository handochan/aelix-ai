<p align="center">
  <img src="https://raw.githubusercontent.com/handochan/aelix-ai/main/docs/assets/brand/lockup-stacked.png" width="360" alt="Aelix — A×X 마크와 Aelix 워드마크">
</p>

**당신만의 에이전트 세계를, 파이썬 생태계 위에.**

순수 파이썬으로 만든 에이전트 런타임이자 확장 플랫폼입니다 — 직접 호스팅하고, 모든 코드를
감사하고, 팀이 이미 쓰는 언어로 확장하세요. 이미 지불하고 있는 모델 예산 위에서.

[English README →](README.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

<p align="center">
  <img src="docs/assets/demo.gif" width="100%" alt="Aelix 데모 — 에이전트가 pandas 확장을 my_ext.py에 직접 작성하고, /reload로 재시작 없이 핫리로드한 뒤, 바로 다음 프롬프트에서 in-process로 실행">
</p>
<p align="center"><em>에이전트가 스스로를 확장합니다: pandas <code>describe_dataset</code> 툴을 <code>my_ext.py</code>에 직접 작성하고, <code>/reload</code>로 재시작 없이 핫리로드한 뒤, 바로 다음 프롬프트에서 in-process로 실행합니다. 대기 구간은 빨리감기 처리했습니다.</em></p>

Aelix는 오늘 이 런타임 위의 터미널 에이전트로 먼저 제공됩니다 — 첫 워크로드일 뿐, 경계가
아닙니다. 실행되는 모든 코드를 직접 읽을
수 있고, 에이전트 전체를 사내 경계 안에 두고 운영하며, 평범한 파이썬 함수로 확장합니다.
확장은 in-process로 동작하므로 pandas·내부 SDK·웨어하우스 클라이언트 같은 기존 스택을
그대로 import해 쓸 수 있습니다 — 데이터·ML 팀이 가장 먼저 찾는 이유입니다. 그리고 어떤
정보도 외부로 전송하지 않습니다.

---

## 왜 Aelix인가

- 🐍 **확장은 그냥 파이썬입니다.** 툴은 평범한 함수 하나입니다 — 플러그인 언어도, 프로세스
  밖 브리지도 없습니다. 터미널·노트북·파이프라인·CI 어디서든 에이전트를 구동하세요.
  [예제 보기 ↓](#확장은-그냥-파이썬입니다--데이터-스택을-in-process로)
- 💳 **이미 보유한 예산으로 돌아갑니다.** Anthropic, OpenAI, Gemini/Vertex, OpenRouter,
  Cloudflare, 그리고 GitHub Copilot — 이미 로그인해 쓰고 있는 individual/Business/Enterprise
  좌석 포함(사용은 귀사의 GitHub 계약 조건을 따릅니다) — 네이티브 어댑터를 제공합니다. 싼
  작업과 어려운 추론을 한 세션에서 다른 모델로 라우팅하세요. 종량제 ACU도, 새 벤더 계약도
  없습니다.
- 🔏 **켜서 쓰는 퍼블리셔 서명.** Ed25519 서명 + SHA-256 핀 툴체인이 완비되어 있고
  (`extension keygen | sign | trust`), `extension install --require-signature`는
  fail-closed입니다 — 서명이 없거나 신뢰되지 않은 팩은 거부됩니다. 다만 **기본값은
  아닙니다**: 아직 퍼스트파티 키가 프로비저닝되지 않았으므로, 이 플래그 없이는 서명이
  없어도 최초 사용 시 그대로 통과합니다. [SECURITY.md](SECURITY.md)를 참고하세요.
- 🔍 **감사 가능한 자체 호스팅.** 완전한 오픈소스, 텔레메트리 없음. `--offline`은 최초
  사용 시의 `rg`/`fd` 다운로드와 확장 카탈로그 fetch를 건너뜁니다(모델 호출 자체를
  차단하지는 않습니다 — 호출은 설정한 프로바이더로 그대로 나갑니다). 신뢰는 직접 읽을 수
  있는 코드에서 나옵니다 — *"내가 만들지 않은 에이전트를 왜 돌리는가?"* 에 대한 답입니다.
- 🧩 **코어까지 확장 가능.** 정책·권한·가드레일조차 교체 가능한 내장 확장으로 제공되는 작은
  커널과, 하나의 넓은 `ExtensionAPI` — 툴, 슬래시 명령, 프로바이더, 메시지 렌더러, 테마,
  그리고 자체 `/login` 플로우(SSO/사번 인증)까지 — 를 제공합니다. 재시작 없는 라이브
  핫리로드를 지원합니다.
- ⚙️ **스크립트·헤드리스 구동.** `--print`, 라인 단위 `--mode json`, `--mode rpc` JSONL
  프로토콜로 파이프라인·CI·평가 루프에 그대로 임베드할 수 있습니다 — 결정적이고 기계가
  읽을 수 있는 출력.

## 설치

베타 기간에는 체크섬 검증 인스톨러를 통해 GitHub Releases에서 설치합니다. 필요하면
[uv](https://docs.astral.sh/uv/)를 자동으로 설치하고, 모든 *Aelix* wheel을 릴리즈의
`SHA256SUMS` 매니페스트와 대조한 뒤(불일치 시 즉시 중단), 그 매니페스트가 지목한 정확한
버전으로 **고정해서** 전역 `aelix` 명령을 설치합니다 — 검증한 wheel이 곧 실제로 설치되는
wheel이 되도록. 서드파티 의존성은 평소대로 PyPI에서 해석됩니다:

```bash
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

`AELIX_VERSION=v0.1.0-beta.1`로 릴리즈를 고정하고(베타 기간 권장), `AELIX_EXTRAS`로
extras를 선택하세요 — 기본값은 `tui`, `tui,images`는 터미널 인라인 이미지 렌더링을
추가하고, 비워두면(`AELIX_EXTRAS=`) TUI 없는 헤드리스 CLI(print/json/rpc)만 설치됩니다.

**업그레이드 / 제거.** 같은 `curl … | sh` 한 줄을 다시 실행하면 업그레이드됩니다 — 최신
릴리즈를 찾아 체크섬 검증 설치를 처음부터 다시 수행합니다(`uv tool install --force`라
재실행이 멱등입니다). 제거는 `uv tool uninstall aelix`.

> **GA 전까지 PyPI에 있는 것은 자리맡기용 껍데기입니다 — 위 인스톨러를 쓰세요.**
> `pip install aelix` / `pipx install aelix` / `uv tool install aelix`는 오늘 실패하지
> **않습니다**. 그게 함정입니다. 이름을 선점해 두려고 올린 1.3 kB짜리 메타데이터 전용
> `0.0.0a0` 릴리즈가 있어서, 저 명령들은 성공했다고 말해 놓고 실제로 쓸 수 있는 것은
> 아무것도 설치하지 않습니다. 설치가 끝나도 `aelix` 명령은 없습니다. 베타 기간 내내 진짜
> Aelix는 GitHub Releases와 위의 체크섬 검증 인스톨러로만 배포됩니다. 자리맡기 버전은
> 사전 릴리즈(pre-release)라 진짜 릴리즈를 절대 앞지르지 못합니다 — 첫 GA가 게시되는
> 순간부터 `uv tool install 'aelix[tui]'`(또는 `pipx`/`pip`)가 그대로 동작합니다.

```bash
aelix                                            # 인터랙티브 에이전트 (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # 일회성 헤드리스 실행
aelix --offline                                  # rg/fd·카탈로그 fetch 건너뛰기
aelix --help
```

`grep`/`find`를 처음 쓸 때 Aelix는 `ripgrep`과 `fd`를 각 upstream GitHub Releases에서
`~/.aelix/agent/bin`으로 내려받습니다(두 도구가 `.gitignore`를 존중하도록). 런타임에
가져오는 바이너리는 이 둘뿐이고, `--offline`이면 건너뜁니다 — `PATH`에 시스템 사본이
있으면 그쪽을 우선합니다.

`aelix`에는 프로바이더 자격증명이 필요합니다 — `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`를 설정하거나, `aelix`를 실행한 뒤 TUI 안에서 `/login`을 입력하거나
(Copilot/구독 OAuth), `--api-key`를 넘기거나, `~/.aelix/agent/models.json`을 구성하세요.
자세한 내용은 [프로바이더 가이드](docs/guides/providers-and-models.md)를 참고하세요.

에이전트 위임을 쓸 계획이라면 `--api-key`보다 환경변수나 `/login`을 쓰세요. `--api-key`는
부모 프로세스의 메모리에만 존재하므로 위임된 자식은 그 값을 **받지 못합니다**. 대신 자식은
새 프로세스가 늘 하는 방식으로 자기 자격증명을 스스로 해석합니다 — `auth.json`(`/login`이
쓰는 파일)과 상속받은 환경변수에서요. 그래서 둘 중 하나에 해당 프로바이더의 키가 있으면
정상 인증되고, **둘 다 없을 때만** 첫 턴 전에 `No API key found for <provider>`로 멈춥니다.

## 프로바이더

litellm도, 범용 래퍼 레이어도 없는 프로바이더별 수제 네이티브 어댑터입니다(OpenRouter와
Cloudflare Workers AI는 공유 OpenAI-completions 어댑터 위에서 동작합니다). 프로바이더별
동작 분기를 통해 Anthropic thinking-block replay, 모델별 `/responses` vs
`/chat/completions` 라우팅, Copilot 엔터프라이즈 호스트 해석 같은 고유 동작을 뭉개지 않고
보존합니다.

| 프로바이더 | 상태 |
|---|---|
| Anthropic (Messages) | ✅ 지원 |
| OpenAI (chat completions) | ✅ 지원 |
| OpenRouter | ✅ 지원 |
| GitHub Copilot (individual / Business) | ✅ 지원 |
| GitHub Copilot (Enterprise) | 🧪 미검증 |
| OpenAI Responses API | 🧪 실험적 |
| Google Gemini / Vertex | 🧪 실험적 |
| Cloudflare Workers AI | 🧪 실험적 |

Copilot Enterprise를 미검증으로 표시한 이유: 라이브 검증은 유료 individual 좌석과 Copilot
Business 좌석에서만 이뤄졌고, Aelix가 싣고 있는 엔드포인트 카탈로그는 정적이라 모든
엔터프라이즈 호스트/플랜에서 맞는다고 보장할 수 없습니다. 동작할 가능성은 충분하지만, 저희가
확인한 범위는 아닙니다.

번들 모델 카탈로그에 실려 있지만 **이 빌드에 어댑터가 없어** 실행할 수 없는 프로바이더가
셋 있습니다: `amazon-bedrock`(`bedrock-converse-stream`), `azure-openai-responses`,
`mistral`(`mistral-conversations`). 첫 턴에서 실패시키는 대신 아예 숨기므로
`--list-models`와 `/model` 피커에 나타나지 않습니다.
[프로바이더 가이드](docs/guides/providers-and-models.md)를 참고하세요.

## 확장은 그냥 파이썬입니다 — 데이터 스택을 in-process로

Aelix 확장은 `setup(aelix)` 함수 하나입니다. 별도의 플러그인 언어도, 프로세스 밖 브리지도
없으므로 툴이 기존 스택을 그대로 import해 결과를 모델에 바로 돌려줄 수 있습니다 — Aelix가
데이터·ML 팀을 가장 먼저 겨냥해 만들어진 이유입니다:

```python
# my_ext.py  —  ~20줄짜리 데이터 툴; 로드:  aelix -e ./my_ext.py
from typing import Any
import pandas as pd                       # 당신의 의존성, in-process로 import

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.messages import TextContent


async def _describe(args: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
    df = pd.read_parquet(args["path"])     # 웨어하우스 쿼리, 내부 SDK 호출도 가능…
    return ToolResult(content=[TextContent(text=df.describe().to_markdown())])


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(AgentTool(
        name="describe_dataset",
        description="Summary statistics for a Parquet/CSV dataset.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the dataset."}},
            "required": ["path"],
        },
        execute=_describe,
    ))
```

같은 `ExtensionAPI`로 슬래시 명령, 프로바이더, 메시지 렌더러, 테마, 커스텀 `/login`
플로우까지 등록할 수 있고, 모든 확장은 **세션 재시작 없이 핫리로드**됩니다.

**파이썬이 도는 곳이면 어디든 임베드하세요.** 노트북, Airflow/Prefect/Dagster 태스크, CI
잡에서 헤드리스로 구동할 수 있습니다:

```bash
aelix --print "profile data/train.parquet and flag columns with >5% nulls"
aelix --mode json "run the eval suite and summarise failures"   # 라인 단위 이벤트
```

전체 API 표면은 [확장 작성하기](docs/guides/extension-authoring.md)를 보세요.
[Aelix Marketplace](https://handochan.github.io/aelix-marketplace/)는 Aelix가 기본으로 읽는
공식 카탈로그입니다. 베타에서는 비어 있는 상태로 시작하며, 등록 신청을 받고 있습니다.

## 신뢰와 자체 호스팅

Aelix는 폐쇄망과 고객사 내부 배포를 전제로 만들어졌습니다. `--offline`은 Aelix 자신이
내보내는 요청을 차단합니다 — `rg`/`fd` 툴 바이너리 다운로드, 카탈로그 fetch, 인덱스 없는
확장 설치. 다만 설정한 프로바이더 호출까지 막지는 않으므로, 폐쇄망 배포에도 도달 가능한
모델 엔드포인트(또는 자체 호스팅 엔드포인트)는 여전히 필요합니다. 확장 카탈로그는 로컬
사본에서 탐색·설치되고, 신뢰는 로컬 핀으로 관리되며(온라인 폐기 목록 조회 없음),
`register_login_provider`로 확장이 엔터프라이즈 SSO/사번 인증을 추가할 수 있습니다.
정책과 가드레일은 내장 확장으로 강제되므로, 모든 툴 호출과 컨텍스트 변경은 관찰·감사
가능한 훅 이벤트입니다.

서명된 공급망으로 확장을 배포·검증하세요 — 에어갭 설치에서도 살아남는 신뢰입니다
(`--require-signature`로 설치할 때 강제됩니다):

```bash
aelix extension install <path | git-url | package[==version]>   # pip 기반, --offline 지원
aelix extension keygen                                          # 퍼블리셔 Ed25519 키
aelix extension sign <artifact>                                 # detached .aelixsig
aelix extension trust add <key>                                 # 검증 키 신뢰 등록
aelix extension install <target> --require-signature            # fail-closed 서명 게이트
```

의도적으로 게이트하지 **않는** 입력이 하나 있습니다. 작업 디렉터리부터 파일시스템 루트까지
어디에서든 발견된 `AGENTS.md`는 그 프로젝트를 신뢰했는지와 무관하게 시스템 프롬프트로 읽히며,
내용과 절대 경로가 설정한 모델 프로바이더로 전송됩니다. `--no-context-files`로 이 탐색 자체를
끌 수 있습니다. 이렇게 게이트하지 않는 것은 pi의 공개된 정책에서 벗어나지 않고 그대로 따른
결정입니다 — 그것이 실제로 무엇을 뜻하는지, 그리고 이 플래그가 *막지 못하는* 것은
[SECURITY.md](SECURITY.md#scope-what-this-project-is)에 적혀 있습니다.

## 알려진 한계 (베타)

중요한 작업에 Aelix를 붙이기 전에 알아두어야 할 세 가지입니다.

**한 번의 실행에 지출 상한이 없습니다.** 최대 반복 횟수 제한도, 중복 호출 감지도, 누적
토큰·비용 예산도 없습니다 — 모델이 툴을 계속 호출하면 스스로 끝내거나 사용자가 중단할
때까지 비용이 계속 발생합니다([#14](https://github.com/handochan/aelix-ai/issues/14),
[#6](https://github.com/handochan/aelix-ai/issues/6),
[#52](https://github.com/handochan/aelix-ai/issues/52)). 존재하는 안전장치는 실제로
동작하지만, 그중 어느 것도 지출을 제한하지는 않습니다: TUI에서 `Esc`는 진행 중인 턴을
중단하고, `GuardrailExtension`은 권한 검사보다 먼저 파괴적인 명령을 하드 차단하며,
컨텍스트는 넘치기 전에 자동으로 압축되고, `bash`는 기본 600초에 타임아웃됩니다(호출별로
명시한 값은 최대 1시간). 무인 장시간 실행은 지켜보시고, 토큰 단가를 확인한 모델을
쓰세요.

**헤드리스 모드는 변경 툴을 자동 승인합니다.** `--print`, `--mode json`, `--mode rpc`는
승인 대화상자를 그릴 터미널이 없으므로 `write`·`edit`·`bash`가 묻지 않고 실행됩니다 —
바로 그 점이 이들을 스크립트로 쓸 수 있게 만들고, 위의 임베드 예시가 기대는 동작이기도
합니다. 두 가지 보장은 남습니다: `GuardrailExtension`은 헤드리스에서도 자신의 패턴을
하드 차단하고, `--permission-mode plan`은 헤드리스 경로에서도 모든 변경 툴을 차단합니다.
그 외에는 헤드리스 실행이 해당 머신에 대한 전체 쓰기·셸 권한을, 그 머신이 보유한
자격증명과 함께 갖습니다. 컨테이너나 샌드박스, 혹은 버려도 되는 체크아웃에서
실행하세요.

**위임(delegation)은 리눅스 우선이며, 그 지출은 `/cost`에 잡히지 않습니다.** 에이전트
위임(`--agents`, `[features] agents`)은 실제 자식 프로세스를 띄우고, 그 프로세스 배관은
POSIX 기준으로 작성되어 있습니다. 모든 spawn이 `preexec_fn`을 넘기고 종료 경로는
`SIGKILL`을 쓰는데 윈도우에는 둘 다 없으므로, 위임은 윈도우에서 지원되지
않습니다([#110](https://github.com/handochan/aelix-ai/issues/110)). macOS에서는 자식
자체에는 시그널이 정상 전달되지만 자손 탐색이 `/proc`을 읽기 때문에 아무것도 찾지
못합니다 — 위임된 에이전트가 fork한 프로세스는 그보다 오래 살아남을 수 있고, 부모가 강제
종료된 자식도 계속 실행됩니다. 헤드리스 부모(`--print`, `--mode json`, `--mode rpc`)는
spawn 동의 대화상자를 그릴 터미널이 없으므로 스스로 동의합니다: 자식은 여전히 부모의 권한
자세와 툴 권한을 넘을 수 없지만, 사람에게 묻지는 않습니다. 그리고 자식의 토큰은 부모
세션에 들어오지 않은 채 프로바이더에 청구되므로 `/cost`는 부모 몫만 보고합니다 — 자식이
쓴 양은 각 위임의 `[agent … in / … out]` 푸터를 보세요.

## 아키텍처

에이전트는 세 패키지로 구성되며(uv 워크스페이스), `Agent`와 `AgentHarness`가
오케스트레이션합니다:

- **`aelix-ai`** — 프로바이더 중립 메시지, 스트리밍 프리미티브, 툴 정의. 루프 없음, 훅 없음.
- **`aelix-agent-core`** — 에이전트 루프, `Agent`, `AgentHarness`, 타입드 `HookBus`. 확장 의존성 없음.
- **`aelix-coding-agent`** — `ExtensionAPI`, 확장 로더, 내장 `PolicyExtension` / `GuardrailExtension`.

설계 원칙: 작은 커널 + 넓은 확장 표면 · 정책/가드레일은 코어가 아닌 내장 확장 · 감사를
위한 명시적 훅 버스. 전체 근거는 [`docs/`](docs/README.md)에 있습니다.

## 문서

[시작하기](docs/guides/getting-started.md) ·
[프로바이더와 모델](docs/guides/providers-and-models.md) ·
[커스텀 모델](docs/guides/models-json.md) ·
[에이전트 프로필](docs/guides/agent-profiles.md) ·
[확장 작성하기](docs/guides/extension-authoring.md) ·
[프로젝트 신뢰](docs/guides/project-trust.md) ·
[프라이빗 카탈로그](docs/guides/private-catalog.md) ·
[릴리즈](RELEASING.md)

`RELEASING.md`를 제외한 위 가이드는 휠 안에도 복사되어 들어갑니다. 설치된
환경에서는 네트워크도 체크아웃도 없이 그대로 읽을 수 있습니다.

```bash
aelix docs                          # 번들된 가이드 목록
aelix docs project-trust            # 하나 출력
aelix docs --search register_tool   # 전체 가이드 부분 문자열 검색
```

[홈페이지 →](https://handochan.github.io/aelix-ai/) ·
[확장 카탈로그 →](https://handochan.github.io/aelix-marketplace/)

## 소스에서 빌드하기 (기여자용)

Aelix는 환경·의존성 관리에 [uv](https://docs.astral.sh/uv/)를 사용합니다.

```bash
uv sync                  # .venv 생성 및 전체 워크스페이스 패키지 설치
uv run pytest            # 테스트 스위트 실행
uv run aelix --help      # 실제 CLI
```

라이브 프로바이더 자격증명이 필요하면 `.env.example`을 `.env`로 복사하세요(자격증명이
필요 없는 데모 `python -m aelix`는 아무것도 필요하지 않습니다). `.env`는 **프로바이더
자격증명과, 프로바이더 설정 이름 6개**만 읽습니다 — Google Vertex의 project·location,
Cloudflare의 account·gateway id, 그리고 OpenRouter 기본 모델이며, 각각 값 형식까지
검사한 뒤에만 통과합니다. 이 중 5개가 "어떤 모델이 보이는가"를 결정하기 때문에 Vertex와
Cloudflare 두 프로바이더는 `.env`만으로 계속 동작합니다. 나머지 하나인
`OPENROUTER_DEFAULT_MODEL`은 모델을 드러내는 게 아니라 고르는 이름입니다. (가시성을
결정하는 이름은 그 5개에 `GOOGLE_CLOUD_API_KEY`를 더한 것이고, 후자는 자격증명 규칙이
이미 통과시킵니다 — 즉 API 키로 도는 Vertex 설정은 설정 목록이 아예 필요 없습니다.)
aelix는 해당 디렉터리를
신뢰할지 판단하기 전에 이 파일을 읽으므로, 단순히 clone 한 저장소가 `.env`로 aelix 설정을
바꿀 수는 없습니다. 그 외의 값(CA 번들, SDK 옵션, base URL 등)은 셸에 두세요.
[ADR-0203](docs/decisions/0203-dotenv-admission-control.md) 참고.

## 라이선스와 저작자 표시

[Apache-2.0](LICENSE) — 명시적 특허 허여가 포함된 허용적(permissive) 라이선스입니다.

**이름과 로고**는 Apache-2.0 6조가 명시한 대로 코드 라이선스와 별개입니다.
[TRADEMARK.md](TRADEMARK.md)는 6조보다 더 넓게 허용합니다 — Aelix 기반이라거나
호환된다거나 Aelix 확장이라고 설명하는 것, `aelix-<무엇>` 패키지명을 쓰는 것에는
허락이 필요 없습니다.

Aelix의 상당 부분은 [pi](https://github.com/earendil-works/pi)(참조 커밋 `734e08e`,
Copyright © 2025 [Mario Zechner](https://github.com/badlogic), MIT)의 TypeScript→Python
포팅입니다. 번들된 모델 카탈로그는 [models.dev](https://models.dev)(MIT)가 공개한
데이터에서 파생되었습니다. 서드파티 라이선스 전문은 모든 wheel과 sdist에 포함되는
[NOTICE](NOTICE)와 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)에 보존되며, 의존성
인벤토리는 [`sbom/`](sbom/) 아래 CycloneDX SBOM으로 기록됩니다.

Anthropic, OpenAI, Google Gemini, GitHub Copilot, OpenRouter, Cloudflare는 각 소유자의
상표입니다. Aelix는 독립 프로젝트이며, 이름은 연결 가능한 서비스를 식별하는 용도로만
사용됩니다.
