"""Diagnostic S11 probes for #277 phase 2 and #286 (branch diag/s277-npcs), pass 4.

Pass 4 (s277-diag-4, NPC_CPROFILE): the NPC side is one cProfile per
Npcs.tick instead of the child probes below, so the slow canonical ticks
(the Sable enforcer's act self time and the tick body, unexplained in
s277-diag-3) get a full breakdown. The publisher probes stay as in pass 3.

Harness only: every probe wraps a live function or method for the
profiling window and is put back afterwards. Nothing in src/ changes.

Pass 2 (s277-diag-2) attributed phase 1. Pass 3 drops its IO, hostile and
search probes and keeps only what the technical lead asked for
(2026-11-26):

#277 phase 2, the four candidates:
- nav.step_toward: its steady cost (engine and behaviours call sites);
- npcs.process:<zone>:idle|spawned: Spawners.process per zone, split by
  whether the call spawned anything, so the cost with nothing due shows;
- npcs.tick's body outside its children: every label carries self_ms
  (wall minus its directly nested probed calls), and the tick's direct
  children (process, reserved, evaluate, act, calm_crowds) are probed so
  the body is what remains;
- light.radius:<phase>:in_visible|outside: lighting_radius, split by
  whether Combat.visible is on the stack; combat.visible itself is probed.

#286, the publishers (a 58 ms spike at an early tick in s285-1):
- pub.loop:<owner>: each loop publisher, pub.total their sum per tick;
  pub.sim: the zone views (nested inside Characters' publisher);
- pub.build:<zone>: the zone view body per session;
- pub.frames:<kind>: full frame and delta diff and size measure;
- pub.view:<session>:<kind>:<zone>:<full|delta|skip|same>: one session's
  ViewStore.publish, by what was sent;
- pub.send:<full|delta|skip>: Session.publish_view, the queue side.
  Sessions are opaque ordinals (s1, s2, ...) in first-seen order, never
  IDs or names. Slow publisher calls (0.5 ms or more) list the zone, kind,
  choice, whether a full view was pending and the session's age in ticks.

Every label reports wall_ms (inclusive), net_ms (wall minus the calibrated
cost of every probe call nested inside it) and self_ms (net minus the wall
of its directly nested probed calls). Rankings use net_ms; the tick body
uses self_ms. Each row also carries probe_calls and probe_overhead_ms.
"""

from __future__ import annotations

import time
from typing import Any, Callable

SLOW_S = 0.0005
NPC_CPROFILE = False  # Pass 4 (s277-diag-4): cProfile per Npcs.tick instead of the NPC child probes.
COPIES_ONLY = True  # Pass 5 (s277-diag-5): only the deepcopy sizes and times at the shot sites; every other probe off.
KEEP = 8  # Slowest Npcs ticks per profile window kept with a full breakdown.
SLOW_PREFIXES = ('npcs.act:', 'npcs.evaluate:', 'npcs.process:', 'pub.')


def zone_label(name: str, index: int) -> Callable[..., str]:
    """A label from the call's zone argument; never raises into the game."""
    def label(*args: Any, **kwargs: Any) -> str:
        zone = kwargs.get('zone', args[index] if len(args) > index else None)
        return f'{name}:{getattr(zone, "id", "?")}'
    return label


def fixed(name: str) -> Callable[..., str]:
    return lambda *args, **kwargs: name


def actor_detail(offset: int) -> Callable[..., dict[str, Any]]:
    """Who a slow call was about: template, state, target flag. Never an ID."""
    def detail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        actor = args[offset] if len(args) > offset else None
        zone = args[offset + 1] if len(args) > offset + 1 else None
        behaviour = getattr(actor, 'behaviour', {}) or {}
        return {'zone': getattr(zone, 'id', None), 'template': behaviour.get('template'),
                'state': behaviour.get('state'), 'has_target': bool(getattr(actor, 'target', None)),
                'zone_actors': len(getattr(zone, 'actors', {}) or {})}
    return detail


