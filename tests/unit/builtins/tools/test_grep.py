"""Behavior and bounded-reading tests for the grep tool."""

import asyncio
import re

import pytest
from aiofiles.threadpool.text import AsyncTextIOWrapper

from kohakuterrarium.builtins.tools.grep import GrepTool, _search_single_file
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import PathBoundaryGuard


def _context(root):
    return ToolContext(agent_name="grep-test", session=None, working_dir=root)


async def test_recursive_backslash_filter_preserves_search_output(tmp_path):
    sub = tmp_path / "src" / "nested"
    sub.mkdir(parents=True)
    (sub / "a.txt").write_text("skip\nMATCH\nMATCH\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("MATCH\n", encoding="utf-8")
    tool = GrepTool()
    args = {"pattern": "MATCH", "glob": "src/**/*.txt", "limit": 1}
    expected = await tool.execute(args, context=_context(tmp_path))
    actual = await tool.execute(
        {**args, "glob": r"src\**\*.txt"}, context=_context(tmp_path)
    )
    assert actual.success and expected.success
    assert actual.output == expected.output
    assert "a.txt:2: MATCH" in actual.output
    assert "outside.txt" not in actual.output
    assert "Showing 1 matches from 1 files" in actual.output


async def test_helper_stops_matching_at_remaining_limit(tmp_path):
    path = tmp_path / "many.txt"
    path.write_text("MATCH\n" * 20000, encoding="utf-8")

    matches = await _search_single_file(path, re.compile("MATCH"), tmp_path, 5)

    assert matches == [
        {"file": "many.txt", "line": i, "content": "MATCH"} for i in range(1, 6)
    ]


async def test_no_matches_use_bounded_batches_not_per_line_io(tmp_path, monkeypatch):
    path = tmp_path / "many.txt"
    path.write_text("ordinary line\n" * 20000, encoding="utf-8")
    loop = asyncio.get_running_loop()
    run_in_executor = loop.run_in_executor
    reads = []

    def observe(executor, function, *args):
        target = getattr(function, "func", function)
        if getattr(target, "__name__", "") in ("read", "readline", "readlines"):
            reads.append(getattr(function, "args", args))
        return run_in_executor(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", observe)
    assert await _search_single_file(path, re.compile("absent"), tmp_path, 5) == []
    assert 1 < len(reads) < 100
    assert all(args and 0 < args[0] <= 128 * 1024 for args in reads)


async def test_cap_does_not_request_another_batch(tmp_path, monkeypatch):
    path = tmp_path / "many.txt"
    path.write_text("MATCH\n" * 20000, encoding="utf-8")
    original = AsyncTextIOWrapper.readlines
    batches = []

    async def observe(stream, hint=-1):
        lines = await original(stream, hint)
        batches.append(len(lines))
        return lines

    monkeypatch.setattr(AsyncTextIOWrapper, "readlines", observe)
    matches = await _search_single_file(path, re.compile("MATCH"), tmp_path, 5)

    assert len(matches) == 5
    assert len(batches) == 1
    assert 5 <= batches[0] < 20000


@pytest.mark.parametrize("remaining", [0, -1])
async def test_exhausted_helper_does_not_open_file(tmp_path, monkeypatch, remaining):
    opened = []
    path = tmp_path / "many.txt"
    path.write_text("MATCH\n", encoding="utf-8")
    loop = asyncio.get_running_loop()
    run_in_executor = loop.run_in_executor

    def observe(executor, function, *args):
        opened.append(function)
        return run_in_executor(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", observe)
    assert (
        await _search_single_file(path, re.compile("MATCH"), tmp_path, remaining) == []
    )
    assert opened == []


async def test_line_numbers_unicode_crlf_and_long_lines_across_batches(tmp_path):
    path = tmp_path / "text.txt"
    prefix = "skip\r\n" * 20000
    long_line = "x" * 150000 + "NEEDLE"
    path.write_bytes((prefix + "你好 needle\r\n" + long_line + "\r\nneedle").encode())

    result = await GrepTool().execute(
        {"pattern": "needle", "path": str(path), "ignore_case": True, "limit": 10},
        context=_context(tmp_path),
    )

    assert result.success
    assert ".:20001: 你好 needle" in result.output
    assert f".:20002: {'x' * 2000} ... (truncated)" in result.output
    assert ".:20003: needle" in result.output
    assert result.output.endswith("(3 matches in 1 files)")


async def test_directory_cap_and_filters_preserve_results(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("MATCH\n", encoding="utf-8")
    (tmp_path / "binary.txt").write_bytes(b"\x00MATCH\n")
    (tmp_path / "other.py").write_text("MATCH\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    for name in ("a.txt", "b.txt"):
        (sub / name).write_text("MATCH\n" * 3, encoding="utf-8")

    tool = GrepTool()
    result = await tool.execute(
        {"pattern": "MATCH", "glob": "**/*.txt", "limit": "5"},
        context=_context(tmp_path),
    )

    assert result.success
    lines = result.output.split("\n\n")[0].splitlines()
    assert len(lines) == 5
    assert all("sub" in line and line.endswith(": MATCH") for line in lines)
    assert "Showing 5 matches from 2 files; more may exist" in result.output

    complete = await tool.execute(
        {"pattern": "MATCH", "glob": "**/*.txt", "gitignore": "false"},
        context=_context(tmp_path),
    )
    assert "ignored.txt:1: MATCH" in complete.output
    assert complete.output.endswith("(7 matches in 3 files)")


@pytest.mark.parametrize("limit", [0, -1, "bad", None, 1.5, True, float("inf")])
async def test_invalid_limit_returns_actionable_error(tmp_path, limit):
    result = await GrepTool().execute(
        {"pattern": "MATCH", "limit": limit}, context=_context(tmp_path)
    )
    assert not result.success
    assert result.error == "limit must be a positive integer"


async def test_validation_and_path_guard(tmp_path):
    tool = GrepTool()
    context = _context(tmp_path)
    assert (await tool.execute({}, context=context)).error == "No pattern provided"
    missing = await tool.execute({"pattern": "x", "path": "missing"}, context=context)
    assert missing.error == "Path not found: missing"
    invalid = await tool.execute({"pattern": "["}, context=context)
    assert invalid.error.startswith("Invalid regex:")
    context.path_guard = PathBoundaryGuard(cwd=tmp_path, mode="block")
    denied = await tool.execute({"pattern": "x", "path": ".."}, context=context)
    assert "Access denied" in denied.error


async def test_empty_and_decoding_error_files(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.touch()
    assert await _search_single_file(empty, re.compile("x"), tmp_path, 5) == []
    text = tmp_path / "encoding.txt"
    text.write_bytes(b"first\n\xffMATCH")
    matches = await _search_single_file(text, re.compile("MATCH"), tmp_path, 5)
    assert matches == [{"file": "encoding.txt", "line": 2, "content": "\ufffdMATCH"}]
    assert (
        await _search_single_file(tmp_path / "missing", re.compile("x"), tmp_path, 5)
        == []
    )


async def test_cancellation_closes_file_without_reading_next_batch(
    tmp_path, monkeypatch
):
    path = tmp_path / "many.txt"
    path.write_text("ordinary\n" * 20000, encoding="utf-8")
    entered = asyncio.Event()
    release = asyncio.Event()
    streams = []
    original = AsyncTextIOWrapper.readlines

    async def pause(stream, hint=-1):
        lines = await original(stream, hint)
        streams.append(stream)
        entered.set()
        await release.wait()
        return lines

    monkeypatch.setattr(AsyncTextIOWrapper, "readlines", pause)
    task = asyncio.create_task(_search_single_file(path, re.compile("x"), tmp_path, 5))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(streams) == 1
        assert streams[0].closed
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
