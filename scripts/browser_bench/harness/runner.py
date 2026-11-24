"""Run one task through a real agent with the ``browser`` plugin and turn it into a record.

Each run gets its own ``BenchSite`` and working directory, an agent built
with the browser plugin (only the site's ``main`` and ``third`` ports
allowed) plus the ``read`` tool, one user turn under a wall-clock and a
tool-call limit, then the check over the site's recorded state.
"""

import asyncio
import json
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium import Agent
from kohakuterrarium.core.config.types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    ToolConfigItem,
)
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    ToolVisibility,
)

from ..model import CheckResult, Task, check
from ..oracles.bot import blocked_in
from ..site import BenchSite
from .memory import free_memory_gb
from .score import PASSING, egress_summary, failure_reason, judge_run
from .store import RunStore
from .usage import CallLog, instrument, run_totals
from .verdict import SYSTEM_PROMPT, parse_verdict, user_message

BROWSER_TOOLS = (
    "browser_navigate",
    "browser_snapshot",
    "browser_act",
    "browser_screenshot",
    "browser_tabs",
    "browser_eval",
)
ALLOWED_TOOLS = frozenset({"read", *BROWSER_TOOLS})
LIMIT_MESSAGE = (
    "Tool-call limit for this request reached; no more tools will run. "
    "Give your final answer now."
)
BLOCKED_BEFORE_INTERRUPT = 3
HARNESS_RETRIES = 1
HARNESS_GRACE_S = 120.0
STOP_TIMEOUT_S = 60.0
RESULT_KEEP = 600
LAUNCH_MARKERS = (
    "executable doesn't exist",
    "failed to launch",
    "browser unavailable",
    "playwright is not installed",
    "browsertype.launch",
)


@dataclass(frozen=True)
class Limits:
    """Per-run settings: tool-call and wall-clock limits, and the prompt-cache key mode.

    ``per_run_cache_key`` gives each run's provider its own ``prompt_cache_key``
    instead of the default derived from the system prompt (shared by every run).
    """

    max_tool_calls: int
    timeout_s: float
    per_run_cache_key: bool = False


def browser_options(site: Any) -> dict[str, Any]:
    """Plugin options: headless, loopback on, only the site's main and third ports."""
    return {
        "headless": True,
        "allow_loopback": True,
        "allow_hosts": [
            f"127.0.0.1:{site.port('main')}",
            f"127.0.0.1:{site.port('third')}",
        ],
    }


def agent_config(workdir: Path, options: Mapping[str, Any]) -> AgentConfig:
    """The benchmark creature: neutral prompt, ``read`` plus the browser plugin."""
    return AgentConfig(
        name="browser-bench",
        system_prompt=SYSTEM_PROMPT,
        tools=[ToolConfigItem(name="read")],
        plugins=[{"name": "browser", "options": dict(options)}],
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
        fork_enabled=False,
        agent_path=workdir,
        tool_format="native",
    )


def action_count(args: Mapping[str, Any]) -> int:
    """Actions in one ``browser_act`` call, whether given as a list, an object or JSON."""
    actions = args.get("actions")
    if isinstance(actions, str):
        try:
            actions = json.loads(actions)
        except ValueError:
            return 0
    if isinstance(actions, dict):
        return 1
    return len(actions) if isinstance(actions, list) else 0


