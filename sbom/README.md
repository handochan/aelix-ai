# SBOM

`aelix-<version>.cdx.json` is a CycloneDX **1.6** (JSON) Software Bill of Materials
for the aelix workspace.

## Scope

- Locked runtime dependency closure from `uv.lock` (frozen, no hashes), including all
  optional extras of the workspace (tui, images) — the full user-facing install.
- Dev dependency group excluded.
- Workspace members themselves are not exported by uv; the umbrella `aelix` package
  (metadata.component) and the four first-party packages (`aelix-ai`, `aelix-agent-core`,
  `aelix-coding-agent`, `aelix-server`) are added by post-processing, all Apache-2.0.
- Every third-party component carries a license (SPDX id or expression), backfilled
  from installed package metadata; packages that publish no usable metadata are pinned
  in the `OVERRIDES` table of the generator script. Note the file-level-copyleft
  (MPL-2.0) entries: `certifi`, `pathspec`, `tqdm` — see `THIRD-PARTY-NOTICES.md`.
- Two provenance components are injected that no lockfile scanner can discover
  (neither is an installed dependency):
  - `pi (earendil-works)` (MIT, `pkg:github/earendil-works/pi@734e08e`) — substantial
    portions of aelix are a TypeScript-to-Python port of pi.
  - `models.dev (model catalog data)` (MIT) — original source of the generated model
    catalog data, via pi's `models.generated.ts`.

## Regeneration

```sh
uv run python scripts/generate_sbom.py
```

The script exports the locked closure (`uv export`), generates the base BOM with
`cyclonedx-py` (via `uvx --from cyclonedx-bom`), then applies the post-processing
described above. It strict-validates the enriched document against the CycloneDX
1.6 schema and exits non-zero — leaving the committed SBOM untouched — if any
component lacks license data or validation fails. Run it before each release
(see RELEASING.md) so the SBOM version tracks the tag; the release workflow
attaches the file to the GitHub Release.

`serialNumber` and `metadata.timestamp` are intentionally fresh on every run
(each regeneration is a new BOM document), so expect those two fields — and only
those — to churn in the diff when the dependency closure is unchanged.

Schema validation:

```sh
uvx --from cyclonedx-bom python -c "
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
err = JsonStrictValidator(SchemaVersion.V1_6).validate_str(open('sbom/aelix-0.1.0b1.cdx.json').read())
print('FAILED: %s' % err if err else 'PASSED')
"
```
