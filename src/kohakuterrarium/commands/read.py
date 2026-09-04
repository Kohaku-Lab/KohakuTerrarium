"""
Legacy text-format commands for job output, documentation, and job control.

Native tool-call formats use the corresponding registered tools.
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
from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.skill_docs import SkillDoc, load_skill_doc


class ReadCommand(BaseCommand):
    """Read and optionally slice a running or completed job's output."""

    @property
    def command_name(self) -> str:
        return "read_job"

    @property
    def description(self) -> str:
        return "Read output from a job"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Return rendered job output or its current state."""
        job_id, kwargs = parse_command_args(args)

        if not job_id:
            return CommandResult(error="No job_id provided. Usage: ##read_job job_id##")

        lines = int(kwargs.get("lines", 0))
        offset = int(kwargs.get("offset", 0))

        # Text commands require a context that can expose job results.
        if not hasattr(context, "get_job_result"):
            return CommandResult(error="Context does not support job result retrieval")

        result = _get_job_result(context, job_id)
        if result is None:
            # A missing result may still represent a pending or running job.
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

        # Multimodal parts render as text placeholders in this text command.
        output = render_content_text(result.output or "")

        if lines > 0 or offset > 0:
            output_lines = output.split("\n")
            if offset > 0:
                output_lines = output_lines[offset:]
            if lines > 0:
                output_lines = output_lines[:lines]
            output = "\n".join(output_lines)

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
    """Resolve tool, sub-agent, or skill documentation by precedence.

    Creature overrides precede built-in docs, runtime objects, registry metadata,
    and procedural skills. This is the text-format counterpart of ``info``.
    """

    @property
    def command_name(self) -> str:
        return "info"

    @property
    def description(self) -> str:
        return "Get documentation for a tool, sub-agent, or procedural skill"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Return the highest-priority documentation for a named target."""
        target_name, _ = parse_command_args(args)

        if not target_name:
            return CommandResult(error="No name provided. Use info(name=...).")

        # Creature-local documentation overrides every shared source.
        if hasattr(context, "agent_path") and context.agent_path:
            agent_path = Path(context.agent_path)

            tool_doc_path = agent_path / "prompts" / "tools" / f"{target_name}.md"
            if tool_doc_path.exists():
                rendered = _render_skill_from_path(tool_doc_path)
                if rendered is not None:
                    return CommandResult(content=rendered)

            subagent_doc_path = (
                agent_path / "prompts" / "subagents" / f"{target_name}.md"
            )
            if subagent_doc_path.exists():
                rendered = _render_skill_from_path(subagent_doc_path)
                if rendered is not None:
                    return CommandResult(content=rendered)

        # Built-in markdown precedes runtime registry fallbacks.
        rendered = _render_builtin_skill("tools", target_name)
        if rendered is not None:
            return CommandResult(content=rendered)

        rendered = _render_builtin_skill("subagents", target_name)
        if rendered is not None:
            return CommandResult(content=rendered)

        # Runtime tools may provide richer documentation than registry metadata.
        if hasattr(context, "get_tool_info"):
            tool_info = context.get_tool_info(target_name)
            if tool_info is not None:
                if hasattr(context, "get_tool") and context.get_tool:
                    tool = context.get_tool(target_name)
                    if tool and hasattr(tool, "get_full_documentation"):
                        doc = tool.get_full_documentation()
                        if doc:
                            return CommandResult(content=doc)

                # Registry metadata is the final tool fallback.
                return CommandResult(
                    content=tool_info.resolve_documentation()
                    or f"# {target_name}\n\n{tool_info.description}"
                )

        if hasattr(context, "get_subagent_info"):
            subagent_info = context.get_subagent_info(target_name)
            if subagent_info is not None:
                return CommandResult(content=subagent_info)

        # Procedural skills are distinct from registered tools in the rendered output.
        skill_content = _render_skill_info(context, target_name)
        if skill_content is not None:
            return CommandResult(content=skill_content)

        return CommandResult(error=f"Not found: {target_name}")


def _get_job_result(context: Any, job_id: str):
    getter = getattr(context, "get_job_result", None)
    if callable(getter):
        try:
            result = getter(job_id)
        except Exception:
            result = None
        if result is not None and not type(result).__module__.startswith(
            "unittest.mock"
        ):
            return result
    job_store = getattr(context, "job_store", None)
    if job_store is not None and hasattr(job_store, "get_result"):
        return job_store.get_result(job_id)
    return None


