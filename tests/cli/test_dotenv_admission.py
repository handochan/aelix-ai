"""cwd ``.env`` admission control — the security boundary (ADR-0203).

``load_dotenv`` runs from ``main_sync`` BEFORE the Project Trust gate exists, so
a ``.env`` in a repo you merely cloned is attacker-controlled input to
``os.environ``. These tests pin the policy: provider credentials in, a short
value-shape-checked provider-configuration list in, everything else out.

DELIBERATE: this module imports ONLY :func:`load_dotenv` — never
``_dotenv_key_allowed`` / ``_DOTENV_NEVER`` / ``_DOTENV_LOCKED`` /
``_DOTENV_CONFIG_VALUES``, except in the two invariant tests that exist to
compare two module constants to each other and say so. A test that reaches for
the filter's internals passes by re-asserting the filter's own opinion of
itself; asserting through the public loader is what lets this exact file run
unchanged against a build with the guard removed.

EVERY test here must be RED under some mutation of the loader. One that is not
is worse than no test: ``test_exploit_chain2b_repo_cannot_relocate_the_global_
settings_file`` shipped as a structurally vacuous assertion, passed with the
admission guard entirely deleted, AND leaked the hijacked value into its two
sibling params so they passed too — see
:func:`test_the_old_chain2b_assertion_was_vacuous`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv

# Each entry carries the CONSUMER that makes the name dangerous, so a later
# reader who wonders "why is this one on the list?" has the answer in the
# failure message rather than in a commit archaeology session.
CONTROL_PLANE = [
    ("AELIX_MCP_CONFIG", "cli/config.py load_mcp_server_contribs -> spawns the MCP `command` in the never-gated env tier"),
    ("AELIX_SETTINGS_PATH", "aelix_ai/settings/storage.py -> becomes the GLOBAL settings store"),
    ("AELIX_CODING_AGENT_DIR", "cli/config.py get_agent_dir -> entry.py agent_dir -> global store + trust.json"),
    ("AELIX_AUTH_PATH", "oauth auth store location"),
    # The ONLY case in this list that the ``^AELIX_`` branch of _DOTENV_NEVER
    # has to refuse by itself: it ends in `_API_KEY`, so the credential suffix
    # rule would otherwise admit it. Measured, the whole file passes with that
    # alternation deleted unless this case exists — every other AELIX_* name
    # here is refused for some other reason, so the branch the code calls "the
    # load-bearing half" had zero test pressure.
    ("AELIX_FUTURE_API_KEY", "^AELIX_ — a knob we add later, named like a credential, must still be repo-unsettable"),
    ("XDG_CONFIG_HOME", "aelix_ai/settings/storage.py -> global settings dir"),
    ("HOME", "everything anchored at ~"),
    ("OPENROUTER_BASE_URL", "runtime_bootstrap.resolve_model -> Model.base_url, carries the key + the prompt"),
    ("BASH_ENV", "tools/bash.py hands os.environ to `bash -c`; bash SOURCES $BASH_ENV"),
    ("LD_PRELOAD", "same env -> arbitrary .so into every child"),
    ("NODE_OPTIONS", "MCP servers run via npx"),
    ("GIT_SSH_COMMAND", "any `git fetch` the agent runs"),
    ("PYTHONSTARTUP", "any python the agent runs interactively"),
    ("EDITOR", "tui/shell.py -> spawned on Ctrl+G"),
    ("VISUAL", "tui/shell.py -> spawned on Ctrl+G"),
    ("PI_OFFLINE", "pi-parity control plane"),
]

CREDENTIALS = [
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "HF_TOKEN",
    "GH_TOKEN",
    # The owner's own ``.env`` carries this and NOTHING in the repo consumes it
    # by name — it is in no table. A closed allow-list would refuse it.
    "OPENAI_RESPONSE_API_KEY",
    # Any self-hosted provider: model_registry resolves an ARBITRARY env-var
    # name declared in models.json, so an unregistered key name is a supported
    # feature, not a typo.
    "MY_COMPANY_API_KEY",
]

# The floor under the escape hatch, in full. Grew from 5 to 14 when the
# criterion was corrected: the old one ("these decide where aelix's global
# settings live") was drawn on a different axis from the reason the floor
# exists, which is why AELIX_MCP_CONFIG — the only chain that fires under
# ``--no-approve``, and the one that is arbitrary code execution — was missing.
LOCKED = [
    "AELIX_SETTINGS_PATH",
    "AELIX_CODING_AGENT_DIR",
    "AELIX_AUTH_PATH",
    "XDG_CONFIG_HOME",
    "HOME",
    "AELIX_DOTENV_ALLOW",
    "AELIX_MCP_CONFIG",
    "BASH_ENV",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "GIT_SSH_COMMAND",
    "PYTHONSTARTUP",
    "EDITOR",
    "VISUAL",
]

# Google's own documented region + project-id syntax. If any of these were
# refused the fix would be breaking a legitimate Vertex setup.
GCP_NAMES_OK = [
    "us-central1",
    "europe-west4",
    "asia-northeast3",
    "us-east5",
    "northamerica-northeast1",
    "global",
    "my-gcp-project",
    "aelix-prod-1",
]

# Every one of these, admitted as a LOCATION, moves the request off
# googleapis.com or reaches into the request path. Measured base_url for the
# first three: NETLOC 'attacker.example', 'attacker.example:8443',
# '@attacker.example-aiplatform.googleapis.com'.
GCP_NAMES_BAD = [
    "attacker.example/x",
    "@attacker.example",
    "attacker.example:8443/v1",
    "us-central1.attacker.example",
    "US-CENTRAL1",
    "../../x",
    "a b",
    "x\ty",
    "",
]

# Cloudflare account ids and AI-Gateway names. Unlike the Vertex location these
# cannot move the HOST — measured, the 16 values of CF_IDS_BAD against the
# catalog's 4 {CLOUDFLARE*}-templated base URLs is 64 cases; 60 produced a URL
# and every one put the request host at gateway.ai.cloudflare.com or
# api.cloudflare.com, and the other 4 are x\ty raising httpx.InvalidURL on each
# template. What they CAN do is rewrite the PATH, which is what _CF_ID exists for.
CF_IDS_OK = [
    "0123456789abcdef0123456789abcdef",
    "acct1234",
    "my-gateway",
    "my_gateway",
    "Prod-Gateway-01",
    "g",
    "a" * 64,
]

# Measured joined request URLs for the first three, with the shape rule removed:
#   '../../..'                          .../gw/anthropic/chat/completions  (out of /v1/{acct}/{gw}/)
#   'x/../../../../../attacker.example' .../attacker.example/gw/anthropic/chat/completions
#   'x#f'                               .../v1/x/chat/completions#f/gw/anthropic  (gateway routing gone)
CF_IDS_BAD = [
    "",
    "a" * 65,
    "attacker.example/x",
    "attacker.example:8443/v1",
    "@attacker.example",
    "..",
    "../../..",
    "x/../../../../../attacker.example",
    "x?q=",
    "x#f",
    "a b",
    "//attacker.example",
    "%2F%2Fattacker.example",
    "\\attacker.example",
    "us-central1.attacker.example",
    "x\ty",
]
# Not in the list above and deliberately so: a value containing a NEWLINE cannot
# reach the shape rule at all, because ``load_dotenv`` splits the file into lines
# first. Measured through the loader, ``CLOUDFLARE_GATEWAY_ID=x\ny`` admits ``x``
# and drops ``y`` as a line with no ``=``. It is covered at the URL layer
# instead: httpx raises ``InvalidURL`` on it.


def _load(env_file, text: str) -> None:
    """Write ``text`` and run the loader over it."""

    env_file.write_text(text)
    load_dotenv(str(env_file))


@pytest.mark.parametrize("key,consumer", CONTROL_PLANE, ids=[k for k, _ in CONTROL_PLANE])
def test_repo_dotenv_cannot_set_control_plane(tmp_path, monkeypatch, key, consumer) -> None:
    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}=/attacker/controlled\n")
    leaked = os.environ.get(key)
    # ``load_dotenv`` writes ``os.environ`` directly, so monkeypatch has no
    # record to restore for a key that was absent — pop it ourselves, or a
    # FAILURE here leaks into every later test and hides itself.
    os.environ.pop(key, None)
    assert leaked is None, f"repo .env set {key} — {consumer}"


@pytest.mark.parametrize("key", CREDENTIALS)
def test_repo_dotenv_still_loads_credentials(tmp_path, monkeypatch, key) -> None:
    """Green in BOTH lanes by design — these pin the workflow, not the fix.

    Their mutation is a different one: swap the suffix rule for the closed
    ``ENV_API_KEYS`` set and the last two entries go RED, which is what makes
    the rejection of a table-driven allow-list executable rather than asserted.
    """

    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}=sk-not-a-real-key\n")
    value = os.environ.get(key)
    os.environ.pop(key, None)
    assert value == "sk-not-a-real-key"


def test_every_shipped_provider_key_is_admitted(tmp_path, monkeypatch) -> None:
    """Naming-discipline guard: the suffix rule must cover 100% of providers.

    The rule is only safe because it is also COMPLETE — if someone registers a
    provider whose key is, say, ``ACME_CREDENTIAL``, this turns red at the time
    the provider is added rather than as a mystery auth failure in the field.

    It doubles as the guard on the sibling-precedence snapshot: ``anthropic``
    contributes two names from ONE file, and both must load. A ``.env`` line is
    not a "shell-supplied" sibling for the next line.
    """

    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    names = sorted({n for group in ENV_API_KEYS.values() for n in group})
    assert names, "ENV_API_KEYS is empty — the guard below would be vacuous"
    for n in names:
        monkeypatch.delenv(n, raising=False)
    _load(tmp_path / ".env", "".join(f"{n}=sk-not-a-real-key\n" for n in names))
    refused = [n for n in names if os.environ.get(n) != "sk-not-a-real-key"]
    for n in names:
        os.environ.pop(n, None)
    assert refused == [], f"shipped provider keys refused by the .env filter: {refused}"


def test_exported_value_still_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-real-environment")
    _load(tmp_path / ".env", "ANTHROPIC_API_KEY=from-the-file\n")
    assert os.environ["ANTHROPIC_API_KEY"] == "from-the-real-environment"


@pytest.mark.parametrize("key", ["AELIX_MCP_CONFIG", "SOME_VENDOR_ENDPOINT"])
def test_refusal_names_the_key_and_never_the_value(tmp_path, monkeypatch, key, capsys) -> None:
    """Both rejecting branches, because they build their name lists separately.

    ``AELIX_MCP_CONFIG`` moved from the *refused* branch to the *locked* one when
    the floor grew to 14, and with only that key this test stopped exercising
    *refused* at all — a mutation making the refusal line carry ``KEY=VALUE``
    went unnoticed. ``SOME_VENDOR_ENDPOINT`` is an ordinary refusal.
    """

    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}=/attacker/secret-payload.json\n")
    err = capsys.readouterr().err
    os.environ.pop(key, None)
    assert key in err
    assert "secret-payload" not in err


def test_notice_goes_to_stderr_so_print_mode_stdout_stays_clean(
    tmp_path, monkeypatch, capsys
) -> None:
    """``--print`` / ``--mode json`` / ``--mode rpc`` write MACHINE-READ stdout.

    A notice on stdout would corrupt a json payload or an rpc frame, which is a
    worse bug than the one being fixed. Exercised over a ``.env`` that triggers
    ALL NINE notice lines at once, so a line added later without a ``file=``
    argument is caught here rather than in the field.
    """

    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_OAUTH_TOKEN",
        "GH_TOKEN",
        "BASH_ENV",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "OPENROUTER_BASE_URL",
        "SOME_UNKNOWN_THING",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "users-own-exported-key")
    monkeypatch.setenv("GITHUB_TOKEN", "x" * 40)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    _load(
        tmp_path / ".env",
        "GH_TOKEN=fake\n"  # 1 credentials + 9 foreign precedence
        "GOOGLE_CLOUD_PROJECT=my-gcp-project\n"  # 2 config
        "OPENROUTER_BASE_URL=http://127.0.0.1:1/v1\n"  # 3 hatched
        "SOME_UNKNOWN_THING=x\n"  # 4 refused
        "BASH_ENV=/attacker/p.sh\n"  # 5 locked
        "AELIX_DOTENV_ALLOW=BASH_ENV\n"  # 6 gate
        "ANTHROPIC_OAUTH_TOKEN=from-the-file\n"  # 7 shadowed
        "GOOGLE_CLOUD_LOCATION=attacker.example/x\n",  # 8 bad value
    )
    captured = capsys.readouterr()
    for name in ("GH_TOKEN", "GOOGLE_CLOUD_PROJECT", "OPENROUTER_BASE_URL"):
        os.environ.pop(name, None)
    assert captured.out == ""
    # All nine lines really fired, or this test would pin only the ones that did.
    assert len([ln for ln in captured.err.splitlines() if ln.startswith("Notice:")]) == 9


def test_attacker_key_name_cannot_smuggle_ansi_into_the_notice(
    tmp_path, monkeypatch, capsys
) -> None:
    """The KEY text is attacker-controlled and now reaches a terminal.

    Before this change the loader printed nothing, so the escape-injection risk
    is one we INTRODUCED; a crafted key could otherwise repaint the screen and
    forge something shaped like a trust prompt.
    """

    _load(tmp_path / ".env", "\x1b[31mFAKE_PROMPT\x07=x\n")
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "\x07" not in err


def test_escape_hatch_is_per_key_and_comes_from_the_real_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("BASH_ENV", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    _load(
        tmp_path / ".env",
        "OPENROUTER_BASE_URL=http://127.0.0.1:1/v1\nBASH_ENV=/attacker/p.sh\n",
    )
    hatched = os.environ.get("OPENROUTER_BASE_URL")
    leaked_bash = os.environ.get("BASH_ENV")
    os.environ.pop("OPENROUTER_BASE_URL", None)
    os.environ.pop("BASH_ENV", None)
    # BOTH halves matter. Without the positive one, a build whose hatch is
    # simply broken passes — failing safe is still failing.
    assert hatched == "http://127.0.0.1:1/v1"
    assert leaked_bash is None


def test_escape_hatch_cannot_be_opened_by_the_dotenv_itself(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    monkeypatch.delenv("BASH_ENV", raising=False)
    _load(tmp_path / ".env", "AELIX_DOTENV_ALLOW=BASH_ENV\nBASH_ENV=/attacker/p.sh\n")
    gate = os.environ.get("AELIX_DOTENV_ALLOW")
    bash = os.environ.get("BASH_ENV")
    os.environ.pop("AELIX_DOTENV_ALLOW", None)
    os.environ.pop("BASH_ENV", None)
    assert gate is None
    assert bash is None


def test_escape_hatch_has_no_wildcard(tmp_path, monkeypatch) -> None:
    """The key here must NOT be one ``_DOTENV_LOCKED`` also refuses.

    With ``BASH_ENV`` this asserted nothing about the wildcard once the floor
    grew to 14: the locked branch runs first, so a build that honoured ``*``
    still passed. ``OPENROUTER_BASE_URL`` is hatchable, so only the ``*`` rule
    can refuse it.
    """

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "*")
    _load(tmp_path / ".env", "OPENROUTER_BASE_URL=http://127.0.0.1:1/v1\n")
    leaked = os.environ.get("OPENROUTER_BASE_URL")
    os.environ.pop("OPENROUTER_BASE_URL", None)
    assert leaked is None


@pytest.mark.parametrize("key", LOCKED)
def test_locked_keys_cannot_be_unlocked_by_the_escape_hatch(tmp_path, monkeypatch, key) -> None:
    """The hatch is a convenience; these fourteen are the floor under it.

    CRITERION, which is the maintained artifact — the list is only its current
    application: the hatch may let a repo REDIRECT; it may never let a repo
    EXECUTE, and it may never let a repo choose the global settings/auth store
    or widen the gate itself.

    Measured: one pasted ``export AELIX_DOTENV_ALLOW=AELIX_MCP_CONFIG`` used to
    restore startup ``sh -c <payload>`` in full, which is why the old
    five-name floor was drawn on the wrong axis.

    ONE param is a default-deny assertion rather than a hatch one, and says so
    here so nobody reads more into it: for ``AELIX_DOTENV_ALLOW`` the hatch and
    the target are the same variable, so the ``delenv`` below necessarily erases
    the hatch it just set. That name is defended four times over (its own branch
    in the loader, ``_DOTENV_LOCKED``, ``^AELIX_``, and default-deny); the guard
    that stops a ``.env`` from setting it is pinned by
    :func:`test_escape_hatch_cannot_be_opened_by_the_dotenv_itself`.
    """

    monkeypatch.setenv("AELIX_DOTENV_ALLOW", key)
    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}=/attacker/controlled\n")
    leaked = os.environ.get(key)
    os.environ.pop(key, None)
    assert leaked is None


@pytest.mark.parametrize("key,value", [("OPENROUTER_BASE_URL", "http://127.0.0.1:1/v1"), ("PI_OFFLINE", "1")])
def test_the_floor_did_not_swallow_the_hatch(tmp_path, monkeypatch, key, value) -> None:
    """The complement of the test above — a floor that locks everything is not a floor.

    ``OPENROUTER_BASE_URL`` is the hatch's own documented use case (a proxy) and
    ``PI_OFFLINE`` is an ordinary knob. Both must stay hatchable, or the next
    person to widen ``_DOTENV_LOCKED`` removes the feature without noticing.
    """

    monkeypatch.setenv("AELIX_DOTENV_ALLOW", key)
    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}={value}\n")
    got = os.environ.get(key)
    os.environ.pop(key, None)
    assert got == value


def test_missing_file_is_silent(tmp_path, capsys) -> None:
    load_dotenv(str(tmp_path / "does_not_exist.env"))
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# === Provider configuration — the third admission arm ========================
#
# Refusing these was a measured regression: ``google-vertex``'s primary
# documented auth path is ADC (ADR-0173), which uses no API key at all. They are
# admitted by NAME **and** by VALUE, because the location owns the request HOST.


def test_dotenv_can_configure_vertex_adc_at_the_consumer(tmp_path, monkeypatch) -> None:
    """The ``.env``-routed twin of ``test_runtime_bootstrap_google_unhide``.

    That file configures Vertex with ``monkeypatch.setenv`` and never through a
    ``.env``, so it stayed 7/7 green while this path was broken — a false green
    over exactly the regression. Asserted at ``is_runnable``, the boundary that
    decides whether the user sees any Vertex model at all.
    """

    from aelix_ai.models import get_model
    from aelix_coding_agent.core.runnable_models import is_runnable

    vtx = get_model("google-vertex", "gemini-2.5-flash")
    assert vtx is not None and vtx.api == "google-vertex"
    for name in ("GOOGLE_CLOUD_API_KEY", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name, raising=False)
    assert is_runnable(vtx, {"google-vertex"}) is False, "control: hidden before the .env"

    _load(
        tmp_path / ".env",
        "GOOGLE_CLOUD_PROJECT=my-gcp-project\nGOOGLE_CLOUD_LOCATION=us-central1\n",
    )
    runnable = is_runnable(vtx, {"google-vertex"})
    for name in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        os.environ.pop(name, None)
    assert runnable is True, "an ADC developer's .env no longer configures Vertex"


def test_a_location_that_would_move_the_host_is_refused(tmp_path, monkeypatch, capsys) -> None:
    """Measured, no network: ``location='attacker.example/x'`` yields base_url
    ``https://attacker.example/x-aiplatform.googleapis.com/`` — NETLOC
    ``attacker.example``. An unvalidated location is chain 3 by another name,
    carrying an ADC bearer token and the whole prompt.
    """

    from aelix_ai.models import get_model
    from aelix_coding_agent.core.runnable_models import is_runnable

    vtx = get_model("google-vertex", "gemini-2.5-flash")
    for name in ("GOOGLE_CLOUD_API_KEY", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name, raising=False)
    _load(
        tmp_path / ".env",
        "GOOGLE_CLOUD_PROJECT=my-gcp-project\nGOOGLE_CLOUD_LOCATION=attacker.example/x\n",
    )
    leaked = os.environ.get("GOOGLE_CLOUD_LOCATION")
    runnable = is_runnable(vtx, {"google-vertex"})
    err = capsys.readouterr().err
    os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    assert leaked is None
    assert runnable is False
    # Names the key, never the value — the value is the attacker's string.
    assert "GOOGLE_CLOUD_LOCATION" in err
    assert "attacker.example" not in err


def test_the_hatch_cannot_override_the_value_shape(tmp_path, monkeypatch) -> None:
    """Ordering is load-bearing: the shape rule runs BEFORE the hatch.

    Measured both ways with ``AELIX_DOTENV_ALLOW=GOOGLE_CLOUD_LOCATION`` and
    value ``attacker.example/x``: hatch-first ADMITS and yields base_url
    ``https://attacker.example/x-aiplatform.googleapis.com/``; shape-first
    refuses. The hatch names a KEY, and for these names the redirect lives in
    the VALUE, so the hatch has nothing to say about it.
    """

    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "GOOGLE_CLOUD_LOCATION")
    _load(tmp_path / ".env", "GOOGLE_CLOUD_LOCATION=attacker.example/x\n")
    leaked = os.environ.get("GOOGLE_CLOUD_LOCATION")
    os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
    assert leaked is None


@pytest.mark.parametrize(
    "key,value,admitted",
    [
        # #362 / ADR-0250: shell-only now — a model choice is a route choice, and
        # a project .env may authenticate a route but never choose one.
        ("OPENROUTER_DEFAULT_MODEL", "qwen/qwen3-coder", False),
        # A PATH to a full GCP service-account identity a repo can ship, whose
        # ``token_uri`` points the signed assertion wherever the repo likes.
        # Refusing it does not break ADC: google.auth finds
        # application_default_credentials.json with no env var at all.
        ("GOOGLE_APPLICATION_CREDENTIALS", "/attacker/sa.json", False),
        # ``^AELIX_`` is the invariant the whole design leans on; a cosmetic
        # attribution string is not worth a hole in it.
        ("AELIX_CODEX_ORIGINATOR", "not-aelix", False),
    ],
)
def test_the_config_arm_is_exactly_these_names(tmp_path, monkeypatch, key, value, admitted) -> None:
    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}={value}\n")
    got = os.environ.get(key)
    os.environ.pop(key, None)
    assert (got == value) is admitted


@pytest.mark.parametrize("value", GCP_NAMES_OK)
def test_legitimate_gcp_names_are_accepted(tmp_path, monkeypatch, value) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    _load(tmp_path / ".env", f"GOOGLE_CLOUD_LOCATION={value}\n")
    got = os.environ.get("GOOGLE_CLOUD_LOCATION")
    os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
    assert got == value, "the shape rule excludes a value Google itself documents"


@pytest.mark.parametrize("value", GCP_NAMES_BAD, ids=[repr(v) for v in GCP_NAMES_BAD])
def test_values_that_escape_the_googleapis_host_are_refused(tmp_path, monkeypatch, value) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    _load(tmp_path / ".env", f"GOOGLE_CLOUD_LOCATION={value}\n")
    got = os.environ.get("GOOGLE_CLOUD_LOCATION")
    os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
    assert got is None


def test_dotenv_can_configure_cloudflare_at_the_consumer(tmp_path, monkeypatch) -> None:
    """The second silent provider regression, at the boundary that showed it.

    Both Cloudflare providers carry the catalog's only ``{ENV_VAR}``-templated
    base URLs, so ``_base_url_unconfigured`` hides every one of their models
    until the tokens expand. ``CLOUDFLARE_API_KEY`` is admitted by the suffix
    rule while the two ids were not, so a developer's working ``.env`` produced
    "loaded credentials: CLOUDFLARE_API_KEY" and zero Cloudflare models.

    Measured A/B at ``is_runnable`` over the whole catalog: 847 runnable on
    ``0c9da7d``, 804 with the ids refused (delta 43, exactly two providers
    moved), 847 with them admitted. The delta was 43 rows then, 54 after #172
    added 11 Cloudflare models and 57 after the fold-back of 2026-10-27 — so the
    count below is a scan guard that
    has to be re-derived when the catalog moves, not the claim being tested.
    The claim is the two assertions around it: none of them runnable without
    the ``.env``, all of them runnable with it.
    """

    from aelix_ai.models_generated import MODELS
    from aelix_coding_agent.core.runnable_models import is_runnable

    apis = {"anthropic-messages", "openai-responses", "openai-completions"}
    cf = [
        m
        for provider in ("cloudflare-ai-gateway", "cloudflare-workers-ai")
        for m in MODELS[provider].values()
    ]
    assert len(cf) == 57, "catalog changed; re-derive this count and the two below"

    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_GATEWAY_ID"):
        monkeypatch.delenv(name, raising=False)
    assert not any(is_runnable(m, apis) for m in cf), "control: hidden before the .env"

    _load(
        tmp_path / ".env",
        "CLOUDFLARE_API_KEY=cf-not-a-real-key\n"
        "CLOUDFLARE_ACCOUNT_ID=acct1234\n"
        "CLOUDFLARE_GATEWAY_ID=my-gateway\n",
    )
    runnable = sum(1 for m in cf if is_runnable(m, apis))
    for name in ("CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_GATEWAY_ID"):
        os.environ.pop(name, None)
    assert runnable == 57, "a Cloudflare developer's .env no longer surfaces their models"


def test_every_templated_base_url_token_is_admissible() -> None:
    """The half of the visibility criterion that CAN be made mechanical.

    A ``{ENV_VAR}`` token in a catalog ``baseUrl`` is a name whose absence hides
    every model that carries it. Both times this arm was drawn too narrowly the
    symptom was models vanishing from ``/model`` with the stderr notice pointing
    at something else. Walking the shipped catalog turns "somebody should have
    noticed" into a failing test the day a provider is added.

    Asserted through the loader, not against ``_DOTENV_CONFIG_VALUES``: a token
    admitted by the credential-suffix rule is equally fine, and this must not
    care which arm let it through.
    """

    import re
    import tempfile
    from pathlib import Path

    from aelix_ai.models_generated import MODELS

    token_re = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    tokens = {
        name
        for models in MODELS.values()
        for m in models.values()
        for name in token_re.findall(getattr(m, "base_url", "") or "")
        # Lowercase tokens are not env-var names: google-vertex's ``{location}``
        # is filled by the SDK from resolved options, and ``is_runnable`` routes
        # those models to ``_vertex_config_missing`` instead of the placeholder
        # guard. Measured: ``location`` is never read from the environment.
        if name.isupper()
    }
    assert tokens, "no templated base_url in the catalog — this test is vacuous"

    unsettable = []
    with tempfile.TemporaryDirectory() as d:
        env_file = Path(d) / ".env"
        for name in sorted(tokens):
            os.environ.pop(name, None)
            _load(env_file, f"{name}=plainvalue1\n")
            if os.environ.get(name) != "plainvalue1":
                unsettable.append(name)
            os.environ.pop(name, None)
    assert unsettable == [], (
        "a catalog base_url needs these to expand, but a .env cannot set them, "
        f"so every model carrying them is hidden: {unsettable}"
    )


@pytest.mark.parametrize("value", CF_IDS_OK)
def test_legitimate_cloudflare_ids_are_accepted(tmp_path, monkeypatch, value) -> None:
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    _load(tmp_path / ".env", f"CLOUDFLARE_ACCOUNT_ID={value}\n")
    got = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
    assert got == value


@pytest.mark.parametrize("value", CF_IDS_BAD, ids=[repr(v) for v in CF_IDS_BAD])
def test_cloudflare_ids_that_rewrite_the_request_path_are_refused(
    tmp_path, monkeypatch, value
) -> None:
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID", raising=False)
    _load(tmp_path / ".env", f"CLOUDFLARE_GATEWAY_ID={value}\n")
    got = os.environ.get("CLOUDFLARE_GATEWAY_ID")
    os.environ.pop("CLOUDFLARE_GATEWAY_ID", None)
    assert got is None


def test_the_hatch_cannot_override_a_cloudflare_id_shape(tmp_path, monkeypatch) -> None:
    """Same ordering argument as the Vertex location, re-asserted per key.

    The hatch names a KEY; the path rewrite lives in the VALUE. Measured with
    ``AELIX_DOTENV_ALLOW=CLOUDFLARE_ACCOUNT_ID`` and the shape rule moved behind
    the hatch: ``x/../../../../../attacker.example`` produced the request URL
    ``https://gateway.ai.cloudflare.com/attacker.example/gw/openai/…``.
    """

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "CLOUDFLARE_ACCOUNT_ID")
    _load(
        tmp_path / ".env",
        "CLOUDFLARE_ACCOUNT_ID=x/../../../../../attacker.example\n",
    )
    leaked = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
    assert leaked is None


def test_the_config_arm_can_never_become_a_locked_key_bypass() -> None:
    """An INVARIANT between two module constants — hence the internal import.

    This rationale shipped INVERTED for one round: it said the config arm is
    checked before the locked branch. It is not. ``load_dotenv`` tests
    ``_DOTENV_GATE``, then ``_DOTENV_LOCKED``, then ``_DOTENV_CONFIG_VALUES``, so
    a name in both is refused outright, not admitted by shape.

    The false version described a REAL and reachable loader, which is why the
    assertion stays. Measured: mutate the locked branch to ``if key in
    _DOTENV_LOCKED and key not in _DOTENV_CONFIG_VALUES`` — i.e. make the code
    match that docstring — and this test still passes while
    ``AELIX_MCP_CONFIG=/tmp/anything.json`` starts landing in ``os.environ``,
    the one chain that is arbitrary code execution rather than an indirect trust
    defeat.

    So the reason to keep it is the opposite of what was written: it is a set
    intersection and therefore ORDER-INDEPENDENT, which is what makes it hold
    under either branch order. Today the locked branch also happens to win; this
    is what keeps that from being load-bearing.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import (
        _DOTENV_CONFIG_VALUES,
        _DOTENV_LOCKED,
    )

    assert set(_DOTENV_CONFIG_VALUES) & _DOTENV_LOCKED == set()


