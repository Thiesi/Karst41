"""Installed content upgrade, durable zone seeds and the local walk bot."""

import asyncio
from pathlib import Path

from karst41.bot.__main__ import run
from karst41.server.app import Server
from karst41.version import PACKAGE_VERSION
from tests import storage_reader


def test_zone_upgrade_seed_restart_and_walk_bot(scratch_dir: Path, capsys: object) -> None:
    async def scenario() -> None:
        first = Server(scratch_dir, tcp=True)
        await first.start()
        try:
            seeds = dict(storage_reader.meta(scratch_dir / "world.db", "rng_seeds"))
            first.io.mark("meta", "content_package_version", "0.0.1")  # The stop's Close flushes it.
        finally:
            await first.stop()
        # Recreate an old installed content tree missing the new map.
        (scratch_dir / "content/zones/vatside-wake-hall.map").unlink()
        second = Server(scratch_dir, tcp=True, spawn_zone="vatside-wake-hall")
        await second.start()
        try:
            assert storage_reader.meta(scratch_dir / "world.db", "content_package_version") == PACKAGE_VERSION
            assert storage_reader.meta(scratch_dir / "world.db", "rng_seeds") == seeds
            assert {key: zone.seed for key, zone in second.world.zones.items()} == seeds
            async with asyncio.timeout(5):
                await run(scratch_dir, False, 80, 24, steps=4, seed=3)
        finally:
            await second.stop()
    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert "pong " in output and "walk 4 inputs" in output and "bye logout" in output
