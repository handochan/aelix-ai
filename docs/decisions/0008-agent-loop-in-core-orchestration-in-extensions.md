# 0008. Agent Loop in Core, Orchestration in Extensions

Status: Accepted — **Decision ¶2의 "runtime core에는 multi-agent 개념을 두지
않습니다" 문장은 ADR-0196 (P1 agent-profile identity)에 의해 AMEND 되었습니다**
(아래 `## Amendment (2026-07-19)` 참조). L1/L2 분리 자체는 **그대로
유효**합니다 — amendment는 "single-agent profile **identity**"가 어느 밴드에
속하는지를 명시할 뿐, L2 orchestration을 core로 옮기지 않습니다.
**추가로 ADR-0197 (P2 subagent-runtime seam)이 아래 `## Amendment (2026-07-26)`
로 리뷰 게이트 인용문의 두 조항(`caps` 범위 · consent policy)을 조정합니다.**
원 Decision과 2026-07-19 amendment는 둘 다 **그대로 유효**합니다.

## Context

`02-initial-requirements.md`의 Open Question Q4는 "Agent orchestration은
runtime core에 둘 것인가, extension layer로 둘 것인가"를 미결정 항목으로
두었습니다.

orchestration이라는 단어는 두 수준을 가리킬 수 있습니다.

- L1 단일 agent loop: LLM 호출, tool call, 결과 처리, 반복
- L2 multi-agent orchestration: subagent delegation, planner, parallel
  execution, shared task coordination

Pi에서 두 수준은 다음과 같이 분리되어 있습니다.

- `pi-agent-core`: agent runtime with tool calling and state management (L1)
- `pi-subagents` 같은 extension: subagent delegation, parallel execution (L2)

ADR-0002는 "작은 kernel"을 원칙으로, kernel을 extension invocation,
ExecutionContext, registry, lifecycle hook으로 제한했습니다. ADR-0003은 pi
agent를 primary reference로 두었습니다. ADR-0004는 policy/guardrail을
built-in extension으로 두었습니다.

## Decision

Aelix는 orchestration을 다음과 같이 두 수준으로 분리합니다.

- **L1 단일 agent loop는 runtime core 책임**입니다. invocation, tool call
  처리, 결과 dispatch, lifecycle hook 발행이 여기 포함됩니다. 이는 ADR-0002
  kernel 범위에 자연스럽게 포함됩니다.
- **L2 multi-agent / planner / subagent orchestration은 extension layer**의
  책임입니다. runtime core에는 multi-agent 개념을 두지 않습니다.

L2 orchestration extension은 lifecycle hook과 invocation API를 사용해 다른
extension을 호출하거나 병렬 실행을 구성할 수 있습니다.

## Consequences

- runtime core는 단일 agent loop까지만 책임지고, multi-agent coordination은
  protocol 안에 들어오지 않습니다.
- subagent helper, planner, parallel execution 같은 기능은 모두 extension으로
  제공됩니다. 일부는 기본 번들에 포함될 수 있지만 core가 아닙니다.
- pi agent의 `pi-agent-core` + `pi-subagents` 분리 모델과 동형입니다.
- multi-agent algorithm은 protocol 변경 없이 새 extension으로 교체/추가될
  수 있습니다. ADR-0002 "Stable Contracts, Replaceable Internals"와 정합.
- `02-initial-requirements.md` Runtime 절의 "장기적으로 orchestration,
  multi-agent coordination 포함"이라는 표현은 "extension layer에서"라는
  맥락으로 해석합니다. 필요 시 문서를 후속 갱신합니다.

## Amendment (2026-07-19)

**Amended by:** ADR-0196 (agent-profile format & single-agent invoke, P1).
**Trigger:** `.omc/specs/multiagent-profiles-teams-architecture-spec.md` §9
(owner-ratified 2026-07-19) — "ADR-0008 — AMEND (not overturn)".

