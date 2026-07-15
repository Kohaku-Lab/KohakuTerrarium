"""Pydantic request/response models for the HTTP API."""

from typing import Literal

from pydantic import BaseModel


class TerrariumCreate(BaseModel):
    """Request body for creating a terrarium."""

    config_path: str
    llm: str | None = None  # Overrides every creature's configured LLM profile.
    pwd: str | None = None  # Defaults to the server process working directory.
    name: str | None = None  # Defaults to the recipe name.
    on_node: str | None = None  # Absence selects the Laboratory host.


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
    direction: str  # Channel wiring accepts only the listen or send direction.
    enabled: bool = True


class AgentCreate(BaseModel):
    """Request body for creating a standalone agent."""

    config_path: str
    llm: str | None = None  # Overrides the configured LLM profile.
    pwd: str | None = None  # Defaults to the server process working directory.
    name: str | None = None  # Defaults to the creature configuration name.
    on_node: str | None = None  # Absence selects the Laboratory host.


class RenameRequest(BaseModel):
    """Request body for renaming a session or creature."""

    name: str


class ModelSwitch(BaseModel):
    """Request body for switching an agent/creature's LLM model."""

    model: str  # LLM profile name rather than a provider-specific model ID.


class AgentChat(BaseModel):
    """Request body for sending a chat message to an agent."""

    message: str | None = None
    content: list[ContentPartPayload] | None = None


class RegenerateRequest(BaseModel):
    """Select the turn and branch view used to regenerate a response.

    Omitting ``turn_index`` regenerates the conversation tail. A specific turn
    opens a branch there, and ``branch_view`` restores the selected subtree before
    branching from a non-latest path.
    """

    turn_index: int | None = None
    branch_view: dict[int, int] | None = None


class MessageEdit(BaseModel):
    """Request body for editing a user message and re-running."""

    # Text-only and multimodal edits share the same payload accepted by chat.
    content: str | list[ContentPartPayload]
    # Visible-user coordinates remain stable when hidden system or tool messages
    # change raw conversation indices.
    turn_index: int | None = None
    user_position: int | None = None
    # Restore the selected subtree before resolving edits on a non-latest branch.
    branch_view: dict[int, int] | None = None


class SlashCommand(BaseModel):
    """Request body for executing a slash command."""

    command: str  # Command name excludes the leading slash.
    args: str = ""  # Unparsed argument text follows the command name.


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
