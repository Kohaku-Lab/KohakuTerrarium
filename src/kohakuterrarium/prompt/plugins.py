"""Define priority-ordered components for modular prompt composition."""

import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    XML_FORMAT,
    format_tool_call_example,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.registry import Registry

logger = get_logger(__name__)


@dataclass
class PluginContext:
    """Provide registry, path, format, and custom data to prompt plugins."""

    registry: "Registry | None" = None
    working_dir: Path = field(default_factory=Path.cwd)
    agent_path: Path | None = None
    tool_format: str = "bracket"
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PromptPlugin(Protocol):
    """Require named, prioritized prompt content providers."""

    @property
    def name(self) -> str:
        """Return the plugin's stable registry name."""
        ...

    @property
    def priority(self) -> int:
        """Return prompt order; lower values appear earlier."""
        ...

    def get_content(self, context: PluginContext) -> str | None:
        """Return prompt content or ``None`` when the plugin has nothing to add."""
        ...


class BasePlugin(ABC):
    """Provide the default priority for prompt plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the plugin's stable registry name."""
        ...

    @property
    def priority(self) -> int:
        """Return prompt order, defaulting to 50."""
        return 50

    @abstractmethod
    def get_content(self, context: PluginContext) -> str | None:
        """Return content to append to the system prompt."""
        ...


class ToolListPlugin(BasePlugin):
    """List registered tools with one-line descriptions."""

    @property
    def name(self) -> str:
        return "tool_list"

    @property
    def priority(self) -> int:
        return 50

    def get_content(self, context: PluginContext) -> str | None:
        if not context.registry:
            return None

        tool_names = context.registry.list_tools()
        if not tool_names:
            return None

        lines = ["## Available Tools", ""]
        for name in sorted(tool_names):
            info = context.registry.get_tool_info(name)
            description = info.description if info else "No description"
            lines.append(f"- `{name}`: {description}")

        lines.append("")
        lines.append("Use the `info` tool for full documentation on any function.")

        return "\n".join(lines)


class FrameworkHintsPlugin(BasePlugin):
    """Describe tool-call syntax and framework commands."""

    @property
    def name(self) -> str:
        return "framework_hints"

    @property
    def priority(self) -> int:
        return 60

    def get_content(self, context: PluginContext) -> str | None:
        if context.tool_format == "native":
            return (
                "## Tool Usage\n\n"
                "Tools are called via the API's native function calling mechanism.\n"
                "You do not need to format tool calls manually.\n\n"
                "Use the `info` tool for full documentation on any function."
            )

        match context.tool_format:
            case "xml":
                fmt = XML_FORMAT
            case _:
                fmt = BRACKET_FORMAT

        bash_ex = format_tool_call_example(fmt, "bash", body="ls -la")
        read_ex = format_tool_call_example(fmt, "read", {"path": "src/main.py"})
        write_ex = format_tool_call_example(
            fmt, "write", {"path": "new_file.py"}, "file content here"
        )
        grep_ex = format_tool_call_example(
            fmt, "grep", {"pattern": "TODO", "path": "src/"}
        )
        info_ex = format_tool_call_example(fmt, "info", body="tool_name")

        return (
            "## Tool Call Syntax\n\n"
            f"```\n{bash_ex}\n```\n\n"
            f"```\n{read_ex}\n```\n\n"
            f"```\n{write_ex}\n```\n\n"
            f"```\n{grep_ex}\n```\n\n"
            "## Framework Commands\n\n"
            f"- Get full docs: `{info_ex}`"
        )


class EnvInfoPlugin(BasePlugin):
    """Inject working-directory, repository, platform, and date context."""

    @property
    def name(self) -> str:
        return "env_info"

    @property
    def priority(self) -> int:
        return 10

    def get_content(self, context: PluginContext) -> str | None:
        cwd = context.working_dir
        is_git = (cwd / ".git").exists()
        plat = platform.system()
        date = datetime.now().strftime("%Y-%m-%d")

        return f"""<env>
Working directory: {cwd}
Is git repo: {"Yes" if is_git else "No"}
Platform: {plat}
Date: {date}
</env>"""


class ProjectInstructionsPlugin(BasePlugin):
    """Load hierarchical project instructions from the working tree.

    Root-level files appear first so deeper instructions can override them.
    """

    INSTRUCTION_FILES = ["AGENTS.md", ".kohaku.md", "CLAUDE.md"]

    @property
    def name(self) -> str:
        return "project_instructions"

    @property
    def priority(self) -> int:
        return 20

    def get_content(self, context: PluginContext) -> str | None:
        cwd = context.working_dir
        instructions = []

        current = cwd
        paths_to_check = []

        while current != current.parent:
            paths_to_check.append(current)
            if (current / ".git").exists():
                break
            current = current.parent

        # Append from root to leaf so narrower instructions take precedence.
        for path in reversed(paths_to_check):
            for filename in self.INSTRUCTION_FILES:
                filepath = path / filename
                if filepath.exists():
                    try:
                        content = filepath.read_text(encoding="utf-8")
                        rel_path = (
                            filepath.relative_to(cwd)
                            if filepath.is_relative_to(cwd)
                            else filepath
                        )
                        instructions.append(f"# From: {rel_path}\n{content}")
                        logger.debug(
                            "Loaded project instructions", filepath=str(filepath)
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to load instructions",
                            filepath=str(filepath),
                            error=str(e),
                        )

        if not instructions:
            return None

        return "## Project Instructions\n\n" + "\n\n".join(instructions)


BUILTIN_PLUGINS: dict[str, type[BasePlugin]] = {
    "tool_list": ToolListPlugin,
    "framework_hints": FrameworkHintsPlugin,
    "env_info": EnvInfoPlugin,
    "project_instructions": ProjectInstructionsPlugin,
}


def create_plugin(name: str) -> BasePlugin | None:
    """Instantiate a built-in plugin by registry name."""
    plugin_cls = BUILTIN_PLUGINS.get(name)
    if plugin_cls:
        return plugin_cls()
    return None


def get_default_plugins() -> list[BasePlugin]:
    """Return the basic tool-list and framework-hint plugins."""
    return [
        ToolListPlugin(),
        FrameworkHintsPlugin(),
    ]


def get_swe_plugins() -> list[BasePlugin]:
    """Return environment, project, tool, and framework plugins for SWE agents."""
    return [
        EnvInfoPlugin(),
        ProjectInstructionsPlugin(),
        ToolListPlugin(),
        FrameworkHintsPlugin(),
    ]
