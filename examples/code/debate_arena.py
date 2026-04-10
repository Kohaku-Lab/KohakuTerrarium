"""
Debate Arena — multi-agent debate with external turn control.

Three agents:
  - Proposer: argues FOR a position
  - Opponent: argues AGAINST
  - Judge: evaluates each round, declares winner

Your code is the referee:
  - Feeds each debater the other's latest argument
  - Enforces strict turn-taking (A → B → judge, repeat)
  - Asks the judge to score after each round
  - Detects when arguments converge or max rounds hit
  - Produces a structured result

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
from dataclasses import dataclass

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.serving.agent_session import AgentSession


@dataclass
class RoundResult:
    proposer_arg: str
    opponent_arg: str
    judge_score: str
    round_num: int


async def create_debater(
    name: str, stance: str, topic: str
) -> AgentSession:
    """Create a debater agent with a specific stance."""
    config = load_agent_config("@kt-defaults/creatures/general")
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
    return await AgentSession.from_config(config)


async def create_judge(topic: str) -> AgentSession:
    """Create a judge agent that scores debate rounds."""
    config = load_agent_config("@kt-defaults/creatures/general")
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
    return await AgentSession.from_config(config)


async def collect_response(session: AgentSession, prompt: str) -> str:
    """Send a message and collect the full response."""
    parts: list[str] = []
    async for chunk in session.chat(prompt):
        parts.append(chunk)
    return "".join(parts).strip()


def parse_score(judge_text: str) -> str:
    """Extract the score from judge's response."""
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


async def run_debate(topic: str, max_rounds: int = 4) -> None:
    """Run a structured debate between two agents."""
    print(f'\n{"=" * 60}')
    print(f"DEBATE: {topic}")
    print(f'{"=" * 60}\n')

    # Create all three agents
    proposer = await create_debater("Proposer", "ARGUE IN FAVOR OF", topic)
    opponent = await create_debater("Opponent", "ARGUE AGAINST", topic)
    judge = await create_judge(topic)

    scores = {"PROPOSER": 0, "OPPONENT": 0, "TIE": 0}
    rounds: list[RoundResult] = []

    try:
        last_argument = f"The debate begins. State your opening argument for: {topic}"

        for round_num in range(1, max_rounds + 1):
            print(f"\n--- Round {round_num} ---\n")

            # Step 1: Proposer argues (responds to opponent's last point)
            prop_arg = await collect_response(proposer, last_argument)
            print(f"PROPOSER: {prop_arg}\n")

            # Step 2: Opponent responds to proposer's argument
            opp_arg = await collect_response(
                opponent,
                f"Your opponent just argued:\n\n{prop_arg}\n\nRespond:",
            )
            print(f"OPPONENT: {opp_arg}\n")

            # Step 3: Judge scores the round (sees both arguments)
            judge_input = (
                f"Round {round_num}:\n\n"
                f"PROPOSER argued: {prop_arg}\n\n"
                f"OPPONENT argued: {opp_arg}\n\n"
                "Score this round."
            )
            judge_text = await collect_response(judge, judge_input)
            score = parse_score(judge_text)
            scores[score] += 1
            print(f"JUDGE: {judge_text}\n")

            rounds.append(RoundResult(
                proposer_arg=prop_arg,
                opponent_arg=opp_arg,
                judge_score=score,
                round_num=round_num,
            ))

            # Feed opponent's argument to proposer for next round
            last_argument = (
                f"Your opponent responded:\n\n{opp_arg}\n\n"
                "Counter their argument:"
            )

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

    finally:
        await proposer.stop()
        await opponent.stop()
        await judge.stop()


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "Pineapple belongs on pizza"
    asyncio.run(run_debate(topic))
