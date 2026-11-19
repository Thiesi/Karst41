"""S44 part 2 (#187): the keeper's guards pursue to the last seen position.

Main's shipped Row is open ground: it has no bar alcoves yet. These tests
replace only the in-memory Row grid with the round 3 demo map that showed
the defect (fixtures/issue187-row-demo.map, SHA-256 68897e1e...ca14f,
byte-for-byte the #187 diagnosis fixture at 087aa96). No alcove geometry
ships in this slice.
"""

from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from karst41.content.formats import parse_map
from karst41.content.loader import load_content
from karst41.server.npcs import behaviours
from karst41.server.npcs.behaviours import ROUTE_NODES, Route, route_toward, search
from karst41.server.npcs.templates import LOST_SIGHT_SECONDS, make_actor
from karst41.server.protocol import Frame
from karst41.server.rules.combat import ticks
from tests.character_helpers import create, place
from tests.harness import HeadlessServer

FIXTURE = Path(__file__).with_name("fixtures") / "issue187-row-demo.map"
START, HIDDEN, POST = (10, 6), (9, 6), (15, 9)  # Inside Upstairs.
DOORWAY = {(13, 8), (14, 8)}  # Upstairs' doorway.


def advance(game: HeadlessServer, client: Any, count: int = 1) -> None:
    for _ in range(count):
        game.step()
        game.commit_commerce()
        game.frames_for(client)
        client.session.acknowledge(client.session.view_revision)


def row_world(repo_root: Path) -> tuple[Any, Any]:
    content = load_content(repo_root/"src/karst41/content")
    content.zones = {key: replace(zone, meta=replace(zone.meta, spawners=()))
                     for key, zone in content.zones.items()}
    grid, problems = parse_map(FIXTURE.read_text(encoding="utf-8"), "issue187 Row fixture",
                               {tile.char: tile.id for tile in content.tiles.values()})
    assert not problems
    content.zones["sodium-row"] = replace(content.zones["sodium-row"], grid=grid)
    game = HeadlessServer(content, character_state={})
    client = game.connect()
    create(game, client, archetype=0, card="none")
    return game, client


def scene(repo_root: Path) -> tuple[Any, ...]:
    """The #187 reproduction: a real shot from inside Upstairs, then a step out of sight."""
    game, client = row_world(repo_root)
    content = game.core.loop.content
    player = game.core.characters.players[client.session.player_id]
    player.progress["wake_combat"] = "skipped"
    place(game, client, "sodium-row", START)
    source = game.core.world.actors[player.id]
    zone = game.core.world.zones["sodium-row"]
    regular = make_actor(content.npcs["npc-sodium-regular"], "issue187-regular",
                         {"spawner": "issue187-regular", "home": (11, 6), "phase": 0, "pause": 100000})
    hunter = make_actor(content.npcs["npc-sable-enforcer"], "issue187-enforcer",
                        {"spawner": "issue187-enforcer", "home": POST, "phase": 0})
    game.core.world.enter(regular, zone.id, (11, 6))
    game.core.world.enter(hunter, zone.id, POST)
    fire = Frame("intent", {"name": "fire", "args": {"target": regular.id}})
    client.send(fire)
    advance(game, client, 22)
    client.send(fire)
    advance(game, client)
    for _ in range(40):
        if hunter.cooldowns.get("combat", 0) > game.clock.now_tick():
            break
        advance(game, client)
    assert hunter.target == source.id and hunter.behaviour["state"] == "hunt"
    assert hunter.behaviour["last_position"] == START
    return game, client, source, hunter, zone


