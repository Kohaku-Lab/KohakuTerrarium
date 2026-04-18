"""
Task Orchestrator — decompose tasks, dispatch to specialist agents.

Uses the composition algebra:
  - ``factory()`` for ephemeral specialists (created and destroyed per task)
  - ``>>`` with auto-wrapped callables for transforms
  - ``asyncio.gather`` for parallel execution (Python-native, no custom operator)

Why code, not terrarium?
  - Agent TOPOLOGY is dynamic: the number and type of specialists
    depends on the task. Can't pre-define this in terrarium YAML.
  - Dependencies between sub-tasks need a DAG executor, not channels.
  - Specialists are EPHEMERAL: created for one sub-task, destroyed after.

Example:
    python task_orchestrator.py "Build a landing page for a coffee shop"
"""

import asyncio
import json
import sys

from kohakuterrarium.compose import factory
from kohakuterrarium.core.config import load_agent_config

# ── Config builders ──────────────────────────────────────────────────

SPECIALIST_PROMPTS = {
    "planner": (
        "You are a project planner. Given a task, break it into 3-6 sub-tasks.\n"
        "Output ONLY valid JSON — no markdown, no explanation:\n"
        '[\n  {{"id": "t1", "description": "...", "specialist": "writer|designer|coder|reviewer", '
        '"depends_on": []}},\n  ...\n]'
    ),
    "writer": (
        "You are a copywriter. Write clear, compelling copy for the task described. "
        "Output the final text directly, no commentary."
    ),
    "designer": (
        "You are a UI/UX designer. Describe the visual design: layout, colors, "
        "typography, components. Be specific enough that a developer can implement it."
    ),
    "coder": (
        "You are a frontend developer. Write clean HTML/CSS/JS code. "
        "Output only the code, no explanation."
    ),
    "reviewer": (
        "You are a quality reviewer. Review the work done so far and provide "
        "specific, actionable feedback. Note what's good and what needs improvement."
    ),
}


def make_specialist_config(role: str, context: str = ""):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name = f"specialist-{role}"
    config.tools = (
        []
        if role != "coder"
        else [
            {"name": "write", "type": "builtin"},
            {"name": "read", "type": "builtin"},
        ]
    )
    config.subagents = []
    prompt = SPECIALIST_PROMPTS.get(role, f"You are a {role}.")
    if context:
        prompt += f"\n\nContext from previous tasks:\n{context}"
    config.system_prompt = prompt
    return config


# ── DAG helpers ──────────────────────────────────────────────────────


def parse_plan(raw: str) -> list[dict]:
    """Extract task list JSON from planner output."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])
    return json.loads(cleaned)


def topological_waves(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into waves respecting dependencies."""
    done: set[str] = set()
    remaining = list(tasks)
    waves: list[list[dict]] = []

    while remaining:
        wave = [t for t in remaining if all(d in done for d in t.get("depends_on", []))]
        if not wave:
            raise RuntimeError(
                f"Deadlock: {[t['id'] for t in remaining]} can't proceed"
            )
        waves.append(wave)
        done.update(t["id"] for t in wave)
        remaining = [t for t in remaining if t["id"] not in done]

    return waves


# ── Main ─────────────────────────────────────────────────────────────


async def orchestrate(request: str) -> None:
    print(f'\n{"=" * 60}')
    print(f"REQUEST: {request}")
    print(f'{"=" * 60}')

    # Step 1: Plan — factory creates ephemeral planner, >> pipes through parser
    print("\n[1/3] Planning...")
    planner = factory(make_specialist_config("planner"))
    plan = await (planner >> parse_plan)(request)

    print(f"\nPlan: {len(plan)} sub-tasks")
    for t in plan:
        deps = f" (after {t.get('depends_on', [])})" if t.get("depends_on") else ""
        print(
            f"  {t['id']}: [{t.get('specialist', 'writer')}] {t['description']}{deps}"
        )

    # Step 2: Execute DAG — parallel waves using asyncio.gather
    print("\n[2/3] Executing...")
    results: dict[str, str] = {}

    for wave in topological_waves(plan):
        print(f"\n  Wave: {[t['id'] for t in wave]}")

        # Build context from completed dependencies
        def build_context(task: dict) -> str:
            ctx = ""
            for dep_id in task.get("depends_on", []):
                if dep_id in results:
                    ctx += f"\n--- {dep_id} ---\n{results[dep_id]}\n"
            return ctx

        # Run all tasks in this wave in parallel — each with an ephemeral factory agent
        wave_results = await asyncio.gather(
            *(
                factory(
                    make_specialist_config(
                        t.get("specialist", "writer"),
                        build_context(t),
                    )
                )(t["description"])
                for t in wave
            )
        )

        for task, result in zip(wave, wave_results):
            results[task["id"]] = result
            preview = result[:100].replace("\n", " ")
            print(f"  [{task['id']}] Done: {preview}...")

    # Step 3: Results
    print(f'\n[3/3] Results\n{"=" * 60}')
    for task in plan:
        print(f"\n--- {task['id']}: {task['description']} ---")
        text = results.get(task["id"], "")
        print(text[:500])
        if len(text) > 500:
            print(f"... ({len(text)} chars total)")

    print(f'\n{"=" * 60}')
    print(f"Completed {len(plan)} sub-tasks for: {request}")


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "Build a landing page for a coffee shop"
    asyncio.run(orchestrate(request))
