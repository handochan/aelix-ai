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


class _SeqClient:
    """An ``AsyncClient`` stub returning a scripted payload per GET.

    Records ``(url, headers)`` for every GET into a shared ``calls`` list so a
    test can assert both the exact number of GETs and the pagination URLs. The
    last payload is repeated if more GETs happen than payloads supplied.
    """

    def __init__(self, payloads: list, calls: list) -> None:
        self._payloads = payloads
        self._calls = calls

    async def __aenter__(self) -> _SeqClient:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        i = len(self._calls)
        self._calls.append((url, headers))
        return _FakeResp(self._payloads[min(i, len(self._payloads) - 1)])


def _patch_httpx_seq(monkeypatch, payloads: list) -> list:
    """Patch ``httpx.AsyncClient`` to a :class:`_SeqClient`; return the calls log."""

    import httpx

    calls: list = []
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **k: _SeqClient(payloads, calls)
    )
    return calls


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


async def test_fetch_gemini_compatible_headers_and_prefix(monkeypatch) -> None:
    # Issue #36: the Gemini Developer API ListModels authenticates with
    # ``x-goog-api-key`` (a Bearer token 401s against generativelanguage), and
    # returns ``name: "models/<id>"`` — the ``models/`` prefix must be stripped
    # so the registered id matches the catalog id (and reads cleanly in /model).
    _patch_httpx(
        monkeypatch,
        {"models": [{"name": "models/gemini-2.0-flash"}, {"name": "models/gemini-1.5-pro"}]},
    )
    ids = await lw._fetch_openai_model_ids(
        "https://generativelanguage.googleapis.com/v1beta",
        "gkey",
        api="google-generative-ai",
    )
    assert ids == ["gemini-1.5-pro", "gemini-2.0-flash"]  # sorted + prefix-stripped
    assert (
        _FakeClient.last_url
        == "https://generativelanguage.googleapis.com/v1beta/models"
    )
    assert _FakeClient.last_headers == {"x-goog-api-key": "gkey"}


