"""#194: Spawners.location picks the same tile with the same RNG draws, cheaper.

The original built `occupied | reserved` once per candidate tile. On the
Row's 92x24 regular region that was 2208 unions per spawn, about 13 ms in
S11. The new code builds it once. This test runs the original, frozen
here, against the new one on the shipped Sodium Row spawners, across many
occupancy and player layouts, each from an identical RNG state.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.npcs.spawn import SPAWN_CLEARANCE
from tests.harness import HeadlessServer


def reference_location(spawners: Any, zone: Any, spawner: Any) -> Any:
    candidates = spawner.region.tiles() if spawner.region else [spawner.tile]
    occupied = {a.position for a in zone.actors.values() if not a.behaviour.get("training")}
    reserved = spawners.reserved(zone)
    candidates = [t for t in candidates if t is not None and zone.can_enter(None, t)
                  and not zone.tiles[zone.tile_id(t)].hazard and t not in occupied | reserved]
    players = [a.position for a in zone.actors.values() if a.kind == "player"]
    if players and candidates:
        clearance = {t: min(max(abs(t[0]-p[0]), abs(t[1]-p[1])) for p in players) for t in candidates}
        preferred = [t for t in candidates if clearance[t] > SPAWN_CLEARANCE]
        farthest = max(clearance.values())
        candidates = preferred or [t for t in candidates if clearance[t] == farthest]
    return zone.rng.choice(candidates) if candidates else None


@pytest.fixture(scope="module")
def row() -> Any:
    content = load_content(Path(__file__).parents[2] / "src/karst41/content")
    game = HeadlessServer(content, character_state={})
    client = game.connect()
    game.step()
    zone = game.core.world.zones["sodium-row"]
    return game, zone, client


def test_the_same_tile_and_the_same_rng_draws_across_layouts(row: Any) -> None:
    game, zone, _ = row
    spawners = game.core.npcs.spawners
    shuffle = random.Random(194)
    actors = list(zone.actors.values())
    wall = next(key for key, tile in zone.tiles.items() if not tile.passable)
    doors = list(zone.doors.values())
    saved = (dict(zone.geometry), zone.geometry_revision, [door.open for door in doors])
    for layout in range(60):
        for actor in actors:  # Move the cast around (positions only; the zone's own state is untouched).
            actor.x, actor.y = shuffle.randrange(zone.template.width), shuffle.randrange(zone.template.height)
        if layout % 7 == 3 and doors:  # A door changes state: the cached ground must follow.
            door = shuffle.choice(doors)
            door.open = not door.open
        if layout % 11 == 5:  # The map changes: new geometry, new revision.
            zone.geometry = {**zone.geometry, **{(shuffle.randrange(3, 95), shuffle.randrange(3, 27)): wall
                                                 for _ in range(40)}}
            zone.geometry_revision += 1
        for spec in zone.template.meta.spawners:
            state = zone.rng.getstate()
            expected = reference_location(spawners, zone, spec)
            after_reference = zone.rng.getstate()
            zone.rng.setstate(state)
            assert spawners.location(zone, spec) == expected, (layout, spec.id)
            assert zone.rng.getstate() == after_reference, "the same RNG draws"
    zone.geometry, zone.geometry_revision = saved[0], saved[1]
    for door, opened in zip(doors, saved[2]):
        door.open = opened


def test_the_rows_regular_region_is_now_cheap(row: Any) -> None:
    """With an S11-like crowd on the Row (stand-ins: only their positions are read)."""
    from types import SimpleNamespace
    game, zone, _ = row
    spawners = game.core.npcs.spawners
    regular = next(sp for sp in zone.template.meta.spawners if sp.id == "sodium-regular")
    crowd = {f"stand-in:{n}": SimpleNamespace(position=(10 + n % 80, 5 + n // 80 * 3), behaviour={}, kind="npc")
             for n in range(60)}
    zone.actors.update(crowd)
    state = zone.rng.getstate()

    def timed(function: Any) -> float:
        zone.rng.setstate(state)
        began = time.perf_counter()
        for _ in range(20):
            function(zone, regular)
        return (time.perf_counter() - began) / 20 * 1000
    old = timed(lambda z, s: reference_location(spawners, z, s))
    new = timed(spawners.location)
    for key in crowd:
        del zone.actors[key]
    print(f"sodium-regular location with 60 actors: {old:.2f} ms -> {new:.2f} ms")
    assert new < old / 2, (old, new)
