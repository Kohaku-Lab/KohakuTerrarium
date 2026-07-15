"""Bridge discord.py messages and media into shared Terrarium I/O state.

This module contains the core Discord client that bridges discord.py
with the KohakuTerrarium framework.

Supports multimodal content: attachments, stickers, and custom emojis.
"""

import asyncio
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import discord

from kohakuterrarium.utils.logging import get_logger

logger = get_logger("kohakuterrarium.custom.discord_client")


@dataclass
class MediaAttachment:
    """Store normalized metadata for a Discord file attachment."""

    url: str
    filename: str
    content_type: str | None
    size: int
    is_image: bool
    is_animated: bool = False  # Discord serves animated image attachments as GIFs.

    @classmethod
    def from_discord(cls, attachment: discord.Attachment) -> "MediaAttachment":
        """Normalize a discord.py attachment."""
        content_type = attachment.content_type or ""
        is_image = content_type.startswith("image/")
        is_animated = content_type == "image/gif"
        return cls(
            url=attachment.url,
            filename=attachment.filename,
            content_type=content_type,
            size=attachment.size,
            is_image=is_image,
            is_animated=is_animated,
        )


@dataclass
class MediaSticker:
    """Store normalized metadata for a Discord sticker."""

    name: str
    sticker_id: int
    url: str
    format_type: str  # "png", "apng", "lottie", "gif"
    is_animated: bool

    @classmethod
    def from_discord(cls, sticker: discord.StickerItem) -> "MediaSticker":
        """Normalize a discord.py sticker and its animation format."""
        format_type = "png"
        is_animated = False
        if hasattr(sticker, "format"):
            fmt = sticker.format
            if fmt == discord.StickerFormatType.png:
                format_type = "png"
            elif fmt == discord.StickerFormatType.apng:
                format_type = "apng"
                is_animated = True
            elif fmt == discord.StickerFormatType.lottie:
                format_type = "lottie"
                is_animated = True
            elif fmt == discord.StickerFormatType.gif:
                format_type = "gif"
                is_animated = True

        return cls(
            name=sticker.name,
            sticker_id=sticker.id,
            url=str(sticker.url),
            format_type=format_type,
            is_animated=is_animated,
        )


@dataclass
class CustomEmoji:
    """Store a custom emoji parsed from Discord message markup."""

    name: str
    emoji_id: int
    animated: bool
    url: str

    @classmethod
    def from_match(cls, name: str, emoji_id: int, animated: bool) -> "CustomEmoji":
        """Build emoji metadata from parsed markup fields."""
        ext = "gif" if animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        return cls(
            name=name,
            emoji_id=emoji_id,
            animated=animated,
            url=url,
        )


# Discord encodes static and animated custom emoji as <:name:id> and <a:name:id>.
CUSTOM_EMOJI_PATTERN = re.compile(r"<(a?):(\w+):(\d+)>")


# Custom modules are instantiated independently, so the registry shares one connection.
_shared_clients: dict[str, "DiscordClient"] = {}

# Bound tool history so prompt context cannot grow without limit.
_tool_history: deque[str] = deque(maxlen=10)


def register_client(name: str, client: "DiscordClient") -> None:
    """Register a client for use by independently loaded custom modules."""
    _shared_clients[name] = client


def get_client(name: str) -> "DiscordClient | None":
    """Return the shared client registered under a module name."""
    return _shared_clients.get(name)


def add_tool_history(entry: str) -> None:
    """Append a tool-call summary to the bounded shared history."""
    _tool_history.append(entry)


def get_tool_history() -> list[str]:
    """Return a snapshot of recent tool-call summaries."""
    return list(_tool_history)


def clear_tool_history() -> None:
    """Clear all shared tool-call summaries."""
    _tool_history.clear()


def short_id(full_id: int) -> str:
    """Abbreviate long Discord IDs while preserving recognizable edges."""
    s = str(full_id)
    if len(s) <= 8:
        return s
    return f"{s[:4]}..{s[-4:]}"


# User mentions may include Discord's optional nickname marker: <@id> or <@!id>.
DISCORD_MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


def parse_custom_emojis(content: str) -> list[CustomEmoji]:
    """Extract custom emoji metadata from Discord markup."""
    emojis = []
    for match in CUSTOM_EMOJI_PATTERN.finditer(content):
        animated = bool(match.group(1))  # The optional "a" prefix marks animation.
        name = match.group(2)
        emoji_id = int(match.group(3))
        emojis.append(CustomEmoji.from_match(name, emoji_id, animated))
    return emojis