async def test_fetch_gemini_filters_generate_content(monkeypatch) -> None:
    # ADR-0190 polish: for google-* apis, keep only models whose
    # ``supportedGenerationMethods`` includes ``generateContent`` (drops
    # embedding / imagen / aqa-only models). A model MISSING the field is kept
    # (conservative KEEP-if-absent — do not over-filter a sparse endpoint).
    _patch_httpx(
        monkeypatch,
        {
            "models": [
                {
                    "name": "models/gemini-2.0-flash",
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {"name": "models/gemini-legacy"},  # no field → conservative KEEP
            ]
        },
    )
    ids = await lw._fetch_openai_model_ids(
        "https://generativelanguage.googleapis.com/v1beta",
        "gkey",
        api="google-generative-ai",
    )
    # The embedding-only model is dropped; the chat model and the field-less
    # model both register.
    assert ids == ["gemini-2.0-flash", "gemini-legacy"]


async def test_fetch_gemini_follows_next_page_token(monkeypatch) -> None:
    # ADR-0190 polish: for google-* apis, follow ``nextPageToken`` and accumulate
    # generateContent models across pages. Page 1 carries a token, page 2 does
    # not → exactly two GETs, both pages' chat models register (page 2's
    # embedding-only model is still filtered out).
    page1 = {
        "models": [
            {
                "name": "models/gemini-a",
                "supportedGenerationMethods": ["generateContent"],
            }
        ],
        "nextPageToken": "TOK2",
    }
    page2 = {
        "models": [
            {
                "name": "models/gemini-b",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/embed-only",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    }
    calls = _patch_httpx_seq(monkeypatch, [page1, page2])
    ids = await lw._fetch_openai_model_ids(
        "https://generativelanguage.googleapis.com/v1beta",
        "gkey",
        api="google-generative-ai",
    )
    assert ids == ["gemini-a", "gemini-b"]  # both pages, embedding filtered
    assert len(calls) == 2  # exactly two GETs (page 1 + page 2)
    assert calls[0][0] == "https://generativelanguage.googleapis.com/v1beta/models"
    # page 2 re-requests the SAME /models URL with ?pageToken appended, key/base
    # preserved (the key rides the header, not the query).
    assert calls[1][0] == (
        "https://generativelanguage.googleapis.com/v1beta/models?pageToken=TOK2"
    )
    assert calls[1][1] == {"x-goog-api-key": "gkey"}


async def test_fetch_openai_single_get_unfiltered(monkeypatch) -> None:
    # Regression: a plain OpenAI-compatible fetch does exactly ONE GET and is
    # UNFILTERED — the generateContent capability filter and nextPageToken
    # pagination are google-* only. A stray ``supportedGenerationMethods`` /
    # ``nextPageToken`` in an OpenAI-shaped response must be ignored.
    payload = {
        "data": [
            {"id": "gpt-x", "supportedGenerationMethods": ["embedContent"]},
            {"id": "gpt-y"},
        ],
        "nextPageToken": "SHOULD-BE-IGNORED",
    }
    calls = _patch_httpx_seq(monkeypatch, [payload, {"data": [{"id": "gpt-z"}]}])
    ids = await lw._fetch_openai_model_ids(
        "https://h/v1", "k", api="openai-completions"
    )
    # ``gpt-x`` survives despite an embedding-only method list (no filter), and
    # the second page is never fetched (no pagination).
    assert ids == ["gpt-x", "gpt-y"]
    assert len(calls) == 1  # single GET, nextPageToken NOT followed


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


class _ScopeSettings:
    """Settings double exposing only ``get_enabled_models`` (the scope read)."""

    def __init__(self, patterns: list[str] | None) -> None:
        self._patterns = patterns

    def get_enabled_models(self) -> list[str] | None:
        return None if self._patterns is None else list(self._patterns)


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


def _cat(*pairs: tuple[str, str]) -> list[object]:
    """A fake auth catalog: objects with .provider/.id for _pattern_matches."""

    return [types.SimpleNamespace(provider=p, id=i) for p, i in pairs]


async def _register_myco(
    tmp_path, monkeypatch, settings_manager, catalog=None
) -> list[object]:
    """Register 2 models ('m1','m2') for 'myco'; return the committed lines.

    ``catalog`` (list of ``(provider, id)``) is attached to the registry as
    ``get_available()`` so the scope-aware message exercises the real empty-match
    lockout guard; omit it to test the best-effort fallback (no registry catalog).
    """

    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["m1", "m2"]))
    reg = types.SimpleNamespace(
        _models_json_path=str(tmp_path / "models.json"),
        _load_models=lambda: None,
    )
    if catalog is not None:
        reg.get_available = lambda: _cat(*catalog)

    async def fake_multiselect(_title, _options, *, selected):
        return ({"m1", "m2"}, {})

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
        settings_manager=settings_manager,
    )
    assert ok is True
    return commits


async def test_register_message_no_scope_is_green(tmp_path, monkeypatch) -> None:
    # No /scoped-models allow-list (None sentinel) → the plain "they now appear
    # in /model" confirmation, unchanged from the pre-scope behaviour.
    commits = await _register_myco(tmp_path, monkeypatch, _ScopeSettings(None))
    assert any("they now appear in /model" in _plain(c) for c in commits)
    assert not any("hidden" in _plain(c) for c in commits)


async def test_register_message_all_hidden_warns(tmp_path, monkeypatch) -> None:
    # An ACTIVE allow-list (it matches a catalog model 'other/keep') that matches
    # NEITHER new model → warn the whole batch is hidden (not the false "appear").
    commits = await _register_myco(
        tmp_path,
        monkeypatch,
        _ScopeSettings(["other/keep"]),
        catalog=[("other", "keep"), ("myco", "m1"), ("myco", "m2")],
    )
    msg = " ".join(_plain(c) for c in commits)
    assert "hides them from /model" in msg
    assert "run /scoped-models" in msg
    assert "they now appear in /model" not in msg


async def test_register_message_partial_hidden_warns_count(tmp_path, monkeypatch) -> None:
    # Allow-list includes exactly one of the two new models → the message reports
    # 1 shown / 1 hidden (canonical provider/id identity via _pattern_matches).
    commits = await _register_myco(
        tmp_path,
        monkeypatch,
        _ScopeSettings(["myco/m1"]),
        catalog=[("myco", "m1"), ("myco", "m2")],
    )
    msg = " ".join(_plain(c) for c in commits)
    assert "1 now appear in /model" in msg
    assert "1 are hidden by your /scoped-models allow-list" in msg


async def test_register_message_all_visible_is_green(tmp_path, monkeypatch) -> None:
    # Allow-list covers both new models (glob) → green "they now appear".
    commits = await _register_myco(
        tmp_path,
        monkeypatch,
        _ScopeSettings(["myco/*"]),
        catalog=[("myco", "m1"), ("myco", "m2")],
    )
    assert any("they now appear in /model" in _plain(c) for c in commits)
    assert not any("hidden" in _plain(c) for c in commits)


async def test_register_message_stale_scope_shows_all_green(tmp_path, monkeypatch) -> None:
    # Corner case (review LOW): a fully-STALE allow-list matching NOTHING in the
    # catalog → scoped_available's empty-match lockout guard shows the FULL list,
    # so the new models are NOT hidden. The message must be green, not a false
    # "hidden" warning.
    commits = await _register_myco(
        tmp_path,
        monkeypatch,
        _ScopeSettings(["ghost/missing"]),
        catalog=[("myco", "m1"), ("myco", "m2")],
    )
    assert any("they now appear in /model" in _plain(c) for c in commits)
    assert not any("hidden" in _plain(c) for c in commits)


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


async def test_run_custom_gemini_auto_registers(tmp_path, monkeypatch) -> None:
    # Issue #36 / #15 (ADR-0173): a Gemini-compatible custom provider now maps to
    # the ``google-generative-ai`` adapter (was ``None`` → manual-note fallback),
    # so its ListModels result auto-registers into models.json.
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["gemini-2.0-flash"]))
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
        return "Gemini-compatible"

    async def fake_multiselect(_title, _options, *, selected):
        return (set(selected), {})

    await lw._run_custom(
        auth_storage=_Auth(),
        select=fake_select,
        prompt_input=_prompt_seq(
            "mygemini", "https://generativelanguage.googleapis.com/v1beta", "gkey"
        ),
        commit=lambda _x: None,
        Text=Text,
        multiselect=fake_multiselect,
        model_registry=reg,
    )

    # Key stored AND models registered under the google-generative-ai adapter
    # (not skipped to the manual-note fallback).
    assert stored == {"mygemini": "gkey"}
    assert reloaded == [True]
    cfg = json.loads((tmp_path / "models.json").read_text())
    prov = cfg["providers"]["mygemini"]
    assert prov["api"] == "google-generative-ai"
    assert prov["baseUrl"] == "https://generativelanguage.googleapis.com/v1beta"
    assert [m["id"] for m in prov["models"]] == ["gemini-2.0-flash"]
    assert validate_models_config(cfg) == []


