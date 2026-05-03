"""Pydantic request/response models for the HTTP API."""

from typing import Literal

from pydantic import BaseModel


class TerrariumCreate(BaseModel):
    """Request body for creating a terrarium."""

    config_path: str
    llm: str | None = None  # LLM profile override for all creatures
    pwd: str | None = None  # Working directory (default: server cwd)
    name: str | None = None  # Display name override (defaults to recipe name)


class TerrariumStatus(BaseModel):
    """Response model for terrarium status."""

    terrarium_id: str
    name: str
    running: bool
    creatures: dict
    channels: list


class CreatureAdd(BaseModel):
    """Request body for adding a creature to a terrarium."""

    name: str
    config_path: str
    listen_channels: list[str] = []
    send_channels: list[str] = []


class TextPartPayload(BaseModel):
    type: Literal["text"]
    text: str


class ImageUrlPayload(BaseModel):
    url: str
    detail: Literal["auto", "low", "high"] = "low"


class ContentMetaPayload(BaseModel):
    source_type: str | None = None
    source_name: str | None = None


class ImagePartPayload(BaseModel):
    type: Literal["image_url"]
    image_url: ImageUrlPayload
    meta: ContentMetaPayload | None = None


class FilePayload(BaseModel):
    path: str | None = None
    name: str | None = None
    content: str | None = None
    mime: str | None = None
    data_base64: str | None = None
    encoding: Literal["utf-8", "base64"] | None = None
    is_inline: bool = False


class FilePartPayload(BaseModel):
    type: Literal["file"]
    file: FilePayload


ContentPartPayload = TextPartPayload | ImagePartPayload | FilePartPayload


class ChannelSend(BaseModel):
    """Request body for sending a message to a channel."""

    content: str | list[ContentPartPayload]
    sender: str = "human"


class ChannelAdd(BaseModel):
    """Request body for adding a channel to a terrarium."""

    name: str
    channel_type: str = "queue"
    description: str = ""


class WireChannel(BaseModel):
    """Request body for wiring a creature to a channel."""

    channel: str
    direction: str  # "listen" or "send"
    enabled: bool = True


class AgentCreate(BaseModel):
    """Request body for creating a standalone agent."""

    config_path: str
    llm: str | None = None  # LLM profile override
    pwd: str | None = None  # Working directory (default: server cwd)
    name: str | None = None  # Display name override (defaults to config name)


class RenameRequest(BaseModel):
    """Request body for renaming a session or creature."""

    name: str


class ModelSwitch(BaseModel):
    """Request body for switching an agent/creature's LLM model."""

    model: str  # Profile name (e.g. "claude-opus-4.6", "gemini-3.1-pro")


class AgentChat(BaseModel):
    """Request body for sending a chat message to an agent."""

    message: str | None = None
    content: list[ContentPartPayload] | None = None


class MessageEdit(BaseModel):
    """Request body for editing a user message and re-running."""

    # Accept either a plain string (legacy / text-only edit) or a list of
    # multimodal content parts — same shape as ``AgentChat.content`` —
    # so the frontend's ``buildMessageParts`` output is valid.
    content: str | list[ContentPartPayload]
    # Prefer stable visible-user targeting over raw conversation indices.
    # ``msg_idx`` remains in the URL for backward compatibility, but the
    # frontend sends one of these fields so system/tool messages cannot
    # shift the target.
    turn_index: int | None = None
    user_position: int | None = None


class SlashCommand(BaseModel):
    """Request body for executing a slash command."""

    command: str  # Command name without slash (e.g. "model", "status")
    args: str = ""  # Arguments string


class FileWrite(BaseModel):
    """Request body for writing a file."""

    path: str
    content: str


class FileRename(BaseModel):
    """Request body for renaming/moving a file."""

    old_path: str
    new_path: str


class FileDelete(BaseModel):
    """Request body for deleting a file."""

    path: str


class FileMkdir(BaseModel):
    """Request body for creating a directory."""

    path: str


class ForkMutationPayload(BaseModel):
    """Description of the optional fork-point mutation.

    ``kind`` picks the canned mutator; ``args`` carry mutator-specific
    parameters (validated by the route handler).
    """

    kind: Literal[
        "drop_trailing",
        "edit_user_message",
        "inject_user_message",
        "inject_tool_result",
    ]
    args: dict | None = None


class ForkRequest(BaseModel):
    """Request body for ``POST /sessions/{id}/fork``."""

    at_event_id: int
    mutate: ForkMutationPayload | None = None
    name: str | None = None


class ForkResponse(BaseModel):
    """Response body for a successful fork."""

    session_id: str
    fork_point: int
    path: str
