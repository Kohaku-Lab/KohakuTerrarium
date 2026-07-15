"""Store, search, and serialize captioned Discord guild emoji metadata.

Stores emoji information including:
- Basic info: name, id, url, animated
- Generated caption from vision LLM
- Search tags derived from name and caption
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger("kohakuterrarium.custom.emoji_db")


@dataclass
class EmojiRecord:
    """Store searchable metadata and Discord markup for one emoji."""

    emoji_id: int
    name: str
    url: str
    animated: bool
    guild_id: int
    guild_name: str
    caption: str = ""
    tags: list[str] = field(default_factory=list)

    def to_discord_format(self) -> str:
        """Return the Discord markup needed to send this emoji."""
        prefix = "a" if self.animated else ""
        return f"<{prefix}:{self.name}:{self.emoji_id}>"

    def matches_query(self, query: str) -> tuple[bool, float]:
        """Score a query against the emoji name, caption, and tags."""
        query_lower = query.lower()
        query_words = query_lower.split()

        score = 0.0

        # Exact names should dominate every fuzzy caption or tag match.
        if self.name.lower() == query_lower:
            return True, 100.0

        if query_lower in self.name.lower():
            score += 50.0

        if self.name.lower() in query_lower:
            score += 30.0

        caption_lower = self.caption.lower()
        if query_lower in caption_lower:
            score += 40.0

        for word in query_words:
            if len(word) < 2:
                continue
            if word in self.name.lower():
                score += 10.0
            if word in caption_lower:
                score += 5.0
            for tag in self.tags:
                if word in tag.lower():
                    score += 8.0

        for tag in self.tags:
            if tag.lower() == query_lower:
                score += 25.0

        return score > 0, score


@dataclass
class EmojiDatabase:
    """Index guild emojis for filtered relevance search and JSON storage."""

    emojis: dict[int, EmojiRecord] = field(
        default_factory=dict
    )  # IDs are globally unique.
    guilds: dict[int, str] = field(default_factory=dict)  # Preserve names for display.

    def add_emoji(self, emoji: EmojiRecord) -> None:
        """Upsert an emoji and its guild display name."""
        self.emojis[emoji.emoji_id] = emoji
        self.guilds[emoji.guild_id] = emoji.guild_name

    def get_emoji(self, emoji_id: int) -> EmojiRecord | None:
        """Return an emoji by its Discord ID."""
        return self.emojis.get(emoji_id)

    def get_by_name(self, name: str) -> list[EmojiRecord]:
        """Return every case-insensitive exact name match."""
        return [e for e in self.emojis.values() if e.name.lower() == name.lower()]

    def search(
        self,
        query: str,
        limit: int = 10,
        guild_id: int | None = None,
        animated_only: bool = False,
        static_only: bool = False,
    ) -> list[EmojiRecord]:
        """Return relevance-ranked emojis that satisfy optional filters.

        Args:
            query: Search query (matches name, caption, tags)
            limit: Maximum results to return
            guild_id: Filter by guild (None = all guilds)
            animated_only: Only return animated emojis
            static_only: Only return static emojis

        Returns:
            List of matching EmojiRecords sorted by relevance
        """
        results: list[tuple[EmojiRecord, float]] = []

        for emoji in self.emojis.values():
            if guild_id is not None and emoji.guild_id != guild_id:
                continue
            if animated_only and not emoji.animated:
                continue
            if static_only and emoji.animated:
                continue

            matches, score = emoji.matches_query(query)
            if matches:
                results.append((emoji, score))

        results.sort(key=lambda x: x[1], reverse=True)

        return [emoji for emoji, _ in results[:limit]]

    def list_all(self, guild_id: int | None = None) -> list[EmojiRecord]:
        """List all emojis, optionally restricted to one guild."""
        if guild_id is None:
            return list(self.emojis.values())
        return [e for e in self.emojis.values() if e.guild_id == guild_id]

    def stats(self) -> dict:
        """Summarize emoji, animation, caption, and guild counts."""
        total = len(self.emojis)
        animated = sum(1 for e in self.emojis.values() if e.animated)
        captioned = sum(1 for e in self.emojis.values() if e.caption)
        return {
            "total_emojis": total,
            "animated": animated,
            "static": total - animated,
            "captioned": captioned,
            "uncaptioned": total - captioned,
            "guilds": len(self.guilds),
        }

    def to_dict(self) -> dict:
        """Convert the database into JSON-compatible mappings."""
        return {
            "emojis": {str(k): asdict(v) for k, v in self.emojis.items()},
            "guilds": {str(k): v for k, v in self.guilds.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EmojiDatabase":
        """Reconstruct a database from JSON-compatible mappings."""
        db = cls()
        for emoji_id_str, emoji_data in data.get("emojis", {}).items():
            emoji = EmojiRecord(**emoji_data)
            db.emojis[int(emoji_id_str)] = emoji
        for guild_id_str, guild_name in data.get("guilds", {}).items():
            db.guilds[int(guild_id_str)] = guild_name
        return db

    def save(self, path: Path | str) -> None:
        """Persist the database as human-readable UTF-8 JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info("Saved emoji database", extra={"path": str(path), **self.stats()})

    @classmethod
    def load(cls, path: Path | str) -> "EmojiDatabase":
        """Load a database, returning an empty one when the file is absent."""
        path = Path(path)
        if not path.exists():
            logger.warning(
                "Emoji database not found, creating empty", extra={"path": str(path)}
            )
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        db = cls.from_dict(data)
        logger.info("Loaded emoji database", extra={"path": str(path), **db.stats()})
        return db


# Keep generated emoji memory inside the example agent's memory tree.
DEFAULT_DB_PATH = Path(__file__).parent.parent / "memory" / "emojis" / "emoji_db.json"


def get_emoji_db(path: Path | str | None = None) -> EmojiDatabase:
    """Load the requested emoji database or the agent-local default."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    return EmojiDatabase.load(db_path)


def save_emoji_db(db: EmojiDatabase, path: Path | str | None = None) -> None:
    """Save an emoji database to the requested or agent-local path."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    db.save(db_path)
