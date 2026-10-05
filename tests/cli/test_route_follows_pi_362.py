"""#362 / ADR-0250 — ``--model`` follows pi's ``resolveCliModel``, and a ``.env`` key cannot choose.

The owner's decision (2026-10-02) replaced ADR-0249's rung 0 and the
OpenRouter-from-env rung with pi's order (``packages/coding-agent/src/core/
model-resolver.ts:406-606`` @ 88ff80b98), exact ids only, plus two guards:

* GUARD 1 — a credential a cwd ``.env`` supplied (``load_dotenv``'s record,
  ``AELIX_DOTENV_ADMITTED``) never counts in a judgement that CHOOSES a route:
  the bare-id tie-break, the swap, the raw fallback, guard 2. It still
  authenticates a route once chosen.
* GUARD 2 — a ``<vendor>/<model>`` this build's catalogue cannot place goes to
  OpenRouter as written, only on an OpenRouter credential of the user's own,
  never under a user-defined prefix or a catalogued non-namespace one.

Every row builds a REAL :class:`ModelRegistry` over a ``tmp_path`` agent dir,
exports fake keys with ``monkeypatch``, and loads ``.env`` rows through the REAL
``load_dotenv`` over a ``tmp_path`` file. Nothing opens a socket. The row ids
are the design's matrix (``/tmp/362-work/design.md`` §C.1) plus the critique's
(``/tmp/362-work/critique.md``: L1-L3, P1-P2, MISSING, S4).

The route rows assert through ``resolve_model`` (present on ``aa026d08``), so
on the base they fail on the ROUTE, not on an import; the message rows need
``resolve_route`` and import it inside the test.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.oauth import AuthStorage
from aelix_ai.streaming import Model
from aelix_coding_agent.cli.runtime_bootstrap import load_dotenv, resolve_model
from aelix_coding_agent.model_registry import ModelRegistry, ProviderConfigInput

_OR = "https://openrouter.ai/api/v1"
_ANT = "https://api.anthropic.com"
_OAI = "https://api.openai.com/v1"
_XAI = "https://api.x.ai/v1"
_CODEX = "https://chatgpt.com/backend-api"
_COPILOT = "https://api.individual.githubcopilot.com"
_RP = "http://127.0.0.1:9/rp/v1"
_GW = "http://127.0.0.1:9/gw/v1"
_EXT = "http://127.0.0.1:9/ext/v1"
_OLLAMA = "http://127.0.0.1:11434/v1"

_RETRYPROBE = {
    "retryprobe": {
        "api": "openai-completions",
        "baseUrl": _RP,
        "apiKey": "rp-fake-literal",
        "models": [{"id": "held-model"}],
    }
}
_MODELS_JSON: dict[str, dict[str, Any]] = {
    "base": {"providers": _RETRYPROBE},
    "OpenAI": {
        "providers": {
            **_RETRYPROBE,
            "OpenAI": {
                "api": "openai-completions",
                "baseUrl": _GW,
                "apiKey": "gw-fake-literal",
                "models": [{"id": "m1"}],
            },
        }
    },
    "repoint": {
        "providers": {**_RETRYPROBE, "openai": {"baseUrl": _GW, "apiKey": "gw-fake-literal"}}
    },
    "gwverbatim": {
        "providers": {
            **_RETRYPROBE,
            "mygw": {
                "api": "openai-completions",
                "baseUrl": _GW,
                "apiKey": "gw-fake-literal",
                "models": [{"id": "openai/gpt-4o"}],
            },
        }
    },
    "gwbare": {
        "providers": {
            **_RETRYPROBE,
            "mygw": {
                "api": "openai-completions",
                "baseUrl": _GW,
                "apiKey": "gw-fake-literal",
                "models": [{"id": "gpt-4o-mini"}],
            },
        }
    },
    "ollama": {
        "providers": {
            **_RETRYPROBE,
            "ollama": {
                "api": "openai-completions",
                "baseUrl": _OLLAMA,
                "apiKey": "ollama",
                "models": [{"id": "qwen3.6:35b-a3b"}],
            },
        }
    },
}


def _jwt(payload: dict[str, Any]) -> str:
    def enc(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


_CODEX_LOGIN = {
    "openai-codex": {
        "type": "oauth",
        "refresh": "r",
        "access": _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a"}}),
        "expires": int(time.time() * 1000) + 10**12,
    }
}


class World:
    """One row's machine: exported keys, a project ``.env``, an agent dir."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.agent = tmp_path / "agent"
        self.agent.mkdir()
        monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(self.agent))

    def shell(self, **values: str) -> None:
        for name, value in values.items():
            self.mp.setenv(name, value)

    def dotenv(self, text: str, capsys: pytest.CaptureFixture[str] | None = None) -> str:
        """Load ``text`` as a cwd ``.env`` through the REAL loader; return its notices."""

        path = self.tmp / "project" / ".env"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        # Register an undo for every name the file (or the loader) may write:
        # ``setenv`` then ``delenv`` records the outer state even for an absent
        # name, so the loader's direct ``os.environ`` writes are rolled back.
        names = [line.partition("=")[0].strip() for line in text.splitlines()]
        for name in [*names, "AELIX_DOTENV_ADMITTED"]:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name not in os.environ:
                self.mp.setenv(name, "")
                self.mp.delenv(name)
        load_dotenv(str(path))
        return capsys.readouterr().err if capsys is not None else ""

    async def registry(
        self,
        variant: str = "base",
        auth: dict[str, Any] | None = None,
        ext: bool = False,
    ) -> ModelRegistry:
        (self.agent / "models.json").write_text(json.dumps(_MODELS_JSON[variant]), encoding="utf-8")
        (self.agent / "auth.json").write_text(json.dumps(auth or {}), encoding="utf-8")
        storage = AuthStorage(self.agent / "auth.json")
        await storage.load()
        registry = ModelRegistry.create(storage, str(self.agent / "models.json"))
        if ext:
            registry.register_provider(
                "extprov",
                ProviderConfigInput(
                    name="ext probe",
                    api_key="ext-fake-literal",
                    models={
                        "m1": Model(
                            id="m1", provider="extprov", api="openai-completions", base_url=_EXT
                        )
                    },
                ),
            )
        assert registry.get_error() is None
        return registry


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """No credential, no OpenRouter setting, no record, no agent dir from the real shell."""

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    return World(tmp_path, monkeypatch)


