"""S11 real native door: populated streets, refusal, heat and local guards.

The heat interval is actually waited at both sizes. No player teleports,
extra money, faction membership or progression fixtures are needed.
"""

import argparse
import asyncio
import gc
from collections import deque
from dataclasses import asdict
import json
from pathlib import Path
import time
import threading
from typing import Any

from accept_s10_netbbs import Connection, NativeProbe, wait
from tests import storage_reader
from acceptance_hall import crossed_quietly
from karst41.admin.client import request
from karst41.server.loop import TickContext
from karst41.server.world.actors import Actor
from karst41.server.world.sight import line_of_sight
from s277_probes import Probes, summary as diag_summary
from write_profile import WriteProfile
from minimal_s40_profile import MinimalProfile
from tick_budget import enforce


async def approach(probe: NativeProbe, caller: Connection, target: Actor,
                   *, hostile_tile: bool = True) -> None:
    actor=caller.avatar.actor
    zone=probe.service.world.zones[actor.zone]
    async with asyncio.timeout(20):
        while not probe.combat.visible(target,actor) or (actor.x-target.x)**2+(actor.y-target.y)**2>16:
            # A moving patrol may leave a geometrically close tile behind
            # a wall or another body. Choose a reachable visible approach,
            # then take only one real-key step before planning again.
            occupied={a.position for a in zone.actors.values() if a is not actor}
            exits={e.tile for e in zone.template.meta.exits if e.kind=='street'}
            pending,previous=deque([actor.position]),{actor.position:None}
            while pending:
                x,y=pending.popleft()
                for tile in ((x+1,y),(x,y+1),(x-1,y),(x,y-1)):
                    if tile not in previous and tile not in occupied and tile not in exits and zone.passable(tile):
                        previous[tile]=(x,y);pending.append(tile)
            candidates=[tile for tile in previous if (not hostile_tile or zone.allows_hostile(tile))
                and (tile[0]-target.x)**2+(tile[1]-target.y)**2<=16
                and line_of_sight(zone,tile,target.position)]
            if not candidates:
                await asyncio.sleep(.1)
                continue
            tile=min(candidates,key=lambda p:(p[0]-actor.x)**2+(p[1]-actor.y)**2)
            if tile == actor.position:
                await asyncio.sleep(.1)
                continue
            step=tile
            while previous[step]!=actor.position:
                step=previous[step]
            # A new actor can occupy even that first step after the search.
            try:
                async with asyncio.timeout(1):
                    await probe.walk(caller,step)
            except TimeoutError:
                pass



def percentiles(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {'count': 0}
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered)-1, int(q*len(ordered)))]
    return {'count': len(ordered), 'p50_ms': pick(.5)*1000, 'p99_ms': pick(.99)*1000, 'max_ms': ordered[-1]*1000}


def writer_window(io: Any, marks: dict[str, int], counters: dict[str, int]) -> dict[str, Any]:
    """S47 figures over the sampling window (technical lead's ruling (e))."""
    window = {key: io.samples(key, marks.get('sample', 0)) for key in ('commit_seconds', 'latency_seconds', 'apply_seconds')}
    summary = {'commit': percentiles(window['commit_seconds']),
               'submit_to_settle': percentiles(window['latency_seconds']),
               'max_queue_depth': io.stats['max_depth'],
               'ticks_with_receipt_in_flight_across_step':
                   io.stats['ticks_in_flight_across_step']-counters.get('ticks_in_flight_across_step', 0),
               'steps': io.stats['steps']-counters.get('steps', 0),
               'paused_ticks': io.stats['paused_ticks']-counters.get('paused_ticks', 0)}
    return {'summary': summary,
            'guard': 'armed for the whole run: GuardedConnection on the live connection (any other thread raises '
                     'LoopThreadSQLite) and storage_reader.loop_guard: no connection to this world.db opens on the loop thread',
            'commit_seconds': window['commit_seconds'], 'latency_seconds': window['latency_seconds']}


async def profile(probe: NativeProbe, width: int, height: int,
                  title: str = 'S11 tick budget') -> float:
    # Only status, sparse commit exits and two loop boundaries are added.
    # Detailed write wrappers/proxies remain in the later diagnostic.
    with (WriteProfile(probe.service, trace_writes=False) as writes_profile,
          MinimalProfile(probe.service) as minimal):
        return await _profile(probe, width, height, writes_profile, minimal, title)


