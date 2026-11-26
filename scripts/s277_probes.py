"""Diagnostic S11 probes for #277 and #280 (branch diag/s277-npcs), pass 2.

Harness only: every probe wraps a live function or method for the
profiling window and is put back afterwards. Nothing in src/ changes.

Pass 1 (s277-diag-1) wrapped the inner calls too (move, visible, legal,
line_of_sight, now_local, event_motion and others) and shadowed methods
with instance attributes. Its Npcs segment ran about 6 ms above the
unprobed baseline against about 1 ms of reported overhead (technical lead,
2026-11-26). Pass 2 keeps the top level only and wraps methods on their
class, so call sites keep their usual method lookup:

#277, the NPC tick:
- Npcs.tick; per zone: Spawners.process, Npcs.evaluate, Npcs.act,
  Npcs.calm_crowds; Spawners.location (the spawn walk, for the early-tick
  spike); Npcs.hostile, labelled npc: inside the NPC tick and other:
  outside it;
- navigation: step_toward, route_toward, search (with hit and miss
  counts; a miss expands the full ROUTE_NODES bound);
- slow single calls: any act, evaluate, process, location or search call
  of 0.5 ms or more is listed with its zone, template, state and target
  flag (never a player's name or ID).

#280, the submit side of the writes label (settle plus pump, on the loop):
- IO.settle, pump, mark_world, mark per record kind (meta records by
  name), _batch, _submit, _character, _flush_due, frozen (deepcopy), the
  contracts record, writer.submit, commerce.prepare;
- after the window, outside every timed tick: the JSON size of each meta
  record mark_world re-freezes.

Every label reports `wall_ms` (inclusive, as measured) and `net_ms`: wall
minus the calibrated cost of every probe call nested inside it. Rankings
use net_ms. Each row also carries probe_calls and probe_overhead_ms.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

SLOW_S = 0.0005
SLOW_PREFIXES = ('npcs.act:', 'npcs.evaluate:', 'npcs.process:', 'spawn.location:', 'nav.search')


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


class Probes:
    def __init__(self, service: Any, current: Callable[[], dict[str, Any] | None]) -> None:
        self.service, self.current = service, current
        self.restores: list[Callable[[], None]] = []
        self.per_call_s = 0.0
        self.calls = 0  # Every probe call so far; nested calls are the difference.
        self.in_npcs = False
        self.record_sizes: dict[str, Any] = {}

    # --- bookkeeping --------------------------------------------------------

    def _timed(self, original: Callable[..., Any], label: Callable[..., str], context: bool = False,
               detail: Callable[..., dict[str, Any]] | None = None) -> Callable[..., Any]:
        def measured(*args: Any, **kwargs: Any) -> Any:
            row = self.current()
            if row is None:
                return original(*args, **kwargs)
            before = self.calls
            wall, cpu = time.perf_counter(), time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                wall, cpu = time.perf_counter() - wall, time.thread_time() - cpu
                inner = self.calls - before
                self.calls += 1
                net = max(0.0, wall - inner * self.per_call_s)
                name = label(*args, **kwargs)
                if context:
                    name = ('npc:' if self.in_npcs else 'other:') + name
                slot = row['diag'].setdefault(name, [0.0, 0.0, 0, 0.0])
                slot[0] += wall
                slot[1] += cpu
                slot[2] += 1
                slot[3] += net
                row['probe_calls'] = row.get('probe_calls', 0) + 1
                if detail is not None and net >= SLOW_S and name.startswith(SLOW_PREFIXES):
                    try:
                        extra = detail(*args, **kwargs)
                    except Exception:  # A probe never raises into the game.
                        extra = {}
                    row['slow'].append({'label': name, 'net_ms': net * 1000, 'wall_ms': wall * 1000,
                                        'inner_probe_calls': inner, **extra})
        return measured

    def wrap(self, owner: Any, name: str, label: Callable[..., str], context: bool = False,
             detail: Callable[..., dict[str, Any]] | None = None) -> None:
        """Wrap a module function or a class's method (on the class itself)."""
        if owner is None or not hasattr(owner, name):
            return
        own = name in vars(owner)
        original = vars(owner)[name] if own else getattr(owner, name)
        if isinstance(original, (staticmethod, classmethod)):
            return  # Not used in pass 2.
        setattr(owner, name, self._timed(original, label, context, detail))
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
        self._npcs()
        self._io()

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

        # Class-level wrappers receive self first: the zone index shifts by one.
        self.wrap(Spawners, 'process', zone_label('npcs.process', 1), detail=zone_detail(1))
        self.wrap(Spawners, 'location', zone_label('spawn.location', 1), detail=zone_detail(1))
        self.wrap(Npcs, 'evaluate', zone_label('npcs.evaluate', 2), detail=actor_detail(1))
        self.wrap(Npcs, 'act', zone_label('npcs.act', 2), detail=actor_detail(1))
        self.wrap(Npcs, 'calm_crowds', zone_label('npcs.calm_crowds', 1))
        self.wrap(Npcs, 'hostile', fixed('npcs.hostile'), context=True)

        # Navigation: the engine imported these by name; behaviours calls its
        # own module globals (route_toward -> search, step_toward).
        self.wrap(engine, 'step_toward', fixed('nav.step_toward'))
        self.wrap(engine, 'route_toward', fixed('nav.route_toward'))
        self.wrap(behaviours, 'step_toward', fixed('nav.step_toward'))
        self._search(behaviours)

    def _search(self, behaviours: Any) -> None:
        """search with hit/miss counts: a miss expands the whole node bound."""
        original = behaviours.search

        def search(actor: Any, zone: Any, goal: Any, avoid: Any, blocked: Any) -> Any:
            path = original(actor, zone, goal, avoid, blocked)
            row = self.current()
            if row is not None:
                slot = row['diag'].setdefault('nav.search.hit' if path else 'nav.search.miss', [0.0, 0.0, 0, 0.0])
                slot[2] += 1
            return path
        behaviours.search = self._timed(search, fixed('nav.search'), detail=actor_detail(0))
        self.restores.append(lambda: setattr(behaviours, 'search', original))

    def _io(self) -> None:
        from karst41.server import io as io_module
        io = getattr(self.service, 'io', None)
        if io is None:
            return
        cls = type(io)
        self.wrap(cls, 'settle', fixed('io.settle'))
        self.wrap(cls, 'pump', fixed('io.pump'))

        def mark_label(io: Any, kind: str, record_id: str, value: Any = None) -> str:
            return f'io.mark:{kind}:{record_id}' if kind == 'meta' else f'io.mark:{kind}'
        self.wrap(cls, 'mark', mark_label)
        for name in ('mark_world', '_batch', '_submit', '_character', '_flush_due'):
            self.wrap(cls, name, fixed('io.' + name.lstrip('_')))
        self.wrap(io_module, 'frozen', fixed('io.frozen'))
        writer = getattr(io, 'writer', None)
        self.wrap(type(writer) if writer is not None else None, 'submit', fixed('io.writer_submit'))
        core = self.service.core
        contracts, commerce = getattr(core, 'contracts', None), getattr(core, 'commerce', None)
        self.wrap(type(contracts) if contracts is not None else None, 'record', fixed('io.record:contracts'))
        self.wrap(type(commerce) if commerce is not None else None, 'prepare', fixed('io.commerce_prepare'))

    def restore(self) -> None:
        for undo in reversed(self.restores):
            undo()
        self.restores.clear()
        self._record_sizes()

    def _record_sizes(self) -> None:
        """The JSON size of each re-frozen meta record, after the window."""
        io = getattr(self.service, 'io', None)
        if io is None or not hasattr(io, '_recorders'):
            return
        for name, record in io._recorders().items():
            try:
                self.record_sizes[name] = len(json.dumps(record(), ensure_ascii=False, sort_keys=True))
            except Exception as exc:  # Diagnostic only.
                self.record_sizes[name] = f'error: {type(exc).__name__}'

    # --- per tick -----------------------------------------------------------

    def open_row(self, row: dict[str, Any]) -> None:
        row['diag'], row['slow'] = {}, []

    def close_row(self, row: dict[str, Any]) -> None:
        row['probe_overhead_ms'] = row.get('probe_calls', 0) * self.per_call_s * 1000
        row['diag'] = {label: {'wall_ms': w * 1000, 'net_ms': net * 1000, 'cpu_ms': c * 1000, 'calls': n}
                       for label, (w, c, n, net) in row['diag'].items()}


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def summary(rows: list[dict[str, Any]], top: int = 5) -> dict[str, Any]:
    """p99 and max per label (net and wall), plus the slowest NPC ticks by net time."""
    n = len(rows)
    net: dict[str, list[float]] = {}
    wall: dict[str, list[float]] = {}
    calls: dict[str, list[float]] = {}
    for row in rows:
        for label, figure in row.get('diag', {}).items():
            net.setdefault(label, []).append(figure['net_ms'])
            wall.setdefault(label, []).append(figure['wall_ms'])
            calls.setdefault(label, []).append(float(figure['calls']))
    pad = lambda values: values + [0.0] * (n - len(values))  # A tick without the call counts as 0.
    per_label = {label: {'net_p99': round(quantile(pad(net[label]), 0.99), 3), 'net_max': round(max(pad(net[label])), 3),
                         'wall_p99': round(quantile(pad(wall[label]), 0.99), 3),
                         'calls_p99': quantile(pad(calls[label]), 0.99)} for label in net}
    key = lambda row: -row.get('diag', {}).get('npcs.tick', {}).get('net_ms', 0)
    slowest = []
    for row in sorted(rows, key=key)[:top]:
        diag = row.get('diag', {})
        parts = sorted(((label, f['net_ms']) for label, f in diag.items() if label != 'npcs.tick'),
                       key=lambda item: -item[1])[:8]
        slowest.append({'tick': row.get('tick'), 'npcs_net_ms': round(diag.get('npcs.tick', {}).get('net_ms', 0), 3),
                        'total_ms': round(row.get('total_wall_ms', 0), 3),
                        'top': [[label, round(ms, 3)] for label, ms in parts],
                        'slow_calls': sorted(row.get('slow', []), key=lambda s: -s['net_ms'])[:5]})
    overhead = [row.get('probe_overhead_ms', 0.0) for row in rows]
    return {'ticks': n, 'labels': dict(sorted(per_label.items(), key=lambda item: -item[1]['net_p99'])),
            'slowest_npc_ticks': slowest,
            'probe_overhead_ms': {'p99': round(quantile(overhead, 0.99), 3), 'max': round(max(overhead, default=0), 3)}}
