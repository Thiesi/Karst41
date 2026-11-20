"""S48e: the first evening (native NetBBS evidence).

One whole evening per run, over NetBBS Telnet to the registered native
stdio door, in the door's default renderer (DOOR_KARST41_PRESENTATION
unset; technical lead, ruling (c)). Real keys throughout, in S48's Goal
order, as the headless walk (tests/test_s48e_evening.py):

1. Noor wakes, fights the training drone, jacks into the Hall terminal and
   lifts the record, chooses a template and a name; asks Okafor what the
   clinic charges; walks to the Tin Halo; takes Vesper's first job and does
   it by hand; is paid; takes the board's first generated offer, finishes it
   and is paid; breaches the Row truce and dies when Sable answers; wakes in
   the nearest vat; logs out.
2. In the same run Tam skips every lesson (B at the drone, E at the street
   door), goes straight to Vesper, does her job by jacking in at the loan
   terminal, is paid and logs out (ruling (d)).

Declared fixture (ruling (b), "seeded"): the board's seed is the contract
engine's origin and the 30-minute epoch. The run sets the engine's origin to
the headless harness's ("test-origin") before anyone connects and starts the
server clock at the harness's 2026-01-01 00:00 UTC, so Vesper's first
generated offer is the walk's ("Mark the service approaches") for the run's
first half hour. Nothing else is placed, made due or skipped.
"""

import argparse
import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
import traceback
from typing import Any

from accept_s10_netbbs import Connection, NativeProbe, wait
from karst41.door.client import presentation_on
from karst41.server import truce
from karst41.server.app import Server
from karst41.server.clock import RealClock
from tests.presentation.snapshots import snapshot

HALL, HALO, ROW, BLOCKS = 'vatside-wake-hall', 'sodium-tin-halo', 'sodium-row', 'terraces-blocks'
TO_HALO = ('vatside-clinics', 'core-plaza', ROW, HALO)
UP, DOWN, ESC, ENTER, TAB, LEAVE = b'\x1b[A', b'\x1b[B', b'\x1b', b'\r', b'\t', b'\x18'
CUT = ('...', '…')
WAKE_SECONDS = 5*60


class EveningClock(RealClock):
    """The declared fixture's clock: real time from the harness's start."""
    START = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def now_utc(self) -> datetime:
        return self.START+timedelta(seconds=time.monotonic()-self.started)


