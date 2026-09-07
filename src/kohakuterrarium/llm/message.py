"""
Define typed text, file, image, and conversation message structures.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class TextPart:
    """Text content part for multimodal messages."""

    text: str
    type: Literal["text"] = "text"

    def to_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format."""
        return {"type": "text", "text": self.text}


@dataclass
class FilePart:
    """Custom file reference part resolved before LLM calls."""

    path: str | None = None
    name: str | None = None
    content: str | None = None
    mime: str | None = None
    data_base64: str | None = None
    encoding: Literal["utf-8", "base64"] | None = None
    is_inline: bool = False
    type: Literal["file"] = "file"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "file",
            "file": {
                "path": self.path,
                "name": self.name,
                "content": self.content,
                "mime": self.mime,
                "data_base64": self.data_base64,
                "encoding": self.encoding,
                "is_inline": self.is_inline,
            },
        }


@dataclass
class ImagePart:
    """Image content with optional source metadata for display."""

    url: str
    detail: Literal["auto", "low", "high"] = "low"
    source_type: str | None = (
        None  # Identifies the display source, such as an attachment, emoji, or frame.
    )
    source_name: str | None = None  # Filename or source identifier shown to users.
    type: Literal["image_url"] = "image_url"

    def to_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format."""
        result = {
            "type": "image_url",
            "image_url": {
                "url": self.url,
                "detail": self.detail,
            },
        }
        if self.source_type or self.source_name:
            result["meta"] = {
                "source_type": self.source_type,
                "source_name": self.source_name,
            }
        return result

    def get_description(self) -> str:
        """Get human-readable description of the image source."""
        if self.source_type and self.source_name:
            return f"[{self.source_type}: {self.source_name}]"
        elif self.source_type:
            return f"[{self.source_type}]"
        return "[image]"


ContentPart = TextPart | ImagePart | FilePart
RawContentPart = dict[str, Any]


def content_part_from_dict(data: dict[str, Any]) -> ContentPart | None:
    """Convert a raw content-part dict into a typed ContentPart."""
    part_type = data.get("type")
    if part_type == "text":
        return TextPart(text=data.get("text", ""))
    if part_type == "image_url":
        img_data = data.get("image_url", {})
        meta = data.get("meta") or {}
        return ImagePart(
            url=img_data.get("url", ""),
            detail=img_data.get("detail", "low"),
            source_type=meta.get("source_type"),
            source_name=meta.get("source_name"),
        )
    if part_type == "file":
        file_data = data.get("file", {})
        return FilePart(
            path=file_data.get("path"),
            name=file_data.get("name"),
            content=file_data.get("content"),
            mime=file_data.get("mime"),
            data_base64=file_data.get("data_base64"),
            encoding=file_data.get("encoding"),
            is_inline=bool(file_data.get("is_inline", False)),
        )
    return None


def normalize_content_parts(
    content: str | list[ContentPart | RawContentPart] | None,
) -> str | list[ContentPart] | None:
    """Normalize content into typed parts where applicable."""
    if content is None or isinstance(content, str):
        return content

    parts: list[ContentPart] = []
    for item in content:
        if isinstance(item, (TextPart, ImagePart, FilePart)):
            parts.append(item)
        elif isinstance(item, dict):
            part = content_part_from_dict(item)
            if part is not None:
                parts.append(part)
    return parts


def content_parts_to_dicts(parts: list[ContentPart]) -> list[dict[str, Any]]:
    """Serialize typed or resumed raw content parts to wire dictionaries."""
    return [part if isinstance(part, dict) else part.to_dict() for part in parts]


def content_display_text(
    content: str | list[ContentPart | RawContentPart] | None,
) -> str:
    """Render content as display text, replacing non-text parts with short labels.

    String content passes through untouched. Multimodal content-part lists
    (e.g. web submissions persisted by ``create_user_input_event``) become
    plain text: text parts are joined with newlines, images render as
    ``[image]``-style descriptions, and files as ``[file: <name>]``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    parts = normalize_content_parts(content)
    if not isinstance(parts, list):
        return ""

    lines: list[str] = []
    for part in parts:
        if isinstance(part, TextPart):
            if part.text:
                lines.append(part.text)
        elif isinstance(part, ImagePart):
            lines.append(part.get_description())
        elif isinstance(part, FilePart):
            label = part.name or part.path or "file"
            lines.append(f"[file: {label}]")
    return "\n".join(lines)


# Non-standard fields are preserved separately so provider state survives round-trips.
_STANDARD_MESSAGE_KEYS = frozenset(
    {"role", "content", "name", "tool_call_id", "tool_calls"}
)


