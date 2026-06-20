"""``models.json`` custom-model loader — P0 #4 (ADR-0140).

Pi parity: ``packages/coding-agent/src/core/model-registry.ts`` (SHA
``734e08e``) — the custom-model loading machinery that
:class:`aelix_coding_agent.model_registry.ModelRegistry` orchestrates.
Sprint 6f₁ shipped the runtime with this path stubbed
(:class:`NotImplementedError`); this module lands the real loader so a
user's ``~/.aelix/agent/models.json`` can add providers/models and
override built-ins.

The pure, registry-state-free helpers live here; the orchestration
(``loadModels`` / ``loadCustomModels`` wiring, the per-load request-config
maps, OAuth ``modify_models``) stays on :class:`ModelRegistry` and drives
these via two callbacks (``store_provider_request_config`` /
``store_model_headers``) so this module never imports the registry.

Ported surface (Pi parity):

- :func:`strip_json_comments` — Pi ``stripJsonComments`` (verbatim two-pass
  regex: ``//`` line comments + trailing commas, string-literal aware).
- :func:`validate_models_config` — schema validation (Pi's TypeBox
  ``validateModelsConfig.Check`` + ``formatValidationPath``). Hand-written
  to mirror the TypeBox schema; produces ``path: message`` pairs with Pi's
  dotted instance paths. (The message TEXT is not byte-identical to
  TypeBox — documented divergence; the PATHS and accept/reject set match.)
- :func:`validate_config_semantics` — Pi ``validateConfig`` (verbatim
  imperative checks + error strings).
- :func:`merge_compat` — Pi ``mergeCompat``.
- :func:`apply_model_override` — Pi ``applyModelOverride``.
- :func:`merge_custom_models` — Pi ``mergeCustomModels``.
- :func:`parse_models` — Pi ``parseModels``.
- :func:`load_built_in_models` — Pi ``loadBuiltInModels``.
- :func:`load_custom_models` — Pi ``loadCustomModels`` (+
  ``emptyCustomModelsResult``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from aelix_ai.models import get_models, get_providers
from aelix_ai.streaming import Model, ModelCost

# ── stripJsonComments ──────────────────────────────────────────────────
#
# Pi ``stripJsonComments`` (verbatim two-regex impl). The first pass
# strips ``//`` line comments that are NOT inside a JSON string; the
# second drops trailing commas before ``}`` / ``]``. Block comments
# (``/* */``) are intentionally NOT handled — Pi doesn't either.
_STRING_OR_LINE_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')
_STRING_OR_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])')


def strip_json_comments(content: str) -> str:
    """Pi parity: ``model-registry.ts::stripJsonComments``."""

    def _strip_comment(match: re.Match[str]) -> str:
        text = match.group(0)
        # Keep string literals; drop ``//`` comments.
        return text if text[0] == '"' else ""

    def _strip_trailing_comma(match: re.Match[str]) -> str:
        tail = match.group(1)
        if tail is not None:
            # Matched ``,(\s*[}\]])`` — drop the comma, keep the bracket.
            return tail
        text = match.group(0)
        return text if text[0] == '"' else ""

    content = _STRING_OR_LINE_COMMENT.sub(_strip_comment, content)
    content = _STRING_OR_TRAILING_COMMA.sub(_strip_trailing_comma, content)
    return content


# ── Schema validation (Pi TypeBox-equivalent) ─────────────────────────
#
# Pi compiles ``ModelsConfigSchema`` with TypeBox and reports each error
# as ``  - {formatValidationPath(e)}: {e.message}``. We mirror the schema
# by hand. ``Type.Object`` does NOT forbid extra keys (no
# ``additionalProperties: false``), so unknown keys are IGNORED — only
# declared keys are type-checked, and only genuinely-required fields
# (``providers``, model ``id``, and the four ``cost`` fields on a model
# DEFINITION) are presence-checked. ``compat`` is a permissive union of
# all-optional objects, so it validates as "an object" (matching the
# effective TypeBox behavior).

_INPUT_LITERALS = ("text", "image")
_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")

# A single schema error: ``(pi-dotted-instance-path, message)``.
ValidationError = tuple[str, str]


def _is_number(value: Any) -> bool:
    # JSON numbers; ``bool`` is an ``int`` subclass — exclude it.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_str_min1(value: Any, path: str, errors: list[ValidationError]) -> None:
    if not isinstance(value, str):
        errors.append((path, "Expected string"))
    elif len(value) < 1:
        errors.append((path, "Expected string length >= 1"))


def _check_bool(value: Any, path: str, errors: list[ValidationError]) -> None:
    if not isinstance(value, bool):
        errors.append((path, "Expected boolean"))


def _check_number(value: Any, path: str, errors: list[ValidationError]) -> None:
    if not _is_number(value):
        errors.append((path, "Expected number"))


def _check_headers(value: Any, path: str, errors: list[ValidationError]) -> None:
    if not isinstance(value, dict):
        errors.append((path, "Expected object"))
        return
    for key, val in value.items():
        if not isinstance(val, str):
            errors.append((f"{path}.{key}", "Expected string"))


def _check_compat(value: Any, path: str, errors: list[ValidationError]) -> None:
    # Permissive: Pi's ProviderCompat is a union of all-optional objects.
    if not isinstance(value, dict):
        errors.append((path, "Expected object"))


def _check_input(value: Any, path: str, errors: list[ValidationError]) -> None:
    if not isinstance(value, list):
        errors.append((path, "Expected array"))
        return
    for i, item in enumerate(value):
        if item not in _INPUT_LITERALS:
            errors.append((f"{path}.{i}", "Expected 'text' | 'image'"))


def _check_thinking_level_map(
    value: Any, path: str, errors: list[ValidationError]
) -> None:
    if not isinstance(value, dict):
        errors.append((path, "Expected object"))
        return
    for level in _THINKING_LEVELS:
        if (
            level in value
            and value[level] is not None
            and not isinstance(value[level], str)
        ):
            errors.append((f"{path}.{level}", "Expected string | null"))


def _check_cost(
    value: Any, path: str, errors: list[ValidationError], *, required: bool
) -> None:
    if not isinstance(value, dict):
        errors.append((path, "Expected object"))
        return
    for fieldname in ("input", "output", "cacheRead", "cacheWrite"):
        if fieldname in value:
            _check_number(value[fieldname], f"{path}.{fieldname}", errors)
        elif required:
            errors.append((f"{path}.{fieldname}", "Expected required property"))


def _check_model_def(
    model: Any, path: str, errors: list[ValidationError], *, is_override: bool
) -> None:
    if not isinstance(model, dict):
        errors.append((path, "Expected object"))
        return
    if not is_override:
        # ``id`` is required on a model DEFINITION (absent on overrides).
        if "id" not in model:
            errors.append((f"{path}.id", "Expected required property"))
        else:
            _check_str_min1(model["id"], f"{path}.id", errors)
        for fieldname in ("api", "baseUrl"):
            if fieldname in model:
                _check_str_min1(model[fieldname], f"{path}.{fieldname}", errors)
    if "name" in model:
        _check_str_min1(model["name"], f"{path}.name", errors)
    if "reasoning" in model:
        _check_bool(model["reasoning"], f"{path}.reasoning", errors)
    if "thinkingLevelMap" in model:
        _check_thinking_level_map(
            model["thinkingLevelMap"], f"{path}.thinkingLevelMap", errors
        )
    if "input" in model:
        _check_input(model["input"], f"{path}.input", errors)
    if "cost" in model:
        _check_cost(model["cost"], f"{path}.cost", errors, required=not is_override)
    if "contextWindow" in model:
        _check_number(model["contextWindow"], f"{path}.contextWindow", errors)
    if "maxTokens" in model:
        _check_number(model["maxTokens"], f"{path}.maxTokens", errors)
    if "headers" in model:
        _check_headers(model["headers"], f"{path}.headers", errors)
    if "compat" in model:
        _check_compat(model["compat"], f"{path}.compat", errors)


def _check_provider_config(
    config: Any, path: str, errors: list[ValidationError]
) -> None:
    if not isinstance(config, dict):
        errors.append((path, "Expected object"))
        return
    for fieldname in ("name", "baseUrl", "apiKey", "api"):
        if fieldname in config:
            _check_str_min1(config[fieldname], f"{path}.{fieldname}", errors)
    if "headers" in config:
        _check_headers(config["headers"], f"{path}.headers", errors)
    if "compat" in config:
        _check_compat(config["compat"], f"{path}.compat", errors)
    if "authHeader" in config:
        _check_bool(config["authHeader"], f"{path}.authHeader", errors)
    if "models" in config:
        if not isinstance(config["models"], list):
            errors.append((f"{path}.models", "Expected array"))
        else:
            for i, model in enumerate(config["models"]):
                _check_model_def(
                    model, f"{path}.models.{i}", errors, is_override=False
                )
    if "modelOverrides" in config:
        overrides = config["modelOverrides"]
        if not isinstance(overrides, dict):
            errors.append((f"{path}.modelOverrides", "Expected object"))
        else:
            for model_id, override in overrides.items():
                _check_model_def(
                    override,
                    f"{path}.modelOverrides.{model_id}",
                    errors,
                    is_override=True,
                )


def validate_models_config(parsed: Any) -> list[ValidationError]:
    """Pi parity: ``validateModelsConfig.Check`` + ``formatValidationPath``.

    Returns a list of ``(path, message)`` pairs — empty means valid. The
    ``path`` uses Pi's dotted instance-path form (e.g.
    ``providers.myco.models.0.contextWindow``).
    """

    errors: list[ValidationError] = []
    if not isinstance(parsed, dict):
        errors.append(("root", "Expected object"))
        return errors
    if "providers" not in parsed:
        errors.append(("providers", "Expected required property"))
        return errors
    providers = parsed["providers"]
    if not isinstance(providers, dict):
        errors.append(("providers", "Expected object"))
        return errors
    for provider_name, provider_config in providers.items():
        _check_provider_config(
            provider_config, f"providers.{provider_name}", errors
        )
    return errors


def validate_config_semantics(config: dict[str, Any]) -> None:
    """Pi parity: ``model-registry.ts::validateConfig`` (verbatim).

    Raises :class:`ValueError` (Pi throws ``Error``) on a semantic failure
    that the structural schema can't express — e.g. a custom-model
    provider missing ``baseUrl`` / ``apiKey``.
    """

    built_in_providers = set(get_providers())

    for provider_name, provider_config in config["providers"].items():
        is_built_in = provider_name in built_in_providers
        has_provider_api = bool(provider_config.get("api"))
        models = provider_config.get("models") or []
        model_overrides = provider_config.get("modelOverrides")
        has_model_overrides = bool(model_overrides) and len(model_overrides) > 0

        if len(models) == 0:
            if (
                not provider_config.get("baseUrl")
                and not provider_config.get("headers")
                and not provider_config.get("compat")
                and not has_model_overrides
            ):
                raise ValueError(
                    f"Provider {provider_name}: must specify "
                    f'"baseUrl", "headers", "compat", "modelOverrides", or "models".'
                )
        elif not is_built_in:
            if not provider_config.get("baseUrl"):
                raise ValueError(
                    f'Provider {provider_name}: "baseUrl" is required '
                    f"when defining custom models."
                )
            if not provider_config.get("apiKey"):
                raise ValueError(
                    f'Provider {provider_name}: "apiKey" is required '
                    f"when defining custom models."
                )

        for model_def in models:
            has_model_api = bool(model_def.get("api"))
            if not has_provider_api and not has_model_api and not is_built_in:
                raise ValueError(
                    f"Provider {provider_name}, model {model_def.get('id')}: "
                    f'no "api" specified. Set at provider or model level.'
                )
            if not model_def.get("id"):
                raise ValueError(f'Provider {provider_name}: model missing "id"')
            context_window = model_def.get("contextWindow")
            if context_window is not None and context_window <= 0:
                raise ValueError(
                    f"Provider {provider_name}, model {model_def['id']}: "
                    f"invalid contextWindow"
                )
            max_tokens = model_def.get("maxTokens")
            if max_tokens is not None and max_tokens <= 0:
                raise ValueError(
                    f"Provider {provider_name}, model {model_def['id']}: "
                    f"invalid maxTokens"
                )


# ── compat / override merging ──────────────────────────────────────────


def merge_compat(
    base_compat: dict[str, Any] | None, override_compat: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Pi parity: ``model-registry.ts::mergeCompat``.

    Shallow-merges ``override_compat`` over ``base_compat`` with a deep
    merge for the two nested routing blocks (``openRouterRouting`` /
    ``vercelGatewayRouting``). Returns ``base_compat`` verbatim when there
    is no override.
    """

    if not override_compat:
        return base_compat

    base = base_compat or {}
    merged: dict[str, Any] = {**base, **override_compat}

    if base.get("openRouterRouting") or override_compat.get("openRouterRouting"):
        merged["openRouterRouting"] = {
            **(base.get("openRouterRouting") or {}),
            **(override_compat.get("openRouterRouting") or {}),
        }
    if base.get("vercelGatewayRouting") or override_compat.get("vercelGatewayRouting"):
        merged["vercelGatewayRouting"] = {
            **(base.get("vercelGatewayRouting") or {}),
            **(override_compat.get("vercelGatewayRouting") or {}),
        }

    return merged


