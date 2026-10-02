"""Unit tests for base-URL placeholder expansion (pi cloudflare-auth parity)."""

from __future__ import annotations

import pytest
from aelix_ai.providers._base_url import (
    expand_base_url,
    has_unexpanded_placeholders,
    unexpanded_placeholder_names,
)

_CF = (
    "https://gateway.ai.cloudflare.com/v1/"
    "{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/openai"
)


def test_expand_returns_input_when_no_placeholder() -> None:
    assert expand_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert expand_base_url(None) is None
    assert expand_base_url("") == ""


def test_expand_substitutes_set_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    assert expand_base_url(_CF) == (
        "https://gateway.ai.cloudflare.com/v1/acct-123/gw-456/openai"
    )
    assert not has_unexpanded_placeholders(_CF)
    assert unexpanded_placeholder_names(_CF) == []


def test_unset_env_var_leaves_token_and_flags_unexpanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    expanded = expand_base_url(_CF)
    assert "{CLOUDFLARE_ACCOUNT_ID}" in expanded
    assert "gw-456" in expanded
    assert has_unexpanded_placeholders(_CF)
    assert unexpanded_placeholder_names(_CF) == ["CLOUDFLARE_ACCOUNT_ID"]


def test_empty_env_var_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    assert has_unexpanded_placeholders(_CF)
    assert "CLOUDFLARE_ACCOUNT_ID" in unexpanded_placeholder_names(_CF)


# --- #362, Codex's third cross-review (C3): a .env value never addresses a request ---

_TENANT = "https://{TENANT_KEY}.owner-gateway.invalid/v1"


def test_a_dotenv_supplied_name_does_not_fill_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TENANT_KEY`` from a cwd ``.env`` (in the record): the token stays, and the caller can say why.

    On 5e983992 the ``/`` and ``#`` in ``repo-chosen.invalid/v1#`` erased the
    template's fixed host suffix: host ``repo-chosen.invalid``.
    """

    from aelix_ai.providers._base_url import dotenv_withheld_placeholder_names

    monkeypatch.setenv("TENANT_KEY", "repo-chosen.invalid/v1#")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY,TENANT_KEY")
    assert expand_base_url(_TENANT) == _TENANT
    assert unexpanded_placeholder_names(_TENANT) == ["TENANT_KEY"]
    assert dotenv_withheld_placeholder_names(_TENANT) == ["TENANT_KEY"]


def test_the_same_value_exported_fills_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not in the record: the user chose the value, as before."""

    from aelix_ai.providers._base_url import dotenv_withheld_placeholder_names

    monkeypatch.setenv("TENANT_KEY", "repo-chosen.invalid/v1#")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    assert expand_base_url(_TENANT) == "https://repo-chosen.invalid/v1#.owner-gateway.invalid/v1"
    assert unexpanded_placeholder_names(_TENANT) == []
    assert dotenv_withheld_placeholder_names(_TENANT) == []


def test_cloudflare_ids_from_a_dotenv_still_fill_the_catalogue_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0203's template configuration, admitted with a value-shape rule: expanded."""

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    monkeypatch.setenv(
        "AELIX_DOTENV_ADMITTED", "CLOUDFLARE_ACCOUNT_ID,CLOUDFLARE_API_KEY,CLOUDFLARE_GATEWAY_ID"
    )
    assert expand_base_url(_CF) == "https://gateway.ai.cloudflare.com/v1/acct-123/gw-456/openai"


def test_every_template_name_has_a_value_shape_rule_and_covers_the_catalogue() -> None:
    """``DOTENV_TEMPLATE_NAMES`` is exactly ADR-0203's templated configuration.

    Each name has a value-shape rule in the ``.env`` loader's configuration arm
    (so its value cannot carry ``/``, ``#`` or ``..``), and every upper-case
    ``{TOKEN}`` in the shipped catalogue is one of them (a new templated
    provider whose ids come from a ``.env`` would otherwise be hidden).
    """

    import re

    from aelix_ai.dotenv_record import DOTENV_ADMITTED_ENV, DOTENV_TEMPLATE_NAMES
    from aelix_ai.models_generated import MODELS
    from aelix_coding_agent.cli.runtime_bootstrap import _DOTENV_CONFIG_VALUES
    from aelix_coding_agent.core.dotenv_provenance import (
        DOTENV_ADMITTED_ENV as CODING_AGENT_RECORD,
    )

    assert DOTENV_ADMITTED_ENV == CODING_AGENT_RECORD == "AELIX_DOTENV_ADMITTED"
    assert set(_DOTENV_CONFIG_VALUES).issuperset(DOTENV_TEMPLATE_NAMES)
    for name in DOTENV_TEMPLATE_NAMES:
        assert not _DOTENV_CONFIG_VALUES[name].shape.match("x/../y#")
    token_re = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    tokens = {
        name
        for models in MODELS.values()
        for m in models.values()
        for name in token_re.findall(getattr(m, "base_url", "") or "")
        if name.isupper()
    }
    assert tokens == DOTENV_TEMPLATE_NAMES