class Evening:
    def __init__(self, probe: NativeProbe, root: Path, width: int, height: int, tier: str) -> None:
        self.probe, self.root, self.width, self.height, self.tier = probe, root, width, height, tier
        self.core = probe.service.core
        self.rows: list[dict[str, Any]] = []
        self.facts: dict[str, Any] = {}
        self.heard: dict[str, list[tuple[float, str, str]]] = {}

    # --- reading the game --------------------------------------------------

    def body(self, conn: Connection) -> dict:
        view = self.probe.game.sim.views[conn.session.id].previous or {}
        return view.get('body', {})

    def screen(self, conn: Connection) -> str:
        return conn.caller.screen.screen_text()

    def actor(self, conn: Connection):
        return self.core.world.actors.get(conn.session.player_id)

    def job(self, conn: Connection, template: str):
        owner, pid = self.core.contracts, conn.session.player_id
        return next((j for j in owner.state.engine.instances.values()
                     if j.template == template and pid in {p.player for p in j.roster}), None)

    def listen(self, conn: Connection, name: str) -> None:
        heard = self.heard.setdefault(name, [])
        send, clock = conn.session.send, self.probe.service.clock

        def observe(frame: Any) -> bool:  # Observation only: every log line delivered.
            if frame.type == 'log' and not frame.data.get('replay'):
                heard.extend((clock.now_tick()/10, line.get('kind', ''), line.get('text', ''))
                             for line in frame.data['lines'])
            return send(frame)
        conn.session.send = observe

    # --- keys -----------------------------------------------------------------

    async def settled(self, conn: Connection) -> None:
        await wait(lambda: conn.session.ack_revision == conn.session.view_revision, 15)

    async def press(self, conn: Connection, data: bytes, until: Any = None, timeout: float = 15) -> None:
        await self.settled(conn)
        await conn.caller.write(data)
        if until is not None:
            await wait(until, timeout)
        await self.settled(conn)

    async def beside(self, conn: Connection, tile: Any) -> None:
        """Walk to the nearest free tile next to a tile (or a moving actor's tile)."""
        def choose() -> tuple[int, int] | None:
            target = tile() if callable(tile) else tile
            me = self.actor(conn)
            zone = self.core.world.zones[me.zone]
            if max(abs(me.x-target[0]), abs(me.y-target[1])) <= 1 and me.position != target:
                return me.position
            occupied = {a.position for a in zone.actors.values() if a is not me}
            exits = {e.tile for e in zone.template.meta.exits}
            prev, queue = {me.position: None}, deque([me.position])
            while queue:
                x, y = queue.popleft()
                for t in ((x+1, y), (x, y+1), (x-1, y), (x, y-1)):
                    if t in prev or t in occupied or t in exits or not zone.passable(t):
                        continue
                    prev[t] = (x, y)
                    if max(abs(t[0]-target[0]), abs(t[1]-target[1])) <= 1 and t != target:
                        return t
                    queue.append(t)
            return None
        await self.probe.walk(conn, choose)

    async def cursor_to(self, conn: Connection, row: str) -> None:
        panels = self.core.contracts.panels
        for _ in range(30):
            panel = panels.menus[conn.session.id]
            if panels.rows(conn.session, panel)[panel.cursor]['id'] == row:
                return
            cursor = panel.cursor
            await self.press(conn, DOWN, lambda: panels.menus[conn.session.id].cursor != cursor)
        raise AssertionError(('board row never under the cursor', row))

    # --- evidence -------------------------------------------------------------

    async def capture(self, conn: Connection, name: str, caller: str, **facts: Any) -> None:
        await self.settled(conn)
        await wait(lambda: conn.caller.output.endswith((b'\x1b[?25l', b'\x1b[?25h')), 15)
        label = f's48e-{caller.lower()}-{name}-{self.width}x{self.height}-{self.tier}'
        self.probe.save(conn, label)
        (self.root/(label+'.json')).write_text(json.dumps(snapshot(conn.caller.screen), ensure_ascii=False,
                                                          indent=2)+'\n', encoding='utf-8')
        rows = self.screen(conn).splitlines()
        hint = rows[-2].strip('║│ ') if len(rows) > 1 else ''
        self.rows.append({'capture': label, 'caller': caller, 'step': name, 'hint': hint,
                          'hint_cut': any(mark in hint for mark in CUT), **facts,
                          'sha256': {suffix: sha256((self.root/(label+suffix)).read_bytes()).hexdigest()
                                     for suffix in ('.ans', '.txt', '.json')}})
        self.probe.report(f'  {label}')

    # --- the opening ----------------------------------------------------------

    def own_drone(self, conn: Connection) -> bool:
        zone = self.core.world.zones[HALL]
        return any(a.id.startswith(conn.session.player_id) and 'training' in a.id and a.health > 0
                   for a in zone.actors.values())

    async def drone(self, conn: Connection, name: str, skip: bool) -> None:
        await self.probe.walk(conn, (21, 14))
        await wait(lambda: self.own_drone(conn), 15)
        await self.capture(conn, 'drone', name)
        deadline = time.monotonic()+90
        while self.own_drone(conn):
            assert time.monotonic() < deadline, 'the drone never went down'
            key = b'b' if skip else (b'f' if self.body(conn).get('target') else TAB)
            await self.press(conn, key)
            await asyncio.sleep(.5)

    async def terminal(self, conn: Connection, name: str) -> None:
        lat, pid = self.core.lattice, conn.session.player_id
        await self.beside(conn, (35, 15))
        await self.press(conn, b'j', lambda: pid in lat.presences)
        await self.capture(conn, 'training-core', name)
        await self.press(conn, TAB)
        deadline = time.monotonic()+60
        while True:
            defenders = lat.encounters.defenders(lat.presences[pid])
            if not defenders or not defenders[0].alive:
                break
            assert time.monotonic() < deadline, 'the training ICE never fell'
            await self.press(conn, b'1')
            await asyncio.sleep(1.5)
        await self.press(conn, b'e', lambda: 'Your decant record' in self.screen(conn))
        wake = self.probe.game.wakes[conn.session.id]
        await self.press(conn, ENTER, lambda: wake.lessons.get('lifted'), 20)
        deadline = time.monotonic()+30
        while pid in lat.presences:
            assert time.monotonic() < deadline, 'B did not leave the training core'
            await self.press(conn, b'b')
            await asyncio.sleep(1)

    async def street_door(self, conn: Connection) -> None:
        await self.probe.walk(conn, (37, 16))
        await self.press(conn, b'e', lambda: 'DECANT RECORD' in self.screen(conn))

    async def record(self, conn: Connection, name: str, archetype: int) -> None:
        await wait(lambda: 'DECANT RECORD' in self.screen(conn), 15)
        wake = self.probe.game.wakes[conn.session.id]
        self.facts.setdefault('lessons_at_record', {})[name] = dict(wake.lessons)
        await self.capture(conn, 'record', name)
        for index in range(archetype):
            await self.press(conn, DOWN, lambda index=index: wake.cursor == index+1)
        await self.press(conn, ENTER, lambda: 'Name (Enter keeps default):' in self.screen(conn), 30)
        pid = conn.session.player_id
        await self.press(conn, name.encode()+ENTER,
                         lambda: pid in self.probe.game.players and not self.probe.game.unread(pid), 30)

    # --- Vesper -----------------------------------------------------------------

    def vesper(self):
        zone = self.core.world.zones[HALO]
        return next(a for a in zone.actors.values()
                    if a.behaviour.get('template') == 'npc-vendor-sodium-tin-halo-vesper')

    async def talk_to_vesper(self, conn: Connection) -> None:
        for _ in range(5):
            if conn.session.id in self.core.dialogue.menus:
                return
            await self.beside(conn, lambda: self.vesper().position)
            await self.settled(conn)
            await conn.caller.write(b'e')
            try:
                await wait(lambda: conn.session.id in self.core.dialogue.menus, 3)
            except TimeoutError:
                continue
        assert conn.session.id in self.core.dialogue.menus, "E beside Vesper did not open her page"

    async def board(self, conn: Connection) -> None:
        await self.talk_to_vesper(conn)
        await self.press(conn, b'c', lambda: conn.session.id in self.core.contracts.panels.menus)

    async def turn_in(self, conn: Connection, template: str) -> int:
        player = self.probe.game.players[conn.session.player_id]
        chits = player.chits_hand
        await self.board(conn)
        await self.cursor_to(conn, self.job(conn, template).id)
        await self.press(conn, b't')
        await self.press(conn, b'y', lambda: self.job(conn, template).state == 'turned_in')
        return player.chits_hand-chits

    async def route(self, conn: Connection, *zones: str) -> None:
        for zone in zones:
            await self.probe.travel(conn, zone)

    async def first_job(self, conn: Connection, name: str, by: str) -> None:
        stage = lambda: self.job(conn, 'vesper-first-thing')
        await self.talk_to_vesper(conn)
        await self.capture(conn, 'vesper-page', name)
        await self.press(conn, b'c', lambda: conn.session.id in self.core.contracts.panels.menus)
        await self.cursor_to(conn, 'vesper-first-thing')
        await self.capture(conn, 'work-board', name)
        await self.press(conn, b'a')
        await self.press(conn, b'y', lambda: stage() is not None)
        await self.capture(conn, 'job-accepted', name)
        await self.press(conn, ESC, lambda: conn.session.id not in self.core.contracts.panels.menus)
        await self.route(conn, ROW, 'core-plaza', BLOCKS)
        await self.probe.walk(conn, (73, 25))
        await self.press(conn, b'e', lambda: stage().index == 1)
        await self.capture(conn, 'hatch-checked', name)
        if by == 'hand':
            await self.press(conn, b'e', lambda: stage().index == 2, 30)
        else:
            lat, pid = self.core.lattice, conn.session.player_id
            await self.beside(conn, (71, 25))
            await self.press(conn, b'j', lambda: pid in lat.presences)
            await self.capture(conn, 'loan-terminal', name)
            await self.press(conn, b'e', lambda: 'Enter Stairwell C cabinet' in self.screen(conn))
            await self.press(conn, ENTER, lambda: lat.presences[pid].core == 'lat-stairwell-c', 20)
            await self.press(conn, b'e', lambda: 'Release parcel hatch' in self.screen(conn))
            await self.capture(conn, 'cabinet-controls', name)
            await self.press(conn, ENTER, lambda: stage().index == 2, 30)
            await self.capture(conn, 'released-by-jack', name)
            deadline = time.monotonic()+30
            while pid in lat.presences:
                assert time.monotonic() < deadline, 'J did not bring the body back'
                await self.press(conn, b'j')
                await asyncio.sleep(3)
        await self.beside(conn, (72, 23))
        await self.capture(conn, 'parcel-shelf', name)
        await self.press(conn, b'e', lambda: stage().index == 3)
        await self.route(conn, 'core-plaza', ROW, HALO)
        await self.beside(conn, (8, 15))
        await self.capture(conn, 'hand-over', name, focus=self.body(conn).get('focus'))
        await self.press(conn, b'e', lambda: stage().state == 'complete')
        self.facts.setdefault('paid', {})[name] = await self.turn_in(conn, 'vesper-first-thing')
        await self.capture(conn, 'paid', name)
        await self.press(conn, ESC, lambda: conn.session.id not in self.core.contracts.panels.menus)

    async def generated_job(self, conn: Connection, name: str) -> None:
        owner = self.core.contracts
        await self.board(conn)
        offer = owner.offers('vesper')[0]
        assert offer.template == 'vesper-local-plant', ('the declared seed gave another offer', offer.template)
        await self.cursor_to(conn, offer.id)
        await self.capture(conn, 'generated-offer', name)
        await self.press(conn, b'a')
        await self.press(conn, b'y', lambda: self.job(conn, offer.template) is not None)
        await self.press(conn, ESC, lambda: conn.session.id not in owner.panels.menus)
        job = lambda: self.job(conn, offer.template)
        await self.route(conn, ROW, 'core-plaza', BLOCKS)
        await self.beside(conn, (103, 10))
        await self.capture(conn, 'collect-supply', name, focus=self.body(conn).get('focus'))
        await self.press(conn, b'e', lambda: job().index == 1)
        target = job().stages[1].targets[0]
        await self.route(conn, 'core-plaza', ROW, target.zone)
        await self.beside(conn, self.core.world.zones[target.zone].objects[target.object].spec.tile)
        await self.capture(conn, 'plant', name, focus=self.body(conn).get('focus'))
        await self.press(conn, b'e', lambda: job().state == 'complete', 30)
        self.facts['generated'] = [offer.template, await self.turn_in(conn, offer.template)]
        await self.capture(conn, 'generated-paid', name)
        await self.press(conn, ESC, lambda: conn.session.id not in owner.panels.menus)

    async def okafor(self, conn: Connection, name: str) -> None:
        zone = self.core.world.zones[HALL]
        okafor = next(a for a in zone.actors.values() if a.behaviour.get('template') == 'npc-cast-okafor')
        await self.beside(conn, lambda: okafor.position)
        await self.press(conn, b'e', lambda: conn.session.id in self.core.dialogue.menus)
        await self.press(conn, b'a', lambda: 'The counter shows the real sum.' in self.screen(conn))
        await self.capture(conn, 'okafor-ask', name)
        await self.press(conn, ESC, lambda: conn.session.id not in self.core.dialogue.menus)

    async def breach(self, conn: Connection, name: str) -> None:
        zone = self.core.world.zones[ROW]
        player = self.probe.game.players[conn.session.player_id]

        def regular():
            me = self.actor(conn)
            near = [a for a in zone.actors.values()
                    if a.kind == 'npc' and 'regular' in (a.behaviour.get('template') or '')]
            return min(near, key=lambda a: (abs(a.x-me.x)+abs(a.y-me.y), a.id)) if near else None
        await wait(lambda: regular() is not None, 60)
        vat = self.core.combat.vat(player)
        deadline = time.monotonic()+90
        while not truce.hunted(player, self.core.combat.now()):
            assert time.monotonic() < deadline, 'the shot never broke the truce'
            mark = regular()
            await self.beside(conn, lambda: mark.position)
            await self.probe.target(conn, mark.id)
            await self.press(conn, b'f')
            await asyncio.sleep(2.5)  # A raised weapon fires after two seconds.
        await self.capture(conn, 'breach', name)
        await wait(lambda: self.actor(conn) is None or self.actor(conn).zone != ROW, 240)
        await wait(lambda: self.actor(conn) is not None and self.actor(conn).zone != ROW, 120)
        await wait(lambda: any(text.endswith('considers it settled.') for _, _, text in self.heard[name]), 30)
        await asyncio.sleep(12)  # P8's danger hold and the decant grace.
        await self.capture(conn, 'decant', name)
        me = self.actor(conn)
        self.facts['death'] = {'vat': [vat[0], list(vat[1])], 'woke': [me.zone, list(me.position)],
                               'lines': [text for _, kind, text in self.heard[name]
                                         if text.startswith(('Clone charge', 'You wake in the vat',
                                                             'Sable considers'))]}

    # --- the run ----------------------------------------------------------------

    async def caller(self, name: str, by: str, archetype: int, **tier: Any) -> None:
        self.probe.report(f'{name}: {by}')
        conn = await self.probe.open(self.width, self.height, name+self.tier, unread=True, **tier)
        assert conn.session.caps['tier'] == self.tier, conn.session.caps
        opened = time.monotonic()
        self.listen(conn, name)
        await self.capture(conn, 'first-screen', name)
        start = self.actor(conn).position
        await self.press(conn, b'\x1b[C', lambda: self.actor(conn).position != start)
        self.facts.setdefault('first_step', {})[name] = [list(start), list(self.actor(conn).position)]
        if by == 'skip':
            await self.drone(conn, name, skip=True)
            await self.street_door(conn)
        else:
            await self.drone(conn, name, skip=False)
            await self.terminal(conn, name)
        await self.record(conn, name, archetype)
        self.facts.setdefault('wake_seconds', {})[name] = round(time.monotonic()-opened, 1)
        if by == 'hand':
            await self.okafor(conn, name)
        await self.route(conn, *TO_HALO)
        await self.first_job(conn, name, 'jack' if by == 'skip' else 'hand')
        if by == 'hand':
            await self.generated_job(conn, name)
            await self.route(conn, ROW)
            await self.breach(conn, name)
        walls = {line.text: line.id for line in self.core.characters.content.walls.lines}
        self.facts.setdefault('walls', {})[name] = [walls[text] for _, _, text in self.heard[name] if text in walls]
        await self.probe.leave(conn, f's48e-{name.lower()}-left-{self.width}x{self.height}-{self.tier}')


