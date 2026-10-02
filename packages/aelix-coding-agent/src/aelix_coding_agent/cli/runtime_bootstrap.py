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
- :func:`resolve_route` / :func:`resolve_model` — resolve the :class:`Model` to
  drive a turn from the flags, the static catalog and (optionally) the live
  ``ModelRegistry``, in pi's ``resolveCliModel`` order (ADR-0250, #362): an
  explicit ``--provider``, else a known ``<provider>/`` prefix, else the whole
  string as an id across every provider, with the credential the user holds
  breaking ties — and two guards. Guard 1: a credential a cwd ``.env`` supplied
  never chooses a route (it still authenticates one). Guard 2: an id this
  build's catalogue cannot place goes to OpenRouter only on an OpenRouter key of
  the user's own. An unresolvable string comes back as a placeholder whose
  ``api`` stays ``"unknown"`` and the route carries pi's error text, so callers
  gate on ``core.runnable_models.is_runnable`` (#98). ``OPENROUTER_API_KEY`` is
  no longer a route switch on its own; ``openrouter/<id>``,
  ``--provider openrouter`` and a shell ``OPENROUTER_DEFAULT_MODEL`` are the
  explicit routes there.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple

from aelix_ai.providers import anthropic as _anthropic
from aelix_ai.providers import google_generative_ai as _google_generative_ai
from aelix_ai.providers import google_vertex as _google_vertex
from aelix_ai.providers import openai_codex_responses as _openai_codex_responses
from aelix_ai.providers import openai_completions as _openai
from aelix_ai.providers import openai_responses as _openai_responses
from aelix_ai.streaming import Model

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
    # ``OPENROUTER_DEFAULT_MODEL`` used to be the sixth name here, "bounded to
    # model choice". #362 (ADR-0250) took it out: a model choice IS a route
    # choice — it was the one value in this file that sent a prompt to
    # OpenRouter by itself — and a project ``.env`` may authenticate a route but
    # never choose one. It is read from your shell only now (or through
    # ``AELIX_DOTENV_ALLOW``, which names it yourself).
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
    badname: list[str] | None = None,
    record: list[str] | None = None,
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
    if badname:
        # #362 — a key that is not a plain environment name. Nothing consumes
        # one, and the provenance record is comma-joined: a key spelled
        # ``X,ANTHROPIC_API_KEY`` was admitted and then read back as the
        # user's EXPORTED ``ANTHROPIC_API_KEY``. No hatch remedy is offered:
        # ``AELIX_DOTENV_ALLOW`` is itself comma-separated and cannot name it.
        print(
            f"Notice: ignored {_names(badname)} from {p} — these are not "
            "environment variable names (letters, digits and '_', not starting "
            "with a digit).",
            file=sys.stderr,
        )
    if record:
        print(
            f"Notice: ignored {_names(record)} from {p} — aelix writes it to "
            "record which credentials came from a project .env; a .env cannot "
            "set it.",
            file=sys.stderr,
        )
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

    from aelix_coding_agent.core.dotenv_provenance import (
        DOTENV_ADMITTED_ENV,
        ENV_NAME,
        dotenv_admitted_names,
        env_name,
        format_record,
    )

    p = Path(path)
    if not p.exists():
        # An inherited record (a delegated child, a nested aelix) stays as it is.
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
    badname: list[str] = []
    record: list[str] = []
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
        if not ENV_NAME.match(key):
            # FIRST, ahead of every other arm and the hatch (#362, ADR-0250).
            badname.append(key)
            continue
        if env_name(key) == DOTENV_ADMITTED_ENV:
            # The provenance record (#362). Its own branch, ahead of the hatch:
            # ``^AELIX_`` refuses it only until ``AELIX_DOTENV_ALLOW`` names it
            # (measured on ``9ca53a4f``: admitted), and a .env that could write
            # it could mark the user's exported key as planted, or erase the
            # mark on its own. Compared in the OS spelling: on Windows any case
            # of this name is the same variable.
            record.append(key)
            continue
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
    # #362 / ADR-0250 guard 1 — record every name this file supplied, so a
    # route-deciding judgement can leave it out (``ModelRegistry.has_route_auth``)
    # in this process and in every child that inherits the environment. UNION
    # with the inherited record, never overwrite: a delegated child started in
    # the same cwd re-reads this .env, admits nothing (the keys are already set),
    # and an overwrite would erase the mark and make the planted key "exported"
    # again. Recorded in the OS spelling (``env_name``), which on Windows is the
    # upper-cased name the line actually set.
    supplied = [*credentials, *config, *hatched]
    if supplied:
        inherited = dotenv_admitted_names(before)
        os.environ[DOTENV_ADMITTED_ENV] = format_record([*inherited, *supplied])
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
        badname=badname,
        record=record,
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


# === Which providers a ``--model`` string can name (#344 ADR-0249, #362 ADR-0250) ==
#
# #344 measured an OpenRouter-from-env rung taking EVERY ``--model`` string —
# on main ``fbead6e0``, through the real CLI (fake keys, a recording CONNECT
# proxy), a models.json provider's ``retryprobe/held-model``, an extension's
# ``extprov/m1`` and the owner's own ``ollama/qwen3.6:35b-a3b`` all went to
# ``openrouter.ai:443``. These helpers answer, from CONFIGURATION only (the
# catalog, models.json, extension registrations), which providers exist, which
# of them the user defined, and how a prefix is spelled; :func:`resolve_route`
# below (ADR-0250, pi's order) is where credentials enter, through guard 1.


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
    OpenRouter id (``openai/gpt-4o-mini``), so ADR-0250's guard 2 may send such
    a string to OpenRouter when the user holds no key of their own for that
    vendor and does hold an OpenRouter one; the other 26 catalogued providers
    (``openai-codex``, ``xai``, ``groq``, ``mistral`` …) cannot be, so a string
    under them never leaves them. A catalog regeneration that adds an OpenRouter
    namespace equal to a provider name widens guard 2 at that build, with no
    code change — the release notes of that regeneration are where it shows.
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
    ``openrouter/<id>`` string is an explicit OpenRouter route
    (``openrouter/auto``), which adopts that ``baseUrl`` like any re-pointed
    built-in, and "user-defined" would only keep it from guard 2's checks.

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


def _own_endpoint_providers(registry: Any, user_defined: frozenset[str]) -> frozenset[str]:
    """Providers whose endpoint the user chose — :func:`user_defined_providers` plus a
    re-pointed ``openrouter``.

    Step 2's ``defaultProvider`` rule (ADR-0250 §2.1) asks "is this the user's
    own endpoint": a models.json provider, an extension's, a re-pointed
    built-in. :func:`user_defined_providers` drops ``openrouter`` for the
    prefix rule's sake only; a ``openrouter`` that models.json re-points is the
    user's endpoint like any other re-pointed built-in (``/model``'s
    ``_case_rule_providers`` reads it the same way). A project file can NAME one
    of these, but it cannot define one: models.json and extensions live outside
    the repo or behind project trust.
    """

    getter = getattr(registry, "get_user_defined_providers", None) if registry else None
    if callable(getter):
        try:
            returned: Any = getter()
            if "openrouter" in frozenset(str(name) for name in returned):
                return user_defined | {"openrouter"}
        except Exception:  # noqa: BLE001 — resolution must never break launch
            return user_defined
    return user_defined


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
    the caller holds the name as typed, never guessing either. A named
    provider is the route (ADR-0250 step E): no swap, no raw fallback, no
    guard 2 — only the ``<provider>/`` strip pi tolerates.
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
    the extension's. The launch therefore resolves such a prefix inside the
    REGISTRATION (and the registration's models replace the catalogue's in the
    set a ``--model`` string can name): the #344 review measured
    ``openai/gpt-4o-mini`` with
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
    # (e) — ``api`` stays "unknown": no catalog entry, no registry entry, no
    # unanimous sibling api. Driving a turn with it raises the internal
    # "No provider registered for api='unknown'", so every caller gates on
    # ``core.runnable_models.is_runnable`` first (#98) — which names the
    # provider, so a typo is a refusal and never a fall-through elsewhere.
    return Model(id=model_id, provider=provider)


# === #362 / ADR-0250: pi's ``resolveCliModel`` order, with two guards ==========
#
# ADR-0249 put a credential-blind rung 0 in front of an OpenRouter-from-env rung
# that turned every other ``--model`` into an OpenRouter id whenever
# ``OPENROUTER_API_KEY`` was set — from ANY source. #362 measured what that
# costs: with ``ANTHROPIC_API_KEY`` exported and a cloned repo's ``.env``
# carrying ``OPENROUTER_API_KEY``, ``--model anthropic/claude-haiku-4-5`` went to
# ``openrouter.ai:443`` on the file's key (``/tmp/362-work/design/live/today.out``
# A11). The owner's decision (2026-10-02) replaces both rungs with pi's order,
# ``resolveCliModel`` (``packages/coding-agent/src/core/model-resolver.ts:406-606``
# @ 88ff80b98), exact ids only (no fuzzy match, no ``:thinking`` suffix), and two
# guards:
#
# GUARD 1 — a credential a cwd ``.env`` supplied never takes part in a judgement
#   that CHOOSES a route (pi's auth tie-break :470-504, its swap :525-540, the
#   raw fallback :548-555, and guard 2). It still authenticates the route once
#   chosen. npm pi reads no ``.env`` at all, so pi never faces this; aelix does
#   (ADR-0203). :func:`route_authenticated` is the predicate.
# GUARD 2 — pi refreshes newer ids from pi.dev every 4 h
#   (``core/remote-catalog-provider.ts``); aelix has only this build's snapshot,
#   which misses ~45% of live OpenRouter ids (#136). So a ``<vendor>/<model>``
#   this build cannot place goes to OpenRouter as written — only on an
#   OpenRouter credential of the user's own, never under a user-defined prefix,
#   never under a catalogued provider OpenRouter cannot be (``xai/…``).


@dataclass(frozen=True)
class ResolvedRoute:
    """What :func:`resolve_route` decided for one ``--model``/``--provider`` pair.

    ``model`` is always set: on ``error`` it is a placeholder no turn runs
    (``api='unknown'``), shaped so the late-registration path can re-resolve it.
    ``kind`` names the step that decided (for tests, sabotage and the child
    pin); ``warning`` is the one line printed at launch (a custom id, guard 2).
    """

    model: Model
    kind: str
    error: str | None = None
    warning: str | None = None


def route_authenticated(registry: Any, provider: str, *, runtime_overrides: bool = True) -> bool:
    """Guard 1 — does ``provider`` hold a credential the user configured outside a cwd ``.env``?

    :meth:`ModelRegistry.has_route_auth` when the registry has it. Without a
    registry, only the environment layer exists, with the ``.env`` names taken
    out. A duck-typed registry (an embedder's, a test double) gets its
    ``has_configured_auth`` — except when the provider's environment layer is
    held only by ``.env`` names, which that predicate cannot see past, so it
    fails CLOSED (#362 critique S3: falling back to it unconditionally let the
    record be ignored).
    """

    if not provider:
        return False
    if registry is not None:
        getter = getattr(registry, "has_route_auth", None)
        if callable(getter):
            try:
                return bool(getter(provider, runtime_overrides=runtime_overrides))
            except Exception:  # noqa: BLE001 — resolution must never break launch
                return False
    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    from aelix_coding_agent.core.dotenv_provenance import dotenv_admitted_names, env_name

    admitted = dotenv_admitted_names()
    present = [n for n in ENV_API_KEYS.get(provider, ()) if os.environ.get(n)]
    if any(env_name(n) not in admitted for n in present):
        return True
    if registry is None or present:
        return False
    return _configured_auth(registry, provider)


def holds_route_auth(registry: Any, *, runtime_overrides: bool = True) -> bool:
    """Guard 1's switch: does the user hold a route-authenticating credential of their own anywhere?

    While this is True, every implicit chooser (``/model <arg>``'s match, the
    post-``/login`` pick, RPC ``cycle_model``) sets aside the providers only a
    cwd ``.env`` authenticates (ADR-0250 §2.2, §2.8, §2.10). It does not read
    model rows: a credential of the user's own on a provider that serves no
    model counts too (Codex's second cross-review of ``a0edf615``, F3 —
    ``get_available()`` missed an ``auth.json`` key for a provider registered
    with no models, and the session then counted as ``.env``-only). The
    candidates are :meth:`ModelRegistry.route_auth_candidates` (model rows,
    ``auth.json`` entries, runtime overrides, models.json providers and
    extension registrations, models or not) plus every provider an environment
    key name belongs to; a duck-typed registry contributes the providers it
    serves. Each is asked :func:`route_authenticated`, whose failure counts as
    not route-authenticated (fail-closed), as before. ``runtime_overrides=False``
    leaves ``--api-key`` out, as :func:`route_authenticated` does (a delegated
    child never receives it).

    An installed fallback resolver (``AuthStorage.set_fallback_resolver``,
    :meth:`ModelRegistry.has_fallback_resolver`) counts by itself: a resolver is
    a function, and the providers it answers for cannot be listed, so a provider
    only it authenticates is in no candidate set (Codex's third cross-review of
    #362, C2: an embedder's resolver answering for ``private-seat``, which has no
    rows, registration or stored entry - held was False, and with a ``.env``
    ``OPENAI_API_KEY`` ``/model openai/gpt-4o-mini`` went to ``api.openai.com``
    on the file's key). Failing toward the guard has a cost, stated in
    ADR-0250 §2.8 and §6: an embedder that installs a resolver and holds only
    ``.env`` credentials gets the strict guard's refusals, not the residual.
    The stock CLI installs none.
    """

    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    if registry is not None:
        has_fallback = getattr(registry, "has_fallback_resolver", None)
        try:
            if callable(has_fallback) and has_fallback():
                return True
        except Exception:  # noqa: BLE001 — an unreadable registry adds nothing
            pass
    names: set[str] = set(ENV_API_KEYS)
    if registry is not None:
        getter = getattr(registry, "route_auth_candidates", None)
        sources: list[Any] = [getter] if callable(getter) else []
        sources += [getattr(registry, "get_all", None), getattr(registry, "get_available", None)]
        for source in sources:
            if not callable(source):
                continue
            try:
                found: Any = source()
                for item in found:
                    name = item if isinstance(item, str) else getattr(item, "provider", "")
                    if name:
                        names.add(str(name))
            except Exception:  # noqa: BLE001 — a source that fails adds no names
                continue
    return any(
        route_authenticated(registry, name, runtime_overrides=runtime_overrides)
        for name in sorted(names)
    )


def _configured_auth(registry: Any, provider: str) -> bool:
    """Auth from ANY source, ``.env`` included — the "can this route run" question.

    A duck-typed registry without ``has_configured_auth`` is read through what
    it offers: ``get_available()`` is the auth-filtered list by contract.
    """

    if registry is None:
        from aelix_ai.providers._env_api_keys import get_env_api_key

        return bool(get_env_api_key(provider))
    try:
        checker = getattr(registry, "has_configured_auth", None)
        if callable(checker):
            return bool(checker(Model(id="", provider=provider)))
        return any(m.provider == provider for m in registry.get_available())
    except Exception:  # noqa: BLE001
        return False


def _dotenv_only_names(registry: Any, provider: str) -> list[str]:
    """The ``.env`` names that are the only reason ``provider`` counts as configured."""

    if route_authenticated(registry, provider) or not _configured_auth(registry, provider):
        return []
    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    from aelix_coding_agent.core.dotenv_provenance import dotenv_admitted_names, env_name

    admitted = dotenv_admitted_names()
    names = [
        n for n in ENV_API_KEYS.get(provider, ()) if os.environ.get(n) and env_name(n) in admitted
    ]
    try:
        configs = getattr(registry, "_provider_request_configs", None)
        config = configs.get(provider) if isinstance(configs, Mapping) else None
        key = getattr(config, "api_key", None)
        if isinstance(key, str) and os.environ.get(key) and env_name(key) in admitted:
            names.append(key)
    except Exception:  # noqa: BLE001
        pass
    return sorted(set(names))


def _route_universe(registry: Any) -> list[Model]:
    """Every model a ``--model`` string can name: pi's ``modelRuntime.getModels()``.

    The registry's models (catalogue, models.json, extension registrations),
    plus the catalogue of any provider the registry serves nothing for (a
    duck-typed registry; for :class:`ModelRegistry` this adds nothing). A
    catalogued provider an extension took over contributes only its
    registration's models (:func:`_registration_models`, #365 scope) — pi
    replaces a provider's models on ``registerProvider`` the same way.
    """

    from aelix_ai.models import get_models, get_providers

    models: list[Model] = []
    if registry is not None:
        try:
            models = list(registry.get_all())
        except Exception:  # noqa: BLE001 — resolution must never break launch
            models = []
    served = {m.provider for m in models}
    models += [m for p in get_providers() if p not in served for m in get_models(p)]
    for name in sorted(user_defined_providers(registry)):
        registered = _registration_models(registry, name)
        if registered is not None:
            models = [m for m in models if m.provider != name] + list(registered)
    return models


def _launch_shape(model: Model, registry: Any) -> Model:
    """A catalogued hit as the launch returns it: the catalogue entry, host adopted (S).

    Whatever step found it — a swap or raw match reads the registry's copy —
    so every catalogued route has one shape (ADR-0249 §2.4; the full
    composition is #363's). Registration and registry-only models as they are.
    """

    if _registration_models(registry, model.provider) is not None:
        return model
    from aelix_ai.models import get_model

    catalog = get_model(model.provider, model.id)
    if catalog is not None:
        return _adopt_base_url_override(catalog, registry)
    return model


def _find_in(provider: str, model_id: str, registry: Any) -> Model | None:
    """``model_id`` exactly, inside ``provider`` only (pi :514-517, minus fuzzy matching)."""

    registered = _registration_models(registry, provider)
    if registered is not None:
        return next((m for m in registered if m.id == model_id), None)
    from aelix_ai.models import get_model

    catalog = get_model(provider, model_id)
    if catalog is not None:
        return _adopt_base_url_override(catalog, registry)
    return _registry_lookup(registry, provider, model_id)


def _custom_in(provider: str, model_id: str, registry: Any, user_defined: frozenset[str]) -> Model:
    """pi's ``buildFallbackModel`` (:570-597), with aelix's tail: never another provider."""

    registered = _registration_models(registry, provider)
    if registered is not None:
        backfilled = _unanimous_backfill(registered, provider, model_id)
        return backfilled if backfilled is not None else Model(id=model_id, provider=provider)
    return _resolve_in_provider(provider, model_id, registry, user_defined)


def _custom_warning(provider: str, model_id: str, model: Model) -> str | None:
    """pi's custom-id warning (:593-594), only for a model a turn can run."""

    if model.api == "unknown":
        return None
    return f'Model "{model_id}" not found for provider "{provider}". Using custom model id.'


def _openrouter_base(model: Model) -> Model:
    """``OPENROUTER_BASE_URL`` (shell, or hatched) on every route that lands on OpenRouter."""

    base_url = os.environ.get("OPENROUTER_BASE_URL")
    if model.provider == "openrouter" and base_url:
        return replace(model, base_url=base_url)
    return model


def _matches(
    universe: list[Model], strings: frozenset[str], *, ids_only: bool = False
) -> list[Model]:
    """Models whose id (or ``provider/id``) is one of ``strings``, each pair once, in order."""

    seen: set[tuple[str, str]] = set()
    found: list[Model] = []
    for m in universe:
        hit = m.id in strings or (not ids_only and f"{m.provider}/{m.id}" in strings)
        if hit and (m.provider, m.id) not in seen:
            seen.add((m.provider, m.id))
            found.append(m)
    return found


def _ambiguous_message(model_ref: str, hits: list[Model], registry: Any, overrides: bool) -> str:
    """pi's ambiguity error (:493-501), naming the ``.env`` keys that did not count."""

    listed = ", ".join(sorted({f"{m.provider}/{m.id}" for m in hits}))
    trusted = [
        m for m in hits if route_authenticated(registry, m.provider, runtime_overrides=overrides)
    ]
    hint = (
        "No matching provider is authenticated."
        if not trusted
        else "More than one matching provider is authenticated."
    )
    names = sorted({n for m in hits for n in _dotenv_only_names(registry, m.provider)})
    if names:
        hint += (
            f" ({', '.join(_sanitize(n) for n in names)} came from a project .env, which "
            "does not choose between providers.)"
        )
    return f'Model "{model_ref}" is ambiguous across providers: {listed}. {hint} Use --provider or provider/model.'


def _default_aside(provider: str, registry: Any) -> str:
    """Why step 2 set settings ``defaultProvider`` aside (ADR-0250 §2.1), for the refusal."""

    message = (
        f' Settings defaultProvider "{_sanitize(provider)}" was not used: no credential of '
        "your own authenticates it, and a project .aelix/settings.json can set it"
    )
    names = _dotenv_only_names(registry, provider)
    if names:
        message += f" ({', '.join(_sanitize(n) for n in names)} came from a project .env)"
    return message + "."


def _not_found_message(model_ref: str, registry: Any) -> str:
    """pi's not-found error (:599-605), plus why guard 2 declined a ``.env`` OpenRouter key."""

    message = f'Model "{model_ref}" not found. Use --list-models to see available models.'
    names = _dotenv_only_names(registry, "openrouter")
    if names and _guard2_shape(model_ref):
        message += (
            f" ({', '.join(_sanitize(n) for n in names)} came from a project .env, which "
            "does not send ids this build does not know to OpenRouter; use "
            f"openrouter/{model_ref} or --provider openrouter.)"
        )
    return message


def _dotenv_declined_hint(
    model_ref: str, candidates: list[Model], registry: Any, taken: str
) -> str | None:
    """Why a raw match on another provider was NOT taken: its only key came from a ``.env``.

    ``taken`` is the provider the route went to. A candidate on that same
    provider offers no other route — the request already goes there, on that
    key — so it is skipped (#362 verify round 4, N4: ``--model openrouter/auto``
    on a ``.env`` OpenRouter key went to OpenRouter AND printed "use
    openrouter/openrouter/auto ... to send it there").
    """

    for m in candidates:
        if m.provider == taken:
            continue
        names = _dotenv_only_names(registry, m.provider)
        if names:
            return (
                f'"{model_ref}" is also {m.provider}\'s model id, but '
                f"{', '.join(_sanitize(n) for n in names)} came from a project .env, which "
                f"does not choose a route; use {m.provider}/{model_ref} or export the key "
                "to send it there."
            )
    return None


def _guard2_shape(model_ref: str) -> bool:
    """OpenRouter ids are ``<vendor>/<model>``: a slash, and no empty segment.

    Measured over this build's catalogue: of 356 OpenRouter ids the only one
    without a slash is pi's ``auto`` alias, so a bare id this build does not
    know is pi's "not found", never a guess at OpenRouter.
    """

    parts = model_ref.split("/")
    return len(parts) >= 2 and all(part.strip() for part in parts)


def _guard2(
    model_ref: str,
    inferred: str | None,
    registry: Any,
    overrides: bool,
) -> bool:
    """Guard 2 — may ``model_ref`` go to OpenRouter as written?

    Only when the user holds an OpenRouter credential of their own
    (:func:`route_authenticated`: exported, ``/login``-stored in ``auth.json``,
    or their models.json — all outside the repo; ADR-0203 locks
    ``AELIX_CODING_AGENT_DIR``/``AELIX_AUTH_PATH`` against a ``.env``), and, when
    the prefix names a provider, only when that provider is one of OpenRouter's
    namespaces in this build (:func:`openrouter_namespaces`) — ``xai/grok-4``
    stays a refusal, OpenRouter spells xAI ``x-ai/``.

    Two conditions are the CALLERS', not re-checked here (the #362 review and
    its verification each found a re-check here unreachable — one-line
    sabotages of them stayed green): a user-defined prefix never reaches this
    (step 3 returns inside it; step 2 has no inferred provider), and steps (4)
    and (5) call this only when the inferred provider is not route-authenticated
    (and never under ``--api-key``, step 3b).
    """

    if not _guard2_shape(model_ref):
        return False
    if inferred is not None and inferred.lower() not in openrouter_namespaces():
        return False
    return route_authenticated(registry, "openrouter", runtime_overrides=overrides)


def _guard2_route(
    model_ref: str, registry: Any, user_defined: frozenset[str], warning: str
) -> ResolvedRoute:
    """``model_ref`` on OpenRouter as written, catalogue-enriched when OpenRouter lists it."""

    model = _openrouter_base(_custom_in("openrouter", model_ref, registry, user_defined))
    return ResolvedRoute(model, "guard2", warning=warning)


def resolve_route(
    model_flag: str | None,
    provider_flag: str | None,
    registry: Any = None,
    default_provider: str | None = None,
    *,
    runtime_overrides: bool = True,
    typed_key: bool = False,
) -> ResolvedRoute:
    """The launch route for ``--model``/``--provider`` — pi's ``resolveCliModel`` order.

    ``provider_flag`` means "the user explicitly named this provider"
    (``--provider``, a profile's ``provider:``, the seeded settings pair);
    ``default_provider`` (settings ``defaultProvider`` split from its model) is
    the weakest signal (ADR-0195 Decision 4) and only breaks a bare-id tie or
    homes a bare id nothing else claims. ``registry`` is optional (catalogue
    only). ``runtime_overrides=False`` asks what a delegated child — which never
    receives ``--api-key`` — would decide (:mod:`agents.resolver`).
    ``typed_key`` says this launch carries ``--api-key``: see (3b).

    0. no ``--model``: shell ``OPENROUTER_DEFAULT_MODEL`` as
       ``--provider openrouter --model <it>``, when OpenRouter has any key;
    E. ``--provider``: aelix's case rule (:func:`_named_provider`); inside it
       the id exactly, else pi's ``<provider>/`` strip (:506-512), else a custom
       id under it — stripped too, as pi builds it (:570-597);
    1. a first-slash prefix naming a known provider (a user-defined one first
       and alone) — the INFERRED provider (:429-463);
    2. none inferred: the whole string as an id or ``provider/id`` across every
       model (:465-504) — one hit wins; several: settings ``defaultProvider``
       (when it counts, below), else the sole route-authenticated one, else the
       sole user-defined one among those, else pi's ambiguity error; none: a
       bare id under settings ``defaultProvider`` (when it counts), else guard
       2, else pi's not-found error. ``defaultProvider`` is the MERGED setting,
       which a project ``.aelix/settings.json`` sets even over the user's global
       one (and in an untrusted directory), so while the user holds a
       route-authenticating credential of their own anywhere
       (:func:`holds_route_auth`) it counts only when it names a provider that
       credential authenticates, or the user's own endpoint
       (:func:`_own_endpoint_providers`); otherwise both arms fall through as if
       it were unset (#362 verify round 4, B1: a project
       ``{"defaultProvider": "anthropic"}`` plus a ``.env`` ``ANTHROPIC_API_KEY``
       sent ``--model claude-haiku-4-5`` to ``api.anthropic.com`` while the
       user's own ``OPENROUTER_API_KEY`` was exported). pi has no
       ``defaultProvider`` tie-break at all (:465-503); the home is ADR-0195's.
       A session holding no credential of its own keeps both arms (ADR-0250 §6);
    3. inside the inferred provider, the id EXACTLY. A user-defined provider
       never lets the string leave it (the #344 principle): a custom id there
       or a refusal;
    3b. ``--api-key`` (``typed_key``) and a catalogued inferred provider: the
       found id, else a custom id there — no swap, no raw match on another
       provider, no guard 2. The key is attached after resolution to the
       route's provider (pi ``main.ts:476-484``, ``:827-834``), so nothing at
       resolve time counted it, and the swap or guard 2 carried a key typed for
       the provider the prefix names to ``openrouter.ai`` as the bearer — over
       the user's own OpenRouter key (#362 review, R02/R03);
    4. found, inferred provider not route-authenticated: the sole
       route-authenticated model whose raw id is the string (:519-541), else
       the sole user-defined one; with neither, guard 2 (S1, below); else the
       hit;
    5. not found: a route-authenticated raw match (:543-556), else — the
       inferred provider being route-authenticated — the custom id there
       (where pi takes the first raw match with no auth check, which is where a
       planted key captured ``anthropic/claude-haiku-4.5``), else guard 2, else
       pi's first raw match, else the custom id.

    Ids are compared case-sensitively (aelix's rule), but in (4) and (5) the
    string is also tried with the prefix in its canonical spelling: pi
    lower-cases both sides there (:526-527), and without it ``OpenAI/gpt-4o-mini``
    lost the swap and stayed on a planted ``OPENAI_API_KEY`` (#362 critique M4).

    Guard 2 also covers (4) — a DIVERGENCE from pi, stated in ADR-0250: an
    OpenRouter namespace provider the user holds no key of their own for, with
    an OpenRouter key they do hold, sends the string to OpenRouter as written
    even when the vendor's catalogue lists the id. pi reaches the same answer
    through its swap whenever pi.dev's live list carries the id on OpenRouter;
    aelix's snapshot lacks 133 such ids (``anthropic/claude-haiku-4-5``,
    ``openai/o1-pro`` …), and without this a planted vendor key in a ``.env``
    captured them from an OpenRouter user (#362 critique S1/S2).
    """

    user_defined = user_defined_providers(registry)
    overrides = runtime_overrides

    def _ra(provider: str) -> bool:
        return route_authenticated(registry, provider, runtime_overrides=overrides)

    def _shape(model: Model) -> Model:
        return _openrouter_base(_launch_shape(model, registry))

    def _default_counts(named: str) -> bool:
        # Step 2's settings ``defaultProvider`` (see the docstring): the user's
        # own endpoint, or a provider their own credential authenticates, or a
        # session holding no credential of its own.
        if named in _own_endpoint_providers(registry, user_defined) or _ra(named):
            return True
        return not holds_route_auth(registry, runtime_overrides=overrides)

    # --- 0: no model string ------------------------------------------------------
    if not model_flag:
        named = _named_provider(provider_flag, registry, user_defined)[0] if provider_flag else None
        default_id = os.environ.get("OPENROUTER_DEFAULT_MODEL")
        if (
            default_id
            and named in (None, "openrouter")
            and _configured_auth(registry, "openrouter")
        ):
            # The variable chose OpenRouter, not the key, so a key from any
            # source authenticates it (ADR-0250 §2.6).
            inner = resolve_route(
                default_id, "openrouter", registry, runtime_overrides=runtime_overrides
            )
            return replace(inner, kind="openrouter_default")
        return ResolvedRoute(Model(id="", provider=named or provider_flag or ""), "none")

    # --- E: an explicitly named provider -----------------------------------------
    if provider_flag:
        provider, clash = _named_provider(provider_flag, registry, user_defined)
        if clash:
            return ResolvedRoute(
                Model(id=model_flag, provider=provider),
                "held",
                ambiguous_provider_name_message(provider_flag, registry),
            )
        hit = _find_in(provider, model_flag, registry)
        model_id = model_flag
        if hit is None and model_flag.lower().startswith(f"{provider.lower()}/"):
            stripped = model_flag[len(provider) + 1 :]
            if stripped:
                model_id = stripped
                hit = _find_in(provider, model_id, registry)
        if hit is not None:
            return ResolvedRoute(_openrouter_base(hit), "explicit")
        custom = _openrouter_base(_custom_in(provider, model_id, registry, user_defined))
        return ResolvedRoute(
            custom, "explicit", warning=_custom_warning(provider, model_id, custom)
        )

    universe = _route_universe(registry)

    # --- 1: a prefix naming a known provider -------------------------------------
    inferred: str | None = None
    rest = model_flag
    prefix, sep, tail = model_flag.partition("/")
    if sep and prefix and tail:
        canon, clash = _user_defined_prefix(prefix, user_defined)
        if clash:
            return ResolvedRoute(
                Model(id=tail, provider=prefix),
                "held",
                ambiguous_provider_message(model_flag, registry),
            )
        if canon is None:
            known = _catalogued_providers() | user_defined | {m.provider for m in universe}
            canon = canonical_provider(prefix, frozenset(known))
        if canon is not None:
            inferred, rest = canon, tail

    # --- 2: no inferred provider -------------------------------------------------
    if inferred is None:
        aside = ""  # why settings defaultProvider did not count, for the refusal
        hits = _matches(universe, frozenset({model_flag}))
        if len(hits) == 1:
            return ResolvedRoute(_shape(hits[0]), "exact")
        if hits:
            if default_provider:
                named, dclash = _named_provider(default_provider, registry, user_defined)
                if dclash:
                    return ResolvedRoute(
                        Model(id=model_flag, provider=named),
                        "held",
                        ambiguous_provider_name_message(
                            default_provider, registry, source="settings defaultProvider"
                        ),
                    )
                on_default = [m for m in hits if m.provider == named]
                if len(on_default) == 1:
                    if _default_counts(named):
                        return ResolvedRoute(_shape(on_default[0]), "default_provider")
                    aside = _default_aside(named, registry)
            trusted = [m for m in hits if _ra(m.provider)]
            if len(trusted) == 1:
                return ResolvedRoute(_shape(trusted[0]), "auth_tiebreak")
            mine = [m for m in trusted if m.provider in user_defined]
            if len(mine) == 1:
                return ResolvedRoute(_shape(mine[0]), "user_defined")
            return ResolvedRoute(
                Model(id=model_flag, provider=""),
                "error",
                _ambiguous_message(model_flag, hits, registry, overrides) + aside,
            )
        if default_provider and not sep:
            # ADR-0195: a bare id nothing else claimed is homed under the
            # settings default provider — when it counts (see the docstring).
            named, dclash = _named_provider(default_provider, registry, user_defined)
            if dclash:
                return ResolvedRoute(
                    Model(id=model_flag, provider=named),
                    "held",
                    ambiguous_provider_name_message(
                        default_provider, registry, source="settings defaultProvider"
                    ),
                )
            if _default_counts(named):
                custom = _openrouter_base(_custom_in(named, model_flag, registry, user_defined))
                return ResolvedRoute(
                    custom, "default_provider", warning=_custom_warning(named, model_flag, custom)
                )
            aside = _default_aside(named, registry)
        if _guard2(model_flag, None, registry, overrides):
            return _guard2_route(
                model_flag,
                registry,
                user_defined,
                f'Model "{model_flag}" is not in this build\'s catalog; sending it to '
                "OpenRouter as written.",
            )
        # The placeholder keeps the prefix as a provider so the late-registration
        # path can re-resolve it once ``session_start`` has registered it.
        placeholder = (
            Model(id=tail, provider=prefix)
            if (sep and prefix and tail)
            else Model(id=model_flag, provider="")
        )
        return ResolvedRoute(placeholder, "error", _not_found_message(model_flag, registry) + aside)

    # --- 3: inside the inferred provider, the id exactly -------------------------
    found = _find_in(inferred, rest, registry)
    if inferred in user_defined:
        if found is not None:
            return ResolvedRoute(found, "prefix")
        custom = _custom_in(inferred, rest, registry, user_defined)
        return ResolvedRoute(custom, "custom", warning=_custom_warning(inferred, rest, custom))

    # --- 3b: --api-key keeps the string on the provider it names -------------------
    if typed_key:
        # A divergence from pi, which swaps first and attaches the key after
        # (``main.ts:476-484``, ``:827-834`` @ 88ff80b98): there
        # ``--model openai/gpt-4o-mini --api-key K`` with an OpenRouter key
        # exported puts K on openrouter. The key is typed for the provider the
        # string names; ``--provider openrouter`` / ``openrouter/<id>`` name
        # OpenRouter.
        if found is not None:
            return ResolvedRoute(_openrouter_base(found), "prefix")
        custom = _openrouter_base(_custom_in(inferred, rest, registry, user_defined))
        return ResolvedRoute(custom, "custom", warning=_custom_warning(inferred, rest, custom))
    strings = frozenset({model_flag, f"{inferred}/{rest}"})
    inferred_ra = _ra(inferred)

    # --- 4: found — pi's swap, then guard 2 --------------------------------------
    if found is not None:
        if not inferred_ra:
            others = [
                m
                for m in _matches(universe, strings, ids_only=True)
                if not (m.provider == found.provider and m.id == found.id)
            ]
            trusted = [m for m in others if _ra(m.provider)]
            if len(trusted) == 1:
                return ResolvedRoute(_shape(trusted[0]), "auth_swap")
            mine = [m for m in trusted if m.provider in user_defined]
            if len(mine) == 1:
                return ResolvedRoute(_shape(mine[0]), "user_defined")
            if not trusted and _guard2(model_flag, inferred, registry, overrides):
                note = (
                    f'"{model_flag}" goes to OpenRouter as written: you hold no {inferred} '
                    "credential of your own, and this build's OpenRouter catalog does not "
                    f"list the id. Use --provider {inferred} to send it to {inferred}."
                )
                names = _dotenv_only_names(registry, inferred)
                if names:
                    note += (
                        f" ({', '.join(_sanitize(n) for n in names)} came from a project "
                        ".env, which does not choose a route.)"
                    )
                return _guard2_route(model_flag, registry, user_defined, note)
            hint = (
                None
                if trusted
                else _dotenv_declined_hint(model_flag, others, registry, found.provider)
            )
            return ResolvedRoute(_openrouter_base(found), "prefix", warning=hint)
        return ResolvedRoute(_openrouter_base(found), "prefix")

    # --- 5: not found inside it --------------------------------------------------
    raw = _matches(universe, strings)
    trusted = [m for m in raw if _ra(m.provider)]
    mine = [m for m in trusted if m.provider in user_defined]
    if len(mine) == 1:
        return ResolvedRoute(_shape(mine[0]), "user_defined")
    if trusted:
        return ResolvedRoute(_shape(trusted[0]), "raw_trusted")
    if inferred_ra:
        # The decision the owner left open (pi :548-555 takes the first raw
        # match with no auth check). Measured: that is where a planted key
        # captures an OpenRouter-spelled id — ``anthropic/claude-haiku-4.5``
        # with ``ANTHROPIC_API_KEY`` exported and ``OPENROUTER_API_KEY`` from a
        # ``.env`` went to OpenRouter on the file's key. The user holds a key
        # for the provider they named, so the id stays there.
        custom = _openrouter_base(_custom_in(inferred, rest, registry, user_defined))
        warning = _custom_warning(inferred, rest, custom)
        hint = _dotenv_declined_hint(model_flag, raw, registry, inferred)
        if hint is not None:
            warning = f"{warning} {hint}" if warning else hint
        return ResolvedRoute(custom, "custom", warning=warning)
    if _guard2(model_flag, inferred, registry, overrides):
        return _guard2_route(
            model_flag,
            registry,
            user_defined,
            f'Model "{model_flag}" is not in this build\'s catalog; sending it to '
            "OpenRouter as written.",
        )
    if raw:
        # pi's first raw match. No route-authenticated credential is involved
        # anywhere, so nothing a ``.env`` supplied chose it (it may still
        # authenticate it — ADR-0203 residual risk 1, ADR-0250 §6).
        return ResolvedRoute(_shape(raw[0]), "raw_first")
    custom = _openrouter_base(_custom_in(inferred, rest, registry, user_defined))
    return ResolvedRoute(custom, "custom", warning=_custom_warning(inferred, rest, custom))


def resolve_model(
    model_flag: str | None,
    provider_flag: str | None,
    registry: Any = None,
    default_provider: str | None = None,
) -> Model:
    """The turn :class:`Model` of :func:`resolve_route` — what every caller drives.

    TOTAL: an ambiguous or unknown string comes back as a placeholder whose
    ``api`` stays ``"unknown"``, so callers still gate on
    ``core.runnable_models.is_runnable`` (#98); the launch paths read
    :func:`resolve_route`'s ``error`` for the message.
    """

    return resolve_route(model_flag, provider_flag, registry, default_provider).model


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
    """Why the launch model is not the provider ``--model`` names now, if it is not.

    #344 / ADR-0249 §2.3, kept by ADR-0250 (#367 is the issue that changes it to
    a refusal). X1 resolves the launch model after the extensions' ``setup()``
    registrations are replayed, but a provider registered in a ``session_start``
    handler arrives later still — inside ``create_agent_session_runtime``, after
    the first build. At launch its prefix is therefore UNKNOWN, and
    :func:`resolve_route` either sends the string to OpenRouter (guard 2, an
    OpenRouter credential of the user's own; review of ``0fcc3333`` measured 12 ×
    ``CONNECT openrouter.ai:443`` for this shape under the old rung) or refuses it
    as not found (no such credential) and holds the harness on a placeholder.

    Returns the reason text when ``--model`` came without ``--provider`` and
    either the harness is on ``openrouter`` and a re-resolve over the registry
    as it is NOW lands on a user-defined provider — or two that differ only in
    case (Codex second pass on ``ebfe411a``, F3) — or the harness holds the
    not-found placeholder and the re-resolve now lands on a user-defined
    provider a turn can run. The second arm is #362's: under the old rung a
    ``.env`` OpenRouter key put the string on OpenRouter and this switched it,
    and with no OpenRouter key at all the first turn failed with
    ``No provider registered for api='unknown'``; both now switch (critique S5).
    ``None`` otherwise. Reads no credential.

    A provider registered and then unregistered before ``session_start``
    returns is not in the registry as it is NOW, so the string stays where the
    launch put it (F2, ADR-0249 §2.3) — nothing to catch here.

    What the caller does with it: every mode switches the harness through the
    ``/model`` path
    (:func:`~aelix_coding_agent.cli.model_switch.switch_to_late_registered_route`),
    without persisting it. The message itself is only the reason text of the
    trigger; nothing prints it.
    """

    if not (model_flag and not provider_flag):
        return None
    current_provider = getattr(current_model, "provider", "") or ""
    held = getattr(current_model, "api", "") == "unknown"
    if current_provider != "openrouter" and not held:
        return None
    try:
        now = resolve_route(model_flag, None, model_registry).model
        user_defined = user_defined_providers(model_registry)
    except Exception:  # noqa: BLE001 — a diagnostic must never break launch
        return None
    if current_provider == "openrouter":
        ambiguous = ambiguous_provider_message(model_flag, model_registry)
        if ambiguous is not None:
            return ambiguous
    elif now.api == "unknown":
        # Held, and still nothing a turn can run: a refusal the launch already
        # reports (a case clash, an id the user's provider does not serve).
        return None
    if not now.provider or now.provider == "openrouter" or now.provider not in user_defined:
        return None
    return (
        f"--model {model_flag} names provider '{now.provider}', which was registered "
        "after the launch model was chosen (in a session_start handler). "
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
    "ResolvedRoute",
    "resolve_model",
    "resolve_route",
    "route_authenticated",
    "user_defined_providers",
]
