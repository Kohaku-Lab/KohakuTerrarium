"""
Read command - Read job output.
Info command - Get tool/subagent documentation.

These commands are used by legacy/custom text tool-call formats; native
models use the corresponding registered tools.
"""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.builtin_skills import (
    BUILTIN_SKILLS_DIR,
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
    read_skill_body,
)
from kohakuterrarium.commands.base import BaseCommand, CommandResult, parse_command_args
from kohakuterrarium.skill_docs import SkillDoc, load_skill_doc


class ReadCommand(BaseCommand):
    """
    Read job output command.

    Retrieves output from a completed or running job.

    Usage:
        ##read_job job_123##
        ##read_job job_123 --lines 50##
        ##read_job job_123 --lines 50 --offset 10##
    """

    @property
    def command_name(self) -> str:
        return "read_job"

    @property
    def description(self) -> str:
        return "Read output from a job"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Read job output."""
        job_id, kwargs = parse_command_args(args)

        if not job_id:
            return CommandResult(error="No job_id provided. Usage: ##read_job job_id##")

        # Get optional parameters
        lines = int(kwargs.get("lines", 0))
        offset = int(kwargs.get("offset", 0))

        # Get job result from context
        # Context should have get_job_result method
        if not hasattr(context, "get_job_result"):
            return CommandResult(error="Context does not support job result retrieval")

        result = context.get_job_result(job_id)
        if result is None:
            # Check if job exists but not completed
            if hasattr(context, "get_job_status"):
                status = context.get_job_status(job_id)
                if status is not None:
                    if status.is_running:
                        return CommandResult(
                            content=f"[Job {job_id} is still running: {status.to_context_string()}]"
                        )
                    elif not status.is_complete:
                        return CommandResult(content=f"[Job {job_id} is pending]")
            return CommandResult(error=f"Job not found: {job_id}")

        # Get output
        output = result.output or ""

        # Apply slicing if requested
        if lines > 0 or offset > 0:
            output_lines = output.split("\n")
            if offset > 0:
                output_lines = output_lines[offset:]
            if lines > 0:
                output_lines = output_lines[:lines]
            output = "\n".join(output_lines)

        # Format result
        if result.error:
            content = f"## Job {job_id} (error)\n\nError: {result.error}\n\n"
            if output:
                content += f"Output:\n```\n{output}\n```"
        else:
            content = f"## Job {job_id} Output\n\n```\n{output}\n```"

            if result.exit_code is not None:
                content += f"\n\nExit code: {result.exit_code}"

        return CommandResult(content=content)


class InfoCommand(BaseCommand):
    """
    Get documentation for a tool or sub-agent.

    Loads documentation from files in order of priority:
    1. prompts/tools/{name}.md (agent folder - user override)
    2. prompts/subagents/{name}.md (agent folder - user override)
    3. Builtin skills from package (builtin_skills/tools/{name}.md)
    4. Tool's get_full_documentation() method
    5. ToolInfo.documentation field
    6. Basic description fallback

    Legacy text-format command counterpart of the native ``info`` tool.
    """

    @property
    def command_name(self) -> str:
        return "info"

    @property
    def description(self) -> str:
        return "Get documentation for a tool, sub-agent, or procedural skill"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Get tool/subagent documentation."""
        target_name, _ = parse_command_args(args)

        if not target_name:
            return CommandResult(error="No name provided. Use info(name=...).")

        # 1. Try to load from agent folder first (user override)
        if hasattr(context, "agent_path") and context.agent_path:
            agent_path = Path(context.agent_path)

            # Try tool documentation file
            tool_doc_path = agent_path / "prompts" / "tools" / f"{target_name}.md"
            if tool_doc_path.exists():
                rendered = _render_skill_from_path(tool_doc_path)
                if rendered is not None:
                    return CommandResult(content=rendered)

            # Try subagent documentation file
            subagent_doc_path = (
                agent_path / "prompts" / "subagents" / f"{target_name}.md"
            )
            if subagent_doc_path.exists():
                rendered = _render_skill_from_path(subagent_doc_path)
                if rendered is not None:
                    return CommandResult(content=rendered)

        # 2. Try builtin skills from package
        rendered = _render_builtin_skill("tools", target_name)
        if rendered is not None:
            return CommandResult(content=rendered)

        rendered = _render_builtin_skill("subagents", target_name)
        if rendered is not None:
            return CommandResult(content=rendered)

        # 3. Try to get tool info from registry
        if hasattr(context, "get_tool_info"):
            tool_info = context.get_tool_info(target_name)
            if tool_info is not None:
                # Try to get full documentation from tool instance
                if hasattr(context, "get_tool") and context.get_tool:
                    tool = context.get_tool(target_name)
                    if tool and hasattr(tool, "get_full_documentation"):
                        doc = tool.get_full_documentation()
                        if doc:
                            return CommandResult(content=doc)

                # Fall back to ToolInfo documentation
                return CommandResult(
                    content=tool_info.documentation
                    or f"# {target_name}\n\n{tool_info.description}"
                )

        # 4. Try to get subagent info
        if hasattr(context, "get_subagent_info"):
            subagent_info = context.get_subagent_info(target_name)
            if subagent_info is not None:
                return CommandResult(content=subagent_info)

        # 5. Fall through to procedural skills (Cluster 4 / Qc).
        # The controller context carries a reference to the runtime
        # SkillRegistry when one exists; resolve the skill and render
        # its body with a short preamble so the model can tell this is
        # a skill rather than a registered tool.
        skill_content = _render_skill_info(context, target_name)
        if skill_content is not None:
            return CommandResult(content=skill_content)

        return CommandResult(error=f"Not found: {target_name}")


