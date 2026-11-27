"""#288: every template spawner's ground is walked when the Core is built.

The first refill of a zone (server start, or S11 waking every zone) used to
walk each spawner's region on the wake tick. `Spawners.prewarm` fills C4's
identity-keyed ground cache at construction instead, so the wake reuses it.

- It is pure: no RNG draw, no serial, slot or actor change.
- It makes one entry per template spawner, holding the keyed objects
  themselves (never ids), within GROUND_STATES and evicting nothing.
- A city that sleeps, wakes every zone and runs on ends in the same world
  with and without it (actors, every zone's RNG, serial and slots), also
  when a door changes the map state before the wake so the entries miss.
- With it, the wake tick walks no ground at all.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.npcs import spawn
from karst41.server.npcs.spawn import GROUND_STATES, Spawners
from tests.harness import HeadlessServer

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def content() -> Any:
    return load_content(ROOT / "src/karst41/content")


def template_spawners(zone: Any) -> list[Any]:
    return [sp for sp in zone.template.meta.spawners if sp.condition != "per_player"]


def snapshot(game: HeadlessServer) -> Any:
    world, spawners = game.core.world, game.core.npcs.spawners
    actors = sorted((a.id, a.zone, a.position, a.health, a.target, a.stance, json.dumps(a.behaviour, sort_keys=True, default=str))
                    for a in world.actors.values())
    return (actors, [(zid, zone.rng.getstate()) for zid, zone in sorted(world.zones.items())],
            spawners.serial, deepcopy(spawners.slots), spawners.revision)


def fingerprint(game: HeadlessServer) -> str:
    return hashlib.sha256(repr(snapshot(game)).encode()).hexdigest()


def test_prewarm_draws_nothing_and_changes_no_state(content) -> None:
    game = HeadlessServer(content, character_state={})
    game.connect()
    game.step(5)
    zones = game.core.world.zones.values()
    for zone in zones:
        zone.spawn_ground.clear()
    before = snapshot(game)
    game.core.npcs.spawners.prewarm(zones)
    assert snapshot(game) == before
    assert sum(len(zone.spawn_ground) for zone in zones) > 20, "the city's spawners were walked"


def test_construction_leaves_one_strongly_keyed_entry_per_template_spawner(content) -> None:
    game = HeadlessServer(content, character_state={})
    walked = 0
    for zone in game.core.world.zones.values():
        doors = tuple(door.open for door in zone.doors.values())
        for sp in template_spawners(zone):
            states = zone.spawn_ground[sp.id]
            assert len(states) == 1 <= GROUND_STATES, (zone.id, sp.id)
            template, tiles, geometry, opened, spec, ground = states[0]
            # The keyed objects themselves, so no id can be reused while the entry lives.
            assert template is zone.template and tiles is zone.tiles and geometry is zone.geometry and spec is sp
            assert opened == doors
            assert ground == Spawners._walk(zone, sp)
            walked += 1
        assert set(zone.spawn_ground) == {sp.id for sp in template_spawners(zone)}, zone.id
    assert walked > 20


def sleep_and_wake(content: Any, *, prewarm: bool, toggle: bool, monkeypatch: pytest.MonkeyPatch) -> tuple[str, list[int]]:
    """A city that runs with every zone dormant, then wakes them all at once (as S11 does) and runs on.
    Returns the final world's fingerprint and the ground walks per tick from the wake on."""
    walks: list[str] = []
    walk = Spawners._walk
    with monkeypatch.context() as patch:
        if not prewarm:
            patch.setattr(Spawners, "prewarm", lambda self, zones: None)
        patch.setattr(Spawners, "_walk", staticmethod(lambda zone, sp: walks.append(sp.id) or walk(zone, sp)))
        game = HeadlessServer(content, character_state={})
        game.connect()
        game.step(30)
        if toggle:  # The map state changes before the wake, so pre-warmed entries miss for these zones.
            toggled = 0
            for zone in game.core.world.zones.values():
                doors = sorted((o for o in zone.objects.values() if o.spec.tile in zone.doors), key=lambda o: o.spec.id)
                if doors and template_spawners(zone):
                    zone.set_door(doors[0].spec.id, not doors[0].open)
                    toggled += 1
            assert toggled >= 3, toggled
        for zone in game.core.world.zones.values():
            zone.activate("test-288", game.clock.now_tick())
        per_tick = []
        for _ in range(40):
            walks.clear()
            game.step()
            per_tick.append(len(walks))
        assert sum(1 for a in game.core.world.actors.values() if a.kind == "npc") > 40, "the wake refilled the city"
        return fingerprint(game), per_tick


@pytest.mark.parametrize("toggle", [False, True], ids=["same-map", "door-toggled"])
def test_a_wake_ends_in_the_same_world_with_and_without_prewarm(content, monkeypatch, toggle) -> None:
    """CHECK_REUSE stays on (tests/conftest.py): every cache hit re-walks and must be equal."""
    assert spawn.CHECK_REUSE
    with_prewarm, _ = sleep_and_wake(content, prewarm=True, toggle=toggle, monkeypatch=monkeypatch)
    without, _ = sleep_and_wake(content, prewarm=False, toggle=toggle, monkeypatch=monkeypatch)
    assert with_prewarm == without


def test_the_wake_tick_walks_no_ground_after_prewarm(content, monkeypatch) -> None:
    monkeypatch.setattr(spawn, "CHECK_REUSE", False)  # Count the cache's own walks, not the check's.
    _, warmed = sleep_and_wake(content, prewarm=True, toggle=False, monkeypatch=monkeypatch)
    _, cold = sleep_and_wake(content, prewarm=False, toggle=False, monkeypatch=monkeypatch)
    assert cold[0] > 20, f"without the pre-warm the wake tick walks every spawner's ground ({cold[0]})"
    assert warmed == [0] * len(warmed), warmed
