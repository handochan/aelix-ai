"""WP-8 follow-up — custom-provider model fetch + registration (login_wizard).

Covers the new OpenAI-compatible custom-provider flow: fetch ``{base_url}/models``,
let the user pick, persist a SCHEMA-VALID models.json (key stays in auth.json), and
reload the registry so the models appear in /model.
"""

from __future__ import annotations

import json
import types

from aelix_coding_agent.models_json import validate_models_config
from aelix_coding_agent.tui import login_wizard as lw
from rich.text import Text


# ── _fetch_openai_model_ids ────────────────────────────────────────────
class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


class _FakeClient:
    last_url: str = ""
    last_headers: dict | None = None

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        _FakeClient.last_url = url
        _FakeClient.last_headers = headers
        return _FakeResp(self._payload)


def _patch_httpx(monkeypatch, payload: object) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(payload))


def _aret(value: object):
    async def _f(*_a: object, **_k: object) -> object:
        return value

    return _f


async def test_fetch_openai_data_shape(monkeypatch) -> None:
    _patch_httpx(monkeypatch, {"data": [{"id": "gpt-y"}, {"id": "gpt-x"}]})
    ids = await lw._fetch_openai_model_ids("https://h/v1", "k")
    assert ids == ["gpt-x", "gpt-y"]  # sorted + de-duped
    assert _FakeClient.last_url == "https://h/v1/models"
    assert _FakeClient.last_headers == {"Authorization": "Bearer k"}


async def test_fetch_models_key_and_bare_list(monkeypatch) -> None:
    _patch_httpx(monkeypatch, {"models": [{"id": "m1"}, {"name": "m2"}]})
    assert await lw._fetch_openai_model_ids("https://h/v1", "k") == ["m1", "m2"]
    _patch_httpx(monkeypatch, ["b", "a", "a"])
    ids = await lw._fetch_openai_model_ids("https://h", "")  # no key → no header
    assert ids == ["a", "b"]
    assert _FakeClient.last_headers == {}


async def test_fetch_anthropic_compatible_headers(monkeypatch) -> None:
    # A genuine Anthropic-compatible endpoint authenticates the model-list probe
    # with ``x-api-key`` + a pinned ``anthropic-version`` — a Bearer token 401s
    # there and the feature silently degrades to the manual note (review LOW).
    _patch_httpx(monkeypatch, {"data": [{"id": "claude-x"}]})
    ids = await lw._fetch_openai_model_ids(
        "https://anth.test/v1", "sk-ant", api="anthropic-messages"
    )
    assert ids == ["claude-x"]
    assert _FakeClient.last_url == "https://anth.test/v1/models"
    assert _FakeClient.last_headers == {
        "x-api-key": "sk-ant",
        "anthropic-version": lw._ANTHROPIC_VERSION,
    }
    assert lw._ANTHROPIC_VERSION == "2023-06-01"  # the SDK-pinned value


async def test_fetch_openai_compatible_stays_bearer(monkeypatch) -> None:
    # The OpenAI-shaped path (and any non-anthropic api) keeps Bearer auth.
    _patch_httpx(monkeypatch, {"data": [{"id": "gpt-x"}]})
    await lw._fetch_openai_model_ids("https://h/v1", "k", api="openai-completions")
    assert _FakeClient.last_headers == {"Authorization": "Bearer k"}
    # api=None (the legacy positional call) also stays on Bearer.
    await lw._fetch_openai_model_ids("https://h/v1", "k")
    assert _FakeClient.last_headers == {"Authorization": "Bearer k"}


# ── _write_custom_models_json ──────────────────────────────────────────
def test_write_models_json_is_schema_valid(tmp_path) -> None:
    from aelix_coding_agent.models_json import validate_config_semantics

    p = tmp_path / "models.json"
    lw._write_custom_models_json(
        str(p), "myco", "https://h/v1", "openai-completions", "sk-secret", ["m2", "m1"]
    )
    cfg = json.loads(p.read_text())
    assert validate_models_config(cfg) == []  # structural schema OK
    # The semantic validator REQUIRES apiKey for a custom provider — must not raise
    # (this is the bug the writer must avoid: an apiKey-less file is rejected).
    validate_config_semantics(cfg)
    prov = cfg["providers"]["myco"]
    assert prov["api"] == "openai-completions"
    assert prov["baseUrl"] == "https://h/v1"
    assert [m["id"] for m in prov["models"]] == ["m1", "m2"]  # sorted
    assert prov["apiKey"] == "sk-secret"  # written so the loader accepts it
    # The file holds a secret → owner-only (mirrors auth.json's 0600).
    assert (p.stat().st_mode & 0o777) == 0o600


def test_write_models_json_merges_existing(tmp_path) -> None:
    p = tmp_path / "models.json"
    p.write_text(
        json.dumps(
            {
                "providers": {
                    "other": {
                        "api": "openai-completions",
                        "baseUrl": "https://o",
                        "apiKey": "sk-o",
                        "models": [{"id": "o1"}],
                    }
                }
            }
        )
    )
    lw._write_custom_models_json(
        str(p), "myco", "https://h", "openai-completions", "sk-secret", ["m1"]
    )
    cfg = json.loads(p.read_text())
    assert set(cfg["providers"]) == {"other", "myco"}  # existing provider preserved
    assert validate_models_config(cfg) == []


