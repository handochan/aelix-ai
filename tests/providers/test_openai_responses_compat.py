"""Tests for ``OpenAIResponsesCompat`` / ``get_responses_compat`` (pi parity).

Pi parity: ``getCompat`` in ``packages/ai/src/api/openai-responses.ts``
(lines 58-64) @ 927e98068cda. Distinct from the completions compat
shape; all three flags default to ``True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aelix_ai.providers._openai_responses_compat import (
    OpenAIResponsesCompat,
    get_responses_compat,
)


@dataclass
class _FakeModel:
    compat: Any = None


def test_dataclass_defaults_all_true() -> None:
    c = OpenAIResponsesCompat()
    assert c.supports_developer_role is True
    assert c.send_session_id_header is True
    assert c.supports_long_cache_retention is True


def test_dataclass_is_frozen() -> None:
    c = OpenAIResponsesCompat()
    try:
        c.supports_developer_role = False  # type: ignore[misc]
    except Exception as exc:  # FrozenInstanceError
        assert type(exc).__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("expected frozen dataclass")


def test_no_compat_returns_baseline() -> None:
    c = get_responses_compat(_FakeModel(compat=None))
    assert c == OpenAIResponsesCompat()


def test_camelcase_override() -> None:
    c = get_responses_compat(
        _FakeModel(
            compat={
                "supportsDeveloperRole": False,
                "sendSessionIdHeader": False,
                "supportsLongCacheRetention": False,
            }
        )
    )
    assert c.supports_developer_role is False
    assert c.send_session_id_header is False
    assert c.supports_long_cache_retention is False


def test_snake_case_override() -> None:
    c = get_responses_compat(
        _FakeModel(compat={"supports_developer_role": False})
    )
    assert c.supports_developer_role is False
    # untouched flags keep the True baseline
    assert c.send_session_id_header is True
    assert c.supports_long_cache_retention is True


def test_partial_override_keeps_baseline_for_missing_keys() -> None:
    c = get_responses_compat(_FakeModel(compat={"sendSessionIdHeader": False}))
    assert c.send_session_id_header is False
    assert c.supports_developer_role is True
    assert c.supports_long_cache_retention is True


def test_none_value_in_dict_falls_back_to_true() -> None:
    # Mirrors pi's ``?? true`` — an explicit null/None defers to default.
    c = get_responses_compat(_FakeModel(compat={"supportsDeveloperRole": None}))
    assert c.supports_developer_role is True


def test_snake_case_takes_precedence_over_camel() -> None:
    c = get_responses_compat(
        _FakeModel(
            compat={
                "supports_developer_role": False,
                "supportsDeveloperRole": True,
            }
        )
    )
    assert c.supports_developer_role is False


def test_object_style_override() -> None:
    @dataclass
    class _Compat:
        supports_developer_role: bool = False
        send_session_id_header: bool = True
        supports_long_cache_retention: bool = True

    c = get_responses_compat(_FakeModel(compat=_Compat()))
    assert c.supports_developer_role is False
    assert c.send_session_id_header is True


def test_model_without_compat_attr() -> None:
    class _Bare:
        pass

    c = get_responses_compat(_Bare())  # type: ignore[arg-type]
    assert c == OpenAIResponsesCompat()
