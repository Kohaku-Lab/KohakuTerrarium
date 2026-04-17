# cli/

`kt` command dispatcher. One handler file per subcommand, with
`cli/__init__.py:main()` as the single argparse + dispatch entry point.

## Files

| File | Subcommand(s) |
|------|---------------|
| `__init__.py` | `main()` — argparse setup, `COMMANDS` dispatch table |
| `run.py` | `kt run` — launch an agent from a config folder (rich CLI / plain / TUI modes) |
| `resume.py` | `kt resume` — resume an agent or terrarium from a `.kohakutr` session file |
| `auth.py` | `kt login` — API key / Codex OAuth flow for a backend |
| `packages.py` | `kt list` / `kt info` / `kt install` / `kt uninstall` / `kt update` / `kt edit` |
| `model.py` | `kt model list/default/show` — compatibility wrapper that delegates to `config_cli` |
| `memory.py` | `kt embedding` / `kt search` — offline embedding build + session memory search |
| `serve.py` | `kt serve start/stop/status` — manage a detached web server process (PID + state files under `~/.kohakuterrarium/run/`) |
| `config.py` | `kt config` command group — unified LLM profile / backend / API-key / MCP management |
| `config_mcp.py` | MCP subcommand helpers shared between `kt config mcp` and interactive prompts |
| `extension.py` | `kt extension list` / `info` — inspect installed package extension modules (tools, plugins, presets) |
| `mcp.py` | `kt mcp list` — list MCP servers declared in an agent config |
| `version.py` | `kt --version` report (Python, git, platform, install source) |

The top-level `kt run` entry point invokes `cli/__init__.py:main()` via the
`pyproject.toml` console script.

## Dependency direction

Imported only as the process entry point. Imports nearly everything it
drives: `core/agent`, `serving/web` (web + desktop), `terrarium/cli`,
`session/resume`, `session/store`, `llm/*`, `packages`, `builtins/cli_rich`,
`utils/logging`.

Nothing inside the framework imports `cli/` — it is the top of the graph.

## Key entry points

- `cli/__init__.py:main()` — argparse + dispatch
- `cli/run.py:run_agent_cli()` — standalone agent launcher
- `cli/resume.py:resume_cli()` — session resume
- `cli/serve.py:serve_cli()` — detached web server control
- `cli/config.py:config_cli()` — unified profile / backend / key config

## Notes

- `web` and `app` commands delegate to `serving/web.py:run_web_server` /
  `run_desktop_app` (Briefcase and pywebview integration).
- `__run-server` is a hidden internal subcommand used by `kt serve start`
  to spawn the detached worker process with the right environment.
- `_dispatch_*` helpers in `__init__.py` exist only to adapt between the
  argparse `Namespace` and the handler signatures; the real logic is in
  the per-command modules.
- `@package/path` syntax (e.g. `@kt-defaults/creatures/swe`) is resolved
  via `packages.resolve_package_path` before dispatch.

## See also

- `../api/README.md` — the HTTP server `kt serve` / `kt web` launches
- `../serving/README.md` — `KohakuManager` + desktop app wiring
- `../terrarium/cli.py` — terrarium subcommand implementations
