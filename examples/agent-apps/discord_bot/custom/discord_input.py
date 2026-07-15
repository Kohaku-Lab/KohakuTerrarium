"""Convert Discord messages and media into controller trigger events.

Wraps DiscordClient to produce TriggerEvents for the controller.
Uses string.format() templates for flexible context formatting.
Supports multimodal input (images from attachments, stickers, emojis).
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Literal

import discord

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.llm.message import ContentPart, ImagePart, TextPart
from kohakuterrarium.modules.input import BaseInputModule
from kohakuterrarium.utils.logging import get_logger

from discord_client import (
    DiscordClient,
    DiscordMessage,
    register_client,
    short_id,
)
from image_utils import process_multiple_images

logger = get_logger("kohakuterrarium.custom.discord_input")


class DiscordInputModule(BaseInputModule):
    """Build text or multimodal trigger events from batched Discord input."""

    def __init__(
        self,
        token: str | None = None,
        token_env: str = "DISCORD_BOT_TOKEN",
        channel_ids: list[int] | None = None,
        readonly_channel_ids: list[int] | None = None,
        history_limit: int = 50,
        recent_limit: int = 10,
        client_name: str = "default",
        shared_client: DiscordClient | None = None,
        instant_memory_file: str | None = None,
        context_format_file: str | None = None,
        context_files: dict[str, str] | None = None,
        timezone: str | None = None,
        include_images: bool = False,
        include_attachments: bool = True,
        include_stickers: bool = True,
        include_emojis: bool = True,
        image_detail: Literal["auto", "low", "high"] = "low",
        max_images_per_message: int = 4,
        max_total_images: int = 10,
        gif_sample_frames: list[str] | None = None,
    ):
        """Initialize Discord connectivity, context rendering, and media limits.

        Args:
            token: Bot token (or use token_env)
            token_env: Environment variable name for token
            channel_ids: Channel IDs to listen and respond to
            readonly_channel_ids: Channel IDs to observe but not respond in
            history_limit: Background context messages (older, for reference)
            recent_limit: Recent messages to show (newer, to respond to)
            client_name: Name for shared client registry
            shared_client: Share client with output module
            instant_memory_file: Path to memory file to auto-inject
            context_format_file: Template file for context formatting (uses str.format())
            context_files: Dict mapping template vars to file paths
                           e.g., {"character": "./memory/character.md"}
            timezone: Timezone for message timestamps (e.g., "Asia/Tokyo", "America/New_York").
                     If None, uses system local timezone.

            Multimodal options:
            include_images: Enable multimodal image input (master switch)
            include_attachments: Include image attachments
            include_stickers: Include sticker images
            include_emojis: Include custom emoji images
            image_detail: Vision model detail level ("low", "high", "auto")
            max_images_per_message: Max images to include from single message
            max_total_images: Max total images across all messages
            gif_sample_frames: Which frames to sample from GIFs
                              Default: ["first", "middle", "last"]
        """
        import os

        super().__init__()

        self.token = token or os.environ.get(token_env, "")
        if not self.token:
            raise ValueError(
                f"Discord token not provided. Set {token_env} or pass token."
            )

        self.channel_ids = channel_ids
        self.readonly_channel_ids = readonly_channel_ids
        self.history_limit = history_limit
        self.recent_limit = recent_limit
        self.client_name = client_name
        self.instant_memory_file = instant_memory_file
        self.context_format_file = context_format_file
        self.context_files = context_files or {}

        self.include_images = include_images
        self.include_attachments = include_attachments
        self.include_stickers = include_stickers
        self.include_emojis = include_emojis
        self.image_detail: Literal["auto", "low", "high"] = image_detail
        self.max_images_per_message = max_images_per_message
        self.max_total_images = max_total_images
        self.gif_sample_frames = gif_sample_frames or ["first", "middle", "last"]

        # Templates remain optional so the module has a self-contained fallback.
        self._context_template: str | None = None
        if context_format_file:
            self._load_context_template(context_format_file)

        # Resolve debug output beside the agent rather than the process cwd.
        self._debug_output_dir = self._resolve_path("./debug_context")

        logger.info(
            "Initializing Discord input module",
            extra={
                "channel_ids": channel_ids,
                "readonly_channel_ids": readonly_channel_ids,
                "history_limit": history_limit,
                "recent_limit": recent_limit,
                "context_format_file": context_format_file,
                "context_files": list(context_files.keys()) if context_files else [],
                "timezone": timezone or "local",
                "multimodal": include_images,
                "image_detail": image_detail if include_images else None,
            },
        )

        if shared_client:
            self.client = shared_client
            self._owns_client = False
        else:
            self.client = DiscordClient(
                channel_ids=channel_ids,
                readonly_channel_ids=readonly_channel_ids,
                history_limit=history_limit,
                timezone=timezone,
            )
            self._owns_client = True

        register_client(client_name, self.client)
        self._client_task: asyncio.Task | None = None

    def _resolve_path(self, file_path: str) -> Path:
        """Resolve relative paths from the agent folder."""
        path = Path(file_path)
        if path.is_absolute():
            return path
        # Custom modules live one directory below the agent root.
        module_dir = Path(__file__).parent.parent
        return module_dir / file_path

    def _load_context_template(self, template_path: str) -> None:
        """Load an optional str.format context template from the agent folder."""
        try:
            path = self._resolve_path(template_path)
            if path.exists():
                self._context_template = path.read_text(encoding="utf-8")
                logger.debug("Loaded context template", extra={"path": str(path)})
            else:
                logger.warning(
                    "Context template file not found", extra={"path": str(path)}
                )
        except Exception as e:
            logger.error(
                "Failed to load context template",
                extra={"path": template_path, "error": str(e)},
            )

    def _load_context_file(self, file_path: str) -> str:
        """Read a context fragment from the agent folder when available."""
        try:
            path = self._resolve_path(file_path)
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
            else:
                logger.warning("Context file not found", extra={"path": str(path)})
        except Exception as e:
            logger.warning(
                "Failed to load context file",
                extra={"path": file_path, "error": str(e)},
            )
        return ""

    async def _on_start(self) -> None:
        """Connect the Discord client when this module owns it."""
        if self._owns_client:
            logger.info("Starting Discord client...")
            await self.client.login(self.token)
            self._client_task = asyncio.create_task(self.client.connect())
            await self.client.wait_until_ready()
            logger.info("Discord client is ready")

    async def _on_stop(self) -> None:
        """Close and await the Discord task when this module owns it."""
        if self._owns_client and self._client_task:
            await self.client.close()
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass

    def _read_instant_memory(self) -> str:
        """Return the configured instant-memory fragment without wrapping it."""
        if not self.instant_memory_file:
            return ""
        return self._load_context_file(self.instant_memory_file)

    def _build_history_text(self, history_msgs: list[str]) -> str:
        """Number older history messages for compact later references."""
        if not history_msgs:
            return ""
        numbered = [f"#{i} {msg}" for i, msg in enumerate(history_msgs, 1)]
        return "\n".join(numbered)

    def _build_recent_text(self, recent_msgs: list[str], start_num: int) -> str:
        """Number recent messages and identify the newest entry."""
        if not recent_msgs:
            return ""
        numbered = []
        for i, msg in enumerate(recent_msgs):
            num = start_num + i
            if i == len(recent_msgs) - 1:
                numbered.append(f"#{num} [LATEST] {msg}")
            else:
                numbered.append(f"#{num} {msg}")
        return "\n".join(numbered)

    def _build_media_markers(self, msg: DiscordMessage) -> str:
        """Summarize a message's supported media in prompt-readable markers."""
        markers = []

        for att in msg.attachments:
            if att.is_image:
                anim_tag = " (animated GIF)" if att.is_animated else ""
                markers.append(f"[attachment:{att.filename}{anim_tag}]")

        for sticker in msg.stickers:
            anim_tag = " (animated)" if sticker.is_animated else ""
            markers.append(f"[sticker:{sticker.name}{anim_tag}]")

        # Names are sufficient in text; image URLs are carried as vision parts.
        if msg.custom_emojis:
            emoji_names = [f":{e.name}:" for e in msg.custom_emojis]
            animated_count = sum(1 for e in msg.custom_emojis if e.animated)
            if animated_count > 0:
                markers.append(
                    f"[emojis: {', '.join(emoji_names)} ({animated_count} animated)]"
                )
            else:
                markers.append(f"[emojis: {', '.join(emoji_names)}]")

        return " ".join(markers)

    def _build_new_messages_text(
        self, messages: list[DiscordMessage], is_readonly: bool
    ) -> str:
        """Format the newly batched messages with reply and policy markers."""
        formatted_lines = []
        for i, msg in enumerate(messages, 1):
            readonly_marker = "[READONLY] " if is_readonly else ""
            ping_marker = "[PINGED] " if msg.is_mention else ""
            bot_marker = "[BOT] " if msg.is_bot else ""

            if msg.author_display_name != msg.author_name:
                author_info = f"{msg.author_display_name}|{msg.author_name}({msg.short_author_id})"
            else:
                author_info = f"{msg.author_name}({msg.short_author_id})"

            reply_marker = ""
            if msg.reply_to_author:
                reply_bot = "[BOT]" if msg.reply_to_is_bot else ""
                if msg.reply_to_content:
                    quote_preview = msg.reply_to_content[:60]
                    if len(msg.reply_to_content) > 60:
                        quote_preview += "..."
                    reply_marker = (
                        f'[→{reply_bot}{msg.reply_to_author}: "{quote_preview}"] '
                    )
                else:
                    reply_marker = f"[→{reply_bot}{msg.reply_to_author}] "
            elif msg.reply_to_id:
                reply_marker = f"[→msg:{short_id(msg.reply_to_id)}] "

            media_markers = self._build_media_markers(msg)
            media_suffix = f" {media_markers}" if media_markers else ""

            msg_header = f"[{msg.timestamp}] {readonly_marker}{ping_marker}{bot_marker}{reply_marker}[{author_info}]"
            formatted_lines.append(f"NEW#{i} {msg_header}: {msg.content}{media_suffix}")

        return "\n".join(formatted_lines)

    def _build_location(self, last_msg: DiscordMessage) -> str:
        """Build compact bot, server, and channel location context."""
        bot_identity = self.client.get_bot_identity()

        guild_part = ""
        if last_msg.guild_name and last_msg.guild_id:
            guild_short = short_id(last_msg.guild_id)
            guild_part = f"[Server:{last_msg.guild_name}({guild_short})]"

        channel_short = short_id(last_msg.channel_id)
        channel_part = f"[#{last_msg.channel_name}({channel_short})]"

        identity_header = f"[You:{bot_identity}]"
        return f"{identity_header} {guild_part} {channel_part}".strip()

    def _render_with_template(self, template_vars: dict) -> str:
        """Render context with the configured template or built-in fallback."""
        if self._context_template:
            try:
                result = self._context_template.format(**template_vars)
                return result
            except KeyError as e:
                logger.error("Template missing variable", extra={"missing": str(e)})
            except Exception as e:
                logger.error("Template render failed", extra={"error": str(e)})

        fallback = self._render_fallback(template_vars)
        return fallback

    def _save_debug_output(self, content: str) -> None:
        """Persist rendered context for local prompt debugging."""
        try:
            self._debug_output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_file = self._debug_output_dir / f"context_{timestamp}.md"
            debug_file.write_text(content, encoding="utf-8")
            logger.debug("Saved debug context", extra={"path": str(debug_file)})
        except Exception as e:
            logger.warning("Failed to save debug output", extra={"error": str(e)})

    def _render_fallback(self, vars: dict) -> str:
        """Render non-empty context sections in a stable default order."""
        sections = [
            ("Instant Memory", "instant_memory"),
            ("Tool History", "tool_history"),
            ("History", "history"),
            ("Recent", "recent"),
            ("Rules", "rules"),
            ("Character", "character"),
            ("Location", "location"),
        ]

        parts = []
        for header, key in sections:
            if vars.get(key):
                parts.append(f"### {header}\n\n{vars[key]}")

        parts.append(f"### New Messages\n\n{vars.get('new_messages', '')}")

        return "\n\n".join(parts)

    def _collect_image_items(
        self, messages: list[DiscordMessage]
    ) -> list[tuple[str, str, str, bool]]:
        """Collect bounded image descriptors from messages for processing."""
        items: list[tuple[str, str, str, bool]] = []
        images_collected = 0

        for msg in messages:
            msg_images = 0

            if self.include_attachments:
                for att in msg.attachments:
                    if att.is_image and msg_images < self.max_images_per_message:
                        items.append(
                            (att.url, "attachment", att.filename, att.is_animated)
                        )
                        msg_images += 1
                        images_collected += 1

            if self.include_stickers:
                for sticker in msg.stickers:
                    if msg_images < self.max_images_per_message:
                        # The image pipeline cannot rasterize Lottie vectors.
                        if sticker.format_type == "lottie":
                            continue
                        items.append(
                            (
                                sticker.url,
                                "sticker",
                                sticker.name,
                                sticker.is_animated,
                            )
                        )
                        msg_images += 1
                        images_collected += 1

            if self.include_emojis:
                for emoji in msg.custom_emojis:
                    if msg_images < self.max_images_per_message:
                        items.append((emoji.url, "emoji", emoji.name, emoji.animated))
                        msg_images += 1
                        images_collected += 1

            if images_collected >= self.max_total_images:
                break

        return items[: self.max_total_images]

    async def _process_images(self, messages: list[DiscordMessage]) -> list[ImagePart]:
        """Download, sample, and encode message images as vision parts."""
        items = self._collect_image_items(messages)
        if not items:
            return []

        logger.debug(
            "Processing images",
            extra={"count": len(items), "types": [t for _, t, _, _ in items]},
        )

        processed = await process_multiple_images(
            items,
            max_images=self.max_total_images,
            gif_sample_positions=self.gif_sample_frames,
        )

        image_parts: list[ImagePart] = []
        for img in processed:
            if img.frame_info:
                source_name = f"{img.source_name} ({img.frame_info})"
            else:
                source_name = img.source_name

            image_parts.append(
                ImagePart(
                    url=img.data_url,
                    detail=self.image_detail,
                    source_type=img.source_type,
                    source_name=source_name,
                )
            )

        logger.debug("Processed images", extra={"result_count": len(image_parts)})
        return image_parts

    def _build_multimodal_content(
        self, text: str, images: list[ImagePart]
    ) -> str | list[ContentPart]:
        """Return plain text or image-first multimodal content as needed."""
        if not images:
            return text

        parts: list[ContentPart] = []

        image_desc_lines = []
        for i, img in enumerate(images, 1):
            desc = img.get_description()
            image_desc_lines.append(f"Image {i}: {desc}")

        image_header = (
            "## Attached Images\n\n"
            + "\n".join(image_desc_lines)
            + "\n\n(Images shown below as visual content)\n\n"
        )

        parts.append(TextPart(text=image_header))

        parts.extend(images)

        parts.append(TextPart(text=text))

        return parts

    async def get_input(self) -> TriggerEvent | None:
        """Batch pending Discord messages into one contextual trigger event."""
        if not self._running:
            return None

        try:
            first_msg = await asyncio.wait_for(
                self.client.get_message(),
                timeout=1.0,
            )

            await asyncio.sleep(0.5)

            messages: list[DiscordMessage] = [first_msg]

            while True:
                try:
                    extra_msg = self.client._message_queue.get_nowait()
                    messages.append(extra_msg)
                except asyncio.QueueEmpty:
                    break

            logger.info(
                "Messages consumed from queue",
                extra={
                    "consumed_count": len(messages),
                    "authors": [m.author_display_name for m in messages],
                },
            )

            last_msg = messages[-1]
            self.client.set_output_context(channel_id=last_msg.channel_id)

            is_readonly = self.client.is_readonly_channel(last_msg.channel_id)
            any_mention = any(m.is_mention for m in messages)

            # History is fetched after choosing the last message's output channel.
            channel = self.client.get_channel(last_msg.channel_id)
            if not channel:
                try:
                    channel = await self.client.fetch_channel(last_msg.channel_id)
                except discord.DiscordException:
                    channel = None

            # Keep recent messages distinct because they receive stronger prompt markers.
            history_text = ""
            recent_text = ""
            recent_media_messages: list[DiscordMessage] = []
            if channel and isinstance(channel, (discord.TextChannel, discord.Thread)):
                all_history = await self.client.fetch_channel_history(channel)
                if all_history:
                    total = len(all_history)
                    if total > self.recent_limit:
                        history_msgs = all_history[: total - self.recent_limit]
                        recent_msgs = all_history[total - self.recent_limit :]
                    else:
                        history_msgs = []
                        recent_msgs = all_history

                    history_text = self._build_history_text(history_msgs)
                    recent_text = self._build_recent_text(
                        recent_msgs, len(history_msgs) + 1
                    )

                # Media history is fetched separately because text history drops URLs.
                if self.include_images:
                    recent_media_messages = (
                        await self.client.fetch_channel_history_with_media(
                            channel, limit=self.recent_limit
                        )
                    )

            # Template values stay raw so custom templates control all presentation.
            template_vars = {
                "instant_memory": self._read_instant_memory(),
                "history": history_text,
                "recent": recent_text,
                "new_messages": self._build_new_messages_text(messages, is_readonly),
                "location": self._build_location(last_msg),
                "tool_history": "",
            }

            # Named context fragments map directly to custom template variables.
            for var_name, file_path in self.context_files.items():
                template_vars[var_name] = self._load_context_file(file_path)

            formatted_text = self._render_with_template(template_vars)

            image_parts: list[ImagePart] = []
            if self.include_images:
                # Include recent context and newly arrived media without duplicating IDs.
                all_media_messages = recent_media_messages.copy()

                recent_ids = {m.message_id for m in recent_media_messages}
                for msg in messages:
                    if msg.message_id not in recent_ids and msg.has_media():
                        all_media_messages.append(msg)

                if all_media_messages:
                    image_parts = await self._process_images(all_media_messages)
                    logger.info(
                        "Processed multimodal content",
                        extra={
                            "image_count": len(image_parts),
                            "from_recent": len(recent_media_messages),
                            "from_new": len(all_media_messages)
                            - len(recent_media_messages),
                            "sources": [
                                f"{img.source_type}:{img.source_name}"
                                for img in image_parts[:5]  # Bound structured log volume.
                            ],
                        },
                    )

            final_content = self._build_multimodal_content(formatted_text, image_parts)

            total_attachments = sum(len(m.attachments) for m in messages)
            total_stickers = sum(len(m.stickers) for m in messages)
            total_emojis = sum(len(m.custom_emojis) for m in messages)

            return TriggerEvent(
                type="user_input",
                content=final_content,
                context={
                    **last_msg.to_context(),
                    "is_readonly": is_readonly,
                    "bot_identity": self.client.get_bot_identity(),
                    "is_mention": any_mention,
                    "message_count": len(messages),
                    "multimodal": len(image_parts) > 0,
                    "image_count": len(image_parts),
                    "total_attachments": total_attachments,
                    "total_stickers": total_stickers,
                    "total_emojis": total_emojis,
                },
                stackable=True,
            )
        except asyncio.TimeoutError:
            return None

    def get_client(self) -> DiscordClient:
        """Return the client shared with output and trigger modules."""
        return self.client
