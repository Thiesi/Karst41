"""S47: the commerce queue's two bounds and the writer-behind pause.

Technical lead's ruling on the settle() audit:
- player-originated jobs keep QUEUE_LIMIT 64 and today's busy texts;
- system jobs (simulation outcomes) get SYSTEM_LIMIT and are never refused;
- at PAUSE_HIGH queued system jobs the simulation stops stepping until they
  drain below PAUSE_LOW, while results settle and views publish;
- reaching SYSTEM_LIMIT is a controlled fatal failure after the step.
Every test holds a commit by hand (ManualWriter), with no sleeps.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from karst41.content.loader import load_content
from karst41.content.schema import Region, Spawner
from karst41.server.commerce import SYSTEM_LIMIT
from karst41.server.io import PAUSE_HIGH, PAUSE_LOW, StorageFailure
from karst41.server.sessions import QUEUE_LIMIT
from karst41.server.skills import Use
from tests.character_helpers import place
from tests.server.test_s47_writer import Rig


@pytest.fixture(scope="module")
def content() -> Any:
    tree = load_content(Path(__file__).parents[2] / "src/karst41/content")
    tree.walls = None
    tree.zones = {key: replace(z, meta=replace(z.meta, spawners=())) for key, z in tree.zones.items()}
    return tree


def hold(rig: Rig) -> None:
    calls: list[bool] = []
    rig.reward(rig.player(), 1, calls)
    rig.cycle()
    assert rig.game.core.commerce.pending


def fill(rig: Rig, depth: int) -> None:
    commerce = rig.game.core.commerce
    while commerce.system_queued() < depth:
        commerce.enqueue_system(lambda: None)


def release(rig: Rig, rounds: int = 20) -> None:
    for _ in range(rounds):
        rig.writer.complete()
        rig.cycle()


def drive(rig: Rig, ticks: int) -> int:
    """Server._ticks' gate, by hand: step if allowed, else publish and settle. Returns steps."""
    loop, steps = rig.game.core.loop, 0
    for _ in range(ticks):
        rig.game.clock.advance()
        if rig.io.step_allowed():
            loop.step()
            steps += 1
            rig.cycle()
        elif rig.io.paused_tick(rig.game.clock.now_tick()):
            loop.publish()
            rig.cycle()
    return steps


def messages(rig: Rig) -> list[str]:
    return [line.get("message", "") for _, line in rig.logs.lines]


def enforcer_scene(rig: Rig, x: int = 23) -> tuple[Any, Any]:
    game, client = rig.game, rig.clients[0]
    place(game, client, "vatside-clinics", (20, 3))
    zone = game.core.world.zones["vatside-clinics"]
    spec = Spawner("test-group", "npc-sable-enforcer", 1, 60, region=Region(x, 3, 1, 1), condition="always")
    zone.template = replace(zone.template, meta=replace(zone.template.meta, spawners=(spec,)))
    rig.step()
    enforcer = next(a for a in zone.actors.values() if a.kind != "player")
    runner = game.core.characters.sim.avatars[client.session.user_id].actor
    runner.faction = "kestrel"
    return enforcer, runner


def simulation_state(rig: Rig) -> list[tuple[Any, ...]]:
    """Every actor's position, health, stance, cooldowns and effect timers."""
    return sorted((actor.id, actor.zone, actor.position, actor.health, actor.stance, sorted(actor.cooldowns.items()),
                   [sorted((k, v) for k, v in e.items() if not isinstance(v, (dict, list))) for e in actor.effects])
                  for actor in rig.game.core.world.actors.values())