@dataclass
class DiscordMessage:
    """Represent one Discord message with reply and multimodal metadata."""

    content: str
    author_id: int
    author_name: str
    author_display_name: str
    channel_id: int
    channel_name: str
    guild_id: int | None
    guild_name: str | None
    message_id: int
    is_mention: bool
    mentioned_users: list[int]
    reply_to_id: int | None
    reply_to_author: str | None = None
    reply_to_content: str | None = None
    reply_to_is_bot: bool = False
    is_bot: bool = False
    timestamp: str = ""
    short_msg_id: str = ""
    short_author_id: str = ""

    attachments: list[MediaAttachment] = field(default_factory=list)
    stickers: list[MediaSticker] = field(default_factory=list)
    custom_emojis: list[CustomEmoji] = field(default_factory=list)

    def __post_init__(self):
        self.short_msg_id = short_id(self.message_id)
        self.short_author_id = short_id(self.author_id)

    def has_media(self) -> bool:
        """Return whether the message contains any supported media."""
        return bool(self.attachments or self.stickers or self.custom_emojis)

    def get_image_attachments(self) -> list[MediaAttachment]:
        """Return attachments classified as images."""
        return [a for a in self.attachments if a.is_image]

    def has_animated_content(self) -> bool:
        """Return whether any attachment, sticker, or emoji is animated."""
        if any(a.is_animated for a in self.attachments):
            return True
        if any(s.is_animated for s in self.stickers):
            return True
        if any(e.animated for e in self.custom_emojis):
            return True
        return False

    def to_context(self) -> dict[str, Any]:
        """Build the serializable metadata attached to a trigger event."""
        return {
            "source": "discord",
            "author_id": self.author_id,
            "author_name": self.author_name,
            "author_display_name": self.author_display_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "guild_id": self.guild_id,
            "guild_name": self.guild_name,
            "message_id": self.message_id,
            "short_msg_id": self.short_msg_id,
            "is_mention": self.is_mention,
            "mentioned_users": self.mentioned_users,
            "reply_to_id": self.reply_to_id,
            "has_attachments": len(self.attachments) > 0,
            "has_stickers": len(self.stickers) > 0,
            "has_custom_emojis": len(self.custom_emojis) > 0,
        }


@dataclass
class RecentMessage:
    """Retain the minimal message metadata needed for later references."""

    message_id: int
    short_id: str
    author_name: str
    author_id: int
    content_preview: str
    is_bot: bool = False


