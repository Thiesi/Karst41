"""#284 diagnostic: does Clinic Row's Warden patrol stay on its waypoint loop?

Headless, no caller: Clinic Row is kept active and the world runs for --ticks
ticks (600 s at the default 6000) on each of --seeds zone-seed sets. The
patrol counts as trapped when it never reaches its north-street loop
(x 22-41, y 2-5) from its spawn tile, or when, after first reaching it, it
stays off the loop for 300 ticks (30 s) straight. Before
#284 the greedy step could be lured into a permanent orbit under the east
clinic block (x 38-40, y 13-32); with waypoint routing it should never be.

Run it against a tree for a before/after:

    PYTHONPATH="<tree>/src;<tree>" python scripts/diag_284_patrol_trap.py --seeds 20

Prints one JSON line per seed and a summary line.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFF_LOOP_TICKS = 300


def on_loop(actor) -> bool:
    return 2 <= actor.y <= 5 and 22 <= actor.x <= 41


def run(content, seed: int, ticks: int) -> dict:
    from tests.harness import HeadlessServer
    game = HeadlessServer(content, character_state={},
                          zone_seeds={zone: seed * 1000 + index for index, zone in enumerate(sorted(content.zones))})
    zone = game.core.world.zones['vatside-clinics']
    zone.activate('diag-284', game.clock.now_tick())
    off, arrived, trapped_at, longest, patrol = 0, None, None, 0, None
    for tick in range(ticks):
        game.step()
        patrol = next(a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-warden-patrol')
        if arrived is None and on_loop(patrol):
            arrived = tick
        off = 0 if on_loop(patrol) else off + 1
        if arrived is not None:
            longest = max(longest, off)
            if off == OFF_LOOP_TICKS and trapped_at is None:
                trapped_at = tick - OFF_LOOP_TICKS
    # A patrol that never reaches its loop from its spawn tile is trapped from the start.
    return {'seed': seed, 'arrived_tick': arrived, 'trapped_from_tick': 0 if arrived is None else trapped_at,
            'longest_off_loop_ticks': longest, 'end': list(patrol.position) if patrol else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seeds', type=int, default=20)
    parser.add_argument('--first-seed', type=int, default=101)
    parser.add_argument('--ticks', type=int, default=6000)
    args = parser.parse_args()
    import karst41
    from karst41.content.loader import load_content
    content = load_content(ROOT / 'src/karst41/content')
    content.walls = None
    rows = []
    for seed in range(args.first_seed, args.first_seed + args.seeds):
        rows.append(run(content, seed, args.ticks))
        print(json.dumps(rows[-1]), flush=True)
    trapped = [r for r in rows if r['trapped_from_tick'] is not None]
    print(json.dumps({'tree': str(Path(karst41.__file__).resolve().parents[2]), 'seeds': len(rows), 'ticks': args.ticks,
                      'trapped': len(trapped), 'trapped_from_ticks': sorted(r['trapped_from_tick'] for r in trapped),
                      'never_arrived': sum(1 for r in rows if r['arrived_tick'] is None),
                      'arrived_tick_max': max((r['arrived_tick'] for r in rows if r['arrived_tick'] is not None), default=None),
                      'longest_off_loop_ticks': max(r['longest_off_loop_ticks'] for r in rows)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
