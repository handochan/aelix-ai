"""AgentProfile — declarative single-agent identity (ADR-0196).

AELIX-ORIGINAL: neither the kernel (``aelix-agent-core``) nor pi's product
core has an agent-profile concept. The nearest analogue is the skill loader
(``aelix_agent_core.harness.skills``), whose frontmatter → validate →
diagnostics shape this module deliberately mirrors so profiles and skills stay
one idiom; the frontmatter parser itself is shared
(``aelix_agent_core.harness._frontmatter.parse_frontmatter``).

Two deliberate divergences from ``skills.py``, both required:
  * ``ProfileDiagnostic.type`` admits ``"error"`` — a bad profile must be
    fatal, whereas ``SkillDiagnostic.type`` is ``Literal["warning"]`` only
    (``skills.py:80-87``). Running under an identity other than the one the
    user asked for is a safety problem, not a warning.
  * unknown frontmatter keys emit a WARNING (``skills.py`` ignores them
    silently) so P2+ fields (``deny_tools`` / ``mcp_servers`` / ``extends`` /
    ``output_schema``) are forward-compatible but visible.

One-way pi compat: a pi ``agents/<name>.md`` role loads here unchanged; an
aelix profile using ``extensions:``/``role:`` does not run on pi.

Scope note (the RCE cut): ``extensions:`` names ``.py`` files that
``extensions/loader.py:795-859`` ``exec_module``s **outside** both the
``no_discovery`` and the ``no_project_local`` guards, i.e. an explicit
extension path is ungated by Project Trust. A project-scoped profile is
therefore forbidden from declaring ``extensions:`` at all
(:data:`ProfileDiagnosticCode` ``"scope_forbidden"``); a checked-in
``.aelix/agents/x.md`` must never be able to widen the tier-3 grant.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from aelix_agent_core.harness._frontmatter import parse_frontmatter

from aelix_coding_agent.cli.args import VALID_THINKING_LEVELS

# Mirrors ``skills.py:43-47`` so a profile name and a skill name are the same
# shape of identifier. The regex ALONE is not enough: ``^[a-z0-9-]+$`` happily
# accepts ``--tools`` and ``-p``, which would be indistinguishable from a flag
# once a name reaches an argv (``resolver.profile_to_argv``). All four rules
# from ``skills.py:470-482`` are ported below in :func:`_validate_name`.
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_NAME_REGEX = re.compile(r"^[a-z0-9-]+$")

ProfileScope = Literal["bundled", "user", "project", "explicit"]
"""Where a profile came from, by RESOLVED-PATH CONTAINMENT — see
``discovery.classify_scope``. Never "how the user spelled it": ``--agent-file
.aelix/agents/x.md`` still classifies ``"project"``.

``"bundled"`` is the tier shipped INSIDE the wheel
(``discovery.builtin_agents_dir``). It exists because ``profile`` is a REQUIRED
parameter of the ``agent`` tool while a clean install discovered zero profiles,
so the tool's own description read "No agent profiles are available" and the
model had no legal value to pass — delegation was 0% usable out of the box.