OR = {"OPENROUTER_API_KEY": "or-shell-fake"}
ANT = {"ANTHROPIC_API_KEY": "ant-shell-fake"}
OAI = {"OPENAI_API_KEY": "oai-shell-fake"}
XAI = {"XAI_API_KEY": "xai-shell-fake"}
HF = {"HF_TOKEN": "hf-shell-fake"}
DOR = "OPENROUTER_API_KEY=or-dotenv-planted\n"
DANT = "ANTHROPIC_API_KEY=ant-dotenv-planted\n"
DOAI = "OPENAI_API_KEY=oai-dotenv-planted\n"

# id, shell, .env, models.json, auth.json, extension, --model, --provider,
# settings defaultProvider, expected (provider, id, base_url) — or None when the
# route is an ERROR (the placeholder no turn runs; text pinned below).
ROUTES: list[tuple[Any, ...]] = [
    (
        "A01",
        OR,
        "",
        "base",
        None,
        False,
        "retryprobe/held-model",
        None,
        None,
        ("retryprobe", "held-model", _RP),
    ),
    (
        "A02",
        OR,
        "",
        "base",
        None,
        False,
        "held-model",
        None,
        None,
        ("retryprobe", "held-model", _RP),
    ),
    (
        "A02b",
        OR,
        "",
        "ollama",
        None,
        False,
        "qwen3.6:35b-a3b",
        None,
        None,
        ("ollama", "qwen3.6:35b-a3b", _OLLAMA),
    ),
    (
        "A03",
        OR,
        "",
        "base",
        _CODEX_LOGIN,
        False,
        "openai-codex/gpt-5.5",
        None,
        None,
        ("openai-codex", "gpt-5.5", _CODEX),
    ),
    (
        "A04",
        OR,
        "",
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openrouter", "openai/gpt-4o-mini", _OR),
    ),
    (
        "A05-dual-key",
        {**OR, **OAI},
        "",
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _OAI),
    ),
    (
        "A06-M1",
        OR,
        DOAI,
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openrouter", "openai/gpt-4o-mini", _OR),
    ),
    (
        "A07",
        OAI,
        DOR,
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _OAI),
    ),
    (
        "A08-guard2",
        OR,
        "",
        "base",
        None,
        False,
        "anthropic/claude-new-9",
        None,
        None,
        ("openrouter", "anthropic/claude-new-9", _OR),
    ),
    (
        "A09",
        {**OR, **ANT},
        "",
        "base",
        None,
        False,
        "anthropic/claude-new-9",
        None,
        None,
        ("anthropic", "claude-new-9", _ANT),
    ),
    (
        "A10-planted",
        ANT,
        DOR,
        "base",
        None,
        False,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        ("anthropic", "claude-haiku-4.5", _ANT),
    ),
    (
        "A11-issue-362",
        ANT,
        DOR,
        "base",
        None,
        False,
        "anthropic/claude-haiku-4-5",
        None,
        None,
        ("anthropic", "claude-haiku-4-5", _ANT),
    ),
    ("A12", OR, "", "base", None, False, "xai/grok-4", None, None, ("xai", "grok-4", _XAI)),
    (
        "A13",
        {**OR, **XAI},
        "",
        "base",
        None,
        False,
        "xai/grok-4",
        None,
        None,
        ("xai", "grok-4", _XAI),
    ),
    (
        "A14",
        OR,
        "",
        "base",
        None,
        False,
        "x-ai/grok-4.3",
        None,
        None,
        ("openrouter", "x-ai/grok-4.3", _OR),
    ),
    ("A15", OR, "", "base", None, False, "gpt-4o-mini", None, None, None),
    (
        "A16",
        ANT,
        "",
        "base",
        None,
        False,
        "claude-haiku-4-5",
        None,
        None,
        ("anthropic", "claude-haiku-4-5", _ANT),
    ),
    (
        "A17",
        {**ANT, **OR},
        "",
        "base",
        None,
        False,
        "claude-haiku-4-5",
        None,
        None,
        ("anthropic", "claude-haiku-4-5", _ANT),
    ),
    (
        "A18-guard2",
        OR,
        "",
        "base",
        None,
        False,
        "newlab/model-x",
        None,
        None,
        ("openrouter", "newlab/model-x", _OR),
    ),
    ("A19-dotenv-or", {}, DOR, "base", None, False, "newlab/model-x", None, None, None),
    (
        "A20-strip",
        OR,
        "",
        "base",
        None,
        False,
        "openrouter/newlab/model-x",
        None,
        None,
        ("openrouter", "newlab/model-x", _OR),
    ),
    (
        "A21-auto",
        OR,
        "",
        "base",
        None,
        False,
        "openrouter/auto",
        None,
        None,
        ("openrouter", "auto", _OR),
    ),
    (
        "A22",
        OR,
        "",
        "base",
        None,
        False,
        "newlab/model-x",
        "openrouter",
        None,
        ("openrouter", "newlab/model-x", _OR),
    ),
    ("A25", OR, "", "base", None, True, "extprov/m1", None, None, ("extprov", "m1", _EXT)),
    (
        "A26-launch",
        OR,
        "",
        "base",
        None,
        False,
        "sessext/m1",
        None,
        None,
        ("openrouter", "sessext/m1", _OR),
    ),
    ("A27-launch", {}, DOR, "base", None, False, "sessext/m1", None, None, None),
    ("A28-launch", {}, "", "base", None, False, "sessext/m1", None, None, None),
    ("A29", OR, "", "OpenAI", None, False, "openai/m1", None, None, ("OpenAI", "m1", _GW)),
    (
        "A30",
        OR,
        "",
        "repoint",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _GW),
    ),
    (
        "A31",
        OR,
        "",
        "base",
        None,
        False,
        "RetryProbe/held-model",
        None,
        None,
        ("retryprobe", "held-model", _RP),
    ),
    (
        "A32",
        OR,
        "",
        "base",
        None,
        False,
        "x-ai/grok-4.3",
        "OPENROUTER",
        None,
        ("openrouter", "x-ai/grok-4.3", _OR),
    ),
    ("A33", {}, DOAI, "base", None, False, "gpt-4o-mini", None, None, None),
    (
        "A34",
        {},
        DOR,
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _OAI),
    ),
    (
        "A35",
        {**OR, **HF},
        "",
        "base",
        None,
        False,
        "meta-llama/llama-3.3-70b-instruct",
        None,
        None,
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", _OR),
    ),
    (
        "A36",
        {},
        DOR,
        "base",
        None,
        False,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        ("openrouter", "anthropic/claude-haiku-4.5", _OR),
    ),
    (
        "A37",
        OR,
        "",
        "base",
        None,
        False,
        "moonshotai/kimi-k2.6",
        None,
        None,
        ("openrouter", "moonshotai/kimi-k2.6", _OR),
    ),
    (
        "A38",
        {**ANT, **OR},
        "",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        ("openrouter", "anthropic/claude-haiku-4.5", _OR),
    ),
    (
        "A39",
        ANT,
        "",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4-5",
        None,
        None,
        ("anthropic", "claude-haiku-4-5", _ANT),
    ),
    (
        "A41",
        OR,
        "",
        "gwverbatim",
        None,
        False,
        "openai/gpt-4o",
        None,
        None,
        ("mygw", "openai/gpt-4o", _GW),
    ),
    # SUBJECT CHANGED (verify round 4, B1): settings ``defaultProvider`` names
    # ``openai``, which no credential of the user's own authenticates, while
    # their OpenRouter key is exported — it no longer breaks the tie (a project
    # file can set it), so the bare id is pi's ambiguity refusal. See
    # test_settings_default_provider_counts_only_for_your_own_credential_or_endpoint.
    (
        "A42",
        OR,
        "",
        "base",
        None,
        False,
        "gpt-4o-mini",
        None,
        "openai",
        None,
    ),
    (
        "A43",
        OR,
        "",
        "base",
        None,
        False,
        "claude-haiku-4.5",
        None,
        "openai",
        ("github-copilot", "claude-haiku-4.5", _COPILOT),
    ),
    (
        "A44",
        OR,
        "",
        "base",
        None,
        False,
        "openai-codex/gpt-9-new",
        None,
        None,
        ("openai-codex", "gpt-9-new", _CODEX),
    ),
    (
        "A45-strip",
        ANT,
        "",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4-5",
        "anthropic",
        None,
        ("anthropic", "claude-haiku-4-5", _ANT),
    ),
    (
        "A46",
        {},
        "",
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _OAI),
    ),
    (
        "A47",
        {},
        "",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        ("openrouter", "anthropic/claude-haiku-4.5", _OR),
    ),
    (
        "A48",
        {},
        DOR,
        "base",
        None,
        False,
        "openrouter/free",
        None,
        None,
        ("openrouter", "openrouter/free", _OR),
    ),
    (
        "A49",
        OR,
        "",
        "base",
        None,
        False,
        "qwen/qwen3-32b",
        None,
        None,
        ("openrouter", "qwen/qwen3-32b", _OR),
    ),
    ("A50", {}, "", "base", None, False, "newlab/model-x", None, None, None),
    (
        "A51",
        {**OR, **OAI},
        "",
        "gwverbatim",
        None,
        False,
        "openai/gpt-4o",
        None,
        None,
        ("openai", "gpt-4o", _OAI),
    ),
    (
        "A52",
        {},
        "",
        "gwverbatim",
        None,
        False,
        "openai/gpt-4o",
        None,
        None,
        ("mygw", "openai/gpt-4o", _GW),
    ),
    ("A53", {}, DOR, "base", None, True, "extprov/m1", None, None, ("extprov", "m1", _EXT)),
    (
        "A54",
        OR,
        "",
        "base",
        None,
        True,
        "extprov/unlisted",
        None,
        None,
        ("extprov", "unlisted", _EXT),
    ),
    (
        "A55",
        OR,
        "",
        "base",
        None,
        False,
        "retryprobe/unlisted",
        None,
        None,
        ("retryprobe", "unlisted", _RP),
    ),
    # --- the critique's rows -------------------------------------------------------
    # S1/S2: a namespace id OpenRouter's snapshot does not list verbatim, OR key
    # exported, the vendor key from a .env (or absent) -> OpenRouter, as written.
    (
        "L1-S1",
        OR,
        DANT,
        "base",
        None,
        False,
        "anthropic/claude-haiku-4-5",
        None,
        None,
        ("openrouter", "anthropic/claude-haiku-4-5", _OR),
    ),
    (
        "L1c-S2",
        OR,
        "",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4-5",
        None,
        None,
        ("openrouter", "anthropic/claude-haiku-4-5", _OR),
    ),
    # L2: ``openai/o1-pro`` IS in a catalogue (the vendor's) and not in
    # OpenRouter's snapshot; guard 2 still takes it. Codex's cross-review of
    # 854bf319 (C4, probe_guard2.py "catalog_match True route guard2 openrouter")
    # read the owner's "an id in no catalogue" literally; the scope kept is the
    # refinement stated in ADR-0250 §2.3 — see
    # test_guard2_scope_is_the_openrouter_snapshot_not_every_catalogue.
    (
        "L2-S1",
        OR,
        DOAI,
        "base",
        None,
        False,
        "openai/o1-pro",
        None,
        None,
        ("openrouter", "openai/o1-pro", _OR),
    ),
    (
        "L2c-S2",
        OR,
        "",
        "base",
        None,
        False,
        "openai/o1-pro",
        None,
        None,
        ("openrouter", "openai/o1-pro", _OR),
    ),
    # M4: a case-variant prefix still finds OpenRouter's lower-case id for the swap.
    (
        "L3-M4",
        OR,
        DOAI,
        "base",
        None,
        False,
        "OpenAI/gpt-4o-mini",
        None,
        None,
        ("openrouter", "openai/gpt-4o-mini", _OR),
    ),
    # S3: guard 2's inferred-provider clause with a .env vendor key.
    (
        "MISSING-S3",
        OR,
        DANT,
        "base",
        None,
        False,
        "anthropic/claude-new-9",
        None,
        None,
        ("openrouter", "anthropic/claude-new-9", _OR),
    ),
    # M2(a): a comma in a .env key name cannot poison the record.
    (
        "P1-M2",
        ANT,
        DOR + "X,ANTHROPIC_API_KEY=junk\n",
        "base",
        None,
        False,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        ("anthropic", "claude-haiku-4.5", _ANT),
    ),
    (
        "P2-M2",
        {**OR, **OAI},
        "Z,OPENAI_API_KEY=junk\n",
        "base",
        None,
        False,
        "openai/gpt-4o-mini",
        None,
        None,
        ("openai", "gpt-4o-mini", _OAI),
    ),
    # S4: pi's ``<provider>/`` strip reaches the custom-id tail too.
    (
        "S4a",
        ANT,
        "",
        "base",
        None,
        False,
        "anthropic/claude-new-9",
        "anthropic",
        None,
        ("anthropic", "claude-new-9", _ANT),
    ),
    (
        "S4b",
        OR,
        "",
        "base",
        None,
        False,
        "openrouter/newlab/model-x",
        "openrouter",
        None,
        ("openrouter", "newlab/model-x", _OR),
    ),
]


