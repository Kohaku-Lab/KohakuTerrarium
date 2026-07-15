"""Test native tool calling with a real LLM via OpenRouter.

Sends a simple request with tool schemas and checks if the model
returns structured tool_calls instead of text-formatted calls.

Usage:
    python scripts/test_native_tools.py
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from kohakuterrarium.llm.base import ToolSchema
from kohakuterrarium.llm.openai import OpenAIProvider


async def main() -> None:
    """Verify live structured tool calls and the no-tools compatibility path."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")

    if not api_key:
        print("ERROR: Set OPENROUTER_API_KEY in .env")
        return

    print(f"Model: {model}")
    print(f"API: OpenRouter")
    print()

    provider = OpenAIProvider(
        model=model,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    tools = [
        ToolSchema(
            name="get_weather",
            description="Get current weather for a city",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit",
                    },
                },
                "required": ["city"],
            },
        ),
        ToolSchema(
            name="search_web",
            description="Search the web for information",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        ),
    ]

    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant. Use tools when needed.",
        },
        {"role": "user", "content": "What's the weather like in Tokyo?"},
    ]

    print("=== Test 1: Native tool calling ===")
    print(f"Sending request with {len(tools)} tool schemas...")
    print()

    text_chunks = []
    async for chunk in provider.chat(messages, stream=True, tools=tools):
        text_chunks.append(chunk)
        print(chunk, end="", flush=True)

    print()
    print()

    text = "".join(text_chunks)
    native_calls = provider.last_tool_calls

    print(f"Text content: {len(text)} chars")
    print(f"Native tool calls: {len(native_calls)}")

    if native_calls:
        print()
        for i, call in enumerate(native_calls):
            print(f"  Tool call {i + 1}:")
            print(f"    ID: {call.id}")
            print(f"    Name: {call.name}")
            print(f"    Arguments (raw): {call.arguments}")
            print(f"    Arguments (parsed): {call.parsed_arguments()}")
        print()
        print("SUCCESS: Model returned native tool calls!")
    else:
        print()
        if text:
            print(f"Model returned text instead of tool calls: {text[:200]}")
        print("NOTE: Model did not use native tool calling.")
        print("This may mean the model doesn't support it via OpenRouter,")
        print("or the prompt didn't trigger tool use.")

    # The same provider must clear tool-call state when no schemas are supplied.
    print()
    print("=== Test 2: Plain request (no tools) ===")
    messages2 = [
        {"role": "user", "content": "Say hello in exactly 5 words."},
    ]

    text2 = []
    async for chunk in provider.chat(messages2, stream=True):
        text2.append(chunk)
        print(chunk, end="", flush=True)
    print()

    assert (
        provider.last_tool_calls == []
    ), "Should have no tool calls without tools param"
    print("SUCCESS: Plain request works as before.")

    await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