def apply_model_override(model: Model, override: dict[str, Any]) -> Model:
    """Pi parity: ``model-registry.ts::applyModelOverride``.

    Returns a NEW :class:`Model` (the dataclass is frozen) with the
    override's present fields applied. ``compat`` is always re-derived via
    :func:`merge_compat`.
    """

    changes: dict[str, Any] = {}
    if "name" in override:
        changes["name"] = override["name"]
    if "reasoning" in override:
        changes["reasoning"] = override["reasoning"]
    if "thinkingLevelMap" in override:
        changes["thinking_level_map"] = {
            **(model.thinking_level_map or {}),
            **(override["thinkingLevelMap"] or {}),
        }
    if "input" in override:
        changes["input"] = list(override["input"])
    if "contextWindow" in override:
        changes["context_window"] = override["contextWindow"]
    if "maxTokens" in override:
        changes["max_tokens"] = override["maxTokens"]
    if override.get("cost"):
        cost = override["cost"]
        changes["cost"] = ModelCost(
            input=cost.get("input", model.cost.input),
            output=cost.get("output", model.cost.output),
            cache_read=cost.get("cacheRead", model.cost.cache_read),
            cache_write=cost.get("cacheWrite", model.cost.cache_write),
        )
    changes["compat"] = merge_compat(model.compat, override.get("compat"))
    return replace(model, **changes)


