"""Fork children retain parent policy without sharing parent plugin lifecycle."""

from types import SimpleNamespace

import pytest

from kohakuterrarium import Agent
from kohakuterrarium.core.config.load import AgentConfig
from kohakuterrarium.core.conversation.context_snapshot import capture_context
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.utils.file_guard import FileReadState
from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.fork_policy import ForkToolPolicy
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.builtins.plugins.budget.plugin import BudgetPlugin
from kohakuterrarium.builtins.plugins.permgate.plugin import PermGatePlugin
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin


class ForkableScript(ScriptedLLM):
    def fork(self):
        return ForkableScript(self.script)


@pytest.mark.asyncio
@pytest.mark.parametrize("forked", [True, False])
@pytest.mark.parametrize("policy_kind", ["sandbox", "permgate"])
async def test_fork_read_obeys_parent_policy_without_changing_isolated_runs(
    tmp_path, forked, policy_kind
):
    secret = tmp_path / "secret.txt"
    secret.write_text("private-fork-policy-canary", encoding="utf-8")
    registry = Registry()
    registry.register_tool(ReadTool())
    llm = ForkableScript([f"[/read]\n@@path={secret}\n[read/]", "done"])
    parent = PluginManager()
    policy = (
        SandboxPlugin(fs_read="deny")
        if policy_kind == "sandbox"
        else PermGatePlugin(gated_tools=["read"])
    )
    parent.register(policy)
    router = ReplyRouter("deny")
    parent_context = PluginContext(
        agent_name="parent",
        working_dir=tmp_path,
        _host_agent=SimpleNamespace(output_router=router),
    )
    await parent.load_all(parent_context)
    loaded_context = policy._context
    conversation = Conversation()
    conversation.append("user", "parent conversation must not change")
    before = conversation.to_json()
    manager = SubAgentManager(registry, llm, working_dir=tmp_path)
    manager._parent_plugins = parent
    manager._capture_context = lambda: capture_context(conversation)
    ledger = FileReadState()
    context = ToolContext("parent", None, tmp_path, file_read_state=ledger)
    manager._parent_executor = SimpleNamespace(_build_tool_context=lambda: context)
    manager.register(
        SubAgentConfig(
            name="child", tools=["read"], model="parent", fork_context=forked
        )
    )

    job_id = await manager.spawn(
        "child", "Read the file", background=False, audience="user"
    )
    child = manager._jobs[job_id].subagent
    transcript = child.conversation.to_json()
    if forked:
        assert "private-fork-policy-canary" not in transcript
        assert (
            "SandboxViolation" if policy_kind == "sandbox" else "blocked by user"
        ) in transcript
        assert ledger.get(str(secret)) is None
        assert child._build_tool_context().file_read_state.get(str(secret)) is None
    else:
        assert "private-fork-policy-canary" in transcript
    assert conversation.to_json() == before
    assert llm.call_count == 0
    assert parent.is_enabled(policy_kind)
    assert policy._context is loaded_context


class ReplyRouter:
    def __init__(self, action):
        self.action = action
        self.events = []

    async def emit_and_wait(self, event, timeout_s=None):
        self.events.append(event)
        return SimpleNamespace(action_id=self.action)


@pytest.mark.asyncio
async def test_permgate_private_approvals_live_options_and_missing_output(tmp_path):
    router = ReplyRouter("allow_session")
    host = SimpleNamespace(output_router=router, controller=object())
    parent = PluginManager()
    policy = PermGatePlugin(gated_tools=["read"])
    parent.register(policy)
    await parent.load_all(PluginContext(_host_agent=host, working_dir=tmp_path))
    context = policy._context
    gate = ForkToolPolicy(parent)
    await gate.check({"path": "a"}, tool_name="read")
    await gate.check({"path": "b"}, tool_name="read")
    assert len(router.events) == 1
    assert policy._session_approvals == set()
    assert policy._context is context
    assert policy._context.host_agent is host

    router.action = "deny"
    with pytest.raises(PluginBlockError, match="blocked by user"):
        await ForkToolPolicy(parent).check({"path": "a"}, tool_name="read")
    await gate.check({"url": "https://example.com"}, tool_name="web_fetch")
    policy.set_options({"gated_tools": ["read", "web_fetch"]})
    with pytest.raises(PluginBlockError, match="blocked by user"):
        await gate.check({"url": "https://example.com"}, tool_name="web_fetch")
    assert policy._session_approvals == set()
    policy._context = None
    with pytest.raises(PluginBlockError, match="unavailable"):
        await ForkToolPolicy(parent).check({}, tool_name="read")
    policy.set_options({"allowlist": ["read"]})
    await ForkToolPolicy(parent).check({}, tool_name="read")


