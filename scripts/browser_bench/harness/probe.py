"""Measure the provider input tokens of every fixed prompt part, by difference.

Builds the benchmark creature (scripted model, no browser launched), takes
its system prompt and the tool schemas the model sees, then sends one tiny
request per variant to the real model: everything, no tools, each system
section left out, each tool left out. The differences are the tokens each
part costs on every call. Optionally measures a sample text's tokens.

    python -m scripts.browser_bench.harness.probe --out FILE [--sample TEXT_FILE]
"""

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from kohakuterrarium import Agent
from kohakuterrarium.bootstrap.llm import coerce_llm_provider
from kohakuterrarium.testing.llm import ScriptedLLM

from .cli import model_profile
from .runner import BenchHooks, _quiet_skills, agent_config
from .usage import section_key, schema_chars

QUESTION = "Reply with the single word OK."
MINIMAL = "You are a helpful assistant."


async def fixed_parts() -> tuple[str, list[Any]]:
    """The benchmark creature's system prompt and the tool schemas the model sees."""
    workdir = Path(tempfile.mkdtemp(prefix="bench-probe-"))
    hooks = BenchHooks(1)
    options = {"headless": True, "allow_loopback": True, "allow_hosts": []}
    agent = await Agent.build(
        agent_config(workdir, options),
        llm=ScriptedLLM(["x"]),
        pwd=str(workdir),
        plugins=[hooks],
    )
    await agent.start()
    try:
        _quiet_skills(agent)
        system = agent.get_system_prompt()
        visible = hooks.get_tool_visibility(None).allowed_tools
        tools = [
            t for t in agent.controller._get_native_tool_schemas() if t.name in visible
        ]
    finally:
        await agent.stop()
    return system, tools


def sections(system: str) -> list[tuple[str, str]]:
    """``(heading or "base", text)`` per ``## `` section, in order."""
    out, key, buf = [], "base", []
    for line in system.splitlines(keepends=True):
        if line.startswith("## "):
            out.append((key, "".join(buf)))
            key, buf = line.strip()[3:], []
        buf.append(line)
    out.append((key, "".join(buf)))
    return [(k, t) for k, t in out if t]


def components(usage: Mapping[str, Mapping[str, int]]) -> dict[str, int]:
    """Tokens per fixed component from the variant usages."""
    full = usage["full"]["prompt_tokens"]
    no_tools = usage["no_tools"]["prompt_tokens"]
    out = {}
    for key, value in usage.items():
        kind, _, name = key.partition(":")
        if kind == "without_section":
            label = "base" if name == "base" else section_key(name)
            out[f"system.{label}"] = no_tools - value["prompt_tokens"]
        elif kind == "without_tool":
            out[f"tools.{name}"] = full - value["prompt_tokens"]
    return out


async def measure(provider: Any, system: str, tools: list[Any], user: str) -> dict:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    async for _ in provider.chat(messages, tools=tools or None):
        pass
    return dict(provider._last_usage)


async def run_probe(model: str, sample: str | None) -> dict[str, Any]:
    system, tools = await fixed_parts()
    provider = coerce_llm_provider(model_profile(model, "low", websocket=False), None)
    usage = {
        "full": await measure(provider, system, tools, QUESTION),
        "no_tools": await measure(provider, system, [], QUESTION),
        "minimal": await measure(provider, MINIMAL, [], QUESTION),
    }
    parts = sections(system)
    for name, _ in parts:
        rest = "".join(t for n, t in parts if n != name) or MINIMAL
        usage[f"without_section:{name}"] = await measure(provider, rest, [], QUESTION)
    for tool in tools:
        rest = [t for t in tools if t.name != tool.name]
        usage[f"without_tool:{tool.name}"] = await measure(
            provider, system, rest, QUESTION
        )
    result = {
        "model": model,
        "system_chars": {
            ("base" if n == "base" else section_key(n)): len(t) for n, t in parts
        },
        "tool_chars": dict(schema_chars(t) for t in tools),
        "usage": usage,
        "components": components(usage),
    }
    if sample:
        text = Path(sample).read_text(encoding="utf-8")
        measured = await measure(provider, MINIMAL, [], QUESTION + "\n\n" + text)
        base = usage["minimal"]["prompt_tokens"]
        result["sample"] = {
            "chars": len(text),
            "tokens": measured["prompt_tokens"] - base,
            "tokens_per_char": (measured["prompt_tokens"] - base) / max(1, len(text)),
        }
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--sample", default=None)
    args = parser.parse_args(argv)
    result = asyncio.run(run_probe(args.model, args.sample))
    args.out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result["components"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
