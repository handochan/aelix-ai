<p align="center">
  <img src="https://raw.githubusercontent.com/handochan/aelix-ai/main/docs/assets/brand/lockup-stacked.png" width="360" alt="Aelix — A×X 마크와 Aelix 워드마크">
</p>

<p align="center">
  <strong>당신만의 에이전트 세계를, 파이썬 생태계 위에.</strong>
</p>

<p align="center">
  <a href="README.md">English README →</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
</p>

Aelix는 작은 코어입니다. 플러그인과 확장이 생태계이고, 확장은 그냥 파이썬 함수입니다 —
이미 쓰고 있는 스택이 그대로 에이전트의 도구가 됩니다. 직접 호스팅하고, 모든 코드를
감사하고, 이미 지불하고 있는 모델 예산 위에서 돌립니다.

<p align="center">
  <img src="docs/assets/demo.gif" width="100%" alt="Aelix 데모 — 에이전트가 DuckDB 확장을 my_ext.py에 직접 작성하고, /reload로 재시작 없이 핫리로드한 뒤, 바로 다음 프롬프트에서 in-process로 실행">
</p>
<p align="center"><em>에이전트가 스스로를 확장합니다: <code>duckdb_query</code> 툴을 <code>my_ext.py</code>에 직접 작성하고, <code>/reload</code>로 재시작 없이 핫리로드한 뒤, 바로 다음 프롬프트에서 in-process로 실행합니다. 대기 구간은 잘라냈고, 2배속으로 재생됩니다.</em></p>

---

## Aelix란

순수 파이썬으로 만든 에이전트 런타임이자 확장 플랫폼입니다. 오늘은 그 위의 터미널 코딩
에이전트로 먼저 제공됩니다 — 첫 워크로드일 뿐, 경계가 아닙니다. 실행되는 모든 코드를 직접
읽을 수 있고, 에이전트 전체를 사내 경계 안에 두고 운영하며, 평범한 파이썬 함수로
확장합니다. DuckDB든 내부 SDK든 웨어하우스 클라이언트든 in-process로 그대로 import됩니다.

사용자에 관한 어떤 정보도 외부로 전송하지 않습니다 — 텔레메트리는 일절 없습니다. Aelix가
스스로 내보내는 요청이 몇 가지 있고, 그게 전부입니다: 하루 한 번의 릴리즈 확인(대화형 세션에서만
— 헤드리스 실행은 확인하지 않고, `/settings`의 **Check for updates**를 끄면 다음 실행부터
확인하지 않습니다), 최초 사용 시의 `ripgrep`/`fd` 다운로드, 그리고 확장 카탈로그 fetch.
`--offline`이 이들을 끕니다.

## 설치

