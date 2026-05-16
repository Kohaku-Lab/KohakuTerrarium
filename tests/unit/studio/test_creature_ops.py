"""Unit tests for the small studio/sessions/creature_* delegation modules.

These modules forward to the TerrariumService Protocol — tests verify
the forwarding contract using a fake service."""

from kohakuterrarium.studio.sessions.creature_ctl import (
    cancel_job,
    interrupt,
    list_jobs,
    promote_job,
)


class _FakeService:
    def __init__(self):
        self.calls = []

    async def interrupt(self, cid):
        self.calls.append(("interrupt", cid))

    async def list_jobs(self, cid):
        self.calls.append(("list_jobs", cid))
        return [{"id": "j1"}]

    async def stop_job(self, cid, jid):
        self.calls.append(("stop_job", cid, jid))
        return True

    async def promote_job(self, cid, jid):
        self.calls.append(("promote_job", cid, jid))
        return False


class TestCreatureCtl:
    async def test_interrupt(self):
        svc = _FakeService()
        await interrupt(svc, "s", "cid")
        assert svc.calls == [("interrupt", "cid")]

    async def test_list_jobs(self):
        svc = _FakeService()
        out = await list_jobs(svc, "s", "cid")
        assert out == [{"id": "j1"}]

    async def test_cancel_job(self):
        svc = _FakeService()
        ok = await cancel_job(svc, "s", "cid", "j1")
        assert ok is True
        assert svc.calls[-1] == ("stop_job", "cid", "j1")

    async def test_promote_job(self):
        svc = _FakeService()
        ok = await promote_job(svc, "s", "cid", "j1")
        assert ok is False
        assert svc.calls[-1] == ("promote_job", "cid", "j1")