def _format_skill_for_info(doc: "SkillDoc", body: str) -> str:
    """Render a ``SkillDoc`` body with a short ``Tags:`` preamble.

    Tags are wired into the info output so that the first-class
    ``SkillDoc.tags`` field is actually consumed by the agent — otherwise
    it would just be parsed-and-discarded metadata.
    """
    if not doc.tags:
        return body
    tag_line = "Tags: " + ", ".join(str(t) for t in doc.tags)
    if not body:
        return tag_line
    return f"{tag_line}\n\n{body}"


def _render_skill_from_path(path: Path) -> str | None:
    """Load a skill doc from ``path`` and render its body with tags.

    Falls back to the raw body via :func:`read_skill_body` if YAML parsing
    fails, so info output never breaks on malformed frontmatter.
    """
    doc = load_skill_doc(path)
    if doc is not None:
        return _format_skill_for_info(doc, doc.content)
    # load_skill_doc failed (already logged). Degrade gracefully.
    return read_skill_body(path)


def _render_skill_info(context: Any, name: str) -> str | None:
    """Resolve ``name`` against the procedural-skill registry.

    Returns ``None`` when no matching skill exists so :class:`InfoCommand`
    can fall back to its "Not found" error.
    """
    registry = _lookup_skill_registry(context)
    if registry is None:
        return None
    skill = registry.get(name)
    if skill is None:
        return None
    preamble = f"--- Skill: {skill.name} ---"
    origin = f"Origin: {skill.origin}"
    desc = (skill.description or "").strip()
    parts = [preamble, origin]
    if desc:
        parts.append(f"Description: {desc}")
    if skill.paths:
        parts.append(f"Paths: {', '.join(skill.paths)}")
    parts.append("")  # blank line
    if skill.body:
        parts.append(skill.body)
    return "\n".join(parts)


def _lookup_skill_registry(context: Any):
    """Extract the SkillRegistry from whatever shape of context we got."""
    if context is None:
        return None
    # Controller context carries the registry at ``skills_registry`` in
    # its session.extra or via an attribute; tool contexts expose the
    # active agent; test contexts may expose it directly.
    direct = getattr(context, "skills_registry", None)
    if direct is not None:
        return direct
    agent = getattr(context, "agent", None)
    if agent is not None:
        direct = getattr(agent, "skills", None)
        if direct is not None:
            return direct
    controller = getattr(context, "controller", None)
    if controller is not None:
        direct = getattr(controller, "skills_registry", None)
        if direct is not None:
            return direct
        agent = getattr(controller, "_agent", None)
        if agent is not None:
            direct = getattr(agent, "skills", None)
            if direct is not None:
                return direct
    # Session-based lookup.
    session = getattr(context, "session", None)
    if session is not None:
        extras = getattr(session, "extra", None) or {}
        if isinstance(extras, dict) and extras.get("skills_registry"):
            return extras["skills_registry"]
    return None


