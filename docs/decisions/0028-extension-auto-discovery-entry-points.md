# 0028. Extension Auto-Discovery — Directory Scan (Pi Parity) + entry_points (Aelix-Additive)

Status: **Accepted (Sprint 5a / Phase 3.1.1 shipped — directory scan PRIMARY (Pi parity), entry_points ADDITIVE)**

Supersedes (partial): ADR-0012 (Extension Discovery Model — Deferred)

## Sprint 5a P-21 verification block (2026-05-17 at SHA `734e08e`)

> **The original Draft ADR (Sprint 2) inverted the primary/fallback
> relationship.** Pi's actual discovery model — verified at Pi
> `packages/coding-agent/src/core/extensions/loader.ts:575-621` SHA
> `734e08e` — is a 3-tier **directory scan** (`cwd/.pi/extensions/`,
> `~/.pi/extensions/`, explicit configured paths). Pi has NO
> `entry_points` analogue because JavaScript lacks one.
>
> Sprint 5a corrects the framing: **directory scan is PRIMARY (Pi
> parity); `entry_points` is ADDITIVE (Aelix-only convenience), loaded
> LAST so installed packages cannot shadow project-local files.** The
> 1st-principle invariant ("pi agent를 완전 동일하게 완벽하게 구현이
> 1차적 목표") demands this reversal.

## Context

ADR-0012는 extension discovery를 Phase 3에서 결정하기로 defer했습니다.
미결정 항목: `~/.aelix/extensions`, `pyproject.toml`, `entry_points` 우선순위.

Pi는 directory scan 만을 사용합니다 (Pi `coding-agent` loader.ts 575-621,
SHA `734e08e`):

1. **Project-local**: `cwd/.pi/extensions/` 내 파일/패키지를 `jiti` runtime
   loader로 동적 로드.
2. **Global**: `~/.pi/extensions/` (또는 `agentDir` override) 내 파일/패키지.
3. **Explicit**: caller가 지정한 path (file 또는 directory).

Pi의 JavaScript 생태계에는 `entry_points`에 상응하는 first-class 표준이 없습니다.
Python 생태계는 PEP 621 `[project.entry-points]`를 제공합니다. 이는 installed
package가 그룹별 entry point를 선언하고, 소비자가 `importlib.metadata`로
discovery하는 표준 메커니즘입니다.

## Decision (Sprint 5a Accepted)

Phase 3.1에서 **directory scan을 primary로** 채택하고 `entry_points` 기반
discovery를 **additive 형태로** 마지막 tier로 layer합니다.

### Tier 1 — Project-local directory (PRIMARY)

```
cwd/.aelix/extensions/
  ├── my_ext.py                # *.py file → load directly
  ├── plugin/                  # subdir with __init__.py → load as module
  │   └── __init__.py
  └── pkg/                     # subdir with pyproject.toml [tool.aelix]
      ├── pyproject.toml       #   extensions = ["main.py", "extra.py"]
      ├── main.py
      └── extra.py
```

### Tier 2 — Global directory

`~/.aelix/extensions/` (또는 `agent_dir` override) — same shape as Tier 1.

### Tier 3 — Explicit configured paths

```python
discover_and_load_extensions(
    [Path("/path/to/external.py"), my_inline_factory],
    cwd=Path.cwd(),
)
```

Directory entries here are recursively expanded via `_discover_in_dir`;
files / factories pass through unchanged.

### Tier 4 — entry_points (ADDITIVE, loaded LAST)

```toml
# my-aelix-extension/pyproject.toml
[project.entry-points."aelix.extensions"]
my-ext = "my_aelix_extension:MyExtension"
```

Loaded via `importlib.metadata.entry_points(group="aelix.extensions")`.
Each endpoint's `.load()` result is treated as an inline factory.

### Priority (Pi parity + Aelix-additive)

1. **Project-local** wins on tool-name collisions.
2. **Global** beats explicit paths.
3. **Explicit** beats entry_points.
4. **entry_points** lands last — pure additive convenience.

### Dedup + error containment

- Dedup by `Path.resolve()` for filesystem entries; by `ep.name=ep.value`
  string key for entry_points.
- Per-entry `try/except` in each tier — one broken extension never
  aborts the wave.

## Rationale

- **Pi parity first**: matches Pi's `discoverAndLoadExtensions` exactly so
  Pi extension authors writing for Aelix get the same discovery surface.
- **Marketplace + entry_points still work**: `pip install aelix-ext-foo`
  registers via entry_points, just at lower priority than user-edited
  files. This preserves the "edit locally → reflect immediately" workflow
  developers expect.
- **Editable installs (`pip install -e`)** continue to work because
  entry_points resolve through `importlib.metadata`.

## Consequences

- ADR-0012의 "미결정" 상태를 Sprint 5a에서 해소합니다. **directory scan이
  primary, entry_points가 additive** 입니다 (Sprint 2 Draft 명세의
  정반대; W1 Architect P-21 검증).
- Pi extension authors가 Aelix로 옮기는 cost가 directory placement만
  바뀝니다 — discovery 모델은 1:1.
- `tests/test_extension_discovery.py` + `test_extension_discovery_entry_points.py`가
  4 tier의 ordering / dedup / error containment를 mechanise합니다.
- Phase 3에서 ADR-0012를 이 ADR로 Supersede 처리합니다.
- Marketplace (ADR-0005) integration: marketplace를 통해 설치된 extension은
  entry_points 방식으로 자동 등록됩니다 (lowest priority tier).
