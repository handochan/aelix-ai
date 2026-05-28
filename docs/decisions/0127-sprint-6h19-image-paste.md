# 0127. Sprint 6h₁₉ — Ctrl+V paste-image (pi-faithful port)

Status: Accepted (6h₁₉ shipped)
Date: 2026-05-28
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

## Context

Audit MEDIUM #4 — pi's `Ctrl+V` reads a clipboard image, writes it to a temp file,
and inserts the absolute path at the cursor (`interactive-mode.ts:2430-2450
handleClipboardImagePaste`). Aelix lacked the binding. This sprint ports it
non-invasively: PIL is already a dep (`util/image_resize.py:36 from PIL import
Image, ImageOps`), so no new dependency.

## Decision (all non-protected `aelix-coding-agent`)

- `tui/chrome.py` — new `on_image_paste` callback + `@kb.add("c-v")` binding that
  fires it. Idle and mid-turn both work (no `running` gate — pi doesn't gate
  either).
- `tui/shell.py::_paste_image()`:
  - Lazy-imports `PIL.{Image, ImageGrab}`.
  - Calls `ImageGrab.grabclipboard()` — platform dispatch (Win/Mac native;
    Linux uses xclip/wl-paste under the hood).
  - `isinstance(grabbed, Image.Image)` narrows the `Image | list[str] | None`
    return: file-list paste is out of v1 scope (pi also handles only the Image
    branch in `handleClipboardImagePaste`); `None` → silent.
  - Saves to `tempfile.gettempdir()/aelix-clipboard-<uuid4>.png` as PNG
    (matches pi's `?? "png"` fallback for unknown MIMEs; PNG is lossless +
    universal — simpler than pi's MIME-derived extension and equivalent for
    common-case paste).
  - Inserts the bare absolute path at the cursor via `chrome.paste_to_editor`
    (pi `interactive-mode.ts:2445`: `editor.insertTextAtCursor(filePath)`).
  - Outer try/except swallows all errors → silent no-op on clipboard
    unavailability, missing platform tools (xclip/wl-paste), or write failure.
    Matches pi `interactive-mode.ts:2447-2449` outer try/catch.
- `tui/commands.py` — `_HOTKEYS` row: `Ctrl+V → Paste a clipboard image
  (inserts the temp-file path)`.

## pi divergences (documented)

- **Filename**: `aelix-clipboard-<uuid>.png` vs pi's `pi-clipboard-<uuid>.<ext>`.
  Product-name swap + always-PNG. The always-PNG simplification matches pi's
  documented `?? "png"` fallback path; pi's MIME-derived extension is a
  nice-to-have that doesn't change behavior for round-trippable images.
- **Cleanup**: pi never cleans up (audit-confirmed). Aelix matches — files
  accumulate in `os.tmpdir()`; OS temp-eviction policy applies.
- **Windows alt+v binding**: pi uses `alt+v` on Windows due to a terminal
  conflict. v1 Aelix binds `c-v` everywhere; Windows users can override via
  descriptor key-rebind if needed.

## Consequences

- ruff clean; pyright 0 errors.
- Tests:
  - `tests/tui/test_chrome.py::test_ctrl_v_fires_image_paste` — Ctrl+V keypress
    fires `on_image_paste`.
  - `tests/tui/test_run_tui_smoke.py::test_run_tui_ctrl_v_pastes_clipboard_image_path_to_editor`
    — full integration: stubs `ImageGrab.grabclipboard()` with a real tiny
    `PIL.Image.new("RGB",(4,4))`, drives Ctrl+V through the real
    `_paste_image` path, asserts a real PNG was written to `os.tmpdir()` and
    the absolute path was inserted into the editor.
  - `tests/tui/test_run_tui_smoke.py::test_run_tui_ctrl_v_silent_noop_when_clipboard_has_no_image`
    — `grabclipboard() → None` → no editor change, no error.
  - `tests/tui/test_commands.py::test_hotkeys_lists_shortcuts` — updated to
    assert the new Ctrl+V row.
- Protected core (`packages/aelix-agent-core`, `docs/contracts`) byte-unchanged.

## Code review (separate lane) — APPROVE-WITH-NITS → applied

`code-reviewer`: 0 CRITICAL / 0 HIGH; pi port fidelity confirmed (filename
template, always-PNG fallback path, bare-path insertion, silent no-op
behavior); protected core untouched. Two MEDIUM nits fixed:

- **[M1]** Windows path-separator: replaced `f"{tempfile.gettempdir()}/..."`
  with `os.path.join(...)` so the inserted path is platform-correct on
  Windows (no mixed `\`/`/` separators reaching the model).
- **[M2]** Test global-mutation risk: switched both smoke tests to pytest's
  `monkeypatch` fixture (auto-restores even if the test body raises before
  reaching teardown — the prior try/finally could leak the global on
  exceptions inside the `async with`).

LOW nits (consciously left as pi-parity-defensible): nested try/except style
(each block has its own pi-citation comment), no PNG size cap (pi matches),
lazy imports (PIL lazy is justified for headless-tk degradation), no
diagnostic on missing xclip/wl-paste (pi also silent).

## Live verification (deferred)

Real clipboard image testing requires an attached display + an image on the
clipboard, which the Codespace headless environment lacks. The integration
test stubs `grabclipboard()` with a real PIL Image and exercises the full
save → path-insert chain — equivalent end-to-end coverage. Manual desktop
verification by users (Mac/Linux) is the appropriate complement.
