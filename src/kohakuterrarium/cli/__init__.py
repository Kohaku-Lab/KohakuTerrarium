"""KohakuTerrarium CLI — command dispatch and argument parsing."""

import argparse
import importlib.metadata
import os
import platform
import site
import subprocess
import sys
from pathlib import Path

from kohakuterrarium import __version__
from kohakuterrarium.packages import resolve_package_path
from kohakuterrarium.serving.web import run_desktop_app, run_web_server
from kohakuterrarium.terrarium.cli import (
    add_terrarium_subparser,
    handle_terrarium_command,
)

from kohakuterrarium.cli.auth import login_cli
from kohakuterrarium.cli.extension import extension_info_cli, extension_list_cli
from kohakuterrarium.cli.mcp import mcp_list_cli
from kohakuterrarium.cli.memory import embedding_cli, search_cli
from kohakuterrarium.cli.model import model_cli
from kohakuterrarium.cli.packages import (
    edit_cli,
    install_cli,
    list_cli,
    show_agent_info_cli,
    uninstall_cli,
)
from kohakuterrarium.cli.resume import resume_cli
from kohakuterrarium.cli.run import run_agent_cli


def _detect_install_source() -> str:
    """Best-effort detection of how KohakuTerrarium is installed."""
    try:
        dist = importlib.metadata.distribution("KohakuTerrarium")
    except importlib.metadata.PackageNotFoundError:
        return "source checkout (not installed as a distribution)"

    direct_url = None
    try:
        direct_url = dist.read_text("direct_url.json")
    except FileNotFoundError:
        direct_url = None

    if direct_url:
        direct_url_lower = direct_url.lower()
        if '"editable": true' in direct_url_lower:
            return "editable install"
        if '"url": "file://' in direct_url_lower:
            return "local path install"
        if '"vcs_info"' in direct_url_lower:
            return "vcs install"

    package_path = Path(__file__).resolve()
    site_roots = []
    try:
        site_roots.extend(Path(p).resolve() for p in site.getsitepackages())
    except Exception:
        pass
    user_site = site.getusersitepackages()
    if user_site:
        site_roots.append(Path(user_site).resolve())

    if any(root in package_path.parents for root in site_roots):
        return "installed distribution"
    return "source checkout"


