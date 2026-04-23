"""
Input and output module factories.

Creates input and output modules from agent config, with fallback
to CLI input and stdout output for unknown or failed types.

Bare-name input/output types (neither a builtin nor ``custom``/``package``)
are looked up in installed package manifests via
:func:`kohakuterrarium.packages.resolve_package_io`. This lets packages ship
IO modules under short names (e.g. ``type: discord_input``) without the
user needing to spell out ``module: ... ; class: ...`` in every creature
config.
"""

from typing import Any

from kohakuterrarium.builtins.inputs import (
    CLIInput,
    create_builtin_input,
    is_builtin_input,
)
from kohakuterrarium.builtins.outputs import (
    StdoutOutput,
    create_builtin_output,
    is_builtin_output,
)
from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.loader import ModuleLoadError, ModuleLoader
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.packages import resolve_package_io
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def create_input(
    config: AgentConfig,
    input_override: InputModule | None,
    loader: ModuleLoader | None,
) -> InputModule:
    """Create an input module from agent config.

    If input_override is provided, returns it directly. Otherwise
    resolves the input type from config (builtin, custom, or package)
    and falls back to CLIInput on failure.
    """
    if input_override:
        return input_override

    input_type = config.input.type
    options = {
        "prompt": config.input.prompt,
        **config.input.options,
    }
    # Ensure TUI input uses the agent's session key
    if input_type == "tui" and "session_key" not in options:
        options["session_key"] = config.session_key or config.name

    # Builtin input type
    if is_builtin_input(input_type):
        try:
            return create_builtin_input(input_type, options)
        except Exception as e:
            logger.error(
                "Failed to create builtin input",
                input_type=input_type,
                error=str(e),
            )
            return CLIInput(prompt=config.input.prompt)

    # Custom/package input
    if input_type in ("custom", "package"):
        if not config.input.module or not config.input.class_name:
            logger.warning("Custom input missing module or class, using CLI")
            return CLIInput(prompt=config.input.prompt)
        if loader is None:
            logger.warning("No module loader available for custom input, using CLI")
            return CLIInput(prompt=config.input.prompt)
        try:
            return loader.load_instance(
                module_path=config.input.module,
                class_name=config.input.class_name,
                module_type=input_type,
                options=config.input.options,
            )
        except ModuleLoadError as e:
            logger.error("Failed to load custom input", error=str(e))
            return CLIInput(prompt=config.input.prompt)

    # Bare name (e.g. "discord_input") — try to resolve via package manifest
    # before giving up. This wires the kohaku.yaml `io:` entries through
    # bootstrap so users can reference packaged inputs by short name.
    package_match = resolve_package_io(input_type)
    if package_match is not None:
        module_path, class_name = package_match
        if loader is None:
            logger.warning(
                "No module loader available for packaged input, using CLI",
                input_type=input_type,
            )
            return CLIInput(prompt=config.input.prompt)
        try:
            return loader.load_instance(
                module_path=module_path,
                class_name=class_name,
                module_type="package",
                options=config.input.options,
            )
        except ModuleLoadError as e:
            logger.error(
                "Failed to load packaged input",
                input_type=input_type,
                error=str(e),
            )
            return CLIInput(prompt=config.input.prompt)

    # Unknown type
    logger.warning("Unknown input type, using CLI", input_type=input_type)
    return CLIInput(prompt=config.input.prompt)


def _create_output_module(
    output_type: str,
    module_path: str | None,
    class_name: str | None,
    options: dict[str, Any],
    loader: ModuleLoader | None,
) -> OutputModule:
    """Create a single output module from its config fields.

    Resolves builtin, custom, or package output types and falls
    back to StdoutOutput on failure.
    """
    if is_builtin_output(output_type):
        try:
            return create_builtin_output(output_type, options)
        except Exception as e:
            logger.error(
                "Failed to create builtin output",
                output_type=output_type,
                error=str(e),
            )
            return StdoutOutput()

    if output_type in ("custom", "package"):
        if not module_path or not class_name:
            logger.warning("Custom output missing module or class, using stdout")
            return StdoutOutput()
        if loader is None:
            logger.warning("No module loader available for custom output, using stdout")
            return StdoutOutput()
        try:
            return loader.load_instance(
                module_path=module_path,
                class_name=class_name,
                module_type=output_type,
                options=options,
            )
        except ModuleLoadError as e:
            logger.error("Failed to load custom output", error=str(e))
            return StdoutOutput()

    # Bare name (e.g. "discord_output") — try to resolve via package manifest
    # before giving up. This wires the kohaku.yaml `io:` entries through
    # bootstrap so users can reference packaged outputs by short name.
    package_match = resolve_package_io(output_type)
    if package_match is not None:
        pkg_module, pkg_class = package_match
        if loader is None:
            logger.warning(
                "No module loader available for packaged output, using stdout",
                output_type=output_type,
            )
            return StdoutOutput()
        try:
            return loader.load_instance(
                module_path=pkg_module,
                class_name=pkg_class,
                module_type="package",
                options=options,
            )
        except ModuleLoadError as e:
            logger.error(
                "Failed to load packaged output",
                output_type=output_type,
                error=str(e),
            )
            return StdoutOutput()

    # Unknown type
    logger.warning("Unknown output type, using stdout", output_type=output_type)
    return StdoutOutput()


def create_output(
    config: AgentConfig,
    output_override: OutputModule | None,
    loader: ModuleLoader | None,
) -> tuple[OutputModule, dict[str, OutputModule]]:
    """Create default and named output modules from agent config.

    Returns a tuple of (default_output, named_outputs_dict).
    If output_override is provided, it becomes the default output.
    """
    # Default output
    if output_override:
        default_output = output_override
    else:
        out_options = config.output.options.copy()
        # Ensure TUI output uses the agent's session key
        if config.output.type == "tui" and "session_key" not in out_options:
            out_options["session_key"] = config.session_key or config.name
        default_output = _create_output_module(
            output_type=config.output.type,
            module_path=config.output.module,
            class_name=config.output.class_name,
            options=out_options,
            loader=loader,
        )

    # Named outputs
    named_outputs: dict[str, OutputModule] = {}
    for name, output_config in config.output.named_outputs.items():
        output_module = _create_output_module(
            output_type=output_config.type,
            module_path=output_config.module,
            class_name=output_config.class_name,
            options=output_config.options.copy(),
            loader=loader,
        )
        named_outputs[name] = output_module
        logger.debug("Named output registered", output_name=name)

    return default_output, named_outputs
