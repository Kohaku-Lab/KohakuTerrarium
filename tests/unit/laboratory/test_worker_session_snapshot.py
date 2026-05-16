"""Verify worker-side snapshot capture for resume.

Reproduces the exact path-form remote-spawn flow: real engine, real
``add_creature(path)``, real ``WorkerSessionAttacher.attach()``. After
attach the worker's local store must carry either ``config_path`` or
``config_snapshot`` so resuming the file on any node (including the
host's mirror, which receives meta via the SessionEventTee) succeeds.
"""

from pathlib import Path

import pytest

from kohakuterrarium.laboratory.adapters._worker_session import (
    WorkerSessionAttacher,
)
from kohakuterrarium.terrarium.engine import Terrarium


def _write_dummy_agent_cfg(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        "system_prompt: hello\n"
        "llm_profile: openai/gpt-4-test\n"
        "model: gpt-4\nprovider: openai\n"
        "input:\n  type: cli\noutput:\n  type: stdout\n",
        encoding="utf-8",
    )
    return cdir


class _RecordingNode:
    def __init__(self):
        self.notified = []

    async def notify(self, *args, **kwargs):
        self.notified.append((args, kwargs))


@pytest.mark.asyncio
async def test_path_form_spawn_persists_config_path_in_meta(tmp_path):
    cdir = _write_dummy_agent_cfg(tmp_path, "alpha")
    engine = Terrarium(session_dir=str(tmp_path / "worker-sessions"))
    attacher = WorkerSessionAttacher(
        engine, _RecordingNode(), session_dir=tmp_path / "worker-sessions"
    )
    try:
        creature = await engine.add_creature(str(cdir), suppress_io=True)
        attacher.attach(creature.creature_id)

        store = engine._session_stores[creature.graph_id]
        meta = store.load_meta()
        assert meta.get("config_type") == "agent"
        # Path-form spawns: config_path is the absolute config folder.
        assert meta.get("config_path"), f"meta has no config_path: {meta}"
    finally:
        attacher.close_all()
        await engine.shutdown()


@pytest.mark.asyncio
async def test_meta_is_populated_before_session_store_publish(tmp_path):
    """``_ObservingSessionStores`` triggers a Tee on store assignment.

    If meta were written AFTER ``engine._session_stores[gid] = store``,
    the Tee's synchronous ``_meta_item`` snapshot would race the
    ``init_meta`` write and the host mirror would receive a meta with
    only ``agents`` (load_meta's default) — losing ``config_path`` /
    ``config_snapshot`` and breaking resume.  The attacher MUST populate
    meta before publishing the store.
    """
    from kohakuterrarium.session.sync import SessionEventTee

    cdir = _write_dummy_agent_cfg(tmp_path, "gamma")
    engine = Terrarium(session_dir=str(tmp_path / "worker-sessions"))
    snapshot_seen: dict = {}

    class _CapturingNode:
        async def notify(self, *args, **kwargs):
            body = kwargs.get("body") or (args[3] if len(args) > 3 else {})
            type_ = kwargs.get("type") or (args[2] if len(args) > 2 else "")
            if type_ == "meta":
                snapshot_seen["meta"] = body.get("meta") or {}

        def register_app_extension(self, *a, **kw):
            pass

        def unregister_app_extension(self, *a, **kw):
            pass

    node = _CapturingNode()
    attacher = WorkerSessionAttacher(
        engine, node, session_dir=tmp_path / "worker-sessions"
    )
    try:
        # Patch SessionEventTee to capture meta the moment it enqueues.
        original_enqueue = SessionEventTee._meta_item

        def _capture(self):
            item = original_enqueue(self)
            snapshot_seen["from_tee"] = dict(item[1].get("meta") or {})
            return item

        SessionEventTee._meta_item = _capture
        try:
            creature = await engine.add_creature(str(cdir), suppress_io=True)
            attacher.attach(creature.creature_id)
            # Allow the pump one tick to send.
            import asyncio as _aio

            for _ in range(5):
                await _aio.sleep(0.01)
                if "from_tee" in snapshot_seen:
                    break
            tee_meta = snapshot_seen.get("from_tee") or {}
            # The Tee MUST snapshot meta after init_meta wrote it.
            assert tee_meta.get(
                "config_path"
            ), f"Tee captured meta before init_meta finished; keys={sorted(tee_meta)}"
        finally:
            SessionEventTee._meta_item = original_enqueue
    finally:
        attacher.close_all()
        await engine.shutdown()


@pytest.mark.asyncio
async def test_inline_config_spawn_persists_snapshot(tmp_path):
    """An ``AgentConfig`` spawn (no ``agent_path``) must save a snapshot."""
    from kohakuterrarium.core.config_types import (
        AgentConfig,
        InputConfig,
        OutputConfig,
    )

    cfg = AgentConfig(
        name="inline",
        system_prompt="hi",
        input=InputConfig(type="cli"),
        output=OutputConfig(type="stdout"),
    )
    engine = Terrarium(session_dir=str(tmp_path / "worker-sessions"))
    attacher = WorkerSessionAttacher(
        engine, _RecordingNode(), session_dir=tmp_path / "worker-sessions"
    )
    try:
        creature = await engine.add_creature(cfg, suppress_io=True)
        attacher.attach(creature.creature_id)

        store = engine._session_stores[creature.graph_id]
        meta = store.load_meta()
        snapshot = meta.get("config_snapshot") or {}
        # Inline configs have no agent_path on disk; the snapshot is the
        # ONLY way resume can rebuild on a fresh node.
        assert snapshot, f"meta has no config_snapshot: keys={list(meta)}"
        assert snapshot.get("name") == "inline"
    finally:
        attacher.close_all()
        await engine.shutdown()
