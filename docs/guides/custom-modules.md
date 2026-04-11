# Custom Modules

KohakuTerrarium is designed around a modular architecture where every component -- tools, input, output, triggers, and sub-agents -- follows a well-defined protocol. When the built-in modules don't fit your use case, you can write your own and plug them in through configuration alone. No framework source code changes required.

This guide walks through building each type of custom module from scratch with complete, runnable examples and the YAML configuration to wire them into your creature.

## Table of Contents

1. [Introduction](#introduction)
2. [Custom Tools](#custom-tools)
3. [Custom Input Modules](#custom-input-modules)
4. [Custom Output Modules](#custom-output-modules)
5. [Custom Triggers](#custom-triggers)
6. [Custom Sub-Agents](#custom-sub-agents)
7. [Packaging and Distribution](#packaging-and-distribution)

---

## Introduction

### Why Custom Modules?

The built-in modules cover common scenarios: shell execution, file reading, CLI input, stdout output, timer triggers. But real-world agents often need domain-specific capabilities:

- **Tools**: Call your internal APIs, query a proprietary database, interact with hardware.
- **Input**: Receive messages from Slack, Discord, a web socket, or a voice pipeline.
- **Output**: Post to webhooks, stream to a TTS engine, write to a dashboard.
- **Triggers**: Watch files for changes, poll an RSS feed, listen to a message queue.
- **Sub-Agents**: Create specialized sub-agents with custom prompts, tool sets, and behaviors.

### When to Use Each Type

| You want to... | Module type |
|---|---|
| Give the LLM a new capability it can call | **Tool** |
| Change where user input comes from | **Input** |
| Change where agent output goes | **Output** |
| Make the agent act autonomously on events | **Trigger** |
| Create a specialized nested agent the controller can delegate to | **Sub-Agent** |

### Project Layout Convention

Custom modules live in a `custom/` directory inside your creature folder:

```
my_creature/
  config.yaml
  system.md
  custom/
    tools/
      weather.py
    inputs/
      slack_input.py
    outputs/
      discord_webhook.py
    triggers/
      file_watcher.py
    subagents/
      code_reviewer.py
```

The framework's `ModuleLoader` resolves paths relative to your creature folder, so `./custom/tools/weather.py` in your config means `<creature_folder>/custom/tools/weather.py`.

---

## Custom Tools

Tools are the most commonly extended module type. They give the LLM new capabilities -- anything from calling an API to running a database query.

### What You'll Build

A **weather tool** that fetches current weather data from the Open-Meteo API (free, no API key required).

### The BaseTool Class

Every custom tool extends `BaseTool` and implements these pieces:

| Member | Type | Purpose |
|---|---|---|
| `tool_name` | property | Unique identifier used in tool calls |
| `description` | property | One-line description shown in the system prompt |
| `execution_mode` | property | `DIRECT` (blocking), `BACKGROUND` (async status), or `STATEFUL` (multi-turn) |
| `needs_context` | class var | Set `True` to receive `ToolContext` with agent state |
| `_execute(args, **kwargs)` | method | The actual implementation -- returns `ToolResult` |

The base class handles error wrapping automatically: if `_execute` raises an exception, it gets caught and returned as a `ToolResult(error=...)`.

### ToolResult

Every tool returns a `ToolResult`:

```python
@dataclass
class ToolResult:
    output: str | list[ContentPart] = ""  # Text or multimodal content
    exit_code: int | None = None          # 0 = success, non-zero = failure
    error: str | None = None              # Error message if failed
    metadata: dict[str, Any] = field(default_factory=dict)
```

Key properties:
- `success` -- `True` when `error is None` and `exit_code` is `None` or `0`
- `get_text_output()` -- extracts text from multimodal results
- `has_images()` / `is_multimodal()` -- check content type

### ExecutionMode

- **`DIRECT`** -- Tool runs and returns immediately. The LLM waits for the result before continuing. Best for fast operations (file reads, API calls under a few seconds).
- **`BACKGROUND`** -- Tool runs in the background. The controller receives periodic status updates and can continue working. Best for long-running operations (builds, deployments).
- **`STATEFUL`** -- Tool maintains state across multiple interactions, like a generator. Best for interactive sessions (debugger, REPL).

Most custom tools use `DIRECT`.

### Step-by-Step: Weather Tool

Create the file `my_creature/custom/tools/weather.py`:

```python
"""
Weather tool -- fetch current weather from Open-Meteo API.
"""

import httpx

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Geocoding and weather endpoints (free, no API key)
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherTool(BaseTool):
    """Fetch current weather conditions for a given location."""

    def __init__(self, config: ToolConfig | None = None):
        super().__init__(config)

    @property
    def tool_name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return "Get current weather conditions for a location"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        """Optional: JSON Schema for tool parameters.

        The controller uses this to understand what arguments to pass.
        If you provide get_full_documentation() instead, this is not
        strictly required -- but it helps with native function calling.
        """
        return {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name (e.g. 'Tokyo', 'New York')",
                },
            },
            "required": ["location"],
        }

    def get_full_documentation(self, tool_format: str = "native") -> str:
        """Full documentation shown when the controller uses ##info weather##."""
        return (
            "# weather\n\n"
            "Get current weather conditions for any location worldwide.\n\n"
            "## Parameters\n"
            "- **location** (required): City name, e.g. 'Tokyo', 'London', "
            "'San Francisco'\n\n"
            "## Returns\n"
            "Current temperature, wind speed, and weather description.\n\n"
            "## Examples\n"
            "```\n"
            "weather(location=\"Tokyo\")\n"
            "weather(location=\"New York\")\n"
            "```\n"
        )

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Fetch weather data."""
        location = args.get("location", "")
        if not location:
            return ToolResult(error="No location provided")

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: Geocode the location
            geo_resp = await client.get(
                GEOCODE_URL,
                params={"name": location, "count": 1},
            )
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()

            results = geo_data.get("results", [])
            if not results:
                return ToolResult(
                    error=f"Location not found: {location}",
                )

            place = results[0]
            lat = place["latitude"]
            lon = place["longitude"]
            name = place.get("name", location)
            country = place.get("country", "")

            # Step 2: Fetch current weather
            weather_resp = await client.get(
                WEATHER_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                },
            )
            weather_resp.raise_for_status()
            weather_data = weather_resp.json()

            current = weather_data.get("current_weather", {})
            temp = current.get("temperature", "N/A")
            wind = current.get("windspeed", "N/A")
            code = current.get("weathercode", -1)
            description = _weather_code_to_text(code)

            output = (
                f"Weather for {name}, {country}:\n"
                f"  Temperature: {temp}C\n"
                f"  Wind speed: {wind} km/h\n"
                f"  Conditions: {description}\n"
            )

            logger.info("Weather fetched", location=name, temp=temp)
            return ToolResult(output=output, exit_code=0)


# Import needed for type hints in _execute
from typing import Any


def _weather_code_to_text(code: int) -> str:
    """Convert WMO weather code to human-readable text."""
    codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        95: "Thunderstorm",
    }
    return codes.get(code, f"Unknown (code {code})")
```

### Register in Config

In your creature's `config.yaml`:

```yaml
name: my_agent
version: "1.0"

controller:
  llm: gpt-5.4
  temperature: 0.5
  tool_format: bracket

tools:
  # Built-in tools
  - name: bash
    type: builtin
  - name: read
    type: builtin

  # Custom weather tool
  - name: weather
    type: custom
    module: ./custom/tools/weather.py
    class: WeatherTool

input:
  type: cli

output:
  type: stdout
```

The key fields for custom tools are:
- **`type: custom`** -- tells the loader to look for a Python file
- **`module`** -- path relative to the creature folder
- **`class`** -- the class name to instantiate

### Context-Aware Tools

If your tool needs access to agent state (working directory, session, scratchpad), set `needs_context = True`:

```python
class MyContextTool(BaseTool):
    needs_context = True

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")  # ToolContext instance
        if context is None:
            return ToolResult(error="Context required but not provided")

        # Access working directory
        cwd = context.working_dir

        # Access scratchpad
        scratchpad = context.scratchpad

        # Access session channels (for terrarium communication)
        channels = context.channels

        return ToolResult(output=f"Working in {cwd}", exit_code=0)
```

### Tool with Constructor Options

You can pass constructor options from the config:

```yaml
tools:
  - name: weather
    type: custom
    module: ./custom/tools/weather.py
    class: WeatherTool
    units: metric       # These become constructor kwargs
    cache_ttl: 300
```

Then accept them in `__init__`:

```python
class WeatherTool(BaseTool):
    def __init__(self, units: str = "metric", cache_ttl: int = 0, **kwargs):
        super().__init__()
        self.units = units
        self.cache_ttl = cache_ttl
```

Any keys in the config that aren't reserved (`name`, `type`, `module`, `class`, `doc`) get passed as constructor options.

### Testing Your Tool

Use the built-in test infrastructure:

```python
"""tests/test_weather_tool.py"""

import pytest

from my_creature.custom.tools.weather import WeatherTool
from kohakuterrarium.modules.tool.base import ToolResult


@pytest.mark.asyncio
async def test_weather_missing_location():
    tool = WeatherTool()
    result = await tool.execute({})
    assert not result.success
    assert "No location" in result.error


@pytest.mark.asyncio
async def test_weather_valid_location():
    tool = WeatherTool()
    result = await tool.execute({"location": "Tokyo"})
    assert result.success
    assert "Temperature" in result.get_text_output()
```

For integration tests using the full agent pipeline, use `TestAgentBuilder`:

```python
from kohakuterrarium.testing.agent import TestAgentBuilder
from kohakuterrarium.testing.llm import ScriptedLLM

from my_creature.custom.tools.weather import WeatherTool


@pytest.mark.asyncio
async def test_agent_uses_weather_tool():
    builder = TestAgentBuilder()
    builder.with_llm_script([
        '[/weather]location="Tokyo"[weather/]',
        "The weather in Tokyo is looking nice!",
    ])
    builder.with_tool(WeatherTool())

    env = builder.build()
    await env.inject("What's the weather in Tokyo?")

    assert env.output.has_output
    assert env.llm.call_count == 2  # Tool call + final response
```

---

## Custom Input Modules

Input modules control where user messages come from. The built-in `CLIInput` reads from the terminal; you might want input from Slack, a web API, or a voice pipeline.

### What You'll Build

A **Slack input module** that polls a Slack channel for new messages using the Slack Web API.

### The BaseInputModule Protocol

Input modules extend `BaseInputModule` and implement:

| Member | Type | Purpose |
|---|---|---|
| `_on_start()` | method | Initialize resources (connections, sessions) |
| `_on_stop()` | method | Clean up resources |
| `get_input()` | method | Async method that waits for and returns the next `TriggerEvent`, or `None` to signal exit |

The base class provides:
- `start()` / `stop()` lifecycle with `_running` state tracking
- Slash command dispatch via `try_user_command(text)`
- Interactive data rendering via `render_command_data()`

### TriggerEvent for Input

Input modules return `TriggerEvent` objects:

```python
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event

# Simple text input
event = create_user_input_event("Hello, agent!", source="slack")

# Or construct directly for more control
event = TriggerEvent(
    type="user_input",
    content="Hello, agent!",
    context={"source": "slack", "channel": "#general", "user": "alice"},
)
```

### Step-by-Step: Slack Input Module

Create `my_creature/custom/inputs/slack_input.py`:

```python
"""
Slack input module -- receive messages from a Slack channel.
"""

import asyncio
import os

import httpx

from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

SLACK_API = "https://slack.com/api"


class SlackInput(BaseInputModule):
    """
    Input module that receives messages from a Slack channel.

    Polls the Slack conversations.history API for new messages.
    """

    def __init__(
        self,
        channel_id: str = "",
        poll_interval: float = 2.0,
        bot_user_id: str = "",
        prompt: str = "> ",
    ):
        """
        Args:
            channel_id: Slack channel ID to monitor.
            poll_interval: Seconds between API polls.
            bot_user_id: Bot's own user ID (to ignore its own messages).
            prompt: Unused but accepted for interface compatibility.
        """
        super().__init__()
        self.channel_id = channel_id
        self.poll_interval = poll_interval
        self.bot_user_id = bot_user_id
        self._token = os.environ.get("SLACK_BOT_TOKEN", "")
        self._last_ts: str = ""  # Timestamp of last processed message
        self._client: httpx.AsyncClient | None = None

    async def _on_start(self) -> None:
        """Initialize the HTTP client and get latest timestamp."""
        if not self._token:
            logger.error("SLACK_BOT_TOKEN not set")
            return
        if not self.channel_id:
            logger.error("No channel_id configured")
            return

        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10.0,
        )

        # Get the latest message timestamp so we don't replay history
        resp = await self._client.get(
            f"{SLACK_API}/conversations.history",
            params={"channel": self.channel_id, "limit": 1},
        )
        data = resp.json()
        messages = data.get("messages", [])
        if messages:
            self._last_ts = messages[0].get("ts", "")

        logger.info(
            "Slack input started",
            channel=self.channel_id,
            last_ts=self._last_ts,
        )

    async def _on_stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Slack input stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Poll Slack for new messages."""
        if not self._running or not self._client:
            return None

        while self._running:
            try:
                params = {
                    "channel": self.channel_id,
                    "limit": 10,
                }
                if self._last_ts:
                    params["oldest"] = self._last_ts

                resp = await self._client.get(
                    f"{SLACK_API}/conversations.history",
                    params=params,
                )
                data = resp.json()

                if not data.get("ok"):
                    logger.warning("Slack API error", error=data.get("error"))
                    await asyncio.sleep(self.poll_interval)
                    continue

                messages = data.get("messages", [])
                # Messages come newest-first; reverse for chronological order
                messages.reverse()

                for msg in messages:
                    ts = msg.get("ts", "")
                    user = msg.get("user", "")
                    text = msg.get("text", "")

                    # Skip bot's own messages
                    if user == self.bot_user_id:
                        continue

                    # Skip messages we've already seen
                    if ts <= self._last_ts:
                        continue

                    self._last_ts = ts

                    logger.debug("Slack message received", user=user, text=text[:50])

                    return create_user_input_event(
                        text,
                        source="slack",
                        slack_user=user,
                        slack_channel=self.channel_id,
                        slack_ts=ts,
                    )

            except Exception as e:
                logger.error("Slack poll error", error=str(e))

            # No new messages, wait and poll again
            await asyncio.sleep(self.poll_interval)

        return None
```

### Register in Config

```yaml
name: slack_bot
version: "1.0"

controller:
  llm: gpt-5.4
  temperature: 0.7

input:
  type: custom
  module: ./custom/inputs/slack_input.py
  class: SlackInput
  channel_id: C0123456789
  poll_interval: 2.0
  bot_user_id: U0123456789

tools:
  - name: bash
    type: builtin
  - name: think
    type: builtin

output:
  type: stdout
```

Note how `channel_id`, `poll_interval`, and `bot_user_id` are passed directly in the input config block. Any key not in the reserved set (`type`, `module`, `class`, `prompt`) is forwarded as a constructor kwarg.

### Testing Your Input Module

```python
"""tests/test_slack_input.py"""

import pytest

from my_creature.custom.inputs.slack_input import SlackInput


@pytest.mark.asyncio
async def test_slack_input_no_token(monkeypatch):
    """Should handle missing SLACK_BOT_TOKEN gracefully."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    module = SlackInput(channel_id="C123")
    await module.start()
    # Module starts but won't have a client
    assert module.is_running
    await module.stop()
```

---

## Custom Output Modules

Output modules control where agent responses are delivered. The built-in `StdoutOutput` prints to the terminal; you might want to post to Discord, send an email, or stream to a TTS engine.

### What You'll Build

A **Discord webhook output** module that posts agent responses to a Discord channel via a webhook URL.

### The BaseOutputModule Protocol

Output modules extend `BaseOutputModule` and implement:

| Member | Type | Purpose |
|---|---|---|
| `write(content)` | method | **Required.** Write a complete message. |
| `write_stream(chunk)` | method | Write a streaming chunk. Default calls `write()`. |
| `flush()` | method | Flush buffered content. Default is no-op. |
| `on_processing_start()` | method | Called when agent starts thinking. |
| `on_processing_end()` | method | Called when agent finishes a turn. |
| `on_activity(type, detail)` | method | Tool/sub-agent activity notifications. |
| `on_user_input(text)` | method | Called when user input is received. |
| `on_resume(events)` | method | Called during session resume with history. |
| `_on_start()` | method | Initialize resources. |
| `_on_stop()` | method | Clean up resources. |

At minimum, you only need to implement `write()`. Everything else has sensible defaults.

### Step-by-Step: Discord Webhook Output

Create `my_creature/custom/outputs/discord_webhook.py`:

```python
"""
Discord webhook output module -- post agent responses to Discord.
"""

import httpx

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Discord messages have a 2000 character limit
DISCORD_MAX_LENGTH = 2000


class DiscordWebhookOutput(BaseOutputModule):
    """
    Output module that sends messages to a Discord channel via webhook.

    Buffers streaming chunks and sends the complete message on flush.
    """

    def __init__(
        self,
        webhook_url: str = "",
        username: str = "KohakuTerrarium",
        avatar_url: str = "",
    ):
        """
        Args:
            webhook_url: Discord webhook URL.
            username: Display name for the webhook messages.
            avatar_url: Avatar URL for the webhook messages.
        """
        super().__init__()
        self.webhook_url = webhook_url
        self.username = username
        self.avatar_url = avatar_url
        self._buffer: list[str] = []
        self._client: httpx.AsyncClient | None = None

    async def _on_start(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("Discord webhook output started")

    async def _on_stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Discord webhook output stopped")

    async def write(self, content: str) -> None:
        """Send a complete message to Discord."""
        if not content.strip():
            return
        await self._send_to_discord(content)

    async def write_stream(self, chunk: str) -> None:
        """Buffer streaming chunks until flush."""
        if chunk:
            self._buffer.append(chunk)

    async def flush(self) -> None:
        """Send all buffered content as a single Discord message."""
        if not self._buffer:
            return

        full_text = "".join(self._buffer)
        self._buffer.clear()

        if full_text.strip():
            await self._send_to_discord(full_text)

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Log tool activity (not sent to Discord)."""
        logger.debug(
            "Activity",
            activity_type=activity_type,
            detail=detail[:100],
        )

    async def on_processing_start(self) -> None:
        """Optionally show a typing indicator."""
        # Discord webhooks don't support typing indicators,
        # but a real implementation could use the bot API here.
        pass

    async def _send_to_discord(self, text: str) -> None:
        """Send text to the Discord webhook, splitting if needed."""
        if not self.webhook_url or not self._client:
            logger.warning("Discord webhook not configured or client not ready")
            return

        # Split long messages at Discord's limit
        chunks = _split_message(text, DISCORD_MAX_LENGTH)

        for chunk in chunks:
            payload: dict = {
                "content": chunk,
                "username": self.username,
            }
            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url

            try:
                resp = await self._client.post(self.webhook_url, json=payload)
                if resp.status_code == 429:
                    # Rate limited -- log and skip
                    logger.warning("Discord rate limited")
                elif resp.status_code >= 400:
                    logger.warning(
                        "Discord webhook error",
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
            except Exception as e:
                logger.error("Discord send failed", error=str(e))


def _split_message(text: str, max_length: int) -> list[str]:
    """Split a message into chunks that fit Discord's limit."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Try to split at a newline
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            # No newline found, split at max length
            split_at = max_length

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks
```

### Register in Config

As the **default output** (all agent output goes to Discord):

```yaml
output:
  type: custom
  module: ./custom/outputs/discord_webhook.py
  class: DiscordWebhookOutput
  webhook_url: "https://discord.com/api/webhooks/1234567890/abcdef"
  username: "My Agent"
```

As a **named output** (the agent explicitly routes specific content there):

```yaml
output:
  type: stdout              # Default output goes to terminal
  controller_direct: true

  named_outputs:
    discord:
      type: custom
      module: ./custom/outputs/discord_webhook.py
      class: DiscordWebhookOutput
      webhook_url: "https://discord.com/api/webhooks/1234567890/abcdef"
      username: "My Agent"
```

With named outputs, the agent's system prompt will include the output name, and the controller can direct content to it explicitly using the output routing syntax.

### Testing Your Output Module

```python
"""tests/test_discord_output.py"""

import pytest

from my_creature.custom.outputs.discord_webhook import DiscordWebhookOutput


@pytest.mark.asyncio
async def test_write_buffers_and_flushes():
    output = DiscordWebhookOutput(webhook_url="")
    await output.start()

    # write_stream buffers
    await output.write_stream("Hello ")
    await output.write_stream("world!")
    assert output._buffer == ["Hello ", "world!"]

    # flush clears buffer (won't actually send with empty URL)
    await output.flush()
    assert output._buffer == []

    await output.stop()


@pytest.mark.asyncio
async def test_split_message():
    from my_creature.custom.outputs.discord_webhook import _split_message

    text = "A" * 5000
    chunks = _split_message(text, 2000)
    assert len(chunks) == 3
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text
```

---

## Custom Triggers

Triggers make agents autonomous. Instead of waiting for user input, a trigger fires a `TriggerEvent` that wakes the controller. The built-in `TimerTrigger` fires on an interval; you can build triggers that watch files, poll APIs, or listen to message queues.

### What You'll Build

A **file watcher trigger** that fires whenever files in a directory change.

### The BaseTrigger Class

Custom triggers extend `BaseTrigger` and implement:

| Member | Type | Purpose |
|---|---|---|
| `wait_for_trigger()` | method | **Required.** Async method that blocks until the trigger fires, returning a `TriggerEvent` or `None` to stop. |
| `_on_start()` | method | Initialize resources. |
| `_on_stop()` | method | Clean up resources. |
| `_on_context_update(ctx)` | method | React to context changes from the controller. |
| `resumable` | class var | Set `True` to persist trigger across session resume. |
| `universal` | class var | Set `True` to allow creation via the `create_trigger` tool at runtime. |

The base class provides:
- `start()` / `stop()` lifecycle management
- `_create_event(type, content, context)` helper to build `TriggerEvent` objects
- `prompt` attribute for a default message included in events
- `set_context()` for receiving controller state updates

### Step-by-Step: File Watcher Trigger

Create `my_creature/custom/triggers/file_watcher.py`:

```python
"""
File watcher trigger -- fires when files in a directory change.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from kohakuterrarium.core.events import TriggerEvent
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class FileWatcherTrigger(BaseTrigger):
    """
    Trigger that fires when files in a watched directory change.

    Polls the directory for modification time changes. For production
    use, consider using watchdog or inotify for efficiency.
    """

    resumable = True
    universal = False  # Not suitable for runtime creation

    def __init__(
        self,
        watch_path: str = ".",
        patterns: list[str] | None = None,
        poll_interval: float = 5.0,
        prompt: str | None = None,
        **options: Any,
    ):
        """
        Args:
            watch_path: Directory to watch (absolute or relative to cwd).
            patterns: File patterns to watch (e.g. ["*.py", "*.yaml"]).
                     None means watch all files.
            poll_interval: Seconds between polls.
            prompt: Message to include in trigger events.
        """
        super().__init__(prompt=prompt, **options)
        self.watch_path = Path(watch_path).resolve()
        self.patterns = patterns or ["*"]
        self.poll_interval = poll_interval
        self._snapshot: dict[str, float] = {}  # path -> mtime
        self._stop_event: asyncio.Event | None = None

    def to_resume_dict(self) -> dict[str, Any]:
        """Serialize for session persistence."""
        return {
            "watch_path": str(self.watch_path),
            "patterns": self.patterns,
            "poll_interval": self.poll_interval,
            "prompt": self.prompt,
        }

    @classmethod
    def from_resume_dict(cls, data: dict[str, Any]) -> "FileWatcherTrigger":
        return cls(**data)

    async def _on_start(self) -> None:
        """Take initial snapshot of file states."""
        self._stop_event = asyncio.Event()
        self._snapshot = self._scan_directory()
        logger.info(
            "File watcher started",
            watch_path=str(self.watch_path),
            patterns=self.patterns,
            files_tracked=len(self._snapshot),
        )

    async def _on_stop(self) -> None:
        """Signal the stop event."""
        if self._stop_event:
            self._stop_event.set()
        logger.info("File watcher stopped")

    async def wait_for_trigger(self) -> TriggerEvent | None:
        """Poll for file changes."""
        if not self._running or not self._stop_event:
            return None

        while self._running:
            # Wait for poll interval or stop signal
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval,
                )
                # Stop event was set
                return None
            except asyncio.TimeoutError:
                pass  # Interval elapsed, check for changes

            if not self._running:
                return None

            # Scan for changes
            current = self._scan_directory()
            changes = self._diff_snapshots(self._snapshot, current)

            if changes:
                self._snapshot = current

                change_summary = []
                for change_type, paths in changes.items():
                    for p in paths:
                        change_summary.append(f"  {change_type}: {p}")

                content = (
                    self.prompt
                    or f"Files changed in {self.watch_path}:\n"
                    + "\n".join(change_summary)
                )

                logger.info(
                    "File changes detected",
                    change_count=sum(len(v) for v in changes.values()),
                )

                return self._create_event(
                    event_type="file_change",
                    content=content,
                    context={
                        "trigger": "file_watcher",
                        "watch_path": str(self.watch_path),
                        "changes": {k: list(v) for k, v in changes.items()},
                    },
                )

            # No changes, continue polling
            self._snapshot = current

        return None

    def _scan_directory(self) -> dict[str, float]:
        """Scan the watched directory and return {path: mtime} map."""
        result: dict[str, float] = {}

        if not self.watch_path.is_dir():
            return result

        for pattern in self.patterns:
            for path in self.watch_path.rglob(pattern):
                if path.is_file():
                    try:
                        result[str(path)] = os.path.getmtime(path)
                    except OSError:
                        pass  # File may have been deleted mid-scan

        return result

    def _diff_snapshots(
        self,
        old: dict[str, float],
        new: dict[str, float],
    ) -> dict[str, list[str]]:
        """Compare two snapshots and return changes by type."""
        changes: dict[str, list[str]] = {
            "added": [],
            "modified": [],
            "deleted": [],
        }

        for path, mtime in new.items():
            if path not in old:
                changes["added"].append(path)
            elif old[path] != mtime:
                changes["modified"].append(path)

        for path in old:
            if path not in new:
                changes["deleted"].append(path)

        # Remove empty categories
        return {k: v for k, v in changes.items() if v}
```

### Register in Config

```yaml
name: watcher_agent
version: "1.0"

controller:
  llm: gpt-5.4
  temperature: 0.3

system_prompt: >
  You are a code review agent. When files change, review the changes
  and provide feedback.

# No user input -- purely trigger-driven
input:
  type: none

triggers:
  # Built-in timer for periodic status
  - type: timer
    prompt: "Check overall project health."
    interval: 300
    immediate: true

  # Custom file watcher
  - type: custom
    module: ./custom/triggers/file_watcher.py
    class: FileWatcherTrigger
    prompt: "Files have changed. Review the modifications."
    watch_path: /path/to/project/src
    patterns: ["*.py", "*.yaml"]
    poll_interval: 5.0

tools:
  - name: bash
    type: builtin
  - name: read
    type: builtin
  - name: think
    type: builtin

output:
  type: stdout
```

For custom triggers, options work the same way as tools: any key not in the reserved set (`type`, `module`, `class`, `prompt`) is forwarded to the constructor.

### Testing Your Trigger

```python
"""tests/test_file_watcher.py"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from my_creature.custom.triggers.file_watcher import FileWatcherTrigger


@pytest.mark.asyncio
async def test_detects_new_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        trigger = FileWatcherTrigger(
            watch_path=tmpdir,
            poll_interval=0.5,
            prompt="Files changed",
        )
        await trigger.start()

        # Create a file after the initial snapshot
        Path(tmpdir, "test.py").write_text("print('hello')")

        # Should fire on next poll
        event = await asyncio.wait_for(
            trigger.wait_for_trigger(),
            timeout=3.0,
        )
        assert event is not None
        assert event.type == "file_change"
        assert "added" in event.context.get("changes", {})

        await trigger.stop()


@pytest.mark.asyncio
async def test_no_false_triggers():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create file before starting
        Path(tmpdir, "existing.py").write_text("pass")

        trigger = FileWatcherTrigger(
            watch_path=tmpdir,
            poll_interval=0.2,
        )
        await trigger.start()

        # No changes -- should time out
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                trigger.wait_for_trigger(),
                timeout=1.0,
            )

        await trigger.stop()
```

---

## Custom Sub-Agents

Sub-agents are nested agents that the controller can delegate tasks to. They have their own LLM conversation, tool access, and system prompt. The controller dispatches work to them and receives their output.

### What You'll Build

A **code reviewer sub-agent** that reviews code changes with specific guidelines and returns structured feedback.

### SubAgentConfig

Sub-agents are defined by `SubAgentConfig` dataclass:

```python
@dataclass
class SubAgentConfig:
    name: str                    # Unique identifier
    description: str = ""       # One-line description for controller
    tools: list[str] = []       # Allowed tool names
    system_prompt: str = ""     # Full system prompt (overrides prompt_file)
    prompt_file: str | None     # Path to prompt file (relative to agent folder)
    extra_prompt: str = ""      # Appended to base prompt
    can_modify: bool = False    # Can modify files?
    stateless: bool = True      # Reset state between calls?
    interactive: bool = False   # Receives ongoing context updates?
    output_to: OutputTarget     # CONTROLLER (default) or EXTERNAL
    max_turns: int = 0          # 0 = unlimited
    timeout: float = 0          # 0 = no timeout
    model: str | None = None    # None = inherit parent's model
    temperature: float | None   # None = inherit parent's temperature
```

### Two Ways to Define Sub-Agents

**1. Inline in config.yaml** -- simpler, everything in one place:

```yaml
subagents:
  - name: code_reviewer
    type: custom
    description: "Reviews code for quality, bugs, and style"
    tools: [read, bash, think]
    can_modify: false
    system_prompt: "You are a code reviewer..."
    max_turns: 5
```

**2. Python module with exported config** -- more flexible, reusable:

```yaml
subagents:
  - name: code_reviewer
    type: custom
    module: ./custom/subagents/code_reviewer.py
    config: CODE_REVIEWER_CONFIG
```

### Step-by-Step: Code Reviewer Sub-Agent

For the Python module approach, create `my_creature/custom/subagents/code_reviewer.py`:

```python
"""
Code reviewer sub-agent configuration.

Exports a SubAgentConfig that defines a specialized code review agent
with structured output guidelines.
"""

from kohakuterrarium.modules.subagent.config import (
    OutputTarget,
    SubAgentConfig,
)

CODE_REVIEWER_CONFIG = SubAgentConfig(
    name="code_reviewer",
    description="Reviews code changes for bugs, style issues, and improvements",
    tools=["read", "bash", "think"],
    system_prompt="""\
You are a meticulous code reviewer. When asked to review code, follow this process:

1. **Read the files** using the read tool to understand the changes.
2. **Analyze** for:
   - Bugs and logic errors
   - Security vulnerabilities
   - Performance issues
   - Style and readability
   - Missing error handling
   - Test coverage gaps
3. **Provide structured feedback** in this format:

## Review Summary
[1-2 sentence overview]

## Issues Found

### Critical
- [issue]: [file:line] - [description]

### Suggestions
- [suggestion]: [file:line] - [description]

### Positive
- [what's done well]

## Verdict
[APPROVE / REQUEST_CHANGES / NEEDS_DISCUSSION]

Be specific. Reference exact file paths and line numbers. Explain *why*
something is an issue, not just *that* it is.
""",
    can_modify=False,     # Read-only -- reviewer shouldn't change code
    stateless=True,       # Fresh context for each review
    max_turns=10,         # Allow enough turns to read multiple files
    timeout=120,          # 2 minute max per review
    temperature=0.3,      # Low temperature for consistent reviews
)
```

### Register in Config

Using the Python module approach:

```yaml
name: my_swe_agent
version: "1.0"

controller:
  model: openai/gpt-4o
  temperature: 0.5

tools:
  - name: bash
    type: builtin
  - name: read
    type: builtin
  - name: write
    type: builtin
  - name: think
    type: builtin

subagents:
  # From Python module
  - name: code_reviewer
    type: custom
    module: ./custom/subagents/code_reviewer.py
    config: CODE_REVIEWER_CONFIG

  # Inline definition (simpler alternative)
  - name: summarize
    type: custom
    description: "Summarizes long text or conversation context"
    tools: [think]
    system_prompt: >
      You are a summarization agent. Produce concise, accurate summaries
      that capture the key points and decisions.
    max_turns: 3
    can_modify: false

output:
  type: stdout
```

### Sub-Agent Output Routing

By default, sub-agent output goes back to the parent controller (`OutputTarget.CONTROLLER`). If you want the sub-agent to stream directly to the user:

```python
CODE_REVIEWER_CONFIG = SubAgentConfig(
    name="code_reviewer",
    description="Reviews code changes",
    output_to=OutputTarget.EXTERNAL,  # Stream to user output
    # ...
)
```

Or in inline YAML:

```yaml
subagents:
  - name: code_reviewer
    type: custom
    description: "Reviews code changes"
    output_to: external
    tools: [read, bash, think]
```

### Interactive Sub-Agents

For long-running sub-agents that receive ongoing context:

```python
MONITOR_CONFIG = SubAgentConfig(
    name="monitor",
    description="Monitors system metrics in real-time",
    interactive=True,             # Stays alive between calls
    stateless=False,              # Keeps conversation history
    context_mode=ContextUpdateMode.QUEUE_APPEND,  # Queue new info
    tools=["bash", "read"],
)
```

### Testing Sub-Agents

```python
"""tests/test_code_reviewer.py"""

import pytest

from my_creature.custom.subagents.code_reviewer import CODE_REVIEWER_CONFIG


def test_config_properties():
    config = CODE_REVIEWER_CONFIG
    assert config.name == "code_reviewer"
    assert not config.can_modify
    assert "read" in config.tools
    assert config.max_turns == 10
    assert config.timeout == 120


def test_prompt_contains_review_structure():
    prompt = CODE_REVIEWER_CONFIG.load_prompt()
    assert "## Review Summary" in prompt
    assert "APPROVE" in prompt
    assert "REQUEST_CHANGES" in prompt
```

---

## Packaging and Distribution

Once you've built useful custom modules, you can package them for reuse across projects or share them with others.

### Package Structure

A KohakuTerrarium package is a directory with a `kohaku.yaml` manifest:

```
my-kt-package/
  kohaku.yaml           # Package manifest (required)
  creatures/            # Creature configs
    my_agent/
      config.yaml
      system.md
      custom/
        tools/
          weather.py
  terrariums/           # Terrarium configs (optional)
  requirements.txt      # Python dependencies (optional)
```

### The kohaku.yaml Manifest

```yaml
name: my-kt-tools
version: "1.0.0"
description: "Custom tools and creatures for KohakuTerrarium"

# Python packages required by your modules
python_dependencies:
  - httpx>=0.25
  - pydantic>=2.0

# Creature configs included in this package
creatures:
  - name: weather_agent
    path: creatures/weather_agent
    description: "Agent with weather lookup capability"

# Terrarium configs included in this package
terrariums:
  - name: weather_team
    path: terrariums/weather_team
    description: "Multi-agent weather analysis team"

# Tools that can be discovered by the framework
tools:
  - name: weather
    module: creatures/weather_agent/custom/tools/weather.py
    class: WeatherTool
    description: "Fetch current weather conditions"

# Plugins (loaded during agent init)
plugins: []

# LLM profile presets
llm_presets: []
```

### Installing Packages

```bash
# From a local directory
kt install /path/to/my-kt-package

# From a local directory (editable -- changes are live)
kt install /path/to/my-kt-package -e

# From a git repository
kt install https://github.com/user/my-kt-package.git
```

Packages are installed to `~/.kohakuterrarium/packages/<name>/`.

Editable installs create a `.link` pointer file instead of copying, so changes to the source directory take effect immediately.

### Referencing Packages in Configs

Use `@package-name/path` syntax:

```yaml
# Inherit from a packaged creature
base_config: "@my-kt-tools/creatures/weather_agent"

# Or reference specific files
tools:
  - name: weather
    type: package
    module: my_kt_tools.tools.weather
    class: WeatherTool
```

### The `type: package` Loading Path

When `type: package` is used in config, the `ModuleLoader` uses Python's standard import system (`importlib.import_module`) instead of file-based loading. This means the package must be importable:

```yaml
tools:
  - name: weather
    type: package
    module: my_kt_tools.tools.weather    # Python import path
    class: WeatherTool                    # Class name
```

For this to work, `my_kt_tools` must be an installed Python package or on `sys.path`. The `kt install` command handles adding the package root to `sys.path` automatically.

### Publishing as a pip Package

For maximum portability, you can also distribute modules as standard Python packages:

```
my-kt-tools/
  pyproject.toml
  src/
    my_kt_tools/
      __init__.py
      tools/
        __init__.py
        weather.py
      creatures/
        weather_agent/
          config.yaml
          system.md
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "my-kt-tools"
version = "1.0.0"
dependencies = [
    "kohakuterrarium",
    "httpx>=0.25",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]
```

After `pip install my-kt-tools`, users can reference your modules with `type: package`:

```yaml
tools:
  - name: weather
    type: package
    module: my_kt_tools.tools.weather
    class: WeatherTool
```

### Listing and Managing Packages

```bash
# List all installed packages
kt list

# Uninstall a package
kt uninstall my-kt-tools
```

---

## Quick Reference

### Config Type Cheat Sheet

| Module | Config key | `type` values | Required fields |
|---|---|---|---|
| Tool | `tools[]` | `builtin`, `custom`, `package` | `name`, `module`, `class` |
| Input | `input` | builtin name, `custom`, `package` | `module`, `class` |
| Output | `output` | builtin name, `custom`, `package` | `module`, `class` |
| Trigger | `triggers[]` | builtin name, `custom`, `package` | `module`, `class` |
| Sub-Agent | `subagents[]` | `builtin`, `custom`, `package` | `name`, `module`, `config` or inline fields |

### Module Loading Resolution

1. **`type: builtin`** -- Framework looks up the name in its internal registry.
2. **`type: custom`** -- `ModuleLoader` loads a Python file relative to the creature folder. The `module` field is a file path like `./custom/tools/weather.py`.
3. **`type: package`** -- `ModuleLoader` uses `importlib.import_module` with a dotted Python path like `my_package.tools.weather`.

### Base Class Import Paths

```python
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult, ToolConfig, ExecutionMode, ToolContext
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.modules.subagent.config import SubAgentConfig, OutputTarget, ContextUpdateMode
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event, EventType
from kohakuterrarium.utils.logging import get_logger
```
