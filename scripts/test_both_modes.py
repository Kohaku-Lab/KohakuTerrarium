"""Test both native and bracket tool calling through the controller pipeline.

Verifies that the Controller correctly handles:
1. Native mode: API tool calls → ToolCallEvent
2. Bracket mode: Text parsing → ToolCallEvent

Usage:
    python scripts/test_both_modes.py
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.openai import OpenAIProvider
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.parsing import TextEvent, ToolCallEvent


class DummyTool(BaseTool):
    """Provide deterministic weather output for live parser-mode checks."""

    @property
    def tool_name(self) -> str:
        return "get_weather"

    @property
    def description(self) -> str:
        return "Get current weather for a city"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, context=None):
        city = args.get("city", args.get("content", "unknown"))
        return ToolResult(output=f"Weather in {city}: 22C, sunny", exit_code=0)

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        }


async def test_mode(mode: str) -> None:
    """Run one live controller turn and report parsed events for a tool mode."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")

    print(f"\n{'='*50}")
    print(f"Testing: {mode} mode with {model}")
    print(f"{'='*50}\n")

    provider = OpenAIProvider(
        model=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    registry = Registry()
    registry.register_tool(DummyTool())

    config = ControllerConfig(
        system_prompt="You are a helpful assistant. Use the get_weather tool when asked about weather.",
        tool_format=mode,
    )

    controller = Controller(provider, config, registry=registry)

    event = create_user_input_event("What's the weather in Tokyo?")
    await controller.push_event(event)

    text_parts = []
    tool_calls = []

    async for parse_event in controller.run_once():
        if isinstance(parse_event, TextEvent):
            text_parts.append(parse_event.text)
            print(f"  [TEXT] {parse_event.text}", end="")
        elif isinstance(parse_event, ToolCallEvent):
            tool_calls.append(parse_event)
            print(f"\n  [TOOL] {parse_event.name}({parse_event.args})")

    print()
    text = "".join(text_parts)
    print(f"\nResults:")
    print(f"  Text chunks: {len(text_parts)}, total {len(text)} chars")
    print(f"  Tool calls: {len(tool_calls)}")
    for tc in tool_calls:
        print(f"    - {tc.name}({tc.args})")

    if tool_calls:
        print(f"\n  SUCCESS: {mode} mode produced tool calls!")
    else:
        print(f"\n  WARNING: No tool calls detected in {mode} mode")
        if text:
            print(f"  Model output text instead: {text[:200]}")

    await provider.close()


async def main() -> None:
    """Require OpenRouter credentials and exercise native and bracket modes."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: Set OPENROUTER_API_KEY in .env")
        return

    await test_mode("native")
    await test_mode("bracket")


if __name__ == "__main__":
    asyncio.run(main())
