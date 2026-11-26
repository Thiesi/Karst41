"""Diagnostic S11 probes for #277 and #280 (branch diag/s277-npcs).

Harness only: every probe wraps a live function or method for the
profiling window and is put back afterwards. Nothing in src/ changes.

#277, the NPC tick one level below Npcs.act and Npcs.evaluate:
- per zone: spawners.process, spawners.reserved, evaluate, act, calm_crowds;
- navigation: step_toward, route_toward, patrol, search (with hit and miss
  counts; a miss expands the full ROUTE_NODES bound), move;
- the checks: Npcs.hostile, shared_crowd, reaction_group, take_up_answer,
  reach_holders, keeper_guard, event_motion; Combat.visible, legal, warn,
  shot, reveal; line_of_sight; clock.now_local. The combat, sight and clock
  probes are labelled `npc:` inside the NPC tick and `other:` outside it;
- spawning: spawners.location and world.enter during the NPC tick;
- slow single calls: any act, evaluate, process or search call of 0.5 ms
  or more is listed with its zone, template, state and target kind (never
  a player's name or ID), so a one-tick spike shows who caused it.

#280, the submit side of the writes label (settle plus pump, on the loop):
- IO.settle, each _settle_<kind>, IO.pump, mark_world, mark per record
  kind (meta records by name), the contracts record, _batch, _submit,
  _character, _flush_due, _refresh_due, frozen (deepcopy),
  writer.submit and commerce.prepare;
- after the window, outside every timed tick: the JSON size of each meta
  record mark_world re-freezes.

All labels are inclusive: a label's time includes the labelled calls
inside it. Probe cost: calibrate() times the wrapper on a no-op, and each
row carries probe_calls and probe_overhead_ms (calls times that cost).
With these probes on, the gate figure includes their overhead, so this
run is a diagnostic, not a gate run.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

SLOW_S = 0.0005
SLOW_PREFIXES = ('npcs.act:', 'npcs.evaluate:', 'npcs.process:', 'nav.search')


def zone_label(name: str, index: int) -> Callable[..., str]:
    """A label from the call's zone argument; never raises into the game."""
    def label(*args: Any, **kwargs: Any) -> str:
        zone = kwargs.get('zone', args[index] if len(args) > index else None)
        return f'{name}:{getattr(zone, "id", "?")}'
    return label


def fixed(name: str) -> Callable[..., str]:
    return lambda *args, **kwargs: name


def actor_detail(content: Any) -> Callable[..., dict[str, Any]]:
    """Who a slow call was about: template, state, target kind. Never an ID."""
    def detail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        actor = args[0] if args else None
        zone = args[1] if len(args) > 1 else None
        behaviour = getattr(actor, 'behaviour', {}) or {}
        return {'zone': getattr(zone, 'id', None), 'template': behaviour.get('template'),
                'state': behaviour.get('state'), 'has_target': bool(getattr(actor, 'target', None)),
                'zone_actors': len(getattr(zone, 'actors', {}) or {})}
    return detail