@pytest.mark.parametrize(
    (
        "row",
        "shell",
        "dotenv",
        "variant",
        "auth",
        "ext",
        "model",
        "provider",
        "default",
        "expected",
    ),
    ROUTES,
    ids=[r[0] for r in ROUTES],
)
async def test_the_route_is_pi_order_with_both_guards(
    world: World,
    row: str,
    shell: dict[str, str],
    dotenv: str,
    variant: str,
    auth: dict[str, Any] | None,
    ext: bool,
    model: str,
    provider: str | None,
    default: str | None,
    expected: tuple[str, str, str] | None,
) -> None:
    world.shell(**shell)
    if dotenv:
        world.dotenv(dotenv)
    registry = await world.registry(variant, auth, ext)
    got = resolve_model(model, provider, registry, default)
    if expected is None:
        # A refusal: nothing a turn can run, on no vendor's host.
        assert (got.api, got.base_url) == ("unknown", ""), (row, got)
    else:
        assert (got.provider, got.id, got.base_url) == expected, row


# === the texts (pi's, plus why a .env key did not count) =======================

_AMBIGUOUS_GPT4O_MINI = (
    'Model "gpt-4o-mini" is ambiguous across providers: azure-openai-responses/gpt-4o-mini, '
    "cloudflare-ai-gateway/gpt-4o-mini, openai/gpt-4o-mini. No matching provider is "
    "authenticated."
)