원 Decision은 **overturn되지 않습니다.** 이 amendment는 위 Decision ¶2가 아무런
구분 없이 쓰고 있던 "multi-agent 개념"이라는 표현을 두 갈래로 쪼개서, 어느 쪽이
어느 밴드에 속하는지만 명시합니다.

### (a) Single-agent profile **identity** — product-core resource concern

명명된 단일 agent 정체성(`<name>.md` 프로필)의 **format · discovery ·
resolution**은 *한 번의 실행을 형태짓는 선언적 resource*이며, 이미 product-core에
있는 skill 로딩과 **동형(isomorphic)**입니다. 이것은 kernel이 금지하는 multi-agent
orchestration이 아니라 **L1-adjacent**한 관심사이므로 **product-core**
(`packages/aelix-coding-agent`)에 둡니다.

세 가지를 함께 못박습니다.

1. **프로필 하나는 프로세스 하나입니다.** 정체성은 그 세션의 system prompt ·
   model/provider · tool allow-list · skill 경로를 정할 뿐, 두 번째 agent를
   만들지 않습니다. 스폰(spawn)이 없으면 조정(coordination)도 없고, 따라서
   L2가 아닙니다.
2. **동형성 논거는 *shape*에 대한 것이지 *package*에 대한 것이 아닙니다.**
   (spec §1 line 35가 skill 파서를 product-core에 있다고 적은 것은 오기 —
   실제로는 kernel `packages/aelix-agent-core/harness/skills.py`이고, 배선은
   `entry.py:1355-1356` + `:1427`입니다. ADR-0196 §"Known limitations"에서
   정정.) 프로필 파서가 product-core에 놓이는 근거는 "skill 파서가 거기 있어서"가
   아니라 **바로 이 amendment 조항**입니다.
3. **kernel은 문자 그대로 0줄 변경입니다.** ADR-0002 경계도, 아래 Decision ¶2도
   kernel에 대해서는 여전히 글자 그대로 참입니다.

### (b) Multi-agent spawn / teams / routing — 여전히 extension layer

subagent 스폰, 병렬·체인 위임, 팀 구성, 라우팅, 대시보드는 **변경 없이 L2 =
extension layer**입니다. 원 Decision ¶2가 그대로 지배합니다.

### Decision ¶2 문장의 scope 확정

"runtime core에는 multi-agent 개념을 두지 않습니다"는 다음 두 축 모두에서
**여전히 참**이며, 그 범위를 아래와 같이 명시합니다.

- **kernel 축** — `packages/aelix-agent-core`에는 profile 개념도, spawn 개념도,
  multi-agent event type도 없습니다 (untouched).
- **orchestration 축** — 스폰/팀/라우팅 *구현*은 extension에 있습니다.

즉 이 문장은 "product-core가 agent 정체성을 *선언적 resource로서* 읽는 것"까지
금지하지는 않습니다.

### 리뷰 게이트 — mechanism/policy rejection criterion (spec §9, verbatim)

아래 문장을 **원문 그대로** 심사 기준으로 등록합니다. 이후 이 경계를 넘는 PR은
설계 토론 없이 이 조항만으로 반려됩니다.

> *"Product-core owns the profile format/resolver and a subagent-runtime
> CONTRACT (types + binding slot) only. The spawn IMPLEMENTATION, caps,
> registry, and all topology/task-list/goal/dashboard decisions are extension
> policy. A PR adding spawn behavior, a supervisor, or a subagent lifecycle
> event TYPE to product-core or the kernel is rejected on sight."*

