"""S47, slice §5 and ruling (b): pending combat across a held commit.

A Sable enforcer hunts a Kestrel runner. The same fight runs twice: once
with every receipt settling before the next step (normal operation, about
1 ms per commit), and once with an unrelated receipt held across the
enforcer's next turn (a stalled disk). Every shot the enforcer attempts and
every hit that lands is traced with its tick.

The technical lead asked for this trace before any rule is chosen: if the
held run shows a burst, a dropped shot or a doubled cooldown, it goes to
the technical lead first.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.content.schema import Region, Spawner
from karst41.server.items import make_item
from karst41.server.npcs.engine import weapon as npc_weapon
from karst41.server.protocol import Frame
from karst41.server.rules import combat as rules
from tests.character_helpers import place
from tests.server.test_s47_writer import Rig

HOLD_TICKS = 40  # Four seconds: longer than the enforcer's weapon cooldown.


@pytest.fixture(scope="module")
def content() -> Any:
    tree = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree.walls = None
    tree.zones = {key: replace(z, meta=replace(z.meta, spawners=())) for key, z in tree.zones.items()}
    return tree


def fight(tmp_path: Path, content: Any, monkeypatch: pytest.MonkeyPatch, hold: bool) -> list[tuple[int, str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    rig = Rig(tmp_path, content)
    game, client = rig.game, rig.clients[0]
    player = rig.player()
    player.progress["wake_combat"] = "skipped"
    place(game, client, "vatside-clinics", (20, 3))
    zone = game.core.world.zones["vatside-clinics"]
    spec = Spawner("test-group", "npc-sable-enforcer", 1, 60, region=Region(24, 3, 1, 1), condition="always")
    zone.template = replace(zone.template, meta=replace(zone.template.meta, spawners=(spec,)))
    rig.step()
    enforcer = next(a for a in zone.actors.values() if a.kind != "player")
    runner = game.core.characters.sim.avatars[client.session.user_id].actor
    runner.faction = "kestrel"
    runner.health = player.health = 10_000  # Survive the whole trace.
    monkeypatch.setattr(rules, "to_hit", lambda *args: 100)
    trace: list[tuple[int, str]] = []
    combat = game.core.combat
    shot, hurt = combat.shot, combat.hurt

    def traced_shot(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        pending, waiting = game.core.commerce.pending, len(game.core.commerce.waiting)
        shot(source, target, *args, **kwargs)
        if source is enforcer:
            label = ("attempt (queued)" if len(game.core.commerce.waiting) > waiting else "attempt (refused)")                 if pending else "attempt"
            trace.append((game.clock.now_tick(), label))

    def traced_hurt(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        before = target.health
        hurt(source, target, *args, **kwargs)
        if source is enforcer and target.health < before:
            trace.append((game.clock.now_tick(), "hit"))
    combat.shot, combat.hurt = traced_shot, traced_hurt
    # Until the enforcer has fired once, with every receipt settling at once.
    for _ in range(200):
        rig.step()
        rig.writer.complete()
        rig.cycle()
        if any(event == "hit" for _, event in trace):
            break
    assert any(event == "hit" for _, event in trace), "the enforcer never fired"
    if hold:
        calls: list[bool] = []
        rig.reward(player, 1, calls)  # An unrelated receipt: a purchase, a sale.
        rig.cycle()
        trace.append((game.clock.now_tick(), "commit held"))
        rig.step(HOLD_TICKS)  # The disk stalls; ticks go on.
        trace.append((game.clock.now_tick(), "commit released"))
    for _ in range(60):
        rig.writer.complete()
        rig.cycle()
        rig.step()
    rig.writer.complete()
    rig.cycle()
    return trace


def hits(trace: list[tuple[int, str]]) -> list[int]:
    return [tick for tick, event in trace if event == "hit"]


def test_a_held_commit_across_the_enforcers_next_turn(tmp_path: Path, content: Any, monkeypatch) -> None:
    normal = fight(tmp_path / "normal", content, monkeypatch, hold=False)
    held = fight(tmp_path / "held", content, monkeypatch, hold=True)
    print("normal:", normal)
    print("held:  ", held)
    cadence = 8  # The enforcer's weapon cooldown, in ticks.
    first = hits(normal)[0]
    assert hits(normal) == list(range(first, first + cadence * len(hits(normal)), cadence)), "normal play unchanged"
    released = next(tick for tick, event in held if event == "commit released")
    # Ruling (b): one shot queued per source; later turns during the hold are refused, then normal cadence.
    assert hits(held)[:4] == [first, released, released + cadence, released + 2 * cadence], held
    assert [event for _, event in held].count("attempt (queued)") == 1


def dot(tmp_path: Path, content: Any, monkeypatch: pytest.MonkeyPatch, hold: bool) -> list[tuple[int, str]]:
    """A ten-second damage-over-time on the runner; the enforcer itself holds fire."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    rig = Rig(tmp_path, content)
    game, client = rig.game, rig.clients[0]
    player = rig.player()
    player.progress["wake_combat"] = "skipped"
    place(game, client, "vatside-clinics", (20, 3))
    zone = game.core.world.zones["vatside-clinics"]
    spec = Spawner("test-group", "npc-sable-enforcer", 1, 60, region=Region(24, 3, 1, 1), condition="always")
    zone.template = replace(zone.template, meta=replace(zone.template.meta, spawners=(spec,)))
    rig.step()
    enforcer = next(a for a in zone.actors.values() if a.kind != "player")
    enforcer.cooldowns["weapon"] = 1 << 40
    runner = game.core.characters.sim.avatars[client.session.user_id].actor
    runner.faction = "kestrel"
    runner.health = player.health = 10_000
    trace: list[tuple[int, str]] = []
    combat = game.core.combat
    hurt = combat.hurt

    def traced_hurt(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        before = target.health
        queued = game.core.commerce.pending
        hurt(source, target, *args, **kwargs)
        trace.append((game.clock.now_tick(), "dot queued" if queued else "dot" if target.health < before else "dot none"))
    combat.hurt = traced_hurt
    now = game.clock.now_tick()
    runner.effects.append({"kind": "dot", "source": enforcer.id, "damage_type": "chemical", "damage": 3,
                           "until_tick": now + 100, "next_tick": now + 10})
    rig.step(15)
    if hold:
        calls: list[bool] = []
        rig.reward(player, 1, calls)
        rig.cycle()
        trace.append((game.clock.now_tick(), "commit held"))
        rig.step(HOLD_TICKS)
        trace.append((game.clock.now_tick(), "commit released"))
    while game.clock.now_tick() <= now + 130:  # Well past the effect's end, held or not.
        rig.writer.complete()
        rig.cycle()
        rig.step()
    return trace


def test_trace_a_damage_over_time_across_a_held_commit(tmp_path: Path, content: Any, monkeypatch) -> None:
    normal = dot(tmp_path / "normal", content, monkeypatch, hold=False)
    held = dot(tmp_path / "held", content, monkeypatch, hold=True)
    print("dot normal:", normal)
    print("dot held:  ", held)
    assert sum(event == "dot" for _, event in held) == sum(event == "dot" for _, event in normal)


# --- The follow-ups of one attack all land (ruling (b), its load-bearing assumption) ---

class Scene:
    """A Kestrel runner with a weapon and three still Sable enforcers in the clinics."""

    def __init__(self, tmp_path: Path, content: Any, monkeypatch: pytest.MonkeyPatch,
                 at: tuple[tuple[int, int], ...]) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.rig = rig = Rig(tmp_path, content)
        game, client = rig.game, rig.clients[0]
        self.player = player = rig.player()
        player.progress["wake_combat"] = "skipped"
        player.update_progression(1500)
        place(game, client, "vatside-clinics", (20, 3))
        zone = game.core.world.zones["vatside-clinics"]
        spec = Spawner("test-group", "npc-sable-enforcer", len(at), 60, region=Region(30, 8, len(at), 1),
                       condition="always")
        zone.template = replace(zone.template, meta=replace(zone.template.meta, spawners=(spec,)))
        rig.step()
        self.runner = game.core.characters.sim.avatars[client.session.user_id].actor
        self.runner.faction = "kestrel"
        self.runner.health = 10_000
        self.npcs = sorted((a for a in zone.actors.values() if a.kind != "player"), key=lambda a: a.id)
        for npc, tile in zip(self.npcs, at):
            game.core.world.leave(npc)
            game.core.world.enter(npc, "vatside-clinics", tile)
            npc.behaviour.update(home=tile, pause=1 << 40)
            npc.cooldowns["weapon"] = 1 << 40
            npc.health = npc.health_max = 10_000
        monkeypatch.setattr(rules, "to_hit", lambda *args: 100)
        self.hits: list[tuple[int, str]] = []
        combat = game.core.combat
        hurt = combat.hurt

        def traced(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
            before = target.health
            hurt(source, target, *args, **kwargs)
            if source is self.runner and target.health < before:
                self.hits.append((game.clock.now_tick(), target.id))
        combat.hurt = traced

    def arm(self, template: str, ammo: str | None = None) -> None:
        game, player = self.rig.game, self.player
        items, at = game.core.commerce.items, game.clock.now_utc().isoformat()
        iid = "test-origin:item:97001"
        items[iid] = make_item(iid, game.core.loop.content.items[template], player.id, at)
        player.inventory.append(iid)
        player.equipment["weapon_main"] = iid
        if ammo:
            aid = "test-origin:item:97002"
            items[aid] = make_item(aid, game.core.loop.content.items[ammo], player.id, at, stack=30)
            player.inventory.append(aid)

    def press_fire(self) -> None:
        self.rig.clients[0].session.input_queue.append(
            Frame("intent", {"name": "fire", "args": {"target": self.npcs[0].id}}))

    def fire(self, hold: int = 0) -> None:
        """Warn, wait out the warning, then fire once; optionally hold the follow-ups' commit."""
        rig = self.rig
        self.press_fire()
        rig.step(25)
        for _ in range(5):
            rig.writer.complete()
            rig.cycle()
        self.press_fire()
        rig.step()
        rig.writer.complete()  # The ammo or equipment receipt: resolved() runs, the first hit lands.
        rig.cycle()
        if hold:
            rig.step(hold)  # The first hit's XP receipt is held; the follow-ups wait in the queue.
        for _ in range(20):
            rig.writer.complete()
            rig.cycle()


WEAPONS = [
    # (template, ammo, archetype, enforcer tiles, hits from one attack)
    ("wpn_chapel_choir_bell", None, "cantor", ((22, 3), (22, 4), (21, 2)), 3),  # Cone 3: every actor in it.
    ("wpn_ostrom_arc_projector", "ammo_cell", None, ((23, 3), (24, 3), (30, 9)), 2),  # Two targets.
    ("wpn_ow_arc_knuckles", None, None, ((21, 3), (30, 9), (31, 9)), 2),  # Two hits per swing.
]


@pytest.mark.parametrize("hold", [0, 30], ids=["normal", "held"])
@pytest.mark.parametrize("weapon", WEAPONS, ids=[w[0] for w in WEAPONS])
def test_every_hit_of_one_attack_lands(tmp_path: Path, content: Any, monkeypatch, weapon, hold) -> None:
    template, ammo, archetype, at, expected = weapon
    scene = Scene(tmp_path / "scene", content, monkeypatch, at)
    if archetype:
        scene.player.archetype = archetype
    scene.arm(template, ammo)
    scene.fire(hold)
    print(template, "hold", hold, scene.hits)
    assert len(scene.hits) == expected, scene.hits
    assert not scene.rig.game.core.combat.queued_attacks


# --- The area dissonance cast ---------------------------------------------

def cast(scene: Scene) -> None:
    combat, now = scene.rig.game.core.combat, scene.rig.game.clock.now_tick()
    combat.complete_cast(scene.runner, {"ability": "dis_choir", "target": scene.npcs[0].id, "until": now})


@pytest.mark.parametrize("hold", [0, 30], ids=["normal", "held"])
def test_an_area_cast_whose_first_recipient_dies_still_lands_on_all_three(tmp_path, content, monkeypatch, hold):
    scene = Scene(tmp_path / "scene", content, monkeypatch, ((22, 3), (22, 4), (21, 2)))
    scene.player.archetype = "cantor"
    scene.npcs[0].health = 1  # Its kill receipt makes recipients 2-3 queue in the cast's tick.
    cast(scene)
    assert scene.rig.game.core.commerce.pending, "the forced case: a recipient's death settled a receipt"
    scene.rig.cycle()
    if hold:
        scene.rig.step(hold)
    for _ in range(20):
        scene.rig.writer.complete()
        scene.rig.cycle()
    print("area cast, hold", hold, scene.hits)
    assert sorted(target for _, target in scene.hits) == sorted(npc.id for npc in scene.npcs)


def test_an_area_cast_with_no_death_lands_on_all_three_at_once(tmp_path, content, monkeypatch):
    scene = Scene(tmp_path / "scene", content, monkeypatch, ((22, 3), (22, 4), (21, 2)))
    scene.player.archetype = "cantor"
    cast(scene)
    assert len({tick for tick, _ in scene.hits}) == 1 and len(scene.hits) == 3


def test_a_second_cast_completing_during_a_hold_is_refused(tmp_path, content, monkeypatch):
    scene = Scene(tmp_path / "scene", content, monkeypatch, ((22, 3), (22, 4), (21, 2)))
    scene.player.archetype = "cantor"
    rig = scene.rig
    calls: list[bool] = []
    rig.reward(scene.player, 1, calls)
    rig.cycle()  # A commit is held.
    cast(scene)  # Queued.
    rig.step(25)
    cast(scene)  # A later turn: refused, a no-op.
    for _ in range(20):
        rig.writer.complete()
        rig.cycle()
    assert len(scene.hits) == 3, "one cast's three recipients, not two casts' six"


# --- Ruling (b), the technical lead's list ----------------------------------

def enforcers(rig: Rig, count: int) -> list[Any]:
    game, client = rig.game, rig.clients[0]
    place(game, client, "vatside-clinics", (20, 3))
    zone = game.core.world.zones["vatside-clinics"]
    spec = Spawner("test-group", "npc-sable-enforcer", count, 60, region=Region(23, 3, count, 1), condition="always")
    zone.template = replace(zone.template, meta=replace(zone.template.meta, spawners=(spec,)))
    rig.step()
    return sorted((a for a in zone.actors.values() if a.kind != "player"), key=lambda a: a.id)


def test_two_enforcers_on_one_target_each_keep_one_shot(tmp_path, content) -> None:
    rig = Rig(tmp_path, content)
    first, second = enforcers(rig, 2)
    game = rig.game
    combat, commerce = game.core.combat, game.core.commerce
    runner = game.core.characters.sim.avatars[rig.clients[0].session.user_id].actor
    calls: list[bool] = []
    rig.reward(rig.player(), 1, calls)
    rig.cycle()
    arm = npc_weapon(game.core.loop.content, game.core.loop.content.npcs["npc-sable-enforcer"])
    for source in (first, second):
        combat.shot(source, runner, arm)  # Queued in tick T.
    game.clock.advance(8)
    for source in (first, second):
        combat.shot(source, runner, arm)  # Each source's later turn: refused.
    assert len(commerce.waiting) == 2 and set(combat.queued_attacks) == {first.id, second.id}


def test_a_source_that_dies_with_a_shot_queued_runs_nothing_and_clears(tmp_path, content) -> None:
    rig = Rig(tmp_path, content)
    [enforcer] = enforcers(rig, 1)
    game = rig.game
    combat = game.core.combat
    runner = game.core.characters.sim.avatars[rig.clients[0].session.user_id].actor
    calls: list[bool] = []
    rig.reward(rig.player(), 1, calls)
    rig.cycle()
    arm = npc_weapon(game.core.loop.content, game.core.loop.content.npcs["npc-sable-enforcer"])
    combat.shot(enforcer, runner, arm)
    assert enforcer.id in combat.queued_attacks
    ran: list[int] = []
    shot = combat.shot
    combat.shot = lambda *a, **k: (ran.append(1), shot(*a, **k))
    enforcer.stance = "dead"
    for _ in range(5):
        rig.writer.complete()
        rig.cycle()
    assert ran == [] and enforcer.id not in combat.queued_attacks
