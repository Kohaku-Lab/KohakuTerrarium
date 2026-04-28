"""``kt config`` dispatcher — argparse wiring for identity sub-commands."""

from kohakuterrarium.cli.auth import login_cli
from kohakuterrarium.cli.identity_backend import (
    add_or_update_cli as _backend_add_cli,
)
from kohakuterrarium.cli.identity_backend import delete_cli as _backend_delete_cli
from kohakuterrarium.cli.identity_backend import list_cli as _backend_list_cli
from kohakuterrarium.cli.identity_keys import delete_cli as _keys_delete_cli
from kohakuterrarium.cli.identity_keys import list_cli as _keys_list_cli
from kohakuterrarium.cli.identity_keys import set_cli as _keys_set_cli
from kohakuterrarium.cli.identity_llm import add_or_update_cli as _llm_add_cli
from kohakuterrarium.cli.identity_llm import default_cli as _llm_default_cli
from kohakuterrarium.cli.identity_llm import delete_cli as _llm_delete_cli
from kohakuterrarium.cli.identity_llm import list_cli as _llm_list_cli
from kohakuterrarium.cli.identity_llm import show_cli as _llm_show_cli
from kohakuterrarium.cli.identity_mcp import add_or_update_cli as _mcp_add_cli
from kohakuterrarium.cli.identity_mcp import delete_cli as _mcp_delete_cli
from kohakuterrarium.cli.identity_mcp import list_cli as _mcp_list_cli
from kohakuterrarium.cli.identity_settings import edit_cli as _settings_edit_cli
from kohakuterrarium.cli.identity_settings import path_cli as _settings_path_cli
from kohakuterrarium.cli.identity_settings import show_cli as _settings_show_cli


def add_config_subparser(subparsers):
    parser = subparsers.add_parser(
        "config",
        help="Manage KohakuTerrarium configuration (providers, presets, keys, MCP)",
    )
    sub = parser.add_subparsers(dest="config_command")

    sub.add_parser("show", help="Show all configuration file paths")
    path_parser = sub.add_parser("path", help="Print the path of a config file")
    path_parser.add_argument("name", nargs="?", default=None)
    edit_parser = sub.add_parser("edit", help="Open a config file in EDITOR")
    edit_parser.add_argument("name", nargs="?", default=None)

    provider_parser = sub.add_parser(
        "provider",
        aliases=["backend"],
        help="Manage LLM providers",
    )
    provider_sub = provider_parser.add_subparsers(dest="config_provider_command")
    provider_sub.add_parser("list", help="List providers")
    p_add = provider_sub.add_parser("add", help="Add or update a provider")
    p_add.add_argument("name", nargs="?", default=None)
    p_edit = provider_sub.add_parser("edit", help="Edit an existing provider")
    p_edit.add_argument("name")
    p_del = provider_sub.add_parser("delete", help="Delete a provider")
    p_del.add_argument("name")

    llm_parser = sub.add_parser(
        "llm",
        aliases=["model", "preset"],
        help="Manage LLM presets",
    )
    llm_sub = llm_parser.add_subparsers(dest="config_llm_command")
    l_list = llm_sub.add_parser("list", help="List presets")
    l_list.add_argument(
        "--all",
        dest="include_builtins",
        action="store_true",
        help="Include built-in presets in the listing",
    )
    l_show = llm_sub.add_parser("show", help="Show preset details")
    l_show.add_argument("name")
    l_add = llm_sub.add_parser("add", help="Add or update a preset")
    l_add.add_argument("name", nargs="?", default=None)
    l_edit = llm_sub.add_parser("edit", help="Edit an existing preset")
    l_edit.add_argument("name")
    l_del = llm_sub.add_parser("delete", help="Delete a preset")
    l_del.add_argument("name")
    l_def = llm_sub.add_parser("default", help="Get or set the default model")
    l_def.add_argument("name", nargs="?", default=None)

    key_parser = sub.add_parser("key", help="Manage stored API keys")
    key_sub = key_parser.add_subparsers(dest="config_key_command")
    key_sub.add_parser("list", help="List providers with key status")
    k_set = key_sub.add_parser("set", help="Set the API key for a provider")
    k_set.add_argument("provider")
    k_set.add_argument("value", nargs="?", default=None)
    k_del = key_sub.add_parser("delete", help="Delete the stored key")
    k_del.add_argument("provider")

    login_parser = sub.add_parser("login", help="Authenticate with a provider")
    login_parser.add_argument("provider")

    mcp_parser = sub.add_parser("mcp", help="Manage MCP servers")
    mcp_sub = mcp_parser.add_subparsers(dest="config_mcp_command")
    mcp_sub.add_parser("list", help="List MCP servers")
    m_add = mcp_sub.add_parser("add", help="Add or update an MCP server")
    m_add.add_argument("name", nargs="?", default=None)
    m_edit = mcp_sub.add_parser("edit", help="Edit an existing MCP server")
    m_edit.add_argument("name")
    m_del = mcp_sub.add_parser("delete", help="Delete an MCP server")
    m_del.add_argument("name")


def _dispatch_provider(args):
    sub = getattr(args, "config_provider_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _backend_list_cli()
        case "add" | "edit":
            return _backend_add_cli(name)
        case "delete":
            return _backend_delete_cli(name)
    print("Usage: kt config provider")
    return 1


def _dispatch_llm(args):
    sub = getattr(args, "config_llm_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _llm_list_cli(
                include_builtins=getattr(args, "include_builtins", False)
            )
        case "show":
            return _llm_show_cli(name) if name else (print("name required") or 1)
        case "add" | "edit":
            return _llm_add_cli(name)
        case "delete":
            return _llm_delete_cli(name) if name else (print("name required") or 1)
        case "default":
            return _llm_default_cli(name)
    print("Usage: kt config llm")
    return 1


def _dispatch_key(args):
    sub = getattr(args, "config_key_command", None) or "list"
    provider = getattr(args, "provider", None)
    match sub:
        case "list":
            return _keys_list_cli()
        case "set":
            if not provider:
                print("provider required")
                return 1
            return _keys_set_cli(provider, getattr(args, "value", None))
        case "delete":
            if not provider:
                print("provider required")
                return 1
            return _keys_delete_cli(provider)
    print("Usage: kt config key")
    return 1


def _dispatch_mcp(args):
    sub = getattr(args, "config_mcp_command", None) or "list"
    name = getattr(args, "name", None)
    match sub:
        case "list":
            return _mcp_list_cli()
        case "add" | "edit":
            return _mcp_add_cli(name)
        case "delete":
            return _mcp_delete_cli(name) if name else (print("name required") or 1)
    print("Usage: kt config mcp")
    return 1


def config_cli(args):
    command = getattr(args, "config_command", None)
    match command:
        case None | "show":
            return _settings_show_cli()
        case "path":
            return _settings_path_cli(getattr(args, "name", None))
        case "edit":
            return _settings_edit_cli(getattr(args, "name", None))
        case "provider" | "backend":
            return _dispatch_provider(args)
        case "llm" | "model" | "preset":
            return _dispatch_llm(args)
        case "key":
            return _dispatch_key(args)
        case "login":
            return login_cli(getattr(args, "provider", ""))
        case "mcp":
            return _dispatch_mcp(args)
    print("Usage: kt config")
    return 1
