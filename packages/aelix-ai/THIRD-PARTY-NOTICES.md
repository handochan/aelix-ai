# Third-Party Notices

Aelix is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)). This
file reproduces the license texts and copyright notices of third-party software
that Aelix is derived from, as required by those licenses. It accompanies the
`LICENSE` and `NOTICE` files in all source and binary distributions of Aelix.

## pi (earendil-works/pi)

Substantial portions of Aelix are a TypeScript-to-Python port of **pi**
(<https://github.com/earendil-works/pi>, reference commit `734e08e`): the agent
loop and harness, session and compaction machinery, provider adapters, built-in
tools, model registry and generated model catalog, CLI modes, and HTML export
are derived from the corresponding pi modules. Per-module provenance markers
(`Pi parity: <file>.ts (SHA 734e08e)`) are preserved in source docstrings.

```
MIT License

Copyright (c) 2025 Mario Zechner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## models.dev (model catalog data)

The generated model catalog
(`packages/aelix-ai/src/aelix_ai/models_generated.json`) is derived from pi's
`models.generated.ts`, which pi generates from data published by **models.dev**
(<https://github.com/sst/models.dev> / <https://models.dev>).

```
MIT License

Copyright (c) 2025 models.dev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Python package dependencies

Aelix's wheels and sdists do not vendor or bundle third-party Python packages;
dependencies are declared as installation requirements and are installed
separately by pip/uv under their own licenses. The full dependency inventory
with licenses is recorded in the CycloneDX SBOM under [`sbom/`](sbom/).

If you redistribute Aelix *together with* its resolved dependencies (for
example a frozen binary, a container image, or vendored `site-packages`), you
must additionally comply with those packages' licenses. In the current locked
set this notably includes `certifi`, `pathspec`, and parts of `tqdm` (Mozilla
Public License 2.0 — file-level copyleft: include their license texts and keep
their source availability) and `rich-pixels` (MIT; publishes no license
metadata on PyPI but ships its MIT license text in the wheel).
