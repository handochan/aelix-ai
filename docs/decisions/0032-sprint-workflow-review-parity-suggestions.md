# 0032. Sprint Workflow — Review + Pi Parity + Suggestions

Status: Accepted (Sprint 2 onwards)

## Context

Sprint 2 W6 review 과정에서 사용자가 명시적으로 binding했습니다: 매 sprint마다
(1) 구현 → (2) 코드 리뷰 → (3) Pi parity 비교 → (4) 더 좋은 방법 제안 패턴은
선택사항이 아닌 **mandatory gate**입니다.

Sprint 1은 이 패턴을 일부 적용했으나 ADR로 pin되지 않아 "즉흥적 추가"로
인식될 수 있었습니다. Sprint workflow가 ADR로 명문화되지 않으면 sprint마다
재협상이 발생합니다.

## Decision

Sprint 2 이후 모든 sprint는 다음 wave 구조를 따릅니다:

| Wave | 담당 | 성격 | 설명 |
| --- | --- | --- | --- |
| W0 | — | 선택 | 컨텍스트 수집 (이전 sprint 결과, 열린 질문 정리) |
| W1 | architect (opus, read-only) | **필수** | 계획 수립 → `.omc/specs/sprint-N-*.md` |
| W1.5 | critic (opus, read-only) | 선택 | 대형 sprint에 한해 plan review |
| W2 | executor (opus/sonnet) | **필수** | 구현 (코드 + tests) |
| W3 | verifier | **필수** | lint + tests + pyright + smoke |
| W4 | code-reviewer (opus, read-only) | **필수** | 코드 리뷰 — mandatory gate |
| W5 | architect (opus, read-only) | **필수** | Pi parity audit + 더 좋은 방법 제안 — mandatory gate |
| W6 | executor + git-master | **필수** | Review/parity findings 적용 + atomic commits |
| W7 | verifier (opus) | 선택 | Phase 2+ 이상 critical sprint에 한해 최종 검증 |

### Mandatory gates 상세

**W4 Code Review** 요구사항:
- 모든 변경 파일을 순서대로 리뷰
- 버그, 타입 오류, Pi parity 위반을 분류 (M-: mandatory fix / S-: suggestion)
- M- items만 W6에서 필수 적용

**W5 Pi Parity Audit + 제안** 요구사항:
- Pi 소스와 Aelix 구현을 field-by-field 비교
- 의도된 divergence (Aelix-only improvement)와 버그성 divergence 구분
- 더 나은 Python 관용 구현 제안 (S- items)
- ADR-0029 parity test harness와 연계

## Consequences

- 매 sprint에 4개 mandatory wave (W1 plan + W4 code-review + W5 pi-parity + W6
  commit)가 보장됩니다.
- 가벼운 작업도 동일 workflow를 거칩니다. overhead vs quality 일관성을
  선택합니다. 필요시 W1.5/W7을 생략하는 것으로 조정합니다.
- Sprint workflow는 ADR로 pin됩니다. bypass는 새 ADR이 필요합니다.
  "이번엔 빠르게"는 허용되지 않습니다.
- 1차 원칙 (Pi parity, ADR-0003)을 process-level에서 강제합니다. W5가
  누락되면 sprint는 완료되지 않은 것으로 간주합니다.
- W4/W5가 M-item을 발견하면 W6가 blocking됩니다. S-item은 다음 sprint의
  W1 컨텍스트로 누적됩니다.
- ADR-0029 (Pi-Parity Acceptance Test Harness)는 W5 수동 audit의 기계화입니다.
  ADR-0029가 성숙하면 W5 scope를 "구조적 parity + 제안"으로 좁힐 수 있습니다.