def shortest(zone: Any, hunter: Any, start: tuple[int, int], target: tuple[int, int],
             avoid: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Read-only oracle, independent of the production search."""
    queue, parents = deque([start]), {start: None}
    blocked = {actor.position for actor in zone.actors.values() if actor is not hunter}
    while queue:
        current = queue.popleft()
        if current == target:
            path = []
            while current is not None:
                path.append(current)
                current = parents[current]
            return path[::-1]
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            tile = (current[0] + dx, current[1] + dy)
            if tile not in parents and tile not in avoid | blocked and zone.can_enter(hunter, tile):
                parents[tile] = current
                queue.append(tile)
    return []


@pytest.mark.parametrize("clear_avoid", [False, True], ids=["ordinary-avoid", "empty-avoid"])
def test_187_hunter_reaches_bar_trail_before_lost_sight_timeout(repo_root, monkeypatch, clear_avoid):
    """t6's reproduction (087aa96), now passing."""
    game, client, source, hunter, zone = scene(repo_root)
    if clear_avoid:
        monkeypatch.setattr(game.core.npcs.spawners, "reserved", lambda zone: set())
    avoid = game.core.npcs.spawners.reserved(zone)
    last_seen = hunter.behaviour["last_seen"]
    client.send(Frame("intent", {"name": "move", "args": {"dir": "left"}}))
    advance(game, client)
    assert source.position == HIDDEN and not game.core.combat.visible(hunter, source)
    assert len(shortest(zone, hunter, POST, START, avoid)) - 1 == 8
    trail = [hunter.position]
    reacquired = False
    while game.clock.now_tick() < last_seen + ticks(LOST_SIGHT_SECONDS) + 5:
        if game.core.combat.visible(hunter, source) or hunter.position == START:
            reacquired = True
            break
        advance(game, client)
        if hunter.position != trail[-1]:
            trail.append(hunter.position)
    assert reacquired, trail
    assert (13, 8) in trail or (14, 8) in trail  # Through the bar's doorway.


def guard_in(game: Any, tile: tuple[int, int], target: Any, last: tuple[int, int]) -> Any:
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "bar-guard",
                        {"spawner": "bar-guard", "home": tile, "phase": 0})
    game.core.world.enter(hunter, "sodium-row", tile)
    hunter.target = target.id
    hunter.behaviour.update(state="hunt", last_seen=game.clock.now_tick(), last_position=last)
    game.core.npcs.provocations["sodium-row", "bar-guard", target.id] = game.core.combat.now() + 300
    return hunter


def test_s44_round4_guard_follows_a_hunted_player_out_of_a_bar(repo_root):
    """Round 4: the player leaves the bar; the guard goes out after them."""
    game, client = row_world(repo_root)
    player = game.core.characters.players[client.session.player_id]
    player.progress["wake_combat"] = "skipped"
    street = (24, 11)
    place(game, client, "sodium-row", (40, 13))  # Gone on, out of the guard's sight.
    source = game.core.world.actors[player.id]
    hunter = guard_in(game, (9, 6), source, street)
    assert not game.core.combat.visible(hunter, source)
    trail = [hunter.position]
    for _ in range(ticks(LOST_SIGHT_SECONDS) - 10):
        advance(game, client)
        if hunter.position != trail[-1]:
            trail.append(hunter.position)
        if hunter.position == street or game.core.combat.visible(hunter, source):
            break
    assert hunter.position == street or game.core.combat.visible(hunter, source), trail
    assert {(13, 8), (14, 8)} & set(trail)
    assert hunter.zone == "sodium-row"


def test_s44_route_makes_no_rng_draws_and_recomputes_only_on_change(repo_root):
    game, client = row_world(repo_root)
    zone = game.core.world.zones["sodium-row"]
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "router",
                        {"spawner": "router", "home": (9, 6), "phase": 0})
    game.core.world.enter(hunter, "sodium-row", (9, 6))

    class Counting:
        draws = 0

        def __getattr__(self, name: str) -> Any:
            Counting.draws += 1
            return getattr(real, name)
    real, zone.rng = zone.rng, Counting()
    route, tick = Route(), game.clock.now_tick()
    goal = (24, 11)
    while hunter.position != goal:
        tick += 10
        assert route_toward(hunter, zone, goal, tick, route, set())
    assert Counting.draws == 0
    assert route.searches == 1  # Walking the path never recomputes it.
    tick += 10
    route_toward(hunter, zone, (30, 11), tick, route, set())
    assert route.searches == 2  # A new goal does.
    tick += 10
    route_toward(hunter, zone, (30, 11), tick, route, {(40, 11)})
    assert route.searches == 3  # So does a change in the blocking set.
    zone.rng = real