def tally(calls: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Executed tool calls per tool, ``browser_act`` actions, blocked and failed calls."""
    calls = list(calls)
    ran = [c for c in calls if not c.get("blocked")]
    return {
        "total": len(ran),
        "by_tool": dict(sorted(Counter(c["tool"] for c in ran).items())),
        "act_actions": sum(
            action_count(c.get("args") or {}) for c in ran if c["tool"] == "browser_act"
        ),
        "blocked": len(calls) - len(ran),
        "errors": sum(bool(c.get("error")) for c in ran),
    }


def sum_usage(usages: Iterable[Mapping[str, Any]]) -> dict[str, int | None]:
    """Token totals over LLM calls; ``reasoning`` stays ``None`` unless a call reported it."""
    usages = list(usages)
    reasoning = [u["reasoning_tokens"] for u in usages if "reasoning_tokens" in u]
    return {
        "input": sum(int(u.get("prompt_tokens") or 0) for u in usages),
        "cached_input": sum(int(u.get("cached_tokens") or 0) for u in usages),
        "output": sum(int(u.get("completion_tokens") or 0) for u in usages),
        "reasoning": sum(int(r or 0) for r in reasoning) if reasoning else None,
    }


def launch_failure(calls: Iterable[Mapping[str, Any]], *, requests: int) -> str | None:
    """A browser-launch error text, when no request ever reached the site."""
    if requests:
        return None
    for c in calls:
        text = str(c.get("result", ""))
        if c["tool"].startswith("browser_") and any(
            marker in text.lower() for marker in LAUNCH_MARKERS
        ):
            return text.strip().splitlines()[0][:300] if text.strip() else c["tool"]
    return None


def _scrub_part(part: Any) -> Any:
    if isinstance(part, dict) and part.get("type") == "image_url":
        url = str((part.get("image_url") or {}).get("url", ""))
        return {"type": "image", "omitted_chars": len(url)}
    return part


def scrub_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A copy of ``messages`` with inline image data replaced by its size."""
    out = []
    for msg in messages:
        copy = dict(msg)
        if isinstance(copy.get("content"), list):
            copy["content"] = [_scrub_part(p) for p in copy["content"]]
        out.append(copy)
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") if p.get("type") == "text" else f"[{p.get('type')}]"
            for p in content
            if isinstance(p, dict)
        )
    return "" if content is None else str(content)


def final_assistant_text(messages: list[Mapping[str, Any]]) -> str:
    """Text of the last assistant message."""
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return _text_of(msg.get("content"))
    return ""


def render_transcript(messages: Iterable[Mapping[str, Any]]) -> str:
    """The conversation as the model saw it, as readable markdown."""
    lines: list[str] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        name = f" `{msg['name']}`" if msg.get("name") else ""
        lines += [f"### {i}. {role}{name}", "", "````", _text_of(msg.get("content"))]
        for call in msg.get("tool_calls") or []:
            fn = call.get("function", call)
            lines.append(f">> call {fn.get('name')} {fn.get('arguments')}")
        lines += ["````", ""]
    return "\n".join(lines)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if hasattr(result, "get_text_output"):
        text = result.get_text_output()
    else:
        text = str(getattr(result, "output", result))
    error = getattr(result, "error", None)
    return f"{error}\n{text}" if error and error not in text else text


class BenchHooks(BasePlugin):
    """Harness hooks: tool catalog, tool-call limit, call log and LLM usage of one run."""

    name = "bench_harness"
    priority = 1

    def __init__(self, max_tool_calls: int) -> None:
        super().__init__()
        self.max_tool_calls = max_tool_calls
        self.calls: list[dict[str, Any]] = []
        self.usages: list[dict[str, Any]] = []
        self.egress: list[dict[str, Any]] = []
        self.limit_hit = False
        self.agent: Any = None
        self.log = CallLog()
        self._open: dict[Any, dict[str, Any]] = {}

    def get_tool_visibility(self, context: Any) -> ToolVisibility:
        return ToolVisibility(
            allowed_tools=ALLOWED_TOOLS, allowed_subagents=frozenset()
        )

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        entry = {
            "tool": kwargs.get("tool_name", "?"),
            "args": _jsonable(args),
            "blocked": False,
            "error": False,
            "result": "",
        }
        ran = sum(not c["blocked"] for c in self.calls)
        self.calls.append(entry)
        if ran >= self.max_tool_calls:
            entry["blocked"] = True
            self.limit_hit = True
            blocked = sum(c["blocked"] for c in self.calls)
            if blocked > BLOCKED_BEFORE_INTERRUPT and self.agent is not None:
                asyncio.get_running_loop().call_soon(self.agent.interrupt)
            raise PluginBlockError(LIMIT_MESSAGE)
        self._open[kwargs.get("job_id")] = entry
        return None

    async def post_tool_execute(self, result: Any, **kwargs: Any) -> Any | None:
        entry = self._open.pop(kwargs.get("job_id"), None)
        if entry is not None:
            text = _result_text(result)
            entry["error"] = bool(getattr(result, "error", None))
            entry["result"] = text[:RESULT_KEEP]
            if entry["tool"].startswith("browser_"):
                self.egress.extend(blocked_in(text))
        return None

    async def pre_llm_call(
        self, messages: list[dict], **kwargs: Any
    ) -> list[dict] | None:
        self.log.begin(list(messages), kwargs.get("tools"))
        return None

    async def post_llm_call(
        self, messages: list[dict], response: str, usage: dict, **kwargs: Any
    ) -> str | None:
        self.usages.append(dict(usage or {}))
        self.log.end(usage)
        return None


