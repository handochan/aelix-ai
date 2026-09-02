# Sprint 6h₅c — Phase 4.16 BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

A 단계 closure — wire 5 items from ADR-0084 carry-forward. NO new RPC commands. SUPPORTED 29 / DEFERRED 0 / total 29 unchanged.

---

## §0 — W0 findings (P-369 ~ P-373)

### P-369 — `_get_context_usage_safe` (Pi `agent-session.ts:2946-2990`)
- Aelix stub at `harness/core.py:1657-1670` returns None unconditionally
- Pi helpers MISSING in Aelix: `calculate_context_tokens` / `estimate_context_tokens` / `estimate_tokens` / `get_latest_compaction_entry`
- `Usage` payload is `dict[str, Any]` (defensive `.get()` access)
- `Model.context_window: int = 0` default
- **Aelix `Session.get_branch()` is async** — Pi `getBranch()` is sync. Method MUST become async.

### P-370 — Factory `assert_session_cwd_exists` (Pi `agent-session-runtime.ts:382-400` line `:391`)
- Aelix has NO standalone factory; `AgentSessionRuntime.__init__` accepts pre-built harness
- **Decision:** Add module-level `async def create_agent_session_runtime(...)` mirroring Pi factory. Assertion BEFORE construction.

### P-371 — Bootstrap `session_start(reason="startup")` (Pi `:326, :2050, :2401`)
- Aelix emits `session_start` ONLY on replacement (`_finish_session_replacement`); NO bootstrap emit
- `SessionStartHookEvent` already accepts `reason="startup"` (harness/hooks.py:727)
- **Decision:** Emit inside the new `create_agent_session_runtime` factory after construction. Mirror Pi `??` default via optional `session_start_event` kwarg.
- Reload path (Pi `:2401`) — DEFERRED to 6h₅d (no Aelix `reload()` method yet).

### P-372 — HTML visual fidelity
- Current Aelix `_export_html.py:1-183` minimal renderer (no syntax highlight, no markdown, no theme)
- Pi `coding-agent/src/core/export-html/` is 3700+ LOC across 6 files + 2 vendored JS bundles
- **Decision:** Restructure to `_export_html/` directory with `template.py` (CSS + skeleton) + `format.py` (renderer). Add Pygments + markdown-it-py deps. DEFER ANSI pipeline, tool-renderer, sidebar/tree, color-derivation to 6h₅d.

### P-373 — `ImageContent` HTML rendering (Pi `template.js:909`)
- Current Aelix emits HTML comment `<!-- image: ... -->`
- **Decision:** Pi-shape `<img src="data:{mime};base64,{data}" class="message-image" />` + 4 CSS rules.

---

## §A — Scope LOC table (~1,035 LOC)

| Slice | File | LOC |
|---|---|---|
| Compaction helpers port | `session/compaction.py` | +60 |
| `_get_context_usage_safe` real impl + async migration | `harness/core.py` | +50 |
| Factory `create_agent_session_runtime` + bootstrap emit | `runtime/agent_session_runtime.py` | +50 |
| HTML directory `__init__.py` + `template.py` + `format.py` | NEW `_export_html/` | +440 (net -183 delete) |
| Dependency additions | `pyproject.toml` | +2 |
| **Prod subtotal** | | **~605 net** |
| Tests | 4 NEW test files | ~430 |
| **Total** | | **~1,035** |

### NOT in scope (carry-forward to Sprint 6h₅d)
- ANSI → HTML pipeline (Pi `ansi-to-html.ts`)
- Tool-renderer per-tool templates (Pi `tool-renderer.ts`)
- Client-side JS port (sidebar/tree navigation)
- Pi color-derivation math (luminance-based theme)
- `reload()` bootstrap emit branch

---

## §B — `_get_context_usage_safe` real impl + helpers

### B.1 — `session/compaction.py` (NEW helpers, ~60 LOC)
Append below existing module:

