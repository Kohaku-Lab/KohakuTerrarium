"""
Ensemble Voting — multiple agents solve the same problem, best answer wins.

Demonstrates:
  - ``&`` (parallel): 3 agents run simultaneously on the same input
  - ``>>`` with auto-wrap: pipe tuple of results through a voting function
  - ``|`` (fallback): if ensemble fails, fall back to a single expert
  - ``* N`` (retry): retry the ensemble if voting is inconclusive

Pattern: redundancy through diversity. Different models/prompts produce
different outputs. A voting function picks the best one. More reliable
than any single agent.

Usage:
    python ensemble_voting.py "What causes rain?"
"""

import asyncio
import sys

from kohakuterrarium.compose import factory
from kohakuterrarium.core.config import load_agent_config


def make_expert(name: str, style: str):
    """Build a tool-free expert configuration with one answer style."""
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = f"expert-{name}"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        f"You are an expert. Answer questions {style}.\n"
        "Give ONE clear answer in 2-3 sentences. No hedging."
    )
    return config


def pick_best(answers: tuple[str, ...]) -> str:
    """Choose the longest answer as a simple detail proxy for this example."""
    if not answers:
        raise ValueError("No answers to vote on")
    return max(answers, key=len)


def format_result(answer: str) -> str:
    """Normalize surrounding whitespace in the selected answer."""
    return answer.strip()


async def main(question: str) -> None:
    """Run a parallel expert ensemble with retry and single-agent fallback."""
    print(f"Question: {question}\n")

    # Factories create fresh experts so calls do not share conversation state.
    analytical = factory(
        make_expert("analytical", "with logical analysis and examples")
    )
    creative = factory(
        make_expert("creative", "using analogies and vivid explanations")
    )
    concise = factory(make_expert("concise", "as briefly and precisely as possible"))

    # Parallel outputs are reduced by a deterministic voting transform.
    ensemble = (analytical & creative & concise) >> pick_best >> format_result

    # Retry the ensemble once, then fall back to one analytical expert.
    safe_pipeline = (ensemble * 2) | analytical

    print("Running ensemble (3 experts in parallel)...")
    result = await safe_pipeline(question)
    print(f"\nBest answer:\n{result}")


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What causes rain?"
    asyncio.run(main(question))
