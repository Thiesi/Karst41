"""#277 pricing: a deterministic headless replay, probes off (technical lead, 2026-11-26).

Five scenarios on the production Core with every hub zone active, writes
through S47's IO and a ManualWriter (the same Executor and SQL as the live
writer, completed after each tick and outside every timed section):

- pursuit: a caller on Sodium Row provokes a Sable enforcer; both enforcers
  hunt and shoot. The caller is healed below 40 HP so the fight continues.
  Hit ticks are the ticks whose character outbox received effects.
- refill: every Sodium Row spawner slot is emptied with a due deadline, so
  the next NPC tick refills the zone at once (the S11 early-tick spike).
- focus (#277 D4): `Core.interaction_views`, the publishers' E/J focus
  lookup, timed on its own for one caller on sampled tiles of every hub zone.
- wake (#288): a fresh world runs with the other zones dormant, then every
  zone wakes at once, as S11's profiler does; the wake tick's Npcs.tick and
  step are timed, in a new world each round.
- patrol (#284, `--only patrol` alone): every zone active and no fight, so
  patrols walk their loops among wandering residents; Npcs.tick, the step
  and the patrols' route searches per tick are recorded.

The same content, seed and inputs give the same world on every repeat;
the script checks that each repeat ends in the same actor and RNG state,
and reports a fingerprint of that state: equal fingerprints on two trees
mean the same simulation outcome.
Timings are wall clock around Npcs.tick, the whole step, and the writes
(IO.settle plus IO.pump). Run it on two trees for a before/after:

    PYTHONPATH="<tree>/src;<tree>" python scripts/bench_277.py --repeats 5

`--only npcs` runs pursuit and refill; `--only focus`, `--only wake` and `--only patrol` run one scenario.
"""

from __future__ import annotations

import argparse
import hashlib
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


