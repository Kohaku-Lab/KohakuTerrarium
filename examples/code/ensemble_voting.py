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

# ── Config builders ──────────────────────────────────────────────────


def make_expert(name: str, style: str):
    """Create a config with a specific answering style."""
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = f"expert-{name}"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        f"You are an expert. Answer questions {style}.\n"
        "Give ONE clear answer in 2-3 sentences. No hedging."
    )
    return config


# ── Voting logic ─────────────────────────────────────────────────────


def pick_best(answers: tuple[str, ...]) -> str:
    """Simple voting: pick the longest, most detailed answer.

    In production, you'd use a judge agent or semantic similarity
    to find consensus. This is a demo of the pattern.
    """
    if not answers:
        raise ValueError("No answers to vote on")
    # Pick the answer with the most substance (longest, as proxy)
    return max(answers, key=len)


def format_result(answer: str) -> str:
    """Clean up the winning answer."""
    return answer.strip()


# ── Main ─────────────────────────────────────────────────────────────


async def main(question: str) -> None:
    print(f"Question: {question}\n")

    # Three experts with different styles — all ephemeral (factory)
    analytical = factory(
        make_expert("analytical", "with logical analysis and examples")
    )
    creative = factory(
        make_expert("creative", "using analogies and vivid explanations")
    )
    concise = factory(make_expert("concise", "as briefly and precisely as possible"))

    # The ensemble: run all 3 in parallel, vote on the best answer, format
    ensemble = (analytical & creative & concise) >> pick_best >> format_result

    # Fallback: if ensemble fails (all 3 crash), use a single retry
    safe_pipeline = (ensemble * 2) | analytical

    print("Running ensemble (3 experts in parallel)...")
    result = await safe_pipeline(question)
    print(f"\nBest answer:\n{result}")


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What causes rain?"
    asyncio.run(main(question))