def test_s44_route_never_steps_on_a_zone_exit(repo_root):
    content = load_content(repo_root/"src/karst41/content")  # Main's shipped Row and exits.
    content.zones = {key: replace(zone, meta=replace(zone.meta, spawners=()))
                     for key, zone in content.zones.items()}
    game = HeadlessServer(content, character_state={})
    zone = game.core.world.zones["sodium-row"]
    exits = {ex.tile for ex in zone.template.meta.exits}
    assert (2, 2) in exits  # The Neon Stair.
    hunter = make_actor(content.npcs["npc-sable-enforcer"], "edge",
                        {"spawner": "edge", "home": (2, 3), "phase": 0})
    game.core.world.enter(hunter, "sodium-row", (2, 3))
    path = search(hunter, zone, (2, 1), set(), frozenset())
    assert path and not exits & set(path)
    assert len(path) - 1 == 4  # Around the exit, not through it.


def test_s44_route_search_is_bounded(repo_root, monkeypatch):
    game, _ = row_world(repo_root)
    zone = game.core.world.zones["sodium-row"]
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "far",
                        {"spawner": "far", "home": (2, 10), "phase": 0})
    game.core.world.enter(hunter, "sodium-row", (2, 10))
    calls = 0
    original = zone.can_enter

    def counted(actor: Any, tile: tuple[int, int]) -> bool:
        nonlocal calls
        calls += 1
        return original(actor, tile)
    monkeypatch.setattr(zone, "can_enter", counted)
    # An unreachable goal (inside solid rock) explores at most the bound.
    assert search(hunter, zone, (95, 28), set(), frozenset()) == []
    assert calls <= 4 * ROUTE_NODES


def test_s44_lost_sight_give_up_is_unchanged(repo_root):
    game, client = row_world(repo_root)
    player = game.core.characters.players[client.session.player_id]
    player.progress["wake_combat"] = "skipped"
    place(game, client, "sodium-row", (60, 13))
    source = game.core.world.actors[player.id]
    hunter = guard_in(game, (9, 6), source, (10, 6))  # Already at its goal's door.
    seen = hunter.behaviour["last_seen"]
    while game.clock.now_tick() < seen + ticks(LOST_SIGHT_SECONDS) - 5:
        advance(game, client)
        assert hunter.target == source.id
    advance(game, client, 10)
    assert hunter.target is None


def test_s44_unreachable_goal_searches_once_then_steps_greedily(repo_root):
    game, _ = row_world(repo_root)
    zone = game.core.world.zones["sodium-row"]
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "stuck",
                        {"spawner": "stuck", "home": (2, 10), "phase": 0})
    game.core.world.enter(hunter, "sodium-row", (2, 10))
    route, tick = Route(), game.clock.now_tick()
    moves = 0
    while moves < behaviours.ROUTE_RETRY_MOVES:
        tick += 10
        moves += route_toward(hunter, zone, (95, 28), tick, route, set())
    assert route.searches == 1 and route.path == []  # One search per 8 greedy moves.
    tick += 10
    route_toward(hunter, zone, (95, 28), tick, route, set())
    assert route.searches == 2