def zone_detail(index: int) -> Callable[..., dict[str, Any]]:
    def detail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        zone = args[index] if len(args) > index else None
        return {'zone': getattr(zone, 'id', None), 'zone_actors': len(getattr(zone, 'actors', {}) or {})}
    return detail


def body_zone(body: Any) -> str:
    if not isinstance(body, dict):
        return '-'
    me = body.get('me') if isinstance(body.get('me'), dict) else {}
    return str(body.get('zone') or me.get('zone') or '-')


class Probes:
    def __init__(self, service: Any, current: Callable[[], dict[str, Any] | None]) -> None:
        self.service, self.current = service, current
        self.restores: list[Callable[[], None]] = []
        self.per_call_s = 0.0
        self.calls = 0  # Every probe call so far; nested calls are the difference.
        self.stack: list[list[float]] = []  # Per open probe call: [direct children's wall].
        self.in_npcs = False
        self.in_publish = False
        self.visible_depth = 0
        self.sessions: dict[str, str] = {}
        self.sent: list[str] = []  # publish_view choices, read by the enclosing ViewStore.publish label.
        self.record_sizes: dict[str, Any] = {}  # Pass 2's field, kept for the report shape.

    # --- bookkeeping --------------------------------------------------------

    def _timed(self, original: Callable[..., Any], label: Callable[..., str], context: bool = False,
               detail: Callable[..., dict[str, Any]] | None = None,
               before: Callable[..., Any] | None = None) -> Callable[..., Any]:
        """before(*args) runs ahead of the call; its result is passed to label and
        detail as the keyword `state` (both then take it)."""
        def measured(*args: Any, **kwargs: Any) -> Any:
            row = self.current()
            if row is None:
                return original(*args, **kwargs)
            state = None
            if before is not None:
                try:
                    state = before(*args, **kwargs)
                except Exception:  # A probe never raises into the game.
                    state = None
            calls_before = self.calls
            self.stack.append([0.0])
            wall, cpu = time.perf_counter(), time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                wall, cpu = time.perf_counter() - wall, time.thread_time() - cpu
                children = self.stack.pop()[0]
                if self.stack:
                    self.stack[-1][0] += wall
                inner = self.calls - calls_before
                self.calls += 1
                net = max(0.0, wall - inner * self.per_call_s)
                own = max(0.0, wall - children)  # Children's wall carries their own probe cost.
                try:
                    name = label(*args, state=state, **kwargs) if before is not None else label(*args, **kwargs)
                except Exception:
                    name = 'probe.label_error'
                if context:
                    name = ('npc:' if self.in_npcs else 'pub:' if self.in_publish else 'other:') + name
                slot = row['diag'].setdefault(name, [0.0, 0.0, 0, 0.0, 0.0])
                slot[0] += wall
                slot[1] += cpu
                slot[2] += 1
                slot[3] += net
                slot[4] += own
                row['probe_calls'] = row.get('probe_calls', 0) + 1
                if detail is not None and net >= SLOW_S and name.startswith(SLOW_PREFIXES):
                    try:
                        extra = detail(*args, state=state, **kwargs) if before is not None else detail(*args, **kwargs)
                    except Exception:
                        extra = {}
                    row['slow'].append({'label': name, 'net_ms': net * 1000, 'wall_ms': wall * 1000,
                                        'self_ms': own * 1000, 'inner_probe_calls': inner, **extra})
        return measured

    def wrap(self, owner: Any, name: str, label: Callable[..., str], context: bool = False,
             detail: Callable[..., dict[str, Any]] | None = None, before: Callable[..., Any] | None = None) -> None:
        """Wrap a module function, or a method or staticmethod on its class."""
        if owner is None or not hasattr(owner, name):
            return
        own = name in vars(owner)
        original = vars(owner)[name] if own else getattr(owner, name)
        if isinstance(original, classmethod):
            return
        if isinstance(original, staticmethod):
            setattr(owner, name, staticmethod(self._timed(original.__func__, label, context, detail, before)))
        else:
            setattr(owner, name, self._timed(original, label, context, detail, before))
        self.restores.append(lambda: setattr(owner, name, original) if own else delattr(owner, name))

    def calibrate(self, n: int = 20000) -> float:
        """Per-call probe cost on the real call shape: a method-style call with
        actor and zone arguments, a per-zone f-string label and the slow-call
        check, against the same call unwrapped. Best of three rounds."""
        row: dict[str, Any] = {'diag': {}, 'slow': []}
        saved, self.per_call_s = self.current, 0.0
        self.current = lambda: row

        class Zone:
            id, actors = 'calibration-zone', {}

        def bare(owner: Any, actor: Any, zone: Any, tick: int) -> None:
            return None
        wrapped = self._timed(bare, zone_label('calibration', 2), detail=actor_detail(1))
        zone, actor = Zone(), object()
        costs = []
        for _ in range(3):
            began = time.perf_counter()
            for tick in range(n):
                wrapped(self, actor, zone, tick)
            with_probe = time.perf_counter() - began
            began = time.perf_counter()
            for tick in range(n):
                bare(self, actor, zone, tick)
            costs.append(max(0.0, (with_probe - (time.perf_counter() - began)) / n))
        self.per_call_s = min(costs)
        self.current, self.calls = saved, 0
        return self.per_call_s

    # --- install ------------------------------------------------------------

    def install(self) -> None:
        if COPIES_ONLY:
            self._copies()
            return
        if NPC_CPROFILE:
            self._npcs_cprofile()
        else:
            self._npcs()
            self._sight()
        self._publishers()

    # --- pass 4: a per-tick cProfile of Npcs.tick -----------------------------

    def _npcs_cprofile(self) -> None:
        """Pass 4 (s277-diag-4): no NPC child probes. Each Npcs.tick runs under its
        own cProfile.Profile; the KEEP slowest ticks keep a full breakdown (the
        tick's own time, every function by own time, and the callees of
        Npcs.tick and Npcs.act), and the window keeps an aggregate. cProfile
        inflates call-heavy code: figures rank, they don't price."""
        import cProfile
        import heapq
        import pstats
        from karst41.server.npcs.engine import Npcs
        if getattr(self.service.core, 'npcs', None) is None:
            return
        original_tick = vars(Npcs)['tick']
        self.kept: list[tuple[float, int, dict[str, Any]]] = []
        self.window: dict[str, list[float]] = {}
        self.profiled = 0

        def tick(npcs: Any, ctx: Any) -> None:
            if self.current() is None:
                return original_tick(npcs, ctx)
            profile = cProfile.Profile()
            self.in_npcs = True
            began = time.perf_counter()
            profile.enable()
            try:
                return original_tick(npcs, ctx)
            finally:
                profile.disable()
                wall = (time.perf_counter() - began) * 1000
                self.in_npcs = False
                try:
                    stats = pstats.Stats(profile).stats
                    self.profiled += 1
                    for func, (_, calls, own, cumulative, _) in stats.items():
                        slot = self.window.setdefault(_name(func), [0, 0.0, 0.0])
                        slot[0] += calls
                        slot[1] += own * 1000
                        slot[2] += cumulative * 1000
                    if len(self.kept) < KEEP or wall > self.kept[0][0]:
                        entry = (wall, self.profiled, _breakdown(stats, wall, getattr(ctx, 'tick', -1)))
                        (heapq.heappush if len(self.kept) < KEEP else heapq.heapreplace)(self.kept, entry)
                except Exception as exc:  # A probe never raises into the game.
                    self.window.setdefault(f'probe.error:{type(exc).__name__}', [0, 0.0, 0.0])[0] += 1
        Npcs.tick = self._timed(tick, fixed('npcs.tick'))
        self.restores.append(lambda: setattr(Npcs, 'tick', original_tick))

    # --- pass 5: the deepcopies on the shot path ---------------------------------

    def _copies(self) -> None:
        """Pass 5 (s277-diag-5): wrap the module-level `deepcopy` name in characters,
        commerce and skills (each imports it by name). Each copy in the window records
        its call site (function, line and source text), the value's JSON size and the
        copy's own time. The JSON size is measured after the timed copy."""
        import json
        import linecache
        import sys
        from karst41.server import characters, commerce, skills
        self.copies: dict[str, dict[str, list[float]]] = {}
        self.copy_ticks: dict[int, float] = {}
        for module in (characters, commerce, skills):
            original = module.deepcopy

            def measured(value: Any, memo: Any = None, _original: Any = original, _module: Any = module) -> Any:
                row = self.current()
                if row is None or memo is not None:
                    return _original(value, memo)
                began = time.perf_counter()
                result = _original(value)
                took = (time.perf_counter() - began) * 1e6
                try:
                    frame = sys._getframe(1)
                    code = frame.f_code
                    source = linecache.getline(code.co_filename, frame.f_lineno).strip()[:90]
                    site = f'{_module.__name__.rsplit(".", 1)[-1]}.{code.co_name}:{frame.f_lineno} {source}'
                    size = len(json.dumps(value, default=str))
                    slot = self.copies.setdefault(site, {'us': [], 'bytes': []})
                    slot['us'].append(took)
                    slot['bytes'].append(size)
                    tick = row.get('tick', -1)
                    self.copy_ticks[tick] = self.copy_ticks.get(tick, 0.0) + took
                except Exception:  # A probe never raises into the game.
                    pass
                return result
            module.deepcopy = measured
            self.restores.append(lambda module=module, original=original: setattr(module, 'deepcopy', original))

    def copies_report(self) -> dict[str, Any]:
        if not COPIES_ONLY or not hasattr(self, 'copies'):
            return {}
        sites = {}
        for site, slot in sorted(self.copies.items(), key=lambda item: -sum(item[1]['us'])):
            sites[site] = {'calls': len(slot['us']), 'us_p50': round(quantile(slot['us'], .5), 2),
                           'us_p99': round(quantile(slot['us'], .99), 2), 'us_max': round(max(slot['us']), 2),
                           'us_total': round(sum(slot['us']), 1), 'bytes_p50': quantile(slot['bytes'], .5),
                           'bytes_max': max(slot['bytes'])}
        per_tick = sorted(self.copy_ticks.values())
        return {'sites': sites, 'ticks_with_copies': len(per_tick),
                'copy_ms_per_tick': {'p50': round(quantile(per_tick, .5) / 1000, 3), 'p99': round(quantile(per_tick, .99) / 1000, 3),
                                     'max': round(max(per_tick, default=0) / 1000, 3)}}

    def cprofile_report(self) -> dict[str, Any]:
        if not NPC_CPROFILE or not hasattr(self, 'kept'):
            return {}
        top = sorted(self.window.items(), key=lambda item: -item[1][1])[:40]
        return {'profiled_ticks': self.profiled,
                'slowest_ticks': [entry for _, _, entry in sorted(self.kept, key=lambda e: -e[0])],
                'window_by_own_ms': [[name, calls, round(own, 3), round(cum, 3)] for name, (calls, own, cum) in top]}

    def _npcs(self) -> None:
        from karst41.server.npcs import behaviours, engine
        from karst41.server.npcs.engine import Npcs
        from karst41.server.npcs.spawn import Spawners
        if getattr(self.service.core, 'npcs', None) is None:
            return
        own = 'tick' in vars(Npcs)
        original_tick = vars(Npcs)['tick']

        def tick(npcs: Any, ctx: Any) -> None:
            self.in_npcs = True
            try:
                return original_tick(npcs, ctx)
            finally:
                self.in_npcs = False
        Npcs.tick = self._timed(tick, fixed('npcs.tick'))
        self.restores.append(lambda: setattr(Npcs, 'tick', original_tick) if own else delattr(Npcs, 'tick'))

        # The tick's direct children. Class-level wrappers receive self first.
        def spawned(spawners: Any, zone: Any, now: Any) -> int:
            return len(getattr(zone, 'actors', {}) or {})

        def process_label(spawners: Any, zone: Any, now: Any, state: Any = None) -> str:
            grew = state is not None and len(getattr(zone, 'actors', {}) or {}) != state
            return f'npcs.process:{getattr(zone, "id", "?")}:{"spawned" if grew else "idle"}'

        def process_detail(spawners: Any, zone: Any, now: Any, state: Any = None) -> dict[str, Any]:
            return {'zone': getattr(zone, 'id', None), 'zone_actors': len(getattr(zone, 'actors', {}) or {}),
                    'actors_before': state}
        self.wrap(Spawners, 'process', process_label, detail=process_detail, before=spawned)
        self.wrap(Spawners, 'reserved', zone_label('npcs.reserved', 0))  # A staticmethod: no self.
        self.wrap(Npcs, 'evaluate', zone_label('npcs.evaluate', 2), detail=actor_detail(1))
        self.wrap(Npcs, 'act', zone_label('npcs.act', 2), detail=actor_detail(1))
        self.wrap(Npcs, 'calm_crowds', zone_label('npcs.calm_crowds', 1))

        # step_toward: the engine imported it by name; behaviours calls its own global.
        self.wrap(engine, 'step_toward', fixed('nav.step_toward'), context=True)
        self.wrap(behaviours, 'step_toward', fixed('nav.step_toward'), context=True)

    def _sight(self) -> None:
        from karst41.server.combat.engine import Combat
        from karst41.server.world import sight

        def visible(*args: Any, **kwargs: Any) -> str:
            return 'combat.visible'
        original = vars(Combat)['visible']

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.visible_depth += 1
            try:
                return original(*args, **kwargs)
            finally:
                self.visible_depth -= 1
        Combat.visible = self._timed(counted, visible, context=True)
        self.restores.append(lambda: setattr(Combat, 'visible', original))
        # sight_radius calls its module's lighting_radius global (the only caller).
        self.wrap(sight, 'lighting_radius',
                  lambda *a, **k: 'light.radius:' + ('in_visible' if self.visible_depth else 'outside'), context=True)

    def _publishers(self) -> None:
        from karst41.server import sessions as sessions_module
        from karst41.server.sessions import Session
        from karst41.server.views.delta import ViewStore
        from karst41.server.world import simulation
        core = self.service.core
        # The loop holds publishers as methods bound at start-up, so a class-level
        # wrap would miss them: wrap the list entries (pub.loop:<owner>) and,
        # for the nested call Characters.publish makes, ZoneSimulation.publish.
        loop = getattr(core, 'loop', None)
        if loop is not None:
            saved = list(loop.publishers)

            def entry(original: Any) -> Any:
                owner = type(getattr(original, '__self__', None)).__name__

                def publishing(ctx: Any) -> Any:
                    self.in_publish = True
                    try:
                        return original(ctx)
                    finally:
                        self.in_publish = False
                return self._timed(publishing, fixed(f'pub.loop:{owner}'))
            loop.publishers[:] = [entry(original) for original in saved]
            self.restores.append(lambda: loop.publishers.__setitem__(slice(None), saved))
        self.wrap(simulation.ZoneSimulation, 'publish', fixed('pub.sim'))
        # pub.sim's own time: its providers (instance attributes already, so no lookup
        # changes) and its helper methods.
        sim = getattr(getattr(core, 'characters', None), 'sim', None) or getattr(core, 'simulation', None)
        for name in ('interaction_provider', 'scout_provider', 'me_provider', 'target_provider'):
            original = getattr(sim, name, None)
            if callable(original) and name in vars(sim):
                setattr(sim, name, self._timed(original, fixed(f'pub.part:{name}')))
                self.restores.append(lambda name=name, original=original: setattr(sim, name, original))
        for name in ('crew_view', 'place_context'):
            self.wrap(simulation.ZoneSimulation, name, fixed(f'pub.part:{name}'))
        self.wrap(simulation, 'build', zone_label('pub.build', 1), detail=zone_detail(1))
        self.wrap(ViewStore, 'frames', lambda store, session, body, kind='zone', **k: f'pub.frames:{kind}')

        def ordinal(session: Any) -> str:
            return self.sessions.setdefault(getattr(session, 'id', '?'), f's{len(self.sessions) + 1}')

        def choice(session: Any, full: Any, delta: Any, tick: int) -> str:
            if session.closed or session.last_view_tick == tick:
                return 'skip'
            lag = session.view_revision - session.ack_revision
            return 'full' if delta is None or session.needs_full_view or lag > sessions_module.MAX_REVISION_LAG else 'delta'

        def send_before(session: Any, full: Any, delta: Any, tick: int) -> str:
            decided = choice(session, full, delta, tick)
            self.sent.append(decided)
            return decided
        self.wrap(Session, 'publish_view', lambda *a, state=None, **k: f'pub.send:{state}', before=send_before)

        def view_before(store: Any, session: Any, body: Any, tick: int, kind: str = 'zone') -> dict[str, Any]:
            return {'mark': len(self.sent), 'full_pending': bool(session.needs_full_view),
                    'age_ticks': tick - (getattr(session, 'connected_tick', None) or tick)}

        def view_choice(state: Any) -> str:
            if not state:
                return '?'
            sent = self.sent[state['mark']:]
            del self.sent[state['mark']:]
            return sent[-1] if sent else 'same'

        def view_label(store: Any, session: Any, body: Any, tick: int, kind: str = 'zone', state: Any = None) -> str:
            picked = view_choice(state)
            if state is not None:
                state['choice'] = picked
            return f'pub.view:{ordinal(session)}:{kind}:{body_zone(body)}:{picked}'

        def view_detail(store: Any, session: Any, body: Any, tick: int, kind: str = 'zone', state: Any = None) -> dict[str, Any]:
            state = state or {}
            return {'session': ordinal(session), 'kind': kind, 'zone': body_zone(body), 'choice': state.get('choice'),
                    'full_pending': state.get('full_pending'), 'session_age_ticks': state.get('age_ticks'),
                    'first_view': getattr(store, 'previous', None) is None}
        self.wrap(ViewStore, 'publish', view_label, detail=view_detail, before=view_before)

    def restore(self) -> None:
        for undo in reversed(self.restores):
            undo()
        self.restores.clear()

    # --- per tick -----------------------------------------------------------

    def open_row(self, row: dict[str, Any]) -> None:
        row['diag'], row['slow'] = {}, []
        self.sent.clear()

    def close_row(self, row: dict[str, Any]) -> None:
        row['probe_overhead_ms'] = row.get('probe_calls', 0) * self.per_call_s * 1000
        loops = [slot for label, slot in row['diag'].items() if label.startswith('pub.loop:')]
        if loops:
            row['diag']['pub.total'] = [sum(slot[i] for slot in loops) for i in range(5)]
        row['diag'] = {label: {'wall_ms': w * 1000, 'net_ms': net * 1000, 'self_ms': own * 1000,
                               'cpu_ms': c * 1000, 'calls': n}
                       for label, (w, c, n, net, own) in row['diag'].items()}


