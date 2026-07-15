"""Build a searchable guild-emoji database with optional vision captions.

Usage:
    python emoji_builder.py --env ../env --guild-ids 123,456
    python emoji_builder.py --env ../env  # Fetch every visible guild.

This script:
1. Connects to Discord and fetches emojis from specified guilds
2. Downloads each emoji image
3. Uses a vision LLM to generate a caption/description
4. Saves everything to the emoji database
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

import discord
import httpx
import yaml

# Standalone execution must mirror ModuleLoader's sibling-import path setup.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))

from kohakuterrarium.llm.message import ImagePart, Message, TextPart
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.utils.logging import get_logger

from emoji_db import DEFAULT_DB_PATH, EmojiDatabase, EmojiRecord, save_emoji_db
from image_utils import (
    convert_image_to_jpeg,
    extract_animated_frames,
    image_to_data_url,
)

logger = get_logger("kohakuterrarium.emoji_builder")

# The custom folder sits directly beneath the agent configuration.
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_llm_config_from_yaml(config_path: Path | None = None) -> dict:
    """Load caption-model settings from the agent configuration."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        logger.warning("Config file not found", extra={"path": str(path)})
        return {}

    with open(path) as f:
        config = yaml.safe_load(f)

    controller = config.get("controller", {})
    return {
        "model": controller.get("model", ""),
        "base_url": controller.get("base_url", "https://openrouter.ai/api/v1"),
        "api_key_env": controller.get("api_key_env", "OPENROUTER_API_KEY"),
    }


def load_env_file(env_path: str) -> None:
    """Load simple KEY=VALUE entries, replacing existing environment values."""
    path = Path(env_path)
    if not path.exists():
        print(f"Warning: env file not found: {env_path}")
        return

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                os.environ[key] = value


async def download_emoji_image(url: str, timeout: float = 10.0) -> bytes | None:
    """Download one emoji image from Discord's CDN."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=timeout, follow_redirects=True)
            if response.status_code == 200:
                return response.content
            logger.warning(
                "Failed to download emoji",
                extra={"url": url[:50], "status": response.status_code},
            )
    except Exception as e:
        logger.warning(
            "Error downloading emoji", extra={"url": url[:50], "error": str(e)}
        )
    return None


def prepare_emoji_for_vision(image_data: bytes, animated: bool) -> str | None:
    """Convert a static emoji or representative animation frame to JPEG data."""
    if animated:
        # A middle frame is more representative than animation boundaries.
        frames = extract_animated_frames(image_data, sample_positions=["middle"])
        if frames:
            frame_data, _ = frames[0]
            return image_to_data_url(frame_data, "image/jpeg")
        # Some animated payloads remain decodable as a single image.
        jpeg_data = convert_image_to_jpeg(image_data)
        if jpeg_data:
            return image_to_data_url(jpeg_data, "image/jpeg")
        return None
    else:
        jpeg_data = convert_image_to_jpeg(image_data)
        if jpeg_data:
            return image_to_data_url(jpeg_data, "image/jpeg")
        return None


async def caption_emoji_with_llm(
    llm: OpenAIProvider,
    data_url: str,
    emoji_name: str,
) -> str:
    """Generate a concise, searchable caption with a vision model.

    Args:
        llm: LLM provider instance
        data_url: Base64 data URL of the emoji image
        emoji_name: Name of the emoji (for context)

    Returns:
        Generated caption string
    """
    prompt = f"""Describe this Discord emoji in 1-2 short sentences.