# === Precedence — what a shell-exported value does and does not beat =========


def test_a_repo_key_cannot_outrank_an_exported_sibling(tmp_path, monkeypatch) -> None:
    """``setdefault`` protects a NAME, not a provider.

    ``ENV_API_KEYS['anthropic'] = ['ANTHROPIC_OAUTH_TOKEN', 'ANTHROPIC_API_KEY']``
    and ``get_env_api_key`` returns the first non-empty one, so a repo ``.env``
    supplying the OAuth token never collides with the user's exported API key —
    it simply outranks it, and every turn authenticates as whoever wrote the
    file. Measured before the guard: ``get_api_key_cascade('anthropic')``
    returned the file's token. Asserted at ``get_env_api_key``, the selection
    boundary, not at ``os.environ``.
    """

    from aelix_ai.providers._env_api_keys import get_env_api_key

    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "users-own-exported-key")
    _load(tmp_path / ".env", "ANTHROPIC_OAUTH_TOKEN=from-the-repo\n")
    selected = get_env_api_key("anthropic")
    os.environ.pop("ANTHROPIC_OAUTH_TOKEN", None)
    assert selected == "users-own-exported-key"


def test_the_hatch_is_how_you_mean_it_for_a_shadowed_sibling(tmp_path, monkeypatch) -> None:
    """The positive half. Without it, a guard that is simply always-on passes."""

    from aelix_ai.providers._env_api_keys import get_env_api_key

    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "users-own-exported-key")
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "ANTHROPIC_OAUTH_TOKEN")
    _load(tmp_path / ".env", "ANTHROPIC_OAUTH_TOKEN=from-the-repo\n")
    selected = get_env_api_key("anthropic")
    os.environ.pop("ANTHROPIC_OAUTH_TOKEN", None)
    assert selected == "from-the-repo"