def _detect_git_revision() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return "n/a"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _format_version_report() -> str:
    package_path = Path(__file__).resolve().parents[1]
    lines = [
        f"KohakuTerrarium {__version__}",
        f"install: {_detect_install_source()}",
        f"package path: {package_path}",
        f"python: {sys.version.splitlines()[0]} ({sys.executable})",
        f"platform: {platform.platform()}",
        f"system: {platform.system()} {platform.release()} ({platform.machine()})",
        f"processor: {platform.processor() or 'unknown'}",
        f"cwd: {Path.cwd()}",
        f"git revision: {_detect_git_revision()}",
    ]

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        lines.append(f"venv: {virtual_env}")

    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="kt",
        description="KohakuTerrarium - Universal Agent Framework",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show KohakuTerrarium version and environment information",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument(
        "agent_path",
        help="Path to agent config folder (e.g., agents/swe-agent)",
    )
    run_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )
    run_parser.add_argument(
        "--session",
        nargs="?",
        const="__auto__",
        default="__auto__",
        help="Session file path (default: auto in ~/.kohakuterrarium/sessions/). Use --no-session to disable.",
    )
    run_parser.add_argument(
        "--no-session",
        action="store_true",
        help="Disable session persistence",
    )
    run_parser.add_argument(
        "--llm",
        default=None,
        help="Override LLM profile (e.g., gpt-5.4, gemini, claude-sonnet-4)",
    )
    run_parser.add_argument(
        "--mode",
        choices=["cli", "plain", "tui"],
        default=None,
        help=(
            "Input/output mode. cli=rich inline (default if TTY), "
            "plain=dumb stdout/stdin, tui=full-screen Textual app"
        ),
    )

    # List command
    list_parser = subparsers.add_parser("list", help="List available agents")
    list_parser.add_argument(
        "--path",
        default="agents",
        help="Path to agents directory",
    )

    # Info command
    info_parser = subparsers.add_parser("info", help="Show agent info")
    info_parser.add_argument(
        "agent_path",
        help="Path to agent config folder",
    )

    # Terrarium command group
    add_terrarium_subparser(subparsers)

    # Resume command
    resume_parser = subparsers.add_parser(
        "resume", help="Resume a session (by name, path, or list recent)"
    )
    resume_parser.add_argument(
        "session",
        nargs="?",
        default=None,
        help="Session name/prefix, full path, or omit to list recent sessions",
    )
    resume_parser.add_argument("--pwd", help="Override working directory")
    resume_parser.add_argument(
        "--last",
        action="store_true",
        help="Resume the most recent session",
    )
    resume_parser.add_argument(
        "--mode",
        choices=["cli", "plain", "tui"],
        default=None,
        help=(
            "Input/output mode. cli=rich inline (default if TTY), "
            "plain=dumb stdout/stdin, tui=full-screen Textual app. "
            "Defaults match `kt run`: cli on a TTY, plain otherwise."
        ),
    )
    resume_parser.add_argument(
        "--llm",
        default=None,
        help="Override LLM profile (e.g., gpt-5.4, gemini, claude-sonnet-4.6)",
    )
    resume_parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    # Login command
    login_parser = subparsers.add_parser("login", help="Authenticate with a provider")
    login_parser.add_argument(
        "provider",
        choices=["codex", "openrouter", "openai", "anthropic", "gemini", "mimo"],
        help="Provider to authenticate with",
    )

    # Install command
    install_parser = subparsers.add_parser(
        "install", help="Install a creature/terrarium package"
    )
    install_parser.add_argument("source", help="Git URL or local path to package")
    install_parser.add_argument(
        "-e",
        "--editable",
        action="store_true",
        help="Install as editable (symlink, like pip -e)",
    )
    install_parser.add_argument("--name", default=None, help="Override package name")

    # Uninstall command
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Remove an installed package"
    )
    uninstall_parser.add_argument("name", help="Package name to remove")

    # Edit command
    edit_parser = subparsers.add_parser(
        "edit", help="Open a creature/terrarium config in editor"
    )
    edit_parser.add_argument(
        "target",
        help="@package/creatures/name or @package/terrariums/name",
    )

    # Embedding command
    embed_parser = subparsers.add_parser(
        "embedding", help="Build embeddings for a session (offline indexing)"
    )
    embed_parser.add_argument("session", help="Session name/prefix or path")
    embed_parser.add_argument(
        "--provider",
        choices=["auto", "model2vec", "sentence-transformer", "api"],
        default="auto",
        help="Embedding provider (default: auto, prefers jina v5 nano)",
    )
    embed_parser.add_argument(
        "--model", default=None, help="Model name (default: provider-dependent)"
    )
    embed_parser.add_argument(
        "--dimensions", type=int, default=None, help="Embedding dimensions (Matryoshka)"
    )

    # Search command
    search_parser = subparsers.add_parser("search", help="Search a session's memory")
    search_parser.add_argument("session", help="Session name/prefix or path")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument(
        "--mode",
        choices=["fts", "semantic", "hybrid", "auto"],
        default="auto",
        help="Search mode (default: auto)",
    )
    search_parser.add_argument("--agent", default=None, help="Filter by agent name")
    search_parser.add_argument(
        "-k", type=int, default=10, help="Max results (default: 10)"
    )

    # Web server command
    web_parser = subparsers.add_parser(
        "web", help="Serve web UI + API (single process)"
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1, use 0.0.0.0 for LAN)",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Bind port (auto-increments if busy)",
    )
    web_parser.add_argument(
        "--dev",
        action="store_true",
        help="API-only mode (run vite dev server separately)",
    )

    # Desktop app command
    app_parser = subparsers.add_parser(
        "app", help="Launch native desktop UI (requires pywebview)"
    )
    app_parser.add_argument(
        "--port", type=int, default=8001, help="Internal server port"
    )

    # Model command
    model_parser = subparsers.add_parser("model", help="Manage LLM profiles")
    model_sub = model_parser.add_subparsers(dest="model_command")
    model_sub.add_parser("list", help="List all profiles and presets")
    model_default_parser = model_sub.add_parser("default", help="Set default model")
    model_default_parser.add_argument("name", help="Model/profile name")
    model_show_parser = model_sub.add_parser("show", help="Show profile details")
    model_show_parser.add_argument("name", help="Model/profile name")

    # Extension command group
    ext_parser = subparsers.add_parser(
        "extension", help="Manage package extension modules"
    )
    ext_sub = ext_parser.add_subparsers(dest="extension_command")
    ext_sub.add_parser("list", help="List all installed extension modules")
    ext_info_parser = ext_sub.add_parser(
        "info", help="Show details of a specific package"
    )
    ext_info_parser.add_argument("name", help="Package name")

    # MCP command group
    mcp_parser = subparsers.add_parser("mcp", help="MCP server management")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")
    mcp_list_parser = mcp_sub.add_parser(
        "list", help="List MCP servers from agent config"
    )
    mcp_list_parser.add_argument(
        "--agent", required=True, help="Path to agent config folder"
    )

    return parser