# ── _run_custom: OpenAI Responses-API protocol ─────────────────────────
def test_responses_protocol_registered() -> None:
    # The Responses-API protocol is offered in the wizard and maps to the
    # openai-responses adapter, so a custom OpenAI endpoint can run against
    # /v1/responses — not only chat/completions.
    assert "OpenAI-compatible (Responses API)" in lw._CUSTOM_PROTOCOLS
    assert lw._PROTOCOL_API["OpenAI-compatible (Responses API)"] == "openai-responses"
    # The plain OpenAI-compatible label still means chat/completions (unchanged).
    assert lw._PROTOCOL_API["OpenAI-compatible"] == "openai-completions"


async def test_fetch_openai_responses_stays_bearer(monkeypatch) -> None:
    # The Responses API shares OpenAI's /v1/models catalog probe → Bearer auth and
    # a single UNFILTERED GET (the google-only capability filter / pagination must
    # not fire; ``openai-responses`` is not a google-* api).
    _patch_httpx(monkeypatch, {"data": [{"id": "gpt-5.4"}, {"id": "o4-mini"}]})
    ids = await lw._fetch_openai_model_ids(
        "https://api.openai.com/v1", "sk-openai", api="openai-responses"
    )
    assert ids == ["gpt-5.4", "o4-mini"]  # sorted, unfiltered
    assert _FakeClient.last_url == "https://api.openai.com/v1/models"
    assert _FakeClient.last_headers == {"Authorization": "Bearer sk-openai"}


async def test_run_custom_openai_responses_auto_registers(tmp_path, monkeypatch) -> None:
    # New protocol: picking "OpenAI-compatible (Responses API)" registers the
    # fetched models under the openai-responses adapter (turns hit /v1/responses),
    # rather than the completions-only default. The model-list probe is the same
    # OpenAI-shaped Bearer /v1/models call.
    monkeypatch.setattr(lw, "_fetch_openai_model_ids", _aret(["gpt-5.4", "o4-mini"]))
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
        return "OpenAI-compatible (Responses API)"

    async def fake_multiselect(_title, _options, *, selected):
        return (set(selected), {})

    await lw._run_custom(
        auth_storage=_Auth(),
        select=fake_select,
        prompt_input=_prompt_seq("myopenai", "https://api.openai.com/v1", "sk-openai"),
        commit=lambda _x: None,
        Text=Text,
        multiselect=fake_multiselect,
        model_registry=reg,
    )

    # Key stored AND models registered under the openai-responses adapter.
    assert stored == {"myopenai": "sk-openai"}
    assert reloaded == [True]
    cfg = json.loads((tmp_path / "models.json").read_text())
    prov = cfg["providers"]["myopenai"]
    assert prov["api"] == "openai-responses"
    assert prov["baseUrl"] == "https://api.openai.com/v1"
    assert [m["id"] for m in prov["models"]] == ["gpt-5.4", "o4-mini"]
    assert validate_models_config(cfg) == []