It is deliberately NOT a security boundary: bundled files ship with the code, so
they are exactly as trusted as the code, and every gate in the tree tests the
literal ``"project"`` (``runtime.py:488``, ``consent.py:523``, ``posture.py:222``,
``profile.py``'s ``extensions:`` cut, ``discovery.py:357``). ``"bundled"`` falls
through all of them the same way ``"user"`` does, which is the intended reading."""

ProfileDiagnosticCode = Literal[
    "read_failed",
    "parse_failed",
    "invalid_metadata",
    "unknown_field",
    "missing_path",
    "scope_forbidden",
]
"""Mirrors ``SkillDiagnosticCode`` (``skills.py:50-56``) minus the two
directory-walk codes (profiles are a flat non-recursive glob) plus the three
aelix-original ones (``unknown_field`` / ``missing_path`` /
``scope_forbidden``)."""

_SYSTEM_PROMPT_MODES: tuple[str, ...] = ("append", "replace")
_ROLES: tuple[str, ...] = ("leaf", "orchestrator")
_APPROVAL_MODES: tuple[str, ...] = ("inherit", "ask", "auto", "deny")

_INHERIT = "inherit"
"""Spec §2.5 compares ``!= "inherit"`` — CASE-SENSITIVE. ``model: inherit`` is
the documented default and MUST normalize to :data:`None`: left as a literal
id it reaches ``resolve_model('inherit', None)`` → ``Model(id='inherit',
provider='', api='unknown')`` → the #98 unrunnable gate at
``cli/entry.py:2878-2895``."""

_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "model",
        "provider",
        "tools",
        "builtin_tools",
        "skills",
        "inherit_skills",
        "extensions",
        "inherit_extensions",
        "system_prompt",
        "context_files",
        "thinking",
        "role",
        "output_cap",
        "timeout_ms",
        "approval_mode",
    }
)


@dataclass(frozen=True)
class ProfileDiagnostic:
    """Mirrors ``SkillDiagnostic`` (``skills.py:80-87``).

    Divergence: ``type`` admits ``"error"`` and DEFAULTS to it. A profile is an
    identity — a half-applied one is worse than none — so every validation
    failure is fatal unless a rule deliberately downgrades it (``unknown_field``
    and ``missing_path`` at parse time; see the module docstring).
    """

    code: ProfileDiagnosticCode
    message: str
    path: str
    type: Literal["warning", "error"] = "error"


@dataclass(frozen=True)
class AgentProfile:
    """A parsed, validated ``agents/<name>.md``.

    Frozen: the overlay (``resolver.apply_profile_to_args``) writes to
    :class:`~aelix_coding_agent.cli.args.Args`, never back onto the profile,
    so one profile object can be rendered (``/agents show``) and applied
    without the two views drifting.
    """

    name: str
    """Required. Validated by all four ``skills.py:470-482`` rules."""

    description: str
    """Required, non-empty, ≤1024 chars (``skills.py:487-495``)."""

    body: str
    """Required, non-empty — THE SYSTEM PROMPT (replaced or appended per
    :attr:`system_prompt`)."""

    file_path: str
    """Absolute path to the ``.md``. Doubles as the anchor for relative
    :attr:`skills` / :attr:`extensions` entries and as the ``prompt_path``
    ``/agents show`` renders."""

    scope: ProfileScope
    """Effective scope by resolved-path containment (``discovery.classify_scope``)."""

    model: str | None = None
    """``"inherit"`` and an absent key BOTH normalize to :data:`None` = inherit
    the ambient model."""

    provider: str | None = None
    """``"inherit"`` and an absent key BOTH normalize to :data:`None`."""

    tools: tuple[str, ...] | None = None
    """THREE-valued, and the distinction is load-bearing:

    * :data:`None` — key absent → inherit the ambient tool set.
    * ``()`` — ``tools: []`` → **NO** tools; emits ``--no-tools``.
    * non-empty — an allowlist; emits one ``--tools a,b`` CSV token.

    ``()`` must NOT collapse onto ``--tools ''``: ``parse_args(['--tools',''])``
    yields ``tools == []``, which ``cli/entry.py:_resolve_active_tools``
    (``:446-465``) reads as falsy → :data:`None` → **every** tool active. The
    exact inversion of what the profile asked for."""

    builtin_tools: bool = True
    """``false`` emits ``--no-builtin-tools`` (extension/MCP tools survive)."""

    skills: tuple[str, ...] = ()
    """ABSOLUTE paths after parse. aelix's ``--skill`` takes a PATH, not a name
    (``cli/entry.py:921-962``) — unlike pi. Relative entries resolve against the
    PROFILE's own directory, never cwd, so a profile is portable across cwds."""

    inherit_skills: bool = True
    """``false`` emits ``--no-skills``. A boolean, NOT a ``skills: none``
    sentinel: ``yaml.safe_load("skills: none")`` yields the *string* ``'none'``,
    indistinguishable from a skill directory literally named ``none``.

    Deliberate asymmetry with :attr:`inherit_extensions` (default ``False``):
    skills are inert prompt text, extensions are code."""

    extensions: tuple[str, ...] = ()
    """ABSOLUTE paths after parse. Forbidden outright when
    :attr:`scope` is ``"project"`` — see the module docstring."""

    inherit_extensions: bool = False
    """Default ``False`` (spec §2.3) — extensions execute arbitrary code, so a
    profile does not silently inherit the ambient set. Emits
    ``--no-extensions``, which suppresses *discovery* only; explicit ``-e``
    paths still load (``cli/entry.py:1346-1348``)."""

    system_prompt: Literal["append", "replace"] = "append"
    """``append`` puts :attr:`body` FIRST among the appends (ahead of the user's
    own ``--append-system-prompt`` chunks); ``replace`` swaps the base prompt."""

    context_files: bool = True
    """``false`` emits ``--no-context-files`` (drops auto-discovered AGENTS.md)."""

    thinking: str | None = None
    """Validated against ``cli.args.VALID_THINKING_LEVELS``.

    DELIBERATE ASYMMETRY with the CLI flag: ``--thinking bogus`` warns and drops
    the value (``args.py:504-521``) because a typo must not abort a session
    already being launched; a profile is a checked-in file read before anything
    starts, so a bogus level is an ERROR here and the profile is rejected."""

    role: Literal["leaf", "orchestrator"] = "leaf"
    """READ by ``print_channel.build_child_env`` — a ``leaf`` child is spawned
    with delegation switched off, so it cannot start a third generation.

    NOTE: ``build_rpc_child_argv`` and ``build_child_argv`` both append
    ``--no-agents`` unconditionally today, so ``orchestrator`` does not yet buy
    a profile anything. Declaring it is not an error; it is simply not honoured
    (issue #123's track)."""

    output_cap: int = 51200
    """READ by ``envelope.build_result`` — the byte budget for the summary
    handed back to the parent, after which it is truncated with a visible
    marker."""

    timeout_ms: int | None = None
    """READ by both channels as the whole delegation's wall clock. The ``agent``
    tool additionally bounds a caller-supplied value to
    ``MIN_TIMEOUT_MS`` (1 000) … ``MAX_TIMEOUT_MS`` (1 800 000)."""

    approval_mode: Literal["inherit", "ask", "auto", "deny"] = "inherit"
    """READ by ``posture.child_permission_mode`` and by the consent gate: it is
    how a profile DECLARES that it needs write authority, which is what lets the
    spawn dialog offer to widen a ``plan`` clamp. It cannot grant that authority
    on its own — only a human answering the dialog can."""


@dataclass(frozen=True)
class ParseProfileResult:
    """Mirrors ``LoadSkillsResult`` (``skills.py:90-95``).

    :attr:`profile` is :data:`None` iff at least one diagnostic has
    ``type == "error"``.
    """

    profile: AgentProfile | None = None
    diagnostics: list[ProfileDiagnostic] = field(default_factory=list)


def parse_profile(
    content: str,
    *,
    file_path: str,
    scope: ProfileScope,
) -> ParseProfileResult:
    """Parse + validate one ``agents/<name>.md``.

    ``file_path`` must be ABSOLUTE: it anchors relative :attr:`AgentProfile.skills`
    / :attr:`AgentProfile.extensions` entries and is echoed in every diagnostic.
    ``scope`` is the EFFECTIVE scope (``discovery.classify_scope``), not the
    flag the user typed — the ``extensions:``/``project`` prohibition is only a
    real gate if it cannot be laundered by naming a project file with
    ``--agent-file``.

    Never raises. Callers that must be fatal (``discovery.resolve_profile``)
    escalate; callers that must not (``/agents list``) render the warnings.
    """

    diagnostics: list[ProfileDiagnostic] = []

    def _err(code: ProfileDiagnosticCode, message: str) -> None:
        diagnostics.append(
            ProfileDiagnostic(code=code, message=message, path=file_path)
        )

    def _warn(code: ProfileDiagnosticCode, message: str) -> None:
        diagnostics.append(
            ProfileDiagnostic(
                code=code, message=message, path=file_path, type="warning"
            )
        )

    frontmatter, body, parse_error = parse_frontmatter(content)
    if frontmatter is None:
        # Mirrors ``skills.py:393-406`` — surface YAML's own message.
        suffix = f": {parse_error}" if parse_error else ""
        _err("parse_failed", f"failed to parse YAML frontmatter{suffix}")
        return ParseProfileResult(diagnostics=diagnostics)

    # An UNCLOSED frontmatter block is the dangerous case, not merely an empty
    # one: ``parse_frontmatter`` returns ``({}, <the whole file>, None)`` for it
    # (``_frontmatter.py:45-47``), so without this check the profile's own YAML
    # would ship verbatim into the model's system prompt. It would also fail on
    # the missing ``name``, but with a message that points at the wrong thing.
    if not frontmatter and body.startswith("---"):
        _err(
            "parse_failed",
            "frontmatter block is not closed with a line containing only `---`",
        )
        return ParseProfileResult(diagnostics=diagnostics)

    for key in sorted(frontmatter):
        if key not in _KNOWN_KEYS:
            # Forward-compat: a P2+ field (deny_tools / mcp_servers / extends /
            # output_schema) must not brick a profile on an older aelix, but it
            # must not be invisible either.
            _warn("unknown_field", f"unknown frontmatter key {key!r} (ignored)")

    # === Identity ==========================================================
    raw_name = frontmatter.get("name")
    name = raw_name if isinstance(raw_name, str) else None
    if name is None or name.strip() == "":
        _err("invalid_metadata", "name is required")
        name = ""
    else:
        name = name.strip()
        for message in _validate_name(name):
            _err("invalid_metadata", message)

    raw_description = frontmatter.get("description")
    description = raw_description if isinstance(raw_description, str) else None
    if description is None or description.strip() == "":
        _err("invalid_metadata", "description is required")
        description = ""
    elif len(description) > _MAX_DESCRIPTION_LENGTH:
        _err(
            "invalid_metadata",
            f"description exceeds {_MAX_DESCRIPTION_LENGTH} characters "
            f"({len(description)})",
        )

    if body.strip() == "":
        _err("invalid_metadata", "body is required (it is the system prompt)")

    # === Model / provider ==================================================
    model = _parse_inheritable_str(frontmatter, "model", _err)
    provider = _parse_inheritable_str(frontmatter, "provider", _err)

    # === Tools =============================================================
    tools: tuple[str, ...] | None = None
    if "tools" in frontmatter and frontmatter["tools"] is not None:
        coerced = _coerce_str_list(frontmatter["tools"], "tools", _err)
        # ``coerced is None`` only when the value was a type error (already
        # diagnosed). ``()`` is a MEANINGFUL value here — see AgentProfile.tools.
        tools = coerced

    builtin_tools = _parse_bool(frontmatter, "builtin_tools", True, _err)
    inherit_skills = _parse_bool(frontmatter, "inherit_skills", True, _err)
    inherit_extensions = _parse_bool(frontmatter, "inherit_extensions", False, _err)
    context_files = _parse_bool(frontmatter, "context_files", True, _err)

    # === Path-valued lists =================================================
    profile_dir = Path(file_path).parent
    skills = _parse_path_list(frontmatter, "skills", profile_dir, _err, _warn)
    extensions = _parse_path_list(frontmatter, "extensions", profile_dir, _err, _warn)

    # THE RCE CUT. ``extensions/loader.py:795-859`` exec_module's explicit
    # extension paths OUTSIDE both the ``no_discovery`` and ``no_project_local``
    # guards, so a project-scoped profile declaring ``extensions:`` would be a
    # checked-in file that runs arbitrary code the Project Trust gate never sees.
    if extensions and scope == "project":
        _err(
            "scope_forbidden",
            "extensions: is not allowed in a project-scoped profile; move it to "
            "~/.aelix/agent/agents/ or pass -e explicitly.",
        )

    # === Enums + numbers ===================================================
    system_prompt = _parse_enum(
        frontmatter, "system_prompt", _SYSTEM_PROMPT_MODES, "append", _err
    )
    role = _parse_enum(frontmatter, "role", _ROLES, "leaf", _err)
    approval_mode = _parse_enum(
        frontmatter, "approval_mode", _APPROVAL_MODES, "inherit", _err
    )

    thinking: str | None = None
    raw_thinking = frontmatter.get("thinking")
    if raw_thinking is not None:
        if isinstance(raw_thinking, str) and raw_thinking in VALID_THINKING_LEVELS:
            thinking = raw_thinking
        else:
            _err(
                "invalid_metadata",
                f"thinking must be one of {', '.join(VALID_THINKING_LEVELS)} "
                f"(got {raw_thinking!r})",
            )

    output_cap = _parse_positive_int(frontmatter, "output_cap", 51200, _err)
    timeout_ms = _parse_positive_int(frontmatter, "timeout_ms", None, _err)

    if any(diag.type == "error" for diag in diagnostics):
        return ParseProfileResult(diagnostics=diagnostics)

    profile = AgentProfile(
        name=name,
        description=description.strip(),
        body=body,
        file_path=file_path,
        scope=scope,
        model=model,
        provider=provider,
        tools=tools,
        builtin_tools=builtin_tools,
        skills=skills,
        inherit_skills=inherit_skills,
        extensions=extensions,
        inherit_extensions=inherit_extensions,
        system_prompt=system_prompt,  # type: ignore[arg-type]
        context_files=context_files,
        thinking=thinking,
        role=role,  # type: ignore[arg-type]
        output_cap=output_cap if output_cap is not None else 51200,
        timeout_ms=timeout_ms,
        approval_mode=approval_mode,  # type: ignore[arg-type]
    )
    return ParseProfileResult(profile=profile, diagnostics=diagnostics)


# === Validation helpers =====================================================


def _validate_name(name: str) -> list[str]:
    """Port of ``skills.py:463-483`` minus the parent-directory rule.

    A profile's identity is its ``name`` FIELD, not its filename (a mismatch is
    a discovery-level warning), so the ``name != parent_dir_name`` clause does
    not apply. The other four rules DO, and all four are load-bearing: the regex
    alone accepts ``--tools`` and ``-p``, which would be indistinguishable from
    flags inside ``resolver.profile_to_argv``.
    """

    errors: list[str] = []
    if len(name) > _MAX_NAME_LENGTH:
        errors.append(f"name exceeds {_MAX_NAME_LENGTH} characters ({len(name)})")
    if not _NAME_REGEX.match(name):
        errors.append(
            "name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)"
        )
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def _parse_inheritable_str(
    frontmatter: dict[str, Any],
    key: str,
    err: Any,
) -> str | None:
    """``"inherit"`` (case-sensitive, spec §2.5) and absent both → :data:`None`."""

    raw = frontmatter.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        err("invalid_metadata", f"{key} must be a string (got {type(raw).__name__})")
        return None
    value = raw.strip()
    if value == "" or value == _INHERIT:
        return None
    return value


def _coerce_str_list(
    raw: Any,
    key: str,
    err: Any,
) -> tuple[str, ...] | None:
    """Accept a YAML list OR a CSV string; return :data:`None` on a type error.

    ``[]`` and ``""`` both yield ``()`` — an EMPTY tuple, which for ``tools`` is
    a meaningful "no tools" and must not be confused with an absent key.
    """

    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple)):
        items: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                err(
                    "invalid_metadata",
                    f"{key} entries must be strings (got {type(item).__name__})",
                )
                return None
            if item.strip():
                items.append(item.strip())
        return tuple(items)
    err(
        "invalid_metadata",
        f"{key} must be a list or a comma-separated string "
        f"(got {type(raw).__name__})",
    )
    return None


