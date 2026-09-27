"""MCP admission and ownership routing over KT jobs, creatures and subagents."""

import asyncio
import uuid
from dataclasses import dataclass

from kohakuterrarium.core.job import JobResult, JobState, JobStatus
from kohakuterrarium.mcp_server.delegation_history import DelegationHistory
from kohakuterrarium.mcp_server.delegation_adapters import (
    DelegationAdapters,
    SessionExecution,
)


@dataclass
class DelegatedSession:
    """A server-owned conversation handle and its current runtime owner."""

    session_id: str
    target: str
    kind: str
    history: DelegationHistory
    execution: SessionExecution
    closed: bool = False
    active_job: str | None = None
    closing: int = 0
    stopping: int = 0


class DelegationRuntime:
    """Own delegated instances for one MCP server lifetime."""

    def __init__(self, config, store, instance_id, *, llm_factory=None):
        self.config = config
        self.store = store
        self.instance_id = instance_id
        self.adapters = DelegationAdapters(config, instance_id, llm_factory=llm_factory)
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
        return "closed" if session.closed else session.execution.state

    def _busy(self, session):
        return bool(
            session.closing
            or session.stopping
            or session.active_job
            or session.execution.busy
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
            if not session.execution.can_continue:
                raise ValueError("Subagent sessions are one-shot")
            if session.closed:
                raise ValueError("Session is closed")
            if self._busy(session):
                return {
                    "error": "Session is busy",
                    "session_id": session_id,
                    "job_id": session.active_job,
                }
        else:
            session_id = f"{self.instance_id}_session_{uuid.uuid4().hex}"
            history = DelegationHistory()
            session = DelegatedSession(
                session_id,
                target,
                self.config.delegation[target].kind,
                history,
                self.adapters.create(target, history),
            )
            self._sessions[session_id] = session
        job_id = f"{self.instance_id}_{session.kind}_{uuid.uuid4().hex}"
        session.active_job = job_id
        session.history.append("input", text=prompt, job_id=job_id)
        self.store.register(
            JobStatus(
                job_id,
                session.execution.job_type,
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

    async def _execute(self, session, job_id, prompt):
        output, error = "", None
        metadata = {"session_id": session.session_id, "kind": session.kind}
        state = JobState.DONE
        try:
            self.store.update_status(job_id, state=JobState.RUNNING)
            result = await session.execution.run(job_id, prompt)
            output, error, state = result.output, result.error, result.state
            metadata.update(result.metadata)
        except asyncio.CancelledError:
            state, error = JobState.CANCELLED, "Delegation cancelled"
        except Exception as exc:
            state, error = JobState.ERROR, str(exc)
        finally:
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
            await session.execution.stop()
            task = self._tasks[job_id]
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if session.active_job == job_id:
                session.active_job = None
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
        accepted = await session.execution.send(content)
        if accepted:
            session.history.append(
                "input", text=content, job_id=job_id, supplemental=True
            )
        return accepted

    def history(self, session_id, *, cursor=0, limit=100, view="events"):
        session = self._session(session_id)
        if view == "conversation":
            session.history.page(cursor, limit)
            messages = session.execution.conversation()
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
        session.closing += 1
        return await self._control(self._close_session(session))

    async def _close_session(self, session):
        try:
            if session.closed:
                return
            if session.active_job:
                await self._cancel(session.active_job)
            await session.execution.close()
            session.closed = True
        finally:
            session.closing -= 1

    async def close(self):
        self._closing = True
        if self._controls:
            await asyncio.gather(*self._controls, return_exceptions=True)
        error = None
        for session in self._sessions.values():
            session.closing += 1
            try:
                await self._close_session(session)
            except Exception as exc:
                error = error or exc
        try:
            await self.adapters.close()
        except Exception as exc:
            error = error or exc
        if error is not None:
            raise error