# --- #362, verify round 6: a .env value never fills the authority, even a Cloudflare id ---

_CF_RECORD = "CLOUDFLARE_ACCOUNT_ID,CLOUDFLARE_API_KEY,CLOUDFLARE_GATEWAY_ID"


@pytest.mark.parametrize(
    "template",
    [
        "https://{CLOUDFLARE_ACCOUNT_ID}.owner-gateway.invalid/v1",  # a host label
        "https://{CLOUDFLARE_ACCOUNT_ID}/v1",  # the whole host
        "https://gw.owner.invalid:{CLOUDFLARE_ACCOUNT_ID}/v1",  # the port
        "https://{CLOUDFLARE_ACCOUNT_ID}@gw.owner.invalid/v1",  # user-info
        "https://{CLOUDFLARE_ACCOUNT_ID}",  # an authority with no path after it
        "{CLOUDFLARE_ACCOUNT_ID}/v1",  # no scheme: no path a .env value may fill
    ],
    ids=["host-label", "whole-host", "port", "userinfo", "no-path", "no-scheme"],
)
def test_a_dotenv_cloudflare_id_never_fills_the_authority(
    monkeypatch: pytest.MonkeyPatch, template: str
) -> None:
    """The shape rule keeps the id to one plain label; the position rule keeps it out of the host.

    Measured on f5731ae5 (the verify round on 6): a user's own
    ``https://{CLOUDFLARE_ACCOUNT_ID}.owner-gateway.invalid/v1`` with a ``.env``
    ``CLOUDFLARE_ACCOUNT_ID=repochosen`` reached ``repochosen.owner-gateway.invalid``.
    """

    from aelix_ai.providers._base_url import dotenv_withheld_placeholder_names

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "repochosen")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    assert expand_base_url(template) == template
    assert unexpanded_placeholder_names(template) == ["CLOUDFLARE_ACCOUNT_ID"]
    assert dotenv_withheld_placeholder_names(template) == ["CLOUDFLARE_ACCOUNT_ID"]


def test_a_dotenv_cloudflare_id_still_fills_a_path_token_of_your_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below a host the user's template fixes, as the catalogue's templates put it."""

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    template = "https://gw.owner.invalid/accounts/{CLOUDFLARE_ACCOUNT_ID}/v1"
    assert expand_base_url(template) == "https://gw.owner.invalid/accounts/acct-123/v1"
    assert unexpanded_placeholder_names(template) == []


def test_an_exported_value_fills_the_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not in the record: the user chose it, wherever their template puts it."""

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "mytenant")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    template = "https://{CLOUDFLARE_ACCOUNT_ID}.owner-gateway.invalid/v1"
    assert expand_base_url(template) == "https://mytenant.owner-gateway.invalid/v1"


def test_the_not_runnable_reason_says_export_for_an_authority_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``runnable_models`` names the variable to EXPORT, not merely to set."""

    from aelix_coding_agent.core.runnable_models import _dotenv_withheld

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "repochosen")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    assert _dotenv_withheld("CLOUDFLARE_ACCOUNT_ID")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "OPENROUTER_API_KEY")
    assert not _dotenv_withheld("CLOUDFLARE_ACCOUNT_ID")


# --- #362, Codex's fourth cross-review: the path is a span, and the value is re-checked ---


@pytest.mark.parametrize(
    "template",
    [
        "https://owner.invalid?next=/{CLOUDFLARE_ACCOUNT_ID}",  # no path: a query
        "https://owner.invalid#/{CLOUDFLARE_ACCOUNT_ID}",  # no path: a fragment
        "https://owner.invalid/v1?tenant={CLOUDFLARE_ACCOUNT_ID}",  # after the path
        "https://owner.invalid/v1#{CLOUDFLARE_ACCOUNT_ID}",  # after the path
        # Codex's fifth cross-review: the path ends at the FIRST ? or # (a
        # mutant taking the last one filled this query).
        "https://owner.invalid/v1?tenant={CLOUDFLARE_ACCOUNT_ID}#tail",
    ],
    ids=["query-no-path", "fragment-no-path", "query", "fragment", "query-then-fragment"],
)
def test_a_dotenv_value_fills_only_the_path_span(
    monkeypatch: pytest.MonkeyPatch, template: str
) -> None:
    """F3: a ``/`` inside a query or fragment is not the path; the path ends at ``?``/``#``."""

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct1")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    assert expand_base_url(template) == template
    assert unexpanded_placeholder_names(template) == ["CLOUDFLARE_ACCOUNT_ID"]


