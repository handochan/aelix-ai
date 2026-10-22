"""Additive, versioned runtime control API for non-terminal hosts.

This is separate from the 29-command Pi compatibility roster. Nothing here
invokes a model or falls through from an unknown command to a model prompt.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from aelix_agent_core.harness.prompt_templates import (
    format_prompt_template_invocation,
    parse_command_args,
)
from aelix_agent_core.harness.skills import format_skill_invocation
from aelix_ai.models import get_supported_thinking_levels

from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.extensions.command_dispatch import (
    CommandDispatchService,
    CommandSurfaceBindings,
)
from aelix_coding_agent.rpc.control_mcp import RpcMcpController
from aelix_coding_agent.rpc.control_ui import RpcCommandUI

# Explicit setters only: credentials, arbitrary attribute access, and project
# trust are not writable through an introspection endpoint.
SETTING_FIELDS: dict[str, tuple[str, type, tuple[Any, ...] | None]] = {
    "default_provider": ("default_provider", str, None),
    "default_model": ("default_model", str, None),
    "default_thinking_level": ("default_thinking_level", str,
                               ("off", "minimal", "low", "medium", "high", "xhigh", "max")),
    "steering_mode": ("steering_mode", str, ("all", "one-at-a-time")),
    "follow_up_mode": ("follow_up_mode", str, ("all", "one-at-a-time")),
    "compaction_enabled": ("compaction_enabled", bool, None),
    "retry_enabled": ("retry_enabled", bool, None),
    "hide_thinking_block": ("hide_thinking_block", bool, None),
    "hide_compaction_summary": ("hide_compaction_summary", bool, None),
    "respect_gitignore": ("respect_gitignore", bool, None),
    "check_for_updates": ("check_for_updates", bool, None),
    "skill_paths": ("skill_paths", list, None),
    "prompt_template_paths": ("prompt_template_paths", list, None),
    "enabled_models": ("enabled_models", list, None),
}


class RpcControlPlane:
    """Operate on the live runtime, including after session replacement."""

    def __init__(self, runtime_host: Any, *, permission_posture: Any = None,
                 settings_manager: Any = None, auth_storage: Any = None, model_registry: Any = None, agent_service: Any = None, extension_errors: Any = None, emit: Any = None) -> None:
        self.runtime = runtime_host
        self.posture = permission_posture
        self.settings = settings_manager
        self.auth = auth_storage
        self.agents = agent_service
        self.extension_errors = extension_errors if extension_errors is not None else []
        self.emit = emit or (lambda event: None)
        self.subscriptions: list[Any] = []
        self.model_registry = model_registry
        self.lock = asyncio.Lock()
        self.ui = RpcCommandUI(emit or (lambda event: None))
        self.mcp = RpcMcpController()
        self.command_task: asyncio.Task[Any] | None = None
        self.bind_progress(self.harness)

    @property
    def harness(self) -> Any:
        return self.runtime.harness

    async def handle(self, method: object, params: object) -> dict[str, Any]:
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ValueError("Control method and params must be a string and object")
        if method == "commands/cancel":
            active = self.command_task
            if active is not None and not active.done():
                active.cancel()
                return {"cancelled": True}
            return {"cancelled": False}
        readonly = {"capabilities", "resources/list", "settings/get", "permissions/get", "permissions/evaluate",
                    "mcp/status", "extensions/list", "auth/status", "agents/list", "agents/show"}
        if method in readonly:
            return await self._dispatch(method, params)
        async with self.lock:
            self.command_task = asyncio.current_task()
            try:
                return await self._dispatch(method, params)
            finally:
                self.command_task = None

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "capabilities":
            model = self.harness.current_model
            return {"version": 1, "methods": ["commands/run", "commands/cancel", "resources/list", "resources/reload",
                    "settings/get", "settings/set", "permissions/get", "permissions/set", "permissions/evaluate",
                    "tools/set_active", "ui/enable", "mcp/configure", "mcp/status",
                    "auth/status", "auth/set_api_key", "auth/logout", "auth/login", "extensions/list",
                    "agents/list", "agents/show", "agents/use", "agents/run", "sessions/branch"],
                    "permissionModes": [mode.value for mode in PermissionMode] if self.posture else [],
                    "thinkingLevels": get_supported_thinking_levels(model) if model else ["off"],
                    "activeAgent": self.agents.active.name if self.agents and self.agents.active else None,
                    "subagentsEnabled": self.harness.runtime.subagents is not None}
        if isinstance(method, str) and method.startswith("auth/"):
            return await self.auth_action(method, params)
        if method.startswith("agents/"):
            return await self.agent_action(method, params)
        if method == "ui/enable":
            self.ui.enabled = params.get("enabled") is True
            return {"enabled": self.ui.enabled}
        if method == "permissions/evaluate":
            from aelix_coding_agent.builtin.permission import PermissionExtension
            args = params.get("args")
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be an object")
            return {"decision": PermissionExtension._auto_classify_bash(args)}
        if method == "mcp/status":
            return self.mcp.status()
        if method == "extensions/list":
            from importlib.metadata import entry_points

            from aelix_coding_agent.cli.extension_install import list_installed_extensions
            references = {entry.name: entry.value for entry in entry_points(group="aelix.extensions")}
            return {"extensions": [{"name": entry.ep_name, "distribution": entry.dist_name, "version": entry.version,
                                    "entry": references.get(entry.ep_name)} for entry in list_installed_extensions()]}
        if method == "resources/list":
            return self.resources()
        if method == "settings/get":
            if self.settings is None:
                raise ValueError("This RPC host has no settings manager")
            return {"settings": {key: getattr(self.settings, "get_" + field)()
                                 for key, (field, _kind, _choices) in SETTING_FIELDS.items()},
                    "schema": [{"key": key, "type": {str: "string", bool: "boolean", list: "string_array"}[kind],
                                "choices": choices, "scope": "user", "requiresReload": True}
                               for key, (_field, kind, choices) in SETTING_FIELDS.items()]}
        if method == "permissions/get":
            if self.posture is None:
                raise ValueError("This RPC host has no permission posture")
            return {"mode": self.posture.get().value}
        if self.harness.phase != "idle":
            raise ValueError("Runtime mutations require an idle session")
        if method == "sessions/branch":
            return await self.branch_session(params)
        if method == "mcp/configure":
            return await self.mcp.configure(params.get("servers"), self.harness)
        if method == "permissions/set":
            if self.posture is None:
                raise ValueError("This RPC host has no permission posture")
            self.posture.set(PermissionMode(params.get("mode")))
            return {"mode": self.posture.get().value}
        if method == "settings/set":
            return self.set_setting(params)
        if method == "resources/reload":
            result = await self.runtime.reload()
            return {"cancelled": result.cancelled}
        if method == "tools/set_active":
            names = params.get("names")
            available = {tool.name for tool in self.harness.runtime.actions.get_all_tools()}
            if not isinstance(names, list) or any(not isinstance(n, str) or n not in available for n in names):
                raise ValueError("Active tool names must refer to loaded tools")
            await self.harness.set_active_tools(names)
            return {"activeTools": self.harness.runtime.actions.get_active_tools()}
        if method == "commands/run":
            text = params.get("text")
            if not isinstance(text, str) or not text.startswith("/") or len(text) > 100_000:
                raise ValueError("Command text must start with / and fit in 100000 characters")
            messages: list[dict[str, str]] = []
            service = CommandDispatchService(lambda: self.runtime.harness, session_runtime=self.runtime)
            command_runtime = self.harness.runtime
            previous_ui = command_runtime.ui
            if self.ui.enabled:
                command_runtime.bind_ui(self.ui)
            self.command_task = asyncio.current_task()
            try:
                result = await service.try_execute(text, CommandSurfaceBindings(
                    emit_text=lambda text: messages.append({"type": "text", "text": text}),
                    emit_error=lambda text: messages.append({"type": "error", "text": text}),
                ))
            finally:
                self.command_task = None
                command_runtime.bind_ui(previous_ui)
            if result.outcome.value == "not_a_command":
                name, _, args = text[1:].partition(" ")
                for template in self.harness.prompt_templates:
                    if template.name == name:
                        return {"outcome": "prompt", "command": name,
                                "prompt": format_prompt_template_invocation(template, parse_command_args(args))}
                for skill in self.harness.skills:
                    if name == "skill:" + skill.name:
                        return {"outcome": "prompt", "command": name,
                                "prompt": format_skill_invocation(skill, args or None)}
            return {"outcome": result.outcome.value, "command": result.command, "messages": messages}
        raise ValueError(f"Unsupported runtime control method: {method}")

    def resources(self) -> dict[str, Any]:
        harness = self.harness
        service = CommandDispatchService(lambda: self.runtime.harness, session_runtime=self.runtime)
        runtime = harness.runtime
        return {
            "commands": [{"name": name, "description": description} for name, description in service.list_commands()],
            "tools": [{"name": tool.name, "description": tool.description} for tool in runtime.actions.get_all_tools()],
            "activeTools": runtime.actions.get_active_tools(),
            "skills": [{"name": skill.name, "description": skill.description, "filePath": skill.file_path}
                       for skill in harness.skills],
            "prompts": [{"name": prompt.name, "description": prompt.description}
                        for prompt in harness.prompt_templates],
            "projectTrusted": harness.project_trusted,
            "mcp": self.mcp.status()["servers"],
            "extensionErrors": [{"source": str(error.path), "message": str(error.error)} for error in self.extension_errors],
            "extensions": [{"name": extension.name, "source": str(getattr(extension, "resolved_path", ""))}
                           for extension in harness.extension_runner.extensions],
            "hooks": [{"extension": str(getattr(extension, "resolved_path", "")),
                       "events": list(extension.handlers)} for extension in harness.extension_runner.extensions],
        }

    def set_setting(self, params: dict[str, Any]) -> dict[str, Any]:
        key = params.get("key")
        if self.settings is None or not isinstance(key, str) or key not in SETTING_FIELDS:
            raise ValueError("Unknown or unavailable settings key")
        field, kind, choices = SETTING_FIELDS[key]
        value = params.get("value")
        if type(value) is not kind or (choices is not None and value not in choices):
            raise ValueError(f"Invalid setting value for {key}")
        if isinstance(value, list) and (len(value) > 256 or any(not isinstance(v, str) or not v or len(v) > 4096 for v in value)):
            raise ValueError("Setting must be a bounded list of non-empty strings")
        if isinstance(value, str) and (not value or len(value) > 4096 or "\x00" in value):
            raise ValueError("Setting must be a bounded, non-empty string")
        getattr(self.settings, "set_" + field)(value)
        return {"key": key, "value": getattr(self.settings, "get_" + field)(), "requiresReload": True}

    async def auth_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.auth is None:
            raise ValueError("This RPC host has no authentication storage")
        provider = params.get("provider")
        if method == "auth/status":
            providers = {provider} if isinstance(provider, str) else set(self.auth.list())
            if self.model_registry is not None and provider is None:
                providers.update(model.provider for model in self.model_registry.get_all())
            statuses = []
            for name in sorted(providers):
                status = await self.auth.get_auth_status(name)
                statuses.append({"provider": name, "configured": status.configured, "source": status.source})
            return {"providers": statuses}
        if self.harness.phase != "idle":
            raise ValueError("Authentication changes require an idle session")
        if not isinstance(provider, str) or not provider or len(provider) > 128:
            raise ValueError("Provider id is required")
        if method == "auth/set_api_key":
            key = params.get("key")
            if not isinstance(key, str) or not key or len(key) > 16384 or any(c in key for c in "\r\n\x00"):
                raise ValueError("A non-empty provider API key is required")
            await self.auth.set_api_key(provider, key)
        elif method == "auth/logout":
            await self.auth.logout(provider)
        elif method == "auth/login":
            if not self.ui.enabled:
                raise ValueError("OAuth requires the RPC dialog client")
            from aelix_ai.oauth.types import OAuthLoginCallbacks

            async def on_auth(info: Any) -> None:
                reply = await self.ui.request("oauth", url=info.url, instructions=info.instructions)
                if reply.get("cancelled"):
                    raise ValueError("Authentication cancelled")

            async def on_prompt(prompt: Any) -> str:
                value = await self.ui.input(prompt.message, prompt.placeholder)
                if value is None:
                    raise ValueError("Authentication cancelled")
                return value

            async def on_select(prompt: Any) -> str | None:
                reply = await self.ui.request("oauthSelect", title=prompt.message,
                    options=[{"id": option.id, "label": option.label} for option in prompt.options])
                value = reply.get("value")
                return value if value in [option.id for option in prompt.options] else None

            await self.auth.login(provider, OAuthLoginCallbacks(on_auth=on_auth, on_prompt=on_prompt,
                on_select=on_select, on_progress=lambda text: self.ui.emit({"type": "extension_ui_request",
                    "method": "authProgress", "message": text})))
        else:
            raise ValueError("Unsupported authentication action")
        return await self.auth_action("auth/status", {"provider": provider})

    def bind_progress(self, harness: Any) -> None:
        self.unbind_progress()
        runtime = getattr(harness, "runtime", None)
        bus = getattr(runtime, "event_bus", None)
        if bus is None:
            return
        fields = ("id", "profile", "state", "current_tool", "elapsed_ms", "tokens", "cost", "model", "provider")
        for channel in ("subagent_start", "subagent_tool", "subagent_end"):
            def receive(value: Any, event: str = channel) -> None:
                self.emit({"type": "aelix_subagent", "event": event,
                           "data": {key: getattr(value, key, None) for key in fields}})
            self.subscriptions.append(bus.on(channel, receive))

    def unbind_progress(self) -> None:
        for unsubscribe in self.subscriptions:
            unsubscribe()
        self.subscriptions.clear()

    async def agent_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.agents is None:
            raise ValueError("Agent profile service is unavailable")
        name = params.get("name")
        if method == "agents/list":
            profiles = self.agents.list()
            return {"profiles": [{"name": profile.name, "description": profile.description,
                     "scope": profile.scope, "filePath": profile.file_path} for profile in profiles.profiles],
                    "diagnostics": [asdict(diagnostic) for diagnostic in profiles.diagnostics]}
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("Agent profile name is required")
        if method == "agents/show":
            return {"profile": asdict(self.agents.show(name))}
        if self.harness.phase != "idle":
            raise ValueError("Agent profile changes require an idle session")
        if method == "agents/use":
            message = await self.agents.use(None if name == "--none" else name, harness=self.harness)
            return {"message": message}
        if method != "agents/run":
            raise ValueError("Unsupported agent profile action")
        task = params.get("task")
        if not isinstance(task, str) or not task or len(task) > 100_000:
            raise ValueError("Agent task must be a non-empty bounded string")
        runtime = self.harness.runtime.subagents
        if runtime is None:
            raise ValueError("Subagents are disabled; explicitly enable agent delegation")
        resolved = runtime.resolve_profile(name, allow_project=params.get("allowProject") is True)
        command_runtime = self.harness.runtime
        previous_ui = command_runtime.ui
        if self.ui.enabled:
            command_runtime.bind_ui(self.ui)
        self.command_task = asyncio.current_task()
        try:
            result = asdict(await runtime.spawn(resolved, task))
        finally:
            self.command_task = None
            command_runtime.bind_ui(previous_ui)
        for key in ("details", "tool_trail"):
            text = result.get(key)
            if isinstance(text, str) and len(text) > 100_000:
                result[key] = text[:100_000]
                result[key + "Truncated"] = True
        return {"result": result}

    async def branch_session(self, params: dict[str, Any]) -> dict[str, Any]:
        original = self.harness.session_file
        if not original or self.runtime.session is None:
            raise ValueError("No persisted session is available to branch")
        kind = params.get("kind")
        if kind not in {"fork", "clone"}:
            raise ValueError("Branch kind must be fork or clone")
        entry = params.get("entryId")
        if kind == "clone":
            entry = await self.runtime.session.get_leaf_id()
        if not isinstance(entry, str) or not entry:
            raise ValueError("A conversation entry is required to branch")
        try:
            result = await self.runtime.fork(entry, **({"position": "at"} if kind == "clone" else {}))
            if result.cancelled:
                return {"cancelled": True}
            return {"cancelled": False, "sessionFile": self.harness.session_file,
                    "title": self.harness.session_name}
        finally:
            if self.harness.session_file != original:
                await asyncio.shield(self.runtime.switch_session(original))
