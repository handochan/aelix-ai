# 0029. Pi-Parity Acceptance Test Harness

Status: Draft (Phase 2.1+ ongoing)

## Context

Aelix의 1차 원칙은 "pi agent를 완전히, 완벽하게 구현한다"입니다 (ADR-0003).
매 sprint마다 Pi parity를 검증해야 합니다.

현재 방식: architect agent의 수동 audit (스프린트 W5 wave). 수동 audit은:

- Sprint마다 반복되는 인지 비용이 큽니다.
- 미묘한 behavioral drift를 놓칠 수 있습니다.
- "우리는 match한다고 믿는다" 수준의 확신을 제공합니다.

Mechanical drift detection이 필요합니다.

### 고려한 대안

1. **Unit test 내 Pi parity assertion 혼합**: 기존 test에 parity assertion 추가.
   Pi fixture와 Aelix test가 섞여 유지 비용이 높습니다.
2. **별도 `tests/pi_parity/` lane** (채택): 격리된 lane. CI에서 별도로 실행
   가능합니다.

## Decision

`tests/pi_parity/` 별도 test lane을 Phase 2.1부터 구축합니다.

### 구조

```text
tests/pi_parity/
├── conftest.py              # Pi fixture loader
├── fixtures/                # vendored Pi snapshots
│   ├── tool_call_sequence.json
│   ├── hook_event_sequences/
│   │   └── tool_result_reducer.json
│   └── session_snapshots/
│       └── basic_turn.jsonl
└── test_pi_parity_tool_execution.py
    test_pi_parity_hook_events.py
    test_pi_parity_session.py
```

### Fixture 전략

Pi 소스에서 fixture JSON을 vendor합니다:

- **Recorded LLM responses**: Pi의 `__mocks__` 또는 test fixtures에서 추출한
  message sequences
- **Hook event sequences**: Pi 실행 시 emit되는 event 순서 snapshots
- **Session JSONL snapshots**: Pi session 파일 포맷 examples

### Test 방식

```python
@pytest.mark.parametrize("fixture", load_pi_fixtures("hook_event_sequences"))
def test_hook_event_sequence_matches_pi(fixture):
    result = run_aelix_with_fixture(fixture["input"])
    assert_message_level_equivalent(result.events, fixture["expected_events"])
    assert_event_sequence_equivalent(result.sequence, fixture["expected_sequence"])
```

동일 시나리오를 Aelix를 통해 실행하고:
- message-level equivalence (content, role)
- event-sequence equivalence (type, order, payload shape)
를 assert합니다.

### CI 실행

```bash
uv sync --group pi-parity
pytest tests/pi_parity/ -v --tb=short
```

`pi-parity` dependency group은 fixture download 스크립트와 추가 assertion
helper를 포함합니다.

## Consequences

- Pi behavior 변경 시 CI에서 즉시 surface됩니다. 수동 audit 없이도
  regression을 감지합니다.
- "우리는 match한다고 믿는다" → "tests가 match를 증명한다" 수준으로 확신이
  높아집니다.
- Vendored fixture 유지 비용: Pi snapshot을 주기적으로 refresh해야 합니다.
  Sprint W5 audit 시 fixture도 함께 갱신합니다.
- Phase 2.1부터 시작합니다 (tool_call/tool_result reducer fixtures 우선).
  Session fixtures는 Phase 2.2 (ADR-0022) land 시 추가합니다.
- ADR-0026 (workspace-root pytest layout)과 자연스럽게 통합됩니다.
  `tests/pi_parity/`는 `tests/` 아래에 위치합니다.
- 수동 W5 audit이 완전히 대체되지는 않습니다. 구조적 parity(코드 패턴, 누락
  ADR)는 여전히 architect agent 판단이 필요합니다.
