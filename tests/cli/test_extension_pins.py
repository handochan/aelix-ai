"""Issue #64 (ADR-0187) — unit tests for the pure pin-store + decision module.

No pip, no network: exercises hashing, sidecar load/save round-trips (including
forward-compat unknown-key preservation), artifact discovery, version parsing,
and the tofi/strict decision primitives directly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from aelix_coding_agent.cli import extension_pins as ep

# === sha256_file =========================================================


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    f = tmp_path / "artifact.whl"
    f.write_bytes(b"some bytes to hash")
    assert ep.sha256_file(f) == hashlib.sha256(b"some bytes to hash").hexdigest()


def test_sha256_file_large_streaming(tmp_path: Path) -> None:
    # > one 64 KiB chunk to exercise the streaming loop.
    data = b"x" * (65536 * 3 + 7)
    f = tmp_path / "big.tar.gz"
    f.write_bytes(data)
    assert ep.sha256_file(f) == hashlib.sha256(data).hexdigest()


# === pins_file_path / load / save round-trip =============================


def test_pins_file_path_under_agent_dir(tmp_path: Path) -> None:
    assert ep.pins_file_path(tmp_path) == tmp_path / ep.PINS_FILENAME


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert ep.load_pins(tmp_path / "nope.json") == {}


def test_load_bad_json_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "extension_pins.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert ep.load_pins(p) == {}


def test_load_non_dict_root_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "extension_pins.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert ep.load_pins(p) == {}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    p = ep.pins_file_path(tmp_path)
    pin = ep.Pin(
        identity="/abs/ext.whl",
        kind="path",
        mode="tofi",
        sha256="a" * 64,
        pinned_at="2026-07-05T00:00:00+00:00",
    )
    ep.save_pins({pin.identity: pin}, p)
    loaded = ep.load_pins(p)
    assert loaded == {pin.identity: pin}
    # Schema version is written.
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == ep.SCHEMA_VERSION


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "agent"
    p = ep.pins_file_path(nested)
    ep.save_pins({"id": ep.Pin(identity="id", kind="pypi", mode="tofi")}, p)
    assert p.is_file()


def test_save_atomic_leaves_no_temp(tmp_path: Path) -> None:
    p = ep.pins_file_path(tmp_path)
    ep.save_pins({"id": ep.Pin(identity="id", kind="pypi", mode="tofi")}, p)
    leftovers = [x.name for x in tmp_path.iterdir() if x.name.startswith(".extension_pins.")]
    assert leftovers == []


def test_unknown_keys_preserved_on_round_trip(tmp_path: Path) -> None:
    # Forward-compat: a newer schema's extra fields (e.g. an Approach-B key
    # record) must survive a read/write by an older build, not be dropped.
    p = tmp_path / "extension_pins.json"
    p.write_text(
        json.dumps(
            {
                "version": 99,
                "pins": {
                    "id-x": {
                        "kind": "pypi",
                        "mode": "tofi",
                        "sha256": "b" * 64,
                        "futureField": {"nested": 1},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = ep.load_pins(p)
    assert loaded["id-x"].extra == {"futureField": {"nested": 1}}
    ep.save_pins(loaded, p)
    reloaded = json.loads(p.read_text(encoding="utf-8"))
    assert reloaded["pins"]["id-x"]["futureField"] == {"nested": 1}


def test_pin_to_json_omits_none_fields() -> None:
    pin = ep.Pin(identity="id", kind="git", mode="tofi", git_sha="c" * 40)
    body = pin.to_json()
    assert body == {"kind": "git", "mode": "tofi", "gitSha": "c" * 40}
    assert "sha256" not in body  # None fields are not serialized


# === find_top_level_artifact =============================================


def test_find_prefers_wheel_over_sdist(tmp_path: Path) -> None:
    (tmp_path / "some-pkg-1.2.tar.gz").write_bytes(b"s")
    (tmp_path / "some_pkg-1.2-py3-none-any.whl").write_bytes(b"w")
    found = ep.find_top_level_artifact(tmp_path, "some-pkg")
    assert found is not None and found.name.endswith(".whl")


def test_find_canonicalizes_name(tmp_path: Path) -> None:
    # A wheel's normalized ``my_ext`` must match a canonical ``my-ext``.
    (tmp_path / "my_ext-2.0-py3-none-any.whl").write_bytes(b"w")
    found = ep.find_top_level_artifact(tmp_path, "my-ext")
    assert found is not None


def test_find_ignores_other_packages(tmp_path: Path) -> None:
    (tmp_path / "other_pkg-1.0-py3-none-any.whl").write_bytes(b"w")
    assert ep.find_top_level_artifact(tmp_path, "some-pkg") is None


def test_find_exact_name_not_prefix(tmp_path: Path) -> None:
    # A prefix-colliding dependency (jupyter_core for target jupyter) must NOT be
    # mistaken for the top-level artifact (MED review finding).
    (tmp_path / "jupyter_core-5.0-py3-none-any.whl").write_bytes(b"dep")
    assert ep.find_top_level_artifact(tmp_path, "jupyter") is None
    (tmp_path / "jupyter-1.0.tar.gz").write_bytes(b"real")
    found = ep.find_top_level_artifact(tmp_path, "jupyter")
    assert found is not None and found.name == "jupyter-1.0.tar.gz"


def test_find_ambiguous_wheels_is_none(tmp_path: Path) -> None:
    # Two exact-name platform wheels — refuse rather than guess which pip installs.
    (tmp_path / "some_pkg-1.2-py3-none-macosx_11_0_arm64.whl").write_bytes(b"a")
    (tmp_path / "some_pkg-1.2-py3-none-manylinux1_x86_64.whl").write_bytes(b"b")
    assert ep.find_top_level_artifact(tmp_path, "some-pkg") is None


def test_find_sdist_when_no_wheel(tmp_path: Path) -> None:
    (tmp_path / "some-pkg-1.2.tar.gz").write_bytes(b"s")
    found = ep.find_top_level_artifact(tmp_path, "some-pkg")
    assert found is not None and found.name.endswith(".tar.gz")


def test_find_missing_dir_is_none(tmp_path: Path) -> None:
    assert ep.find_top_level_artifact(tmp_path / "nope", "some-pkg") is None


def test_canonicalize_name_pep503() -> None:
    assert ep.canonicalize_name("Some.Pkg") == "some-pkg"
    assert ep.canonicalize_name("some_pkg") == "some-pkg"
    assert ep.canonicalize_name("some--pkg__x") == "some-pkg-x"


# === version_from_artifact ===============================================


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("some_pkg-1.2-py3-none-any.whl", "1.2"),
        ("some-pkg-1.2.3.tar.gz", "1.2.3"),
        ("some_pkg-0.1.0-cp312-cp312-linux_x86_64.whl", "0.1.0"),
        ("other-9.9.whl", None),  # wrong package (too few wheel components too)
    ],
)
def test_version_from_artifact(filename: str, expected: str | None) -> None:
    assert ep.version_from_artifact(filename, "some-pkg") == expected


# === decide_generic (path artifact / git SHA) ============================


def test_decide_generic_first_tofi_records() -> None:
    d = ep.decide_generic(None, "sha1", mode="tofi", repin=False, label="x")
    assert d.record is True


def test_decide_generic_first_strict_refuses() -> None:
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_generic(None, "sha1", mode="strict", repin=False, label="x")


def test_decide_generic_first_strict_repin_records() -> None:
    d = ep.decide_generic(None, "sha1", mode="strict", repin=True, label="x")
    assert d.record is True


def test_decide_generic_match_no_record() -> None:
    existing = ep.Pin(identity="x", kind="path", mode="tofi", sha256="sha1")
    d = ep.decide_generic(existing, "sha1", mode="tofi", repin=False, label="x")
    assert d.record is False


def test_decide_generic_mismatch_refuses() -> None:
    existing = ep.Pin(identity="x", kind="path", mode="tofi", sha256="sha1")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_generic(existing, "sha2", mode="tofi", repin=False, label="x")


def test_decide_generic_mismatch_repin_records() -> None:
    existing = ep.Pin(identity="x", kind="path", mode="tofi", sha256="sha1")
    d = ep.decide_generic(existing, "sha2", mode="tofi", repin=True, label="x")
    assert d.record is True


def test_decide_generic_git_field() -> None:
    existing = ep.Pin(identity="x", kind="git", mode="tofi", git_sha="abc")
    d = ep.decide_generic(
        existing, "abc", mode="tofi", repin=False, label="x", field_name="git_sha"
    )
    assert d.record is False


# === decide_pypi (version-aware) =========================================


def test_decide_pypi_first_tofi_records() -> None:
    d = ep.decide_pypi(None, "sha1", "1.0", mode="tofi", repin=False, label="p")
    assert d.record is True


def test_decide_pypi_same_version_match_no_record() -> None:
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    d = ep.decide_pypi(existing, "sha1", "1.0", mode="tofi", repin=False, label="p")
    assert d.record is False


def test_decide_pypi_same_version_tamper_refuses() -> None:
    # SAME version but different bytes is the attack signal.
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(existing, "sha2", "1.0", mode="tofi", repin=False, label="p")


def test_decide_pypi_same_version_tamper_repin_records() -> None:
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    d = ep.decide_pypi(existing, "sha2", "1.0", mode="tofi", repin=True, label="p")
    assert d.record is True


def test_decide_pypi_version_bump_tofi_records() -> None:
    # A legitimate upgrade re-pins under tofi.
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    d = ep.decide_pypi(existing, "sha2", "2.0", mode="tofi", repin=False, label="p")
    assert d.record is True


def test_decide_pypi_version_bump_strict_refuses() -> None:
    existing = ep.Pin(identity="p", kind="pypi", mode="strict", version="1.0", sha256="sha1")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(existing, "sha2", "2.0", mode="strict", repin=False, label="p")


def test_decide_pypi_first_strict_refuses() -> None:
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(None, "sha1", "1.0", mode="strict", repin=False, label="p")


def test_decide_pypi_unknown_version_match_ok() -> None:
    # observed_version None → byte comparison; matching bytes verify.
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    d = ep.decide_pypi(existing, "sha1", None, mode="tofi", repin=False, label="p")
    assert d.record is False


def test_decide_pypi_unknown_version_mismatch_refuses() -> None:
    # observed_version None + changed bytes must REFUSE, not silently re-pin.
    existing = ep.Pin(identity="p", kind="pypi", mode="tofi", version="1.0", sha256="sha1")
    with pytest.raises(ep.VerifyRefusal):
        ep.decide_pypi(existing, "sha2", None, mode="tofi", repin=False, label="p")