def merge_custom_models(
    built_in_models: list[Model], custom_models: list[Model]
) -> list[Model]:
    """Pi parity: ``model-registry.ts::mergeCustomModels``.

    Custom wins on a ``(provider, id)`` conflict; otherwise the custom
    model is appended.
    """

    merged = list(built_in_models)
    for custom in custom_models:
        existing_index = next(
            (
                i
                for i, m in enumerate(merged)
                if m.provider == custom.provider and m.id == custom.id
            ),
            -1,
        )
        if existing_index >= 0:
            merged[existing_index] = custom
        else:
            merged.append(custom)
    return merged


def _parse_cost(cost: dict[str, Any] | None) -> ModelCost:
    if not cost:
        return ModelCost()
    return ModelCost(
        input=cost["input"],
        output=cost["output"],
        cache_read=cost["cacheRead"],
        cache_write=cost["cacheWrite"],
    )


def parse_models(
    config: dict[str, Any],
    store_model_headers: Callable[[str, str, dict[str, str] | None], None],
) -> list[Model]:
    """Pi parity: ``model-registry.ts::parseModels``.

    Builds custom :class:`Model` objects from ``config.providers[].models``.
    ``api`` / ``baseUrl`` resolve model → provider → built-in-provider
    default; a model with neither resolvable is skipped (Pi ``continue``).
    Per-model ``headers`` are forwarded to ``store_model_headers`` and set
    to ``None`` on the Model itself (Pi stores request headers separately).
    """

    models: list[Model] = []
    built_in_providers = set(get_providers())

    defaults_cache: dict[str, dict[str, str]] = {}

    def get_built_in_defaults(provider_name: str) -> dict[str, str] | None:
        if provider_name not in built_in_providers:
            return None
        if provider_name in defaults_cache:
            return defaults_cache[provider_name]
        built_in = get_models(provider_name)
        if not built_in:
            return None
        defaults = {"api": built_in[0].api, "base_url": built_in[0].base_url}
        defaults_cache[provider_name] = defaults
        return defaults

    for provider_name, provider_config in config["providers"].items():
        model_defs = provider_config.get("models") or []
        if len(model_defs) == 0:
            continue

        built_in_defaults = get_built_in_defaults(provider_name)

        for model_def in model_defs:
            api = (
                model_def.get("api")
                or provider_config.get("api")
                or (built_in_defaults and built_in_defaults["api"])
            )
            if not api:
                continue

            base_url = (
                model_def.get("baseUrl")
                or provider_config.get("baseUrl")
                or (built_in_defaults and built_in_defaults["base_url"])
            )
            if not base_url:
                continue

            compat = merge_compat(
                provider_config.get("compat"), model_def.get("compat")
            )
            store_model_headers(
                provider_name, model_def["id"], model_def.get("headers")
            )

            input_value = model_def.get("input")
            models.append(
                Model(
                    id=model_def["id"],
                    name=model_def.get("name") or model_def["id"],
                    api=api,
                    provider=provider_name,
                    base_url=base_url,
                    reasoning=model_def.get("reasoning", False),
                    thinking_level_map=model_def.get("thinkingLevelMap"),
                    input=list(input_value) if input_value is not None else ["text"],
                    cost=_parse_cost(model_def.get("cost")),
                    context_window=model_def.get("contextWindow", 128000),
                    max_tokens=model_def.get("maxTokens", 16384),
                    headers=None,
                    compat=compat,
                )
            )

    return models


