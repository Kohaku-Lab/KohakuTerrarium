"""Message actions, as the web's message toolbar: copy, edit and re-run, retry, versions.

In full screen a message is picked with ctrl+↑ (then ↑↓) or a click; its keys are listed on
the bottom row: ``c`` copy, ``e`` edit (the text goes to the composer; enter re-runs from that
message, esc or an empty message cancels), ``r`` retry (a new reply to that message), and on a
turn with several versions ``[`` / ``]`` to switch between them.

Edit and retry start a new branch of that turn, as the web does: the transcript is redrawn
from the session's history up to that message, then the new reply streams in below it.
Switching versions redraws the whole transcript on the chosen branch.
"""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.ktui.replay import RESUME_TURNS, replay
from kohakuterrarium.session.history.replay import collect_branch_metadata
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry, KtuiShell
    from kohakuterrarium.builtins.ktui.view_state import Message

logger = get_logger(__name__)


class MessageActions:
    """Acts on the message cell an ``action`` event names."""

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        # the message being edited in the composer: (creature id, turn)
        self.editing: tuple[str, int] | None = None

    def find(self, node_id: Any) -> "tuple[Entry, Message] | None":
        for entry in self.shell.entries.values():
            msg = entry.view.messages.get(node_id)
            if msg is not None:
                return entry, msg
        return None

    def handle(self, name: str, node_id: Any) -> bool:
        """``msg:*`` actions; False for other actions."""
        if not name.startswith("msg:"):
            return False
        found = self.find(node_id)
        if found is None:
            return True
        entry, msg = found
        shell = self.shell
        match name.removeprefix("msg:"):
            case "older":
                self.load_older(entry)
            case "copy":
                shell.app.copy(msg.text)
            case "edit" if self._ready(entry, msg):
                shell.focus(entry.creature.creature_id)
                shell.composing.follow_conversation()
                shell.composing.begin_edit()
                self.editing = (entry.creature.creature_id, msg.turn)
                shell.composer.update(value=msg.text)
                shell.composer.focus()
                shell.app.notify(
                    "editing · enter re-runs from this message · esc cancels", "info", 6
                )
                shell.refresh()
            case "retry" if self._ready(entry, msg):
                shell.spawn(self.retry(entry, msg.turn))
            case "prev" | "next" as step if msg.turn is not None:
                shell.spawn(self.switch(entry, msg.turn, 1 if step == "next" else -1))
        return True

    def load_older(self, entry: "Entry") -> None:
        """The whole history, not only its last turns."""
        self._redraw(entry, everything=True)

    def _ready(self, entry: "Entry", msg: "Message") -> bool:
        shell = self.shell
        if msg.turn is None or entry.agent.session_store is None:
            shell.app.notify("this message cannot be re-run", "warning")
            return False
        if shell.is_busy(entry):
            shell.app.notify(
                "wait for the turn to finish (or esc to interrupt)", "warning"
            )
            return False
        return True

    def cancel_edit(self) -> bool:
        if self.editing is None:
            return False
        self.editing = None
        self.shell.composing.end_edit()
        self.shell.app.notify("edit cancelled", "info")
        return True

    async def submit_edit(self, text: str) -> bool:
        """The composer's text while editing: re-runs from the edited message."""
        if self.editing is None:
            return False
        cid, turn = self.editing
        self.editing = None
        self.shell.composing.end_edit()
        entry = self.shell.entries.get(cid)
        if entry is None or not text.strip():
            self.shell.app.notify("edit cancelled", "info")
            return True
        agent = entry.agent
        self._redraw(entry, until=(turn, False))
        await self._run(
            entry,
            turn,
            agent.edit_and_rerun(
                0, text, turn_index=turn, branch_view=_branch_view(agent) or None
            ),
        )
        return True

    async def retry(self, entry: "Entry", turn: int) -> None:
        agent = entry.agent
        self._redraw(entry, until=(turn, True))
        await self._run(
            entry,
            turn,
            agent.regenerate_last_response(
                turn_index=turn, branch_view=_branch_view(agent) or None
            ),
        )

    async def switch(self, entry: "Entry", turn: int, step: int) -> None:
        agent = entry.agent
        events = _events(agent)
        info = collect_branch_metadata(events, branch_view=_branch_view(agent)).get(
            turn
        )
        branches = sorted((info or {}).get("branches") or [])
        if len(branches) < 2:
            return
        current = _branch_view(agent).get(turn, info.get("latest_branch"))
        at = branches.index(current) if current in branches else len(branches) - 1
        target = branches[max(0, min(len(branches) - 1, at + step))]
        if target == current:
            return
        result = await agent._try_slash_command_text(f"/branch {turn} {target}")
        if getattr(result, "error", None):
            self.shell.app.notify(str(result.error), "error")
            return
        await _fresh(agent)
        self._redraw(entry)

    def _redraw(
        self,
        entry: "Entry",
        until: tuple[int, bool] | None = None,
        everything: bool = False,
    ) -> None:
        """The creature's transcript again from history, on the branch now selected: its
        last turns (a re-run from an older message, or ``everything``: all of it)."""
        view = entry.view
        view.reset()
        replay(
            view,
            _events(entry.agent),
            branch_view=_branch_view(entry.agent),
            until=until,
            dividers=False,
            last_turns=None if (everything or until) else RESUME_TURNS,
        )
        self.shell.refresh()

    async def _run(self, entry: "Entry", turn: int, coro: Any) -> None:
        shell = self.shell
        cid = entry.creature.creature_id
        shell._sending[cid] = shell._sending.get(cid, 0) + 1
        shell.refresh()
        try:
            await coro
        except Exception as exc:
            logger.warning("ktui rerun failed", error=str(exc), exc_info=True)
            entry.view.render("error", f"{type(exc).__name__}: {exc}")
        finally:
            shell._sending[cid] = max(0, shell._sending.get(cid, 1) - 1)
        # the new version is the one shown now: drop the choices from this turn on
        view = getattr(entry.agent, "_branch_view", None)
        if isinstance(view, dict):
            for t in [t for t in view if t >= turn]:
                view.pop(t)
        # the whole branch again, now with its version marks
        if shell.mode == "fullscreen" and cid in shell.entries:
            await _fresh(entry.agent)
            self._redraw(entry)
        shell.refresh()


async def _fresh(agent: Any) -> None:
    """A session in a server: its history read again before it is redrawn."""
    refresh = getattr(agent, "refresh_events", None)
    if refresh is not None:
        await refresh()


def _branch_view(agent: Any) -> dict[int, int]:
    return dict(getattr(agent, "_branch_view", None) or {})


def _events(agent: Any) -> list[dict]:
    store = getattr(agent, "session_store", None)
    if store is None:
        return []
    try:
        return list(store.get_events(agent.config.name))
    except Exception as exc:
        logger.warning("ktui could not read the history", error=str(exc))
        return []