def test_a_path_token_before_a_query_still_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct1")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    template = "https://owner.invalid/accounts/{CLOUDFLARE_ACCOUNT_ID}/v1?x=1"
    assert expand_base_url(template) == "https://owner.invalid/accounts/acct1/v1?x=1"


@pytest.mark.parametrize(
    "value",
    # " acct1 ": a mutant that shape-checked the stripped value filled the untrimmed one.
    ["acct?capture=repo", "a/b", "a#b", "..", "x" * 65, "a.b", " acct1 "],
)
def test_the_reader_rechecks_a_template_value_however_it_was_admitted(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """F2: on Windows a hatched ``Cloudflare_Account_ID`` skipped the loader's shape check.

    The reader does not trust the admission path: a ``.env``-supplied template
    name fills a path token only with a plain-id value.
    """

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", value)
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw1")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    expanded = expand_base_url(_CF)
    assert "{CLOUDFLARE_ACCOUNT_ID}" in expanded and value not in expanded
    assert unexpanded_placeholder_names(_CF) == ["CLOUDFLARE_ACCOUNT_ID"]


def test_the_reader_shape_is_the_loaders_shape() -> None:
    """``dotenv_record.TEMPLATE_VALUE_SHAPE`` is ADR-0203's ``_CF_ID``, character for character."""

    from aelix_ai.dotenv_record import DOTENV_TEMPLATE_NAMES, TEMPLATE_VALUE_SHAPE
    from aelix_coding_agent.cli.runtime_bootstrap import _CF_ID, _DOTENV_CONFIG_VALUES

    assert TEMPLATE_VALUE_SHAPE.pattern == _CF_ID.pattern
    for name in DOTENV_TEMPLATE_NAMES:
        assert _DOTENV_CONFIG_VALUES[name].shape.pattern == _CF_ID.pattern


def test_a_plain_id_from_a_non_template_dotenv_name_still_fills_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``DOTENV_TEMPLATE_NAMES`` may fill: a ``.env`` ``TENANT_KEY`` with a VALID shape.

    The C3 rows used a value the shape check refuses anyway, so dropping the
    template-name condition kept every test green (the independent check of the
    fifth pass's fixes).
    """

    monkeypatch.setenv("TENANT_KEY", "repochosen")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", "TENANT_KEY")
    template = "https://owner.invalid/accounts/{TENANT_KEY}/v1"
    assert expand_base_url(template) == template
    assert unexpanded_placeholder_names(template) == ["TENANT_KEY"]


@pytest.mark.parametrize(
    ("template", "fills"),
    [
        ("h.invalid/v1?next=https://x/{CLOUDFLARE_ACCOUNT_ID}", False),  # :// in a query
        (" https://owner.invalid/v1/{CLOUDFLARE_ACCOUNT_ID}", False),  # a leading space
        ("my_scheme://owner.invalid/v1/{CLOUDFLARE_ACCOUNT_ID}", False),  # '_' is no scheme char
        ("2http://owner.invalid/v1/{CLOUDFLARE_ACCOUNT_ID}", False),  # must start with a letter
        ("HTTPS://owner.invalid/v1/{CLOUDFLARE_ACCOUNT_ID}", True),
        ("git+https://owner.invalid/v1/{CLOUDFLARE_ACCOUNT_ID}", True),
    ],
    ids=["query", "leading-space", "underscore", "digit-first", "upper", "plus"],
)
def test_a_scheme_must_open_the_template(
    monkeypatch: pytest.MonkeyPatch, template: str, fills: bool
) -> None:
    """``scheme://`` must OPEN the template, in RFC 3986 scheme characters, for a ``.env`` value to fill.

    A ``://`` inside a query is not a scheme; a looser pattern (leading space,
    any characters before ``://``) kept every earlier test green (the
    independent re-check of ``6b0607e3``).
    """

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct1")
    monkeypatch.setenv("AELIX_DOTENV_ADMITTED", _CF_RECORD)
    if fills:
        assert expand_base_url(template) == template.replace("{CLOUDFLARE_ACCOUNT_ID}", "acct1")
    else:
        assert expand_base_url(template) == template
        assert unexpanded_placeholder_names(template) == ["CLOUDFLARE_ACCOUNT_ID"]