class DiscordClient(discord.Client):
    """Bridge Discord I/O while retaining lookup and reply context."""

    def __init__(
        self,
        channel_ids: list[int] | None = None,
        readonly_channel_ids: list[int] | None = None,
        history_limit: int = 20,
        timezone: str | None = None,
        **kwargs: Any,
    ):
        """Initialize channel policy, bounded history, and lookup caches.

        Args:
            channel_ids: List of channel IDs to listen to
            readonly_channel_ids: List of read-only channel IDs
            history_limit: Number of messages to fetch for history
            timezone: Timezone name for timestamp formatting (e.g., "Asia/Tokyo",
                     "America/New_York", "UTC"). If None, uses system local timezone.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents, **kwargs)

        self.channel_ids = set(channel_ids) if channel_ids else None
        self.readonly_channel_ids = (
            set(readonly_channel_ids) if readonly_channel_ids else set()
        )
        self.history_limit = history_limit
        self._message_queue: asyncio.Queue[DiscordMessage] = asyncio.Queue()

        if timezone:
            try:
                self._timezone = ZoneInfo(timezone)
            except KeyError:
                logger.warning(
                    f"Unknown timezone '{timezone}', using system local timezone"
                )
                self._timezone = None  # astimezone() then uses the system zone.
        else:
            self._timezone = None  # astimezone() then uses the system zone.

        # Output follows the channel of the most recently consumed input.
        self._current_channel_id: int | None = None

        # Per-channel buffers resolve compact reply references without API calls.
        self._recent_messages: dict[int, deque[RecentMessage]] = {}
        self._max_recent = max(50, history_limit)

        # Cache both account and display names for mention generation.
        self._user_cache: dict[str, int] = {}

        # Record channels whose history has been fetched for coordinating callers.
        self._history_fetched: set[int] = set()

    async def on_ready(self) -> None:
        """Log the established Discord identity and guild count."""
        logger.info(
            "Discord client ready",
            extra={"bot_user": str(self.user), "guilds": len(self.guilds)},
        )

    def _format_timestamp(self, dt: datetime) -> str:
        """Format a Discord timestamp in the configured or system timezone.

        Discord returns UTC timezone-aware datetimes. This method converts
        to the configured timezone (or system local if not configured).

        Args:
            dt: UTC datetime from Discord

        Returns:
            Formatted timestamp string in "YYYY-MM-DD HH:MM" format
        """
        if self._timezone:
            local_dt = dt.astimezone(self._timezone)
        else:
            local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")

    def get_bot_identity(self) -> str:
        """Return a compact bot identity suitable for prompt context."""
        if self.user:
            return f"{self.user.display_name}({short_id(self.user.id)})"
        return "Bot(unknown)"

    async def fetch_channel_history(
        self,
        channel: discord.TextChannel | discord.Thread,
        force: bool = False,
    ) -> list[str]:
        """Fetch and format recent channel history while warming lookup caches."""
        if self.history_limit <= 0:
            return []

        channel_id = channel.id
        self._history_fetched.add(channel_id)

        try:
            messages = []
            async for msg in channel.history(limit=self.history_limit):
                self._user_cache[msg.author.display_name.lower()] = msg.author.id
                self._user_cache[msg.author.name.lower()] = msg.author.id

                if channel_id not in self._recent_messages:
                    self._recent_messages[channel_id] = deque(maxlen=self._max_recent)

                is_self = msg.author == self.user
                self._recent_messages[channel_id].appendleft(
                    RecentMessage(
                        message_id=msg.id,
                        short_id=short_id(msg.id),
                        author_name=msg.author.display_name,
                        author_id=msg.author.id,
                        content_preview=msg.content[:100] if msg.content else "",
                        is_bot=msg.author.bot,
                    )
                )

                msg_time = self._format_timestamp(msg.created_at)
                parsed_content = self.parse_mentions(msg.content, msg.guild)
                if is_self:
                    self_marker = "[SELF] "
                else:
                    self_marker = ""
                bot_marker = "[BOT] " if msg.author.bot and not is_self else ""
                formatted = f"[{msg_time}] {self_marker}{bot_marker}[{msg.author.display_name}]: {parsed_content}"
                messages.append(formatted)

            messages.reverse()

            logger.info(
                "Fetched channel history",
                extra={"channel_id": channel_id, "message_count": len(messages)},
            )
            return messages

        except discord.DiscordException as e:
            logger.warning(
                "Failed to fetch history",
                extra={"channel_id": channel_id, "error": str(e)},
            )
            return []

    async def fetch_channel_history_with_media(
        self,
        channel: discord.TextChannel | discord.Thread,
        limit: int | None = None,
    ) -> list[DiscordMessage]:
        """Fetch recent media-bearing messages for multimodal context.

        Args:
            channel: Discord channel to fetch from
            limit: Number of messages to fetch (defaults to history_limit)

        Returns:
            List of DiscordMessage objects with media info populated
        """
        fetch_limit = limit or self.history_limit
        if fetch_limit <= 0:
            return []

        try:
            messages: list[DiscordMessage] = []
            async for msg in channel.history(limit=fetch_limit):
                if msg.author == self.user:
                    continue

                attachments = [
                    MediaAttachment.from_discord(att) for att in msg.attachments
                ]

                stickers = [MediaSticker.from_discord(s) for s in msg.stickers]

                custom_emojis = parse_custom_emojis(msg.content)

                # Text-only history is supplied separately and must not be duplicated.
                if not attachments and not stickers and not custom_emojis:
                    continue

                is_mention = self.user in msg.mentions if self.user else False
                parsed_content = self.parse_mentions(msg.content, msg.guild)
                msg_time = self._format_timestamp(msg.created_at)

                discord_msg = DiscordMessage(
                    content=parsed_content,
                    author_id=msg.author.id,
                    author_name=msg.author.name,
                    author_display_name=msg.author.display_name,
                    channel_id=msg.channel.id,
                    channel_name=getattr(msg.channel, "name", "DM"),
                    guild_id=msg.guild.id if msg.guild else None,
                    guild_name=msg.guild.name if msg.guild else None,
                    message_id=msg.id,
                    is_mention=is_mention,
                    mentioned_users=[u.id for u in msg.mentions],
                    reply_to_id=msg.reference.message_id if msg.reference else None,
                    is_bot=msg.author.bot,
                    timestamp=msg_time,
                    attachments=attachments,
                    stickers=stickers,
                    custom_emojis=custom_emojis,
                )
                messages.append(discord_msg)

            messages.reverse()
            logger.debug(
                "Fetched history with media",
                extra={"channel_id": channel.id, "media_messages": len(messages)},
            )
            return messages

        except discord.DiscordException as e:
            logger.warning(
                "Failed to fetch history with media",
                extra={"channel_id": channel.id, "error": str(e)},
            )
            return []

    async def on_message(self, message: discord.Message) -> None:
        """Normalize an allowed incoming message and enqueue it for input."""
        if message.author == self.user:
            return

        all_channels = set()
        if self.channel_ids:
            all_channels.update(self.channel_ids)
        all_channels.update(self.readonly_channel_ids)

        if all_channels and message.channel.id not in all_channels:
            return

        self._user_cache[message.author.display_name.lower()] = message.author.id
        self._user_cache[message.author.name.lower()] = message.author.id

        channel_id = message.channel.id
        if channel_id not in self._recent_messages:
            self._recent_messages[channel_id] = deque(maxlen=self._max_recent)

        self._recent_messages[channel_id].append(
            RecentMessage(
                message_id=message.id,
                short_id=short_id(message.id),
                author_name=message.author.display_name,
                author_id=message.author.id,
                content_preview=message.content[:100] if message.content else "",
                is_bot=message.author.bot,
            )
        )

        is_mention = self.user in message.mentions if self.user else False
        mentioned_users = [u.id for u in message.mentions]

        # Resolve replies from Discord's cache, local history, then the API.
        reply_to_id = None
        reply_to_author = None
        reply_to_content = None
        reply_to_is_bot = False
        if message.reference and message.reference.message_id:
            reply_to_id = message.reference.message_id
            if message.reference.cached_message:
                ref_msg = message.reference.cached_message
                reply_to_author = ref_msg.author.display_name
                reply_to_content = self.parse_mentions(ref_msg.content, message.guild)
                reply_to_is_bot = ref_msg.author.bot
            else:
                if channel_id in self._recent_messages:
                    for recent in self._recent_messages[channel_id]:
                        if recent.message_id == reply_to_id:
                            reply_to_author = recent.author_name
                            reply_to_content = recent.content_preview
                            reply_to_is_bot = recent.is_bot
                            break
                if reply_to_content is None:
                    try:
                        ref_msg = await message.channel.fetch_message(reply_to_id)
                        reply_to_author = ref_msg.author.display_name
                        reply_to_content = self.parse_mentions(
                            ref_msg.content[:100] if ref_msg.content else "",
                            message.guild,
                        )
                        reply_to_is_bot = ref_msg.author.bot
                    except discord.DiscordException:
                        pass

        parsed_content = self.parse_mentions(message.content, message.guild)
        msg_time = self._format_timestamp(message.created_at)

        attachments = [MediaAttachment.from_discord(att) for att in message.attachments]

        stickers = [MediaSticker.from_discord(s) for s in message.stickers]

        custom_emojis = parse_custom_emojis(message.content)

        discord_msg = DiscordMessage(
            content=parsed_content,
            author_id=message.author.id,
            author_name=message.author.name,
            author_display_name=message.author.display_name,
            channel_id=message.channel.id,
            channel_name=getattr(message.channel, "name", "DM"),
            guild_id=message.guild.id if message.guild else None,
            guild_name=message.guild.name if message.guild else None,
            message_id=message.id,
            is_mention=is_mention,
            mentioned_users=mentioned_users,
            reply_to_id=reply_to_id,
            reply_to_author=reply_to_author,
            reply_to_content=reply_to_content,
            reply_to_is_bot=reply_to_is_bot,
            is_bot=message.author.bot,
            timestamp=msg_time,
            attachments=attachments,
            stickers=stickers,
            custom_emojis=custom_emojis,
        )

        await self._message_queue.put(discord_msg)
        logger.info(
            "Message queued",
            extra={
                "author": message.author.display_name,
                "channel": getattr(message.channel, "name", "DM"),
                "queue_size": self._message_queue.qsize(),
                "is_mention": is_mention,
                "attachments": len(attachments),
                "stickers": len(stickers),
                "custom_emojis": len(custom_emojis),
            },
        )

    async def get_message(self) -> DiscordMessage:
        """Wait for and return the next normalized Discord message."""
        return await self._message_queue.get()

    def set_output_context(self, channel_id: int) -> None:
        """Set the channel that subsequent output should target."""
        self._current_channel_id = channel_id

    def is_readonly_channel(self, channel_id: int) -> bool:
        """Return whether channel policy forbids bot output."""
        return channel_id in self.readonly_channel_ids

    def find_message_id(self, reference: str, channel_id: int) -> int | None:
        """Resolve a positional, abbreviated-ID, or author reference."""
        if channel_id not in self._recent_messages:
            return None

        recent = self._recent_messages[channel_id]

        if reference.startswith("#") and reference[1:].isdigit():
            n = int(reference[1:])
            if 1 <= n <= len(recent):
                return list(recent)[-(n)].message_id
            return None

        if ".." in reference:
            for msg in recent:
                if msg.short_id == reference:
                    return msg.message_id
            return None

        ref_lower = reference.lower()
        for msg in reversed(list(recent)):
            if msg.author_name.lower() == ref_lower:
                return msg.message_id

        return None

    def find_user_id(self, name: str) -> int | None:
        """Resolve a cached account or display name to a user ID."""
        return self._user_cache.get(name.lower())

    def parse_mentions(self, content: str, guild: discord.Guild | None) -> str:
        """Replace Discord user markup with readable cached display names."""
        if not content:
            return content

        def replace_mention(match: re.Match) -> str:
            user_id = int(match.group(1))

            if self.user and user_id == self.user.id:
                return f"@{self.user.display_name}"

            if guild:
                member = guild.get_member(user_id)
                if member:
                    self._user_cache[member.display_name.lower()] = user_id
                    self._user_cache[member.name.lower()] = user_id
                    return f"@{member.display_name}"

            for name, uid in self._user_cache.items():
                if uid == user_id:
                    return f"@{name}"

            return f"@User({short_id(user_id)})"

        return DISCORD_MENTION_PATTERN.sub(replace_mention, content)

    async def send_message(
        self,
        content: str,
        channel_id: int | None = None,
        reply_to_id: int | None = None,
        mentions: list[int] | None = None,
    ) -> discord.Message | None:
        """Send content to an allowed text channel with optional reply metadata."""
        target_channel_id = channel_id or self._current_channel_id

        if not target_channel_id:
            logger.warning("No target channel for send_message")
            return None

        if self.is_readonly_channel(target_channel_id):
            logger.debug(
                "Skipping send to read-only channel",
                extra={"channel_id": target_channel_id},
            )
            return None

        channel = self.get_channel(target_channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(target_channel_id)
            except discord.DiscordException as e:
                logger.warning(
                    "Failed to fetch channel",
                    extra={"channel_id": target_channel_id, "error": str(e)},
                )
                return None

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            logger.warning(
                "Channel not messageable",
                extra={
                    "channel_id": target_channel_id,
                    "channel_type": type(channel).__name__,
                },
            )
            return None

        try:
            reference = None
            if reply_to_id:
                reference = discord.MessageReference(
                    message_id=reply_to_id,
                    channel_id=target_channel_id,
                )

            final_content = content
            if mentions:
                mention_strs = [f"<@{uid}>" for uid in mentions]
                final_content = " ".join(mention_strs) + " " + content

            logger.debug(
                "Sending Discord message",
                extra={
                    "channel_id": target_channel_id,
                    "reply_to": reply_to_id,
                    "content_preview": final_content[:50] if final_content else "",
                },
            )
            return await channel.send(final_content, reference=reference)
        except discord.DiscordException as e:
            logger.error("Failed to send Discord message", extra={"error": str(e)})
            return None

    def get_bot_user_id(self) -> int | None:
        """Return the connected bot's user ID when available."""
        return self.user.id if self.user else None

    def has_pending_messages(self) -> bool:
        """Return whether normalized input is waiting in the queue."""
        return not self._message_queue.empty()

    def pending_message_count(self) -> int:
        """Return the number of normalized messages awaiting input."""
        return self._message_queue.qsize()
