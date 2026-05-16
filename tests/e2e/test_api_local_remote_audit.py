"""API route audit for local-vs-remote inconsistency.

Each test targets ONE suspected bug found by static audit of
``src/kohakuterrarium/api/routes/`` cross-checked against
``studio.sessions.lifecycle``.  The bugs are the lifecycle helpers
that still call ``as_engine(service)`` despite living behind routes
that ``Depends(get_service)`` — in lab-host mode
``MultiNodeTerrariumService.engine`` raises ``RuntimeError`` and the
helper's only excepts are ``ValueError`` / ``KeyError`` →
FastAPI surfaces a 500.

All assertions are behavior asserts — each watches a concrete HTTP
status code from the real route, not internal shape.
"""

import asyncio
from pathlib import Path

import pytest

from kohakuterrarium.testing.llm import ScriptEntry
from tests.e2e._lab_harness import (
    OP_TIMEOUT,
    RealLabHost,
    RealLabWorker,
    install_scripted_llm,
)

pytestmark = pytest.mark.timeout(180)


def _write_cfg(root: Path, name: str) -> Path:
    cdir = root / f"creature_{name}"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.yaml").write_text(
        f"name: {name}\n"
        f"system_prompt: 'You are {name}.'\n"
        "model: gpt-4\n"
        "provider: openai\n"
        "input:\n  type: cli\n"
        "output:\n  type: stdout\n",
        encoding="utf-8",
    )
    return cdir


class TestApiLocalRemoteAudit:
    """One method per suspected route bug.

    The fixtures boot a real lab-host + one worker so any
    ``as_engine(service)`` reach on the host engine raises
    ``RuntimeError`` (the multi-node service's ``engine`` property
    explicitly does so) — exactly the path the standalone-only routes
    silently rely on.
    """

    async def test_hotplug_add_creature_into_remote_session_does_not_500(
        self, tmp_path, monkeypatch
    ):
        """``POST /api/sessions/active/{sid}/creatures`` should not 500
        when the target session lives on a worker.

        ``studio.sessions.lifecycle.add_creature`` unconditionally does
        ``engine = as_engine(service)`` (line ~709) and then
        ``engine.list_graphs()``.  In lab-host mode this raises
        ``RuntimeError("lab-host mode runs no host agent engine ...")``
        — the route's ``except (ValueError, KeyError)`` does not catch
        it, so FastAPI surfaces a 500.

        Spawn a creature on worker ``w1`` to get a remote session id,
        then POST another creature into that session — assert the
        route returns something other than 500.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")
        cfg_b = _write_cfg(tmp_path, "bravo")

        async with RealLabHost(tmp_path) as host:
            async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"):
                await asyncio.sleep(0.3)

                # Spawn alpha on w1 to get a remote-hosted session id.
                spawn = await host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_a), "on_node": "w1"},
                )
                assert spawn.status_code == 200, spawn.text
                sid = spawn.json()["session_id"]

                # Hot-plug another creature into that remote session.
                # The route accepts a ``CreatureAdd`` body — fields
                # match the schemas.CreatureAdd contract used by the
                # frontend graph editor.
                resp = await host.http.post(
                    f"/api/sessions/active/{sid}/creatures",
                    json={
                        "name": "bravo",
                        "config_path": str(cfg_b / "config.yaml"),
                        "listen_channels": [],
                        "send_channels": [],
                    },
                    timeout=OP_TIMEOUT,
                )
                # Behavior assertion: any 5xx is the bug. The route is
                # allowed to return 400 ("not implemented for remote
                # sessions") or 200 / 201 (success). as_engine raising
                # RuntimeError lands here as a 500.
                assert resp.status_code < 500, (
                    f"hot-plug add_creature into a worker-hosted session "
                    f"returned {resp.status_code} {resp.text!r} — "
                    f"lifecycle.add_creature calls as_engine(service) which "
                    f"raises RuntimeError on MultiNodeTerrariumService"
                )

    async def test_hotplug_remove_creature_from_remote_session_does_not_500(
        self, tmp_path, monkeypatch
    ):
        """``DELETE /api/sessions/active/{sid}/creatures/{cid}`` should
        not 500 when the session lives on a worker.

        ``studio.sessions.lifecycle.remove_creature`` (line ~786) does
        ``engine = as_engine(service)`` exactly like ``add_creature`` —
        same failure mode.  Tested separately because the route is a
        different verb (DELETE) and a different code path on the active
        router.
        """
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "host-sessions"))
        install_scripted_llm(monkeypatch, script=[ScriptEntry(response="ack")])
        cfg_a = _write_cfg(tmp_path, "alpha")

        async with RealLabHost(tmp_path) as host:
            async with RealLabWorker("w1", host.lab_ws_url, tmp_path / "w1"):
                await asyncio.sleep(0.3)

                spawn = await host.http.post(
                    "/api/sessions/active/creature",
                    json={"config_path": str(cfg_a), "on_node": "w1"},
                )
                assert spawn.status_code == 200, spawn.text
                payload = spawn.json()
                sid = payload["session_id"]
                cid = payload["creatures"][0]["creature_id"]

                resp = await host.http.delete(
                    f"/api/sessions/active/{sid}/creatures/{cid}",
                    timeout=OP_TIMEOUT,
                )
                assert resp.status_code < 500, (
                    f"hot-plug remove_creature on a worker-hosted session "
                    f"returned {resp.status_code} {resp.text!r} — "
                    f"lifecycle.remove_creature calls as_engine(service) "
                    f"which raises RuntimeError on MultiNodeTerrariumService"
                )
