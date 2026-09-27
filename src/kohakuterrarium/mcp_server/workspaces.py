"""Persistent registrations and lazily loaded, independently owned runtimes."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from kohakuterrarium.mcp_server.config import GlobalToolsConfig, MCPToolsConfig
from kohakuterrarium.mcp_server.records import workspace_identity, write_json
from kohakuterrarium.mcp_server.runtime import ToolRuntime
from kohakuterrarium.utils.file_lock import FileLock


class WorkspaceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    path: str
    registration_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class WorkspaceRegistry:
    """Atomic registration records; names are independent of directory identity."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = FileLock(path.with_suffix(".lock"))

    def read(self) -> dict[str, WorkspaceRegistration]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("Invalid workspace registry")
        values = [WorkspaceRegistration.model_validate(v) for v in raw["workspaces"]]
        result = {v.workspace_id: v for v in values}
        if len(result) != len(values):
            raise ValueError("Duplicate workspace registration")
        return result

    def _save(self, entries):
        write_json(
            self.path,
            {"version": 1, "workspaces": [v.model_dump() for v in entries.values()]},
        )

    def add(self, name: str, path: Path) -> WorkspaceRegistration:
        if not path.expanduser().is_dir():
            raise ValueError("workspace must be an existing directory")
        entry = WorkspaceRegistration(workspace_id=name, path=workspace_identity(path))
        with self.lock:
            entries = self.read()
            if name in entries:
                raise ValueError("Workspace name already registered")
            entries[name] = entry
            self._save(entries)
        return entry

    def remove(self, name: str, registration_id: str):
        with self.lock:
            entries = self.read()
            if name not in entries or entries[name].registration_id != registration_id:
                raise ValueError("Workspace registration changed")
            del entries[name]
            self._save(entries)


class WorkspacePool:
    """Route each admitted call to one registration's immutable execution binding."""

    def __init__(
        self,
        config: GlobalToolsConfig,
        registry: WorkspaceRegistry,
        *,
        llm_factory=None,
    ):
        self.config = config.model_copy(deep=True)
        self.registry = registry
        self.llm_factory = llm_factory
        self.instance_id = uuid.uuid4().hex
        self._runtimes = {}
        self._locks = {}
        self._calls = {}
        self._requests = set()
        self._errors = {}
        self._draining = set()
        self._closed = False

    async def __aenter__(self):
        self.registry.read()
        return self

    async def __aexit__(self, *_):
        self._closed = True
        tasks = set(self._requests)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        results = await asyncio.gather(
            *(r.__aexit__(None, None, None) for r in self._runtimes.values()),
            return_exceptions=True,
        )
        self._runtimes.clear()
        for result in results:
            if isinstance(result, BaseException):
                raise result

    def _entry(self, name):
        entry = self.registry.read().get(name)
        if entry is None:
            raise ValueError(
                "Unknown workspace; call workspaces to discover registered names"
            )
        return entry

    def list(self):
        return [
            {
                **entry.model_dump(),
                "state": (
                    "draining"
                    if entry.registration_id in self._draining
                    else (
                        "ready"
                        if entry.registration_id in self._runtimes
                        else (
                            "unavailable"
                            if entry.registration_id in self._errors
                            else "unloaded"
                        )
                    )
                ),
                "error": self._errors.get(entry.registration_id),
            }
            for entry in self.registry.read().values()
        ]

    @asynccontextmanager
    async def use(self, name: str):
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            async with self._use(name) as runtime:
                yield runtime
        finally:
            self._requests.discard(task)

    @asynccontextmanager
    async def _use(self, name: str):
        entry = self._entry(name)
        key = entry.registration_id
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._closed or key in self._draining:
                raise ValueError("Workspace is stopping")
            if self._entry(name).registration_id != key:
                raise ValueError(
                    "Workspace registration changed; rediscover workspaces"
                )
            if key not in self._runtimes:
                try:
                    config = MCPToolsConfig(
                        **self.config.model_dump(), workspace=Path(entry.path)
                    )
                    runtime = ToolRuntime(config, llm_factory=self.llm_factory)
                    await runtime.__aenter__()
                    self._runtimes[key] = runtime
                    self._errors.pop(key, None)
                except Exception as exc:
                    self._errors[key] = str(exc)
                    raise
            runtime = self._runtimes[key]
            task = asyncio.current_task()
            self._calls.setdefault(key, set()).add(task)
        try:
            yield runtime
        finally:
            self._calls[key].discard(task)

    async def remove(self, name: str, *, force: bool = False):
        entry = self._entry(name)
        key = entry.registration_id
        async with self._locks.setdefault(key, asyncio.Lock()):
            runtime = self._runtimes.get(key)
            calls = set(self._calls.get(key, ()))
            busy = calls or (
                runtime
                and (
                    runtime.executor.get_running_jobs()
                    or any(
                        s["state"] != "closed" for s in runtime.delegation.sessions()
                    )
                )
            )
            if busy and not force:
                raise ValueError(
                    "Workspace is busy; finish jobs and close sessions, or use --force"
                )
            self._draining.add(key)
            try:
                for task in calls:
                    task.cancel()
                await asyncio.gather(*calls, return_exceptions=True)
                if runtime:
                    await runtime.__aexit__(None, None, None)
                    self._runtimes.pop(key, None)
                self.registry.remove(name, key)
                self._errors.pop(key, None)
            except BaseException:
                raise
            else:
                self._draining.discard(key)
