"""Issue #151 — the ``/login`` API-key picker annotates providers that cannot run.

The wizard used to offer every catalog provider as a bare id, take a key, STORE
it, and only then report that nothing runs. Six providers are in that state, for
two materially different reasons, so the picker now carries a per-row annotation
built at picker-BUILD time (after extensions have loaded) from the
credential-blind :mod:`aelix_coding_agent.core.runnable_models` predicate.

Two invariants the tests pin hardest:

* **The raw id still reaches ``set_api_key``.** Annotating splits the LABEL from
  the ID; the historical contract was id-as-label, so a regression here stores
  credentials under a bogus provider like
  ``"mistral  (unusable: no adapter in this build)"``.
* **Fail open.** No registered adapters, or a provider with no catalog rows,
  must NOT be annotated — an extension may register a provider whose models
  arrive later, and "unknown" is not "broken".
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from aelix_ai.api_registry import clear_providers, get_registered_providers
from aelix_coding_agent.core import runnable_models as rm
from aelix_coding_agent.tui.login_wizard import (
    _accepts_detail,
    _build_provider_labels,
    _make_block_detail,
    run_login,
)

_METHOD_API_KEY = "Using an API key (built-in provider)"

# Every env var that can flip one of the three RECOVERABLE providers to runnable.
# Scrubbed by the fixture below so the suite measures the same "config missing"
# state a first-run user sees, whatever the developer's shell happens to export.
_CONFIG_ENV = (
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_GATEWAY_ID",
    "GOOGLE_CLOUD_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)

# The six providers with ZERO runnable catalog rows, and why. Derived by running
# the live ``is_runnable`` over the bundled catalog with the real adapters
# registered and the vars above scrubbed; ``test_the_six_blocked_providers_are_
# exactly_these`` re-derives it so the table cannot silently rot.
_EXPECTED_BLOCKED = {
    "amazon-bedrock": rm.BLOCKED_NO_ADAPTER,
    "azure-openai-responses": rm.BLOCKED_NO_ADAPTER,
    "mistral": rm.BLOCKED_NO_ADAPTER,
    "cloudflare-ai-gateway": rm.BLOCKED_CONFIG_MISSING,
    "cloudflare-workers-ai": rm.BLOCKED_CONFIG_MISSING,
    "google-vertex": rm.BLOCKED_VERTEX_CONFIG,
}


@pytest.fixture
def real_adapters(monkeypatch):
    """Register the REAL adapter set for the duration of a test, then restore.

    The annotation is only meaningful against the adapters this build actually
    ships, so these tests drive ``runtime_bootstrap.register_providers`` rather
    than a hand-written api set — a build that gains a Mistral adapter must make
    the "mistral is unusable" assertions fail, not keep passing off a stale
    constant. The registry is process-global, so the previous contents are
    snapshotted and put back.
    """

    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    snapshot = get_registered_providers()
    clear_providers()
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    try:
        yield rm.supported_apis()
    finally:
        clear_providers()
        from aelix_ai.api_registry import register_provider_object

        for provider in snapshot.values():
            register_provider_object(provider)


def _model(**kw: Any) -> types.SimpleNamespace:
    kw.setdefault("id", "m")
    kw.setdefault("provider", "p")
    kw.setdefault("base_url", "https://example.test")
    return types.SimpleNamespace(**kw)


# === core: provider_block_reason =============================================


def test_provider_block_reason_names_the_shared_reason() -> None:
    apis = {"openai-completions"}
    rows = [_model(api="mistral-x"), _model(api="mistral-y")]
    assert rm.provider_block_reason(rows, apis) == rm.BLOCKED_NO_ADAPTER


def test_provider_block_reason_fails_open_on_no_rows() -> None:
    """An extension registered the provider; its models arrive later (#77)."""

    assert rm.provider_block_reason([], {"openai-completions"}) == ""


def test_provider_block_reason_fails_open_when_no_adapters_registered() -> None:
    """Matches ``partition_runnable``: an empty api set means "can't tell"."""

    assert rm.provider_block_reason([_model(api="anything")], set()) == ""


def test_provider_block_reason_fails_open_when_one_row_runs() -> None:
    apis = {"openai-completions"}
    rows = [_model(api="nope"), _model(api="openai-completions")]
    assert rm.provider_block_reason(rows, apis) == ""


def test_provider_block_reason_reports_mixed_rather_than_picking_a_winner() -> None:
    apis = {"openai-completions"}
    rows = [_model(api="nope"), _model(api="openai-completions", base_url="")]
    assert rm.provider_block_reason(rows, apis) == rm.BLOCKED_MIXED
    # Assert on the ROWS, not on the sentinel: the previous version of this test
    # ended with ``assert not rm.is_recoverable_block(rm.BLOCKED_MIXED)``, a
    # tautology about a module constant that never touched ``rows`` and so passed
    # whatever ``provider_block_reason`` returned.
    assert not rm.is_recoverable_block(rm.provider_block_reasons(rows, apis))


def test_provider_block_reasons_keeps_what_the_summary_discards() -> None:
    """The lossless form: ``mixed`` names neither reason it stands for."""

    apis = {"openai-completions"}
    rows = [
        _model(api="openai-completions", base_url="https://x/{EXT_ACCOUNT_ID}/v1"),
        _model(api="openai-completions", base_url=""),
    ]
    assert rm.provider_block_reasons(rows, apis) == frozenset(
        {rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_HOST}
    )
    assert rm.provider_block_reason(rows, apis) == rm.BLOCKED_MIXED
    # The sentinel is a summary OF the set, never a member of it.
    assert rm.BLOCKED_MIXED not in rm.provider_block_reasons(rows, apis)


def test_provider_block_reasons_fails_open_the_same_three_ways() -> None:
    apis = {"openai-completions"}
    assert rm.provider_block_reasons([], apis) == frozenset()
    assert rm.provider_block_reasons([_model(api="anything")], set()) == frozenset()
    assert (
        rm.provider_block_reasons(
            [_model(api="nope"), _model(api="openai-completions")], apis
        )
        == frozenset()
    )


def test_recoverable_split_matches_the_reason_ids() -> None:
    assert rm.is_recoverable_block(rm.BLOCKED_CONFIG_MISSING)
    assert rm.is_recoverable_block(rm.BLOCKED_VERTEX_CONFIG)
    assert rm.is_recoverable_block(rm.BLOCKED_NO_HOST)
    assert not rm.is_recoverable_block(rm.BLOCKED_NO_ADAPTER)
    assert not rm.is_recoverable_block(rm.BLOCKED_UNRESOLVED_API)


def test_a_set_of_reasons_is_recoverable_only_when_all_of_them_are() -> None:
    """Two different fixable reasons are still fixable — the #151 round-2 defect.

    ``is_recoverable_block`` used to take a single string and hardcoded
    :data:`BLOCKED_MIXED` to ``False`` on the docstring claim that a mixed set
    "contains at least one reason that is not recoverable". Both reasons here are
    in ``_RECOVERABLE_REASONS``, so the claim was simply false.
    """

    assert rm.is_recoverable_block({rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_HOST})
    assert rm.is_recoverable_block({rm.BLOCKED_VERTEX_CONFIG, rm.BLOCKED_NO_HOST})
    assert not rm.is_recoverable_block({rm.BLOCKED_NO_HOST, rm.BLOCKED_NO_ADAPTER})
    assert not rm.is_recoverable_block({rm.BLOCKED_NO_ADAPTER, rm.BLOCKED_UNRESOLVED_API})
    assert not rm.is_recoverable_block(frozenset())  # nothing blocked, nothing to fix


def test_config_missing_hint_names_every_placeholder_var() -> None:
    rows = [_model(api="openai-completions", base_url="https://x/{A_ID}/{B_ID}/v1")]
    assert rm.provider_block_hint(rm.BLOCKED_CONFIG_MISSING, rows) == "set A_ID, B_ID"


def test_hint_for_an_all_fixable_set_keeps_every_next_step() -> None:
    rows = [_model(api="openai-completions", base_url="https://x/{A_ID}/v1")]
    hint = rm.provider_block_hint({rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_HOST}, rows)
    assert hint == "set A_ID; no base URL configured"


def test_config_missing_hint_ignores_placeholders_of_other_reasons() -> None:
    """Found in the live TUI while verifying the mixed-reason fix.

    A ``google-vertex`` row's ``{location}`` token is filled by the SDK, not by an
    env var, and its reason is ``vertex-config-missing``. Unioning placeholders
    across EVERY row put it in the ``config-missing`` hint, and the picker offered
    ``set CLOUDFLARE_ACCOUNT_ID, location`` — there is no ``location`` to set.
    """

    apis = {"openai-completions", "google-vertex"}
    rows = [
        _model(api="openai-completions", base_url="https://x/{CLOUDFLARE_ACCOUNT_ID}/v1"),
        _model(api="google-vertex", base_url="https://{location}-aiplatform.test/v1"),
    ]
    hint = rm.provider_block_hint(rm.BLOCKED_CONFIG_MISSING, rows, apis)
    assert hint == "set CLOUDFLARE_ACCOUNT_ID"
    assert "location" not in hint
    # The whole-provider hint keeps both next steps, each naming only its own.
    both = rm.provider_block_hint(rm.provider_block_reasons(rows, apis), rows, apis)
    assert both == (
        "set CLOUDFLARE_ACCOUNT_ID; "
        "set GOOGLE_CLOUD_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION"
    )


def test_hint_for_a_mixed_set_names_the_dead_end_subset() -> None:
    """#156 at the hint level — this test's OLD assertion was the bug itself.

    ``{config-missing, no-adapter}`` is a MIXED set, and it used to require the
    hint ``"nothing it offers can run in this build"``. The ``config-missing``
    half is a next step the user can take, so the sentence was false about it,
    and the row it appeared on could have said what to do instead.
    """

    rows = [_model(api="openai-completions", base_url="https://x/{A_ID}/v1")]
    hint = rm.provider_block_hint(
        {rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_ADAPTER}, rows
    )
    assert hint != "nothing it offers can run in this build"
    assert "no adapter in this build" in hint  # the dead-end half, scoped
    assert "set A_ID" in hint  # the fixable half, still actionable


def test_hint_for_an_all_dead_end_set_still_blames_the_build() -> None:
    """The case where the old literal is TRUE, kept so the fix cannot
    over-correct into never blaming the build again."""

    rows = [_model(api="telnaut-proprietary")]
    assert (
        rm.provider_block_hint({rm.BLOCKED_NO_ADAPTER, rm.BLOCKED_UNRESOLVED_API}, rows)
        == "nothing it offers can run in this build"
    )


def test_dead_end_reasons_partitions_the_set() -> None:
    """The predicate the three states are derived from. ``is_recoverable_block``
    is deliberately NOT widened — it is compared with ``is`` at one call site
    and its docstring records a previous over-reach on this exact function."""

    assert rm.dead_end_reasons(rm.BLOCKED_CONFIG_MISSING) == frozenset()
    assert rm.dead_end_reasons(rm.BLOCKED_NO_ADAPTER) == {rm.BLOCKED_NO_ADAPTER}
    assert rm.dead_end_reasons(
        {rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_ADAPTER}
    ) == {rm.BLOCKED_NO_ADAPTER}
    assert rm.dead_end_reasons(frozenset()) == frozenset()
    # Unchanged, and load-bearing: the old predicate still answers as it did.
    assert rm.is_recoverable_block({rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_HOST})
    assert not rm.is_recoverable_block(
        {rm.BLOCKED_CONFIG_MISSING, rm.BLOCKED_NO_ADAPTER}
    )


def test_block_sample_picks_a_row_that_argues_for_the_label() -> None:
    """The panel's model must not contradict the provider-level verdict."""

    apis = {"openai-completions"}
    fixable = _model(id="fixable", api="openai-completions", base_url="")
    dead = _model(id="dead", api="telnaut-proprietary")
    rows = [fixable, dead]
    assert rm.provider_block_sample(rows, recoverable=False, apis=apis) is dead
    assert rm.provider_block_sample(rows, recoverable=True, apis=apis) is fixable
    # Falls back to the first row rather than returning nothing to show.
    assert rm.provider_block_sample([dead], recoverable=True, apis=apis) is dead
    assert rm.provider_block_sample([], recoverable=True, apis=apis) is None


# === the six, against the real catalog + real adapters =======================


def test_the_six_blocked_providers_are_exactly_these(real_adapters) -> None:
    """Re-derive the table instead of trusting it: no more, no fewer."""

    from aelix_ai.models import get_models, get_providers

    found = {
        provider: rm.provider_block_reason(get_models(provider), real_adapters)
        for provider in get_providers()
    }
    assert {p: r for p, r in found.items() if r} == _EXPECTED_BLOCKED


@pytest.mark.parametrize(("provider", "reason"), sorted(_EXPECTED_BLOCKED.items()))
def test_each_blocked_provider_is_annotated_with_its_reason(
    real_adapters, provider: str, reason: str
) -> None:
    labels, blocked = _build_provider_labels([provider], model_registry=None)
    assert blocked[provider][0] == reason
    label = labels[0]
    # The bare id stays the PREFIX so the picker's case-insensitive substring
    # filter still finds the row by typing the provider id.
    assert label.startswith(provider)
    assert label != provider
    if rm.is_recoverable_block(reason):
        assert "needs setup:" in label
    else:
        assert "unusable:" in label


def test_recoverable_annotations_name_the_env_vars(real_adapters) -> None:
    labels, _ = _build_provider_labels(
        ["cloudflare-ai-gateway", "cloudflare-workers-ai", "google-vertex"],
        model_registry=None,
    )
    assert "CLOUDFLARE_ACCOUNT_ID" in labels[0]
    assert "CLOUDFLARE_GATEWAY_ID" in labels[0]
    assert "CLOUDFLARE_ACCOUNT_ID" in labels[1]
    assert "GOOGLE_CLOUD_API_KEY" in labels[2]
    assert "GOOGLE_CLOUD_LOCATION" in labels[2]


def test_a_runnable_provider_label_is_untouched(real_adapters) -> None:
    labels, blocked = _build_provider_labels(
        ["openrouter", "anthropic", "openai"], model_registry=None
    )
    assert labels == ["openrouter", "anthropic", "openai"]
    assert blocked == {}


def test_provider_with_no_catalog_rows_is_not_annotated(real_adapters) -> None:
    """#77: an extension may register a provider whose models arrive later."""

    labels, blocked = _build_provider_labels(["telnaut"], model_registry=None)
    assert labels == ["telnaut"]
    assert blocked == {}


class _FakeRegistry:
    """A ``model_registry`` stand-in exposing only what the picker uses."""

    def __init__(self, models: list[Any], registered: tuple[str, ...] = ()) -> None:
        self._models = models
        self._registered = registered

    def get_all(self) -> list[Any]:
        return list(self._models)

    def get_registered_providers(self) -> dict[str, Any]:
        return dict.fromkeys(self._registered, object())


def test_extension_provider_is_evaluated_from_the_live_registry(real_adapters) -> None:
    """#77 + #151: evaluation happens at picker-BUILD time, against the live
    registry — an import-time constant could not see a provider an extension
    registered during startup. Here 'telnaut' exists ONLY in the registry."""

    registry = _FakeRegistry(
        [
            _model(id="telnaut-1", provider="telnaut", api="telnaut-proprietary"),
            _model(id="telnaut-2", provider="telnaut", api="telnaut-proprietary"),
        ],
        registered=("telnaut",),
    )
    labels, blocked = _build_provider_labels(["telnaut", "openrouter"], registry)
    assert blocked["telnaut"][0] == rm.BLOCKED_NO_ADAPTER
    assert labels[0] == "telnaut  (unusable: no adapter in this build)"
    # A provider the registry does not know still falls back to the bundled
    # catalog, so the built-ins keep their verdicts.
    assert labels[1] == "openrouter"


def test_extension_provider_with_a_runnable_row_is_not_annotated(real_adapters) -> None:
    registry = _FakeRegistry(
        [_model(id="c-1", provider="mycorp", api="anthropic-messages")],
        registered=("mycorp",),
    )
    labels, blocked = _build_provider_labels(["mycorp"], registry)
    assert labels == ["mycorp"]
    assert blocked == {}


def test_all_fixable_mixed_provider_reads_needs_setup(real_adapters) -> None:
    """#151 round 2: two DIFFERENT fixable reasons is still one env var away.

    ``config-missing`` and ``no-host`` are both in ``_RECOVERABLE_REASONS``, but
    together they collapse to the ``mixed`` sentinel — and the label was decided
    from that sentinel, so this provider was announced as "unusable: nothing it
    offers can run in this build" while its own detail panel named the env var to
    set. Not reachable from the bundled catalog (all six blocked providers are
    unanimous), so it takes an extension-registered provider to construct.
    """

    registry = _FakeRegistry(
        [
            _model(
                id="ext-a",
                provider="telnaut",
                api="openai-completions",
                base_url="https://gw.test/{TELNAUT_ACCOUNT_ID}/v1",
            ),
            _model(id="ext-b", provider="telnaut", api="openai-completions", base_url=""),
        ],
        registered=("telnaut",),
    )
    labels, blocked = _build_provider_labels(["telnaut"], registry)

    # The LABEL is the assertion that matters — it is what the user reads, and it
    # is what the sentinel-driven version got wrong.
    assert labels[0] == (
        "telnaut  (needs setup: set TELNAUT_ACCOUNT_ID; no base URL configured)"
    )
    assert "unusable" not in labels[0]
    entry = blocked["telnaut"]
    assert entry[0] == rm.BLOCKED_MIXED  # the summary is unchanged...
    assert entry[2] is True  # ...but the verdict no longer comes from it
    # The panel argues for the SAME conclusion the label states.
    panel = " ".join(_make_block_detail(["telnaut"], blocked)(0))
    assert "TELNAUT_ACCOUNT_ID" in panel
    assert entry[1].id == "ext-a"


def test_genuinely_mixed_provider_reads_unusable_but_names_the_subset(
    real_adapters,
) -> None:
    """#156 REWROTE THIS TEST, and its old assertion WAS the defect.

    It required the label to read exactly
    ``"telnaut  (unusable: nothing it offers can run in this build)"``. This
    provider is MIXED — ``ext-a`` is blocked by ``no-host`` (a missing
    ``baseUrl``, which the user can set) and ``ext-b`` by ``no-adapter`` (which
    they cannot) — so that sentence is false about ``ext-a``. The equivalent
    shape was disproved by measurement: a provider with 8 config-missing rows
    and 1 no-adapter row went from 0 runnable models to 8 by setting one env
    var, while this wording blamed the build.

    The VERDICT is unchanged: one unrescuable model still earns "unusable",
    an explicit #151 decision that is NOT reversed here. Only the sentence
    beside it changes, and it now says which models it is about.

    Note this test was GREEN at HEAD against the wrong wording, so a #156 fix
    could have shipped without ever touching it — the rewrite must be seen red
    first.
    """

    registry = _FakeRegistry(
        [
            _model(id="ext-a", provider="telnaut", api="openai-completions", base_url=""),
            _model(id="ext-b", provider="telnaut", api="telnaut-proprietary"),
        ],
        registered=("telnaut",),
    )
    labels, blocked = _build_provider_labels(["telnaut"], registry)

    assert labels[0].startswith("telnaut  (unusable: ")
    assert "nothing it offers can run in this build" not in labels[0]
    assert "some models" in labels[0]
    assert "no adapter in this build" in labels[0]

    entry = blocked["telnaut"]
    assert entry[0] == rm.BLOCKED_MIXED
    # The sample is still the row that CANNOT be configured away — a panel
    # reading "set an explicit baseUrl" under "unusable" would send the user
    # hunting for a knob that fixes one row and leaves the provider as dead.
    assert entry[1].id == "ext-b"
    panel = " ".join(_make_block_detail(["telnaut"], blocked)(0))
    assert "no adapter for" in panel
    assert "baseUrl" not in panel
    # Index access survives the widening — that is what the NamedTuple buys.
    assert entry[2] is False
    assert entry.recoverable is False
    assert entry.partial is True


def test_all_dead_end_provider_keeps_the_build_wording(real_adapters) -> None:
    """The honest case #156 must NOT regress.

    Two reasons, BOTH dead ends, so "nothing it offers can run in this build"
    is simply true. A fix keyed on ``len(reasons) > 1`` instead of on WHICH
    reasons are recoverable would break exactly this — the mixed set above and
    this set have the same cardinality, and only their membership differs.
    """

    registry = _FakeRegistry(
        [
            _model(id="ext-a", provider="telnaut", api="telnaut-proprietary"),
            _model(id="ext-b", provider="telnaut", api="also-not-an-api"),
        ],
        registered=("telnaut",),
    )
    labels, blocked = _build_provider_labels(["telnaut"], registry)

    assert "some models" not in labels[0]
    entry = blocked["telnaut"]
    assert entry.recoverable is False
    assert entry.partial is False


def test_no_registered_adapters_annotates_nothing(monkeypatch) -> None:
    monkeypatch.setattr("aelix_ai.api_registry.get_registered_providers", dict)
    providers = ["mistral", "amazon-bedrock", "openrouter"]
    labels, blocked = _build_provider_labels(providers, model_registry=None)
    assert labels == providers
    assert blocked == {}


def test_detail_panel_never_contradicts_the_label(real_adapters) -> None:
    """``azure-openai-responses`` rows declare BOTH an empty ``baseUrl`` and an
    api this build does not implement. ``blocked_reason`` classifies adapter-first
    (correctly: no config conjures an adapter) while ``unsupported_message``
    classifies config-first — so a detail panel built on the latter told the user
    to "set an explicit baseUrl" beside a label reading "no adapter in this
    build". Caught in the live TUI; the panel now uses ``blocked_message``.
    """

    providers = ["azure-openai-responses"]
    labels, blocked = _build_provider_labels(providers, model_registry=None)
    assert blocked["azure-openai-responses"][0] == rm.BLOCKED_NO_ADAPTER
    assert "no adapter in this build" in labels[0]
    panel = " ".join(_make_block_detail(providers, blocked)(0))  # type: ignore[misc]
    assert "no adapter" in panel
    assert "baseUrl" not in panel


def test_blocked_message_agrees_with_blocked_reason_for_every_catalog_row(
    real_adapters,
) -> None:
    """The general form of the bug above, over every blocked row in the catalog."""

    from aelix_ai.models import get_models

    checked = 0
    for provider in _EXPECTED_BLOCKED:
        for model in get_models(provider):
            reason = rm.blocked_reason(model, real_adapters)
            message = rm.blocked_message(model, real_adapters)
            assert message, f"{provider}: blocked row with no message"
            if reason == rm.BLOCKED_NO_ADAPTER:
                assert "has no adapter for" in message
            elif reason == rm.BLOCKED_CONFIG_MISSING:
                assert "environment variable(s)" in message
            elif reason == rm.BLOCKED_VERTEX_CONFIG:
                assert "Google Cloud configuration" in message
            checked += 1
    assert checked == 212  # 84 + 42 + 28 + 35 + 8 + 15


def test_blocked_message_is_empty_for_a_runnable_model() -> None:
    assert rm.blocked_message(_model(api="openai-completions"), {"openai-completions"}) == ""


def test_detail_panel_carries_the_actionable_sentence(real_adapters) -> None:
    providers = ["openrouter", "cloudflare-workers-ai"]
    _, blocked = _build_provider_labels(providers, model_registry=None)
    detail = _make_block_detail(providers, blocked)
    assert detail is not None
    assert detail(0) == []  # a runnable provider gets no panel
    text = " ".join(detail(1))
    assert "CLOUDFLARE_ACCOUNT_ID" in text
    assert _make_block_detail(providers, {}) is None


def test_detail_is_only_passed_to_a_select_that_accepts_it() -> None:
    async def two_arg(_msg: str, _options: list[str]) -> str | None:  # pragma: no cover
        return None

    async def with_detail(  # pragma: no cover
        _msg: str, _options: list[str], detail: Any = None
    ) -> str | None:
        return None

    async def kwargs_sink(*_a: Any, **_k: Any) -> str | None:  # pragma: no cover
        return None

    assert not _accepts_detail(two_arg)
    assert _accepts_detail(with_detail)
    assert _accepts_detail(kwargs_sink)


# === the trap: the stored id must be the RAW id ==============================


class _FakeAuth:
    def __init__(self) -> None:
        self.stored: dict[str, str] = {}

    async def set_api_key(self, provider: str, key: str) -> None:
        self.stored[provider] = key

    async def get_auth_status(self, provider: str) -> Any:
        return types.SimpleNamespace(source="apiKey")


async def _login_picking(label_predicate) -> tuple[_FakeAuth, list[str], list[str]]:
    """Drive ``run_login`` down the API-key path, choosing the matching LABEL."""

    auth = _FakeAuth()
    offered: list[str] = []
    committed: list[str] = []

    async def select(msg: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        if msg == "Add a provider":
            return _METHOD_API_KEY
        offered.extend(options)
        return next((o for o in options if label_predicate(o)), None)

    async def prompt_input(_msg: str, *_a: Any, **_k: Any) -> str | None:
        return "sk-test-151"

    async def confirm(*_a: Any, **_k: Any) -> bool:
        return True

    await run_login(
        auth_storage=auth,
        select=select,
        prompt_input=prompt_input,
        confirm=confirm,
        notify=lambda *_a, **_k: None,
        commit=lambda x: committed.append(str(getattr(x, "plain", x))),
    )
    return auth, offered, committed


async def test_selecting_an_annotated_provider_stores_the_raw_id(
    real_adapters,
) -> None:
    auth, offered, committed = await _login_picking(
        lambda o: o.startswith("cloudflare-workers-ai")
    )
    # The annotation was on screen before the key prompt ran...
    assert any(
        o.startswith("cloudflare-workers-ai") and "needs setup" in o for o in offered
    )
    # ...and the credential landed under the BARE id, not the label.
    assert auth.stored == {"cloudflare-workers-ai": "sk-test-151"}
    # ...and the actionable reason was committed BEFORE the key was taken.
    assert any("CLOUDFLARE_ACCOUNT_ID" in c for c in committed)
    # ...and the summary above that reason agrees with the "needs setup" label the
    # user just read. "cannot run in this build" is a claim about the BUILD, and
    # this provider's blocker is local configuration; saying it here re-merged the
    # two categories the whole annotation exists to keep apart.
    assert any("can run until it is configured" in c for c in committed)
    assert not any("in this build" in c for c in committed)


async def test_the_pre_key_summary_says_in_this_build_only_for_a_dead_end(
    real_adapters,
) -> None:
    """The reserved phrasing: ``mistral`` has no adapter, so no config fixes it."""

    _, offered, committed = await _login_picking(lambda o: o.startswith("mistral"))
    assert any(o.startswith("mistral") and "unusable" in o for o in offered)
    assert any(
        "none of its" in c and "can run in this build" in c for c in committed
    ), committed
    assert not any("until it is configured" in c for c in committed)


async def test_selecting_a_normal_provider_still_stores_the_raw_id(
    real_adapters,
) -> None:
    auth, offered, committed = await _login_picking(lambda o: o == "openrouter")
    assert "openrouter" in offered
    assert auth.stored == {"openrouter": "sk-test-151"}
    assert not any("unusable" in c or "needs setup" in c for c in committed)


async def test_an_id_answering_select_still_resolves(real_adapters) -> None:
    """Back-compat: a caller answering with the bare id (the historical
    id-as-label contract) must still store under that id, not error out."""

    auth, _, _ = await _login_picking(lambda o: o == "mistral")
    assert auth.stored == {}  # no bare 'mistral' label exists any more

    async def select(msg: str, options: list[str], *_a: Any, **_k: Any) -> str | None:
        return _METHOD_API_KEY if msg == "Add a provider" else "mistral"

    auth2 = _FakeAuth()
    await run_login(
        auth_storage=auth2,
        select=select,
        prompt_input=lambda *_a, **_k: _answer("sk-bare"),
        confirm=lambda *_a, **_k: _answer(True),
        notify=lambda *_a, **_k: None,
        commit=lambda _x: None,
    )
    assert auth2.stored == {"mistral": "sk-bare"}


async def _answer(value: Any) -> Any:
    return value
