"""Validation-only parent policy gates for context-fork tool calls."""

import functools
import inspect
from copy import deepcopy
from typing import Any, Callable

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError
from kohakuterrarium.modules.plugin.manager.manager import PluginManager


class ForkToolPolicy:
    """Apply live opted-in parent guards after child argument transformations."""

    def __init__(self, parent: PluginManager | None) -> None:
        self._parent = parent
        self._hooks: dict[int, tuple[BasePlugin, Callable[..., Any]]] = {}

    async def check(self, args: dict, *, tool_name: str, **kwargs: Any) -> None:
        """Validate final tool arguments without dispatching parent lifecycle hooks."""
        if self._parent is None:
            return
        policies = self._policies()
        before = self._state(policies)
        for plugin in policies:
            try:
                entry = self._hooks.get(id(plugin))
                if entry is None:
                    entry = (plugin, plugin.fork_tool_policy())
                    self._hooks[id(plugin)] = entry
                checked = deepcopy(args)
                result = entry[1](checked, tool_name=tool_name, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if checked != args or (result is not None and result != args):
                    raise PluginBlockError(
                        "Inherited fork policy must not rewrite arguments"
                    )
            except PluginBlockError:
                raise
            except Exception as exc:
                raise PluginBlockError(
                    f"Inherited fork policy {plugin.name} failed: {exc}"
                ) from exc
        if self._state(self._policies()) != before:
            raise PluginBlockError(
                "Inherited fork policy changed during approval; retry the tool call"
            )

    def _policies(self) -> list[BasePlugin]:
        return [
            plugin
            for plugin in self._parent._applicable_plugins()
            if plugin.inherit_fork_policy
        ]

    @staticmethod
    def _state(policies: list[BasePlugin]) -> list[tuple[int, dict]]:
        try:
            return [
                (id(plugin), deepcopy(getattr(plugin, "options", {})))
                for plugin in policies
            ]
        except Exception as exc:
            raise PluginBlockError(
                "Cannot capture inherited fork policy state"
            ) from exc

    def wrap(
        self, execute: Callable[..., Any], *, tool_name: str, tool: Any = None
    ) -> Callable[..., Any]:
        """Wrap the final invocation inside any child-owned plugin transforms."""

        @functools.wraps(execute)
        async def guarded(args: dict, **kwargs: Any) -> Any:
            await self.check(args, tool_name=tool_name, tool=tool, **kwargs)
            return await execute(args, **kwargs)

        return guarded
