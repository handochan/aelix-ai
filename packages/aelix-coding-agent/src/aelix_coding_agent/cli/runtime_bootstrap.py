"""CLI runtime bootstrap — provider registration + .env load + model resolution.

Wires real LLM turns for the interactive / print / rpc CLI. Three pieces:

- :func:`load_dotenv` — the cwd ``.env`` loader (``setdefault`` semantics so
  real environment variables always win). This is a SECURITY BOUNDARY, not a
  dev convenience: it runs from ``main_sync`` BEFORE the Project Trust gate
  exists, so a ``.env`` from a repo you merely cloned is attacker-controlled
  input to ``os.environ``. It admits provider credentials plus a short,
  value-shape-checked list of provider-configuration names, and refuses
  everything else — see the admission-control block below for the four
  privilege-escalation chains that were reproduced end-to-end against the real
  CLI, and ADR-0203.
- :func:`register_providers` — registers the built-in provider adapters on the
  global API registry (idempotent).
- :func:`resolve_model` — resolves the :class:`Model` to drive a turn, from the
  flags, the env, the static catalog and (optionally) the live ``ModelRegistry``.
  OpenRouter (OpenAI-compatible) is configured purely from env: when
  ``OPENROUTER_API_KEY`` + a model id are present (and no conflicting
  ``--provider``), a model with ``provider="openrouter"``,
  ``api="openai-completions"`` and the OpenRouter ``base_url`` is built — unless
  the ``--model`` string names an endpoint OpenRouter cannot serve (a provider
  the user defined, or a catalogued provider that is not an OpenRouter
  namespace; ADR-0249, #344), which is resolved inside that provider FIRST. The
  ``openai_completions`` adapter reads ``OPENROUTER_API_KEY`` from the
  environment itself, so no auth callback wiring is required. Falls back to a
  bare ``Model`` (from ``--model`` / ``--provider``) otherwise — which CANNOT
  drive a turn, so callers gate on ``core.runnable_models.is_runnable`` (#98).
  This function owns the ENTIRE provider-precedence ladder (explicit flag →
  a ``--model`` OpenRouter cannot serve → OpenRouter env → in-id prefix →
  settings default); callers pass each source in its own parameter and must
  never pre-merge them, because the earlier rungs are gated on the later ones
  being absent.

Provider registration + ``.env`` load run from the real console entry
(:func:`aelix_coding_agent.cli.entry.main_sync`), NOT from ``_async_main`` — so
embedders / tests that call ``_async_main`` directly keep deterministic,
side-effect-free behavior.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from aelix_ai.providers import anthropic as _anthropic
from aelix_ai.providers import google_generative_ai as _google_generative_ai
from aelix_ai.providers import google_vertex as _google_vertex
from aelix_ai.providers import openai_codex_responses as _openai_codex_responses
from aelix_ai.providers import openai_completions as _openai
from aelix_ai.providers import openai_responses as _openai_responses
from aelix_ai.providers.openai_completions import OPENAI_COMPLETIONS_API
from aelix_ai.streaming import Model

_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# === cwd ``.env`` admission control (ADR-0203) ===============================
#
# A cwd ``.env`` is ATTACKER-CONTROLLED the moment you ``git clone`` and ``cd``,
# and this function runs from ``entry.py`` ``main_sync`` BEFORE the Project
# Trust gate exists. Four chains were reproduced end-to-end (2026-08-01)
# against the real CLI (``python -m aelix_coding_agent --print``), under
# ``env -i`` — no API key, no model, no network:
#
#   AELIX_MCP_CONFIG       cli/config.py ``load_mcp_server_contribs`` — the
#                          "env" tier, which entry.py never gates because it
#                          assumes that tier is a USER choice. Spawned
#                          ``sh -c <payload>`` at startup; marker written. This
#                          one fires even under ``--no-approve``, i.e. with the
#                          user explicitly DECLINING to trust the directory,
#                          because it never consults trust at all.
#   AELIX_SETTINGS_PATH    aelix_ai/settings/storage.py ``default_settings_path``
#                          — the repo's own file becomes the GLOBAL settings
#                          store, so ``defaultProjectTrust: "always"``
#                          self-elevates the repo to trusted and the
#                          project-tier ``.aelix/mcp.json`` then executed.
#   AELIX_CODING_AGENT_DIR cli/config.py ``get_agent_dir`` -> entry.py
#                          ``agent_dir=`` -> ``SettingsStore`` global path — the
#                          same hijack by a different door, which is why this is
#                          a POLICY and not a fix for three key names.
#   OPENROUTER_BASE_URL    :func:`resolve_model` below -> ``Model.base_url``,
#                          which carries the Authorization header and the full
#                          prompt to an attacker-chosen host.
#
# CORRECTION to the sprint spec, which said all four reproduce "with
# ``--no-approve``": the two trust-defeat chains do NOT, and cannot. Step 1 of
# ``resolve_project_trusted`` short-circuits on an explicit override BEFORE
# step 5 reads ``defaultProjectTrust``, so ``--no-approve`` is honored. Their
# real path is the ordinary one — no trust flag at all, where step 6's
# non-interactive DENY is what the hijacked setting overturns. Measured A/B in
# a repo carrying only ``.env`` + ``.aelix/mcp.json``:
#
#   without .env  -> BOTH "skipped in an untrusted directory" notices, no marker
#   with .env     -> NEITHER notice, marker written (TRUST_GATE_DEFEATED)
#
# Two measurements decided the SHAPE:
#
# 1. Gating this on Project Trust does NOT close it. A repo carrying only a
#    ``.env`` and no ``.aelix/`` has no trust-requiring resource, so
#    ``resolve_project_trusted`` short-circuits at step 2 and returns True.
#    Making trust real here means making ``.env`` itself trust-requiring, which
#    prompts every developer who has one and DENIES it non-interactively
#    (``--print``/json/rpc are deny-by-default, ADR-0149): their own key
#    silently stops loading in CI.
# 2. A denylist cannot be completed. ``tools/bash.py`` hands ``get_shell_env()``
#    (``dict(os.environ)``) to every ``bash -c``, and bash SOURCES ``$BASH_ENV``
#    in every non-interactive shell — measured on bash 5.2.21:
#    ``env -i PATH=… BASH_ENV=p.sh bash -c 'echo body'`` ran the payload first.
#    ``BASH_ENV`` carries no aelix prefix and is owned by bash; behind it sit
#    ``LD_PRELOAD``, ``NODE_OPTIONS`` (MCP via npx), ``GIT_SSH_COMMAND``,
#    ``PYTHONSTARTUP``, ``EDITOR``/``VISUAL``. So: default-DENY. The dangerous
#    set does not have to be enumerable, only the safe one.
#
# The safe set is SECRET MATERIAL ONLY, by suffix rather than by table: all 31
# distinct names in ``ENV_API_KEYS`` end in one of these suffixes (measured, 0
# refused), and the suffix also reaches names no table holds — the owner's own
# ``.env`` carries ``OPENAI_RESPONSE_API_KEY``, which has no consumer anywhere in
# the repo, and ``model_registry.py`` ``resolve_config_value_uncached`` resolves
# an ARBITRARY env-var name declared in a user's models.json. A closed list
# refuses both. The reach is only as wide as the SHAPE, though: measured, a
# models.json provider that declares a key named ``ACME_ENDPOINT_NAME`` is
# still refused, because nothing about that name says "secret". Such a provider
# needs the hatch or an export — see ``.env.example``. A repo may hand us a
# credential; it may not hand us a path, a URL, an interpreter option or a
# program name.
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET", "_PASSWORD")

# Subtraction from the suffix rule: a credential-SHAPED name that is really a
# path, URL, program or one of OUR OWN knobs still loses. The ``^AELIX_``/
# ``^PI_`` branch is the load-bearing half — it stops a FUTURE aelix variable we
# happen to name ``*_KEY`` from being repo-settable, which is the one thing the
# suffix rule alone cannot do. That sentence is executable rather than asserted,
# and the executable form is one parametrization: measured, deleting the
# ``^(AELIX|PI|…)_`` alternation turns exactly ONE of the 133 admission tests red —
# ``test_repo_dotenv_cannot_set_control_plane[AELIX_FUTURE_API_KEY]``. Before that
# case existed the whole file passed with the alternation gone, because every
# other ``AELIX_*`` name under test is refused for some other reason. So one
# ``CONTROL_PLANE`` entry is the entire test pressure on the branch this comment
# calls load-bearing; do not delete it. (An earlier draft of this comment cited a
# standalone test by name. No such test exists — the coverage is the parametrized
# case above, and citing a function that is not in the tree is the failure mode
# this file's comments are supposed to be immune to.)
# Measured today, against corpora you can re-run rather than a number you have to
# trust: :func:`_dotenv_key_allowed` refuses 0 of the 31 distinct ``ENV_API_KEYS``
# names, and admits 0 of the 16 names in ``CONTROL_PLANE``
# (``tests/cli/test_dotenv_admission.py``), which is the dangerous corpus with a
# named consumer per entry.
_DOTENV_NEVER = re.compile(
    r"^(AELIX|PI|PYTHON|LD|BASH|NODE|PERL|RUBY|GIT|SSH|NPM|npm)_"
    r"|URL|PATH|DIR|HOME|SHELL|PROXY|OPTS|OPTIONS|PRELOAD|COMMAND"
)


class _ConfigRule(NamedTuple):
    """A provider-config name a ``.env`` may set, and the VALUES it may set it to."""

    shape: re.Pattern[str]
    #: Why a rejected value was rejected — printed verbatim, so it must be true
    #: of THIS key. The three GCP names and a model id fail for different
    #: reasons and one shared sentence would be false for one of them.
    why: str


# GCP regions (``us-central1``, ``europe-west4``, ``global``) and project ids are
# plain lowercase names. Measured ACCEPT: us-central1, europe-west4,
# asia-northeast3, us-east5, northamerica-northeast1, global, my-gcp-project,
# aelix-prod-1. Measured REJECT: 'attacker.example/x', '@attacker.example',
# 'attacker.example:8443/v1', 'us-central1.attacker.example', 'US-CENTRAL1',
# '../../x', 'a b', 'x\ty', ''. Both lists are committed as ``GCP_NAMES_OK`` /
# ``GCP_NAMES_BAD``. It forbids ``/ @ : .`` STRUCTURALLY, which is the point.
#
# BOUND, stated rather than glossed: an earlier draft of this comment and of
# ADR-0203 claimed the rule "excludes no legitimate value". That is not something
# this repo can check — it is an assertion about Google's documented syntax, and
# there is no network here to check it against. Measured, one shape it DOES
# exclude is a numeric GCP project (``123456789012`` -> no match, because the
# first character must be a letter). Whether Vertex accepts a project NUMBER
# where it accepts a project id is exactly the thing that cannot be verified
# offline, so the rule is left as-is and the remedy is written down instead: the
# shape check lives inside :func:`load_dotenv` and governs the ``.env`` path
# only, so any value at all still works when you export it in your own shell.
_GCP_NAME = re.compile(r"\A[a-z][a-z0-9-]{0,61}[a-z0-9]\Z")
_MODEL_ID = re.compile(r"\A[A-Za-z0-9._:/-]{1,128}\Z")

# A Cloudflare account id / AI-Gateway name. These differ from the Vertex
# location in WHERE they land, and the difference is measured, not assumed:
# the 16 hostile values of ``CF_IDS_BAD`` against the catalog's 4
# ``{CLOUDFLARE*}``-templated base URLs — 64 cases, each expanded and then joined
# by a real ``httpx.Client``. 60 produced a URL, and every one of them kept the
# request host at ``gateway.ai.cloudflare.com`` or ``api.cloudflare.com``. The
# other 4 are the single value ``x\ty``, which raises ``httpx.InvalidURL`` on
# each of the four templates.
# Contrast the Vertex location, which owns the host and moves it to
# ``attacker.example``. So these are PATH-only, which is the same argument
# ADR-0203 uses to admit ``GOOGLE_CLOUD_PROJECT``.
#
# The shape rule is still load-bearing, because the PATH is not fixed. Measured
# joined request URLs with the rule removed:
#   '../../..'                          https://gateway.ai.cloudflare.com/gw/anthropic/chat/completions
#   'x/../../../../../attacker.example' https://gateway.ai.cloudflare.com/attacker.example/gw/anthropic/chat/completions
#   'x?q='                              https://gateway.ai.cloudflare.com/v1/x?q=/gw/anthropic/chat/completions
#   'x#f'                               https://gateway.ai.cloudflare.com/v1/x/chat/completions#f/gw/anthropic
# i.e. a repo can climb out of ``/v1/{account}/{gateway}/`` and put the key and
# the prompt on a different endpoint of Cloudflare's own host. The same holds
# through the hatch, which is why this arm runs BEFORE it.
# Measured ACCEPT 7/7, REJECT 16/16 — both lists committed as ``CF_IDS_OK`` /
# ``CF_IDS_BAD``. Same offline bound as ``_GCP_NAME``: no claim is made here
# about Cloudflare's documented id syntax, only that every structural escape the
# repo could construct is rejected, and that an export still takes any value.
_CF_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

# THIRD ADMISSION ARM — provider CONFIGURATION, admitted by name AND by value.
#
# The credential rule refused these, and that was a measured regression TWICE, in
# the same shape, which is why the criterion is written down below rather than
# left to the next reader's judgement:
#
#   1. ``google-vertex``'s primary documented auth path is ADC (ADR-0173), which
#      uses no API key at all — it needs a project AND a location, and without
#      both ``runnable_models._vertex_config_missing`` hides all 15 vertex models.
#   2. Both Cloudflare providers carry the catalog's only ``{ENV_VAR}``-templated
#      base_urls, and ``runnable_models._base_url_unconfigured`` hides every model
#      whose token is still unexpanded. Measured A/B at
#      ``core.runnable_models.is_runnable``, one ``.env``, ``env -i``, no network:
#      total runnable 847 -> 804 with the ids refused, delta 43, and exactly two
#      providers moved (cloudflare-ai-gateway 35 -> 0, cloudflare-workers-ai
#      8 -> 0). ``CLOUDFLARE_API_KEY`` is admitted by the suffix rule, so the
#      failure looked like a working configuration.
#
# In both cases the developer saw aelix claim it had no models for a provider
# they had configured, with nothing connecting that to the stderr notice.
#
# THE CRITERION, so a third one does not have to be found by A/B: a name that a
# provider's models need in order to be VISIBLE belongs in this arm. Today that
# is exactly (a) every ``{ENV_VAR}`` token in a catalog ``baseUrl`` and (b) every
# name read by a ``runnable_models`` config guard. Measured, ``is_runnable``
# consults SIX environment names in total across all 1001 catalog models —
# CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID, GOOGLE_CLOUD_API_KEY,
# GOOGLE_CLOUD_PROJECT, GCLOUD_PROJECT, GOOGLE_CLOUD_LOCATION — and all six are
# now admitted: five by this arm, and ``GOOGLE_CLOUD_API_KEY`` by the suffix rule.
# A committed test holds (a) mechanically; see
# ``test_every_templated_base_url_token_is_admissible``.
#
# The VALUE rule is not decoration. Measured on this branch, no network:
# ``create_vertex_client(project=…, location=L)`` builds
# ``https://{L}-aiplatform.googleapis.com/``, so L owns the HOST —
#
#   location='us-central1'              NETLOC 'us-central1-aiplatform.googleapis.com'
#   location='attacker.example/x'       NETLOC 'attacker.example'
#   location='attacker.example:8443/v1' NETLOC 'attacker.example:8443'
#
# — i.e. an unvalidated ``GOOGLE_CLOUD_LOCATION`` is chain 3 by another name,
# carrying an ADC bearer token and the whole prompt off googleapis.com.
# Admitting it by NAME alone would have re-opened the exact class this block
# exists to close, so the shape check is UNCONDITIONAL and runs BEFORE the
# escape hatch: measured, hatch-first with ``AELIX_DOTENV_ALLOW=
# GOOGLE_CLOUD_LOCATION`` and value ``attacker.example/x`` yields
# ``base_url='https://attacker.example/x-aiplatform.googleapis.com/'``. The hatch
# names a KEY; the redirect lives in the VALUE, so the hatch has nothing to say
# about it.
#
# The project is the weaker case and is admitted on a narrower argument.
# Measured, it never reaches the host at all — it lands in the request PATH
# (``projects/{p}/locations/{l}/…``) under a host fixed by the location, and the
# same regex removes path traversal. What a repo-chosen project CAN still do is
# have the call attributed and billed to a project the user did not choose and —
# if the attacker owns a project with ``allAuthenticatedUsers`` bound to
# ``roles/aiplatform.user`` — put the prompt somewhere they can read it. That is
# the SAME CLASS as the credential substitution ADR-0203 already accepts (its
# residual risk 1), not a new one, and it is written down there. Project alone is
# worthless anyway: ``_vertex_config_missing`` requires project AND location, so
# it is both or neither.
#
# The two Cloudflare ids are the same weaker case: path-only, host fixed. See
# ``_CF_ID`` above for the 45 measurements and for the path rewrites that make
# their shape rule load-bearing anyway.
#
# REFUSED on purpose, both still hatchable, both disclosed in ``.env.example``:
#   GOOGLE_APPLICATION_CREDENTIALS — a PATH to a full GCP service-account
#       identity a repo can ship, whose ``token_uri`` points the signed assertion
#       wherever the repo likes: finding 5's substitution with a blast radius
#       bigger than one provider. Refusing it does NOT break ADC — measured,
#       ``google.auth`` finds ``application_default_credentials.json`` at its
#       well-known Cloud-SDK location with NO env var, and this variable is
#       consumed only by the explicit service-account-file variant. ``gcloud auth
#       application-default login`` + project + location keeps working entirely
#       from ``.env``.
#   AELIX_CODEX_ORIGINATOR — ``^AELIX_`` is the invariant this whole design leans
#       on ("a future aelix knob named ``*_KEY`` is still un-settable by a repo").
#       Punching a hole in it for a cosmetic attribution string sent to OpenAI is
#       a bad trade; the built-in default ``"aelix"`` is the correct value.
_DOTENV_CONFIG_VALUES: dict[str, _ConfigRule] = {
    "GOOGLE_CLOUD_LOCATION": _ConfigRule(
        _GCP_NAME,
        "its value is not a plain name (lowercase letters, digits, hyphens). A "
        "value containing '/', '@' or ':' would move Vertex requests off "
        "googleapis.com.",
    ),
    "GOOGLE_CLOUD_PROJECT": _ConfigRule(
        _GCP_NAME,
        "its value is not a plain name (lowercase letters, digits, hyphens). A "
        "GCP project id cannot contain '/', '@', ':' or '.', and one that did "
        "would reach into the Vertex request path.",
    ),
    "GCLOUD_PROJECT": _ConfigRule(
        _GCP_NAME,
        "its value is not a plain name (lowercase letters, digits, hyphens). A "
        "GCP project id cannot contain '/', '@', ':' or '.', and one that did "
        "would reach into the Vertex request path.",
    ),
    # The catalog's only ``{ENV_VAR}``-templated base_urls. Both ids land in the
    # request PATH under a host the template fixes, so their sentence is about
    # the path — the Vertex one, about the host, would be false here.
    "CLOUDFLARE_ACCOUNT_ID": _ConfigRule(
        _CF_ID,
        "its value is not a plain id (letters, digits, '-' and '_', up to 64 "
        "characters). A value containing '/', '?', '#' or '..' would rewrite "
        "the request path under Cloudflare's own host.",
    ),
    "CLOUDFLARE_GATEWAY_ID": _ConfigRule(
        _CF_ID,
        "its value is not a plain id (letters, digits, '-' and '_', up to 64 "
        "characters). A value containing '/', '?', '#' or '..' would rewrite "
        "the request path under Cloudflare's own host.",
    ),
    # Bounded to model choice, therefore to cost: consumed only by
    # :func:`resolve_model` below as ``model_flag or os.environ.get(...)``, and
    # it reaches the JSON body's ``"model"`` field — never a URL, never a path,
    # never a program.
    "OPENROUTER_DEFAULT_MODEL": _ConfigRule(
        _MODEL_ID,
        "its value is not a model id (letters, digits and '.', '_', ':', '/', "
        "'-', up to 128 characters).",
    ),
}

# The gate's own name. It gets its own branch and its own notice because the
# locked sentence below does not describe it: it decides who may open the gate,
# not where anything lives.
_DOTENV_GATE = "AELIX_DOTENV_ALLOW"

# The floor under the escape hatch. CRITERION, which is the maintained artifact
# here — the list is only its current application:
#
#   the hatch may let a repo REDIRECT; it may never let a repo EXECUTE, and it
#   may never let a repo choose the global settings/auth store or widen the gate.
#
# The previous criterion ("these decide where aelix's global settings live") was
# drawn on a different axis from the reason the floor exists, which is why
# ``AELIX_MCP_CONFIG`` — the only chain that fires under ``--no-approve``, and
# the one that is arbitrary code execution rather than an indirect trust defeat —
# was not on it. Measured: one pasted ``export AELIX_DOTENV_ALLOW=AELIX_MCP_CONFIG``
# restored startup ``sh -c <payload>`` in full.
#
# HONESTY, because the neighbouring rules do not have this property: the
# ADMISSION rule above is default-deny and therefore complete by construction,
# whereas this set is a BEST-EFFORT floor under a user-typed opt-in. It is not
# claimed to be complete. Apply the criterion to a name nobody has thought of yet
# rather than pattern-matching this list.
#
# Measured with this set, one exported name per run, ``.env`` supplying the value:
#   HELD     all 14 below
#   UNLOCKED OPENROUTER_BASE_URL, PI_OFFLINE  <- the hatch keeps its use case
_DOTENV_LOCKED = frozenset(
    {
        # A. store / identity locators — every "GLOBAL scope only" read is only
        #    global-scope-only if the PATH to the global file is out of reach.
        "AELIX_SETTINGS_PATH",  # settings/storage.py default_settings_path
        "AELIX_CODING_AGENT_DIR",  # cli/config.py get_agent_dir -> settings + trust.json
        "AELIX_AUTH_PATH",  # the oauth auth store
        "XDG_CONFIG_HOME",  # storage.py, the same store by a third door
        "HOME",  # everything anchored at ~
        # B. gate integrity — a .env must not widen the gate it is judged by.
        _DOTENV_GATE,
        # C. code execution from the VALUE alone.
        "AELIX_MCP_CONFIG",  # load_mcp_server_contribs' never-gated "env" tier
        "BASH_ENV",  # bash SOURCES it in every non-interactive shell
        "LD_PRELOAD",  # arbitrary .so into every child process
        "NODE_OPTIONS",  # --require=<js> into every node child (MCP via npx)
        "GIT_SSH_COMMAND",  # arbitrary command on any git the agent runs
        # The last three fire on a USER action (Ctrl+G) or an interactive
        # interpreter rather than on aelix's own startup. They are here because
        # the criterion is about the value NAMING A PROGRAM, and nobody has a
        # legitimate reason to set them from a repo ``.env``.
        "PYTHONSTARTUP",  # sourced by any interactive python the agent starts
        "EDITOR",  # tui/shell.py spawns it on Ctrl+G
        "VISUAL",  # same
    }
)


def _dotenv_key_allowed(key: str) -> bool:
    """May a repo-supplied ``.env`` set ``key``? Default DENY — see above."""

    if _DOTENV_NEVER.search(key):
        return False
    if key.endswith(_CREDENTIAL_SUFFIXES):
        return True
    # Redundant with the suffix rule for every name in the table today — measured,
    # replacing this line with ``return False`` turns 0 of the 133 admission tests
    # red. Kept so a provider added with an odd key name keeps working without
    # anyone remembering this filter exists.
    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    return any(key in names for names in ENV_API_KEYS.values())


def _dotenv_user_allowlist() -> frozenset[str]:
    """Per-key opt-in, read from the REAL environment only.

    A ``.env`` cannot set this. Measured, FOUR independent guards refuse it: its
    own branch in :func:`load_dotenv`, ``_DOTENV_LOCKED``, ``^AELIX_``, and plain
    default-deny (``AELIX_DOTENV_ALLOW`` is not credential-shaped). So the guard
    cannot be disarmed by the thing it guards against — and no single-guard
    mutation can prove that, which is why the committed test for it removes the
    admission rule wholesale. (``setdefault`` does NOT contribute here: it
    protects a key NAME, not this guard; see :func:`_dotenv_shadowed_sibling` for
    what it does and does not buy.)

    No wildcard: ``*`` is discarded. Measured, this does NOT stop a hostile
    README's one-liner — a comma-list restores exactly the key set a wildcard
    would, because the only thing refusing anything in either arm is
    ``_DOTENV_LOCKED``, which applies to both. Re-measured against the 14-name
    floor with a 16-key hostile ``.env`` (the 14 locked names plus
    ``OPENROUTER_BASE_URL`` and ``PI_OFFLINE``): shipped + a comma-list naming all
    16 took ``['OPENROUTER_BASE_URL', 'PI_OFFLINE']``, and since a wildcard arm
    would compute the same ``keys - _DOTENV_LOCKED``, the keys denied by the
    no-wildcard rule = NONE. (Under the old five-name floor the same probe let 9
    keys through both arms — the floor is what changed, not the wildcard rule.)
    What the per-key rule buys is that every name a repo can set is a name the
    USER typed: the notices below name something they can recognise, and an audit
    of a machine can read the intent off one line. The floor that actually stops
    the pasted one-liner is ``_DOTENV_LOCKED`` — see its criterion above.

    The ``- _DOTENV_LOCKED`` below is DEFENSE-IN-DEPTH, not the lock. Measured:
    deleting it turns 0 of the 133 admission tests red, because
    :func:`load_dotenv` tests ``_DOTENV_LOCKED`` in its own branch BEFORE it looks
    at this set. It is kept so that the returned set can be read as "names a repo
    may set" without having to hold the loader's branch order in your head.
    """

    raw = os.environ.get(_DOTENV_GATE, "")
    names = frozenset(
        n.strip() for n in raw.split(",") if n.strip() and n.strip() != "*"
    )
    return names - _DOTENV_LOCKED


def _dotenv_shadowed_sibling(
    key: str, before: Mapping[str, str]
) -> tuple[str, str] | None:
    """Would admitting ``key`` outrank a key the USER exported for the provider?

    ``setdefault`` protects a key NAME, not a provider. ``get_env_api_key``
    returns the first non-empty name in ``ENV_API_KEYS[provider]``, so a repo
    ``.env`` supplying ``ANTHROPIC_OAUTH_TOKEN`` never collides with an exported
    ``ANTHROPIC_API_KEY`` — it simply outranks it, and every turn then
    authenticates as whoever wrote the file. Measured before this guard existed:
    shell ``ANTHROPIC_API_KEY`` + repo ``.env`` ``ANTHROPIC_OAUTH_TOKEN`` ->
    ``get_api_key_cascade('anthropic')`` returned the file's token.

    INDEX ORDER IS THE WHOLE QUESTION, and the first version of this guard did
    not ask it. ``ENV_API_KEYS['anthropic'] = ['ANTHROPIC_OAUTH_TOKEN',
    'ANTHROPIC_API_KEY']``, so the two directions are not symmetric:

    * shell ``ANTHROPIC_API_KEY`` + ``.env`` ``ANTHROPIC_OAUTH_TOKEN`` — the file
      wins the selection. Refuse; the notice is true.
    * shell ``ANTHROPIC_OAUTH_TOKEN`` + ``.env`` ``ANTHROPIC_API_KEY`` — measured,
      ``get_env_api_key`` returns the SHELL value whether the file's key is
      admitted or not, because index 0 wins. Admitting it changes no selection,
      so refusing it dropped a key the user asked for and told them, in the
      notice, that it "would have outranked" a token it could not outrank.

    So the test is not "is any sibling present" but "is the sibling the selector
    would currently pick ranked BELOW ``key``". That also stays correct for a
    hypothetical three-name provider where the shell holds both a higher- and a
    lower-ranked name: the higher one is already winning, so ``key`` changes
    nothing and is admitted.

    ``before`` MUST be a snapshot taken before any line of this file was applied.
    Read live, a ``.env`` that legitimately supplies both names would shadow
    itself on the second line — a ``.env`` line is not a shell-supplied sibling.

    Returns ``(sibling, provider)`` — the name the selector picks TODAY — or
    ``None``.

    SCOPE, deliberate: **we change precedence only where we implement it.**
    ``get_env_api_key`` is our selection, so we may refuse. Measured, ``anthropic``
    is the only ``ENV_API_KEYS`` entry with more than one name today (three names
    are shared by two providers each, but same NAME, so ``setdefault`` already
    covers those). Precedence implemented by OTHER programs is disclosed instead
    of overridden — see the ``GH_TOKEN`` notice in :func:`_report_dotenv` for the
    one we measured and deliberately did not close.

    KNOWN BLIND SPOT, measured rather than left for a later round to find: this
    guard iterates ``ENV_API_KEYS``, so it says nothing about a name that is not
    in that table. ``ANTHROPIC_AUTH_TOKEN`` is admitted by the ``_TOKEN`` suffix,
    is absent from the table, and the anthropic SDK reads it itself — measured at
    request build, with ``api_key=None`` and no aelix-supplied header, a
    ``.env``-supplied ``ANTHROPIC_AUTH_TOKEN`` becomes the request's
    ``Authorization: Bearer`` header (with an explicit api_key, ``x-api-key``
    carries the user's key instead). That is ADR-0203 residual risk 1's class —
    a repo supplying a credential — reached by a route this guard cannot see,
    and it is recorded there rather than silently patched here, because widening
    the guard past our own selection is the thing the SCOPE note above forbids.
    """

    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    for provider, names in ENV_API_KEYS.items():
        if len(names) < 2 or key not in names:
            continue
        selected = next((n for n in names if before.get(n)), None)
        if selected is not None and names.index(key) < names.index(selected):
            return (selected, provider)
    return None


def _sanitize(name: str) -> str:
    """Make an attacker-controlled key name safe to print.

    The KEY text comes from the repo and goes to a terminal, so a crafted key
    could otherwise smuggle ANSI escapes and forge output — including something
    shaped like a trust prompt. This injection risk did not exist before the
    notices below, because the old loader printed nothing.
    """

    return "".join(c for c in name if c.isprintable() and c != "\x1b")[:64]


def _report_dotenv(
    p: Path,
    *,
    credentials: list[str],
    config: list[str],
    hatched: list[str],
    refused: list[str],
    locked: list[str],
    gate: list[str],
    shadowed: list[tuple[str, str, str]],
    badvalue: list[tuple[str, str]],
    foreign_precedence: bool,
) -> None:
    """Disclose what a ``.env`` did and did not get to set. Names, never values.

    Each admitted CLASS gets its own line. One line hard-coded to the word
    "credentials" would be a false label for two of the three admitted classes —
    a GCP location and a Cloudflare account id are provider configuration, and a
    hatch-admitted ``OPENROUTER_BASE_URL`` is neither. The residual-risk argument
    rests on a specific line per class: line 1 for credentials (the "I never
    typed that key" signal that makes residual risk 1 acceptable), line 2 for
    provider configuration, line 3 for the interesting case, a hatch-admitted
    NON-credential whose value came from this repo.

    EVERY sentence below is printed for a specific outcome and has to be true of
    that outcome and no other. Two of them were not, and both are pinned by tests
    now: the refusal line said a ``.env`` "is for provider credentials only"
    while the config line two above it announced otherwise, and the shadowed line
    claimed the refused key "would have outranked" a sibling it could not
    outrank. When a branch's behaviour changes, its sentence is part of the
    change.

    Stderr only: ``--print`` / ``--mode json`` / ``--mode rpc`` stdout stays
    byte-clean.
    """

    def _names(keys: list[str]) -> str:
        return ", ".join(sorted(_sanitize(k) for k in keys))

    if credentials:
        print(
            f"Notice: loaded credentials from {p}: {_names(credentials)}",
            file=sys.stderr,
        )
    if config:
        print(
            f"Notice: loaded provider configuration from {p}: {_names(config)}",
            file=sys.stderr,
        )
    if hatched:
        print(
            f"Notice: loaded {_names(hatched)} from {p} because your "
            f"{_DOTENV_GATE} lists them — these are not credentials, and their "
            "values come from this repo.",
            file=sys.stderr,
        )
    if refused:
        # "…is for provider credentials only" shipped here for one round while
        # the line two above it announced "loaded provider configuration from
        # .env" — one stderr block asserting both halves of a contradiction, and
        # the false half was printed for names (the Cloudflare ids) that the
        # config arm has since been widened to admit. This sentence has to
        # describe what the loader ACTUALLY admits, which is two classes.
        print(
            f"Notice: ignored {_names(refused)} from {p} — a project .env "
            "carries provider credentials and a short list of "
            "provider-configuration names, and these are neither. Export them "
            f"in your shell, or list them in {_DOTENV_GATE}.",
            file=sys.stderr,
        )
    if locked:
        # Their own sentence, and it deliberately does NOT offer the hatch as a
        # remedy: for the one chain that is arbitrary code execution, the old
        # generic line printed the exploit's own recipe next to the key name.
        # "settings and credentials" rather than "settings": AELIX_AUTH_PATH
        # locates the auth store, which is not the settings file.
        print(
            f"Notice: ignored {_names(locked)} from {p} — a project file may "
            "never set these: their value alone decides where aelix's global "
            f"settings and credentials live, or what program aelix runs. "
            f"{_DOTENV_GATE} cannot unlock them. Export them in your own shell "
            "if you need them.",
            file=sys.stderr,
        )
    if gate:
        print(
            f"Notice: ignored {_DOTENV_GATE} from {p} — it decides which names "
            "a .env may set, so it is only ever read from your shell "
            "environment. A .env cannot widen its own gate.",
            file=sys.stderr,
        )
    for key, sibling, provider in sorted(shadowed):
        print(
            f"Notice: ignored {_sanitize(key)} from {p} — your shell already "
            f"provides {_sanitize(sibling)} for {provider}, and "
            f"{_sanitize(key)} would have outranked it. Unset "
            f"{_sanitize(sibling)}, or list {_sanitize(key)} in {_DOTENV_GATE}.",
            file=sys.stderr,
        )
    for key, why in sorted(badvalue):
        print(f"Notice: ignored {_sanitize(key)} from {p} — {why}", file=sys.stderr)
    if foreign_precedence:
        # DISCLOSURE, not a guard. gh 2.88.0 prefers GH_TOKEN over GITHUB_TOKEN
        # (measured twice, ``env -i … gh auth token`` -> the GH_TOKEN value), and
        # that precedence is gh's to implement, not ours to override. Refusing it
        # here would silently break a deliberate, documented setup in every
        # GitHub Codespace, where GITHUB_TOKEN is an AMBIENT platform default the
        # user never chose — measured present on this machine with GH_TOKEN unset.
        print(
            f"Notice: {p} supplied GH_TOKEN; the gh command aelix runs prefers "
            "it over the GITHUB_TOKEN already in your environment.",
            file=sys.stderr,
        )


def load_dotenv(path: str = ".env") -> None:
    """Load provider credentials from a cwd ``.env`` into ``os.environ``.

    ``setdefault`` semantics: a value already present in the real environment
    is never overwritten. Lines that are blank, comments (``#``), or lack ``=``
    are skipped; surrounding single/double quotes on the value are stripped.

    SECURITY: this is admission-controlled — see the block above. It admits
    provider credentials, plus the short, value-shape-checked
    ``_DOTENV_CONFIG_VALUES`` list, because this runs before the Project Trust
    gate and a cloned repo's ``.env`` would otherwise be able to spawn processes,
    relocate the global settings file and redirect API traffic. Anything refused
    is still available by exporting it in your own shell.

    PRECEDENCE, stated precisely because the previous three sentences on this
    were false:

    1. a value you exported under the SAME name always wins — that is
       ``setdefault``, and it is name-scoped, not provider-scoped;
    2. for a provider aelix resolves itself, a shell-supplied key now also wins
       over a DIFFERENT name from this file (:func:`_dotenv_shadowed_sibling`);
    3. it is still not universal: programs aelix RUNS have their own precedence.
       ``gh`` prefers ``GH_TOKEN`` over ``GITHUB_TOKEN`` (measured, gh 2.88.0),
       so a ``GH_TOKEN`` here does outrank a ``GITHUB_TOKEN`` in your shell.
       Export ``GH_TOKEN`` yourself if that matters.
    """

    p = Path(path)
    if not p.exists():
        return
    # Read the escape hatch BEFORE applying any key, so a ``.env`` that sets
    # ``AELIX_DOTENV_ALLOW`` cannot widen the gate it is being judged by.
    # DEFENSE-IN-DEPTH, not the load-bearing guard: measured, moving this read
    # inside the loop STILL refuses, because ``AELIX_DOTENV_ALLOW`` is caught
    # independently by BOTH ``^AELIX_`` and ``_DOTENV_LOCKED`` and so never
    # reaches ``os.environ`` for a late read to observe. Kept because it makes
    # the ordering local and obvious instead of a fact the next reader has to
    # re-derive from two other constants.
    extra = _dotenv_user_allowlist()
    # The REAL environment, before a single line of this file was applied. The
    # sibling guard must not see keys we ourselves just wrote — one .env line is
    # not a "shell-supplied" value for the next one.
    before = dict(os.environ)
    credentials: list[str] = []
    config: list[str] = []
    hatched: list[str] = []
    refused: list[str] = []
    locked: list[str] = []
    gate: list[str] = []
    shadowed: list[tuple[str, str, str]] = []
    badvalue: list[tuple[str, str]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        admit: list[str]
        if key == _DOTENV_GATE:
            gate.append(key)
            continue
        if key in _DOTENV_LOCKED:
            locked.append(key)
            continue
        if key in _DOTENV_CONFIG_VALUES:
            # UNCONDITIONAL, and ahead of the hatch on purpose — the hatch names
            # a KEY, and for these names the danger is in the VALUE.
            rule = _DOTENV_CONFIG_VALUES[key]
            if not rule.shape.match(value):
                badvalue.append((key, rule.why))
                continue
            admit = config
        elif key in extra:
            # The hatch and the credential rule are an OR, not a partition: a
            # user may list a name the rule already admits. Report it as what it
            # IS, or the hatch notice's "these are not credentials" would be
            # false for exactly that case (measured: AELIX_DOTENV_ALLOW=
            # OPENAI_API_KEY). Naming the key in your own shell is also the
            # "I mean it" signal that bypasses the sibling guard below.
            admit = credentials if _dotenv_key_allowed(key) else hatched
        elif _dotenv_key_allowed(key):
            sibling = _dotenv_shadowed_sibling(key, before)
            if sibling is not None:
                shadowed.append((key, sibling[0], sibling[1]))
                continue
            admit = credentials
        else:
            refused.append(key)
            continue
        if key not in os.environ:
            os.environ[key] = value
            admit.append(key)
    _report_dotenv(
        p,
        credentials=credentials,
        config=config,
        hatched=hatched,
        refused=refused,
        locked=locked,
        gate=gate,
        shadowed=shadowed,
        badvalue=badvalue,
        foreign_precedence="GH_TOKEN" in credentials
        and bool(before.get("GITHUB_TOKEN")),
    )


def register_providers() -> None:
    """Register the built-in provider adapters (idempotent)."""

    _openai.register_all()
    _anthropic.register_all()
    # #15 Workflow B — un-hide the OpenAI Responses adapter (openai 42 +
    # github-copilot 7 + cloudflare-ai-gateway 16 + opencode 16). This surfaces
    # the previously-blocked ``openai-responses`` models in the /model picker;
    # auth resolves from env keys (OPENAI_API_KEY / COPILOT_GITHUB_TOKEN /
    # CLOUDFLARE_API_KEY / OPENCODE_API_KEY) via ``_resolve_client_api_key``.
    # cloudflare-ai-gateway carries a templated base_url whose
    # ``{CLOUDFLARE_ACCOUNT_ID}`` / ``{CLOUDFLARE_GATEWAY_ID}`` tokens are
    # expanded from the environment at client construction; until both are set
    # those models stay hidden (``runnable_models`` placeholder guard) instead
    # of failing at the first turn with a malformed URL.
    _openai_responses.register_all()
    # #15 / Phase B §4.1 item #6 — register the OpenAI **Codex** Responses
    # adapter (``openai-codex-responses``). Without it, the 10 ``openai-codex``
    # catalog models resolve auth via ChatGPT Plus/Pro OAuth (so they appear in
    # ``/scoped-models``) but ``partition_runnable`` HIDES them from the
    # ``/model`` picker because their ``api`` had no registered provider. This
    # is the fix for that split-visibility bug.
    _openai_codex_responses.register_all()
    # #15 Workflow B — un-hide the native Gemini adapters. ``google`` (Gemini
    # Developer API, ``google-generative-ai``) surfaces the 29 catalog models +
    # the 2 opencode-zen gemini models (provider=opencode, served via the
    # google-generative-ai protocol at ``opencode.ai/zen/v1/models/{id}``,
    # authenticating from ``OPENCODE_API_KEY``); a missing ``GEMINI_API_KEY``
    # gives a normal "no API key" error, so they surface unconditionally.
    # ``google-vertex`` surfaces its 15 catalog models, but ``runnable_models``
    # keeps them HIDDEN until GCP auth is resolvable (GOOGLE_CLOUD_API_KEY, or a
    # project + GOOGLE_CLOUD_LOCATION) — the cloudflare "never surface a model
    # that errors at turn-1 for missing required config" precedent.
    _google_generative_ai.register_all()
    _google_vertex.register_all()


def _registry_lookup(registry: Any, provider: str, model_id: str) -> Model | None:
    """Resolve ``model_id`` against the LIVE :class:`ModelRegistry`.

    The static catalog is a build-time snapshot. The registry additionally holds
    ``models.json`` custom providers and — once ``bind_model_registry`` has
    replayed them — extension ``register_provider`` models. Neither is knowable
    from the catalog, so without this lookup they resolve to ``api="unknown"``
    and raise the internal "No provider registered for api='unknown'" at the
    first turn (#98).

    An EMPTY ``provider`` is resolved across providers and accepted ONLY when
    exactly one provider serves ``model_id``. An owner guess would dispatch the
    turn — and the credentials with it — to whichever vendor sorted first: the
    bundled catalog alone serves ``gpt-5.4`` from six providers (openai,
    azure-openai-responses, github-copilot, opencode, openai-codex,
    cloudflare-ai-gateway). Ambiguity therefore stays unresolved on purpose and
    the caller's ``is_runnable`` gate points the user at ``/model``.

    A hit is returned VERBATIM, including one whose ``base_url`` is empty (an
    extension ``register_provider`` model can omit it; step 3b of
    ``ModelRegistry._load_models`` merges it without injecting a host). Such a
    model must NOT be dropped to "no match" here: :func:`_sibling_backfill` would
    then stamp the catalog's unanimous api over the api this provider's own
    registration declared, misrouting the turn on a second axis. It is instead
    caught downstream by ``core.runnable_models.is_runnable``, which refuses a
    hostless model precisely because the adapter would resolve it to its SDK's
    first-party vendor host (#98) — the same gate covers the ``/model`` picker,
    which hands registry models straight to ``set_model``.

    Introspection-only: an alternate registry lacking ``find`` / ``get_all``
    degrades to "no match" and must never break launch.
    """

    if registry is None or not model_id:
        return None
    try:
        if provider:
            return registry.find(provider, model_id)
        matches = [m for m in registry.get_all() if m.id == model_id]
        if len({m.provider for m in matches}) == 1:
            return matches[0]
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return None
    return None


def _sibling_backfill(provider: str, model_id: str) -> Model | None:
    """Backfill ``api``/``base_url`` for an uncatalogued id under a KNOWN provider.

    Lets a custom / newly-released id under a catalogued provider still reach an
    adapter. Only an UNANIMOUS sibling ``api`` is adopted: five catalog providers
    span several apis (github-copilot, opencode, cloudflare-ai-gateway,
    fireworks, opencode-go) and every one of them includes ``anthropic-messages``,
    so the previous ``siblings[0].api`` guess routed a github-copilot id to the
    ANTHROPIC adapter (its first sibling is claude-haiku-4.5). That adapter does
    ``base_url=model.base_url or None`` (``providers/anthropic.py``), collapsing
    the omitted base_url to the AsyncAnthropic default host — so a GitHub Copilot
    OAuth bearer left the process for ``api.anthropic.com`` (#98).

    A unanimous ``api`` means every sibling agrees this provider speaks that
    protocol, so the adapter choice cannot cross vendors. ``base_url`` is carried
    only when it too is unanimous, pinning the host explicitly rather than
    relying on an SDK default (amazon-bedrock is the one single-api provider with
    several base_urls — same vendor, different regions).
    """

    from aelix_ai.models import get_models

    siblings = get_models(provider)
    if not siblings:
        return None
    apis = {m.api for m in siblings}
    if len(apis) != 1:
        return None
    base_urls = {m.base_url for m in siblings}
    return Model(
        id=model_id,
        provider=provider,
        api=next(iter(apis)),
        base_url=siblings[0].base_url if len(base_urls) == 1 else "",
    )


# === #344 / ADR-0249: a ``--model`` OpenRouter cannot serve never reaches it ====
#
# The OpenRouter-from-env rung used to run FIRST, so with ``OPENROUTER_API_KEY``
# set EVERY ``--model`` string became an OpenRouter id. Measured on main
# ``fbead6e0`` through the real CLI (fake keys, a recording CONNECT proxy): a
# models.json provider's ``retryprobe/held-model`` went to ``openrouter.ai:443``
# (and an ``--api-key`` meant for that endpoint went with it as the bearer), an
# extension provider's ``extprov/m1`` likewise, and the owner's own
# ``ollama/qwen3.6:35b-a3b`` retried twelve times against OpenRouter instead of
# reaching ``127.0.0.1:11434``. The helpers below are the rung that now runs
# before it (``_openrouter_cannot_serve``); every one of them reads
# CONFIGURATION only — the catalog, models.json, extension registrations — and
# never a credential, so a vendor key in a cloned repo's ``.env`` (or a stale
# OAuth record) cannot change where a prompt goes.


def _catalogued_providers() -> frozenset[str]:
    """The providers this build's static catalog knows (35 on ``fbead6e0``)."""

    from aelix_ai.models import get_providers

    return frozenset(get_providers())


def openrouter_namespaces() -> frozenset[str]:
    """First path segments of the bundled catalog's OpenRouter ids, lower-cased.

    Derived, never listed: measured on ``fbead6e0`` there are 50 (``openai``,
    ``anthropic``, ``meta-llama``, ``x-ai``, ``~openai`` …), and 9 of them are
    also catalogued provider names — ``anthropic deepseek google minimax
    moonshotai nvidia openai openrouter xiaomi``. A prefix in this set can be an
    OpenRouter id (``openai/gpt-4o-mini``), so it keeps going to OpenRouter when
    the key is set; the other 26 catalogued providers (``openai-codex``,
    ``xai``, ``groq``, ``mistral`` …) cannot, so rung 0c resolves them in place.
    A catalog regeneration that adds an OpenRouter namespace equal to a provider
    name moves that prefix to OpenRouter at that build, with no code change —
    the release notes of that regeneration are where it shows.
    """

    from aelix_ai.models import get_models

    return frozenset(m.id.split("/", 1)[0].lower() for m in get_models("openrouter") if "/" in m.id)


def _registry_providers(registry: Any) -> frozenset[str]:
    """Every provider the registry serves a model for. Fail closed to empty."""

    if registry is None:
        return frozenset()
    try:
        return frozenset(m.provider for m in registry.get_all())
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return frozenset()


def user_defined_providers(registry: Any) -> frozenset[str]:
    """Providers whose endpoint the user chose (``ModelRegistry.get_user_defined_providers``).

    ``openrouter`` is removed even when models.json re-points it: an
    ``openrouter/<id>`` string is itself an OpenRouter id (``openrouter/auto``),
    so the OpenRouter rung — which adopts that ``baseUrl`` — keeps it.

    A registry that predates the accessor (tests, an embedder's duck-typed one)
    is read the only way it can be: whatever it serves that the catalog does not
    know was defined by someone other than this build. Introspection-only and
    fail-closed, like :func:`_registry_lookup`.
    """

    if registry is None:
        return frozenset()
    try:
        getter = getattr(registry, "get_user_defined_providers", None)
        if callable(getter):
            returned: Any = getter()
            found = frozenset(str(name) for name in returned)
        else:
            found = _registry_providers(registry) - _catalogued_providers()
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return frozenset()
    return found - {"openrouter"}


def canonical_provider(prefix: str, known: frozenset[str]) -> str | None:
    """``prefix`` as one of ``known``, matched exactly, else case-insensitively.

    pi canonicalises the prefix the same way (``providerMap``,
    ``packages/coding-agent/src/core/model-resolver.ts:430-433`` @ 1ff5b6fdd),
    and so does the in-session ``/model``. Before #344 the launch path split
    case-sensitively, so ``--model RetryProbe/held-model`` was refused as an
    unknown protocol (measured, C20). Two known names differing only in case
    make the match ambiguous: ``None``, never a guess.
    """

    if not prefix:
        return None
    if prefix in known:
        return prefix
    folded = [name for name in known if name.lower() == prefix.lower()]
    return folded[0] if len(folded) == 1 else None


def _user_defined_prefix(
    prefix: str, user_defined: frozenset[str]
) -> tuple[str | None, tuple[str, ...]]:
    """``prefix`` as a USER-DEFINED provider: ``(name, ())``, ``(None, clash)`` or ``(None, ())``.

    Codex cross-review of ``8c9d6397`` (C1): the case-insensitive match used to
    run over the catalogue and the user's providers TOGETHER and prefer an exact
    spelling, so with a models.json provider named ``OpenAI`` the string
    ``openai/m1`` matched the catalogue's ``openai`` exactly — not user-defined,
    and an OpenRouter namespace — and went to OpenRouter with the ``--api-key``
    meant for the user's endpoint as the bearer. A user-defined provider is now
    matched FIRST and on its own: the exact spelling, else the one user-defined
    name that differs only in case. pi lands the same way — its ``providerMap``
    is keyed by the lower-cased name and filled built-ins first, so the custom
    ``OpenAI`` is what ``openai/…`` finds
    (``packages/coding-agent/src/core/model-resolver.ts:430-433`` @ 1ff5b6fdd).
    Two user-defined names that differ only in case, neither spelled exactly,
    come back as ``clash`` (sorted): the caller refuses rather than guess, since
    either guess sends one provider's key to the other's host.
    """

    if not prefix or not user_defined:
        return None, ()
    if prefix in user_defined:
        return prefix, ()
    folded = sorted(name for name in user_defined if name.lower() == prefix.lower())
    if len(folded) == 1:
        return folded[0], ()
    return None, tuple(folded)


def ambiguous_provider_prefix(model_ref: str | None, registry: Any) -> tuple[str, ...]:
    """The user-defined providers a ``<prefix>/<id>`` could mean, when it is more than one.

    Empty unless ``prefix`` differs only in case from two or more user-defined
    providers and spells none of them exactly (models.json keys ``OpenAI`` and
    ``OPENAI``, or a custom ``OpenAI`` next to a re-pointed built-in ``openai``,
    and ``--model Openai/m1``). :func:`resolve_model` then holds the string on a
    model no turn runs (``api='unknown'``); this is what names both providers in
    the refusal (:func:`ambiguous_provider_message`).
    """

    if not model_ref:
        return ()
    prefix, sep, rest = model_ref.partition("/")
    if not (sep and rest):
        return ()
    _, clash = _user_defined_prefix(prefix, user_defined_providers(registry))
    return clash


def ambiguous_provider_message(model_ref: str | None, registry: Any) -> str | None:
    """The refusal for :func:`ambiguous_provider_prefix`, or ``None``."""

    clash = ambiguous_provider_prefix(model_ref, registry)
    if not clash or not model_ref:
        return None
    prefix = model_ref.partition("/")[0]
    names = " and ".join(f"'{name}'" for name in clash)
    return (
        f"--model {model_ref}: the provider prefix '{prefix}' matches the providers "
        f"you defined {names}, which differ only in case. Spell the prefix exactly "
        "as one of them."
    )


def _named_provider(
    name: str, registry: Any, user_defined: frozenset[str]
) -> tuple[str, tuple[str, ...]]:
    """An explicitly NAMED provider (``--provider``, ``defaultProvider``, a profile's) as known.

    Codex second pass on ``ebfe411a`` (F1): the slash prefix had the case rule
    and ``--provider`` did not, so with a models.json provider ``OpenAI``,
    ``--model openai/m1`` reached the user's endpoint while ``--model m1
    --provider openai --api-key K`` reached ``api.openai.com`` with ``K`` — the
    shadowing the prefix rule exists to prevent — and ``--provider OPENAI`` was
    ``api='unknown'``. The same rule as :func:`_user_defined_prefix`, then the
    catalogue: a user-defined provider first and on its own (the exact
    spelling, else the one user-defined name equal up to case), else any
    provider this build or the registry knows, matched as
    :func:`canonical_provider` does, else the name as typed. Returns
    ``(name, clash)``; ``clash`` (sorted) is non-empty when two user-defined
    names differ from ``name`` only in case and neither is spelled exactly —
    the caller holds the name as typed, never guessing either. Routing is
    untouched: a named provider still switches rung 0 and the OpenRouter rung
    off unless it IS ``openrouter``.
    """

    if not name:
        return name, ()
    canon, clash = _user_defined_prefix(name, user_defined)
    if canon is not None:
        return canon, ()
    if clash:
        return name, clash
    known = _catalogued_providers() | user_defined | _registry_providers(registry)
    return canonical_provider(name, known) or name, ()


def canonical_provider_name(name: str, registry: Any) -> str:
    """``name`` as :func:`resolve_model` matches a named provider — as typed on a clash."""

    return _named_provider(name, registry, user_defined_providers(registry))[0]


def ambiguous_provider_name(name: str | None, registry: Any) -> tuple[str, ...]:
    """The user-defined providers an explicitly named provider could mean, when more than one.

    :func:`ambiguous_provider_prefix` for ``--provider``, settings
    ``defaultProvider`` and a profile's ``provider`` (F1).
    """

    if not name:
        return ()
    _, clash = _named_provider(name, registry, user_defined_providers(registry))
    return clash


def ambiguous_provider_name_message(
    name: str | None, registry: Any, source: str = "--provider"
) -> str | None:
    """The refusal for :func:`ambiguous_provider_name`, or ``None``."""

    clash = ambiguous_provider_name(name, registry)
    if not clash or not name:
        return None
    names = " and ".join(f"'{provider}'" for provider in clash)
    return (
        f"{source} {name} matches the providers you defined {names}, which differ "
        "only in case. Spell it exactly as one of them."
    )


def ambiguous_route_message(
    model_flag: str | None,
    provider_flag: str | None,
    registry: Any,
    default_provider: str | None = None,
) -> str | None:
    """Why :func:`resolve_model` held a launch on ``api='unknown'`` for a case clash, if it did.

    The named provider's clash when ``--provider`` was given; else the slash
    prefix's; else settings ``defaultProvider``'s, when a bare id fell to it.
    """

    if provider_flag:
        return ambiguous_provider_name_message(provider_flag, registry)
    prefix_message = ambiguous_provider_message(model_flag, registry)
    if prefix_message is not None:
        return prefix_message
    if default_provider and not (model_flag and "/" in model_flag):
        return ambiguous_provider_name_message(
            default_provider, registry, source="settings defaultProvider"
        )
    return None


def _adopt_base_url_override(model: Model, registry: Any) -> Model:
    """A catalog model of a provider models.json re-pointed takes that ``baseUrl``.

    ADR-0249 (S). The registry's copy of a built-in model carries the
    models.json provider-level ``baseUrl`` (``load_built_in_models``), but the
    launch path returns the STATIC catalog entry before it asks the registry, so
    the override was ignored: measured on ``fbead6e0``, ``--provider openai
    --model gpt-4o-mini`` with ``providers.openai.baseUrl`` → a local proxy went
    to ``api.openai.com:443`` and the proxy saw nothing (C7). Only the host moves
    — the catalog ``api``, window, cost and thinking map stay (the
    :func:`enrich_copilot_base_url` shape), which is why
    ``test_resolve_model_catalog_hit_wins_over_registry`` (it pins the ``api``)
    stays as it is.
    """

    if registry is None:
        return model
    try:
        getter = getattr(registry, "get_base_url_override", None)
        override = getter(model.provider) if callable(getter) else None
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return model
    if isinstance(override, str) and override and override != model.base_url:
        return replace(model, base_url=override)
    return model


def _unanimous_backfill(siblings: list[Model], provider: str, model_id: str) -> Model | None:
    """``model_id`` built from ``siblings`` when they agree on one ``api``.

    ``base_url`` and ``compat`` are carried only when unanimous; a split
    ``base_url`` leaves it empty, which ``is_runnable`` then refuses rather than
    letting an SDK default host decide.
    """

    if not siblings:
        return None
    apis = {m.api for m in siblings}
    if len(apis) != 1:
        return None
    first = siblings[0]
    return Model(
        id=model_id,
        name=model_id,
        provider=provider,
        api=first.api,
        base_url=first.base_url if all(m.base_url == first.base_url for m in siblings) else "",
        compat=first.compat if all(m.compat == first.compat for m in siblings) else None,
    )


def _registry_sibling_backfill(registry: Any, provider: str, model_id: str) -> Model | None:
    """An id a USER-DEFINED provider does not list, backfilled from its own models.

    The user-defined analogue of :func:`_sibling_backfill` — same unanimity rule
    (#98: a first-sibling guess once sent a Copilot bearer to Anthropic), but
    over the REGISTRY's models for that provider, because the static catalog has
    none for a models.json or extension provider (:func:`_unanimous_backfill`).
    This is what lets ``ollama/<a model pulled after models.json was written>``
    reach the user's ollama instead of being refused — pi builds the same
    fallback from the provider's first model (``buildFallbackModel``,
    ``model-resolver.ts:175-189`` @ 1ff5b6fdd), without the unanimity guard.
    """

    if registry is None:
        return None
    try:
        siblings = [m for m in registry.get_all() if m.provider == provider]
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return None
    return _unanimous_backfill(siblings, provider, model_id)


def _registration_models(registry: Any, provider: str) -> list[Model] | None:
    """What an extension registration brings for a CATALOGUED provider it took over.

    ``None`` unless ``provider`` is a catalogued name that is user-defined ONLY
    because an extension's ``register_provider`` brought ``models`` for it (a
    ``models.json`` provider-level ``baseUrl`` moves the whole provider instead,
    decision 3). Such a registration is MERGED into the registry next to the
    catalog's own models, so the registry's ``openai`` still lists
    ``gpt-4o-mini`` at ``api.openai.com`` — but the provider-wide ``api_key`` is
    the extension's. Rung 0 therefore resolves such a prefix inside the
    REGISTRATION: the review measured ``openai/gpt-4o-mini`` with
    ``OPENROUTER_API_KEY`` set reaching ``api.openai.com`` with the extension's
    key (on ``fbead6e0`` it went to OpenRouter with the user's). pi replaces a
    provider's models on ``registerProvider`` with models, so there the catalog
    id is simply not found. Introspection-only and fail-closed.
    """

    if registry is None or provider not in _catalogued_providers():
        return None
    try:
        override = getattr(registry, "get_base_url_override", None)
        if callable(override) and override(provider):
            return None
        registrations = getattr(registry, "get_registered_providers", None)
        registered: Any = registrations() if callable(registrations) else None
        config = registered.get(provider) if isinstance(registered, Mapping) else None
        brought: Any = getattr(config, "models", None) if config is not None else None
        if not isinstance(brought, Mapping) or not brought:
            return None
        models: list[Model] = []
        for model in brought.values():
            # The registry's copy when it has one (an OAuth ``modify_models``
            # pass may have touched it), else the registration's own entry.
            live = registry.find(provider, model.id)
            models.append(live if live is not None else replace(model, provider=provider))
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return None
    return models or None


def _resolve_in_provider(
    provider: str,
    model_id: str,
    registry: Any,
    user_defined: frozenset[str],
) -> Model:
    """Resolve ``model_id`` INSIDE ``provider`` — never another provider.

    The explicit-provider tail: (a) an exact static-catalog hit, with a
    models.json ``baseUrl`` override adopted; (b) the registry's own entry (a
    models.json custom model, an extension model, an OAuth-modified copy); (c)
    for a user-defined provider, a backfill from its own registry models; (d)
    unanimous static siblings, override adopted; (e) a bare ``Model`` whose
    ``api`` stays ``"unknown"`` — which ``is_runnable`` refuses with a message
    naming the provider, so an id the provider does not serve is a clear
    refusal and never a silent fall-through to OpenRouter.
    """

    from aelix_ai.models import get_model

    catalog = get_model(provider, model_id)
    if catalog is not None:
        return _adopt_base_url_override(catalog, registry)
    found = _registry_lookup(registry, provider, model_id)
    if found is not None:
        return found
    if provider in user_defined:
        backfilled = _registry_sibling_backfill(registry, provider, model_id)
        if backfilled is not None:
            return backfilled
    backfilled = _sibling_backfill(provider, model_id)
    if backfilled is not None:
        return _adopt_base_url_override(backfilled, registry)
    # (e) — see the note at ``resolve_model``'s bare return.
    return Model(id=model_id, provider=provider)


def user_defined_route(model_ref: str, registry: Any) -> tuple[str, str] | None:
    """``(provider, id)`` when ``model_ref`` is a user-defined route (rungs 0a/0b).

    0a: the first-slash prefix names a user-defined provider (case-insensitive)
    → ``(canonical, rest)``. 0b: otherwise the WHOLE string is an id exactly one
    user-defined provider serves → ``(that provider, model_ref)``. ``None``
    otherwise. A catalogued provider an extension took over "serves" only the
    ids its registration brings (:func:`_registration_models`): the catalog's
    own ids next to them are not the user's.
    """

    user_defined = user_defined_providers(registry)
    if not user_defined or not model_ref:
        return None
    prefix, sep, rest = model_ref.partition("/")
    if sep and rest:
        # Matched against the user's providers ALONE (Codex C1): a catalogue
        # spelling must not win over a user-defined one that differs in case.
        canon, clash = _user_defined_prefix(prefix, user_defined)
        if canon is not None:
            return canon, rest
        if clash:
            # Ambiguous between two of the user's own providers: the prefix as
            # typed, which :func:`_openrouter_cannot_serve` holds unrunnable.
            return prefix, rest
    registered_ids: dict[str, frozenset[str]] = {}
    for name in user_defined:
        registered = _registration_models(registry, name)
        if registered is not None:
            registered_ids[name] = frozenset(m.id for m in registered)
    try:
        owners = {
            m.provider
            for m in registry.get_all()
            if m.id == model_ref
            and m.provider in user_defined
            and (m.provider not in registered_ids or m.id in registered_ids[m.provider])
        }
    except Exception:  # noqa: BLE001 — resolution must never break launch
        return None
    if len(owners) == 1:
        return next(iter(owners)), model_ref
    return None


def _openrouter_cannot_serve(model_ref: str, registry: Any) -> Model | None:
    """Rung 0 — a ``--model`` string OpenRouter-from-env must not take (ADR-0249).

    Runs only with ``OPENROUTER_API_KEY`` set, a non-empty ``--model`` and no
    ``--provider``; ``None`` hands the string to the OpenRouter rung unchanged.

    * 0a — the prefix names a USER-DEFINED provider → resolved inside it, and
      never falls through to OpenRouter, even for an id it does not list (a
      registry-sibling backfill, else a refusal). For a catalogued name an
      extension took over, "inside it" is inside the registration
      (:func:`_registration_models`).
    * 0b — the whole string is an id exactly ONE user-defined provider serves →
      that model (the owner's bare ``qwen3.6:35b-a3b`` from their ollama
      provider; a gateway listing ``openai/gpt-4o`` verbatim).
    * 0c — the prefix names a CATALOGUED provider that is not an OpenRouter
      namespace (:func:`openrouter_namespaces`) → resolved inside it:
      ``openai-codex/gpt-5.1`` used to reach OpenRouter and fail 400.

    Everything else — the 9 overlapping namespaces (``openai/…``,
    ``anthropic/…``), unknown prefixes, bare ids no user-defined provider
    serves — goes to OpenRouter exactly as before. No credential is read here:
    whether the user ALSO holds an OpenAI key does not decide ``openai/…``.
    """

    route = user_defined_route(model_ref, registry)
    user_defined = user_defined_providers(registry)
    if route is not None:
        provider, model_id = route
        if provider not in user_defined:
            # A prefix two user-defined providers share up to case (Codex C1):
            # refused (``api='unknown'``), never resolved in the catalogue's
            # same-spelled provider and never handed to OpenRouter.
            # :func:`ambiguous_provider_message` names both.
            return Model(id=model_id, provider=provider)
        registered = _registration_models(registry, provider)
        if registered is not None:
            # A catalogued name an extension took over: its registration's
            # entry, else a backfill from the registration's models, else a
            # refusal — never the catalog's vendor host with the extension's key.
            for model in registered:
                if model.id == model_id:
                    return model
            backfilled = _unanimous_backfill(registered, provider, model_id)
            return backfilled if backfilled is not None else Model(id=model_id, provider=provider)
        if model_id == model_ref:
            # 0b — the registry entry itself (it is exactly one provider's).
            found = _registry_lookup(registry, provider, model_id)
            if found is not None:
                return found
        return _resolve_in_provider(provider, model_id, registry, user_defined)
    prefix, sep, rest = model_ref.partition("/")
    if not (sep and rest):
        return None
    catalogued = _catalogued_providers()
    canon = canonical_provider(prefix, catalogued)
    if canon is None or canon.lower() in openrouter_namespaces():
        return None
    return _resolve_in_provider(canon, rest, registry, user_defined)


def resolve_model(
    model_flag: str | None,
    provider_flag: str | None,
    registry: Any = None,
    default_provider: str | None = None,
) -> Model:
    """Resolve the turn :class:`Model` from flags + env + the live registry.

    Resolution order (ADR-0249 amends ADR-0195 §Decision 4): (0) with
    ``OPENROUTER_API_KEY`` set, a ``--model`` (no ``--provider``) that
    OpenRouter cannot serve — a user-defined provider's prefix, an id only one
    user-defined provider serves, or a catalogued provider that is not an
    OpenRouter namespace — resolved inside that provider
    (:func:`_openrouter_cannot_serve`); (1) OpenRouter-from-env
    (``OPENROUTER_API_KEY`` + a model id, no conflicting ``--provider``); (2) an
    exact static-catalog hit for ``--provider``/``--model``, the
    ``<provider>/<model>`` slash shorthand (prefix matched case-insensitively),
    or ``default_provider`` — a models.json ``baseUrl`` override of that
    provider adopted; (3) ``registry`` — the models.json custom +
    extension-registered providers the build-time catalog cannot know
    (:func:`_registry_lookup`); (4) an uncatalogued id backfilled from unanimous
    siblings — the registry's for a user-defined provider, the catalog's
    otherwise; (5) a bare model whose ``api`` stays the ``Model`` default
    ``"unknown"``.

    ``provider_flag`` means "the user EXPLICITLY named this provider" (``--provider``)
    and NOTHING else — rung 0, the OpenRouter-env branch and the slash shorthand
    are all gated on its emptiness, so anything weaker must not be passed through
    it. ``default_provider`` (settings.json ``defaultProvider``) is that weaker
    signal and has its own, lowest-precedence slot below (#98).

    ``registry`` is optional (:data:`None` = catalog-only) because callers resolve
    at points where no registry exists yet; with none, rung 0 can still see the
    catalog (0c) but no user-defined provider. Outcome (5) CANNOT drive a turn,
    so callers MUST gate on ``core.runnable_models.is_runnable`` (#98) — see the
    note at the bare return.
    """

    user_defined = user_defined_providers(registry)
    clash: tuple[str, ...] = ()
    if provider_flag:
        # The prefix rule's case matching for a NAMED provider too (Codex second
        # pass on ``ebfe411a``, F1; :func:`_named_provider`): with a models.json
        # ``OpenAI``, ``--provider openai`` is the user's, never the vendor's
        # host with the user's ``--api-key``; ``--provider OPENAI`` resolves;
        # a case clash between two of the user's providers is held below. What
        # a named provider does to routing is unchanged — it still switches
        # rung 0 and the OpenRouter rung off unless it names ``openrouter``.
        provider_flag, clash = _named_provider(provider_flag, registry, user_defined)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key and model_flag and not provider_flag:
        # (0) Only ``model_flag``: an id that came from ``OPENROUTER_DEFAULT_MODEL``
        # was named FOR OpenRouter and stays an OpenRouter id.
        claimed = _openrouter_cannot_serve(model_flag, registry)
        if claimed is not None:
            return claimed
    model_id = model_flag or os.environ.get("OPENROUTER_DEFAULT_MODEL")
    if openrouter_key and model_id and (provider_flag in (None, "", "openrouter")):
        # Enrich from the Pi catalog when the id is known: a bare Model has
        # ``context_window=0`` / ``max_tokens=0`` / empty cost, which silently
        # disables the context-usage meter (``getContextUsage`` returns None
        # when the window is 0), zeroes ``/cost``, and drops the model's
        # ``thinking_level_map``. The full catalog entry carries all of these.
        # Falls back to a bare model for ids absent from the catalog (custom /
        # newly-released OpenRouter models). Honors a custom OPENROUTER_BASE_URL
        # first, then a models.json ``providers.openrouter.baseUrl`` (ADR-0249 S:
        # the same override every other catalog hit adopts).
        from aelix_ai.models import get_model

        catalog = get_model("openrouter", model_id)
        env_base_url = os.environ.get("OPENROUTER_BASE_URL")
        if catalog is not None:
            if env_base_url:
                return replace(catalog, base_url=env_base_url)
            return _adopt_base_url_override(catalog, registry)
        uncatalogued = Model(
            id=model_id,
            provider="openrouter",
            api=OPENAI_COMPLETIONS_API,
            base_url=env_base_url or _DEFAULT_OPENROUTER_BASE_URL,
        )
        if env_base_url:
            return uncatalogued
        return _adopt_base_url_override(uncatalogued, registry)
    # Explicit --provider/--model path. Three enrichments over the old bare
    # ``Model(id, provider)`` return, which left ``api="unknown"`` (streaming.py
    # Model default) and so made the stream loop raise the internal
    # ``No provider registered for api='unknown'. Sprint 6a ... register_all()``
    # error for the documented flagship commands (e.g.
    # ``aelix --provider anthropic --model claude-sonnet-4-6 -p hi``):
    #
    #  1. ``<provider>/<model>`` slash shorthand — split it when no separate
    #     ``--provider`` was given (Pi ``resolveModelFromCli`` main.ts:303-304),
    #     so ``aelix --model openai/gpt-4o-mini`` resolves ``provider=openai``
    #     instead of falling through with an empty provider ("No model selected").
    #     With an ``OPENROUTER_API_KEY`` set, only a string rung 0 declined
    #     reaches the OpenRouter branch above, and ``openai/gpt-4o-mini`` is one
    #     of them (``openai`` is an OpenRouter namespace) — it never reaches
    #     here. The prefix is matched case-insensitively against every provider
    #     this build or the user knows (#344, C20).
    #  2. ``default_provider`` — see below; strictly weaker than both 1 and the
    #     OpenRouter branch, so it is applied only after they decline.
    #  3. Catalog enrichment — resolve the full Pi catalog entry (carrying the
    #     real ``api``, context window, cost, thinking map), then the live
    #     registry, then unanimous siblings. Catalogued ids are always exact; the
    #     later steps are the best-effort tail for ids the catalog never saw.
    provider = provider_flag or ""
    resolved_id = model_flag or ""
    if not provider and "/" in resolved_id:
        prefix, _, resolved_id = resolved_id.partition("/")
        # A user-defined provider first and on its own (Codex C1, as in rung
        # 0a): without an OpenRouter key, a custom ``OpenAI`` used to lose
        # ``openai/m1`` to the catalogue's ``openai`` — the vendor's host. A
        # case clash between two of the user's providers keeps the prefix as
        # typed and is refused below — never resolved in a same-spelled
        # catalogue provider, whose host would get the user's key.
        canon, clash = _user_defined_prefix(prefix, user_defined)
        if canon is None and not clash:
            known = _catalogued_providers() | user_defined | _registry_providers(registry)
            canon = canonical_provider(prefix, known)
        provider = canon or prefix
    # (2) settings.json ``defaultProvider`` — the LOWEST-precedence provider
    # source, applied only once every stronger signal has declined. It is a
    # SEPARATE parameter, never folded into ``provider_flag``, because the
    # OpenRouter branch and the slash split are both gated on that flag being
    # empty: a persisted default routed through it silently disables them —
    # ``--model openai/gpt-4o-mini`` ignores its own ``openai/`` prefix and an
    # ``OPENROUTER_API_KEY`` user is locked out of OpenRouter. Both then land on
    # a DIFFERENT vendor holding an id it never heard of, and both still satisfy
    # ``is_runnable`` (the default provider's own api backfills cleanly), so no
    # downstream gate can catch it (#98).
    if not provider and default_provider:
        # Named, so matched as ``--provider`` is (F1): a case clash is held.
        provider, clash = _named_provider(default_provider, registry, user_defined)
    if resolved_id:
        if clash:
            return Model(id=resolved_id, provider=provider)
        if provider:
            return _resolve_in_provider(provider, resolved_id, registry, user_defined)
        found = _registry_lookup(registry, provider, resolved_id)
        if found is not None:
            return found
    # Bare model — ``api`` stays the ``Model`` default "unknown": no catalog
    # entry, no registry entry, and no unanimous sibling api to adopt. Driving a
    # turn with it raises the internal "No provider registered for api='unknown'"
    # from the PROTECTED ``aelix_ai.api_registry``, so every caller must first
    # gate on ``core.runnable_models.is_runnable``: print/json refuses the run,
    # the TUI warns at startup and points at ``/model``.
    #
    # That gate CANNOT be a ``not model.provider`` emptiness check: an
    # uncatalogued provider (a models.json custom, an extension
    # ``register_provider``, or a plain typo) is non-empty and sails straight
    # past it into the raw adapter error (#98).
    return Model(id=resolved_id, provider=provider)


def enrich_copilot_base_url(model: Model, registry: Any) -> Model:
    """Adopt the registry's proxy-ep ``base_url`` for a github-copilot turn model.

    :func:`resolve_model` (→ :func:`aelix_ai.models.get_model`) returns the RAW
    catalog entry whose ``base_url`` is the STATIC default host
    ``https://api.individual.githubcopilot.com``. The token-derived proxy-ep host
    (which DIFFERS for GitHub Copilot Business/Enterprise seats) is injected only
    by ``OAuthProvider.modify_models`` inside :meth:`ModelRegistry._load_models`,
    so it reaches only the interactive ``/model`` picker — every non-picker path
    (CLI ``--print``, TUI startup/default, ``/model <id>``) dispatches to the
    static individual host. On an individual account that host coincidentally
    equals the proxy-ep so the bug is invisible; on an enterprise/business seat
    whose ``proxy-ep=`` names a different host, the request hits the WRONG host →
    httpx "Connection error".

    This adopts the registry copy's ``base_url`` (already modify_models-injected,
    because the registry is built AFTER ``auth_storage.load()``) for
    github-copilot models only, leaving every other provider — including
    OpenRouter's env ``OPENROUTER_BASE_URL`` override baked into ``model`` — intact.
    A ``registry`` miss (uncatalogued id) or a missing registry falls back to the
    input model unchanged.
    """

    if registry is None or getattr(model, "provider", None) != "github-copilot":
        return model
    found = registry.find(model.provider, model.id)
    if found is not None and found.base_url and found.base_url != model.base_url:
        return replace(model, base_url=found.base_url)
    return model


def late_registered_route(
    model_flag: str | None,
    provider_flag: str | None,
    current_model: Any,
    model_registry: Any,
) -> str | None:
    """Why the launch model went to OpenRouter although the ``--model`` is now the user's.

    #344 / ADR-0249 §2.3. X1 resolves the launch model after the extensions'
    ``setup()`` registrations are replayed, but a provider registered in a
    ``session_start`` handler arrives later still — inside
    ``create_agent_session_runtime``, after the first build. With
    ``OPENROUTER_API_KEY`` set, ``--model sessext/m1`` was therefore taken by the
    OpenRouter rung and the prompt went to ``openrouter.ai`` (review of
    ``0fcc3333``: 12 × ``CONNECT openrouter.ai:443``), while the print-mode #98
    gate in ``cli/entry.py`` re-resolved AFTER ``session_start``, found ``sessext`` and passed
    the run. pi resolves at the same point but has no OpenRouter rung, so there
    the same launch is an error, not a leak.

    Returns the message when the harness is on ``openrouter``, the string came
    from ``--model`` (not ``OPENROUTER_DEFAULT_MODEL``, not with ``--provider``)
    and a re-resolve over the registry as it is NOW lands on a user-defined
    provider; ``None`` otherwise. Reads no credential beyond the one the rung
    itself is gated on. Also when the re-resolve is HELD because the prefix now
    matches two late-registered providers that differ only in case (Codex
    second pass on ``ebfe411a``, F3: ``session_start`` registering ``SessExt``
    and ``SESSEXT``, ``--model sessext/m1`` — print refused it at its #98 gate,
    but RPC started on ``openrouter sessext/m1`` and sent the prompt there,
    because the held spelling is in neither set): the caller then holds the
    harness in every mode (:func:`ambiguous_provider_message` names both).

    A provider registered and then unregistered before ``session_start``
    returns is not in the registry as it is NOW, so an unknown prefix goes to
    OpenRouter by the owner's rule (F2, ADR-0249 §2.3) — nothing to catch here.

    What the caller does with it: every mode switches the harness through the
    ``/model`` path
    (:func:`~aelix_coding_agent.cli.model_switch.switch_to_late_registered_route`)
    — interactive and RPC since fix round 2 (they used to warn and then send the
    first prompt to OpenRouter), print and json since round 3 (they used to
    refuse with this message). The message itself is now only the reason text of
    the trigger; nothing prints it.
    """

    if not (os.environ.get("OPENROUTER_API_KEY") and model_flag and not provider_flag):
        return None
    if getattr(current_model, "provider", "") != "openrouter":
        return None
    try:
        now = resolve_model(model_flag, None, model_registry)
        user_defined = user_defined_providers(model_registry)
    except Exception:  # noqa: BLE001 — a diagnostic must never break launch
        return None
    ambiguous = ambiguous_provider_message(model_flag, model_registry)
    if ambiguous is not None:
        return ambiguous
    if not now.provider or now.provider == "openrouter" or now.provider not in user_defined:
        return None
    return (
        f"--model {model_flag} names provider '{now.provider}', which was registered "
        "after the launch model was chosen (in a session_start handler), so this run "
        "resolved it as an OpenRouter model id and would send the prompt to OpenRouter. "
        f"Register '{now.provider}' in the extension's setup() instead."
    )


__all__ = [
    "ambiguous_provider_message",
    "ambiguous_provider_name",
    "ambiguous_provider_name_message",
    "ambiguous_provider_prefix",
    "ambiguous_route_message",
    "canonical_provider",
    "canonical_provider_name",
    "enrich_copilot_base_url",
    "late_registered_route",
    "load_dotenv",
    "openrouter_namespaces",
    "register_providers",
    "resolve_model",
    "user_defined_providers",
    "user_defined_route",
]
