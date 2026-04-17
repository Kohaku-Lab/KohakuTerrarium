"""
Read command - Read job output.
Info command - Get tool/subagent documentation.

Usage:
    ##read_job job_id [--lines N] [--offset M]##
    ##info tool_name##
"""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.builtin_skills import (
    get_builtin_subagent_doc,
    get_builtin_tool_doc,
)
from kohakuterrarium.commands.base import BaseCommand, CommandResult, parse_command_args


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

    Usage:
        ##info tool_name##
        ##info subagent_name##
    """

    @property
    def command_name(self) -> str:
        return "info"

    @property
    def description(self) -> str:
        return "Get documentation for a tool or sub-agent"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Get tool/subagent documentation."""
        target_name, _ = parse_command_args(args)

        if not target_name:
            return CommandResult(error="No name provided. Usage: ##info name##")

        # 1. Try to load from agent folder first (user override)
        if hasattr(context, "agent_path") and context.agent_path:
            agent_path = Path(context.agent_path)

            # Try tool documentation file
            tool_doc_path = agent_path / "prompts" / "tools" / f"{target_name}.md"
            if tool_doc_path.exists():
                content = tool_doc_path.read_text(encoding="utf-8")
                return CommandResult(content=content)

            # Try subagent documentation file
            subagent_doc_path = (
                agent_path / "prompts" / "subagents" / f"{target_name}.md"
            )
            if subagent_doc_path.exists():
                content = subagent_doc_path.read_text(encoding="utf-8")
                return CommandResult(content=content)

        # 2. Try builtin skills from package
        builtin_tool_doc = get_builtin_tool_doc(target_name)
        if builtin_tool_doc:
            return CommandResult(content=builtin_tool_doc)

        builtin_subagent_doc = get_builtin_subagent_doc(target_name)
        if builtin_subagent_doc:
            return CommandResult(content=builtin_subagent_doc)

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

        return CommandResult(error=f"Not found: {target_name}")


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
