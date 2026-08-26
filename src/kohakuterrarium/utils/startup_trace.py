"""Opt-in JSONL milestones for profiling process startup."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

_TRACE_PATH = os.environ.get("KT_STARTUP_TRACE", "")
_START_NS = time.perf_counter_ns()
_RUN_ID = os.environ.get("KT_STARTUP_RUN_ID", "")
try:
    _ORIGIN_NS = int(os.environ.get("KT_STARTUP_ORIGIN_NS", "0") or 0)
except ValueError:
    _ORIGIN_NS = 0
try:
    if _TRACE_PATH:
        if not _RUN_ID:
            _RUN_ID = uuid4().hex
            os.environ["KT_STARTUP_RUN_ID"] = _RUN_ID
        if not _ORIGIN_NS:
            _ORIGIN_NS = time.time_ns()
            os.environ["KT_STARTUP_ORIGIN_NS"] = str(_ORIGIN_NS)
except Exception:
    _TRACE_PATH = ""


def mark(event: str, **fields: object) -> None:
    """Append a startup milestone when ``KT_STARTUP_TRACE`` is enabled."""
    if not _TRACE_PATH:
        return
    try:
        monotonic_ns = time.perf_counter_ns()
        wall_ns = time.time_ns()
        record = {
            "run_id": _RUN_ID,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "event": event,
            "elapsed_ms": round((monotonic_ns - _START_NS) / 1_000_000, 3),
            "monotonic_ns": monotonic_ns,
            "wall_ns": wall_ns,
            "startup_ms": round((wall_ns - _ORIGIN_NS) / 1_000_000, 3),
            **fields,
        }
        path = Path(_TRACE_PATH).expanduser()
        suffix = path.suffix or ".jsonl"
        shard = path.with_name(f"{path.stem}.{os.getpid()}{suffix}")
        shard.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            default=repr,
        )
        with shard.open("a", encoding="utf-8") as stream:
            stream.write(f"{line}\n")
    except Exception:
        return
