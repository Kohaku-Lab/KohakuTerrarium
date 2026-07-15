"""Filter, deduplicate, and send controller output through Discord.

Uses the shared DiscordClient from input module to send messages.
Supports reply markers, mentions, keyword filtering, and deduplication.
"""

import asyncio
import re
from collections import deque

import discord

from kohakuterrarium.modules.output import BaseOutputModule
from kohakuterrarium.utils.logging import get_logger

from discord_client import DiscordClient, get_client

logger = get_logger("kohakuterrarium.custom.discord_output")


class DiscordOutputModule(BaseOutputModule):
    """Send guarded output through the Discord client shared by input.

    Supports special markers in output:
    - [reply:Username] or [reply:#N] - reply to a message
    - [@Username] - mention a user

    Supports keyword filtering and consecutive message deduplication.
    """

    REPLY_PATTERN = re.compile(r"\[reply:([^\]]+)\]", re.IGNORECASE)
    MENTION_PATTERN = re.compile(r"\[@([^\]]+)\]")

    def __init__(
        self,
        client: DiscordClient | None = None,
        client_name: str = "default",
        filtered_keywords: list[str] | None = None,
        keywords_file: str | None = None,
        drop_base_chance: float = 0.25,
        drop_increment: float = 0.15,
        drop_max_chance: float = 0.7,
        dedup_threshold: float = 0.85,
        dedup_window: int = 5,
    ):
        """Initialize filtering, load shedding, and deduplication policy.

        Args:
            client: Shared Discord client
            client_name: Name to look up in shared client registry
            filtered_keywords: Keywords to replace with [filtered]
            keywords_file: Path to keywords file (one per line)
            drop_base_chance: Base chance to drop when messages pending
            drop_increment: Additional chance per extra pending message
            drop_max_chance: Maximum drop chance cap
            dedup_threshold: Similarity threshold for deduplication (0.0-1.0)
            dedup_window: Number of recent messages to check for duplicates
        """
        super().__init__()
        self.client = client
        self.client_name = client_name
        self._buffer: list[str] = []

        self.drop_base_chance = drop_base_chance
        self.drop_increment = drop_increment
        self.drop_max_chance = drop_max_chance

        self.dedup_threshold = dedup_threshold
        self._recent_outputs: deque[str] = deque(maxlen=dedup_window)

        self._filtered_keywords: set[str] = set()
        if filtered_keywords:
            self._filtered_keywords.update(kw.lower() for kw in filtered_keywords)

        if keywords_file:
            self._load_keywords_file(keywords_file)

        if self._filtered_keywords:
            logger.info(
                "Keyword filter enabled",
                extra={"keyword_count": len(self._filtered_keywords)},
            )

        self._typing_task: asyncio.Task | None = None

        logger.info(
            "Initializing Discord output module",
            extra={
                "client_name": client_name,
                "drop_base": drop_base_chance,
                "dedup_threshold": dedup_threshold,
            },
        )

    def _load_keywords_file(self, filepath: str) -> None:
        """Load one case-insensitive filtered keyword per non-comment line."""
        import os

        if not os.path.exists(filepath):
            logger.warning("Keywords file not found", extra={"path": filepath})
            return

        try:
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self._filtered_keywords.add(line.lower())
        except OSError as e:
            logger.warning("Failed to load keywords file", extra={"error": str(e)})

    def _filter_keywords(self, content: str) -> str:
        """Redact configured keywords without regard to case."""
        if not self._filtered_keywords:
            return content

        result = content
        for keyword in self._filtered_keywords:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            result = pattern.sub("[filtered]", result)

        return result

    def _similarity(self, a: str, b: str) -> float:
        """Estimate normalized character overlap between two messages."""
        norm_a = " ".join(a.lower().split())
        norm_b = " ".join(b.lower().split())

        if norm_a == norm_b:
            return 1.0

        if not norm_a or not norm_b:
            return 0.0

        len_a, len_b = len(norm_a), len(norm_b)

        if norm_a in norm_b or norm_b in norm_a:
            return min(len_a, len_b) / max(len_a, len_b)

        common = 0
        b_chars = list(norm_b)
        for c in norm_a:
            if c in b_chars:
                common += 1
                b_chars.remove(c)

        return (2 * common) / (len_a + len_b)

    def _is_duplicate(self, content: str) -> bool:
        """Return whether content is too similar to a recent sent message."""
        if not self._recent_outputs:
            return False

        for recent in self._recent_outputs:
            sim = self._similarity(content, recent)
            if sim >= self.dedup_threshold:
                logger.debug(
                    "Duplicate detected",
                    extra={
                        "similarity": f"{sim:.2%}",
                        "threshold": f"{self.dedup_threshold:.2%}",
                    },
                )
                return True

        return False

    def _ensure_client(self) -> DiscordClient | None:
        """Return the explicit client or resolve it from the shared registry."""
        if self.client is None:
            self.client = get_client(self.client_name)
            if self.client:
                logger.debug("Retrieved Discord client from registry")
            else:
                logger.warning("Discord client not found in registry")
        return self.client

    async def _typing_loop(self, channel: discord.TextChannel | discord.Thread) -> None:
        """Refresh Discord's expiring typing indicator until cancelled."""
        try:
            while True:
                await channel.typing()
                # Refresh before Discord's roughly ten-second indicator expires.
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            pass
        except discord.DiscordException:
            pass

    async def on_processing_start(self) -> None:
        """Start one typing indicator for the active writable channel."""
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            self._typing_task = None

        client = self._ensure_client()
        if not client or not client._current_channel_id:
            return

        channel_id = client._current_channel_id

        if client.is_readonly_channel(channel_id):
            return

        channel = client.get_channel(channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(channel_id)
            except discord.DiscordException:
                return

        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            self._typing_task = asyncio.create_task(self._typing_loop(channel))
            logger.debug("Started typing indicator")

    async def on_processing_end(self) -> None:
        """Cancel and await the active typing indicator."""
        if self._typing_task and not self._typing_task.done():
            self._typing_task.cancel()
            try:
                await self._typing_task
            except asyncio.CancelledError:
                pass
            self._typing_task = None
            logger.debug("Stopped typing indicator")

    def _parse_markers(
        self, content: str, channel_id: int
    ) -> tuple[str, int | None, list[int]]:
        """Resolve reply and mention markers, then remove them from content."""
        client = self._ensure_client()
        if not client:
            return content, None, []

        reply_to_id = None
        mention_ids = []

        reply_match = self.REPLY_PATTERN.search(content)
        if reply_match:
            ref = reply_match.group(1).strip()
            reply_to_id = client.find_message_id(ref, channel_id)
            content = self.REPLY_PATTERN.sub("", content)

        for match in self.MENTION_PATTERN.finditer(content):
            name = match.group(1).strip()
            user_id = client.find_user_id(name)
            if user_id:
                mention_ids.append(user_id)

        content = self.MENTION_PATTERN.sub("", content)

        return content.strip(), reply_to_id, mention_ids

    async def write(self, content: str) -> None:
        """Apply output policy and send one complete Discord message."""
        client = self._ensure_client()
        if not client:
            return

        # Shed stale responses increasingly aggressively as new input accumulates.
        import random

        pending_count = client.pending_message_count()
        if pending_count > 0:
            drop_chance = min(
                self.drop_max_chance,
                self.drop_base_chance + (pending_count - 1) * self.drop_increment,
            )
            if random.random() < drop_chance:
                logger.info(
                    "Dropping response - new messages pending",
                    extra={"pending_count": pending_count},
                )
                return

        clean_content = content.strip()
        if not clean_content:
            return

        if self._is_duplicate(clean_content):
            logger.info("Filtering duplicate response")
            return

        # Reject controller/protocol fragments that are not user-facing messages.
        garbage_patterns = [
            "user",
            "assistant",
            "[tool",
            "## ",
            "background job",
            "```",
            "---",
        ]
        lower_content = clean_content.lower()
        for pattern in garbage_patterns:
            if lower_content.strip() == pattern or lower_content.startswith(pattern):
                logger.debug("Filtering garbage output")
                return

        # Preserve short words, but reject punctuation-only fragments.
        stripped = clean_content.replace(" ", "").replace("\n", "")
        if len(stripped) <= 3:
            if not any(c.isalnum() for c in stripped):
                return

        # Repetitive low-entropy output is usually a generation artifact.
        unique_chars = set(stripped)
        if len(unique_chars) <= 2 and len(stripped) > 1:
            return

        channel_id = client._current_channel_id
        if not channel_id:
            return

        final_content, reply_to_id, mention_ids = self._parse_markers(
            clean_content, channel_id
        )

        if not final_content:
            return

        final_content = self._filter_keywords(final_content)

        sent = await client.send_message(
            final_content,
            reply_to_id=reply_to_id,
            mentions=mention_ids,
        )

        if sent:
            self._recent_outputs.append(final_content)

    async def write_stream(self, chunk: str) -> None:
        """Buffer a streaming chunk until the turn is flushed."""
        self._buffer.append(chunk)

    async def flush(self) -> None:
        """Join and send all buffered streaming chunks as one message."""
        if self._buffer:
            content = "".join(self._buffer)
            self._buffer.clear()
            await self.write(content)


def create_discord_io(
    token: str | None = None,
    token_env: str = "DISCORD_BOT_TOKEN",
    channel_ids: list[int] | None = None,
    readonly_channel_ids: list[int] | None = None,
    history_limit: int = 20,
    filtered_keywords: list[str] | None = None,
    keywords_file: str | None = None,
):
    """Create paired modules that share one Discord client."""
    from discord_input import DiscordInputModule

    input_module = DiscordInputModule(
        token=token,
        token_env=token_env,
        channel_ids=channel_ids,
        readonly_channel_ids=readonly_channel_ids,
        history_limit=history_limit,
    )

    output_module = DiscordOutputModule(
        client=input_module.get_client(),
        filtered_keywords=filtered_keywords,
        keywords_file=keywords_file,
    )

    return input_module, output_module
