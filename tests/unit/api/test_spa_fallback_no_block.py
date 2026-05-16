"""Guard: ``GET /`` (and the static-file fallback) must not block.

Pre-fix the SPA catch-all in ``api.app._mount_spa`` ran sync
filesystem checks inline on the event loop — ``Path.is_file`` + two
``Path.resolve()`` calls per request.  Under concurrent traffic this
stalled other routes; the user observed "GET / sometimes super
costly" and "blocking as well".

These tests pin two properties:

1. ``GET /`` with empty path takes the index-html short-circuit (no
   filesystem walk).
2. A slow filesystem check (simulated by a sleeping resolver) does
   NOT block concurrent ``asyncio.sleep`` work — the route off-loads
   to the dedicated I/O executor.
"""

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def static_dir(tmp_path):
    """A minimal Vue-build-like static directory."""
    d = tmp_path / "web"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><html>SPA</html>")
    (d / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    assets = d / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("// vue app")
    return d


class TestSpaFallback:
    def test_get_root_serves_index_html(self, static_dir: Path):
        from kohakuterrarium.api.app import _mount_spa

        app = FastAPI()
        _mount_spa(app, static_dir)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"SPA" in resp.content

    def test_get_real_file_serves_it(self, static_dir: Path):
        from kohakuterrarium.api.app import _mount_spa

        app = FastAPI()
        _mount_spa(app, static_dir)
        client = TestClient(app)
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200
        assert resp.content.startswith(b"\x00\x00\x01\x00")

    def test_unknown_path_falls_through_to_index(self, static_dir: Path):
        """Vue Router's client-side paths return the SPA shell."""
        from kohakuterrarium.api.app import _mount_spa

        app = FastAPI()
        _mount_spa(app, static_dir)
        client = TestClient(app)
        resp = client.get("/some/client/route")
        assert resp.status_code == 200
        assert b"SPA" in resp.content

    def test_traversal_attempt_falls_through_to_index(self, static_dir: Path):
        """``..`` segments must not escape ``static_dir``."""
        from kohakuterrarium.api.app import _mount_spa

        # Plant a file OUTSIDE static_dir to confirm it can't be read.
        outside = static_dir.parent / "secret.txt"
        outside.write_text("secret")
        app = FastAPI()
        _mount_spa(app, static_dir)
        client = TestClient(app)
        resp = client.get("/../secret.txt")
        # Either 404 (path normalised by httpx) or 200 with SPA body —
        # never with the secret content.
        assert resp.status_code == 200
        assert b"secret" not in resp.content


class TestSpaFallbackOffLoadsToExecutor:
    async def test_slow_resolve_does_not_block_loop(self, static_dir, monkeypatch):
        """A slow filesystem check on the static path must not stall
        the event loop — the off-load to the I/O executor keeps the
        loop free for other requests.

        Without the off-load (the pre-fix path), ``time.sleep(0.3)``
        inside the resolver would stop every other coroutine for
        300 ms.  With the off-load, an independent ``asyncio.sleep``
        coroutine continues ticking at its 20 ms cadence.
        """
        from kohakuterrarium.api import app as app_mod

        # Patch run_in_io_executor's wrapped resolver path: the
        # function we want to slow is built inside ``_mount_spa``.
        # Instead, slow the underlying ``is_file`` call on Path.
        from pathlib import Path as _RealPath

        original_is_file = _RealPath.is_file

        def _slow_is_file(self):
            time.sleep(0.3)
            return original_is_file(self)

        monkeypatch.setattr(_RealPath, "is_file", _slow_is_file)

        app = FastAPI()
        app_mod._mount_spa(app, static_dir)

        # The slow stat happens INSIDE the route handler, which runs
        # in an executor (httpx TestClient is sync — to test loop
        # blocking we need to drive an asyncio loop directly).  Use
        # the FastAPI app's underlying ASGI callable.
        loop_alive: list[float] = []

        async def _ping() -> None:
            for _ in range(8):
                await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

        async def _request() -> None:
            # Drive the route via the in-process ``router`` so we run
            # on the test's event loop (no thread bridge).  The
            # handler internally awaits ``run_in_io_executor`` for the
            # blocking is_file call.
            from fastapi.routing import APIRoute

            handler = None
            for route in app.routes:
                if isinstance(route, APIRoute) and route.path == "/{full_path:path}":
                    handler = route.endpoint
                    break
            assert handler is not None, "spa_fallback route not registered"
            await handler("favicon.ico")

        await asyncio.gather(_request(), _ping())

        gaps = [loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)]
        max_gap = max(gaps)
        assert max_gap < 0.15, (
            f"event loop stalled during GET /favicon.ico; max ping gap = "
            f"{max_gap:.3f}s — spa_fallback is running its filesystem "
            "check on the event loop instead of the I/O executor"
        )