@dataclass
class Drive:
    """What driving the agent produced, before judging."""

    status: str = "not_started"
    error: str | None = None
    wall_s: float = 0.0
    messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    instrumented: bool = False


def _quiet_skills(agent: Any) -> None:
    """Keep host-discovered skills out of the benchmark creature's prompt."""
    skills = getattr(agent, "skills", None)
    for skill in skills.all() if skills is not None else []:
        skills.disable(skill.name)
    agent.refresh_system_prompt()


async def drive_agent(
    config: AgentConfig,
    llm: Any,
    workdir: Path,
    message: str,
    hooks: BenchHooks,
    limits: Limits,
) -> Drive:
    """Build, start, run one turn, read the conversation, stop."""
    out = Drive()
    agent = await Agent.build(config, llm=llm, pwd=str(workdir), plugins=[hooks])
    hooks.agent = agent
    out.instrumented = instrument(getattr(agent, "llm", None), hooks.log)
    if limits.per_run_cache_key and hasattr(agent.llm, "prompt_cache_key"):
        agent.llm.prompt_cache_key = uuid.uuid4().hex
    try:
        await agent.start()
        _quiet_skills(agent)
        out.system_prompt = agent.get_system_prompt()
        started = time.monotonic()
        result = await agent.run(
            message, timeout=limits.timeout_s, raise_on_error=False
        )
        out.wall_s = time.monotonic() - started
        out.status, out.error = result.status, result.error
        out.messages = agent.controller.conversation.to_messages()
    finally:
        try:
            await asyncio.wait_for(agent.stop(), STOP_TIMEOUT_S)
        except Exception as exc:
            out.error = out.error or f"stop failed: {type(exc).__name__}: {exc}"
    return out


def _tokens(hooks: BenchHooks) -> dict[str, int | None]:
    """Run totals from provider usage per call when recorded, else the controller's."""
    if any(c.get("usage") and "source" not in c["usage"] for c in hooks.log.calls):
        return run_totals(hooks.log.calls)
    return sum_usage(hooks.usages)


def _check_dict(result: CheckResult) -> dict[str, Any]:
    return _jsonable(asdict(result))


def _harness_error(drive: Drive, hooks: BenchHooks, requests: int) -> str | None:
    match drive.status:
        case "error":
            return f"turn error: {drive.error or 'unknown'}"
        case "rejected" | "not_started":
            return f"turn {drive.status}: {drive.error or ''}".strip()
    launch = launch_failure(hooks.calls, requests=requests)
    return f"browser launch: {launch}" if launch else None


def _limit(drive: Drive, hooks: BenchHooks) -> str | None:
    if drive.status == "timeout":
        return "wall"
    return "tool_calls" if hooks.limit_hit else None


def write_files(task: Task, workdir: Path) -> dict[str, str]:
    """Write the task's fixture files into ``workdir``; their absolute paths by name."""
    paths = {}
    for name, content in task.files.items():
        path = workdir / name
        path.write_bytes(content)
        paths[name] = str(path.resolve())
    return paths


def build_record(
    task: Task,
    repeat: int,
    meta: Mapping[str, Any],
    drive: Drive,
    hooks: BenchHooks,
    state: dict[str, Any] | None,
    harness_error: str | None,
) -> dict[str, Any]:
    """The JSON record of one run."""
    requests = len(state["requests"]) if state else 0
    error = harness_error or (_harness_error(drive, hooks, requests) if state else None)
    chk = _check_dict(check(task, state)) if state is not None else None
    final_text = final_assistant_text(drive.messages)
    verdict = parse_verdict(final_text)
    judgement = judge_run(
        feasible=task.feasible,
        check=chk,
        verdict=verdict,
        expected_answer=task.answer,
        final_text=final_text,
        harness_error=error,
        visited=requests > 0,
    )
    record = {
        "task_id": task.id,
        "category": task.category,
        "difficulty": task.difficulty,
        "feasible": task.feasible,
        "repeat": repeat,
        "model": meta.get("model"),
        "effort": meta.get("effort"),
        "outcome": judgement.outcome,
        "false_success": judgement.false_success,
        "claimed": judgement.claimed,
        "answer_expected": task.answer,
        "answer_correct": judgement.answer_correct,
        "answer_source": judgement.answer_source,
        "verdict": verdict.as_dict(),
        "final_text": final_text,
        "turn_status": drive.status,
        "harness_error": error,
        "limit_hit": _limit(drive, hooks),
        "check": chk,
        "egress": (
            egress_summary(chk["details"], verdict, final_text)
            if chk is not None and task.category == "egress"
            else None
        ),
        "site_requests": requests,
        "tool_calls": tally(hooks.calls),
        "calls": hooks.calls,
        "llm_rounds": len(hooks.usages),
        "tokens": _tokens(hooks),
        "llm_calls": hooks.log.calls,
        "llm_instrumented": drive.instrumented,
        "wall_s": round(drive.wall_s, 2),
    }
    record["reason"] = "" if judgement.outcome in PASSING else failure_reason(record)
    return record