```python
def calculate_context_tokens(usage: dict[str, Any] | None) -> int:
    """Pi parity: compaction.ts:135-137."""
    if usage is None:
        return 0
    total = usage.get("total_tokens") or usage.get("totalTokens") or 0
    if total:
        return int(total)
    return int(
        (usage.get("input_tokens") or usage.get("input") or 0)
        + (usage.get("output_tokens") or usage.get("output") or 0)
        + (usage.get("cache_read") or usage.get("cacheRead") or 0)
        + (usage.get("cache_write") or usage.get("cacheWrite") or 0)
    )


def estimate_tokens(message: Any) -> int:
    """Pi parity: compaction.ts:232-279. chars/4 heuristic; image=4800 chars."""
    from aelix_ai.messages import ImageContent, TextContent, ToolCallContent
    chars = 0
    content = getattr(message, "content", None)
    if isinstance(content, str):
        chars = len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, TextContent):
                chars += len(block.text or "")
            elif isinstance(block, ImageContent):
                chars += 4800  # Pi line :264
            elif isinstance(block, ToolCallContent):
                import json as _json
                chars += len(_json.dumps(block.input or {}))
                chars += len(block.tool_name or "")
            elif hasattr(block, "text"):
                chars += len(getattr(block, "text", "") or "")
    return chars // 4


@dataclass(frozen=True)
class _EstimateResult:
    tokens: int


def estimate_context_tokens(messages: list[Any]) -> _EstimateResult:
    """Pi parity: compaction.ts:186-214."""
    from aelix_ai.messages import AssistantMessage
    last_idx: int | None = None
    last_usage: dict[str, Any] | None = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AssistantMessage):
            stop = getattr(msg, "stop_reason", None)
            if stop in ("aborted", "error"):
                continue
            last_idx = i
            last_usage = getattr(msg, "usage", None)
            break
    if last_idx is None:
        return _EstimateResult(tokens=sum(estimate_tokens(m) for m in messages))
    usage_tokens = calculate_context_tokens(last_usage)
    trailing = sum(estimate_tokens(m) for m in messages[last_idx + 1:])
    return _EstimateResult(tokens=usage_tokens + trailing)


def get_latest_compaction_entry(branch_entries: list[Any]) -> Any | None:
    """Pi parity: walk entries reverse for type=='compaction'."""
    for entry in reversed(branch_entries):
        if getattr(entry, "type", None) == "compaction":
            return entry
    return None
```

Add 4 names to `__all__`.

### B.2 — `_get_context_usage_safe` rename to async + real impl
File: `harness/core.py:1657-1670` — rename method to `async def _get_context_usage_safe(self)` and update ALL callers (grep `_get_context_usage_safe` and add `await`):

```python
async def _get_context_usage_safe(self) -> Any | None:
    """Pi parity: getContextUsage (agent-session.ts:2946-2990).
    
    Sprint 6h₅c (ADR-0085 P-369) replaces Sprint 6h₃ stub.
    Async because Aelix Session.get_branch() is async (Pi sync).
    """
    from aelix_agent_core.session.compaction import (
        calculate_context_tokens, estimate_context_tokens, get_latest_compaction_entry,
    )
    from aelix_coding_agent.extensions.api import ContextUsage
    
    model = self._state.model
    if model is None:
        return None
    context_window = getattr(model, "context_window", 0) or 0
    if context_window <= 0:
        return None
    
    if self._session is None:
        estimate = estimate_context_tokens(self._state.messages)
        percent = (estimate.tokens / context_window) * 100
        return ContextUsage(tokens=estimate.tokens, context_window=context_window, percent=percent)
    
    branch_entries = await self._session.get_branch()
    latest_compaction = get_latest_compaction_entry(branch_entries)
    
    if latest_compaction is not None:
        compaction_idx = branch_entries.index(latest_compaction)
        has_post_compaction_usage = False
        for i in range(len(branch_entries) - 1, compaction_idx, -1):
            entry = branch_entries[i]
            if getattr(entry, "type", None) != "message":
                continue
            msg = getattr(entry, "message", None)
            if msg is None or getattr(msg, "role", None) != "assistant":
                continue
            stop = getattr(msg, "stop_reason", None)
            if stop in ("aborted", "error"):
                continue
            ctx_tokens = calculate_context_tokens(getattr(msg, "usage", None))
            if ctx_tokens > 0:
                has_post_compaction_usage = True
            break
        if not has_post_compaction_usage:
            return ContextUsage(tokens=None, context_window=context_window, percent=None)
    
    estimate = estimate_context_tokens(self._state.messages)
    percent = (estimate.tokens / context_window) * 100
    return ContextUsage(tokens=estimate.tokens, context_window=context_window, percent=percent)
```

---

## §C — Factory `create_agent_session_runtime` (P-370 + P-371)

File: `runtime/agent_session_runtime.py` — add module-level async factory below class:

