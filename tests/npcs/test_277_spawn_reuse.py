"""#277 (C4): spawn ground keyed by map identity, and candidate reuse per refill.

- The ground cache keeps up to GROUND_STATES map states per spawner, keyed
  by (template, tile table, geometry) identity and the doors' open flags,
  so a door that returns to a state already seen reuses its ground.
- Within one Spawners.process call, a spawner's later spawns reuse its
  first candidate filter and clearance while the blocked set has only
  grown and the players have not moved.

Every refill here is compared with the original per-spawn computation
(frozen in test_spawn_location_equivalence.reference_location): the same
actors on the same tiles with the same RNG draws. spawn.CHECK_REUSE is on
for the whole suite (tests/conftest.py): every cache hit re-walks the
ground and every reuse re-filters, and both must be equal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.npcs import spawn
from karst41.server.npcs.spawn import GROUND_STATES, Spawners
from tests.harness import HeadlessServer
from tests.npcs.test_spawn_location_equivalence import reference_location, zones_with_spawners

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def content() -> Any:
    return load_content(ROOT / "src/karst41/content")


def city(content: Any, players: int = 1) -> HeadlessServer:
    game = HeadlessServer(content, character_state={})
    for _ in range(players):
        game.connect()
    for zone in game.core.world.zones.values():
        zone.activate("test-277", game.clock.now_tick())
    game.step()
    return game


def empty(game: HeadlessServer, zone_id: str) -> None:
    """Every slotted NPC in the zone leaves with a due deadline, so the next process() refills them all."""
    npcs, zone = game.core.npcs, game.core.world.zones[zone_id]
    now = npcs.combat.now()
    for actor in [a for a in zone.actors.values() if a.kind == "npc" and a.behaviour.get("slot")]:
        npcs.spawners.removed(actor, now, deadline=now)
        game.core.world.leave(actor)


def state(game: HeadlessServer, zone_id: str) -> Any:
    zone = game.core.world.zones[zone_id]
    return sorted((a.id, a.position) for a in zone.actors.values() if a.kind == "npc"), zone.rng.getstate()


@pytest.mark.parametrize("zone_id", zones_with_spawners())
def test_a_refill_spawns_the_same_actors_on_the_same_tiles_as_the_original(content, monkeypatch, zone_id) -> None:
    """Cold, warm, and after real door toggles: two identical cities, one on the original computation."""
    reference, current = city(content), city(content)
    original = Spawners.location
    for round_ in range(6):
        for game in (reference, current):
            zone = game.core.world.zones[zone_id]
            if round_ in (2, 4) and zone.doors:  # A real toggle (set_door bumps the geometry revision).
                door = sorted(zone.objects.values(), key=lambda o: o.spec.id)
                door = next(o for o in door if o.spec.tile in zone.doors)
                zone.set_door(door.spec.id, not door.open)
            empty(game, zone_id)
        monkeypatch.setattr(Spawners, "location", lambda self, zone, sp, memo=None: reference_location(self, zone, sp))
        reference.core.npcs.spawners.process(reference.core.world.zones[zone_id], reference.core.npcs.combat.now())
        monkeypatch.setattr(Spawners, "location", original)
        current.core.npcs.spawners.process(current.core.world.zones[zone_id], current.core.npcs.combat.now())
        assert state(current, zone_id) == state(reference, zone_id), (zone_id, round_)


def test_a_door_returning_to_a_seen_state_reuses_its_ground(content, monkeypatch) -> None:
    game = city(content)
    zone = game.core.world.zones["sodium-row"]
    spawners = game.core.npcs.spawners
    regular = next(sp for sp in zone.template.meta.spawners if sp.id == "sodium-regular")
    monkeypatch.setattr(spawn, "CHECK_REUSE", False)  # Count the cache's own walks, not the check's.
    walks = []
    walk = Spawners._walk
    monkeypatch.setattr(Spawners, "_walk", staticmethod(lambda z, sp: walks.append(sp.id) or walk(z, sp)))
    zone.spawn_ground.clear()
    door = next(o for o in zone.objects.values() if o.spec.tile in zone.doors)
    first = door.open
    revisions = []
    for opened in (first, not first, first, not first, first):  # Two map states, five lookups.
        zone.set_door(door.spec.id, opened)
        revisions.append(zone.geometry_revision)
        spawners.location(zone, regular)
    assert revisions == sorted(set(revisions)), "every toggle bumps the revision, which the old key carried"
    assert walks == ["sodium-regular", "sodium-regular"], "one walk per distinct map state"
    assert len(zone.spawn_ground["sodium-regular"]) == 2


def test_the_ground_cache_is_bounded_per_spawner_oldest_first(content) -> None:
    game = city(content)
    zone = game.core.world.zones["vatside-clinics"]
    spawners = game.core.npcs.spawners
    spec = zone.template.meta.spawners[0]
    wall = next(key for key, tile in zone.tiles.items() if not tile.passable)
    geometries = []
    for n in range(GROUND_STATES + 3):
        zone.geometry = {**zone.geometry, (n + 1, 1): wall}  # Replaced, as compose() does.
        geometries.append(zone.geometry)
        spawners.location(zone, spec)
    states = zone.spawn_ground[spec.id]
    assert len(states) == GROUND_STATES
    assert [entry[2] for entry in states] == geometries[-GROUND_STATES:], "the oldest states were evicted"


def test_a_city_run_with_door_toggles_a_layer_change_and_refills_passes_every_reuse_check(content, monkeypatch) -> None:
    """The technical lead's guard run: every hub zone active, doors toggled through set_door, a real
    geometry layer on Clinic Row, and mass refills. CHECK_REUSE re-walks every cache hit and
    re-filters every reuse; the counters show both paths actually ran."""
    assert spawn.CHECK_REUSE
    game = city(content, players=2)
    counts = {"hits": 0, "reuses": 0}
    ground, location = Spawners._ground, Spawners.location

    def counting_ground(self: Spawners, zone: Any, sp: Any) -> Any:
        known = {id(entry[5]) for entry in zone.spawn_ground.get(sp.id, [])}
        result = ground(self, zone, sp)
        counts["hits"] += id(result) in known
        return result

    def counting_location(self: Spawners, zone: Any, sp: Any, memo: Any = None) -> Any:
        counts["reuses"] += bool(memo and sp.id in memo)
        return location(self, zone, sp, memo)
    monkeypatch.setattr(Spawners, "_ground", counting_ground)
    monkeypatch.setattr(Spawners, "location", counting_location)
    clinics = game.core.world.zones["vatside-clinics"]
    layout = next(item.id for item in clinics.template.meta.layouts if item.changes)  # "reserve": a locked door.
    zones = sorted(zid for zid, zone in game.core.world.zones.items() if zone.template.meta.spawners)
    geometries = {clinics.geometry is not None and id(clinics.geometry)}
    for tick in range(400):
        now = game.clock.now_tick()
        if tick == 50:
            clinics.schedule_layout("test-277", layout, now, 120)
        clinics.compose(now)
        geometries.add(id(clinics.geometry))
        if tick % 37 == 5:
            for zid in zones:
                zone = game.core.world.zones[zid]
                doors = sorted((o for o in zone.objects.values() if o.spec.tile in zone.doors), key=lambda o: o.spec.id)
                if doors:
                    door = doors[tick % len(doors)]
                    zone.set_door(door.spec.id, not door.open)
        if tick % 50 == 20:
            for zid in zones:
                empty(game, zid)
        game.step()
    assert len(geometries) >= 3, "the layer replaced Clinic Row's geometry and expired again"
    assert counts["hits"] > 20, f"only {counts['hits']} ground cache hits ran"
    assert counts["reuses"] > 20, f"only {counts['reuses']} candidate reuses ran"