def test_a_sibling_key_still_loads_when_the_shell_supplies_nothing(tmp_path, monkeypatch) -> None:
    """The supported workflow: a developer's own ``.env``, no exported key."""

    from aelix_ai.providers._env_api_keys import get_env_api_key

    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    _load(tmp_path / ".env", "ANTHROPIC_OAUTH_TOKEN=from-the-repo\n")
    selected = get_env_api_key("anthropic")
    os.environ.pop("ANTHROPIC_OAUTH_TOKEN", None)
    assert selected == "from-the-repo"


def test_one_dotenv_line_is_not_a_shell_supplied_sibling_for_the_next(
    tmp_path, monkeypatch
) -> None:
    """The snapshot must be taken BEFORE the loop, not read live inside it.

    A ``.env`` may legitimately supply both names for a provider. Read live, the
    second line would be shadowed by the first — by a value that came out of the
    same file.
    """

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    _load(
        tmp_path / ".env",
        "ANTHROPIC_API_KEY=from-the-repo-1\nANTHROPIC_OAUTH_TOKEN=from-the-repo-2\n",
    )
    got = (os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ANTHROPIC_OAUTH_TOKEN"))
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_OAUTH_TOKEN", None)
    assert got == ("from-the-repo-1", "from-the-repo-2")


def test_a_lower_ranked_repo_key_is_not_refused(tmp_path, monkeypatch) -> None:
    """The direction the first guard got wrong, asserted at the SELECTOR.

    ``ENV_API_KEYS['anthropic'] = ['ANTHROPIC_OAUTH_TOKEN', 'ANTHROPIC_API_KEY']``,
    so with the shell holding index 0 and the file offering index 1,
    ``get_env_api_key`` returns the SHELL value whether the file's key is
    admitted or not. The order-blind guard refused it anyway: it dropped a key
    the user asked for out of ``os.environ`` — where ``tools/bash.py
    get_shell_env()`` hands it to every child process — and printed a notice
    saying it "would have outranked" a token it cannot outrank.

    Both halves are asserted, because either alone is satisfiable by a wrong
    build: an always-on guard fails the first, and a guard that admits but still
    reports fails the second.
    """

    from aelix_ai.providers._env_api_keys import get_env_api_key

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "users-own-exported-token")
    _load(tmp_path / ".env", "ANTHROPIC_API_KEY=from-the-repo\n")
    loaded = os.environ.get("ANTHROPIC_API_KEY")
    selected = get_env_api_key("anthropic")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    assert loaded == "from-the-repo", "a key that cannot change the selection was dropped"
    assert selected == "users-own-exported-token", "the shell key must still win"


def test_no_false_outranking_notice_for_a_lower_ranked_key(
    tmp_path, monkeypatch, capsys
) -> None:
    """The sentence, not just the behaviour.

    "would have outranked it" is a specific, checkable claim about two named
    keys. Printed for the lower-ranked direction it is measurably false, and a
    security disclosure that states a falsehood is the defect this round exists
    to remove.
    """

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "users-own-exported-token")
    _load(tmp_path / ".env", "ANTHROPIC_API_KEY=from-the-repo\n")
    err = capsys.readouterr().err
    os.environ.pop("ANTHROPIC_API_KEY", None)
    assert "would have outranked" not in err


def test_every_multi_name_provider_group_is_guarded_by_index(
    tmp_path, monkeypatch
) -> None:
    """Written over the TABLE, not over ``anthropic``, and over INDEX ORDER.

    ``anthropic`` is the only ``ENV_API_KEYS`` entry with more than one name
    today, so a guard hard-coded to it passes right now. This turns red the day
    a second multi-name provider is added with the guard left as it is.

    The expected verdict per ordered pair is derived from the SELECTOR's own
    rule — first non-empty name wins — rather than restated, so the table stays
    the single source of truth. Its predecessor asserted every pair was refused,
    which is what pinned the false half in place.
    """

    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    groups = {p: n for p, n in ENV_API_KEYS.items() if len(n) > 1}
    assert groups, "no multi-name provider — this guard would be vacuous"
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    wrong = []
    for provider, names in groups.items():
        for injected in names:
            for sibling in names:
                if sibling == injected:
                    continue
                for n in names:
                    monkeypatch.delenv(n, raising=False)
                monkeypatch.setenv(sibling, "users-own-exported-key")
                _load(tmp_path / ".env", f"{injected}=from-the-repo\n")
                admitted = os.environ.get(injected) is not None
                os.environ.pop(injected, None)
                # Admitting `injected` changes the selection only if it ranks
                # ABOVE the name the shell already supplies. Refuse exactly then.
                should_refuse = names.index(injected) < names.index(sibling)
                if admitted is should_refuse:
                    wrong.append((provider, injected, sibling, admitted))
    assert wrong == [], (
        "guard does not match the selector's index order "
        "(provider, .env key, shell key, admitted): " + repr(wrong)
    )


def test_gh_token_is_deliberately_not_guarded_and_is_disclosed(
    tmp_path, monkeypatch, capsys
) -> None:
    """The half we do NOT close, pinned so nobody "fixes" it.

    ``gh`` prefers ``GH_TOKEN`` over ``GITHUB_TOKEN`` (measured, gh 2.88.0), but
    that precedence is gh's to implement, not ours to override. ``GITHUB_TOKEN``
    is AMBIENT in GitHub Codespaces — a platform default the user never chose —
    while putting ``GH_TOKEN`` in ``.env`` to outrank it is a documented,
    deliberate workflow. Refusing it here would silently break every Codespace.
    We change precedence only where we implement it; elsewhere we disclose.
    """

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x" * 40)
    _load(tmp_path / ".env", "GH_TOKEN=fake\n")
    got = os.environ.get("GH_TOKEN")
    err = capsys.readouterr().err
    os.environ.pop("GH_TOKEN", None)
    assert got == "fake"
    assert "prefers it over the GITHUB_TOKEN" in err


# === What the notices say ====================================================
#
# Pinned by the DISTINGUISHING clause rather than the whole sentence, so ordinary
# rewording does not churn these but a class losing its own line does. Each of
# these sentences was, or replaces, a measured false statement.


