"""
Review Loop — writer and reviewer iterate until approval.

Demonstrates:
  - ``async for`` with ``.iterate()``: native loop with break condition
  - ``>>`` chaining agents with transforms between them
  - ``agent()`` persistent agents: both remember the full conversation,
    so the reviewer sees the evolution and the writer sees all feedback

This is the canonical "write → review → revise → review → approve" loop
that can't be expressed as a terrarium (needs strict turn ordering and
a convergence check controlled by your code).

Usage:
    python review_loop.py "Write a haiku about programming"
"""

import asyncio
import sys

from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config


def make_writer_config():
    config = load_agent_config("@kt-defaults/creatures/general")
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
    config = load_agent_config("@kt-defaults/creatures/general")
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
    print(f"Task: {task}\n")

    async with (
        await agent(make_writer_config()) as writer,
        await agent(make_reviewer_config()) as reviewer,
    ):
        # Pipeline: writer produces text → bridge formats for reviewer → reviewer evaluates
        write_and_review = (
            writer
            >> (lambda text: f"Review this text:\n\n{text}\n\nIs it good enough?")
            >> reviewer
        )

        # Iterate until reviewer approves
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

            # Feed reviewer's feedback back as the writer's next input
            # (iterate() automatically does this — output becomes next input)
            # But we want to frame it as feedback:
            write_and_review.iterate(task)  # no-op, persistent agents remember


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Write a haiku about programming"
    asyncio.run(main(task))
