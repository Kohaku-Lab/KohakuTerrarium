"""Input module placeholder for ktui: the shell injects composer input itself.

It replaces a creature input that would read the terminal (``CLIInput``, the Textual input),
so two readers never race for stdin. ``get_input`` only waits until the module stops.
"""

import asyncio

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.input.base import BaseInputModule


class KtuiInput(BaseInputModule):
    """Satisfies the input contract while :class:`KtuiShell` owns the terminal."""

    # the shell replays resume history itself, into the right view (see BaseInputModule)
    replay_resume_history = False

    def __init__(self) -> None:
        super().__init__()
        self._stopped = asyncio.Event()

    @property
    def exit_requested(self) -> bool:
        return self._stopped.is_set()

    async def _on_stop(self) -> None:
        self._stopped.set()

    async def get_input(self) -> TriggerEvent | None:
        await self._stopped.wait()
        return None
