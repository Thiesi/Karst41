"""S48d: the wall voice's cost on the Row (headless).

One and three created callers stand on the Row with the layer on, and again
with it off. The voice's own time is measured around `Walls.stride` (the
once-a-second poll on P8's stride: idle, region, zone_entered and pushed
windows) and `Walls.input`; the loop's around `HeadlessServer.step`. The
callers stand still, so idle and region are checked every stride, which is
the costlier case. Prints a summary and writes JSON, as
`measure_s48c2_cost.py` does. Timing is observation only; the simulation
reads ticks.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import time

from karst41.content.loader import load_content
from karst41.server.clock import TICK_HZ
from tests.character_helpers import create, place
from tests.harness import HeadlessServer

ROOT = Path(__file__).resolve().parents[1]
SPOTS = ((40, 10), (44, 12), (60, 22))   # Open Row floor, in no line's region.


def measure(callers: int, seconds: int, enabled: bool) -> dict:
    content = load_content(ROOT/'src/karst41/content')
    content.walls = replace(content.walls, enabled=enabled)
    game = HeadlessServer(content, character_state={})
    clients = []
    for uid, tile in enumerate(SPOTS[:callers], 1):
        client = game.connect({'user_id': uid, 'handle': f'Cost{uid}', 'node_name': 'S48d'})
        create(game, client, name=f'Cost{uid}', card='none')
        place(game, client, 'sodium-row', tile)
        clients.append(client)
    voice = game.core.walls
    spent: list[float] = []
    for name in ('stride', 'input'):
        original = getattr(voice, name)

        def timed(*args, _original=original, **kwargs):
            start = time.perf_counter()
            result = _original(*args, **kwargs)
            spent.append(time.perf_counter() - start)
            return result
        setattr(voice, name, timed)
    game.core.characters.sim.voice_input = voice.input
    loop, lines = [], [0] * callers
    texts = {ln.text for ln in content.walls.lines}
    for _ in range(seconds):
        for _ in range(TICK_HZ):
            start = time.perf_counter()
            game.step(1)
            loop.append(time.perf_counter() - start)
        game.commit_commerce()
        for i, client in enumerate(clients):
            lines[i] += sum(1 for f in game.frames_for(client) if f.type == 'log'
                            for line in f.data['lines'] if line['text'] in texts)
    us = 1e6
    ordered = sorted(spent) or [0.0]
    return {'callers': callers, 'seconds': seconds, 'enabled': enabled,
            'voice_calls': len(spent),
            'voice_mean_us_per_call': round(statistics.mean(ordered) * us, 1),
            'voice_max_us': round(ordered[-1] * us, 1),
            'voice_us_per_tick': round(sum(spent) / len(loop) * us, 2),
            'loop_tick_mean_ms': round(statistics.mean(loop) * 1e3, 3),
            'voice_share_of_loop_percent': round(sum(spent) / sum(loop) * 100, 3),
            'wall_lines_per_caller': lines}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()
    results = [measure(n, args.seconds, on) for on in (True, False) for n in (1, 3)]
    for row in results:
        print(json.dumps(row))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