def test_provider_config_is_not_announced_as_a_credential(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    _load(tmp_path / ".env", "GOOGLE_CLOUD_PROJECT=my-gcp-project\n")
    err = capsys.readouterr().err
    os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    assert "provider configuration" in err
    assert "loaded credentials" not in err


def test_a_hatched_non_credential_says_where_its_value_came_from(
    tmp_path, monkeypatch, capsys
) -> None:
    """The line whose job is "I never typed that" used to call this a credential."""

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    _load(tmp_path / ".env", "OPENROUTER_BASE_URL=http://127.0.0.1:1/v1\n")
    err = capsys.readouterr().err
    os.environ.pop("OPENROUTER_BASE_URL", None)
    assert "because your AELIX_DOTENV_ALLOW lists them" in err
    assert "loaded credentials" not in err


def test_a_hatched_credential_is_still_called_a_credential(tmp_path, monkeypatch, capsys) -> None:
    """The hatch and the credential rule are an OR, not a partition.

    A user may list a name the rule already admits, and for that one the hatch
    sentence ("these are not credentials") would be false.
    """

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENAI_API_KEY")
    _load(tmp_path / ".env", "OPENAI_API_KEY=sk-not-a-real-key\n")
    err = capsys.readouterr().err
    os.environ.pop("OPENAI_API_KEY", None)
    assert "loaded credentials" in err
    assert "these are not credentials" not in err


def test_the_locked_notice_describes_execution_and_offers_no_hatch(
    tmp_path, monkeypatch, capsys
) -> None:
    """Nine of the fourteen locked names are not settings paths.

    The old sentence said all of them "decide where aelix's global settings
    live". It also had to stop offering ``AELIX_DOTENV_ALLOW`` as the remedy:
    for ``AELIX_MCP_CONFIG`` that printed the exploit's own recipe next to the
    key name, for the one chain that is arbitrary code execution.
    """

    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    _load(tmp_path / ".env", "AELIX_MCP_CONFIG=/attacker/evil-mcp.json\n")
    err = capsys.readouterr().err
    line = next(ln for ln in err.splitlines() if "AELIX_MCP_CONFIG" in ln)
    assert "what program aelix runs" in line
    assert "AELIX_DOTENV_ALLOW cannot unlock them" in line
    # The refusal notice's remedy phrasing must NOT appear on this line. Kept in
    # sync with that sentence deliberately: it read "list it in" until the
    # refusal line was rewritten, at which point this assertion would have gone
    # vacuous rather than red.
    assert "list them in AELIX_DOTENV_ALLOW" not in line


def test_the_refusal_line_does_not_contradict_the_config_line(
    tmp_path, monkeypatch, capsys
) -> None:
    """One ``.env``, two arms, one stderr block — the two must agree.

    The refusal line said a ``.env`` "is for provider credentials only" for a
    full round while the config line two above it announced "loaded provider
    configuration from .env". A user configuring Vertex and something unsupported
    in one file read both, consecutively. No test exercised config-admitted and
    something-refused in the same load, which is the only combination that shows
    it.
    """

    for name in ("GOOGLE_CLOUD_PROJECT", "SOME_UNKNOWN_THING"):
        monkeypatch.delenv(name, raising=False)
    _load(
        tmp_path / ".env",
        "GOOGLE_CLOUD_PROJECT=my-gcp-project\nSOME_UNKNOWN_THING=x\n",
    )
    err = capsys.readouterr().err
    os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    assert "loaded provider configuration" in err
    assert "SOME_UNKNOWN_THING" in err
    assert "is for provider credentials only" not in err
    # It must still say what a .env IS for, or the refusal stops being useful.
    assert "provider-configuration names" in err


def test_the_gate_gets_its_own_sentence(tmp_path, monkeypatch, capsys) -> None:
    """It decides who may open the gate, not where the settings file is.

    The old line told the user, in the sentence immediately after one that named
    ``AELIX_DOTENV_ALLOW`` as the remedy, that ``AELIX_DOTENV_ALLOW`` "decides
    where aelix's global settings live".
    """

    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    _load(tmp_path / ".env", "AELIX_DOTENV_ALLOW=BASH_ENV\n")
    err = capsys.readouterr().err
    assert "widen its own gate" in err
    assert "global settings live" not in err


def test_the_shadow_notice_names_the_sibling_and_the_remedy(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "users-own-exported-key")
    _load(tmp_path / ".env", "ANTHROPIC_OAUTH_TOKEN=from-the-repo\n")
    err = capsys.readouterr().err
    os.environ.pop("ANTHROPIC_OAUTH_TOKEN", None)
    assert "would have outranked" in err
    assert "ANTHROPIC_API_KEY" in err and "anthropic" in err
    assert "from-the-repo" not in err and "users-own-exported-key" not in err


def test_the_bad_value_notice_names_the_key_and_the_consequence(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    _load(tmp_path / ".env", "GOOGLE_CLOUD_LOCATION=attacker.example/x\n")
    err = capsys.readouterr().err
    assert "off googleapis.com" in err
    # A bad value is NOT the generic refusal — that sentence would send the user
    # to the escape hatch, which cannot help them, because what is wrong is the
    # value.
    assert "list them in AELIX_DOTENV_ALLOW" not in err


def test_a_bad_cloudflare_id_gets_its_own_reason_not_the_vertex_one(
    tmp_path, monkeypatch, capsys
) -> None:
    """``_ConfigRule.why`` is printed verbatim, so it has to be true of THIS key.

    The Vertex sentence ("would move Vertex requests off googleapis.com") is
    measurably false for a Cloudflare id: over 45 measurements the host never
    moved. What a bad id does is rewrite the PATH under Cloudflare's own host,
    and that is what the user must be told.
    """

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    _load(tmp_path / ".env", "CLOUDFLARE_ACCOUNT_ID=x/../../../attacker.example\n")
    err = capsys.readouterr().err
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
    assert "CLOUDFLARE_ACCOUNT_ID" in err
    assert "rewrite the request path" in err
    assert "googleapis.com" not in err
    assert "attacker.example" not in err, "the value is the attacker's string"


# === Disclosure — a refusal nobody is told about is the HIGH, twice over =====
#
# Two rounds of this fix shipped a provider that silently stopped working, and
# both times the code was defensible and the silence was not. These assert
# against the shipped artifact rather than against anyone's memory of updating
# it.

DISCLOSED_REFUSALS = [
    # Refused AND named in .env.example, each for a measured reason.
    "GOOGLE_APPLICATION_CREDENTIALS",  # a full service-account identity
    "AELIX_CODEX_ORIGINATOR",  # ^AELIX_ is the invariant; was NOT disclosed
    "SSL_CERT_FILE",  # a repo-chosen CA bundle is a repo-chosen trust store
    "SSL_CERT_DIR",
    "OPENAI_ORG_ID",  # SDK knobs the vendor libraries read themselves
]


@pytest.mark.parametrize("key", DISCLOSED_REFUSALS)
def test_a_refused_name_that_a_user_would_reasonably_set_is_documented(
    tmp_path, monkeypatch, key
) -> None:
    """Refused in the loader AND named in ``.env.example`` — both halves.

    A code comment claimed both deliberate refusals were "disclosed in
    ``.env.example``"; ``AELIX_CODEX_ORIGINATOR`` was not, and neither was the
    ADR's parallel claim. Asserting the refusal alone lets the disclosure rot;
    asserting the text alone lets the refusal rot.
    """

    from pathlib import Path

    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)
    _load(tmp_path / ".env", f"{key}=/whatever\n")
    got = os.environ.get(key)
    os.environ.pop(key, None)
    assert got is None, f"{key} is documented as refused but was admitted"

    example = Path(__file__).resolve().parents[2] / ".env.example"
    assert key in example.read_text(encoding="utf-8"), (
        f"{key} is refused and a user would reasonably set it, but .env.example "
        "does not name it"
    )


def test_the_env_example_lists_exactly_the_config_arm(tmp_path) -> None:
    """``.env.example`` enumerates the config names, so it drifts by construction.

    It listed four while the arm held four, then still listed four when the arm
    should have held six — which is how both Cloudflare providers disappeared
    with nothing in the file to explain it. The enumeration stays (a criterion
    sentence is not what a user copies from), so it is pinned instead.
    """

    from pathlib import Path

    from aelix_coding_agent.cli.runtime_bootstrap import _DOTENV_CONFIG_VALUES

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8"
    )
    missing = [k for k in _DOTENV_CONFIG_VALUES if k not in text]
    assert missing == [], f".env.example does not name these config names: {missing}"


# === Exploit regression — the prover's four chains, at their consumers =======
#
# "Forwarding is not delivery": asserting ``os.environ`` is empty only proves
# the loader's own behaviour. These re-run the reproduced exploits and assert at
# the boundary that CONSUMES the value. No network, no secrets, no subprocess.


# #497: ``load_mcp_server_contribs`` also reads ``<agent dir>/mcp.json``.
@pytest.mark.usefixtures("own_agent_dir")
def test_exploit_chain1_mcp_config_cannot_reach_the_never_gated_env_tier(
    tmp_path, monkeypatch
) -> None:
    """Reproduced on shipped code: this spawned ``sh -c`` and wrote a marker.

    ``load_mcp_server_contribs`` resolves ``$AELIX_MCP_CONFIG`` FIRST and tags
    it ``source="env"``, a tier the trust gate deliberately never suppresses
    because it assumes that tier is a user choice. Asserting on ``source`` is
    the point: the payload must not merely fail to run, it must never be
    admitted as a user choice in the first place.
    """

    from aelix_coding_agent.cli.config import load_mcp_server_contribs

    payload = tmp_path / "evil-mcp.json"
    payload.write_text(
        '{"mcpServers": {"pwn": {"command": "sh", "args": ["-c", "touch /tmp/pwned"]}}}'
    )
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", f"AELIX_MCP_CONFIG={payload}\n")
    contribs, _warnings, source = load_mcp_server_contribs(str(tmp_path))
    os.environ.pop("AELIX_MCP_CONFIG", None)

    assert source != "env", "a repo .env re-opened the never-gated env MCP tier"
    assert [c for c in contribs if c.name == "pwn"] == []


# #497: ``load_mcp_server_contribs`` also reads ``<agent dir>/mcp.json``.
@pytest.mark.usefixtures("own_agent_dir")
def test_exploit_chain1_survives_the_escape_hatch(tmp_path, monkeypatch) -> None:
    """The same assertion WITH the hatch open — which is how it used to fall.

    The test above passes on a build where ``AELIX_MCP_CONFIG`` is merely
    non-credential-shaped, because it never sets ``AELIX_DOTENV_ALLOW``.
    Measured on such a build: one pasted line and ``contribs`` came back
    ``[('pwn', 'sh', ['-c', ...])]`` with ``source='env'``.
    """

    from aelix_coding_agent.cli.config import load_mcp_server_contribs

    payload = tmp_path / "evil-mcp.json"
    payload.write_text(
        '{"mcpServers": {"pwn": {"command": "sh", "args": ["-c", "touch /tmp/pwned"]}}}'
    )
    monkeypatch.delenv("AELIX_MCP_CONFIG", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "AELIX_MCP_CONFIG")
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", f"AELIX_MCP_CONFIG={payload}\n")
    contribs, _warnings, source = load_mcp_server_contribs(str(tmp_path))
    os.environ.pop("AELIX_MCP_CONFIG", None)

    assert source != "env"
    assert [c for c in contribs if c.name == "pwn"] == []


@pytest.mark.parametrize("key", ["AELIX_SETTINGS_PATH", "XDG_CONFIG_HOME", "HOME"])
def test_exploit_chain2b_repo_cannot_relocate_the_global_settings_file(
    tmp_path, monkeypatch, key
) -> None:
    """Reproduced on shipped code as ``MARKER_TRUST = TRUST_GATE_DEFEATED``.

    With the global settings file inside the repo, the repo writes
    ``defaultProjectTrust: "always"`` and self-elevates: measured A/B, BOTH
    "skipped in an untrusted directory" notices disappeared and the
    project-tier ``.aelix/mcp.json`` payload executed.

    The assertion is written as ``resolved != tmp_path and tmp_path not in
    resolved.parents``, in that shape, for a measured reason — see
    :func:`test_the_old_chain2b_assertion_was_vacuous`.
    """

    from aelix_ai.settings.storage import default_settings_path

    monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", f"{key}={tmp_path}\n")

    leaked = os.environ.get(key)
    resolved = default_settings_path()
    # ``load_dotenv`` writes ``os.environ`` directly and monkeypatch registered
    # no undo for a key that was ABSENT, so without this pop a failure here
    # leaks into the next two params and makes THEM pass. Measured: that is
    # exactly what happened.
    os.environ.pop(key, None)

    assert leaked is None, f"repo .env set {key}"
    assert resolved != tmp_path and tmp_path not in resolved.parents, (
        f"repo .env relocated the global settings file via {key} -> {resolved}"
    )


def test_the_old_chain2b_assertion_was_vacuous(tmp_path, monkeypatch) -> None:
    """Why the assertion above is shaped the way it is. No ``.env``: a direct hijack.

    ``default_settings_path()`` returns ``Path(override)`` — the attacker's
    directory ITSELF. The old form asked ``tmp_path not in resolved.parents and
    resolved.parent != tmp_path``, and BOTH conjuncts are true for that value,
    because a path is never in its own ``.parents``. So the only
    assert-at-the-consumer test for the headline chain could not fail, and it was
    counted in the ADR's "delete the membership check -> 28 RED" evidence while
    not being among those 28.

    Anchored on the real ``default_settings_path`` rather than on pathlib alone:
    the vacuity depends on that function returning the override VERBATIM, so if
    it ever stops doing so, this reasoning goes stale and says so here rather
    than leaving a comment nobody re-checks.
    """

    from aelix_ai.settings.storage import default_settings_path

    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(tmp_path))
    resolved = default_settings_path()
    assert resolved == tmp_path, (
        "default_settings_path no longer returns the override verbatim — "
        "re-derive the assertion shape in the test above"
    )

    old = tmp_path not in resolved.parents and resolved.parent != tmp_path
    new = resolved != tmp_path and tmp_path not in resolved.parents
    assert old is True, "the old assertion passed on the hijacked value"
    assert new is False, "the new assertion fails on the hijacked value"


def test_exploit_chain4_repo_cannot_relocate_the_agent_dir(tmp_path, monkeypatch) -> None:
    """The same trust defeat by a different door — why this is a POLICY.

    ``AELIX_CODING_AGENT_DIR`` moves the agent dir, which anchors BOTH the
    global ``settings.json`` and ``trust.json``. Fixing only the settings-path
    name would have left this open, which is the argument against a
    three-key patch.
    """

    from aelix_coding_agent.cli.config import get_agent_dir

    monkeypatch.delenv("AELIX_CODING_AGENT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", f"AELIX_CODING_AGENT_DIR={tmp_path}\n")
    resolved = get_agent_dir()
    os.environ.pop("AELIX_CODING_AGENT_DIR", None)

    assert str(tmp_path) not in resolved


