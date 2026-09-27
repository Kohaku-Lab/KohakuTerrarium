"""Standalone task subagents built from KT factories without a parent Creature."""

from dataclasses import fields
from pathlib import Path

import yaml

from kohakuterrarium.bootstrap.llm import coerce_llm_provider
from kohakuterrarium.bootstrap.tools import create_tool
from kohakuterrarium.core.config_types import AgentConfig, ToolConfigItem
from kohakuterrarium.core.execution_context import ExecutionBinding
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.utils.file_guard import FileReadState


class SubagentHost:
    """Own one independent subagent's provider, tools and cancellation scope."""

    def __init__(self, reference, workspace, store, history, *, llm=None):
        path = Path(resolve_any_path(reference))
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Subagent configuration must be an object")
        known = {item.name for item in fields(SubAgentConfig)} | {"llm"}
        if set(raw) - known:
            raise ValueError(f"Unknown subagent settings: {sorted(set(raw) - known)}")
        if raw.get("interactive"):
            raise ValueError("MCP subagents are one-shot; interactive is unsupported")
        data = dict(raw)
        selector = data.pop("llm", None) or data.get("model") or "default"
        # The target owns this provider; bypass global per-child model defaults.
        data["model"] = "inherit"
        registry = Registry()
        loader = ModuleLoader(path.parent)
        names = []
        for item in data.get("tools", []):
            entry = {"name": item} if isinstance(item, str) else dict(item)
            if "config" in entry:
                entry["options"] = entry.pop("config")
            if "class" in entry:
                entry["class_name"] = entry.pop("class")
            spec = ToolConfigItem(**entry)
            if spec.name in names:
                raise ValueError(f"Duplicate subagent tool: {spec.name}")
            registry.register_tool(create_tool(spec, loader, strict=True))
            names.append(spec.name)
        data["tools"] = names
        self.config = SubAgentConfig.from_dict(data)
        self.llm = coerce_llm_provider(
            llm, AgentConfig(name=self.config.name, llm_profile=selector)
        )
        self.manager = SubAgentManager(
            registry,
            self.llm,
            job_store=store,
            agent_path=path.parent,
            strict=True,
            working_dir=workspace,
        )
        self.context = Executor(
            binding=ExecutionBinding(
                ToolContext(
                    agent_name=self.config.name,
                    session=None,
                    working_dir=workspace,
                    file_read_state=FileReadState(),
                ),
                PluginManager(),
            )
        )
        self.manager._parent_executor = self.context
        self.manager._on_tool_activity = (
            lambda name, kind, tool, detail, job, extra=None: history.append(
                kind, tool=tool, detail=detail, job_id=job, metadata=extra or {}
            )
        )
        self.manager.register(self.config)
        self.job_id = None
        self._closed = False

    async def start(self, job_id, prompt):
        self.job_id = job_id
        await self.manager.spawn(self.config.name, prompt, job_id=job_id)
        child = self.manager.get_live_subagent(job_id)
        self.context.binding.plugins = child.plugins or PluginManager()

    async def wait(self):
        return await self.manager.wait_for(self.job_id)

    async def cancel(self):
        await self.manager.cancel_all()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self.manager.cancel_all()
        await self.llm.close()

    async def send(self, content):
        return await self.manager.send_to_subagent(content, job_id=self.job_id)

    def conversation(self):
        return self.manager.get_subagent_conversation(self.job_id)