def load_built_in_models(
    overrides: dict[str, ProviderOverride],
    model_overrides: dict[str, dict[str, dict[str, Any]]],
) -> list[Model]:
    """Pi parity: ``model-registry.ts::loadBuiltInModels``.

    Applies provider-level (``baseUrl`` / ``compat``) and per-model
    overrides from ``models.json`` onto the built-in catalog.
    """

    out: list[Model] = []
    for provider in get_providers():
        provider_override = overrides.get(provider)
        per_model_overrides = model_overrides.get(provider)
        for model in get_models(provider):
            current = model
            if provider_override is not None:
                current = replace(
                    current,
                    base_url=(
                        provider_override.base_url
                        if provider_override.base_url is not None
                        else current.base_url
                    ),
                    compat=merge_compat(current.compat, provider_override.compat),
                )
            if per_model_overrides is not None:
                model_override = per_model_overrides.get(model.id)
                if model_override:
                    current = apply_model_override(current, model_override)
            out.append(current)
    return out


# ── loadCustomModels ───────────────────────────────────────────────────


@dataclass
class ProviderOverride:
    """Pi parity: ``model-registry.ts::ProviderOverride``."""

    base_url: str | None = None
    compat: dict[str, Any] | None = None


@dataclass
class LoadCustomModelsResult:
    """Pi parity: ``model-registry.ts::CustomModelsResult``."""

    models: list[Model]
    overrides: dict[str, ProviderOverride]
    model_overrides: dict[str, dict[str, dict[str, Any]]]
    error: str | None


