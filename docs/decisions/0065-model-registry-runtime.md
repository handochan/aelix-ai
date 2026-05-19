# 0065. ModelRegistry Runtime

Status: Accepted (Sprint 6f / Phase 4.6 / W6 shipped)

## Context

Pi reference's `pi/packages/agent/src/model-registry.ts` is the runtime
**model catalog + provider auth + current-model selection** registry. It
is the single object the harness, the adapter dispatch (ADR-0045), the
RPC `set_model` / `cycle_model` / `get_available_models` commands
(ADR-0058 deferred set), and the OAuth `modify_models` callback (ADR-0059)
all reach into.

Sprint 6c shipped the OAuth surface, Sprint 6d shipped the RPC mode,
Sprint 6e closed the OAuth catalog — but the 3 RPC model commands stayed
deferred (ADR-0058 `DEFERRED_COMMANDS` = 20) because they require a
registry runtime. Sprint 6f W0 binding spec confirms Pi's
`ModelRegistry` exposes **14 public methods + 2 factory constructors**
across 5 surfaces.

Sprint 6f W4 + W5 produced 5 W6 must-fix findings binding the runtime
to Aelix:

- **P-175** — Pi clears `_load_error` at the **top** of every
  `_load_models` call so a successful reload drains the error state.
  Multi-provider failures must be newline-joined to preserve the
  per-provider diagnostic.
- **P-176** — Pi's `is_using_oauth` trusts the `AuthStorage`
  discriminator (`type === "oauth"`) **exclusively** — no fallback
  cross-check against `get_oauth_provider()`. The Sprint 6f draft had
  an extra guard that returned `False` when the provider was OAuth-eligible
  but stored as `apiKey` (which can legitimately happen during
  `--api-key` runtime override); Pi's stricter semantic is `True`
  iff stored as OAuth.
- **P-178 (MAJOR)** — `Model.headers` field wiring; the registry
  must thread it through `_model_to_dict` so RPC `set_model` /
  `get_available_models` responses preserve provider header overrides.
- **P-180** — Pi `ResolvedRequestAuth.authHeader` is the **bool**
  literal `true | false`. The Sprint 6f draft cast it to string
  (`"true"`) for JSONL serialization, breaking Pi shape.
- **P-184** — Pi uses `asyncio.get_running_loop()` for in-loop
  scheduling. Python 3.12 deprecates `asyncio.get_event_loop()` for
  the same use case. Sprint 6f W6 migrates to the non-deprecated
  call.

Sprint 6f W6 also resolves the P-187 binding decision: Pi's
`set_current_model` writes to `state.model` directly (no override
layer); the harness `current_model` property is a thin reader of
`_state.model`. Earlier drafts contemplated an override layer for the
RPC `set_model` path — P-187 rejects it as Pi-divergent.

## Decision

Port `pi/packages/agent/src/model-registry.ts` as
`packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py`.

### Class shape (14 methods + 2 constructors)

```python
class ModelRegistry:
    # 2 factory constructors
    @classmethod
    def create(cls, auth_storage: AuthStorage, ...) -> ModelRegistry: ...
    @classmethod
    def in_memory(cls, seed: list[Model] | None = None) -> ModelRegistry: ...

    # Surface 1: model access (4 methods)
    def get_all_models(self) -> list[Model]: ...
    def get_models_for_provider(self, provider_id: str) -> list[Model]: ...
    def get_model_by_id(self, model_id: str) -> Model | None: ...
    def find_model_with_provider(self, model_id: str) -> tuple[Model, Provider] | None: ...

    # Surface 2: auth (3 methods)
    def is_using_oauth(self, provider_id: str) -> bool: ...
    def resolve_request_auth(self, provider_id: str) -> ResolvedRequestAuth: ...
    def get_provider_config(self, provider_id: str) -> ProviderConfigInput | None: ...

    # Surface 3: lifecycle (3 methods)
    async def _load_models(self) -> None: ...
    def reload(self) -> None: ...
    def get_load_error(self) -> str | None: ...

    # Surface 4: dynamic (2 methods)
    def register_provider(self, config: ProviderConfigInput) -> None: ...
    def unregister_provider(self, provider_id: str) -> None: ...

    # Surface 5: display (2 methods)
    def get_provider_display_name(self, provider_id: str) -> str: ...
    def get_model_display_name(self, model_id: str) -> str: ...
```

### `ResolvedRequestAuth` discriminated union

```python
@dataclass(frozen=True)
class ResolvedRequestAuth:
    ok: bool          # discriminator
    api_key: str | None = None
    auth_header: bool = False     # P-180: Pi-strict bool, NOT str
    error: str | None = None
```

The `ok` field is the discriminator. When `ok=False`, `error` carries
the diagnostic and `api_key` / `auth_header` are absent (None / False).
When `ok=True`, `api_key` is populated and `auth_header` is the Pi
bool literal.

### `ProviderConfigInput` dataclass

```python
@dataclass(frozen=True)
class ProviderConfigInput:
    id: str
    name: str = ""
    base_url: str | None = None
    # Sprint 6f₁ deferred fields — see ADR-0066:
    # models: list[Model] | None = None    # full models.json schema = Sprint 6g
    # auth_kind: str | None = None         # AuthSource subset
```

