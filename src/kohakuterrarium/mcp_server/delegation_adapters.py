"""Execution owners behind MCP delegation, independent of admission and job IDs."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.job import JobState, JobStore, JobType
from kohakuterrarium.mcp_server.subagent_host import SubagentHost
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir


@dataclass
class ExecutionResult:
    output: str = ""
    error: str | None = None
    state: JobState = JobState.DONE
    metadata: dict[str, Any] = field(default_factory=dict)


def _cancel_startup(task):
    """Interrupt owned startup once without repeatedly cancelling its cleanup."""
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()


class DelegationAdapters:
    """Bind configured targets and share one Studio across Creature sessions."""

    def __init__(self, config, instance_id, *, llm_factory=None):
        self.config = config
        self.instance_id = instance_id
        self.llm_factory = llm_factory
        self.studio = None
        self._lock = asyncio.Lock()

    def create(self, target, history):
        adapter = (
            CreatureExecution
            if self.config.delegation[target].kind == "creature"
            else SubagentExecution
        )
        return adapter(self, target, history)

    def model(self, target):
        return self.llm_factory(target) if self.llm_factory else None

    async def engine(self):
        async with self._lock:
            if self.studio is None:
                studio = Studio(engine=Terrarium())
                await studio.__aenter__()
                self.studio = studio
        return self.studio.engine

    async def close(self):
        if self.studio is not None:
            await self.studio.__aexit__(None, None, None)


class CreatureExecution:
    """Continue a headless Creature, including stop/save/adopt and autonomous work."""

    can_continue = True
    job_type = JobType.CREATURE

    def __init__(self, owner, target, history):
        self.owner, self.target, self.history = owner, target, history
        self._creature = None
        self._pending_llm = None
        self._saved_path = None
        self._state = "starting"
        self._starting_task = None
        self._lock = asyncio.Lock()

    @property
    def busy(self):
        return bool(self._creature and self._creature.agent.is_processing)

    @property
    def state(self):
        if self._state == "running" and self._creature is not None:
            return "paused" if self._creature.paused else self._creature.status
        return self._state

    async def _start(self):
        engine = await self.owner.engine()
        if self._pending_llm is not None:
            await self._pending_llm.close()
            self._pending_llm = None
        llm = self._pending_llm = self.owner.model(self.target)
        if self._saved_path is not None:
            graph = await engine.adopt_session(
                str(self._saved_path), llm=llm, io="headless"
            )
            self._creature = next(
                c
                for c in engine.list_creatures()
                if c.graph_id == graph and c.name == self._creature.name
            )
            self._pending_llm = None
            self._creature.agent.output_router.add_secondary(self.history)
        else:
            path = (
                config_dir()
                / "mcp-serve"
                / "sessions"
                / self.owner.instance_id
                / f"{uuid.uuid4().hex}.kohakutr"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self._creature = await engine.add_creature(
                self.owner.config.delegation[self.target].config,
                llm=llm,
                pwd=str(self.owner.config.workspace),
                io="headless",
                session=path,
                start=False,
            )
            self._pending_llm = None
            self._saved_path = path
            if self.owner.llm_factory is not None:
                # The adapter recreates this provider through its factory on resume.
                self._creature.injected_runtime = tuple(
                    label
                    for label in self._creature.injected_runtime
                    if label != "llm_provider"
                )
            self._creature.agent.output_router.add_secondary(self.history)
            await self._creature.start()

    async def run(self, job_id, prompt):
        result = ExecutionResult()
        try:
            async with self._lock:
                if self._creature is None or self._state == "stopped":
                    self._starting_task = asyncio.current_task()
                    try:
                        await self._start()
                    finally:
                        self._starting_task = None
                self._state = "running"
            if self.busy:
                raise ValueError("Session is busy with autonomous activity")
            event = create_user_input_event(prompt, source="mcp", correlation_id=job_id)
            event.stackable = False
            turn = await self._creature.inject_event(event)
            result.output, result.error = turn.text, turn.error
            result.metadata.update(
                status=turn.status, usage=turn.usage, duration_s=turn.duration_s
            )
            if turn.status == "interrupted":
                result.state = JobState.CANCELLED
            elif not turn.ok:
                result.state, result.error = JobState.ERROR, turn.error or turn.status
        except asyncio.CancelledError:
            result.state, result.error = JobState.CANCELLED, "Delegation cancelled"
        except Exception as exc:
            result.state, result.error = JobState.ERROR, str(exc)
            if self._state == "starting":
                try:
                    await self.close()
                except Exception as cleanup:
                    result.error += f"; resource cleanup failed: {cleanup}"
                if self._creature is None:
                    self._state = "error"
        return result

    async def stop(self):
        task, self._starting_task = self._starting_task, None
        _cancel_startup(task)
        async with self._lock:
            if self._creature is not None and self._state != "stopped":
                await self.owner.studio.sessions.stop(self._creature.graph_id)
                self._state = "stopped"
            elif self._state == "starting":
                self._state = "stopped"
            if self._pending_llm is not None:
                await self._pending_llm.close()
                self._pending_llm = None

    async def close(self):
        await self.stop()

    async def send(self, content):
        if not self.busy:
            return False
        await self._creature.inject_input(content, source="mcp_feedback")
        return True

    def conversation(self):
        if self._creature is None:
            return []
        return self._creature.agent.controller.conversation.to_messages()


class SubagentExecution:
    """Own one task subagent and settle execution only after its cleanup finishes."""

    can_continue = False
    job_type = JobType.SUBAGENT
    busy = False  # No autonomous turns outside the admitted delegation job.

    def __init__(self, owner, target, history):
        self.owner, self.target, self.history = owner, target, history
        self._host = None
        self._pending_llm = None
        self.state = "starting"
        self._starting_task = None
        self._lock = asyncio.Lock()

    async def run(self, job_id, prompt):
        result = ExecutionResult()
        try:
            async with self._lock:
                self._pending_llm = self.owner.model(self.target)
                self._host = SubagentHost(
                    self.owner.config.delegation[self.target].config,
                    self.owner.config.workspace,
                    JobStore(),
                    self.history,
                    llm=self._pending_llm,
                )
                self._pending_llm = None
                self._starting_task = asyncio.current_task()
                try:
                    await self._host.start(job_id, prompt)
                finally:
                    self._starting_task = None
                self.state = "running"
            turn = await self._host.wait()
            result.output, result.error = turn.output, turn.error
            result.metadata.update(
                turns=turn.turns,
                duration_s=turn.duration,
                usage={
                    "total_tokens": turn.total_tokens,
                    "prompt_tokens": turn.prompt_tokens,
                    "completion_tokens": turn.completion_tokens,
                },
            )
            result.state = (
                JobState.CANCELLED
                if turn.cancelled or turn.interrupted
                else (JobState.DONE if turn.success else JobState.ERROR)
            )
            self.history.append("result", text=result.output, job_id=job_id)
        except asyncio.CancelledError:
            result.state, result.error = JobState.CANCELLED, "Delegation cancelled"
        except Exception as exc:
            result.state, result.error = JobState.ERROR, str(exc)
            self.state = "error"
        finally:
            if self._host is not None or self._pending_llm is not None:
                cleanup = asyncio.create_task(self.close())
                try:
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            result.state, result.error = (
                                JobState.CANCELLED,
                                "Delegation cancelled",
                            )
                    cleanup.result()
                except Exception as exc:
                    result.state, result.error = (
                        JobState.ERROR,
                        f"Resource cleanup failed: {exc}",
                    )
                if self._host is not None:
                    self.state = "completed"
        return result

    async def stop(self):
        task, self._starting_task = self._starting_task, None
        _cancel_startup(task)
        async with self._lock:
            if self._host is not None:
                await self._host.cancel()
            elif self.state == "starting":
                self.state = "stopped"

    async def close(self):
        async with self._lock:
            if self._host is not None:
                await self._host.close()
            elif self._pending_llm is not None:
                await self._pending_llm.close()
                self._pending_llm = None

    async def send(self, content):
        return bool(self._host and await self._host.send(content))

    def conversation(self):
        return self._host.conversation() or [] if self._host else []


SessionExecution = CreatureExecution | SubagentExecution