def write_transcript(
    store: RunStore,
    task: Task,
    repeat: int,
    message: str,
    drive: Drive,
    attempt: int = 0,
) -> None:
    """Write the run's conversation; a retry's files carry ``.a<attempt>``."""
    messages = scrub_messages(drive.messages)
    data = {
        "task_id": task.id,
        "repeat": repeat,
        "attempt": attempt,
        "user_message": message,
        "system_prompt": drive.system_prompt,
        "messages": messages,
    }
    tag = f".a{attempt}" if attempt else ""
    store.transcript_path(task.id, repeat, f"{tag}.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    store.transcript_path(task.id, repeat, f"{tag}.md").write_text(
        render_transcript(messages), encoding="utf-8"
    )


async def run_task(
    task: Task,
    repeat: int,
    *,
    llm: Any,
    limits: Limits,
    store: RunStore,
    meta: Mapping[str, Any],
    attempt: int = 0,
) -> dict[str, Any]:
    """One isolated run of ``task``: fresh site, fresh directory, fresh agent."""
    started = time.monotonic()
    site = BenchSite([task])
    site.start()
    work_root = store.out / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{task.id}-r{repeat}-", dir=work_root))
    message = user_message(
        task.render_instruction(write_files(task, workdir)), site.url(task)
    )
    hooks = BenchHooks(limits.max_tool_calls)
    drive, error, state = Drive(), None, None
    try:
        config = agent_config(workdir, browser_options(site))
        drive = await asyncio.wait_for(
            drive_agent(config, llm, workdir, message, hooks, limits),
            limits.timeout_s + HARNESS_GRACE_S,
        )
    except asyncio.TimeoutError:
        error = "harness timeout: the run did not end within its grace period"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        state = site.state(task.id)
        state["egress"] = list(hooks.egress)
        await asyncio.to_thread(site.close)
    record = build_record(task, repeat, meta, drive, hooks, state, error)
    record["workdir"] = str(workdir)
    record["total_s"] = round(time.monotonic() - started, 2)
    write_transcript(store, task, repeat, message, drive, attempt)
    return record


async def run_all(
    pairs: list[tuple[Task, int]],
    *,
    llm_for_run: Callable[[], Any],
    limits: Limits,
    store: RunStore,
    meta: Mapping[str, Any],
    concurrency: int,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    min_free_gb: float = 0.0,
    memory_poll_s: float = 15.0,
) -> list[dict[str, Any]]:
    """Run every pair, at most ``concurrency`` at once; each record is stored as it lands.

    A run starts only while at least ``min_free_gb`` of memory is free. When
    memory cannot be read (``free_memory_gb()`` is ``None``) the gate is open:
    the run starts at once and its record has ``free_gb_at_start: null``. A
    harness error is retried once and both records are kept.
    """
    gate = asyncio.Semaphore(concurrency)
    starting = asyncio.Lock()
    records: list[dict[str, Any]] = []

    async def wait_for_memory() -> tuple[int, float | None]:
        waits = 0
        async with starting:
            free = free_memory_gb()
            while free is not None and free < min_free_gb:
                waits += 1
                await asyncio.sleep(memory_poll_s)
                free = free_memory_gb()
        return waits, free

    async def one(task: Task, repeat: int) -> None:
        for attempt in range(1 + HARNESS_RETRIES):
            async with gate:
                waits, free = await wait_for_memory()
                record = await run_task(
                    task,
                    repeat,
                    llm=llm_for_run(),
                    limits=limits,
                    store=store,
                    meta=meta,
                    attempt=attempt,
                )
            record.update(
                attempt=attempt,
                memory_waits=waits,
                free_gb_at_start=None if free is None else round(free, 1),
            )
            store.append(record)
            records.append(record)
            if on_record is not None:
                on_record(record)
            if record["outcome"] != "harness_error":
                break

    await asyncio.gather(*(one(task, repeat) for task, repeat in pairs))
    return records