def _parse_path_list(
    frontmatter: dict[str, Any],
    key: str,
    profile_dir: Path,
    err: Any,
    warn: Any,
) -> tuple[str, ...]:
    """Resolve ``skills:`` / ``extensions:`` entries to ABSOLUTE paths.

    aelix's ``--skill`` and ``--extension``/``-e`` take PATHS, not names
    (``cli/entry.py:921-962``, ``args.py``) — a divergence from pi that the spec
    originally got wrong. Relative entries resolve against ``profile_dir``, NOT
    cwd: a profile must mean the same thing from any working directory, and an
    absolute result round-trips unchanged through ``_resolve_skill_dirs``'
    cwd-relative logic (``entry.py:950-956``).

    A non-existent path is a WARNING here — ``/agents list`` must never break —
    and ``discovery.resolve_profile`` escalates it to fatal, because running
    under an identity whose skills silently vanished is the same class of
    problem as running the wrong identity.
    """

    raw = frontmatter.get(key)
    if raw is None:
        return ()
    entries = _coerce_str_list(raw, key, err)
    if entries is None:
        return ()

    resolved: list[str] = []
    for entry in entries:
        path = Path(os.path.expanduser(entry))
        if not path.is_absolute():
            path = profile_dir / path
        text = str(path)
        if not path.exists():
            warn("missing_path", f"{key} path does not exist: {text}")
        resolved.append(text)
    return tuple(resolved)


def _parse_bool(
    frontmatter: dict[str, Any],
    key: str,
    default: bool,
    err: Any,
) -> bool:
    raw = frontmatter.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    err("invalid_metadata", f"{key} must be a boolean (got {raw!r})")
    return default


def _parse_enum(
    frontmatter: dict[str, Any],
    key: str,
    allowed: tuple[str, ...],
    default: str,
    err: Any,
) -> str:
    raw = frontmatter.get(key)
    if raw is None:
        return default
    if isinstance(raw, str) and raw in allowed:
        return raw
    err(
        "invalid_metadata",
        f"{key} must be one of {', '.join(allowed)} (got {raw!r})",
    )
    return default


def _parse_positive_int(
    frontmatter: dict[str, Any],
    key: str,
    default: int | None,
    err: Any,
) -> int | None:
    raw = frontmatter.get(key)
    if raw is None:
        return default
    # ``bool`` is an ``int`` subclass — ``output_cap: true`` must not become 1.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        err("invalid_metadata", f"{key} must be a positive integer (got {raw!r})")
        return default
    return raw


__all__ = [
    "AgentProfile",
    "ParseProfileResult",
    "ProfileDiagnostic",
    "ProfileDiagnosticCode",
    "ProfileScope",
    "parse_profile",
]
