"""
Smart Router — classify input, route to specialist agents.

Demonstrates:
  - ``>> dict`` routing: classifier produces key, dict maps to specialists
  - ``factory()`` ephemeral specialists: each handles one request
  - ``|`` fallback: if classifier fails, use a generalist
  - Combining routing with retry and fallback for resilience

Pattern: instead of one agent that does everything, use a classifier
to pick the right specialist. Each specialist has a focused prompt
and minimal tools — cheaper, faster, more accurate.

Usage:
    python smart_router.py "Fix the bug in auth.py"
    python smart_router.py "Write a blog post about AI"
    python smart_router.py "What's the weather like?"
"""

import asyncio
import sys

from kohakuterrarium.compose import Pure, factory
from kohakuterrarium.core.config import load_agent_config

# ── Configs ──────────────────────────────────────────────────────────


def make_classifier_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "classifier"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a task classifier. Given a user request, output EXACTLY "
        "one word indicating the category:\n\n"
        "- code: programming, debugging, code review\n"
        "- writing: blog posts, documentation, creative writing\n"
        "- research: factual questions, analysis, investigation\n"
        "- general: anything else\n\n"
        "Output ONLY the category word, nothing else."
    )
    return config


def make_specialist_config(role: str, description: str):
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = f"specialist-{role}"
    config.tools = (
        [
            {"name": "read", "type": "builtin"},
            {"name": "write", "type": "builtin"},
            {"name": "edit", "type": "builtin"},
            {"name": "bash", "type": "builtin"},
            {"name": "glob", "type": "builtin"},
            {"name": "grep", "type": "builtin"},
        ]
        if role == "code"
        else []
    )
    config.subagents = []
    config.system_prompt = f"You are a {description}. Help the user with their request."
    return config


# ── Build the router ─────────────────────────────────────────────────


def build_router():
    """Construct a classify → route → specialist pipeline."""
    classifier = factory(make_classifier_config())

    # Specialists for each category
    specialists = {
        "code": factory(make_specialist_config("code", "software engineer")),
        "writing": factory(make_specialist_config("writing", "professional writer")),
        "research": factory(make_specialist_config("research", "research analyst")),
        "_default": factory(make_specialist_config("general", "helpful assistant")),
    }

    # Classifier extracts the category, then we pair it with the original input
    # so the specialist sees the original request, not just the category name.
    #
    # Flow: input → (classify, echo) → (category, original_input) → route → specialist

    async def classify_and_pair(request: str) -> tuple[str, str]:
        """Classify the request and return (category, original_request)."""
        category = await classifier(request)
        return (category.strip().lower(), request)

    # The router pipeline:
    #   classify_and_pair produces ("code", "Fix the bug...")
    #   >> dict routes by key, passing the original request to the specialist
    router = Pure(classify_and_pair) >> specialists

    return router


# ── Main ─────────────────────────────────────────────────────────────


async def main(request: str) -> None:
    print(f"Request: {request}\n")

    # Build the router with fallback for resilience
    router = build_router()
    safe_router = (router * 2) | factory(
        make_specialist_config("general", "helpful assistant")
    )

    print("Classifying and routing...")
    result = await safe_router(request)
    print(f"\nResponse:\n{result}")


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "Fix the bug in auth.py"
    asyncio.run(main(request))