# ── _register_custom_models ────────────────────────────────────────────
async def test_register_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["m1", "m2"]))
    reloaded: list[bool] = []
    reg = types.SimpleNamespace(
        _models_json_path=str(tmp_path / "models.json"),
        _load_models=lambda: reloaded.append(True),
    )

    async def fake_multiselect(title, options, *, selected):
        assert selected == {"m1", "m2"}  # default = all
        return ({"m1"}, {})

    commits: list[object] = []
    ok = await lw._register_custom_models(
        provider_id="myco",
        base_url="https://h/v1",
        api="openai-completions",
        api_key="k",
        model_registry=reg,
        multiselect=fake_multiselect,
        commit=commits.append,
        Text=Text,
    )
    assert ok is True
    assert reloaded == [True]  # registry reloaded
    cfg = json.loads((tmp_path / "models.json").read_text())
    assert [m["id"] for m in cfg["providers"]["myco"]["models"]] == ["m1"]


async def test_register_degrades_on_fetch_error(monkeypatch) -> None:
    async def boom(*_a: object, **_k: object) -> list[str]:
        raise RuntimeError("dns")

    monkeypatch.setattr(lw, "_fetch_openai_model_ids", boom)
    commits: list[object] = []
    ok = await lw._register_custom_models(
        provider_id="x",
        base_url="u",
        api="openai-completions",
        api_key="k",
        model_registry=types.SimpleNamespace(),
        multiselect=_aret(({"m"}, {})),
        commit=commits.append,
        Text=Text,
    )
    assert ok is False  # fall through to the honest note


async def test_register_empty_fetch_and_cancel(tmp_path, monkeypatch) -> None:
    # Empty fetch → False (no models offered).
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret([]))
    ok = await lw._register_custom_models(
        provider_id="x",
        base_url="u",
        api="openai-completions",
        api_key="k",
        model_registry=types.SimpleNamespace(),
        multiselect=_aret(({"m"}, {})),
        commit=lambda _x: None,
        Text=Text,
    )
    assert ok is False

    # Esc at the multiselect → None → False (key already stored upstream).
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["m1"]))
    ok2 = await lw._register_custom_models(
        provider_id="x",
        base_url="u",
        api="openai-completions",
        api_key="k",
        model_registry=types.SimpleNamespace(
            _models_json_path=str(tmp_path / "m.json")
        ),
        multiselect=_aret(None),
        commit=lambda _x: None,
        Text=Text,
    )
    assert ok2 is False


# ── _run_custom: Anthropic-compatible auto-registration (issue #49) ─────
def _prompt_seq(provider_id: str, base_url: str, key: str):
    """A ``prompt_input`` stub answering by the prompt text it is shown."""

    async def _f(prompt: str, *_a: object, **_k: object) -> str:
        if "Provider id" in prompt:
            return provider_id
        if "Base URL" in prompt:
            return base_url
        if "API key" in prompt:
            return key
        return ""

    return _f


async def test_run_custom_anthropic_auto_registers(tmp_path, monkeypatch) -> None:
    # Issue #49 / Pi #5953: an Anthropic-compatible custom provider whose
    # endpoint exposes ``/v1/models`` must auto-register (previously the gate
    # only fired for OpenAI-compatible → "no model registered").
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["claude-x", "claude-y"]))
    stored: dict[str, str] = {}
    reloaded: list[bool] = []

    class _Auth:
        async def set_api_key(self, provider: str, key: str) -> None:
            stored[provider] = key

    reg = types.SimpleNamespace(
        _models_json_path=str(tmp_path / "models.json"),
        _load_models=lambda: reloaded.append(True),
    )

    async def fake_select(_title: str, _options: object) -> str:
        return "Anthropic-compatible"

    async def fake_multiselect(_title, _options, *, selected):
        return (set(selected), {})

    await lw._run_custom(
        auth_storage=_Auth(),
        select=fake_select,
        prompt_input=_prompt_seq("myanth", "https://anth.test/v1", "sk-ant"),
        commit=lambda _x: None,
        Text=Text,
        multiselect=fake_multiselect,
        model_registry=reg,
    )

    # The key was stored AND the models were registered under the
    # anthropic-messages adapter (not skipped to the manual-note fallback).
    assert stored == {"myanth": "sk-ant"}
    assert reloaded == [True]
    cfg = json.loads((tmp_path / "models.json").read_text())
    prov = cfg["providers"]["myanth"]
    assert prov["api"] == "anthropic-messages"
    assert prov["baseUrl"] == "https://anth.test/v1"
    assert [m["id"] for m in prov["models"]] == ["claude-x", "claude-y"]
    assert validate_models_config(cfg) == []
