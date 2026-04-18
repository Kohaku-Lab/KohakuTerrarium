"""
Debate Arena — multi-agent debate with external turn control.

Three agents:
  - Proposer: argues FOR a position
  - Opponent: argues AGAINST
  - Judge: evaluates each round, declares winner

Uses the composition algebra:
  - ``await agent(config)`` for persistent agents
  - ``>>`` to pipe output through transforms
  - ``async for`` to loop rounds with native control flow

Why code, not terrarium?
  Terrarium channels are event-driven queues — any creature can send
  at any time. A debate requires STRICT ORDERING: proposer speaks,
  then opponent responds to exactly that, then judge scores both.
  Channels can't express "wait for exactly 2 inputs then process."
  Your code enforces the protocol.

Usage:
    python debate_arena.py "AI will replace most white-collar jobs within 10 years"
"""

import asyncio
import sys

from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config

# ── Config builders ──────────────────────────────────────────────────


def make_debater_config(name: str, stance: str, topic: str):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = f"debater-{name.lower()}"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        f"You are {name}, a skilled debater.\n"
        f"Your position: {stance} the following claim:\n\n"
        f'  "{topic}"\n\n'
        "Rules:\n"
        "- Make ONE clear argument per turn (2-4 sentences)\n"
        "- Directly address your opponent's last point\n"
        "- Use evidence, logic, or real-world examples\n"
        "- Never agree with your opponent\n"
        "- Never break character or discuss the debate format"
    )
    return config


def make_judge_config(topic: str):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = "judge"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are an impartial debate judge.\n"
        f'The topic is: "{topic}"\n\n'
        "After each round, you receive both arguments.\n"
        "Score the round:\n"
        "- Which argument was stronger and why (1 sentence)\n"
        "- Score: PROPOSER or OPPONENT or TIE\n\n"
        "Be concise. End your response with exactly one of:\n"
        "  SCORE: PROPOSER\n"
        "  SCORE: OPPONENT\n"
        "  SCORE: TIE"
    )
    return config


def parse_score(judge_text: str) -> str:
    for line in reversed(judge_text.splitlines()):
        line = line.strip().upper()
        if "SCORE:" in line:
            if "PROPOSER" in line:
                return "PROPOSER"
            if "OPPONENT" in line:
                return "OPPONENT"
            if "TIE" in line:
                return "TIE"
    return "TIE"


# ── Main ─────────────────────────────────────────────────────────────


async def run_debate(topic: str, max_rounds: int = 4) -> None:
    print(f'\n{"=" * 60}')
    print(f"DEBATE: {topic}")
    print(f'{"=" * 60}\n')

    # Create persistent agents — they accumulate conversation context
    async with (
        await agent(
            make_debater_config("Proposer", "ARGUE IN FAVOR OF", topic)
        ) as proposer,
        await agent(
            make_debater_config("Opponent", "ARGUE AGAINST", topic)
        ) as opponent,
        await agent(make_judge_config(topic)) as judge,
    ):

        scores = {"PROPOSER": 0, "OPPONENT": 0, "TIE": 0}

        # Build the proposer >> bridge >> opponent pipeline
        debate_round = (
            proposer
            >> (lambda prop_arg: f"Your opponent argued:\n\n{prop_arg}\n\nRespond:")
            >> opponent
        )

        # Iterate rounds using async for
        prompt = f"The debate begins. State your opening argument for: {topic}"
        round_num = 0

        async for opp_arg in debate_round.iterate(prompt):
            round_num += 1
            print(f"\n--- Round {round_num} ---\n")
            print(f"OPPONENT: {opp_arg}\n")

            # Judge scores — use >> to parse the score
            judge_prompt = (
                f"Round {round_num}:\n\n" f"OPPONENT: {opp_arg}\n\n" "Score this round."
            )
            verdict = await (judge >> parse_score)(judge_prompt)
            scores[verdict] += 1
            print(f"JUDGE: {verdict}\n")

            if round_num >= max_rounds:
                break

            # Feed opponent's argument back to proposer for next round
            debate_round.iterate(
                prompt
            )  # reset not needed — persistent agents remember

        # Final verdict
        print(f'\n{"=" * 60}')
        print("FINAL SCORES")
        print(f"  Proposer: {scores['PROPOSER']} rounds")
        print(f"  Opponent: {scores['OPPONENT']} rounds")
        print(f"  Ties:     {scores['TIE']} rounds")

        if scores["PROPOSER"] > scores["OPPONENT"]:
            print("\nWINNER: PROPOSER")
        elif scores["OPPONENT"] > scores["PROPOSER"]:
            print("\nWINNER: OPPONENT")
        else:
            print("\nRESULT: DRAW")
        print(f'{"=" * 60}')


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "Pineapple belongs on pizza"
    asyncio.run(run_debate(topic))
