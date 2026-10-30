"""Transfer deletion revalidates activity after resource admission."""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.laboratory.adapters.terrarium_session import (
    TerrariumSessionAdapter,
)
from kohakuterrarium.session.occupancy.gate import GATE, try_fast


async def test_attach_while_delete_waits_does_not_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    root = tmp_path / "resume"
    root.mkdir()
    path = root / "session.kohakutr"
    path.write_bytes(b"saved")
    engine = SimpleNamespace(_session_stores={})
    adapter = TerrariumSessionAdapter(
        engine, SimpleNamespace(register_app_extension=lambda *_: None)
    )
    fast = try_fast(path, eligible=True).lease
    task = asyncio.create_task(
        adapter._dispatch_delete_transfer({"session_path": str(path)})
    )
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while GATE.snapshot(path).legacy_pending == 0:
            if task.done():
                await task
                pytest.fail("deletion completed before admission")
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        engine._session_stores["new-owner"] = SimpleNamespace(path=path)
        fast.finish(source_released=True)
        with pytest.raises(ValueError, match="active session"):
            await task
        assert path.read_bytes() == b"saved"
    finally:
        fast.finish(source_released=True)
        await asyncio.gather(task, return_exceptions=True)