async def accept(root: Path, door_python: Path, width: int, height: int, tier: str, show: bool) -> None:
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
    root.mkdir(parents=True, exist_ok=False)
    probe = NativeProbe(root, door_python, show, show_all_tiers=True)
    # Ruling (c): the door's default renderer, with the switch unset.
    probe.door_environment.pop('DOOR_KARST41_PRESENTATION', None)
    probe.renderer = 'present' if presentation_on(probe.door_environment) else 'legacy'
    assert probe.renderer == 'present'
    probe.service = Server(probe.root/'world', tcp=True, clock=EveningClock())
    await probe.start()
    evening = Evening(probe, root, width, height, tier)
    try:
        engine = probe.service.core.contracts.state.engine
        engine.origin = 'test-origin'  # Declared fixture: the headless walk's board seed.
        engine.boards.clear()
        kwargs = {'sixteen': tier == '16', 'truecolor': tier == 'truecolor'}
        await evening.caller('Noor', 'hand', 0, **kwargs)
        await evening.caller('Tam', 'skip', 1, **kwargs)
        facts = evening.facts
        assert facts['paid'] == {'Noor': 200, 'Tam': 200}, facts['paid']
        assert facts['generated'][1] > 0, facts['generated']
        assert all(seconds <= WAKE_SECONDS for seconds in facts['wake_seconds'].values()), facts['wake_seconds']
        death = facts['death']
        assert death['woke'][0] == death['vat'][0] == 'core-precinct', death
        cut = [row for row in evening.rows if row['hint_cut']]
        assert not probe.errors and probe.service.failure is None
        (root/'s48e-results.json').write_text(json.dumps({
            'passed': True, 'head': head, 'transport': 'NetBBS Telnet -> registered native stdio',
            'renderer': probe.renderer, 'size': [width, height], 'tier': tier,
            'captures': evening.rows, 'facts': facts,
            'heard': {name: [{'second': round(t, 1), 'kind': k, 'text': x} for t, k, x in lines
                             if k in ('ambient', 'speech')] for name, lines in evening.heard.items()},
            'hint_cut_rows': [(row['capture'], row['hint']) for row in cut],
            'fixtures': ["the contract engine's origin set to the headless harness's 'test-origin' before "
                         'anyone connects, and the server clock started at 2026-01-01 00:00 UTC: the board '
                         "seed (origin and epoch) is the walk's, so Vesper's first generated offer is its "
                         '"Mark the service approaches"'],
            'presentation_acceptance': False, 'h1_layout_approval': False,
            's25_layout_approval': False}, indent=2, ensure_ascii=False)+'\n', encoding='utf-8', newline='\n')
        probe.report(f'S48e PASS {width}x{height}/{tier}: one whole evening, and Vesper by jack after skipping')
    except BaseException:
        (root/'failure-trace.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        await probe.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--door-python', type=Path, required=True)
    parser.add_argument('--size', choices=('80x24', '132x50'), required=True)
    parser.add_argument('--tier', choices=('16', '256', 'truecolor'), required=True)
    parser.add_argument('--show', action='store_true')
    args = parser.parse_args()
    width, height = map(int, args.size.split('x'))
    asyncio.run(accept(args.root, args.door_python, width, height, args.tier, args.show))