def empty_custom_models_result(error: str | None = None) -> LoadCustomModelsResult:
    """Pi parity: ``model-registry.ts::emptyCustomModelsResult``."""

    return LoadCustomModelsResult(
        models=[], overrides={}, model_overrides={}, error=error
    )


def load_custom_models(
    models_json_path: str,
    *,
    store_provider_request_config: Callable[[str, dict[str, Any]], None],
    store_model_headers: Callable[[str, str, dict[str, str] | None], None],
) -> LoadCustomModelsResult:
    """Pi parity: ``model-registry.ts::loadCustomModels``.

    Reads + comment-strips + parses + validates ``models.json``, then
    builds the override maps and the custom-model list. Every failure path
    returns an :func:`empty_custom_models_result` carrying a Pi-verbatim
    ``error`` string (so built-ins still load while ``getError()`` surfaces
    the cause). The two callbacks populate the registry's per-load
    request-config maps without coupling this module to the registry.
    """

    if not os.path.exists(models_json_path):
        return empty_custom_models_result()

    try:
        content = Path(models_json_path).read_text(encoding="utf-8")
        parsed = json.loads(strip_json_comments(content))

        schema_errors = validate_models_config(parsed)
        if schema_errors:
            formatted = "\n".join(
                f"  - {path}: {message}" for path, message in schema_errors
            )
            return empty_custom_models_result(
                f"Invalid models.json schema:\n{formatted}"
                f"\n\nFile: {models_json_path}"
            )

        config: dict[str, Any] = parsed

        # Pi: additional (semantic) validation — throws on failure.
        validate_config_semantics(config)

        overrides: dict[str, ProviderOverride] = {}
        model_overrides: dict[str, dict[str, dict[str, Any]]] = {}

        for provider_name, provider_config in config["providers"].items():
            if provider_config.get("baseUrl") or provider_config.get("compat"):
                overrides[provider_name] = ProviderOverride(
                    base_url=provider_config.get("baseUrl"),
                    compat=provider_config.get("compat"),
                )

            store_provider_request_config(provider_name, provider_config)

            provider_model_overrides = provider_config.get("modelOverrides")
            if provider_model_overrides:
                model_overrides[provider_name] = dict(provider_model_overrides)
                for model_id, model_override in provider_model_overrides.items():
                    store_model_headers(
                        provider_name, model_id, model_override.get("headers")
                    )

        return LoadCustomModelsResult(
            models=parse_models(config, store_model_headers),
            overrides=overrides,
            model_overrides=model_overrides,
            error=None,
        )
    except json.JSONDecodeError as exc:
        return empty_custom_models_result(
            f"Failed to parse models.json: {exc}\n\nFile: {models_json_path}"
        )
    except Exception as exc:  # noqa: BLE001 — Pi catches all + reports the message.
        return empty_custom_models_result(
            f"Failed to load models.json: {exc}\n\nFile: {models_json_path}"
        )


__all__ = [
    "LoadCustomModelsResult",
    "ProviderOverride",
    "apply_model_override",
    "empty_custom_models_result",
    "load_built_in_models",
    "load_custom_models",
    "merge_compat",
    "merge_custom_models",
    "parse_models",
    "strip_json_comments",
    "validate_config_semantics",
    "validate_models_config",
]