MESSAGES: list[tuple[Any, ...]] = [
    # id, shell, .env, --model, --provider, error (exact), warning (exact) — None = absent
    (
        "A15",
        OR,
        "",
        "gpt-4o-mini",
        None,
        f"{_AMBIGUOUS_GPT4O_MINI} Use --provider or provider/model.",
        None,
    ),
    (
        "A33",
        {},
        DOAI,
        "gpt-4o-mini",
        None,
        f"{_AMBIGUOUS_GPT4O_MINI} (OPENAI_API_KEY came from a project .env, which does not "
        "choose between providers.) Use --provider or provider/model.",
        None,
    ),
    (
        "A19",
        {},
        DOR,
        "newlab/model-x",
        None,
        'Model "newlab/model-x" not found. Use --list-models to see available models. '
        "(OPENROUTER_API_KEY came from a project .env, which does not send ids this build "
        "does not know to OpenRouter; use openrouter/newlab/model-x or --provider openrouter.)",
        None,
    ),
    (
        "A50",
        {},
        "",
        "newlab/model-x",
        None,
        'Model "newlab/model-x" not found. Use --list-models to see available models.',
        None,
    ),
    (
        "A28",
        {},
        "",
        "sessext/m1",
        None,
        'Model "sessext/m1" not found. Use --list-models to see available models.',
        None,
    ),
    (
        "A08",
        OR,
        "",
        "anthropic/claude-new-9",
        None,
        None,
        'Model "anthropic/claude-new-9" is not in this build\'s catalog; sending it to OpenRouter as written.',
    ),
    (
        "A18",
        OR,
        "",
        "newlab/model-x",
        None,
        None,
        'Model "newlab/model-x" is not in this build\'s catalog; sending it to OpenRouter as written.',
    ),
    (
        "A09",
        {**OR, **ANT},
        "",
        "anthropic/claude-new-9",
        None,
        None,
        'Model "claude-new-9" not found for provider "anthropic". Using custom model id.',
    ),
    (
        "A12",
        OR,
        "",
        "xai/grok-4",
        None,
        None,
        'Model "grok-4" not found for provider "xai". Using custom model id.',
    ),
    (
        "A20",
        OR,
        "",
        "openrouter/newlab/model-x",
        None,
        None,
        'Model "newlab/model-x" not found for provider "openrouter". Using custom model id.',
    ),
    (
        "S4a",
        ANT,
        "",
        "anthropic/claude-new-9",
        "anthropic",
        None,
        'Model "claude-new-9" not found for provider "anthropic". Using custom model id.',
    ),
    (
        "A10",
        ANT,
        DOR,
        "anthropic/claude-haiku-4.5",
        None,
        None,
        'Model "claude-haiku-4.5" not found for provider "anthropic". Using custom model id. '
        '"anthropic/claude-haiku-4.5" is also openrouter\'s model id, but OPENROUTER_API_KEY '
        "came from a project .env, which does not choose a route; use "
        "openrouter/anthropic/claude-haiku-4.5 or export the key to send it there.",
    ),
    (
        "A34",
        {},
        DOR,
        "openai/gpt-4o-mini",
        None,
        None,
        '"openai/gpt-4o-mini" is also openrouter\'s model id, but OPENROUTER_API_KEY came from '
        "a project .env, which does not choose a route; use openrouter/openai/gpt-4o-mini or "
        "export the key to send it there.",
    ),
    (
        "L1",
        OR,
        DANT,
        "anthropic/claude-haiku-4-5",
        None,
        None,
        '"anthropic/claude-haiku-4-5" goes to OpenRouter as written: you hold no anthropic '
        "credential of your own, and this build's OpenRouter catalog does not list the id. "
        "Use --provider anthropic to send it to anthropic. (ANTHROPIC_API_KEY came from a "
        "project .env, which does not choose a route.)",
    ),
    ("A04", OR, "", "openai/gpt-4o-mini", None, None, None),
    ("A11", ANT, DOR, "anthropic/claude-haiku-4-5", None, None, None),
]


@pytest.mark.parametrize(
    ("row", "shell", "dotenv", "model", "provider", "error", "warning"),
    MESSAGES,
    ids=[m[0] for m in MESSAGES],
)
async def test_the_route_says_why(
    world: World,
    row: str,
    shell: dict[str, str],
    dotenv: str,
    model: str,
    provider: str | None,
    error: str | None,
    warning: str | None,
) -> None:
    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**shell)
    if dotenv:
        world.dotenv(dotenv)
    route = resolve_route(model, provider, await world.registry())
    assert (route.error, route.warning) == (error, warning), row


# === OPENROUTER_DEFAULT_MODEL is the shell's, and an explicit route =============


async def test_openrouter_default_model_from_the_shell_is_an_explicit_openrouter_route(
    world: World,
) -> None:
    """A23: no ``--model``, the shell variable names the id, OpenRouter serves it."""

    world.shell(**OR, OPENROUTER_DEFAULT_MODEL="anthropic/claude-haiku-4.5")
    got = resolve_model(None, None, await world.registry())
    assert (got.provider, got.id, got.base_url) == ("openrouter", "anthropic/claude-haiku-4.5", _OR)


