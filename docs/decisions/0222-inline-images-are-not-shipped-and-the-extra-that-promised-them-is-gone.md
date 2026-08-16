# 0222. Inline images are not shipped, and the extra that promised them is gone

Status: Accepted (2026-08-16).
Date: 2026-08-16
Supersedes in part: **ADR-0106 §D** (Sprint 6h₁₀c, "Inline images") — the descriptor-renderer
half of that ADR is untouched.
Relates: ADR-0107 §C (the pyte snapshots that exercised the renderer), ADR-0148 (which recorded
`Provides-Extra: tui/images` as wheel-verified — the `images` half of that observation no longer
holds).
GitHub: #163.
Pi: **no citation offered.** The local pi copy under this workspace is a partial fetch with no
TUI sources, so whether pi renders inline images was not read. This ADR makes no parity or
divergence claim; it records what aelix ships. Same stance, same reason, as ADR-0219 / ADR-0221.

## Context

`tui/images.py` was built and specified in ADR-0106 §D, shipped behind an installable
`[images]` extra, and advertised in three places. Nothing in production ever called it.

```
$ grep -rn "render_image\|ImageCapability\|detect_image_capability" \
      --include=*.py packages/*/src/ | grep -v tui/images.py
(no output)
```

Its entire consumer set was two test files — `tests/tui/test_images.py` and the §2.3 section of
`tests/tui/test_snapshots.py`. There was no `importlib` path either.

### Why this was more than dead code

Dead code is a maintenance cost. This was a **published promise** with a price attached:

| surface | claim |
|---|---|
| `pyproject.toml` | `images = ["aelix-coding-agent[tui,images]==0.1.0b1"]` — a top-level installable extra |
| `packages/aelix-coding-agent/pyproject.toml` | the `[images]` extra, pulling `rich-pixels` |
| `README.md` / `README.ko.md` | "`tui,images` adds inline terminal image rendering" |
| `install.sh` | `AELIX_EXTRAS` … "Use `tui,images` for inline image rendering" |
| `packages/aelix-coding-agent/README.md` | `pip install 'aelix-coding-agent[images]'  # inline image rendering` |

A user could run `AELIX_EXTRAS=tui,images`, pay the dependency install, and get **no behaviour
change whatsoever**. ADR-0106 documented a component in full detail — capability precedence, the
degradation ladder, the dormant `term-image` constraint — and never recorded a call site.

### The two image paths are different, and only one was inert

Worth stating because the names collide:

- **Images TO the model** — `util/image_resize.py`, called by the `read` tool
  (`tools/read.py:25,140`) and `cli/file_processor.py`. **Live, unchanged, and the reason the
  `Pillow>=11` core pin stays.**
- **Images FROM the terminal** — `tui/images.py`. Never called. Removed here.

## Decision

**Remove it, and record that inline images are not a shipped feature.** The owner chose removal
over wiring (#163 offered both).

Removed: `tui/images.py`; `tests/tui/test_images.py`; the §2.3 snapshots; the `[images]` extra in
both pyprojects; `rich-pixels` from the dev group, the lockfile, the SBOM and the five
`THIRD-PARTY-NOTICES.md` copies; and the claim from `README.md`, `README.ko.md`, the
coding-agent README, `install.sh` and `RELEASING.md`.

**The absence is asserted, not merely achieved.** `tests/test_release_version_consistency.py`
now asserts `"images" not in extras`, so re-adding the extra without a call site fails a test
rather than shipping as another inert promise. Verified end to end on the built wheel:
`Provides-Extra: tui` only, and no `tui/images.py` member.

### What wiring one would have taken

Kept so option 1 stays open rather than being re-derived:

1. A trigger — a `read`-tool image result, an `@file` image, or a descriptor image cell.
2. Real `max_cells` threaded from the live terminal. ADR-0219 gave the TUI a live width reader
   (`tui/width.py::terminal_columns`); the row axis has `overlay._modal_cap` as precedent. This
   was unanswerable before: "how are inline images sized?" had no answer because
   `images.py:170/182` took `max_cells` from callers that did not exist.
3. A live-terminal QA note, since neither tier is observable from a headless suite.
4. The ADR-0106 constraint still holds: `term-image` hard-caps `Pillow<11` against this
   workspace's `Pillow>=11` core pin, so **only the `rich-pixels` Unicode tier was ever
   reachable** — the Kitty/iTerm2 graphics tier was dormant from the day it was written.

## Consequences

- `AELIX_EXTRAS=tui,images` is now an install error rather than a silent no-op that costs a
  dependency. That is the intended outcome: a name that does nothing is worse than a name that
  is absent.
- The `0.1.0-beta.1` CHANGELOG section had not shipped, so its inline-image line was corrected
  rather than recorded as a removal; the `[Unreleased]` section carries the removal for anyone
  tracking `main`.
- One dependency leaves the runtime closure (`rich-pixels`), and with it an MPL-adjacent
  redistribution note that no longer applies.

## Known leftover, stated rather than hidden

`SettingsManager.get_show_images()` / `set_show_images()` and `TerminalSettings.show_images`
survive with **no production consumer** — the same class of defect, one layer down. They are
retained deliberately, not overlooked:

- It is a settings-schema field, and removing one is a schema change with its own migration
  question, not a code cleanup.
- Its provenance is unresolved for the reason given at the top: pi's TUI was not read, so
  whether `terminal.showImages` is a parity surface is unknown, and deleting a possible parity
  field on a guess is exactly what ADR-0218 was written to stop.
- Unlike the extra, it is **not advertised**: it does not appear in `/settings`
  (`tui/settings_rows.py` carries `block_images`, which *is* wired, and not `show_images`), so
  it is not a promise made to a user — only a key that can be set with no effect.

If inline images are never wired, that field should go with the next settings-schema pass.
