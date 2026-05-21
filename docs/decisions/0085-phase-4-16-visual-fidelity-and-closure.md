# 0085. Sprint 6h₅c Phase 4.16 — Visual fidelity + context_usage real + bootstrap session_start + factory cwd + ImageContent (A 단계 closure)

Status: Accepted (Sprint 6h₅c / Phase 4.16 / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₅b (ADR-0083 / ADR-0084) closed the runtime callback Pi parity
subset of ADR-0082's Sprint 6h₅ carry-forward roster. ADR-0084
§"Sprint 6h₅c carry-forward" enumerated **5 remaining items** scoped to
this sprint:

- Bootstrap `session_start` emit (Pi `reason="startup"` /
  `"reload"`; factory pattern change required).
- Factory bootstrap `assertSessionCwdExists` call site (Pi `:391`).
- Pi HTML visual fidelity (CSS framework, syntax highlighting,
  responsive layout).
- `ImageContent` rendering in HTML export.
- `_get_context_usage_safe` real implementation (P-282 from ADR-0074).

(Plus 2 lower-priority items: live `session_id` read via session manager
+ Pi-source-grep verification tooling — both retained as carry-forward.)

Sprint 6h₅c closes the 5 binding items above end-to-end. W4
(code-review opus) returned **0 CRITICAL / 0 HIGH / 0 MAJOR** with
**1 MEDIUM + 3 MINOR + 1 NIT** advisory items; W5 (architect opus Pi
parity audit) returned **1 MAJOR + 4 MINOR/INFO**. Sprint 6h₅c W6
absorbs the MAJOR + MEDIUM + a load-bearing MINOR + the NIT:

- **P-374 W5 MAJOR** — `_ExtensionContext.get_context_usage` was still
  the Sprint 5a `return None` stub even after P-369 landed the async
  harness-level real impl. Extensions calling
  `ctx.getContextUsage()` continued to see :data:`None`.
- **W4 MEDIUM** — `estimate_tokens` missing explicit
  :class:`ThinkingContent` branch; the catch-all `hasattr(block,
  "text")` matched the wrong attribute (ThinkingContent has
  ``thinking``, not ``text``) so thinking blocks silently contributed
  zero tokens.
- **P-377 W5 MINOR** — `_export_html/format.py` emitted
  `class="message-image tool-image"` for tool-result images; Pi
  `template.js:909` uses `class="tool-image"` ONLY.
- **W4 NIT** — dead code in `tests/harness/test_context_usage.py`
  (`chars_tool = msg_tool.content[0]; _ = chars_tool`).

The remaining items (P-375 monkeypatch fragility, MINOR-1 f-string
assembly, MINOR-3 `harness._session` private access) carry forward to
Sprint 6h₅d as non-blocking polish.

The **Phase 4 RPC roster STAYS CLOSED** at 29 supported / 0 deferred /
29 total — Sprint 6h₅c is runtime + visual polish, no dispatch changes.

## Decision

### Pi parity decisions (P-369 ~ P-373)

- **P-369 — `session/compaction.py` helpers + `_get_context_usage_safe`
  real impl (BINDING).** Adds 4 Pi-parity helpers
  (`calculate_context_tokens` / `estimate_tokens` /
  `estimate_context_tokens` / `get_latest_compaction_entry`) ported
  from Pi `compaction.ts:135-279`. Replaces the Sprint 6h₃ stub
  `return None` in :meth:`AgentHarness._get_context_usage_safe` with
  the full Pi `getContextUsage` algorithm (Pi
  `agent-session.ts:2946-2990`):

    1. ``None`` when no model is bound, or ``context_window <= 0``.
    2. Heuristic estimate path when no session is bound
       (in-memory harness).
    3. When a session is bound, walk
       :meth:`Session.get_branch` for the latest ``compaction`` entry.
       If a compaction exists but no post-compaction assistant message
       carries positive ``usage`` tokens, return the
       :class:`ContextUsage(tokens=None, percent=None)` sentinel.
    4. Otherwise return the full
       :class:`ContextUsage(tokens, context_window, percent)` triple.

  Method becomes ``async`` because :meth:`Session.get_branch` is async
  in Aelix (Pi's ``getBranch()`` is sync); the 3 callers update with
  ``await``.

- **P-370 — Factory `assert_session_cwd_exists` call site (BINDING).**
  Adds module-level :func:`create_agent_session_runtime` async factory
  to `runtime/agent_session_runtime.py`. Mirrors Pi factory at
  `agent-session-runtime.ts:382-400`. The assertion runs against
  ``harness._session`` BEFORE the
  :class:`AgentSessionRuntime` constructor fires, matching Pi line
  ``:391``. Skipped silently when ``harness._session is None``
  (in-memory factory invocation, e.g. tests).

- **P-371 — Bootstrap `session_start(reason="startup")` (BINDING).**
  After construction, the factory emits :class:`SessionStartHookEvent`
  with ``reason="startup"`` (Pi `:326` + `:2050`). The optional
  ``session_start_event=None`` kwarg mirrors Pi's ``??`` default —
  callers wanting ``reason="reload"`` (Sprint 6h₅d carry-forward) can
  pre-build a custom event. Emit is gated on
  :meth:`ExtensionRunner.has_handlers` and raises are caught + logged
  (bootstrap MUST complete even when an extension misbehaves; matches
  :meth:`_finish_session_replacement` P-343 emit policy).

- **P-372 — `_export_html/` directory restructure (BINDING).** Deletes
  the Sprint 6h₃ minimal `_export_html.py` and ships a 3-module
  package:

    - `_export_html/__init__.py` — re-exports :func:`export_html`.
    - `_export_html/template.py` — `_THEME_CSS` constant (curated
      ~240 LOC dark theme + Pygments token classes via
      :class:`HtmlFormatter.get_style_defs(".pyg")`) +
      `_HTML_TEMPLATE` HTML5 skeleton.
    - `_export_html/format.py` — renderer pipeline: markdown-it-py
      (commonmark + table plugin + breaks) with Pygments hook for
      fenced code, role-section dispatch, content-block renderer.

  Adds `pygments>=2.18` + `markdown-it-py>=3.0` to
  `packages/aelix-coding-agent/pyproject.toml`.

- **P-373 — :class:`ImageContent` HTML rendering (BINDING).** Replaces
  the Sprint 6h₃ HTML-comment fallback with an inline base64 data URI
  ``<img>`` tag mirroring Pi `template.js:909`. Non-tool-result
  variant carries ``class="message-image"``; the tool-result variant
  carries ``class="tool-image"`` ONLY (Pi strict literal — see P-377
  below). CSS adds `.message-image { max-width: 100%; max-height:
  400px; }` + `.tool-image { max-height: 500px; }`.

### W4 / W5 audit triage (W6 closure)

- **P-374 W5 MAJOR fix — `_ExtensionContext.get_context_usage` real
  sync bridge.** The W2 implementation left the Sprint 5a
  ``return None`` stub in `_make_context` even though
  :meth:`AgentHarness._get_context_usage_safe` shipped a real
  async impl. The bridge MUST stay sync (Pi
  `ExtensionContext.getContextUsage` returns ``ContextUsage |
  undefined`` synchronously; calling sites expect sync return). W6
  fix: implements the heuristic estimate path inline — no async
  ``get_branch`` walk, no compaction sentinel. Pi's full algorithm
  runs synchronously because Pi `getBranch()` is sync; Aelix's
  async `Session.get_branch()` forces the harness-level method
  async, but the extension-context bridge stays sync via the
  estimate path. Extensions wanting the full algorithm reach for
  the async harness method directly.

- **W4 MEDIUM fix — `estimate_tokens` :class:`ThinkingContent`
  branch.** Adds explicit
  ``elif isinstance(block, ThinkingContent): chars += len(block.thinking
  or "")`` BEFORE the ``hasattr(block, "text")`` catch-all. The
  catch-all matched the wrong attribute (ThinkingContent has
  ``thinking``, not ``text``) so thinking blocks silently
  contributed zero tokens to the estimate.

- **P-377 W5 MINOR fix — strict Pi `tool-image` class literal.** Pi
  `template.js:909` uses `class="tool-image"` ONLY for tool-result
  images; W2 emitted `class="message-image tool-image"` (the
  combined string). W6 fix: when ``is_tool_result=True``, the
  class string is literally ``"tool-image"`` — matches Pi byte-for-
  byte.

- **W4 NIT fix — drop dead code in
  `tests/harness/test_context_usage.py`.** Removed the
  ``chars_tool = msg_tool.content[0]; _ = chars_tool`` lines that
  did nothing functional.

### Aelix-additive divergences (documented)

1. **Async migration of :meth:`_get_context_usage_safe`.** Pi's
   `getContextUsage` is sync because Pi's `Session.getBranch()` is
   sync. Aelix's :meth:`Session.get_branch` is async (storage layer
   does I/O), forcing the harness-level method async. All 3 callers
   updated with ``await``.

2. **Factory as module-level async function.** Pi
   :func:`createAgentSessionRuntime` is a top-level TS function;
   Aelix mirrors with a module-level ``async def`` in
   `runtime/agent_session_runtime.py`. Behavior + Pi line `:391`
   ordering preserved.

3. **Extension-context bridge sync heuristic path.** Pi's full
   algorithm runs sync inside the bridge because `getBranch()` is
   sync. Aelix's bridge uses the heuristic estimate path only
   (no async `get_branch`, no compaction sentinel) to honor the
   sync return contract Pi callers depend on. The full algorithm
   is reachable via the async harness method.

4. **HTML directory layout (Python modules).** Pi
   `coding-agent/src/core/export-html/` is 6 TS files + 2 vendored
   JS bundles totaling 3700+ LOC. Aelix ships 3 Python modules
   (`__init__.py` + `template.py` + `format.py`) totaling ~440 LOC
   for the visual-fidelity-only slice. Tool-renderer +
   ANSI-pipeline + color-derivation defer to Sprint 6h₅d.

5. **Pygments + markdown-it-py (vs Pi `marked.js` +
   `highlight.js`).** Synthesis per §L Consensus Addendum.
   Different libraries, semantically equivalent HTML output —
   class names differ (Pygments `.k`/`.s`/`.c` vs hljs
   `.hljs-keyword`/`.hljs-string`/`.hljs-comment`). Pure server-
   side rendering instead of Pi's ~3 MB vendored browser JS
   bundle.

6. **Single fixed dark theme.** Pi's luminance-based
   color-derivation math (`template.css:1-30` CSS variables
   computed from a single accent) is deferred to Sprint 6h₅d.
   Sprint 6h₅c ships a single curated dark-theme constant.

## Roster

**W0 (binding spec, P-369 ~ P-373):**

- P-369 — `session/compaction.py` helpers (4) +
  :meth:`_get_context_usage_safe` real async impl.
- P-370 — :func:`create_agent_session_runtime` factory +
  `assert_session_cwd_exists` at Pi `:391`.
- P-371 — Bootstrap `session_start(reason="startup")` emit
  (Pi `:326` + `:2050`).
- P-372 — `_export_html/` package restructure + Pygments +
  markdown-it-py dependencies.
- P-373 — :class:`ImageContent` HTML rendering (Pi
  `template.js:909`).

**W4 / W5 audit triage (P-374 ~ P-377 + W4 MEDIUM + W4 NIT — W6
closure):**

- P-374 — `_ExtensionContext.get_context_usage` real sync bridge
  via heuristic estimate path (Pi parity for return shape; Aelix-
  additive divergence for algorithm scope per §Decision above).
- W4 MEDIUM — :class:`ThinkingContent` branch in
  :func:`estimate_tokens` BEFORE the catch-all.
- P-377 — Strict Pi `tool-image` class literal (drop the combined
  `message-image tool-image` string for tool-result images).
- W4 NIT — drop dead code from `test_context_usage.py`.

**Carry-forward to Sprint 6h₅d (visual polish + Pi grep tooling):**

- ANSI → HTML pipeline (Pi `ansi-to-html.ts`).
- Tool-renderer per-tool templates (bash / read / write / edit / ls
  per Pi `tool-renderer.ts`).
- Client-side JS port (sidebar / tree navigation).
- Pi color-derivation math (luminance-based theme).
- `reload()` bootstrap emit branch (Pi `:2401` —
  `reason="reload"`).
- Pixel-perfect HTML closure pin tests.
- P-375 monkeypatch fragility in
  `tests/test_factory_assert_session_cwd.py` (replace runtime
  attribute monkeypatch with proper test seam).
- MINOR-1 f-string assembly polish in `_export_html/format.py`.
- MINOR-3 `harness._session` private-attribute reads (introduce
  read-through property or factory accessor).
- Live `session_id` read via session manager (P-291 from ADR-0074).
- Pi-source-grep verification tooling (P-286 from ADR-0074).

## Counts

| Period | SUPPORTED | DEFERRED | Total |
|---|---|---|---|
| Sprint 6h₅b (start of 6h₅c) | 29 | 0 | 29 |
| Sprint 6h₅c (this ADR) | **29** | **0** | **29** |

**RPC roster UNCHANGED.** Runtime / visual polish doesn't change the
dispatch table. Phase 4 RPC remains CLOSED — see ADR-0086 (A 단계
closure).

## Consequences

- **`_get_context_usage_safe` Pi-parity wired end-to-end.** The
  harness-level method runs the full Pi algorithm (compaction
  sentinel + post-compaction usage walk + heuristic fallback) over
  async :meth:`Session.get_branch`. The extension-context bridge
  surfaces a real sync :class:`ContextUsage` triple via the
  heuristic estimate path. The Sprint 5a `return None` stub is gone
  end-to-end.

- **Bootstrap `session_start(reason="startup")` Pi-parity emit
  lands.** Extensions that registered a `session_start` handler now
  observe a startup emit at runtime construction time, matching Pi
  `:326` + `:2050`. The `reload` branch (Pi `:2401`) is deferred
  to Sprint 6h₅d when Aelix grows a `reload()` primitive.

- **Factory `assertSessionCwdExists` at Pi `:391` wired.** The
  cwd-on-disk assertion runs BEFORE
  :class:`AgentSessionRuntime` construction, so a stored-cwd-missing
  condition fails LOUD at bootstrap rather than later when the wire
  layer reads through to the missing path.

- **HTML visual fidelity ships.** Sessions now render with
  Pygments-highlighted fenced code, markdown-it markdown (headings,
  lists, tables, blockquotes), a curated dark theme + inline image
  data URIs. The Sprint 6h₃ minimal renderer (no syntax highlight,
  no markdown, no theme) is fully replaced.

- **`ImageContent` HTML rendering Pi-shape wired.** Images embedded
  in messages render as inline base64 `<img>` tags with the
  Pi-shape `data:{mime};base64,{data}` URI. The tool-result variant
  uses the strict `class="tool-image"` literal per Pi
  `template.js:909` (P-377 W5 MINOR fix).

- **`ThinkingContent` contributes to token estimates.** Pi
  `compaction.ts:232-279` treats every content block uniformly;
  Aelix's missing-branch bug silently dropped thinking blocks. With
  the W4 MEDIUM fix, thinking tokens are accounted for in the same
  4-chars-per-token Pi heuristic.

- **5 ADR-0084 carry-forward items CLOSE.** ADR-0086 (A 단계 closure
  sibling) records the closure ledger.

- **Phase 4 RPC STAYS CLOSED at 29 / 0 / 29.** No new commands; no
  dispatch impact.

## References

- `packages/agent/src/core/agent-session.ts:2946-2990` (Pi
  `getContextUsage` — P-369 source)
- `packages/agent/src/harness/compaction/compaction.ts:135-279`
  (Pi compaction helpers — P-369 source)
- `packages/agent/src/core/agent-session-runtime.ts:382-400` (Pi
  `createAgentSessionRuntime` factory — P-370 + P-371 source)
- `packages/agent/src/core/agent-session-runtime.ts:391` (Pi factory
  `assertSessionCwdExists` site — P-370 source)
- `packages/agent/src/core/agent-session-runtime.ts:326` (Pi
  startup `session_start` emit — P-371 source)
- `packages/agent/src/core/agent-session-runtime.ts:2050` (Pi
  startup `session_start` payload — P-371 source)
- `packages/coding-agent/src/core/export-html/` (Pi HTML emitter
  package — P-372 source)
- `packages/coding-agent/src/core/export-html/template.js:909` (Pi
  `ImageContent` ↔ `tool-image` strict literal — P-373 + P-377
  source)
- `aelix-agent-core/src/aelix_agent_core/session/compaction.py`
  (AMEND — 4 new helpers + ThinkingContent branch in
  `estimate_tokens`)
- `aelix-agent-core/src/aelix_agent_core/harness/core.py`
  (AMEND — `_get_context_usage_safe` async real impl + 3 caller
  ``await`` updates + `_ExtensionContext.get_context_usage` real
  sync bridge P-374)
- `aelix-agent-core/src/aelix_agent_core/runtime/agent_session_runtime.py`
  (AMEND — :func:`create_agent_session_runtime` module-level async
  factory + bootstrap session_start emit)
- `aelix-agent-core/src/aelix_agent_core/runtime/__init__.py`
  (AMEND — re-export :func:`create_agent_session_runtime`)
- `aelix-coding-agent/src/aelix_coding_agent/_export_html/`
  (NEW — 3-module package replacing single-file Sprint 6h₃
  renderer)
- `aelix-coding-agent/src/aelix_coding_agent/_export_html/__init__.py`
  (NEW — re-export :func:`export_html`)
- `aelix-coding-agent/src/aelix_coding_agent/_export_html/template.py`
  (NEW — `_THEME_CSS` + `_HTML_TEMPLATE`)
- `aelix-coding-agent/src/aelix_coding_agent/_export_html/format.py`
  (NEW — renderer pipeline with markdown-it + Pygments + Pi-strict
  `tool-image` literal)
- `aelix-coding-agent/pyproject.toml` (AMEND — +Pygments +
  markdown-it-py dependencies)
- `tests/harness/test_context_usage.py` (NEW — 9 tests including
  ThinkingContent branch + `_ExtensionContext.get_context_usage`
  real-bridge tests + Pi-shape helper assertions)
- `tests/test_factory_assert_session_cwd.py` (NEW — 3 tests:
  cwd-assertion fires BEFORE construction + skips when no session +
  uses harness session for cwd)
- `tests/test_bootstrap_session_start.py` (NEW — 5 tests: factory
  emits with reason=startup + custom event override + skip-when-no-
  handlers + replacement uses reason=new/resume regression +
  bootstrap runs after construction)
- `tests/test_export_html_visual_fidelity.py` (NEW — 7 tests:
  base64 img tag + Pi-strict tool-image class + XSS-safe escape +
  markdown paragraph + Pygments token classes + unknown-lang
  fallback + theme CSS includes Pygments styles)

## Related

- ADR-0034 — Pi pin (amended Sprint 6h₅c row this sprint).
- ADR-0073 — Sprint 6h₃ session stats + HTML export wire port
  (Sprint 6h₅c HTML visual fidelity replaces the minimal Sprint
  6h₃ renderer).
- ADR-0074 — Sprint 6h₃ Phase 4.10 strict-superset closure
  (Sprint 6h₅c closes the P-280 / P-282 / P-283 visual-fidelity
  + context-usage carry-forward items enumerated there).
- ADR-0081 / ADR-0082 — Sprint 6h₅a extension event Pi parity
  (Sprint 6h₅c closes the factory-bootstrap cwd assertion
  carry-forward from ADR-0082).
- ADR-0083 / ADR-0084 — Sprint 6h₅b runtime callback Pi parity
  (Sprint 6h₅c closes 5 of the carry-forward items enumerated
  in ADR-0084 §"Sprint 6h₅c carry-forward").
- ADR-0086 — Sprint 6h₅c sibling ADR — **A 단계 closure**
  recording the full delivery ledger for Phase 4 RPC + extension
  events + runtime callbacks + visual fidelity.
- ADR-0029 — Pi parity acceptance test harness (closure-pin lane).
- ADR-0032 — Sprint workflow + W4/W5 audit mandatory gate.

## Phase

Sprint 6h₅c / Phase 4.16 / W6 (shipped — visual fidelity +
context_usage real + bootstrap session_start + factory cwd +
ImageContent ALL CLOSED; Phase 4 RPC roster STAYS CLOSED;
**A 단계 CLOSED** — see ADR-0086).
