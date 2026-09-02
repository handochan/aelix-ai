# Sprint 6h₆ — Phase 5a-i + 5a-ii BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016`

**Phase 5a-i + 5a-ii** — Aelix processes first runnable CLI entry. NO TUI work. NO new RPC commands. ~1,000-1,200 prod + ~500-650 test LOC.

---

## §0 — W0 findings (P-385 ~ P-394)

### P-385 — Pi `main.ts` 716 LOC, 4-mode router
Pi mode resolution (`main.ts:96-113`):
```ts
type AppMode = "interactive" | "print" | "json" | "rpc";
function resolveAppMode(parsed: Args, stdinIsTTY: boolean): AppMode {
    if (parsed.mode === "rpc") return "rpc";
    if (parsed.mode === "json") return "json";
    if (parsed.print || !stdinIsTTY) return "print";
    return "interactive";
}
```
TTY second pass (`main.ts:631-637`): if `appMode === "interactive"` AND piped stdin detected → demote to `"print"`.

### P-386 — Pi `cli/args.ts` 354 LOC hand-rolled linear parser
30+ optional fields in `Args`. `parseArgs(args)` is a single `for` loop with lookahead. **3 features argparse/click cannot cleanly express:**
- `--print` opportunistic positional eat (peek next, swallow if not `@`/`-`)
- `--list-models [search]` ambiguous optional value
- Unknown `--ext-flag value` passthrough → `unknownFlags: Map<string, boolean|string>`

**Decision:** Aelix hand-rolls `parse_args` mirroring Pi. NOT argparse. NOT click.

Full flag inventory (must support 30+):
`--help`, `--version`, `--mode <text|json|rpc>`, `--print/-p [pos]`, `--continue/-c`, `--resume/-r`, `--provider`, `--model`, `--api-key`, `--system-prompt`, `--append-system-prompt`, `--no-session`, `--session`, `--fork`, `--session-dir`, `--models`, `--no-tools/-nt`, `--no-builtin-tools/-nbt`, `--tools/-t`, `--thinking`, `--extension/-e`, `--no-extensions/-ne`, `--skill`, `--no-skills/-ns`, `--prompt-template`, `--no-prompt-templates/-np`, `--theme`, `--no-themes`, `--no-context-files/-nc`, `--export`, `--list-models [search]`, `--verbose`, `--offline`, `@file` positional, plain positional → `messages[]`.

### P-387 — Pi `cli/file-processor.ts` 100 LOC
`processFileArguments` per arg: expand `~` → resolve → access check → stat → image branch (base64 + optional resize) OR text branch (read + `<file name="X">...</file>` wrapping). **Image branch deferred to 5a-iii.**

### P-388 — Pi `cli/initial-message.ts` 43 LOC SIDE EFFECTS
```ts
const parts: string[] = [];
if (stdinContent !== undefined) parts.push(stdinContent);
if (fileText) parts.push(fileText);
if (parsed.messages.length > 0) {
    parts.push(parsed.messages[0]);
    parsed.messages.shift();  // SIDE EFFECT — mutates parsed
}
return {
    initialMessage: parts.length > 0 ? parts.join("") : undefined,
    initialImages: fileImages && fileImages.length > 0 ? fileImages : undefined,
};
```

**Two parity hazards:**
- `.shift()` mutates `parsed.messages` — Aelix MUST mirror
- `parts.join("")` no-separator concat — `stdin + fileText + firstMessage` glued without whitespace

### P-389 — Pi `modes/print-mode.ts` 158 LOC
Lifecycle:
1. `registerSignalHandlers` (SIGTERM/SIGHUP non-Windows)
2. `setRebindSession(async () => rebindSession())`
3. `rebindSession`: bind extensions + subscribe (JSON mode: `event => writeRawStdout(JSON.stringify(event)+"\n")`)
4. JSON mode header emit (sessionManager.getHeader)
5. await rebindSession
6. If `initialMessage`: `await session.prompt(initial)`
7. Loop residual `messages`: `await session.prompt(msg)`
8. **Text mode terminal printout** (`:128-145`): if last is assistant + non-error/aborted, emit only TextContent blocks via `writeRawStdout`
9. catch → exit 1; finally → cleanup

JSON output: line-delimited JSON, one event per line.