def test_s44_far_hunter_searches_again_from_closer(repo_root):
    """Past the bound the hunter steps greedily, then finds the doorway from closer."""
    content = load_content(repo_root/"src/karst41/content")
    content.zones = {key: replace(zone, meta=replace(zone.meta, spawners=()))
                     for key, zone in content.zones.items()}
    grid, _ = parse_map(FIXTURE.read_text(encoding="utf-8"), "fx", {t.char: t.id for t in content.tiles.values()})
    content.zones["sodium-row"] = replace(content.zones["sodium-row"], grid=grid)
    game = HeadlessServer(content, character_state={})
    zone = game.core.world.zones["sodium-row"]
    hunter = make_actor(content.npcs["npc-sable-enforcer"], "far", {"spawner": "far", "home": (80, 12), "phase": 0})
    game.core.world.enter(hunter, "sodium-row", (80, 12))
    route, tick = Route(), game.clock.now_tick()
    for _ in range(400):
        if hunter.position == START:
            break
        tick += 10
        route_toward(hunter, zone, START, tick, route, set())
    assert hunter.position == START, (hunter.position, route.searches)
    assert route.searches <= 12


def test_s44_route_passes_through_an_open_door_object(repo_root):
    """Spawn reservations list every object tile; an open door is still a way through."""
    from karst41.content.schema import ObjectSpec
    from karst41.server.world.zone import ObjectState
    game, _ = row_world(repo_root)
    zone = game.core.world.zones["sodium-row"]
    doors = []
    for index, tile in enumerate(sorted(DOORWAY)):
        door = ObjectState(ObjectSpec(f"bar-door-{index}", tile, "door", {"open": True}), True)
        zone.objects[door.spec.id] = door
        zone.doors[tile] = door
        doors.append(door)
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "via-door",
                        {"spawner": "via-door", "home": POST, "phase": 0})
    game.core.world.enter(hunter, "sodium-row", POST)
    avoid = game.core.npcs.spawners.reserved(zone)
    assert DOORWAY <= avoid
    path = search(hunter, zone, START, avoid, frozenset())
    assert path and DOORWAY & set(path)
    for door in doors:
        door.open = False
    assert search(hunter, zone, START, avoid, frozenset()) == []  # The bar is shut.


# #263: the S44 acceptance's bar scene (scripts/accept_s44_netbbs.py) on
# main's live Row with only Upstairs' walls copied in, as its inject_bar does.
ACCEPTANCE = Path(__file__).parents[2]/"scripts"/"accept_s44_netbbs.py"
BAR = (6, 4, 20, 8)
SEEN = (13, 6)
STAGE, PURSUER_AT = (10, 11), (16, 12)
ENFORCER_POST = (43, 20)  # The Row's enforcer spawner.
MOVES = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}


def acceptance_row(repo_root: Path) -> tuple[Any, Any]:
    content = load_content(repo_root/"src/karst41/content")
    content.zones = {key: replace(zone, meta=replace(zone.meta, spawners=()))
                     for key, zone in content.zones.items()}
    fixture, problems = parse_map(FIXTURE.read_text(encoding="utf-8"), "S44 bar fixture",
                                  {tile.char: tile.id for tile in content.tiles.values()})
    assert not problems
    row = content.zones["sodium-row"]
    grid = [list(line) for line in row.grid]
    x0, y0, x1, y1 = BAR
    for y in range(y0, y1+1):
        for x in range(x0, x1+1):
            if not content.tiles[fixture[y][x]].passable and content.tiles[grid[y][x]].passable:
                grid[y][x] = fixture[y][x]
    content.zones["sodium-row"] = replace(row, grid=grid)
    game = HeadlessServer(content, character_state={})
    client = game.connect()
    create(game, client, archetype=3, card="none")
    game.core.characters.players[client.session.player_id].progress["wake_combat"] = "skipped"
    return game, client


def pursuer(game: Any, tile: tuple[int, int], target: Any) -> Any:
    """One keeper guard hunting the caller with fire held, as in the scene."""
    hunter = make_actor(game.core.loop.content.npcs["npc-sable-enforcer"], "acceptance-guard",
                        {"spawner": "acceptance-guard", "home": ENFORCER_POST, "phase": 0})
    game.core.world.enter(hunter, "sodium-row", tile)
    hunter.target = target.id
    hunter.behaviour.update(state="hunt", last_seen=game.clock.now_tick(), last_position=target.position)
    hunter.cooldowns["weapon"] = 10**12
    game.core.npcs.provocations["sodium-row", "acceptance-guard", target.id] = game.core.combat.now() + 300
    return hunter


