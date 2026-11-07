"""The shell's input side, mixed into :class:`KtuiShell`: what the composer submits (a
message, a slash command, ``@name``, a channel post, an edited message), the message queue
while a creature works, and the composer's own requests (completions, attachments, the
external editor)."""

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.shell_extras import key_help, run_shell

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry


class ShellInput:
    """Mixed into ``KtuiShell``; uses its ``entries``, ``focused``, ``app`` and tasks."""

    def is_busy(self, entry: "Entry") -> bool:
        """Working, or a message from here has not finished its turn yet."""
        task = getattr(entry.agent, "_processing_task", None)
        if entry.view.processing or self._sending.get(entry.creature.creature_id):
            return True
        return bool(task is not None and not task.done())

    async def _submit(self, value: Any) -> None:
        if isinstance(value, dict):
            text, now = str(value.get("text", "")), bool(value.get("now"))
        else:
            text, now = str(value or ""), False
        self.composing.submitted()
        if await self.messages.submit_edit(text):
            return
        text = text.strip()
        if not text:
            return
        if text.startswith("/"):
            self.spawn(self.command(text))
            return
        if text == "?":
            await self.show_help()
            return
        if text.startswith("!") and len(text) > 1:
            self.spawn(run_shell(self, self.focused, text[1:].strip()))
            return
        if await self.chrome.submit(text):
            return

        target = self.focused
        if text.startswith("@") and self.multi:
            name, _, rest = text[1:].partition(" ")
            match = self._by_name(name)
            if match is None:
                self.app.notify(f"no creature named @{name}", "error")
                return
            target, text = match, rest.strip()
            if not text:
                self.focus(target.creature.creature_id)
                return
        self.send(target, text, now)

    def send(self, target: "Entry", text: str, now: bool = False) -> None:
        """Sends a message; while the agent works it is queued (``now``: interrupt first)."""
        pending_id = None
        if self.is_busy(target):
            if now:
                target.agent.interrupt()
            pending_id = uuid.uuid4().hex
            target.view.queue(pending_id, text)
        cid = target.creature.creature_id
        self._sending[cid] = self._sending.get(cid, 0) + 1
        content = self.composing.content(text, target)
        turn = self.spawn(
            target.agent.inject_input(content, source="cli", pending_id=pending_id)
        )
        self._turns.add(turn)

        def _sent(task: asyncio.Task) -> None:
            self._turns.discard(task)
            self._sending[cid] = max(0, self._sending.get(cid, 1) - 1)
            self.refresh()

        turn.add_done_callback(_sent)
        self.refresh()

    async def show_help(self) -> None:
        """``?`` (or F1): the keys, as a note in the conversation."""
        self.focused.view.render(
            "markdown_notice", "keys", key_help(self.mode == "fullscreen"), False
        )
        self.refresh()

    def _unqueue(self) -> None:
        entry = self.focused
        pending_id = entry.view.unqueue_last()
        if pending_id is None:
            self.app.notify("nothing queued", "info")
            return
        cancel = getattr(entry.agent, "cancel_pending", None)
        if callable(cancel) and cancel(pending_id):
            self.app.notify("queued message cancelled", "info")
        else:
            self.app.notify("too late: the agent already took it", "warning")
        self.refresh()

    def _by_name(self, name: str) -> "Entry | None":
        name = name.lower()
        for entry in self.entries.values():
            if (
                entry.creature.name.lower() == name
                or entry.creature.creature_id == name
            ):
                return entry
        return None
