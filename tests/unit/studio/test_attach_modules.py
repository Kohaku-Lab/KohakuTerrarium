"""Unit tests for the easier attach helpers — policies, log parsing,
observer, trace.
"""

import asyncio
import os
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.attach import (
    log as log_mod,
    observer as observer_mod,
    policies as policies_mod,
    trace as trace_mod,
)
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

# ── attach.policies ──────────────────────────────────────────


class _FakeAgent:
    def __init__(self, *, has_input=False, has_channels=False):
        if has_input:
            self.input_module = object()
        if has_channels:
            self._channels = ["c1"]


class _FakeManager:
    def __init__(self, agents=None, terrariums=None):
        self._agents = agents or {}
        self._terrariums = terrariums or {}


class TestGetPolicies:
    def test_no_manager_baseline(self):
        out = policies_mod.get_policies("cid")
        assert out == [policies_mod.Policy.LOG, policies_mod.Policy.TRACE]

    def test_unknown_agent_baseline(self):
        out = policies_mod.get_policies("ghost", _FakeManager())
        assert out == [policies_mod.Policy.LOG, policies_mod.Policy.TRACE]

    def test_input_module_adds_io(self):
        mgr = _FakeManager(agents={"cid": _FakeAgent(has_input=True)})
        out = policies_mod.get_policies("cid", mgr)
        assert out[0] == policies_mod.Policy.IO

    def test_channels_adds_observer(self):
        mgr = _FakeManager(
            agents={"cid": _FakeAgent(has_input=True, has_channels=True)}
        )
        out = policies_mod.get_policies("cid", mgr)
        assert policies_mod.Policy.OBSERVER in out


class TestGetGraphPolicies:
    def test_no_manager_baseline(self):
        out = policies_mod.get_graph_policies("sid")
        assert policies_mod.Policy.OBSERVER in out

    def test_unknown_session_baseline(self):
        out = policies_mod.get_graph_policies("ghost", _FakeManager())
        assert policies_mod.Policy.IO not in out

    def test_with_root_agent_adds_io(self):
        runtime = SimpleNamespace(root=object())
        mgr = _FakeManager(terrariums={"sid": runtime})
        out = policies_mod.get_graph_policies("sid", mgr)
        assert out[0] == policies_mod.Policy.IO

    def test_root_agent_attr_alternate_name(self):
        runtime = SimpleNamespace(_root_agent=object())
        runtime.root = None
        mgr = _FakeManager(terrariums={"sid": runtime})
        out = policies_mod.get_graph_policies("sid", mgr)
        assert out[0] == policies_mod.Policy.IO


