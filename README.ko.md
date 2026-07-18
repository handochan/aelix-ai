# Aelix

**당신만의 코딩 에이전트를, 순수 파이썬으로.**

직접 호스팅하고, 모든 코드를 감사하고, 팀이 이미 쓰는 언어로 확장하세요 — 이미 지불하고
있는 모델 예산 위에서.

[English README →](README.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)

Aelix는 순수 파이썬으로 작성된 오픈소스 코딩 에이전트입니다. 실행되는 모든 코드를 직접 읽을
수 있고, 에이전트 전체를 사내 경계 안에 두고 운영하며, 평범한 파이썬 함수로 확장합니다.
확장은 in-process로 동작하므로 pandas·내부 SDK·웨어하우스 클라이언트 같은 기존 스택을
그대로 import해 쓸 수 있습니다 — 데이터·ML 팀이 가장 먼저 찾는 이유입니다. 그리고 어떤
정보도 외부로 전송하지 않습니다.

---

## 왜 aelix인가

- 🐍 **확장은 그냥 파이썬입니다.** 툴은 평범한 함수 하나입니다 — 플러그인 언어도, 프로세스
  밖 브리지도 없습니다. 터미널·노트북·파이프라인·CI 어디서든 에이전트를 구동하세요.
  [예제 보기 ↓](#확장은-그냥-파이썬입니다--데이터-스택을-in-process로)
- 💳 **이미 보유한 예산으로 돌아갑니다.** Anthropic, OpenAI, Gemini/Vertex, OpenRouter,
  Cloudflare, 그리고 GitHub Copilot — 이미 로그인해 쓰고 있는 individual/Business/Enterprise
  좌석 포함(사용은 귀사의 GitHub 계약 조건을 따릅니다) — 네이티브 어댑터를 제공합니다. 싼
  작업과 어려운 추론을 한 세션에서 다른 모델로 라우팅하세요. 종량제 ACU도, 새 벤더 계약도
  없습니다.
- 🔏 **서명된 공급망.** 확장은 Ed25519 서명과 SHA-256 핀으로 검증됩니다(`extension keygen |
  sign | trust`, fail-closed `--require-signature`). 오프라인 카탈로그에서 설치할 수
  있습니다. 코딩 에이전트에서 보기 드문 기능이지만, aelix에서는 기본입니다.
- 🔍 **감사 가능한 자체 호스팅.** 완전한 오픈소스, 텔레메트리 없음, 폐쇄망을 위한
  `--offline` 모드. 신뢰는 직접 읽을 수 있는 코드에서 나옵니다 — *"내가 만들지 않은
  에이전트를 왜 돌리는가?"* 에 대한 답입니다.
- 🧩 **코어까지 확장 가능.** 정책·권한·가드레일조차 교체 가능한 내장 확장으로 제공되는 작은
  커널과, 하나의 넓은 `ExtensionAPI` — 툴, 슬래시 명령, 프로바이더, 메시지 렌더러, 테마,
  그리고 자체 `/login` 플로우(SSO/사번 인증)까지 — 를 제공합니다. 재시작 없는 라이브
  핫리로드를 지원합니다.
- ⚙️ **스크립트·헤드리스 구동.** `--print`, 라인 단위 `--mode json`, `--mode rpc` JSONL
  프로토콜로 파이프라인·CI·평가 루프에 그대로 임베드할 수 있습니다 — 결정적이고 기계가
  읽을 수 있는 출력.

## 설치

베타 기간에는 체크섬 검증 인스톨러를 통해 GitHub Releases에서 설치합니다. 필요하면
[uv](https://docs.astral.sh/uv/)를 자동으로 설치하고, 모든 wheel을 릴리즈의 `SHA256SUMS`
매니페스트와 대조한 뒤(불일치 시 즉시 중단), 전역 `aelix` 명령을 설치합니다:

```bash
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

`AELIX_VERSION=v0.1.0-beta.1`로 릴리즈를 고정하고(베타 기간 권장), `AELIX_EXTRAS`로
extras를 선택하세요 — 기본값은 `tui`, `tui,images`는 터미널 인라인 이미지 렌더링을
추가하고, 비워두면(`AELIX_EXTRAS=`) TUI 없는 헤드리스 CLI(print/json/rpc)만 설치됩니다.
PyPI 게시 이후에는 `uv tool install 'aelix[tui]'` — 또는 `pipx`/`pip` — 를 그대로 쓸 수
있게 됩니다.

```bash
aelix                                            # 인터랙티브 에이전트 (TUI)
aelix --model openai/gpt-4o-mini "summarise this repo"
aelix --print "what files changed?"              # 일회성 헤드리스 실행
aelix --offline                                  # 에어갭 모드
aelix --help
```

`aelix`에는 프로바이더 자격증명이 필요합니다 — `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`를 설정하거나, `aelix`를 실행한 뒤 TUI 안에서 `/login`을 입력하거나
(Copilot/구독 OAuth), `--api-key`를 넘기거나, `~/.aelix/agent/models.json`을 구성하세요.
자세한 내용은 [프로바이더 가이드](docs/guides/providers-and-models.md)를 참고하세요.

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
| GitHub Copilot (individual / Business / Enterprise) | ✅ 지원 |
| OpenAI Responses API | 🧪 실험적 |
| Google Gemini / Vertex | 🧪 실험적 |
| Cloudflare Workers AI | 🧪 실험적 |

## 확장은 그냥 파이썬입니다 — 데이터 스택을 in-process로

aelix 확장은 `setup(aelix)` 함수 하나입니다. 별도의 플러그인 언어도, 프로세스 밖 브리지도
없으므로 툴이 기존 스택을 그대로 import해 결과를 모델에 바로 돌려줄 수 있습니다 — aelix가
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

전체 API 표면은 [확장 작성하기](docs/guides/extension-authoring.md)를 참고하세요.

## 신뢰와 자체 호스팅

Aelix는 폐쇄망과 고객사 내부 배포를 전제로 만들어졌습니다. `--offline`은 에어갭 모드를
켭니다(툴 바이너리 다운로드, 네트워크 확장 설치 차단). 확장 카탈로그는 외부 통신 없이
탐색·설치되고, 신뢰는 로컬 핀으로 관리되며(온라인 폐기 목록 조회 없음),
`register_login_provider`로 확장이 엔터프라이즈 SSO/사번 인증을 추가할 수 있습니다.
정책과 가드레일은 내장 확장으로 강제되므로, 모든 툴 호출과 컨텍스트 변경은 관찰·감사
가능한 훅 이벤트입니다.

서명된 공급망으로 확장을 배포·검증하세요 — 에어갭 설치에서도 살아남는 신뢰입니다:

```bash
aelix extension install <path | git-url | package[==version]>   # pip 기반, --offline 지원
aelix extension keygen                                          # 퍼블리셔 Ed25519 키
aelix extension sign <artifact>                                 # detached .aelixsig
aelix extension trust add <key>                                 # 검증 키 신뢰 등록
aelix extension install <target> --require-signature            # fail-closed 서명 게이트
```

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
[확장 작성하기](docs/guides/extension-authoring.md) ·
[릴리즈](RELEASING.md)

## 소스에서 빌드하기 (기여자용)

Aelix는 환경·의존성 관리에 [uv](https://docs.astral.sh/uv/)를 사용합니다.

```bash
uv sync                  # .venv 생성 및 전체 워크스페이스 패키지 설치
uv run pytest            # 테스트 스위트 실행
uv run aelix --help      # 실제 CLI
```

라이브 프로바이더 자격증명이 필요하면 `.env.example`을 `.env`로 복사하세요(자격증명이
필요 없는 데모 `python -m aelix`는 아무것도 필요하지 않습니다).

## 라이선스와 저작자 표시

[Apache-2.0](LICENSE) — 명시적 특허 허여가 포함된 허용적(permissive) 라이선스입니다.

Aelix의 상당 부분은 [pi](https://github.com/earendil-works/pi)(참조 커밋 `734e08e`,
Copyright © 2025 [Mario Zechner](https://github.com/badlogic), MIT)의 TypeScript→Python
포팅입니다. 번들된 모델 카탈로그는 [models.dev](https://models.dev)(MIT)가 공개한
데이터에서 파생되었습니다. 서드파티 라이선스 전문은 모든 wheel과 sdist에 포함되는
[NOTICE](NOTICE)와 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)에 보존되며, 의존성
인벤토리는 [`sbom/`](sbom/) 아래 CycloneDX SBOM으로 기록됩니다.

Anthropic, OpenAI, Google Gemini, GitHub Copilot, OpenRouter, Cloudflare는 각 소유자의
상표입니다. Aelix는 독립 프로젝트이며, 이름은 연결 가능한 서비스를 식별하는 용도로만
사용됩니다.