```python
async def create_agent_session_runtime(
    harness: AgentHarness,
    create_harness: HarnessFactory,
    *,
    repo: JsonlSessionRepo,
    fs: FileSystem,
    diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
    model_fallback_message: str | None = None,
    session_start_event: Any | None = None,
) -> AgentSessionRuntime:
    """Pi parity: createAgentSessionRuntime (agent-session-runtime.ts:382-400).
    
    Sprint 6h₅c (ADR-0085 P-370+P-371):
    - Pi :391 — assert_session_cwd_exists BEFORE constructing runtime.
    - Pi :326 + :2050 — emit session_start(reason="startup") after construction.
      Mirror Pi `??` default via session_start_event=None sentinel.
    """
    from aelix_agent_core.session.session_cwd import assert_session_cwd_exists
    from aelix_agent_core.harness.hooks import SessionStartHookEvent
    
    # P-370 — Pi line :391
    if harness._session is not None:
        await assert_session_cwd_exists(harness._session, fallback_cwd=None, fs=fs)
    
    runtime = AgentSessionRuntime(
        harness, create_harness,
        repo=repo, fs=fs,
        diagnostics=diagnostics,
        model_fallback_message=model_fallback_message,
    )
    
    # P-371 — Pi :326 + :2050
    event = session_start_event or SessionStartHookEvent(
        type="session_start", reason="startup", previous_session_file=None,
    )
    runner = harness.extension_runner
    if runner.has_handlers("session_start"):
        try:
            await runner.emit(event)
        except Exception:
            _log.exception("create_agent_session_runtime.session_start emit raised")
    
    return runtime
```

Add to `__all__`.

---

## §E — HTML visual fidelity (P-372)

### E.1 — Restructure
DELETE `_export_html.py` → CREATE `_export_html/` directory:
- `__init__.py` (~10 LOC) re-export `export_html`
- `template.py` (~250 LOC) — `_THEME_CSS` + `_HTML_TEMPLATE` constants
- `format.py` (~180 LOC) — renderer pipeline with markdown + pygments

### E.2 — `format.py` core
```python
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound

_HTML_FMT = HtmlFormatter(cssclass="pyg", nowrap=True)

def _highlight(code: str, lang: str, attrs) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return f'<pre class="pyg"><code>{highlight(code, lexer, _HTML_FMT)}</code></pre>'

_MD = MarkdownIt("commonmark", {"breaks": True, "html": False, "highlight": _highlight}).enable("table")


def _render_text_markdown(text: str) -> str:
    return _MD.render(text or "")
```

### E.3 — `template.py` `_THEME_CSS`
Port curated subset (~250 LOC) of Pi `template.css`:
- body / layout (Pi :1-50)
- section.user/assistant/tool_result (Pi :280-380)
- .role header (Pi :120-150)
- pre code + Pygments tokens (Pi :520-720)
- .message-image, .tool-image (Pi :909-930)
- blockquote, h1-h6, p, ul, ol (markdown, Pi :430-510)
- Dark theme CSS variables (Pi :1-30)

Include `HtmlFormatter(cssclass="pyg").get_style_defs(".pyg")` resolved at module import.

---

## §F — ImageContent rendering (P-373)

In `format.py::_render_content_block`:
```python
if isinstance(block, ImageContent):
    # Pi parity: template.js:909 — base64 data URI inline
    mime = html.escape(block.mime_type or "image/png")
    data = html.escape(block.data or "")
    classes = "message-image tool-image" if is_tool_result else "message-image"
    return f'<img src="data:{mime};base64,{data}" class="{classes}" />'
```

CSS in `template.py`:
```css
.message-image { max-width: 100%; max-height: 400px; border-radius: 4px; }
.tool-image    { max-height: 500px; }
```

---

## §G — Dependencies

`packages/aelix-coding-agent/pyproject.toml`:
```toml
dependencies = [
    "aelix-ai",
    "aelix-agent-core",
    "pygments>=2.18",
    "markdown-it-py>=3.0",
]
```

Run `uv sync` at executor time.

---

## §H — Tests (~430 LOC)

### H.1 `tests/harness/test_context_usage.py` (~140 LOC)
- `test_returns_none_when_no_model`
- `test_returns_none_when_context_window_zero`
- `test_returns_estimate_when_no_compaction`
- `test_short_circuits_when_compaction_has_no_post_usage`
- `test_walks_post_compaction_assistant_usage`
- `test_compaction_helpers_pi_shape` (calculate/estimate/estimate_tokens — image=4800)

### H.2 `tests/test_bootstrap_session_start.py` (~90 LOC)
- `test_factory_emits_session_start_startup`
- `test_factory_respects_custom_session_start_event`
- `test_factory_skips_emit_when_no_handlers`
- `test_replacement_emit_uses_reason_new_or_resume` (regression guard)
- `test_bootstrap_emit_runs_after_runtime_construction`

### H.3 `tests/test_factory_assert_session_cwd.py` (~60 LOC)
- `test_factory_asserts_cwd_before_construction`
- `test_factory_skips_assert_when_no_session`
- `test_factory_uses_harness_session_for_cwd`

