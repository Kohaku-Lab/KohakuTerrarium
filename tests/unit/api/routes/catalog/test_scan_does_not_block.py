"""Guard tests: catalog scan routes off-load to the I/O executor.

Pre-fix the ``/api/configs/creatures`` and ``/api/configs/terrariums``
endpoints called ``scan_*_in_dirs`` synchronously on the event loop —
walking + YAML-parsing every config blocked the whole server while
the dashboard's quick-start modal opened.

These tests pin that the scan runs OFF the event loop: substitute a
scan function that sleeps and assert other event-loop work makes
progress while the scan is in flight.
"""

import asyncio
import time


class TestScanOffloadedToExecutor:
    async def test_creatures_scan_does_not_block_event_loop(self, monkeypatch):
        """A slow scan must not stop a concurrent ``await asyncio.sleep``.

        Patch the underlying ``scan_creatures_in_dirs`` to a 0.3s
        ``time.sleep`` (i.e. genuinely blocking).  Trigger the route
        and an independent ``asyncio.sleep(0.1)`` concurrently —
        the sleep should finish well before the scan does because
        the scan runs on the I/O executor, not the loop.
        """
        from kohakuterrarium.api.routes.catalog import creatures_scan as mod

        def _slow_scan(dirs):
            time.sleep(0.3)
            return []

        monkeypatch.setattr(mod, "scan_creatures_in_dirs", _slow_scan)

        loop_alive = []

        async def _ping():
            for _ in range(5):
                await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

        scan_task = asyncio.create_task(mod.list_creature_configs())
        ping_task = asyncio.create_task(_ping())
        await asyncio.gather(scan_task, ping_task)

        # If the scan blocked the loop, ``_ping`` would have had no
        # chance to run during the 0.3s sleep — gaps between its
        # timestamps would balloon.  With the executor off-load, the
        # ping ticks at its 0.02s cadence throughout.
        gaps = [loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)]
        max_gap = max(gaps)
        assert max_gap < 0.15, (
            f"event loop stalled during creatures scan; max ping gap = {max_gap:.3f}s "
            "(scan is running on the loop instead of the I/O executor)"
        )

    async def test_terrariums_scan_does_not_block_event_loop(self, monkeypatch):
        """Same property for the terrariums scan route."""
        from kohakuterrarium.api.routes.catalog import terrariums_scan as mod

        def _slow_scan(dirs):
            time.sleep(0.3)
            return []

        monkeypatch.setattr(mod, "scan_terrariums_in_dirs", _slow_scan)

        loop_alive = []

        async def _ping():
            for _ in range(5):
                await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

        scan_task = asyncio.create_task(mod.list_terrarium_configs())
        ping_task = asyncio.create_task(_ping())
        await asyncio.gather(scan_task, ping_task)

        gaps = [loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)]
        max_gap = max(gaps)
        assert (
            max_gap < 0.15
        ), f"event loop stalled during terrariums scan; max ping gap = {max_gap:.3f}s"

    async def test_models_list_does_not_block_event_loop(self, monkeypatch):
        """Pin /api/configs/models off-load.

        Pre-fix the route called ``list_all`` synchronously on the
        event loop — with many providers + per-preset
        ``_is_available`` disk checks, the model-switcher modal
        stalled at "Loading models" whenever the default pool was
        busy with other ``to_thread`` work.
        """
        from kohakuterrarium.api.routes.catalog import models as mod

        def _slow_list():
            time.sleep(0.3)
            return []

        monkeypatch.setattr(mod, "list_all_models", _slow_list)

        loop_alive = []

        async def _ping():
            for _ in range(5):
                await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

        list_task = asyncio.create_task(mod.list_models())
        ping_task = asyncio.create_task(_ping())
        await asyncio.gather(list_task, ping_task)

        gaps = [loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)]
        max_gap = max(gaps)
        assert max_gap < 0.15, (
            f"event loop stalled during /configs/models; max ping gap = "
            f"{max_gap:.3f}s — modal will hang at 'Loading models'"
        )