async def _profile(probe: NativeProbe, width: int, height: int,
                   writes_profile: WriteProfile, minimal: MinimalProfile,
                   title: str = 'S11 tick budget') -> float:
    loop = probe.service.core.loop
    original_subsystems, original_step = loop.subsystems, loop.step
    original_writes = probe.service._character_writes
    io = probe.service.io
    carry = {'wall': 0.0, 'cpu': 0.0, 'calls': 0}  # S47: settles between steps, for the next tick's row.
    gc_starts: dict[tuple[int, int], tuple[float, float, int]] = {}
    gc_pauses: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sampling_tick, status = -1, {}
    marks: dict[str, int] = {}
    counters: dict[str, int] = {}

    def gc_trace(phase: str, info: dict[str, int]) -> None:
        identity = threading.get_ident(), info['generation']
        if phase == 'start':
            gc_starts[identity] = time.perf_counter(), time.thread_time(), probe.service.clock.now_tick()
        elif phase == 'stop':
            began = gc_starts.pop(identity, None)
            if began:
                gc_pauses.append({'tick': began[2], 'thread': identity[0], 'generation': identity[1],
                    'wall_ms': (time.perf_counter()-began[0])*1000,
                    'cpu_ms': (time.thread_time()-began[1])*1000})

    class TimedSubsystem:
        def __init__(self, subsystem: Any, label: str) -> None:
            self.subsystem, self.label = subsystem, label

        def tick(self, ctx: TickContext) -> None:
            wall, cpu = time.perf_counter(), time.thread_time()
            try:
                self.subsystem.tick(ctx)
            finally:
                if current is not None:
                    current['segments'][self.label] = {
                        'wall_ms': (time.perf_counter()-wall)*1000,
                        'cpu_ms': (time.thread_time()-cpu)*1000}

    probes = Probes(probe.service, lambda: current)
    probes.calibrate()

    def step() -> None:
        nonlocal current
        current = {'tick': probe.service.clock.now_tick(), 'segments': {},
                   '_wall': time.perf_counter(), '_cpu': time.thread_time()}
        probes.open_row(current)
        wall, cpu = time.perf_counter_ns(), time.thread_time()
        try:
            original_step()
        finally:
            ended = time.perf_counter_ns()
            current['tick'] = loop.last_tick
            current['loop_wall_ms'] = (ended-wall)/1_000_000
            current['loop_cpu_ms'] = (time.thread_time()-cpu)*1000
            current.update(minimal.intervals(wall, ended))

    def writes() -> None:
        nonlocal current
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            original_writes()
        finally:
            if current is None:
                # S47: results settle as they arrive, between steps; the next tick's row carries them.
                carry['wall'] += time.perf_counter()-wall
                carry['cpu'] += time.thread_time()-cpu
                carry['calls'] += 1
            else:
                current['writes_wall_ms'] = (time.perf_counter()-wall+carry['wall'])*1000
                current['writes_cpu_ms'] = (time.thread_time()-cpu+carry['cpu'])*1000
                current['settles_between_steps'] = carry['calls']
                carry.update(wall=0.0, cpu=0.0, calls=0)
                current['total_wall_ms'] = (time.perf_counter()-current.pop('_wall'))*1000
                current['total_cpu_ms'] = (time.thread_time()-current.pop('_cpu'))*1000
                probes.close_row(current)
                rows.append(current)
                current = None

    loop.subsystems = [TimedSubsystem(system, f'{index}:{type(system).__name__}')
                       for index, system in enumerate(original_subsystems)]
    loop.step = step
    probe.service._character_writes = writes
    probes.install()
    gc.callbacks.append(gc_trace)
    for zone in probe.service.world.zones.values():
        zone.activate("s11-profiling",probe.service.clock.now_tick())
    try:
        await asyncio.sleep(2)
        sampling_tick = probe.service.clock.now_tick()
        loop.timings.clear()
        marks = {'sample': io.sample}
        counters = {key: io.stats[key] for key in ('ticks_in_flight_across_step', 'steps', 'paused_ticks')}
        io.stats['max_depth'] = 0
        await asyncio.sleep(10)
        status=await request(probe.service.install_dir,"status")
        count = len(probe.service.world.zones)
        assert status["active_zones"]==count,status
        # diag/s277-npcs only: the probed gate is recorded, never a stop, so the
        # canonical (pursuit) profile is always captured. Never on main.
        try:
            verdict=enforce(status["tick_p99_ms"],f"admin status {width}x{height}",title=title)
        except AssertionError as error:
            verdict=f"block, probes on: {error}"
        probe.report(f"DIAG GATE (probes on, not enforced) admin status {width}x{height}: all {count} zones active, p99 {status['tick_p99_ms']:.3f} ms ({verdict})")
        return status["tick_p99_ms"]
    finally:
        loop.subsystems, loop.step = original_subsystems, original_step
        probes.restore()
        diag = diag_summary(rows)
        probe.report(f"S277 DIAG {width}x{height}: " + json.dumps({
            'probe_overhead_ms': diag['probe_overhead_ms'], 'per_call_us': round(probes.per_call_s*1e6, 3),
            'labels': dict(list(diag['labels'].items())[:60]), 'slowest_npc_ticks': diag['slowest_npc_ticks'][:3],
            'slowest_publisher_ticks': diag['slowest_publisher_ticks'][:3]}))
        probe.service._character_writes = original_writes
        if gc_trace in gc.callbacks:
            gc.callbacks.remove(gc_trace)
        for zone in probe.service.world.zones.values():
            zone.deactivate("s11-profiling",probe.service.clock.now_tick())
        writer_figures = writer_window(io, marks if sampling_tick >= 0 else {}, counters if sampling_tick >= 0 else {})
        probe.report(f"S47 WRITER {width}x{height}: " + json.dumps(writer_figures['summary']))
        number = len(list(probe.root.glob(f'npc-profile-{width}x{height}-*.json')))+1
        (probe.root/f'npc-profile-{width}x{height}-{number}.json').write_text(json.dumps({
            'status': status, 'sampling_started_tick': sampling_tick,
            'instrumentation': 'wall/thread CPU and GC probes; writes = settle + submit per tick (S47); S11 gate including overhead: p99 at or above 20 ms blocks, at or above 10 ms warns (2026-11-12)',
            'gc_pauses': gc_pauses, 'writer': writer_figures,
            'ticks': rows, 'write_probe': writes_profile.metadata(),
            'diag_probes': {'branch': 'diag/s277-npcs', 'pass': 3, 'per_call_us': probes.per_call_s*1e6,
                            'record_sizes': probes.record_sizes, 'summary': diag,
                            'note': 'probes on: the gate includes their overhead; probe_overhead_ms per row bounds it'},
            'commerce_commits': minimal.export(),
            'canonical_observer': {
                'commit': 'Two perf_counter_ns reads around existing SQLite context exit; wall only',
                'loop': 'Two extra perf_counter_ns reads: after inputs, before publishers',
                'limits': 'Commit failure may include SQLite rollback; no thread CPU or rusage; no gate correction'},
            'write_calls': list(writes_profile.calls),
            'gate': writes_profile.gate(status) if status else None}, indent=2)+'\n', encoding='utf-8')


