"""MCP admission and ownership routing over KT jobs, creatures and subagents."""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.job import JobResult, JobState, JobStatus, JobStore, JobType
from kohakuterrarium.mcp_server.delegation_history import DelegationHistory
from kohakuterrarium.mcp_server.subagent_host import SubagentHost
from kohakuterrarium.studio.studio import Studio
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir


@dataclass
class DelegatedSession:
    """A server-owned conversation handle and its current runtime owner."""

    session_id: str
    target: str
    kind: str
    history: DelegationHistory = field(default_factory=DelegationHistory)
    creature: Any = None
    subagent: Any = None
    saved_path: Path | None = None
    state: str = "starting"
    active_job: str | None = None
    closing: bool = False
    stopping: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DelegationRuntime:
    """Own delegated instances for one MCP server lifetime."""

    def __init__(self, config, store, instance_id, *, llm_factory=None):
        self.config = config
        self.store = store
        self.instance_id = instance_id
        self.llm_factory = llm_factory
        self.studio = None
        self._sessions: dict[str, DelegatedSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._owners: dict[str, DelegatedSession] = {}
        self._cancelling: set[str] = set()
        self._controls: set[asyncio.Task] = set()
        self._closing = False

    def targets(self):
        return [
            {"name": name, "kind": target.kind, "description": target.description}
            for name, target in self.config.delegation.items()
        ]

    def _session(self, session_id):
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown session")
        return session

    def sessions(self):
        return [
            {
                "session_id": s.session_id,
                "target": s.target,
                "kind": s.kind,
                "state": self._state(s),
                "job_id": s.active_job,
            }
            for s in self._sessions.values()
        ]

    def _state(self, session):
        if self._busy(session):
            return "busy"
        if session.state == "running" and session.creature is not None:
            return "paused" if session.creature.paused else session.creature.status
        return session.state

    def _busy(self, session):
        return bool(
            session.closing
            or session.stopping
            or session.active_job
            or (session.creature is not None and session.creature.agent.is_processing)
        )

    async def submit(self, target: str, prompt: str, *, session_id: str | None = None):
        if self._closing:
            raise RuntimeError("Delegation runtime is closing")
        if target not in self.config.delegation:
            raise ValueError("Unknown target")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must not be empty")
        if session_id is not None:
            session = self._session(session_id)
            if session.target != target:
                raise ValueError("Session belongs to another target")
            if session.kind == "subagent":
                raise ValueError("Subagent sessions are one-shot")
            if session.state == "closed":
                raise ValueError("Session is closed")
            if self._busy(session) or session.lock.locked():
                return {
                    "error": "Session is busy",
                    "session_id": session_id,
                    "job_id": session.active_job,
                }
        else:
            session_id = f"{self.instance_id}_session_{uuid.uuid4().hex}"
            session = DelegatedSession(
                session_id, target, self.config.delegation[target].kind
            )
            self._sessions[session_id] = session
        job_id = f"{self.instance_id}_{session.kind}_{uuid.uuid4().hex}"
        session.active_job = job_id
        session.history.append("input", text=prompt, job_id=job_id)
        self.store.register(
            JobStatus(
                job_id,
                JobType.CREATURE if session.kind == "creature" else JobType.SUBAGENT,
                target,
                context={"session_id": session_id, "kind": session.kind},
            )
        )
        self._owners[job_id] = session
        self._tasks[job_id] = asyncio.create_task(
            self._execute(session, job_id, prompt)
        )
        self._prune()
        return {"job_id": job_id, "session_id": session_id, "kind": session.kind}

    def owns(self, job_id):
        return job_id in self._owners and self.store.get_status(job_id) is not None

    def _prune(self):
        for job_id in list(self._owners):
            if self.store.get_status(job_id) is None:
                self._owners.pop(job_id, None)
                self._tasks.pop(job_id, None)

    async def _creature(self, session):
        if self.studio is None:
            self.studio = Studio(engine=Terrarium())
            await self.studio.__aenter__()
        engine = self.studio.engine
        llm = self.llm_factory(session.target) if self.llm_factory else None
        if session.saved_path is not None:
            graph = await engine.adopt_session(str(session.saved_path), llm=llm)
            creatures = [c for c in engine.list_creatures() if c.graph_id == graph]
            session.creature = next(
                c for c in creatures if c.name == session.creature.name
            )
        else:
            path = (
                config_dir()
                / "mcp-serve"
                / "sessions"
                / self.instance_id
                / f"{uuid.uuid4().hex}.kohakutr"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            session.creature = await engine.add_creature(
                self.config.delegation[session.target].config,
                llm=llm,
                pwd=str(self.config.workspace),
                io="headless",
                session=path,
                start=False,
            )
            session.saved_path = path
            session.creature.agent.output_router.add_secondary(session.history)
            await session.creature.start()
            return
        session.creature.agent.output_router.add_secondary(session.history)

    async def _execute(self, session, job_id, prompt):
        output, error, metadata = (
            "",
            None,
            {"session_id": session.session_id, "kind": session.kind},
        )
        state = JobState.DONE
        try:
            async with session.lock:
                self.store.update_status(job_id, state=JobState.RUNNING)
                if session.kind == "creature":
                    if session.creature is None or session.state == "stopped":
                        await self._creature(session)
                else:
                    llm = self.llm_factory(session.target) if self.llm_factory else None
                    session.subagent = SubagentHost(
                        self.config.delegation[session.target].config,
                        self.config.workspace,
                        # Like a Creature, the child owns its internal jobs. The
                        # MCP job settles only after execution and cleanup finish.
                        JobStore(),
                        session.history,
                        llm=llm,
                    )
                    await session.subagent.start(job_id, prompt)
                session.state = "running"
            if session.kind == "creature":
                if session.creature.agent.is_processing:
                    raise ValueError("Session is busy with autonomous activity")
                event = create_user_input_event(
                    prompt, source="mcp", correlation_id=job_id
                )
                event.stackable = False
                result = await session.creature.inject_event(event)
                output, error = result.text, result.error
                metadata.update(
                    status=result.status,
                    usage=result.usage,
                    duration_s=result.duration_s,
                )
                if result.status == "interrupted":
                    state = JobState.CANCELLED
                elif not result.ok:
                    state, error = JobState.ERROR, error or result.status
            else:
                result = await session.subagent.wait()
                output, error = result.output, result.error
                metadata.update(
                    turns=result.turns,
                    duration_s=result.duration,
                    usage={
                        "total_tokens": result.total_tokens,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                    },
                )
                state = (
                    JobState.CANCELLED
                    if result.cancelled or result.interrupted
                    else (JobState.DONE if result.success else JobState.ERROR)
                )
                session.history.append("result", text=output, job_id=job_id)
        except asyncio.CancelledError:
            state, error = JobState.CANCELLED, "Delegation cancelled"
        except Exception as exc:
            state, error = JobState.ERROR, str(exc)
            if session.state == "starting":
                if session.creature is not None:
                    try:
                        await self._stop(session)
                    except Exception as cleanup:
                        error += f"; resource cleanup failed: {cleanup}"
                else:
                    session.state = "error"
        finally:
            if session.subagent:
                try:
                    await session.subagent.close()
                except Exception as exc:
                    state, error = JobState.ERROR, f"Resource cleanup failed: {exc}"
                session.state = "completed"
            if job_id in self._cancelling:
                state, error = JobState.CANCELLED, "Delegation cancelled"
            if session.active_job == job_id:
                session.active_job = None
            self.store.update_status(job_id, state=state, error=error)
            self.store.store_result(
                JobResult(job_id, output=output, error=error, metadata=metadata)
            )
            session.history.append(
                "job_end", job_id=job_id, state=state.value, error=error
            )

    async def wait(self, job_id, timeout):
        task = self._tasks.get(job_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def _stop(self, session):
        async with session.lock:
            if session.creature is not None and session.state not in {
                "stopped",
                "closed",
            }:
                await self.studio.sessions.stop(session.creature.graph_id)
                session.state = "stopped"
            if session.subagent is not None:
                await session.subagent.cancel()

    async def _control(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._controls.add(task)
        task.add_done_callback(self._controls.discard)
        return await asyncio.shield(task)

    async def cancel(self, job_id):
        return await self._control(self._cancel(job_id))

    async def _cancel(self, job_id):
        status = self.store.get_status(job_id)
        if not status or status.is_complete:
            return False
        session = self._owners[job_id]
        self._cancelling.add(job_id)
        session.stopping += 1
        try:
            await self._stop(session)
            task = self._tasks[job_id]
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if session.active_job == job_id:
                session.active_job = None
            if session.state == "starting":
                session.state = "stopped"
            self.store.update_status(
                job_id, state=JobState.CANCELLED, error="Delegation cancelled"
            )
            if self.store.get_result(job_id) is None:
                self.store.store_result(
                    JobResult(
                        job_id,
                        error="Delegation cancelled",
                        metadata={
                            "session_id": session.session_id,
                            "kind": session.kind,
                        },
                    )
                )
            return True
        finally:
            session.stopping -= 1
            self._cancelling.discard(job_id)

    async def send(self, job_id, content):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")
        if not self.owns(job_id):
            raise ValueError("Unknown delegation job")
        session = self._owners[job_id]
        if session.active_job != job_id or job_id in self._cancelling:
            return False
        if session.kind == "subagent":
            accepted = bool(session.subagent and await session.subagent.send(content))
        else:
            accepted = (
                session.creature is not None and session.creature.agent.is_processing
            )
            if accepted:
                await session.creature.inject_input(content, source="mcp_feedback")
        if accepted:
            session.history.append(
                "input", text=content, job_id=job_id, supplemental=True
            )
        return accepted

    def history(self, session_id, *, cursor=0, limit=100, view="events"):
        session = self._session(session_id)
        if view == "conversation":
            session.history.page(cursor, limit)
            if session.creature:
                messages = session.creature.agent.controller.conversation.to_messages()
            elif session.subagent:
                messages = session.subagent.conversation() or []
            else:
                messages = []
            public = []
            for message in messages[cursor : cursor + limit]:
                data = message if isinstance(message, dict) else message.to_dict()
                public.append(
                    {
                        key: value
                        for key, value in data.items()
                        if key
                        in {"role", "content", "tool_calls", "tool_call_id", "name"}
                    }
                )
            return {
                "session_id": session_id,
                "messages": public,
                "next_cursor": cursor + len(public),
                "has_more": cursor + len(public) < len(messages),
            }
        if view != "events":
            raise ValueError("Unknown history view")
        return {"session_id": session_id, **session.history.page(cursor, limit)}

    async def close_session(self, session_id):
        session = self._session(session_id)
        session.closing = True
        return await self._control(self._close_session(session))

    async def _close_session(self, session):
        try:
            if session.state == "closed":
                return
            if session.active_job:
                await self._cancel(session.active_job)
            else:
                await self._stop(session)
            session.state = "closed"
        finally:
            session.closing = False

    async def close(self):
        self._closing = True
        if self._controls:
            await asyncio.gather(*self._controls, return_exceptions=True)
        error = None
        for session in self._sessions.values():
            try:
                await self._close_session(session)
            except Exception as exc:
                error = error or exc
        if self.studio is not None:
            try:
                await self.studio.__aexit__(None, None, None)
            except Exception as exc:
                error = error or exc
        if error is not None:
            raise error
