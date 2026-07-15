"""
Review Loop — writer and reviewer iterate until approval.

Demonstrates:
  - ``async for`` with ``.iterate()``: native loop with break condition
  - ``>>`` chaining agents with transforms between them
  - ``agent()`` persistent agents: both remember the full conversation,
    so the reviewer sees the evolution and the writer sees all feedback

This write → review → revise protocol uses native control flow because it
requires strict turn ordering and an application-owned convergence check.

Usage:
    python review_loop.py "Write a haiku about programming"
"""

import asyncio
import sys

from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config


def make_writer_config():
    """Build a persistent tool-free writer that emits only revised text."""
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = "writer"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a writer. When given a task or feedback, produce "
        "improved text. Output ONLY the text, no commentary.\n\n"
        "If you receive feedback, revise your work accordingly."
    )
    return config


def make_reviewer_config():
    """Build a persistent reviewer with an explicit approval sentinel."""
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = "reviewer"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a strict reviewer. Evaluate the text you receive.\n\n"
        "If it needs improvement: explain what's wrong and how to fix it.\n"
        "If it's good enough: respond with EXACTLY 'APPROVED' on the first line, "
        "followed by a brief compliment.\n\n"
        "Be demanding — only approve truly good work."
    )
    return config


async def main(task: str) -> None:
    """Iterate writer and reviewer turns until approval or the round limit."""
    print(f"Task: {task}\n")

    async with (
        await agent(make_writer_config()) as writer,
        await agent(make_reviewer_config()) as reviewer,
    ):
        # The transform frames each writer output as the reviewer's next task.
        write_and_review = (
            writer
            >> (lambda text: f"Review this text:\n\n{text}\n\nIs it good enough?")
            >> reviewer
        )

        round_num = 0
        async for feedback in write_and_review.iterate(task):
            round_num += 1
            print(f"--- Round {round_num} ---")
            print(f"Reviewer: {feedback[:200]}")
            print()

            if feedback.strip().startswith("APPROVED"):
                print(f"Approved after {round_num} round(s)!")
                break

            if round_num >= 5:
                print("Max rounds reached — accepting last version.")
                break

            # This constructor call is a no-op; the active iterator already advances.
            write_and_review.iterate(task)


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Write a haiku about programming"
    asyncio.run(main(task))
