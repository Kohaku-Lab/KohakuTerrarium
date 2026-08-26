import importlib
import json
import os
import sys


def _reload_trace_module():
    sys.modules.pop("kohakuterrarium.utils.startup_trace", None)
    return importlib.import_module("kohakuterrarium.utils.startup_trace")


def test_startup_trace_is_silent_when_disabled(monkeypatch, tmp_path):
    trace_path = tmp_path / "startup.jsonl"
    monkeypatch.delenv("KT_STARTUP_TRACE", raising=False)

    trace = _reload_trace_module()
    trace.mark("parser_ready", surface="cli")

    assert not trace_path.exists()


def test_startup_trace_writes_process_correlated_jsonl(monkeypatch, tmp_path):
    trace_path = tmp_path / "nested" / "startup.jsonl"
    monkeypatch.setenv("KT_STARTUP_TRACE", str(trace_path))
    monkeypatch.setenv("KT_STARTUP_RUN_ID", "startup-test")

    trace = _reload_trace_module()
    trace.mark("parser_ready", surface="cli", command="version")
    trace.mark("dispatch_selected", surface="cli", command="version")

    shard = trace_path.with_name(f"{trace_path.stem}.{os.getpid()}{trace_path.suffix}")
    records = [json.loads(line) for line in shard.read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "parser_ready",
        "dispatch_selected",
    ]
    assert all(record["run_id"] == "startup-test" for record in records)
    assert all(record["pid"] == os.getpid() for record in records)
    assert all(record["ppid"] == os.getppid() for record in records)
    assert all(record["surface"] == "cli" for record in records)
    assert records[0]["command"] == "version"
    assert records[0]["elapsed_ms"] <= records[1]["elapsed_ms"]
    assert records[0]["monotonic_ns"] <= records[1]["monotonic_ns"]
    assert records[0]["wall_ns"] <= records[1]["wall_ns"]


def test_startup_trace_uses_external_wall_clock_origin(monkeypatch, tmp_path):
    trace_path = tmp_path / "startup.jsonl"
    monkeypatch.setenv("KT_STARTUP_TRACE", str(trace_path))
    monkeypatch.setenv("KT_STARTUP_ORIGIN_NS", "1000000000")

    trace = _reload_trace_module()
    trace.mark("process_enter", surface="web")

    shard = trace_path.with_name(f"{trace_path.stem}.{os.getpid()}{trace_path.suffix}")
    record = json.loads(shard.read_text())
    assert record["startup_ms"] > 0


def test_startup_trace_never_breaks_startup_for_bad_metadata(monkeypatch, tmp_path):
    trace_path = tmp_path / "startup.jsonl"
    monkeypatch.setenv("KT_STARTUP_TRACE", str(trace_path))
    monkeypatch.setenv(
        "KT_STARTUP_ORIGIN_NS", "9999999999999999999999999999999999999999"
    )

    trace = _reload_trace_module()
    trace.mark("process_enter", metadata=object())

    shard = trace_path.with_name(f"{trace_path.stem}.{os.getpid()}{trace_path.suffix}")
    record = json.loads(shard.read_text())
    assert record["event"] == "process_enter"
    assert record["metadata"].startswith("<object object at")


def test_startup_trace_uses_process_shards(monkeypatch, tmp_path):
    trace_path = tmp_path / "startup.jsonl"
    monkeypatch.setenv("KT_STARTUP_TRACE", str(trace_path))

    trace = _reload_trace_module()
    trace.mark("process_enter")

    shard = trace_path.with_name(f"{trace_path.stem}.{os.getpid()}{trace_path.suffix}")
    assert shard.exists()
    assert not trace_path.exists()


def test_startup_trace_generates_and_exports_run_id(monkeypatch, tmp_path):
    trace_path = tmp_path / "startup.jsonl"
    monkeypatch.setenv("KT_STARTUP_TRACE", str(trace_path))
    monkeypatch.delenv("KT_STARTUP_RUN_ID", raising=False)

    trace = _reload_trace_module()
    trace.mark("process_enter", surface="web")

    shard = trace_path.with_name(f"{trace_path.stem}.{os.getpid()}{trace_path.suffix}")
    record = json.loads(shard.read_text())
    assert record["run_id"]
    assert os.environ["KT_STARTUP_RUN_ID"] == record["run_id"]
