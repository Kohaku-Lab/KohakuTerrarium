# cli/

`kt` command dispatcher. One handler file per subcommand, with a lightweight
`cli/__init__.py:main()` for common startup paths and `_main.py` for the full
argparse command catalog.

## Files

| File            | Subcommand(s)                                                                                                           |
| --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`    | Lightweight `main()` dispatch for version, desktop, web, CLI, and TUI startup paths                                      |
| `_main.py`       | Full argparse setup and `COMMANDS` dispatch table for the complete command catalog                                      |
| `run.py`         | `kt run`: launch a creature or recipe through the Terrarium engine (rich CLI / TUI modes)                               |
| `resume.py`      | `kt resume`: resume an agent or terrarium from a `.kohakutr` session file                                               |
| `doctor.py`      | `kt doctor`: pre-flight environment + config validation (wraps `kohakuterrarium.validate`)                              |
| `auth.py`        | `kt login`: API key / Codex OAuth flow for a backend                                                                    |
| `packages.py`    | `kt list` / `kt info` / `kt install` / `kt uninstall` / `kt update` / `kt edit`                                          |
| `marketplace.py` | `kt marketplace`: list / add / remove / refresh / search / info over marketplace sources                                |
| `model.py`       | `kt model list/default/show`: compatibility wrapper that delegates to `config_cli`                                      |
| `memory.py`      | `kt embedding` / `kt search`: offline embedding build + session memory search                                           |
| `serve.py`       | `kt serve start/stop/status`: manage a detached web server process (PID + state files under `~/.kohakuterrarium/run/`)  |
| `config.py`      | `kt config` command group: unified LLM profile / backend / API-key / MCP management (+ `config_prompts.py`)             |
| `identity_*.py`  | Settings backends behind `kt config` (LLM, keys, MCP registry, codex, general settings)                                  |
| `extension.py`   | `kt extension list` / `info`: inspect installed package extension modules (tools, plugins, presets)                     |
| `admin.py`       | Auth-layer administration (admin token, users) (+ `admin_qr.py`)                                                         |
| `service.py`     | Multi-node service runner (+ `lab_client.py`)                                                                            |
| `self_update.py` | Launcher-managed self-update                                                                                             |
| `version.py`     | `kt --version` report (Python, git, platform, install source)                                                            |

The top-level `kt run` entry point invokes `cli/__init__.py:main()` via the
`pyproject.toml` console script.

## Dependency direction

Imported only as the process entry point. Imports nearly everything it
drives: `terrarium/engine` (+ `engine_cli` / `engine_rich_cli`),
`serving/web` (web + desktop), `session/resume`, `session/store`, `llm/*`,
`packages/`, `builtins/cli_rich`, `utils/logging`.

Nothing inside the framework imports `cli/`; it is the top of the graph.

## Key entry points

- `cli/__init__.py:main()`: argparse + dispatch
- `cli/run.py:run_agent_cli()`: creature / recipe launcher via the engine
- `cli/resume.py:resume_cli()`: session resume
- `cli/serve.py:serve_cli()`: detached web server control
- `cli/config.py:config_cli()`: unified profile / backend / key config

## Notes

- `web` and `app` commands delegate to `serving/web.py:run_web_server` /
  `run_desktop_app` (Briefcase and pywebview integration).
- `__run-server` is a hidden internal subcommand used by `kt serve start`
  to spawn the detached worker process with the right environment.
- `_dispatch_*` helpers in `__init__.py` exist only to adapt between the
  argparse `Namespace` and the handler signatures; the real logic is in
  the per-command modules.
- `@package/path` syntax (e.g. `@kt-biome/creatures/swe`) passes through
  the dispatchers verbatim; resolution happens once, inside the config
  loaders (`core/config.py:load_agent_config` /
  `terrarium/config.py:load_terrarium_config`), for every entry point.

## See also

- `../api/README.md`: the HTTP server `kt serve` / `kt web` launches
- `../serving/README.md`: static frontend / desktop app launcher
- `run.py`: agent / terrarium execution implementation
