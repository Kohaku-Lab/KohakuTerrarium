"""Guard tests: catalog scan routes off-load to the I/O executor.

Walking + YAML-parsing every config on the event loop would block the whole
server while the dashboard's quick-start modal opens. Each test substitutes the
scan with a function that checks the loop stays live while it runs.
"""

import asyncio

from kohakuterrarium.api.routes.catalog.creatures import scan as creatures_mod
from kohakuterrarium.api.routes.catalog import models as models_mod
from kohakuterrarium.api.routes.catalog import terrariums_scan as terrariums_mod
from tests.helpers.loop_witness import LoopWitness


class TestScanOffloadedToExecutor:
    async def test_creatures_scan_does_not_block_event_loop(self, monkeypatch):
        witness = LoopWitness(asyncio.get_running_loop())

        def _scan(dirs):
            witness.observe()
            return []

        monkeypatch.setattr(creatures_mod, "scan_creatures_in_dirs", _scan)
        await creatures_mod.list_creature_configs()
        assert witness.never_blocked, "creatures scan ran on the event loop"

    async def test_terrariums_scan_does_not_block_event_loop(self, monkeypatch):
        witness = LoopWitness(asyncio.get_running_loop())

        def _scan(dirs):
            witness.observe()
            return []

        monkeypatch.setattr(terrariums_mod, "scan_terrariums_in_dirs", _scan)
        await terrariums_mod.list_terrarium_configs()
        assert witness.never_blocked, "terrariums scan ran on the event loop"

    async def test_models_list_does_not_block_event_loop(self, monkeypatch):
        """``list_all`` checks every preset's availability on disk; on the loop the
        model-switcher modal stalls at "Loading models"."""
        witness = LoopWitness(asyncio.get_running_loop())

        def _list():
            witness.observe()
            return []

        monkeypatch.setattr(models_mod, "list_all_models", _list)
        await models_mod.list_models()
        assert witness.never_blocked, "/configs/models ran on the event loop"