async def write_diagnostic(probe: NativeProbe, width: int, height: int) -> None:
    """Collect detailed all-hub writes after the canonical result is saved."""
    loop = probe.service.core.loop
    # Model the NPC armour-learning receipt after the canonical result is
    # saved. This is a current snapshot, not the stalled receipt's payload.
    game, commerce = probe.service.core.characters, probe.service.core.commerce
    records = [player.record() for player in game.players.values()]
    player = max(records, key=lambda row: len(json.dumps(row).encode('utf-8')))
    shape = {'basis': 'Post-result largest current player; record_use player-only receipt',
             'tick': loop.last_tick, 'not_captured_stalled_payload': True,
             'effect': {'player': player, 'players': [], 'items': [], 'delete_items': [],
                        'budgets': {key: asdict(value) for key, value in commerce.budgets.items()},
                        'caches': commerce.caches, 'serial': commerce.serial}}
    (probe.root/'disk-baseline-shape.json').write_text(
        json.dumps(shape, ensure_ascii=False)+'\n', encoding='utf-8')
    began_tick, began = loop.last_tick, time.perf_counter()
    for zone in probe.service.world.zones.values():
        zone.activate('s40-write-diagnostic', probe.service.clock.now_tick())
    trace = WriteProfile(probe.service)
    try:
        with trace:
            await asyncio.sleep(120)
    finally:
        try:
            path = probe.root/f'npc-write-diagnostic-{width}x{height}.json'
            manifest = trace.export_trace(path, {
                'diagnostic_only': True, 'phase': 'after_final_canonical_gate',
                'began_tick': began_tick, 'ended_tick': loop.last_tick,
                'all_hub_zones': len(probe.service.world.zones),
                'requested_seconds': 120, 'elapsed_seconds': time.perf_counter()-began,
                'retention': 'Latest calls only, bounded by loop.timings.maxlen',
                'limits': 'Detailed probe overhead is included here; these are not gate samples.'})
            (probe.root/f'npc-write-shape-{width}x{height}.json').write_text(
                json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
            probe.report(f"S40 DIAGNOSTIC {width}x{height}: {manifest['exported_calls']} calls, "
                         f"{manifest['trace_bytes']} bytes, export {manifest['export_seconds']:.3f}s; not a gate")
        finally:
            for zone in probe.service.world.zones.values():
                zone.deactivate('s40-write-diagnostic', probe.service.clock.now_tick())
            del trace


async def sodium_response(probe: NativeProbe, caller: Connection, width: int, height: int) -> None:
    actor=caller.avatar.actor
    await probe.walk(caller,(16,5))
    sodium=probe.service.world.zones[actor.zone]
    guards=[a for a in sodium.actors.values() if a.behaviour.get("template")=="npc-sable-enforcer"]
    assert len(guards)>=2 and all(a.faction=="sable" for a in guards)
    victim=min(guards,key=lambda a:(a.x-actor.x)**2+(a.y-actor.y)**2)
    responders=[a for a in guards if a.id!=victim.id]
    # #147 makes regulars unaffiliated: Sable no longer answers their attacks.
    # Provoke a real Sable peer and require another enforcer to answer.
    # Keep using real movement/fire keys if the victim leaves weapon range.
    async with asyncio.timeout(30):
        while not any(a.target==actor.id for a in responders):
            if (victim.x-actor.x)**2+(victim.y-actor.y)**2>16:
                occupied={a.position for a in sodium.actors.values()}
                candidates=[(x,y) for x in range(victim.x-1,victim.x+2) for y in range(victim.y-1,victim.y+2)
                    if sodium.passable((x,y)) and (x,y) not in occupied]
                tile=min(candidates,key=lambda p:(p[0]-actor.x)**2+(p[1]-actor.y)**2)
                await probe.walk(caller,tile)
            await probe.target(caller,victim.id)
            await caller.caller.write(b"f")
            await asyncio.sleep(.25)
    assert not any(a.faction=="wardens" and a.target==actor.id for a in sodium.actors.values())
    probe.save(caller,f"s11-sodium-{width}x{height}")
    probe.report(f"PASS local response {width}x{height}: another Sable enforcer answers an attack on a Sable peer, no Wardens")


async def accept(root: Path, door_python: Path, show: bool,
                 sizes: tuple[tuple[int, int], ...] = ((80,24),(132,50))) -> None:
    probe=NativeProbe(root,door_python,show)
    await probe.start()
    results=[]
    # S47: no SQLite connection to this install's world.db opens on the loop thread for the whole run.
    guard=storage_reader.loop_guard(threading.get_ident(),database=probe.service.install_dir/"world.db")
    guard.__enter__()
    try:
        for width,height in sizes:
            caller=await probe.open(width,height,"NpcRunner",0,True)
            actor=caller.avatar.actor
            # S48c1b: a character crosses the drone's ring and nothing happens.
            await crossed_quietly(probe.service, caller.session.player_id, probe.walk(caller, (21, 14)))
            await probe.travel(caller,"vatside-clinics")
            await probe.travel(caller,"core-plaza")
            await probe.walk(caller,(20,6))
            plaza=probe.service.world.zones["core-plaza"]
            before={a.id:a.position for a in plaza.actors.values() if a.kind!="player"}
            moved=set()
            for _ in range(30):
                await asyncio.sleep(.1)
                moved.update(a.behaviour.get("template") for a in plaza.actors.values()
                             if a.id in before and a.position!=before[a.id])
            assert {"npc-civilian","npc-warden-patrol","npc-courier"} <= moved,moved
            await wait(lambda:"~" in caller.caller.screen.screen_text())
            await profile(probe,width,height)
            # Profiling leaves the simulation running: civilians can walk
            # out of sight. Approach a real civilian instead of assuming
            # one is still visible from the earlier observation point.
            target=min((a for a in plaza.actors.values() if a.behaviour.get("template")=="npc-civilian"),
                       key=lambda a:(a.x-actor.x)**2+(a.y-actor.y)**2)
            await approach(probe,caller,target,hostile_tile=False)
            await probe.target(caller,target.id)
            hp=actor.health;target_hp=target.health
            started=time.monotonic()
            await caller.caller.write(b"f")
            await wait(lambda:probe.game.players[actor.id].warden_heat_until is not None,2)
            response=time.monotonic()-started
            assert len([a for a in plaza.actors.values() if a.behaviour.get("response")==actor.id])==2
            for _ in range(3):
                await caller.caller.write(b"f");await asyncio.sleep(.2)
            assert (actor.health,target.health)==(hp,target_hp)
            await wait(lambda:"HEAT" in caller.caller.screen.screen_text().splitlines()[0])
            probe.save(caller,f"s11-plaza-{width}x{height}")
            probe.report(f"PASS NPC plaza {width}x{height}: patrol, wander, courier, relation glyph, warning {response:.2f}s, two responders, safe HP")
            await probe.travel(caller,"vatside-clinics")
            await probe.walk(caller,(44,3))
            patrol=next(a for a in probe.service.world.zones[actor.zone].actors.values() if a.behaviour.get("template")=="npc-warden-patrol")
            await approach(probe,caller,patrol)
            await wait(lambda:patrol.target==actor.id,3)
            probe.report(f"PASS heat pursuit {width}x{height}: Clinic Row patrol targets caller after visible warning")
            # Retreat to the real public-clinic pocket before waiting out heat.
            clinic=next(o for o in probe.service.world.zones[actor.zone].objects.values() if o.spec.essential and o.spec.params.get("vendor"))
            await probe.walk(caller,clinic.spec.tile)
            assert not probe.service.world.zones[actor.zone].allows_hostile(actor.position)
            await wait(lambda:patrol.target is None,3)
            safe_hp=actor.health
            until=probe.game.players[actor.id].warden_heat_until
            from datetime import datetime
            while probe.combat.now()<datetime.fromisoformat(until).timestamp():
                await asyncio.sleep(min(30,datetime.fromisoformat(until).timestamp()-probe.combat.now()))
                probe.report(f"WAIT heat {width}x{height}: {max(0,datetime.fromisoformat(until).timestamp()-probe.combat.now()):.0f}s remain in protected clinic")
                assert actor.health==safe_hp
            await probe.walk(caller,(44,3))
            await approach(probe,caller,patrol)
            await asyncio.sleep(3)
            assert patrol.target is None
            assert not probe.game.me(caller.session)["heat_until"]
            probe.report(f"PASS actual ten-minute heat expiry {width}x{height}: patrol ignores Freelance again")
            await probe.travel(caller,"core-plaza")
            await probe.travel(caller,"sodium-row")
            await sodium_response(probe,caller,width,height)
            try:
                p99=await profile(probe,width,height)
            except AssertionError:
                probe.report(f"S40 DIAGNOSTIC {width}x{height}: final canonical gate FAILED; preserving result before detailed capture")
                await write_diagnostic(probe,width,height)
                raise
            # With both sizes in one service, leave every canonical window
            # ahead of detailed collection. CI uses one service per size.
            if (width, height) == sizes[-1]:
                probe.report(f"S40 DIAGNOSTIC {width}x{height}: final canonical gate passed; collecting supplementary evidence")
                await write_diagnostic(probe,width,height)
            await probe.leave(caller,f"s11-complete-{width}x{height}")
            results.append({"size":[width,height],"warning_seconds":response,"heat_wait":"actual 600-second deadline",
                "safe_damage":False,"local_guards":True,"all_hub_p99_ms":p99})
        assert not probe.errors,probe.errors
        (probe.root/"result.json").write_text(json.dumps({"passed":True,"diag":"diag/s277-npcs: probes on, gate recorded but not enforced","transport":"NetBBS Telnet native stdio",
            "teleports":False,"human_playtest":False,"loop_thread_guard":"armed for the whole run (S47)",
            "runs":results},indent=2),encoding="utf-8")
    finally:
        guard.__exit__(None,None,None)
        await probe.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--door-python",type=Path,required=True)
    parser.add_argument("--show",action="store_true")
    parser.add_argument("--size", choices=("80x24", "132x50"),
                        help="Run one profile; the default runs both sizes.")
    args=parser.parse_args()
    sizes = (tuple(map(int,args.size.split("x"))),) if args.size else ((80,24),(132,50))
    asyncio.run(accept(args.root,args.door_python,args.show,sizes))