class ParentObserver(BasePlugin):
    name = "parent-observer"

    def __init__(self):
        super().__init__()
        self.events = []

    async def pre_tool_execute(self, args, **kwargs):
        self.events.append("parent budget/tool hook")
        raise PluginBlockError("parent budget exhausted")

    async def pre_llm_call(self, messages, **kwargs):
        self.events.append("parent context injection")
        return messages

    async def on_unload(self):
        self.events.append("parent unload")


@pytest.mark.asyncio
@pytest.mark.parametrize("read_policy", ["deny", "broad"])
async def test_agent_build_wires_fork_policy_before_start(tmp_path, read_policy):
    secret = tmp_path / "secret.txt"
    secret.write_text("agent-build-secret-canary", encoding="utf-8")
    sandbox = SandboxPlugin(fs_read=read_policy)
    observer = ParentObserver()
    budget = BudgetPlugin(tool_call_budget={"hard": 1})
    budget.budgets.tool_call.consume(1)
    agent = await Agent.build(
        AgentConfig(name="parent", default_plugins=[], tool_format="bracket"),
        llm=ForkableScript([f"[/read]\n@@path={secret}\n[read/]", "done"]),
        tools=[ReadTool()],
        plugins=[sandbox, observer, budget],
        pwd=tmp_path,
        io="headless",
    )
    before = agent.controller.conversation.to_json()
    try:
        agent.subagent_manager.register(
            SubAgentConfig(
                name="child", tools=["read"], model="parent", fork_context=True
            )
        )
        job = await agent.subagent_manager.spawn(
            "child", "read", background=False, audience="user"
        )
        child = agent.subagent_manager._jobs[job].subagent
        transcript = child.conversation.to_json()
        if read_policy == "deny":
            assert "agent-build-secret-canary" not in transcript
            assert "SandboxViolation" in transcript
        else:
            assert "agent-build-secret-canary" in transcript
        assert budget.budgets.tool_call.used == 1
        assert (
            agent.executor._build_tool_context().file_read_state.get(str(secret))
            is None
        )
        assert observer.events == []
        assert agent.controller.conversation.to_json() == before
        assert agent.plugins.is_enabled("sandbox")
        assert sandbox._enabled
    finally:
        await agent.stop()


class Rewriter(BasePlugin):
    name = "rewrite"

    async def pre_tool_execute(self, args, **kwargs):
        return {"path": "secret.txt"}