def _name(func: tuple[str, int, str]) -> str:
    path, line, name = func
    path = path.replace('\\', '/')
    short = path.split('/karst41/', 1)[-1] if '/karst41/' in path else path.rsplit('/', 1)[-1]
    return f'{short}:{line}({name})'


def _breakdown(stats: dict[Any, Any], wall: float, tick: int) -> dict[str, Any]:
    """One profiled tick: own time by function, and the callees of Npcs.tick and Npcs.act."""
    def callees(match: Callable[[tuple[str, int, str]], bool]) -> list[list[Any]]:
        keys = [func for func in stats if match(func)]
        found: dict[str, list[float]] = {}
        for func, (_, _, _, _, callers) in stats.items():
            for key in keys:
                if key in callers:
                    _, calls, own, cumulative = callers[key]
                    slot = found.setdefault(_name(func), [0, 0.0, 0.0])
                    slot[0] += calls
                    slot[1] += own * 1000
                    slot[2] += cumulative * 1000
        return [[name, calls, round(own, 3), round(cum, 3)]
                for name, (calls, own, cum) in sorted(found.items(), key=lambda item: -item[1][2])[:25]]
    engine = lambda name: (lambda func: func[2] == name and func[0].replace('\\', '/').endswith('npcs/engine.py'))
    tick_own = sum(own for func, (_, _, own, _, _) in stats.items() if engine('tick')(func)) * 1000
    by_own = sorted(stats.items(), key=lambda item: -item[1][2])[:30]
    return {'tick': tick, 'npcs_wall_ms_profiled': round(wall, 3), 'tick_own_ms': round(tick_own, 3),
            'by_own': [[_name(func), calls, round(own * 1000, 3), round(cum * 1000, 3)]
                       for func, (_, calls, own, cum, _) in by_own],
            'tick_callees': callees(engine('tick')), 'act_callees': callees(engine('act'))}


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def _slowest(rows: list[dict[str, Any]], label: str, prefix: str, top: int, exclude: str | None = None) -> list[dict[str, Any]]:
    key = lambda row: -row.get('diag', {}).get(label, {}).get('net_ms', 0)
    found = []
    for row in sorted(rows, key=key)[:top]:
        diag = row.get('diag', {})
        if label not in diag:
            continue
        parts = sorted(((name, f['net_ms'], f['self_ms']) for name, f in diag.items()
                        if name != label and prefix in name and not (exclude and exclude in name)), key=lambda item: -item[1])[:10]
        found.append({'tick': row.get('tick'), 'net_ms': round(diag[label]['net_ms'], 3),
                      'self_ms': round(diag[label]['self_ms'], 3), 'total_ms': round(row.get('total_wall_ms', 0), 3),
                      'top': [[name, round(net, 3), round(own, 3)] for name, net, own in parts],
                      'slow_calls': sorted((s for s in row.get('slow', []) if s['label'].startswith(prefix)),
                                           key=lambda s: -s['net_ms'])[:6]})
    return found