def test_at_the_high_water_mark_steps_pause_and_views_still_publish_then_resume(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    client = rig.clients[0]
    enforcer, runner = enforcer_scene(rig)  # A live scene: an NPC that would move and fire.
    now = rig.game.clock.now_tick()
    runner.effects.append({"kind": "dot", "source": enforcer.id, "damage_type": "chemical", "damage": 3,
                           "until_tick": now + 300, "next_tick": now + 10})
    hold(rig)
    fill(rig, PAUSE_HIGH)
    last = rig.game.core.loop.last_tick
    rig.game.frames_for(client)
    client.session.needs_full_view = True
    frozen = simulation_state(rig)
    assert drive(rig, 30) == 0, "no simulation step while the writer is far behind"
    assert rig.game.core.loop.last_tick == last
    # As in a freeze: positions, health and timers don't move during paused ticks.
    assert simulation_state(rig) == frozen
    assert any(f.type == "view" for f in rig.game.frames_for(client)), "views keep publishing while paused"
    assert rig.io.stats["paused_ticks"] == 30 and rig.io.stats["pauses"] == 1
    release(rig)  # The held commit settles; its drain runs every queued job.
    assert rig.game.core.commerce.system_queued() < PAUSE_LOW
    assert drive(rig, 3) == 3, "steps resume below the low-water mark"
    assert rig.game.core.loop.last_tick == rig.game.clock.now_tick(), "the clock jumps to now, as after a freeze"
    release(rig)
    assert "simulation paused: writer behind" in messages(rig) and "resumed" in messages(rig)
    resumed = next(line for _, line in rig.logs.lines if line.get("message") == "resumed")
    assert resumed["paused_ticks"] == 30 and resumed["depth"] < PAUSE_LOW


def test_between_the_marks_the_pause_holds_until_below_low_water(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    hold(rig)
    fill(rig, PAUSE_HIGH)
    assert not rig.io.step_allowed()
    commerce = rig.game.core.commerce
    while commerce.system_queued() > PAUSE_LOW:  # Drained part way, as settlement would.
        commerce.waiting.pop()
    assert commerce.system_queued() == PAUSE_LOW and not rig.io.step_allowed(), "at low water: still paused"
    commerce.waiting.pop()
    assert rig.io.step_allowed(), "below low water: resumed"


def test_a_subdue_at_depth_above_64_queues_and_its_outcome_lands(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    target, runner = enforcer_scene(rig, x=21)
    hold(rig)
    fill(rig, QUEUE_LIMIT + 36)
    rig.game.core.contracts.subdued(runner, target)  # Before the split: "The city is busy." out of the NPC tick.
    release(rig)
    assert target.stance == "restrained" and target.behaviour.get("resolution") == "subdued"
    assert target.zone is None


def test_record_use_at_depth_above_64_lands_its_xp_after_release(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    player = rig.player()
    hold(rig)
    fill(rig, QUEUE_LIMIT + 36)
    rig.game.core.progression.record_use(player.id, Use("weapon_hit", "kinetic", "probe-receipt", True, threat=True))
    release(rig)
    assert sum(player.skills.values()) > 0


def test_a_damage_over_time_across_a_long_hold_above_64_lands_every_tick(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    enforcer, runner = enforcer_scene(rig)
    enforcer.cooldowns["weapon"] = 1 << 40
    runner.health = rig.player().health = 10_000
    game = rig.game
    hurt, hits = game.core.combat.hurt, []

    def traced(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        before = target.health
        hurt(source, target, *args, **kwargs)
        if target is runner and target.health < before:
            hits.append(game.clock.now_tick())
    game.core.combat.hurt = traced
    now = game.clock.now_tick()
    runner.effects.append({"kind": "dot", "source": enforcer.id, "damage_type": "chemical", "damage": 3,
                           "until_tick": now + 100, "next_tick": now + 10})
    hold(rig)
    fill(rig, QUEUE_LIMIT + 36)  # Above the old shared bound, below the pause.
    rig.step(60)  # Six DoT seconds while the commit is held.
    release(rig)
    while game.clock.now_tick() <= now + 130:
        rig.step()
        release(rig, 2)
    assert len(hits) == 10, hits  # Every tick, from next_tick to until_tick.


def test_the_player_busy_text_appears_at_64_player_jobs_and_system_jobs_still_queue(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    commerce, session = rig.game.core.commerce, rig.clients[0].session
    hold(rig)
    for _ in range(QUEUE_LIMIT):
        commerce.propose(session, "inv.use", {"item": "nothing"})
    with pytest.raises(ValueError, match="The counter is busy. Try again."):
        commerce.propose(session, "inv.use", {"item": "nothing"})
    with pytest.raises(ValueError, match="The city is busy. Try again."):
        commerce.enqueue_player(lambda: None)
    commerce.enqueue_system(lambda: None)  # A simulation outcome never sees the player bound.
    assert commerce.player_queued() == QUEUE_LIMIT and commerce.system_queued() == 1


def test_reaching_the_system_limit_takes_the_fatal_path_after_the_step(tmp_path: Path, content: Any) -> None:
    rig = Rig(tmp_path, content)
    commerce = rig.game.core.commerce
    hold(rig)
    fill(rig, SYSTEM_LIMIT)  # enqueue_system never raises: the job is kept, the breach recorded.
    assert commerce.breach == SYSTEM_LIMIT
    with pytest.raises(StorageFailure, match="system queue limit breached"):
        rig.cycle()  # IO.pump, after the step: the controlled fatal path.
    assert "system queue limit breached" in messages(rig)


def test_with_the_pause_a_busy_scene_never_nears_the_system_limit(tmp_path: Path, content: Any) -> None:
    """The invariant holds in practice: once paused, no step runs, so no step adds jobs."""
    rig = Rig(tmp_path, content)
    enforcer, runner = enforcer_scene(rig)
    runner.health = rig.player().health = 10_000
    now = rig.game.clock.now_tick()
    runner.effects.append({"kind": "dot", "source": enforcer.id, "damage_type": "chemical", "damage": 3,
                           "until_tick": now + 600, "next_tick": now + 10})
    commerce = rig.game.core.commerce
    hold(rig)
    fill(rig, PAUSE_HIGH - 5)  # Just under the pause.
    deepest = 0
    for _ in range(300):  # Thirty seconds of a stalled disk, with combat and a DoT running.
        drive(rig, 1)
        deepest = max(deepest, commerce.system_queued())
    assert rig.io.paused_since is not None, "the scene reached the pause"
    assert deepest < PAUSE_HIGH + 64 < SYSTEM_LIMIT, deepest
    assert commerce.breach is None