Focus on: what it depicts, the emotion/mood, and when it might be used.
The emoji is named "{emoji_name}".
Be concise and specific. Do not start with "This emoji" or similar."""

    message = Message(
        role="user",
        content=[
            TextPart(text=prompt),
            ImagePart(url=data_url, detail="low"),
        ],
    )

    # Captions are short and consumed atomically, so streaming adds no value.
    response = await llm._complete_chat([message.to_dict()])
    return response.content.strip()


def extract_tags_from_caption(caption: str, emoji_name: str) -> list[str]:
    """Derive bounded search tags from the emoji name and caption."""
    tags = set()

    for part in emoji_name.split("_"):
        if len(part) >= 2:
            tags.add(part.lower())

    words = re.findall(r"\b[a-zA-Z]{3,}\b", caption.lower())

    # Exclude generic caption vocabulary that carries little search value.
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "are",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "emoji",
        "used",
        "when",
        "someone",
        "something",
        "showing",
        "depicts",
        "features",
        "often",
    }

    for word in words:
        if word not in stop_words and len(word) >= 3:
            tags.add(word)

    return list(tags)[:15]  # Bound serialized database and prompt size.


class EmojiBuilderClient(discord.Client):
    """Connect only to enumerate emojis from selected guilds."""

    def __init__(self, guild_ids: list[int] | None = None):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.emojis = True
        super().__init__(intents=intents)

        self.target_guild_ids = set(guild_ids) if guild_ids else None
        self.ready_event = asyncio.Event()

    async def on_ready(self) -> None:
        logger.info(
            "Connected to Discord",
            extra={"user": str(self.user), "guilds": len(self.guilds)},
        )
        self.ready_event.set()

    def get_target_guilds(self) -> list[discord.Guild]:
        """Return selected guilds, or every visible guild when unrestricted."""
        if self.target_guild_ids:
            return [g for g in self.guilds if g.id in self.target_guild_ids]
        return list(self.guilds)


async def build_emoji_database(
    token: str,
    guild_ids: list[int] | None = None,
    output_path: Path | None = None,
    llm_api_key: str | None = None,
    llm_base_url: str | None = None,
    llm_model: str | None = None,
    skip_captioning: bool = False,
    update_existing: bool = False,
    batch_size: int = 5,
    delay_between_batches: float = 1.0,
) -> EmojiDatabase:
    """Build or update an emoji database from selected Discord guilds.

    Args:
        token: Discord bot token
        guild_ids: List of guild IDs to fetch from (None = all guilds)
        output_path: Path to save database (None = default)
        llm_api_key: API key for LLM (from env/config if not provided)
        llm_base_url: LLM API base URL (from config if not provided)
        llm_model: Model to use for captioning (from config if not provided)
        skip_captioning: Skip caption generation (just fetch metadata)
        update_existing: Update existing database instead of replacing
        batch_size: Number of emojis to caption in parallel
        delay_between_batches: Delay between batches (rate limiting)

    Returns:
        Built EmojiDatabase
    """
    db_path = output_path or DEFAULT_DB_PATH

    if update_existing and db_path.exists():
        db = EmojiDatabase.load(db_path)
        logger.info("Loaded existing database for update", extra=db.stats())
    else:
        db = EmojiDatabase()

    yaml_config = load_llm_config_from_yaml()

    llm: OpenAIProvider | None = None
    if not skip_captioning:
        # Explicit arguments override agent config and built-in defaults.
        resolved_model = llm_model or yaml_config.get(
            "model", "google/gemini-2.0-flash-001"
        )
        resolved_base_url = llm_base_url or yaml_config.get(
            "base_url", "https://openrouter.ai/api/v1"
        )
        api_key_env = yaml_config.get("api_key_env", "OPENROUTER_API_KEY")
        api_key = llm_api_key or os.environ.get(api_key_env)

        # Agent configs may defer the model name through ${VAR:default}.
        if resolved_model.startswith("${") and "}" in resolved_model:
            var_part = resolved_model[2 : resolved_model.index("}")]
            if ":" in var_part:
                var_name, default = var_part.split(":", 1)
            else:
                var_name, default = var_part, ""
            resolved_model = os.environ.get(var_name, default)

        if not api_key:
            logger.warning("No LLM API key provided, skipping captioning")
            skip_captioning = True
        else:
            logger.info(
                "Using LLM for captioning",
                extra={"model": resolved_model, "base_url": resolved_base_url},
            )
            llm = OpenAIProvider(
                api_key=api_key,
                base_url=resolved_base_url,
                model=resolved_model,
                max_tokens=150,
            )

    client = EmojiBuilderClient(guild_ids)

    async def run_builder() -> None:
        await client.wait_until_ready()

        guilds = client.get_target_guilds()
        logger.info("Fetching emojis from guilds", extra={"guild_count": len(guilds)})

        all_emojis: list[tuple[discord.Emoji, discord.Guild]] = []

        for guild in guilds:
            logger.info(
                f"Processing guild: {guild.name}",
                extra={"guild_id": guild.id, "emoji_count": len(guild.emojis)},
            )
            for emoji in guild.emojis:
                all_emojis.append((emoji, guild))

        logger.info(f"Total emojis to process: {len(all_emojis)}")

        # Batch caption requests to limit provider concurrency.
        for i in range(0, len(all_emojis), batch_size):
            batch = all_emojis[i : i + batch_size]
            tasks = [
                process_emoji(emoji, guild, db, llm, skip_captioning, update_existing)
                for emoji, guild in batch
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            processed = min(i + batch_size, len(all_emojis))
            logger.info(f"Progress: {processed}/{len(all_emojis)} emojis processed")

            if i + batch_size < len(all_emojis):
                await asyncio.sleep(delay_between_batches)

        save_emoji_db(db, db_path)

        if llm:
            await llm.close()
        await client.close()

    try:
        await client.login(token)
        task = asyncio.create_task(client.connect())
        await client.ready_event.wait()
        await run_builder()
        task.cancel()
    except Exception as e:
        logger.error("Error building database", extra={"error": str(e)})
        raise

    return db


async def process_emoji(
    emoji: discord.Emoji,
    guild: discord.Guild,
    db: EmojiDatabase,
    llm: OpenAIProvider | None,
    skip_captioning: bool,
    update_existing: bool,
) -> None:
    """Download, caption, and persist one emoji record when needed."""
    try:
        existing = db.get_emoji(emoji.id)
        if (
            existing
            and existing.caption
            and not (update_existing and not skip_captioning)
        ):
            logger.debug(f"Skipping existing emoji: {emoji.name}")
            return

        ext = "gif" if emoji.animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji.id}.{ext}"

        caption = ""
        tags: list[str] = []

        if not skip_captioning and llm:
            image_data = await download_emoji_image(url)
            if image_data:
                data_url = prepare_emoji_for_vision(image_data, emoji.animated)
                if data_url:
                    try:
                        caption = await caption_emoji_with_llm(
                            llm, data_url, emoji.name
                        )
                        tags = extract_tags_from_caption(caption, emoji.name)
                        logger.debug(
                            f"Captioned emoji: {emoji.name}",
                            extra={"caption": caption[:50]},
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to caption {emoji.name}", extra={"error": str(e)}
                        )
        else:
            # Metadata-only updates preserve any existing human or model caption.
            if existing:
                caption = existing.caption
                tags = existing.tags

        record = EmojiRecord(
            emoji_id=emoji.id,
            name=emoji.name,
            url=url,
            animated=emoji.animated,
            guild_id=guild.id,
            guild_name=guild.name,
            caption=caption,
            tags=tags or extract_tags_from_caption("", emoji.name),
        )

        db.add_emoji(record)

    except Exception as e:
        logger.error(f"Error processing emoji {emoji.name}", extra={"error": str(e)})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build emoji database from Discord guilds"
    )
    parser.add_argument("--env", help="Path to env file to load (KEY=VALUE format)")
    parser.add_argument("--token", help="Discord bot token")
    parser.add_argument(
        "--token-env",
        default="DISCORD_BOT_TOKEN",
        help="Environment variable for token",
    )
    parser.add_argument(
        "--guild-ids", help="Comma-separated guild IDs (default: all guilds)"
    )
    parser.add_argument("--output", help="Output database path")
    parser.add_argument(
        "--llm-base-url",
        help="LLM API base URL (default: from config.yaml)",
    )
    parser.add_argument(
        "--llm-model",
        help="LLM model for captioning (default: from config.yaml)",
    )
    parser.add_argument("--llm-api-key", help="API key for LLM")
    parser.add_argument(
        "--skip-captions", action="store_true", help="Skip caption generation"
    )
    parser.add_argument(
        "--update", action="store_true", help="Update existing database"
    )
    parser.add_argument(
        "--batch-size", type=int, default=5, help="Batch size for captioning"
    )
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Delay between batches (seconds)"
    )

    args = parser.parse_args()

    if args.env:
        load_env_file(args.env)

    token = args.token or os.environ.get(args.token_env)
    if not token:
        print(f"Error: No token provided. Set {args.token_env} or use --token")
        sys.exit(1)

    guild_ids = None
    if args.guild_ids:
        guild_ids = [int(gid.strip()) for gid in args.guild_ids.split(",")]

    output_path = Path(args.output) if args.output else None

    asyncio.run(
        build_emoji_database(
            token=token,
            guild_ids=guild_ids,
            output_path=output_path,
            llm_api_key=args.llm_api_key,
            llm_base_url=args.llm_base_url,
            llm_model=args.llm_model,
            skip_captioning=args.skip_captions,
            update_existing=args.update,
            batch_size=args.batch_size,
            delay_between_batches=args.delay,
        )
    )


if __name__ == "__main__":
    main()