def _dispatch_run(args: argparse.Namespace) -> int:
    """Handle the 'run' command."""
    agent_path = args.agent_path
    if agent_path.startswith("@"):
        agent_path = str(resolve_package_path(agent_path))
    session = None if args.no_session else args.session
    return run_agent_cli(
        agent_path,
        args.log_level,
        session=session,
        io_mode=args.mode,
        llm_override=args.llm,
    )


def _dispatch_resume(args: argparse.Namespace) -> int:
    """Handle the 'resume' command."""
    return resume_cli(
        args.session,
        args.pwd,
        args.log_level,
        last=args.last,
        io_mode=args.mode,
        llm_override=args.llm,
    )


def _dispatch_terrarium(args: argparse.Namespace) -> int:
    """Handle the 'terrarium' command with @package path resolution."""
    if hasattr(args, "terrarium_path") and args.terrarium_path:
        if args.terrarium_path.startswith("@"):
            args.terrarium_path = str(resolve_package_path(args.terrarium_path))
    return handle_terrarium_command(args)


def _dispatch_embedding(args: argparse.Namespace) -> int:
    """Handle the 'embedding' command."""
    return embedding_cli(args.session, args.provider, args.model, args.dimensions)


def _dispatch_search(args: argparse.Namespace) -> int:
    """Handle the 'search' command."""
    return search_cli(args.session, args.query, args.mode, args.agent, args.k)


def _dispatch_web(args: argparse.Namespace) -> int:
    """Handle the 'web' command."""
    run_web_server(host=args.host, port=args.port, dev=args.dev)
    return 0


def _dispatch_app(args: argparse.Namespace) -> int:
    """Handle the 'app' command."""
    run_desktop_app(port=args.port)
    return 0


def _dispatch_extension(args: argparse.Namespace) -> int:
    """Handle the 'extension' command group."""
    sub = getattr(args, "extension_command", None)
    if sub == "list":
        return extension_list_cli()
    elif sub == "info":
        return extension_info_cli(args.name)
    else:
        # Print help for extension subparser; re-parse to get the parser
        parser = _build_parser()
        parser.parse_args(["extension", "--help"])
        return 0


def _dispatch_mcp(args: argparse.Namespace) -> int:
    """Handle the 'mcp' command group."""
    sub = getattr(args, "mcp_command", None)
    if sub == "list":
        return mcp_list_cli(args.agent)
    else:
        parser = _build_parser()
        parser.parse_args(["mcp", "--help"])
        return 0


# Command dispatch table: command name -> handler function
COMMANDS: dict[str, callable] = {
    "run": _dispatch_run,
    "resume": _dispatch_resume,
    "list": lambda args: list_cli(args.path),
    "info": lambda args: show_agent_info_cli(args.agent_path),
    "terrarium": _dispatch_terrarium,
    "login": lambda args: login_cli(args.provider),
    "install": lambda args: install_cli(args.source, args.editable, args.name),
    "uninstall": lambda args: uninstall_cli(args.name),
    "edit": lambda args: edit_cli(args.target),
    "embedding": _dispatch_embedding,
    "search": _dispatch_search,
    "web": _dispatch_web,
    "app": _dispatch_app,
    "model": lambda args: model_cli(args),
    "extension": _dispatch_extension,
    "mcp": _dispatch_mcp,
}


def main() -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.version:
        print(_format_version_report())
        return 0

    # No command given: launch desktop app (used by Briefcase and double-click)
    if not args.command:
        run_desktop_app()
        return 0

    handler = COMMANDS.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 0
