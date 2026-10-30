# core/agent

`Agent`: the orchestrator that wires LLM, controller, executor, I/O, plugins
and sessions. The class is assembled in `assembly.py` from per-concern mixins;
`__init__.py` is a lazy facade, so `from kohakuterrarium.core.agent import Agent`
keeps working while leaf modules stay importable without building the class.

## Layout

```
agent/
├── __init__.py            lazy facade: Agent, run_agent
├── assembly.py            class Agent(<mixins>) + __init__ ordering + run_agent
├── construct.py           AgentConstructMixin   Agent.build / Agent.from_path
├── extensions.py          AgentExtensionsMixin  add_tool / add_plugin at runtime
├── startup.py             AgentStartupMixin     start(), module wiring, MCP, compaction, plugins
├── session.py             AgentSessionMixin     attach_session_store, session_info, prompt, state
├── lifecycle.py           AgentLifecycleMixin   stop / pause / resume
├── model.py               AgentModelMixin       switch_model, llm_identifier
├── observability.py       session-info + observer wiring helpers
├── workspace.py           WorkspaceController   per-agent working directory
├── mcp.py                 MCP init + prompt wiring
├── helpers.py             attach_session_helpers
├── turn/                  driving turns
│   ├── run.py             AgentTurnMixin        typed run / run_stream
│   ├── drive.py           AgentDriveMixin       run_forever, inject_input / inject_event
│   ├── interrupt.py       AgentInterruptMixin   interrupt, cancel job, promote to background
│   ├── event_loop.py      AgentEventLoopMixin   single event consumer, batching
│   ├── handlers.py        AgentHandlersMixin    event dispatch, controller loop
│   ├── mid_turn.py        mid-turn user input
│   ├── input.py           input polling + startup trigger
│   ├── pre_dispatch.py    pre-dispatch plugin chain
│   ├── budget_recovery.py provider recovery hooks
│   └── output_wiring.py   turn-end output wiring
├── tools/                 tools as the agent sees them
│   ├── dispatch.py        AgentToolsMixin       tool / sub-agent dispatch, completion
│   ├── runtime_tools.py   runtime tool registration
│   ├── gate.py            AgentToolGateMixin
│   ├── options.py         ToolOptions
│   ├── native_tools.py    NativeToolOptions
│   ├── plugin_tools.py    AgentPluginToolsMixin
│   ├── plugin_options.py  PluginOptions
│   └── metrics.py         tool metrics
└── history/               conversation history
    ├── messages.py        AgentMessagesMixin    edit / regenerate / rewind
    ├── message_history.py replay and branch history
    ├── raw_history.py     raw history protocol
    ├── selection.py       model / plugin selection tracking
    └── compact.py         AgentCompactMixin     compaction + resume state
```

`AgentInitMixin` (component construction) lives in `bootstrap/agent_init.py`.

## Rules

1. **One part, one concern**, named `Agent<Concern>Mixin`.
2. **A part owns its state** through `_init_<concern>_state()`; `Agent.__init__`
   only calls the hooks in order, then builds the components.
3. **Parts new to the class sit first in the base list** so they keep the
   precedence the methods had when they were defined on `Agent` itself.
4. **Parts never import `assembly`** at run time.
5. **Package import stays light.** Modules outside this package import its
   leaves (for example `turn.budget_recovery`), and `assembly` depends on those
   modules, so `__init__` must not import `assembly` eagerly.
6. **Tests mirror the tree** under `tests/unit/core/agent/`. Patch the module
   that uses a name (`startup.init_plugins`, `turn.input.asyncio.sleep`), not
   the facade.

## Constructor order

`config, strict` → `_init_event_state` → `_init_model_state` → `_init_session_state`
→ `_init_interrupt_state` → session helpers and branch state → loader and
termination checker → component construction from `AgentInitMixin`.
