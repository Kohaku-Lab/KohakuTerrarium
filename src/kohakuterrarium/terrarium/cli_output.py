"""CLI output module for headless terrarium mode."""

import sys
from datetime import datetime

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.session.history import select_live_event_ids


class CLIOutput(BaseOutputModule):
    """Minimal stdout-backed terrarium output for headless CLI mode."""

    def __init__(self, speaker: str):
        super().__init__()
        self.speaker = speaker
        self._has_output = False
        self._streaming = False

    @property
    def _prefix(self) -> str:
        return f"[{self.speaker}] "

    async def write(self, content: str) -> None:
        if not content:
            return

        output = ""
        if not self._has_output:
            output += self._prefix

        output += content
        if not output.endswith("\n"):
            output += "\n"

        sys.stdout.write(output)
        sys.stdout.flush()
        self._has_output = True
        self._streaming = False

    async def write_stream(self, chunk: str) -> None:
        if not chunk:
            return

        if not self._streaming and not self._has_output:
            sys.stdout.write(self._prefix)

        sys.stdout.write(chunk)
        sys.stdout.flush()
        self._streaming = True
        self._has_output = True

    async def flush(self) -> None:
        if self._streaming:
            sys.stdout.write("\n")
        sys.stdout.flush()
        self._streaming = False

    async def on_processing_start(self) -> None:
        self.reset()

    async def on_processing_end(self) -> None:
        await self.flush()
        self.reset()

    async def on_resume(self, events: list[dict]) -> None:
        turns = _group_resume_events(events)
        if not turns:
            return

        print(f"\n--- Resumed {self.speaker} session ({len(turns)} turns) ---")
        for turn in turns:
            if turn["user"]:
                user_preview = turn["user"][:120]
                if len(turn["user"]) > 120:
                    user_preview += "..."
                print(f"[{self.speaker}] You: {user_preview}")

            if turn["text"]:
                text_preview = turn["text"].strip()[:200]
                if len(turn["text"].strip()) > 200:
                    text_preview += "..."
                tools_str = ""
                if turn["tools"]:
                    tools_str = f" [used {', '.join(turn['tools'])}]"
                print(f"[{self.speaker}]{tools_str} {text_preview}")
        print(f"--- End of {self.speaker} history ---\n")

    def reset(self) -> None:
        self._has_output = False
        self._streaming = False


def _group_resume_events(events: list[dict]) -> list[dict]:
    """Group persisted events into condensed turns for CLI replay."""
    if not events:
        return []

    live_ids = select_live_event_ids(events)
    turns: list[dict] = []
    current: dict = {"user": "", "text": "", "tools": []}

    for evt in events:
        etype = evt.get("type", "")
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid not in live_ids:
            continue
        if etype == "user_input":
            if current["user"] or current["text"]:
                turns.append(current)
            current = {"user": evt.get("content", ""), "text": "", "tools": []}
        elif etype == "trigger_fired":
            if current["user"] or current["text"]:
                turns.append(current)
            channel = evt.get("channel", "")
            sender = evt.get("sender", "")
            current = {
                "user": f"[trigger: {channel} from {sender}]",
                "text": "",
                "tools": [],
            }
        elif etype in ("text", "text_chunk"):
            # ``text_chunk`` is Wave C's per-chunk streaming format;
            # both render the same way in the resume preview.
            current["text"] += evt.get("content", "")
        elif etype == "tool_call":
            name = evt.get("name", "tool")
            if name not in current["tools"]:
                current["tools"].append(name)

    if current["user"] or current["text"]:
        turns.append(current)

    return turns


def _format_ts(ts: float | None) -> str:
    """Format a persisted epoch timestamp for channel replay."""
    if ts is None:
        return "--:--:--"

    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "--:--:--"


def _print_channel_message(channel: str, sender: str, content: str, ts: str) -> None:
    """Print channel traffic in a stable CLI-friendly format."""
    content_preview = str(content)[:100].replace("\n", "\\n")
    print(f"  [{ts}] [{channel}] {sender}: {content_preview}")