def _format_skill_for_info(doc: "SkillDoc", body: str) -> str:
    """Render a skill body with its tags when present."""
    if not doc.tags:
        return body
    tag_line = "Tags: " + ", ".join(str(t) for t in doc.tags)
    if not body:
        return tag_line
    return f"{tag_line}\n\n{body}"


def _render_skill_from_path(path: Path) -> str | None:
    """Render parsed skill metadata, falling back to the raw markdown body."""
    doc = load_skill_doc(path)
    if doc is not None:
        return _format_skill_for_info(doc, doc.content)
    # Malformed frontmatter must not hide otherwise readable documentation.
    return read_skill_body(path)


def _render_skill_info(context: Any, name: str) -> str | None:
    """Render a procedural skill or return ``None`` when it is absent."""
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
    parts.append("")  # Separate metadata from the skill body.
    if skill.body:
        parts.append(skill.body)
    return "\n".join(parts)


def _lookup_skill_registry(context: Any):
    """Find the skill registry across supported command-context shapes."""
    if context is None:
        return None
    # Contexts may expose the registry directly, through an agent, or a controller.
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
    session = getattr(context, "session", None)
    if session is not None:
        extras = getattr(session, "extra", None) or {}
        if isinstance(extras, dict):
            # Empty registries are valid despite being falsy.
            reg = extras.get("skills_registry")
            if reg is not None:
                return reg
    return None


def _render_builtin_skill(kind: str, name: str) -> str | None:
    """Render built-in markdown with metadata, then use body-only helpers."""
    doc_path = BUILTIN_SKILLS_DIR / kind / f"{name}.md"
    if doc_path.exists():
        rendered = _render_skill_from_path(doc_path)
        if rendered is not None:
            return rendered
    # Helper lookup preserves alternate built-in storage conventions.
    if kind == "tools":
        return get_builtin_tool_doc(name)
    if kind == "subagents":
        return get_builtin_subagent_doc(name)
    return None


class JobsCommand(BaseCommand):
    """List currently running background jobs."""

    @property
    def command_name(self) -> str:
        return "jobs"

    @property
    def description(self) -> str:
        return "List running background jobs"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Render the active jobs from the shared job store."""
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
    """Poll a background job until completion or a caller-specified timeout."""

    @property
    def command_name(self) -> str:
        return "wait"

    @property
    def description(self) -> str:
        return "Wait for background job/sub-agent to complete (use timeout=N for max seconds)"

    async def _execute(self, args: str, context: Any) -> CommandResult:
        """Return a completed result or the terminal wait state."""
        job_id, kwargs = parse_command_args(args)

        if not job_id:
            return CommandResult(error="No job_id provided. Usage: <wait>job_id</wait>")

        timeout = float(kwargs.get("timeout", 60.0))

        if not hasattr(context, "job_store"):
            return CommandResult(error="No job store available")

        status = context.job_store.get_status(job_id)
        if status is None:
            return CommandResult(error=f"Job not found: {job_id}")

        # Avoid polling jobs that already reached a terminal state.
        if status.is_complete:
            result = _get_job_result(context, job_id)
            if result:
                if result.error:
                    return CommandResult(content=f"## {job_id} - ERROR\n{result.error}")
                return CommandResult(
                    content=f"## {job_id} - DONE\n{render_content_text(result.output)}"
                )
            return CommandResult(content=f"## {job_id} - DONE (no output)")

        try:
            elapsed = 0.0
            interval = 0.5
            while elapsed < timeout:
                await asyncio.sleep(interval)
                elapsed += interval

                status = context.job_store.get_status(job_id)
                if status and status.is_complete:
                    result = (
                        context.get_job_result(job_id)
                        if hasattr(context, "get_job_result")
                        else context.job_store.get_result(job_id)
                    )
                    if result:
                        if result.error:
                            return CommandResult(
                                content=f"## {job_id} - ERROR\n{result.error}"
                            )
                        return CommandResult(
                            content=f"## {job_id} - DONE\n{render_content_text(result.output)}"
                        )
                    return CommandResult(content=f"## {job_id} - DONE (no output)")

            return CommandResult(
                content=f"## {job_id} - TIMEOUT\nJob still running after {timeout}s"
            )

        except asyncio.CancelledError:
            return CommandResult(content=f"## {job_id} - CANCELLED")


# Shared stateless command instances.
read_command = ReadCommand()
info_command = InfoCommand()
jobs_command = JobsCommand()
wait_command = WaitCommand()