@pytest.mark.asyncio
async def test_final_arguments_live_selection_and_child_cannot_loosen_parent(tmp_path):
    (tmp_path / "secret.txt").write_text("must-not-leak", encoding="utf-8")
    parent = PluginManager()
    sandbox = SandboxPlugin(fs_read="broad")
    parent.register(sandbox)
    observer = ParentObserver()
    parent.register(observer)
    child = PluginManager()
    child.register(SandboxPlugin(backend="off"))
    child.register(Rewriter())
    tool = ReadTool()
    gate = ForkToolPolicy(parent)
    execute = child.wrap_method(
        "pre_tool_execute",
        "post_tool_execute",
        gate.wrap(tool.execute, tool_name="read"),
        extra_kwargs={"tool_name": "read"},
    )
    context = ToolContext("child", None, tmp_path)
    assert (
        "must-not-leak"
        in (await execute({"path": "public.txt"}, context=context)).output
    )
    sandbox.set_options({"fs_deny": [str(tmp_path / "secret.txt")]})
    with pytest.raises(PluginBlockError, match="path is denied"):
        await execute({"path": "public.txt"}, context=context)
    parent.disable("sandbox")
    assert (
        "must-not-leak"
        in (await execute({"path": "public.txt"}, context=context)).output
    )
    parent.enable("sandbox")
    child.disable("sandbox")
    with pytest.raises(PluginBlockError, match="path is denied"):
        await execute({"path": "public.txt"}, context=context)
    await child.unload_all()
    assert observer.events == []
    assert parent.is_enabled("sandbox")
    with pytest.raises(PluginBlockError, match="path is denied"):
        await gate.check({"path": "secret.txt"}, tool_name="read", context=context)


class DeclaredPathTool(ReadTool):
    path_arguments = {"media": "read"}

    @property
    def tool_name(self) -> str:
        return "pkg_media"


@pytest.mark.asyncio
async def test_inherited_sandbox_checks_declared_path_arguments(tmp_path):
    parent = PluginManager()
    parent.register(SandboxPlugin(fs_read="workspace"))
    tool = DeclaredPathTool()
    gate = ForkToolPolicy(parent)
    execute = gate.wrap(tool.execute, tool_name="pkg_media", tool=tool)
    context = ToolContext("child", None, tmp_path)
    with pytest.raises(PluginBlockError, match="outside working directory"):
        await execute({"path": "x", "media": "../escape.txt"}, context=context)


class BrokenPolicy(BasePlugin):
    inherit_fork_policy = True

    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def pre_tool_execute(self, args, **kwargs):
        if self.mode == "raise":
            raise ValueError("invalid policy")
        if self.mode == "mutate":
            args["path"] = "different"
        if self.mode == "return":
            return {"path": "different"}
        return args


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "mutate", "return", "unchanged"])
async def test_generic_opt_in_policies_fail_closed_and_cannot_mutate_arguments(mode):
    parent = PluginManager()
    parent.register(BrokenPolicy(mode))
    args = {"path": "original"}
    gate = ForkToolPolicy(parent)
    if mode == "unchanged":
        await gate.check(args, tool_name="read")
    else:
        with pytest.raises(PluginBlockError):
            await gate.check(args, tool_name="read")
    assert args == {"path": "original"}
    await ForkToolPolicy(None).check(args, tool_name="read")


class PolicyEditingRouter(ReplyRouter):
    def __init__(self, parent, edit):
        super().__init__("allow_once")
        self.parent = parent
        self.edit = edit

    async def emit_and_wait(self, event, timeout_s=None):
        sandbox = self.parent.get_plugin("sandbox")
        if self.edit == "options":
            sandbox.set_options({"fs_read": "deny"})
        else:
            self.parent.enable("sandbox")
        return await super().emit_and_wait(event, timeout_s)


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["options", "selection"])
async def test_parent_policy_change_during_approval_blocks_execution(tmp_path, edit):
    parent = PluginManager()
    parent.register(SandboxPlugin(fs_read="broad" if edit == "options" else "deny"))
    if edit == "selection":
        parent.disable("sandbox")
    parent.register(PermGatePlugin(gated_tools=["read"]))
    router = PolicyEditingRouter(parent, edit)
    await parent.load_all(
        PluginContext(_host_agent=SimpleNamespace(output_router=router))
    )
    (tmp_path / "secret.txt").write_text("approval-race-canary", encoding="utf-8")
    ledger = FileReadState()
    context = ToolContext("child", None, tmp_path, file_read_state=ledger)
    execute = ForkToolPolicy(parent).wrap(ReadTool().execute, tool_name="read")
    with pytest.raises(PluginBlockError):
        await execute({"path": "secret.txt"}, context=context)
    assert ledger.get(str(tmp_path / "secret.txt")) is None