async def test_openrouter_default_model_from_a_dotenv_is_ignored(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    """A24: on ``9ca53a4f`` a project ``.env`` could set it and the run went to OpenRouter.

    It was the one value in the config arm that chose a route by itself
    (``loaded provider configuration from .env: OPENROUTER_DEFAULT_MODEL``,
    ``/tmp/362-work/design/live/today.out``). Now it is refused like any other
    non-credential name, and with no ``--model`` nothing is selected.
    """

    world.shell(**ANT)
    err = world.dotenv(DOR + "OPENROUTER_DEFAULT_MODEL=anthropic/claude-haiku-4.5\n", capsys)
    assert os.environ.get("OPENROUTER_DEFAULT_MODEL") is None
    assert "ignored OPENROUTER_DEFAULT_MODEL" in err
    got = resolve_model(None, None, await world.registry())
    assert (got.provider, got.id) == ("", "")


async def test_a_hatched_openrouter_default_model_is_honoured(world: World) -> None:
    """The user named it in ``AELIX_DOTENV_ALLOW`` themselves (ADR-0203's hatch).

    So "shell-only" (ADR-0250 §2.6) means: unless you hatch it yourself. Codex's
    cross-review of 854bf319 (C3, ``probe_default_hatch.py``) measured that the
    hatched ``.env`` value then picks the no-model route — kept: the opt-in is
    per name, set in the user's shell, and a ``.env`` cannot set
    ``AELIX_DOTENV_ALLOW`` for itself (``probe_controls.py`` found no way).
    """

    world.shell(**OR, AELIX_DOTENV_ALLOW="OPENROUTER_DEFAULT_MODEL")
    world.dotenv("OPENROUTER_DEFAULT_MODEL=x-ai/grok-4.3\n")
    got = resolve_model(None, None, await world.registry())
    assert (got.provider, got.id) == ("openrouter", "x-ai/grok-4.3")


async def test_openrouter_base_url_applies_to_an_explicit_openrouter_route(
    world: World,
) -> None:
    """``--provider openrouter`` with no OpenRouter key used to ignore it (the rung applied it)."""

    world.shell(OPENROUTER_BASE_URL="http://127.0.0.1:9/orproxy/v1")
    got = resolve_model("openai/gpt-4o-mini", "openrouter", await world.registry())
    assert (got.provider, got.id, got.base_url) == (
        "openrouter",
        "openai/gpt-4o-mini",
        "http://127.0.0.1:9/orproxy/v1",
    )


# === guard 2's clauses, one row each ============================================


async def test_guard2_scope_is_the_openrouter_snapshot_not_every_catalogue(
    world: World,
) -> None:
    """Guard 2's exact scope — a REFINEMENT of the owner's wording, kept (ADR-0250 §2.3).

    The owner worded guard 2 as "an id in no catalogue". What aelix applies is:
    an id the OpenRouter snapshot does not list, under an OpenRouter-namespace
    prefix whose vendor is not route-authenticated, goes to OpenRouter on the
    user's own OpenRouter key. ``openai/o1-pro`` is in the OpenAI catalogue and
    not in OpenRouter's snapshot; with only ``OPENROUTER_API_KEY`` exported it
    goes to OpenRouter as written. That is guard 2's purpose: pi reaches such
    ids through pi.dev's remote catalogue, which aelix does not have, and the
    request goes only to the provider whose key the user exported. Codex's
    cross-review of 854bf319 flagged it (C4); the main loop kept it and
    documented the scope instead.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**OR)
    registry = await world.registry()
    assert registry.find("openai", "o1-pro") is not None  # the vendor catalogue lists it
    assert registry.find("openrouter", "openai/o1-pro") is None  # OpenRouter's snapshot does not
    route = resolve_route("openai/o1-pro", None, registry)
    assert (route.kind, route.model.provider, route.model.id) == (
        "guard2",
        "openrouter",
        "openai/o1-pro",
    )
    assert route.error is None


async def test_guard2_never_takes_a_bare_id(world: World) -> None:
    """Clause 1 — OpenRouter ids are ``<vendor>/<model>``; a bare unknown id is pi's not-found."""

    world.shell(**OR)
    got = resolve_model("brand-new-model", None, await world.registry())
    assert (got.provider, got.api) == ("", "unknown")


async def test_guard2_never_takes_an_empty_segment(world: World) -> None:
    world.shell(**OR)
    got = resolve_model("newlab//model-x", None, await world.registry())
    assert got.provider != "openrouter"


async def test_guard2_never_takes_a_user_defined_prefix(world: World) -> None:
    """Clause 2 — the #344 principle: an unlisted id under the user's provider stays there."""

    world.shell(**OR)
    registry = await world.registry(ext=True)
    registry.register_provider("emptyext", ProviderConfigInput(api_key="x"))
    got = resolve_model("emptyext/anything", None, registry)
    assert (got.provider, got.api) == ("emptyext", "unknown")


async def test_guard2_never_takes_a_catalogued_provider_openrouter_cannot_be(
    world: World,
) -> None:
    """Clause 3 — ``xai`` is not an OpenRouter namespace (it spells xAI ``x-ai/``)."""

    world.shell(**OR)
    got = resolve_model("xai/grok-9-new", None, await world.registry())
    assert got.provider == "xai"


async def test_guard2_needs_an_openrouter_key_of_the_users_own(world: World) -> None:
    """Clause 4 — a ``.env`` OpenRouter key never enables it (A19), an ``auth.json`` one does."""

    world.dotenv(DOR)
    assert resolve_model("newlab/model-x", None, await world.registry()).provider != "openrouter"
    stored = {"openrouter": {"type": "api_key", "key": "or-stored-fake"}}
    got = resolve_model("newlab/model-x", None, await world.registry(auth=stored))
    assert (got.provider, got.id) == ("openrouter", "newlab/model-x")


# === guard 1, per judgement =====================================================


@pytest.mark.parametrize("source", ["dotenv", "exported"])
async def test_a_dotenv_key_does_not_break_a_bare_id_tie(world: World, source: str) -> None:
    """pi :470-504 — with only a ``.env`` Anthropic key, ``claude-haiku-4-5`` is ambiguous.

    The same key exported breaks the tie (A16).
    """

    if source == "dotenv":
        world.dotenv(DANT)
    else:
        world.shell(**ANT)
    got = resolve_model("claude-haiku-4-5", None, await world.registry())
    if source == "dotenv":
        assert (got.provider, got.api) == ("", "unknown")
    else:
        assert got.provider == "anthropic"


async def test_a_dotenv_key_does_not_decline_the_swap(world: World) -> None:
    """pi :525-540 — the swap asks whether the INFERRED provider is authenticated (A06/M1)."""

    world.shell(**OR)
    world.dotenv(DOAI)
    got = resolve_model("openai/gpt-4o-mini", None, await world.registry())
    assert got.provider == "openrouter"


async def test_a_dotenv_key_does_not_take_the_swap(world: World) -> None:
    """The other side of :525-540: a ``.env`` OpenRouter key is not the swap's target (A34)."""

    world.dotenv(DOR)
    got = resolve_model("openai/gpt-4o-mini", None, await world.registry())
    assert got.provider == "openai"


async def test_a_dotenv_key_does_not_pick_the_raw_fallback(world: World) -> None:
    """pi :548-555 — the planted capture (A10): the user's own Anthropic key keeps the string."""

    world.shell(**ANT)
    world.dotenv(DOR)
    got = resolve_model("anthropic/claude-haiku-4.5", None, await world.registry())
    assert got.provider == "anthropic"


@pytest.mark.parametrize("source", ["dotenv", "exported"])
async def test_a_models_json_apikey_naming_a_dotenv_variable_does_not_count(
    world: World, source: str
) -> None:
    """A models.json ``apiKey`` naming a variable the ``.env`` supplied is the ``.env``'s key.

    ``resolve_config_value`` reads ``os.environ.get(name, name)``, so the name is
    an indirection to whatever set the variable. Exported, the gateway is the
    sole route-authenticated raw match and takes ``openai/gpt-4o`` (pi's swap);
    from a ``.env`` it does not count and pi's found hit (``openai``) stands.
    """

    if source == "dotenv":
        world.dotenv("MYGW_API_KEY=planted\n")
    else:
        world.shell(MYGW_API_KEY="exported")
    _MODELS_JSON["_apikey_name"] = {
        "providers": {
            "mygw": {
                "api": "openai-completions",
                "baseUrl": _GW,
                "apiKey": "MYGW_API_KEY",
                "models": [{"id": "openai/gpt-4o"}],
            }
        }
    }
    try:
        registry = await world.registry("_apikey_name")
    finally:
        _MODELS_JSON.pop("_apikey_name")
    got = resolve_model("openai/gpt-4o", None, registry)
    assert got.provider == ("openai" if source == "dotenv" else "mygw")


# === the #362 review round: clauses that had no row =============================


async def test_rule_u_takes_the_sole_user_defined_provider_among_several_of_yours(
    world: World,
) -> None:
    """ADR-0250 §2.1 step 2, "rule U" — aelix's, not pi's (pi :497-501 would refuse).

    ``gpt-4o-mini`` is served by four providers; two are route-authenticated —
    ``openai`` (exported) and the user's ``mygw`` (a literal models.json key).
    The user's own provider takes it. The #362 review's SB35 removed the rule and
    nothing turned red.
    """

    world.shell(**OAI)
    got = resolve_model("gpt-4o-mini", None, await world.registry("gwbare"))
    assert (got.provider, got.id, got.base_url) == ("mygw", "gpt-4o-mini", _GW)


async def test_openrouter_default_model_runs_on_an_openrouter_key_from_any_source(
    world: World,
) -> None:
    """ADR-0250 §2.6: the SHELL variable chose OpenRouter, so a ``.env`` key authenticates it.

    The review measured the CLI (R08: ``OR … token=or_env``); its SB29 (asking
    for a route-authenticating key here) left every test green.
    """

    world.shell(OPENROUTER_DEFAULT_MODEL="x-ai/grok-4.3")
    world.dotenv(DOR)
    got = resolve_model(None, None, await world.registry())
    assert (got.provider, got.id, got.base_url) == ("openrouter", "x-ai/grok-4.3", _OR)


def _typed_not_found(model: str) -> str:
    """pi's not-found text with #370's ``--api-key`` hint (ADR-0250 §2.12)."""

    return (
        f'Model "{model}" not found. Use --list-models to see available models. (--api-key '
        "does not send an id this build does not know to OpenRouter; to send it there with "
        f"that key, use --model openrouter/{model} or --provider openrouter --model {model}.)"
    )


def _bare_not_found(model: str) -> str:
    """pi's not-found text alone (``model-resolver.ts`` :599-605)."""

    return f'Model "{model}" not found. Use --list-models to see available models.'


TYPED_KEY: list[tuple[Any, ...]] = [
    # id, shell, --model, expected (provider, id, base_url), warning, error
    # The review's R03: guard 2's widened arm carried K to openrouter.ai.
    (
        "R03",
        OR,
        "anthropic/claude-haiku-4-5",
        ("anthropic", "claude-haiku-4-5", _ANT),
        None,
        None,
    ),
    # R02: guard 2 as decided (an uncatalogued id under a catalogued prefix).
    (
        "R02",
        OR,
        "anthropic/claude-new-9",
        ("anthropic", "claude-new-9", _ANT),
        'Model "claude-new-9" not found for provider "anthropic". Using custom model id.',
        None,
    ),
    # A40: pi's swap carried K to openrouter.ai (pi does too; aelix diverges).
    ("A40", OR, "openai/gpt-4o-mini", ("openai", "gpt-4o-mini", _OAI), None, None),
    # (5)'s raw match on OpenRouter (it lists the dotted spelling verbatim).
    (
        "raw",
        OR,
        "anthropic/claude-haiku-4.5",
        ("anthropic", "claude-haiku-4.5", _ANT),
        'Model "claude-haiku-4.5" not found for provider "anthropic". Using custom model id.',
        None,
    ),
    # Unchanged: the explicit OpenRouter spelling.
    ("explicit", {}, "openrouter/auto", ("openrouter", "auto", _OR), None, None),
    # #370: a prefix that names no provider is pi's not-found under --api-key
    # (guard 2 sent it to openrouter.ai with the typed key as the bearer; on
    # 62e2238b this row was ("openrouter", "newlab/model-x", _OR) with guard 2's
    # Note). The placeholder keeps the typed prefix for the late path (#367).
    (
        "unknown-prefix",
        OR,
        "newlab/model-x",
        ("newlab", "model-x", ""),
        None,
        _typed_not_found("newlab/model-x"),
    ),
    # The same text without an OpenRouter key of the user's own (decision: the
    # hint does not depend on which credentials exist).
    (
        "unknown-prefix-no-or",
        {},
        "newlab/model-x",
        ("newlab", "model-x", ""),
        None,
        _typed_not_found("newlab/model-x"),
    ),
    # An OpenRouter vendor namespace that is not a provider, with an id the
    # snapshot does not list: not found too (was guard 2, the issue's row I).
    (
        "xai-unlisted",
        OR,
        "x-ai/grok-4",
        ("x-ai", "grok-4", ""),
        None,
        _typed_not_found("x-ai/grok-4"),
    ),
    # ... but an id OpenRouter's catalogue lists is step 2's exact hit, as in pi
    # (``model-resolver.ts`` :465-504): K goes to OpenRouter (ADR-0250 §2.7).
    ("xai-listed", OR, "x-ai/grok-4.3", ("openrouter", "x-ai/grok-4.3", _OR), None, None),
    # #370 round 2 (Codex cat 4): a nested id is not found too, with the hint —
    # guard 2's shape takes every slashed string whose segments are non-empty.
    # A "two segments only" condition on guard 2 or a one-slash hint passed
    # every row above.
    (
        "nested",
        OR,
        "newlab/org/model-x",
        ("newlab", "org/model-x", ""),
        None,
        _typed_not_found("newlab/org/model-x"),
    ),
    # Round 2 (Codex cat 2), decided: a string with an empty segment cannot be
    # an OpenRouter id, so it gets pi's bare not-found, no OpenRouter routes
    # named (ADR-0250 §2.12).
    *(
        (f"empty-segment-{i}", OR, model, placeholder, None, _bare_not_found(model))
        for i, (model, placeholder) in enumerate(
            [
                ("newlab//model-x", ("newlab", "/model-x", "")),
                ("newlab/model-x/", ("newlab", "model-x/", "")),
                ("/newlab/model-x", ("", "/newlab/model-x", "")),
                ("/", ("", "/", "")),
            ]
        )
    ),
]


@pytest.mark.parametrize(
    ("row", "shell", "model", "expected", "warning", "error"),
    TYPED_KEY,
    ids=[r[0] for r in TYPED_KEY],
)
async def test_api_key_keeps_the_string_on_the_provider_its_prefix_names(
    world: World,
    row: str,
    shell: dict[str, str],
    model: str,
    expected: tuple[str, str, str],
    warning: str | None,
    error: str | None,
) -> None:
    """``--api-key`` (``typed_key``) is attached after resolution to the route's provider.

    The #362 review's must_fix: counted nowhere at resolve time, it let the swap,
    (5)'s raw match and guard 2 move a key typed for the provider the prefix
    names to ``openrouter.ai`` as the bearer, over the user's own OpenRouter key.
    #370: guard 2 never fires under it — a string no provider places is pi's
    not-found, and the route's provider (none) gets no key.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**shell)
    route = resolve_route(model, None, await world.registry(), typed_key=True)
    got = route.model
    assert (got.provider, got.id, got.base_url) == expected, row
    assert (route.warning, route.error) == (warning, error), row
    if error is not None:
        assert (route.kind, got.api) == ("error", "unknown"), row


@pytest.mark.parametrize("typed", [False, True], ids=["no-api-key", "api-key"])
async def test_guard_two_without_api_key_is_unchanged(world: World, typed: bool) -> None:
    """#370 changes guard 2 only under ``--api-key``: without it the user's own
    OpenRouter key still takes an unplaceable ``<vendor>/<model>`` as written,
    with the Note (ADR-0250 §2.4)."""

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**OR)
    route = resolve_route("newlab/model-x", None, await world.registry(), typed_key=typed)
    if typed:
        assert (route.kind, route.model.provider) == ("error", "newlab")
        return
    assert route.kind == "guard2"
    assert (route.model.provider, route.model.id, route.model.base_url) == (
        "openrouter",
        "newlab/model-x",
        _OR,
    )
    assert route.warning == (
        'Model "newlab/model-x" is not in this build\'s catalog; sending it to OpenRouter as '
        "written."
    )


# === verify round 4, B1: settings defaultProvider is not the user's choice =======

_MODELS_JSON["gwenv"] = {
    "providers": {
        **_RETRYPROBE,
        "mygw": {
            "api": "openai-completions",
            "baseUrl": _GW,
            "apiKey": "MYGW_API_KEY",
            "models": [{"id": "gpt-4o-mini"}],
        },
    }
}
_MODELS_JSON["repoint_or"] = {
    "providers": {**_RETRYPROBE, "openrouter": {"baseUrl": _GW}},
}
_MODELS_JSON["empty"] = {"providers": {}}
# models.json tunes openrouter (headers) without re-pointing it: not the user's endpoint.
_MODELS_JSON["tuned_or"] = {
    "providers": {**_RETRYPROBE, "openrouter": {"headers": {"X-Title": "mine"}}},
}
# models.json tunes openai (headers) without re-pointing it: not the user's endpoint
# either (Codex's third cross-review, C5).
_MODELS_JSON["tuned_oai"] = {
    "providers": {**_RETRYPROBE, "openai": {"headers": {"X-Title": "mine"}}},
}
DMYGW = "MYGW_API_KEY=mygw-dotenv-fake\n"
GROQ = {"GROQ_API_KEY": "groq-shell-fake"}
_GROQ = "https://api.groq.com/openai/v1"

# id, shell, .env (each row runs with it and without it), models.json, --model,
# settings defaultProvider, expected (provider, id, base_url) or None (a refusal)
DEFAULT_PROVIDER_ROWS: list[tuple[Any, ...]] = [
    # verify4's S4/S5: the project names openai; the user's own key is OpenRouter's.
    ("S4", OR, DOAI, "base", "gpt-4o-mini", "openai", None),
    # S12: the ADR-0195 home of a bare id nothing claims.
    ("S12", OR, DOAI, "base", "newmodel-x", "openai", None),
    # S13: the issue's own shape, one step removed (anthropic via the project file).
    ("S13", OR, DANT, "base", "claude-haiku-4-5", "anthropic", None),
    # The user's own key authenticates the default: honoured, as before.
    (
        "own-tie",
        {**OR, **OAI},
        DANT,
        "base",
        "gpt-4o-mini",
        "openai",
        ("openai", "gpt-4o-mini", _OAI),
    ),
    (
        "own-home",
        {**OR, **OAI},
        DANT,
        "base",
        "newmodel-x",
        "openai",
        ("openai", "newmodel-x", _OAI),
    ),
    # The default names the user's own endpoint (models.json), keyed from the .env:
    # honoured (a project file can name it, but cannot define it).
    ("ud-tie", OR, DMYGW, "gwenv", "gpt-4o-mini", "mygw", ("mygw", "gpt-4o-mini", _GW)),
    ("ud-home", OR, DMYGW, "gwenv", "newmodel-x", "mygw", ("mygw", "newmodel-x", _GW)),
    # A re-pointed openrouter is the user's endpoint too (_own_endpoint_providers).
    (
        "ud-home-or",
        OAI,
        DOR,
        "repoint_or",
        "newmodel-x",
        "openrouter",
        ("openrouter", "newmodel-x", _GW),
    ),
    # The built-in openrouter is NOT the user's endpoint; only a models.json
    # ``baseUrl`` makes it one (verify round 5, B1h: counting it unconditionally
    # kept every other row green while a project ``{"defaultProvider":
    # "openrouter"}`` plus a ``.env`` OpenRouter key took the tie and the home
    # again - real CLI, ``qwen/qwen3-32b`` with GROQ exported went to
    # ``openrouter.ai``).
    (
        "or-builtin-tie",
        GROQ,
        DOR,
        "base",
        "qwen/qwen3-32b",
        "openrouter",
        ("groq", "qwen/qwen3-32b", _GROQ),
    ),
    ("or-builtin-home", OAI, DOR, "base", "newmodel-x", "openrouter", None),
    ("or-tuned-home", OAI, DOR, "tuned_or", "newmodel-x", "openrouter", None),
    # Codex's third cross-review, C5: the same for a built-in other than openrouter.
    # A mutant counting every non-openrouter provider with a models.json entry as
    # the user's endpoint kept all 279 #362 rows green while a headers-only openai
    # plus a project defaultProvider=openai homed brand-new-model under openai on
    # the .env key (api.openai.com, Bearer repo-oai-fake).
    ("oai-tuned-home", OR, DOAI, "tuned_oai", "brand-new-model", "openai", None),
    # No credential of the user's own anywhere (``base`` would hold retryprobe's
    # literal key): today's behaviour (ADR-0250 §6).
    ("residual", {}, DOAI, "empty", "newmodel-x", "openai", ("openai", "newmodel-x", _OAI)),
]


@pytest.mark.parametrize("with_dotenv", [False, True], ids=["without .env", "with .env"])
@pytest.mark.parametrize(
    ("row", "shell", "dotenv", "variant", "model", "default", "expected"),
    DEFAULT_PROVIDER_ROWS,
    ids=[r[0] for r in DEFAULT_PROVIDER_ROWS],
)
async def test_settings_default_provider_counts_only_for_your_own_credential_or_endpoint(
    world: World,
    row: str,
    shell: dict[str, str],
    dotenv: str,
    variant: str,
    model: str,
    default: str,
    expected: tuple[str, str, str] | None,
    with_dotenv: bool,
) -> None:
    """Verify round 4, B1 (ADR-0250 §2.1 step 2): the tie-break and the home.

    Settings ``defaultProvider`` is the MERGED value: a cloned repo's
    ``.aelix/settings.json`` sets it, even over the user's global one and in an
    untrusted directory. On ``001ef77d`` step 2 honoured it before any auth
    check, so with the user's own OpenRouter key exported a project
    ``{"defaultProvider": "anthropic"}`` plus a ``.env`` ``ANTHROPIC_API_KEY``
    sent ``--model claude-haiku-4-5`` to ``api.anthropic.com`` on the repo's key
    (verify4's S13, real CLI). While the user holds a credential of their own,
    the default now counts only when it names a provider that credential
    authenticates, or the user's own endpoint; otherwise pi's answer stands (pi
    has no ``defaultProvider`` tie-break, ``model-resolver.ts:465-503``). The
    ``.env`` never turns a refusal into a send: every row gives the same answer
    with and without it, except where the default names the user's own endpoint
    and the ``.env`` key only authenticates that (the route is the same; only
    whether a key is found differs).
    """

    world.shell(**shell)
    if with_dotenv:
        world.dotenv(dotenv)
    registry = await world.registry(variant)
    got = resolve_model(model, None, registry, default)
    if expected is None:
        assert (got.api, got.base_url) == ("unknown", ""), (row, got)
    else:
        assert (got.provider, got.id, got.base_url) == expected, (row, got)


async def test_a_set_aside_default_provider_is_named_in_the_refusal(world: World) -> None:
    """verify4's S13 at the resolver: pi's ambiguity text, then why the default did not count."""

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**OR)
    world.dotenv(DANT)
    route = resolve_route("claude-haiku-4-5", None, await world.registry(), "anthropic")
    assert route.error is not None
    assert route.error.startswith('Model "claude-haiku-4-5" is ambiguous across providers: ')
    assert route.error.endswith(
        ' Settings defaultProvider "anthropic" was not used: no credential of your own '
        "authenticates it, and a project .aelix/settings.json can set it "
        "(ANTHROPIC_API_KEY came from a project .env)."
    )
    home = resolve_route("newmodel-x", None, await world.registry(), "anthropic")
    assert home.error == (
        'Model "newmodel-x" not found. Use --list-models to see available models. '
        'Settings defaultProvider "anthropic" was not used: no credential of your own '
        "authenticates it, and a project .aelix/settings.json can set it "
        "(ANTHROPIC_API_KEY came from a project .env)."
    )


# === verify round 4, N4: the declined-route hint names another route only =======


@pytest.mark.parametrize("variant", ["base", "repoint_or"], ids=["built-in", "re-pointed"])
async def test_the_dotenv_hint_never_offers_the_route_already_taken(
    world: World, variant: str
) -> None:
    """verify4 L-F6/L-F7: ``--model openrouter/auto`` with the OpenRouter key in the ``.env``.

    The route is OpenRouter (``auto``), on the ``.env`` key, and ``001ef77d`` also
    printed 'Warning: "openrouter/auto" is also openrouter's model id, but
    OPENROUTER_API_KEY came from a project .env, which does not choose a route;
    use openrouter/openrouter/auto or export the key to send it there.' — "there"
    being where it already went. The hint is for a route the ``.env`` key did not
    get to choose, so a candidate on the provider taken is skipped.
    """

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.shell(**OAI)
    world.dotenv(DOR)
    route = resolve_route("openrouter/auto", None, await world.registry(variant))
    assert (route.model.provider, route.model.id) == ("openrouter", "auto")
    assert route.error is None
    assert route.warning is None


async def test_the_dotenv_hint_still_names_a_route_on_another_provider(world: World) -> None:
    """The hint's own subject is kept: A34's declined swap names the other provider."""

    from aelix_coding_agent.cli.runtime_bootstrap import resolve_route

    world.dotenv(DOR)
    world.shell(**ANT)
    route = resolve_route("openai/gpt-4o-mini", None, await world.registry())
    assert route.model.provider == "openai"
    assert route.warning is not None
    assert "openrouter's model id" in route.warning and "use openrouter/openai/gpt-4o-mini" in (
        route.warning
    )
