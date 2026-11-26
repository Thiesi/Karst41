"""#277 pricing: a deterministic headless replay, probes off (technical lead, 2026-11-26).

Two scenarios on the production Core with every hub zone active, writes
through S47's IO and a ManualWriter (the same Executor and SQL as the live
writer, completed after each tick and outside every timed section):

- pursuit: a caller on Sodium Row provokes a Sable enforcer; both enforcers
  hunt and shoot. The caller is healed below 40 HP so the fight continues.
  Hit ticks are the ticks whose character outbox received effects.
- refill: every Sodium Row spawner slot is emptied with a due deadline, so
  the next NPC tick refills the zone at once (the S11 early-tick spike).

The same content, seed and inputs give the same world on every repeat;
the script checks that each repeat ends in the same actor and RNG state.
Timings are wall clock around Npcs.tick, the whole step, and the writes
(IO.settle plus IO.pump). Run it on two trees for a before/after:

    PYTHONPATH="<tree>/src;<tree>" python scripts/bench_277.py --repeats 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))] if ordered else 0.0


class Timed:
    """One wrapper around Npcs.tick: the only instrumentation, outside the cuts."""

    def __init__(self) -> None:
        from karst41.server.npcs.engine import Npcs
        self.cls, self.original, self.last = Npcs, Npcs.tick, 0.0
        timed = self

        def tick(npcs: Any, ctx: Any) -> None:
            began = time.perf_counter()
            try:
                return timed.original(npcs, ctx)
            finally:
                timed.last = (time.perf_counter() - began) * 1000
        Npcs.tick = tick

    def close(self) -> None:
        self.cls.tick = self.original


def world(tmp: Path, content: Any) -> Any:
    from tests.character_helpers import place
    from tests.server.test_s47_writer import Rig
    rig = Rig(tmp, content)
    game = rig.game
    caller = rig.clients[0]
    place(game, caller, 'sodium-row', (16, 5))
    rig.cycle()
    rig.writer.complete()
    for zone in game.core.world.zones.values():
        zone.activate('bench-277', game.clock.now_tick())
    return rig, caller


def fingerprint(game: Any) -> str:
    actors = sorted((a.id, a.zone, a.position, a.health, a.behaviour.get('state'), a.target)
                    for a in game.core.world.actors.values())
    rngs = [zone.rng.getstate() for _, zone in sorted(game.core.world.zones.items())]
    return json.dumps([actors, repr(rngs)], default=str)


def cycle(rig: Any) -> float:
    began = time.perf_counter()
    rig.cycle()
    elapsed = (time.perf_counter() - began) * 1000
    rig.writer.complete()  # The writer's work, outside the timed section.
    return elapsed


def pursuit(content: Any, ticks: int) -> tuple[list[dict[str, float]], str]:
    with tempfile.TemporaryDirectory() as tmp:
        rig, caller = world(Path(tmp), content)
        game = rig.game
        for _ in range(40):
            game.step()
            cycle(rig)
        npcs, zone = game.core.npcs, game.core.world.zones['sodium-row']
        me = game.core.characters.sim.avatars[caller.session.user_id].actor
        guards = [a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-sable-enforcer']
        npcs.provoked(me, min(guards, key=lambda a: ((a.x - me.x) ** 2 + (a.y - me.y) ** 2, a.id)))
        timer, rows = Timed(), []
        try:
            for _ in range(ticks):
                if me.health < 40:
                    me.health = 130
                queued = len(game.core.characters.outbox)
                began = time.perf_counter()
                game.step()
                step = (time.perf_counter() - began) * 1000
                hit = len(game.core.characters.outbox) > queued
                writes = cycle(rig)
                rows.append({'npcs': timer.last, 'step': step, 'writes': writes, 'total': step + writes, 'hit': hit})
        finally:
            timer.close()
        result = fingerprint(game)
        rig.store.db.close()
        return rows, result


def refill(content: Any, rounds: int) -> tuple[list[float], str]:
    with tempfile.TemporaryDirectory() as tmp:
        rig, _ = world(Path(tmp), content)
        game = rig.game
        for _ in range(40):
            game.step()
            cycle(rig)
        npcs, zone = game.core.npcs, game.core.world.zones['sodium-row']
        timer, spikes = Timed(), []
        try:
            for _ in range(rounds):
                now = npcs.combat.now()
                for actor in [a for a in zone.actors.values() if a.kind == 'npc' and a.behaviour.get('slot')]:
                    npcs.spawners.removed(actor, now, deadline=now)
                    game.core.world.leave(actor)
                game.step()
                spikes.append(timer.last)
                cycle(rig)
                for _ in range(20):
                    game.step()
                    cycle(rig)
        finally:
            timer.close()
        result = fingerprint(game)
        rig.store.db.close()
        return spikes, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--ticks', type=int, default=600)
    parser.add_argument('--rounds', type=int, default=10)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    import karst41
    from karst41.content.loader import load_content
    content = load_content(ROOT / 'src/karst41/content')
    content.walls = None  # As the S47 rig's tests: the wall voice is S48d's and only adds log lines.
    report: dict[str, Any] = {'tree': str(Path(karst41.__file__).resolve().parents[2]), 'repeats': []}
    fingerprints = set()
    hits_all: dict[str, list[float]] = {'npcs': [], 'writes': [], 'total': []}
    quiet_all: dict[str, list[float]] = {'npcs': [], 'writes': [], 'total': []}
    spikes_all: list[float] = []
    for repeat in range(args.repeats):
        rows, pursuit_state = pursuit(content, args.ticks)
        spikes, refill_state = refill(content, args.rounds)
        fingerprints.add((pursuit_state, refill_state))
        hits = [r for r in rows if r['hit']]
        quiet = [r for r in rows if not r['hit']]
        for key in hits_all:
            hits_all[key] += [r[key] for r in hits]
            quiet_all[key] += [r[key] for r in quiet]
        spikes_all += spikes
        line = {'repeat': repeat, 'hit_ticks': len(hits),
                **{f'hit_{k}_median': round(statistics.median(r[k] for r in hits), 3) for k in hits_all},
                **{f'hit_{k}_p99': round(quantile([r[k] for r in hits], .99), 3) for k in hits_all},
                'refill_npcs_median': round(statistics.median(spikes), 3)}
        report['repeats'].append(line)
        print(json.dumps(line), flush=True)
    report['deterministic'] = len(fingerprints) == 1
    report['summary'] = {
        **{f'hit_{k}': {'median': round(statistics.median(v), 3), 'p99': round(quantile(v, .99), 3)} for k, v in hits_all.items()},
        **{f'quiet_{k}': {'median': round(statistics.median(v), 3), 'p99': round(quantile(v, .99), 3)} for k, v in quiet_all.items()},
        'refill_npcs': {'median': round(statistics.median(spikes_all), 3), 'p99': round(quantile(spikes_all, .99), 3)},
        'hit_ticks_per_repeat': report['repeats'][0]['hit_ticks']}
    print(json.dumps({'deterministic': report['deterministic'], **report['summary']}, indent=1))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    return 0 if report['deterministic'] else 1


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
