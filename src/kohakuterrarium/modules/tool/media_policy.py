"""Declare how a tool's media results are transported and presented.

A tool result may carry images or video. Two questions follow every such
result and the answers belong to the tool, not to the executor or the UI:

* Is this media the tool's *product* (generated, worth keeping with the
  session) or a *reference* to something that already exists on disk?
* Should a UI keep the media on screen when the tool block is collapsed?

:class:`MediaPolicy` answers both. A tool sets its default on the class; a
single result may override it through ``ToolResult.metadata["media_policy"]``.
"""

from dataclasses import dataclass
from typing import Any

METADATA_KEY = "media_policy"


@dataclass(frozen=True)
class MediaPolicy:
    """Transport and presentation rules for inline media in a tool result.

    ``persist`` copies inline (``data:``) media into the session artifact
    store and lists the copy as a saved artifact. A tool whose media already
    lives on disk returns a ``file://`` reference and sets this False so nothing
    is duplicated; inline data under ``persist=False`` stays inline and is the
    tool's own responsibility.

    ``pinned`` keeps the media visible when the tool block is collapsed. Set
    it False for media that is context rather than output, such as a file the
    creature looked at.
    """

    persist: bool = True
    pinned: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {"persist": self.persist, "pinned": self.pinned}

    @classmethod
    def coerce(cls, value: Any, base: "MediaPolicy | None" = None) -> "MediaPolicy":
        """Build a policy from a policy, a mapping, or nothing, over ``base``."""
        base = base or cls()
        if isinstance(value, MediaPolicy):
            return value
        if isinstance(value, dict):
            return cls(
                persist=bool(value.get("persist", base.persist)),
                pinned=bool(value.get("pinned", base.pinned)),
            )
        return base


def resolve_media_policy(tool: Any, metadata: dict[str, Any] | None) -> MediaPolicy:
    """Return the effective policy: the result override on top of the tool default."""
    base = MediaPolicy.coerce(getattr(tool, "media_policy", None))
    override = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    return MediaPolicy.coerce(override, base)


__all__ = ["METADATA_KEY", "MediaPolicy", "resolve_media_policy"]
