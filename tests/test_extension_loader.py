"""Tests for the extension loader (extensions/loader.py).

Covers spec E test_extension_loader.py cases: inline factory, module path,
file path, async setup, error collection, ordering, and runtime sharing (D.1.7).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from aelix.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime
from aelix.extensions.loader import (
    load_extension_from_factory,
    load_extensions,
)

# === Inline factory ===


async def test_load_inline_factory_returns_extension() -> None:
    def factory(aelix: ExtensionAPI) -> None:
        aelix.register_flag("verbose", type="bool", default=False)

    result = await load_extensions([factory])

    assert len(result.extensions) == 1
    assert len(result.errors) == 0
    ext = result.extensions[0]
    assert "verbose" in ext.flags


async def test_load_inline_factory_name_derived_from_qualname() -> None:
    def my_factory(aelix: ExtensionAPI) -> None:
        pass

    result = await load_extensions([my_factory])
    assert len(result.extensions) == 1
    # Name is set from the factory's __qualname__.
    assert "my_factory" in result.extensions[0].name


# === Module path ===


async def test_load_module_path_without_setup_reports_error_and_class_factory_works() -> None:
    """Loading a module with no bare 'setup' reports an error; the class factory works directly."""
    # PolicyExtension instances are factories — the module exports PolicyExtension class,
    # not a bare `setup` function. Per D.1.8, if setup is absent but an attribute
    # matching ExtensionFactory exists, it's treated correctly.
    # The loader looks for 'setup'; aelix.builtin.policy does NOT have a bare setup().
    # What it does have is PolicyExtension which is a callable factory class.
    # So we test loading it as a module path raises AttributeError (no bare setup),
    # which the loader collects as an error — this verifies the module loading path works.
    result = await load_extensions(["aelix.builtin.policy"])
    # aelix.builtin.policy has no top-level 'setup' function; error is expected.
    assert len(result.errors) == 1
    assert "aelix.builtin.policy" in result.errors[0].path

    # Now verify that importing the class and calling it as a factory works correctly.
    from aelix.builtin.policy import PolicyExtension

    result2 = await load_extensions([PolicyExtension()])
    assert len(result2.extensions) == 1
    assert len(result2.errors) == 0


async def test_load_module_path_invalid_module_reported_as_error() -> None:
    result = await load_extensions(["aelix.does_not_exist_module"])
    assert len(result.errors) == 1
    assert "aelix.does_not_exist_module" in result.errors[0].path


# === File path ===


async def test_load_file_path_executes_setup(tmp_path: Path) -> None:
    ext_file = tmp_path / "my_ext.py"
    ext_file.write_text(
        textwrap.dedent("""\
        def setup(aelix):
            aelix.register_flag("my_flag", type="str", default="hello")
        """)
    )

    result = await load_extensions([ext_file])
    assert len(result.extensions) == 1
    assert len(result.errors) == 0
    ext = result.extensions[0]
    assert "my_flag" in ext.flags
    assert ext.flags["my_flag"].default == "hello"


async def test_load_file_path_string_ending_py_executed(tmp_path: Path) -> None:
    ext_file = tmp_path / "str_ext.py"
    ext_file.write_text(
        textwrap.dedent("""\
        def setup(aelix):
            aelix.register_flag("str_flag", type="bool", default=True)
        """)
    )

    result = await load_extensions([str(ext_file)])
    assert len(result.extensions) == 1
    assert "str_flag" in result.extensions[0].flags


async def test_load_file_path_not_found_reported_as_error(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.py"
    result = await load_extensions([missing])
    assert len(result.errors) == 1
    assert len(result.extensions) == 0


# === Async setup ===


async def test_async_setup_supported() -> None:
    async def async_factory(aelix: ExtensionAPI) -> None:
        aelix.register_flag("async_flag", type="bool", default=True)

    result = await load_extensions([async_factory])
    assert len(result.extensions) == 1
    assert len(result.errors) == 0
    assert "async_flag" in result.extensions[0].flags


async def test_async_setup_from_file(tmp_path: Path) -> None:
    ext_file = tmp_path / "async_ext.py"
    ext_file.write_text(
        textwrap.dedent("""\
        async def setup(aelix):
            aelix.register_flag("async_file_flag", type="str", default="world")
        """)
    )

    result = await load_extensions([ext_file])
    assert len(result.extensions) == 1
    assert "async_file_flag" in result.extensions[0].flags


# === Error collection — factory raises ===


async def test_factory_raising_collected_as_error_not_thrown() -> None:
    def bad_factory(aelix: ExtensionAPI) -> None:
        raise RuntimeError("setup exploded")

    result = await load_extensions([bad_factory])
    assert len(result.extensions) == 0
    assert len(result.errors) == 1
    assert "setup exploded" in result.errors[0].error


async def test_factory_missing_setup_reported_as_error() -> None:
    """Module with no top-level callable 'setup' is reported as error."""
    result = await load_extensions(["aelix.builtin.policy"])
    assert len(result.errors) == 1
    assert "setup" in result.errors[0].error.lower() or "no" in result.errors[0].error.lower()


# === Order and resilience ===


async def test_load_multiple_extensions_preserves_order() -> None:
    order: list[str] = []

    def factory_a(aelix: ExtensionAPI) -> None:
        order.append("a")
        aelix.register_flag("flag_a", type="bool", default=False)

    def factory_b(aelix: ExtensionAPI) -> None:
        order.append("b")
        aelix.register_flag("flag_b", type="bool", default=True)

    def factory_c(aelix: ExtensionAPI) -> None:
        order.append("c")
        aelix.register_flag("flag_c", type="str", default="x")

    result = await load_extensions([factory_a, factory_b, factory_c])
    assert len(result.extensions) == 3
    assert order == ["a", "b", "c"]
    # Extensions are in the same order as the input list.
    assert "flag_a" in result.extensions[0].flags
    assert "flag_b" in result.extensions[1].flags
    assert "flag_c" in result.extensions[2].flags


async def test_load_continues_on_per_extension_error() -> None:
    def good_a(aelix: ExtensionAPI) -> None:
        aelix.register_flag("good_a", type="bool", default=False)

    def bad(aelix: ExtensionAPI) -> None:
        raise ValueError("bad extension")

    def good_b(aelix: ExtensionAPI) -> None:
        aelix.register_flag("good_b", type="bool", default=True)

    result = await load_extensions([good_a, bad, good_b])
    assert len(result.extensions) == 2
    assert len(result.errors) == 1
    assert "bad extension" in result.errors[0].error
    assert "good_a" in result.extensions[0].flags
    assert "good_b" in result.extensions[1].flags


# === One runtime per call (D.1.7) ===


async def test_loader_creates_one_runtime_per_call() -> None:
    """All extensions loaded in a single call share the same runtime instance."""
    runtimes_seen: list[_ExtensionRuntime] = []

    def factory_a(aelix: ExtensionAPI) -> None:
        runtimes_seen.append(aelix.runtime)

    def factory_b(aelix: ExtensionAPI) -> None:
        runtimes_seen.append(aelix.runtime)

    result = await load_extensions([factory_a, factory_b])
    assert len(result.extensions) == 2
    assert len(runtimes_seen) == 2
    # Both factories received the same runtime instance.
    assert runtimes_seen[0] is runtimes_seen[1]
    # The result runtime is the same object.
    assert result.runtime is runtimes_seen[0]


async def test_two_separate_load_calls_have_independent_runtimes() -> None:
    """Separate load_extensions calls produce separate runtimes."""
    runtimes: list[_ExtensionRuntime] = []

    def factory(aelix: ExtensionAPI) -> None:
        runtimes.append(aelix.runtime)

    result_a = await load_extensions([factory])
    result_b = await load_extensions([factory])

    assert result_a.runtime is not result_b.runtime


# === load_extension_from_factory helper ===


async def test_load_extension_from_factory_returns_single_extension() -> None:
    def factory(aelix: ExtensionAPI) -> None:
        aelix.register_flag("single", type="str", default="yes")

    ext = await load_extension_from_factory(factory, name="my_ext")
    assert isinstance(ext, Extension)
    assert ext.name == "my_ext"
    assert "single" in ext.flags


async def test_load_extension_from_factory_async_supported() -> None:
    async def async_factory(aelix: ExtensionAPI) -> None:
        aelix.register_flag("async_single", type="bool", default=True)

    ext = await load_extension_from_factory(async_factory)
    assert "async_single" in ext.flags
