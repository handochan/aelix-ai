"""Agent-profile discovery + resolution (ADR-0196).

AELIX-ORIGINAL. Three directories are scanned, non-recursively, for
``<name>.md``, in ASCENDING order of precedence:

* BUNDLED — ``<this package>/agents/builtin``, always. Ships in the wheel.
* USER   — ``<get_agent_dir()>/agents`` (``cli/config.py:82``), always.
* PROJECT — ``<cwd>/.aelix/agents``, **only when the project is trusted**.

The bundled tier is an ON-RAMP, not a security tier. ``profile`` is a REQUIRED
parameter of the ``agent`` tool, so with no profiles on disk there was no legal
value to pass and the tool's own description said as much — a default install
could not delegate at all. Bundled files ship with the code and are therefore
exactly as trusted as the code; they lose every name collision to the two
writable tiers, so shipping one takes nothing away from a user who wants their
own ``explorer``.

The project tier is gated because a profile is an identity: it can replace the
system prompt, swap the model, and (outside project scope) name extensions.
``cli/project_trust.py:174-199`` learns about ``.aelix/agents/`` in the same
change that adds this module — without that, ``project_trusted`` here is
decorative (an agents-only ``.aelix`` resolves trusted with no prompt in every
mode).

Two rules exist purely to close escalation paths:

* **Effective scope by containment** (:func:`classify_scope`). A path under
  ``cwd`` is ``"project"`` no matter how the user named it, so
  ``--agent-file .aelix/agents/x.md`` cannot launder a project profile past the
  confirmation prompt or the ``extensions:`` prohibition. Containment is the
  UNION of where the path points and where it lives — a symlink is a path the
  PROJECT can ship, so deciding on the target alone let the repo pick its own
  scope.
* **Project wins a name collision** (spec §2.2, ratified) — but the collision
  emits a WARNING naming BOTH absolute paths, and ``cli/entry.py`` fires the
  confirmation prompt. Project-wins WITHOUT the warning + prompt is the
  escalation primitive, not the feature; the halves ship together.

:func:`resolve_profile` is the fatal path (``--agent`` / ``--agent-file``):
running under an identity other than the one requested is a safety problem, so
it raises :class:`ProfileError` on anything the render path would merely warn
about — including a ``missing_path`` warning for a ``skills:`` entry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from aelix_coding_agent.cli.config import CONFIG_DIR_NAME, get_agent_dir

from .profile import (
    AgentProfile,
    ParseProfileResult,
    ProfileDiagnostic,
    ProfileScope,
    parse_profile,
)


class ProfileError(Exception):
    """A requested profile could not be resolved. Always fatal at the caller.

    Carries an ACTIONABLE message (the discovered names, or ``--approve``, or
    the offending path) because the only useful response to a broken ``--agent``
    is to fix the profile or pick another one.
    """


@dataclass(frozen=True)
class ProfileDiscoveryResult:
    """Mirrors ``LoadSkillsResult`` (``skills.py:90-95``).

    ``diagnostics`` includes the warnings of profiles that DID load (unknown
    keys, missing skill paths, name/filename mismatch, collisions) as well as
    the errors of ones that did not, so ``/agents list`` can render both.
    """

    profiles: list[AgentProfile] = field(default_factory=list)
    diagnostics: list[ProfileDiagnostic] = field(default_factory=list)


def builtin_agents_dir() -> Path:
    """``<this package>/agents/builtin`` — the tier shipped in the wheel.

    Anchored on ``__file__`` and NOT on ``get_agent_dir()``: this directory is
    part of the installed code, so it moves with the package and is not
    something a user config can re-point.

    It is scanned FIRST and therefore loses every name collision to the user and
    project tiers (:func:`discover_profiles`), which is the property that keeps
    a bundled profile a starting point rather than something a user cannot
    escape: writing ``~/.aelix/agent/agents/explorer.md`` replaces ours outright.
    """

    return Path(__file__).resolve().parent / "builtin"


def user_agents_dir(agent_dir: str | None = None) -> Path:
    """``<agent_dir or get_agent_dir()>/agents`` (``cli/config.py:82``).

    ``agent_dir`` is the agent ROOT (``~/.aelix/agent``), not the agents dir —
    same convention as ``_resolve_skill_dirs`` (``entry.py:959``).
    """

    return Path(agent_dir or get_agent_dir()) / "agents"


def project_agents_dir(cwd: str | Path) -> Path:
    """``<cwd>/.aelix/agents`` — the trust-gated tier."""

    return Path(cwd) / CONFIG_DIR_NAME / "agents"


def classify_scope(
    resolved: Path,
    *,
    cwd: Path,
    agent_dir: Path,
    spelled: Path | None = None,
) -> ProfileScope:
    """EFFECTIVE scope by CONTAINMENT, not by how it was named.

    ``"project"`` is the UNION of two containment tests, and both halves are
    load-bearing:

    * where the path POINTS (``Path.resolve()``) — this is what stops
      ``--agent-file .aelix/agents/../../../etc/x.md`` from claiming a scope by
      spelling alone;
    * where the path LIVES (``spelled``, normalised lexically — ``..`` collapsed,
      symlinks NOT followed) — this is what stops the reverse trick. A symlink
      the PROJECT ships at ``.aelix/agents/helper.md`` pointing anywhere outside
      ``cwd`` used to classify by its target, i.e. ``"explicit"``, and an
      untrusted repo could then land an identity file past ALL THREE gates in one
      ``aelix --agent-file .aelix/agents/helper.md``: :func:`resolve_profile`'s
      ``project_trusted`` check, ``cli/entry.py``'s per-identity confirmation, and
      ``profile.parse_profile``'s ``extensions:``-at-project-scope prohibition —
      the last of which is the RCE cut (tier-3 explicit extension paths are
      ungated by BOTH ``--no-discovery`` and ``--no-project-local``,
      ``extensions/loader.py:795-859``). Proven end to end before the fix.

    ``spelled`` defaults to ``resolved`` so a caller that only has the resolved
    path keeps the old (target-only) behaviour; :func:`load_profile_file` passes
    the path as the user actually wrote it.

    User is checked FIRST — and on the TARGET only — so a user dir that happens
    to sit under ``cwd`` (a developer running aelix from inside ``~``) still
    classifies ``"user"`` and is not gated by Project Trust. A project symlink
    aimed INTO the user tier therefore reads ``"user"``, which is correct: the
    bytes loaded are the user's own file.
    """

    target = _safe_resolve(resolved)
    # Bundled first, and on the TARGET only, for the same reason "user" is: this
    # directory lives inside the installed package, and a user who runs aelix
    # from within site-packages (or from this source tree) would otherwise see
    # our own shipped profile classified ``"project"`` and trust-gated by an
    # ``--agent-file`` that names it.
    if _is_within(target, _safe_resolve(builtin_agents_dir())):
        return "bundled"
    if _is_within(target, _safe_resolve(user_agents_dir(str(agent_dir)))):
        return "user"

    cwd_resolved = _safe_resolve(cwd)
    if _is_within(target, cwd_resolved):
        return "project"

    lexical = Path(
        os.path.abspath(os.path.expanduser(str(resolved if spelled is None else spelled)))
    )
    # ``cwd`` is compared both resolved and lexical: the caller may hand over an
    # unresolved cwd (``/tmp/...`` behind a symlinked ``/tmp``), and a lexical
    # child of an unresolved parent is not a lexical child of its resolved form.
    cwd_lexical = Path(os.path.abspath(os.path.expanduser(str(cwd))))
    if _is_within(lexical, cwd_resolved) or _is_within(lexical, cwd_lexical):
        return "project"

    return "explicit"


def discover_profiles(
    cwd: str | Path,
    *,
    project_trusted: bool,
    agent_dir: str | None = None,
) -> ProfileDiscoveryResult:
    """Scan the user tier, then the project tier when trusted.

    Non-recursive ``*.md`` glob per directory; dotfiles and subdirectories are
    skipped (a profile is a single file — there is no ``agents/<name>/agent.md``
    form). Missing directories are NOT an error, mirroring ``load_skills``
    (``skills.py:167-168``).

    Identity is the ``name`` FIELD, not the filename; a mismatch is a warning
    (the file still loads under its declared name). Within one scope a duplicate
    name is last-wins + warning; across scopes PROJECT wins + warning.
    """

    diagnostics: list[ProfileDiagnostic] = []
    by_name: dict[str, AgentProfile] = {}

    # ORDER IS PRECEDENCE — last writer wins the ``by_name`` dict below, so this
    # list runs least-authoritative first: bundled → user → project.
    tiers: list[tuple[ProfileScope, Path]] = [
        ("bundled", builtin_agents_dir()),
        ("user", user_agents_dir(agent_dir)),
    ]
    if project_trusted:
        tiers.append(("project", project_agents_dir(cwd)))

    for scope, directory in tiers:
        for profile in _scan_dir(directory, scope, diagnostics):
            previous = by_name.get(profile.name)
            if previous is not None:
                if previous.scope == profile.scope:
                    diagnostics.append(
                        ProfileDiagnostic(
                            code="invalid_metadata",
                            message=(
                                f"duplicate agent profile name {profile.name!r} in "
                                f"the {scope} scope; {profile.file_path} shadows "
                                f"{previous.file_path}"
                            ),
                            path=profile.file_path,
                            type="warning",
                        )
                    )
                else:
                    # Spec §2.2 (ratified): the later tier wins. The warning
                    # naming BOTH paths is half of the ratified mitigation; the
                    # other half is the confirmation prompt in ``cli/entry.py``.
                    #
                    # BOTH scopes are read from the profiles rather than spelled
                    # "project"/"user": with the bundled tier there are three
                    # tiers and four orderings, and a hardcoded pair made the
                    # commonest one (a user profile shadowing a bundled one) read
                    # "project … overrides the user profile" — false on both
                    # nouns, about the one collision a normal install produces.
                    diagnostics.append(
                        ProfileDiagnostic(
                            code="invalid_metadata",
                            message=(
                                f"{profile.scope} agent profile {profile.name!r} "
                                f"({profile.file_path}) overrides the "
                                f"{previous.scope} profile at {previous.file_path}"
                            ),
                            path=profile.file_path,
                            type="warning",
                        )
                    )
            by_name[profile.name] = profile

    return ProfileDiscoveryResult(
        profiles=list(by_name.values()), diagnostics=diagnostics
    )


def load_profile_file(
    path: str,
    *,
    cwd: str | Path,
    agent_dir: str | None = None,
) -> ParseProfileResult:
    """Parse ONE profile named by an explicit path (``--agent-file``).

    The scope is still computed by containment (:func:`classify_scope`), never
    assumed ``"explicit"``. ``file_path`` on the returned profile is the
    resolved absolute path so relative ``skills:`` entries anchor correctly.
    """

    candidate = Path(os.path.expanduser(path))
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    resolved = _safe_resolve(candidate)
    scope = classify_scope(
        resolved,
        cwd=Path(cwd),
        agent_dir=Path(agent_dir or get_agent_dir()),
        # The path as the user SPELLED it (cwd-joined, ``~`` expanded, but not
        # symlink-resolved). Containment is decided on both this and ``resolved``
        # — see :func:`classify_scope`; passing only the latter let a
        # project-shipped symlink out of ``"project"`` scope entirely.
        spelled=candidate,
    )

    try:
        content = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Mirrors ``skills.py:380-391`` — a read failure is a diagnostic, not a
        # raise; ``resolve_profile`` is the one that escalates.
        return ParseProfileResult(
            diagnostics=[
                ProfileDiagnostic(
                    code="read_failed", message=str(exc), path=str(resolved)
                )
            ]
        )

    return parse_profile(content, file_path=str(resolved), scope=scope)


def resolve_profile(
    name_or_path: str,
    *,
    cwd: str | Path,
    project_trusted: bool,
    agent_dir: str | None = None,
    is_file: bool = False,
) -> AgentProfile:
    """Resolve ``--agent <name>`` / ``--agent-file <path>`` or raise.

    Fatal-on-error by design (plan decision 3): the alternative is running under
    a *different* identity than the user asked for. Three escalations over the
    render path:

    1. any parse ERROR → :class:`ProfileError`;
    2. a ``"project"``-scope hit while ``project_trusted`` is False →
       :class:`ProfileError` naming ``--approve`` (this is where
       :func:`classify_scope` earns its keep — an ``--agent-file`` pointing
       inside ``cwd`` is gated exactly like a discovered project profile);
    3. every ``missing_path`` WARNING → fatal. A profile whose ``skills:`` path
       silently vanished is not the identity it claims to be.
    """

    cwd_path = Path(cwd)

    if is_file:
        result = load_profile_file(name_or_path, cwd=cwd_path, agent_dir=agent_dir)
        if result.profile is None:
            raise ProfileError(
                f"cannot load agent profile from {name_or_path}: "
                f"{_format_errors(result.diagnostics)}"
            )
        profile = result.profile
        diagnostics = result.diagnostics
    else:
        discovered = discover_profiles(
            cwd_path, project_trusted=project_trusted, agent_dir=agent_dir
        )
        match = next(
            (p for p in discovered.profiles if p.name == name_or_path), None
        )
        if match is None:
            raise ProfileError(_unknown_name_message(
                name_or_path,
                discovered,
                cwd=cwd_path,
                project_trusted=project_trusted,
                agent_dir=agent_dir,
            ))
        profile = match
        # Per-profile diagnostics: filter by path, so a shadowed user profile's
        # warnings never escalate the WINNING project profile.
        diagnostics = [d for d in discovered.diagnostics if d.path == profile.file_path]

    if profile.scope == "project" and not project_trusted:
        raise ProfileError(
            f"agent profile {profile.name!r} ({profile.file_path}) is "
            "project-local and this directory is not trusted; pass --approve to "
            "trust this directory for this run, or move the profile to "
            f"{user_agents_dir(agent_dir)}."
        )

    missing = [d for d in diagnostics if d.code == "missing_path"]
    if missing:
        raise ProfileError(
            f"agent profile {profile.name!r} ({profile.file_path}) references "
            f"paths that do not exist: {_format_errors(missing)}"
        )

    return profile


# === Internals ==============================================================


def _scan_dir(
    directory: Path,
    scope: ProfileScope,
    diagnostics: list[ProfileDiagnostic],
) -> list[AgentProfile]:
    """Non-recursive ``*.md`` scan of one tier. Missing dir → ``[]``."""

    try:
        if not directory.is_dir():
            return []
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        diagnostics.append(
            ProfileDiagnostic(
                code="read_failed", message=str(exc), path=str(directory)
            )
        )
        return []

    profiles: list[AgentProfile] = []
    for entry in entries:
        # Dotfiles (editor swap files, ``.DS_Store``) and subdirectories are not
        # profiles — the format is a single flat ``<name>.md`` (plan decision 2).
        if entry.name.startswith("."):
            continue
        if not entry.name.endswith(".md"):
            continue
        if not entry.is_file():
            continue

        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.append(
                ProfileDiagnostic(
                    code="read_failed", message=str(exc), path=str(entry)
                )
            )
            continue

        result = parse_profile(content, file_path=str(entry), scope=scope)
        diagnostics.extend(result.diagnostics)
        if result.profile is None:
            continue
        if result.profile.name != entry.stem:
            diagnostics.append(
                ProfileDiagnostic(
                    code="invalid_metadata",
                    message=(
                        f"name {result.profile.name!r} does not match filename "
                        f"{entry.name!r}; the name field wins"
                    ),
                    path=str(entry),
                    type="warning",
                )
            )
        profiles.append(result.profile)
    return profiles


def _unknown_name_message(
    name: str,
    discovered: ProfileDiscoveryResult,
    *,
    cwd: Path,
    project_trusted: bool,
    agent_dir: str | None,
) -> str:
    """Actionable "no such profile" text.

    When the project tier was suppressed, peek at it anyway (parse only — no
    code runs) so the message can say ``--approve`` instead of the useless
    "unknown profile" for a profile the user can plainly see on disk.
    """

    if not project_trusted:
        hidden = _scan_dir(project_agents_dir(cwd), "project", [])
        if any(p.name == name for p in hidden):
            return (
                f"agent profile {name!r} exists only in {project_agents_dir(cwd)} "
                "and this directory is not trusted; pass --approve to trust this "
                "directory for this run."
            )

    known = sorted(p.name for p in discovered.profiles)
    if known:
        return f"unknown agent profile {name!r}; available: {', '.join(known)}"
    # Reachable only if the bundled tier is missing too — an install whose wheel
    # content was stripped. Naming the WRITABLE tier is the actionable half; the
    # bundled one is not somewhere a user should be told to write.
    return (
        f"unknown agent profile {name!r}; no profiles found in "
        f"{user_agents_dir(agent_dir)}"
    )


def _format_errors(diagnostics: list[ProfileDiagnostic]) -> str:
    errors = [d for d in diagnostics if d.type == "error"] or diagnostics
    return "; ".join(d.message for d in errors) or "no diagnostics"


def _safe_resolve(path: Path) -> Path:
    """``Path.resolve()`` that survives an unreadable/looping path.

    Symlink-aware on purpose: where a path really points is HALF of the
    containment decision (``.aelix/agents/../../../etc/x.md`` must not claim
    project scope by spelling). :func:`classify_scope` supplies the other half.
    """

    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(str(path)))


def _is_within(target: Path, parent: Path) -> bool:
    try:
        return target == parent or target.is_relative_to(parent)
    except (OSError, ValueError):
        return False


__all__ = [
    "ProfileDiscoveryResult",
    "ProfileError",
    "builtin_agents_dir",
    "classify_scope",
    "discover_profiles",
    "load_profile_file",
    "project_agents_dir",
    "resolve_profile",
    "user_agents_dir",
]
