"""Pi parity: ``coding-agent/src/core/model-resolver.ts`` (SHA 734e08e).

Model resolution, scoping, and initial selection (Sprint 6g₁ / ADR-0067,
P-198/P-201/P-202). 7 public functions + 3 helpers ported verbatim from
Pi ``model-resolver.ts:1-637``.

External-dep translation:

- Pi ``minimatch`` → :func:`fnmatch.fnmatchcase` with explicit
  ``.casefold()`` for cross-platform case-insensitive parity.
- Pi ``chalk.yellow`` / ``chalk.red`` / ``chalk.dim`` → plain text via
  :data:`sys.stderr` / :data:`sys.stdout` (Sprint 6h or Phase 5 TUI wires
  the colored variant).
- Pi ``process.exit(1)`` (in :func:`find_initial_model` CLI failure
  branch) → :func:`sys.exit`.
- Pi ``isValidThinkingLevel`` →
  :func:`aelix_coding_agent.core.defaults.is_valid_thinking_level`.
- Pi ``DEFAULT_THINKING_LEVEL`` →
  :data:`aelix_coding_agent.core.defaults.DEFAULT_THINKING_LEVEL`.
- Pi ``ModelRegistry`` →
  :class:`aelix_coding_agent.model_registry.ModelRegistry`.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aelix_ai.models import models_are_equal
from aelix_ai.streaming import Model

from aelix_coding_agent.core.defaults import (
    DEFAULT_THINKING_LEVEL,
    is_valid_thinking_level,
)

if TYPE_CHECKING:
    from aelix_coding_agent.model_registry import ModelRegistry


# Pi parity: ``model-resolver.ts:14-47`` — default model id per known
# provider. Model-catalog refresh (#15) grows the map from 32 to 35 by
# adding the three new OpenAI-compatible providers' Pi defaults verbatim:
# ``ant-ling`` → ``Ring-2.6-1T``, ``nvidia`` →
# ``nvidia/nemotron-3-super-120b-a12b``, ``zai-coding-cn`` → ``glm-5.1``.
# Existing entries are kept at their pinned values (Pi's own version bumps
# of e.g. anthropic/openai defaults are a separate item).
DEFAULT_MODEL_PER_PROVIDER: dict[str, str] = {
    "amazon-bedrock": "us.anthropic.claude-opus-4-6-v1",
    "ant-ling": "Ring-2.6-1T",
    "anthropic": "claude-opus-4-7",
    "openai": "gpt-5.4",
    "azure-openai-responses": "gpt-5.4",
    "openai-codex": "gpt-5.5",
    "nvidia": "nvidia/nemotron-3-super-120b-a12b",
    "deepseek": "deepseek-v4-pro",
    "google": "gemini-3.1-pro-preview",
    "google-vertex": "gemini-3.1-pro-preview",
    "github-copilot": "gpt-5.4",
    "openrouter": "moonshotai/kimi-k2.6",
    "vercel-ai-gateway": "zai/glm-5.1",
    "xai": "grok-4.20-0309-reasoning",
    "groq": "openai/gpt-oss-120b",
    "cerebras": "zai-glm-4.7",
    "zai": "glm-5.1",
    "zai-coding-cn": "glm-5.1",
    "mistral": "devstral-medium-latest",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "moonshotai": "kimi-k2.6",
    "moonshotai-cn": "kimi-k2.6",
    "huggingface": "moonshotai/Kimi-K2.6",
    "fireworks": "accounts/fireworks/models/kimi-k2p6",
    "together": "moonshotai/Kimi-K2.6",
    "opencode": "kimi-k2.6",
    "opencode-go": "kimi-k2.6",
    "kimi-coding": "kimi-for-coding",
    "cloudflare-workers-ai": "@cf/moonshotai/kimi-k2.6",
    "cloudflare-ai-gateway": "workers-ai/@cf/moonshotai/kimi-k2.6",
    "xiaomi": "mimo-v2.5-pro",
    "xiaomi-token-plan-cn": "mimo-v2.5-pro",
    "xiaomi-token-plan-ams": "mimo-v2.5-pro",
    "xiaomi-token-plan-sgp": "mimo-v2.5-pro",
}


@dataclass(frozen=True)
class ScopedModel:
    """Pi parity: ``model-resolver.ts:49-53`` ``ScopedModel``."""

    model: Model
    # Pi camelCase ``thinkingLevel`` — only set when an explicit ":level"
    # appears in the pattern.
    thinking_level: str | None = None


@dataclass(frozen=True)
class ParsedModelResult:
    """Pi parity: ``model-resolver.ts:153-158`` ``ParsedModelResult``."""

    model: Model | None = None
    thinking_level: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class ResolveCliModelResult:
    """Pi parity: ``model-resolver.ts:315-324`` ``ResolveCliModelResult``."""

    model: Model | None = None
    thinking_level: str | None = None
    warning: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class InitialModelResult:
    """Pi parity: ``model-resolver.ts:469-473`` ``InitialModelResult``."""

    model: Model | None = None
    thinking_level: str = DEFAULT_THINKING_LEVEL
    fallback_message: str | None = None


@dataclass(frozen=True)
class RestoreModelResult:
    """Pi parity: ``model-resolver.ts:574,599,629,636`` return shape.

    Sprint 6g₂ W6 P-206 MAJOR fix: typed dataclass mirrors the other
    four resolver return shapes (``ParsedModelResult`` /
    ``ResolveCliModelResult`` / ``InitialModelResult`` /
    ``ScopedModel``). The earlier Sprint 6g₁ port returned a
    ``dict[str, Model | str | None]`` from
    :func:`restore_model_from_session`, which forced every caller to
    use string-keyed access + ``cast`` for the fallback message.
    """

    model: Model | None = None
    fallback_message: str | None = None


# Pi parity: ``model-resolver.ts:64`` — dated YYYYMMDD detection.
_DATE_SUFFIX_PATTERN: re.Pattern[str] = re.compile(r"-\d{8}$")


def _glob_match_pi_minimatch(haystack: str, pattern: str) -> bool:
    """Pi parity: ``minimatch(haystack, pattern, {nocase: true})``.

    Sprint 6g₂ W6 P-207 MAJOR fix: Pi ``minimatch`` does NOT let ``*``
    cross the ``/`` segment separator, while Python
    :func:`fnmatch.fnmatchcase` happily matches across slashes. The
    earlier Sprint 6g₁ port called :func:`fnmatch.fnmatchcase`
    directly, so a pattern like ``openai/*`` would have matched
    ``openrouter/openai/gpt-5`` (Pi rejects it).

    Implementation: split ``haystack`` and ``pattern`` by ``/`` and
    match per-segment with :func:`fnmatch.fnmatchcase` (case-folded);
    require identical segment counts.

    See ``model-resolver.ts:266-272`` — Pi tests both
    ``provider/id`` AND bare ``id`` against the same pattern, so a
    bare-id pattern like ``*sonnet*`` still resolves
    ``anthropic/claude-sonnet-4-5`` via the second probe (1 segment vs
    1 segment) — the union-of-both behavior in :func:`resolve_model_scope`
    is preserved.
    """

    haystack_segments = haystack.casefold().split("/")
    pattern_segments = pattern.casefold().split("/")
    if len(haystack_segments) != len(pattern_segments):
        return False
    return all(
        fnmatch.fnmatchcase(h, p)
        for h, p in zip(haystack_segments, pattern_segments, strict=False)
    )


def _is_alias(model_id: str) -> bool:
    """Pi parity: ``model-resolver.ts:59-66`` ``isAlias``.

    Returns ``True`` iff ``model_id`` ends with ``-latest`` OR does NOT
    match ``/-\\d{8}$/``.
    """

    if model_id.endswith("-latest"):
        return True
    return not bool(_DATE_SUFFIX_PATTERN.search(model_id))


def find_exact_model_reference_match(
    model_reference: str, available_models: list[Model],
) -> Model | None:
    """Pi parity: ``model-resolver.ts:73-115`` ``findExactModelReferenceMatch``.

    Supports either a bare model id or a canonical ``provider/modelId``
    reference. Ambiguous matches across providers return :data:`None`.
    """

    trimmed_reference = model_reference.strip()
    if not trimmed_reference:
        return None

    normalized_reference = trimmed_reference.lower()

    canonical_matches = [
        m for m in available_models
        if f"{m.provider}/{m.id}".lower() == normalized_reference
    ]
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        return None

    slash_index = trimmed_reference.find("/")
    if slash_index != -1:
        provider = trimmed_reference[:slash_index].strip()
        model_id = trimmed_reference[slash_index + 1:].strip()
        if provider and model_id:
            provider_matches = [
                m for m in available_models
                if m.provider.lower() == provider.lower()
                and m.id.lower() == model_id.lower()
            ]
            if len(provider_matches) == 1:
                return provider_matches[0]
            if len(provider_matches) > 1:
                return None

    id_matches = [
        m for m in available_models if m.id.lower() == normalized_reference
    ]
    return id_matches[0] if len(id_matches) == 1 else None


def _try_match_model(
    model_pattern: str, available_models: list[Model],
) -> Model | None:
    """Pi parity: ``model-resolver.ts:121-151`` ``tryMatchModel`` (internal).

    Exact match first, then case-insensitive partial id/name match, then
    alias-prefer / latest-dated descending sort.
    """

    exact_match = find_exact_model_reference_match(
        model_pattern, available_models
    )
    if exact_match is not None:
        return exact_match

    # No exact match — fall back to partial matching.
    pattern_lower = model_pattern.lower()
    matches = [
        m for m in available_models
        if pattern_lower in m.id.lower()
        or (m.name is not None and pattern_lower in m.name.lower())
    ]

    if not matches:
        return None

    # Separate into aliases and dated versions.
    aliases = [m for m in matches if _is_alias(m.id)]
    dated_versions = [m for m in matches if not _is_alias(m.id)]

    if aliases:
        # Prefer alias — if multiple aliases, pick the one that sorts highest.
        aliases.sort(key=lambda m: m.id, reverse=True)
        return aliases[0]
    # No alias found, pick latest dated version.
    dated_versions.sort(key=lambda m: m.id, reverse=True)
    return dated_versions[0]


def _build_fallback_model(
    provider: str, model_id: str, available_models: list[Model],
) -> Model | None:
    """Pi parity: ``model-resolver.ts:160-174`` ``buildFallbackModel``.

    Spreads (`dataclasses.replace`) the provider's default-or-first model
    with overridden ``id``/``name``. Used when CLI ``--provider`` was given
    but no model in the catalog matches the requested id — Pi treats this
    as "user wants a custom model id under this provider's API."
    """

    provider_models = [m for m in available_models if m.provider == provider]
    if not provider_models:
        return None

    default_id = DEFAULT_MODEL_PER_PROVIDER.get(provider)
    if default_id:
        base_model = next(
            (m for m in provider_models if m.id == default_id),
            provider_models[0],
        )
    else:
        base_model = provider_models[0]

    return dataclasses.replace(base_model, id=model_id, name=model_id)


def parse_model_pattern(
    pattern: str,
    available_models: list[Model],
    *,
    allow_invalid_thinking_level_fallback: bool = True,
) -> ParsedModelResult:
    """Pi parity: ``model-resolver.ts:189-242`` ``parseModelPattern``.

    Recursive pattern parser. Tries full pattern; if no match, splits on
    the LAST ``:`` and either recurses (valid thinking-level suffix) or
    fails / warns (invalid suffix) per ``allow_invalid_thinking_level_fallback``.

    Supports models with colons in their IDs (e.g., OpenRouter's
    ``model:exacto`` suffix).
    """

    # Try exact match first.
    exact_match = _try_match_model(pattern, available_models)
    if exact_match is not None:
        return ParsedModelResult(
            model=exact_match, thinking_level=None, warning=None
        )

    # No match — try splitting on last colon if present.
    last_colon_index = pattern.rfind(":")
    if last_colon_index == -1:
        # No colons, pattern simply doesn't match any model.
        return ParsedModelResult(model=None, thinking_level=None, warning=None)

    prefix = pattern[:last_colon_index]
    suffix = pattern[last_colon_index + 1:]

    if is_valid_thinking_level(suffix):
        # Valid thinking level — recurse on prefix and use this level.
        result = parse_model_pattern(
            prefix,
            available_models,
            allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
        )
        if result.model is not None:
            # Only use this thinking level if no warning from inner recursion.
            return ParsedModelResult(
                model=result.model,
                thinking_level=None if result.warning else suffix,
                warning=result.warning,
            )
        return result

    # Invalid suffix.
    if not allow_invalid_thinking_level_fallback:
        # Strict mode (CLI --model parsing) — treat suffix as part of the
        # model id and fail. Avoids accidentally resolving to a different
        # model.
        return ParsedModelResult(model=None, thinking_level=None, warning=None)

    # Scope mode: recurse on prefix and warn.
    result = parse_model_pattern(
        prefix,
        available_models,
        allow_invalid_thinking_level_fallback=allow_invalid_thinking_level_fallback,
    )
    if result.model is not None:
        return ParsedModelResult(
            model=result.model,
            thinking_level=None,
            warning=(
                f'Invalid thinking level "{suffix}" in pattern "{pattern}". '
                f"Using default instead."
            ),
        )
    return result


async def resolve_model_scope(
    patterns: list[str], model_registry: ModelRegistry,
) -> list[ScopedModel]:
    """Pi parity: ``model-resolver.ts:255-313`` ``resolveModelScope``.

    Resolve model patterns to actual :class:`Model` objects with optional
    thinking levels. Format: ``"pattern:level"`` where ``:level`` is
    optional. For each pattern, finds matching models and picks the best
    version (alias preferred over dated, otherwise latest-dated).

    Supports glob characters ``*``, ``?``, ``[`` via :func:`fnmatch.fnmatchcase`
    (case-insensitive via ``.casefold()``) against both ``provider/id`` and
    bare ``id``.
    """

    # Pi ``modelRegistry.getAvailable()`` is sync in Aelix; await for
    # forward-compat with any async override.
    available_models = model_registry.get_available()
    scoped_models: list[ScopedModel] = []

    for pattern in patterns:
        # Check if pattern contains glob characters.
        if "*" in pattern or "?" in pattern or "[" in pattern:
            # Extract optional thinking level suffix (e.g., "provider/*:high").
            colon_idx = pattern.rfind(":")
            glob_pattern = pattern
            thinking_level: str | None = None

            if colon_idx != -1:
                suffix = pattern[colon_idx + 1:]
                if is_valid_thinking_level(suffix):
                    thinking_level = suffix
                    glob_pattern = pattern[:colon_idx]

            # Match against "provider/modelId" format OR just model ID.
            # Pi parity: ``minimatch(haystack, glob, {nocase: true})`` —
            # `*` does NOT cross `/`. See :func:`_glob_match_pi_minimatch`
            # (Sprint 6g₂ W6 P-207 fix).
            matching_models: list[Model] = []
            for m in available_models:
                full_id = f"{m.provider}/{m.id}"
                if _glob_match_pi_minimatch(
                    full_id, glob_pattern
                ) or _glob_match_pi_minimatch(m.id, glob_pattern):
                    matching_models.append(m)

            if not matching_models:
                sys.stderr.write(
                    f'Warning: No models match pattern "{pattern}"\n'
                )
                continue

            for model in matching_models:
                if not any(
                    models_are_equal(sm.model, model) for sm in scoped_models
                ):
                    scoped_models.append(
                        ScopedModel(model=model, thinking_level=thinking_level)
                    )
            continue

        result = parse_model_pattern(pattern, available_models)

        if result.warning:
            sys.stderr.write(f"Warning: {result.warning}\n")

        if result.model is None:
            sys.stderr.write(
                f'Warning: No models match pattern "{pattern}"\n'
            )
            continue

        # Avoid duplicates.
        if not any(
            models_are_equal(sm.model, result.model) for sm in scoped_models
        ):
            scoped_models.append(
                ScopedModel(
                    model=result.model, thinking_level=result.thinking_level
                )
            )

    return scoped_models


def resolve_cli_model(
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    model_registry: ModelRegistry,
) -> ResolveCliModelResult:
    """Pi parity: ``model-resolver.ts:337-467`` ``resolveCliModel``.

    Resolve a single model from CLI flags. Supports:

    - ``--provider <provider> --model <pattern>``
    - ``--model <provider>/<pattern>``
    - Fuzzy matching (same rules as :func:`resolve_model_scope`).

    Does not apply the thinking level by itself, but may *parse* and
    return one from ``"<pattern>:<thinking>"`` so the caller can apply it.
    """

    if not cli_model:
        return ResolveCliModelResult(
            model=None, warning=None, error=None
        )

    # Important: use *all* models here, not just models with pre-configured
    # auth. This allows ``--api-key`` to be used for first-time setup.
    available_models = model_registry.get_all()
    if not available_models:
        return ResolveCliModelResult(
            model=None,
            warning=None,
            error=(
                "No models available. Check your installation or add models "
                "to models.json."
            ),
        )

    # Build canonical provider lookup (case-insensitive).
    provider_map: dict[str, str] = {}
    for m in available_models:
        provider_map[m.provider.lower()] = m.provider

    provider: str | None = (
        provider_map.get(cli_provider.lower()) if cli_provider else None
    )
    if cli_provider and not provider:
        return ResolveCliModelResult(
            model=None,
            warning=None,
            error=(
                f'Unknown provider "{cli_provider}". '
                f"Use --list-models to see available providers/models."
            ),
        )

    # If no explicit --provider, try to interpret "provider/model" format
    # first. When the prefix before the first slash matches a known provider,
    # prefer that interpretation over matching models whose IDs literally
    # contain slashes (e.g. "zai/glm-5" should resolve to provider=zai,
    # model=glm-5, not to a vercel-ai-gateway model with id "zai/glm-5").
    pattern = cli_model
    inferred_provider = False

    if not provider:
        slash_index = cli_model.find("/")
        if slash_index != -1:
            maybe_provider = cli_model[:slash_index]
            canonical = provider_map.get(maybe_provider.lower())
            if canonical:
                provider = canonical
                pattern = cli_model[slash_index + 1:]
                inferred_provider = True

    # If no provider was inferred from the slash, try exact matches without
    # provider inference. This handles models whose IDs naturally contain
    # slashes (e.g. OpenRouter-style IDs).
    if not provider:
        lower = cli_model.lower()
        exact = next(
            (
                m for m in available_models
                if m.id.lower() == lower
                or f"{m.provider}/{m.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(
                model=exact,
                warning=None,
                thinking_level=None,
                error=None,
            )

    if cli_provider and provider:
        # If both were provided, tolerate --model <provider>/<pattern> by
        # stripping the provider prefix.
        prefix = f"{provider}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix):]

    candidates = (
        [m for m in available_models if m.provider == provider]
        if provider
        else available_models
    )
    parsed = parse_model_pattern(
        pattern, candidates, allow_invalid_thinking_level_fallback=False
    )

    if parsed.model is not None:
        return ResolveCliModelResult(
            model=parsed.model,
            thinking_level=parsed.thinking_level,
            warning=parsed.warning,
            error=None,
        )

    # If we inferred a provider from the slash but found no match within
    # that provider, fall back to matching the full input as a raw model id
    # across all models. This handles OpenRouter-style IDs like
    # "openai/gpt-4o:extended" where "openai" looks like a provider but the
    # full string is actually a model id on openrouter.
    if inferred_provider:
        lower = cli_model.lower()
        exact = next(
            (
                m for m in available_models
                if m.id.lower() == lower
                or f"{m.provider}/{m.id}".lower() == lower
            ),
            None,
        )
        if exact is not None:
            return ResolveCliModelResult(
                model=exact,
                warning=None,
                thinking_level=None,
                error=None,
            )
        # Also try parseModelPattern on the full input against all models.
        fallback = parse_model_pattern(
            cli_model,
            available_models,
            allow_invalid_thinking_level_fallback=False,
        )
        if fallback.model is not None:
            return ResolveCliModelResult(
                model=fallback.model,
                thinking_level=fallback.thinking_level,
                warning=fallback.warning,
                error=None,
            )

    if provider:
        fallback_model = _build_fallback_model(
            provider, pattern, available_models
        )
        if fallback_model is not None:
            fallback_warning = (
                f'{parsed.warning} Model "{pattern}" not found for provider '
                f'"{provider}". Using custom model id.'
                if parsed.warning
                else (
                    f'Model "{pattern}" not found for provider "{provider}". '
                    f"Using custom model id."
                )
            )
            return ResolveCliModelResult(
                model=fallback_model,
                thinking_level=None,
                warning=fallback_warning,
                error=None,
            )

    display = f"{provider}/{pattern}" if provider else cli_model
    return ResolveCliModelResult(
        model=None,
        thinking_level=None,
        warning=parsed.warning,
        error=(
            f'Model "{display}" not found. Use --list-models to see '
            f"available models."
        ),
    )


async def find_initial_model(
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    scoped_models: list[ScopedModel] | None = None,
    is_continuing: bool = False,
    default_provider: str | None = None,
    default_model_id: str | None = None,
    default_thinking_level: str | None = None,
    model_registry: ModelRegistry,
) -> InitialModelResult:
    """Pi parity: ``model-resolver.ts:483-563`` ``findInitialModel``.

    Find the initial model to use based on priority:

    1. CLI args (provider + model) via :func:`resolve_cli_model`.
    2. First model from ``scoped_models`` (skip if ``is_continuing``).
    3. Saved default from settings (``default_provider`` + ``default_model_id``).
    4. First available model matching :data:`DEFAULT_MODEL_PER_PROVIDER`.
    5. First available model from :meth:`ModelRegistry.get_available`.
    """

    scoped = list(scoped_models) if scoped_models is not None else []

    model: Model | None = None
    thinking_level: str = DEFAULT_THINKING_LEVEL

    # 1. CLI args take priority.
    if cli_provider and cli_model:
        resolved = resolve_cli_model(
            cli_provider=cli_provider,
            cli_model=cli_model,
            model_registry=model_registry,
        )
        if resolved.error:
            sys.stderr.write(f"{resolved.error}\n")
            sys.exit(1)
        if resolved.model is not None:
            return InitialModelResult(
                model=resolved.model,
                thinking_level=DEFAULT_THINKING_LEVEL,
                fallback_message=None,
            )

    # 2. Use first model from scoped models (skip if continuing/resuming).
    if scoped and not is_continuing:
        first = scoped[0]
        return InitialModelResult(
            model=first.model,
            thinking_level=(
                first.thinking_level
                or default_thinking_level
                or DEFAULT_THINKING_LEVEL
            ),
            fallback_message=None,
        )

    # 3. Try saved default from settings.
    if default_provider and default_model_id:
        found = model_registry.find(default_provider, default_model_id)
        if found is not None:
            model = found
            if default_thinking_level:
                thinking_level = default_thinking_level
            return InitialModelResult(
                model=model,
                thinking_level=thinking_level,
                fallback_message=None,
            )

    # 4. Try first available model with valid API key.
    available_models = model_registry.get_available()

    if available_models:
        # Try to find a default model from known providers.
        for known_provider in DEFAULT_MODEL_PER_PROVIDER:
            default_id = DEFAULT_MODEL_PER_PROVIDER[known_provider]
            match = next(
                (
                    m for m in available_models
                    if m.provider == known_provider and m.id == default_id
                ),
                None,
            )
            if match is not None:
                return InitialModelResult(
                    model=match,
                    thinking_level=DEFAULT_THINKING_LEVEL,
                    fallback_message=None,
                )

        # If no default found, use first available.
        return InitialModelResult(
            model=available_models[0],
            thinking_level=DEFAULT_THINKING_LEVEL,
            fallback_message=None,
        )

    # 5. No model found.
    return InitialModelResult(
        model=None,
        thinking_level=DEFAULT_THINKING_LEVEL,
        fallback_message=None,
    )


async def restore_model_from_session(
    saved_provider: str,
    saved_model_id: str,
    current_model: Model | None,
    should_print_messages: bool,
    model_registry: ModelRegistry,
) -> RestoreModelResult:
    """Pi parity: ``model-resolver.ts:568-637`` ``restoreModelFromSession``.

    Restore model from session, with fallback to available models.

    Returns a :class:`RestoreModelResult` mirroring Pi's
    ``{model, fallbackMessage}`` object literal (W6 P-206 fix —
    previously a dict).
    """

    restored_model = model_registry.find(saved_provider, saved_model_id)

    # Check if restored model exists and still has auth configured.
    has_configured_auth = (
        model_registry.has_configured_auth(restored_model)
        if restored_model is not None
        else False
    )

    if restored_model is not None and has_configured_auth:
        if should_print_messages:
            sys.stdout.write(
                f"Restored model: {saved_provider}/{saved_model_id}\n"
            )
        return RestoreModelResult(model=restored_model, fallback_message=None)

    # Model not found or no API key — fall back.
    reason = "model no longer exists" if restored_model is None else "no auth configured"

    if should_print_messages:
        sys.stderr.write(
            f"Warning: Could not restore model "
            f"{saved_provider}/{saved_model_id} ({reason}).\n"
        )

    # If we already have a model, use it as fallback.
    if current_model is not None:
        if should_print_messages:
            sys.stdout.write(
                f"Falling back to: {current_model.provider}/{current_model.id}\n"
            )
        return RestoreModelResult(
            model=current_model,
            fallback_message=(
                f"Could not restore model {saved_provider}/{saved_model_id} "
                f"({reason}). Using {current_model.provider}/{current_model.id}."
            ),
        )

    # Try to find any available model.
    available_models = model_registry.get_available()

    if available_models:
        # Try to find a default model from known providers.
        fallback_model: Model | None = None
        for known_provider in DEFAULT_MODEL_PER_PROVIDER:
            default_id = DEFAULT_MODEL_PER_PROVIDER[known_provider]
            match = next(
                (
                    m for m in available_models
                    if m.provider == known_provider and m.id == default_id
                ),
                None,
            )
            if match is not None:
                fallback_model = match
                break

        # If no default found, use first available.
        if fallback_model is None:
            fallback_model = available_models[0]

        if should_print_messages:
            sys.stdout.write(
                f"Falling back to: "
                f"{fallback_model.provider}/{fallback_model.id}\n"
            )

        return RestoreModelResult(
            model=fallback_model,
            fallback_message=(
                f"Could not restore model {saved_provider}/{saved_model_id} "
                f"({reason}). "
                f"Using {fallback_model.provider}/{fallback_model.id}."
            ),
        )

    # No models available.
    return RestoreModelResult(model=None, fallback_message=None)


__all__ = [
    "DEFAULT_MODEL_PER_PROVIDER",
    "InitialModelResult",
    "ParsedModelResult",
    "ResolveCliModelResult",
    "RestoreModelResult",
    "ScopedModel",
    "find_exact_model_reference_match",
    "find_initial_model",
    "parse_model_pattern",
    "resolve_cli_model",
    "resolve_model_scope",
    "restore_model_from_session",
]