### P-390 — Pi `config.ts`
`VERSION` (from package.json), `APP_NAME = "pi"`, `CONFIG_DIR_NAME = ".pi"`, `ENV_AGENT_DIR = "PI_CODING_AGENT_DIR"`, `ENV_SESSION_DIR = "PI_CODING_AGENT_SESSION_DIR"`, `expandTildePath`, `getAgentDir`.

**Aelix mapping:**
- `VERSION` via `importlib.metadata.version("aelix-coding-agent")`
- `APP_NAME = "aelix"` (Sprint 6h₃ already substituted in HTML export)
- `CONFIG_DIR_NAME = ".aelix"`
- `ENV_AGENT_DIR = "AELIX_CODING_AGENT_DIR"`
- `ENV_SESSION_DIR = "AELIX_CODING_AGENT_SESSION_DIR"`

### P-391 — Aelix entry point absent
- NO `__main__.py`
- NO `[project.scripts]` in pyproject.toml
- Existing `cli/repl.py` 123 LOC is Sprint 5b §B.2 minimal REPL (NOT mode router)
- Running `python -m aelix_coding_agent` fails

### P-392 — Async vs sync entry
`[project.scripts]` calls sync function. Inner body: `asyncio.run(_async_main(sys.argv[1:]))`. Windows `add_signal_handler` not supported — must guard `if sys.platform != "win32"`.

### P-393 — Deferred items (5a-iii / 5a-iv)
- **SettingsManager** — Pi `core/settings-manager.ts`. Required for `--list-models`, `--resume` theme bootstrap, `getDefaultProvider/Model`. Light path: skip settings, `--model` is only way to choose model in print/json modes; env-var auth via existing `AuthStorage`.
- **--list-models** — needs SettingsManager + `fuzzy_filter`. Defer.
- **migrations** — Pi `migrations.ts`. No-op for Aelix today.
- **session-picker** (`--resume`) — needs interactive TUI.
- **--export** — already exists via `harness.export_to_html()` (Sprint 6h₅c). Wire as 5a-i convenience.
- **--continue/-c** — needs session picker.
- **--fork** — needs session picker.

### P-394 — `takeOverStdout` gap
Pi redirects stdout when `appMode !== "interactive"` so tool `console.log` doesn't corrupt JSONL/text stream. Aelix builtins emit through harness events, NOT raw stdout — **less load-bearing**. Punt acceptable for 6h₆.

---

## §A — Scope LOC table (~1,000-1,200 prod + ~500-650 test)

| Component | File | Prod | Test |
|---|---|---|---|
| Config constants | `aelix_coding_agent/cli/config.py` (NEW) | ~60 | ~30 |
| Args parser | `aelix_coding_agent/cli/args.py` (NEW) | ~250 | ~200 |
| File processor (text-only) | `aelix_coding_agent/cli/file_processor.py` (NEW) | ~80 | ~80 |
| Initial message builder | `aelix_coding_agent/cli/initial_message.py` (NEW) | ~35 | ~80 |
| Print mode | `aelix_coding_agent/modes/print_mode.py` (NEW) | ~180 | ~150 |
| Modes re-export | `aelix_coding_agent/modes/__init__.py` (NEW) | ~20 | — |
| Entry main | `aelix_coding_agent/cli/entry.py` (NEW) | ~280 | ~140 |
| `__main__.py` | `aelix_coding_agent/__main__.py` (NEW) | ~20 | — |
| pyproject.toml [project.scripts] | (AMEND) | ~5 | — |
| **Total** | | **~930** | **~680** |