### H.4 `tests/test_export_html_visual_fidelity.py` (~140 LOC)
- `test_image_content_renders_as_base64_img_tag`
- `test_image_inside_tool_result_uses_tool_image_class`
- `test_image_xss_safe`
- `test_markdown_renders_to_paragraph`
- `test_fenced_code_block_gets_pygments_classes`
- `test_unknown_language_falls_back_to_text_lexer`
- Update `tests/harness/test_harness_export_to_html.py` if it asserted on comment-fallback

---

## §I — ADRs

### I.1 NEW `docs/decisions/0085-phase-4-16-visual-fidelity-and-closure.md`
Title: Sprint 6h₅c — Phase 4.16: visual fidelity + context_usage + bootstrap emit + factory cwd + image
Status: Accepted
Date: 2026-05-22
Pi pin
Top-level principle echo
Decisions per P-369~P-373
Aelix-additive divergences:
1. Async migration of `_get_context_usage_safe`
2. Factory `create_agent_session_runtime` as module-level async function
3. HTML directory layout (Python modules)
4. Pygments vs highlight.js (synthesis per §L)
5. Single fixed dark theme (color-derivation deferred)
Deferred items → ADR-0086 carry-forward.

### I.2 NEW `docs/decisions/0086-sprint-6h5c-a-stage-closure.md`
Title: A 단계 closure — Phase 4 RPC + extension events + runtime callbacks COMPLETE
Status: Accepted
- Closure table — every A 단계 carry-forward item paired with delivering Sprint/ADR
- Counts unchanged 29/0/29
- Sprint 6h₅d carry-forward: ANSI / tool-renderer / color math / reload emit / pixel-perfect HTML tests

### I.3 AMEND `docs/decisions/0034-pi-reference-version-pin.md`
Append Sprint 6h₅c row.

### I.4 AMEND `docs/decisions/0084-sprint-6h5b-closure.md`
Update carry-forward — strike delivered items.

---

## §J — Atomic commit plan (EXACTLY 5)

1. **`feat: session/compaction — helpers + harness/_get_context_usage_safe real impl async (P-369)`**
   - `session/compaction.py`
   - `harness/core.py` (rename method + impl)
   - RPC handler caller updates (`rpc_mode.py` etc.)
   - `tests/harness/test_context_usage.py`

2. **`feat: runtime — create_agent_session_runtime factory + assert_session_cwd_exists at :391 (P-370)`**
   - `runtime/agent_session_runtime.py`
   - `runtime/__init__.py` re-export
   - `tests/test_factory_assert_session_cwd.py`

3. **`feat: runtime — bootstrap session_start emit reason=startup (P-371)`**
   - `runtime/agent_session_runtime.py` (factory body extended)
   - `tests/test_bootstrap_session_start.py`

4. **`feat: coding-agent/_export_html — visual fidelity + ImageContent rendering (P-372/P-373)`**
   - DELETE `_export_html.py`
   - NEW `_export_html/__init__.py` + `template.py` + `format.py`
   - `pyproject.toml` (+2 deps)
   - `tests/test_export_html_visual_fidelity.py`
   - Update `tests/harness/test_harness_export_to_html.py` if needed

5. **`docs: ADRs 0085/0086 + ADR-0034/0084 amends + A 단계 closure (P-369~P-373)`**
   - NEW ADR-0085
   - NEW ADR-0086
   - AMEND ADR-0034
   - AMEND ADR-0084
   - README

---

## §K — Verification gates

After each commit:
- pytest — 1904 + ~25 new = 1929+ pass, 0 fail
- pyright — 8 baseline preserved
- ruff — clean

Per-commit gate enforcement before next commit. Final §K manual smoke: render session with text + image + fenced code block to HTML; verify image data URI + Pygments classes visible.

---

## §L — Consensus Addendum

### Pygments + markdown-it-py vs Pi JS bundle
**Steelman:** Vendor Pi's `marked.min.js` + `highlight.min.js` for byte-identical output. Different class names mean Pi parity drift.
**Counter:** ~3MB JS bundle + browser execution requirement vs pure Python MIT pkgs producing semantically equivalent HTML.
**Synthesis:** Ship Pygments + markdown-it-py. Document divergence in ADR-0085. User-observable output is visually equivalent.

### Single theme vs full Pi color-derivation
**Tension:** Pi's luminance-based theme variation is a visible aesthetic gap.
**Decision:** Single dark theme in 6h₅c. Color-derivation deferred to 6h₅d. Aesthetic polish doesn't block A 단계 closure.

---

## §M — Workflow

W2 executor opus → W3 verification → W4/W5 parallel review/audit → W6 commits + ADRs.

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
