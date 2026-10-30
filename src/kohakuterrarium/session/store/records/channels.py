"""Channel message history."""

import time
from typing import Any

from kohakuterrarium.session.store.events.flush import insert_fts_rows
from kohakuterrarium.session.store.events.counters import restore_suffix_counters
from kohakuterrarium.session.vault_io import read_prefix


class ChannelRecordsMixin:
    """Append-only channel messages with per-channel sequence keys."""

    # Tables opened by the handle part.
    channels: Any
    fts: Any

    def _init_channels(self) -> None:
        self._channel_seq: dict[str, int] = {}

    def _restore_channel_counters(self) -> None:
        restore_suffix_counters(self.channels, ":m", self._channel_seq)

    def _next_channel_seq(self, channel: str) -> int:
        seq = self._channel_seq.get(channel, 0)
        self._channel_seq[channel] = seq + 1
        return seq

    def save_channel_message(self, channel: str, data: dict) -> str:
        """Append a channel message. Returns the key."""
        seq = self._next_channel_seq(channel)
        key = f"{channel}:m{seq:06d}"
        if "ts" not in data:
            data["ts"] = time.time()
        self.channels[key] = data

        content = data.get("content", "")
        # The channel row commits now, so its FTS row does too.
        if self.fts is not None and isinstance(content, str) and len(content) > 10:
            insert_fts_rows(
                self.fts,
                [
                    (
                        content,
                        {
                            "channel_key": key,
                            "channel": channel,
                            "sender": data.get("sender", ""),
                            "type": "channel",
                        },
                    )
                ],
            )
        return key

    def get_channel_messages(self, channel: str) -> list[dict]:
        """Get all messages for a channel, ordered."""
        rows = read_prefix(self.channels, f"{channel}:m", what="channel message")
        return [value for _, value in rows]