### NOT in scope (5a-iii / 5a-iv carry-forward)
- SettingsManager port
- `--list-models` (needs SettingsManager)
- `--export` (light wire only — full session-picker resume deferred)
- `--continue/-c`, `--resume/-r`, `--fork` (need session picker UI)
- migrations.ts port (no-op today)
- Image branch in file_processor (image-resize utility not yet ported)
- `takeOverStdout` (low-priority, Aelix builtins don't corrupt stream)

---

## §B — `cli/config.py` (NEW)

```python
"""Pi parity: ``config.ts`` constants + helpers.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-390).
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

APP_NAME = "aelix"  # Pi APP_NAME equivalent (Sprint 6h₃ HTML export precedent)
CONFIG_DIR_NAME = ".aelix"  # Pi `.pi` equivalent
ENV_AGENT_DIR = "AELIX_CODING_AGENT_DIR"
ENV_SESSION_DIR = "AELIX_CODING_AGENT_SESSION_DIR"


def _get_version() -> str:
    """Pi parity: VERSION read from package.json. Aelix reads PEP 621 [project] version."""
    try:
        return version("aelix-coding-agent")
    except PackageNotFoundError:
        return "0.0.0-dev"


VERSION = _get_version()


def expand_tilde_path(path: str) -> str:
    """Pi parity: expandTildePath."""
    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    return path


def get_agent_dir() -> str:
    """Pi parity: getAgentDir.
    
    Returns ENV_AGENT_DIR if set (with tilde expansion), else
    ~/.aelix/agent.
    """
    env = os.environ.get(ENV_AGENT_DIR)
    if env:
        return expand_tilde_path(env)
    return str(Path.home() / CONFIG_DIR_NAME / "agent")


def get_session_dir() -> str | None:
    """Pi parity: ENV_SESSION_DIR override (no default — repo's default applies)."""
    env = os.environ.get(ENV_SESSION_DIR)
    return expand_tilde_path(env) if env else None
```

---

## §C — `cli/args.py` (NEW, ~250 LOC)

Hand-rolled linear loop per P-386. Output dataclass:

```python
@dataclass
class Args:
    # Mode + IO
    mode: Literal["text", "json", "rpc"] = "text"
    print_mode: bool = False  # --print / -p
    
    # Session
    continue_session: bool = False  # --continue / -c
    resume: bool = False  # --resume / -r
    no_session: bool = False
    session: str | None = None
    fork: str | None = None
    session_dir: str | None = None
    
    # Model
    provider: str | None = None
    model: str | None = None
    models: list[str] = field(default_factory=list)  # comma-split
    api_key: str | None = None
    thinking: str | None = None
    
    # Prompt
    system_prompt: str | None = None
    append_system_prompt: list[str] = field(default_factory=list)
    
    # Tools/Extensions
    no_tools: bool = False
    no_builtin_tools: bool = False
    tools: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)  # --extension repeatable
    no_extensions: bool = False
    skills: list[str] = field(default_factory=list)
    no_skills: bool = False
    prompt_templates: list[str] = field(default_factory=list)
    no_prompt_templates: bool = False
    themes: list[str] = field(default_factory=list)
    no_themes: bool = False
    no_context_files: bool = False
    
    # Misc
    export: str | None = None
    list_models: str | bool | None = None  # None=absent, True=no-pattern, str=pattern
    verbose: bool = False
    offline: bool = False
    help: bool = False
    version: bool = False
    
    # Always-present
    messages: list[str] = field(default_factory=list)
    file_args: list[str] = field(default_factory=list)
    unknown_flags: dict[str, str | bool] = field(default_factory=dict)
    diagnostics: list[dict[str, str]] = field(default_factory=list)


VALID_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")


def parse_args(argv: list[str]) -> Args:
    """Pi parity: parseArgs (cli/args.ts).
    
    Single linear loop with lookahead. Per Pi P-386 — argparse/click can't
    cleanly express --print opportunistic positional, --list-models optional
    pattern, and unknown extension flag passthrough.
    """
    parsed = Args()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            parsed.help = True
        elif arg in ("--version", "-v"):
            parsed.version = True
        elif arg == "--mode" and i + 1 < len(argv):
            mode_val = argv[i + 1]
            if mode_val in ("text", "json", "rpc"):
                parsed.mode = mode_val
            else:
                parsed.diagnostics.append({"type": "error", "message": f"Invalid --mode: {mode_val}"})
            i += 1
        elif arg in ("--print", "-p"):
            parsed.print_mode = True
            # Pi opportunistic eat
            if i + 1 < len(argv) and not argv[i + 1].startswith("@") and not argv[i + 1].startswith("-"):
                parsed.messages.append(argv[i + 1])
                i += 1
        elif arg in ("--continue", "-c"):
            parsed.continue_session = True
        elif arg in ("--resume", "-r"):
            parsed.resume = True
        elif arg == "--provider" and i + 1 < len(argv):
            parsed.provider = argv[i + 1]; i += 1
        elif arg == "--model" and i + 1 < len(argv):
            parsed.model = argv[i + 1]; i += 1
        elif arg == "--models" and i + 1 < len(argv):
            parsed.models = [s.strip() for s in argv[i + 1].split(",") if s.strip()]
            i += 1
        elif arg == "--api-key" and i + 1 < len(argv):
            parsed.api_key = argv[i + 1]; i += 1
        elif arg == "--system-prompt" and i + 1 < len(argv):
            parsed.system_prompt = argv[i + 1]; i += 1
        elif arg == "--append-system-prompt" and i + 1 < len(argv):
            parsed.append_system_prompt.append(argv[i + 1]); i += 1
        elif arg == "--no-session":
            parsed.no_session = True
        elif arg == "--session" and i + 1 < len(argv):
            parsed.session = argv[i + 1]; i += 1
        elif arg == "--fork" and i + 1 < len(argv):
            parsed.fork = argv[i + 1]; i += 1
        elif arg == "--session-dir" and i + 1 < len(argv):
            parsed.session_dir = argv[i + 1]; i += 1
        elif arg in ("--no-tools", "-nt"):
            parsed.no_tools = True
        elif arg in ("--no-builtin-tools", "-nbt"):
            parsed.no_builtin_tools = True
        elif arg in ("--tools", "-t") and i + 1 < len(argv):
            parsed.tools = [s.strip() for s in argv[i + 1].split(",") if s.strip()]
            i += 1
        elif arg == "--thinking" and i + 1 < len(argv):
            level = argv[i + 1]
            if level in VALID_THINKING_LEVELS:
                parsed.thinking = level
            else:
                parsed.diagnostics.append({"type": "warning", "message": f"Invalid --thinking level: {level}"})
            i += 1
        elif arg in ("--extension", "-e") and i + 1 < len(argv):
            parsed.extensions.append(argv[i + 1]); i += 1
        elif arg in ("--no-extensions", "-ne"):
            parsed.no_extensions = True
        elif arg == "--skill" and i + 1 < len(argv):
            parsed.skills.append(argv[i + 1]); i += 1
        elif arg in ("--no-skills", "-ns"):
            parsed.no_skills = True
        elif arg == "--prompt-template" and i + 1 < len(argv):
            parsed.prompt_templates.append(argv[i + 1]); i += 1
        elif arg in ("--no-prompt-templates", "-np"):
            parsed.no_prompt_templates = True
        elif arg == "--theme" and i + 1 < len(argv):
            parsed.themes.append(argv[i + 1]); i += 1
        elif arg == "--no-themes":
            parsed.no_themes = True
        elif arg in ("--no-context-files", "-nc"):
            parsed.no_context_files = True
        elif arg == "--export" and i + 1 < len(argv):
            parsed.export = argv[i + 1]; i += 1
        elif arg == "--list-models":
            # Optional pattern lookahead
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                parsed.list_models = argv[i + 1]; i += 1
            else:
                parsed.list_models = True
        elif arg == "--verbose":
            parsed.verbose = True
        elif arg == "--offline":
            parsed.offline = True
        elif arg.startswith("@"):
            parsed.file_args.append(arg[1:])
        elif arg.startswith("--"):
            # Unknown ext flag passthrough
            if "=" in arg:
                k, v = arg[2:].split("=", 1)
                parsed.unknown_flags[k] = v
            elif i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                parsed.unknown_flags[arg[2:]] = argv[i + 1]; i += 1
            else:
                parsed.unknown_flags[arg[2:]] = True
        elif arg.startswith("-") and len(arg) > 1:
            parsed.diagnostics.append({"type": "error", "message": f"Unknown short flag: {arg}"})
        else:
            parsed.messages.append(arg)
        i += 1
    return parsed


def print_help(extension_flags: list[dict[str, str]] | None = None) -> None:
    """Print Pi-parity help text."""
    # Full help text mirroring Pi printHelp output, with APP_NAME=aelix
    ...
```

---

## §D — `cli/file_processor.py` (NEW, text-only, ~80 LOC)

```python
"""Pi parity: cli/file-processor.ts text-only port.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-387). Image branch deferred to
5a-iii / image-resize utility port.
"""

from __future__ import annotations
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import expand_tilde_path


@dataclass
class ProcessedFiles:
    text: str = ""
    images: list[Any] = field(default_factory=list)  # ImageContent | None


async def process_file_arguments(
    file_args: list[str],
    *,
    cwd: str | None = None,
) -> ProcessedFiles:
    """Pi parity: processFileArguments.
    
    Image branch DEFERRED (5a-iii). Emits warning diagnostic if image
    extension detected.
    """
    result = ProcessedFiles()
    cwd_path = Path(cwd) if cwd else Path.cwd()
    for file_arg in file_args:
        expanded = expand_tilde_path(file_arg)
        path = (cwd_path / expanded).resolve() if not Path(expanded).is_absolute() else Path(expanded)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            stat = path.stat()
            if stat.st_size == 0:
                continue  # skip zero-byte
            # Image detection (simplistic mime check)
            ext = path.suffix.lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                print(f"Warning: image file {path} skipped (Sprint 6h₆ text-only)", file=sys.stderr)
                continue
            content = path.read_text(encoding="utf-8")
            result.text += f'<file name="{path.name}">\n{content}\n</file>\n'
        except OSError as e:
            print(f"Error reading {path}: {e}", file=sys.stderr)
            sys.exit(1)
    return result
```

---

## §E — `cli/initial_message.py` (NEW, ~35 LOC)

```python
"""Pi parity: cli/initial-message.ts.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-388). Mirrors Pi side-effect
(.shift() mutates parsed.messages).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .args import Args


@dataclass
class InitialMessage:
    initial_message: str | None = None
    initial_images: list[Any] | None = None


def build_initial_message(
    parsed: Args,
    *,
    file_text: str | None = None,
    file_images: list[Any] | None = None,
    stdin_content: str | None = None,
) -> InitialMessage:
    """Pi parity: buildInitialMessage.
    
    Composition order: stdin + fileText + firstMessage (no separator).
    SIDE EFFECT: pops index 0 from parsed.messages (Pi `.shift()`).
    """
    parts: list[str] = []
    if stdin_content is not None:
        parts.append(stdin_content)
    if file_text:
        parts.append(file_text)
    if parsed.messages:
        parts.append(parsed.messages[0])
        parsed.messages.pop(0)  # SIDE EFFECT: Pi .shift() parity
    return InitialMessage(
        initial_message="".join(parts) if parts else None,
        initial_images=file_images if file_images else None,
    )
```

---

## §F — `modes/print_mode.py` (NEW, ~180 LOC)

```python
"""Pi parity: modes/print-mode.ts.

Sprint 6h₆ (Phase 5a-ii, ADR-0089, P-389).
"""

from __future__ import annotations
import asyncio
import json
import signal
import sys
from typing import Any, Literal

from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime
from aelix_ai.messages import AssistantMessage, TextContent


def _write_raw_stdout(text: str) -> None:
    """Pi parity: writeRawStdout."""
    sys.stdout.write(text)
    sys.stdout.flush()


async def run_print_mode(
    runtime_host: AgentSessionRuntime,
    *,
    mode: Literal["text", "json"],
    messages: list[str],
    initial_message: str | None,
    initial_images: list[Any] | None = None,
) -> int:
    """Pi parity: runPrintMode.
    
    Returns exit code (0 success, 1 error).
    """
    # Pi step 1 — register signal handlers (non-Windows)
    loop = asyncio.get_running_loop()
    cleanup_handlers: list[signal.Handlers | None] = []
    if sys.platform != "win32":
        def _sig_handler(sig: int) -> None:
            asyncio.create_task(_cleanup_and_exit(runtime_host, sig))
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                loop.add_signal_handler(sig, _sig_handler, sig)
            except (NotImplementedError, RuntimeError):
                pass
    
    # Pi step 2-3 — rebind closure + subscribe
    unsubscribe: callable | None = None
    
    async def _rebind() -> None:
        nonlocal unsubscribe
        if unsubscribe:
            unsubscribe()
            unsubscribe = None
        if mode == "json":
            unsubscribe = runtime_host.harness.subscribe(
                lambda event: _write_raw_stdout(json.dumps(_event_to_dict(event)) + "\n")
            )
    
    runtime_host.set_rebind_session(_rebind)
    
    try:
        # Pi step 4 — JSON header emit
        if mode == "json":
            session = runtime_host.harness.session
            if session is not None:
                metadata = await session.get_metadata()
                if metadata:
                    header_dict = _metadata_to_dict(metadata)
                    _write_raw_stdout(json.dumps(header_dict) + "\n")
        
        # Pi step 5 — initial rebind
        await _rebind()
        
        # Pi step 6 — initial message
        if initial_message is not None:
            await runtime_host.harness.prompt(initial_message, images=initial_images)
        
        # Pi step 7 — residual messages loop
        for message in messages:
            await runtime_host.harness.prompt(message)
        
        # Pi step 8 — text mode terminal printout
        exit_code = 0
        if mode == "text":
            state_messages = list(runtime_host.harness.state.messages)
            if state_messages:
                last = state_messages[-1]
                if isinstance(last, AssistantMessage):
                    stop_reason = getattr(last, "stop_reason", None)
                    if stop_reason in ("error", "aborted"):
                        error_message = getattr(last, "error_message", None) or f"Request {stop_reason}"
                        print(error_message, file=sys.stderr)
                        exit_code = 1
                    else:
                        for content in last.content:
                            if isinstance(content, TextContent):
                                _write_raw_stdout(f"{content.text}\n")
        
        return exit_code
    
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    
    finally:
        # Pi step 10 — cleanup
        if unsubscribe:
            unsubscribe()
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGHUP):
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    pass
        try:
            await runtime_host.dispose()
        except Exception:  # noqa: BLE001
            pass
        sys.stdout.flush()


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert harness event to dict for JSON emit."""
    # Reuse existing rpc/rpc_mode.py _dataclass_to_dict pattern
    from aelix_coding_agent.rpc.rpc_mode import _dataclass_to_dict
    return _dataclass_to_dict(event)


def _metadata_to_dict(metadata: Any) -> dict[str, Any]:
    """Convert session metadata to dict for JSON header emit."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(metadata):
        return asdict(metadata)
    return dict(metadata) if hasattr(metadata, "__iter__") else {"metadata": str(metadata)}


async def _cleanup_and_exit(runtime_host: AgentSessionRuntime, sig: int) -> None:
    """Pi parity: signal handler cleanup."""
    try:
        await runtime_host.dispose()
    finally:
        sys.exit(128 + sig)
```

---

## §G — `modes/__init__.py` (NEW, ~20 LOC)

```python
"""Pi parity: modes/index.ts."""

from aelix_coding_agent.modes.print_mode import run_print_mode
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

__all__ = ["run_print_mode", "run_rpc_mode"]
```

---

## §H — `cli/entry.py` (NEW, ~280 LOC)

```python
"""Pi parity: main.ts entry point.

Sprint 6h₆ (Phase 5a-i + 5a-ii, ADR-0089, P-385/P-391/P-392).
"""

from __future__ import annotations
import asyncio
import sys
from typing import Literal

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime.agent_session_runtime import create_agent_session_runtime
from aelix_agent_core.session.fs import LocalFileSystem
from aelix_agent_core.session.jsonl_repo import JsonlSessionRepo

from .args import Args, parse_args, print_help
from .config import VERSION
from .file_processor import process_file_arguments
from .initial_message import build_initial_message
from ..modes import run_print_mode, run_rpc_mode

AppMode = Literal["interactive", "print", "json", "rpc"]


def resolve_app_mode(parsed: Args, stdin_is_tty: bool) -> AppMode:
    """Pi parity: resolveAppMode (main.ts:96-113)."""
    if parsed.mode == "rpc":
        return "rpc"
    if parsed.mode == "json":
        return "json"
    if parsed.print_mode or not stdin_is_tty:
        return "print"
    return "interactive"


def to_print_output_mode(app_mode: AppMode) -> Literal["text", "json"]:
    """Pi parity: toPrintOutputMode."""
    return "json" if app_mode == "json" else "text"


async def _read_piped_stdin() -> str | None:
    """Pi parity: readPipedStdin. Returns None when stdin is TTY."""
    if sys.stdin.isatty():
        return None
    data = await asyncio.to_thread(sys.stdin.read)
    return data.strip() or None


async def _async_main(argv: list[str]) -> int:
    """Pi parity: main() (main.ts:423-716) reduced for 5a-i/5a-ii scope."""
    parsed = parse_args(argv)
    
    # Diagnostics
    for diag in parsed.diagnostics:
        prefix = "Error: " if diag["type"] == "error" else "Warning: "
        print(f"{prefix}{diag['message']}", file=sys.stderr)
    if any(d["type"] == "error" for d in parsed.diagnostics):
        return 1
    
    if parsed.help:
        print_help()
        return 0
    if parsed.version:
        print(VERSION)
        return 0
    
    # Mode resolution
    stdin_is_tty = sys.stdin.isatty()
    app_mode = resolve_app_mode(parsed, stdin_is_tty)
    
    # Pi guard: rpc + @file is invalid
    if app_mode == "rpc" and parsed.file_args:
        print("Error: --mode rpc cannot be combined with @file arguments", file=sys.stderr)
        return 1
    
    # Interactive mode deferred (Phase 5b)
    if app_mode == "interactive":
        print("Error: interactive mode not implemented (Phase 5b — TUI carry-forward)", file=sys.stderr)
        return 1
    
    # Read piped stdin (non-RPC)
    stdin_content = None
    if app_mode != "rpc":
        stdin_content = await _read_piped_stdin()
        # Pi second-pass demotion: interactive + piped → print (already covered by resolve_app_mode TTY check)
    
    # File processing
    file_text = ""
    file_images = None
    if parsed.file_args:
        processed = await process_file_arguments(parsed.file_args)
        file_text = processed.text
        file_images = processed.images or None
    
    # Initial message
    initial = build_initial_message(
        parsed, file_text=file_text, file_images=file_images, stdin_content=stdin_content,
    )
    
    # Harness + runtime construction
    fs = LocalFileSystem()
    # Session: in-memory if --no-session, else default repo
    if parsed.no_session:
        from aelix_agent_core.session.memory_storage import InMemorySessionStorage
        from aelix_agent_core.session.session import Session
        session = Session(storage=InMemorySessionStorage())
    else:
        repo = JsonlSessionRepo(fs=fs)
        from aelix_agent_core.session.jsonl_repo import JsonlSessionCreateOptions
        from pathlib import Path
        cwd = str(Path.cwd())
        session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    
    # Harness factory (minimal — model selection deferred to SettingsManager port)
    async def _harness_factory(new_session) -> AgentHarness:
        options = AgentHarnessOptions(session=new_session)
        return AgentHarness(options)
    
    harness = await _harness_factory(session)
    runtime = await create_agent_session_runtime(
        harness, _harness_factory, repo=None if parsed.no_session else repo, fs=fs,
    )
    
    try:
        if app_mode == "rpc":
            await run_rpc_mode(harness, runtime_host=runtime)
            return 0
        else:  # print or json
            return await run_print_mode(
                runtime,
                mode=to_print_output_mode(app_mode),
                messages=parsed.messages,
                initial_message=initial.initial_message,
                initial_images=initial.initial_images,
            )
    finally:
        try:
            await runtime.dispose()
        except Exception:  # noqa: BLE001
            pass


def main_sync() -> None:
    """Sync entry for [project.scripts] aelix = '...:main_sync'."""
    exit_code = asyncio.run(_async_main(sys.argv[1:]))
    sys.exit(exit_code)
```

---

## §I — `__main__.py` (NEW, ~20 LOC)

```python
"""Pi parity: enables `python -m aelix_coding_agent`.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-391).
"""

from aelix_coding_agent.cli.entry import main_sync

if __name__ == "__main__":
    main_sync()
```

---

## §J — `pyproject.toml` AMEND

```toml
[project.scripts]
aelix = "aelix_coding_agent.cli.entry:main_sync"
```

---

## §K — Tests (~500-650 LOC)

| File | Coverage |
|---|---|
| `tests/cli/test_config.py` (NEW) | VERSION, APP_NAME, expand_tilde_path, get_agent_dir, env override |
| `tests/cli/test_args.py` (NEW) | every flag, diagnostic combos, --print opportunistic eat, --list-models optional, unknown flag passthrough, @file fork |
| `tests/cli/test_file_processor.py` (NEW) | text file, missing file, empty file, image skip warning, ~/ expansion |
| `tests/cli/test_initial_message.py` (NEW) | composition order, .shift() mutation, missing parts, empty result None |
| `tests/cli/test_print_mode.py` (NEW) | text mode output, JSON mode streaming, error stop reason, signal cleanup |
| `tests/cli/test_entry_router.py` (NEW) | resolve_app_mode table, --rpc + @file guard, --version, --help, piped stdin upgrade |

Expected delta: 1932 → ~1955-1985 pass.

---

## §L — ADRs

### NEW ADR-0088 — Phase 5b TUI library decision note
- **Status:** Proposed (deferred decision)
- Document Python TUI options: textual, rich, prompt-toolkit, blessed + hybrid combos
- Pi `pi-tui` extensibility model (31-method `ExtensionUIContext`, factory pattern)
- **Recommendations:**
  - **PRIMARY:** textual + rich
  - **ALTERNATIVE:** prompt-toolkit + rich
  - **CONTINGENCY:** textual alone
- Library-agnostic `Component` Protocol REQUIRED before locking in
- 10 open questions enumerated
- Decision deferred to Phase 5b kickoff

### NEW ADR-0089 — Sprint 6h₆ Phase 5a-i + 5a-ii closure
- **Status:** Accepted
- Pi citations: `main.ts:96-113, :423-716`, `cli/args.ts`, `cli/file-processor.ts`, `cli/initial-message.ts`, `modes/print-mode.ts`, `config.ts`
- Decisions: P-385 ~ P-394
- Aelix-additive divergences:
  1. APP_NAME = "aelix" (Sprint 6h₃ precedent)
  2. argparse/click rejected — hand-rolled parser for Pi parity
  3. SettingsManager / list-models / image-resize / migrations / session-picker deferred (5a-iii/iv)
  4. interactive mode raises NotImplementedError (Phase 5b TUI carry-forward)
  5. takeOverStdout punted (low-priority — Aelix builtins don't corrupt stream)
- 5a-iii / 5a-iv carry-forward catalog

### AMEND ADR-0034
Add Sprint 6h₆ row — Aelix CLI entrypoint shipped.

---

## §M — Atomic commit plan (EXACTLY 5)

**Commit 1** — `feat(cli): config + args + file_processor + initial_message (Sprint 6h₆ §B/§C/§D/§E)`
Files:
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/config.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/args.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/file_processor.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/initial_message.py` (NEW)
- `tests/cli/__init__.py` (NEW)
- `tests/cli/test_config.py` (NEW)
- `tests/cli/test_args.py` (NEW)
- `tests/cli/test_file_processor.py` (NEW)
- `tests/cli/test_initial_message.py` (NEW)

**Commit 2** — `feat(modes): print_mode + modes/__init__ (Sprint 6h₆ §F/§G)`
Files:
- `packages/aelix-coding-agent/src/aelix_coding_agent/modes/__init__.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/modes/print_mode.py` (NEW)
- `tests/cli/test_print_mode.py` (NEW)

**Commit 3** — `feat(cli): entry main_sync + __main__.py + pyproject [project.scripts] (Sprint 6h₆ §H/§I/§J)`
Files:
- `packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py` (NEW)
- `packages/aelix-coding-agent/src/aelix_coding_agent/__main__.py` (NEW)
- `packages/aelix-coding-agent/pyproject.toml` (AMEND)
- `tests/cli/test_entry_router.py` (NEW)

**Commit 4** — `docs: ADR-0088 (TUI decision note) + ADR-0089 (Sprint 6h₆ closure) + ADR-0034 amend + README`
Files:
- `docs/decisions/0088-phase-5b-tui-library-decision.md` (NEW)
- `docs/decisions/0089-sprint-6h6-phase-5a-i-and-ii.md` (NEW)
- `docs/decisions/0034-pi-reference-version-pin.md` (AMEND)
- `docs/decisions/README.md` (AMEND)

**Commit 5** — Reserved for W6 must-fix items from W4/W5 audit (or merge into 4 if no fixes needed).

Each commit uses HEREDOC + trailer:
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §N — Verification gates

After each commit:
- `uv run pytest 2>&1 | tail -3` — 1932 + ~25-50 new = 1955+ pass
- `uv run ruff check 2>&1 | tail -2` — clean
- `uv run pyright 2>&1 | tail -3` — 8 baseline preserved
- After C3: `uv run python -m aelix_coding_agent --version` returns version string
- After C3: `uv run python -m aelix_coding_agent --help` shows help

Final smoke test:
```bash
echo "hello" | uv run python -m aelix_coding_agent --print --model anthropic/claude-sonnet-4-5
```
(May fail without API key — acceptable; success path requires auth setup, but parse + print mode lifecycle must work.)

---

## §O — Workflow

W2 executor opus → W3 verification → W4 code-reviewer / W5 Pi parity audit parallel → W6 apply findings + commits.

**Out-of-scope** (binding):
- NO interactive TUI mode (Phase 5b — pending consultation + ADR-0088 decision)
- NO SettingsManager port (5a-iii)
- NO --list-models (5a-iii)
- NO image branch in file_processor (5a-iii)
- NO session picker (--continue/-c, --resume/-r, --fork)
- NO migrations port
- NO new RPC commands

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