class TestGetCreaturePolicies:
    async def test_unknown_creature_baseline(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:
            out = policies_mod.get_creature_policies(svc, "ghost")
            assert "io" not in [p.value for p in out]
        finally:
            await t.shutdown()

    async def test_creature_with_input_adds_io(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent.input_module = object()
            out = policies_mod.get_creature_policies(svc, "alice")
            assert policies_mod.Policy.IO in out
        finally:
            await t.shutdown()

    async def test_creature_in_graph_with_channel_adds_observer(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        svc = LocalTerrariumService(t)
        try:
            out = policies_mod.get_creature_policies(svc, "alice")
            assert policies_mod.Policy.OBSERVER in out
        finally:
            await t.shutdown()


class TestGetSessionPolicies:
    async def test_unknown_session_baseline(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:
            out = policies_mod.get_session_policies(svc, "ghost")
            assert policies_mod.Policy.IO not in out
        finally:
            await t.shutdown()

    async def test_privileged_creature_adds_io(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            t.get_creature("alice").is_privileged = True
            gid = t.get_creature("alice").graph_id
            out = policies_mod.get_session_policies(svc, gid)
            assert policies_mod.Policy.IO in out
        finally:
            await t.shutdown()


# ── attach.log helpers ──────────────────────────────────────


class TestLogHelpers:
    def test_parse_line_matches_format(self):
        out = log_mod._parse_line("[12:34:56] [some.module] [INFO] hello world")
        assert out == {
            "ts": "12:34:56",
            "level": "info",
            "module": "some.module",
            "text": "hello world",
        }

    def test_parse_line_malformed_falls_back(self):
        out = log_mod._parse_line("not a log line")
        assert out["level"] == "unknown"
        assert out["text"] == "not a log line"

    def test_find_current_process_log_no_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(log_mod, "DEFAULT_LOG_DIR", tmp_path / "ghost")
        assert log_mod._find_current_process_log() is None

    def test_find_current_process_log_no_matches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(log_mod, "DEFAULT_LOG_DIR", tmp_path)
        # Create a log file with a different pid pattern.
        (tmp_path / "20260101_000000_pid99999_x.log").write_text("x")
        assert log_mod._find_current_process_log() is None

    def test_find_current_process_log_picks_newest(self, monkeypatch, tmp_path):
        monkeypatch.setattr(log_mod, "DEFAULT_LOG_DIR", tmp_path)
        pid = os.getpid()
        old = tmp_path / f"20260101_000000_pid{pid}_x.log"
        old.write_text("old")
        os.utime(old, (1000, 1000))
        new = tmp_path / f"20260101_120000_pid{pid}_y.log"
        new.write_text("new")
        out = log_mod._find_current_process_log()
        assert out == new


# ── attach.trace helpers ────────────────────────────────────


class TestTraceHelpers:
    def test_agent_from_key(self):
        assert trace_mod._agent_from_key("alice:e0") == "alice"
        assert (
            trace_mod._agent_from_key("alice:attached:bob:e3") == "alice:attached:bob"
        )
        assert trace_mod._agent_from_key("noseparator") == ""

    def test_find_live_store_none_matches(self):
        # Empty registry → None.
        out = trace_mod._find_live_store("session-x", stores=[])
        assert out is None

    def test_find_live_store_skips_none(self):
        out = trace_mod._find_live_store("session-x", stores=[None])
        assert out is None

    def test_find_live_store_matches_by_path(self):
        store = SimpleNamespace(_path="/some/path/sess-1.kohakutr")
        out = trace_mod._find_live_store("sess-1", stores=[store])
        assert out is store

    def test_find_live_store_matches_kt_suffix(self):
        store = SimpleNamespace(_path="/p/sess-2.kt")
        out = trace_mod._find_live_store("sess-2", stores=[store])
        assert out is store

    def test_find_live_store_versioned(self):
        store = SimpleNamespace(_path="/p/sess-3.kohakutr.v2")
        out = trace_mod._find_live_store("sess-3", stores=[store])
        assert out is store

    def test_find_live_store_no_path(self):
        store = SimpleNamespace()  # no _path attr
        out = trace_mod._find_live_store("sess-x", stores=[store])
        assert out is None

    def test_enqueue_or_drop_normal(self):
        q = asyncio.Queue()
        trace_mod._enqueue_or_drop(q, {"x": 1})
        assert q.qsize() == 1

    def test_enqueue_or_drop_full(self):
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"first": True})
        # Should silently drop, not raise.
        trace_mod._enqueue_or_drop(q, {"second": True})
        assert q.qsize() == 1


# ── observer.stream_session_channels ────────────────────────


class TestObserverStreams:
    async def test_unknown_session_raises(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:

            async def _consume():
                async for _ in observer_mod.stream_session_channels(svc, "ghost"):
                    pass

            with pytest.raises(KeyError):
                await _consume()
        finally:
            await t.shutdown()

    async def test_no_channels_stream_ends(self):
        # Session with no channels and an empty registry → the iterator
        # subscribes nothing and immediately exits via running_check.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            # Subscribe and immediately tear down by removing the env.
            collected = []

            async def collect():
                async for ev in observer_mod.stream_session_channels(svc, gid):
                    collected.append(ev)

            task = asyncio.create_task(collect())
            await asyncio.sleep(0.05)
            # Remove the env so running_check goes False.
            t._environments.pop(gid, None)
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()
        finally:
            await t.shutdown()