The full `models.json` schema (which Pi parses via TypeBox in
`pi/packages/agent/src/types.ts` schema literals) is deferred to
Sprint 6g per Phase 4.6 §0; Sprint 6f₁ ships the minimum-viable shape
(`id` + `name` + `base_url`).

### P-187 binding: `set_current_model` writes `_state.model` directly

The harness `set_current_model(model_id)` method writes
`self._state.model = resolved_model_id` directly. The `current_model`
property is `return self._state.model`. There is no override layer
between RPC `set_model` and the state field — Pi's pattern is a
single source of truth.

### W6 must-fix wire-ups (applied in this commit)

- **P-175** — `_load_models` opens with `self._load_error = None`,
  and on any per-provider exception appends `f"{provider_id}: {err}"`
  to a list. After the loop, if the list is non-empty, joins with
  `"\n"` and assigns to `self._load_error`.
- **P-176** — `is_using_oauth(provider_id)` returns
  `self._auth_storage.has(provider_id) and self._auth_storage.get(provider_id).type == "oauth"`.
  No extra `get_oauth_provider` cross-check.
- **P-178** — `_model_to_dict(model)` in `rpc_mode` outputs the
  `headers` field when non-None, matching Pi's
  `model-registry.ts:312` serializer.
- **P-180** — `ResolvedRequestAuth.auth_header` is `bool`. Adapter
  code that needs the Pi JSONL serialization writes
  `"true" if auth.auth_header else "false"` at the wire boundary.
- **P-184** — Replaced `asyncio.get_event_loop()` with
  `asyncio.get_running_loop()` in 1 call site
  (`ModelRegistry._schedule_reload`).

## Consequences

### Immediate

- The 3 RPC model commands (`set_model` / `cycle_model` /
  `get_available_models`) move from `DEFERRED_COMMANDS` (Sprint 6d
  ADR-0058) → live (Sprint 6f Phase 4.6 closure). `DEFERRED_COMMANDS`
  drops from 20 → 17; `SUPPORTED_COMMANDS` rises from 9 → 12.
- The Sprint 6e OAuth `modify_models` callback (ADR-0059 / 0061) now
  has a registry to mutate. Copilot OAuth login can swap in
  `proxy-ep` base URLs; the registry round-trips them to
  `set_model` callers.
- The `_OAUTH_DEFERRED_PROVIDERS == {}` Sprint 6e closure invariant
  is preserved — ModelRegistry depends on the AuthStorage cascade
  but does not add new OAuth provider types.
- The harness `current_model` property + `set_current_model`
  method bind to `_state.model` directly (P-187). RPC `set_model`
  and `cycle_model` route through this seam.

### Carry-forward — Sprint 6g

- Full `model-resolver.ts` port (~530 LOC at Pi SHA 734e08e):
  partial-id matching + provider auto-detect + thinking-level
  inheritance.
- `applyProviderConfig` for `register_provider.config.models` —
  the full `models.json` schema port including TypeBox validation.
- `enableGitHubCopilotModel` POST automation
  (`/models/{id}/policy` per Copilot OAuth login).
- The 16 remaining deferred RPC commands (ADR-0058 minus the 3
  shipped here).
- `image-models.ts` / `image-models.generated.ts` (Pi parallel
  registry for image-generation models).
- Workspace-scoped model selection (Pi `cycle_model` honors
  `isScoped: true` for workspace-pinned model lists).
- W4 m1 — `harness.auth_storage` public exposure so default
  `ModelRegistry.create()` can resolve auth without explicit
  threading.
- W4 m2..m7 + NIT-1..NIT-6 (code-cleanup hygiene).
- P-177 (Sprint 6d carry — `is_streaming` Pi alignment).
- P-183 (`register_provider.config.models` wiring — paired with
  the full `models.json` schema).
- P-185..P-196 — INFO + clarification carry-forward.

## Alternatives considered

- **Override layer between RPC `set_model` and `_state.model`**:
  rejected per P-187 — Pi has no such layer. Adding one would mint
  silent drift on the next Pi pin bump.
- **Module-level catalog (no class)**: rejected — the catalog
  shares state with `AuthStorage`, the modify_models callback, and
  per-provider HTTP clients. A class lets `create()` thread these
  cleanly; a module-level catalog would force global mutable state.
- **Make `ResolvedRequestAuth.auth_header` a string at the
  dataclass level**: rejected per P-180 — Pi uses `boolean`.
  String coercion happens at the JSONL wire boundary only.

## Related

- ADR-0034 — Pi reference version pin (amended Sprint 6f).
- ADR-0045 — Provider Adapter Interface (consumes the registry).
- ADR-0049 — Message dataclass provenance (Sprint 6f amends for 5
  new Model fields).
- ADR-0058 — Phase 4.4 strict superset closure (3 deferred
  commands now live).
- ADR-0061 — AuthStorage layered cascade (the auth seam ModelRegistry
  reads).
- ADR-0064 — ModelCost / UsageCost / Usage / headers field shapes.
- ADR-0066 — Phase 4.6 strict superset closure.

## Phase

Sprint 6f / Phase 4.6 / W6 (shipped — 14 methods live; 3 RPC model
commands live; `model-resolver.ts` + full `models.json` schema +
`enableGitHubCopilotModel` automation + 16 remaining RPC commands
deferred to Sprint 6g per Phase 4.6 §0).