Aelix는 체크섬을 검증하는 설치 스크립트로 GitHub Releases에서 설치됩니다. 필요하면
[uv](https://docs.astral.sh/uv/)를 부트스트랩하고, 모든 Aelix 휠을 릴리즈의
`SHA256SUMS`와 대조한 뒤, 그 매니페스트가 지정한 버전으로 전역 `aelix` 명령을
설치합니다.

```bash
# macOS, Linux
curl -fsSL https://raw.githubusercontent.com/handochan/aelix-ai/main/install.sh | sh
```

```powershell
# Windows — EXPERIMENTAL, v0.1.0-beta.2 이상 필요 (PowerShell 5.1 이상)
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/handochan/aelix-ai/main/install.ps1 | iex"
```

Windows 수정이 전부 들어간 릴리즈가 `v0.1.0-beta.2`이고, 설치 스크립트는 가장 새
릴리즈를 가져옵니다 — 그보다 오래된 릴리즈에서는 위 줄이 이 문서의 내용이 하나도
통하지 않는 빌드를 설치합니다. 범위와 근거는 [플랫폼 지원](#플랫폼-지원)에 있습니다.

둘 다 같은 환경 변수를 읽습니다. `AELIX_VERSION`은 릴리즈 태그를 고정하고
(`vX.Y.Z-beta.N`), `AELIX_EXTRAS`는 extras를 고르며(기본값 `tui`, 빈 값이면 헤드리스
CLI만 — POSIX에서만 됩니다), `AELIX_PYTHON`은 인터프리터를 요청하고(기본값
`>=3.11,<3.14`, 고정된 OpenAI SDK가 견디는 범위 — 두면 `uv tool install`이 그
머신에서 가장 새 인터프리터를 집고, 3.14에서는 OpenAI Responses 경로가 턴 도중에
죽습니다),
`GITHUB_TOKEN`은 익명 GitHub API 요청 한도를 올립니다.
다만 파이프 *건너편* 셸까지 값이 닿아야 해서, 가장 먼저 떠오르는 표기가 양쪽 다
틀립니다 — `VAR=x curl … | sh`는 `curl`에만 설정하고, PowerShell 프롬프트에서
`powershell -c "$env:VAR=…"`는 자식이 보기 전에 바깥 셸이 전개해 버립니다(pwsh 7.6에서
측정). 각 플랫폼에서 실제로 되는 형태는 [시작하기
가이드](docs/guides/getting-started.md#windows)에 있습니다. 업그레이드는 같은 줄을 다시
실행하면 되고, `PATH`에 잡히지 않을 때는 두 설치 스크립트 모두 안내만 하는 대신
`uv tool update-shell`을 직접 실행합니다. 제거는 `uv tool uninstall aelix`입니다.
**업그레이드는 그 줄로 하세요. `uv tool install aelix@latest`로 하지 마세요** — uv가
직접 그 명령을 안내하지만, 그건 그 머신에서 가장 새 Python으로 환경을 다시 만들고
오늘 기준 그건 3.14, 즉 턴 도중 크래시입니다(#262). 실측: 올바르게 깔린 3.13 설치를
덮어씁니다.

> **`0.1.0b2` 전까지 PyPI에는 플레이스홀더가 올라가 있습니다.** 지금은 네 이름 모두
> 메타데이터만 있는 `0.0.0a0` 선점본이라, `pip install aelix`는 **exit 0으로 끝나면서
> 실행 가능한 것을 아무것도 설치하지 않고**(`aelix` 명령도 없고 `import aelix`는
> `ModuleNotFoundError`), `uv tool install aelix@latest`는 **기존 설치를 지웁니다.**
> `0.1.0b2`부터 그 함정이 닫힙니다: 인덱스의 후보가 전부 pre-release가 되고, 그럴 때
> pip과 uv는 둘 다 그중 가장 새것을 고르므로 `uv tool install 'aelix[tui]'`와
> `pipx install aelix`가 진짜를 받아옵니다
> ([ADR-0240](docs/decisions/0240-a-pre-release-tag-publishes-to-pypi-too.md)).
> 그래도 권장 경로는 위 설치 스크립트입니다 — 릴리즈의 `SHA256SUMS`를 검사하는 것은
> 그것뿐이고, 그렇게 설치한 것은 `uv tool upgrade`가 아니라 스크립트를 다시 실행해서
> 업그레이드합니다.

## 플랫폼 지원

macOS, Linux, Windows. Windows는 `v0.1.0-beta.2`에서 쓸 수 있게 됐고, 셋 중 근거가
가장 얇습니다. CI가 `ubuntu-latest`와 `windows-latest`에서 Python 3.11/3.12로 전체
스위트를 돌리고 `install.ps1`을 pwsh와 Windows PowerShell 5.1 양쪽에서 end to end로
실행하며, 2026-09-09에 사람 한 명이 이 후보를 Windows 머신 한 대에서 직접 돌렸습니다 —
`bash` 호출이 낸 한국어 출력이 한국어로 나왔고, 모델은 자기가 PowerShell 위에 있다고
바르게 보고하며 `&&` 대신 PowerShell 문법을 썼고, TUI는 Windows 콘솔에서 제대로
그려졌습니다. 그게 전부입니다 — 사람 하나, 머신 하나, 로케일 하나, 그리고 초록 레그
하나. 오래 돌려 본 적도, 두 번째 머신도, 세 번째 로케일도 없고, 이번 릴리즈의 Windows
수정 중 셋은 소스와 CI만으로 논증한 것입니다.

Windows 콘솔을 직접 여는 프로그램 — git 자격 증명 프롬프트,
`Read-Host -AsSecureString`, `Get-Credential` — 은 거기서 여전히 프롬프트를 띄우고
명령의 타임아웃을 통째로 태울 수 있습니다. 이번 릴리즈가 바꾼 것 중 거기에 닿는 것은
없고, 그 레그의 어떤 테스트도 거기까지 가지 못합니다.

어느 수정이 어떻게 측정됐는지, 그리고 아직 열려 있는 것:
[시작하기 → Windows](docs/guides/getting-started.md#windows)와
[`SLICE-STATUS.md`](SLICE-STATUS.md). 포팅 현황은
[#110](https://github.com/handochan/aelix-ai/issues/110)에서 추적합니다.

## 빠른 시작

```bash
aelix                                            # 대화형 에이전트 (TUI)
aelix --print "이 저장소에서 뭐가 바뀌었지?"        # 원샷, 헤드리스
aelix --model anthropic/claude-haiku-4-5 "이 저장소를 요약해줘"
aelix status                                     # 신뢰·확장·TLS — 세션을 시작하지 않음
aelix docs                                       # 휠에 함께 실린 가이드
```

프로바이더 자격증명이 필요합니다: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`OPENROUTER_API_KEY`를 설정하거나, TUI 안에서 `/login`(Copilot·구독 OAuth)을 실행하거나,
`--api-key`를 넘기거나, `~/.aelix/agent/models.json`을 구성하세요.
[프로바이더 가이드](docs/guides/providers-and-models.md)를 참고하세요.

`grep`이나 `find`를 처음 쓸 때 Aelix는 `ripgrep`과 `fd`를 `~/.aelix/agent/bin`에
내려받습니다(둘 다 `.gitignore`를 존중하게 하려고). 런타임에 받아오는 바이너리는 이 둘뿐이고,
`--offline`이 이를 건너뛰며, `PATH`에 이미 있으면 그쪽을 우선합니다.
`@` 파일 메뉴도 이 `fd`를 찾으면 그대로 씁니다. 그래서 git이 무시하는 파일은 목록에서
빠집니다 — 디렉터리를 직접 입력하면 여전히 닿고(`@target/`), 다 보고 싶으면 `/settings`의
**Gitignore in @ menu**를 끄면 됩니다. `fd`가 없으면 무시 규칙을 전혀 적용하지 않는
디렉터리 순회로 돌아갑니다. 두 열거자 모두 20 000개에서 멈추고, 무시 규칙이 꺼진 상태에서는
큰 빌드 트리 하나가 실제 디렉터리에 닿기도 전에 그 예산을 다 써 버릴 수 있습니다.

## 왜 Aelix인가

- 🐍 **확장은 그냥 파이썬입니다.** 툴은 평범한 함수 하나입니다 — 플러그인 언어도, 프로세스
  밖 브리지도 없습니다. 같은 `ExtensionAPI`로 툴·슬래시 명령·프로바이더·렌더러·테마, 그리고
  자체 `/login` 플로우까지 등록하고, 모든 확장이 재시작 없이 핫리로드됩니다. 정책과
  가드레일조차 교체 가능한 내장 확장입니다.
- 💳 **이미 보유한 예산으로 돌아갑니다.** Anthropic, OpenAI, OpenRouter, Gemini/Vertex,
  Cloudflare, 그리고 이미 로그인해 쓰는 GitHub Copilot 좌석. 싼 작업과 어려운 추론을 한
  세션에서 다른 모델로 라우팅하세요. 종량제 크레딧도, 새 벤더도 없습니다.
- 🔍 **감사 가능한 자체 호스팅.** 완전한 오픈소스, 텔레메트리 없음, 폐쇄망을 전제로 한 설계.
  `--offline`은 Aelix가 스스로 내보내는 요청을 끕니다. 신뢰는 직접 읽을 수 있는
  코드에서 나옵니다 — *"내가 만들지 않은 에이전트를 왜 돌리는가?"* 에 대한 답입니다.
- ⚙️ **스크립트·헤드리스 구동.** `--print`, 라인 단위 `--mode json`, `--mode rpc` JSONL
  프로토콜로 파이프라인·CI·평가 루프에 그대로 임베드됩니다.

## 확장은 그냥 파이썬입니다 — 데이터 스택을 in-process로 질의

확장은 `setup(aelix)` 함수 하나입니다. 플러그인 언어도 브리지도 없으니, 툴이 기존 스택을
그대로 import해서 결과를 모델에 바로 넘깁니다:

```python
# my_ext.py  —  로드:  aelix -e ./my_ext.py
from typing import Any

import duckdb                              # 당신의 의존성, in-process로 import

from aelix_coding_agent.extensions.api import ExtensionAPI
from aelix_agent_core.types import AgentTool
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.messages import TextContent


async def _query(args: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
    # DuckDB는 Parquet/CSV/JSON을 그 자리에서 읽습니다 — 적재 단계도, 복사도 없습니다.
    rel = duckdb.sql(args["sql"]).limit(args.get("limit", 20))
    return ToolResult(content=[TextContent(text=str(rel))])


def setup(aelix: ExtensionAPI) -> None:
    aelix.register_tool(AgentTool(
        name="duckdb_query",
        description="Run DuckDB SQL straight against Parquet/CSV/JSON files. No load step.",
        parameters={
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT … FROM 'data/*.parquet'"},
                "limit": {"type": "integer", "description": "Max rows returned (default 20)."},
            },
            "required": ["sql"],
        },
        execute=_query,
    ))
```

**파이썬이 도는 곳이면 어디든** — 노트북, Airflow/Prefect/Dagster 태스크, CI 잡:

```bash
aelix --print "data/orders.parquet에서 churn_score가 비어 있는 채널이 어디야?"
aelix --mode json "평가 스위트를 돌리고 실패를 요약해줘"   # 라인 단위 이벤트
```

전체 확장 표면은 [확장 작성 가이드](docs/guides/extension-authoring.md)를, 공식 카탈로그는
[Aelix 마켓플레이스](https://handochan.github.io/aelix-marketplace/)를 보세요. 베타 동안은
비어 있고, 등록 제안을 받고 있습니다.

## 프로바이더

어댑터는 손으로 썼고 벤더가 아니라 **와이어 프로토콜** 단위로 나뉩니다 — litellm도 범용 래퍼
레이어도 없어서, 프로바이더별 동작(Anthropic thinking 블록 리플레이, 모델별 `/responses` vs
`/chat/completions` 라우팅, Copilot 엔터프라이즈 호스트 해석)이 뭉개지지 않고 그대로
보존됩니다. 어댑터는 여섯 개이고 카탈로그 프로바이더들이 그 위에 올라탑니다 — OpenRouter와
Cloudflare Workers AI는 둘 다 `openai-completions`를, 번들된 OpenAI 모델은 전부
`openai-responses`를 씁니다.

| 프로바이더                              | 사용 어댑터            | 상태            |
| -------------------------------------- | --------------------- | -------------- |
| Anthropic                              | `anthropic-messages`  | ✅ 지원         |
| OpenRouter                             | `openai-completions`  | ✅ 지원         |
| GitHub Copilot (individual / Business) | 혼합                   | ✅ 지원         |
| OpenAI                                 | `openai-responses`    | 🧪 실험적       |
| GitHub Copilot (Enterprise)            | 혼합                   | 🧪 라이브 미검증 |
| Google Gemini / Vertex                 | `google-*`            | 🧪 실험적       |
| Cloudflare Workers AI                  | `openai-completions`  | 🧪 실험적       |

번들된 OpenAI 모델은 전부 `openai-responses`로 라우팅되므로, OpenAI 모델을 고르면 실험적
경로에 올라탑니다. `openai-completions` 어댑터의 실제 트래픽은 OpenRouter·Cloudflare와 기타
OpenAI 호환 호스트입니다.

Copilot Enterprise는 호스트 해석·도메인 입력·영속화까지 구현되어 있고 단위 테스트도 있지만,
실제 Enterprise 좌석에 대고 돌려본 적은 없습니다.

카탈로그에는 있지만 **이 빌드에 어댑터가 없는** 세 프로바이더(`amazon-bedrock`,
`azure-openai-responses`, `mistral`)는 `/model` 피커가 가리고, 디스패치가 지원 프로토콜을
알려주며 거부합니다 — 다만 해당 API 키를 설정하면 `--list-models`에는 여전히 나옵니다.
[프로바이더 가이드](docs/guides/providers-and-models.md)를 참고하세요.

## 신뢰와 자체 호스팅

폐쇄망과 고객사 설치를 전제로 만들었습니다. `--offline`은 Aelix가 스스로 내보내는 요청을
끕니다 — `rg`/`fd` 다운로드, 카탈로그 fetch, 인덱스 없는 확장 설치, 업데이트 확인. 다만
설정한 프로바이더 호출은 건드리지 않으므로, 폐쇄망이라도 도달 가능하거나 자체 호스팅한 모델
엔드포인트는 필요합니다. 알아둘 구멍이 둘 있습니다: `extension` 하위명령 **앞**에 붙인
`--offline`은 인식되지 않고(뒤에 붙이세요), `git+https://` 설치 대상은 그래도 진행됩니다. 정책과 가드레일이 내장 확장으로 집행되므로, 모든 툴 호출과 컨텍스트
변경이 감사 가능한 훅 이벤트로 남습니다.

빌드된 확장 아티팩트는 에어갭 설치에서도 살아남는 서명 공급망을 갖습니다 — `aelix extension
keygen | sign | trust add`, 그리고 `install --require-signature`는 fail-closed입니다. 경로나
인덱스로 설치하는 아티팩트가 대상이고, `git+` 대상과 editable 디렉터리는 이 플래그 아래에서
검증되는 게 아니라 **거부**됩니다. 그리고 **기본값도 아닙니다**: 아직 퍼스트파티 키가
프로비저닝되지 않았으므로, 이 플래그 없이는 서명이 없어도 최초 사용 시 그대로 통과합니다.

의도적으로 **게이트하지 않은** 입력이 하나 있습니다: 작업 디렉터리와 파일시스템 루트 사이에
있는 `AGENTS.md`는 프로젝트를 신뢰했는지와 무관하게 시스템 프롬프트로 읽히고, 그 내용은 설정한
프로바이더로 전송됩니다. `--no-context-files`로 끕니다. [SECURITY.md](SECURITY.md)와
[프로젝트 신뢰 가이드](docs/guides/project-trust.md)를 참고하세요.

## 알려진 한계 (베타)

중요한 일에 Aelix를 붙이기 전에 알아둘 것이 여섯 가지입니다.

**한 번의 실행에 비용 상한이 없습니다.** 반복 횟수 제한도, 중복 호출 감지도, 누적
토큰·비용 예산도 없습니다 — 툴을 계속 부르는 모델은 끝나거나 멈출 때까지 계속 돈을
씁니다. `Esc`, `bash`의 600초 *기본* 타임아웃(호출이 값을 명시하면 최대 1시간까지
허용됩니다), 자동 컴팩션은 실재하는 안전장치지만 그중 어느 것도 비용을 묶지 않습니다
([#14](https://github.com/handochan/aelix-ai/issues/14),
[#6](https://github.com/handochan/aelix-ai/issues/6),
[#52](https://github.com/handochan/aelix-ai/issues/52)).

**헤드리스 모드는 변경 툴을 자동 승인하고, 두 안전망은 내장 툴만 알아봅니다.**
`--print`, `--mode json`, `--mode rpc`에는 승인 대화상자를 그릴 터미널이 없어서
`write`·`edit`·`bash`가 묻지 않고 실행됩니다. 그리고 두 안전망 모두 고정된 내장 툴
이름 목록으로 판단하므로, MCP 서버·스킬·서드파티 확장이 제공한 툴은
`GuardrailExtension`에도 `--permission-mode plan` 차단에도 걸리지 않습니다. 헤드리스
실행에는 컨테이너나 버려도 되는 체크아웃을 주세요
([#188](https://github.com/handochan/aelix-ai/issues/188)).

**세션 하나에 터미널 하나.** 세션 JSONL은 append-only이고 파일 잠금이 없어서, 같은
세션을 두 번 열면 한쪽 터미널의 작업이 어떤 `--resume`도 따라가지 않는 가지가 됩니다 —
디스크상으로는 멀쩡하지만 트랜스크립트에서는 사라집니다
([#137](https://github.com/handochan/aelix-ai/issues/137)).

**트랜스크립트는 전부, 영구히, 원문 그대로 남습니다.** 모든 프롬프트·툴 인자·툴 결과가
그대로 기록되며 살균 단계가 없습니다. macOS와 Linux에서는 소유자 전용(`0700` 안의
`0600`)이고, Windows에는 Aelix가 걸 수 있는 모드 비트 자체가 없습니다 — 어느 쪽이든
노출 대상은 홈 디렉터리를 복사해 가는 것들, 즉 백업·동기화 클라이언트·지원 번들입니다
([#138](https://github.com/handochan/aelix-ai/issues/138)).

**성공한 `bash` 호출이 자기 출력의 꼬리를 조용히 잃을 수 있습니다.** 명령이 종료하는
구간에서 읽기 스레드가 굶으면 출력 끝에서 최대 64 KiB가량이 버려질 수 있습니다 —
`exit_code`는 0이고 무엇이 사라졌다는 표시도 없으며, 하필 그 꼬리가 모델에게 보여 주는
부분입니다. 평범한 조건의 약 3,300회 반복에서는 재현되지 않았습니다
([#260](https://github.com/handochan/aelix-ai/issues/260)).

**위임의 비용은 `/cost`에 안 잡히고, 정리(cleanup)는 OS마다 같지 않습니다.** 자식의
토큰은 부모 세션에 들어오지 않으므로 자식이 쓴 양은 각 위임의 자체 푸터를 읽으세요.
헤드리스 부모는 스폰 동의를 스스로 처리합니다. Aelix 자신이 죽었을 때 자식을 끝내는 것은
Linux에서는 `PR_SET_PDEATHSIG`, Windows에서는 job object의 `kill_on_close`이고, macOS는
둘 다 없어서, 자기 세션을 만든 손자는 거기서 유일한 봉쇄 수단인 프로세스 그룹을 빠져나갑니다
([#110](https://github.com/handochan/aelix-ai/issues/110)).

## 아키텍처

세 패키지(uv 워크스페이스)를 `Agent`와 `AgentHarness`가 오케스트레이션합니다:

- **`aelix-ai`** — 프로바이더 비의존 메시지·스트리밍 프리미티브·툴 정의. 루프도 훅도 없음.
- **`aelix-agent-core`** — 에이전트 루프, `Agent`, `AgentHarness`, 타입이 붙은 `HookBus`. 확장 의존 없음.
- **`aelix-coding-agent`** — `ExtensionAPI`, 확장 로더, 내장 `PolicyExtension` / `GuardrailExtension`.

작은 커널과 넓은 확장 표면, 코어가 아니라 내장 확장으로 두는 정책·가드레일, 감사를 위한 명시적
훅 버스. 전체 근거는 [`docs/`](docs/README.md)에 있습니다.

## 문서

[시작하기](docs/guides/getting-started.md) ·
[프로바이더와 모델](docs/guides/providers-and-models.md) ·
[커스텀 모델](docs/guides/models-json.md) ·
[에이전트 프로필](docs/guides/agent-profiles.md) ·
[확장 작성](docs/guides/extension-authoring.md) ·
[프로젝트 신뢰](docs/guides/project-trust.md) ·
[프라이빗 카탈로그](docs/guides/private-catalog.md) ·
[릴리즈](RELEASING.md)

`RELEASING.md`를 뺀 모든 가이드가 휠 안에 함께 실리므로, 설치된 머신은 네트워크도 체크아웃도
없이 읽습니다 — `aelix docs`, `aelix docs project-trust`, `aelix docs --search register_tool`.

[홈페이지 →](https://handochan.github.io/aelix-ai/) ·
[확장 카탈로그 →](https://handochan.github.io/aelix-marketplace/)

## 소스에서 빌드하기

```bash
uv sync                  # .venv 생성 + 워크스페이스 패키지 전체 설치
uv run pytest            # 테스트 스위트
uv run aelix --help      # 진짜 CLI
```

라이브 프로바이더 자격증명은 `.env.example`을 `.env`로 복사해 넣으세요. `.env`는 프로바이더
자격증명과 짧은 프로바이더 설정값 목록만 받아들이고 그 외에는 무시하며, Aelix가 디렉터리를
신뢰하는지 알기 전에 읽습니다. 이 허용 목록은 좁지만 비어 있지는 않습니다: 클론한 저장소의
`.env`가 **세션이 쓸 API 키를 넣을 수 있고**, 그러면 프롬프트가 공격자 계정으로 갑니다. 다만
프로그램을 실행하거나, 자격증명 저장소를 옮기거나, 자기 게이트를 넓히거나, 프로바이더를 진짜
호스트에서 떼어내지는 못합니다
([ADR-0203](docs/decisions/0203-dotenv-admission-control.md)). CA 번들·SDK 노브·base URL은 셸에
두세요.

## 라이선스와 저작자 표시

[Apache-2.0](LICENSE), 명시적 특허 허여 포함. **이름과 로고**는 코드 라이선스와 별개이며,
[TRADEMARK.md](TRADEMARK.md)는 Apache-2.0 §6보다 더 넓게 허용합니다: 당신의 작업을 Aelix 기반·
호환·확장이라고 설명하는 데 허가가 필요 없고, 패키지 이름을 `aelix-<something>`으로 짓는 것도
마찬가지입니다.

Aelix의 상당 부분은 [pi](https://github.com/earendil-works/pi)(기준 커밋 `734e08e`, Copyright
© 2025 [Mario Zechner](https://github.com/badlogic), MIT)를 TypeScript에서 파이썬으로 옮긴
것입니다. 번들 모델 카탈로그는 [models.dev](https://models.dev)(MIT)의 데이터에서 파생됩니다.
서드파티 라이선스 전문은 모든 휠과 sdist에 함께 실립니다([NOTICE](NOTICE),
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)). 의존성 인벤토리는 [`sbom/`](sbom/)의
CycloneDX SBOM입니다.

Anthropic, OpenAI, Google Gemini, GitHub Copilot, OpenRouter, Cloudflare는 각 소유자의
상표입니다. Aelix는 독립 프로젝트이며, 이름은 연결 가능한 서비스를 식별하기 위해서만
사용합니다.
