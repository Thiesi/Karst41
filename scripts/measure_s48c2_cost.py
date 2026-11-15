"""S48c2: the opening runner's per-tick cost in the Wake Hall (headless).

One and three callers wake in the hall with a live script and stand still,
so their beat keeps waiting on a polled trigger (the costlier case; a beat
waiting only on pushed facts does no per-tick work). The runner's own time
is measured around `Opening.tick`; the loop's around `HeadlessServer.step`.
Prints a summary and writes JSON, as `measure_p8_cost.py` does. Timing is
observation only; the simulation reads ticks.
"""

import argparse
import json
from pathlib import Path
import statistics
import time

from karst41.content.loader import load_content
from karst41.server.clock import TICK_HZ
from tests.character_helpers import place
from tests.harness import HeadlessServer

ROOT = Path(__file__).resolve().parents[1]
SPOTS = ((6, 6), (10, 7), (15, 7))   # On the hall floor, short of beat 2's row-8 trigger.


def measure(callers: int, seconds: int) -> dict:
    game = HeadlessServer(load_content(ROOT/'src/karst41/content'), character_state={})
    clients = []
    for uid, tile in enumerate(SPOTS[:callers], 1):
        client = game.connect({'user_id': uid, 'handle': f'Cost{uid}', 'node_name': 'S48c2'})
        game.step(1)
        place(game, client, 'vatside-wake-hall', tile)
        clients.append(client)
    runner = game.core.opening
    original, every = runner.tick, []

    def timed(ctx):
        start = time.perf_counter()
        original(ctx)
        every.append(time.perf_counter() - start)
    runner.tick = timed
    loop, lines, polled = [], [0] * callers, 0
    for _ in range(seconds):
        for _ in range(TICK_HZ):
            start = time.perf_counter()
            game.step(1)
            loop.append(time.perf_counter() - start)
            polled += sum(1 for state in runner.states.values() if runner.polled(state))
        for i, client in enumerate(clients):
            lines[i] += sum(1 for f in game.frames_for(client) if f.type == 'log'
                            for line in f.data['lines'] if line['kind'] == 'speech')
    ordered = sorted(every)
    us = 1e6
    return {'callers': callers, 'seconds': seconds,
            'runner_mean_us_per_tick': round(statistics.mean(every) * us, 1),
            'runner_p99_us': round(ordered[int(len(ordered) * .99)] * us, 1),
            'runner_max_us': round(ordered[-1] * us, 1),
            'loop_tick_mean_ms': round(statistics.mean(loop) * 1e3, 3),
            'runner_share_of_loop_percent': round(sum(every) / sum(loop) * 100, 3),
            'polled_caller_ticks': polled,
            'speech_lines_per_caller': lines}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()
    results = [measure(n, args.seconds) for n in (1, 3)]
    for row in results:
        print(json.dumps(row))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