class Probes:
    def __init__(self, service: Any, current: Callable[[], dict[str, Any] | None]) -> None:
        self.service, self.current = service, current
        self.restores: list[Callable[[], None]] = []
        self.per_call_s = 0.0
        self.in_npcs = False
        self.record_sizes: dict[str, int] = {}

    # --- bookkeeping --------------------------------------------------------

    def _add(self, row: dict[str, Any], label: str, wall: float, cpu: float) -> None:
        slot = row['diag'].setdefault(label, [0.0, 0.0, 0])
        slot[0] += wall
        slot[1] += cpu
        slot[2] += 1
        row['probe_calls'] = row.get('probe_calls', 0) + 1

    def _timed(self, original: Callable[..., Any], label: Callable[..., str], context: bool = False,
               detail: Callable[..., dict[str, Any]] | None = None) -> Callable[..., Any]:
        def measured(*args: Any, **kwargs: Any) -> Any:
            row = self.current()
            if row is None:
                return original(*args, **kwargs)
            wall, cpu = time.perf_counter(), time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                wall, cpu = time.perf_counter() - wall, time.thread_time() - cpu
                name = label(*args, **kwargs)
                if context:
                    name = ('npc:' if self.in_npcs else 'other:') + name
                self._add(row, name, wall, cpu)
                if detail is not None and wall >= SLOW_S and name.startswith(SLOW_PREFIXES):
                    try:
                        extra = detail(*args, **kwargs)
                    except Exception:  # A probe never raises into the game.
                        extra = {}
                    row['slow'].append({'label': name, 'wall_ms': wall * 1000, 'cpu_ms': cpu * 1000, **extra})
        return measured

    def wrap(self, target: Any, name: str, label: Callable[..., str], context: bool = False,
             detail: Callable[..., dict[str, Any]] | None = None) -> None:
        if target is None or not hasattr(target, name):
            return
        own = name in vars(target)
        original = getattr(target, name)
        setattr(target, name, self._timed(original, label, context, detail))
        self.restores.append(lambda: setattr(target, name, original) if own else delattr(target, name))

    def calibrate(self, n: int = 20000) -> float:
        """Per-call probe cost, measured on a no-op wrapped the same way."""
        row: dict[str, Any] = {'diag': {}, 'slow': []}
        saved = self.current
        self.current = lambda: row
        noop = self._timed(lambda: None, fixed('calibration'), context=True)
        began = time.perf_counter()
        for _ in range(n):
            noop()
        self.per_call_s = max(0.0, (time.perf_counter() - began) / n)
        self.current = saved
        return self.per_call_s

    # --- install ------------------------------------------------------------

    def install(self) -> None:
        self._npcs()
        self._io()

    def _npcs(self) -> None:
        from karst41.server.combat import engine as combat_engine
        from karst41.server.npcs import behaviours, engine
        core = self.service.core
        npcs = getattr(core, 'npcs', None)
        if npcs is None:
            return
        detail = actor_detail(core.loop.content if hasattr(core.loop, 'content') else None)

        # The whole NPC tick, which also sets the npc:/other: context.
        own = 'tick' in vars(npcs)
        original_tick = npcs.tick

        def tick(ctx: Any) -> None:
            self.in_npcs = True
            try:
                return original_tick(ctx)
            finally:
                self.in_npcs = False
        npcs.tick = self._timed(tick, fixed('npcs.tick'))
        self.restores.append(lambda: setattr(npcs, 'tick', original_tick) if own else delattr(npcs, 'tick'))

        spawners = npcs.spawners
        self.wrap(spawners, 'process', zone_label('npcs.process', 0),
                  detail=lambda zone, now: {'zone': zone.id, 'zone_actors': len(zone.actors)})
        self.wrap(spawners, 'reserved', zone_label('npcs.reserved', 0))
        self.wrap(spawners, 'location', zone_label('spawn.location', 0))
        self.wrap(npcs, 'evaluate', zone_label('npcs.evaluate', 1), detail=detail)
        self.wrap(npcs, 'act', zone_label('npcs.act', 1), detail=detail)
        self.wrap(npcs, 'calm_crowds', zone_label('npcs.calm_crowds', 0))
        for name in ('hostile', 'shared_crowd', 'reaction_group', 'take_up_answer', 'reach_holders',
                     'keeper_guard', 'event_motion'):
            self.wrap(npcs, name, fixed(f'npcs.{name}'))

        # Navigation. The engine imported these by name; behaviours calls its
        # own module globals (route_toward -> search/step_toward/move).
        for module in (engine, behaviours):
            for name in ('step_toward', 'route_toward', 'patrol'):
                if module is behaviours and name == 'route_toward':
                    continue  # Called only from the engine.
                self.wrap(module, name, fixed(f'nav.{name}'))
        self.wrap(behaviours, 'move', fixed('nav.move'))
        self._search(behaviours, detail)

        combat = npcs.combat
        for name in ('visible', 'legal', 'warn', 'shot', 'reveal'):
            self.wrap(combat, name, fixed(f'combat.{name}'), context=True)
        self.wrap(combat_engine, 'line_of_sight', fixed('sight.line_of_sight'), context=True)
        self.wrap(getattr(combat, 'clock', None), 'now_local', fixed('clock.now_local'), context=True)
        self.wrap(npcs.world, 'enter', fixed('world.enter'), context=True)

    def _search(self, behaviours: Any, detail: Callable[..., dict[str, Any]]) -> None:
        """search with hit/miss counts: a miss expands the whole node bound."""
        original = behaviours.search

        def search(actor: Any, zone: Any, goal: Any, avoid: Any, blocked: Any) -> Any:
            row = self.current()
            path = original(actor, zone, goal, avoid, blocked)
            if row is not None:
                outcome = 'hit' if path else 'miss'
                slot = row['diag'].setdefault(f'nav.search.{outcome}', [0.0, 0.0, 0])
                slot[2] += 1
            return path
        timed = self._timed(search, fixed('nav.search'), detail=detail)
        behaviours.search = timed
        self.restores.append(lambda: setattr(behaviours, 'search', original))

    def _io(self) -> None:
        from karst41.server import io as io_module
        io = getattr(self.service, 'io', None)
        if io is None:
            return
        self.wrap(io, 'settle', fixed('io.settle'))
        self.wrap(io, 'pump', fixed('io.pump'))
        for name in [n for n in dir(type(io)) if n.startswith('_settle_')]:
            self.wrap(io, name, fixed('io.settle.' + name[len('_settle_'):]))

        def mark_label(kind: str, record_id: str, value: Any = None) -> str:
            return f'io.mark:{kind}:{record_id}' if kind == 'meta' else f'io.mark:{kind}'
        self.wrap(io, 'mark', mark_label)
        for name in ('mark_world', '_batch', '_submit', '_character', '_flush_due', '_refresh_due'):
            self.wrap(io, name, fixed('io.' + name.lstrip('_')))
        self.wrap(io_module, 'frozen', fixed('io.frozen'))
        self.wrap(getattr(io, 'writer', None), 'submit', fixed('io.writer_submit'))
        core = self.service.core
        self.wrap(getattr(core, 'contracts', None), 'record', fixed('io.record:contracts'))
        self.wrap(getattr(core, 'commerce', None), 'prepare', fixed('io.commerce_prepare'))

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
                self.record_sizes[name] = -1
                self.record_sizes[name + ':error'] = type(exc).__name__  # type: ignore[assignment]

    # --- per tick -----------------------------------------------------------

    def open_row(self, row: dict[str, Any]) -> None:
        row['diag'], row['slow'] = {}, []

    def close_row(self, row: dict[str, Any]) -> None:
        row['probe_overhead_ms'] = row.get('probe_calls', 0) * self.per_call_s * 1000
        row['diag'] = {label: {'wall_ms': w * 1000, 'cpu_ms': c * 1000, 'calls': n}
                       for label, (w, c, n) in row['diag'].items()}


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def summary(rows: list[dict[str, Any]], top: int = 5) -> dict[str, Any]:
    """p99 and max per label over the window, plus the slowest NPC ticks."""
    labels: dict[str, list[float]] = {}
    calls: dict[str, list[int]] = {}
    for row in rows:
        for label, figure in row.get('diag', {}).items():
            labels.setdefault(label, []).append(figure['wall_ms'])
            calls.setdefault(label, []).append(figure['calls'])
    n = len(rows)
    per_label = {}
    for label, values in labels.items():
        padded = values + [0.0] * (n - len(values))  # Ticks without the call count as 0.
        per_label[label] = {'p99': round(quantile(padded, 0.99), 3), 'max': round(max(padded), 3),
                            'calls_p99': quantile([float(c) for c in calls[label]] + [0.0] * (n - len(values)), 0.99)}
    worst = sorted(rows, key=lambda r: -r.get('diag', {}).get('npcs.tick', {}).get('wall_ms', 0))[:top]
    slowest = []
    for row in worst:
        diag = row.get('diag', {})
        parts = sorted(((label, f['wall_ms']) for label, f in diag.items() if label != 'npcs.tick'),
                       key=lambda item: -item[1])[:8]
        slowest.append({'tick': row.get('tick'), 'npcs_ms': round(diag.get('npcs.tick', {}).get('wall_ms', 0), 3),
                        'total_ms': round(row.get('total_wall_ms', 0), 3),
                        'top': [[label, round(ms, 3)] for label, ms in parts],
                        'slow_calls': sorted(row.get('slow', []), key=lambda s: -s['wall_ms'])[:5]})
    overhead = [row.get('probe_overhead_ms', 0.0) for row in rows]
    return {'ticks': n, 'labels': dict(sorted(per_label.items(), key=lambda item: -item[1]['p99'])),
            'slowest_npc_ticks': slowest,
            'probe_overhead_ms': {'p99': round(quantile(overhead, 0.99), 3), 'max': round(max(overhead, default=0), 3)}}