def summary(rows: list[dict[str, Any]], top: int = 5) -> dict[str, Any]:
    """p99 and max per label (net, self and wall), the slowest NPC and publisher ticks."""
    n = len(rows)
    figures: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        for label, figure in row.get('diag', {}).items():
            slot = figures.setdefault(label, {'net': [], 'self': [], 'wall': [], 'calls': []})
            slot['net'].append(figure['net_ms'])
            slot['self'].append(figure['self_ms'])
            slot['wall'].append(figure['wall_ms'])
            slot['calls'].append(float(figure['calls']))
    pad = lambda values: values + [0.0] * (n - len(values))  # A tick without the call counts as 0.
    per_label = {}
    for label, slot in figures.items():
        net, own = pad(slot['net']), pad(slot['self'])
        per_label[label] = {'net_p50': round(quantile(net, 0.5), 3), 'net_p99': round(quantile(net, 0.99), 3),
                            'net_max': round(max(net), 3), 'self_p99': round(quantile(own, 0.99), 3),
                            'self_max': round(max(own), 3), 'wall_p99': round(quantile(pad(slot['wall']), 0.99), 3),
                            'calls_p99': quantile(pad(slot['calls']), 0.99), 'ticks_with': len(slot['net'])}
    overhead = [row.get('probe_overhead_ms', 0.0) for row in rows]
    return {'ticks': n, 'labels': dict(sorted(per_label.items(), key=lambda item: -item[1]['net_p99'])),
            'slowest_npc_ticks': _slowest(rows, 'npcs.tick', '', top, exclude='pub'),
            'slowest_publisher_ticks': _slowest(rows, 'pub.total', 'pub.', top),
            'probe_overhead_ms': {'p99': round(quantile(overhead, 0.99), 3), 'max': round(max(overhead, default=0), 3)}}