def _render_builtin_skill(kind: str, name: str) -> str | None:
    """Render a built-in skill (``tools`` or ``subagents``) by name.

    Uses :func:`load_skill_doc` so we can surface tags; falls back to the
    body-only builtin helpers if the SKILL.md fails to parse.
    """
    doc_path = BUILTIN_SKILLS_DIR / kind / f"{name}.md"
    if doc_path.exists():
        rendered = _render_skill_from_path(doc_path)
        if rendered is not None:
            return rendered
    # Degrade to the pre-existing body-only helpers as a final fallback
    # (used when the path itself is missing but a helper finds it via some
    # other convention in the future).
    if kind == "tools":
        return get_builtin_tool_doc(name)
    if kind == "subagents":
        return get_builtin_subagent_doc(name)
    return None


class JobsCommand(BaseCommand):
    """
    List running and recent background jobs.

    Usage:
        <jobs/>
    """

    @property
    def command_name(self) -> str:
        return "jobs"

    @property
    def description(self) -> str:
        return "List running background jobs"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """List jobs."""
        if not hasattr(context, "job_store"):
            return CommandResult(error="No job store available")

        job_store = context.job_store
        running = job_store.get_running_jobs()

        if not running:
            return CommandResult(content="No running jobs.")

        lines = ["## Running Jobs", ""]
        for job in running:
            lines.append(f"- `{job.job_id}`: {job.type_name} ({job.state.value})")

        return CommandResult(content="\n".join(lines))


class WaitCommand(BaseCommand):
    """
    Wait for a background job or sub-agent to complete.

    Usage:
        [/wait]job_id[wait/]              - Wait until job completes (up to 60s)
        [/wait timeout="30"]job_id[wait/] - Wait up to 30 seconds
        [/wait timeout="5"]job_id[wait/]  - Quick check (wait 5 seconds max)

    The wait command blocks until:
    - Job completes (returns result)
    - Timeout reached (returns timeout message)
    - Job not found (returns error)

    How it works:
    - Uses shared job_store (same as executor and subagent_manager)
    - Polls job status every 0.5 seconds until complete or timeout
    - Returns job result (output or error) when complete

    When to use:
    - Sub-agents always run in background; use wait to get their results
    - Background tools (execution_mode=BACKGROUND) also need wait
    - Direct tools don't need wait - their results come automatically

    Without wait:
    - The main agent loop reports job status ("RUNNING", then "DONE")
    - But the model doesn't block - it continues generating
    - Wait allows the model to explicitly block for a specific job
    """

    @property
    def command_name(self) -> str:
        return "wait"

    @property
    def description(self) -> str:
        return "Wait for background job/sub-agent to complete (use timeout=N for max seconds)"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Wait for job."""
        job_id, kwargs = parse_command_args(args)

        if not job_id:
            return CommandResult(error="No job_id provided. Usage: <wait>job_id</wait>")

        timeout = float(kwargs.get("timeout", 60.0))

        # Check if job exists
        if not hasattr(context, "job_store"):
            return CommandResult(error="No job store available")

        status = context.job_store.get_status(job_id)
        if status is None:
            return CommandResult(error=f"Job not found: {job_id}")

        # If already complete, return result
        if status.is_complete:
            result = context.job_store.get_result(job_id)
            if result:
                if result.error:
                    return CommandResult(content=f"## {job_id} - ERROR\n{result.error}")
                return CommandResult(
                    content=f"## {job_id} - DONE\n{result.output[:2000]}"
                )
            return CommandResult(content=f"## {job_id} - DONE (no output)")

        # Wait for completion
        try:
            # Poll for completion
            elapsed = 0.0
            interval = 0.5
            while elapsed < timeout:
                await asyncio.sleep(interval)
                elapsed += interval

                status = context.job_store.get_status(job_id)
                if status and status.is_complete:
                    result = context.job_store.get_result(job_id)
                    if result:
                        if result.error:
                            return CommandResult(
                                content=f"## {job_id} - ERROR\n{result.error}"
                            )
                        return CommandResult(
                            content=f"## {job_id} - DONE\n{result.output[:2000]}"
                        )
                    return CommandResult(content=f"## {job_id} - DONE (no output)")

            return CommandResult(
                content=f"## {job_id} - TIMEOUT\nJob still running after {timeout}s"
            )

        except asyncio.CancelledError:
            return CommandResult(content=f"## {job_id} - CANCELLED")


# Default command instances
read_command = ReadCommand()
info_command = InfoCommand()
jobs_command = JobsCommand()
wait_command = WaitCommand()
