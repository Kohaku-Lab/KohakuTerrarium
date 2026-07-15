"""Plugin response rewriting and controller command registration."""

import importlib
from typing import TYPE_CHECKING

from kohakuterrarium.commands.base import Command
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.packages.resolve import ensure_package_importable
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent
    from kohakuterrarium.core.controller import Controller

logger = get_logger(__name__)

# Built-ins are protected because plugins and packages share one command namespace.
BUILTIN_COMMANDS: frozenset[str] = frozenset({"read_job", "info", "jobs", "wait"})


async def run_post_llm_call_chain(
    controller: "Controller", messages: list[dict]
) -> None:
    """Apply post-LLM rewrites to the canonical assistant message.

    Plugin failures are isolated so later rewrites still run. Successful changes
    emit an edit marker through the output router.
    """
    plugins = controller.plugins
    if plugins is None:
        return
    last = controller.conversation.get_last_assistant_message()
    original = last.get_text_content() if last else ""
    current = original
    edited_by: list[str] = []
    base_method = getattr(BasePlugin, "post_llm_call", None)

    for plugin in plugins._applicable_plugins():
        method = getattr(type(plugin), "post_llm_call", None)
        if method is None or method is base_method:
            continue
        try:
            rewritten = await plugin.post_llm_call(
                messages,
                current,
                controller._last_usage or {},
                model=getattr(controller.llm, "model", ""),
            )
        except Exception as e:
            logger.warning(
                "Plugin post_llm_call raised",
                plugin_name=getattr(plugin, "name", "?"),
                error=str(e),
                exc_info=True,
            )
            continue
        if isinstance(rewritten, str) and rewritten != current:
            current = rewritten
            edited_by.append(getattr(plugin, "name", "?"))

    if edited_by and current != original and last is not None:
        last.content = current
        _emit_edit_marker(controller, original, current, edited_by)


def _emit_edit_marker(
    controller: "Controller",
    original: str,
    rewritten: str,
    plugin_names: list[str],
) -> None:
    """Notify outputs that plugins rewrote the recorded assistant message.

    The original stream remains visible; the activity is an annotation rather
    than replacement output.
    """
    router = controller.output_router
    if router is None or not hasattr(router, "notify_activity"):
        return
    preview = (original or "")[:200]
    try:
        router.notify_activity(
            "assistant_message_edited",
            f"[edited by plugin: {', '.join(plugin_names)}]",
            metadata={
                "edited_by": list(plugin_names),
                "original_preview": preview,
                "final_length": len(rewritten),
            },
        )
    except Exception as e:  # pragma: no cover - marker failure must not fail turn
        logger.debug(
            "Failed to emit assistant_message_edited marker",
            error=str(e),
            exc_info=True,
        )


def register_controller_command(
    controller: "Controller",
    command_name: str,
    cmd: Command,
    override: bool = False,
) -> None:
    """Register a controller command and update any live parser configuration.

    Built-in and duplicate names require explicit override. Updating the parser
    ensures ``##name##`` is classified as a command rather than a tool call.
    """
    if command_name in BUILTIN_COMMANDS and not override:
        raise ValueError(
            f"'{command_name}' is a built-in controller command. "
            f"Set override: true in your package manifest to replace it."
        )
    if command_name in controller._commands and not override:
        raise ValueError(
            f"Duplicate command '{command_name}'. Another module "
            f"registered it. Set override: true if intentional."
        )
    if override and command_name in controller._commands:
        logger.warning(
            "Controller command overridden",
            command=command_name,
        )
    controller._commands[command_name] = cmd
    parser_config = getattr(controller, "_parser_config", None)
    if parser_config is not None:
        parser_config.known_commands.add(command_name)
    logger.debug("Controller command registered", command=command_name)


def register_plugin_and_package_commands(agent: "Agent") -> None:
    """Register command contributions from loaded plugins and packages.

    Plugin loading must finish first because command construction may depend on
    state initialized by ``on_load``. Unapproved collisions remain hard errors.
    """
    controller = getattr(agent, "controller", None)
    if controller is None or not hasattr(controller, "register_command"):
        return

    plugins = getattr(agent, "plugins", None)
    if plugins is not None:
        for plugin, mapping in plugins.collect_commands():
            override = bool(getattr(plugin, "command_override", False))
            plugin_name = getattr(plugin, "name", "?")
            for cname, cmd in mapping.items():
                try:
                    controller.register_command(cname, cmd, override=override)
                except ValueError as e:
                    logger.error(
                        "Plugin command registration failed",
                        plugin_name=plugin_name,
                        command=cname,
                        error=str(e),
                    )
                    raise

    _register_package_commands(controller)


def _register_package_commands(controller: "Controller") -> None:
    """Load package commands while rejecting cross-package name collisions.

    Invalid or unimportable entries are skipped so unrelated package commands
    remain available.
    """
    seen: dict[str, str] = {}
    for pkg in list_packages():
        pkg_name = pkg.get("name", "?")
        for entry in pkg.get("commands", []) or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            if name in seen:
                raise ValueError(
                    f"Collision for command name {name!r}: declared by "
                    f"packages [{seen[name]}, {pkg_name}]. Uninstall "
                    "one or rename the entry to resolve the conflict."
                )
            seen[name] = pkg_name
            _load_one_package_command(controller, pkg_name, name, entry)


def _load_one_package_command(
    controller: "Controller",
    pkg_name: str,
    name: str,
    entry: dict,
) -> None:
    module = entry.get("module", "")
    class_name = entry.get("class") or entry.get("class_name", "")
    override = bool(entry.get("override", False))
    if not module or not class_name:
        logger.warning(
            "Package command entry missing module/class",
            command=name,
            package=pkg_name,
        )
        return
    ensure_package_importable(pkg_name)
    try:
        mod = importlib.import_module(module)
        cls = getattr(mod, class_name)
        cmd = cls()
    except Exception as e:
        logger.warning(
            "Failed to load package command",
            command=name,
            module_path=module,
            class_name=class_name,
            package=pkg_name,
            error=str(e),
            exc_info=True,
        )
        return
    try:
        controller.register_command(name, cmd, override=override)
    except ValueError as e:
        logger.error(
            "Package command registration failed",
            command=name,
            package=pkg_name,
            error=str(e),
        )
        raise


__all__: tuple[str, ...] = (
    "BUILTIN_COMMANDS",
    "run_post_llm_call_chain",
    "register_controller_command",
    "register_plugin_and_package_commands",
)
