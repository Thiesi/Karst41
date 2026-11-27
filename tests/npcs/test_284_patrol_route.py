"""#284: a patrol keeps to its authored waypoints (game design 5.5).

Clinic Row's Warden patrol loops (23,3) -> (40,3) -> (40,4) -> (23,4) on the
north street, where patients wander. With the greedy step, a patient on a
waypoint could slide the patrol off the street and into a permanent orbit
under the east clinic block (x 38-40, y 13-32). Patrols now take only steps
that bring them closer, follow the bounded shortest path hunts use when none
is free, and count a body parked on a waypoint as reached once beside it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from tests.harness import HeadlessServer

ROOT = Path(__file__).parents[2]
LOOP = [(23, 3), (40, 3), (40, 4), (23, 4)]


@pytest.fixture(scope="module")
def content() -> Any:
    return load_content(ROOT / "src/karst41/content")


def clinic_row(content: Any) -> tuple[HeadlessServer, Any, Any]:
    game = HeadlessServer(content, character_state={})
    zone = game.core.world.zones["vatside-clinics"]
    zone.activate("test-284", game.clock.now_tick())
    game.step()
    patrol = next(a for a in zone.actors.values() if a.behaviour.get("template") == "npc-warden-patrol")
    return game, zone, patrol


def place(game: HeadlessServer, actor: Any, zone: Any, tile: tuple[int, int]) -> None:
    game.core.world.leave(actor)
    game.core.world.enter(actor, zone.id, tile)


def on_street(actor: Any) -> bool:
    return 2 <= actor.y <= 5 and 22 <= actor.x <= 41


def park(game: HeadlessServer, zone: Any, tile: tuple[int, int]) -> Any:
    """A patient that stands still on tile for the rest of the test."""
    patient = next(a for a in zone.actors.values() if a.behaviour.get("template") == "npc-patient")
    place(game, patient, zone, tile)
    patient.behaviour["pause"] = 10**9  # Wander returns early while paused.
    return patient


def test_a_patient_parked_on_a_waypoint_neither_freezes_nor_lures_the_patrol(content) -> None:
    game, zone, patrol = clinic_row(content)
    place(game, patrol, zone, (39, 3))
    patrol.behaviour.update(waypoint=1)  # Heading for (40,3), then (40,4).
    blocker = park(game, zone, (40, 4))
    reached: list[tuple[int, int]] = []
    for _ in range(300):
        game.step()
        assert blocker.position == (40, 4)
        assert on_street(patrol), patrol.position  # Never lured off the street.
        if patrol.position in LOOP and (not reached or reached[-1] != patrol.position):
            reached.append(patrol.position)
    # It arrived beside the held (40,4), advanced, and went on round the loop.
    assert (23, 4) in reached and (23, 3) in reached, reached


def test_a_patrol_caught_in_the_old_orbit_finds_its_way_back(content) -> None:
    game, zone, patrol = clinic_row(content)
    place(game, patrol, zone, (40, 13))  # Under the east block, where the greedy step looped.
    patrol.behaviour.update(waypoint=1)  # Heading for (40,3), straight through the wall.
    for tick in range(200):
        game.step()
        if on_street(patrol):
            break
    assert on_street(patrol), patrol.position
    for _ in range(600):
        game.step()
        assert on_street(patrol), patrol.position


def unreachable(game: HeadlessServer, zone: Any, patrol: Any, start: tuple[int, int],
                monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """The patrol at start, its next waypoint a wall tile no search can reach."""
    wall = (38, 8)  # The east clinic block's west wall.
    assert not zone.passable(wall)
    place(game, patrol, zone, start)
    content = game.core.loop.content
    template = content.npcs[patrol.behaviour["template"]]
    from dataclasses import replace
    from karst41.content.schema import Behaviour
    monkeypatch.setitem(content.npcs, template.id,
                        replace(template, behaviours=(Behaviour("patrol", {"waypoints": [list(wall)]}),)))
    patrol.behaviour.update(waypoint=0)
    return wall, content.npcs[template.id]


def test_a_failed_search_backs_off_instead_of_running_every_tick(content, monkeypatch) -> None:
    """No path (or past the bound): the miss is cached for ROUTE_RETRY_MOVES greedy moves, keyed on the goal."""
    from karst41.server.npcs.behaviours import ROUTE_RETRY_MOVES
    game, zone, patrol = clinic_row(content)
    unreachable(game, zone, patrol, (30, 3), monkeypatch)
    moves, previous = 0, patrol.position
    for _ in range(200):
        game.step()
        moves += patrol.position != previous
        previous = patrol.position
    searches = game.core.npcs.patrol_routes[patrol.id].searches
    assert 1 <= searches <= 1 + moves // ROUTE_RETRY_MOVES + 1, (searches, moves)


def test_a_boxed_in_patrol_with_no_path_searches_once(content, monkeypatch) -> None:
    """Nowhere to go and no path: one search, then nothing until the goal or blockers change."""
    game, zone, patrol = clinic_row(content)
    unreachable(game, zone, patrol, (1, 1), monkeypatch)  # The street's north-west corner.
    for tile in ((2, 1), (1, 2)):
        park(game, zone, tile)
    for _ in range(100):
        game.step()
        assert patrol.position == (1, 1)
    assert game.core.npcs.patrol_routes[patrol.id].searches == 1


def test_the_route_is_reused_while_nothing_on_it_changes(content) -> None:
    """No re-route storm: in a still crowd the patrol searches about once per leg, not once per tick."""
    game, zone, patrol = clinic_row(content)
    for actor in list(zone.actors.values()):
        if actor is not patrol and actor.kind == "npc":
            actor.behaviour["pause"] = 10**9  # Everyone else stands still.
    place(game, patrol, zone, (23, 3))
    patrol.behaviour.update(waypoint=1)
    game.step()
    route = game.core.npcs.patrol_routes[patrol.id]
    before = route.searches
    legs = 0
    previous = patrol.behaviour.get("waypoint")
    for _ in range(400):
        game.step()
        if patrol.behaviour.get("waypoint") != previous:
            legs += 1
            previous = patrol.behaviour.get("waypoint")
    assert legs >= 8, legs
    assert route.searches - before <= 2 * legs, (route.searches - before, legs)