def step_to(game: Any, client: Any, body: Any, goal: tuple[int, int]) -> None:
    """One tick of the acceptance probe's walk: a 4-way shortest path round other bodies."""
    zone = game.core.world.zones[body.zone]
    if body.position != goal and game.clock.now_tick() >= body.cooldowns.get("move", 0):
        occupied = {a.position for a in zone.actors.values() if a is not body}
        start = body.position
        previous, pending = {start: None}, deque([start])
        while pending and goal not in previous:
            x, y = pending.popleft()
            for tile in ((x+1, y), (x, y+1), (x-1, y), (x, y-1)):
                if tile not in previous and tile not in occupied and zone.passable(tile):
                    previous[tile] = (x, y)
                    pending.append(tile)
        step = goal
        while previous[step] != start:
            step = previous[step]
        client.send(Frame("intent", {"name": "move", "args": {"dir": MOVES[step[0]-start[0], step[1]-start[1]]}}))
    advance(game, client)


def sees_at_seen(game: Any, hunter: Any, body: Any) -> bool:
    """The acceptance's wait before the step out of sight."""
    return (hunter.target == body.id and tuple(hunter.behaviour.get("last_position", ())) == SEEN
            and game.core.combat.visible(hunter, body))


def test_263_tiles_match_the_acceptance_script():
    text = ACCEPTANCE.read_text(encoding="utf-8")
    assert "SEEN, INSIDE, DOORWAY = (13, 6), (8, 6)" in text
    assert f"STAGE, PURSUER_AT = {STAGE}, {PURSUER_AT}" in text
    assert f"BAR = {BAR}" in text


def test_263_a_guard_north_of_the_bar_gives_up_and_never_sees_the_doorway(repo_root):
    """The flake's shape, as specified (#187, S44): pursue to the last seen
    tile, give up after 20 s. Breach north-west of the bar, guard north of
    it: the walk in rounds the west corner out of its sight."""
    game, client = acceptance_row(repo_root)
    player = game.core.characters.players[client.session.player_id]
    place(game, client, "sodium-row", (6, 3))
    body = game.core.world.actors[player.id]
    hunter = pursuer(game, (16, 3), body)
    start, sighted, gave_up = game.clock.now_tick(), None, None
    while game.clock.now_tick() < start + ticks(40):
        step_to(game, client, body, SEEN)
        hunter.cooldowns["weapon"] = 10**12
        assert not sees_at_seen(game, hunter, body)
        if gave_up is None and hunter.target is None:
            gave_up = game.clock.now_tick()
        elif gave_up is None:
            sighted = int(hunter.behaviour['last_seen'])  # Its own last sighting.
    assert body.position == SEEN
    # Evaluation runs every 5 ticks, so the give-up lands within one phase.
    assert gave_up is not None and 0 <= gave_up - (sighted + ticks(LOST_SIGHT_SECONDS)) <= 5
    assert hunter.position == ENFORCER_POST  # Home along the north side, past no line into the bar.


def test_263_the_scene_start_south_of_the_doorway_sees_the_caller_at_once(repo_root):
    """The acceptance's declared start: caller at STAGE, pursuer at PURSUER_AT."""
    game, client = acceptance_row(repo_root)
    player = game.core.characters.players[client.session.player_id]
    place(game, client, "sodium-row", STAGE)
    body = game.core.world.actors[player.id]
    hunter = pursuer(game, PURSUER_AT, body)
    arrived = None
    for _ in range(ticks(20)):
        step_to(game, client, body, SEEN)
        hunter.cooldowns["weapon"] = 10**12
        if body.position == SEEN and arrived is None:
            arrived = game.clock.now_tick()
        if arrived is not None and (sees_at_seen(game, hunter, body) or game.clock.now_tick() > arrived + ticks(1)):
            break
    assert arrived is not None and sees_at_seen(game, hunter, body)
    assert game.clock.now_tick() - arrived <= ticks(1)