P1 시점의 적용 결과: product-core는 위 인용문의 **앞쪽 절반(format/resolver)만**
가집니다. subagent-runtime CONTRACT(`subagent_contract.py`, `bind_subagents`)는
P2에 착륙하며, 그 배치는 별도 ADR("subagent-runtime seam & `aelix-agents`
extension", spec §9)이 소유합니다.

## Amendment (2026-07-26)

**Amended by:** ADR-0197 (subagent-runtime seam & the bundled `aelix-agents`
extension, P2).
**Trigger:** P2 착륙으로 위 리뷰 게이트 인용문의 **뒤쪽 절반**(CONTRACT + spawn
implementation)이 실제 코드가 되었고, 그 과정에서 인용문의 두 낱말 —
`caps`, 그리고 (인용문에 아예 없던) *consent* — 이 어느 밴드에 속하는지가
실무에서 갈렸습니다.

원 Decision도, 2026-07-19 amendment도 **overturn되지 않습니다.** 이 amendment는
리뷰 게이트를 두 조항으로 **정밀화**할 뿐입니다.

### (c) `caps` 조항의 scope 확정 — tunable vs seam invariant

리뷰 게이트 인용문은 *"The spawn IMPLEMENTATION, **caps**, registry, and all
topology/task-list/goal/dashboard decisions are extension policy"* 라고 씁니다.
문자 그대로 읽으면 ADR-0197이 product-core `subagent_contract.py`에 두는
`MAX_SUBAGENT_DEPTH = 1`이 위반처럼 보입니다. 아닙니다. 다음과 같이 확정합니다.

> **Output caps, concurrency limits, topology, task lists, goals, and dashboards
> are extension policy. The only limits product-core may declare are seam
> invariants that exist to keep the seam SAFE (delegation depth) — never
> tunables.**

판별 기준은 한 줄입니다: **사용자나 프로필이 값을 고를 수 있으면 tunable이고,
따라서 extension policy입니다.** `MAX_SUBAGENT_DEPTH`는 하드코딩 상수이고,
`max_depth` 프로필 필드도 설정 키도 CLI 플래그도 없으며, 그 유일한 목적은
fork bomb 방지입니다 (ADR-0197 §(c)). 반대로 `output_cap` / `timeout_ms`는
프로필이 고르는 값이므로 extension(`aelix_agents`)이 소비합니다.

### (d) consent policy도 extension policy — product-core는 거절을 *전달*만 한다

리뷰 게이트 인용문에는 consent라는 낱말이 없었습니다. P2가 spawn-time consent
게이트(ADR-0197 §(i))를 도입하면서 필요해졌으므로, 다음 조항을 **추가**합니다.

> **Consent policy is likewise extension policy — product-core may SURFACE a
> refusal, never AUTHOR one.**

적용 결과(ADR-0197 §(i)/§(f)):

- consent 다이얼로그, 위임(widening) 상한, grant 수명, 프로젝트-스코프 금지는
  전부 `aelix_agents/consent.py`에 있습니다.
- product-core `tui/commands.py`의 `/agents run`은 bound Protocol을 호출하고
  돌아온 `SubagentResult(status="declined")`를 **렌더링만** 합니다. grant 타입은
  Protocol에 **의도적으로 없습니다.**
- 이 조항은 테스트로 강제됩니다: `aelix_coding_agent/**`에서 `SpawnGrant` /
  `request_spawn_consent` grep → 0 hits.

### Decision ¶2 문장의 scope — P2에서도 그대로 참

- **kernel 축** — `packages/aelix-agent-core`에는 여전히 profile 개념도, spawn
  개념도, subagent event type도 없습니다. 실측: 위 5개 심볼 grep → **0 hits**
  (ADR-0197 §Consequences, CI content gate로 상시 검증).
- **orchestration 축** — 스폰 *구현*은 번들 extension `aelix_agents`에 있고,
  product-core는 CONTRACT(타입 · Protocol · 바인딩 슬롯 · 버전 범위 · depth
  invariant)만 가집니다.

즉 P2 시점에 product-core는 리뷰 게이트 인용문의 **"and a subagent-runtime
CONTRACT (types + binding slot) only"** 절까지 가지며, 그 이상은 가지지
않습니다.