def focus(content: Any, stride: int) -> tuple[list[float], str]:
    """#277 (D4): the publishers' interaction lookup, `Core.interaction_views`, on its own.

    One caller stands on every `stride`-th passable tile of every hub zone, in
    sorted order, skipping occupied tiles. Each tile's first call warms the
    caller's sight cache, as on a tick where the caller hasn't moved, and only
    the second call is timed. The returned digest covers every (E, J) focus
    pair, so equal digests on two trees mean the same disclosed focus.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rig, caller = world(Path(tmp), content)
        game = rig.game
        for _ in range(40):
            game.step()
            cycle(rig)
        core, session = game.core, caller.session
        avatar = core.characters.sim.avatars[session.user_id]
        timings, seen = [], hashlib.sha256()
        for zid in sorted(core.world.zones):
            zone = core.world.zones[zid]
            tiles = [(x, y) for y in range(zone.template.height) for x in range(zone.template.width)
                     if zone.passable((x, y))]
            for tile in tiles[::stride]:
                try:
                    core.world.enter(avatar.actor, zid, tile)
                except ValueError:  # Occupied.
                    continue
                avatar.zone = session.subscribed = zid
                core.interaction_views(session)
                began = time.perf_counter()
                views = core.interaction_views(session)
                timings.append((time.perf_counter() - began) * 1000)
                seen.update(json.dumps([zid, tile, views], sort_keys=True).encode())
        rig.store.db.close()
        return timings, seen.hexdigest()


def wake(content: Any, rounds: int) -> tuple[list[dict[str, float]], str]:
    """#288: the first refill of every zone, as when S11's profiler wakes them all.

    Each round builds a new world (so its spawn ground starts as the tree's
    Core leaves it), runs 30 ticks with only the caller's zone awake, then
    activates every zone and times the next tick: Npcs.tick and the whole
    step. Twenty more ticks follow, and the last round's world is the digest.
    """
    rows, result = [], ''
    for _ in range(rounds):
        with tempfile.TemporaryDirectory() as tmp:
            from tests.server.test_s47_writer import Rig
            rig = Rig(Path(tmp), content)
            game = rig.game
            for _ in range(30):
                game.step()
                cycle(rig)
            for zone in game.core.world.zones.values():
                zone.activate('bench-288', game.clock.now_tick())
            timer = Timed()
            try:
                began = time.perf_counter()
                game.step()
                step = (time.perf_counter() - began) * 1000
                rows.append({'npcs': timer.last, 'step': step})
            finally:
                timer.close()
            cycle(rig)
            for _ in range(20):
                game.step()
                cycle(rig)
            result = fingerprint(game)
            rig.store.db.close()
    return rows, result


def patrols(content: Any, ticks: int) -> tuple[list[dict[str, float]], str]:
    """#284: every zone active and no fight, so patrols walk their loops among
    wandering residents (Clinic Row's Warden patrol and its patients included).
    Times Npcs.tick and the step, and counts patrol route searches per tick
    (trees before #284 have no patrol routes and report 0)."""
    with tempfile.TemporaryDirectory() as tmp:
        rig, _ = world(Path(tmp), content)
        game = rig.game
        routes = getattr(game.core.npcs, 'patrol_routes', None)
        timer, rows = Timed(), []
        try:
            for _ in range(ticks):
                before = {aid: route.searches for aid, route in routes.items()} if routes is not None else {}
                began = time.perf_counter()
                game.step()
                step = (time.perf_counter() - began) * 1000
                searches = (sum(route.searches - before.get(aid, 0) for aid, route in routes.items())
                            if routes is not None else 0)
                cycle(rig)
                rows.append({'npcs': timer.last, 'step': step, 'searches': searches})
        finally:
            timer.close()
        result = fingerprint(game)
        rig.store.db.close()
        return rows, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--ticks', type=int, default=600)
    parser.add_argument('--rounds', type=int, default=10)
    parser.add_argument('--stride', type=int, default=7, help='focus: every n-th passable tile')
    parser.add_argument('--wakes', type=int, default=3, help='wake: fresh worlds per repeat')
    parser.add_argument('--only', choices=('npcs', 'focus', 'wake', 'patrol'),
                        help='run one part: pursuit and refill, focus, wake, or patrol')
    parser.add_argument('--patrol-ticks', type=int, default=1200, help='patrol: ticks per repeat')
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
    focus_all: list[float] = []
    wake_all: list[dict[str, float]] = []
    patrol_all: list[dict[str, float]] = []
    for repeat in range(args.repeats):
        if args.only in (None, 'npcs'):
            rows, pursuit_state = pursuit(content, args.ticks)
            spikes, refill_state = refill(content, args.rounds)
        else:
            rows, pursuit_state, spikes, refill_state = [], '', [], ''
        timings, focus_state = focus(content, args.stride) if args.only in (None, 'focus') else ([], '')
        focus_all += timings
        woken, wake_state = wake(content, args.wakes) if args.only in (None, 'wake') else ([], '')
        wake_all += woken
        walked, patrol_state = patrols(content, args.patrol_ticks) if args.only == 'patrol' else ([], '')
        patrol_all += walked
        # The wake and patrol digests join only when they ran, so the other scenarios' fingerprints stay comparable.
        fingerprints.add((pursuit_state, refill_state, focus_state) + ((wake_state,) if woken else ())
                         + ((patrol_state,) if walked else ()))
        hits = [r for r in rows if r['hit']]
        quiet = [r for r in rows if not r['hit']]
        for key in hits_all:
            hits_all[key] += [r[key] for r in hits]
            quiet_all[key] += [r[key] for r in quiet]
        spikes_all += spikes
        line: dict[str, Any] = {'repeat': repeat}
        if rows:
            line.update({'hit_ticks': len(hits),
                         **{f'hit_{k}_median': round(statistics.median(r[k] for r in hits), 3) for k in hits_all},
                         **{f'hit_{k}_p99': round(quantile([r[k] for r in hits], .99), 3) for k in hits_all},
                         'refill_npcs_median': round(statistics.median(spikes), 3)})
        if timings:
            line.update({'focus_calls': len(timings), 'focus_median': round(statistics.median(timings), 4),
                         'focus_p99': round(quantile(timings, .99), 4), 'focus_mean': round(statistics.fmean(timings), 4)})
        if woken:
            line.update({'wake_npcs': [round(r['npcs'], 3) for r in woken], 'wake_step': [round(r['step'], 3) for r in woken]})
        if walked:
            line.update({f'patrol_{k}_{stat}': round(fn([r[k] for r in walked]), 3) for k in ('npcs', 'step')
                         for stat, fn in (('median', statistics.median), ('p99', lambda v: quantile(v, .99)))})
            line.update({'patrol_searches': sum(r['searches'] for r in walked),
                         'patrol_search_ticks': sum(1 for r in walked if r['searches']),
                         'patrol_searches_max_per_tick': max(r['searches'] for r in walked)})
        report['repeats'].append(line)
        print(json.dumps(line), flush=True)
    report['deterministic'] = len(fingerprints) == 1
    # The final world of both scenarios (actors, positions, health, states, targets and every
    # zone's RNG). Equal across trees means the cuts changed no simulation outcome.
    report['fingerprint'] = hashlib.sha256(json.dumps(sorted(fingerprints)).encode()).hexdigest()
    report['summary'] = {}
    if spikes_all:
        report['summary'].update({
            **{f'hit_{k}': {'median': round(statistics.median(v), 3), 'p99': round(quantile(v, .99), 3)} for k, v in hits_all.items()},
            **{f'quiet_{k}': {'median': round(statistics.median(v), 3), 'p99': round(quantile(v, .99), 3)} for k, v in quiet_all.items()},
            'refill_npcs': {'median': round(statistics.median(spikes_all), 3), 'p99': round(quantile(spikes_all, .99), 3)},
            'hit_ticks_per_repeat': report['repeats'][0]['hit_ticks']})
    if focus_all:
        report['summary']['focus'] = {'median': round(statistics.median(focus_all), 4), 'p99': round(quantile(focus_all, .99), 4),
                                      'mean': round(statistics.fmean(focus_all), 4), 'calls_per_repeat': len(focus_all) // args.repeats}
    if wake_all:
        report['summary']['wake'] = {f'{key}_{stat}': round(fn([r[key] for r in wake_all]), 3) for key in ('npcs', 'step')
                                     for stat, fn in (('median', statistics.median), ('max', max))}
        report['summary']['wake']['worlds'] = len(wake_all)
    if patrol_all:
        report['summary']['patrol'] = {
            **{f'{key}_{stat}': round(fn([r[key] for r in patrol_all]), 3) for key in ('npcs', 'step')
               for stat, fn in (('median', statistics.median), ('p99', lambda v: quantile(v, .99)), ('max', max))},
            'ticks': len(patrol_all), 'searches': sum(r['searches'] for r in patrol_all),
            'search_ticks': sum(1 for r in patrol_all if r['searches']),
            'searches_max_per_tick': max(r['searches'] for r in patrol_all)}
    print(json.dumps({'deterministic': report['deterministic'], 'fingerprint': report['fingerprint'],
                      **report['summary']}, indent=1))
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    return 0 if report['deterministic'] else 1


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
