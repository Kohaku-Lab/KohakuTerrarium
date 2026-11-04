"""Measure sender buffering for synthetic archive bytes; requires psutil."""

import argparse
import asyncio
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
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


class DigestSink:
    """A constant-space RPC sink that verifies every transferred byte."""

    def __init__(self):
        self.digest = hashlib.sha256()
        self.received = 0
        self.chunks = 0
        self.max_chunk_bytes = 0
        self.expected = None

    async def request(self, *, to_node, namespace, type, body, timeout):
        if type == "write_begin":
            self.expected = body
            return {"transfer_id": "benchmark", "chunk_size": 262144}
        if type == "write_chunk":
            assert body["seq"] == self.chunks
            piece = base64.b64decode(body["bytes_b64"])
            self.digest.update(piece)
            self.received += len(piece)
            self.chunks += 1
            self.max_chunk_bytes = max(self.max_chunk_bytes, len(piece))
            return {"received": self.received}
        if type == "write_commit":
            assert self.received == self.expected["total_size"]
            assert self.digest.hexdigest() == self.expected["sha256"]
            return {"written": self.received, "sha256": self.digest.hexdigest()}
        raise AssertionError(type)


async def transfer(source):
    import aiofiles

    from kohakuterrarium.laboratory import file_transfer

    sink = DigestSink()
    process = psutil.Process()
    before = process.memory_info()
    started = time.perf_counter()
    if hasattr(file_transfer, "stream_write_path"):
        result = await file_transfer.stream_write_path(
            sink, "worker", "config://", "benchmark.bin", source
        )
        implementation = "stream_write_path"
    else:
        async with aiofiles.open(source, "rb") as stream:
            data = await stream.read()
        result = await file_transfer.stream_write_file(
            sink, "worker", "config://", "benchmark.bin", data
        )
        implementation = "read_all_then_stream_write_file"
    elapsed = time.perf_counter() - started
    after = process.memory_info()
    return {
        "implementation": implementation,
        "operation_s": elapsed,
        "rss_before_mib": before.rss / 2**20,
        "rss_after_mib": after.rss / 2**20,
        "peak_before_mib": before.peak_wset / 2**20,
        "peak_working_set_mib": after.peak_wset / 2**20,
        "peak_above_initial_rss_mib": (after.peak_wset - before.rss) / 2**20,
        "bytes": result["written"],
        "sha256": result["sha256"],
        "chunks": sink.chunks,
        "max_chunk_bytes": sink.max_chunk_bytes,
    }


def invoke(python, arguments, root):
    env = dict(os.environ)
    env["KT_CONFIG_DIR"] = str(root / "config")
    result = subprocess.run(
        [str(python), "-I", str(Path(__file__).resolve()), *arguments],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Child failed:\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def compare(args):
    baseline, candidate = Path(args.baseline).resolve(), Path(sys.executable)
    with tempfile.TemporaryDirectory(prefix="kt-transfer-bench-") as folder:
        root = Path(folder)
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
        rows, samples = [], []
        for size_mib in args.sizes:
            source = root / f"archive-{size_mib}.bin"
            digest = hashlib.sha256()
            block = bytes(range(256)) * 4096
            with source.open("wb") as stream:
                for _ in range(size_mib):
                    stream.write(block)
                    digest.update(block)
            samples.append({"size_mib": size_mib, "sha256": digest.hexdigest()})
            for repeat in range(args.repeats):
                order = (("baseline", baseline), ("candidate", candidate))
                if repeat % 2:
                    order = tuple(reversed(order))
                for name, executable in order:
                    row = invoke(executable, ["--child", str(source)], root)
                    assert row["sha256"] == digest.hexdigest()
                    row.update(group=name, size_mib=size_mib, repeat=repeat + 1)
                    rows.append(row)
        summary = []
        for size in args.sizes:
            for group in ("baseline", "candidate"):
                selected = [
                    r for r in rows if r["size_mib"] == size and r["group"] == group
                ]
                keys = (
                    "operation_s",
                    "peak_working_set_mib",
                    "peak_above_initial_rss_mib",
                )
                summary.append(
                    {
                        "group": group,
                        "size_mib": size,
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
            "notes": "Transport-only synthetic bytes, not session replay. Fresh processes, same installed dependencies, warm/unspecified OS caches. A constant-space in-process digest sink replaces network and worker disk; this is not remote-ready latency or full cluster memory. Timings include source hashing and chunk encoding/decoding. Peak is Windows process-lifetime peak working set.",
            "environment": versions,
            "samples": samples,
            "repeats": args.repeats,
            "summary": summary,
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
    parser.add_argument("--sizes", type=int, nargs="+", default=[64, 256])
    parser.add_argument("--output")
    parser.add_argument("--environment", action="store_true")
    parser.add_argument("--child", type=Path)
    args = parser.parse_args()
    if sys.platform != "win32" and not args.environment:
        parser.error("Windows is required for peak working-set measurements")
    if args.repeats <= 0 or any(size <= 0 for size in args.sizes):
        parser.error("--repeats and --sizes must be positive")
    if args.environment:
        print(json.dumps(environment()))
    elif args.child:
        print(json.dumps(asyncio.run(transfer(args.child))))
    else:
        if not args.baseline:
            parser.error("--baseline is required")
        compare(args)
