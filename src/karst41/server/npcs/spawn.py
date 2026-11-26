"""Bounded spawners. UTC deadlines permit lazy wake and durable death receipts."""

from typing import Any
from collections.abc import Callable

from karst41.content.schema import Spawner, Tile
from karst41.server.npcs.templates import EVALUATION_TICKS, make_actor
from karst41.server.world.actors import Actor
from karst41.server.world.registry import World
from karst41.server.world.zone import ZoneInstance


# Game design 5.5: prefer more than five tiles, then the farthest free tile.
SPAWN_CLEARANCE = 5
# #277 (C4): map states remembered per spawner, oldest evicted first. Each
# entry holds a reference to its geometry dict, so the bound also caps how
# many replaced geometries stay alive.
GROUND_STATES = 8
# Test-only (the #276 delta.CHECK_REUSE pattern): recompute the ground walk
# on every cache hit and the candidate filter on every reuse, and require
# them equal. The test suite turns it on (tests/conftest.py).
CHECK_REUSE = False


class Spawners:
    def __init__(self, world: World, state: dict[str, Any], origin: str) -> None:
        self.world = world
        self.origin = origin
        self.slots: dict[str, dict[str, Any]] = state.get("slots", {})
        self.serial: int = state.get("serial", 0)
        self.revision = 0
        self.enabled_events: set[str] = set()  # S24 supplies active IDs.
        self.extra: Callable[[ZoneInstance], tuple[Spawner, ...]] = lambda zone: ()
        self.policy: Callable[[ZoneInstance, Spawner], tuple[int, float]] = lambda zone, spawner: (spawner.count, spawner.respawn_seconds)
        self.on_spawn: Callable[[Actor, Spawner], None] = lambda actor, spawner: None

    @staticmethod
    def reserved(zone: ZoneInstance) -> set[Tile]:
        meta = zone.template.meta
        return {ex.tile for ex in meta.exits} | {obj.spec.tile for obj in zone.objects.values()} | ({meta.spawn} if meta.spawn else set())

    @staticmethod
    def _walk(zone: ZoneInstance, spawner: Spawner) -> list[Tile]:
        """The region's static ground: passable, hazard-free tiles, in region order."""
        tiles = spawner.region.tiles() if spawner.region else [spawner.tile]
        return [t for t in tiles if t is not None and zone.passable(t) and not zone.tiles[zone.tile_id(t)].hazard]

    def _ground(self, zone: ZoneInstance, spawner: Spawner) -> list[Tile]:
        """The static ground, cached per map state (#194; keyed by identity since #277, C4).

        passable() and the hazard check read exactly the template, the zone's
        tile table, its geometry dict and each door's open flag. The geometry
        dict is only ever replaced, never edited in place, and zone.doors is
        fixed at build; so a state is (template, tiles, geometry) by identity
        plus the door flags, and the spawner by identity. A door that returns
        to a state already seen reuses its ground; the old key carried the
        monotonic geometry_revision, which a door toggle bumps, so no earlier
        state ever matched. References, never id()s. Held on the zone
        instance: a rebuilt instance starts cold.
        """
        doors = tuple(door.open for door in zone.doors.values())
        states = zone.spawn_ground.setdefault(spawner.id, [])
        for template, tiles, geometry, opened, spec, ground in states:
            if (template is zone.template and tiles is zone.tiles and geometry is zone.geometry
                    and opened == doors and spec is spawner):
                if CHECK_REUSE:
                    assert ground == self._walk(zone, spawner), f"stale spawn ground for {spawner.id} in {zone.id}"
                return ground
        ground = self._walk(zone, spawner)
        states.append((zone.template, zone.tiles, zone.geometry, doors, spawner, ground))
        if len(states) > GROUND_STATES:
            del states[0]
        return ground

    def location(self, zone: ZoneInstance, spawner: Spawner,
                 memo: dict[str, tuple[Any, ...]] | None = None) -> Tile | None:
        occupied = {a.position for a in zone.actors.values() if not a.behaviour.get("training")}
        # #194: one union for the whole region, not one per candidate tile.
        blocked = occupied | self.reserved(zone)
        ground = self._ground(zone, spawner)
        players = tuple(a.position for a in zone.actors.values() if a.kind == "player")
        # #277 (C4): within one process() call (memo), a spawner's later spawns
        # reuse its first filter and clearance while nothing they read has
        # changed in a way that could add a candidate: the same ground, a
        # blocked set that has only grown, the same players. Dropping the
        # newly blocked tiles keeps the order, so rng.choice sees the same list.
        entry = memo.get(spawner.id) if memo is not None else None
        clearance: dict[Tile, int] | None = None
        if entry is not None and entry[0] is spawner and entry[1] is ground and entry[2] <= blocked:
            candidates = [t for t in entry[3] if t not in blocked]
            if entry[4] == players:
                clearance = entry[5]
            if CHECK_REUSE:
                assert candidates == [t for t in ground if zone.entry_policy(None, t) and t not in blocked],                     f"stale spawn candidates for {spawner.id} in {zone.id}"
        else:
            candidates = [t for t in ground if zone.entry_policy(None, t) and t not in blocked]
        if players and candidates:
            if clearance is None:
                clearance = {t: min(max(abs(t[0]-p[0]), abs(t[1]-p[1])) for p in players) for t in candidates}
            if memo is not None:
                memo[spawner.id] = (spawner, ground, blocked, candidates, players, clearance)
            preferred = [t for t in candidates if clearance[t] > SPAWN_CLEARANCE]
            if preferred:
                candidates = preferred
            else:  # The farthest tiles are needed only when none clears the preference (#277, C4).
                farthest = max(clearance[t] for t in candidates)
                candidates = [t for t in candidates if clearance[t] == farthest]
        elif memo is not None:
            memo[spawner.id] = (spawner, ground, blocked, candidates, players, clearance)
        return zone.rng.choice(candidates) if candidates else None

    def process(self, zone: ZoneInstance, now: float) -> None:
        memo: dict[str, tuple[Any, ...]] = {}  # #277 (C4): this call's candidate filters.
        for sp in (*zone.template.meta.spawners, *self.extra(zone)):
            if sp.condition == "per_player":
                continue
            if sp.condition and sp.condition.startswith("event:") and sp.condition[6:] not in self.enabled_events:
                continue
            template = self.world.content.npcs[sp.npc]
            count, respawn = self.policy(zone, sp)
            for index in range(count):
                key = f"{zone.id}:{sp.id}:{index}"
                slot = self.slots.setdefault(key, {"until": 0})
                if slot.get("actor") in self.world.actors or now < slot["until"]:
                    continue
                tile = self.location(zone, sp, memo)
                if tile is None:
                    continue
                self.serial += 1
                aid = f"{self.origin}:npc:{self.serial}"
                actor = make_actor(template, aid, {"slot": key, "spawner": sp.id,
                    "home": tile, "region": sp.region, "respawn": respawn,
                    "phase": sum(aid.encode()) % EVALUATION_TICKS})
                slot["actor"] = aid
                self.revision += 1
                self.world.enter(actor, zone.id, tile)
                self.on_spawn(actor, sp)

    def deadline(self, actor: Actor, now: float) -> float:
        zone = self.world.zones[actor.zone]
        spec = next((sp for sp in (*zone.template.meta.spawners, *self.extra(zone))
                     if sp.id == actor.behaviour.get('spawner')), None)
        delay = self.policy(zone, spec)[1] if spec else float(actor.behaviour.get('respawn', 0))
        return now+delay

    def removed(self, actor: Actor, now: float, *, deadline: float | None = None) -> None:
        slot = self.slots.get(str(actor.behaviour.get("slot")))
        if slot is not None:
            slot.update(until=self.deadline(actor, now) if deadline is None else deadline)
            slot.pop("actor", None)
            self.revision += 1

    def record(self) -> dict[str, Any]:
        return {"serial": self.serial, "slots": self.slots}
