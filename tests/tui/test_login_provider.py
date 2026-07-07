"""Issue #77 — extension login providers in the ``/login`` wizard.

Covers the process-global ``login_registry`` and the wizard integration: an
extension-registered provider (e.g. a corporate 'telnaut' whose sign-in asks for
an employee number) appears in the ``/login`` method list, and picking it runs
its custom ``authenticate`` handler and persists the returned credential. Also
covers the API-key sub-flow unioning extension-registered provider ids.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_coding_agent.login_registry import (
    LoginProvider,
    get_login_providers,
    register_login_provider,
    reset_login_providers,
    unregister_login_provider,
)
from aelix_coding_agent.tui.login_wizard import run_login

_METHOD_API_KEY = "Using an API key (built-in provider)"


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_login_providers()
    yield
    reset_login_providers()


class _FakeAuth:
    def __init__(self) -> None:
        self.stored: dict[str, str] = {}

    async def set_api_key(self, provider: str, key: str) -> None:
        self.stored[provider] = key

    async def get_auth_status(self, provider: str) -> Any:
        class _S:
            source = "apiKey"

        return _S()


def _wizard_fakes(*, pick: str, answers: list[str]):
    """Build the run_login dialog fakes. ``pick`` is the method label to select;
    ``answers`` feeds successive prompt_input calls."""

    it = iter(answers)
    committed: list[str] = []

    async def select(_msg: str, options: list[str]) -> str | None:
        # First call is the method list; return the requested pick if present.
        return pick if pick in options else None

    async def prompt_input(_msg: str, *, placeholder=None, password=False) -> str | None:
        return next(it, None)

    async def confirm(_t: str, _m: str) -> bool:
        return True

    def notify(_m: str, *, kind: str = "info") -> None:
        pass

    def commit(x: object) -> None:
        committed.append(str(x))

    return select, prompt_input, confirm, notify, commit, committed


# === login_registry ==========================================================


def test_registry_register_get_unregister() -> None:
    async def _auth(_ctx):
        return "x"

    register_login_provider(LoginProvider(id="telnaut", name="Telnaut", authenticate=_auth))
    assert [p.id for p in get_login_providers()] == ["telnaut"]
    unregister_login_provider("telnaut")
    assert get_login_providers() == []


def test_registry_last_write_wins() -> None:
    async def _a(_ctx):
        return "a"

    async def _b(_ctx):
        return "b"

    register_login_provider(LoginProvider(id="telnaut", name="First", authenticate=_a))
    register_login_provider(LoginProvider(id="telnaut", name="Second", authenticate=_b))
    providers = get_login_providers()
    assert len(providers) == 1 and providers[0].name == "Second"


def test_registry_ignores_provider_without_id() -> None:
    class _NoId:
        name = "x"

        async def authenticate(self, _ctx):
            return "x"

    register_login_provider(_NoId())  # no id → ignored, not raised
    assert get_login_providers() == []


# === run_login integration (the telnaut / 사번 flow) =========================


async def test_login_provider_appears_and_stores_credential() -> None:
    captured: dict[str, Any] = {}

    async def telnaut_auth(ctx):
        captured["emp"] = await ctx.prompt("사번을 입력하세요")
        captured["pw"] = await ctx.prompt("비밀번호", password=True)
        return f"{captured['emp']}:{captured['pw']}"

    register_login_provider(
        LoginProvider(id="telnaut", name="Telnaut (사내)", authenticate=telnaut_auth)
    )
    auth = _FakeAuth()
    select, prompt_input, confirm, notify, commit, _ = _wizard_fakes(
        pick="Telnaut (사내)", answers=["10231", "secret"]
    )
    await run_login(
        auth_storage=auth,
        select=select,
        prompt_input=prompt_input,
        confirm=confirm,
        notify=notify,
        commit=commit,
    )
    assert auth.stored == {"telnaut": "10231:secret"}
    assert captured == {"emp": "10231", "pw": "secret"}


async def test_login_provider_cancel_stores_nothing() -> None:
    async def _auth(ctx):
        return None  # user cancelled the custom flow

    register_login_provider(LoginProvider(id="telnaut", name="Telnaut", authenticate=_auth))
    auth = _FakeAuth()
    select, prompt_input, confirm, notify, commit, _ = _wizard_fakes(
        pick="Telnaut", answers=[]
    )
    await run_login(
        auth_storage=auth, select=select, prompt_input=prompt_input,
        confirm=confirm, notify=notify, commit=commit,
    )
    assert auth.stored == {}


async def test_login_provider_handler_exception_degrades() -> None:
    async def _auth(_ctx):
        raise RuntimeError("corporate SSO down")

    register_login_provider(LoginProvider(id="telnaut", name="Telnaut", authenticate=_auth))
    auth = _FakeAuth()
    select, prompt_input, confirm, notify, commit, committed = _wizard_fakes(
        pick="Telnaut", answers=[]
    )
    await run_login(
        auth_storage=auth, select=select, prompt_input=prompt_input,
        confirm=confirm, notify=notify, commit=commit,
    )
    assert auth.stored == {}
    assert any("login failed" in c and "corporate SSO down" in c for c in committed)


async def test_login_provider_empty_credential_stores_nothing() -> None:
    async def _auth(_ctx):
        return "   "  # whitespace only

    register_login_provider(LoginProvider(id="telnaut", name="Telnaut", authenticate=_auth))
    auth = _FakeAuth()
    select, prompt_input, confirm, notify, commit, committed = _wizard_fakes(
        pick="Telnaut", answers=[]
    )
    await run_login(
        auth_storage=auth, select=select, prompt_input=prompt_input,
        confirm=confirm, notify=notify, commit=commit,
    )
    assert auth.stored == {}
    assert any("no credential" in c for c in committed)


async def test_two_providers_same_name_are_dedup_labeled() -> None:
    async def _a(_ctx):
        return "a"

    register_login_provider(LoginProvider(id="p1", name="Dup", authenticate=_a))
    register_login_provider(LoginProvider(id="p2", name="Dup", authenticate=_a))
    seen_options: list[str] = []

    async def select(_msg: str, options: list[str]) -> str | None:
        seen_options.extend(options)
        return None  # abort at the method list — we only inspect the labels

    async def prompt_input(*_a, **_k):
        return None

    async def confirm(*_a):
        return True

    def notify(*_a, **_k):
        pass

    def commit(_x):
        pass

    await run_login(
        auth_storage=_FakeAuth(), select=select, prompt_input=prompt_input,
        confirm=confirm, notify=notify, commit=commit,
    )
    dup_labels = [o for o in seen_options if o.startswith("Dup")]
    assert dup_labels == ["Dup", "Dup (2)"]  # collision suffixed


# === API-key sub-flow union (Gap A) ==========================================


async def test_api_key_flow_unions_registered_provider_ids() -> None:
    class _FakeRegistry:
        def get_registered_providers(self) -> dict[str, Any]:
            return {"telnaut": object()}

    auth = _FakeAuth()
    offered: list[str] = []

    async def select(msg: str, options: list[str]) -> str | None:
        if msg == "Add a provider":
            return _METHOD_API_KEY
        offered.extend(options)  # the provider list
        return "telnaut"

    async def prompt_input(_msg: str, *, placeholder=None, password=False) -> str | None:
        return "sk-telnaut"

    async def confirm(*_a):
        return True

    def notify(*_a, **_k):
        pass

    def commit(_x):
        pass

    await run_login(
        auth_storage=auth,
        select=select,
        prompt_input=prompt_input,
        confirm=confirm,
        notify=notify,
        commit=commit,
        model_registry=_FakeRegistry(),
    )
    assert "telnaut" in offered
    assert auth.stored == {"telnaut": "sk-telnaut"}
