"""User-audience side conversations without parent turn delivery."""

import asyncio
from typing import Any

from kohakuterrarium.errors import ConflictError
from kohakuterrarium.core.conversation.context_snapshot import capture_context
from kohakuterrarium.core.conversation.conversation import Conversation


def side_title(task: str) -> str:
    """Immediate bounded label while the session summary service refines it."""
    text = " ".join(task.split()).strip("\"'`")
    if len(text) > 80:
        text = (text[:79].rsplit(" ", 1)[0] or text[:79]).rstrip(" ,;:") + "…"
    return text or "By the way"


class SideConversationMixin:
    """Start, reopen, and continue retained private side transcripts."""

    def _check_side_enabled(self) -> None:
        host = self._host_agent
        if host is not None and not host.config.fork_enabled:
            raise ValueError(
                "Context forks and /btw are disabled (fork_enabled: false)"
            )
        if self.get_config("fork") is None:
            raise ValueError("The fork sub-agent is unavailable")

    def side_conversation(self, job_id: str) -> tuple[Conversation, dict[str, Any]]:
        """Resolve only user-audience runs belonging to this parent."""
        job = self._jobs.get(job_id)
        if job is not None:
            child = job.subagent
            if child._audience != "user":
                raise ValueError("Not a /btw conversation")
            saved = (
                self._session_store.load_subagent_meta(
                    self._parent_name, child.config.name, child._run_index
                )
                if self._session_store
                else None
            ) or {}
            result = self._results.get(job_id)
            task = self._tasks.get(job_id)
            status = (
                "running"
                if task is not None and not task.done()
                else (
                    "cancelled"
                    if result and (result.cancelled or result.interrupted)
                    else "error" if result and not result.success else "done"
                )
            )
            return child.conversation, {
                "title": saved.get("title")
                or getattr(child, "_side_title", "By the way"),
                "status": status,
                "side_turn": getattr(child, "_side_turn", 1),
                "error": result.error if result else None,
                "job_id": job_id,
                "name": child.config.name,
                "run": child._run_index,
                "audience": "user",
                "inherited_message_count": child._inherited_message_count,
                "source": child._snapshot.provenance if child._snapshot else {},
                **self._side_usage(child),
            }
        store = self._session_store
        row = (
            store.find_subagent_run(job_id, parent=self._parent_name) if store else None
        )
        if row is None or (row.get("meta") or {}).get("audience") != "user":
            raise ValueError("Side conversation not found for this creature")
        raw = store.load_subagent_conversation(row["parent"], row["name"], row["run"])
        if not raw:
            raise ValueError("Side conversation has no saved context")
        return Conversation.from_json(raw), {
            **row["meta"],
            "name": row["name"],
            "run": row["run"],
        }

    async def start_side(self, task: str) -> str:
        self._check_side_enabled()
        job_id = await self.spawn("fork", task, audience="user")
        self._notify_side(
            "btw_started",
            "Side conversation started",
            {"job_id": job_id, "audience": "user"},
        )
        return job_id

    async def continue_side(self, job_id: str, task: str) -> str:
        self._check_side_enabled()
        lock = self._side_locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            running = self._tasks.get(job_id)
            if running is not None and not running.done():
                raise ConflictError(
                    "Side conversation is still running; wait or stop it before following up"
                )
            conversation, meta = self.side_conversation(job_id)
            snapshot = capture_context(
                conversation, provenance=meta.get("source") or {}
            )
            self._results.pop(job_id, None)
            resumed_id = await self.spawn(
                "fork",
                task,
                job_id=job_id,
                snapshot=snapshot,
                audience="user",
                continuation=True,
                run_index=meta["run"],
                inherited_message_count=meta.get("inherited_message_count", 0),
            )
            self._jobs[resumed_id].subagent._side_title = meta.get(
                "title"
            ) or side_title(task)
            self._jobs[resumed_id].subagent._side_turn = meta.get("side_turn", 1) + 1
            self._jobs[resumed_id].subagent._usage_offset = {
                key: meta.get(key, 0)
                for key in (
                    "total_tokens",
                    "prompt_tokens",
                    "completion_tokens",
                    "cached_tokens",
                )
            }
            return resumed_id

    def _notify_side(self, kind: str, text: str, metadata: dict) -> None:
        router = getattr(self._host_agent, "output_router", None)
        if router is not None:
            router.notify_activity(kind, text, metadata=metadata)

    @staticmethod
    def _side_usage(child: Any) -> dict[str, int]:
        return {
            key: child._usage_offset.get(key, 0) + getattr(child, "_" + key, 0)
            for key in (
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
            )
        }

    def _persist_side(self, job_id: str, child: Any, result: Any = None) -> dict:
        saved = (
            self._session_store.load_subagent_meta(
                self._parent_name, child.config.name, child._run_index
            )
            if self._session_store
            else None
        ) or {}
        meta = {
            "title": saved.get("title")
            or getattr(child, "_side_title", side_title(child._task_text)),
            "title_source": saved.get("title_source", "heuristic"),
            "title_at_turn": saved.get("title_at_turn", 0),
            "side_turn": getattr(child, "_side_turn", 1),
            "job_id": job_id,
            "name": child.config.name,
            "audience": "user",
            "run": child._run_index,
            "task": child._task_text,
            "inherited_message_count": child._inherited_message_count,
            "source": child._snapshot.provenance if child._snapshot else {},
            "success": result.success if result else None,
            "error": result.error if result else None,
            "status": (
                "running"
                if result is None
                else (
                    "cancelled"
                    if result.cancelled or result.interrupted
                    else "done" if result.success else "error"
                )
            ),
            "turns": child._turns,
            "duration": child._calculate_duration(),
            **self._side_usage(child),
        }
        if self._session_store:
            self._session_store.save_subagent(
                parent=self._parent_name,
                name=child.config.name,
                run=child._run_index,
                meta=meta,
                conv_json=child.conversation.to_json(),
            )
        return meta

    def _finish_side(self, job_id: str, child: Any, result: Any) -> None:
        if result is None:
            return
        meta = self._persist_side(job_id, child, result)
        self._notify_side(
            "btw_result",
            result.output or result.error or "Side conversation finished",
            meta,
        )
