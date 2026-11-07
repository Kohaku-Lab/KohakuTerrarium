"""Small shell features: ``!`` shell commands, the key help (``?`` / F1) and attention (the
terminal title while working, a notification and a bell when a turn ends or an approval waits
while the terminal is in the background)."""

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

from kohakuterrarium.builtins.ktui.attach import working_dir
from kohakuterrarium.builtins.ktui.format import one_line

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import Entry, KtuiShell

SHELL_TIMEOUT = 600.0

KEYS_COMMON = """\
- enter · shift+enter / alt+enter / `\\` enter — send · new line
- ctrl+s (or ctrl+enter) — interrupt the turn and send now
- ↑ ↓ · ctrl+r — history · search the history
- esc · ctrl+c — interrupt · twice on an idle prompt: exit
- `@` — complete files and agents; a file named with `@path` is attached
- `!command` — run a shell command in the creature's folder
- ctrl+g — edit the message in `$EDITOR`
- alt+v · `/attach <path>` — attach the clipboard image · a file
- ↓ from the input · ctrl+b — agents, sub-agents, jobs and approvals · jobs
- alt+j · ctrl+k — the next approval · approve the oldest
- ctrl+x — cancel the last queued message
- ctrl+t · ctrl+o — full screen on / off · open every folded call
"""

KEYS_FULLSCREEN = """\
**full screen**
- alt+1 … alt+5 — Chat · Graph · Status · Debug · Settings
- alt+r · alt+s — the chats list · the activity panel (or click their titles)
- ctrl+pgup / ctrl+pgdn — previous / next conversation
- ctrl+↑ then ↑ ↓ (or a click) — pick a message: `c` copy, `e` edit, `r` retry, `[` `]` versions
- ctrl+f — find in the conversation (↑ older, ↓ newer, esc)
- ctrl+home / ctrl+end — top / bottom of the conversation
- F6 · alt+← → — next area · resize the focused panel
- drag — select text (copied on release)
"""


def key_help(fullscreen: bool) -> str:
    return KEYS_COMMON + ("\n" + KEYS_FULLSCREEN if fullscreen else "")


async def run_shell(shell: "KtuiShell", entry: "Entry", command: str) -> None:
    """``!command``: runs it in the creature's working directory, output in a tool cell."""
    if not command:
        return
    view = entry.view
    job_id = f"shell:{uuid.uuid4().hex[:8]}"
    view.render("tool_start", job_id, "!", one_line(command, 80), False)
    shell.refresh()
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(working_dir(entry.agent)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), SHELL_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            out, error = b"", f"stopped after {SHELL_TIMEOUT:.0f} s"
        else:
            error = f"exit {proc.returncode}" if proc.returncode else ""
    except OSError as exc:
        out, error = b"", str(exc)
    text = out.decode("utf-8", errors="replace")
    if error and text.strip():
        error = f"{error}\n{text}"
    view.render("tool_end", job_id, "!", text, error, time.monotonic() - started)
    shell.refresh()


class Attention:
    """The terminal title says what is going on; a turn that ends or an approval that waits
    while the window is in the background raises a notification (OSC 9 / 777) and a bell.
    """

    def __init__(self, shell: "KtuiShell"):
        self.shell = shell
        self.away = False
        self._title = ""

    def focus(self, gained: bool) -> None:
        self.away = not gained

    def _on(self, key: str) -> bool:
        return bool(self.shell.config.attention.get(key, True))

    def update(self, force: bool = False) -> None:
        shell = self.shell
        if not shell.entries:
            return
        if not self._on("title"):
            if self._title or force:
                self._title = ""
                shell.app.set_title("")
            return
        if force:
            self._title = ""
        busy = any(shell.is_busy(e) for e in shell.entries.values())
        waiting = shell.interactions.count
        name = shell.focused.view.name
        mark = "⚠ " if waiting else ("⟳ " if busy else "")
        title = f"{mark}{name} · kt"
        if title != self._title:
            self._title = title
            shell.app.set_title(title)

    def turn_done(self, name: str) -> None:
        if self.away and self._on("notify_done"):
            self.shell.app.alert(f"{name} finished", "KohakuTerrarium")
            if self._on("bell"):
                self.shell.app.bell()

    def approval(self, text: str) -> None:
        if self.away and self._on("notify_waiting"):
            self.shell.app.alert(
                f"approval needed · {one_line(text, 80)}", "KohakuTerrarium"
            )
            if self._on("bell"):
                self.shell.app.bell()

    def close(self) -> None:
        self.shell.app.set_title("")
