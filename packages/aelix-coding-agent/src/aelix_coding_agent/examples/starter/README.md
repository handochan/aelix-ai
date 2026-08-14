# Aelix starter extension

A minimal, **buildable** extension you can copy to publish your own. One slash
command (`/hello`) and one theme (`example`), shipped with a manifest that
actually binds.

```
starter/
  pyproject.toml            # hatchling build + the aelix.extensions entry point
  aelix_starter/
    __init__.py             # setup(aelix) — the entry point's callable
    aelix-plugin.toml       # the manifest (MUST ship in the wheel)
    themes/example.toml     # a contributed theme (MUST ship in the wheel)
```

## Build it and prove the manifest ships

```console
$ pip wheel . -w dist --no-deps
$ python -m zipfile -l dist/aelix_starter_ext-0.1.0-py3-none-any.whl
```

The listing MUST contain `aelix_starter/aelix-plugin.toml` and
`aelix_starter/themes/example.toml`. If it does not, the host has nothing to
read from your installed metadata and every declaration is inert.

## Prove it binds after install

```console
$ pip install dist/aelix_starter_ext-0.1.0-py3-none-any.whl
$ aelix extension verify aelix-starter
  BOUND     aelix-starter [aelix-starter-ext 0.1.0]  (manifest: plugin 'aelix-starter')

verify: all 1 endpoint(s) BOUND.
```

Exit code 0 means the manifest bound. A non-zero exit with an `ABSENT` line and
a `setuptools / package-data` hint means the manifest was dropped from the wheel
— see [the packaging guide](../../docs/extension-authoring.md#packaging-your-extension)
(`aelix docs extension`).

## Why hatchling

hatchling ships every file inside the package directory by default, data files
included, so the manifest and theme ride along with no extra configuration. A
setuptools build with default configuration packages `*.py` and **drops**
`aelix-plugin.toml` and `themes/*.toml` — the pack installs and `setup()` runs,
but the manifest is gone. If you must use setuptools, opt the data files in
explicitly (`include_package_data = true` + a `MANIFEST.in`, or
`[tool.setuptools.package-data]`).
