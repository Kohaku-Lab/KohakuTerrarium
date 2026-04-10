"""
Task Orchestrator — decompose tasks, dispatch to specialist agents.

A planner agent breaks a complex request into sub-tasks.
Your code creates specialist agents per sub-task, manages a
dependency graph, runs independent tasks in parallel, and
aggregates results.

Why code, not terrarium?
  - Agent TOPOLOGY is dynamic: the number and type of specialists
    depends on the task. Can't pre-define this in terrarium YAML.
  - DEPENDENCIES need a DAG executor: "task C depends on A and B"
    means C waits until both A and B finish. Channels can't express
    join points natively.
  - Specialists are EPHEMERAL: created for one sub-task, destroyed
    after. Terrarium creatures are long-running.
  - Your code manages the lifecycle: create → run → collect → destroy.

Example:
    python task_orchestrator.py "Build a landing page for a coffee shop"
"""

import asyncio
import json
import sys
from dataclasses import dataclass, field

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.serving.agent_session import AgentSession


@dataclass
class SubTask:
    id: str
    description: str
    specialist: str  # "writer", "designer", "coder", "reviewer"
    depends_on: list[str] = field(default_factory=list)
    result: str = ""
    status: str = "pending"  # pending, running, done


# ── Agent factory ────────────────────────────────────────────────────

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


async def create_specialist(role: str, context: str = "") -> AgentSession:
    """Create a specialist agent for a specific role."""
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = f"specialist-{role}"
    config.tools = [] if role != "coder" else [
        {"name": "write", "type": "builtin"},
        {"name": "read", "type": "builtin"},
    ]
    config.subagents = []
    prompt = SPECIALIST_PROMPTS.get(role, f"You are a {role}.")
    if context:
        prompt += f"\n\nContext from previous tasks:\n{context}"
    config.system_prompt = prompt
    return await AgentSession.from_config(config)


async def run_agent(session: AgentSession, prompt: str) -> str:
    """Run an agent and collect its full response."""
    parts: list[str] = []
    async for chunk in session.chat(prompt):
        parts.append(chunk)
    return "".join(parts).strip()


# ── DAG executor ─────────────────────────────────────────────────────


def get_ready_tasks(tasks: dict[str, SubTask]) -> list[SubTask]:
    """Find tasks whose dependencies are all satisfied."""
    ready = []
    for task in tasks.values():
        if task.status != "pending":
            continue
        deps_done = all(
            tasks[dep].status == "done" for dep in task.depends_on
        )
        if deps_done:
            ready.append(task)
    return ready


async def execute_task(task: SubTask, tasks: dict[str, SubTask]) -> None:
    """Execute a single sub-task with a specialist agent."""
    task.status = "running"
    print(f"  [{task.id}] Starting: {task.description} (specialist: {task.specialist})")

    # Gather context from completed dependencies
    dep_context = ""
    for dep_id in task.depends_on:
        dep = tasks[dep_id]
        dep_context += f"\n--- Result of '{dep.description}' ---\n{dep.result}\n"

    # Create specialist, run task, destroy
    session = await create_specialist(task.specialist, dep_context)
    try:
        task.result = await run_agent(session, task.description)
        task.status = "done"
        preview = task.result[:100].replace("\n", " ")
        print(f"  [{task.id}] Done: {preview}...")
    finally:
        await session.stop()


async def run_dag(tasks: dict[str, SubTask]) -> None:
    """Execute tasks respecting dependencies, parallelizing where possible."""
    while any(t.status != "done" for t in tasks.values()):
        ready = get_ready_tasks(tasks)
        if not ready:
            # Deadlock or error
            pending = [t.id for t in tasks.values() if t.status == "pending"]
            raise RuntimeError(f"Deadlock: tasks {pending} can't proceed")

        # Run all ready tasks in parallel
        print(f"\n  Wave: {[t.id for t in ready]}")
        await asyncio.gather(*(execute_task(t, tasks) for t in ready))


# ── Main orchestration ───────────────────────────────────────────────


async def orchestrate(request: str) -> None:
    print(f'\n{"=" * 60}')
    print(f"REQUEST: {request}")
    print(f'{"=" * 60}')

    # Step 1: Planner agent decomposes the task
    print("\n[1/3] Planning...")
    planner = await create_specialist("planner")
    try:
        plan_json = await run_agent(planner, request)
    finally:
        await planner.stop()

    # Parse the plan
    try:
        # Strip markdown fences if the model wrapped it
        cleaned = plan_json.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        raw_tasks = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"Planner output wasn't valid JSON:\n{plan_json}")
        return

    tasks: dict[str, SubTask] = {}
    for item in raw_tasks:
        task = SubTask(
            id=item["id"],
            description=item["description"],
            specialist=item.get("specialist", "writer"),
            depends_on=item.get("depends_on", []),
        )
        tasks[task.id] = task

    print(f"\nPlan: {len(tasks)} sub-tasks")
    for t in tasks.values():
        deps = f" (after {t.depends_on})" if t.depends_on else ""
        print(f"  {t.id}: [{t.specialist}] {t.description}{deps}")

    # Step 2: Execute the DAG
    print("\n[2/3] Executing...")
    await run_dag(tasks)

    # Step 3: Aggregate results
    print(f'\n[3/3] Results\n{"=" * 60}')
    for task in tasks.values():
        print(f"\n--- {task.id}: {task.description} ---")
        print(task.result[:500])
        if len(task.result) > 500:
            print(f"... ({len(task.result)} chars total)")

    print(f'\n{"=" * 60}')
    print(f"Completed {len(tasks)} sub-tasks for: {request}")


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "Build a landing page for a coffee shop"
    asyncio.run(orchestrate(request))