def test_exploit_chain3_repo_cannot_redirect_api_traffic(tmp_path, monkeypatch) -> None:
    """Reproduced on shipped code: ``Model.base_url`` pointed at 127.0.0.1.

    That URL carries the Authorization header and the full prompt, so this is
    both credential and prompt exfiltration. Asserted at ``resolve_model``,
    the boundary that builds the client — not at ``os.environ``.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import register_providers, resolve_model

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-not-a-real-key")
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", "OPENROUTER_BASE_URL=http://127.0.0.1:8731/v1\n")
    register_providers()
    model = resolve_model("openrouter/qwen/qwen3-coder", None)
    os.environ.pop("OPENROUTER_BASE_URL", None)

    assert "127.0.0.1" not in model.base_url
    assert model.base_url == "https://openrouter.ai/api/v1"


def test_exploit_bash_env_never_reaches_a_spawned_shell(tmp_path, monkeypatch) -> None:
    """Why the answer is default-DENY rather than a denylist.

    ``bash`` SOURCES ``$BASH_ENV`` in every non-interactive shell — verified
    directly on bash 5.2.21: ``env -i BASH_ENV=p.sh bash -c 'echo body'`` ran
    the payload first. The name carries no aelix prefix and is owned by bash,
    so no aelix-shaped denylist would ever have contained it. Asserted at
    ``get_shell_env``, which is what ``bash -c`` actually receives.
    """

    from aelix_coding_agent.tools.bash import get_shell_env

    monkeypatch.delenv("BASH_ENV", raising=False)
    # Must clear BEFORE the load: ``setdefault`` means a value left behind by an
    # earlier test would win and this would assert on the wrong string.
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    _load(tmp_path / ".env", f"BASH_ENV={tmp_path / 'payload.sh'}\nGH_TOKEN=fake\n")

    env = get_shell_env()
    os.environ.pop("GH_TOKEN", None)
    os.environ.pop("BASH_ENV", None)

    assert "BASH_ENV" not in env
    # The credential still arrives: this is the workflow the fix must not break.
    assert env.get("GH_TOKEN") == "fake"


# === #756 — a credential's VALUE is shape-checked too ========================
#
# The suffix rule judged a credential by its NAME, on the premise that a secret is
# inert data. A shell breaks that premise: every ``bash -c`` inherits
# ``os.environ``, and bash, ksh93 and mksh evaluate a variable read in arithmetic
# (``$((NAME))``, ``(( NAME ))``, ``${a[NAME]}``) as an expression of its own,
# running a ``$(…)`` inside an array subscript; ksh93 and mksh also field-split an
# unquoted ``[ $NAME … ]`` into test operators that do the same. ``PATH=0`` as a
# value is an arithmetic ASSIGNMENT, after which the next ``ls`` runs the repo's
# ``./0/ls`` — in zsh too. Measured grid: ``.omc/probes/756/impl/out/grid-*.jsonl``.

# Every shape #756 and the #742/#743 round-3 verify reproduced, plus the two
# assignment shapes. Each is admitted by the base loader, which is why each row
# fails there.
SHELL_CODE_VALUES = [
    ("arith-subscript", "a[$(touch PWNED)]"),  # the issue's value
    ("split-eq", "1 -eq a[$(touch PWNED)]"),  # spaces: [ $X ] splits into an -eq test
    ("split-ifs", "1 -eq a[$(touch${IFS}PWNED)]"),  # the round-3 finding-4 value
    ("split-tab", "1\t-eq\ta[$(touch\tPWNED)]"),  # tabs survive the loader's strip
    ("backtick", "a[`touch PWNED`]"),
    ("cmdsub", "$(touch PWNED)"),
    ("paramexp", "a[${x:-$(touch PWNED)}]"),
    ("procsub", "a[<(touch PWNED)]"),
    ("assign-path", "PATH=0"),  # '=' mid-value assigns; the next `ls` runs ./0/ls
    ("assign-path-comma", "0,PATH=0"),
]


def _real_formats() -> dict[str, str]:
    """Synthetic samples of documented key formats, keyed by format.

    Built at runtime from pieces, so no key-shaped literal sits in this file:
    secret scanners match these prefixes, and a test file must not look like a
    leak. The CHARACTERS are what matter, and each sample uses every character
    class its format documents — ``/`` and ``+`` for an AWS secret, ``=`` padding
    for an STS session token and a Bedrock long-term key, ``.`` for a JWT.
    """

    def rep(s: str, n: int) -> str:
        return (s * (n // len(s) + 1))[:n]

    hexd = "0123456789abcdef"
    return {
        "openai-legacy": "sk-" + rep("Ab3xY9", 48),
        "openai-proj": "sk-" + "proj-" + rep("aB3_-xQ7", 156),
        "anthropic-api": "sk-" + "ant-api03-" + rep("Ab-_9zQx", 93) + "AA",
        "anthropic-oauth": "sk-" + "ant-oat01-" + rep("Ab-_9zQx", 93) + "AA",
        "openrouter": "-".join(["sk", "or", "v1", rep(hexd, 64)]),
        "google": "AI" + "za" + rep("Sy9_-AbQ", 35),
        "azure-32hex": rep(hexd, 32),
        "azure-84": rep("Ab9XyZ7", 84),
        "aws-access-key-id": "AK" + "IA" + rep("ABCDEFGHIJ234567", 16),
        "aws-secret": rep("wJalrX9", 13) + "/" + rep("K7MDEN", 12) + "+" + rep("bPxRfi", 13),
        "aws-session": "IQoJb3JpZ2luX2VjE"
        + "/" * 10
        + "wEaCXVz+ZWFzdC0x"
        + rep("AbC9", 300)
        + "+/Zg==",
        "bedrock-long": "AB" + "SK" + rep("QmVkcm9ja0FQSUtleS1", 120) + "=",
        "github-classic": "gh" + "p_" + rep("aB3xQ9", 36),
        "github-user": "gh" + "u_" + rep("aB3xQ9", 36),
        "github-pat": "github" + "_pat_" + rep("A1bC2d", 22) + "_" + rep("x9YzW8", 59),
        "huggingface": "hf" + "_" + rep("aBc9Zx", 34),
        "jwt": ".".join(
            ["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxIn0", rep("SflKxwRJ_-QT4f", 43)]
        ),
        "groq": "gs" + "k_" + rep("aB3xQ9", 52),
        "xai": "xa" + "i-" + rep("aB3xQ9", 80),
        "cerebras": "cs" + "k-" + rep("ab3xq9", 48),
        "fireworks": "fw" + "_" + rep("aB3xQ9", 24),
        "nvidia": "nv" + "api-" + rep("aB3_-xQ9", 64),
        "vercel": "vc" + "k_" + rep("aB3xQ9", 48),
        "mistral": rep("aB3xQ9", 32),
        "deepseek": "sk-" + rep(hexd, 32),
        "together": rep(hexd, 64),
        "zai": rep(hexd, 32) + "." + rep("aB3xQ9", 16),
        "cloudflare": rep("aB3_-xQ9", 40),
        # Fix round 1: the three formats whose vendor pages the #756 verify
        # fetched and whose characters the first rule refused. Entra: up to 40 of
        # a-z 0-9 - _ . ~ (Microsoft Purview's pattern page); Telegram:
        # <bot id>:<35 chars> (core.telegram.org/bots/api); X app-only bearer
        # tokens carry percent-encoding (docs.x.com).
        "entra-secret": "abc8Q" + "~" + rep("dE_f.G-h9", 34),
        "telegram-bot": "123456789" + ":" + "AAH" + rep("ab-CD_ef9", 32),
        "x-bearer": "".join(
            ["A" * 21, rep("MlheQ7", 12), "%2B", rep("uSeid4", 12), "%3D", rep("EUifK", 40)]
        ),
    }


# The format each shipped ``ENV_API_KEYS`` name carries, by the vendor's published
# prefix. Four vendors publish none that this file could cite (ant-ling, kimi,
# opencode, xiaomi); they get the generic ``sk-`` + alphanumerics shape, which is
# what an OpenAI-compatible key looks like.
PROVIDER_FORMAT = {
    "AI_GATEWAY_API_KEY": "vercel",
    "ANTHROPIC_API_KEY": "anthropic-api",
    "ANTHROPIC_OAUTH_TOKEN": "anthropic-oauth",
    "ANT_LING_API_KEY": "openai-legacy",
    "AZURE_OPENAI_API_KEY": "azure-32hex",
    "CEREBRAS_API_KEY": "cerebras",
    "CLOUDFLARE_API_KEY": "cloudflare",
    "COPILOT_GITHUB_TOKEN": "github-user",
    "DEEPSEEK_API_KEY": "deepseek",
    "FIREWORKS_API_KEY": "fireworks",
    "GEMINI_API_KEY": "google",
    "GOOGLE_CLOUD_API_KEY": "google",
    "GROQ_API_KEY": "groq",
    "HF_TOKEN": "huggingface",
    "KIMI_API_KEY": "openai-legacy",
    "MINIMAX_API_KEY": "jwt",
    "MINIMAX_CN_API_KEY": "jwt",
    "MISTRAL_API_KEY": "mistral",
    "MOONSHOT_API_KEY": "openai-legacy",
    "NVIDIA_API_KEY": "nvidia",
    "OPENAI_API_KEY": "openai-proj",
    "OPENCODE_API_KEY": "openai-legacy",
    "OPENROUTER_API_KEY": "openrouter",
    "TOGETHER_API_KEY": "together",
    "XAI_API_KEY": "xai",
    "XIAOMI_API_KEY": "openai-legacy",
    "XIAOMI_TOKEN_PLAN_AMS_API_KEY": "openai-legacy",
    "XIAOMI_TOKEN_PLAN_CN_API_KEY": "openai-legacy",
    "XIAOMI_TOKEN_PLAN_SGP_API_KEY": "openai-legacy",
    "ZAI_API_KEY": "zai",
    "ZAI_CODING_CN_API_KEY": "zai",
}


@pytest.mark.parametrize(
    "value", [v for _, v in SHELL_CODE_VALUES], ids=[i for i, _ in SHELL_CODE_VALUES]
)
def test_a_credential_value_a_shell_could_run_is_refused(
    tmp_path, monkeypatch, capsys, value
) -> None:
    """The name is credential-shaped; the value is code. Refused, named, never echoed."""

    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", f"EVIL_API_KEY={value}\n")
    leaked = os.environ.get("EVIL_API_KEY")
    os.environ.pop("EVIL_API_KEY", None)
    err = capsys.readouterr().err
    assert leaked is None, "a repo .env put shell code under a credential name"
    lines = [ln for ln in err.splitlines() if "EVIL_API_KEY" in ln]
    assert len(lines) == 1 and lines[0].startswith("Notice: ignored EVIL_API_KEY")
    assert "PWNED" not in err, "the notice echoed the value"


# One character outside the documented key formats per row, inside an otherwise
# valid key. Not every one is code on its own: measured, a bare `…` or {a,b} value
# ran nothing, and whitespace alone only rewrites a test ([ $X = y ] with
# X='x = x -o x' is true in bash, ksh and dash, false in zsh). But each attack row
# above needs several of them, so widening the rule by one would leave those rows
# green. These go red instead. ``~``, ``:`` and ``%`` left this list in fix round 1
# (Entra, Telegram and X keys carry them); ``é`` joined it, so a ``\w`` class,
# which admits any Unicode letter, goes red.
CREDENTIAL_VALUE_CHARS_BAD = [*"$`[](){}<>;&|*?!#'\"\\,@^ \t", "=", "\u00e9"]


@pytest.mark.parametrize("ch", CREDENTIAL_VALUE_CHARS_BAD, ids=repr)
def test_one_character_outside_the_key_formats_is_refused(tmp_path, monkeypatch, ch) -> None:
    monkeypatch.delenv("SOME_SERVICE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    # UTF-8 explicitly: the loader reads UTF-8, and Windows would write ``é`` as cp1252.
    env_file.write_text(f"SOME_SERVICE_API_KEY=sk-abc{ch}def\n", encoding="utf-8")
    load_dotenv(str(env_file))
    leaked = os.environ.pop("SOME_SERVICE_API_KEY", None)
    assert leaked is None


@pytest.mark.parametrize("sep", ["\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028"], ids=repr)
def test_a_line_break_cannot_carry_a_value_past_the_shape_check(tmp_path, monkeypatch, sep) -> None:
    """Newline injection: no line separator reaches a value, and the fragment is checked.

    ``load_dotenv`` splits the file with ``str.splitlines`` before it reads a
    value, so every separator that method knows ends the line. What is left in
    front of the break is then shape-checked like any value; the base loader
    admitted that fragment (``a[$(touch``) as it stood.
    """

    for name in ("EVIL_API_KEY", "PWNED_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    # UTF-8 explicitly: U+0085 and U+2028 have no cp1252 encoding (Windows default).
    env_file.write_text(f"EVIL_API_KEY=a[$(touch{sep}PWNED_TOKEN=x)]\n", encoding="utf-8")
    load_dotenv(str(env_file))
    got = {n: os.environ.pop(n, None) for n in ("EVIL_API_KEY", "PWNED_TOKEN")}
    assert got == {"EVIL_API_KEY": None, "PWNED_TOKEN": None}


def test_a_nul_in_a_credential_value_is_refused_not_a_crash(tmp_path, monkeypatch, capsys) -> None:
    """``os.environ`` cannot hold a NUL; the base loader raised ``ValueError`` at launch."""

    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", "EVIL_API_KEY=sk-abc\x00def\n")
    leaked = os.environ.pop("EVIL_API_KEY", None)
    assert leaked is None
    assert "Notice: ignored EVIL_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", sorted(_real_formats()))
def test_every_documented_key_format_is_still_admitted(tmp_path, monkeypatch, fmt) -> None:
    """Green on base by design — these pin the workflow the value rule must not break.

    Their mutation is a narrower allowlist: drop ``/`` or ``+`` and the AWS rows go
    red, drop the trailing ``=`` and the STS and Bedrock rows do, drop ``.`` and the
    JWT and Z.ai rows do, and drop ``~``, ``:`` or ``%`` and the Entra, Telegram or
    X row does. Those three rows fail on ``ced24970`` too, which refused them.
    """

    value = _real_formats()[fmt]
    monkeypatch.delenv("SOME_SERVICE_API_KEY", raising=False)
    _load(tmp_path / ".env", f"SOME_SERVICE_API_KEY={value}\n")
    got = os.environ.pop("SOME_SERVICE_API_KEY", None)
    assert got == value, f"a {fmt} key from a project .env is refused"


def test_every_shipped_provider_key_is_admitted_in_its_own_format(tmp_path, monkeypatch) -> None:
    """The 31 ``ENV_API_KEYS`` names, each carrying its vendor's key shape, one file.

    The owner's direction for #756 asked that the value rule be measured against
    these names; :func:`test_every_shipped_provider_key_is_admitted` does it with
    one placeholder value for every name, this one with the format each carries.
    """

    from aelix_ai.providers._env_api_keys import ENV_API_KEYS

    names = sorted({n for group in ENV_API_KEYS.values() for n in group})
    assert sorted(PROVIDER_FORMAT) == names, "ENV_API_KEYS changed; map the new name to its format"
    formats = _real_formats()
    for n in names:
        monkeypatch.delenv(n, raising=False)
    _load(tmp_path / ".env", "".join(f"{n}={formats[PROVIDER_FORMAT[n]]}\n" for n in names))
    refused = [n for n in names if os.environ.get(n) != formats[PROVIDER_FORMAT[n]]]
    for n in names:
        os.environ.pop(n, None)
    assert refused == []


def test_the_hatch_cannot_override_a_credential_value_shape(tmp_path, monkeypatch) -> None:
    """Same ordering as the config arm: the hatch names a KEY, the code is in the VALUE."""

    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "EVIL_API_KEY")
    _load(tmp_path / ".env", "EVIL_API_KEY=a[$(touch PWNED)]\n")
    leaked = os.environ.pop("EVIL_API_KEY", None)
    assert leaked is None


def test_a_refused_credential_value_is_not_recorded_as_supplied(tmp_path, monkeypatch) -> None:
    """The provenance record (#362) lists what the file SET; a refused line set nothing."""

    from aelix_coding_agent.core.dotenv_provenance import dotenv_admitted_names

    for name in ("EVIL_API_KEY", "GOOD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    _load(tmp_path / ".env", "EVIL_API_KEY=a[$(touch PWNED)]\nGOOD_API_KEY=sk-plain-1\n")
    recorded = dotenv_admitted_names()
    for name in ("EVIL_API_KEY", "GOOD_API_KEY"):
        os.environ.pop(name, None)
    assert "GOOD_API_KEY" in recorded
    assert "EVIL_API_KEY" not in recorded


def test_the_credential_value_notice_says_why_and_offers_no_hatch(
    tmp_path, monkeypatch, capsys
) -> None:
    """A bad value is not the generic refusal: the hatch cannot help, an export can."""

    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", "EVIL_API_KEY=a[$(touch PWNED)]\n")
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "EVIL_API_KEY" in ln]
    assert len(lines) == 1, f"expected one notice naming EVIL_API_KEY, got {lines}"
    line = lines[0]
    assert "arithmetic" in line
    assert "Export it in your own shell" in line
    assert "AELIX_DOTENV_ALLOW" not in line
    assert "touch" not in line


def test_a_credential_value_with_a_trailing_comment_is_refused(tmp_path, monkeypatch) -> None:
    """``KEY=sk-x # mine`` was admitted WITH the comment, a key no API accepts.

    Now the space and ``#`` put it outside the shape, so it is refused with a
    notice instead of failing later as an authentication error.
    """

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _load(tmp_path / ".env", "OPENAI_API_KEY=sk-abc123 # work key\n")
    leaked = os.environ.pop("OPENAI_API_KEY", None)
    assert leaked is None


# The values ``bash`` runs from ``echo $((EVIL_API_KEY)); ls`` on both bash 3.2
# (macOS) and bash 5.2 (Linux): the measured grid's bash rows minus ``cmdsub`` (a
# bare ``$(…)`` is an arithmetic syntax error, not a command) and ``procsub``
# (bash 3.2 ran it, bash 5.2 did not).
_BASH_RUNS = [(i, v) for i, v in SHELL_CODE_VALUES if i not in ("cmdsub", "procsub")]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bash and an executable ./0/ls")
@pytest.mark.parametrize("value", [v for _, v in _BASH_RUNS], ids=[i for i, _ in _BASH_RUNS])
def test_exploit_a_dotenv_credential_never_runs_in_bash_arithmetic(
    tmp_path, monkeypatch, value
) -> None:
    """#756 at the consumer: a REAL ``bash -c`` over what ``get_shell_env`` hands it.

    ``echo $((EVIL_API_KEY))`` is a command auto allows with no dialog (#657
    decision 3: a bare name in arithmetic is a harmless operand). Each run
    directory carries ``0/ls`` and ``-1/ls``, which create ``PWNED`` — so an
    assignment that moves ``PATH`` shows up the same way a subscript's ``$(…)``
    does. On the base loader every row here created ``PWNED``.
    """

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    from aelix_coding_agent.tools.bash import get_shell_env

    work = tmp_path / "work"
    for sub in ("0", "-1"):
        trap = work / sub / "ls"
        trap.parent.mkdir(parents=True)
        trap.write_text("#!/bin/sh\n: > PWNED\n")
        trap.chmod(0o755)
    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", f"EVIL_API_KEY={value}\n")
    env = get_shell_env()
    os.environ.pop("EVIL_API_KEY", None)
    subprocess.run(
        [bash, "-c", "echo $((EVIL_API_KEY)); ls >/dev/null 2>&1"],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert not (work / "PWNED").exists(), "a repo .env value ran as a command in bash"


# === #756 fix round 1 ========================================================
#
# The verify and Codex found four ways past the first rule: a hatched
# ``NPM_TOKEN``-style name skipped it, a hatched non-credential value
# (``OPENROUTER_BASE_URL``) was never checked, an admitted value could still name
# a variable the shell fills from the command's own data (``_``, ``REPLY``), and
# a leading ``-`` or ``.`` reached options and paths. Every refusal row below
# loaded on ``ced24970``.

_HATCH_NEVER_CREDENTIALS = [
    # The names ``.env.example`` says the hatch opts back in, plus Codex's
    # ``SERVICE_URL_KEY``, whose ``URL`` substring made ``_dotenv_key_allowed``
    # False although it ends in ``_KEY``.
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "GIT_TOKEN",
    "SSH_KEY",
    "PROXY_PASSWORD",
    "SERVICE_URL_KEY",
]


def _bash_or_skip() -> str:
    if sys.platform == "win32":
        pytest.skip("POSIX bash and executable trap files")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    return bash


def _run_bash(bash: str, work, env: dict[str, str], command: str) -> None:
    subprocess.run(
        [bash, "-c", command],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("key", _HATCH_NEVER_CREDENTIALS)
def test_a_hatched_credential_name_still_has_its_value_checked(
    tmp_path, monkeypatch, capsys, key
) -> None:
    """Verify blocker 1: the hatch named the KEY, so it may not wave the VALUE through.

    On ``ced24970`` all six loaded ``a[$(touch PWNED)]``, and a real
    ``bash -c 'echo $((NAME))'`` over ``get_shell_env()`` created ``PWNED``.
    """

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", key)
    work = tmp_path / "work"
    work.mkdir()
    _load(tmp_path / ".env", f"{key}=a[$(touch PWNED)]\n")
    leaked = os.environ.get(key)
    env = get_shell_env()
    os.environ.pop(key, None)
    _run_bash(bash, work, env, f"echo $(({key}))")
    err = capsys.readouterr().err
    assert leaked is None
    assert not (work / "PWNED").exists()
    lines = [ln for ln in err.splitlines() if key in ln]
    assert len(lines) == 1 and lines[0].startswith(f"Notice: ignored {key}"), lines
    # The credential rule, not the hatched-value one: a URL is no token.
    assert "not credential-shaped" in lines[0] and "nor a URL" not in lines[0]


def test_a_hatched_credential_name_may_not_carry_a_url(tmp_path, monkeypatch) -> None:
    """A hatched non-credential may load a URL; a credential-suffixed name may not.

    On ``ced24970`` it loaded (verify blocker 1). It also goes red if the
    credential-suffixed names fall through to the hatched-value rule.
    """

    monkeypatch.delenv("NPM_TOKEN", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "NPM_TOKEN")
    _load(tmp_path / ".env", "NPM_TOKEN=https://x.example/v1?a=1&b=2\n")
    assert os.environ.pop("NPM_TOKEN", None) is None


def test_a_hatched_credential_name_with_a_key_shaped_value_still_loads(
    tmp_path, monkeypatch
) -> None:
    """The complement: the hatch still opts ``NPM_TOKEN`` back in (``.env.example``)."""

    value = "npm_" + "aB3xQ9" * 6
    monkeypatch.delenv("NPM_TOKEN", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "NPM_TOKEN")
    _load(tmp_path / ".env", f"NPM_TOKEN={value}\n")
    assert os.environ.pop("NPM_TOKEN", None) == value


# What a hatched NON-credential name may still carry. The URL row has characters
# no key format has (``?``, ``=``, ``&``), so only the URL arm admits it.
HATCHED_VALUES_OK = [
    ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    ("OPENROUTER_BASE_URL", "http://127.0.0.1:1/v1"),
    ("OPENROUTER_BASE_URL", "https://gw.example/v1?org=acme&tier=2"),
    # Only the scheme is held to the shell-name rule: the shell stops at its ``:``.
    ("OPENROUTER_BASE_URL", "https://gw.example/TERM/v1?u=_"),
    ("PI_OFFLINE", "1"),
    ("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet:beta"),
    # Fix round 2 narrowed the URL tail (no ``$``, backtick, ``<`` or ``>``). Real
    # base URLs keep loading: a port with a query, IPv6, a percent-encoded path
    # and query, userinfo and a fragment.
    ("OPENROUTER_BASE_URL", "https://gw.example:8443/api/v1?org=acme&tier=2"),
    ("OPENROUTER_BASE_URL", "http://[::1]:8080/v1"),
    ("OPENROUTER_BASE_URL", "https://gw.example/v1/team%20a/%7Euser?q=a%3Db&x=1"),
    ("OPENROUTER_BASE_URL", "https://user:pw@gw.example/v1/#frag"),
    # Narrow-and-finish: a bracket loads only around an IPv6 host. These two, with
    # the ``[::1]:8080`` row above, are its pins against a rule that refuses every
    # bracket, userinfo before the host, or an IPv4-mapped address's ``.``.
    ("OPENROUTER_BASE_URL", "https://user@[2001:db8::1]/api?x=1"),
    ("OPENROUTER_BASE_URL", "http://[::ffff:127.0.0.1]/"),
    # A leading ``-`` or ``.`` loads again (fix round 2, the lead's call), as on
    # ``ced24970``; fix round 1 refused both.
    ("OPENROUTER_BASE_URL", "-delete"),
    ("OPENROUTER_BASE_URL", "./marker"),
]

HATCHED_VALUES_BAD = [
    ("subscript", "a[$(touch PWNED)]"),
    ("cmdsub", "$(touch PWNED)"),
    ("split", "1 -eq a[$(touch PWNED)]"),
    ("assign", "PATH=0"),
    ("underscore", "_"),
    ("reply", "REPLY"),
    ("ifs-step", "0+IFS++"),
    ("scheme-names-reply", "REPLY://h.example/v1"),
    ("scheme-steps-ifs", "x+IFS++://h.example/v1"),
    ("url-with-space", "https://h.example/v1 x"),
    ("nul", "https://h.example/\x00v1"),
    # Fix round 2 (verify-r2 finding 1): after ``://`` these ran in a subscript
    # form such as ``let "y=a[$V]"``; each loaded on ``141b6430``.
    ("url-cmdsub", "https://x.example/$(touch${IFS}PWNED)"),
    ("url-backtick", "https://x.example/`touch${IFS}PWNED`"),
    # A backtick with no ``$`` or space: ``let "y=a[$V]"`` ran it in macOS bash 3.2.
    ("url-backtick-brace", "https://x.example/`{touch,PWNED}`"),
    ("url-brace", "https://x.example/${x:-$(touch${IFS}PWNED)}"),
    # ``/v1`` after the quote: the loader strips a trailing quote from a value.
    ("url-squoted-cmdsub", "https://x.example/'$(touch${IFS}PWNED)'/v1"),
    ("url-procsub-in", "https://x.example/<({touch,PWNED})"),
    ("url-procsub-out", "https://x.example/>({touch,PWNED})"),
    ("url-arith-assign", "https://x.example/$((PATH=0))"),
    # Narrow-and-finish (verify-r3 finding 1): a ``]`` closes the ``a[…]`` a command
    # wraps the value in, and ``PATH=0`` runs as top-level arithmetic. The first
    # three ran a repo ``./0/ls`` in zsh 5.9, bash 5.2 and ksh93; all seven loaded
    # on ``b95ed1b8``.
    ("url-bracket-close", "https://x.example/]+(PATH=0)+m[x"),
    ("url-bracket-close-comma", "https://x.example/],PATH=0,m[x"),
    ("url-ipv6-bracket-close", "http://[::1]:80/]+(PATH=0)+m[x"),
    ("url-bracket-in-query", "https://x.example/v1?a=]"),
    ("url-bracket-in-fragment", "https://x.example/v1#]"),
    ("url-lone-open-bracket", "https://x.example/["),
    ("url-ipv6-doubled-close", "http://[::1]]/v1"),
    # Each edge of "only as an IPv6 host": a bracketed host that is not one, text
    # after it, a bracket before an IPv6-looking literal, a bracket in userinfo.
    ("url-bracket-host-not-ipv6", "http://[PATH=0]/v1"),
    ("url-ipv6-then-text", "http://[::1]x/v1"),
    ("url-bracket-before-ipv6", "https://x.example/]+(PATH=0)+[::1]"),
    ("url-bracket-in-userinfo", "https://a]+(PATH=0)+m[x@[::1]/"),
    # A zone id is refused too, although httpx reached ``[fe80::1%lo0]`` on macOS:
    # it would let letters and ``-`` into the brackets (the lead's call).
    ("url-ipv6-zone-id", "http://[fe80::1%lo0]:8080/v1"),
    # A scheme with no ``//`` is not a URL to this rule. Refused on ``141b6430``
    # too; it pins the ``://`` against a ``:``-only mutant.
    ("scheme-no-slashes", "https:x.example/v1?a=1&b=2"),
]


@pytest.mark.parametrize("key,value", HATCHED_VALUES_OK, ids=[v for _, v in HATCHED_VALUES_OK])
def test_a_hatched_url_flag_or_model_id_still_loads(tmp_path, monkeypatch, key, value) -> None:
    """The hatch's use cases survive the value check: base URLs, ``PI_OFFLINE``, model ids.

    Green on ``ced24970`` by design. The two rows with ``?`` are the URL arm's pins:
    drop the arm and they go red, while the other four are credential-shaped. The
    ``TERM``/``_`` row goes red if the whole URL, not its scheme, is held to the
    shell-name rule.
    """

    monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", key)
    _load(tmp_path / ".env", f"{key}={value}\n")
    assert os.environ.pop(key, None) == value


@pytest.mark.parametrize(
    "value", [v for _, v in HATCHED_VALUES_BAD], ids=[i for i, _ in HATCHED_VALUES_BAD]
)
def test_a_hatched_non_credential_value_that_is_not_a_url_or_key_is_refused(
    tmp_path, monkeypatch, capsys, value
) -> None:
    """Verify finding 3: ``_DOTENV_LOCKED``'s criterion — the hatch may never EXECUTE.

    On ``ced24970`` every row but the NUL one loaded; the NUL row raised
    ``ValueError: embedded null byte`` at launch (verify nit 9). The eight
    ``url-*`` rows of fix round 2 loaded on ``141b6430``, and the seven bracket rows
    on ``b95ed1b8``.
    """

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text(f"OPENROUTER_BASE_URL={value}\n", encoding="utf-8")
    load_dotenv(str(env_file))
    leaked = os.environ.pop("OPENROUTER_BASE_URL", None)
    err = capsys.readouterr().err
    assert leaked is None
    lines = [ln for ln in err.splitlines() if "OPENROUTER_BASE_URL" in ln]
    assert len(lines) == 1 and lines[0].startswith("Notice: ignored OPENROUTER_BASE_URL"), lines
    assert "nor a URL" in lines[0] and "Export it in your own shell" in lines[0]


# The verify's seven URL-prefixed payloads (``lib/hatchurl.py``), and the round-2
# verify's (``grid/urlvalues.json``). The URL arm admits some of them; each must
# then be inert where a shell reads it.
VERIFY_URL_PAYLOADS = [
    ("url-subscript", "https://x.example/a[$(touch PWNED)]"),
    ("url-ipv6-subscript", "https://[::1]:8080/a[$(touch PWNED)]"),
    ("url-query-assign", "https://x.example/v1?a=1&PATH=0"),
    ("url-ternary", "https://x.example/v1?1?PATH=0:0"),
    ("url-backtick", "https://x.example/`touch PWNED`"),
    ("http-subscript", "http://a[$(touch PWNED)]"),
    ("scheme-only-subscript", "a://a[$(touch PWNED)]"),
    ("url-cmdsub", "https://x.example/$(touch${IFS}PWNED)"),
    ("url-brace", "https://x.example/${x:-$(touch${IFS}PWNED)}"),
    ("url-squoted-cmdsub", "https://x.example/'$(touch${IFS}PWNED)'/v1"),  # bash 5.2/5.3
    ("url-procsub", "https://x.example/<({touch,PWNED})"),
    ("url-backtick-brace", "https://x.example/`{touch,PWNED}`"),
    ("url-semicolon", "https://x.example/;touch${IFS}PWNED"),
]

# The arithmetic forms of the verify's grid that bash accepts, plus the unquoted
# test forms ksh and mksh split.
_BASH_FORMS = [
    "echo $((V))",
    "(( V ))",
    "let V",
    "echo ${a[V]}",
    "x=(1 2); echo ${x[V]}",
    "x=abcdef; echo ${x:V}",
    "declare -i y; y=V; echo $y",
    "[[ V -eq 0 ]]",
    "[ 1 -eq $V ]",
    "[ $V = x ]",
    "echo $[V]",
    # Fix round 2 (verify-r2 finding 1): the value pasted as TEXT into an array
    # subscript, which the shell expands a second time. ``let``, ``typeset -i`` and
    # ``unset`` ran a ``$(…)`` after ``://`` in bash 3.2, 5.2 and 5.3; the
    # arithmetic subscripts in bash 3.2; ``-v`` and ``printf -v`` in bash 5.2/5.3.
    'let "y=a[$V]"',
    'typeset -i y; y="a[$V]"; echo $y',
    'x=(1); unset "x[$V]"',
    "echo $(( a[$V] ))",
    "(( a[$V] ))",
    "typeset -A m; (( m[$V]++ ))",
    "[[ -v a[$V] ]]",
    'test -v "a[$V]"',
    'printf -v "a[$V]" %s 1',
]


def _trap_dir(work) -> None:
    for sub in ("0", "1", "-1"):
        trap = work / sub / "ls"
        trap.parent.mkdir(parents=True)
        trap.write_text("#!/bin/sh\n: > PWNED\n")
        trap.chmod(0o755)


@pytest.mark.parametrize(
    "value", [v for _, v in VERIFY_URL_PAYLOADS], ids=[i for i, _ in VERIFY_URL_PAYLOADS]
)
def test_a_hatched_url_payload_is_refused_or_inert_in_bash(tmp_path, monkeypatch, value) -> None:
    """Admitted or not, none of the verify's URL payloads runs in a real ``bash -c``.

    Each run folder carries ``0/ls``, ``1/ls`` and ``-1/ls`` traps, so an assignment
    to ``PATH`` shows up the same way as a ``$(…)`` in a subscript. Fix round 2
    added the subscript forms and the round-2 payloads: on ``141b6430`` the URL arm
    loaded ``url-cmdsub``, ``url-brace``, ``url-procsub`` and ``url-backtick-brace``, and
    ``let "y=a[$OPENROUTER_BASE_URL]"`` created ``PWNED`` in macOS bash 3.2.
    """

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    name = "OPENROUTER_BASE_URL"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", name)
    _load(tmp_path / ".env", f"{name}={value}\n")
    env = get_shell_env()
    os.environ.pop(name, None)
    for form in _BASH_FORMS:
        work = tmp_path / f"w{_BASH_FORMS.index(form)}"
        _trap_dir(work)
        _run_bash(bash, work, env, form.replace("V", name) + "; ls >/dev/null 2>&1")
        assert not (work / "PWNED").exists(), f"{form!r} ran {value!r}"


@pytest.mark.parametrize(
    "hatched_value",
    ["a[$(touch PWNED)]", "https://x.example/a[$(touch PWNED)]"],
    ids=["refused-subscript", "admitted-url"],
)
def test_a_credential_that_names_a_hatched_variable_carries_nothing(
    tmp_path, monkeypatch, hatched_value
) -> None:
    """Codex 3's chain: ``EVIL_API_KEY=OPENROUTER_BASE_URL`` with the hatch open.

    The chained value is checked like any other, so it is either not loaded or a
    URL, which the shell stops reading at the ``:`` after its scheme. On
    ``ced24970`` the first row loaded both names and ``echo $((EVIL_API_KEY))``
    created ``PWNED``.
    """

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    for name in ("OPENROUTER_BASE_URL", "EVIL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    _load(
        tmp_path / ".env",
        f"OPENROUTER_BASE_URL={hatched_value}\nEVIL_API_KEY=OPENROUTER_BASE_URL\n",
    )
    env = get_shell_env()
    for name in ("OPENROUTER_BASE_URL", "EVIL_API_KEY"):
        os.environ.pop(name, None)
    assert env.get("EVIL_API_KEY") == "OPENROUTER_BASE_URL"
    work = tmp_path / "work"
    _trap_dir(work)
    _run_bash(bash, work, env, "echo $((EVIL_API_KEY)); ls >/dev/null 2>&1")
    assert not (work / "PWNED").exists()


# Codex 4 and the verify's state-change rows: values the first rule admitted.
SHELL_NAME_VALUES = [
    ("underscore", "_"),  # bash's last argument: ls files/* makes it a repo file name
    ("underscore-mid", "1+_"),
    ("underscore-in-key", "sk-ant-_-x9"),
    ("reply", "REPLY"),  # what read read
    ("reply-mid", "0+REPLY"),
    ("optarg", "OPTARG"),
    ("bash-rematch", "BASH_REMATCH"),
    ("ifs-post", "IFS++"),
    ("ifs-pre", "++IFS"),
    ("ifs-mid", "0+IFS++"),
    ("optind-post", "OPTIND++"),
    ("cdpath-dec", "--CDPATH"),
    ("cdpath-tilde", "~CDPATH++"),
    ("path", "PATH"),
    ("random", "RANDOM"),
    ("seconds", "SECONDS"),
    ("lineno", "LINENO"),
    ("ps4", "PS4"),
    ("ksh-sh-match", "0+.sh.match"),
    # ksh93's ``.sh.`` namespace at the very start of a value. Until fix round 2
    # the leading ``.`` refused these as well; now only the ``.sh.`` rule does.
    ("ksh-sh-version", ".sh.version"),
    ("ksh-sh-match-lead", ".sh.match"),
    # Fix round 2. ``PWD`` pins the name set against dropping a name the shell
    # sets itself (``cd``); it loaded on ``ced24970``, not on ``141b6430``.
    ("pwd", "PWD"),
    # Verify-r2 finding 2: mksh fills ``KSH_MATCH`` from ``[[ x = pattern ]]`` and
    # ``case``. These and the next nine loaded on ``141b6430``.
    ("ksh-match", "KSH_MATCH"),
    ("ksh-match-mid", "0+KSH_MATCH"),
    ("execshell", "EXECSHELL"),
    ("textdomain", "TEXTDOMAIN"),
    ("textdomaindir", "TEXTDOMAINDIR"),
    ("zsh-debug-cmd", "ZSH_DEBUG_CMD"),
    ("history-ignore", "HISTORY_IGNORE"),
    ("reporttime", "REPORTTIME"),
    ("reportmemory", "REPORTMEMORY"),
    ("correct-ignore", "CORRECT_IGNORE"),
    ("correct-ignore-file", "CORRECT_IGNORE_FILE"),
]


@pytest.mark.parametrize(
    "value", [v for _, v in SHELL_NAME_VALUES], ids=[i for i, _ in SHELL_NAME_VALUES]
)
def test_a_value_naming_a_shell_variable_is_refused(tmp_path, monkeypatch, capsys, value) -> None:
    """Rule 2. Every row loaded on ``ced24970``; the round-2 rows also on ``141b6430``."""

    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", f"EVIL_API_KEY={value}\n")
    leaked = os.environ.pop("EVIL_API_KEY", None)
    err = capsys.readouterr().err
    assert leaked is None
    assert "Notice: ignored EVIL_API_KEY" in err


# A leading ``-`` or ``.`` loads (fix round 2, the lead's call): the round-2
# verify ran 37 commands the classifier allows as ``cmd $X`` with such values and
# nothing ran. Fix round 1 refused these; ``ced24970`` loaded them.
LEADING_VALUES = [
    ("option-n", "-n"),
    ("option-delete", "-delete"),
    ("option-eq", "-eq"),
    ("dot-slash", "./marker"),
    ("dot-dot", "../x"),
    ("dot-key", ".abc8Q~dE_f"),
]


@pytest.mark.parametrize(
    "value", [v for _, v in LEADING_VALUES], ids=[i for i, _ in LEADING_VALUES]
)
def test_a_value_led_by_dash_or_dot_loads(tmp_path, monkeypatch, value) -> None:
    monkeypatch.delenv("SOME_SERVICE_API_KEY", raising=False)
    _load(tmp_path / ".env", f"SOME_SERVICE_API_KEY={value}\n")
    assert os.environ.pop("SOME_SERVICE_API_KEY", None) == value


# Names that only LOOK like a shell variable: rule 2 compares whole names (runs of
# letters, digits and ``_``), and ``.sh.`` only where a name may start. Green on
# ``ced24970``; a substring or case-blind match turns them red.
SHELL_NAME_LOOKALIKES = [
    "sk-proj-PATHfinder_IFSx9",
    "sk-ifs-path-reply-9",
    "FOO_TOKEN+1",
    "x.sh.y",
    "a_b-c",
]


@pytest.mark.parametrize("value", SHELL_NAME_LOOKALIKES)
def test_a_value_that_only_contains_a_shell_name_still_loads(tmp_path, monkeypatch, value) -> None:
    monkeypatch.delenv("SOME_SERVICE_API_KEY", raising=False)
    _load(tmp_path / ".env", f"SOME_SERVICE_API_KEY={value}\n")
    assert os.environ.pop("SOME_SERVICE_API_KEY", None) == value


# Verify finding 6 / Codex 6: "up to two = at the end", and an empty value.
PADDING_ROWS = [
    ("", True),  # empty: admitted on base and on ced24970
    ("abc=", True),
    ("abc==", True),
    ("abc===", False),
    ("=", False),
    ("==", False),
    ("ab=c", False),
]


@pytest.mark.parametrize("value,admitted", PADDING_ROWS, ids=[repr(v) for v, _ in PADDING_ROWS])
def test_padding_is_up_to_two_trailing_equals_and_an_empty_value_loads(
    tmp_path, monkeypatch, value, admitted
) -> None:
    """Pins the boundary the first round's rows left open.

    ``={0,3}``, ``=*``, ``+`` to ``*`` inside the group, and a required group all
    survived the first round's file; each turns one of these rows red.
    """

    monkeypatch.delenv("X_TOKEN", raising=False)
    _load(tmp_path / ".env", f"X_TOKEN={value}\n")
    got = os.environ.pop("X_TOKEN", None)
    assert (got == value) if admitted else (got is None)


def test_exploit_an_underscore_value_never_reaches_a_repo_file_name_in_bash(
    tmp_path, monkeypatch
) -> None:
    """Verify finding 4 at the consumer: ``UNDERSCORE_TOKEN=_`` and a planted file name.

    ``ls files/*`` leaves the last file name in ``$_``, and arithmetic follows the
    value ``_`` into it. On ``ced24970`` bash 3.2 and 5.2 created ``PWNED``, and a
    real model under auto ran this very command with no dialog.
    """

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    work = tmp_path / "work"
    (work / "files").mkdir(parents=True)
    (work / "files" / "z[$(touch PWNED)]").write_text("")
    monkeypatch.delenv("UNDERSCORE_TOKEN", raising=False)
    _load(tmp_path / ".env", "UNDERSCORE_TOKEN=_\n")
    env = get_shell_env()
    os.environ.pop("UNDERSCORE_TOKEN", None)
    _run_bash(bash, work, env, "ls files/* >/dev/null; echo $((UNDERSCORE_TOKEN))")
    assert not (work / "PWNED").exists()


def test_exploit_a_reply_value_never_reaches_what_read_read_in_bash(tmp_path, monkeypatch) -> None:
    """``0+REPLY`` after ``read REPLY < files/r``: bash and ksh93u+ created ``PWNED`` on ``ced24970``."""

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    work = tmp_path / "work"
    (work / "files").mkdir(parents=True)
    (work / "files" / "r").write_text("a[$(touch PWNED)]\n")
    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", "EVIL_API_KEY=0+REPLY\n")
    env = get_shell_env()
    os.environ.pop("EVIL_API_KEY", None)
    _run_bash(bash, work, env, "read REPLY < files/r; echo $((EVIL_API_KEY))")
    assert not (work / "PWNED").exists()


def test_a_key_you_exported_gets_no_notice_for_its_dotenv_duplicate(
    tmp_path, monkeypatch, capsys
) -> None:
    """Verify nit 7: the line would not set the key, so "export it" is noise.

    On ``ced24970`` this printed "ignored OPENAI_API_KEY … Export it in your own
    shell" to a user who had; the base loader said nothing.
    """

    monkeypatch.setenv("OPENAI_API_KEY", "users-own-exported-key")
    _load(tmp_path / ".env", "OPENAI_API_KEY=sk-abc # work key\n")
    err = capsys.readouterr().err
    assert os.environ["OPENAI_API_KEY"] == "users-own-exported-key"
    assert [ln for ln in err.splitlines() if "OPENAI_API_KEY" in ln] == []


# The configuration arm's own shapes were drawn against a moved host or path, and a
# Cloudflare id may still be ``_`` or a shell variable's name. Found in fix round 1
# after the verify: every row loaded on ``ced24970``.
CONFIG_VALUES_SHELL = [
    ("CLOUDFLARE_GATEWAY_ID", "_"),
    ("CLOUDFLARE_ACCOUNT_ID", "REPLY"),
    ("CLOUDFLARE_GATEWAY_ID", "IFS"),
    ("CLOUDFLARE_ACCOUNT_ID", "KSH_MATCH"),
]


@pytest.mark.parametrize(
    "key,value", CONFIG_VALUES_SHELL, ids=[f"{k}={v}" for k, v in CONFIG_VALUES_SHELL]
)
def test_a_config_value_naming_a_shell_variable_is_refused(
    tmp_path, monkeypatch, capsys, key, value
) -> None:
    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}={value}\n")
    leaked = os.environ.pop(key, None)
    err = capsys.readouterr().err
    assert leaked is None
    lines = [ln for ln in err.splitlines() if key in ln]
    assert len(lines) == 1 and "shell's own variables" in lines[0], lines
    # Its own reason, not the credential one: a Cloudflare id is no key.
    assert "credential-shaped" not in lines[0], lines


def test_exploit_an_underscore_cloudflare_id_never_reaches_a_repo_file_name_in_bash(
    tmp_path, monkeypatch
) -> None:
    """``CLOUDFLARE_GATEWAY_ID=_``: on ``ced24970`` bash 3.2 created ``PWNED`` through
    ``ls files/* >/dev/null; echo $((CLOUDFLARE_GATEWAY_ID))``, which the classifier allows."""

    bash = _bash_or_skip()
    from aelix_coding_agent.tools.bash import get_shell_env

    work = tmp_path / "work"
    (work / "files").mkdir(parents=True)
    (work / "files" / "z[$(touch PWNED)]").write_text("")
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID", raising=False)
    _load(tmp_path / ".env", "CLOUDFLARE_GATEWAY_ID=_\n")
    env = get_shell_env()
    os.environ.pop("CLOUDFLARE_GATEWAY_ID", None)
    _run_bash(bash, work, env, "ls files/* >/dev/null; echo $((CLOUDFLARE_GATEWAY_ID))")
    assert not (work / "PWNED").exists()


# === #756 fix round 2 ========================================================


@pytest.mark.parametrize(
    "key,value",
    [("CLOUDFLARE_ACCOUNT_ID", "-delete"), ("CLOUDFLARE_GATEWAY_ID", "-my-gateway")],
    ids=["account-option", "gateway-dash"],
)
def test_a_cloudflare_id_led_by_dash_loads(tmp_path, monkeypatch, key, value) -> None:
    """The configuration arm drops its leading-``-`` refusal with rule 1's (the lead's call).

    Both rows were refused on ``141b6430`` and loaded on ``ced24970``.
    """

    monkeypatch.delenv(key, raising=False)
    _load(tmp_path / ".env", f"{key}={value}\n")
    assert os.environ.pop(key, None) == value


def test_a_hatched_off_credential_name_gets_the_name_notice_whatever_its_value(
    tmp_path, monkeypatch, capsys
) -> None:
    """Verify-r2 nit 4: the name refusal comes first, the value check once the hatch opens.

    On ``141b6430`` a hatched-off ``NPM_TOKEN`` with a bad value got the value
    notice ("Export it"), and the same line with a key-shaped value got the name
    notice instead, so fixing one reason surfaced the other. Now the closed hatch
    gives the name notice for both values, as it does for every name only the
    hatch loads, and the value notice appears once the hatch names the key.
    """

    monkeypatch.delenv("NPM_TOKEN", raising=False)
    monkeypatch.delenv("AELIX_DOTENV_ALLOW", raising=False)

    def notices(value: str) -> list[str]:
        _load(tmp_path / ".env", f"NPM_TOKEN={value}\n")
        assert os.environ.pop("NPM_TOKEN", None) is None
        return [ln for ln in capsys.readouterr().err.splitlines() if "NPM_TOKEN" in ln]

    bad = notices("a[$(touch PWNED)]")
    good = notices("npm_" + "aB3xQ9" * 6)
    assert len(bad) == 1 and bad == good, (bad, good)
    assert "AELIX_DOTENV_ALLOW" in bad[0] and "credential-shaped" not in bad[0]
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "NPM_TOKEN")
    opened = notices("a[$(touch PWNED)]")
    assert len(opened) == 1 and "not credential-shaped" in opened[0], opened


# Verify-r2 finding 2, measured in mksh R59: ``[[ … = pattern ]]`` and ``case`` leave
# what matched in ``KSH_MATCH``, and ``$((EVIL_API_KEY))`` with the value
# ``KSH_MATCH`` evaluated it, running the ``$(…)`` in a repo file's text.
_KSH_MATCH_FORMS = [
    "[[ $(head -1 files/r) = *x* ]] && echo $((EVIL_API_KEY))",
    "case $(cat files/r) in *x*) echo $((EVIL_API_KEY));; esac",
    "[[ $(cat files/r) == *x* ]] && echo $((EVIL_API_KEY))",
]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mksh")
@pytest.mark.parametrize("form", _KSH_MATCH_FORMS)
def test_exploit_a_ksh_match_value_never_reaches_what_a_pattern_matched_in_mksh(
    tmp_path, monkeypatch, form
) -> None:
    """On ``141b6430`` ``EVIL_API_KEY=KSH_MATCH`` loaded and each form created ``PWNED`` in
    mksh R59 (Linux docker). Skipped where no ``mksh`` is installed."""

    mksh = shutil.which("mksh")
    if mksh is None:
        pytest.skip("no mksh")
    from aelix_coding_agent.tools.bash import get_shell_env

    work = tmp_path / "work"
    (work / "files").mkdir(parents=True)
    (work / "files" / "r").write_text("a[$(touch PWNED)]x\n")
    monkeypatch.delenv("EVIL_API_KEY", raising=False)
    _load(tmp_path / ".env", "EVIL_API_KEY=KSH_MATCH\n")
    env = get_shell_env()
    os.environ.pop("EVIL_API_KEY", None)
    _run_bash(mksh, work, env, form)
    assert not (work / "PWNED").exists()


# Verify-r3 finding 1: the forms in which a hatched URL's ``]`` closed the subscript
# and ``PATH=0`` ran, and the shells that ran the repo's ``./0/ls`` after it.
_BRACKET_CLOSE_FORMS = [
    ("zsh", "echo $(( a[$OPENROUTER_BASE_URL] ))"),  # zsh 5.9 and macOS zsh
    ("ksh", 'typeset -A m; let "y=m[$OPENROUTER_BASE_URL]"'),  # ksh93u+ and ksh93u+m
    ("bash", 'typeset -A m; let "y=m[$OPENROUTER_BASE_URL]"'),  # bash 5.2, not 3.2 or 5.3
]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells and an executable ./0/ls")
@pytest.mark.parametrize(
    "shell,form", _BRACKET_CLOSE_FORMS, ids=[s for s, _ in _BRACKET_CLOSE_FORMS]
)
def test_exploit_a_hatched_url_cannot_close_the_subscript_it_is_pasted_into(
    tmp_path, monkeypatch, shell, form
) -> None:
    """On ``b95ed1b8`` ``https://x.example/]+(PATH=0)+m[x`` loaded as a hatched URL, and the
    ``ls`` after each form ran ``./0/ls``: in macOS zsh and ksh93u+ and, in Linux docker,
    zsh 5.9, ksh93u+m and bash 5.2. Skipped where the shell is not installed."""

    binary = shutil.which(shell)
    if binary is None:
        pytest.skip(f"no {shell}")
    from aelix_coding_agent.tools.bash import get_shell_env

    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("AELIX_DOTENV_ALLOW", "OPENROUTER_BASE_URL")
    _load(tmp_path / ".env", "OPENROUTER_BASE_URL=https://x.example/]+(PATH=0)+m[x\n")
    env = get_shell_env()
    os.environ.pop("OPENROUTER_BASE_URL", None)
    # zsh -c reads $ZDOTDIR/.zshenv, else $HOME/.zshenv: keep the runner's out.
    env["HOME"] = env["ZDOTDIR"] = str(tmp_path)
    work = tmp_path / "work"
    _trap_dir(work)
    _run_bash(binary, work, env, form + "; ls >/dev/null 2>&1")
    assert not (work / "PWNED").exists()
