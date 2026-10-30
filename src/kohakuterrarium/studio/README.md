# studio

The management framework above the Terrarium engine. The web dashboard, the
desktop app and the `kt` CLI are all adapters over Studio: it is the shared
Python surface they delegate to, not a UI.

`Studio` (in `studio.py`) groups the work into six namespaces. Each namespace
method stays thin so validation, persistence and runtime behaviour keep one
implementation in the owning sub-package.

```python
async with Studio() as s:
    sess = await s.sessions.start_creature("@kt-biome/creatures/general")
    packages = s.catalog.packages.list()
    profiles = s.identity.llm.list_profiles()
    saved = s.persistence.list()
```

## Layout

Families of files are sub-packages with the family prefix dropped; unit tests
mirror the tree under `tests/unit/studio/` where a test maps to one module.

```
studio/
├── studio.py                  Studio facade and the six namespaces
├── facade_sessions.py  facade_persistence.py
├── hooks.py  nodes.py  files.py  deploy.py  _runtime.py
├── catalog/                   packages, creatures, modules, builtins, introspection
│   └── packages/              packages (installed list), aggregate, remote, scan
├── identity/                  settings, API keys, UI prefs, LLM and account identities
│   ├── llm/                   backends, default, native_tools, profiles
│   ├── codex/                 account, oauth
│   ├── grok/                  account, subscription
│   ├── mcp/                   servers, usage
│   └── drive_settings/        settings, io
├── sessions/                  live sessions on the engine
│   ├── creature/              chat, command, ctl, model, modules, plugins, state, subagents
│   ├── drives/                drives operations, serde
│   ├── memory/                build, search
│   ├── cluster/               fold, paths
│   ├── live/  summary/        live run tracking, session summaries
│   └── lifecycle.py  registry.py  topology.py  wiring.py  stop.py  find.py  ...
├── persistence/               saved sessions: store, fork, history, resume, artifacts, viewer
├── editors/                   creature / module / workspace editing
│   ├── codegen/               common, init, io, pending, plugin, subagent, tool, trigger
│   ├── workspace/             fs, manifest, wiring
│   ├── yaml/                  creature, manifest
│   └── creatures_crud.py  modules_crud.py  templates.py  validators.py  ...
└── attach/                    attach policies and I/O streams
    ├── pty/                   posix, windows, router
    ├── workspace/             files, watch
    └── io.py  io_cluster.py  input_ops.py  log.py  observer.py  trace.py  policies.py
```

## Dependency direction

- Imports: `terrarium/` (the engine and `TerrariumService`), `session/`,
  `core/`, `llm/`, `modules/`, `packages/`, `utils/`, and `laboratory/`
  (protocols for multi-node operations).
- Imported by: `api/`, `cli/` and `laboratory/`.
- Nothing below `api/` knows about users, tokens or hosts; auth lives only in
  `api/auth/`.

## See also

- `../terrarium/README.md`: the engine Studio delegates to.
- `../api/README.md`: the HTTP layer over Studio.
