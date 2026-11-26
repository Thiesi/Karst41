"""#277 (C5): hostile's static facts, cached and checked by identity.

Npcs.hostile, covered_unarmed, posed_spawner, reaction_group and
shared_crowd now read per-template facts (covered and unarmed, hostile_all)
and per-zone spawner lookups from caches. Each entry is used only while
the objects it was computed from (the template and its weapon item, the
zone's template) are the same objects. These tests compare every result
with the original code, frozen here, across a populated city, and show
that replacing a zone template (as housing does) or a content template
takes effect at once.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.server.npcs.templates import relation, weapon
from tests.character_helpers import create, place
from tests.harness import HeadlessServer


# --- The original code, frozen (main 7e477561) ------------------------------

def covered_unarmed(npcs: Any, actor: Any) -> bool:
    template = npcs.content.npcs.get(actor.behaviour.get('template'))
    return bool(template and template.presents and weapon(npcs.content, template) is None)


def posed_spawner(npcs: Any, actor: Any) -> Any:
    if not covered_unarmed(npcs, actor):
        return None
    spawners = npcs.world.zones[actor.zone].template.meta.spawners
    own = next((sp for sp in spawners if sp.id == actor.behaviour.get('spawner')), None)
    return next((sp for sp in spawners if own and sp.id == own.poses_as), None)


def reaction_group(npcs: Any, actor: Any) -> str:
    posed = posed_spawner(npcs, actor)
    return posed.id if posed else str(actor.behaviour.get('spawner'))


def shared_crowd(npcs: Any, actor: Any) -> bool:
    group = reaction_group(npcs, actor)
    return any(sp.poses_as == group for sp in npcs.world.zones[actor.zone].template.meta.spawners)


def hostile(npcs: Any, actor: Any, other: Any) -> bool:
    if actor is other or actor.kind == "player" or actor.zone != other.zone:
        return False
    if actor.behaviour.get("training"):
        return actor.owner == other.id and not actor.behaviour.get("inert")
    group = actor.behaviour.get("spawner")
    if group and group == other.behaviour.get("spawner"):
        return False
    template = npcs.content.npcs.get(actor.behaviour.get("template"))
    if template is None:
        return False
    now = npcs.combat.now()
    if npcs.provocations.get((actor.zone, reaction_group(npcs, actor), other.id), 0) > now:
        return True
    if covered_unarmed(npcs, actor):
        return False
    player = npcs.game.players.get(other.id)
    if actor.faction == "wardens" and player:
        if other.marked or (player.warden_heat_until and datetime.fromisoformat(player.warden_heat_until).timestamp() > now):
            return True
    if npcs.world.zones[actor.zone].template.meta.truce:
        return False
    explicit = any(b.params.get("hostile_all") for b in template.behaviours)
    return explicit or relation(npcs.content, actor.faction, other.faction) == "H"


# --- Tests -------------------------------------------------------------------

def compare(npcs: Any) -> int:
    checked = 0
    for zone in npcs.world.zones.values():
        actors = sorted(zone.actors.values(), key=lambda a: a.id)
        for actor in actors:
            assert npcs.covered_unarmed(actor) == covered_unarmed(npcs, actor), actor.id
            assert npcs.posed_spawner(actor) is posed_spawner(npcs, actor), actor.id
            assert npcs.reaction_group(actor) == reaction_group(npcs, actor), actor.id
            if actor.kind == "npc":
                assert npcs.shared_crowd(actor) == shared_crowd(npcs, actor), actor.id
            for other in actors:
                assert npcs.hostile(actor, other) == hostile(npcs, actor, other), (actor.id, other.id)
                checked += 1
    return checked


@pytest.fixture
def game() -> HeadlessServer:
    game = HeadlessServer(load_content(Path(__file__).parents[2] / "src/karst41/content"), character_state={})
    for n, (zone, tile) in enumerate([("sodium-row", (16, 5)), ("core-plaza", (20, 6)), ("sodium-tin-halo", (5, 5))]):
        client = game.connect({"user_id": n + 1, "handle": f"Fact{n}", "node_name": "t"})
        create(game, client, archetype=n % 2, name=f"Fact{n}", card="none")
        game.commit_characters()
        game.commit_commerce()
        place(game, client, zone, tile)
        game.commit_commerce()
    for zone in game.core.world.zones.values():
        zone.activate("test-277", game.clock.now_tick())
    game.step(20)
    return game


def test_every_fact_matches_the_original_through_a_provoked_city(game) -> None:
    npcs = game.core.npcs
    callers = [a for a in game.core.world.actors.values() if a.kind == "player"]
    checked = 0
    for tick in range(240):
        if tick == 30:  # Provocations make reaction groups (and posed crowds) matter.
            for caller in callers:
                zone = game.core.world.zones[caller.zone]
                victim = next((a for a in sorted(zone.actors.values(), key=lambda a: a.id) if a.kind == "npc"), None)
                if victim is not None:
                    npcs.provoked(caller, victim)
        game.step()
        if tick % 20 == 0:
            checked += compare(npcs)
    assert checked > 5000
    assert any(npcs.covered_unarmed(a) for a in game.core.world.actors.values() if a.kind == "npc"), \
        "the city has covered, unarmed NPCs (the posed-crowd branches ran)"


def test_a_replaced_zone_template_takes_effect_at_once(game) -> None:
    """Housing replaces zone.template at runtime; the spawner lookups must follow."""
    npcs = game.core.npcs
    compare(npcs)  # Fill the caches.
    for zone in game.core.world.zones.values():
        spawners = zone.template.meta.spawners
        if not any(sp.poses_as for sp in spawners):
            continue
        # The same zone with no declared poses: every covered NPC stops posing.
        meta = replace(zone.template.meta, spawners=tuple(replace(sp, poses_as=None) for sp in spawners))
        zone.template = replace(zone.template, meta=meta)
        compare(npcs)
        assert not any(npcs.posed_spawner(a) for a in zone.actors.values() if a.kind == "npc")
        return
    pytest.skip("no shipped zone declares a posed crowd")


def test_a_replaced_content_template_takes_effect_at_once(game) -> None:
    npcs = game.core.npcs
    compare(npcs)
    actor = next(a for a in sorted(game.core.world.actors.values(), key=lambda a: a.id)
                 if a.kind == "npc" and not npcs.covered_unarmed(a) and npcs.content.npcs[a.behaviour["template"]].weapon)
    template_id = actor.behaviour["template"]
    original = npcs.content.npcs[template_id]
    try:
        npcs.content.npcs[template_id] = replace(original, presents=original.presents or "a stand-in cover",
                                                 weapon=None, attack=None)
        assert covered_unarmed(npcs, actor) and npcs.covered_unarmed(actor)
        compare(npcs)
    finally:
        npcs.content.npcs[template_id] = original
    assert not npcs.covered_unarmed(actor)
    compare(npcs)
