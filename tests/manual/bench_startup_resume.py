"""Compare fresh-process tool lookup and synthetic session resume."""

import argparse
import asyncio
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil


def environment():
    packages = {
        dist.metadata["Name"].lower(): dist.version
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"].lower() != "kohakuterrarium"
    }
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "dependency_digest": hashlib.sha256(
            json.dumps(packages, sort_keys=True).encode()
        ).hexdigest(),
    }


async def seed(path, config):
    from kohakuterrarium import Agent
    from kohakuterrarium.session.store import SessionStore
    from kohakuterrarium.testing.llm import ScriptedLLM

    agent = await Agent.build(config, llm=ScriptedLLM(["seed reply"]), io="none")
    store = SessionStore(path)
    store.init_meta(
        session_id="startup-benchmark",
        config_type="agent",
        config_path=str(config),
        pwd=str(path.parent),
        agents=["bench"],
    )
    agent.attach_session_store(store)
    try:
        await agent.start()
        for index in range(50):
            result = await agent.run(f"seed turn {index}", timeout=30)
            assert result.text == "seed reply", result
    finally:
        await agent.stop()
        store.close(update_status=False)


async def resume(path):
    from kohakuterrarium.session.resume.async_flow import resume_agent_async
    from kohakuterrarium.testing.llm import ScriptedLLM

    class ObservedLLM(ScriptedLLM):
        called_at = None

        async def chat(self, *args, **kwargs):
            self.called_at = time.perf_counter()
            async for chunk in super().chat(*args, **kwargs):
                yield chunk

    llm = ObservedLLM(["probe reply"])
    entry = time.perf_counter()
    agent, store = await resume_agent_async(path, llm=llm)
    try:
        await agent.start()
        ready = time.perf_counter()
        gc.collect()
        rss_ready = psutil.Process().memory_info().rss
        sent = time.perf_counter()
        result = await agent.run("benchmark next message", timeout=30)
        finished = time.perf_counter()
        assert result.text == "probe reply", result
        assert llm.call_count == 1
        return {
            "resume_entry_to_ready_s": ready - entry,
            "ready_at": ready,
            "rss_ready_mib": rss_ready / 2**20,
            "first_message_to_llm_s": llm.called_at - sent,
            "first_message_roundtrip_s": finished - sent,
        }
    finally:
        await agent.stop()
        store.close(update_status=False)


def child(mode, source):
    process = psutil.Process()
    started = time.perf_counter()
    if mode == "lookup":
        from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

        assert get_builtin_tool("read").tool_name == "read"
        row = {"ready_at": time.perf_counter()}
    else:
        row = asyncio.run(resume(source))
    row["operation_to_ready_s"] = row.pop("ready_at") - started
    info = process.memory_info()
    row.update(
        rss_mib=info.rss / 2**20,
        peak_working_set_mib=info.peak_wset / 2**20,
        openai_loaded="openai" in sys.modules,
        modules=len(sys.modules),
    )
    print(json.dumps(row))


def invoke(python, arguments, root):
    env = dict(os.environ)
    env.update(
        KT_CONFIG_DIR=str(root / "config"),
        HOME=str(root / "home"),
        USERPROFILE=str(root / "home"),
        CODEX_HOME=str(root / "codex"),
    )
    started = time.perf_counter()
    result = subprocess.run(
        [str(python), "-I", str(Path(__file__).resolve()), *arguments],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(
            f"Child failed ({arguments}):\n{result.stdout}\n{result.stderr}"
        )
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    row = json.loads(lines[-1])
    row["process_wall_s"] = time.perf_counter() - started
    return row


def compare(args):
    candidate = Path(sys.executable)
    baseline = Path(args.baseline).resolve()
    with tempfile.TemporaryDirectory(prefix="kt-startup-bench-") as folder:
        root = Path(folder)
        (root / "home").mkdir()
        config = root / "creature"
        config.mkdir()
        (config / "config.yaml").write_text(
            "name: bench\nsystem_prompt: You are a benchmark agent.\n"
            "input: {type: none}\noutput: {type: none}\n"
            "tools:\n  - {name: read, type: builtin}\n"
            "controller: {tool_format: bracket}\n",
            encoding="utf-8",
        )
        versions = {
            name: invoke(executable, ["--environment"], root)
            for name, executable in (("baseline", baseline), ("candidate", candidate))
        }
        assert (
            versions["baseline"]["dependency_digest"]
            == versions["candidate"]["dependency_digest"]
        ), versions
        assert (
            versions["baseline"]["python"] == versions["candidate"]["python"]
        ), versions
        original = root / "original.kohakutr"
        invoke(baseline, ["--seed", str(original), "--config", str(config)], root)
        sample = {
            "turns": 50,
            "bytes": original.stat().st_size,
            "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        }
        rows = []
        for mode in ("lookup", "resume"):
            for repeat in range(args.repeats):
                order = (("baseline", baseline), ("candidate", candidate))
                if repeat % 2:
                    order = tuple(reversed(order))
                for name, executable in order:
                    target = root / f"{mode}-{name}-{repeat}.kohakutr"
                    if mode == "resume":
                        shutil.copyfile(original, target)
                    row = invoke(
                        executable, ["--child", mode, "--source", str(target)], root
                    )
                    row.update(group=name, mode=mode, repeat=repeat + 1)
                    rows.append(row)
        summaries = []
        for mode in ("lookup", "resume"):
            for name in ("baseline", "candidate"):
                selected = [r for r in rows if r["mode"] == mode and r["group"] == name]
                keys = [
                    key
                    for key, value in selected[0].items()
                    if type(value) in (int, float) and key != "repeat"
                ]
                summaries.append(
                    {
                        "mode": mode,
                        "group": name,
                        "metrics": {
                            key: {
                                "median": statistics.median(r[key] for r in selected),
                                "min": min(r[key] for r in selected),
                                "max": max(r[key] for r in selected),
                            }
                            for key in keys
                        },
                    }
                )
        output = {
            "notes": "Fresh processes, warm/unspecified OS caches; no network or real LLM. Resume uses 50 baseline-generated turns and a read-only builtin. Timings exclude interpreter startup but include target imports; process_wall_s includes cleanup. RSS-ready samples after GC; peak is Windows process-lifetime peak working set.",
            "environment": versions,
            "sample": sample,
            "repeats": args.repeats,
            "summary": summaries,
            "raw": rows,
        }
        encoded = json.dumps(output, indent=2)
        if args.output:
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        print(encoded)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output")
    parser.add_argument("--child", choices=("lookup", "resume"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--environment", action="store_true")
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" and not args.environment:
        parser.error("Windows is required for peak working-set measurements")
    if args.repeats <= 0:
        parser.error("--repeats must be positive")
    if args.environment:
        print(json.dumps(environment()))
    elif args.seed:
        asyncio.run(seed(args.seed, args.config))
        print(json.dumps({"seeded": True}))
    elif args.child:
        child(args.child, args.source)
    else:
        if not args.baseline:
            parser.error("--baseline is required")
        compare(args)