@dataclass
class Message:
    """Conversation message with multimodal content and provider-owned extra fields."""

    role: Role
    content: str | list[ContentPart]
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to OpenAI API format dict."""
        result: dict[str, Any] = {"role": self.role}

        if isinstance(self.content, str):
            result["content"] = self.content
        elif self.content is None:
            result["content"] = None
        else:
            result["content"] = content_parts_to_dicts(self.content)

        if self.name:
            result["name"] = self.name
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls

        # Extras are applied last but cannot overwrite canonical message fields.
        for k, v in (self.extra_fields or {}).items():
            if k in _STANDARD_MESSAGE_KEYS:
                continue
            result[k] = v
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Deserialize a wire message while retaining provider-specific fields."""
        content = data.get("content", "")

        if isinstance(content, list):
            content = normalize_content_parts(content) or []

        extras = {k: v for k, v in data.items() if k not in _STANDARD_MESSAGE_KEYS}
        # Persistence metadata is internal and must never return to providers.
        extras.pop("metadata", None)

        return cls(
            role=data["role"],
            content=content,
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=data.get("tool_calls"),
            extra_fields=extras,
        )

    def get_text_content(self) -> str:
        """Extract text, joining text parts from multimodal content."""
        if isinstance(self.content, str):
            return self.content
        # Native tool-call assistant messages may legally carry ``None`` content.
        if not isinstance(self.content, list):
            return ""
        return "\n".join(
            part.text for part in self.content if isinstance(part, TextPart)
        )

    def has_images(self) -> bool:
        """Check if message contains image content."""
        if not isinstance(self.content, list):
            return False
        return any(isinstance(part, ImagePart) for part in self.content)

    def get_images(self) -> list[ImagePart]:
        """Get all image parts from the message."""
        if not isinstance(self.content, list):
            return []
        return [part for part in self.content if isinstance(part, ImagePart)]

    def is_multimodal(self) -> bool:
        """Check if message uses multimodal content format."""
        return isinstance(self.content, list)


@dataclass
class SystemMessage(Message):
    """System message that sets up the conversation context."""

    role: Role = field(default="system", init=False)

    def __init__(self, content: str, **kwargs: Any):
        super().__init__(role="system", content=content, **kwargs)


@dataclass
class UserMessage(Message):
    """User message supporting text and multimodal content."""

    role: Role = field(default="user", init=False)

    def __init__(
        self,
        content: str | list[ContentPart],
        name: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(role="user", content=content, name=name, **kwargs)


@dataclass
class AssistantMessage(Message):
    """Assistant message in the conversation."""

    role: Role = field(default="assistant", init=False)

    def __init__(self, content: str, name: str | None = None, **kwargs: Any):
        super().__init__(role="assistant", content=content, name=name, **kwargs)


@dataclass
class ToolMessage(Message):
    """Tool result message supporting multimodal outputs."""

    role: Role = field(default="tool", init=False)

    def __init__(
        self,
        content: str | list[ContentPart],
        tool_call_id: str,
        name: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
            **kwargs,
        )


MessageList = list[Message]

MessageContent = str | list[ContentPart]


def messages_to_dicts(messages: MessageList) -> list[dict[str, Any]]:
    """Convert a list of Messages to OpenAI API format.

    Handles both Message objects and raw dicts (e.g. from resumed sessions).
    """
    return [msg if isinstance(msg, dict) else msg.to_dict() for msg in messages]


def dicts_to_messages(dicts: list[dict[str, Any]]) -> MessageList:
    """Convert OpenAI API format dicts to Messages."""
    return [Message.from_dict(d) for d in dicts]


def create_message(
    role: Role,
    content: str | list[ContentPart],
    **kwargs: Any,
) -> Message:
    """Create the role-specific message type while preserving structured content."""
    match role:
        case "system":
            if isinstance(content, list):
                content = "\n".join(p.text for p in content if isinstance(p, TextPart))
            return SystemMessage(content, **kwargs)
        case "user":
            return UserMessage(content, **kwargs)
        case "assistant":
            # Keep non-text parts structured so generated media survives persistence.
            if isinstance(content, list):
                if any(not isinstance(p, TextPart) for p in content):
                    return AssistantMessage(content, **kwargs)
                content = "\n".join(p.text for p in content)
            return AssistantMessage(content, **kwargs)
        case "tool":
            if "tool_call_id" not in kwargs:
                raise ValueError("ToolMessage requires tool_call_id")
            return ToolMessage(content, **kwargs)
        case _:
            return Message(role=role, content=content, **kwargs)


def make_multimodal_content(
    text: str,
    images: list[ImagePart] | None = None,
    prepend_images: bool = False,
) -> str | list[ContentPart]:
    """Return plain text unless images require multimodal content."""
    if not images:
        return text

    text_part = TextPart(text=text)
    if prepend_images:
        return [*images, text_part]
    return [text_part, *images]
